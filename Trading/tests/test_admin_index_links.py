"""Admin ▸ Ticker ▸ Manage indices: move a ticker between index groups."""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import admin
from tradinglib.tools import open_db


@pytest.fixture()
def db(tmp_path):
    """yf_tickers.db with AAD.DE in ^SDAXI and one ticker without any index."""
    path = str(tmp_path / 'yf_tickers.db')
    with open_db(path) as conn:
        conn.executescript("""
            CREATE TABLE stocks (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                 Ticker TEXT, Date TEXT, INVESTED REAL, ISIN TEXT);
            CREATE TABLE indices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
            CREATE TABLE stock_indices (stock_id INTEGER, index_id INTEGER);
            INSERT INTO stocks (Ticker) VALUES ('AAD.DE'), ('SAP.DE'), ('LONELY');
            INSERT INTO indices (name) VALUES ('^SDAXI'), ('^MDAXI'), ('^GDAXI');
            INSERT INTO stock_indices VALUES (1, 1), (2, 3);
        """)
    return path


def test_zugehoerigkeit_lesen(db):
    sid, names = admin.index_links(db, 'AAD.DE')
    assert sid == 1 and names == ['^SDAXI']
    assert admin.index_links(db, 'LONELY') == (3, [])
    assert admin.index_links(db, 'NOPE') == (None, [])


def test_verschieben_aus_einem_index_in_einen_anderen(db):
    sid, current = admin.index_links(db, 'AAD.DE')
    target = ['^MDAXI']
    added, removed = admin.set_index_links(db, sid, sorted(set(target) - set(current)),
                                           sorted(set(current) - set(target)))
    assert (added, removed) == (1, 1)
    assert admin.index_links(db, 'AAD.DE')[1] == ['^MDAXI']
    # the ticker itself stays — leaving an index is not a delisting
    with open_db(db, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM stocks WHERE Ticker='AAD.DE'").fetchone()[0] == 1


def test_doppelte_verknuepfung_und_unbekannter_index(db):
    sid, _ = admin.index_links(db, 'AAD.DE')
    assert admin.set_index_links(db, sid, ['^SDAXI'], []) == (0, 0)     # already linked
    assert admin.set_index_links(db, sid, [], ['^GDAXI']) == (0, 0)     # was not linked
    assert admin.set_index_links(db, sid, ['CRYPTO'], []) == (1, 0)     # new group is created
    assert admin.index_links(db, 'AAD.DE')[1] == ['CRYPTO', '^SDAXI']


def test_locale_keys_in_beiden_sprachen():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, 'tradinglib', 'admin.py'), encoding='utf-8').read()
    used = set(re.findall(r"'(admin\.idx_edit_\w+)'", src))
    assert used
    for lang in ('de', 'en'):
        keys = json.load(open(os.path.join(here, 'locales', f'{lang}.json'), encoding='utf-8'))
        assert not used - set(keys), (lang, sorted(used - set(keys)))


def test_neue_gruppe_beim_verschieben_anlegen(db):
    """Alte Zuordnung entfernen und dabei eine noch nicht vorhandene Gruppe anlegen."""
    sid, current = admin.index_links(db, 'AAD.DE')
    target = ['WATCHLIST']
    added, removed = admin.set_index_links(db, sid, sorted(set(target) - set(current)),
                                           sorted(set(current) - set(target)))
    assert (added, removed) == (1, 1)
    assert admin.index_links(db, 'AAD.DE')[1] == ['WATCHLIST']
    with open_db(db, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM indices WHERE name='WATCHLIST'").fetchone()[0] == 1


def test_freie_eingabe_wird_unterstuetzt():
    """Ohne accept_new_options braeuchte die UI ein eigenes Textfeld."""
    import inspect
    import streamlit as st
    assert admin._MULTISELECT_NEW_OPTIONS == (
        'accept_new_options' in inspect.signature(st.multiselect).parameters)


@pytest.fixture()
def dbdir(tmp_path, monkeypatch):
    """TradingDB with a current-year simulation row for SAP.DE and prices for SAP.DE only."""
    monkeypatch.setenv('TradingDB', str(tmp_path))
    with open_db(str(tmp_path / 'asset_simulation_.db')) as conn:
        conn.execute("CREATE TABLE asset_simulation (ticker TEXT, Date TEXT)")
        conn.execute("INSERT INTO asset_simulation VALUES ('SAP.DE', '2026-10-09 00:00:00')")
    (tmp_path / 'yf_SAP.DE.db').write_bytes(b'')
    return tmp_path


def _keys(follow):
    return [k for _, k, _ in follow]


def test_hinweis_verschieben_zwischen_indizes_braucht_keinen_lauf(dbdir):
    f = admin.membership_followup('SAP.DE', ['^GDAXI'], ['^MDAXI'], held=set())
    assert _keys(f) == ['admin.idx_follow_live', 'admin.idx_follow_breadth']
    assert f[0][2]['date'] == '2026-10-09'
    assert f[1][2]['indices'] == '^GDAXI, ^MDAXI'


def test_hinweis_nur_noch_gruppe_wird_nicht_fortgeschrieben(dbdir):
    f = admin.membership_followup('SAP.DE', ['^GDAXI'], ['WATCHLIST'], held=set())
    assert 'admin.idx_follow_not_daily' in _keys(f)
    kw = dict((k, p) for _, k, p in f)['admin.idx_follow_not_daily']
    assert kw['cmd'] == 'python asset_perf2.py /group:WATCHLIST /fill /silent'
    assert kw['cmd_prices'] == 'python get_asset_data.py /group:WATCHLIST'
    # held tickers stay in the daily run regardless of their index
    held = admin.membership_followup('SAP.DE', ['^GDAXI'], ['WATCHLIST'], held={'SAP.DE'})
    assert 'admin.idx_follow_not_daily' not in _keys(held)


def test_hinweis_ohne_simulation_und_ohne_kurse(dbdir):
    f = dict((k, p) for _, k, p in admin.membership_followup('NEW.DE', [], ['^SDAXI'], held=set()))
    assert f['admin.idx_follow_no_prices']['cmd'] == 'python get_asset_data.py /tickers:NEW.DE 1d:max'
    assert f['admin.idx_follow_no_sim']['cmd'] == 'python asset_perf2.py /index:^SDAXI /fill /silent'


def test_hinweis_in_keinem_index_mehr(dbdir):
    assert _keys(admin.membership_followup('SAP.DE', ['^GDAXI'], [], held=set()))[0] == \
        'admin.idx_follow_orphan'


def test_hinweistexte_lassen_sich_formatieren(dbdir):
    """Every follow-up key exists in both languages and takes exactly its params."""
    cases = [admin.membership_followup('SAP.DE', ['^GDAXI'], ['^MDAXI'], held=set()),
             admin.membership_followup('SAP.DE', ['^GDAXI'], ['WATCHLIST'], held=set()),
             admin.membership_followup('NEW.DE', [], ['^SDAXI'], held=set()),
             admin.membership_followup('SAP.DE', ['^GDAXI'], [], held=set())]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang in ('de', 'en'):
        texts = json.load(open(os.path.join(here, 'locales', f'{lang}.json'), encoding='utf-8'))
        for follow in cases:
            for _, key, kw in follow:
                assert texts[key].format(**kw)
