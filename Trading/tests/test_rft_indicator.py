"""Range Filtered Trend (port of AlgoAlpha's Pine script)."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib.indicator import rft


def _ohlc(closes, start='2022-01-03', freq='B'):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq=freq)
    return pd.DataFrame({'Open': np.r_[closes[0], closes[:-1]], 'High': closes * 1.01,
                         'Low': closes * 0.99, 'Close': closes}, index=idx)


def _walk(n=900, seed=3):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))


def test_kalman_startet_wie_in_pine():
    """Bar 0 has no a[1] -> na; bar 1 starts from the previous close."""
    a = np.array([10.0, 11.0, 12.0])
    k = rft.kalman(a, period=77, alpha=0.01, beta=0.1)
    assert np.isnan(k[0])
    v3 = 0.01 * 77
    v2 = (1 - 1 / (1 + v3)) * 1.0 + 0.1 / 77          # v2 after bar 0
    gain = v2 / (v2 + v3)
    assert abs(k[1] - (10.0 + gain * (11.0 - 10.0))) < 1e-12


def test_atr_ist_wilder_mit_sma_start():
    h, l, c = np.full(5, 11.0), np.full(5, 9.0), np.full(5, 10.0)
    atr = rft.atr_rma(h, l, c, 3)
    assert np.isnan(atr[1]) and atr[2] == 2.0 and atr[4] == 2.0


def test_kausal_zukunft_abschneiden_aendert_nichts():
    frame = _ohlc(_walk())
    full = rft.compute(frame)
    for cut in (300, 650):
        pd.testing.assert_frame_equal(full.iloc[:cut], rft.compute(frame.iloc[:cut]))


def test_aufwaertstrend_wird_als_trend_erkannt():
    closes = np.r_[np.full(250, 100.0) + np.sin(np.arange(250)) * 0.5,
                   100 * 1.004 ** np.arange(1, 251)]
    out = rft.compute(_ohlc(closes))
    tail = out.iloc[-50:]
    assert (tail['rft_trend'] == 1).all() and (tail['rft_ktrend'] == 1).all()
    assert (tail['rft_state'] == 1).all()
    starts = out[out['rft_signal'] != 0]
    assert len(starts) >= 1 and starts['rft_signal'].iloc[-1] == 1


def test_signal_nur_wenn_zustand_ueber_null_kreuzt():
    out = rft.compute(_ohlc(_walk(seed=11)))
    state = out['rft_state']
    crossed = (state > 0) & (state.shift(1) <= 0)
    assert ((out['rft_signal'] != 0) == crossed).all()
    assert set(out['rft_signal'].unique()) <= {-1.0, 0.0, 1.0}
    assert (out.loc[crossed, 'rft_signal'] == out.loc[crossed, 'rft_trend']).all()


def test_tageschart_nimmt_vorlauf_aus_der_historie(monkeypatch):
    from tradinglib import four_ps
    full = _ohlc(_walk())
    hist = full.iloc[:-1]                          # local DB lacks today's bar
    chart = full.iloc[-250:].copy()                # the chart has it
    monkeypatch.setattr(four_ps, 'load_daily', lambda s, *a, **k: hist)
    ind = rft.Rft(chart.copy(), symbol='TEST')
    expected = rft.compute(full.iloc[-(250 + rft.WARMUP_BARS):]).iloc[-250:]
    for col in rft.COLUMNS:
        np.testing.assert_allclose(ind.df[col].to_numpy(), expected[col].to_numpy(), equal_nan=True)
    assert ind.df['rft_upper'].notna().all()       # no 200-bar hole in the chart


def test_intraday_rechnet_auf_eigenen_balken(monkeypatch):
    from tradinglib import four_ps
    monkeypatch.setattr(four_ps, 'load_daily', lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    chart = _ohlc(_walk(400), start='2026-10-20 09:00', freq='15min')
    ind = rft.Rft(chart.copy(), symbol='TEST')
    expected = rft.compute(chart)
    np.testing.assert_allclose(ind.df['rft_kalman'].to_numpy(), expected['rft_kalman'].to_numpy(),
                               equal_nan=True)


def test_figur_wird_gezeichnet():
    ind = rft.Rft(_ohlc(_walk(500)))
    ind.df.index.name = 'Date'
    ind.add_fig()
    names = {t.name for t in ind.fig.data}
    assert {'Kalman trend', 'Upper band', 'Lower band', 'Trend start'} <= names


def test_ohne_ohlc_leere_spalten():
    ind = rft.Rft(pd.DataFrame({'Close': [1.0, 2.0]}, index=pd.date_range('2026-01-01', periods=2)))
    assert all(ind.df[c].isna().all() for c in rft.COLUMNS)
