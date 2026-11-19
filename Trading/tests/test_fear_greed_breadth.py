"""Fear & Greed breadth: current-year source on a fresh install."""
import datetime as dt
import sqlite3

import pandas as pd

from tradinglib import fear_greed as fg


def _tickers_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, Ticker TEXT);
        CREATE TABLE indices (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE stock_indices (stock_id INTEGER, index_id INTEGER);
        INSERT INTO stocks VALUES (1, 'AAA'), (2, 'BBB');
        INSERT INTO indices VALUES (1, '^TEST');
        INSERT INTO stock_indices VALUES (1, 1), (2, 1);
    """)
    con.commit()
    con.close()


def _sim_db(path, close):
    day = f"{dt.date.today().year}-01-05"
    df = pd.DataFrame({'ticker': ['AAA', 'BBB'], 'Date': day, 'close': close, 'sma200': 10.0,
                       'rsi': 60.0, 'markov_regime': 1, 'lyHigh': 20.0, 'lyLow': 5.0})
    con = sqlite3.connect(path)
    df.to_sql('asset_simulation', con, index=False)
    con.close()


def test_fresh_install_reads_current_year_db(tmp_path, monkeypatch):
    monkeypatch.setattr(fg, '_DB', str(tmp_path))
    _tickers_db(tmp_path / 'yf_tickers.db')
    _sim_db(tmp_path / 'asset_simulation_.db', [12.0, 8.0])     # no _all.db yet
    df = fg._breadth_timeseries('^TEST', start_year=dt.date.today().year)
    assert len(df) == 1 and df['above200'].iloc[0] == 0.5


def test_all_db_wins_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(fg, '_DB', str(tmp_path))
    _tickers_db(tmp_path / 'yf_tickers.db')
    _sim_db(tmp_path / 'asset_simulation_.db', [12.0, 8.0])
    _sim_db(tmp_path / 'asset_simulation_all.db', [12.0, 12.0])
    df = fg._breadth_timeseries('^TEST', start_year=dt.date.today().year)
    assert df['above200'].iloc[0] == 1.0


def test_seed_carries_fear_greed_inputs_in_a_downloaded_group():
    import seed_db
    from tradinglib.tools import NON_STOCK_GROUPS
    assert 'INDEX' in NON_STOCK_GROUPS           # --init loads it via /index
    for t in ('^VIX', '^VIX3M', 'TLT', 'HYG', 'LQD'):
        assert t in seed_db.INDEX, t
