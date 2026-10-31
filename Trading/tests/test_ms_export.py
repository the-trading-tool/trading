"""Strategy Finder settings exported as a multi_transactions entry."""
import ast
import json
from pathlib import Path

from tradinglib import ms_export

LOCALES = Path(__file__).resolve().parent.parent / 'locales'


def _entry(**kw):
    base = dict(buy='(close <= sup_support * 1.02)\n(rsi < 40)', sell="(rsi >= 70)",
                num_assets='5', invest='100000', order_by='sortino', signal_window=2,
                trailing_stop=0.0, stop_loss=8.0, sizing_cap='factor', sizing_factor_max=2.0,
                fractional=False, require_isin=True, min_hold_days=15, cooldown_days=0,
                currency='ANY')
    base.update(kw)
    return ms_export.entry(**base)


def test_block_is_a_valid_literal_with_the_index_entry():
    e = _entry()
    text = ms_export.format_strategy("Dip 'test'", '^GDAXI', e)
    parsed = ast.literal_eval('{' + text + '}')
    assert parsed == {"Dip 'test'": {'^GDAXI': e}}
    # Multi Strategies reads the text with eval(); same result.
    assert eval('{' + text + '}') == parsed  # noqa: S307 - literal built above


def test_fields_match_what_multi_strategies_reads():
    e = _entry()
    assert e['buy'] == '(close <= sup_support * 1.02)\n(rsi < 40)'
    assert e['num_assets'] == 5 and e['invest'] == 100000
    assert e['order_by'] == 'sortino' and e['signal_window'] == 2
    assert e['stop_loss'] == 8 and e['trailing_stop'] == 0
    assert e['sizing_cap'] == 'factor' and e['sizing_factor_max'] == 2
    assert e['fractional'] is False and e['require_isin'] is True
    assert e['min_hold_days'] == 15
    assert 'cooldown_days' not in e and 'currency' not in e


def test_stops_are_always_written():
    # A missing stop_loss would fall back to the global stop_loss_pct in Multi Strategies.
    e = _entry(stop_loss=0, trailing_stop=0)
    assert e['stop_loss'] == 0 and e['trailing_stop'] == 0


def test_factor_only_with_factor_cap_and_currency_when_set():
    e = _entry(sizing_cap='normalized', currency='EUR')
    assert 'sizing_factor_max' not in e
    assert e['currency'] == 'EUR'


def test_locale_keys_in_both_languages():
    for lang in ('de', 'en'):
        d = json.loads((LOCALES / f'{lang}.json').read_text(encoding='utf-8'))
        for k in ('msx.title', 'msx.name', 'msx.name_default', 'msx.how', 'msx.not_transferred'):
            assert k in d, (lang, k)
