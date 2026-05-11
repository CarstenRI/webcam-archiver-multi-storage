#!/usr/bin/env bash
# Webcam-Uploader – Installer für Ubuntu/Debian.
# Wird auf dem Linux-Server ausgeführt:
#   sudo bash install.sh
#
# Idempotent: kann mehrfach laufen (Update-Mode).

set -euo pipefail

INSTALL_DIR="/opt/webcam-uploader"
DATA_DIR="/var/lib/webcam-uploader"
CONFIG_DIR="/etc/webcam-uploader"
SERVICE_USER="webcam-uploader"
SERVICE_NAME="webcam-uploader"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$EUID" -ne 0 ]; then
  echo "Bitte mit sudo/root ausführen: sudo bash install.sh"
  exit 1
fi

echo "==> Webcam-Uploader Installer"
echo "    Quelle:  $SRC_DIR"
echo "    Ziel:    $INSTALL_DIR"
echo

# 1) System-Pakete
echo "==> Installiere System-Pakete (python3, venv, build-essentials)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-dev \
  build-essential libjpeg-dev zlib1g-dev libwebp-dev \
  ca-certificates curl

# 2) System-User
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  echo "==> Lege Service-User '$SERVICE_USER' an…"
  useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# 3) Verzeichnisse
echo "==> Erstelle Verzeichnisse…"
mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$DATA_DIR/tmp" "$DATA_DIR/previews" "$CONFIG_DIR"

# 4) Code kopieren (alte app/ entfernen, dann frisch kopieren)
echo "==> Kopiere Anwendungs-Code nach $INSTALL_DIR…"
rm -rf "$INSTALL_DIR/app"
cp -r "$SRC_DIR/app" "$INSTALL_DIR/"
# Aufräumen falls __pycache__ im Bundle wären (sollte nicht der Fall sein)
find "$INSTALL_DIR/app" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
find "$INSTALL_DIR/app" -name '*.pyc' -delete 2>/dev/null || true
cp "$SRC_DIR/requirements.txt" "$INSTALL_DIR/"

# 5) venv + Python-Deps
echo "==> Erstelle/aktualisiere virtuelles Environment…"
if [ ! -d "$INSTALL_DIR/venv" ]; then
  python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip wheel >/dev/null
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# 6) .env (nur anlegen, falls noch nicht existent – nichts überschreiben)
if [ ! -f "$CONFIG_DIR/.env" ]; then
  echo "==> Erstelle initiale Konfiguration in $CONFIG_DIR/.env"
  cp "$SRC_DIR/.env.example" "$CONFIG_DIR/.env"
  RANDOM_PW="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16 || true)"
  sed -i "s|WU_AUTH_PASSWORD=change-me-please|WU_AUTH_PASSWORD=$RANDOM_PW|" "$CONFIG_DIR/.env"
  echo
  echo "    >>> Initiales Passwort: $RANDOM_PW"
  echo "    >>> Login: admin / $RANDOM_PW"
  echo "    >>> Anpassen in $CONFIG_DIR/.env"
  echo
else
  echo "==> $CONFIG_DIR/.env existiert bereits – wird nicht überschrieben."
fi
chmod 600 "$CONFIG_DIR/.env"

# 7) Permissions
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$DATA_DIR" "$CONFIG_DIR"

# 8) systemd-Unit
echo "==> Installiere systemd-Unit…"
cp "$SRC_DIR/systemd/webcam-uploader.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

# 8.5) sudoers-Eintrag fuer UI-gesteuerten Service-Restart nach Port-Aenderung
SUDOERS_FILE="/etc/sudoers.d/webcam-uploader"
echo "==> Installiere sudoers-Eintrag (passwordless restart fuer Service-User)…"
SYSTEMCTL_PATHS="/bin/systemctl /usr/bin/systemctl"
SYSTEMCTL_FOUND=""
for p in $SYSTEMCTL_PATHS; do
  [ -x "$p" ] && SYSTEMCTL_FOUND="$p" && break
done
if [ -z "$SYSTEMCTL_FOUND" ]; then
  echo "    WARNUNG: systemctl nicht gefunden – sudoers-Datei wird nicht angelegt."
  echo "    UI-Port-Aenderungen muessen dann manuell mit 'systemctl restart $SERVICE_NAME' aktiviert werden."
else
  cat > "$SUDOERS_FILE" <<SUDO_EOF
# Webcam-Uploader: Erlaubt dem Service-User passwordless 'systemctl restart webcam-uploader'.
# Wird von der Settings-UI nach einer Port-Aenderung aufgerufen, damit der Restart
# automatisch passieren kann.
$SERVICE_USER ALL=(root) NOPASSWD: /bin/systemctl restart $SERVICE_NAME, /usr/bin/systemctl restart $SERVICE_NAME, /bin/systemctl restart $SERVICE_NAME.service, /usr/bin/systemctl restart $SERVICE_NAME.service
SUDO_EOF
  chmod 0440 "$SUDOERS_FILE"
  if ! visudo -c -f "$SUDOERS_FILE" >/dev/null 2>&1; then
    echo "    FEHLER: sudoers-Eintrag ungueltig – wird entfernt."
    rm -f "$SUDOERS_FILE"
  fi
fi

# 9) Status-Ausgabe
sleep 1
echo
echo "==> Service-Status:"
systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true

# 10) Hinweise
PORT=$(grep -E '^WU_PORT=' "$CONFIG_DIR/.env" | cut -d= -f2 || echo 8080)
HOST_IP=$(hostname -I | awk '{print $1}')

cat <<EOF

============================================================
✓ Installation abgeschlossen.

  Web-UI:    http://$HOST_IP:$PORT
  Login:     siehe oben (oder $CONFIG_DIR/.env)

  Logs:        sudo journalctl -u $SERVICE_NAME -f
  Neustart:    sudo systemctl restart $SERVICE_NAME
  Konfig:      sudo nano $CONFIG_DIR/.env
  Daten:       $DATA_DIR

  Update:    install.sh erneut ausführen.

============================================================
EOF
