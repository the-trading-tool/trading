"""Membership history in yf_tickers.db is written by triggers on stock_indices."""
import sqlite3

import pytest

from tradinglib import index_history as ih

SCHEMA = """
CREATE TABLE stocks (id INTEGER PRIMARY KEY AUTOINCREMENT, Ticker TEXT, Date TEXT,
                     INVESTED REAL, ISIN TEXT);
CREATE TABLE indices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
CREATE TABLE stock_indices (stock_id INTEGER, index_id INTEGER);
"""


def _db():
    c = sqlite3.connect(':memory:')
    c.executescript(SCHEMA)
    c.executemany("INSERT INTO stocks (Ticker) VALUES (?)", [('A',), ('B',), ('C',)])
    c.executemany("INSERT INTO indices (name) VALUES (?)", [('^X',), ('^Y',)])
    c.executemany("INSERT INTO stock_indices VALUES (?, ?)", [(1, 1), (2, 1), (1, 2)])
    c.commit()
    return c


def _sid(c, ticker):
    return c.execute("SELECT id FROM stocks WHERE Ticker=?", (ticker,)).fetchone()[0]


def _open(c):
    return set(c.execute(f"SELECT ticker, index_name FROM {ih.TABLE} WHERE valid_to IS NULL"))


def test_seed_marks_existing_members_with_unknown_start():
    c = _db()
    assert ih.ensure_schema(c) == 3
    assert ih.ensure_schema(c) == 0          # idempotent
    rows = c.execute(f"SELECT valid_from, source FROM {ih.TABLE}").fetchall()
    assert all(r == (None, 'seed') for r in rows)
    assert ih.tracking_start(c)


def test_insert_and_delete_open_and_close_periods():
    c = _db()
    ih.ensure_schema(c)
    c.execute("INSERT INTO stock_indices VALUES (3, 1)")          # C joins ^X
    c.execute("DELETE FROM stock_indices WHERE stock_id=2 AND index_id=1")   # B leaves
    c.commit()
    assert ('C', '^X') in _open(c) and ('B', '^X') not in _open(c)
    left = c.execute(f"SELECT valid_to FROM {ih.TABLE} WHERE ticker='B'").fetchone()[0]
    assert left is not None
    joined = c.execute(f"SELECT valid_from FROM {ih.TABLE} WHERE ticker='C'").fetchone()[0]
    assert joined == left                    # both today


def test_duplicate_links_do_not_create_false_exits():
    c = _db()
    ih.ensure_schema(c)
    c.execute("INSERT INTO stock_indices VALUES (1, 1)")          # duplicate A/^X
    c.execute("DELETE FROM stock_indices WHERE rowid = (SELECT MAX(rowid) FROM stock_indices "
              "WHERE stock_id=1 AND index_id=1)")
    c.commit()
    assert c.execute(f"SELECT COUNT(*) FROM {ih.TABLE} WHERE ticker='A' AND index_name='^X'"
                     ).fetchone()[0] == 1
    assert ('A', '^X') in _open(c)


def test_repoint_to_canonical_row_of_same_ticker_is_no_change():
    c = _db()
    ih.ensure_schema(c)
    c.execute("INSERT INTO stocks (Ticker) VALUES ('A')")         # legacy duplicate stocks row
    dup = c.execute("SELECT MAX(id) FROM stocks").fetchone()[0]
    c.execute("UPDATE stock_indices SET stock_id=? WHERE stock_id=1 AND index_id=1", (dup,))
    c.commit()
    rows = c.execute(f"SELECT valid_from, valid_to FROM {ih.TABLE} WHERE ticker='A' "
                     "AND index_name='^X'").fetchall()
    assert rows == [(None, None)]


def test_ticker_deletion_order_of_admin_closes_the_period():
    c = _db()
    ih.ensure_schema(c)
    c.execute("DELETE FROM stock_indices WHERE stock_id=1")
    c.execute("DELETE FROM stocks WHERE id=1")
    c.commit()
    assert not {p for p in _open(c) if p[0] == 'A'}
    # the record survives the deleted ticker
    assert len(ih.history(c, ticker='A')) == 2


def test_tag_changes_names_the_tool():
    c = _db()
    ih.ensure_schema(c)
    mark = ih.run_marker(c)
    c.execute("INSERT INTO stock_indices VALUES (3, 2)")
    c.execute("DELETE FROM stock_indices WHERE stock_id=2")
    c.commit()
    assert ih.tag_changes(c, mark, 'finanzen.net') == (1, 1)
    h = ih.history(c, changes_only=True)
    assert set(h['source'].dropna()) >= {'finanzen.net'}
    assert 'finanzen.net' in set(h['source_to'].dropna())


def test_members_asof_before_and_after_tracking():
    c = _db()
    ih.ensure_schema(c)
    c.execute(f"UPDATE {ih.META} SET value='2026-11-15' WHERE key='tracking_start'")
    c.execute(f"INSERT INTO {ih.TABLE} (ticker, index_name, valid_from, valid_to) "
              "VALUES ('C', '^X', '2026-11-20', NULL)")
    c.execute(f"UPDATE {ih.TABLE} SET valid_to='2026-11-18' WHERE ticker='B'")
    c.commit()
    m, complete = ih.members_asof(c, '^X', '2026-11-16')
    assert m == {'A', 'B'} and complete
    m, _ = ih.members_asof(c, '^X', '2026-11-25')
    assert m == {'A', 'C'}
    _, complete = ih.members_asof(c, '^X', '2020-01-01')
    assert not complete


def test_history_without_table_is_empty():
    c = sqlite3.connect(':memory:')
    assert ih.history(c).empty
    assert ih.tracking_start(c) is None
