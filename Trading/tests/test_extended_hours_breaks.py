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


def _breaks(df, extended=False):
    return GraphTools().get_range_breaks_NewV3(df, exchange='', extended_hours=extended)


def _sichtbar(df, extended=False):
    """Uhrzeiten (HH:MM) der Balken, die kein Rangebreak verdeckt."""
    dates = pd.to_datetime(df['Date'])
    verdeckt = pd.Series(False, index=df.index)
    for b in _breaks(df, extended):
        assert 'pattern' not in b          # nur Datumsgrenzen, kein Stundenmuster
        s, e = pd.Timestamp(b['bounds'][0]), pd.Timestamp(b['bounds'][1])
        verdeckt |= (dates >= s) & (dates < e)
    return set(dates[~verdeckt].dt.strftime('%H:%M'))


def _achse_leer(df, extended=False):
    """Leere Achsenzeit zwischen zwei sichtbaren Balken, die KEIN Break abdeckt."""
    dates = pd.to_datetime(df['Date']).sort_values()
    freq = dates.diff().value_counts().index[0]
    br = [(pd.Timestamp(b['bounds'][0]), pd.Timestamp(b['bounds'][1]))
          for b in _breaks(df, extended)]
    leer = pd.Timedelta(0)
    for a, b in zip(dates, dates.iloc[1:]):
        luecke_start, luecke_ende = a + freq, b
        if luecke_ende <= luecke_start:
            continue
        gedeckt = any(s <= luecke_start and e >= luecke_ende for s, e in br)
        if not gedeckt:
            leer += luecke_ende - luecke_start
    return leer


# US-Fall: regulaere Sitzung 15:30-21:30 mit Volumen, aussen herum ohne.
US = ([(h, 0, 0) for h in (10, 11, 12, 13, 14)]
      + [(h, 30, 1_000_000) for h in (15, 16, 17, 18, 19, 20, 21)]
      + [(h, 0, 0) for h in (22, 23)])
US_SITZUNG = {f'{h}:30' for h in (15, 16, 17, 18, 19, 20, 21)}
# Europaeischer Fall: nur die Eroeffnungsauktion ohne Volumen.
EU = [(9, 0, 0)] + [(h, 0, 500_000) for h in range(10, 18)]


def test_zweite_serie_wird_ausgeblendet():
    assert _sichtbar(_frame(US)) == US_SITZUNG


def test_eingeschaltet_bleiben_alle_balken():
    alle = {f'{h:02d}:{m:02d}' for h, m, _ in US}
    assert _sichtbar(_frame(US), extended=True) == alle


def test_eroeffnungsauktion_wird_nicht_weggefiltert():
    """Der Fall, der die erste Fassung falsch machte: genau EIN volumenloser
    Balken ist keine zweite Serie, sondern die Auktion."""
    assert '09:00' in _sichtbar(_frame(EU), extended=False)
    assert _sichtbar(_frame(EU), False) == _sichtbar(_frame(EU), True)


def test_reihe_ganz_ohne_volumen_bleibt_unberuehrt():
    """Indizes fuehren kein Volumen -- ein Filter wuerde den Rahmen leeren."""
    idx = [(h, 0, 0) for h in range(9, 18)]
    assert _sichtbar(_frame(idx), False) == {f'{h:02d}:00' for h in range(9, 18)}


def test_reihe_ganz_mit_volumen_bleibt_unberuehrt():
    voll = [(h, 0, 500_000) for h in range(9, 18)]
    assert _sichtbar(_frame(voll), False) == _sichtbar(_frame(voll), True)


def test_fehlende_volumenspalte_bricht_nicht_ab():
    df = _frame(US).drop(columns=['Volume'])
    assert _sichtbar(df, extended=False)


# ------------------------------------------------- dynamische Breaks

def test_keine_leere_achse_zwischen_tagen_und_wochenenden():
    assert _achse_leer(_frame(EU)) == pd.Timedelta(0)
    assert _achse_leer(_frame(US)) == pd.Timedelta(0)


def test_feiertag_hinterlaesst_keine_luecke():
    """Labor Day liess mit dem alten Stundenmuster einen ganzen leeren Tag stehen."""
    df = _frame(EU, tage=10)
    tag = pd.to_datetime(df['Date']).dt.date
    df = df[tag != pd.Timestamp('2026-06-03').date()].reset_index(drop=True)
    assert _achse_leer(df) == pd.Timedelta(0)


def test_verstreute_vorboersen_trades_blaehen_die_achse_nicht_auf():
    """FMAO 30m: einzelne Vorboersen-Trades an wenigen Tagen fuellten das
    Stunden-Histogramm, der Chart zeigte 16 statt 6,5 Stunden je Tag."""
    df = _frame([(h, 30, 1000) for h in range(15, 22)], tage=40)
    selten = pd.DataFrame({'Date': [pd.Timestamp('2026-06-01') + pd.Timedelta(days=7 * i, hours=11)
                                    for i in range(4)], 'Volume': 50, 'Close': 100.0})
    df = pd.concat([df, selten]).sort_values('Date').reset_index(drop=True)
    assert '11:00' not in _sichtbar(df)
    assert {f'{h}:30' for h in range(15, 22)} <= _sichtbar(df)


def test_isolierter_nachtbalken_wird_ausgeblendet():
    """Ein naechtlicher Nachboersen-Print (FMAO 01:30) bildet einen eigenen
    Block und gehoert nicht zur Sitzung, auch wenn er fast taeglich kommt."""
    df = _frame([(h, 30, 1000) for h in range(15, 22)] + [(1, 30, 100)])
    assert '01:30' not in _sichtbar(df)


def test_sitzung_ueber_mitternacht_bleibt_ein_block():
    """US-Werte mit Vor-/Nachboerse in Berliner Zeit: 20:00 bis 01:00."""
    df = _frame([(h, 0, 1000) for h in (20, 21, 22, 23, 0, 1)])
    assert _sichtbar(df) == {'20:00', '21:00', '22:00', '23:00', '00:00', '01:00'}


def test_schlussauktion_verkuerzt_den_folgebruch_nicht_zu_frueh():
    """Nach dem 17:30-Auktionsbalken beginnt der Nachtbruch um 18:00, nicht 18:30."""
    df = _frame([(h, 0, 1000) for h in range(9, 18)] + [(17, 30, 5000)], tage=2)
    starts = {b['bounds'][0][11:] for b in _breaks(df)}
    assert '18:00:00' in starts and '18:30:00' not in starts


def test_anzahl_breaks_ist_gedeckelt():
    """Illiquide 1m-Reihen haben tausende Mini-Luecken; die laengsten bleiben."""
    gt = GraphTools()
    zeilen = []
    for tag in range(30):
        basis = pd.Timestamp('2026-06-01') + pd.Timedelta(days=tag)
        if basis.dayofweek >= 5:
            continue
        for minute in range(0, 390, 2):       # jede zweite Minute fehlt
            zeilen.append({'Date': basis + pd.Timedelta(hours=15, minutes=30 + minute),
                           'Volume': 10, 'Close': 1.0})
    df = pd.DataFrame(zeilen)
    br = gt.get_range_breaks_NewV3(df)
    assert len(br) <= gt.MAX_INTRADAY_BREAKS + 2
    # Die Naechte (laengste Luecken) sind dabei
    assert any(b['bounds'][0].endswith('22:00:00') for b in br)


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
