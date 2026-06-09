"""
seed_db.py — erstellt database/yf_tickers.db mit Beispiel-Tickern.

Enthält:
  GDAXI  — DAX 40 (Stand: 2025)
  MDAXI  — MDAX-Auswahl (~20 Titel)
  SDAXI  — SDAX-Auswahl (~14 Titel)
  SPX    — S&P-500-Auswahl (40 Blue Chips)
  INDEX  — Index-Instrumente selbst (^GDAXI, ^GSPC …)

Aufruf: python seed_db.py [--dry-run]
"""

import sqlite3
import sys
import os
from datetime import datetime
from pathlib import Path
from tradinglib.tools import open_db

DRY_RUN = '--dry-run' in sys.argv

# ---------------------------------------------------------------------------
# Ticker-Listen
# ---------------------------------------------------------------------------

# DAX 40 (GDAXI) — Stand 2025
GDAXI = [
    'ADS.DE',  'AIR.DE',  'ALV.DE',  'BAS.DE',  'BAYN.DE', 'BEI.DE',
    'BMW.DE',  'BNR.DE',  'CBK.DE',  'CON.DE',  'DB1.DE',  'DBK.DE',
    'DHL.DE',  'DTE.DE',  'EOAN.DE', 'FME.DE',  'FRE.DE',  'HEI.DE',
    'HEN3.DE', 'HNR1.DE', 'IFX.DE',  'KGX.DE',  'MBG.DE',  'MRK.DE',
    'MTX.DE',  'MUV2.DE', 'P911.DE', 'PAH3.DE', 'QIA.DE',  'RHM.DE',
    'RWE.DE',  'SAP.DE',  'SHL.DE',  'SIE.DE',  'ENR.DE',  'SRT3.DE',
    'SY1.DE',  'VOW3.DE', 'VNA.DE',  'ZAL.DE',
]

# MDAX-Auswahl (~20 Titel, repräsentative Auswahl)
MDAXI = [
    'AIXA.DE',  # Aixtron
    'BOSS.DE',  # Hugo Boss
    'DHER.DE',  # Delivery Hero
    'DWS.DE',   # DWS Group
    'EVD.DE',   # CTS Eventim
    'FNTN.DE',  # freenet
    'HLE.DE',   # Hella/FORVIA
    'LXS.DE',   # Lanxess
    'NDX1.DE',  # Nordex
    'NEM.DE',   # Nemetschek
    'O2D.DE',   # Telefonica Deutschland
    'PUM.DE',   # Puma
    'RTL.DE',   # RTL Group
    'S92.DE',   # SMA Solar
    'TKA.DE',   # thyssenkrupp
    'TUI1.DE',  # TUI
    'UTDI.DE',  # United Internet
    'WAF.DE',   # Siltronic
]

# SDAX-Auswahl (~12 Titel)
SDAXI = [
    'AFX.DE',    # Carl Zeiss Meditec
    'BVB.DE',    # Borussia Dortmund
    'GXI.DE',    # Gerresheimer
    'KSB3.DE',   # KSB SE (Vorzüge)
    'MBB.DE',    # MBB Industries
    'NDA.DE',    # Aurubis
    'SBS.DE',    # Stabilus
    'STO3.DE',   # Sto SE (Vorzüge)
    'TTK.DE',    # Takkt AG
]

# S&P 500 — 40 Blue Chips
SPX = [
    'AAPL',  'MSFT',  'AMZN',  'GOOGL', 'META',  'NVDA',  'TSLA',  'BRK-B',
    'LLY',   'JPM',   'V',     'MA',    'UNH',   'AVGO',  'HD',    'PG',
    'XOM',   'COST',  'JNJ',   'ABBV',  'BAC',   'NFLX',  'CRM',   'MRK',
    'ORCL',  'CVX',   'AMD',   'KO',    'PEP',   'WMT',   'TMO',   'CSCO',
    'ACN',   'ADBE',  'DIS',   'QCOM',  'INTC',  'TXN',   'NKE',   'GS',
]

# Index-Instrumente (werden von der Simulation ausgeschlossen)
INDEX = [
    '^GDAXI',  # DAX 40
    '^MDAXI',  # MDAX
    '^SDAXI',  # SDAX
    '^GSPC',   # S&P 500
    '^DJI',    # Dow Jones
    '^IXIC',   # NASDAQ Composite
    '^VIX',    # CBOE Volatility Index
]

ALL_GROUPS = {
    'GDAXI': GDAXI,
    'MDAXI': MDAXI,
    'SDAXI': SDAXI,
    'SPX':   SPX,
    'INDEX': INDEX,
}

# ---------------------------------------------------------------------------
# DB-Pfad — analog zu tools.get_path()
# ---------------------------------------------------------------------------

def get_db_path() -> str:
    tdb = os.environ.get('TradingDB', '')
    if tdb:
        base = Path(tdb)
    else:
        base = Path(sys.argv[0]).resolve().parent / 'database'
    base.mkdir(exist_ok=True)
    return str(base / 'yf_tickers.db')


# ---------------------------------------------------------------------------
# Schema + Seed
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    Ticker   TEXT NOT NULL,
    Date     TEXT,
    INVESTED REAL,
    ISIN     TEXT,
    UNIQUE(Ticker)
);

CREATE TABLE IF NOT EXISTS indices (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_indices (
    stock_id  INTEGER NOT NULL,
    index_id  INTEGER NOT NULL,
    PRIMARY KEY (stock_id, index_id),
    FOREIGN KEY (stock_id)  REFERENCES stocks(id),
    FOREIGN KEY (index_id)  REFERENCES indices(id)
);
"""


def seed(conn: sqlite3.Connection):
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    inserted = 0
    skipped  = 0

    for index_name, tickers in ALL_GROUPS.items():
        cursor.execute('INSERT OR IGNORE INTO indices (name) VALUES (?)', (index_name,))
        cursor.execute('SELECT id FROM indices WHERE name = ?', (index_name,))
        index_id = cursor.fetchone()[0]

        for ticker in tickers:
            cursor.execute(
                'INSERT OR IGNORE INTO stocks (Ticker, Date) VALUES (?, ?)',
                (ticker, date_str),
            )
            cursor.execute('SELECT id FROM stocks WHERE Ticker = ?', (ticker,))
            stock_id = cursor.fetchone()[0]

            cursor.execute(
                'INSERT OR IGNORE INTO stock_indices (stock_id, index_id) VALUES (?, ?)',
                (stock_id, index_id),
            )

            if cursor.rowcount:
                inserted += 1
            else:
                skipped += 1

    conn.commit()
    return inserted, skipped


def get_config_db_path() -> str:
    tdb = os.environ.get('TradingDB', '')
    if tdb:
        base = Path(tdb)
    else:
        base = Path(sys.argv[0]).resolve().parent / 'database'
    base.mkdir(exist_ok=True)
    return str(base / 'config.db')


def seed_config(conn: sqlite3.Connection):
    """Seed config.db with FMP as the default data provider (free-version default)."""
    import json
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
        ("_app:data_provider", json.dumps("fmp")),
    )
    conn.commit()


def main():
    db_path = get_db_path()
    total   = sum(len(v) for v in ALL_GROUPS.values())
    print(f"Ziel-DB : {db_path}")
    print(f"Ticker  : {total} in {len(ALL_GROUPS)} Indizes")

    if DRY_RUN:
        print("[dry-run] Keine Änderungen geschrieben.")
        for name, tickers in ALL_GROUPS.items():
            print(f"  {name:8s} ({len(tickers):3d}): {', '.join(tickers[:5])} …")
        return

    with open_db(db_path) as conn:
        inserted, skipped = seed(conn)

    print(f"Fertig — {inserted} neu eingetragen, {skipped} bereits vorhanden.")

    cfg_path = get_config_db_path()
    print(f"Config-DB: {cfg_path}")
    with open_db(cfg_path) as conn:
        seed_config(conn)
    print("Config geseedet — Standard-Provider: FMP.")


if __name__ == '__main__':
    main()
