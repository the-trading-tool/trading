"""
recalc_correlation.py — scheduler entry point for the cross-asset correlation index.

Thin root-level wrapper around ``tradinglib.correlation_index.run`` so the job can
follow the same ``"<python>" "<root>\\<script>.py"`` command shape as the other
scheduled scripts (get_asset_data.py, asset_perf2.py, warm_market_stress.py).

Run manually or from the scheduler:

    python recalc_correlation.py            # recompute + store correlation_index.db
    python recalc_correlation.py /quiet     # no stdout summary
"""
import sys

from tradinglib import correlation_index as ci

if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    from tradinglib import logging_config as lgc
    lgc.enable_logging(to_console=True, level='INFO')
    _quiet = any(a.lower() in ('/quiet', '--quiet') for a in sys.argv[1:])
    ci.run(quiet=_quiet)
