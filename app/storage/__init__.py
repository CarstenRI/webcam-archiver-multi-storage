"""Storage-Backend-Subsystem: Multi-Target-Upload für Webcam-Bilder.

Architektur:
- `base.StorageBackend` ist das Interface, das alle Backends implementieren.
- `registry.get_backend(target)` liefert die Backend-Instanz für ein DB-Target.
- `path.render_template()` rendert Pfad-Templates mit Platzhaltern.

Implementierte Backends:
- amazon: Amazon Photos (über bestehende uploader.py)
- local:  Lokales Verzeichnis
- sftp:   SFTP/SSH auf entferntem Server
- s3:     S3-kompatibel (AWS, B2, R2, MinIO, Wasabi, Hetzner Object Storage)
"""
from .base import StorageBackend, UploadResult, ConfigField, BackendError
from .registry import get_backend, list_backend_types, backend_meta

__all__ = [
    "StorageBackend",
    "UploadResult",
    "ConfigField",
    "BackendError",
    "get_backend",
    "list_backend_types",
    "backend_meta",
]
