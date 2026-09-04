"""Tests fuer die Begrenzung der Positionsgroesse (PortfolioSimulator.sizing_cap).

Ausgangspunkt war ein Strategy-Finder-Lauf auf COMMODITIES: Total cash -37.581
bei 100.000 Einsatz. Ursache ist die Kombination aus einer nach oben offenen
Risikogewichtung (Positionswert = Einsatz * Ø-Vola/Asset-Vola / Slots) und einer
Kaufpruefung, die nur `self.cash > price` testet — also ob das Geld fuer EINE
Aktie reicht, nicht fuer die berechnete Stueckzahl.

ZN=F mit Vola 1,4 gegen eine Ø-Vola von 9,95 ergibt Faktor 7,11 und damit eine
Position von 142.133 EUR bei 100.000 Einsatz und 5 Slots. Genau dieser Fall wird
hier nachgestellt.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib.premium.asset_simulator import PortfolioSimulator


def _sim(cap='none', factor_max=2.0, cash=100_000, slots=5):
    """Simulator ohne simulate(): nur die Sizing-Konstanten setzen."""
    p = object.__new__(PortfolioSimulator)
    p.initial_cash = cash
    p.cash = cash
    p.max_assets = slots
    p.fee_pct = 0.0
    p.portfolio = {}
    p.history = []
    p.bought_assets = set()
    p.sizing_cap = cap
    p.sizing_factor_max = factor_max
    # der COMMODITIES-Fall: ruhiger Anleihe-Future gegen bewegte Gruppe
    p._avg_vola = 9.949
    p._vola_by_ticker = {'ZN=F': 1.4, 'NG=F': 18.9, 'MID': 9.949}
    return p


def _position_value(p, ticker, price=100.0):
    p.buy_asset(ticker, price, '2026-01-05 00:00:00')
    if not p.history:
        return 0.0
    return -p.history[-1]['value']


# ------------------------------------------------------------------- 'none'

def test_none_ist_das_bisherige_verhalten():
    """Der gemeldete Fall: eine Position groesser als das ganze Kapital."""
    p = _sim('none')
    assert _position_value(p, 'ZN=F') == pytest.approx(142_130, rel=1e-3)
    assert p.cash < 0                      # genau das war der Befund


def test_none_bleibt_die_vorgabe():
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000)
    assert p.sizing_cap == 'none'


# ------------------------------------------------------------------- 'cash'

def test_cash_deckelt_auf_das_freie_kapital():
    p = _sim('cash')
    val = _position_value(p, 'ZN=F')
    assert val == pytest.approx(100_000, rel=1e-6)
    assert p.cash >= 0


def test_cash_haelt_die_kasse_ueber_mehrere_kaeufe_positiv():
    p = _sim('cash')
    for tk in ('ZN=F', 'NG=F', 'MID'):
        p.buy_asset(tk, 100.0, '2026-01-05 00:00:00')
        assert p.cash >= 0, f'nach {tk} negativ'


def test_cash_rechnet_die_gebuehr_mit():
    """Ohne die Gebuehr im Deckel reisst genau sie die Kasse ins Minus."""
    p = _sim('cash')
    p.fee_pct = 1.0
    p.buy_asset('ZN=F', 100.0, '2026-01-05 00:00:00')
    assert p.cash >= 0


def test_cash_laesst_kleine_positionen_unangetastet():
    """NG=F liegt unter der Gleichgewichtung — der Deckel darf nicht greifen."""
    a = _position_value(_sim('none'), 'NG=F')
    b = _position_value(_sim('cash'), 'NG=F')
    assert a == pytest.approx(b)


# ----------------------------------------------------------------- 'factor'

def test_factor_klammert_die_konzentration():
    p = _sim('factor', factor_max=2.0)
    # 2.0 * 100000 / 5 = 40000 statt 142133
    assert _position_value(p, 'ZN=F') == pytest.approx(40_000, rel=1e-3)


def test_factor_laesst_werte_innerhalb_der_klammer_unberuehrt():
    """NG=F hat Faktor 0,53 und liegt damit ueber der Untergrenze 1/2 — die
    Klammer darf hier nichts tun."""
    a = _position_value(_sim('none'), 'NG=F')
    b = _position_value(_sim('factor', factor_max=2.0), 'NG=F')
    assert b == pytest.approx(a)


def test_factor_hebt_sehr_kleine_positionen_an():
    """Enger geklammert (f=1,5 -> Untergrenze 0,667) greift sie auch nach unten."""
    a = _position_value(_sim('none'), 'NG=F')
    b = _position_value(_sim('factor', factor_max=1.5), 'NG=F')
    assert b > a
    # 13.300 statt 13.333: es werden ganze Stuecke gekauft (133 x 100 EUR)
    assert b == pytest.approx((1 / 1.5) * 100_000 / 5, rel=5e-3)


def test_factor_garantiert_keine_positive_kasse():
    """Ehrlich dokumentiert: f=2 und 5 Slots erlauben rechnerisch 200 % Einsatz.
    Fuenf ruhige Werte reichen, um die Kasse trotz Klammer ins Minus zu ziehen."""
    p = _sim('factor', factor_max=2.0)
    p._vola_by_ticker = {f'T{i}': 1.4 for i in range(5)}
    for i in range(5):
        p.buy_asset(f'T{i}', 100.0, '2026-01-05 00:00:00')
    assert p.cash < 0
    # Der Schaden bleibt begrenzt, aber nicht durch die Klammer: die Pruefung
    # `self.cash > price` laesst nach dem Rutsch ins Minus keinen Kauf mehr zu.
    # Es kommen also 3 der 5 Positionen zustande (40.000 x 3 = 120.000).
    assert len(p.portfolio) == 3
    assert p.cash == pytest.approx(-20_000, rel=1e-3)


def test_faktor_eins_ist_gleichgewichtung():
    p = _sim('factor', factor_max=1.0)
    assert _position_value(p, 'ZN=F') == pytest.approx(20_000, rel=1e-3)
    assert _position_value(_sim('factor', factor_max=1.0), 'NG=F') == pytest.approx(20_000, rel=1e-3)


# -------------------------------------------------------------- Robustheit

def test_unbekannter_modus_faellt_auf_none_zurueck():
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000,
                           sizing_cap='quatsch')
    assert p.sizing_cap == 'none'


def test_faktor_unter_eins_wird_angehoben():
    """Sonst waere die Untergrenze 1/f groesser als die Obergrenze f."""
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000,
                           sizing_cap='factor', sizing_factor_max=0.5)
    assert p.sizing_factor_max == 1.0


class _Cfg:
    def __init__(self, values):
        self.values = values

    def get_value(self, key, default=None):
        return self.values.get(key, default)


def _resolve(cfg_values, cap=None, factor=None):
    p = object.__new__(PortfolioSimulator)
    p.sys_config = _Cfg(cfg_values)
    p._init_sizing_cap(cap, factor)
    return p


def test_modus_kommt_aus_der_config():
    """Der Weg, auf dem Multi Strategies dieselbe Einstellung bekommt."""
    p = _resolve({'sizing_cap': 'cash'})
    assert p.sizing_cap == 'cash'


def test_faktor_kommt_aus_der_config():
    p = _resolve({'sizing_cap': 'factor', 'sizing_factor_max': 3.0})
    assert p.sizing_cap == 'factor'
    assert p.sizing_factor_max == 3.0


def test_ausdruecklicher_wert_schlaegt_die_config():
    """Braucht ein Vergleichslauf, der zwei Modi gegeneinander stellt."""
    p = _resolve({'sizing_cap': 'cash'}, cap='none')
    assert p.sizing_cap == 'none'


def test_ohne_config_bleibt_es_bei_none():
    p = _resolve({})
    assert p.sizing_cap == 'none'
    assert p.sizing_factor_max == PortfolioSimulator.DEFAULT_FACTOR_MAX


def test_ui_aufrufstellen_uebergeben_username():
    """Die Einstellung liegt pro Benutzer in config.db ("kurt:sizing_cap").

    Baut eine Aufrufstelle den Simulator ohne `username`, liest er im leeren
    Namensraum ":sizing_cap" und faellt still auf 'none' zurueck — die Auswahl
    in der Oberflaeche bliebe wirkungslos, ohne dass irgendwo ein Fehler
    erscheint. Genau so ist es beim ersten Bauen passiert.

    trading_bridge ist bewusst NICHT dabei: das ist der Live-Agent, dessen
    Positionsgroessen sich nicht als Nebenwirkung einer Backtest-Einstellung
    aendern sollen.
    """
    import ast as _ast
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    missing = []
    for rel in ('tradinglib/premium/asset_simulator.py',
                'tradinglib/premium/multi_transaction.py'):
        path = _os.path.join(root, *rel.split('/'))
        tree = _ast.parse(open(path, encoding='utf-8').read())
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, _ast.Attribute)
                    else getattr(fn, 'id', ''))
            if name != 'PortfolioSimulator':
                continue
            if not any(k.arg == 'username' for k in node.keywords):
                missing.append(f'{rel}:{node.lineno}')
    assert not missing, ('PortfolioSimulator ohne username aufgerufen: '
                         + ', '.join(missing))


def test_unsinniger_faktor_faellt_auf_die_vorgabe():
    p = PortfolioSimulator(data=pd.DataFrame({'vola': [1.0]}), initial_cash=1000,
                           sizing_cap='factor', sizing_factor_max='keine Zahl')
    assert p.sizing_factor_max == PortfolioSimulator.DEFAULT_FACTOR_MAX
