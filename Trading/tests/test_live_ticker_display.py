"""Tests for the live ticker display path (tick handling and OHLC aggregation).

The LiveTicker instances are built with __new__ so no config DB, Streamlit
session or SQLite file is needed — only the pure logic is exercised.
"""

import datetime as dt
import types

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


def _signal_frame(bars, price=26500.0, histogram=5.0, diff=1.0, momentum=60.0):
    """A frame shaped like compute_signals' output, with a known EWO histogram."""
    frame = pd.DataFrame({
        'Close': [price] * bars,
        'ewo': [histogram] * bars,
        'ewo_ema': [0.0] * bars,
        'ewo_diff': [diff] * bars,
        'momentum': [momentum] * bars,
    })
    return frame


def _signal_ticker():
    obj = _ticker()
    obj.sys_conf = None                     # falls back to the defaults
    obj.reset_signals()
    return obj


def test_the_strength_is_normalised_so_symbols_are_comparable():
    """EWO is a difference of moving averages and carries the price's unit.

    Summed raw, the same market state read +25 on the Nikkei (67 000) and +0.00
    on EURUSD (1.15) — a fixed threshold could only ever fit one instrument.
    """
    index = _signal_ticker()
    lt.LiveTicker.update_signals(index, _signal_frame(50, price=26500.0, histogram=26.5), '1min')

    fx = _signal_ticker()
    lt.LiveTicker.update_signals(fx, _signal_frame(50, price=1.15, histogram=0.00115), '1min')

    # Both moved by 0.1 % of their price, so both must report the same strength.
    assert index.value == pytest.approx(0.1)
    assert fx.value == pytest.approx(0.1)


def test_an_interval_without_enough_bars_is_excluded_not_counted_as_zero():
    """Measured on ^GDAXI: the 5min interval had 24 bars, its EWO-EMA was still
    initialising, and its -8.46 flipped the sign of the whole indicator while
    the only interval with enough data said +8.03.
    """
    obj = _signal_ticker()
    lt.LiveTicker.update_signals(obj, _signal_frame(113, histogram=8.03, diff=1.0), '1min')
    lt.LiveTicker.update_signals(obj, _signal_frame(24, histogram=-8.46, diff=-1.0), '5min')
    lt.LiveTicker.update_signals(obj, _signal_frame(8), '15min')

    assert obj.signals['1min']['ready'] is True
    assert obj.signals['5min']['ready'] is False        # 24 < min_signal_bars
    assert obj.signals['15min']['ready'] is False
    assert obj.signal_state() == (1, 3)
    assert obj.value > 0                                # the ready interval decides
    assert obj.trend == 1                               # not 0, and not -1


def test_missing_oscillator_columns_leave_the_interval_unready():
    obj = _signal_ticker()
    frame = _signal_frame(50).drop(columns=['ewo_ema'])

    lt.LiveTicker.update_signals(obj, frame, '1min')

    assert obj.signals['1min']['ready'] is False
    assert obj.signal_state() == (0, 3)


def test_the_strength_is_averaged_and_the_trend_summed():
    """Averaging keeps the threshold stable when an interval is added."""
    obj = _signal_ticker()
    lt.LiveTicker.update_signals(obj, _signal_frame(50, price=100.0, histogram=0.2), '1min')
    lt.LiveTicker.update_signals(obj, _signal_frame(50, price=100.0, histogram=0.4), '5min')

    assert obj.value == pytest.approx(0.3)      # mean of 0.2 % and 0.4 %
    assert obj.trend == 2                       # agreement count, scale-free
    assert obj.momentum == pytest.approx(60.0)


def test_reset_signals_is_explicit_not_tied_to_an_interval_name():
    obj = _signal_ticker()
    lt.LiveTicker.update_signals(obj, _signal_frame(50), '5min')
    assert obj.signal_state()[0] == 1

    obj.reset_signals()
    assert obj.signal_state() == (0, 3)
    assert (obj.value, obj.momentum, obj.trend) == (0, 0, 0)


def test_the_alert_threshold_is_a_percentage_and_needs_a_ready_interval():
    obj = _signal_ticker()
    assert obj.should_notify() is False                  # nothing computed yet

    lt.LiveTicker.update_signals(obj, _signal_frame(50, price=100.0, histogram=0.05), '1min')
    assert obj.value == pytest.approx(0.05)
    assert obj.should_notify() is False                  # below the 0.1 % default

    obj.reset_signals()
    lt.LiveTicker.update_signals(obj, _signal_frame(50, price=100.0, histogram=0.2), '1min')
    assert obj.should_notify() is True


def test_the_alert_needs_a_direction():
    obj = _signal_ticker()
    lt.LiveTicker.update_signals(obj, _signal_frame(50, price=100.0, histogram=0.2, diff=0.0),
                                 '1min')

    assert obj.value == pytest.approx(0.2)               # strong enough …
    assert obj.trend == 0                                # … but no direction
    assert obj.should_notify() is False


def test_the_momentum_filter_is_off_by_default():
    class _Config:
        def __init__(self, **values):
            self.values = values

        def get_value(self, key, default=None):
            return self.values.get(key, default)

    obj = _signal_ticker()
    obj.sys_conf = _Config()
    # Long signal with a bearish momentum reading.
    lt.LiveTicker.update_signals(obj, _signal_frame(50, price=100.0, histogram=0.2, momentum=20.0),
                                 '1min')
    assert obj.should_notify() is True

    obj.sys_conf = _Config(live_signal_momentum_filter=True)
    assert obj.should_notify() is False                  # momentum disagrees

    obj.sys_conf = _Config(live_signal_threshold=0.5)
    assert obj.should_notify() is False                  # threshold is configurable


def test_signal_line_names_the_contributing_intervals():
    obj = _signal_ticker()
    obj.symbol = '^GDAXI'
    lt.LiveTicker.update_signals(obj, _signal_frame(50, price=100.0, histogram=0.2), '1min')

    line = obj.signal_line()
    assert '^GDAXI' in line and '1' in line and '3' in line


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

    def get_plot_options(self, name):
        return []


class _StubRegion:
    """Swallows the Streamlit calls render() makes on `region`."""

    def write(self, *args, **kwargs):
        pass

    def radio(self, *args, **kwargs):
        raise AssertionError("render() must use select_tick_interval()")


def test_the_layout_order_is_table_symbol_interval_chart_then_selectors(monkeypatch):
    """The Interval/Period row sits below the chart it feeds.

    Streamlit widgets only yield their value where they are created, so the
    chart must be drawn into a container reserved earlier. If that indirection
    is ever removed, the chart would silently use last run's overlays.
    """
    obj = _ticker(_tick_frame(minutes=60))
    obj.sys_conf = _StubConfig(stored={'live_tick_interval': '1min'})
    obj.multi_selector = _StubSelector()
    obj.calc_max_periods = lambda interval, period: 512
    obj.get_database_files = lambda asterik='*': []
    obj.load_from_db = lambda **kw: None
    obj.compute_signals = lambda symbol, interval, **kw: None
    obj.select_tick_interval = lambda region=None: '1min'

    order = []
    obj.render_price_summary = lambda region=None, **kw: order.append('table')
    obj.plot_candlestick = lambda symbol, interval, **kw: order.append('chart')
    original_render = _StubSelector.render
    _StubSelector.render = lambda self: order.append('selectors')
    try:
        lt.LiveTicker.render(obj, region=_StubRegion(), bare_mode=False)
    finally:
        _StubSelector.render = original_render

    # The selector row is rendered before the chart is *drawn* — its values are
    # needed — but it lands below it on screen via the reserved container.
    assert order == ['table', 'selectors', 'chart']


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


def test_history_chart_is_configured_like_the_asset_viewer(monkeypatch):
    """The history chart must see the same settings as the Asset Viewer.

    `username` is the critical one: tiny_chart builds its own SystemConfig from
    it, so without a user every per-user setting (indicator parameters, zoom
    factor, …) silently falls back to the defaults — the chart looks subtly
    different for the same ticker.
    """
    obj = _ticker(_tick_frame(minutes=60))
    obj.username = 'kurt'
    obj.sys_conf = _StubConfig(stored={'live_tick_interval': '1min'})
    obj.multi_selector = _StubSelector()
    obj.calc_max_periods = lambda interval, period: 512
    obj.get_database_files = lambda asterik='*': []
    obj.load_from_db = lambda **kw: None
    obj.compute_signals = lambda symbol, interval, **kw: None
    obj.plot_candlestick = lambda symbol, interval, **kw: None
    obj.render_price_summary = lambda region=None, **kw: None
    obj.select_tick_interval = lambda region=None: '1min'

    captured = {}

    class _Chart:
        fig = object()

    def _tiny_chart(symbol, **kwargs):
        captured['symbol'] = symbol
        captured.update(kwargs)
        return _Chart()

    monkeypatch.setattr(lt.tc, 'tiny_chart', _tiny_chart)
    monkeypatch.setattr(lt.st, 'checkbox', lambda *args, **kwargs: True)
    monkeypatch.setattr(lt.st, 'plotly_chart', lambda *args, **kwargs: None)

    lt.LiveTicker.render(obj, region=_StubRegion(), bare_mode=False)

    assert captured['username'] == 'kurt'
    assert captured['no_plot_overlays'] == []
    assert captured['no_plot_oszilators'] == []
    assert captured['zoom'] is True and captured['range_breaks'] is True


def test_history_chart_passes_every_argument_the_asset_viewer_passes():
    """Pin the parity itself, so the two call sites cannot drift apart.

    When the Asset Viewer gains a new tiny_chart argument, this fails and points
    at the live ticker's history chart — which is exactly the moment somebody
    has to decide whether it needs it too.
    """
    import re
    from pathlib import Path

    def kwargs_of(path, anchor):
        source = Path(path).read_text(encoding='utf-8')
        start = source.index(anchor)
        opening = source.index('(', start)
        depth = 0
        for end in range(opening, len(source)):
            if source[end] == '(':
                depth += 1
            elif source[end] == ')':
                depth -= 1
                if depth == 0:
                    break
        return {m.group(1) for m in re.finditer(r'(\w+)\s*=', source[opening + 1:end])}

    root = Path(__file__).resolve().parent.parent / 'tradinglib'
    viewer = kwargs_of(root / 'main_page.py', 'self.t_chart = tc.tiny_chart(')
    history = kwargs_of(root / 'live_ticker.py', 'history = tc.tiny_chart(')

    assert not viewer - history, f"history chart is missing: {sorted(viewer - history)}"


def test_paper_anchored_annotations_are_transferred_without_row():
    """Indicator labels are anchored to the paper, not to the data axis.

    Passing row/col makes Plotly rewrite xref='paper' to the date axis, where
    x=1.0 becomes 1970-01-01: the shared axis then spans 1970..today and every
    candle is squeezed against the right edge. That is what the live chart did.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    source = go.Figure()
    source.add_annotation(x=1.0, y=100.0, xref='paper', yref='y', text=' EMA 9 ',
                          showarrow=False)

    target = make_subplots(rows=2, cols=1, shared_xaxes=True)
    assert lt.LiveTicker.transfer_annotations(target, source) == 1
    assert target.layout.annotations[0].xref == 'paper'      # survived the copy

    # Sub-plot rows are skipped rather than distorted.
    target2 = make_subplots(rows=2, cols=1, shared_xaxes=True)
    assert lt.LiveTicker.transfer_annotations(target2, source, row=2) == 0
    assert not target2.layout.annotations


def test_label_right_marks_the_last_value():
    import plotly.graph_objects as go

    fig = go.Figure()
    assert lt.LiveTicker.label_right(fig, pd.Series([1.0, 2.0, 26497.03]),
                                     'EMA 9', 'darkorange') is True

    label = fig.layout.annotations[-1]
    assert label.text.strip() == 'EMA 9'
    assert label.y == pytest.approx(26497.03)      # the line's last value
    assert label.xref == 'paper' and label.x == 1.0
    assert label.bgcolor == 'darkorange'


def test_label_right_ignores_an_empty_series():
    import plotly.graph_objects as go

    fig = go.Figure()
    assert lt.LiveTicker.label_right(fig, pd.Series(dtype='float64'), 'x', 'red') is False
    assert not fig.layout.annotations


class _Slot:
    """Records what render_price_summary writes into one column."""

    def __init__(self, log):
        self.log = log

    def markdown(self, body, unsafe_allow_html=False):
        self.log.append(body)


class _TileRegion:
    """Stand-in for st: hands out slots and collects captions."""

    def __init__(self, width=4):
        self.width = width
        self.log = []
        self.captions = []

    def columns(self, count):
        assert count == self.width
        return [_Slot(self.log) for _ in range(count)]

    def expander(self, label, expanded=True):
        self.expander_label = label
        self.expanded = expanded
        return self

    def caption(self, text):
        self.captions.append(text)

    def info(self, text):
        self.captions.append(text)


def test_price_summary_renders_one_tile_per_symbol():
    """Hand-built tiles: st.dataframe paints on a canvas, st.table prints the
    row index, and st.metric's value font filled the page on its own.
    """
    rows = []
    base = dt.datetime(2026, 8, 12, 11, 42, 0)
    for index in range(6):
        rows.append((base.strftime("%Y-%m-%d %H:%M:%S"), f"SYM{index}", 100.0 + index))
        rows.append(((base + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
                     f"SYM{index}", 101.0 + index))
    obj = _ticker(pd.DataFrame(rows, columns=["timestamp", "symbol", "price"]))
    region = _TileRegion(width=4)

    summary = lt.LiveTicker.render_price_summary(obj, region=region, columns=4)

    assert len(region.log) == len(summary) == 6      # 4 + 2, wrapped into two rows
    assert '6' in region.expander_label              # the tiles live in an expander
    assert 'SYM0' in region.log[0]
    assert lt.LiveTicker.format_price(101.0) in region.log[0]
    assert '11:43:00' in region.log[0]               # the quote time stays visible


def test_a_tile_colours_the_change_and_marks_a_stale_quote():
    row = types.SimpleNamespace(symbol='^GDAXI', price=26530.81, change=0.53,
                                time='11:47:24', age=0)

    up = lt.LiveTicker.price_tile(row)
    assert lt.LiveTicker.up_color in up and '+0.53' in up
    assert '⏳' not in up

    down = lt.LiveTicker.price_tile(types.SimpleNamespace(**{**row.__dict__, 'change': -0.95}))
    assert lt.LiveTicker.down_color in down and '-0.95' in down

    flat = lt.LiveTicker.price_tile(types.SimpleNamespace(**{**row.__dict__, 'change': 0.0}))
    assert 'color:inherit' in flat            # no colour claim for an unchanged quote

    stale = lt.LiveTicker.price_tile(row, stale=True)
    assert '⏳' in stale


def test_a_tile_escapes_its_values():
    row = types.SimpleNamespace(symbol='<script>', price=1.0, change=0.0,
                                time='11:00:00', age=0)
    assert '<script>' not in lt.LiveTicker.price_tile(row)


def test_price_summary_says_so_when_there_are_no_ticks():
    region = _TileRegion()
    assert lt.LiveTicker.render_price_summary(_ticker(), region=region).empty
    assert region.captions                          # the "no ticks yet" notice


def test_the_expander_label_carries_the_stale_count():
    """Collapsed, the label is the only place a stopped quote is still visible."""
    rows = [
        ("2026-08-12 12:17:00", "^GDAXI", 26518.5),
        ("2026-08-12 06:59:00", "EURUSD=X", 1.1535),
    ]
    obj = _ticker(pd.DataFrame(rows, columns=["timestamp", "symbol", "price"]))
    region = _TileRegion(width=4)

    lt.LiveTicker.render_price_summary(obj, region=region, columns=4, stale_after_min=15)

    assert '⏳' in region.expander_label
    assert '2' in region.expander_label          # two symbols, one of them stale


def test_the_expander_label_is_plain_when_every_quote_is_fresh():
    stamp = "2026-08-12 12:17:00"
    obj = _ticker(pd.DataFrame([(stamp, "^GDAXI", 26518.5), (stamp, "^SPX", 7741.35)],
                               columns=["timestamp", "symbol", "price"]))
    region = _TileRegion(width=4)

    lt.LiveTicker.render_price_summary(obj, region=region, columns=4)

    assert '⏳' not in region.expander_label


class _FakeOsc:
    """Minimal stand-in for an indicator instance."""

    def __init__(self, name):
        self.name = name
        self.df = pd.DataFrame()
        self.figured = False

    def add_fig(self):
        self.figured = True


def _osc_ticker(broken=()):
    """A ticker whose init_instance serves fakes and fails for `broken` names."""
    obj = _ticker()
    obj.instances = {}

    def init_instance(name, df=None, symbol=None):
        if name in broken:
            # Exactly how relvol fails on tick data: no volume column.
            raise KeyError('Volume')
        obj.instances[name] = _FakeOsc(name)
        setattr(obj, name, obj.instances[name])

    obj.init_instance = init_instance
    obj.merge_signal_columns = lambda df, name, instance: df
    return obj


def test_selected_but_not_plotted_oscillators_get_no_row():
    """The Asset Viewer honours the no-plot flag; the live chart must match it."""
    obj = _osc_ticker()

    sub_plots, _ = obj.build_sub_plots(pd.DataFrame({'Close': [1.0]}),
                                       ['ewo', 'macd', 'ovt'],
                                       no_plot_oszilators=['macd'])

    assert [name for name, _ in sub_plots] == ['ewo', 'ovt']
    assert obj.unavailable_oszilators == []


def test_a_computed_but_unplotted_oscillator_is_still_instantiated():
    """Its columns feed the buy/sell expressions even without a row."""
    obj = _osc_ticker()

    obj.build_sub_plots(pd.DataFrame({'Close': [1.0]}), ['ewo', 'macd'],
                        no_plot_oszilators=['macd'])

    assert 'macd' in obj.instances
    assert obj.instances['macd'].figured is False   # computed, not drawn


def test_one_broken_oscillator_does_not_drop_the_ones_behind_it():
    """A single try around the whole loop lost every row after the failure."""
    obj = _osc_ticker(broken=('relvol',))

    sub_plots, _ = obj.build_sub_plots(pd.DataFrame({'Close': [1.0]}),
                                       ['ewo', 'ovt', 'relvol', 'rsi'])

    assert [name for name, _ in sub_plots] == ['ewo', 'ovt', 'rsi']
    assert obj.unavailable_oszilators == ['relvol']


def test_an_unavailable_oscillator_claims_no_empty_row():
    obj = _osc_ticker(broken=('relvol',))

    sub_plots, _ = obj.build_sub_plots(pd.DataFrame({'Close': [1.0]}), ['ewo', 'relvol'])

    assert len(sub_plots) == 1
