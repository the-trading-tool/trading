"""sizing_cap='normalized' — die Live-Formel im Backtest.

Backtest und Live-Pfad dimensionierten Positionen unterschiedlich. Beide haben
dieselbe Gestalt::

    Position_i = invest / max_assets * (Normierer / vola_i)

nur der Normierer unterscheidet sich: der Backtest nimmt das arithmetische
Mittel der Volatilitaet ueber den GANZEN Index, der Live-Pfad
(trading_agent._size_signals / TradingPage._apply_inv_vola_sizing) das
harmonische Mittel der AUSGEWAEHLTEN Signale eines Tages.

Gemessen an der echten Konfiguration: Verhaeltnis im Median 1,07x, je Eintrag
0,57x bis 2,00x. Praktisch aeussert es sich nicht nur in der Groesse, sondern in
der Zusammensetzung — eine zu gross bemessene erste Position verbraucht das
Budget, sodass der zweite Slot leer bleibt.

Der neue Modus rechnet die Live-Formel, damit sich beides gegeneinander messen
laesst. Die Vorgabe bleibt unveraendert, damit Altlaeufe vergleichbar bleiben.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib.premium.asset_simulator import PortfolioSimulator

# Der gemessene CRYPTO-Fall: zwei Stablecoins (vola 1,6) in einem Universum mit
# Ø-Vola ~12,25, zwei Slots, 4.000 Einsatz.
TK = ['USDT-EUR', 'USDC-EUR', 'BTC-EUR', 'ETH-EUR', 'BNB-EUR',
      'SOL-EUR', 'XRP-EUR', 'DOGE-EUR', 'TRX-EUR', 'XMR-EUR']
VOLA = [1.6, 1.6, 12.0, 15.0, 14.0, 20.0, 16.0, 22.0, 9.0, 11.3]


def _data(signale=(1, 1, 0, 0, 0, 0, 0, 0, 0, 0), preis=0.86):
    return pd.DataFrame({
        'ticker': TK, 'Date': ['2026-09-05 00:00:00'] * len(TK),
        'close': [preis] * len(TK), 'vola': VOLA, 'buySell': list(signale),
        'longName': TK, 'ISIN': [None] * len(TK),
        'currency': ['EUR'] * len(TK), 'stockIndex': ['CRYPTO'] * len(TK)})


def _run(cap, cash=4000, slots=2, **kw):
    p = PortfolioSimulator(data=_data(**kw), initial_cash=cash, max_assets=slots,
                           sizing_cap=cap, sizing_factor_max=2.0, fee_pct=0.0)
    p.simulate()
    return p


def _werte(p):
    return {t: round(v['shares'] * v['buy_price'], 2) for t, v in p.portfolio.items()}


# ------------------------------------------------------------- der Modus selbst

def test_modus_ist_registriert():
    assert 'normalized' in PortfolioSimulator.SIZING_CAPS


def test_gewichte_summieren_sich_auf_den_einsatz():
    """w_i summiert sich auf 1, mal k/max_assets — bei k = max_assets also
    genau auf den Einsatz. Konstruktiv kein Ueberziehen."""
    p = _run('normalized')
    assert round(sum(_werte(p).values()), 2) == pytest.approx(4000, abs=1.0)
    assert p.cash >= 0


def test_beide_slots_werden_besetzt():
    """Der Kern des Unterschieds: die Vorgabe bemisst die erste Position so
    gross, dass fuer die zweite kein Geld bleibt."""
    norm = _run('normalized')
    fak = _run('factor')
    assert len(norm.portfolio) == 2
    assert len(fak.portfolio) == 1


def test_gleiche_vola_ergibt_gleiche_positionen():
    """Beide Stablecoins haben vola 1,6 -> haelftige Aufteilung.

    Auf ein STUECK genau, nicht auf den Cent: 2.000 / 0,86 = 2.325,58 rundet
    auf 2.326 Stueck (2.000,36 EUR), womit fuer die zweite Position ein Stueck
    weniger bleibt. Die Rundung wandert also in den letzten Kauf — das ist
    Absicht (ganze Stuecke) und kein Gewichtungsfehler.
    """
    preis = 0.86
    w = list(_werte(_run('normalized', preis=preis)).values())
    assert w[0] == pytest.approx(w[1], abs=preis * 1.01)
    assert w[0] == pytest.approx(2000, abs=preis * 1.01)


def test_ruhigerer_titel_bekommt_mehr():
    """Die RELATIVE Gewichtung ist in beiden Formeln identisch (1/vola) —
    das darf der neue Modus nicht verdrehen."""
    sig = [1, 0, 1, 0, 0, 0, 0, 0, 0, 0]      # USDT (1,6) und BTC (12,0)
    w = _werte(_run('normalized', signale=sig))
    assert w['USDT-EUR'] > w['BTC-EUR']
    # Verhaeltnis der Gewichte = umgekehrtes Verhaeltnis der Volas
    assert w['USDT-EUR'] / w['BTC-EUR'] == pytest.approx(12.0 / 1.6, rel=0.02)


def test_weniger_signale_als_slots_binden_nicht_das_ganze_budget():
    """Fair-Slot-Skalierung k/max_assets — analog zum Live-Pfad, wo ein
    einzelnes Signal sonst 100 % des Index-Budgets bekaeme."""
    sig = [1] + [0] * 9
    p = _run('normalized', slots=4, signale=sig)
    assert round(sum(_werte(p).values()), 2) == pytest.approx(1000, abs=1.0)


# ------------------------------------------------------- Vorgabe unveraendert

@pytest.mark.parametrize('cap', ['none', 'cash', 'factor'])
def test_bestehende_modi_bleiben_unberuehrt(cap):
    """Die Vorgabe muss dieselben Zahlen liefern wie vorher, sonst waeren alle
    Altlaeufe entwertet."""
    p = _run(cap)
    assert p.sizing_cap == cap
    assert not getattr(p, '_day_budget', {})       # nur 'normalized' baut sie


def test_ohne_normalized_keine_tagesbudgets():
    p = _run('factor')
    assert p._day_budget == {}


# -------------------------------------------------------------- Robustheit

def test_unbrauchbare_vola_faellt_auf_die_alte_formel_zurueck():
    """Fehlt fuer einen Titel die Volatilitaet, darf der Kauf nicht ausfallen."""
    d = _data()
    d.loc[d['ticker'] == 'USDT-EUR', 'vola'] = 0.0
    p = PortfolioSimulator(data=d, initial_cash=4000, max_assets=2,
                           sizing_cap='normalized', fee_pct=0.0)
    p.simulate()
    assert 'USDC-EUR' in p.portfolio      # der brauchbare Titel wird gekauft


def test_kein_kaufsignal_ergibt_leere_budgets():
    p = _run('normalized', signale=[0] * 10)
    assert p._day_budget == {}
    assert p.portfolio == {}
