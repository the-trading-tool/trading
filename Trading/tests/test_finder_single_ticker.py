"""Strategy Finder single-ticker mode: lookup, loading and the strategy spec."""
import sqlite3

import pandas as pd
import pytest

from tradinglib import make_query as mq
from tradinglib import strategy_spec
from tradinglib.premium.asset_simulator import AssetSimulator


def _make_dbs(tmp_path):
    t = sqlite3.connect(tmp_path / 'yf_tickers.db')
    t.executescript("""
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, Ticker TEXT, ISIN TEXT);
        CREATE TABLE indices (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE stock_indices (stock_id INTEGER, index_id INTEGER);
        INSERT INTO stocks VALUES (1, '1U1.DE', 'DE0005545503'), (2, 'SAP.DE', 'DE0007164600'),
                                  (3, 'BABA', NULL);
        INSERT INTO indices VALUES (1, '^SDAXI'), (2, '^GDAXI'), (3, '^MDAXI');
        INSERT INTO stock_indices VALUES (1, 1), (1, 3), (2, 2);
    """)
    t.commit()
    t.close()
    s = sqlite3.connect(tmp_path / 'asset_simulation_.db')
    s.execute('CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, close REAL, sortino REAL)')
    rows = [(tk, f'2026-01-0{d}', 10.0 + d, 1.0) for tk in ('1U1.DE', 'SAP.DE', 'BABA')
            for d in range(1, 4)]
    s.executemany('INSERT INTO asset_simulation VALUES (?,?,?,?)', rows)
    s.commit()
    s.close()
    i = sqlite3.connect(tmp_path / 'asset_info.db')
    i.execute("""CREATE TABLE asset_info (ticker TEXT, sector TEXT, shortName TEXT, longName TEXT,
        exchange TEXT, industry TEXT, longBusinessSummary TEXT, beta REAL, lastDividendValue REAL,
        recommendationKey TEXT, targetHighPrice REAL, targetMeanPrice REAL, targetLowPrice REAL,
        ebitdaMargins REAL, revenueGrowth REAL, marketCap REAL, totalDebt REAL, totalRevenue REAL,
        enterpriseValue REAL, currency TEXT)""")
    for tk in ('1U1.DE', 'SAP.DE', 'BABA'):
        i.execute('INSERT INTO asset_info (ticker, longName, exchange, currency) VALUES (?,?,?,?)',
                  (tk, tk, 'GER', 'EUR'))
    i.commit()
    i.close()


@pytest.fixture
def sim(tmp_path):
    _make_dbs(tmp_path)
    s = AssetSimulator.__new__(AssetSimulator)
    s.db_path = str(tmp_path)
    s.ticker_db, s.performance_db, s.info_db = 'yf_tickers.db', 'asset_simulation_.db', 'asset_info.db'
    s.index_column = '^SDAXI'
    s.attach_dbs()
    yield s
    s.ticker_conn.close()


@pytest.fixture(autouse=True)
def _abs_paths(monkeypatch):
    # get_path(path=<absolute dir>) must not be redirected by the TradingDB env var.
    from tradinglib import tools
    monkeypatch.setattr(tools.Tools, 'get_path',
                        lambda self, path='database', file_name='', ignore_env=False:
                        f'{path}/{file_name}')


def test_resolve_is_case_insensitive_and_lists_indices(sim):
    assert sim.resolve_single_ticker('1u1.de') == ('1U1.DE', ['^MDAXI', '^SDAXI'])
    assert sim.resolve_single_ticker('  ') == ('', [])
    assert sim.resolve_single_ticker('NOPE.DE') == (None, [])


def test_ticker_without_index_is_found(sim):
    assert sim.resolve_single_ticker('baba') == ('BABA', [])
    df = sim.fetch_single_ticker('BABA')
    assert df['ticker'].unique().tolist() == ['BABA'] and len(df) == 3


def test_fetch_loads_only_the_ticker_once(sim):
    df = sim.fetch_single_ticker('1U1.DE')
    # member of two indices, still one row per day
    assert df['ticker'].unique().tolist() == ['1U1.DE']
    assert sorted(df['Date']) == ['2026-01-01', '2026-01-02', '2026-01-03']
    assert df['index_name'].iloc[0] == '^MDAXI'


def test_query_takes_ticker_as_parameter(sim):
    q = mq.make_query('asset_simulation', q=8, conn=sim.ticker_conn)
    assert 'WHERE yt.Ticker = ?' in q
    df = pd.read_sql_query(q, sim.ticker_conn, params=('x" OR 1=1 --',))
    assert df.empty


def test_spec_ticker_default_keeps_old_signature():
    base = strategy_spec.normalize({'universe': {'index': '^GDAXI'}})
    assert base['universe']['ticker'] == ''
    assert '"ticker"' not in strategy_spec.signature(base)
    single = strategy_spec.normalize({'universe': {'index': '^GDAXI', 'ticker': ' 1U1.DE '}})
    assert single['universe']['ticker'] == '1U1.DE'
    assert strategy_spec.signature(single) != strategy_spec.signature(base)


def test_single_ticker_spec_is_not_exported_to_multi():
    assert strategy_spec.ms_exportable({'universe': {'index': '^GDAXI'}})
    assert not strategy_spec.ms_exportable({'universe': {'index': '^GDAXI', 'ticker': 'SAP.DE'}})


def test_describe_names_the_ticker_not_the_filters():
    lines = strategy_spec.describe({'universe': {'index': '^SDAXI', 'ticker': '1U1.DE',
                                                 'sector': 'Technology', 'years': ['2026']}})
    assert '1U1.DE' in lines[0] and 'Technology' not in lines[0]
