"""run_agent syncs the order log with the broker before the agent runs.

October 2026: GS/TMUS/UE were bought by the agent but stayed 'submitted' in
trading.db (the only sync lived in the Trading page), so the sell cycle never
saw them as open positions.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_agent


class _Log:
    def __init__(self, result=None, fail=False):
        self.calls, self.result, self.fail = [], result or {}, fail

    def sync_from_broker(self, broker, mode, broker_id):
        self.calls.append((broker, mode, broker_id))
        if self.fail:
            raise RuntimeError('network down')
        return self.result


def test_sync_wird_mit_modus_und_broker_aufgerufen():
    log = _Log({'imported': 0, 'fills_updated': 3, 'external_closes': 0, 'errors': []})
    assert run_agent.sync_order_log(log, 'B', 'paper', 'alpaca')['fills_updated'] == 3
    assert log.calls == [('B', 'paper', 'alpaca')]


def test_fehlgeschlagener_sync_stoppt_den_lauf_nicht(caplog):
    assert run_agent.sync_order_log(_Log(fail=True), 'B', 'paper', 'alpaca') == {}
    assert 'Broker sync failed' in caplog.text


def test_sync_vor_dem_agentenlauf():
    src = open(run_agent.__file__, encoding='utf-8').read()
    main = src[src.index('def main():'):]
    assert main.index('sync_order_log(log, broker, mode, broker_id)') < main.index('agent.run(')
