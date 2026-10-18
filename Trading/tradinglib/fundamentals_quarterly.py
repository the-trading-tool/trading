"""Quarterly results, point in time: what was reported, and from when it counts.

Minervini's fundamental criteria (SEPA) are quarterly: earnings and sales well
above the year-ago quarter, growth accelerating over two or three quarters,
positive surprises. A weekly snapshot of ``asset_info`` cannot deliver that —
``trailingEps`` is a rolling figure and says nothing about a single quarter.

Two Yahoo sources are merged per quarter:

* ``quarterly_income_stmt``: revenue, net income, diluted EPS, gross profit,
  operating income for about six quarters. It lags: in October 2026 Goldman
  Sachs had reported Q3 on the 13th, the statement still ended with Q2.
* ``get_earnings_dates``: reported EPS, the estimate and the surprise, with the
  exact date and time of the release — and the next scheduled release.

**The report date is the point of the table.** A quarter's figures only exist
from the day they were published; using them from the quarter end on would be
the same look-ahead that made ``pctTargetHighPrice`` look like an alpha source.
When no release date is known, the report date is estimated conservatively as
quarter end + 75 days (``report_date_source = 'estimated'``) and replaced once
the real date shows up.

**First reported value wins.** Yahoo restates figures later; overwriting would
leak the corrected number back into the past. Existing values are never
overwritten — only empty fields are filled.

**Fetching is incremental.** A ticker is only asked again when its next
scheduled release has passed, or after 30 days without a check. Once the table
is filled that is roughly one ticker in twelve per week.

Usage::

    from tradinglib import fundamentals_quarterly as fq
    rows, next_report = fq.fetch('GS')
    fq.record(conn, 'GS', rows, next_report)
    frame = fq.asof_frame(['GS'], '2026-08-01')   # quarters published by then

CLI::

    python -m tradinglib.fundamentals_quarterly                  # coverage report
    python -m tradinglib.fundamentals_quarterly /backfill        # all due equities
    python -m tradinglib.fundamentals_quarterly /backfill /worker:4 /tickers:GS,SAP.DE
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

TABLE = 'fundamentals_quarterly'
META = 'fundamentals_quarterly_meta'

ESTIMATED_REPORT_LAG_DAYS = 75   # conservative: 10-Q deadline is 40-45 days, abroad longer
MAX_REPORT_LAG_DAYS = 120        # a release later than this belongs to no quarter we know
RECHECK_DAYS = 30                # ask again after this long even without a scheduled date

# Statement rows -> columns. The first row present wins.
STATEMENT_ROWS = {
    'revenue':          ('Total Revenue', 'Operating Revenue'),
    'net_income':       ('Net Income Common Stockholders', 'Net Income'),
    'eps_diluted':      ('Diluted EPS', 'Basic EPS'),
    'gross_profit':     ('Gross Profit',),
    'operating_income': ('Operating Income', 'Total Operating Income As Reported'),
}
VALUE_COLUMNS = ('eps_reported', 'eps_estimate', 'surprise_pct', 'eps_diluted',
                 'revenue', 'net_income', 'gross_profit', 'operating_income')


def _db_path(db_path: str = 'database', db_name: str = 'asset_info.db') -> str:
    from tradinglib.tools import Tools
    return Tools().get_path(path=db_path, file_name=db_name)


def ensure_tables(conn) -> None:
    """Create the quarterly table and its fetch bookkeeping when missing."""
    values = ', '.join(f'{c} REAL' for c in VALUE_COLUMNS)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE} ("
        f"ticker TEXT NOT NULL, period_end TEXT NOT NULL, "
        f"report_date TEXT, report_date_source TEXT, {values}, "
        f"first_seen TEXT, updated_at TEXT, PRIMARY KEY (ticker, period_end))")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_ticker_report "
                 f"ON {TABLE} (ticker, report_date)")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {META} ("
        f"ticker TEXT PRIMARY KEY, next_report_date TEXT, checked_at TEXT)")
    conn.commit()


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _month_end(ts) -> pd.Timestamp:
    return (pd.Timestamp(ts) + pd.offsets.MonthEnd(0)).normalize()


def _quarter_end_for(report: pd.Timestamp, known_ends: list) -> pd.Timestamp | None:
    """The fiscal quarter a release on *report* belongs to.

    The quarter ends follow the company's fiscal calendar, taken from the
    statement columns and extended in steps of three months; without statement
    data calendar quarters are assumed. The quarter is the last end strictly
    before the release, at most MAX_REPORT_LAG_DAYS earlier.
    """
    report = pd.Timestamp(report).tz_localize(None) if pd.Timestamp(report).tzinfo else pd.Timestamp(report)
    if known_ends:
        anchor = _month_end(max(known_ends))
    else:
        anchor = _month_end(pd.Timestamp(report.year, ((report.month - 1) // 3) * 3 + 1, 1)
                            - pd.Timedelta(days=1))
    # Walk the three-month grid from the anchor to the release.
    candidate = anchor
    while candidate + pd.DateOffset(months=3) < report:
        candidate = _month_end(candidate + pd.DateOffset(months=3))
    while candidate >= report:
        candidate = _month_end(candidate - pd.DateOffset(months=3))
    if (report - candidate).days > MAX_REPORT_LAG_DAYS:
        return None
    return candidate


def parse(statement: pd.DataFrame | None, earnings: pd.DataFrame | None,
          today: date | None = None):
    """Merge the two Yahoo frames into quarter rows. Pure function, no I/O.

    Returns ``(rows, next_report_date)``: rows are dicts keyed like the table
    columns (without ticker/bookkeeping), next_report_date an ISO date or None.
    """
    today = pd.Timestamp(today or date.today())
    quarters: dict[pd.Timestamp, dict] = {}

    known_ends = []
    if statement is not None and not statement.empty:
        for col in statement.columns:
            end = pd.to_datetime(col, errors='coerce')
            if pd.isna(end):
                continue
            end = _month_end(end)
            known_ends.append(end)
            row = quarters.setdefault(end, {})
            for field, names in STATEMENT_ROWS.items():
                for name in names:
                    if name in statement.index:
                        value = _num(statement.at[name, col])
                        if value is not None:
                            row[field] = value
                            break

    next_report = None
    if earnings is not None and not earnings.empty:
        for when, rec in earnings.iterrows():
            ts = pd.Timestamp(when)
            ts = ts.tz_localize(None) if ts.tzinfo else ts
            reported = _num(rec.get('Reported EPS'))
            day = ts.strftime('%Y-%m-%d')
            if reported is None and ts.normalize() >= today.normalize():
                next_report = day if next_report is None or day < next_report else next_report
                continue
            end = _quarter_end_for(ts, known_ends)
            if end is None:
                continue
            row = quarters.setdefault(end, {})
            if reported is not None:
                row['eps_reported'] = reported
                row['eps_estimate'] = _num(rec.get('EPS Estimate'))
                row['surprise_pct'] = _num(rec.get('Surprise(%)'))
            # A past release without EPS (Yahoo gap, e.g. WMT 2026-08-20) still
            # dates the quarter — better than the 75-day estimate.
            # Two releases for one quarter (e.g. preliminary + final): the first counts.
            if row.get('report_date_source') != 'earnings_dates' or day < row['report_date']:
                row['report_date'] = day
                row['report_date_source'] = 'earnings_dates'

    rows = []
    for end, row in sorted(quarters.items()):
        if not any(row.get(c) is not None for c in VALUE_COLUMNS):
            continue
        if 'report_date' not in row:
            row['report_date'] = (end + pd.Timedelta(days=ESTIMATED_REPORT_LAG_DAYS)).strftime('%Y-%m-%d')
            row['report_date_source'] = 'estimated'
        row['period_end'] = end.strftime('%Y-%m-%d')
        rows.append(row)
    return rows, next_report


def fetch(ticker: str):
    """Download and parse one ticker. Returns ``(rows, next_report)`` or None on failure."""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        try:
            statement = tk.quarterly_income_stmt
        except Exception:
            statement = None
        try:
            earnings = tk.get_earnings_dates(limit=16)
        except Exception:
            earnings = None
        if (statement is None or statement.empty) and (earnings is None or earnings.empty):
            return [], None
        return parse(statement, earnings)
    except Exception:
        logger.debug("fundamentals_quarterly: fetch failed for %s", ticker, exc_info=True)
        return None


def record(conn, ticker: str, rows, next_report=None, checked_at: str = '') -> int:
    """Store quarters for one ticker. First reported values win; empty fields are filled.

    An estimated report date is replaced by a real one. Returns the number of
    new quarters. Never raises.
    """
    try:
        ensure_tables(conn)
        now = checked_at or datetime.now().strftime('%Y-%m-%d')
        existing = {r[0]: r for r in conn.execute(
            f"SELECT period_end, report_date, report_date_source, {', '.join(VALUE_COLUMNS)} "
            f"FROM {TABLE} WHERE ticker = ?", (ticker,))}
        new = 0
        for row in rows or []:
            pe = row['period_end']
            if pe not in existing:
                cols = ['ticker', 'period_end', 'report_date', 'report_date_source',
                        *VALUE_COLUMNS, 'first_seen', 'updated_at']
                vals = [ticker, pe, row.get('report_date'), row.get('report_date_source'),
                        *[row.get(c) for c in VALUE_COLUMNS], now, now]
                conn.execute(f"INSERT INTO {TABLE} ({', '.join(cols)}) "
                             f"VALUES ({','.join('?' * len(cols))})", vals)
                new += 1
                continue
            old = existing[pe]
            sets, params = [], []
            for i, c in enumerate(VALUE_COLUMNS):
                if old[3 + i] is None and row.get(c) is not None:
                    sets.append(f'{c} = ?')
                    params.append(row[c])
            if old[2] != 'earnings_dates' and row.get('report_date_source') == 'earnings_dates':
                sets += ['report_date = ?', 'report_date_source = ?']
                params += [row['report_date'], 'earnings_dates']
            if sets:
                conn.execute(f"UPDATE {TABLE} SET {', '.join(sets)}, updated_at = ? "
                             f"WHERE ticker = ? AND period_end = ?",
                             params + [now, ticker, pe])
        conn.execute(f"INSERT OR REPLACE INTO {META} (ticker, next_report_date, checked_at) "
                     f"VALUES (?, ?, ?)", (ticker, next_report, now))
        conn.commit()
        return new
    except Exception:
        # Drop this ticker's partial writes; the next commit would keep them without
        # a meta row. No meta row means the ticker is due again on the next run.
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("fundamentals_quarterly: %s not stored", ticker, exc_info=True)
        return 0


def due_tickers(conn, tickers, today: date | None = None) -> list:
    """Tickers worth asking again: never checked, next release passed, or stale."""
    ensure_tables(conn)
    today = today or date.today()
    meta = {r[0]: (r[1], r[2]) for r in conn.execute(
        f"SELECT ticker, next_report_date, checked_at FROM {META}")}
    stale = (today - timedelta(days=RECHECK_DAYS)).isoformat()
    out = []
    for t in tickers:
        if t not in meta:
            out.append(t)
            continue
        next_report, checked = meta[t]
        if not checked or checked <= stale:
            out.append(t)
        elif next_report and next_report < today.isoformat() and checked <= next_report:
            out.append(t)
    return out


def equity_tickers(conn) -> list:
    """All equities in asset_info (quoteType EQUITY) — Minervini screens stocks only."""
    try:
        return [r[0] for r in conn.execute(
            "SELECT ticker FROM asset_info WHERE quoteType = 'EQUITY' ORDER BY ticker")]
    except Exception:
        return []


def asof_frame(tickers, when, db_path: str = 'database') -> pd.DataFrame:
    """Quarters of *tickers* that had been published on *when* (report_date <= when)."""
    from tradinglib.tools import open_db
    path = _db_path(db_path)
    tickers = list(tickers or [])
    when = pd.to_datetime(when, errors='coerce')
    if not tickers or pd.isna(when) or not os.path.exists(path):
        return pd.DataFrame()
    conn = open_db(path, readonly=True)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                            (TABLE,)).fetchone():
            return pd.DataFrame()
        frames = []
        for start in range(0, len(tickers), 500):
            part = tickers[start:start + 500]
            frames.append(pd.read_sql_query(
                f"SELECT * FROM {TABLE} WHERE ticker IN ({','.join('?' * len(part))}) "
                f"AND report_date <= ? ORDER BY ticker, period_end",
                conn, params=part + [when.strftime('%Y-%m-%d')]))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    finally:
        conn.close()


def coverage(db_path: str = 'database') -> dict:
    """Rows, tickers, latest quarter and share of real report dates."""
    from tradinglib.tools import open_db
    path = _db_path(db_path)
    empty = {'rows': 0, 'tickers': 0, 'last_period': None, 'real_dates': 0.0}
    if not os.path.exists(path):
        return empty
    conn = open_db(path, readonly=True)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                            (TABLE,)).fetchone():
            return empty
        rows, tickers, last, real = conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT ticker), MAX(period_end), "
            f"AVG(report_date_source = 'earnings_dates') FROM {TABLE}").fetchone()
        return {'rows': rows or 0, 'tickers': tickers or 0, 'last_period': last,
                'real_dates': round(100 * (real or 0), 1)}
    finally:
        conn.close()


def backfill(tickers=None, workers: int = 1, db_path: str = 'database') -> dict:
    """Fetch every due equity (or the given tickers) and store the results."""
    import concurrent.futures
    import time
    from tradinglib.tools import open_db
    # Long batch next to other writers (app, scheduler jobs): wait longer for a lock.
    conn = open_db(_db_path(db_path), timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        ensure_tables(conn)
        universe = list(tickers) if tickers else equity_tickers(conn)
        todo = due_tickers(conn, universe) if not tickers else universe
        logger.info("fundamentals_quarterly: %d of %d tickers due", len(todo), len(universe))
        stats = {'checked': 0, 'failed': 0, 'new_quarters': 0}

        def _one(t):
            time.sleep(0.3)
            return t, fetch(t)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
            for t, result in ex.map(_one, todo):
                if result is None:
                    stats['failed'] += 1
                    continue
                rows, next_report = result
                stats['new_quarters'] += record(conn, t, rows, next_report)
                stats['checked'] += 1
                if stats['checked'] % 250 == 0:
                    logger.info("fundamentals_quarterly: %d checked, %d new quarters",
                                stats['checked'], stats['new_quarters'])
        return stats
    finally:
        conn.close()


def main(argv=None) -> int:
    import sys
    from tradinglib import cli
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = cli.parse_args(argv or sys.argv)
    if args.get('backfill') is not None or '/backfill' in [a.lower() for a in (argv or sys.argv)[1:]]:
        tickers = [t.strip() for t in str(args.get('tickers') or '').split(',') if t.strip()] or None
        print(backfill(tickers=tickers, workers=args.get('worker') or 1))
    print(coverage())
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
