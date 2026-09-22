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


# --- rule refinements (2026-09-22) -------------------------------------------

LEAD = TEXTBOOK[:2]


def done_forms(inst):
    return [(lb['text'], lb['form']) for lb in inst.wave_labels
            if lb['state'] == 'done' and lb['group'] == 'abc']


def test_no_two_pivots_on_the_same_bar():
    df = make_df([(0, 100), (50, 150), (80, 150)])
    # One wide bar: new high first, then a crash to the close.
    df.iloc[50, df.columns.get_loc('High')] = 152.0
    df.iloc[50, df.columns.get_loc('Low')] = 118.0
    df.iloc[50, df.columns.get_loc('Close')] = 119.0
    df.iloc[51:, df.columns.get_loc('Close')] = np.linspace(121, 140, len(df) - 51)
    df['High'] = np.maximum(df['High'], df['Close'] + 0.5)
    df['Low'] = np.minimum(df['Low'], df['Close'] - 0.5)
    close = df['Close'].to_numpy()
    atr = _elliott.atr(df['High'], df['Low'], close)
    pv, _ = _elliott.pivots(df['High'], df['Low'], close, atr, 3.0)
    bars = [p['i'] for p in pv]
    assert 50 in bars
    assert all(a < b for a, b in zip(bars, bars[1:]))


def test_truncated_fifth_only_when_allowed():
    points = LEAD + [(50, 130), (62, 115), (92, 190), (104, 165), (120, 186),
                     (136, 150), (146, 165), (160, 130), (175, 150)]
    assert done(Ewo(make_df(points))) == []
    labels = [t for t, _ in done(Ewo(make_df(points), allow_truncation=True))]
    assert labels == ['1', '2', '3', '4', '5', 'A', 'B', 'C']


def test_textbook_correction_is_a_zigzag(df):
    assert done_forms(Ewo(df)) == [('A', 'zigzag'), ('B', 'zigzag'), ('C', 'zigzag')]


def test_expanded_flat_b_beyond_end_of_wave_5():
    # B at 206 retraces 130 % of A (200 -> 180): expanded flat, not a new impulse.
    points = LEAD + [(50, 130), (62, 115), (92, 190), (104, 165), (124, 200),
                     (136, 180), (148, 206), (166, 170), (180, 190)]
    assert done_forms(Ewo(make_df(points))) == [('A', 'flat'), ('B', 'flat'), ('C', 'flat')]


def test_b_far_beyond_wave_5_ends_the_correction():
    points = LEAD + [(50, 130), (62, 115), (92, 190), (104, 165), (124, 200),
                     (136, 180), (148, 215), (166, 170), (180, 190)]
    assert done_forms(Ewo(make_df(points))) == []


def test_c_must_go_beyond_the_end_of_a():
    points = LEAD + [(50, 130), (62, 115), (92, 190), (104, 165), (124, 200),
                     (136, 170), (146, 190), (156, 178), (170, 195)]
    assert done_forms(Ewo(make_df(points))) == []


def test_wave_4_ewo_zero_rule_is_switchable():
    # Short, shallow wave 4: the EWO never gets back to zero.
    points = LEAD + [(50, 130), (62, 115), (92, 190), (97, 180), (121, 220),
                     (133, 195), (143, 208), (157, 170), (172, 190)]
    loose = [t for t, _ in done(Ewo(make_df(points))) if t.isdigit()]
    strict = [t for t, _ in done(Ewo(make_df(points), require_w4_ewo_zero=True)) if t.isdigit()]
    assert loose == ['1', '2', '3', '4', '5']
    assert strict == []


def test_joseph_basis_leaves_the_ewo_column_alone(df):
    plain = Ewo(df)
    joseph = Ewo(df, wave_ewo_basis='joseph_5_35')
    assert np.allclose(plain.df['ewo'], joseph.df['ewo'], equal_nan=True)
    assert [t for t, _ in done(joseph)] == ['1', '2', '3', '4', '5', 'A', 'B', 'C']


def test_joseph_ewo_formula():
    df = make_df(TEXTBOOK)
    mid = (df['High'] + df['Low']) / 2
    expected = (mid.rolling(5).mean() - mid.rolling(35).mean()).to_numpy()
    assert np.allclose(_elliott.joseph_ewo(df), expected, equal_nan=True)


def test_new_options_keep_the_wave_column_causal(df):
    kw = dict(allow_truncation=True, require_w4_ewo_zero=True, wave_ewo_basis='joseph_5_35')
    full = Ewo(df, **kw).df['ewo_wave'].to_numpy()
    for cut in range(40, len(df), 7):
        part = Ewo(df.iloc[:cut], **kw).df['ewo_wave'].to_numpy()
        assert np.array_equal(part, full[:cut]), cut
