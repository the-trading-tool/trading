"""Edge Scores Uebersicht: Daten je Markt, Tacho/Sparkline, Locale-Keys."""
import json
import re

import numpy as np
import pandas as pd

from tradinglib import asset_card_data as ac
from tradinglib import edge_overview_page as ov
from tradinglib import edge_scores_page as esp


def _trend(rows):
    return pd.DataFrame([{"key": k, "score": s, "p_sma200": p, "missing": False} for k, s, p in rows])


def test_overview_covers_all_markets_and_matches_total():
    cot = pd.DataFrame({"key": ["USD", "GOLD"], "score": [3, 3]})
    macro = pd.DataFrame({"score": [6]}, index=["USD"])
    trend = _trend([("USD", 4, 1), ("GOLD", -2, -1), ("DAX", 3, 1), ("BTC", 1, -1)])
    df = ac.overview(cot, macro, trend).set_index("key")
    assert set(k for k, _g in ac.markets()) == set(df.index)
    assert df.at["USD", "total"] == ac.total_of(3, 6, 4) == 10.0
    assert df.at["GOLD", "total"] == -5.0 and df.at["GOLD", "cot"] == 3       # COT nur Kontext
    assert df.at["DAX", "group"] == "indices_world" and df.at["DAX", "bias"] == "bull"   # gedeckelt
    assert df.at["BTC", "unconfirmed"] and df.at["BTC", "bias"] == "neutral"   # unter SMA200
    assert pd.isna(df.at["MDAX", "total"])                                     # keine Kurse


def test_overview_relative_strength_context():
    idx = pd.bdate_range("2026-01-01", "2026-11-13")
    growth = {"DAX": 1.10, "IBEX": 0.99, "SPX": 0.97, "SMI": 1.05, "N225": 0.92, "KOSPI": 0.85}
    series = {k: pd.Series(np.linspace(100, 100 * g, len(idx)), index=idx) for k, g in growth.items()}
    trend = _trend([(k, 1, 1) for k in growth])
    df = ac.overview(pd.DataFrame(columns=["key", "score"]), pd.DataFrame(columns=["score"]),
                     trend, series).set_index("key")
    assert df.at["DAX", "rs_rank"] == 1 and df.at["DAX", "rs_n"] == 6 and df.at["DAX", "rs_diff"] > 0
    assert df.at["DAX", "total"] == 2.5                                        # RS geht nicht in den Score


def test_gauge_needle_position():
    assert ov._xy(-10, 44)[0] < 17 and abs(ov._xy(0, 44)[0] - 60) < 1e-6 and ov._xy(10, 44)[0] > 103
    svg = ov.gauge_svg(7.5)
    assert "+7.5" in svg and "<line" in svg
    assert "<line" not in ov.gauge_svg(None)
    assert ov.spark_svg(None) == "" and "#e53935" in ov.spark_svg(pd.Series(np.linspace(2, 1, 50)))


def test_top_setup_needs_agreement():
    df = pd.DataFrame({"key": ["USD", "EUR", "JPY"], "group": "fx",
                       "cot": [None] * 3, "macro": [6, -6, 1], "trend": [4, -4, -1],
                       "total": [10.0, -10.0, -0.4], "n": 2})
    p = ov.top_setup(df)
    assert p["pair"] == "EURUSD" and p["score"] == -20.0


def test_card_registered_and_locale_keys():
    assert "overview" in [k for k, *_r in esp.CARDS] and esp.DEFAULT_CARD == "asset"
    src = open("tradinglib/edge_overview_page.py", encoding="utf-8").read()
    keys = set(re.findall(r't\("((?:ov|edge)\.[a-z_]+)"', src))
    keys = {k for k in keys if not k.endswith("_")} | {f"ov.sort_{o}" for o in ("group", "strength", "chg_1w", "chg_4w")}
    assert "ov.method_md" in keys
    for lang in ("de", "en"):
        d = json.load(open(f"locales/{lang}.json", encoding="utf-8"))
        assert not [k for k in keys if k not in d], lang


def test_past_totals_are_causal_and_change_sign():
    import datetime as dt
    idx = pd.bdate_range("2014-01-01", "2026-11-13")
    rising = pd.Series(np.linspace(100, 300, len(idx)), index=idx)
    # fiel bis vor 2 Wochen, dann Sprung nach oben
    late = rising.copy()
    late.iloc[:-10] = np.linspace(300, 100, len(idx) - 10)
    series = {"DAX": rising, "SMI": late}
    day = dt.date(2026, 10, 30)
    past = ac.past_totals(series, None, day, ["DAX", "SMI"])
    cut = {k: s[s.index <= pd.Timestamp(day)] for k, s in series.items()}
    assert past == ac.past_totals(cut, None, day, ["DAX", "SMI"])           # Zukunft egal
    assert past["DAX"] == ac.total_of(None, None, ac.trend_at(rising, day)["score"])
    cur = pd.DataFrame({"key": ["DAX", "SMI"], "total": [7.5, 2.5]})
    chg = ac.score_changes(cur, series, None, dt.date(2026, 11, 13), weeks=(2,)).set_index("key")
    assert chg.at["DAX", "chg_2w"] == round(7.5 - ac.past_totals(series, None, dt.date(2026, 10, 30))["DAX"], 1)
    assert chg.at["SMI", "chg_2w"] > 0                                       # war bearish


def test_score_spark_fixed_scale_and_colour():
    assert ov.score_spark_svg(None) == "" and ov.score_spark_svg([3.0]) == ""
    svg = ov.score_spark_svg([-10.0, 0.0, 10.0])
    # fixed scale: -10 at the bottom edge (y=28), +10 at the top (y=2), regardless of the data range
    assert "0.0,28.0" in svg and "200.0,2.0" in svg
    assert "#2e7d32" in ov.score_spark_svg([0.0, 6.0])
    assert "#ef6c00" in ov.score_spark_svg([0.0, -2.0])
    assert "#9e9e9e" in ov.score_spark_svg([0.0, 1.0])


def test_score_lines_appends_current_total():
    df = pd.DataFrame({"key": ["DAX", "GOLD", "SMI"], "total": [4.0, float("nan"), 1.0]})
    weekly = {"DAX": list(range(30)), "GOLD": [1.0, 2.0]}
    out = ov.score_lines(df, weekly, weeks=3)
    assert out["DAX"] == [27, 28, 29, 4.0]
    assert out["GOLD"] == [1.0, 2.0]          # no current total -> history only
    assert "SMI" not in out                   # no stored history -> no line
