"""Tests fuer die OHLC-Skip-Warnung des PortfolioSimulators.

Die Meldung nannte bisher nur die Anzahl uebersprungener Bars. Damit wusste man,
DASS Kursdaten faul sind, aber nicht, welche Reihe man neu laden muss — und genau
die Ticker braucht der Bereinigungs-Befehl (get_asset_data.py /tickers:...).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import Counter

# asset_simulator zieht streamlit & Co. — der Import ist hier gewollt, die
# Formatierung selbst haengt an nichts davon.
from tradinglib.premium.asset_simulator import format_ohlc_skip_warning


def test_nennt_ticker_anzahl_und_erstes_datum():
    msg = format_ohlc_skip_warning(Counter({'ORLY': 40, 'AAF.L': 2}),
                                   {'ORLY': '2023-12-12', 'AAF.L': '2024-05-02'})
    assert 'ORLY (40x ab 2023-12-12)' in msg
    assert 'AAF.L (2x ab 2024-05-02)' in msg
    assert '42 Bar(s) in 2 Ticker(n)' in msg


def test_schlimmster_zuerst():
    msg = format_ohlc_skip_warning(Counter({'AAA': 1, 'ZZZ': 99}), {})
    assert msg.index('ZZZ') < msg.index('AAA')


def test_gleichstand_alphabetisch_stabil():
    """Sonst wechselt die Reihenfolge zwischen zwei Laeufen ohne Datenaenderung."""
    counts = Counter({'MNST': 3, 'FAST': 3, 'VST': 3})
    assert format_ohlc_skip_warning(counts, {}) == format_ohlc_skip_warning(counts, {})
    msg = format_ohlc_skip_warning(counts, {})
    assert msg.index('FAST') < msg.index('MNST') < msg.index('VST')


def test_lange_liste_wird_gekappt():
    counts = Counter({f'T{i:02d}': 100 - i for i in range(20)})
    msg = format_ohlc_skip_warning(counts, {})
    assert 'und 12 weitere' in msg
    assert 'T00' in msg and 'T07' in msg
    assert 'T08' not in msg


def test_ohne_datum_nur_die_anzahl():
    """Die Datumsspur ist Beiwerk -- fehlt sie, bleibt die Meldung lesbar."""
    msg = format_ohlc_skip_warning(Counter({'BYND': 5}))
    assert 'BYND (5x)' in msg
    assert ' ab ' not in msg


def test_zeitstempel_wird_auf_das_datum_gekuerzt():
    msg = format_ohlc_skip_warning(Counter({'TRIB': 1}),
                                   {'TRIB': '2025-03-04 00:00:00'})
    assert 'TRIB (1x ab 2025-03-04)' in msg


def test_einzelner_ticker():
    msg = format_ohlc_skip_warning(Counter({'PRPL': 42}), {'PRPL': '2026-01-05'})
    assert '42 Bar(s) in 1 Ticker(n)' in msg
    assert 'weitere' not in msg
