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
    def make(minute_df, calendar=('Europe/Berlin', False)):
        f = fd.FetchData.__new__(fd.FetchData)
        f.ftime_str = FT
        f.tz_info = 'Europe/Berlin'
        f.logger = logging.getLogger('test')
        f.load_price_data = lambda *a, **kw: minute_df
        f._exchange_calendar = lambda symbol: calendar
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


# Exchange-local day (2026-10): minute data arrives in display time
# (Europe/Berlin). A US post-market candle after midnight Berlin belongs to the
# US trading day before, not to the next calendar day -- on Fridays the old
# calendar-day binning wrote a Saturday row into asset_simulation.

US = ('America/New_York', False)


def _bars(stamps, close):
    return pd.DataFrame(
        {'Open': close, 'High': close, 'Low': close, 'Close': close,
         'Volume': 5}, index=stamps)


FRIDAY_DAYS = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15',
               '2026-10-16']


def test_us_post_market_after_midnight_berlin_stays_on_friday(maker):
    """Fri 2026-10-16 19:30 ET = Sat 01:30 Berlin: no Saturday row."""
    minute = _bars(['2026-10-16 21:59:00', '2026-10-17 01:30:00'], 62.67)
    df = _daily(FRIDAY_DAYS)
    out = maker(minute, US).add_current_price(df, symbol='EEM')

    days = pd.to_datetime(out.index)
    assert (days.dayofweek < 5).all(), 'no weekend candle'
    assert str(days[-1])[:10] == '2026-10-16'
    assert len(out) == len(df), 'replaces Friday, does not append'
    assert float(out['Close'].iloc[-1]) == 62.67


def test_us_post_market_after_midnight_berlin_on_a_weekday(maker):
    """Tue 19:30 ET = Wed 01:30 Berlin: belongs to Tuesday, not Wednesday."""
    minute = _bars(['2026-10-13 21:59:00', '2026-10-14 01:30:00'], 50.0)
    df = _daily(FRIDAY_DAYS[:2])
    out = maker(minute, US).add_current_price(df, symbol='EEM')

    assert str(pd.to_datetime(out.index)[-1])[:10] == '2026-10-13'
    assert len(out) == 2


def test_weekend_only_minutes_leave_the_series_alone(maker):
    """Futures reopen Sunday evening ET: no Sunday candle for a weekday market."""
    minute = _bars(['2026-10-19 00:05:00', '2026-10-19 00:06:00'], 70.0)  # Sun 18:05 ET
    df = _daily(FRIDAY_DAYS)
    out = maker(minute, US).add_current_price(df, symbol='CL=F')

    assert out.equals(df)


def test_crypto_keeps_its_weekend_candle(maker):
    minute = _bars(['2026-10-17 12:00:00', '2026-10-17 12:01:00'], 100.0)
    df = _daily(FRIDAY_DAYS)
    out = maker(minute, ('UTC', True)).add_current_price(df, symbol='BTC-USD')

    assert str(pd.to_datetime(out.index)[-1])[:10] == '2026-10-17'
    assert len(out) == len(df) + 1
