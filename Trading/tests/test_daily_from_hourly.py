"""StockDataSaver daily-from-hourly fallback: exchange-local days, no overwrite."""
import sqlite3

import pandas as pd
import pytest

from tradinglib import ticker_tools
from tradinglib.ticker_tools import StockDataSaver


def _asset_info(tmp_path, ticker, tz, quote_type="EQUITY"):
    con = sqlite3.connect(tmp_path / "asset_info.db")
    con.execute("CREATE TABLE asset_info (ticker TEXT, exchangeTimezoneName TEXT, quoteType TEXT)")
    con.execute("INSERT INTO asset_info VALUES (?,?,?)", (ticker, tz, quote_type))
    con.commit()
    con.close()


def _hourly(stamps):
    idx = pd.to_datetime(stamps)
    return pd.DataFrame({"Open": range(1, len(idx) + 1), "High": 100.0, "Low": 1.0,
                         "Close": range(1, len(idx) + 1), "Volume": 10}, index=idx)


@pytest.fixture
def saver(tmp_path, monkeypatch):
    monkeypatch.setenv("TradingDB", str(tmp_path))

    def make(ticker, tz, quote_type="EQUITY"):
        _asset_info(tmp_path, ticker, tz, quote_type)
        s = StockDataSaver(ticker)
        s.create_table("1d")
        return s
    yield make


def _days(s):
    return [d[:10] for (d,) in s.cursor.execute("SELECT Date FROM day_data ORDER BY Date")]


def test_us_post_market_bar_stays_on_friday(saver):
    s = saver("TEST", "America/New_York")
    # Fri 2026-02-06 regular session (EST) plus the post-market bar that is
    # stamped Sat 00:00 UTC (= Fri 19:00 New York).
    stamps = [f"2026-02-06 {h:02d}:30:00" for h in range(14, 21)] + ["2026-02-07 00:00:00"]
    stamps += [f"2026-02-09 {h:02d}:30:00" for h in range(14, 21)]
    s.save_to_db(_hourly(stamps), "1h")
    assert s._backfill_daily_from_hourly(min_rows=1) == 2
    assert _days(s) == ["2026-02-06", "2026-02-09"]
    fri = s.cursor.execute("SELECT Close FROM day_data WHERE Date LIKE '2026-02-06%'").fetchone()[0]
    assert fri == 8  # the post-market bar is Friday's last bar


def test_sunday_evening_open_is_dropped_for_futures(saver):
    s = saver("GC=F", "America/New_York", "FUTURE")
    stamps = ["2026-10-11 22:00:00", "2026-10-11 23:00:00", "2026-10-12 14:00:00"]
    s.save_to_db(_hourly(stamps), "1h")
    s._backfill_daily_from_hourly(min_rows=1)
    assert _days(s) == ["2026-10-12"]


def test_crypto_keeps_weekends(saver):
    s = saver("BTC-EUR", "UTC", "CRYPTOCURRENCY")
    s.save_to_db(_hourly(["2026-10-10 10:00:00", "2026-10-11 10:00:00"]), "1h")
    s._backfill_daily_from_hourly(min_rows=1)
    assert _days(s) == ["2026-10-10", "2026-10-11"]


def test_existing_provider_bar_is_not_replaced(saver):
    s = saver("TEST", "America/New_York")
    provider = pd.DataFrame({"Open": [5.0], "High": [6.0], "Low": [4.0], "Close": [5.5],
                             "Volume": [123456]}, index=pd.to_datetime(["2026-02-06"]))
    s.save_to_db(provider, "1d")
    s.save_to_db(_hourly(["2026-02-06 15:30:00", "2026-02-09 15:30:00"]), "1h")
    assert s._backfill_daily_from_hourly(min_rows=1) == 1
    row = s.cursor.execute("SELECT Close, Volume FROM day_data WHERE Date LIKE '2026-02-06%'").fetchone()
    assert row == (5.5, 123456)


def test_failed_daily_download_does_not_resample(saver, monkeypatch):
    s = saver("TEST", "America/New_York")
    provider = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1},
                            index=pd.bdate_range("2026-01-01", periods=30))
    s.save_to_db(provider, "1d")
    calls = []
    monkeypatch.setattr(s, "_backfill_daily_from_hourly", lambda *a, **k: calls.append(1) or 0)
    monkeypatch.setattr(ticker_tools.time, "sleep", lambda *_: None)

    def boom(*a, **k):
        raise RuntimeError("429 Too Many Requests")
    monkeypatch.setattr(s, "fetch_data_dl", boom)
    assert s.save_all_intervals(intervals=["1d"], periods=["1mo"]) == ["1d"]
    assert calls == []


def test_failed_download_on_empty_table_still_resamples(saver, monkeypatch):
    s = saver("^VIXEQ", "America/New_York", "INDEX")
    calls = []
    monkeypatch.setattr(s, "_backfill_daily_from_hourly", lambda *a, **k: calls.append(1) or 5)
    monkeypatch.setattr(ticker_tools.time, "sleep", lambda *_: None)
    monkeypatch.setattr(s, "fetch_data_dl", lambda *a, **k: pd.DataFrame())
    assert s.save_all_intervals(intervals=["1d"], periods=["1mo"]) == []
    assert calls == [1]
