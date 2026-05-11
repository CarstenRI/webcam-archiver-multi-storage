# Changelog

Alle nennenswerten Änderungen an diesem Projekt sind hier dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
und das Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

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
- Backend (`set