"""Tests fuer tradinglib.allocation - Budgetaufteilung aus Slots und Volatilitaet.

Die Rechenkerne (raw_weights / apply_floor / round_to_step) sind bewusst reine
Funktionen ohne Datenbankzugriff, damit genau das hier pruefbar ist: dass die
Summe stimmt, dass Mindestgroessen halten und dass ein Markt ohne messbare
Volatilitaet nicht stillschweigend aus dem Portfolio faellt.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from tradinglib.allocation import (apply_floor, propose_allocation, raw_weights,
                                   round_to_step, apply_allocation,
                                   format_transactions)

PAIRS = [('S1', '^SPX', 6), ('S1', '^GDAXI', 4), ('S2', '^SPX', 3)]
VOLAS = {'^SPX': 0.20, '^GDAXI': 0.10}


# ---------------------------------------------------------------- Gewichtung

def test_mehr_slots_mehr_kapital():
    w = raw_weights([('S', 'A', 6), ('S', 'B', 3)], {'A': 0.2, 'B': 0.2})
    assert w[('S', 'A')] == pytest.approx(2 * w[('S', 'B')])


def test_hoehere_vola_weniger_kapital():
    """Gleiche Slotzahl, doppelte Vola -> halbes Gewicht."""
    w = raw_weights([('S', 'A', 4), ('S', 'B', 4)], {'A': 0.30, 'B': 0.15})
    assert w[('S', 'B')] == pytest.approx(2 * w[('S', 'A')])


def test_gewichte_summieren_auf_eins():
    assert sum(raw_weights(PAIRS, VOLAS).values()) == pytest.approx(1.0)


def test_markt_ohne_vola_faellt_nicht_raus():
    """Ein fehlender Wert darf keinen Markt aus dem Portfolio kippen -- das waere
    ein groesserer Fehler als ein naeherungsweises Gewicht."""
    w = raw_weights([('S', 'A', 4), ('S', 'B', 4)], {'A': 0.2, 'B': None})
    assert w[('S', 'B')] > 0
    assert sum(w.values()) == pytest.approx(1.0)


def test_alles_null_ist_ein_fehler():
    with pytest.raises(ValueError):
        raw_weights([('S', 'A', 0)], {'A': 0.2})


# -------------------------------------------------------------- Mindestgroesse

def test_ohne_mindestgroesse_reine_proportion():
    w = raw_weights(PAIRS, VOLAS)
    got = apply_floor(w, 100_000, {})
    assert sum(got.values()) == pytest.approx(100_000)
    assert got[('S1', '^SPX')] == pytest.approx(100_000 * w[('S1', '^SPX')])


def test_kleinster_posten_wird_angehoben():
    w = raw_weights(PAIRS, VOLAS)
    floors = {('S2', '^SPX'): 20_000}          # weit ueber seinem Anteil
    got = apply_floor(w, 100_000, floors)
    assert got[('S2', '^SPX')] == pytest.approx(20_000)
    assert sum(got.values()) == pytest.approx(100_000)


def test_anhebung_geht_zulasten_der_uebrigen():
    w = raw_weights(PAIRS, VOLAS)
    frei = apply_floor(w, 100_000, {})
    mit = apply_floor(w, 100_000, {('S2', '^SPX'): 20_000})
    assert mit[('S1', '^SPX')] < frei[('S1', '^SPX')]
    assert mit[('S1', '^GDAXI')] < frei[('S1', '^GDAXI')]


def test_unerfuellbare_mindestgroessen_melden_sich():
    w = raw_weights(PAIRS, VOLAS)
    with pytest.raises(ValueError, match='Mindestgroessen'):
        apply_floor(w, 10_000, {k: 5_000 for k in w})


# ------------------------------------------------------------------- Rundung

def test_rundung_trifft_das_budget_exakt():
    w = raw_weights(PAIRS, VOLAS)
    exact = apply_floor(w, 100_000, {})
    got = round_to_step(exact, 100_000, step=100)
    assert sum(got.values()) == 100_000
    assert all(v % 100 == 0 for v in got.values())


@pytest.mark.parametrize('step', [1, 50, 100, 500, 1000])
def test_budget_stimmt_bei_jeder_schrittweite(step):
    w = raw_weights(PAIRS, VOLAS)
    exact = apply_floor(w, 100_000, {})
    assert sum(round_to_step(exact, 100_000, step=step).values()) == 100_000


def test_rundung_unterschreitet_die_mindestgroesse_nicht():
    w = raw_weights(PAIRS, VOLAS)
    floors = {('S2', '^SPX'): 20_000}
    exact = apply_floor(w, 100_000, floors)
    got = round_to_step(exact, 100_000, step=1000, floors=floors)
    assert got[('S2', '^SPX')] >= 20_000
    assert sum(got.values()) == 100_000


# ------------------------------------------------------- Vorschlag insgesamt

TRANS = {
    'S1': {'^SPX': {'num_assets': 6, 'invest': 1}, '^GDAXI': {'num_assets': 4, 'invest': 1}},
    'S2': {'^SPX': {'num_assets': 3, 'invest': 1}},
}


def test_vorschlag_summiert_und_bleibt_vollstaendig():
    df = propose_allocation(TRANS, 100_000, volas=VOLAS)
    assert df['invest'].sum() == 100_000
    assert len(df) == 3
    assert set(zip(df['strategy'], df['index'])) == {
        ('S1', '^SPX'), ('S1', '^GDAXI'), ('S2', '^SPX')}


def test_slot_ist_invest_durch_slots():
    df = propose_allocation(TRANS, 100_000, volas=VOLAS)
    for r in df.to_dict('records'):
        assert r['slot'] == pytest.approx(r['invest'] / r['num_assets'])


def test_mindestslot_wirkt_durch():
    df = propose_allocation(TRANS, 100_000, volas=VOLAS, min_slot=5_000)
    assert (df['slot'] >= 5_000 - 1e-6).all()
    assert df['invest'].sum() == 100_000


def test_apply_allocation_aendert_nur_invest():
    df = propose_allocation(TRANS, 100_000, volas=VOLAS)
    out = apply_allocation(TRANS, df)
    assert TRANS['S1']['^SPX']['invest'] == 1        # Original unberuehrt
    assert out['S1']['^SPX']['num_assets'] == 6      # andere Felder bleiben
    assert sum(c['invest'] for s in out.values() for c in s.values()) == 100_000


def test_format_transactions_ist_wieder_einlesbar():
    import ast
    out = apply_allocation(TRANS, propose_allocation(TRANS, 100_000, volas=VOLAS))
    assert ast.literal_eval(format_transactions(out)) == out


def test_leere_transaktionen_melden_sich():
    with pytest.raises(ValueError):
        propose_allocation({}, 100_000, volas={})
