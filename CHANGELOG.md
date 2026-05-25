# Changelog

Alle nennenswerten Änderungen an diesem Projekt sind hier dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
und das Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

## [0.11.0] – 2026-05-25

### Added
- **Server-seitiger Thumbnail-Cache fuer die Timelapse-Slideshow.** Jedes
  Frame wird beim Upload zusaetzlich als WebP 640x360 qual 80 unter
  `data_dir/thumbs/cam-{cam_id}/{upload_id}.webp` abgelegt (typisch 30-60 KB
  statt 1-3 MB Original-JPG). Der Slideshow-Player zieht standardmaessig
  die Thumbs, sodass FPS>1 jetzt fluessig laeuft. MP4-Rendering liest
  weiterhin die Originale, also keine Qualitaetsverschlechterung im Export.
- Neuer Modul `app/thumbnails.py` mit `ensure`, `generate`, `delete`,
  `evict_to_cap`, `prune_orphans`.
- Frame-Endpoint akzeptiert `?thumb=1` (lazy-generiert beim ersten Zugriff
  fuer historische Frames vor dem Upgrade). Antwort hat
  `Cache-Control: public, max-age=86400`.
- Frame-Listen-Endpoint liefert pro Frame zusaetzlich `thumb_url`.
- Neue Settings: `WU_THUMBNAIL_CACHE_MAX_MB` (default 500),
  `WU_THUMBNAIL_WIDTH` (640), `WU_THUMBNAIL_HEIGHT` (360),
  `WU_THUMBNAIL_WEBP_QUALITY` (80).

### Changed
- **Slideshow-Player** (`app/static/timelapse.js`): nutzt `thumb_url` statt
  Original-Frame. Lookahead PRELOAD_AHEAD von 5 auf 18 erhoeht und
  PRELOAD_CACHE_MAX von 60 auf 120, da Thumbs <50 KB pro Stueck.
  Faellt safe auf `url` zurueck, wenn der Server kein `thumb_url`
  liefert (Pre-v0.11.0-Server, alte Page-Cache).
- **Daily-Cleanup**: zusaetzlich Thumb-Orphan-Prune (Thumbs ohne lebende
  TargetUpload-Row) + LRU-Eviction wenn ueber Cap. Loggt
  `thumbnail cleanup: orphans=N evicted=N freed=K KB cache=K KB`.
- **Prune-Path** (`_prune_target_for_cam`): loescht bei einem Retention-Prune
  das zugehoerige Thumbnail mit.

### Files
- new `app/thumbnails.py` (250 Zeilen)
- modified `app/config.py` (Thumbnail-Settings + ensure_dirs)
- modified `app/scheduler.py` (ensure() nach Local-Upload, delete() im Prune,
  `_thumbnail_cleanup` im Daily)
- modified `app/main.py` (?thumb=1 + thumb_url)
- modified `app/static/timelapse.js` (thumb_url, Lookahead 18)
- modified `app/__init__.py` (0.11.0)
- modified `deploy.sh` (ssh -t fuer sudo-Passwort-Prompt auf .142)

### Notes
- Keine DB-Migration. Cache ist filesystem-basiert und idempotent.
- Bestehende Frames (vor v0.11.0 hochgeladen) bekommen ihren Thumb beim
  ersten Slideshow-Aufruf — der `?thumb=1`-Endpoint generiert lazy.

## [0.10.2] – 2026-05-25

### Fixed
- **Timelapse-Vorschau: Slideshow zeigt jetzt zuverlaessig die Bilder.**
  Bisher konnte es passieren, dass der Scrub-Balken mehrfach durchlief, ohne
  dass das angezeigte Frame wechselte. Zwei zusammenwirkende Ursachen:
  - **Race-Condition im Frame-Swap**: Bei hoeheren FPS (15+) wurde
    `<img>.src` ueberholt, bevor der Browser das vorige Bild dekodiert hatte.
    Der Decoder verwarf nicht mehr aktuelle Frames und das `<img>` blieb
    optisch stehen, obwohl die State-Variablen weiterliefen.
  - **CSS-Layout-Konflikt**: Das Empty-Overlay (`.tl-stage-empty`) war ein
    normales Flex-Item neben dem `<img>`, statt es zu ueberlagern. Je nach
    Zustand konnte das Image-Element auf 0px schrumpfen.
- **Loop am Ende einer Page**: bei `has_more=true` und letztem Frame der
  geladenen Page lief der Timer leer (immer nur `return`), bis Nachladen
  fertig war. Jetzt wird der Page-Load proaktiv getriggert und der Loop
  setzt erst aus, wenn wirklich nichts mehr nachkommt.

### Changed
- Neuer Slideshow-Player in `app/static/timelapse.js`:
  - **Preload-Cache** (LRU, max. 60 Eintraege) mit Lookahead PRELOAD_AHEAD=5
    Frames; jeder Eintrag durchlaeuft `img.decode()` und ist erst dann
    swap-bereit.
  - **rAF-basierter Tick** statt `setInterval` — robust gegen Browser-Throttling
    in inaktiven Tabs.
  - FPS-Slider greift jetzt sofort beim naechsten Tick, ohne Stop/Start des
    Timers.
- `.tl-stage-empty` ist jetzt `position:absolute; inset:0; pointer-events:none`
  — echtes Overlay, das das `<img>` nicht mehr verdraengt.

### Files
- modified `app/static/timelapse.js` (Komplett-Rewrite des Player-Teils)
- modified `app/static/style.css` (`.tl-stage img`, `.tl-stage-empty`)
- modified `app/__init__.py` (0.10.1 → 0.10.2)

## [0.10.1] – 2026-05-12

### Changed
- **Neues Logo + Favicon.** Das alte Kamera-Emoji im Header ist durch ein inline
  SVG-Iris-Icon (Kamera-Blende, 6-Wedge-Design in den App-Blautoenen) ersetzt.
  Gleicher Brand-Mark dient jetzt auch als SVG-Favicon
  (`/static/favicon.svg`) — der Browser-Tab zeigt damit ein echtes App-Icon
  statt des generischen Platzhalters. Skaliert sauber bis 16x16.
- Die fruehere Gradient-Box mit Glow um das Logo ist entfallen — das neue
  Logo steht naked und hat seine eigene Komposition.

### Files
- new `app/static/favicon.svg`
- `app/templates/base.html` (inline-SVG-Logo, `<link rel=icon>`)
- `app/static/style.css` (`.brand .logo` Stil reduziert)

## [0.10.0] – 2026-05-12

### Foundation laid (this commit)
- **Design-Dokument** `docs/timelapse-design.md` mit kompletter Architektur:
  Tab-basierte Cam-Detail-Page, Browser-Diashow + ffmpeg-MP4-Export,
  Best-of-Day-Modus, Retention/Cache-Cap, DB-Schema, Edge-Cases.
- **Neue DB-Tabelle `timelapse_jobs`** plus neue Cam-Spalte
  `timelapse_source_target_id`. Idempotente Migration
  `_migrate_add_timelapse()` analog zu den bestehenden v0.7.x/v0.8.x Migrationen.
- **Neue Settings-Felder** `WU_TIMELAPSE_CACHE_MAX_GB` (default 5),
  `WU_TIMELAPSE_RETENTION_PER_CAM` (default 10),
  `WU_TIMELAPSE_WORKER_INTERVAL_S` (default 5). Neue Property
  `settings.timelapse_dir = /var/lib/webcam-uploader/timelapse/`,
  wird via `ensure_dirs()` beim Startup angelegt.
- **install.sh** installiert ab v0.10.0 zusaetzlich `ffmpeg`
  (~20 MB extra auf Debian 12 / Ubuntu 22+).

- **Backend-Modul `app/timelapse.py`** (678 LOC):
  - `list_frames()` — reine SQL-Query auf `target_uploads` JOIN `fetches` mit
    Filter cam_id + source_target_id + status='success' + pruned_at IS NULL,
    plus optionalen Wochentag-/Tageszeit-Filtern (Python-seitig).
  - `pick_source_target()` — waehlt das Storage-Target aus, von dem gelesen
    wird: cam.timelapse_source_target_id falls gesetzt, sonst erstes aktives
    local-Target der Cam.
  - `best_of_day_filter()` — Pillow-basierter Helligkeits-Filter, behaelt pro
    Kalendertag das hellste Bild (100x100-Downscale, ImageStat.mean).
  - `enqueue_job()` + `worker_tick()` + `_process_job()` — Job-Queue auf der
    `timelapse_jobs`-Tabelle. Worker verarbeitet hoechstens einen pending Job
    pro Tick, isoliert die Render-Zeit von der DB-Session.
  - `ffmpeg_render()` — Symlink-basierte Sequenz, libx264/yuv420p, faststart,
    drawtext-Overlay mit Cam-Name + Zeitraum-Label, progress-Parsing via
    `-progress pipe:1`. Resolution-Presets: original / 1080p / 720p.
  - `cleanup_cache()` — beidseitige Retention (Pro-Cam + Global-GB-Cap),
    Daily-Job. Evicted Files: output_path -> NULL, DB-Row bleibt fuer UI.
- **Scheduler-Integration** (`app/scheduler.py`):
  - Neuer Job `__timelapse_worker__` triggert `worker_tick()` alle
    `WU_TIMELAPSE_WORKER_INTERVAL_S` Sekunden (default 5s).
  - Der Log-Retention-Daily-Job wurde in `_daily_cleanup()` zusammengefasst
    und ruft jetzt auch `timelapse.cleanup_cache()` auf.

### UI layer (this commit)
- **Cam-Detail-Page `/cams/{id}`** mit Tab-Navigation
  (Vorschau / Timelapse / Logs / Bearbeiten). Tabs werden ueber URL-Hash gewaehlt,
  Default-Tab ist Vorschau. `/cams/{id}/edit` 302-redirected auf `/cams/{id}#bearbeiten`
  fuer Bookmark-Kompatibilitaet.
- **Vorschau-Tab:** Aktuelle Cam-Preview (Klick = Full-Size-Lightbox), Lifetime-Counter
  (ok/dup/err), Zeitfenster, Wochentage, alle aktiven Storage-Targets als Pills.
- **Timelapse-Tab:**
  - Source-Picker mit Live-Anzeige der verfuegbaren Frames + Zeit-Range pro Target.
  - Date-Range-Form (Von/Bis, Wochentage, Tageszeit-Fenster, Best-of-Day-Toggle).
  - Browser-Slideshow-Player: lazy-loaded JSON-Chunks (1000 Frames/Page), automatisches
    Nachladen ab 80% Page-Progress, FPS-Slider, Scrub-Bar, Tastatur-Shortcuts
    (Space=Play/Pause, ◀/▶=Step).
  - MP4-Render-Form: Resolution-Preset (1080p/720p/Original), Render-FPS, Submit
    triggert `POST /api/cams/{id}/timelapse/render`. 2s-Polling auf den Job-Status,
    nach Erfolg automatischer Tabellen-Reload.
  - Job-Tabelle mit Status-Pills, Download-Button, Loesch-Button (mit Confirm-Dialog).
  - Info-Karte falls die Cam kein Local-Target hat (Link zu `/storage`).
- **Logs-Tab:** Embedded Liste der letzten 50 Fetches dieser Cam mit Per-Target-Status-
  Pills. Tiefer-Einstieg via "Alle Logs öffnen →" Button.
- **Bearbeiten-Tab:** Existierendes Cam-Form, refactored in das neue Partial
  `app/templates/_cam_form.html` damit es sowohl in `cam_form.html` (Neu-Anlage)
  als auch im Detail-Tab embedded werden kann. Leaflet-Map fuer Geo-Picker bleibt
  unveraendert.
- **Settings-Sub-Sektion "Timelapse-Cache":**
  Zeigt die aktuelle Auslastung (Summe der gerenderten Bytes + Render-Count).
  Inputs fuer die DB-Overrides `timelapse_cache_max_gb` und
  `timelapse_retention_per_cam`. Leerlassen = env-Defaults aus `.env` verwenden.

### Routes (this commit)
- `GET  /cams/{id}` — Tab-basierte Detail-Page.
- `GET  /api/cams/{id}/timelapse/frames` — paginierte JSON-Frame-Liste mit Filtern
  (from/to/source_target_id/weekdays/time_start/time_end/page/page_size).
- `GET  /api/cams/{id}/timelapse/frame/{upload_id}` — JPEG-Stream eines Frames,
  validiert cam-Zuordnung und `pruned_at IS NULL`. Cache-Header fuer Browser-Cache.
- `POST /api/cams/{id}/timelapse/render` — Job-Enqueue. Validiert Source-Target,
  FPS-Clamp, Resolution-Preset. 503 wenn ffmpeg fehlt.
- `GET  /api/timelapse/jobs/{job_id}` — Polling-Endpoint mit `progress_pct`,
  `frame_count`, `bytes`, `duration_s`, `download_url` wenn done.
- `GET  /api/timelapse/jobs/{job_id}/download` — MP4-Download.
- `POST /api/timelapse/jobs/{job_id}/delete` — Job + Output-File loeschen.
- `POST /settings/timelapse_cache` — DB-Overrides fuer Cache-Cap + Per-Cam-Retention.

## [0.9.3] – 2026-05-11

### Added
- **Automatischer Service-Restart nach Port-Aenderung.** Statt nach dem Speichern
  manuell `sudo systemctl restart webcam-uploader` aufrufen zu muessen, stoesst die
  App den Restart jetzt selbst an und der Browser wird automatisch zum neuen Port
  umgeleitet.
  - **install.sh** legt eine sudoers-Datei `/etc/sudoers.d/webcam-uploader` an,
    die dem Service-User passwordless `systemctl restart webcam-uploader` erlaubt
    (NOPASSWD, nur dieser eine Befehl). Wird via `visudo -c` validiert; bei Fehler
    sofort wieder entfernt.
  - **`/settings/port`** schreibt die .env wie gewohnt, startet danach einen
    detached Background-Prozess `sleep 2 && sudo -n systemctl restart webcam-uploader`.
    Die 2-Sekunden-Verzoegerung gibt der HTTP-Response Zeit, den Browser zu erreichen,
    bevor der Service den Socket schliesst. `start_new_session=True` loest den
    Subprozess vom Parent, damit er den Service-Stop ueberlebt.
  - **UI:** Banner zeigt jetzt einen 8-Sekunden-Countdown und einen "Jetzt umleiten"-
    Button. Nach Ablauf erfolgt automatisch `window.location.replace()` auf
    `http://&lt;hostname&gt;:&lt;neuer-port&gt;/settings`. Der Server-Hostname wird vom
    Browser uebernommen — funktioniert also egal ob via IP, lokalem Namen oder DNS.
  - **Fallback:** Wenn der Subprocess-Start scheitert (z.B. weil die sudoers-Datei
    fehlt — was bei Installationen vor v0.9.3 der Fall ist), zeigt die UI weiterhin
    die manuelle Anleitung. Klar getrennt ueber die zwei Flags `port_restart_X`
    (auto-Restart eingeplant) und `port_saved_X` (manueller Restart noetig).

## [0.9.2] – 2026-05-11

### Added
- **Port via UI änderbar.** Neue Sektion „Netzwerk &amp; Port" auf der Settings-Seite.
  Schreibt `WU_PORT` direkt in die `/etc/webcam-uploader/.env` (sofern Service-User
  Schreibrechte hat — was install.sh standardmaessig setzt). Ports 1-1023 sind aus
  Sicherheitsgruenden blockiert (Ausnahme: 80, 443).
  Nach Speichern: Hinweis-Banner mit Kopier-Kommando fuer den noetigen Service-Restart.
- Backend-Helpers `_read_env_var(name)`, `_write_env_var(name, value)` und
  `_env_file_status()` (zeigt Pfad, exists, writable, aktueller Port/Host).
- Settings-Klasse hat eine neue Property `env_file_path` (default `/etc/webcam-uploader/.env`),
  per Env-Var ueberschreibbar fuer abweichende Installationen.
- Neue Route `POST /settings/port` mit Validierung (Integer-Check, Range 1024-65535
  + 80/443-Ausnahme).

### Changed
- **Settings-Seite UI komplett neu aufgebaut.**
  - **Sticky Sidebar-Nav** links mit Anker-Links zu allen Sektionen (Scroll-Highlight zeigt
    aktuelle Position).
  - **Karten mit Header-Bar** statt nur Ueberschriften: Icon, Titel, Kurzbeschreibung.
  - **Konsistente Section-Reihenfolge:** System-Status, Mein Passwort, Netzwerk, User,
    Log-Aufbewahrung, Backup, Amazon Cookies, Wartung.
  - **Neue „kv-list"-Komponente** fuer Status-Daten (Service-Start, DB-Groesse, Pfade...).
  - **Form-Tight-Variante** ohne doppelten Karten-Hintergrund.
  - **Mobile:** Sidebar wird zur horizontal scrollbaren Pill-Reihe.

## [0.9.1] – 2026-05-11

### Added
- **Dashboard: Drag&Drop-Sortierung fuer Cam-Karten.** Analog zu Webcams- und Alben-Seite gibt
  es jetzt einen Drag-Handle (`⋮⋮`-Icon in der linken oberen Ecke jeder Karte). Beim Ziehen
  zeigt sich ein blauer Indikator an der linken/rechten Kante der Ziel-Karte und die Position
  springt direkt mit. Speichert ueber die bestehende `POST /api/cams/reorder`-Route, sodass
  die Sortierung mit der `/cams`-Seite (und umgekehrt) **vollstaendig synchron** ist —
  Dashboard und Webcams-Liste zeigen immer dieselbe Reihenfolge.
- Handle ist nur waehrend des Hovers ueber der Karte sichtbar (Opacity 0/0.85), auf Touch-
  Geraeten (`@media (hover: none)`) dauerhaft eingeblendet.

## [0.9.0] – 2026-05-11

### Added
- **Mobile-Layout (responsive Design).** Greift unter 768px (Tablet/Phone) bzw. 480px (Phone)
  und laesst das Desktop-Layout (>=768px) komplett unveraendert. Umgesetzt rein per CSS-Media-Queries
  und ein kleines vanilla-JS-Snippet fuer das Burger-Menue — keine zusaetzlichen Frameworks.
  - **Topbar: Burger-Menue** auf Mobile. Brand bleibt sichtbar; sechs Nav-Links werden hinter
    einem ☰-Button versteckt und klappen vertikal aus. Animation des Buttons zu × bei „offen".
    Versions-Pill in der Brand wird auf Mobile ausgeblendet (spart Platz).
  - **Stats-Boxen 2x2 auf Tablet, 1x4 auf Phone** (statt der Desktop-Vierer-Reihe).
  - **Storage-Chips** kompakter (kleinere Schrift, kuerzere Fehlermeldungen).
  - **Forms 1-spaltig** auf Mobile (Cam-Form, Storage-Form, Cookie-Felder) — `.form-grid`
    und `.cookie-grid` schalten von 2 auf 1 Spalte um.
  - **Cam-Grid 1-spaltig** auf Mobile, Karten in voller Breite.
  - **Logs-Toolbar** stackt vertikal: Filter-Form, Reset und Loeschen-Button untereinander.
  - **Tabellen horizontal scrollbar.** Alle Tabellen-Templates (Cams, Albums, Storage, Logs,
    Dashboard-Recent) wurden in einen `<div class="table-wrap">`-Container gepackt. Auf Mobile
    bekommt der Wrapper `overflow-x: auto` mit Touch-Scrolling — die Spaltenstruktur bleibt
    erhalten statt zerquetscht zu werden, der Nutzer scrollt einfach horizontal.
  - **Lightbox** auf Mobile mit etwas mehr Outer-Padding, X-Button auf 40px vergroessert,
    Bild-max-height auf 80vh.
  - **Container/Topbar-Padding** kompakter auf Mobile (14px statt 24px, auf <=480px 10px).

## [0.8.9] – 2026-05-11

### Changed
- **Lightbox-UX-Politur:**
  - **zoom-hint wieder klein.** Statt der grossen „vergroessern"-Pille jetzt wieder ein kompakter
    runder „+"-Knopf (24x24px) rechts unten in der Cam-Preview.
  - **Schliessen-X immer im Viewport.** Der `lightbox-close`-Button hing zuvor `top: -40px`
    ueber der Bild-Box — bei hochaufloesenden Bildern (`max-height: 92vh`) wurde er aus dem
    sichtbaren Bereich geschoben. Jetzt sitzt er als runder, halbtransparenter Button mit
    Blur-Hintergrund **innerhalb** der Box (`top: 8px right: 8px`) und ist auf jeder Aufloesung
    erreichbar.
  - **Klick aufs Bild schliesst die Lightbox.** Statt jedes Mal das X zu treffen, schliesst
    jetzt jeder Klick irgendwo in der Lightbox (auch direkt aufs Bild) sie sofort. Der
    Cursor zeigt das per `cursor: pointer` an.

## [0.8.8] – 2026-05-11

### Fixed
- **Logs loeschen hat das Dashboard zurueckgesetzt.** Ursache: alle Dashboard-Zahlen
  (Erfolge, Duplikate, Fehler, Uploads gesamt pro Cam) wurden direkt aus der
  `fetches`-Tabelle gerechnet. Wer ueber den neuen „Loeschen"-Button die Logs leert,
  hat damit unfreiwillig auch alle Counter genullt. Fix: persistente Lifetime-Counter
  pro Cam (`total_uploads`, `total_duplicates`, `total_errors`), die beim Fetch
  inkrementiert werden und vom Log-Loeschen nicht angetastet werden.

### Added
- **Cam-Tabelle: drei persistente Counter-Spalten** (`total_uploads`, `total_duplicates`,
  `total_errors`). Default 0, NOT NULL.
- **DB-Migration `_migrate_add_cam_counters`** legt die Spalten an und backfilled die
  Werte beim ersten Start nach Upgrade aus der bestehenden `fetches`-Tabelle —
  bestehende Zahlen bleiben damit erhalten.
- **Scheduler `_bump_cam_counter(cam, status)`** erhoeht den entsprechenden
  Cam-Counter nach jedem Fetch. Aufgerufen in allen vier Endzustaenden des
  `run_cam()`-Flows: Duplicate, Erfolg/Partial, FetchError, generelle Exception.

### Changed
- **Dashboard-Stats-Boxen umbenannt:**
  - „Erfolge (24h)" → **„Erfolge (gesamt)"** — aus `SUM(cam.total_uploads)`
  - „Duplikate (24h)" → **„Duplikate (gesamt)"** — aus `SUM(cam.total_duplicates)`
  - „Fehler (24h)" → **„Fehler (gesamt)"** — aus `SUM(cam.total_errors)`
  - „Uploads gesamt" → **„Aktivität (24h)"** — Anzahl Fetch-Eintraege der letzten 24h
    (vergaenglich, Fetch-basiert — Hauptanker ist jetzt die persistente Spalten oben).
- „Uploads gesamt"-Zahl auf den Cam-Cards kommt jetzt ebenfalls aus `cam.total_uploads`
  statt aus einer Fetch-Summen-Query.

## [0.8.7] – 2026-05-11

### Fixed
- **Lightbox liess sich nicht oeffnen** und das Vorschaubild blieb klein. Der zoom-in-Cursor
  ("Browser-Lupe mit +") suggerierte zwar eine Klick-Affordance, der Klick selbst hat aber
  oft nichts ausgeloest. Behebung in mehreren Schritten:
  - `<a class="preview" href="#" onclick="…">` durch `<button type="button" class="preview js-open-lightbox">`
    ersetzt. Buttons triggern kein implizites Browser-Default-Verhalten und keine Hash-Navigation.
  - **Inline onclick durch Event-Delegation** ersetzt: ein einziger `document.addEventListener('click',…)`
    haengt sich an `.js-open-lightbox` (via `closest`). Das funktioniert robust auch dann, wenn der
    Klick technisch das innere `<img>` oder den `zoom-hint`-Span trifft.
  - **Cursor von `zoom-in` auf `pointer` geaendert** — der Lupen-mit-Plus-Cursor war optisch leicht
    mit dem Browser-Eigentool zu verwechseln (Screenshot war so unmoeglich, weil der OS-Cursor
    permanent im Bild war).
  - `localStorage`-Zugriffe (Compact-Toggle) jetzt try/catch-umschlossen — falls Cookies/Storage
    blockiert sind, crasht der ganze Script-Block nicht mehr und die Lightbox bleibt verfuegbar.
  - `.zoom-hint`-Pille umgestaltet: heisst jetzt „vergroessern" (statt nur 🔍), groesseres Klick-Target,
    immer leicht sichtbar (Opacity 0.85) statt nur on-hover — das macht die Klick-Affordance
    auf den ersten Blick klar.

## [0.8.6] – 2026-05-11

### Fixed
- **Dashboard-Lightbox zeigte nur kleines Bild.** Ursache: `save_preview()` hat das Vorschaubild
  hart auf max. 640px Kantenlaenge gedeckelt — bei einer Lightbox-Anzeige mit 92vh wurde dieses
  640er-Bild hochskaliert (also nicht "groesser", sondern nur unschaerfer). Behebung: `save_preview()`
  schreibt jetzt **zwei** Dateien: `cam{id}.jpg` (640px Thumb fuer das Grid) und
  `cam{id}_full.jpg` (max. 1920px fuer die Lightbox). Neue Route `GET /preview/cam/{id}/full`
  liefert das grosse Bild — mit Fallback auf den Thumb, falls die Full-Variante (z.B. fuer Cams
  die seit dem Upgrade noch nicht erneut gefetcht wurden) noch nicht existiert.
- Dashboard-Template laedt die Lightbox jetzt von `/preview/cam/{id}/full`.

### Added
- **Logs-Seite: „Loeschen"-Button** in der Toolbar neben Filter/Reset. Loescht Fetch-Eintraege
  (und zugehoerige TargetUpload-Eintraege ueber Bulk-Delete-mit-IDs). Respektiert den aktuellen
  Filter (cam_id / status) — wenn ein Filter aktiv ist, werden nur die gefilterten Eintraege
  geloescht; ohne Filter alle. Confirm-Dialog warnt vor unbeabsichtigtem Massenloeschen und
  zeigt die genaue Anzahl der betroffenen Eintraege an.
- Logs-Endpoint liefert jetzt zusaetzlich `total_matching` (Anzahl Treffer **vor** LIMIT)
  und nimmt ein optionales `flash`-Query-Param fuer Erfolgsmeldungen nach dem Loeschen entgegen.
- Neue CSS-Klasse `.logs-toolbar` (Filter-Form links, Loeschen rechts).

## [0.8.5] – 2026-05-11

### Added
- **Alben-Seite: Drag&Drop-Sortierung** analog zur Webcams-Seite. Drag-Handle (`⋮⋮`) in jeder Zeile,
  die Reihenfolge wird per `POST /api/albums/reorder` mit der neuen Liste an die DB durchgereicht;
  Pfeil-Buttons (▲/▼) bleiben als Fallback erhalten.
- **Dashboard: kompakte Multi-Storage-Status-Leiste** statt grossem Amazon-Banner. Alle aktivierten
  Speicherziele werden als Status-Chips (Pill mit farbigem Dot) angezeigt: Typ-Label, Target-Name,
  Status (ok/warn/err) und ggf. Fehlermeldung. Chips sind klickbar (Amazon → `/settings`,
  Rest → `/storage`). Neue Helper-Funktion `_collect_storage_status()` ruft pro Target
  `backend.health_check()` auf — kein Netzwerk-Call, daher schnell.

### Changed
- **Dashboard: Lightbox vergroessert** — `max-height: 92vh` (statt 85vh), Container `max-width: 98vw`.
- **Dashboard: Hover-Affordance** auf Cam-Previews (Zoom-Lupe rechts unten, Bild wird minimal heller),
  damit klar ist, dass das Bild durch Klick gross angezeigt werden kann.
- **Header (Topbar) aufgepeppt** — zurueckhaltend:
  - Logo bekommt einen abgerundeten, leicht leuchtenden Gradient-Container (34x34px).
  - Titel „Webcam-Uploader" mit subtilem Weiss-zu-Hellblau-Gradient (background-clip: text).
  - Versions-Badge als kleine, blaue Pill statt nur schwach gegrauter Text.
  - Topbar selbst mit dezentem Vertikal-Gradient und feinem Box-Shadow.

## [0.8.4] – 2026-05-11

### Added
- **Cookie-Setup-Assistent** auf der Settings-Seite — macht das Cookie-Refresh deutlich angenehmer:
  - **Sieben Einzelfelder** statt JSON-Textarea, eines pro erwarteten Cookie (`at-acbde`, `ubid-acbde`, `session-id`, `session-token`, `sst-acbde`, `sess-at-acbde`, `x-acbde`). Pflichtfelder mit *, optionale ohne.
  - **Vorbefuellung** aus bisher gespeicherten Cookies – beim Refresh muessen nur abgelaufene Werte ersetzt werden.
  - **Live-Status** pro Cookie („✓ gesetzt" / „✗ fehlt"). Sofort erkennbar, was noch reinmuss.
  - **„Werte anzeigen/verbergen"-Button** zum Sichtbarmachen der Cookie-Werte.
  - **DevTools-Tabelle-Paste**: Cookies in DevTools (Application → Cookies → amazon.de) markieren, kopieren, in Textarea einfuegen, Button druecken → alle Felder werden automatisch befuellt. Parser akzeptiert Tab-Separated- oder Mehrfach-Space-getrennte Spalten.
  - **JSON-Import**: alternativ kann ein komplettes JSON-Blob eingefuegt werden.
  - **Console-Snippet**: kurzes JavaScript-Snippet zum Auslesen nicht-HttpOnly-Cookies aus der Browser-Console.
  - **Erweitert-Bereich**: Legacy-JSON-Textarea fuer Power-User bleibt verfuegbar (ueberschreibt alle Einzelfelder).
- Backend (`settings_save`): merget Einzelfelder mit bestehenden Cookies; leere Felder lassen alten Wert unveraendert (Teil-Updates moeglich).

### Changed
- `/settings/save`-Route verlangt jetzt explizit `require_admin` (vorher uneindeutig).

## [0.8.3] – 2026-05-11

### Fixed
- **Cookies-UX-Bug**: Beim Speichern der Amazon-Cookies wurden bestehende Cookies auch dann ueberschrieben, wenn das Cookie-Textarea leer war. Damit hat ein "Speichern" ohne Cookie-Eingabe die alten Cookies geloescht – obwohl der Hilfetext sagt "Leer lassen, um nichts zu aendern". Backend wurde an den Hilfetext angepasst.

### Added
- **401-Skip-Mode** im Amazon-Backend: Wenn Amazon mit `401 Cookies expired` antwortet, werden Amazon-Uploads fuer **15 Minuten** komplett uebersprungen statt fuer jede Cam dreimal mit exponentialem Backoff zu retryen. Spart pro Cam ~40 Sekunden Wartezeit; Service bleibt responsiv auch waehrend abgelaufener Cookies.
- **Auffaelliger Fehler-Banner** auf dem Dashboard bei abgelaufenen Cookies (rot statt orange), mit direktem Link zu `/settings`.
- Skip-Mode endet automatisch nach 15 Min, oder sofort bei `reset_client()` (also wenn neue Cookies via UI gespeichert werden).

## [0.8.2] – 2026-05-11

### Added
- **Logout-Button** in der Top-Navigation („⎋ Abmelden"). Da HTTPBasic-Auth keine echte Server-Session hat, wird der Browser-Cache via JS-Trick invalidiert (XHR mit absichtlich invaliden Credentials zu einer geschuetzten Route). Funktioniert in allen modernen Browsern; bei hartnaeckigen Caches gibt die Logout-Seite den Hinweis, das Fenster zu schliessen oder ein Inkognito-Fenster zu nutzen.
- Neue oeffentliche Route `GET /logout` mit eigenem Template `logout.html`.

## [0.8.1] – 2026-05-11

### Fixed
- **Service-Crash beim ersten Start auf Debian-Server**: `passlib.hash.bcrypt.hash(...)` warf `ValueError: password cannot be longer than 72 bytes`. Bekannte Inkompatibilitaet zwischen `passlib 1.7.x` und `bcrypt >= 4.0` — passlib macht intern einen Self-Test mit zu langem Passwort, der in bcrypt 4.x crasht statt zu truncaten.
- Fix: Neuer Helper `app/security.py` mit `hash_password()` / `verify_password()` direkt via `bcrypt`-Library, ohne passlib-Wrapper. Truncate auf 72 Bytes proaktiv im Helper. `db.py`, `auth.py`, `main.py` umgestellt.

## [0.8.0] – 2026-05-11

### Added
- **Multi-User-Authentifizierung mit Rollen**: Neue `users`-Tabelle mit bcrypt-Hashes. Rollen `admin` (alles) und `viewer` (nur lesen). Bei erstem Start wird der `.env`-User automatisch als Admin uebernommen.
- `.env` bleibt als **Notfall-Fallback** aktiv – falls die DB-Tabelle z.B. nach einem fehlgeschlagenen Restore leer ist, kommt man trotzdem rein.
- Settings-Seite mit **User-Verwaltung**: Anlegen, Passwort setzen, Rolle aendern (Dropdown), Loeschen. Schutz gegen Aussperren – letzter Admin kann nicht degradiert oder geloescht werden.
- Jeder eingeloggte User (auch Viewer) kann sein **eigenes Passwort** aendern.
- Neue **System-Status-Box**: Anzahl Webcams/Speicherziele/Fetches/User, Service-Uptime, letzter Fetch, DB-Groesse, freier Plattenplatz unter `data_dir`.
- **Log-Retention**: in den Settings konfigurierbar (Tage). Daily-Cleanup-Job loescht alte `fetches` (CASCADE auch `target_uploads`).
- **Backup**: `GET /settings/backup` liefert die SQLite-DB als Download mit Zeitstempel-Dateinamen.
- **Restore**: `POST /settings/restore` akzeptiert SQLite-Datei, validiert Magic-Bytes, sichert die aktuelle DB als `.prerestore`, ersetzt. Service-Restart erforderlich (Hinweis in UI).

### Changed
- `auth.py` komplett umgebaut: `current_user` liefert User-Objekt, `require_auth` / `require_admin` als FastAPI-Dependencies.
- Alle Schreib-Operationen (`POST`-Routes) verlangen Admin-Rolle; Lese-Operationen reichen `require_auth`.
- Settings-Seite zeigt fuer Viewer eingeschraenkte UI (User-Verwaltung, Cookie-Bearbeitung, Retention, Backup nur fuer Admins sichtbar).

## [0.7.8] – 2026-05-10

### Added
- **Drag&Drop-Sortierung** auf der Seite „Webcams": neben den ▲/▼-Buttons jetzt ein ⋮⋮-Griff in der Reihenfolge-Spalte. Per Maus eine Zeile greifen, an die gewuenschte Position ziehen, fertig. Visuelle Indikatoren beim Hover (oben/unten der Ziel-Zeile), optimistic UI (DOM verschiebt sofort), Server-Speicherung im Hintergrund.
- Neue API `POST /api/cams/reorder` mit `{ids: [...]}` – setzt sort_order auf 1..N in der gelieferten Reihenfolge.

## [0.7.7] – 2026-05-10

### Changed
- Webcams-Liste: Wochentage-Spalte ist jetzt einzeilig. Die sieben Tag-Buchstaben (Mo Di Mi Do Fr Sa So) werden als kompakte Mini-Pillen nebeneinander dargestellt — aktive Tage gruen, inaktive dezent grau. Tabellen-Zeile wird dadurch etwa halb so hoch, alle Infos bleiben auf einen Blick erkennbar.

## [0.7.6] – 2026-05-10

### Fixed
- Sortier-Buttons (▲/▼) auf den Seiten „Webcams" und „Alben" funktionierten nicht, wenn mehrere Eintraege denselben `sort_order` hatten (typisch: alle neuen Cams haben default-Wert 0). Die alte Logik suchte mit `WHERE sort_order < X` nach dem Vorgaenger und fand nichts, wenn alle Werte gleich waren. Fix: vor jedem Swap werden alle Eintraege in der aktuellen Display-Reihenfolge (sort_order, name) auf 1..N normalisiert, dann ganz normal mit dem Vorgaenger / Nachfolger getauscht. Robust gegen alle Zustaende.

## [0.7.5] – 2026-05-10

### Changed
- Alle in der UI angezeigten Zeiten (Dashboard, Logs, Storage-Targets, Lightbox-Caption) werden jetzt in **lokaler Serverzeit** dargestellt statt UTC. Intern bleibt alles UTC (`datetime.utcnow()`), die Konvertierung passiert via neuem Jinja-Filter `localfmt(fmt)` (`app/template_filters.py`), der `datetime.astimezone()` mit System-Timezone nutzt. Auf einem Debian-Server mit `Europe/Berlin` zeigt also „21:35" statt „19:35".

## [0.7.4] – 2026-05-10

### Added
- **Retention-Policy pro Speicherziel und Webcam**: neues Feld „Max. Anzahl Bilder pro Webcam" (0 = unbegrenzt). Nach jedem erfolgreichen Upload werden ueberzaehlige aeltere Bilder automatisch geloescht.
- Backend-Interface `StorageBackend.delete(remote_ref)`. Implementiert in local/sftp/s3 (Amazon: no-op, hat keine sinnvolle Lösch-API).
- Local und SFTP raeumen leere Eltern-Verzeichnisse beim Loeschen mit auf.
- Neue DB-Spalten `storage_targets.retention_per_cam`, `target_uploads.cam_id` (denormalisiert fuer Retention-Query) und `target_uploads.pruned_at`. Idempotente Migration.

### Changed
- Scheduler ruft nach jedem erfolgreichen Upload `_prune_target_for_cam()` auf; das Pruning protokolliert die Anzahl geloeschter Eintraege ins Service-Log.

## [0.7.3] – 2026-05-10

### Added
- **Filesystem-Browser fuer Local-Backend**: beim Anlegen eines Speicherziels vom Typ „Lokales Verzeichnis" gibt es im Feld „Basis-Verzeichnis" jetzt einen „📁 Durchsuchen"-Button. Modal zeigt Unterverzeichnisse, Up-Navigation, Schreibbar-Status, und kann via „+ Ordner" neue Verzeichnisse anlegen.
- Neue API-Endpunkte `GET /api/fs/browse?path=...` (listet nur Verzeichnisse, keine Dateien) und `POST /api/fs/mkdir` – HTTPBasic-geschuetzt, nur fuer authentifizierte User.

## [0.7.2] – 2026-05-10

### Added
- Relative Zeitangabe auf Dashboard-Karten: hinter "10.05. 19:23" steht jetzt zusaetzlich "(vor 12 Min.)" / "(vor 2:35 Std.)" – server-seitig via neuem Jinja-Filter `rel` (`app/template_filters.py`)

### Fixed
- CSS-Block fuer Compact-Modus (`Details ausblenden`) blendete die in v0.7.0 hinzugefuegten `.cam-targets`-Pills nicht aus – der Toggle hatte dadurch wenig sichtbaren Effekt und wirkte "kaputt". Selector um `.cam-targets` erweitert.
- `style.css` wurde wegen Sync-Bug ebenfalls in v0.7.0/0.7.1 truncated geliefert; komplett neu via Bash-Heredoc geschrieben (inkl. `.pill.status-partial`, `.row-partial`, `.storage-target-list`).

## [0.7.1] – 2026-05-10

### Fixed
- Hotfix für v0.7.0: `dashboard.html`, `base.html`, `cam_form.html`, `logs.html` waren beim Bundle-Build truncated (Sync-Bug zwischen Edit-Tool und Linux-Mount). Service-Crash beim Aufruf von `/dashboard` mit `jinja2.exceptions.TemplateSyntaxError: Unexpected end of template`. Templates neu via Bash-Heredoc geschrieben, Bundle neu gebaut.

## [0.7.0] – 2026-05-10

### Added
- **Multi-Storage-Backend-System** – Bilder können parallel an mehrere Ziele hochgeladen werden (M:N Cam↔Target)
- Neues Storage-Backend „Lokales Verzeichnis" (`local`) – schreibt Bilder auf das Filesystem des Servers
- Neues Storage-Backend „SFTP/SSH" (`sftp`) mit `paramiko` – Passwort- **und** SSH-Key-Auth, rekursive Verzeichnis-Anlage, Known-Hosts-Strict-Mode optional
- Neues Storage-Backend „S3-kompatibel" (`s3`) mit `boto3` – AWS, Backblaze B2, Cloudflare R2, MinIO, Wasabi, Hetzner Object Storage etc. via Custom-Endpoint
- Pfad-Templates pro Storage-Target frei konfigurierbar (`{cam_slug}/{Y}-{m}-{d}/{H}-{M}-{S}{ext}` u.v.m.)
- Neue UI-Seite „Speicherziele" (`/storage`) – Anlegen, Bearbeiten, Aktivieren/Deaktivieren, Verbindungs-Test, Löschen
- Sofortiger Verbindungs-Test beim Speichern eines Storage-Targets; bei Fehler wird nicht gespeichert. Optionaler „Speichern ohne Test"-Button
- Cam-Formular: Multi-Select „Speicherziele" zur parallelen Zuweisung
- Dashboard zeigt pro Cam die zugewiesenen Target-Typen als Pills
- Logs-Seite zeigt pro Fetch die Ergebnisse jedes einzelnen Storage-Targets
- Neuer Fetch-Status `partial` – wenn ≥1 Ziel erfolgreich, ≥1 Ziel fehlgeschlagen
- DB-Auto-Migration: bestehende Cams werden einem Default-Target „Amazon Photos" zugewiesen, sodass das bisherige Upload-Verhalten nahtlos weiterläuft

### Changed
- `scheduler.run_cam()` iteriert über alle aktiven Storage-Targets einer Cam, sammelt Ergebnisse, aggregiert zu Fetch-Status (`success` / `partial` / `upload_error`)
- Dashboard-Statistiken zählen `partial` zusätzlich zu `success` als „Upload"
- Cams ohne explizit zugewiesene Targets fallen automatisch auf das erste aktive `amazon`-Target zurück (Sicherheits-Net nach Migration)

### Fixed
- Logs-Filter um neue Status-Werte `partial` erweitert

## [0.6.0] – 2026-05-10

### Added
- Pro-Cam einstellbare Duplicate-Hash-Schwelle (überschreibt globalen `WU_DUPLICATE_HASH_THRESHOLD`)
- Sortier-Buttons (▲/▼) für Webcams – Reihenfolge wirkt sich auf Dashboard und Cams-Liste aus
- Sortier-Buttons (▲/▼) für Alben – Reihenfolge wirkt nur in der Alben-Übersicht
- Toggle „Details einblenden / ausblenden" auf dem Dashboard, persistiert über `localStorage`
- Self-Heal-Job: alle 30 Min läuft `sync_jobs()` automatisch, holt verlorene Cam-Jobs zurück
- Map-Picker auf der Cam-Bearbeiten-Seite (Leaflet + OpenStreetMap, mit Ortssuche via Nominatim)
- Lightbox-Vollbildansicht beim Klick auf das Webcam-Vorschaubild im Dashboard
- Pro-Cam-Upload-Counter unterhalb des Status auf jeder Dashboard-Karte
- Vierte Stat-Karte „Uploads gesamt" auf dem Dashboard
- Klickbarer „Webcam-Uploader"-Brand in der Topbar (führt zu `/dashboard`)
- DB-Migrationen für `cams.sort_order`, `albums.sort_order`, `cams.duplicate_hash_threshold` (idempotent beim Service-Start)

### Changed
- Album-Zuordnung in Amazon Photos läuft jetzt direkt über die Drive-API mit lokalem ID-Caching, statt den Bulk-Sync der `amazon-photos`-Lib zu triggern
- Albums-Liste verwendet auf der Übersicht `sort_order`, im Cam-Form-Dropdown weiterhin alphabetisch
- Lightweight `AmazonPhotosLite`-Subklasse überschreibt `load_db`/`get_folders`/`build_tree` als No-Ops, um Init-Zeit von Minuten auf Sekunden zu drücken (relevant bei großen Foto-Bibliotheken)

### Fixed
- Service-Crash-Loop nach Service-Restart durch nicht synchronisierte Scheduler-Jobs (jetzt Self-Heal)
- Indentation-/Truncation-Bugs durch konsequentes Schreiben großer Files via Bash-Heredoc

## [0.5.0] – 2026-05-10

### Added
- Web-UI: Lightbox für vergrößerte Webcam-Vorschau
- Dashboard zeigt zusätzlich Pro-Cam und globalen Upload-Counter
- Klick auf Brand-Logo navigiert zum Dashboard
- Album-Zuordnung pro Cam mit Auto-Anlage in Amazon Photos beim ersten Upload

### Changed
- `cam_save` übergibt `album_db_id` zusätzlich zum Namen an `upload_file()` für stabile ID-Auflösung

## [0.4.0] – 2026-05-09

### Added
- Inoffizielle Lib `amazon-photos>=0.0.97` mit korrektem Cookie-Setup (statt v0.0.10)
- Pro-Cam Geo-Koordinaten und Sunrise/Sunset-Fenster via `astral`
- Settings-Seite zum Eintragen der Amazon-Photos-