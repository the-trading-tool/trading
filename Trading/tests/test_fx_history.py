"""Historical exchange rates: local FX series (fx_history) and the day-by-day conversion."""
import sqlite3

import pandas as pd
import pytest

from tradinglib import fx_history
from tradinglib import ms_export
from tradinglib import strategy_spec as ss


def _fx_db(folder, ticker, rows):
    con = sqlite3.connect(folder / f'yf_{ticker}.db')
    con.execute('CREATE TABLE day_data (Date TEXT, Close REAL)')
    con.executemany('INSERT INTO day_data VALUES (?, ?)',
                    [(f'{d} 00:00:00', v) for d, v in rows])
    con.commit()
    con.close()


@pytest.fixture()
def fxdir(tmp_path):
    fx_history._CACHE.clear()
    _fx_db(tmp_path, 'EURUSD=X', [('2024-01-02', 1.10), ('2024-01-03', 1.00), ('2024-01-05', 1.25)])
    _fx_db(tmp_path, 'GBPUSD=X', [('2024-01-02', 1.32), ('2024-01-03', 1.20), ('2024-01-05', 1.50)])
    _fx_db(tmp_path, 'JPYEUR=X', [('2024-01-02', 0.0062), ('2024-01-03', 0.0060)])
    yield str(tmp_path)
    fx_history._CACHE.clear()


def test_per_unit_usd_gbp_minor_units_and_crosses(fxdir):
    usd = fx_history.per_unit('USD', 'EUR', fxdir)
    assert usd.round(6).tolist() == [round(1 / 1.10, 6), 1.0, 0.8]
    gbp = fx_history.per_unit('GBP', 'EUR', fxdir)
    assert gbp.round(6).tolist() == [1.2, 1.2, 1.2]                  # 1.32/1.10, 1.20/1.00, 1.50/1.25
    gbx = fx_history.per_unit('GBp', 'EUR', fxdir)
    assert gbx.round(6).tolist() == [0.012, 0.012, 0.012]            # pence = GBP / 100
    jpy = fx_history.per_unit('JPY', 'EUR', fxdir)
    assert jpy.tolist() == [0.0062, 0.0060]
    # System currency USD: EUR per JPY / EUR per USD; the last JPY rate carries forward.
    jpy_usd = fx_history.per_unit('JPY', 'USD', fxdir)
    assert jpy_usd.round(6).tolist() == [round(0.0062 * 1.10, 6), round(0.0060 * 1.00, 6),
                                         round(0.0060 * 1.25, 6)]


def test_per_unit_none_without_series_or_for_the_same_currency(fxdir):
    assert fx_history.per_unit('DKK', 'EUR', fxdir) is None
    assert fx_history.per_unit('EUR', 'EUR', fxdir) is None
    assert fx_history.per_unit('0', 'EUR', fxdir) is None


def test_historical_conversion_uses_each_days_rate(fxdir):
    df = pd.DataFrame({
        'ticker': ['A', 'A', 'A', 'A', 'B', 'C'],
        'Date': ['2024-01-01', '2024-01-02', '2024-01-04', '2024-01-08', '2024-01-03', '2024-01-03'],
        'currency': ['USD', 'USD', 'USD', 'USD', 'EUR', 'DKK'],
        'close': [100.0, 100.0, 100.0, 100.0, 50.0, 70.0],
        'sup_support': [90.0] * 6})
    out = ss.to_system_currency(df, 'EUR', lambda c: {'DKK': 7.0}[c],
                                series_fn=lambda c: fx_history.per_unit(c, 'EUR', fxdir))
    # before the series: first rate; on a day without one: last known; after: last.
    assert out['close'].round(4).tolist() == [90.9091, 90.9091, 100.0, 80.0, 50.0, 10.0]
    assert out['sup_support'].tolist() == [90.0] * 6
    assert out.attrs['fx_fallback'] == ['DKK']
    assert (out['currency'] == 'EUR').all()


def test_spec_fx_mode_signature_and_multi_entry():
    cur = ss.normalize({'entry': {'buy': 'a'}, 'exit': {'sell': 'b'}})
    hist = ss.normalize({'entry': {'buy': 'a'}, 'exit': {'sell': 'b'}, 'universe': {'fx': 'historical'}})
    assert cur['universe']['fx'] == 'current'
    assert ss.normalize({'universe': {'fx': 'x'}})['universe']['fx'] == 'current'
    assert '"fx"' not in ss.signature(cur) and ss.signature(cur) != ss.signature(hist)
    assert 'fx' not in ss.to_ms_entry(cur)                      # missing = today's rate in Multi
    assert ss.to_ms_entry(hist)['fx'] == 'historical'
    assert ss.from_ms_entry('S', '^SPX', ss.to_ms_entry(hist))['universe']['fx'] == 'historical'
    assert ss.from_ms_entry('S', '^SPX', ss.to_ms_entry(cur))['universe']['fx'] == 'current'
    e = ms_export.entry(buy='a', sell='b', num_assets=1, invest=1, order_by='sortino', fx='historical')
    assert e['fx'] == 'historical'


def test_reference_entry_overrides_fx():
    saved = {'S': ss.normalize({'entry': {'buy': 'a'}, 'exit': {'sell': 'b'}})}
    tx = {'T': {'^SPX': {'strategy': 'S', 'fx': 'historical'}}}
    assert ss.expand_references(tx, saved)['T']['^SPX']['_spec']['universe']['fx'] == 'historical'
