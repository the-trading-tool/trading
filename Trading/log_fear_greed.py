"""
log_fear_greed.py — scheduler entry point for the Fear & Greed history.

Thin root-level wrapper around ``tradinglib.fear_greed.log_history`` so the job can
follow the same ``"<python>" "<root>\\<script>.py"`` command shape as the other
scheduled scripts (recalc_correlation.py, warm_market_stress.py, …).

Computes the composite index per index and appends one daily row per index to
``fear_greed.db`` / ``fg_history`` (upsert on (date, index)).

Run manually or from the scheduler:

    python log_fear_greed.py                 # log all default indices
    python log_fear_greed.py /index:^SPX     # log a single index
    python log_fear_greed.py /quiet          # no stdout summary
"""
import sys

from tradinglib import fear_greed as fg

if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    from tradinglib import logging_config as lgc
    lgc.enable_logging(to_console=True, level='INFO')

    args = sys.argv[1:]
    quiet = any(a.lower() in ('/quiet', '--quiet') for a in args)
    index = None
    for a in args:
        if a.lower().startswith('/index:'):
            index = a.split(':', 1)[1]

    n = fg.log_history([index] if index else None)
    if not quiet:
        print(f"fear_greed: {n} Zeile(n) in fg_history geloggt.")
