"""edge_daily must reproduce the scorecards day by day (vectorised vs. per-day)."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tradinglib import asset_card_data as ac
from tradinglib import edge_daily as ed
from tradinglib import ticker_scorecard as ts


def _walk(seed: int, start="2013-01-01", end="2026-11-13", drift=0.0003, weekends=False) -> pd.Series:
    idx = pd.date_range(start, end, freq="D" if weekends else "B")
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, 0.015, len(idx))
    return pd.Series(100 * np.exp(np.cumsum(r)), index=idx)


def _days(s: pd.Series, n=120, seed=7):
    rng = np.random.default_rng(seed)
    pick = sorted(rng.choice(s.index[150:], n, replace=False))
    extra = [pd.Timestamp("2024-02-29"), pd.Timestamp("2025-01-03"), pd.Timestamp("2026-11-13"),
             pd.Timestamp("2013-10-01")]          # leap day, early January, last day, < 200 closes
    return pd.DatetimeIndex(sorted(set(pick) | {d for d in extra if d in s.index}))


@pytest.mark.parametrize("weekends", [False, True])
@pytest.mark.parametrize("sign", [1, -1])
def test_trend_frame_equals_trend_at(weekends, sign):
    s = _walk(1, weekends=weekends)
    days = _days(s)
    fr = ed.trend_frame(s, days, sign)
    for d in days:
        want = ac.trend_at(s, d.date(), sign)
        got = fr.loc[d]
        if want["score"] is None:
            assert pd.isna(got["score"]), d
        else:
            assert got["score"] == want["score"], d
            assert (got["p_sma200"], got["p_cross"], got["p_mom"], got["p_season"]) == \
                (want["p_sma200"], want["p_cross"], want["p_mom"], want["p_season"]), d


def test_season_hits_both_thresholds():
    """Guard that the comparison above actually exercises the season points."""
    s = _walk(1)
    fr = ed.trend_frame(s, s.index[3000:], 1)
    assert set(fr["p_season"].dropna().unique()) >= {-1, 1}


def test_stock_scores_equal_card_at():
    s, idx = _walk(2), _walk(3, drift=0.0001)
    days = _days(s, n=80)
    got = ed.stock_scores(s, idx, days)
    for d in days:
        want = ts.card_at(s, idx, d.date())
        row = got.loc[d]
        for col, k in (("edge_total", "total"), ("edge_trend", "trend"), ("edge_rs", "rs"),
                       ("edge_market", "market")):
            a, b = row[col], want[k]
            assert (a is None or pd.isna(a)) == (b is None) and (b is None or a == b), (d, col, a, b)
        assert row["bias"] == want["bias"], d


def test_stock_scores_without_index():
    s = _walk(4)
    got = ed.stock_scores(s, None, s.index[-5:])
    assert got["edge_rs"].isna().all() and got["edge_market"].isna().all()
    assert (got["edge_total"] == got["edge_trend"] * 10 / 4).all()


def test_market_scores_equal_history_points():
    s = _walk(5)
    days = list(_days(s, n=60))
    today = dt.date(2026, 11, 13)
    got = ed.market_scores("GOLD", s, None, days, today)
    want = ac.history_points("GOLD", s, None, None, [d.date() for d in days], today)
    np.testing.assert_array_equal(pd.to_numeric(got["edge_total"]).to_numpy(),
                                  pd.to_numeric(want["total"]).to_numpy())
    assert list(got["bias"]) == list(want["bias"])


def test_causal():
    """Cutting the future off changes no past value."""
    s, idx = _walk(6), _walk(8)
    days = s.index[-300:]
    full = ed.stock_scores(s, idx, days)
    cut = s.index[-120]
    part = ed.stock_scores(s[s.index <= cut], idx[idx.index <= cut], days[days <= cut])
    pd.testing.assert_frame_equal(full.loc[part.index], part)
