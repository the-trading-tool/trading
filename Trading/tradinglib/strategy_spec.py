"""A strategy as data: the Strategy Finder's settings as six building blocks.

Until now a strategy existed only as the state of the Strategy Finder sidebar
(plus, once copied, as a Python literal in ``multi_transactions``). Here it is a
plain dict that can be saved, loaded, described and compared:

* **universe** -- index, filters, years
* **entry**    -- buy formula, *always* (every stock is a candidate) or *random*
                  (each stock-day with probability p)
* **select**   -- which candidates fill free slots: a column (high or low first),
                  a row-wise expression, or *random*
* **exit**     -- sell formula, after N calendar days, formula or N days, never
* **size**     -- slots, capital, sizing cap, fractional shares
* **costs**    -- fee, stops, minimum hold, cooldown

plus a description (name, idea, status) and the key figures of the last run.

"Always" and "random" make baselines ordinary strategies: random entries with the
strategy's signal density and holding period are the fair yardstick a formula
has to beat (see ``random_entry_signals``).

The classic Strategy Finder is the special case entry=formula, select=column,
exit=formula; ``is_classic`` tells, and only such a spec converts to a
``multi_transactions`` entry. No Streamlit here except through ``describe``'s
i18n lookup.
"""
from __future__ import annotations

import ast
import copy
import datetime as dt
import json
import re

import numpy as np
import pandas as pd

FX_MODES = ('current', 'historical')
ENTRY_TYPES = ('formula', 'always', 'random')
SELECT_TYPES = ('column', 'column_asc', 'expr', 'random')
EXIT_TYPES = ('formula', 'formula_days', 'days', 'never')
STATUSES = ('idea', 'paper', 'live')

ALWAYS = '(close > 0)'          # every bar with a price
NEVER = '(close < 0)'           # no bar
RANDOM_COL = '_entry_rand'      # 1 on randomly drawn entry bars
SELECT_COL = '_select'          # sort key for expr/random selection

STORE_KEY = 'sf_strategies'     # per-user config key: {name: spec}

DEFAULT = {
    'name': '', 'idea': '', 'status': 'idea',
    # 'indices' > 1 = a bundle: the members compete for the same slots. 'index' is
    # the first of them (labels, the index benchmark, single-index consumers).
    'universe': {'index': '^GDAXI', 'indices': [], 'exchange': 'ANY', 'sector': 'ANY',
                 'currency': 'ANY', 'cap_size': 'ANY', 'price_range': 'ANY', 'years': [],
                 'require_isin': False,
                 # conversion into the system currency: today's rate or each day's own
                 'fx': 'current',
                 # one ticker instead of the filtered universe ('' = off); the filters
                 # above are then ignored (they only choose which tickers take part)
                 'ticker': ''},
    # 'windows': buy periods within each year, [['MM-DD', 'MM-DD'], ...]; empty = all year.
    'entry': {'type': 'formula', 'buy': '', 'p_pct': 2.0, 'seed': 1, 'window': 1, 'delay': 1,
              'windows': []},
    'select': {'type': 'column', 'column': 'sortino', 'expr': '', 'seed': 1},
    'exit': {'type': 'formula', 'sell': '', 'days': 21},
    'size': {'num_assets': 5, 'invest': 100000, 'cap': 'none', 'factor_max': 2.0,
             'fractional': False},
    # 'take_profit': sell at buy price + x % (0 = off), the mirror of 'stop_loss'.
    'costs': {'fee_pct': 0.3, 'trailing_stop': 0.0, 'stop_loss': 0.0, 'take_profit': 0.0,
              'min_hold_days': 0, 'cooldown_days': 0},
    'report': {},
    'saved': '',
}

_NUM = {('entry', 'p_pct'): float, ('entry', 'seed'): int, ('entry', 'window'): int,
        ('entry', 'delay'): int, ('select', 'seed'): int, ('exit', 'days'): int,
        ('size', 'num_assets'): int, ('size', 'invest'): float, ('size', 'factor_max'): float,
        ('costs', 'fee_pct'): float, ('costs', 'trailing_stop'): float,
        ('costs', 'stop_loss'): float, ('costs', 'take_profit'): float,
        ('costs', 'min_hold_days'): int,
        ('costs', 'cooldown_days'): int}
_ENUM = {('entry', 'type'): ENTRY_TYPES, ('select', 'type'): SELECT_TYPES,
         ('exit', 'type'): EXIT_TYPES, ('universe', 'fx'): FX_MODES}


def normalize(spec: dict | None) -> dict:
    """Spec with every field present and of the right type; unknown values -> default."""
    out = copy.deepcopy(DEFAULT)
    spec = spec or {}
    for key, val in spec.items():
        if isinstance(out.get(key), dict) and isinstance(val, dict):
            out[key].update(val)
        elif key in out:
            out[key] = val
    for (block, field), cast in _NUM.items():
        try:
            out[block][field] = cast(float(out[block][field]))
        except (TypeError, ValueError):
            out[block][field] = DEFAULT[block][field]
    for (block, field), allowed in _ENUM.items():
        if out[block][field] not in allowed:
            out[block][field] = DEFAULT[block][field]
    if out['status'] not in STATUSES:
        out['status'] = 'idea'
    out['entry']['window'] = max(out['entry']['window'], 1)
    out['entry']['delay'] = max(out['entry']['delay'], 0)
    out['entry']['p_pct'] = min(max(out['entry']['p_pct'], 0.0), 100.0)
    out['entry']['windows'] = normalize_windows(out['entry']['windows'])
    out['exit']['days'] = max(out['exit']['days'], 1)
    out['costs']['take_profit'] = max(out['costs']['take_profit'], 0.0)
    out['size']['num_assets'] = max(out['size']['num_assets'], 1)
    for b in ('require_isin',):
        out['universe'][b] = bool(out['universe'][b])
    out['size']['fractional'] = bool(out['size']['fractional'])
    out['universe']['years'] = [str(y) for y in (out['universe']['years'] or [])]
    idx = [str(i) for i in dict.fromkeys(out['universe']['indices'] or []) if i]
    out['universe']['indices'] = idx or [str(out['universe']['index'])]
    out['universe']['index'] = out['universe']['indices'][0]
    for f in ('name', 'idea'):
        out[f] = str(out[f] or '').strip()
    out['universe']['ticker'] = str(out['universe']['ticker'] or '').strip()
    return out


# ------------------------------------------------------------------ buy windows

def _parse_day(value) -> tuple[int, int] | None:
    """(month, day) from 'MM-DD' or 'DD.MM.' (Feb 29 allowed), None if invalid."""
    text = str(value or '').strip().rstrip('.')
    try:
        if '.' in text:
            d, m = (int(p) for p in text.split('.')[:2])
        else:
            m, d = (int(p) for p in text.split('-')[-2:])
        dt.date(2000, m, d)     # leap year: 29 Feb is a valid day
    except (TypeError, ValueError):
        return None
    return m, d


def normalize_windows(windows) -> list[list[str]]:
    """Buy windows as [['MM-DD', 'MM-DD'], ...]; invalid pairs are dropped.

    A window whose end lies before its start runs over the turn of the year
    (['11-15', '02-10'] = 15 Nov to 10 Feb).
    """
    out = []
    for w in windows or []:
        if not isinstance(w, (list, tuple)) or len(w) != 2:
            continue
        a, b = _parse_day(w[0]), _parse_day(w[1])
        if a and b:
            pair = [f'{a[0]:02d}-{a[1]:02d}', f'{b[0]:02d}-{b[1]:02d}']
            if pair not in out:
                out.append(pair)
    return out


def in_windows(dates, windows) -> np.ndarray:
    """Boolean array: which *dates* fall into one of the buy *windows* (bounds included).

    No windows = every date. Unparseable dates are outside as soon as windows are set.
    """
    d = pd.to_datetime(pd.Series(dates), errors='coerce')
    wins = normalize_windows(windows)
    if not wins:
        return np.ones(len(d), dtype=bool)
    md = (d.dt.month * 100 + d.dt.day).to_numpy(dtype=float)     # NaN for NaT
    mask = np.zeros(len(d), dtype=bool)
    for start, end in wins:
        s = int(start.replace('-', ''))
        e = int(end.replace('-', ''))
        mask |= ((md >= s) & (md <= e)) if s <= e else ((md >= s) | (md <= e))
    return mask


def windows_label(windows) -> str:
    """'15.03.–01.08., 01.11.–20.12.' (empty string without windows)."""
    def _de(mmdd):
        m, d = mmdd.split('-')
        return f'{d}.{m}.'
    return ', '.join(f'{_de(a)}–{_de(b)}' for a, b in normalize_windows(windows))


def is_classic(spec: dict) -> bool:
    """Entry formula, best-of-column selection, exit formula: the classic Finder."""
    s = normalize(spec)
    return (s['entry']['type'] == 'formula' and s['select']['type'] == 'column'
            and s['exit']['type'] == 'formula')


def is_bundle(spec: dict) -> bool:
    return len(normalize(spec)['universe']['indices']) > 1


def ms_exportable(spec: dict) -> bool:
    """Multi Strategies runs one index per pot and only the classic blocks."""
    return is_classic(spec) and not is_bundle(spec) and not normalize(spec)['universe']['ticker']


def bundle_label(spec: dict) -> str:
    return ' + '.join(normalize(spec)['universe']['indices'])


BUNDLE_SEP = '+'


def index_members(key) -> list[str]:
    """Indices of a Multi index key: '^GDAXI + ^MDAXI' -> ['^GDAXI', '^MDAXI'], '^SPX' -> ['^SPX'].

    A key with several members is a bundle: one pot whose capital and slots the
    members share (Multi Strategies, agent and signals tab alike).
    """
    parts = [p.strip() for p in str(key or '').split(BUNDLE_SEP)]
    return list(dict.fromkeys(p for p in parts if p)) or [str(key or '')]


def is_bundle_key(key) -> bool:
    return len(index_members(key)) > 1


def merge_bundle(frames: list[tuple[str, pd.DataFrame]]) -> tuple[pd.DataFrame, int]:
    """Concatenate the members' frames; a stock in several indices counts once.

    The first index in the list keeps the stock (its stockIndex stays on the rows),
    so its signals are not doubled. Returns (frame, number of stocks dropped as
    duplicates).
    """
    kept, seen, dropped = [], set(), 0
    for _idx, df in frames:
        if df is None or df.empty:
            continue
        tick = set(df['ticker'].unique())
        dup = tick & seen
        dropped += len(dup)
        kept.append(df[~df['ticker'].isin(dup)] if dup else df)
        seen |= tick
    if not kept:
        return pd.DataFrame(), dropped
    return pd.concat(kept, ignore_index=True), dropped


def _currency_by_ticker(df: pd.DataFrame) -> pd.Series:
    """Trading currency per ticker; anything that is not a currency code (0, None) dropped."""
    if df is None or df.empty or 'currency' not in df.columns:
        return pd.Series(dtype=str)
    cur = df.drop_duplicates('ticker').set_index('ticker')['currency'].dropna().astype(str).str.strip()
    return cur[cur.str.fullmatch(r'[A-Za-z]{3}')]


def trading_currencies(df: pd.DataFrame) -> dict:
    """Number of stocks per trading currency in *df* (missing or invalid currency left out)."""
    return _currency_by_ticker(df).value_counts().to_dict()


PRICE_COLUMNS = ('close', 'Open', 'High', 'Low')


def to_system_currency(df: pd.DataFrame, system_currency: str, rate_fn,
                       series_fn=None) -> pd.DataFrame:
    """Copy of a signalled frame with prices in the system currency, each row by its own currency.

    The conversion Multi Strategies has always applied (one place, so the
    Strategy Finder computes the same numbers): ``rate_fn(currency)`` is the
    rate currency -> system currency (TickerTools.get_exchange_rate, today's
    rate), prices are divided by it; a currency that fails or is unknown counts
    as 1. Run it after the signals: the formulas compare a stock's columns with
    each other, all in its listing currency. Without a 'currency' column the
    frame comes back unchanged.

    Historical rates: with *series_fn* (currency -> daily Series "system units
    per 1 currency", fx_history.per_unit) every row is converted at the rate of
    its own day (last known rate on days without one, the first known rate
    before the series starts). A currency without a series falls back to
    today's rate; such currencies are listed in ``out.attrs['fx_fallback']``.
    """
    if df is None or df.empty or 'currency' not in df.columns:
        return df
    sc = str(system_currency or '')
    if series_fn is not None:
        return _to_system_currency_hist(df, sc, rate_fn, series_fn)
    cache: dict = {}

    def _fx(cur):
        cur = str(cur)
        if cur not in cache:
            r = 1.0
            if cur and cur not in ('0', 'nan', 'None', '', sc):
                try:
                    r = float(rate_fn(cur)) or 1.0
                except Exception:
                    r = 1.0
            cache[cur] = r
        return cache[cur]

    out = df.copy()
    rate = out['currency'].map(_fx)
    for col in PRICE_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors='coerce') / rate
    out['currency'] = sc
    return out


def _to_system_currency_hist(df: pd.DataFrame, sc: str, rate_fn, series_fn) -> pd.DataFrame:
    """to_system_currency at each row's own date (see there)."""
    out = df.copy()
    cur = out['currency'].astype(str)
    days = pd.to_datetime(out['Date'], errors='coerce').dt.normalize()
    factor = pd.Series(1.0, index=out.index)
    fallback = []
    for c in cur.unique():
        rows = cur == c
        if not c or c in ('0', 'nan', 'None', '', sc):
            continue
        try:
            s = series_fn(c)
        except Exception:
            s = None
        if s is not None and len(s):
            s = s[~s.index.duplicated(keep='last')].sort_index()
            d = days[rows]
            full = s.reindex(s.index.union(d.dropna().unique())).ffill().bfill()
            factor[rows] = d.map(full).astype(float).fillna(float(s.iloc[-1])).to_numpy()
            continue
        fallback.append(c)
        try:
            r = float(rate_fn(c)) or 1.0
        except Exception:
            r = 1.0
        factor[rows] = 1.0 / r
    for col in PRICE_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors='coerce') * factor
    out['currency'] = sc
    out.attrs['fx_fallback'] = sorted(fallback)
    return out


# ------------------------------------------------------------------ engine glue

def buy_formula(spec: dict, buy: str | None = None) -> str:
    """Buy condition for the signal generator (*buy* overrides the spec's formula)."""
    s = normalize(spec)
    kind = s['entry']['type']
    if kind == 'always':
        return ALWAYS
    if kind == 'random':
        return f'({RANDOM_COL} > 0)'
    return s['entry']['buy'] if buy is None else buy


def sell_formula(spec: dict, sell: str | None = None) -> str:
    """Sell condition; time-only and never-exits get a condition that is never true."""
    s = normalize(spec)
    if s['exit']['type'] in ('days', 'never'):
        return NEVER
    return s['exit']['sell'] if sell is None else sell


def max_hold_days(spec: dict) -> int:
    s = normalize(spec)
    return s['exit']['days'] if s['exit']['type'] in ('formula_days', 'days') else 0


def _ticker_date_order(df: pd.DataFrame) -> np.ndarray:
    """Row positions of *df* in (ticker, Date) order -- the draw order for random numbers.

    Independent of how the frame happens to be sorted, so the same seed gives the
    same draws on every page run.
    """
    key = pd.DataFrame({'t': df['ticker'].astype(str).to_numpy(),
                        'd': pd.to_datetime(df['Date'], errors='coerce').to_numpy()})
    return key.sort_values(['t', 'd'], kind='mergesort').index.to_numpy()


def _draws(df: pd.DataFrame, seed: int) -> np.ndarray:
    """Uniform random numbers aligned to *df*'s rows, drawn in (ticker, Date) order."""
    order = _ticker_date_order(df)
    vals = np.empty(len(df))
    vals[order] = np.random.default_rng(int(seed)).random(len(df))
    return vals


def add_entry_column(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """Add the random-entry column when the entry block is 'random' (else unchanged)."""
    s = normalize(spec)
    if s['entry']['type'] != 'random' or df.empty:
        return df
    out = df.copy()
    out[RANDOM_COL] = (_draws(out, s['entry']['seed']) < s['entry']['p_pct'] / 100.0).astype(int)
    return out


def order_signals(sig: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """Sort a signalled frame for the portfolio run: by Date, then the selection block.

    The simulator takes same-day signals in row order, so this order *is* the
    selection. 'column' reproduces the classic sort (Date asc, column desc).
    """
    s = normalize(spec)
    kind, col = s['select']['type'], s['select']['column']
    if kind in ('column', 'column_asc') and col in sig.columns:
        return sig.sort_values(['Date', col], ascending=[True, kind == 'column_asc'])
    out = sig.copy()
    if kind == 'expr' and s['select']['expr'].strip():
        try:
            val = out.eval(s['select']['expr'], engine='python')
            out[SELECT_COL] = pd.to_numeric(val, errors='coerce')
        except Exception as e:
            raise ValueError(f"selection expression: {e}") from e
    elif kind == 'random':
        out[SELECT_COL] = _draws(out, s['select']['seed'])
    else:
        return out.sort_values('Date')
    return out.sort_values(['Date', SELECT_COL], ascending=[True, False], na_position='last')


def random_entry_signals(sig: pd.DataFrame, p: float, seed: int, windows=None) -> pd.DataFrame:
    """Copy of *sig* whose buySell is 1 on random rows (rate *p*), never -1.

    For the matched-random baseline: same signal density as the strategy, exits by
    time only (the caller passes max_hold_days to the simulator). Selection among
    same-day signals is random too. With buy *windows* random buys fall only
    inside them, as the strategy's do (*p* is then the rate within the windows).
    """
    out = sig.copy()
    out['buySell'] = (_draws(out, seed) < p).astype(int)
    if normalize_windows(windows):
        out.loc[~in_windows(out['Date'].to_numpy(), windows), 'buySell'] = 0
    out[SELECT_COL] = _draws(out, seed + 100003)
    return out.sort_values(['Date', SELECT_COL], ascending=[True, False])


def median_hold_days(trades: pd.DataFrame, default: int = 21) -> int:
    """Median calendar days between buy and sell (open trades as marked)."""
    if trades is None or trades.empty:
        return default
    b = pd.to_datetime(trades.get('buyDate'), errors='coerce')
    e = pd.to_datetime(trades.get('sellDate'), errors='coerce')
    days = (e - b).dt.days.dropna()
    days = days[days > 0]
    return int(max(round(days.median()), 1)) if len(days) else default


# ------------------------------------------------------------------ multi strategies

def to_ms_entry(spec: dict) -> dict | None:
    """The spec as a written-out multi_transactions index entry, or None if it has none.

    Written out (formulas instead of a reference) only for one index and the
    classic blocks; a bundle or other blocks go to Multi as a reference.
    """
    if not ms_exportable(spec):
        return None
    return _ms_fields(spec)


def _ms_fields(spec: dict) -> dict:
    """The classic Multi fields of a spec (no check whether Multi could run it written out)."""
    from tradinglib import ms_export
    s = normalize(spec)
    return ms_export.entry(
        buy=s['entry']['buy'], sell=s['exit']['sell'], num_assets=s['size']['num_assets'],
        invest=s['size']['invest'], order_by=s['select']['column'],
        signal_window=s['entry']['window'], trailing_stop=s['costs']['trailing_stop'],
        stop_loss=s['costs']['stop_loss'], sizing_cap=s['size']['cap'],
        sizing_factor_max=(s['size']['factor_max']
                           if s['size']['cap'] in ('factor', 'factor_capped') else None),
        fractional=s['size']['fractional'], require_isin=s['universe']['require_isin'],
        min_hold_days=s['costs']['min_hold_days'], cooldown_days=s['costs']['cooldown_days'],
        currency=s['universe']['currency'], buy_windows=s['entry']['windows'],
        fx=s['universe']['fx'], take_profit=s['costs']['take_profit'])


def from_ms_entry(name: str, index: str, entry: dict, base: dict | None = None) -> dict:
    """A multi_transactions entry as a spec.

    Multi Strategies computes without fee and without buy delay; the spec says so
    (fee 0, delay 0) so a Finder run of it shows what Multi computes. Fields Multi
    takes from global settings when missing are taken from *base* (the current
    Finder settings) where given.
    """
    b = normalize(base)
    e = dict(entry or {})
    spec = copy.deepcopy(b)
    spec.update({'name': f'{name} / {index}', 'idea': '', 'status': 'live', 'report': {},
                 'saved': ''})
    spec['universe'].update({'index': index, 'indices': [index], 'exchange': 'ANY', 'sector': 'ANY',
                             'cap_size': 'ANY', 'price_range': 'ANY',
                             'currency': e.get('currency', 'ANY') or 'ANY'})
    spec['universe']['fx'] = e.get('fx') or 'current'
    if 'require_isin' in e:
        spec['universe']['require_isin'] = str(e['require_isin']).strip().lower() in (
            '1', 'true', 'yes', 'ja')
    spec['entry'].update({'type': 'formula', 'buy': e.get('buy', ''),
                          'window': e.get('signal_window', 1), 'delay': 0,
                          'windows': e.get('buy_windows') or []})
    spec['select'].update({'type': 'column', 'column': e.get('order_by', 'sortino')})
    spec['exit'].update({'type': 'formula', 'sell': e.get('sell', '')})
    spec['size'].update({k: e[src] for k, src in (('num_assets', 'num_assets'),
                                                  ('invest', 'invest'), ('cap', 'sizing_cap'),
                                                  ('factor_max', 'sizing_factor_max'),
                                                  ('fractional', 'fractional')) if src in e})
    spec['costs'].update({'fee_pct': 0.0})
    spec['costs'].update({k: e[k] for k in ('trailing_stop', 'stop_loss', 'take_profit',
                                            'min_hold_days', 'cooldown_days') if k in e})
    if 'take_profit' not in e:          # no global default: missing = off, not the base's
        spec['costs']['take_profit'] = 0.0
    return normalize(spec)


def parse_multi_transactions(value) -> dict:
    """The multi_transactions config value as a dict ({} if unreadable).

    The stored value can be a dict, a Python literal (the settings text area),
    JSON (the trading page's strategy editor writes json.dumps -- true/false/null
    are no Python literal), or either inside a string (double-encoded). Never eval.
    """
    v = value
    for _ in range(3):
        if isinstance(v, dict):
            return v
        if not isinstance(v, str) or not v.strip():
            return {}
        text = v.strip()
        try:
            v = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            try:
                v = json.loads(text)
            except ValueError:
                return {}
    return v if isinstance(v, dict) else {}


# ------------------------------------------------------------------ references

REF_KEY = 'strategy'    # index entry field naming a saved strategy

# Entry fields that override the referenced strategy, and where they go in the spec.
_ENTRY_FIELDS = {
    'buy': ('entry', 'buy'), 'sell': ('exit', 'sell'), 'signal_window': ('entry', 'window'),
    'buy_delay': ('entry', 'delay'), 'order_by': ('select', 'column'),
    'num_assets': ('size', 'num_assets'), 'invest': ('size', 'invest'),
    'sizing_cap': ('size', 'cap'), 'sizing_factor_max': ('size', 'factor_max'),
    'fractional': ('size', 'fractional'), 'fee_pct': ('costs', 'fee_pct'),
    'trailing_stop': ('costs', 'trailing_stop'), 'stop_loss': ('costs', 'stop_loss'),
    'take_profit': ('costs', 'take_profit'),
    'min_hold_days': ('costs', 'min_hold_days'), 'cooldown_days': ('costs', 'cooldown_days'),
    'require_isin': ('universe', 'require_isin'), 'currency': ('universe', 'currency'),
    'buy_windows': ('entry', 'windows'), 'fx': ('universe', 'fx'),
}


def _expand_entry(index: str, entry: dict, saved: dict, live: bool):
    """One index entry with its strategy reference resolved (see expand_references)."""
    name = entry.get(REF_KEY)
    if not name:
        return entry
    spec = saved.get(str(name))
    if spec is None:
        return None if live else {**entry, '_ref_error': 'missing'}
    s = normalize(spec)
    for key, (block, field) in _ENTRY_FIELDS.items():
        if key in entry:
            s[block][field] = entry[key]
    # The Multi entry's index key is the universe: one index, or a bundle
    # ('^GDAXI + ^MDAXI') whose members share one pot.
    s['universe']['indices'] = index_members(index)
    s = normalize(s)
    if live and not is_classic(s):
        return None
    out = _ms_fields(s) if is_classic(s) else None
    if out is None:     # non-classic: Multi's own fields, formulas as the blocks run them
        out = {'buy': buy_formula(s), 'sell': sell_formula(s),
               'num_assets': s['size']['num_assets'], 'invest': s['size']['invest'],
               'order_by': s['select']['column'], 'signal_window': s['entry']['window']}
        if s['entry']['windows']:
            out['buy_windows'] = s['entry']['windows']
    out[REF_KEY] = str(name)
    if not live:
        out['_spec'] = s
    return out


def expand_references(transactions: dict, saved: dict, live: bool = False) -> dict:
    """Resolve ``{'strategy': name, ...}`` index entries against saved strategies.

    Entries without a reference come back unchanged -- existing configurations
    compute exactly as before. A referenced entry gets the classic fields every
    reader understands (buy, sell, num_assets, invest, order_by, ...), filled from
    the saved strategy and overridden by the fields the entry sets itself
    (typically invest and num_assets).

    live=False (Multi Strategies backtest): the resolved spec rides along as
    ``_spec``, so Multi can run it with the Strategy Finder's engine; a missing
    strategy is marked ``_ref_error``.
    live=True (agent, signals tab, Pine export): only entries the live path can
    execute -- buy formula, column selection, sell formula. Others (random
    entry, time exit, missing reference) are left out, and so is a strategy
    with no index left.
    """
    out = {}
    for strat, idxs in (transactions or {}).items():
        if not isinstance(idxs, dict):
            out[strat] = idxs
            continue
        new = {}
        for idx, entry in idxs.items():
            if isinstance(entry, dict):
                e = _expand_entry(str(idx), entry, saved, live)
                if e is not None:
                    new[idx] = e
            else:
                new[idx] = entry
        if new or not live:
            out[strat] = new
    return out


def apply_strategy_defaults(transactions: dict) -> dict:
    """Strategy-level fields as defaults of its index entries, the way the agent reads them.

    The trading page's strategy editor stores shared settings (num_assets,
    invest, order_by, enabled) next to the index entries. The agent merges them
    as ``{**strategy_fields, **index_entry}``; readers that walk the index keys
    (Multi Strategies, dashboard) would take 'num_assets' for an index. Here
    the fields go into every index entry (the entry's own value wins) and leave
    the strategy level, so only index entries remain. A strategy with ``buy`` at
    the top (the old flat form, the strategy is its own index) stays as it is.
    'enabled' is the editor's switch, not a run setting, and is dropped.
    """
    out = {}
    for strat, v in (transactions or {}).items():
        if not isinstance(v, dict):
            out[strat] = v
            continue
        idx = {k: e for k, e in v.items() if isinstance(e, dict)}
        if not idx or 'buy' in v:
            out[strat] = v
            continue
        defaults = {k: e for k, e in v.items() if not isinstance(e, dict) and k != 'enabled'}
        out[strat] = {k: {**defaults, **e} for k, e in idx.items()}
    return out


def has_references(transactions: dict) -> bool:
    return any(isinstance(e, dict) and e.get(REF_KEY)
               for idxs in (transactions or {}).values() if isinstance(idxs, dict)
               for e in idxs.values())


def saved_from_value(value) -> dict:
    """{name: spec} from a raw ``sf_strategies`` config value (for readers without SystemConfig)."""
    raw = value
    for _ in range(3):
        if isinstance(raw, dict):
            break
        if not isinstance(raw, str) or not raw.strip():
            return {}
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): normalize(v) for k, v in raw.items() if isinstance(v, dict)}


def load_transactions(cfg, live: bool = False, default=None) -> dict:
    """multi_transactions of *cfg*'s user, parsed (no eval) and references resolved.

    *default* is used when nothing readable is stored (the readers' former
    fallback, SystemConfig.transactions).
    """
    tx = parse_multi_transactions(cfg.get_value('multi_transactions', None))
    if not tx and default is not None:
        tx = parse_multi_transactions(default)
    if not has_references(tx):
        return tx
    return expand_references(tx, load_all(cfg), live=live)


def ms_strategies(value) -> list[tuple[str, str, dict]]:
    """(strategy, index, entry) for every index entry in multi_transactions."""
    out = []
    for strat, idxs in parse_multi_transactions(value).items():
        if isinstance(idxs, dict):
            for idx, entry in idxs.items():
                if isinstance(entry, dict):
                    out.append((str(strat), str(idx), entry))
    return out


# ------------------------------------------------------------------ storage

def load_all(cfg) -> dict:
    """Saved specs of the config's user: {name: spec}."""
    raw = cfg.get_value(STORE_KEY, '{}')
    for _ in range(2):
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except ValueError:
                return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): normalize(v) for k, v in raw.items() if isinstance(v, dict)}


def save(cfg, spec: dict) -> dict:
    """Store *spec* under its name (required); returns the stored spec."""
    s = normalize(spec)
    if not s['name']:
        raise ValueError('name required')
    s['saved'] = dt.datetime.now().isoformat(timespec='minutes')
    all_ = load_all(cfg)
    all_[s['name']] = s
    cfg.set_value(STORE_KEY, json.dumps(all_, ensure_ascii=False))
    return s


def delete(cfg, name: str) -> bool:
    all_ = load_all(cfg)
    if name not in all_:
        return False
    del all_[name]
    cfg.set_value(STORE_KEY, json.dumps(all_, ensure_ascii=False))
    return True


def delete_many(cfg, names) -> list[str]:
    """Delete several saved strategies with one write; returns the names actually deleted."""
    all_ = load_all(cfg)
    gone = [n for n in dict.fromkeys(str(x) for x in names or []) if n in all_]
    if gone:
        for n in gone:
            del all_[n]
        cfg.set_value(STORE_KEY, json.dumps(all_, ensure_ascii=False))
    return gone


def referenced_names(transactions_value) -> set[str]:
    """Names of saved strategies that multi_transactions entries point at."""
    return {str(e[REF_KEY]) for _, _, e in ms_strategies(transactions_value) if e.get(REF_KEY)}


def _swap_refs(transactions: dict, old: str, new: str) -> tuple[dict, int]:
    """Copy of *transactions* with every reference to *old* pointing at *new*, and their count."""
    out, n = copy.deepcopy(transactions), 0
    for idxs in out.values():
        if not isinstance(idxs, dict):
            continue
        for e in idxs.values():
            if isinstance(e, dict) and e.get(REF_KEY) is not None and str(e[REF_KEY]) == old:
                e[REF_KEY] = new
                n += 1
    return out, n


def _quoted(text: str, q: str) -> str:
    """*text* as a string literal in quote *q* (valid Python and, for '"', JSON)."""
    return q + text.replace('\\', '\\\\').replace(q, '\\' + q) + q


def rename_references(value, old: str, new: str):
    """multi_transactions *value* with every reference to *old* pointing at *new*.

    Returns (value, count). A text value is edited in place so the user's
    layout and comments in the settings text area survive: only the quoted
    name after 'strategy' changes. The result is parsed back and must equal
    the original with the references swapped, otherwise ValueError -- the
    caller then writes nothing.
    """
    want, n = _swap_refs(parse_multi_transactions(value), old, new)
    if not n:
        return value, 0
    if isinstance(value, dict):
        return want, n
    text = str(value)
    for q in ("'", '"'):
        pat = re.compile(r'''(["']''' + REF_KEY + r'''["']\s*:\s*)''' + re.escape(_quoted(old, q)))
        text = pat.sub(lambda m: m.group(1) + _quoted(new, q), text)
    if parse_multi_transactions(text) != want:
        raise ValueError('multi_transactions references could not be renamed in place')
    return text, n


def rename(cfg, old: str, new: str) -> int:
    """Rename a saved strategy and point multi_transactions references at the new name.

    Returns the number of references changed. Everything is checked before the
    first write: *old* must exist (KeyError), *new* must be non-empty and free,
    and the references must be renamable (ValueError). The strategy keeps its
    place in the store. The run history (strategy_reports) is not touched here.
    """
    old, new = str(old), str(new or '').strip()
    all_ = load_all(cfg)
    if old not in all_:
        raise KeyError(old)
    if not new:
        raise ValueError('name required')
    if new == old:
        return 0
    if new in all_:
        raise ValueError(f'a strategy named {new!r} exists')
    raw = cfg.get_value('multi_transactions', None)
    tx_value, n = rename_references(raw, old, new) if raw else (raw, 0)
    renamed = {(new if k == old else k): ({**v, 'name': new} if k == old else v)
               for k, v in all_.items()}
    cfg.set_value(STORE_KEY, json.dumps(renamed, ensure_ascii=False))
    if n:
        cfg.set_value('multi_transactions', tx_value)
    return n


def signature(spec: dict) -> str:
    """Stable text of everything that changes a run (not name, idea, status, report)."""
    s = normalize(spec)
    run = {k: s[k] for k in ('universe', 'entry', 'select', 'exit', 'size', 'costs')}
    # Defaults added later leave the text as it was, so earlier runs keep matching.
    if not run['entry']['windows']:
        run['entry'] = {k: v for k, v in run['entry'].items() if k != 'windows'}
    if run['universe']['fx'] == 'current':
        run['universe'] = {k: v for k, v in run['universe'].items() if k != 'fx'}
    if not run['universe']['ticker']:
        run['universe'] = {k: v for k, v in run['universe'].items() if k != 'ticker'}
    if not run['costs']['take_profit']:
        run['costs'] = {k: v for k, v in run['costs'].items() if k != 'take_profit'}
    return json.dumps(run, sort_keys=True, ensure_ascii=False)


# ------------------------------------------------------------------ description

def describe(spec: dict) -> list[str]:
    """The spec in plain sentences (current UI language)."""
    from tradinglib.i18n import t as _t
    s = normalize(spec)
    u, e, sel, x, z, c = (s[k] for k in ('universe', 'entry', 'select', 'exit', 'size', 'costs'))
    filters = [f'{_t(f"sbs.f.{k}")} {u[k]}' for k in ('exchange', 'sector', 'currency',
                                                      'cap_size', 'price_range')
               if u[k] and u[k] != 'ANY']
    if u['require_isin']:
        filters.append(_t('sbs.f.isin'))
    if u['fx'] == 'historical':
        filters.append(_t('sbs.f.fx_hist'))
    years = ', '.join(sorted(u['years'])) if u['years'] else '–'
    if u['ticker']:
        fx = ('; ' + _t('sbs.f.fx_hist')) if u['fx'] == 'historical' else ''
        lines = [_t('sbs.d.universe_ticker', ticker=u['ticker'], years=years, filters=fx)]
    else:
        lines = [_t('sbs.d.universe', index=' + '.join(u['indices']), years=years,
                    filters=('; ' + ', '.join(filters)) if filters else '')]
    if e['type'] == 'formula':
        lines.append(_t('sbs.d.entry_formula', buy=' & '.join(
            ln.strip() for ln in (e['buy'] or '').splitlines() if ln.strip()) or '–',
            window=e['window'], delay=e['delay']))
    else:
        lines.append(_t(f"sbs.d.entry_{e['type']}", p=f"{e['p_pct']:g}", seed=e['seed'],
                        delay=e['delay']))
    if e['windows']:
        lines.append(_t('sbs.d.windows', windows=windows_label(e['windows'])))
    lines.append(_t(f"sbs.d.select_{sel['type']}", column=sel['column'],
                    expr=sel['expr'] or '–', seed=sel['seed']))
    lines.append(_t(f"sbs.d.exit_{x['type']}", sell=' & '.join(
        ln.strip() for ln in (x['sell'] or '').splitlines() if ln.strip()) or '–',
        days=x['days']))
    stops = []
    if c['stop_loss']:
        stops.append(_t('sbs.d.stop_loss', pct=f"{c['stop_loss']:g}"))
    if c['trailing_stop']:
        stops.append(_t('sbs.d.trailing', pct=f"{c['trailing_stop']:g}"))
    if c['take_profit']:
        stops.append(_t('sbs.d.take_profit', pct=f"{c['take_profit']:g}"))
    if stops:
        lines.append(', '.join(stops))
    lines.append(_t('sbs.d.size', n=z['num_assets'], invest=f"{z['invest']:,.0f}",
                    cap=_t(f"sf.sizing_cap_{z['cap']}"),
                    frac=_t('sbs.d.frac') if z['fractional'] else ''))
    lines.append(_t('sbs.d.costs', fee=f"{c['fee_pct']:g}", hold=c['min_hold_days'],
                    cool=c['cooldown_days']))
    return lines
