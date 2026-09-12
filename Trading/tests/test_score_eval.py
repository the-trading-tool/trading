"""Pins the arithmetic of the measurement harness.

The point of score_eval is that its numbers can be trusted to judge a signal,
so the metrics themselves are tested against cases whose answer is known by
construction — a column that predicts perfectly must score IC 1, a selection
that never changes must show zero turnover, and the forward-looking targets
must not be able to see the past instead of the future.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from tradinglib import score_eval as se


def _panel(frame, horizon=1):
    return se.Panel(df=frame.reset_index(drop=True), horizon=horizon, years=[2024])


def _synthetic(n_dates=60, n_tickers=40, seed=7):
    """A panel where 'signal' ranks the forward return exactly, per date."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2024-01-01', periods=n_dates)
    rows = []
    for date in dates:
        scores = rng.normal(size=n_tickers)
        for i, score in enumerate(scores):
            rows.append({'Date': date, 'ticker': f'T{i:03d}', 'signal': score,
                         'noise': rng.normal(), 'fwd_ret': score / 100.0,
                         'fwd_vol': 0.2 + 0.3 * (i / n_tickers),
                         'fwd_mdd': -0.02 - 0.10 * (i / n_tickers),
                         'close': 100.0})
    frame = pd.DataFrame(rows)
    frame['year'] = frame['Date'].dt.year
    return _panel(frame)


def test_rank_correlation_matches_pandas():
    panel = _synthetic(n_dates=5, n_tickers=30)
    ours = se._daily_rank_corr(panel.df, 'noise', 'fwd_ret')
    theirs = pd.Series({date: group['noise'].corr(group['fwd_ret'], method='spearman')
                        for date, group in panel.df.groupby('Date')})
    assert np.allclose(ours.values, theirs.loc[ours.index].values)


def test_perfect_signal_scores_ic_one():
    panel = _synthetic()
    result = se.ic(panel, 'signal')
    assert result['ic'] == pytest.approx(1.0)
    assert result['hit'] == pytest.approx(1.0)
    assert result['days'] == panel.df['Date'].nunique()


def test_unrelated_signal_scores_about_zero():
    panel = _synthetic()
    result = se.ic(panel, 'noise')
    assert abs(result['ic']) < 0.15
    assert abs(result['t']) < 3.0


def test_buckets_are_monotone_for_a_perfect_signal():
    table = se.buckets(_synthetic(), 'signal', n=5)
    assert list(table.index) == [1, 2, 3, 4, 5]
    assert table['fwd_ret'].is_monotonic_increasing


def test_neutralised_ic_removes_a_duplicated_signal():
    """A column that is only a relabelled copy has nothing of its own left."""
    panel = _synthetic()
    panel.df['copy'] = panel.df['signal'] * 3.0 + 1.0
    result = se.neutralised_ic(panel, 'copy', 'signal')
    assert result['raw_ic'] == pytest.approx(1.0)
    assert abs(result['residual_ic']) < 0.05


def test_gate_edge_is_measured_against_the_universe():
    panel = _synthetic()
    median = panel.df['signal'].median()
    result = se.gate(panel, f'signal > {median}', name='upper half')
    assert result['coverage'] == pytest.approx(0.5, abs=0.02)
    assert result['edge'] > 0
    assert result['fwd_ret'] == pytest.approx(
        result['baseline']['fwd_ret'] + result['edge'])


def test_gate_rejects_a_non_boolean_expression():
    result = se.gate(_synthetic(), 'signal * 2', name='not a gate')
    assert 'error' in result


def test_turnover_zero_when_the_selection_never_changes():
    panel = _synthetic()
    stable = panel.df['ticker'].isin([f'T{i:03d}' for i in range(10)])
    assert se.turnover(panel, stable) == pytest.approx(0.0)


def test_turnover_one_when_the_selection_rotates_completely():
    panel = _synthetic(n_dates=4, n_tickers=4)
    dates = sorted(panel.df['Date'].unique())
    # every date picks a disjoint set of names
    picks = {d: {f'T{(i + k) % 4:03d}'} for k, d in enumerate(dates) for i in [0]}
    mask = panel.df.apply(lambda r: r['ticker'] in picks[r['Date']], axis=1)
    assert se.turnover(panel, mask) == pytest.approx(1.0)


def test_conformity_counts_a_position_that_never_fell_as_inside_the_band():
    """A drawdown limit is a floor: 'never traded below entry' honours it."""
    frame = pd.DataFrame({
        'Date': pd.bdate_range('2024-01-01', periods=200),
        'ticker': ['AAA'] * 200,
        'close': 100.0,
        'fwd_ret': 0.01,
        'fwd_vol': 0.20,
        'fwd_mdd': 0.03,          # price only ever rose after entry
        'signal': 1.0,
    })
    frame['year'] = frame['Date'].dt.year
    result = se.conformity(_panel(frame), 'signal > 0',
                           se.DEFAULT_BANDS['conservative'], name='conservative')
    assert result['metrics']['fwd_mdd']['inside'] == pytest.approx(1.0)
    assert result['metrics']['fwd_vol']['inside'] == pytest.approx(1.0)


def test_conformity_flags_a_band_that_is_broken():
    frame = pd.DataFrame({
        'Date': pd.bdate_range('2024-01-01', periods=200),
        'ticker': ['AAA'] * 200,
        'close': 100.0, 'fwd_ret': 0.01,
        'fwd_vol': 0.80,          # far outside a conservative band
        'fwd_mdd': -0.40,
        'signal': 1.0,
    })
    frame['year'] = frame['Date'].dt.year
    result = se.conformity(_panel(frame), 'signal > 0',
                           se.DEFAULT_BANDS['conservative'])
    assert result['metrics']['fwd_vol']['inside'] == pytest.approx(0.0)
    assert result['metrics']['fwd_mdd']['inside'] == pytest.approx(0.0)


def test_reverse_rolling_looks_forward():
    """The forward targets must come from the future, not from the past."""
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    ahead = se._reverse_rolling(series, 3, 'min')
    # the first bar sees bars 0..2, so the minimum ahead of it is 1, not 5
    assert ahead.iloc[0] == 1.0
    assert pd.isna(ahead.iloc[-1]) or ahead.iloc[-1] >= 4.0


def test_format_report_survives_an_empty_battery():
    panel = _synthetic(n_dates=5, n_tickers=25)
    text = se.format_report(se.evaluate(panel, columns=[], gates={}, profiles={}))
    assert 'Score evaluation' in text
    assert 'caveats' in text
