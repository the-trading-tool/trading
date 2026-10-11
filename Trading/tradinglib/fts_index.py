"""Full-text search index over asset_info — single definition of the schema.

The FTS5 table was previously created in three places (search.py twice, once in
get_asset_info.py), each with its own copy of the CREATE/INSERT statements. They
had to stay byte-compatible or the app and the CLI would build different tables.
Everything now goes through here, so the schema exists exactly once.

The ISIN lives in a different database (``yf_tickers.db.stocks``), not in
asset_info, so filling it means attaching that file while the index is built.
When it is missing the index is still built, just with empty ISINs — searching
by name or ticker keeps working.
"""
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# The indexed columns. Order matters: the INSERT below lists them explicitly.
FTS_COLUMNS = ('ticker', 'longName', 'isin')
SOURCE_COLUMNS = ('ticker', 'longName')      # what asset_info itself must provide


def fts_table_name(table_name: str = 'asset_info') -> str:
    return f'{table_name}_fts'


def current_columns(conn: sqlite3.Connection, fts: str) -> tuple:
    """Columns of the existing FTS table, empty when it does not exist."""
    try:
        return tuple(row[1] for row in conn.execute(f'PRAGMA table_info({fts})'))
    except sqlite3.Error:
        return ()


def schema_is_current(conn: sqlite3.Connection, table_name: str = 'asset_info') -> bool:
    """True when the existing index has exactly the columns we index today.

    Checked explicitly because ``CREATE VIRTUAL TABLE IF NOT EXISTS`` silently
    keeps an older table with fewer columns: without this the new ISIN column
    would appear to do nothing until someone dropped the table by hand.
    """
    return current_columns(conn, fts_table_name(table_name)) == FTS_COLUMNS


def _default_tickers_db(conn: sqlite3.Connection) -> str:
    """yf_tickers.db next to the database this connection is attached to."""
    try:
        for _seq, name, path in conn.execute('PRAGMA database_list'):
            if name == 'main' and path:
                return os.path.join(os.path.dirname(path), 'yf_tickers.db')
    except sqlite3.Error:
        pass
    return ''


def rebuild(conn: sqlite3.Connection, table_name: str = 'asset_info',
            tickers_db: str = None) -> int:
    """Drop and rebuild the index from the current source data.

    Returns the number of indexed rows, or -1 when the source table lacks the
    required columns (get_asset_info.py has not populated it yet).
    """
    fts = fts_table_name(table_name)
    cur = conn.cursor()

    cols = {row[1] for row in cur.execute(f'PRAGMA table_info({table_name})')}
    if not set(SOURCE_COLUMNS) <= cols:
        logger.warning('FTS rebuild skipped: %s is missing %s',
                       table_name, ', '.join(sorted(set(SOURCE_COLUMNS) - cols)))
        return -1

    if tickers_db is None:
        tickers_db = _default_tickers_db(conn)
    attached = False
    if tickers_db and os.path.exists(tickers_db):
        try:
            cur.execute('ATTACH DATABASE ? AS tickdb', (tickers_db,))
            attached = True
        except sqlite3.Error as e:
            logger.warning('Could not attach %s for ISIN lookup: %s', tickers_db, e)

    # A correlated subquery rather than a JOIN: a duplicated stocks row would
    # otherwise multiply the indexed rows instead of just supplying an ISIN.
    isin_expr = ("(SELECT s.ISIN FROM tickdb.stocks s "
                 "WHERE UPPER(s.Ticker) = UPPER(a.ticker) "
                 "AND s.ISIN IS NOT NULL AND TRIM(s.ISIN) NOT IN ('', 'None', 'nan') "
                 "LIMIT 1)") if attached else "NULL"

    try:
        cur.execute(f'DROP TABLE IF EXISTS {fts};')
        cur.execute(f'CREATE VIRTUAL TABLE {fts} USING fts5({", ".join(FTS_COLUMNS)});')
        cur.execute(
            f'INSERT INTO {fts} (ticker, longName, isin) '
            f'SELECT DISTINCT a.ticker, a.longName, COALESCE({isin_expr}, "") '
            f'FROM {table_name} a;'
        )
        conn.commit()
        n = cur.execute(f'SELECT COUNT(*) FROM {fts}').fetchone()[0]
        with_isin = cur.execute(
            f"SELECT COUNT(*) FROM {fts} WHERE isin != ''").fetchone()[0]
    finally:
        if attached:
            try:
                cur.execute('DETACH DATABASE tickdb')
            except sqlite3.Error:
                pass

    logger.info('FTS index %s rebuilt: %d rows, %d with ISIN.', fts, n, with_isin)
    return n


def ensure(conn: sqlite3.Connection, table_name: str = 'asset_info',
           tickers_db: str = None) -> int:
    """Build the index when it is missing, outdated or empty; otherwise leave it.

    Returns the number of rows indexed by this call, 0 when nothing was done.
    """
    fts = fts_table_name(table_name)
    if not schema_is_current(conn, table_name):
        logger.info('FTS index %s missing or outdated (%s) — rebuilding.',
                    fts, ', '.join(current_columns(conn, fts)) or 'absent')
        return max(rebuild(conn, table_name, tickers_db), 0)
    try:
        if conn.execute(f'SELECT COUNT(*) FROM {fts}').fetchone()[0] == 0:
            return max(rebuild(conn, table_name, tickers_db), 0)
    except sqlite3.Error:
        return max(rebuild(conn, table_name, tickers_db), 0)
    return 0
