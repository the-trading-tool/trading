"""Ticker sources for the Asset Summary page (no Streamlit).

Besides the monitored assets the summary can show the instruments from
Own Trades (trades.db) and Paper Trading (trading.db). All reads are read-only.
"""
import logging
from contextlib import closing
import os
import sqlite3

import pandas as pd

from tradinglib.tools import Tools

logger = logging.getLogger(__name__)

SOURCES = ('monitored', 'own', 'paper', 'market')

_EPS = 1e-4


def _connect_ro(file_name: str):
    """Open a database read-only; None when the file does not exist."""
    db_file = Tools().get_path('database', file_name)
    if not os.path.exists(db_file):
        return None
    return sqlite3.connect(f'file:{db_file}?mode=ro', uri=True)


def _net_open(df: pd.DataFrame, key: str, side: str, qty: str) -> list:
    """Tickers whose buy quantity exceeds the sell quantity."""
    if df.empty:
        return []
    q = pd.to_numeric(df[qty], errors='coerce').fillna(0)
    sign = df[side].astype(str).str.lower().str.strip().map({'buy': 1, 'sell': -1}).fillna(0)
    net = (q * sign).groupby(df[key].astype(str).str.strip()).sum()
    return [t for t, n in net.items() if t and n > _EPS]


def own_trade_tickers(open_only: bool = True) -> list:
    """Tickers from Own Trades; open positions only, or everything ever traded."""
    try:
        conn = _connect_ro('trades.db')
        if conn is None:
            return []
        with closing(conn):
            df = pd.read_sql_query(
                'SELECT ticker, action, shares FROM trades WHERE ticker IS NOT NULL', conn)
    except Exception as e:
        logger.debug('own_trade_tickers failed: %s', e)
        return []
    df['ticker'] = df['ticker'].astype(str).str.upper().str.strip()
    df = df[~df['ticker'].isin(('', 'NONE', 'NAN'))]
    tickers = _net_open(df, 'ticker', 'action', 'shares') if open_only else df['ticker'].unique().tolist()
    return sorted(set(tickers))


def paper_trade_tickers(open_only: bool = True) -> list:
    """Tickers from Paper Trading (mode='paper').

    Open positions come from the broker fills (broker_activities) when they were
    synced: the local order log can keep phantom positions whose closing sell
    was logged with a wrong quantity. Broker symbols are mapped back to the app
    ticker via broker_orders. Without synced fills the order log is used.
    """
    try:
        conn = _connect_ro('trading.db')
        if conn is None:
            return []
        with closing(conn):
            orders = pd.read_sql_query(
                "SELECT ticker, broker_symbol, action, qty, status FROM broker_orders "
                "WHERE mode='paper' AND ticker IS NOT NULL", conn)
            try:
                fills = pd.read_sql_query(
                    "SELECT symbol, side, qty FROM broker_activities "
                    "WHERE mode='paper' AND activity_type='FILL'", conn)
            except Exception:
                fills = pd.DataFrame(columns=['symbol', 'side', 'qty'])
    except Exception as e:
        logger.debug('paper_trade_tickers failed: %s', e)
        return []

    orders['ticker'] = orders['ticker'].astype(str).str.strip()
    if not open_only:
        return sorted(set(orders['ticker']) - {''})

    if not fills.empty:
        sym_map = {}
        for _, row in orders.iterrows():
            sym = str(row['broker_symbol'] or '').strip() or row['ticker']
            sym_map.setdefault(sym, row['ticker'])
        return sorted({sym_map.get(s, s) for s in _net_open(fills, 'symbol', 'side', 'qty')})

    filled = orders[orders['status'].astype(str).str.lower() == 'filled']
    return sorted(set(_net_open(filled, 'ticker', 'action', 'qty')))


def multi_strategy_tickers(year: int | None = None) -> list:
    """Open positions of Multi Strategies (trades{year}.db, not yet sold)."""
    from datetime import datetime
    year = year or datetime.now().year
    try:
        conn = _connect_ro(f'trades{year}.db')
        if conn is None:
            return []
        with closing(conn):
            df = pd.read_sql_query(
                "SELECT DISTINCT ticker FROM trades WHERE ticker IS NOT NULL "
                "AND (sellVolume IS NULL OR sellVolume = '' OR sellVolume = 0)", conn)
    except Exception as e:
        logger.debug('multi_strategy_tickers failed: %s', e)
        return []
    return sorted({str(t).strip() for t in df['ticker'] if str(t).strip()})


def held_tickers() -> list:
    """Everything currently held: own trades, paper trading, multi strategies.

    The daily runs select index members only. A position whose ticker left its
    index (AAD.DE was unlinked from ^SDAXI on 2026-08-09) then silently stopped
    getting prices and scores in the current-year simulation, and pages reading
    it showed stop-loss levels from a weeks-old close. The runs add this list so
    anything held keeps being computed regardless of index membership.
    """
    out = set()
    for fn in (own_trade_tickers, paper_trade_tickers, multi_strategy_tickers):
        try:
            out |= set(fn())
        except Exception as e:
            logger.debug('held_tickers: %s failed: %s', fn.__name__, e)
    return sorted(t for t in out if t)
