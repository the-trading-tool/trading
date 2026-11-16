"""Earnings surprise -- live oscillator with the engine's column names.

``eps_surprise``, ``eps_surprise_prev`` and ``eps_days`` from
:mod:`tradinglib.earnings_surprise`, on the full local daily history (like
``mtt``/``prof``: the trading-day count since a release needs every bar, not
only the zoomed ones), then carried onto the chart's bars. The same numbers are
stored in asset_simulation (``/backfill:eps``), so a formula like
``(eps_days >= 1) & (eps_days <= 63) & (eps_surprise > 20)`` behaves alike in
the chart, the Strategy Finder and the candidate search.
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

COLUMNS = ('eps_surprise', 'eps_surprise_prev', 'eps_days')

# Bars are clipped here for drawing; surprises near a zero estimate reach
# thousands of percent and would flatten everything else.
_CLIP = 100.0


class Eps(_indicator._Indicator):

    is_oszilator = True
    name = 'Earnings surprise'

    params = {
        'threshold': {'type': 'float', 'default': 20.0, 'min': 0.0, 'max': 200.0,
                      'label': 'Reference line (surprise %)'},
    }

    def __init__(self, df, symbol="", threshold=20.0):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        try:
            self.threshold = float(threshold)
        except (TypeError, ValueError):
            self.threshold = 20.0
        self.data()

    def _target_index(self) -> pd.DatetimeIndex:
        idx = pd.to_datetime(self.df.index, errors='coerce')
        if idx.isna().all() and 'Date' in self.df.columns:
            idx = pd.to_datetime(self.df['Date'], errors='coerce')
        return pd.DatetimeIndex(idx)

    def _trading_days(self, target: pd.DatetimeIndex) -> pd.DatetimeIndex:
        """Daily dates of the full local history, or the chart's own daily bars."""
        if self.symbol:
            try:
                from tradinglib import four_ps
                hist = four_ps.cap_to_chart(four_ps.load_daily(self.symbol), target)
                if hist is not None and not hist.empty:
                    return pd.DatetimeIndex(hist.index)
            except Exception:
                pass
        t = target.dropna()
        if len(t) < 2:
            return pd.DatetimeIndex([])
        spacing = pd.Series(t).diff().dt.total_seconds().median()
        if not spacing or spacing > 4 * 24 * 3600:
            return pd.DatetimeIndex([])         # weekly bars cannot count trading days
        return pd.DatetimeIndex(t.normalize().unique())

    def data(self):
        """Attach the three columns to self.df (NaN / 0 days without data)."""
        from tradinglib import earnings_surprise as es

        target = self._target_index()
        try:
            days = self._trading_days(target)
            rep = es.reports(self.symbol) if self.symbol else None
        except Exception:
            days, rep = pd.DatetimeIndex([]), None
        if len(days) == 0 or rep is None or rep.empty or target.isna().all():
            self.df['eps_surprise'] = np.nan
            self.df['eps_surprise_prev'] = np.nan
            self.df['eps_days'] = 0.0
            return
        frame = es.daily_columns(days.sort_values(), rep)
        frame = frame[~frame.index.duplicated(keep='last')]
        projected = frame.reindex(target, method='ffill')
        for column in COLUMNS:
            values = projected[column].to_numpy()
            if column == 'eps_days':
                values = np.nan_to_num(values, nan=0.0)
            self.df[column] = values

    def add_fig(self):
        """Surprise of the report in effect as bars (clipped), reference lines at 0 / +-threshold."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
        if 'Date' not in self.df.columns and len(self.df.columns):
            self.df = self.df.rename(columns={self.df.columns[0]: 'Date'})
        x = self.df['Date']
        s = pd.to_numeric(self.df['eps_surprise'], errors='coerce')
        d = pd.to_numeric(self.df['eps_days'], errors='coerce')
        colors = np.where(s >= 0, 'seagreen', 'indianred')
        self.fig.add_trace(go.Bar(
            x=x, y=s.clip(-_CLIP, _CLIP), name='EPS surprise %', showlegend=False,
            marker_color=colors, opacity=0.6,
            customdata=np.stack([s, d], axis=-1),
            hovertemplate='Surprise %{customdata[0]:.1f} % · day %{customdata[1]:.0f}'
                          '<extra></extra>'))
        # Plain hlines only (see prof.py: annotations break the shared date axis).
        self.fig.add_hline(y=0, line_width=1, line_color='grey')
        if self.threshold:
            self.fig.add_hline(y=self.threshold, line_width=1, line_dash='dot', line_color='green')
            self.fig.add_hline(y=-self.threshold, line_width=1, line_dash='dot', line_color='red')
        return self.fig
