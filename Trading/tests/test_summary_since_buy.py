"""Asset summary 'from buy date only': position starts, chart period, zoom start."""
import pandas as pd

from tradinglib import summary_sources as ss
from tradinglib.tiny_chart_grid import period_since


def test_position_start_is_the_buy_that_reopened_it():
    df = pd.DataFrame({
        'ticker': ['A', 'A', 'A', 'A', 'B', 'B'],
        'action': ['buy', 'sell', 'buy', 'buy', 'buy', 'sell'],
        'shares': [10, 10, 5, 5, 3, 1],
        'timestamp': ['2024-01-02 10:00:00', '2024-03-01 10:00:00', '2025-06-10 09:00:00',
                      '2025-07-01 09:00:00', '2026-02-02 12:00:00', '2026-03-03 12:00:00'],
    })
    out = ss._position_starts(df, 'ticker', 'action', 'shares', 'timestamp')
    assert out['A'] == pd.Timestamp('2025-06-10 09:00:00')     # not the first round trip, not the top-up
    assert out['B'] == pd.Timestamp('2026-02-02 12:00:00')     # partly sold, still open


def test_multi_strategy_tickers_and_starts(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv('TradingDB', str(tmp_path))
    year = pd.Timestamp.today().year
    con = sqlite3.connect(tmp_path / f'trades{year}.db')
    con.execute('CREATE TABLE trades (ticker, buyDate, sellVolume)')
    con.executemany('INSERT INTO trades VALUES (?, ?, ?)', [
        ('A', f'{year}-02-01 00:00:00', 10),       # closed
        ('A', f'{year}-05-01 00:00:00', None),     # open
        ('A', f'{year}-06-01 00:00:00', None),     # open in a second strategy
        ('B', f'{year}-03-01 00:00:00', 5),        # only closed
    ])
    con.commit()
    con.close()
    assert ss.multi_strategy_tickers() == ['A']
    assert ss.multi_strategy_tickers(open_only=False) == ['A', 'B']
    starts = ss.multi_strategy_starts()
    assert starts['A'] == pd.Timestamp(f'{year}-05-01')      # earliest open buy
    assert starts['B'] == pd.Timestamp(f'{year}-03-01')      # last buy of a closed one


def test_position_start_reads_utc_fill_times():
    df = pd.DataFrame({'symbol': ['X'], 'side': ['buy'], 'qty': [1],
                       'transaction_time': ['2026-11-04T14:42:45.43Z']})
    out = ss._position_starts(df, 'symbol', 'side', 'qty', 'transaction_time')
    assert out['X'] == pd.Timestamp('2026-11-04 14:42:45.43')


def test_period_covers_the_buy_date():
    today = '2026-11-04'
    assert period_since('2026-10-20', '1mo', '1d', today) == ('1mo', '1d')     # already covered
    assert period_since('2026-06-01', '1mo', '1d', today) == ('6mo', '1d')
    assert period_since('2026-05-01', '1mo', '1d', today) == ('1y', '1d')       # 188 days > 6mo
    assert period_since('2021-01-01', '1y', '1d', today) == ('10y', '1d')
    assert period_since('2026-10-01', '1mo', '30m', today) == ('3mo', '30m')   # 30m reaches 59 days
    assert period_since('2026-01-01', '1mo', '30m', today) == ('1y', '1d')     # too far for 30m
    assert period_since('2025-01-01', '3mo', '60m', today) == ('2y', '60m')
    assert period_since('2026-01-01', 'max', '1d', today) == ('max', '1d')
    assert period_since(None, '1mo', '60m', today) == ('1mo', '60m')
    assert period_since('kein Datum', '1mo', '60m', today) == ('1mo', '60m')


class _Chart:
    """Just enough of tiny_chart for _x_bar_for."""
    ftime_str = '%Y-%m-%d %H:%M:%S'

    def __init__(self, dates):
        self.df = pd.DataFrame({'Date': dates})


def test_zoom_start_bar():
    from tradinglib.tiny_chart import tiny_chart
    c = _Chart(['2026-10-01 00:00:00', '2026-10-02 00:00:00', '2026-10-05 00:00:00'])
    bar = tiny_chart._x_bar_for
    assert bar(c, pd.Timestamp('2026-10-02 15:30')) == '2026-10-02 00:00:00'   # the bar the buy is in
    assert bar(c, pd.Timestamp('2026-09-01')) == '2026-10-01 00:00:00'         # before the chart
    assert bar(c, pd.Timestamp('2026-10-06')) is None                          # after the last bar
