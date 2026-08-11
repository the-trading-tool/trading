"""Reading the quote tables and getting the overlays out of the way.

The page throws three things at an automated reader, all verified against the
live site:

1. the Contentpass wall — a cross-origin iframe WITHOUT id or class
   (cp.finanzen.net/first-layer), full viewport, z-index 2147483647, with a
   scroll lock. The quote table stays readable behind it, so "the table is
   there" is NOT proof that the page is usable;
2. a Google sign-in prompt — not the stock One Tap iframe but the site's own
   `div#onetap-container` with `button#pseudo-google-one-tap-close`;
3. a push-notification opt-in whose decline button ("SPÄTER ENTSCHEIDEN") may be
   upper-cased by CSS, which XPath does not see.

Buttons that would cost money or subscribe to something are never pressed —
see FORBIDDEN_CLICK_TEXT.
"""

import contextlib
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from .browser import Browser
from .parsing import parse_number, parse_time, to_timestamp

logger = logging.getLogger(__name__)

CALL_TIMEOUT = 45           # hard deadline for a single WebDriver command
QUIT_TIMEOUT = 15           # deadline for quit() before the process is killed
PAGE_READY_TIMEOUT = 25     # how long to poll for consent dialog / quote table

# Where the quote columns sit, per page type.
TABLE_LAYOUTS = {
    'indices': {
        'tbody_xpath': '//h2[contains(text(),"{headline}")]/../div/table/tbody',
        'name_col': 0,
        'price_col': 1,
        'time_col': 5,
        'probe_headline': 'Indikation auf Indizes',
    },
    'members': {
        'tbody_xpath': '//th[contains(text(),"{headline}")]/../../../../table/tbody',
        'name_col': 1,
        'price_col': 3,
        'time_col': 8,
        'probe_headline': 'Name',
    },
}

# --- click policy ------------------------------------------------------------

FORBIDDEN_CLICK_TEXT = ('abonnier', 'abo ', 'einloggen', 'login', 'anmelden',
                        'registrier', 'kaufen', 'kostenpflichtig', 'zahlungs',
                        'jetzt testen', 'weiter mit google',
                        'aktivieren', 'zulassen', 'erlauben', 'allow', 'enable',
                        'subscribe', 'benachrichtigung', 'push')

# XPath 1.0 has no lower-case(); a label may be upper-cased by CSS.
_XPATH_LOWER = ("translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜ', "
                "'abcdefghijklmnopqrstuvwxyzäöü')")

CONSENT_BUTTONS = [
    '//button[normalize-space()="Einwilligen & weiter"]',
    '//*[@role="button"][normalize-space()="Einwilligen & weiter"]',
    '//a[normalize-space()="Einwilligen & weiter"]',
    '//button[contains(., "Einwilligen")]',
    '//*[@role="button"][contains(., "Einwilligen")]',
    '//*[@title="Einwilligen & weiter"]',
    '//button[normalize-space()="Alle akzeptieren"]',
    '//button[contains(., "Alle akzeptieren")]',
    '//button[normalize-space()="Akzeptieren"]',
    '//button[contains(., "Accept all")]',
]

# Deliberately without "ablehnen": on the Contentpass wall that word belongs to
# "Ablehnen & abonnieren", the paid option.
DISMISS_BUTTONS = [
    f'//button[contains({_XPATH_LOWER}, "später entscheiden")]',
    f'//*[@role="button"][contains({_XPATH_LOWER}, "später entscheiden")]',
    f'//a[contains({_XPATH_LOWER}, "später entscheiden")]',
    f'//button[contains({_XPATH_LOWER}, "entscheiden")]',
    f'//button[contains({_XPATH_LOWER}, "nicht jetzt")]',
    f'//button[contains({_XPATH_LOWER}, "nicht interessiert")]',
    f'//button[contains({_XPATH_LOWER}, "maybe later")]',
    f'//button[contains({_XPATH_LOWER}, "not now")]',
]

CLOSE_BUTTONS = [
    '//button[@title="Close"]',
    '//button[@aria-label="Close"]',
    '//*[@aria-label="Schließen"]',
    '//button[contains(@class,"close")]',
]

# --- page scripts ------------------------------------------------------------

_TABLE_JS = """
const tb = document.evaluate(arguments[0], document, null,
                             XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
if (!tb) { return null; }
return Array.from(tb.rows).map(r => Array.from(r.cells).map(c => (c.innerText || '').trim()));
"""

_HAS_TABLE_JS = """
const tb = document.evaluate(arguments[0], document, null,
                             XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
return !!(tb && tb.rows && tb.rows.length);
"""

_OVERLAY_HELPERS = """
function _visible(el) {
  const s = getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity || '1') < 0.1) return false;
  const r = el.getBoundingClientRect();
  return r.width > 1 && r.height > 1;
}
function _floating(el) {
  const s = getComputedStyle(el);
  return s.position === 'fixed' || s.position === 'sticky';
}
function _area(el) {
  const r = el.getBoundingClientRect();
  return Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)) *
         Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
}
function _describe(el) {
  return (el.tagName || '?').toLowerCase() +
         (el.id ? '#' + el.id : '') +
         (el.className && typeof el.className === 'string'
            ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
}
"""

_OVERLAY_JS = _OVERLAY_HELPERS + """
const CONSENT_HINTS = ['first-layer', 'cp.finanzen.net', 'contentpass', 'sp_message',
                       'sourcepoint', 'consent', 'cmp', 'usercentrics',
                       'didomi', 'onetrust', 'gdpr'];
const SIGNIN_SELECTORS = ['#onetap-container', '#pseudo-google-one-tap-close',
                          'iframe[src*="accounts.google.com/gsi"]',
                          'div[id^="credential_picker"]'];
const DISMISS = ['später entscheiden', 'spaeter entscheiden', 'nicht jetzt',
                 'nicht interessiert', 'maybe later', 'not now', 'no thanks',
                 'nein danke'];
const report = {consent: null, signin: null, prompt: null, overlays: [],
                scrollLocked: false, covered: false, blocked: false};

for (const sel of SIGNIN_SELECTORS) {
  const el = document.querySelector(sel);
  if (el && _visible(el)) { report.signin = _describe(el); break; }
}

const frames = Array.from(document.querySelectorAll('iframe'));
for (let i = 0; i < frames.length; i++) {
  const f = frames[i];
  if (!_visible(f)) continue;
  const key = ((f.id || '') + ' ' + (f.title || '') + ' ' + (f.src || '')).toLowerCase();
  const hint = CONSENT_HINTS.find(h => key.includes(h));
  const z = parseInt(getComputedStyle(f).zIndex || '0', 10) || 0;
  const huge = _area(f) > innerWidth * innerHeight * 0.5;
  if (hint || (huge && z >= 1000000)) {
    report.consent = {frame: f.id || '', index: i, src: (f.src || '').slice(0, 120),
                      hint: hint || 'geometry', area: Math.round(_area(f))};
    break;
  }
}

for (const el of document.querySelectorAll('button,[role="button"],a')) {
  if (!_visible(el)) continue;
  const label = (el.innerText || '').trim().toLowerCase();
  if (!label || label.length > 60) continue;
  if (!DISMISS.some(d => label.includes(d))) continue;
  let host = el, floating = false;
  for (let i = 0; host && i < 8; host = host.parentElement, i++) {
    if (_floating(host)) { floating = true; break; }
  }
  if (!floating) continue;
  report.prompt = {el: _describe(el), label: label.slice(0, 40)};
  break;
}

const seen = new Set();
for (const el of document.querySelectorAll('body *')) {
  if (!_floating(el) || !_visible(el)) continue;
  const z = parseInt(getComputedStyle(el).zIndex || '0', 10) || 0;
  const area = _area(el);
  if (area < innerWidth * innerHeight * 0.12 && z < 1000) continue;
  const key = _describe(el);
  if (seen.has(key)) continue;
  seen.add(key);
  report.overlays.push({el: key, z: z, area: Math.round(area)});
  if (report.overlays.length >= 8) break;
}

const bodyStyle = getComputedStyle(document.body);
report.scrollLocked = bodyStyle.overflow === 'hidden' || bodyStyle.position === 'fixed';
const centre = document.elementFromPoint(Math.round(innerWidth / 2), Math.round(innerHeight / 2));
if (centre) {
  for (let el = centre; el && el !== document.body; el = el.parentElement) {
    if (_floating(el) && _area(el) > innerWidth * innerHeight * 0.2) { report.covered = true; break; }
  }
}

report.blocked = !!report.consent || !!report.signin || !!report.prompt ||
                 report.covered || report.scrollLocked ||
                 report.overlays.some(o => o.area > innerWidth * innerHeight * 0.25);
return report;
"""

_ONETAP_JS = """
const done = [];
const own = document.querySelector('#pseudo-google-one-tap-close');
if (own) { try { own.click(); done.push('onetap-close-button'); } catch (e) {} }
try {
  if (window.google && google.accounts && google.accounts.id) {
    google.accounts.id.cancel();
    done.push('api-cancel');
  }
} catch (e) {}
for (const sel of ['#onetap-container', 'div[id^="credential_picker"]',
                   'iframe[src*="accounts.google.com/gsi"]']) {
  document.querySelectorAll(sel).forEach(el => {
    const host = el.closest('#onetap-container, div[id^="credential_picker"]') || el;
    try { host.remove(); done.push(sel); } catch (e) {}
  });
}
return done;
"""

_CLOSE_JS = _OVERLAY_HELPERS + """
const LABELS = ['close', 'schließen', 'schliessen', 'close player', 'dismiss',
                'nicht interessiert', 'no thanks', 'nein danke', 'überspringen', 'skip ad'];
const DISMISS = ['später entscheiden', 'spaeter entscheiden', 'nicht jetzt',
                 'nicht interessiert', 'maybe later', 'not now', 'no thanks',
                 'nein danke'];
const FORBIDDEN = ['abonnier', 'einloggen', 'login', 'anmelden', 'registrier',
                   'kaufen', 'kostenpflichtig', 'zahlungs', 'jetzt testen',
                   'weiter mit google', 'aktivieren', 'zulassen', 'erlauben',
                   'allow', 'enable', 'subscribe', 'benachrichtigung', 'push'];
const SYMBOLS = ['×', '✕', '✖', 'x'];
const clicked = [];

function _forbidden(el) {
  const text = ((el.innerText || '') + ' ' +
                (el.getAttribute('aria-label') || '') + ' ' +
                (el.getAttribute('title') || '')).toLowerCase();
  return FORBIDDEN.some(f => text.includes(f));
}

function _closeish(el) {
  const txt = (el.innerText || '').trim().toLowerCase();
  if (txt && txt.length <= 60 && DISMISS.some(d => txt.includes(d))) return true;
  const label = ((el.getAttribute('aria-label') || '') + ' ' +
                 (el.getAttribute('title') || '') + ' ' +
                 (typeof el.className === 'string' ? el.className : '') + ' ' +
                 (el.id || '')).toLowerCase();
  if (LABELS.some(l => label.includes(l))) return true;
  if (/(^|[-_ ])(close|dismiss)([-_ ]|$)/.test(label)) return true;
  return txt.length <= 2 && SYMBOLS.includes(txt);
}

for (const el of document.querySelectorAll('button,[role="button"],a,span[class*="close"],div[class*="close"]')) {
  if (clicked.length >= 6) break;
  if (!_visible(el) || _forbidden(el) || !_closeish(el)) continue;
  let host = el, floating = false;
  for (let i = 0; host && i < 8; host = host.parentElement, i++) {
    if (_floating(host)) { floating = true; break; }
  }
  if (!floating) continue;
  try { el.click(); clicked.push(_describe(el)); } catch (e) {}
}
return clicked;
"""


class DriverTimeout(Exception):
    """A WebDriver command did not return within its deadline."""


class Scraper:
    """Page lifecycle, overlay handling and table reads.

    Every driver command runs behind a deadline (see `call`) — a hung renderer
    or a dead driver ends the cycle instead of freezing the collector.
    """

    def __init__(self, url='finanzen.net', page='/realtimekurse', protocol='https://',
                 layout=None, profile='source', headless=False, binary='',
                 driver_path='', profile_root='', user_agent='',
                 source_timezone='', call_timeout=CALL_TIMEOUT,
                 autostart=True):
        """Configure the scraper and, unless told otherwise, open the page."""
        self.protocol = protocol
        self.url = url
        self.page = page
        self.layout = layout or TABLE_LAYOUTS['indices']
        self.call_timeout = call_timeout
        self.source_timezone = source_timezone
        self.recovery_step = 0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='webdriver')
        self.browser = Browser(profile=profile, headless=headless, binary=binary,
                               driver_path=driver_path, profile_root=profile_root,
                               user_agent=user_agent)
        self.browser.start()
        if autostart:
            self.setup_page()

    # -- driver plumbing ------------------------------------------------------

    @property
    def d(self):
        """The underlying WebDriver."""
        return self.browser.d

    def call(self, label, func, *args, timeout=None):
        """Run a WebDriver command with a hard deadline.

        Selenium blocks on an HTTP call to the driver; if the browser hangs,
        that call never returns. The command therefore runs in a worker thread
        and, on timeout, the driver process is killed so the blocked call fails.
        """
        pool = self._pool
        future = pool.submit(func, *args)
        try:
            return future.result(timeout=timeout or self.call_timeout)
        except FutureTimeout:
            logger.error("driver command '%s' timed out after %ss — killing the browser",
                         label, timeout or self.call_timeout)
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='webdriver')
            pool.shutdown(wait=False)
            self.browser.kill()
            raise DriverTimeout(label)

    def js(self, script, *args, timeout=None, default=None, label='script'):
        """Execute JavaScript in the page; returns `default` on any page-side error."""
        try:
            return self.call(label, self.d.execute_script, script, *args, timeout=timeout)
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("script '%s' failed", label, exc_info=True)
            return default

    @contextlib.contextmanager
    def no_implicit_wait(self):
        """Set the implicit wait to 0 for the block — element misses must be free."""
        try:
            self.d.implicitly_wait(0)
        except Exception:
            logger.debug("could not clear the implicit wait", exc_info=True)
        try:
            yield
        finally:
            try:
                self.d.implicitly_wait(self.browser.def_to)
            except Exception:
                logger.debug("could not restore the implicit wait", exc_info=True)

    def wait_until(self, predicate, timeout=15, interval=0.5, label=''):
        """Poll `predicate` until it returns truthy or the timeout expires."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = predicate()
            except DriverTimeout:
                raise
            except Exception:
                result = None
            if result:
                return result
            if time.monotonic() >= deadline:
                logger.debug("wait_until('%s') gave up after %ss", label, timeout)
                return None
            time.sleep(interval)

    # -- navigation -----------------------------------------------------------

    def target_url(self, path='', page=''):
        """Build the absolute URL for a request."""
        return f'{self.protocol}{self.url}{page or self.page}{path}'

    def setup_page(self):
        """Load the page, clear the walls and wait for the quote tables."""
        self.call('get', self.browser.get, self.target_url())
        time.sleep(random.uniform(1.5, 3.5))
        self.wait_until(lambda: self.has_dialog() or self.has_content(),
                        timeout=PAGE_READY_TIMEOUT, label='page ready')
        self.dismiss_overlays()
        ready = self.wait_until(self.has_content, timeout=PAGE_READY_TIMEOUT, label='content')
        if not ready:
            logger.warning("page loaded but no quote table is visible yet")
        return bool(ready)

    # -- page state -----------------------------------------------------------

    def has_content(self):
        """True when the configured quote table is present in the DOM."""
        xpath = self.layout['tbody_xpath'].format(headline=self.layout['probe_headline'])
        return bool(self.js(_HAS_TABLE_JS, xpath, default=False, label='has_content'))

    def has_dialog(self):
        """True when a consent, sign-in or opt-in dialog is on the page."""
        return self._has_dialog(self.probe_overlays())

    @staticmethod
    def _has_dialog(report):
        """True when the page shows something that has to be answered."""
        return bool(report.get('consent') or report.get('signin') or report.get('prompt'))

    def probe_overlays(self):
        """Return a report about consent walls, sign-in and opt-in dialogs."""
        report = self.js(_OVERLAY_JS, default={}, label='probe_overlays') or {}
        if report.get('blocked'):
            logger.debug("overlay probe: %s", report)
        return report

    # -- overlay handling -----------------------------------------------------

    def dismiss_overlays(self, max_rounds=2):
        """Clear what stands between the collector and the quote table.

        Content-first: the page counts as usable as soon as the table is
        readable — the site permanently carries a full-viewport ad iframe and a
        scroll lock, and treating that as "blocked" would fire the recovery
        ladder on every cycle. Dialogs are always answered, though: the consent
        wall does not hide the table but does stop the quotes from updating.
        """
        report = self.probe_overlays()
        dialog = self._has_dialog(report)
        content = self.has_content()
        if content and not dialog:
            return True

        for attempt in range(1, max_rounds + 1):
            if report.get('consent'):
                self.dismiss_consent()
            if report.get('signin'):
                self.dismiss_signin()
            if report.get('prompt'):
                self.dismiss_prompt(report['prompt'])
            if not content:
                closed = self.js(_CLOSE_JS, default=[], label='close_overlays') or []
                if closed:
                    logger.info("closed %s overlay element(s): %s", len(closed), closed[:3])
                else:
                    self.press_escape()

            time.sleep(0.6)
            report = self.probe_overlays()
            content = self.has_content()
            dialog = self._has_dialog(report)
            if content and not dialog:
                logger.info("page is usable again after %s round(s)", attempt)
                return True

        logger.warning("page still blocked — content=%s, dialogs=%s, overlays=%s",
                       content, dialog, (report.get('overlays') or [])[:3])
        return False

    def dismiss_consent(self):
        """Accept the cookie/paywall dialog so the quotes keep updating."""
        report = self.probe_overlays()
        consent = report.get('consent') or {}
        if not consent:
            return False

        logger.info("consent wall detected (%s, %s)", consent.get('hint'),
                    consent.get('src') or consent.get('frame') or 'inline')
        for frame in self.consent_frames(consent):
            if self._consent_in_frame(frame):
                time.sleep(1.0)
                if not self.probe_overlays().get('consent'):
                    return True

        if self.click_first(CONSENT_BUTTONS, label='consent'):
            return True
        logger.warning("consent wall is up but no accept button was found")
        return False

    def consent_frames(self, consent):
        """Return the iframe element(s) that may host the consent wall.

        The Contentpass iframe carries no id and no class, so it is addressed by
        a distinctive part of its src and, failing that, by its position in the
        document's iframe list.
        """
        source = (consent.get('src') or '')
        for fragment in ('first-layer', 'cp.finanzen.net', 'contentpass'):
            if fragment in source:
                frames = self.find_frames(f"iframe[src*='{fragment}']")
                if frames:
                    return frames
        if consent.get('frame'):
            frames = self.find_frames(f"iframe[id*='{consent['frame']}']")
            if frames:
                return frames

        index = consent.get('index')
        if index is not None:
            frames = self.find_frames('iframe')
            if 0 <= index < len(frames):
                return [frames[index]]
        return []

    def find_frames(self, selector):
        """Return iframes matching a CSS selector, without waiting."""
        try:
            with self.no_implicit_wait():
                return self.call('find_frames', self.d.find_elements,
                                 self.browser.By.CSS_SELECTOR, selector) or []
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("frame lookup failed: %s", selector, exc_info=True)
            return []

    def _consent_in_frame(self, frame):
        """Switch into a consent iframe, click accept, switch back."""
        try:
            self.call('switch_frame', self.d.switch_to.frame, frame)
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("could not switch into the consent frame", exc_info=True)
            return False
        try:
            clicked = self.click_first(CONSENT_BUTTONS, label='consent')
            if clicked:
                logger.info("consent dialog: clicked %s", clicked)
            return bool(clicked)
        finally:
            try:
                self.call('switch_default', self.d.switch_to.default_content)
            except Exception:
                logger.warning("could not leave the consent frame", exc_info=True)

    def dismiss_signin(self):
        """Dismiss the Google sign-in prompt — close only, never sign in."""
        done = self.js(_ONETAP_JS, default=[], label='onetap') or []
        if done:
            logger.info("dismissed the Google sign-in prompt (%s)", ", ".join(done))
        return not self.probe_overlays().get('signin')

    def dismiss_prompt(self, prompt=None):
        """Decline an opt-in dialog (push notifications, app install, …).

        Presses the "not now" control only — never the opt-in button next to it.
        """
        if prompt:
            logger.info("opt-in dialog detected (%s)", prompt.get('label') or prompt.get('el'))
        clicked = self.click_first(DISMISS_BUTTONS, label='dismiss-prompt')
        if clicked:
            logger.info("declined the opt-in dialog via %s", clicked)
            return True
        closed = self.js(_CLOSE_JS, default=[], label='close_overlays') or []
        if closed:
            logger.info("closed the opt-in dialog (%s)", closed[:2])
            return True
        logger.warning("opt-in dialog is up but no decline control was found")
        return False

    def press_escape(self):
        """Send ESC to dismiss overlays that have no close button."""
        try:
            self.call('escape', lambda: self.browser.action_chains()
                      .send_keys(self.browser.Keys.ESCAPE).perform())
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("escape key failed", exc_info=True)

    @staticmethod
    def is_forbidden(element):
        """True when an element's label marks it as subscribe/login/payment."""
        try:
            text = (element.text or '')
        except Exception:
            return True          # unreadable -> do not click it
        for attribute in ('aria-label', 'title'):
            try:
                text += ' ' + (element.get_attribute(attribute) or '')
            except Exception:
                pass
        text = text.strip().lower()
        return any(bad in text for bad in FORBIDDEN_CLICK_TEXT)

    def click_first(self, paths, label='', guard=True):
        """Click the first visible element matching any XPath; no waiting at all.

        With guard=True any element whose text hits FORBIDDEN_CLICK_TEXT is
        skipped — subscribe/login/payment controls are never pressed.
        """
        with self.no_implicit_wait():
            for path in paths:
                try:
                    elements = self.call('find', self.d.find_elements,
                                         self.browser.By.XPATH, path) or []
                except DriverTimeout:
                    raise
                except Exception:
                    continue
                for element in elements[:3]:
                    try:
                        if not element.is_displayed():
                            continue
                    except Exception:
                        continue
                    if guard and self.is_forbidden(element):
                        logger.warning("refusing to click a subscribe/login control (%s)", path)
                        continue
                    try:
                        self.call('click', element.click)
                        return path
                    except DriverTimeout:
                        raise
                    except Exception:
                        try:
                            self.call('js_click', self.d.execute_script,
                                      "arguments[0].click();", element)
                            return path
                        except DriverTimeout:
                            raise
                        except Exception:
                            logger.debug("click failed: %s", path, exc_info=True)
        return ''

    # -- reading --------------------------------------------------------------

    def read_table(self, headline):
        """Return the quote table below `headline` as a list of cell-text rows."""
        xpath = self.layout['tbody_xpath'].format(headline=headline)
        rows = self.js(_TABLE_JS, xpath, default=None, label='read_table')
        if rows:
            return rows

        rows = []
        try:
            with self.no_implicit_wait():
                bodies = self.call('find_table', self.d.find_elements,
                                   self.browser.By.XPATH, xpath) or []
                if not bodies:
                    return []
                for tr in bodies[0].find_elements(self.browser.By.TAG_NAME, 'tr'):
                    cells = tr.find_elements(self.browser.By.TAG_NAME, 'td')
                    rows.append([c.text.strip() for c in cells])
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("table read failed for %s", headline, exc_info=True)
        return rows

    def scrape(self, symbols):
        """Scrape every symbol in `symbols` ({sym: {name, headline}}).

        Returns (quotes, issues) where quotes maps symbol → {"price": float,
        "time": "HH:MM:SS"} and issues is a list of human-readable problems.
        """
        quotes, issues = {}, []
        name_col = self.layout['name_col']
        price_col = self.layout['price_col']
        time_col = self.layout['time_col']

        headlines = sorted({meta["headline"] for meta in symbols.values()})
        for headline in headlines:
            rows = self.read_table(headline)
            if not rows:
                issues.append(f"table missing: {headline}")
                continue

            # Index the table once by its name column. Keys are casefolded: the
            # site writes "TecDAX" where the config says "TecDax".
            by_name = {}
            for cells in rows:
                if len(cells) > max(name_col, price_col, time_col):
                    by_name.setdefault(cells[name_col].strip().casefold(), cells)

            wanted = {s: m for s, m in symbols.items() if m["headline"] == headline}
            for symbol, meta in wanted.items():
                cells = by_name.get(meta["name"].strip().casefold())
                if cells is None:
                    issues.append(f"row missing: {meta['name']}")
                    continue
                price = parse_number(cells[price_col])
                # A full local timestamp, not a bare clock time: the source
                # publishes in its own zone, and only the collector knows both.
                stamp = to_timestamp(cells[time_col], self.source_timezone)
                if price is None or price <= 0:
                    issues.append(f"unparsable price for {meta['name']}: {cells[price_col]!r}")
                    continue
                if stamp is None:
                    issues.append(f"unparsable time for {meta['name']}: {cells[time_col]!r}")
                    continue
                quotes[symbol] = {"price": price, "time": stamp}

        return quotes, issues

    # -- recovery -------------------------------------------------------------

    def recover(self, reason=""):
        """Escalating recovery: overlays → reload → re-navigate → restart."""
        self.recovery_step += 1
        step = self.recovery_step
        logger.warning("recovery step %s (%s)", step, reason or "unknown reason")
        try:
            if step == 1:
                self.dismiss_overlays()
            elif step == 2:
                self.call('refresh', self.d.refresh)
                time.sleep(random.uniform(2, 4))
                self.dismiss_overlays()
            elif step == 3:
                self.setup_page()
            else:
                self.restart()
                self.recovery_step = 0
        except DriverTimeout:
            logger.warning("recovery step %s hit a driver timeout", step)
            self.restart()
            self.recovery_step = 0
        except Exception:
            logger.warning("recovery step %s failed", step, exc_info=True)
            self.restart()
            self.recovery_step = 0
        return step

    def reset_recovery(self):
        """Forget previous recovery attempts after a healthy cycle."""
        self.recovery_step = 0

    def restart(self):
        """Throw the browser away and open a fresh one on the ticker page."""
        logger.warning("restarting the browser")
        self.quit()
        self.browser.start()
        self.setup_page()

    def quit(self):
        """Close the browser and free the driver process."""
        try:
            self.call('quit', self.browser.quit, timeout=QUIT_TIMEOUT)
        except DriverTimeout:
            pass  # the process was killed already
        except Exception:
            logger.debug("quit failed", exc_info=True)
            self.browser.kill()
