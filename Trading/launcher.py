"""
launcher.py — Windows-Launcher für die Trading App.

Kompilierung zu einer einzelnen .exe:
    pip install pyinstaller
    pyinstaller --onefile --noconsole --icon=app/icon.ico ^
                --name "Trading App" launcher.py

Was dieser Launcher tut:
  1. Findet Python relativ zu sich selbst (portables Bundle)
     oder fällt auf das System-Python zurück.
  2. Startet `streamlit run asset_analyzer.py` als Kindprozess.
  3. Wartet bis der Server auf localhost:8501 antwortet.
  4. Öffnet den Standard-Browser.
  5. Zeigt ein System-Tray-Icon mit "Beenden"-Menü (optional).
  6. Beendet den Streamlit-Prozess, wenn der Launcher geschlossen wird.

Abhängigkeiten (nur für den Launcher selbst, nicht für die App):
    pip install pystray pillow
    (pystray/Pillow sind optional — ohne sie läuft der Launcher auch,
     aber ohne Tray-Icon)
"""

import os
import sys
import subprocess
import time
import webbrowser
import urllib.request
import urllib.error
import signal
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

PORT = 8501
APP_SCRIPT = "asset_analyzer.py"
STARTUP_TIMEOUT = 60       # Sekunden, bevor Fehler gemeldet wird
POLL_INTERVAL  = 0.5       # Sekunden zwischen Server-Checks

# ---------------------------------------------------------------------------
# Pfad-Ermittlung
# ---------------------------------------------------------------------------

def get_base_dir() -> Path:
    """Verzeichnis, in dem der Launcher (oder die .exe) liegt."""
    if getattr(sys, 'frozen', False):
        # PyInstaller-Bundle: sys.executable ist die .exe
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def find_python(base: Path) -> str:
    """
    Sucht das Python-Executable.
    Reihenfolge:
      1. <base>/python/python.exe  (portables Bundle)
      2. <base>/.venv/Scripts/python.exe  (lokales venv)
      3. sys.executable  (dasselbe Python, das den Launcher ausführt)
      4. 'python' im PATH
    """
    candidates = [
        base / "python"  / "python.exe",
        base / ".venv"   / "Scripts" / "python.exe",
        base / "venv"    / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    # Im gefrorenen Bundle ist sys.executable die Launcher-.exe — kein Python.
    # Im Entwicklungsmodus ist es das echte Python.
    if not getattr(sys, 'frozen', False):
        return sys.executable
    return "python"   # letzter Ausweg: PATH


# ---------------------------------------------------------------------------
# Erster Start (Flag-Datei)
# ---------------------------------------------------------------------------

def is_first_run(base: Path) -> bool:
    return not (base / "database" / "yf_tickers.db").exists()


def run_seed(python: str, base: Path):
    """Führt seed_db.py synchron aus und zeigt den Output im Terminal."""
    seed_script = base / "seed_db.py"
    if not seed_script.exists():
        print("[Launcher] seed_db.py nicht gefunden — übersprungen.")
        return
    print("[Launcher] Ersteinrichtung: Ticker-Datenbank wird angelegt …")
    result = subprocess.run(
        [python, str(seed_script)],
        cwd=str(base),
    )
    if result.returncode != 0:
        print(f"[Launcher] WARNUNG: seed_db.py endete mit Code {result.returncode}")
    else:
        print("[Launcher] Ticker-Datenbank erfolgreich angelegt.")


# ---------------------------------------------------------------------------
# Streamlit starten und auf Bereitschaft warten
# ---------------------------------------------------------------------------

def start_streamlit(python: str, base: Path) -> subprocess.Popen:
    app = base / APP_SCRIPT
    cmd = [
        python, "-m", "streamlit", "run", str(app),
        f"--server.port={PORT}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    print(f"[Launcher] Starte: {' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=str(base))


def wait_for_server(timeout: int = STARTUP_TIMEOUT) -> bool:
    """Pollt http://localhost:<PORT>, bis er antwortet oder Timeout."""
    url = f"http://localhost:{PORT}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(POLL_INTERVAL)
    return False


# ---------------------------------------------------------------------------
# System-Tray-Icon (optional)
# ---------------------------------------------------------------------------

def run_tray_icon(stop_callback):
    """
    Zeigt ein System-Tray-Icon mit "Trading App beenden".
    Wird in einem eigenen Thread gestartet.
    Benötigt: pip install pystray pillow
    """
    try:
        import pystray
        from PIL import Image, ImageDraw

        # Einfaches Icon: grünes Quadrat mit weißem "T"
        img = Image.new("RGBA", (64, 64), (0, 120, 60, 255))
        draw = ImageDraw.Draw(img)
        draw.text((18, 16), "T", fill="white")

        def on_quit(icon, item):
            icon.stop()
            stop_callback()

        menu = pystray.Menu(
            pystray.MenuItem("Trading App läuft …", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", on_quit),
        )
        icon = pystray.Icon("TradingApp", img, "Trading App", menu)
        icon.run()

    except ImportError:
        # pystray/Pillow nicht installiert — kein Tray-Icon, kein Problem
        pass
    except Exception as e:
        print(f"[Launcher] Tray-Icon konnte nicht gestartet werden: {e}")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main():
    base   = get_base_dir()
    python = find_python(base)

    print(f"[Launcher] Basis-Verzeichnis : {base}")
    print(f"[Launcher] Python            : {python}")

    # Erster Start?
    if is_first_run(base):
        run_seed(python, base)

    # Streamlit starten
    proc = start_streamlit(python, base)

    # Warten bis der Server antwortet
    print(f"[Launcher] Warte auf http://localhost:{PORT} …")
    if wait_for_server():
        print("[Launcher] Server bereit — Browser wird geöffnet.")
        webbrowser.open(f"http://localhost:{PORT}")
    else:
        print(f"[Launcher] FEHLER: Server hat nach {STARTUP_TIMEOUT}s nicht geantwortet.")

    # Tray-Icon in eigenem Thread (damit main() nicht blockiert)
    def shutdown():
        print("[Launcher] Beenden …")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        os._exit(0)

    tray_thread = threading.Thread(target=run_tray_icon, args=(shutdown,), daemon=True)
    tray_thread.start()

    # Auf Streamlit-Prozess warten — wenn der endet, endet auch der Launcher
    try:
        proc.wait()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
