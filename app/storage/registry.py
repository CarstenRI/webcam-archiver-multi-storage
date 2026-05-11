"""Registry für Storage-Backend-Typen + Factory-Funktion."""
from __future__ import annotations

import json
import logging
from typing import Type

from .amazon import AmazonBackend
from .base import BackendError, StorageBackend
from .local import LocalBackend
from .s3 import S3Backend
from .sftp import SftpBackend

log = logging.getLogger(__name__)

# Type-ID -> Backend-Klasse
_REGISTRY: dict[str, Type[StorageBackend]] = {
    AmazonBackend.type_id: AmazonBackend,
    LocalBackend.type_id: LocalBackend,
    SftpBackend.type_id: SftpBackend,
    S3Backend.type_id: S3Backend,
}


def list_backend_types() -> list[tuple[str, str, str]]:
    """Liefert [(type_id, display_name, description), ...] für UI-Auswahl."""
    return [
        (cls.type_id, cls.display_name, cls.description)
        for cls in _REGISTRY.values()
    ]


def backend_meta(type_id: str) -> Type[StorageBackend]:
    """Liefert die Backend-Klasse zu einem type_id. Raises BackendError."""
    cls = _REGISTRY.get(type_id)
    if cls is None:
        raise BackendError(f"Unbekannter Storage-Typ: {type_id}")
    return cls


def get_backend(target) -> StorageBackend:
    """Erzeugt eine Backend-Instanz aus einem DB-StorageTarget-Objekt."""
    cls = backend_meta(target.type)
    try:
        config = json.loads(target.config_json or "{}")
        if not isinstance(config, dict):
            config = {}
    except json.JSONDecodeError:
        log.warning("StorageTarget %s: ungültiges config_json", target.name)
        config = {}
    return cls(
        config=config,
        path_template=target.path_template or "",
        target_name=target.name,
    )
