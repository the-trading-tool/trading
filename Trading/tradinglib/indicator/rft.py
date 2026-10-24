"""Range Filtered Trend — Kalman line, Supertrend and a volatility band.

Port of "Range Filtered Trend Signals [AlgoAlpha]" (TradingView, Pine Script v6).
Attribution: © AlgoAlpha. The original is published under the Mozilla Public
License 2.0 (https://mozilla.org/MPL/2.0/); this file, as a derived work, is
under the same licence.

What it computes, bar by bar and causally (every value uses bars up to its own):

* ``rft_kalman``      a Kalman-smoothed close (the original's ``k``).
* ``rft_supertrend``  a Supertrend run on that line (ATR from the real bars).
* ``rft_upper`` / ``rft_lower``
                      ``k`` +/- ``deviation`` x WMA(High - Low, 200).
* ``rft_trend``       short-term trend: +1 after a close above the upper band,
                      -1 after a close below the lower band, held in between
                      (0 until the first break).
* ``rft_ktrend``      long-term trend from the Supertrend direction (+1 / -1).
* ``rft_state``       ``rft_ktrend * rft_trend``: +1 = trending (both agree),
                      -1 = ranging (they disagree), 0 = undefined.
* ``rft_signal``      on the bar the market starts trending (``rft_state``
                      crosses above 0): +1 trending up, -1 trending down; else 0.

Drawing follows the original: the Kalman line only while trending, the band
edges as dots on the side that matters, a grey range band while ranging and a
dot where a trend starts. The original also recolours the candles
(``barcolor``); a Plotly overlay cannot do that, the band fill carries the same
information.

Daily charts take their warm-up from the full local history (like ``atc`` and
``prof``): the 200-bar WMA alone would otherwise leave most of a one-year chart
without a band. The chart's own bars stay the source for every bar it shows,
including today's.
"""
import logging
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

logger = logging.getLogger(__name__)

COLUMNS = ('rft_kalman', 'rft_supertrend', 'rft_upper', 'rft_lower', 'rft_trend',
           'rft_ktrend', 'rft_state', 'rft_signal')

VOLA_PERIOD = 200
WARMUP_BARS = 600          # history put in front of a daily chart


def kalman(a: np.ndarray, period: float, alpha: float, beta: float) -> np.ndarray:
    """The original's ``kalman(a, b, alpha, beta)``, one value per bar."""
    out = np.full(len(a), np.nan)
    v1, v2, v3 = np.nan, 1.0, alpha * period
    for t in range(len(a)):
        if np.isnan(v1):
            v1 = a[t - 1] if t > 0 else np.nan
        v5 = v1
        v4 = v2 / (v2 + v3)
        v1 = v5 + v4 * (a[t] - v5)
        v2 = (1 - v4) * v2 + beta / period
        out[t] = v1
    return out


def atr_rma(high, low, close, period: int) -> np.ndarray:
    """Pine ``ta.atr``: Wilder average of the true range, seeded with an SMA."""
    prev = np.r_[np.nan, close[:-1]]
    tr = np.where(np.isnan(prev), high - low,
                  np.nanmax(np.vstack([high - low, np.abs(high - prev), np.abs(low - prev)]), axis=0))
    out = np.full(len(tr), np.nan)
    if len(tr) < period:
        return out
    out[period - 1] = np.mean(tr[:period])
    for t in range(period, len(tr)):
        out[t] = out[t - 1] + (tr[t] - out[t - 1]) / period
    return out


def supertrend(k, high, low, close, factor: float, atr_period: int):
    """The original's ``pine_supertrend`` on the Kalman line -> (line, direction)."""
    n = len(k)
    atr = atr_rma(high, low, close, atr_period)
    st = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    upper_prev = lower_prev = np.nan          # final band values of the previous bar
    st_prev = np.nan
    for t in range(n):
        up = k[t] + factor * atr[t]
        lo = k[t] - factor * atr[t]
        p_lo = 0.0 if np.isnan(lower_prev) else lower_prev       # nz()
        p_up = 0.0 if np.isnan(upper_prev) else upper_prev
        k_prev = k[t - 1] if t > 0 else np.nan
        # Comparisons with na are false in Pine, as they are with NaN here.
        lo = lo if (lo > p_lo or k_prev < p_lo) else p_lo
        up = up if (up < p_up or k_prev > p_up) else p_up
        if t == 0 or np.isnan(atr[t - 1]):
            d = 1
        elif st_prev == p_up:
            d = -1 if k[t] > up else 1
        else:
            d = 1 if k[t] < lo else -1
        st[t] = lo if d == -1 else up
        direction[t] = d
        lower_prev, upper_prev, st_prev = lo, up, st[t]
    return st, direction


def wma(x: np.ndarray, period: int) -> np.ndarray:
    """Pine ``ta.wma``: linearly weighted, the newest bar weighs ``period``."""
    w = np.arange(1, period + 1, dtype=float)
    return (pd.Series(x).rolling(period, min_periods=period)
            .apply(lambda s: np.dot(s, w) / w.sum(), raw=True).to_numpy())


def compute(frame: pd.DataFrame, alpha=0.01, beta=0.1, period=77, deviation=1.2,
            st_factor=0.7, st_atr_period=7) -> pd.DataFrame:
    """All columns for an OHLC frame (Open optional, High/Low/Close required)."""
    high = pd.to_numeric(frame['High'], errors='coerce').to_numpy(float)
    low = pd.to_numeric(frame['Low'], errors='coerce').to_numpy(float)
    close = pd.to_numeric(frame['Close'], errors='coerce').to_numpy(float)

    k = kalman(close, float(period), float(alpha), float(beta))
    st, direction = supertrend(k, high, low, close, float(st_factor), int(st_atr_period))
    vola = wma(high - low, VOLA_PERIOD)
    upper = k + vola * deviation
    lower = k - vola * deviation

    trend = np.zeros(len(close))
    state = 0
    for t in range(len(close)):
        if close[t] > upper[t]:
            state = 1
        elif close[t] < lower[t]:
            state = -1
        trend[t] = state
    ktrend = np.where(direction < 0, 1, np.where(direction > 0, -1, 0)).astype(float)
    combined = ktrend * trend
    prev = np.r_[np.nan, combined[:-1]]
    starts = (combined > 0) & (prev <= 0)
    signal = np.where(starts, trend, 0.0)

    return pd.DataFrame({
        'rft_kalman': k, 'rft_supertrend': st, 'rft_upper': upper, 'rft_lower': lower,
        'rft_trend': trend, 'rft_ktrend': ktrend, 'rft_state': combined, 'rft_signal': signal,
    }, index=frame.index)


class Rft(_indicator._Indicator):

    is_oszilator = False
    name = 'Range Filtered Trend'

    params = {
        'kalman_alpha':  {'type': 'float', 'default': 0.01, 'min': 0.0001, 'step': 0.01,
                          'label': 'Kalman alpha (smaller = smoother)'},
        'kalman_beta':   {'type': 'float', 'default': 0.1, 'min': 0.0001, 'step': 0.01,
                          'label': 'Kalman beta (sensitivity to trend changes)'},
        'kalman_period': {'type': 'int', 'default': 77, 'min': 2, 'max': 500,
                          'label': 'Kalman period'},
        'deviation':     {'type': 'float', 'default': 1.2, 'min': 0.1, 'step': 0.1,
                          'label': 'Band deviation (x WMA of the bar range)'},
        'st_factor':     {'type': 'float', 'default': 0.7, 'min': 0.1, 'step': 0.1,
                          'label': 'Supertrend ATR factor'},
        'st_atr_period': {'type': 'int', 'default': 7, 'min': 1, 'max': 100,
                          'label': 'Supertrend ATR period'},
        'show_signals':  {'type': 'bool', 'default': True, 'label': 'Show trend-start dots'},
        'show_range':    {'type': 'bool', 'default': True, 'label': 'Fill the range band'},
        'color_bull':    {'type': 'color', 'default': '', 'label': 'Bullish color'},
        'color_bear':    {'type': 'color', 'default': '', 'label': 'Bearish color'},
    }

    def __init__(self, df, symbol="", kalman_alpha=0.01, kalman_beta=0.1, kalman_period=77,
                 deviation=1.2, st_factor=0.7, st_atr_period=7, show_signals=True,
                 show_range=True, color_bull='', color_bear=''):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.kalman_alpha = float(kalman_alpha)
        self.kalman_beta = float(kalman_beta)
        self.kalman_period = max(2, int(kalman_period))
        self.deviation = float(deviation)
        self.st_factor = float(st_factor)
        self.st_atr_period = max(1, int(st_atr_period))
        self.show_signals = show_signals
        self.show_range = show_range
        self.color_bull = color_bull or '#00ffbb'
        self.color_bear = color_bear or '#ff1100'
        self.data()

    # ── Input ────────────────────────────────────────────────────────────────
    def _target_index(self) -> pd.DatetimeIndex:
        idx = pd.to_datetime(self.df.index, errors='coerce')
        if idx.isna().all() and 'Date' in self.df.columns:
            idx = pd.to_datetime(self.df['Date'], errors='coerce')
        return pd.DatetimeIndex(idx)

    @staticmethod
    def _is_daily(index: pd.DatetimeIndex) -> bool:
        if len(index) < 3:
            return False
        spacing = pd.Series(index).diff().dt.total_seconds().median()
        return bool(spacing) and 20 * 3600 <= spacing <= 4 * 24 * 3600

    def _input(self, target: pd.DatetimeIndex) -> tuple[pd.DataFrame, int]:
        """Chart bars, on daily charts preceded by local history as warm-up.

        Returns the frame and the number of warm-up rows in front of the chart.
        """
        chart = self.df[['High', 'Low', 'Close']].copy()
        chart.index = target
        chart = chart.astype(float)
        if not (self.symbol and self._is_daily(target)):
            return chart, 0
        try:
            from tradinglib import four_ps
            hist = four_ps.load_daily(self.symbol)
        except Exception:
            logger.debug("rft: no local history for %s", self.symbol, exc_info=True)
            return chart, 0
        if hist is None or hist.empty:
            return chart, 0
        first = target.min()
        if getattr(first, 'tzinfo', None) is not None:
            first = first.tz_localize(None)
        before = hist[hist.index.normalize() < first.normalize()]
        before = before[['High', 'Low', 'Close']].astype(float).iloc[-WARMUP_BARS:]
        return pd.concat([before, chart]), len(before)

    def data(self):
        """Compute the rft_* columns and attach them to self.df."""
        target = self._target_index()
        if target.isna().all() or not {'High', 'Low', 'Close'} <= set(self.df.columns):
            for column in COLUMNS:
                self.df[column] = np.nan
            return
        frame, warm = self._input(target)
        out = compute(frame, self.kalman_alpha, self.kalman_beta, self.kalman_period,
                      self.deviation, self.st_factor, self.st_atr_period).iloc[warm:]
        for column in COLUMNS:
            self.df[column] = out[column].to_numpy()

    # ── Figure ───────────────────────────────────────────────────────────────
    @staticmethod
    def _rgba(color: str, alpha: float) -> str:
        c = color.lstrip('#')
        if len(c) != 6:
            return color
        return f'rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{alpha})'

    def _runs(self, df, mask, key=None):
        """Consecutive stretches where *mask* holds (split further by *key*)."""
        group = (mask != mask.shift()).cumsum()
        if key is not None:
            group = group.astype(str) + '_' + (key != key.shift()).cumsum().astype(str)
        for _, seg in df[mask].groupby(group[mask]):
            yield seg

    def add_fig(self):
        """Kalman line while trending, band dots, range fill and trend-start dots."""
        self.fig = go.Figure()
        df = self.df.copy()
        if 'Date' not in df.columns:
            df = df.reset_index()
            if 'Date' not in df.columns:
                df = df.rename(columns={df.columns[0]: 'Date'})
        x = df['Date']
        state, trend, ktrend = df['rft_state'], df['rft_trend'], df['rft_ktrend']
        trending, ranging = state == 1, state == -1
        bull_t, bear_t = self._rgba(self.color_bull, 0.3), self._rgba(self.color_bear, 0.3)

        # Shade between the Kalman line and the candle bodies while trending.
        if 'Open' in df.columns:
            midbody = (df['Close'] + df['Open']) / 2
            for seg in self._runs(df.assign(_mid=midbody), trending, trend):
                xs, ys = self.segmented_band(seg['Date'], np.maximum(seg['rft_kalman'], seg['_mid']),
                                             np.minimum(seg['rft_kalman'], seg['_mid']))
                color = self.color_bull if seg['rft_trend'].iloc[0] == 1 else self.color_bear
                self.fig.add_trace(go.Scatter(x=xs, y=ys, fill='toself', fillcolor=self._rgba(color, 0.15),
                                              line=dict(color='rgba(0,0,0,0)'), hoverinfo='skip',
                                              showlegend=False, name='Trend fill'))

        if self.show_range:
            for seg in self._runs(df, ranging):
                xs, ys = self.segmented_band(seg['Date'], seg['rft_upper'], seg['rft_lower'])
                self.fig.add_trace(go.Scatter(x=xs, y=ys, fill='toself', fillcolor='rgba(128,128,128,0.10)',
                                              line=dict(color='rgba(0,0,0,0)'), hoverinfo='skip',
                                              showlegend=False, name='Range'))

        self.fig.add_trace(go.Scatter(
            x=x, y=df['rft_kalman'].where(trending), mode='lines', name='Kalman trend',
            line=dict(color='grey', width=3), connectgaps=False, showlegend=False))

        up_shown = (trend == -1) | ranging
        up_color = np.where(ranging, 'grey', np.where(ktrend == -1, bear_t, 'grey'))
        self.fig.add_trace(go.Scatter(
            x=x, y=df['rft_upper'].where(up_shown), mode='markers', name='Upper band',
            marker=dict(color=up_color, size=3), showlegend=False))
        lo_shown = (trend == 1) | ranging
        lo_color = np.where(ranging, 'grey', np.where(ktrend == 1, bull_t, 'grey'))
        self.fig.add_trace(go.Scatter(
            x=x, y=df['rft_lower'].where(lo_shown), mode='markers', name='Lower band',
            marker=dict(color=lo_color, size=3), showlegend=False))

        if self.show_signals:
            starts = df['rft_signal'] != 0
            self.fig.add_trace(go.Scatter(
                x=x[starts], y=df.loc[starts, 'rft_kalman'], mode='markers', name='Trend start',
                marker=dict(symbol='circle-dot', size=11,
                            color=np.where(df.loc[starts, 'rft_signal'] > 0, self.color_bull, self.color_bear),
                            line=dict(color='white', width=1)),
                showlegend=False))
