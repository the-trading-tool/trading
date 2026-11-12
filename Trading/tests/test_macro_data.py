"""Makro-Scorecard: Perioden, Veränderungen, Punkte — ohne Netz/DB."""
import json
import re

import pandas as pd
import pytest

from tradinglib import macro_data as m


@pytest.mark.parametrize("raw,norm", [
    ("2026-10", ("2026-10-01", "M")),
    ("2026-Q1", ("2026-01-01", "Q")),
    ("2026-Q3", ("2026-07-01", "Q")),
    ("2026-10-01", ("2026-10-01", "M")),
])
def test_norm_period(raw, norm):
    assert m.norm_period(raw) == norm


def _obs(rows):
    df = pd.DataFrame(rows, columns=["ccy", "indicator", "period", "freq", "value"])
    df["period"] = pd.to_datetime(df["period"])
    df["source"] = "test"
    return df


def _monthly(ccy, ind, values, start="2026-01-01"):
    per = pd.date_range(start, periods=len(values), freq="MS").strftime("%Y-%m-%d")
    return [(ccy, ind, p, "M", v) for p, v in zip(per, values)]


def test_latest_uses_month_lag_and_quarter_step():
    rows = _monthly("USD", "rate", [3, 3, 3, 3, 3, 3.25, 3.5])            # 6 M: 3 → 3.5
    rows += [("CHF", "unemp", "2026-01-01", "Q", 5.2), ("CHF", "unemp", "2026-04-01", "Q", 5.0)]
    lat = m.latest(_obs(rows)).set_index(["ccy", "indicator"])
    assert lat.loc[("USD", "rate"), "change"] == pytest.approx(0.5)
    assert lat.loc[("CHF", "unemp"), "change"] == pytest.approx(-0.2)   # Vorquartal


def test_score_components_and_signs():
    rows = []
    # USD: hoher, steigender Zins; Inflation steigt; Arbeitslosigkeit faellt; BIP stark; CLI > 100 steigend
    rows += _monthly("USD", "rate", [4.0] * 6 + [4.5])
    rows += _monthly("USD", "cpi", [2.0, 2.0, 2.0, 2.0, 2.5])
    rows += _monthly("USD", "unemp", [4.5, 4.5, 4.5, 4.5, 4.0])
    rows += [("USD", "gdp", "2026-04-01", "Q", 0.8)]
    rows += _monthly("USD", "cli", [100.5, 100.8])
    # JPY: spiegelbildlich
    rows += _monthly("JPY", "rate", [1.0] * 6 + [0.5])
    rows += _monthly("JPY", "cpi", [3.0, 3.0, 3.0, 3.0, 2.5])
    rows += _monthly("JPY", "unemp", [2.5, 2.5, 2.5, 2.5, 3.0])
    rows += [("JPY", "gdp", "2026-04-01", "Q", -0.2)]
    rows += _monthly("JPY", "cli", [99.5, 99.2])
    sc = m.score_table(m.latest(_obs(rows)))
    assert sc.loc["USD"].to_dict() == {"p_rate": 2, "p_cpi": 1, "p_unemp": 1, "p_gdp": 1,
                                       "p_cli": 1, "score": 6, "bias": "strong_bull"}
    assert sc.loc["JPY", "score"] == -6
    assert sc.loc["JPY", "bias"] == "strong_bear"
    assert list(sc.index) == ["USD", "JPY"]                              # Reihenfolge CURRENCIES


def test_missing_indicator_scores_zero():
    rows = _monthly("CHF", "rate", [0.0] * 7) + _monthly("NZD", "rate", [2.0] * 7)
    sc = m.score_table(m.latest(_obs(rows)))
    assert sc.loc["CHF", "p_cli"] == 0 and sc.loc["CHF", "p_gdp"] == 0
    assert sc.loc["CHF", "p_rate"] == -1 and sc.loc["NZD", "p_rate"] == 1


@pytest.mark.parametrize("s,label", [(4, "strong_bull"), (2, "bull"), (1, "neutral"),
                                     (-2, "bear"), (-4, "strong_bear")])
def test_bias_label(s, label):
    assert m.bias_label(s) == label


def test_is_stale_throttles(tmp_path, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(m, "_DB_DIR", str(tmp_path))
    assert m.is_stale() is True
    con = m._connect()
    m._set_meta(con, "last_attempt", dt.datetime.now().isoformat())
    con.commit(); con.close()
    assert m.is_stale() is False                       # Fehlversuch gerade eben -> Sperre
    con = m._connect()
    old = (dt.datetime.now() - dt.timedelta(hours=7)).isoformat()
    m._set_meta(con, "last_attempt", old)
    m._set_meta(con, "last_success", (dt.datetime.now() - dt.timedelta(hours=1)).isoformat())
    con.commit(); con.close()
    assert m.is_stale() is False                       # Erfolg < 24 h


def test_page_locale_keys_exist():
    src = open("tradinglib/macro_page.py", encoding="utf-8").read()
    keys = set(re.findall(r't\("([a-z_]+\.[a-z_]+)"', src))
    for pat in ("macro.ind_{}", "macro.lag_{}", "macro.unit_{}"):
        keys |= {pat.format(i) for i in m.INDICATORS}
    for lang in ("de", "en"):
        d = json.load(open(f"locales/{lang}.json", encoding="utf-8"))
        assert not sorted(k for k in keys if k not in d), lang


def test_scorecard_view_keeps_every_delta_column(monkeypatch):
    """Regression: 'Δ 3 M' gab es fuer Inflation UND Arbeitslosigkeit — die zweite
    Spalte hat die erste ueberschrieben."""
    from tradinglib import macro_page as mp
    import streamlit as st
    rows = []
    for c, cpi, un in (("USD", [2.0, 2.0, 2.0, 2.0, 2.5], [4.0] * 5), ("JPY", [1.0] * 5, [2.0, 2.0, 2.0, 2.0, 2.6])):
        rows += _monthly(c, "cpi", cpi) + _monthly(c, "unemp", un) + _monthly(c, "rate", [1.0] * 7)
    lat = m.latest(_obs(rows))
    sc = m.score_table(lat)
    captured = {}
    monkeypatch.setattr(st, "dataframe", lambda data, **k: captured.setdefault("df", data))
    monkeypatch.setattr(st, "expander", lambda *a, **k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(st, "caption", lambda *a, **k: None)
    mp.MacroPage()._scorecard_tab(sc, lat)
    view = captured["df"].data
    num = lambda x: float(str(x).replace("+", "").replace(" %", ""))
    deltas = [c for c in view.columns if c.startswith("Δ")]
    assert len(deltas) == len(mp._IND)
    usd = view.iloc[0]
    assert num(usd[deltas[1]]) == pytest.approx(0.5)      # Inflation USD +0,5
    assert num(usd[deltas[2]]) == pytest.approx(0.0)      # Arbeitslosigkeit USD unveraendert
    assert "None" not in view.astype(str).values  # fehlende Werte = "–"
