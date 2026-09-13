"""Erkennung von Stundenbalken, die als Kopie in Boersen-Ortszeit gespeichert wurden."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import repair_h60_local_copies as r

BERLIN = 'Europe/Berlin'


def _bar(ts, o, h, l, c, v):
    return (ts, o, h, l, c, v)


def test_kopie_mit_spanne_wird_erkannt():
    """Sommer: Berlin = UTC+2, die Kopie liegt zwei Stunden spaeter."""
    rows = [_bar('2026-07-10 08:00:00', 9, 11, 8, 10, 100),
            _bar('2026-07-10 09:00:00', 10, 12, 9, 11, 200),
            _bar('2026-07-10 11:00:00', 10, 12, 9, 11, 200)]
    assert [t[0] for t in r.find_copies(rows, BERLIN)] == ['2026-07-10 11:00:00']


def test_schlussauktion_flach_mit_gleichem_volumen_ist_kopie():
    """Der SAP.DE-Fall: Auktionsbalken 15:30 UTC, Kopie 17:30 UTC."""
    rows = [_bar('2026-07-21 15:00:00', 136.5, 136.6, 135.8, 136.2, 135104),
            _bar('2026-07-21 15:30:00', 136.42, 136.42, 136.42, 136.42, 639658),
            _bar('2026-07-21 17:30:00', 136.42, 136.42, 136.42, 136.42, 639658)]
    assert [t[0] for t in r.find_copies(rows, BERLIN)] == ['2026-07-21 17:30:00']


def test_flach_ohne_volumen_ist_keine_kopie():
    """Yahoo fuellt nach Handelsschluss mit Volumen-0-Balken auf dem Schlusskurs."""
    rows = [_bar('2026-01-02 16:00:00', 201.2, 201.8, 200.0, 201.95, 168222),
            _bar('2026-01-02 17:30:00', 201.95, 201.95, 201.95, 201.95, 0),
            _bar('2026-01-02 18:30:00', 201.95, 201.95, 201.95, 201.95, 0),
            _bar('2026-01-02 19:30:00', 201.95, 201.95, 201.95, 201.95, 0)]
    assert r.find_copies(rows, BERLIN) == []


def test_flach_mit_abweichendem_volumen_ist_keine_kopie():
    rows = [_bar('2026-07-21 15:30:00', 136.42, 136.42, 136.42, 136.42, 639658),
            _bar('2026-07-21 17:30:00', 136.42, 136.42, 136.42, 136.42, 1000)]
    assert r.find_copies(rows, BERLIN) == []


def test_schwelle_gilt_je_art_getrennt():
    """Viele flache Treffer duerfen vereinzelte Treffer mit Spanne (Rauschen)
    nicht mit ueber die Schwelle ziehen."""
    flat = [_bar(f'2026-07-{d:02d} 17:30:00', 1, 1, 1, 1, 5) for d in range(1, 8)]
    span = [_bar('2026-07-01 11:00:00', 1, 2, 0.5, 1.5, 7)]
    out = r._above_threshold(flat + span)
    assert len(out) == 7 and all(t[2] == t[3] for t in out)
