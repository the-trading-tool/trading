"""Tests for the live ticker display path (tick handling and OHLC aggregation).

The LiveTicker instances are built with __new__ so no config DB, Streamlit
session or SQLite file is needed — only the pure logic is exercised.
"""

import datetime as dt

import pandas as pd
import pytest

from tradinglib import live_ticker as lt


def _ticker(df=None):
    """Build a LiveTicker without touching config/DB."""
    obj = lt.LiveTicker.__new__(lt.LiveTicker)
    obj.db_path = 'database'
    obj.db_table = 'ticker_data'
    obj.symbol = '^GDAXI'
    obj.value = obj.momentum = obj.trend = 0
    obj.market_price = 0
    obj.trend_ticker = ''
    obj._ohlc_cache = {}
    obj.symbol_list = []
    obj.df = df if df is not None else pd.DataFrame(columns=["timestamp", "symbol", "price"])
    obj.written = []
    obj._write_ticks = lambda rows: (obj.written.extend(list(rows)), len(obj.written))[1]
    return obj


@pytest.mark.parametrize("raw,expected", [
    (24004.02, 24004.02),
    ("24004.02", 24004.02),
    ("24.004,02", 24004.02),      # German format straight from the page
    ("1,1383", 1.1383),
    ("-12,5", -12.5),
])
def test_to_price_accepts_the_formats_the_collector_can_send(raw, expected):
    assert lt.LiveTicker._to_price(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "   ", "n/a", None, "abc"])
def test_to_price_rejects_junk(raw):
    assert lt.LiveTicker._to_price(raw) is None


def test_resolve_timestamp_today_and_yesterday():
    obj = _ticker()
    now = dt.datetime.now()

    past = (now - dt.timedelta(minutes=30)).strftime("%H:%M:%S")
    assert lt.LiveTicker.resolve_timestamp(obj, past).startswith(now.strftime("%Y-%m-%d")) \
        or (now - dt.timedelta(minutes=30)).date() != now.date()

    far_future = (now + dt.timedelta(hours=3)).strftime("%H:%M:%S")
    resolved = dt.datetime.strptime(lt.LiveTicker.resolve_timestamp(obj, far_future),
                                    "%Y-%m-%d %H:%M:%S")
    assert resolved < now


def test_resolve_timestamp_tolerates_a_slightly_fast_source_clock():
    obj = _ticker()
    now = dt.datetime.now()
    just_ahead = (now + dt.timedelta(seconds=30)).strftime("%H:%M:%S")

    resolved = dt.datetime.strptime(lt.LiveTicker.resolve_timestamp(obj, just_ahead),
                                    "%Y-%m-%d %H:%M:%S")

    # Must NOT be pushed back a full day.
    assert abs((resolved - now).total_seconds()) < 120


def test_resolve_timestamp_takes_a_full_timestamp_as_is():
    """A collector in another timezone resolves the date itself — trust it.

    With a bare clock time, quotes from a source that runs an hour ahead (MESZ
    seen from Canary time) would all be filed a day early.
    """
    obj = _ticker()
    assert lt.LiveTicker.resolve_timestamp(obj, "2026-08-11 13:21:31") == "2026-08-11 13:21:31"
    assert lt.LiveTicker.resolve_timestamp(obj, "2026-08-11 13:21") == "2026-08-11 13:21:00"


@pytest.mark.parametrize("raw", ["", None, "not a time", "26.05.2025"])
def test_resolve_timestamp_rejects_junk(raw):
    assert lt.LiveTicker.resolve_timestamp(_ticker(), raw) is None


def test_add_tick_batch_stores_valid_ticks_and_skips_broken_ones():
    obj = _ticker()
    now = dt.datetime.now().replace(microsecond=0)
    t1 = (now - dt.timedelta(minutes=2)).strftime("%H:%M:%S")
    t2 = (now - dt.timedelta(minutes=1)).strftime("%H:%M:%S")

    stored = lt.LiveTicker.add_tick_batch(obj, [
        (t1, "^GDAXI", "24.004,02"),
        (t2, "^GDAXI", 24010.5),
        (t2, "^SPX", "n/a"),          # unparsable price
        ("kaputt", "^SPX", 5877.99),  # unparsable time
    ])

    assert stored == 2
    assert len(obj.df) == 2
    assert obj.df["price"].tolist() == [24004.02, 24010.5]
    assert obj.symbol_list == ["^GDAXI"]


def test_add_tick_batch_deduplicates_on_timestamp_and_symbol():
    obj = _ticker()
    stamp = (dt.datetime.now() - dt.timedelta(minutes=1)).strftime("%H:%M:%S")

    lt.LiveTicker.add_tick_batch(obj, [(stamp, "^GDAXI", 100.0)])
    lt.LiveTicker.add_tick_batch(obj, [(stamp, "^GDAXI", 101.0)])

    assert len(obj.df) == 1
    assert obj.df["price"].iloc[-1] == 101.0


def _tick_frame(minutes=30, start_price=100.0):
    """Build a synthetic tick frame: one tick per minute for one symbol."""
    base = dt.datetime(2026, 8, 7, 10, 0, 0)
    rows = [((base + dt.timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
             "^GDAXI", start_price + i * 0.5) for i in range(minutes)]
    return pd.DataFrame(rows, columns=["timestamp", "symbol", "price"])


def test_aggregate_ticks_builds_ohlc_and_only_the_used_averages():
    obj = _ticker(_tick_frame(minutes=30))

    ohlc = lt.LiveTicker.aggregate_ticks(obj, interval="5min", symbol="^GDAXI")

    assert len(ohlc) == 6
    assert list(ohlc.columns[:4]) == ["Open", "High", "Low", "Close"]
    assert ohlc["Open"].iloc[0] == 100.0
    assert ohlc["Close"].iloc[0] == 102.0     # 5 one-minute ticks per bar
    assert ohlc["High"].iloc[0] == 102.0
    assert ohlc["Low"].iloc[0] == 100.0
    assert "ema9" in ohlc.columns and "sma200" in ohlc.columns
    assert "ema100" not in ohlc.columns       # was computed but never drawn


def test_aggregate_ticks_is_cached_until_new_ticks_arrive():
    obj = _ticker(_tick_frame(minutes=30))

    lt.LiveTicker.aggregate_ticks(obj, interval="5min", symbol="^GDAXI")
    assert len(obj._ohlc_cache) == 1

    lt.LiveTicker.add_tick_batch(obj, [(dt.datetime.now().strftime("%H:%M:%S"), "^GDAXI", 1.0)])
    assert obj._ohlc_cache == {}


def test_aggregate_ticks_returns_empty_for_an_unknown_symbol():
    obj = _ticker(_tick_frame())
    assert lt.LiveTicker.aggregate_ticks(obj, interval="5min", symbol="^SPX").empty


def test_prepare_frame_shape_matches_indicator_expectations():
    obj = _ticker(_tick_frame(minutes=30))

    frame = lt.LiveTicker.prepare_frame(obj, "^GDAXI", "5min")

    assert {"Date", "Open", "High", "Low", "Close", "symbol"} <= set(frame.columns)
    assert pd.api.types.is_datetime64_any_dtype(frame["Date"])


def test_update_signals_resets_on_the_first_interval_and_accumulates():
    obj = _ticker(_tick_frame(minutes=30))
    frame = lt.LiveTicker.prepare_frame(obj, "^GDAXI", "5min")
    frame["ewo"] = 10.0
    frame["ewo_ema"] = 4.0
    frame["ewo_diff"] = 1.0
    frame["momentum"] = 20.0

    obj.value, obj.momentum, obj.trend = 99, 99, 99
    lt.LiveTicker.update_signals(obj, frame, "1min")     # resets, then adds
    assert obj.value == pytest.approx(6.0)
    assert obj.momentum == pytest.approx(20.0)
    assert obj.trend == 1

    lt.LiveTicker.update_signals(obj, frame, "5min")     # accumulates
    assert obj.value == pytest.approx(12.0)
    assert obj.trend == 2
    assert obj.market_price == pytest.approx(frame["Close"].iloc[-1], abs=0.05)


SEP = lt.LiveTicker.thousands_separator


@pytest.mark.parametrize("value,expected", [
    (26497.03, f"26{SEP}497.03"),   # index: grouped digits, 2 decimals
    (1.153499960899353, "1.1535"),  # FX: 4 decimals instead of 15
    (88.84, "88.84"),
    (0.5, "0.5000"),
    ("kaputt", ""),
])
def test_format_price_matches_the_magnitude(value, expected):
    assert lt.LiveTicker.format_price(value) == expected


def test_price_summary_reports_last_price_change_and_age():
    rows = [
        ("2026-08-11 10:00:00", "^GDAXI", 26000.0),
        ("2026-08-11 10:34:00", "^GDAXI", 26260.0),      # +1 %
        ("2026-08-11 06:59:00", "EURUSD=X", 1.1535),     # stopped updating
    ]
    obj = _ticker(pd.DataFrame(rows, columns=["timestamp", "symbol", "price"]))

    summary = lt.LiveTicker.price_summary(obj).set_index('symbol')

    assert summary.loc['^GDAXI', 'price'] == 26260.0
    assert summary.loc['^GDAXI', 'change'] == pytest.approx(1.0)
    assert summary.loc['^GDAXI', 'time'] == "10:34:00"
    assert summary.loc['^GDAXI', 'age'] == 0
    # Age is measured against the freshest tick, so a stalled market stands out.
    assert summary.loc['EURUSD=X', 'age'] == pytest.approx(215)


def test_price_summary_survives_an_empty_frame():
    assert lt.LiveTicker.price_summary(_ticker()).empty


def test_only_the_selected_interval_is_drawn_but_all_of_them_are_computed():
    """The trend signal is the sum over all three intervals.

    notifier() fires on abs(value) >= 30, so drawing fewer charts must not
    change what is accumulated — otherwise the alert thresholds silently shift.
    """
    obj = _ticker(_tick_frame(minutes=60))
    obj.sys_conf = _StubConfig(stored={'live_tick_interval': '5min'})
    obj.multi_selector = _StubSelector()
    obj.calc_max_periods = lambda interval, period: 512
    drawn, computed = [], []
    obj.plot_candlestick = lambda symbol, interval, **kw: drawn.append(interval)
    obj.compute_signals = lambda symbol, interval, **kw: computed.append(interval)
    obj.render_price_summary = lambda region=None, **kw: None
    obj.get_database_files = lambda asterik='*': []
    obj.load_from_db = lambda **kw: None
    obj.select_tick_interval = lambda region=None: '5min'

    lt.LiveTicker.render(obj, region=_StubRegion(), bare_mode=False)

    assert drawn == ['5min']                                # one chart only
    assert computed == ['1min', '15min']                    # the others still run
    # 1min must come first — it resets the accumulators.
    assert (computed + drawn).index('1min') == 0


class _StubConfig:
    """Minimal SystemConfig stand-in that records writes."""

    def __init__(self, stored=None):
        self.stored = dict(stored or {})
        self.writes = []

    def get_value(self, key, default=None):
        return self.stored.get(key, default)

    def set_value(self, key, value):
        self.stored[key] = value
        self.writes.append((key, value))

    def get_selectors(self, interval, period, overlays, oszilators):
        return ('1d', '1mo', overlays, oszilators)


class _StubSelector:
    """Stand-in for the shared Interval/Period/Overlay selector row."""

    def render(self):
        pass

    def get_selected_options(self, name):
        return []


class _StubRegion:
    """Swallows the Streamlit calls render() makes on `region`."""

    def write(self, *args, **kwargs):
        pass

    def radio(self, *args, **kwargs):
        raise AssertionError("render() must use select_tick_interval()")


def test_select_tick_interval_persists_only_on_change():
    obj = _ticker()
    obj.sys_conf = _StubConfig(stored={'live_tick_interval': '5min'})

    class _Region:
        def __init__(self, answer):
            self.answer = answer
            self.kwargs = None

        def radio(self, label, options, **kwargs):
            self.kwargs = kwargs
            return self.answer

    unchanged = _Region('5min')
    assert lt.LiveTicker.select_tick_interval(obj, region=unchanged) == '5min'
    assert obj.sys_conf.writes == []                         # nothing to store

    changed = _Region('15min')
    assert lt.LiveTicker.select_tick_interval(obj, region=changed) == '15min'
    assert obj.sys_conf.writes == [('live_tick_interval', '15min')]
    # The stored value preselects the widget on the next run.
    assert changed.kwargs['index'] == lt.LiveTicker.tick_intervals.index('5min')


def test_select_tick_interval_survives_an_unknown_stored_value():
    obj = _ticker()
    obj.sys_conf = _StubConfig(stored={'live_tick_interval': '4h'})

    class _Region:
        def radio(self, label, options, **kwargs):
            assert kwargs['index'] == 0                      # falls back to the first
            return options[0]

    assert lt.LiveTicker.select_tick_interval(obj, region=_Region()) == '1min'


def test_get_symbol_list_handles_an_empty_frame():
    obj = _ticker()
    assert lt.LiveTicker.get_symbol_list(obj) == []
