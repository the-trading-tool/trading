import sys
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

class Candle(_indicator._Indicator):

    is_oszilator = False
    name = 'Candle Stick Chart'

    def __init__(self, df, symbol = ""):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
                
    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
    
        pass
        
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        self.fig.add_trace(
            go.Candlestick(
                    x = self.df['Date'],
                    open = self.df['Open'], 
                    high = self.df['High'],
                    low = self.df['Low'],
                    close = self.df['Close'],
                    showlegend = False,
                    name = 'Candlestick chart'
                ))

