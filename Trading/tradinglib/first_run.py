"""
tradinglib/first_run.py — Erkennung und Durchführung der Ersteinrichtung.

Wird von asset_analyzer.py aufgerufen, bevor der eigentliche App-Code läuft.
Zeigt einen Streamlit-Setup-Wizard, wenn noch keine Datenbank vorhanden ist.

Schritte:
  1. Willkommen  — kurze Erklärung
  2. Ticker-DB   — seed_db.py ausführen (legt yf_tickers.db an)
  3. Preisdaten  — get_asset_data.py für einen ersten Tag-Datensatz
  4. Fertig      — App neu laden

Aufruf in asset_analyzer.py:

  # Ganz oben im Modul (VOR TradingApp()):
  from tradinglib.first_run import ensure_config_yaml
  ensure_config_yaml()

  # Ganz oben in render():
  from tradinglib.first_run import maybe_show_setup
  if maybe_show_setup():
      st.stop()
"""

import sys
import os
import subprocess
import threading
import time
import logging
from pathlib import Path
from typing import Optional

import streamlit as st

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pfad-Hilfsfunktionen
# ---------------------------------------------------------------------------

def _app_root() -> Path:
    """Return the app root directory containing asset_analyzer.py.

    Searches the current working directory first, then falls back to the
    parent of this file's tradinglib/ package directory.
    """
    # 1. Aktuelles Arbeitsverzeichnis (normal beim `streamlit run` Aufruf)
    cwd = Path(os.getcwd())
    if (cwd / "asset_analyzer.py").exists():
        return cwd

    # 2. Relativ zu dieser Datei (tradinglib/ ist ein Unterordner der App)
    here = Path(__file__).resolve().parent.parent
    if (here / "asset_analyzer.py").exists():
        return here

    return cwd


def _db_dir() -> Path:
    """Return the database directory, respecting the TradingDB environment variable."""
    tdb = os.environ.get("TradingDB", "")
    if tdb:
        return Path(tdb)
    return _app_root() / "database"


def _find_python() -> str:
    """Return the Python executable that is currently running the app."""
    return sys.executable


# ---------------------------------------------------------------------------
# Zustandsprüfungen
# ---------------------------------------------------------------------------

def _tickers_db_exists() -> bool:
    """Return True when yf_tickers.db exists in the database directory."""
    return (_db_dir() / "yf_tickers.db").exists()


def _price_data_exists() -> bool:
    """True wenn mindestens eine yf_*.db Preisdatei vorhanden ist."""
    db_dir = _db_dir()
    if not db_dir.exists():
        return False
    return any(db_dir.glob("yf_*.db"))


def setup_needed() -> bool:
    """True wenn noch keine Grunddaten vorhanden sind."""
    return not _tickers_db_exists()


# ---------------------------------------------------------------------------
# Welcome-Page-Marker  (einmalige Anzeige beim ersten App-Start)
# ---------------------------------------------------------------------------

_WELCOME_FLAG = "welcome_shown"          # Dateiname im database/-Verzeichnis


def welcome_shown() -> bool:
    """True wenn die WelcomePage bereits einmal angezeigt wurde."""
    return (_db_dir() / _WELCOME_FLAG).exists()


def mark_welcome_shown() -> None:
    """Create the welcome_shown marker file so the WelcomePage is not shown again."""
    flag = _db_dir() / _WELCOME_FLAG
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    except OSError as exc:
        logger.warning("Konnte welcome_shown-Marker nicht anlegen: %s", exc)


# ---------------------------------------------------------------------------
# Hintergrund-Task-Tracking via st.session_state
# ---------------------------------------------------------------------------

_KEY_STEP       = "first_run_step"          # aktueller Schritt (int)
_KEY_LOG        = "first_run_log"           # Liste von Log-Zeilen
_KEY_RUNNING    = "first_run_running"       # bool: Task läuft gerade
_KEY_SUCCESS    = "first_run_success"       # bool: letzter Task erfolgreich
_KEY_DONE       = "first_run_done"          # bool: gesamter Setup abgeschlossen


def _init_state():
    """Initialise all first-run wizard keys in st.session_state with default values."""
    defaults = {
        _KEY_STEP:    0,
        _KEY_LOG:     [],
        _KEY_RUNNING: False,
        _KEY_SUCCESS: None,
        _KEY_DONE:    False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _append_log(line: str):
    """Append a log line to the session-state log buffer."""
    st.session_state[_KEY_LOG].append(line)


def _run_script_in_thread(script_path: str, args: list[str]):
    """Run a Python script in a background thread, streaming output to the session-state log.

    Sets _KEY_RUNNING=False and _KEY_SUCCESS=True/False when the process finishes.
    """
    python = _find_python()
    cmd = [python, script_path] + args

    def _worker():
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(_app_root()),
            )
            for line in proc.stdout:
                _append_log(line.rstrip())
            proc.wait()
            st.session_state[_KEY_SUCCESS] = (proc.returncode == 0)
        except Exception as e:
            _append_log(f"FEHLER: {e}")
            st.session_state[_KEY_SUCCESS] = False
        finally:
            st.session_state[_KEY_RUNNING] = False

    st.session_state[_KEY_RUNNING] = True
    st.session_state[_KEY_SUCCESS] = None
    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Wizard-Schritte
# ---------------------------------------------------------------------------

_STEPS = [
    "Willkommen",
    "Ticker-Datenbank anlegen",
    "Erste Preisdaten laden",
    "Fertig",
]


def _step_welcome(col):
    """Render the welcome/disclaimer step of the setup wizard."""
    col.title("🚀 Willkommen bei Silvesto, your portal for growth.")
    col.markdown("""
Diese App benötigt beim ersten Start eine kurze Einrichtung:

| Schritt | Was passiert |
|---------|-------------|
| 1 | Ticker-Datenbank mit DAX, MDAX, S&P 500 wird angelegt |
| 2 | Erste Tages-Kursdaten werden geladen |

**Dauer:** ca. 2–5 Minuten (abhängig von der Internetverbindung).
    """)

    col.divider()
    col.subheader("⚖️ Rechtlicher Hinweis / Legal Disclaimer")
    col.markdown("""
**DE** — Diese App ist ein persönliches Werkzeug für den privaten, nicht-kommerziellen Gebrauch.
Sie enthält selbst **keine Marktdaten**. Kursdaten werden zur Laufzeit von Drittanbietern
(z. B. Yahoo Finance, Financial Modeling Prep) abgerufen. Jeder Nutzer ist **selbst verantwortlich**
für die Einhaltung der jeweiligen Nutzungsbedingungen dieser Anbieter.
Die App dient ausschließlich zu Informations- und Analysezwecken und stellt
**keine Anlage- oder Finanzberatung** dar. Handlungen auf Basis der angezeigten Daten
erfolgen auf eigene Verantwortung und eigenes Risiko.

---

**EN** — This app is a personal tool for private, non-commercial use.
It contains **no market data** of its own. Price data is fetched at runtime from third-party
providers (e.g. Yahoo Finance, Financial Modeling Prep). Each user is **solely responsible**
for complying with the terms of service of those providers.
The app is provided for informational and analytical purposes only and does
**not constitute investment or financial advice**. Any actions taken based on the
information displayed are at the user's own responsibility and risk.
    """)

    accepted = col.checkbox(
        "Ich habe den Haftungsausschluss gelesen und akzeptiere ihn. / "
        "I have read and accept the disclaimer.",
        key="_disclaimer_accepted",
    )
    col.divider()

    if col.button("▶ Einrichtung starten", type="primary",
                  use_container_width=True, disabled=not accepted):
        st.session_state[_KEY_STEP] = 1
        st.rerun()


def _step_seed_db(col):
    """Render step 1: run seed_db.py in the background and show live log output."""
    col.subheader("Schritt 1 — Ticker-Datenbank anlegen")

    root = _app_root()
    seed_script = str(root / "seed_db.py")

    # Automatisch starten, wenn noch nicht gestartet
    if not st.session_state[_KEY_RUNNING] and st.session_state[_KEY_SUCCESS] is None:
        st.session_state[_KEY_LOG] = []
        _run_script_in_thread(seed_script, [])
        st.rerun()

    # Log anzeigen
    log_text = "\n".join(st.session_state[_KEY_LOG]) or "Bitte warten …"
    col.code(log_text, language="text")

    if st.session_state[_KEY_RUNNING]:
        col.info("⏳ Datenbank wird angelegt …")
        time.sleep(1)
        st.rerun()
    elif st.session_state[_KEY_SUCCESS] is True:
        col.success("✅ Ticker-Datenbank erfolgreich angelegt.")
        if col.button("Weiter →", type="primary", use_container_width=True):
            st.session_state[_KEY_STEP] = 2
            st.session_state[_KEY_SUCCESS] = None
            st.rerun()
    else:
        col.error("❌ Fehler beim Anlegen der Datenbank. Siehe Log oben.")
        if col.button("Nochmal versuchen", use_container_width=True):
            st.session_state[_KEY_SUCCESS] = None
            st.rerun()


def _step_price_data(col):
    """Render step 2: run get_asset_data.py for initial daily price data."""
    col.subheader("Schritt 2 — Erste Preisdaten laden")
    col.markdown("""
Es werden Tages-Kursdaten (`1d:1mo`) für alle Ticker geladen.
Das kann einige Minuten dauern.
    """)

    root = _app_root()
    data_script = str(root / "get_asset_data.py")

    if not st.session_state[_KEY_RUNNING] and st.session_state[_KEY_SUCCESS] is None:
        st.session_state[_KEY_LOG] = []
        # index_member = nur Indizes-Mitglieder, 1d:1mo = Tages-Daten, 1 Monat
        _run_script_in_thread(data_script, ["/index_member", "1d:1mo"])
        st.rerun()

    log_lines = st.session_state[_KEY_LOG]
    # Nur die letzten 20 Zeilen zeigen (kann sehr lang werden)
    log_text = "\n".join(log_lines[-20:]) or "Bitte warten …"
    col.code(log_text, language="text")
    if log_lines:
        col.caption(f"{len(log_lines)} Zeilen insgesamt")

    if st.session_state[_KEY_RUNNING]:
        col.info("⏳ Preisdaten werden geladen …")
        time.sleep(2)
        st.rerun()
    elif st.session_state[_KEY_SUCCESS] is True:
        col.success("✅ Preisdaten erfolgreich geladen.")
        if col.button("Weiter →", type="primary", use_container_width=True):
            st.session_state[_KEY_STEP] = 3
            st.session_state[_KEY_SUCCESS] = None
            st.rerun()
    else:
        col.error("❌ Fehler beim Laden der Preisdaten.")
        c1, c2 = col.columns(2)
        if c1.button("Nochmal versuchen", use_container_width=True):
            st.session_state[_KEY_SUCCESS] = None
            st.rerun()
        if c2.button("Überspringen (später nachholen)", use_container_width=True):
            # Preisdaten können später über den Scheduler nachgeladen werden
            st.session_state[_KEY_STEP] = 3
            st.session_state[_KEY_SUCCESS] = None
            st.rerun()


def _step_done(col):
    """Render the final step: success message and 'Start app' button."""
    col.title("🎉 Einrichtung abgeschlossen!")
    col.markdown("""
Die Trading App ist jetzt einsatzbereit.

**Tipp:** Vollständige historische Daten können über den integrierten
Scheduler oder manuell über `get_asset_data.py` nachgeladen werden.
    """)
    if col.button("▶ App starten", type="primary", use_container_width=True):
        st.session_state[_KEY_DONE] = True
        # Session-State aufräumen
        for k in [_KEY_STEP, _KEY_LOG, _KEY_RUNNING, _KEY_SUCCESS]:
            st.session_state.pop(k, None)
        st.rerun()


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------

def maybe_show_setup() -> bool:
    """Check whether first-time setup is needed and show the wizard if so.

    Returns True when the wizard was rendered (caller should call st.stop()),
    or False when setup is complete and the app can proceed normally.
    """
    _init_state()

    # Bereits abgeschlossen (in dieser Session)?
    if st.session_state.get(_KEY_DONE, False):
        return False

    # DB vorhanden → kein Setup nötig
    if _tickers_db_exists():
        return False

    # --------------- Wizard rendern ---------------
    # Zentrierte Spalte (40 % Breite)
    _, col, _ = st.columns([0.3, 0.4, 0.3])

    step = st.session_state[_KEY_STEP]

    # Fortschrittsanzeige
    col.progress((step) / (len(_STEPS) - 1), text=f"Schritt {step} von {len(_STEPS)-1}: {_STEPS[step]}")

    if step == 0:
        _step_welcome(col)
    elif step == 1:
        _step_seed_db(col)
    elif step == 2:
        _step_price_data(col)
    elif step >= 3:
        _step_done(col)

    return True  # Wizard aktiv → Aufrufer soll st.stop() aufrufen


# ---------------------------------------------------------------------------
# config.yaml — muss VOR TradingApp().__init__() existieren
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_YAML = """\
# Trading App - Authentifizierungs-Konfiguration
# Standard-Login: admin / changeme
# Passwort bitte nach dem ersten Login aendern!
#
# Neuen Passwort-Hash erzeugen:
#   python -c "import bcrypt; print(bcrypt.hashpw(b'NEUESPASSWORT', bcrypt.gensalt()).decode())"

credentials:
  usernames:
    admin:
      email: admin@localhost
      name: Administrator
      password: {pw_hash}

cookie:
  expiry_days: 30
  key: {cookie_key}
  name: trading_app_auth

preauthorized:
  emails: []
"""


def ensure_config_yaml() -> bool:
    """Create a default config.yaml if one does not exist yet.

    Must be called before TradingApp() is instantiated because TradingApp.__init__
    opens the file immediately. Uses the same path resolver as TradingApp.load_config()
    so the TradingDB environment variable is handled consistently.

    Returns True when the file was newly created, False when it already existed.
    """
    try:
        from tradinglib.tools import Tools
        config_path = Path(Tools().get_path('', 'config.yaml'))
    except Exception:
        # Fallback falls tools nicht importierbar
        config_path = _app_root() / "config.yaml"

    if config_path.exists():
        return False

    try:
        import bcrypt
        pw_hash = bcrypt.hashpw(b"changeme", bcrypt.gensalt()).decode()
    except ImportError:
        # bcrypt nicht verfügbar — Klartext-Fallback (funktioniert mit
        # streamlit-authenticator nur wenn es intern bcrypt aufruft)
        pw_hash = "$2b$12$BpqOS0p/5FOARlHbxZ3rWuyciiZYtqSWmzXwD/yLcpfzGMn3YzQeG"

    # Pro-Installation einen zufälligen Cookie-Signing-Key erzeugen. Der Key
    # signiert das Auth-JWT (HS256) — ein fester Default-Wert im öffentlichen
    # Repo würde es jedem erlauben, gültige Admin-Cookies selbst zu fälschen.
    import secrets
    cookie_key = secrets.token_hex(32)

    content = _DEFAULT_CONFIG_YAML.format(pw_hash=pw_hash, cookie_key=cookie_key)

    try:
        config_path.write_text(content, encoding="utf-8")
        logger.info("config.yaml angelegt: %s", config_path)
        return True
    except OSError as e:
        logger.error("Konnte config.yaml nicht anlegen: %s", e)
        return False
