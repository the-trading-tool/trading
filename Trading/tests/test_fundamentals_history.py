"""Pins the point-in-time fundamentals table.

The table exists because a snapshot cannot be measured against history. Two
properties keep it honest and are tested here: it must never store a figure that
contains today's price (that is how the artefact got in), and a read for a date
before recording started must come back empty rather than quietly handing over
the newest row.

Every test runs against a temporary database — nothing touches asset_info.db.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib import fundamentals_history as fh


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / 'asset_info.db')
    sqlite3.connect(path).close()
    monkeypatch.setattr(fh, '_db_path', lambda *a, **k: path)
    return path


def _conn(path):
    return sqlite3.connect(path)


def _row(ticker='AAPL', **overrides):
    row = {'ticker': ticker, 'forwardEps': 9.5, 'bookValue': 7.36,
           'targetHighPrice': 400.0, 'operatingMargins': 0.326,
           'returnOnAssets': 0.28, 'revenueGrowth': 0.09}
    row.update(overrides)
    return row


def test_no_price_derived_ratio_is_tracked():
    """A ratio against today's price would smuggle the look-ahead back in."""
    forbidden = {'forwardPE', 'trailingPE', 'priceToBook', 'pegRatio',
                 'trailingPegRatio', 'priceToSalesTrailing12Months',
                 'pctTargetHighPrice', 'currentPrice', 'previousClose'}
    assert not (set(fh.TRACKED_FIELDS) & forbidden)
    # the ingredients for computing those at read time must be there instead
    assert {'forwardEps', 'bookValue', 'targetHighPrice'} <= set(fh.TRACKED_FIELDS)


def test_first_snapshot_is_written(db):
    with _conn(db) as conn:
        assert fh.record(conn, [_row()], asof='2026-01-05') == 1
    assert fh.coverage()['rows'] == 1
    assert fh.coverage()['first'] == '2026-01-05'


def test_unchanged_figures_write_nothing(db):
    with _conn(db) as conn:
        fh.record(conn, [_row()], asof='2026-01-05')
        assert fh.record(conn, [_row()], asof='2026-01-06') == 0
    assert fh.coverage()['rows'] == 1


def test_rounding_noise_is_not_a_change(db):
    with _conn(db) as conn:
        fh.record(conn, [_row(forwardEps=9.5)], asof='2026-01-05')
        # Yahoo rounds differently between calls; that must not fill the table
        assert fh.record(conn, [_row(forwardEps=9.50000001)], asof='2026-01-06') == 0


def test_a_real_change_is_recorded(db):
    with _conn(db) as conn:
        fh.record(conn, [_row(forwardEps=9.5)], asof='2026-01-05')
        assert fh.record(conn, [_row(forwardEps=10.2)], asof='2026-04-05') == 1
    assert fh.coverage()['rows'] == 2
    assert fh.coverage()['days'] == 2


def test_a_figure_appearing_or_vanishing_counts_as_a_change(db):
    with _conn(db) as conn:
        fh.record(conn, [_row(earningsGrowth=None)], asof='2026-01-05')
        assert fh.record(conn, [_row(earningsGrowth=0.12)], asof='2026-02-05') == 1
        assert fh.record(conn, [_row(earningsGrowth=None)], asof='2026-03-05') == 1


def test_asof_returns_what_was_known_then(db):
    with _conn(db) as conn:
        fh.record(conn, [_row(forwardEps=9.5)], asof='2026-01-05')
        fh.record(conn, [_row(forwardEps=10.2)], asof='2026-04-05')

    older = fh.asof_frame(['AAPL'], '2026-03-01')
    assert older['forwardEps'].iloc[0] == pytest.approx(9.5)
    assert older['asof'].iloc[0] == '2026-01-05'

    newer = fh.asof_frame(['AAPL'], '2026-06-01')
    assert newer['forwardEps'].iloc[0] == pytest.approx(10.2)


def test_asof_before_the_series_starts_is_empty(db):
    """The honest answer for a date nothing is known about — not the newest row."""
    with _conn(db) as conn:
        fh.record(conn, [_row()], asof='2026-01-05')
    assert fh.asof_frame(['AAPL'], '2024-06-01').empty


def test_asof_without_the_table_is_empty(db):
    assert fh.asof_frame(['AAPL'], '2026-06-01').empty
    assert fh.coverage()['rows'] == 0


def test_record_survives_a_broken_connection(db):
    """A failure here must not cost the caller its already-committed master data."""
    class _Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError('disk is full')

    assert fh.record(_Broken(), [_row()]) == 0


def test_several_tickers_are_kept_apart(db):
    with _conn(db) as conn:
        fh.record(conn, [_row('AAPL', forwardEps=9.5),
                         _row('SAP.DE', forwardEps=8.4)], asof='2026-01-05')
        # only one of them moves
        assert fh.record(conn, [_row('AAPL', forwardEps=9.5),
                                _row('SAP.DE', forwardEps=9.9)], asof='2026-04-05') == 1
    frame = fh.asof_frame(['AAPL', 'SAP.DE'], '2026-06-01').set_index('ticker')
    assert frame.loc['AAPL', 'asof'] == '2026-01-05'
    assert frame.loc['SAP.DE', 'asof'] == '2026-04-05'


def test_rows_without_a_ticker_are_ignored(db):
    with _conn(db) as conn:
        assert fh.record(conn, [{'forwardEps': 1.0}], asof='2026-01-05') == 0
