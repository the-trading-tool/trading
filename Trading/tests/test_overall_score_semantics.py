"""Nagelt die Semantik von overallTrend / overallValueTrend fest.

Diese Tests pruefen NICHT, dass die Formel richtig ist — sie ist es nach dem
Wortsinn der Namen nicht. Sie halten fest, wie sie sich HEUTE verhaelt, weil
Live-Strategien darauf stehen.

Hintergrund: In Sum.up() stammen Zaehler und Nenner von overallTrend aus
disjunkten Mengen (Fundamentalpunkte / technische Gewichte). Dadurch bekommt ein
fundamental starker Titel einen hohen overallTrend und muss die Huerde in
`overallValueTrend >= 1.1*overallTrend` umso hoeher nehmen. Die Kaufbedingung der
Strategie "Value Trend ^2" nutzt genau das als Bewertungsdisziplin. Wer die
Asymmetrie "repariert", dreht diese Auswahl still um.

Schlaegt einer dieser Tests fehl, ist die Semantik geaendert worden. Dann ist die
Frage nicht, wie man die Erwartung anpasst, sondern ob alle Verbraucher migriert
sind: asset_simulation_*.db-Spalten, Buy/Sell-Formeln, indicator/ovt.py,
multi_transaction, Dashboard.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from asset_perf2 import Sum, _d_trend_scalar, _mo_trend_scalar, score_df


def _fresh():
    """Sum haelt seine Zaehler als KLASSENattribute — je Test zuruecksetzen."""
    s = Sum()
    s.total_value_weight = 0
    s.total_weight = 0
    s.overall_trend = 0
    s.overall_value_trend = 0
    return s


# --------------------------------------------------- Asymmetrie in Sum.up

def test_technisches_kriterium_erreicht_overall_trend_nicht():
    s = _fresh()
    s.up(True, weight=2, points=2, isValue=False)
    assert s.overall_value_trend == 2      # Value-Score bekommt es
    assert s.overall_trend == 0            # Trend-Score NICHT
    assert s.total_weight == 2             # aber sein Nenner waechst
    assert s.total_value_weight == 2


def test_fundamentales_kriterium_landet_im_trend_zaehler():
    s = _fresh()
    s.up(True, weight=2, points=2, isValue=True)
    assert s.overall_value_trend == 2
    assert s.overall_trend == 2            # Fundamentalpunkte im Trend-Zaehler
    assert s.total_weight == 0             # dessen Nenner waechst dabei NICHT
    assert s.total_value_weight == 2


def test_zaehler_und_nenner_von_overall_trend_sind_disjunkt():
    """Der Kern: nur Fundamentales oben, nur Technisches unten."""
    s = _fresh()
    s.up(True, weight=3, points=3, isValue=False)   # technisch
    s.up(True, weight=1, points=1, isValue=True)    # fundamental
    assert s.overall_trend == 1                     # nur das fundamentale
    assert s.total_weight == 3                      # nur das technische
    assert s.overall_value_trend == 4 and s.total_value_weight == 4


def test_punkte_ohne_gewicht_sprengen_den_prozentrahmen():
    """Kriterien mit Gewicht 0 vergeben Punkte ohne Nenneranteil — der Quotient
    ist deshalb keine Prozentzahl und braucht das clip() am Ende."""
    s = _fresh()
    s.up(True, weight=0, points=0.5, isValue=True)
    assert s.overall_value_trend == 0.5 and s.total_value_weight == 0


def test_points_faellt_auf_weight_zurueck():
    s = _fresh()
    s.up(True, weight=2)
    assert s.overall_value_trend == 2


def test_falsche_bedingung_vergibt_nichts_zaehlt_aber_das_gewicht():
    s = _fresh()
    s.up(False, weight=2, points=2, isValue=False)
    assert s.overall_value_trend == 0 and s.overall_trend == 0
    assert s.total_weight == 2 and s.total_value_weight == 2


# ------------------------------------- combined_trend feuert immer (Konstante)

@pytest.mark.parametrize('sma200,sma50,sma20,close,ly_high', [
    (100.0, 90.0, 95.0, 80.0, 120.0),    # klarer Abwaertstrend
    (80.0, 85.0, 90.0, 100.0, 95.0),     # klarer Aufwaertstrend
    (100.0, 100.0, 100.0, 100.0, 100.0),  # flach
    (0.0, 0.0, 0.0, 0.0, 0.0),           # alles leer
])
def test_combined_trend_ist_nie_null(sma200, sma50, sma20, close, ly_high):
    """mo_trend und d_trend liegen in {0; 0,5; 1}, wk_trend in {0,5; 1} —
    die Summe kann nicht 0 werden. Da sie als Bedingung uebergeben wird, sind
    die 3 Punkte (groesstes Einzelgewicht) auf JEDER Zeile vergeben, auch im
    Abwaertstrend."""
    mo = _mo_trend_scalar(sma200, sma50, sma20, close, close, ly_high)
    d = _d_trend_scalar(sma200, sma50, sma20, close)
    for wk in (0.5, 1.0):                 # np.where(wkTrend > 0, 1.0, 0.5)
        combined = mo + wk + d
        assert combined > 0, (mo, wk, d)
        assert bool(np.where(combined, 3, 0)) is True


# ------------------------------------------- score_df spiegelt Sum.up getreu

def _row(**over):
    base = dict(ticker='T', Date='2026-01-05 00:00:00', close=100.0, Open=99.0,
                High=101.0, Low=98.0, sma20=95.0, sma50=90.0, sma200=85.0,
                lyHigh=90.0, ewo=1.0, ewo_ema=0.5, ewo_angle=1.0, vola=10.0,
                moTrend=5.0, macd_trend=0.0, macd_trend_wk=0.0, macd_trend_mo=0.0,
                momentum=50.0, rsi_ema=40.0, adx=30.0, rsi=55.0, sharpe=0.5,
                sortino=0.5, logVola=0.2, predictedHigh=110.0, predictedLow=90.0,
                roa=0.1, pctTargetHighPrice=5.0, wkTrend=1.0,
                ha_ema_low=95.0, ha_ema_high=105.0)
    base.update(over)
    return pd.DataFrame([base])


def test_score_df_liefert_beide_spalten_im_erlaubten_bereich():
    out = score_df(_row(), None)
    for col in ('overallTrend', 'overallValueTrend'):
        assert col in out.columns
        assert -100 <= int(out[col].iloc[0]) <= 100


def test_fundamental_starker_titel_hebt_overall_trend():
    """Die Wirkungsrichtung, auf der die Value-Trend-Kaufbedingung beruht:
    bessere Fundamentaldaten -> HOEHERER overallTrend -> hoehere Huerde in
    `overallValueTrend >= 1.1*overallTrend`."""
    schwach = pd.DataFrame([{'ticker': 'T', 'roa': 0.0, 'operatingMargins': -0.1,
                             'earningsGrowth': -0.1, 'forwardEps': 0.0,
                             'forwardPE': 0.0, 'trailingPE': 0.0,
                             'dividendRate': 0.0, 'priceToBook': 10.0,
                             'enterpriseValue': 0.0, 'totalDebt': 0.0,
                             'averageVolume': 100.0}])
    stark = pd.DataFrame([{'ticker': 'T', 'roa': 0.5, 'operatingMargins': 0.4,
                           'earningsGrowth': 0.3, 'forwardEps': 5.0,
                           'forwardPE': 12.0, 'trailingPE': 15.0,
                           'dividendRate': 2.0, 'priceToBook': 3.0,
                           'enterpriseValue': 1e10, 'totalDebt': 1e9,
                           'averageVolume': 5_000_000.0}])
    ot_schwach = int(score_df(_row(), schwach)['overallTrend'].iloc[0])
    ot_stark = int(score_df(_row(), stark)['overallTrend'].iloc[0])
    assert ot_stark > ot_schwach, (ot_schwach, ot_stark)


def test_technische_kriterien_bleiben_aus_overall_trend_heraus():
    """Gegenprobe zur Asymmetrie auf der vektorisierten Seite: ein rein
    technischer Unterschied (adx/momentum treiben trend_dir) darf overallTrend
    NICHT bewegen, wohl aber overallValueTrend."""
    info = pd.DataFrame([{'ticker': 'T', 'roa': 0.1, 'operatingMargins': 0.1,
                          'earningsGrowth': 0.1, 'forwardEps': 1.0,
                          'forwardPE': 12.0, 'trailingPE': 15.0,
                          'dividendRate': 1.0, 'priceToBook': 3.0,
                          'enterpriseValue': 1e10, 'totalDebt': 1e9,
                          'averageVolume': 5_000_000.0}])
    schwach = score_df(_row(adx=10.0, momentum=20.0, rsi_ema=50.0), info)
    stark = score_df(_row(adx=40.0, momentum=60.0, rsi_ema=30.0), info)
    assert int(schwach['overallTrend'].iloc[0]) == int(stark['overallTrend'].iloc[0])
    assert int(schwach['overallValueTrend'].iloc[0]) < int(stark['overallValueTrend'].iloc[0])
