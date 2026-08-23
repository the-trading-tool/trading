"""What state was a market in on a given day?

Two independent readings, both answered from the market's own local daily series:

* **trend** — where the index stood relative to its 200- and 50-day average, and
  whether that 200-day average was rising. The classic "is the market trending"
  question.
* **season** — standing at this trading day of the year, how the same market
  behaved over the next few weeks in earlier years.

The Multi-Strategies view uses them to mark the market context of a buy
recommendation.

**Causal by construction.** Every value uses only data up to and including the
date asked about; the seasonal average uses only years strictly *before* that
date's year. A value shown next to a historical trade is therefore one that was
knowable on that day — which is what makes it fair to measure the marker against
the trades' outcomes instead of just decorating them.

Deliberately independent of premium/seasonality.py: that module renders
Streamlit output for a single asset and always looks at *today*. The seasonal
maths — align years by trading day of year, average the forward return — is the
same, and both read the same local series.
"""
import logging
import threading

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# --- seasonal parameters ----------------------------------------------------
# Forward horizon in trading days. 21 ~ one month, the order of magnitude the
# multi-strategy positions are held.
DEFAULT_HORIZON = 21
# Fewer past years than this and the average is noise, not a pattern.
MIN_YEARS = 3
# Older years say little about today's market structure.
MAX_YEARS = 15
# A verdict only for clear cases — anything between stays neutral, so the column
# does not suggest a precision the sample cannot carry.
STRONG_PROB = 60.0
WEAK_PROB = 40.0

# --- trend parameters -------------------------------------------------------
SLOW_MA = 200
FAST_MA = 50
# How far back the slow average is compared to call it rising/falling.
SLOPE_DAYS = 20

# --- beta parameters --------------------------------------------------------
# Trailing window for the regression, in sessions. 252 ~ one year: long enough
# to be stable, short enough to describe the asset as it behaves now.
BETA_WINDOW = 252
BETA_MIN_OBS = 120
# Only clearly offensive / defensive values get a verdict; the crowd around 1.0
# moves with its market and says nothing either way.
HIGH_BETA = 1.15
LOW_BETA = 0.85

# verdict vocabulary
UP, DOWN, TRANSITION = 'up', 'down', 'transition'
STRONG, WEAK, NEUTRAL = 'strong', 'weak', 'neutral'
TAILWIND, HEADWIND = 'tailwind', 'headwind'
NO_DATA = 'no_data'

_EMPTY_TREND = {'verdict': NO_DATA, 'close': None, 'sma_slow': None,
                'sma_fast': None, 'rising': None, 'dist_slow': None}
_EMPTY_SEASON = {'verdict': NO_DATA, 'prob': None, 'avg': None, 'n': 0,
                 'tday': None, 'horizon': DEFAULT_HORIZON}


class MarketPhase:
    """Trend and seasonal lookup for one market, built once from its series."""

    def __init__(self, ticker, max_years=MAX_YEARS, min_years=MIN_YEARS,
                 db_path='database'):
        self.ticker = ticker
        self.max_years = max_years
        self.min_years = min_years
        self._daily = None       # DataFrame: close, sma_slow, sma_fast, rising
        self._pivot = None       # year x trading-day-of-year -> close
        self._last_tday = None   # year -> last trading day carrying data
        self._days_by_year = {}
        self._load(db_path)

    # ------------------------------------------------------------------ setup
    def _load(self, db_path):
        try:
            from tradinglib.fetch_data import FetchData
            from tradinglib.utils import DataUtils
            df = FetchData(database_path=db_path).load_price_data(
                self.ticker, period='max', interval='1d')
            df = DataUtils.ensure_datetime_index(df)
        except Exception as e:
            logger.debug("market_phase %s: no local series (%s)", self.ticker, e)
            return
        if df is None or df.empty or 'Close' not in df.columns:
            return

        close = pd.to_numeric(df['Close'], errors='coerce').dropna().sort_index()
        if close.empty:
            return
        # Globex Sunday bars would shift every following trading day of the year
        # by one, so drop them (same treatment as premium/seasonality.py).
        close = close[close.index.dayofweek != 6]
        if close.empty:
            return

        self._daily = pd.DataFrame({'close': close})
        self._daily['sma_slow'] = close.rolling(SLOW_MA).mean()
        self._daily['sma_fast'] = close.rolling(FAST_MA).mean()
        self._daily['rising'] = (self._daily['sma_slow']
                                 > self._daily['sma_slow'].shift(SLOPE_DAYS))

        year = pd.Series(close.index.year, index=close.index)
        tday = year.groupby(year).cumcount() + 1
        self._days_by_year = {y: g.index for y, g in close.groupby(year)}

        piv = pd.DataFrame({'year': year.to_numpy(),
                            'tday': tday.to_numpy(),
                            'close': close.to_numpy()}) \
            .pivot(index='year', columns='tday', values='close') \
            .sort_index(axis=0).sort_index(axis=1)
        self._last_tday = piv.apply(lambda r: r.last_valid_index(), axis=1)
        # Fill interior holes only; the clamp against _last_tday keeps the fill
        # from inventing sessions past a year's end.
        self._pivot = piv.ffill(axis=1)

    @property
    def has_prices(self):
        return self._daily is not None and not self._daily.empty

    @property
    def has_seasonality(self):
        """True when the series carries enough past years for any statement."""
        return self._pivot is not None and len(self._pivot.index) > self.min_years

    def years(self):
        return [] if self._pivot is None else list(self._pivot.index)

    # ------------------------------------------------------------------ trend
    def trend(self, date):
        """Trend state of the market on *date* (last session at or before it)."""
        out = dict(_EMPTY_TREND)
        if not self.has_prices:
            return out
        ts = pd.Timestamp(date)
        if pd.isna(ts):
            return out
        sub = self._daily.loc[:ts.normalize()]
        if sub.empty:
            return out
        row = sub.iloc[-1]
        if pd.isna(row['sma_slow']):
            return out       # less than SLOW_MA sessions of history

        out['close'] = float(row['close'])
        out['sma_slow'] = float(row['sma_slow'])
        out['sma_fast'] = None if pd.isna(row['sma_fast']) else float(row['sma_fast'])
        out['rising'] = bool(row['rising'])
        out['dist_slow'] = (out['close'] / out['sma_slow'] - 1.0) * 100.0

        above = out['close'] > out['sma_slow']
        if above and out['rising']:
            out['verdict'] = UP
        elif not above and not out['rising']:
            out['verdict'] = DOWN
        else:
            # Above a falling average or below a rising one — the market has
            # turned but not confirmed. Worth its own bucket: these were the
            # weakest trades in the measurement.
            out['verdict'] = TRANSITION
        return out

    # ----------------------------------------------------------------- season
    def _tday(self, ts):
        """1-based trading-day-of-year position of *ts* in this market's calendar.

        The date need not be a session of this market — a German index gets asked
        about a day a US position was bought on. The position is then the number
        of sessions the market had up to and including that day.
        """
        days = self._days_by_year.get(ts.year)
        if days is None or not len(days):
            return None
        pos = int(np.searchsorted(days.values, np.datetime64(ts), side='right'))
        return pos or None

    def season(self, date, horizon=DEFAULT_HORIZON):
        """Seasonal state of the market at *date*.

        'prob' is the share of past years that rose over the next *horizon*
        sessions from this point in the year, 'avg' their mean forward return,
        'n' how many years the average rests on.
        """
        out = dict(_EMPTY_SEASON, horizon=horizon)
        if self._pivot is None:
            return out
        ts = pd.Timestamp(date)
        if pd.isna(ts):
            return out
        ts = ts.normalize()
        x1 = self._tday(ts)
        if x1 is None:
            return out
        out['tday'] = x1

        past = [y for y in self._pivot.index if y < ts.year][-self.max_years:]
        if len(past) < self.min_years:
            return out

        rets = []
        for y in past:
            last = self._last_tday.get(y)
            if last is None or pd.isna(last) or x1 > last:
                continue          # that year ended before this point — no vote
            x2 = min(x1 + horizon, int(last))
            if x2 <= x1:
                continue
            row = self._pivot.loc[y]
            v1, v2 = row.get(x1), row.get(x2)
            if v1 is None or v2 is None or pd.isna(v1) or pd.isna(v2) or not v1:
                continue
            rets.append((v2 / v1 - 1.0) * 100.0)

        if len(rets) < self.min_years:
            return out

        prob = sum(1 for r in rets if r > 0) / len(rets) * 100.0
        avg = float(np.mean(rets))
        out.update(prob=prob, avg=avg, n=len(rets))
        if prob >= STRONG_PROB and avg > 0:
            out['verdict'] = STRONG
        elif prob <= WEAK_PROB and avg < 0:
            out['verdict'] = WEAK
        else:
            out['verdict'] = NEUTRAL
        return out

    def state(self, date, horizon=DEFAULT_HORIZON):
        """Both readings in one dict, keys prefixed 'trend_' / 'season_'."""
        t = self.trend(date)
        s = self.season(date, horizon=horizon)
        out = {f'trend_{k}': v for k, v in t.items()}
        out.update({f'season_{k}': v for k, v in s.items()})
        out['ticker'] = self.ticker
        return out


# ------------------------------------------------------------------------ beta
_returns_cache: dict = {}
_returns_lock = threading.Lock()


def _returns(ticker, db_path='database'):
    """Daily returns of *ticker* from its local series, cached.

    Only the return series is kept, not the frame — a beta run touches over a
    thousand tickers.
    """
    key = str(ticker)
    with _returns_lock:
        if key in _returns_cache:
            return _returns_cache[key]
    try:
        from tradinglib.fetch_data import FetchData
        from tradinglib.utils import DataUtils
        df = FetchData(database_path=db_path).load_price_data(
            key, period='max', interval='1d')
        df = DataUtils.ensure_datetime_index(df)
        close = pd.to_numeric(df['Close'], errors='coerce').dropna().sort_index()
        close = close[~close.index.duplicated(keep='last')]
        ser = close.pct_change().dropna() if len(close) > 1 else None
    except Exception:
        ser = None
    with _returns_lock:
        _returns_cache[key] = ser
    return ser


def clear_returns_cache():
    with _returns_lock:
        _returns_cache.clear()


def beta(asset, market, date, window=BETA_WINDOW, min_obs=BETA_MIN_OBS,
         db_path='database'):
    """Beta of *asset* against *market* over the sessions ending at *date*.

    Measured against the row's **own** index rather than taken from
    `asset_info.beta`: that field is a single current Yahoo number against an
    unstated benchmark and covers only ~56 % of the universe. Regressing the
    local series against the index the position actually belongs to is both
    causal — nothing after *date* enters — and asks the right question.

    Returns None when either series is missing or the overlap is too short.
    """
    if not asset or not market:
        return None
    ra, rm = _returns(asset, db_path), _returns(market, db_path)
    if ra is None or rm is None or ra.empty or rm.empty:
        return None
    ts = pd.Timestamp(date)
    if pd.isna(ts):
        return None
    ts = ts.normalize()
    # Align first, then take the window: asset and index calendars differ (a US
    # stock against a German index), and cutting each side separately would pair
    # up days that are not the same day.
    joined = pd.concat([ra.loc[:ts], rm.loc[:ts]], axis=1,
                       join='inner', keys=['a', 'm']).dropna()
    if len(joined) < min_obs:
        return None
    joined = joined.tail(window)
    var = float(joined['m'].var())
    if not var or pd.isna(var):
        return None
    return float(joined['a'].cov(joined['m']) / var)


def beta_class(beta_value):
    """Offensive / market-like / defensive, from the beta level alone.

    This — not `beta_fit` below — is what the buy table shows, because it is the
    reading the trades support: over 4372 closed trades a beta above HIGH_BETA
    beat one below LOW_BETA in all six strategies and in seven of nine markets.

    Read it as amplitude, not as odds: the hit rate barely moves (53.3 % against
    49.8 %), the average does (+2.63 % against +1.35 %). Beta scales whatever the
    strategy produces, so in the one losing year of the sample (2022) the ramp
    reversed.
    """
    if beta_value is None or (isinstance(beta_value, float) and pd.isna(beta_value)):
        return NO_DATA
    if beta_value >= HIGH_BETA:
        return UP
    if beta_value <= LOW_BETA:
        return DOWN
    return NEUTRAL


def beta_fit(beta_value, trend_verdict):
    """Does the asset's market sensitivity point with or against the phase?

    The textbook reading: a high beta amplifies whatever the market does —
    welcome in a rising market, unwelcome in a falling one; a low beta shelters
    in a falling market but lags a rising one.

    **Measured and not confirmed**, which is why the buy table shows
    `beta_class` instead. Over the same 4372 trades 'headwind' did about as well
    as 'tailwind' (avg +3.27 % against +3.48 %) and beat it inside three of the
    four years that carried both — because high beta helped in *both* market
    phases (down +3.27 %, up +4.15 %, against +1.81 % / +2.13 % for low beta).
    Conditioning on the phase dilutes the signal that beta itself carries.
    Kept as the explicit answer to that question; not wired into the UI.
    """
    if beta_value is None or (isinstance(beta_value, float) and pd.isna(beta_value)):
        return NO_DATA
    if trend_verdict == UP:
        if beta_value >= HIGH_BETA:
            return TAILWIND
        return NEUTRAL          # low beta lags a rising market, it does not fight it
    if trend_verdict == DOWN:
        if beta_value >= HIGH_BETA:
            return HEADWIND
        if beta_value <= LOW_BETA:
            return TAILWIND
        return NEUTRAL
    return NEUTRAL if trend_verdict == TRANSITION else NO_DATA


# ---------------------------------------------------------------- module cache
_cache: dict = {}
_lock = threading.Lock()


def get_market(ticker, **kw):
    """Cached MarketPhase. Building one reads a full price series, and a trade
    table asks about the same handful of markets over and over.
    """
    key = (str(ticker), kw.get('max_years', MAX_YEARS), kw.get('min_years', MIN_YEARS))
    with _lock:
        inst = _cache.get(key)
        if inst is None:
            inst = MarketPhase(str(ticker), **kw)
            _cache[key] = inst
    return inst


def clear_cache():
    with _lock:
        _cache.clear()


def state_for(ticker, date, horizon=DEFAULT_HORIZON, **kw):
    """Trend + seasonal state of *ticker* at *date* (see MarketPhase.state)."""
    if not ticker or str(ticker).lower() in ('nan', 'none', ''):
        out = {f'trend_{k}': v for k, v in _EMPTY_TREND.items()}
        out.update({f'season_{k}': v for k, v in _EMPTY_SEASON.items()})
        out.update(ticker=ticker, season_horizon=horizon)
        return out
    return get_market(ticker, **kw).state(date, horizon=horizon)


_ANNOTATED = ('trend_verdict', 'trend_dist_slow',
              'season_verdict', 'season_prob', 'season_avg', 'season_n')
_BETA_COLS = ('beta', 'beta_fit')


def annotate(df, market_col='stockIndex', date_col='buyDate',
             horizon=DEFAULT_HORIZON, asset_col=None, **kw):
    """Add the market-context columns of `_ANNOTATED` to a copy of *df*.

    One lookup per (market, day) pair — the same combination repeats across
    strategies and a lookup is pure.

    Pass *asset_col* (e.g. 'ticker') to also get 'beta' — the row's sensitivity
    to its own market, measured over the year before the date — and 'beta_fit',
    whether that sensitivity points with or against the market's phase. This
    costs one price series per distinct asset, so it is opt-in.
    """
    out = df.copy()
    for col in _ANNOTATED:
        if col not in out.columns:
            out[col] = NO_DATA if col.endswith('verdict') else np.nan
    if asset_col:
        out['beta'] = np.nan
        out['beta_fit'] = NO_DATA
        out['beta_class'] = NO_DATA
    if out.empty or market_col not in out.columns or date_col not in out.columns:
        return out

    seen: dict = {}
    collected: dict = {c: [] for c in _ANNOTATED}
    for market, date in zip(out[market_col], out[date_col]):
        key = (str(market), str(date)[:10])
        st = seen.get(key)
        if st is None:
            st = state_for(market, date, horizon=horizon, **kw)
            seen[key] = st
        for c in _ANNOTATED:
            v = st.get(c)
            collected[c].append(np.nan if v is None else v)
    for c in _ANNOTATED:
        out[c] = collected[c]

    if asset_col and asset_col in out.columns:
        seen_b: dict = {}
        betas, fits = [], []
        for asset, market, date, trend in zip(out[asset_col], out[market_col],
                                              out[date_col], out['trend_verdict']):
            key = (str(asset), str(market), str(date)[:10])
            b = seen_b.get(key, ...)
            if b is ...:
                b = beta(asset, market, date)
                seen_b[key] = b
            betas.append(np.nan if b is None else b)
            fits.append(beta_fit(b, trend))
        out['beta'] = betas
        out['beta_fit'] = fits
        out['beta_class'] = [beta_class(b) for b in betas]
    return out
