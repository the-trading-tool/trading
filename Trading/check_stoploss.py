"""
check_stoploss.py — CLI runner for the StopLossMonitor (trailing stop).

Run manually or schedule via schedserver / cron:

    python check_stoploss.py --user kurt --notify
    python check_stoploss.py --user kurt --execute --notify       # close on breach
    python check_stoploss.py --user kurt --broker ibkr --execute  # IBKR

Exit codes:
  0 — no breaches
  1 — one or more positions below trailing stop
  2 — runtime error
"""
import argparse
import sys
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def main():
    """Parse CLI args, run the ATR trailing-stop monitor, optionally close positions and notify."""
    parser = argparse.ArgumentParser(description='ATR trailing stop monitor')
    parser.add_argument('--atr-mult', type=float, default=2.5,
                        help='ATR multiplier: stop = HWM - N x ATR (default 2.5)')
    parser.add_argument('--user',     default='admin',
                        help='Username for config lookup (default: admin)')
    parser.add_argument('--broker',   default='alpaca', choices=['alpaca', 'ibkr'],
                        help='Broker (default: alpaca)')
    parser.add_argument('--mode',     default='paper', choices=['paper', 'live'],
                        help='Trading mode (default: paper)')
    parser.add_argument('--execute',  action='store_true', default=False,
                        help='Close breached positions immediately via broker. '
                             'Default: alert only, no orders sent.')
    parser.add_argument('--live',     action='store_true', default=False,
                        help='Shorthand for --mode live')
    parser.add_argument('--notify',   action='store_true', default=False,
                        help='Send Pushover notifications for breached positions')
    args = parser.parse_args()

    if args.live:
        args.mode = 'live'

    try:
        from tradinglib.system_config import SystemConfig
        from tradinglib.premium.trading_bridge import OrderLog, BrokerFactory, StopLossMonitor

        cfg    = SystemConfig(username=args.user)
        log    = OrderLog()
        broker = BrokerFactory.create(args.broker, cfg)

        if not broker.is_connected():
            logger.error('Broker not connected — check API credentials.')
            sys.exit(2)

        mon    = StopLossMonitor(log)
        trails = mon.update_trails(
            broker,
            mode=args.mode,
            broker_id=args.broker,
            atr_mult=args.atr_mult,
        )

        # ── Log all positions ─────────────────────────────────────────
        for r in trails:
            logger.info(
                '%s: current=%.2f HWM=%.2f trail=%.2f dist=%.1f%%  gain=%+.1f%%',
                r['ticker'], r['current_price'], r['high_water_mark'],
                r['trail_stop'], r.get('dist_to_trail', 0), r['gain_pct'],
            )

        triggered = [r for r in trails if r['breached']]

        if not triggered:
            logger.info('All %d position(s) above trailing stop.', len(trails))
            sys.exit(0)

        # ── Handle breached positions ────────────────────────────────
        executed = []
        for pos in triggered:
            logger.warning(
                '⚠️  TRAIL BREACHED %s: current=%.4f ≤ trail=%.4f '
                '(HWM=%.4f, entry=%.4f, gain=%+.1f%%)',
                pos['ticker'], pos['current_price'], pos['trail_stop'],
                pos['high_water_mark'], pos['entry_price'], pos['gain_pct'],
            )

            if args.execute:
                try:
                    result = broker.close_position(pos['broker_symbol'])
                    if result.status != 'error':
                        log.save(
                            mode=args.mode,
                            broker=args.broker,
                            strategy=pos.get('strategy', ''),
                            ticker=pos['ticker'],
                            broker_symbol=pos['broker_symbol'],
                            action='sell',
                            qty=pos.get('qty', 0),
                            signal_price=pos['current_price'],
                            order_id=result.order_id,
                            status=result.status,
                            signal_date=datetime.now().strftime('%Y-%m-%d'),
                            error_msg='trail_stop_triggered',
                        )
                        logger.info(
                            '✅ Closed %s — order %s (%s)',
                            pos['ticker'], result.order_id[:12], result.status,
                        )
                        executed.append(pos)
                    else:
                        logger.error(
                            'close_position %s failed: %s',
                            pos['ticker'], result.error_msg,
                        )
                except Exception as e:
                    logger.error('close_position %s: %s', pos['ticker'], e)
            else:
                logger.info(
                    'ℹ️  %s — alert only (use --execute to close automatically).',
                    pos['ticker'],
                )

        # ── Pushover ──────────────────────────────────────────────────
        if args.notify and triggered:
            try:
                from tradinglib.pushover_notifier import PushoverNotifier
                notifier = PushoverNotifier(username=args.user)
                for pos in triggered:
                    closed = pos in executed
                    notifier.send(
                        title=f"🛑 Trail Stop: {pos['ticker']}",
                        message=(
                            f"Current: {pos['current_price']:.2f}  "
                            f"Trail: {pos['trail_stop']:.2f}  "
                            f"HWM: {pos['high_water_mark']:.2f}\n"
                            f"Gain: {pos['gain_pct']:+.1f}%  "
                            f"ATR×{pos['atr_mult']:.1f}\n"
                            f"Action: {'✅ Closed' if closed else '⚠️ Alert only'}"
                        ),
                    )
            except Exception as e:
                logger.warning('Pushover notification failed: %s', e)

        sys.exit(1)   # breaches found

    except Exception as e:
        logger.error('check_stoploss failed: %s', e, exc_info=True)
        sys.exit(2)


if __name__ == '__main__':
    main()
