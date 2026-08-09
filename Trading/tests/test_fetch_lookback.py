"""Regression tests for the daily lookback window (2026-08).

``fetch_data_dl`` derives a start date from a per-interval limit table and
clamps every request to it. ``'1d'`` sat at -729 days -- that is Yahoo's
*intraday* limit (60m/1h reach back about two years), while daily bars go back
decades. The clamp therefore capped daily history at two years without any
error:

    - a ticker added to an index could never gain more than two years of daily
      candles, while long-standing ones kept the decades they had from earlier
      imports -- so backtests silently ran on different history depths
    - the scheduled ETP job asking for "1d:5y" quietly received 2y

These tests pin the resulting windows. They capture the ``start`` handed to the
download layer instead of hitting the network.

Run: .venv/Scripts/python.exe -m pytest tests/ -q
"""
from datetime import datetime

import pandas as pd
import pytest

from tradinglib import ticker_tools as tt


@pytest.fixture
def capture_start(monkeypatch):
    """Return a callable (interval, period) -> years of lookback requested."""
    seen = {}

    def fake_download(*args, **kwargs):
        seen['start'] = kwargs.get('start')
        seen['interval'] = kwargs.get('interval')
        # Shaped like a yfinance multi-ticker response so the caller's
        # xs(level='Ticker') / drop('Adj Close') step still works.
        cols = pd.MultiIndex.from_product(
            [['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume'], ['TEST']],
            names=['Price', 'Ticker'])
        return pd.DataFrame([[1.0] * 6], index=pd.to_datetime(['2026-01-02']),
                            columns=cols)

    monkeypatch.setattr(tt.md, 'download', fake_download)

    def run(interval, period):
        saver = tt.StockDataSaver.__new__(tt.StockDataSaver)
        saver.ticker = 'TEST'
        saver.tz_ready = ''
        saver.fetch_data_dl(interval, period=period)
        start = datetime.strptime(seen['start'], '%Y-%m-%d')
        return (datetime.now() - start).days / 365.0

    return run


def test_daily_max_reaches_back_decades(capture_start):
    # The bug capped this at ~2 years.
    assert capture_start('1d', 'max') > 15


def test_daily_explicit_period_is_honoured(capture_start):
    # "1d:5y" used to be clamped to 729 days -- the ETP job's case.
    assert 4.5 < capture_start('1d', '5y') < 5.5


def test_period_units_are_calendar_days(capture_start):
    """A requested period must span that much calendar time.

    The units table held trading days (wk=5, mo=21, y=252) but the result is
    subtracted as calendar days, so every window came out about 30 % short.
    """
    assert 0.9 < capture_start('1d', '1y') < 1.1
    assert 1.9 < capture_start('1d', '2y') < 2.1
    days_1mo = capture_start('1d', '1mo') * 365
    assert 28 <= days_1mo <= 32


def test_short_daily_period_stays_short(capture_start):
    # The scheduled jobs use 1mo/2mo; those must keep their narrow window so
    # the fix does not blow up routine run volume.
    assert capture_start('1d', '1mo') < 0.2


def test_intraday_limits_stay_at_yahoos_ceiling(capture_start):
    # 60m/1h really are capped near two years at the source -- asking for more
    # would only produce empty responses.
    assert capture_start('1h', 'max') < 2.1
    assert capture_start('60m', 'max') < 2.1


def test_intraday_minute_window_unchanged(capture_start):
    assert capture_start('1m', 'max') < 0.05


@pytest.mark.parametrize('interval', ['1wk', '1mo'])
def test_weekly_and_monthly_keep_their_window(capture_start, interval):
    assert capture_start(interval, 'max') > 15
