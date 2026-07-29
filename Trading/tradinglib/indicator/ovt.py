"""Overall(Value)Trend indicator — hybrid A+B.

Exposes the composite scores ``overallTrend`` (technical) and
``overallValueTrend`` (value/fundamental) — the same numbers the backtest
engine stores in ``asset_simulation_*.db`` — as a live oscillator, plus an
EMA of the value trend (``ovtEma{span}``, matching ``OvtEmaUpdater``).

Design (no logic duplicated):

* **B — stored values** are read straight from ``asset_simulation_*.db`` per
  ``ticker`` + date and are authoritative wherever they exist (exact match with
  signals / notifier / backtest).
* **A — live fallback** fills every bar *not* covered by the sim DB (the recent
  tail / current intraday candle, or a ticker that was never simulated) by
  calling :func:`asset_perf2.score_df` — the very same vectorised scorer the
  engine uses — on the live chart DataFrame. Only the few scalar inputs that
  are not present live (``vola``/``sharpe``/``sortino``/``logVola``/
  ``wkTrend``/``moTrend``/``roa`` + the ``asset_info`` fundamentals) are
  reconstructed here with the same helpers ``asset_perf2`` uses; the per-bar
  technical columns (``ewo``/``macd_trend``/``adx``/``rsi``/``momentum``/
  ``sma*``/``ha_ema_*``) are taken as-is from the chart DataFrame.

The stored branch is accurate regardless of which indicators are enabled; the
live tail is most faithful when ewo, macd, adx, rsi and heikin are also active
(otherwise ``score_df`` sees those columns as 0 → a degraded but non-crashing
approximation).
"""

import math
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator


class Ovt(_indicator._Indicator):

    is_oszilator = True
    name = 'Overall (Value) Trend'

    params = {
        'ema_span':     {'type': 'int',   'default': 9, 'min': 2, 'max': 50, 'label': 'Value-trend EMA span'},
        'color_value':  {'type': 'color', 'default': '', 'label': 'Value-trend line color'},
        'color_trend':  {'type': 'color', 'default': '', 'label': 'Trend line color'},
    }

    # Table / DB names are engine conventions (see CLAUDE.md).
    _SIM_TABLE = 'asset_simulation'

    def __init__(self, df, symbol="", ema_span=9, color_value='', color_trend=''):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        self.ema_span = int(ema_span)
        self.color_value = color_value or 'teal'
        self.color_trend = color_trend or 'darkorange'
        super().__init__(df=df, symbol=symbol)

        self.data()

    # ------------------------------------------------------------------ B ---
    def _date_keys(self, index) -> pd.Series:
        """Normalise a (string or datetime) index to 'YYYY-MM-DD' keys."""
        return pd.to_datetime(pd.Series(list(index)), errors='coerce').dt.strftime('%Y-%m-%d')

    def _read_stored_scores(self, symbol, years):
        """Return {date_key: (overallTrend, overallValueTrend)} from the sim DBs.

        Spans as many ``asset_simulation_{year}.db`` files as the chart covers;
        the current calendar year lives in ``asset_simulation_.db`` (engine
        naming convention). Missing DBs are skipped silently → those bars fall
        through to the live branch.
        """
        import os
        from datetime import datetime
        from tradinglib.tools import open_db, Tools

        out = {}
        if not symbol:
            return out
        cur_year = datetime.now().year
        tools = Tools()
        for yr in sorted({y for y in years if y}):
            db_name = 'asset_simulation_.db' if int(yr) == cur_year else f'asset_simulation_{int(yr)}.db'
            path = tools.get_path(path='database', file_name=db_name)
            if not path or not os.path.exists(path):
                continue
            try:
                conn = open_db(path, readonly=True)
                try:
                    rows = conn.execute(
                        f"SELECT Date, overallTrend, overallValueTrend "
                        f"FROM {self._SIM_TABLE} WHERE ticker = ?", (symbol,)
                    ).fetchall()
                finally:
                    conn.close()
            except Exception as e:
                self._log(f"stored-score read failed for {db_name}: {e}")
                continue
            for d, ot, ovt in rows:
                key = str(d)[:10]
                if ot is not None or ovt is not None:
                    out[key] = (ot, ovt)
        return out

    # ------------------------------------------------------------------ A ---
    def _live_scores(self, df):
        """Full-length live (overallTrend, overallValueTrend) via score_df.

        Reuses the engine scorer — only the inputs missing from the live chart
        DataFrame are reconstructed here.
        """
        try:
            from asset_perf2 import score_df, get_roa
        except Exception as e:
            self._log(f"score_df import failed, live branch disabled: {e}")
            return None, None

        work = df.copy()

        # score_df reads lowercase 'close'; the live chart uses 'Close'.
        if 'close' not in work.columns and 'Close' in work.columns:
            work['close'] = work['Close']

        # daily_returns underpins the 'vola' scalar (same formula as asset_perf2).
        if 'daily_returns' not in work.columns and 'Close' in work.columns:
            work['daily_returns'] = work['Close'].pct_change(fill_method=None) * 100

        def _const(col, value):
            """Broadcast a per-ticker scalar as a constant column (don't clobber live per-bar columns)."""
            if col not in work.columns:
                try:
                    work[col] = float(value) if value == value else 0.0  # NaN-safe
                except Exception:
                    work[col] = 0.0

        # Scalars asset_perf2 stores per row but the live chart lacks — same helpers.
        try:
            _const('vola', round(work['daily_returns'].std() * math.sqrt(21), 1)
                   if 'daily_returns' in work.columns else 0.0)
        except Exception:
            _const('vola', 0.0)
        try:
            _const('logVola', indicator.log_return(work)['log_vola'].iloc[-1])
        except Exception:
            _const('logVola', 0.0)
        try:
            _const('sharpe', indicator.sharpe_ratio(work))
        except Exception:
            _const('sharpe', 0.0)
        try:
            _const('sortino', indicator.sortino_ratio(work))
        except Exception:
            _const('sortino', 0.0)
        try:
            wk = work['Close'].resample('W').last().to_frame('Close') \
                if isinstance(work.index, pd.DatetimeIndex) else None
        except Exception:
            wk = None
        try:
            _const('wkTrend', indicator.trend_pct_df(wk) if wk is not None else 0.0)
            _const('moTrend', 0.0)
        except Exception:
            _const('wkTrend', 0.0); _const('moTrend', 0.0)
        try:
            _const('roa', get_roa(self.symbol) if self.symbol else 0.0)
        except Exception:
            _const('roa', 0.0)

        info_df = self._read_asset_info(self.symbol)

        try:
            scored = score_df(work, info_df)
        except Exception as e:
            self._log(f"score_df failed: {e}")
            return None, None
        return scored.get('overallTrend'), scored.get('overallValueTrend')

    def _read_asset_info(self, symbol):
        """Single asset_info row as a 1-row DataFrame (fundamentals for score_df), or None."""
        import os
        from tradinglib.tools import open_db, Tools
        if not symbol:
            return None
        path = Tools().get_path(path='database', file_name='asset_info.db')
        if not path or not os.path.exists(path):
            return None
        try:
            conn = open_db(path, readonly=True)
            try:
                return pd.read_sql_query(
                    "SELECT * FROM asset_info WHERE ticker = ? LIMIT 1", conn, params=(symbol,))
            finally:
                conn.close()
        except Exception as e:
            self._log(f"asset_info read failed: {e}")
            return None

    def _log(self, msg):
        """Best-effort debug logging (indicators run in many contexts)."""
        try:
            import logging
            logging.getLogger(__name__).debug("Ovt(%s): %s", self.symbol, msg)
        except Exception:
            pass

    # ---------------------------------------------------------------- glue --
    def data(self):
        """Compute overallTrend / overallValueTrend / ovtEma{span} and attach them to self.df."""
        df = self.df
        keys = self._date_keys(df.index)
        years = pd.to_datetime(pd.Series(list(df.index)), errors='coerce').dt.year.dropna().astype(int).tolist()

        stored = self._read_stored_scores(self.symbol, set(years))
        trend_stored = keys.map(lambda k: stored.get(k, (np.nan, np.nan))[0]).astype(float)
        value_stored = keys.map(lambda k: stored.get(k, (np.nan, np.nan))[1]).astype(float)
        trend_stored.index = df.index
        value_stored.index = df.index

        # Live fallback only if any bar is uncovered (all-covered charts skip the work).
        if trend_stored.isna().any() or value_stored.isna().any():
            live_trend, live_value = self._live_scores(df)
        else:
            live_trend = live_value = None

        def _merge(stored_s, live_s):
            if live_s is None:
                return stored_s
            live_s = pd.Series(np.asarray(live_s, dtype=float), index=df.index)
            return stored_s.where(stored_s.notna(), live_s)

        df['overallTrend'] = _merge(trend_stored, live_trend)
        df['overallValueTrend'] = _merge(value_stored, live_value)

        # EMA of the value trend — same semantics as OvtEmaUpdater (ewm over overallValueTrend).
        span = self.ema_span
        df[f'ovtEma{span}'] = df['overallValueTrend'].ewm(span=span, adjust=False).mean()

        self.df = df

    def add_fig(self):
        """Add the indicator traces to a fresh oscillator figure."""
        span = self.ema_span
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass
        # The chart index is named 'Date'; guard the rare unnamed-index case so a
        # reset produces a usable x-axis instead of raising.
        if 'Date' not in self.df.columns and len(self.df.columns):
            self.df = self.df.rename(columns={self.df.columns[0]: 'Date'})

        x = self.df['Date']

        # Value trend (primary) — filled toward zero for a heatmap-like read.
        self.fig.add_trace(go.Scatter(
            x=x, y=self.df['overallValueTrend'],
            name='Value trend', showlegend=False,
            mode='lines', line=dict(color=self.color_value, width=2),
            fill='tozeroy', fillcolor='rgba(0,128,128,0.12)',
        ))

        # EMA of the value trend — solid dark grey so it stays readable over the
        # teal value-trend fill (the previous dotted light grey was near-invisible).
        self.fig.add_trace(go.Scatter(
            x=x, y=self.df[f'ovtEma{span}'],
            name=f'Value EMA{span}', showlegend=False,
            mode='lines', line=dict(color='dimgray', width=1.5),
        ))

        # Technical trend.
        self.fig.add_trace(go.Scatter(
            x=x, y=self.df['overallTrend'],
            name='Trend', showlegend=False,
            mode='lines', line=dict(color=self.color_trend, width=2),
        ))

        # Reference levels — plain hlines only (shapes transfer to the sub-plot as
        # 'x{row} domain' → safe). NOTE: do NOT use _add_hline_outside here: that
        # helper emits a layout *annotation* with xref='paper'/x=0.0, and the
        # oscillator sub-plot path in tiny_chart re-homes annotations via
        # add_annotation(row=1, col=1), which rewrites xref='paper'→'x' — turning
        # x=0.0 into the epoch date 1970-01-01 and stretching the shared date axis.
        # (The overlay path handles this correctly; the sub-plot path does not.)
        self.fig.add_hline(y=0, line_width=1, line_dash='dot', line_color='grey')
        self.fig.add_hline(y=50, line_width=1, line_dash='dot', line_color='green')
        self.fig.add_hline(y=-50, line_width=1, line_dash='dot', line_color='firebrick')
