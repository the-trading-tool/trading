"""Gesamt-Score: Normierung, fehlende Komponenten, Einigkeit, Paare."""
import json
import re

import pandas as pd
import pytest

from tradinglib import total_data as tot

MARKETS = [("USD", "", "", "fx", "USD"), ("EUR", "", "", "fx", "EUR"),
           ("JPY", "", "", "fx", "JPY"), ("GOLD", "", "", "commodities", None)]


def _frames(cot, macro, trend):
    return (pd.DataFrame({"key": list(cot), "score": list(cot.values())}),
            pd.DataFrame({"score": list(macro.values())}, index=list(macro)),
            pd.DataFrame({"key": list(trend), "score": list(trend.values())}))


def test_normalised_mean_and_missing_macro():
    df = tot.combine(*_frames({"USD": 3, "EUR": -3, "JPY": 0, "GOLD": 3},
                              {"USD": 6, "EUR": -3, "JPY": 3},
                              {"USD": 4, "EUR": -2, "JPY": 2, "GOLD": -4}), markets=MARKETS).set_index("key")
    assert df.at["USD", "total"] == pytest.approx(10.0)
    assert df.at["EUR", "total"] == pytest.approx(round(10 * (-1 - 0.5 - 0.5) / 3, 1))
    assert df.at["GOLD", "n"] == 2 and pd.isna(df.at["GOLD", "macro"])
    assert df.at["GOLD", "total"] == pytest.approx(0.0)          # +1 und −1 heben sich auf
    assert df.at["USD", "bias"] == "strong_bull"


def test_agreement_requires_same_sign_and_no_zero():
    df = tot.combine(*_frames({"USD": 1, "EUR": -1, "JPY": 0, "GOLD": 2},
                              {"USD": 2, "EUR": -1, "JPY": 2},
                              {"USD": 1, "EUR": 1, "JPY": 1, "GOLD": 1}), markets=MARKETS).set_index("key")
    assert df.at["USD", "agree"]
    assert not df.at["EUR", "agree"]          # Widerspruch
    assert not df.at["JPY", "agree"]          # COT neutral
    assert df.at["GOLD", "agree"]             # zwei Komponenten reichen


def test_market_without_any_component():
    df = tot.combine(*_frames({}, {}, {}), markets=MARKETS)
    assert df["total"].isna().all() and (df["bias"] == "neutral").all()


@pytest.mark.parametrize("s,label", [(5, "strong_bull"), (2, "bull"), (1.9, "neutral"),
                                     (-2, "bear"), (-5, "strong_bear")])
def test_bias_label(s, label):
    assert tot.bias_label(s) == label


def test_pair_setups_component_deltas():
    total = tot.combine(*_frames({"USD": 3, "EUR": -3, "JPY": 1, "GOLD": 0},
                                 {"USD": 6, "EUR": -6, "JPY": -2},
                                 {"USD": 4, "EUR": -4, "JPY": 2, "GOLD": 0}), markets=MARKETS)
    pairs = tot.pair_setups(total, ["USD", "EUR", "JPY"]).set_index("pair")
    assert set(pairs.index) == {"EURUSD", "EURJPY", "USDJPY"}
    assert pairs.at["EURUSD", "score"] == pytest.approx(-20.0)
    assert pairs.at["EURUSD", "d_cot"] == -6 and pairs.at["EURUSD", "d_macro"] == -12
    assert pairs.at["EURUSD", "agree"] and pairs.at["EURUSD", "bias"] == "strong_bear"
    assert pairs.index[0] == "EURUSD"                             # staerkstes zuerst


def test_page_locale_keys_exist():
    src = open("tradinglib/total_page.py", encoding="utf-8").read()
    keys = set(re.findall(r't\("([a-z_]+\.[a-z_0-9]+)"', src))
    keys |= {f"total.col_{c}" for c in ("cot", "macro", "trend", "d_cot", "d_macro", "d_trend")}
    for lang in ("de", "en"):
        d = json.load(open(f"locales/{lang}.json", encoding="utf-8"))
        assert not sorted(k for k in keys if k not in d), lang
