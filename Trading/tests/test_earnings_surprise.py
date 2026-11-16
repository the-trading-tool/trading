"""Earnings surprise columns (eps): effective day, predecessor, day count, causality."""
import numpy as np
import pandas as pd

from tradinglib import earnings_surprise as es


def _rep(*pairs):
    return pd.DataFrame({'report_date': pd.to_datetime([d for d, _ in pairs]),
                         'surprise_pct': [float(s) for _, s in pairs]})


def test_report_counts_from_the_next_trading_day():
    days = pd.bdate_range('2026-01-05', periods=6)          # Mon..Mon
    out = es.daily_columns(days, _rep(('2026-01-06', 12.5)))
    assert np.isnan(out['eps_surprise'].iloc[1])            # report day itself: not yet
    assert out['eps_surprise'].iloc[2] == 12.5              # first day after
    assert out['eps_days'].tolist() == [0, 0, 1, 2, 3, 4]


def test_weekend_report_takes_effect_on_monday():
    days = pd.bdate_range('2026-01-05', periods=10)
    out = es.daily_columns(days, _rep(('2026-01-10', -3.0)))  # Saturday
    first = out.index[out['eps_days'] == 1][0]
    assert first == pd.Timestamp('2026-01-12')


def test_previous_surprise_and_reset():
    days = pd.bdate_range('2026-01-01', periods=12)
    out = es.daily_columns(days, _rep(('2026-01-02', 3), ('2026-01-08', -2), ('2026-01-13', 10)))
    last = out.iloc[-1]
    assert (last['eps_surprise'], last['eps_surprise_prev']) == (10.0, -2.0)
    assert out.loc['2026-01-09', 'eps_surprise_prev'] == 3.0
    assert out.loc['2026-01-14', 'eps_days'] == 1


def test_no_reports_means_zero_days_and_nan():
    days = pd.bdate_range('2026-01-01', periods=5)
    out = es.daily_columns(days, _rep())
    assert out['eps_days'].eq(0).all() and out['eps_surprise'].isna().all()


def test_cutting_the_future_changes_no_past_value():
    days = pd.bdate_range('2025-01-01', '2026-06-30')
    rep = _rep(('2025-02-03', 5), ('2025-05-05', -1), ('2025-08-04', 30), ('2025-11-03', 2),
               ('2026-02-02', -7), ('2026-05-04', 4))
    full = es.daily_columns(days, rep)
    cut = es.daily_columns(days[days <= '2025-12-31'], rep)
    pd.testing.assert_frame_equal(full.loc[cut.index], cut)


def test_indicator_projects_onto_chart_bars(monkeypatch):
    from tradinglib import four_ps
    from tradinglib.indicator import eps as eps_mod
    days = pd.bdate_range('2026-03-02', periods=20)
    hist = pd.DataFrame({'Close': np.arange(20.0) + 1}, index=days)
    monkeypatch.setattr(four_ps, 'load_daily', lambda tk, db_path=None: hist)
    monkeypatch.setattr(es, 'reports', lambda tk, db_path=None: _rep(('2026-03-10', 25)))
    chart = hist.tail(8).copy()
    ind = eps_mod.Eps(chart, symbol='X')
    # Day count runs on the full history, not on the eight chart bars.
    full = es.daily_columns(days, _rep(('2026-03-10', 25)))
    assert ind.df['eps_days'].tolist() == full['eps_days'].tail(8).tolist()
    assert (ind.df['eps_surprise'] == 25).all()


def test_indicator_without_data_writes_zero_days(monkeypatch):
    from tradinglib import four_ps
    from tradinglib.indicator import eps as eps_mod
    days = pd.bdate_range('2026-03-02', periods=5)
    monkeypatch.setattr(four_ps, 'load_daily', lambda tk, db_path=None: pd.DataFrame())
    monkeypatch.setattr(es, 'reports', lambda tk, db_path=None: _rep())
    ind = eps_mod.Eps(pd.DataFrame({'Close': [1.0] * 5}, index=days), symbol='^IDX')
    assert ind.df['eps_days'].eq(0).all()
    assert ind.df['eps_surprise'].isna().all()


def test_engine_map_matches_indicator_columns():
    from asset_perf2 import INDICATOR_BACKFILL_MAP
    from tradinglib.indicator.eps import COLUMNS
    assert tuple(INDICATOR_BACKFILL_MAP['eps']) == COLUMNS == es.COLUMNS
