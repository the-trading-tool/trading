import sys
try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

import logging
import pandas as pd
import plotly.graph_objects as go
from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator
from tradinglib import predictlib as pl

logger = logging.getLogger(__name__)


class Pre(_indicator._Indicator):

    is_oszilator = False
    name = 'Predict target range'

    params = {
        'show_forecast': {
            'type': 'bool', 'default': False,
            'label': '3-Tage-Prognose anzeigen',
        },
    }

    def __init__(self, df, symbol = "", show_forecast=False):
        """Initialize the indicator with the provided DataFrame and optional params.

        show_forecast toggles the recursive t+1..t+3 close projection (default
        off — it trains a second model, so keep it opt-in).
        """
        self.show_forecast = bool(show_forecast)
        super().__init__(df=df, symbol=symbol)
        self.data()

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""

        self.pr_low = 0
        self.pr_high = 0
        self.forecast = []          # recursive t+1..t+3 close forecast (leakage-free)
        try:
            p = pl.predict(precise = True)
            p.df = self.df.filter(['Open','Close','High','Low','Volume','ema9','ema21'])
            p.prepare_lag()
            p.load_model()
            nd = pl.np.sort(p.next_days)
            self.pr_low = int(nd[0]*100)/100
            self.pr_high = int(nd[-1]*100)/100

        except Exception:
            logger.exception("Pre: target-range prediction failed")

        # Additional, genuinely forward-looking 3-day forecast — opt-in via the
        # overlay config toggle (default off). The block above is a contemporaneous
        # fit over the last known bars; this rolls the model recursively into the
        # future on log-returns (see predictlib.forecast_future).
        if self.show_forecast:
            try:
                f = pl.predict(precise = True)
                f.df = self.df.filter(['Close']).copy()
                self.forecast = [round(float(v), 2) for v in f.forecast_future(horizon=3)]
            except Exception:
                logger.exception("Pre: 3-day recursive forecast failed")

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        self._add_hline_outside(
            y=self.pr_high,
            text=f'Pred H: {self.pr_high}',
            line_color='blue',
            line_dash='dash',
            line_width=1,
        )
        self.fig.add_hrect(y0=self.pr_low, y1=self.pr_high, line_width=0, fillcolor="blue", opacity=0.2)
        self._add_hline_outside(
            y=self.pr_low,
            text=f'Pred L: {self.pr_low}',
            line_color='darkblue',
            line_dash='dash',
            line_width=1,
        )

        # Draw the recursive 3-day forecast as points extending past the last bar.
        try:
            if self.forecast:
                self._add_forecast_trace()
        except Exception:
            logger.exception("Pre: forecast trace render failed")

    def _add_forecast_trace(self):
        """Plot t+1..t+3 predicted closes on business days after the last bar.

        The scatter's future x-coordinates widen the chart's x-axis (Plotly
        autorange honours trace coords), so the projection is actually visible
        to the right of the last candle — a horizontal line alone couldn't show
        the time dimension.
        """
        df = self.df

        # Recover a date axis + close series regardless of index/column layout
        date_col = next((c for c in ('Date', 'index', 'level_0') if c in df.columns), None)
        if date_col is not None:
            dseries = pd.to_datetime(df[date_col], errors='coerce').reset_index(drop=True)
        else:
            dseries = pd.Series(pd.to_datetime(df.index, errors='coerce'))
        cseries = pd.to_numeric(df['Close'], errors='coerce').reset_index(drop=True)

        valid = dseries.notna() & cseries.notna()
        if not valid.any():
            return
        last_date = pd.Timestamp(dseries[valid].iloc[-1])
        last_close = float(cseries[valid].iloc[-1])

        # Next N business days (skip weekends so a rangebreak can't swallow them)
        fut_dates = list(pd.bdate_range(
            start=last_date + pd.Timedelta(days=1), periods=len(self.forecast)))

        xs = [last_date] + fut_dates
        ys = [last_close] + list(self.forecast)
        labels = [''] + [f'{v:.2f}' for v in self.forecast]

        self.fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode='lines+markers+text',
            line=dict(color='royalblue', width=2, dash='dot'),
            marker=dict(size=7, color='royalblue', symbol='diamond'),
            text=labels, textposition='top center',
            textfont=dict(color='royalblue', size=11),
            name='3-Tage-Prognose', showlegend=False,
            hovertemplate='Prognose %{x|%Y-%m-%d}: <b>%{y:.2f}</b><extra>Pre</extra>',
        ))
