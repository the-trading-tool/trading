"""Edge score per trading day — the daily series behind the ``edge`` oscillator.

Same rules as the scorecards, only evaluated on every day instead of on Fridays:

* **Edge market** (``^GDAXI``, ``GC=F``, ``EURUSD=X`` …, see
  :func:`ticker_scorecard.edge_market_for`): trend & season (−4 … +4) plus, for the
  eight major currencies, the macro score (−6 … +6) — :func:`asset_card_data.total_of`.
* **Single stock**: trend & season, relative strength against its index (−2 … +2)
  and the index's own score as market environment (−10 … +10) —
  :func:`ticker_scorecard.combine`.

The scorecards compute one day with :func:`asset_card_data.trend_at`, which takes
~15 ms (rolling means over the whole history and ten seasonal windows). A chart
needs hundreds of days, so the *metrics* are computed vectorised here and fed into
the unchanged point rules :func:`trend_data.score_trend` / :func:`trend_data.score_season`.
``tests/test_edge_daily.py`` checks day by day that the result equals ``trend_at`` /
``card_at``.

Causal throughout: day ``d`` only sees closes up to ``d``; macro values count from
their estimated publication date (:data:`asset_card_data.PUBLICATION_LAG`) except on
``today``, where everything loaded counts — exactly as in the asset scorecard.
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

from tradinglib import asset_card_data as ac
from tradinglib import total_data as tot
from tradinglib import trend_data as td

logger = logging.getLogger(__name__)

COLUMNS = ("edge_total", "edge_trend", "edge_rs", "edge_market", "edge_macro")


# ── Trend & season, vectorised ────────────────────────────────────────────────

def _season_points(idx: np.ndarray, vals: np.ndarray, pos: np.ndarray,
                   days: pd.DatetimeIndex) -> list[dict]:
    """``trend_data.seasonality`` for every day in ``days`` (series cut at ``pos``)."""
    years = td.SEASON_YEARS
    n_days = len(days)
    starts = np.empty((n_days, years), dtype="datetime64[ns]")
    for i, d in enumerate(days):
        day = min(d.day, 28) if d.month == 2 else d.day
        for j, y in enumerate(range(d.year - years, d.year)):
            starts[i, j] = np.datetime64(dt.date(y, d.month, day), "ns")
    ends = starts + np.timedelta64(td.SEASON_DAYS, "D")
    cut = days.to_numpy().astype("datetime64[ns]")[:, None]
    ends = np.minimum(ends, cut)                         # the series ends at d
    ia = np.searchsorted(idx, starts, side="left")
    ib = np.searchsorted(idx, ends, side="right") - 1
    last = pos[:, None]
    ok = (ia <= last) & (ib >= 0)
    ia_c, ib_c = np.clip(ia, 0, len(idx) - 1), np.clip(ib, 0, len(idx) - 1)
    ok &= idx[ia_c] < idx[ib_c]
    ok &= (idx[ia_c] - starts) <= np.timedelta64(5, "D") + np.timedelta64(86399, "s")
    rets = (vals[ib_c] / vals[ia_c] - 1) * 100
    out = []
    for i in range(n_days):
        r = rets[i][ok[i]]
        if len(r) == 0:
            out.append({"n": 0, "mean": np.nan, "hit": np.nan})
        else:
            out.append({"n": len(r), "mean": float(r.mean()), "hit": float((r > 0).mean() * 100)})
    return out


def trend_frame(s: pd.Series, days, sign: int = 1) -> pd.DataFrame:
    """Trend points per day, identical to :func:`asset_card_data.trend_at`.

    Columns ``score`` (None below 200 closes), ``p_sma200``, ``p_cross``, ``p_mom``,
    ``p_season``; index = ``days`` (normalised)."""
    days = pd.DatetimeIndex(pd.to_datetime(pd.Index(days))).normalize()
    cols = ["score", "p_sma200", "p_cross", "p_mom", "p_season"]
    if s is None or s.empty or len(days) == 0:
        return pd.DataFrame(index=days, columns=cols, dtype=object)
    s = s.dropna().sort_index()
    idx = s.index.to_numpy().astype("datetime64[ns]")
    vals = s.to_numpy(dtype=float)
    pos = np.searchsorted(idx, days.to_numpy().astype("datetime64[ns]"), side="right") - 1
    sma50 = s.rolling(50).mean().to_numpy()
    sma200 = s.rolling(200).mean().to_numpy()
    pos_c = np.clip(pos, 0, len(idx) - 1)
    ref = pd.DatetimeIndex(idx[pos_c]) - pd.DateOffset(months=3)
    rpos = np.searchsorted(idx, ref.to_numpy().astype("datetime64[ns]"), side="right") - 1
    season = _season_points(idx, vals, pos, days)
    rows = []
    for i in range(len(days)):
        p = pos[i]
        if p + 1 < 200:
            rows.append({"score": None, "p_sma200": None, "p_cross": None, "p_mom": None, "p_season": None})
            continue
        r63 = float(vals[p] / vals[rpos[i]] - 1) * 100 if rpos[i] >= 0 else np.nan
        m = {"last": float(vals[p]), "sma50": float(sma50[p]), "sma200": float(sma200[p]), "r63": r63}
        p1, p2, p3 = td.score_trend(m, sign)
        ps = td.score_season(season[i], sign)
        rows.append({"score": p1 + p2 + p3 + ps, "p_sma200": p1, "p_cross": p2, "p_mom": p3, "p_season": ps})
    return pd.DataFrame(rows, index=days, columns=cols)


def period_returns(s: pd.Series, days, weeks: int = ac.RS_WEEKS) -> pd.Series:
    """:func:`asset_card_data.period_return` for every day (series cut at the day)."""
    days = pd.DatetimeIndex(pd.to_datetime(pd.Index(days))).normalize()
    if s is None or len(s) < 2:
        return pd.Series(np.nan, index=days)
    s = s.dropna().sort_index()
    idx = s.index.to_numpy().astype("datetime64[ns]")
    vals = s.to_numpy(dtype=float)
    pos = np.searchsorted(idx, days.to_numpy().astype("datetime64[ns]"), side="right") - 1
    pos_c = np.clip(pos, 0, len(idx) - 1)
    ref = idx[pos_c] - np.timedelta64(weeks * 7, "D")
    rpos = np.searchsorted(idx, ref, side="right") - 1
    ok = (pos >= 1) & (rpos >= 0)
    out = np.where(ok, (vals[pos_c] / vals[np.clip(rpos, 0, None)] - 1) * 100, np.nan)
    return pd.Series(out, index=days)


# ── Scores ────────────────────────────────────────────────────────────────────

def _none(v):
    return None if v is None or pd.isna(v) else v


def stock_scores(s: pd.Series, idx: pd.Series | None, days) -> pd.DataFrame:
    """Single-stock scorecard (:func:`ticker_scorecard.card_at`) for every day."""
    from tradinglib import ticker_scorecard as ts
    days = pd.DatetimeIndex(pd.to_datetime(pd.Index(days))).normalize()
    tr = trend_frame(s, days, 1)
    have_idx = idx is not None and not idx.empty
    mt = trend_frame(idx, days, 1) if have_idx else None
    ret = period_returns(s, days)
    iret = period_returns(idx, days) if have_idx else None
    rows = []
    for i, d in enumerate(days):
        trend = _none(tr["score"].iat[i])
        rs = market = None
        if have_idx:
            if not pd.isna(ret.iat[i]) and not pd.isna(iret.iat[i]):
                rs = ts.rs_points(float(ret.iat[i] - iret.iat[i]))
            market = ac.total_of(None, None, _none(mt["score"].iat[i]))
        total = ts.combine(trend, rs, market)
        n = ts.n_components(trend, rs, market)
        rows.append({"edge_total": total, "edge_trend": trend, "edge_rs": rs, "edge_market": market,
                     "edge_macro": None,
                     "bias": tot.bias_for(total, n, trend, _none(tr["p_sma200"].iat[i]))})
    return pd.DataFrame(rows, index=days)


def market_scores(key: str, s: pd.Series, obs: pd.DataFrame | None, days,
                  today: dt.date | None = None) -> pd.DataFrame:
    """Asset-scorecard total (:func:`asset_card_data.history_points`) for every day."""
    from tradinglib import macro_data as md
    today = today or dt.date.today()
    days = pd.DatetimeIndex(pd.to_datetime(pd.Index(days))).normalize()
    tr = trend_frame(s, days, td.SIGN.get(key, 1))
    use_macro = ac.has_macro(key) and obs is not None and not obs.empty
    avail, memo = None, {}
    if use_macro and len(days):
        obs = obs[obs["period"] >= days[0] - pd.DateOffset(years=2)]
        avail = ac.available_from(obs)
    rows = []
    for i, d in enumerate(days):
        trend = _none(tr["score"].iat[i])
        macro = None
        if use_macro:
            known = obs if d.date() >= today else obs[avail <= d]
            if len(known) not in memo:
                memo[len(known)] = md.score_table(md.latest(known)) if not known.empty else pd.DataFrame()
            tab = memo[len(known)]
            if key in tab.index:
                macro = float(tab.at[key, "score"])
        total = ac.total_of(None, macro, trend)
        n = ac.n_components(None, macro, trend)
        rows.append({"edge_total": total, "edge_trend": trend, "edge_rs": None, "edge_market": None,
                     "edge_macro": macro,
                     "bias": tot.bias_for(total, n, trend, _none(tr["p_sma200"].iat[i]))})
    return pd.DataFrame(rows, index=days)


def for_ticker(ticker: str, start, end, today: dt.date | None = None) -> pd.DataFrame:
    """Daily edge scores of ``ticker`` on its trading days between ``start`` and ``end``.

    Read-only (local ``yf_*.db``; edge markets may fall back to a Yahoo download in
    memory, like the scorecards). Empty frame when there is no usable history."""
    from tradinglib import edge_market as em
    from tradinglib import ticker_scorecard as ts
    today = today or dt.date.today()
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    key = ts.edge_market_for(ticker)
    if key:
        s = td.price_series(key, today)[0]
    else:
        s = td._local_close(ticker)
    if s.empty:
        return pd.DataFrame(columns=list(COLUMNS) + ["bias"])
    days = s.index[(s.index >= start) & (s.index <= end)]
    if len(days) == 0:
        return pd.DataFrame(columns=list(COLUMNS) + ["bias"])
    if key:
        from tradinglib import macro_data as md
        try:
            obs = md.load_obs() if ac.has_macro(key) else None
        except Exception:
            obs = None
        return market_scores(key, s, obs, days, today)
    try:
        ik = em.ticker_markets([ticker]).get(ticker)
    except Exception:
        ik = None
    idx = td.price_series(ik, today)[0] if ik else None
    return stock_scores(s, idx, days)
