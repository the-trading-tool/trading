"""Pins the behaviour of the risk profiles.

Every test runs against a fake config store — none of them may touch the real
config.db, since save_settings() would otherwise write into whatever database
TradingDB points at while the suite runs.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from tradinglib import risk_profile as rp


class _FakeConfig:
    """Stand-in for SystemConfig: same two methods, a dict instead of SQLite."""

    store = {}

    def __init__(self, username):
        self.username = username

    def get_value(self, key, default=None):
        return self.store.get((self.username, key), default)

    def set_value(self, key, value):
        self.store[(self.username, key)] = value


@pytest.fixture(autouse=True)
def fake_config(monkeypatch):
    _FakeConfig.store = {}
    monkeypatch.setattr(rp, '_config', lambda username: _FakeConfig(username))
    return _FakeConfig


def _frame(atr_pcts, price=100.0):
    return pd.DataFrame({
        'ticker': [f'T{i}' for i in range(len(atr_pcts))],
        'close': price,
        'atr': [price * a for a in atr_pcts],
        'logVola': [a * 5 for a in atr_pcts],
    })


def test_every_profile_is_complete_and_ordered():
    names = rp.available()
    assert names == ['conservative', 'balanced', 'dynamic', 'offensive']
    for name in names:
        profile = rp.resolve(name=name)
        for field in ('coverage', 'typical_vol', 'vol_band', 'drawdown_floor',
                      'stop_loss_pct', 'bucket'):
            assert field in profile, f"{name} is missing {field}"
        assert profile['vol_band'] > profile['typical_vol']
        assert profile['drawdown_floor'] < 0


def test_profiles_widen_monotonically():
    """Each profile down the list must admit more risk than the one before."""
    previous = None
    for name in rp.available():
        profile = rp.resolve(name=name)
        if previous is not None:
            assert profile['coverage'] >= previous['coverage']
            assert profile['vol_band'] >= previous['vol_band']
            assert profile['drawdown_floor'] <= previous['drawdown_floor']
        previous = profile


def test_apply_selects_by_the_atr_cut():
    frame = _frame([0.01, 0.025, 0.035, 0.05])
    assert list(rp.apply(frame, 'conservative')) == [True, False, False, False]
    assert list(rp.apply(frame, 'balanced')) == [True, True, False, False]
    assert list(rp.apply(frame, 'dynamic')) == [True, True, True, False]
    assert list(rp.apply(frame, 'offensive')) == [True] * 4


def test_apply_falls_back_to_log_vola_without_atr():
    frame = pd.DataFrame({'close': [100.0, 100.0], 'logVola': [0.08, 0.30]})
    assert list(rp.apply(frame, 'conservative')) == [True, False]


def test_unknown_risk_never_enters_a_narrow_profile():
    frame = pd.DataFrame({'close': [100.0], 'atr': [np.nan], 'logVola': [np.nan]})
    assert list(rp.apply(frame, 'conservative')) == [False]
    assert list(rp.apply(frame, 'offensive')) == [True]


def test_filter_expression_is_evaluable_and_agrees_with_apply():
    frame = _frame([0.01, 0.025, 0.035, 0.05])
    for name in rp.available():
        expression = rp.filter_expression(name)
        by_eval = frame.eval(expression)
        assert list(by_eval) == list(rp.apply(frame, name)), name


def test_risk_score_is_monotone_and_spans_the_scale():
    values = [0.004, 0.015, 0.022, 0.029, 0.042, 0.076, 0.25]
    scores = rp.risk_score(values)
    assert all(a > b for a, b in zip(scores, scores[1:]))   # calmer = higher
    assert scores[0] == pytest.approx(100.0)
    assert scores[-1] == pytest.approx(0.0)
    assert np.isnan(rp.risk_score([np.nan])[0])


def test_score_frame_attaches_both_columns():
    frame = _frame([0.01, 0.025, 0.035, 0.05])
    scored = rp.score_frame(frame)
    assert list(scored['riskBucket']) == [1, 2, 3, 4]
    assert scored['riskScore'].is_monotonic_decreasing
    assert 'riskScore' not in frame.columns      # the input is left alone


def test_score_frame_uses_log_vola_when_atr_is_missing():
    frame = pd.DataFrame({'close': [100.0, 100.0], 'logVola': [0.08, 0.30]})
    scored = rp.score_frame(frame)
    assert scored['riskScore'].iloc[0] > scored['riskScore'].iloc[1]


def test_bands_match_the_stated_promise():
    profile = rp.resolve(name='balanced')
    band = rp.bands('balanced')
    assert band['fwd_vol'] == (0.0, profile['vol_band'])
    assert band['fwd_mdd'][0] == profile['drawdown_floor']
    assert band['fwd_mdd'][1] == float('inf')


def test_settings_round_trip_per_user():
    rp.save_settings('kurt', {'profile': 'dynamic', 'custom': {'stop_loss_pct': 9.0}})
    stored = rp.settings('kurt')
    assert stored['profile'] == 'dynamic'
    assert stored['custom'] == {'stop_loss_pct': 9.0}
    # a second user is unaffected
    assert rp.settings('someone_else')['profile'] == rp.DEFAULT_PROFILE


def test_settings_reject_unknown_profiles_and_fields():
    rp.save_settings('kurt', {'profile': 'reckless',
                              'custom': {'stop_loss_pct': 9.0, 'coverage': 1.0}})
    stored = rp.settings('kurt')
    assert stored['profile'] == rp.DEFAULT_PROFILE
    assert stored['custom'] == {'stop_loss_pct': 9.0}   # 'coverage' is not editable


def test_resolve_applies_the_users_override():
    rp.save_settings('kurt', {'profile': 'conservative',
                              'custom': {'max_atr_pct': 0.015}})
    profile = rp.resolve('kurt')
    assert profile['name'] == 'conservative'
    assert profile['max_atr_pct'] == 0.015
    assert profile['customised'] is True
    assert rp.filter_expression(profile) == '(atr / close <= 0.015)'


def test_a_stored_calibration_wins_over_the_shipped_numbers():
    rp.save_calibration({'version': 'test', 'source': 'unit test',
                         'profiles': {'balanced': {'vol_band': 0.99}}})
    assert rp.resolve(name='balanced')['vol_band'] == 0.99
    assert rp.resolve(name='balanced')['calibration_version'] == 'test'
    # the cut itself is the definition and is never rewritten by a calibration
    assert rp.resolve(name='balanced')['max_atr_pct'] == rp.PROFILES['balanced']['max_atr_pct']


def test_format_profiles_renders_every_profile():
    text = rp.format_profiles()
    for name in rp.available():
        assert name in text
    assert 'atr / close' in text


def test_page_module_imports_and_its_locale_keys_exist():
    """A typo in a translation key should fail here, not in the rendered page."""
    import json
    import os
    import re

    from tradinglib import risk_profile_page            # noqa: F401  (import is the test)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, 'tradinglib', 'risk_profile_page.py'),
                  encoding='utf-8').read()
    # plain t('key') calls — the f-string ones are checked against the presets below
    used = set(re.findall(r"(?<![A-Za-z_.])t\(\s*'([^']+)'", source))
    for name in rp.available():
        used |= {f'risk.name_{name}', f'risk.desc_{name}'}
    used |= {'nav.risk_profile', 'page.risk_profile', 'error.load_risk_profile'}

    for language in ('de', 'en'):
        with open(os.path.join(root, 'locales', f'{language}.json'), encoding='utf-8') as fh:
            catalogue = json.load(fh)
        missing = sorted(key for key in used if key not in catalogue)
        assert not missing, f'{language}: {missing}'


# ── Position sizing ─────────────────────────────────────────────────────────

def test_the_vola_column_is_converted_to_an_annualised_fraction():
    """The stored column is a 21-bar percentage; the profiles speak annualised."""
    # 10.5 is the median of the universe and corresponds to roughly 36 % a year
    assert rp.annualised_vol([10.5]).iloc[0] == pytest.approx(0.364, abs=0.005)
    assert rp.annualised_vol([0.0]).iloc[0] == 0.0


def test_an_asset_at_the_target_gets_exactly_one_slot():
    profile = rp.resolve(name='balanced')
    # the column value whose annualised equivalent is the target
    at_target = profile['target_position_vol'] * 100 / np.sqrt(252 / 21)
    assert rp.position_weight([at_target], profile).iloc[0] == pytest.approx(1.0, abs=0.01)


def test_calmer_assets_get_more_and_wilder_ones_less():
    profile = rp.resolve(name='balanced')
    weights = rp.position_weight([5.0, 10.5, 25.0], profile)
    assert weights.iloc[0] > 1.0 > weights.iloc[1] > weights.iloc[2]


def test_the_weight_is_clamped_in_both_directions():
    profile = rp.resolve(name='balanced')
    weights = rp.position_weight([0.01, 500.0], profile, max_factor=2.0)
    assert weights.iloc[0] == pytest.approx(2.0)
    assert weights.iloc[1] == pytest.approx(0.5)


def test_an_unusable_volatility_falls_back_to_a_plain_slot():
    """Zero or missing volatility must not produce an infinite position."""
    profile = rp.resolve(name='balanced')
    weights = rp.position_weight([0.0, np.nan, None], profile)
    assert list(weights) == [1.0, 1.0, 1.0]


def test_sizing_is_off_until_it_is_switched_on():
    assert rp.sizing_enabled('kurt') is False
    assert rp.sizing_profile('kurt') is None
    rp.save_settings('kurt', {'profile': 'dynamic', 'custom': {}, 'sizing': True})
    assert rp.sizing_enabled('kurt') is True
    assert rp.sizing_profile('kurt')['name'] == 'dynamic'


def test_the_sizing_flag_survives_a_round_trip():
    rp.save_settings('kurt', {'profile': 'balanced', 'custom': {}, 'sizing': True})
    assert rp.settings('kurt')['sizing'] is True
    rp.save_settings('kurt', {'profile': 'balanced', 'custom': {}, 'sizing': False})
    assert rp.settings('kurt')['sizing'] is False
