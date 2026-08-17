"""4 Phase Sequence (4PS) overlay.

Draws the method's trade skeleton on the price chart: the consolidation base
(Phase 2) as a box outline, the breakout entry (Phase 3), the stop and target of
the running position (Phase 4), plus buy/sell markers.

The maths lives in :mod:`tradinglib.four_ps` — this class only takes care of
history loading and of projecting the daily result onto whatever timeframe the
chart is showing. Column names are identical to the ones persisted in
``asset_simulation_*.db`` (``fps_phase``, ``fps_buy``, …), so a buy/sell formula
written here also runs in the backtest.
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

# Signal columns are events: they must land on exactly one bar, never be
# forward-filled across the whole chart like a level does.
_SIGNAL_COLS = ('fps_buy', 'fps_sell', 'fps_breakout')

# Years of daily history the method needs before it can score anything
# (Phase 1 looks for completed multi-year legs).
_MIN_HISTORY_YEARS = 8


class Fps(_indicator._Indicator):

    is_oszilator = False
    name = '4 Phase Sequence (4PS)'

    params = {
        'trend_min_pct':   {'type': 'float', 'default': 90.0, 'min': 20.0, 'max': 500.0,
                            'label': 'Phase 1: min. past trend (%)'},
        'min_trends':      {'type': 'int',   'default': 1, 'min': 1, 'max': 10,
                            'label': 'Phase 1: number of such trends'},
        'base_weeks':      {'type': 'int',   'default': 8, 'min': 3, 'max': 52,
                            'label': 'Phase 2: min. base length (weeks)'},
        'base_depth_pct':  {'type': 'float', 'default': 25.0, 'min': 5.0, 'max': 60.0,
                            'label': 'Phase 2: max. base range (%)'},
        'near_high_pct':   {'type': 'float', 'default': 20.0, 'min': 2.0, 'max': 60.0,
                            'label': 'Phase 2: max. distance to 52w high (%)'},
        'breakout_pct':    {'type': 'float', 'default': 0.5, 'min': 0.0, 'max': 10.0,
                            'label': 'Phase 3: breakout buffer (%)'},
        'confirm_weeks':   {'type': 'int',   'default': 2, 'min': 1, 'max': 12,
                            'label': 'Phase 4: confirmation (weeks)'},
        'trend_sma_weeks': {'type': 'int',   'default': 30, 'min': 5, 'max': 60,
                            'label': 'Phase 4: weekly trend SMA'},
        'stop_pct':        {'type': 'float', 'default': 8.0, 'min': 1.0, 'max': 30.0,
                            'label': 'Stop below the breakout (%)'},
        'trail_pct':       {'type': 'float', 'default': 0.0, 'min': 0.0, 'max': 50.0,
                            'label': 'Trailing stop (%, 0 = off)'},
        'target_pct':      {'type': 'float', 'default': 80.0, 'min': 10.0, 'max': 300.0,
                            'label': 'Target (% above entry)'},
        'benchmark':       {'type': 'text',  'default': '^SPX',
                            'label': 'Relative-strength benchmark'},
        'color_base':      {'type': 'color', 'default': '#7CB518',
                            'label': 'Base color'},
    }

    def __init__(self, df, symbol="", trend_min_pct=90.0, min_trends=1, base_weeks=8,
                 base_depth_pct=25.0, near_high_pct=20.0, breakout_pct=0.5,
                 confirm_weeks=2, trend_sma_weeks=30, stop_pct=8.0, trail_pct=0.0,
                 target_pct=80.0, benchmark='^SPX', color_base='#7CB518'):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.opts = dict(
            trend_min_pct=trend_min_pct, min_trends=min_trends, base_weeks=base_weeks,
            base_depth_pct=base_depth_pct, near_high_pct=near_high_pct,
            breakout_pct=breakout_pct, confirm_weeks=confirm_weeks,
            trend_sma_weeks=trend_sma_weeks, stop_pct=stop_pct, trail_pct=trail_pct,
            target_pct=target_pct, benchmark=benchmark,
        )
        self.color_base = color_base or '#7CB518'
        self.data()

    # ── History ───────────────────────────────────────────────────────────────
    def _history(self, target: pd.DatetimeIndex) -> pd.DataFrame:
        """Daily OHLCV long enough to score the method.

        The chart hands over only the displayed window (and possibly an intraday
        or weekly timeframe), which never carries the multi-year legs Phase 1
        looks for — so reload the full local daily history for ``self.symbol``.
        Falls back to the passed frame when no symbol/local history is available.
        """
        from tradinglib import four_ps as fps

        if self.symbol:
            hist = fps.load_daily(self.symbol)
            if not hist.empty:
                return hist
        # No local history: only fall back to the chart's own frame when it is
        # daily or finer. Feeding weekly/monthly bars into the daily pipeline
        # would silently produce meaningless phases.
        if len(target) < 2 or 'Close' not in self.df.columns:
            return pd.DataFrame()
        spacing = pd.Series(target).diff().dt.total_seconds().median()
        if not spacing or spacing > 4 * 24 * 3600:
            return pd.DataFrame()
        df = self.df.copy()
        df.index = target
        keep = [c for c in ('Open', 'High', 'Low', 'Close', 'Volume') if c in df.columns]
        return df[keep].dropna(subset=['Close'])

    def _target_index(self) -> pd.DatetimeIndex:
        """Datetime version of the chart's own index (which may hold strings)."""
        idx = pd.to_datetime(self.df.index, errors='coerce')
        if idx.isna().all() and 'Date' in self.df.columns:
            idx = pd.to_datetime(self.df['Date'], errors='coerce')
        return pd.DatetimeIndex(idx)

    def _project(self, frame: pd.DataFrame, target: pd.DatetimeIndex) -> pd.DataFrame:
        """Map the daily result onto the chart's bars.

        Levels are forward-filled (they hold until they change), events are placed
        on the bar that carries their date — on an intraday chart that can be the
        last bar of the previous session, which is close enough for a marker.
        """
        out = pd.DataFrame(index=self.df.index)
        if frame.empty or len(target) == 0:
            for col in frame.columns:
                out[col] = np.nan
            return out

        level_cols = [c for c in frame.columns if c not in _SIGNAL_COLS]
        lvl = frame[level_cols].reindex(target, method='ffill')
        for col in level_cols:
            out[col] = lvl[col].to_numpy()

        pos = np.clip(np.searchsorted(target.to_numpy(), frame.index.to_numpy(),
                                      side='right') - 1, 0, len(target) - 1)
        for col in _SIGNAL_COLS:
            if col not in frame.columns:
                continue
            vals = np.full(len(target), np.nan)
            src = frame[col].to_numpy(dtype=float)
            for src_i, tgt_i in enumerate(pos):
                v = src[src_i]
                if not np.isnan(v) and v != 0:
                    vals[tgt_i] = v
            out[col] = vals
        return out

    # ── Computation ───────────────────────────────────────────────────────────
    def data(self):  # 4 Phase Sequence
        from tradinglib import four_ps as fps

        target = self._target_index()
        try:
            hist = self._history(target)
        except Exception:
            hist = pd.DataFrame()
        if hist is None or hist.empty or target.isna().all():
            for col in ('fps_phase', 'fps_best_trend', 'fps_trend_gain', 'fps_base_high',
                        'fps_base_low', 'fps_base_weeks', 'fps_breakout', 'fps_buy',
                        'fps_sell', 'fps_stop', 'fps_target', 'fps_rs', 'fps_dist_high'):
                self.df[col] = np.nan
            return

        bm = None
        benchmark = self.opts.get('benchmark')
        if benchmark and str(benchmark) != str(self.symbol):
            bm = fps.benchmark_series(benchmark)
        frame = fps.compute(hist, bm, **self.opts)
        projected = self._project(frame, target)
        for col in projected.columns:
            self.df[col] = projected[col]

    # ── Figure ────────────────────────────────────────────────────────────────
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        df = self.df
        x = df['Date'] if 'Date' in df.columns else df.index

        def _mask(col):
            """Level column with 0/NaN blanked out so the line breaks properly."""
            if col not in df.columns:
                return pd.Series(np.nan, index=df.index)
            s = pd.to_numeric(df[col], errors='coerce')
            return s.where(s > 0)

        base_high, base_low = _mask('fps_base_high'), _mask('fps_base_low')
        self.fig.add_trace(go.Scatter(
            x=x, y=base_high, name='4PS base high', showlegend=False,
            line=dict(color=self.color_base, width=2), connectgaps=False))
        self.fig.add_trace(go.Scatter(
            x=x, y=base_low, name='4PS base low', showlegend=False,
            line=dict(color=self.color_base, width=1, dash='dot'), connectgaps=False))

        self.fig.add_trace(go.Scatter(
            x=x, y=_mask('fps_stop'), name='4PS stop', showlegend=False,
            line=dict(color='#C0392B', width=1, dash='dash'), connectgaps=False))
        self.fig.add_trace(go.Scatter(
            x=x, y=_mask('fps_target'), name='4PS target', showlegend=False,
            line=dict(color='#2E7D32', width=1, dash='dot'), connectgaps=False))

        if 'fps_buy' in df.columns:
            self.fig.add_trace(go.Scatter(
                x=x, y=pd.to_numeric(df['fps_buy'], errors='coerce'), mode='markers',
                marker_symbol='triangle-up', marker_color=self.color_base,
                marker_line_width=1, marker_size=13, name='4PS buy', showlegend=False))
        if 'fps_sell' in df.columns:
            self.fig.add_trace(go.Scatter(
                x=x, y=pd.to_numeric(df['fps_sell'], errors='coerce'), mode='markers',
                marker_symbol='triangle-down', marker_color='#C0392B',
                marker_line_width=1, marker_size=13, name='4PS sell', showlegend=False))

        # Current levels as labelled hlines (overlay path keeps xref='paper')
        try:
            phase = float(pd.to_numeric(df['fps_phase'], errors='coerce').iloc[-1])
            last_high = float(base_high.dropna().iloc[-1]) if base_high.notna().any() else 0.0
            last_stop = float(_mask('fps_stop').dropna().iloc[-1]) \
                if _mask('fps_stop').notna().any() else 0.0
            if phase <= 2 and last_high > 0:
                self._add_hline_outside(last_high, f'4PS breakout {last_high:.2f}',
                                        line_color=self.color_base)
            elif phase >= 3 and last_stop > 0:
                self._add_hline_outside(last_stop, f'4PS stop {last_stop:.2f}',
                                        line_color='#C0392B')
        except Exception:
            pass
