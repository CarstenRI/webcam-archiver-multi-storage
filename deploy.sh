#!/usr/bin/env bash
# Deployment-Skript: baut das Bundle, lädt es auf den Server, installiert.
#
# Voraussetzungen lokal:
#   - bash (Linux/macOS, oder Windows mit Git-Bash/WSL)
#   - ssh + scp (auf Windows in PowerShell ab Win10 dabei)
#
# Verwendung:
#   ./deploy.sh user@server.example.com
#
# Optional ein Port:
#   ./deploy.sh user@server.example.com -p 2222
#
# Dieses Script führt aus:
#   1. ./scripts/make_bundle.sh   (erzeugt webcam-uploader-install.sh)
#   2. scp dieses Bundle nach $HOME auf den Remote-Server
#   3. sudo bash <bundle>         (Installation/Update)

set -euo pipefail

if [ $# -lt 1 ]; then
  cat <<EOF
Verwendung: $0 <user@host> [ssh-options]

Beispiele:
  $0 root@cam-server.lan
  $0 carsten@192.168.0.42 -p 2222 -i ~/.ssh/id_ed25519

EOF
  exit 1
fi

REMOTE="$1"
shift
SSH_OPTS=("$@")

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "==> Baue Bundle…"
bash scripts/make_bundle.sh

BUNDLE="$PROJECT_ROOT/webcam-uploader-install.sh"

echo
echo "==> Lade Bundle auf $REMOTE …"
scp "${SSH_OPTS[@]}" "$BUNDLE" "$REMOTE:/tmp/webcam-uploader-install.sh"

echo
echo "==> Installiere/Update auf $REMOTE …"
# -t allokiert ein TTY, damit sudo nach dem Passwort fragen kann, falls
# der Remote-Account kein NOPASSWD-sudoers-Eintrag hat (auf .142 der Fall).
ssh -t "${SSH_OPTS[@]}" "$REMOTE" 'sudo bash /tmp/webcam-uploader-install.sh'

echo
echo "✓ Deploy fertig."
