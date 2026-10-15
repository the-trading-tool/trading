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


# --- Waehrung von Stop-Loss/Take-Profit (2026-10-15) --------------------------
#
# GS wurde als "Kurs: 785.24 EUR / Stop-Loss: 833.12 EUR" verschickt: der Kurs
# aus trades.db in EUR, Stop und Ziel aber die USD-Zahlen aus asset_simulation
# mit EUR-Label (833.12 USD = 743.14 EUR). Die Umrechnung im Notifier lief nie,
# weil weder run_notifier noch die CLI system_currency uebergeben haben.

BUY_ROW = {'ticker': 'GS', 'longName': 'The Goldman Sachs Group, Inc.',
           'Strategy': 'Support/RSI', 'stockIndex': '^SPX', 'currency': 'EUR',
           'buyPrice': 785.24, 'buyVolume': 2, 'buyValue': -1565.58}
SIM_USD = {'stop_loss': 833.12, 'take_profit': 953.84, 'currency': 'USD'}


def _lines(msg):
    return {line.split(':', 1)[0]: line.split(':', 1)[1].strip()
            for line in msg.splitlines() if ':' in line}


def test_sl_tp_werden_in_die_systemwaehrung_umgerechnet(monkeypatch):
    monkeypatch.setattr(sn.DataUtils, 'get_exchange_rate', lambda **kw: 1.1211)
    out = _lines(sn._build_buy_message(BUY_ROW, SIM_USD, '', system_currency='EUR'))
    assert out['Stop-Loss'] == '743.13 EUR'        # die App zeigt 743.14 bei ihrem Tageskurs
    assert out['Take-Profit'] == '850.81 EUR'


def test_ohne_umrechnung_steht_die_native_waehrung_dran(monkeypatch):
    monkeypatch.setattr(sn.DataUtils, 'get_exchange_rate', lambda **kw: 0)
    out = _lines(sn._build_buy_message(BUY_ROW, SIM_USD, '', system_currency='EUR'))
    assert out['Stop-Loss'] == '833.12 USD'        # nie "833.12 EUR"


def test_ohne_systemwaehrung_steht_die_native_waehrung_dran():
    out = _lines(sn._build_buy_message(BUY_ROW, SIM_USD, '', system_currency=''))
    assert out['Stop-Loss'] == '833.12 USD'


def test_gleiche_waehrung_bleibt_unveraendert(monkeypatch):
    monkeypatch.setattr(sn.DataUtils, 'get_exchange_rate',
                        lambda **kw: pytest.fail('keine Umrechnung noetig'))
    sim = {'stop_loss': 16.06, 'take_profit': 18.58, 'currency': 'EUR'}
    out = _lines(sn._build_buy_message(BUY_ROW, sim, '', system_currency='EUR'))
    assert out['Stop-Loss'] == '16.06 EUR'


def _config(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE config (key TEXT, value TEXT)")
        conn.executemany("INSERT INTO config VALUES (?,?)", rows)


def test_systemwaehrung_kommt_aus_der_config(dbdir):
    _config(dbdir / 'config.db', [('kurt:system_currency', '"EUR"'),
                                  ('admin:system_currency', '"USD"')])
    assert sn._system_currency('kurt') == 'EUR'
    assert sn._system_currency('admin') == 'USD'
    assert sn._system_currency('niemand') == ''
