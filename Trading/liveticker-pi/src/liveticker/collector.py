"""The collection loop: scrape → validate → deliver → react to problems."""

import datetime as dt
import logging
import time

from . import symbols as symbol_sets
from .config import split_target
from .parsing import quote_age_minutes
from .scraper import Scraper, TABLE_LAYOUTS, DriverTimeout
from .stream import StreamUnavailable, TickStreamClient, websocket_url

logger = logging.getLogger(__name__)

MIN_COVERAGE = 0.5          # below this share of resolved symbols the page counts as broken
STALL_CYCLES = 5            # unchanged cycles before the page is treated as frozen
MAX_JUMP_PCT = 10.0         # a bigger single-step move must be confirmed twice
MAX_QUOTE_AGE_MIN = 90      # quote timestamps older than this are reported as stale
IDLE_SLEEP = 300            # nap length while the markets are closed (no requests)


class Collector:
    """Scrapes quotes during trading hours and streams the changes to the app."""

    def __init__(self, settings, ignore_schedule=False, dry_run=False):
        """Store the effective settings; browsers are opened on demand."""
        self.settings = settings
        self.ignore_schedule = ignore_schedule
        self.dry_run = dry_run

        self.fetch_type = settings.get('fetch_type', 'indices')
        self.symbols = symbol_sets.for_type(self.fetch_type)
        self.layout = TABLE_LAYOUTS['indices' if self.fetch_type == 'indices' else 'members']
        self.page = settings.get('page') or '/realtimekurse'
        self.transport = settings.get('transport', 'auto')
        (self.protocol, self.host,
         self.port, self.base_path) = split_target(settings.get('target'))

        self.scraper = None
        self.stream = None
        self.last_sent = {}
        self.pending = {}
        self.stale_reported = set()
        self.stall_cycles = 0

        logger.info("streaming ticks to %s%s%s%s (transport=%s)", self.protocol,
                    self.host, self.port, self.base_path, self.transport)
        if ignore_schedule:
            logger.warning("schedule override active — collecting outside trading hours")
        if dry_run:
            logger.warning("dry run — quotes are logged, nothing is sent")

    # -- lifecycle ------------------------------------------------------------

    def ensure_scraper(self):
        """Open the browser on demand — never while the markets are closed."""
        if self.scraper is None:
            source = self.settings.get('source_host', 'finanzen.net')
            logger.info("opening the source page https://%s%s", source, self.page)
            self.scraper = Scraper(
                # The source site is not the target app — it is always the
                # quote page, over https.
                protocol='https://',
                url=source,
                page=self.page, layout=self.layout, profile='source',
                headless=bool(self.settings.get('headless', True)),
                binary=self.settings.get('chrome_binary', ''),
                driver_path=self.settings.get('chromedriver', ''),
                profile_root=self.settings.get('profile_dir', ''),
                user_agent=self.settings.get('user_agent', ''),
                source_timezone=self.settings.get('source_timezone', ''))
        return self.scraper

    def close(self):
        """Shut down browser and websocket while the markets are closed."""
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.scraper is not None:
            self.scraper.quit()
            self.scraper = None
        # A fresh session re-sends every quote once, so change detection cannot
        # suppress the first ticks of the day.
        self.last_sent = {}
        self.pending = {}
        self.stale_reported = set()

    # -- schedule -------------------------------------------------------------

    def is_trading_time(self, now=None):
        """True inside the configured collection window (weekdays only)."""
        now = now or dt.datetime.now()
        return (now.weekday() <= 4
                and self.settings['start_time'] <= now.time() <= self.settings['end_time'])

    def in_session(self, now=None):
        """True when the collector may query the website."""
        return True if self.ignore_schedule else self.is_trading_time(now)

    def seconds_until_session(self, now=None):
        """Seconds until the next session starts, capped so the loop stays responsive."""
        now = now or dt.datetime.now()
        candidate = dt.datetime.combine(now.date(), self.settings['start_time'])
        while candidate <= now or candidate.weekday() > 4:
            candidate = dt.datetime.combine(candidate.date() + dt.timedelta(days=1),
                                            self.settings['start_time'])
        return min(IDLE_SLEEP, max(1.0, (candidate - now).total_seconds()))

    # -- delivery -------------------------------------------------------------

    def deliver(self, quotes):
        """Send the quotes to the app. Returns True when they were accepted."""
        if self.dry_run:
            logger.info("dry run — would send %s quotes: %s",
                        len(quotes), self.price_line(quotes))
            return True

        body = dict(quotes)
        body['api_key'] = self.settings.get('api_key', '')
        if not body['api_key']:
            logger.error("no api_key configured — the app will reject every payload")

        if self.transport in ('auto', 'ws'):
            accepted = self._send_via_websocket(body, len(quotes))
            if accepted is not None:
                return accepted
            # This package is websocket-only on purpose: the browser fallback
            # needs a second Chrome, which is exactly what a Pi cannot spare.
            logger.warning("websocket delivery failed — retrying next cycle")
            return False
        logger.error("transport %r is not available in this package (websocket only)",
                     self.transport)
        return False

    def _send_via_websocket(self, body, expected):
        """Send over the Streamlit websocket. Returns None when unavailable."""
        try:
            if self.stream is None:
                self.stream = TickStreamClient(
                    websocket_url(self.protocol, self.host, self.port, self.base_path))
            stored, total = self.stream.send(body)
        except StreamUnavailable as exc:
            logger.warning("websocket transport unusable: %s", exc)
            self.stream = None
            return None

        if stored is None:
            logger.warning("app did not confirm the delivery — treating it as failed")
            return False
        if stored < 0:
            logger.error("app rejected the payload (check the api_key)")
            return False
        if stored < total or total < expected:
            logger.warning("app stored only %s of %s sent quotes", stored, expected)
        logger.debug("sent %s quotes, app stored %s", expected, stored)
        return True

    # -- validation -----------------------------------------------------------

    def validate(self, quotes):
        """Filter scraped quotes down to values that are safe to store."""
        accepted, issues = {}, []
        for symbol, quote in quotes.items():
            previous = self.last_sent.get(symbol)
            if previous:
                change = abs(quote["price"] - previous["price"]) / previous["price"] * 100
                if change > MAX_JUMP_PCT:
                    confirm = self.pending.get(symbol)
                    if not confirm or abs(confirm - quote["price"]) > 1e-9:
                        self.pending[symbol] = quote["price"]
                        issues.append(f"{symbol}: {change:.1f}% jump to {quote['price']} "
                                      f"— waiting for confirmation")
                        continue
                    logger.info("%s: %.1f%% jump confirmed at %s", symbol, change, quote["price"])
            self.pending.pop(symbol, None)

            # Report staleness once per episode — a closed market would
            # otherwise warn on every single cycle.
            age = quote_age_minutes(quote["time"])
            if age is not None and age > MAX_QUOTE_AGE_MIN:
                if symbol not in self.stale_reported:
                    self.stale_reported.add(symbol)
                    issues.append(f"{symbol}: quote is {age:.0f} min old")
            else:
                self.stale_reported.discard(symbol)
            accepted[symbol] = quote
        return accepted, issues

    def changed_quotes(self, quotes):
        """Return only the quotes whose price or quote time differs from the last send."""
        return {symbol: quote for symbol, quote in quotes.items()
                if self.last_sent.get(symbol) != quote}

    def price_line(self, quotes):
        """Format a one-line summary of the given quotes for the log."""
        return " - ".join(
            f'{self.symbols.get(symbol, {}).get("name", symbol)} '
            f'{quote["price"]} @ {quote["time"]}'
            for symbol, quote in quotes.items())

    # -- cycle ----------------------------------------------------------------

    def cycle(self):
        """Run one collection cycle."""
        scraper = self.ensure_scraper()

        # The consent wall can reappear at any time and does NOT hide the table
        # from the DOM — scraping alone would never notice it.
        scraper.dismiss_overlays()

        quotes, issues = scraper.scrape(self.symbols)
        for issue in issues:
            logger.warning("page issue: %s", issue)

        if len(quotes) / max(len(self.symbols), 1) < MIN_COVERAGE:
            scraper.recover(f"only {len(quotes)}/{len(self.symbols)} symbols readable")
            return

        accepted, quality_issues = self.validate(quotes)
        for issue in quality_issues:
            logger.warning("data issue: %s", issue)

        changed = self.changed_quotes(accepted)
        if not changed:
            self.stall_cycles += 1
            logger.info("no quote changed (%s/%s cycles)", self.stall_cycles, STALL_CYCLES)
            if self.stall_cycles >= STALL_CYCLES:
                self.stall_cycles = 0
                scraper.recover("quotes frozen")
            return

        self.stall_cycles = 0
        if self.deliver(dict(changed)):
            # Only remember what actually reached the app — a failed send must
            # be retried on the next cycle, not swallowed by change detection.
            self.last_sent.update(changed)
            scraper.reset_recovery()
            logger.info("%s", self.price_line(changed))
        else:
            logger.warning("%s quotes could not be delivered — retrying next cycle",
                           len(changed))

    def run(self, once=False):
        """Collect until the day rolls over (or for a single cycle)."""
        start_day = dt.date.today()
        idle_logged = False
        cycle_seconds = self.settings.get('cycle_seconds', 20)

        while True:
            now = dt.datetime.now()
            if now.date() != start_day:
                logger.info("day changed — restarting collector")
                return

            if not self.in_session(now):
                if self.scraper is not None or self.stream is not None:
                    logger.info("markets are closed — closing the connections")
                    self.close()
                delay = self.seconds_until_session(now)
                if not idle_logged:
                    logger.info("idle until the next session (checking every %ss)", int(delay))
                    idle_logged = True
                time.sleep(delay)
                continue

            idle_logged = False
            try:
                self.cycle()
            except KeyboardInterrupt:
                raise
            except DriverTimeout:
                logger.error("driver timed out — the browser was killed")
                self.scraper = None
            except Exception:
                logger.error("collection cycle failed", exc_info=True)
                if self.scraper is None:
                    return
                try:
                    self.scraper.recover("cycle raised")
                except Exception:
                    logger.error("recovery failed", exc_info=True)
                    return

            if once:
                logger.info("single cycle finished")
                return

            elapsed = (dt.datetime.now() - now).total_seconds()
            time.sleep(max(1.0, cycle_seconds - elapsed))
