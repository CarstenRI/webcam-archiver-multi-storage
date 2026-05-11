#!/usr/bin/env bash
# Erstellt einen self-extracting Single-File-Installer.
# Output: webcam-uploader-install.sh (im Projekt-Root)
#
# Nutzung:
#   bash scripts/make_bundle.sh
#
# Ergebnis kann der User per scp auf den Server kopieren und dort ausführen:
#   scp webcam-uploader-install.sh user@server:~/
#   ssh user@server 'sudo bash ~/webcam-uploader-install.sh'

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_FILE="$PROJECT_ROOT/webcam-uploader-install.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cd "$PROJECT_ROOT"

# 1) Tarball aller relevanten Files (ohne .env, venv, __pycache__, scripts/, output)
tar --exclude='.env' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='scripts' \
    --exclude='webcam-uploader-install.sh' \
    --exclude='deploy.sh' \
    -czf "$TMP_DIR/payload.tgz" \
    app/ systemd/ install.sh requirements.txt .env.example README.md CHANGELOG.md 2>/dev/null || \
tar --exclude='.env' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='scripts' \
    --exclude='webcam-uploader-install.sh' \
    --exclude='deploy.sh' \
    -czf "$TMP_DIR/payload.tgz" \
    app/ systemd/ install.sh requirements.txt .env.example CHANGELOG.md

# 2) Base64-kodieren
PAYLOAD_B64="$(base64 -w 0 "$TMP_DIR/payload.tgz" 2>/dev/null || base64 "$TMP_DIR/payload.tgz" | tr -d '\n')"
PAYLOAD_SIZE="$(stat -c%s "$TMP_DIR/payload.tgz" 2>/dev/null || stat -f%z "$TMP_DIR/payload.tgz")"

# 3) Shell-Stub schreiben + Payload anhängen
cat > "$OUT_FILE" <<'STUB_EOF'
#!/usr/bin/env bash
# Webcam-Uploader – Self-Extracting Installer
# Auf dem Linux-Server ausführen:
#     sudo bash webcam-uploader-install.sh
#
# Dieses Script enthält den kompletten Anwendungs-Code.
# Es entpackt sich nach /tmp/webcam-uploader-install/ und ruft install.sh auf.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Bitte mit sudo/root ausführen:"
  echo "    sudo bash $0"
  exit 1
fi

EXTRACT_DIR="${WU_EXTRACT_DIR:-/tmp/webcam-uploader-install}"
mkdir -p "$EXTRACT_DIR"
echo "==> Entpacke Anwendung nach $EXTRACT_DIR …"

# Payload-Marker finden und alles danach extrahieren
PAYLOAD_LINE="$(grep -anm1 '^__PAYLOAD_BELOW__' "$0" | cut -d: -f1)"
if [ -z "$PAYLOAD_LINE" ]; then
  echo "Fehler: Payload-Marker nicht gefunden."
  exit 1
fi
PAYLOAD_START=$((PAYLOAD_LINE + 1))

tail -n +$PAYLOAD_START "$0" | base64 -d | tar -xz -C "$EXTRACT_DIR"

cd "$EXTRACT_DIR"
echo "==> Starte install.sh aus $EXTRACT_DIR …"
exec bash "$EXTRACT_DIR/install.sh"

# ------------------------------------------------------------------
__PAYLOAD_BELOW__
STUB_EOF

# 4) Payload anhängen
echo "$PAYLOAD_B64" >> "$OUT_FILE"

chmod +x "$OUT_FILE"

OUT_SIZE="$(stat -c%s "$OUT_FILE" 2>/dev/null || stat -f%z "$OUT_FILE")"
OUT_KB=$((OUT_SIZE / 1024))
PL_KB=$((PAYLOAD_SIZE / 1024))

echo "✓ Bundle erstellt:"
echo "    $OUT_FILE  (${OUT_KB} KB, Payload ${PL_KB} KB)"
echo
echo "Auf den Linux-Server kopieren z.B. mit:"
echo "    scp $OUT_FILE  user@server:~/"
echo "    ssh user@server 'sudo bash ~/webcam-uploader-install.sh'"
