"""Tests fuer tools.resolve_holding_conflicts — chronologische Aufloesung.

Wer ZUERST gekauft hat, behaelt den Titel; die Konfigurationsreihenfolge ist nur
noch Gleichstand-Kriterium. Das spiegelt das Live-Verhalten: dort haelt der Agent
die zuerst gekaufte Position und ueberspringt das zweite Signal. Dadurch ist auch
kein Nachkauf oder Teilverkauf noetig — die zweite Strategie steigt nicht ein.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib.tools import resolve_holding_conflicts

A, B, C = ('S1', '^SPX'), ('S2', '^SPX'), ('S3', '^SPX')


def test_der_frueher_gekaufte_behaelt_den_titel():
    """Der Fall E.ON: B kaufte am 12.08., A erst am 26.08. -- obwohl A in der
    Config vorne steht, gewinnt B."""
    got = resolve_holding_conflicts(
        {A: {'EOAN.DE': [('2026-08-26', '2026-08-29')]},
         B: {'EOAN.DE': [('2026-08-12', '2026-08-29')]}},
        priority=[A, B])
    assert got == {A: {'EOAN.DE': {'2026-08-26'}}}      # A gibt ab, nicht B


def test_ohne_ueberlappung_bleibt_alles():
    got = resolve_holding_conflicts(
        {A: {'X': [('2026-01-02', '2026-01-20')]},
         B: {'X': [('2026-02-01', '2026-02-20')]}},
        priority=[A, B])
    assert got == {}


def test_gleicher_kauftag_entscheidet_die_config():
    got = resolve_holding_conflicts(
        {A: {'X': [('2026-01-02', '2026-01-20')]},
         B: {'X': [('2026-01-02', '2026-01-30')]}},
        priority=[A, B])
    assert got == {B: {'X': {'2026-01-02'}}}
    # umgekehrte Prioritaet dreht den Gewinner
    got2 = resolve_holding_conflicts(
        {A: {'X': [('2026-01-02', '2026-01-20')]},
         B: {'X': [('2026-01-02', '2026-01-30')]}},
        priority=[B, A])
    assert got2 == {A: {'X': {'2026-01-02'}}}


def test_drei_bewerber_nur_der_erste_bleibt():
    got = resolve_holding_conflicts(
        {A: {'X': [('2026-03-01', '2026-04-01')]},
         B: {'X': [('2026-02-01', '2026-05-01')]},
         C: {'X': [('2026-03-15', '2026-03-20')]}},
        priority=[A, B, C])
    assert got == {A: {'X': {'2026-03-01'}}, C: {'X': {'2026-03-15'}}}


def test_nach_dem_verkauf_darf_der_naechste_rein():
    """Sequentiell ist erlaubt — nur Gleichzeitigkeit nicht."""
    got = resolve_holding_conflicts(
        {A: {'X': [('2026-01-02', '2026-01-20')]},
         B: {'X': [('2026-01-21', '2026-02-10')]}},
        priority=[A, B])
    assert got == {}


def test_eigene_mehrfachhaltung_wird_nicht_blockiert():
    """Ein Paar haelt denselben Titel nacheinander — kein Konflikt mit sich selbst."""
    got = resolve_holding_conflicts(
        {A: {'X': [('2026-01-02', '2026-01-20'), ('2026-03-01', '2026-03-20')]}},
        priority=[A])
    assert got == {}


def test_verschiedene_ticker_stoeren_sich_nicht():
    got = resolve_holding_conflicts(
        {A: {'X': [('2026-01-02', '2026-06-01')]},
         B: {'Y': [('2026-01-02', '2026-06-01')]}},
        priority=[A, B])
    assert got == {}


def test_leere_eingaben():
    assert resolve_holding_conflicts({}, []) == {}
    assert resolve_holding_conflicts(None, None) == {}


def test_ohne_prioritaet_kein_absturz():
    got = resolve_holding_conflicts(
        {A: {'X': [('2026-01-02', '2026-02-02')]},
         B: {'X': [('2026-01-10', '2026-02-10')]}})
    assert got == {B: {'X': {'2026-01-10'}}}
