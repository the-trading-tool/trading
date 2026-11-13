"""Asset-Scorecard: Gesamt je Markt, Technik, kausaler Verlauf."""
import datetime as dt
import json
import re

import numpy as np
import pandas as pd
import pytest

from tradinglib import asset_card_data as ac
from tradinglib import total_data as tot


def test_total_of_matches_combine():
    markets = [("USD", "", "", "fx", "USD"), ("GOLD", "", "", "commodities", None)]
    cot = pd.DataFrame({"key": ["USD", "GOLD"], "score": [2, -1]})
    macro = pd.DataFrame({"score": [5]}, index=["USD"])
    trend = pd.DataFrame({"key": ["USD", "GOLD"], "score": [3, -2]})
    df = tot.combine(cot, macro, trend, markets=markets).set_index("key")
    assert ac.total_of(2, 5, 3) == df.at["USD", "total"]
    assert ac.total_of(-1, None, -2) == df.at["GOLD", "total"]
    assert ac.total_of(None, None, None) is None


def test_technicals_states_and_moves():
    s = pd.Series(np.linspace(100, 200, 300), index=pd.bdate_range("2025-01-01", periods=300))
    x = ac.technicals(s)
    assert all(x[f"sma{w}_state"] == 1 for w in ac.SMA_WINDOWS)
    assert ac.technicals(s, sign=-1)["sma200_state"] == -1
    assert x["move7"] > 0 and x["move90"] > 0
    assert ac.vol_level(10) == "low" and ac.vol_level(50) == "normal" and ac.vol_level(90) == "high"
    assert ac.vol_level(float("nan")) == "na"


def test_cot_uses_release_date_not_report_date():
    hist = pd.DataFrame({"report_date": ["2026-11-03", "2026-11-10"], "score": [1, -2],
                         "release_date": pd.to_datetime(["2026-11-06", "2026-11-13"])})
    assert ac.cot_at(hist, dt.date(2026, 11, 12))["score"] == 1     # Report vom 10.11. noch nicht draussen
    assert ac.cot_at(hist, dt.date(2026, 11, 13))["score"] == -2
    assert ac.cot_at(hist, dt.date(2026, 11, 1)) is None


def test_macro_values_only_after_estimated_release():
    obs = pd.DataFrame({"ccy": "USD", "indicator": "cpi", "freq": "M", "value": [3.0, 3.5],
                        "period": pd.to_datetime(["2026-08-01", "2026-09-01"]), "source": "x"})
    known = ac.obs_known_at(obs, dt.date(2026, 11, 5))
    assert list(known["period"].dt.month) == [8]                     # September-CPI erst ~10.11.
    assert len(ac.obs_known_at(obs, dt.date(2026, 11, 13))) == 2


def test_history_is_causal():
    idx = pd.bdate_range("2014-01-01", "2026-11-13")
    rng = np.random.default_rng(0)
    s = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, len(idx))), index=idx)
    today = dt.date(2026, 11, 13)
    full = ac.history("DAX", s, None, None, weeks=8, today=today)
    cut = ac.history("DAX", s[s.index <= "2026-10-16"], None, None, weeks=8, today=today)
    early = full["date"] <= "2026-10-16"
    pd.testing.assert_series_equal(full.loc[early, "total"], cut.loc[early, "total"])
    assert full["date"].iloc[-1] == pd.Timestamp(today)
    assert full["cot"].isna().all() and full["macro"].isna().all()


def test_markets_cover_trend_sources():
    from tradinglib import trend_data as td
    assert {k for k, _g in ac.markets()} == set(td.SOURCES)


def test_page_locale_keys_exist():
    src = open("tradinglib/asset_card_page.py", encoding="utf-8").read()
    keys = set(re.findall(r't\("([a-z_]+\.[a-z_0-9]+)"', src))
    keys |= {f"asset.vol_{v}" for v in ("low", "normal", "high", "na")} | {"edge.card_asset"}
    for lang in ("de", "en"):
        d = json.load(open(f"locales/{lang}.json", encoding="utf-8"))
        assert not sorted(k for k in keys if k not in d), lang
