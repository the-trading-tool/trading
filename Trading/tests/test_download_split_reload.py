"""Download pipeline: a split in the provider's adjusted history triggers a full reload."""
import os
import sqlite3

import pandas as pd

from tradinglib import ticker_tools as tt


def _saver(tmp_path, monkeypatch, stored):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    con = sqlite3.connect(tmp_path / 'yf_T.db')
    con.execute('CREATE TABLE day_data (Date TEXT PRIMARY KEY, Open REAL, High REAL, Low REAL, Close REAL, Volume INTEGER)')
    con.executemany('INSERT INTO day_data VALUES (?, ?, ?, ?, ?, ?)',
                    [(f'{d} 00:00:00', c, c, c, c, 100) for d, c in stored])
    con.commit()
    con.close()
    return tt.StockDataSaver('T')


def _frame(rows):
    idx = pd.DatetimeIndex([d for d, _ in rows])
    c = [v for _, v in rows]
    return pd.DataFrame({'Open': c, 'High': c, 'Low': c, 'Close': c, 'Volume': [100] * len(c)}, index=idx)


STORED = [('2026-09-01', 0.16), ('2026-09-02', 0.17), ('2026-09-03', 0.16), ('2026-09-04', 0.15)]


def test_stale_scale_detects_split_and_ignores_normal_moves(tmp_path, monkeypatch):
    s = _saver(tmp_path, monkeypatch, STORED)
    assert s._stale_scale(_frame([(d, c * 80) for d, c in STORED])) == 80.0
    assert s._stale_scale(_frame([(d, c * 1.05) for d, c in STORED])) is None
    assert s._stale_scale(_frame([('2026-10-01', 12.0)])) is None          # no overlap
    s.conn.close()


def test_split_triggers_full_reload(tmp_path, monkeypatch):
    s = _saver(tmp_path, monkeypatch, [('2026-08-03', 0.20)] + STORED)
    window = _frame([(d, c * 80) for d, c in STORED])
    full = _frame([('2026-08-03', 16.0)] + [(d, c * 80) for d, c in STORED])
    calls = []

    def fetch(interval, period='max', force_remote=False):
        calls.append((interval, period))
        if period == 'max':
            return full if interval == '1d' else None
        return window
    monkeypatch.setattr(s, 'fetch_data_dl', fetch)
    monkeypatch.setattr('tradinglib.ticker_tools.time.sleep', lambda *_: None)
    assert s.save_all_intervals(intervals=['1d'], periods=['1mo']) == []
    assert ('1d', 'max') in calls
    closes = [r[0] for r in s.conn.execute('SELECT Close FROM day_data ORDER BY Date')]
    s.conn.close()
    assert [round(c, 6) for c in closes] == [16.0, 12.8, 13.6, 12.8, 12.0]  # one scale throughout
