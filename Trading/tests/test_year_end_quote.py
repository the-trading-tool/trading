"""Tests fuer year_end_quote - der Bewertungsstichtag offener Positionen.

Bei einem Backtest ueber 2022 stand als Verkaufsdatum der 01.09.2022: der Code
nahm Tag und Monat von HEUTE und stellte das gewaehlte Jahr davor. Fuer eine im
Dezember gekaufte Position lag dieser "Verkauf" damit vor dem Kauf.

Geprueft wird gegen echte Tagesreihen aus einem Scratch-Verzeichnis, nicht gegen
die Produktionsdaten.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib.premium.multi_transaction import year_end_quote


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Eine Kursreihe ueber zwei Jahre, letzter Handelstag jeweils der 30.12.

    TradingDB muss gesetzt werden: Db_tools.get_path() gibt der Env-Variablen
    Vorrang vor dem uebergebenen db_path -- ohne das Setzen suchte der Test in
    den Produktionsdatenbanken und bekaeme den Silvester-Rueckfall.
    """
    monkeypatch.setenv('TradingDB', str(tmp_path))
    conn = sqlite3.connect(str(tmp_path / 'yf_TEST.db'))
    conn.execute('CREATE TABLE day_data (Date TEXT, Open REAL, High REAL, '
                 'Low REAL, Close REAL, Volume REAL)')
    rows = [('2021-12-29 00:00:00', 9.0), ('2021-12-30 00:00:00', 10.0),
            ('2022-06-15 00:00:00', 12.0),
            ('2022-12-29 00:00:00', 5.5), ('2022-12-30 00:00:00', 6.045)]
    conn.executemany('INSERT INTO day_data (Date, Close) VALUES (?, ?)', rows)
    conn.commit()
    conn.close()
    return tmp_path


def test_letzter_handelstag_des_jahres(db):
    close, date = year_end_quote('TEST', 2022, db_path=str(db))
    assert date == '2022-12-30 00:00:00'
    assert close == pytest.approx(6.045)


def test_jahr_wird_nicht_verwechselt(db):
    close, date = year_end_quote('TEST', 2021, db_path=str(db))
    assert date == '2021-12-30 00:00:00'
    assert close == pytest.approx(10.0)


def test_stichtag_liegt_nie_vor_einem_dezemberkauf(db):
    """Der eigentliche Fehler: Kauf 29.12., 'Verkauf' 01.09. desselben Jahres."""
    _, date = year_end_quote('TEST', 2022, db_path=str(db))
    assert date >= '2022-12-29'


def test_jahr_ohne_handelstage_faellt_auf_silvester(db):
    """Unscharf, aber garantiert nicht vor einem Kauf desselben Jahres."""
    close, date = year_end_quote('TEST', 2019, db_path=str(db))
    assert close is None
    assert date == '2019-12-31 00:00:00'


def test_unbekannter_ticker_stuerzt_nicht_ab(db):
    close, date = year_end_quote('GIBTESNICHT', 2022, db_path=str(db))
    assert close is None
    assert date == '2022-12-31 00:00:00'


def test_kaputte_datei_stuerzt_nicht_ab(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    (tmp_path / 'yf_MUELL.db').write_text('kein sqlite')
    close, date = year_end_quote('MUELL', 2022, db_path=str(tmp_path))
    assert close is None
    assert date == '2022-12-31 00:00:00'
