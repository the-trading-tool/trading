"""Tests fuer Bruchteile im Scalable-Order-Korb.

Scalable nimmt Bruchteile nur als BETRAGS-Order entgegen ("kaufe fuer 15.400
EUR"), nicht als Bruchteil einer Stueckzahl. Verkaeufe sind ausschliesslich
stueckbasiert — dort gibt es diesen Ausweg nicht.

Vorher fiel ein Bruchteil-Kaufsignal komplett weg (qty < 1 -> uebersprungen),
und `int(qty)` haette aus 0,22 eine 0 gemacht.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib.scalable_orders import (OrderDraft, drafts_from_agent_signals,
                                        to_preview_payload, validate)

ISIN = 'DE0007164600'
BTC = 69_829.53


def _sig(qty, price=BTC, ticker='BTC-EUR', isin='XF000BTC0017'):
    return {'ticker': ticker, 'qty': qty, 'price': price, 'isin': isin,
            'currency': 'EUR'}


# ------------------------------------------------------------ Signal-Builder

def test_bruchteil_wird_zur_betragsorder():
    drafts, skipped = drafts_from_agent_signals([_sig(0.22)], allow_network=False)
    assert not skipped, skipped
    d = drafts[0]
    assert d.shares == 0
    assert d.amount == pytest.approx(round(0.22 * BTC, 2))


def test_bruchteil_faellt_nicht_mehr_weg():
    """Der eigentliche Fehler: qty < 1 wurde komplett uebersprungen."""
    drafts, skipped = drafts_from_agent_signals([_sig(0.22)], allow_network=False)
    assert len(drafts) == 1 and not skipped


def test_ganze_stueckzahl_bleibt_stueckzahl():
    drafts, _ = drafts_from_agent_signals(
        [_sig(12, price=100.0, ticker='SAP.DE', isin=ISIN)], allow_network=False)
    d = drafts[0]
    assert d.shares == 12 and d.amount == 0


def test_bruchteil_ohne_kurs_wird_gemeldet():
    """Ohne Kurs laesst sich kein Betrag rechnen — stilles Verwerfen waere schlecht."""
    drafts, skipped = drafts_from_agent_signals([_sig(0.22, price=0)],
                                                allow_network=False)
    assert not drafts
    assert skipped and 'Kurs' in skipped[0][1]


def test_null_stueck_wird_gemeldet():
    drafts, skipped = drafts_from_agent_signals([_sig(0)], allow_network=False)
    assert not drafts and skipped


# ---------------------------------------------------------------- Validierung

def test_betragsorder_ist_gueltig():
    d = OrderDraft(side='buy', isin=ISIN, ticker='BTC-EUR', amount=15_400.0)
    assert validate(d) == []


def test_bruchteil_als_stueckzahl_wird_abgelehnt_mit_hinweis():
    d = OrderDraft(side='buy', isin=ISIN, ticker='BTC-EUR', shares=0.22)
    problems = validate(d)
    assert problems and 'amount' in problems[0]


def test_verkauf_von_bruchteilen_bleibt_unmoeglich():
    """Beim Verkauf kennt Scalable keinen Betragsmodus."""
    d = OrderDraft(side='sell', isin=ISIN, ticker='BTC-EUR', shares=0.22)
    problems = validate(d)
    assert problems and 'ganze' in problems[0].lower()


# -------------------------------------------------------------------- Export

def test_payload_nutzt_den_betragsmodus():
    d = OrderDraft(side='buy', isin=ISIN, ticker='BTC-EUR', amount=15_400.0)
    q = to_preview_payload(d)['quantity']
    assert q['mode'] == 'amount' and q['amount'] == '15400'


def test_payload_verschluckt_keinen_bruchteil():
    """int() haette 0,22 still zu 0 gemacht — eine 0-Stueck-Order."""
    d = OrderDraft(side='buy', isin=ISIN, ticker='BTC-EUR', shares=0.22)
    with pytest.raises(ValueError, match='Bruchteil'):
        to_preview_payload(d)


def test_ganze_stueckzahl_exportiert_unveraendert():
    d = OrderDraft(side='buy', isin=ISIN, ticker='SAP.DE', shares=12)
    q = to_preview_payload(d)['quantity']
    assert q == {'mode': 'shares', 'shares': 12}
