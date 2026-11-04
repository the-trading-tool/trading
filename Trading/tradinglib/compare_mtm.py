"""Mark-to-market curves for 'Backtest vs. Paper Trading' (no Streamlit).

Both lines count a position's gain on the day it happens: realised gains from
the sell on, open positions at each day's close. Realised-only lines book every
open position on the last day instead -- the backtest then drops by the paper
loss of all its open positions at once, while the paper line leaves them out.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from tradinglib import backtest_eval as be
from tradinglib.tools import Tools

logger = logging.getLogger(__name__)

BACKTEST_COLUMNS = ('buyDate', 'sellDate', 'ticker', 'gain', 'buyValue', 'sellVolume')


def load_closes(tickers, since, db_path: str = 'database', until=None) -> pd.DataFrame:
    """Business days x tickers of daily closes from the local yf_<T>.db (read only), ffilled.

    The price index is stored as text: it is parsed, normalised to the day and
    de-duplicated before pivoting.
    """
    since = pd.Timestamp(since).normalize()
    until = pd.Timestamp(until if until is not None else pd.Timestamp.now()).normalize()
    lo = (since - pd.Timedelta(days=10)).strftime('%Y-%m-%d')
    cols = {}
    for tk in sorted({str(t) for t in tickers if t}):
        path = Tools().get_path(path=db_path, file_name=f'yf_{tk}.db')
        if not os.path.exists(path):
            continue
        try:
            con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
            try:
                df = pd.read_sql_query('SELECT Date, Close FROM day_data WHERE Date >= ?', con, params=(lo,))
            finally:
                con.close()
        except Exception:
            logger.debug('closes for %s not readable', tk, exc_info=True)
            continue
        idx = pd.to_datetime(df['Date'], errors='coerce', utc=True).dt.tz_localize(None).dt.normalize()
        s = pd.Series(pd.to_numeric(df['Close'], errors='coerce').to_numpy(), index=idx).dropna()
        s = s[~s.index.isna()]
        s = s[~s.index.duplicated(keep='last')].sort_index()
        if not s.empty:
            cols[tk] = s
    days = pd.bdate_range(since - pd.Timedelta(days=10), until)
    if not cols:
        return pd.DataFrame(index=days)
    panel = pd.DataFrame(cols)
    return panel.reindex(panel.index.union(days)).sort_index().ffill().reindex(days)


def backtest_curve(trades: pd.DataFrame, panel: pd.DataFrame) -> pd.Series:
    """Daily mark-to-market P&L of the backtest trades (0 at the first panel day).

    Needs buyValue and sellVolume (open = no sell volume). Value while held =
    |buyValue| * close(day) / close(buyDate), so the listing currency cancels out
    (as be.mtm_equity). Without a price at the buy day a closed trade still books
    its realised gain on the sell day, an open one its recorded gain on the last day.
    """
    if trades is None or trades.empty or panel.empty:
        return pd.Series(dtype=float)
    days = panel.index
    pnl = pd.Series(0.0, index=days)
    for r in be._norm_trades(trades).itertuples():
        s = panel[r.ticker] if r.ticker in panel.columns else None
        base = s.asof(r.buyDate) if s is not None and r.buyDate >= days[0] else np.nan
        if r.closed:
            if pd.notna(base) and base:
                hold = (days >= r.buyDate) & (days < r.sellDate)
                pnl[hold] += (r.bv * (s[hold] / base - 1.0)).fillna(0.0)
            pnl[days >= r.sellDate] += r.gain
        elif pd.notna(base) and base:
            hold = days >= r.buyDate
            pnl[hold] += (r.bv * (s[hold] / base - 1.0)).fillna(0.0)
        elif pd.notna(r.gain):
            pnl.iloc[-1] += r.gain
    return pnl


def paper_pieces(fills: pd.DataFrame, sym_map: dict | None = None) -> pd.DataFrame:
    """FIFO pieces of the paper FILLs: qty, buy time/price, sell time/price (open = NaT/NaN).

    Same lot matching as the realised timeline of the trading page; sells without
    a matching buy lot (positions opened before the synced history) are dropped.
    """
    cols = ['ticker', 'qty', 'buy_time', 'buy_price', 'sell_time', 'sell_price']
    if fills is None or fills.empty:
        return pd.DataFrame(columns=cols)
    f = fills.copy()
    if 'activity_type' in f.columns:
        f = f[f['activity_type'] == 'FILL']
    f['qty'] = pd.to_numeric(f['qty'], errors='coerce')
    f['price'] = pd.to_numeric(f['price'], errors='coerce')
    f['time'] = pd.to_datetime(f['transaction_time'], errors='coerce', utc=True).dt.tz_localize(None)
    f['side'] = f['side'].astype(str).str.lower()
    f = f.dropna(subset=['qty', 'price', 'time', 'symbol'])
    f = f[f['qty'] > 0].sort_values('time', kind='stable')
    sym_map = sym_map or {}
    lots: dict = defaultdict(deque)          # symbol -> deque([qty, price, time])
    rows = []
    for sym, side, qty, price, tm in zip(f['symbol'], f['side'], f['qty'], f['price'], f['time']):
        if side == 'buy':
            lots[sym].append([qty, price, tm])
        elif side == 'sell':
            remaining, dq = qty, lots[sym]
            while remaining > 0 and dq:
                lot = dq[0]
                take = min(remaining, lot[0])
                rows.append((sym, take, lot[2], lot[1], tm, price))
                lot[0] -= take
                remaining -= take
                if lot[0] <= 1e-9:
                    dq.popleft()
    for sym, dq in lots.items():
        for q, p, tm in dq:
            if q > 1e-9:
                rows.append((sym, q, tm, p, pd.NaT, np.nan))
    out = pd.DataFrame(rows, columns=cols)
    out['ticker'] = out['ticker'].map(lambda s: sym_map.get(s, s))
    return out


def paper_curve(pieces: pd.DataFrame, panel: pd.DataFrame) -> pd.Series:
    """Daily mark-to-market P&L of the paper pieces on the panel's days.

    A closed piece books (sell - buy) * qty from its sell day on; a piece that is
    held at a day's close counts (close - buy) * qty. Without a close for the
    ticker a held piece counts 0 (valued at its buy price).
    """
    if panel.empty:
        return pd.Series(dtype=float)
    days = panel.index
    pnl = pd.Series(0.0, index=days)
    if pieces is None or pieces.empty:
        return pnl
    for r in pieces.itertuples():
        b = pd.Timestamp(r.buy_time).normalize()
        closed = pd.notna(r.sell_time)
        s_day = pd.Timestamp(r.sell_time).normalize() if closed else None
        hold = (days >= b) & ((days < s_day) if closed else True)
        if r.ticker in panel.columns:
            pnl[hold] += ((panel[r.ticker][hold] - r.buy_price) * r.qty).fillna(0.0)
        if closed:
            pnl[days >= s_day] += (r.sell_price - r.buy_price) * r.qty
    return pnl
