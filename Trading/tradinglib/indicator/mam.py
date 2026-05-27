import numpy as np
import plotly.graph_objects as go
import sys

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

from tradinglib.indicator import _indicator


class Mam(_indicator._Indicator):
    """Triple configurable Moving Average overlay (SMA / EMA / WMA / DEMA)."""

    name = 'MA Multi'
    is_oszilator = False

    params = {
        'ma1_type':   {'type': 'select', 'default': 'EMA',  'options': ['SMA', 'EMA', 'WMA', 'DEMA'], 'label': 'MA 1 type'},
        'ma1_period': {'type': 'int',    'default': 20,     'min': 2, 'max': 500, 'label': 'MA 1 period'},
        'ma1_color':  {'type': 'color',  'default': '',                                                'label': 'MA 1 color'},
        'ma2_type':   {'type': 'select', 'default': 'EMA',  'options': ['SMA', 'EMA', 'WMA', 'DEMA'], 'label': 'MA 2 type'},
        'ma2_period': {'type': 'int',    'default': 50,     'min': 2, 'max': 500, 'label': 'MA 2 period'},
        'ma2_color':  {'type': 'color',  'default': '',                                                'label': 'MA 2 color'},
        'ma3_type':   {'type': 'select', 'default': 'SMA',  'options': ['SMA', 'EMA', 'WMA', 'DEMA'], 'label': 'MA 3 type'},
        'ma3_period': {'type': 'int',    'default': 200,    'min': 2, 'max': 500, 'label': 'MA 3 period'},
        'ma3_color':  {'type': 'color',  'default': '',                                                'label': 'MA 3 color'},
    }

    _COLORS = ['orange', 'deepskyblue', 'tomato']

    def __init__(
        self,
        df,
        symbol='',
        ma1_type='EMA',  ma1_period=20,
        ma2_type='EMA',  ma2_period=50,
        ma3_type='SMA',  ma3_period=200,
    ):
        super().__init__(df=df, symbol=symbol)
        self.ma1_type   = ma1_type
        self.ma1_period = ma1_period
        self.ma2_type   = ma2_type
        self.ma2_period = ma2_period
        self.ma3_type   = ma3_type
        self.ma3_period = ma3_period
        self.data()

    def _calc_ma(self, series, length: int, ma_type: str):
        t = ma_type.upper()
        if t == 'SMA':
            return series.rolling(length).mean()
        elif t == 'EMA':
            return series.ewm(span=length, adjust=False).mean()
        elif t == 'WMA':
            w = np.arange(1, length + 1, dtype=float)
            return series.rolling(length).apply(
                lambda x: np.dot(x, w) / w.sum(), raw=True
            )
        elif t == 'DEMA':
            e1 = series.ewm(span=length, adjust=False).mean()
            e2 = e1.ewm(span=length, adjust=False).mean()
            return 2 * e1 - e2
        return series

    def data(self):
        cfg = [
            (self.ma1_type, self.ma1_period, 'ma1'),
            (self.ma2_type, self.ma2_period, 'ma2'),
            (self.ma3_type, self.ma3_period, 'ma3'),
        ]
        for ma_type, period, prefix in cfg:
            self.df[f'{prefix}_{ma_type}_{period}'] = self._calc_ma(
                self.df['Close'], period, ma_type
            )

    def add_fig(self):
        self.fig = go.Figure()
        cfg = [
            (self.ma1_type, self.ma1_period, 'ma1', self._COLORS[0]),
            (self.ma2_type, self.ma2_period, 'ma2', self._COLORS[1]),
            (self.ma3_type, self.ma3_period, 'ma3', self._COLORS[2]),
        ]
        x = self.df['Date'] if 'Date' in self.df.columns else self.df.index
        for ma_type, period, prefix, color in cfg:
            col = f'{prefix}_{ma_type}_{period}'
            if col in self.df.columns:
                self.fig.add_trace(go.Scatter(
                    x=x,
                    y=self.df[col],
                    mode='lines',
                    name=f'{ma_type} {period}',
                    showlegend=False,
                    line=dict(color=color, width=1.5),
                ))
