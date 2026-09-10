"""Ausserboersliche Balken im Chart ausblenden (chart_extended_hours).

Die Quelle liefert fuer US-Werte ZWEI Serien in derselben Tabelle: die
regulaere Sitzung mit Volumen (bei AAPL 8.634 Balken, Zeitstempel auf :30) und
die vor-/nachboersliche ohne Volumen (8.200 Balken, auf :00). Ohne Begrenzung
zeigt der Chart dadurch 16 Stunden je Tag statt 6,5, und die duenne Nachboerse
enthaelt Fehldrucke (AAPL am 10.07.: High 331,78 bei einem Kurs um 315).

Statt eines zweiten Rangebreaks wird das Stunden-Histogramm auf Balken MIT
Volumen beschraenkt -- der bestehende Nachtbruch wird dadurch von allein zum
Komplement der regulaeren Sitzung.

Die Bedingung dafuer musste zweimal geschaerft werden, und die Tests halten
beide Faelle fest:
  * Reihen ganz ohne Volumen (Indizes) duerfen nicht gefiltert werden -- der
    Rahmen waere sonst leer.
  * Ein EINZELNER volumenloser Balken ist die Eroeffnungsauktion, keine zweite
    Serie. Bei SAP.DE, VOW.DE und AZN.L betrifft das genau eine Stunde (4-10 %
    der Zeilen); ohne die Mehr-Stunden-Bedingung verschwand die
    Eroeffnungsstunde aus dem Chart. Die echte Zweit-Serie ist unverkennbar
    groesser: AAPL 55 % ueber neun Stunden.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib.graph_tools import GraphTools


def _frame(spec, tage=30):
    """spec: Liste (stunde, minute, volumen) je Handelstag."""
    zeilen = []
    tag = pd.Timestamp('2026-06-01')          # Montag
    gezaehlt = 0
    while gezaehlt < tage:
        if tag.dayofweek < 5:
            for h, m, v in spec:
                zeilen.append({'Date': tag + pd.Timedelta(hours=h, minutes=m),
                               'Volume': v, 'Close': 100.0})
            gezaehlt += 1
        tag += pd.Timedelta(days=1)
    return pd.DataFrame(zeilen)


def _nachtbruch(df, extended=False):
    br = GraphTools().get_range_breaks_NewV3(df, exchange='', extended_hours=extended)
    return next((b['bounds'] for b in br if b.get('pattern') == 'hour'), None)


# US-Fall: regulaere Sitzung 15:30-21:30 mit Volumen, aussen herum ohne.
US = ([(h, 0, 0) for h in (10, 11, 12, 13, 14)]
      + [(h, 30, 1_000_000) for h in (15, 16, 17, 18, 19, 20, 21)]
      + [(h, 0, 0) for h in (22, 23)])
# Europaeischer Fall: nur die Eroeffnungsauktion ohne Volumen.
EU = [(9, 0, 0)] + [(h, 0, 500_000) for h in range(10, 18)]


def test_zweite_serie_wird_ausgeblendet():
    bounds = _nachtbruch(_frame(US))
    assert bounds is not None
    # Bruch beginnt nach dem letzten Balken MIT Volumen (21:30) und endet vor
    # dem ersten (15:30).
    assert bounds[0] >= 22.0 and bounds[1] <= 15.5


def test_eingeschaltet_bleiben_alle_balken():
    eng = _nachtbruch(_frame(US), extended=False)
    weit = _nachtbruch(_frame(US), extended=True)
    assert eng != weit
    assert weit[1] <= 10.0        # Bruch endet vor dem ersten vorboerslichen Balken


def test_eroeffnungsauktion_wird_nicht_weggefiltert():
    """Der Fall, der die erste Fassung falsch machte: genau EIN volumenloser
    Balken ist keine zweite Serie, sondern die Auktion."""
    eng = _nachtbruch(_frame(EU), extended=False)
    weit = _nachtbruch(_frame(EU), extended=True)
    assert eng == weit
    assert eng[1] <= 9.0          # 09:00 bleibt sichtbar


def test_reihe_ganz_ohne_volumen_bleibt_unberuehrt():
    """Indizes fuehren kein Volumen -- ein Filter wuerde den Rahmen leeren."""
    idx = [(h, 0, 0) for h in range(9, 18)]
    eng = _nachtbruch(_frame(idx), extended=False)
    weit = _nachtbruch(_frame(idx), extended=True)
    assert eng == weit and eng is not None


def test_reihe_ganz_mit_volumen_bleibt_unberuehrt():
    voll = [(h, 0, 500_000) for h in range(9, 18)]
    assert _nachtbruch(_frame(voll), False) == _nachtbruch(_frame(voll), True)


def test_fehlende_volumenspalte_bricht_nicht_ab():
    df = _frame(US).drop(columns=['Volume'])
    assert _nachtbruch(df, extended=False) is not None


# ------------------------------------------------------- Verdrahtung

def test_tiny_chart_reicht_den_schalter_durch():
    import inspect
    from tradinglib import tiny_chart
    src = inspect.getsource(tiny_chart)
    assert 'chart_extended_hours' in src
    assert 'extended_hours=_ext' in src


def test_vorgabe_ist_aus():
    """Ohne gesetzten Wert soll der Chart die reguläre Sitzung zeigen."""
    import inspect
    from tradinglib import tiny_chart
    src = inspect.getsource(tiny_chart)
    assert 'get_value("chart_extended_hours", False)' in src


def test_signatur_bleibt_rueckwaertskompatibel():
    """Aufrufer ohne den neuen Parameter duerfen sich nicht aendern muessen."""
    import inspect
    sig = inspect.signature(GraphTools.get_range_breaks)
    assert sig.parameters['extended_hours'].default is False
    assert GraphTools().get_range_breaks(_frame(EU)) is not None
