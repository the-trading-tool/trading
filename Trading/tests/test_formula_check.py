"""Formulas are checked before evaluation: syntax, tp/sl/entry_ in buy, unknown columns."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from tradinglib import formula_reference as fr
from tradinglib import tools
from tradinglib.tools import BuySellSignalGenerator, formula_names, formula_problems

COLS = ['ticker', 'Date', 'close', 'Low', 'High', 'rsi', 'sup_support', 'stop_loss', 'sector']


def _kinds(p):
    return [x['kind'] for x in p]


def test_valid_formulas_pass():
    for f in ('(rsi < 30) & (close > sup_support)',
              '(close <= sup_support * 1.02)\n(rsi > 50)',
              '(close > close.rolling(5).mean()) & ~(rsi > 70)',
              '(High >= High.rolling(21).max().shift(1)) | (close[-1] > 0)',
              "(sector == 'Technology') & (abs(rsi - 50) > 10)",
              '(rsi > %%lo) & (rsi < %%hi)',
              'not (rsi > 70) and (close > 0)',
              ''):
        assert formula_problems(f, COLS, 'buy') == [], f
    assert formula_problems('(close < entry_sup_support * 0.97) | (close < sl) | (close > tp)',
                            COLS, 'sell') == []


def test_unclosed_and_unmatched_brackets():
    p = formula_problems('(close < sup_support', COLS, 'buy')
    assert _kinds(p) == ['syntax'] and 'never closed' in p[0]['msg']
    p = formula_problems('(rsi < 30))', COLS, 'buy')
    assert _kinds(p) == ['syntax'] and 'unmatched' in p[0]['msg']
    # the line with the error is named, the good line is not
    p = formula_problems('(rsi < 30)\n(close > (sup_support * 2)', COLS, 'sell')
    assert [x['line'] for x in p] == ['(close > (sup_support * 2)']
    # without columns the syntax is still checked (while typing)
    assert _kinds(formula_problems('(close <', None, 'buy')) == ['syntax']


def test_entry_values_only_in_sell():
    p = formula_problems('(close > entry_close) & (close < sl)', COLS, 'buy')
    assert _kinds(p) == ['entry_in_buy'] and p[0]['names'] == ['entry_close', 'sl']


def test_unknown_columns():
    p = formula_problems('(rsi < 30) & (atc_top_low > 0) & (Close > 1)', COLS, 'buy')
    assert p == [{'kind': 'unknown', 'names': ['atc_top_low']}]       # Close ~ close
    p = formula_problems('(close < entry_nope)', COLS, 'sell')
    assert p == [{'kind': 'unknown', 'names': ['nope']}]
    assert formula_names("(ma20@W > 1) & (sector == 'X') & abs(rsi) & 1e5") == ['ma20@W', 'sector', 'rsi']


def _frame():
    return pd.DataFrame({'ticker': 'A', 'Date': pd.date_range('2026-01-01', periods=6),
                         'close': np.arange(6, dtype=float), 'b': [1, 0, 0, 1, 0, 0]})


def test_generator_stops_once_on_broken_formula(caplog):
    gen = BuySellSignalGenerator(_frame(), '(b > 0)', '(close > 2', buy_delay_days=0)
    with caplog.at_level('ERROR'):
        out = gen.apply_signals()
    assert (out['buySell'] == 0).all()                  # nothing evaluated, no buys either
    assert list(gen.formula_errors) == ['sell']
    assert caplog.text.count('FORMULA-ERROR') == 1 and 'SELL-ERROR' not in caplog.text
    assert 'Close' not in out.columns                   # aliases removed as usual


def test_generator_runs_with_good_formula():
    gen = BuySellSignalGenerator(_frame(), '(b > 0)', '(close > 2)')
    out = gen.apply_signals()
    assert gen.formula_errors == {} and out['buySell'].tolist() == [1, 0, 0, -1, 0, 0]


def test_row_fallback_reports_once(monkeypatch, caplog):
    # A runtime error the pre-check cannot see: the row-by-row path stops after the first.
    def boom(*a, **k):
        raise RuntimeError('vectorised failed')
    monkeypatch.setattr(tools, 'compute_signal_mask',
                        lambda df, cond, **k: pd.Series(df['b'] > 0, index=df.index)
                        if cond == '(b > 0)' else boom())
    df = _frame()
    df['txt'] = 'x'
    gen = BuySellSignalGenerator(df, '(b > 0)', '(txt / 2 > 1)')
    with caplog.at_level('ERROR'):
        out = gen.apply_signals()
    assert caplog.text.count('SELL-ERROR') == 1
    assert gen.formula_errors['sell'][0]['kind'] == 'runtime'
    assert out['buySell'].tolist() == [1, 0, 0, 1, 0, 0]  # buys kept, no sells (as before)


def test_messages_localised_in_both_languages():
    root = Path(__file__).resolve().parent.parent / 'locales'
    keys = ('fref.err.buy', 'fref.err.sell', 'fref.err.syntax', 'fref.err.unclosed',
            'fref.err.unmatched', 'fref.err.entry_in_buy', 'fref.err.unknown',
            'fref.err.not_started')
    for lang in ('de', 'en'):
        d = json.loads((root / f'{lang}.json').read_text(encoding='utf-8'))
        assert all(k in d for k in keys), lang
    problems = fr.check('(close < sup_support', '(close > entry_x)', COLS)
    msgs = fr.problem_messages(problems)
    assert len(msgs) == 2 and '(close < sup_support' in msgs[0] and 'x' in msgs[1]
    assert fr.check('(rsi < 30)', '(rsi > 70)', COLS) == {}
