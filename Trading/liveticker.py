"""Live ticker collector.

Scrapes real-time quotes from finanzen.net with Selenium and streams them into
the Trading app (``/?stream=api``), where ``tradinglib.live_ticker`` stores them
as ticks and renders the intraday charts.

Design notes
------------
* One JavaScript round-trip per table instead of one per cell — the old
  ``tr.find_elements(TAG_NAME,'td')`` walk cost hundreds of round-trips per cycle.
* Every value is parsed and validated *before* it is sent: German number format
  to float, quote time to ``HH:MM:SS``, plus a plausibility check against the
  last accepted price. Unparseable or implausible values are dropped, never
  forwarded as strings (the receiving DB column is REAL).
* Page problems (missing table, frozen quotes, overlays, expired session) are
  detected and answered with an escalating recovery ladder:
  dismiss overlays → reload → re-navigate → restart the browser.

Usage::

    python liveticker.py [/messages_only] [/allow_notify] [/fetch_type:members]
                         [/page:/aktien/dax-realtimekurse] [/log:DEBUG]
                         [/target:https://trading.cloogidoo.com] [/transport:ws]
                         [/anytime] [/dry_run] [/once]

``/transport:`` picks how the ticks reach the app:

``auto``     (default) websocket first, browser as fallback when it fails
``ws``       websocket only — no browser at all, fails loudly instead
``browser``  the legacy route: drive a browser to the ingest URL

``/target:`` picks the app instance the ticks are streamed to. It defaults to the
local app (http://localhost:8080); the live instance has to be named explicitly:
``/target:https://trading.cloogidoo.com``. Scheme and port are optional —
"localhost:8080" resolves to http, a public host to https.

Test switches:

``/anytime``  ignore the trading calendar (weekend *and* time of day) — the only
              way to exercise the scraper outside 06:00-21:59:30 on a weekday.
``/dry_run``  scrape and log, but send nothing to the app: no target browser is
              opened and the tick database stays untouched.
``/once``     run a single cycle, then close the browsers and exit.

A safe end-to-end check of the page handling therefore is::

    python liveticker.py /anytime /dry_run /once /log:DEBUG
"""

import contextlib
import copy
import datetime as dt
import json
import logging
import math
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from tradinglib import (
    system_config as sysconf, web_tools, live_ticker as live_ticker, file_provider,
    tick_stream
)

logger = logging.getLogger("liveticker")

INDICES =  {
                "^GDAXI":{"name":"DAX","headline":"Indikation auf Indizes"},
                "^MDAXI":{"name":"MDAX","headline":"Indikation auf Indizes"},
                "^SDAXI":{"name":"SDAX","headline":"Indikation auf Indizes"},
                "^STOXX50E":{"name":"EURO STOXX 50","headline":"Indikation auf Indizes"},
                "^TECDAX":{"name":"TecDAX","headline":"Indikation auf Indizes"},
                "^FTSE":{"name":"FTSE 100","headline":"Indikation auf Indizes"},
                "^DJI":{"name":"Dow Jones","headline":"Indikation auf Indizes"},
                "^SPX":{"name":"S&P 500","headline":"Indikation auf Indizes"},
                "^HSI":{"name":"Hang Seng","headline":"Indikation auf Indizes"},
                "^N225":{"name":"NIKKEI 225","headline":"Indikation auf Indizes"},
                "GC=F":{"name":"Goldpreis","headline":"Indikation auf Rohstoffe"},
                "SI=F":{"name":"Silberpreis","headline":"Indikation auf Rohstoffe"},
                "EURUSD=X": {"name":"Dollarkurs","headline":"Indikation auf Währungen und Wechselkurse"},
#                "EURJPY=X": {"name":"EUR/JPY","headline":"Indikation auf Währungen und Wechselkurse"},
                "BZ=F": {"name":"Ölpreis (Brent)","headline":"Indikation auf Rohstoffe"},
                "BUND-FUT": {"name":"Euro-BUND-Future","headline":"Indikation auf Futures"},
                }
DAX_MEMBERS = {
                "ADS.DE":{"name":"adidas","headline":"Name"},
                "AIR.DE":{"name":"Airbus","headline":"Name"},
                "ALV.DE":{"name":"Allianz","headline":"Name"},
                "BAS.DE":{"name":"BASF","headline":"Name"},
                "BAYN.DE":{"name":"Bayer","headline":"Name"},
                "BEI.DE":{"name":"Beiersdorf","headline":"Name"},
                "BMW.DE":{"name":"BMW","headline":"Name"},
                "BNR.DE":{"name":"Brenntag","headline":"Name"},
                "CBK.DE":{"name":"Commerzbank","headline":"Name"},
                "CON.DE":{"name":"Continental","headline":"Name"},
                "DTG.DE":{"name":"Daimler Truck","headline":"Name"},
                "DBK.DE":{"name":"Deutsche Bank","headline":"Name"},
                "DB1.DE":{"name":"Deutsche Börse","headline":"Name"},
                "DTE.DE":{"name":"Deutsche Telekom","headline":"Name"},
                "DHL.DE":{"name":"DHL Group (ex Deutsche Post)","headline":"Name"},
                "EOAN.DE":{"name":"E.ON","headline":"Name"},
                "FME.DE":{"name":"Fresenius Medical Care (FMC) St.","headline":"Name"},
                "FRE.DE":{"name":"Fresenius","headline":"Name"},
                "HNR1.DE":{"name":"Hannover Rück","headline":"Name"},
                "HEI.DE":{"name":"Heidelberg Materials","headline":"Name"},
                "HEN.DE":{"name":"Henkel vz.","headline":"Name"},
                "IFX.DE":{"name":"Infineon","headline":"Name"},
                "MBG.DE":{"name":"Mercedes-Benz Group (ex Daimler)","headline":"Name"},
                "MRK.DE":{"name":"Merck","headline":"Name"},
                "MTX.DE":{"name":"MTU Aero Engines","headline":"Name"},
                "MUV2.DE":{"name":"Münchener Rückversicherungs-Gesellschaft","headline":"Name"},
                "P911.DE":{"name":"Porsche","headline":"Name"},
                "PAH3.DE":{"name":"Porsche Automobil vz.","headline":"Name"},
                "QIA.DE":{"name":"QIAGEN","headline":"Name"},
                "RHM.DE":{"name":"Rheinmetall","headline":"Name"},
                "RWE.DE":{"name":"RWE","headline":"Name"},
                "SAP.DE":{"name":"SAP","headline":"Name"},
                "SRT3.DE":{"name":"Sartorius vz.","headline":"Name"},
                "SIE.DE":{"name":"Siemens","headline":"Name"},
                "ENR.DE":{"name":"Siemens Energy","headline":"Name"},
                "SHL.DE":{"name":"Siemens Healthineers","headline":"Name"},
                "SY1.DE":{"name":"Symrise","headline":"Name"},
                "VOW3.DE":{"name":"Volkswagen (VW) vz.","headline":"Name"},
                "VNA.DE":{"name":"Vonovia","headline":"Name"},
                "ZAL.DE":{"name":"Zalando","headline":"Name"},
}

# Where the quote columns sit, per page type. The realtime index page renders
# name/price/…/time; the DAX member page has an extra leading column.
TABLE_LAYOUTS = {
    'indices': {
        'tbody_xpath': '//h2[contains(text(),"{headline}")]/../div/table/tbody',
        'name_col': 0,
        'price_col': 1,
        'time_col': 5,
        # Headline used for the cheap "is the page ready" probe.
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

# Read a whole table in a single WebDriver round-trip.
_TABLE_JS = """
const tb = document.evaluate(arguments[0], document, null,
                             XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
if (!tb) { return null; }
return Array.from(tb.rows).map(r => Array.from(r.cells).map(c => (c.innerText || '').trim()));
"""

# Cheap "is the content there at all" probe.
_HAS_TABLE_JS = """
const tb = document.evaluate(arguments[0], document, null,
                             XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
return !!(tb && tb.rows && tb.rows.length);
"""

# Shared helper for the overlay scripts: what counts as a blocking layer.
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

# Report consent dialogs and anything that covers the page. One round-trip,
# no implicit waits — a clean page costs a single script call.
_OVERLAY_JS = _OVERLAY_HELPERS + """
const CONSENT_HINTS = ['first-layer', 'cp.finanzen.net', 'contentpass', 'sp_message',
                       'sourcepoint', 'consent', 'cmp', 'usercentrics',
                       'didomi', 'onetrust', 'gdpr'];
// finanzen.net wraps Google One Tap in its own container (#onetap-container with
// #pseudo-google-one-tap-close); the plain Google iframe variants are listed too
// in case the site switches back to the stock widget.
const SIGNIN_SELECTORS = ['#onetap-container', '#pseudo-google-one-tap-close',
                          'iframe[src*="accounts.google.com/gsi"]',
                          'iframe[src*="accounts.google.com/o/oauth2"]',
                          'div[id^="credential_picker"]'];
const report = {consent: null, signin: null, prompt: null, overlays: [],
                scrollLocked: false, covered: false, blocked: false};

// 0. Google One Tap ("Melde Dich mit Deinem Google-Konto an"). It lives in a
// cross-origin iframe, so the generic close pass below can never reach it.
for (const sel of SIGNIN_SELECTORS) {
  const el = document.querySelector(sel);
  if (el && _visible(el)) { report.signin = _describe(el); break; }
}

// 1. Consent / pay-wall iframes (cross-origin -> only reachable via a frame
// switch). finanzen.net's Contentpass wall ("Weiter mit Werbung … oder mit
// Contentpass") is an iframe WITHOUT id and WITHOUT class, served from
// cp.finanzen.net/first-layer — it can only be recognised by its src or by its
// sheer geometry, and the quote table stays readable behind it, so "the table
// is there" is NOT proof that the page is usable.
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

// 2. Inline consent walls (no iframe): a floating box mentioning cookies.
if (!report.consent) {
  for (const el of document.querySelectorAll('div,section,aside,dialog')) {
    if (!_floating(el) || !_visible(el)) continue;
    if (_area(el) < innerWidth * innerHeight * 0.15) continue;
    const txt = (el.innerText || '').toLowerCase().slice(0, 400);
    if (txt.includes('cookie') || txt.includes('datenschutz') || txt.includes('einwillig')) {
      report.consent = {frame: '', hint: 'inline', area: Math.round(_area(el))};
      break;
    }
  }
}

// 3. Any large floating layer with a high stacking order.
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

// 3b. Opt-in dialogs (push notifications, app install …): a floating box that
// offers a "not now" control. Matched on innerText, so a label upper-cased by
// CSS is found too. The opt-in button itself is never touched.
const DISMISS = ['später entscheiden', 'spaeter entscheiden', 'nicht jetzt',
                 'nicht interessiert', 'maybe later', 'not now', 'no thanks',
                 'nein danke'];
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

// 4. Modal side effects: a scroll lock, or the page centre no longer being content.
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

# Dismiss Google One Tap. This only ever CANCELS the prompt — the same thing the
# X button does. The scraper must never touch "Weiter mit Google": signing in is
# not its job. google.accounts.id.cancel() is the official API for this; node
# removal is the fallback for when the library object is not exposed.
_ONETAP_JS = """
const done = [];
// finanzen.net's own wrapper has a real close button — press that first.
const own = document.querySelector('#pseudo-google-one-tap-close');
if (own) { try { own.click(); done.push('onetap-close-button'); } catch (e) {} }
try {
  if (window.google && google.accounts && google.accounts.id) {
    google.accounts.id.cancel();
    done.push('api-cancel');
  }
} catch (e) {}
for (const sel of ['#onetap-container', 'div[id^="credential_picker"]',
                   'iframe[src*="accounts.google.com/gsi"]',
                   'iframe[src*="accounts.google.com/o/oauth2"]']) {
  document.querySelectorAll(sel).forEach(el => {
    const host = el.closest('#onetap-container, div[id^="credential_picker"]') || el;
    try { host.remove(); done.push(sel); } catch (e) {}
  });
}
return done;
"""

# Click only elements with explicit close semantics, or close controls that sit
# inside a floating layer. Deliberately narrow: a scraper must not click random
# call-to-action buttons.
_CLOSE_JS = _OVERLAY_HELPERS + """
const LABELS = ['close', 'schließen', 'schliessen', 'close player', 'dismiss',
                'nicht interessiert', 'no thanks', 'nein danke', 'überspringen', 'skip ad'];
// "Not now" of opt-in dialogs — matched on the *rendered* text, so a label
// upper-cased by CSS is caught as well.
const DISMISS = ['später entscheiden', 'spaeter entscheiden', 'nicht jetzt',
                 'nicht interessiert', 'maybe later', 'not now', 'no thanks',
                 'nein danke'];
// Never press these, whatever else matches: they cost money or subscribe to
// something ("Ablehnen & abonnieren", "Benachrichtigungen aktivieren", logins).
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

for (const el of document.querySelectorAll('button,[role="button"],a[href="#"],a,span[class*="close"],div[class*="close"]')) {
  if (clicked.length >= 6) break;
  if (!_visible(el) || _forbidden(el) || !_closeish(el)) continue;
  // Only inside a floating layer, or floating itself — never a page button.
  let host = el, floating = false;
  for (let i = 0; host && i < 8; host = host.parentElement, i++) {
    if (_floating(host)) { floating = true; break; }
  }
  if (!floating) continue;
  try { el.click(); clicked.push(_describe(el)); } catch (e) {}
}
return clicked;
"""

# Consent: the user chose the free "accept" route for this scraper. The wall on
# finanzen.net is the Contentpass first layer, whose free option reads
# "Einwilligen & weiter"; the other labels cover a CMP wording change.
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

# Hard guard for every click. Two of the buttons this scraper meets would cost
# something if pressed: "Ablehnen & abonnieren" (3,99 €/month subscription, right
# next to the accept button) and "Benachrichtigungen aktivieren" (push opt-in).
# Matching is done on the element's own text, aria-label and title.
FORBIDDEN_CLICK_TEXT = ('abonnier', 'abo ', 'einloggen', 'login', 'anmelden',
                        'registrier', 'kaufen', 'kostenpflichtig', 'zahlungs',
                        'jetzt testen', 'weiter mit google',
                        'aktivieren', 'zulassen', 'erlauben', 'allow', 'enable',
                        'subscribe', 'benachrichtigung', 'push')

# Lower-casing for XPath 1.0, which has no lower-case() function. Needed because
# a label may be upper-cased by CSS (innerText shows "SPÄTER ENTSCHEIDEN" while
# the DOM text — which XPath sees — reads "Später entscheiden", or vice versa).
_XPATH_LOWER = ("translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜ', "
                "'abcdefghijklmnopqrstuvwxyzäöü')")

# "Not now" controls of opt-in dialogs (push notifications, app install, …).
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

# Generic close controls used by click_close()/the recovery ladder.
CLOSE_BUTTONS = [
    '//button[@title="Close"]',
    '//button[@aria-label="Close"]',
    '//*[@aria-label="Schließen"]',
    '//button[contains(@class,"close")]',
    '//button[contains(., " entscheiden")]',
]

# --- collection tuning -------------------------------------------------------
CYCLE_SECONDS = 20          # target interval between two scrapes
MIN_COVERAGE = 0.5          # below this share of resolved symbols the page counts as broken
STALL_CYCLES = 5            # unchanged cycles before the page is treated as frozen
MAX_JUMP_PCT = 10.0         # a bigger single-step move must be confirmed twice
MAX_QUOTE_AGE_MIN = 90      # quote timestamps older than this are reported as stale
# A tab that stays open all day goes partly stale: the source keeps pushing the
# index and commodity sections but stops refreshing the FX one (measured: the
# collector sat on one EURUSD=X quote from 06:59 while a freshly loaded page
# showed the current rate). Nothing detects that — the frozen-quotes check only
# fires when *every* symbol stops moving. A periodic reload cures the whole
# class of "section quietly stopped updating" without having to know which
# section it is.
RELOAD_MINUTES = 30
CHUNK_SIZE = 20             # symbols per API call (keeps the GET URL short)
# Where the ticks are streamed to. Defaults to the local app; a remote instance
# is selected explicitly with /target:https://trading.cloogidoo.com.
DEFAULT_TARGET = "http://localhost:8080"
IDLE_SLEEP = 300            # nap length while the markets are closed (no requests)
CALL_TIMEOUT = 45           # hard deadline for a single WebDriver command
QUIT_TIMEOUT = 15           # deadline for driver.quit() before the process is killed
PAGE_READY_TIMEOUT = 25     # how long to poll for consent dialog / quote table

START_TIME = dt.time(6, 0, 0)
END_TIME = dt.time(21, 59, 30)
CLEANUP_TIME = dt.time(21, 59, 0)

_NUMBER_RE = re.compile(r"[-+]?\d[\d.,]*")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def parse_number(raw, locale='de'):
    """Parse a localised price string into a float.

    Handles German ("24.004,02"), plain ("1.1383") and US ("24,004.02") formats
    and strips unit suffixes such as " PKT" / " EUR" / " USD".
    Returns None when the text holds no usable number.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(float(raw)) else None

    # Drop every kind of spacing first — the site uses non-breaking and thin
    # spaces both as padding and as a thousands separator.
    text = re.sub(r"[\s   ']", "", str(raw)).replace('−', '-')
    match = _NUMBER_RE.search(text)
    if not match:
        return None

    token = match.group(0).rstrip('.,')
    has_dot, has_comma = '.' in token, ',' in token

    if has_dot and has_comma:
        # The separator that appears last is the decimal one.
        if token.rfind(',') > token.rfind('.'):
            token = token.replace('.', '').replace(',', '.')
        else:
            token = token.replace(',', '')
    elif has_comma:
        # Several commas can only be thousands separators.
        token = token.replace(',', '') if token.count(',') > 1 else token.replace(',', '.')
    elif has_dot:
        tail = token.rsplit('.', 1)[1]
        # "1.234" is ambiguous — on a German page it is a thousands separator.
        if token.count('.') > 1 or (len(tail) == 3 and locale == 'de'):
            token = token.replace('.', '')

    try:
        value = float(token)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_time(raw):
    """Extract a HH:MM:SS clock time from a cell such as "26.05.25 21:57:52".

    Returns None when no plausible time is present.
    """
    if raw is None:
        return None
    match = _TIME_RE.search(str(raw))
    if not match:
        return None
    hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def split_target(target=''):
    """Split a target URL into the (protocol, host, port, path) WebFetch expects.

    Accepts "http://localhost:8080", "localhost:8080", "trading.cloogidoo.com"
    and a base path ("http://localhost:8080/app"). A missing scheme becomes http
    for loopback hosts and https for everything else — a local dev server almost
    never speaks TLS, a public one always should. An empty value falls back to
    DEFAULT_TARGET (the local app).
    """
    text = (target or DEFAULT_TARGET).strip()
    if '://' not in text:
        host = text.split('/')[0].split(':')[0].lower()
        scheme = 'http' if host in ('localhost', '127.0.0.1', '::1', '0.0.0.0') else 'https'
        text = f'{scheme}://{text}'

    parts = urllib.parse.urlsplit(text)
    protocol = f'{parts.scheme}://'
    host = parts.hostname or 'localhost'
    try:
        port = f':{parts.port}' if parts.port else ''
    except ValueError:                       # malformed port -> ignore it
        port = ''
    return protocol, host, port, parts.path.rstrip('/')


def quote_age_minutes(time_str, now=None):
    """Return how many minutes ago `time_str` (HH:MM:SS) occurred, wrapping over midnight."""
    now = now or dt.datetime.now()
    try:
        stamp = dt.datetime.combine(now.date(), dt.datetime.strptime(time_str, "%H:%M:%S").time())
    except (TypeError, ValueError):
        return None
    delta = (now - stamp).total_seconds() / 60.0
    if delta < -5:                      # quote "in the future" → it belongs to yesterday
        delta += 24 * 60
    return delta


class DriverTimeout(Exception):
    """A WebDriver command did not return within its deadline."""


class WebFetch:
    """Thin Selenium wrapper: page lifecycle, overlay handling and table reads.

    Every driver command runs behind a deadline (see `call`) — a hung renderer
    or a dead chromedriver ends the cycle instead of freezing the collector.
    """

    def __init__(self, protocol='http://', url='localhost', page='', port='', autosetup=True,
                 layout=None, call_timeout=CALL_TIMEOUT, profile=''):
        """Open a browser for the given site and optionally load the start page."""
        self.protocol = protocol
        self.url = url
        self.page = page
        self.port = port
        self.layout = layout or TABLE_LAYOUTS['indices']
        self.call_timeout = call_timeout
        self.profile = profile
        self.recovery_step = 0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='webdriver')
        self.wt = web_tools.WebTools()
        if profile:
            # Instance attribute — shadows the shared class-level options so the
            # two browsers of this collector cannot fight over one profile dir.
            self.wt.chromeOptions = self.profile_options(profile)
        self.date_str = dt.datetime.now().strftime(self.wt.ftime_str)
        self.wt.init_webdriver()
        if autosetup:
            self.setup_page()

    @staticmethod
    def profile_options(profile):
        """Clone the shared Chrome options and pin them to a persistent profile.

        With a throwaway profile the consent wall returns on every browser start
        (and this collector restarts daily plus on every hard recovery). Keeping
        the cookies turns that into a rare event instead of a daily one.
        """
        options = web_tools.webdriver.ChromeOptions()
        shared = web_tools.WebTools.chromeOptions
        for argument in shared.arguments:
            options.add_argument(argument)
        try:
            options.page_load_strategy = shared.page_load_strategy
        except Exception:
            logger.debug("could not copy the page load strategy", exc_info=True)
        path = os.path.join(tempfile.gettempdir(), 'liveticker_profiles', profile)
        os.makedirs(path, exist_ok=True)
        options.add_argument(f'--user-data-dir={path}')
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        options.add_argument('--disable-session-crashed-bubble')
        options.add_argument('--disable-notifications')
        # Deny permission requests at the browser level: Chrome's own permission
        # bubble is not part of the DOM, so Selenium could never dismiss it.
        # 2 = block. The site's own pre-prompt is handled by dismiss_prompt().
        options.add_experimental_option("prefs", {
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_setting_values.geolocation": 2,
            "profile.default_content_setting_values.media_stream_mic": 2,
            "profile.default_content_setting_values.media_stream_camera": 2,
        })
        logger.debug("using the persistent browser profile %s", path)
        return options

    # -- driver plumbing ------------------------------------------------------

    def call(self, label, func, *args, timeout=None):
        """Run a WebDriver command with a hard deadline.

        Selenium blocks on an HTTP call to chromedriver; if the browser hangs,
        that call never returns. The command therefore runs in a worker thread
        and, on timeout, the chromedriver process is killed so the blocked call
        fails and the thread can die. Raises DriverTimeout.
        """
        pool = self._pool
        future = pool.submit(func, *args)
        try:
            return future.result(timeout=timeout or self.call_timeout)
        except FutureTimeout:
            logger.error("driver command '%s' timed out after %ss — killing the browser",
                         label, timeout or self.call_timeout)
            # Abandon the stuck worker; killing the driver process makes its
            # pending HTTP request fail, after which the thread terminates.
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='webdriver')
            pool.shutdown(wait=False)
            self.hard_kill()
            raise DriverTimeout(label)

    def js(self, script, *args, timeout=None, default=None, label='script'):
        """Execute JavaScript in the page; returns `default` on any page-side error."""
        try:
            return self.call(label, self.wt.d.execute_script, script, *args, timeout=timeout)
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("script '%s' failed", label, exc_info=True)
            return default

    def hard_kill(self):
        """Kill the chromedriver process tree — the only reliable cure for a hang."""
        pid = None
        try:
            pid = self.wt.d.service.process.pid
        except Exception:
            logger.debug("no driver process to kill", exc_info=True)
        if not pid:
            return
        try:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                               capture_output=True, timeout=20)
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            logger.warning("killed chromedriver pid %s", pid)
        except Exception:
            logger.warning("could not kill chromedriver pid %s", pid, exc_info=True)

    def wait_until(self, predicate, timeout=15, interval=0.5, label=''):
        """Poll `predicate` until it returns truthy or the timeout expires.

        Used instead of fixed sleeps and instead of WebDriverWait: every probe
        is a cheap non-blocking script call, so a missing element costs the
        timeout at most once — not on every single lookup.
        """
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
        page = page if page else self.page
        return f'{self.protocol}{self.url}{self.port}{page}{path}'

    def get(self, path='', page=''):
        """Navigate to the configured page (optionally with an extra path/query)."""
        self.call('get', self.wt.get, self.target_url(path=path, page=page))

    def setup_page(self):
        """Load the page, clear the consent wall and wait for the quote tables.

        Replaces the old fixed sleep chain (5-10s + 8-12s + 3s ≈ 20s per load):
        the page is polled instead, so a clean load continues immediately and a
        blocked one is handled as soon as the dialog appears.
        """
        self.get()
        # A short human-like pause before touching anything.
        time.sleep(random.uniform(1.5, 3.5))
        self.maximize()

        # Wait until either the consent dialog or the content shows up.
        self.wait_until(lambda: self.has_consent_dialog() or self.has_content(),
                        timeout=PAGE_READY_TIMEOUT, label='page ready')
        self.dismiss_consent()
        self.dismiss_overlays()
        ready = self.wait_until(self.has_content, timeout=PAGE_READY_TIMEOUT, label='content')
        if not ready:
            logger.warning("page loaded but no quote table is visible yet")
        return bool(ready)

    def connect_to(self, symbol="", page=""):
        """Re-open the ticker page — used on start-up and after a hard recovery."""
        if page:
            self.page = page
        if symbol:
            logger.info("search: %s", symbol)
        return self.setup_page()

    def maximize(self):
        """Maximise the window; a small viewport makes the site drop table columns."""
        try:
            self.call('maximize', self.wt.d.maximize_window)
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("maximize_window failed", exc_info=True)

    # -- page state -----------------------------------------------------------

    def has_content(self):
        """True when at least one configured quote table is present in the DOM."""
        xpath = self.layout['tbody_xpath'].format(headline=self.layout['probe_headline'])
        return bool(self.js(_HAS_TABLE_JS, xpath, default=False, label='has_content'))

    def has_consent_dialog(self):
        """True when a consent iframe or an inline consent wall is on the page."""
        report = self.probe_overlays()
        return bool(report.get('consent'))

    def probe_overlays(self):
        """Return a report about consent dialogs and blocking overlays.

        One round-trip, no implicit waits: the script inspects the live DOM and
        reports what is actually there instead of blindly probing a fixed list
        of selectors (which cost a WebDriverWait each time they missed).
        """
        report = self.js(_OVERLAY_JS, default={}, label='probe_overlays') or {}
        if report.get('blocked'):
            logger.debug("overlay probe: %s", report)
        return report

    # -- overlay handling -----------------------------------------------------

    def dismiss_overlays(self, max_rounds=2):
        """Close what stands between the collector and the quote table.

        Content-first policy: the page counts as usable as soon as the table is
        readable. finanzen.net permanently carries a full-viewport ad iframe with
        z-index 2147483647 and a scroll lock — treating that as "blocked" would
        fire the recovery ladder on every single cycle even though the data is
        perfectly readable. Only consent and sign-in dialogs are always acted on;
        the generic close pass runs only when the table is actually missing.
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
                if not closed:
                    self.click_escape()

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

    @staticmethod
    def _has_dialog(report):
        """True when the page shows something that has to be answered, not just covered."""
        return bool(report.get('consent') or report.get('signin') or report.get('prompt'))

    def dismiss_prompt(self, prompt=None):
        """Decline an opt-in dialog (push notifications, app install, …).

        Presses the "not now" control only — never the opt-in button next to it.
        The XPaths match case-insensitively because such labels are often
        upper-cased by CSS, which XPath (unlike innerText) does not see.
        """
        if prompt:
            logger.info("opt-in dialog detected (%s)", prompt.get('label') or prompt.get('el'))
        clicked = self.click_first(DISMISS_BUTTONS, label='dismiss-prompt')
        if clicked:
            logger.info("declined the opt-in dialog via %s", clicked)
            return True
        # The generic close pass also knows the "not now" vocabulary.
        closed = self.js(_CLOSE_JS, default=[], label='close_overlays') or []
        if closed:
            logger.info("closed the opt-in dialog (%s)", closed[:2])
            return True
        logger.warning("opt-in dialog is up but no decline control was found")
        return False

    def dismiss_signin(self):
        """Dismiss the Google One Tap sign-in prompt.

        The prompt is a cross-origin iframe from accounts.google.com, so the
        generic close pass cannot reach it. Order: the official cancel API,
        then removing the container, then clicking the X inside the frame.

        This only ever closes the prompt — the collector never signs in and
        must not click "Weiter mit Google".
        """
        done = self.js(_ONETAP_JS, default=[], label='onetap') or []
        if done:
            logger.info("dismissed the Google sign-in prompt (%s)", ", ".join(done))
        if not self.probe_overlays().get('signin'):
            return True

        # Last resort: the X inside the One Tap frame.
        for frame in self.find_frames_by_src('accounts.google.com'):
            try:
                self.call('switch_frame', self.wt.d.switch_to.frame, frame)
            except DriverTimeout:
                raise
            except Exception:
                continue
            try:
                # '#close' is One Tap's close button — never the sign-in button.
                if self.click_first(['//*[@id="close"]',
                                     '//*[@aria-label="Schließen"]',
                                     '//*[@aria-label="Close"]'], label='onetap-close'):
                    logger.info("closed the Google sign-in prompt via its X button")
                    return True
            finally:
                try:
                    self.call('switch_default', self.wt.d.switch_to.default_content)
                except Exception:
                    logger.warning("could not leave the sign-in frame", exc_info=True)
        return False

    def find_frames_by_src(self, fragment):
        """Return iframes whose src contains `fragment`, without waiting."""
        try:
            with self.no_implicit_wait():
                return self.call('find_frames_src', self.wt.d.find_elements,
                                 self.wt.By.CSS_SELECTOR, f"iframe[src*='{fragment}']") or []
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("frame lookup by src failed", exc_info=True)
            return []

    def dismiss_consent(self):
        """Accept the cookie dialog so the quote tables render.

        Consent lives in a cross-origin iframe, so it cannot be clicked from the
        top document — this switches into the frame, clicks the configured
        button and always switches back. The click target is CONSENT_BUTTONS.
        """
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

        # Inline wall (no iframe) — click straight in the main document.
        if self.click_first(CONSENT_BUTTONS, label='consent'):
            return True
        logger.warning("consent wall is up but no accept button was found")
        return False

    def consent_frames(self, consent):
        """Return the iframe element(s) that may host the consent wall.

        The Contentpass iframe carries no id and no class, so it is addressed by
        a distinctive part of its src and, failing that, by its position in the
        document's iframe list (the probe reports the index).
        """
        source = (consent.get('src') or '')
        for fragment in ('first-layer', 'cp.finanzen.net', 'contentpass'):
            if fragment in source:
                frames = self.find_frames_by_src(fragment)
                if frames:
                    return frames
        if consent.get('frame'):
            frames = self.find_frames(consent['frame'])
            if frames:
                return frames

        index = consent.get('index')
        if index is not None:
            try:
                with self.no_implicit_wait():
                    frames = self.call('find_all_frames', self.wt.d.find_elements,
                                       self.wt.By.CSS_SELECTOR, 'iframe') or []
                if 0 <= index < len(frames):
                    return [frames[index]]
            except DriverTimeout:
                raise
            except Exception:
                logger.debug("iframe lookup by index failed", exc_info=True)
        return []

    def _consent_in_frame(self, frame):
        """Switch into a consent iframe, click the accept button, switch back."""
        try:
            self.call('switch_frame', self.wt.d.switch_to.frame, frame)
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("could not switch into consent frame", exc_info=True)
            return False
        try:
            clicked = self.click_first(CONSENT_BUTTONS, label='consent')
            if clicked:
                logger.info("consent dialog: clicked %s", clicked)
            return bool(clicked)
        finally:
            try:
                self.call('switch_default', self.wt.d.switch_to.default_content)
            except Exception:
                logger.warning("could not leave the consent frame", exc_info=True)

    def find_frames(self, id_fragment=''):
        """Return candidate consent iframes without waiting for them."""
        selector = f"iframe[id*='{id_fragment}']" if id_fragment else "iframe"
        try:
            with self.no_implicit_wait():
                return self.call('find_frames', self.wt.d.find_elements,
                                 self.wt.By.CSS_SELECTOR, selector) or []
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("frame lookup failed", exc_info=True)
            return []

    def click_first(self, paths, label='', guard=True):
        """Click the first visible element matching any XPath; no waiting at all.

        Returns the XPath that was clicked, or '' when nothing matched. A normal
        click is tried first, then a JS click for elements covered by an ad.
        With guard=True any element whose text hits FORBIDDEN_CLICK_TEXT is
        skipped — subscribe/login/payment controls are never pressed.
        """
        with self.no_implicit_wait():
            for path in paths:
                try:
                    elements = self.call('find', self.wt.d.find_elements,
                                         self.wt.By.XPATH, path) or []
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
                    if guard and self._is_forbidden(element):
                        logger.warning("refusing to click a subscribe/login control (%s)", path)
                        continue
                    try:
                        self.call('click', element.click)
                        return path
                    except DriverTimeout:
                        raise
                    except Exception:
                        try:
                            self.call('js_click', self.wt.d.execute_script,
                                      "arguments[0].click();", element)
                            return path
                        except DriverTimeout:
                            raise
                        except Exception:
                            logger.debug("click failed: %s", path, exc_info=True)
        return ''

    @staticmethod
    def _is_forbidden(element):
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

    @contextlib.contextmanager
    def no_implicit_wait(self):
        """Set the implicit wait to 0 for the block — element misses must be free."""
        try:
            self.wt.d.implicitly_wait(0)
        except Exception:
            logger.debug("could not clear the implicit wait", exc_info=True)
        try:
            yield
        finally:
            try:
                self.wt.d.implicitly_wait(self.wt.def_to)
            except Exception:
                logger.debug("could not restore the implicit wait", exc_info=True)

    def click_popups(self):
        """Close the interstitials that can cover the quote tables."""
        return self.dismiss_overlays()

    def click_player_close(self):
        """Close the auto-playing video player overlay."""
        return bool(self.click_first(['//*[@title="Close Player"]',
                                      '//*[@aria-label="Close Player"]'], label='player'))

    def click_close(self):
        """Close a generic modal overlay."""
        return bool(self.click_first(CLOSE_BUTTONS, label='close'))

    def click_escape(self):
        """Send ESC to dismiss overlays that have no close button."""
        try:
            self.call('escape', lambda: self.wt.get_action_chains()
                      .send_keys(self.wt.Keys.ESCAPE).perform())
        except DriverTimeout:
            raise
        except Exception:
            logger.debug("escape key failed", exc_info=True)

    # -- reading --------------------------------------------------------------

    def read_table(self, headline):
        """Return the quote table below `headline` as a list of cell-text rows.

        Uses one JS round-trip and falls back to the WebDriver element walk when
        the script returns nothing (e.g. because the DOM is still building).
        """
        xpath = self.layout['tbody_xpath'].format(headline=headline)
        rows = self.js(_TABLE_JS, xpath, default=None, label='read_table')
        if rows:
            return rows

        rows = []
        try:
            with self.no_implicit_wait():
                # No WebDriverWait here: a missing table is answered by the
                # overlay/recovery logic, not by blocking the cycle.
                bodies = self.call('find_table', self.wt.d.find_elements,
                                   self.wt.By.XPATH, xpath) or []
                if not bodies:
                    return []
                for tr in bodies[0].find_elements(self.wt.By.TAG_NAME, 'tr'):
                    cells = tr.find_elements(self.wt.By.TAG_NAME, 'td')
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

            # Index the table once by its name column instead of re-scanning it
            # for every symbol (was O(symbols x rows) with an exception per miss).
            # Keys are casefolded: the site writes "TecDAX" where the config says
            # "TecDax", and such casing changes must not drop a symbol silently.
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
                stamp = parse_time(cells[time_col])
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
        """Run the next step of the escalating recovery ladder.

        1. close overlays  2. reload  3. re-navigate + consent  4. restart browser.
        The step counter is reset by `reset_recovery()` after a healthy cycle.
        A driver timeout skips straight to the restart — the browser is already
        dead at that point.
        """
        self.recovery_step += 1
        step = self.recovery_step
        logger.warning("recovery step %s (%s)", step, reason or "unknown reason")
        try:
            if step == 1:
                self.dismiss_overlays()
            elif step == 2:
                self.call('refresh', self.wt.d.refresh)
                time.sleep(random.uniform(2, 4))
                self.dismiss_consent()
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

    def reload_page(self):
        """Reload the page and clear whatever the reload brings back."""
        logger.info("reloading the page (periodic refresh)")
        try:
            self.call('refresh', self.wt.d.refresh)
        except DriverTimeout:
            raise
        except Exception:
            logger.warning("reload failed", exc_info=True)
            return False
        time.sleep(random.uniform(2, 4))
        self.dismiss_overlays()
        return self.wait_until(self.has_content, timeout=PAGE_READY_TIMEOUT,
                               label='content after reload') is not None

    def reset_recovery(self):
        """Forget previous recovery attempts after a healthy cycle."""
        self.recovery_step = 0

    def restart(self):
        """Throw the browser away and open a fresh one on the ticker page."""
        logger.warning("restarting webdriver")
        self.quit()
        self.wt = web_tools.WebTools()
        if self.profile:
            self.wt.chromeOptions = self.profile_options(self.profile)
        self.wt.init_webdriver()
        self.setup_page()

    def quit(self):
        """Close the browser and free the driver process."""
        try:
            self.call('quit', self.wt.quit_webdriver, timeout=QUIT_TIMEOUT)
        except DriverTimeout:
            pass  # hard_kill already ran
        except Exception:
            logger.debug("quit_webdriver failed", exc_info=True)
            self.hard_kill()

    # -- kept for callers outside this module ---------------------------------

    def extract_and_convert_to_float(self, s):
        """Convert a scraped price string to float (0 when nothing parses)."""
        value = parse_number(s)
        return value if value is not None else 0

    def price_data(self, path=""):
        """Read a single price element by XPath and return it as a float."""
        text = self.wt.text_from_element(
            self.wt.check_exists(path, bytype=self.wt.By.XPATH, timeout=15))
        return self.extract_and_convert_to_float(text)

    def price_time(self, path=""):
        """Read a single quote-time element by XPath."""
        return self.wt.text_from_element(
            self.wt.check_exists(path, bytype=self.wt.By.XPATH, timeout=15))


class TradingApp:
    """Collector loop: scrape → validate → forward changed quotes to the app."""

    fmt = "%Y-%m-%d %H:%M:%S"
    config_file = 'config.yaml'
    url = "/?symbol="

    def __init__(self, username='admin', messages_only=False, allow_notify=False,
                 page="/realtimekurse", fetch_type='indices',
                 ignore_schedule=False, dry_run=False, target='', transport='auto'):
        """Set up config, the target app browser and the source page browser."""
        self.title = "The Trading Tools"
        self.authentication_status = None
        self.username = username
        self.fetch_type = fetch_type
        self.page = '/realtimekurse' if fetch_type == 'indices' else page
        self.layout = TABLE_LAYOUTS['indices' if fetch_type == 'indices' else 'members']
        self.symbols = INDICES if fetch_type == 'indices' else DAX_MEMBERS
        self.messages_only = messages_only
        self.is_admin = False
        self.allow_notify = allow_notify
        self.ignore_schedule = ignore_schedule
        self.dry_run = dry_run
        if ignore_schedule:
            logger.warning("schedule override active — collecting outside trading hours "
                           "(quotes will be stale, indices do not move at weekends)")
        if dry_run:
            logger.warning("dry run — scraped quotes are logged, nothing is sent to the app")
        (self.target_protocol, self.target_url,
         self.target_port, self.target_path) = split_target(target)
        logger.info("streaming ticks to %s%s%s%s", self.target_protocol, self.target_url,
                    self.target_port, self.target_path)
        self.sys_config = sysconf.SystemConfig(username=self.username)
        self.rt_prices = self.sys_config.get_value('rt_prices', False)
        self.platform = sys.platform
        self.transport = transport if transport in ('auto', 'ws', 'browser') else 'auto'
        self.dl = None
        self.target = None
        self.wf = None
        self.lt = None
        self.stream = None

        # Accepted state per symbol: {"price": float, "time": str}.
        self.last_sent = {}
        self.pending = {}           # candidate outliers awaiting confirmation
        self.stale_reported = set()  # symbols already reported as stale
        self.last_reload = None      # when the page was last refreshed
        self.stall_cycles = 0
        self.refresh_counter = 0

        # Browsers are opened lazily by ensure_browsers(): outside trading hours
        # the collector must not touch the site at all — and it must not park two
        # idle Chrome instances over the weekend either.
        if self.rt_prices:
            self.lt = live_ticker.LiveTicker(username=username)

    # -- browser lifecycle ----------------------------------------------------

    def ensure_browsers(self):
        """Open the browsers on demand; returns the source WebFetch (or None).

        Called from the first cycle of a trading session — never while the
        markets are closed, so no page is fetched on a Saturday.
        """
        if self.messages_only:
            return None
        if self.dl is None and self.allow_notify:
            self.dl = file_provider.DownloadProvider()
        # The target browser is only needed for the fallback transport — the
        # websocket route does not open a browser at all.
        if self.target is None and not self.dry_run and self.transport == 'browser':
            self.target = WebFetch(protocol=self.target_protocol, url=self.target_url,
                                   port=self.target_port, page=self.target_path,
                                   autosetup=False, profile='target')
        if self.wf is None:
            logger.info("opening the source page finanzen.net%s", self.page)
            self.wf = WebFetch(protocol='https://', url='finanzen.net', page=self.page,
                               layout=self.layout, profile='source')
        return self.wf

    def close_browsers(self):
        """Shut the browsers down while the markets are closed."""
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        for name in ('wf', 'target'):
            holder = getattr(self, name)
            if holder is not None:
                holder.quit()
                setattr(self, name, None)
        if self.dl is not None:
            try:
                self.dl.close_driver()
            except Exception:
                logger.debug("closing download driver failed", exc_info=True)
            self.dl = None
        # A fresh session re-sends every quote once, so change detection cannot
        # suppress the first ticks of the day.
        self.last_sent = {}
        self.pending = {}
        self.stale_reported = set()
        self.last_reload = None

    # -- schedule -------------------------------------------------------------

    @staticmethod
    def is_trading_time(now=None):
        """True inside the regular collection window (weekdays, 06:00-21:59:30)."""
        now = now or dt.datetime.now()
        return now.weekday() <= 4 and START_TIME <= now.time() <= END_TIME

    def in_session(self, now=None):
        """True when the collector may query the website.

        /anytime lifts the schedule entirely (weekend *and* time-of-day) so the
        scraper can be exercised outside trading hours.
        """
        if self.ignore_schedule:
            return True
        return self.is_trading_time(now)

    @staticmethod
    def seconds_until_session(now=None):
        """Seconds until the next session starts, capped at IDLE_SLEEP.

        The cap keeps the loop responsive (day rollover, cleanup, Ctrl+C) while
        idling — the weekend is spent sleeping, not polling.
        """
        now = now or dt.datetime.now()
        candidate = dt.datetime.combine(now.date(), START_TIME)
        while candidate <= now or candidate.weekday() > 4:
            candidate = dt.datetime.combine(candidate.date() + dt.timedelta(days=1), START_TIME)
        return min(IDLE_SLEEP, max(1.0, (candidate - now).total_seconds()))

    # -- transport ------------------------------------------------------------

    def send_message(self, payload=None):
        """Push a {symbol: {price, time}} payload to the app's API endpoint.

        Preferred route is the websocket (no browser, and the app answers with
        the number of stored ticks). Falls back to driving a browser to the URL
        when the websocket is unavailable — the protocol behind it is a
        Streamlit internal and may change with a Streamlit upgrade.
        """
        if self.messages_only:
            return False
        payload = payload or {}
        if not payload:
            return False

        if self.dry_run:
            logger.info("dry run — would send %s quotes: %s",
                        len(payload), self.price_line(payload))
            return True

        body = dict(payload)
        body['api_key'] = self.sys_config.get_value('api_key', '')

        if self.transport in ('auto', 'ws'):
            sent = self._send_via_websocket(body, len(payload))
            if sent is not None:
                return sent
            if self.transport == 'ws':
                return False
            logger.warning("falling back to the browser transport for this session")
            self.transport = 'browser'

        return self._send_via_browser(payload)

    def _send_via_websocket(self, body, expected):
        """Send over the Streamlit websocket. Returns None when unavailable."""
        try:
            if self.stream is None:
                self.stream = tick_stream.TickStreamClient(
                    tick_stream.websocket_url(self.target_protocol, self.target_url,
                                              self.target_port, self.target_path))
            stored, total = self.stream.send(body)
        except tick_stream.StreamUnavailable as exc:
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

    def _send_via_browser(self, payload):
        """Legacy route: navigate a browser to the ingest URL, in chunks."""
        if self.target is None:
            # Opened on demand — a fallback can happen mid-session.
            logger.info("opening the target browser for the fallback transport")
            self.target = WebFetch(protocol=self.target_protocol, url=self.target_url,
                                   port=self.target_port, page=self.target_path,
                                   autosetup=False, profile='target')
        api_key = self.sys_config.get_value('api_key', '')
        symbols = list(payload)
        sent = 0
        for start in range(0, len(symbols), CHUNK_SIZE):
            chunk = {s: payload[s] for s in symbols[start:start + CHUNK_SIZE]}
            chunk['api_key'] = api_key
            data = urllib.parse.quote(json.dumps(chunk, separators=(',', ':')))
            try:
                self.target.get(path=f"/?stream=api&data={data}")
                sent += len(chunk) - 1
            except Exception:
                logger.error("sending %s quotes failed", len(chunk) - 1, exc_info=True)
                return False
        logger.debug("sent %s quotes (browser transport, unconfirmed)", sent)
        return True

    # -- validation -----------------------------------------------------------

    def validate(self, quotes):
        """Filter scraped quotes down to values that are safe to store.

        Rejects implausible single-step jumps (> MAX_JUMP_PCT) unless the same
        value shows up twice in a row — that keeps parser glitches and stale
        columns out of the DB without swallowing real gaps.
        """
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

            # Report staleness once per episode — a closed market (Nikkei in the
            # European afternoon) would otherwise warn on every single cycle.
            age = quote_age_minutes(quote["time"])
            if age is not None and age > MAX_QUOTE_AGE_MIN:
                if symbol not in self.stale_reported:
                    self.stale_reported.add(symbol)
                    issues.append(f"{symbol}: quote is {age:.0f} min old")
            else:
                self.stale_reported.discard(symbol)
            accepted[symbol] = quote
        return accepted, issues

    def changed_quotes(self, quotes, now=None):
        """Return the quotes worth sending, with a usable timestamp.

        The receiving table is keyed on (timestamp, symbol) and written with
        INSERT OR REPLACE. A source that keeps serving the same clock time while
        the price moves therefore overwrites one and the same row instead of
        appending — measured on the FX rows, which repeated 06:59:48 and
        11:39:19 to the second across two days and collapsed a whole day of
        quotes into two points.

        So when the price moved but the source's clock did not, the observation
        time is used instead. An unchanged price *and* time is still skipped —
        that is the normal, wanted deduplication.
        """
        now = now or dt.datetime.now()
        changed = {}
        for symbol, quote in quotes.items():
            previous = self.last_sent.get(symbol)
            if previous is None or previous != quote:
                if previous and quote['time'] == previous['time']:
                    logger.debug("%s: source clock stuck at %s — stamping the observation time",
                                 symbol, quote['time'])
                    quote = dict(quote, time=now.strftime("%H:%M:%S"))
                changed[symbol] = quote
        return changed

    def price_line(self, quotes):
        """Format a one-line summary of the given quotes for the log."""
        parts = []
        for symbol, quote in quotes.items():
            name = self.symbols.get(symbol, {}).get("name", symbol)
            parts.append(f'{name} {quote["price"]} @ {quote["time"]}')
        return " - ".join(parts)

    # -- cycle ----------------------------------------------------------------

    def update_price(self):
        """Run one collection cycle: scrape, validate, forward, react to problems."""
        if self.messages_only:
            self.notify()
            time.sleep(random.uniform(30, 60))
            return

        # The consent wall can reappear at any time (new session cookie, CMP
        # re-prompt, after a reload) and it does NOT hide the table from the DOM
        # — scraping alone would never notice it while the site stops updating
        # behind it. One cheap probe per cycle closes that gap.
        self.wf.dismiss_overlays()

        if self.reload_due():
            self.wf.reload_page()
            self.last_reload = dt.datetime.now()

        quotes, issues = self.wf.scrape(self.symbols)
        coverage = len(quotes) / max(len(self.symbols), 1)
        for issue in issues:
            logger.warning("page issue: %s", issue)

        if coverage < MIN_COVERAGE:
            self.wf.recover(f"only {len(quotes)}/{len(self.symbols)} symbols readable")
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
                self.wf.recover("quotes frozen")
            return

        self.stall_cycles = 0
        if self.send_message(copy.deepcopy(changed)):
            # Only remember what actually reached the app — a failed send must be
            # retried on the next cycle, not swallowed by the change detection.
            self.last_sent.update(changed)
            self.wf.reset_recovery()
            self.refresh_counter += 1
            logger.info("%s", self.price_line(changed))
        else:
            logger.warning("%s quotes could not be delivered — retrying next cycle", len(changed))

        self.notify()

    def reload_due(self, now=None):
        """True when the page has been open longer than RELOAD_MINUTES.

        Sections of a long-lived page stop updating one by one; a reload is the
        only reliable cure and costs one page load per half hour.
        """
        now = now or dt.datetime.now()
        if self.last_reload is None:
            self.last_reload = now       # the page was just opened
            return False
        return (now - self.last_reload).total_seconds() >= RELOAD_MINUTES * 60

    def notify(self):
        """Refresh the local tick DB and fire the trend notification, if enabled."""
        if not (self.allow_notify or self.messages_only) or self.lt is None:
            return
        try:
            if self.dl is not None:
                self.dl.download()
            self.lt.render(bare_mode=True)
            self.lt.notifier(bare_mode=True)
        except Exception:
            logger.warning("notification step failed", exc_info=True)
            time.sleep(random.uniform(10, 20))

    def runner(self, once=False):
        """Collect quotes during trading hours until the day rolls over.

        Outside the session (nights, weekends, holidays as far as the clock can
        tell) nothing is requested and no browser stays open — the loop only
        wakes up often enough to notice the day rollover and run the cleanup.
        With once=True a single cycle is run and the browsers are closed again.
        """
        if not self.rt_prices:
            logger.info("rt_prices is disabled — nothing to collect")
            return

        start_day = dt.date.today()
        cleanup_day = None
        idle_logged = False

        while True:
            now = dt.datetime.now()
            if now.date() != start_day:
                logger.info("day changed — restarting collector")
                return

            # Only trading days get an archive — a weekend rollover would file
            # away a database that never received a tick. With the schedule
            # override the day is a working day by definition.
            if ((now.weekday() <= 4 or self.ignore_schedule)
                    and now.time() >= CLEANUP_TIME and cleanup_day != now.date()):
                cleanup_day = now.date()
                logger.info("running cleanup")
                try:
                    if self.lt is not None:
                        self.lt.cleanup()
                except Exception:
                    logger.warning("cleanup failed", exc_info=True)

            if not (self.in_session(now) or self.messages_only):
                if self.wf is not None or self.target is not None or self.stream is not None:
                    logger.info("markets are closed — closing the connections")
                    self.close_browsers()
                delay = self.seconds_until_session(now)
                if not idle_logged:
                    logger.info("idle until the next session (checking every %ss)", int(delay))
                    idle_logged = True
                time.sleep(delay)
                continue

            idle_logged = False
            try:
                self.ensure_browsers()
                self.update_price()
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.error("collection cycle failed", exc_info=True)
                if self.wf is None:
                    return
                try:
                    self.wf.recover("cycle raised")
                except Exception:
                    logger.error("recovery failed", exc_info=True)
                    return

            if once:
                logger.info("single cycle finished")
                return

            # Sleep the rest of the cycle instead of spinning on the clock.
            elapsed = (dt.datetime.now() - now).total_seconds()
            time.sleep(max(1.0, CYCLE_SECONDS - elapsed))

    def close(self):
        """Shut down every browser this instance owns."""
        self.close_browsers()


def parse_args(argv):
    """Parse the /flag CLI style used across the project."""
    opts = {
        'messages_only': False,   # don't scrape, only notify from local data
        'allow_notify': False,    # download the tick DB and send trend alerts
        'anytime': False,         # ignore the trading calendar (test outside hours)
        'dry_run': False,         # scrape and log, but send nothing to the app
        'once': False,            # run a single cycle, then exit
        'page': "/aktien/dax-realtimekurse",
        'fetch_type': 'indices',
        'user': 'kurt',
        'log': 'INFO',
        'target': DEFAULT_TARGET,   # where the ticks are streamed to
        'transport': 'auto',        # auto | ws | browser
    }
    flags = ('messages_only', 'allow_notify', 'anytime', 'dry_run', 'once')
    values = ('page', 'fetch_type', 'user', 'log', 'target', 'transport')
    for arg in argv[1:]:
        if not arg.startswith('/'):
            continue
        raw = arg[1:]
        # Split on the FIRST colon only and keep the value untouched — the
        # target carries its own scheme and port ("/target:http://host:8080").
        key, _, value = raw.partition(':')
        key = key.lower().replace('-', '_')
        if key in flags:
            opts[key] = True
        elif key in values and value:
            opts[key] = value
    return opts


if __name__ == "__main__":

    opts = parse_args(sys.argv)
    logging.basicConfig(level=getattr(logging, opts['log'].upper(), logging.INFO),
                        format="%(asctime)s | %(levelname)s | %(message)s")

    while True:
        app = None
        try:
            app = TradingApp(username=opts['user'],
                             messages_only=opts['messages_only'],
                             allow_notify=opts['allow_notify'],
                             page=opts['page'],
                             fetch_type=opts['fetch_type'],
                             ignore_schedule=opts['anytime'],
                             dry_run=opts['dry_run'],
                             target=opts['target'],
                             transport=opts['transport'])
            app.runner(once=opts['once'])
            if opts['once']:
                break
        except KeyboardInterrupt:
            logger.info("stopped by user")
            break
        except Exception:
            logger.error("collector crashed — restarting in 20s", exc_info=True)
            time.sleep(20)
        finally:
            if app is not None:
                app.close()
