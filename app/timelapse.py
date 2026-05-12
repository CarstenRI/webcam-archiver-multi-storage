"""Timelapse-Modul (v0.10.0).

Liefert die Datengrundlage und den Render-Pfad fuer das in `docs/timelapse-design.md`
beschriebene Feature: Cam-Bilder aus `target_uploads` listen, ueber den Browser
als Diashow ausspielen und/oder mit `ffmpeg` zu einem MP4 rendern.

Public API:
    list_frames(session, cam_id, source_target_id=None, *, from_dt, to_dt,
                weekdays=None, time_start=None, time_end=None,
                best_of_day=False) -> list[Frame]
    pick_source_target(session, cam) -> StorageTarget | None
    enqueue_job(session, cam, params) -> TimelapseJob
    worker_tick() -> bool        # processes at most one pending job
    cleanup_cache() -> dict      # called by the daily cleanup scheduler

Implementation notes:
- Frame-Listing ist eine reine SQL-Query (JOIN target_uploads -> fetches),
  gefiltert auf successful + pruned_at IS NULL + cam_id + storage_target_id.
- Filesystem-Walks finden NICHT statt; wir vertrauen der DB.
- Files, die laut DB existieren sollten aber auf der Disk fehlen (zwischen
  Listing und Rendering wegen Retention geloescht), werden beim Render
  uebersprungen und geloggt; Diashow gibt 404 zurueck, Player skippt.
- ffmpeg-Aufruf via subprocess.Popen mit Pipe fuer Progress (-progress pipe:1).
- Symlinks gegen Zero-Padded-Frame-Numbers, damit -i 'f_%06d.jpg' geht ohne
  Renaming der Originale.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional, Iterable

from sqlalchemy import select, and_, asc
from sqlalchemy.orm import Session

from .config import settings
from .db import (
    Cam, Fetch, SessionLocal, StorageTarget, TargetUpload, TimelapseJob,
    get_setting,
)
from .storage.base import slugify

log = logging.getLogger(__name__)


# --- Constants --------------------------------------------------------------

RESOLUTION_PRESETS: dict[str, Optional[int]] = {
    "original": None,
    "1080p": 1920,
    "720p": 1280,
}

DEFAULT_FPS = 25
MIN_FPS, MAX_FPS = 1, 60
MIN_FRAMES_FOR_RENDER = 2

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg"))


# --- Data classes -----------------------------------------------------------

@dataclass
class Frame:
    """Ein einzelnes Bild in der Timelapse-Sequenz."""
    upload_id: int
    cam_id: int
    path: str
    ts: datetime
    bytes: int = 0

    def exists(self) -> bool:
        try:
            return Path(self.path).is_file()
        except OSError:
            return False


# --- Frame listing ----------------------------------------------------------

def pick_source_target(session: Session, cam: Cam) -> Optional[StorageTarget]:
    """Bestimmt das Storage-Target, aus dem Timelapse-Frames gelesen werden.

    Priorisierung:
      1) cam.timelapse_source_target_id (vom User explizit gesetzt)
      2) Erstes aktives local-Target unter cam.storage_targets
      3) None — Cam hat kein local-Target

    Nur Targets vom Typ `local` sind in v0.10.0 verwendbar (siehe Design-Doc).
    """
    if cam.timelapse_source_target_id:
        t = session.get(StorageTarget, cam.timelapse_source_target_id)
        if t and t.enabled and t.type == "local":
            return t
        # Fallthrough: gesetztes Target ist disabled/geloescht/nicht-local
    for t in cam.storage_targets:
        if t.enabled and t.type == "local":
            return t
    return None


def list_frames(
    session: Session,
    cam_id: int,
    source_target_id: int,
    *,
    from_dt: datetime,
    to_dt: datetime,
    weekdays: Optional[str] = None,   # "1010111" o.ae. (Mo..So) oder None
    time_start: Optional[str] = None, # "HH:MM" oder None
    time_end: Optional[str] = None,
) -> list[Frame]:
    """Liefert die geordnete Frame-Liste fuer die gegebenen Filter.

    Reine DB-Query, keine Disk-Zugriffe. Caller entscheidet selbst, ob er
    danach noch `frame.exists()` checken will (Render macht das, Diashow
    ueberlaesst es dem 404).
    """
    stmt = (
        select(TargetUpload, Fetch)
        .join(Fetch, TargetUpload.fetch_id == Fetch.id)
        .where(
            and_(
                TargetUpload.cam_id == cam_id,
                TargetUpload.storage_target_id == source_target_id,
                TargetUpload.status == "success",
                TargetUpload.pruned_at.is_(None),
                TargetUpload.remote_ref.isnot(None),
                Fetch.started_at >= from_dt,
                Fetch.started_at <= to_dt,
            )
        )
        .order_by(asc(Fetch.started_at))
    )

    wd_filter: Optional[set[int]] = None
    if weekdays and len(weekdays) == 7 and weekdays != "1111111":
        # weekdays[0] = Montag (Python: Monday=0)
        wd_filter = {i for i, c in enumerate(weekdays) if c == "1"}

    ts_start: Optional[dtime] = _parse_hhmm(time_start) if time_start else None
    ts_end: Optional[dtime] = _parse_hhmm(time_end) if time_end else None

    out: list[Frame] = []
    for tu, f in session.execute(stmt).all():
        if wd_filter is not None and f.started_at.weekday() not in wd_filter:
            continue
        if ts_start or ts_end:
            t = f.started_at.time()
            if ts_start and ts_end and ts_start <= ts_end:
                if not (ts_start <= t <= ts_end):
                    continue
            elif ts_start and ts_end and ts_start > ts_end:
                # Ueber-Mitternacht-Fenster
                if not (t >= ts_start or t <= ts_end):
                    continue
            elif ts_start and t < ts_start:
                continue
            elif ts_end and t > ts_end:
                continue
        out.append(Frame(
            upload_id=tu.id, cam_id=cam_id, path=tu.remote_ref,
            ts=f.started_at, bytes=tu.bytes or 0,
        ))
    return out


def _parse_hhmm(s: str) -> Optional[dtime]:
    try:
        hh, mm = s.split(":")
        return dtime(int(hh), int(mm))
    except Exception:  # noqa: BLE001
        return None


# --- Best-of-day filter (Pillow) --------------------------------------------

def best_of_day_filter(frames: list[Frame]) -> list[Frame]:
    """Filtert Frames auf je ein 'bestes' Bild pro Kalendertag.

    'Best' = hoechste Mean-Brightness im 100x100-Downscale. Damit verschwinden
    Nachtaufnahmen und Nebelbilder, Tagsueber-Bilder bleiben. Pillow-only,
    kein OpenCV.

    Performance: ~1-3 ms pro Frame (PIL.Image.open + thumbnail + histogram).
    """
    try:
        from PIL import Image, ImageStat
    except ImportError:
        log.warning("Pillow nicht verfuegbar — best_of_day uebersprungen")
        return frames

    if not frames:
        return frames

    per_day: dict[str, tuple[float, Frame]] = {}
    for fr in frames:
        if not fr.exists():
            continue
        day_key = fr.ts.strftime("%Y-%m-%d")
        try:
            with Image.open(fr.path) as im:
                im.thumbnail((100, 100))
                im = im.convert("L")  # Greyscale fuer Brightness
                brightness = ImageStat.Stat(im).mean[0]
        except Exception as e:  # noqa: BLE001
            log.debug("brightness check failed for %s: %s", fr.path, e)
            continue
        prev = per_day.get(day_key)
        if prev is None or brightness > prev[0]:
            per_day[day_key] = (brightness, fr)
    out = [fr for _, fr in sorted(per_day.values(), key=lambda x: x[1].ts)]
    log.info("best_of_day: %d → %d frames", len(frames), len(out))
    return out


# --- Job lifecycle ----------------------------------------------------------

def enqueue_job(
    session: Session, cam: Cam, params: dict,
) -> TimelapseJob:
    """Schreibt einen pending Job in die DB. Wird vom Worker abgearbeitet."""
    job = TimelapseJob(
        cam_id=cam.id,
        source_target_id=params.get("source_target_id"),
        params_json=json.dumps(params, default=str),
        status="pending",
        progress_pct=0,
    )
    session.add(job)
    session.flush()
    session.commit()
    log.info("Job %d enqueued for cam %s", job.id, cam.name)
    return job


def worker_tick() -> bool:
    """Worker-Tick. Verarbeitet hoechstens einen pending Job pro Aufruf.

    Wird von Scheduler periodisch (alle WU_TIMELAPSE_WORKER_INTERVAL_S Sekunden)
    aufgerufen. Returns True wenn ein Job verarbeitet wurde, False wenn nichts
    zu tun war.
    """
    with SessionLocal() as s:
        job = (
            s.query(TimelapseJob)
            .filter(TimelapseJob.status == "pending")
            .order_by(TimelapseJob.created_at.asc())
            .first()
        )
        if not job:
            return False
        job.status = "running"
        job.started_at = datetime.utcnow()
        s.commit()
        job_id = job.id

    # Process outside the session — rendering can be slow and we don't want
    # to keep a transaction open.
    try:
        _process_job(job_id)
    except Exception as e:  # noqa: BLE001
        log.exception("Job %d crashed: %s", job_id, e)
        with SessionLocal() as s:
            j = s.get(TimelapseJob, job_id)
            if j and j.status == "running":
                j.status = "error"
                j.error_message = f"crashed: {e}"[:1000]
                j.finished_at = datetime.utcnow()
                s.commit()
    return True


def _process_job(job_id: int) -> None:
    """Eigentlicher Render-Pfad. Trennt sich strikt von der Job-Queue-Logik."""
    with SessionLocal() as s:
        job = s.get(TimelapseJob, job_id)
        if not job:
            return
        params = json.loads(job.params_json or "{}")
        cam = s.get(Cam, job.cam_id)
        if not cam:
            job.status = "error"
            job.error_message = "Cam nicht mehr vorhanden"
            job.finished_at = datetime.utcnow()
            s.commit()
            return

        source_target_id = job.source_target_id
        if not source_target_id:
            tgt = pick_source_target(s, cam)
            if not tgt:
                job.status = "error"
                job.error_message = (
                    "Kein local-Target verfuegbar fuer diese Cam"
                )
                job.finished_at = datetime.utcnow()
                s.commit()
                return
            source_target_id = tgt.id

        try:
            from_dt = _iso(params.get("from"))
            to_dt = _iso(params.get("to"))
        except Exception as e:  # noqa: BLE001
            job.status = "error"
            job.error_message = f"Ungueltiger Zeitraum: {e}"
            job.finished_at = datetime.utcnow()
            s.commit()
            return

        frames = list_frames(
            s, cam.id, source_target_id,
            from_dt=from_dt, to_dt=to_dt,
            weekdays=params.get("weekdays"),
            time_start=params.get("time_start"),
            time_end=params.get("time_end"),
        )
        if params.get("best_of_day"):
            frames = best_of_day_filter(frames)
        frames = [f for f in frames if f.exists()]

        job.frame_count = len(frames)
        s.commit()

        if len(frames) < MIN_FRAMES_FOR_RENDER:
            job.status = "error"
            job.error_message = (
                f"Zu wenige Frames im Zeitraum ({len(frames)} < {MIN_FRAMES_FOR_RENDER})"
            )
            job.finished_at = datetime.utcnow()
            s.commit()
            return

        # Output-Pfad
        cam_dir = settings.timelapse_dir / str(cam.id)
        cam_dir.mkdir(parents=True, exist_ok=True)
        from_lbl = from_dt.strftime("%Y%m%d-%H%M")
        to_lbl = to_dt.strftime("%Y%m%d-%H%M")
        slug = slugify(cam.name)
        out_path = cam_dir / f"{slug}_{from_lbl}_{to_lbl}_j{job_id}.mp4"

        if not FFMPEG_AVAILABLE:
            job.status = "error"
            job.error_message = (
                "ffmpeg nicht gefunden — bitte 'sudo bash install.sh' erneut "
                "ausfuehren (installiert ffmpeg ab v0.10.0)"
            )
            job.finished_at = datetime.utcnow()
            s.commit()
            return

        cam_label = cam.name
        cam_id_for_progress = cam.id

    # Render ausserhalb der Session (kann mehrere Minuten dauern)
    try:
        result = ffmpeg_render(
            frames=frames,
            fps=int(params.get("fps") or DEFAULT_FPS),
            resolution=params.get("resolution") or "1080p",
            label=f"{cam_label} · {from_lbl} – {to_lbl}",
            out_path=out_path,
            progress_cb=lambda pct: _set_progress(job_id, pct),
        )
    except Exception as e:  # noqa: BLE001
        log.exception("ffmpeg failed for job %d", job_id)
        with SessionLocal() as s:
            j = s.get(TimelapseJob, job_id)
            if j:
                j.status = "error"
                j.error_message = f"ffmpeg: {e}"[:1000]
                j.finished_at = datetime.utcnow()
                s.commit()
        return

    with SessionLocal() as s:
        j = s.get(TimelapseJob, job_id)
        if j:
            j.status = "done"
            j.output_path = str(out_path)
            j.bytes = result["bytes"]
            j.duration_s = result["duration_s"]
            j.progress_pct = 100
            j.finished_at = datetime.utcnow()
            s.commit()
            log.info(
                "Job %d done: %s (%.1f MB, %.1fs render)",
                job_id, out_path, result["bytes"] / 1024 / 1024,
                result["duration_s"],
            )

    # Retention pro Cam nach Erfolg anwenden
    _enforce_per_cam_retention(cam_id_for_progress)


def _set_progress(job_id: int, pct: int) -> None:
    """Setzt progress_pct ohne lange Lock — eigene Mini-Session."""
    with SessionLocal() as s:
        j = s.get(TimelapseJob, job_id)
        if j and j.status == "running":
            j.progress_pct = max(0, min(99, pct))
            s.commit()


def _iso(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        raise ValueError("leer")
    # akzeptiert "YYYY-MM-DDTHH:MM" oder "YYYY-MM-DDTHH:MM:SS"
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"unparsable: {value!r}")


# --- ffmpeg pipeline --------------------------------------------------------

def ffmpeg_render(
    frames: list[Frame],
    fps: int,
    resolution: str,
    label: str,
    out_path: Path,
    progress_cb=None,
) -> dict:
    """Rendert eine Frame-Liste zu einem MP4. Returns {bytes, duration_s}.

    Approach:
      1) Symlink-Verzeichnis mit zero-padded Sequenznummern (f_000001.jpg etc.)
      2) ffmpeg liest mit `-i 'f_%06d.jpg'`, scale+drawtext, libx264, AAC.
      3) Aufraeumen der tmp-Dir, MP4 verbleibt unter out_path.

    `progress_cb(pct)` wird waehrend des Renderns mit dem Fortschritt (0-99)
    aufgerufen, gespeist aus ffmpeg's `-progress pipe:1` Output.
    """
    fps = max(MIN_FPS, min(MAX_FPS, int(fps)))
    res_max_w = RESOLUTION_PRESETS.get(resolution)

    tmp_dir = settings.timelapse_dir / "tmp" / f"job_{int(time.time() * 1000)}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    try:
        # 1) Symlinks
        for i, fr in enumerate(frames, start=1):
            link = tmp_dir / f"f_{i:06d}.jpg"
            try:
                # Originalbild kann andere Endung haben — wir behalten .jpg
                # als sequenz, ffmpeg liest am stream-detect ohnehin
                if link.exists() or link.is_symlink():
                    link.unlink()
                os.symlink(fr.path, link)
            except OSError as e:
                log.warning("symlink fail %s: %s", fr.path, e)

        # 2) Filter-Chain bauen
        vf_parts = []
        if res_max_w:
            # Skaliert auf max. Breite, Hoehe wird proportional gerundet (even)
            vf_parts.append(
                f"scale='min({res_max_w},iw)':-2:flags=lanczos"
            )
        # drawtext: einfaches statisches Label unten rechts (v0.10.0).
        # Per-frame-Timestamps kommen in v0.10.1 (siehe Design-Doc).
        # Escape colons/backslashes fuer ffmpeg-filter-Syntax.
        safe_label = (
            label.replace("\\", "\\\\")
                 .replace(":", "\\:")
                 .replace("'", "\\'")
        )
        vf_parts.append(
            f"drawtext=text='{safe_label}':"
            "fontcolor=white:fontsize=18:"
            "x=w-tw-12:y=h-th-12:"
            "box=1:boxcolor=black@0.5:boxborderw=6"
        )
        vf = ",".join(vf_parts)

        cmd = [
            FFMPEG, "-y",
            "-framerate", str(fps),
            "-i", str(tmp_dir / "f_%06d.jpg"),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-progress", "pipe:1",
            "-loglevel", "warning",
            str(out_path),
        ]
        log.info("ffmpeg cmd: %s", " ".join(cmd))

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        total = len(frames)
        # 3) Progress parsen
        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("frame=") and progress_cb:
                    try:
                        n = int(line.split("=", 1)[1])
                        pct = int(n * 100 / total) if total else 0
                        progress_cb(min(99, pct))
                    except (ValueError, IndexError):
                        pass
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"ffmpeg exit {proc.returncode}: {err[:500]}")

        if not out_path.exists():
            raise RuntimeError("ffmpeg success aber Output fehlt")
        return {
            "bytes": out_path.stat().st_size,
            "duration_s": time.time() - t_start,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --- Cache cleanup ----------------------------------------------------------

def cleanup_cache() -> dict:
    """Wendet Retention auf den Timelapse-Cache an.

    Wird vom taeglichen Cleanup-Scheduler-Job aufgerufen. Zwei Regeln:
      1) Pro Cam: nur die `timelapse_retention_per_cam` neuesten Jobs behalten.
      2) Global: wenn Summe > `timelapse_cache_max_gb` GB, aelteste Renderings
         loeschen bis unter dem Cap.
    """
    summary = {"removed": 0, "freed_bytes": 0, "checked_jobs": 0}
    cap_bytes = _get_cache_cap_bytes()
    per_cam = _get_per_cam_retention()

    with SessionLocal() as s:
        # 1) Pro Cam
        cam_ids = [
            r[0] for r in s.query(TimelapseJob.cam_id).distinct().all()
            if r[0] is not None
        ]
        for cid in cam_ids:
            jobs = (
                s.query(TimelapseJob)
                .filter(
                    TimelapseJob.cam_id == cid,
                    TimelapseJob.status == "done",
                    TimelapseJob.output_path.isnot(None),
                )
                .order_by(TimelapseJob.finished_at.desc())
                .all()
            )
            summary["checked_jobs"] += len(jobs)
            for j in jobs[per_cam:]:
                summary["freed_bytes"] += _remove_job_file(j)
                summary["removed"] += 1
        s.commit()

        # 2) Global Cap
        if cap_bytes > 0:
            total = _total_bytes(s)
            if total > cap_bytes:
                # Aelteste zuerst
                victims = (
                    s.query(TimelapseJob)
                    .filter(
                        TimelapseJob.status == "done",
                        TimelapseJob.output_path.isnot(None),
                    )
                    .order_by(TimelapseJob.finished_at.asc())
                    .all()
                )
                for j in victims:
                    if _total_bytes(s) <= cap_bytes:
                        break
                    summary["freed_bytes"] += _remove_job_file(j)
                    summary["removed"] += 1
            s.commit()
    log.info("timelapse cleanup: %s", summary)
    return summary


def _get_cache_cap_bytes() -> int:
    """Effektiver Cap: DB-Setting > env-Setting > default."""
    try:
        with SessionLocal() as s:
            raw = get_setting(s, "timelapse_cache_max_gb", "")
        if raw:
            gb = int(raw)
        else:
            gb = settings.timelapse_cache_max_gb
    except (ValueError, Exception):
        gb = settings.timelapse_cache_max_gb
    return max(0, gb) * 1024 * 1024 * 1024


def _get_per_cam_retention() -> int:
    try:
        with SessionLocal() as s:
            raw = get_setting(s, "timelapse_retention_per_cam", "")
        if raw:
            return max(1, int(raw))
    except (ValueError, Exception):
        pass
    return max(1, settings.timelapse_retention_per_cam)


def _total_bytes(s: Session) -> int:
    total = 0
    for (b,) in s.query(TimelapseJob.bytes).filter(
        TimelapseJob.status == "done",
        TimelapseJob.bytes.isnot(None),
        TimelapseJob.output_path.isnot(None),
    ).all():
        total += b or 0
    return total


def _remove_job_file(job: TimelapseJob) -> int:
    """Loescht die MP4-Datei und neutralisiert die DB-Row.

    Job-Eintrag bleibt erhalten (mit output_path=NULL, evicted-Hinweis im
    error_message-Feld), damit die UI weiss 'das Video gab's mal'.
    """
    freed = 0
    if job.output_path:
        try:
            p = Path(job.output_path)
            if p.is_file():
                freed = p.stat().st_size
                p.unlink()
        except OSError as e:
            log.warning("cleanup unlink fail %s: %s", job.output_path, e)
    job.output_path = None
    job.bytes = None
    if not job.error_message:
        job.error_message = "evicted by retention"
    return freed


# --- Enforcement nach Render (Phase Post-Job) -------------------------------

def _enforce_per_cam_retention(cam_id: int) -> None:
    """Sofort-Cleanup nach einem erfolgreichen Render — kein Warten auf den
    Daily-Job. Nur Pro-Cam-Limit, kein Global-Cap (das macht der Daily)."""
    per_cam = _get_per_cam_retention()
    with SessionLocal() as s:
        jobs = (
            s.query(TimelapseJob)
            .filter(
                TimelapseJob.cam_id == cam_id,
                TimelapseJob.status == "done",
                TimelapseJob.output_path.isnot(None),
            )
            .order_by(TimelapseJob.finished_at.desc())
            .all()
        )
        for j in jobs[per_cam:]:
            _remove_job_file(j)
        s.commit()
