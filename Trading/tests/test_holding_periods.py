"""Tests fuer Mindesthaltedauer und Sperrfrist (min_hold_days / cooldown_days).

Beide zaehlen KALENDERTAGE. Die Mindesthaltedauer sperrt nur den REGULAEREN
Ausstieg — die Stops laufen weiter, denn eine Frist, die eine Risikobremse
aussetzt, liesse eine Position beliebig weit unter ihr Stop-Niveau laufen.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib.premium.asset_simulator import PortfolioSimulator

T0 = pd.Timestamp('2026-01-05')


def _sim(min_hold=0, cooldown=0, *, make):
    """Attributliste steht in tests/conftest.py — dort einmal pflegen."""
    return make(min_hold_days=min_hold, cooldown_days=cooldown,
                avg_vola=10.0, volas={'AAA': 10.0})


# ------------------------------------------------------- Mindesthaltedauer

def test_ohne_frist_darf_sofort_verkauft_werden(bare_simulator):
    p = _sim(make=bare_simulator)
    p.buy_asset('AAA', 100.0, str(T0), ts=T0)
    assert p._hold_satisfied('AAA', T0 + pd.Timedelta(days=1)) is True


def test_frist_blockiert_den_fruehen_ausstieg(bare_simulator):
    p = _sim(min_hold=10, make=bare_simulator)
    p.buy_asset('AAA', 100.0, str(T0), ts=T0)
    assert p._hold_satisfied('AAA', T0 + pd.Timedelta(days=3)) is False


def test_frist_gibt_am_stichtag_frei(bare_simulator):
    p = _sim(min_hold=10, make=bare_simulator)
    p.buy_asset('AAA', 100.0, str(T0), ts=T0)
    assert p._hold_satisfied('AAA', T0 + pd.Timedelta(days=9)) is False
    assert p._hold_satisfied('AAA', T0 + pd.Timedelta(days=10)) is True


def test_ohne_zeitstempel_wird_nicht_blockiert(bare_simulator):
    """Eine Frist, die bei fehlender Zeitangabe sperrt, machte Positionen
    unverkaeuflich — im Zweifel freigeben."""
    p = _sim(min_hold=10, make=bare_simulator)
    p.buy_asset('AAA', 100.0, str(T0), ts=None)
    assert p._hold_satisfied('AAA', T0 + pd.Timedelta(days=1)) is True
    p2 = _sim(min_hold=10, make=bare_simulator)
    p2.buy_asset('AAA', 100.0, str(T0), ts=T0)
    assert p2._hold_satisfied('AAA', None) is True


def test_unbekannter_titel_blockiert_nicht(bare_simulator):
    assert _sim(min_hold=10, make=bare_simulator)._hold_satisfied('GIBTESNICHT', T0) is True


# ------------------------------------------------------------- Sperrfrist

def test_ohne_sperrfrist_sofortiger_rueckkauf_moeglich(bare_simulator):
    p = _sim(make=bare_simulator)
    p.buy_asset('AAA', 100.0, str(T0), ts=T0)
    p.sell_asset('AAA', 110.0, str(T0), ts=T0)
    p.bought_assets.discard('AAA')
    p.buy_asset('AAA', 100.0, str(T0 + pd.Timedelta(days=1)), ts=T0 + pd.Timedelta(days=1))
    assert 'AAA' in p.portfolio


def test_sperrfrist_verhindert_den_rueckkauf(bare_simulator):
    p = _sim(cooldown=5, make=bare_simulator)
    p.buy_asset('AAA', 100.0, str(T0), ts=T0)
    p.sell_asset('AAA', 110.0, str(T0), ts=T0)
    p.bought_assets.discard('AAA')
    p.buy_asset('AAA', 100.0, str(T0 + pd.Timedelta(days=2)), ts=T0 + pd.Timedelta(days=2))
    assert 'AAA' not in p.portfolio


def test_sperrfrist_laeuft_ab(bare_simulator):
    p = _sim(cooldown=5, make=bare_simulator)
    p.buy_asset('AAA', 100.0, str(T0), ts=T0)
    p.sell_asset('AAA', 110.0, str(T0), ts=T0)
    p.bought_assets.discard('AAA')
    p.buy_asset('AAA', 100.0, str(T0 + pd.Timedelta(days=5)), ts=T0 + pd.Timedelta(days=5))
    assert 'AAA' in p.portfolio


def test_sperrfrist_gilt_nur_fuer_den_verkauften_titel(bare_simulator):
    p = _sim(cooldown=30, make=bare_simulator)
    p._vola_by_ticker['BBB'] = 10.0
    p.buy_asset('AAA', 100.0, str(T0), ts=T0)
    p.sell_asset('AAA', 110.0, str(T0), ts=T0)
    p.buy_asset('BBB', 100.0, str(T0 + pd.Timedelta(days=1)), ts=T0 + pd.Timedelta(days=1))
    assert 'BBB' in p.portfolio


# -------------------------------------------------------------- Robustheit

def test_vorgabe_ist_aus(bare_simulator):
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000)
    assert p.min_hold_days == 0 and p.cooldown_days == 0


def test_unsinnige_werte_werden_zu_null(bare_simulator):
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000,
                           min_hold_days='keine Zahl', cooldown_days=-5)
    assert p.min_hold_days == 0 and p.cooldown_days == 0


def test_per_index_felder_werden_durchgereicht(bare_simulator):
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
                assert 'min_hold_days' in kw and 'cooldown_days' in kw
    assert "get('min_hold_days'" in src and "get('cooldown_days'" in src
