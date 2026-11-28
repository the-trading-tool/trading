"""Simulation rows: the newest computation of a day wins, other columns survive."""
import json
import sqlite3

import asset_perf2


def _db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, close REAL, rsi REAL, "
                "macd_diff REAL, _1y REAL, zones TEXT)")
    con.execute("INSERT INTO asset_simulation VALUES ('AAPL', '2026-11-27 00:00:00', 348.4, 55.0, 1.5, 0.1, NULL)")
    return con


def test_existing_day_is_updated_and_backfill_columns_survive():
    con = _db()
    row = {"ticker": "AAPL", "Date": "2026-11-27 00:00:00", "close": 351.2, "rsi": 57.5,
           "1y": 0.2, "zones": [1, 2]}
    assert asset_perf2.upsert_sim_row(con.cursor(), "asset_simulation", row) is True
    rows = con.execute("SELECT ticker, Date, close, rsi, macd_diff, _1y, zones FROM asset_simulation").fetchall()
    assert rows == [("AAPL", "2026-11-27 00:00:00", 351.2, 57.5, 1.5, 0.2, json.dumps([1, 2]))]


def test_new_day_is_inserted():
    con = _db()
    row = {"ticker": "AAPL", "Date": "2026-11-28 00:00:00", "close": 352.0, "rsi": 58.0}
    assert asset_perf2.upsert_sim_row(con.cursor(), "asset_simulation", row) is False
    assert con.execute("SELECT COUNT(*) FROM asset_simulation").fetchone()[0] == 2
    assert con.execute("SELECT close, macd_diff FROM asset_simulation WHERE Date LIKE '2026-11-28%'"
                       ).fetchone() == (352.0, None)


def test_same_day_twice_in_one_run_keeps_the_last():
    con = _db()
    cur = con.cursor()
    for close in (360.0, 361.0):
        asset_perf2.upsert_sim_row(cur, "asset_simulation",
                                   {"ticker": "MSFT", "Date": "2026-11-27 00:00:00", "close": close})
    assert con.execute("SELECT close FROM asset_simulation WHERE ticker='MSFT'").fetchall() == [(361.0,)]


def test_other_ticker_same_day_untouched():
    con = _db()
    asset_perf2.upsert_sim_row(con.cursor(), "asset_simulation",
                               {"ticker": "KO", "Date": "2026-11-27 00:00:00", "close": 70.0})
    assert con.execute("SELECT close FROM asset_simulation WHERE ticker='AAPL'").fetchone() == (348.4,)
