"""wkTrend must be causal: week-to-date close vs. the last COMPLETED week's close.

The engine used the last weekly bar up to the day; that bar is dated Monday and,
in multi-day runs, already closes on Friday -- Monday's row knew Friday's close.
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asset_perf2


def _frame(n=120, seed=4):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range('2026-01-05', periods=n)               # starts on a Monday
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return pd.DataFrame({'Open': close, 'High': close * 1.01, 'Low': close * 0.99,
                         'Close': close, 'Volume': 1000.0}, index=idx)


def test_montag_kennt_nur_den_freitag_davor():
    df = _frame()
    wk = asset_perf2.wk_trend_series(df)
    monday = df.index[10]                                       # third Monday
    assert monday.dayofweek == 0
    prev_friday_close = df.loc[:monday - pd.Timedelta(days=1), 'Close'].iloc[-1]
    expected = round(round((df.loc[monday, 'Close'] - prev_friday_close) / df.loc[monday, 'Close'], 4) * 100, 2)
    assert wk[monday] == expected


def test_zukunft_abschneiden_aendert_nichts():
    df = _frame()
    full = asset_perf2.wk_trend_series(df)
    cut = df.index[57]                                          # mid-week
    part = asset_perf2.wk_trend_series(df.loc[:cut])
    pd.testing.assert_series_equal(full.loc[:cut], part)


def test_nachrechnung_schreibt_wktrend():
    df = _frame()

    class FakeFt:
        def load_price_data(self, symbol, period, interval):
            return df.copy()

        def init_instance(self, name, df, symbol):
            raise AssertionError('trend is not an indicator module')

    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, wkTrend REAL)')
    dates = [d.strftime('%Y-%m-%d 00:00:00') for d in df.index[-40:]]
    con.executemany('INSERT INTO asset_simulation (ticker, Date) VALUES (?, ?)', [('T', d) for d in dates])
    assert asset_perf2._backfill_symbol('T', ['trend'], con, 'asset_simulation', '', FakeFt()) == 40
    got = [r[0] for r in con.execute('SELECT wkTrend FROM asset_simulation ORDER BY Date')]
    np.testing.assert_allclose(got, asset_perf2.wk_trend_series(df).iloc[-40:].to_numpy())


def test_formel_mit_wktrend_laedt_keinen_indikator_trend():
    from tradinglib.fetch_data import indicators_for_expressions
    assert indicators_for_expressions(['(wkTrend > 0) & (dTrend > 0)']) == []
