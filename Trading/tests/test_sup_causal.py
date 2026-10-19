"""sup_support / sup_resistance must be causal: each bar sees only its own window.

The old indicator wrote the LAST 21-bar low/high into every row, so backfilled
year DBs held one constant (future) level per ticker.
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from tradinglib.indicator import indicator
from tradinglib.indicator.sup import Sup


def _frame(n=400, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range('2023-01-02', periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    return pd.DataFrame({'Open': close, 'High': high, 'Low': low, 'Close': close,
                         'Volume': 1000.0}, index=idx)


def test_matches_pine_definition():
    df = _frame()
    sup, res = indicator.support_resistance(df)
    pd.testing.assert_series_equal(sup, df['Low'].rolling(21).min(), check_names=False)
    pd.testing.assert_series_equal(res, df['High'].rolling(21).max(), check_names=False)
    valid = sup.notna()
    assert (sup[valid] <= df['Low'][valid]).all()          # current bar included
    assert (res[valid] >= df['High'][valid]).all()


def test_future_does_not_change_past():
    df = _frame()
    full_s, full_r = indicator.support_resistance(df)
    cut_s, cut_r = indicator.support_resistance(df.iloc[:250])
    pd.testing.assert_series_equal(full_s.iloc[:250], cut_s)
    pd.testing.assert_series_equal(full_r.iloc[:250], cut_r)


def test_indicator_column_varies_and_last_value_unchanged():
    df = _frame()
    old_last = df['Low'].rolling(21).min().iloc[-1]
    s = Sup(df.copy())
    assert s.df['sup_support'].iloc[-1] == pytest.approx(old_last)
    assert s.df['sup_support'].dropna().nunique() > 10      # not one broadcast scalar


def test_empty_frame():
    sup, res = indicator.support_resistance(_frame().iloc[:0])
    assert sup.empty and res.empty


@pytest.mark.parametrize('freq,rule', [('W', 'W-SUN'), ('M', 'ME')])
def test_htf_equals_frame_ending_that_day(freq, rule):
    """Value on day d == rolling(21) on a wk/mo frame built from data up to d."""
    df = _frame(n=700)
    sup, res = indicator.support_resistance_htf(df, freq)
    for d in df.index[-60::7]:
        part = df.loc[:d]
        bars = part.resample(rule).agg({'Low': 'min', 'High': 'max'}).dropna()
        assert sup[d] == pytest.approx(bars['Low'].rolling(21).min().iloc[-1])
        assert res[d] == pytest.approx(bars['High'].rolling(21).max().iloc[-1])


@pytest.mark.parametrize('freq', ['W', 'M'])
def test_htf_future_does_not_change_past(freq):
    df = _frame(n=700)
    full_s, full_r = indicator.support_resistance_htf(df, freq)
    cut = df.index[500]                                      # mid-period cut
    cut_s, cut_r = indicator.support_resistance_htf(df.loc[:cut], freq)
    pd.testing.assert_series_equal(full_s.loc[:cut], cut_s)
    pd.testing.assert_series_equal(full_r.loc[:cut], cut_r)


def test_htf_nan_until_enough_periods():
    df = _frame(n=60)                                        # ~12 weeks only
    sup, _ = indicator.support_resistance_htf(df, 'W')
    assert sup.isna().all()


def test_backfill_writes_daily_and_htf_series():
    import asset_perf2

    df = _frame(n=700)

    class FakeFt:
        def load_price_data(self, symbol, period, interval):
            return df.copy()

        def init_instance(self, name, df, symbol):
            setattr(self, name, Sup(df.copy(), symbol=symbol))

    con = sqlite3.connect(':memory:')
    cols = ['sup_support', 'sup_resistance', 'sup_support_wk', 'sup_resistance_wk',
            'sup_support_mo', 'sup_resistance_mo']
    con.execute('CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, '
                + ', '.join(f'{c} REAL' for c in cols) + ')')
    dates = [d.strftime('%Y-%m-%d 00:00:00') for d in df.index[-120:]]
    con.executemany('INSERT INTO asset_simulation (ticker, Date) VALUES (?, ?)',
                    [('T', d) for d in dates])

    n = asset_perf2._backfill_symbol('T', ['sup'], con, 'asset_simulation', '', FakeFt())
    assert n == len(dates)

    got = pd.read_sql_query('SELECT * FROM asset_simulation ORDER BY Date', con)
    want_s = df['Low'].rolling(21).min().iloc[-120:].to_numpy()
    np.testing.assert_allclose(got['sup_support'].to_numpy(), want_s)
    want_wk, _ = indicator.support_resistance_htf(df, 'W')
    np.testing.assert_allclose(got['sup_support_wk'].to_numpy(), want_wk.iloc[-120:].to_numpy())
    _, want_mo = indicator.support_resistance_htf(df, 'M')
    np.testing.assert_allclose(got['sup_resistance_mo'].to_numpy(), want_mo.iloc[-120:].to_numpy())
    # a 21-month level may legitimately stay flat over 120 days; daily/weekly may not
    for c in cols[:4]:
        assert got[c].nunique() > 1, c
