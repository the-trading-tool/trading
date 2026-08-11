"""Tests for the browser-free tick transport (sender + ingest endpoint)."""

import json
import types
import urllib.parse

import pytest

from tradinglib import tick_stream


# --- URL building ------------------------------------------------------------

@pytest.mark.parametrize("protocol,host,port,path,expected", [
    ('http://', 'localhost', ':8080', '', 'ws://localhost:8080/_stcore/stream'),
    ('https://', 'trading.cloogidoo.com', '', '',
     'wss://trading.cloogidoo.com/_stcore/stream'),
    ('http://', 'localhost', ':8080', '/app', 'ws://localhost:8080/app/_stcore/stream'),
])
def test_websocket_url(protocol, host, port, path, expected):
    assert tick_stream.websocket_url(protocol, host, port, path) == expected


# --- protocol handling -------------------------------------------------------

class _FakeForwardMsg:
    """Stands in for the protobuf: the payload is already a dict."""

    def __init__(self):
        self.kind = None
        self.payload = None

    def ParseFromString(self, raw):
        message = json.loads(raw.decode())
        self.kind = message['kind']
        self.payload = message.get('text', '')

    def WhichOneof(self, _):
        return self.kind

    @property
    def delta(self):
        element = types.SimpleNamespace(
            markdown=types.SimpleNamespace(body=self.payload),
            HasField=lambda field: field == 'markdown')
        return types.SimpleNamespace(new_element=element)


def _forward(kind, text=''):
    return json.dumps({'kind': kind, 'text': text}).encode()


class _FakeSocket:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        if not self.replies:
            raise AssertionError("no more replies queued")
        return self.replies.pop(0)

    def close(self):
        self.closed = True


def _client(replies):
    client = tick_stream.TickStreamClient("ws://localhost:8080/_stcore/stream")
    client._ws = _FakeSocket(replies)
    return client


def test_send_reports_what_the_app_stored(monkeypatch):
    client = _client([_forward('delta', 'Success: 15/15'), _forward('script_finished')])
    monkeypatch.setattr(tick_stream, '_imports',
                        lambda: (None, _BackMsgStub, _FakeForwardMsg))

    assert client.send({"^GDAXI": {"price": 1.0, "time": "10:00:00"}}) == (15, 15)


def test_send_detects_a_partial_write(monkeypatch):
    client = _client([_forward('delta', 'Success: 12/15'), _forward('script_finished')])
    monkeypatch.setattr(tick_stream, '_imports',
                        lambda: (None, _BackMsgStub, _FakeForwardMsg))

    assert client.send({"^GDAXI": {"price": 1.0, "time": "10:00:00"}}) == (12, 15)


def test_send_detects_a_rejected_payload(monkeypatch):
    client = _client([_forward('delta', 'Rejected'), _forward('script_finished')])
    monkeypatch.setattr(tick_stream, '_imports',
                        lambda: (None, _BackMsgStub, _FakeForwardMsg))

    assert client.send({"^GDAXI": {"price": 1.0, "time": "10:00:00"}}) == (-1, -1)


def test_send_puts_the_payload_into_the_query_string(monkeypatch):
    client = _client([_forward('script_finished')])
    monkeypatch.setattr(tick_stream, '_imports',
                        lambda: (None, _BackMsgStub, _FakeForwardMsg))

    client.send({"^GDAXI": {"price": 24004.02, "time": "10:00:00"}, "api_key": "s3cret"})

    query = json.loads(client._ws.sent[0].decode())
    assert query.startswith("stream=api&data=")
    payload = json.loads(urllib.parse.unquote(query.split('data=', 1)[1]))
    assert payload["^GDAXI"]["price"] == 24004.02
    # The key travels in the message body, not in a navigated URL.
    assert payload["api_key"] == "s3cret"


class _BackMsgStub:
    """Records the query string and serialises it as plain JSON."""

    def __init__(self):
        self.rerun_script = types.SimpleNamespace(query_string='')

    def SerializeToString(self):
        return json.dumps(self.rerun_script.query_string).encode()


def test_missing_dependencies_raise_stream_unavailable(monkeypatch):
    def _boom():
        raise tick_stream.StreamUnavailable("no websockets module")

    monkeypatch.setattr(tick_stream, '_imports', _boom)
    client = tick_stream.TickStreamClient("ws://localhost:8080/_stcore/stream")

    with pytest.raises(tick_stream.StreamUnavailable):
        client.send({"^GDAXI": {"price": 1.0, "time": "10:00:00"}})


def test_a_dropped_connection_is_retried_once(monkeypatch):
    attempts = {'n': 0}

    def _run(query_string):
        attempts['n'] += 1
        if attempts['n'] == 1:
            raise ConnectionResetError("server closed the session")
        return (15, 15)

    client = tick_stream.TickStreamClient("ws://localhost:8080/_stcore/stream")
    client._run_script = _run

    assert client.send({"^GDAXI": {"price": 1.0, "time": "10:00:00"}}) == (15, 15)
    assert attempts['n'] == 2
