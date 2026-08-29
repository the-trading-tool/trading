"""Tests fuer tools.move_column_before (Spaltenreihenfolge der Anzeigetabellen).

Rein kosmetisch, aber die Tabelle ist das, was der Nutzer liest: das Kaufdatum
gehoert vor das Verkaufsdatum, sonst liest sich eine Trade-Zeile rueckwaerts.
Werte duerfen dabei unter keinen Umstaenden wandern.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from tradinglib.tools import move_column_before


def _df():
    return pd.DataFrame({
        'buyDate':  ['2026-01-02', '2026-02-03'],
        'ticker':   ['AAA', 'BBB'],
        'gain':     [1.5, -2.5],
        'sellDate': ['2026-01-20', '2026-02-20'],
        'cum':      [1.5, -1.0],
    })


def test_spalte_wird_davor_einsortiert():
    got = move_column_before(_df(), 'buyDate', 'sellDate')
    assert list(got.columns) == ['ticker', 'gain', 'buyDate', 'sellDate', 'cum']


def test_werte_bleiben_bei_ihrer_spalte():
    src = _df()
    got = move_column_before(src, 'buyDate', 'sellDate')
    for col in src.columns:
        assert got[col].tolist() == src[col].tolist()
    assert len(got) == len(src)


def test_bereits_richtige_reihenfolge_bleibt():
    src = pd.DataFrame({'a': [1], 'buyDate': [2], 'sellDate': [3]})
    assert list(move_column_before(src, 'buyDate', 'sellDate').columns) == \
        ['a', 'buyDate', 'sellDate']


def test_fehlende_spalte_aendert_nichts():
    """Anzeigetabellen entstehen je nach Pfad mit unterschiedlichem Spaltensatz --
    eine fehlende Spalte darf die Seite nicht kosten."""
    src = pd.DataFrame({'a': [1], 'sellDate': [2]})
    assert list(move_column_before(src, 'buyDate', 'sellDate').columns) == ['a', 'sellDate']
    src2 = pd.DataFrame({'a': [1], 'buyDate': [2]})
    assert list(move_column_before(src2, 'buyDate', 'sellDate').columns) == ['a', 'buyDate']


def test_gleiche_spalte_ist_kein_fehler():
    src = _df()
    assert list(move_column_before(src, 'buyDate', 'buyDate').columns) == list(src.columns)


def test_leerer_dataframe():
    src = pd.DataFrame(columns=['buyDate', 'sellDate'])
    got = move_column_before(src, 'buyDate', 'sellDate')
    assert list(got.columns) == ['buyDate', 'sellDate']


def test_kein_dataframe_stuerzt_nicht_ab():
    assert move_column_before(None, 'a', 'b') is None


def test_stueckzahl_vor_die_erste_wert_spalte():
    """Die Tabelle fuehrt den Kaufwert zweimal (nativ und Systemwaehrung).
    Die Stueckzahl gehoert vor die erste von beiden -- sonst steht sie mitten
    zwischen zwei Betraegen."""
    src = pd.DataFrame({
        'ticker':      ['AAA'],
        'buyValue':    [-1000.0],
        'buyValueEUR': [-920.0],
        'buyVolume':   [12],
    })
    first = next(c for c in src.columns if c in ('buyValue', 'buyValueEUR'))
    got = move_column_before(src, 'buyVolume', first)
    assert list(got.columns) == ['ticker', 'buyVolume', 'buyValue', 'buyValueEUR']
    assert got['buyVolume'].tolist() == [12]
