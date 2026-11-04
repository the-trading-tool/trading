"""Take profit in % above the buy price (PortfolioSimulator take_profit_pct, strategy spec)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib import ms_export, strategy_spec

asim = pytest.importorskip('tradinglib.premium.asset_simulator')


def _data(bars):
    """One ticker; bars = (Open, High, Low, close, buySell)."""
    rows = []
    for i, (o, h, lo, c, sig) in enumerate(bars):
        rows.append({'Date': f'2024-01-{i + 2:02d} 00:00:00', 'ticker': 'AAA', 'close': c,
                     'Open': o, 'High': h, 'Low': lo, 'buySell': sig, 'longName': 'A',
                     'ISIN': 'X', 'stockIndex': 'I', 'currency': 'EUR', 'vola': 1.0})
    return pd.DataFrame(rows)


def _sells(bars, **kw):
    p = asim.PortfolioSimulator(data=_data(bars), initial_cash=1000, max_assets=1, **kw)
    p.simulate()
    tx = p.get_transaction_dataframe()
    return p, tx[tx['action'] == 'sell']


def test_off_by_default_keeps_the_position():
    p, sells = _sells([(10, 10, 10, 10, 1), (10, 14, 10, 13, 0), (13, 13, 13, 13, 0)])
    assert sells.empty and 'AAA' in p.portfolio


def test_intraday_high_fills_at_the_target():
    p, sells = _sells([(10, 10, 10, 10, 1), (10, 12.5, 10, 11, 0), (11, 11, 11, 11, 0)],
                      take_profit_pct=20)
    assert len(sells) == 1 and float(sells['price'].iloc[0]) == pytest.approx(12.0)
    assert 'AAA' not in p.portfolio


def test_gap_up_fills_at_the_open():
    _, sells = _sells([(10, 10, 10, 10, 1), (15, 16, 14, 15, 0)], take_profit_pct=20)
    assert float(sells['price'].iloc[0]) == pytest.approx(15.0)


def test_below_the_target_nothing_happens():
    p, sells = _sells([(10, 10, 10, 10, 1), (10, 11.9, 10, 11.5, 0)], take_profit_pct=20)
    assert sells.empty and 'AAA' in p.portfolio


def test_stop_loss_wins_when_a_bar_touches_both():
    _, sells = _sells([(10, 10, 10, 10, 1), (10, 13, 8, 11, 0)], take_profit_pct=20, stop_loss_pct=10)
    assert float(sells['price'].iloc[0]) == pytest.approx(9.0)


def test_spec_field_and_signature_stay_compatible():
    s = strategy_spec.normalize({'costs': {'take_profit': '15'}})
    assert s['costs']['take_profit'] == 15.0
    assert strategy_spec.normalize({'costs': {'take_profit': -3}})['costs']['take_profit'] == 0.0
    # 0 leaves the signature as it was, so runs saved before keep matching
    assert 'take_profit' not in strategy_spec.signature({})
    assert '"take_profit": 15.0' in strategy_spec.signature(s)


def test_multi_entry_round_trip():
    e = ms_export.entry(buy='(close>0)', sell='(close<0)', num_assets=5, invest=1000,
                        order_by='sortino', take_profit=20)
    assert e['take_profit'] == 20
    assert 'take_profit' not in ms_export.entry(buy='a', sell='b', num_assets=5, invest=1,
                                                order_by='sortino')
    spec = strategy_spec.from_ms_entry('S', '^SPX', e, base={'costs': {'take_profit': 7}})
    assert spec['costs']['take_profit'] == 20.0
    assert strategy_spec.from_ms_entry('S', '^SPX', {'buy': 'a'},
                                       base={'costs': {'take_profit': 7}})['costs']['take_profit'] == 0.0
