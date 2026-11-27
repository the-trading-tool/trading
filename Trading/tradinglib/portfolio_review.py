"""Portfolio review: fixed risk rules over the open positions of one depot.

Answers "what in this depot needs a look today" -- not "where is the market
going". Every finding comes from a named rule with a fixed threshold, so the
same depot always yields the same list and each row can say why it is there.

What the rules deliberately are, and are not:

* **Risk rules, not return forecasts.** Stops, concentration, the volatility
  band of the risk profile and each position's share of the depot risk are
  things a rule can keep. A market view is not: the timing signals that were
  measured had no usable edge (COT IC ~ 0, index edge total contrarian, market
  phase marker without edge), and the macro data was never measured at all, so
  the market context is shown for orientation and never raises a finding.
* **The profile band is per position.** ``vol_band`` was calibrated on single
  assets (p85 of the forward 21-bar volatility); a spread depot moves less than
  its parts, so the depot volatility is a plain number without a threshold.
* **One measured exclusion.** Two negative earnings surprises in a row
  (-1.5 pp against the market over 63 days, 0 of 7 years positive) is the only
  fundamental rule with a measured effect; the other quality red flags are
  snapshots and say so.

Sources (``SOURCES``): own trades (trades.db), paper (Alpaca), live (IBKR) and
the Multi Strategies run. The rules are the same; only where a stop comes from
differs -- the trailing-stop table of the risk tab for own trades
(``own_trades_trails``, system currency), the one of the trading page's
positions tab for paper/live (``position_trails`` in trading.db, listing
currency). The broker's own stop orders are not readable: ``get_orders`` of the
bridges returns neither order type nor stop price.

``review()`` is pure (frames and dicts in, a ``Review`` out) and is what the
tests exercise; the ``load_*`` helpers read the local databases read-only.
No Streamlit here.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SOURCES = ('own', 'paper', 'live', 'multi')
BROKER_OF = {'paper': 'alpaca', 'live': 'ibkr'}

# Thresholds of the rules. Risk policy, not measured return edges -- editable in
# the tab's settings and stored per user under CONFIG_KEY.
THRESHOLDS = {
    'position_pct': 20.0,       # single position above this share of the depot
    'sector_pct': 35.0,         # one sector above this share
    'currency_pct': 60.0,       # one listing currency above this share
    'risk_share_factor': 2.0,   # risk share above this multiple of the weight ...
    'risk_share_min': 10.0,     # ... and at least this share of the depot risk
}
CONFIG_KEY = 'portfolio_review'

VOL_DAYS = 21               # same horizon as the stored ``vola`` and the profile bands
COV_DAYS = 90               # window of the covariance behind the risk shares
COV_MIN_OBS = 40            # fewer returns than this -> left out of the risk split
GAP_DAYS = 120              # price gaps younger than this distort vol and risk
STALE_DAYS = 5              # last bar this many days behind the newest position
TRAIL_STALE_DAYS = 3        # stored trailing stops older than this
TRADING_DAYS = 252

LEVELS = ('check', 'note', 'data')          # Prüfen / Hinweis / Daten
_CCY_ALIAS = {'GBp': 'GBP', 'GBX': 'GBP', '': '?'}   # pence listings count as pounds
QUALITY_FLAGS = ('leverage', 'interest', 'cash_burn', 'losses')   # 'surprise' has its own rule


@dataclass
class Finding:
    level: str                  # one of LEVELS
    rule: str                   # i18n key suffix: pr.rule.<rule>
    ticker: str = ''            # '' = depot-wide
    params: dict = field(default_factory=dict)

    def text(self, lang_dict: dict | None = None) -> str:
        """The finding as text, from *lang_dict* (a locale) or the active language."""
        key = f'pr.rule.{self.rule}'
        if lang_dict is not None:
            tmpl = lang_dict.get(key, key)
            try:
                return tmpl.format(**self.params)
            except (KeyError, ValueError, IndexError):
                return tmpl
        from tradinglib.i18n import t
        return t(key, **self.params)


@dataclass
class Review:
    positions: pd.DataFrame                     # one row per ticker, see POSITION_COLUMNS
    findings: list[Finding]
    metrics: dict
    sectors: pd.Series                          # weight % per sector
    currencies: pd.Series                       # weight % per listing currency
    profile_name: str = ''


POSITION_COLUMNS = ['ticker', 'longName', 'sector', 'currency', 'shares', 'positionValue',
                    'gainPct', 'weight', 'vol', 'risk_share', 'stop', 'dist_stop',
                    'signal', 'flags']


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _close(prices) -> pd.Series:
    """Close series with a DatetimeIndex from a daily frame or a series."""
    if prices is None:
        return pd.Series(dtype=float)
    if isinstance(prices, pd.DataFrame):
        if 'Close' not in prices.columns:
            return pd.Series(dtype=float)
        s = prices['Close']
    else:
        s = prices
    s = pd.to_numeric(s, errors='coerce')
    s.index = pd.to_datetime(s.index, errors='coerce')
    s = s[~s.index.isna()].dropna()
    s = s[s > 0]
    return s[~s.index.duplicated(keep='last')].sort_index()


def _returns(closes: dict[str, pd.Series], days: int) -> pd.DataFrame:
    """Daily returns on a common business-day calendar, last *days* rows.

    Exchanges close on different holidays; an inner join would drop every such
    day for all tickers, so prices are carried forward over a holiday (a zero
    return that day) instead.
    """
    series = {t: s for t, s in closes.items() if len(s) > 1}
    if not series:
        return pd.DataFrame()
    frame = pd.DataFrame(series)
    frame.index = frame.index.normalize()
    frame = frame.groupby(level=0).last()
    frame = frame[frame.index.dayofweek < 5].ffill()
    ret = frame.pct_change(fill_method=None)
    return ret.iloc[1:].tail(days)


def _ann_vol(close: pd.Series, days: int = VOL_DAYS) -> float:
    r = close.pct_change().dropna().tail(days)
    if len(r) < max(5, days // 2):
        return np.nan
    return float(r.std() * np.sqrt(TRADING_DAYS))


def _risk_split(weights: pd.Series, returns: pd.DataFrame) -> tuple[pd.Series, float, pd.DataFrame]:
    """(risk share % per ticker, annualised depot vol, correlation matrix).

    Tickers with too short a history stay out; the weights of the rest are
    re-normalised so the shares add up to 100.
    """
    if returns.empty:
        return pd.Series(dtype=float), np.nan, pd.DataFrame()
    ok = [t for t in weights.index if t in returns.columns
          and returns[t].notna().sum() >= COV_MIN_OBS]
    if not ok:
        return pd.Series(dtype=float), np.nan, pd.DataFrame()
    w = weights[ok].astype(float)
    if w.sum() <= 0:
        return pd.Series(dtype=float), np.nan, pd.DataFrame()
    w = w / w.sum()
    r = returns[ok].fillna(0.0)
    cov = r.cov()
    var = float(w @ cov @ w)
    if not np.isfinite(var) or var <= 0:
        return pd.Series(dtype=float), np.nan, r.corr()
    share = w * (cov @ w) / var * 100.0
    return share, float(np.sqrt(var * TRADING_DAYS)), r.corr()


def _mean_corr(corr: pd.DataFrame, tickers: list[str]) -> float:
    names = [t for t in tickers if t in corr.columns]
    if len(names) < 2:
        return np.nan
    sub = corr.loc[names, names].to_numpy()
    upper = sub[np.triu_indices(len(names), k=1)]
    upper = upper[np.isfinite(upper)]
    return float(upper.mean()) if upper.size else np.nan


def _names(tickers) -> str:
    return ', '.join(sorted(tickers))


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

def review(positions: pd.DataFrame, prices: dict, stops: dict | None = None,
           stop_tab: str = '', signals: dict | None = None,
           quality: dict | None = None, profile: dict | None = None,
           thresholds: dict | None = None, today=None) -> Review:
    """Apply the rules to one depot.

    positions  ticker, longName, shares, positionValue (system currency), gainPct,
               sector, currency (listing) -- one row per ticker
    prices     {ticker: daily frame with Close, or a close series}
    stops      {ticker: {'stop', 'price', 'updated'}} with stop and price in the
               same currency; None = stop source unavailable (rules skipped)
    stop_tab   name of the tab where the stops are updated (for the texts)
    signals    {ticker: {'action', 'last_sell', ...}} of the user's strategy; None = off
    quality    {ticker: 'leverage,surprise'} red flags of the quality score
    profile    risk_profile.resolve(...) -- for the per-position volatility band
    """
    th = {**THRESHOLDS, **(thresholds or {})}
    today = pd.Timestamp(today or dt.date.today()).normalize()
    findings: list[Finding] = []

    pos = positions.copy() if positions is not None else pd.DataFrame()
    if pos.empty:
        empty = pd.DataFrame(columns=POSITION_COLUMNS)
        return Review(empty, [], {'total': 0.0, 'n': 0}, pd.Series(dtype=float),
                      pd.Series(dtype=float),
                      (profile or {}).get('label') or (profile or {}).get('name', ''))
    for c, default in (('longName', ''), ('sector', 'Other'), ('currency', ''),
                       ('gainPct', np.nan)):
        if c not in pos.columns:
            pos[c] = default
    pos['positionValue'] = pd.to_numeric(pos['positionValue'], errors='coerce')
    # map/where instead of Series.replace: replace() with several values trips a
    # pandas bug under copy-on-write ("truth value of an array is ambiguous")
    sec = pos['sector'].fillna('Other').astype(str)
    pos['sector'] = sec.where(sec != '', 'Other')
    pos['currency'] = pos['currency'].fillna('').astype(str)
    pos = pos.set_index('ticker', drop=False)

    for tk in pos.index[pos['positionValue'].isna()]:
        findings.append(Finding('data', 'no_value', tk))
    valued = pos['positionValue'].fillna(0.0).clip(lower=0.0)
    total = float(valued.sum())
    pos['weight'] = valued / total * 100.0 if total > 0 else 0.0

    # -- prices: volatility, data quality -----------------------------------
    closes = {tk: _close(prices.get(tk)) for tk in pos.index}
    last_dates = {tk: s.index[-1] for tk, s in closes.items() if not s.empty}
    newest = max(last_dates.values()) if last_dates else None
    gap_since = today - pd.Timedelta(days=GAP_DAYS)
    from tradinglib.data_quality import detect_price_gaps
    for tk, s in closes.items():
        if s.empty:
            findings.append(Finding('data', 'no_prices', tk))
            continue
        lag = (newest - last_dates[tk]).days
        if lag > STALE_DAYS:
            findings.append(Finding('data', 'stale', tk,
                                    {'date': last_dates[tk].strftime('%Y-%m-%d'), 'days': lag}))
        gaps = detect_price_gaps(s.to_frame('Close'), since=gap_since.strftime('%Y-%m-%d'))
        if gaps:
            g = gaps[-1]
            findings.append(Finding('data', 'price_gap', tk,
                                    {'date': pd.Timestamp(g['date']).strftime('%Y-%m-%d'),
                                     'factor': g['factor'], 'cause': g['cause']}))
    pos['vol'] = [(_ann_vol(closes[tk]) if not closes[tk].empty else np.nan) for tk in pos.index]

    # -- risk split -------------------------------------------------------------
    share, depot_vol, corr = _risk_split(pos['weight'], _returns(closes, COV_DAYS))
    pos['risk_share'] = share.reindex(pos.index)

    # -- stops --------------------------------------------------------------------
    pos['stop'] = np.nan
    pos['dist_stop'] = np.nan
    if stops is not None:
        missing = []
        stale = []
        for tk in pos.index:
            s = stops.get(tk) or stops.get(str(tk).upper())
            if not s or not s.get('stop'):
                missing.append(tk)
                continue
            stop, price = float(s['stop']), s.get('price')
            pos.at[tk, 'stop'] = stop
            if price is not None and np.isfinite(price) and price > 0:
                pos.at[tk, 'dist_stop'] = (price / stop - 1) * 100.0
                if price <= stop:
                    findings.append(Finding('check', 'stop_breached', tk,
                                            {'price': price, 'stop': stop}))
            upd = pd.to_datetime(s.get('updated'), errors='coerce')
            if pd.notna(upd) and (today - upd.normalize()).days > TRAIL_STALE_DAYS:
                stale.append(upd)
        if missing:
            findings.append(Finding('note', 'no_trail', '',
                                    {'n': len(missing), 'tickers': _names(missing),
                                     'tab': stop_tab}))
        if stale:
            findings.append(Finding('data', 'trail_stale', '',
                                    {'date': min(stale).strftime('%Y-%m-%d'), 'tab': stop_tab}))

    # -- volatility band of the profile (per position) ----------------------------
    band = float((profile or {}).get('vol_band') or 0)
    if band > 0:
        for tk in pos.index[pos['vol'] > band]:
            findings.append(Finding('note', 'vol_band', tk,
                                    {'vol': pos.at[tk, 'vol'] * 100, 'band': band * 100,
                                     'profile': (profile or {}).get('label')
                                     or (profile or {}).get('name', '')}))

    # -- risk share vs weight ------------------------------------------------------
    for tk in pos.index:
        rs, w = pos.at[tk, 'risk_share'], pos.at[tk, 'weight']
        if pd.notna(rs) and w > 0 and rs >= th['risk_share_min'] and rs > th['risk_share_factor'] * w:
            findings.append(Finding('note', 'risk_share', tk, {'risk': rs, 'weight': w}))

    # -- concentration ---------------------------------------------------------------
    for tk in pos.index[pos['weight'] > th['position_pct']]:
        findings.append(Finding('note', 'position_weight', tk,
                                {'weight': pos.at[tk, 'weight'], 'max': th['position_pct']}))
    sectors = pos.groupby('sector')['weight'].sum().sort_values(ascending=False)
    for sec, w in sectors.items():
        if sec != 'Other' and w > th['sector_pct']:
            members = pos.index[pos['sector'] == sec].tolist()
            mc = _mean_corr(corr, members)
            findings.append(Finding('note', 'sector_weight', '',
                                    {'sector': sec, 'weight': w, 'max': th['sector_pct'],
                                     'tickers': _names(members),
                                     'corr': '–' if np.isnan(mc) else f'{mc:.2f}'}))
    cur = pos['currency'].map(lambda c: _CCY_ALIAS.get(c, c))
    currencies = pos.assign(_c=cur).groupby('_c')['weight'].sum().sort_values(ascending=False)
    for c, w in currencies.items():
        if c != '?' and w > th['currency_pct']:
            findings.append(Finding('note', 'currency_weight', '',
                                    {'currency': c, 'weight': w, 'max': th['currency_pct']}))

    # -- strategy signal ---------------------------------------------------------------
    pos['signal'] = ''
    if signals is not None:
        for tk in pos.index:
            sig = signals.get(tk)
            if not sig:
                continue
            pos.at[tk, 'signal'] = sig.get('action') or ''
            if sig.get('action') == 'reduce':
                findings.append(Finding('note', 'sell_signal', tk,
                                        {'date': sig.get('last_sell') or sig.get('last_date') or '–'}))

    # -- quality red flags -----------------------------------------------------------------
    pos['flags'] = ''
    for tk in pos.index:
        flags = [f for f in str((quality or {}).get(tk) or '').split(',') if f]
        pos.at[tk, 'flags'] = ','.join(flags)
        if 'surprise' in flags:
            findings.append(Finding('note', 'surprise', tk))
        other = [f for f in flags if f in QUALITY_FLAGS]
        if other:
            findings.append(Finding('note', 'quality_flags', tk, {'flags': ', '.join(other)}))

    findings.sort(key=lambda f: (LEVELS.index(f.level), f.ticker == '', f.ticker, f.rule))
    largest = pos['weight'].idxmax() if total > 0 else ''
    metrics = {
        'total': total, 'n': int(len(pos)), 'depot_vol': depot_vol,
        'largest': largest, 'largest_weight': float(pos['weight'].max()) if total > 0 else 0.0,
        'n_check': sum(f.level == 'check' for f in findings),
        'n_note': sum(f.level == 'note' for f in findings),
        'n_data': sum(f.level == 'data' for f in findings),
        'price_date': newest.strftime('%Y-%m-%d') if newest is not None else '',
        'vol_band': band,
    }
    out = pos.reset_index(drop=True)
    for c in POSITION_COLUMNS:
        if c not in out.columns:
            out[c] = np.nan
    out = out[POSITION_COLUMNS].sort_values('weight', ascending=False).reset_index(drop=True)
    return Review(out, findings, metrics, sectors, currencies,
                  (profile or {}).get('label') or (profile or {}).get('name', ''))


# ---------------------------------------------------------------------------
# loaders (read-only)
# ---------------------------------------------------------------------------

def _path(db_path: str, name: str) -> str:
    from tradinglib.tools import Tools
    return Tools().get_path(path=db_path, file_name=name)


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def load_positions(source: str, username: str = '', db_path: str = 'database',
                   system_currency: str = 'EUR', broker=None) -> pd.DataFrame:
    """Open positions of *source*, one row per ticker, with sector and listing currency.

    *broker*: a connected broker instance to reuse (paper/live only).
    """
    from tradinglib import portfolio_map as pm
    if source in BROKER_OF:
        df = pm.paper_positions(username, db_path, system_currency,
                                broker_id=BROKER_OF[source], broker=broker)
    elif source in ('own', 'multi'):
        df = pm.load(source, username, db_path, system_currency)
    else:
        raise ValueError(f'unknown portfolio source {source!r}')
    df = pm.by_ticker(df)
    if df.empty:
        return df.assign(sector=pd.Series(dtype=str), currency=pd.Series(dtype=str))
    df = pm.enrich(df, db_path=db_path, system_currency=system_currency)
    return df.merge(_listing_currencies(df['ticker'].tolist(), db_path), on='ticker', how='left')


def _listing_currencies(tickers: list[str], db_path: str) -> pd.DataFrame:
    out = pd.DataFrame({'ticker': tickers, 'currency': ''})
    p = _path(db_path, 'asset_info.db')
    if not tickers or not os.path.exists(p):
        return out
    con = _ro(p)
    try:
        ph = ','.join('?' * len(tickers))
        info = pd.read_sql_query(f'SELECT ticker, currency FROM asset_info WHERE ticker IN ({ph})',
                                 con, params=tickers).drop_duplicates('ticker')
    except Exception:
        return out
    finally:
        con.close()
    return out[['ticker']].merge(info, on='ticker', how='left').fillna({'currency': ''})


def load_prices(tickers, db_path: str = 'database') -> dict[str, pd.DataFrame]:
    """Local daily history per ticker (yf_<ticker>.db only, no network)."""
    from tradinglib import four_ps
    out = {}
    for tk in tickers:
        try:
            out[tk] = four_ps.load_daily(tk, db_path)
        except Exception:
            logger.debug('portfolio_review: no prices for %s', tk, exc_info=True)
            out[tk] = pd.DataFrame()
    return out


def own_stops(positions: pd.DataFrame, db_path: str = 'database',
              system_currency: str = 'EUR') -> dict | None:
    """Stored trailing stops of the risk tab, compared with today's position price.

    The table holds stops in the system currency; the price is the position value
    per share, also in system currency -- fresher than the stored last_price,
    which only moves when somebody presses "update" in the risk tab. Rows written
    in another currency (or before the column existed) are skipped, as the risk
    tab itself does (``_read_prev_hwm``).
    None when trades.db does not exist.
    """
    p = _path(db_path, 'trades.db')
    if not os.path.exists(p):
        return None
    con = _ro(p)
    try:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='own_trades_trails'").fetchone():
            return {}
        cols = {r[1] for r in con.execute('PRAGMA table_info(own_trades_trails)')}
        ccy = 'currency' if 'currency' in cols else 'NULL AS currency'
        trails = pd.read_sql_query(
            f'SELECT ticker, trail_stop, updated_at, {ccy} FROM own_trades_trails', con)
    finally:
        con.close()
    price = {}
    if positions is not None and not positions.empty:
        sh = pd.to_numeric(positions['shares'], errors='coerce')
        val = pd.to_numeric(positions['positionValue'], errors='coerce')
        price = dict(zip(positions['ticker'].astype(str).str.upper(),
                         (val / sh.where(sh > 0)).tolist()))
    out = {}
    for r in trails.itertuples():
        if str(r.currency or '') != str(system_currency or ''):
            continue
        tk = str(r.ticker).upper()
        out[tk] = {'stop': r.trail_stop, 'price': price.get(tk), 'updated': r.updated_at}
    return out


def broker_trail_stops(prices: dict, source: str, db_path: str = 'database') -> dict | None:
    """Stored trailing stops of the trading page (position_trails) for paper/live.

    Stops there are in the listing currency (broker prices), so today's price is
    the latest local close, not the position value, which is in system currency.
    None when trading.db or the table does not exist.
    """
    p = _path(db_path, 'trading.db')
    if source not in BROKER_OF or not os.path.exists(p):
        return None
    mode = 'live' if source == 'live' else 'paper'
    con = _ro(p)
    try:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='position_trails'").fetchone():
            return None
        trails = pd.read_sql_query(
            'SELECT ticker, broker_symbol, trail_stop, updated_at FROM position_trails '
            'WHERE mode=? AND broker=?', con, params=(mode, BROKER_OF[source]))
    finally:
        con.close()
    last = {tk: _close(df) for tk, df in (prices or {}).items()}
    out = {}
    for r in trails.itertuples():
        for tk in {str(r.ticker), str(r.broker_symbol or r.ticker)}:
            s = last.get(tk)
            out[tk] = {'stop': r.trail_stop, 'updated': r.updated_at,
                       'price': float(s.iloc[-1]) if s is not None and not s.empty else None}
    return out


def load_quality(tickers, db_path: str = 'database') -> dict[str, str]:
    try:
        from tradinglib import quality_score as qs
        frame = qs.load(db_path)
    except Exception:
        logger.debug('portfolio_review: quality scores not readable', exc_info=True)
        return {}
    if frame.empty or 'quality_flags' not in frame.columns:
        return {}
    frame = frame[frame['ticker'].isin(list(tickers))]
    return {r.ticker: str(r.quality_flags or '') for r in frame.itertuples()}


def load_macro(currencies: pd.Series) -> pd.DataFrame:
    """Stored macro values for the depot's currencies (no fetch), with their weight."""
    try:
        from tradinglib import macro_data as md
        lat = md.latest(md.load_obs())
    except Exception:
        logger.debug('portfolio_review: macro data not readable', exc_info=True)
        return pd.DataFrame()
    if lat is None or lat.empty:
        return pd.DataFrame()
    weights = currencies.rename({'GBp': 'GBP'}).groupby(level=0).sum()
    lat = lat[lat['ccy'].isin(weights.index)].copy()
    lat['weight'] = lat['ccy'].map(weights)
    return lat


def settings(username: str) -> dict:
    """The user's thresholds, filled up with the defaults."""
    try:
        from tradinglib.system_config import SystemConfig
        stored = SystemConfig(username=username).get_value(CONFIG_KEY, None)
    except Exception:
        stored = None
    out = dict(THRESHOLDS)
    if isinstance(stored, dict):
        for k, v in stored.items():
            if k in THRESHOLDS:
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
    return out


def save_settings(username: str, values: dict) -> None:
    from tradinglib.system_config import SystemConfig
    SystemConfig(username=username).set_value(
        CONFIG_KEY, {k: float(v) for k, v in values.items() if k in THRESHOLDS})


# ---------------------------------------------------------------------------
# AI comment
# ---------------------------------------------------------------------------

def build_prompt(rv: Review, source_label: str, macro_text: str = '') -> str:
    """Prompt for the optional AI comment: explain and prioritise, add nothing.

    The scaffold stays German like the market overview; the findings are
    rendered from the German locale so the model reads the same rule texts.
    """
    from tradinglib.i18n import _load
    de = _load('de')
    m = rv.metrics
    lines = [
        f'DEPOT-BEWERTUNG ({source_label}), Kursstand {m.get("price_date") or "–"}',
        f'Depotwert {m.get("total", 0):,.0f} · {m.get("n", 0)} Positionen · '
        f'Risikoprofil {rv.profile_name or "–"}',
        '',
        'POSITIONEN (Gewicht % · Vola p.a. % · Risikoanteil % · Abstand Stop % · Sektor · Währung):',
    ]
    for r in rv.positions.itertuples():
        def f(v, fmt='{:.1f}'):
            return fmt.format(v) if pd.notna(v) else '–'
        lines.append(f'  {r.ticker} ({r.longName}): {f(r.weight)} · {f(r.vol * 100 if pd.notna(r.vol) else np.nan)}'
                     f' · {f(r.risk_share)} · {f(r.dist_stop)} · {r.sector} · {r.currency or "?"}')
    lines += ['', 'REGEL-ERGEBNISSE (feste Schwellen, Stufe: Prüfen > Hinweis > Daten):']
    level_de = {'check': 'Prüfen', 'note': 'Hinweis', 'data': 'Daten'}
    if rv.findings:
        for fd in rv.findings:
            who = f'{fd.ticker}: ' if fd.ticker else 'Depot: '
            lines.append(f'  [{level_de[fd.level]}] {who}{fd.text(de)}')
    else:
        lines.append('  keine')
    if macro_text:
        lines += ['', macro_text.strip()]
    lines += [
        '',
        'AUFGABE:',
        'Du bist ein nüchterner Risiko-Assistent. Erkläre die Regel-Ergebnisse oben in '
        'klarer Sprache und ordne sie nach Dringlichkeit (zuerst Prüfen, dann Hinweise mit '
        'dem größten Depotanteil). Höchstens 8 Sätze oder Stichpunkte.',
        'REGELN:',
        '- Nur die Regel-Ergebnisse erklären und priorisieren. KEINE neuen Kauf- oder '
        'Verkaufsempfehlungen, KEINE Kursziele, KEINE Markt-Prognose.',
        '- Das Makro-Umfeld ist nur Einordnung: ob es Kurse vorhersagt, ist nicht gemessen, '
        'und die gemessenen Timing-Signale (COT, Marktphase) hatten keinen Vorteil. '
        'Es darf keine Handlung begründen.',
        '- Daten-Punkte zuerst als mögliche Datenfehler benennen, bevor daraus etwas gefolgert wird.',
        '- Gibt es keine Regel-Ergebnisse, sage das in einem Satz.',
    ]
    return '\n'.join(lines)
