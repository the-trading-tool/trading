"""Regime Data Engine — vectorized Markov regime labels from local OHLCV data.

All heavy computation is parallelised with ThreadPoolExecutor and cached for
1 hour via @st.cache_data so repeat renders are instant.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
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
                   MIN(CASE WHEN UPPER(TRIM(i.name)) NOT IN ('INDEX','ALL','STOCKS','ETF')
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


def _compute_breadth_ticker(ticker: str, period: str) -> dict | None:
    """Latest Markov regime (Bull/Bear/Sideways) on day/week/month bars. Thread-safe.

    Returns {'ticker': ..., 'day': 0|1|2|nan, 'week': ..., 'month': ...}
    or None if no usable price history is available.
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
        for tf, rule in _BREADTH_TIMEFRAMES.items():
            series = close if rule is None else close.resample(rule).last().dropna()
            if len(series) < _LOOKBACK + 5:
                out[tf] = np.nan
                continue
            valid = _label_regimes(series)
            valid = valid[~np.isnan(valid)]
            out[tf] = float(valid[-1]) if len(valid) else np.nan
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

    Returns a wide DataFrame: ticker | day | week | month
    (regime codes 0=Sideways, 1=Bull, 2=Bear, NaN where history is too short).
    Cached for 1h — recompute via compute_index_breadth.clear() if needed sooner.
    """
    tickers = load_index_members(index_name)
    if not tickers:
        return pd.DataFrame(columns=['ticker', 'day', 'week', 'month'])

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
        return pd.DataFrame(columns=['ticker', 'day', 'week', 'month'])

    return (pd.DataFrame(results)[['ticker', 'day', 'week', 'month']]
            .sort_values('ticker').reset_index(drop=True))


def summarize_breadth(df: pd.DataFrame) -> dict:
    """Aggregate per-member regimes into breadth percentages + a composite market score.

    Returns:
        {
          'day':   {'bull': pct, 'bear': pct, 'side': pct, 'n': count},
          'week':  {...}, 'month': {...},
          'score': float in [-1, +1],   # weighted (%Bull - %Bear), day=1/week=2/month=3
          'state': 'bull' | 'bear' | 'neutral',   # language-neutral code — UI translates via t()
          'aligned': bool,              # do day/week/month agree on the majority regime?
        }
    Empty dict if df has no usable rows.
    """
    if df is None or df.empty:
        return {}

    _weights = {'day': 1.0, 'week': 2.0, 'month': 3.0}
    tf_stats = {}
    weighted_sum = 0.0
    weight_total = 0.0
    majorities = {}

    for tf in ('day', 'week', 'month'):
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
