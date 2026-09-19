"""Kennzahlen-Seite: die Signalzeile muss die JUENGSTE sein.

AAD.DE fiel aus seinem Index, der Tageslauf rechnete es in asset_simulation_.db
nicht mehr (letzte Zeile 2026-08-07), asset_simulation_all.db dagegen schon.
Die Seite nahm die erste Datei mit irgendeiner Zeile und zeigte Stop-Loss 18,67
bei einem Kurs von 17,72 -- gerechnet aus einem Schlusskurs vom August.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib.headlines import Headlines


@pytest.fixture()
def dbdir(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    return tmp_path


def _sim(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, close REAL, stop_loss REAL)")
        conn.executemany("INSERT INTO asset_simulation VALUES (?,?,?,?)", rows)


def _row(symbol):
    h = Headlines.__new__(Headlines)
    h._sig_src = None
    return h._load_signal_row(symbol), h._sig_src


def test_juengere_zeile_aus_all_gewinnt(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [('AAD.DE', '2026-08-07 00:00:00', 22.55, 18.67)])
    _sim(dbdir / 'asset_simulation_all.db', [('AAD.DE', '2026-09-18 00:00:00', 17.72, 16.48)])
    row, src = _row('AAD.DE')
    assert row['stop_loss'] == 16.48 and src == 'asset_simulation_all.db'


def test_bei_gleichem_datum_bleibt_die_jahres_db(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [('SAP.DE', '2026-09-18 00:00:00', 180.0, 170.0)])
    _sim(dbdir / 'asset_simulation_all.db', [('SAP.DE', '2026-09-18 00:00:00', 180.0, 169.0)])
    row, src = _row('SAP.DE')
    assert src == 'asset_simulation_.db' and row['stop_loss'] == 170.0


def test_nur_in_all_vorhanden(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [('X', '2026-09-18 00:00:00', 1.0, 0.9)])
    _sim(dbdir / 'asset_simulation_all.db', [('^GDAXI', '2026-09-18 00:00:00', 25000.0, 24000.0)])
    row, src = _row('^GDAXI')
    assert src == 'asset_simulation_all.db' and row['close'] == 25000.0


def test_nirgends_vorhanden(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [])
    assert _row('NOPE') == ({}, None)
