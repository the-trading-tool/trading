"""Ewo: causal Elliott wave count and config-switchable chart labels.

Run: .venv/Scripts/python.exe -m pytest tests/test_ewo_waves.py -q
"""
import numpy as np
import pandas as pd
import pytest

from tradinglib.indicator import _elliott
from tradinglib.indicator.ewo import Ewo

# (bar, price): lead-in decline, impulse 0-5, correction A-B-C, recovery.
TEXTBOOK = [(0, 130), (30, 100), (50, 130), (62, 115), (92, 190), (104, 165),
            (124, 200), (136, 175), (146, 188), (160, 150), (175, 175)]
PIVOT_BARS = [50, 62, 92, 104, 124, 136, 146, 160]


def make_df(points):
    bars, prices = zip(*points)
    close = np.interp(np.arange(bars[-1] + 1), bars, prices)
    idx = pd.date_range('2025-01-01', periods=len(close), freq='D').strftime('%Y-%m-%d %H:%M:%S')
    return pd.DataFrame({'Open': close, 'High': close + 0.5, 'Low': close - 0.5,
                         'Close': close, 'Volume': 1000},
                        index=pd.Index(idx, name='Date'))


@pytest.fixture
def df():
    return make_df(TEXTBOOK)


def done(inst):
    return [(lb['text'], lb['i']) for lb in inst.wave_labels if lb['state'] == 'done']


def test_textbook_impulse_and_correction_are_labelled(df):
    inst = Ewo(df)
    assert done(inst) == list(zip(['1', '2', '3', '4', '5', 'A', 'B', 'C'], PIVOT_BARS))


def test_wave_column_reports_the_running_wave(df):
    wave = Ewo(df).df['ewo_wave'].to_numpy()
    assert wave[80] == 3        # waves 1 and 2 confirmed, wave 3 under way
    assert wave[118] == 5
    assert wave[140] == 6       # impulse complete, correction A under way


def test_wave_column_has_no_look_ahead(df):
    full = Ewo(df).df['ewo_wave'].to_numpy()
    for cut in range(40, len(df), 7):
        part = Ewo(df.iloc[:cut]).df['ewo_wave'].to_numpy()
        assert np.array_equal(part, full[:cut]), cut


def test_wave_4_overlapping_wave_1_is_rejected():
    points = list(TEXTBOOK)
    points[5] = (104, 125)      # wave 4 low below the wave 1 high (130)
    assert not [t for t, _ in done(Ewo(make_df(points))) if t.isdigit()]


def test_ewo_peak_rule_is_switchable():
    # Steep, long wave 5 carries the EWO peak -> rejected only with the rule.
    points = list(TEXTBOOK)
    points[6] = (150, 290)
    points[7:] = [(162, 265), (172, 278), (186, 240), (200, 265)]
    strict = [t for t, _ in done(Ewo(make_df(points))) if t.isdigit()]
    loose = [t for t, _ in done(Ewo(make_df(points), require_ewo_peak=False)) if t.isdigit()]
    assert strict == []
    assert loose == ['1', '2', '3', '4', '5']


def chart_texts(inst):
    inst.add_fig()
    return [t.strip('<b>/') for tr in inst.fig.data if tr.name == 'Elliott' for t in tr.text]


def test_labels_are_off_by_default(df):
    assert chart_texts(Ewo(df)) == []


def test_labels_follow_the_config_switches(df):
    all_labels = chart_texts(Ewo(df, show_waves=True))
    assert {'1', '2', '3', '4', '5', 'A', 'B', 'C'} <= set(all_labels)
    no_abc = chart_texts(Ewo(df, show_waves=True, show_abc=False))
    assert not {'A', 'B', 'C'} & set(no_abc)
    no_pending = chart_texts(Ewo(df, show_waves=True, show_pending=False))
    assert not [t for t in no_pending if t.endswith('?')]


def test_params_schema_matches_constructor():
    import inspect
    accepted = set(inspect.signature(Ewo.__init__).parameters)
    assert set(Ewo.params) <= accepted


def test_pivots_are_confirmed_after_their_extreme(df):
    close = df['Close'].to_numpy()
    atr = _elliott.atr(df['High'], df['Low'], close)
    pv, _ = _elliott.pivots(df['High'], df['Low'], close, atr, 3.0)
    assert all(p['c'] > p['i'] for p in pv)
    assert [p['kind'] for p in pv] == [(-1) ** k for k in range(len(pv))]
