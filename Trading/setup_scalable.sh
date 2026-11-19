#!/usr/bin/env bash
# Einmal-Setup der Scalable-Edition: eigenes Datenverzeichnis + eigener Login.
#
# Die Scalable-Edition läuft aus derselben Codebasis wie die volle Trading-App,
# nutzt aber ein getrenntes Datenverzeichnis (Env-Var TradingDB). Da config.yaml
# ebenfalls über TradingDB aufgelöst wird, bekommt die Scalable-Edition damit
# automatisch eigene Credentials + eigenen Cookie-Key.
#
# Usage:
#   bash setup_scalable.sh
#   bash setup_scalable.sh /opt/scalable_data

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

DATA_DIR="${1:-$HOME/scalable_data}"

PY="$ROOT/.venv/bin/python"
if [ ! -f "$PY" ]; then
    echo "[FEHLER] .venv nicht gefunden. Bitte zuerst install.sh ausfuehren." >&2
    exit 1
fi

mkdir -p "$DATA_DIR"
CFG="$DATA_DIR/config.yaml"

if [ -f "$CFG" ]; then
    echo "config.yaml existiert bereits in $DATA_DIR -- uebersprungen."
    echo "Setup fertig. Start mit:  bash start_scalable.sh \"$DATA_DIR\""
    exit 0
fi

echo "Neuen Admin-Benutzer fuer die Scalable-Edition anlegen ($DATA_DIR)"
echo ""

while true; do
    read -rp "  Admin-Benutzername (Standard: admin): " USERNAME
    USERNAME="${USERNAME:-admin}"
    if echo "$USERNAME" | grep -qE '^[a-zA-Z0-9_]{3,32}$'; then
        break
    fi
    echo "  Nur Buchstaben, Ziffern und _ (3-32 Zeichen)."
done

read -rp "  E-Mail-Adresse (Standard: admin@localhost): " EMAIL
EMAIL="${EMAIL:-admin@localhost}"

while true; do
    read -rsp "  Passwort: " PW1; echo
    read -rsp "  Passwort bestaetigen: " PW2; echo
    if [ "$PW1" != "$PW2" ]; then
        echo "  Passwoerter stimmen nicht ueberein."
        continue
    fi
    if [ "${#PW1}" -lt 8 ]; then
        echo "  Mindestens 8 Zeichen erforderlich."
        continue
    fi
    break
done

echo "bcrypt-Hash wird berechnet ..."
HASH=$("$PY" -c "
import bcrypt, sys
pw = sys.argv[1].encode()
print(bcrypt.hashpw(pw, bcrypt.gensalt(12)).decode())
" "$PW1")

if [ -z "$HASH" ]; then
    echo "[FEHLER] Passwort-Hash konnte nicht erstellt werden." >&2
    exit 1
fi

COOKIE_KEY=$("$PY" -c "import secrets; print(secrets.token_hex(32))")

cat > "$CFG" <<YAML
credentials:
  usernames:
    ${USERNAME}:
      email: ${EMAIL}
      failed_login_attempts: 0
      logged_in: false
      name: ${USERNAME}
      password: ${HASH}
      admin: true
pre-authorized:
  emails:
  - ${EMAIL}
cookie:
  expiry_days: 30
  key: ${COOKIE_KEY}
  name: scalable_auth
YAML

echo ""
echo "config.yaml erstellt in $DATA_DIR (Benutzer: $USERNAME)"
echo "WICHTIG: config.yaml enthaelt den Passwort-Hash -- niemals in Git committen!"
echo ""
echo "Start der Scalable-Edition:"
echo "  bash start_scalable.sh \"$DATA_DIR\""
