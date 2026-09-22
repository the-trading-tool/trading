"""Pine export of the Order Block Detector and the EWO incl. Elliott count.

Pine cannot be compiled here, so these tests pin the translation: the
templates follow the indicator params, drawings (and only drawings) are gated
by the visibility toggle, the rule constants stay in sync with the Python
implementation and the formula columns reach strategy/signal scripts.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import pine_exporter as pe
from tradinglib.indicator import _elliott
from tradinglib.indicator.obd import Obd


class _Conf:
    def __init__(self, params):
        self.params = params

    def get_plugin_params(self, name):
        return self.params.get(name, {})


def _exporter(params=None):
    return pe.PineExporter(['obd'], ['ewo'], sys_conf=_Conf(params or {}))


def _gated_ifs(script, toggle):
    return [ln for ln in script.splitlines() if ln.startswith(f'if {toggle} and')]


def _lint(script):
    """Block indentation in steps of 4, no tabs, balanced brackets per line."""
    for n, ln in enumerate(script.splitlines(), 1):
        assert '\t' not in ln, n
        if ln.strip():
            assert (len(ln) - len(ln.lstrip())) % 4 == 0, (n, ln)
        code = re.sub(r'"[^"]*"', '""', ln)
        code = code.split('//')[0]
        assert code.count('(') == code.count(')'), (n, ln)
        assert code.count('[') == code.count(']'), (n, ln)


def test_obd_overlay_folgt_den_parametern():
    s = _exporter({'obd': {'ob_periods': 5, 'ob_threshold': 2.5, 'use_wicks': True,
                           'ob_extend': 'left', 'ob_min_weight': 40, 'sr_show': 'true'}}
                  ).generate_overlay()
    assert 'input.int(5, "Impulse candles"' in s
    assert 'input.float(2.5, "Min impulse move %"' in s
    assert 'input.bool(true, "Zone incl. wicks' in s
    assert 'input.string("fixed", "Extend"' in s           # legacy value as obd.py
    assert 'input.int(40, "Min weight' in s
    assert 'input.bool(true, "Show", group="Order Block Detector -- S/R flow signal")' in s
    assert 'box.new(' in s and 'box.set_border_style' in s
    # the old contrarian S/R triangles are gone
    assert 'OBD Bullish OB' not in s and 'obd_ofi' not in s


def test_obd_nur_zeichnungen_haengen_am_schalter():
    s = _exporter().generate_overlay()
    gated = _gated_ifs(s, 'show_obd')
    assert len(gated) == 2                                   # new zones + mitigation restyle
    assert all('obd_born' in g or 'obd_gone' in g for g in gated)
    # zone tracking runs regardless of visibility
    assert 'if array.size(obd_active) > 0' in s
    assert 'if bar_index >= obd_p' in s
    _lint(s)


def test_obd_spalten_im_datenfenster_ohne_doppeltes_display():
    s = _exporter().generate_overlay()
    for col in ('obd_bull_top', 'obd_bull_bot', 'obd_bull_weight',
                'obd_bear_top', 'obd_bear_bot', 'obd_bear_weight'):
        line = next(ln for ln in s.splitlines() if ln.startswith(f'plot({col},'))
        assert line.count('display') == 3                    # display = show ? display.data_window : display.none
        assert 'display = show_obd ? display.data_window : display.none' in line


def test_obd_konstanten_wie_python():
    s = _exporter().generate_overlay()
    assert f'/ {float(Obd.ATR_PERIOD)}' in s
    vol_loop = f'for j = obd_p + 1 to obd_p + {Obd.VOL_PERIOD}'
    assert vol_loop in s
    assert 'math.sqrt(math.max(0.5, math.min(2.0, rel)))' in s


def test_ewo_fenster_winkel_und_abwechselnde_signale():
    s = _exporter({'ewo': {'short_window': 7, 'long_window': 34, 'ema_span': 12, 'angle': 0.2}}
                  ).generate_oscillator_single('ewo')
    assert 'input.int(7, "Short SMA period"' in s
    assert 'input.int(34, "Long SMA period"' in s
    assert 'input.int(12, "EMA span"' in s
    assert 'math.todegrees(math.atan(ewo_val - ewo_val[1]))' in s   # ewo_angle is degrees
    assert 'var int ewo_pos = 0' in s                                # strictly alternating
    _lint(s)


def test_ewo_elliott_schalter_aus_der_config():
    s = _exporter({'ewo': {'show_waves': 'True', 'show_abc': False, 'require_ewo_peak': 'false',
                           'allow_truncation': True, 'wave_ewo_basis': 'joseph_5_35',
                           'wave_atr_mult': 4.5}}).generate_oscillator_single('ewo')
    assert 'ew_show_waves   = input.bool(true' in s
    assert 'ew_show_abc     = input.bool(false' in s
    assert 'ew_req_peak     = input.bool(false' in s
    assert 'ew_trunc        = input.bool(true' in s
    assert 'input.string("joseph_5_35", "EWO for the wave rules"' in s
    assert 'input.float(4.5, "Pivot reversal (x ATR)"' in s


def test_ewo_elliott_regeln_wie_python():
    s = _exporter().generate_oscillator_single('ewo')
    assert f'ew_max_cands = {_elliott.MAX_CANDIDATES}' in s
    assert f'ew_b_min     = {_elliott.FLAT_B_MIN}' in s
    assert f'ew_b_max     = {_elliott.FLAT_B_MAX}' in s
    # labels far in the past must use time coordinates
    assert s.count('xloc = xloc.bar_time') == 2
    gated = _gated_ifs(s, 'show_ewo')
    assert len(gated) == 2                                   # finished labels + right edge
    assert 'if ew_new_k >= 0' in s                           # counting is never gated
    assert 'plot(ew_wave, "Elliott wave (ewo_wave)", color.gray, display = show_ewo ? display.data_window : display.none)' in s


def test_kombinierter_chart_nutzt_die_ewo_fenster():
    e = pe.PineExporter([], ['ewo'], sys_conf=_Conf({'ewo': {'short_window': 6, 'long_window': 30,
                                                              'ema_span': 8, 'angle': 0.3}}))
    s = e.generate_combined()
    assert 'ta.sma(close, 6) - ta.sma(close, 30)' in s
    assert 'ta.ema(_ewo_v, 8)' in s
    assert '_ewo_ang > 0.3' in s


def test_strategie_bekommt_ewo_wave_und_obd_spalten():
    e = _exporter({'ewo': {'wave_atr_mult': 2.5, 'wave_ewo_basis': 'joseph_5_35'},
                   'obd': {'ob_periods': 4}})
    s = e.generate_strategy('(ewo_wave == 3) & (close > obd_bull_top)', '(obd_sell > 0)')
    assert 'strat_buy  = (str_ew_wave == 3) and (close > str_obd_bull_top)' in s
    assert 'strat_sell = (str_obd_sell > 0)' in s
    # ewo_wave pulls in the EWO block first; its params come from the ewo config
    assert s.index('str_ewo       =') < s.index('str_ew_mult')
    assert 'str_ew_mult     = 2.5' in s
    assert 'str_ew_basis    = str_ew_joseph' in s
    assert 'str_obd_p      = 4' in s
    # strategy scripts carry no drawings from these blocks
    assert 'box.new(' not in s and 'label.new(' not in s
    calc = s.split('// ── Indicator computations')[1].split('// ── Buy / Sell conditions')[0]
    _lint(calc.split('\n', 1)[1])


def test_ewo_angle_im_signalexport_in_grad():
    s = _exporter().generate_signal_overlay('(ewo_angle > 1)', '')
    assert 'str_ewo_ang   = math.todegrees(math.atan(str_ewo - str_ewo[1]))' in s


def test_display_parameter_wird_nicht_verdoppelt():
    body = 'plot(x, "x", color.red, display = display.data_window)\nplot(y, "y")\n'
    out = pe._add_visibility_toggle('obd', body)
    assert 'plot(x, "x", color.red, display = show_obd ? display.data_window : display.none)' in out
    assert 'plot(y, "y", display = show_obd ? display.all : display.none)' in out


def test_signal_export_kennt_die_spalten_des_strategie_exports():
    """Screenshot 2026-09-22: 'Undeclared identifier trendScore' im Overlay-Signal."""
    buy = ('(((trendScore>=65)&(ewo>ewo_ema))|((Low<atc_bot_low)&(ewo>0))|((close<=sup_support)))'
           '&(relvol_ratio > 0.5)&(High<atc_mid_zero)&(close>Open)')
    s = pe.PineExporter(['atc'], []).generate_overlay(
        include_signals=True, buy_query=buy, sell_query='(High>=atc_top_high)\n(rsi>=75)')
    assert 'sig_buy_raw  = (((str_trendScore>=65) and (str_ewo>str_ewo_ema))' in s
    assert 'str_trendScore = str_trend_interp(' in s
    assert 'str_sup_support = ta.lowest(low, 21)' in s
    assert 'sig_sell_raw = (high>=str_atc_top_high) and (str_rsi>=75)' in s   # lines ANDed
    declared = set(re.findall(r'^(?:var\s+(?:[\w<>]+\s+)?)?(\w+)\s*(?::?=|\(.*\)\s*=>)', s, re.M))
    for m in re.findall(r'^\[(.+?)\]\s*=', s, re.M):
        declared |= {x.strip() for x in m.split(',')}
    assert not {u for u in re.findall(r'\bstr_\w+', s)} - declared


def test_signal_export_fundamentalscore_wird_false_statt_compilerfehler():
    s = pe.PineExporter([], []).generate_signal_overlay(
        '(overallValueTrend>=1.1*overallTrend)&(ewo>=ewo_ema)', '(rsi>=75)')
    assert 'sig_buy_raw  = false' in s
    assert '// nicht exportierbar (overallTrend, overallValueTrend)' in s
    assert 'sig_sell_raw = (str_rsi>=75)' in s
