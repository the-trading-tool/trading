"""Measurement harness for selection scores, gates and risk profiles.

Answers one question for any candidate building block: does it add anything
over simply holding the universe? Everything here is **read-only** — the panel
is assembled from ``asset_simulation_*.db`` with ``mode=ro`` connections, so a
measurement run can never rewrite production data (unlike the chart path, where
a Yahoo fallback writes back into ``yf_*.db``).

What it reports, and why each number is here:

* **IC** — mean cross-sectional Spearman correlation between a column and the
  forward return, with a t-value and a hit rate. This is the honest yardstick
  for a *ranking*: it says how well the column sorts the universe on any given
  day, not how one backtest happened to run.
* **Bucket table** — mean forward return, hit rate, forward volatility and
  forward drawdown per quantile. A column can have a fine IC and still be
  useless if the payoff is not monotone.
* **Gate stats** — for a boolean condition (``close > sma200`` and friends):
  coverage, and the *edge over the universe baseline*. A gate that selects 45 %
  of all rows and returns less than the universe is not a filter, it is a tax.
* **Conformity** — for a risk profile: the realised forward volatility and
  drawdown of what the profile actually selects, against the band it promised.
  That is the profile's own claim, and the only one it can be held to.
* **Turnover** — how much of the selection changes from day to day. An edge
  that needs a full rotation every week is a cost estimate, not a strategy.

Every metric is also broken down **per year**. That is not decoration: bucket
comparisons pooled over several years have produced sign flips here before
(Simpson's paradox in the market-phase work), so a pooled number alone is not
evidence.

Two data caveats the report repeats on every run, because they cannot be fixed
from inside this module:

* The ticker universe is today's index membership without history, so anything
  mean-reversion-like is flattered by survivors.
* Returns are gross — no fees, no slippage, no spread.

Typical use (see ``score_eval.py`` in the project root for the CLI)::

    from tradinglib import score_eval as se
    panel = se.load_panel(years=[2023, 2024, 2025], columns=['overallValueTrend'])
    print(se.format_report(se.evaluate(panel, columns=['overallValueTrend'])))
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

SIM_TABLE = 'asset_simulation'

# Columns the panel always needs; callers add the ones they want to measure.
BASE_COLUMNS = ('close',)

# Trading days per year, for annualising volatility.
TRADING_DAYS = 252

# Provisional risk bands, read off the measured distribution of the universe
# (2023-2025). They live here so the harness is usable on its own; once
# ``risk_profile.py`` exists it owns the calibration and this dict becomes a
# fallback for standalone measurement runs.
#   fwd_vol — annualised realised volatility over the holding window
#   fwd_mdd — worst close relative to the entry price within the window
# Bands are (floor, ceiling) and open-ended on the harmless side: a position
# that never traded below its entry has a drawdown above zero and obviously
# honours a drawdown limit, so the ceiling for fwd_mdd is infinite.
DEFAULT_BANDS = {
    'conservative': {'fwd_vol': (0.0, 0.25), 'fwd_mdd': (-0.25, float('inf'))},
    'balanced':     {'fwd_vol': (0.0, 0.39), 'fwd_mdd': (-0.35, float('inf'))},
    'dynamic':      {'fwd_vol': (0.0, 0.49), 'fwd_mdd': (-0.50, float('inf'))},
    'offensive':    {'fwd_vol': (0.0, float('inf')), 'fwd_mdd': (-1.00, float('inf'))},
}


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

@dataclass
class Panel:
    """A ticker x date panel with forward-looking targets already attached."""

    df: pd.DataFrame
    horizon: int
    years: list
    diagnostics: dict = field(default_factory=dict)

    @property
    def baseline(self) -> dict:
        """Universe averages — the bar every gate and every score has to clear."""
        d = self.df
        return {
            'n': len(d),
            'fwd_ret': d['fwd_ret'].mean(),
            'hit': (d['fwd_ret'] > 0).mean(),
            'fwd_vol': d['fwd_vol'].mean(),
            'fwd_mdd': d['fwd_mdd'].mean(),
        }


def _sim_db_name(year: int) -> str:
    """Engine naming convention: the current year lives in asset_simulation_.db."""
    return ('asset_simulation_.db' if int(year) == datetime.now().year
            else f'asset_simulation_{int(year)}.db')


def available_years(db_path: str = 'database') -> list:
    """Years for which a simulation DB exists on disk, ascending."""
    tools = Tools()
    out = []
    for year in range(2015, datetime.now().year + 1):
        path = tools.get_path(path=db_path, file_name=_sim_db_name(year))
        if path and os.path.exists(path):
            out.append(year)
    return out


def _reverse_rolling(series: pd.Series, window: int, how: str) -> pd.Series:
    """Apply a rolling aggregate over the *next* `window` bars, not the past ones."""
    rev = series.iloc[::-1]
    agg = getattr(rev.rolling(window, min_periods=max(2, window // 2)), how)()
    return agg.iloc[::-1]


def load_panel(years=None, columns=(), horizon: int = 21, db_path: str = 'database',
               min_price: float = 1.0, max_abs_return: float = 1.0,
               min_cross_section: int = 200, tickers=None) -> Panel:
    """Load the measurement panel from the simulation DBs (read-only).

    *columns* are extra simulation columns to carry along; ticker, Date and
    close always come with it. Rows are dropped when they cannot support an
    honest measurement:

    * penny prices (``min_price``), where a one-cent tick is a double-digit
      percentage;
    * forward returns beyond ``max_abs_return``, which in this data are
      overwhelmingly uncorrected splits or pence/pound mix-ups rather than real
      moves (see ``data_quality.py``);
    * dates whose cross-section is thinner than ``min_cross_section``. The most
      recent day in a simulation DB is regularly half-written — a handful of
      tickers out of thousands — and a cross-sectional rank over nine rows
      still looks perfectly plausible in a table.

    Every drop is counted and reported, so a surprising result can be traced
    back to the filter that caused it.
    """
    tools = Tools()
    years = sorted(set(int(y) for y in (years or available_years(db_path))))
    if not years:
        raise ValueError("no simulation databases found")

    wanted = list(dict.fromkeys(list(BASE_COLUMNS) + [c for c in columns if c]))
    frames, missing_cols, skipped = [], {}, []
    for year in years:
        path = tools.get_path(path=db_path, file_name=_sim_db_name(year))
        if not path or not os.path.exists(path):
            skipped.append(year)
            continue
        conn = open_db(path, readonly=True)
        try:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({SIM_TABLE})")}
            use = [c for c in wanted if c in have]
            gone = [c for c in wanted if c not in have]
            if gone:
                missing_cols[year] = gone
            where = ''
            params = []
            if tickers:
                where = f" WHERE ticker IN ({','.join('?' * len(tickers))})"
                params = list(tickers)
            frame = pd.read_sql_query(
                f"SELECT ticker, Date, {', '.join(use)} FROM {SIM_TABLE}{where}",
                conn, params=params)
        finally:
            conn.close()
        frames.append(frame)

    if not frames:
        raise ValueError(f"no simulation data for years {years}")

    df = pd.concat(frames, ignore_index=True)
    raw_rows = len(df)

    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    # bulk_upsert gave some legacy columns TEXT affinity, so numbers can arrive
    # as strings — coerce everything that is meant to be numeric.
    for col in df.columns:
        if col not in ('ticker', 'Date', 'currency'):
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.dropna(subset=['Date', 'close'])
    df = df[df['close'] > min_price]
    df = df.sort_values(['ticker', 'Date']).reset_index(drop=True)
    after_price = len(df)

    grp = df.groupby('ticker', sort=False)['close']
    df['fwd_ret'] = grp.shift(-horizon) / df['close'] - 1.0
    df['ret'] = grp.pct_change()

    # Forward risk, measured from the entry bar the way a holder experiences it:
    # the volatility of the path ahead and the worst close it reaches.
    df['fwd_vol'] = (df.groupby('ticker', sort=False)['ret']
                     .transform(lambda s: _reverse_rolling(s, horizon, 'std'))
                     * np.sqrt(TRADING_DAYS))
    fwd_low = df.groupby('ticker', sort=False)['close'].transform(
        lambda s: _reverse_rolling(s.shift(-1), horizon, 'min'))
    df['fwd_mdd'] = fwd_low / df['close'] - 1.0

    df = df[df['fwd_ret'].notna()]
    after_fwd = len(df)
    df = df[df['fwd_ret'].abs() < max_abs_return]
    after_quality = len(df)

    cross = df.groupby('Date')['ticker'].transform('size')
    df = df[cross >= min_cross_section]
    after_cross = len(df)

    df['year'] = df['Date'].dt.year
    panel = Panel(df=df.reset_index(drop=True), horizon=horizon, years=years,
                  diagnostics={
                      'rows_raw': raw_rows,
                      'dropped_price': raw_rows - after_price,
                      'dropped_no_forward': after_price - after_fwd,
                      'dropped_quality': after_fwd - after_quality,
                      'dropped_thin_cross_section': after_quality - after_cross,
                      'rows': after_cross,
                      'dates': int(df['Date'].nunique()),
                      'tickers': int(df['ticker'].nunique()),
                      'missing_columns': missing_cols,
                      'skipped_years': skipped,
                  })
    return panel


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _daily_rank_corr(df: pd.DataFrame, x: str, y: str, group: str = 'Date') -> pd.Series:
    """Per-group Spearman correlation, as Pearson over within-group ranks.

    Written out as sums rather than groupby.apply because the panel runs to
    millions of rows and apply() on it costs minutes.
    """
    d = df[[group, x, y]].dropna()
    if d.empty:
        return pd.Series(dtype=float)
    rx = d.groupby(group)[x].rank()
    ry = d.groupby(group)[y].rank()
    work = pd.DataFrame({'g': d[group].values, 'x': rx.values, 'y': ry.values})
    work['xy'] = work['x'] * work['y']
    work['xx'] = work['x'] ** 2
    work['yy'] = work['y'] ** 2
    agg = work.groupby('g').agg(n=('x', 'size'), sx=('x', 'sum'), sy=('y', 'sum'),
                                sxy=('xy', 'sum'), sxx=('xx', 'sum'), syy=('yy', 'sum'))
    num = agg['n'] * agg['sxy'] - agg['sx'] * agg['sy']
    den = np.sqrt((agg['n'] * agg['sxx'] - agg['sx'] ** 2) *
                  (agg['n'] * agg['syy'] - agg['sy'] ** 2))
    out = num / den.replace(0, np.nan)
    return out[agg['n'] >= 10].dropna()


def ic(panel: Panel, column: str, target: str = 'fwd_ret', by_year: bool = True) -> dict:
    """Information coefficient of *column* against *target*.

    Returns the mean daily rank correlation, its t-value (over the daily series,
    so a long quiet stretch cannot fake significance), the share of days with a
    positive reading, and the same broken down per year.
    """
    df = panel.df
    if column not in df.columns:
        return {'column': column, 'error': 'column not in panel'}

    def _stats(frame):
        series = _daily_rank_corr(frame, column, target)
        if len(series) < 20:
            return None
        mean = float(series.mean())
        std = float(series.std())
        t = mean / std * np.sqrt(len(series)) if std > 0 else float('nan')
        return {'ic': mean, 't': float(t), 'hit': float((series > 0).mean()),
                'days': int(len(series))}

    out = {'column': column, 'target': target}
    out.update(_stats(df) or {'error': 'too few usable days'})
    if by_year:
        out['by_year'] = {int(y): _stats(g) for y, g in df.groupby('year')}
    return out


def neutralised_ic(panel: Panel, column: str, against: str,
                   target: str = 'fwd_ret') -> dict:
    """IC of *column* after removing what it shares with *against*, per day.

    The point is to find out how much of a composite's apparent skill sits in a
    single input — for the stored scores, one column of analyst price targets
    carried about a third of it.
    """
    df = panel.df
    for col in (column, against):
        if col not in df.columns:
            return {'column': column, 'against': against, 'error': f'{col} not in panel'}

    work = df[['Date', 'year', column, against, target]].dropna().copy()
    if work.empty:
        return {'column': column, 'against': against, 'error': 'no overlapping rows'}
    by_date = work.groupby('Date')
    work['rx'] = by_date[column].rank(pct=True)
    work['ra'] = by_date[against].rank(pct=True)

    # Per-date least-squares residual, written as group means rather than a
    # polyfit per group: the panel has hundreds of dates and millions of rows.
    by_date = work.groupby('Date')
    dx = work['rx'] - by_date['rx'].transform('mean')
    da = work['ra'] - by_date['ra'].transform('mean')
    work['_cov'] = (dx * da)
    work['_var'] = (da ** 2)
    cov = work.groupby('Date')['_cov'].transform('mean')
    var = work.groupby('Date')['_var'].transform('mean')
    size = by_date['rx'].transform('size')
    slope = (cov / var.replace(0, np.nan)).where(size >= 30)
    work['residual'] = dx - slope * da
    raw = ic(panel, column, target, by_year=False)
    if work['residual'].abs().max() < 1e-12:
        # Nothing survived the projection — the column was a relabelled copy of
        # the one it was measured against, so its own contribution is exactly nil.
        return {'column': column, 'against': against, 'target': target,
                'raw_ic': raw.get('ic'), 'residual_ic': 0.0,
                'raw_t': raw.get('t'), 'residual_t': 0.0}
    sub = Panel(df=work, horizon=panel.horizon, years=panel.years)
    res = ic(sub, 'residual', target, by_year=False)
    return {'column': column, 'against': against, 'target': target,
            'raw_ic': raw.get('ic'), 'residual_ic': res.get('ic'),
            'raw_t': raw.get('t'), 'residual_t': res.get('t')}


def buckets(panel: Panel, column: str, n: int = 5) -> pd.DataFrame:
    """Per-quantile forward return, hit rate, forward volatility and drawdown.

    Quantiles are formed within each day, so the table reads as "what did the
    top fifth of the universe on that day go on to do".
    """
    df = panel.df
    if column not in df.columns:
        raise KeyError(f"{column} not in panel")
    work = df[['Date', 'year', column, 'fwd_ret', 'fwd_vol', 'fwd_mdd']].dropna(
        subset=[column, 'fwd_ret']).copy()
    work['bucket'] = work.groupby('Date')[column].transform(
        lambda s: pd.qcut(s.rank(method='first'), n, labels=False, duplicates='drop'))
    table = work.groupby('bucket').agg(
        n=('fwd_ret', 'size'),
        fwd_ret=('fwd_ret', 'mean'),
        hit=('fwd_ret', lambda s: (s > 0).mean()),
        fwd_vol=('fwd_vol', 'mean'),
        fwd_mdd=('fwd_mdd', 'mean'),
    )
    table.index = table.index.astype(int) + 1
    table.index.name = 'quantile'
    return table


def gate(panel: Panel, condition, name: str = '') -> dict:
    """Evaluate a boolean selection against the universe baseline.

    *condition* is either a boolean Series aligned to the panel or a pandas
    expression over the panel columns (the same syntax the Strategy Finder
    formulas use), e.g. ``"(close > sma200) & (sma50 > sma200)"``.
    """
    df = panel.df
    if isinstance(condition, str):
        try:
            mask = df.eval(condition)
        except Exception as exc:
            return {'name': name or condition, 'error': f'expression failed: {exc}'}
        if not pd.api.types.is_bool_dtype(mask):
            return {'name': name or condition, 'error': 'expression is not boolean'}
    else:
        mask = condition.reindex(df.index)
    mask = mask.fillna(False).astype(bool)

    base = panel.baseline
    sel = df[mask]
    if len(sel) < 100:
        return {'name': name or str(condition), 'n': int(len(sel)),
                'error': 'selection too small to judge'}

    def _stats(frame, ref):
        return {
            'n': int(len(frame)),
            'coverage': len(frame) / len(ref) if len(ref) else float('nan'),
            'fwd_ret': float(frame['fwd_ret'].mean()),
            'edge': float(frame['fwd_ret'].mean() - ref['fwd_ret'].mean()),
            'hit': float((frame['fwd_ret'] > 0).mean()),
            'fwd_vol': float(frame['fwd_vol'].mean()),
            'fwd_mdd': float(frame['fwd_mdd'].mean()),
        }

    out = {'name': name or str(condition), 'baseline': base}
    out.update(_stats(sel, df))
    out['by_year'] = {}
    for year, group in df.groupby('year'):
        part = group[mask.reindex(group.index).fillna(False)]
        if len(part) >= 100:
            out['by_year'][int(year)] = _stats(part, group)
    out['turnover'] = turnover(panel, mask)
    return out


def turnover(panel: Panel, mask: pd.Series) -> float:
    """Mean share of the selection that changes from one date to the next.

    0.0 means the same names every day, 1.0 a complete rotation. Read together
    with the edge: a gate whose edge is 0.2 % per month at 30 % daily turnover
    is already spent before costs.
    """
    df = panel.df
    sel = df.loc[mask.reindex(df.index).fillna(False).astype(bool), ['Date', 'ticker']]
    if sel.empty:
        return float('nan')
    per_day = sel.groupby('Date')['ticker'].apply(set)
    if len(per_day) < 2:
        return float('nan')
    changes = []
    previous = None
    for _, names in per_day.items():
        if previous is not None and (previous or names):
            union = previous | names
            changes.append(len(union - (previous & names)) / len(union) if union else 0.0)
        previous = names
    return float(np.mean(changes)) if changes else float('nan')


def conformity(panel: Panel, condition, band: dict, name: str = '') -> dict:
    """Does a selection stay inside the risk band its profile promised?

    A risk profile is a claim about volatility and drawdown, not about return —
    and unlike return, both are strongly persistent, so the claim is testable.
    Reports the realised mean and the share of selected rows inside the band,
    per metric.
    """
    df = panel.df
    if isinstance(condition, str):
        try:
            mask = df.eval(condition).fillna(False).astype(bool)
        except Exception as exc:
            return {'name': name or condition, 'error': f'expression failed: {exc}'}
    else:
        mask = condition.reindex(df.index).fillna(False).astype(bool)

    sel = df[mask]
    if len(sel) < 100:
        return {'name': name, 'n': int(len(sel)), 'error': 'selection too small to judge'}

    out = {'name': name or str(condition), 'n': int(len(sel)), 'metrics': {}}
    for metric, (low, high) in band.items():
        if metric not in sel.columns:
            continue
        values = sel[metric].dropna()
        if values.empty:
            continue
        inside = ((values >= low) & (values <= high)).mean()
        # The tail is the end of the distribution that breaks the promise: the
        # low quantile for a drawdown floor, the high one for everything else
        # (a volatility ceiling, and any band whose bounds do not bind).
        tail_q = 0.1 if float('-inf') < low < 0 else 0.9
        out['metrics'][metric] = {
            'mean': float(values.mean()),
            'median': float(values.median()),
            'tail': float(values.quantile(tail_q)),
            'tail_quantile': tail_q,
            'band': (low, high),
            'inside': float(inside),
        }
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def evaluate(panel: Panel, columns=(), gates=None, profiles=None,
             neutralise: str = '', bucket_count: int = 5) -> dict:
    """Run the whole battery and return a plain dict (JSON/UI friendly).

    *gates* and *profiles* are ``{name: expression}`` mappings; profiles are
    additionally checked against ``DEFAULT_BANDS[name]`` when the name matches a
    known profile, otherwise against the balanced band.
    """
    report = {
        'horizon': panel.horizon,
        'years': panel.years,
        'diagnostics': panel.diagnostics,
        'baseline': panel.baseline,
        'ic': [],
        'buckets': {},
        'gates': [],
        'profiles': [],
        'caveats': [
            'universe is the current index membership without history — survivorship flatters '
            'mean-reversion-like signals',
            'returns are gross: no fees, no slippage, no spread',
        ],
    }
    for column in columns:
        entry = ic(panel, column)
        report['ic'].append(entry)
        if neutralise and column != neutralise:
            entry['neutralised'] = neutralised_ic(panel, column, neutralise)
        if 'error' not in entry:
            try:
                report['buckets'][column] = buckets(panel, column, bucket_count)
            except Exception as exc:
                logger.debug("bucket table failed for %s: %s", column, exc)

    for name, expression in (gates or {}).items():
        report['gates'].append(gate(panel, expression, name=name))

    for name, expression in (profiles or {}).items():
        band = DEFAULT_BANDS.get(name, DEFAULT_BANDS['balanced'])
        entry = gate(panel, expression, name=name)
        entry['conformity'] = conformity(panel, expression, band, name=name)
        report['profiles'].append(entry)

    return report


def _pct(value, digits=2):
    return 'n/a' if value is None or value != value else f"{value * 100:.{digits}f}"


def format_report(report: dict) -> str:
    """Render a report as plain text for the console or a Streamlit code block."""
    lines = []
    diag = report.get('diagnostics', {})
    base = report.get('baseline', {})
    lines.append("=" * 78)
    lines.append(f"Score evaluation — horizon {report.get('horizon')} bars, "
                 f"years {', '.join(str(y) for y in report.get('years', []))}")
    lines.append("=" * 78)
    lines.append(f"panel: {diag.get('rows', 0):,} rows | {diag.get('dates', 0)} dates | "
                 f"{diag.get('tickers', 0)} tickers")
    dropped = {k: v for k, v in diag.items() if k.startswith('dropped_') and v}
    if dropped:
        lines.append("dropped: " + ", ".join(f"{k[8:]} {v:,}" for k, v in dropped.items()))
    if diag.get('missing_columns'):
        lines.append(f"missing columns: {diag['missing_columns']}")
    lines.append(f"baseline: fwd_ret {_pct(base.get('fwd_ret'))}% | "
                 f"hit {_pct(base.get('hit'), 1)}% | vol {base.get('fwd_vol', float('nan')):.2f} | "
                 f"mdd {_pct(base.get('fwd_mdd'))}%")

    if report.get('ic'):
        lines.append("")
        lines.append("-- information coefficient vs forward return " + "-" * 33)
        lines.append(f"{'column':26s} {'IC':>8s} {'t':>8s} {'hit%':>7s} {'days':>6s}")
        for entry in report['ic']:
            if 'error' in entry:
                lines.append(f"{entry['column']:26s} {entry['error']}")
                continue
            lines.append(f"{entry['column']:26s} {entry['ic']:8.4f} {entry['t']:8.1f} "
                         f"{entry['hit'] * 100:7.1f} {entry['days']:6d}")
            per_year = entry.get('by_year') or {}
            parts = [f"{y}: {v['ic']:+.3f}" for y, v in sorted(per_year.items()) if v]
            if parts:
                lines.append(f"{'':26s} per year  " + "  ".join(parts))
            neu = entry.get('neutralised')
            if neu and 'error' not in neu:
                lines.append(f"{'':26s} without {neu['against']}: "
                             f"{neu['raw_ic']:.4f} -> {neu['residual_ic']:.4f}")

    for column, table in (report.get('buckets') or {}).items():
        lines.append("")
        lines.append(f"-- {column}: quantiles (1 = lowest) " + "-" * max(0, 40 - len(column)))
        lines.append(f"{'q':>3s} {'n':>9s} {'fwd_ret%':>9s} {'hit%':>7s} {'fwd_vol':>8s} {'fwd_mdd%':>9s}")
        for q, row in table.iterrows():
            lines.append(f"{q:3d} {int(row['n']):9,d} {row['fwd_ret'] * 100:9.2f} "
                         f"{row['hit'] * 100:7.1f} {row['fwd_vol']:8.2f} {row['fwd_mdd'] * 100:9.2f}")

    if report.get('gates'):
        lines.append("")
        lines.append("-- gates vs universe " + "-" * 56)
        lines.append(f"{'gate':26s} {'cover%':>7s} {'fwd%':>7s} {'edge':>7s} "
                     f"{'hit%':>6s} {'vol':>6s} {'mdd%':>7s} {'turn%':>6s}")
        for entry in report['gates'] + report.get('profiles', []):
            if 'error' in entry:
                lines.append(f"{entry['name'][:26]:26s} {entry['error']}")
                continue
            lines.append(f"{entry['name'][:26]:26s} {entry['coverage'] * 100:7.1f} "
                         f"{entry['fwd_ret'] * 100:7.2f} {entry['edge'] * 100:+7.2f} "
                         f"{entry['hit'] * 100:6.1f} {entry['fwd_vol']:6.2f} "
                         f"{entry['fwd_mdd'] * 100:7.2f} "
                         f"{entry['turnover'] * 100 if entry['turnover'] == entry['turnover'] else float('nan'):6.1f}")
            parts = [f"{y}: {v['edge'] * 100:+.2f}" for y, v in sorted(entry.get('by_year', {}).items())]
            if parts:
                lines.append(f"{'':26s} edge per year  " + "  ".join(parts))
        if report.get('profiles'):
            lines.append("  (the last rows are risk profiles — they are judged by the conformity "
                         "section below, not by their edge)")

    for entry in report.get('profiles', []):
        conf = entry.get('conformity') or {}
        if 'error' in conf or not conf.get('metrics'):
            continue
        lines.append("")
        lines.append(f"-- profile conformity: {entry['name']} " + "-" * max(0, 40 - len(entry['name'])))
        for metric, values in conf['metrics'].items():
            low, high = values['band']
            high_txt = '  inf' if high == float('inf') else f"{high:+.2f}"
            lines.append(f"  {metric:8s} mean {values['mean']:+.3f} | median {values['median']:+.3f} "
                         f"| p{int(values['tail_quantile'] * 100)} {values['tail']:+.3f} "
                         f"| band [{low:+.2f}, {high_txt}] | inside {values['inside'] * 100:.1f}%")

    if report.get('caveats'):
        lines.append("")
        lines.append("-- caveats " + "-" * 66)
        for caveat in report['caveats']:
            lines.append(f"  * {caveat}")
    return "\n".join(lines)
