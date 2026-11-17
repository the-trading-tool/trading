"""Candidates funnel: regression-channel step (stored 10 years, live other lengths)."""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradinglib import candidates as cand
from tradinglib.indicator import sdc


def _steps():
    out = []
    return out, lambda *a, **k: out.append((a, k))


def _frame():
    return pd.DataFrame({'ticker': ['LOW', 'MID', 'HIGH', 'NOCH'],
                         'sdc_pos': [-3.4, 0.2, 1.6, 0.0],
                         'sdc_std': [5.0, 5.0, 5.0, 0.0]})


def test_stored_long_keeps_the_lower_band():
    steps, step = _steps()
    out = cand._apply_sdc(_frame(), dict(cand.DEFAULTS), 'long', 'database', step)
    assert out['ticker'].tolist() == ['LOW']
    assert steps[0][0][5] == 'note_sdc_stored' and steps[0][1]['n'] == 3


def test_stored_short_keeps_the_upper_band():
    _s, step = _steps()
    out = cand._apply_sdc(_frame(), dict(cand.DEFAULTS), 'short', 'database', step)
    assert out['ticker'].tolist() == ['HIGH']      # NOCH (std 0) has no channel


def test_thresholds_are_configurable():
    _s, step = _steps()
    opt = dict(cand.DEFAULTS, sdc_long=0.5)
    out = cand._apply_sdc(_frame(), opt, 'long', 'database', step)
    assert out['ticker'].tolist() == ['LOW', 'MID']


def _write_daily(folder, ticker, close):
    con = sqlite3.connect(folder / f'yf_{ticker}.db')
    pd.DataFrame({'Date': close.index.strftime('%Y-%m-%d %H:%M:%S'),
                  'Close': close.to_numpy()}).to_sql('day_data', con, index=False)
    con.close()


def _close(years, end, drift, seed):
    days = pd.bdate_range(end=end, periods=int(252 * years))
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, 0.012, len(days)))),
                     index=days)


def test_other_length_is_fitted_live_like_the_chart(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    end = pd.Timestamp.today().normalize()
    a = _close(7, end, 0.0004, 1)
    b = _close(3, end, 0.0004, 2)                   # too short for 5 years
    _write_daily(tmp_path, 'AAA', a)
    _write_daily(tmp_path, 'BBB', b)
    mid, std, _r = sdc.fit_last(a, 5)
    want = (a.iloc[-1] - mid.iloc[-1]) / std
    df = pd.DataFrame({'ticker': ['AAA', 'BBB', 'CCC'],
                       'sdc_pos': [9.0, 9.0, 9.0], 'sdc_std': [1.0, 1.0, 1.0]})
    steps, step = _steps()
    opt = dict(cand.DEFAULTS, sdc_years=5, sdc_long=99.0)
    out = cand._apply_sdc(df, opt, 'long', str(tmp_path), step)
    assert out['ticker'].tolist() == ['AAA']        # stored 9.0 ignored for 5 years
    assert out['sdc_pos'].iloc[0] == pytest.approx(want)
    assert steps[0][0][5] == 'note_sdc_live' and steps[0][1]['years'] == 5


def test_live_equals_the_stored_column_at_ten_years(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    c = _close(11, pd.Timestamp.today().normalize(), 0.0003, 3)
    _write_daily(tmp_path, 'AAA', c)
    live = cand._sdc_live(['AAA'], 10, str(tmp_path))
    assert live['AAA'][0] == pytest.approx(sdc.rolling_channel(c, 10)['sdc_pos'].iloc[-1])


def test_missing_columns_fall_back_to_live(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    _s, step = _steps()
    out = cand._apply_sdc(pd.DataFrame({'ticker': ['NONE']}), dict(cand.DEFAULTS),
                          'long', str(tmp_path), step)
    assert out.empty and _s[0][0][5] == 'note_sdc_live'


def test_locale_keys():
    root = Path(__file__).resolve().parent.parent / 'locales'
    keys = {'cand.use_sdc', 'cand.use_sdc_help', 'cand.sdc_years', 'cand.sdc_years_help',
            'cand.sdc_long', 'cand.sdc_long_help', 'cand.sdc_short', 'cand.sdc_short_help',
            'cand.step_sdc', 'cand.note_sdc_stored', 'cand.note_sdc_live',
            'cand.col_sdc_pos'}
    for lang in ('de', 'en'):
        d = json.loads((root / f'{lang}.json').read_text(encoding='utf-8'))
        assert not keys - set(d), (lang, keys - set(d))
