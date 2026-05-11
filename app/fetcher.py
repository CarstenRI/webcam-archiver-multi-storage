"""HTTP-Fetch + Bildverarbeitung + perceptual hash."""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import httpx
import imagehash
from PIL import Image

from .config import settings

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class FetchError(Exception):
    pass


def _parse_headers(headers_json: Optional[str]) -> dict:
    if not headers_json:
        return {}
    try:
        return json.loads(headers_json)
    except json.JSONDecodeError:
        return {}


def fetch_image(url: str, headers_json: Optional[str] = None) -> bytes:
    """Lädt das Bild von der URL. Wirft FetchError bei Problemen."""
    headers = {"User-Agent": "webcam-uploader/0.1"}
    headers.update(_parse_headers(headers_json))
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "image" not in ctype.lower() and not (
                len(r.content) > 8 and r.content[:3] in (b"\xff\xd8\xff", b"\x89PN", b"GIF")
            ):
                raise FetchError(f"Kein Bild (content-type={ctype})")
            return r.content
    except httpx.HTTPError as e:
        raise FetchError(str(e)) from e


def compute_phash(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    return str(imagehash.phash(img))


def hashes_similar(a: str, b: str, threshold: int) -> bool:
    """Vergleicht zwei perceptual hashes via Hamming-Distanz."""
    if not a or not b:
        return False
    try:
        ha = imagehash.hex_to_hash(a)
        hb = imagehash.hex_to_hash(b)
        return (ha - hb) <= threshold
    except Exception:
        return False


def save_temp_image(cam_id: int, image_bytes: bytes) -> Path:
    """Schreibt Bild in den TMP-Bereich. Returns Pfad."""
    settings.ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = ".jpg"
    if image_bytes[:3] == b"\x89PN":
        suffix = ".png"
    elif image_bytes[:3] == b"GIF":
        suffix = ".gif"
    p = settings.tmp_dir / f"cam{cam_id}-{ts}{suffix}"
    p.write_bytes(image_bytes)
    return p


def save_preview(cam_id: int, image_bytes: bytes) -> Path:
    """Speichert zwei Vorschauen fuer das Dashboard:
    - cam{id}.jpg       max. 640px (Grid-Kachel, schnell)
    - cam{id}_full.jpg  max. 1920px (Lightbox / Bild gross anzeigen)
    """
    settings.ensure_dirs()
    preview_dir = settings.data_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    p_thumb = preview_dir / f"cam{cam_id}.jpg"
    p_full = preview_dir / f"cam{cam_id}_full.jpg"
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        # Full erst (auf groesserer Kopie), dann Thumb
        full = img.copy()
        full.thumbnail((1920, 1920))
        full.save(p_full, "JPEG", quality=85)
        thumb = img.copy()
        thumb.thumbnail((640, 640))
        thumb.save(p_thumb, "JPEG", quality=80)
    except Exception:
        # Fallback: Originalbytes (z.B. wenn Pillow das Format nicht mag)
        p_thumb.write_bytes(image_bytes[:1_500_000])
        # Full = Kopie des Thumbs als Notnagel
        try:
            p_full.write_bytes(p_thumb.read_bytes())
        except Exception:
            pass
    return p_thumb
