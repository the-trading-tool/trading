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


# ---------------------------------------------------------------- entry values (tp/sl/entry_)
def _entry_rows():
    return pd.DataFrame({'ticker': 'GS',
                         'Date': [f'2026-10-{d:02d} 00:00:00' for d in (12, 13, 14, 15, 16)],
                         'close': [100.0, 110.0, 108.0, 106.0, 104.0],
                         'Low': [99.0, 105.0, 104.0, 103.0, 102.0],
                         'stop_loss': [90.0, 106.0, 100.0, 98.0, 96.0],
                         'take_profit': [120.0, 130.0, 128.0, 126.0, 124.0]})


def _entry_agent(monkeypatch, rows):
    agent = _agent(monkeypatch, ['^SPX'])
    monkeypatch.setattr(agent, '_recent_rows', lambda *a, **k: rows, raising=False)
    monkeypatch.setattr(agent, 'sys_config', type('C', (), {'username': 'kurt'})(), raising=False)
    return agent


def test_entry_werte_vom_kauftag(monkeypatch):
    agent = _entry_agent(monkeypatch, _entry_rows())
    bought = pd.Timestamp('2026-10-13 15:42:10')          # entry close 110, Low 105, sl 106
    hit, _ = agent._sell_signal('GS', '(close < entry_close * 0.95)', {}, 'x.db', bought)
    assert hit                                         # 104 < 104.5
    hit, _ = agent._sell_signal('GS', '(close < entry_Low * 0.98)', {}, 'x.db', bought)
    assert not hit                                     # 104 >= 102.9
    hit, _ = agent._sell_signal('GS', '(close < sl)', {}, 'x.db', bought)
    assert hit                                         # 104 < 106 (sl of the buy day, not 96)
    hit, _ = agent._sell_signal('GS', '(close > tp)', {}, 'x.db', bought)
    assert not hit


def test_entry_ohne_kauf_oder_ohne_kauftagszeile_verkauft_nicht(monkeypatch):
    agent = _entry_agent(monkeypatch, _entry_rows())
    assert agent._sell_signal('GS', '(close < entry_close * 2)', {}, 'x.db', None)[0] is False
    # bought today, today's row not written yet
    assert agent._sell_signal('GS', '(close < entry_close * 2)', {}, 'x.db',
                              pd.Timestamp('2026-10-17 14:00'))[0] is False
    # bought on the last row's day: nothing after the entry yet
    assert agent._sell_signal('GS', '(close < entry_close * 2)', {}, 'x.db',
                              pd.Timestamp('2026-10-16 14:00'))[0] is False
    assert agent._sell_signal('GS', '(close < entry_nope)', {}, 'x.db',
                              pd.Timestamp('2026-10-13'))[0] is False


def test_entry_time_aus_dem_orderlog():
    orders = pd.DataFrame({
        'ticker': ['GS', 'GS', 'GS', 'GS', 'MS'],
        'action': ['buy', 'sell', 'buy', 'buy', 'buy'],
        'qty': [5, 5, 3, 2, 1],
        'status': ['filled', 'filled', 'filled', 'filled', 'canceled'],
        'filled_at': ['2026-09-01T14:00:00Z', '2026-09-10T14:00:00Z',
                      '2026-10-13T15:42:10Z', '2026-10-15T15:00:00Z', None],
        'submitted_at': [None] * 5})
    assert ta.TradingAgent._entry_time('GS', orders) == pd.Timestamp('2026-10-13 15:42:10')
    assert ta.TradingAgent._entry_time('MS', orders) is None


def test_recent_rows_reicht_bis_vor_den_kauf(tmp_path, monkeypatch):
    db = tmp_path / 'asset_simulation_.db'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE asset_simulation (ticker TEXT, Date TEXT, close REAL)')
    days = pd.date_range('2026-06-01', periods=100, freq='B')
    con.executemany('INSERT INTO asset_simulation VALUES (?, ?, ?)',
                    [('GS', d.strftime('%Y-%m-%d 00:00:00'), float(i)) for i, d in enumerate(days)])
    con.commit()
    con.close()
    agent = ta.TradingAgent.__new__(ta.TradingAgent)
    agent.db_path = str(tmp_path)
    monkeypatch.setattr('tradinglib.tools.Tools.get_path',
                        lambda self, path='', file_name='': os.path.join(path, file_name))
    assert len(agent._recent_rows('GS', 'asset_simulation_.db', n=10)) == 10
    rows = agent._recent_rows('GS', 'asset_simulation_.db', n=10, since=days[20])
    assert len(rows) == 80 + 10                        # from the buy day on + 10 before
    assert rows['Date'].is_monotonic_increasing and rows['close'].iloc[-1] == 99.0
