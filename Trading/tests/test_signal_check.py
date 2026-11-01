"""Signal check: formula-level tests on synthetic panels with a known answer."""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from tradinglib import signal_check as sc

ROOT = Path(__file__).resolve().parent.parent


def _panel(planted: bool, n_tickers=40, n_days=700, seed=0):
    """Random walks; with *planted*, flag=1 marks days followed by a 63-bar rise."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2020-01-01', periods=n_days)
    rows = []
    for k in range(n_tickers):
        r = rng.normal(0, 0.01, n_days)
        flag = np.zeros(n_days)
        for d in rng.choice(np.arange(50, n_days - 100), size=6, replace=False):
            flag[d] = 1
            if planted:
                r[d + 1:d + 64] += 0.004          # ~+29 % over the next 63 bars
        close = 100 * np.exp(np.cumsum(r))
        rows.append(pd.DataFrame({'ticker': f'T{k}', 'Date': dates, 'close': close,
                                  'flag': flag, 'stockIndex': '^TEST'}))
    return pd.concat(rows, ignore_index=True)


def test_planted_signal_is_green_and_random_one_is_not():
    good = sc.run(_panel(True), '(flag > 0)', '(flag < 0)')
    h = good['buy']['horizons'][63]
    assert h['mean'] > 10 and h['t'] > 5
    assert good['verdicts']['buy'] == sc.GREEN
    noise = sc.run(_panel(False), '(flag > 0)', '(flag < 0)')
    assert noise['verdicts']['buy'] in (sc.YELLOW, sc.GREY)
    assert abs(noise['buy']['horizons'][63]['mean']) < 3


def test_frame_order_does_not_matter():
    df = _panel(True)
    a = sc.run(df, '(flag > 0)', '(flag < 0)')
    b = sc.run(df.sort_values(['Date', 'ticker'], ascending=[False, True]), '(flag > 0)', '(flag < 0)')
    assert a['buy']['horizons'][63]['mean'] == b['buy']['horizons'][63]['mean']


def test_prepare_keeps_only_the_columns_the_formulas_use():
    df = _panel(False).assign(unused=1.0)
    d = sc.prepare(df, '(flag > 0)', '(close < 1)')
    assert set(d.columns) == {'ticker', 'Date', 'close', 'stockIndex', 'flag'}


def test_exit_only_into_strength_makes_random_entries_win():
    """Sell only after a rise: random entries close mostly in profit too."""
    df = _panel(False)
    df['up20'] = df.groupby('ticker')['close'].transform(lambda s: s / s.shift(20) - 1)
    res = sc.run(df, '(flag > 0)', '(up20 > 0.05)')
    c = res['control']
    assert c['rand_hit'] > 55 and c['own_hit'] > 55
    assert res['verdicts']['control'] in (sc.YELLOW, sc.GREY)


def test_concentration():
    t = pd.DataFrame({'gain': [50, 30, 10, 5, 5, -20, 20]})   # net 100
    c = sc.concentration(t)
    assert c['top3'] == 100 * (50 + 30 + 20) / 100
    assert sc.verdict_concentration(c) == sc.RED
    flat = sc.concentration(pd.DataFrame({'gain': [10] * 20}))
    assert flat['top3'] == 15 and sc.verdict_concentration(flat) == sc.GREEN
    loss = sc.concentration(pd.DataFrame({'gain': [-5, 2]}))
    assert np.isnan(loss['top3']) and sc.verdict_concentration(loss) == sc.RED


def test_verdict_markets():
    win, loss = {'strategy': 10, 'universe': 5}, {'strategy': 1, 'universe': 5}
    assert sc.verdict_markets([win, win, loss]) == sc.GREEN
    assert sc.verdict_markets([win, win, loss, loss]) == sc.YELLOW
    assert sc.verdict_markets([win, loss, loss]) == sc.RED
    assert sc.verdict_markets([win, win]) == sc.GREY        # too few markets
    assert sc.verdict_markets([]) == sc.GREY


def test_market_row_against_flat_universe():
    df = _panel(False, n_tickers=5, n_days=300)
    trades = pd.DataFrame(columns=['ticker', 'buyDate', 'sellDate', 'buyValue', 'sellVolume', 'gain'])
    r = sc.market_row(df, trades, 100000)
    assert r['strategy'] == 0 and r['trades'] == 0 and r['years'] >= 1


def test_locale_keys_in_both_languages():
    src = (ROOT / 'tradinglib' / 'signal_check.py').read_text(encoding='utf-8')
    prefixes = ('sfc.buy_v', 'sfc.sell_v', 'sfc.control_v', 'sfc.conc_v', 'sfc.markets_v')
    keys = set(re.findall(r"'(sfc\.[a-z_.]+)'", src)) - set(prefixes)
    for prefix in prefixes:
        keys |= {f'{prefix}.{v}' for v in (sc.GREEN, sc.YELLOW, sc.RED, sc.GREY)}
    keys = {k for k in keys if not k.endswith('.')}
    assert len(keys) > 30
    for lang in ('de', 'en'):
        d = json.loads((ROOT / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        missing = sorted(k for k in keys if k not in d)
        assert not missing, (lang, missing)
