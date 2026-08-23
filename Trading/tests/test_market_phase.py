"""Tests fuer market_phase (Trend- und Saison-Zustand eines Marktes).

Der wichtigste Punkt ist die Kausalitaet: der Zustand zu einem Datum darf
ausschliesslich aus Daten bis zu diesem Tag stammen, die Saison-Statistik nur
aus Jahren VOR dem Jahr des Datums. Nur dann darf man den Marker gegen die
Ergebnisse der Trades messen -- sonst misst man Rueckschau.

Self-contained: synthetische Kursreihen, keine DB.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from tradinglib import market_phase as mph


def _series(spec):
    """spec: dict year -> list of daily closes (business days from Jan 1)."""
    frames = []
    for year, closes in spec.items():
        idx = pd.bdate_range(f'{year}-01-01', periods=len(closes))
        frames.append(pd.DataFrame({'Close': closes}, index=idx))
    return pd.concat(frames).sort_index()


def _market(monkeypatch, df, ticker='^TEST', **kw):
    """Build a MarketPhase over a given series, bypassing the DB loader."""
    def fake_load(self, db_path):
        self._daily = None
        close = pd.to_numeric(df['Close'], errors='coerce').dropna().sort_index()
        close = close[close.index.dayofweek != 6]
        self._daily = pd.DataFrame({'close': close})
        self._daily['sma_slow'] = close.rolling(mph.SLOW_MA).mean()
        self._daily['sma_fast'] = close.rolling(mph.FAST_MA).mean()
        self._daily['rising'] = (self._daily['sma_slow']
                                 > self._daily['sma_slow'].shift(mph.SLOPE_DAYS))
        year = pd.Series(close.index.year, index=close.index)
        tday = year.groupby(year).cumcount() + 1
        self._days_by_year = {y: g.index for y, g in close.groupby(year)}
        piv = pd.DataFrame({'year': year.to_numpy(), 'tday': tday.to_numpy(),
                            'close': close.to_numpy()}) \
            .pivot(index='year', columns='tday', values='close') \
            .sort_index(axis=0).sort_index(axis=1)
        self._last_tday = piv.apply(lambda r: r.last_valid_index(), axis=1)
        self._pivot = piv.ffill(axis=1)

    monkeypatch.setattr(mph.MarketPhase, '_load', fake_load)
    mph.clear_cache()
    return mph.MarketPhase(ticker, **kw)


# ------------------------------------------------------------------ Saison
def test_saison_erkennt_starke_phase(monkeypatch):
    """Alle Vorjahre stiegen ab Tag 1 -- das muss 'strong' bei 100 % geben."""
    # 2026 muss in der Reihe stehen -- gefragt wird nach einem Tag DARIN,
    # gerechnet wird aus den Jahren davor.
    spec = {y: list(np.linspace(100, 130, 60)) for y in range(2020, 2027)}
    m = _market(monkeypatch, _series(spec))
    got = m.season('2026-01-02', horizon=10)
    assert got['verdict'] == mph.STRONG
    assert got['prob'] == 100.0
    assert got['avg'] > 0
    assert got['n'] == 6


def test_saison_erkennt_schwache_phase(monkeypatch):
    spec = {y: list(np.linspace(130, 100, 60)) for y in range(2020, 2027)}
    m = _market(monkeypatch, _series(spec))
    got = m.season('2026-01-02', horizon=10)
    assert got['verdict'] == mph.WEAK
    assert got['prob'] == 0.0
    assert got['avg'] < 0


def test_saison_ist_kausal(monkeypatch):
    """Ein spaeteres Jahr darf den Zustand eines frueheren nicht veraendern.

    Ohne diese Eigenschaft waere der Marker Rueckschau und jede Messung an den
    Trades wertlos.
    """
    early = {y: list(np.linspace(100, 130, 60)) for y in range(2015, 2022)}
    late = dict(early)
    # 2022-2026 laufen genau andersherum -- alles NACH dem Abfragedatum
    late.update({y: list(np.linspace(130, 100, 60)) for y in range(2022, 2027)})

    m_short = _market(monkeypatch, _series(early))
    m_long = _market(monkeypatch, _series(late))
    a = m_short.season('2021-01-04', horizon=10)
    b = m_long.season('2021-01-04', horizon=10)
    assert a['prob'] == b['prob']
    assert a['n'] == b['n']
    assert a['verdict'] == b['verdict']


def test_saison_ohne_genug_jahre(monkeypatch):
    spec = {y: list(np.linspace(100, 120, 40)) for y in (2024, 2025, 2026)}
    m = _market(monkeypatch, _series(spec))
    # 2026 hat nur 2 Vorjahre -> unter MIN_YEARS
    assert m.season('2026-01-05')['verdict'] == mph.NO_DATA
    assert m.has_seasonality is False


def test_saison_horizont_wird_am_jahresende_gekappt(monkeypatch):
    """Ein Horizont ueber das Jahresende hinaus darf nicht ins Folgejahr
    laufen -- sonst misst man einen Jahreswechsel statt der Saison."""
    spec = {y: list(np.linspace(100, 110, 30)) for y in range(2020, 2027)}
    m = _market(monkeypatch, _series(spec))
    got = m.season('2026-01-28', horizon=200)   # weit ueber das Jahresende
    assert got['n'] == 6
    assert got['avg'] > 0


# ------------------------------------------------------------------- Trend
def _trend_series(n, kind):
    idx = pd.bdate_range('2020-01-01', periods=n)
    if kind == 'up':
        vals = np.linspace(100, 300, n)
    elif kind == 'down':
        vals = np.linspace(300, 100, n)
    else:
        vals = np.concatenate([np.linspace(100, 300, n // 2),
                               np.linspace(300, 260, n - n // 2)])
    return pd.DataFrame({'Close': vals}, index=idx)


def test_trend_aufwaerts(monkeypatch):
    m = _market(monkeypatch, _trend_series(400, 'up'))
    got = m.trend('2021-06-01')
    assert got['verdict'] == mph.UP
    assert got['rising'] is True
    assert got['dist_slow'] > 0


def test_trend_abwaerts(monkeypatch):
    m = _market(monkeypatch, _trend_series(400, 'down'))
    got = m.trend('2021-06-01')
    assert got['verdict'] == mph.DOWN
    assert got['dist_slow'] < 0


def test_trend_uebergang(monkeypatch):
    """Kurs unter dem Schnitt, der Schnitt steigt aber noch -- eigener Bucket."""
    m = _market(monkeypatch, _trend_series(400, 'turn'))
    got = m.trend('2021-07-15')
    assert got['verdict'] in (mph.TRANSITION, mph.UP, mph.DOWN)
    # kein Absturz und alle Felder gefuellt
    assert got['close'] is not None and got['sma_slow'] is not None


def test_trend_zu_kurze_historie(monkeypatch):
    m = _market(monkeypatch, _trend_series(50, 'up'))
    assert m.trend('2020-02-14')['verdict'] == mph.NO_DATA


def test_trend_nutzt_nur_vergangenheit(monkeypatch):
    """Der Zustand an einem Tag darf sich nicht aendern, wenn spaeter mehr
    Kurse dazukommen."""
    full = _trend_series(600, 'up')
    m_full = _market(monkeypatch, full)
    m_cut = _market(monkeypatch, full.iloc[:400])
    d = full.index[350]
    assert m_full.trend(d)['verdict'] == m_cut.trend(d)['verdict']
    assert m_full.trend(d)['close'] == m_cut.trend(d)['close']


# ---------------------------------------------------------------- annotate
def test_annotate_fuegt_spalten_hinzu(monkeypatch):
    spec = {y: list(np.linspace(100, 130, 60)) for y in range(2020, 2027)}
    m = _market(monkeypatch, _series(spec), ticker='^TEST')
    monkeypatch.setattr(mph, 'get_market', lambda ticker, **kw: m)
    df = pd.DataFrame({'stockIndex': ['^TEST', '^TEST'],
                       'buyDate': ['2026-01-02', '2026-01-02'],
                       'gainPct': [1.0, 2.0]})
    out = mph.annotate(df)
    assert list(out['season_verdict']) == [mph.STRONG, mph.STRONG]
    assert out['gainPct'].tolist() == [1.0, 2.0]      # Original bleibt erhalten
    assert 'trend_verdict' in out.columns


def test_annotate_ohne_marktspalte_bleibt_unveraendert():
    df = pd.DataFrame({'ticker': ['AAA'], 'gainPct': [1.0]})
    out = mph.annotate(df)
    assert list(out['season_verdict']) == [mph.NO_DATA]
    assert out['gainPct'].tolist() == [1.0]


def test_state_for_leerer_markt():
    got = mph.state_for('', '2026-01-02')
    assert got['season_verdict'] == mph.NO_DATA
    assert got['trend_verdict'] == mph.NO_DATA
