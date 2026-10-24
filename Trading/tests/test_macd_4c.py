"""MACD 4C variant: switchable drawing, columns unchanged, Pine export."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import pine_exporter as pe
from tradinglib.indicator.macd import Macd


def _frame(n=120):
    rng = np.random.default_rng(5)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    df = pd.DataFrame({'Close': close}, index=pd.bdate_range('2026-01-01', periods=n))
    df.index.name = 'Date'
    return df


def test_vier_farben_wie_im_original():
    colors = Macd.colors_4c([np.nan, 1.0, 2.0, 1.5, -1.0, -2.0, -1.5])
    # no previous bar: "rising" is false, as macd > macd[1] with na in Pine
    assert colors == ['lightgrey', 'green', 'lime', 'green', 'maroon', 'maroon', 'red']


def test_spalten_unabhaengig_vom_schalter():
    a, b = Macd(_frame()), Macd(_frame(), variant_4c=True)
    pd.testing.assert_frame_equal(a.df, b.df)


def test_standard_bleibt_histogramm():
    m = Macd(_frame())
    m.add_fig()
    assert [t.name for t in m.fig.data] == ['MACD diff', 'MACD', 'MACD signal']


def test_4c_zeigt_macd_als_farbige_balken():
    m = Macd(_frame(), variant_4c='true')                 # stored configs may hold strings
    m.add_fig()
    names = [t.name for t in m.fig.data]
    assert names == ['MACD 4C', 'MACD signal']
    bars = m.fig.data[0]
    np.testing.assert_allclose(np.asarray(bars.y, dtype=float), m.df['macd'].to_numpy(), equal_nan=True)
    assert set(bars.marker.color) <= {'lime', 'green', 'maroon', 'red', 'lightgrey'}


class _Conf:
    def __init__(self, on):
        self.on = on

    def get_plugin_params(self, name):
        return {'variant_4c': self.on} if name == 'macd' else {}


def test_pine_einzelvorlage():
    s = pe.PineExporter([], ['macd'], sys_conf=_Conf(True)).generate_oscillator_single('macd')
    assert 'macd_4c     = input.bool(true, "MACD 4C (four-colour MACD bars)"' in s
    assert 'color.lime' in s and 'color.maroon' in s
    assert 'plot(macd_4c ? macd_line : na, "MACD 4C", color = macd_4c_col, style = plot.style_columns' in s
    assert 'plot(macd_4c ? na : macd_line, "MACD"' in s
    off = pe.PineExporter([], ['macd'], sys_conf=_Conf(False)).generate_oscillator_single('macd')
    assert 'macd_4c     = input.bool(false,' in off
    assert 'render error' not in s


def test_pine_kombinierter_chart():
    s = pe.PineExporter([], ['macd'], sys_conf=_Conf(True)).generate_combined()
    assert 'n_macd_4c      = input.bool(true,' in s
    assert '_macd_bar = n_macd_4c ? _macd_l : _macd_h' in s
    assert 'not yet implemented' not in s and 'render error' not in s
