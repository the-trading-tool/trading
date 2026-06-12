"""
migrate_asset_groups.py — sortiert Nicht-Index-Instrumente aus der INDEX-Gruppe
in die neuen Asset-Klassen-Gruppen um.

Regeln (in dieser Reihenfolge):
  ^…                       → bleibt INDEX
  GC=F/SI=F/HG=F/PL=F/PA=F → METALS
  …=X                      → CURRENCIES
  …-EUR / …-USD / …-USDT   → CRYPTO
  …=F                      → COMMODITIES
  alles andere             → bleibt INDEX (wird gemeldet)

Zusätzlich wird eine vorhandene ETF-Gruppe in ETP umbenannt
(bzw. mit einer bereits vorhandenen ETP-Gruppe zusammengeführt).

Ziel-DB: %TradingDB%\\yf_tickers.db, sonst ./database/yf_tickers.db
Aufruf : python migrate_asset_groups.py [--dry-run]
Das Skript ist idempotent — ein zweiter Lauf ändert nichts mehr.
"""

import os
import sys
from pathlib import Path

from tradinglib.tools import open_db

DRY_RUN = '--dry-run' in sys.argv

METAL_TICKERS = {'GC=F', 'SI=F', 'HG=F', 'PL=F', 'PA=F'}
CRYPTO_SUFFIXES = ('-EUR', '-USD', '-USDT')


def get_db_path() -> str:
    tdb = os.environ.get('TradingDB', '')
    base = Path(tdb) if tdb else Path(sys.argv[0]).resolve().parent / 'database'
    return str(base / 'yf_tickers.db')


def classify(ticker: str) -> str:
    """Zielgruppe für einen Ticker aus der bisherigen INDEX-Gruppe."""
    if ticker.startswith('^'):
        return 'INDEX'
    if ticker in METAL_TICKERS:
        return 'METALS'
    if ticker.endswith('=X'):
        return 'CURRENCIES'
    if ticker.endswith(CRYPTO_SUFFIXES):
        return 'CRYPTO'
    if ticker.endswith('=F'):
        return 'COMMODITIES'
    return 'INDEX'


def get_group_id(cursor, name: str, create: bool = False):
    row = cursor.execute('SELECT id FROM indices WHERE name = ?', (name,)).fetchone()
    if row:
        return row[0]
    if not create:
        return None
    cursor.execute('INSERT INTO indices (name) VALUES (?)', (name,))
    return cursor.execute('SELECT id FROM indices WHERE name = ?', (name,)).fetchone()[0]


def rename_etf_to_etp(cursor) -> str:
    etf_id = get_group_id(cursor, 'ETF')
    if etf_id is None:
        return 'ETF-Gruppe nicht vorhanden — nichts umzubenennen.'
    etp_id = get_group_id(cursor, 'ETP')
    if etp_id is None:
        cursor.execute('UPDATE indices SET name = ? WHERE id = ?', ('ETP', etf_id))
        return 'ETF → ETP umbenannt.'
    # Beide vorhanden: ETF-Mitglieder nach ETP mergen, ETF-Gruppe löschen.
    cursor.execute(
        'INSERT OR IGNORE INTO stock_indices (stock_id, index_id) '
        'SELECT stock_id, ? FROM stock_indices WHERE index_id = ?',
        (etp_id, etf_id),
    )
    cursor.execute('DELETE FROM stock_indices WHERE index_id = ?', (etf_id,))
    cursor.execute('DELETE FROM indices WHERE id = ?', (etf_id,))
    return 'ETF-Mitglieder in vorhandene ETP-Gruppe gemerged, ETF-Gruppe entfernt.'


def main():
    db_path = get_db_path()
    print(f'Ziel-DB : {db_path}')
    if not Path(db_path).exists():
        print('DB nicht gefunden — Abbruch.')
        sys.exit(1)

    with open_db(db_path) as conn:
        cursor = conn.cursor()

        members = cursor.execute(
            'SELECT s.id, s.Ticker FROM stocks s '
            'JOIN stock_indices si ON s.id = si.stock_id '
            'JOIN indices i ON si.index_id = i.id '
            "WHERE i.name = 'INDEX' ORDER BY s.Ticker"
        ).fetchall()

        plan: dict[str, list[tuple[int, str]]] = {}
        for stock_id, ticker in members:
            target = classify(ticker)
            if target != 'INDEX':
                plan.setdefault(target, []).append((stock_id, ticker))

        staying = [t for _, t in members if classify(t) == 'INDEX']
        print(f'INDEX-Mitglieder: {len(members)}, davon bleiben: {len(staying)}')
        for group, items in sorted(plan.items()):
            print(f'  -> {group:12s} ({len(items)}): {", ".join(t for _, t in items)}')

        if DRY_RUN:
            print('[dry-run] Keine Änderungen geschrieben.')
            print('[dry-run] ETF-Gruppe wuerde in ETP umbenannt/gemerged (falls vorhanden).')
            return

        index_id = get_group_id(cursor, 'INDEX')
        moved = 0
        for group, items in plan.items():
            group_id = get_group_id(cursor, group, create=True)
            for stock_id, _ in items:
                cursor.execute(
                    'INSERT OR IGNORE INTO stock_indices (stock_id, index_id) VALUES (?, ?)',
                    (stock_id, group_id),
                )
                cursor.execute(
                    'DELETE FROM stock_indices WHERE stock_id = ? AND index_id = ?',
                    (stock_id, index_id),
                )
                moved += 1

        msg = rename_etf_to_etp(cursor)
        conn.commit()
        print(f'Fertig — {moved} Ticker umgruppiert. {msg}')


if __name__ == '__main__':
    main()
