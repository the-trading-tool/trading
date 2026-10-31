"""Column reference under the Strategy Finder formula fields."""
import json
from pathlib import Path

from tradinglib import formula_reference as fr

LOCALES = Path(__file__).resolve().parent.parent / 'locales'


def _locale(lang):
    return json.loads((LOCALES / f'{lang}.json').read_text(encoding='utf-8'))


def test_every_catalog_entry_has_both_languages():
    for lang in ('de', 'en'):
        d = _locale(lang)
        missing = [c for c in fr.known_columns() if f'fref.c.{c}' not in d]
        assert not missing, (lang, missing)
        groups = [g for g, _ in fr.GROUPS] + ['context', 'other']
        assert all(f'fref.g.{g}' in d for g in groups), lang
        for k in ('fref.title', 'fref.filter', 'fref.filter_placeholder', 'fref.no_match',
                  'fref.lookahead_note', 'fref.syntax_md'):
            assert k in d, (lang, k)


def test_no_column_in_two_groups():
    cols = fr.known_columns()
    assert len(cols) == len(set(cols))


def test_lookahead_columns_are_catalogued():
    assert fr.LOOKAHEAD <= set(fr.known_columns())


def test_catalog_keeps_present_columns_only_and_collects_unknown():
    out = dict(fr.catalog(['Date', 'ticker', 'close', 'rsi', 'ewo', 'my_new_col', 'longName']))
    assert out['price'] == ['close']
    assert out['momentum'] == ['rsi']
    assert out['ewo'] == ['ewo']
    assert out['other'] == ['my_new_col']
    assert 'atc' not in out
    assert all('Date' not in cols and 'longName' not in cols for cols in out.values())


def test_catalog_follows_group_order():
    groups = [g for g, _ in fr.catalog(['sortino', 'close', 'ewo'])]
    assert groups == ['price', 'ewo', 'risk']


def test_every_example_has_both_languages():
    for lang in ('de', 'en'):
        d = _locale(lang)
        for k, _b, _s in fr.EXAMPLES:
            assert f'fref.ex.{k}.title' in d and f'fref.ex.{k}.desc' in d, (lang, k)
        for k in ('fref.examples', 'fref.examples_placeholder', 'fref.examples_note',
                  'fref.buy', 'fref.sell', 'fref.apply'):
            assert k in d, (lang, k)


def test_examples_evaluate():
    """Every example parses and runs on a frame holding all catalogued columns."""
    import numpy as np
    import pandas as pd
    from tradinglib import tools

    rng = np.random.default_rng(0)
    n = 260
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    frame = {'Date': pd.date_range('2024-01-01', periods=n, freq='B'), 'ticker': 'AAA'}
    for c in fr.known_columns():
        frame[c] = rng.normal(50, 20, n)
    frame.update(close=close, Open=close - 0.5, High=close + 1, Low=close - 1)
    df = pd.DataFrame(frame)
    for k, buy, sell in fr.EXAMPLES:
        x = tools.Tools().populate_indicators_for_df(df.copy(), [buy, sell], default_tf='D')
        window = 3 if '\n' in buy else 1
        for expr in (buy, sell):
            mask = tools.compute_signal_mask(x, expr, window=window)
            assert len(mask) == len(x), k


def test_removed_scores_are_not_offered():
    assert not {'overallTrend', 'overallValueTrend', 'ovtEma9'} & set(fr.known_columns())
