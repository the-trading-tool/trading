import sys
import math
import numpy as np
import plotly.graph_objects as go
from numpy.lib.stride_tricks import sliding_window_view

try:
    sys.path.insert(0, "../../tradinglib.indicators")
except ImportError:
    pass

from tradinglib.indicator import _indicator


class Hbo(_indicator._Indicator):

    is_oszilator = True
    name = 'Hull Butterfly Oscillator'

    params = {
        'length': {'type': 'int',   'default': 14,  'min': 4,   'max': 200,  'label': 'Length'},
        'mult':   {'type': 'float', 'default': 2.0, 'min': 0.1, 'max': 10.0, 'label': 'Levels multiplier'},
    }

    def __init__(self, df, symbol="", length=14, mult=2.0):
        self.length = int(length)
        self.mult   = float(mult)
        super().__init__(df=df, symbol=symbol)
        self.data()

    def _hull_coeffs(self):
        """Compute Hull Moving Average convolution coefficients (mirrors Pine barstate.isfirst block)."""
        length   = self.length
        short_len = length // 2
        hull_len  = int(math.sqrt(length))

        den1 = short_len * (short_len + 1) / 2
        den2 = length    * (length    + 1) / 2
        den3 = hull_len  * (hull_len  + 1) / 2

        # Step 1: linearly combined WMA coefficients — zero-initialised, then unshift
        lcwa = [0.0] * hull_len
        for i in range(length):
            sum1 = max(short_len - i, 0)
            sum2 = length - i
            lcwa.insert(0, 2.0 * (sum1 / den1) - (sum2 / den2))

        # Step 2: zero-pad at front (hull_len - 1 times)
        for _ in range(hull_len - 1):
            lcwa.insert(0, 0.0)

        # Step 3: WMA convolution of lcwa with itself
        hull = []
        n = len(lcwa)
        for i in range(hull_len, n):
            s = sum(lcwa[j] * (i - j) for j in range(i - hull_len, i))
            hull.insert(0, s / den3)

        return np.array(hull)

    def data(self):
        coeffs   = self._hull_coeffs()
        n_coeffs = len(coeffs)
        close    = self.df['Close'].values.astype(float)
        n        = len(close)

        if n < n_coeffs:
            self.df['hbo_hso']      = np.nan
            self.df['hbo_cmean']    = np.nan
            self.df['hbo_os']       = 0
            self.df['hbo_bull_dot'] = np.nan
            self.df['hbo_bear_dot'] = np.nan
            return

        # Sliding windows: windows[t, j] = close[t - (n_coeffs-1) + j]
        close_ext = np.concatenate([np.full(n_coeffs - 1, np.nan), close])
        windows   = sliding_window_view(close_ext, n_coeffs)  # (n, n_coeffs)

        # hma[t]     = Σ close[t-i]          * coeffs[i]  (coeffs[0] = most recent weight)
        # inv_hma[t] = Σ close[t-(n-1-i)]    * coeffs[i]  (mirrored window, same weights)
        hma     = windows @ coeffs[::-1]
        inv_hma = windows @ coeffs
        hso     = hma - inv_hma

        # cmean = cumulative mean of |hso| * mult  (Pine: ta.cum(abs(hso)) / bar_index)
        abs_hso = np.where(np.isnan(hso), 0.0, np.abs(hso))
        cmean   = np.cumsum(abs_hso) / np.arange(1, n + 1) * self.mult

        # State machine (os) — sequential, matches Pine ternary chain exactly
        os = np.zeros(n, dtype=int)
        for t in range(1, n):
            if np.isnan(hso[t]) or np.isnan(hso[t - 1]):
                os[t] = 0
                continue
            h, hp = hso[t], hso[t - 1]
            c, cp = cmean[t], cmean[t - 1]
            # ta.cross(hso, cmean) or ta.cross(hso, -cmean)
            cross = (
                (hp < cp and h >= c) or (hp > cp and h <= c) or
                (hp < -cp and h >= -c) or (hp > -cp and h <= -c)
            )
            if cross:
                os[t] = 0
            elif h < hp and h > c:
                os[t] = -1
            elif h > hp and h < -c:
                os[t] = 1
            else:
                os[t] = os[t - 1]

        os_prev = np.roll(os, 1)
        os_prev[0] = 0

        self.df['hbo_hso']      = hso
        self.df['hbo_cmean']    = cmean
        self.df['hbo_os']       = os
        self.df['hbo_bull_dot'] = np.where((os > os_prev) & (os == 1),  hso, np.nan)
        self.df['hbo_bear_dot'] = np.where((os < os_prev) & (os == -1), hso, np.nan)

    def add_fig(self):
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        dates  = self.df['Date']
        hso    = self.df['hbo_hso']
        cmean  = self.df['hbo_cmean']

        bull_color = '#0cb51a'
        bear_color = '#ff1100'

        bar_colors = [
            'rgba(12,181,26,0.65)' if v > 0 else 'rgba(255,17,0,0.65)'
            for v in hso.fillna(0)
        ]

        # Histogram bars (green above zero, red below)
        self.fig.add_trace(go.Bar(
            x=dates,
            y=hso,
            marker_color=bar_colors,
            name='Hull Butterfly',
            showlegend=False,
        ))

        # Thin outline line on top of bars
        self.fig.add_trace(go.Scatter(
            x=dates,
            y=hso,
            line=dict(color='rgba(180,180,180,0.7)', width=1),
            name='Hull Butterfly',
            showlegend=False,
        ))

        # Bullish dots: os just transitioned to 1 (rising from below -cmean)
        self.fig.add_trace(go.Scatter(
            x=dates,
            y=self.df['hbo_bull_dot'],
            mode='markers',
            marker=dict(color=bull_color, size=8),
            name='Bullish',
            showlegend=False,
        ))

        # Bearish dots: os just transitioned to -1 (falling from above +cmean)
        self.fig.add_trace(go.Scatter(
            x=dates,
            y=self.df['hbo_bear_dot'],
            mode='markers',
            marker=dict(color=bear_color, size=8),
            name='Bearish',
            showlegend=False,
        ))

        # Dynamic level lines (cmean, cmean/2, -cmean/2, -cmean)
        level_style = dict(color='grey', width=1, dash='dot')
        for y_vals, label in [
            (cmean,      '+cmean'),
            (cmean / 2,  '+cmean/2'),
            (-cmean / 2, '-cmean/2'),
            (-cmean,     '-cmean'),
        ]:
            self.fig.add_trace(go.Scatter(
                x=dates,
                y=y_vals,
                line=level_style,
                name=label,
                showlegend=False,
            ))

        # Zero baseline
        self.fig.add_hline(y=0, line_width=1, line_dash='solid', line_color='grey')
