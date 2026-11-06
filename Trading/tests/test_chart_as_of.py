"""Asset Viewer 'as of' date: the chart and its self-loading indicators see nothing later."""
import numpy as np
import pandas as pd

from tradinglib import four_ps
from tradinglib.fetch_data import FetchData


def _hist(n=900, start='2022-01-03'):
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(3)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.015, n))
    return pd.DataFrame({'Open': close, 'High': close * 1.01, 'Low': close * 0.99,
                         'Close': close, 'Volume': 1e6}, index=idx)


def test_cap_to_chart_keeps_the_last_bars_period():
    hist = _hist(40, '2026-01-01')
    daily = hist.index[:20]
    assert four_ps.cap_to_chart(hist, daily).index.max() == daily.max()
    # weekly bars dated Monday: the whole last week stays
    weekly = pd.DatetimeIndex(['2026-01-05', '2026-01-12', '2026-01-19'])
    assert four_ps.cap_to_chart(hist, weekly).index.max() == pd.Timestamp('2026-01-23')
    # intraday: that day's daily row stays, the next day does not
    intraday = pd.date_range('2026-01-14 09:00', periods=8, freq='h', tz='Europe/Berlin')
    assert four_ps.cap_to_chart(hist, intraday).index.max() == pd.Timestamp('2026-01-14')
    # string index on the chart side works too
    assert four_ps.cap_to_chart(hist, daily.strftime('%Y-%m-%d 00:00:00')).index.max() == daily.max()
    assert four_ps.cap_to_chart(pd.DataFrame(), daily).empty


def test_end_date_helpers():
    assert FetchData._end_cutoff(None) is None
    assert FetchData._end_cutoff(pd.Timestamp('2025-06-30').date()) == pd.Timestamp('2025-07-01')
    df = pd.DataFrame({'Close': [1, 2, 3]},
                      index=['2025-06-29 00:00:00', '2025-06-30 15:30:00', '2025-07-01 00:00:00'])
    assert FetchData._cut_at(df, pd.Timestamp('2025-07-01'))['Close'].tolist() == [1, 2]
    # 1y ending 120 days ago needs a longer load period than 1y
    today = pd.Timestamp('2026-11-06')
    assert FetchData._period_reaching('1y', '2026-07-09', today) == '2y'
    assert FetchData._period_reaching('1mo', '2026-11-01', today) == '3mo'
    assert FetchData._period_reaching('max', '2020-01-01', today) == 'max'


def test_atc_draws_the_channel_of_the_last_chart_bar(monkeypatch):
    from tradinglib.indicator.atc import Atc
    full = _hist()
    end = full.index[700]
    chart = full.loc[:end].iloc[-250:].copy()
    monkeypatch.setattr(four_ps, 'load_daily', lambda symbol, db_path=None: full)
    capped = Atc(chart.copy(), symbol='X')
    monkeypatch.setattr(four_ps, 'load_daily', lambda symbol, db_path=None: full.loc[:end])
    honest = Atc(chart.copy(), symbol='X')
    assert capped.channels.keys() == honest.channels.keys() and capped.channels
    for name, seg in capped.channels.items():
        pd.testing.assert_frame_equal(seg, honest.channels[name])
        assert seg.index.max() <= end
    for col in ('atc_top_high', 'atc_mid_high', 'atc_bot_low'):
        np.testing.assert_allclose(capped.df[col].to_numpy(float), honest.df[col].to_numpy(float))


def test_fps_puts_no_later_event_on_the_last_bar(monkeypatch):
    from tradinglib.indicator.fps import Fps
    full = _hist(1600, '2019-01-02')
    end = full.index[1200]
    chart = full.loc[:end].iloc[-250:].copy()
    monkeypatch.setattr(four_ps, 'benchmark_series', lambda *a, **k: None)
    monkeypatch.setattr(four_ps, 'load_daily', lambda symbol, db_path=None: full)
    capped = Fps(chart.copy(), symbol='X', benchmark='')
    monkeypatch.setattr(four_ps, 'load_daily', lambda symbol, db_path=None: full.loc[:end])
    honest = Fps(chart.copy(), symbol='X', benchmark='')
    for col in ('fps_phase', 'fps_buy', 'fps_sell', 'fps_stop'):
        np.testing.assert_allclose(capped.df[col].to_numpy(float), honest.df[col].to_numpy(float),
                                   equal_nan=True, err_msg=col)
