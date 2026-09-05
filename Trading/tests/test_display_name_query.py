"""Namensaufloesung longName -> shortName -> ticker in den Listen-Queries.

Yahoo laesst longName bei vielen ETFs und Futures weg. Ein roher
``ai.longName``-Select zeigt in der Tabelle dann 'None' oder eine leere Zelle,
obwohl in derselben Zeile ein brauchbarer shortName steht (gemessen im
Bestand: 390 Zeilen ohne longName, davon 90 mit shortName).

``utils.display_name_sql()`` ist die eine Stelle, an der die Kette definiert ist.
Diese Tests halten fest, dass die Listen-Queries sie auch benutzen — der Fehler
faellt sonst nicht auf, weil die Spalte ja da ist, nur leer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib import make_query as mq
from tradinglib.utils import display_name_sql, get_display_name


@pytest.mark.parametrize('q', [1, 2, 3])
def test_listen_queries_loesen_den_namen_auf(q):
    """q=1/2/3 speisen Strategy Finder, Multi Strategies und Performance."""
    sql = mq.make_query('asset_simulation', '^GDAXI', '', q=q)
    assert display_name_sql('ai') in sql, f'q={q} nutzt display_name_sql nicht'


def test_ergebnisspalte_heisst_weiterhin_longname():
    """Aufrufer lesen df['longName'] — der Alias darf sich nicht aendern."""
    assert 'AS longName' in mq.field_list


def test_shortname_bleibt_zusaetzlich_verfuegbar():
    """Wer den Kurznamen eigens braucht, bekommt ihn weiterhin."""
    assert 'ai.shortName' in mq.field_list


def test_kein_roher_longname_select_mehr():
    """Ein blankes 'ai.longName,' waere der Rueckfall in das alte Verhalten."""
    assert 'ai.longName,' not in mq.field_list


# ------------------------------------------------ die Kette selbst (SQL + Python)

def test_sql_ausdruck_hat_alle_drei_stufen():
    sql = display_name_sql('ai')
    assert 'ai.longName' in sql and 'ai.shortName' in sql and 'ai.ticker' in sql
    assert sql.index('longName') < sql.index('shortName') < sql.index('ticker')


@pytest.mark.parametrize('row,erwartet', [
    ({'longName': 'Siemens AG', 'shortName': 'SIE', 'ticker': 'SIE.DE'}, 'Siemens AG'),
    ({'longName': '', 'shortName': 'Crude Oil Jul 26', 'ticker': 'CL=F'}, 'Crude Oil Jul 26'),
    ({'longName': None, 'shortName': None, 'ticker': 'XYZ'}, 'XYZ'),
    ({'longName': '   ', 'shortName': '  ', 'ticker': 'XYZ'}, 'XYZ'),
])
def test_python_gegenstueck_liefert_dieselbe_reihenfolge(row, erwartet):
    """get_display_name ist das Gegenstueck fuer bereits geladene Zeilen —
    beide Wege muessen dieselbe Kette gehen."""
    assert get_display_name(row) == erwartet
