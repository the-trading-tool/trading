"""Markt- und Sektorkontext: Breite, Sektorrang, relative Staerke -- kausal."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from tradinglib import market_context as mc


def _panel(n_days=300, n_tickers=30, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2022-01-03', periods=n_days)
    rows = []
    for i in range(n_tickers):
        drift = 0.002 if i < n_tickers // 2 else -0.001
        p = 100 * np.cumprod(1 + drift + rng.normal(0, 0.01, n_days))
        for d, c in zip(dates, p):
            rows.append({'ticker': f'T{i:02d}', 'Date': d, 'close': c})
    df = pd.DataFrame(rows)
    idx = {f'T{i:02d}': '^X' for i in range(n_tickers)}
    sec = {f'T{i:02d}': ('Up' if i < n_tickers // 2 else 'Down') if i % 3 else 'Mixed'
           for i in range(n_tickers)}
    return df, idx, sec


def test_spalten_und_wertebereich():
    df, idx, sec = _panel()
    out = mc.add_context(df, idx, sec)
    for col in mc.COLUMNS:
        assert col in out.columns
    last = out[out['Date'] == out['Date'].max()]
    assert last['mkt_breadth200'].between(0, 100).all()
    assert last['rs_rank'].between(0, 100).all()
    # Die steigende Haelfte fuehrt bei der relativen Staerke
    up = last[last['ticker'].str[1:].astype(int) < 15]['rs_rank'].mean()
    down = last[last['ticker'].str[1:].astype(int) >= 15]['rs_rank'].mean()
    assert up > down


def test_sektor_rang():
    df, idx, sec = _panel()
    out = mc.add_context(df, idx, sec)
    last = out[out['Date'] == out['Date'].max()]
    ranks = last.groupby(last['ticker'].map(sec))['sec_rank'].first()
    assert ranks['Up'] > ranks['Down']


def test_kausal_zukunft_abschneiden_aendert_nichts():
    df, idx, sec = _panel()
    full = mc.add_context(df, idx, sec)
    cut_date = df['Date'].sort_values().unique()[250]
    part = mc.add_context(df[df['Date'] <= cut_date], idx, sec)
    a = full[full['Date'] <= cut_date].set_index(['ticker', 'Date'])[list(mc.COLUMNS)]
    b = part.set_index(['ticker', 'Date'])[list(mc.COLUMNS)]
    pd.testing.assert_frame_equal(a.sort_index(), b.sort_index())


def test_ohne_index_kein_kontext():
    df, idx, sec = _panel()
    out = mc.add_context(df, {}, sec)
    assert out[list(mc.COLUMNS)].isna().all().all()


@pytest.fixture()
def dbdir(tmp_path, monkeypatch):
    """Scratch-TradingDB: 25 Mitglieder in ^X, Simulationsjahr 2024."""
    import sqlite3
    monkeypatch.setenv('TradingDB', str(tmp_path))
    mc._CACHE.clear()
    con = sqlite3.connect(tmp_path / 'yf_tickers.db')
    con.executescript("CREATE TABLE stocks (id INTEGER, Ticker TEXT);"
                      "CREATE TABLE indices (id INTEGER, name TEXT);"
                      "CREATE TABLE stock_indices (stock_id INTEGER, index_id INTEGER);"
                      "INSERT INTO indices VALUES (1, '^X'), (2, '^EU');")
    for i in range(25):
        con.execute("INSERT INTO stocks VALUES (?, ?)", (i, f'T{i:02d}'))
        con.execute("INSERT INTO stock_indices VALUES (?, 1)", (i,))
        con.execute("INSERT INTO stock_indices VALUES (?, 2)", (i,))
    con.commit(); con.close()
    dates = pd.bdate_range('2024-01-02', periods=30)
    rows = []
    for i in range(25):
        for k, d in enumerate(dates):
            above = i < 5 if k < 25 else i < 20     # Breite springt von 20 % auf 80 %
            rows.append((f'T{i:02d}', d.strftime('%Y-%m-%d 00:00:00'), 10.0,
                         9.0 if above else 11.0, 9.0 if above else 11.0))
    con = sqlite3.connect(tmp_path / 'asset_simulation_2024.db')
    con.execute("CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, close REAL, sma50 REAL, sma200 REAL)")
    con.executemany("INSERT INTO asset_simulation VALUES (?,?,?,?,?)", rows)
    con.commit(); con.close()
    yield tmp_path, dates
    mc._CACHE.clear()


def test_aufbau_und_zuspielen(dbdir, monkeypatch):
    _, dates = dbdir
    monkeypatch.setattr('tradinglib.score_eval.available_years', lambda *a, **k: [2024])
    n = mc.build()
    assert n == 2 * len(dates)                       # zwei Indizes x 30 Tage
    df = pd.DataFrame({'ticker': ['T00', 'T00', '^X', 'NOPE'],
                       'Date': [dates[0], dates[-1], dates[-1], dates[-1]]})
    out = mc.attach_breadth(df)
    assert out['mkt_breadth50'].tolist()[:3] == [20.0, 80.0, 80.0]
    assert np.isnan(out['mkt_breadth50'].iloc[3])
    assert out['mkt_breadth_chg'].iloc[1] == pytest.approx(60.0)


def test_intraday_sieht_den_vortag(dbdir, monkeypatch):
    _, dates = dbdir
    monkeypatch.setattr('tradinglib.score_eval.available_years', lambda *a, **k: [2024])
    mc.build()
    day = dates[25]                                  # erster Tag mit 80 %
    df = pd.DataFrame({'close': [1.0]}, index=[day + pd.Timedelta(hours=10)])
    out = mc.attach_breadth(df, symbol='T00')
    assert out['mkt_breadth50'].iloc[0] == 20.0      # Tageswert erst nach Schluss bekannt


def test_formel_bekommt_breite_automatisch(dbdir, monkeypatch):
    from tradinglib import tools
    _, dates = dbdir
    monkeypatch.setattr('tradinglib.score_eval.available_years', lambda *a, **k: [2024])
    mc.build()
    df = pd.DataFrame({'ticker': 'T01', 'Date': [d.strftime('%Y-%m-%d 00:00:00') for d in dates],
                       'close': 10.0})
    m = tools.compute_signal_mask(df, 'mkt_breadth50 >= 50')
    assert m.tolist() == [False] * 25 + [True] * 5
    assert 'mkt_breadth50' not in df.columns         # Eingabe bleibt unveraendert


def test_ohne_breite_in_der_formel_wird_nichts_geladen(monkeypatch):
    from tradinglib import tools
    called = []
    monkeypatch.setattr(mc, 'attach_context', lambda *a, **k: called.append(1))
    df = pd.DataFrame({'ticker': ['A'], 'Date': ['2024-01-02'], 'close': [1.0]})
    tools.compute_signal_mask(df, 'close > 0')
    assert not called
    assert mc.references_breadth('x', '(mkt_breadth200 > 60)')
    assert not mc.references_breadth('close > 1', None)


def _fg_db(tmp_path, dates):
    import sqlite3
    con = sqlite3.connect(tmp_path / 'fear_greed.db')
    con.execute('CREATE TABLE fg_history (date TEXT, "index" TEXT, score REAL)')
    con.executemany('INSERT INTO fg_history VALUES (?,?,?)',
                    [(d.strftime('%Y-%m-%d'), '^X', 30.0 if k < 10 else 70.0)
                     for k, d in enumerate(dates)])
    con.commit(); con.close()


def test_fg_score_ueber_den_bewerteten_index(dbdir):
    tmp, dates = dbdir
    _fg_db(tmp, dates)
    df = pd.DataFrame({'ticker': ['T03', 'T03', '^X', 'NOPE'],
                       'Date': [dates[0], dates[20], dates[20], dates[0]]})
    out = mc.attach_fg(df)
    assert out['fg_score'].tolist()[:3] == [30.0, 70.0, 70.0]
    assert np.isnan(out['fg_score'].iloc[3])


def test_fg_score_in_formel_und_nur_bei_bedarf(dbdir, monkeypatch):
    from tradinglib import tools
    tmp, dates = dbdir
    _fg_db(tmp, dates)
    df = pd.DataFrame({'ticker': 'T03', 'Date': [d.strftime('%Y-%m-%d 00:00:00') for d in dates],
                       'close': 10.0})
    m = tools.compute_signal_mask(df, 'fg_score < 45')
    assert m.tolist() == [True] * 10 + [False] * 20
    # Nur fg_score referenziert -> keine Breite geladen
    monkeypatch.setattr(mc, 'attach_breadth', lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    tools.compute_signal_mask(df, 'fg_score < 45')


def test_fehlender_tageswert_nimmt_den_letzten(dbdir):
    """Der Log-Job laeuft 22:45, der Notifier schon 16:00/22:00 -- der juengste
    Tag hat noch keinen Wert und darf trotzdem nicht leer bleiben."""
    tmp, dates = dbdir
    _fg_db(tmp, dates)
    later = dates[-1] + pd.Timedelta(days=1)
    df = pd.DataFrame({'ticker': ['T03', 'T03'], 'Date': [later, later + pd.Timedelta(days=10)]})
    out = mc.attach_fg(df)
    assert out['fg_score'].iloc[0] == 70.0
    assert np.isnan(out['fg_score'].iloc[1])        # nicht beliebig weit zurueck


def test_strategy_finder_pfad_mit_indikator_nachladen(dbdir, monkeypatch):
    """Der Strategy Finder ruft vorher populate_indicators_for_df auf. Das legte
    fuer fg_score/mkt_breadth50 leere Spalten an (unbekannter Indikator), die den
    echten Join blockierten -> 0 Signale auf ^GDAXI."""
    from tradinglib import tools
    tmp, dates = dbdir
    _fg_db(tmp, dates)
    monkeypatch.setattr('tradinglib.score_eval.available_years', lambda *a, **k: [2024])
    mc.build()
    df = pd.DataFrame({'ticker': 'T03', 'Date': [d.strftime('%Y-%m-%d 00:00:00') for d in dates],
                       'close': 10.0})
    buy = '(fg_score < 45)\n(mkt_breadth50 < 50)'
    out = tools.Tools().populate_indicators_for_df(df, [buy, ''])
    assert out['fg_score'].notna().all() and out['mkt_breadth50'].notna().all()


def test_leere_platzhalterspalte_wird_ersetzt(dbdir):
    tmp, dates = dbdir
    _fg_db(tmp, dates)
    df = pd.DataFrame({'ticker': ['T03'], 'Date': [dates[0]], 'fg_score': [np.nan]})
    assert mc.attach_fg(df)['fg_score'].iloc[0] == 30.0


def test_intraday_fg_vom_vortag(dbdir):
    tmp, dates = dbdir
    _fg_db(tmp, dates)
    df = pd.DataFrame({'close': [1.0]}, index=[dates[10] + pd.Timedelta(hours=11)])
    assert mc.attach_fg(df, symbol='T03')['fg_score'].iloc[0] == 30.0


def test_zu_kleiner_index_liefert_keine_breite():
    df, idx, sec = _panel(n_tickers=10)
    out = mc.add_context(df, idx, sec)
    assert out['mkt_breadth200'].isna().all()
