"""Gemeinsame Testbausteine.

`bare_simulator` baut einen PortfolioSimulator OHNE __init__ — der oeffnet
Datenbanken und Streamlit-Widgets, die fuer die reine Rechenlogik nicht noetig
sind. Der Preis dafuer: jedes neue Attribut, das buy_asset/sell_asset liest,
muss hier gesetzt werden.

Genau daran sind die Sizing- und Bruchstueck-Tests schon zweimal gebrochen,
als spaeter fractional bzw. min_hold_days dazukamen. Deshalb steht die Liste
jetzt an EINER Stelle statt in drei Testdateien.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def bare_simulator():
    """Factory: bare_simulator(cash=..., slots=..., sizing_cap=..., ...)."""
    from tradinglib.premium.asset_simulator import PortfolioSimulator

    def _make(cash=100_000, slots=5, fee_pct=0.0, sizing_cap='none',
              sizing_factor_max=2.0, fractional=False, fractional_decimals=8,
              min_hold_days=0, cooldown_days=0, avg_vola=10.0, volas=None):
        p = object.__new__(PortfolioSimulator)
        p.initial_cash = cash
        p.cash = cash
        p.max_assets = slots
        p.fee_pct = fee_pct
        p.portfolio = {}
        p.history = []
        p.bought_assets = set()
        p.sizing_cap = sizing_cap
        p.sizing_factor_max = sizing_factor_max
        p.fractional = fractional
        p.fractional_decimals = fractional_decimals
        p.min_hold_days = min_hold_days
        p.cooldown_days = cooldown_days
        p._last_sell = {}
        p._avg_vola = avg_vola
        p._vola_by_ticker = dict(volas or {})
        return p

    return _make
