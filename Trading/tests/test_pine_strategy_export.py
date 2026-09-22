"""Strategy export (pine_strategy_export): formulas → TradingView strategy()."""
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import pine_strategy_export as pse


def _lint(script):
    """Block indentation in steps of 4 (except the strategy() continuation), balanced brackets."""
    for n, ln in enumerate(script.splitlines(), 1):
        assert '\t' not in ln, n
        if ln.strip() and not ln.lstrip().startswith('default_qty_value'):
            assert (len(ln) - len(ln.lstrip())) % 4 == 0, (n, ln)
        code = re.sub(r'"[^"]*"', '""', ln).split('//')[0]
        if not ln.startswith(('strategy(', '     default_qty')):
            assert code.count('(') == code.count(')'), (n, ln)
            assert code.count('[') == code.count(']'), (n, ln)


def test_fundamentalscore_bleibt_abgelehnt_mit_grund():
    with pytest.raises(pse.StrategyExportError) as e:
        pse.export_strategy('Value Trend ^2', '(overallValueTrend>=1.1*overallTrend)&(ewo>ewo_ema)', '(rsi>=75)')
    msg = str(e.value)
    assert '(overallTrend, overallValueTrend)' in msg
    assert 'Fundamentaldaten' in msg and 'andere Strategie' in msg
    assert msg.count('Fundamentaldaten') == 1                # one reason, not repeated per column


def test_support_rsi_mehrzeilig_mit_signalfenster():
    buy = '(close<=sup_support*1.02)\n(relvol_ratio > 1)\n(close>Open)\n(macd>macd_signal)'
    s = pse.export_strategy('Support/RSI', buy, '(High>=atc_top_high)\n(rsi>=72)', signal_window=2)
    assert 'longCond = (math.sum((close<=sup_support*1.02) ? 1 : 0, 2) > 0) and ' in s
    assert '(math.sum((macd>macd_signal) ? 1 : 0, 2) > 0)' in s
    assert '[macd, macd_signal, macd_diff] = ta.macd(close, 12, 26, 9)' in s
    assert 'relvol_ratio = na(volume) or na(relvol_past)' in s
    assert 'Signalfenster 2' in s
    _lint(s)


def test_mehrzeilig_ohne_fenster_ist_und():
    s = pse.export_strategy('x', '(rsi>50)\n(ewo>0)', '(rsi<30)')
    assert 'longCond = (rsi>50) and (ewo>0)' in s
    assert 'Signalfenster' not in s
    assert pse._paren('(a) and (b)') == '((a) and (b))'
    assert pse._paren('(a and b)') == '(a and b)'
    # literal \n from JSON counts as a line break, as in the app
    s2 = pse.export_strategy('x', '(rsi>50)\\n(ewo>0)', '(rsi<30)')
    assert 'longCond = (rsi>50) and (ewo>0)' in s2


def test_buy_query_mit_atc_mitten_und_trendscore():
    buy = ('(((trendScore>=65)&(ewo>ewo_ema))|((Low<atc_bot_low)&(ewo>0))|((close<=sup_support)))'
           '&(relvol_ratio > 0.5)&(High<atc_mid_zero)&(close>Open)')
    sell = '((High>=atc_top_high)&(rsi>=75)&(Low>atc_mid_high))'
    s = pse.export_strategy('buy_query', buy, sell)
    for anchor, a in (('high', 1), ('low', -1), ('zero', 0)):
        assert f'[atc_m_{anchor}, atc_s_{anchor}] = atc_calc({a})' in s
    assert s.count('atc_fit(int e, int len) =>') == 1        # helper emitted once
    assert 'trendScore = trend_interp(' in s
    assert 'var trend_x = array.from(-1.0, ' in s
    _lint(s)


def test_trendscore_stuetzstellen_aus_risk_profile():
    from tradinglib import risk_profile as rp
    s = pse.export_strategy('t', '(trendScore>=65)', '(rsi>70)')
    assert 'array.from(' + ', '.join(str(float(x)) for x in rp.TREND_BREAKPOINTS) + ')' in s
    assert 'array.from(' + ', '.join(str(float(y)) for y in rp.TREND_SCORES) + ')' in s


def test_parameter_wie_im_backtest_aus_den_klassen():
    from tradinglib.indicator.atc import Atc
    from tradinglib.indicator.rsi import Rsi
    s = pse.export_strategy('p', '(rsi>50)&(High<atc_mid_zero)', '(rsi<30)')
    assert f"ta.rsi(close, {Rsi.params['lookback']['default']})" in s
    assert f"input.int({Atc.params['lookback']['default']}, \"ATC Rueckblick" in s
    assert f"input.float({float(Atc.params['dev_multi']['default'])}, \"ATC Std-Abweichung\"" in s


def test_ewo_wave_und_obd_spalten():
    s = pse.export_strategy('w', '(ewo_wave == 3)&(close > obd_bull_top)', '(obd_sell > 0)')
    assert 'ewo_wave = xew_wave' in s and 'obd_bull_top = xobd_bull_top' in s
    assert s.index('ewo = ta.sma(close') < s.index('type XewCand')   # basis before the count
    assert 'box.new(' not in s and 'label.new(' not in s
    _lint(s)


def test_interne_bloecke_sind_keine_formelspalten():
    with pytest.raises(pse.StrategyExportError, match='unbekannte Spalten'):
        pse.export_strategy('x', '(_atc_high > 0)', '(rsi>1)')


def _pine_atc_mirror(c, h, l, anchor, lookback=252, dev=2.0):
    """Line-by-line Python mirror of the exported Pine ATC (atc_len/atc_fit)."""
    out = []
    for e in range(len(c)):
        wl = min(lookback, e + 1)
        mn = max(10, wl // 5)
        if wl < mn:
            out.append(np.nan)
            continue
        s, L = e - wl + 1, wl
        if anchor != 0:
            arr = h if anchor == 1 else l
            bi, best = s, None
            for j in range(s, e + 1):
                if best is None or (arr[j] > best if anchor == 1 else arr[j] < best):
                    best, bi = arr[j], j
            L = e - bi + 1
        elif wl > 2:
            sk = skk = sy = sky = 0.0
            best = None
            for k in range(wl - 1):
                y = c[e - k]
                sk += k; skk += k * k; sy += y; sky += k * y
                if k >= 1:
                    n = k + 1
                    varx = skk - sk * sk / n
                    sl = abs((sky - sk * sy / n) / varx) if varx > 0 else 0.0
                    if best is None or sl < best:
                        best, L = sl, k + 1
        L = max(mn, min(L, wl))
        ks = np.arange(L)
        ys = c[e - ks]
        sk, skk, sy, syy, sky = ks.sum(), (ks * ks).sum(), ys.sum(), (ys * ys).sum(), (ks * ys).sum()
        cov, varx = sky - sk * sy / L, skk - sk * sk / L
        b = cov / varx if varx > 0 else 0.0
        ssr = (syy - sy * sy / L) - b * cov
        out.append((sy - b * sk) / L + dev * np.sqrt(max(ssr, 0.0) / L))
    return np.array(out)


def test_atc_algorithmus_entspricht_atc_py():
    """The exported per-bar algorithm reproduces the causal columns of atc.py."""
    from tradinglib.indicator.atc import Atc
    rng = np.random.default_rng(3)
    n = 320
    c = 50 * np.cumprod(1 + rng.normal(0.0004, 0.02, n))
    h = c * (1 + np.abs(rng.normal(0, 0.01, n)))
    l = c * (1 - np.abs(rng.normal(0, 0.01, n)))
    hist = pd.DataFrame({'High': h, 'Low': l, 'Close': c}, index=pd.bdate_range('2021-01-01', periods=n))
    atc = Atc.__new__(Atc)
    atc.use_gog_scale, atc.use_exp_weight, atc.dev_multi, atc.lookback = False, False, 2.0, 252
    for name, a in (('high', 1), ('low', -1), ('zero', 0)):
        ref = atc._causal_columns(hist, name)[f'atc_top_{name}'].to_numpy()
        np.testing.assert_allclose(_pine_atc_mirror(c, h, l, a), ref, rtol=1e-9, equal_nan=True)


def test_signal_export_nutzt_den_kausalen_atc_ohne_namenskollision():
    from tradinglib.pine_exporter import PineExporter
    s = PineExporter(['atc'], []).generate_overlay(
        include_signals=True, buy_query='(Low<atc_bot_low)&(High<atc_mid_zero)',
        sell_query='(High>=atc_top_high)')
    assert 'sig_buy_raw  = (low<str_atc_bot_low) and (high<str_atc_mid_zero)' in s
    assert 'str_atc_fit(int e, int len) =>' in s
    assert 'str_atc_reg(' not in s                   # old anchor regression is gone
    assert s.count('\natc_dev =') == 1                # overlay's own atc_dev untouched
