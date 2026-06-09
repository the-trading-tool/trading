#!/usr/bin/env bash
# Trading-App Installer fuer Linux/macOS
#
# Verwendung:
#   ./install.sh                 # Umgebung + Config
#   ./install.sh --seed          # + Ticker-DB befuellen
#   ./install.sh --init-info     # + Ticker-DB + Metadaten von Yahoo (~20 Min)
#   ./install.sh --init          # Vollstaendige Initialisierung (Stunden!)
#
# Aequivalent zu install.ps1 unter Windows.

set -euo pipefail

# ---------------------------------------------------------------------------
# Farben / Hilfsfunktionen
# ---------------------------------------------------------------------------

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
GRAY='\033[0;37m'
WHITE='\033[1;37m'
NC='\033[0m'

step() { echo -e "\n${CYAN}==> $*${NC}"; }
ok()   { echo -e "${GREEN}    OK  $*${NC}"; }
info() { echo -e "${GRAY}    ... $*${NC}"; }
warn() { echo -e "${YELLOW}    WRN $*${NC}"; }
fail() { echo -e "${RED}    ERR $*${NC}"; }

# ---------------------------------------------------------------------------
# Parameter parsen
# ---------------------------------------------------------------------------

DO_SEED=0
DO_INIT=0
DO_INIT_INFO=0

for arg in "$@"; do
    case "$arg" in
        --seed)      DO_SEED=1 ;;
        --init-info) DO_INIT_INFO=1; DO_SEED=1 ;;
        --init)      DO_INIT=1; DO_INIT_INFO=1; DO_SEED=1 ;;
        -h|--help)
            grep '^#' "$0" | head -12 | sed 's/^# \?//'
            exit 0
            ;;
        *)
            fail "Unbekannter Parameter: $arg"
            fail "Erlaubt: --seed | --init-info | --init"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Verzeichnis-Pruefung
# ---------------------------------------------------------------------------

if [[ ! -f "asset_analyzer.py" ]]; then
    fail "Bitte den Installer aus dem Trading-Verzeichnis heraus starten."
    fail "  cd <Pfad zum Trading-Ordner>  dann  bash install.sh"
    exit 1
fi

ROOT="$(pwd)"
echo -e "\n${WHITE}Trading-App Installer${NC}"
echo -e "Arbeitsverzeichnis: $ROOT\n"

# ---------------------------------------------------------------------------
# Phase 1A: Python 3.10+ finden
# ---------------------------------------------------------------------------

step "Phase 1A: Python 3.10+ suchen"

PYTHON=""

for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" --version 2>&1 || true)
        if echo "$ver" | grep -qE 'Python 3\.(10|11|12|13)'; then
            PYTHON="$candidate"
            ok "$candidate gefunden: $ver"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    fail "Python 3.10+ nicht gefunden."
    fail "Ubuntu/Debian:  sudo apt install python3.11 python3.11-venv"
    fail "Fedora/RHEL:    sudo dnf install python3.11"
    fail "Oder:           https://www.python.org/downloads/"
    exit 1
fi

# 64-bit pruefen
bits=$("$PYTHON" -c "import struct; print(struct.calcsize('P') * 8)" 2>/dev/null || echo "0")
if [[ "$bits" != "64" ]]; then
    fail "32-bit Python erkannt. Diese App benoetigt 64-bit Python."
    exit 1
fi
ok "64-bit Python bestaetigt"

# ---------------------------------------------------------------------------
# Phase 1B: Virtuelle Umgebung
# ---------------------------------------------------------------------------

step "Phase 1B: Virtuelle Umgebung"

VENV_PYTHON="$ROOT/.venv/bin/python"
VENV_PIP="$ROOT/.venv/bin/pip"

if [[ -x "$VENV_PYTHON" ]]; then
    ok ".venv bereits vorhanden -- ueberspringe Erstellung"
else
    info "Erstelle .venv ..."
    "$PYTHON" -m venv .venv
    if [[ ! -x "$VENV_PYTHON" ]]; then
        fail ".venv wurde nicht korrekt erstellt (.venv/bin/python fehlt)."
        fail "Tipp (Ubuntu/Debian): sudo apt install python3-venv"
        exit 1
    fi
    ok ".venv erstellt"
fi

# ---------------------------------------------------------------------------
# Phase 1C: Pakete installieren
# ---------------------------------------------------------------------------

step "Phase 1C: Pakete installieren (requirements.txt + NLTK-Daten)"

info "pip upgrade ..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet || warn "pip upgrade fehlgeschlagen -- fahre trotzdem fort."

# TA-Lib benoetigt auf Linux die C-Bibliothek (libta-lib).
# Wir versuchen erst das Binary-Wheel; falls das fehlschlaegt, geben wir
# einen hilfreichen Hinweis aus aber brechen NICHT ab.
info "TA-Lib vorab pruefen ..."
if ! "$VENV_PYTHON" -c "import talib" &>/dev/null 2>&1; then
    info "TA-Lib wird installiert (kann etwas dauern) ..."
    if ! "$VENV_PYTHON" -m pip install "TA-Lib>=0.6.0" --prefer-binary --quiet 2>/dev/null; then
        warn "TA-Lib-Binary-Wheel fehlgeschlagen."
        warn "Fuer einen manuellen Build vorab installieren:"
        warn "  Ubuntu/Debian: sudo apt install libta-lib-dev"
        warn "  Fedora/RHEL:   sudo dnf install ta-lib-devel"
        warn "Die App laeuft ohne TA-Lib; einige Indikatoren sind dann nicht verfuegbar."
    fi
fi

info "Alle anderen Pakete installieren ..."
if ! "$VENV_PYTHON" -m pip install -r requirements.txt --prefer-binary --quiet; then
    fail "pip install fehlgeschlagen."
    fail "Moegliche Ursachen:"
    fail "  1. Keine Internetverbindung"
    fail "  2. Fehlende System-Bibliotheken (z.B. libta-lib-dev, build-essential)"
    fail "  3. Python-Version nicht unterstuetzt (empfohlen: 3.11)"
    exit 1
fi
ok "Pakete installiert"

info "NLTK vader_lexicon herunterladen (Sentiment-Analyse) ..."
if ! "$VENV_PYTHON" -c \
    "import nltk, ssl; ssl._create_default_https_context = ssl._create_unverified_context; nltk.download('vader_lexicon', quiet=False)"; then
    warn "vader_lexicon konnte nicht heruntergeladen werden."
    warn "Die Sentiment-Analyse in der App wird deaktiviert."
else
    ok "vader_lexicon heruntergeladen"
fi

# ---------------------------------------------------------------------------
# Phase 1D: Verzeichnisse anlegen
# ---------------------------------------------------------------------------

step "Phase 1D: Verzeichnisse anlegen"

for dir in database logs; do
    if [[ ! -d "$dir" ]]; then
        mkdir -p "$dir"
        ok "$dir/ erstellt"
    else
        info "$dir/ bereits vorhanden"
    fi
done

# ---------------------------------------------------------------------------
# Phase 1E: Fernet-Schluessel fuer ksplib generieren
# ---------------------------------------------------------------------------

step "Phase 1E: Fernet-Schluessel generieren (database/secret.key)"

if [[ -f "database/secret.key" ]]; then
    info "secret.key bereits vorhanden -- ueberspringe"
else
    info "Generiere neuen Fernet-Schluessel ..."
    if "$VENV_PYTHON" -c "from tradinglib.ksplib import Ksp; Ksp()"; then
        ok "secret.key erstellt"
    else
        warn "secret.key konnte nicht erstellt werden."
        warn "API-Zugangsdaten (Groq, Gemini, Pushover) koennen noch nicht gespeichert werden."
        warn "Der Schluessel wird automatisch beim ersten App-Start nachgeneriert."
    fi
fi

# ---------------------------------------------------------------------------
# Phase 2: config.yaml anlegen
# ---------------------------------------------------------------------------

step "Phase 2: Authentifizierungs-Konfiguration"

if [[ -f "config.yaml" ]]; then
    info "config.yaml bereits vorhanden -- ueberspringe"
else
    info "config.yaml fehlt -- neuen Admin-Benutzer anlegen"
    echo ""

    # Benutzername
    while true; do
        read -rp "  Admin-Benutzername (Standard: admin): " USERNAME
        USERNAME="${USERNAME:-admin}"
        if echo "$USERNAME" | grep -qE '^[a-zA-Z0-9_]{3,32}$'; then
            break
        fi
        warn "Nur Buchstaben, Ziffern und _ erlaubt (3-32 Zeichen)."
    done

    # E-Mail
    read -rp "  E-Mail-Adresse (Standard: admin@localhost): " EMAIL
    EMAIL="${EMAIL:-admin@localhost}"

    # Passwort
    while true; do
        read -rsp "  Passwort: " PW1; echo ""
        read -rsp "  Passwort bestaetigen: " PW2; echo ""
        if [[ "$PW1" != "$PW2" ]]; then
            warn "Passwoerter stimmen nicht ueberein."
            continue
        fi
        if [[ ${#PW1} -lt 8 ]]; then
            warn "Mindestens 8 Zeichen erforderlich."
            continue
        fi
        break
    done

    info "bcrypt-Hash wird berechnet ..."
    HASH=$("$VENV_PYTHON" - "$PW1" <<'PYEOF'
import sys, bcrypt
pw = sys.argv[1].encode()
print(bcrypt.hashpw(pw, bcrypt.gensalt(12)).decode())
PYEOF
)
    if [[ -z "$HASH" ]]; then
        fail "Passwort-Hash konnte nicht erstellt werden."
        exit 1
    fi

    COOKIE_KEY=$("$VENV_PYTHON" -c "import secrets; print(secrets.token_hex(32))")

    cat > config.yaml <<YAML
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
  name: trading_auth
YAML

    ok "config.yaml erstellt (Benutzer: $USERNAME)"
    warn "WICHTIG: config.yaml enthaelt Passwort-Hash -- niemals in Git committen!"
fi

# ---------------------------------------------------------------------------
# Phase 3: Ticker-DB befuellen
# ---------------------------------------------------------------------------

if [[ "$DO_SEED" -eq 1 ]]; then
    step "Phase 3: Ticker-Datenbank befuellen (seed_db.py)"
    if "$VENV_PYTHON" seed_db.py; then
        ok "yf_tickers.db befuellt"
    else
        fail "seed_db.py fehlgeschlagen."
        exit 1
    fi
else
    info "Ticker-DB nicht befuellt (kein --seed / --init / --init-info angegeben)"
    info "Zum Befuellen: bash install.sh --seed"
fi

# ---------------------------------------------------------------------------
# Phase 4: Yahoo-Downloads
# ---------------------------------------------------------------------------

if [[ "$DO_INIT_INFO" -eq 1 ]]; then
    step "Phase 4A: Asset-Metadaten laden (get_asset_info.py)"
    info "Laedt Ticker-Stammdaten von Yahoo Finance ..."
    if "$VENV_PYTHON" get_asset_info.py; then
        ok "asset_info.db befuellt"
    else
        warn "get_asset_info.py mit Fehler beendet (einige Ticker wurden moeglicherweise uebersprungen)."
    fi
fi

if [[ "$DO_INIT" -eq 1 ]]; then
    step "Phase 4B: OHLC-Kursdaten laden (get_asset_data.py)"
    warn "Dies laedt historische Kurse fuer alle Ticker -- kann Stunden dauern!"
    read -rp "  Fortfahren? (j/N): " CONFIRM
    if echo "$CONFIRM" | grep -qiE '^[jy]'; then
        info "Lade Aktien-Kursdaten (/index_member) ..."
        if "$VENV_PYTHON" get_asset_data.py /index_member 1m:7d 60m:60d 1d:max 1mo:max; then
            ok "Aktien-OHLC geladen"
        else
            warn "get_asset_data.py /index_member mit Fehler beendet."
        fi

        info "Lade Index-Kursdaten (/index) ..."
        if "$VENV_PYTHON" get_asset_data.py /index 1m:7d 60m:60d 1d:max 1mo:max; then
            ok "Index-OHLC geladen (^GDAXI, ^GSPC, ...)"
        else
            warn "get_asset_data.py /index mit Fehler beendet."
        fi
    else
        info "OHLC-Download uebersprungen."
        info "Spaeter nachholen:"
        info "  .venv/bin/python get_asset_data.py /index_member 1m:7d 60m:60d 1d:max 1mo:max"
        info "  .venv/bin/python get_asset_data.py /index 1m:7d 60m:60d 1d:max 1mo:max"
    fi

    step "Phase 4C: Performance berechnen (asset_perf2.py init)"
    info "Berechnet Scores fuer alle Ticker ..."
    if "$VENV_PYTHON" asset_perf2.py init /worker:2; then
        ok "Performance-DB befuellt"
    else
        warn "asset_perf2.py mit Fehler beendet."
    fi
fi

# ---------------------------------------------------------------------------
# Abschluss
# ---------------------------------------------------------------------------

echo ""
echo -e "${WHITE}===========================================${NC}"
echo -e "${GREEN} Installation abgeschlossen!${NC}"
echo -e "${WHITE}===========================================${NC}"
echo ""
echo -e "${CYAN} App starten:${NC}"
echo "   .venv/bin/streamlit run asset_analyzer.py"
echo ""

if [[ "$DO_SEED" -eq 0 ]]; then
    echo -e "${RED}  WICHTIG: Die App benoetigt Ticker-Stammdaten um zu funktionieren!${NC}"
    echo -e "${YELLOW}  Bitte jetzt ausfuehren:${NC}"
    echo ""
    echo -e "${CYAN}    bash install.sh --init-info   # PFLICHT (~20 Min)${NC}"
    echo ""
    echo -e "${GRAY}  Optional danach:${NC}"
    echo "    bash install.sh --init       # + Kursdaten laden (dauert Stunden)"
    echo ""
fi
