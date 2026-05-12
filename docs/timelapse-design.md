# Timelapse / Video Playback — Design v0.10.0

**Status:** Design accepted 2026-05-12. Implementation in progress.

This document describes the design for playing back archived webcam images as a
"video" (browser slideshow) and exporting them as MP4 (server-side ffmpeg).

## Goals

- Watch any cam's history as a smooth slideshow in the browser, with date-range
  filtering, FPS control, and looping.
- Export the same view to an MP4 file with date/time overlay.
- Reuse the existing storage-target abstraction: the data source is one of the
  cam's `local` storage targets — no new persistence layer.
- Stay within the established UI patterns (sidebar nav for settings,
  drag-and-drop for sort, etc.) and keep the desktop/mobile responsiveness.

## Non-goals (deferred)

- Remote-backend pull (SFTP / S3 / Amazon as source) — postponed to v0.11.0.
- Live streaming (real RTSP-style video) — out of scope.
- Multi-cam montage — could be a v0.12+ feature.

## Data source: `target_uploads` + `local`

Every successful fetch already writes a `target_uploads` row with:

- `cam_id` (indexed via `_migrate_add_retention`)
- `storage_target_id` → joins to a configured Local backend
- `remote_ref` → **absolute filesystem path** for Local backend
- `status = 'success'`
- `pruned_at IS NULL` (not yet retention-purged)
- `bytes` and `started_at` on the row, plus `fetches.started_at` for the
  capture timestamp

The frame list for a cam between `from`/`to` is therefore a single SQL query
joining `target_uploads` and `fetches`, ordered by `fetches.started_at ASC`.
Filesystem walks are not needed; pruned files are filtered out by the
`pruned_at IS NULL` predicate, so dangling references never reach the player
or the renderer.

If a cam has multiple Local targets, the user picks one via a new column
`cams.timelapse_source_target_id` (FK to `storage_targets.id`, nullable; falls
back to "first active local target of this cam" if unset).

## UI: Cam-Detail Page

A new page `/cams/{cam_id}` (previously the cam list only linked to
`/cams/{cam_id}/edit`) hosts a tabbed view:

```
┌──────────────────────────────────────────────────────────────┐
│  ←  Webcams                                                  │
│  Cam-Name                                            [Pills] │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ [Vorschau] [Timelapse] [Logs] [Bearbeiten]           │    │
│  ├──────────────────────────────────────────────────────┤    │
│  │                                                      │    │
│  │  Tab body                                            │    │
│  │                                                      │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

- **Vorschau** — current preview, lightbox, the per-cam stats currently shown
  on the dashboard. Default tab.
- **Timelapse** — new. See below.
- **Logs** — `/logs?cam_id={id}` content, embedded.
- **Bearbeiten** — current `/cams/{id}/edit` form, embedded.

The existing `/cams/{id}/edit` route stays as an alias that 302-redirects to
`/cams/{id}#bearbeiten` so existing bookmarks keep working.

### Timelapse tab layout

```
┌─ Quelle ─────────────────────────────────────────────────────┐
│ Storage-Target: [Local NAS ▾]      (Auto)                    │
│ Verfügbare Bilder: 14.382 (2026-04-01 06:12 – 2026-05-12)    │
└──────────────────────────────────────────────────────────────┘

┌─ Zeitraum & Wiedergabe ──────────────────────────────────────┐
│ Von: [2026-05-01 06:00]   Bis: [2026-05-12 20:00]            │
│ Wochentage: [Mo][Di][Mi][Do][Fr][Sa][So]                     │
│ Tageszeit:  [06:00] – [20:00]   (optional)                   │
│ FPS:        [—————●——————] 25                                 │
│ Best-of-Day: [ ] Nur das hellste Bild pro Tag                │
│                                                              │
│  ▶ Vorschau     ⤓ Als MP4 exportieren                        │
└──────────────────────────────────────────────────────────────┘

┌─ Vorschau ───────────────────────────────────────────────────┐
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                                                         │ │
│  │              [Bild der aktuellen Position]              │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ◀◀  ◀  ▶/⏸  ▶  ▶▶          📅 2026-05-08 14:30:12           │
│  [━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━]   1.234 / 4.521         │
└──────────────────────────────────────────────────────────────┘

┌─ Renderings ─────────────────────────────────────────────────┐
│ ✓ 2026-05-01 → 05-12 · 25fps · 1080p · 4.3 MB · 2026-05-12  │
│   [↓ Download] [⨯ Löschen]                                   │
│ ⏳ 2026-04-01 → 04-30 · 25fps · 720p · gerendert: 38%        │
└──────────────────────────────────────────────────────────────┘
```

When the cam has **no Local target**, the body is replaced with an info card:

> Diese Webcam schreibt aktuell nur an Nicht-Local-Targets. Timelapse braucht
> Bilder auf dem lokalen Filesystem.
>
> Lege ein Local-Target unter [Speicherziele](/storage) an und ordne es dieser
> Cam zu. Du kannst Cams parallel an mehrere Targets schreiben lassen — alle
> bestehenden Uploads zu Amazon/SFTP/S3 bleiben unverändert.

## Browser slideshow

JavaScript-only, no encoding. The route `GET /api/cams/{id}/timelapse/frames`
returns JSON for a filter:

```json
{
  "cam_id": 7,
  "from": "2026-05-01T06:00:00",
  "to":   "2026-05-12T20:00:00",
  "count": 4521,
  "frames": [
    {"ts": "2026-05-01T06:12:43", "url": "/api/cams/7/timelapse/frame/9842"},
    {"ts": "2026-05-01T06:17:39", "url": "/api/cams/7/timelapse/frame/9843"},
    ...
  ]
}
```

The frames are referenced by `target_upload.id` (not by raw filesystem path),
so the player gets a stable URL and the server can validate that the upload
belongs to the cam before streaming.

The player preloads N frames ahead, swaps the `<img>` src on a `setTimeout`
loop matching the chosen FPS, and shows a scrub bar. For large ranges (>5000
frames) the JSON is paginated: 1000-frame chunks, prefetched on demand.

`GET /api/cams/{id}/timelapse/frame/{upload_id}` streams the JPEG with
`Content-Disposition: inline`. Validates `cam_id` match and `pruned_at IS
NULL`. Returns 404 if the file no longer exists on disk (handled gracefully
by the player: skip).

## MP4 export (ffmpeg)

`POST /api/cams/{id}/timelapse/render` creates a row in a new table
`timelapse_jobs` with status=`pending`, parameters, and a target output path.
A background worker (APScheduler job, polls every 5s) picks up pending jobs
and runs ffmpeg.

### ffmpeg invocation

The naive approach (`-pattern_type glob`) doesn't work because filenames in
the user's chosen path template are not necessarily numbered sequentially.
Instead the worker:

1. Creates a job-specific temp dir under
   `/var/lib/webcam-uploader/timelapse/tmp/{job_id}/`.
2. Iterates the SQL frame list, symlinks each remote_ref into the temp dir
   as `f_000001.jpg`, `f_000002.jpg`, … (zero-padded sequential).
3. Invokes ffmpeg:

```
ffmpeg -framerate {fps}
       -i {tmp}/f_%06d.jpg
       -vf "scale={w}:{h}:flags=lanczos,
            drawtext=text='{cam_name} %{eif\\:n\\:d\\:6}':
                     fontcolor=white:fontsize=18:
                     x=w-tw-12:y=h-th-12:
                     box=1:boxcolor=black@0.5:boxborderw=6"
       -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p
       -movflags +faststart
       {out}/{cam_slug}_{from}_{to}.mp4
```

The `drawtext` `text` actually uses a per-frame metadata file written by the
worker (the `%{eif:n:d:6}`-trick gives us the frame number, which we map to
the actual capture timestamp via a sidecar `.txt`). Simpler v1 fallback:
overlay just `{cam_name} · {from_date}–{to_date}` as a static label, and add
per-frame timestamps in v0.10.1.

4. On success: moves the MP4 to
   `/var/lib/webcam-uploader/timelapse/{cam_id}/{job_id}.mp4`, updates the
   `timelapse_jobs` row to `status='done'`, `output_path`, `bytes`, `duration_s`.
5. Cleans up the temp dir.
6. On error: records `status='error'`, `error_message`, cleans temp dir.

### Resolution presets

| Preset | Width | Note |
|---|---|---|
| Original | source | No scale filter |
| 1080p | min(1920, src) | Default |
| 720p  | min(1280, src) | Smaller files |

Aspect ratio preserved, `-2:H` and `W:-2` ensure even dimensions for libx264.

### Best-of-Day filter (Pillow)

If enabled, the worker pre-filters the frame list:

1. Group by `date(fetches.started_at, 'Europe/Berlin')`.
2. For each day, compute mean brightness on the 1920px preview (already cached
   for the dashboard lightbox), pick the brightest. Falls back to the raw JPG
   if no preview exists.
3. The filtered list goes into the regular ffmpeg pipeline.

Cost: ~30ms per day evaluated (Pillow histogram). For a 90-day range that's
under 3 seconds added overhead, well within the existing job-processing time.

### Job lifecycle

```
pending  ─► running  ─► done
              │           │
              ▼           ▼
            error      (downloadable)
```

Polling endpoint `GET /api/timelapse/jobs/{job_id}` returns `{status,
progress_pct, message, output_url?}`. The UI polls every 2s while a job is
running.

## Storage and retention

```
/var/lib/webcam-uploader/timelapse/
├── tmp/{job_id}/            (temp working dir, deleted after render)
├── {cam_id}/
│   ├── {job_id}.mp4         (final renderings)
│   └── ...
└── ...
```

Two settings, both in the existing Settings → Log retention section (renamed
"Aufbewahrung & Speicher"):

- `WU_TIMELAPSE_CACHE_MAX_GB` (default `5`) — global cap. When the total size
  of `/var/lib/webcam-uploader/timelapse/` exceeds this, the oldest renderings
  are deleted until under the cap.
- `WU_TIMELAPSE_RETENTION_PER_CAM` (default `10`) — per-cam max number of
  finished renderings. Older ones auto-delete.

Both are runtime-configurable in the Settings UI (new sub-section "Timelapse-
Cache"). Daily cleanup job (reuses the existing daily-cleanup APScheduler job
that already runs `cleanup_old_fetches`).

## DB migrations

```sql
-- _migrate_add_timelapse  (v0.10.0)

-- New column on cams: preferred source target for timelapse
ALTER TABLE cams ADD COLUMN timelapse_source_target_id INTEGER;

-- Job queue
CREATE TABLE IF NOT EXISTS timelapse_jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  cam_id          INTEGER NOT NULL REFERENCES cams(id) ON DELETE CASCADE,
  source_target_id INTEGER REFERENCES storage_targets(id) ON DELETE SET NULL,
  params_json     TEXT NOT NULL,    -- from, to, fps, resolution, best_of_day, label
  status          VARCHAR(20) NOT NULL DEFAULT 'pending',
  progress_pct    INTEGER NOT NULL DEFAULT 0,
  frame_count     INTEGER,
  output_path     VARCHAR(500),
  bytes           INTEGER,
  duration_s      REAL,
  error_message   TEXT,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  started_at      DATETIME,
  finished_at     DATETIME
);
CREATE INDEX IF NOT EXISTS idx_timelapse_jobs_cam_status
  ON timelapse_jobs (cam_id, status);
```

Both idempotent, same pattern as the existing `_migrate_*` functions in
`db.py`.

## Installer change

`install.sh` adds `ffmpeg` to the apt-get install line:

```bash
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-dev \
  build-essential libjpeg-dev zlib1g-dev libwebp-dev \
  ca-certificates curl \
  ffmpeg            # NEW v0.10.0
```

On Debian 12 / Ubuntu 22.04+ this is ~20 MB extra. Re-running install.sh on
an existing v0.9.3 deployment installs ffmpeg without touching the rest.

## File-level change-set

- `app/db.py` — new model `TimelapseJob`, new column `cams.timelapse_source_target_id`, new migration `_migrate_add_timelapse`.
- `app/timelapse.py` — **NEW** module with `list_frames`, `enqueue_job`, `worker_tick`, `cleanup_cache`, `best_of_day_filter`.
- `app/main.py` — new routes:
  - `GET  /cams/{id}` (detail page with tabs)
  - `GET  /api/cams/{id}/timelapse/frames?from=&to=&weekdays=&time_start=&time_end=`
  - `GET  /api/cams/{id}/timelapse/frame/{upload_id}`
  - `POST /api/cams/{id}/timelapse/render`
  - `GET  /api/timelapse/jobs/{job_id}`
  - `GET  /api/timelapse/jobs/{job_id}/download`
  - `POST /api/timelapse/jobs/{job_id}/delete`
  - `/cams/{id}/edit` → 302 to `/cams/{id}#bearbeiten`
- `app/templates/cam_detail.html` — **NEW**, hosts the tabs.
- `app/templates/_timelapse.html` — **NEW**, partial for the timelapse tab.
- `app/templates/settings.html` — new sub-section "Timelapse-Cache".
- `app/static/app.js` (or new `timelapse.js`) — slideshow player, render-form poller.
- `app/static/style.css` — tab styles, slider, progress-bar.
- `app/scheduler.py` — register `worker_tick` job (every 5s) and add timelapse cache cleanup to the daily job.
- `install.sh` — add `ffmpeg` to apt-get line.
- `requirements.txt` — no new Python deps (Pillow is already there).
- `CHANGELOG.md` — v0.10.0 entry.
- `app/__init__.py` — bump `__version__` to `0.10.0`.

## Edge cases handled in v0.10.0

- Cam has multiple Local targets → user-pickable via dropdown; falls back to first active local.
- Cam has zero Local targets → tab body replaced with info card + storage link.
- Files pruned by retention while a job runs → skip silently, continue with remaining frames.
- File reference exists in DB but file missing on disk → skip, log warning.
- ffmpeg not installed → render endpoint returns 503 with helpful message ("Run sudo bash install.sh to add ffmpeg").
- Concurrent jobs for the same cam → allowed (different params is common); the worker processes them sequentially in FIFO order to bound CPU usage.
- Very large ranges (>50.000 frames) → soft cap, the form warns "Render dauert voraussichtlich X Minuten", but doesn't block.

## Not handled in v0.10.0 (next iterations)

- Per-frame timestamp overlay (needs sidecar file generation, deferred to v0.10.1).
- SFTP / S3 / Amazon pull (v0.11.0).
- Audio track from a music URL (probably never).
- WebM/AV1 export (v0.10.x if asked).
