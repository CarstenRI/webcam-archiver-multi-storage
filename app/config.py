"""Konfiguration via Pydantic-Settings, gespeist aus Env-Variablen / .env."""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Web-UI
    host: str = "0.0.0.0"
    port: int = 8080
    base_url: str = "http://localhost:8080"
    # Pfad zur .env-Datei, die systemd via EnvironmentFile liest.
    # Wird von der Settings-UI fuer Port-Aenderungen geschrieben.
    env_file_path: Path = Path("/etc/webcam-uploader/.env")

    # Auth
    auth_user: str = "admin"
    auth_password: str = "change-me-please"

    # Storage
    data_dir: Path = Path("/var/lib/webcam-uploader")
    tmp_dir: Path = Path("/var/lib/webcam-uploader/tmp")
    log_level: str = "INFO"

    # Duplicate-Detection
    duplicate_hash_threshold: int = 5

    # Scheduler
    max_concurrent_fetches: int = 5

    @property
    def db_path(self) -> Path:
        return self.data_dir / "webcam-uploader.sqlite3"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
