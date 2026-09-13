"""Pins that a buy/sell formula gets the indicator columns it references.

A saved formula runs in the backtest against asset_simulation and in the chart
against the live frame, under the same column names. The live frame only holds
an indicator's columns while that indicator is selected — so the formula
"(trendScore>55)" broke the Asset Viewer with "name 'trendScore' is not
defined" as soon as the profile oscillator was not ticked (overallValueTrend had
the same fault before). These tests keep the mapping from column to indicator
honest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from tradinglib.fetch_data import indicators_for_expressions


def test_the_reported_formula_pulls_in_the_profile_indicator():
    formula = ("(trendScore>55)\n(close<=sup_support*1.02)\n(relvol_ratio > 1)\n"
               "(close>Open)\n(macd>macd_signal)")
    needed = indicators_for_expressions([formula], selected=['ewo', 'rsi'])
    assert 'prof' in needed
    assert {'sup', 'relvol', 'macd'} <= set(needed)


def test_selected_indicators_are_not_requested_twice():
    assert indicators_for_expressions(['trendScore > 55'], selected=['prof']) == []


def test_each_indicator_is_requested_once_in_reference_order():
    needed = indicators_for_expressions(
        ['(trendScore > 55) & (riskBucket <= 2)', 'riskScore > 40'], selected=[])
    assert needed == ['prof']


def test_value_trend_columns_map_to_ovt_including_the_ema_span():
    assert indicators_for_expressions(['overallValueTrend >= 1.1*overallTrend']) == ['ovt']
    assert indicators_for_expressions(['overallValueTrend > ovtEma21']) == ['ovt']


def test_plain_price_columns_need_no_indicator():
    assert indicators_for_expressions(['(close > Open) & (High < 100)']) == []


def test_raw_ohlc_backfill_entry_is_never_treated_as_an_indicator():
    """'ohlc' is a backfill-map key for Open/High/Low, not an indicator module."""
    assert 'ohlc' not in indicators_for_expressions(['Open > 1', 'High > Low'])


def test_empty_and_missing_formulas_are_harmless():
    assert indicators_for_expressions([None, '', '   ']) == []


def test_the_live_atr_matches_the_engine_formula():
    """The chart's atr has to be the same number the backtest stored."""
    from tradinglib.indicator.prof import Prof
    frame = pd.DataFrame({
        'High': [11.0, 12.5, 13.0, 12.0, 14.0],
        'Low': [9.0, 10.5, 11.0, 10.0, 12.0],
        'Close': [10.0, 12.0, 12.5, 11.0, 13.5],
    })
    engine = frame.copy()
    engine['H-L'] = (engine['High'] - engine['Low']).abs()
    engine['H-C'] = (engine['High'] - engine['Close'].shift(1)).abs()
    engine['L-C'] = (engine['Low'] - engine['Close'].shift(1)).abs()
    expected = engine[['H-L', 'H-C', 'L-C']].max(axis=1).rolling(3).mean()
    assert np.allclose(Prof.atr_series(frame, period=3).fillna(-1), expected.fillna(-1))
