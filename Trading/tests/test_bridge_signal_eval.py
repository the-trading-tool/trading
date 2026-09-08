"""Signal-Auswertung im Paper/Live-Pfad (trading_bridge).

Die Bridge hatte einen EIGENEN Auswertungspfad aus ExpressionEvaluator und
rohem eval() statt der geteilten Schicht tools.compute_signal_mask, an der
Multi Strategies, Strategy Finder, All Assets und der Chart haengen. Er konnte
drei Dinge nicht, die der Backtest kann:

  1. Mehrzeilige Bedingungen: "(A)\\n(B)" liest Python als Aufruf "(A)(B)" ->
     TypeError: 'Series' object is not callable. Daran scheiterten alle sieben
     Support/RSI-Eintraege (Kaufformel aus vier Zeilen).
  2. signal_window: steht je Index im JSON (Support/RSI: 2) und erlaubt, dass
     die Zeilen auf verschiedenen Balken erfuellt werden. Wurde nicht gelesen.
  3. Auswertung je Ticker: eval() lief ueber den ganzen Frame, rolling/shift
     haetten ueber Ticker-Grenzen hinweg gerechnet.

Ein Auseinanderlaufen faellt hier besonders unangenehm auf: die Bridge erzeugt
die Signale, nach denen tatsaechlich gehandelt wird.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib import tools

MEHRZEILIG = ("(close<=sup_support*1.02)\n(relvol_ratio > 1)\n"
              "(close>Open)\n(macd>macd_signal)")


def _df():
    return pd.DataFrame({
        'ticker': ['A', 'A', 'B', 'B'],
        'close': [1.0, 2.0, 3.0, 0.9],
        'sup_support': [1.0, 1.0, 1.0, 1.0],
        'relvol_ratio': [2.0, 0.5, 2.0, 2.0],
        'Open': [0.5, 0.5, 0.5, 0.5],
        'macd': [1.0, 1.0, 1.0, 1.0],
        'macd_signal': [0.5, 0.5, 0.5, 0.5]})


# ------------------------------------------------ der gemeldete Fehlerfall

def test_roher_eval_scheitert_an_mehrzeiligen_formeln():
    """Belegt die Ursache: so sah der alte Bridge-Pfad aus."""
    from tradinglib.tools import ExpressionEvaluator
    combined_df = _df()
    ev = ExpressionEvaluator(combined_df, dataframe_name='combined_df')
    with pytest.raises(TypeError, match='not callable'):
        eval(ev.validate_and_transform(MEHRZEILIG))


def test_geteilte_schicht_wertet_mehrzeilig_aus():
    m = tools.compute_signal_mask(_df(), MEHRZEILIG, window=1)
    assert len(m) == 4
    assert bool(m.iloc[0]) is True        # alle vier Zeilen erfuellt
    assert bool(m.iloc[1]) is False       # relvol_ratio 0.5


def test_fenster_erlaubt_erfuellung_auf_verschiedenen_balken():
    """Mit window=2 duerfen die Zeilen auf verschiedenen Balken zutreffen —
    genau dafuer steht signal_window=2 in der Support/RSI-Konfiguration."""
    eng = tools.compute_signal_mask(_df(), MEHRZEILIG, window=1)
    weit = tools.compute_signal_mask(_df(), MEHRZEILIG, window=2)
    assert int(weit.sum()) >= int(eng.sum())


# ------------------------------------------------- die Bridge selbst

def _bridge_src():
    from tradinglib.premium import trading_bridge as tb
    return inspect.getsource(tb)


def test_bridge_nutzt_die_geteilte_schicht():
    src = _bridge_src()
    assert 'tools.compute_signal_mask' in src


def test_bridge_hat_keinen_eigenen_eval_pfad_mehr():
    """Ein zweiter Auswerter neben compute_signal_mask laeuft frueher oder
    spaeter wieder auseinander — hier hat er es getan."""
    src = _bridge_src()
    assert 'ExpressionEvaluator(' not in src
    assert 'eval(buy_expr)' not in src and 'eval(sell_expr)' not in src


def test_bridge_liest_signal_window():
    src = _bridge_src()
    assert "strategy_config.get('signal_window')" in src


def test_bridge_behaelt_die_reihenfolge_sell_sticht_buy():
    """Dieselbe Semantik wie multi_transaction.py — sonst haetten Live-Signale
    eine andere Bedeutung als der Backtest."""
    src = _bridge_src()
    assert 'np.where(sell_mask.values, -1,' in src
