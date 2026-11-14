"""Credit spread features: causal, fixed thresholds, publication lag."""
import numpy as np
import pandas as pd

from tradinglib import credit_data as cr


def _series(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_score_from_level_and_change():
    # long calm period at 2.0, then a widening to 3.0 -> top of the 3-year range and +1 Pp
    s = _series([2.0] * 800 + list(np.linspace(2.0, 3.0, 63)))
    f = cr.features(s)
    last = f.iloc[-1]
    assert last["rank3y"] == 1.0 and round(last["chg13"], 2) == 1.0 and last["score"] == -2
    # tightening back to a new low -> +2
    s2 = _series([3.0] * 800 + list(np.linspace(3.0, 1.5, 63)))
    assert cr.features(s2)["score"].iloc[-1] == 2
    # too little history -> no score
    assert cr.features(_series([2.0] * 100))["score"].isna().all()


def test_features_are_causal():
    s = _series(list(np.random.default_rng(1).normal(2, 0.3, 1200)))
    full = cr.features(s)
    cut = cr.features(s.iloc[:900])
    pd.testing.assert_frame_equal(full.iloc[:900], cut)


def test_known_at_uses_one_day_lag():
    f = cr.features(_series([2.0] * 900, start="2022-01-03"))
    f.loc[f.index[-1], "score"] = 99            # value observed on the last day
    day = f.index[-1]
    assert cr.known_at(f, [day])["score"].iloc[0] != 99           # not known the same day
    assert cr.known_at(f, [day + pd.Timedelta(days=1)])["score"].iloc[0] == 99


def test_bias_label():
    assert cr.bias_label(2) == "strong_bull" and cr.bias_label(-1) == "bear"
    assert cr.bias_label(None) == "neutral" and cr.bias_label(float("nan")) == "neutral"
