"""Signals tab: persisted result is reused only while its inputs are unchanged."""
import os

import pytest

from tradinglib import rotation_cache
from tradinglib.premium.trading_page import TradingPage


class _Cfg:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_value(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    monkeypatch.setattr('tradinglib.risk_profile.sizing_profile', lambda u: None)
    (tmp_path / 'asset_simulation_.db').write_bytes(b'x' * 8192)
    p = object.__new__(TradingPage)
    p.username = 'tester'
    p.db_path = 'database'
    p.sys_config = _Cfg({'trading_sim_db': 'asset_simulation_.db'})
    return p


ACTIVE = {'S/R': {'^GDAXI': {'buy': '(close>sma200)', 'sell': '(rsi>70)'}}}


def _bump(path):
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 60))


def test_key_stable_for_same_inputs(page):
    assert page._signals_disk_key(ACTIVE, 'alpaca') == page._signals_disk_key(ACTIVE, 'alpaca')


def test_key_changes_with_formula_setting_broker(page):
    base = page._signals_disk_key(ACTIVE, 'alpaca')
    edited = {'S/R': {'^GDAXI': {'buy': '(close>sma50)', 'sell': '(rsi>70)'}}}
    assert page._signals_disk_key(edited, 'alpaca') != base
    assert page._signals_disk_key(ACTIVE, 'ibkr') != base
    page.sys_config.values['stop_loss_pct'] = 8
    assert page._signals_disk_key(ACTIVE, 'alpaca') != base


def test_key_follows_wal_not_main_file(page, tmp_path):
    main = tmp_path / 'asset_simulation_.db'
    no_wal = page._signals_disk_key(ACTIVE, 'alpaca')
    _bump(main)
    assert page._signals_disk_key(ACTIVE, 'alpaca') != no_wal   # no WAL: main file counts

    wal = tmp_path / 'asset_simulation_.db-wal'
    wal.write_bytes(b'w' * 100)
    with_wal = page._signals_disk_key(ACTIVE, 'alpaca')
    _bump(main)                                                   # checkpoint-only change
    assert page._signals_disk_key(ACTIVE, 'alpaca') == with_wal
    wal.write_bytes(b'w' * 200)                                   # new commit
    assert page._signals_disk_key(ACTIVE, 'alpaca') != with_wal


def test_payload_round_trips_through_rotation_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    sig = {'strategy': 'S/R', 'ticker': 'SAP.DE', 'signal': 'buy', 'price': 201.5,
           'entry_date': None, 'tradeable': True, 'qty': 3, 'budget': 650.0,
           'broker_symbol': '—', 'atr': float('nan')}
    rotation_cache.put('signals|tester|abc', {'signals': [sig], 'errors': {'x': 'y'},
                                              'computed_at': '2026-11-20 17:00:00'})
    hit = rotation_cache.get('signals|tester|abc')
    got = hit['signals'][0]
    assert {k: v for k, v in got.items() if k != 'atr'} == \
        {k: v for k, v in sig.items() if k != 'atr'}
    assert got['atr'] != got['atr']                                # NaN survives
    assert hit['errors'] == {'x': 'y'} and hit['computed_at'] == '2026-11-20 17:00:00'
