"""Tests fuer Bruchstuecke (PortfolioSimulator.fractional).

Ohne Bruchstuecke ist ein Wert, dessen Kurs ueber dem Slot-Budget liegt,
GRUNDSAETZLICH unkaufbar — und zwar aus zwei Gruenden, die beide greifen muessen:

1. `self.cash > price` verlangt Geld fuer eine ganze Einheit.
2. die Stueckzahl wird auf 0 Nachkommastellen gerundet, also auf 0.

Bei einem Bitcoin-Kurs von 90.000 EUR und einem Slot von 20.000 EUR trifft
beides zu. Die Tests fixieren, dass der Schalter beide Stellen loest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib.premium.asset_simulator import PortfolioSimulator

BTC = 90_000.0          # Kurs weit ueber dem Slot-Budget
SLOT_CASH = 100_000     # 5 Slots -> 20.000 je Position


def _sim(fractional=False, decimals=8, cap='none', cash=SLOT_CASH, slots=5):
    p = object.__new__(PortfolioSimulator)
    p.initial_cash = cash
    p.cash = cash
    p.max_assets = slots
    p.fee_pct = 0.0
    p.portfolio = {}
    p.history = []
    p.bought_assets = set()
    p.sizing_cap = cap
    p.sizing_factor_max = 2.0
    p.fractional = fractional
    p.fractional_decimals = decimals
    p._avg_vola = 10.0
    p._vola_by_ticker = {'BTC-EUR': 10.0, 'SAP.DE': 10.0}
    return p


def _buy(p, ticker='BTC-EUR', price=BTC):
    p.buy_asset(ticker, price, '2026-01-05 00:00:00')
    return p.portfolio.get(ticker, {}).get('shares', 0)


# ------------------------------------------------------- der gemeldete Fall

def test_ohne_bruchstuecke_ist_bitcoin_unkaufbar():
    p = _sim(fractional=False)
    assert _buy(p) == 0
    assert p.portfolio == {}
    assert p.cash == SLOT_CASH          # nichts ausgegeben


def test_mit_bruchstuecken_wird_gekauft():
    p = _sim(fractional=True)
    shares = _buy(p)
    assert shares == pytest.approx(20_000 / BTC, rel=1e-6)   # ~0,2222 BTC
    assert 0 < shares < 1
    assert p.cash == pytest.approx(SLOT_CASH - 20_000, abs=1.0)


def test_beide_blockaden_muessen_fallen():
    """Nur runden reicht nicht: die Kaufpruefung verlangt sonst weiter den
    vollen Kurs. Kasse knapp ueber 0, Kurs weit darueber."""
    p = _sim(fractional=True)
    p.cash = 500.0
    assert _buy(p) > 0


# ------------------------------------------------------------- Genauigkeit

def test_nachkommastellen_werden_eingehalten():
    p = _sim(fractional=True, decimals=4)
    shares = _buy(p)
    assert shares == round(shares, 4)


def test_null_nachkommastellen_waere_wirkungslos_und_wird_angehoben():
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000,
                           fractional=True, fractional_decimals=0)
    assert p.fractional_decimals >= 1


def test_absurde_genauigkeit_wird_gedeckelt():
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000,
                           fractional=True, fractional_decimals=99)
    assert p.fractional_decimals == 12


# ----------------------------------------------- Zusammenspiel mit dem Cap

def test_kassendeckel_haelt_auch_bei_bruchstuecken():
    """Abrunden auf die Nachkommastelle, sonst schoebe der Rest die Kasse
    doch ins Minus."""
    p = _sim(fractional=True, cap='cash')
    p.cash = 1_000.0
    p.fee_pct = 1.0
    _buy(p)
    assert p.cash >= 0


def test_ganze_stuecke_bleiben_ganz():
    """Der Regelfall darf sich nicht aendern."""
    p = _sim(fractional=False)
    shares = _buy(p, 'SAP.DE', 100.0)
    assert shares == int(shares)
    assert shares == 200                      # 20.000 / 100


# -------------------------------------------------------------- Robustheit

def test_vorgabe_ist_aus():
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000)
    assert p.fractional is False


def test_string_ja_wird_erkannt():
    """Im JSON steht gern 'yes'/'ja' statt True — analog require_isin."""
    for v in ('yes', 'ja', 'True', '1'):
        p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000,
                               fractional=v)
        assert p.fractional is True, v
    for v in ('no', 'nein', ''):
        p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000,
                               fractional=v)
        assert p.fractional is False, v


def test_per_index_feld_wird_durchgereicht():
    """multi_transactions muss 'fractional' je Index weiterreichen — sonst
    staende das Feld im JSON und waere wirkungslos."""
    import ast as _ast

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'tradinglib', 'premium', 'multi_transaction.py')
    src = open(path, encoding='utf-8').read()
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Call):
            fn = node.func
            name = (fn.attr if isinstance(fn, _ast.Attribute) else getattr(fn, 'id', ''))
            if name == 'PortfolioSimulator':
                kw = {k.arg for k in node.keywords}
                assert 'fractional' in kw and 'fractional_decimals' in kw
    assert "get('fractional'" in src
