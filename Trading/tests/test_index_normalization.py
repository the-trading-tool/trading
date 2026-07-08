"""Regression tests for the DatetimeIndex normalization fixes (2026-07).

Background: a family of failures in asset_perf2.process_symbol and the Bsz
indicator all traced back to price frames carrying a non-monotonic or
duplicate index:

    - "index must be monotonic increasing or decreasing"        (bsz get_indexer)
    - "Reindexing only valid with uniquely valued Index objects" (bsz get_indexer)
    - "Value based partial slicing on non-monotonic DatetimeIndexes ..." (df.loc[a:b])
    - "'<' not supported between instances of 'str' and 'Timestamp'"     (df.loc[:date])

The fixes guarantee a sorted + unique DatetimeIndex at the choke points.
These tests pin that behaviour so it can't silently regress.

Run: .venv/Scripts/python.exe -m pytest tests/ -q
"""
import pandas as pd
import pytest

from tradinglib.utils import DataUtils


def _ohlc(index):
    """Minimal OHLCV frame carrying the given index."""
    n = len(index)
    return pd.DataFrame(
        {
            "Open":   [float(i + 1) for i in range(n)],
            "High":   [float(i + 2) for i in range(n)],
            "Low":    [float(i) + 0.5 for i in range(n)],
            "Close":  [float(i + 1) + 0.5 for i in range(n)],
            "Volume": [10] * n,
        },
        index=index,
    )


# --------------------------------------------------------------------------
# DataUtils.ensure_datetime_index — the shared choke point
# --------------------------------------------------------------------------

def test_ensure_datetime_index_none_passthrough():
    assert DataUtils.ensure_datetime_index(None) is None


def test_ensure_datetime_index_empty_passthrough():
    empty = pd.DataFrame()
    assert DataUtils.ensure_datetime_index(empty).empty


def test_ensure_datetime_index_sorts_unsorted_datetimeindex():
    idx = pd.to_datetime(["2024-03-01", "2024-01-01", "2024-02-01"])
    out = DataUtils.ensure_datetime_index(_ohlc(idx))
    assert isinstance(out.index, pd.DatetimeIndex)
    assert out.index.is_monotonic_increasing


def test_ensure_datetime_index_coerces_and_sorts_string_index():
    # load_price_data hands back a *string* index (strftime); it must still
    # become a sorted DatetimeIndex here.
    idx = ["2024-03-01 00:00:00", "2024-01-01 00:00:00", "2024-02-01 00:00:00"]
    out = DataUtils.ensure_datetime_index(_ohlc(idx))
    assert isinstance(out.index, pd.DatetimeIndex)
    assert out.index.is_monotonic_increasing


def test_ensure_datetime_index_uses_date_column():
    df = _ohlc(range(3))
    df["Date"] = ["2024-02-01", "2024-01-01", "2024-03-01"]
    out = DataUtils.ensure_datetime_index(df)
    assert isinstance(out.index, pd.DatetimeIndex)
    assert out.index.is_monotonic_increasing


def test_ensure_datetime_index_drops_nat_rows():
    idx = ["2024-01-01", "not-a-date", "2024-01-02"]
    out = DataUtils.ensure_datetime_index(_ohlc(idx))
    assert out.index.isna().sum() == 0
    assert len(out) == 2


def test_ensure_datetime_index_enables_value_slicing():
    """The concrete asset_perf2 regressions: df.loc[start:end] on the result."""
    idx = pd.to_datetime(["2024-03-01", "2024-01-01", "2024-02-01", "2024-02-15"])
    out = DataUtils.ensure_datetime_index(_ohlc(idx))

    # df.loc[str:str] — mirrors process_symbol line ~1217
    # (01-01, 02-01, 02-15 fall in range; 03-01 is excluded)
    sliced = out.loc["2024-01-01":"2024-02-28"]
    assert len(sliced) == 3

    # df.loc[:Timestamp] — mirrors process_symbol line ~1250
    sliced_ts = out.loc[: pd.Timestamp("2024-02-01")]
    assert len(sliced_ts) == 2


# --------------------------------------------------------------------------
# Bsz indicator — unsorted + duplicate index must not break zone tagging
# --------------------------------------------------------------------------

def test_bsz_normalizes_unsorted_duplicate_index():
    from tradinglib.indicator.bsz import Bsz

    # Deliberately unsorted with a duplicate timestamp — the exact shape that
    # raised the monotonic / uniquely-valued errors in get_indexer(nearest).
    idx = pd.to_datetime(
        ["2024-01-05", "2024-01-01", "2024-01-03", "2024-01-03", "2024-01-02",
         "2024-01-04", "2024-01-06", "2024-01-08", "2024-01-07", "2024-01-09"]
    )
    b = Bsz(df=_ohlc(idx))
    assert b.df.index.is_monotonic_increasing
    assert b.df.index.is_unique


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
