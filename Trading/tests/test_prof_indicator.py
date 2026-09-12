"""Pins the profile-score indicator.

The two properties that matter for a column stored next to a backtest: it must
be causal (a bar may never see a later one), and it must not depend on how much
history happens to be loaded — otherwise the live chart and the simulation
disagree, which is the failure mode ovt.py has to work around.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from tradinglib.indicator.prof import COLUMNS, Prof


def _daily(closes, start='2020-01-01'):
    index = pd.bdate_range(start, periods=len(closes))
    close = pd.Series(closes, index=index, dtype=float)
    return pd.DataFrame({
        'Open': close.shift(1).fillna(close.iloc[0]),
        'High': close * 1.01,
        'Low': close * 0.99,
        'Close': close,
    }, index=index)


def _run(frame):
    # no symbol -> the indicator scores the frame it was handed
    return Prof(df=frame.copy(), symbol='').df


def test_columns_match_the_backfill_map():
    import asset_perf2
    assert list(COLUMNS) == asset_perf2.INDICATOR_BACKFILL_MAP['prof']


def test_record_high_is_a_running_maximum():
    close = pd.Series([10.0, 12.0, 9.0, 11.0, 15.0, 13.0])
    assert list(Prof.record_high(close)) == [10.0, 12.0, 12.0, 12.0, 15.0, 15.0]


def test_atr_series_matches_the_true_range_by_hand():
    frame = pd.DataFrame({
        'High':  [11.0, 12.0, 13.0],
        'Low':   [9.0, 10.0, 11.0],
        'Close': [10.0, 11.0, 12.0],
    })
    atr = Prof.atr_series(frame, period=2)
    # bar 2: max(|12-10|, |12-10|, |10-10|) = 2 ; bar 1 true range = 2 -> mean 2
    assert atr.iloc[1] == pytest.approx(2.0)
    assert np.isnan(atr.iloc[0])


def test_scores_do_not_look_ahead():
    """Truncating the future must not change a single past value."""
    frame = _daily(list(np.linspace(100, 160, 250)) + list(np.linspace(160, 120, 120)))
    full = _run(frame)
    cut = _run(frame.iloc[:250])
    for column in COLUMNS:
        assert np.allclose(full[column].iloc[:250].fillna(-1),
                           cut[column].fillna(-1)), column


def test_trend_score_is_high_at_the_high_and_low_after_a_slide():
    frame = _daily(list(np.linspace(100, 200, 200)) + list(np.linspace(200, 80, 200)))
    out = _run(frame)
    assert out['trendScore'].iloc[199] > 95      # at the record high
    assert out['trendScore'].iloc[-1] < 40       # 60 % below it


def test_risk_score_is_higher_for_the_calmer_series():
    calm = _daily([100 + 0.1 * (i % 5) for i in range(200)])
    wild = _daily([100 * (1.3 if i % 2 else 0.8) for i in range(200)])
    assert _run(calm)['riskScore'].iloc[-1] > _run(wild)['riskScore'].iloc[-1]


def test_risk_bucket_stays_in_range():
    frame = _daily(list(np.linspace(100, 130, 200)))
    bucket = _run(frame)['riskBucket'].dropna()
    assert bucket.between(1, 4).all()


def test_weekly_bars_are_refused_rather_than_misscored():
    """A weekly frame would give a different ATR and a different high — bail out."""
    index = pd.date_range('2020-01-05', periods=60, freq='W')
    frame = pd.DataFrame({'Open': 100.0, 'High': 101.0, 'Low': 99.0, 'Close': 100.0},
                         index=index)
    out = _run(frame)
    for column in COLUMNS:
        assert out[column].isna().all(), column


def test_every_column_is_attached_even_without_usable_data():
    out = _run(pd.DataFrame({'Close': []}))
    for column in COLUMNS:
        assert column in out.columns
