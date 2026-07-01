"""recalc_index.py — Mitglieder *eines* Index neu berechnen, ohne die
bereits berechneten Werte der anderen Indizes/Ticker anzufassen.

Hintergrund
-----------
Ein einfacher Re-Run von ``asset_perf2.py /index:^NAME /init`` rechnet zwar nur
die Mitglieder dieses einen Index, ueberschreibt aber bereits vorhandene
``(ticker, Date)``-Zeilen NICHT: die Schreib-Routine haengt neue Zeilen an
(``insert_data(replace=False)``) und die anschliessende Dedup behaelt das
Minimum der ``rowid`` pro ``(ticker, Date)`` — also die *alten* (aus der Datei
geladenen) Werte. Die Neuberechnung wird dadurch still verworfen.

Dieses Skript loescht deshalb erst die betroffenen Zeilen im Ziel-DB und startet
dann den normalen ``asset_perf2.py``-Lauf. Alle uebrigen Ticker bleiben
unangetastet, weil ``asset_perf2`` nur die Mitglieder von ``/index:NAME``
berechnet und die Memory-DB die bestehende Datei vollstaendig ein- und
zurueckschreibt.

Aufruf
------
    python recalc_index.py /index:^GDAXI                 # aktuelles Jahr, ab 01.01.
    python recalc_index.py /index:^GDAXI /year:2025      # komplettes Jahr 2025
    python recalc_index.py /index:^SPX /worker:2 /add_current
    python recalc_index.py /index:^GDAXI /dry_run        # nur anzeigen, nichts aendern

Optionen werden an asset_perf2.py durchgereicht: /worker:N, /add_current, /silent.
"""

import os
import sys
import subprocess
from datetime import datetime

from tradinglib import cli
from tradinglib.tools import Tools as _Tools, open_db


def _sim_db_name(year) -> str:
    """Ziel-DB analog asset_perf2: aktuelles Jahr -> asset_simulation_.db,
    sonst asset_simulation_{year}.db."""
    return "asset_simulation_.db" if year == '' else f"asset_simulation_{year}.db"


def _date_range(year):
    """(start, end) als 'YYYY-MM-DD 00:00:00'-Strings analog process_symbol().

    Aktuelles Jahr (year='') -> 01.01. dieses Jahres bis heute (= /init-Verhalten).
    /year:YYYY -> 01.01.YYYY bis 31.12.YYYY.
    """
    fmt = "%Y-%m-%d 00:00:00"
    if year == '':
        now = datetime.now()
        start = f"{now.year}-01-01 00:00:00"
        end = now.strftime(fmt)
    else:
        start = f"{year}-01-01 00:00:00"
        end = f"{year}-12-31 00:00:00"
    return start, end


def _index_tickers(index_name: str) -> list[str]:
    """Mitglieder-Ticker eines Index aus yf_tickers.db (gleiche Query wie
    asset_perf2.py /index:NAME)."""
    tickers_db = _Tools().get_path(path='database', file_name='yf_tickers.db')
    conn = open_db(tickers_db, readonly=True)
    try:
        rows = conn.execute(
            "SELECT s.Ticker FROM stocks s "
            "JOIN stock_indices si ON s.id = si.stock_id "
            "JOIN indices i ON si.index_id = i.id "
            "WHERE i.name = ?",
            (index_name,),
        ).fetchall()
    finally:
        conn.close()
    return sorted({r[0] for r in rows})


def _delete_existing(sim_db_path: str, tickers: list[str],
                     start: str, end: str, dry_run: bool) -> int:
    """Loescht vorhandene asset_simulation-Zeilen der gegebenen Ticker im
    Zeitraum [start, end]. Gibt die Anzahl betroffener Zeilen zurueck."""
    if not os.path.exists(sim_db_path):
        print(f"  Ziel-DB existiert noch nicht ({sim_db_path}) — nichts zu loeschen.")
        return 0
    if not tickers:
        return 0

    conn = open_db(sim_db_path)
    try:
        placeholders = ",".join("?" for _ in tickers)
        where = (f"ticker IN ({placeholders}) AND Date >= ? AND Date <= ?")
        params = (*tickers, start, end)

        n = conn.execute(
            f"SELECT COUNT(*) FROM asset_simulation WHERE {where}", params
        ).fetchone()[0]

        if dry_run:
            print(f"  [dry-run] wuerde {n} Zeilen loeschen.")
            return n

        conn.execute(f"DELETE FROM asset_simulation WHERE {where}", params)
        conn.commit()
        print(f"  {n} alte Zeilen geloescht.")
        return n
    except Exception as e:
        # Tabelle evtl. noch nicht vorhanden -> kein Abbruch, asset_perf2 legt sie an
        print(f"  Hinweis: Loeschen uebersprungen ({e}).")
        return 0
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) <= 1:
        print(__doc__)
        return 1

    args = cli.parse_args(sys.argv)
    index_name = args.get('index_name', '')
    year = args.get('year', '')
    dry_run = any(a.lower().lstrip('/') == 'dry_run' for a in sys.argv[1:])

    if not index_name:
        print("Fehler: /index:^NAME ist erforderlich (z.B. /index:^GDAXI).")
        return 1

    sim_db_name = _sim_db_name(year)
    sim_db_path = _Tools().get_path(path='database', file_name=sim_db_name)
    start, end = _date_range(year)
    tickers = _index_tickers(index_name)

    print(f"Index   : {index_name}")
    print(f"Ziel-DB : {sim_db_path}")
    print(f"Zeitraum: {start[:10]} .. {end[:10]}")
    print(f"Ticker  : {len(tickers)} Mitglieder")
    if not tickers:
        print("Keine Mitglieder fuer diesen Index gefunden — Abbruch.")
        return 1

    print("Schritt 1/2: alte Zeilen loeschen ...")
    _delete_existing(sim_db_path, tickers, start, end, dry_run)

    # asset_perf2-Aufruf zusammenbauen (gleiches Python = gleiches venv)
    perf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset_perf2.py")
    cmd = [sys.executable, perf, f"/index:{index_name}"]
    if year == '':
        cmd.append("/init")
    else:
        cmd.append(f"/year:{year}")
    # durchgereichte Optionen
    if args.get('worker', 0):
        cmd.append(f"/worker:{args['worker']}")
    if args.get('add_current'):
        cmd.append("/add_current")
    if args.get('silent'):
        cmd.append("/silent")

    print(f"Schritt 2/2: Neuberechnung -> {' '.join(cmd[1:])}")
    if dry_run:
        print("  [dry-run] asset_perf2.py wird NICHT gestartet.")
        return 0

    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
