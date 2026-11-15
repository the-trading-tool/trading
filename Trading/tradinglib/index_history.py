"""Index membership history for yf_tickers.db.

``stock_indices`` only knows the current state: a link is there or it is not,
without a date. Every backtest over past years therefore runs on today's
members, which flatters momentum rules (later index entrants are bought
"from the index" before they were in it).

This module adds ``stock_indices_history``: one row per membership period,
keyed by ticker and index NAME (not by id), so the record survives a deleted
ticker or a re-created index. Rows are written by SQLite triggers on
``stock_indices`` — every writer (sync scripts, the admin pages, seed_db's
dedupe) is covered without having to remember a call.

Semantics:

* ``valid_from`` NULL = the membership already existed when tracking began
  (``index_history_meta.tracking_start``); its real start is unknown.
* ``valid_to`` NULL = still a member.
* A pair is opened only when no open row exists and closed only when no link
  for the same ticker NAME and index remains, so the dedupe in
  ``seed_db.ensure_unique_schema`` (delete duplicate links, repoint ids to the
  canonical row) does not create false exits and re-entries.
* ``source`` / ``source_to`` say which tool caused the change; triggers leave
  them NULL, :func:`tag_changes` fills them after a sync run.

Reading: :func:`members_asof` answers "who was in the index on date D". For a
date before ``tracking_start`` that answer is today's members plus later
leavers — still biased, and :func:`members_asof` says so via ``complete``.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3

import pandas as pd

logger = logging.getLogger(__name__)

TABLE = 'stock_indices_history'
META = 'index_history_meta'

_TICKER_OF = "(SELECT Ticker FROM stocks WHERE id = {ref}.stock_id)"
_INDEX_OF = "(SELECT name FROM indices WHERE id = {ref}.index_id)"
_TODAY = "date('now', 'localtime')"
_NOW = "datetime('now', 'localtime')"


def _open_sql(ref: str) -> str:
    t, i = _TICKER_OF.format(ref=ref), _INDEX_OF.format(ref=ref)
    return (f"INSERT INTO {TABLE} (ticker, index_name, valid_from, changed_at) "
            f"SELECT {t}, {i}, {_TODAY}, {_NOW} "
            f"WHERE {t} IS NOT NULL AND {i} IS NOT NULL AND NOT EXISTS ("
            f"SELECT 1 FROM {TABLE} h WHERE h.ticker = {t} AND h.index_name = {i} "
            f"AND h.valid_to IS NULL);")


def _close_sql(ref: str) -> str:
    t, i = _TICKER_OF.format(ref=ref), _INDEX_OF.format(ref=ref)
    return (f"UPDATE {TABLE} SET valid_to = {_TODAY}, closed_at = {_NOW} "
            f"WHERE ticker = {t} AND index_name = {i} AND valid_to IS NULL "
            f"AND NOT EXISTS (SELECT 1 FROM stock_indices si JOIN stocks s ON s.id = si.stock_id "
            f"WHERE s.Ticker = {t} AND si.index_id = {ref}.index_id);")


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    index_name  TEXT NOT NULL,
    valid_from  TEXT,
    valid_to    TEXT,
    source      TEXT,
    source_to   TEXT,
    changed_at  TEXT,
    closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sih_index ON {TABLE} (index_name, ticker);
CREATE INDEX IF NOT EXISTS idx_sih_ticker ON {TABLE} (ticker);
CREATE TABLE IF NOT EXISTS {META} (key TEXT PRIMARY KEY, value TEXT);
CREATE TRIGGER IF NOT EXISTS trg_sih_insert AFTER INSERT ON stock_indices
BEGIN {_open_sql('NEW')} END;
CREATE TRIGGER IF NOT EXISTS trg_sih_delete AFTER DELETE ON stock_indices
BEGIN {_close_sql('OLD')} END;
CREATE TRIGGER IF NOT EXISTS trg_sih_update AFTER UPDATE ON stock_indices
WHEN {_TICKER_OF.format(ref='OLD')} IS NOT {_TICKER_OF.format(ref='NEW')}
  OR OLD.index_id IS NOT NEW.index_id
BEGIN {_close_sql('OLD')} {_open_sql('NEW')} END;
"""


def is_installed(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' "
                             "AND name='trg_sih_insert'").fetchone())


def ensure_schema(conn: sqlite3.Connection) -> int:
    """Create table and triggers and seed the current memberships once.

    Idempotent. Returns the number of seeded rows (0 once installed). The seed
    rows carry ``valid_from`` NULL: their start date is unknown.
    """
    conn.executescript(SCHEMA)
    started = conn.execute(f"SELECT value FROM {META} WHERE key='tracking_start'").fetchone()
    if started:
        return 0
    cur = conn.execute(
        f"INSERT INTO {TABLE} (ticker, index_name, valid_from, source, changed_at) "
        f"SELECT DISTINCT s.Ticker, i.name, NULL, 'seed', {_NOW} "
        "FROM stock_indices si JOIN stocks s ON s.id = si.stock_id "
        "JOIN indices i ON i.id = si.index_id "
        f"WHERE s.Ticker IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {TABLE} h "
        "WHERE h.ticker = s.Ticker AND h.index_name = i.name AND h.valid_to IS NULL)")
    conn.execute(f"INSERT OR REPLACE INTO {META} (key, value) VALUES ('tracking_start', {_TODAY})")
    conn.commit()
    logger.info("index history installed, %s memberships seeded", cur.rowcount)
    return cur.rowcount


def tracking_start(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute(f"SELECT value FROM {META} WHERE key='tracking_start'").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def run_marker(conn: sqlite3.Connection) -> str:
    """Timestamp to pass to :func:`tag_changes` after a write run (same clock as the triggers)."""
    return conn.execute(f"SELECT {_NOW}").fetchone()[0]


def tag_changes(conn: sqlite3.Connection, since: str, source: str) -> tuple[int, int]:
    """Name the tool behind the history rows the triggers wrote since ``since``.

    Returns (opened, closed) rows tagged.
    """
    opened = conn.execute(f"UPDATE {TABLE} SET source = ? WHERE source IS NULL "
                          "AND changed_at >= ?", (source, since)).rowcount
    closed = conn.execute(f"UPDATE {TABLE} SET source_to = ? WHERE source_to IS NULL "
                          "AND valid_to IS NOT NULL AND closed_at >= ?", (source, since)).rowcount
    conn.commit()
    return opened, closed


def history(conn: sqlite3.Connection, index_name: str | None = None,
            ticker: str | None = None, changes_only: bool = False) -> pd.DataFrame:
    """Membership periods, newest change first. ``changes_only`` drops the
    seed rows that never changed (i.e. keeps real entries and exits)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone():
        return pd.DataFrame(columns=['ticker', 'index_name', 'valid_from', 'valid_to',
                                     'source', 'source_to'])
    where, params = [], []
    if index_name:
        where.append("index_name = ?")
        params.append(index_name)
    if ticker:
        where.append("ticker = ?")
        params.append(ticker)
    if changes_only:
        where.append("(valid_from IS NOT NULL OR valid_to IS NOT NULL)")
    sql = (f"SELECT ticker, index_name, valid_from, valid_to, source, source_to FROM {TABLE}"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY COALESCE(valid_to, valid_from, '') DESC, index_name, ticker")
    return pd.read_sql_query(sql, conn, params=params)


def members_asof(conn: sqlite3.Connection, index_name: str,
                 date: str | dt.date) -> tuple[set[str], bool]:
    """Members of ``index_name`` on ``date`` and whether that answer is complete.

    Complete means ``date`` is on or after ``tracking_start``. Before that, the
    set holds everybody whose period is not known to have started later — the
    then-members that left before tracking began are missing.
    """
    d = pd.Timestamp(date).strftime('%Y-%m-%d')
    rows = conn.execute(
        f"SELECT DISTINCT ticker FROM {TABLE} WHERE index_name = ? "
        "AND (valid_from IS NULL OR valid_from <= ?) AND (valid_to IS NULL OR valid_to > ?)",
        (index_name, d, d)).fetchall()
    start = tracking_start(conn)
    return {r[0] for r in rows}, bool(start and d >= start)
