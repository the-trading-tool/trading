"""Point-in-time fundamentals: what was known about a company, and when.

``asset_info`` holds exactly one row per ticker — today's snapshot. That is the
right shape for a screener and the wrong shape for a backtest, and the
difference is not academic. Measured over 2020-2026 on 5.02m rows:

* ``pctTargetHighPrice`` — the distance from the close to the analyst high
  target — reaches a rank IC of 0.45 against the *252-day* forward return, 0.15
  against the 21-day one. A real signal decays with the horizon; this one
  improves, which is the signature of a number anchored on a later price. Its
  rank correlation with the deliberately constructed look-ahead column (today's
  target divided by the historical close) is 0.976.
* The effect is *strongest* among the largest and most liquid names (IC 0.151
  vs 0.121 for the least liquid third), so it is not survivorship either.
* Every other fundamental behaves the same way for the same reason: today's
  margins, growth and returns describe companies that already did well over the
  window being measured. ``operatingMargins`` scores IC 0.039, ``returnOnAssets``
  0.044 — numbers that would be remarkable if they were not hindsight.

So no fundamental from the snapshot can be measured against history. This module
fixes the cause rather than the symptom: it appends a dated row whenever a
tracked figure changes, so that from now on there is a series to ask.

**Only price-independent figures are stored.** Ratios like forward P/E or
price-to-book carry today's price inside them, which would smuggle the same
artefact back in; they are computed at read time against the historical close
instead — ``forwardEps`` and ``bookValue`` are what gets recorded.

Nothing in the app consumes this table yet, and that is deliberate: until it
spans enough time, a backtest over it would be shorter than the strategies it is
meant to judge. The existing ``overallValueTrend`` is untouched — live
strategies depend on its current behaviour (see the ``Sum`` docstring in
``asset_perf2``).

Usage::

    from tradinglib import fundamentals_history as fh
    fh.record(conn, batch)                     # called by get_asset_info.py
    frame = fh.asof_frame(['AAPL'], '2026-06-30')   # what was known back then
    print(fh.format_coverage())

CLI::

    python -m tradinglib.fundamentals_history            # coverage report
    python -m tradinglib.fundamentals_history /seed      # first snapshot from asset_info
"""

from __future__ import annotations

import logging
import os
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

TABLE = 'asset_info_history'

# Figures that describe the company, not its price. A ratio against the price
# belongs on the read side, computed with the close of the day being scored.
TRACKED_FIELDS = (
    'forwardEps', 'trailingEps', 'bookValue', 'dividendRate', 'payoutRatio',
    'targetHighPrice', 'targetMeanPrice', 'targetLowPrice',
    'numberOfAnalystOpinions', 'recommendationMean',
    'totalRevenue', 'ebitda', 'totalDebt', 'totalCash', 'freeCashflow',
    'sharesOutstanding', 'marketCap', 'enterpriseValue',
    'returnOnAssets', 'returnOnEquity', 'operatingMargins', 'grossMargins',
    'profitMargins', 'earningsGrowth', 'revenueGrowth', 'debtToEquity',
)

# A figure counts as changed when it moves by more than this, relative to its
# own size. Yahoo rounds differently between calls; without a tolerance the
# table would grow a row per ticker per run and say nothing.
REL_TOLERANCE = 1e-4


def _db_path(db_path: str = 'database', db_name: str = 'asset_info.db') -> str:
    from tradinglib.tools import Tools
    return Tools().get_path(path=db_path, file_name=db_name)


def ensure_table(conn) -> None:
    """Create the history table and its index when they are missing."""
    columns = ', '.join(f'{field} REAL' for field in TRACKED_FIELDS)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE} ("
        f"ticker TEXT NOT NULL, asof TEXT NOT NULL, {columns}, "
        f"PRIMARY KEY (ticker, asof))")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_ticker_asof "
                 f"ON {TABLE} (ticker, asof)")
    conn.commit()


def _numeric(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _changed(new: dict, previous: dict) -> bool:
    """True when any tracked figure differs beyond the rounding tolerance."""
    for field in TRACKED_FIELDS:
        a, b = new.get(field), previous.get(field)
        if (a is None) != (b is None):
            return True
        if a is None:
            continue
        scale = max(abs(a), abs(b), 1e-9)
        if abs(a - b) / scale > REL_TOLERANCE:
            return True
    return False


def _latest(conn, tickers: list) -> dict:
    """Most recent stored row per ticker, as {ticker: {field: value}}."""
    if not tickers:
        return {}
    out = {}
    columns = ', '.join(TRACKED_FIELDS)
    chunk = 500
    for start in range(0, len(tickers), chunk):
        part = tickers[start:start + chunk]
        placeholders = ','.join('?' * len(part))
        rows = conn.execute(
            f"SELECT h.ticker, {columns} FROM {TABLE} h "
            f"JOIN (SELECT ticker, MAX(asof) AS asof FROM {TABLE} "
            f"      WHERE ticker IN ({placeholders}) GROUP BY ticker) last "
            f"  ON h.ticker = last.ticker AND h.asof = last.asof", part).fetchall()
        for row in rows:
            out[row[0]] = dict(zip(TRACKED_FIELDS, row[1:]))
    return out


def record(conn, rows, asof: str = '') -> int:
    """Append a dated snapshot for every ticker whose figures moved.

    *rows* are the same dicts ``get_asset_info`` hands to ``bulk_upsert_dicts``.
    Returns the number of history rows written. Never raises: a failure here must
    not cost the caller its already-committed master data.
    """
    try:
        ensure_table(conn)
        asof = asof or date.today().isoformat()
        prepared = {}
        for row in rows or []:
            ticker = row.get('ticker')
            if not ticker:
                continue
            prepared[ticker] = {field: _numeric(row.get(field))
                                for field in TRACKED_FIELDS}
        if not prepared:
            return 0

        previous = _latest(conn, list(prepared))
        batch = [(ticker, asof) + tuple(values[f] for f in TRACKED_FIELDS)
                 for ticker, values in prepared.items()
                 if ticker not in previous or _changed(values, previous[ticker])]
        if not batch:
            logger.info("fundamentals history: nothing changed for %d tickers",
                        len(prepared))
            return 0

        placeholders = ','.join('?' * (len(TRACKED_FIELDS) + 2))
        conn.executemany(
            f"INSERT OR REPLACE INTO {TABLE} (ticker, asof, "
            f"{', '.join(TRACKED_FIELDS)}) VALUES ({placeholders})", batch)
        conn.commit()
        logger.info("fundamentals history: %d of %d tickers changed, stored as of %s",
                    len(batch), len(prepared), asof)
        return len(batch)
    except Exception:
        logger.warning("fundamentals history: snapshot not written", exc_info=True)
        return 0


def asof_frame(tickers, when, db_path: str = 'database') -> pd.DataFrame:
    """What was on record for *tickers* on *when* — the last row at or before it.

    Returns an empty frame when the table does not exist yet or holds nothing
    that old, which is the honest answer for any date before recording started.
    """
    from tradinglib.tools import open_db

    path = _db_path(db_path)
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    tickers = list(tickers or [])
    if not tickers:
        return pd.DataFrame()
    when = pd.to_datetime(when, errors='coerce')
    if pd.isna(when):
        return pd.DataFrame()
    cutoff = when.strftime('%Y-%m-%d')

    conn = open_db(path, readonly=True)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name = ?", (TABLE,)).fetchone():
            return pd.DataFrame()
        frames = []
        chunk = 500
        for start in range(0, len(tickers), chunk):
            part = tickers[start:start + chunk]
            placeholders = ','.join('?' * len(part))
            frames.append(pd.read_sql_query(
                f"SELECT h.* FROM {TABLE} h "
                f"JOIN (SELECT ticker, MAX(asof) AS asof FROM {TABLE} "
                f"      WHERE ticker IN ({placeholders}) AND asof <= ? "
                f"      GROUP BY ticker) last "
                f"  ON h.ticker = last.ticker AND h.asof = last.asof",
                conn, params=part + [cutoff]))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    except Exception:
        logger.warning("fundamentals history: as-of read failed", exc_info=True)
        return pd.DataFrame()
    finally:
        conn.close()


def coverage(db_path: str = 'database') -> dict:
    """How far the series reaches — rows, tickers, first and last date."""
    from tradinglib.tools import open_db

    path = _db_path(db_path)
    empty = {'rows': 0, 'tickers': 0, 'first': None, 'last': None, 'days': 0}
    if not path or not os.path.exists(path):
        return empty
    conn = open_db(path, readonly=True)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name = ?", (TABLE,)).fetchone():
            return empty
        rows, tickers, first, last, days = conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(asof), MAX(asof), "
            f"COUNT(DISTINCT asof) FROM {TABLE}").fetchone()
        return {'rows': rows or 0, 'tickers': tickers or 0,
                'first': first, 'last': last, 'days': days or 0}
    except Exception:
        logger.warning("fundamentals history: coverage read failed", exc_info=True)
        return empty
    finally:
        conn.close()


def seed_from_asset_info(db_path: str = 'database') -> int:
    """Write the first dated snapshot from the current ``asset_info`` contents.

    The starting point of the series, and no more than that: it dates today's
    figures as today's, which is all that can honestly be claimed about them.
    """
    from tradinglib.tools import open_db

    path = _db_path(db_path)
    if not path or not os.path.exists(path):
        logger.error("fundamentals history: asset_info.db not found")
        return 0
    conn = open_db(path)
    try:
        columns = ', '.join(TRACKED_FIELDS)
        frame = pd.read_sql_query(f"SELECT ticker, {columns} FROM asset_info", conn)
        return record(conn, frame.to_dict('records'))
    finally:
        conn.close()


def format_coverage(db_path: str = 'database') -> str:
    """Coverage as text, with what it is and is not good for yet."""
    stats = coverage(db_path)
    if not stats['rows']:
        return ("fundamentals history: empty — run "
                "`python -m tradinglib.fundamentals_history /seed` once to start "
                "the series, after that get_asset_info.py keeps it going.")
    span = ''
    if stats['first'] and stats['last']:
        length = (pd.to_datetime(stats['last']) - pd.to_datetime(stats['first'])).days
        span = f", spanning {length} days"
    return (f"fundamentals history: {stats['rows']:,} rows for {stats['tickers']:,} "
            f"tickers on {stats['days']} distinct dates, "
            f"{stats['first']} to {stats['last']}{span}.\n"
            f"Not usable for backtests until it covers a market cycle — until then "
            f"every fundamental measured against history is hindsight, not signal.")


def main(argv=None) -> int:
    import sys
    argv = argv or sys.argv
    args = [a.lower() for a in argv[1:]]
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if any(a in ('/help', '--help', '-h', '/?') for a in args):
        print(__doc__)
        return 0
    if any(a in ('/seed', '--seed') for a in args):
        written = seed_from_asset_info()
        print(f"{written} snapshot rows written")
    print(format_coverage())
    return 0


if __name__ == '__main__':
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
