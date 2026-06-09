"""
run_agent.py — CLI runner for the TradingAgent.

Designed to be called every 15 minutes from a scheduler or cron job.
The agent itself decides whether to run buy cycle (once/day) or sell-only.

Examples
--------
Dry run (safe, no orders):
    python run_agent.py

Paper trading, live orders:
    python run_agent.py --execute

With Pushover notifications:
    python run_agent.py --execute --notify

Custom timing (EU market, buy 1h after 9:00 CET):
    python run_agent.py --execute --region eu --buy-offset 60

Force buy cycle now (for testing):
    python run_agent.py --execute --force-buy

Cron example (every 15 min, Mon–Fri, 14:00–22:00 UTC = 10:00–18:00 ET):
    */15 14-22 * * 1-5 /path/to/venv/python /path/to/run_agent.py --execute --notify
"""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('run_agent')


def main():
    """Parse CLI args, run the TradingAgent buy/sell cycle, and optionally send Pushover alerts."""
    parser = argparse.ArgumentParser(description='TradingAgent runner')
    parser.add_argument(
        '--execute', action='store_true', default=False,
        help='Submit real orders (default: dry run — log only)',
    )
    parser.add_argument(
        '--user', default='admin',
        help='Username for config/strategy lookup (default: admin)',
    )
    parser.add_argument(
        '--mode', default='paper', choices=['paper', 'live'],
        help='Trading mode (default: paper)',
    )
    parser.add_argument(
        '--broker', default='alpaca', choices=['alpaca', 'ibkr'],
        help='Broker (default: alpaca)',
    )
    parser.add_argument(
        '--region', default='us', choices=['us', 'eu'],
        help='Market region for open-time calculation (default: us)',
    )
    parser.add_argument(
        '--buy-offset', type=int, default=60, metavar='MINUTES',
        help='Minutes after market open to trigger buy cycle (default: 60)',
    )
    parser.add_argument(
        '--buy-window', type=int, default=20, metavar='MINUTES',
        help='Width of the buy window in minutes (default: 20)',
    )
    parser.add_argument(
        '--force-buy', action='store_true', default=False,
        help='Force buy cycle regardless of time/date (testing only)',
    )
    parser.add_argument(
        '--index', action='append', default=[], metavar='INDEX',
        dest='index_filter',
        help=(
            'Only execute buy signals for this index. '
            'Can be repeated: --index SPX --index GDAXI. '
            'Accepts with or without leading ^. '
            'Default: all indices.'
        ),
    )
    parser.add_argument(
        '--atr-mult', type=float, default=2.0, metavar='N',
        dest='atr_mult',
        help='ATR multiplier for stop-loss on buy orders: stop = price − N × ATR (default 2.0)',
    )
    parser.add_argument(
        '--trading-days-backward', type=int, default=0, metavar='N',
        dest='trading_days_backward',
        help=(
            'Skip buy signals whose entry_date is older than N trading days '
            '(Mon–Fri, no holidays). '
            '0 = no filter (default). '
            'Example: --trading-days-backward 3 only executes signals from the '
            'last 3 trading days.'
        ),
    )
    parser.add_argument(
        '--notify', action='store_true', default=False,
        help='Send Pushover notifications for executed orders',
    )
    args = parser.parse_args()

    dry_run = not args.execute

    try:
        from tradinglib.system_config import SystemConfig
        from tradinglib.premium.trading_bridge import OrderLog, BrokerFactory
        from tradinglib.premium.trading_agent import TradingAgent

        cfg = SystemConfig(username=args.user)

        # CLI flags override sys_config values; sys_config is the fallback.
        broker_id  = args.broker  or cfg.get_value('agent_broker',     'alpaca') or 'alpaca'
        region     = args.region  or cfg.get_value('agent_region',     'us')     or 'us'
        buy_offset = args.buy_offset if args.buy_offset != 60 \
                     else int(cfg.get_value('agent_buy_offset', 60) or 60)
        agent_dry  = bool(cfg.get_value('agent_dry_run', True))
        # --execute flag always overrides the stored dry_run setting
        if args.execute:
            agent_dry = False
        dry_run = agent_dry
        mode = 'live' if broker_id == 'ibkr' else 'paper'

        log    = OrderLog()
        broker = BrokerFactory.create(broker_id, cfg)

        if not broker.is_connected():
            logger.error('Broker not connected — check API credentials.')
            sys.exit(2)

        agent  = TradingAgent(cfg, log, broker_id=broker_id)
        result = agent.run(
            broker,
            mode                   = mode,
            broker_id              = broker_id,
            dry_run                = dry_run,
            username               = args.user,
            buy_offset_minutes     = buy_offset,
            window_minutes         = args.buy_window,
            region                 = region,
            force_buy              = args.force_buy,
            trading_days_backward  = args.trading_days_backward,
            index_filter           = args.index_filter or None,
            atr_mult               = args.atr_mult,
        )

        cycle    = result['cycle']
        sells    = result['sell_actions']
        buys     = result['buy_actions']
        executed = [a for a in sells + buys if a.get('executed')]

        logger.info(
            'Cycle=%s  sells=%d  buys=%d  executed=%d  dry_run=%s',
            cycle, len(sells), len(buys), len(executed), dry_run,
        )

        if dry_run and (sells or buys):
            logger.info('DRY RUN — no orders submitted. Use --execute to trade.')

        # ── Pushover notifications ────────────────────────────────────
        if args.notify and executed:
            try:
                from tradinglib.pushover_notifier import PushoverNotifier
                notifier = PushoverNotifier(username=args.user)
                for act in executed:
                    notifier.send(
                        title=f"{'🟢 BUY' if act['action'] == 'buy' else '🔴 SELL'} "
                              f"{act['ticker']}",
                        message=(
                            f"Strategy: {act.get('strategy', '?')}\n"
                            f"Qty: {act.get('qty', '?')}\n"
                            f"Price: {act.get('price', '?')}\n"
                            f"Order: {act.get('order_id', '?')[:12]}"
                        ),
                    )
                logger.info('Pushover: sent %d notification(s).', len(executed))
            except Exception as e:
                logger.warning('Pushover notification failed: %s', e)

        sys.exit(0)

    except Exception as e:
        logger.error('run_agent failed: %s', e, exc_info=True)
        sys.exit(2)


if __name__ == '__main__':
    main()
