"""Edge score as an oscillator — the scorecard total per trading day.

The numbers are the ones the Asset Viewer's Scorecard tab and the Edge Scores
pages show, evaluated on every day (see :mod:`tradinglib.edge_daily`):

* ``edge_total``  — −10 … +10, the total score.
* ``edge_trend``  — −4 … +4, trend & season.
* ``edge_rs``     — −2 … +2, relative strength against the index (single stocks).
* ``edge_market`` — −10 … +10, the index's score as market environment (single stocks).
* ``edge_macro``  — −6 … +6, macro score (the eight major currencies only).

Like ``prof``/``fps`` the indicator reloads the full local daily history — SMA200
and the ten-year seasonality need far more bars than a zoomed chart holds — and
caps it at the chart's last bar, so an 'as of' chart does not look ahead.

Live only: these columns are not stored in ``asset_simulation``, so a buy/sell
formula using them works in the chart but not in the Strategy Finder.
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

COLUMNS = ('edge_total', 'edge_trend', 'edge_rs', 'edge_market', 'edge_macro')

# Normalised to the total's scale (−10 … +10) when drawn as components.
_SCALE = {'edge_trend': 10 / 4, 'edge_rs': 10 / 2, 'edge_market': 1.0, 'edge_macro': 10 / 6}

_CACHE: dict = {}


class Edge(_indicator._Indicator):

    is_oszilator = True
    name = 'Edge score (scorecard total)'

    params = {
        'show_components': {'type': 'bool', 'default': False,
                            'label': 'Show components (scaled to -10..+10)'},
        'color_total':     {'type': 'color', 'default': '', 'label': 'Total score color'},
    }

    def __init__(self, df, symbol="", show_components=False, color_total=''):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.show_components = bool(show_components)
        self.color_total = color_total or 'royalblue'
        self.data()

    def _target_index(self) -> pd.DatetimeIndex:
        idx = pd.to_datetime(self.df.index, errors='coerce')
        if idx.isna().all() and 'Date' in self.df.columns:
            idx = pd.to_datetime(self.df['Date'], errors='coerce')
        return pd.DatetimeIndex(idx)

    def _scores(self, target: pd.DatetimeIndex) -> pd.DataFrame:
        from tradinglib import edge_daily as ed
        from tradinglib.market_data import _put_cache
        valid = target.dropna()
        if not self.symbol or valid.empty:
            return pd.DataFrame()
        valid = valid.tz_localize(None) if valid.tz is not None else valid
        # A bar covers its own period: a weekly bar dated Monday needs the rest of
        # its week (same cut as four_ps.cap_to_chart).
        spacing = pd.Series(valid).diff().median() if len(valid) > 1 else pd.Timedelta(days=1)
        if pd.isna(spacing) or spacing < pd.Timedelta(days=1):
            spacing = pd.Timedelta(days=1)
        start = valid.min().normalize() - pd.Timedelta(days=7)
        end = valid.max().normalize() + spacing - pd.Timedelta(days=1)
        today = pd.Timestamp.today().normalize()
        end = min(end, today)
        key = (self.symbol, start, end, today)
        if key not in _CACHE:
            _put_cache(_CACHE, key, ed.for_ticker(self.symbol, start, end, today.date()))
        return _CACHE[key]

    def data(self):
        """Attach the edge columns (daily values, forward-filled onto the chart's bars)."""
        target = self._target_index()
        try:
            scores = self._scores(target)
        except Exception:
            scores = pd.DataFrame()
        if scores is None or scores.empty:
            for column in COLUMNS:
                self.df[column] = np.nan
            return
        frame = scores[list(COLUMNS)].apply(pd.to_numeric, errors='coerce')
        tgt = target.tz_localize(None) if target.tz is not None else target
        projected = frame.reindex(frame.index.union(tgt.dropna())).ffill().reindex(tgt)
        for column in COLUMNS:
            self.df[column] = projected[column].to_numpy()

    def add_fig(self):
        """Total as a line with bias zones; components optionally, scaled to the total."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
        if 'Date' not in self.df.columns and len(self.df.columns):
            self.df = self.df.rename(columns={self.df.columns[0]: 'Date'})
        x = self.df['Date']

        if self.show_components:
            for column, color in (('edge_trend', 'seagreen'), ('edge_rs', 'darkorange'),
                                  ('edge_market', 'grey'), ('edge_macro', 'purple')):
                y = pd.to_numeric(self.df[column], errors='coerce')
                if y.notna().any():
                    self.fig.add_trace(go.Scatter(
                        x=x, y=y * _SCALE[column], name=column, showlegend=False, mode='lines',
                        line=dict(color=color, width=1, dash='dot', shape='hv')))

        total = pd.to_numeric(self.df['edge_total'], errors='coerce')
        self.fig.add_trace(go.Scatter(
            x=x, y=total.clip(lower=0), name='Edge +', showlegend=False, mode='lines',
            line=dict(width=0, shape='hv'), fill='tozeroy', fillcolor='rgba(46,139,87,0.18)',
            hoverinfo='skip'))
        self.fig.add_trace(go.Scatter(
            x=x, y=total.clip(upper=0), name='Edge -', showlegend=False, mode='lines',
            line=dict(width=0, shape='hv'), fill='tozeroy', fillcolor='rgba(178,34,34,0.18)',
            hoverinfo='skip'))
        self.fig.add_trace(go.Scatter(
            x=x, y=total, name='Edge score', showlegend=False, mode='lines',
            line=dict(color=self.color_total, width=2, shape='hv')))

        # Plain hlines only (see the note in prof.add_fig): bias thresholds ±2 / ±5.
        for level, color in ((5, 'green'), (2, 'seagreen'), (0, 'grey'), (-2, 'indianred'), (-5, 'firebrick')):
            self.fig.add_hline(y=level, line_width=1, line_dash='dot', line_color=color)
