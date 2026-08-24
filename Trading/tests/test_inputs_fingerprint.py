"""Tests fuer tools.inputs_fingerprint (Cache-Schluessel-Bestandteil).

Hintergrund: der Multi-Strategies-Ergebnis-Cache war auf (Config, Jahr, Datum,
Waehrung) gekeyt. Aenderte sich eine globale Einstellung wie signal_window oder
wurde die Simulations-DB neu geschrieben, blieb der Cache gueltig und die Seite
zeigte eine Rechnung unter alten Eingaben weiter. Der Fingerabdruck schliesst
genau diese Luecke -- deshalb muss er auf JEDE dieser Aenderungen reagieren.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib.tools import inputs_fingerprint


def test_gleiche_eingaben_gleicher_abdruck():
    a = inputs_fingerprint({'signal_window': 3, 'stop_loss_pct': 10.0})
    b = inputs_fingerprint({'stop_loss_pct': 10.0, 'signal_window': 3})
    assert a == b          # Reihenfolge im Dict darf nichts aendern


def test_geaenderte_einstellung_aendert_abdruck():
    a = inputs_fingerprint({'signal_window': 1})
    b = inputs_fingerprint({'signal_window': 3})
    assert a != b


def test_typwechsel_wird_bemerkt():
    """'3' und 3 kommen beide aus der Config -- sie duerfen nicht kollidieren,
    sonst haengt die Cache-Gueltigkeit daran, wie der Wert gespeichert wurde."""
    assert inputs_fingerprint({'w': 3}) != inputs_fingerprint({'w': '3'})


def test_false_und_null_unterscheidbar():
    assert inputs_fingerprint({'require_isin': False}) != inputs_fingerprint({'require_isin': 0})


def test_leere_eingabe():
    assert inputs_fingerprint() == ''
    assert inputs_fingerprint({}, files=()) == ''


def test_datei_geht_ein(tmp_path):
    p = tmp_path / 'sim.db'
    p.write_bytes(b'x' * 100)
    a = inputs_fingerprint({}, files=[str(p)])
    assert 'sim.db' in a
    assert '100' in a


def test_geaenderte_dateigroesse_aendert_abdruck(tmp_path):
    p = tmp_path / 'sim.db'
    p.write_bytes(b'x' * 100)
    a = inputs_fingerprint({}, files=[str(p)])
    p.write_bytes(b'x' * 200)
    assert inputs_fingerprint({}, files=[str(p)]) != a


def test_neu_geschrieben_bei_gleicher_groesse(tmp_path):
    """asset_perf2 schreibt die DB komplett neu -- die Groesse kann dabei gleich
    bleiben. Dann muss die Zeit den Ausschlag geben."""
    p = tmp_path / 'sim.db'
    p.write_bytes(b'x' * 100)
    a = inputs_fingerprint({}, files=[str(p)])
    later = time.time() + 120
    os.utime(p, (later, later))
    assert inputs_fingerprint({}, files=[str(p)]) != a


def test_fehlende_datei_ist_ein_zustand(tmp_path):
    p = tmp_path / 'weg.db'
    a = inputs_fingerprint({}, files=[str(p)])
    assert a.endswith('missing')
    p.write_bytes(b'x')
    assert inputs_fingerprint({}, files=[str(p)]) != a


def test_mehrere_dateien_bleiben_getrennt(tmp_path):
    p1, p2 = tmp_path / 'a.db', tmp_path / 'b.db'
    p1.write_bytes(b'x')
    p2.write_bytes(b'xx')
    got = inputs_fingerprint({}, files=[str(p1), str(p2)])
    assert 'a.db' in got and 'b.db' in got
