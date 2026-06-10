import ta
import sys
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

class Fib(_indicator._Indicator):

    is_oszilator = False
    name = 'Fibonacci Levels'

    params = {
        'period': {'type': 'int', 'default': 100, 'min': 10, 'max': 1000, 'label': 'Lookback period (bars)'},
    }

    def __init__(self, df, symbol = "", period = 100):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.period = period
                
    def data(self): # Fibonacci
        
        pass
        
    def add_fig(self):
        """Compute the indicator values and attach them as columns to self.df."""
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
			
        max_value = self.df['Close'][-self.period:].max()
        min_value = self.df['Close'][-self.period:].min()

        # Fibonacci constants
        difference = max_value - min_value

        # Set Fibonacci levels
#        up_third = max_value + difference * 0.5
#        up_second = max_value + difference * 0.382
        up_first = max_value + difference * 0.236
        first_level = max_value - difference * 0.236
        second_level = max_value - difference * 0.382
        third_level = max_value - difference * 0.5
        fourth_level = max_value - difference * 0.618
        low_first = min_value - difference * 0.236
#        low_second = min_value - difference * 0.382
#        low_third =  min_value - difference * 0.5
        
    # Plot Fibonacci graph
        self._add_hline_outside(y=up_first,     text=f'+23.6% {round(up_first, 1)}',     line_color='lightgreen', line_dash='dash', line_width=1, font_color='black')
        self._add_hline_outside(y=low_first,    text=f'-23.6% {round(low_first, 1)}',    line_color='brown',      line_dash='dash', line_width=1)
        self._add_hline_outside(y=up_first,     text=f'+23.6% {round(up_first, 1)}',     line_color='royalblue',  line_dash='dash', line_width=1)
        self._add_hline_outside(y=max_value,    text=f'0% {round(max_value, 1)}',        line_color='purple',     line_dash='dash', line_width=1)
        self.fig.add_hrect(y0=first_level, y1=max_value, line_width=0, fillcolor="blue", opacity=0.2)
        self._add_hline_outside(y=first_level,  text=f'23.6% {round(first_level, 1)}',  line_color='blue',       line_dash='dash', line_width=1)
        self.fig.add_hrect(y0=second_level, y1=first_level, line_width=0, fillcolor="green", opacity=0.2)
        self._add_hline_outside(y=second_level, text=f'38.2% {round(second_level, 1)}', line_color='green',      line_dash='dash', line_width=1)
        self.fig.add_hrect(y0=third_level, y1=second_level, line_width=0, fillcolor="red", opacity=0.2)
        self._add_hline_outside(y=third_level,  text=f'50% {round(third_level, 1)}',    line_color='red',        line_dash='dash', line_width=1)
        self.fig.add_hrect(y0=fourth_level, y1=third_level, line_width=0, fillcolor="orange", opacity=0.2)
        self.fig.add_hrect(y0=fourth_level, y1=second_level, line_width=0, fillcolor="orange", opacity=0.2)
        self._add_hline_outside(y=fourth_level, text=f'61.8% {round(fourth_level, 1)}', line_color='orange',     line_dash='dash', line_width=1, font_color='black')
        self.fig.add_hrect(y0=min_value, y1=fourth_level, line_width=0, fillcolor="yellow", opacity=0.2)
        self._add_hline_outside(y=min_value,    text=f'100% {round(min_value, 1)}',     line_color='black',      line_dash='dash', line_width=1)

