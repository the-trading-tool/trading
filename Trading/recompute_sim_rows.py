"""Recompute asset_simulation rows built on daily bars that repair_daily_resample.py replaced.

Indicators are path dependent, so every simulation row of a repaired ticker
from its FIRST changed daily bar on is stale -- including rows on phantom
days (Saturdays) that no longer exist in day_data.

asset_perf2 itself cannot do this: a normal run keeps an existing row and
drops the new one (insert + "keep MIN(rowid)"), and /fill only adds days that
are missing. This script therefore uses asset_perf2's own per-ticker worker
(process_symbol with only_dates, i.e. the /fill path) and, per ticker and only
after the computation succeeded, replaces the stale span in one transaction:

    DELETE rows of the ticker in [start, end]  (start = max(first change, first row))
    INSERT the recomputed rows                  (dates that exist in day_data)

A ticker whose computation fails keeps its old rows. Coverage is never
extended: only the span the database already held for that ticker is redone.
Afterwards the cross-sectional RS rating (mtt_rs) is re-ranked from the
earliest recomputed day on.

The first changed day per ticker comes from diffing day_data against the
backups of repair_daily_resample.py (cached as first_change.csv there).
Every simulation database is copied with VACUUM INTO before its first write.

    python recompute_sim_rows.py                          # dry run: rows per database
    python recompute_sim_rows.py /tickers:MSFT,SAP.DE     # dry run, targeted
    python recompute_sim_rows.py /apply /tickers:MSFT     # write, targeted
    python recompute_sim_rows.py /apply /year:2024        # write, one database
    python recompute_sim_rows.py /apply                   # write, all databases
"""
import concurrent.futures
import glob
import itertools
import logging
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime

import pandas as pd

from tradinglib import cli, logging_config
from tradinglib.tools import Db_tools, Tools, open_db

logger = logging.getLogger(__name__)

TABLE = 'asset_simulation'
REPAIR_BACKUP = 'backup_daily_resample_20261024'
# Earliest copy of the simulation databases, taken before any recompute wrote
# to them. Columns that were empty there stay empty (see write_ticker).
ORIGINAL_BACKUP = 'backup_sim_recompute_20261025'


def _db_dir():
    return os.path.dirname(Tools().get_path(path='database', file_name='asset_info.db'))


def _day_frame(path):
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        return pd.read_sql_query("SELECT * FROM day_data", conn).set_index('Date')


def first_changes(db_dir):
    """{ticker: 'YYYY-MM-DD'} of the earliest day_data row the repair touched."""
    bk = os.path.join(db_dir, REPAIR_BACKUP)
    cache = os.path.join(bk, 'first_change.csv')
    if os.path.exists(cache):
        return pd.read_csv(cache, index_col=0)['first'].to_dict()
    first = {}
    for path in sorted(glob.glob(os.path.join(bk, 'yf_*.db'))):
        ticker = os.path.basename(path)[3:-3]
        live = os.path.join(db_dir, os.path.basename(path))
        if not os.path.exists(live):
            continue
        b, l = _day_frame(path), _day_frame(live)
        common = b.index.intersection(l.index)
        x, y = b.loc[common], l.loc[common]
        changed = x.index[~((x.fillna(-1) - y.fillna(-1)).abs() < 1e-9).all(axis=1)]
        touched = set(changed) | (set(b.index) ^ set(l.index))
        if touched:
            first[ticker] = min(touched)[:10]
    pd.Series(first, name='first').to_csv(cache)
    return first


def databases(db_dir, year_filter, all_only):
    """(file name, asset_perf2 year argument) for every simulation database."""
    now = datetime.now().year
    out = []
    for path in sorted(glob.glob(os.path.join(db_dir, f'{TABLE}_*.db'))):
        name = os.path.basename(path)
        tag = name[len(TABLE) + 1:-3]
        if tag in ('all', 'etp'):
            year, y = '', now
        elif tag == '':
            year, y = '', now
        elif tag.isdigit():
            year, y = int(tag), int(tag)
        else:
            continue
        if all_only and tag not in ('all', 'etp'):
            continue
        if year_filter and (y != int(year_filter) or tag in ('all', 'etp')):
            continue
        out.append((name, year))
    return out


def work_plan(sim_path, first, only, weekend_tickers):
    """{ticker: (start, end, sim_rows, days to compute)} for one database.

    Weekend days are not computed for symbols that do not trade on weekends:
    a weekend row left in day_data is a phantom, not a trading day.
    """
    plan = {}
    with closing(open_db(sim_path, readonly=True)) as conn:
        spans = conn.execute(
            f"SELECT ticker, MIN(Date), MAX(Date) FROM {TABLE} GROUP BY ticker").fetchall()
        for ticker, lo, hi in spans:
            if ticker not in first or (only and ticker not in only):
                continue
            start = max(f"{first[ticker]} 00:00:00", lo)
            if start > hi:
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE ticker = ? AND Date BETWEEN ? AND ?",
                             (ticker, start, hi)).fetchone()[0]
            plan[ticker] = [start, hi, n, None]
    db_dir = os.path.dirname(sim_path)
    for ticker, p in plan.items():
        try:
            days = _day_frame(os.path.join(db_dir, f'yf_{ticker}.db')).index
        except Exception:
            days = pd.Index([])
        days = [d for d in days if p[0] <= d <= p[1]]
        if ticker not in weekend_tickers:
            days = [d for d in days if pd.Timestamp(d[:10]).dayofweek < 5]
        p[3] = {d[:10] for d in days}
    return plan


def original_nulls(ref_path, ticker, start, end):
    """{date: set of columns that were NULL} in the original database."""
    if not ref_path or not os.path.exists(ref_path):
        return {}
    with closing(sqlite3.connect(f"file:{ref_path}?mode=ro", uri=True)) as conn:
        df = pd.read_sql_query(f"SELECT * FROM {TABLE} WHERE ticker = ? AND Date BETWEEN ? AND ?",
                               conn, params=(ticker, start, end))
    return {row['Date'][:10]: {c for c, v in row.items() if v is None or (isinstance(v, float) and pd.isna(v))}
            for row in df.to_dict('records')}


def write_ticker(db, ticker, start, end, rows, nulls=None):
    """Replace the ticker's rows in [start, end] with *rows* in one transaction.

    *nulls* ({date: columns}) keeps columns empty that were empty in the
    original row: some indicators were never backfilled into older
    databases (fps_*, relvol_direction), and filling them for the repaired
    tickers alone would let a formula on those columns pick only them.
    """
    if nulls:
        for r in rows:
            for c in nulls.get(str(r.get('Date', ''))[:10], ()):
                if c in r and c not in ('ticker', 'Date'):
                    r[c] = None
    keys = []
    for r in rows:
        keys.extend(k for k in r if k not in keys)
    db.ensure_table_and_columns(keys, rows[0], TABLE, primary_key=False)
    db.conn.commit()
    try:
        db.cursor.execute(f"DELETE FROM {TABLE} WHERE ticker = ? AND Date BETWEEN ? AND ?",
                          (ticker, start, end))
        for r in rows:
            db.insert_data(r.keys(), r, TABLE, replace=False)
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise


def main():
    args = cli.parse_args()
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))
    apply = bool(args.get('apply'))
    only = args.get('tickers')
    only = {t.strip() for t in str(only).split(',') if t.strip()} if only else None
    workers = int(args.get('worker') or 2)
    db_dir = _db_dir()
    backup_dir = os.path.join(db_dir, f"backup_sim_recompute_{datetime.now():%Y%m%d}")

    first = first_changes(db_dir)
    logger.info("%d repaired tickers with changed daily bars", len(first))

    import asset_perf2
    from repair_daily_resample import _quote_types, trades_weekends
    from tradinglib import minervini

    quote_types = _quote_types()
    weekend_tickers = {t for t in first if trades_weekends(t, quote_types)}

    report = []
    for name, year in databases(db_dir, args.get('year'), bool(args.get('all'))):
        sim_path = os.path.join(db_dir, name)
        plan = work_plan(sim_path, first, only, weekend_tickers)
        ref_path = os.path.join(db_dir, ORIGINAL_BACKUP, name)
        if not os.path.exists(ref_path):
            ref_path = os.path.join(backup_dir, name)
        n_rows = sum(p[2] for p in plan.values())
        logger.info("%s: %d tickers, %d stale rows", name, len(plan), n_rows)
        if not apply or not plan:
            report += [dict(db=name, ticker=t, stale=p[2], days=len(p[3]), written=0, error='dry')
                       for t, p in plan.items()]
            continue

        os.makedirs(backup_dir, exist_ok=True)
        target = os.path.join(backup_dir, name)
        if not os.path.exists(target):
            t0 = time.time()
            with closing(sqlite3.connect(sim_path, timeout=60)) as conn:
                conn.execute("VACUUM INTO ?", (target,))
            logger.info("%s: backup in %.0fs", name, time.time() - t0)

        db = Db_tools(db_path='database', database_name=name)
        t0, done = time.time(), 0
        # Tickers whose stale span holds no day_data day any more (only phantom
        # rows) need no computation, just the deletion.
        empty = [t for t, p in plan.items() if not p[3]]
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(asset_perf2.process_symbol, t, True, False, year=year,
                                     init=False, only_dates=p[3]): t
                           for t, p in plan.items() if p[3]}
                # Write each result as it arrives; collecting a whole database
                # first would hold every recomputed row in memory.
                pending = itertools.chain(concurrent.futures.as_completed(futures), empty)
                for fut in pending:
                    t = fut if isinstance(fut, str) else futures[fut]
                    start, end, stale, days = plan[t]
                    err, written = '', 0
                    try:
                        rows = [] if isinstance(fut, str) else fut.result()
                        if rows is None:
                            err = 'computation failed, old rows kept'
                        elif not rows and days:
                            err = 'no rows computed, old rows kept'
                        else:
                            if rows:
                                write_ticker(db, t, start, end, rows,
                                             original_nulls(ref_path, t, start, end))
                            else:  # every stale row sits on a day day_data no longer has
                                db.cursor.execute(
                                    f"DELETE FROM {TABLE} WHERE ticker = ? AND Date BETWEEN ? AND ?",
                                    (t, start, end))
                                db.conn.commit()
                            written = len(rows)
                    except Exception as e:
                        err = f"{type(e).__name__}: {e}"[:160]
                    done += 1
                    if err:
                        logger.warning("%s %s: %s", name, t, err)
                    if done % 25 == 0 or done == len(plan):
                        logger.info("%s: %d/%d tickers, %.0fs", name, done, len(plan), time.time() - t0)
                    report.append(dict(db=name, ticker=t, stale=stale, days=len(days),
                                       written=written, error=err))
        finally:
            db.close()

        since = min(p[0] for p in plan.values())[:10]
        try:
            n = minervini.update_rs(sim_path, since=since)
            logger.info("%s: RS rating re-ranked since %s (%d rows)", name, since, n)
        except Exception as e:
            logger.warning("%s: RS rating update failed: %s", name, e)

    out = pd.DataFrame(report)
    if len(out):
        logger.info("total: %d stale rows, %d written, %d tickers with errors",
                    int(out.stale.sum()), int(out.written.sum()),
                    int((~out.error.isin(['', 'dry'])).sum()))
        if apply:
            os.makedirs(backup_dir, exist_ok=True)
            path = os.path.join(backup_dir, f"report_{datetime.now():%H%M%S}.csv")
            out.to_csv(path, index=False)
            logger.info("Report: %s", path)


if __name__ == '__main__':
    main()
