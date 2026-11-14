"""
log_edge_scores.py — scheduler entry point for the weekly Edge Scores history.

Thin root-level wrapper around ``tradinglib.edge_history.update`` (same command shape
as log_fear_greed.py). Appends one causal row per market and Friday to
``edge_scores.db`` / ``edge_weekly``; only Fridays missing since the last run are
computed, an empty database is back-filled.

    python log_edge_scores.py                  # add missing Fridays
    python log_edge_scores.py /weeks:260       # deeper initial back-fill
    python log_edge_scores.py /force /weeks:8  # recompute the last 8 weeks
    python log_edge_scores.py /key:DAX,GOLD    # only these markets
    python log_edge_scores.py /quiet           # no stdout summary
"""
import sys

from tradinglib import edge_history as eh

if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    from tradinglib import logging_config as lgc
    lgc.enable_logging(to_console=True, level='INFO')

    args = sys.argv[1:]
    low = [a.lower() for a in args]
    quiet = any(a in ('/quiet', '--quiet') for a in low)
    force = any(a in ('/force', '--force') for a in low)
    weeks, keys = None, None
    for a in args:
        if a.lower().startswith('/weeks:'):
            weeks = int(a.split(':', 1)[1])
        elif a.lower().startswith('/key:'):
            keys = [k.strip().upper() for k in a.split(':', 1)[1].split(',') if k.strip()]

    stats = eh.update(keys=keys, weeks=weeks, force=force)
    if not quiet:
        print(f"edge_scores: {stats['rows']} Zeile(n) fuer {stats['markets']} Markt/Maerkte "
              f"bis {stats['upto']} gespeichert."
              + (f" Fehler: {', '.join(stats['failed'])}" if stats['failed'] else ""))
    sys.exit(1 if stats['failed'] else 0)
