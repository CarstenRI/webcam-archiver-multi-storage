"""Basis-Interface für Storage-Backends + Pfad-Template-Renderer."""
from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class BackendError(Exception):
    """Allgemeiner Backend-Fehler – wird vom Scheduler abgefangen."""
    pass


@dataclass
class UploadResult:
    """Ergebnis eines einzelnen Uploads zu einem Storage-Target."""
    status: str  # "success" | "error" | "skipped"
    message: str = ""
    remote_ref: Optional[str] = None  # Pfad, Node-ID, S3-Key etc.
    bytes: int = 0


@dataclass
class ConfigField:
    """Beschreibt ein Feld im dynamischen UI-Formular eines Backend-Typs."""
    key: str
    label: str
    kind: str = "text"  # "text" | "password" | "number" | "checkbox" | "textarea" | "select"
    required: bool = False
    default: Any = ""
    placeholder: str = ""
    help: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)  # für kind="select": [(value, label), ...]


def slugify(value: str) -> str:
    """Erzeugt ASCII-Slug aus beliebigem String (für Pfade/Object-Keys)."""
    if not value:
        return "cam"
    # Unicode → ASCII (NFKD: ä → a, ß → ss vorher abfangen)
    value = value.replace("ß", "ss")
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_-.")
    return value.lower() or "cam"


def _detect_ext(data: bytes, fallback: str = ".jpg") -> str:
    """Erkennt Bild-Endung anhand der ersten Bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:3] == b"\x89PN":
        return ".png"
    if data[:3] == b"GIF":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return fallback


def render_template(
    template: str,
    cam_id: int,
    cam_name: str,
    album_name: Optional[str],
    image_bytes: bytes,
    ts: Optional[datetime] = None,
) -> str:
    """Rendert ein Pfad-Template mit Platzhaltern.

    Unterstützte Platzhalter:
      {cam_id}, {cam_name}, {cam_slug}, {album}, {album_slug},
      {Y}, {m}, {d}, {H}, {M}, {S},
      {ymd}    -> YYYY-MM-DD
      {hms}    -> HH-MM-SS
      {ts}     -> Unix-Timestamp (int)
      {ext}    -> .jpg / .png / .gif / .webp (mit führendem Punkt)

    Unbekannte Platzhalter bleiben unverändert. Doppelte oder führende
    Slashes werden normalisiert.
    """
    if ts is None:
        ts = datetime.now()
    ext = _detect_ext(image_bytes)
    vars = {
        "cam_id": str(cam_id),
        "cam_name": cam_name or "",
        "cam_slug": slugify(cam_name or f"cam{cam_id}"),
        "album": album_name or "",
        "album_slug": slugify(album_name) if album_name else "",
        "Y": ts.strftime("%Y"),
        "m": ts.strftime("%m"),
        "d": ts.strftime("%d"),
        "H": ts.strftime("%H"),
        "M": ts.strftime("%M"),
        "S": ts.strftime("%S"),
        "ymd": ts.strftime("%Y-%m-%d"),
        "hms": ts.strftime("%H-%M-%S"),
        "ts": str(int(ts.timestamp())),
        "ext": ext,
    }

    def repl(match):
        key = match.group(1)
        return vars.get(key, match.group(0))

    rendered = re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", repl, template or "")
    # Normalisierung: doppelte Slashes, führende Slashes
    rendered = re.sub(r"/+", "/", rendered)
    rendered = rendered.lstrip("/")
    return rendered or f"cam{cam_id}{ext}"


class StorageBackend(ABC):
    """Basis-Klasse für alle Storage-Backends.

    Instanzen werden pro DB-Target erzeugt; config kommt aus
    StorageTarget.config_json (JSON-deserialisiert).
    """

    # Typ-Identifier (muss mit DB-Spalte `type` übereinstimmen)
    type_id: str = ""
    # Anzeigename für UI
    display_name: str = ""
    # Kurzbeschreibung für UI
    description: str = ""

    def __init__(self, config: dict, path_template: str = "", target_name: str = ""):
        self.config = config or {}
        self.path_template = path_template or "{cam_slug}/{Y}-{m}-{d}/{H}-{M}-{S}{ext}"
        self.target_name = target_name

    @classmethod
    @abstractmethod
    def config_fields(cls) -> list[ConfigField]:
        """Liefert die UI-Feld-Definitionen für dieses Backend."""
        ...

    @abstractmethod
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
        """Lädt ein Bild zu diesem Storage-Target hoch.

        `path` ist der temporäre lokale Pfad (kann ignoriert werden, wenn das
        Backend image_bytes direkt nutzen will). `taken_at` ist der Zeitstempel
        des Bilds (für Pfad-Template).
        """
        ...

    def health_check(self) -> tuple[bool, str]:
        """Schneller Status-Check ohne Bild-Upload. Default: ok wenn Config valide."""
        return True, "OK"


    def delete(self, remote_ref: str) -> tuple[bool, str]:
        """Loescht das Objekt referenziert durch remote_ref. Returns (ok, msg).

        Default: nicht unterstuetzt. Backends die Retention unterstuetzen,
        ueberschreiben diese Methode.
        """
        return False, "delete not supported"

    def test_connection(self) -> tuple[bool, str]:
        """Test, ob das Target erreichbar/beschreibbar ist. Wird beim Speichern
        des Targets in der UI aufgerufen. Default: gleich wie health_check."""
        return self.health_check()
