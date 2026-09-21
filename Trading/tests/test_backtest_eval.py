"""Bewertungsbausteine des Strategy Finders (backtest_eval)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from tradinglib import backtest_eval as be


def _frame(prices: dict, start='2024-01-01'):
    dates = pd.bdate_range(start, periods=len(next(iter(prices.values()))))
    rows = []
    for tk, ps in prices.items():
        for d, p in zip(dates, ps):
            rows.append({'Date': d.strftime('%Y-%m-%d 00:00:00'), 'ticker': tk, 'close': p})
    return pd.DataFrame(rows), dates


def test_equity_markt_offene_und_bucht_geschlossene_positionen():
    df, dates = _frame({'A': [10, 11, 12, 12, 12], 'B': [20, 20, 20, 20, 20]})
    panel = be.close_panel(df)
    trades = pd.DataFrame([
        # A am Tag 0 fuer 1000 gekauft, am Tag 2 zu 12 verkauft: +200
        {'ticker': 'A', 'buyDate': dates[0], 'buyValue': -1000, 'sellDate': dates[2],
         'sellVolume': 100, 'gain': 200},
        # B am Tag 3 gekauft, offen
        {'ticker': 'B', 'buyDate': dates[3], 'buyValue': -500, 'sellDate': None,
         'sellVolume': None, 'gain': None},
    ])
    eq, inv = be.mtm_equity(trades, panel, 10_000)
    assert list(eq.round(2)) == [10_000, 10_100, 10_200, 10_200, 10_200]
    assert inv.iloc[1] == pytest.approx(1100) and inv.iloc[2] == 0 and inv.iloc[4] == 500


def test_waehrung_kuerzt_sich():
    """Positionswert ueber das Kursverhaeltnis: Pence-Kurse aendern nichts."""
    df, dates = _frame({'X.L': [1000, 1100]})
    trades = pd.DataFrame([{'ticker': 'X.L', 'buyDate': dates[0], 'buyValue': -50,
                            'sellDate': None, 'sellVolume': None, 'gain': None}])
    eq, _ = be.mtm_equity(trades, be.close_panel(df), 100)
    assert eq.iloc[-1] == pytest.approx(105)


def test_buy_and_hold_und_gleichgewichtet():
    df, dates = _frame({'A': [10, 11], 'B': [10, 9]})
    bh = be.buy_and_hold(pd.Series([100.0, 110.0], index=dates), dates, 1000)
    assert list(bh) == [1000, 1100]
    ew = be.equal_weight(be.close_panel(df), 1000)
    assert ew.iloc[-1] == pytest.approx(1000)          # +10 % und -10 % im Mittel


def test_gleichgewichtet_kappt_datenfehler():
    df, _ = _frame({'A': [10, 10], 'B': [10, 1000]})    # Split-Artefakt x100
    ew = be.equal_weight(be.close_panel(df), 1000)
    assert ew.iloc[-1] <= 1250


def test_stats():
    idx = pd.bdate_range('2024-01-01', periods=3)
    s = be.stats(pd.Series([100.0, 80.0, 120.0], index=idx))
    assert s['total'] == pytest.approx(20) and s['max_dd'] == pytest.approx(-20)


def test_jahre_verketten_sich_zum_gesamtergebnis():
    idx = pd.to_datetime(['2023-06-01', '2023-12-29', '2024-06-03', '2024-12-31'])
    e = pd.Series([100.0, 110.0, 99.0, 121.0], index=idx)
    y = be.yearly({'strategy': e})
    assert y.loc[2023, 'strategy'] == pytest.approx(10)
    assert y.loc[2024, 'strategy'] == pytest.approx(10)
    assert (1 + y['strategy'] / 100).prod() == pytest.approx(1.21)
    assert y.loc[2024, 'max_dd'] == pytest.approx(-10)


def test_jahre_mit_trades():
    idx = pd.to_datetime(['2024-01-02', '2024-12-31'])
    trades = pd.DataFrame([
        {'ticker': 'A', 'buyDate': '2024-01-02', 'buyValue': -1, 'sellDate': '2024-03-01', 'sellVolume': 1, 'gain': 5},
        {'ticker': 'B', 'buyDate': '2024-02-02', 'buyValue': -1, 'sellDate': '2024-04-01', 'sellVolume': 1, 'gain': -5},
        {'ticker': 'C', 'buyDate': '2024-05-02', 'buyValue': -1, 'sellDate': None, 'sellVolume': None, 'gain': None},
    ])
    y = be.yearly({'s': pd.Series([1.0, 2.0], index=idx)}, trades)
    assert y.loc[2024, 'buys'] == 3 and y.loc[2024, 'win_rate'] == pytest.approx(50)


def test_split_trennt_lern_und_pruefzeitraum():
    idx = pd.bdate_range('2024-01-01', periods=4)
    e = pd.Series([100.0, 150.0, 150.0, 135.0], index=idx)
    s = be.split({'s': e}, idx[2])
    assert s.loc['s', 'train_total'] == pytest.approx(50)
    assert s.loc['s', 'test_total'] == pytest.approx(-10)


def test_perzentil():
    assert be.percentile_rank(5, [1, 2, 3, 4]) == 100
    assert be.percentile_rank(2, [1, 2, 3, 4]) == pytest.approx(37.5)
    assert np.isnan(be.percentile_rank(1, []))


def test_konsistenz_zwischen_lern_und_pruefzeitraum():
    good = pd.DataFrame({'train%': [1, 2, 3, 4, 5], 'test%': [2, 4, 6, 8, 10]})
    c = be.train_test_consistency(good)
    assert c['rank_corr'] == pytest.approx(1) and c['best_train_test_pct'] == 90
    bad = pd.DataFrame({'train%': [1, 2, 3, 4, 5], 'test%': [10, 8, 6, 4, 2]})
    assert be.train_test_consistency(bad)['best_train_test_pct'] == 10
