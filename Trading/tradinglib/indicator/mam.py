import numpy as np
import plotly.graph_objects as go
import sys

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

_DASH_MAP = {'solid': 'solid', 'dashed': 'dash', 'dotted': 'dot'}


class Mam(_indicator._Indicator):
    """Up to 5 individually toggleable Moving Averages (SMA / EMA / WMA / DEMA)."""

    name = 'MA Multi'
    is_oszilator = False

    _MA_DEFAULTS = [
        # (enabled, type,  period, color)
        (True,  'EMA', 9,  ''),
        (True,  'EMA', 21,  ''),
        (True,  'SMA', 50, ''),
        (True, 'EMA', 100, ''),
        (True, 'EMA', 200, ''),
    ]
    _WIDTH_OPTIONS = ['0.5', '1', '1.5', '2']
    _STYLE_OPTIONS = ['solid', 'dashed', 'dotted']
    # Distinct, theme-neutral defaults (visible on light AND dark). Avoids the
    # old 'black' (invisible on dark) / 'grey' (weak, collides with band fills)
    # and keeps all five MAs clearly separable. Still overridable per config.
    _COLORS        = ['#E8890C', '#2E86DE', '#16A085', '#8E44AD', '#C0392B']
    #                  orange     blue       teal       violet     red

    params = {
        # MA 1
        'ma1_enabled': {'type': 'bool',   'default': True,    'label': 'MA 1 enabled'},
        'ma1_type':    {'type': 'select', 'default': 'EMA',   'options': ['SMA', 'EMA', 'WMA', 'DEMA'], 'label': 'MA 1 type'},
        'ma1_period':  {'type': 'int',    'default': 9,      'min': 2, 'max': 500, 'label': 'MA 1 period'},
        'ma1_color':   {'type': 'color',  'default': '',      'label': 'MA 1 color'},
        'ma1_width':   {'type': 'select', 'default': '1.5',   'options': ['0.5', '1', '1.5', '2'],        'label': 'MA 1 width'},
        'ma1_style':   {'type': 'select', 'default': 'solid', 'options': ['solid', 'dashed', 'dotted'],   'label': 'MA 1 style'},
        # MA 2
        'ma2_enabled': {'type': 'bool',   'default': True,    'label': 'MA 2 enabled'},
        'ma2_type':    {'type': 'select', 'default': 'EMA',   'options': ['SMA', 'EMA', 'WMA', 'DEMA'], 'label': 'MA 2 type'},
        'ma2_period':  {'type': 'int',    'default': 21,      'min': 2, 'max': 500, 'label': 'MA 2 period'},
        'ma2_color':   {'type': 'color',  'default': '',      'label': 'MA 2 color'},
        'ma2_width':   {'type': 'select', 'default': '1.5',   'options': ['0.5', '1', '1.5', '2'],        'label': 'MA 2 width'},
        'ma2_style':   {'type': 'select', 'default': 'solid', 'options': ['solid', 'dashed', 'dotted'],   'label': 'MA 2 style'},
        # MA 3
        'ma3_enabled': {'type': 'bool',   'default': True,    'label': 'MA 3 enabled'},
        'ma3_type':    {'type': 'select', 'default': 'SMA',   'options': ['SMA', 'EMA', 'WMA', 'DEMA'], 'label': 'MA 3 type'},
        'ma3_period':  {'type': 'int',    'default': 50,     'min': 2, 'max': 500, 'label': 'MA 3 period'},
        'ma3_color':   {'type': 'color',  'default': '',      'label': 'MA 3 color'},
        'ma3_width':   {'type': 'select', 'default': '1.5',   'options': ['0.5', '1', '1.5', '2'],        'label': 'MA 3 width'},
        'ma3_style':   {'type': 'select', 'default': 'solid', 'options': ['solid', 'dashed', 'dotted'],   'label': 'MA 3 style'},
        # MA 4
        'ma4_enabled': {'type': 'bool',   'default': True,   'label': 'MA 4 enabled'},
        'ma4_type':    {'type': 'select', 'default': 'SMA',   'options': ['SMA', 'EMA', 'WMA', 'DEMA'], 'label': 'MA 4 type'},
        'ma4_period':  {'type': 'int',    'default': 100,     'min': 2, 'max': 500, 'label': 'MA 4 period'},
        'ma4_color':   {'type': 'color',  'default': '',      'label': 'MA 4 color'},
        'ma4_width':   {'type': 'select', 'default': '1.5',   'options': ['0.5', '1', '1.5', '2'],        'label': 'MA 4 width'},
        'ma4_style':   {'type': 'select', 'default': 'solid', 'options': ['solid', 'dashed', 'dotted'],   'label': 'MA 4 style'},
        # MA 5
        'ma5_enabled': {'type': 'bool',   'default': True,   'label': 'MA 5 enabled'},
        'ma5_type':    {'type': 'select', 'default': 'SMA',   'options': ['SMA', 'EMA', 'WMA', 'DEMA'], 'label': 'MA 5 type'},
        'ma5_period':  {'type': 'int',    'default': 200,     'min': 2, 'max': 500, 'label': 'MA 5 period'},
        'ma5_color':   {'type': 'color',  'default': '',      'label': 'MA 5 color'},
        'ma5_width':   {'type': 'select', 'default': '1.5',   'options': ['0.5', '1', '1.5', '2'],        'label': 'MA 5 width'},
        'ma5_style':   {'type': 'select', 'default': 'solid', 'options': ['solid', 'dashed', 'dotted'],   'label': 'MA 5 style'},
    }

    def __init__(
        self,
        df,
        symbol='',
        ma1_enabled=True,  ma1_type='EMA', ma1_period=8,  ma1_width='1.5', ma1_style='solid', ma1_color='',
        ma2_enabled=True,  ma2_type='EMA', ma2_period=21,  ma2_width='1.5', ma2_style='solid', ma2_color='',
        ma3_enabled=True,  ma3_type='SMA', ma3_period=50, ma3_width='1.5', ma3_style='solid', ma3_color='',
        ma4_enabled=False, ma4_type='SMA', ma4_period=100, ma4_width='1.5', ma4_style='solid', ma4_color='',
        ma5_enabled=False, ma5_type='SMA', ma5_period=200, ma5_width='1.5', ma5_style='solid', ma5_color='',
    ):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.ma1_enabled = ma1_enabled; self.ma1_type = ma1_type; self.ma1_period = ma1_period
        self.ma1_width   = ma1_width;   self.ma1_style = ma1_style; self.ma1_color = ma1_color or self._COLORS[0]
        self.ma2_enabled = ma2_enabled; self.ma2_type = ma2_type; self.ma2_period = ma2_period
        self.ma2_width   = ma2_width;   self.ma2_style = ma2_style; self.ma2_color = ma2_color or self._COLORS[1]
        self.ma3_enabled = ma3_enabled; self.ma3_type = ma3_type; self.ma3_period = ma3_period
        self.ma3_width   = ma3_width;   self.ma3_style = ma3_style; self.ma3_color = ma3_color or self._COLORS[2]
        self.ma4_enabled = ma4_enabled; self.ma4_type = ma4_type; self.ma4_period = ma4_period
        self.ma4_width   = ma4_width;   self.ma4_style = ma4_style; self.ma4_color = ma4_color or self._COLORS[3]
        self.ma5_enabled = ma5_enabled; self.ma5_type = ma5_type; self.ma5_period = ma5_period
        self.ma5_width   = ma5_width;   self.ma5_style = ma5_style; self.ma5_color = ma5_color or self._COLORS[4]
        self.data()

    def _calc_ma(self, series, length: int, ma_type: str):
        """Compute a moving average with the configured period."""
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

    def _ma_configs(self):
        """Return the list of moving-average configurations."""
        return [
            (self.ma1_enabled, self.ma1_type, self.ma1_period, 'ma1', self.ma1_color, self.ma1_width, self.ma1_style),
            (self.ma2_enabled, self.ma2_type, self.ma2_period, 'ma2', self.ma2_color, self.ma2_width, self.ma2_style),
            (self.ma3_enabled, self.ma3_type, self.ma3_period, 'ma3', self.ma3_color, self.ma3_width, self.ma3_style),
            (self.ma4_enabled, self.ma4_type, self.ma4_period, 'ma4', self.ma4_color, self.ma4_width, self.ma4_style),
            (self.ma5_enabled, self.ma5_type, self.ma5_period, 'ma5', self.ma5_color, self.ma5_width, self.ma5_style),
        ]

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
        for enabled, ma_type, period, prefix, *_ in self._ma_configs():
            if enabled:
                self.df[f'{prefix}_{ma_type}_{period}'] = self._calc_ma(
                    self.df['Close'], period, ma_type
                )

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        x = self.df['Date'] if 'Date' in self.df.columns else self.df.index
        for enabled, ma_type, period, prefix, color, width, style in self._ma_configs():
            if not enabled:
                continue
            col = f'{prefix}_{ma_type}_{period}'
            if col not in self.df.columns:
                continue
            self.fig.add_trace(go.Scatter(
                x=x,
                y=self.df[col],
                mode='lines',
                name=f'{ma_type} {period}',
                showlegend=False,
                line=dict(
                    color=color,
                    width=float(width),
                    dash=_DASH_MAP.get(style, 'solid'),
                ),
            ))
            try:
                last_val = self.df[col].dropna().iloc[-1]
                self.fig.add_annotation(
                    x=1.0,
                    y=last_val,
                    xref='paper',
                    yref='y',
                    text=f' {ma_type} {period} ',
                    showarrow=False,
                    xanchor='left',
                    yanchor='middle',
                    bgcolor=color,
                    font=dict(color='white', size=13),
                    bordercolor=color,
                    borderpad=3,
                    opacity=0.9,
                )
            except Exception:
                pass
