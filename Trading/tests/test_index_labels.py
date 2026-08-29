"""Tests fuer die Kopfzeilen der Index-Detailbloecke (Multi Strategies).

Die Bloecke wurden zweimal angezeigt: einmal flach als stehengebliebenes
Fortschrittsprotokoll, einmal nach Strategien gruppiert. Die Fortschrittsanzeige
wird jetzt nach dem Lauf entfernt; damit ihre Zahlen (offene Positionen zum Kurs
bewertet) nicht verloren gehen, baut _final_index_labels() sie aus dem fertigen
trades_df — dieselbe Rechnung, die auch der Kennzahlen-Block zeigt.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from tradinglib.premium.multi_transaction import MultiTransactionProcessor as MTP


def _proc(df):
    """Instanz ohne __init__ (das oeffnet DBs und Streamlit-Widgets)."""
    p = object.__new__(MTP)
    p.trades_df = df
    p.system_currency = 'EUR'
    return p


def _df():
    # zwei Paare; im ersten eine offene Position (sellVolume NaN)
    return pd.DataFrame({
        'Strategy':   ['S1', 'S1', 'S1', 'S2'],
        'stockIndex': ['^SPX', '^SPX', '^SPX', '^GDAXI'],
        'sellVolume': [10.0, np.nan, 5.0, 3.0],
        'gain':       [100.0, 250.0, -50.0, 70.0],
    })


def test_offene_position_zaehlt_ins_potenzial_nicht_ins_realisierte():
    got = _proc(_df())._final_index_labels()
    # 3 Trades, davon 1 offen; Potenzial 100+250-50=300, realisiert 100-50=50
    assert got[('S1', '^SPX')] == (
        '^SPX — 3 Trades (1 offen) · Potenzial: 300 EUR (realisiert: 50)')


def test_paar_ohne_offene_position():
    got = _proc(_df())._final_index_labels()
    assert got[('S2', '^GDAXI')] == (
        '^GDAXI — 1 Trades (0 offen) · Potenzial: 70 EUR (realisiert: 70)')


def test_jedes_paar_bekommt_eine_zeile():
    assert set(_proc(_df())._final_index_labels()) == {('S1', '^SPX'), ('S2', '^GDAXI')}


def test_ohne_sellvolume_spalte_gilt_alles_als_geschlossen():
    """Aeltere Vintages fuehren die Spalte nicht -- das darf die Seite nicht kosten."""
    df = _df().drop(columns=['sellVolume'])
    got = _proc(df)._final_index_labels()
    assert '(0 offen)' in got[('S1', '^SPX')]
    assert 'realisiert: 300' in got[('S1', '^SPX')]


def test_leeres_oder_fehlendes_trades_df():
    assert _proc(pd.DataFrame())._final_index_labels() == {}
    assert _proc(None)._final_index_labels() == {}


def test_kaputtes_trades_df_wirft_nicht():
    """Fehlt eine Spalte, faellt die Anzeige auf die Payload-Kopfzeile zurueck --
    aber sie darf nicht mit einer Exception die ganze Seite abbrechen."""
    assert _proc(pd.DataFrame({'foo': [1]}))._final_index_labels() == {}
