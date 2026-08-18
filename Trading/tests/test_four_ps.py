"""Tests for the 4PS engine (tradinglib/four_ps.py).

Synthetic price paths only — no database access, so the tests run anywhere.
"""
import numpy as np
import pandas as pd
import pytest

from tradinglib import four_ps as fps


def _path() -> pd.DataFrame:
    """Build a price path that walks through all four phases.

    rise 10 → 40 (+300 %), correction to 28 (confirms the leg), a long flat base
    around 30, then a fast breakout to 60.
    """
    idx = pd.bdate_range('2016-01-04', periods=2200)
    n = len(idx)
    seg = [
        (0.00, 0.45, 10.0, 40.0),   # long up-leg
        (0.45, 0.52, 40.0, 28.0),   # correction >25 % → leg confirmed
        (0.52, 0.94, 28.0, 30.0),   # sideways base
        (0.94, 1.00, 30.0, 60.0),   # breakout + trend
    ]
    close = np.zeros(n)
    for lo, hi, start, end in seg:
        a, b = int(lo * n), int(hi * n)
        close[a:b] = np.linspace(start, end, b - a)
    close[-1] = seg[-1][3]
    # A little deterministic wobble so highs/lows are not degenerate
    close = close * (1 + 0.004 * np.sin(np.arange(n) / 7.0))
    return pd.DataFrame({
        'Open': close, 'High': close * 1.01, 'Low': close * 0.99,
        'Close': close, 'Volume': np.full(n, 1_000_000.0),
    }, index=idx)


@pytest.fixture(scope='module')
def frame() -> pd.DataFrame:
    return fps.compute(_path())


def test_columns_present(frame):
    for col in ('fps_phase', 'fps_best_trend', 'fps_base_high', 'fps_base_low',
                'fps_base_weeks', 'fps_breakout', 'fps_buy', 'fps_sell',
                'fps_stop', 'fps_target', 'fps_rs', 'fps_dist_high'):
        assert col in frame.columns


def test_phase_one_is_proven_after_the_confirmed_leg(frame):
    """The +300 % leg counts only once the >25 % correction has confirmed it."""
    best = frame['fps_best_trend']
    assert best.iloc[:900].max() == 0        # nothing confirmed during the rise
    assert best.iloc[-1] >= 90.0


def test_base_then_breakout_then_trend(frame):
    phase = frame['fps_phase']
    assert (phase == 2).any(), 'no consolidation base detected'
    assert (phase == 3).any(), 'no breakout detected'
    assert phase.iloc[-1] == 4, 'trend was never confirmed'
    assert frame['fps_breakout'].sum() >= 1
    assert frame['fps_buy'].notna().sum() >= 1


def test_stop_and_target_are_sane(frame):
    last = frame.iloc[-1]
    close = _path()['Close'].iloc[-1]
    entry = frame['fps_buy'].dropna().iloc[-1]
    assert 0 < last['fps_stop'] < close
    # Target is fixed at entry (+target_pct) — price may well have passed it,
    # take_profit is off by default.
    assert last['fps_target'] > entry


def test_no_look_ahead():
    """Truncating the input must not change the values before the cut."""
    daily = _path()
    full = fps.compute(daily)
    cut = daily.index[-400]
    trunc = fps.compute(daily[daily.index <= cut])
    common = trunc.index[-250:]
    pd.testing.assert_frame_equal(full.loc[common].fillna(0.0),
                                  trunc.loc[common].fillna(0.0))


def test_price_gap_detection():
    """A 4:1 split that was never applied backwards must be reported."""
    from tradinglib import data_quality as dq

    daily = _path()
    broken = daily.copy()
    cut = broken.index[1200]
    for col in ('Open', 'High', 'Low', 'Close'):
        broken.loc[broken.index >= cut, col] = broken.loc[broken.index >= cut, col] / 4.0

    assert dq.detect_price_gaps(daily) == []          # sauber = keine Meldung
    gaps = dq.detect_price_gaps(broken)
    assert len(gaps) == 1
    assert gaps[0]['date'] == cut
    assert gaps[0]['factor'] == pytest.approx(0.25, abs=0.02)
    assert '4:1' in gaps[0]['cause']


def test_zigzag_confirms_only_after_the_reversal():
    close = pd.Series([10, 12, 15, 20, 25, 30, 22, 21, 20],
                      index=pd.date_range('2020-01-31', periods=9, freq='ME'))
    best, count, leg_low, legs = fps.zigzag(close, reversal_pct=25.0, trend_min_pct=100.0)
    # The high of 30 is only confirmed when price drops 25 % below it (22)
    assert best.iloc[5] == 0.0
    assert best.iloc[6] == pytest.approx(200.0)
    assert count.iloc[6] == 1
    assert legs[0]['gain'] == pytest.approx(200.0)


def test_entry_modes_fire_differently():
    """Base-less modes must find entries the breakout mode cannot see."""
    daily = _path()
    brk = fps.compute(daily, entry_mode='breakout')
    rec = fps.compute(daily, entry_mode='record_high')
    both = fps.compute(daily, entry_mode='both')

    brk_buys, rec_buys = brk['fps_buy'].dropna(), rec['fps_buy'].dropna()
    assert len(brk_buys) >= 1 and len(rec_buys) >= 1

    # Different triggers, different timing: the base high (~30) sits below the
    # old peak of 40, so the breakout fires first and the record-high entry has
    # to wait until price actually takes out that peak.
    assert rec_buys.index[0] > brk_buys.index[0]
    assert rec_buys.iloc[0] > 40.0 * 0.99

    # 'both' fires on whichever comes first -> never later than the breakout
    both_buys = both['fps_buy'].dropna()
    assert len(both_buys) >= 1
    assert both_buys.index[0] <= brk_buys.index[0]


@pytest.mark.parametrize('mode', ['record_high', 'new_high', 'both'])
def test_no_look_ahead_in_entry_modes(mode):
    daily = _path()
    full = fps.compute(daily, entry_mode=mode)
    cut = daily.index[-400]
    trunc = fps.compute(daily[daily.index <= cut], entry_mode=mode)
    common = trunc.index[-250:]
    pd.testing.assert_frame_equal(full.loc[common].fillna(0.0),
                                  trunc.loc[common].fillna(0.0))
