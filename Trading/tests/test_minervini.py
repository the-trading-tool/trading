"""Minervini Trend Template: per-ticker conditions, RS rating, live indicator."""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import minervini as mv


def _series(values, start='2024-01-01'):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def _uptrend(n=400):
    return _series(100 * 1.002 ** np.arange(n))


def test_stetiger_aufwaertstrend_erfuellt_alle_sieben():
    out = mv.trend_template(_uptrend())
    last = out.iloc[-1]
    assert last['mtt_score'] == 7 and last['mtt_pass'] == 1
    assert last['mtt_slope200'] > 0 and last['mtt_rs_raw'] > 0
    assert mv.criteria(_uptrend()).iloc[-1].all()


def test_abwaertstrend_faellt_durch():
    out = mv.trend_template(_series(100 * 0.998 ** np.arange(400)))
    assert out['mtt_pass'].iloc[-1] == 0 and out['mtt_score'].iloc[-1] <= 1


def test_ohne_vorlauf_nicht_berechnet():
    """Before 252 bars there is no 52-week range: not computed, not failed."""
    out = mv.trend_template(_uptrend())
    assert out['mtt_score'].iloc[:251].isna().all()
    assert out['mtt_score'].iloc[252:].notna().all()


def test_kausal_zukunft_abschneiden_aendert_nichts():
    close = _series(100 + np.cumsum(np.random.default_rng(1).normal(0.1, 1.5, 600)))
    full = mv.trend_template(close)
    part = mv.trend_template(close.iloc[:400])
    pd.testing.assert_frame_equal(full.iloc[:400], part)


def test_nahe_hoch_und_abstand_vom_tief():
    """Conditions 6 and 7: +30 % over the low, within 25 % of the high."""
    up = list(100 * 1.003 ** np.arange(300))                # rally to ~245
    close = _series(up + [up[-1] * 0.70] * 5)               # 30 % below the high
    crit = mv.criteria(close)
    assert not crit['near_high'].iloc[-1]
    assert crit['above_low'].iloc[-1]


def _frame(raws, day='2026-10-16 00:00:00', prefix='M'):
    return pd.DataFrame({'ticker': [f'{prefix}{i}' for i in range(len(raws))],
                         'Date': day, 'mtt_rs_raw': raws})


def test_rs_rating_skala_1_bis_99():
    frame = _frame(np.linspace(-0.5, 1.5, 100))
    markets = {t: 'USD' for t in frame['ticker']}
    rating = mv.rs_rating(frame, markets, set(frame['ticker']))
    assert rating.min() == 1 and rating.max() == 99
    assert rating.is_monotonic_increasing


def test_nichtmitglied_veraendert_die_skala_nicht():
    members = _frame(np.linspace(-0.5, 1.5, 60))
    outsider = pd.DataFrame({'ticker': ['HELD'], 'Date': members['Date'].iloc[0],
                             'mtt_rs_raw': [9.0]})
    markets = {t: 'USD' for t in list(members['ticker']) + ['HELD']}
    alone = mv.rs_rating(members, markets, set(members['ticker']))
    both = mv.rs_rating(pd.concat([members, outsider], ignore_index=True), markets,
                        set(members['ticker']))
    assert both.iloc[:60].tolist() == alone.tolist()
    assert both.iloc[60] == 99


def test_maerkte_werden_getrennt_gerankt():
    us, eu = _frame(np.linspace(0, 1, 60), prefix='U'), _frame(np.linspace(-1, -0.01, 60), prefix='E')
    frame = pd.concat([us, eu], ignore_index=True)
    markets = {**{t: 'USD' for t in us['ticker']}, **{t: 'EUR' for t in eu['ticker']}}
    rating = mv.rs_rating(frame, markets, set(frame['ticker']))
    # The best EUR name is rated 99 although it is below every USD name.
    assert rating.iloc[59] == 99 and rating.iloc[119] == 99


def test_zu_kleiner_markt_und_null_bleiben_leer():
    frame = _frame(list(np.linspace(0, 1, 30)) + [0.0])
    markets = {t: 'USD' for t in frame['ticker']}
    assert mv.rs_rating(frame, markets, set(frame['ticker'])).isna().all()


def test_update_rs_schreibt_ab_stichtag(tmp_path, monkeypatch):
    path = str(tmp_path / 'asset_simulation_.db')
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, mtt_rs_raw REAL)")
    rows = []
    for day in ('2026-10-15 00:00:00', '2026-10-16 00:00:00'):
        rows += [(f'M{i}', day, float(i)) for i in range(1, 61)]
    con.executemany("INSERT INTO asset_simulation VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    monkeypatch.setattr(mv, 'load_markets',
                        lambda *a, **k: ({f'M{i}': 'USD' for i in range(1, 61)},
                                         {f'M{i}' for i in range(1, 61)}))
    assert mv.update_rs(path, since='2026-10-16') == 60
    con = sqlite3.connect(path)
    got = dict(con.execute("SELECT Date, MAX(mtt_rs) FROM asset_simulation GROUP BY Date"))
    con.close()
    assert got == {'2026-10-15 00:00:00': 0.0, '2026-10-16 00:00:00': 99.0}


def test_indikator_traegt_letztes_rating_auf_neuen_balken(monkeypatch):
    from tradinglib import four_ps
    from tradinglib.indicator.mtt import Mtt, COLUMNS
    close = _uptrend(320)
    monkeypatch.setattr(four_ps, 'load_daily', lambda s, *a, **k: pd.DataFrame({'Close': close}))
    stored = pd.Series([80.0, 85.0], index=close.index[-3:-1])        # nothing for the last bar
    monkeypatch.setattr(mv, 'stored_rs', lambda *a, **k: stored)
    chart = pd.DataFrame({'Close': close.iloc[-30:]})
    ind = Mtt(chart.copy(), symbol='TEST')
    assert set(COLUMNS) <= set(ind.df.columns)
    assert ind.df['mtt_rs'].iloc[-1] == 85.0
    assert ind.df['mtt_pass'].iloc[-1] == 1
    assert ind.df['mtt_score'].iloc[-1] == mv.trend_template(close)['mtt_score'].iloc[-1]


def test_spalten_passen_zur_engine():
    import asset_perf2
    from tradinglib.indicator.mtt import COLUMNS
    assert list(mv.TICKER_COLUMNS) == asset_perf2.INDICATOR_BACKFILL_MAP['mtt']
    assert list(COLUMNS) == list(mv.TICKER_COLUMNS) + [mv.RS_COLUMN]


def test_formel_aktiviert_den_indikator():
    from tradinglib.fetch_data import indicators_for_expressions
    assert indicators_for_expressions(['(mtt_pass == 1) & (mtt_rs >= 70)']) == ['mtt']
