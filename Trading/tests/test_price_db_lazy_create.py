"""StockDataSaver creates yf_<ticker>.db only on the first write.

Opening it up front left a 4 KB file without tables for every ticker the
provider did not serve; readers then failed with "no such table: day_data".
"""
import sqlite3

import pandas as pd
import pytest

from tradinglib import ticker_tools as tt


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    monkeypatch.setattr('tradinglib.ticker_tools.time.sleep', lambda s: None)
    return tmp_path


def _bars(n=3):
    idx = pd.bdate_range('2026-11-02', periods=n)
    return pd.DataFrame({'Open': 1.0, 'High': 2.0, 'Low': 0.5, 'Close': 1.5, 'Volume': 10}, index=idx)


def test_unserved_ticker_leaves_no_file(env, monkeypatch):
    s = tt.StockDataSaver('DEAD')
    monkeypatch.setattr(s, 'fetch_data_dl', lambda *a, **k: pd.DataFrame())
    failed = s.save_all_intervals(intervals=['1m', '60m', '1d', '1mo'], periods=['7d', '60d', 'max', 'max'])
    s.close_connection()
    assert failed == ['1m', '60m', '1d', '1mo']
    assert not (env / 'yf_DEAD.db').exists()


def test_raising_download_leaves_no_file(env, monkeypatch):
    s = tt.StockDataSaver('DEAD')
    monkeypatch.setattr(s, 'fetch_data_dl', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('404')))
    assert s.save_all_intervals(intervals=['1d'], periods=['max']) == ['1d']
    s.close_connection()
    assert not (env / 'yf_DEAD.db').exists()


def test_first_save_creates_the_file(env, monkeypatch):
    s = tt.StockDataSaver('NEW')
    monkeypatch.setattr(s, 'fetch_data_dl', lambda *a, **k: _bars())
    assert s.save_all_intervals(intervals=['1d'], periods=['max']) == []
    s.close_connection()
    con = sqlite3.connect(env / 'yf_NEW.db')
    assert con.execute('SELECT COUNT(*) FROM day_data').fetchone()[0] == 3
    con.close()


def test_readers_do_not_create_the_file(env):
    s = tt.StockDataSaver('NONE')
    assert s._stale_scale(_bars()) is None
    assert s._daily_row_count() == 0
    assert s._backfill_daily_from_hourly() == 0
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        s.get_data('1d')
    s.close_connection()
    s.close_connection()                                # idempotent
    assert not (env / 'yf_NONE.db').exists()


def test_existing_file_is_read_and_extended(env, monkeypatch):
    s = tt.StockDataSaver('OLD')
    s.create_table('1d')
    s.save_to_db(_bars(2), '1d')
    s.conn.commit()
    s.close_connection()
    s = tt.StockDataSaver('OLD')
    assert s._daily_row_count() == 2
    assert len(s.get_data('1d')) == 2
    s.close_connection()


def test_stock_list_reader_leaves_no_blank_ticker_file(env):
    tt.StockDataSaver('')
    assert not (env / 'yf_.db').exists()
