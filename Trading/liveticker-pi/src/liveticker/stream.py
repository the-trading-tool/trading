"""Deliver ticks to the Trading app over Streamlit's own websocket.

Streamlit exposes no HTTP route that runs the script: a plain GET returns the
static index.html. The browser drives a script run over ``/_stcore/stream`` by
sending a ``BackMsg`` with ``rerun_script.query_string`` — this module speaks
that protocol directly, so no browser is needed to deliver a tick batch.

**No streamlit dependency.** The one message we send is encoded by hand: a
``BackMsg`` carrying a ``ClientState`` is nothing but two nested
length-delimited protobuf fields, and the reply is matched as raw bytes. That
keeps this package installable on a Raspberry Pi without pulling in streamlit,
pandas and pyarrow. The two field numbers below are the whole contract; they
were verified byte-for-byte against streamlit's own protobuf output.

⚠ ``/_stcore/stream`` is a Streamlit internal, not a public API. Every failure
mode raises StreamUnavailable so the caller can fall back to a browser.
"""

import json
import logging
import re
import time
import urllib.parse

logger = logging.getLogger(__name__)

# Streamlit's frontend announces itself with this subprotocol pair; the token is
# a placeholder for deployments without host-level auth.
SUBPROTOCOLS = ["streamlit", "PLACEHOLDER_AUTH_TOKEN"]
STREAM_PATH = "/_stcore/stream"

# Protobuf field numbers — the complete wire contract of this module.
BACKMSG_RERUN_SCRIPT = 11        # BackMsg.rerun_script  (ClientState)
CLIENTSTATE_QUERY_STRING = 1     # ClientState.query_string (string)

CONNECT_TIMEOUT = 20
RESPONSE_TIMEOUT = 30

_RESULT_RE = re.compile(rb"Success:\s*(\d+)\s*/\s*(\d+)")
_REJECTED_RE = re.compile(rb"Rejected")


class StreamUnavailable(Exception):
    """The websocket transport cannot be used (import, connect or protocol error)."""


# --- minimal protobuf encoding ----------------------------------------------

def _varint(value):
    """Encode an unsigned integer as a protobuf varint."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _length_delimited(field, payload):
    """Encode one length-delimited (wire type 2) protobuf field."""
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def encode_rerun(query_string):
    """Build the BackMsg frame that asks for a script run with `query_string`.

    Byte-identical to ``BackMsg(rerun_script=ClientState(query_string=…))``
    produced by streamlit's own protobuf classes.
    """
    query = query_string.encode('utf-8')
    client_state = _length_delimited(CLIENTSTATE_QUERY_STRING, query)
    return _length_delimited(BACKMSG_RERUN_SCRIPT, client_state)


def parse_result(frame):
    """Read the app's answer out of a raw ForwardMsg frame.

    Returns (stored, total), (-1, -1) when the app rejected the payload, or None
    when this frame carries no verdict. Matching raw bytes avoids having to
    parse the full ForwardMsg — the status line is plain ASCII inside it.
    """
    if not isinstance(frame, (bytes, bytearray)):
        return None
    match = _RESULT_RE.search(frame)
    if match:
        return int(match.group(1)), int(match.group(2))
    if _REJECTED_RE.search(frame):
        return -1, -1
    return None


def websocket_url(protocol, host, port='', path=''):
    """Build the ws(s) URL of the Streamlit stream endpoint."""
    scheme = 'wss' if str(protocol).startswith('https') else 'ws'
    return f"{scheme}://{host}{port}{path}{STREAM_PATH}"


class TickStreamClient:
    """Keeps one websocket open and asks for a script run per payload."""

    def __init__(self, url, connect_timeout=CONNECT_TIMEOUT,
                 response_timeout=RESPONSE_TIMEOUT):
        """Store the endpoint; the connection is opened on the first send."""
        self.url = url
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self._ws = None

    def connect(self):
        """Open the websocket if it is not open yet."""
        if self._ws is not None:
            return self._ws
        try:
            from websockets.sync.client import connect
        except Exception as exc:
            raise StreamUnavailable(f"websockets is not installed: {exc}") from exc
        try:
            self._ws = connect(self.url, subprotocols=SUBPROTOCOLS,
                               open_timeout=self.connect_timeout, max_size=None)
        except Exception as exc:
            self._ws = None
            raise StreamUnavailable(f"could not connect to {self.url}: {exc}") from exc
        logger.info("tick stream connected to %s", self.url)
        return self._ws

    def close(self):
        """Close the websocket, ignoring errors."""
        if self._ws is None:
            return
        try:
            self._ws.close()
        except Exception:
            logger.debug("closing the tick stream failed", exc_info=True)
        finally:
            self._ws = None

    def send(self, payload, retry=True):
        """Send a {symbol: {price, time}} payload and return (stored, total).

        Returns (-1, -1) when the app rejected the request (bad key/JSON) and
        (None, None) when no verdict arrived. A dropped connection is retried
        once, since the server recycles idle sessions.
        """
        query = "stream=api&data=" + urllib.parse.quote(
            json.dumps(payload, separators=(',', ':')))
        try:
            return self._run_script(query)
        except StreamUnavailable:
            raise
        except Exception as exc:
            self.close()
            if not retry:
                raise StreamUnavailable(f"sending failed: {exc}") from exc
            logger.warning("tick stream dropped (%s) — reconnecting once", exc)
            return self.send(payload, retry=False)

    def _run_script(self, query_string):
        """Ask the server to run the script and wait for its verdict.

        The app sends a burst of frames per run; only one carries the status
        line. The overall deadline is shared across them, so a chatty page
        cannot stretch the wait indefinitely.
        """
        ws = self.connect()
        ws.send(encode_rerun(query_string))

        deadline = time.monotonic() + self.response_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("no verdict within %ss", self.response_timeout)
                return (None, None)
            try:
                frame = ws.recv(timeout=remaining)
            except TimeoutError:
                return (None, None)
            result = parse_result(frame)
            if result is not None:
                return result
