"""Tests fuer den Cross-Strategie-Dedup (tools.holding_intervals /
tools.overlapping_buy_days).

Der Fall, der bisher durchrutschte: eine Strategie, die SPAETER verarbeitet
wird, aber FRUEHER gekauft hat. Die alte Regel verglich Kaufdatum gegen
Intervall — dann liegt so ein Kauf vor jedem bekannten Intervall und wurde nie
geblockt. Gemessen an trades2026.db hatten 56 von 56 Doppelhaltungen genau
diese Signatur (E.ON: Support/RSI ab 12.08., Value Trend ^2 ab 26.08.).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib.tools import holding_intervals, overlapping_buy_days


def _h(*events):
    return [{'ticker': t, 'timestamp': d, 'action': a} for t, d, a in events]


# ------------------------------------------------------- holding_intervals
def test_geschlossene_position():
    got = holding_intervals(_h(('AAA', '2026-01-02', 'buy'),
                               ('AAA', '2026-01-20', 'sell')), '2026-08-31')
    assert got == {'AAA': [('2026-01-02', '2026-01-20')]}


def test_offene_position_laeuft_bis_zum_ende():
    got = holding_intervals(_h(('AAA', '2026-08-12', 'buy')), '2026-08-31')
    assert got == {'AAA': [('2026-08-12', '2026-08-31')]}


def test_mehrere_runden_je_ticker():
    got = holding_intervals(_h(('AAA', '2026-01-02', 'buy'), ('AAA', '2026-01-20', 'sell'),
                               ('AAA', '2026-03-01', 'buy'), ('AAA', '2026-03-15', 'sell')),
                            '2026-08-31')
    assert got == {'AAA': [('2026-01-02', '2026-01-20'),
                           ('2026-03-01', '2026-03-15')]}


def test_zeitstempel_mit_uhrzeit_wird_gekuerzt():
    got = holding_intervals(_h(('AAA', '2026-01-02 00:00:00', 'buy')), '2026-08-31')
    assert got == {'AAA': [('2026-01-02', '2026-08-31')]}


def test_leere_history():
    assert holding_intervals([], '2026-08-31') == {}
    assert holding_intervals(None, '2026-08-31') == {}


# --------------------------------------------------- overlapping_buy_days
def test_der_fall_der_bisher_durchrutschte():
    """Kandidat kaufte am 12.08. (frueher), bekannt ist eine Haltung ab 26.08.

    Kaufdatum-gegen-Intervall haette hier NICHTS gefunden — 12.08. liegt vor
    dem Intervallbeginn. Intervall gegen Intervall erkennt die Ueberlappung.
    """
    candidate = {'EOAN.DE': [('2026-08-12', '2026-08-29')]}
    held = {'EOAN.DE': [('2026-08-26', '2026-08-29')]}
    assert overlapping_buy_days(candidate, held) == {'EOAN.DE': {'2026-08-12'}}


def test_kauf_innerhalb_eines_intervalls():
    candidate = {'AAA': [('2026-03-10', '2026-03-20')]}
    held = {'AAA': [('2026-03-01', '2026-03-31')]}
    assert overlapping_buy_days(candidate, held) == {'AAA': {'2026-03-10'}}


def test_sequentielle_wiederkaeufe_bleiben_erlaubt():
    """Nach dem Verkauf darf derselbe Titel wieder gekauft werden — das ist
    keine gleichzeitige Doppelhaltung."""
    candidate = {'AAA': [('2026-04-01', '2026-04-20')]}
    held = {'AAA': [('2026-01-02', '2026-03-31')]}
    assert overlapping_buy_days(candidate, held) == {}


def test_beruehrung_am_rand_zaehlt_als_ueberlappung():
    """Am selben Tag verkaufen und kaufen heisst, der Titel liegt an dem Tag in
    beiden Depots — bewusst als Ueberlappung gewertet."""
    candidate = {'AAA': [('2026-03-31', '2026-04-10')]}
    held = {'AAA': [('2026-01-02', '2026-03-31')]}
    assert overlapping_buy_days(candidate, held) == {'AAA': {'2026-03-31'}}


def test_anderer_ticker_stoert_nicht():
    candidate = {'AAA': [('2026-03-10', '2026-03-20')]}
    held = {'BBB': [('2026-03-01', '2026-03-31')]}
    assert overlapping_buy_days(candidate, held) == {}


def test_mehrere_kollisionen_werden_alle_gemeldet():
    candidate = {'AAA': [('2026-01-05', '2026-01-10'),
                         ('2026-05-05', '2026-05-10'),
                         ('2026-09-05', '2026-09-10')]}
    held = {'AAA': [('2026-01-01', '2026-02-01'), ('2026-05-01', '2026-06-01')]}
    assert overlapping_buy_days(candidate, held) == {'AAA': {'2026-01-05', '2026-05-05'}}


def test_leere_eingaben():
    assert overlapping_buy_days({}, {}) == {}
    assert overlapping_buy_days(None, None) == {}
    assert overlapping_buy_days({'AAA': [('2026-01-01', '2026-01-02')]}, {}) == {}
