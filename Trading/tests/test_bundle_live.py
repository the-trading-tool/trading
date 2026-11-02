"""Index bundles ('^GDAXI + ^MDAXI') on the live side: sell rule and Scalable pairing."""
from tradinglib import scalable_signals
from tradinglib.premium.trading_agent import TradingAgent

STRATS = {
    'Dip': {'^GDAXI + ^MDAXI': {'buy': 'b1', 'sell': 's-bundle', 'num_assets': 15},
            '^SPX': {'buy': 'b2', 'sell': 's-spx'}},
    'Other': {'^SDAXI': {'buy': 'b3', 'sell': 's-sdax'}},
}


def _agent(indices):
    a = object.__new__(TradingAgent)
    a._ticker_indices = lambda ticker: indices
    return a


def test_sell_rule_finds_the_bundle_by_a_member_index():
    sn, key, sell, cfg = _agent(['^MDAXI'])._sell_rule_for('AIXA.DE', 'Dip', STRATS)
    assert (sn, key, sell) == ('Dip', '^GDAXI + ^MDAXI', 's-bundle') and cfg['num_assets'] == 15
    assert _agent(['^SPX'])._sell_rule_for('AAPL', 'Dip', STRATS)[2] == 's-spx'


def test_sell_rule_without_buying_strategy_still_maps_members():
    rule = _agent(['^GDAXI'])._sell_rule_for('SAP.DE', '', STRATS)
    assert rule[1] == '^GDAXI + ^MDAXI'


def test_scalable_pairs_apply_a_bundle_to_its_members():
    pairs = scalable_signals.parse_strategy_config(STRATS)
    out = scalable_signals.applicable_pairs(['SAP.DE', 'AAPL', 'X'], pairs,
                                            {'SAP.DE': ['^GDAXI'], 'AAPL': ['^SPX'], 'X': ['^RUT']})
    got = {(r['ticker'], r['index']) for r in out}
    assert got == {('SAP.DE', '^GDAXI + ^MDAXI'), ('AAPL', '^SPX')}
