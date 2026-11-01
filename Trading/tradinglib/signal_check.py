"""Signal check: does a buy/sell formula know anything, apart from the portfolio?

A Strategy Finder result mixes three things: the formula, the slot mechanics
(which signals find a free slot, in which order) and luck in a few long-held
positions. Measured 2026-11 on kurt's ATC formulas: a filter that left the
single signals no better moved a ^MDAXI run from 32 % to 106 %, half of the
gain came from three trades, and the same formula lost to the market in four
of seven other indices. This module looks at the formula on its own:

* **Buy signals**: return from entry over the next 21/63 bars, minus the
  equally weighted universe over the same days. No slots, every signal counts.
* **Sell signals**: the same after a sell signal. A useful exit is followed by
  weaker weeks (negative); a positive value means it sells winners too early.
* **Random-entry control**: the formula's entries and random entries in the same
  tickers, both closed by the formula's sell rule. An exit that only sells at
  strength makes almost any entry look good (high win rate); the difference is
  what the buy rule adds.
* **Concentration**: share of the run's net gain from its best 3 and 5 trades.

Thresholds for the traffic lights are rules of thumb, stated next to each
result. Everything is computed on the frame the run used -- no DB access.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

HORIZONS = (21, 63)
MIN_EVENTS = 30          # fewer -> no verdict
MIN_YEAR_EVENTS = 10     # a year counts for "years positive" only with this many
RANDOM_SEEDS = (1, 2, 3)
MIN_MARKETS = 3          # other markets needed for a verdict

GREEN, YELLOW, RED, GREY = 'green', 'yellow', 'red', 'grey'

_TOKEN = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def formula_columns(df: pd.DataFrame, *formulas: str) -> list[str]:
    """Columns of *df* a formula refers to, plus what the check itself needs."""
    names = set()
    for f in formulas:
        names.update(_TOKEN.findall(f or ''))
    base = ['ticker', 'Date', 'close', 'stockIndex']
    return [c for c in dict.fromkeys(base + sorted(names)) if c in df.columns]


def prepare(df: pd.DataFrame, buy: str, sell: str) -> pd.DataFrame:
    """Narrow, time-ordered copy (ticker, Date) of the columns the formulas use."""
    d = df[formula_columns(df, buy, sell)].copy()
    d['Date'] = pd.to_datetime(d['Date'], errors='coerce')
    d['close'] = pd.to_numeric(d['close'], errors='coerce')
    d = d.dropna(subset=['Date', 'close'])
    d = d[d['close'] > 0]
    d = d.drop_duplicates(['ticker', 'Date'], keep='last')
    return d.sort_values(['ticker', 'Date']).reset_index(drop=True)


def market_index(d: pd.DataFrame) -> pd.Series:
    """Equally weighted universe, daily rebalanced; bad bars clipped at +-50 %."""
    p = d.pivot(index='Date', columns='ticker', values='close').sort_index()
    r = p.pct_change(fill_method=None).clip(-0.5, 0.5).mean(axis=1).fillna(0.0)
    return (1.0 + r).cumprod()


def new_events(mask: pd.Series, ticker: pd.Series) -> np.ndarray:
    """Row positions where a run of True starts, per ticker."""
    prev = mask.groupby(ticker).shift(1, fill_value=False)
    return np.flatnonzero((mask & ~prev).to_numpy())


class _Frame:
    """Arrays the event functions share."""

    def __init__(self, d: pd.DataFrame):
        self.d = d
        self.close = d['close'].to_numpy(dtype=float)
        self.mkt = d['Date'].map(market_index(d)).to_numpy(dtype=float)
        self.pos = d.groupby('ticker').cumcount().to_numpy()
        self.size = d.groupby('ticker')['Date'].transform('size').to_numpy()
        self.dates = d['Date'].to_numpy()
        self.year = d['Date'].dt.year.to_numpy()

    def excess(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Return from row a to row b minus the market over the same days."""
        return (self.close[b] / self.close[a] - 1) - (self.mkt[b] / self.mkt[a] - 1)


def _t_value(x: np.ndarray) -> float:
    if len(x) < 2:
        return np.nan
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else np.nan


def event_stats(f: _Frame, events: np.ndarray, delay: int = 0) -> dict:
    """Excess vs the market after *events* (entry *delay* bars later), per horizon."""
    entry = events + delay
    entry = entry[f.pos[events] + delay < f.size[events]]
    out = {'events': int(len(entry)), 'horizons': {}}
    for h in HORIZONS:
        ok = entry[f.pos[entry] + h < f.size[entry]]
        x = f.excess(ok, ok + h)
        good = np.isfinite(x)
        x, yr = x[good], f.year[ok][good]
        per_year = pd.Series(x, index=yr).groupby(level=0).agg(['mean', 'size'])
        counted = per_year[per_year['size'] >= MIN_YEAR_EVENTS]
        out['horizons'][h] = {
            'n': int(len(x)),
            'mean': float(x.mean() * 100) if len(x) else np.nan,
            't': _t_value(x),
            'hit': float((x > 0).mean() * 100) if len(x) else np.nan,
            'years_pos': int((counted['mean'] > 0).sum()),
            'years': int(len(counted)),
            'per_year': (per_year['mean'] * 100).round(2).to_dict(),
        }
    return out


def _first_exit(f: _Frame, entries: np.ndarray, sell_rows: np.ndarray, min_hold_days: int) -> np.ndarray:
    """Row of the first sell signal after each entry (same ticker), -1 if none."""
    ends = entries - f.pos[entries] + f.size[entries]
    out = np.full(len(entries), -1)
    min_hold = np.timedelta64(int(min_hold_days or 0), 'D')
    for n, (e, end) in enumerate(zip(entries, ends)):
        a = np.searchsorted(sell_rows, e + 1)
        while a < len(sell_rows) and sell_rows[a] < end:
            j = sell_rows[a]
            if f.dates[j] - f.dates[e] >= min_hold:
                out[n] = j
                break
            a += 1
    return out


def _time_exit(f: _Frame, entries: np.ndarray, days: int) -> np.ndarray:
    """First row at least *days* calendar days after each entry (same ticker), -1 if none."""
    out = np.full(len(entries), -1)
    if not days:
        return out
    step = np.timedelta64(int(days), 'D')
    ends = entries - f.pos[entries] + f.size[entries]
    for n, (e, end) in enumerate(zip(entries, ends)):
        k = e + int(np.searchsorted(f.dates[e:end], f.dates[e] + step))
        if k < end:
            out[n] = k
    return out


def _trades(f: _Frame, entries: np.ndarray, sell_rows: np.ndarray, min_hold_days: int,
            max_hold_days: int = 0) -> np.ndarray:
    """Excess per trade: entry to the first sell signal or time exit (open: to the last bar)."""
    ex = _first_exit(f, entries, sell_rows, min_hold_days)
    tx = _time_exit(f, entries, max_hold_days)
    ex = np.where((tx >= 0) & ((ex < 0) | (tx < ex)), tx, ex)
    last = entries - f.pos[entries] + f.size[entries] - 1
    j = np.where(ex < 0, last, ex)
    keep = j > entries
    x = f.excess(entries[keep], j[keep])
    return x[np.isfinite(x)]


def exit_control(f: _Frame, buy_events: np.ndarray, sell_rows: np.ndarray,
                 delay: int = 0, min_hold_days: int = 0, max_hold_days: int = 0) -> dict:
    """Formula entries vs random entries in the same tickers, both closed by the sell rule."""
    entries = buy_events + delay
    entries = entries[f.pos[buy_events] + delay < f.size[buy_events]]
    own = _trades(f, entries, sell_rows, min_hold_days, max_hold_days)
    starts = entries - f.pos[entries]
    rand = []
    for seed in RANDOM_SEEDS:
        rng = np.random.default_rng(seed)
        r = starts + (rng.random(len(entries)) * (f.size[entries] - 1)).astype(int)
        rand.append(_trades(f, r, sell_rows, min_hold_days, max_hold_days))
    rand = np.concatenate(rand) if rand else np.array([])
    diff = own.mean() - rand.mean() if len(own) and len(rand) else np.nan
    se = (np.sqrt(own.var(ddof=1) / len(own) + rand.var(ddof=1) / len(rand))
          if len(own) > 1 and len(rand) > 1 else np.nan)
    return {'trades': int(len(own)),
            'own_mean': float(own.mean() * 100) if len(own) else np.nan,
            'own_hit': float((own > 0).mean() * 100) if len(own) else np.nan,
            'rand_mean': float(rand.mean() * 100) if len(rand) else np.nan,
            'rand_hit': float((rand > 0).mean() * 100) if len(rand) else np.nan,
            'diff': float(diff * 100) if pd.notna(diff) else np.nan,
            't': float(diff / se) if se and pd.notna(se) and se > 0 else np.nan}


def concentration(trades: pd.DataFrame) -> dict:
    """Share of the net gain from the best 3 and 5 trades (open ones as marked)."""
    if trades is None or 'gain' not in getattr(trades, 'columns', ()):
        g = pd.Series(dtype=float)
    else:
        g = pd.to_numeric(trades['gain'], errors='coerce').dropna()
    total = float(g.sum()) if len(g) else 0.0
    best = g.sort_values(ascending=False)
    if total <= 0 or not len(g):
        return {'trades': int(len(g)), 'net': total, 'top3': np.nan, 'top5': np.nan}
    return {'trades': int(len(g)), 'net': total,
            'top3': float(best.head(3).sum() / total * 100),
            'top5': float(best.head(5).sum() / total * 100)}


# ------------------------------------------------------------------ verdicts

def verdict_buy(h: dict) -> str:
    if not h or h['n'] < MIN_EVENTS or pd.isna(h['t']):
        return GREY
    if h['mean'] > 0 and h['t'] >= 2 and h['years'] and h['years_pos'] / h['years'] >= 2 / 3:
        return GREEN
    if h['mean'] < 0 and h['t'] <= -2:
        return RED
    return YELLOW


def verdict_sell(h: dict) -> str:
    """After a sell signal the stock should do worse than the market."""
    if not h or h['n'] < MIN_EVENTS or pd.isna(h['t']):
        return GREY
    if h['mean'] < 0 and h['t'] <= -2:
        return GREEN
    if h['mean'] > 0 and h['t'] >= 2:
        return RED
    return YELLOW


def verdict_control(c: dict) -> str:
    if c['trades'] < MIN_EVENTS or pd.isna(c['t']):
        return GREY
    if c['diff'] > 0 and c['t'] >= 2:
        return GREEN
    if c['diff'] < 0 and c['t'] <= -2:
        return RED
    return YELLOW


def verdict_concentration(c: dict) -> str:
    if pd.isna(c['top3']):
        return RED if c['trades'] else GREY
    if c['top3'] > 50:
        return RED
    if c['top3'] > 30:
        return YELLOW
    return GREEN


def verdict_markets(rows: list[dict]) -> str:
    rows = [r for r in rows if pd.notna(r.get('strategy')) and pd.notna(r.get('universe'))]
    if len(rows) < MIN_MARKETS:
        return GREY
    share = sum(r['strategy'] > r['universe'] for r in rows) / len(rows)
    return GREEN if share >= 2 / 3 else RED if share <= 1 / 3 else YELLOW


def market_row(sig: pd.DataFrame, trades: pd.DataFrame, invest: float) -> dict:
    """Strategy vs equally weighted universe on another market's run (total % and per year)."""
    from tradinglib import backtest_eval as be

    panel = be.close_panel(sig)
    eq, _inv = be.mtm_equity(trades, panel, invest)
    bench = be.equal_weight(panel, invest)
    s, b = be.stats(eq), be.stats(bench)
    better = years = 0
    for y in sorted(set(eq.index.year)):
        e, u = eq[eq.index.year == y], bench[bench.index.year == y]
        if len(e) > 5 and len(u) > 5:
            years += 1
            better += (e.iloc[-1] / e.iloc[0]) > (u.iloc[-1] / u.iloc[0])
    return {'strategy': s['total'], 'universe': b['total'], 'max_dd': s['max_dd'],
            'years_better': int(better), 'years': int(years),
            'trades': int(len(trades)) if trades is not None else 0}


def run(df: pd.DataFrame, buy: str, sell: str, *, window: int = 1, delay: int = 0,
        min_hold_days: int = 0, max_hold_days: int = 0,
        trades: pd.DataFrame | None = None) -> dict:
    """All formula-level checks on the run's frame. Raises if a formula cannot be evaluated.

    *max_hold_days* is the strategy's time exit (0 = none); the control closes
    trades at the sell signal or after that many calendar days, whichever comes first.
    """
    from tradinglib import tools

    d = prepare(df, buy, sell)
    f = _Frame(d)
    buy_mask = tools.compute_signal_mask(d, buy, window=window).fillna(False).astype(bool)
    sell_mask = tools.compute_signal_mask(d, sell, window=window).fillna(False).astype(bool)
    buy_ev = new_events(buy_mask, d['ticker'])
    sell_ev = new_events(sell_mask, d['ticker'])
    out = {'buy': event_stats(f, buy_ev, delay),
           'sell': event_stats(f, sell_ev, 0),
           'control': exit_control(f, buy_ev, np.flatnonzero(sell_mask.to_numpy()),
                                   delay, min_hold_days, max_hold_days),
           'concentration': concentration(trades),
           'tickers': int(d['ticker'].nunique())}
    out['verdicts'] = {'buy': verdict_buy(out['buy']['horizons'].get(63)),
                       'sell': verdict_sell(out['sell']['horizons'].get(63)),
                       'control': verdict_control(out['control']),
                       'concentration': verdict_concentration(out['concentration'])}
    return out


# ------------------------------------------------------------------ UI

_LIGHT = {GREEN: '🟢', YELLOW: '🟡', RED: '🔴', GREY: '⚪'}


def _fmt(x, spec='+.2f'):
    return '–' if x is None or pd.isna(x) else format(x, spec)


def _event_line(st, title_key, res, verdict, verdict_prefix):
    from tradinglib.i18n import t as _t
    h21, h63 = res['horizons'].get(21, {}), res['horizons'].get(63, {})
    st.markdown(f"{_LIGHT[verdict]} **{_t(title_key)}** — " + _t(
        'sfc.event_line', n=res['events'],
        m21=_fmt(h21.get('mean')), t21=_fmt(h21.get('t'), '.1f'),
        m63=_fmt(h63.get('mean')), t63=_fmt(h63.get('t'), '.1f'),
        yp=h63.get('years_pos', 0), y=h63.get('years', 0)))
    st.caption(_t(f'{verdict_prefix}.{verdict}'))


def render(container, signature, compute, *, key='sfc', market_options=(),
           market_defaults=(), market_fn=None):
    """Expander with the check. *compute()* returns run(...)'s dict; *market_fn(index)*
    returns market_row(...)'s dict for another index (or None without data).

    Both only run on a button: they cost seconds (minutes for large indices),
    and the page reruns on every widget change. Results are kept per
    *signature* (formula + settings), so a changed formula never shows an old result.
    """
    import streamlit as st

    from tradinglib.i18n import t as _t

    res_key, mkt_key = f'{key}_res', f'{key}_mkt'
    with container.expander(_t('sfc.title'), expanded=False):
        st.caption(_t('sfc.intro'))
        if st.button(_t('sfc.run_btn'), key=f'{key}_run'):
            with st.spinner(_t('sfc.running')):
                try:
                    st.session_state[res_key] = (signature, compute())
                except Exception as e:  # formula the evaluator cannot handle
                    st.session_state[res_key] = (signature, {'error': str(e)})
        stored = st.session_state.get(res_key)
        res = stored[1] if stored and stored[0] == signature else None
        if res and 'error' in res:
            st.warning(_t('sfc.failed', error=res['error']))
        elif res:
            v = res['verdicts']
            _event_line(st, 'sfc.buy_title', res['buy'], v['buy'], 'sfc.buy_v')
            _event_line(st, 'sfc.sell_title', res['sell'], v['sell'], 'sfc.sell_v')
            c = res['control']
            st.markdown(f"{_LIGHT[v['control']]} **{_t('sfc.control_title')}** — " + _t(
                'sfc.control_line', n=c['trades'], own=_fmt(c['own_mean']),
                own_hit=_fmt(c['own_hit'], '.0f'), rand=_fmt(c['rand_mean']),
                rand_hit=_fmt(c['rand_hit'], '.0f'), diff=_fmt(c['diff']), t=_fmt(c['t'], '.1f')))
            st.caption(_t(f"sfc.control_v.{v['control']}"))
            k = res['concentration']
            st.markdown(f"{_LIGHT[v['concentration']]} **{_t('sfc.conc_title')}** — " + (
                _t('sfc.conc_line', top3=_fmt(k['top3'], '.0f'), top5=_fmt(k['top5'], '.0f'),
                   n=k['trades']) if pd.notna(k['top3']) else _t('sfc.conc_none')))
            st.caption(_t(f"sfc.conc_v.{v['concentration']}"))
            per_year = pd.DataFrame({
                _t('sfc.col_buy'): res['buy']['horizons'][63]['per_year'],
                _t('sfc.col_sell'): res['sell']['horizons'][63]['per_year']})
            if not per_year.empty:
                per_year.index.name = _t('sfc.col_year')
                st.markdown(f"**{_t('sfc.per_year')}**")
                st.dataframe(per_year.T, use_container_width=True)

        if market_fn is not None and market_options:
            st.markdown(f"**{_t('sfc.markets_title')}**")
            st.caption(_t('sfc.markets_help'))
            pick = st.multiselect(_t('sfc.markets_pick'), options=list(market_options),
                                  default=[m for m in market_defaults if m in market_options],
                                  key=f'{key}_mkt_pick')
            if st.button(_t('sfc.markets_btn'), key=f'{key}_mkt_run', disabled=not pick):
                rows, bar = [], st.progress(0.0)
                for i, m in enumerate(pick):
                    bar.progress(i / len(pick), text=_t('sfc.markets_running', index=m,
                                                         i=i + 1, n=len(pick)))
                    try:
                        r = market_fn(m)
                    except Exception as e:
                        r = {'error': str(e)}
                    rows.append({'index': m, **(r or {'error': _t('sfc.markets_no_data')})})
                bar.empty()
                st.session_state[mkt_key] = (signature, rows)
            stored = st.session_state.get(mkt_key)
            rows = stored[1] if stored and stored[0] == signature else None
            if rows:
                ok = [r for r in rows if 'error' not in r]
                if ok:
                    vm = verdict_markets(ok)
                    st.markdown(f"{_LIGHT[vm]} " + _t(
                        'sfc.markets_line', better=sum(r['strategy'] > r['universe'] for r in ok),
                        n=len(ok)))
                    table = pd.DataFrame([{
                        _t('sfc.col_index'): r['index'],
                        _t('sfc.col_strategy'): round(r['strategy'], 1),
                        _t('sfc.col_universe'): round(r['universe'], 1),
                        _t('sfc.col_years_better'): f"{r['years_better']}/{r['years']}",
                        _t('sfc.col_maxdd'): round(r['max_dd'], 1),
                        _t('sfc.col_trades'): r['trades']} for r in ok])
                    st.dataframe(table, use_container_width=True, hide_index=True)
                    st.caption(_t(f'sfc.markets_v.{vm}'))
                for r in rows:
                    if 'error' in r:
                        st.caption(_t('sfc.markets_error', index=r['index'], error=r['error']))
        st.caption(_t('sfc.rules'))
