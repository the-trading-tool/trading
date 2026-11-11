"""Own trades in the asset viewer: FIFO average of the held lots, one marker per trade."""
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from tradinglib.portfolio_analysis import fifo_open_lots

ROOT = Path(__file__).resolve().parent.parent

# Infineon as booked: 9 @ 83.20, sell 5, buy 4 @ 52.29 -> Scalable average 67.745
IFX = pd.DataFrame({
    'timestamp': ['2026-06-25 12:21:00', '2026-07-07 15:31:21', '2026-11-11 17:10:24'],
    'action': ['buy', 'sell', 'buy'],
    'shares': [9.0, 5.0, 4.0],
    'price': [83.2, 71.21, 52.29],
})


def _avg(lots):
    return sum(l['shares'] * l['price'] for l in lots) / sum(l['shares'] for l in lots)


def test_fifo_average_matches_the_broker():
    lots = fifo_open_lots(IFX)
    assert [(l['timestamp'][:10], l['shares']) for l in lots] == [('2026-06-25', 4.0), ('2026-11-11', 4.0)]
    assert round(_avg(lots), 3) == 67.745


def test_fifo_unsorted_input_and_full_close():
    lots = fifo_open_lots(IFX.iloc[::-1])                        # order of the query must not matter
    assert round(_avg(lots), 3) == 67.745
    closed = pd.DataFrame({'timestamp': ['2026-01-02', '2026-02-02'], 'action': ['buy', 'sell'],
                           'shares': [10, 10], 'price': [5.0, 6.0]})
    assert fifo_open_lots(closed) == []


def test_sells_beyond_known_lots_are_ignored():
    # buy older than the import: the sell must not create a negative lot
    df = pd.DataFrame({'timestamp': ['2026-03-01', '2026-04-01'], 'action': ['sell', 'buy'],
                       'shares': [15, 60], 'price': [17.0, 18.23]})
    assert fifo_open_lots(df) == [{'timestamp': '2026-04-01', 'shares': 60.0, 'price': 18.23}]
    assert fifo_open_lots(pd.DataFrame()) == []


class _Chart:
    """Just enough of tiny_chart for add_trade_markers."""
    ftime_str = '%Y-%m-%d %H:%M:%S'

    def __init__(self, dates):
        from plotly.subplots import make_subplots
        self.df = pd.DataFrame({'Date': dates})
        self.fig = make_subplots(rows=1, cols=1)

    from tradinglib.tiny_chart import tiny_chart as _tc
    _x_bar_for = _tc._x_bar_for
    add_trade_markers = _tc.add_trade_markers


def test_markers_land_on_their_bar_and_skip_trades_outside_the_chart():
    # daily bars; today's buy at 17:10 lies after the 00:00 stamp of the last bar
    c = _Chart(['2026-07-06 00:00:00', '2026-07-07 00:00:00', '2026-11-10 00:00:00', '2026-11-11 00:00:00'])
    c.add_trade_markers([
        {'since': '2026-06-25 12:21:00', 'y': 83.2, 'kind': 'buy', 'text': 'before chart'},
        {'since': '2026-07-07 15:31:21', 'y': 71.21, 'kind': 'sell', 'text': 'sell'},
        {'since': '2026-11-11 17:10:24', 'y': 52.29, 'kind': 'buy', 'text': 'buy'},
        {'since': '2026-11-12 09:00:00', 'y': 50.0, 'kind': 'buy', 'text': 'after chart'},
    ])
    traces = {tr.marker.symbol: tr for tr in c.fig.data if isinstance(tr, go.Scatter)}
    assert list(traces['triangle-up'].x) == ['2026-11-11 00:00:00']
    assert list(traces['triangle-up'].y) == [52.29]
    assert list(traces['triangle-down'].x) == ['2026-07-07 00:00:00']


def test_marker_locale_keys():
    for lang in ('de', 'en'):
        d = json.loads((ROOT / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        for k in ('main.own_buy_marker', 'main.own_sell_marker'):
            assert '{shares}' in d[k] and '{price}' in d[k] and '{date}' in d[k], (lang, k)
