"""Amazon-Session-Maintenance (v0.12.0).

Loest das Cookie-Refresh-Drama: statt alle 24-72h manuell Cookies aus
F12 ins UI zu kopieren, pingt der Service periodisch eine billige
amazon.de-URL an, bei der Amazon die Kurzlauf-Cookies (session-id,
session-token) selbst rolliert wie bei einem normalen Browser-Besuch.

Empirisch (siehe scripts/cookie_refresh_probe.py-Output 2026-05-25):
- GET https://www.amazon.de/photos/ -> 200, rotiert session-id+session-token
- GET https://www.amazon.de/gp/your-account/order-history -> 401 aber rotiert
  zusaetzlich ubid-acbde + x-acbde (Step-Up-Auth, Session selbst intakt)
- at-acbde wird NIE serverseitig rolliert (nur beim Login gesetzt) -
  das ist die ultimative TTL-Konstante; UI muss expires anzeigen.

Public API:
    heartbeat() -> HeartbeatResult        # /photos/ + cookie-merge in DB
    secondary_refresh() -> HeartbeatResult # /your-account/* fuer ubid/x
    cookie_health() -> list[CookieInfo]   # je Cookie {name, expires_at, status}
    cookies_from_jar(jar) -> dict         # Helper fuer uploader.py

Logging-Konvention:
    log.info("amazon-heartbeat: %s -> %d rotated, %s",
             url, count, "OK"/"FAIL: reason")
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from .db import SessionLocal, get_setting, set_setting

log = logging.getLogger(__name__)


# Endpoints — Reihenfolge wichtig: Primary zuerst (200 = healthy).
PRIMARY_URL = "https://www.amazon.de/photos/"
SECONDARY_URL = "https://www.amazon.de/gp/your-account/order-history"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Setting-Keys
LAST_HEARTBEAT_KEY = "amazon_last_heartbeat"   # ISO-Timestamp
LAST_HEARTBEAT_STATUS_KEY = "amazon_last_heartbeat_status"  # "ok"/"fail:<msg>"
COOKIE_EXPIRES_KEY = "amazon_cookie_expires"   # JSON: {name: iso_expires}
LAST_UPLOAD_ERROR_KEY = "amazon_last_upload_error"  # JSON: {ts, status_code, message} (v0.12.1)

# Wie lange ein persistierter Upload-Fehler das Banner triggern darf (in Minuten).
# Default deckt > Skip-Mode-Dauer ab, damit das Banner laenger sichtbar bleibt als der
# in-process Skip-Mode, aber sich automatisch verabschiedet falls clear_upload_error
# nicht aufgerufen wurde.
RECENT_UPLOAD_ERROR_WINDOW_MIN = 30

# Mindest-TTL-Hinweise (Heuristik fuer Health-UI; Amazon kommuniziert sie nicht):
# - session-id / session-token: kurz (Stunden bis 1-2 Tage), durch Heartbeat erneuert
# - ubid-acbde / x-acbde / lc-acbde / i18n-prefs: mittel
# - at-acbde / sst-acbde / sess-at-acbde: lang, kritisch — Re-Login wenn weg
LONG_LIVED_REQUIRED = {"at-acbde", "ubid-acbde", "session-id", "session-token"}

_lock = threading.Lock()


@dataclass
class HeartbeatResult:
    ok: bool
    status_code: int = 0
    url: str = ""
    rotated: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    message: str = ""
    duration_ms: int = 0

    def to_log(self) -> str:
        if self.ok:
            return (f"{self.url} -> {self.status_code}, "
                    f"{len(self.rotated)} rotated, {len(self.new)} new ({self.duration_ms} ms)")
        return f"{self.url} -> {self.status_code} FAIL: {self.message[:120]} ({self.duration_ms} ms)"


@dataclass
class CookieInfo:
    name: str
    expires_at: Optional[str]  # ISO timestamp or None
    seconds_to_expiry: Optional[int]
    status: str  # "ok" | "warn" (<24h) | "expired" | "unknown"
    required: bool  # in LONG_LIVED_REQUIRED


# ----- Cookie-Load / -Save ---------------------------------------------------

def load_cookies() -> dict:
    """Aktuelle Cookies aus der DB."""
    with SessionLocal() as s:
        raw = get_setting(s, "amazon_cookies", "")
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_cookies(cookies: dict, *, expires: Optional[dict] = None) -> None:
    """Schreibt Cookies + optional ein {name: iso-expires}-Mapping zurueck.

    Triggert reset_client(), damit die amazon-photos-Lib die neuen Cookies sieht.
    """
    with _lock:
        with SessionLocal() as s:
            set_setting(s, "amazon_cookies", json.dumps(cookies))
            if expires is not None:
                # Bestehende Expires-Map ergaenzen statt ueberschreiben
                raw = get_setting(s, COOKIE_EXPIRES_KEY, "")
                try:
                    cur = json.loads(raw) if raw else {}
                    if not isinstance(cur, dict):
                        cur = {}
                except json.JSONDecodeError:
                    cur = {}
                cur.update(expires)
                # Cookies die nicht mehr in der DB sind, aus Expires-Map droppen
                cur = {k: v for k, v in cur.items() if k in cookies}
                set_setting(s, COOKIE_EXPIRES_KEY, json.dumps(cur))
    # Lib-Cache zuruecksetzen, damit Folge-Uploads die neuen Cookies nutzen
    try:
        from .uploader import reset_client
        reset_client()
    except Exception as e:  # noqa: BLE001
        log.debug("reset_client after save_cookies: %s", e)


def merge_into_db(new_cookies: dict, new_expires: Optional[dict] = None) -> tuple[int, int]:
    """Mergt new_cookies in die DB-Cookies und schreibt sie zurueck.

    Returns (count_rotated, count_added). Erhaelt alle bestehenden Cookies,
    die new_cookies nicht enthaelt (das ist wichtig — Amazon liefert pro
    Heartbeat nur eine Teilmenge zurueck).
    """
    if not new_cookies:
        return 0, 0
    existing = load_cookies()
    rotated = sum(1 for k, v in new_cookies.items() if k in existing and existing[k] != v)
    added = sum(1 for k in new_cookies if k not in existing)
    merged = dict(existing)
    merged.update(new_cookies)
    save_cookies(merged, expires=new_expires)
    return rotated, added


# ----- Cookies aus httpx.Cookies extrahieren ---------------------------------

def cookies_from_jar(jar: httpx.Cookies) -> tuple[dict, dict]:
    """Wandelt httpx.Cookies in {name: value} + {name: expires-iso}.

    Wird genutzt vom Heartbeat (eigener httpx-Client) und vom uploader.py
    (lib-internal Client).
    """
    values: dict = {}
    expires: dict = {}
    # httpx.Cookies wraps cookielib.CookieJar
    try:
        jar_inner = jar.jar  # type: ignore[attr-defined]
    except AttributeError:
        jar_inner = jar
    for c in jar_inner:
        if not c.name:
            continue
        # Nur Amazon-Cookies behalten
        if "amazon" not in (c.domain or ""):
            continue
        values[c.name] = c.value or ""
        if c.expires:
            try:
                expires[c.name] = datetime.fromtimestamp(c.expires, tz=timezone.utc).isoformat()
            except (OSError, ValueError):
                pass
    return values, expires


# ----- Heartbeat -------------------------------------------------------------

def _do_request(url: str, cookies: dict, *, timeout: float = 15.0) -> tuple[httpx.Response, dict, dict, int]:
    """Macht den Request, liefert Response + extrahierte Cookies + duration_ms."""
    t0 = datetime.now()
    with httpx.Client(
        cookies=dict(cookies),
        follow_redirects=False,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        },
    ) as client:
        r = client.get(url)
        new_values, new_expires = cookies_from_jar(client.cookies)
    duration_ms = int((datetime.now() - t0).total_seconds() * 1000)
    return r, new_values, new_expires, duration_ms


def heartbeat(url: str = PRIMARY_URL) -> HeartbeatResult:
    """Macht einen Heartbeat-Request gegen amazon.de.

    Bei Erfolg (HTTP 200) werden die rollierten Cookies in die DB gemerged
    und ein Status-Update geschrieben. Bei Fehler (nicht-200 oder Exception)
    bleibt die DB unangetastet und der Fehler wird als Status-Eintrag
    gespeichert.
    """
    cookies = load_cookies()
    if not cookies:
        return HeartbeatResult(
            ok=False, url=url, message="Keine Cookies in der DB (Setup noch nicht erfolgt).",
        )

    try:
        r, new_values, new_expires, duration_ms = _do_request(url, cookies)
    except Exception as e:  # noqa: BLE001
        result = HeartbeatResult(
            ok=False, url=url, message=f"HTTP-Exception: {e}"[:300], duration_ms=0,
        )
        _record_status(result)
        log.warning("amazon-heartbeat: %s", result.to_log())
        return result

    # Sign-In-Redirect detektieren (3xx mit auth/signin-Pfad = Session tot)
    is_signin_redirect = False
    if 300 <= r.status_code < 400:
        loc = r.headers.get("Location", "").lower()
        if "ap/signin" in loc or "auth.amazon" in loc:
            is_signin_redirect = True

    # Erfolg = 200 ODER 401/404 mit Cookie-Rotation (Step-Up-Auth-Seiten;
    # Session selbst noch valide — siehe Probe vom 2026-05-25).
    # NICHT erfolg: Sign-In-Redirect oder gar keine Cookies zurueck.
    rotated_keys = [k for k, v in new_values.items() if cookies.get(k) != v and k in cookies]
    new_keys = [k for k in new_values if k not in cookies]
    has_rotation = bool(rotated_keys or new_keys)

    if is_signin_redirect:
        result = HeartbeatResult(
            ok=False, status_code=r.status_code, url=url, duration_ms=duration_ms,
            message="Sign-In-Redirect — Session abgelaufen, manueller Re-Login noetig.",
        )
        _record_status(result)
        log.warning("amazon-heartbeat: %s", result.to_log())
        return result

    if r.status_code == 200 or has_rotation:
        # Cookies updaten — gilt als gesund
        n_rot, n_add = merge_into_db(new_values, new_expires) if has_rotation else (0, 0)
        result = HeartbeatResult(
            ok=True, status_code=r.status_code, url=url, duration_ms=duration_ms,
            rotated=rotated_keys, new=new_keys,
            message=f"{n_rot} cookies rotated, {n_add} added",
        )
        _record_status(result)
        log.info("amazon-heartbeat: %s", result.to_log())
        return result

    # Nicht-200 + keine Rotation = unbekanntes Problem
    result = HeartbeatResult(
        ok=False, status_code=r.status_code, url=url, duration_ms=duration_ms,
        message=f"Unerwarteter Status ohne Cookie-Rotation",
    )
    _record_status(result)
    log.warning("amazon-heartbeat: %s", result.to_log())
    return result


def secondary_refresh() -> HeartbeatResult:
    """Sekundaerer Heartbeat fuer ubid-acbde + x-acbde-Rotation.

    /gp/your-account/order-history liefert 401 (Step-Up-Auth), aber rotiert
    die Mittellauf-Cookies. Wird seltener gebraucht als Primary.
    """
    return heartbeat(SECONDARY_URL)


def _record_status(result: HeartbeatResult) -> None:
    """Speichert last-heartbeat-Status in die DB (fuer UI)."""
    try:
        with SessionLocal() as s:
            set_setting(s, LAST_HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())
            status = "ok" if result.ok else f"fail:{result.message[:200]}"
            set_setting(s, LAST_HEARTBEAT_STATUS_KEY, status)
    except Exception as e:  # noqa: BLE001
        log.debug("_record_status: %s", e)


# ----- Upload-Error-Tracking (v0.12.1) ---------------------------------------
# Ground-Truth-Signal fuers Health-Banner: wenn der uploader gerade 401 von Amazon
# bekommen hat, ist das die belastbarste Information ueber den Session-Zustand —
# wertvoller als jede Cookie-TTL-Heuristik. Wird vom uploader.py beim Setzen des
# in-process Skip-Mode persistiert und beim Reset wieder gecleart.

def record_upload_error(
    status_code: int,
    message: str,
    cam_name: Optional[str] = None,
) -> None:
    """Persistiert: 'letzter Upload-Versuch endete mit status_code'.

    Wird vom uploader._mark_cookies_expired() aufgerufen. Fehler hier sind nicht
    fatal (Banner waere dann ungenau, aber Upload-Pfad bleibt intakt).
    """
    try:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "status_code": int(status_code),
            "message": (message or "")[:300],
            "cam_name": (cam_name or "")[:80] or None,
        }
        with SessionLocal() as s:
            set_setting(s, LAST_UPLOAD_ERROR_KEY, json.dumps(payload))
    except Exception as e:  # noqa: BLE001
        log.debug("record_upload_error: %s", e)


def clear_upload_error() -> None:
    """Loescht den persistierten Upload-Fehler.

    Wird vom uploader.reset_client() nach einem manuellen Cookie-Refresh
    aufgerufen, damit das Banner sofort gruen wird ohne Warten auf den
    Window-Ablauf.
    """
    try:
        with SessionLocal() as s:
            set_setting(s, LAST_UPLOAD_ERROR_KEY, "")
    except Exception as e:  # noqa: BLE001
        log.debug("clear_upload_error: %s", e)


def recent_upload_error(
    window_minutes: int = RECENT_UPLOAD_ERROR_WINDOW_MIN,
) -> Optional[dict]:
    """Liefert den persistierten Upload-Fehler, falls juenger als window_minutes.

    Rueckgabe ergaenzt das gespeicherte Dict um `age_seconds` (int, fuer UI).
    None wenn nichts gespeichert, ungueltig, oder ausserhalb des Fensters.
    window_minutes <= 0 deaktiviert den Zeit-Cutoff.
    """
    try:
        with SessionLocal() as s:
            raw = get_setting(s, LAST_UPLOAD_ERROR_KEY, "")
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        ts_iso = data.get("ts")
        if not ts_iso:
            return None
        try:
            ts = datetime.fromisoformat(ts_iso)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
        age_seconds = int((datetime.now(timezone.utc) - ts).total_seconds())
        if window_minutes > 0 and age_seconds > window_minutes * 60:
            return None
        data["age_seconds"] = age_seconds
        return data
    except Exception as e:  # noqa: BLE001
        log.debug("recent_upload_error: %s", e)
        return None


# ----- Cookie-Health ---------------------------------------------------------

def cookie_health() -> list[CookieInfo]:
    """Liefert je Cookie {name, expires_at, seconds_to_expiry, status, required}.

    expires_at kommt aus der COOKIE_EXPIRES_KEY-Map (vom Heartbeat aufgesammelt).
    Wenn fuer einen Cookie kein Expires bekannt ist (z.B. weil er von einem
    manuellen Setup stammt und nie ueber HTTP gerollt wurde), bleibt das Feld
    None und status='unknown'.
    """
    cookies = load_cookies()
    with SessionLocal() as s:
        raw = get_setting(s, COOKIE_EXPIRES_KEY, "")
    try:
        exp_map = json.loads(raw) if raw else {}
        if not isinstance(exp_map, dict):
            exp_map = {}
    except json.JSONDecodeError:
        exp_map = {}

    now = datetime.now(timezone.utc)
    out: list[CookieInfo] = []
    for name in sorted(cookies):
        expires_iso = exp_map.get(name)
        seconds_to_expiry: Optional[int] = None
        status = "unknown"
        if expires_iso:
            try:
                exp_dt = datetime.fromisoformat(expires_iso)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                seconds_to_expiry = int((exp_dt - now).total_seconds())
                if seconds_to_expiry <= 0:
                    status = "expired"
                elif seconds_to_expiry < 24 * 3600:
                    status = "warn"
                else:
                    status = "ok"
            except (ValueError, TypeError):
                pass
        out.append(CookieInfo(
            name=name,
            expires_at=expires_iso,
            seconds_to_expiry=seconds_to_expiry,
            status=status,
            required=(name in LONG_LIVED_REQUIRED),
        ))
    return out


def last_heartbeat() -> tuple[Optional[str], str]:
    """Liefert (last_heartbeat_iso, status_string). Status "ok" oder "fail:msg" oder ""."""
    with SessionLocal() as s:
        ts = get_setting(s, LAST_HEARTBEAT_KEY, "")
        st = get_setting(s, LAST_HEARTBEAT_STATUS_KEY, "")
    return (ts or None), st


def health_summary() -> dict:
    """Aggregat fuer UI: gesamt-status, last_heartbeat, problematische cookies.

    Wichtig: 'warn'/'critical' im overall-Feld triggert nur durch PFLICHT-Cookies
    (LONG_LIVED_REQUIRED) oder einen fehlgeschlagenen Heartbeat. Akamai-Bot-
    Detection-Cookies wie ak_bmsc/bm_sv leben kurz und werden bei jedem Request
    automatisch neu gesetzt — die sollen das Banner nicht ungerechtfertigt
    triggern.

    v0.12.1: Zusaetzlich `last_upload_error`. Wenn der uploader in den letzten
    30 Min einen 401/403 von Amazon bekommen hat, wird overall hart auf
    'critical' gesetzt — das ist Ground-Truth (echte Server-Antwort) und schlaegt
    jede TTL-Heuristik. Loest das v0.12.0-Banner-Luge-Problem: bei abgelaufenem
    at-acbde (Status 'unknown' weil nie rolliert) blieb der Banner faelschlich
    auf gruen, obwohl Uploads schon 401 zogen.
    """
    cookies = cookie_health()
    ts, st = last_heartbeat()
    expired_all = [c for c in cookies if c.status == "expired"]
    warn_all = [c for c in cookies if c.status == "warn"]
    # Nur PFLICHT-Cookies zaehlen fuer das overall-Banner
    expired_required = [c for c in expired_all if c.required]
    warn_required = [c for c in warn_all if c.required]
    missing_required = [n for n in LONG_LIVED_REQUIRED if not any(c.name == n for c in cookies)]

    # v0.12.1: Ground-Truth-Signal aus dem uploader
    upload_err = recent_upload_error()

    overall = "ok"
    if upload_err and upload_err.get("status_code") in (401, 403):
        overall = "critical"
    elif expired_required or missing_required:
        overall = "critical"
    elif warn_required:
        overall = "warn"
    elif st.startswith("fail:"):
        overall = "warn"
    return {
        "overall": overall,
        "last_heartbeat": ts,
        "last_status": st,
        "cookies": [asdict(c) for c in cookies],
        # In den Listen weiterhin ALLE problematischen Cookies anzeigen,
        # damit der User Transparenz hat — nur das overall-Banner ist gefiltert.
        "expired": [c.name for c in expired_all],
        "warn": [c.name for c in warn_all],
        "missing_required": missing_required,
        # v0.12.1: Letzter belastbarer Upload-Fehler (oder None)
        "last_upload_error": upload_err,
    }
