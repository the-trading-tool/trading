"""Batch download: one provider call for many tickers, shaped like the single path."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from tradinglib import cli
from tradinglib import ticker_tools as tt


def _multi(frames):
    """yf.download-shaped answer: columns (Price, Ticker), union index, NaN where a ticker had no bar."""
    parts = {}
    for ticker, df in frames.items():
        for col in ('Adj Close', 'Close', 'High', 'Low', 'Open', 'Volume'):
            src = 'Close' if col == 'Adj Close' else col
            parts[(col, ticker)] = df[src]
    out = pd.DataFrame(parts)
    out.columns = pd.MultiIndex.from_tuples(out.columns, names=['Price', 'Ticker'])
    return out


def _ohlc(dates, base):
    idx = pd.DatetimeIndex(dates)
    c = np.arange(len(idx), dtype=float) + base
    return pd.DataFrame({'Open': c, 'High': c + 1, 'Low': c - 1, 'Close': c, 'Volume': 100.0}, index=idx)


def test_window_is_shared_with_single_path():
    planner = tt.StockDataSaver.__new__(tt.StockDataSaver)
    today = datetime.now()
    assert planner.download_window('1m', '2d') == ('1m', (today - timedelta(days=2)).strftime('%Y-%m-%d'))
    # 60m is asked as 1h, and 2y is clamped to Yahoo's intraday limit
    assert planner.download_window('60m', '2y') == ('1h', (today - timedelta(days=729)).strftime('%Y-%m-%d'))
    assert planner.download_window('1d', 'max') == ('1d', (today - timedelta(days=20 * 365)).strftime('%Y-%m-%d'))


def test_batch_splits_tickers_and_drops_foreign_rows(monkeypatch):
    us = _ohlc(['2026-11-02 14:30', '2026-11-02 14:31'], 10)
    eu = _ohlc(['2026-11-02 08:00', '2026-11-02 08:01', '2026-11-02 14:30'], 50)
    calls = []

    def fake_download(**kw):
        calls.append(kw)
        return _multi({'US': us, 'EU': eu})
    monkeypatch.setattr(tt.md, 'download', fake_download)

    got = tt.batch_download(['US', 'EU', 'GONE'], '1m', '2d')

    assert len(calls) == 1 and calls[0]['tickers'] == ['US', 'EU', 'GONE']
    assert calls[0]['interval'] == '1m' and calls[0]['force_remote'] is True
    assert set(got) == {'US', 'EU'}                      # missing ticker left to the single path
    assert list(got['US'].index) == list(us.index)       # EU-only bars are not US rows
    assert list(got['EU'].index) == list(eu.index)
    assert list(got['US'].columns) == ['Close', 'High', 'Low', 'Open', 'Volume']
    assert got['US']['Close'].tolist() == us['Close'].tolist()


def test_single_path_keeps_rows_as_sent(monkeypatch):
    df = _ohlc(['2026-11-02', '2026-11-03'], 10)
    df.loc[df.index[1], ['Open', 'High', 'Low', 'Close']] = np.nan
    monkeypatch.setattr(tt.md, 'download', lambda **kw: _multi({'T': df}))
    s = tt.StockDataSaver.__new__(tt.StockDataSaver)
    s.ticker, s.tz_ready = 'T', ''
    assert len(s.fetch_data_dl('1d', '1mo')) == 2         # unchanged: no row dropped here


def test_save_all_intervals_uses_prefetched(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    sleeps, fetched = [], []
    monkeypatch.setattr('tradinglib.ticker_tools.time.sleep', lambda s: sleeps.append(s))
    s = tt.StockDataSaver('T')

    def fetch(interval, period='max', force_remote=False):
        fetched.append(interval)
        return _ohlc(['2026-11-02 10:00'], 1)
    monkeypatch.setattr(s, 'fetch_data_dl', fetch)

    pre = {'1m': _ohlc(['2026-11-02 10:00', '2026-11-02 10:01'], 5)}
    failed = s.save_all_intervals(intervals=['1m', '60m'], periods=['2d', '2d'], prefetched=pre)

    assert failed == []
    assert fetched == ['60m']                 # only the interval the batch did not bring
    assert sleeps == [0.8]                    # pause only after the single request
    assert s.conn.execute('SELECT COUNT(*) FROM min_data').fetchone()[0] == 2
    s.conn.close()


def test_prefetched_empty_counts_as_no_data(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    monkeypatch.setattr('tradinglib.ticker_tools.time.sleep', lambda s: None)
    s = tt.StockDataSaver('T')
    monkeypatch.setattr(s, 'fetch_data_dl', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no request')))
    assert s.save_all_intervals(intervals=['1m'], periods=['2d'], prefetched={'1m': pd.DataFrame()}) == ['1m']
    s.conn.close()


def test_cli_batch_flag():
    assert cli.parse_args(['x', '/batch:20'])['batch'] == 20
    assert cli.parse_args(['x', '/batch:0'])['batch'] == 1
    assert cli.parse_args(['x'])['batch'] == 0
