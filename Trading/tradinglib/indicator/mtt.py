"""Minervini Trend Template — live oscillator with the engine's column names.

The seven per-ticker conditions come from :mod:`tradinglib.minervini` on the
full local daily history (like ``prof`` and ``fps``: the 200-day slope and the
52-week range need more bars than a zoomed chart holds, and only the full
history makes the live number equal the stored one).

The RS rating ``mtt_rs`` ranks a ticker against its whole market and therefore
cannot be computed from one ticker. It is read from ``asset_simulation_*.db``,
where asset_perf2 writes it after each run; a bar newer than the last stored
rating (today, intraday) shows the last known value.
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

# Kept in sync with asset_perf2.INDICATOR_BACKFILL_MAP['mtt'] plus the
# cross-sectional rating.
COLUMNS = ('mtt_sma150', 'mtt_slope200', 'mtt_high52', 'mtt_low52',
           'mtt_rs_raw', 'mtt_score', 'mtt_pass', 'mtt_rs')


class Mtt(_indicator._Indicator):

    is_oszilator = True
    name = 'Minervini Trend Template'

    params = {
        'color_rs':    {'type': 'color', 'default': '', 'label': 'RS rating color'},
        'color_score': {'type': 'color', 'default': '', 'label': 'Template score color'},
    }

    def __init__(self, df, symbol="", color_rs='', color_score=''):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.color_rs = color_rs or 'royalblue'
        self.color_score = color_score or 'seagreen'
        self.data()

    def _target_index(self) -> pd.DatetimeIndex:
        idx = pd.to_datetime(self.df.index, errors='coerce')
        if idx.isna().all() and 'Date' in self.df.columns:
            idx = pd.to_datetime(self.df['Date'], errors='coerce')
        return pd.DatetimeIndex(idx)

    def _history(self, target: pd.DatetimeIndex) -> pd.Series:
        """Full local daily closes, or the chart's own closes on daily frames."""
        if self.symbol:
            try:
                from tradinglib import four_ps
                hist = four_ps.load_daily(self.symbol)
                if hist is not None and not hist.empty:
                    return hist['Close']
            except Exception:
                pass
        if len(target) < 2 or 'Close' not in self.df.columns:
            return pd.Series(dtype=float)
        # Weekly bars would turn 200 days into 200 weeks — daily or finer only.
        spacing = pd.Series(target).diff().dt.total_seconds().median()
        if not spacing or spacing > 4 * 24 * 3600:
            return pd.Series(dtype=float)
        return pd.Series(pd.to_numeric(self.df['Close'], errors='coerce').to_numpy(),
                         index=target).dropna()

    def data(self):
        """Attach the template columns and the stored RS rating to self.df."""
        from tradinglib import minervini

        target = self._target_index()
        try:
            close = self._history(target)
        except Exception:
            close = pd.Series(dtype=float)
        if close.empty or target.isna().all():
            for column in COLUMNS:
                self.df[column] = np.nan
            return

        frame = minervini.trend_template(close.sort_index())
        try:
            years = range(int(target.min().year), int(target.max().year) + 1)
            rs = minervini.stored_rs(self.symbol, years) if self.symbol else pd.Series(dtype=float)
        except Exception:
            rs = pd.Series(dtype=float)
        # Levels, not events — carry the last daily value onto the chart's bars.
        # The rating is carried on its own so a fresh bar without a stored rating
        # still shows the previous one.
        projected = frame.reindex(target, method='ffill')
        projected['mtt_rs'] = (rs.reindex(target.normalize(), method='ffill').to_numpy()
                               if not rs.empty else np.nan)
        for column in COLUMNS:
            self.df[column] = projected[column].to_numpy()

    def add_fig(self):
        """RS rating as a line, template score (0..7, scaled to 0..100) as bars."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
        if 'Date' not in self.df.columns and len(self.df.columns):
            self.df = self.df.rename(columns={self.df.columns[0]: 'Date'})
        x = self.df['Date']
        score = pd.to_numeric(self.df['mtt_score'], errors='coerce')

        self.fig.add_trace(go.Bar(
            x=x, y=score / 7 * 100, name='Template score', showlegend=False,
            marker_color=self.color_score, opacity=0.35,
            customdata=score, hovertemplate='Template %{customdata:.0f}/7<extra></extra>'))
        self.fig.add_trace(go.Scatter(
            x=x, y=self.df['mtt_rs'], name='RS rating', showlegend=False,
            mode='lines', line=dict(color=self.color_rs, width=2)))

        # Plain hlines only (see ovt.py: annotations break the shared date axis).
        self.fig.add_hline(y=70, line_width=1, line_dash='dot', line_color='green')
