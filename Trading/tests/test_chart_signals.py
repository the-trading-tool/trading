"""Signal badge of the asset summary (tradinglib.chart_signals)."""
import json
import os
import sqlite3

import numpy as np
import pandas as pd

from tradinglib import chart_signals as cs


def test_own_signal_is_the_latest_marker():
    df = pd.DataFrame({'Date': ['2026-10-01 10:00:00', '2026-10-02 10:00:00', '2026-10-03 15:00:00',
                                '2026-10-04 10:00:00'],
                       'Close': [10.0, 11.0, 12.5, 12.0], 'position': [1, 0, -1, 0]})
    sig = cs.own_signal(df)
    assert sig['kind'] == 'sell' and sig['price'] == 12.5
    assert sig['date'] == pd.Timestamp('2026-10-03 15:00')
    assert cs.own_signal(df.assign(position=0)) is None
    assert cs.own_signal(df.drop(columns='position')) is None


def test_yahoo_signals(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    con = sqlite3.connect(tmp_path / 'asset_info.db')
    con.execute('CREATE TABLE asset_info (ticker, recommendationKey, recommendationMean, '
                'numberOfAnalystOpinions, timestamp)')
    con.executemany('INSERT INTO asset_info VALUES (?, ?, ?, ?, ?)', [
        ('SAP.DE', 'buy', 1.5, 27, '2026-10-25 12:04:47'),
        ('AAD.DE', 'none', None, 3, '2026-10-25 12:04:46'),
        ('X', None, None, None, None)])
    con.commit()
    con.close()
    out = cs.yahoo_signals(['SAP.DE', 'AAD.DE', 'X', 'MISSING'])
    assert list(out) == ['SAP.DE']
    assert out['SAP.DE']['kind'] == 'buy' and out['SAP.DE']['n'] == 27
    assert out['SAP.DE']['date'] == pd.Timestamp('2026-10-25 12:04:47')


def test_badge_text():
    t = lambda key, **kw: key + (json.dumps(kw, sort_keys=True) if kw else '')   # noqa: E731
    own = cs.badge_html({'source': 'own', 'kind': 'buy', 'date': pd.Timestamp('2026-10-03 15:00'),
                         'price': 12.5}, t)
    assert 'sig.kind_buy' in own and '03.10.2026 15:00' in own and '@ 12.50' in own
    yahoo = cs.badge_html({'source': 'yahoo', 'kind': 'hold', 'date': pd.Timestamp('2026-10-25 12:04'),
                           'mean': 2.6, 'n': 18}, t)
    assert 'sig.kind_hold' in yahoo and '25.10.2026' in yahoo and '12:04' not in yahoo
    assert 'sig.none' in cs.badge_html(None, t)


def test_locale_keys_exist():
    root = os.path.join(os.path.dirname(__file__), '..')
    keys = {'sig.none', 'sig.own_detail', 'sig.yahoo_detail', 'sig.analysts'} | \
        {f'sig.kind_{k}' for k in cs.YAHOO_KINDS}
    for lang in ('de', 'en'):
        with open(os.path.join(root, 'locales', f'{lang}.json'), encoding='utf-8') as fh:
            have = json.load(fh)
        assert not sorted(keys - set(have)), lang
