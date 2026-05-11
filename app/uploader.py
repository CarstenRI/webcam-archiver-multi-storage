"""Amazon-Photos-Upload-Wrapper – schlanke Variante.

Die offizielle `amazon-photos`-Lib (trevorhobenshield) macht beim Init einen
kompletten Bulk-Sync der gesamten Foto-Bibliothek. Bei Bibliotheken mit
zehntausenden Fotos dauert das ewig und triggert Rate-Limits.

Wir leiten von AmazonPhotos ab und überschreiben `load_db` und `get_folders`
zu No-Ops. Album-IDs cachen wir lokal in der SQLite-DB, sodass wir Amazons
Drive-API nur einmal pro Album fragen müssen.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from .db import Album, SessionLocal, get_setting

log = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client = None
_client_signature: Optional[str] = None

# Skip-Mode: wenn Amazon mit 401 antwortet, fuer X Minuten alle Uploads ueberspringen,
# damit nicht jede Cam in den 3x retry-Storm der amazon-photos-Lib laeuft.
_cookies_expired_until: Optional[float] = None  # unix-timestamp
_cookies_expired_msg: str = ""
SKIP_DURATION_SECONDS = 15 * 60  # 15 Minuten


def _is_in_skip_mode() -> tuple[bool, str]:
    """Returns (skip_active, msg). Skip-Mode laeuft automatisch ab."""
    import time
    global _cookies_expired_until, _cookies_expired_msg
    if _cookies_expired_until and time.time() < _cookies_expired_until:
        return True, _cookies_expired_msg
    return False, ""


def _mark_cookies_expired(msg: str = "Cookies abgelaufen") -> None:
    import time
    global _cookies_expired_until, _cookies_expired_msg
    _cookies_expired_until = time.time() + SKIP_DURATION_SECONDS
    _cookies_expired_msg = msg
    log.warning("Amazon-Cookies abgelaufen – Skip-Mode fuer %d Min aktiv", SKIP_DURATION_SECONDS // 60)


def _clear_skip_mode() -> None:
    global _cookies_expired_until, _cookies_expired_msg
    _cookies_expired_until = None
    _cookies_expired_msg = ""



class UploadError(Exception):
    pass


def _load_cookies() -> dict:
    with SessionLocal() as s:
        raw = get_setting(s, "amazon_cookies", "")
    if not raw:
        return {}
    try:
        cookies = json.loads(raw)
        if isinstance(cookies, dict):
            return cookies
    except json.JSONDecodeError:
        pass
    return {}


def _required_cookies_present(cookies: dict) -> tuple[bool, str]:
    if not cookies:
        return False, "Keine Amazon-Cookies konfiguriert."
    keys = set(cookies.keys())
    if "session-id" not in keys:
        return False, "Cookie 'session-id' fehlt."
    has_at = any(k.startswith("at-") for k in keys)
    has_ubid = any(k.startswith("ubid-") for k in keys)
    if not has_at:
        return False, "Cookie 'at-*' (z.B. at-acbde / at-main) fehlt."
    if not has_ubid:
        return False, "Cookie 'ubid-*' (z.B. ubid-acbde / ubid-main) fehlt."
    return True, "Cookies hinterlegt."


def _make_lite_class():
    """Subklasst AmazonPhotos und skippt teure Init-Schritte."""
    from amazon_photos import AmazonPhotos

    class AmazonPhotosLite(AmazonPhotos):
        def get_folders(self):  # type: ignore[override]
            return []

        def load_db(self, **kwargs):  # type: ignore[override]
            import pandas as pd
            return pd.DataFrame()

        def build_tree(self, folders=None):  # type: ignore[override]
            return {"name": "", "id": self.root["id"], "children": [], "path": {}}

    return AmazonPhotosLite


def _build_client():
    global _client, _client_signature

    cookies = _load_cookies()
    sig = json.dumps({"cookies": sorted(cookies.keys())}, sort_keys=True)
    if _client is not None and _client_signature == sig:
        return _client

    ok, msg = _required_cookies_present(cookies)
    if not ok:
        raise UploadError(msg + " Bitte unter 'Einstellungen' eintragen.")

    try:
        AmazonPhotosLite = _make_lite_class()
    except ImportError as e:
        raise UploadError(f"amazon-photos Library nicht verfügbar: {e}") from e

    from .config import settings
    settings.ensure_dirs()
    ap_data_dir = settings.data_dir / "amazon_photos"
    ap_data_dir.mkdir(parents=True, exist_ok=True)

    try:
        _client = AmazonPhotosLite(
            cookies=cookies,
            db_path=str(ap_data_dir / "ap.parquet"),
            tmp=str(ap_data_dir / "tmp"),
        )
        _client_signature = sig
        return _client
    except Exception as e:
        raise UploadError(f"Amazon-Login fehlgeschlagen: {e}") from e


def reset_client() -> None:
    global _client, _client_signature
    with _client_lock:
        _client = None
        _client_signature = None
    _clear_skip_mode()


# ---------- Album-Handling ----------
def _find_amazon_album_by_name(client, album_name: str) -> Optional[str]:
    """Sucht in Amazon nach einem Album mit diesem Namen via Drive-API."""
    try:
        r = client.client.get(
            f"{client.drive_url}/search",
            params={
                "asset": "ALL",
                "tempLink": "false",
                "resourceVersion": "V2",
                "ContentType": "JSON",
                "filters": f'kind:VISUAL_COLLECTION AND name:"{album_name}"',
                "limit": 10,
                "lowResThumbnail": "false",
                "searchContext": "customer",
            },
        )
        if r.status_code != 200:
            return None
        data = r.json().get("data", [])
        for entry in data:
            if entry.get("name") == album_name:
                return entry.get("id")
    except Exception as e:
        log.debug("Album-Search fehlgeschlagen: %s", e)
    return None


def _create_amazon_album(client, album_name: str) -> Optional[str]:
    """Legt ein neues Album in Amazon an, gibt die node_id zurück."""
    try:
        r = client.client.post(
            f"{client.drive_url}/nodes",
            json={
                "kind": "VISUAL_COLLECTION",
                "name": album_name,
                "resourceVersion": "V2",
                "ContentType": "JSON",
            },
        )
        if r.status_code >= 400:
            log.warning("Album-Create %s fehlgeschlagen: %s %s", album_name, r.status_code, r.text[:200])
            return None
        body = r.json()
        return body.get("id") or body.get("nodeId")
    except Exception as e:
        log.warning("Album-Create %s exception: %s", album_name, e)
        return None


def _add_to_amazon_album(client, album_id: str, node_id: str) -> bool:
    """Fügt eine node_id zu einem Album hinzu."""
    try:
        r = client.client.patch(
            f"{client.drive_url}/nodes/{album_id}/children",
            json={
                "op": "add",
                "value": [node_id],
                "resourceVersion": "V2",
                "ContentType": "JSON",
            },
        )
        if r.status_code >= 400:
            log.warning("add_to_album %s/%s fehlgeschlagen: %s %s", album_id, node_id, r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        log.warning("add_to_album exception: %s", e)
        return False


def _resolve_album_id(client, album_db_id: int, album_name: str) -> Optional[str]:
    """Liefert die Amazon-Album-ID für unser DB-Album. Lokal gecacht.

    1. Wenn `Album.amazon_album_id` schon gesetzt ist → nutzen.
    2. Sonst: in Amazon nach Album mit diesem Namen suchen → ID speichern.
    3. Sonst: neu anlegen → ID speichern.
    """
    with SessionLocal() as s:
        a = s.get(Album, album_db_id)
        if a is None:
            return None
        if a.amazon_album_id:
            return a.amazon_album_id

    # Nicht gecacht – erst suchen
    found_id = _find_amazon_album_by_name(client, album_name)
    if not found_id:
        # Nicht gefunden – anlegen
        found_id = _create_amazon_album(client, album_name)
    if not found_id:
        return None

    # In DB cachen
    with SessionLocal() as s:
        a = s.get(Album, album_db_id)
        if a is not None:
            a.amazon_album_id = found_id
            s.commit()
    return found_id


# ---------- Upload ----------
def upload_file(
    path: Path,
    album_name: Optional[str] = None,
    album_db_id: Optional[int] = None,
) -> dict:
    """Lädt eine Datei zu Amazon Photos hoch und ordnet sie ggf. einem Album zu.

    `album_name` und `album_db_id` werden zusammen übergeben (aus scheduler.py).
    """
    # Skip-Mode-Check: wenn Cookies bekanntermaßen abgelaufen sind, sofort raus
    in_skip, skip_msg = _is_in_skip_mode()
    if in_skip:
        raise UploadError(f"Skip-Mode aktiv: {skip_msg}")

    with _client_lock:
        try:
            client = _build_client()
        except UploadError as e:
            # Wenn der Build den 401 vorausschauend erkennt
            emsg = str(e)
            if "401" in emsg or "expired" in emsg.lower():
                _mark_cookies_expired(emsg[:200])
            raise

    parent_id = client.root.get("id") if hasattr(client, "root") and client.root else None
    if not parent_id:
        raise UploadError("Konnte Amazon-Photos-Root nicht ermitteln.")

    try:
        with open(path, "rb") as f:
            r = client.client.post(
                client.cdproxy_url,
                content=f.read(),
                params={
                    "name": Path(path).name,
                    "kind": "FILE",
                    "parentNodeId": parent_id,
                },
            )
    except Exception as e:
        raise UploadError(f"Upload-HTTP-Call fehlgeschlagen: {e}") from e

    if r.status_code == 409:
        return {"status": "duplicate_filename", "code": 409}
    if r.status_code == 401:
        _mark_cookies_expired(f"Amazon 401: {r.text[:200]}")
        raise UploadError(f"Amazon antwortete 401 (Cookies abgelaufen): {r.text[:200]}")
    if r.status_code >= 400:
        raise UploadError(f"Amazon antwortete {r.status_code}: {r.text[:300]}")

    try:
        body = r.json()
    except Exception:
        body = {}
    node_id = body.get("id") or body.get("nodeId")

    # Album-Zuordnung (optional)
    album_status = "no_album"
    if node_id and album_name and album_db_id:
        try:
            album_id = _resolve_album_id(client, album_db_id, album_name)
            if album_id:
                if _add_to_amazon_album(client, album_id, node_id):
                    album_status = "added_to_album"
                else:
                    album_status = "album_assign_failed"
            else:
                album_status = "album_create_failed"
        except Exception as e:
            log.warning("Album-Handling-Fehler: %s", e)
            album_status = f"album_error:{e}"[:80]

    return {"node_id": node_id, "status": "success", "album": album_status}


def health_check() -> tuple[bool, str]:
    """Schneller Status-Check – KEIN Netzwerk-Call."""
    cookies = _load_cookies()
    ok, msg = _required_cookies_present(cookies)
    if not ok:
        return False, msg
    # Skip-Mode → Cookies abgelaufen
    in_skip, skip_msg = _is_in_skip_mode()
    if in_skip:
        import time
        rem = int(_cookies_expired_until - time.time()) if _cookies_expired_until else 0
        return False, f"Cookies abgelaufen ({skip_msg[:60]}) – Skip-Mode noch {rem // 60} Min."
    if _client is not None:
        return True, "Verbunden"
    return True, msg + " (Verbindung wird beim ersten Upload geprüft)"
