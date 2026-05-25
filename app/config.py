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

    # Timelapse (v0.10.0)
    timelapse_cache_max_gb: int = 5
    timelapse_retention_per_cam: int = 10
    timelapse_worker_interval_s: int = 5

    # Thumbnails (v0.11.0)
    thumbnail_cache_max_mb: int = 500
    thumbnail_width: int = 640
    thumbnail_height: int = 360
    thumbnail_webp_quality: int = 80

    # Amazon-Session-Heartbeat (v0.12.0)
    # Primary-Heartbeat (GET /photos/) alle X Minuten.
    # Rotiert session-id+session-token, haelt Session lange am Leben.
    amazon_heartbeat_minutes: int = 360  # 6h
    # Sekundaer-Heartbeat (GET /your-account/*) alle X Minuten.
    # Rotiert zusaetzlich ubid-acbde + x-acbde.
    amazon_secondary_minutes: int = 1440  # 24h
    # 0 = Heartbeats ausgeschaltet
    amazon_heartbeat_enabled: bool = True

    @property
    def timelapse_dir(self) -> Path:
        return self.data_dir / "timelapse"

    @property
    def thumbnail_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "webcam-uploader.sqlite3"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.timelapse_dir.mkdir(parents=True, exist_ok=True)
        (self.timelapse_dir / "tmp").mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
