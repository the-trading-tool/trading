"""Candidates prefilter with time-series syntax (shift/rolling).

The prefilter runs as SQL against asset_simulation; SQLite has no
``x.shift(1)`` and answered 'near "(": syntax error'. Such formulas are now
evaluated per ticker in pandas. Pinned here: both window modes, no bleeding of
shift() across tickers, case-insensitive column names like the SQL path, and
unknown columns reported in SQLite's wording (the page turns that into a hint).
"""
import sqlite3

import pandas as pd
import pytest

from tradinglib import candidates as cand


def _make_db(path):
    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=30)
    rows = []
    for tk, closes in (
        # A: dips to yesterday's support on the last day
        ('A', [100.0] * 29 + [90.5]),
        # B: same last close, but its support sits far below
        ('B', [100.0] * 29 + [90.5]),
    ):
        for d, c in zip(days, closes):
            sup = 90.0 if tk == 'A' else 50.0
            rows.append((tk, d.strftime('%Y-%m-%d 00:00:00'), c, c - 1, sup, 1.5, 1.0))
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, close REAL, '
                'Open REAL, sup_support REAL, relvol_ratio REAL, sortino REAL)')
    con.executemany('INSERT INTO asset_simulation VALUES (?,?,?,?,?,?,?)', rows)
    con.commit()
    con.close()
    info = sqlite3.connect(path.parent / 'asset_info.db')
    info.execute('CREATE TABLE asset_info (ticker TEXT, longName TEXT, shortName TEXT, '
                 'sector TEXT, currency TEXT)')
    info.executemany('INSERT INTO asset_info VALUES (?,?,?,?,?)',
                     [('A', 'A', 'A', 'Tech', 'USD'), ('B', 'B', 'B', 'Tech', 'USD')])
    info.commit()
    info.close()
    tick = sqlite3.connect(path.parent / 'yf_tickers.db')
    tick.execute('CREATE TABLE stocks (Ticker TEXT, ISIN TEXT)')
    tick.commit()
    tick.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    monkeypatch.setattr(cand, 'settings', lambda username: {})
    _make_db(tmp_path / 'asset_simulation_all.db')
    return tmp_path


def _find(prefilter, window):
    frame, steps = cand.find('tester', 'database', prefilter=prefilter,
                             prefilter_window=window, trend_mode='off', retest=False,
                             use_rotation=False, use_rsc=False, with_signal=False,
                             max_per_sector=0, top_n=50, universe=[])
    return frame, steps


def test_detects_time_series_syntax():
    assert cand.needs_pandas('(close <= sup_support.shift(1) * 1.02)')
    assert cand.needs_pandas('close > High.rolling(20).max()')
    assert not cand.needs_pandas('(relvol_ratio > 0.5) & (sup_resistance / sup_support > 1.03)')
    assert not cand.needs_pandas('close > 1.02')


@pytest.mark.parametrize('window', [1, 3])
def test_shift_prefilter_runs(db, window):
    f = r"(close<=sup_support.shift(1)*1.02)\n(close>open)"
    frame, steps = _find(f, window)
    assert steps[0].get('note_key') != 'note_error', steps[0]
    assert frame['ticker'].tolist() == ['A']


def test_multiline_sql_prefilter_window_one(db):
    # A raw multi-line text was handed to SQLite as is ("(a)\n(b)").
    frame, steps = _find("(relvol_ratio > 1)\n(close > Open)", 1)
    assert steps[0].get('note_key') != 'note_error', steps[0]
    assert sorted(frame['ticker']) == ['A', 'B']


def test_unknown_column_uses_sqlite_wording(db):
    frame, steps = _find("(ema200.shift(1) > close)", 1)
    assert frame.empty
    assert 'no such column: ema200' in steps[0]['note']


def test_shift_stays_inside_one_ticker(tmp_path):
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, close REAL)')
    con.executemany('INSERT INTO asset_simulation VALUES (?,?,?)', [
        ('A', '2026-01-01', 1.0), ('A', '2026-01-02', 2.0),
        ('B', '2026-01-01', 10.0), ('B', '2026-01-02', 11.0)])
    panel = cand._load_panel(con, 'close > close.shift(1)', '2026-01-01')
    m = cand._line_masks(panel, ['close > close.shift(1)'])[0]
    # First row of B must not compare against A's last close.
    assert m.tolist() == [False, True, False, True]
