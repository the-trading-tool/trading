"""market_data.download muss tz-naiv in UTC liefern (2026-08).

Gemeldet an ^GDAXI: die Stundenkerzen sassen im Chart zwei Stunden zu spaet
(Handel scheinbar 11:00-19:00 statt 09:00-17:30), und um den 6. August klafften
Luecken.

Zwei Dinge trafen zusammen. ``save_ohlc_to_sql`` schreibt den Zeitstempel per
strftime und verliert dabei jede tz-Angabe -- was ankommt, wird als Wanduhrzeit
gespeichert. Und die Normalisierung in ``download`` lief nie: die Funktion hatte
ein ``import pandas as pd`` im except-Zweig, wodurch ``pd`` fuer die GESAMTE
Funktion lokal wurde. Der Zugriff weiter oben lief damit in einen
UnboundLocalError, den ein ``except Exception`` verschluckte -- mitsamt der
Normalisierung.

Aufgefallen ist das jahrelang nicht, weil yfinance bis 1.2.0 aus ``download()``
UTC zurueckgab: die uebersprungene Umrechnung war ein Nullvorgang. Ab 1.5.2
liefert dieselbe Funktion Boersen-Ortszeit, und die Anzeige rechnete die
Zeitzone ein zweites Mal drauf.

Run: .venv/Scripts/python.exe -m pytest tests/ -q
"""
import pandas as pd
import pytest

from tradinglib import market_data as md


def _aware(tz, hours):
    idx = pd.to_datetime([f'2026-08-11 {h:02d}:00' for h in hours]).tz_localize(tz)
    cols = pd.MultiIndex.from_product(
        [['Adj Close', 'Close', 'High', 'Low', 'Open', 'Volume'], ['X']])
    return pd.DataFrame([[1.0] * 6 for _ in hours], index=idx, columns=cols)


class _Provider:
    def __init__(self, df):
        self.df = df

    def download(self, **kw):
        return self.df


@pytest.fixture
def provider(monkeypatch):
    def use(df):
        import tradinglib.providers as P
        monkeypatch.setattr(P, 'get_provider', lambda *a, **kw: _Provider(df))
    return use


@pytest.mark.parametrize('tz, hour, expected', [
    ('Europe/Berlin', 9, 7),        # Xetra-Eroeffnung -> 07:00 UTC
    ('America/New_York', 9, 13),    # NYSE-Eroeffnung  -> 13:00 UTC
    ('Asia/Tokyo', 9, 0),
])
def test_exchange_local_is_converted_to_utc(provider, tz, hour, expected):
    provider(_aware(tz, [hour]))
    out = md.download(tickers='X', period='5d', interval='60m', force_remote=True)
    assert out.index.tz is None, 'Ergebnis muss tz-naiv sein'
    assert out.index[0].hour == expected


def test_naive_index_is_left_alone(provider):
    """Tages-/Wochenkerzen kommen tz-naiv -- ein Umrechnen wuerde das Datum
    verschieben (Mitternacht lokal -> 22:00 des Vortags in UTC)."""
    idx = pd.to_datetime(['2026-08-11 00:00', '2026-08-12 00:00'])
    cols = pd.MultiIndex.from_product([['Close'], ['X']])
    provider(pd.DataFrame([[1.0], [2.0]], index=idx, columns=cols))
    out = md.download(tickers='X', period='1mo', interval='1d', force_remote=True)
    assert [str(v)[:10] for v in out.index] == ['2026-08-11', '2026-08-12']


def test_download_has_no_function_local_pandas_import():
    """Der eigentliche Fehler -- und er ist unsichtbar, solange die Quelle
    ohnehin UTC liefert. Deshalb hier direkt am Quelltext festgehalten."""
    import inspect
    src = inspect.getsource(md.download)
    assert 'import pandas' not in src, (
        'ein funktionslokaler pandas-Import macht pd fuer die ganze Funktion '
        'lokal und laesst jeden frueheren Zugriff in einen UnboundLocalError '
        'laufen')


def test_post_processing_failure_is_logged_not_swallowed(provider, caplog):
    """Der stille except hat den Fehler jahrelang verdeckt."""
    class Broken:
        def download(self, **kw):
            class D:
                @property
                def columns(self):
                    raise RuntimeError('boom')
            return D()

    import tradinglib.providers as P
    import logging
    P.get_provider = lambda *a, **kw: Broken()
    with caplog.at_level(logging.WARNING, logger='tradinglib.market_data'):
        md.download(tickers='X', period='5d', interval='60m', force_remote=True)
    assert any('Nachbearbeitung' in r.message or 'download failed' in r.message
               for r in caplog.records)
