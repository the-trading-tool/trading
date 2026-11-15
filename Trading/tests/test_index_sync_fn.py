"""finanzen.net index sync (premium): mapping, plan and apply with history."""
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

fs = pytest.importorskip("tradinglib.premium.index_sync_fn")
from tradinglib import index_history as ih  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _df(rows):
    return pd.DataFrame(rows, columns=['ticker', 'isin', 'name'])


def test_name_score_rules():
    assert fs.name_score('adidas', 'adidas AG') == 1.0
    assert fs.name_score('BBVA', 'Banco Bilbao Vizcaya Argentaria, S.A.') >= 0.9
    assert fs.name_score('Andritz', 'Wienerberger AG') < 0.6
    assert fs.norm_name('Mercedes-Benz Group (ex Daimler)') == 'mercedes benz'


def test_home_suffix_by_country_for_euro_stoxx():
    assert fs.home_suffix('^STOXX50E', 'FR0000120073') == '.PA'
    assert fs.home_suffix('^GDAXI', 'NL0000235190') == '.DE'
    assert fs.on_exchange('AAPL', '') and not fs.on_exchange('SAP.DE', '')


def test_plan_isin_name_and_removal():
    source = [{'name': 'adidas', 'isin': 'DE000A1EWWW0'},
              {'name': 'HOCHTIEF', 'isin': 'DE0006070006'},
              {'name': 'UBS', 'isin': 'CH0244767585'},
              {'name': 'Brandneu', 'isin': 'DE000NEU0001'}]
    members = _df([('ADS.DE', 'DE000A1EWWW0', 'adidas AG'),
                   ('UBSG.DE', None, 'UBS Group AG'),
                   ('PAH3.DE', 'DE000PAH0038', 'Porsche Automobil Holding SE')])
    known = _df([('ADS.DE', 'DE000A1EWWW0', 'adidas AG'), ('HOT.DE', 'DE0006070006', 'HOCHTIEF AG'),
                 ('UBS', 'CH0244767585', 'UBS Group AG'), ('UBSG.DE', None, 'UBS Group AG'),
                 ('PAH3.DE', 'DE000PAH0038', 'Porsche Automobil Holding SE')])
    p = fs.plan('^GDAXI', source, members, known).set_index('ticker', drop=False)
    assert p.loc['ADS.DE', ['action', 'how']].tolist() == ['keep', 'isin']
    assert p.loc['HOT.DE', ['action', 'how']].tolist() == ['add', 'isin_new']
    # the foreign NYSE listing does not beat the member without ISIN
    assert p.loc['UBSG.DE', ['action', 'how']].tolist() == ['keep', 'name']
    assert 'UBS' not in p.index
    assert p.loc['PAH3.DE', 'action'] == 'remove'
    open_rows = p[(p['action'] == 'add') & (p['ticker'] == '')]
    assert open_rows['fn_name'].tolist() == ['Brandneu']


def test_plan_falls_back_to_foreign_listing():
    source = [{'name': 'ArcelorMittal', 'isin': 'LU1598757687'}]
    known = _df([('MT.AS', 'LU1598757687', 'ArcelorMittal S.A.')])
    p = fs.plan('^FCHI', source, _df([]), known)
    assert p.iloc[0][['action', 'ticker', 'how']].tolist() == ['add', 'MT.AS', 'isin_foreign']


def _db(tmp_path):
    db = tmp_path / 'yf_tickers.db'
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE stocks (id INTEGER PRIMARY KEY AUTOINCREMENT, Ticker TEXT, Date TEXT,
                             INVESTED REAL, ISIN TEXT);
        CREATE TABLE indices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
        CREATE TABLE stock_indices (stock_id INTEGER, index_id INTEGER);
        INSERT INTO stocks (Ticker, ISIN) VALUES ('ADS.DE', 'DE000A1EWWW0'), ('PAH3.DE', NULL),
                                                 ('UBSG.DE', NULL);
        INSERT INTO indices (name) VALUES ('^GDAXI');
        INSERT INTO stock_indices VALUES (1, 1), (2, 1), (3, 1);
    """)
    c.commit()
    ih.ensure_schema(c)
    c.close()
    return db


def test_apply_writes_links_isin_and_tagged_history(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    db = _db(tmp_path)
    res = fs.apply('database', '^GDAXI', adds=[('HOT.DE', 'DE0006070006')], removes=['PAH3.DE'],
                   set_isin=[('UBSG.DE', 'CH0244767585')])
    assert res['added'] == ['HOT.DE'] and res['removed'] == ['PAH3.DE']
    assert res['new_tickers'] == ['HOT.DE'] and res['isin_set'] == 1 and res['members'] == 3
    c = sqlite3.connect(db)
    assert c.execute("SELECT ISIN FROM stocks WHERE Ticker='HOT.DE'").fetchone()[0] == 'DE0006070006'
    assert c.execute("SELECT ISIN FROM stocks WHERE Ticker='UBSG.DE'").fetchone()[0] == 'CH0244767585'
    h = ih.history(c, changes_only=True)
    assert h.loc[h['ticker'] == 'HOT.DE', 'source'].tolist() == ['finanzen.net']
    assert h.loc[h['ticker'] == 'PAH3.DE', 'source_to'].tolist() == ['finanzen.net']
    # PAH3.DE keeps its stocks row, only the link is gone
    assert c.execute("SELECT COUNT(*) FROM stocks WHERE Ticker='PAH3.DE'").fetchone()[0] == 1


def test_locale_keys_exist_in_both_languages():
    src = Path(fs.__file__).read_text(encoding='utf-8')
    keys = set(re.findall(r"t\('(fnsync\.[a-z_]+)'", src)) | {'admin.fn_sync_expander'}
    keys |= {f'fnsync.act_{a}' for a in ('add', 'remove', 'keep')}
    keys |= set(fs._HOW_KEYS.values())
    for lang in ('de', 'en'):
        d = json.loads((ROOT / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        assert not keys - set(d), (lang, keys - set(d))
