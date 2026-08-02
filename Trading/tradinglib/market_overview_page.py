"""
market_overview_page.py — Global market overview with AI analysis.

Analyses 8 global market indicators via FetchData (the same pipeline as in
the charts), reads the active indicators from the user configuration
(overlays + oscillators from system_config) and sends the computed values
to Groq/Gemini for an AI market assessment.
"""
import logging
from datetime import datetime

import pandas as pd
import streamlit as st

from tradinglib.ai_client import AiClient, AiRateLimitError, AiProviderError
from tradinglib import system_config as sysconf
from tradinglib import i18n
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

# German data category (from _INDEX_NAMES) → locale key, for display translation.
_CAT_KEY = {
    'Angstbarometer': 'mv.cat.fear',   'Zins USA': 'mv.cat.rates_us',
    'USA': 'mv.cat.usa',               'Europa': 'mv.cat.europe',
    'Japan': 'mv.cat.japan',           'Asien': 'mv.cat.asia',
    'Australien': 'mv.cat.australia',  'Metalle': 'mv.cat.metals',
    'Energie': 'mv.cat.energy',        'Agrar': 'mv.cat.agri',
    'Währung': 'mv.cat.currency',      'Krypto': 'mv.cat.crypto',
    'Sonstiges': 'mv.cat.other',       'Index': 'mv.cat.index',
}


def _cat(cat: str) -> str:
    """Translate a data category label to the active language (fallback: as-is)."""
    key = _CAT_KEY.get(cat)
    return t(key) if key else cat

# ── Symbols ────────────────────────────────────────────────────────────────────
# (display_id, yf_ticker, long_name, category)

# Default selection (fallback when DB is unavailable or nothing has been saved)
_SYMBOLS = [
    ('^VIX',    '^VIX',    'CBOE Volatility Index',  'Angstbarometer'),
    ('^TNX',    '^TNX',    '10Y US Treasury Yield',  'Zins USA'),
    ('^HSI',    '^HSI',    'Hang Seng Index',         'Asien'),
    ('^N225',   '^N225',   'Nikkei 225',              'Japan'),
    ('^GDAXI',  '^GDAXI',  'DAX',                     'Europa'),
    ('^SPX',    '^GSPC',   'S&P 500',                 'USA'),
    ('GC=F',    'GC=F',    'Gold Futures',            'Rohstoff'),
    ('BTC-EUR', 'BTC-EUR', 'Bitcoin (EUR)',            'Krypto'),
]

_SESSION_KEY = 'market_overview_result'   # State 3: AI response available
_PREP_KEY    = 'market_overview_prep'      # State 2: data + prompt ready, waiting to send
_CFG_INSTRUMENTS_KEY = 'market_overview_instruments'  # saved instrument selection

# Display names for known indices (display_id → (long_name, category))
_INDEX_NAMES: dict[str, tuple[str, str]] = {
    '^GDAXI':   ('DAX 40',            'Europa'),
    '^MDAXI':   ('MDAX',              'Europa'),
    '^SDAXI':   ('SDAX',              'Europa'),
    '^TECDAX':  ('TecDAX',            'Europa'),
    '^STOXX50E':('Euro Stoxx 50',     'Europa'),
    '^FTSE':    ('FTSE 100',          'Europa'),
    '^SSMI':    ('SMI',               'Europa'),
    '^IBEX':    ('IBEX 35',           'Europa'),
    '^SPX':     ('S&P 500',           'USA'),
    '^DJI':     ('Dow Jones',         'USA'),
    '^NDX':     ('NASDAQ 100',        'USA'),
    '^IXIC':    ('NASDAQ Composite',  'USA'),
    '^NYA':     ('NYSE Composite',    'USA'),
    '^N225':    ('Nikkei 225',        'Japan'),
    '^HSI':     ('Hang Seng',         'Asien'),
    '^ASXJO':   ('ASX 200',           'Australien'),
    '^VIX':     ('CBOE Volatility',   'Angstbarometer'),
    '^TNX':     ('10Y Treasury Yield','Zins USA'),
    'GC=F':     ('Gold Futures',             'Metalle'),
    'SI=F':     ('Silber Futures',           'Metalle'),
    'HG=F':     ('Kupfer Futures',           'Metalle'),
    'PL=F':     ('Platin Futures',           'Metalle'),
    'PA=F':     ('Palladium Futures',        'Metalle'),
    'BZ=F':     ('Brent Crude Oil',          'Energie'),
    'CL=F':     ('WTI Crude Oil',            'Energie'),
    'NG=F':     ('Natural Gas',              'Energie'),
    'RB=F':     ('Gasoline (RBOB)',          'Energie'),
    'HO=F':     ('Heating Oil',              'Energie'),
    'ZW=F':     ('Weizen (Wheat)',           'Agrar'),
    'ZC=F':     ('Mais (Corn)',              'Agrar'),
    'ZS=F':     ('Sojabohnen',              'Agrar'),
    'KC=F':     ('Kaffee',                  'Agrar'),
    'CC=F':     ('Kakao',                   'Agrar'),
    'SB=F':     ('Zucker',                  'Agrar'),
    'CT=F':     ('Baumwolle',               'Agrar'),
    'DX-Y.NYB': ('US Dollar Index',          'Sonstiges'),
    'ZN=F':     ('US-Treasuries 10Y',        'Zins USA'),
    'LE=F':     ('Live Cattle',              'Sonstiges'),
    'BTC-EUR':  ('Bitcoin (EUR)',            'Krypto'),
    'ETH-EUR':  ('Ethereum (EUR)',           'Krypto'),
    # Währungspaare
    'EURUSD=X': ('EUR/USD',                  'Währung'),
    'GBPUSD=X': ('GBP/USD',                  'Währung'),
    'USDJPY=X': ('USD/JPY',                  'Währung'),
    'USDCHF=X': ('USD/CHF',                  'Währung'),
    'AUDUSD=X': ('AUD/USD',                  'Währung'),
    'USDCAD=X': ('USD/CAD',                  'Währung'),
    'EURGBP=X': ('EUR/GBP',                  'Währung'),
    'EURJPY=X': ('EUR/JPY',                  'Währung'),
    'USDCNY=X': ('USD/CNY',                  'Währung'),
}

# yfinance ticker differs from the display ticker (^SPX index price = ^GSPC).
# The US Dollar Index is the ICE spot index DX-Y.NYB — the DX=F future is delisted
# on Yahoo. DX-Y.NYB is used directly as the display id, so no mapping is needed.
_YF_TICKER_MAP: dict[str, str] = {'^SPX': '^GSPC'}

# Symbols that are always in the pool (regardless of yf_tickers.db)
_EXTRA_SYMBOLS = [
    # Fear gauge / interest rates
    '^VIX', '^TNX',
    # Metals
    'GC=F', 'SI=F', 'HG=F', 'PL=F', 'PA=F',
    # Energy
    'BZ=F', 'CL=F', 'NG=F', 'RB=F', 'HO=F',
    # Agriculture
    'ZW=F', 'ZC=F', 'ZS=F', 'KC=F', 'CC=F', 'SB=F', 'CT=F',
    # Crypto
    'BTC-EUR', 'ETH-EUR',
    # Rates / bonds
    'ZN=F',
    # Other
    'DX-Y.NYB', 'LE=F',
    # Currencies
    'EURUSD=X', 'GBPUSD=X', 'USDJPY=X', 'USDCHF=X',
    'AUDUSD=X', 'USDCAD=X', 'EURGBP=X', 'EURJPY=X', 'USDCNY=X',
]

# Default activation on first call
_DEFAULT_INSTRUMENTS = ['^VIX', '^TNX', '^HSI', '^N225', '^GDAXI', '^SPX', 'GC=F', 'BTC-EUR']

# Instruments backing the Correlation Index (correlation_index.db). They are shown
# permanently checked + disabled in the instrument picker and are always part of the
# analysis, so the AI can relate their stored cross-asset correlations to the market
# data. display_id (market-overview) — the yf-ticker mapping (^SPX→^GSPC) stays as
# configured in _YF_TICKER_MAP; the US Dollar Index uses DX-Y.NYB directly.
_CORR_LOCKED_IDS = ['^SPX', 'BTC-EUR', 'GC=F', 'DX-Y.NYB', 'HG=F', 'ZN=F']

# German pair labels for the correlation prompt block (keyed by correlation_index pair id).
_CORR_PAIR_LABELS = {
    'spx_btc':    'S&P 500 ↔ Bitcoin',
    'btc_gold':   'Bitcoin ↔ Gold',
    'spx_usd':    'S&P 500 ↔ US-Dollar-Index',
    'copper_spx': 'Kupfer ↔ S&P 500',
    'zn_spx':     'US-Treasuries (10Y) ↔ S&P 500',
}
_CORR_PAIR_CONTEXT = {
    'spx_btc':    'BTC als High-Beta-Risk-Asset (positiv = wenig Diversifikation).',
    'btc_gold':   '"digitales Gold"-These (positiv = bestätigt, near-null = entkoppelt).',
    'spx_usd':    'normal negativ; Vorzeichenwechsel ins Positive = Stress/Regimewechsel.',
    'copper_spx': 'Konjunktur-Bestätigung (positiv = risk-on; fallend = Wachstumszweifel).',
    'zn_spx':     'negativ = gesunde 60/40-Diversifikation; positiv = Zins-Regime, geschwächt.',
}


def _load_index_symbols() -> list[tuple[str, str, str, str]]:
    """Read the indices table from yf_tickers.db and return
    [(display_id, yf_ticker, name, category), ...].

    Respects the TradingDB environment variable for the DB path.
    Returns an empty list on errors.
    """
    import os
    from tradinglib.tools import open_db

    db_dir  = os.environ.get('TradingDB', 'database')
    db_file = os.path.join(db_dir, 'yf_tickers.db')
    result: list[tuple[str, str, str, str]] = []
    try:
        with open_db(db_file, readonly=True) as conn:
            rows = conn.execute(
                "SELECT name FROM indices WHERE name LIKE '^%' ORDER BY name"
            ).fetchall()
    except Exception as exc:
        logger.warning("_load_index_symbols: %s", exc)
        return result

    for (ticker,) in rows:
        if not ticker:
            continue
        name, cat  = _INDEX_NAMES.get(ticker, (ticker, 'Index'))
        yf_ticker  = _YF_TICKER_MAP.get(ticker, ticker)
        result.append((ticker, yf_ticker, name, cat))

    # Append extra symbols (GC=F, BTC-EUR, VIX, TNX) if not already present
    existing = {r[0] for r in result}
    for sym in _EXTRA_SYMBOLS:
        if sym not in existing:
            name, cat = _INDEX_NAMES.get(sym, (sym, ''))
            result.append((sym, _YF_TICKER_MAP.get(sym, sym), name, cat))

    return result

# Columns that are not considered indicators and are filtered out of the prompt
_SKIP_COLS = frozenset({
    'Open', 'High', 'Low', 'Close', 'Volume', 'Adj Close',
    'buy_close', 'sell_close', 'crosszero', 'position',
    'daily_returns', 'log_return', 'index', 'level_0',
})

# Interpretation hints for the AI prompt
_INDICATOR_HINTS = {
    'markov_regime':      '(0=Seitwärts, 1=Bullenmarkt, 2=Bärenmarkt)',
    'markov_bull_prob':   '(Wahrscheinlichkeit Bullenmarkt-Regime, 0–1)',
    'markov_bear_prob':   '(Wahrscheinlichkeit Bärenmarkt-Regime, 0–1)',
    'markov_sideways_prob': '(Wahrscheinlichkeit Seitwärts-Regime, 0–1)',
    'ha_close':           '(Heikin-Ashi Schlusskurs; > ha_open = bullisch)',
    'ha_open':            '(Heikin-Ashi Eröffnungskurs)',
    'ha_ema_high':        '(HA EMA oberes Band → dynamischer Widerstand)',
    'ha_ema_low':         '(HA EMA unteres Band → dynamische Unterstützung)',
    'ewo':                '(Elliott Wave Oscillator: positiv = bullisch, EMA5 > EMA35)',
    'ewo_ema':            '(EWO Glättungslinie)',
    'ewo_angle':          '(EWO Steigungswinkel: positiv = steigendes Momentum)',
    'ewo_buy':            '(EWO Kauf-Signal: 1=aktiv)',
    'ewo_sell':           '(EWO Verkauf-Signal: 1=aktiv)',
    'macd':               '(MACD-Linie)',
    'macd_signal':        '(Signal-Linie)',
    'macd_diff':          '(Histogramm: positiv = bullisch)',
    'sup_support':        '(berechnete Unterstützungszone)',
    'sup_resistance':     '(berechnete Widerstandszone)',
    'atl_high':           '(ATL obere Kanalgrenze)',
    'atl_low':            '(ATL untere Kanalgrenze)',
    'pvt_p':              '(Pivot-Punkt)',
    'pvt_r1':             '(Widerstand 1)',
    'pvt_r2':             '(Widerstand 2)',
    'pvt_s1':             '(Unterstützung 1)',
    'pvt_s2':             '(Unterstützung 2)',
}


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def _get_active_indicators(sys_conf) -> tuple[str, str, list]:
    """Read interval, period, and indicator list from the user's system configuration."""
    interval, period, overlays, oscillators = sys_conf.get_selectors()
    # 'bar' = pure Plotly rendering, produces no data columns → skip it
    all_inds = list(overlays) + list(oscillators)
    indicators = [i for i in all_inds if i != 'bar']
    return interval, period, indicators


def _extract_indicator_values(df: pd.DataFrame) -> dict[str, float]:
    """Return all non-NaN indicator column values from the last row as a name→float dict."""
    last = df.iloc[-1]
    result: dict[str, float] = {}
    for col, val in last.items():
        if str(col) in _SKIP_COLS:
            continue
        if pd.notna(val):
            try:
                result[str(col)] = round(float(val), 4)
            except (TypeError, ValueError):
                pass
    return result


# ── Trend snippets ─────────────────────────────────────────────────────────────

# Candidates for numeric trend snippets (in priority order)
_SNIPPET_NUM_COLS = ['markov_regime', 'macd_diff', 'ewo', 'ewo_angle', 'rsi', 'cci', 'adx']


def _dir_arrow(v_mid: float, v_now: float) -> str:
    """Return a direction arrow (↗/↘/→); stable when the change is < 1% relative to |v_mid|."""
    if v_mid == 0:
        return '→'
    if abs(v_now - v_mid) / abs(v_mid) < 0.01:
        return '→'
    return '↗' if v_now > v_mid else '↘'


def _compute_trend_snippets(df: pd.DataFrame, interval: str) -> dict[str, str]:
    """Compute trend-change snippets for key indicators across three time points.

    Compares ~10 periods back, ~5 periods back, and now; flags sign flips and
    regime changes with a ⚠ symbol.
    """
    n = len(df)
    if n < 12:
        return {}

    # Period labels for the prompt
    is_daily = interval in ('1d', '3d', '1wk')
    lbl_10 = 'vor 10 HT' if is_daily else f'vor 10×{interval}'
    lbl_5  = 'vor 5 HT'  if is_daily else f'vor 5×{interval}'

    p10 = df.iloc[max(0, n - 11)]
    p5  = df.iloc[max(0, n - 6)]
    p0  = df.iloc[-1]

    snippets: dict[str, str] = {}

    # 1. Numeric key indicators
    for col in _SNIPPET_NUM_COLS:
        if col not in df.columns:
            continue
        vals = []
        for pt in (p10, p5, p0):
            v = pt.get(col)
            vals.append(float(v) if pd.notna(v) else None)
        if any(v is None for v in vals):
            continue
        v10, v5, v0 = vals

        if col == 'markov_regime':
            lbl = {0.0: 'Sw', 1.0: 'Bull', 2.0: 'Bear'}
            s = ' → '.join(lbl.get(v, str(int(v))) for v in vals)
            flag = '  ⚠ Regime-Wechsel!' if v10 != v0 else ''
            snippets[col] = f"[{lbl_10}: {lbl.get(v10,'?')} | {lbl_5}: {lbl.get(v5,'?')} | heute: {lbl.get(v0,'?')}]{flag}"
        else:
            fmt = lambda v: f"{v:+.2f}" if abs(v) < 100 else f"{v:+.0f}"
            arrow = _dir_arrow(v5, v0)
            sign_flip = col in ('macd_diff', 'ewo') and (v5 >= 0) != (v0 >= 0)
            flag = '  ⚠ Vorzeichenwechsel!' if sign_flip else ''
            snippets[col] = f"[{lbl_10}: {fmt(v10)} | {lbl_5}: {fmt(v5)} | heute: {fmt(v0)}]  {arrow}{flag}"

    # 2. Heikin-Ashi direction (derived)
    if 'ha_close' in df.columns and 'ha_open' in df.columns:
        ha_labels = []
        for pt in (p10, p5, p0):
            hc, ho = pt.get('ha_close'), pt.get('ha_open')
            if pd.notna(hc) and pd.notna(ho):
                ha_labels.append('Bull' if float(hc) > float(ho) else 'Bear')
            else:
                ha_labels.append('?')
        flag = '  ⚠ Richtungsflip!' if ha_labels[0] != ha_labels[2] and '?' not in ha_labels else ''
        snippets['ha_direction'] = (
            f"[{lbl_10}: {ha_labels[0]} | {lbl_5}: {ha_labels[1]} | heute: {ha_labels[2]}]{flag}"
        )

    # 3. Pivot compression over time (only relevant if pvt values were dynamic — pvt is static,
    #    so skip here; compression is already covered in the ind_values block)

    return snippets


def _compute_extended_snippets(df: pd.DataFrame, interval: str) -> dict[str, str]:
    """Long-term trend snippets: 200 periods → 50 periods → today.

    Requires sufficient data points (≥ 55 for 50P, ≥ 205 for 200P).
    When data is insufficient, only the available time points are shown.
    """
    n = len(df)
    if n < 12:
        return {}

    is_daily  = interval in ('1d', '3d', '1wk')
    lbl_200   = 'vor 200 HT' if is_daily else f'vor 200×{interval}'
    lbl_50    = 'vor 50 HT'  if is_daily else f'vor 50×{interval}'

    # Available time points
    has_200 = n >= 205
    has_50  = n >= 55

    if not has_50:
        return {}   # Insufficient data

    pts: list[tuple[str, 'pd.Series']] = []
    if has_200:
        pts.append((lbl_200, df.iloc[max(0, n - 201)]))
    pts.append((lbl_50, df.iloc[max(0, n - 51)]))
    pts.append(('heute',   df.iloc[-1]))

    snippets: dict[str, str] = {}

    for col in _SNIPPET_NUM_COLS:
        if col not in df.columns:
            continue
        vals: list[tuple[str, float | None]] = []
        for lbl, row in pts:
            v = row.get(col)
            vals.append((lbl, float(v) if pd.notna(v) else None))
        if any(v is None for _, v in vals):
            continue

        if col == 'markov_regime':
            lbl_map = {0.0: 'Sw', 1.0: 'Bull', 2.0: 'Bear'}
            parts = ' | '.join(f"{l}: {lbl_map.get(v, str(int(v)))}" for l, v in vals)
            v_first, v_last = vals[0][1], vals[-1][1]
            flag = '  ⚠ Regime-Wechsel!' if v_first != v_last else ''
            snippets[col] = f"[{parts}]{flag}"
        else:
            fmt = lambda v: f"{v:+.2f}" if abs(v) < 100 else f"{v:+.0f}"
            v_mid, v_now = vals[-2][1], vals[-1][1]
            arrow = _dir_arrow(v_mid, v_now)
            sign_flip = col in ('macd_diff', 'ewo') and (v_mid >= 0) != (v_now >= 0)
            flag = '  ⚠ Langfristig Vorzeichenwechsel!' if sign_flip else ''
            parts = ' | '.join(f"{l}: {fmt(v)}" for l, v in vals)
            snippets[col] = f"[{parts}]  {arrow}{flag}"

    # Heikin-Ashi direction
    if 'ha_close' in df.columns and 'ha_open' in df.columns:
        ha_vals: list[tuple[str, str]] = []
        for lbl, row in pts:
            hc, ho = row.get('ha_close'), row.get('ha_open')
            if pd.notna(hc) and pd.notna(ho):
                ha_vals.append((lbl, 'Bull' if float(hc) > float(ho) else 'Bear'))
        if ha_vals:
            v_first_ha = ha_vals[0][1]
            v_last_ha  = ha_vals[-1][1]
            flag = '  ⚠ Langfristig Richtungsflip!' if v_first_ha != v_last_ha else ''
            parts = ' | '.join(f"{l}: {v}" for l, v in ha_vals)
            snippets['ha_direction'] = f"[{parts}]{flag}"

    return snippets


# ── 4-Wochen-Snapshot ─────────────────────────────────────────────────────────

def _bars_for_4weeks(interval: str) -> int:
    """Return the number of bars that correspond to approximately 4 weeks (20 trading days)."""
    return {
        '1d': 20,  '3d': 7,   '1wk': 4,
        '60m': 160, '30m': 320, '15m': 640, '5m': 1920,
    }.get(interval, 20)


def _compute_4weeks_snapshot(df: pd.DataFrame, interval: str, close_now: float) -> dict:
    """Build an indicator snapshot from ~4 weeks ago using the already-loaded DataFrame.

    Returns an empty dict when there is insufficient history.
    """
    bars_back = _bars_for_4weeks(interval)
    # At least bars_back + 5 rows required; otherwise no meaningful comparison
    if len(df) < bars_back + 5:
        return {}

    df_then  = df.iloc[:-(bars_back)]          # alles bis vor 4 Wochen
    last_row = df_then.iloc[-1]

    close_then = None
    try:
        close_then = round(float(last_row['Close']), 4)
    except (KeyError, TypeError, ValueError):
        return {}

    ind_then = _extract_indicator_values(df_then)
    pvm_then = _pivot_compression(ind_then, close_then)

    return {
        'close_then':    close_then,
        'close_chg_pct': round((close_now - close_then) / close_then * 100, 2),
        'datum_then':    str(df_then.index[-1])[:10],
        'indicator_values': ind_then,
        'pivot_metrics':    pvm_then,
    }


# ── Daten + Indikatoren ───────────────────────────────────────────────────────

def _result_from_df(df: pd.DataFrame, display_id: str, interval: str) -> dict:
    """Build a market-overview result dict from an already-computed OHLC+indicator df.

    Shared by _compute_for_symbol (market overview — fetches the df itself) and the
    single-asset analysis tab in the Asset Viewer (reuses the chart's df, no re-fetch),
    so both judge an asset by exactly the same indicator extraction / snippet logic.
    """
    close = df['Close'].dropna()
    if close.empty:
        return {'ticker': display_id, 'error': 'Keine Kursdaten'}

    last_date  = str(df.index[-1])[:10]
    last_close = round(float(close.iloc[-1]), 4)

    week_ret  = round((float(close.iloc[-1]) / float(close.iloc[-6])  - 1) * 100, 2) if len(close) >= 6  else None
    month_ret = round((float(close.iloc[-1]) / float(close.iloc[-22]) - 1) * 100, 2) if len(close) >= 22 else None

    ind = _extract_indicator_values(df)
    _ensure_core_indicators(df, ind)   # RSI + ATR immer verfügbar

    return {
        'ticker':            display_id,
        'datum':             last_date,
        'close':             last_close,
        'week_ret':          week_ret,
        'month_ret':         month_ret,
        'indicator_values':  ind,
        'trend_snippets':    _compute_trend_snippets(df, interval),
        'extended_snippets': _compute_extended_snippets(df, interval),
        'snapshot_4w':       _compute_4weeks_snapshot(df, interval, last_close),
        'interval':          interval,
    }


def _compute_for_symbol(
    display_id: str, yf_ticker: str, interval: str,
    period: str, indicators: list, sys_conf,
) -> dict:
    """Load OHLCV and compute configured indicators for one symbol via the FetchData pipeline.

    Falls back automatically to yfinance for symbols without a local DB (e.g. ^VIX, ^TNX).
    """
    from tradinglib.fetch_data import FetchData

    try:
        fd = FetchData(
            database_path='database',
            indicators=indicators,
            buy_query=sys_conf.get_value('buy_query', ''),
            sell_query=sys_conf.get_value('sell_query', ''),
            sys_conf=sys_conf,
        )
        df, _ = fd.fetch_data(yf_ticker, period=period, interval=interval)

        if df is None or df.empty:
            return {'ticker': display_id, 'error': 'Keine Daten verfügbar'}

        return _result_from_df(df, display_id, interval)

    except Exception as exc:
        logger.error(
            "market_overview: FetchData error for %s (%s): %s",
            display_id, yf_ticker, exc, exc_info=True,
        )
        return {'ticker': display_id, 'error': str(exc)}


def _ensure_core_indicators(df: pd.DataFrame, ind: dict) -> None:
    """Ergänzt RSI(14), MACD(12/26/9) und ATR(14) in ind, falls FetchData sie
    nicht berechnet hat.

    Pflicht-Indikatoren für das Regime-Modell (Momentum + Volatilität). Die
    Prompt-Überschrift "[2-MOMENTUM — RSI + MACD]" ist hartkodiert; MACD wird von
    FetchData aber nur erzeugt, wenn 'macd' in der Chart-Indikator-Config steht.
    Damit das Format zur Datenlage passt, wird MACD hier — wie RSI/ATR — notfalls
    direkt aus dem Schlusskurs berechnet (Standard 12/26/9).
    Mutiert ind in-place — kein Rückgabewert.
    """
    # RSI(14)
    if 'rsi' not in ind:
        try:
            close = df['Close'].dropna()
            delta = close.diff().dropna()
            gain  = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
            loss  = (-delta).clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
            rs    = gain / loss
            v     = float((100 - 100 / (1 + rs)).iloc[-1])
            if not pd.isna(v):
                ind['rsi'] = round(v, 1)
        except Exception:
            pass

    # MACD(12/26/9) — Standard-Parameter; nur falls FetchData keine macd-Spalte lieferte
    if 'macd' not in ind:
        try:
            close   = df['Close'].dropna()
            ema12   = close.ewm(span=12, adjust=False, min_periods=12).mean()
            ema26   = close.ewm(span=26, adjust=False, min_periods=26).mean()
            macd_l  = ema12 - ema26
            signal  = macd_l.ewm(span=9, adjust=False, min_periods=9).mean()
            hist    = macd_l - signal
            m, s, h = float(macd_l.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])
            if not (pd.isna(m) or pd.isna(s) or pd.isna(h)):
                ind['macd']        = round(m, 4)
                ind['macd_signal'] = round(s, 4)
                ind['macd_diff']   = round(h, 4)
        except Exception:
            pass

    # ATR(14) — prüfe mehrere mögliche Spaltennamen aus verschiedenen Indikatoren
    _atr_cols = ('atr14', 'atr', 'bos_atr', 'sqz_atr')
    if not any(c in ind for c in _atr_cols):
        try:
            high  = df['High']
            low   = df['Low']
            close = df['Close']
            hl    = high - low
            hc    = (high - close.shift(1)).abs()
            lc    = (low  - close.shift(1)).abs()
            tr    = pd.concat([hl, hc, lc], axis=1).max(axis=1)
            v     = float(tr.rolling(14).mean().iloc[-1])
            if not pd.isna(v):
                ind['atr14'] = round(v, 4)
        except Exception:
            pass


# ── Nachrichten-Sentiment ─────────────────────────────────────────────────────

# Für diese drei Indizes werden Yahoo-Finance-RSS-Schlagzeilen abgerufen
_SENTIMENT_TICKERS = ['^GDAXI', '^N225', '^SPX']


def _fetch_sentiment_headlines(max_per_ticker: int = 3) -> dict[str, list[dict]]:
    """Fetch current headlines + title-level VADER sentiment from Yahoo Finance RSS.

    Returns {ticker: [{'title', 'link', 'published', 'compound', 'sentiment'}]}.
    Returns an empty dict on network errors or when VADER is unavailable.
    Uses headline-only sentiment (no article-text fetch) for speed.
    """
    try:
        from tradinglib.sentiment import YahooNewsSentiment
    except Exception as exc:
        logger.warning("sentiment.py konnte nicht importiert werden: %s", exc)
        return {}

    def _parse_dt(s: str):
        try:
            return datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            return datetime.min

    result: dict[str, list[dict]] = {}
    for ticker in _SENTIMENT_TICKERS:
        try:
            yns      = YahooNewsSentiment(ticker=ticker)
            articles = yns.fetch_news()
            if not articles:
                result[ticker] = []
                continue

            articles.sort(key=lambda a: _parse_dt(a.get('published', '')), reverse=True)

            headlines = []
            for a in articles[:max_per_ticker]:
                title    = a.get('title', '')
                compound = 0.0
                label    = 'neutral'
                if yns.sia:
                    try:
                        sc       = yns.sia.polarity_scores(title)
                        compound = round(sc['compound'], 2)
                        label    = ('positiv' if compound >= 0.05 else
                                    'negativ' if compound <= -0.05 else 'neutral')
                    except Exception:
                        pass
                headlines.append({
                    'title':     title,
                    'link':      a.get('link', ''),
                    'published': a.get('published', '')[:25],
                    'compound':  compound,
                    'sentiment': label,
                })
            result[ticker] = headlines

        except Exception as exc:
            logger.warning("sentiment fetch failed for %s: %s", ticker, exc)
            result[ticker] = []

    return result


# ── Prompt-Builder ─────────────────────────────────────────────────────────────

def _pivot_compression(ind: dict, close: float) -> dict:
    """Derive pivot compression metrics from raw pvt_* indicator values.

    Returns keys: compression_pct ((R1-S1)/P×100), close_vs_p_pct ((Close-P)/P×100),
    nearest_label (closest level name), compressed (True when compression_pct < 2%).
    """
    p   = ind.get('pvt_p')
    r1  = ind.get('pvt_r1')
    s1  = ind.get('pvt_s1')
    if p is None or r1 is None or s1 is None or p == 0:
        return {}

    compression_pct = round((r1 - s1) / p * 100, 2)
    close_vs_p_pct  = round((close - p) / p * 100, 2)

    # nächstes Level zum Kurs bestimmen
    levels = {
        'P':  p,
        'R1': r1,
        'R2': ind.get('pvt_r2', r1),
        'S1': s1,
        'S2': ind.get('pvt_s2', s1),
    }
    nearest_label = min(levels, key=lambda k: abs(levels[k] - close))

    return {
        'compression_pct': compression_pct,
        'close_vs_p_pct':  close_vs_p_pct,
        'nearest_label':   nearest_label,
        'compressed':      compression_pct < 2.0,
    }


def _correlation_prompt_block() -> str:
    """Format the stored cross-asset correlations for the AI prompt (German scaffold).

    Reads correlation_index.db via the correlation_index module. Returns '' when no
    correlation data is available yet (scheduler job not run).
    """
    try:
        from tradinglib import correlation_index as ci
    except Exception:
        return ''
    try:
        series, meta, updated_at = ci.load_from_db()
    except Exception as exc:
        logger.warning("market_overview: correlation load failed: %s", exc)
        return ''
    if series is None or series.empty:
        return ''

    meta_by = ({r['pair_id']: r for _, r in meta.iterrows()}
               if meta is not None and not meta.empty else {})

    lines = [
        "════════════════════════════════════════",
        "CROSS-ASSET-KORRELATIONEN (rollierende Tages-Log-Renditen)",
        "Skala: -1 Gegenlauf · 0 entkoppelt · +1 Gleichlauf"
        + (f"  |  Stand: {updated_at}" if updated_at else ""),
        "════════════════════════════════════════",
        "",
    ]
    for pair in ci.PAIRS:
        pid = pair['id']
        sub = series[series['pair_id'] == pid]
        c30_ser = sub['corr_30'].dropna() if 'corr_30' in sub else pd.Series(dtype=float)
        c90_ser = sub['corr_90'].dropna() if 'corr_90' in sub else pd.Series(dtype=float)
        if c30_ser.empty and c90_ser.empty:
            continue
        c30 = float(c30_ser.iloc[-1]) if not c30_ser.empty else None
        c90 = float(c90_ser.iloc[-1]) if not c90_ser.empty else None

        # Trend: current vs. ~21 rows back on the longer window (fallback 30d)
        t_ser = c90_ser if not c90_ser.empty else c30_ser
        trend = '→ stabil'
        if len(t_ser) > 21:
            d = float(t_ser.iloc[-1]) - float(t_ser.iloc[-22])
            trend = ('↗ steigend' if d > 0.12 else '↘ fallend' if d < -0.12 else '→ stabil')

        mrow = meta_by.get(pid)
        full = (float(mrow['full_corr'])
                if mrow is not None and pd.notna(mrow.get('full_corr')) else None)

        c30s = f"{c30:+.2f}" if c30 is not None else "n/a"
        c90s = f"{c90:+.2f}" if c90 is not None else "n/a"
        fulls = f"{full:+.2f}" if full is not None else "n/a"
        lines.append(f"─── {_CORR_PAIR_LABELS.get(pid, pid)} ───")
        lines.append(f"  30T: {c30s} | 90T: {c90s} | Ø gesamt: {fulls} | Trend(~1M): {trend}")
        lines.append(f"  Kontext: {_CORR_PAIR_CONTEXT.get(pid, '')}")
    lines.append("")
    return "\n".join(lines)


def _build_market_prompt(
    results: list[dict], symbol_meta: list[tuple],
    interval: str, period: str, indicators: list,
    headlines: dict | None = None,
    sections: dict | None = None,
    freetext: str | None = None,
    asset_info: str | None = None,
    market_status: str | None = None,
    signals: str | None = None,
    seasonality: str | None = None,
    sector_rotation: str | None = None,
    news: str | None = None,
    single_asset: bool = False,
    primary_ticker: str | None = None,
    primary_name: str = '',
    primary_market: str = '',
    membership_note: str | None = None,
    compact: bool = False,
) -> str:
    """Multi-Asset Regime & Risk Model — hierarchisch strukturierter Analyse-Prompt.

    asset_info: optionaler vorformatierter Stammdaten-/Fundamental-Block (z.B.
    Geschäftsbeschreibung + Kennzahlen aus asset_info), der vor der Analyse-Aufgabe
    eingefügt wird. Wird von der Einzel-Asset-Analyse im Asset Viewer genutzt.
    market_status: optionaler Index-Breadth-/Frühwarn-Statusblock (derselbe Hinweis
    wie das Banner unter dem Trend-Chart), der als Marktkontext übergeben wird.
    signals: optionaler Block mit Buy/Sell-Signalen + zugrunde liegender Formel.
    seasonality: optionaler Saisonalitäts-Zusammenfassungsblock.
    single_asset: True → Intro und Analyse-Aufgabe sind auf EIN Ziel-Asset zugeschnitten
    (statt Multi-Asset-Makro-Bild). primary_ticker = das Analyse-Ziel; membership_note =
    optionaler Hinweis, in welchem Leitindex das Asset enthalten ist.
    """
    meta    = {s[0]: (s[2], s[3]) for s in symbol_meta}
    today   = datetime.now().strftime('%d.%m.%Y')
    _regime = {0.0: 'Seitwärts', 1.0: 'Bullenmarkt', 2.0: 'Bärenmarkt'}
    inc     = sections or {}   # Abkürzung für bedingte Includes
    if compact:                # Kompakt: lange Trend-Snippets weglassen (Token sparen)
        inc = {**inc, 'extended_snippets': False}

    # Anzahl der ausgewählten Märkte explizit an die KI übergeben, damit sie ALLE
    # auswertet und nicht von einer angenommenen Zahl ausgeht / eigenständig begrenzt.
    n_total = len(results)
    n_data  = sum(1 for r in results if not r.get('error'))
    # Mehrheits-Schwelle (echte Mehrheit der Assets MIT Daten) — ersetzt das frühere
    # hartkodierte "5 von 8", damit die Schwelle mit der Auswahl mitskaliert.
    n_majority = n_data // 2 + 1 if n_data else 0
    count_line = f"Ausgewählte Märkte: {n_total}"
    if n_data != n_total:
        count_line += f" (davon {n_data} mit verfügbaren Daten, {n_total - n_data} mit FEHLER)"

    # ^TNX (10J-US-Rendite) ist nur dann referenzierbar/semantisch einführbar, wenn es
    # wirklich als Asset in den Daten steht (Market Overview: meist ausgewählt; Einzel-
    # Asset: als Zins-Kontext mitgeliefert). Sonst würde der [4-MAKRO]-Verweis unten auf
    # einen nicht existierenden Block zeigen und Zins-Halluzinationen einladen.
    has_tnx = any(r.get('ticker') == '^TNX' for r in results)

    if single_asset:
        # Einzel-Asset-Framing: EIN Ziel-Asset, der Rest (z.B. Leitindex) ist Kontext.
        primary = primary_ticker or (results[0]['ticker'] if results else '')
        lines = [
            t('mv.prompt_lang_directive'),
            "",
            "Du bist ein quantitativer Aktien-/Asset-Analyst.",
            "Deine Aufgabe: Analysiere das FOLGENDE EINZELNE ASSET umfassend und handlungsorientiert —",
            "technisch (Trend/Momentum/Volatilität/Regime), fundamental (Kennzahlen) und im Kontext",
            "seines Marktumfelds (Leitindex + Marktbreite). INTERPRETATION statt Wiederholung; Rohzahlen",
            "nur, wenn sie eine Aussage BELEGEN.",
            f"Auswertungsdatum: {today}  |  Intervall: {interval}  |  Zeitraum: {period}",
            f"Analysiertes Asset (ZIEL): {primary}"
            + (f" — {primary_name}" if primary_name else "")
            + (f"  |  Markt: {primary_market}" if primary_market else ""),
        ]
        if membership_note:
            lines.append(membership_note)
        lines += [
            "Regeln:",
            "  • Analyse-ZIEL ist AUSSCHLIESSLICH das oben genannte Asset. Ein evtl. mitgelieferter Index/"
            "weitere Werte sind reiner MARKTUMFELD-Kontext, kein eigenes Analyseziel.",
            "  • Nutze NUR die gelieferten Daten. Erfinde keine externen Fakten, News oder Kursziele.",
            "  • Echte Widersprüche (z.B. günstige Bewertung vs. technischer Abwärtstrend) als KONFLIKT "
            "benennen und EINORDNEN — welches Signal wiegt schwerer und warum.",
            "  • EXTREME zuerst: RSI ≥ 80 (überkauft) / ≤ 20 (überverkauft), Vorzeichenwechsel, "
            "Kompression/Breakout — dort liegt die eigentliche Information.",
            "  • MA-POSITION KONSISTENT halten: Für Szenarien NUR die im [1-TREND]-Block unter "
            "'HAUPTREGIME (VERBINDLICH)' bereits ausformulierten Bestätigungs-/Invalidierungs-Trigger "
            "verwenden — erfinde KEINE eigene >/<-Richtung. Ein 'fortgesetzter Abwärtstrend' bedeutet "
            "Kurs BLEIBT UNTER SMA200 (schreibe nie '(>SMA200)' dafür); eine Rückeroberung ÜBER SMA200 "
            "ist IMMER bullisch (nie bärisch). Liegt der Kurs bereits UNTER einer MA, ist ein "
            "'Durchbruch darunter' KEIN gültiges Signal — der bärische Trigger ist der VERLUST einer "
            "tieferliegenden Unterstützung (SMA50/20, sup_support, Pivot S1). Über einer MA "
            "spiegelbildlich. Niemals einen Szenario-Tag schreiben, der der Ist-Lage widerspricht.",
            "  • Gib KEINE pauschale Anlageberatung — datenbasierte Einordnung.",
        ]
        if has_tnx:
            lines.append(
                "  • ^TNX (als KONTEXT mitgeliefert) = Rendite 10-jähriger US-Staatsanleihen (in %). "
                "Steigende Rendite = Zinsdruck/Straffung → tendenziell RISK-OFF (Gegenwind für "
                "Aktien/Gold/Krypto); fallende Rendite = Lockerung → risk-on. Als ZINS-UMFELD für das "
                "Ziel-Asset einordnen (nicht als Kurs/Preis lesen, nicht als Analyseziel)."
            )
        lines += [
            "",
            "════════════════════════════════════════",
            "MARKTDATEN",
            "════════════════════════════════════════",
            "",
        ]
    else:
        lines = [
            # Output language follows the user's app language; the scaffold below stays
            # German (internal engineering text), only the AI's response language changes.
            t('mv.prompt_lang_directive'),
            "",
            "Du bist ein quantitativer Multi-Asset-Stratege.",
            "Deine Aufgabe ist INTERPRETATION, nicht Wiederholung: Verbinde die Assets zu einem",
            "kohärenten Makro-Bild und leite handlungsrelevante Implikationen ab. Rohzahlen nur,",
            "wenn sie eine Aussage BELEGEN — kein Vorlesen der Daten.",
            f"Auswertungsdatum: {today}  |  Intervall: {interval}  |  Zeitraum: {period}",
            count_line,
            "Regeln:",
            f"  • Werte ALLE {n_total} Märkte aus (keine eigene Vorauswahl, Anzahl NICHT begrenzen).",
            "  • Nutze NUR die gelieferten Daten (Marktdaten + Korrelationen). Erfinde keine externen "
            "Fakten, News oder Kursziele.",
            "  • Echte Widersprüche als KONFLIKT benennen, aber EINORDNEN (welches Signal wiegt schwerer "
            "und warum) — nicht nur auflisten.",
            "  • SEMANTIK beachten: ^TNX = 10J-RENDITE (steigende Rendite = fallender Anleihepreis = "
            "Zinsdruck/Straffung → tendenziell RISK-OFF für Aktien/Gold, NICHT 'risk-on'). ZN=F = "
            "Anleihe-PREIS (invers zu ^TNX). Gold/Silber/Kupfer als Metall-Komplex zusammen lesen; "
            "^TNX und ZN=F als EIN Zins-Thema behandeln (nicht doppelt zählen).",
            "  • EXTREME zuerst: RSI ≥ 80 (überkauft) / ≤ 20 (überverkauft), Vorzeichenwechsel, "
            "Korrelationen die stark vom Ø-Wert abweichen — dort liegt die eigentliche Information.",
            "  • MA-POSITION KONSISTENT halten: Für Szenarien je Asset NUR die im [1-TREND]-Block "
            "unter 'HAUPTREGIME (VERBINDLICH)' ausformulierten Trigger verwenden — KEINE eigene "
            ">/<-Richtung erfinden. 'Fortgesetzter Abwärtstrend' = Kurs BLEIBT UNTER SMA200 (nie "
            "'(>SMA200)'); Rückeroberung ÜBER SMA200 ist IMMER bullisch. Liegt der Kurs bereits UNTER "
            "einer MA, ist ein 'Durchbruch darunter' KEIN gültiges Signal; der bärische Trigger ist der "
            "VERLUST einer tieferliegenden Unterstützung. Über einer MA spiegelbildlich. Nie einen "
            "Szenario-Tag schreiben, der der Ist-Lage widerspricht.",
            "  • Gib KEINE pauschale Anlageberatung — datenbasierte Einordnung"
            + (" (Ausnahme: DEPOT-PROFIL am Ende)." if inc.get('depot_55plus', True) else "."),
            "",
            "════════════════════════════════════════",
            "MARKTDATEN",
            "════════════════════════════════════════",
            "",
        ]

    for r in results:
        ticker = r['ticker']
        name, cat = meta.get(ticker, (ticker, ''))
        ctx_tag = "  (KONTEXT — Leitindex/Marktumfeld, NICHT das Analyseziel)" if r.get('context') else ""

        if r.get('error'):
            lines += [f"─── {ticker} | {name}{ctx_tag} ───", f"FEHLER: {r['error']}", ""]
            continue

        close     = r['close']
        ind       = r.get('indicator_values', {})
        week_str  = f"{r['week_ret']:+.1f}%"  if r['week_ret']  is not None else "n/a"
        month_str = f"{r['month_ret']:+.1f}%" if r['month_ret'] is not None else "n/a"

        # Kompakt: Kontext-Assets (Leitindex, ^TNX) als EIN-Zeiler statt vollem Block.
        if compact and r.get('context'):
            _ma = []
            for _lbl, _col in (('SMA50', 'sma50'), ('SMA200', 'sma200')):
                _v = ind.get(_col)
                if _v and close:
                    _ma.append(f"{'>' if close > _v else '<'}{_lbl}")
            _xtra = []
            if ind.get('rsi') is not None:
                _xtra.append(f"RSI {ind['rsi']}")
            if ind.get('markov_regime') is not None:
                _xtra.append(f"Markov {_regime.get(ind['markov_regime'], ind['markov_regime'])}")
            if ind.get('ewo') is not None:
                _xtra.append(f"ewo {ind['ewo']:+.1f}")
            if ind.get('macd_diff') is not None:
                _xtra.append(f"MACD-Hist {ind['macd_diff']:+.2f}")
            lines += [
                f"─── {ticker} | {name} | {cat} (KONTEXT, kompakt) ───",
                f"Kurs {close} | 1W {week_str} | 1M {month_str}"
                + (" | Trend " + " ".join(_ma) if _ma else "")
                + (" | " + " · ".join(_xtra) if _xtra else ""),
                "",
            ]
            continue

        lines += [
            f"─── {ticker} | {name} | {cat}{ctx_tag} ───",
            f"Kurs: {close}  |  Stand: {r['datum']}  |  1W: {week_str}  |  1M: {month_str}",
        ]

        # ── [1-TREND] MA-Positionierung ───────────────────────────────────────
        lines.append("[1-TREND — MA-Positionierung → Hauptregime]")
        ma_found = False
        _ma_vals = {}
        for label, col in [('SMA20', 'sma20'), ('SMA50', 'sma50'), ('SMA200', 'sma200')]:
            v = ind.get(col)
            if v and close:
                pct = (close - v) / v * 100
                pos = 'ÜBER' if pct > 0 else 'UNTER'
                lines.append(f"  {label}: {v}  → Kurs {abs(pct):.1f}% {pos} {label}")
                _ma_vals[label] = v
                ma_found = True
        if not ma_found:
            lines.append("  MA-Daten: nicht verfügbar")
        else:
            # VERBINDLICHER Regime-Fakt + fertig ausformulierte Trigger. Die KI soll diese
            # Richtung WÖRTLICH übernehmen, statt eine eigene >/<-Richtung zu erfinden —
            # verhindert die MA-Vorzeichen-Inversion ('Abwärtstrend (>SMA200)' oder
            # 'Rückeroberung >SMA200 = bärisch'). SMA200 = dominante Trennlinie Bulle/Bär.
            _s200 = _ma_vals.get('SMA200')
            if _s200 and close:
                if close < _s200:
                    _s50 = _ma_vals.get('SMA50')
                    if _s50 and _s50 < close:
                        _lower = f"SMA50 {_s50}"
                    elif ind.get('sup_support'):
                        _lower = f"sup_support {ind.get('sup_support')}"
                    else:
                        _lower = "die nächste tiefere Unterstützung"
                    lines += [
                        f"  → HAUPTREGIME (VERBINDLICH): BÄRISCH — Kurs {close} liegt UNTER SMA200 {_s200}.",
                        f"    · bärische Bestätigung = Schlusskurs UNTER {_lower} (tieferliegend).",
                        f"    · bullische Invalidierung = Rückeroberung, Schlusskurs ÜBER SMA200 {_s200}.",
                    ]
                else:
                    _res = ind.get('sup_resistance')
                    _upper = f"sup_resistance {_res}" if _res else "das nächste höhere Widerstandsniveau"
                    lines += [
                        f"  → HAUPTREGIME (VERBINDLICH): BULLISCH — Kurs {close} liegt ÜBER SMA200 {_s200}.",
                        f"    · bullische Bestätigung = Schlusskurs ÜBER {_upper} (höherliegend).",
                        f"    · bärische Invalidierung = Schlusskurs UNTER SMA200 {_s200}.",
                    ]

        # ── [2-MOMENTUM] RSI + MACD ───────────────────────────────────────────
        lines.append("[2-MOMENTUM — RSI + MACD]")
        rsi = ind.get('rsi')
        if rsi is not None:
            interp = 'überkauft' if rsi > 70 else ('überverkauft' if rsi < 30 else 'neutral')
            lines.append(f"  RSI(14): {rsi}  ({interp}; >70=überkauft, <30=überverkauft)")
        macd      = ind.get('macd')
        macd_sig  = ind.get('macd_signal')
        macd_diff = ind.get('macd_diff')
        if macd is not None:
            lines.append(f"  MACD-Linie:   {macd}")
        if macd_sig is not None:
            lines.append(f"  MACD-Signal:  {macd_sig}")
        if macd_diff is not None:
            d = 'bullisch' if macd_diff > 0 else 'bearisch'
            lines.append(f"  MACD-Hist:    {macd_diff:+.4f}  ({d}; Vorzeichen = Richtung)")
        if rsi is None and macd is None:
            lines.append("  Momentum-Daten: nicht verfügbar")

        # ── [3-VOLATILITÄT] ATR ───────────────────────────────────────────────
        lines.append("[3-VOLATILITÄT — ATR]")
        atr = (ind.get('atr14') or ind.get('atr') or
               ind.get('bos_atr') or ind.get('sqz_atr'))
        if atr and close:
            pct = atr / close * 100
            lines.append(f"  ATR(14): {atr}  ({pct:.2f}% des Kurses)")
        else:
            lines.append("  ATR: nicht verfügbar")

        # ── [4-MAKRO] Zinskontext ─────────────────────────────────────────────
        # Verweis nur, wenn ^TNX wirklich als Block vorhanden ist (has_tnx) — sonst
        # zeigt er ins Leere. TNX selbst bekommt keinen Selbstverweis.
        if has_tnx and ticker != '^TNX':
            lines.append("[4-MAKRO → Zinskontext: siehe ^TNX-Block]")

        # ── [5-OPTIONALE REGIME-DATEN] ────────────────────────────────────────
        any_opt = False

        # 5a: Markov · HA · EWO
        if inc.get('regime_optional', True):
            regime_lines = []
            markov = ind.get('markov_regime')
            if markov is not None:
                regime_lines.append(f"  markov_regime: {_regime.get(markov, markov)}")
            ha_c = ind.get('ha_close')
            ha_o = ind.get('ha_open')
            if ha_c is not None and ha_o is not None:
                regime_lines.append(
                    f"  ha_direction:  {'Bull' if ha_c > ha_o else 'Bear'}"
                    f"  (ha_close {'>' if ha_c > ha_o else '<'} ha_open)"
                )
            ewo = ind.get('ewo')
            if ewo is not None:
                regime_lines.append(
                    f"  ewo:           {ewo:+.2f}  "
                    f"({'positiv=bullisch' if ewo > 0 else 'negativ=bearisch'})"
                )
            if regime_lines:
                if not any_opt:
                    lines.append("[5-OPTIONALE REGIME-DATEN — überlappend, potenziell widersprüchlich]")
                    any_opt = True
                lines.extend(regime_lines)

        # 5b: Support / Resistance
        if inc.get('support_resist', True):
            sup = ind.get('sup_support')
            res = ind.get('sup_resistance')
            if sup or res:
                if not any_opt:
                    lines.append("[5-OPTIONALE REGIME-DATEN — überlappend, potenziell widersprüchlich]")
                    any_opt = True
                if sup:
                    lines.append(f"  sup_support:   {sup}")
                if res:
                    lines.append(f"  sup_resistance:{res}")

        # 5c: Pivot Points
        if inc.get('pivot', True):
            pvm = _pivot_compression(ind, close)
            if pvm:
                if not any_opt:
                    lines.append("[5-OPTIONALE REGIME-DATEN — überlappend, potenziell widersprüchlich]")
                    any_opt = True
                flag = '  *** KOMPRESSION — Breakout wahrscheinlich ***' if pvm['compressed'] else ''
                lines += [
                    f"  pvt_p: {ind.get('pvt_p','?')}  R1: {ind.get('pvt_r1','?')}  S1: {ind.get('pvt_s1','?')}",
                    f"  pvt_compression: {pvm['compression_pct']}%{flag}",
                    f"  pvt_close_vs_p:  {pvm['close_vs_p_pct']:+.2f}%  "
                    f"(Kurs {'über' if pvm['close_vs_p_pct'] >= 0 else 'unter'} Pivot, "
                    f"nächstes Level: {pvm['nearest_label']})",
                ]

        # ── Trend-Snippets (kurz) ─────────────────────────────────────────────
        if inc.get('snippets', True):
            snips = r.get('trend_snippets', {})
            if snips:
                lines.append("[TREND-VERLAUF KURZ (10P → 5P → heute)]")
                for col, snippet in snips.items():
                    lines.append(f"  {col}: {snippet}")

        # ── Trend-Snippets (lang) ─────────────────────────────────────────────
        if inc.get('extended_snippets', False):
            ext = r.get('extended_snippets', {})
            if ext:
                lines.append("[TREND-VERLAUF LANG (200P → 50P → heute)]")
                for col, snippet in ext.items():
                    lines.append(f"  {col}: {snippet}")
            elif inc.get('extended_snippets', False):
                lines.append("[TREND-VERLAUF LANG: nicht genug Daten (Period verlängern)]")

        # ── 4-Wochen-Vergleich ────────────────────────────────────────────────
        s4w = r.get('snapshot_4w', {}) if inc.get('compare_4w', True) else {}
        if s4w:
            chg = s4w['close_chg_pct']
            lines.append(f"[VERGLEICH VOR 4 WOCHEN — Stand {s4w['datum_then']}]")
            lines.append(f"  Kurs damals: {s4w['close_then']}  → heute: {chg:+.1f}%")
            ind_then = s4w.get('indicator_values', {})
            for col in ['markov_regime', 'macd_diff', 'ewo', 'rsi']:
                vt, vn = ind_then.get(col), ind.get(col)
                if vt is None or vn is None:
                    continue
                sign_flip  = (vt >= 0) != (vn >= 0)
                regime_chg = col == 'markov_regime' and vt != vn
                chg_pct    = abs(vn - vt) / (abs(vt) + 1e-9) * 100
                if sign_flip or regime_chg:
                    flag = '  ⚠ WECHSEL'
                    if col == 'markov_regime':
                        lines.append(f"  {col}: {_regime.get(vt,'?')} → {_regime.get(vn,'?')}{flag}")
                    else:
                        lines.append(f"  {col}: {vt:+.2f} → {vn:+.2f}{flag}")
                elif chg_pct > 15:
                    lines.append(f"  {col}: {vt} → {vn}  ({chg_pct:+.0f}%)")
            pvm_then = s4w.get('pivot_metrics', {})
            pvm_now  = _pivot_compression(ind, close)
            if pvm_then and pvm_now:
                trend = '↘ Kompression zugenommen' if pvm_now['compression_pct'] < pvm_then['compression_pct'] else '↗ Niveaus weiten sich'
                lines.append(f"  pvt_compression: {pvm_then['compression_pct']}% → {pvm_now['compression_pct']}%  {trend}")

        lines.append("")

    # ── Nachrichten-Sentiment ──────────────────────────────────────────────────
    if inc.get('headlines', True) and headlines and any(bool(v) for v in headlines.values()):
        name_map = {s[0]: s[2] for s in symbol_meta}
        lines += [
            "════════════════════════════════════════",
            "NACHRICHTEN-SENTIMENT (Yahoo Finance RSS — nur Titel, VADER)",
            "Compound: +1.0=sehr positiv · 0=neutral · -1.0=sehr negativ · ±0.05=Schwelle",
            "════════════════════════════════════════",
            "",
        ]
        for ticker, articles in headlines.items():
            if not articles:
                continue
            lines.append(f"{ticker} | {name_map.get(ticker, ticker)}:")
            for a in articles:
                lines.append(f"  [{a['sentiment'].upper()} {a['compound']:+.2f}]  {a['title']}")
                lines.append(f"  Datum: {a['published']}")
            lines.append("")

    # ── Cross-Asset-Korrelationen ──────────────────────────────────────────────
    has_corr = False
    if inc.get('correlations', True):
        _corr_block = _correlation_prompt_block()
        if _corr_block:
            lines.append(_corr_block)
            has_corr = True

    # ── Index-Status / Marktbreite (Einzel-Asset-Analyse) ──────────────────────
    if market_status and market_status.strip():
        lines += [
            "════════════════════════════════════════",
            "INDEX-STATUS / MARKTBREITE (Frühwarn-Banner des zugehörigen Index)",
            "Score-Skala 0–100; höherer Frühwarn-Score = mehr Stress. Kontext, kein Einzelwert-Vorlesen.",
            "════════════════════════════════════════",
            "",
            market_status.strip(),
            "",
        ]

    # ── Unternehmens-/Asset-Info (Einzel-Asset-Analyse) ────────────────────────
    if asset_info and asset_info.strip():
        lines += [
            "════════════════════════════════════════",
            "UNTERNEHMENS- / ASSET-INFO (Stammdaten + Fundamental-Kennzahlen)",
            "════════════════════════════════════════",
            "",
            asset_info.strip(),
            "",
        ]

    # ── Chart-Signale + Formel (Einzel-Asset-Analyse) ──────────────────────────
    if signals and signals.strip():
        lines += [
            "════════════════════════════════════════",
            "CHART-SIGNALE (Buy/Sell des Trend-Charts) + zugrunde liegende Formel",
            "════════════════════════════════════════",
            "",
            signals.strip(),
            "",
        ]

    # ── Saisonalität (Einzel-Asset-Analyse) ────────────────────────────────────
    if seasonality and seasonality.strip():
        lines += [
            "════════════════════════════════════════",
            "SAISONALITÄT (historische Jahresverläufe, aktuelle Position im Jahr)",
            "════════════════════════════════════════",
            "",
            seasonality.strip(),
            "",
        ]

    # ── Sektor-Rotation (Einzel-Asset-Analyse) ─────────────────────────────────
    if sector_rotation and sector_rotation.strip():
        lines += [
            "════════════════════════════════════════",
            "SEKTOR-ROTATION (RRG — relative Stärke der Sektoren vs. Markt)",
            "RS-Ratio/Momentum um 100: Leading (>100/>100) · Weakening (>100/<100) · "
            "Improving (<100/>100) · Lagging (<100/<100).",
            "════════════════════════════════════════",
            "",
            sector_rotation.strip(),
            "",
        ]

    # ── Nachrichten (Einzel-Asset-Analyse) ─────────────────────────────────────
    if news and news.strip():
        lines += [
            "════════════════════════════════════════",
            "NACHRICHTEN (Top 5, Yahoo Finance RSS — Titel + VADER-Sentiment)",
            "Compound: +1.0=sehr positiv · 0=neutral · -1.0=sehr negativ · ±0.05=Schwelle.",
            "Hinweis: Nur die TITEL + Sentiment bewerten — die Artikel-Links können NICHT "
            "geöffnet werden (kein Web-Zugriff). Keine Inhalte erfinden.",
            "════════════════════════════════════════",
            "",
            news.strip(),
            "",
        ]

    # ── Analyse-Aufgabe ────────────────────────────────────────────────────────
    if single_asset:
        primary = primary_ticker or (results[0]['ticker'] if results else '')
        lines += [
            "════════════════════════════════════════",
            "ANALYSE-AUFGABE",
            "════════════════════════════════════════",
            f"Ziel: {primary}",
            "",
            "TEIL 1 — ASSET-THESE (zuerst! 3–5 Sätze, das Wichtigste oben):",
            f"  • Was ist der dominante Treiber JETZT für {primary}? Verbinde technisches Bild "
            "(Trend/Momentum/Volatilität/Regime), fundamentale Lage (Bewertung/Profitabilität/Bilanz) "
            "und Marktumfeld (Leitindex/Marktbreite) zu EINER kohärenten These — belege mit 2–3 Werten.",
            "  • 1 Satz: Welches Signal würde diese These KIPPEN?",
            "",
            f"TEIL 2 — TECHNISCHE EVIDENZ (Ziel {primary}; ein evtl. Leitindex NUR als 1 klar "
            "markierte Kontext-Zeile, nicht als Analyseziel):",
            "  Format: <ticker>: <bull/bear/sideways> · Mom <stark/neutral/schwach> · Vol "
            "<hoch/normal/niedrig> · Regime <risk-on/off/unklar> · Auffälligkeit: <Extremwert/"
            "Vorzeichenwechsel oder '—'>",
            "  Schwellen (VERBINDLICH): Trend bull=Kurs>SMA50 UND >SMA200 · bear=<SMA50 UND <SMA200 · "
            "sonst sideways.  Momentum(RSI14): stark≥60 · neutral 40–59 · schwach<40 (MACD-Hist "
            "bestätigt +/−; widerspricht es → neutral + KONFLIKT).  Volatilität(ATR% Kurs): niedrig<1.5 "
            "· normal 1.5–3.0 · hoch>3.0.  Regime nur bei eindeutiger Ausrichtung, sonst 'unklar'.",
        ]
        if asset_info:
            lines += [
                "",
                "TEIL 3 — FUNDAMENTALE EINORDNUNG:",
                "  • Bewertung (günstig/fair/teuer) anhand KGV/EV-EBITDA/KBV/KUV im Verhältnis zur "
                "Ertragslage; Qualität (Margen, ROE, Umsatzwachstum) und Bilanzrisiko (Debt/Equity, "
                "Current Ratio). Benenne den zentralen Konflikt Bewertung ↔ Qualität, falls vorhanden.",
            ]
        if seasonality:
            lines += [
                "",
                "TEIL 4 — SAISONALITÄT:",
                "  • Stützt oder widerspricht das saisonale Muster die technische These? Ordne "
                "Aufwärts-Wahrscheinlichkeit und Ø-Forward-Return ein (nicht nur nennen).",
            ]
        if signals:
            lines += [
                "",
                "TEIL 5 — SIGNALLAGE:",
                "  • Ob AKTUELL ein Signal ansteht, sagt die 'Aktuell'-Zeile im Signale-Block "
                "VERBINDLICH — leite das NICHT selbst aus der Formel ab. Nutze die Formeln plus die "
                "'Formel-Spalten aktuell'-Werte NUR zur ERKLÄRUNG (welche Bedingung ist erfüllt/nicht).",
                "  • Zitiere Bedingungen NUR wörtlich aus der jeweils richtigen Formel. Ordne KEINE "
                "Bedingung der falschen Formel zu und erfinde KEINE Schwellen, die dort nicht stehen "
                "(z.B. 'rsi>=72' steht in der SELL-, NICHT in der Buy-Formel). Fehlt der Wert einer "
                "Spalte, sag 'nicht angegeben' statt zu raten.",
                "  • Tragfähigkeit des letzten Signals angesichts Trend/Regime/Fundamental einordnen; "
                "Konflikt zwischen Signal und Gesamtbild ausdrücklich benennen.",
            ]
        if has_corr or sector_rotation or news:
            _ctx = ["", "TEIL 6 — MARKTUMFELD-KONTEXT (Einbettung in das größere Bild):"]
            if has_corr:
                _ctx.append(
                    "  • Cross-Asset-Korrelationen: In welchem Risk-Regime (risk-on/off, Entkopplung) "
                    "steht der Markt, und was bedeutet das FÜR DIESES Asset (z.B. High-Beta-Risk, "
                    "sicherer Hafen, zinssensitiv)?")
            if sector_rotation:
                _ctx.append(
                    "  • Sektor-Rotation: Steht der Sektor des Assets relativ VORNE (Leading/Improving) "
                    "oder HINTEN (Weakening/Lagging)? Rückenwind oder Gegenwind für den Einzelwert — "
                    "erhärtet oder relativiert das die These?")
            if news:
                _ctx.append(
                    "  • Nachrichten: Passt das Titel-Sentiment der aktuellen Meldungen zur technischen "
                    "These? Nenne news-getriebene Chancen/Risiken — bewerte NUR die gelieferten Titel "
                    "(keine Link-Inhalte, keine erfundenen Fakten).")
            lines += _ctx
        lines += [
            "",
            f"FAZIT — 2–3 Sätze, datenbasiert, zu {primary} (KEIN pauschaler Kauf-/Verkaufsrat): "
            "Chancen vs. Risiken, und worauf als Nächstes zu achten ist.",
        ]
        # Freitext + Rückgabe: die Multi-Asset-Zusatzblöcke unten überspringen.
        if freetext and freetext.strip():
            lines += [
                "",
                "════════════════════════════════════════",
                "INDIVIDUELLE ZUSATZFRAGEN / ANWEISUNGEN",
                "════════════════════════════════════════",
                freetext.strip(),
            ]
        return "\n".join(lines)

    lines += [
        "════════════════════════════════════════",
        "ANALYSE-AUFGABE",
        "════════════════════════════════════════",
        "",
        "TEIL 1 — MARKT-THESE (zuerst! 3–5 Sätze, das Wichtigste oben):",
        "  • Benenne DEN dominanten Treiber JETZT (z.B. Rendite-/Zinsschock, Dollar-Stärke, "
        "Wachstumssorge, breites Risk-off) und belege ihn mit 2–3 konkreten Werten ÜBER Assets hinweg.",
        "  • Welche Assets bestätigen den Treiber, welche widersprechen? Komplex-Sicht (Metalle "
        "zusammen, zinssensitive Assets zusammen) — nicht Asset für Asset.",
        "  • 1 Satz: Welches Signal würde diese These KIPPEN?",
        "",
        f"TEIL 2 — EVIDENZ JE ASSET (alle {n_total}, GENAU EINE Zeile pro Asset, KEINE Rohzahl-Wiederholung):",
        "  Format: <ticker>: <bull/bear/sideways> · Mom <stark/neutral/schwach> · Vol "
        "<hoch/normal/niedrig> · Regime <risk-on/off/unklar> · Auffälligkeit: <Extremwert/"
        "Vorzeichenwechsel oder '—'>",
        "  Asset ohne eigene OHLC: '<ticker>: keine OHLC — Einordnung aus Korrelationsblock' "
        "(falls dort vertreten, z.B. ZN=F, DX-Y.NYB), sonst 'keine Daten'.",
        "  Schwellen (VERBINDLICH): Trend bull=Kurs>SMA50 UND >SMA200 · bear=<SMA50 UND <SMA200 · "
        "sonst sideways.  Momentum(RSI14): stark≥60 · neutral 40–59 · schwach<40 (MACD-Hist "
        "bestätigt +/−; widerspricht es → neutral + KONFLIKT).  Volatilität(ATR% Kurs): niedrig<1.5 "
        "· normal 1.5–3.0 · hoch>3.0.  Regime nur bei eindeutiger Ausrichtung, sonst 'unklar'.",
    ]

    if inc.get('global_summary', True):
        lines += [
            "",
            "─────────────────────────────────────────",
            "GLOBAL SUMMARY (Synthese, keine Aufzählung)",
            "─────────────────────────────────────────",
            "Risk-Modus:      [risk-on / neutral / risk-off / gemischt] + 1 Satz Mechanismus.",
            "Dominante Kraft: [1 Treiber + WIE er wirkt, z.B. 'steigende Renditen drücken Gold, "
            "Aktien und Kupfer gleichzeitig'] — Mechanismus, keine bloße Asset-Liste.",
            "Bestätigung:     [Assets/Gruppen, die die These stützen — je 1 Kennzahl].",
            "Divergenz:       [Assets, die widersprechen — mit möglicher Erklärung].",
            "Confidence:      [0–100] + was sie senkt/hebt (1 Satz).",
        ]

    if inc.get('correlations', True):
        lines += [
            "",
            "─────────────────────────────────────────",
            "KORRELATIONS-NUTZUNG (Cross-Asset — interpretieren, NICHT abschreiben)",
            "─────────────────────────────────────────",
            "Für JEDES Paar im Block CROSS-ASSET-KORRELATIONEN (ALLE, nicht nur einige):",
            "  • Regime benennen (Gleichlauf / Gegenlauf / entkoppelt) + ökonomische Bedeutung.",
            "  • Aktuellen Wert gegen 'Ø gesamt' UND den Trend spiegeln: Ist die Kopplung "
            "UNGEWÖHNLICH (weicht vom Ø ab) oder verstärkt/lockert sie sich? Das ist das Signal.",
            "  • Genau 1 Portfolio-Implikation je Paar.",
            "  Regime-Flags: SPX↔USD positiv = Stress/US-Sonderrolle; ZN↔SPX positiv (bes. über Ø) "
            "= Anleihen schützen nicht mehr → 60/40 geschwächt; SPX↔BTC hoch = BTC = High-Beta-Risk; "
            "Kupfer↔SPX bestätigt/verneint die Konjunktur.",
            "GUT: 'ZN↔SPX +0.54 (Ø −0.08, steigend) → Anleihen und Aktien koppeln positiv, weit über "
            "dem historischen Schnitt: Zins-Regime, Anleihen diversifizieren im Abschwung nicht mehr → "
            "Absicherung eher über Cash/Gold.'",
            "SCHLECHT (NICHT so): 'S&P 500 und Bitcoin: 30T +0.41, 90T +0.40 (positiv).' — nur Zahlen "
            "wiederholt, keine Aussage.",
        ]

    if inc.get('trend_compare', True):
        lines += [
            "",
            "─────────────────────────────────────────",
            "TRENDVERGLEICH VOR 4 WOCHEN & UMSCHICHTUNG",
            "─────────────────────────────────────────",
            "Nutze die [VERGLEICH VOR 4 WOCHEN]-Blöcke pro Asset PLUS die Korrelations-Trends.",
            "Welche Regime-/Vorzeichenwechsel sind seit vor 4 Wochen aufgetreten?",
            "Allocation-Richtung (nur Richtung aus den Daten, kein pauschaler Rat):",
            "  KONSISTENZ-PFLICHT: Jede Umschichtung braucht einen KAUSAL passenden Grund. Erhöhe ein "
            "Asset NIE mit der Begründung, dass es fällt — außer als explizit benannte Mean-Reversion-"
            "These (RSI überverkauft ≤ 20 oder Kompression). Fehlt ein sauberer Grund: 'neutral halten'.",
            "  Beispiel: 'Aktienquote reduzieren: SPX+GDAXI Markov Bull→Bear + MACD dreht negativ'",
            "            'Cash/Gold statt Anleihen: ZN↔SPX-Korrelation steigt über Ø (Diversifikation schwächer)'",
        ]

    if inc.get('depot_55plus', True):
        lines += [
            "",
            "─────────────────────────────────────────",
            "DEPOT-PROFIL: ANLEGER 55+",
            "(Kapitalerhalt vor Wachstum | 10–15J Horizont | begrenzte Drawdown-Toleranz)",
            "─────────────────────────────────────────",
            "Gewichtung (Summe = 100%):",
            "  Aktien:       X %  — Begründung aus Marktdaten",
            "  Anleihen:     X %  — Begründung aus Marktdaten",
            "  Edelmetalle:  X %  — Begründung aus Marktdaten",
            "  Rohstoffe:    X %  — Begründung aus Marktdaten",
            "  Cash:         X %  — Begründung aus Marktdaten",
            "Übergewichten: [konkrete Asset-Klasse + Datenbegründung]",
            "Untergewichten:[konkrete Asset-Klasse + Datenbegründung]",
            "Meiden:        [konkrete Asset-Klasse + Risikobegründung]",
        ]

    # ── Freier Text (individuelle Zusatzfragen) ────────────────────────────────
    if freetext and freetext.strip():
        lines += [
            "",
            "════════════════════════════════════════",
            "INDIVIDUELLE ZUSATZFRAGEN / ANWEISUNGEN",
            "════════════════════════════════════════",
            freetext.strip(),
        ]

    return "\n".join(lines)


# ── Datenauswahl-Konfiguration ────────────────────────────────────────────────

_CFG_SECTIONS_KEY  = 'market_overview_sections'
_CFG_FREETEXT_KEY  = 'market_overview_freetext'

# (label, default_an) — Trend/Momentum/Volatilität sind immer Pflicht
_SECTION_DEFAULTS: dict[str, tuple[str, bool]] = {
    'regime_optional':    ('Regime-Daten (Markov · HA · EWO)',       True),
    'pivot':              ('Pivot Points (S1/P/R1 + Kompression)',    True),
    'support_resist':     ('Support / Resistance',                    True),
    'snippets':           ('Trend-Verlauf kurz (10P→5P→heute)',       True),
    'extended_snippets':  ('Trend-Verlauf lang (200P→50P→heute)',     False),
    'compare_4w':         ('4-Wochen-Vergleich',                      True),
    'headlines':          ('Nachrichten-Sentiment (Yahoo RSS)',        True),
    'correlations':       ('Cross-Asset-Korrelationen',               True),
    'global_summary':     ('Global Summary + Risk Score',             True),
    'trend_compare':      ('Trendvergleich & Umschichtung',           True),
    'depot_55plus':       ('Depot-Profil 55+',                        True),
}

# Section-Preset für die Einzel-Asset-Analyse (Asset-Viewer-Tab). Die portfolio-
# bezogenen Abschnitte des Market Overview (Cross-Asset-Korrelationen, Global Summary,
# 4-Wochen-Umschichtung, Depot 55+) sind AUS — sie ergeben nur über viele Assets Sinn —
# während alle Pro-Asset-Kriterien (Trend, Momentum, Volatilität, Regime, Pivots,
# Snippets, 4-Wochen-Vergleich) AN bleiben, damit ein einzelnes Asset nach demselben
# Maßstab beurteilt wird wie im Market Overview.
_SINGLE_ASSET_SECTIONS: dict[str, bool] = {
    'regime_optional':   True,
    'pivot':             True,
    'support_resist':    True,
    'snippets':          True,
    'extended_snippets': True,
    'compare_4w':        True,
    'headlines':         False,
    'correlations':      True,    # Cross-Asset-Korrelationen als Makro-Regime-Kontext
    'global_summary':    False,
    'trend_compare':     False,
    'depot_55plus':      False,
}


def _get_sector_summary():
    """Sector-rotation summary (market-wide, same for every asset) — computed once per
    session and cached in session_state, since build_summary() fetches ~12 ETFs (~15s)."""
    key = '_sector_rotation_summary'
    if key not in st.session_state:
        try:
            from tradinglib.sector_rotation import SectorRotation
            st.session_state[key] = SectorRotation().build_summary()
        except Exception:
            logger.warning("sector rotation summary failed", exc_info=True)
            st.session_state[key] = None
    return st.session_state[key]


def build_sector_rotation_text(asset_sector: str) -> str:
    """Sector-rotation context block for a single asset (via its Yahoo sector → ETF).

    Returns '' when the asset has no sector (crypto/index/commodity) or no data.
    Uses the market-wide summary (US sectors as the global RRG reference), matched to
    the asset's sector by ETF ticker to avoid GICS-vs-Yahoo naming mismatches.
    """
    if not asset_sector:
        return ''
    summ = _get_sector_summary()
    if summ is None or getattr(summ, 'empty', True):
        return ''
    try:
        from tradinglib.sector_stocks import SECTOR_ETF_MAP
        from tradinglib import sector_rotation as _srot
        etf = SECTOR_ETF_MAP.get(asset_sector, '')
        return _srot.sector_context_text(summ, asset_etf=etf, asset_sector=asset_sector)
    except Exception:
        logger.debug("build_sector_rotation_text failed", exc_info=True)
        return ''


def get_rate_context(interval: str = '1d', period: str = '1y', sys_conf=None) -> str:
    """Kurzer Zins-Kontext-String (^TNX = Rendite 10J US-Staatsanleihen) für den Report,
    z.B. '4.35% (1M -1.2%)'. Leerer String bei fehlendem sys_conf oder Fehler."""
    if sys_conf is None:
        return ''
    try:
        r = _compute_for_symbol('^TNX', '^TNX', interval, period, [], sys_conf)
        if r.get('error') or r.get('close') is None:
            return ''
        s = f"{r['close']}%"
        if r.get('month_ret') is not None:
            s += f" (1M {r['month_ret']:+.1f}%)"
        return s
    except Exception:
        logger.debug("get_rate_context failed", exc_info=True)
        return ''


def _compact_asset_info(text: str) -> str:
    """Kompakt: Geschäftsbeschreibung auf ~2 Sätze kürzen, Fundamental-Kennzahlen behalten."""
    if not text or not text.strip():
        return text
    import re as _re
    lines = text.split('\n')
    idx = next((i for i, l in enumerate(lines) if l.startswith('Stammdaten:')), None)
    if idx is None:
        summary, rest = text.strip(), ''
    else:
        summary = '\n'.join(lines[:idx]).strip()
        rest    = '\n'.join(lines[idx:]).strip()
    if summary:
        sents   = _re.split(r'(?<=[.!?])\s+', summary)
        summary = ' '.join(sents[:2]).strip()
        if len(summary) > 400:
            summary = summary[:400].rsplit(' ', 1)[0] + ' …'
    return (summary + ('\n\n' + rest if rest else '')).strip()


def _compact_news(text: str, max_items: int = 3) -> str:
    """Kompakt: News auf die Top-N reduzieren und die URL-Zeilen weglassen
    (die KI kann Links ohnehin nicht öffnen; im Report bleiben sie klickbar)."""
    if not text or not text.strip():
        return text
    kept, count = [], 0
    for line in text.split('\n'):
        if line.strip().startswith('http'):
            continue
        if line.startswith('['):
            count += 1
            if count > max_items:
                break
        kept.append(line)
    return '\n'.join(kept).strip()


def build_single_asset_prompt(
    df: pd.DataFrame, display_id: str, name: str, category: str,
    interval: str, period: str,
    sections: dict | None = None,
    asset_info: str | None = None,
    market_status: str | None = None,
    signals: str | None = None,
    seasonality: str | None = None,
    sector_rotation: str | None = None,
    news: str | None = None,
    parent_index: tuple | None = None,
    indicators: list | None = None,
    sys_conf=None,
    include_tnx: bool = True,
    market: str = '',
    compact: bool = False,
    freetext: str | None = None,
) -> str:
    """Builds the AI prompt for a SINGLE asset, reusing the market-overview prompt
    scaffold (same per-asset criteria / thresholds) with a single-asset framing.

    df: already-computed OHLC + indicator DataFrame from the chart (no re-fetch).
    asset_info:    optional pre-formatted fundamentals/business-summary block.
    market_status: optional index breadth / early-warning status block.
    signals:       optional buy/sell-signals + formula block.
    seasonality:   optional seasonality summary block.
    parent_index:  optional (display_id, yf_ticker, name) of the leading index the
                   asset belongs to — computed as a CONTEXT data block + membership note.
    include_tnx:   attach ^TNX (10y US Treasury yield) as a compact rate-environment
                   context block (unless the target asset IS ^TNX).
    freetext:      optional individual user question appended at the end.
    """
    result = _result_from_df(df, display_id, interval)
    result['name']     = name
    result['category'] = category
    results     = [result]
    symbol_meta = [(display_id, display_id, name, category)]
    membership_note = None

    # Leitindex als Kontext-Datenblock (eigene OHLC/Indikatoren), plus Membership-Hinweis.
    if parent_index and sys_conf is not None:
        try:
            idx_id, idx_yf = parent_index[0], parent_index[1]
            idx_name = parent_index[2] if len(parent_index) > 2 else idx_id
            idx_res = _compute_for_symbol(
                idx_id, idx_yf, interval, period, list(indicators or []), sys_conf
            )
            idx_res['name']     = idx_name
            idx_res['category'] = 'Index'
            idx_res['context']  = True
            results.append(idx_res)
            symbol_meta.append((idx_id, idx_yf, idx_name, 'Index'))
            membership_note = (f"Zugehörigkeit: {display_id} ist Bestandteil des Leitindex "
                               f"{idx_id} ({idx_name}) — dessen Daten sind als KONTEXT beigefügt.")
        except Exception:
            logger.warning("single-asset: parent index compute failed", exc_info=True)

    # ^TNX (Rendite 10J US-Staatsanleihen) als kompakter Zins-Umfeld-Kontext — außer das
    # Ziel-Asset IST ^TNX. indicators=[] hält den Block schlank (Yield-Level + 1W/1M-Change
    # + Kern-Momentum), das reicht als Makro-/Zinskontext.
    if include_tnx and str(display_id) != '^TNX' and sys_conf is not None:
        try:
            tnx = _compute_for_symbol('^TNX', '^TNX', interval, period, [], sys_conf)
            if not tnx.get('error'):
                _tnx_name = '10J US-Staatsanleihe (Rendite)'
                tnx['name']     = _tnx_name
                tnx['category'] = 'Zins USA'
                tnx['context']  = True
                results.append(tnx)
                symbol_meta.append(('^TNX', '^TNX', _tnx_name, 'Zins USA'))
        except Exception:
            logger.warning("single-asset: TNX context compute failed", exc_info=True)

    if compact:
        asset_info = _compact_asset_info(asset_info)
        news       = _compact_news(news)

    sec = sections if sections is not None else dict(_SINGLE_ASSET_SECTIONS)
    return _build_market_prompt(
        results, symbol_meta, interval, period, indicators=[],
        headlines=None, sections=sec, freetext=freetext,
        asset_info=asset_info, market_status=market_status,
        signals=signals, seasonality=seasonality, sector_rotation=sector_rotation,
        news=news,
        single_asset=True, primary_ticker=display_id,
        primary_name=name, primary_market=market, membership_note=membership_note,
        compact=compact,
    )


def render_single_asset_ai(
    df: pd.DataFrame, display_id: str, name: str, category: str,
    interval: str, period: str,
    asset_info: str | None = None,
    market_status: str | None = None,
    signals: str | None = None,
    seasonality: str | None = None,
    news: str | None = None,
    parent_index: tuple | None = None,
    indicators: list | None = None,
    sys_conf=None,
    market: str = '',
    asset_sector: str = '',
    username: str = 'admin',
    region=st,
) -> None:
    """Self-contained single-asset AI analysis UI for the Asset Viewer tab.

    Two-step flow inside the caller's fragment:
      1. free-text question + "Analyse starten" button
      2. on click → build prompt (reusing the market-overview logic) → AiClient →
         cache result in session_state (keyed per ticker so switching assets resets).
    """
    result_key = f'_asset_ai_result_{display_id}'
    cached     = st.session_state.get(result_key)

    region.caption(t('asset_ai.intro'))

    freetext = region.text_area(
        t('asset_ai.freetext_label'),
        value='',
        height=80,
        placeholder=t('asset_ai.freetext_placeholder'),
        key=f'_asset_ai_freetext_{display_id}',
    )

    # Kompakter Prompt (Default): kürzt Business-Summary, Leitindex/^TNX-Kontext und News
    # → passt in die knappen Free-Tier-Limits (Groq TPM, GitHub Input-Cap). Aus = voll.
    compact = region.toggle(
        t('asset_ai.compact_toggle'), value=True,
        key='_asset_ai_compact', help=t('asset_ai.compact_help'),
    )

    if region.button(t('asset_ai.run_button'), type='primary', key=f'_asset_ai_btn_{display_id}'):
        if df is None or df.empty:
            region.warning(t('asset_ai.no_data'))
            return
        # Sector rotation is expensive (~15s, ~12 ETF fetches) → compute only here on
        # click (cached market-wide per session), never on every tab rerun.
        with st.spinner(t('mv.spinner_ai')):
            sector_rotation = build_sector_rotation_text(asset_sector)
        prompt = build_single_asset_prompt(
            df, display_id, name, category, interval, period,
            asset_info=asset_info, market_status=market_status,
            signals=signals, seasonality=seasonality, sector_rotation=sector_rotation,
            news=news,
            parent_index=parent_index, indicators=indicators, sys_conf=sys_conf,
            market=market, compact=compact, freetext=freetext,
        )
        with st.spinner(t('mv.spinner_ai')):   # module-level: writes into the active tab context
            try:
                client   = AiClient(username=username)
                analysis = client.run_question(prompt, max_tokens=2800, groq_brevity=True)
                st.session_state[result_key] = {
                    'analysis':     analysis,
                    'model':        client.model_used,
                    'provider_log': client.provider_log,
                    'prompt':       prompt,
                    'ts':           datetime.now().strftime('%d.%m.%Y %H:%M'),
                }
                cached = st.session_state[result_key]
            except AiRateLimitError as exc:
                region.error(t('mv.err_ratelimit', error=exc))
                return
            except AiProviderError as exc:
                region.error(t('mv.err_provider', error=exc))
                return
            except Exception as exc:
                logger.exception("asset_ai: unexpected AI error")
                region.error(t('mv.err_unexpected', error=exc))
                return

    if cached:
        region.markdown("---")
        provider_log = cached.get('provider_log', [])
        if provider_log:
            parts = []
            for e in provider_log:
                if e['status'] == 'ok':
                    parts.append(f"✅ **{e['provider']}** · `{e['model']}`")
                else:
                    err = (e.get('error') or '')[:80]
                    parts.append(f"❌ ~~{e['provider']}~~ — {err}")
            region.markdown("  →  ".join(parts))
        region.markdown(t('asset_ai.result_header', ts=cached.get('ts', '?')))
        region.markdown(cached.get('analysis', ''))
        # Write to the expander's own handle — bare region.* would target the tab, not
        # the expander, so the prompt would spill out below it.
        _exp = region.expander(t('asset_ai.prompt_debug'), expanded=False)
        _p = cached.get('prompt', '')
        _exp.caption(t('asset_ai.prompt_length', chars=f"{len(_p):,}", tokens=f"{len(_p)//4:,}"))
        _exp.code(_p, language='text')


# ── Streamlit-Seite ────────────────────────────────────────────────────────────

class MarketOverviewPage:
    """3-Zustands-Seite:
    State 0 — leer:          Symbolliste + "Daten laden"-Button
    State 1 — _PREP_KEY:     Tabelle + Headlines + Debug-Expander + "An KI senden"-Button
    State 2 — _SESSION_KEY:  Tabelle + Headlines + Provider-Log + Analyse
    """

    def __init__(self, username: str = 'admin'):
        self.username = username
        self.sys_conf = sysconf.SystemConfig(username=username)

    # ── Haupt-Render ──────────────────────────────────────────────────────────

    def render(self):
        interval, period, indicators = _get_active_indicators(self.sys_conf)

        st.title(t('mv.title'))
        st.caption(t('mv.caption', interval=interval, period=period,
                     indicators=', '.join(indicators) if indicators else '—'))

        # Datenauswahl (immer sichtbar, vor dem Laden ausgewertet)
        sections, symbols, freetext = self._render_section_selector()

        prep   = st.session_state.get(_PREP_KEY)
        result = st.session_state.get(_SESSION_KEY)

        # Status-Info + Haupt-Button
        col_btn, col_status = st.columns([2, 4])
        with col_btn:
            load_btn = st.button(
                t('mv.load_data'), type="primary", use_container_width=True,
                help=t('mv.load_data_help'),
            )
        with col_status:
            if result:
                st.info(t('mv.status_last_analysis', ts=result.get('ts', '?')))
            elif prep:
                st.success(t('mv.status_prompt_ready'))
            else:
                st.info(t('mv.status_idle'))

        if load_btn:
            # Zustand komplett zurücksetzen, dann Step 1
            st.session_state.pop(_SESSION_KEY, None)
            st.session_state.pop(_PREP_KEY, None)
            self._run_fetch_and_build(interval, period, indicators, sections, symbols, freetext)
            return   # st.rerun() wurde inside aufgerufen

        st.markdown("---")

        if result:
            self._render_indicator_table(result['indicators_data'])
            self._render_headlines(result.get('headlines', {}))
            self._render_analysis(
                result['analysis'], result['model'], result['ts'],
                result.get('provider_log', []),
            )
        elif prep:
            self._render_indicator_table(prep['results'])
            self._render_headlines(prep.get('headlines', {}))
            self._render_debug(prep)
            st.markdown("")
            if st.button(t('mv.send_to_ai'), type="primary"):
                self._run_ai_call(prep)
        else:
            self._render_symbol_list(indicators, symbols)

    # ── Workflow Step 1: Daten + Prompt ───────────────────────────────────────

    def _run_fetch_and_build(self, interval: str, period: str, indicators: list,
                             sections: dict | None = None,
                             symbols: list | None = None,
                             freetext: str | None = None):
        """Lädt Daten, berechnet Indikatoren, holt Headlines, baut Prompt → _PREP_KEY."""
        active_symbols = symbols if symbols else list(_SYMBOLS)
        results = self._fetch_all(interval, period, indicators, active_symbols)

        with st.spinner(t('mv.spinner_sentiment')):
            headlines = _fetch_sentiment_headlines()

        prompt    = _build_market_prompt(
            results, active_symbols, interval, period, indicators,
            headlines, sections, freetext,
        )
        client    = AiClient(username=self.username)   # nur für Provider-Liste

        st.session_state[_PREP_KEY] = {
            'results':    results,
            'headlines':  headlines,
            'prompt':     prompt,
            'providers':  [p.name for p in client._providers],
            'interval':   interval,
            'period':     period,
            'indicators': indicators,
        }
        st.rerun()

    # ── Workflow Step 2: KI-Call ──────────────────────────────────────────────

    def _run_ai_call(self, prep: dict):
        """Sendet den fertigen Prompt an die KI und speichert das Ergebnis → _SESSION_KEY."""
        with st.spinner(t('mv.spinner_ai')):
            try:
                client   = AiClient(username=self.username)
                analysis = client.run_question(prep['prompt'], max_tokens=2800)
                ts       = datetime.now().strftime('%d.%m.%Y %H:%M')

                st.session_state[_SESSION_KEY] = {
                    'indicators_data': prep['results'],
                    'headlines':       prep.get('headlines', {}),
                    'analysis':        analysis,
                    'model':           client.model_used,
                    'provider_name':   client.provider_name,
                    'provider_log':    client.provider_log,
                    'ts':              ts,
                }
                st.session_state.pop(_PREP_KEY, None)
                st.rerun()

            except AiRateLimitError as exc:
                st.error(t('mv.err_ratelimit', error=exc))
            except AiProviderError as exc:
                st.error(t('mv.err_provider', error=exc))
            except Exception as exc:
                logger.exception("market_overview: unexpected AI error")
                st.error(t('mv.err_unexpected', error=exc))

    # ── Daten laden ───────────────────────────────────────────────────────────

    def _fetch_all(self, interval: str, period: str, indicators: list,
                   symbols: list | None = None) -> list[dict]:
        active = symbols if symbols else list(_SYMBOLS)
        results = []
        prog = st.progress(0, text=t('mv.progress_loading'))
        for i, (display_id, yf_ticker, name, cat) in enumerate(active):
            prog.progress(
                (i + 1) / len(active),
                text=t('mv.progress_load_one', id=display_id, name=name),
            )
            r = _compute_for_symbol(
                display_id, yf_ticker, interval, period, indicators, self.sys_conf
            )
            r['name']     = name
            r['category'] = cat
            results.append(r)
        prog.empty()
        return results

    # ── Darstellung ───────────────────────────────────────────────────────────

    def _render_section_selector(self) -> tuple[dict, list[tuple]]:
        """Zeigt Checkboxen für Daten-Abschnitte und Instrument-Auswahl.

        Gibt (sections_dict, symbols_list) zurück.
        Speichert beide Auswahlen in config.db.
        """
        # ── Abschnitt-Auswahl laden ────────────────────────────────────────────
        saved_sec = self.sys_conf.get_value(_CFG_SECTIONS_KEY, {})
        if not isinstance(saved_sec, dict):
            saved_sec = {}

        # ── Instrument-Pool aus DB + gespeicherte Auswahl ─────────────────────
        all_symbols   = _load_index_symbols() or list(_SYMBOLS)
        symbol_pool   = {s[0]: s for s in all_symbols}   # display_id → tuple

        saved_instr = self.sys_conf.get_value(_CFG_INSTRUMENTS_KEY, None)
        if isinstance(saved_instr, list) and saved_instr:
            selected_ids = [sid for sid in saved_instr if sid in symbol_pool]
        else:
            selected_ids = [sid for sid in _DEFAULT_INSTRUMENTS if sid in symbol_pool]

        with st.expander(t('mv.data_selection'), expanded=False):

            # ── Provider-Reihenfolge ──────────────────────────────────────────
            _all_providers  = ['claude', 'groq', 'github', 'gemini', 'ollama']
            _prov_labels    = {p: t(f'mv.prov.{p}') for p in _all_providers}
            from tradinglib.ai_client import merge_provider_order
            saved_order = self.sys_conf.get_value('ai_provider_order', _all_providers)
            if not isinstance(saved_order, list):
                saved_order = _all_providers
            # Bekannte Namen behalten; fehlende (z.B. neu: claude) an ihrer
            # Default-Position einfügen (claude → vorne, nicht ans Ende).
            saved_order = merge_provider_order(saved_order, _all_providers)

            st.markdown(t('mv.provider_order_head'))
            st.caption(t('mv.provider_order_caption'))
            new_order = st.multiselect(
                t('mv.provider_order_label'),
                options=_all_providers,
                default=saved_order,
                format_func=lambda p: _prov_labels.get(p, p),
                key='prov_order_select',
            )
            # Fehlende Provider ans Ende damit sie als Fallback noch greifen
            full_order = new_order + [p for p in _all_providers if p not in new_order]
            if full_order != saved_order:
                try:
                    self.sys_conf.set_value('ai_provider_order', full_order)
                except Exception as exc:
                    logger.warning("Provider-Reihenfolge konnte nicht gespeichert werden: %s", exc)

            st.markdown("---")

            # ── Instrumente ───────────────────────────────────────────────────
            st.markdown(t('mv.instruments_head'))
            # Sortiert nach Kategorie für bessere Übersicht
            by_cat: dict[str, list] = {}
            for sym in all_symbols:
                by_cat.setdefault(sym[3], []).append(sym)

            new_selected_ids: list[str] = []
            cat_order = ['Angstbarometer', 'Zins USA', 'USA', 'Europa', 'Japan',
                         'Asien', 'Australien', 'Metalle', 'Energie', 'Agrar',
                         'Währung', 'Krypto', 'Sonstiges', 'Index']
            sorted_cats = sorted(by_cat.keys(),
                                 key=lambda c: cat_order.index(c) if c in cat_order else 99)

            cols = st.columns(4)
            col_idx = 0
            for cat in sorted_cats:
                for sym in by_cat[cat]:
                    display_id = sym[0]
                    label = f"{display_id} · {sym[2]}"
                    locked = display_id in _CORR_LOCKED_IDS
                    with cols[col_idx % 4]:
                        # Correlation-Index instruments are permanently on: shown
                        # checked + disabled (greyed) and always included below.
                        checked = st.checkbox(
                            f"🔒 {label}" if locked else label,
                            value=True if locked else (display_id in selected_ids),
                            disabled=locked,
                            key=f'instr_{display_id}',
                            help=t('mv.corr_locked_help') if locked else None,
                        )
                    if checked or locked:
                        new_selected_ids.append(display_id)
                    col_idx += 1

            st.caption(t('mv.corr_locked_caption'))
            st.markdown("---")

            # ── Daten-Abschnitte ──────────────────────────────────────────────
            st.caption(t('mv.mandatory_caption'))
            st.markdown(t('mv.optional_head'))
            sec_cols = st.columns(3)
            selections: dict[str, bool] = {}
            for i, key in enumerate(_SECTION_DEFAULTS):
                _label, default = _SECTION_DEFAULTS[key]
                with sec_cols[i % 3]:
                    selections[key] = st.checkbox(
                        t(f'mv.section.{key}'),
                        value=saved_sec.get(key, default),
                        key=f'sec_{key}',
                    )

        st.markdown("---")

        # ── Freitextfeld ──────────────────────────────────────────────────────
        saved_freetext = self.sys_conf.get_value(_CFG_FREETEXT_KEY, '') or ''
        if not isinstance(saved_freetext, str):
            saved_freetext = ''
        st.markdown(t('mv.freetext_head'))
        st.caption(t('mv.freetext_caption'))
        new_freetext = st.text_area(
            t('mv.freetext_label'),
            value=saved_freetext,
            height=100,
            placeholder=t('mv.freetext_placeholder'),
            key='sec_freetext',
            label_visibility='collapsed',
        )

        # ── Speichern ─────────────────────────────────────────────────────────
        if selections != saved_sec:
            try:
                self.sys_conf.set_value(_CFG_SECTIONS_KEY, selections)
            except Exception as exc:
                logger.warning("Abschnitt-Auswahl konnte nicht gespeichert werden: %s", exc)

        if new_selected_ids != selected_ids:
            try:
                self.sys_conf.set_value(_CFG_INSTRUMENTS_KEY, new_selected_ids)
            except Exception as exc:
                logger.warning("Instrument-Auswahl konnte nicht gespeichert werden: %s", exc)

        if new_freetext != saved_freetext:
            try:
                self.sys_conf.set_value(_CFG_FREETEXT_KEY, new_freetext)
            except Exception as exc:
                logger.warning("Freitext konnte nicht gespeichert werden: %s", exc)

        # Symbole in richtiger Reihenfolge zurückgeben
        selected_symbols = [symbol_pool[sid] for sid in new_selected_ids if sid in symbol_pool]
        return selections, selected_symbols, new_freetext

    def _render_symbol_list(self, indicators: list, symbols: list | None = None):
        active = symbols if symbols else list(_SYMBOLS)
        st.markdown(t('mv.instruments_to_analyse', n=len(active)))
        rows = [{t('mv.col_symbol'): s[0], t('mv.col_name'): s[2],
                 t('mv.col_category'): _cat(s[3])} for s in active]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if indicators:
            st.markdown(t('mv.active_indicators', indicators=', '.join(indicators)))

    def _render_indicator_table(self, results: list[dict]):
        _regime_map = {0.0: t('mv.regime_sideways'), 1.0: t('mv.regime_bull'),
                       2.0: t('mv.regime_bear')}
        c_sym, c_name = t('mv.col_symbol'), t('mv.col_name')
        c_price, c_date = t('mv.col_price'), t('mv.col_date')
        compact_rows = []
        for r in results:
            ind       = r.get('indicator_values', {})
            week_str  = f"{r['week_ret']:+.1f}%"  if r.get('week_ret')  is not None else '-'
            month_str = f"{r['month_ret']:+.1f}%" if r.get('month_ret') is not None else '-'

            if r.get('error'):
                compact_rows.append({
                    c_sym: r['ticker'], c_name: r.get('name', ''),
                    c_price: t('mv.val_error'), '1W %': '-', '1M %': '-',
                    'Markov': '-', 'HA': '-', 'MACD H.': '-', 'EWO': '-',
                    'PVT-Komp.': '-', c_date: '-',
                })
                continue

            regime_raw = ind.get('markov_regime')
            regime     = _regime_map.get(regime_raw,
                         str(int(regime_raw)) if regime_raw is not None else '-')

            ha_close = ind.get('ha_close')
            ha_open  = ind.get('ha_open')
            ha_low   = ind.get('ha_ema_low')
            if ha_close is not None and ha_open is not None:
                ha_signal = (t('mv.ha_bear_lowband') if (ha_low and ha_close < ha_low)
                             else t('mv.ha_bull') if ha_close > ha_open else t('mv.ha_bear'))
            else:
                ha_signal = '-'

            macd_hist = ind.get('macd_diff')
            ewo       = ind.get('ewo')
            pvm       = _pivot_compression(ind, r['close'])

            compact_rows.append({
                c_sym:       r['ticker'],
                c_name:      r.get('name', ''),
                c_price:     r['close'],
                '1W %':      week_str,
                '1M %':      month_str,
                'Markov':    regime,
                'HA':        ha_signal,
                'MACD H.':   f"{macd_hist:+.4f}" if macd_hist is not None else '-',
                'EWO':       f"{ewo:+.4f}"        if ewo       is not None else '-',
                'PVT-Komp.': (f"{pvm['compression_pct']}% {'⚠' if pvm['compressed'] else ''}"
                              if pvm else '-'),
                c_date:      r.get('datum', '-'),
            })

        st.dataframe(pd.DataFrame(compact_rows), hide_index=True, use_container_width=True)

        with st.expander(t('mv.raw_values'), expanded=False):
            for r in results:
                if r.get('error') or not r.get('indicator_values'):
                    continue
                st.markdown(f"**{r['ticker']} — {r.get('name', '')}**")
                rows = [{t('mv.col_indicator'): k, t('mv.col_value'): v,
                         t('mv.col_hint'): _INDICATOR_HINTS.get(k, '')}
                        for k, v in r['indicator_values'].items()]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=False)

    def _render_headlines(self, headlines: dict):
        if not headlines or not any(bool(v) for v in headlines.values()):
            return
        _meta  = {s[0]: s[2] for s in _SYMBOLS}
        _color = {'positiv': '#27ae60', 'negativ': '#e74c3c', 'neutral': '#7f8c8d'}
        with st.expander(t('mv.headlines'), expanded=False):
            for ticker, articles in headlines.items():
                if not articles:
                    continue
                st.markdown(f"**{ticker} — {_meta.get(ticker, ticker)}**")
                for a in articles:
                    col = _color.get(a['sentiment'], '#7f8c8d')
                    st.markdown(
                        f"<span style='color:{col}'>●</span>&nbsp;"
                        f"[{a['title']}]({a['link']})&nbsp;&nbsp;"
                        f"<small style='color:gray'>Score: {a['compound']:+.2f}"
                        f" · {a['published']}</small>",
                        unsafe_allow_html=True,
                    )
                st.markdown("")

    def _render_debug(self, prep: dict):
        """Zeigt Prompt + Provider-Reihenfolge BEVOR der KI-Call geht (State 1)."""
        with st.expander(t('mv.debug_title'), expanded=True):
            providers = prep.get('providers', [])
            st.markdown(t('mv.provider_order_tried'))
            for i, name in enumerate(providers, 1):
                st.markdown(f"&nbsp;&nbsp;{i}. `{name}`", unsafe_allow_html=True)
            prompt = prep.get('prompt', '')
            st.markdown(t('mv.prompt_length', chars=f"{len(prompt):,}",
                          tokens=f"{len(prompt)//4:,}"))
            st.code(prompt, language="text")

    def _render_analysis(self, analysis: str, model: str, ts: str,
                         provider_log: list | None = None):
        """Zeigt Provider-Log (welcher Provider erfolgreich war / welche scheiterten)
        und anschließend den vollständigen Analysetext."""
        st.markdown("---")

        # Provider-Log: zeigt was wirklich passiert ist
        if provider_log:
            parts = []
            for e in provider_log:
                if e['status'] == 'ok':
                    parts.append(f"✅ **{e['provider']}** · `{e['model']}`")
                else:
                    err = (e.get('error') or '')[:80]
                    parts.append(f"❌ ~~{e['provider']}~~ — {err}")
            st.markdown("  →  ".join(parts))

        st.markdown(t('mv.ai_analysis', ts=ts))
        st.markdown(analysis)
