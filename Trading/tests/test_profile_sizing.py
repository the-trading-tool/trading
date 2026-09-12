"""Pins profile-based position sizing across all three paths.

The backtest, the signals tab and the agent each compute a position size, and
they have drifted apart before — once over the normaliser (index average vs the
day's selection), once over the currency. The point of these tests is that the
three now agree by construction: all of them multiply an equal slot by the same
``risk_profile.position_weight``.

The other thing pinned here is that the rule is allowed to spend LESS than the
budget. That looks like a bug in a backtest report and is exactly the promise.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib import risk_profile as rp
from tradinglib.premium.trading_agent import _size_signals
from tradinglib.premium.trading_page import TradingPage

INVEST = 100_000.0
SLOTS = 5
PROFILE = rp.resolve(name='balanced')


def _signal(ticker, vola, price=100.0, score=1.0):
    return {'ticker': ticker, 'vola': vola, 'price': price, 'score': score,
            'currency': 'EUR', 'signal': 'buy'}


def _cfg():
    return {'invest': INVEST, 'num_assets': SLOTS}


def _expected(vola):
    weight = float(rp.position_weight([vola], PROFILE).iloc[0])
    return INVEST / SLOTS * weight


# ── the three paths agree ───────────────────────────────────────────────────

def test_agent_and_signals_tab_size_identically():
    signals = [_signal('A', 5.0, score=3), _signal('B', 10.5, score=2),
               _signal('C', 25.0, score=1)]
    from_agent = _size_signals(signals, _cfg(), 'EUR', profile=PROFILE)
    from_tab = TradingPage._apply_inv_vola_sizing(signals, _cfg(), 'EUR', PROFILE)
    assert [s['budget'] for s in from_agent] == [s['budget'] for s in from_tab]
    assert [s['qty'] for s in from_agent] == [s['qty'] for s in from_tab]


def test_the_live_budget_is_the_slot_times_the_profile_weight():
    signals = [_signal('A', 5.0), _signal('B', 10.5), _signal('C', 25.0)]
    sized = _size_signals(signals, _cfg(), 'EUR', profile=PROFILE)
    for row in sized:
        assert row['budget'] == pytest.approx(_expected(row['vola']), rel=1e-6)


def test_the_backtest_sizes_the_same_way(bare_simulator):
    sim = bare_simulator(cash=INVEST, slots=SLOTS, sizing_cap='profile',
                         volas={'A': 5.0}, sizing_profile=PROFILE,
                         fractional=True, fractional_decimals=8)
    sim.buy_asset('A', 100.0, '2026-01-05 00:00:00')
    spent = sim.portfolio['A']['shares'] * 100.0
    assert spent == pytest.approx(_expected(5.0), rel=1e-6)


# ── what the rule does differently ──────────────────────────────────────────

def test_without_a_profile_the_old_relative_rule_still_applies():
    """Default behaviour must not move — existing results stay comparable."""
    signals = [_signal('A', 5.0, score=2), _signal('B', 20.0, score=1)]
    sized = _size_signals(signals, _cfg(), 'EUR')
    # relative weights sum to 1 over the selection, scaled by the fair slot
    assert sum(s['budget'] for s in sized) == pytest.approx(
        INVEST * len(signals) / SLOTS)


def test_a_restless_day_deploys_less_than_the_budget():
    """The whole point: no quiet build-up of a portfolio nobody asked for."""
    calm = [_signal(f'C{i}', 5.0, score=i) for i in range(SLOTS)]
    wild = [_signal(f'W{i}', 40.0, score=i) for i in range(SLOTS)]
    calm_total = sum(s['budget'] for s in _size_signals(calm, _cfg(), 'EUR', profile=PROFILE))
    wild_total = sum(s['budget'] for s in _size_signals(wild, _cfg(), 'EUR', profile=PROFILE))
    assert wild_total < INVEST < calm_total
    assert wild_total == pytest.approx(INVEST * 0.5)      # the clamp


def test_a_single_signal_does_not_soak_up_the_whole_budget():
    """The relative rule needed a fair-slot correction for this; the absolute
    one gets it for free, because a weight never refers to the other signals."""
    sized = _size_signals([_signal('A', 10.5)], _cfg(), 'EUR', profile=PROFILE)
    assert sized[0]['budget'] < INVEST / 2


def test_the_backtest_never_goes_overdrawn(bare_simulator):
    """A calm asset earns more than one slot — but not more than the cash."""
    sim = bare_simulator(cash=10_000, slots=5, sizing_cap='profile',
                         volas={'A': 0.5}, sizing_profile=PROFILE,
                         fractional=True, fractional_decimals=8)
    sim.buy_asset('A', 100.0, '2026-01-05 00:00:00')
    assert sim.cash >= 0


def test_a_missing_profile_falls_back_to_an_equal_slot(bare_simulator):
    """Config unreadable must not mean an unbounded position."""
    sim = bare_simulator(cash=INVEST, slots=SLOTS, sizing_cap='profile',
                         volas={'A': 5.0}, sizing_profile=None,
                         fractional=True, fractional_decimals=8)
    sim.buy_asset('A', 100.0, '2026-01-05 00:00:00')
    assert sim.portfolio['A']['shares'] * 100.0 == pytest.approx(INVEST / SLOTS)


def test_profile_is_a_known_sizing_mode():
    from tradinglib.premium.asset_simulator import PortfolioSimulator
    assert 'profile' in PortfolioSimulator.SIZING_CAPS
