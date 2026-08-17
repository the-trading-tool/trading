"""4 Phase Sequence method (4PS) — trend-continuation screening engine.

The method looks for stocks that have *proven* they can outperform the index and
then buys them at the start of their next trend leg. Four phases:

  Phase 1  Proven performance history — at least one completed monthly up-leg of
           ``trend_min_pct`` (default 90 %). The benchmark index itself bounces
           60–90 % off its weekly 200 SMA, so an outperformer must beat that.
  Phase 2  Consolidation base — a sideways range near the *record* high from
           which the next trend starts. The reference is the high of a long
           window (``record_weeks``, 10 years by default), never the trailing
           52-week high: that one falls along with a declining price, so a stock
           deep in a downtrend would pass the "near the highs" test trivially.
  Phase 3  Consolidation breakout — close above the base high and above a rising
           weekly trend average (``require_uptrend``); entry. The trend condition
           mirrors the exit rule — entering below that average would open a
           position that is already due to be sold.
  Phase 4  Trend confirmed — price holds above the breakout level and above the
           rising weekly trend average; position is held.

Everything in here is *causal*: the value on day D uses only data up to and
including D. Base levels come from completed weekly bars, the Phase-1 history
from confirmed monthly zigzag legs. The resulting ``fps_*`` columns are therefore
safe to persist into ``asset_simulation_*.db`` and to use in backtest formulas.

Columns produced by :func:`compute` (daily index):

  fps_phase        0..4 — current phase (see above)
  fps_best_trend   best completed monthly up-leg so far, in %
  fps_trend_gain   gain of the running up-leg since the last confirmed low, in %
  fps_base_high    high of the active consolidation base (0 = no base)
  fps_base_low     low of the active consolidation base (0 = no base)
  fps_base_weeks   length of the active base in weeks (0 = no base)
  fps_breakout     1.0 on the day the base high is broken, else 0
  fps_buy          close on a buy signal, else NaN
  fps_sell         close on a sell signal, else NaN
  fps_stop         active stop level while in a position, else 0
  fps_target       target level derived from ``target_pct``, else 0
  fps_rs           52-week relative strength vs the benchmark, in percentage points
  fps_dist_high    distance to the all-time high, in % (<= 0)

CLI::

    python -m tradinglib.four_ps /ticker:APH
    python -m tradinglib.four_ps /index:^GDAXI [/benchmark:^SPX] [/limit:40]
    python -m tradinglib.four_ps /regime:^SPX
"""
from __future__ import annotations

import os
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Phase labels (English — the UI translates them via the 'fps.phase_*' keys)
PHASE_NAMES = {
    0: "No history",
    1: "Watchlist",
    2: "Base",
    3: "Breakout",
    4: "Trend",
}

# Every tunable of the method in one place. The indicator, the page and the CLI
# all merge their overrides onto this dict, so a default only ever changes here.
DEFAULTS: dict = {
    # Phase 1 — proven performance history (monthly closes)
    'reversal_pct':    25.0,   # zigzag reversal that confirms a monthly swing
    'trend_min_pct':   90.0,   # an up-leg counts as "proven" from this gain on
    'min_trends':      1,      # how many such legs are required
    # Phase 2 — consolidation base (weekly bars)
    'base_weeks':      8,      # minimum length of the base
    'max_base_weeks':  60,     # maximum length considered
    'base_depth_pct':  25.0,   # max (high-low)/low of the base
    'near_high_pct':   15.0,   # base high must sit within this % of the record high
    'record_weeks':    520,    # window the "record high" is taken from (10 years).
                               # Using the 52-week high instead lets a stock that
                               # has been falling for a year pass trivially — its
                               # reference falls with it. The method wants names
                               # AT their records, so the window has to be long.
    # Phase 3 — breakout
    'breakout_pct':    0.5,    # close must exceed the base high by this much
    'vol_factor':      0.0,    # volume vs 10-week average (0 = no volume filter)
    'require_uptrend': True,   # only break out above a RISING weekly trend average
                               # — the exit uses that same average, so entering
                               # below it would open a position that is already
                               # due for the exit
    'slope_weeks':     8,      # lookback for "the trend average is rising"
    # Phase 4 — confirmation / position management
    'confirm_weeks':   2,      # weeks above the breakout level before "confirmed"
    'trend_sma_weeks': 40,     # weekly trend average (exit + confirmation filter).
                               # Measured over 656 index members since 2015 a slower
                               # average beats a faster one in every sub-period and
                               # both regions (PF 2.03 at 30 weeks -> 2.40 at 40 with
                               # the wider stop) — it simply lets the winners run.
    'stop_pct':        12.0,   # initial stop below the breakout level (8 % stopped
                               # intact trends out on noise)
    'trail_pct':       0.0,    # trailing stop off the highest close (0 = off)
    'target_pct':      80.0,   # target level = entry * (1 + target_pct/100)
    'take_profit':     False,  # True = sell at the target instead of holding
    # Context
    'benchmark':       '^SPX',  # relative-strength reference
    'min_years':       8,      # history required before a ticker is scored
}


# ── Data access ───────────────────────────────────────────────────────────────

def _db_dir(db_path: str | None = None) -> str:
    """Directory the DB files live in — the TradingDB env var wins, as everywhere."""
    return os.environ.get("TradingDB") or db_path or "database"


def _p(name: str, db_path: str | None = None) -> str:
    """Full path of a DB file (mirrors tools.Tools.get_path's TradingDB handling)."""
    return os.path.join(_db_dir(db_path), name)


def load_daily(ticker: str, db_path: str | None = None) -> pd.DataFrame:
    """Full local daily OHLCV history of *ticker* (empty frame when unavailable).

    Reads ``yf_<ticker>.db`` directly — the method needs 10+ years, which is more
    than the chart pipeline usually loads.
    """
    path = _p(f"yf_{ticker}.db", db_path)
    if not os.path.exists(path):
        return pd.DataFrame()
    con = sqlite3.connect(path)
    try:
        df = pd.read_sql_query(
            "SELECT Date, Open, High, Low, Close, Volume FROM day_data "
            "WHERE Close IS NOT NULL GROUP BY Date ORDER BY Date", con)
    except Exception:
        logger.debug("four_ps: no day_data for %s", ticker, exc_info=True)
        return pd.DataFrame()
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df["Date"], errors="coerce")
    df = df.drop(columns=["Date"])
    for c in ("Open", "High", "Low", "Close", "Volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[~df.index.isna()].dropna(subset=["Close"]).sort_index()


def index_members(index: str, db_path: str | None = None) -> list[str]:
    """Ticker symbols of an index/group from yf_tickers.db."""
    con = sqlite3.connect(_p("yf_tickers.db", db_path))
    try:
        rows = con.execute(
            """SELECT s.Ticker FROM stocks s
               JOIN stock_indices si ON si.stock_id = s.id
               JOIN indices i ON i.id = si.index_id
               WHERE UPPER(i.name) = UPPER(?)""", (index,)).fetchall()
        return sorted({r[0] for r in rows if r[0]})
    except Exception:
        logger.debug("four_ps: member query failed for %s", index, exc_info=True)
        return []
    finally:
        con.close()


def index_list(db_path: str | None = None) -> list[str]:
    """All selectable universes: real indices (``^…``) first, then groups."""
    con = sqlite3.connect(_p("yf_tickers.db", db_path))
    try:
        names = [r[0] for r in con.execute("SELECT name FROM indices").fetchall() if r[0]]
    except Exception:
        return []
    finally:
        con.close()
    idx = sorted(n for n in names if n.startswith("^"))
    grp = sorted(n for n in names if not n.startswith("^"))
    return idx + grp


def sector_map(tickers: list[str], db_path: str | None = None) -> dict[str, str]:
    """ticker → sector from asset_info.db (tickers without a sector are omitted)."""
    if not tickers:
        return {}
    con = sqlite3.connect(_p("asset_info.db", db_path))
    try:
        out: dict[str, str] = {}
        for i in range(0, len(tickers), 500):
            chunk = tickers[i:i + 500]
            ph = ",".join("?" * len(chunk))
            rows = con.execute(
                f"SELECT ticker, sector FROM asset_info WHERE ticker IN ({ph})",
                chunk).fetchall()
            out.update({r[0]: r[1] for r in rows if r[1]})
        return out
    except Exception:
        logger.debug("four_ps: sector lookup failed", exc_info=True)
        return {}
    finally:
        con.close()


def window_return(daily: pd.DataFrame, weeks: int = 52) -> float | None:
    """Percentage return of the last *weeks* weeks (None when the history is short)."""
    if daily is None or daily.empty or 'Close' not in daily.columns:
        return None
    close = pd.to_numeric(daily['Close'], errors='coerce').dropna()
    if close.empty:
        return None
    start = close.index[-1] - pd.Timedelta(weeks=weeks)
    past = close[close.index <= start]
    if past.empty or not past.iloc[-1]:
        return None
    return float((close.iloc[-1] / past.iloc[-1] - 1.0) * 100.0)


def quick_return(ticker: str, weeks: int = 52, db_path: str | None = None) -> float | None:
    """Window return without loading the full history — reads only the tail rows.

    Used to build the sector peer group, where hundreds of tickers are needed but
    none of them has to be scored.
    """
    path = _p(f"yf_{ticker}.db", db_path)
    if not os.path.exists(path):
        return None
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT Date, Close FROM day_data WHERE Close IS NOT NULL "
            "ORDER BY Date DESC LIMIT ?", (int(weeks * 5 + 40),)).fetchall()
    except Exception:
        return None
    finally:
        con.close()
    if len(rows) < 20:
        return None
    df = pd.DataFrame(rows, columns=['Date', 'Close'])
    df.index = pd.to_datetime(df['Date'], errors='coerce')
    return window_return(df.sort_index()[['Close']], weeks)


def sector_reference(tickers: list[str], weeks: int = 52, db_path: str | None = None,
                     returns: dict[str, float] | None = None) -> dict:
    """Median return per sector over a peer group.

    ``returns`` lets a caller reuse figures it already has (the screener computes
    them while scoring anyway); everything missing is read from the local OHLC.
    The peer group is always the passed ticker list — i.e. "the sector inside the
    universe you are looking at", not some global sector index.
    """
    sectors = sector_map(tickers, db_path)
    rets: dict[str, float] = {}
    for tk in tickers:
        sec = sectors.get(tk)
        if not sec:
            continue
        val = (returns or {}).get(tk)
        if val is None:
            val = quick_return(tk, weeks, db_path)
        if val is not None:
            rets[tk] = float(val)
    if not rets:
        return {'weeks': weeks, 'medians': {}, 'counts': {}, 'returns': {}, 'sectors': {}}

    frame = pd.DataFrame({'ticker': list(rets), 'ret': list(rets.values())})
    frame['sector'] = frame['ticker'].map(sectors)
    grp = frame.groupby('sector')['ret']
    return {
        'weeks': weeks,
        'medians': grp.median().to_dict(),
        'counts': grp.size().to_dict(),
        'returns': rets,
        'sectors': {tk: sectors[tk] for tk in rets},
    }


def sector_context(ticker: str, reference: dict, own_return: float | None = None) -> dict:
    """Where *ticker* stands inside its sector: median, distance and percentile."""
    empty = {'sector': '', 'ret': None, 'median': None, 'vs_median': None,
             'rank': None, 'above': None, 'peers': 0}
    if not reference or not reference.get('medians'):
        return empty
    sector = reference['sectors'].get(ticker) or sector_map([ticker]).get(ticker, '')
    if not sector or sector not in reference['medians']:
        return empty
    ret = own_return if own_return is not None else reference['returns'].get(ticker)
    median = float(reference['medians'][sector])
    peers = [v for tk, v in reference['returns'].items()
             if reference['sectors'].get(tk) == sector]
    if ret is None:
        return {**empty, 'sector': sector, 'median': median, 'peers': len(peers)}
    rank = float(np.mean([ret >= v for v in peers]) * 100.0) if peers else None
    return {'sector': sector, 'ret': float(ret), 'median': median,
            'vs_median': float(ret - median), 'rank': rank,
            'above': bool(ret >= median), 'peers': len(peers)}


def long_names(tickers: list[str], db_path: str | None = None) -> dict[str, str]:
    """ticker → longName map from asset_info.db (missing entries are omitted)."""
    if not tickers:
        return {}
    con = sqlite3.connect(_p("asset_info.db", db_path))
    try:
        out = {}
        # Chunked IN(...) — SQLite's variable limit is 999 by default
        for i in range(0, len(tickers), 500):
            chunk = tickers[i:i + 500]
            ph = ",".join("?" * len(chunk))
            rows = con.execute(
                f"SELECT ticker, longName FROM asset_info WHERE ticker IN ({ph})",
                chunk).fetchall()
            out.update({r[0]: (r[1] or "") for r in rows})
        return out
    except Exception:
        logger.debug("four_ps: asset_info lookup failed", exc_info=True)
        return {}
    finally:
        con.close()


# ── Building blocks ───────────────────────────────────────────────────────────

def _params(overrides: dict | None = None) -> dict:
    p = dict(DEFAULTS)
    if overrides:
        p.update({k: v for k, v in overrides.items() if v is not None})
    return p


def _bars(daily: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Aggregate daily OHLCV into period bars indexed by a PeriodIndex.

    A PeriodIndex (instead of resample's timestamps) keeps the mapping back onto
    the daily rows exact: every daily row knows its own period, so "the value of
    the last *completed* period" is a plain ``shift(1)`` + reindex — no guessing
    about partial trailing bars.
    """
    key = daily.index.to_period(freq)
    out = daily.groupby(key).agg(Open=("Open", "first"), High=("High", "max"),
                                 Low=("Low", "min"), Close=("Close", "last"),
                                 Volume=("Volume", "sum"))
    return out.dropna(subset=["Close"])


def period_bars(daily: pd.DataFrame, freq: str = 'W-FRI') -> pd.DataFrame:
    """Public wrapper around :func:`_bars` for the UI (weekly/monthly candles)."""
    return _bars(daily, freq)


def _to_daily(series: pd.Series, daily_index: pd.DatetimeIndex, freq: str,
              completed_only: bool = True) -> pd.Series:
    """Project a period-indexed series onto the daily index.

    ``completed_only`` shifts by one period first, so a daily row never sees a
    value that contains its own (still running) week/month — that is what keeps
    the breakout levels free of look-ahead.
    """
    s = series.shift(1) if completed_only else series
    key = daily_index.to_period(freq)
    return pd.Series(s.reindex(key).to_numpy(), index=daily_index)


def zigzag(close: pd.Series, reversal_pct: float, trend_min_pct: float):
    """Causal zigzag over *close*.

    Returns ``(best, count, leg_low, legs)``:
      best     Series — best completed up-leg so far, in %
      count    Series — number of completed up-legs >= trend_min_pct so far
      leg_low  Series — price the running up-leg started from
      legs     list of dicts (start/end/confirmed timestamps + gain) for plotting

    A leg only enters ``best``/``count`` on the bar where the reversal confirms
    it — never on the bar of the (then still unknown) extreme.
    """
    vals = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    idx = close.index
    n = len(vals)
    best = np.zeros(n)
    count = np.zeros(n)
    leg_low = np.full(n, np.nan)
    legs: list[dict] = []
    if n == 0:
        return (pd.Series(best, index=idx), pd.Series(count, index=idx),
                pd.Series(leg_low, index=idx), legs)

    rev = float(reversal_pct) / 100.0
    direction = 1                    # 1 = looking for a high, -1 = looking for a low
    pivot_price = vals[0]            # last confirmed pivot (a low while direction=1)
    pivot_i = 0
    extreme = vals[0]
    extreme_i = 0
    best_val = 0.0
    n_qual = 0

    for i in range(n):
        p = vals[i]
        if np.isnan(p):
            best[i], count[i] = best_val, n_qual
            leg_low[i] = pivot_price if direction > 0 else extreme
            continue
        if direction > 0:
            if p > extreme:
                extreme, extreme_i = p, i
            elif p <= extreme * (1.0 - rev):
                gain = (extreme / pivot_price - 1.0) * 100.0 if pivot_price else 0.0
                legs.append({'start': idx[pivot_i], 'end': idx[extreme_i],
                             'confirmed': idx[i], 'gain': gain})
                best_val = max(best_val, gain)
                if gain >= trend_min_pct:
                    n_qual += 1
                direction = -1
                pivot_price, pivot_i = extreme, extreme_i
                extreme, extreme_i = p, i
        else:
            if p < extreme:
                extreme, extreme_i = p, i
            elif p >= extreme * (1.0 + rev):
                direction = 1
                pivot_price, pivot_i = extreme, extreme_i
                extreme, extreme_i = p, i
        best[i], count[i] = best_val, n_qual
        # Start of the running up-leg: the last confirmed low while rising, the
        # running low while still falling.
        leg_low[i] = pivot_price if direction > 0 else extreme

    return (pd.Series(best, index=idx), pd.Series(count, index=idx),
            pd.Series(leg_low, index=idx), legs)


def _base_levels(weekly: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Longest qualifying consolidation window per weekly bar.

    For every bar the longest window L in [base_weeks, max_base_weeks] whose
    (high-low)/low stays within ``base_depth_pct`` wins. The range grows
    monotonically with L, so scanning L upwards and keeping the last hit yields
    exactly that longest window.
    """
    high, low = weekly["High"], weekly["Low"]
    n = len(weekly)
    base_len = np.zeros(n)
    base_high = np.zeros(n)
    base_low = np.zeros(n)
    for L in range(int(p['base_weeks']), int(p['max_base_weeks']) + 1):
        if L > n:
            break
        rmax = high.rolling(L).max().to_numpy()
        rmin = low.rolling(L).min().to_numpy()
        with np.errstate(divide='ignore', invalid='ignore'):
            depth = np.where(rmin > 0, (rmax / rmin - 1.0) * 100.0, np.inf)
        ok = np.nan_to_num(depth, nan=np.inf) <= float(p['base_depth_pct'])
        base_len = np.where(ok, L, base_len)
        base_high = np.where(ok, rmax, base_high)
        base_low = np.where(ok, rmin, base_low)
    return pd.DataFrame({'base_len': base_len, 'base_high': base_high,
                         'base_low': base_low}, index=weekly.index)


# ── Core computation ──────────────────────────────────────────────────────────

def compute(daily: pd.DataFrame, benchmark_close: pd.Series | None = None,
            **overrides) -> pd.DataFrame:
    """Return a DataFrame of ``fps_*`` columns on the index of *daily*."""
    p = _params(overrides)
    out = pd.DataFrame(index=daily.index)
    if daily is None or daily.empty or 'Close' not in daily.columns:
        return out

    close = pd.to_numeric(daily['Close'], errors='coerce')

    # ── Phase 1: monthly zigzag history ──────────────────────────────────────
    monthly = _bars(daily, 'M')
    m_best, m_count, m_leg_low, legs = zigzag(
        monthly['Close'], p['reversal_pct'], p['trend_min_pct'])
    best_trend = _to_daily(m_best, daily.index, 'M')
    qual_count = _to_daily(m_count, daily.index, 'M')
    leg_low = _to_daily(m_leg_low, daily.index, 'M')
    qualified = qual_count.fillna(0) >= float(p['min_trends'])

    # ── Phase 2: weekly consolidation base ───────────────────────────────────
    weekly = _bars(daily, 'W-FRI')
    lv = _base_levels(weekly, p)
    record_high = weekly['High'].rolling(int(p['record_weeks']), min_periods=20).max()
    near_high = lv['base_high'] >= (1.0 - float(p['near_high_pct']) / 100.0) * record_high
    sma_w = weekly['Close'].rolling(int(p['trend_sma_weeks']),
                                    min_periods=int(p['trend_sma_weeks'])).mean()
    slope_up = sma_w > sma_w.shift(int(p['slope_weeks']))
    vol_ok = pd.Series(True, index=weekly.index)
    if float(p['vol_factor']) > 0:
        vol_avg = weekly['Volume'].rolling(10, min_periods=4).mean()
        vol_ok = weekly['Volume'] >= float(p['vol_factor']) * vol_avg

    base_high = _to_daily(lv['base_high'], daily.index, 'W-FRI').fillna(0.0)
    base_low = _to_daily(lv['base_low'], daily.index, 'W-FRI').fillna(0.0)
    base_len = _to_daily(lv['base_len'], daily.index, 'W-FRI').fillna(0.0)
    base_near = _to_daily(near_high.astype(float), daily.index, 'W-FRI').fillna(0.0) > 0
    trend_sma = _to_daily(sma_w, daily.index, 'W-FRI')
    vol_flag = _to_daily(vol_ok.astype(float), daily.index, 'W-FRI').fillna(1.0) > 0
    slope_flag = _to_daily(slope_up.astype(float), daily.index, 'W-FRI').fillna(0.0) > 0

    has_base = (base_len >= float(p['base_weeks'])) & (base_high > 0) & base_near & qualified

    # ── Relative strength vs benchmark (52 weeks) ────────────────────────────
    rs = pd.Series(np.nan, index=daily.index)
    own_ret = close / close.shift(252) - 1.0
    if benchmark_close is not None and not benchmark_close.empty:
        bm = pd.to_numeric(benchmark_close, errors='coerce').reindex(
            daily.index, method='ffill')
        bm_ret = bm / bm.shift(252) - 1.0
        rs = (own_ret - bm_ret) * 100.0
    else:
        rs = own_ret * 100.0

    # ── Phases 3 + 4: state machine on daily closes ──────────────────────────
    c = close.to_numpy(dtype=float)
    bh = base_high.to_numpy(dtype=float)
    bl = base_low.to_numpy(dtype=float)
    blen = base_len.to_numpy(dtype=float)
    hb = has_base.to_numpy(dtype=bool)
    qz = qualified.to_numpy(dtype=bool)
    sw = trend_sma.to_numpy(dtype=float)
    vk = vol_flag.to_numpy(dtype=bool)
    sl = slope_flag.to_numpy(dtype=bool)
    need_up = bool(p['require_uptrend'])
    n = len(c)

    phase = np.zeros(n)
    breakout = np.zeros(n)
    buy = np.full(n, np.nan)
    sell = np.full(n, np.nan)
    stop_col = np.zeros(n)
    target_col = np.zeros(n)
    act_high = np.zeros(n)
    act_low = np.zeros(n)
    act_len = np.zeros(n)

    in_pos = False
    confirmed = False
    level = stop = target = 0.0     # breakout level / stop / target of the position
    pos_low = pos_len = 0.0         # base of the position, frozen at entry
    peak = 0.0                      # highest close since entry (trailing stop)
    days_above = 0
    confirm_days = max(1, int(round(float(p['confirm_weeks']) * 5)))
    buf = 1.0 + float(p['breakout_pct']) / 100.0

    for i in range(n):
        price = c[i]
        if np.isnan(price):
            phase[i] = phase[i - 1] if i else 0
            continue

        if in_pos:
            peak = max(peak, price)
            if float(p['trail_pct']) > 0:
                stop = max(stop, peak * (1.0 - float(p['trail_pct']) / 100.0))
            trend_break = (not np.isnan(sw[i])) and price < sw[i]
            hit_target = bool(p['take_profit']) and target > 0 and price >= target
            if price < stop or trend_break or hit_target:
                sell[i] = price
                in_pos = confirmed = False
                level = stop = target = peak = pos_low = pos_len = 0.0
                days_above = 0
                phase[i] = 2.0 if hb[i] else (1.0 if qz[i] else 0.0)
                act_high[i] = bh[i] if hb[i] else 0.0
                act_low[i] = bl[i] if hb[i] else 0.0
                act_len[i] = blen[i] if hb[i] else 0.0
                continue
            if not confirmed:
                days_above = days_above + 1 if price > level else 0
                if days_above >= confirm_days and (np.isnan(sw[i]) or price > sw[i]):
                    confirmed = True
            phase[i] = 4.0 if confirmed else 3.0
            stop_col[i] = stop
            target_col[i] = target
            act_high[i] = level
            act_low[i] = pos_low
            act_len[i] = pos_len
            continue

        # Not in a position — watch for the breakout out of an intact base.
        # The uptrend filter mirrors the exit rule: no entry below/against the
        # weekly trend average the position would immediately be sold on.
        trend_ok = (not need_up) or (not np.isnan(sw[i]) and price > sw[i] and sl[i])
        if hb[i] and price > bh[i] * buf and vk[i] and trend_ok:
            level = bh[i]
            stop = max(bl[i], level * (1.0 - float(p['stop_pct']) / 100.0))
            target = price * (1.0 + float(p['target_pct']) / 100.0)
            peak = price
            pos_low, pos_len = bl[i], blen[i]
            in_pos, confirmed, days_above = True, False, 0
            breakout[i] = 1.0
            buy[i] = price
            phase[i] = 3.0
            stop_col[i] = stop
            target_col[i] = target
            act_high[i] = level
            act_low[i] = bl[i]
            act_len[i] = blen[i]
        elif hb[i]:
            phase[i] = 2.0
            act_high[i] = bh[i]
            act_low[i] = bl[i]
            act_len[i] = blen[i]
        elif qz[i]:
            phase[i] = 1.0
        else:
            phase[i] = 0.0

    out['fps_phase'] = phase
    out['fps_best_trend'] = best_trend.fillna(0.0)
    out['fps_trend_gain'] = np.where(leg_low.to_numpy(dtype=float) > 0,
                                     (c / leg_low.to_numpy(dtype=float) - 1.0) * 100.0, 0.0)
    out['fps_base_high'] = act_high
    out['fps_base_low'] = act_low
    out['fps_base_weeks'] = act_len
    out['fps_breakout'] = breakout
    out['fps_buy'] = buy
    out['fps_sell'] = sell
    out['fps_stop'] = stop_col
    out['fps_target'] = target_col
    out['fps_rs'] = rs.fillna(0.0)
    out['fps_dist_high'] = (close / close.cummax() - 1.0) * 100.0
    return out


# ── Per-ticker analysis ───────────────────────────────────────────────────────

_bm_cache: dict[str, pd.Series] = {}


def benchmark_series(benchmark: str, db_path: str | None = None) -> pd.Series:
    """Cached daily close series of the relative-strength benchmark."""
    key = f"{benchmark}|{_db_dir(db_path)}"
    if key not in _bm_cache:
        df = load_daily(benchmark, db_path)
        _bm_cache[key] = df['Close'] if not df.empty else pd.Series(dtype=float)
    return _bm_cache[key]


def analyze(ticker: str, db_path: str | None = None, daily: pd.DataFrame | None = None,
            **overrides) -> dict:
    """Score a single ticker; returns a summary dict (``ok=False`` when skipped)."""
    p = _params(overrides)
    if daily is None:
        daily = load_daily(ticker, db_path)
    if daily is None or daily.empty:
        return {'ticker': ticker, 'ok': False, 'reason': 'no data'}
    years = (daily.index[-1] - daily.index[0]).days / 365.25
    if years < float(p['min_years']):
        return {'ticker': ticker, 'ok': False, 'reason': f'history {years:.1f}y'}

    bm = benchmark_series(p['benchmark'], db_path) if p['benchmark'] else None
    if p['benchmark'] and str(p['benchmark']) == str(ticker):
        bm = None
    fps = compute(daily, bm, **p)
    if fps.empty:
        return {'ticker': ticker, 'ok': False, 'reason': 'no result'}

    last = fps.iloc[-1]
    price = float(daily['Close'].iloc[-1])
    base_high = float(last['fps_base_high'])
    phase = int(last['fps_phase'])
    # Distance to the trigger: positive = still below the breakout level
    to_break = ((base_high / price - 1.0) * 100.0) if base_high > 0 and phase <= 2 else 0.0

    buys = fps['fps_buy'].dropna()
    sells = fps['fps_sell'].dropna()
    last_buy = buys.index[-1] if len(buys) else None
    last_sell = sells.index[-1] if len(sells) else None
    signal, signal_date = '', None
    if last_buy is not None and (last_sell is None or last_buy > last_sell):
        signal, signal_date = 'buy', last_buy
    elif last_sell is not None:
        signal, signal_date = 'sell', last_sell

    return {
        'ticker': ticker,
        'ok': True,
        'phase': phase,
        'phase_name': PHASE_NAMES.get(phase, '-'),
        'price': price,
        'date': daily.index[-1],
        'best_trend': float(last['fps_best_trend']),
        'trend_gain': float(last['fps_trend_gain']),
        'base_high': base_high,
        'base_low': float(last['fps_base_low']),
        'base_weeks': int(last['fps_base_weeks']),
        'to_breakout': to_break,
        'stop': float(last['fps_stop']),
        'target': float(last['fps_target']),
        'rs': float(last['fps_rs']),
        'dist_high': float(last['fps_dist_high']),
        # Raw window return — the sector comparison is built from these
        'ret_52w': window_return(daily, 52),
        'signal': signal,
        'signal_date': signal_date,
        'days_since_signal': (int((daily.index[-1] - signal_date).days)
                              if signal_date is not None else None),
        'years': years,
        'frame': fps,
        'daily': daily,
    }


def scan(tickers: list[str], db_path: str | None = None, workers: int = 1,
         progress=None, **overrides) -> pd.DataFrame:
    """Score every ticker and return the results as a DataFrame (one row each).

    ``progress`` is an optional ``callback(done, total)`` for the UI.

    ``workers`` defaults to 1 on purpose: the work is CPU-bound pandas/NumPy plus
    a per-bar state machine, so threads only add GIL contention — measured on
    60 S&P members: 6.3 s with one worker, 21.6 s with eight.
    """
    p = _params(overrides)
    if p['benchmark']:
        benchmark_series(p['benchmark'], db_path)   # warm the cache single-threaded

    rows: list[dict] = []
    total = len(tickers)
    done = 0

    def _collect(res: dict) -> None:
        if not res.get('ok'):
            return
        res.pop('frame', None)
        res.pop('daily', None)
        rows.append(res)

    def _tick() -> None:
        if progress and (done % 10 == 0 or done == total):
            try:
                progress(done, total)
            except Exception:
                pass

    if int(workers) <= 1:
        for tk in tickers:
            done += 1
            _tick()
            try:
                _collect(analyze(tk, db_path, None, **p))
            except Exception:
                logger.debug("four_ps: scan failed for %s", tk, exc_info=True)
    else:
        with ThreadPoolExecutor(max_workers=int(workers)) as ex:
            futures = {ex.submit(analyze, tk, db_path, None, **p): tk for tk in tickers}
            for fut in as_completed(futures):
                done += 1
                _tick()
                try:
                    _collect(fut.result())
                except Exception:
                    logger.debug("four_ps: scan failed for %s", futures[fut], exc_info=True)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    names = long_names(list(df['ticker']), db_path)
    df['name'] = df['ticker'].map(names).fillna('')

    # Sector standing — peer group is the scanned universe itself. The window
    # returns already exist from the scoring pass, so this costs one asset_info
    # query and no extra price reads.
    own = {r['ticker']: r['ret_52w'] for _, r in df.iterrows() if r.get('ret_52w') is not None}
    ref = sector_reference(list(df['ticker']), 52, db_path, returns=own)
    ctx = [sector_context(tk, ref, own.get(tk)) for tk in df['ticker']]
    df['sector'] = [c['sector'] for c in ctx]
    df['sector_median'] = [c['median'] for c in ctx]
    df['vs_sector'] = [c['vs_median'] for c in ctx]
    df['sector_rank'] = [c['rank'] for c in ctx]
    df['sector_peers'] = [c['peers'] for c in ctx]

    return df.sort_values(['phase', 'rs'], ascending=[False, False]).reset_index(drop=True)


# ── Index regime (the "trampoline" context from the method) ───────────────────

def index_regime(benchmark: str = '^SPX', db_path: str | None = None,
                 reversal_pct: float = 15.0, sma_weeks: int = 200) -> dict:
    """Weekly regime of the reference index: 200-week SMA plus its up-legs.

    Mirrors the observation the method builds on — the index bounces off its
    weekly 200 SMA and runs 60–90 % before the next correction.
    """
    daily = load_daily(benchmark, db_path)
    if daily.empty:
        return {'ok': False, 'benchmark': benchmark}
    weekly = _bars(daily, 'W-FRI')
    sma = weekly['Close'].rolling(sma_weeks, min_periods=sma_weeks).mean()
    best, count, leg_low, legs = zigzag(weekly['Close'], reversal_pct, 60.0)

    close = float(weekly['Close'].iloc[-1])
    sma_last = float(sma.iloc[-1]) if not np.isnan(sma.iloc[-1]) else 0.0
    cur_low = float(leg_low.iloc[-1]) if len(leg_low) else 0.0
    return {
        'ok': True,
        'benchmark': benchmark,
        'close': close,
        'sma': sma_last,
        'above_sma': bool(sma_last and close > sma_last),
        'dist_sma': ((close / sma_last - 1.0) * 100.0) if sma_last else 0.0,
        'current_leg': ((close / cur_low - 1.0) * 100.0) if cur_low else 0.0,
        'leg_start': leg_low.index[-1].to_timestamp() if len(leg_low) else None,
        'legs': [{'start': l['start'].to_timestamp(), 'end': l['end'].to_timestamp(),
                  'gain': l['gain']} for l in legs],
        'weekly': weekly,
        'sma_series': sma,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli(argv: list[str]) -> None:
    ticker = index = regime = ''
    benchmark = DEFAULTS['benchmark']
    limit = 40
    for a in argv:
        al = a.lower()
        if al.startswith('/ticker:'):
            ticker = a.split(':', 1)[1]
        elif al.startswith('/index:'):
            index = a.split(':', 1)[1]
        elif al.startswith('/regime:'):
            regime = a.split(':', 1)[1]
        elif al.startswith('/benchmark:'):
            benchmark = a.split(':', 1)[1]
        elif al.startswith('/limit:'):
            try:
                limit = int(a.split(':', 1)[1])
            except ValueError:
                pass

    if regime:
        r = index_regime(regime)
        if not r.get('ok'):
            print(f"four_ps: no data for {regime}")
            return
        print(f"\n  {regime}: close {r['close']:.0f} / 200w SMA {r['sma']:.0f} "
              f"({r['dist_sma']:+.1f} %) — {'above' if r['above_sma'] else 'BELOW'}")
        print(f"  current up-leg: {r['current_leg']:+.1f} %\n")
        for leg in r['legs'][-8:]:
            print(f"    {leg['start']:%Y-%m} - {leg['end']:%Y-%m}  {leg['gain']:+7.1f} %")
        print()
        return

    if ticker:
        res = analyze(ticker, benchmark=benchmark)
        if not res.get('ok'):
            print(f"four_ps: {ticker} skipped ({res.get('reason')})")
            return
        print(f"\n  {ticker} — Phase {res['phase']} ({res['phase_name']})")
        print(f"  price {res['price']:.2f} · best past trend {res['best_trend']:.0f} % · "
              f"running leg {res['trend_gain']:+.0f} %")
        print(f"  base {res['base_weeks']}w {res['base_low']:.2f}–{res['base_high']:.2f} · "
              f"to breakout {res['to_breakout']:+.1f} %")
        print(f"  RS(52w) {res['rs']:+.1f} pp · off high {res['dist_high']:.1f} % · "
              f"last signal {res['signal'] or '-'} "
              f"{res['signal_date']:%Y-%m-%d}" if res['signal_date'] is not None else "")
        print()
        return

    if index:
        members = index_members(index)
        if not members:
            print(f"four_ps: no members for {index}")
            return
        df = scan(members, benchmark=benchmark)
        if df.empty:
            print("four_ps: no candidates")
            return
        cols = ['ticker', 'phase', 'base_weeks', 'to_breakout', 'best_trend', 'rs', 'price']
        print(df[cols].head(limit).to_string(index=False))
        return

    print(__doc__)


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    _cli(sys.argv[1:])
