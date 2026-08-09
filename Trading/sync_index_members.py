"""Reconcile index membership in yf_tickers.db against the current constituents.

Membership used to come from All_Assets.xlsx via read_stock_list(). That file is
gone, so the lists have been frozen since the 2024-08-28 import while the real
indices kept turning over -- takeovers leave, new companies join. Stale
membership quietly skews everything built on an index universe (screeners,
sector rotation, breadth).

Departing members are only UNLINKED from the index, never deleted: the ticker
row, its prices and any past trades stay intact. Leaving an index is not the
same as being delisted -- a company dropping out of the S&P 500 usually keeps
trading -- so departures deliberately do not touch tradinglib.asset_status.

Symbols are validated against the data source before they are added, so a
parsing slip cannot seed junk tickers.

Usage:
    python sync_index_members.py                 # dry run for ^SPX
    python sync_index_members.py /index:^SPX /apply
    python sync_index_members.py /index:^SPX /apply /nocheck   # skip validation
"""
import io
import logging
import sys
import urllib.request
from datetime import datetime

import pandas as pd

from tradinglib import cli, logging_config
from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

# Per index: where the constituent list lives and which table/column holds it.
# Wikipedia's list articles are the conventional public source for these and
# carry a stable "Symbol" column.
SOURCES = {
    '^SPX': {
        'url': 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
        'table': 0,
        'column': 'Symbol',
        'name': 'S&P 500',
        'min': 400,
    },
    '^NDX': {
        # The Nasdaq-100 article itself no longer carries the constituents --
        # they live in this separate list article.
        'url': 'https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies',
        'table': 0,
        'column': 'Ticker',
        'name': 'Nasdaq-100',
        # 101 securities: Alphabet is in with both share classes.
        'min': 90,
    },
    '^DJI': {
        'url': 'https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average',
        'table': 1,
        'column': 'Symbol',
        'name': 'Dow Jones Industrial Average',
        'min': 25,
    },
}

USER_AGENT = 'Mozilla/5.0 (compatible; trading-app index sync)'


def to_yahoo_symbol(symbol):
    """Wikipedia writes class shares with a dot, Yahoo with a hyphen."""
    return str(symbol).strip().upper().replace('.', '-')


def fetch_constituents(index_name):
    """Current constituent symbols for one index, in Yahoo notation."""
    src = SOURCES.get(index_name)
    if not src:
        raise SystemExit(f"no constituent source configured for {index_name} "
                         f"(known: {', '.join(sorted(SOURCES))})")
    req = urllib.request.Request(src['url'], headers={'User-Agent': USER_AGENT})
    html = urllib.request.urlopen(req, timeout=30).read().decode('utf-8')
    table = pd.read_html(io.StringIO(html))[src['table']]
    if src['column'] not in table.columns:
        raise SystemExit(f"column {src['column']!r} missing — page layout changed; "
                         f"found: {list(table.columns)}")
    symbols = {to_yahoo_symbol(s) for s in table[src['column']].dropna()}
    # A layout change could yield a handful of rows and silently wipe the index,
    # so each source states the count below which the result is not credible.
    minimum = src.get('min', 50)
    if len(symbols) < minimum:
        raise SystemExit(f"only {len(symbols)} symbols parsed, expected at least "
                         f"{minimum} — page layout changed; refusing to sync")
    return symbols


def db_members(conn, index_name):
    """Tickers currently linked to the index."""
    return {r[0] for r in conn.execute(
        'SELECT s.Ticker FROM stocks s '
        'JOIN stock_indices si ON s.id = si.stock_id '
        'JOIN indices i ON si.index_id = i.id WHERE i.name = ?', (index_name,))}


def symbol_resolves(ticker):
    """Does the data source actually know this symbol?"""
    try:
        from tradinglib import market_data
        info = market_data.ticker_info(ticker, use_cache=False) or {}
        return bool(info.get('longName') or info.get('shortName'))
    except Exception:
        logger.debug("validation failed for %s", ticker, exc_info=True)
        return False


def sync(index_name, apply=False, check=True):
    db = Tools().get_path(path='database', file_name='yf_tickers.db')
    current = fetch_constituents(index_name)
    conn = open_db(db)
    try:
        have = db_members(conn, index_name)
        to_add = sorted(current - have)
        to_remove = sorted(have - current)

        print(f"{index_name} ({SOURCES[index_name]['name']})")
        print(f"  Quelle : {len(current)} Mitglieder")
        print(f"  DB     : {len(have)} Mitglieder")
        print(f"  fehlend: {len(to_add)}")
        print(f"  zu viel: {len(to_remove)}")
        if to_add:
            print(f"\n  aufzunehmen: {', '.join(to_add)}")
        if to_remove:
            print(f"\n  zu entfernen: {', '.join(to_remove)}")

        if not apply:
            print("\nTrockenlauf — nichts geaendert. Mit /apply ausfuehren.")
            return to_add, to_remove

        cur = conn.cursor()
        cur.execute('INSERT OR IGNORE INTO indices (name) VALUES (?)', (index_name,))
        index_id = cur.execute('SELECT id FROM indices WHERE name = ?',
                               (index_name,)).fetchone()[0]

        added, skipped = [], []
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for ticker in to_add:
            if check and not symbol_resolves(ticker):
                skipped.append(ticker)
                continue
            row = cur.execute('SELECT id FROM stocks WHERE Ticker = ?',
                              (ticker,)).fetchone()
            if row:
                stock_id = row[0]
            else:
                cur.execute('INSERT INTO stocks (Ticker, Date, INVESTED, ISIN) '
                            'VALUES (?, ?, ?, ?)', (ticker, now, 0.0, None))
                stock_id = cur.lastrowid
            if not cur.execute('SELECT 1 FROM stock_indices '
                               'WHERE stock_id = ? AND index_id = ?',
                               (stock_id, index_id)).fetchone():
                cur.execute('INSERT INTO stock_indices (stock_id, index_id) '
                            'VALUES (?, ?)', (stock_id, index_id))
            added.append(ticker)

        for ticker in to_remove:
            row = cur.execute('SELECT id FROM stocks WHERE Ticker = ?',
                              (ticker,)).fetchone()
            if row:
                # Unlink only — the ticker, its prices and its trades survive.
                cur.execute('DELETE FROM stock_indices '
                            'WHERE stock_id = ? AND index_id = ?',
                            (row[0], index_id))
        conn.commit()

        print(f"\n  aufgenommen : {len(added)}")
        if skipped:
            print(f"  uebersprungen (Quelle kennt das Symbol nicht): "
                  f"{', '.join(skipped)}")
        print(f"  entfernt    : {len(to_remove)} (nur die Indexbindung)")
        print(f"  Stand jetzt : {len(db_members(conn, index_name))} Mitglieder")
        if added:
            print("\n  Fuer die neuen Mitglieder fehlen noch Kurse und Stammdaten:")
            print(f"    python get_asset_data.py \"60m:2d\" \"1d:max\" /group:{index_name}")
            print(f"    python get_asset_info.py /group:{index_name} /worker:3")
        return added, to_remove
    finally:
        conn.close()


if __name__ == '__main__':
    args = cli.parse_args()
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))
    # cli.parse_args puts the value of /index:NAME into 'index_name' (plain
    # 'index' is the flag without a value) and keeps the case, so '^NDX'
    # survives. Comma-separated lists are documented, so sync them in one run.
    raw = args.get('index_name') or '^SPX'
    indices = [i.strip().upper() for i in str(raw).split(',') if i.strip()]
    for n, index_name in enumerate(indices):
        if n:
            print()
        sync(index_name,
             apply=bool(args.get('apply')),
             check=not args.get('nocheck'))
