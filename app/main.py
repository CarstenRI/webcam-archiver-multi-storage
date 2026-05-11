"""FastAPI-App: Web-UI + JSON-API fuer Webcam-Uploader."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func as sa_func
from sqlalchemy.orm import joinedload

from . import __version__, scheduler
from .auth import require_auth, require_admin
from .config import settings
from .db import (
    Album, Cam, Fetch, SessionLocal, StorageTarget, TargetUpload, User,
    cam_storage_targets, get_setting, init_db, set_setting,
)
from .fetcher import FetchError, fetch_image, save_preview
from .solar import WEEKDAY_LABELS
from .storage import backend_meta, get_backend, list_backend_types
from .template_filters import humanize_relative, localfmt
from .uploader import health_check as uploader_health, reset_client

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("webcam-uploader")
APP_STARTUP_AT = datetime.utcnow()

TYPE_LABELS = {
    "amazon": "Amazon Photos",
    "local": "Lokal",
    "sftp": "SFTP",
    "s3": "S3-kompatibel",
}


def _collect_storage_status(s) -> list[dict]:
    """Sammelt Health-Status fuer alle aktiven Storage-Targets fuers Dashboard.
    Nur schnelle Checks (kein Netzwerk-Call)."""
    targets = (
        s.query(StorageTarget)
        .filter(StorageTarget.enabled.is_(True))
        .order_by(StorageTarget.sort_order, StorageTarget.name)
        .all()
    )
    out = []
    for t in targets:
        ok, msg = False, "Backend-Fehler"
        try:
            backend = get_backend(t)
            ok, msg = backend.health_check()
        except Exception as e:
            ok, msg = False, f"Fehler: {e}"
        out.append({
            "id": t.id,
            "name": t.name,
            "type": t.type,
            "type_label": TYPE_LABELS.get(t.type, t.type),
            "ok": ok,
            "msg": msg,
        })
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    log.info(
        "Webcam-Uploader %s gestartet auf %s:%s",
        __version__, settings.host, settings.port,
    )
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="Webcam-Uploader", version=__version__, lifespan=lifespan)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["WEEKDAY_LABELS"] = WEEKDAY_LABELS
templates.env.globals["app_version"] = __version__
templates.env.filters["rel"] = humanize_relative
templates.env.filters["localfmt"] = localfmt

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def _parse_weekdays(form_data: dict) -> str:
    bits = []
    for i in range(7):
        bits.append("1" if form_data.get(f"weekday_{i}") in ("on", "1", "true") else "0")
    return "".join(bits)


def _cam_summary(cam, album_name):
    return {
        "id": cam.id,
        "name": cam.name,
        "url": cam.url,
        "album": album_name,
        "enabled": cam.enabled,
        "interval_minutes": cam.interval_minutes,
        "use_solar": cam.use_solar,
        "time_start": cam.time_start,
        "time_end": cam.time_end,
        "weekdays": cam.weekdays,
        "last_status": cam.last_status,
        "last_fetch_at": cam.last_fetch_at.isoformat() if cam.last_fetch_at else None,
        "storage_targets": [
            {"id": t.id, "name": t.name, "type": t.type}
            for t in cam.storage_targets
        ],
    }


@app.get("/")
def root(_: str = Depends(require_auth)):
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
def dashboard(request: Request, _: str = Depends(require_auth)):
    with SessionLocal() as s:
        cams = (
            s.query(Cam)
            .options(joinedload(Cam.album), joinedload(Cam.storage_targets))
            .order_by(Cam.sort_order, Cam.name)
            .all()
        )
        recent = (
            s.query(Fetch)
            .options(joinedload(Fetch.cam))
            .order_by(Fetch.started_at.desc())
            .limit(20)
            .all()
        )
        # Stats aus persistenten Cam-Countern (bleiben nach Logs-Loeschen erhalten).
        ok = sum((c.total_uploads or 0) for c in cams)
        dup = sum((c.total_duplicates or 0) for c in cams)
        err = sum((c.total_errors or 0) for c in cams)
        # Box 4: vergaenglicher 24h-Aktivitaetsindikator aus den fetches-Logs.
        # Wenn der User die Logs leert, geht dieser Wert auf 0 zurueck — gewollt.
        since = datetime.utcnow() - timedelta(hours=24)
        activity_24h = s.query(Fetch).filter(Fetch.started_at >= since).count()
        # Per-Cam-Counter ebenfalls aus persistentem Feld.
        cam_upload_counts = {c.id: (c.total_uploads or 0) for c in cams}
        amazon_ok, amazon_msg = uploader_health()
        storage_status = _collect_storage_status(s)
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "cams": cams,
                "recent": recent,
                "stats": {
                    "ok": ok,
                    "duplicate": dup,
                    "error": err,
                    "activity_24h": activity_24h,
                },
                "cam_upload_counts": cam_upload_counts,
                "amazon_ok": amazon_ok,
                "amazon_msg": amazon_msg,
                "storage_status": storage_status,
            },
        )


@app.get("/preview/cam/{cam_id}")
def preview(cam_id: int, _: str = Depends(require_auth)):
    p = settings.data_dir / "previews" / f"cam{cam_id}.jpg"
    if not p.exists():
        raise HTTPException(404, "Keine Vorschau")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/preview/cam/{cam_id}/full")
def preview_full(cam_id: int, _: str = Depends(require_auth)):
    """Grosse Vorschau (max. 1920px) fuer die Lightbox.
    Fallback auf die kleine Vorschau, falls Full noch nicht existiert
    (z.B. fuer Cams, die vor v0.8.6 zuletzt gefetcht wurden)."""
    base = settings.data_dir / "previews"
    full = base / f"cam{cam_id}_full.jpg"
    if full.exists():
        return FileResponse(full, media_type="image/jpeg")
    thumb = base / f"cam{cam_id}.jpg"
    if thumb.exists():
        return FileResponse(thumb, media_type="image/jpeg")
    raise HTTPException(404, "Keine Vorschau")


@app.get("/cams")
def cams_list(request: Request, _: str = Depends(require_auth)):
    with SessionLocal() as s:
        cams = (
            s.query(Cam)
            .options(joinedload(Cam.album), joinedload(Cam.storage_targets))
            .order_by(Cam.sort_order, Cam.name)
            .all()
        )
        albums = s.query(Album).order_by(Album.name).all()
        return templates.TemplateResponse(
            "cams.html",
            {"request": request, "cams": cams, "albums": albums},
        )


@app.get("/cams/new")
def cam_new_form(request: Request, _: str = Depends(require_auth)):
    with SessionLocal() as s:
        albums = s.query(Album).order_by(Album.name).all()
        targets = s.query(StorageTarget).order_by(StorageTarget.sort_order, StorageTarget.name).all()
        return templates.TemplateResponse(
            "cam_form.html",
            {
                "request": request, "cam": None, "albums": albums,
                "storage_targets": targets,
                "selected_target_ids": {t.id for t in targets if t.enabled and t.type == "amazon"},
            },
        )


@app.get("/cams/{cam_id}/edit")
def cam_edit_form(cam_id: int, request: Request, _: str = Depends(require_auth)):
    with SessionLocal() as s:
        cam = (
            s.query(Cam)
            .options(joinedload(Cam.album), joinedload(Cam.storage_targets))
            .filter(Cam.id == cam_id)
            .first()
        )
        if cam is None:
            raise HTTPException(404)
        albums = s.query(Album).order_by(Album.name).all()
        targets = s.query(StorageTarget).order_by(StorageTarget.sort_order, StorageTarget.name).all()
        selected = {t.id for t in cam.storage_targets}
        return templates.TemplateResponse(
            "cam_form.html",
            {
                "request": request, "cam": cam, "albums": albums,
                "storage_targets": targets,
                "selected_target_ids": selected,
            },
        )


@app.post("/cams/save")
async def cam_save(request: Request, user: User = Depends(require_admin)):
    form = await request.form()
    data = dict(form)
    target_ids = [int(v) for v in form.getlist("storage_target_ids") if v]
    cam_id_raw = data.get("id", "").strip()
    weekdays = _parse_weekdays(data)
    with SessionLocal() as s:
        if cam_id_raw:
            cam = s.get(Cam, int(cam_id_raw))
            if cam is None:
                raise HTTPException(404)
        else:
            cam = Cam(name=data.get("name", "").strip(), url=data.get("url", "").strip())
            s.add(cam)
            s.flush()
        cam.name = data.get("name", "").strip() or "Unbenannt"
        cam.url = data.get("url", "").strip()
        headers_raw = data.get("headers_json", "").strip()
        cam.headers_json = headers_raw if headers_raw else None
        cam.enabled = data.get("enabled") == "on"
        cam.interval_minutes = max(1, int(data.get("interval_minutes", "15") or 15))
        cam.time_start = data.get("time_start", "").strip()
        cam.time_end = data.get("time_end", "").strip()
        cam.weekdays = weekdays
        cam.use_solar = data.get("use_solar") == "on"
        cam.latitude = float(data["latitude"]) if data.get("latitude") else None
        cam.longitude = float(data["longitude"]) if data.get("longitude") else None
        cam.timezone = data.get("timezone", "Europe/Berlin").strip() or "Europe/Berlin"
        cam.solar_offset_min = int(data.get("solar_offset_min") or 0)
        cam.skip_duplicates = data.get("skip_duplicates") == "on"
        dht_raw = data.get("duplicate_hash_threshold", "").strip()
        cam.duplicate_hash_threshold = int(dht_raw) if dht_raw else None
        album_id_raw = data.get("album_id", "").strip()
        cam.album_id = int(album_id_raw) if album_id_raw else None

        if target_ids:
            targets = (
                s.query(StorageTarget).filter(StorageTarget.id.in_(target_ids)).all()
            )
            cam.storage_targets = targets
        else:
            cam.storage_targets = []
        s.commit()
    scheduler.sync_jobs()
    return RedirectResponse(url="/cams", status_code=303)


@app.post("/cams/{cam_id}/delete")
def cam_delete(cam_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        cam = s.get(Cam, cam_id)
        if cam:
            s.delete(cam)
            s.commit()
    scheduler.sync_jobs()
    return RedirectResponse(url="/cams", status_code=303)


@app.post("/cams/{cam_id}/toggle")
def cam_toggle(cam_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        cam = s.get(Cam, cam_id)
        if cam:
            cam.enabled = not cam.enabled
            s.commit()
    scheduler.sync_jobs()
    return RedirectResponse(url="/cams", status_code=303)


def _normalize_cam_sort(s) -> list:
    """Numeriert alle Cams in aktueller Display-Reihenfolge (sort_order, name) auf 1..N um.
    Liefert die sortierte Cam-Liste zurueck."""
    cams = s.query(Cam).order_by(Cam.sort_order, Cam.name).all()
    for i, c in enumerate(cams, start=1):
        c.sort_order = i
    s.commit()
    return cams


@app.post("/cams/{cam_id}/move_up")
def cam_move_up(cam_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        cam = s.get(Cam, cam_id)
        if cam is None:
            raise HTTPException(404)
        ordered = _normalize_cam_sort(s)
        # nach Normalisierung neu holen, weil cam.sort_order frisch ist
        try:
            idx = next(i for i, c in enumerate(ordered) if c.id == cam_id)
        except StopIteration:
            return RedirectResponse(url="/cams", status_code=303)
        if idx > 0:
            prev = ordered[idx - 1]
            cam = ordered[idx]
            cam.sort_order, prev.sort_order = prev.sort_order, cam.sort_order
            s.commit()
    return RedirectResponse(url="/cams", status_code=303)


@app.post("/cams/{cam_id}/move_down")
def cam_move_down(cam_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        cam = s.get(Cam, cam_id)
        if cam is None:
            raise HTTPException(404)
        ordered = _normalize_cam_sort(s)
        try:
            idx = next(i for i, c in enumerate(ordered) if c.id == cam_id)
        except StopIteration:
            return RedirectResponse(url="/cams", status_code=303)
        if idx < len(ordered) - 1:
            nxt = ordered[idx + 1]
            cam = ordered[idx]
            cam.sort_order, nxt.sort_order = nxt.sort_order, cam.sort_order
            s.commit()
    return RedirectResponse(url="/cams", status_code=303)


@app.post("/cams/{cam_id}/run")
def cam_run_now(cam_id: int, user: User = Depends(require_admin)):
    scheduler.trigger_now(cam_id)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/cams/{cam_id}/test")
def cam_test(cam_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        cam = s.get(Cam, cam_id)
        if cam is None:
            raise HTTPException(404)
        try:
            data = fetch_image(cam.url, cam.headers_json)
            save_preview(cam.id, data)
            return {"ok": True, "bytes": len(data)}
        except FetchError as e:
            return {"ok": False, "error": str(e)}


@app.get("/albums")
def albums_list(request: Request, _: str = Depends(require_auth)):
    with SessionLocal() as s:
        rows = s.query(Album).options(joinedload(Album.cams)).order_by(Album.sort_order, Album.name).all()
        seen = {}
        for a in rows:
            seen[a.id] = a
        albums = sorted(seen.values(), key=lambda x: (x.sort_order, x.name))
        return templates.TemplateResponse(
            "albums.html", {"request": request, "albums": albums}
        )


def _normalize_album_sort(s) -> list:
    albums = s.query(Album).order_by(Album.sort_order, Album.name).all()
    for i, a in enumerate(albums, start=1):
        a.sort_order = i
    s.commit()
    return albums


@app.post("/albums/{album_id}/move_up")
def album_move_up(album_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        a = s.get(Album, album_id)
        if a is None:
            raise HTTPException(404)
        ordered = _normalize_album_sort(s)
        try:
            idx = next(i for i, x in enumerate(ordered) if x.id == album_id)
        except StopIteration:
            return RedirectResponse(url="/albums", status_code=303)
        if idx > 0:
            prev = ordered[idx - 1]
            a = ordered[idx]
            a.sort_order, prev.sort_order = prev.sort_order, a.sort_order
            s.commit()
    return RedirectResponse(url="/albums", status_code=303)


@app.post("/albums/{album_id}/move_down")
def album_move_down(album_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        a = s.get(Album, album_id)
        if a is None:
            raise HTTPException(404)
        ordered = _normalize_album_sort(s)
        try:
            idx = next(i for i, x in enumerate(ordered) if x.id == album_id)
        except StopIteration:
            return RedirectResponse(url="/albums", status_code=303)
        if idx < len(ordered) - 1:
            nxt = ordered[idx + 1]
            a = ordered[idx]
            a.sort_order, nxt.sort_order = nxt.sort_order, a.sort_order
            s.commit()
    return RedirectResponse(url="/albums", status_code=303)


@app.post("/albums/save")
async def album_save(request: Request, user: User = Depends(require_admin)):
    form = await request.form()
    data = dict(form)
    name = data.get("name", "").strip()
    if not name:
        return RedirectResponse(url="/albums", status_code=303)
    with SessionLocal() as s:
        if data.get("id"):
            a = s.get(Album, int(data["id"]))
            if a:
                a.name = name
        else:
            a = Album(name=name)
            s.add(a)
        s.commit()
    return RedirectResponse(url="/albums", status_code=303)


@app.post("/albums/{album_id}/delete")
def album_delete(album_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        a = s.get(Album, album_id)
        if a:
            s.delete(a)
            s.commit()
    return RedirectResponse(url="/albums", status_code=303)


# ====================== Storage-Targets ======================

def _cam_count_per_target(s):
    rows = (
        s.query(cam_storage_targets.c.storage_target_id, sa_func.count())
        .group_by(cam_storage_targets.c.storage_target_id)
        .all()
    )
    return {tid: cnt for tid, cnt in rows}


@app.get("/storage")
def storage_list(request: Request, _: str = Depends(require_auth)):
    with SessionLocal() as s:
        targets = s.query(StorageTarget).order_by(
            StorageTarget.sort_order, StorageTarget.name
        ).all()
        cam_counts = _cam_count_per_target(s)
        return templates.TemplateResponse(
            "storage.html",
            {
                "request": request,
                "targets": targets,
                "cam_counts": cam_counts,
                "type_labels": TYPE_LABELS,
                "flash": None,
            },
        )


def _render_storage_form(request, target, type_id, current_config, flash=None):
    backend_types = list_backend_types()
    try:
        cls = backend_meta(type_id)
    except Exception:
        cls = backend_meta(backend_types[0][0])
        type_id = cls.type_id
    fields = cls.config_fields()
    return templates.TemplateResponse(
        "storage_form.html",
        {
            "request": request,
            "target": target,
            "backend_types": backend_types,
            "backend_fields": fields,
            "backend_description": cls.description,
            "current_config": current_config,
            "selected_type": type_id,
            "needs_path_template": type_id != "amazon",
            "default_path_template": "{cam_slug}/{Y}-{m}-{d}/{H}-{M}-{S}{ext}",
            "flash": flash,
        },
    )


@app.get("/storage/new")
def storage_new_form(request: Request, type: str = "local", _: str = Depends(require_auth)):
    return _render_storage_form(request, None, type, {}, None)


@app.get("/storage/{target_id}/edit")
def storage_edit_form(target_id: int, request: Request, _: str = Depends(require_auth)):
    with SessionLocal() as s:
        target = s.get(StorageTarget, target_id)
        if target is None:
            raise HTTPException(404)
        try:
            cfg = json.loads(target.config_json or "{}")
            if not isinstance(cfg, dict):
                cfg = {}
        except json.JSONDecodeError:
            cfg = {}
        return _render_storage_form(request, target, target.type, cfg, None)


@app.post("/storage/save")
async def storage_save(request: Request, user: User = Depends(require_admin)):
    form = await request.form()
    data = dict(form)
    action = data.get("action", "save")
    target_id_raw = data.get("id", "").strip()
    type_id = data.get("type", "").strip()
    name = data.get("name", "").strip()
    path_template = data.get("path_template", "").strip()
    enabled = data.get("enabled") == "on"
    try:
        retention_per_cam = max(0, int(data.get("retention_per_cam") or 0))
    except (TypeError, ValueError):
        retention_per_cam = 0

    if not name:
        return _render_storage_form(request, None, type_id, {}, None)

    cfg = {}
    try:
        cls = backend_meta(type_id)
    except Exception as e:
        return _render_storage_form(
            request, None, type_id, {},
            {"ok": False, "msg": f"Ungueltiger Typ: {e}"},
        )
    for field in cls.config_fields():
        key = f"cfg__{field.key}"
        if field.kind == "checkbox":
            cfg[field.key] = "1" if data.get(key) == "on" else ""
        else:
            cfg[field.key] = (data.get(key) or "").strip()

    with SessionLocal() as s:
        if target_id_raw:
            target = s.get(StorageTarget, int(target_id_raw))
            if target is None:
                raise HTTPException(404)
            type_id = target.type
            cls = backend_meta(type_id)
        else:
            existing = s.query(StorageTarget).filter(StorageTarget.name == name).first()
            if existing is not None:
                return _render_storage_form(
                    request, None, type_id, cfg,
                    {"ok": False, "msg": f"Name '{name}' existiert bereits."},
                )
            target = StorageTarget(name=name, type=type_id)
            s.add(target)

        target.name = name
        target.config_json = json.dumps(cfg)
        target.path_template = path_template or (
            "" if type_id == "amazon" else "{cam_slug}/{Y}-{m}-{d}/{H}-{M}-{S}{ext}"
        )
        target.enabled = enabled
        target.retention_per_cam = retention_per_cam

        if action != "save_skip_test":
            try:
                backend = cls(
                    config=cfg,
                    path_template=target.path_template,
                    target_name=name,
                )
                ok, msg = backend.test_connection()
            except Exception as e:
                ok, msg = False, f"Test-Exception: {e}"
            if not ok:
                s.rollback()
                target_for_render = None if not target_id_raw else s.get(StorageTarget, int(target_id_raw))
                return _render_storage_form(
                    request, target_for_render, type_id, cfg,
                    {"ok": False, "msg": f"Verbindungs-Test fehlgeschlagen: {msg}"},
                )
            target.last_status = "success"
            target.last_status_msg = msg
            target.last_status_at = datetime.utcnow()

        s.commit()
    return RedirectResponse(url="/storage", status_code=303)


@app.post("/storage/{target_id}/delete")
def storage_delete(target_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        t = s.get(StorageTarget, target_id)
        if t:
            s.delete(t)
            s.commit()
    return RedirectResponse(url="/storage", status_code=303)


@app.post("/storage/{target_id}/toggle")
def storage_toggle(target_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        t = s.get(StorageTarget, target_id)
        if t:
            t.enabled = not t.enabled
            s.commit()
    return RedirectResponse(url="/storage", status_code=303)


@app.post("/storage/{target_id}/test")
def storage_test(target_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        t = s.get(StorageTarget, target_id)
        if t is None:
            raise HTTPException(404)
        try:
            backend = get_backend(t)
            ok, msg = backend.test_connection()
        except Exception as e:
            ok, msg = False, f"Exception: {e}"
        t.last_status = "success" if ok else "error"
        t.last_status_msg = msg
        t.last_status_at = datetime.utcnow()
        s.commit()
    return RedirectResponse(url="/storage", status_code=303)


# ====================== Logs / Settings ======================

@app.get("/logs")
def logs(
    request: Request,
    cam_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 200,
    flash: Optional[str] = None,
    _: str = Depends(require_auth),
):
    with SessionLocal() as s:
        q = s.query(Fetch).options(
            joinedload(Fetch.cam),
            joinedload(Fetch.target_uploads),
        )
        if cam_id:
            q = q.filter(Fetch.cam_id == cam_id)
        if status:
            q = q.filter(Fetch.status == status)
        rows = q.order_by(Fetch.started_at.desc()).limit(min(limit, 1000)).all()
        cams = s.query(Cam).order_by(Cam.sort_order, Cam.name).all()
        # Gesamt-Treffer (vor LIMIT) fuer Loesch-Button-Anzeige
        cnt_q = s.query(sa_func.count(Fetch.id))
        if cam_id:
            cnt_q = cnt_q.filter(Fetch.cam_id == cam_id)
        if status:
            cnt_q = cnt_q.filter(Fetch.status == status)
        total_matching = cnt_q.scalar() or 0
        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "rows": rows,
                "cams": cams,
                "filter_cam_id": cam_id,
                "filter_status": status,
                "total_matching": total_matching,
                "flash": flash,
            },
        )


@app.post("/logs/clear")
async def logs_clear(request: Request, user: User = Depends(require_admin)):
    """Loescht Fetch-Logs (und zugehoerige TargetUpload-Eintraege).
    Respektiert die Filter cam_id/status aus dem Form-Body.
    Bei leerem Filter werden ALLE Logs geloescht."""
    form = await request.form()
    cam_id_raw = (form.get("cam_id") or "").strip()
    status_raw = (form.get("status") or "").strip()
    cam_id_val = int(cam_id_raw) if cam_id_raw else None
    status_val = status_raw if status_raw else None
    with SessionLocal() as s:
        q = s.query(Fetch.id)
        if cam_id_val:
            q = q.filter(Fetch.cam_id == cam_id_val)
        if status_val:
            q = q.filter(Fetch.status == status_val)
        fetch_ids = [row[0] for row in q.all()]
        n = len(fetch_ids)
        if n:
            # Erst die abhaengigen TargetUploads (kein Cascade bei Bulk-Delete)
            s.query(TargetUpload).filter(
                TargetUpload.fetch_id.in_(fetch_ids)
            ).delete(synchronize_session=False)
            s.query(Fetch).filter(
                Fetch.id.in_(fetch_ids)
            ).delete(synchronize_session=False)
            s.commit()
    # Filter im Redirect erhalten + Flash-Nachricht
    qs = []
    if cam_id_val:
        qs.append(f"cam_id={cam_id_val}")
    if status_val:
        qs.append(f"status={status_val}")
    qs.append(f"flash={n} Log-Eintraege geloescht.")
    return RedirectResponse(url="/logs?" + "&".join(qs), status_code=303)


def _read_env_var(name: str) -> Optional[str]:
    """Liest eine WU_*-Variable aus der env-Datei (z.B. /etc/webcam-uploader/.env)."""
    env_path = settings.env_file_path
    if not env_path.exists():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == name:
                return val.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _write_env_var(name: str, value: str) -> tuple[bool, str]:
    """Schreibt eine WU_*-Variable in die env-Datei. Erhaelt Kommentare/Reihenfolge.
    Wenn die Variable nicht existiert, wird sie ans Ende angefuegt."""
    env_path = settings.env_file_path
    try:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            lines = content.splitlines()
        else:
            lines = []
        new_lines = []
        replaced = False
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key == name:
                    new_lines.append(f"{name}={value}")
                    replaced = True
                    continue
            new_lines.append(line)
        if not replaced:
            new_lines.append(f"{name}={value}")
        # trailing newline
        out = "\n".join(new_lines)
        if not out.endswith("\n"):
            out += "\n"
        env_path.write_text(out, encoding="utf-8")
        return True, "OK"
    except OSError as e:
        return False, str(e)


def _env_file_status() -> dict:
    """Status der .env-Datei fuer die UI: Pfad, existiert?, schreibbar?, aktueller Port."""
    import os as _os
    env_path = settings.env_file_path
    exists = env_path.exists()
    writable = False
    try:
        if exists:
            writable = _os.access(env_path, _os.W_OK)
        else:
            # Verzeichnis schreibbar?
            writable = _os.access(env_path.parent, _os.W_OK)
    except OSError:
        writable = False
    return {
        "path": str(env_path),
        "exists": exists,
        "writable": writable,
        "port": _read_env_var("WU_PORT") or str(settings.port),
        "host": _read_env_var("WU_HOST") or settings.host,
    }


def _system_stats(s):
    """Sammelt System-Status-Infos."""
    import shutil, os as _os
    from .db import _engine
    stats = {}
    stats["cam_count"] = s.query(Cam).count()
    stats["target_count"] = s.query(StorageTarget).count()
    stats["fetch_count"] = s.query(Fetch).count()
    stats["user_count"] = s.query(User).count()
    last = s.query(Fetch).order_by(Fetch.started_at.desc()).first()
    stats["last_fetch_at"] = last.started_at if last else None
    db_path = settings.db_path
    try:
        stats["db_size"] = db_path.stat().st_size
    except OSError:
        stats["db_size"] = 0
    try:
        du = shutil.disk_usage(str(settings.data_dir))
        stats["disk_free"] = du.free
        stats["disk_total"] = du.total
    except OSError:
        stats["disk_free"] = 0
        stats["disk_total"] = 0
    stats["startup_at"] = APP_STARTUP_AT
    return stats


@app.get("/settings")
def settings_view(request: Request, user: User = Depends(require_auth)):
    with SessionLocal() as s:
        cookies_raw = get_setting(s, "amazon_cookies", "")
        tld = get_setting(s, "amazon_tld", "de")
        retention_days = int(get_setting(s, "fetch_retention_days", "0") or "0")
        users = s.query(User).order_by(User.id).all()
        stats = _system_stats(s)
    amazon_ok, amazon_msg = uploader_health()
    env_status = _env_file_status()
    cookie_keys = []
    cookie_values = {}
    if cookies_raw:
        try:
            parsed = json.loads(cookies_raw)
            if isinstance(parsed, dict):
                cookie_keys = sorted(parsed.keys())
                cookie_values = parsed
        except json.JSONDecodeError:
            cookie_keys = ["(ungueltiges JSON)"]
    # Pflicht-Cookies fuer Amazon Photos DE
    cookie_names = [
        ("at-acbde", True),
        ("ubid-acbde", True),
        ("session-id", True),
        ("session-token", True),
        ("sst-acbde", False),
        ("sess-at-acbde", False),
        ("x-acbde", False),
    ]
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "cookie_keys": cookie_keys,
            "cookie_values": cookie_values,
            "cookie_names": cookie_names,
            "tld": tld,
            "amazon_ok": amazon_ok,
            "amazon_msg": amazon_msg,
            "users": users,
            "current_user": user,
            "stats": stats,
            "retention_days": retention_days,
            "env_status": env_status,
        },
    )


@app.post("/settings/port")
async def settings_port(request: Request, user: User = Depends(require_admin)):
    """Schreibt den neuen Port in die env-Datei. Service-Neustart erforderlich."""
    form = await request.form()
    port_raw = (form.get("port") or "").strip()
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        return RedirectResponse(url="/settings?err=port_invalid#netzwerk", status_code=303)
    if port < 1 or port > 65535:
        return RedirectResponse(url="/settings?err=port_range#netzwerk", status_code=303)
    if port < 1024 and port != 80 and port != 443:
        return RedirectResponse(url="/settings?err=port_privileged#netzwerk", status_code=303)
    ok, msg = _write_env_var("WU_PORT", str(port))
    if not ok:
        log.warning("Port-Schreiben fehlgeschlagen: %s", msg)
        return RedirectResponse(url="/settings?err=port_write#netzwerk", status_code=303)
    # Detached Service-Restart: nach 2 Sekunden 'sudo -n systemctl restart webcam-uploader'.
    # Damit hat der HTTP-Response Zeit, den Browser zu erreichen, bevor der Service
    # den Socket zumacht. Braucht den sudoers-Eintrag aus install.sh.
    restart_triggered = False
    try:
        import subprocess
        subprocess.Popen(
            ["sh", "-c", "sleep 2 && sudo -n systemctl restart webcam-uploader 2>&1 | logger -t webcam-uploader-restart"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # vom Parent-Prozess loesen, ueberlebt unseren Tod
        )
        restart_triggered = True
        log.info("Port geaendert auf %s. Service-Restart in 2s eingeplant.", port)
    except Exception as e:
        log.warning("Konnte Auto-Restart nicht starten: %s", e)
    flag = "port_restart" if restart_triggered else "port_saved"
    return RedirectResponse(url=f"/settings?ok={flag}_{port}#netzwerk", status_code=303)


# ===================== User-Management =====================

@app.post("/settings/users/create")
async def users_create(request: Request, user: User = Depends(require_admin)):
    from .security import hash_password
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    role = (form.get("role") or "viewer").strip()
    if role not in ("admin", "viewer"):
        role = "viewer"
    if not username or not password:
        return RedirectResponse(url="/settings", status_code=303)
    with SessionLocal() as s:
        existing = s.query(User).filter(User.username == username).first()
        if existing is not None:
            return RedirectResponse(url="/settings?err=user_exists", status_code=303)
        u = User(username=username, password_hash=hash_password(password), role=role)
        s.add(u); s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/users/{user_id}/password")
async def users_password(user_id: int, request: Request, user: User = Depends(require_admin)):
    from .security import hash_password
    form = await request.form()
    pw = form.get("password") or ""
    if not pw:
        return RedirectResponse(url="/settings", status_code=303)
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if u is not None:
            u.password_hash = hash_password(pw)
            s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/users/{user_id}/role")
async def users_role(user_id: int, request: Request, user: User = Depends(require_admin)):
    form = await request.form()
    role = (form.get("role") or "viewer").strip()
    if role not in ("admin", "viewer"):
        return RedirectResponse(url="/settings", status_code=303)
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if u is None:
            return RedirectResponse(url="/settings", status_code=303)
        # Schutz: letzten Admin nicht degradieren
        if u.role == "admin" and role != "admin":
            admin_count = s.query(User).filter(User.role == "admin").count()
            if admin_count <= 1:
                return RedirectResponse(url="/settings?err=last_admin", status_code=303)
        u.role = role
        s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/users/{user_id}/delete")
def users_delete(user_id: int, user: User = Depends(require_admin)):
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if u is None:
            return RedirectResponse(url="/settings", status_code=303)
        if u.role == "admin":
            admin_count = s.query(User).filter(User.role == "admin").count()
            if admin_count <= 1:
                return RedirectResponse(url="/settings?err=last_admin", status_code=303)
        s.delete(u); s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/me/password")
async def me_password(request: Request, user: User = Depends(require_auth)):
    """Jeder eingeloggte User kann sein eigenes Passwort aendern (auch Viewer)."""
    from .security import hash_password
    form = await request.form()
    pw = form.get("password") or ""
    if not pw or user.id == 0:
        return RedirectResponse(url="/settings", status_code=303)
    with SessionLocal() as s:
        u = s.get(User, user.id)
        if u is not None:
            u.password_hash = hash_password(pw)
            s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/retention")
async def settings_retention(request: Request, user: User = Depends(require_admin)):
    form = await request.form()
    try:
        days = max(0, int(form.get("retention_days") or 0))
    except (TypeError, ValueError):
        days = 0
    with SessionLocal() as s:
        set_setting(s, "fetch_retention_days", str(days))
    return RedirectResponse(url="/settings", status_code=303)


@app.get("/settings/backup")
def settings_backup(user: User = Depends(require_admin)):
    """Liefert die aktuelle SQLite-DB als Download."""
    db_path = settings.db_path
    if not db_path.exists():
        raise HTTPException(404, "Keine DB gefunden.")
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    filename = f"webcam-uploader-backup-{ts}.sqlite3"
    return FileResponse(
        str(db_path),
        media_type="application/x-sqlite3",
        filename=filename,
    )


@app.post("/settings/restore")
async def settings_restore(request: Request, user: User = Depends(require_admin)):
    """Ersetzt die aktive DB durch ein hochgeladenes Backup. Service-Restart erforderlich."""
    import shutil as _shutil
    form = await request.form()
    upload = form.get("backup_file")
    if upload is None or not hasattr(upload, "file"):
        return RedirectResponse(url="/settings?err=no_file", status_code=303)
    content = await upload.read()
    if len(content) < 100 or content[:16] != b"SQLite format 3\x00":
        return RedirectResponse(url="/settings?err=not_sqlite", status_code=303)
    db_path = settings.db_path
    # Sicherung der aktuellen DB anlegen
    if db_path.exists():
        bak = db_path.with_suffix(db_path.suffix + ".prerestore")
        try:
            _shutil.copy2(db_path, bak)
        except OSError:
            pass
    db_path.write_bytes(content)
    return RedirectResponse(url="/settings?ok=restored", status_code=303)


@app.post("/settings/save")
async def settings_save(request: Request, user: User = Depends(require_admin)):
    """Speichert Amazon-Cookies (Einzelfelder + JSON-Textarea) + TLD."""
    form = await request.form()
    legacy_json = (form.get("amazon_cookies") or "").strip()
    tld = (form.get("amazon_tld") or "de").strip() or "de"

    # Beginne mit bestehenden Cookies aus der DB (Merge fuer Teil-Updates)
    with SessionLocal() as s:
        existing_raw = get_setting(s, "amazon_cookies", "")
    existing = {}
    if existing_raw:
        try:
            parsed = json.loads(existing_raw)
            if isinstance(parsed, dict):
                existing = parsed
        except json.JSONDecodeError:
            existing = {}

    merged = dict(existing)
    any_change = False

    # 1) Einzelfelder cookie__<name> (vom Cookie-Setup-Assistenten)
    PREFIX = "cookie_" + "_"
    for key in list(form.keys()):
        if not key.startswith(PREFIX):
            continue
        name = key[len(PREFIX):]
        if not name:
            continue
        val = (form.get(key) or "").strip()
        if val:
            merged[name] = val
            any_change = True

    # 2) Legacy: komplettes JSON-Textarea ueberschreibt alles
    if legacy_json:
        try:
            parsed = json.loads(legacy_json)
            if not isinstance(parsed, dict):
                raise ValueError("Cookies muessen JSON-Objekt sein")
            merged = parsed
            any_change = True
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(400, f"Ungueltiges JSON: {e}")

    with SessionLocal() as s:
        if any_change:
            set_setting(s, "amazon_cookies", json.dumps(merged))
        set_setting(s, "amazon_tld", tld)
    reset_client()
    return RedirectResponse(url="/settings", status_code=303)


# ====================== Filesystem-Browser ======================

@app.get("/api/fs/browse")
def api_fs_browse(path: str = "/", _: str = Depends(require_auth)):
    """Listet Unterverzeichnisse eines Pfads. Nur Verzeichnisse, keine Dateien.

    Antwort: {ok, path, parent, exists, writable, entries: [{name, full_path}]}
    """
    import os as _os
    try:
        target = Path(path or "/").expanduser()
        try:
            target = target.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            return {"ok": False, "error": str(e), "path": str(target)}
        result = {
            "ok": True,
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "exists": target.exists(),
            "is_dir": target.is_dir() if target.exists() else False,
            "writable": _os.access(target, _os.W_OK) if target.exists() else False,
            "entries": [],
        }
        if target.exists() and target.is_dir():
            try:
                items = []
                with _os.scandir(target) as it:
                    for entry in it:
                        if entry.name.startswith("."):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                items.append({
                                    "name": entry.name,
                                    "full_path": str(target / entry.name),
                                })
                        except OSError:
                            continue
                items.sort(key=lambda x: x["name"].lower())
                result["entries"] = items
            except PermissionError as e:
                result["error"] = f"Kein Zugriff: {e}"
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}


@app.post("/api/fs/mkdir")
async def api_fs_mkdir(request: Request, user: User = Depends(require_admin)):
    """Erzeugt ein neues Verzeichnis. Body: JSON {path: "/parent/newdir"}."""
    body = await request.json()
    raw = (body.get("path") or "").strip() if isinstance(body, dict) else ""
    if not raw:
        return {"ok": False, "error": "Pfad fehlt."}
    try:
        target = Path(raw).expanduser().resolve(strict=False)
        target.mkdir(parents=True, exist_ok=False)
        return {"ok": True, "path": str(target)}
    except FileExistsError:
        return {"ok": False, "error": "Verzeichnis existiert bereits."}
    except PermissionError as e:
        return {"ok": False, "error": f"Keine Berechtigung: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/cams/reorder")
async def api_cams_reorder(request: Request, user: User = Depends(require_admin)):
    """Setzt die Cam-Sortierung neu. Body: {"ids": [3, 1, 2, ...]}.
    Cams werden in genau dieser Reihenfolge auf sort_order = 1..N gesetzt;
    nicht gelistete Cams behalten ihre alte Position dahinter."""
    body = await request.json()
    ids = body.get("ids") or [] if isinstance(body, dict) else []
    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        return {"ok": False, "error": "ids muss eine Liste von Integern sein"}
    if not ids:
        return {"ok": False, "error": "leere Liste"}
        cams = {c.id: c for c in s.query(Cam).filter(Cam.id.in_(ids)).all()}
        for i, cid in enumerate(ids, start=1):
            c = cams.get(cid)
            if c is not None:
                c.sort_order = i
        # nicht gelistete Cams ans Ende
        other = (
            s.query(Cam).filter(~Cam.id.in_(ids)).order_by(Cam.sort_order, Cam.name).all()
        )
        offset = len(ids)
        for j, c in enumerate(other, start=1):
            c.sort_order = offset + j
        s.commit()
    return {"ok": True, "count": len(ids)}


@app.post("/api/albums/reorder")
async def api_albums_reorder(request: Request, user: User = Depends(require_admin)):
    """Setzt die Album-Sortierung neu. Body: {"ids": [3, 1, 2, ...]}."""
    body = await request.json()
    ids = body.get("ids") or [] if isinstance(body, dict) else []
    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        return {"ok": False, "error": "ids muss eine Liste von Integern sein"}
    if not ids:
        return {"ok": False, "error": "leere Liste"}
    with SessionLocal() as s:
        albums = {a.id: a for a in s.query(Album).filter(Album.id.in_(ids)).all()}
        for i, aid in enumerate(ids, start=1):
            a = albums.get(aid)
            if a is not None:
                a.sort_order = i
        other = (
            s.query(Album).filter(~Album.id.in_(ids)).order_by(Album.sort_order, Album.name).all()
        )
        offset = len(ids)
        for j, a in enumerate(other, start=1):
            a.sort_order = offset + j
        s.commit()
    return {"ok": True, "count": len(ids)}


@app.get("/logout")
def logout(request: Request):
    """Logout-Seite – oeffentlich. JS invalidiert den HTTPBasic-Auth-Cache."""
    return templates.TemplateResponse("logout.html", {"request": request})


@app.get("/api/cams")
def api_cams(_: str = Depends(require_auth)):
    with SessionLocal() as s:
        cams = (
            s.query(Cam)
            .options(joinedload(Cam.album), joinedload(Cam.storage_targets))
            .order_by(Cam.sort_order, Cam.name)
            .all()
        )
        return [_cam_summary(c, c.album.name if c.album else None) for c in cams]


@app.get("/api/health")
def api_health(_: str = Depends(require_auth)):
    ok, msg = uploader_health()
    return {"amazon_ok": ok, "amazon_msg": msg, "version": __version__}
