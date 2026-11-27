"""Portfolio review: the fixed rules over one depot (pure, no database)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from tradinglib import portfolio_review as pr

LOCALES = Path(__file__).resolve().parent.parent / 'locales'
TODAY = '2026-11-27'


def _series(seed, vol=0.01, n=200, end=TODAY, start=100.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    return pd.Series(start * np.cumprod(1 + rng.normal(0, vol, n)), index=idx)


def _positions(rows):
    return pd.DataFrame(rows, columns=['ticker', 'longName', 'shares', 'positionValue',
                                       'gainPct', 'sector', 'currency'])


def _base():
    pos = _positions([
        ('AAA', 'Alpha', 10, 4000.0, 5.0, 'Technology', 'USD'),
        ('BBB', 'Beta', 10, 3000.0, -2.0, 'Technology', 'USD'),
        ('CCC', 'Gamma', 10, 2000.0, 1.0, 'Healthcare', 'EUR'),
        ('DDD', 'Delta', 10, 1000.0, 0.0, 'Utilities', 'EUR'),
    ])
    prices = {'AAA': _series(1), 'BBB': _series(2), 'CCC': _series(3), 'DDD': _series(4)}
    return pos, prices


def _rules(rv, ticker=None):
    return {f.rule for f in rv.findings if ticker is None or f.ticker == ticker}


def test_clean_depot_has_only_concentration_findings():
    pos, prices = _base()
    rv = pr.review(pos, prices, today=TODAY)
    assert rv.metrics['n'] == 4 and rv.metrics['total'] == 10000.0
    assert rv.metrics['largest'] == 'AAA' and rv.metrics['largest_weight'] == 40.0
    # AAA 40 % > 20 %, Technology 70 % > 35 %, USD 70 % > 60 %
    assert _rules(rv) == {'position_weight', 'sector_weight', 'currency_weight'}
    assert rv.positions['risk_share'].sum() == pytest_approx(100.0)
    assert 0 < rv.metrics['depot_vol'] < 0.25          # 1 % daily ~ 16 % p.a. per position


def pytest_approx(v):
    import pytest
    return pytest.approx(v, rel=1e-6)


def test_thresholds_are_configurable():
    pos, prices = _base()
    rv = pr.review(pos, prices, today=TODAY,
                   thresholds={'position_pct': 50, 'sector_pct': 80, 'currency_pct': 80})
    assert not _rules(rv)


def test_breached_stop_is_check_and_sorted_first():
    pos, prices = _base()
    stops = {'AAA': {'stop': 410.0, 'price': 400.0, 'updated': TODAY},
             'BBB': {'stop': 270.0, 'price': 300.0, 'updated': TODAY},
             'CCC': {'stop': 180.0, 'price': 200.0, 'updated': TODAY},
             'DDD': {'stop': 90.0, 'price': 100.0, 'updated': TODAY}}
    rv = pr.review(pos, prices, stops=stops, today=TODAY)
    first = rv.findings[0]
    assert (first.level, first.rule, first.ticker) == ('check', 'stop_breached', 'AAA')
    row = rv.positions.set_index('ticker').loc['BBB']
    assert row['dist_stop'] == pytest_approx((300 / 270 - 1) * 100)


def test_missing_trails_are_one_note_and_stale_trails_a_data_point():
    pos, prices = _base()
    stops = {'AAA': {'stop': 300.0, 'price': 400.0, 'updated': '2026-11-01'}}
    rv = pr.review(pos, prices, stops=stops, stop_tab='Risikomanagement', today=TODAY)
    note = [f for f in rv.findings if f.rule == 'no_trail']
    assert len(note) == 1 and note[0].level == 'note'
    assert note[0].params == {'n': 3, 'tickers': 'BBB, CCC, DDD', 'tab': 'Risikomanagement'}
    stale = [f for f in rv.findings if f.rule == 'trail_stale']
    assert stale[0].level == 'data' and stale[0].params['date'] == '2026-11-01'
    stops['AAA']['updated'] = '2026-11-26'
    assert 'trail_stale' not in _rules(pr.review(pos, prices, stops=stops, today=TODAY))


def test_stop_without_price_is_not_judged():
    pos, prices = _base()
    stops = {t: {'stop': 999.0, 'price': None, 'updated': TODAY} for t in ('AAA', 'BBB', 'CCC', 'DDD')}
    rv = pr.review(pos, prices, stops=stops, today=TODAY)
    assert 'stop_breached' not in _rules(rv)
    assert rv.positions['dist_stop'].isna().all()


def test_stops_none_skips_stop_rules():
    pos, prices = _base()
    rv = pr.review(pos, prices, stops=None, today=TODAY)
    assert not _rules(rv) & {'no_trail', 'trail_stale', 'stop_breached'}


def test_vol_band_and_risk_share():
    pos, prices = _base()
    prices['DDD'] = _series(9, vol=0.06)               # ~95 % p.a.
    profile = {'name': 'balanced', 'label': 'Ausgewogen', 'vol_band': 0.3714}
    rv = pr.review(pos, prices, profile=profile, today=TODAY)
    assert rv.profile_name == 'Ausgewogen'
    vb = [f for f in rv.findings if f.rule == 'vol_band']
    assert [f.ticker for f in vb] == ['DDD'] and vb[0].params['profile'] == 'Ausgewogen'
    # 10 % weight, but the wild one carries far more of the depot risk
    rs = [f for f in rv.findings if f.rule == 'risk_share']
    assert [f.ticker for f in rs] == ['DDD'] and rs[0].params['risk'] > 20


def test_short_history_stays_out_of_risk_split():
    pos, prices = _base()
    prices['DDD'] = _series(4, n=20)
    rv = pr.review(pos, prices, today=TODAY)
    p = rv.positions.set_index('ticker')
    assert np.isnan(p.loc['DDD', 'risk_share'])
    assert p['risk_share'].sum() == pytest_approx(100.0)


def test_data_findings_gap_stale_missing():
    pos, prices = _base()
    s = _series(3)
    s.iloc[-10:] = s.iloc[-10:] / 10                   # unadjusted 10:1 split
    prices['CCC'] = s
    prices['BBB'] = _series(2, end='2026-11-10')
    del prices['DDD']
    rv = pr.review(pos, prices, today=TODAY)
    assert 'price_gap' in _rules(rv, 'CCC')
    assert 'stale' in _rules(rv, 'BBB')
    assert 'no_prices' in _rules(rv, 'DDD')
    assert all(f.level == 'data' for f in rv.findings if f.rule in ('price_gap', 'stale', 'no_prices'))


def test_signals_and_quality():
    pos, prices = _base()
    signals = {'AAA': {'action': 'reduce', 'last_sell': '2026-11-20'},
               'BBB': {'action': 'hold'}}
    quality = {'CCC': 'surprise', 'DDD': 'leverage,cash_burn'}
    rv = pr.review(pos, prices, signals=signals, quality=quality, today=TODAY)
    assert 'sell_signal' in _rules(rv, 'AAA') and 'sell_signal' not in _rules(rv, 'BBB')
    assert _rules(rv, 'CCC') == {'surprise'}
    q = [f for f in rv.findings if f.rule == 'quality_flags']
    assert q[0].ticker == 'DDD' and q[0].params['flags'] == 'leverage, cash_burn'
    assert rv.positions.set_index('ticker').loc['AAA', 'signal'] == 'reduce'


def test_market_context_never_raises_findings():
    """Only the rules above produce findings; review() takes no market input at all."""
    import inspect
    params = set(inspect.signature(pr.review).parameters)
    assert not params & {'macro', 'market', 'cot', 'regime'}


def test_empty_depot():
    rv = pr.review(pd.DataFrame(), {}, today=TODAY)
    assert rv.findings == [] and rv.metrics['n'] == 0 and rv.positions.empty


def test_rule_texts_exist_in_both_languages_and_format():
    pos, prices = _base()
    s = _series(3)
    s.iloc[-10:] = s.iloc[-10:] / 10
    prices['CCC'] = s
    prices['DDD'] = _series(9, vol=0.06)
    stops = {'AAA': {'stop': 410.0, 'price': 400.0, 'updated': '2026-11-01'}}
    rv = pr.review(pos, prices, stops=stops, stop_tab='X', profile={'name': 'x', 'vol_band': 0.3},
                   signals={'BBB': {'action': 'reduce', 'last_sell': '2026-11-20'}},
                   quality={'BBB': 'surprise,losses'}, today=TODAY)
    rules = {f.rule for f in rv.findings}
    for lang in ('de', 'en'):
        d = json.loads((LOCALES / f'{lang}.json').read_text(encoding='utf-8'))
        for rule in ('no_value', 'no_prices', 'stale', 'price_gap', 'stop_breached',
                     'no_trail', 'trail_stale', 'vol_band', 'risk_share',
                     'position_weight', 'sector_weight', 'currency_weight', 'sell_signal',
                     'surprise', 'quality_flags'):
            assert f'pr.rule.{rule}' in d, (lang, rule)
        for f in rv.findings:
            txt = f.text(d)
            assert '{' not in txt, (lang, f.rule, txt)
    assert len(rules) >= 9


def test_prompt_only_explains():
    pos, prices = _base()
    rv = pr.review(pos, prices, today=TODAY)
    p = pr.build_prompt(rv, 'Eigene Trades', macro_text='MAKRO-UMFELD\n  USD: Leitzins 3.88 %')
    assert 'REGEL-ERGEBNISSE' in p and '[Hinweis] AAA:' in p
    assert 'KEINE neuen Kauf' in p and 'MAKRO-UMFELD' in p
    assert 'Verkaufs-empfehlungen' not in p
    assert 'Eigene Trades' in p
