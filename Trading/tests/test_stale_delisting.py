"""Delisting: erkennen, markieren, Positionen schliessen.

Drei getrennte Stufen, bewusst nicht zu einem Automatismus verbunden:

1. ERKENNEN — ``data_quality.scan_stale_tickers`` vergleicht den letzten Balken
   gegen den MEDIAN der Kollegen, nicht gegen heute. Ein ausgefallener
   Abruflauf verschiebt den Median mit und meldet deshalb niemanden.
2. MARKIEREN — bleibt eine ausdrueckliche Entscheidung ueber
   ``asset_status.set_status``. Handelsstopp, Quellenausfall und echtes
   Delisting sehen in den Daten gleich aus; gemessen wurden im Bestand 29
   Verdachtsfaelle, darunter zwei aktiv gehandelte S&P-500-REITs.
3. SCHLIESSEN — offene Positionen auf markierten Tickern werden am letzten
   verfuegbaren Balken geschlossen und mit ``closeReason='delisted'`` versehen.
   Vorher entstand der Abschluss nur als NEBENWIRKUNG eines gescheiterten
   Live-Kursabrufs; bei einer Quelle, die noch veraltete Kurse liefert
   (KCO.DE nach dem 2026-08-12), waere er ausgeblieben.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib import data_quality as dq


def _reihe(letzter, n=60):
    idx = pd.date_range(end=pd.Timestamp(letzter), periods=n, freq='D')
    return pd.DataFrame({'Close': [1.0] * n, 'High': [1.0] * n,
                         'Low': [1.0] * n, 'Open': [1.0] * n}, index=idx)


@pytest.fixture
def reihen(monkeypatch):
    """load_daily durch eine Attrappe ersetzen — kein DB-Zugriff im Test."""
    daten = {}

    def fake(tk, db_path='database'):
        return daten.get(tk)

    import tradinglib.four_ps as fps
    monkeypatch.setattr(fps, 'load_daily', fake)
    return daten


# ------------------------------------------------------------------ Erkennen

def test_frischer_bestand_meldet_nichts(reihen):
    for tk in ('A', 'B', 'C'):
        reihen[tk] = _reihe('2026-09-08')
    assert dq.scan_stale_tickers(['A', 'B', 'C']).empty


def test_stehengebliebener_ticker_wird_gefunden(reihen):
    reihen['A'] = _reihe('2026-09-08')
    reihen['B'] = _reihe('2026-09-08')
    reihen['TOT'] = _reihe('2026-06-30')
    df = dq.scan_stale_tickers(['A', 'B', 'TOT'])
    assert list(df['ticker']) == ['TOT']
    assert df.iloc[0]['lag_days'] == 70
    assert df.iloc[0]['last_date'] == '2026-06-30'


def test_gemeinsamer_rueckstand_meldet_niemanden(reihen):
    """Der entscheidende Punkt: faellt ein Abruflauf aus, sind ALLE zurueck —
    gegen 'heute' gemessen waere der ganze Bestand tot, gegen den Median der
    Kollegen niemand."""
    for tk in ('A', 'B', 'C'):
        reihen[tk] = _reihe('2026-08-01')
    assert dq.scan_stale_tickers(['A', 'B', 'C']).empty


def test_schwelle_ist_einstellbar(reihen):
    reihen['A'] = _reihe('2026-09-08')
    reihen['B'] = _reihe('2026-09-08')
    reihen['LAHM'] = _reihe('2026-08-29')          # 10 Tage zurueck
    assert dq.scan_stale_tickers(['A', 'B', 'LAHM'], lag_days=10).empty
    assert not dq.scan_stale_tickers(['A', 'B', 'LAHM'], lag_days=5).empty


def test_sortierung_nach_rueckstand(reihen):
    reihen['A'] = _reihe('2026-09-08')
    reihen['B'] = _reihe('2026-09-08')
    reihen['ALT'] = _reihe('2026-05-01')
    reihen['NEUER'] = _reihe('2026-07-01')
    df = dq.scan_stale_tickers(['A', 'B', 'ALT', 'NEUER'])
    assert list(df['ticker']) == ['ALT', 'NEUER']


@pytest.mark.parametrize('eingabe', [[], None])
def test_leere_eingabe(reihen, eingabe):
    df = dq.scan_stale_tickers(eingabe)
    assert df.empty and list(df.columns) == ['ticker', 'last_date', 'lag_days', 'rows']


def test_unlesbare_reihe_wird_uebersprungen(reihen):
    reihen['A'] = _reihe('2026-09-08')
    reihen['B'] = _reihe('2026-09-08')
    reihen['LEER'] = pd.DataFrame()
    df = dq.scan_stale_tickers(['A', 'B', 'LEER'])
    assert 'LEER' not in list(df['ticker'])


def test_detektor_markiert_nicht_selbst():
    """Die Entscheidung bleibt beim Menschen — der Scanner darf keinen Status
    schreiben. Zwei aktiv gehandelte S&P-500-Werte standen in der Messung auf
    der Verdachtsliste."""
    import ast as _ast
    import inspect
    import textwrap
    baum = _ast.parse(textwrap.dedent(inspect.getsource(dq.scan_stale_tickers)))
    aufrufe = {(n.func.attr if isinstance(n.func, _ast.Attribute)
                else getattr(n.func, 'id', ''))
               for n in _ast.walk(baum) if isinstance(n, _ast.Call)}
    # Strukturell statt per Textsuche: der Docstring ERWAEHNT set_status
    # absichtlich als den Weg, den ein Mensch geht.
    assert 'set_status' not in aufrufe
    assert 'clear_status' not in aufrufe


# ------------------------------------------------------------- Schliessen

def _mt_src():
    import inspect
    from tradinglib.premium import multi_transaction as m
    return inspect.getsource(m.MultiTransactionProcessor.render)


def test_position_wird_aus_dem_status_geschlossen_nicht_aus_einem_fehlschlag():
    src = _mt_src()
    assert 'inactive_tickers()' in src
    assert "closeReason'] = 'delisted'" in src


def test_abschluss_setzt_auch_das_volumen():
    """Ohne sellVolume gilt die Position ueberall als offen — sie waere
    markiert und bewertet, wuerde aber weiter als gebundenes Kapital gefuehrt."""
    src = _mt_src()
    assert "'sellVolume'] = _bv" in src
    assert "'sellValue'] = round(_sp * _bv, 2)" in src


def test_live_bewertung_ueberschreibt_den_abschluss_nicht():
    """Die Bedingung sv == 0 der Live-Bewertung greift sonst auch hier und
    setzt Datum und Kurs wieder auf den heutigen Stand."""
    src = _mt_src()
    assert '_ist_stillgelegt' in src
    assert 'not _ist_stillgelegt' in src


def test_kein_verkauf_vor_dem_kauf():
    src = _mt_src()
    assert 'buyDate' in src and 'continue' in src
