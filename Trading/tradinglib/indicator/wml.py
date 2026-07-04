import numpy as np
import pandas as pd
import plotly.graph_objects as go

from tradinglib.indicator import _indicator


class Wml(_indicator._Indicator):

    is_oszilator = False
    name = 'Week/Month Levels'

    params = {
        'show_week':    {'type': 'bool',  'default': True,  'label': 'Show previous week H/L'},
        'show_month':   {'type': 'bool',  'default': True,  'label': 'Show previous month H/L'},
        'color_week':   {'type': 'color', 'default': '',    'label': 'Week levels color'},
        'color_month':  {'type': 'color', 'default': '',    'label': 'Month levels color'},
    }

    def __init__(self, df, symbol='', show_week=True, show_month=True,
                 color_week='', color_month=''):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.show_week   = show_week
        self.show_month  = show_month
        self.color_week  = color_week  or 'darkorange'
        self.color_month = color_month or 'crimson'
        self.data()

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
        work = self.df.copy()

        if 'Date' in work.columns:
            work = work.set_index('Date')
        work.index = pd.to_datetime(work.index)

        wk_hi_s = work.groupby(work.index.to_period('W'))['High'].max()
        wk_lo_s = work.groupby(work.index.to_period('W'))['Low'].min()
        mo_hi_s = work.groupby(work.index.to_period('M'))['High'].max()
        mo_lo_s = work.groupby(work.index.to_period('M'))['Low'].min()

        self.df['wml_wk_hi'] = float(wk_hi_s.iloc[-2]) if len(wk_hi_s) >= 2 else np.nan
        self.df['wml_wk_lo'] = float(wk_lo_s.iloc[-2]) if len(wk_lo_s) >= 2 else np.nan
        self.df['wml_mo_hi'] = float(mo_hi_s.iloc[-2]) if len(mo_hi_s) >= 2 else np.nan
        self.df['wml_mo_lo'] = float(mo_lo_s.iloc[-2]) if len(mo_lo_s) >= 2 else np.nan

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        x = self.df['Date']

        if self.show_week:
            for col, label in [('wml_wk_hi', 'Prev Week High'), ('wml_wk_lo', 'Prev Week Low')]:
                self.fig.add_trace(go.Scatter(
                    x=x, y=self.df[col],
                    name=label,
                    line=dict(color=self.color_week, width=1, dash='dot'),
                    showlegend=False,
                ))

        if self.show_month:
            for col, label in [('wml_mo_hi', 'Prev Month High'), ('wml_mo_lo', 'Prev Month Low')]:
                self.fig.add_trace(go.Scatter(
                    x=x, y=self.df[col],
                    name=label,
                    line=dict(color=self.color_month, width=1, dash='dash'),
                    showlegend=False,
                ))
