"""Geschlossene Positionen duerfen im Auftragsprotokoll nicht offen bleiben.

Am 2026-06-09 schloss der Trailing Stop ADTN, APP, MU und STX. check_stoploss.py
schrieb die Verkaeufe mit qty=0, weil update_trails() keine Stueckzahl zurueckgab,
und der Broker-Abgleich aktualisierte nur Status, Preis und Zeitpunkt. Der
FIFO-Abgleich im Protokoll hielt die Kaeufe deshalb fuer offen.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

tb = pytest.importorskip('tradinglib.premium.trading_bridge')


@pytest.fixture
def log(tmp_path):
    lg = tb.OrderLog.__new__(tb.OrderLog)
    lg._db = str(tmp_path / 'trading.db')
    lg._ensure_table()          # legt broker_orders und broker_activities an
    return lg


def _order(conn, oid, action, qty, status='filled', ticker='ADTN'):
    conn.execute("INSERT INTO broker_orders (mode, broker, ticker, broker_symbol, action, "
                 "qty, order_id, status) VALUES ('paper','alpaca',?,?,?,?,?,?)",
                 (ticker, ticker, action, qty, oid, status))


def _qty(log, oid):
    with sqlite3.connect(log._db) as conn:
        return conn.execute("SELECT qty FROM broker_orders WHERE order_id=?", (oid,)).fetchone()[0]


def test_menge_kommt_aus_den_fill_aktivitaeten(log):
    with sqlite3.connect(log._db) as conn:
        _order(conn, 'b1', 'buy', 83)
        _order(conn, 's1', 'sell', 0.0)
        for q in (53, 24, 2, 4):          # Teilausfuehrungen wie bei ADTN
            conn.execute("INSERT INTO broker_activities (activity_type, qty, order_id) "
                         "VALUES ('FILL', ?, 's1')", (q,))
    assert log.repair_zero_qty_fills('paper', 'alpaca') == 1
    assert _qty(log, 's1') == 83


def test_brokermenge_hat_vorrang(log):
    with sqlite3.connect(log._db) as conn:
        _order(conn, 's1', 'sell', None)
    assert log.repair_zero_qty_fills(alpaca_by_id={'s1': {'filled_qty': 5.0}}) == 1
    assert _qty(log, 's1') == 5


def test_ohne_quelle_bleibt_die_zeile_unberuehrt(log):
    with sqlite3.connect(log._db) as conn:
        _order(conn, 's1', 'sell', 0.0)
    assert log.repair_zero_qty_fills() == 0
    assert _qty(log, 's1') == 0.0


def test_nicht_ausgefuehrte_und_korrekte_zeilen_bleiben(log):
    with sqlite3.connect(log._db) as conn:
        _order(conn, 'c1', 'sell', 0.0, status='canceled')
        _order(conn, 'f1', 'sell', 3.0)
        conn.execute("INSERT INTO broker_activities (activity_type, qty, order_id) "
                     "VALUES ('FILL', 9, 'c1'), ('FILL', 9, 'f1')")
    assert log.repair_zero_qty_fills() == 0
    assert _qty(log, 'c1') == 0.0 and _qty(log, 'f1') == 3.0


def test_fehlende_aktivitaetentabelle_bricht_nicht_ab(tmp_path):
    lg = tb.OrderLog.__new__(tb.OrderLog)
    lg._db = str(tmp_path / 'trading.db')
    lg._ensure_table()
    with sqlite3.connect(lg._db) as conn:
        conn.execute("DROP TABLE IF EXISTS broker_activities")
        _order(conn, 's1', 'sell', 0.0)
    assert lg.repair_zero_qty_fills() == 0


class _Broker:
    def get_latest_prices(self, symbols):
        return {s: 10.0 for s in symbols}

    def close_position(self, sym):
        raise AssertionError('darf ohne execute nicht schliessen')


def test_update_trails_gibt_die_stueckzahl_zurueck(log, monkeypatch):
    """check_stoploss.py protokolliert den Verkauf mit dieser Menge."""
    mon = tb.StopLossMonitor.__new__(tb.StopLossMonitor)
    mon._log = log
    monkeypatch.setattr(mon, '_open_positions', lambda *a, **k: [
        {'ticker': 'MU', 'broker_symbol': 'MU', 'strategy': '', 'qty': 7.0,
         'entry_price': 12.0}], raising=False)
    monkeypatch.setattr(mon, '_get_atr', lambda *a, **k: 0.5, raising=False)
    res = mon.update_trails(_Broker(), mode='paper', broker_id='alpaca')
    assert res and res[0]['qty'] == 7.0


def test_check_stoploss_schreibt_keine_null_mehr():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'check_stoploss.py'), encoding='utf-8').read()
    assert "pos.get('qty', 0)" not in src
