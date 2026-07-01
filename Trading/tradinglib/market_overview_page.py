"""
market_overview_page.py — Globale Marktübersicht mit KI-Analyse.

Analysiert 8 globale Marktindikatoren via FetchData (dieselbe Pipeline wie in
den Charts), liest die aktiven Indikatoren aus der Benutzer-Konfiguration
(Overlays + Oszillatoren aus system_config) und sendet die berechneten Werte
an Groq/Gemini für eine KI-Markteinschätzung.
"""
import logging
from datetime import datetime

import pandas as pd
import streamlit as st

from tradinglib.ai_client import AiClient, AiRateLimitError, AiProviderError
from tradinglib import system_config as sysconf

logger = logging.getLogger(__name__)

# ── Symbole ────────────────────────────────────────────────────────────────────
# (display_id, yf_ticker, langer_name, kategorie)

# Standardauswahl (Fallback wenn DB nicht verfügbar oder nichts gespeichert)
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

_SESSION_KEY = 'market_overview_result'   # State 3: AI-Antwort vorhanden
_PREP_KEY    = 'market_overview_prep'      # State 2: Daten + Prompt bereit, wartet auf Senden
_CFG_INSTRUMENTS_KEY = 'market_overview_instruments'  # gespeicherte Instrument-Auswahl

# Anzeigenamen für bekannte Indizes (display_id → (langer_name, kategorie))
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
    'DX=F':     ('US Dollar Index',          'Sonstiges'),
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

# yfinance-Ticker weicht vom Display-Ticker ab
# DX=F (US-Dollar-Index-Future) liefert über Yahoo keine Daten mehr (404/delisted);
# der ICE-Spot-Index DX-Y.NYB liefert dieselbe Größe und funktioniert.
_YF_TICKER_MAP: dict[str, str] = {'^SPX': '^GSPC', 'DX=F': 'DX-Y.NYB'}

# Symbole die immer im Pool sind (unabhängig von yf_tickers.db)
_EXTRA_SYMBOLS = [
    # Angstbarometer / Zinsen
    '^VIX', '^TNX',
    # Metalle
    'GC=F', 'SI=F', 'HG=F', 'PL=F', 'PA=F',
    # Energie
    'BZ=F', 'CL=F', 'NG=F', 'RB=F', 'HO=F',
    # Agrar
    'ZW=F', 'ZC=F', 'ZS=F', 'KC=F', 'CC=F', 'SB=F', 'CT=F',
    # Krypto
    'BTC-EUR', 'ETH-EUR',
    # Sonstiges
    'DX=F', 'LE=F',
    # Währungen
    'EURUSD=X', 'GBPUSD=X', 'USDJPY=X', 'USDCHF=X',
    'AUDUSD=X', 'USDCAD=X', 'EURGBP=X', 'EURJPY=X', 'USDCNY=X',
]

# Standard-Aktivierung beim ersten Aufruf
_DEFAULT_INSTRUMENTS = ['^VIX', '^TNX', '^HSI', '^N225', '^GDAXI', '^SPX', 'GC=F', 'BTC-EUR']


def _load_index_symbols() -> list[tuple[str, str, str, str]]:
    """Liest die indices-Tabelle aus yf_tickers.db und gibt
    [(display_id, yf_ticker, name, kategorie), ...] zurück.

    Berücksichtigt die TradingDB-Umgebungsvariable für den DB-Pfad.
    Gibt bei Fehlern eine leere Liste zurück.
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

    # Extra-Symbole anhängen (GC=F, BTC-EUR, VIX, TNX) wenn noch nicht drin
    existing = {r[0] for r in result}
    for sym in _EXTRA_SYMBOLS:
        if sym not in existing:
            name, cat = _INDEX_NAMES.get(sym, (sym, ''))
            result.append((sym, _YF_TICKER_MAP.get(sym, sym), name, cat))

    return result

# Spalten, die nicht als Indikatoren gelten und aus dem Prompt rausgefiltert werden
_SKIP_COLS = frozenset({
    'Open', 'High', 'Low', 'Close', 'Volume', 'Adj Close',
    'buy_close', 'sell_close', 'crosszero', 'position',
    'daily_returns', 'log_return', 'index', 'level_0',
})

# Interpretation-Hinweise für den KI-Prompt
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
    # 'bar' = reines Plotly-Rendering, erzeugt keine Datenspalten → weglassen
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


# ── Trend-Snippets ─────────────────────────────────────────────────────────────

# Kandidaten für numerische Trend-Snippets (in Prioritätsreihenfolge)
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

    # Perioden-Label für Prompt
    is_daily = interval in ('1d', '3d', '1wk')
    lbl_10 = 'vor 10 HT' if is_daily else f'vor 10×{interval}'
    lbl_5  = 'vor 5 HT'  if is_daily else f'vor 5×{interval}'

    p10 = df.iloc[max(0, n - 11)]
    p5  = df.iloc[max(0, n - 6)]
    p0  = df.iloc[-1]

    snippets: dict[str, str] = {}

    # 1. Numerische Schlüssel-Indikatoren
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

    # 2. Heikin-Ashi Richtung (abgeleitet)
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

    # 3. Pivot-Kompression über Zeit (nur wenn pvt-Werte dynamisch wären — pvt ist statisch,
    #    daher überspringen; Kompression steht bereits im ind_values-Block)

    return snippets


def _compute_extended_snippets(df: pd.DataFrame, interval: str) -> dict[str, str]:
    """Langzeit-Trend-Snippets: 200 Perioden → 50 Perioden → heute.

    Erfordert ausreichend Datenpunkte (≥ 55 für 50P, ≥ 205 für 200P).
    Bei unzureichenden Daten werden nur verfügbare Zeitpunkte gezeigt.
    """
    n = len(df)
    if n < 12:
        return {}

    is_daily  = interval in ('1d', '3d', '1wk')
    lbl_200   = 'vor 200 HT' if is_daily else f'vor 200×{interval}'
    lbl_50    = 'vor 50 HT'  if is_daily else f'vor 50×{interval}'

    # Verfügbare Zeitpunkte
    has_200 = n >= 205
    has_50  = n >= 55

    if not has_50:
        return {}   # Zu wenig Daten

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

    # Heikin-Ashi Richtung
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
    # Mindestens bars_back + 5 Zeilen nötig; sonst kein sinnvoller Vergleich
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

        close = df['Close'].dropna()
        if close.empty:
            return {'ticker': display_id, 'error': 'Keine Kursdaten'}

        # Datum aus Index lesen
        last_date = str(df.index[-1])[:10]
        last_close = round(float(close.iloc[-1]), 4)

        week_ret  = round((float(close.iloc[-1]) / float(close.iloc[-6])  - 1) * 100, 2) if len(close) >= 6  else None
        month_ret = round((float(close.iloc[-1]) / float(close.iloc[-22]) - 1) * 100, 2) if len(close) >= 22 else None

        ind = _extract_indicator_values(df)
        _ensure_core_indicators(df, ind)   # RSI + ATR immer verfügbar

        return {
            'ticker':           display_id,
            'datum':            last_date,
            'close':            last_close,
            'week_ret':         week_ret,
            'month_ret':        month_ret,
            'indicator_values':   ind,
            'trend_snippets':     _compute_trend_snippets(df, interval),
            'extended_snippets':  _compute_extended_snippets(df, interval),
            'snapshot_4w':        _compute_4weeks_snapshot(df, interval, last_close),
            'interval':           interval,
        }

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
    _atr_cols = ('atr14', 'atr', 'bos_atr', 'mmm_atr')
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


def _build_market_prompt(
    results: list[dict], symbol_meta: list[tuple],
    interval: str, period: str, indicators: list,
    headlines: dict | None = None,
    sections: dict | None = None,
    freetext: str | None = None,
) -> str:
    """Multi-Asset Regime & Risk Model — hierarchisch strukturierter Analyse-Prompt."""
    meta    = {s[0]: (s[2], s[3]) for s in symbol_meta}
    today   = datetime.now().strftime('%d.%m.%Y')
    _regime = {0.0: 'Seitwärts', 1.0: 'Bullenmarkt', 2.0: 'Bärenmarkt'}
    inc     = sections or {}   # Abkürzung für bedingte Includes

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

    lines = [
        "Du bist ein quantitativer Multi-Asset-Analyst.",
        "Analysiere die folgenden Märkte STRIKT DATENBASIERT — keine narrative Vereinheitlichung.",
        f"Auswertungsdatum: {today}  |  Intervall: {interval}  |  Zeitraum: {period}",
        count_line,
        "Regeln:",
        f"  • Werte ALLE {n_total} ausgewählten Märkte aus. Triff KEINE eigene Vorauswahl "
        "und begrenze die Anzahl NICHT — die Auswahl ist bereits vom Nutzer getroffen.",
        "  • Verwende NUR die gelieferten Daten. Keine externen Annahmen.",
        '  • Widersprüchliche Indikatoren → als "KONFLIKT" markieren, NICHT auflösen.',
        "  • Hierarchie: [1-TREND] > [2-MOMENTUM] > [3-VOLATILITÄT] > [4-MAKRO] > [5-OPTIONAL]",
        "  • Gib KEINE Anlageberatung — nur Datenanalyse"
        + (" (Ausnahme: Abschnitt DEPOT-PROFIL am Ende)." if inc.get('depot_55plus', True) else "."),
        "",
        "════════════════════════════════════════",
        "MARKTDATEN",
        "════════════════════════════════════════",
        "",
    ]

    for r in results:
        ticker = r['ticker']
        name, cat = meta.get(ticker, (ticker, ''))

        if r.get('error'):
            lines += [f"─── {ticker} | {name} ───", f"FEHLER: {r['error']}", ""]
            continue

        close     = r['close']
        ind       = r.get('indicator_values', {})
        week_str  = f"{r['week_ret']:+.1f}%"  if r['week_ret']  is not None else "n/a"
        month_str = f"{r['month_ret']:+.1f}%" if r['month_ret'] is not None else "n/a"

        lines += [
            f"─── {ticker} | {name} | {cat} ───",
            f"Kurs: {close}  |  Stand: {r['datum']}  |  1W: {week_str}  |  1M: {month_str}",
        ]

        # ── [1-TREND] MA-Positionierung ───────────────────────────────────────
        lines.append("[1-TREND — MA-Positionierung → Hauptregime]")
        ma_found = False
        for label, col in [('SMA20', 'sma20'), ('SMA50', 'sma50'), ('SMA200', 'sma200')]:
            v = ind.get(col)
            if v and close:
                pct = (close - v) / v * 100
                pos = 'ÜBER' if pct > 0 else 'UNTER'
                lines.append(f"  {label}: {v}  → Kurs {abs(pct):.1f}% {pos} {label}")
                ma_found = True
        if not ma_found:
            lines.append("  MA-Daten: nicht verfügbar")

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
               ind.get('bos_atr') or ind.get('mmm_atr'))
        if atr and close:
            pct = atr / close * 100
            lines.append(f"  ATR(14): {atr}  ({pct:.2f}% des Kurses)")
        else:
            lines.append("  ATR: nicht verfügbar")

        # ── [4-MAKRO] Zinskontext ─────────────────────────────────────────────
        # TNX ist als eigenes Asset in den Daten — kein doppelter Block nötig.
        # Für alle anderen Assets: Hinweis dass TNX-Kontext aus dem TNX-Block kommt.
        if ticker != '^TNX':
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

    # ── Analyse-Aufgabe ────────────────────────────────────────────────────────
    lines += [
        "════════════════════════════════════════",
        "ANALYSE-AUFGABE",
        "════════════════════════════════════════",
        "",
        f"Schreibe für JEDES der {n_total} ausgewählten Assets exakt dieses Format",
        "(keine Abweichungen, keine zusätzlichen Abschnitte pro Asset).",
        "Assets mit FEHLER/ohne Daten: nur den ASSET-Kopf + 'keine Daten verfügbar' notieren,",
        "nicht aus der Analyse streichen:",
        "",
        "Klassifikations-Schwellen (VERBINDLICH — nutze exakt diese Grenzen, erfinde keine eigenen):",
        "  Trend:       bull = Kurs über SMA50 UND SMA200  |  bear = Kurs unter SMA50 UND SMA200  |",
        "               sideways = gemischt (Kurs über die eine, unter die andere MA)",
        "  Momentum (RSI 14):  stark = RSI ≥ 60  |  neutral = 40 ≤ RSI < 60  |  schwach = RSI < 40.",
        "               MACD-Hist-Vorzeichen bestätigt (+ = bullisch, − = bearisch); widerspricht es",
        "               der RSI-Einstufung → 'neutral' UND als KONFLIKT vermerken.",
        "  Volatilität (ATR % des Kurses):  niedrig < 1.5%  |  normal 1.5–3.0%  |  hoch > 3.0%",
        "",
        "ASSET: [ticker | name]",
        "Trend:       [bull / bear / sideways] — Begründung: welche MAs bestätigen?",
        "Momentum:    [stark / neutral / schwach] — Begründung: RSI-Wert + MACD-Hist-Vorzeichen",
        "Volatilität: [hoch / normal / niedrig] — Begründung: ATR-% des Kurses",
        "Regime:      [risk-on / risk-off / neutral] — NUR wenn Trend+Momentum+Volatilität",
        "             EINDEUTIG in DIESELBE Richtung zeigen. Sonst: 'unklar'",
        "Konflikte:   [liste jeden Widerspruch zwischen [1]–[5] explizit auf.",
        "             Beispiel: 'RSI neutral vs. MACD bullisch' oder 'keine']",
        "Kurzfazit:   [1–2 Sätze — ausschließlich aus den gelieferten Daten ableitbar]",
    ]

    if inc.get('global_summary', True):
        lines += [
            "",
            "─────────────────────────────────────────",
            "GLOBAL SUMMARY",
            "─────────────────────────────────────────",
            f"Risk-Modus:    [risk-on / neutral / risk-off]",
            "               Assets (mit Daten) dasselbe Regime zeigen. Sonst: 'gemischt'",
            "Haupttreiber:  [max. 3 Faktoren aus den Daten — konkrete Werte nennen]",
            "Widersprüche:  [Asset-Kombinationen die gegenläufige Signale zeigen]",
            "Confidence:    [0–100] — 0=maximal widersprüchlich, 100=alle Signale konsistent",
            "               Begründung der Confidence in 1 Satz.",
        ]

    if inc.get('trend_compare', True):
        lines += [
            "",
            "─────────────────────────────────────────",
            "TRENDVERGLEICH VOR 4 WOCHEN",
            "─────────────────────────────────────────",
            "Nutze die [VERGLEICH VOR 4 WOCHEN]-Blöcke pro Asset.",
            "Welche Regime-Wechsel oder Vorzeichenwechsel sind seit vor 4 Wochen aufgetreten?",
            "Richtungsänderung der Allocation (kein Ratschlag, nur Richtung aus den Daten):",
            "  Beispiel: 'Aktienquote reduzieren: SPX+GDAXI Markov Bull→Bear + MACD dreht negativ'",
            "            'Gold erhöhen: VIX-Kompression + RSI überverkauft'",
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
    'global_summary':     ('Global Summary + Risk Score',             True),
    'trend_compare':      ('Trendvergleich & Umschichtung',           True),
    'depot_55plus':       ('Depot-Profil 55+',                        True),
}


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

        st.title("Globale Marktübersicht")
        st.caption(
            f"Intervall: **{interval}** · Zeitraum: **{period}** · "
            f"Indikatoren: **{', '.join(indicators) if indicators else '—'}**"
        )

        # Datenauswahl (immer sichtbar, vor dem Laden ausgewertet)
        sections, symbols, freetext = self._render_section_selector()

        prep   = st.session_state.get(_PREP_KEY)
        result = st.session_state.get(_SESSION_KEY)

        # Status-Info + Haupt-Button
        col_btn, col_status = st.columns([2, 4])
        with col_btn:
            load_btn = st.button(
                "Daten laden", type="primary", use_container_width=True,
                help="Marktdaten + Indikatoren abrufen und Prompt bauen",
            )
        with col_status:
            if result:
                st.info(f"Letzte Analyse: {result.get('ts', '?')} — "
                        "'Daten laden' startet von vorne.")
            elif prep:
                st.success("Prompt bereit — prüfe Debug-Ausgabe und klicke 'An KI senden'.")
            else:
                st.info("Startet Datenabruf + Indikator-Berechnung + Prompt-Erstellung.")

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
            if st.button("An KI senden", type="primary"):
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

        with st.spinner("Nachrichten-Sentiment wird abgerufen …"):
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
        with st.spinner("KI analysiert Marktdaten …"):
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
                st.error(f"KI-Rate-Limit — alle Provider erschöpft:\n\n{exc}")
            except AiProviderError as exc:
                st.error(f"KI-Provider-Fehler:\n\n{exc}")
            except Exception as exc:
                logger.exception("market_overview: unexpected AI error")
                st.error(f"Unerwarteter Fehler: {exc}")

    # ── Daten laden ───────────────────────────────────────────────────────────

    def _fetch_all(self, interval: str, period: str, indicators: list,
                   symbols: list | None = None) -> list[dict]:
        active = symbols if symbols else list(_SYMBOLS)
        results = []
        prog = st.progress(0, text="Marktdaten werden geladen …")
        for i, (display_id, yf_ticker, name, cat) in enumerate(active):
            prog.progress(
                (i + 1) / len(active),
                text=f"Lade {display_id} ({name}) …",
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

        with st.expander("⚙ Datenauswahl für KI-Prompt", expanded=False):

            # ── Provider-Reihenfolge ──────────────────────────────────────────
            _all_providers  = ['groq', 'github', 'gemini', 'ollama']
            _prov_labels    = {
                'groq':   'Groq  (llama-4-scout · kostenlos · schnell)',
                'github': 'GitHub Models  (gpt-4o-mini · kostenlos · GPT-Qualität)',
                'gemini': 'Gemini  (flash-lite · kostenlos)',
                'ollama': 'Ollama  (lokal)',
            }
            saved_order = self.sys_conf.get_value('ai_provider_order', _all_providers)
            if not isinstance(saved_order, list):
                saved_order = _all_providers
            # Nur bekannte Namen; fehlende ans Ende
            saved_order = [p for p in saved_order if p in _all_providers] + \
                          [p for p in _all_providers if p not in saved_order]

            st.markdown("**KI-Provider Reihenfolge:**")
            st.caption(
                "Auswahl bestimmt die Priorität — erster Provider wird zuerst versucht. "
                "Nicht konfigurierte Provider (kein API-Key) werden übersprungen."
            )
            new_order = st.multiselect(
                "Reihenfolge (oberster = höchste Priorität):",
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
            st.markdown("**Instrumente:**")
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
                    with cols[col_idx % 4]:
                        checked = st.checkbox(
                            label,
                            value=(display_id in selected_ids),
                            key=f'instr_{display_id}',
                        )
                    if checked:
                        new_selected_ids.append(display_id)
                    col_idx += 1

            st.markdown("---")

            # ── Daten-Abschnitte ──────────────────────────────────────────────
            st.caption(
                "**Pflicht (immer aktiv):** Trend (SMA20/50/200) · Momentum (RSI + MACD) · "
                "Volatilität (ATR)"
            )
            st.markdown("**Optionale Daten & Analyse-Abschnitte:**")
            sec_cols = st.columns(3)
            selections: dict[str, bool] = {}
            for i, key in enumerate(_SECTION_DEFAULTS):
                label, default = _SECTION_DEFAULTS[key]
                with sec_cols[i % 3]:
                    selections[key] = st.checkbox(
                        label,
                        value=saved_sec.get(key, default),
                        key=f'sec_{key}',
                    )

        st.markdown("---")

        # ── Freitextfeld ──────────────────────────────────────────────────────
        saved_freetext = self.sys_conf.get_value(_CFG_FREETEXT_KEY, '') or ''
        if not isinstance(saved_freetext, str):
            saved_freetext = ''
        st.markdown("**Individuelle Zusatzfragen / Anweisungen:**")
        st.caption(
            "Dieser Text wird direkt ans Ende des Prompts angehängt. "
            "Beispiel: *'Berechne das Sharpe-Ratio basierend auf den Volatilitätsdaten.'* "
            "oder *'Vergleiche VIX mit dem historischen Durchschnitt von 20.'*"
        )
        new_freetext = st.text_area(
            "Freitext (optional):",
            value=saved_freetext,
            height=100,
            placeholder="Eigene Fragen oder Anweisungen an die KI …",
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
        st.markdown(f"**Zu analysierende Instrumente ({len(active)}):**")
        rows = [{'Symbol': s[0], 'Name': s[2], 'Kategorie': s[3]} for s in active]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if indicators:
            st.markdown(f"**Aktive Indikatoren:** {', '.join(indicators)}")

    def _render_indicator_table(self, results: list[dict]):
        _regime_map = {0.0: 'Seitwärts', 1.0: 'Bullen', 2.0: 'Bären'}
        compact_rows = []
        for r in results:
            ind       = r.get('indicator_values', {})
            week_str  = f"{r['week_ret']:+.1f}%"  if r.get('week_ret')  is not None else '-'
            month_str = f"{r['month_ret']:+.1f}%" if r.get('month_ret') is not None else '-'

            if r.get('error'):
                compact_rows.append({
                    'Symbol': r['ticker'], 'Name': r.get('name', ''),
                    'Kurs': 'Fehler', '1W %': '-', '1M %': '-',
                    'Markov': '-', 'HA': '-', 'MACD H.': '-', 'EWO': '-',
                    'PVT-Komp.': '-', 'Stand': '-',
                })
                continue

            regime_raw = ind.get('markov_regime')
            regime     = _regime_map.get(regime_raw,
                         str(int(regime_raw)) if regime_raw is not None else '-')

            ha_close = ind.get('ha_close')
            ha_open  = ind.get('ha_open')
            ha_low   = ind.get('ha_ema_low')
            if ha_close is not None and ha_open is not None:
                ha_signal = ('Bear (u.Band)' if (ha_low and ha_close < ha_low)
                             else 'Bull' if ha_close > ha_open else 'Bear')
            else:
                ha_signal = '-'

            macd_hist = ind.get('macd_diff')
            ewo       = ind.get('ewo')
            pvm       = _pivot_compression(ind, r['close'])

            compact_rows.append({
                'Symbol':    r['ticker'],
                'Name':      r.get('name', ''),
                'Kurs':      r['close'],
                '1W %':      week_str,
                '1M %':      month_str,
                'Markov':    regime,
                'HA':        ha_signal,
                'MACD H.':   f"{macd_hist:+.4f}" if macd_hist is not None else '-',
                'EWO':       f"{ewo:+.4f}"        if ewo       is not None else '-',
                'PVT-Komp.': (f"{pvm['compression_pct']}% {'⚠' if pvm['compressed'] else ''}"
                              if pvm else '-'),
                'Stand':     r.get('datum', '-'),
            })

        st.dataframe(pd.DataFrame(compact_rows), hide_index=True, use_container_width=True)

        with st.expander("Alle Indikatorwerte (Rohdaten)", expanded=False):
            for r in results:
                if r.get('error') or not r.get('indicator_values'):
                    continue
                st.markdown(f"**{r['ticker']} — {r.get('name', '')}**")
                rows = [{'Indikator': k, 'Wert': v, 'Hinweis': _INDICATOR_HINTS.get(k, '')}
                        for k, v in r['indicator_values'].items()]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=False)

    def _render_headlines(self, headlines: dict):
        if not headlines or not any(bool(v) for v in headlines.values()):
            return
        _meta  = {s[0]: s[2] for s in _SYMBOLS}
        _color = {'positiv': '#27ae60', 'negativ': '#e74c3c', 'neutral': '#7f8c8d'}
        with st.expander("Aktuelle Schlagzeilen (Yahoo Finance RSS)", expanded=False):
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
        with st.expander("Debug: Prompt & Provider-Reihenfolge", expanded=True):
            providers = prep.get('providers', [])
            st.markdown("**Provider-Reihenfolge** (der Reihe nach versucht):")
            for i, name in enumerate(providers, 1):
                st.markdown(f"&nbsp;&nbsp;{i}. `{name}`", unsafe_allow_html=True)
            prompt = prep.get('prompt', '')
            st.markdown(
                f"**Prompt-Länge:** {len(prompt):,} Zeichen "
                f"≈ {len(prompt)//4:,} Tokens"
            )
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

        st.markdown(f"**KI-Marktanalyse** — Stand: {ts}")
        st.markdown(analysis)
