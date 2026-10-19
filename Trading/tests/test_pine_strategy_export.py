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


def test_shift_wird_vorbalken():
    """sup_support.shift(1) = Vortag in der App -> sup_support[1] in Pine."""
    assert pse.shift_to_history('close < x.shift(2)') == 'close < x[2]'
    assert pse.shift_to_history('x.shift()') == 'x[1]'
    assert pse.shift_to_history('x.shift( 3 )') == 'x[3]'
    assert pse.shift_to_history('x.shift(-1)') == 'x.shift(-1)'   # future bar: no Pine form
    buy = '(close*1.015<=sup_support.shift(1))&(ewo>ewo_ema)&(dTrend>0)&(wkTrend>0)'
    s = pse.export_strategy('sup', buy, '(close>sup_resistance.shift(1)*0.985)|(ewo<ewo_ema)')
    assert 'close*1.015<=sup_support[1]' in s
    assert 'close>sup_resistance[1]*0.985' in s
    assert 'sup_support = ta.lowest(low, 21)' in s
    assert 'shift' not in s
    _lint(s)


def test_negativer_shift_wird_abgelehnt():
    with pytest.raises(pse.StrategyExportError, match='shift'):
        pse.export_strategy('x', '(close<sup_support.shift(-1))', '(rsi<30)')


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
    assert f"input.int({Atc.params['lookback']['default']}, \"ATC: channel lookback per bar" in s
    assert f"input.float({float(Atc.params['dev_multi']['default'])}, \"ATC: channel deviation multiplier\"" in s


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


_RULES = {'invest': 6000, 'num_assets': 4, 'trailing_stop': 20, 'stop_loss': 8,
          'min_hold_days': 2, 'cooldown_days': 3, 'fractional': True,
          'sizing_cap': 'factor', 'sizing_factor_max': 2, 'source': 'Regeln von ^SPX'}


def test_regeln_wie_portfoliosimulator():
    s = pse.export_strategy('R', '(rsi>50)', '(rsi<30)', rules=_RULES)
    head = s.splitlines()[1]
    assert 'initial_capital=6000.0' in head and 'process_orders_on_close=true' in head
    assert 'r_slot     = input.float(1500.0,' in s                  # invest / num_assets
    assert 'r_sl_pct   = input.float(8.0,' in s
    assert 'r_tr_pct   = input.float(20.0,' in s
    assert 'r_min_hold = input.int(2,' in s and 'r_cooldown = input.int(3,' in s
    assert 'r_frac     = input.bool(true,' in s
    assert '[1/2, 2] geklammert' in s
    # hard stop at the entry price (a stop order: level or open on a gap)
    assert 'strategy.exit("SL", "Long", stop = (r_in_pos ? strategy.position_avg_price : close)' in s
    # min hold gates only the regular sell; a sell signal beats a buy
    assert 'else if r_in_pos and exitCond and r_hold_ok' in s
    assert 'longCond and not exitCond and r_cool_ok' in s
    assert s.index('strategy.exit("SL"') < s.index('comment = "Trail"') < s.index('comment = "Sell"')
    _lint(s)


def test_ohne_regeln_bleibt_das_einfache_skript():
    s = pse.export_strategy('R', '(rsi>50)', '(rsi<30)')
    assert 'default_qty_type=strategy.percent_of_equity' in s and 'r_slot' not in s
    assert 'if longCond and not exitCond and strategy.position_size == 0' in s


def test_regeln_fallen_auf_die_config_zurueck(monkeypatch):
    cfg = {'stop_loss_pct': '5', 'min_hold_days': '1', 'cooldown_days': '4',
           'fractional_shares': 'true', 'sizing_cap': '"factor"'}
    monkeypatch.setattr(pse, '_config_value', lambda user, key: cfg.get(key))
    r = pse.rules_for({'invest': 1000, 'num_assets': 2, 'stop_loss': 0, 'min_hold_days': None},
                      'kurt', source='x')
    assert r['stop_loss'] == 0                      # per-index value beats the global default
    assert r['min_hold_days'] == 1 and r['cooldown_days'] == 4
    assert r['fractional'] is True and r['sizing_cap'] == 'factor'


def test_abweichende_index_regeln_als_kommentar():
    tx = {'S': {'^SPX': {'buy': '(rsi>50)', 'sell': '(rsi<30)', 'invest': 6000, 'num_assets': 6},
                '^N225': {'buy': '(rsi>50)', 'sell': '(rsi<30)', 'invest': 1200, 'num_assets': 3}}}
    pine = pse.export_from_config(tx)['S']['pine']
    assert 'r_slot     = input.float(1000.0,' in pine
    assert '//   ^N225: invest=1200, num_assets=3' in pine


def test_signal_export_erklaert_die_atc_linien_statt_vor_lookahead_zu_warnen():
    from tradinglib.pine_exporter import PineExporter
    s = PineExporter([], []).generate_signal_overlay('(Low<atc_bot_low)', '(High>=atc_top_high)')
    assert 'exactly like the atc_* columns' in s
    assert '"ATC bottom (low anchor, per bar)"' in s
    low = s.lower()
    assert 'lookahead' not in low and 'look-ahead' not in low and 'cannot reproduce' not in low


def _pine_atc_line_mirror(y, e, length, wexp):
    """Python mirror of the overlay's atc_line(): [oldest, newest, stdev]."""
    ks = np.arange(length)
    w = 0.9 ** ks if wexp else np.ones(length)
    ys = y[e - ks]
    sw, swk, swkk = w.sum(), (w * ks).sum(), (w * ks * ks).sum()
    swy, swky = (w * ys).sum(), (w * ks * ys).sum()
    varx = swkk - swk * swk / sw
    b = (swky - swk * swy / sw) / varx if varx > 0 else 0.0
    a = (swy - b * swk) / sw
    r = ys - (a + b * ks)
    sd = np.sqrt(max((r * r).sum() / length - r.mean() ** 2, 0.0))
    return a + b * (length - 1), a, sd


@pytest.mark.parametrize('wexp', [False, True])
def test_atc_overlay_zeichnet_den_kanal_wie_atc_py(wexp):
    """Overlay = today's channel of atc.py (start, end, edges) for all three anchors."""
    from tradinglib.indicator.atc import Atc
    rng = np.random.default_rng(7)
    n = 400
    c = 80 * np.cumprod(1 + rng.normal(0.0003, 0.018, n))
    h = c * (1 + np.abs(rng.normal(0, 0.01, n)))
    l = c * (1 - np.abs(rng.normal(0, 0.01, n)))
    hist = pd.DataFrame({'High': h, 'Low': l, 'Close': c}, index=pd.bdate_range('2021-01-01', periods=n))
    atc = Atc.__new__(Atc)
    atc.use_gog_scale, atc.use_exp_weight, atc.dev_multi, atc.lookback = False, wexp, 2.0, 252
    e, wl = n - 1, 252
    mn = max(10, wl // 5)
    for name, anchor in (('high', 1), ('low', -1), ('zero', 0)):
        seg = atc._todays_channel(hist, name, hist.index)
        y_win = c[e - wl + 1:]
        length = atc._length(name, y_win - y_win.mean(), h[e - wl + 1:], l[e - wl + 1:], mn)
        assert len(seg) == length
        y0, y1, sd = _pine_atc_line_mirror(c, e, length, wexp)
        np.testing.assert_allclose([seg['mid'].iloc[0], seg['mid'].iloc[-1]], [y0, y1], rtol=1e-9)
        np.testing.assert_allclose([seg['top'].iloc[-1], seg['bot'].iloc[0]],
                                   [y1 + 2 * sd, y0 - 2 * sd], rtol=1e-9)


def test_atc_overlay_vorlage_folgt_der_konfiguration():
    from tradinglib.pine_exporter import PineExporter

    class _Conf:
        def get_plugin_params(self, name):
            return {'dev_multi': 1.5, 'lookback': 120, 'show_high_bot': True,
                    'show_width': False, 'use_exp_weight': 'true'}
    s = PineExporter(['atc'], [], sys_conf=_Conf()).generate_overlay()
    assert 'input.float(1.5, "ATC: channel deviation multiplier"' in s
    assert 'input.int(120, "ATC: channel lookback per bar (bars)"' in s
    assert 'atc_hi_bot   = input.bool(true,' in s and 'atc_hi_mid   = input.bool(false,' in s
    assert 'atc_width_on = input.bool(false,' in s and 'atc_wexp     = input.bool(true,' in s
    assert 'if show_atc and barstate.islast' in s
    assert 'atc_reg(' not in s and 'math.min(499' not in s       # old 500-bar anchor search gone
    assert s.count('atc_draw(') == 4                             # definition + three channels


def test_obd_im_strategie_export_nutzt_die_chart_einstellungen(monkeypatch):
    """obd has no stored backtest columns, so the chart's ⚙ settings apply."""
    import json
    cfg = {'plugin_params:obd': json.dumps({'ob_min_weight': 34, 'ob_periods': 4, 'use_wicks': 'true'}),
           'plugin_params:rsi': json.dumps({'lookback': 21})}
    monkeypatch.setattr(pse, '_config_value', lambda user, key: cfg.get(key))
    params = pse.chart_params('kurt')
    assert params == {'obd': {'ob_min_weight': 34, 'ob_periods': 4, 'use_wicks': 'true'}}
    s = pse.export_strategy('o', '(close > obd_bull_top) & (rsi > 50)', '(rsi < 30)', params=params)
    assert 'xobd_min_w  = 34.0' in s and 'xobd_p      = 4' in s and 'xobd_wicks  = true' in s
    assert 'rsi = ta.rsi(close, 8)' in s           # stored indicators keep the backtest defaults
    # without a user: class defaults
    assert 'xobd_min_w  = 0.0' in pse.export_strategy('o', '(close > obd_bull_top)', '(rsi < 30)')
    tx = {'S': {'^SPX': {'buy': '(close > obd_bull_top)', 'sell': '(rsi < 30)'}}}
    assert 'xobd_min_w  = 34.0' in pse.export_from_config(tx, username='kurt')['S']['pine']
