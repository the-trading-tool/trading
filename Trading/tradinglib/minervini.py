"""Mark Minervini's Trend Template — per ticker and day, causal (no Streamlit).

The template is eight conditions a stock has to meet before Minervini looks at
it at all. Seven depend on the ticker's own price history and are computed per
ticker; the eighth, the RS rating, ranks the ticker against its market and can
only be computed once every ticker of the day is known.

Per-ticker columns (``trend_template``, stored by asset_perf2 via the ``mtt``
indicator):

``mtt_sma150``        150-day simple average of the close.
``mtt_slope200``      change of the 200-day average over 21 bars, in percent
                      (condition 3: the 200-day line rises for at least a month).
``mtt_high52`` / ``mtt_low52``
                      highest / lowest close of the last 252 bars. Closes, not
                      intraday highs and lows: single bad ticks in the local
                      OHLC would otherwise move the 52-week extremes.
``mtt_rs_raw``        IBD-style weighted return,
                      0.4·r63 + 0.2·r126 + 0.2·r189 + 0.2·r252 — the input of
                      the RS rating; the latest quarter counts double.
``mtt_score``         number of conditions 1-7 met (0..7).
``mtt_pass``          1 when all seven are met, else 0.

Cross-sectional column (``update_rs``, written after each run):

``mtt_rs``            RS rating 1..99 — the percentile of ``mtt_rs_raw`` among
                      the index members of the same market on that day. The
                      market is the listing currency (USD, EUR, JPY, ...), so a
                      DAX stock is ranked against the eurozone index members,
                      not only the 40 names of its own index. Tickers outside
                      the ^-indices (held positions, ETPs) are placed on the
                      members' scale without changing it. 0 = not computed.

The eight conditions:

1. close > sma150 and close > sma200
2. sma150 > sma200
3. sma200 higher than 21 bars ago
4. sma50 > sma150 and sma50 > sma200
5. close > sma50
6. close at least 30 % above the 52-week low
7. close within 25 % of the 52-week high
8. RS rating >= 70

A formula for the full template therefore reads
``(mtt_pass == 1) & (mtt_rs >= 70)``.

Membership comes from today's ``stock_indices`` (no history), so the ranking
universe carries survivorship bias, like every cross-sectional column here.
"""
from __future__ import annotations

import logging
import os
import sqlite3

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TICKER_COLUMNS = ('mtt_sma150', 'mtt_slope200', 'mtt_high52', 'mtt_low52',
                  'mtt_rs_raw', 'mtt_score', 'mtt_pass')
RS_COLUMN = 'mtt_rs'

YEAR_BARS = 252
SLOPE_BARS = 21
MIN_ABOVE_LOW = 0.30          # condition 6
MAX_BELOW_HIGH = 0.25         # condition 7
RS_MIN = 70                   # condition 8
RS_WEIGHTS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))
MIN_MARKET_MEMBERS = 50       # fewer members on a day -> no rating
SIM_TABLE = 'asset_simulation'


# ---------------------------------------------------------------------------
# Per ticker
# ---------------------------------------------------------------------------

def _levels(close: pd.Series) -> pd.DataFrame:
    """Averages, slope, 52-week range and RS input — all trailing windows."""
    sma200 = close.rolling(200, min_periods=200).mean()
    levels = pd.DataFrame({
        'close': close,
        'sma50': close.rolling(50, min_periods=50).mean(),
        'sma150': close.rolling(150, min_periods=150).mean(),
        'sma200': sma200,
        'slope200': (sma200 / sma200.shift(SLOPE_BARS) - 1.0) * 100.0,
        'high52': close.rolling(YEAR_BARS, min_periods=YEAR_BARS).max(),
        'low52': close.rolling(YEAR_BARS, min_periods=YEAR_BARS).min(),
    }, index=close.index)
    rs_raw = pd.Series(0.0, index=close.index)
    for bars, weight in RS_WEIGHTS:
        rs_raw = rs_raw + weight * close.pct_change(bars, fill_method=None)
    levels['rs_raw'] = rs_raw
    return levels


def _conditions(lv: pd.DataFrame) -> pd.DataFrame:
    c = lv['close']
    return pd.DataFrame({
        'above_150_200': (c > lv['sma150']) & (c > lv['sma200']),
        'sma150_over_200': lv['sma150'] > lv['sma200'],
        'sma200_rising': lv['slope200'] > 0,
        'sma50_over_150_200': (lv['sma50'] > lv['sma150']) & (lv['sma50'] > lv['sma200']),
        'above_50': c > lv['sma50'],
        'above_low': c >= (1.0 + MIN_ABOVE_LOW) * lv['low52'],
        'near_high': c >= (1.0 - MAX_BELOW_HIGH) * lv['high52'],
    }, index=lv.index)


def criteria(close: pd.Series) -> pd.DataFrame:
    """The seven conditions as booleans per bar — for detail views and tests."""
    close = pd.to_numeric(close, errors='coerce').astype(float)
    return _conditions(_levels(close))


def trend_template(close: pd.Series) -> pd.DataFrame:
    """The seven per-ticker conditions for every bar of a daily close series.

    Every value uses closes up to its own bar only. Bars without the full
    warm-up (the 200-day slope needs 221 bars, the 52-week range and the RS
    input 253) leave score and pass NaN — not computed, rather than failed.
    """
    close = pd.to_numeric(close, errors='coerce').astype(float)
    lv = _levels(close)
    met = _conditions(lv)
    score = met.sum(axis=1).astype(float)

    out = pd.DataFrame(index=close.index)
    out['mtt_sma150'] = lv['sma150']
    out['mtt_slope200'] = lv['slope200']
    out['mtt_high52'] = lv['high52']
    out['mtt_low52'] = lv['low52']
    out['mtt_rs_raw'] = lv['rs_raw']
    out['mtt_score'] = score
    out['mtt_pass'] = (score == met.shape[1]).astype(float)
    warm = lv['high52'].notna() & lv['slope200'].notna()
    out.loc[~warm, ['mtt_score', 'mtt_pass']] = np.nan
    return out


# ---------------------------------------------------------------------------
# Cross-sectional RS rating
# ---------------------------------------------------------------------------

def _ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def load_markets(db_path: str = 'database') -> tuple[dict, set]:
    """(ticker -> market, set of ^-index members). Market = listing currency."""
    from tradinglib.market_context import load_membership
    from tradinglib.tools import Tools
    members = set(load_membership(db_path)['ticker'])
    path = Tools().get_path(path=db_path, file_name='asset_info.db')
    markets = {}
    if os.path.exists(path):
        con = _ro(path)
        try:
            for ticker, currency in con.execute(
                    "SELECT ticker, currency FROM asset_info "
                    "WHERE currency IS NOT NULL AND TRIM(currency) <> ''"):
                # GBp (pence) and GBP are the same market.
                markets[ticker] = str(currency).strip().upper()
        finally:
            con.close()
    return markets, members


def rs_rating(frame: pd.DataFrame, markets: dict, members: set) -> pd.Series:
    """RS rating 1..99 for a ticker x day frame with ticker, Date, mtt_rs_raw.

    Per (market, Date) the members' raw values define the scale; every ticker of
    that market — member or not — gets the share of members it beats, mapped to
    1..99. Rows without a market, without a raw value, or on a day with fewer
    than MIN_MARKET_MEMBERS members stay NaN.
    """
    raw = pd.to_numeric(frame['mtt_rs_raw'], errors='coerce')
    # 0.0 is what the engine stores for "not computed".
    raw = raw.where(raw != 0)
    market = frame['ticker'].map(markets)
    is_member = frame['ticker'].isin(members)
    day = pd.to_datetime(frame['Date'], errors='coerce').dt.normalize()
    out = pd.Series(np.nan, index=frame.index)
    work = pd.DataFrame({'raw': raw, 'market': market, 'member': is_member, 'day': day})
    work = work.dropna(subset=['raw', 'market', 'day'])
    for (_, _), group in work.groupby(['market', 'day'], sort=False):
        scale = np.sort(group.loc[group['member'], 'raw'].to_numpy())
        if len(scale) < MIN_MARKET_MEMBERS:
            continue
        # Share of members strictly below, plus half of the ties.
        below = np.searchsorted(scale, group['raw'].to_numpy(), side='left')
        upto = np.searchsorted(scale, group['raw'].to_numpy(), side='right')
        pct = (below + upto) / 2.0 / len(scale)
        out.loc[group.index] = np.clip(np.ceil(pct * 99), 1, 99)
    return out


def update_rs(sim_db_path: str, since: str | None = None, db_path: str = 'database') -> int:
    """Rank ``mtt_rs_raw`` per market and day and write ``mtt_rs``.

    *since* ('YYYY-MM-DD') limits the pass to the days from then on — enough for
    the daily run; without it the whole DB is ranked. Returns rows updated.
    """
    if not sim_db_path or not os.path.exists(sim_db_path):
        return 0
    from tradinglib.tools import open_db
    markets, members = load_markets(db_path)
    conn = open_db(sim_db_path, timeout=30.0)
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({SIM_TABLE})")}
        if 'mtt_rs_raw' not in cols:
            logger.info("mtt: %s has no mtt_rs_raw yet", os.path.basename(sim_db_path))
            return 0
        if RS_COLUMN not in cols:
            conn.execute(f"ALTER TABLE {SIM_TABLE} ADD COLUMN {RS_COLUMN} REAL DEFAULT 0")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{SIM_TABLE}_ticker_date "
                     f"ON {SIM_TABLE} (ticker, Date)")
        sql = f"SELECT ticker, Date, mtt_rs_raw FROM {SIM_TABLE}"
        params: list = []
        if since:
            sql += " WHERE Date >= ?"
            params.append(str(since)[:10])
        frame = pd.read_sql_query(sql, conn, params=params)
        if frame.empty:
            return 0
        frame['mtt_rs'] = rs_rating(frame, markets, members)
        values = frame['mtt_rs'].fillna(0.0).astype(float)
        before = conn.total_changes
        conn.executemany(
            f"UPDATE {SIM_TABLE} SET {RS_COLUMN} = ? WHERE ticker = ? AND Date = ?",
            zip(values.tolist(), frame['ticker'].tolist(), frame['Date'].tolist()))
        conn.commit()
        n = conn.total_changes - before
        logger.info("mtt: RS rating written for %d rows of %s (%d rated)",
                    n, os.path.basename(sim_db_path), int(frame['mtt_rs'].notna().sum()))
        return n
    finally:
        conn.close()


def stored_rs(symbol: str, years, db_path: str = 'database') -> pd.Series:
    """Stored ``mtt_rs`` of *symbol* across the sim DBs of *years* (date-indexed)."""
    from datetime import datetime
    from tradinglib.tools import Tools
    tools = Tools()
    current = datetime.now().year
    parts = []
    for year in sorted({int(y) for y in years if y}):
        name = 'asset_simulation_.db' if year == current else f'asset_simulation_{year}.db'
        path = tools.get_path(path=db_path, file_name=name)
        if not path or not os.path.exists(path):
            continue
        con = _ro(path)
        try:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({SIM_TABLE})")}
            if RS_COLUMN not in cols:
                continue
            rows = con.execute(f"SELECT Date, {RS_COLUMN} FROM {SIM_TABLE} "
                               f"WHERE ticker = ? AND {RS_COLUMN} > 0", (symbol,)).fetchall()
        except Exception:
            logger.debug("mtt: stored RS read failed for %s", name, exc_info=True)
            continue
        finally:
            con.close()
        if rows:
            parts.append(pd.Series([r[1] for r in rows],
                                   index=pd.to_datetime([str(r[0])[:10] for r in rows])))
    if not parts:
        return pd.Series(dtype=float)
    series = pd.concat(parts)
    return series[~series.index.duplicated(keep='last')].sort_index()
