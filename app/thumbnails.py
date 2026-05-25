"""Thumbnail-Cache (v0.11.0).

Liefert WebP-Thumbnails fuer Timelapse-Frames. Cache liegt unter
`data_dir/thumbs/cam-{cam_id}/{upload_id}.webp`. Synchron beim Upload
(scheduler) + lazy on-demand (Frame-Endpoint mit ?thumb=1).

Public API:
    path_for(cam_id, upload_id) -> Path
    ensure(orig_path, cam_id, upload_id, *, force=False) -> Path | None
    generate(orig_path, dest) -> bool
    delete(cam_id, upload_id) -> bool
    cache_size_bytes() -> int
    evict_to_cap(cap_bytes) -> dict

Performance: Pillow oeffnet das Original, skaliert auf max. 640x360
(thumbnail-Algorithmus = preserve aspect, fits inside box) und speichert
als WebP qual 80. Typische Groesse: 30-60 KB.

Fehler werden geloggt, nie gethrown — Upload/Frame-Listing duerfen niemals
wegen eines defekten Thumbs failen.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

from .config import settings

log = logging.getLogger(__name__)


def path_for(cam_id: int, upload_id: int) -> Path:
    """Liefert den Cache-Pfad fuer ein Thumbnail (legt das Verzeichnis NICHT an)."""
    return settings.thumbnail_dir / f"cam-{cam_id}" / f"{upload_id}.webp"


def generate(orig_path: Path | str, dest: Path) -> bool:
    """Erzeugt ein WebP-Thumbnail aus orig_path bei dest.

    Returns True bei Erfolg, False sonst. Wirft NICHT — Aufrufer kann
    safe weiterlaufen.
    """
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow nicht verfuegbar — Thumbnail uebersprungen")
        return False

    orig = Path(orig_path)
    if not orig.is_file():
        log.debug("Thumbnail: Original fehlt: %s", orig)
        return False

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("Thumbnail: mkdir %s fehlgeschlagen: %s", dest.parent, e)
        return False

    target_w = max(64, int(settings.thumbnail_width))
    target_h = max(64, int(settings.thumbnail_height))
    quality = max(1, min(100, int(settings.thumbnail_webp_quality)))

    tmp_dest = dest.with_suffix(".webp.tmp")
    try:
        with Image.open(orig) as im:
            # EXIF-Rotation respektieren, falls vorhanden
            try:
                from PIL import ImageOps
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            im.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
            im.save(tmp_dest, format="WEBP", quality=quality, method=4)
        os.replace(tmp_dest, dest)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Thumbnail-Generate fail %s -> %s: %s", orig, dest, e)
        try:
            if tmp_dest.exists():
                tmp_dest.unlink()
        except OSError:
            pass
        return False


def ensure(
    orig_path: Path | str, cam_id: int, upload_id: int, *, force: bool = False,
) -> Optional[Path]:
    """Stellt sicher dass das Thumbnail existiert. Returns Pfad oder None bei Fehler.

    Wenn das Thumb schon da und nicht aelter als das Original ist, wird nichts
    getan (Idempotent). `force=True` regeneriert immer.
    """
    dest = path_for(cam_id, upload_id)
    orig = Path(orig_path)
    if not force and dest.is_file():
        try:
            if not orig.is_file() or dest.stat().st_mtime >= orig.stat().st_mtime:
                return dest
        except OSError:
            return dest
    if generate(orig, dest):
        return dest
    return None


def delete(cam_id: int, upload_id: int) -> bool:
    """Loescht das Thumbnail (idempotent). Returns True wenn etwas geloescht wurde."""
    p = path_for(cam_id, upload_id)
    if not p.is_file():
        return False
    try:
        p.unlink()
        # Leeres cam-{id}-Verzeichnis aufraeumen
        try:
            p.parent.rmdir()
        except OSError:
            pass
        return True
    except OSError as e:
        log.warning("Thumbnail-Delete fail %s: %s", p, e)
        return False


def cache_size_bytes(root: Optional[Path] = None) -> int:
    """Aufsummierte Groesse aller WebP-Files im Cache-Verzeichnis."""
    root = root or settings.thumbnail_dir
    if not root.exists():
        return 0
    total = 0
    try:
        for cam_dir in root.iterdir():
            if not cam_dir.is_dir():
                continue
            for f in cam_dir.iterdir():
                if f.suffix.lower() == ".webp":
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
    except OSError:
        pass
    return total


def evict_to_cap(cap_bytes: int) -> dict:
    """LRU-Eviction (mtime-basiert): loescht aelteste Thumbs bis unter cap_bytes.

    Returns {removed, freed_bytes, total_before, total_after}.
    """
    summary = {
        "removed": 0, "freed_bytes": 0,
        "total_before": 0, "total_after": 0,
    }
    root = settings.thumbnail_dir
    if not root.exists():
        return summary

    # Alle Thumbs einsammeln mit mtime + size
    entries: list[tuple[float, int, Path]] = []
    try:
        for cam_dir in root.iterdir():
            if not cam_dir.is_dir():
                continue
            for f in cam_dir.iterdir():
                if f.suffix.lower() != ".webp":
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, f))
    except OSError:
        pass

    total = sum(sz for _, sz, _ in entries)
    summary["total_before"] = total
    summary["total_after"] = total

    if cap_bytes <= 0 or total <= cap_bytes:
        return summary

    # Aelteste zuerst
    entries.sort(key=lambda t: t[0])
    for _, sz, f in entries:
        if total <= cap_bytes:
            break
        try:
            f.unlink()
            total -= sz
            summary["removed"] += 1
            summary["freed_bytes"] += sz
        except OSError as e:
            log.warning("evict_to_cap unlink %s: %s", f, e)

    # Verwaiste leere cam-Verzeichnisse loeschen
    try:
        for cam_dir in root.iterdir():
            if cam_dir.is_dir():
                try:
                    cam_dir.rmdir()
                except OSError:
                    pass
    except OSError:
        pass

    summary["total_after"] = total
    return summary


def prune_orphans(valid_keys: set[tuple[int, int]]) -> int:
    """Loescht Thumbs, deren (cam_id, upload_id) nicht mehr in valid_keys ist.

    `valid_keys` enthaelt alle TargetUpload-IDs mit pruned_at IS NULL aus
    local-Targets. Returns Anzahl geloeschter Files.
    """
    root = settings.thumbnail_dir
    if not root.exists():
        return 0
    removed = 0
    try:
        for cam_dir in root.iterdir():
            if not cam_dir.is_dir() or not cam_dir.name.startswith("cam-"):
                continue
            try:
                cam_id = int(cam_dir.name[len("cam-"):])
            except ValueError:
                continue
            for f in cam_dir.iterdir():
                if f.suffix.lower() != ".webp":
                    continue
                try:
                    upload_id = int(f.stem)
                except ValueError:
                    continue
                if (cam_id, upload_id) not in valid_keys:
                    try:
                        f.unlink()
                        removed += 1
                    except OSError as e:
                        log.warning("prune_orphans %s: %s", f, e)
    except OSError:
        pass
    return removed
