import ta
import sys
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

from tradinglib.indicator import _indicator

class Don(_indicator._Indicator):

    is_oszilator = False
    name = 'Donchian Channel'

    params = {
        'period': {'type': 'int', 'default': 10, 'min': 2, 'max': 200, 'label': 'Channel period'},
    }

    def __init__(self, df, symbol = "", period = 10):
        super().__init__(df=df, symbol=symbol)
        self.period = period
        self.data()
#        self.add_fig()
        
        
    def data(self): # Doncian Channel
        self.df["don_upper"] = self.df["High"].rolling(self.period).max()
        self.df["don_lower"] = self.df["Low"].rolling(self.period).min()
        self.df["don_mid"] = (self.df["don_upper"] + self.df["don_lower"]) / 2
        
    def add_fig(self):

        self.fig = go.Figure()
        try:
            self.df.reset_index(inplace=True)
        except Exception:
            pass

        self.fig.add_trace(go.Scatter(x=self.df['Date'],
                         y=self.df['don_upper'],
                         name = 'Upper Donchian',
                         line=dict(color='darkblue', width=1),
                         showlegend = False,
#                         visible = "legendonly",
                        ))
        
        self.fig.add_trace(go.Scatter(x=self.df['Date'],
                         y=self.df['don_lower'],
                         name = 'Lower Donchian',
                         line=dict(color='blue', width=1),
                         showlegend = False,
#                         visible = "legendonly",
                        ))

        self.fig.add_trace(go.Scatter(x=self.df['Date'],
                         y=self.df['don_mid'],
                         name = 'Mid Donchian',
                         showlegend = False,
                         line=dict(dash='dot',color='blue', width=1)
                        ))
