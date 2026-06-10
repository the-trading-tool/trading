import math
import plotly.graph_objects as go

from tradinglib.indicator import _indicator

_STEP_MAP = {'1/8': 0.125, '1/4': 0.25, '1/2': 0.5}

_R_COLORS = ['#c0392b', '#e74c3c', '#e57373', '#ef9a9a', '#ffcdd2', '#fce4ec', '#fce4ec', '#fce4ec']
_S_COLORS = ['#27ae60', '#2ecc71', '#81c784', '#a5d6a7', '#c8e6c9', '#e8f5e9', '#e8f5e9', '#e8f5e9']


def _auto_factor(price: float) -> float:
    """Scale factor so that price/factor lands in [100, 1000), giving consistent
    0.8-2.5 % spacing between adjacent Gann octant levels for any price magnitude."""
    if price <= 0:
        return 1.0
    return 10.0 ** (math.floor(math.log10(price)) - 2)


def _gann_levels(price: float, step: float, n: int, factor: float = 0.0):
    """Return (resistances, supports) — each a list of n floats.

    resistances[0] is the nearest level strictly above price (R1),
    supports[0] is the nearest level strictly below price (S1).
    """
    f   = factor if factor > 0 else _auto_factor(price)
    num = price / f
    sqn = math.sqrt(num)

    # kb: largest grid index where (kb*step)^2 * f is strictly below price
    kb = math.floor(sqn / step)
    while (kb * step) ** 2 * f >= price and kb > 0:
        kb -= 1
    # ka: smallest grid index where (ka*step)^2 * f is strictly above price
    ka = kb + 1
    while (ka * step) ** 2 * f <= price:
        ka += 1

    dec = max(0, 2 - int(math.floor(math.log10(max(f, 1e-10)))))

    resistances = [round((ka + i) * step * ((ka + i) * step) * f, dec) for i in range(n)]
    supports    = [round((kb - i) * step * ((kb - i) * step) * f, dec) for i in range(n)]
    return resistances, supports


def _fmt(value: float) -> str:
    """Format a numeric price value as a rounded string label."""
    if value >= 10_000:
        return f'{value:,.0f}'
    if value >= 1_000:
        return f'{value:.1f}'
    if value >= 100:
        return f'{value:.2f}'
    if value >= 10:
        return f'{value:.3f}'
    return f'{value:.4f}'


class Gan(_indicator._Indicator):

    is_oszilator = False
    name = 'Gann Levels'

    params = {
        'levels':    {'type': 'int',    'default': 5,       'min': 1, 'max': 8,
                      'label': 'Levels above/below'},
        'step_frac': {'type': 'select', 'default': '1/8',
                      'options': ['1/8', '1/4', '1/2'],     'label': 'Gann step'},
        'anchor':    {'type': 'select', 'default': 'close',
                      'options': ['close', 'hl2', 'hlc3'],  'label': 'Anchor price'},
    }

    def __init__(self, df, symbol='', levels=5, step_frac='1/8', anchor='close'):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.levels = max(1, min(8, int(levels)))
        self.step   = _STEP_MAP.get(str(step_frac), 0.125)
        self.anchor = str(anchor)
        self.data()

    def _anchor_price(self) -> float:
        """Determine the anchor price for Gann level calculations."""
        row = self.df.iloc[-1]
        if self.anchor == 'hl2':
            return (float(row['High']) + float(row['Low'])) / 2.0
        if self.anchor == 'hlc3':
            return (float(row['High']) + float(row['Low']) + float(row['Close'])) / 3.0
        return float(row['Close'])

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
        price = self._anchor_price()
        if price <= 0:
            return
        resistances, supports = _gann_levels(price, self.step, self.levels)
        for i, r in enumerate(resistances, 1):
            self.df[f'gan_r{i}'] = r
        for i, s in enumerate(supports, 1):
            self.df[f'gan_s{i}'] = s

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        for i in range(1, self.levels + 1):
            col_r = f'gan_r{i}'
            col_s = f'gan_s{i}'
            width = 2 if i == 1 else 1
            rc = _R_COLORS[min(i - 1, len(_R_COLORS) - 1)]
            sc = _S_COLORS[min(i - 1, len(_S_COLORS) - 1)]

            if col_r in self.df.columns:
                val = self.df[col_r].iloc[-1]
                self._add_hline_outside(
                    y=val,
                    text=f'GR{i}: {_fmt(val)}',
                    line_color=rc,
                    line_dash='dash',
                    line_width=width,
                )

            if col_s in self.df.columns:
                val = self.df[col_s].iloc[-1]
                self._add_hline_outside(
                    y=val,
                    text=f'GS{i}: {_fmt(val)}',
                    line_color=sc,
                    line_dash='dash',
                    line_width=width,
                )
