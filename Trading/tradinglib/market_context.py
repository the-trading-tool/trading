"""Market and sector context per ticker and day (no Streamlit).

Buy/sell formulas only see the columns of one ticker row. Context -- how broad
the market's advance is, whether the ticker's sector leads, where the ticker ranks
within its own index -- has to become a column before a formula can use it.

Everything here is cross-sectional and causal: the value for day D uses closes up
to D only. Membership comes from the current ``stock_indices`` table (no history),
so the universe carries survivorship bias; every report says so.

Columns
-------
``mkt_breadth200`` / ``mkt_breadth50``
    Share (0..100) of the members of the ticker's index trading above their
    200-/50-day average.
``mkt_breadth_chg``
    ``mkt_breadth50`` minus its value 20 trading days earlier (momentum of breadth).
``sec_rank``
    Percentile (0..100) of the ticker's sector within its index, by the median
    63-day return of the sector's members. Sectors with fewer than
    ``MIN_SECTOR_MEMBERS`` members stay empty.
``rs_rank``
    Percentile (0..100) of the ticker's own 126-day return within its index.
"""
from __future__ import annotations

import logging
import os
import sqlite3

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_SECTOR_MEMBERS = 5
MIN_INDEX_MEMBERS = 20
SECTOR_WINDOW = 63
RS_WINDOW = 126
BREADTH_CHG_LAG = 20
COLUMNS = ('mkt_breadth200', 'mkt_breadth50', 'mkt_breadth_chg', 'sec_rank', 'rs_rank')
CROSS_BORDER_INDICES = {'^STOXX50E'}


def _ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def load_membership(db_path: str = 'database') -> pd.DataFrame:
    """All (ticker, idx) pairs of the ^-indices (current membership, no history)."""
    from tradinglib.tools import Tools
    path = Tools().get_path(path=db_path, file_name='yf_tickers.db')
    if not os.path.exists(path):
        return pd.DataFrame(columns=['ticker', 'idx'])
    con = _ro(path)
    try:
        return pd.read_sql_query(
            "SELECT DISTINCT s.Ticker AS ticker, i.name AS idx FROM stock_indices si "
            "JOIN stocks s ON s.id = si.stock_id JOIN indices i ON i.id = si.index_id "
            "WHERE i.name LIKE '^%'", con)
    finally:
        con.close()


def load_primary_index(db_path: str = 'database') -> dict:
    """ticker -> primary ^-index (the largest national one it belongs to)."""
    rows = load_membership(db_path)
    if rows.empty:
        return {}
    size = rows.groupby('idx')['ticker'].transform('size')
    # Cross-border indices only when nothing national is available: SAP.DE belongs
    # to ^STOXX50E (50) and ^GDAXI (40), and its market is the German one.
    cross = rows['idx'].isin(CROSS_BORDER_INDICES).astype(int)
    rows = rows.assign(size=size, cross=cross).sort_values(
        ['ticker', 'cross', 'size', 'idx'], ascending=[True, True, False, True])
    return dict(rows.drop_duplicates('ticker')[['ticker', 'idx']].values)


def load_sectors(db_path: str = 'database') -> dict:
    """ticker -> sector from asset_info (static snapshot; sectors rarely change)."""
    from tradinglib.tools import Tools
    path = Tools().get_path(path=db_path, file_name='asset_info.db')
    if not os.path.exists(path):
        return {}
    con = _ro(path)
    try:
        rows = pd.read_sql_query(
            "SELECT ticker, sector FROM asset_info WHERE sector IS NOT NULL AND TRIM(sector) <> ''", con)
    finally:
        con.close()
    return dict(rows.values)


# ---------------------------------------------------------------------------
# Persisted breadth: one row per (index, day) in market_context.db
# ---------------------------------------------------------------------------
#
# Only the breadth columns are persisted. They measured as the one context signal
# with a consistent sign (2020-2026, fwd 21 days): breadth50 <= 25 beat the
# universe by +1.70 pp in 7 of 7 years, breadth200 >= 60 lagged by -0.63 pp.
# rs_rank (IC 0.004) and sec_rank (IC -0.009) carried nothing usable and stay
# measurement-only. Breadth depends on index and day only, so it lives in its own
# small table (~16 indices x ~250 days a year) instead of as three more columns on
# every row of the multi-GB simulation DBs; it is joined in when a formula
# references it.

DB_NAME = 'market_context.db'
TABLE = 'breadth'
BREADTH_COLUMNS = ('mkt_breadth200', 'mkt_breadth50', 'mkt_breadth_chg')
_BREADTH_TOKEN = 'mkt_breadth'
_CACHE: dict = {}


def references_breadth(*expressions) -> bool:
    """True when any buy/sell expression uses a breadth column."""
    return any(_BREADTH_TOKEN in str(e or '') for e in expressions)


def _db_path(db_path: str = 'database') -> str:
    from tradinglib.tools import Tools
    return Tools().get_path(path=db_path, file_name=DB_NAME)


def breadth_frame(df: pd.DataFrame, membership) -> pd.DataFrame:
    """Breadth per (idx, Date) from a ticker x day frame with close/sma50/sma200.

    *membership* is the (ticker, idx) table -- every member counts for every index
    it belongs to (a dict ticker -> idx is accepted as well). Returns idx, Date
    (normalised), mkt_breadth200, mkt_breadth50, n. Days with fewer than
    MIN_INDEX_MEMBERS usable members are left empty.
    """
    if isinstance(membership, dict):
        membership = pd.DataFrame(list(membership.items()), columns=['ticker', 'idx'])
    d = df[['ticker', 'Date', 'close', 'sma50', 'sma200']].copy()
    d['Date'] = pd.to_datetime(d['Date'], errors='coerce').dt.normalize()
    for c in ('close', 'sma50', 'sma200'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d = d.merge(membership[['ticker', 'idx']], on='ticker', how='inner')
    d = d.dropna(subset=['idx', 'Date', 'close'])
    d['a200'] = (d['close'] > d['sma200']).where(d['sma200'] > 0)
    d['a50'] = (d['close'] > d['sma50']).where(d['sma50'] > 0)
    agg = d.groupby(['idx', 'Date']).agg(mkt_breadth200=('a200', 'mean'),
                                         mkt_breadth50=('a50', 'mean'),
                                         n=('ticker', 'size')).reset_index()
    small = agg['n'] < MIN_INDEX_MEMBERS
    agg.loc[small, ['mkt_breadth200', 'mkt_breadth50']] = np.nan
    agg[['mkt_breadth200', 'mkt_breadth50']] *= 100
    return agg


def _with_change(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(['idx', 'Date'])
    frame['mkt_breadth_chg'] = frame.groupby('idx')['mkt_breadth50'].diff(BREADTH_CHG_LAG)
    return frame


def _read_sim(year: int, since=None, db_path: str = 'database', tickers=None) -> pd.DataFrame:
    from tradinglib.score_eval import _sim_db_name
    from tradinglib.tools import Tools
    path = Tools().get_path(path=db_path, file_name=_sim_db_name(year))
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    con = _ro(path)
    try:
        sql = "SELECT ticker, Date, close, sma50, sma200 FROM asset_simulation"
        params: list = []
        if since is not None:
            sql += " WHERE Date >= ?"
            params.append(pd.Timestamp(since).strftime('%Y-%m-%d'))
        df = pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()
    if tickers is not None:
        df = df[df['ticker'].isin(tickers)]
    return df


def _write(frame: pd.DataFrame, db_path: str = 'database') -> int:
    path = _db_path(db_path)
    rows = frame[['idx', 'Date', 'mkt_breadth200', 'mkt_breadth50', 'mkt_breadth_chg', 'n']].copy()
    rows['Date'] = pd.to_datetime(rows['Date']).dt.strftime('%Y-%m-%d')
    rows = rows.astype(object).where(rows.notna(), None)
    con = sqlite3.connect(path)
    try:
        con.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (idx TEXT, Date TEXT, "
                    "mkt_breadth200 REAL, mkt_breadth50 REAL, mkt_breadth_chg REAL, n INTEGER, "
                    "PRIMARY KEY (idx, Date))")
        con.executemany(f"INSERT OR REPLACE INTO {TABLE} VALUES (?,?,?,?,?,?)",
                        rows.itertuples(index=False, name=None))
        con.commit()
    finally:
        con.close()
    _CACHE.clear()
    return len(rows)


def build(years=None, db_path: str = 'database') -> int:
    """(Re)compute breadth for all simulation years and store it. Returns rows written."""
    from tradinglib.score_eval import available_years
    membership = load_membership(db_path)
    members = set(membership['ticker'])
    parts = []
    for y in sorted(years or available_years(db_path)):
        sim = _read_sim(int(y), db_path=db_path, tickers=members)
        if not sim.empty:
            parts.append(breadth_frame(sim, membership))
            logger.info('breadth %s: %d index-days', y, len(parts[-1]))
    if not parts:
        return 0
    frame = pd.concat(parts, ignore_index=True)
    # A day can sit in two year-DBs (year boundary re-runs); keep the fuller one.
    frame = frame.sort_values('n').drop_duplicates(['idx', 'Date'], keep='last')
    return _write(_with_change(frame), db_path)


def update(days: int = 60, db_path: str = 'database') -> int:
    """Recompute the last *days* calendar days (cheap; for the daily run)."""
    from datetime import datetime, timedelta
    membership = load_membership(db_path)
    members = set(membership['ticker'])
    now = datetime.now()
    since = now - timedelta(days=days + 40)          # + lag window for the change
    parts = []
    for y in sorted({since.year, now.year}):
        sim = _read_sim(y, since, db_path, members)
        if not sim.empty:
            parts.append(breadth_frame(sim, membership))
    if not parts:
        return 0
    frame = pd.concat(parts, ignore_index=True)
    frame = frame.sort_values('n').drop_duplicates(['idx', 'Date'], keep='last')
    frame = _with_change(frame)
    frame = frame[frame['Date'] >= pd.Timestamp(now - timedelta(days=days)).normalize()]
    return _write(frame, db_path)


def load_breadth(db_path: str = 'database') -> pd.DataFrame:
    """The stored breadth table (cached per file modification time)."""
    path = _db_path(db_path)
    if not path or not os.path.exists(path):
        return pd.DataFrame(columns=['idx', 'Date', *BREADTH_COLUMNS])
    key = (path, os.path.getmtime(path))
    if key not in _CACHE:
        con = _ro(path)
        try:
            df = pd.read_sql_query(f"SELECT idx, Date, {', '.join(BREADTH_COLUMNS)} FROM {TABLE}", con)
        finally:
            con.close()
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        _CACHE.clear()
        _CACHE[key] = df
    return _CACHE[key]


def _primary_cached(db_path: str = 'database') -> dict:
    key = ('primary', db_path)
    if key not in _CACHE:
        _CACHE[key] = load_primary_index(db_path)
    return _CACHE[key]


def attach_breadth(df: pd.DataFrame, ticker_col: str = 'ticker', date_col: str = 'Date',
                   symbol: str | None = None, db_path: str = 'database') -> pd.DataFrame:
    """Return *df* with the breadth columns joined by the ticker's index and day.

    *symbol* overrides the ticker column (single-asset chart frames). An index
    ticker itself (^GDAXI) gets its own breadth. Rows without a match stay NaN,
    and existing breadth columns are left untouched.
    """
    if all(c in df.columns for c in BREADTH_COLUMNS):
        return df
    table = load_breadth(db_path)
    out = df.copy()
    if table.empty:
        for c in BREADTH_COLUMNS:
            if c not in out.columns:
                out[c] = np.nan
        return out
    primary = _primary_cached(db_path)
    own = set(table['idx'].unique())
    tick = pd.Series(symbol, index=out.index) if symbol else out[ticker_col]
    idx = tick.map(lambda t: t if t in own else primary.get(t))
    dates = (pd.to_datetime(out[date_col], errors='coerce') if date_col in out.columns
             else pd.to_datetime(pd.Series(out.index, index=out.index), errors='coerce'))
    try:
        dates = dates.dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    day = dates.dt.normalize()
    key = pd.DataFrame({'idx': idx.values, 'Date': day.values, '_pos': np.arange(len(out))})
    if (dates.notna() & (dates != day)).any():
        # Intraday bars: today's breadth is only known after the close, so a bar
        # during the session sees the previous day's value (no look-ahead).
        k = key.dropna(subset=['idx', 'Date']).sort_values('Date')
        t = table.sort_values('Date')
        merged = pd.merge_asof(k, t, on='Date', by='idx', allow_exact_matches=False)
        merged = merged.set_index('_pos').reindex(range(len(out)))
    else:
        merged = key.merge(table, on=['idx', 'Date'], how='left')
    for c in BREADTH_COLUMNS:
        if c not in out.columns:
            out[c] = merged[c].values
    return out


def main(argv=None) -> None:
    """CLI: python -m tradinglib.market_context [/build | /update[:DAYS]]"""
    import sys
    from tradinglib import cli, logging_config
    args = cli.parse_args(argv or sys.argv)
    logging_config.configure_logging(to_console=True)
    if args.get('build'):
        n = build()
    else:
        u = args.get('update')
        n = update(days=int(u) if str(u).isdigit() else 60)
    size = os.path.getsize(_db_path()) if os.path.exists(_db_path()) else 0
    print(f'{n} index-days written, {DB_NAME} {size / 1e6:.2f} MB')


if __name__ == '__main__':
    main()


def add_context(df: pd.DataFrame, primary_index: dict, sectors: dict) -> pd.DataFrame:
    """Return *df* with the context columns added.

    *df* needs ticker, Date, close and ideally sma50/sma200 (computed from close
    when missing). Rows are matched to an index via *primary_index*; tickers
    without one get NaN context.
    """
    out = df.copy()
    out['Date'] = pd.to_datetime(out['Date'], errors='coerce')
    out = out.sort_values(['ticker', 'Date'])
    close = pd.to_numeric(out['close'], errors='coerce')
    g = close.groupby(out['ticker'], sort=False)
    for n in (50, 200):
        col = f'sma{n}'
        if col in out.columns:
            out[f'_sma{n}'] = pd.to_numeric(out[col], errors='coerce')
        else:
            out[f'_sma{n}'] = g.transform(lambda s: s.rolling(n, min_periods=n).mean())
    out['_ret_sec'] = g.pct_change(SECTOR_WINDOW, fill_method=None)
    out['_ret_rs'] = g.pct_change(RS_WINDOW, fill_method=None)
    out['_idx'] = out['ticker'].map(primary_index)
    out['_sector'] = out['ticker'].map(sectors)

    # ── breadth per (index, date) ───────────────────────────────────────────
    valid = out['_idx'].notna()
    b = out[valid].assign(
        _a200=(close[valid] > out.loc[valid, '_sma200']).where(out.loc[valid, '_sma200'].notna()),
        _a50=(close[valid] > out.loc[valid, '_sma50']).where(out.loc[valid, '_sma50'].notna()))
    agg = b.groupby(['_idx', 'Date']).agg(
        mkt_breadth200=('_a200', 'mean'), mkt_breadth50=('_a50', 'mean'),
        _n=('ticker', 'size'))
    agg.loc[agg['_n'] < MIN_INDEX_MEMBERS, ['mkt_breadth200', 'mkt_breadth50']] = np.nan
    agg[['mkt_breadth200', 'mkt_breadth50']] *= 100
    agg = agg.sort_index()
    agg['mkt_breadth_chg'] = agg.groupby(level=0)['mkt_breadth50'].diff(BREADTH_CHG_LAG)
    out = out.merge(agg[['mkt_breadth200', 'mkt_breadth50', 'mkt_breadth_chg']],
                    left_on=['_idx', 'Date'], right_index=True, how='left')

    # ── relative strength within the index ─────────────────────────────────
    out['rs_rank'] = out[valid].groupby(['_idx', 'Date'])['_ret_rs'].rank(pct=True) * 100
    cnt = out[valid].groupby(['_idx', 'Date'])['_ret_rs'].transform('count')
    out.loc[out.index.isin(cnt[cnt < MIN_INDEX_MEMBERS].index), 'rs_rank'] = np.nan

    # ── sector strength within the index ───────────────────────────────────
    s = out[valid & out['_sector'].notna()]
    sec = s.groupby(['_idx', 'Date', '_sector'])['_ret_sec'].agg(['median', 'count'])
    sec = sec[sec['count'] >= MIN_SECTOR_MEMBERS]
    sec['sec_rank'] = sec.groupby(level=[0, 1])['median'].rank(pct=True) * 100
    nsec = sec.groupby(level=[0, 1])['median'].transform('count')
    sec.loc[nsec < 3, 'sec_rank'] = np.nan
    out = out.merge(sec[['sec_rank']], left_on=['_idx', 'Date', '_sector'],
                    right_index=True, how='left')

    return out.drop(columns=[c for c in out.columns if c.startswith('_')])
