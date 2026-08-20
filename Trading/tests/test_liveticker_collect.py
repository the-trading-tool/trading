"""Tests for the live ticker collector's parsing and quality gates.

These cover the pure helpers only — no browser, no network, no database.
"""

import datetime as dt
import importlib.util
import os
import sys
import types
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_liveticker():
    """Import liveticker.py with its heavy tradinglib imports stubbed out.

    The stubs are removed from sys.modules again once the module is loaded —
    liveticker keeps its own references, and the real tradinglib package stays
    importable for the other test modules regardless of execution order.
    """
    if 'liveticker' in sys.modules:
        return sys.modules['liveticker']

    injected = []
    attributes = []
    if 'tradinglib' not in sys.modules:
        stub = types.ModuleType('tradinglib')
        stub.__path__ = []
        sys.modules['tradinglib'] = stub
        injected.append('tradinglib')
    else:
        stub = sys.modules['tradinglib']

    for name in ('system_config', 'web_tools', 'live_ticker', 'file_provider', 'tick_stream'):
        if f'tradinglib.{name}' in sys.modules:
            continue
        module = types.ModuleType(f'tradinglib.{name}')
        sys.modules[f'tradinglib.{name}'] = module
        # Remember whether the real package already carried this attribute:
        # a leftover stub attribute would shadow the real submodule for every
        # later `from tradinglib import <name>` in the test session.
        attributes.append((name, getattr(stub, name, None)))
        setattr(stub, name, module)
        injected.append(f'tradinglib.{name}')

    try:
        spec = importlib.util.spec_from_file_location('liveticker',
                                                      os.path.join(ROOT, 'liveticker.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules['liveticker'] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name in injected:
            sys.modules.pop(name, None)
        for name, previous in attributes:
            if previous is None:
                if hasattr(stub, name):
                    delattr(stub, name)
            else:
                setattr(stub, name, previous)


lt = _load_liveticker()


@pytest.mark.parametrize("raw,expected", [
    ("24.004,02", 24004.02),      # German index quote
    ("24.004,02 PKT", 24004.02),
    ("1,1383", 1.1383),           # FX rate
    ("130,73 EUR", 130.73),
    ("3.343,87 USD", 3343.87),
    ("1.234", 1234.0),            # German thousands separator
    ("24,004.02", 24004.02),      # US format
    ("-0,45 %", -0.45),
    ("1 234,56", 1234.56),        # non-breaking space as thousands separator
    (1234.5, 1234.5),
])
def test_parse_number(raw, expected):
    assert lt.parse_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "  ", None, "n/a", "--", "PKT"])
def test_parse_number_rejects_junk(raw):
    assert lt.parse_number(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("21:57:52", "21:57:52"),
    ("26.05.25 21:57:52", "21:57:52"),
    ("Kurs 09:05", "09:05:00"),
    ("7:05:03", "07:05:03"),
])
def test_parse_time(raw, expected):
    assert lt.parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "26.05.25", "99:99:99", "25:10:00"])
def test_parse_time_rejects_junk(raw):
    assert lt.parse_time(raw) is None


def test_quote_age_minutes_wraps_over_midnight():
    now = dt.datetime(2026, 8, 8, 0, 10, 0)
    assert lt.quote_age_minutes("00:05:00", now=now) == pytest.approx(5)
    # A quote "in the future" belongs to the previous day.
    assert lt.quote_age_minutes("23:55:00", now=now) == pytest.approx(15)


class _FakeDriver:
    """Minimal stand-in for the Selenium driver.

    Serves canned tables and a canned overlay report, and records every script
    that was executed so the tests can assert what the collector tried.
    """

    def __init__(self, rows_by_xpath, overlay_report=None):
        self.rows_by_xpath = rows_by_xpath
        self.overlay_report = overlay_report or {}
        self.executed = []
        self.clicked = []

    def execute_script(self, script, *args):
        self.executed.append(script)
        if script == lt._TABLE_JS:
            return self.rows_by_xpath.get(args[0])
        if script == lt._HAS_TABLE_JS:
            return bool(self.rows_by_xpath.get(args[0]))
        if script == lt._OVERLAY_JS:
            return dict(self.overlay_report)
        if script == lt._CLOSE_JS:
            self.clicked.append('close-pass')
            self.overlay_report = {'blocked': False}
            return ['button.close']
        if script == lt._ONETAP_JS:
            self.clicked.append('onetap')
            self.overlay_report = {k: v for k, v in self.overlay_report.items()
                                   if k != 'signin'}
            self.overlay_report['blocked'] = bool(self.overlay_report.get('consent'))
            return ['api-cancel']
        return None

    def implicitly_wait(self, seconds):
        pass

    def find_elements(self, by, selector):
        return []


def _web_fetch(rows_by_xpath, layout='indices', overlay_report=None):
    fetch = lt.WebFetch.__new__(lt.WebFetch)
    fetch.layout = lt.TABLE_LAYOUTS[layout]
    fetch.call_timeout = 5
    fetch.recovery_step = 0
    fetch._pool = ThreadPoolExecutor(max_workers=1)
    fetch.wt = types.SimpleNamespace(d=_FakeDriver(rows_by_xpath, overlay_report),
                                     By=types.SimpleNamespace(XPATH='xpath',
                                                              CSS_SELECTOR='css',
                                                              TAG_NAME='tag'),
                                     def_to=5)
    return fetch


def _index_rows():
    return [
        ["DAX", "24.004,02", "+1,20", "+0,3 %", "XETRA", "26.05.25 21:57:52"],
        ["MDAX", "30.416,04", "-8,00", "-0,1 %", "XETRA", "26.05.25 21:57:54"],
    ]


def test_scrape_parses_and_reports_missing_rows():
    xpath = lt.TABLE_LAYOUTS['indices']['tbody_xpath'].format(headline="Indikation auf Indizes")
    fetch = _web_fetch({xpath: _index_rows()})
    symbols = {
        "^GDAXI": {"name": "DAX", "headline": "Indikation auf Indizes"},
        "^MDAXI": {"name": "MDAX", "headline": "Indikation auf Indizes"},
        "^SPX": {"name": "S&P 500", "headline": "Indikation auf Indizes"},
    }

    quotes, issues = fetch.scrape(symbols)

    assert quotes["^GDAXI"] == {"price": 24004.02, "time": "21:57:52"}
    assert quotes["^MDAXI"]["price"] == pytest.approx(30416.04)
    assert "^SPX" not in quotes
    assert any("S&P 500" in issue for issue in issues)


def test_scrape_reports_a_missing_table():
    fetch = _web_fetch({})
    symbols = {"^GDAXI": {"name": "DAX", "headline": "Indikation auf Indizes"}}

    quotes, issues = fetch.scrape(symbols)

    assert quotes == {}
    assert issues == ["table missing: Indikation auf Indizes"]


def test_scrape_skips_unparsable_cells():
    xpath = lt.TABLE_LAYOUTS['indices']['tbody_xpath'].format(headline="Indikation auf Indizes")
    rows = [["DAX", "-", "", "", "XETRA", "26.05.25 21:57:52"]]
    fetch = _web_fetch({xpath: rows})

    quotes, issues = fetch.scrape({"^GDAXI": {"name": "DAX", "headline": "Indikation auf Indizes"}})

    assert quotes == {}
    assert any("unparsable price" in issue for issue in issues)


def _readable_page(overlay_report=None):
    """A fetch whose probe table is present — i.e. the data is readable."""
    xpath = lt.TABLE_LAYOUTS['indices']['tbody_xpath'].format(
        headline=lt.TABLE_LAYOUTS['indices']['probe_headline'])
    return _web_fetch({xpath: _index_rows()}, overlay_report=overlay_report)


def test_dismiss_overlays_does_nothing_on_a_clean_page():
    fetch = _readable_page({'blocked': False})

    assert lt.WebFetch.dismiss_overlays(fetch) is True
    assert fetch.wt.d.clicked == []


def test_dismiss_overlays_ignores_harmless_ad_layers_while_data_is_readable():
    # finanzen.net permanently carries a full-viewport ad iframe with the maximum
    # z-index; clicking at it every cycle would be pure self-harm.
    fetch = _readable_page({'blocked': True, 'scrollLocked': True, 'covered': True,
                            'overlays': [{'el': 'iframe', 'z': 2147483647,
                                          'area': 1049076}]})

    assert lt.WebFetch.dismiss_overlays(fetch) is True
    assert fetch.wt.d.clicked == []


def test_dismiss_overlays_closes_a_blocker_when_the_table_is_gone():
    fetch = _web_fetch({}, overlay_report={'blocked': True,
                                           'overlays': [{'el': 'div#ad', 'z': 9999}]})

    lt.WebFetch.dismiss_overlays(fetch)

    assert 'close-pass' in fetch.wt.d.clicked
    # Probed again afterwards to check whether the page became usable.
    assert fetch.wt.d.executed.count(lt._OVERLAY_JS) >= 2


def test_dismiss_overlays_handles_the_google_one_tap_prompt_even_if_data_is_readable():
    fetch = _readable_page({'blocked': True, 'signin': 'div#onetap-container'})

    assert lt.WebFetch.dismiss_overlays(fetch) is True
    assert 'onetap' in fetch.wt.d.clicked


def test_dismiss_signin_uses_the_close_button_and_cancel_api():
    fetch = _readable_page({'blocked': True, 'signin': 'div#onetap-container'})

    assert lt.WebFetch.dismiss_signin(fetch) is True
    assert lt._ONETAP_JS in fetch.wt.d.executed
    # The real page's close button and the official cancel API are both used.
    assert 'pseudo-google-one-tap-close' in lt._ONETAP_JS
    assert 'google.accounts.id.cancel()' in lt._ONETAP_JS


def test_has_content_detects_a_rendered_table():
    xpath = lt.TABLE_LAYOUTS['indices']['tbody_xpath'].format(
        headline=lt.TABLE_LAYOUTS['indices']['probe_headline'])
    assert lt.WebFetch.has_content(_web_fetch({xpath: _index_rows()})) is True
    assert lt.WebFetch.has_content(_web_fetch({})) is False


def test_consent_buttons_target_the_accept_button_of_the_first_wall():
    # The consent choice is a user decision — keep it pinned in a test.
    # finanzen.net's wall is the Contentpass first layer: "Einwilligen & weiter".
    assert 'Einwilligen & weiter' in lt.CONSENT_BUTTONS[0]
    assert any('Alle akzeptieren' in path for path in lt.CONSENT_BUTTONS)
    # No positional XPath any more: on this dialog a blind position could land
    # on "Ablehnen & abonnieren" (a paid subscription).
    assert not any('div[3]' in path for path in lt.CONSENT_BUTTONS)
    for path in lt.CONSENT_BUTTONS:
        assert 'abonnier' not in path.lower()


class _Element:
    """Stand-in for a Selenium WebElement."""

    def __init__(self, text='', attrs=None, displayed=True):
        self._text = text
        self._attrs = attrs or {}
        self._displayed = displayed
        self.clicks = 0

    @property
    def text(self):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def is_displayed(self):
        return self._displayed

    def click(self):
        self.clicks += 1


@pytest.mark.parametrize("text", [
    "Ablehnen & abonnieren",
    "Mit Contentpass einloggen",
    "Jetzt anmelden",
    "Weiter mit Google",
    "BENACHRICHTIGUNGEN AKTIVIEREN",
    "Benachrichtigungen aktivieren",
])
def test_click_first_refuses_subscribe_and_login_controls(text):
    fetch = _web_fetch({})
    element = _Element(text)
    fetch.wt.d.find_elements = lambda by, path: [element]

    assert lt.WebFetch.click_first(fetch, ['//button'], label='consent') == ''
    assert element.clicks == 0


def test_click_first_presses_the_accept_button():
    fetch = _web_fetch({})
    element = _Element("Einwilligen & weiter")
    fetch.wt.d.find_elements = lambda by, path: [element]

    assert lt.WebFetch.click_first(fetch, ['//button'], label='consent') == '//button'
    assert element.clicks == 1


class _UnreadableElement(_Element):
    """An element whose label cannot be read — must never be clicked."""

    @property
    def text(self):
        raise RuntimeError("stale element")


def test_forbidden_click_text_covers_the_paid_option():
    assert lt.WebFetch._is_forbidden(_Element("Ablehnen & abonnieren")) is True
    assert lt.WebFetch._is_forbidden(_Element("BENACHRICHTIGUNGEN AKTIVIEREN")) is True
    assert lt.WebFetch._is_forbidden(_Element("Einwilligen & weiter")) is False
    assert lt.WebFetch._is_forbidden(_Element("SPÄTER ENTSCHEIDEN")) is False
    assert lt.WebFetch._is_forbidden(_UnreadableElement()) is True


def test_dismiss_overlays_declines_an_opt_in_prompt_even_if_data_is_readable():
    # The push-notification dialog does not hide the table, but it must go —
    # and the opt-in button next to "Später entscheiden" must never be pressed.
    fetch = _readable_page({'blocked': True,
                            'prompt': {'el': 'button.btn', 'label': 'später entscheiden'}})
    fetch.wt.d.find_elements = lambda by, path: []      # XPath route finds nothing

    assert lt.WebFetch.dismiss_overlays(fetch) is True
    assert 'close-pass' in fetch.wt.d.clicked           # generic pass took over


def test_dismiss_prompt_prefers_the_decline_button():
    fetch = _readable_page({'blocked': True, 'prompt': {'label': 'später entscheiden'}})
    element = _Element("SPÄTER ENTSCHEIDEN")
    fetch.wt.d.find_elements = lambda by, path: [element]

    assert lt.WebFetch.dismiss_prompt(fetch, {'label': 'später entscheiden'}) is True
    assert element.clicks == 1


def test_dismiss_xpaths_are_case_insensitive_and_avoid_the_opt_in():
    # Labels are frequently upper-cased by CSS, which XPath does not see.
    assert all('translate(' in path for path in lt.DISMISS_BUTTONS)
    for path in lt.DISMISS_BUTTONS:
        assert 'aktivieren' not in path.lower()
        assert 'ablehnen' not in path.lower()   # that is the paid Contentpass option
    # Both click paths know the opt-in vocabulary.
    assert 'aktivieren' in lt.FORBIDDEN_CLICK_TEXT
    assert 'aktivieren' in lt._CLOSE_JS and 'FORBIDDEN' in lt._CLOSE_JS


def test_probe_reports_the_contentpass_wall_as_consent_not_as_an_ad():
    # The wall has no id and no class; only src and geometry identify it.
    # Pinning the JS rules keeps the regression from coming back.
    assert 'first-layer' in lt._OVERLAY_JS
    assert 'cp.finanzen.net' in lt._OVERLAY_JS
    assert 'z >= 1000000' in lt._OVERLAY_JS


def test_consent_frames_finds_the_wall_by_src():
    fetch = _web_fetch({})
    marker = object()
    calls = []

    def _find(by, selector):
        calls.append(selector)
        return [marker] if 'first-layer' in selector else []

    fetch.wt.d.find_elements = _find
    frames = lt.WebFetch.consent_frames(
        fetch, {'src': 'https://cp.finanzen.net/first-layer/?start', 'frame': '', 'index': 3})

    assert frames == [marker]
    assert any('first-layer' in selector for selector in calls)


def test_consent_frames_falls_back_to_the_dom_index():
    fetch = _web_fetch({})
    frames = ['a', 'b', 'c', 'd']
    fetch.wt.d.find_elements = lambda by, selector: frames if selector == 'iframe' else []

    found = lt.WebFetch.consent_frames(fetch, {'src': '', 'frame': '', 'index': 2})

    assert found == ['c']


def test_the_scraper_never_targets_the_google_sign_in_button():
    for path in lt.CONSENT_BUTTONS + lt.CLOSE_BUTTONS:
        assert 'Google' not in path
    assert 'Weiter mit Google' not in lt._CLOSE_JS
    assert 'signin' not in lt._CLOSE_JS


def _collector():
    app = lt.TradingApp.__new__(lt.TradingApp)
    app.symbols = lt.INDICES
    app.last_sent = {}
    app.pending = {}
    app.stale_reported = set()
    return app


def test_validate_holds_back_an_unconfirmed_jump():
    app = _collector()
    app.last_sent = {"^GDAXI": {"price": 24000.0, "time": "10:00:00"}}

    accepted, issues = app.validate({"^GDAXI": {"price": 2400.0, "time": "10:00:20"}})

    assert accepted == {}
    assert any("jump" in issue for issue in issues)

    # The same value on the next cycle is a real move, not a parser glitch.
    accepted, _ = app.validate({"^GDAXI": {"price": 2400.0, "time": "10:00:40"}})
    assert accepted["^GDAXI"]["price"] == 2400.0


def test_validate_passes_normal_moves():
    app = _collector()
    fresh = dt.datetime.now().strftime("%H:%M:%S")
    app.last_sent = {"^GDAXI": {"price": 24000.0, "time": fresh}}

    accepted, issues = app.validate({"^GDAXI": {"price": 24120.0, "time": fresh}})

    assert accepted["^GDAXI"]["price"] == 24120.0
    assert issues == []


def test_validate_reports_a_stale_quote_but_keeps_it():
    app = _collector()
    stale = (dt.datetime.now() - dt.timedelta(hours=3)).strftime("%H:%M:%S")

    accepted, issues = app.validate({"^GDAXI": {"price": 24000.0, "time": stale}})

    assert accepted["^GDAXI"]["price"] == 24000.0
    assert any("old" in issue for issue in issues)


def test_a_stuck_source_clock_gets_the_observation_time():
    """Measured on the FX rows: the same second, twice, on two different days.

    The tick table is keyed on (timestamp, symbol) and written with INSERT OR
    REPLACE — a repeating source clock therefore overwrites one row while the
    price moves, collapsing a whole day into a couple of points.
    """
    app = _collector()
    app.last_sent = {"EURUSD=X": {"price": 1.15350, "time": "11:39:19"}}
    now = dt.datetime(2026, 8, 12, 11, 34, 51)

    changed = app.changed_quotes({"EURUSD=X": {"price": 1.15360, "time": "11:39:19"}}, now=now)

    assert changed["EURUSD=X"]["price"] == 1.15360
    assert changed["EURUSD=X"]["time"] == "11:34:51"      # observation time
    # An advancing source clock is left alone.
    app.last_sent = {"^GDAXI": {"price": 26000.0, "time": "11:30:00"}}
    moved = app.changed_quotes({"^GDAXI": {"price": 26010.0, "time": "11:31:00"}}, now=now)
    assert moved["^GDAXI"]["time"] == "11:31:00"


def test_an_unchanged_quote_is_still_skipped():
    app = _collector()
    app.last_sent = {"EURUSD=X": {"price": 1.15350, "time": "11:39:19"}}

    assert app.changed_quotes({"EURUSD=X": {"price": 1.15350, "time": "11:39:19"}}) == {}


def test_changed_quotes_only_returns_updates():
    app = _collector()
    app.last_sent = {"^GDAXI": {"price": 24000.0, "time": "10:00:00"}}

    same = app.changed_quotes({"^GDAXI": {"price": 24000.0, "time": "10:00:00"}})
    moved = app.changed_quotes({"^GDAXI": {"price": 24001.0, "time": "10:00:20"}})
    retick = app.changed_quotes({"^GDAXI": {"price": 24000.0, "time": "10:00:20"}})

    assert same == {}
    assert moved["^GDAXI"]["price"] == 24001.0
    assert retick["^GDAXI"]["time"] == "10:00:20"


def test_is_trading_time_covers_weekdays_only():
    monday = dt.datetime(2026, 8, 3, 10, 0, 0)      # Monday
    saturday = dt.datetime(2026, 8, 8, 10, 0, 0)    # Saturday
    sunday = dt.datetime(2026, 8, 9, 10, 0, 0)      # Sunday

    assert lt.TradingApp.is_trading_time(monday) is True
    assert lt.TradingApp.is_trading_time(saturday) is False
    assert lt.TradingApp.is_trading_time(sunday) is False
    assert lt.TradingApp.is_trading_time(monday.replace(hour=5, minute=59)) is False
    assert lt.TradingApp.is_trading_time(monday.replace(hour=22, minute=30)) is False


def test_anytime_switch_lifts_the_whole_schedule():
    app = lt.TradingApp.__new__(lt.TradingApp)
    saturday_night = dt.datetime(2026, 8, 8, 23, 30, 0)

    app.ignore_schedule = False
    assert app.in_session(saturday_night) is False

    app.ignore_schedule = True
    assert app.in_session(saturday_night) is True


def test_seconds_until_session_skips_the_weekend():
    # Saturday morning -> next start is Monday 06:00, so the nap is capped.
    saturday = dt.datetime(2026, 8, 8, 10, 0, 0)
    assert lt.TradingApp.seconds_until_session(saturday) == lt.IDLE_SLEEP

    # Just before the open the wait is the real remainder, not the cap.
    monday = dt.datetime(2026, 8, 3, 5, 58, 0)
    assert lt.TradingApp.seconds_until_session(monday) == pytest.approx(120)


class _StopLoop(Exception):
    """Raised from the patched sleep to break out of runner()'s while loop."""


def _idle_collector(ignore_schedule=False):
    app = lt.TradingApp.__new__(lt.TradingApp)
    app.rt_prices = True
    app.messages_only = False
    app.ignore_schedule = ignore_schedule
    app.dry_run = False
    app.lt = None
    app.wf = None
    app.target = None
    app.dl = None
    app.stream = None
    app.opened = False
    app.ensure_browsers = lambda: setattr(app, 'opened', True)
    return app


def test_runner_never_opens_a_browser_outside_the_session(monkeypatch):
    """The website must not be touched at all while the markets are closed."""
    app = _idle_collector()
    saturday = dt.datetime(2026, 8, 8, 10, 0, 0)

    class _FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return saturday

    monkeypatch.setattr(lt.dt, 'datetime', _FrozenDatetime)
    monkeypatch.setattr(lt.dt, 'date', types.SimpleNamespace(today=lambda: saturday.date()))

    def _stop(seconds):
        raise _StopLoop(seconds)

    monkeypatch.setattr(lt.time, 'sleep', _stop)

    with pytest.raises(_StopLoop) as excinfo:
        lt.TradingApp.runner(app)

    assert app.opened is False              # no page was ever requested
    assert excinfo.value.args[0] == lt.IDLE_SLEEP


def test_runner_with_anytime_collects_on_a_saturday(monkeypatch):
    """The /anytime switch is what makes an out-of-hours test run possible."""
    app = _idle_collector(ignore_schedule=True)
    saturday = dt.datetime(2026, 8, 8, 23, 30, 0)
    cycles = []
    app.update_price = lambda: cycles.append(1)

    class _FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return saturday

    monkeypatch.setattr(lt.dt, 'datetime', _FrozenDatetime)
    monkeypatch.setattr(lt.dt, 'date', types.SimpleNamespace(today=lambda: saturday.date()))
    monkeypatch.setattr(lt.time, 'sleep', lambda seconds: None)

    lt.TradingApp.runner(app, once=True)

    assert app.opened is True
    assert cycles == [1]                    # exactly one cycle thanks to /once


def test_dry_run_sends_nothing():
    app = lt.TradingApp.__new__(lt.TradingApp)
    app.messages_only = False
    app.dry_run = True
    app.target = None                       # no target browser is even opened
    app.symbols = lt.INDICES

    sent = lt.TradingApp.send_message(app, {"^GDAXI": {"price": 1.0, "time": "10:00:00"}})

    # Reported as delivered so the cycle continues, but nothing left the process.
    assert sent is True


@pytest.mark.parametrize("target,expected", [
    ("http://localhost:8080", ('http://', 'localhost', ':8080', '')),
    ("localhost:8080", ('http://', 'localhost', ':8080', '')),          # scheme inferred
    ("127.0.0.1:8501", ('http://', '127.0.0.1', ':8501', '')),
    ("https://trading.cloogidoo.com", ('https://', 'trading.cloogidoo.com', '', '')),
    ("trading.cloogidoo.com", ('https://', 'trading.cloogidoo.com', '', '')),
    ("http://localhost:8080/app/", ('http://', 'localhost', ':8080', '/app')),
    ("", ('http://', 'localhost', ':8080', '')),                        # default
])
def test_split_target(target, expected):
    assert lt.split_target(target) == expected


def test_the_default_target_is_the_local_app():
    # Production runs have to name the live instance explicitly.
    assert lt.DEFAULT_TARGET == "http://localhost:8080"
    assert lt.split_target() == ('http://', 'localhost', ':8080', '')


def test_parse_args_keeps_the_target_url_intact():
    # The value carries its own colons — only the first one separates the key.
    opts = lt.parse_args(["liveticker.py", "/target:http://localhost:8080"])
    assert opts['target'] == "http://localhost:8080"
    assert lt.parse_args(["liveticker.py"])['target'] == lt.DEFAULT_TARGET


def test_send_message_builds_the_url_for_a_local_target():
    app = lt.TradingApp.__new__(lt.TradingApp)
    app.messages_only = False
    app.dry_run = False
    app.transport = 'browser'
    app.stream = None
    app.symbols = lt.INDICES
    app.sys_config = types.SimpleNamespace(get_value=lambda key, default=None: 'secret')

    protocol, host, port, path = lt.split_target("http://localhost:8080")
    requested = []

    class _Target:
        def get(self, path='', page=''):
            requested.append(f"{protocol}{host}{port}{path}")

    app.target = _Target()

    assert lt.TradingApp.send_message(app, {"^GDAXI": {"price": 1.0, "time": "10:00:00"}})
    assert requested[0].startswith("http://localhost:8080/?stream=api&data=")
    assert 'secret' in urllib.parse.unquote(requested[0])


def test_parse_args_reads_the_test_switches():
    opts = lt.parse_args(["liveticker.py", "/anytime", "/dry_run", "/once"])

    assert opts['anytime'] is True
    assert opts['dry_run'] is True
    assert opts['once'] is True

    # Hyphens are accepted too.
    assert lt.parse_args(["liveticker.py", "/dry-run"])['dry_run'] is True
    # Defaults stay off.
    plain = lt.parse_args(["liveticker.py"])
    assert (plain['anytime'], plain['dry_run'], plain['once']) == (False, False, False)


def test_close_browsers_forgets_the_last_sent_state():
    app = lt.TradingApp.__new__(lt.TradingApp)
    app.wf = app.target = app.dl = app.stream = None
    app.last_sent = {"^GDAXI": {"price": 1.0, "time": "10:00:00"}}
    app.pending = {"^SPX": 2.0}
    app.stale_reported = {"^N225"}

    lt.TradingApp.close_browsers(app)

    # The next session re-sends every quote once instead of suppressing it.
    assert app.last_sent == {} and app.pending == {} and app.stale_reported == set()


def test_parse_args_reads_flags_and_values():
    opts = lt.parse_args(["liveticker.py", "/allow_notify", "/fetch_type:members",
                          "/page:/aktien/dax-realtimekurse", "/log:DEBUG"])

    assert opts['allow_notify'] is True
    assert opts['messages_only'] is False
    assert opts['fetch_type'] == 'members'
    assert opts['page'] == "/aktien/dax-realtimekurse"
    assert opts['log'] == 'DEBUG'


def test_reload_due_refreshes_a_long_open_page():
    """A tab left open all day goes partly stale.

    Measured on the live source: the index and commodity sections kept updating
    while the FX row sat on its 06:59 quote for hours — a freshly loaded page
    showed the current rate. The frozen-quotes check cannot catch that, it only
    fires when *every* symbol stops moving.
    """
    app = lt.TradingApp.__new__(lt.TradingApp)
    app.last_reload = None
    start = dt.datetime(2026, 8, 12, 9, 0, 0)

    # First call just remembers when the page was opened.
    assert app.reload_due(start) is False
    assert app.last_reload == start

    assert app.reload_due(start + dt.timedelta(minutes=lt.RELOAD_MINUTES - 1)) is False
    assert app.reload_due(start + dt.timedelta(minutes=lt.RELOAD_MINUTES)) is True


def test_closing_the_browsers_forgets_the_reload_clock():
    app = lt.TradingApp.__new__(lt.TradingApp)
    app.wf = app.target = app.dl = app.stream = None
    app.last_sent = {}
    app.pending = {}
    app.stale_reported = set()
    app.last_reload = dt.datetime(2026, 8, 12, 9, 0, 0)

    lt.TradingApp.close_browsers(app)

    # The next session opens a fresh page — its clock starts then, not now.
    assert app.last_reload is None


def _transport_app(ws_results):
    """A TradingApp whose websocket returns the queued results in order."""
    app = lt.TradingApp.__new__(lt.TradingApp)
    app.messages_only = False
    app.dry_run = False
    app.transport = 'auto'
    app.stream = None
    app.target = None
    app.symbols = lt.INDICES
    app.sys_config = types.SimpleNamespace(get_value=lambda key, default=None: 'secret')
    app.ws_failures = 0
    app.ws_blocked_until = None
    app.ws_backoff_min = lt.WS_RETRY_MINUTES
    app.ws_probing = False

    app.ws_calls = 0
    app.browser_calls = 0

    def _ws(body, expected):
        app.ws_calls += 1
        return ws_results[min(app.ws_calls - 1, len(ws_results) - 1)]

    def _browser(payload):
        app.browser_calls += 1
        return True

    app._send_via_websocket = _ws
    app._send_via_browser = _browser
    return app


def _send(app):
    return lt.TradingApp.send_message(app, {"^GDAXI": {"price": 1.0, "time": "10:00:00"}})


def test_a_single_websocket_hiccup_does_not_start_a_browser():
    """One dropped connection used to cost the fast route for the whole session."""
    app = _transport_app([None, True])

    assert _send(app) is False               # this cycle's quotes are simply retried
    assert app.browser_calls == 0
    assert _send(app) is True                # recovered on the next cycle
    assert app.browser_calls == 0
    assert app.ws_failures == 0              # success clears the count


def test_the_browser_takes_over_after_the_failure_budget():
    app = _transport_app([None])

    for _ in range(lt.WS_FAIL_LIMIT):
        _send(app)

    assert app.browser_calls == 1            # only the last attempt fell back
    assert app.ws_calls == lt.WS_FAIL_LIMIT
    assert app.ws_blocked_until is not None


def test_while_blocked_the_websocket_is_not_tried_at_all():
    app = _transport_app([None])
    for _ in range(lt.WS_FAIL_LIMIT):
        _send(app)
    calls_before = app.ws_calls

    _send(app)

    assert app.ws_calls == calls_before      # no knocking during the wait
    assert app.browser_calls == 2


def test_the_websocket_is_probed_again_when_the_wait_is_over():
    app = _transport_app([None] * lt.WS_FAIL_LIMIT + [True])
    for _ in range(lt.WS_FAIL_LIMIT):
        _send(app)
    app.ws_blocked_until = dt.datetime.now() - dt.timedelta(seconds=1)

    assert _send(app) is True                # back on the fast route
    assert app.ws_blocked_until is None
    assert app.ws_backoff_min == lt.WS_RETRY_MINUTES
    assert app.browser_calls == 1            # no further browser sends


def test_a_failed_probe_waits_twice_as_long_next_time():
    """A dead endpoint must not be knocked on every quarter hour forever."""
    app = _transport_app([None])
    for _ in range(lt.WS_FAIL_LIMIT):
        _send(app)
    first_wait = app.ws_backoff_min

    app.ws_blocked_until = dt.datetime.now() - dt.timedelta(seconds=1)
    _send(app)                               # the probe fails again

    assert app.ws_backoff_min == first_wait * 2
    assert app.ws_blocked_until is not None   # straight back to the browser


def test_the_backoff_stops_at_the_ceiling():
    app = _transport_app([None])
    for _ in range(lt.WS_FAIL_LIMIT):
        _send(app)
    for _ in range(20):
        app.ws_blocked_until = dt.datetime.now() - dt.timedelta(seconds=1)
        _send(app)

    assert app.ws_backoff_min == lt.WS_RETRY_MAX_MINUTES


def test_a_recovered_websocket_releases_the_fallback_browser():
    app = _transport_app([None] * lt.WS_FAIL_LIMIT + [True])
    for _ in range(lt.WS_FAIL_LIMIT):
        _send(app)

    closed = []
    app.target = types.SimpleNamespace(quit=lambda: closed.append(True))
    app.ws_blocked_until = dt.datetime.now() - dt.timedelta(seconds=1)
    _send(app)

    assert closed == [True]
    assert app.target is None                # reopened on demand if ever needed


def test_an_explicit_ws_transport_never_reaches_the_browser():
    app = _transport_app([None])
    app.transport = 'ws'

    for _ in range(lt.WS_FAIL_LIMIT + 2):
        assert _send(app) is False

    assert app.browser_calls == 0
