"""Pins the risk-profile steps in the candidates funnel.

The funnel is driven with a stubbed data source so the assertions are about the
chain, not about whatever happens to sit in the production database today. Two
behaviours matter beyond "it filters": the profile step has to run *before* the
trend filter (that ordering is what the measurement argued for), and a trend
score of 0 means "not computed yet" and must not be read as "at the bottom".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib import candidates as cand
from tradinglib import risk_profile as rp


def _rows():
    """Four names: calm/wild crossed with near/far from the high."""
    return pd.DataFrame({
        'ticker': ['CALM_HIGH', 'CALM_LOW', 'WILD_HIGH', 'WILD_LOW'],
        'Date': '2026-09-11 00:00:00',
        'close': [100.0, 100.0, 100.0, 100.0],
        'atr': [1.5, 1.5, 5.0, 5.0],          # 1.5 % vs 5 % of price
        'logVola': [0.09, 0.09, 0.30, 0.30],
        'trendScore': [90.0, 20.0, 90.0, 20.0],
        'riskScore': [90.0, 90.0, 20.0, 20.0],
        'sector': 'Technology', 'currency': 'USD', 'isin': 'US0000000001',
        'longName': 'Test', 'sup_support': 90.0, 'sup_resistance': 110.0,
        'relvol_ratio': 1.0, 'overallValueTrend': 50.0,
    })


@pytest.fixture(autouse=True)
def stubbed(monkeypatch):
    """No database, no stored settings — only the chain under test."""
    monkeypatch.setattr(cand, 'settings', lambda username: {})
    monkeypatch.setattr(cand, '_universe_tickers', lambda groups, db_path='database': None)
    monkeypatch.setattr(cand, '_latest_rows',
                        lambda prefilter, db_path='database', lookback_days=10:
                        (_rows(), ('source', {'db': 'test.db', 'date': '2026-09-11'}), 4))


def _find(**kw):
    options = dict(prefilter='', trend_mode='off', retest=False, use_rotation=False,
                   use_rsc=False, with_signal=False, max_per_sector=0, top_n=50)
    options.update(kw)
    frame, steps = cand.find('tester', **options)
    return frame, {s['key']: s for s in steps}


def test_without_a_profile_nothing_is_cut():
    frame, steps = _find()
    assert len(frame) == 4
    assert 'risk_profile' not in steps
    assert 'trend_score' not in steps


def test_the_profile_keeps_only_the_calm_names():
    frame, steps = _find(risk_profile='conservative')
    assert sorted(frame['ticker']) == ['CALM_HIGH', 'CALM_LOW']
    assert (steps['risk_profile']['before'], steps['risk_profile']['after']) == (4, 2)


def test_a_wider_profile_keeps_everything():
    frame, _ = _find(risk_profile='offensive')
    assert len(frame) == 4


def test_the_trend_score_keeps_only_the_names_near_their_high():
    frame, steps = _find(min_trend_score=75)
    assert sorted(frame['ticker']) == ['CALM_HIGH', 'WILD_HIGH']
    assert steps['trend_score']['after'] == 2


def test_profile_and_trend_combine_to_one_name():
    frame, steps = _find(risk_profile='conservative', min_trend_score=75)
    assert list(frame['ticker']) == ['CALM_HIGH']
    # the profile has to run first — that ordering is the measured part
    keys = [k for k in steps]
    assert keys.index('risk_profile') < keys.index('trend_score')
    assert steps['trend_score']['before'] == 2


def test_short_mirrors_the_trend_score():
    """Going short, distance from the high is what counts, not closeness."""
    frame, _ = _find(min_trend_score=75, direction='short')
    assert sorted(frame['ticker']) == ['CALM_LOW', 'WILD_LOW']


def test_a_trend_score_of_zero_means_not_computed(monkeypatch):
    """An unfilled column must not be read as 'at the very bottom'."""
    rows = _rows()
    rows['trendScore'] = 0.0
    monkeypatch.setattr(cand, '_latest_rows',
                        lambda prefilter, db_path='database', lookback_days=10:
                        (rows, ('source', {'db': 'test.db', 'date': '2026-09-11'}), 4))
    frame_long, _ = _find(min_trend_score=75)
    frame_short, _ = _find(min_trend_score=75, direction='short')
    assert frame_long.empty and frame_short.empty


def test_a_missing_column_is_reported_not_guessed(monkeypatch):
    rows = _rows().drop(columns=['trendScore'])
    monkeypatch.setattr(cand, '_latest_rows',
                        lambda prefilter, db_path='database', lookback_days=10:
                        (rows, ('source', {'db': 'test.db', 'date': '2026-09-11'}), 4))
    frame, steps = _find(min_trend_score=75)
    assert len(frame) == 4                       # nothing silently dropped
    assert steps['trend_score']['note_key'] == 'note_cols_missing'


def test_text_columns_do_not_break_the_profile(monkeypatch):
    """Legacy rows can carry numbers as TEXT — a string compare would mis-select."""
    rows = _rows()
    rows['atr'] = rows['atr'].astype(str)
    rows['close'] = rows['close'].astype(str)
    monkeypatch.setattr(cand, '_latest_rows',
                        lambda prefilter, db_path='database', lookback_days=10:
                        (rows, ('source', {'db': 'test.db', 'date': '2026-09-11'}), 4))
    frame, _ = _find(risk_profile='conservative')
    assert sorted(frame['ticker']) == ['CALM_HIGH', 'CALM_LOW']


def test_the_new_settings_are_part_of_the_defaults():
    assert cand.DEFAULTS['risk_profile'] == ''
    assert cand.DEFAULTS['min_trend_score'] == 0
    assert 'trendScore' in cand.RANK_COLUMNS


def test_the_step_labels_exist_in_both_languages():
    import json
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    keys = ['cand.step_risk_profile', 'cand.step_trend_score',
            'cand.note_risk_profile', 'cand.note_trend_score',
            'cand.risk_profile', 'cand.risk_profile_off', 'cand.risk_profile_own',
            'cand.min_trend_score', 'cand.rank_trendScore', 'cand.rank_riskScore']
    for language in ('de', 'en'):
        with open(os.path.join(root, 'locales', f'{language}.json'), encoding='utf-8') as fh:
            catalogue = json.load(fh)
        missing = [k for k in keys if k not in catalogue]
        assert not missing, f'{language}: {missing}'


def test_the_user_profile_is_looked_up_per_user(monkeypatch):
    seen = {}

    def _resolve(username='', name='', calibration=None):
        seen['username'], seen['name'] = username, name
        return rp.PROFILES['offensive'] | {'name': 'offensive'}

    monkeypatch.setattr(rp, 'resolve', _resolve)
    _find(risk_profile='user')
    assert seen == {'username': 'tester', 'name': ''}
