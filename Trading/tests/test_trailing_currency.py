"""Tests fuer die Waehrungsbehandlung im Trailing-Stop-Management.

Hintergrund: der Einstandskurs kommt aus trades.db in der ABRECHNUNGS-Waehrung
(Scalable bucht EUR, auch bei einem LSE-Listing), der aktuelle Kurs und der ATR
dagegen in der LISTING-Waehrung. Ohne Umrechnung verglich der Stop Pence mit
Euro -- AAF.L zeigte Einstand 4,11 gegen Kurs 333,80 und +8021 % Gewinn.

Self-contained: temporaere SQLite-Dateien, FX gemockt, kein Netz.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib import own_trades_analysis as ota


# ------------------------------------------------------------------ Umrechnung
def test_gleiche_waehrung_keine_umrechnung():
    assert ota._native_to_system_rate('EUR', 'EUR') == 1.0


def test_unbekannte_waehrung_bleibt_unveraendert():
    """Ohne Angabe gilt die alte Annahme (schon Systemwaehrung) -- richtig fuer
    den reinen EUR-Fall und nicht schlechter als vorher."""
    assert ota._native_to_system_rate('', 'EUR') == 1.0
    assert ota._native_to_system_rate(None, 'EUR') == 1.0


def test_pence_wird_umgerechnet(monkeypatch):
    """GBp -> EUR: der Faktor kommt aus DataUtils (dort GBP * 100)."""
    from tradinglib.utils import DataUtils
    monkeypatch.setattr(DataUtils, 'get_exchange_rate',
                        classmethod(lambda cls, symbol='', system_currency='', **kw: 85.5))
    rate = ota._native_to_system_rate('GBp', 'EUR')
    assert rate == pytest.approx(85.5)
    assert 333.80 / rate == pytest.approx(3.90, abs=0.02)


def test_fehlender_kurs_gibt_none_statt_eins(monkeypatch):
    """Der entscheidende Punkt: ein Fallback auf 1,0 wuerde den Fehler still
    wiederherstellen. None zwingt den Aufrufer, die Position auszulassen."""
    from tradinglib.utils import DataUtils
    monkeypatch.setattr(DataUtils, 'get_exchange_rate',
                        classmethod(lambda cls, **kw: 0))
    assert ota._native_to_system_rate('GBp', 'EUR') is None

    def _boom(cls, **kw):
        raise RuntimeError('kein Netz')
    monkeypatch.setattr(DataUtils, 'get_exchange_rate', classmethod(_boom))
    assert ota._native_to_system_rate('USD', 'EUR') is None


def test_kaputter_kurswert_gibt_none(monkeypatch):
    from tradinglib.utils import DataUtils
    monkeypatch.setattr(DataUtils, 'get_exchange_rate',
                        classmethod(lambda cls, **kw: 'n/a'))
    assert ota._native_to_system_rate('USD', 'EUR') is None
    monkeypatch.setattr(DataUtils, 'get_exchange_rate',
                        classmethod(lambda cls, **kw: -3))
    assert ota._native_to_system_rate('USD', 'EUR') is None


# -------------------------------------------------------------- HWM-Migration
def _old_table(path):
    """Die Tabelle so anlegen, wie sie vor dem Fix aussah (ohne currency)."""
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE own_trades_trails (
            ticker TEXT PRIMARY KEY, entry_price REAL, high_water_mark REAL,
            trail_stop REAL, atr REAL, atr_mult REAL, last_price REAL,
            breached INTEGER DEFAULT 0, updated_at TEXT)""")
        conn.execute("INSERT INTO own_trades_trails VALUES "
                     "('AAF.L',4.11,333.80,313.73,8.03,2.5,333.80,0,'alt')")


def test_migration_ergaenzt_spalte(tmp_path):
    p = str(tmp_path / 'trades.db')
    _old_table(p)
    with sqlite3.connect(p) as conn:
        ota._ensure_own_trades_trails_table(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(own_trades_trails)")}
    assert 'currency' in cols


def test_alte_marke_in_pence_wird_verworfen(tmp_path):
    """Der High-Water-Mark steigt nur. Eine in Pence geschriebene Marke wuerde
    sonst fuer immer stehenbleiben und den Stop unerreichbar machen."""
    p = str(tmp_path / 'trades.db')
    _old_table(p)
    with sqlite3.connect(p) as conn:
        ota._ensure_own_trades_trails_table(conn)
        got = ota._read_prev_hwm(conn, 'AAF.L', 'EUR', entry=4.11)
    assert got == 4.11          # Einstand als Saat, nicht 333.80


def test_marke_in_systemwaehrung_bleibt(tmp_path):
    p = str(tmp_path / 'trades.db')
    with sqlite3.connect(p) as conn:
        ota._ensure_own_trades_trails_table(conn)
        conn.execute("INSERT INTO own_trades_trails "
                     "(ticker, entry_price, high_water_mark, currency) "
                     "VALUES ('AAF.L', 4.11, 4.50, 'EUR')")
        got = ota._read_prev_hwm(conn, 'AAF.L', 'EUR', entry=4.11)
    assert got == 4.50


def test_marke_aus_anderer_systemwaehrung_wird_verworfen(tmp_path):
    """Wer die Systemwaehrung umstellt, darf keine Marken der alten erben."""
    p = str(tmp_path / 'trades.db')
    with sqlite3.connect(p) as conn:
        ota._ensure_own_trades_trails_table(conn)
        conn.execute("INSERT INTO own_trades_trails "
                     "(ticker, entry_price, high_water_mark, currency) "
                     "VALUES ('AAF.L', 4.11, 4.50, 'USD')")
        got = ota._read_prev_hwm(conn, 'AAF.L', 'EUR', entry=4.11)
    assert got == 4.11


def test_ohne_zeile_gilt_der_einstand(tmp_path):
    p = str(tmp_path / 'trades.db')
    with sqlite3.connect(p) as conn:
        ota._ensure_own_trades_trails_table(conn)
        assert ota._read_prev_hwm(conn, 'NEU', 'EUR', entry=12.5) == 12.5


# ------------------------------------------------------------ Rechnung gesamt
def test_stop_liegt_unter_dem_kurs_nach_umrechnung(monkeypatch):
    """Durchgerechnet mit den echten AAF.L-Zahlen: 333,80 GBp / 85,5 = 3,90 EUR,
    ATR 8,03 GBp = 0,094 EUR -> Stop 3,90 - 2,5*0,094 = 3,67."""
    from tradinglib.utils import DataUtils
    monkeypatch.setattr(DataUtils, 'get_exchange_rate',
                        classmethod(lambda cls, **kw: 85.5))
    rate = ota._native_to_system_rate('GBp', 'EUR')
    current = 333.80 / rate
    atr = 8.03 / rate
    entry = 4.11
    hwm = max(entry, current)
    stop = hwm - 2.5 * atr
    assert current == pytest.approx(3.904, abs=0.01)
    assert stop == pytest.approx(3.876, abs=0.01)
    assert stop < entry          # plausibel: Stop unter dem Einstand
    # und ohne Umrechnung waere es grotesk gewesen:
    assert (333.80 / entry - 1) * 100 > 8000
