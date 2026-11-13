"""Deleting a ticker clears its rows from the running year's simulation DBs only.

Past-year DBs keep them: they are the only record that a delisted ticker existed,
and without its price DB the rows cannot be recomputed.
"""
import json
import sqlite3
from pathlib import Path

from tradinglib import admin

ROOT = Path(__file__).resolve().parent.parent


def _sim(path, tickers):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, close REAL)')
    con.executemany('INSERT INTO asset_simulation VALUES (?, ?, ?)',
                    [(tk, f'2026-01-0{d}', 1.0) for tk in tickers for d in (1, 2)])
    con.commit()
    con.close()


def _count(path, ticker):
    con = sqlite3.connect(path)
    try:
        return con.execute('SELECT COUNT(*) FROM asset_simulation WHERE ticker=?', (ticker,)).fetchone()[0]
    finally:
        con.close()


def test_current_year_dbs_cleared_past_years_kept(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    _sim(tmp_path / 'asset_simulation_.db', ['DEAD', 'KEEP'])
    _sim(tmp_path / 'asset_simulation_all.db', ['DEAD', 'KEEP'])
    _sim(tmp_path / 'asset_simulation_2025.db', ['DEAD', 'KEEP'])
    # asset_simulation_etp.db missing on purpose: skipped, not created

    res = admin.delete_current_sim_rows(['DEAD', 'DEAD', ''])

    assert res == {'asset_simulation_.db': 2, 'asset_simulation_all.db': 2}
    assert _count(tmp_path / 'asset_simulation_.db', 'DEAD') == 0
    assert _count(tmp_path / 'asset_simulation_all.db', 'DEAD') == 0
    assert _count(tmp_path / 'asset_simulation_.db', 'KEEP') == 2
    assert _count(tmp_path / 'asset_simulation_2025.db', 'DEAD') == 2
    assert not (tmp_path / 'asset_simulation_etp.db').exists()


def test_nothing_to_delete_gives_no_message(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    _sim(tmp_path / 'asset_simulation_.db', ['KEEP'])
    assert admin.delete_current_sim_rows([]) == {}
    assert admin.sim_cleanup_message(['GONE']) == (None, None)


def test_message_and_locale_keys(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    _sim(tmp_path / 'asset_simulation_etp.db', ['ETF1'])
    ok, err = admin.sim_cleanup_message(['ETF1'])
    assert err is None and ok and 'asset_simulation_etp.db' in ok
    for lang in ('de', 'en'):
        d = json.loads((ROOT / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        assert '{count}' in d['admin.sim_rows_deleted'] and '{dbs}' in d['admin.sim_rows_deleted']
