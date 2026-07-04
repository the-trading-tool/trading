import ta
import sys
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

class Bol(_indicator._Indicator):

    is_oszilator = False
    name = 'Bollinger Bands'

    params = {
        'slow_window':       {'type': 'int',   'default': 21,  'min': 2, 'max': 200, 'label': 'Slow window'},
        'fast_window':       {'type': 'int',   'default': 9,   'min': 2, 'max': 100, 'label': 'Fast window'},
        'slow_dev':          {'type': 'float', 'default': 2.0,            'label': 'Slow deviation'},
        'fast_dev':          {'type': 'float', 'default': 0.2,            'label': 'Fast deviation'},
        'fill_slow':         {'type': 'bool',  'default': True,           'label': 'Fill slow band'},
        'fill_fast':         {'type': 'bool',  'default': True,           'label': 'Fill fast band'},
        'color_slow_band':   {'type': 'color', 'default': '',             'label': 'Slow band color'},
        'color_fast_band':   {'type': 'color', 'default': '',             'label': 'Fast band color'},
    }

    def __init__(self, df, symbol = "", slow_window=21, fast_window=9, slow_dev=2, fast_dev=0.2,
                 fill_slow=True, fill_fast=True,
                 color_slow_band='', color_fast_band='',
                 line_width=2, **kwargs):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.slow_window = slow_window
        self.fast_window = fast_window
        self.slow_dev = slow_dev
        self.fast_dev = fast_dev
        self.fill_slow = fill_slow
        self.fill_fast = fill_fast
        self.color_slow_band = color_slow_band or 'grey'
        self.color_fast_band = color_fast_band or 'grey'
        self.line_width = line_width

        self.data()
        
        
    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
    
        # slow indicator
        indicator_bb = ta.volatility.BollingerBands(close=self.df["Close"], 
                                            window=self.slow_window, window_dev=self.slow_dev)
        self.df[f'bol_mid_{self.slow_window}']   = indicator_bb.bollinger_mavg()   # Middle Band
        self.df[f'bol_upper_{self.slow_window}'] = indicator_bb.bollinger_hband()  # Upper Band
        self.df[f'bol_lower_{self.slow_window}'] = indicator_bb.bollinger_lband()  # Lower Band

        # fast indicator
        indicator_bb = ta.volatility.BollingerBands(close=self.df["Close"],
                                            window=self.fast_window, window_dev=self.fast_dev)
        self.df[f'bol_mid_{self.fast_window}']   = indicator_bb.bollinger_mavg()   # Middle Band
        self.df[f'bol_upper_{self.fast_window}'] = indicator_bb.bollinger_hband()  # Upper Band
        self.df[f'bol_lower_{self.fast_window}'] = indicator_bb.bollinger_lband()  # Lower Band
        
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        slow_bw = 0.5 if self.fill_slow else self.line_width
        fast_bw = 0.5 if self.fill_fast else self.line_width

        # Mid
        self.fig.add_trace(go.Scatter(x = self.df['Date'],
                         y = self.df[f"bol_mid_{self.slow_window}"],
                         line_color = self.color_slow_band,
                         line = {'dash': 'dot', 'width': 1 if self.fill_slow else self.line_width},
                         name = f'mid {self.slow_window} Bollinger band',
                         showlegend = False,
                         opacity = 0.2),
              )

        # Upper Bound
        self.fig.add_trace(go.Scatter(x = self.df['Date'],
                         y = self.df[f"bol_upper_{self.slow_window}"],
                         line_color = self.color_slow_band,
                         line = {'dash': 'dot', 'width': slow_bw},
                         name = f'upper {self.slow_window} Bollinger band',
                         showlegend = False,
                         opacity = 0.2),
              )

        # Lower Bound
        self.fig.add_trace(go.Scatter(x = self.df['Date'],
                         y = self.df[f"bol_lower_{self.slow_window}"],
                         line_color = self.color_slow_band,
                         line = {'dash': 'dot', 'width': slow_bw},
                         fill = 'tonexty' if self.fill_slow else 'none',
                         name = f'lower {self.slow_window} Bollinger band',
                         showlegend = False,
                         opacity = 0.2),
              )


        # mid
        self.fig.add_trace(go.Scatter(x = self.df['Date'],
                         y = self.df[f"bol_mid_{self.fast_window}"],
                         line_color = self.color_fast_band,
                         line = {'dash': 'dot', 'width': 1 if self.fill_fast else self.line_width},
                         name = f'mid {self.fast_window} Bollinger band',
                         showlegend = False,
                         opacity = 0.2),
              )

        # Upper Bound
        self.fig.add_trace(go.Scatter(x = self.df['Date'],
                         y = self.df[f"bol_upper_{self.fast_window}"],
                         line_color = self.color_fast_band,
                         line = {'dash': 'dot', 'width': fast_bw},
                         name = f'upper {self.fast_window} Bollinger band',
                         showlegend = False,
                         opacity = 0.2),
              )

        # Lower Bound fill in between with parameter 'fill': 'tonexty'
        self.fig.add_trace(go.Scatter(x = self.df['Date'],
                         y = self.df[f"bol_lower_{self.fast_window}"],
                         line_color = self.color_fast_band,
                         line = {'dash': 'dot', 'width': fast_bw},
                         fill = 'tonexty' if self.fill_fast else 'none',
                         name = f'lower {self.fast_window} Bollinger band',
                         showlegend = False,
                         opacity = 0.2),
              )

