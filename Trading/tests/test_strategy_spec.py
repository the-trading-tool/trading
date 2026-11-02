"""Strategy as data (strategy_spec) and the time exit of the simulator."""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradinglib import strategy_spec as ss

ROOT = Path(__file__).resolve().parent.parent


def _frame(n_tickers=6, n_days=120, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_tickers):
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
        rows.append(pd.DataFrame({
            'ticker': f'T{k}', 'Date': pd.bdate_range('2024-01-01', periods=n_days),
            'close': close, 'sortino': rng.normal(0, 1, n_days), 'vola': rng.uniform(5, 15, n_days),
            'rsi': rng.uniform(10, 90, n_days)}))
    return pd.concat(rows, ignore_index=True)


# ------------------------------------------------------------------ normalize

def test_normalize_fills_and_repairs():
    s = ss.normalize({'entry': {'type': 'nonsense', 'window': '3'}, 'size': {'num_assets': 'x'},
                      'exit': {'days': 0}, 'status': 'whatever'})
    assert s['entry']['type'] == 'formula' and s['entry']['window'] == 3
    assert s['size']['num_assets'] == ss.DEFAULT['size']['num_assets']
    assert s['exit']['days'] == 1 and s['status'] == 'idea'
    assert set(s) == set(ss.DEFAULT)


def test_classic_and_effective_formulas():
    s = ss.normalize({'entry': {'buy': '(rsi < 30)'}, 'exit': {'sell': '(rsi > 70)'}})
    assert ss.is_classic(s)
    assert ss.buy_formula(s) == '(rsi < 30)' and ss.sell_formula(s) == '(rsi > 70)'
    assert ss.max_hold_days(s) == 0
    r = ss.normalize({'entry': {'type': 'random'}, 'exit': {'type': 'days', 'days': 30}})
    assert not ss.is_classic(r)
    assert ss.buy_formula(r) == f'({ss.RANDOM_COL} > 0)'
    assert ss.sell_formula(r) == ss.NEVER and ss.max_hold_days(r) == 30
    a = ss.normalize({'entry': {'type': 'always'}, 'exit': {'type': 'formula_days', 'sell': 'x'}})
    assert ss.buy_formula(a) == ss.ALWAYS and ss.sell_formula(a) == 'x'
    assert ss.max_hold_days(a) == 21
    # an explicit field value wins over the spec's own (optimizer templates)
    assert ss.buy_formula(s, '(rsi < %%x)') == '(rsi < %%x)'


# ------------------------------------------------------------------ selection / entry

def test_column_selection_is_the_classic_sort():
    df = _frame()
    spec = ss.normalize({'select': {'type': 'column', 'column': 'sortino'}})
    a = ss.order_signals(df, spec)
    b = df.sort_values(['Date', 'sortino'], ascending=[True, False])
    pd.testing.assert_frame_equal(a, b)
    asc = ss.order_signals(df, {**spec, 'select': {'type': 'column_asc', 'column': 'sortino'}})
    first_day = asc[asc['Date'] == asc['Date'].min()]
    assert first_day['sortino'].is_monotonic_increasing


def test_random_selection_does_not_depend_on_input_order():
    df = _frame()
    spec = ss.normalize({'select': {'type': 'random', 'seed': 7}})
    a = ss.order_signals(df, spec)
    b = ss.order_signals(df.sample(frac=1, random_state=3), spec)
    assert list(zip(a['ticker'], a['Date'])) == list(zip(b['ticker'], b['Date']))
    c = ss.order_signals(df, {**spec, 'select': {'type': 'random', 'seed': 8}})
    assert list(a['ticker']) != list(c['ticker'])


def test_expression_selection_and_error():
    df = _frame()
    out = ss.order_signals(df, {'select': {'type': 'expr', 'expr': '-rsi'}})
    day = out[out['Date'] == out['Date'].min()]
    assert day['rsi'].is_monotonic_increasing
    with pytest.raises(ValueError):
        ss.order_signals(df, {'select': {'type': 'expr', 'expr': 'no_such_column * 2'}})


def test_random_entry_rate_and_determinism():
    df = _frame(n_tickers=20, n_days=500)
    spec = {'entry': {'type': 'random', 'p_pct': 5, 'seed': 3}}
    a = ss.add_entry_column(df, spec)
    assert abs(a[ss.RANDOM_COL].mean() - 0.05) < 0.01
    b = ss.add_entry_column(df.iloc[::-1], spec).sort_index()
    assert (a[ss.RANDOM_COL] == b[ss.RANDOM_COL]).all()
    assert ss.add_entry_column(df, {}) is df          # formula entry: untouched


def test_matched_random_signals_and_hold():
    df = _frame(n_tickers=10, n_days=300)
    sig = ss.random_entry_signals(df, 0.1, seed=1)
    assert abs((sig['buySell'] == 1).mean() - 0.1) < 0.02 and (sig['buySell'] >= 0).all()
    assert sig['Date'].is_monotonic_increasing
    trades = pd.DataFrame({'buyDate': ['2024-01-01', '2024-01-01', '2024-01-01'],
                           'sellDate': ['2024-01-11', '2024-01-31', None]})
    assert ss.median_hold_days(trades) == 20
    assert ss.median_hold_days(pd.DataFrame()) == 21


# ------------------------------------------------------------------ multi strategies

ENTRY = {'buy': '(Low <= atc_bot_low)\n(relvol_ratio>=1.5)', 'sell': '(rsi >= 72)',
         'num_assets': 4, 'invest': 20000, 'order_by': 'sortino', 'signal_window': 2,
         'trailing_stop': 0, 'stop_loss': 8, 'sizing_cap': 'factor', 'sizing_factor_max': 2,
         'fractional': False, 'require_isin': True, 'min_hold_days': 2, 'cooldown_days': 4}


def test_ms_entry_round_trip():
    spec = ss.from_ms_entry('Dip', '^MDAXI', ENTRY)
    assert spec['name'] == 'Dip / ^MDAXI' and spec['universe']['index'] == '^MDAXI'
    assert spec['costs']['fee_pct'] == 0 and spec['entry']['delay'] == 0   # as Multi computes
    assert ss.is_classic(spec)
    assert ss.to_ms_entry(spec) == ENTRY


def test_missing_ms_fields_come_from_the_base():
    base = {'costs': {'stop_loss': 12}, 'size': {'cap': 'cash'}}
    spec = ss.from_ms_entry('X', '^SPX', {'buy': 'a', 'sell': 'b', 'num_assets': 3,
                                          'invest': 1000, 'order_by': 'sharpe'}, base=base)
    assert spec['costs']['stop_loss'] == 12 and spec['size']['cap'] == 'cash'


def test_non_classic_spec_has_no_ms_entry():
    assert ss.to_ms_entry({'exit': {'type': 'days'}}) is None


def test_parse_multi_transactions_double_encoded():
    lit = repr({'S': {'^GDAXI': {'buy': 'x'}}})
    assert ss.parse_multi_transactions(json.dumps(lit))['S']['^GDAXI']['buy'] == 'x'
    assert ss.parse_multi_transactions('kein literal (') == {}
    assert ss.ms_strategies(lit) == [('S', '^GDAXI', {'buy': 'x'})]


# ------------------------------------------------------------------ bundles

def test_indices_default_to_the_index_and_lead_it():
    s = ss.normalize({'universe': {'index': '^SPX'}})
    assert s['universe']['indices'] == ['^SPX'] and not ss.is_bundle(s)
    b = ss.normalize({'universe': {'index': '^X', 'indices': ['^MDAXI', '^SDAXI', '^MDAXI']}})
    assert b['universe']['indices'] == ['^MDAXI', '^SDAXI'] and b['universe']['index'] == '^MDAXI'
    assert ss.is_bundle(b) and ss.bundle_label(b) == '^MDAXI + ^SDAXI'


def test_bundle_is_not_exportable_to_multi():
    spec = ss.from_ms_entry('Dip', '^MDAXI', ENTRY)
    assert ss.ms_exportable(spec)
    spec['universe']['indices'] = ['^MDAXI', '^SDAXI']
    assert not ss.ms_exportable(spec) and ss.to_ms_entry(spec) is None


def test_merge_bundle_counts_a_stock_once():
    a = pd.DataFrame({'ticker': ['A', 'A', 'B'], 'Date': [1, 2, 1], 'stockIndex': '^X'})
    b = pd.DataFrame({'ticker': ['B', 'C'], 'Date': [1, 1], 'stockIndex': '^Y'})
    out, dropped = ss.merge_bundle([('^X', a), ('^Y', b), ('^Z', pd.DataFrame())])
    assert dropped == 1 and sorted(out['ticker']) == ['A', 'A', 'B', 'C']
    assert out.loc[out['ticker'] == 'B', 'stockIndex'].tolist() == ['^X']   # first index keeps it


def test_trading_currencies():
    df = pd.DataFrame({'ticker': ['A', 'A', 'B', 'C', 'D', 'E'],
                       'currency': ['EUR', 'EUR', 'USD', None, 'EUR', '0']})
    assert ss.trading_currencies(df) == {'EUR': 2, 'USD': 1}
    assert ss.trading_currencies(pd.DataFrame({'ticker': ['A']})) == {}


def test_bundle_currency_check():
    # one stray among many: allowed, named
    df = pd.DataFrame({'ticker': [f'T{i}' for i in range(40)] + ['QGEN'],
                       'currency': ['EUR'] * 40 + ['USD']})
    ok, main, foreign = ss.bundle_currency_check(df)
    assert ok and main == 'EUR' and foreign == {'QGEN': 'USD'}
    # two markets: blocked
    mixed = pd.DataFrame({'ticker': [f'T{i}' for i in range(60)],
                          'currency': ['USD'] * 50 + ['EUR'] * 10})
    assert ss.bundle_currency_check(mixed)[0] is False


# ------------------------------------------------------------------ references (Multi)

SAVED = {
    'Dip': ss.normalize({'name': 'Dip', 'entry': {'buy': '(rsi < 30)', 'delay': 1, 'window': 2},
                         'exit': {'sell': '(rsi > 70)'}, 'select': {'column': 'sharpe'},
                         'size': {'num_assets': 5, 'invest': 50000, 'cap': 'factor_capped'},
                         'costs': {'fee_pct': 0.3, 'stop_loss': 8}}),
    'Zufall': ss.normalize({'name': 'Zufall', 'entry': {'type': 'random', 'p_pct': 3},
                            'exit': {'type': 'days', 'days': 30}}),
}


def test_json_with_booleans_parses():
    assert ss.parse_multi_transactions('{"S": {"^X": {"buy": "a", "enabled": true}}}') == \
        {'S': {'^X': {'buy': 'a', 'enabled': True}}}


def test_entries_without_reference_stay_identical():
    tx = {'S': {'^GDAXI': dict(ENTRY)}}
    assert not ss.has_references(tx)
    assert ss.expand_references(tx, SAVED) == tx
    assert ss.expand_references(tx, SAVED, live=True) == tx


def test_reference_resolves_with_overrides():
    tx = {'S': {'^MDAXI': {'strategy': 'Dip', 'invest': 20000, 'num_assets': 4}}}
    e = ss.expand_references(tx, SAVED)['S']['^MDAXI']
    assert e['buy'] == '(rsi < 30)' and e['sell'] == '(rsi > 70)' and e['order_by'] == 'sharpe'
    assert e['invest'] == 20000 and e['num_assets'] == 4          # the entry wins
    assert e['sizing_cap'] == 'factor_capped' and e['stop_loss'] == 8
    s = e['_spec']
    assert s['universe']['indices'] == ['^MDAXI'] and s['size']['invest'] == 20000
    assert s['costs']['fee_pct'] == 0.3 and s['entry']['delay'] == 1
    live = ss.expand_references(tx, SAVED, live=True)['S']['^MDAXI']
    assert '_spec' not in live and live['buy'] == '(rsi < 30)' and live['strategy'] == 'Dip'


def test_live_leaves_out_what_it_cannot_run():
    tx = {'A': {'^X': {'strategy': 'Zufall', 'invest': 1000}, '^Y': {'strategy': 'Dip'}},
          'B': {'^Z': {'strategy': 'Zufall'}},
          'C': {'^W': {'strategy': 'gibt es nicht'}}}
    live = ss.expand_references(tx, SAVED, live=True)
    assert list(live) == ['A'] and list(live['A']) == ['^Y']
    back = ss.expand_references(tx, SAVED)
    assert back['A']['^X']['_spec']['entry']['type'] == 'random'
    assert ss.max_hold_days(back['A']['^X']['_spec']) == 30
    assert back['C']['^W']['_ref_error'] == 'missing'


def test_load_transactions_and_saved_from_value():
    cfg = _Cfg()
    cfg.set_value('multi_transactions', repr({'S': {'^X': {'strategy': 'Dip', 'invest': 1}}}))
    cfg.set_value(ss.STORE_KEY, json.dumps(SAVED))
    assert ss.load_transactions(cfg, live=True)['S']['^X']['buy'] == '(rsi < 30)'
    assert ss.saved_from_value(json.dumps(json.dumps(SAVED)))['Dip']['select']['column'] == 'sharpe'
    empty = _Cfg()
    assert ss.load_transactions(empty, default={'D': {}}) == {'D': {}}


def test_strategy_level_fields_become_index_defaults():
    """What the trading page's editor stores: shared fields next to the indices."""
    tx = {'S': {'num_assets': 5, 'invest': 10000, 'order_by': 'sortino', 'enabled': True,
                '^GDAXI': {'buy': 'a', 'sell': 'b', 'num_assets': 3, 'stop_loss': 8},
                '^SPX': {'buy': 'c', 'sell': 'd'}},
          'Flat': {'buy': 'x', 'sell': 'y', 'num_assets': 2},          # old flat form
          'Plain': {'^X': dict(ENTRY)}}
    out = ss.apply_strategy_defaults(tx)
    assert set(out['S']) == {'^GDAXI', '^SPX'}                       # only indices left
    assert out['S']['^GDAXI']['num_assets'] == 3 and out['S']['^GDAXI']['stop_loss'] == 8
    assert out['S']['^SPX']['num_assets'] == 5 and out['S']['^SPX']['invest'] == 10000
    assert 'enabled' not in out['S']['^SPX']
    assert out['Flat'] == tx['Flat'] and out['Plain'] == tx['Plain']  # untouched


def test_reference_block_is_a_literal():
    import ast as _ast
    from tradinglib import ms_export
    text = ms_export.reference_block("Dip 'x'", ['^MDAXI', '^SDAXI'], 20000.0, '4')
    parsed = _ast.literal_eval('{' + text + '}')
    # a bundle is ONE entry: one pot shared by its members
    assert parsed == {"Dip 'x'": {'^MDAXI + ^SDAXI': {'strategy': "Dip 'x'", 'invest': 20000,
                                                      'num_assets': 4}}}


def test_index_members():
    assert ss.index_members('^GDAXI + ^MDAXI') == ['^GDAXI', '^MDAXI']
    assert ss.index_members('^SPX') == ['^SPX'] and not ss.is_bundle_key('^SPX')
    assert ss.index_members(' ^A+^B + ^A ') == ['^A', '^B'] and ss.is_bundle_key('^A+^B')


def test_bundle_reference_resolves_to_one_pot():
    tx = {'S': {'^GDAXI + ^MDAXI': {'strategy': 'Dip', 'invest': 100000, 'num_assets': 15}}}
    e = ss.expand_references(tx, SAVED)['S']['^GDAXI + ^MDAXI']
    assert e['_spec']['universe']['indices'] == ['^GDAXI', '^MDAXI']
    assert e['invest'] == 100000 and e['num_assets'] == 15
    live = ss.expand_references(tx, SAVED, live=True)['S']['^GDAXI + ^MDAXI']
    assert live['buy'] == '(rsi < 30)' and live['sizing_cap'] == 'factor_capped'


# ------------------------------------------------------------------ storage / description

class _Cfg:
    """Config stand-in that serialises like SystemConfig (json.dumps on set)."""

    def __init__(self):
        self.d = {}

    def get_value(self, key, default=None):
        return json.loads(self.d[key]) if key in self.d else default

    def set_value(self, key, value):
        self.d[key] = json.dumps(value)


def test_save_load_delete():
    cfg = _Cfg()
    with pytest.raises(ValueError):
        ss.save(cfg, {'name': ''})
    ss.save(cfg, {'name': 'A', 'idea': 'dip', 'entry': {'buy': 'x'}})
    ss.save(cfg, {'name': 'B'})
    got = ss.load_all(cfg)
    assert set(got) == {'A', 'B'} and got['A']['entry']['buy'] == 'x' and got['A']['saved']
    assert ss.delete(cfg, 'A') and set(ss.load_all(cfg)) == {'B'}
    assert not ss.delete(cfg, 'A')


def test_delete_many_writes_once_and_reports_what_went():
    cfg = _Cfg()
    for n in ('A', 'B', 'C'):
        ss.save(cfg, {'name': n})
    writes = []
    orig = cfg.set_value
    cfg.set_value = lambda k, v: (writes.append(k), orig(k, v))
    assert ss.delete_many(cfg, ['A', 'C', 'X', 'A']) == ['A', 'C']
    assert set(ss.load_all(cfg)) == {'B'} and writes == [ss.STORE_KEY]
    assert ss.delete_many(cfg, ['X']) == [] and writes == [ss.STORE_KEY]


def test_referenced_names():
    tx = {'S': {'^GDAXI': {'strategy': 'A', 'invest': 1}, '^MDAXI': {'buy': 'x'}},
          'T': {'^GDAXI + ^MDAXI': {'strategy': 'B'}}}
    assert ss.referenced_names(repr(tx)) == {'A', 'B'}
    assert ss.referenced_names('') == set()


def test_signature_ignores_the_description():
    a = ss.normalize({'name': 'A', 'idea': 'x', 'entry': {'buy': 'q'}})
    b = ss.normalize({'name': 'B', 'status': 'live', 'entry': {'buy': 'q'}})
    assert ss.signature(a) == ss.signature(b)
    assert ss.signature(a) != ss.signature({'entry': {'buy': 'r'}})


def test_describe_and_locales():
    for spec in ({'entry': {'buy': 'a\nb'}, 'costs': {'stop_loss': 5}},
                 {'entry': {'type': 'random'}, 'select': {'type': 'random'},
                  'exit': {'type': 'days'}},
                 {'entry': {'type': 'always'}, 'select': {'type': 'expr', 'expr': '-rsi'},
                  'exit': {'type': 'never'}, 'universe': {'sector': 'Tech', 'require_isin': True}}):
        lines = ss.describe(spec)
        assert len(lines) >= 5 and all(isinstance(x, str) and x for x in lines)
        assert not any('sbs.' in x for x in lines), lines          # every key resolved
    src = (ROOT / 'tradinglib' / 'strategy_spec.py').read_text(encoding='utf-8')
    src += (ROOT / 'tradinglib' / 'premium' / 'asset_simulator.py').read_text(encoding='utf-8')
    keys = set(re.findall(r"'(sbs\.[a-z_.]+)'", src)) | {'cfg.sf_builder', 'cfg.sf_builder_help'}
    for kind, values in (('entry', ss.ENTRY_TYPES), ('select', ss.SELECT_TYPES),
                         ('exit', ss.EXIT_TYPES)):
        keys |= {f'sbs.{kind}_{v}' for v in values} | {f'sbs.d.{kind}_{v}' for v in values}
    keys |= {f'sbs.status_{v}' for v in ss.STATUSES} | {f'cfg.sf_builder_{v}' for v in ('blocks', 'classic')}
    keys = {k for k in keys if not k.endswith(('.', '_'))}
    for lang in ('de', 'en'):
        d = json.loads((ROOT / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        missing = sorted(k for k in keys if k not in d)
        assert not missing, (lang, missing)


# ------------------------------------------------------------------ simulator time exit

def _sim_frame(days=30):
    dates = pd.bdate_range('2024-01-01', periods=days)
    return pd.DataFrame({'ticker': 'AAA', 'Date': [str(d) for d in dates],
                         'close': np.linspace(100, 130, days), 'vola': 10.0, 'longName': 'AAA',
                         'ISIN': '', 'currency': 'EUR', 'stockIndex': '^T',
                         'buySell': [1] + [0] * (days - 1)})


def _run(max_hold):
    from tradinglib.premium.asset_simulator import PortfolioSimulator
    p = PortfolioSimulator(data=_sim_frame(), initial_cash=10000, max_assets=1, fee_pct=0,
                           sizing_cap='none', fractional=False, min_hold_days=0,
                           cooldown_days=0, max_hold_days=max_hold)
    p.simulate()
    return p, p.get_transaction_dataframe()


def test_time_exit_sells_after_n_calendar_days():
    p, tx = _run(14)
    sells = tx[tx['action'] == 'sell']
    assert len(sells) == 1 and not p.portfolio
    held = pd.Timestamp(sells['timestamp'].iloc[0]) - pd.Timestamp(tx['timestamp'].iloc[0])
    assert held.days >= 14 and held.days < 17          # first trading day on or after day 14


def test_no_time_exit_by_default():
    p, tx = _run(0)
    assert (tx['action'] == 'sell').sum() == 0 and 'AAA' in p.portfolio
