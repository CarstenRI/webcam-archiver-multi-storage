"""APScheduler: pro Cam ein Job, der prueft, ob jetzt gefetcht werden soll."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import joinedload

from .config import settings
from .db import Cam, Fetch, SessionLocal, StorageTarget, TargetUpload
from .fetcher import (
    FetchError, compute_phash, fetch_image, hashes_similar,
    save_preview, save_temp_image,
)
from .solar import in_clock_window, in_solar_window, in_weekdays
from .storage import get_backend
from .storage.base import UploadResult

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def cleanup_old_fetches() -> int:
    """Loescht alle Fetch-Eintraege aelter als fetch_retention_days. 0 = aus."""
    from .db import get_setting
    with SessionLocal() as s:
        raw = get_setting(s, "fetch_retention_days", "0")
        try:
            days = int(raw or "0")
        except ValueError:
            days = 0
        if days <= 0:
            return 0
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = (
            s.query(Fetch)
            .filter(Fetch.started_at < cutoff)
            .delete(synchronize_session=False)
        )
        s.commit()
        if deleted:
            log.info("Log-Retention: %d alte Fetch-Eintraege geloescht (>= %d Tage)", deleted, days)
        return deleted


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 60,
            },
        )
    return _scheduler


def start() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
    sync_jobs()
    sched.add_job(
        sync_jobs,
        trigger=IntervalTrigger(minutes=30),
        id="__self_heal_sync_jobs__",
        replace_existing=True,
    )
    # Log-Retention + Timelapse-Cache-Cleanup: einmal taeglich
    sched.add_job(
        _daily_cleanup,
        trigger=IntervalTrigger(hours=24),
        id="__daily_cleanup__",
        replace_existing=True,
    )
    # Timelapse-Worker: pollt pending Jobs alle paar Sekunden
    sched.add_job(
        _timelapse_worker_tick,
        trigger=IntervalTrigger(
            seconds=max(1, int(settings.timelapse_worker_interval_s)),
        ),
        id="__timelapse_worker__",
        replace_existing=True,
    )


def _daily_cleanup() -> None:
    """Sammelt alle taeglichen Cleanup-Schritte (v0.10.0+)."""
    try:
        cleanup_old_fetches()
    except Exception as e:  # noqa: BLE001
        log.exception("cleanup_old_fetches failed: %s", e)
    try:
        from . import timelapse as _tl
        _tl.cleanup_cache()
    except Exception as e:  # noqa: BLE001
        log.exception("timelapse.cleanup_cache failed: %s", e)
    # v0.11.0: Thumbnail-Cache aufraeumen (Orphans + LRU-Eviction).
    try:
        _thumbnail_cleanup()
    except Exception as e:  # noqa: BLE001
        log.exception("thumbnail cleanup failed: %s", e)


def _thumbnail_cleanup() -> None:
    """Orphans loeschen (Thumbs ohne lebende TargetUpload-Row) und auf Cap evicten."""
    from . import thumbnails as _tn
    # 1) Valid keys aus DB: alle nicht-pruned successful Uploads auf local-Targets.
    with SessionLocal() as s:
        rows = (
            s.query(TargetUpload.cam_id, TargetUpload.id)
            .join(StorageTarget, TargetUpload.storage_target_id == StorageTarget.id)
            .filter(
                StorageTarget.type == "local",
                TargetUpload.cam_id.isnot(None),
                TargetUpload.pruned_at.is_(None),
                TargetUpload.status == "success",
            )
            .all()
        )
    valid = {(cid, uid) for cid, uid in rows if cid is not None}
    orphans = _tn.prune_orphans(valid)
    # 2) LRU-Eviction wenn ueber Cap.
    cap_mb = max(0, int(settings.thumbnail_cache_max_mb))
    evict = _tn.evict_to_cap(cap_mb * 1024 * 1024) if cap_mb > 0 else {
        "removed": 0, "freed_bytes": 0, "total_before": 0, "total_after": 0,
    }
    log.info(
        "thumbnail cleanup: orphans=%d evicted=%d freed=%d KB cache=%d KB",
        orphans, evict.get("removed", 0),
        evict.get("freed_bytes", 0) // 1024,
        evict.get("total_after", 0) // 1024,
    )


def _timelapse_worker_tick() -> None:
    """Worker-Tick-Wrapper: ruft timelapse.worker_tick() mit Schutz auf."""
    try:
        from . import timelapse as _tl
        _tl.worker_tick()
    except Exception as e:  # noqa: BLE001
        log.exception("timelapse.worker_tick failed: %s", e)


def shutdown() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def _job_id(cam_id: int) -> str:
    return f"cam-{cam_id}"


def sync_jobs() -> None:
    sched = get_scheduler()
    with SessionLocal() as s:
        cams = s.query(Cam).all()
        wanted: dict[str, Cam] = {_job_id(c.id): c for c in cams if c.enabled}

    with _lock:
        for j in list(sched.get_jobs()):
            if not j.id.startswith("cam-"):
                continue
            if j.id not in wanted:
                sched.remove_job(j.id)

        for jid, cam in wanted.items():
            existing = sched.get_job(jid)
            interval = max(1, cam.interval_minutes)
            trigger = IntervalTrigger(minutes=interval)
            if existing is None:
                sched.add_job(
                    run_cam, trigger=trigger, args=[cam.id],
                    id=jid, replace_existing=True,
                )
            else:
                existing.reschedule(trigger=trigger)


def trigger_now(cam_id: int) -> None:
    sched = get_scheduler()
    sched.add_job(run_cam, args=[cam_id], id=f"now-{cam_id}-{datetime.utcnow().timestamp()}")


def _is_window_open(cam: Cam) -> tuple[bool, str]:
    tz = ZoneInfo(cam.timezone or "Europe/Berlin")
    now_local = datetime.now(tz)

    if not in_weekdays(cam.weekdays or "1111111", now_local):
        return False, "weekday"

    if cam.use_solar and cam.latitude is not None and cam.longitude is not None:
        if not in_solar_window(
            cam.latitude, cam.longitude, cam.timezone or "Europe/Berlin",
            now_local, cam.solar_offset_min or 0,
        ):
            return False, "solar"
        return True, ""

    if not in_clock_window(cam.time_start or "", cam.time_end or "", now_local):
        return False, "clock"
    return True, ""


def _resolve_targets(s, cam: Cam) -> list[StorageTarget]:
    """Liefert die aktiven Storage-Targets einer Cam.

    Fallback: Wenn die Cam keine Targets zugewiesen hat, aber ein
    aktives 'amazon'-Target existiert, nutze dieses (Sicherheits-Net,
    damit nach der 0.7.0-Migration keine Cam ins Leere laeuft).
    """
    targets = [t for t in cam.storage_targets if t.enabled]
    if targets:
        return targets
    amazon = (
        s.query(StorageTarget)
        .filter(StorageTarget.type == "amazon", StorageTarget.enabled.is_(True))
        .order_by(StorageTarget.id.asc())
        .first()
    )
    if amazon is not None:
        return [amazon]
    return []


def _bump_cam_counter(cam: Cam, status: str) -> None:
    """Persistente Lifetime-Counter pro Cam erhoehen. Bleiben auch dann erhalten,
    wenn der User die Logs auf der UI loescht."""
    if status in ("success", "partial"):
        cam.total_uploads = (cam.total_uploads or 0) + 1
    elif status == "duplicate":
        cam.total_duplicates = (cam.total_duplicates or 0) + 1
    elif status in ("fetch_error", "upload_error"):
        cam.total_errors = (cam.total_errors or 0) + 1


def _aggregate_status(results: list[UploadResult]) -> tuple[str, str]:
    """Aggregiert mehrere UploadResults zu einem Fetch-Status + Meldung."""
    if not results:
        return "upload_error", "Keine Speicherziele konfiguriert"
    n_ok = sum(1 for r in results if r.status == "success")
    n_err = sum(1 for r in results if r.status == "error")
    n_skip = sum(1 for r in results if r.status == "skipped")
    total = len(results)
    if n_ok == total:
        return "success", ""
    if n_ok == 0 and n_skip == total:
        return "success", "alle Ziele uebersprungen"
    if n_ok == 0:
        first_err = next((r.message for r in results if r.status == "error"), "")
        return "upload_error", first_err[:300]
    bad_msgs = [r.message for r in results if r.status == "error" and r.message]
    return "partial", f"{n_ok}/{total} ok" + (f": {bad_msgs[0][:200]}" if bad_msgs else "")



def _prune_target_for_cam(s, target: StorageTarget, cam_id: int) -> int:
    """Wenn target.retention_per_cam > 0, loescht die aeltesten ueberzaehligen
    erfolgreichen Uploads fuer diese (target, cam) Kombination. Returns Anzahl
    geloeschter Eintraege."""
    limit = int(target.retention_per_cam or 0)
    if limit <= 0:
        return 0
    rows = (
        s.query(TargetUpload)
        .filter(
            TargetUpload.storage_target_id == target.id,
            TargetUpload.cam_id == cam_id,
            TargetUpload.status == "success",
            TargetUpload.pruned_at.is_(None),
            TargetUpload.remote_ref.isnot(None),
        )
        .order_by(TargetUpload.id.desc())
        .all()
    )
    if len(rows) <= limit:
        return 0
    overflow = rows[limit:]
    try:
        backend = get_backend(target)
    except Exception as e:
        log.warning("Prune: backend init fehlgeschlagen fuer %s: %s", target.name, e)
        return 0
    pruned = 0
    for tu in overflow:
        try:
            ok, msg = backend.delete(tu.remote_ref or "")
        except Exception as e:
            log.warning("Prune-Exception %s: %s", tu.remote_ref, e)
            ok, msg = False, f"exception: {e}"
        tu.pruned_at = datetime.utcnow()
        if not ok:
            # trotzdem als pruned markieren, damit wir es nicht ewig retryen
            tu.message = ((tu.message or "") + f" | prune: {msg}")[:500]
        # Thumbnail mitloeschen (v0.11.0). Nur lokal — fuer remote-Targets gibt's keinen.
        if target.type == "local" and tu.cam_id and tu.id:
            try:
                from . import thumbnails as _tn
                _tn.delete(tu.cam_id, tu.id)
            except Exception as e:
                log.warning("Thumbnail-Delete im Prune fail %s/%s: %s",
                            tu.cam_id, tu.id, e)
        s.commit()
        pruned += 1
    if pruned:
        log.info("Cam %s @ %s: %d alte Uploads gepruned (limit=%d)", cam_id, target.name, pruned, limit)
    return pruned


def run_cam(cam_id: int) -> None:
    with SessionLocal() as s:
        cam = (
            s.query(Cam)
            .options(joinedload(Cam.album), joinedload(Cam.storage_targets))
            .filter(Cam.id == cam_id)
            .first()
        )
        if cam is None or not cam.enabled:
            return

        ok, reason = _is_window_open(cam)
        if not ok:
            log.debug("Cam %s skipped (%s)", cam.name, reason)
            return

        fetch = Fetch(cam_id=cam.id, status="pending", started_at=datetime.utcnow())
        s.add(fetch)
        s.commit()
        s.refresh(fetch)

        tmp_path: Path | None = None
        try:
            data = fetch_image(cam.url, cam.headers_json)
            phash = compute_phash(data)
            fetch.bytes = len(data)
            fetch.phash = phash

            try:
                preview_path = save_preview(cam.id, data)
                cam.last_preview_path = str(preview_path)
            except Exception as e:
                log.debug("Preview-Save fehlgeschlagen: %s", e)

            dup_threshold = (
                cam.duplicate_hash_threshold
                if cam.duplicate_hash_threshold is not None
                else settings.duplicate_hash_threshold
            )
            if cam.skip_duplicates and cam.last_phash and hashes_similar(
                phash, cam.last_phash, dup_threshold
            ):
                fetch.status = "duplicate"
                fetch.message = "Bild praktisch identisch zum letzten."
                cam.last_status = "duplicate"
                cam.last_fetch_at = datetime.utcnow()
                _bump_cam_counter(cam, "duplicate")
                s.commit()
                return

            tmp_path = save_temp_image(cam.id, data)
            taken_at = datetime.now()

            album_name = cam.album.name if cam.album else None
            album_db_id = cam.album.id if cam.album else None

            targets = _resolve_targets(s, cam)
            results: list[UploadResult] = []

            for target in targets:
                tu = TargetUpload(
                    fetch_id=fetch.id,
                    storage_target_id=target.id,
                    cam_id=cam.id,
                    target_name=target.name,
                    target_type=target.type,
                    status="pending",
                    started_at=datetime.utcnow(),
                )
                s.add(tu)
                s.commit()
                s.refresh(tu)
                try:
                    backend = get_backend(target)
                    res = backend.upload(
                        path=tmp_path,
                        image_bytes=data,
                        cam_id=cam.id,
                        cam_name=cam.name,
                        album_name=album_name,
                        album_db_id=album_db_id,
                        taken_at=taken_at,
                    )
                except Exception as e:
                    log.exception("Backend %s (%s) Upload-Exception", target.name, target.type)
                    res = UploadResult(status="error", message=f"backend-exception: {e}"[:500])

                tu.status = res.status
                tu.message = (res.message or "")[:500]
                tu.remote_ref = (res.remote_ref or "")[:500] if res.remote_ref else None
                tu.bytes = res.bytes
                tu.finished_at = datetime.utcnow()
                target.last_status = res.status
                target.last_status_msg = (res.message or "")[:500]
                target.last_status_at = datetime.utcnow()
                s.commit()
                # Thumbnail-Cache fuer local-Targets erzeugen (v0.11.0).
                # Fehler nur loggen — Upload bleibt erfolgreich.
                if res.status == "success" and target.type == "local" and tu.remote_ref:
                    try:
                        from . import thumbnails as _tn
                        _tn.ensure(tu.remote_ref, cam.id, tu.id)
                    except Exception as e:
                        log.warning("Thumbnail-ensure fehlgeschlagen %s/%s: %s",
                                    cam.name, tu.id, e)
                # Retention-Policy anwenden, wenn Upload erfolgreich war
                if res.status == "success":
                    try:
                        _prune_target_for_cam(s, target, cam.id)
                    except Exception as e:
                        log.warning("Prune-Aufruf fehlgeschlagen: %s", e)
                results.append(res)
                log.info(
                    "Cam %s -> %s [%s]: %s %s",
                    cam.name, target.name, target.type, res.status,
                    res.message or "",
                )

            status, msg = _aggregate_status(results)
            fetch.status = status
            fetch.message = msg or None
            cam.last_status = status
            if status in ("success", "partial"):
                cam.last_phash = phash
            cam.last_fetch_at = datetime.utcnow()
            _bump_cam_counter(cam, status)
            s.commit()

        except FetchError as e:
            fetch.status = "fetch_error"
            fetch.message = str(e)[:500]
            cam.last_status = "fetch_error"
            cam.last_fetch_at = datetime.utcnow()
            _bump_cam_counter(cam, "fetch_error")
            s.commit()
        except Exception as e:
            log.exception("Unbehandelter Fehler in run_cam(%s)", cam_id)
            fetch.status = "fetch_error"
            fetch.message = f"Unerwartet: {e}"[:500]
            cam.last_status = "fetch_error"
            cam.last_fetch_at = datetime.utcnow()
            _bump_cam_counter(cam, "fetch_error")
            s.commit()
        finally:
            fetch.finished_at = datetime.utcnow()
            try:
                s.commit()
            except Exception:
                pass
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
