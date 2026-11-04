"""Holdings for the portfolio market map (tradinglib.portfolio_map)."""
import json
import os
import sqlite3

import numpy as np
import pandas as pd
import pytest

from tradinglib import portfolio_map as pm


def test_by_ticker_sums_values_and_weights_gain():
    df = pm._frame([
        {'ticker': 'A', 'longName': 'Alpha', 'strategy': 'S1', 'shares': 10, 'positionValue': 300, 'gainPct': 10.0},
        {'ticker': 'A', 'longName': 'Alpha', 'strategy': 'S2', 'shares': 5, 'positionValue': 100, 'gainPct': -2.0},
        {'ticker': 'B', 'longName': 'Beta', 'strategy': '', 'shares': 1, 'positionValue': 50, 'gainPct': np.nan},
    ])
    out = pm.by_ticker(df).set_index('ticker')
    assert out.loc['A', 'positionValue'] == 400 and out.loc['A', 'shares'] == 15
    assert out.loc['A', 'gainPct'] == pytest.approx((300 * 10 - 100 * 2) / 400)
    assert out.loc['A', 'strategy'] == 'S1, S2'
    assert np.isnan(out.loc['B', 'gainPct'])                 # no price -> no gain, not 0


def test_multi_positions_reads_open_trades(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    year = pd.Timestamp.today().year
    con = sqlite3.connect(tmp_path / f'trades{year}.db')
    con.execute('CREATE TABLE trades (ticker, longName, Strategy, buyVolume, buyPriceEUR, sellPriceEUR, sellVolume)')
    con.executemany('INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?)', [
        ('SAP.DE', 'SAP', 'S/R', 10, 200.0, 220.0, None),     # open
        ('ALV.DE', 'Allianz', 'S/R', 5, 300.0, 330.0, 5),     # closed
    ])
    con.commit()
    con.close()
    df = pm.multi_positions(db_path='database')
    assert df['ticker'].tolist() == ['SAP.DE']
    assert df['positionValue'].iloc[0] == pytest.approx(2200.0)
    assert df['gainPct'].iloc[0] == pytest.approx(10.0)
    assert df['strategy'].iloc[0] == 'S/R'


def test_enrich_converts_sizes_and_takes_latest_sim_row(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    con = sqlite3.connect(tmp_path / 'asset_info.db')
    con.execute('CREATE TABLE asset_info (ticker, longName, sector, marketCap, enterpriseValue, ebitdaMargins, '
                'totalDebt, totalRevenue, currency, financialCurrency)')
    con.execute("INSERT INTO asset_info VALUES ('K.KS', 'Korea Co', 'Industrials', 1.5e12, NULL, 0.1, "
                "NULL, NULL, 'KRW', 'KRW')")
    con.commit()
    con.close()
    today = pd.Timestamp.today().normalize()
    con = sqlite3.connect(tmp_path / 'asset_simulation_.db')
    con.execute('CREATE TABLE asset_simulation (ticker, Date, close, sharpe, moTrend)')
    con.executemany('INSERT INTO asset_simulation VALUES (?, ?, ?, ?, ?)', [
        ('K.KS', str(today - pd.Timedelta(days=2)), 100.0, 0.5, 3.0),
        ('K.KS', str(today - pd.Timedelta(days=1)), 101.0, 0.6, 4.0),
    ])
    con.commit()
    con.close()
    monkeypatch.setattr('tradinglib.utils.DataUtils.get_exchange_rate',
                        lambda symbol, system_currency: 1500.0 if symbol == 'KRW' else 1.0)
    rows = pm._frame([{'ticker': 'K.KS', 'longName': '', 'strategy': '', 'shares': 1,
                       'positionValue': 100, 'gainPct': 1.0},
                      {'ticker': 'NEW', 'longName': None, 'strategy': '', 'shares': 1,
                       'positionValue': 50, 'gainPct': 0.0}])
    out = pm.enrich(rows, db_path='database').set_index('ticker')
    assert out.loc['K.KS', 'marketCap'] == pytest.approx(1e9)      # KRW -> EUR
    assert out.loc['K.KS', 'longName'] == 'Korea Co' and out.loc['K.KS', 'sharpe'] == 0.6
    assert out.loc['NEW', 'sector'] == 'Other' and out.loc['NEW', 'longName'] == 'NEW'
    assert np.isnan(out.loc['NEW', 'sharpe'])


def test_market_map_locale_keys_exist_in_both_languages():
    import re
    root = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(root, 'tradinglib', 'market_map.py'), encoding='utf-8').read()
    keys = set(re.findall(r"t\('(mm\.[a-z_]+)'", src))
    keys |= {f'mm.src_{s}' for s in ('index',) + pm.SOURCES} | {'mm.group_sector', 'mm.group_strategy'}
    for lang in ('de', 'en'):
        with open(os.path.join(root, 'locales', f'{lang}.json'), encoding='utf-8') as fh:
            have = json.load(fh)
        assert not sorted(k for k in keys if k not in have), lang
