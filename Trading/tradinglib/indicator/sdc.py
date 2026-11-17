"""Standard deviation channel -- linear regression of the close +/- k sigma.

Port of the TradingView "Standard Deviation Channel" (Std Dev Chn), with the
length given in years of daily bars (default 10) and four bands at configurable
multiples of the residual standard deviation (default +1, +2, -3, -4).

Two views of the same fit:

* **Columns** (``COLUMNS``, stored in asset_simulation via ``/backfill:sdc``):
  causal. For every day the regression is fitted on the ``years`` before it,
  ending at that day, and the line/bands are read at that day -- what the Pine
  script shows on its last bar if the chart had ended there. Days with less
  than ``years`` of local history stay empty (stored as 0 -- check
  ``sdc_std > 0`` in formulas).
* **Drawing**: the one channel fitted at the chart's last bar, drawn straight
  back over its window like the Pine original. It is redrawn as new bars come
  in (repaints); signals belong on the columns, not on the drawn lines. With
  less local history than ``years`` (but at least ``MIN_DRAW_YEARS``) the
  drawing fits what there is and says so in the line name; the columns stay
  empty then, so a formula never sees a shorter channel than it asks for.

Optional ``log_scale``: the regression runs on ln(close), so the channel is an
exponential curve (a straight line on a log axis) and the bands are constant
*percentage* distances -- lower bands never go negative over long uptrends.
``sdc_std`` is then in log units (0.1 ~ 10 %) and ``sdc_pos`` counts those;
``sdc_mid`` and the bands are prices either way.

Both run on the full local daily history (like ``mtt``/``eps``): ten years need
more bars than a chart holds, and only then do chart and stored values agree.
The standard deviation divides by n - 1 like the original; ``sdc_r`` is the
signed Pearson correlation of the close with time (the Pine label shows |r|).
"""
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

# Kept in sync with asset_perf2.INDICATOR_BACKFILL_MAP['sdc'].
COLUMNS = ('sdc_mid', 'sdc_std', 'sdc_pos', 'sdc_r',
           'sdc_up1', 'sdc_up2', 'sdc_lo1', 'sdc_lo2')

DEFAULT_YEARS = 10
DEFAULT_MULTS = (1.0, 2.0, 3.0, 4.0)        # up1, up2, lo1, lo2 (lower ones subtracted)

# A window counts as full when the history starts at most this long after its
# left edge (holidays, a missing first week).
_TOLERANCE = pd.Timedelta(days=10)
_MIN_BARS = 3
MIN_DRAW_YEARS = 1

# Line colours: regression brown, upper bands in browns, lower bands in greys.
DEFAULT_COLORS = {'mid': '#8B4513', 'up1': '#A0522D', 'up2': '#D2A679',
                  'lo1': '#808080', 'lo2': '#B0B0B0', 'fill': '#A0522D'}
_FILL_OPACITY = 0.12


def window(years) -> pd.Timedelta:
    """Calendar length of a *years* window."""
    return pd.Timedelta(days=round(365.25 * float(years)))


def _clean(close) -> pd.Series:
    s = pd.Series(close, dtype=float).dropna()
    s.index = pd.DatetimeIndex(pd.to_datetime(s.index, errors='coerce'))
    s = s[~s.index.isna()].sort_index()
    return s[~s.index.duplicated(keep='last')]


def _fit_space(close: pd.Series, log: bool) -> pd.Series:
    """Values the regression runs on: the close, or ln(close) (non-positive dropped)."""
    if not log:
        return close
    return np.log(close[close > 0])


def rolling_channel(close, years=DEFAULT_YEARS, mults=DEFAULT_MULTS,
                    log=False) -> pd.DataFrame:
    """Causal channel per day: fit over the last *years*, read at that day.

    *close* is a daily Series on a DatetimeIndex. Returns ``COLUMNS`` on the same
    (cleaned) index, NaN where the history does not reach *years* back. With
    *log* the fit runs on ln(close) and prices are mapped back with exp.
    """
    close = _fit_space(_clean(close), log)
    out = pd.DataFrame(np.nan, index=close.index, columns=list(COLUMNS))
    if len(close) < _MIN_BARS:
        return out
    win = window(years)
    x = pd.Series(np.arange(len(close), dtype=float), index=close.index)
    rx = x.rolling(win, min_periods=_MIN_BARS)
    ry = close.rolling(win, min_periods=_MIN_BARS)
    vx, vy = rx.var(), ry.var()
    cov = rx.cov(close)
    slope = cov / vx
    mid = ry.mean() + slope * (x - rx.mean())
    # Sum of squared residuals / (n - 1) == var_y - cov^2 / var_x.
    std = np.sqrt((vy - cov ** 2 / vx).clip(lower=0))
    r = cov / np.sqrt(vx * vy)
    full = (close.index - close.index[0]) >= win - _TOLERANCE
    up1, up2, lo1, lo2 = (float(m) for m in mults)
    price = np.exp if log else (lambda v: v)
    out['sdc_mid'] = price(mid)
    out['sdc_std'] = std
    out['sdc_pos'] = ((close - mid) / std).where(std > 0)
    out['sdc_r'] = r
    out['sdc_up1'] = price(mid + up1 * std)
    out['sdc_up2'] = price(mid + up2 * std)
    out['sdc_lo1'] = price(mid - lo1 * std)
    out['sdc_lo2'] = price(mid - lo2 * std)
    return out.where(pd.Series(full, index=close.index), axis=0)


def fit_last(close, years=DEFAULT_YEARS, min_years=None, log=False):
    """The channel at the last bar: ``(mid Series over the window, std, r)`` or None.

    Without *min_years* the history must cover the full window (like the
    columns). With it, a shorter history of at least *min_years* is fitted as a
    whole instead. With *log*, mid and std are in ln units (``np.exp`` maps
    ``mid +- k * std`` back to prices).
    """
    close = _fit_space(_clean(close), log)
    if len(close) < _MIN_BARS:
        return None
    win = window(years)
    end = close.index[-1]
    if close.index[0] > end - win + _TOLERANCE:
        if min_years is None or end - close.index[0] < window(min_years) - _TOLERANCE:
            return None
    seg = close[close.index > end - win]
    if len(seg) < _MIN_BARS:
        return None
    x = np.arange(len(seg), dtype=float)
    y = seg.to_numpy()
    slope, intercept = np.polyfit(x, y, 1)
    fitted = intercept + slope * x
    std = float(np.sqrt(((y - fitted) ** 2).sum() / (len(seg) - 1)))
    r = float(np.corrcoef(x, y)[0, 1]) if y.std() > 0 else 0.0
    return pd.Series(fitted, index=seg.index), std, r


class Sdc(_indicator._Indicator):

    is_oszilator = False
    name = 'Standard Deviation Channel'

    params = {
        'years':    {'type': 'int', 'default': DEFAULT_YEARS, 'min': 1, 'max': 30,
                     'label': 'Regression length (years)'},
        'mult_up1': {'type': 'float', 'default': DEFAULT_MULTS[0], 'min': 0.0, 'max': 10.0,
                     'label': 'Upper band 1 (+ sigma)'},
        'mult_up2': {'type': 'float', 'default': DEFAULT_MULTS[1], 'min': 0.0, 'max': 10.0,
                     'label': 'Upper band 2 (+ sigma)'},
        'mult_lo1': {'type': 'float', 'default': DEFAULT_MULTS[2], 'min': 0.0, 'max': 10.0,
                     'label': 'Lower band 1 (- sigma)'},
        'mult_lo2': {'type': 'float', 'default': DEFAULT_MULTS[3], 'min': 0.0, 'max': 10.0,
                     'label': 'Lower band 2 (- sigma)'},
        'log_scale': {'type': 'bool', 'default': False,
                      'label': 'Logarithmic regression (bands as % distance)'},
        'color_mid':  {'type': 'color', 'default': '', 'label': 'Regression line color'},
        'color_up1':  {'type': 'color', 'default': '', 'label': 'Upper band 1 color'},
        'color_up2':  {'type': 'color', 'default': '', 'label': 'Upper band 2 color'},
        'color_lo1':  {'type': 'color', 'default': '', 'label': 'Lower band 1 color'},
        'color_lo2':  {'type': 'color', 'default': '', 'label': 'Lower band 2 color'},
        'color_fill': {'type': 'color', 'default': '',
                       'label': 'Fill between upper band 1 and lower band 1'},
    }

    def __init__(self, df, symbol="", years=DEFAULT_YEARS, mult_up1=DEFAULT_MULTS[0],
                 mult_up2=DEFAULT_MULTS[1], mult_lo1=DEFAULT_MULTS[2],
                 mult_lo2=DEFAULT_MULTS[3], color_mid='', color_up1='', color_up2='',
                 color_lo1='', color_lo2='', color_fill='', log_scale=False):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)

        def num(value, default, cast=float):
            try:
                value = cast(value)
                return value if value > 0 or cast is float and value == 0 else default
            except (TypeError, ValueError):
                return default

        self.years = num(years, DEFAULT_YEARS, int)
        self.mults = (num(mult_up1, DEFAULT_MULTS[0]), num(mult_up2, DEFAULT_MULTS[1]),
                      num(mult_lo1, DEFAULT_MULTS[2]), num(mult_lo2, DEFAULT_MULTS[3]))
        self.log = (log_scale if isinstance(log_scale, bool)
                    else str(log_scale).strip().lower() in ('1', 'true', 'yes'))
        given = {'mid': color_mid, 'up1': color_up1, 'up2': color_up2,
                 'lo1': color_lo1, 'lo2': color_lo2, 'fill': color_fill}
        self.colors = {k: (given[k] or v) for k, v in DEFAULT_COLORS.items()}
        self.close = pd.Series(dtype=float)
        self.data()

    def _target_index(self) -> pd.DatetimeIndex:
        idx = pd.to_datetime(self.df.index, errors='coerce')
        if idx.isna().all() and 'Date' in self.df.columns:
            idx = pd.to_datetime(self.df['Date'], errors='coerce')
        return pd.DatetimeIndex(idx)

    def _history(self, target: pd.DatetimeIndex) -> pd.Series:
        """Full local daily closes up to the chart's end, or the chart's own daily closes."""
        if self.symbol:
            try:
                from tradinglib import four_ps
                hist = four_ps.cap_to_chart(four_ps.load_daily(self.symbol), target)
                if hist is not None and not hist.empty:
                    return hist['Close']
            except Exception:
                pass
        if len(target) < 2 or 'Close' not in self.df.columns:
            return pd.Series(dtype=float)
        # Weekly bars would turn the year count into a different bar count.
        spacing = pd.Series(target).diff().dt.total_seconds().median()
        if not spacing or spacing > 4 * 24 * 3600:
            return pd.Series(dtype=float)
        return pd.Series(pd.to_numeric(self.df['Close'], errors='coerce').to_numpy(),
                         index=target).dropna()

    def data(self):
        """Attach the causal channel columns to self.df (NaN without enough history)."""
        target = self._target_index()
        try:
            self.close = _clean(self._history(target))
        except Exception:
            self.close = pd.Series(dtype=float)
        if self.close.empty or target.isna().all():
            for column in COLUMNS:
                self.df[column] = np.nan
            return
        frame = rolling_channel(self.close, self.years, self.mults, log=self.log)
        # Levels -- carry the last daily value onto the chart's bars.
        projected = frame.reindex(_naive(target), method='ffill')
        for column in COLUMNS:
            self.df[column] = projected[column].to_numpy()

    def add_fig(self):
        """The channel fitted at the chart's last bar: five straight lines, one fill."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
        if 'Date' not in self.df.columns and len(self.df.columns):
            self.df = self.df.rename(columns={self.df.columns[0]: 'Date'})
        fit = (fit_last(self.close, self.years, min_years=MIN_DRAW_YEARS, log=self.log)
               if not self.close.empty else None)
        if fit is None:
            return self.fig
        mid, std, r = fit
        span = (mid.index[-1] - mid.index[0]).days / 365.25
        length = (f'{self.years}y' if span >= self.years - 0.05
                  else f'{span:.1f}y of {self.years}y') + (', log' if self.log else '')
        dates = pd.to_datetime(self.df['Date'], errors='coerce')
        naive = _naive(pd.DatetimeIndex(dates))
        # Own copy: under copy-on-write to_numpy() may hand back a read-only view.
        line = np.array(mid.reindex(naive, method='ffill').to_numpy(), dtype=float, copy=True)
        line[np.asarray(naive < mid.index[0])] = np.nan
        x = self.df['Date']
        up1, up2, lo1, lo2 = self.mults
        price = np.exp if self.log else (lambda v: v)
        bands = {
            'up1': price(line + up1 * std), 'up2': price(line + up2 * std),
            'lo1': price(line - lo1 * std), 'lo2': price(line - lo2 * std),
        }
        line = price(line)
        # One polygon: the channel is straight, a per-session split only leaves
        # hairline seams at every weekend.
        xs, ys = self.segmented_band(x, bands['up1'], bands['lo1'], mark_gaps=False)
        if xs:
            self.fig.add_trace(go.Scatter(x=xs, y=ys, fill='toself',
                                          fillcolor=self.colors['fill'], opacity=_FILL_OPACITY,
                                          line=dict(width=0), hoverinfo='skip',
                                          showlegend=False, name='SDC fill'))
        c = self.colors
        traces = (
            (line, f'SDC regression ({length}, R {r:.2f})', c['mid'], 2, 'solid'),
            (bands['up1'], f'SDC +{up1:g}σ', c['up1'], 1, 'dash'),
            (bands['up2'], f'SDC +{up2:g}σ', c['up2'], 1, 'dash'),
            (bands['lo1'], f'SDC −{lo1:g}σ', c['lo1'], 1, 'dash'),
            (bands['lo2'], f'SDC −{lo2:g}σ', c['lo2'], 1, 'dash'),
        )
        for y, name, color, width, dash in traces:
            self.fig.add_trace(go.Scatter(x=x, y=y, name=name, showlegend=False,
                                          line=dict(color=color, width=width, dash=dash)))
        return self.fig


def _naive(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize(None) if idx.tz is not None else idx
