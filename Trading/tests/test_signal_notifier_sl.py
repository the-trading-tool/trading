"""Push-Meldung: Stop-Loss/Take-Profit aus der Zeile des Kauftags.

Der Kaufkurs ist der Schluss des Kauftags. Der Notifier las SL/TP aber aus der
juengsten Zeile von asset_simulation_all.db, die zur Sendezeit meist vom Vortag
stammt: seit Juni kam jeder Stop aus einer 1-4 Tage aelteren Zeile, 5-mal lag er
ueber dem Kaufkurs.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib import signal_notifier as sn


@pytest.fixture()
def dbdir(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    return tmp_path


def _sim(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, stop_loss REAL, "
                     "take_profit REAL, currency TEXT)")
        conn.executemany("INSERT INTO asset_simulation VALUES (?,?,?,?,?)", rows)


def test_zeile_des_kauftags_statt_der_juengsten(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [
        ('STM.DE', '2026-07-22 00:00:00', 15.02, 18.0, 'EUR'),
        ('STM.DE', '2026-07-23 00:00:00', 12.34, 16.1, 'EUR')])
    _sim(dbdir / 'asset_simulation_all.db', [
        ('STM.DE', '2026-07-22 00:00:00', 15.02, 18.0, 'EUR')])
    assert sn._load_sim('STM.DE', '2026-07-23')['stop_loss'] == 12.34


def test_jahres_db_vor_all(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [('X', '2026-09-14 00:00:00', 29.68, 40.0, 'EUR')])
    _sim(dbdir / 'asset_simulation_all.db', [('X', '2026-09-14 00:00:00', 30.0, 41.0, 'EUR')])
    assert sn._load_sim('X', '2026-09-14')['stop_loss'] == 29.68


def test_nur_in_all_am_kauftag(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [])
    _sim(dbdir / 'asset_simulation_all.db', [('SI=F', '2026-09-05 00:00:00', 30.0, 38.0, 'USD')])
    r = sn._load_sim('SI=F', '2026-09-05')
    assert r['stop_loss'] == 30.0 and r['currency'] == 'USD'


def test_ohne_zeile_am_kauftag_die_letzte_davor_nie_eine_spaetere(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [
        ('Y', '2026-09-10 00:00:00', 9.0, 12.0, 'EUR'),
        ('Y', '2026-09-20 00:00:00', 5.0, 7.0, 'EUR')])
    _sim(dbdir / 'asset_simulation_all.db', [])
    assert sn._load_sim('Y', '2026-09-12')['stop_loss'] == 9.0


def test_nichts_vorhanden(dbdir):
    _sim(dbdir / 'asset_simulation_.db', [])
    _sim(dbdir / 'asset_simulation_all.db', [])
    assert sn._load_sim('Z', '2026-09-12') == {}
