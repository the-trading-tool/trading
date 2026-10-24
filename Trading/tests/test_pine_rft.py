"""Pine export of the Range Filtered Trend (rft.py, AlgoAlpha port)."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import pine_exporter as pe


class _Conf:
    def __init__(self, params):
        self.params = params

    def get_plugin_params(self, name):
        return self.params if name == 'rft' else {}


def _declared(script):
    return set(re.findall(r'^(?:var\s+(?:[\w<>]+\s+)?)?(\w+)\s*(?::?=|\(.*\)\s*=>)', script, re.M)) | \
        {n.strip() for grp in re.findall(r'^\[(.+?)\]\s*=', script, re.M) for n in grp.split(',')}


def test_overlay_uebernimmt_die_chart_parameter():
    conf = _Conf({'kalman_alpha': 0.02, 'kalman_period': 50, 'deviation': 1.5,
                  'st_factor': 0.9, 'st_atr_period': 10, 'color_bull': '#112233',
                  'show_range': False})
    s = pe.PineExporter(['rft'], [], sys_conf=conf).generate_overlay()
    assert 'rft_alpha  = input.float(0.02, "Kalman alpha"' in s
    assert 'rft_period = input.int(50, "Kalman period"' in s
    assert 'rft_dev    = input.float(1.5, "Deviation"' in s
    assert 'rft_stf    = input.float(0.9, "Supertrend factor"' in s
    assert 'rft_atrp   = input.int(10, "Supertrend ATR period"' in s
    assert 'input.color(color.rgb(17, 34, 51), "Bullish color"' in s
    assert 'input.bool(false, "Fill the range band"' in s
    assert 'Attribution: AlgoAlpha' in s and 'Mozilla Public License 2.0' in s
    assert 'render error' not in s and 'not yet implemented' not in s


def test_alle_zeichnungen_haengen_am_sichtbarkeitsschalter():
    s = pe.PineExporter(['rft'], []).generate_overlay()
    assert 'show_rft = input.bool(true, "Show Range Filtered Trend"' in s
    for call in ('plotchar(', 'barcolor(', 'fill(rft_p1', 'fill(rft_up', 'rft_up = plot('):
        line = next(ln for ln in s.splitlines() if ln.startswith(call))
        assert 'display = show_rft ? display.all : display.none' in line, call
    assert 'plot(rft_state, "rft_state", color.gray, display = show_rft ? display.data_window : display.none)' in s
    assert 'rft_m  = plot(rft_mid, "Mid body", color.gray, display = display.none)' in s


def test_rechenkern_wie_im_original():
    """The core is AlgoAlpha's code with prefixed names — the pieces rft.py mirrors."""
    s = pe.PineExporter(['rft'], []).generate_overlay()
    assert 'rft_kalman = rft_kalman_fn(close, rft_period, rft_alpha, rft_beta)' in s
    assert '[rft_supertrend, rft_dir] = rft_st_fn(rft_kalman, rft_stf, rft_atrp)' in s
    assert 'rft_vola  = ta.wma(high - low, 200)' in s
    assert 'rft_state  = rft_ktrend * rft_trend' in s
    assert 'rft_signal = ta.crossover(rft_state, 0) ? rft_trend : 0' in s
    assert not ({'rft_kalman', 'rft_upper', 'rft_lower', 'rft_trend', 'rft_ktrend',
                 'rft_state', 'rft_signal', 'rft_supertrend'} - _declared(s))


def test_formelspalten_im_signal_export():
    conf = _Conf({'kalman_period': 60})
    sig = pe.PineExporter([], [], sys_conf=conf).generate_signal_overlay(
        '(rft_signal == 1) & (rft_state == 1)', '(rft_trend == -1) | (close < rft_lower)')
    assert 'sig_buy_raw  = (str_rft_signal == 1) and (str_rft_state == 1)' in sig
    assert 'sig_sell_raw = (str_rft_trend == -1) or (close < str_rft_lower)' in sig
    assert 'str_rft_period = 60' in sig                     # chart params, rft is not stored
    assert not set(re.findall(r'\bstr_rft_\w+', sig)) - _declared(sig)
    assert 'unbekannte Spalten' not in sig


def test_overlay_und_signal_zusammen_ohne_namenskonflikt():
    """Overlay (rft_*) and signal block (str_rft_*) in one script must not clash."""
    exp = pe.PineExporter(['rft'], [])
    s = exp.generate_overlay() + exp.generate_signal_overlay('(rft_state == 1)', '(rft_state == -1)')
    funcs = re.findall(r'^(\w+)\(.*\)\s*=>', s, re.M)
    assert len(funcs) == len(set(funcs))
