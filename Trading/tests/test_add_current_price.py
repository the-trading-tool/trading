"""add_current_price: veraltete Intraday-Daten duerfen die Tagesreihe nicht anfassen (2026-08).

Gemeldet an zwei US-Werten: der Chart endete einen Tag zu frueh und eine Linie
lief am rechten Rand auf einen frueheren Zeitpunkt zurueck.

Ursache war das Zusammenspiel zweier Dinge. Die Minutensammlung laeuft zu
europaeischen Handelszeiten, die US-Session liegt groesstenteils danach -- bei
JOE und BELFA standen die Minutendaten auf dem 07.08., die Tageskerzen auf dem
12.08. ``add_current_price`` haengte diese fuenf Tage alte Kerze trotzdem als
"aktuelle" an und entfernte dafuer pauschal die LETZTE Zeile. Der Treffer lag
aber mitten in der Reihe, also verschwand die neueste Kerze und eine veraltete
kam doppelt hinzu.

Bei europaeischen Werten fiel das nie auf: dort ist der Minutenstand frisch,
der getroffene Tag ist tatsaechlich der letzte, und das pauschale Entfernen war
zufaellig richtig.

Run: .venv/Scripts/python.exe -m pytest tests/ -q
"""
import logging

import pandas as pd
import pytest

from tradinglib import fetch_data as fd

FT = '%Y-%m-%d %H:%M:%S'


def _daily(days, closes=None):
    idx = [f'{d} 00:00:00' for d in days]
    closes = closes or list(range(10, 10 + len(days)))
    return pd.DataFrame(
        {'Open': closes, 'High': closes, 'Low': closes,
         'Close': closes, 'Volume': [100] * len(days)}, index=idx)


def _minute(day, close):
    idx = [f'{day} 15:30:00', f'{day} 15:31:00']
    return pd.DataFrame(
        {'Open': [close, close], 'High': [close, close], 'Low': [close, close],
         'Close': [close, close], 'Volume': [5, 5]}, index=idx)


@pytest.fixture
def maker(monkeypatch):
    """FetchData mit gestellten Intraday-Daten, ohne Netz und ohne DB."""
    def make(minute_df):
        f = fd.FetchData.__new__(fd.FetchData)
        f.ftime_str = FT
        f.logger = logging.getLogger('test')
        f.load_price_data = lambda *a, **kw: minute_df
        return f
    return make


DAILY_DAYS = ['2026-08-05', '2026-08-06', '2026-08-07', '2026-08-10',
              '2026-08-11', '2026-08-12']


def test_stale_intraday_leaves_the_series_alone(maker):
    """Der gemeldete Fall: Minutenstand 07.08., Tagesstand 12.08."""
    df = _daily(DAILY_DAYS)
    out = maker(_minute('2026-08-07', 99.0)).add_current_price(df, symbol='JOE')

    days = pd.to_datetime(out.index)
    assert str(days[-1])[:10] == '2026-08-12', 'neueste Kerze muss erhalten bleiben'
    assert not days.duplicated().any(), 'keine Dublette einfuegen'
    assert days.is_monotonic_increasing
    assert len(out) == len(df)
    assert float(out['Close'].iloc[-1]) == float(df['Close'].iloc[-1])


def test_same_day_intraday_replaces_that_day(maker):
    """Frischer Intraday-Stand (europaeischer Fall): letzte Kerze wird ersetzt."""
    df = _daily(DAILY_DAYS)
    out = maker(_minute('2026-08-12', 99.0)).add_current_price(df, symbol='SAP.DE')

    days = pd.to_datetime(out.index)
    assert str(days[-1])[:10] == '2026-08-12'
    assert not days.duplicated().any()
    assert len(out) == len(df), 'ersetzen, nicht anhaengen'
    assert float(out['Close'].iloc[-1]) == 99.0, 'laufender Kurs muss ankommen'


def test_new_day_intraday_is_appended(maker):
    """Handelstag laeuft, es gibt noch keine Tageskerze dafuer."""
    df = _daily(DAILY_DAYS)
    out = maker(_minute('2026-08-13', 99.0)).add_current_price(df, symbol='SAP.DE')

    days = pd.to_datetime(out.index)
    assert str(days[-1])[:10] == '2026-08-13'
    assert len(out) == len(df) + 1
    assert not days.duplicated().any()
    assert float(out['Close'].iloc[-1]) == 99.0


def test_matching_day_in_the_middle_keeps_the_newest_bar(maker):
    """Kern des Fehlers: der Treffer lag nicht am Ende der Reihe.

    Frueher entfernte die Funktion stur die letzte Zeile -- damit verschwand die
    neueste Kerze, obwohl der Konflikt einen frueheren Tag betraf.
    """
    df = _daily(DAILY_DAYS)
    before_last = float(df['Close'].iloc[-1])
    out = maker(_minute('2026-08-06', 99.0)).add_current_price(df, symbol='JOE')

    assert float(out['Close'].iloc[-1]) == before_last
    assert str(pd.to_datetime(out.index)[-1])[:10] == '2026-08-12'


def test_empty_inputs_are_harmless(maker):
    empty = pd.DataFrame()
    assert maker(_minute('2026-08-12', 1.0)).add_current_price(empty, symbol='JOE').empty
    df = _daily(DAILY_DAYS)
    # Kein Symbol -> unveraendert durchreichen
    out = maker(_minute('2026-08-12', 1.0)).add_current_price(df, symbol='')
    assert out.equals(df)
