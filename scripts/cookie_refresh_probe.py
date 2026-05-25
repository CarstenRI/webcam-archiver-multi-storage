#!/usr/bin/env python3
"""Diagnose-Skript: empirisch ermitteln, ob amazon.de Cookies selbst rolliert.

Nutzung auf dem Server:
    cd /opt/webcam-uploader     # oder wo die App liegt
    sudo -u webcam-uploader /opt/webcam-uploader/venv/bin/python3 \\
        scripts/cookie_refresh_probe.py

Was es macht:
- Liest die aktuellen Amazon-Cookies aus der App-DB.
- Schickt mit diesen Cookies 5 verschiedene GET-Requests an amazon.de-Endpoints.
- Pro Endpoint: was kommt im Set-Cookie-Header zurueck? Welche Cookies wurden
  veraendert? Was war die HTTP-Response (200, 302, 401, ...)? Liefen wir in
  eine Sign-In-Page (Indikator dass die Session schon abgelaufen ist)?
- Schreibt Ergebnis nach stdout — schickst du mir, ich werte aus.

Datenschutz:
- Cookie-WERTE werden NICHT geprintet, nur die NAMEN und ob ein neuer Wert
  zurueckkam. Sicher zum copy-paste.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# App-Pfad auf den sys.path. Funktioniert sowohl im Repo (scripts/ neben app/)
# als auch wenn das Skript nach /tmp/ kopiert ist — dann nehmen wir den
# Standard-Install-Pfad oder eine env-Variable WU_APP_ROOT.
import os
HERE = Path(__file__).resolve().parent
candidates = [
    Path(os.environ.get("WU_APP_ROOT", "")),
    HERE.parent,                       # Repo-Layout: scripts/ neben app/
    Path("/opt/webcam-uploader"),      # install.sh-Default
]
for c in candidates:
    if c and (c / "app" / "__init__.py").is_file():
        sys.path.insert(0, str(c))
        break
else:
    print("FEHLER: app/ nicht gefunden. Setz WU_APP_ROOT auf den Install-Pfad:")
    print("  WU_APP_ROOT=/opt/webcam-uploader python3 cookie_refresh_probe.py")
    sys.exit(2)

try:
    import httpx
except ImportError:
    print("FEHLER: httpx nicht verfuegbar. Im App-venv ausfuehren:")
    print("  sudo -u webcam-uploader /opt/webcam-uploader/venv/bin/python3 scripts/cookie_refresh_probe.py")
    sys.exit(2)


def load_cookies_from_db() -> dict:
    """Liest die Cookies aus der App-Settings-Tabelle."""
    from app.db import SessionLocal, get_setting
    with SessionLocal() as s:
        raw = get_setting(s, "amazon_cookies", "")
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


# --- Test-Endpoints ---------------------------------------------------------
# Mix aus: leichtgewichtige Logged-In-Seiten + Photos-spezifische Endpoints.
# Ziel: rausfinden welcher als billigster Heartbeat taugt.
ENDPOINTS = [
    # Konto-Übersicht — typischer "ich bin eingeloggt"-Heartbeat
    ("https://www.amazon.de/gp/your-account/order-history", "GET", {}),
    # Photos-Frontend
    ("https://www.amazon.de/photos/", "GET", {}),
    # Photos-API-Root (CDProxy-Domain, die die App auch nutzt fuer Uploads)
    ("https://www.amazon.de/drive/v1/nodes", "GET", {"asset": "ALL", "limit": 1}),
    # Account-Settings — leichtgewichtig, oft cookie-roll-Trigger
    ("https://www.amazon.de/gp/account-manager/", "GET", {}),
    # Photos-Drive-Endpoint (was die amazon-photos-Lib selbst nutzt)
    ("https://www.amazon.de/drive/v1/account/usage", "GET", {}),
]

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def run_probe(initial_cookies: dict) -> None:
    print("=" * 70)
    print(f"Cookie-Refresh-Probe — {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 70)
    print(f"Geladene Cookies: {len(initial_cookies)}")
    if initial_cookies:
        print(f"  Names: {sorted(initial_cookies.keys())}")
    else:
        print("  KEINE Cookies in der DB — kannst Du Cookies bitte erstmal manuell setzen?")
        return
    print()

    # Wir verwenden eine frische cookie-jar pro Endpoint, damit wir sauber
    # sehen was der eine Endpoint im Set-Cookie liefert. Cross-Influence
    # zwischen Endpoints vermeiden.
    for url, method, params in ENDPOINTS:
        print("-" * 70)
        print(f"{method} {url}")
        if params:
            print(f"  params: {params}")
        try:
            with httpx.Client(
                cookies=dict(initial_cookies),
                follow_redirects=False,
                timeout=15.0,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/json,*/*",
                    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                },
            ) as client:
                r = client.request(method, url, params=params or None)
        except Exception as e:
            print(f"  HTTP-EXCEPTION: {e}")
            continue

        print(f"  status: {r.status_code}")
        if 300 <= r.status_code < 400:
            loc = r.headers.get("Location", "")
            print(f"  location: {loc[:120]}")
            signin = "ap/signin" in loc.lower() or "auth.amazon" in loc.lower()
            if signin:
                print("  >> SIGN-IN-REDIRECT — Session ist NICHT (mehr) gueltig fuer diese URL")

        # Set-Cookie-Analyse
        set_cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else []
        if not set_cookies:
            # Fallback: direkt aus client.cookies vergleichen
            new_cookies = dict(r.cookies)
            changed = {k for k, v in new_cookies.items() if initial_cookies.get(k) != v}
            removed = {k for k in initial_cookies if k in new_cookies and not new_cookies[k]}
        else:
            new_names = []
            for sc in set_cookies:
                # Name = alles vor dem ersten '='
                name = sc.split("=", 1)[0].strip()
                # Skip leerer Set-Cookie-Header
                if name:
                    new_names.append(name)
            changed = set(new_names)
            removed = set()

        if changed:
            # Welche davon waren im DB-Set drin? Welche sind neu?
            in_db = changed & set(initial_cookies)
            new_only = changed - set(initial_cookies)
            print(f"  Set-Cookie: {len(changed)} Cookies, davon {len(in_db)} aus DB rotiert, {len(new_only)} neu")
            if in_db:
                print(f"    rotated: {sorted(in_db)}")
            if new_only:
                print(f"    new:     {sorted(new_only)}")
        else:
            print("  Set-Cookie: KEINE — Amazon hat keine neuen Cookies geliefert")

        print(f"  body-size: {len(r.content)} bytes  content-type: {r.headers.get('content-type', '?')[:60]}")

    print()
    print("=" * 70)
    print("FERTIG. Bitte Output an Claude schicken (Werte sind nicht enthalten).")
    print("=" * 70)


if __name__ == "__main__":
    cookies = load_cookies_from_db()
    run_probe(cookies)
