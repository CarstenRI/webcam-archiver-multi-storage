"""Lokales Filesystem-Backend.

Schreibt Bilder in ein konfigurierbares Basis-Verzeichnis, Pfad-Aufbau gemäß
`path_template` des Storage-Targets. Vor dem Upload wird das Verzeichnis
automatisch angelegt.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import (
    BackendError, ConfigField, StorageBackend, UploadResult, render_template,
)

log = logging.getLogger(__name__)


class LocalBackend(StorageBackend):
    type_id = "local"
    display_name = "Lokales Verzeichnis"
    description = (
        "Speichert die Bilder in einem Verzeichnis auf dem Server selbst. "
        "Pfad-Template steuert die Unterordner-Struktur."
    )

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(
                key="base_path",
                label="Basis-Verzeichnis",
                kind="text",
                required=True,
                placeholder="/srv/webcam-archiv",
                help="Absoluter Pfad. Wird beim ersten Schreiben angelegt, falls möglich.",
            ),
        ]

    def _base_path(self) -> Path:
        bp = (self.config.get("base_path") or "").strip()
        if not bp:
            raise BackendError("Basis-Verzeichnis nicht gesetzt.")
        return Path(bp)

    def upload(
        self,
        path: Path,
        image_bytes: bytes,
        cam_id: int,
        cam_name: str,
        album_name: Optional[str] = None,
        album_db_id: Optional[int] = None,
        taken_at: Optional[datetime] = None,
    ) -> UploadResult:
        try:
            base = self._base_path()
            rel = render_template(
                self.path_template, cam_id, cam_name, album_name, image_bytes, taken_at,
            )
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # Wir kopieren aus der bereits geschriebenen Tmp-Datei, um die
            # Original-Bytes 1:1 zu erhalten (inkl. EXIF etc.).
            shutil.copyfile(path, target)
            return UploadResult(
                status="success",
                message="",
                remote_ref=str(target),
                bytes=len(image_bytes),
            )
        except BackendError as e:
            return UploadResult(status="error", message=str(e)[:500])
        except OSError as e:
            return UploadResult(status="error", message=f"FS: {e}"[:500])
        except Exception as e:  # noqa: BLE001
            return UploadResult(status="error", message=f"unerwartet: {e}"[:500])


    def delete(self, remote_ref: str) -> tuple[bool, str]:
        if not remote_ref:
            return False, "kein Pfad"
        try:
            p = Path(remote_ref)
            if p.exists():
                p.unlink()
            # Aufraeumen leerer Eltern-Verzeichnisse bis zum base_path
            try:
                base = self._base_path().resolve()
                cur = p.parent
                while cur != cur.parent:
                    try:
                        if cur.resolve() == base:
                            break
                    except (OSError, ValueError):
                        break
                    try:
                        cur.rmdir()
                    except OSError:
                        break
                    cur = cur.parent
            except Exception:
                pass
            return True, str(p)
        except Exception as e:  # noqa: BLE001
            return False, f"local.delete: {e}"

    def health_check(self) -> tuple[bool, str]:
        bp = (self.config.get("base_path") or "").strip()
        if not bp:
            return False, "Basis-Verzeichnis fehlt."
        p = Path(bp)
        if not p.exists():
            return False, f"Pfad existiert nicht: {bp}"
        if not p.is_dir():
            return False, f"Pfad ist kein Verzeichnis: {bp}"
        if not os.access(p, os.W_OK):
            return False, f"Keine Schreibrechte: {bp}"
        return True, f"OK ({bp})"

    def test_connection(self) -> tuple[bool, str]:
        bp = (self.config.get("base_path") or "").strip()
        if not bp:
            return False, "Basis-Verzeichnis fehlt."
        p = Path(bp)
        # Versuche das Verzeichnis anzulegen
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"Kann Verzeichnis nicht anlegen: {e}"
        # Schreibtest
        test_file = p / ".webcam-uploader-write-test"
        try:
            test_file.write_text("ok")
            test_file.unlink()
        except OSError as e:
            return False, f"Schreibtest fehlgeschlagen: {e}"
        return True, f"Lokal beschreibbar: {bp}"
