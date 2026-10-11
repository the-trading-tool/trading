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
    used = set(re.findall(r"t\('(admin\.idx_edit_\w+)'", src))
    assert used
    for lang in ('de', 'en'):
        keys = json.load(open(os.path.join(here, 'locales', f'{lang}.json'), encoding='utf-8'))
        assert not used - set(keys), (lang, sorted(used - set(keys)))
