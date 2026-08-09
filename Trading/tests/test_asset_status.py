"""Tests for the per-ticker trading status (2026-08).

Background: after a wave of takeovers and one ticker rename (BK -> BNY), 264
symbols in asset_info had lost every name because Yahoo stopped serving them.
Deleting those rows was rejected -- they carry history and past trades, and the
fetch lists are built from yf_tickers.db, so deletion would have saved nothing
and the rows would have reappeared empty on the next run.

Instead the tickers are marked and dropped from the fetch lists. These tests
pin the properties that make that safe:

    - marking excludes a ticker from runs, clearing brings it back
    - a rename keeps its successor, a merger is never treated as one
    - a broken/missing status DB must fail OPEN (nothing silently skipped)

Run: .venv/Scripts/python.exe -m pytest tests/ -q
"""
import pytest

from tradinglib import asset_status


@pytest.fixture
def status_db(tmp_path, monkeypatch):
    """Point the module at a throwaway database."""
    db = tmp_path / "asset_info.db"
    monkeypatch.setattr(asset_status, "_db_path", lambda: str(db))
    return db


def test_roundtrip_keeps_every_field(status_db):
    asset_status.set_status("BK", "renamed", successor="BNY",
                            effective_date="2026-05-21", note="1:1",
                            source="bny.com")
    row = asset_status.get_status("BK")
    assert row["status"] == "renamed"
    assert row["successor"] == "BNY"
    assert row["effective_date"] == "2026-05-21"
    assert row["note"] == "1:1"
    assert row["source"] == "bny.com"
    assert row["updated"]


def test_active_ticker_has_no_status(status_db):
    assert asset_status.get_status("AAPL") is None


def test_unknown_status_is_rejected(status_db):
    # Guards against typos silently creating a status that nothing filters on.
    with pytest.raises(ValueError):
        asset_status.set_status("AAPL", "gone")


def test_marking_removes_from_run_list_clearing_restores(status_db):
    tickers = ["AAPL", "HOLX", "MSFT"]
    assert asset_status.filter_active(tickers) == tickers

    asset_status.set_status("HOLX", "delisted", effective_date="2026-04-07")
    assert asset_status.filter_active(tickers) == ["AAPL", "MSFT"]

    asset_status.clear_status("HOLX")
    assert asset_status.filter_active(tickers) == tickers


@pytest.mark.parametrize("status", asset_status.INACTIVE)
def test_every_status_excludes_from_runs(status_db, status):
    asset_status.set_status("XYZ", status)
    assert asset_status.filter_active(["XYZ", "AAPL"]) == ["AAPL"]


def test_successor_only_for_renames(status_db):
    asset_status.set_status("BK", "renamed", successor="BNY")
    # A merger names its successor in the note, never as a rename: CTRA -> DVN
    # carries a 0.70 exchange ratio, so following it would break the series.
    asset_status.set_status("CTRA", "delisted", note="Devon (DVN), 0.70 je CTRA")

    assert asset_status.successor_of("BK") == "BNY"
    assert asset_status.successor_of("CTRA") is None
    assert asset_status.successor_of("AAPL") is None


def test_second_write_updates_instead_of_duplicating(status_db):
    asset_status.set_status("XYZ", "no_data", note="erste Einschaetzung")
    asset_status.set_status("XYZ", "delisted", effective_date="2026-04-09",
                            note="Beleg nachgereicht")

    assert len(asset_status.all_status()) == 1
    row = asset_status.get_status("XYZ")
    assert row["status"] == "delisted"
    assert row["note"] == "Beleg nachgereicht"


def test_unavailable_db_fails_open(tmp_path, monkeypatch):
    """No status source must never mean "skip everything".

    Silently dropping tickers because a database is locked or missing would
    stop data collection without any visible error -- far worse than a few
    wasted requests. So the filter has to let everything through.
    """
    monkeypatch.setattr(asset_status, "_db_path",
                        lambda: str(tmp_path / "nope" / "missing.db"))
    assert asset_status.inactive_tickers() == set()
    assert asset_status.filter_active(["AAPL", "HOLX"]) == ["AAPL", "HOLX"]
    assert asset_status.get_status("HOLX") is None


def test_filter_preserves_order_and_accepts_any_iterable(status_db):
    asset_status.set_status("B", "delisted")
    assert asset_status.filter_active(iter(["A", "B", "C"])) == ["A", "C"]
