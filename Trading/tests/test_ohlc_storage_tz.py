"""Speicherkonvention der OHLC-Tabellen: tz-naiv in UTC.

save_ohlc_to_sql rendert den Zeitstempel per strftime, und strftime schreibt die
WANDUHRZEIT. Ein tz-behafteter Rahmen wird damit als Boersen-Ortszeit
gespeichert, und die Anzeige rechnet die Zeitzone ein zweites Mal drauf.

So sind rund 11 Millionen doppelte Stundenbalken entstanden: in
market_data.download machte ein ``import pandas as pd`` INNERHALB der Funktion
``pd`` fuer die ganze Funktion lokal, der fruehere Zugriff lief in einen
UnboundLocalError, und ein ``except Exception`` verschluckte ihn -- die
Normalisierung lief nie. Aufgefallen ist es erst, als yfinance anfing, Ortszeit
statt UTC zu liefern, und im Chart Balken hinter dem Sitzungsende erschienen.

Die Konvention wird deshalb an der Speichergrenze erzwungen, wo JEDER
Schreibpfad vorbeikommt -- nicht nur beim Aufrufer, der sie umgehen kann.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib.utils import DataUtils


def _conn():
    return sqlite3.connect(':memory:')


def _lies(conn, table='h60_data'):
    return [r[0] for r in conn.execute(f'SELECT Date FROM {table} ORDER BY Date')]


def _frame(index):
    return pd.DataFrame({'Open': [1.0] * len(index), 'High': [2.0] * len(index),
                         'Low': [0.5] * len(index), 'Close': [1.5] * len(index),
                         'Volume': [100] * len(index)}, index=index)


def test_tz_behafteter_rahmen_wird_nach_utc_umgerechnet():
    """Der gemeldete Fall: Berliner Ortszeit 09:00 muss als 07:00 UTC landen."""
    idx = pd.DatetimeIndex(['2026-07-10 09:00:00', '2026-07-10 10:00:00'],
                           tz='Europe/Berlin')
    conn = _conn()
    DataUtils.save_ohlc_to_sql(conn, 'h60_data', _frame(idx))
    assert _lies(conn) == ['2026-07-10 07:00:00', '2026-07-10 08:00:00']


def test_new_york_wird_ebenfalls_umgerechnet():
    """Negativer Versatz: 09:30 New York ist 13:30 UTC."""
    idx = pd.DatetimeIndex(['2026-07-10 09:30:00'], tz='America/New_York')
    conn = _conn()
    DataUtils.save_ohlc_to_sql(conn, 'h60_data', _frame(idx))
    assert _lies(conn) == ['2026-07-10 13:30:00']


def test_winterzeit_traegt_den_anderen_versatz():
    """Im Winter ist Berlin UTC+1, nicht UTC+2 -- der Versatz kommt aus der
    Zone je Datum, nicht aus einer Konstante."""
    idx = pd.DatetimeIndex(['2026-02-12 09:00:00'], tz='Europe/Berlin')
    conn = _conn()
    DataUtils.save_ohlc_to_sql(conn, 'h60_data', _frame(idx))
    assert _lies(conn) == ['2026-02-12 08:00:00']


def test_tz_naiver_rahmen_bleibt_unveraendert():
    """Der Regelfall darf sich nicht aendern."""
    idx = pd.DatetimeIndex(['2026-07-10 07:00:00', '2026-07-10 08:00:00'])
    conn = _conn()
    DataUtils.save_ohlc_to_sql(conn, 'h60_data', _frame(idx))
    assert _lies(conn) == ['2026-07-10 07:00:00', '2026-07-10 08:00:00']


def test_utc_behafteter_rahmen_verliert_nur_die_kennzeichnung():
    idx = pd.DatetimeIndex(['2026-07-10 07:00:00'], tz='UTC')
    conn = _conn()
    DataUtils.save_ohlc_to_sql(conn, 'h60_data', _frame(idx))
    assert _lies(conn) == ['2026-07-10 07:00:00']


def test_tagesdaten_bleiben_unberuehrt():
    """day_data traegt Datumsangaben ohne Uhrzeit -- nichts umzurechnen."""
    idx = pd.DatetimeIndex(['2026-07-10', '2026-07-13'])
    conn = _conn()
    DataUtils.save_ohlc_to_sql(conn, 'day_data', _frame(idx))
    assert _lies(conn, 'day_data') == ['2026-07-10 00:00:00', '2026-07-13 00:00:00']


def test_umrechnung_wird_gemeldet(caplog):
    """Stillschweigend zu reparieren waere fast so schlecht wie der Fehler --
    die Ursache beim Aufrufer soll auffallen."""
    idx = pd.DatetimeIndex(['2026-07-10 09:00:00'], tz='Europe/Berlin')
    with caplog.at_level('WARNING'):
        DataUtils.save_ohlc_to_sql(_conn(), 'h60_data', _frame(idx))
    assert any('tz-behaftet' in r.getMessage() for r in caplog.records)


def test_unlesbare_daten_brechen_den_schreibvorgang_nicht_ab():
    """Ein kaputter Zeitstempel darf den ganzen Abruf nicht verwerfen."""
    df = pd.DataFrame({'Date': ['kein datum'], 'Open': [1.0], 'High': [2.0],
                       'Low': [0.5], 'Close': [1.5], 'Volume': [1]})
    conn = _conn()
    DataUtils.save_ohlc_to_sql(conn, 'h60_data', df)
    assert _lies(conn) == ['kein datum']
