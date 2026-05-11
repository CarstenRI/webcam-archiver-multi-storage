"""Amazon-Photos-Backend (wrappt die bestehende app.uploader).

Verhalten ist identisch zur bisherigen Direkt-Verwendung von uploader.upload_file.
Album-Zuordnung erfolgt weiterhin über Cam.album (Amazon-spezifisch); das
Pfad-Template wird hier ignoriert.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .. import uploader as _legacy_uploader
from .base import (
    BackendError, ConfigField, StorageBackend, UploadResult,
)


class AmazonBackend(StorageBackend):
    type_id = "amazon"
    display_name = "Amazon Photos"
    description = (
        "Lädt Bilder zu Amazon Photos. Cookies werden global unter "
        "'Einstellungen' verwaltet. Album-Zuordnung läuft über das Cam-Album."
    )

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        # Amazon braucht keine target-spezifische Config – Cookies global.
        return []

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
            res = _legacy_uploader.upload_file(
                path,
                album_name=album_name,
                album_db_id=album_db_id,
            )
        except _legacy_uploader.UploadError as e:
            return UploadResult(status="error", message=str(e)[:500])
        except Exception as e:  # noqa: BLE001
            return UploadResult(status="error", message=f"unerwartet: {e}"[:500])

        status_str = res.get("status", "")
        album_status = res.get("album", "")
        msg = ""
        if album_status and album_status != "no_album":
            msg = f"album={album_status}"
        if status_str == "duplicate_filename":
            return UploadResult(
                status="skipped",
                message="Amazon: duplicate filename",
                remote_ref=None,
                bytes=len(image_bytes),
            )
        return UploadResult(
            status="success",
            message=msg,
            remote_ref=res.get("node_id"),
            bytes=len(image_bytes),
        )

    def health_check(self) -> tuple[bool, str]:
        return _legacy_uploader.health_check()

    def test_connection(self) -> tuple[bool, str]:
        # Bewusst kein Netzwerk-Call – Cookie-Check reicht.
        ok, msg = _legacy_uploader.health_check()
        if ok:
            return True, "Cookies vorhanden"
        return False, msg
