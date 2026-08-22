"""Tests fuer die Lueckenerkennung von /fill (asset_perf2.collect_missing_dates).

Der Kern ist der Vergleich der Datums-MENGEN, nicht der Zeilenzahlen: nur so
faellt ein Loch mitten in einer sonst vollstaendigen Reihe auf. Genau das kam
in der Praxis vor (2026-06-08 fehlte bei 1430 Tickern, waehrend dieselben
Ticker anderswo Zeilen ohne Kurstag hatten).

Self-contained: temporaere SQLite-Dateien, keine Produktionsdaten.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture()
def dbdir(tmp_path, monkeypatch):
    """Ein leeres Datenbank-Verzeichnis, auf das Tools.get_path zeigt."""
    monkeypatch.setenv('TradingDB', str(tmp_path))
    return tmp_path


def _make_sim(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE asset_simulation (ticker TEXT, Date TEXT)")
    conn.executemany("INSERT INTO asset_simulation VALUES (?,?)",
                     [(tk, f"{d} 00:00:00") for tk, d in rows])
    conn.commit()
    conn.close()


def _make_ohlc(path, days):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE day_data (Date TEXT, Open REAL, High REAL, "
                 "Low REAL, Close REAL, Volume REAL)")
    conn.executemany("INSERT INTO day_data VALUES (?,1,1,1,1,1)",
                     [(f"{d} 00:00:00",) for d in days])
    conn.commit()
    conn.close()


def _collect(dbdir, **kw):
    from asset_perf2 import collect_missing_dates
    return collect_missing_dates(
        str(dbdir / 'asset_simulation_.db'),
        kw.pop('tickers'),
        kw.pop('start', '2026-01-01 00:00:00'),
        kw.pop('end', '2026-12-31 23:59:59'), **kw)


def test_fehlender_anfang_wird_gefunden(dbdir):
    """Der Normalfall: ein Asset kam mitten im Jahr dazu."""
    _make_sim(dbdir / 'asset_simulation_.db',
              [('AAA', '2026-01-05'), ('AAA', '2026-01-06')])
    _make_ohlc(dbdir / 'yf_AAA.db',
               ['2026-01-02', '2026-01-05', '2026-01-06'])
    assert _collect(dbdir, tickers=['AAA']) == {'AAA': {'2026-01-02'}}


def test_loch_in_der_mitte_wird_gefunden(dbdir):
    """Ein ausgefallener Nachtlauf -- der Fall, den ein Zeilenzahl-Vergleich
    uebersieht, sobald anderswo eine Zeile zuviel steht."""
    _make_sim(dbdir / 'asset_simulation_.db',
              [('AAA', '2026-01-02'), ('AAA', '2026-01-06'),
               ('AAA', '2026-01-07'),
               ('AAA', '2026-01-08')])       # Zeile ohne Kurstag (Feiertag)
    _make_ohlc(dbdir / 'yf_AAA.db',
               ['2026-01-02', '2026-01-05', '2026-01-06', '2026-01-07'])
    # gleiche Zeilenzahl (4 vs 4), trotzdem fehlt der 05.
    assert _collect(dbdir, tickers=['AAA']) == {'AAA': {'2026-01-05'}}


def test_vollstaendiger_ticker_taucht_nicht_auf(dbdir):
    _make_sim(dbdir / 'asset_simulation_.db',
              [('AAA', '2026-01-02'), ('AAA', '2026-01-05')])
    _make_ohlc(dbdir / 'yf_AAA.db', ['2026-01-02', '2026-01-05'])
    assert _collect(dbdir, tickers=['AAA']) == {}


def test_ticker_ohne_kursdatei_wird_uebersprungen(dbdir):
    """Ohne lokale Kursreihe koennte nichts gerechnet werden -- der Ticker
    darf keinen Worker kosten."""
    _make_sim(dbdir / 'asset_simulation_.db', [])
    assert _collect(dbdir, tickers=['NOPE']) == {}


def test_ticker_ganz_ohne_simulation(dbdir):
    """Neu importiert: gar keine Zeile in der Sim -- das ganze Fenster fehlt."""
    _make_sim(dbdir / 'asset_simulation_.db', [('AAA', '2026-01-02')])
    _make_ohlc(dbdir / 'yf_BBB.db', ['2026-01-02', '2026-01-05'])
    assert _collect(dbdir, tickers=['BBB']) == {'BBB': {'2026-01-02', '2026-01-05'}}


def test_fenster_grenzt_ab(dbdir):
    """Tage ausserhalb von [start, end] zaehlen nicht als Luecke."""
    _make_sim(dbdir / 'asset_simulation_.db', [('AAA', '2026-02-02')])
    _make_ohlc(dbdir / 'yf_AAA.db',
               ['2025-12-30', '2026-02-02', '2026-03-02'])
    got = _collect(dbdir, tickers=['AAA'],
                   start='2026-01-01 00:00:00', end='2026-02-28 23:59:59')
    assert got == {}


def test_letzter_tag_des_fensters_faellt_nicht_raus(dbdir):
    """Das Fenster endet auf 23:59:59 -- sonst schnitte der Vergleich den
    heutigen Tag ab ('2026-08-22 00:00:00' > '2026-08-22')."""
    _make_sim(dbdir / 'asset_simulation_.db', [])
    _make_ohlc(dbdir / 'yf_AAA.db', ['2026-08-22'])
    got = _collect(dbdir, tickers=['AAA'],
                   start='2026-01-01 00:00:00', end='2026-08-22 23:59:59')
    assert got == {'AAA': {'2026-08-22'}}


def test_fehlende_sim_db_ist_kein_absturz(dbdir):
    from asset_perf2 import collect_missing_dates
    assert collect_missing_dates(str(dbdir / 'gibtsnicht.db'), ['AAA'],
                                 '2026-01-01 00:00:00', '2026-12-31 23:59:59') == {}


def test_cli_flags(dbdir):
    from tradinglib import cli
    assert cli.parse_args(['x', '/fill'])['fill'] is True
    assert cli.parse_args(['x', '/fill', '/dry'])['dry'] is True
    assert cli.parse_args(['x', '/init'])['fill'] is False
    assert cli.parse_args(['x', '/fill', '/year:2024'])['year'] == 2024
