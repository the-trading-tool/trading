import sys
try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

import plotly.graph_objects as go
from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator
from tradinglib import predictlib as pl

class Pre(_indicator._Indicator):

    is_oszilator = False
    name = 'Predict target range'

    def __init__(self, df, symbol = ""):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.data()
                
    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
	
        self.pr_low = 0
        self.pr_high = 0
        try:
            p = pl.predict(precise = True)
            p.df = self.df.filter(['Open','Close','High','Low','Volume','ema9','ema21'])
            p.prepare_lag()
            p.load_model()
            nd = pl.np.sort(p.next_days)
            self.pr_low = int(nd[0]*100)/100
            self.pr_high = int(nd[-1]*100)/100
        
        except Exception:
            pass
        
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
            
        self._add_hline_outside(
            y=self.pr_high,
            text=f'Pred H: {self.pr_high}',
            line_color='blue',
            line_dash='dash',
            line_width=1,
        )
        self.fig.add_hrect(y0=self.pr_low, y1=self.pr_high, line_width=0, fillcolor="blue", opacity=0.2)
        self._add_hline_outside(
            y=self.pr_low,
            text=f'Pred L: {self.pr_low}',
            line_color='darkblue',
            line_dash='dash',
            line_width=1,
        )

