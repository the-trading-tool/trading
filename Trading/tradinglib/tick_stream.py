"""Send tick payloads to the Trading app without a browser.

Streamlit has no HTTP ingest route: a plain GET returns the static index.html
and never runs the script — which is why the collector used to drive a second
Chrome. The browser, however, talks to the server over the websocket at
``/_stcore/stream`` and asks for a script run with ``BackMsg.rerun_script``.
This module speaks that protocol directly, so the collector can stream from a
second host with no browser at all, and it *reads the answer back* — the browser
transport navigates blindly and never learns whether the ticks arrived.

⚠ BackMsg/ForwardMsg and /_stcore/stream are Streamlit internals, not a public
API. Every failure mode raises StreamUnavailable so the caller can fall back to
the browser transport; nothing here should ever take the collector down.
"""

import json
import logging
import re
import urllib.parse

logger = logging.getLogger(__name__)

# Streamlit's frontend announces itself with this subprotocol pair; the token is
# a placeholder for deployments without host-level auth.
SUBPROTOCOLS = ["streamlit", "PLACEHOLDER_AUTH_TOKEN"]
STREAM_PATH = "/_stcore/stream"

CONNECT_TIMEOUT = 20
RESPONSE_TIMEOUT = 30

_RESULT_RE = re.compile(r"Success:\s*(\d+)\s*/\s*(\d+)")


class StreamUnavailable(Exception):
    """The websocket transport cannot be used (import, connect or protocol error)."""


def _imports():
    """Import the optional dependencies lazily; raises StreamUnavailable."""
    try:
        from websockets.sync.client import connect
        from streamlit.proto.BackMsg_pb2 import BackMsg
        from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
        return connect, BackMsg, ForwardMsg
    except Exception as exc:                     # missing or incompatible version
        raise StreamUnavailable(f"websocket transport unavailable: {exc}") from exc


def websocket_url(protocol, host, port='', path=''):
    """Build the ws(s) URL of the Streamlit stream endpoint."""
    scheme = 'wss' if protocol.startswith('https') else 'ws'
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

    # -- connection -----------------------------------------------------------

    def connect(self):
        """Open the websocket if it is not open yet."""
        if self._ws is not None:
            return self._ws
        connect, _, _ = _imports()
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

    # -- sending --------------------------------------------------------------

    def send(self, payload, retry=True):
        """Send a {symbol: {price, time}} payload and return (stored, total).

        Returns (-1, -1) when the app rejected the request (bad key/JSON) and
        (None, None) when the answer could not be parsed. A dropped connection
        is retried once, since the server recycles idle sessions.
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
        """Ask the server to run the script with `query_string` and read the answer."""
        _, BackMsg, ForwardMsg = _imports()
        ws = self.connect()

        message = BackMsg()
        message.rerun_script.query_string = query_string
        ws.send(message.SerializeToString())

        return self._read_result(ws, ForwardMsg)

    def _read_result(self, ws, ForwardMsg):
        """Collect ForwardMsgs until the script finishes; parse the result line."""
        result = (None, None)
        while True:
            raw = ws.recv(timeout=self.response_timeout)
            if isinstance(raw, str):
                continue
            forward = ForwardMsg()
            try:
                forward.ParseFromString(raw)
            except Exception:
                continue

            kind = forward.WhichOneof("type")
            if kind == "delta":
                text = _element_text(forward.delta.new_element)
                if text:
                    match = _RESULT_RE.search(text)
                    if match:
                        result = (int(match.group(1)), int(match.group(2)))
                    elif text.strip().startswith("Rejected"):
                        result = (-1, -1)
            elif kind == "script_finished":
                return result


def _element_text(element):
    """Return the text of a ForwardMsg element, or '' for element types we ignore."""
    for field in ("markdown", "text"):
        try:
            if element.HasField(field):
                return getattr(getattr(element, field), "body", "") or ""
        except ValueError:
            continue
    return ""
