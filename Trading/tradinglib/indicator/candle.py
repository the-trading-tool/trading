import sys
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

from tradinglib.indicator import _indicator

class Candle(_indicator._Indicator):

    is_oszilator = False
    name = 'Candle Stick Chart'

    def __init__(self, df, symbol = ""):
        super().__init__(df=df, symbol=symbol)
#        self.add_fig()
                
    def data(self):
    
        pass
        
    def add_fig(self):

        self.fig = go.Figure()
        try:
            self.df.reset_index(inplace=True)
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

