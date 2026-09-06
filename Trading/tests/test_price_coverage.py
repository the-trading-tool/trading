"""Zeilengrenze vs. angeforderter Zeitraum in load_price_data.

OHLCQueryPlanner rechnet ein Jahr pauschal als 252 Handelstage. Werte, die auch
am Wochenende handeln (Krypto), haben 365 Zeilen pro Jahr — dieselbe Zeilenzahl
reicht dort nur gut 5,5 statt 8 Jahre zurueck. asset_perf2 forderte fuer
/year:2020 period='8y' an, bekam fuer BTC-EUR Daten erst ab 2021-03 und meldete
"no data", waehrend GC=F sauber durchlief.

_extend_if_truncated laedt in genau diesem Fall einmal nach. Die Tests halten
beide Richtungen fest: es muss greifen, wenn abgeschnitten wurde, und es darf
NICHT greifen, wenn der aelteste Tag nur wegen Feiertagen etwas spaeter liegt —
sonst kaeme fuer jede Aktie eine zweite Abfrage dazu.
"""
import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib.fetch_data import FetchData


def _bare():
    """FetchData ohne __init__ — geprueft wird reine Rechenlogik.

    Der Logger wird gesetzt, weil eine echte Instanz ihn immer hat
    (load_price_data schreibt schon vorher darauf); ihn im Produktivcode
    abzusichern wuerde nur eine Test-Eigenheit kaschieren.
    """
    ft = object.__new__(FetchData)
    ft.logger = logging.getLogger('test_price_coverage')
    return ft


def _frame(oldest_days_ago, rows):
    """Tagesreihe, die vor *oldest_days_ago* Tagen beginnt."""
    start = datetime.now().date() - timedelta(days=oldest_days_ago)
    dates = [(start + timedelta(days=i)).strftime('%Y-%m-%d %H:%M:%S')
             for i in range(rows)]
    return pd.DataFrame({'Date': dates, 'Close': [1.0] * rows})


class _Loader:
    """Zaehlt die Aufrufe und liefert beim zweiten Mal eine laengere Reihe."""

    def __init__(self, wide_days=2900, wide_rows=2900):
        self.calls = []
        self._wide = _frame(wide_days, wide_rows)

    def __call__(self, conn, price_tbl, limit):
        self.calls.append(limit)
        return self._wide


# ------------------------------------------------------- Zeitraum-Umrechnung

@pytest.mark.parametrize('period,erwartet', [
    ('8y', 8 * 365), ('2y', 730), ('6mo', 180), ('3wk', 21), ('30d', 30),
])
def test_zeitraum_in_kalendertagen(period, erwartet):
    assert FetchData._period_span_days(period) == erwartet


@pytest.mark.parametrize('period', ['max', '', None, 'abc', '1h'])
def test_unbestimmbarer_zeitraum_wird_nicht_geprueft(period):
    """Ohne ableitbaren Sollzeitraum darf es keine Nachlade-Entscheidung geben."""
    assert FetchData._period_span_days(period) is None


# ------------------------------------------------------------ Nachladen ja/nein

def test_krypto_luecke_loest_nachladen_aus():
    """8y = 2016 Zeilen reichen bei 365 Tagen/Jahr nur bis ~5,5 Jahre zurueck."""
    ft = _bare()
    kurz = _frame(int(5.5 * 365), 2016)          # aeltester Tag ~5,5 Jahre alt
    loader = _Loader()
    out = ft._extend_if_truncated(None, loader, kurz, symbol='BTC-EUR',
                                  price_tbl='day_data', period='8y', limit=2016)
    assert loader.calls == [2929], loader.calls    # int(2016*366/252)+1
    assert len(out) > len(kurz)


def test_feiertagsversatz_loest_nichts_aus():
    """Eine Aktie erreicht mit 2016 Zeilen ~8 Jahre; der aelteste Tag liegt nur
    wegen Feiertagen ein paar Wochen spaeter. Das ist kein Abschneiden."""
    ft = _bare()
    fast = _frame(8 * 365 - 50, 2016)
    loader = _Loader()
    out = ft._extend_if_truncated(None, loader, fast, symbol='GC=F',
                                  price_tbl='day_data', period='8y', limit=2016)
    assert loader.calls == []
    assert out is fast


def test_kurze_datei_wird_nicht_nachgeladen():
    """Weniger Zeilen als das LIMIT heisst: die Datei ist zu Ende, nicht
    abgeschnitten. Ein zweiter Versuch brauchte nur Zeit."""
    ft = _bare()
    kurz = _frame(400, 300)
    loader = _Loader()
    out = ft._extend_if_truncated(None, loader, kurz, symbol='NEU',
                                  price_tbl='day_data', period='8y', limit=2016)
    assert loader.calls == []
    assert out is kurz


def test_intraday_bleibt_unberuehrt():
    """Bei Intraday haengt die Zeilenzahl an Handelsstunden — andere Rechnung."""
    ft = _bare()
    kurz = _frame(int(5.5 * 365), 2016)
    loader = _Loader()
    out = ft._extend_if_truncated(None, loader, kurz, symbol='BTC-EUR',
                                  price_tbl='h60_data', period='8y', limit=2016)
    assert loader.calls == []
    assert out is kurz


def test_max_wird_uebersprungen():
    ft = _bare()
    kurz = _frame(int(5.5 * 365), 2016)
    loader = _Loader()
    out = ft._extend_if_truncated(None, loader, kurz, symbol='BTC-EUR',
                                  price_tbl='day_data', period='max', limit=2016)
    assert loader.calls == []


# -------------------------------------------------------------- Robustheit

def test_fehler_beim_nachladen_behaelt_das_erste_ergebnis():
    """Lieber die kurze Reihe als gar keine."""
    ft = _bare()
    kurz = _frame(int(5.5 * 365), 2016)

    def boom(conn, price_tbl, limit):
        raise RuntimeError('db weg')

    out = ft._extend_if_truncated(None, boom, kurz, symbol='BTC-EUR',
                                  price_tbl='day_data', period='8y', limit=2016)
    assert out is kurz


def test_kuerzeres_nachladeergebnis_wird_verworfen():
    ft = _bare()
    kurz = _frame(int(5.5 * 365), 2016)
    loader = _Loader(wide_days=100, wide_rows=100)
    out = ft._extend_if_truncated(None, loader, kurz, symbol='BTC-EUR',
                                  price_tbl='day_data', period='8y', limit=2016)
    assert out is kurz


def test_unlesbares_datum_bricht_nicht_ab():
    ft = _bare()
    kaputt = pd.DataFrame({'Date': ['kein datum'], 'Close': [1.0]})
    loader = _Loader()
    out = ft._extend_if_truncated(None, loader, kaputt, symbol='X',
                                  price_tbl='day_data', period='8y', limit=1)
    assert loader.calls == []
    assert out is kaputt
