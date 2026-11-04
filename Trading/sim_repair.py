"""Repair simulation rows on a stale price scale (unadjusted splits).

    python sim_repair.py /scan [/tickers:A,B] [/db:asset_simulation_2025.db]
    python sim_repair.py /compute [/workers:2]
    python sim_repair.py /merge [/dry] [/force]
    python sim_repair.py /auto [/workers:2]

/scan     read only; writes the plan to <db dir>/sim_repair/plan.csv
/compute  recomputes the plan into <db dir>/sim_repair/stage_*.db (any time)
/merge    writes the staged rows into the production DBs -- only when no
          asset_perf2 is running or due within 30 minutes (/force overrides,
          /dry only reports)
/auto     scan, compute what is not staged yet, merge if the window allows;
          the daily job. Staged rows that could not be merged wait for the next run.

See tradinglib/sim_repair.py.
"""
import logging
import os
import sys

import pandas as pd

from tradinglib import asset_status, logging_config, sim_repair as sr

logger = logging.getLogger('sim_repair')


def _args(argv):
    out = {}
    for a in argv[1:]:
        if not a.startswith('/'):
            continue
        key, _, val = a[1:].partition(':')
        out[key.lower()] = val if val else True
    return out


def _plan_path():
    return os.path.join(sr.stage_dir(), 'plan.csv')


def do_scan(args) -> pd.DataFrame:
    tickers = [t for t in str(args.get('tickers', '')).split(',') if t] or None
    dbs = [d for d in str(args.get('db', '')).split(',') if d] or None
    plan = sr.scan(tickers=tickers, dbs=dbs)
    plan.to_csv(_plan_path(), index=False)
    if plan.empty:
        logger.info('scan: no stale rows')
    else:
        by_t = plan.groupby('ticker').agg(stale=('stale', 'sum'), factor=('factor', 'median'),
                                          dbs=('db', 'nunique'))
        logger.info('scan: %d stale rows in %d tickers, %d (db, year) items -> %s',
                    int(plan['stale'].sum()), plan['ticker'].nunique(), len(plan), _plan_path())
        for tk, r in by_t.sort_values('stale', ascending=False).iterrows():
            logger.info('  %-10s %6d stale rows  factor %8.3f  %d db(s)', tk, r.stale, r.factor, r.dbs)
    return plan


def do_compute(args, plan=None):
    if plan is None:
        plan = pd.read_csv(_plan_path()) if os.path.exists(_plan_path()) else do_scan(args)
    if plan.empty:
        logger.info('compute: nothing to do')
        return []
    if args.get('tickers'):
        plan = plan[plan['ticker'].isin(str(args['tickers']).split(','))]
    workers = int(args.get('workers', 2) or 2)
    logger.info('compute: %d tickers (all their DBs and years), %d workers',
                plan['ticker'].nunique(), workers)
    return sr.compute(plan, workers=workers, refresh=not args.get('norefresh'),
                      progress=lambda r: logger.info('  %s: %s', r.get('ticker'),
                                                     r.get('error') or r.get('rows')))


def do_merge(args):
    rep = sr.merge(force=bool(args.get('force')), dry=bool(args.get('dry')))
    for tk, why in (rep.get('rejected') or {}).items():
        logger.info('  rejected %s: %s', tk, '; '.join(why))
    if not rep.get('merged'):
        logger.info('merge: not done -- %s', rep.get('reason') or rep.get('plan'))
    else:
        logger.info('merge: backup in %s', rep['backup'])
        for name, r in rep['dbs'].items():
            logger.info('  %s: %s', name, r)
    return rep


def main(argv=None):
    args = _args(argv or sys.argv)
    logging_config.configure_logging(to_console=True, level='INFO', logfile=None)
    if args.get('scan'):
        do_scan(args)
    elif args.get('compute'):
        do_compute(args)
    elif args.get('merge'):
        do_merge(args)
    elif args.get('auto'):
        plan = do_scan(args)
        if not plan.empty:
            done = sr.staged()
            skip = sr.blocked_tickers()         # reloaded series rejected recently
            skip |= asset_status.inactive_tickers()     # delisted/renamed: no data to reload
            if not done.empty:                  # computed, waiting for a window
                skip |= set(done['ticker'])
            plan = plan[~plan['ticker'].isin(skip)]
            do_compute(args, plan)
        if not sr.staged().empty:
            do_merge(args)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
