"""Profile scores — how calm an asset is, and how close it is to its own high.

Exposes the two calibrated numbers behind the risk profiles as a live
oscillator, under the same column names the backtest engine stores in
``asset_simulation_*.db``:

* ``riskScore``  — 0..100 from ATR as a share of price, 100 = calmest.
* ``riskBucket`` — 1..4, the narrowest profile whose cut the bar still passes.
* ``trendScore`` — 0..100 from the distance to the asset's own record high,
  100 = at the high.

The calibration lives in :mod:`tradinglib.risk_profile`; this class only loads
history and projects the daily result onto whatever timeframe the chart shows.

Why the full daily history is reloaded instead of scoring the chart's own
frame: both inputs are path-dependent. ATR needs the bars before the window,
and the record high is meaningless if it can only see two years back — a chart
zoomed to 2024 would otherwise show a stock "at its high" that is in fact 40 %
below it. Reloading is the same approach ``fps`` takes, and it is what makes the
live number identical to the stored one.

The record high is the running maximum of the full local daily close history —
the same definition ``fps_dist_high`` already uses, and deliberately *not* the
stored ``ath`` column: that one is built from a ten-year monthly window anchored
at the time of the run, so the same bar gets a different ``ath`` depending on
when the simulation happened to be executed. Measured against forward outcomes
the full-history definition is also the better of the two — it is the one whose
hit rate and drawdown both improve monotonically across every quintile.
"""
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

# Columns this indicator produces (kept in sync with
# asset_perf2.INDICATOR_BACKFILL_MAP['prof']).
COLUMNS = ('riskScore', 'riskBucket', 'trendScore')

ATR_PERIOD = 14


class Prof(_indicator._Indicator):

    is_oszilator = True
    name = 'Profile scores (risk / trend)'

    params = {
        'atr_period':   {'type': 'int', 'default': ATR_PERIOD, 'min': 2, 'max': 60,
                         'label': 'ATR period for the risk score'},
        'color_risk':   {'type': 'color', 'default': '', 'label': 'Risk score color'},
        'color_trend':  {'type': 'color', 'default': '', 'label': 'Trend score color'},
    }

    def __init__(self, df, symbol="", atr_period=ATR_PERIOD,
                 color_risk='', color_trend=''):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.atr_period = max(2, int(atr_period))
        self.color_risk = color_risk or 'steelblue'
        self.color_trend = color_trend or 'seagreen'
        self.data()

    # ── History ───────────────────────────────────────────────────────────────
    def _target_index(self) -> pd.DatetimeIndex:
        """Datetime version of the chart's own index (which may hold strings)."""
        idx = pd.to_datetime(self.df.index, errors='coerce')
        if idx.isna().all() and 'Date' in self.df.columns:
            idx = pd.to_datetime(self.df['Date'], errors='coerce')
        return pd.DatetimeIndex(idx)

    def _history(self, target: pd.DatetimeIndex) -> pd.DataFrame:
        """Full local daily OHLC, or the chart's own frame when there is none."""
        if self.symbol:
            try:
                from tradinglib import four_ps
                hist = four_ps.load_daily(self.symbol)
                if hist is not None and not hist.empty:
                    return hist
            except Exception:
                pass
        if len(target) < 2 or 'Close' not in self.df.columns:
            return pd.DataFrame()
        # Weekly or monthly bars would silently produce a different ATR and a
        # different record high — only fall back on daily-or-finer frames.
        spacing = pd.Series(target).diff().dt.total_seconds().median()
        if not spacing or spacing > 4 * 24 * 3600:
            return pd.DataFrame()
        frame = self.df.copy()
        frame.index = target
        keep = [c for c in ('Open', 'High', 'Low', 'Close') if c in frame.columns]
        return frame[keep].dropna(subset=['Close'])

    # ── Computation ───────────────────────────────────────────────────────────
    @staticmethod
    def atr_series(frame: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
        """ATR in price units — same true range and simple mean as the engine.

        ``asset_perf2.calc_atr_percent`` rounds its single value to two decimals;
        the series here is left unrounded, which only matters for very cheap
        assets and always in the harmless direction (finer, not coarser).
        """
        high, low, close = frame['High'], frame['Low'], frame['Close']
        previous = close.shift(1)
        true_range = pd.concat([(high - low).abs(),
                                (high - previous).abs(),
                                (low - previous).abs()], axis=1).max(axis=1)
        return true_range.rolling(window=period).mean()

    @staticmethod
    def record_high(close: pd.Series) -> pd.Series:
        """Running maximum of the closing price — causal by construction.

        Every bar sees only the closes up to and including itself, which is what
        makes the column safe to store next to a backtest.
        """
        return close.cummax()

    def data(self):
        """Compute riskScore / riskBucket / trendScore and attach them to self.df."""
        from tradinglib import risk_profile as rp

        target = self._target_index()
        try:
            hist = self._history(target)
        except Exception:
            hist = pd.DataFrame()

        if hist is None or hist.empty or 'Close' not in hist.columns or target.isna().all():
            for column in COLUMNS:
                self.df[column] = np.nan
            return

        close = pd.to_numeric(hist['Close'], errors='coerce')
        frame = pd.DataFrame(index=hist.index)
        if {'High', 'Low'} <= set(hist.columns):
            frame['atr_pct'] = self.atr_series(hist, self.atr_period) / close.where(close > 0)
        else:
            frame['atr_pct'] = np.nan
        frame['dist_ath'] = close / self.record_high(close).where(lambda s: s > 0) - 1.0

        frame['riskScore'] = rp.risk_score(frame['atr_pct'])
        frame['riskBucket'] = rp.risk_bucket(frame)
        frame['trendScore'] = rp.trend_score(frame['dist_ath'])

        # Levels, not events — forward-fill onto the chart's bars.
        projected = frame[list(COLUMNS)].reindex(target, method='ffill')
        for column in COLUMNS:
            self.df[column] = projected[column].to_numpy()

    # ── Figure ────────────────────────────────────────────────────────────────
    def add_fig(self):
        """Add the two score lines to a fresh oscillator figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
        if 'Date' not in self.df.columns and len(self.df.columns):
            self.df = self.df.rename(columns={self.df.columns[0]: 'Date'})
        x = self.df['Date']

        self.fig.add_trace(go.Scatter(
            x=x, y=self.df['trendScore'], name='Trend score', showlegend=False,
            mode='lines', line=dict(color=self.color_trend, width=2),
            fill='tozeroy', fillcolor='rgba(46,139,87,0.10)'))
        self.fig.add_trace(go.Scatter(
            x=x, y=self.df['riskScore'], name='Risk score', showlegend=False,
            mode='lines', line=dict(color=self.color_risk, width=2)))

        # Plain hlines only: an oscillator sub-plot re-homes layout annotations
        # to row 1 and rewrites xref='paper' to 'x', which turns x=0.0 into
        # 1970-01-01 and stretches the shared date axis (see ovt.py).
        for level, color in ((25, 'firebrick'), (50, 'grey'), (75, 'green')):
            self.fig.add_hline(y=level, line_width=1, line_dash='dot', line_color=color)
