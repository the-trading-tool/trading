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
import os
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
# Symbol handling per source, applied in this order by to_yahoo_symbol():
#   strip_prefix  cut an exchange marker off the front ("SEHK: 5" -> "5")
#   class_dot     US class shares: BRK.B -> BRK-B (only where a dot means a
#                 share class -- for European sources the dot is the exchange
#                 suffix and must survive untouched)
#   pad           zero-pad to a fixed width (Hong Kong: 5 -> 0005)
#   suffix        exchange suffix Yahoo expects (.HK, .AX, .DE, .T)
SOURCES = {
    '^SPX': {
        'url': 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
        'table': 0,
        'column': 'Symbol',
        'name': 'S&P 500',
        'min': 400,
        'class_dot': True,
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
        'class_dot': True,
    },
    '^DJI': {
        'url': 'https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average',
        'table': 1,
        'column': 'Symbol',
        'name': 'Dow Jones Industrial Average',
        'min': 25,
        'class_dot': True,
    },
    '^N225': {
        # Wikipedia carries no constituent table for the Nikkei (neither the
        # English nor the Japanese article), so this reads the index owner's
        # own component page. It splits the 225 across ~34 sector tables --
        # table='all' concatenates every table carrying the column.
        'url': 'https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225',
        'table': 'all',
        'column': 'Code',
        'name': 'Nikkei 225',
        'min': 200,
        # Codes are not all numeric any more (Japan issues alphanumeric ones
        # such as 285A), so no padding here -- they are already four wide.
        'suffix': '.T',
    },
    '^HSI': {
        'url': 'https://en.wikipedia.org/wiki/Hang_Seng_Index',
        'table': 6,
        'column': 'Ticker',
        'name': 'Hang Seng Index',
        'min': 70,
        'strip_prefix': 'SEHK:',
        'pad': 4,
        'suffix': '.HK',
    },
    '^ASXJO': {
        'url': 'https://en.wikipedia.org/wiki/S%26P/ASX_200',
        'table': 2,
        'column': 'Code',
        'name': 'S&P/ASX 200',
        'min': 180,
        'suffix': '.AX',
    },
    '^TECDAX': {
        # The English article has no ticker column; the German one does.
        'url': 'https://de.wikipedia.org/wiki/TecDAX',
        'table': 5,
        'column': 'Symbol[9]',
        'name': 'TecDAX',
        'min': 25,
        'suffix': '.DE',
    },
    '^STOXX50E': {
        'url': 'https://en.wikipedia.org/wiki/EURO_STOXX_50',
        'table': 3,
        'column': 'Ticker',
        'name': 'EURO STOXX 50',
        'min': 45,
        # Already in Yahoo notation (ADS.DE, ADYEN.AS) -- no dot rewriting.
    },
    '^IBEX': {
        'url': 'https://en.wikipedia.org/wiki/IBEX_35',
        'table': 2,
        'column': 'Ticker',
        'name': 'IBEX 35',
        'min': 30,
    },
    '^BVSP': {
        # Ibovespa, the Brazilian benchmark. The article is titled after the
        # exchange, but it carries 88 rows -- the index portfolio, not B3's
        # several hundred listings. Symbols are bare B3 codes (PETR4, VALE3);
        # Yahoo wants '.SA'. The class digit is part of the code (3 = ordinary,
        # 4 = preferred, 11 = unit), so it must not be touched.
        'url': 'https://en.wikipedia.org/wiki/List_of_companies_listed_on_B3',
        'table': 0,
        'column': 'Ticker',
        'name': 'Ibovespa',
        'min': 70,
        'suffix': '.SA',
    },
    '^SDAXI': {
        # No public source lists SDAX constituents with an identifier:
        # Wikipedia carries names only (a name match scored 67 % and confused
        # ordinary with preference shares) and finanzen.net answers scripted
        # requests with HTTP 403. The list is therefore curated in the repo --
        # see the file header for how it was derived.
        'file': 'index_members/sdaxi.txt',
        'name': 'SDAX',
        'min': 60,
    },
}

USER_AGENT = 'Mozilla/5.0 (compatible; trading-app index sync)'


def to_yahoo_symbol(symbol, src=None):
    """Bring one source symbol into Yahoo notation (see SOURCES for the keys)."""
    src = src or {}
    # Wikipedia separates the exchange marker with a non-breaking space.
    s = str(symbol).replace('\xa0', ' ').strip().upper()

    prefix = (src.get('strip_prefix') or '').upper()
    if prefix and prefix in s:
        s = s.split(prefix, 1)[1].strip()
    if src.get('class_dot'):
        s = s.replace('.', '-')
    if src.get('pad'):
        s = s.zfill(src['pad'])
    suffix = src.get('suffix', '')
    if suffix and not s.endswith(suffix):
        s += suffix
    return s


def fetch_constituents(index_name):
    """Current constituent symbols for one index, in Yahoo notation."""
    src = SOURCES.get(index_name)
    if not src:
        raise SystemExit(f"no constituent source configured for {index_name} "
                         f"(known: {', '.join(sorted(SOURCES))})")
    if src.get('file'):
        # Curated list next to the script; '#' starts a comment.
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), src['file'])
        with open(path, encoding='utf-8') as fh:
            values = [ln.split('#', 1)[0].strip() for ln in fh]
        values = [v for v in values if v]
        symbols = {to_yahoo_symbol(s, src) for s in values}
        minimum = src.get('min', 50)
        if len(symbols) < minimum:
            raise SystemExit(f"{path}: only {len(symbols)} symbols, expected at "
                             f"least {minimum} — refusing to sync")
        return symbols

    req = urllib.request.Request(src['url'], headers={'User-Agent': USER_AGENT})
    html = urllib.request.urlopen(req, timeout=30).read().decode('utf-8')
    tables = pd.read_html(io.StringIO(html))

    if src['table'] == 'all':
        # Constituents spread over several tables (one per sector).
        parts = [t[src['column']].dropna() for t in tables
                 if src['column'] in [str(c) for c in t.columns]]
        if not parts:
            raise SystemExit(f"no table carries column {src['column']!r} — "
                             f"page layout changed")
        values = pd.concat(parts)
    else:
        table = tables[src['table']]
        if src['column'] not in table.columns:
            raise SystemExit(f"column {src['column']!r} missing — page layout "
                             f"changed; found: {list(table.columns)}")
        values = table[src['column']].dropna()

    symbols = {to_yahoo_symbol(s, src) for s in values}
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
