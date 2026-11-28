"""ATR in asset_perf2: precomputed once per ticker == computed per row, and no side effects."""
import numpy as np
import pandas as pd

import asset_perf2


def _frame(n=300, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    high = close * (1 + rng.uniform(0, 0.03, n))
    low = close * (1 - rng.uniform(0, 0.03, n))
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close}, index=idx)


def _old_calc_atr_percent(df, period=14):
    """The implementation before 2026-11-28, verbatim."""
    df['H-L'] = abs(df['High'] - df['Low'])
    df['H-C'] = abs(df['High'] - df['Close'].shift(1))
    df['L-C'] = abs(df['Low'] - df['Close'].shift(1))
    df['TR'] = df[['H-L', 'H-C', 'L-C']].max(axis=1)
    atr = df['TR'].rolling(window=period).mean()
    from tradinglib.utils import DataUtils
    return round(DataUtils.safe_last(atr, default=0), 2)


def test_series_value_equals_per_row_value_bit_for_bit():
    df = _frame()
    start = df.index[20]
    series = asset_perf2.atr_series(df.loc[start:])
    for date in df.index[20:]:
        per_row = _old_calc_atr_percent(df.loc[start:date].copy())
        v = series.at[date]
        pre = round(v.item() if isinstance(v, np.generic) else v, 2)
        if np.isnan(per_row):
            assert np.isnan(pre)
        else:
            assert pre == per_row and type(pre) is type(per_row)


def test_calc_atr_percent_unchanged_and_leaves_frame_alone():
    df = _frame()
    before = list(df.columns)
    assert asset_perf2.calc_atr_percent(df) == _old_calc_atr_percent(df.copy())
    assert list(df.columns) == before


def test_fill_pdict_uses_the_given_atr():
    df = _frame(60)
    df["daily_returns"] = df["Close"].pct_change() * 100
    p = asset_perf2.fill_pdict("T", pd.DataFrame(), df, pd.DataFrame(), pd.DataFrame(),
                               ft=type("FT", (), {"pre": None})(), atr=1.23)
    assert p["atr"] == 1.23
    assert p["stop_loss"] == p["close"] - 2 * 1.23
