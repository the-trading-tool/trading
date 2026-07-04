import pandas as pd
import numpy as np
import plotly.graph_objects as go
from tradinglib.indicator import _indicator

class Nsdt(_indicator._Indicator):
    is_oszilator = False
    name = 'NSDT HAMA Candles'

    params = {
        'open_len':  {'type': 'int', 'default': 21,  'min': 2, 'max': 200, 'label': 'Open MA length'},
        'close_len': {'type': 'int', 'default': 14,  'min': 2, 'max': 200, 'label': 'Close MA length'},
        'ma_len':    {'type': 'int', 'default': 100, 'min': 5, 'max': 500, 'label': 'Trend MA length'},
        'steps':     {'type': 'int', 'default': 5,   'min': 1, 'max': 20,  'label': 'Color gradient steps'},
    }

    def __init__(self, df, symbol="", open_len=21, close_len=14, ma_len=100, steps=5):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.open_len = open_len
        self.close_len = close_len
        self.ma_len = ma_len
        self.steps = steps
        self.data()

    def _calculate_ma(self, series, length, ma_type):
        """Compute the moving average series for the indicator."""
        if ma_type == 'EMA':
            return series.ewm(span=length, adjust=False).mean()
        elif ma_type == 'WMA':
            weights = np.arange(1, length + 1)
            return series.rolling(length).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
        else: # SMA default
            return series.rolling(length).mean()

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
        # 0. Compute auxiliary variables (hl2 for the MA line)
        self.df['hl2'] = (self.df['High'] + self.df['Low']) / 2

        # 1. Compute HAMA candle components
        # Source Open: (open[1] + close[1]) / 2
        src_open = (self.df['Open'].shift(1) + self.df['Close'].shift(1)) / 2
        self.df['hama_open'] = self._calculate_ma(src_open, self.open_len, 'EMA')

        # Source High/Low/Close based on the Pine Script definitions
        src_high = np.maximum(self.df['High'], self.df['Close'])
        src_low = np.minimum(self.df['Low'], self.df['Close'])
        src_close = (self.df['Open'] + self.df['High'] + self.df['Low'] + self.df['Close']) / 4
        
        self.df['hama_high'] = self._calculate_ma(src_high, 20, 'EMA')
        self.df['hama_low'] = self._calculate_ma(src_low, 20, 'EMA')
        self.df['hama_close'] = self._calculate_ma(src_close, self.close_len, 'EMA')
        
        # 2. Main MA Line (uses the computed hl2)
        self.df['ma_line'] = self._calculate_ma(self.df['hl2'], self.ma_len, 'EMA')

        # 3. Gradient logic (f_c_gradientAdvDecPro)
        ma_basis = self.df['ma_line']
        ma_center = ma_basis.ewm(span=3, adjust=False).mean()

        qty = np.zeros(len(self.df))
        colors = ["rgba(0,0,0,0)"] * len(self.df)  # transparent by default

        # We must iterate because qty depends on the previous bar (like 'var' in Pine)
        for i in range(1, len(self.df)):
            if pd.isna(ma_basis.iloc[i]) or pd.isna(ma_center.iloc[i]):
                continue
                
            prev_qty = qty[i-1]
            curr_val = ma_basis.iloc[i]
            center_val = ma_center.iloc[i]
            change = curr_val - ma_basis.iloc[i-1]
            
            # Crossover/crossunder logic
            x_up = (ma_basis.iloc[i] > ma_center.iloc[i]) and (ma_basis.iloc[i-1] <= ma_center.iloc[i-1])
            x_dn = (ma_basis.iloc[i] < ma_center.iloc[i]) and (ma_basis.iloc[i-1] >= ma_center.iloc[i-1])

            if curr_val > center_val:  # Bullish zone
                if x_up: qty[i] = 1
                elif change > 0: qty[i] = min(self.steps, prev_qty + 1)
                elif change < 0: qty[i] = max(1, prev_qty - 1)
                else: qty[i] = prev_qty

                # From neutral (yellow) to strong bull (green)
                ratio = qty[i] / self.steps
                colors[i] = f'rgb({int(255*(1-ratio))}, 255, 0)'

            elif curr_val < center_val:  # Bearish zone
                if x_dn: qty[i] = 1
                elif change < 0: qty[i] = min(self.steps, prev_qty + 1)
                elif change > 0: qty[i] = max(1, prev_qty - 1)
                else: qty[i] = prev_qty

                # From neutral (yellow) to strong bear (red)
                ratio = qty[i] / self.steps
                colors[i] = f'rgb(255, {int(255*(1-ratio))}, 0)'
            else:
                qty[i] = prev_qty
                colors[i] = colors[i-1]

        self.df['hama_color'] = colors

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
        

        # Implementation using the gradient colour (hama_color):
        for i, color in enumerate(self.df['hama_color']):
            if i == 0 or pd.isna(self.df['hama_open'].iloc[i]):
                continue

            # We draw each candle as a small segment to retain full control over the colour.
            # This is the only way to achieve TRUE per-candle gradients in Plotly.
            self.fig.add_trace(go.Candlestick(
                x=[self.df['Date'].iloc[i]],
                open=[self.df['hama_open'].iloc[i]],
                high=[self.df['hama_high'].iloc[i]],
                low=[self.df['hama_low'].iloc[i]],
                close=[self.df['hama_close'].iloc[i]],
                increasing_line_color=color,
                decreasing_line_color=color,
                increasing_fillcolor=color,
                decreasing_fillcolor=color,
                showlegend=False
            ))


        # 2. Draw the MA line as a gradient (segment by segment)
        # Loop through the data and draw one line to the next point per step
        for i in range(1, len(self.df)):
            if pd.isna(self.df['ma_line'].iloc[i]) or pd.isna(self.df['ma_line'].iloc[i-1]):
                continue

            # Colour of the current segment
            seg_color = self.df['hama_color'].iloc[i]
            
            self.fig.add_trace(go.Scatter(
                x=[self.df['Date'].iloc[i-1], self.df['Date'].iloc[i]],
                y=[self.df['ma_line'].iloc[i-1], self.df['ma_line'].iloc[i]],
                mode='lines',
                line=dict(color=seg_color, width=3),
                hoverinfo='none',
                showlegend=False
            ))
