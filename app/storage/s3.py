"""S3-kompatibles Backend (boto3).

Funktioniert mit AWS S3, Backblaze B2 (S3 API), Cloudflare R2, MinIO, Wasabi,
Hetzner Object Storage und allen weiteren S3-kompatiblen Endpoints. Custom-
Endpoint und Region sind frei konfigurierbar.
"""
from __future__ import annotations

import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import (
    BackendError, ConfigField, StorageBackend, UploadResult, render_template,
)

log = logging.getLogger(__name__)


class S3Backend(StorageBackend):
    type_id = "s3"
    display_name = "S3 (AWS, B2, R2, MinIO, Wasabi, ...)"
    description = (
        "S3-kompatibler Object-Storage. Custom-Endpoint optional für nicht-AWS-Anbieter. "
        "Pfad-Template wird zum Object-Key."
    )

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(
                key="endpoint_url", label="Endpoint-URL (optional)",
                placeholder="https://s3.eu-central-003.backblazeb2.com",
                help="Leer lassen für AWS S3. Beispiele: B2 (eu-central-003.backblazeb2.com), "
                     "R2 (https://<accountid>.r2.cloudflarestorage.com), MinIO (https://minio.example.com), "
                     "Wasabi (s3.eu-central-2.wasabisys.com), Hetzner (https://<region>.your-objectstorage.com)",
            ),
            ConfigField(
                key="region", label="Region", required=True,
                placeholder="eu-central-1",
                default="auto",
            ),
            ConfigField(
                key="bucket", label="Bucket", required=True,
                placeholder="my-webcam-archive",
            ),
            ConfigField(
                key="access_key", label="Access Key ID", required=True,
            ),
            ConfigField(
                key="secret_key", label="Secret Access Key", kind="password", required=True,
            ),
            ConfigField(
                key="addressing_style", label="Addressing-Style", kind="select",
                default="auto",
                options=[
                    ("auto", "Auto"),
                    ("virtual", "Virtual-hosted (Standard AWS)"),
                    ("path", "Path-style (MinIO, ältere Server)"),
                ],
            ),
            ConfigField(
                key="acl", label="ACL", kind="select",
                default="private",
                options=[
                    ("private", "private"),
                    ("public-read", "public-read"),
                    ("", "(keine ACL setzen)"),
                ],
                help="Manche Anbieter (R2, B2 mit Object Lock) unterstützen kein ACL. Bei Fehler '(keine ACL setzen)' wählen.",
            ),
        ]

    def _client(self):
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as e:
            raise BackendError(f"boto3 nicht installiert: {e}")

        endpoint = (self.config.get("endpoint_url") or "").strip() or None
        region = (self.config.get("region") or "").strip() or "auto"
        access_key = (self.config.get("access_key") or "").strip()
        secret_key = (self.config.get("secret_key") or "").strip()
        if not access_key or not secret_key:
            raise BackendError("Access-Key / Secret fehlt.")
        addressing = (self.config.get("addressing_style") or "auto").strip()
        if addressing == "auto":
            addressing = "path" if endpoint else "virtual"

        cfg = BotoConfig(
            s3={"addressing_style": addressing},
            signature_version="s3v4",
            connect_timeout=15,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        try:
            return boto3.client(
                "s3",
                endpoint_url=endpoint,
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=cfg,
            )
        except Exception as e:  # noqa: BLE001
            raise BackendError(f"S3-Client-Init fehlgeschlagen: {e}")

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
            bucket = (self.config.get("bucket") or "").strip()
            if not bucket:
                return UploadResult(status="error", message="Bucket fehlt.")
            key = render_template(
                self.path_template, cam_id, cam_name, album_name, image_bytes, taken_at,
            )
            content_type = mimetypes.guess_type(key)[0] or "image/jpeg"
            acl = (self.config.get("acl") or "").strip()

            extra_args = {"ContentType": content_type}
            if acl:
                extra_args["ACL"] = acl

            client = self._client()
            with open(path, "rb") as f:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=f.read(),
                    **extra_args,
                )
            return UploadResult(
                status="success",
                message="",
                remote_ref=f"s3://{bucket}/{key}",
                bytes=len(image_bytes),
            )
        except BackendError as e:
            return UploadResult(status="error", message=str(e)[:500])
        except Exception as e:  # noqa: BLE001
            return UploadResult(status="error", message=f"S3: {e}"[:500])


    def delete(self, remote_ref: str) -> tuple[bool, str]:
        if not remote_ref:
            return False, "kein Ref"
        try:
            # remote_ref Format: "s3://bucket/key"
            if remote_ref.startswith("s3://"):
                _, _, rest = remote_ref.partition("s3://")
                bucket, _, key = rest.partition("/")
            else:
                bucket = (self.config.get("bucket") or "").strip()
                key = remote_ref
            if not (bucket and key):
                return False, f"ungueltige Ref: {remote_ref}"
            client = self._client()
            client.delete_object(Bucket=bucket, Key=key)
            return True, remote_ref
        except BackendError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"s3.delete: {e}"

    def health_check(self) -> tuple[bool, str]:
        for k in ("bucket", "access_key", "secret_key"):
            if not (self.config.get(k) or "").strip():
                return False, f"Feld '{k}' fehlt."
        return True, "Konfiguriert (Test-Verbindung beim Speichern)"

    def test_connection(self) -> tuple[bool, str]:
        try:
            client = self._client()
            bucket = (self.config.get("bucket") or "").strip()
            if not bucket:
                return False, "Bucket fehlt."
            # head_bucket – billiger Auth-Test
            client.head_bucket(Bucket=bucket)
            # Schreibtest: Mini-Object hochladen und löschen
            test_key = ".webcam-uploader-write-test"
            client.put_object(Bucket=bucket, Key=test_key, Body=b"ok")
            try:
                client.delete_object(Bucket=bucket, Key=test_key)
            except Exception:
                pass
            return True, f"S3 OK → {bucket}"
        except BackendError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"S3-Test: {e}"
