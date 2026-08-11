"""Command line entry point: `liveticker [options]`."""

import argparse
import logging
import sys
import time

from . import __version__
from .collector import Collector
from .config import load

RESTART_DELAY = 20


def build_parser():
    """Return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="liveticker",
        description="Scrape real-time quotes and stream them into a Trading app instance.")
    parser.add_argument('--config', default='', metavar='PATH',
                        help="ini file to read (default: $LIVETICKER_CONFIG, "
                             "~/.config/liveticker.ini, /etc/liveticker.ini)")
    parser.add_argument('--target', default='', metavar='URL',
                        help="app instance to stream to, e.g. http://192.168.1.10:8080")
    parser.add_argument('--api-key', default='', metavar='KEY',
                        help="shared secret; prefer LIVETICKER_API_KEY or the ini file")
    parser.add_argument('--fetch-type', choices=('indices', 'members'), default=None,
                        help="which symbol set to scrape")
    parser.add_argument('--page', default='', metavar='PATH', help="source page path")
    parser.add_argument('--headless', dest='headless', action='store_true', default=None,
                        help="run the browser without a display (default on a Pi)")
    parser.add_argument('--no-headless', dest='headless', action='store_false',
                        help="show the browser window (for debugging)")
    parser.add_argument('--anytime', action='store_true',
                        help="ignore the trading calendar — for testing outside hours")
    parser.add_argument('--dry-run', action='store_true',
                        help="scrape and log, but send nothing")
    parser.add_argument('--once', action='store_true',
                        help="run a single cycle, then exit")
    parser.add_argument('--log', default='', metavar='LEVEL',
                        help="logging level (DEBUG, INFO, WARNING …)")
    parser.add_argument('--version', action='version', version=f"liveticker {__version__}")
    return parser


def settings_from(args):
    """Merge CLI arguments over environment and config file."""
    overrides = {
        'target': args.target,
        'api_key': args.api_key,
        'fetch_type': args.fetch_type,
        'page': args.page,
        'log': args.log,
    }
    if args.headless is not None:
        overrides['headless'] = str(args.headless)
    return load(overrides=overrides, path=args.config)


def main(argv=None):
    """Run the collector until interrupted. Returns a process exit code."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    settings = settings_from(args)

    logging.basicConfig(
        level=getattr(logging, str(settings['log']).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s")
    logger = logging.getLogger("liveticker")
    if settings.get('config_file'):
        logger.info("configuration from %s", settings['config_file'])
    if not settings.get('api_key') and not args.dry_run:
        logger.error("no api_key configured — set LIVETICKER_API_KEY or use the ini file")
        return 2

    while True:
        collector = None
        try:
            collector = Collector(settings, ignore_schedule=args.anytime,
                                  dry_run=args.dry_run)
            collector.run(once=args.once)
            if args.once:
                return 0
        except KeyboardInterrupt:
            logger.info("stopped by user")
            return 0
        except Exception:
            logger.error("collector crashed — restarting in %ss", RESTART_DELAY, exc_info=True)
            time.sleep(RESTART_DELAY)
        finally:
            if collector is not None:
                collector.close()


if __name__ == "__main__":
    sys.exit(main())
