"""Pine export of the prof indicator (riskScore / riskBucket / trendScore).

Pine cannot be compiled here; the test mirrors the exported formulas in Python
and holds them against Prof itself, and checks that the calibration is taken
from risk_profile instead of being copied.
"""
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import pine_exporter as pe
from tradinglib import pine_strategy_export as pse
from tradinglib import risk_profile as rp
from tradinglib.indicator.prof import Prof


def _floats(xs):
    return ', '.join(str(float(x)) for x in xs)


def _pine_mirror(frame, n=14):
    """The exported formulas, line by line: ta.sma(ta.tr(true), n) / close,
    close / ta.max(close) - 1, pw_interp, risk_bucket_of."""
    h, l, c = frame['High'], frame['Low'], frame['Close']
    tr = np.maximum.reduce([h - l, (h - c.shift()).abs().fillna(h - l), (l - c.shift()).abs().fillna(h - l)])
    atrp = pd.Series(tr, index=frame.index).rolling(n).mean() / c
    dist = c / c.cummax() - 1.0
    risk = np.interp(atrp, rp.ATR_BREAKPOINTS, rp.BREAKPOINT_SCORES)
    trend = np.interp(dist, rp.TREND_BREAKPOINTS, rp.TREND_SCORES)
    risk = np.where(atrp.isna(), np.nan, risk)
    bucket = np.select([atrp.isna(), atrp <= 0.022, atrp <= 0.030, atrp <= 0.042], [4, 1, 2, 3], 4)
    return risk, bucket, trend


def test_formeln_entsprechen_prof_py():
    rng = np.random.default_rng(11)
    n = 300
    c = 40 * np.cumprod(1 + rng.normal(0.0002, 0.02, n))
    o = c * (1 + rng.normal(0, 0.005, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.012, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.012, n)))
    frame = pd.DataFrame({'Open': o, 'High': h, 'Low': l, 'Close': c},
                         index=pd.bdate_range('2023-01-02', periods=n))
    prof = Prof(frame.copy())                    # no symbol: scores the frame itself
    risk, bucket, trend = _pine_mirror(frame)
    np.testing.assert_allclose(prof.df['riskScore'].to_numpy(), risk, rtol=1e-9, equal_nan=True)
    np.testing.assert_allclose(prof.df['trendScore'].to_numpy(), trend, rtol=1e-9)
    np.testing.assert_array_equal(prof.df['riskBucket'].to_numpy(), bucket)


def test_kalibrierung_kommt_aus_risk_profile():
    s = pe.PineExporter([], ['prof']).generate_oscillator_single('prof')
    assert f'var prof_risk_x = array.from({_floats(rp.ATR_BREAKPOINTS)})' in s
    assert f'var prof_risk_y = array.from({_floats(rp.BREAKPOINT_SCORES)})' in s
    assert f'var prof_trend_x = array.from({_floats(rp.TREND_BREAKPOINTS)})' in s
    cuts = [p['max_atr_pct'] for p in sorted(rp.PROFILES.values(), key=lambda p: p['order'])
            if p['max_atr_pct'] is not None]
    for cut in cuts:
        assert f'atrp <= {float(cut)} ?' in s


def test_oszillator_tagesbasis_und_darstellung():
    class _Conf:
        def get_plugin_params(self, name):
            return {'atr_period': 21, 'color_trend': '#112233'}
    s = pe.PineExporter([], ['prof'], sys_conf=_Conf()).generate_oscillator_single('prof')
    assert 'input.int(21, "ATR period for the risk score"' in s
    assert 'request.security(syminfo.tickerid, "D", prof_daily(prof_len)' in s
    assert 'input.color(color.rgb(17, 34, 51), "Trend score"' in s
    assert 'plot(prof_bucket, "Risk bucket", color.gray, display = show_prof ? display.data_window : display.none)' in s
    assert 'display = display.none)' in s and 'display.none : display.none' not in s
    assert 'render error' not in s


def test_kombinierter_chart_und_formelspalten():
    s = pe.PineExporter([], ['prof']).generate_combined()
    assert '_osc_px(_prof_trend, 0.0, 100.0, 0)' in s and 'not yet implemented' not in s
    st = pse.export_strategy('p', '(riskBucket>=1)&(riskBucket<=2)&(trendScore>=65)',
                             '(riskScore<20)|(atr/close>0.06)')
    assert 'atr = ta.sma(ta.tr(true), 14)' in st
    assert 'riskBucket = risk_bucket_of(risk_atr_pct)' in st
    assert st.count('pw_interp(float x,') == 1               # shared helper emitted once
    sig = pe.PineExporter([], []).generate_signal_overlay('(riskBucket<=2)&(trendScore>=65)', '(riskScore<20)')
    assert 'sig_buy_raw  = (str_riskBucket<=2) and (str_trendScore>=65)' in sig
    declared = set(re.findall(r'^(?:var\s+(?:[\w<>]+\s+)?)?(\w+)\s*(?::?=|\(.*\)\s*=>)', sig, re.M))
    assert not set(re.findall(r'\bstr_\w+', sig)) - declared
