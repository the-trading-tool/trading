"""Standard deviation channel: Pine equivalence, causality, history gate, engine map."""
import numpy as np
import pandas as pd
import pytest

from tradinglib.indicator import sdc


def _close(years=12, seed=1):
    days = pd.bdate_range('2010-01-04', periods=int(252 * years))
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, len(days)))), index=days)


def _pine(values):
    """The Pine script's slpclc + devcalc on one window, newest bar first."""
    src = values[::-1]
    n = len(src)
    per = np.arange(1, n + 1, dtype=float)
    sx, sy, sxx, sxy = per.sum(), src.sum(), (per * per).sum(), (src * per).sum()
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    intercept = sy / n - slope * sx / n + slope
    val = intercept + slope * np.arange(n)
    return intercept, np.sqrt(((src - val) ** 2).sum() / (n - 1))


def test_matches_the_pine_script():
    c = _close()
    f = sdc.rolling_channel(c, years=10)
    day = f.dropna().index[200]
    window = c[(c.index > day - sdc.window(10)) & (c.index <= day)].to_numpy()
    mid, std = _pine(window)
    assert f.loc[day, 'sdc_mid'] == pytest.approx(mid, rel=1e-9)
    assert f.loc[day, 'sdc_std'] == pytest.approx(std, rel=1e-9)
    row = f.loc[day]
    assert row['sdc_up1'] == pytest.approx(mid + std) and row['sdc_up2'] == pytest.approx(mid + 2 * std)
    assert row['sdc_lo1'] == pytest.approx(mid - 3 * std) and row['sdc_lo2'] == pytest.approx(mid - 4 * std)
    assert row['sdc_pos'] == pytest.approx((c[day] - mid) / std)


def test_last_rolling_value_is_the_drawn_channel():
    c = _close()
    f = sdc.rolling_channel(c, years=10)
    mid, std, r = sdc.fit_last(c, years=10)
    assert f['sdc_mid'].iloc[-1] == pytest.approx(mid.iloc[-1])
    assert f['sdc_std'].iloc[-1] == pytest.approx(std)
    assert f['sdc_r'].iloc[-1] == pytest.approx(r)


def test_causal():
    c = _close()
    full = sdc.rolling_channel(c, years=10)
    cut = sdc.rolling_channel(c.iloc[:-100], years=10)
    pd.testing.assert_frame_equal(full.iloc[:-100].dropna(), cut.dropna(), rtol=1e-9)


def test_needs_the_full_window():
    c = _close(years=9)
    assert sdc.rolling_channel(c, years=10).dropna().empty
    assert sdc.fit_last(c, years=10) is None
    assert not sdc.rolling_channel(c, years=5).dropna().empty


def test_engine_map_matches():
    from asset_perf2 import INDICATOR_BACKFILL_MAP
    assert tuple(INDICATOR_BACKFILL_MAP['sdc']) == sdc.COLUMNS


def test_overlay_on_weekly_chart_bars(monkeypatch):
    from tradinglib import four_ps
    c = _close()
    monkeypatch.setattr(four_ps, 'load_daily', lambda t, db_path=None: pd.DataFrame({'Close': c}))
    weekly = c.resample('W-MON', label='left', closed='left').last().dropna()
    df = pd.DataFrame({'Close': weekly.to_numpy()}, index=weekly.index.rename('Date'))
    ind = sdc.Sdc(df, symbol='X', years=10, mult_lo1=2.5)
    assert ind.df['sdc_mid'].notna().any()
    fig = ind.add_fig()
    lines = [t for t in fig.data if t.name and t.name.startswith('SDC') and t.name != 'SDC fill']
    assert len(lines) == 5 and any('2.5σ' in t.name for t in lines)
    mid = np.asarray(lines[0].y, dtype=float)
    # Drawn only over the fit window; the rest of the chart stays empty.
    assert np.isnan(mid).any() and np.isfinite(mid).any()


def _short(monkeypatch, years):
    from tradinglib import four_ps
    c = _close(years=years)
    monkeypatch.setattr(four_ps, 'load_daily', lambda t, db_path=None: pd.DataFrame({'Close': c}))
    df = pd.DataFrame({'Close': c.to_numpy()}, index=c.index.rename('Date'))
    return sdc.Sdc(df, symbol='X')


def test_short_history_is_drawn_but_not_stored(monkeypatch):
    ind = _short(monkeypatch, 3)
    assert ind.df['sdc_mid'].isna().all()          # columns need the full 10 years
    fig = ind.add_fig()
    names = [t.name for t in fig.data]
    assert any('of 10y' in n for n in names) and 'SDC fill' in names
    reg = next(t for t in fig.data if t.name.startswith('SDC regression'))
    assert reg.line.color == sdc.DEFAULT_COLORS['mid'] and reg.line.dash == 'solid'


def test_under_a_year_draws_nothing(monkeypatch):
    assert len(_short(monkeypatch, 0.5).add_fig().data) == 0


def test_colors_are_distinct_and_configurable():
    lines = {k: v for k, v in sdc.DEFAULT_COLORS.items() if k != 'fill'}
    assert len(set(lines.values())) == len(lines)
    ind = sdc.Sdc(pd.DataFrame({'Close': [1.0, 2.0]}), color_lo1='#123456')
    assert ind.colors['lo1'] == '#123456' and ind.colors['mid'] == sdc.DEFAULT_COLORS['mid']


def test_log_variant_fits_ln_close_and_keeps_bands_positive():
    days = pd.bdate_range('2010-01-04', periods=252 * 12)
    rng = np.random.default_rng(3)
    c = pd.Series(np.exp(np.linspace(0, 4, len(days)) + rng.normal(0, 0.15, len(days))),
                  index=days)                                   # fast exponential uptrend
    log = sdc.rolling_channel(c, years=10, log=True)
    lmid, lstd, _ = sdc.fit_last(c, years=10)
    assert lmid.iloc[0] - 4 * lstd < 0                          # linear: left end below zero
    gmid, gstd, _ = sdc.fit_last(c, years=10, log=True)
    assert np.exp(gmid.iloc[0] - 4 * gstd) > 0
    assert (log[['sdc_lo1', 'sdc_lo2']].dropna() > 0).all().all()
    day = log.dropna().index[100]
    window = c[(c.index > day - sdc.window(10)) & (c.index <= day)].to_numpy()
    mid, std = _pine(np.log(window))
    assert log.loc[day, 'sdc_mid'] == pytest.approx(np.exp(mid), rel=1e-9)
    assert log.loc[day, 'sdc_std'] == pytest.approx(std, rel=1e-9)
    assert log.loc[day, 'sdc_lo1'] == pytest.approx(np.exp(mid - 3 * std), rel=1e-9)
    assert log.loc[day, 'sdc_pos'] == pytest.approx((np.log(c[day]) - mid) / std)
    fit, fstd, _ = sdc.fit_last(c, years=10, log=True)
    assert np.exp(fit.iloc[-1]) == pytest.approx(log['sdc_mid'].iloc[-1])
    assert fstd == pytest.approx(log['sdc_std'].iloc[-1])


def test_log_option_reaches_the_drawing(monkeypatch):
    from tradinglib import four_ps
    c = _close()
    monkeypatch.setattr(four_ps, 'load_daily', lambda t, db_path=None: pd.DataFrame({'Close': c}))
    df = pd.DataFrame({'Close': c.to_numpy()}, index=c.index.rename('Date'))
    ind = sdc.Sdc(df, symbol='X', log_scale=True)
    reg = next(t for t in ind.add_fig().data if t.name.startswith('SDC regression'))
    assert ', log' in reg.name
    assert ind.df['sdc_mid'].iloc[-1] == pytest.approx(np.asarray(reg.y, dtype=float)[-1])


def test_drawing_under_copy_on_write(monkeypatch):
    """The app runs pandas with copy-on-write: to_numpy() views are read-only there."""
    from tradinglib import four_ps
    c = _close(years=3)
    monkeypatch.setattr(four_ps, 'load_daily', lambda t, db_path=None: pd.DataFrame({'Close': c}))
    df = pd.DataFrame({'Close': c.to_numpy()}, index=c.index.rename('Date'))
    with pd.option_context('mode.copy_on_write', True):
        assert len(sdc.Sdc(df, symbol='X').add_fig().data) == 6
