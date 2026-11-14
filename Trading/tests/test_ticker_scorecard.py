"""Scorecard-Tab im Asset Viewer: Zuordnung, Komponenten, Kausalitaet, Locale-Keys."""
import datetime as dt
import json
import re

import numpy as np
import pandas as pd
import pytest

from tradinglib import ticker_scorecard as tsc

IDX = pd.bdate_range("2014-01-01", "2026-11-13")


def test_edge_market_mapping():
    assert tsc.edge_market_for("^GDAXI") == "DAX"
    assert tsc.edge_market_for("^GSPC") == tsc.edge_market_for("^SPX") == "SPX"
    assert tsc.edge_market_for("GC=F") == "GOLD" and tsc.edge_market_for("EURUSD=X") == "EUR"
    assert tsc.edge_market_for("JPY=X") == "JPY" and tsc.edge_market_for("BTC-USD") == "BTC"
    assert tsc.edge_market_for("SAP.DE") is None and tsc.edge_market_for("") is None


@pytest.mark.parametrize("diff,pts", [(12, 2), (10, 2), (3, 1), (1.9, 0), (-2, -1), (-15, -2), (None, None)])
def test_rs_points(diff, pts):
    assert tsc.rs_points(diff) == pts


def test_combine_normalises_and_skips_missing():
    assert tsc.combine(4, 2, 10) == 10.0
    assert tsc.combine(-2, None, None) == -5.0
    assert tsc.combine(None, None, None) is None
    assert tsc.combine(2, -1, 5) == round(10 * (0.5 - 0.5 + 0.5) / 3, 1)


def test_card_components_and_brakes():
    stock = pd.Series(np.geomspace(100, 2000, len(IDX)), index=IDX)
    index = pd.Series(np.linspace(100, 110, len(IDX)), index=IDX)
    c = tsc.card_at(stock, index, dt.date(2026, 11, 13))
    assert c["trend"] >= 3 and c["rs"] >= 1 and c["market"] > 0 and c["bias"] in ("bull", "strong_bull")
    alone = tsc.card_at(stock, None, dt.date(2026, 11, 13))
    assert alone["n"] == 1 and alone["rs"] is None and alone["bias"] == "bull"   # Deckel


def test_card_and_history_are_causal():
    rng = np.random.default_rng(1)
    stock = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(IDX)))), index=IDX)
    index = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.008, len(IDX)))), index=IDX)
    day = dt.date(2026, 6, 30)
    full = tsc.card_at(stock, index, day)
    cut = tsc.card_at(stock[:str(day)], index[:str(day)], day)
    assert full["total"] == cut["total"] and full["rs_diff"] == cut["rs_diff"]
    h = tsc.history(stock, index, weeks=8, today=dt.date(2026, 11, 13))
    assert len(h) == 8 and h["date"].is_monotonic_increasing
    assert tsc.change(h, 1) == round(h["total"].iloc[-1] - h["total"].iloc[-2], 1)


def test_sim_context_missing_db(monkeypatch, tmp_path):
    monkeypatch.setenv("TradingDB", str(tmp_path))
    assert tsc.sim_context("SAP.DE") == {} and tsc.listing_currency("SAP.DE") is None


def test_locale_keys_exist():
    src = open("tradinglib/ticker_scorecard_page.py", encoding="utf-8").read()
    keys = set(re.findall(r't\(f?"((?:tsc|main)\.[a-z0-9_]+)"', src)) - {"tsc.chg_"}
    keys |= {"tsc.chg_1w", "tsc.chg_4w", "main.tab_scorecard", "tsc.method_md"}
    for lang in ("de", "en"):
        d = json.load(open(f"locales/{lang}.json", encoding="utf-8"))
        assert not [k for k in keys if k not in d], lang
