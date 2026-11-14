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


def test_single_component_bias_is_capped():
    assert tot.bias_label(7.5, n=1) == "bull"
    assert tot.bias_label(-7.5, n=1) == "bear"
    assert tot.bias_label(7.5, n=2) == "strong_bull"
    assert tot.bias_label(7.5) == "strong_bull"
    markets = [("DAX", "", "", "indices_world", None)]
    df = tot.combine(pd.DataFrame(columns=["key", "score"]), pd.DataFrame(columns=["score"]),
                     pd.DataFrame({"key": ["DAX"], "score": [3]}), markets=markets)
    assert df.at[0, "total"] == 7.5 and df.at[0, "bias"] == "bull"
    assert ac.n_components(None, None, 3) == 1 and ac.n_components(1, None, 3) == 1
    assert ac.n_components(1, 2, 3) == 2 and ac.total_of(3, None, 2) == ac.total_of(-3, None, 2) == 5.0


def test_relative_strength_vs_peer_median(monkeypatch):
    idx = pd.bdate_range("2026-01-01", "2026-11-13")
    growth = {"DAX": 1.10, "IBEX": 0.99, "SPX": 0.97, "SMI": 1.05, "N225": 0.92, "KOSPI": 0.85}
    series = {k: pd.Series(np.linspace(100, 100 * g, len(idx)), index=idx) for k, g in growth.items()}
    rs = ac.relative_strength("IBEX", series)
    assert rs["peer"] == "equity" and rs["n"] == 6
    others = [ac.period_return(series[k]) for k in growth if k != "IBEX"]
    assert rs["median"] == pytest.approx(float(np.median(others)))
    assert rs["rank"] == 3                                  # hinter DAX und SMI
    assert ac.relative_strength("EUR", series) is None      # FX: keine Vergleichsgruppe
    assert ac.relative_strength("IBEX", {k: series[k] for k in ("IBEX", "DAX")}) is None


def test_bias_needs_price_confirmation():
    # Bitcoin 11/2026: COT +2, Trend −1 → Gesamt +2,1, Kurs faellt → neutral
    assert tot.bias_for(2.1, 2, -1) == "neutral"
    assert tot.bias_for(-2.8, 3, -2) == "bear"                 # Trend bestaetigt
    assert tot.bias_for(2.5, 2, 0) == "bull"                   # neutraler Trend widerspricht nicht
    assert tot.bias_for(7.5, 1, 3) == "bull"                   # nur Trend → gedeckelt
    assert tot.bias_for(None, 0, None) == "neutral"
    # Bitcoin mit 3-M-Momentum: Trend +1, aber Kurs unter SMA200 → weiter neutral
    assert tot.bias_for(4.6, 2, 1, -1) == "neutral"
    assert tot.bias_for(4.6, 2, 1, 1) == "bull"
    assert tot.unconfirmed(-3.0, -2, 1) and not tot.unconfirmed(-3.0, -2, -1)
    markets = [("BTC", "", "", "crypto", None)]
    df = tot.combine(pd.DataFrame({"key": ["BTC"], "score": [2]}), pd.DataFrame(columns=["score"]),
                     pd.DataFrame({"key": ["BTC"], "score": [-1]}), markets=markets)
    assert df.at[0, "total"] == -2.5 and df.at[0, "bias"] == "bear"     # COT zaehlt nicht mehr
