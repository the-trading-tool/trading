import pandas as pd
import numpy as np
import plotly.graph_objects as go
import sys

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator


class Gframa(_indicator._Indicator):
    """
    G-FRAMA — Gaussian-smoothed FRAMA with ATR signal bands.
    Port of the QuantEdgeB G-FRAMA Pine Script indicator (Pine v6).

    Pipeline:
      close → Gaussian filter → FRAMA → ATR bands → QB signal (1/−1/0)

    Columns added to df:
      gframa        – FRAMA line
      gframa_upper  – FRAMA + ATR * mult  (long threshold)
      gframa_lower  – FRAMA − ATR         (short threshold)
      gframa_signal – persistent regime: 1=long, −1=short, 0=neutral
    """

    name = 'G-FRAMA'
    is_oszilator = False

    params = {
        'gauss_length':  {'type': 'int',   'default': 4,    'min': 1,   'max': 50,   'label': 'Gaussian length'},
        'gauss_sigma':   {'type': 'float', 'default': 2.0,  'min': 0.1, 'max': 10.0, 'label': 'Gaussian sigma'},
        'frama_length':  {'type': 'int',   'default': 20,   'min': 4,   'max': 200,  'label': 'FRAMA length'},
        'frama_upper':   {'type': 'int',   'default': 8,    'min': 1,   'max': 50,   'label': 'FRAMA upper limit (fast EMA)'},
        'frama_lower':   {'type': 'int',   'default': 40,   'min': 2,   'max': 200,  'label': 'FRAMA lower limit (slow EMA)'},
        'atr_length':    {'type': 'int',   'default': 14,   'min': 1,   'max': 100,  'label': 'ATR length'},
        'atr_mult':      {'type': 'float', 'default': 1.9,  'min': 0.1, 'max': 10.0, 'label': 'ATR mult (upper band)'},
        'show_bands':    {'type': 'bool',  'default': True,              'label': 'Show ATR bands'},
        'fill_bands':    {'type': 'bool',  'default': True,              'label': 'Fill ATR band'},
        'color_long':    {'type': 'color', 'default': '',                'label': 'Long color'},
        'color_short':   {'type': 'color', 'default': '',                'label': 'Short color'},
        'color_neutral': {'type': 'color', 'default': '',                'label': 'Neutral color'},
    }

    def __init__(
        self,
        df,
        symbol='',
        gauss_length=4,
        gauss_sigma=2.0,
        frama_length=20,
        frama_upper=8,
        frama_lower=40,
        atr_length=14,
        atr_mult=1.9,
        show_bands=True,
        fill_bands=True,
        color_long='',
        color_short='',
        color_neutral='',
        **kwargs
    ):
        super().__init__(df=df, symbol=symbol)
        self.df           = df.copy()
        self.gauss_length  = int(gauss_length)
        self.gauss_sigma   = float(gauss_sigma)
        self.frama_length  = int(frama_length)
        self.frama_upper   = int(frama_upper)
        self.frama_lower   = int(frama_lower)
        self.atr_length    = int(atr_length)
        self.atr_mult      = float(atr_mult)
        self.show_bands    = bool(show_bands)
        self.fill_bands    = bool(fill_bands)
        self.color_long    = color_long    or 'rgb(23, 191, 238)'
        self.color_short   = color_short   or 'rgb(180, 30, 30)'
        self.color_neutral = color_neutral or 'rgb(128, 128, 128)'
        self.data()
        self.add_fig()

    # ── Gaussian weighted average (Pine F_Gaussian exact port) ────────────────
    def _gaussian_filter(self, src_arr, length, sigma):
        kernel = np.array([
            np.exp(-0.5 * ((i - (length - 1) / 2.0) / sigma) ** 2)
            for i in range(length)
        ])
        kernel /= kernel.sum()
        n = len(src_arr)
        result = np.full(n, np.nan)
        for j in range(length - 1, n):
            # src_arr[j] = current bar (Pine src[0]); [::-1] puts current first
            window = src_arr[j - length + 1: j + 1][::-1]
            if not np.any(np.isnan(window)):
                result[j] = float(np.dot(kernel, window))
        return result

    # ── ATR — Wilder RMA, matches Pine ta.atr() ───────────────────────────────
    def _atr(self, high, low, close, length):
        n = len(close)
        tr = np.full(n, np.nan)
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i]  - close[i - 1]),
            )
        atr = np.full(n, np.nan)
        # Wilder initialisation: SMA of first `length` TRs
        if length < n:
            atr[length] = float(np.nanmean(tr[1: length + 1]))
            for i in range(length + 1, n):
                atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
        return atr

    # ── FRAMA (exact port of Pine F_FRAMA) ────────────────────────────────────
    def _frama(self, src, high, low, len1, len2, len3):
        half = len1 // 2
        n = len(src)
        frama = np.full(n, np.nan)
        prev_dim = 1.5          # fallback fractal dimension

        for i in range(len1 - 1, n):
            if np.isnan(src[i]):
                continue

            # Full window
            h_full = float(np.max(high[i - len1 + 1: i + 1]))
            l_full = float(np.min(low [i - len1 + 1: i + 1]))
            HL  = (h_full - l_full) / len1

            # Recent half (bars i-half+1 … i)
            h1 = float(np.max(high[i - half + 1: i + 1]))
            l1 = float(np.min(low [i - half + 1: i + 1]))
            HL1 = (h1 - l1) / half

            # Older half (bars i-2*half+1 … i-half) — always `half` bars
            h2 = float(np.max(high[i - 2 * half + 1: i - half + 1]))
            l2 = float(np.min(low [i - 2 * half + 1: i - half + 1]))
            HL2 = (h2 - l2) / half

            if HL1 > 0 and HL2 > 0 and HL > 0:
                D = (np.log(HL1 + HL2) - np.log(HL)) / np.log(2)
                prev_dim = D
            else:
                D = prev_dim

            w       = np.log(2.0 / (len3 + 1))
            alpha   = np.exp(w * (D - 1))
            alpha1  = float(np.clip(alpha, 0.01, 1.0))

            oldN    = (2 - alpha1) / alpha1
            newN    = (len3 - len2) * (oldN - 1) / (len3 - 1) + len2
            newalpha = 2.0 / (newN + 1)
            min_a   = 2.0 / (len3 + 1)
            newalpha1 = float(np.clip(newalpha, min_a, 1.0))

            prev    = frama[i - 1] if (i > 0 and not np.isnan(frama[i - 1])) else float(src[i])
            frama[i] = (1.0 - newalpha1) * prev + newalpha1 * float(src[i])

        return frama

    # ── QB signal — persistent state, mirrors Pine's `var QB = 0` ─────────────
    def _signal(self, close, long_v, short_v):
        n = len(close)
        qb = np.zeros(n, dtype=np.int8)
        for i in range(1, n):
            if np.isnan(long_v[i]) or np.isnan(short_v[i]):
                qb[i] = qb[i - 1]
                continue
            long_c  = bool(close[i] > long_v[i])
            short_c = bool(close[i] < short_v[i])
            if long_c and not short_c:
                qb[i] = 1
            elif short_c:
                qb[i] = -1
            else:
                qb[i] = qb[i - 1]
        return qb

    # ── Attach columns to self.df ──────────────────────────────────────────────
    def data(self):
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        close = self.df['Close'].values.astype(float)
        high  = self.df['High'].values.astype(float)
        low   = self.df['Low'].values.astype(float)

        gauss  = self._gaussian_filter(close, self.gauss_length, self.gauss_sigma)
        frama  = self._frama(gauss, high, low, self.frama_length, self.frama_upper, self.frama_lower)
        atr    = self._atr(high, low, close, self.atr_length)

        long_v  = frama + atr * self.atr_mult
        short_v = frama - atr

        self.df['gframa']        = frama
        self.df['gframa_upper']  = long_v
        self.df['gframa_lower']  = short_v
        self.df['gframa_signal'] = self._signal(close, long_v, short_v)

    # ── Build y-values for one signal state, overlap at transition points ──────
    def _seg_y(self, y, qb, target):
        out = np.where(qb == target, y, np.nan).astype(float)
        for i in range(1, len(qb)):
            if qb[i] == target and qb[i - 1] != target and not np.isnan(y[i - 1]):
                out[i - 1] = float(y[i - 1])
        return out

    # ── Plotly figure ──────────────────────────────────────────────────────────
    def add_fig(self):
        self.fig = go.Figure()

        df  = self.df.reset_index(drop=True)
        x   = df['Date'] if 'Date' in df.columns else df.index
        fra = df['gframa'].values.astype(float)
        upr = df['gframa_upper'].values.astype(float)
        lwr = df['gframa_lower'].values.astype(float)
        qb  = df['gframa_signal'].values.astype(int)

        c_l = self.color_long
        c_s = self.color_short
        c_n = self.color_neutral

        def to_rgba(color, alpha):
            if color.startswith('rgb('):
                return f'rgba({color[4:-1]},{alpha})'
            return color

        # ── ATR bands (drawn first, beneath the FRAMA line) ───────────────
        if self.show_bands:
            # Fill colour follows the last known signal
            valid_mask = ~np.isnan(fra)
            last_qb = int(qb[valid_mask][-1]) if valid_mask.any() else 0
            fill_col = to_rgba(
                c_l if last_qb == 1 else c_s if last_qb == -1 else c_n,
                0.12,
            )

            # Filled band as rangebreak-safe polygons (split at time gaps): a
            # plain 'tonexty' fill balloons into a wedge wherever an x-axis
            # rangebreak collapses a bar — see _Indicator.segmented_band.
            if self.fill_bands:
                xs, ys = self.segmented_band(x, upr, lwr)
                self.fig.add_trace(go.Scatter(
                    x=xs, y=ys,
                    fill='toself',
                    fillcolor=fill_col,
                    line=dict(width=0),
                    hoverinfo='skip',
                    showlegend=False,
                    name='G-FRAMA band',
                ))

            self.fig.add_trace(go.Scatter(
                x=x, y=upr,
                mode='lines',
                name='G-FRAMA Upper',
                showlegend=False,
                line=dict(color='rgba(200,200,200,0.30)', width=1),
            ))

            self.fig.add_trace(go.Scatter(
                x=x, y=lwr,
                mode='lines',
                name='G-FRAMA Lower',
                showlegend=False,
                line=dict(color='rgba(200,200,200,0.30)', width=1),
            ))

        # ── FRAMA line in three coloured segments ─────────────────────────
        for target, color in [(1, c_l), (-1, c_s), (0, c_n)]:
            self.fig.add_trace(go.Scatter(
                x=x,
                y=self._seg_y(fra, qb, target),
                mode='lines',
                name='G-FRAMA',
                showlegend=False,
                connectgaps=False,
                line=dict(color=color, width=2),
            ))
