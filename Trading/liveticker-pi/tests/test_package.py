"""Tests for the standalone collector package.

No browser, no network. The protobuf encoder is checked against streamlit's own
output when streamlit happens to be installed, and against golden bytes when it
is not — which is the normal case on a Raspberry Pi.
"""

import datetime as dt
import json
import types
import urllib.parse

import pytest

from liveticker import config, parsing, stream, symbols
from liveticker.collector import Collector
from liveticker.scraper import (CONSENT_BUTTONS, DISMISS_BUTTONS, FORBIDDEN_CLICK_TEXT,
                                TABLE_LAYOUTS, Scraper, _CLOSE_JS)


# --- parsing -----------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("24.004,02", 24004.02),
    ("24.004,02 PKT", 24004.02),
    ("1,1383", 1.1383),
    ("130,73 EUR", 130.73),
    ("1.234", 1234.0),
    ("24,004.02", 24004.02),
    ("-0,45 %", -0.45),
])
def test_parse_number(raw, expected):
    assert parsing.parse_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "  ", None, "n/a", "--", "PKT"])
def test_parse_number_rejects_junk(raw):
    assert parsing.parse_number(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("21:57:52", "21:57:52"),
    ("26.05.25 21:57:52", "21:57:52"),
    ("Kurs 09:05", "09:05:00"),
])
def test_parse_time(raw, expected):
    assert parsing.parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "26.05.25", "25:10:00"])
def test_parse_time_rejects_junk(raw):
    assert parsing.parse_time(raw) is None


def test_to_timestamp_converts_the_source_timezone():
    """A Pi on Canary time (UTC+1) reading MESZ (UTC+2) quotes.

    Without the conversion the quote looks an hour into the future, and a
    receiver that maps "future" to "yesterday" files a whole day one day early.
    """
    pytest.importorskip("zoneinfo")
    now = dt.datetime(2026, 8, 11, 12, 21, 40)          # local, Atlantic/Canary
    # The page prints 13:21:31 for a quote that just happened.
    stamp = parsing.to_timestamp("13:21:31", source_tz="Europe/Berlin", now=now)
    parsed = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")

    assert parsed.date() == now.date()                  # today, not yesterday
    assert abs((parsed - now).total_seconds()) < 300     # within minutes of now


def test_to_timestamp_without_a_zone_picks_the_closest_day():
    now = dt.datetime(2026, 8, 11, 0, 5, 0)
    # A quote from just before midnight belongs to the previous day.
    assert parsing.to_timestamp("23:58:00", now=now).startswith("2026-08-10")
    assert parsing.to_timestamp("00:03:00", now=now).startswith("2026-08-11")


def test_quote_age_accepts_full_timestamps():
    now = dt.datetime(2026, 8, 11, 12, 0, 0)
    assert parsing.quote_age_minutes("2026-08-11 11:30:00", now=now) == pytest.approx(30)
    assert parsing.quote_age_minutes("11:30:00", now=now) == pytest.approx(30)
    assert parsing.quote_age_minutes("nonsense", now=now) is None


# --- protobuf frame ----------------------------------------------------------

def test_encode_rerun_matches_the_golden_frame():
    # BackMsg(rerun_script=ClientState(query_string="ab")):
    #   field 11, wire type 2 -> 0x5a, len 4
    #     field 1, wire type 2 -> 0x0a, len 2, "ab"
    assert stream.encode_rerun("ab") == b'\x5a\x04\x0a\x02ab'


def test_encode_rerun_handles_long_and_unicode_payloads():
    frame = stream.encode_rerun("x" * 300)
    assert frame.startswith(b'\x5a')          # BackMsg.rerun_script
    assert b'x' * 300 in frame
    assert stream.encode_rerun("ä").endswith("ä".encode('utf-8'))


def test_encode_rerun_equals_streamlits_own_protobuf():
    """When streamlit is available, the hand-rolled frame must be identical."""
    pytest.importorskip("streamlit")
    from streamlit.proto.BackMsg_pb2 import BackMsg

    for query in ("stream=api&data=%7B%22x%22%3A1%7D", "a" * 500, "ä&=/"):
        message = BackMsg()
        message.rerun_script.query_string = query
        assert stream.encode_rerun(query) == message.SerializeToString()


@pytest.mark.parametrize("frame,expected", [
    (b"...Success: 15/15...", (15, 15)),
    (b"garbage Success:12 / 15 more", (12, 15)),
    (b"\x00\x01Rejected\x02", (-1, -1)),
    (b"nothing to see here", None),
    ("a string frame", None),
])
def test_parse_result(frame, expected):
    assert stream.parse_result(frame) == expected


def test_send_serialises_the_payload_into_the_query_string():
    client = stream.TickStreamClient("ws://localhost:8080/_stcore/stream")
    sent = []

    class _Socket:
        def send(self, data):
            sent.append(data)

        def recv(self, timeout=None):
            return b"Success: 1/1"

    client._ws = _Socket()
    assert client.send({"^GDAXI": {"price": 24004.02, "time": "10:00:00"},
                        "api_key": "s3cret"}) == (1, 1)

    frame = sent[0]
    query = frame[frame.index(b'stream=api'):].decode()
    payload = json.loads(urllib.parse.unquote(query.split('data=', 1)[1]))
    assert payload["^GDAXI"]["price"] == 24004.02
    # The key travels in the message body, not in a navigated URL.
    assert payload["api_key"] == "s3cret"


# --- configuration -----------------------------------------------------------

@pytest.mark.parametrize("target,expected", [
    ("http://localhost:8080", ('http://', 'localhost', ':8080', '')),
    ("localhost:8080", ('http://', 'localhost', ':8080', '')),
    ("192.168.1.10:8080", ('https://', '192.168.1.10', ':8080', '')),
    ("https://trading.example.com", ('https://', 'trading.example.com', '', '')),
    ("", ('http://', 'localhost', ':8080', '')),
])
def test_split_target(target, expected):
    assert config.split_target(target) == expected


def test_settings_precedence(tmp_path, monkeypatch):
    ini = tmp_path / "liveticker.ini"
    ini.write_text("[liveticker]\ntarget = http://from-file:8080\napi_key = file-key\n",
                   encoding='utf-8')

    monkeypatch.setenv('LIVETICKER_API_KEY', 'env-key')
    settings = config.load(path=str(ini))
    assert settings['target'] == 'http://from-file:8080'   # file wins over default
    assert settings['api_key'] == 'env-key'                # env wins over file

    settings = config.load(overrides={'api_key': 'cli-key'}, path=str(ini))
    assert settings['api_key'] == 'cli-key'                # CLI wins over env


def test_settings_parse_clock_and_flags(tmp_path):
    ini = tmp_path / "liveticker.ini"
    ini.write_text("[liveticker]\nheadless = no\nstart_time = 07:30\ncycle_seconds = 45\n",
                   encoding='utf-8')

    settings = config.load(path=str(ini))
    assert settings['headless'] is False
    assert settings['start_time'] == dt.time(7, 30)
    assert settings['cycle_seconds'] == 45


# --- scraping ----------------------------------------------------------------

class _FakeDriver:
    def __init__(self, rows_by_xpath):
        self.rows_by_xpath = rows_by_xpath

    def execute_script(self, script, *args):
        from liveticker import scraper as s
        if script == s._TABLE_JS:
            return self.rows_by_xpath.get(args[0])
        if script == s._HAS_TABLE_JS:
            return bool(self.rows_by_xpath.get(args[0]))
        return None

    def implicitly_wait(self, seconds):
        pass

    def find_elements(self, by, selector):
        return []


def _scraper(rows_by_xpath):
    obj = Scraper.__new__(Scraper)
    obj.layout = TABLE_LAYOUTS['indices']
    obj.call_timeout = 5
    obj.source_timezone = ''
    obj.recovery_step = 0
    from concurrent.futures import ThreadPoolExecutor
    obj._pool = ThreadPoolExecutor(max_workers=1)
    obj.browser = types.SimpleNamespace(
        d=_FakeDriver(rows_by_xpath), def_to=5,
        By=types.SimpleNamespace(XPATH='xpath', CSS_SELECTOR='css', TAG_NAME='tag'))
    return obj


def test_scrape_reads_the_quote_table():
    xpath = TABLE_LAYOUTS['indices']['tbody_xpath'].format(headline="Indikation auf Indizes")
    rows = [["DAX", "24.004,02", "+1,20", "+0,3 %", "XETRA", "26.05.25 21:57:52"],
            ["TecDAX", "4.074,48", "+8,00", "+0,2 %", "XETRA", "26.05.25 21:57:57"]]
    scraper = _scraper({xpath: rows})

    quotes, issues = scraper.scrape({
        "^GDAXI": {"name": "DAX", "headline": "Indikation auf Indizes"},
        # Config casing differs from the page on purpose.
        "^TECDAX": {"name": "TecDax", "headline": "Indikation auf Indizes"},
        "^SPX": {"name": "S&P 500", "headline": "Indikation auf Indizes"},
    })

    assert quotes["^GDAXI"]["price"] == pytest.approx(24004.02)
    # The collector resolves the date itself and sends a full timestamp.
    assert quotes["^GDAXI"]["time"].endswith("21:57:52")
    dt.datetime.strptime(quotes["^GDAXI"]["time"], "%Y-%m-%d %H:%M:%S")
    assert quotes["^TECDAX"]["price"] == pytest.approx(4074.48)
    assert any("S&P 500" in issue for issue in issues)


def test_scrape_reports_a_missing_table():
    scraper = _scraper({})
    quotes, issues = scraper.scrape({"^GDAXI": {"name": "DAX",
                                                "headline": "Indikation auf Indizes"}})
    assert quotes == {}
    assert issues == ["table missing: Indikation auf Indizes"]


# --- click policy ------------------------------------------------------------

class _Element:
    def __init__(self, text=''):
        self._text = text

    @property
    def text(self):
        return self._text

    def get_attribute(self, name):
        return None


@pytest.mark.parametrize("text,forbidden", [
    ("Ablehnen & abonnieren", True),          # 3,99 €/month
    ("BENACHRICHTIGUNGEN AKTIVIEREN", True),  # push opt-in
    ("Mit Contentpass einloggen", True),
    ("Weiter mit Google", True),
    ("Einwilligen & weiter", False),
    ("SPÄTER ENTSCHEIDEN", False),
])
def test_click_policy(text, forbidden):
    assert Scraper.is_forbidden(_Element(text)) is forbidden


def test_the_paid_and_opt_in_buttons_are_never_targeted():
    for path in CONSENT_BUTTONS + DISMISS_BUTTONS:
        assert 'abonnier' not in path.lower()
        assert 'aktivieren' not in path.lower()
    assert 'aktivieren' in FORBIDDEN_CLICK_TEXT
    assert 'FORBIDDEN' in _CLOSE_JS          # the JS click path guards as well


def test_dismiss_xpaths_are_case_insensitive():
    # Labels are frequently upper-cased by CSS, which XPath does not see.
    assert all('translate(' in path for path in DISMISS_BUTTONS)


# --- collector logic ---------------------------------------------------------

def _collector(**overrides):
    settings = config.load(overrides={'api_key': 'k', **overrides})
    collector = Collector.__new__(Collector)
    collector.settings = settings
    collector.symbols = symbols.INDICES
    collector.last_sent = {}
    collector.pending = {}
    collector.stale_reported = set()
    collector.ignore_schedule = False
    return collector


def test_validate_holds_back_an_unconfirmed_jump():
    collector = _collector()
    fresh = dt.datetime.now().strftime("%H:%M:%S")
    collector.last_sent = {"^GDAXI": {"price": 24000.0, "time": fresh}}

    accepted, issues = collector.validate({"^GDAXI": {"price": 2400.0, "time": fresh}})
    assert accepted == {}
    assert any("jump" in issue for issue in issues)

    # The same value on the next cycle is a real move, not a parser glitch.
    accepted, _ = collector.validate({"^GDAXI": {"price": 2400.0, "time": fresh}})
    assert accepted["^GDAXI"]["price"] == 2400.0


def test_changed_quotes_only_returns_updates():
    collector = _collector()
    collector.last_sent = {"^GDAXI": {"price": 24000.0, "time": "10:00:00"}}

    assert collector.changed_quotes({"^GDAXI": {"price": 24000.0, "time": "10:00:00"}}) == {}
    assert collector.changed_quotes({"^GDAXI": {"price": 24001.0, "time": "10:00:20"}})


def test_schedule_skips_the_weekend():
    collector = _collector()
    saturday = dt.datetime(2026, 8, 8, 10, 0, 0)
    monday = dt.datetime(2026, 8, 3, 10, 0, 0)

    assert collector.is_trading_time(monday) is True
    assert collector.is_trading_time(saturday) is False
    assert collector.in_session(saturday) is False

    collector.ignore_schedule = True
    assert collector.in_session(saturday) is True


def test_symbol_sets():
    assert symbols.for_type('indices') is symbols.INDICES
    assert symbols.for_type('members') is symbols.DAX_MEMBERS
    assert symbols.for_type('unknown') is symbols.INDICES
    # The page writes "TecDAX" — the config must match after casefolding.
    assert symbols.INDICES["^TECDAX"]["name"] == "TecDAX"


def test_a_stuck_source_clock_gets_the_observation_time():
    """The FX rows repeat the same clock time while the price moves.

    The receiving table is keyed on (timestamp, symbol), so a repeating time
    overwrites one row instead of appending — a whole day collapses into a
    couple of points.
    """
    collector = _collector()
    collector.last_sent = {"EURUSD=X": {"price": 1.15350, "time": "2026-08-12 11:39:19"}}
    now = dt.datetime(2026, 8, 12, 11, 34, 51)

    changed = collector.changed_quotes(
        {"EURUSD=X": {"price": 1.15360, "time": "2026-08-12 11:39:19"}}, now=now)

    assert changed["EURUSD=X"]["time"] == "2026-08-12 11:34:51"
    # Unchanged price and time is still skipped.
    assert collector.changed_quotes(
        {"EURUSD=X": {"price": 1.15350, "time": "2026-08-12 11:39:19"}}, now=now) == {}


def test_reload_due_refreshes_a_long_open_page():
    collector = _collector()
    collector.last_reload = None
    start = dt.datetime(2026, 8, 12, 9, 0, 0)

    assert collector.reload_due(start) is False          # remembers the open time
    assert collector.reload_due(start + dt.timedelta(minutes=29)) is False
    assert collector.reload_due(start + dt.timedelta(minutes=30)) is True
