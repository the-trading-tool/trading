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

# Rueckfrage vor dem (Neu-)Anlegen einer Datenbankdatei, die bereits existiert.
# Gibt 0 (fortfahren) zurueck wenn die Datei fehlt oder der Nutzer bestaetigt,
# 1 (ueberspringen) wenn die Datei existiert und der Nutzer ablehnt.
confirm_overwrite() {
    local desc="$1" path="$2"
    if [[ -e "$path" ]]; then
        warn "$desc existiert bereits: $path"
        read -rp "  Trotzdem neu anlegen/aktualisieren? (j/N): " REPLY
        if ! echo "$REPLY" | grep -qiE '^[jy]'; then
            info "Uebersprungen -- bestehende Datei bleibt unveraendert."
            return 1
        fi
    fi
    return 0
}

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

# Datenbank-Verzeichnis -- respektiert TradingDB-Env analog zu tools.get_path()
DB_DIR="${TradingDB:-$ROOT/database}"

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
# Phase 1F: Scheduler-Jobs seeden (Linux-Variante)
# ---------------------------------------------------------------------------
# Befuellt database/scheduler.db mit den Standard-Jobs, sofern die Tabelle noch
# leer ist (idempotent -- eine bestehende Job-Liste wird NIE ueberschrieben).
# Der Python-Pfad ergibt sich aus sys.executable (= diese .venv), die Skript-
# Pfade aus dem aktuellen Installationsverzeichnis -- keine hartcodierten Pfade.
# Bewusst NICHT geseedet: TradingDB-Env (nur Produktion), sowie die beiden
# broker-/order-ausfuehrenden Jobs STOPLOSS_TRAILING und EXECUTE_ORDER.

step "Phase 1F: Scheduler-Jobs seeden (database/scheduler.db)"

if "$VENV_PYTHON" - <<'PYEOF'
import os, sys, sqlite3

ROOT = os.getcwd()
PY   = sys.executable                                    # = .venv-Python
DB   = os.path.join(ROOT, "database", "scheduler.db")
# Linux: Standby (nur diese Zeile unterscheidet sich von der Windows-Variante)
STANDBY = "systemctl suspend"

os.makedirs(os.path.dirname(DB), exist_ok=True)
conn = sqlite3.connect(DB)
c = conn.cursor()
c.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, time TEXT, command TEXT, "
          "frequency TEXT, job_name TEXT, end_time TEXT, time_range TEXT, allowed_days TEXT, "
          "enabled INTEGER DEFAULT 1, last_run TEXT)")
c.execute("CREATE TABLE IF NOT EXISTS processes (id INTEGER PRIMARY KEY, task_id INTEGER, "
          "pid INTEGER, job_name TEXT)")
conn.commit()

c.execute("SELECT COUNT(*) FROM jobs")
if c.fetchone()[0] > 0:
    print("   scheduler.db enthaelt bereits Jobs -- Seed uebersprungen")
    sys.exit(0)

WD  = "monday,tuesday,wednesday,thursday,friday"          # Handelstage Mo-Fr
EU  = "^GDAXI,^MDAXI,^SDAXI,^SSMI,^IBEX,^FTSE,^STOXX50E"
US  = "^SPX,^DJI,^NDX,^IXIC,^NYA"
AS  = "^HSI,^N225"

# (time, script, extra-args, frequency, job_name, end_time, time_range, allowed_days, enabled)
JOBS = [
    ("5",     "get_asset_data.py",     "1m:2d /group:"+EU,                    "minutes",        "YAHOO_M_DATA_MEMBER_EUROPE",  "",      "08:00-19:00", WD, 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /group:"+EU,            "hours",          "YAHOO_H_DATA_MEMBER_EUROPE",  "",      "08:00-19:00", WD, 1),
    ("3",     "get_asset_data.py",     "1m:2d /index",                        "minutes",        "YAHOO_M_DATA_INDEX",          "",      "08:00-19:00", WD, 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /index",                "hours",          "YAHOO_H_DATA_INDEX",          "",      "08:00-19:00", WD, 1),
    ("16:00", "asset_perf2.py",        "true /add_current /silent",           WD,               "ASSET_PERFORMACE",            "",      "",            "", 1),
    ("19:52", "asset_perf2.py",        "true /all /silent",                   "monday,wednesday,friday", "ASSET_PERFORMACE_ALL", "",     "",            "", 1),
    ("14:00", "get_asset_data.py",     "1m:7d 1d:2mo 60m:2mo 1mo:1y /all",    "saturday",       "YAHOO_PRICES",                "",      "",            "", 1),
    ("09:00", "get_asset_info.py",     "",                                    "sunday",         "ASSET_INFO",                  "",      "",            "", 1),
    ("08:00", "get_asset_data.py",     "60m:2d 1d:1mo /index_member",         WD,               "YAHOO_D_DATA_INDEX_MEMBER",   "",      "",            "", 1),
    ("09:00", "get_asset_data.py",     "60m:2d 1d:1mo /index",                WD,               "YAHOO_D_DATA_INDEX",          "",      "",            "", 1),
    ("10:00", "get_asset_data.py",     "1d:1mo /index_member",                WD,               "YAHOO_D_DATA_INDEX_MEMBER",   "",      "",            "", 1),
    ("19:15", "get_asset_data.py",     "60m:2y 1d:2y /all",                   "saturday",       "YAHOO_ALL",                   "",      "",            "", 1),
    ("11:05", "get_asset_data.py",     "60m:2mo 1d:2mo 1m:7d /inverse",       WD,               "YAHOO_ALL_INVERSE_",          "19:45", "",            "", 1),
    ("22:30", None,                    "",                                    WD+",saturday",   "STANDBY",                     "22:45", "",            "", 0),
    ("22:00", "asset_perf2.py",        "true /add_current /silent",           WD,               "ASSET_PERFORMANCE_2",         "",      "",            "", 1),
    ("22:35", "recalc_correlation.py", "",                                    WD+",saturday",   "CORRELATION_INDEX",           "",      "",            "", 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /group:CRYPTO,COMMODITIES,METALS,CURRENCIES", "hours",   "YAHOO_H_DATA_GROUP",  "",      "",            "", 1),
    ("15",    "get_asset_data.py",     "1m:2d /group:CRYPTO,COMMODITIES,METALS,CURRENCIES",         "minutes", "YAHOO_M_DATA_GROUP",  "",      "",            "", 1),
    ("1",     "get_asset_data.py",     "1m:2d 60m:2d /group:ETP /worker:4",   "hours",          "YAHOO_H_DATA_ETP",            "",      "",            "", 1),
    ("22:00", "asset_perf2.py",        "/all /add_current /silent /group:ETP", WD+",saturday",  "ASSET_PERFORMANCE_ETP",       "",      "",            "", 1),
    ("10:00", "get_asset_data.py",     "1m:5d 1mo:5y 60m:2y 1d:5y /all /group:ETP /worker:4",       "saturday", "YAHOO_W_ETP",        "",      "",            "", 1),
    ("10:30", "warm_market_stress.py", "",                                    WD+",saturday,sunday", "WARM_MARKET_STRESS",     "",      "",            "", 1),
    ("10:35", "warm_rotation.py",      "",                                    WD+",saturday,sunday", "WARM_ROTATION",          "",      "",            "", 1),
    ("5",     "get_asset_data.py",     "1m:2d /group:"+US,                    "minutes",        "YAHOO_M_DATA_MEMBER_AMERICAS", "",     "15:00-22:00", WD, 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /group:"+US,            "hours",          "YAHOO_H_DATA_MEMBER_AMERICAS", "",     "15:00-22:00", WD, 1),
    ("5",     "get_asset_data.py",     "1m:2d /group:"+AS,                    "minutes",        "YAHOO_M_DATA_MEMBER_ASIA",    "",      "22:00-08:00", WD, 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /group:"+AS,            "hours",          "YAHOO_H_DATA_MEMBER_ASIA",    "",      "22:00-08:00", WD, 1),
]

def build(script, extra):
    cmd = '"%s" "%s"' % (PY, os.path.join(ROOT, script))
    return cmd + (" " + extra if extra else "")

for (t, script, extra, freq, name, end, rng, days, en) in JOBS:
    command = STANDBY if script is None else build(script, extra)
    c.execute("INSERT INTO jobs (time, command, frequency, job_name, end_time, time_range, "
              "allowed_days, enabled, last_run) VALUES (?,?,?,?,?,?,?,?,NULL)",
              (t, command, freq, name, end, rng, days, en))
conn.commit()
conn.close()
print("   %d Scheduler-Jobs geseedet -> %s" % (len(JOBS), DB))
PYEOF
then
    ok "Scheduler-Jobs bereit"
    info "Hinweis: Die order-ausfuehrenden Jobs STOPLOSS_TRAILING und EXECUTE_ORDER"
    info "werden bewusst NICHT automatisch angelegt. Bei Bedarf im Scheduler-Tab"
    info "manuell erganzen (Broker/Alpaca-Keys + gewuenschter --user noetig)."
else
    warn "Scheduler-Seed fehlgeschlagen -- die Jobs koennen spaeter in der App (Tab 'Scheduler') angelegt werden."
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
    if confirm_overwrite "yf_tickers.db" "$DB_DIR/yf_tickers.db"; then
        if "$VENV_PYTHON" seed_db.py; then
            ok "yf_tickers.db befuellt"
        else
            fail "seed_db.py fehlgeschlagen."
            exit 1
        fi
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
    if confirm_overwrite "asset_info.db" "$DB_DIR/asset_info.db"; then
        info "Laedt Ticker-Stammdaten von Yahoo Finance ..."
        if "$VENV_PYTHON" get_asset_info.py; then
            ok "asset_info.db befuellt"
        else
            warn "get_asset_info.py mit Fehler beendet (einige Ticker wurden moeglicherweise uebersprungen)."
        fi
    fi
fi

if [[ "$DO_INIT" -eq 1 ]]; then
    step "Phase 4B: OHLC-Kursdaten laden (get_asset_data.py)"
    existing_price_dbs=$(find "$DB_DIR" -maxdepth 1 -name 'yf_*.db' ! -name 'yf_tickers.db' 2>/dev/null | wc -l)
    if [[ "$existing_price_dbs" -gt 0 ]]; then
        warn "$existing_price_dbs vorhandene Kursdaten-Datei(en) (yf_<TICKER>.db) in $DB_DIR gefunden."
        warn "Der Download ergaenzt/aktualisiert bestehende Daten, ueberschreibt sie nicht pauschal."
    fi
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
