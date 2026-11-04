"""Mark-to-market curves for 'Backtest vs. Paper Trading' (tradinglib.compare_mtm)."""
import numpy as np
import pandas as pd
import pytest

from tradinglib import compare_mtm as cm

DAYS = pd.bdate_range('2026-06-01', periods=6)       # Mon 1 .. Mon 8 June


def _panel(**cols):
    return pd.DataFrame(cols, index=DAYS)


def test_backtest_open_position_is_marked_daily_not_dumped_on_the_last_day():
    panel = _panel(A=[10, 11, 12, 9, 8, 8.0])
    trades = pd.DataFrame([{'ticker': 'A', 'buyDate': '2026-06-01', 'sellDate': '2026-06-08',
                            'buyValue': -1000.0, 'sellVolume': None, 'gain': -200.0}])
    curve = cm.backtest_curve(trades, panel)
    assert curve.round(2).tolist() == [0, 100, 200, -100, -200, -200]


def test_backtest_closed_trade_books_its_realised_gain_from_the_sell_day():
    panel = _panel(A=[10, 12, 15, 15, 15, 15.0])
    trades = pd.DataFrame([{'ticker': 'A', 'buyDate': '2026-06-01', 'sellDate': '2026-06-03',
                            'buyValue': -1000.0, 'sellVolume': 100, 'gain': 497.0}])
    curve = cm.backtest_curve(trades, panel)
    assert curve.round(2).tolist() == [0, 200, 497, 497, 497, 497]


def test_backtest_without_prices_keeps_the_realised_gain():
    trades = pd.DataFrame([{'ticker': 'NOPRICE', 'buyDate': '2026-06-01', 'sellDate': '2026-06-03',
                            'buyValue': -500.0, 'sellVolume': 5, 'gain': 50.0}])
    curve = cm.backtest_curve(trades, _panel(A=[1.0] * 6))
    assert curve.tolist() == [0, 0, 50, 50, 50, 50]


def test_paper_pieces_fifo_and_curve():
    fills = pd.DataFrame([
        {'activity_type': 'FILL', 'symbol': 'BRK.B', 'side': 'buy', 'qty': 10, 'price': 100,
         'transaction_time': '2026-06-01T14:00:00Z'},
        {'activity_type': 'FILL', 'symbol': 'BRK.B', 'side': 'sell', 'qty': 4, 'price': 110,
         'transaction_time': '2026-06-03T15:00:00Z'},
    ])
    pieces = cm.paper_pieces(fills, {'BRK.B': 'BRK-B'})
    assert set(pieces['ticker']) == {'BRK-B'}
    assert sorted(pieces['qty'].tolist()) == [4, 6]
    curve = cm.paper_curve(pieces, _panel(**{'BRK-B': [100, 105, 110, 120, 90, 90.0]}))
    # day 1: 0; day 2: 10*5; day 3: sold 4 at +10 (40) + 6 held at +10 (60); day 4: 40 + 6*20
    assert curve.round(2).tolist() == [0, 50, 100, 160, -20, -20]


def test_paper_curve_without_prices_counts_held_lots_at_cost():
    pieces = pd.DataFrame([{'ticker': 'X', 'qty': 2, 'buy_time': pd.Timestamp('2026-06-02'),
                            'buy_price': 50.0, 'sell_time': pd.NaT, 'sell_price': np.nan}])
    assert cm.paper_curve(pieces, _panel(A=[1.0] * 6)).tolist() == [0.0] * 6


def test_load_closes_reads_text_dates_and_fills_gaps(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv('TradingDB', str(tmp_path))
    con = sqlite3.connect(tmp_path / 'yf_A.db')
    con.execute('CREATE TABLE day_data (Date TEXT, Close REAL)')
    con.executemany('INSERT INTO day_data VALUES (?, ?)',
                    [('2026-06-01 00:00:00', 10.0), ('2026-06-01 00:00:00', 10.5),   # duplicate day
                     ('2026-06-03 00:00:00', 12.0)])
    con.commit()
    con.close()
    panel = cm.load_closes(['A', 'MISSING'], '2026-06-01', db_path='database', until='2026-06-04')
    assert list(panel.columns) == ['A']
    assert panel.loc['2026-06-01', 'A'] == 10.5 and panel.loc['2026-06-02', 'A'] == 10.5
    assert panel.loc['2026-06-04', 'A'] == pytest.approx(12.0)
