"""TradingAgent sell cycle: right table, right strategy, backtest semantics.

2026-10-17: the agent never fired a strategy sell. It looked for a table
"asset_simulation_" (derived from the file name), assigned every open position
to the first enabled strategy, and evaluated multi-line rules on one row.
"""
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib.premium import trading_agent as ta

STRATS = {
    'Support/Resistance': {'^SPX': {'buy': 'x', 'sell': '(ewo < ewo_ema)', 'signal_window': 2}},
    'Support/RSI': {'^SPX': {'buy': 'x', 'sell': '(High>=atc_top_high)\n(rsi>=75)', 'signal_window': 2,
                             'min_hold_days': 2},
                    '^RUT': {'buy': 'x', 'sell': '(rsi>=80)'}},
}


def _agent(monkeypatch, indices):
    agent = ta.TradingAgent.__new__(ta.TradingAgent)
    monkeypatch.setattr(agent, '_ticker_indices', lambda t: indices, raising=False)
    return agent


def test_tabelle_heisst_asset_simulation(tmp_path):
    db = tmp_path / 'asset_simulation_.db'
    sqlite3.connect(db).execute('CREATE TABLE asset_simulation (ticker TEXT)').connection.commit()
    assert ta._sim_table(str(db), 'asset_simulation_.db') == 'asset_simulation'
    other = tmp_path / 'x.db'
    sqlite3.connect(other).execute('CREATE TABLE x (a)').connection.commit()
    assert ta._sim_table(str(other), 'x.db') == 'x'


def test_kaufende_strategie_und_index_entscheiden(monkeypatch):
    agent = _agent(monkeypatch, ['^SPX', '^DJI'])
    sn, idx, sell, icfg = agent._sell_rule_for('GS', 'Support/RSI', STRATS)
    assert (sn, idx) == ('Support/RSI', '^SPX') and sell.startswith('(High>=atc_top_high)')
    # unknown buyer (imported order): first strategy covering the ticker
    assert agent._sell_rule_for('GS', '', STRATS)[0] == 'Support/Resistance'
    # buying strategy no longer enabled: no rule (guard / trailing stop protect it)
    assert agent._sell_rule_for('GS', 'Deleted', STRATS) is None


def test_ticker_hat_den_index_verlassen(monkeypatch):
    agent = _agent(monkeypatch, [])
    assert agent._sell_rule_for('AAD.DE', 'Support/RSI', STRATS)[1] == '^SPX'


def test_mehrzeilig_mit_fenster_wie_im_backtest(monkeypatch):
    agent = _agent(monkeypatch, ['^SPX'])
    rows = pd.DataFrame({'ticker': 'GS', 'Date': ['2026-10-14', '2026-10-15', '2026-10-16'],
                         'High': [990.0, 900.0, 900.0], 'atc_top_high': [950.0] * 3,
                         'rsi': [60.0, 60.0, 78.0], 'close': [880.0, 890.0, 891.0]})
    monkeypatch.setattr(agent, '_recent_rows', lambda *a, **k: rows, raising=False)
    monkeypatch.setattr(agent, 'sys_config', type('C', (), {'username': 'kurt'})(), raising=False)
    icfg = STRATS['Support/RSI']['^SPX']
    hit, last = agent._sell_signal('GS', icfg['sell'], {'signal_window': 1}, 'x.db')
    assert not hit                                    # both lines never true on one bar
    hit, last = agent._sell_signal('GS', icfg['sell'], {'signal_window': 3}, 'x.db')
    assert hit and last['close'] == 891.0            # High on 14th, RSI on 16th, window 3
