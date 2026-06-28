"""Regime Data Engine — vectorized Markov regime labels from local OHLCV data.

All heavy computation is parallelised with ThreadPoolExecutor and cached for
1 hour via @st.cache_data so repeat renders are instant.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
import logging

import numpy as np
import pandas as pd
import streamlit as st

from tradinglib import tools as ts
from tradinglib.fetch_data import FetchData

logger = logging.getLogger(__name__)

# ── Constants (same thresholds as Markov indicator) ───────────────────────────

_LOOKBACK = 20
_BULL_PCT  = 0.05
_BEAR_PCT  = 0.05
_WORKERS   = 8

# Maps max needed days (2 × interval) to FetchData period string
_PERIOD_LADDER = [
    (90,   '6mo'),
    (365,  '2y'),
    (9999, '2y'),
]

# Yahoo Finance sector names → German display labels
_SECTOR_DE = {
    'Technology':             'Technologie',
    'Financial Services':     'Finanzen',
    'Consumer Cyclical':      'Konsumgüter (zyklisch)',
    'Consumer Defensive':     'Konsumgüter (defensiv)',
    'Healthcare':             'Gesundheit',
    'Energy':                 'Energie',
    'Basic Materials':        'Rohstoffe',
    'Industrials':            'Industrie',
    'Real Estate':            'Immobilien',
    'Utilities':              'Versorger',
    'Communication Services': 'Kommunikation',
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _period_str(interval_days: int) -> str:
    """Choose FetchData period that covers 2 × interval_days (for arrows)."""
    needed = interval_days * 2
    for threshold, period in _PERIOD_LADDER:
        if needed <= threshold:
            return period
    return '5y'


# Exchange → readable geographic/market label
_EXCHANGE_GRP = {
    'XETRA': 'Aktien — XETRA',
    'GER':   'Aktien — XETRA',
    'FRA':   'Aktien — Frankfurt',
    'NYSE':  'Aktien — NYSE',
    'NYQ':   'Aktien — NYSE',
    'NMS':   'Aktien — Nasdaq',
    'NGM':   'Aktien — Nasdaq',
    'NASDAQ':'Aktien — Nasdaq',
    'LSE':   'Aktien — London',
    'TYO':   'Aktien — Tokio',
    'SWX':   'Aktien — Schweiz',
    'AMS':   'Aktien — Amsterdam',
    'PAR':   'Aktien — Paris',
    'MCE':   'Aktien — Madrid',
    'MIL':   'Aktien — Mailand',
    'BRU':   'Aktien — Brüssel',
    'HEL':   'Aktien — Helsinki',
    'STO':   'Aktien — Stockholm',
    'CPH':   'Aktien — Kopenhagen',
    'OSL':   'Aktien — Oslo',
    'ASX':   'Aktien — Australien',
    'HKG':   'Aktien — Hongkong',
    'SNP':   'Aktien — S&P',
}


def _sector_group(sector: str, exchange: str, ticker: str,
                  index_name: str = '') -> str:
    """Priority: Yahoo sector → ticker heuristic → index membership → exchange → 'Sonstige'."""
    s = str(sector).strip()
    if s and s not in ('', 'nan', 'None', 'N/A', 'none'):
        return _SECTOR_DE.get(s, s)

    t  = str(ticker).upper()
    ex = str(exchange).upper()

    if t.startswith('^'):
        return 'Indizes'
    if ex in ('CCY', 'FX') or t.endswith('=X'):
        return 'Währungen'
    if any(k in t for k in ('XAUUSD', 'GC=F', 'GLD', 'SGOL', 'GOLD')):
        return 'Rohstoffe — Edelmetalle'
    if any(k in t for k in ('CL=F', 'BZ=F', 'WTI', 'BRENT', 'USO', 'OIL')):
        return 'Rohstoffe — Energie'

    # Index-based group: use cleaned index name (strip leading ^)
    if index_name and str(index_name) not in ('', 'nan', 'None'):
        clean = str(index_name).lstrip('^').strip()
        if clean:
            return f'Index — {clean}'

    # Exchange-based geographic group
    grp = _EXCHANGE_GRP.get(ex)
    if grp:
        return grp

    return 'Sonstige'


def _label_regimes(close: pd.Series) -> np.ndarray:
    """Bull/Bear/Sideways per bar via rolling log-return (identical to Markov.data())."""
    log_ret = np.log(close / close.shift(_LOOKBACK))
    return np.where(
        log_ret.isna(), np.nan,
        np.where(log_ret > _BULL_PCT, 1.0,
                 np.where(log_ret < -_BEAR_PCT, 2.0, 0.0))
    )


def _compute_ticker(ticker: str, period: str) -> pd.DataFrame | None:
    """Load OHLCV from yf_{ticker}.db and compute regime / relvol / ewo. Thread-safe."""
    try:
        fd = FetchData(database_path='database')
        df = fd.load_price_data(ticker, period=period, interval='1d', aggregate=False)

        if df is None or df.empty or 'Close' not in df.columns:
            return None

        # load_price_data returns Date as the index (string-formatted).
        # Reset so we have a regular column, then parse to datetime.
        df = df.reset_index()
        date_col = 'Date' if 'Date' in df.columns else df.columns[0]
        df = df.rename(columns={date_col: 'Date'})
        df['Date'] = pd.to_datetime(df['Date'], format='mixed', errors='coerce')
        df = df.dropna(subset=['Date']).sort_values('Date').reset_index(drop=True)
        close = df['Close'].astype(float)

        if len(close) < _LOOKBACK + 5:
            return None

        # ── Regime (identical to Markov.data()) ──────────────────────────────
        regime = _label_regimes(close)

        # ── Relative volume ───────────────────────────────────────────────────
        volume  = df.get('Volume', pd.Series(0.0, index=df.index)).astype(float)
        avg_vol = volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
        relvol  = (volume / avg_vol).fillna(1.0).clip(0.1, 10.0)

        # ── EWO (EMA5 − EMA35 normalised by Close, in %) ─────────────────────
        ema5  = close.ewm(span=5,  adjust=False).mean()
        ema35 = close.ewm(span=35, adjust=False).mean()
        ewo   = (ema5 - ema35) / close.replace(0, np.nan) * 100.0

        out = pd.DataFrame({
            'ticker':      ticker,
            'Date':        df['Date'].values,
            'regime':      regime,
            'relvol_ratio': relvol.values,
            'ewo':         ewo.values,
        })
        return out.dropna(subset=['regime'])

    except Exception as exc:
        logger.debug("_compute_ticker %s: %s", ticker, exc)
        return None


# ── Public cached API ─────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def compute_regimes(tickers: tuple, interval_days: int) -> pd.DataFrame:
    """Parallel-load OHLCV and compute regime labels for all tickers.

    Returns long-format DataFrame: ticker | Date | regime | relvol_ratio | ewo
    Covers 2 × interval_days so the caller can split into prev/now periods.
    """
    if not tickers:
        return pd.DataFrame(columns=['ticker', 'Date', 'regime', 'relvol_ratio', 'ewo'])

    period = _period_str(interval_days)
    results = []

    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        futures = {ex.submit(_compute_ticker, t, period): t for t in tickers}
        for fut in as_completed(futures):
            try:
                df = fut.result()
                if df is not None and not df.empty:
                    results.append(df)
            except Exception:
                pass

    if not results:
        return pd.DataFrame(columns=['ticker', 'Date', 'regime', 'relvol_ratio', 'ewo'])

    out = pd.concat(results, ignore_index=True)
    out['Date']   = pd.to_datetime(out['Date']).dt.normalize()
    out['regime'] = out['regime'].astype(int)
    return out.sort_values(['ticker', 'Date']).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_sector_map() -> pd.DataFrame:
    """Return ticker → (longName, sector_group) from asset_info.db.

    Falls back to index-membership group (yf_tickers.db) then exchange
    when Yahoo Finance sector is missing.
    """
    # ── asset_info: sector + exchange + name ─────────────────────────────────
    db_info = ts.Db_tools(db_path='database', database_name='asset_info.db')
    try:
        df = pd.read_sql_query("""
            SELECT ticker,
                   COALESCE(longName, shortName, ticker) AS longName,
                   COALESCE(sector,   '')                AS sector,
                   COALESCE(exchange, '')                AS exchange
            FROM asset_info
        """, db_info.conn)
    except Exception as exc:
        logger.warning("load_sector_map asset_info: %s", exc)
        df = pd.DataFrame(columns=['ticker', 'longName', 'sector', 'exchange'])
    finally:
        try: db_info.conn.close()
        except Exception: pass

    # ── yf_tickers: primary index per stock (first/alphabetically first) ─────
    db_tk = ts.Db_tools(db_path='database', database_name='yf_tickers.db')
    try:
        # Exclude generic catch-all index names; prefer the most specific index.
        # MIN() on the CASE gives the alphabetically first real index name per ticker.
        idx_map = pd.read_sql_query("""
            SELECT s.Ticker AS ticker,
                   MIN(CASE WHEN UPPER(TRIM(i.name)) NOT IN ('INDEX','ALL','STOCKS','ETF','ETP',
                                                             'COMMODITIES','METALS','CURRENCIES','CRYPTO')
                            THEN i.name END) AS primary_index
            FROM stocks s
            JOIN stock_indices si ON s.id = si.stock_id
            JOIN indices i        ON si.index_id = i.id
            WHERE s.Ticker IS NOT NULL
            GROUP BY s.Ticker
        """, db_tk.conn)
    except Exception as exc:
        logger.warning("load_sector_map yf_tickers: %s", exc)
        idx_map = pd.DataFrame(columns=['ticker', 'primary_index'])
    finally:
        try: db_tk.conn.close()
        except Exception: pass

    # Merge index info and assign sector group
    df = df.merge(idx_map, on='ticker', how='left')
    df['primary_index'] = df['primary_index'].fillna('')

    df['sector_group'] = df.apply(
        lambda r: _sector_group(
            r['sector'], r['exchange'], r['ticker'], r['primary_index']
        ), axis=1
    )
    return df[['ticker', 'longName', 'sector_group']].copy()


@st.cache_data(ttl=3600, show_spinner=False)
def load_index_names() -> list:
    """All index names from yf_tickers.db."""
    db = ts.Db_tools(db_path='database', database_name='yf_tickers.db')
    try:
        df = pd.read_sql_query(
            "SELECT DISTINCT name FROM indices WHERE name IS NOT NULL ORDER BY name",
            db.conn
        )
        return df['name'].tolist()
    except Exception as exc:
        logger.warning("load_index_names: %s", exc)
        return []
    finally:
        try: db.conn.close()
        except Exception: pass


@st.cache_data(ttl=3600, show_spinner=False)
def load_index_members(index_name: str) -> tuple:
    """Tickers for one index from yf_tickers.db."""
    db = ts.Db_tools(db_path='database', database_name='yf_tickers.db')
    try:
        df = pd.read_sql_query("""
            SELECT s.Ticker
            FROM stocks s
            JOIN stock_indices si ON s.id = si.stock_id
            JOIN indices i       ON si.index_id = i.id
            WHERE i.name = ?
        """, db.conn, params=(index_name,))
        return tuple(df['Ticker'].dropna().tolist())
    except Exception as exc:
        logger.warning("load_index_members %s: %s", index_name, exc)
        return ()
    finally:
        try: db.conn.close()
        except Exception: pass


# ── Multi-timeframe breadth (Markov regime across day/week/month) ────────────

# Resample rule per timeframe — None means "use the daily bars as-is".
# Same lookback/thresholds as the daily regime (_LOOKBACK/_BULL_PCT/_BEAR_PCT)
# are applied to the resampled close series, so e.g. "week" reads as
# "Bull/Bear/Sideways over the last 20 weeks" — consistent across timeframes.
_BREADTH_TIMEFRAMES = {'day': None, 'week': 'W-FRI', 'month': 'ME'}
_BREADTH_PERIOD = '5y'   # covers 20 monthly bars + warmup with margin

# How many daily regimes to keep: today ('day') + N-1 prior trading days
# ('day_1' = yesterday … 'day_4'), so the widget can show a short-term tendency.
_BREADTH_DAY_HISTORY = 5
_BREADTH_DAY_HIST_COLS = [f'day_{k}' for k in range(1, _BREADTH_DAY_HISTORY)]


def _compute_breadth_ticker(ticker: str, period: str) -> dict | None:
    """Latest Markov regime (Bull/Bear/Sideways) on day/week/month bars. Thread-safe.

    Returns {'ticker': ..., 'day': 0|1|2|nan, 'day_1': …, 'day_4': …,
    'week': ..., 'month': ...} where day_1…day_4 are the regimes of the
    previous 4 trading days, or None if no usable price history is available.
    """
    try:
        fd = FetchData(database_path='database')
        df = fd.load_price_data(ticker, period=period, interval='1d', aggregate=False)
        if df is None or df.empty or 'Close' not in df.columns:
            return None

        df = df.reset_index()
        date_col = 'Date' if 'Date' in df.columns else df.columns[0]
        df = df.rename(columns={date_col: 'Date'})
        df['Date'] = pd.to_datetime(df['Date'], format='mixed', errors='coerce')
        df = df.dropna(subset=['Date']).sort_values('Date').set_index('Date')
        close = df['Close'].astype(float)

        out = {'ticker': ticker}
        for col in _BREADTH_DAY_HIST_COLS:
            out[col] = np.nan
        for tf, rule in _BREADTH_TIMEFRAMES.items():
            series = close if rule is None else close.resample(rule).last().dropna()
            if len(series) < _LOOKBACK + 5:
                out[tf] = np.nan
                continue
            valid = _label_regimes(series)
            valid = valid[~np.isnan(valid)]
            out[tf] = float(valid[-1]) if len(valid) else np.nan
            if tf == 'day':
                for k, col in enumerate(_BREADTH_DAY_HIST_COLS, start=1):
                    if len(valid) > k:
                        out[col] = float(valid[-1 - k])
        return out

    except Exception as exc:
        logger.debug("_compute_breadth_ticker %s: %s", ticker, exc)
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def compute_index_breadth(index_name: str, period: str = _BREADTH_PERIOD) -> pd.DataFrame:
    """Latest Bull/Bear/Sideways regime per index member, on day/week/month bars.

    Loads each member's daily OHLCV once, resamples to weekly/monthly closes and
    classifies the current regime on all three timeframes with the same rolling
    log-return model as the Markov indicator (lookback=20, ±5% thresholds) —
    just applied to bars of different size, so the breadth is directly comparable
    across timeframes.

    Returns a wide DataFrame: ticker | day | day_1 … day_4 | week | month
    (regime codes 0=Sideways, 1=Bull, 2=Bear, NaN where history is too short;
    day_1…day_4 are the previous 4 trading days for the short-term tendency).
    Cached for 1h — recompute via compute_index_breadth.clear() if needed sooner.
    """
    columns = ['ticker', 'day', *_BREADTH_DAY_HIST_COLS, 'week', 'month']
    tickers = load_index_members(index_name)
    if not tickers:
        return pd.DataFrame(columns=columns)

    results = []
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        futures = {ex.submit(_compute_breadth_ticker, t, period): t for t in tickers}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                if r is not None:
                    results.append(r)
            except Exception:
                pass

    if not results:
        return pd.DataFrame(columns=columns)

    return (pd.DataFrame(results)[columns]
            .sort_values('ticker').reset_index(drop=True))


def summarize_breadth(df: pd.DataFrame) -> dict:
    """Aggregate per-member regimes into breadth percentages + a composite market score.

    Returns:
        {
          'day':   {'bull': pct, 'bear': pct, 'side': pct, 'n': count},
          'day_1': {...} … 'day_4': {...},   # previous 4 trading days (tendency)
          'week':  {...}, 'month': {...},
          'score': float in [-1, +1],   # weighted (%Bull - %Bear), day=1/week=2/month=3
          'state': 'bull' | 'bear' | 'neutral',   # language-neutral code — UI translates via t()
          'aligned': bool,              # do day/week/month agree on the majority regime?
        }
    The day_1…day_4 history feeds the chart only — score/state/aligned are still
    derived from the current day/week/month columns alone.
    Empty dict if df has no usable rows.
    """
    if df is None or df.empty:
        return {}

    _weights = {'day': 1.0, 'week': 2.0, 'month': 3.0}
    tf_stats = {}
    weighted_sum = 0.0
    weight_total = 0.0
    majorities = {}

    hist_cols = [c for c in _BREADTH_DAY_HIST_COLS if c in df.columns]
    for tf in ('day', *hist_cols, 'week', 'month'):
        col = df[tf].dropna()
        n = len(col)
        if n == 0:
            tf_stats[tf] = {'bull': float('nan'), 'bear': float('nan'),
                            'side': float('nan'), 'n': 0}
            continue
        bull = float((col == 1).sum()) / n * 100.0
        bear = float((col == 2).sum()) / n * 100.0
        side = float((col == 0).sum()) / n * 100.0
        tf_stats[tf] = {'bull': bull, 'bear': bear, 'side': side, 'n': n}

        if tf not in _weights:   # history rows don't influence the score
            continue
        weighted_sum  += _weights[tf] * (bull - bear) / 100.0
        weight_total  += _weights[tf]
        majorities[tf] = max((bull, 1), (bear, 2), (side, 0))[1]

    if weight_total == 0:
        return {}

    score = weighted_sum / weight_total   # in [-1, +1]
    if score > 0.20:
        state = 'bull'
    elif score < -0.20:
        state = 'bear'
    else:
        state = 'neutral'

    aligned = len(set(majorities.values())) == 1 if len(majorities) == 3 else False

    return {**tf_stats, 'score': score, 'state': state, 'aligned': aligned}


# ── Early-warning market-stress score (breadth deterioration + divergence) ────
#
# Rationale: a cap-weighted index (^GDAXI, ^SPX, …) is held up by a few
# heavyweights while the *breadth* of its members rolls over first. We therefore
# read the daily breadth time series (% of members Bull/Bear/Sideways) over a
# recent window and derive a *leading* stress score from how that breadth is
# CHANGING — not merely its current level — plus a price/breadth divergence term
# (index still flat/up while breadth falls = the strongest early warning).
#
# This is a probabilistic breadth signal, not a crash predictor: it gives false
# positives (not every deterioration becomes a crash) and cannot see overnight
# news shocks (no breadth lead). The UI must phrase it as "uncertain market /
# raised caution", never "crash imminent".

_STRESS_PERIOD        = '6mo'   # daily bars for slopes + warmup
_STRESS_WINDOW        = 10      # trading days for momentum / divergence slopes
_STRESS_INTERVAL_DAYS = 45      # → compute_regimes loads ~6mo of daily data

# Per-component scaling: the slope (pp/day) / level mapping to "full" stress.
_STRESS_SLOPE_FULL    = 2.0     # ±2 pp/day breadth shift = maximal contribution
_STRESS_RELVOL_FULL   = 0.5     # relvol 1.0 → 1.5 = maximal contribution

# Composite weights (sum = 1.0).
_STRESS_WEIGHTS = {
    'bull_drop':  0.28,   # %Bull falling
    'bear_rise':  0.24,   # %Bear rising
    'side_rise':  0.14,   # %Sideways rising (churn / indecision)
    'relvol':     0.14,   # elevated volume across members (nervousness)
    'divergence': 0.20,   # index price flat/up while breadth deteriorates
}

# Score thresholds → traffic-light level (shared by stress and recovery).
_STRESS_ELEVATED = 33.0
_STRESS_WARNING  = 60.0

# ── Recovery / breadth-thrust (bottom-turn counterpart) ──────────────────────
# The early-warning score is a *top* detector — it stays low at a washed-out
# bottom even after a big fall. The recovery score is its mirror: breadth
# turning UP, gated by how washed-out the recent base was so a normal uptrend
# (rising breadth from a HIGH base) cannot reach the top level — only a thrust
# off a depressed base can.
_RECOVERY_BASE_WINDOW = 20      # trading days to find the recent %Bull trough
_RECOVERY_LOW_REF     = 25.0    # %Bull trough at/below this = fully washed out
_RECOVERY_TURN_WEIGHTS = {      # turn-momentum mix (sum = 1.0), then gated
    'bull_rise': 0.45,          # %Bull rising
    'bear_drop': 0.30,          # %Bear falling
    'relvol':    0.25,          # volume confirmation
}


def _stress_slope(series: pd.Series, window: int) -> float:
    """Least-squares slope (units per day) over the last `window` points."""
    s = series.dropna().tail(window)
    if len(s) < 3:
        return 0.0
    x = np.arange(len(s), dtype=float)
    try:
        return float(np.polyfit(x, s.values.astype(float), 1)[0])
    except Exception:
        return 0.0


def _index_change_pct(index_name: str, window: int) -> float:
    """Index close % change over the last `window` trading days (0.0 if n/a)."""
    try:
        fd = FetchData(database_path='database')
        df = fd.load_price_data(index_name, period=_STRESS_PERIOD,
                                interval='1d', aggregate=False)
        if df is None or df.empty or 'Close' not in df.columns:
            return 0.0
        close = df['Close'].astype(float).dropna()
        if len(close) <= window:
            return 0.0
        past, now = float(close.iloc[-window - 1]), float(close.iloc[-1])
        if past <= 0:
            return 0.0
        return (now - past) / past * 100.0
    except Exception:
        return 0.0


# ── Persistente Tages-Cache für den Market-Stress-Score ───────────────────────
# compute_market_stress() ist zwar @st.cache_data(ttl=1h), aber das lebt nur im
# RAM des jeweiligen Streamlit-Prozesses: nach Restart, Cache-Eviction oder im
# zweiten Worker wird die teure Berechnung (bis ~1900 Member via compute_regimes)
# erneut fällig. Da der Score auf Tageskerzen beruht, genügt EINE Berechnung pro
# Index und Kalendertag. Diese persistente Schicht (SQLite, WAL) sorgt dafür,
# dass nur der allererste Aufruf des Tages rechnet; alle weiteren — prozess- und
# neustartübergreifend — lesen das Ergebnis sofort.
_STRESS_CACHE_DB = 'regime_cache.db'


def _stress_cache_get(key: str):
    """Heutiges gecachtes Stress-Dict für key zurückgeben, sonst None."""
    try:
        db = ts.Tools().get_path(path='database', file_name=_STRESS_CACHE_DB)
        with ts.open_db(db, readonly=True) as conn:
            row = conn.execute(
                "SELECT payload FROM stress_cache WHERE cache_key=? AND day=?",
                (key, dt.date.today().isoformat()),
            ).fetchone()
        return json.loads(row[0]) if row and row[0] else None
    except Exception:
        return None


def _stress_cache_put(key: str, result: dict) -> None:
    """Stress-Dict für key+heute persistieren (Best effort)."""
    try:
        db = ts.Tools().get_path(path='database', file_name=_STRESS_CACHE_DB)
        with ts.open_db(db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stress_cache (
                    cache_key TEXT, day TEXT, payload TEXT,
                    PRIMARY KEY (cache_key, day)
                )""")
            conn.execute("""
                INSERT INTO stress_cache (cache_key, day, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key, day) DO UPDATE SET payload=excluded.payload
            """, (key, dt.date.today().isoformat(), json.dumps(result)))
            conn.commit()
    except Exception as exc:
        logger.debug("stress_cache_put %s: %s", key, exc)


@st.cache_data(ttl=3600, show_spinner=False)
def compute_market_stress(index_name: str) -> dict:
    """Early-warning market-stress score for one index, from its members' breadth.

    Builds the daily breadth series (% of members Bull/Bear/Sideways) over the
    recent window via compute_regimes() and derives a *leading* 0–100 stress
    score from its change plus an index price/breadth divergence term:

      * bull_drop   %Bull falling      (deterioration)
      * bear_rise   %Bear rising       (deterioration)
      * side_rise   %Sideways rising   (churn / indecision)
      * relvol      elevated volume    (nervousness)
      * divergence  index flat/up while %Bull falls → strongest warning

    Returns a dict (empty if no usable data / not a real ^-index):
      {
        'score': 0..100, 'level': 'calm'|'elevated'|'warning',
        'bull','bear','side': current breadth %,
        'bull_slope','bear_slope','side_slope': pp/day,
        'relvol': mean relvol_ratio across members,
        'divergence': bool, 'index_change': index % change over window,
        'components': {name: 0..100 contribution},  # for the UI "why"
        'n': member count, 'as_of': 'YYYY-MM-DD', 'window': int,
      }
    Cached 1h — recompute via compute_market_stress.clear() if needed sooner.
    """
    # Only real exchange indices (^…) carry both a member universe and an own
    # price series for the divergence term. Category groups (ETP, CURRENCIES…)
    # are not meaningful here — use compute_breadth_stress() for those.
    if not index_name or not str(index_name).startswith('^'):
        return {}

    # Persistenter Tages-Cache: nur der erste Aufruf des Tages rechnet.
    cached = _stress_cache_get(index_name)
    if cached is not None:
        return cached

    members = load_index_members(index_name)
    if not members:
        return {}

    df = compute_regimes(members, _STRESS_INTERVAL_DAYS)
    result = _stress_core(df, index_name=index_name)
    if result:                       # leere Ergebnisse nicht cachen → erneuter Versuch
        _stress_cache_put(index_name, result)
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def compute_breadth_stress(tickers: tuple) -> dict:
    """Slimmed early-warning score for an arbitrary ticker set (no index price).

    Same breadth-deterioration logic as compute_market_stress() but WITHOUT the
    price/breadth divergence term — for sources that are groupings rather than a
    real index (monitored assets, category groups like ETP/CURRENCIES) and thus
    have no own index price series. The divergence weight is redistributed across
    the remaining components. Returns the same dict shape (divergence always
    False, index_change 0.0); empty dict if no usable data.
    """
    if not tickers:
        return {}
    df = compute_regimes(tickers, _STRESS_INTERVAL_DAYS)
    return _stress_core(df, index_name=None)


def _stress_core(df: pd.DataFrame, index_name) -> dict:
    """Shared breadth-stress computation from a long regime DataFrame.

    index_name truthy → adds the price/breadth divergence term (index flat/up
    while breadth falls). index_name None → slimmed variant: the divergence
    component is dropped and its weight redistributed across the rest.
    """
    if df is None or df.empty:
        return {}

    # ── Daily breadth time series (% of members per regime) ──────────────────
    daily = (df.groupby(['Date', 'regime'])['ticker'].nunique()
             .reset_index(name='cnt'))
    total = df.groupby('Date')['ticker'].nunique().reset_index(name='total')
    piv = (daily.pivot(index='Date', columns='regime', values='cnt')
           .fillna(0.0))
    for r in (0, 1, 2):
        if r not in piv.columns:
            piv[r] = 0.0
    piv = piv.join(total.set_index('Date')).sort_index()
    if len(piv) < 5:
        return {}
    piv['bull'] = piv[1] / piv['total'].replace(0, np.nan) * 100.0
    piv['bear'] = piv[2] / piv['total'].replace(0, np.nan) * 100.0
    piv['side'] = piv[0] / piv['total'].replace(0, np.nan) * 100.0

    relvol_daily = df.groupby('Date')['relvol_ratio'].mean().sort_index()

    # ── Current levels + slopes (pp per day over the window) ─────────────────
    bull_now = float(piv['bull'].iloc[-1])
    bear_now = float(piv['bear'].iloc[-1])
    side_now = float(piv['side'].iloc[-1])
    relvol_now = float(relvol_daily.iloc[-1]) if len(relvol_daily) else 1.0

    bull_slope = _stress_slope(piv['bull'], _STRESS_WINDOW)
    bear_slope = _stress_slope(piv['bear'], _STRESS_WINDOW)
    side_slope = _stress_slope(piv['side'], _STRESS_WINDOW)

    # ── Component scores 0..100 ──────────────────────────────────────────────
    def _clip01(v: float) -> float:
        return float(max(0.0, min(1.0, v)))

    comp = {
        'bull_drop': _clip01(-bull_slope / _STRESS_SLOPE_FULL) * 100.0,
        'bear_rise': _clip01( bear_slope / _STRESS_SLOPE_FULL) * 100.0,
        'side_rise': _clip01( side_slope / _STRESS_SLOPE_FULL) * 100.0,
        'relvol':    _clip01((relvol_now - 1.0) / _STRESS_RELVOL_FULL) * 100.0,
    }

    weights = dict(_STRESS_WEIGHTS)
    if index_name:
        # Divergence: index flat/up while breadth deteriorates → leading warning.
        idx_change = _index_change_pct(index_name, _STRESS_WINDOW)
        divergence = (idx_change >= 0.0) and (bull_slope < -0.3)
        comp['divergence'] = comp['bull_drop'] if divergence else 0.0
    else:
        # Slimmed variant: drop divergence, renormalise remaining weights to 1.0.
        idx_change = 0.0
        divergence = False
        weights.pop('divergence', None)
        wsum = sum(weights.values()) or 1.0
        weights = {k: v / wsum for k, v in weights.items()}

    score = sum(weights[k] * comp[k] for k in weights)
    score = float(max(0.0, min(100.0, score)))

    if score >= _STRESS_WARNING:
        level = 'warning'
    elif score >= _STRESS_ELEVATED:
        level = 'elevated'
    else:
        level = 'calm'

    # ── Recovery / breadth-thrust score (bottom-turn counterpart) ────────────
    base_bull = float(piv['bull'].tail(_RECOVERY_BASE_WINDOW).min())
    washed01  = _clip01((_RECOVERY_LOW_REF - base_bull) / _RECOVERY_LOW_REF)
    rec_comp = {
        'bull_rise':  _clip01( bull_slope / _STRESS_SLOPE_FULL) * 100.0,
        'bear_drop':  _clip01(-bear_slope / _STRESS_SLOPE_FULL) * 100.0,
        'relvol':     comp['relvol'],            # same volume-confirmation term
        'washed_out': washed01 * 100.0,          # context (how depressed the base)
    }
    turn = sum(_RECOVERY_TURN_WEIGHTS[k] * rec_comp[k] for k in _RECOVERY_TURN_WEIGHTS)
    gate = 0.5 + 0.5 * washed01                  # 0.5 (high base) … 1.0 (washed out)
    rec_score = float(max(0.0, min(100.0, turn * gate)))

    if rec_score >= _STRESS_WARNING:
        rec_level = 'turning'
    elif rec_score >= _STRESS_ELEVATED:
        rec_level = 'building'
    else:
        rec_level = 'none'

    return {
        'score':               round(score, 1),
        'level':               level,
        'recovery_score':      round(rec_score, 1),
        'recovery_level':      rec_level,
        'recovery_components': {k: round(v, 1) for k, v in rec_comp.items()},
        'base_bull':           round(base_bull, 1),
        'bull':                round(bull_now, 1),
        'bear':                round(bear_now, 1),
        'side':                round(side_now, 1),
        'bull_slope':          round(bull_slope, 2),
        'bear_slope':          round(bear_slope, 2),
        'side_slope':          round(side_slope, 2),
        'relvol':              round(relvol_now, 2),
        'divergence':          bool(divergence),
        'index_change':        round(idx_change, 2),
        'components':          {k: round(v, 1) for k, v in comp.items()},
        'n':                   int(df['ticker'].nunique()),
        'as_of':               str(piv.index.max())[:10],
        'window':              _STRESS_WINDOW,
    }
