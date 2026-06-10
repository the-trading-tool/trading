import ta
import sys
import ta
import math
import pandas as pd
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator

class Sup(_indicator._Indicator):

    is_oszilator = False
    name = 'Support / Resistance'

    params = {
        'window':            {'type': 'int',   'default': 21, 'min': 2, 'max': 200, 'label': 'Lookback window'},
        'color_support':     {'type': 'color', 'default': '',  'label': 'Support line color'},
        'color_resistance':  {'type': 'color', 'default': '',  'label': 'Resistance line color'},
    }

    def __init__(self, df, symbol = ""):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.data()
                
    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
    
        (self.df['sup_support'], self.df['sup_resistance']) = indicator.support_resistance(self.df)

        pass
        
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        round_by = 1
        if self.df['Close'].iloc[-1] < 100:
            round_by = 2    
        if self.df['Close'].iloc[-1] < 10:
            round_by = 3    
        if self.df['Close'].iloc[-1] < 0.01:
            round_by = 4    
        if self.df['Close'].iloc[-1] > 1000:
            round_by = 0    
        if not pd.isna(self.df['sup_support'].iloc[-1]):
            self._add_hline_outside(
                y=self.df['sup_support'].iloc[-1],
                text=f"Support: {round(self.df['sup_support'].iloc[-1], round_by)}",
                line_color='green',
                line_dash='dash',
            )

        if not pd.isna(self.df['sup_resistance'].iloc[-1]):
            self._add_hline_outside(
                y=self.df['sup_resistance'].iloc[-1],
                text=f"Resistance: {round(self.df['sup_resistance'].iloc[-1], round_by)}",
                line_color='red',
                line_dash='dash',
            )

