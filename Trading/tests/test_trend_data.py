"""Trend- & Saisonalitäts-Scorecard: Kennzahlen und Punkte auf synthetischen Kursen."""
import datetime as dt
import json
import re

import numpy as np
import pandas as pd
import pytest

from tradinglib import trend_data as td


def _series(values, start="2015-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def test_uptrend_scores_plus_three():
    s = _series(np.linspace(100, 200, 400))
    m = td.trend_metrics(s)
    assert m["dist200"] > 0 and m["r63"] > 1
    assert td.score_trend(m) == (1, 1, 1)
    assert td.score_trend(m, sign=-1) == (-1, -1, -1)        # z. B. Rendite statt Anleihekurs


def test_downtrend_and_flat_momentum():
    s = _series(np.linspace(200, 100, 400))
    assert td.score_trend(td.trend_metrics(s)) == (-1, -1, -1)
    flat = _series(np.r_[np.linspace(100, 150, 330), np.full(70, 150.0)])
    p = td.score_trend(td.trend_metrics(flat))
    assert p[2] == 0                                          # 3 M seitwaerts -> 0


def test_momentum_uses_calendar_months_for_seven_day_markets():
    """Krypto: 7 Tage/Woche -> 63 Zeilen waeren nur 9 Wochen."""
    idx = pd.date_range("2025-01-01", "2026-11-13", freq="D")
    vals = np.where(idx <= pd.Timestamp("2026-08-13"), 100.0, 110.0)
    vals[-63:] = 120.0                                        # die letzten 63 Tage
    m = td.trend_metrics(pd.Series(vals, index=idx))
    assert m["r63"] == pytest.approx(20.0)                    # gegen 13.08. (100), nicht gegen 110


def test_too_short_history_scores_zero():
    assert td.trend_metrics(_series(range(1, 150))) == {}
    assert td.score_trend({}) == (0, 0, 0)


def _seasonal(up_years, today=dt.date(2026, 11, 12)):
    """Taeglicher Kurs 2015-2026; im Fenster 12.11.-12.12. steigt er in den
    ``up_years`` und faellt in allen anderen Jahren."""
    idx = pd.bdate_range("2015-01-01", "2026-11-11")
    rets = np.zeros(len(idx))
    for y in range(2015, 2026):
        w = (idx >= pd.Timestamp(y, 11, 12)) & (idx <= pd.Timestamp(y, 12, 12))
        rets[w] = 0.002 if y in up_years else -0.002
    return pd.Series(100 * np.cumprod(1 + rets), index=idx), today


def test_seasonality_hit_rate_and_years():
    s, today = _seasonal(up_years={2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023})
    sea = td.seasonality(s, today)
    assert sea["years"] == list(range(2016, 2026))
    assert sea["n"] == 10 and sea["hit"] == pytest.approx(80)
    assert td.score_season(sea) == 1
    assert td.score_season(sea, sign=-1) == -1


@pytest.mark.parametrize("up,expected", [(set(), -1), ({2016, 2017, 2018, 2019, 2020, 2021}, 0)])
def test_seasonality_bounded(up, expected):
    s, today = _seasonal(up_years=up)
    assert td.score_season(td.seasonality(s, today)) == expected


def test_season_needs_enough_years():
    sea = {"n": 5, "mean": 3.0, "hit": 100.0}
    assert td.score_season(sea) == 0


@pytest.mark.parametrize("score,label", [(4, "strong_bull"), (3, "strong_bull"), (2, "bull"), (1, "neutral"),
                                         (-2, "bear"), (-3, "strong_bear")])
def test_bias_label(score, label):
    assert td.bias_label(score) == label


def test_every_market_has_a_source_and_cot_key():
    from tradinglib.cot_data import MARKETS
    keys = {m[0] for m in MARKETS} | {k for k, _g in td.EXTRA_MARKETS}
    assert keys == set(td.SOURCES)


def test_short_local_history_prefers_longer_remote(monkeypatch):
    today = dt.date(2026, 11, 12)
    short = pd.Series(100.0, index=pd.bdate_range("2023-05-22", "2026-11-11"))
    long_ = pd.Series(100.0, index=pd.bdate_range("2010-01-01", "2026-11-11"))
    monkeypatch.setattr(td, "_local_close", lambda t: short)
    monkeypatch.setattr(td, "_remote_close", lambda t: long_)
    s, src = td.price_series("SMI", today)
    assert len(s) == len(long_) and src.endswith("(Yahoo)")
    # Yahoo nicht erreichbar -> kurze lokale Reihe statt nichts
    monkeypatch.setattr(td, "_remote_close", lambda t: pd.Series(dtype=float))
    s, src = td.price_series("SMI", today)
    assert len(s) == len(short) and src.endswith("(local)")


def test_price_series_uses_cross_and_inversion(monkeypatch):
    idx = pd.bdate_range("2026-01-01", "2026-11-11")
    data = {"JPYEUR=X": pd.Series(0.006, index=idx), "EURUSD=X": pd.Series(1.10, index=idx)}
    monkeypatch.setattr(td, "_local_close", lambda t: data.get(t, pd.Series(dtype=float)))
    monkeypatch.setattr(td, "_remote_close", lambda t: pd.Series(dtype=float))
    s, src = td.price_series("JPY", dt.date(2026, 11, 12))
    assert s.iloc[-1] == pytest.approx(0.0066)
    assert "JPYEUR=X × EURUSD" in src
    # veraltete lokale Reihe -> naechste Quelle (hier: 1/USDJPY von "Yahoo")
    stale = {"JPYEUR=X": pd.Series(0.006, index=pd.bdate_range("2026-01-01", "2026-10-01")),
             "EURUSD=X": data["EURUSD=X"]}
    monkeypatch.setattr(td, "_local_close", lambda t: stale.get(t, pd.Series(dtype=float)))
    monkeypatch.setattr(td, "_remote_close", lambda t: pd.Series(150.0, index=idx) if t == "JPY=X" else pd.Series(dtype=float))
    s, src = td.price_series("JPY", dt.date(2026, 11, 12))
    assert s.iloc[-1] == pytest.approx(1 / 150) and src.startswith("1/JPY=X")


def test_page_locale_keys_exist():
    src = open("tradinglib/trend_page.py", encoding="utf-8").read()
    keys = set(re.findall(r't\("([a-z_]+\.[a-z_0-9]+)"', src))
    for lang in ("de", "en"):
        d = json.load(open(f"locales/{lang}.json", encoding="utf-8"))
        assert not sorted(k for k in keys if k not in d), lang
