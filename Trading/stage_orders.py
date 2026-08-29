"""Stage prepared Scalable orders and announce them on the phone.

Scheduler entry point for ``tradinglib.scalable_stage`` — a root-level script so
it follows the same command shape as the other jobs.

    python stage_orders.py /dry-run          # show what would be staged
    python stage_orders.py /buys /stops      # stage and push
    python stage_orders.py /test-push        # one push for the first open draft

This never places an order. It fills the order basket and sends a link to the
broker's own trade dialog, where you enter the values and confirm.
"""
import sys

from tradinglib.scalable_stage import main

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
