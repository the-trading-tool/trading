"""Per-ticker trading status — which symbols the data source no longer serves.

Why a separate table instead of columns in ``asset_info``: that table is built
from the provider's response keys (259 columns, schema follows Yahoo). Our own
assessment does not belong in a foreign schema — it has to survive column churn
and a provider switch.

Status values:
    ``renamed``   symbol was renamed; ``successor`` carries the new one and the
                  price series continues there (1:1, no conversion ratio)
    ``delisted``  trading ceased (takeover, merger, insolvency); ``note`` holds
                  the evidence and ``effective_date`` the last trading day
    ``no_data``   source returns nothing for the symbol and the individual
                  cause was not established — an observation, not a verdict

All three count as inactive: such tickers are dropped from the fetch lists, so
every run stops spending requests on them. Nothing is deleted — history and
past trades keep resolving, and clearing a row undoes the exclusion.

A merger successor (CTRA -> DVN at 0.70) is deliberately NOT modelled as
``renamed``: an exchange ratio breaks the price series, so those are recorded
as ``delisted`` with the successor named in ``note`` only.
"""
import logging
from datetime import datetime

from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

TABLE = 'asset_status'
INACTIVE = ('renamed', 'delisted', 'no_data')
VALID = INACTIVE


def _db_path():
    return Tools().get_path(path='database', file_name='asset_info.db')


def ensure_table(conn):
    """Create the status table if it is missing (idempotent, self-healing)."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            ticker         TEXT PRIMARY KEY,
            status         TEXT NOT NULL,
            successor      TEXT,
            effective_date TEXT,
            note           TEXT,
            source         TEXT,
            updated        TEXT NOT NULL
        )""")
    conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_status ON {TABLE}(status)")


def set_status(ticker, status, successor=None, effective_date=None,
               note=None, source=None, conn=None):
    """Record (or update) the status of one ticker.

    Passing an open ``conn`` keeps a bulk update in a single transaction; the
    caller commits. Without it the function opens and commits on its own.
    """
    if status not in VALID:
        raise ValueError(f"unknown status {status!r}, expected one of {VALID}")
    own = conn is None
    conn = conn or open_db(_db_path())
    try:
        ensure_table(conn)
        conn.execute(
            f"INSERT INTO {TABLE} "
            "(ticker, status, successor, effective_date, note, source, updated) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "  status=excluded.status, successor=excluded.successor, "
            "  effective_date=excluded.effective_date, note=excluded.note, "
            "  source=excluded.source, updated=excluded.updated",
            (ticker, status, successor, effective_date, note, source,
             datetime.now().isoformat(timespec='seconds')))
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def clear_status(ticker, conn=None):
    """Drop the status row — the ticker counts as active again."""
    own = conn is None
    conn = conn or open_db(_db_path())
    try:
        ensure_table(conn)
        conn.execute(f"DELETE FROM {TABLE} WHERE ticker = ?", (ticker,))
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def get_status(ticker):
    """Return the status row as a dict, or None when the ticker is active."""
    try:
        with open_db(_db_path(), readonly=True) as conn:
            ensure_table(conn)
            row = conn.execute(
                f"SELECT ticker, status, successor, effective_date, note, "
                f"source, updated FROM {TABLE} WHERE ticker = ?",
                (ticker,)).fetchone()
    except Exception:
        # A missing/locked DB must never break a fetch run — treat as active.
        logger.debug("asset_status unavailable for %s", ticker, exc_info=True)
        return None
    if not row:
        return None
    keys = ('ticker', 'status', 'successor', 'effective_date', 'note',
            'source', 'updated')
    return dict(zip(keys, row))


def inactive_tickers():
    """Set of all tickers currently excluded from fetch runs."""
    try:
        with open_db(_db_path(), readonly=True) as conn:
            ensure_table(conn)
            ph = ','.join('?' * len(INACTIVE))
            return {r[0] for r in conn.execute(
                f"SELECT ticker FROM {TABLE} WHERE status IN ({ph})", INACTIVE)}
    except Exception:
        # Fail open: without the table every ticker stays in the run. Silently
        # skipping tickers on a DB glitch would be far worse than a wasted call.
        logger.debug("asset_status unavailable — no ticker excluded",
                     exc_info=True)
        return set()


def filter_active(tickers, context=''):
    """Drop inactive tickers from a fetch list and log what was skipped."""
    tickers = list(tickers)
    inactive = inactive_tickers()
    if not inactive:
        return tickers
    kept = [t for t in tickers if t not in inactive]
    skipped = len(tickers) - len(kept)
    if skipped:
        logger.info("%s%d inactive ticker(s) skipped (delisted/renamed), "
                    "%d remaining", f"{context}: " if context else '',
                    skipped, len(kept))
    return kept


def successor_of(ticker):
    """New symbol for a renamed ticker, else None."""
    row = get_status(ticker)
    if row and row['status'] == 'renamed':
        return row['successor']
    return None


def all_status():
    """All status rows, newest first — for the admin view and reports."""
    try:
        with open_db(_db_path(), readonly=True) as conn:
            ensure_table(conn)
            rows = conn.execute(
                f"SELECT ticker, status, successor, effective_date, note, "
                f"source, updated FROM {TABLE} ORDER BY updated DESC, ticker"
            ).fetchall()
    except Exception:
        logger.debug("asset_status unavailable", exc_info=True)
        return []
    keys = ('ticker', 'status', 'successor', 'effective_date', 'note',
            'source', 'updated')
    return [dict(zip(keys, r)) for r in rows]
