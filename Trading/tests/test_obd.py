import numpy as np
import pandas as pd

from tradinglib.indicator.obd import Obd


def _frame(rows):
    """rows: list of (open, close); high/low one point outside the body."""
    dates = pd.bdate_range('2026-01-05', periods=len(rows)).strftime('%Y-%m-%d')
    o = np.array([r[0] for r in rows], dtype=float)
    c = np.array([r[1] for r in rows], dtype=float)
    return pd.DataFrame({'Open': o, 'Close': c,
                         'High': np.maximum(o, c) + 1, 'Low': np.minimum(o, c) - 1,
                         'Volume': 1000.0}, index=pd.Index(dates, name='Date'))


def _bull_setup():
    flat = [(100, 100.5), (100.5, 100)] * 10
    # red block candle, three green impulse candles, then drift, then a break below
    return flat + [(101, 99), (99, 102), (102, 105), (105, 108)] + [(108, 108.5)] * 5 + [(108, 97)]


def test_bull_block_zone_is_candle_body():
    ind = Obd(df=_frame(_bull_setup()), ob_threshold=1.0)
    bulls = [z for z in ind.zones if z['kind'] == 'bull']
    assert len(bulls) == 1
    z = bulls[0]
    assert (z['bot'], z['top']) == (99.0, 101.0)
    assert 0 < z['weight'] <= 100


def test_use_wicks_takes_full_range():
    ind = Obd(df=_frame(_bull_setup()), use_wicks=True)
    z = [z for z in ind.zones if z['kind'] == 'bull'][0]
    assert (z['bot'], z['top']) == (98.0, 102.0)


def test_mitigation_on_close_below_zone():
    ind = Obd(df=_frame(_bull_setup()))
    z = [z for z in ind.zones if z['kind'] == 'bull'][0]
    assert z['end'] == len(ind.df) - 1
    # active zone columns exist until mitigation, then disappear
    assert ind.df['obd_bull_top'].iloc[-2] == 101.0
    assert np.isnan(ind.df['obd_bull_top'].iloc[-1])


def test_active_zone_known_only_after_confirmation():
    ind = Obd(df=_frame(_bull_setup()))
    z = [z for z in ind.zones if z['kind'] == 'bull'][0]
    assert np.isnan(ind.df['obd_bull_top'].iloc[z['confirm'] - 1])
    assert ind.df['obd_bull_top'].iloc[z['confirm']] == 101.0


def test_no_look_ahead():
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1.2, 400))
    opn = np.r_[close[0], close[:-1]] + rng.normal(0, 0.4, 400)
    df = pd.DataFrame({'Open': opn, 'Close': close,
                       'High': np.maximum(opn, close) + 0.5, 'Low': np.minimum(opn, close) - 0.5,
                       'Volume': rng.integers(500, 1500, 400).astype(float)},
                      index=pd.Index(pd.bdate_range('2024-01-01', periods=400).strftime('%Y-%m-%d'), name='Date'))
    cols = ['obd_bull_top', 'obd_bull_bot', 'obd_bull_weight', 'obd_bear_top',
            'obd_bear_weight', 'obd_buy', 'obd_sell']
    full = Obd(df=df).df[cols]
    cut = Obd(df=df.iloc[:300]).df[cols]
    pd.testing.assert_frame_equal(full.iloc[:300], cut)


def test_weight_filter_and_plot():
    ind = Obd(df=_frame(_bull_setup()), ob_min_weight=101)
    assert ind.df['obd_bull_top'].isna().all()
    ind.add_fig()
    assert len(ind.fig.layout.shapes) == 0

    ind = Obd(df=_frame(_bull_setup()), ob_zone_scale=2.0, sr_show=False)
    ind.add_fig()
    rect = ind.fig.layout.shapes[0]
    assert (rect.y0, rect.y1) == (98.0, 102.0)
    assert all(tr.name.startswith('obd_ob') for tr in ind.fig.data)


def test_legacy_params_accepted():
    ind = Obd(df=_frame(_bull_setup()), ob_extend='left', sr_min_gap=0.5)
    assert ind.ob_extend == 'fixed'
