import ta
import sys
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

# Minimal CSS color names used as indicator defaults / common config values.
# Only what we need — anything unknown falls back to a neutral grey.
_NAMED_RGB = {
    'grey': (128, 128, 128), 'gray': (128, 128, 128),
    'orange': (255, 165, 0), 'lightblue': (173, 216, 230),
    'blue': (0, 0, 255), 'red': (255, 0, 0), 'green': (0, 128, 0),
    'white': (255, 255, 255), 'black': (0, 0, 0), 'yellow': (255, 255, 0),
}


def _to_rgba(color, alpha):
    """Return an ``rgba(r,g,b,a)`` string for a color name / hex / rgb(a) string.

    Used so the Bollinger fill can be an explicit translucent wash: relying on
    the trace-level ``opacity`` makes Plotly render the fill almost solid, which
    hides the candles behind wide (high-volatility) bands.
    """
    try:
        c = (color or '').strip().lower()
        if c.startswith('#'):
            h = c[1:]
            if len(h) == 3:
                h = ''.join(ch * 2 for ch in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        elif c.startswith('rgb'):
            nums = c[c.find('(') + 1:c.find(')')].split(',')
            r, g, b = (int(float(nums[i])) for i in range(3))
        else:
            r, g, b = _NAMED_RGB.get(c, (128, 128, 128))
    except Exception:
        r, g, b = (128, 128, 128)
    return f'rgba({r},{g},{b},{alpha})'


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
        # Distinct default colours: a neutral grey collides with the grey EMA
        # band that heikin.py also fills (rgba(128,128,128,0.15)) — two overlapping
        # grey washes read as a muddy blob. A cool slow band + warm fast band stay
        # legible on both light and dark themes.
        self.color_slow_band = color_slow_band or '#5B8DEF'   # cool blue
        self.color_fast_band = color_fast_band or '#E0A030'   # warm amber
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

        self._add_band(self.slow_window, self.color_slow_band, self.fill_slow, fill_alpha=0.16)
        self._add_band(self.fast_window, self.color_fast_band, self.fill_fast, fill_alpha=0.18)

    def _add_band(self, window, color, fill, fill_alpha):
        """Add one Bollinger band (mid/upper/lower) to ``self.fig``.

        The interior is filled with explicit closed polygons (``fill='toself'``:
        upper edge forwards, lower edge backwards). Plotly distorts a fill that
        crosses an x-axis ``rangebreak`` (weekend/holiday/overnight gap) into a
        ballooning wedge — neither ``tonexty`` nor a single ``toself`` polygon is
        immune. So the fill is split into one sub-polygon per contiguous run of
        bars: the polygon never spans a time gap, hence never spans a collapsed
        break. The rule is asset-agnostic — it keys off the data's own bar
        spacing, so 24/7 assets (crypto) get one continuous band while stocks/FX
        get a band that breaks over non-trading periods (an honest marker).
        The dotted outline lines stay continuous (lines render fine across breaks)
        and carry the band's visibility on any theme even where the fill is faint.
        """
        line_rgba = _to_rgba(color, 0.9)
        line_w = 1.3 if fill else self.line_width

        # Drop the leading warmup rows where the bands are still NaN.
        band = self.df[['Date', f"bol_upper_{window}",
                        f"bol_lower_{window}", f"bol_mid_{window}"]].dropna().reset_index(drop=True)
        if band.empty:
            return

        dates = band['Date']
        upper = band[f"bol_upper_{window}"]
        lower = band[f"bol_lower_{window}"]
        mid   = band[f"bol_mid_{window}"]

        # Filled interior — split at time gaps so no polygon crosses a rangebreak.
        if fill:
            xs, ys = self.segmented_band(dates, upper, lower)
            self.fig.add_trace(go.Scatter(
                x = xs, y = ys,
                fill = 'toself',
                fillcolor = _to_rgba(color, fill_alpha),
                line = {'width': 0},
                hoverinfo = 'skip',
                name = f'{window} Bollinger band',
                showlegend = False),
              )

        # Upper / lower outline lines (continuous)
        for y_vals, label in ((upper, 'upper'), (lower, 'lower')):
            self.fig.add_trace(go.Scatter(x = dates,
                         y = y_vals,
                         line = {'color': line_rgba, 'dash': 'dot', 'width': line_w},
                         name = f'{label} {window} Bollinger band',
                         showlegend = False),
              )

        # Mid line (drawn last so it sits on top of the fill)
        self.fig.add_trace(go.Scatter(x = dates,
                         y = mid,
                         line = {'color': _to_rgba(color, 0.9), 'dash': 'dot', 'width': 1},
                         name = f'mid {window} Bollinger band',
                         showlegend = False),
              )

