import logging
import pandas as pd
import plotly.graph_objects as go

from tradinglib.indicator import _indicator

logger = logging.getLogger(__name__)

_R_COLORS = ['#c0392b', '#e74c3c', '#e57373']
_S_COLORS = ['#27ae60', '#2ecc71', '#81c784']
_P_COLOR  = '#f39c12'


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


class Pvt(_indicator._Indicator):

    is_oszilator = False
    name = 'Pivot Points'

    params = {
        'method': {
            'type':    'select',
            'default': 'classic',
            'options': ['classic', 'fibonacci', 'woodie'],
            'label':   'Method',
        },
    }

    def __init__(self, df, symbol='', method='classic'):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.method = str(method)
        self.data()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _bar_seconds(self) -> float:
        """Return the typical seconds-per-bar, or 0 on failure."""
        if len(self.df) < 2:
            return 0
        try:
            idx = pd.to_datetime(self.df.index, errors='coerce')
            return (idx[-1] - idx[-2]).total_seconds()
        except Exception:
            return 0

    def _prev_day_hlc_from_df(self):
        """Aggregate (H, L, C) of the trading day before the last bar in df.

        Works for any intraday resolution (1h, 4h, …). Finds the unique calendar
        dates inside df and aggregates all bars from the second-to-last date:
            H = max(High)   L = min(Low)   C = Close of last bar that day

        Returns None when the df contains fewer than two distinct dates.
        """
        try:
            idx = pd.to_datetime(self.df.index, errors='coerce')
            dates = pd.array(idx.date)           # plain Python date objects
            unique_dates = sorted(set(dates))

            if len(unique_dates) < 2:
                return None

            prev_date = unique_dates[-2]         # last *completed* day visible in df

            mask = [d == prev_date for d in dates]
            prev_bars = self.df[mask]

            if prev_bars.empty:
                return None

            h = float(prev_bars['High'].max())
            l = float(prev_bars['Low'].min())
            c = float(prev_bars['Close'].iloc[-1])
            return h, l, c

        except Exception:
            logger.debug("pvt: _prev_day_hlc_from_df failed for %s", self.symbol)
            return None

    # ── Core calculation ───────────────────────────────────────────────────────

    def data(self):
        """Compute pivot levels and attach them as columns to self.df.

        Reference-bar selection:
          intraday chart  → aggregate previous calendar day from df bars
          daily / longer  → self.df.iloc[-2]  (the previous completed bar)
        Both methods respect the range-slider because they operate on self.df.
        """
        if len(self.df) < 2:
            return

        seconds = self._bar_seconds()
        hlc = None

        if 0 < seconds < 86_400:           # intraday  (1 h, 4 h, …)
            hlc = self._prev_day_hlc_from_df()

        if hlc is not None:
            h, l, c = hlc
        else:                               # daily / weekly / fallback
            row = self.df.iloc[-2]
            h = float(row['High'])
            l = float(row['Low'])
            c = float(row['Close'])

        r = h - l

        if self.method == 'fibonacci':
            p  = (h + l + c) / 3
            r1 = p + 0.382 * r
            r2 = p + 0.618 * r
            r3 = p + 1.000 * r
            s1 = p - 0.382 * r
            s2 = p - 0.618 * r
            s3 = p - 1.000 * r
        elif self.method == 'woodie':
            p  = (h + l + 2 * c) / 4
            r1 = 2 * p - l
            r2 = p + r
            r3 = h + 2 * (p - l)
            s1 = 2 * p - h
            s2 = p - r
            s3 = l - 2 * (h - p)
        else:                               # classic (floor trader)
            p  = (h + l + c) / 3
            r1 = 2 * p - l
            r2 = p + r
            r3 = h + 2 * (p - l)
            s1 = 2 * p - h
            s2 = p - r
            s3 = l - 2 * (h - p)

        self.df['pvt_p']  = p
        self.df['pvt_r1'] = r1
        self.df['pvt_r2'] = r2
        self.df['pvt_r3'] = r3
        self.df['pvt_s1'] = s1
        self.df['pvt_s2'] = s2
        self.df['pvt_s3'] = s3

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        levels = [
            ('pvt_r3', 'R3', _R_COLORS[2], 'dash', 1),
            ('pvt_r2', 'R2', _R_COLORS[1], 'dash', 1),
            ('pvt_r1', 'R1', _R_COLORS[0], 'dash', 2),
            ('pvt_p',  'P',  _P_COLOR,     'dot',  2),
            ('pvt_s1', 'S1', _S_COLORS[0], 'dash', 2),
            ('pvt_s2', 'S2', _S_COLORS[1], 'dash', 1),
            ('pvt_s3', 'S3', _S_COLORS[2], 'dash', 1),
        ]

        for col, label, color, dash, width in levels:
            if col in self.df.columns:
                val = self.df[col].iloc[-1]
                self._add_hline_outside(
                    y=val,
                    text=f'{label}: {_fmt(val)}',
                    line_color=color,
                    line_dash=dash,
                    line_width=width,
                )
