"""Robustness evaluation for Strategy Finder results (no Streamlit).

A single backtest number says little on its own. Three questions decide whether a
formula is worth trading:

* **Compared to what?** 30 % is weak when buy-and-hold of the index made 40 %, and
  a strategy that sits in cash half the time must be judged per euro deployed.
* **Is it one lucky year?** The total can be carried by a single year.
* **Was it fitted to the data it is measured on?** The optimizer picks the best
  parameters on the same data it reports. A split into a training period (to
  choose) and a test period (to judge) separates signal from curve fitting.

Plus the noise floor: in a slot-limited portfolio the order in which same-day
signals are taken moves the annual result by +-10-15 % on its own (measured on the
multi strategies). ``percentile_rank`` places the actual run in the distribution of
runs with random order.

Everything here works on the trades the simulator produced and on the close panel
of the frame it ran on -- no database access, so the numbers match the run exactly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# --------------------------------------------------------------------- inputs

def close_panel(df: pd.DataFrame, date_col: str = 'Date', ticker_col: str = 'ticker',
                close_col: str = 'close') -> pd.DataFrame:
    """Dates x tickers matrix of closes, forward-filled, from the simulation frame."""
    if df is None or df.empty or close_col not in df.columns:
        return pd.DataFrame()
    d = df[[date_col, ticker_col, close_col]].copy()
    d[date_col] = pd.to_datetime(d[date_col], errors='coerce').dt.normalize()
    d[close_col] = pd.to_numeric(d[close_col], errors='coerce')
    d = d.dropna().drop_duplicates([date_col, ticker_col], keep='last')
    panel = d.pivot(index=date_col, columns=ticker_col, values=close_col).sort_index()
    return panel.ffill()


def _norm_trades(trades: pd.DataFrame) -> pd.DataFrame:
    t = trades.copy()
    t['buyDate'] = pd.to_datetime(t.get('buyDate'), errors='coerce').dt.normalize()
    t['sellDate'] = pd.to_datetime(t.get('sellDate'), errors='coerce').dt.normalize()
    t['bv'] = pd.to_numeric(t.get('buyValue'), errors='coerce').abs()
    t['gain'] = pd.to_numeric(t.get('gain'), errors='coerce')
    sv = pd.to_numeric(t.get('sellVolume'), errors='coerce').fillna(0)
    t['closed'] = (sv > 0) & t['sellDate'].notna() & t['gain'].notna()
    return t.dropna(subset=['buyDate', 'bv'])


# --------------------------------------------------------------------- curves

def mtm_equity(trades: pd.DataFrame, panel: pd.DataFrame, initial_cash: float):
    """Daily mark-to-market equity and invested value.

    Position value = |buyValue| * close(d) / close(buyDate), so the currency of the
    listing cancels out. A closed trade books its realised ``gain`` from the sell
    date on; an open trade is marked until the last date of the panel.

    Returns (equity, invested) as Series on the panel's dates.
    """
    if panel.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    dates = panel.index
    pnl = pd.Series(0.0, index=dates)
    invested = pd.Series(0.0, index=dates)
    if trades is not None and not trades.empty:
        t = _norm_trades(trades)
        last = dates[-1]
        for r in t.itertuples():
            if r.ticker not in panel.columns:
                continue
            s = panel[r.ticker]
            base = s.asof(r.buyDate) if r.buyDate >= dates[0] else np.nan
            if pd.isna(base) or base == 0:
                continue
            end = r.sellDate if r.closed else last + pd.Timedelta(days=1)
            hold = (dates >= r.buyDate) & (dates < end)
            ratio = (s[hold] / base).fillna(1.0)
            pnl[hold] += r.bv * (ratio - 1.0)
            invested[hold] += r.bv * ratio
            if r.closed:
                pnl[dates >= r.sellDate] += r.gain
    return float(initial_cash) + pnl, invested


def buy_and_hold(prices: pd.Series, dates: pd.DatetimeIndex, initial_cash: float) -> pd.Series:
    """Equity of holding *prices* over *dates*, starting at initial_cash."""
    if prices is None or prices.empty:
        return pd.Series(dtype=float)
    p = pd.to_numeric(prices, errors='coerce').dropna().sort_index()
    p = p[~p.index.duplicated(keep='last')]
    p = p.reindex(p.index.union(dates)).ffill().reindex(dates)
    first = p.first_valid_index()
    if first is None:
        return pd.Series(dtype=float)
    return float(initial_cash) * p / p.loc[first]


def equal_weight(panel: pd.DataFrame, initial_cash: float) -> pd.Series:
    """Equity of an equally weighted, daily rebalanced universe (mean daily return)."""
    if panel.empty:
        return pd.Series(dtype=float)
    rets = panel.pct_change(fill_method=None)
    # A ticker that enters later or has a gap contributes nothing that day; extreme
    # moves from bad bars (split artefacts) are clipped so they cannot dominate.
    daily = rets.clip(-0.5, 0.5).mean(axis=1, skipna=True).fillna(0.0)
    daily.iloc[0] = 0.0
    return float(initial_cash) * (1.0 + daily).cumprod()


# --------------------------------------------------------------------- stats

def stats(equity: pd.Series) -> dict:
    """Total return, CAGR, max drawdown, volatility and return/volatility (all %)."""
    e = equity.dropna()
    if len(e) < 2 or e.iloc[0] <= 0:
        return {'total': np.nan, 'cagr': np.nan, 'max_dd': np.nan, 'vol': np.nan, 'ret_vol': np.nan}
    total = e.iloc[-1] / e.iloc[0] - 1.0
    years = max((e.index[-1] - e.index[0]).days / 365.25, 1 / 365.25)
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else -1.0
    dd = (e / e.cummax() - 1.0).min()
    vol = e.pct_change().dropna().std() * np.sqrt(TRADING_DAYS)
    return {'total': total * 100, 'cagr': cagr * 100, 'max_dd': dd * 100,
            'vol': vol * 100, 'ret_vol': (cagr / vol) if vol > 0 else np.nan}


def avg_exposure(invested: pd.Series, equity: pd.Series) -> float:
    """Mean share of equity that was invested (0..1)."""
    if invested.empty or equity.empty:
        return np.nan
    q = (invested / equity.replace(0, np.nan)).clip(lower=0).dropna()
    return float(q.mean()) if len(q) else np.nan


def _period_return(e: pd.Series, start_value: float) -> float:
    if e.empty or not start_value:
        return np.nan
    return (e.iloc[-1] / start_value - 1.0) * 100


def yearly(curves: dict, trades: pd.DataFrame | None = None, strategy_key: str | None = None) -> pd.DataFrame:
    """Per calendar year: return of every curve, the strategy's max drawdown,
    number of buys and the win rate of trades closed in that year.

    A year's return is measured from the last value of the previous year, so the
    years chain to the total. ``strategy_key`` names the curve whose drawdown and
    trades are reported (default: the first curve).
    """
    if not curves:
        return pd.DataFrame()
    key = strategy_key or next(iter(curves))
    ref = curves[key].dropna()
    if ref.empty:
        return pd.DataFrame()
    t = _norm_trades(trades) if trades is not None and not trades.empty else None
    rows = []
    for y in sorted(set(ref.index.year)):
        row = {'year': int(y)}
        for name, e in curves.items():
            e = e.dropna()
            cur = e[e.index.year == y]
            prev = e[e.index.year < y]
            start = prev.iloc[-1] if len(prev) else (cur.iloc[0] if len(cur) else np.nan)
            row[name] = _period_return(cur, start)
        cur = ref[ref.index.year == y]
        prev = ref[ref.index.year < y]
        seg = pd.concat([prev.iloc[-1:], cur]) if len(prev) else cur
        row['max_dd'] = float((seg / seg.cummax() - 1.0).min() * 100) if len(seg) else np.nan
        if t is not None:
            row['buys'] = int((t['buyDate'].dt.year == y).sum())
            closed = t[t['closed'] & (t['sellDate'].dt.year == y)]
            row['win_rate'] = float((closed['gain'] > 0).mean() * 100) if len(closed) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index('year')


def split(curves: dict, split_date) -> pd.DataFrame:
    """Return and CAGR of every curve before and from *split_date* on.

    The test period starts from the value at the split, so a result that was only
    earned in the training period shows up as a gap between the two rows.
    """
    sd = pd.Timestamp(split_date).normalize()
    out = {}
    for name, e in curves.items():
        e = e.dropna()
        if e.empty:
            continue
        a, b = e[e.index < sd], e[e.index >= sd]
        res = {}
        for label, part, start in (('train', a, e.iloc[0]),
                                   ('test', b, a.iloc[-1] if len(a) else (b.iloc[0] if len(b) else np.nan))):
            if len(part) < 2:
                res[f'{label}_total'] = res[f'{label}_cagr'] = np.nan
                continue
            seg = pd.concat([pd.Series([start], index=[part.index[0] - pd.Timedelta(days=1)]), part])
            s = stats(seg)
            res[f'{label}_total'] = s['total']
            res[f'{label}_cagr'] = s['cagr']
        out[name] = res
    return pd.DataFrame(out).T


def percentile_rank(value: float, samples) -> float:
    """Share of *samples* below *value*, ties counted half (0..100)."""
    s = np.asarray([x for x in samples if x is not None and np.isfinite(x)], dtype=float)
    if not len(s) or value is None or not np.isfinite(value):
        return np.nan
    return float(((s < value).sum() + 0.5 * (s == value).sum()) / len(s) * 100)


def train_test_consistency(results: pd.DataFrame, train_col: str = 'train%',
                           test_col: str = 'test%') -> dict:
    """How well the training ranking carries into the test period.

    * ``rank_corr``: Spearman correlation of train and test results over all
      combinations -- near 0 means the ranking is noise.
    * ``best_train_test_pct``: percentile of the best-by-train combination within
      the test results -- 50 means it is merely average out of sample.
    """
    r = results[[train_col, test_col]].apply(pd.to_numeric, errors='coerce').dropna()
    if len(r) < 3:
        return {'rank_corr': np.nan, 'best_train_test_pct': np.nan, 'n': len(r)}
    corr = r[train_col].rank().corr(r[test_col].rank())
    best = r.sort_values(train_col, ascending=False).iloc[0]
    return {'rank_corr': float(corr),
            'best_train_test_pct': percentile_rank(best[test_col], r[test_col].tolist()),
            'n': int(len(r))}
