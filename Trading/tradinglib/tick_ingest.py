"""Tick ingest endpoint (?stream=api) for the live ticker collector.

Streamlit exposes no HTTP route that runs the script, so the collector drives a
script run — either over the websocket (see tradinglib/tick_stream.py) or, as a
fallback, by navigating a browser to the URL. Both arrive here.

The handler is deliberately kept out of TradingApp: asset_analyzer calls it
BEFORE constructing the app, so a tick request does not pay for config loading,
the authenticator, CSS injection and the router. The response line is
machine-readable ("Success: 15/15") so the sender can verify delivery.
"""

import datetime as dt
import hmac
import json
import logging

import streamlit as st

from tradinglib import live_ticker as lt
from tradinglib import system_config as sysconf

logger = logging.getLogger(__name__)

# The tick database is rotated once a day, shortly before the collector stops.
CLEANUP_AFTER = "21:59:00"

# Machine-readable answers — the sender parses these.
OK_PREFIX = "Success:"
REJECTED = "Rejected"


def handle_request(params=None):
    """Handle a ?stream=api request. Returns True when it was one.

    Call this before building the app; a False result means "not my request,
    carry on with the normal routing".
    """
    if params is None:
        try:
            params = st.query_params.to_dict()
        except Exception:
            logger.debug("no query params available", exc_info=True)
            return False
    if params.get('stream') != 'api':
        return False

    handle_payload(params.get('data'))
    return True


def handle_payload(raw, sys_config=None):
    """Validate and store a tick payload; returns the number of stored ticks.

    Returns -1 when the request was rejected (bad JSON or wrong key). Nothing is
    created or archived before the key has been verified — an unauthenticated
    request must not be able to trigger the database rollover.
    """
    try:
        data = json.loads(raw or '{}')
        if not isinstance(data, dict):
            raise ValueError("payload is not an object")
    except (TypeError, ValueError):
        logger.warning("api stream: payload is not valid JSON")
        st.write(REJECTED)
        return -1

    sys_config = sys_config or sysconf.SystemConfig(username='api_key', bare_mode=True)
    if not api_key_is_valid(data.get('api_key'), sys_config):
        logger.warning("api stream: rejected request with invalid api key")
        st.write(REJECTED)
        return -1

    # The collector may sit on another host, so it cannot rotate this database
    # itself — its own cleanup only touches its local copy.
    if dt.datetime.now().strftime("%H:%M:%S") >= CLEANUP_AFTER:
        _daily_cleanup()

    ticks = [(quote.get('time'), symbol, quote.get('price'))
             for symbol, quote in data.items()
             if symbol != 'api_key' and isinstance(quote, dict)]
    stored = lt.LiveTicker.store_ticks(ticks)
    logger.info("api stream: stored %s of %s ticks", stored, len(ticks))
    st.write(f"{OK_PREFIX} {stored}/{len(ticks)}")
    return stored


def api_key_is_valid(presented, sys_config=None):
    """True when `presented` matches an api_key configured for any user.

    The endpoint is called without a login, so there is no session user whose
    namespaced '<user>:api_key' could be read — the collector runs as its own
    process, possibly on another host. Every configured api_key is therefore
    accepted; the comparison is constant-time because the key is a shared secret.
    """
    if not presented:
        return False
    sys_config = sys_config or sysconf.SystemConfig(username='api_key', bare_mode=True)
    try:
        configured = sys_config.find_values('api_key')
    except Exception as exc:
        logger.warning("api stream: could not read the configured api keys: %s", exc)
        return False
    return any(value and hmac.compare_digest(str(value), str(presented))
               for value in configured)


def _daily_cleanup():
    """Archive the tick database once per day (server side)."""
    try:
        ticker = lt.LiveTicker(init=True, username='api_key', days_back=1)
        ticker.cleanup()
    except Exception:
        logger.warning("api stream: cleanup failed", exc_info=True)
