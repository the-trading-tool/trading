"""dTrend/wkTrend live columns on every chart interval (not only 1d)."""
import numpy as np
import pandas as pd

from tradinglib.indicator import indicator


def _daily(n=60, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2026-01-05', periods=n)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({'Date': dates.strftime('%Y-%m-%d'), 'Close': close})


def test_daily_dtrend_unchanged():
    d = _daily()
    out = indicator.trend_pct_columns(d)
    expected = indicator._trend_pct_vec(d['Close'], d['Close'].shift(1))
    pd.testing.assert_series_equal(out['dTrend'], expected, check_names=False)


def test_intraday_dtrend_uses_previous_day_close():
    stamps = pd.date_range('2026-03-02 09:00', periods=8, freq='h').append(
        pd.date_range('2026-03-03 09:00', periods=8, freq='h'))
    close = pd.Series(np.arange(1, 17, dtype=float) + 100)
    df = pd.DataFrame({'Date': stamps.strftime('%Y-%m-%d %H:%M'), 'Close': close})
    out = indicator.trend_pct_columns(df)
    prev_day_close = close.iloc[7]
    day2 = out.iloc[8:]
    expected = indicator._trend_pct_vec(close.iloc[8:], pd.Series(prev_day_close, index=day2.index))
    pd.testing.assert_series_equal(day2['dTrend'], expected, check_names=False)
    assert (out['dTrend'].iloc[:8] == 0).all()          # no previous day yet
    # causal: cutting the future leaves the past untouched
    cut = indicator.trend_pct_columns(df.iloc[:11])
    pd.testing.assert_frame_equal(cut[['dTrend', 'wkTrend']], out[['dTrend', 'wkTrend']].iloc[:11])


def test_weekly_frame_takes_last_daily_value_of_each_week():
    d = _daily()
    dd = indicator.trend_pct_columns(d)
    dts = pd.to_datetime(d['Date'])
    for label in ('W-MON', 'W-FRI'):                    # Monday- or Friday-labelled bars
        anchor = dts.dt.to_period('W').dt.start_time if label == 'W-MON' \
            else dts.dt.to_period('W').dt.start_time + pd.Timedelta(days=4)
        weekly = (d.assign(Date=anchor.dt.strftime('%Y-%m-%d'))
                   .groupby('Date', as_index=False)['Close'].last())
        out = indicator.trend_pct_onto(weekly, d, '1wk')
        last_rows = dd.groupby(dts.dt.to_period('W').values).last()
        assert list(out['wkTrend']) == list(last_rows['wkTrend'])
        assert list(out['dTrend']) == list(last_rows['dTrend'])


def test_monthly_without_daily_falls_back_to_columns():
    d = _daily()
    out = indicator.trend_pct_onto(d.iloc[:3], None, '1mo')
    assert {'dTrend', 'wkTrend'} <= set(out.columns)
