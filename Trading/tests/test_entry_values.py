"""Sell formulas with entry-bar values: tp, sl and entry_<col>."""
import numpy as np
import pandas as pd

from tradinglib import tools
from tradinglib.tools import BuySellSignalGenerator, entry_columns, entry_state_tokens


def _frame(prices, lows=None, buy=None, ticker='A', start='2026-01-01'):
    n = len(prices)
    return pd.DataFrame({
        'ticker': ticker,
        'Date': pd.date_range(start, periods=n),
        'close': np.asarray(prices, dtype=float),
        'Low': np.asarray(lows if lows is not None else prices, dtype=float),
        'b': buy if buy is not None else [0] * n,
    })


def _run(df, sell, buy='(b > 0)', delay=0, window=1):
    out = BuySellSignalGenerator(df.copy(), buy, sell, buy_delay_days=delay,
                                 signal_window=window).apply_signals()
    return out.sort_values(['ticker', 'Date'])['buySell'].tolist()


def test_tokens():
    q = '(close < entry_sup_support*0.97) | (close > tp) | (Low < sl) | (x.shift(1) > entry_Low)'
    assert entry_state_tokens(q) == ['entry_sup_support', 'tp', 'sl', 'entry_Low']
    assert entry_columns(q) == ['sup_support', 'Low']
    assert entry_state_tokens('(close < stop_loss) & (ewo > 0)') == []
    assert entry_state_tokens('(re_entry_x > 1) & (tps > 0)') == []


def test_fixed_stop_below_entry_low():
    # Buy on bar 1 (Low 95); later lows do not move the stop: 95 * 0.97 = 92.15.
    prices = [100, 100, 104, 96, 93, 92, 95]
    lows = [99, 95, 103, 94, 92, 91, 94]
    df = _frame(prices, lows, buy=[0, 1, 0, 0, 0, 0, 0])
    assert _run(df, '(close < entry_Low * 0.97)') == [0, 1, 0, 0, 0, -1, 0]


def test_entry_close_is_the_buy_bar_with_delay():
    # Signal on bar 0, bought on bar 1 at 110 -> 5 % stop at 104.5.
    df = _frame([100, 110, 106, 104, 90], buy=[1, 0, 0, 0, 0])
    assert _run(df, '(close < entry_close * 0.95)', delay=1) == [0, 1, 0, -1, 0]


def test_each_position_uses_its_own_entry():
    # 1st buy at 100 -> sell at 94; 2nd buy at 90 -> 94 is no stop, 85 is.
    df = _frame([100, 98, 94, 90, 94, 85], buy=[1, 0, 0, 1, 0, 0])
    assert _run(df, '(close < entry_close * 0.95)') == [1, 0, -1, 1, 0, -1]


def test_shift_works_with_entry_values():
    # Sell: below entry AND lower than the day before (a rolling/shift op).
    df = _frame([100, 99, 101, 98, 97], buy=[1, 0, 0, 0, 0])
    assert _run(df, '(close < entry_close) & (close < close.shift(1))') == [1, -1, 0, 0, 0]


def test_no_bleed_between_tickers():
    a = _frame([100, 90, 80], buy=[1, 0, 0], ticker='A')
    b = _frame([50, 60, 70], buy=[1, 0, 0], ticker='B')
    df = pd.concat([a, b], ignore_index=True)
    assert _run(df, '(close < entry_close * 0.95)') == [1, -1, 0, 1, 0, 0]


def test_entry_value_not_visible_on_or_before_entry():
    # Multi-line with window 3: the 'close < entry_close' line must not be held
    # over from bars before the buy.
    df = _frame([90, 95, 100, 101, 102], buy=[0, 0, 1, 0, 0])
    df['flag'] = [0, 0, 0, 1, 0]
    sell = '(close < entry_close)\n(flag > 0)'
    assert _run(df, sell, window=3) == [0, 0, 1, 0, 0]


def test_unknown_entry_column_stops_before_evaluating():
    # Caught by the formula check: nothing is evaluated, not even the buys.
    df = _frame([100, 50, 40], buy=[1, 0, 0])
    gen = BuySellSignalGenerator(df.copy(), '(b > 0)', '(close < entry_nope)')
    out = gen.apply_signals()
    assert out['buySell'].tolist() == [0, 0, 0]
    assert gen.formula_errors['sell'] == [{'kind': 'unknown', 'names': ['nope']}]


def _old_tp_sl(df, buy, sell):
    """The former per-row path for tp/sl (row context + entry tp/sl)."""
    df = df.sort_values(['ticker', 'Date']).reset_index(drop=True)
    bm = tools.compute_signal_mask(df, buy).to_numpy()
    bs = np.where(bm, 1, 0)
    op = {}
    for i in range(len(df)):
        tk = df.at[i, 'ticker']
        if tk in op:
            ctx = {**df.iloc[i].to_dict(), 'tp': op[tk][0], 'sl': op[tk][1]}
            if bool(eval(sell, {}, ctx)):
                bs[i] = -1
                del op[tk]
                continue
        if bs[i] == 1 and tk not in op:
            op[tk] = (df.at[i, 'take_profit'], df.at[i, 'stop_loss'])
    return bs.tolist()


def test_tp_sl_same_result_as_before():
    rng = np.random.default_rng(7)
    frames = []
    for tk in ('A', 'B', 'C'):
        close = 100 * np.cumprod(1 + rng.normal(0, 0.03, 120))
        atr = close * 0.02
        frames.append(pd.DataFrame({
            'ticker': tk, 'Date': pd.date_range('2025-01-01', periods=120),
            'close': close, 'Low': close * 0.99, 'take_profit': close + 4 * atr,
            'stop_loss': close - 2 * atr, 'ewo': rng.normal(0, 1, 120)}))
    df = pd.concat(frames, ignore_index=True)
    buy = '(ewo > 1)'
    for sell in ('(close < sl) | (close > tp)', '(Low <= sl)', '(close > tp) & (ewo < 0)'):
        new = BuySellSignalGenerator(df.copy(), buy, sell).apply_signals()
        got = new.sort_values(['ticker', 'Date'])['buySell'].tolist()
        assert got == _old_tp_sl(df, buy, sell), sell


def test_formula_scanners_read_the_source_column():
    from tradinglib.fetch_data import indicators_for_expressions
    from tradinglib import scalable_signals, signal_check
    assert indicators_for_expressions(['(close < entry_sup_support * 0.97)']) == ['sup']
    avail = {'close', 'sup_support', 'take_profit', 'stop_loss'}
    assert scalable_signals.missing_columns('(close < entry_sup_support) | (close < sl)', avail) == []
    assert scalable_signals.missing_columns('(close < entry_nope)', avail) == ['nope']
    d = pd.DataFrame(columns=['ticker', 'Date', 'close', 'sup_support', 'stop_loss', 'other'])
    cols = signal_check.formula_columns(d, '(b > 0)', '(close < entry_sup_support) | (close < sl)')
    assert 'sup_support' in cols and 'stop_loss' in cols and 'other' not in cols
