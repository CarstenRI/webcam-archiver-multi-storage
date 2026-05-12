"""SQLite-Datenbank: Models und Session-Factory."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Table, Text,
    Column, create_engine, func, text,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

from .config import settings


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )


class User(Base):
    """Authentifizierungs-User mit Rolle (admin/viewer)."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # "admin" | "viewer"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )


class Album(Base):
    __tablename__ = "albums"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    amazon_album_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    cams: Mapped[list["Cam"]] = relationship(back_populates="album")


cam_storage_targets = Table(
    "cam_storage_targets",
    Base.metadata,
    Column("cam_id", Integer, ForeignKey("cams.id", ondelete="CASCADE"), primary_key=True),
    Column("storage_target_id", Integer, ForeignKey("storage_targets.id", ondelete="CASCADE"), primary_key=True),
)


class Cam(Base):
    __tablename__ = "cams"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    headers_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    album_id: Mapped[Optional[int]] = mapped_column(ForeignKey("albums.id"), nullable=True)
    album: Mapped[Optional[Album]] = relationship(back_populates="cams")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    time_start: Mapped[str] = mapped_column(String(5), default="")
    time_end: Mapped[str] = mapped_column(String(5), default="")
    weekdays: Mapped[str] = mapped_column(String(7), default="1111111")
    use_solar: Mapped[bool] = mapped_column(Boolean, default=False)
    latitude: Mapped[Optional[float]] = mapped_column(nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    solar_offset_min: Mapped[int] = mapped_column(Integer, default=0)
    skip_duplicates: Mapped[bool] = mapped_column(Boolean, default=True)
    last_phash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_fetch_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    last_preview_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    duplicate_hash_threshold: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # v0.10.0: bevorzugtes Storage-Target fuer Timelapse-Quelle. Default None = erstes
    # aktives local-Target dieser Cam. Nullable, weil viele Cams kein Timelapse nutzen.
    timelapse_source_target_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("storage_targets.id", ondelete="SET NULL"), nullable=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # Persistente Lifetime-Counter (unabhaengig von der fetches-Tabelle).
    # Werden inkrementiert beim Erstellen eines Fetch-Eintrags und bleiben
    # auch dann erhalten, wenn der User die Logs auf der UI loescht.
    total_uploads: Mapped[int] = mapped_column(Integer, default=0)
    total_duplicates: Mapped[int] = mapped_column(Integer, default=0)
    total_errors: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )
    fetches: Mapped[list["Fetch"]] = relationship(
        back_populates="cam", cascade="all, delete-orphan"
    )
    storage_targets: Mapped[list["StorageTarget"]] = relationship(
        secondary=cam_storage_targets, back_populates="cams"
    )


class StorageTarget(Base):
    """Konfiguriertes Speicherziel (Amazon, Local, SFTP, S3, Immich, ...)."""
    __tablename__ = "storage_targets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    type: Mapped[str] = mapped_column(String(40))
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    path_template: Mapped[str] = mapped_column(
        String(400),
        default="{cam_slug}/{Y}-{m}-{d}/{H}-{M}-{S}{ext}",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # Retention: max. Anzahl Bilder pro Cam (0 = unbegrenzt). Beim Upload werden
    # die aeltesten ueberzaehligen Bilder geloescht. Amazon ignoriert das (kein
    # delete-API), aber wir setzen das Feld trotzdem in der DB.
    retention_per_cam: Mapped[int] = mapped_column(Integer, default=0)
    last_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    last_status_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_status_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )
    cams: Mapped[list["Cam"]] = relationship(
        secondary=cam_storage_targets, back_populates="storage_targets"
    )


class Fetch(Base):
    __tablename__ = "fetches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cam_id: Mapped[int] = mapped_column(ForeignKey("cams.id"))
    cam: Mapped[Cam] = relationship(back_populates="fetches")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    phash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_uploads: Mapped[list["TargetUpload"]] = relationship(
        back_populates="fetch", cascade="all, delete-orphan"
    )


class TargetUpload(Base):
    """Ein Upload-Ergebnis pro (Fetch, StorageTarget)."""
    __tablename__ = "target_uploads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_id: Mapped[int] = mapped_column(ForeignKey("fetches.id", ondelete="CASCADE"))
    fetch: Mapped[Fetch] = relationship(back_populates="target_uploads")
    storage_target_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("storage_targets.id", ondelete="SET NULL"), nullable=True
    )
    target_name: Mapped[str] = mapped_column(String(120), default="")
    target_type: Mapped[str] = mapped_column(String(40), default="")
    # cam_id wird denormalisiert (zusaetzlich zum Pfad ueber fetch.cam_id),
    # damit Retention-Queries einen schnellen Index nutzen koennen.
    cam_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cams.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(40))
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remote_ref: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Wenn nicht None: Eintrag wurde durch Retention-Policy geloescht.
    pruned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TimelapseJob(Base):
    """v0.10.0: Ein Render-Auftrag fuer ein Timelapse-Video."""
    __tablename__ = "timelapse_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cam_id: Mapped[int] = mapped_column(
        ForeignKey("cams.id", ondelete="CASCADE")
    )
    cam: Mapped["Cam"] = relationship()
    source_target_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("storage_targets.id", ondelete="SET NULL"), nullable=True,
    )
    # JSON-Params: {from, to, fps, resolution, weekdays, time_start, time_end,
    #               best_of_day, label, codec, frame_count_estimate}
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    # status: pending | running | done | error | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending")
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    frame_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[Optional[float]] = mapped_column(nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


_engine = create_engine(
    settings.db_url, echo=False, future=True,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(
    bind=_engine, autoflush=False, autocommit=False, future=True,
    expire_on_commit=False,
)


def _migrate_add_sort_order():
    with _engine.begin() as conn:
        for table in ("cams", "albums"):
            cols = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            col_names = {c[1] for c in cols}
            if "sort_order" not in col_names:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN sort_order INTEGER DEFAULT 0"))
                conn.execute(text(f"UPDATE {table} SET sort_order = id"))


def _migrate_add_dup_threshold():
    with _engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(cams)")).fetchall()
        col_names = {c[1] for c in cols}
        if "duplicate_hash_threshold" not in col_names:
            conn.execute(text("ALTER TABLE cams ADD COLUMN duplicate_hash_threshold INTEGER"))


def _migrate_add_retention():
    """v0.7.4: retention_per_cam in storage_targets + cam_id und pruned_at in target_uploads."""
    with _engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(storage_targets)")).fetchall()
        col_names = {c[1] for c in cols}
        if "retention_per_cam" not in col_names:
            conn.execute(text("ALTER TABLE storage_targets ADD COLUMN retention_per_cam INTEGER DEFAULT 0"))
        cols = conn.execute(text("PRAGMA table_info(target_uploads)")).fetchall()
        col_names = {c[1] for c in cols}
        if "cam_id" not in col_names:
            conn.execute(text("ALTER TABLE target_uploads ADD COLUMN cam_id INTEGER"))
            # Backfill: aus fetches.cam_id uebernehmen
            conn.execute(text(
                "UPDATE target_uploads SET cam_id = (SELECT cam_id FROM fetches WHERE fetches.id = target_uploads.fetch_id) "
                "WHERE cam_id IS NULL"
            ))
        if "pruned_at" not in col_names:
            conn.execute(text("ALTER TABLE target_uploads ADD COLUMN pruned_at DATETIME"))


def _migrate_seed_amazon_target():
    """Legt ein Amazon-Photos-Default-Target an und ordnet alle Cams diesem zu,
    sofern noch ueberhaupt kein Storage-Target existiert."""
    with _engine.begin() as conn:
        existing = conn.execute(text("SELECT COUNT(*) FROM storage_targets")).scalar()
        if existing and existing > 0:
            return
        conn.execute(text(
            "INSERT INTO storage_targets (name, type, config_json, path_template, "
            "enabled, sort_order, retention_per_cam, created_at, updated_at) "
            "VALUES (:n, :t, :c, :pt, 1, 0, 0, :now, :now)"
        ), {
            "n": "Amazon Photos",
            "t": "amazon",
            "c": "{}",
            "pt": "",
            "now": datetime.utcnow(),
        })
        target_id = conn.execute(text(
            "SELECT id FROM storage_targets WHERE name = :n"
        ), {"n": "Amazon Photos"}).scalar()
        if not target_id:
            return
        cam_rows = conn.execute(text("SELECT id FROM cams")).fetchall()
        for (cam_id,) in cam_rows:
            conn.execute(text(
                "INSERT OR IGNORE INTO cam_storage_targets (cam_id, storage_target_id) "
                "VALUES (:c, :t)"
            ), {"c": cam_id, "t": target_id})


def _migrate_add_cam_counters():
    """v0.8.8: persistente Lifetime-Counter pro Cam.
    total_uploads / total_duplicates / total_errors. Beim ersten Lauf nach Upgrade
    aus der fetches-Tabelle aufsummiert, damit bestehende Werte erhalten bleiben."""
    with _engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(cams)")).fetchall()
        col_names = {c[1] for c in cols}
        backfill_uploads = "total_uploads" not in col_names
        backfill_duplicates = "total_duplicates" not in col_names
        backfill_errors = "total_errors" not in col_names
        if backfill_uploads:
            conn.execute(text(
                "ALTER TABLE cams ADD COLUMN total_uploads INTEGER NOT NULL DEFAULT 0"
            ))
        if backfill_duplicates:
            conn.execute(text(
                "ALTER TABLE cams ADD COLUMN total_duplicates INTEGER NOT NULL DEFAULT 0"
            ))
        if backfill_errors:
            conn.execute(text(
                "ALTER TABLE cams ADD COLUMN total_errors INTEGER NOT NULL DEFAULT 0"
            ))
        # Backfill: aus fetches-Tabelle aufsummieren, damit Counter beim Upgrade
        # die bisherigen Zahlen widerspiegeln. Wird nur einmalig ausgefuehrt
        # (wenn die Spalten neu angelegt wurden).
        if backfill_uploads:
            conn.execute(text(
                "UPDATE cams SET total_uploads = ("
                "  SELECT COUNT(*) FROM fetches "
                "  WHERE fetches.cam_id = cams.id "
                "    AND fetches.status IN ('success', 'partial')"
                ")"
            ))
        if backfill_duplicates:
            conn.execute(text(
                "UPDATE cams SET total_duplicates = ("
                "  SELECT COUNT(*) FROM fetches "
                "  WHERE fetches.cam_id = cams.id "
                "    AND fetches.status = 'duplicate'"
                ")"
            ))
        if backfill_errors:
            conn.execute(text(
                "UPDATE cams SET total_errors = ("
                "  SELECT COUNT(*) FROM fetches "
                "  WHERE fetches.cam_id = cams.id "
                "    AND fetches.status IN ('fetch_error', 'upload_error')"
                ")"
            ))


def _migrate_seed_admin_user():
    """Legt einen Admin-User aus .env an, wenn die users-Tabelle leer ist."""
    with _engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if count and count > 0:
            return
        from .security import hash_password
        username = settings.auth_user
        password = settings.auth_password
        if not username or not password:
            return
        password_hash = hash_password(password)
        conn.execute(text(
            "INSERT INTO users (username, password_hash, role, created_at, updated_at) "
            "VALUES (:u, :h, 'admin', :now, :now)"
        ), {"u": username, "h": password_hash, "now": datetime.utcnow()})


def _migrate_add_timelapse():
    """v0.10.0: timelapse_source_target_id auf cams + timelapse_jobs-Tabelle."""
    with _engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(cams)")).fetchall()
        col_names = {c[1] for c in cols}
        if "timelapse_source_target_id" not in col_names:
            conn.execute(text(
                "ALTER TABLE cams ADD COLUMN timelapse_source_target_id INTEGER"
            ))
        # CREATE TABLE IF NOT EXISTS — idempotent, kein ALTER noetig
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS timelapse_jobs (\n"
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
            "  cam_id INTEGER NOT NULL REFERENCES cams(id) ON DELETE CASCADE,\n"
            "  source_target_id INTEGER REFERENCES storage_targets(id) ON DELETE SET NULL,\n"
            "  params_json TEXT NOT NULL DEFAULT '{}',\n"
            "  status VARCHAR(20) NOT NULL DEFAULT 'pending',\n"
            "  progress_pct INTEGER NOT NULL DEFAULT 0,\n"
            "  frame_count INTEGER,\n"
            "  output_path VARCHAR(500),\n"
            "  bytes INTEGER,\n"
            "  duration_s REAL,\n"
            "  error_message TEXT,\n"
            "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,\n"
            "  started_at DATETIME,\n"
            "  finished_at DATETIME\n"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_timelapse_jobs_cam_status "
            "ON timelapse_jobs (cam_id, status)"
        ))


def init_db() -> None:
    settings.ensure_dirs()
    Base.metadata.create_all(_engine)
    _migrate_add_sort_order()
    _migrate_add_dup_threshold()
    _migrate_add_retention()
    _migrate_add_cam_counters()
    _migrate_add_timelapse()
    _migrate_seed_amazon_target()
    _migrate_seed_admin_user()


def get_setting(session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.commit()
