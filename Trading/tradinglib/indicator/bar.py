import sys
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

from tradinglib.indicator import _indicator


class Bar(_indicator._Indicator):

    is_oszilator = False
    name = 'OHLC Bar Chart'

    params = {
        'color_up':      {'type': 'color',  'default': '',    'label': 'Up bar color (empty = default)'},
        'color_down':    {'type': 'color',  'default': '',    'label': 'Down bar color (empty = default)'},
        'show_volume':   {'type': 'bool',   'default': False, 'label': 'Show volume bars'},
        'volume_opacity':{'type': 'int',    'default': 4,     'label': 'Volume opacity (1–10)', 'min': 1, 'max': 10},
    }

    def __init__(self, df, symbol="", color_up='', color_down='', show_volume=False, volume_opacity=4):
        super().__init__(df=df, symbol=symbol)
        self.color_up = color_up or 'limegreen'
        self.color_down = color_down or 'indianred'
        self.show_volume = show_volume
        self.volume_opacity = max(1, min(10, volume_opacity)) / 10
        self.add_fig()

    def data(self):
        pass

    def add_fig(self):
        self.fig = go.Figure()

        df = self.df.copy()
        try:
            df.reset_index(inplace=True)
        except Exception:
            pass

        self.fig.add_trace(
            go.Ohlc(
                x=df['Date'],
                open=df['Open'],
                high=df['High'],
                low=df['Low'],
                close=df['Close'],
                name='OHLC Bar',
                showlegend=False,
                increasing_line_color=self.color_up,
                decreasing_line_color=self.color_down,
            )
        )

        if self.show_volume and 'Volume' in df.columns:
            colors = [
                self.color_up if (c >= o) else self.color_down
                for c, o in zip(df['Close'], df['Open'])
            ]
            self.fig.add_trace(
                go.Bar(
                    x=df['Date'],
                    y=df['Volume'],
                    name='Volume',
                    showlegend=False,
                    marker_color=colors,
                    opacity=self.volume_opacity,
                    yaxis='y2',
                )
            )
            self.fig.update_layout(
                yaxis2=dict(
                    overlaying='y',
                    side='right',
                    showticklabels=False,
                    showgrid=False,
                )
            )
