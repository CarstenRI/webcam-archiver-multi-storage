# Webcam Archiver — Multi-Storage Edition

**Version 0.9.3** · See [CHANGELOG.md](CHANGELOG.md) for the full release history.

A self-hosted service that periodically fetches images from public webcams and uploads them in parallel to **multiple storage backends** — Amazon Photos, local filesystem, SFTP, or any S3-compatible object store. Built on FastAPI, ships as a single systemd service for Ubuntu / Debian, and is operated through a fully featured web UI.

> Originally built to archive a curated set of family-and-favorite webcams into the household's Amazon Photos library — and grew from there into a small fleet manager for 10–50 cams.

---

## Highlights

- **Multi-Storage Fan-Out.** Every webcam can push to one or many storage targets in parallel. Mix Amazon Photos for the family album, local disk for the NAS, and S3 for cold backup — all from the same fetch.
- **Smart Scheduling.** Per-cam interval, weekday whitelist, time windows in `HH:MM`–`HH:MM`, or astronomical **sunrise / sunset windows** based on the cam's GPS coordinates.
- **Duplicate Detection.** Perceptual-hash comparison (configurable Hamming-distance threshold, globally and per-cam) skips redundant uploads.
- **Albums.** Maps cleanly onto Amazon Photos albums; cams without a target fall back to the first active Amazon target as a safety net.
- **Multi-User Auth.** Bcrypt-hashed passwords, `admin` / `viewer` roles, last-admin protection, `.env` emergency fallback if the DB is empty.
- **Polished Web UI.** Live dashboard with thumbnails and lightbox, drag-and-drop sort for cams / albums / dashboard, log viewer with filters, mobile-responsive layout, settings sidebar with sticky scroll-spy nav, single-page Cookie wizard for Amazon-Photos re-auth.
- **Operational niceties.** SQLite backup + restore from the UI, log retention with daily cleanup job, per-target retention policy (max images per cam), filesystem browser for the local backend, system status box (uptime, DB size, disk free), live system time in the local server timezone, **port change with automatic service restart** (passwordless via `sudoers.d` drop-in).
- **Self-Heal Scheduler.** A 30-minute job re-syncs missing APScheduler entries so a service restart never leaves a cam stuck.

## Storage Backends

| Backend | Use case | Notes |
|---|---|---|
| `amazon` | Family photo library | Cookie-based session (unofficial lib). 15-min skip-mode on 401 so expired cookies don't storm. |
| `local` | NAS / mounted volumes | Built-in filesystem browser when configuring the target. |
| `sftp` | Remote SSH server | Password or key auth, optional strict host-key check, auto-mkdir, leaf-directory cleanup on retention. |
| `s3` | AWS / B2 / R2 / MinIO / Wasabi / Hetzner | Custom endpoint URL supported; works with any S3-compatible API. |

Path templates per target are fully configurable with placeholders like `{cam_slug}/{Y}-{m}-{d}/{H}-{M}-{S}{ext}`.

> ⚠ **A note on Amazon Photos.** There is no official public API. We use the unofficial [`amazon-photos`](https://pypi.org/project/amazon-photos/) library, which authenticates via browser cookies. It works well in practice but Amazon could break it at any time, and it may technically conflict with their Terms of Service. Use at your own risk for personal archival.

---

## Requirements

- Linux server (Ubuntu 22.04 LTS+ or Debian 11+)
- Root / sudo access for the one-time install
- Python 3.10+ (installed automatically by the bundle if missing)
- For the `amazon` backend: a working Amazon Photos account and the ability to extract cookies from a browser session

---

## Installation

### The easy way: download the release bundle

Grab `webcam-uploader-install.sh` from the latest [GitHub Release](../../releases/latest), drop it on your server, and run it. Everything else — system packages, service user, venv, systemd unit, sudoers entry, random initial password — is set up for you.

```bash
# on your server
wget https://github.com/CarstenRI/webcam-archiver-multi-storage/releases/latest/download/webcam-uploader-install.sh
sudo bash webcam-uploader-install.sh
```

The bundle is fully self-contained: the application code sits embedded as a base64 payload, so you only need to copy a single file to the server. Re-running the script performs an **update** (same script, idempotent).

The installer will:

- install system packages (`python3`, `libjpeg`, `libwebp`, build tools, etc.)
- create the `webcam-uploader` service user
- copy the code to `/opt/webcam-uploader`
- create a virtual environment and install pinned Python dependencies
- generate a random initial admin password
- write `/etc/webcam-uploader/.env` (config file)
- drop a `/etc/sudoers.d/webcam-uploader` entry so the UI can restart the service after a port change (NOPASSWD, scoped strictly to that one command, validated with `visudo -c`)
- register the systemd unit and start the service

At the end of the run you'll see the URL and the generated admin password.

### Building the bundle yourself (from source)

If you want to build the installer locally instead of using the released artifact:

```bash
git clone https://github.com/CarstenRI/webcam-archiver-multi-storage.git
cd webcam-archiver-multi-storage
bash scripts/make_bundle.sh
# → produces ./webcam-uploader-install.sh
```

Then deploy with `deploy.sh` if you have SSH access:

```bash
./deploy.sh user@server.example.com
# non-standard port:
./deploy.sh user@server.example.com -p 2222
```

`deploy.sh` builds the bundle, scp's it over, and runs `sudo install.sh` remotely. Re-running it is the standard way to deploy updates.

---

## First-time setup

1. Open the web UI: `http://<server-ip>:8080`
2. Log in with `admin` and the password printed by the installer (or read it from `/etc/webcam-uploader/.env`).
3. Go to **Settings** and immediately change the admin password.
4. **Storage Targets** → create at least one target (Local / SFTP / S3 / Amazon Photos). The save button runs a live connectivity test by default — failures don't persist unless you explicitly override.
5. *(Optional but recommended for Amazon)* On the Settings page, the Cookie wizard guides you through pasting cookies straight from your browser's DevTools.
6. **Albums** → create albums for grouping (optional).
7. **Webcams → + New Webcam** → fill in URL, interval, schedule, geo-coordinates, target assignment, album, and any per-cam overrides.

### Amazon Photos cookies

The Cookie wizard accepts three input methods, in order of convenience:

1. **DevTools table paste.** Copy the row from Chrome → DevTools → Application → Cookies → `amazon.de` and paste into the wizard's textarea. The parser handles both tab- and multi-space separated columns and fills the per-cookie fields automatically.
2. **JSON paste.** A JSON object like `{"at-acbde":"...", "ubid-acbde":"..."}` is accepted as-is.
3. **Per-field entry.** Seven labelled fields (`at-acbde`, `ubid-acbde`, `session-id`, `session-token`, `sst-acbde`, `sess-at-acbde`, `x-acbde`) with live "✓ set / ✗ missing" indicators. Empty fields don't overwrite existing values, so partial updates work for cookie refreshes.

When cookies expire (typically every 24–72h), Amazon returns `401`, the backend enters a 15-minute skip mode (no retry storms), and the dashboard shows a red banner with a direct link to **Settings** to paste fresh cookies.

---

## Configuration

The main config lives in `/etc/webcam-uploader/.env`:

```ini
WU_HOST=0.0.0.0
WU_PORT=8080
WU_AUTH_USER=admin
WU_AUTH_PASSWORD=...

WU_DATA_DIR=/var/lib/webcam-uploader
WU_TMP_DIR=/var/lib/webcam-uploader/tmp
WU_DUPLICATE_HASH_THRESHOLD=5
WU_MAX_CONCURRENT_FETCHES=5
WU_LOG_LEVEL=INFO
```

Everything else (cams, albums, storage targets, schedules, cookies, retention) is configured through the web UI and stored in SQLite.

The port can also be changed live from the Settings UI — the service then triggers its own restart and the browser is redirected to the new port automatically (no manual `systemctl restart` needed).

### HTTPS / reverse proxy

Recommended for anything reachable from the public internet. With Caddy:

```caddy
cam.example.com {
    reverse_proxy localhost:8080
}
```

---

## Operations

| Action | Command |
|---|---|
| Follow logs | `sudo journalctl -u webcam-uploader -f` |
| Restart | `sudo systemctl restart webcam-uploader` |
| Status | `sudo systemctl status webcam-uploader` |
| Edit config | `sudo nano /etc/webcam-uploader/.env` |
| Update | re-run `sudo bash webcam-uploader-install.sh` (idempotent) |
| Backup DB | from the web UI: Settings → Backup → Download |
| Restore DB | from the web UI: Settings → Restore → upload `.sqlite3` |
| Uninstall | see below |

#### Full uninstall

```bash
sudo systemctl disable --now webcam-uploader
sudo rm -rf /opt/webcam-uploader \
            /var/lib/webcam-uploader \
            /etc/webcam-uploader \
            /etc/systemd/system/webcam-uploader.service \
            /etc/sudoers.d/webcam-uploader
sudo userdel webcam-uploader
```

---

## Architecture

```
                  APScheduler — per-cam jobs
                  ┌──────────────────────────────┐
┌─────────────┐   │                              │
│   Cam #1    │───┘                              │
└─────────────┘                                  ▼
┌─────────────┐                          ┌─────────────────┐
│   Cam #2    │                          │  run_cam(id)    │
└─────────────┘─────────────────────────▶│                 │
┌─────────────┐                          │  fetch (httpx)  │
│   Cam #N    │                          │  ─ phash dedup ─┤
└─────────────┘                          │  ─ fan out ────┐│
                                         └─────────┬──────┘│
                                                   │       │
                                  ┌────────────────┼───────┴───────────┐
                                  ▼                ▼                   ▼
                          ┌──────────────┐  ┌──────────────┐   ┌──────────────┐
                          │ AmazonPhotos │  │  Local FS    │   │  S3 / SFTP   │
                          └──────────────┘  └──────────────┘   └──────────────┘

Per-fetch result: success / partial / duplicate / fetch_error / upload_error
Persistent counters live on `cams` so log-purge never resets dashboard stats.
```

### Where things live on disk

| Path | What |
|---|---|
| `/opt/webcam-uploader/` | Application code, venv |
| `/etc/webcam-uploader/.env` | Config |
| `/etc/sudoers.d/webcam-uploader` | NOPASSWD entry for UI-triggered restart |
| `/var/lib/webcam-uploader/webcam-uploader.sqlite3` | DB (cams, albums, fetches, users, targets, …) |
| `/var/lib/webcam-uploader/tmp/` | Temp images — deleted immediately after upload |
| `/var/lib/webcam-uploader/previews/` | 640px thumbnails + 1920px lightbox previews for the dashboard |
| `/etc/systemd/system/webcam-uploader.service` | Service unit |

---

## Data & Privacy

- Images live **only temporarily** under `/var/lib/webcam-uploader/tmp` and are removed after a successful upload.
- Dashboard previews (640px + 1920px) stay under `/var/lib/webcam-uploader/previews/`.
- The SQLite database is local-only; no telemetry, no remote calls except to your configured storage backends and the webcam sources themselves.
- Cookies for Amazon Photos are stored in the local DB — protect access to the server accordingly.

---

## Versioning

The project follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`):

- **MAJOR** — incompatible API / configuration changes
- **MINOR** — new, backwards-compatible features
- **PATCH** — backwards-compatible bug fixes

The version constant lives in `app/__init__.py` and is also rendered as a pill in the top-left of the web UI.

## Contributing

Issues and pull requests are welcome. For larger changes, please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE) © 2026 Carsten.
