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

import numpy as np
import pandas as pd

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
                 'require_isin': False},
    'entry': {'type': 'formula', 'buy': '', 'p_pct': 2.0, 'seed': 1, 'window': 1, 'delay': 1},
    'select': {'type': 'column', 'column': 'sortino', 'expr': '', 'seed': 1},
    'exit': {'type': 'formula', 'sell': '', 'days': 21},
    'size': {'num_assets': 5, 'invest': 100000, 'cap': 'none', 'factor_max': 2.0,
             'fractional': False},
    'costs': {'fee_pct': 0.3, 'trailing_stop': 0.0, 'stop_loss': 0.0,
              'min_hold_days': 0, 'cooldown_days': 0},
    'report': {},
    'saved': '',
}

_NUM = {('entry', 'p_pct'): float, ('entry', 'seed'): int, ('entry', 'window'): int,
        ('entry', 'delay'): int, ('select', 'seed'): int, ('exit', 'days'): int,
        ('size', 'num_assets'): int, ('size', 'invest'): float, ('size', 'factor_max'): float,
        ('costs', 'fee_pct'): float, ('costs', 'trailing_stop'): float,
        ('costs', 'stop_loss'): float, ('costs', 'min_hold_days'): int,
        ('costs', 'cooldown_days'): int}
_ENUM = {('entry', 'type'): ENTRY_TYPES, ('select', 'type'): SELECT_TYPES,
         ('exit', 'type'): EXIT_TYPES}


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
    out['exit']['days'] = max(out['exit']['days'], 1)
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
    return out


def is_classic(spec: dict) -> bool:
    """Entry formula, best-of-column selection, exit formula: the classic Finder."""
    s = normalize(spec)
    return (s['entry']['type'] == 'formula' and s['select']['type'] == 'column'
            and s['exit']['type'] == 'formula')


def is_bundle(spec: dict) -> bool:
    return len(normalize(spec)['universe']['indices']) > 1


def ms_exportable(spec: dict) -> bool:
    """Multi Strategies runs one index per pot and only the classic blocks."""
    return is_classic(spec) and not is_bundle(spec)


def bundle_label(spec: dict) -> str:
    return ' + '.join(normalize(spec)['universe']['indices'])


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


MAX_FOREIGN_SHARE = 0.05


def bundle_currency_check(df: pd.DataFrame) -> tuple[bool, str, dict]:
    """(ok, main currency, {ticker: currency} of the stocks outside it).

    Not ok when a second currency holds more than MAX_FOREIGN_SHARE of the stocks:
    then the bundle mixes markets and every position size would be off. A few
    strays (an index member listed by its US ticker) are reported but allowed --
    the single-index run computes them the same way today.
    """
    cur = _currency_by_ticker(df)
    if cur.empty:
        return True, '', {}
    counts = cur.value_counts()
    main = str(counts.index[0])
    foreign = cur[cur != main]
    ok = bool(len(counts) == 1 or counts.iloc[1] <= MAX_FOREIGN_SHARE * len(cur))
    return ok, main, foreign.to_dict()


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


def random_entry_signals(sig: pd.DataFrame, p: float, seed: int) -> pd.DataFrame:
    """Copy of *sig* whose buySell is 1 on random rows (rate *p*), never -1.

    For the matched-random baseline: same signal density as the strategy, exits by
    time only (the caller passes max_hold_days to the simulator). Selection among
    same-day signals is random too.
    """
    out = sig.copy()
    out['buySell'] = (_draws(out, seed) < p).astype(int)
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
    """The spec as a multi_transactions index entry, or None if Multi cannot express it."""
    if not ms_exportable(spec):
        return None
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
        currency=s['universe']['currency'])


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
    if 'require_isin' in e:
        spec['universe']['require_isin'] = str(e['require_isin']).strip().lower() in (
            '1', 'true', 'yes', 'ja')
    spec['entry'].update({'type': 'formula', 'buy': e.get('buy', ''),
                          'window': e.get('signal_window', 1), 'delay': 0})
    spec['select'].update({'type': 'column', 'column': e.get('order_by', 'sortino')})
    spec['exit'].update({'type': 'formula', 'sell': e.get('sell', '')})
    spec['size'].update({k: e[src] for k, src in (('num_assets', 'num_assets'),
                                                  ('invest', 'invest'), ('cap', 'sizing_cap'),
                                                  ('factor_max', 'sizing_factor_max'),
                                                  ('fractional', 'fractional')) if src in e})
    spec['costs'].update({'fee_pct': 0.0})
    spec['costs'].update({k: e[k] for k in ('trailing_stop', 'stop_loss', 'min_hold_days',
                                            'cooldown_days') if k in e})
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
    'min_hold_days': ('costs', 'min_hold_days'), 'cooldown_days': ('costs', 'cooldown_days'),
    'require_isin': ('universe', 'require_isin'), 'currency': ('universe', 'currency'),
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
    # The Multi entry's index key is the universe (Multi keeps one pot per index).
    s['universe']['indices'] = [index]
    s = normalize(s)
    if live and not is_classic(s):
        return None
    out = to_ms_entry(s)
    if out is None:     # non-classic: Multi's own fields, formulas as the blocks run them
        out = {'buy': buy_formula(s), 'sell': sell_formula(s),
               'num_assets': s['size']['num_assets'], 'invest': s['size']['invest'],
               'order_by': s['select']['column'], 'signal_window': s['entry']['window']}
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


def signature(spec: dict) -> str:
    """Stable text of everything that changes a run (not name, idea, status, report)."""
    s = normalize(spec)
    run = {k: s[k] for k in ('universe', 'entry', 'select', 'exit', 'size', 'costs')}
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
    years = ', '.join(sorted(u['years'])) if u['years'] else '–'
    lines = [_t('sbs.d.universe', index=' + '.join(u['indices']), years=years,
                filters=('; ' + ', '.join(filters)) if filters else '')]
    if e['type'] == 'formula':
        lines.append(_t('sbs.d.entry_formula', buy=' & '.join(
            ln.strip() for ln in (e['buy'] or '').splitlines() if ln.strip()) or '–',
            window=e['window'], delay=e['delay']))
    else:
        lines.append(_t(f"sbs.d.entry_{e['type']}", p=f"{e['p_pct']:g}", seed=e['seed'],
                        delay=e['delay']))
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
    if stops:
        lines.append(', '.join(stops))
    lines.append(_t('sbs.d.size', n=z['num_assets'], invest=f"{z['invest']:,.0f}",
                    cap=_t(f"sf.sizing_cap_{z['cap']}"),
                    frac=_t('sbs.d.frac') if z['fractional'] else ''))
    lines.append(_t('sbs.d.costs', fee=f"{c['fee_pct']:g}", hold=c['min_hold_days'],
                    cool=c['cooldown_days']))
    return lines
