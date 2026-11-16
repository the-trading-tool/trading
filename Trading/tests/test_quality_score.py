"""Quality score: criteria, availability scaling, banks, red flags, sector rank, funnel step."""
import json

import numpy as np
import pandas as pd
import pytest

from tradinglib import quality_score as qs


def _sheet(rows: dict, years=('2025-12-31', '2024-12-31', '2023-12-31', '2022-12-31', '2021-12-31')):
    cols = [int(pd.Timestamp(y).value // 10**6) for y in years[:len(next(iter(rows.values())))]]
    return json.dumps({'columns': cols, 'index': list(rows), 'data': list(rows.values())})


def _good(**kw):
    row = {
        'ticker': 'GOOD', 'sector': 'Technology', 'currency': 'USD', 'financialCurrency': 'USD',
        'returnOnEquity': 0.25, 'operatingMargins': 0.30, 'totalDebt': 100.0, 'totalCash': 300.0,
        'ebitda': 500.0, 'currentRatio': 2.0, 'operatingCashflow': 450.0,
        'netIncomeToCommon': 300.0, 'freeCashflow': 400.0, 'marketCap': 5000.0,
        'totalRevenue': 1500.0, 'trailingPegRatio': 1.2, 'forwardPE': None,
        'earningsGrowth': None, 'revenueGrowth': 0.05, 'revenueGrowthEst': 0.12,
        'stockBasedCompensation': 30.0,
        'incomeSheet': _sheet({'Gross Profit': [600, 580, 560], 'Total Revenue': [1000, 1000, 1000],
                               'EBIT': [400, 380, 360], 'Interest Expense': [10, 10, 10]}),
        'cashflowSheet': _sheet({'Free Cash Flow': [400, 380, 350, 300, 250]}),
    }
    row.update(kw)
    return row


def _score(row, quarters=None):
    return qs.compute(pd.DataFrame([row]), quarters, today=pd.Timestamp('2026-11-16').date())


def test_points_add_up_to_100():
    assert sum(qs.POINTS.values()) == 100


def test_a_clean_compounder_scores_100():
    out = _score(_good()).iloc[0]
    assert out['quality_avail'] == 100 and out['quality_score'] == 100.0
    assert out['quality_tier'] == 1 and out['quality_flags'] == ''


def test_missing_data_is_not_a_failure():
    out = _score(_good(currentRatio=None, cashflowSheet=None, stockBasedCompensation=None)).iloc[0]
    assert out['quality_avail'] == 100 - 7 - 8 - 7
    assert out['quality_score'] == 100.0


def test_too_little_data_gives_no_score():
    row = {k: None for k in _good()}
    row.update(ticker='X', sector='Technology', returnOnEquity=0.3, operatingMargins=0.3)
    out = _score(row).iloc[0]
    assert pd.isna(out['quality_score']) and out['quality_avail'] == 17


def test_thresholds_fail():
    out = _score(_good(returnOnEquity=0.10, trailingPegRatio=2.0, revenueGrowthEst=0.03)).iloc[0]
    assert out['roe_ok'] == 0 and out['peg_ok'] == 0 and out['revenue_growth_ok'] == 0
    assert out['quality_score'] == 100 - 8 - 10 - 7


def test_net_cash_and_leverage():
    assert _score(_good()).iloc[0]['net_debt_ebitda'] == 0.0
    lev = _score(_good(totalDebt=3000.0, totalCash=0.0)).iloc[0]
    assert lev['net_debt_ebitda'] == 6.0 and lev['net_debt_ebitda_ok'] == 0
    assert 'leverage' in lev['quality_flags']


def test_negative_fcf_year_breaks_the_streak():
    out = _score(_good(cashflowSheet=_sheet({'Free Cash Flow': [400, 380, -10, 300, 250]}))).iloc[0]
    assert out['fcf_years'] == 2 and out['fcf_years_ok'] == 0


def test_short_history_all_positive_is_unknown():
    out = _score(_good(cashflowSheet=_sheet({'Free Cash Flow': [400, 380, 350]}))).iloc[0]
    assert pd.isna(out['fcf_years_ok'])


def test_loss_maker_fails_earnings_quality_and_is_flagged():
    out = _score(_good(netIncomeToCommon=-50.0, operatingCashflow=-20.0,
                       freeCashflow=-60.0,
                       cashflowSheet=_sheet({'Free Cash Flow': [-60, 10, 20, 30, 40]}))).iloc[0]
    assert out['cfo_to_ni_ok'] == 0
    assert set(out['quality_flags'].split(',')) >= {'losses', 'cash_burn'}


def test_banks_skip_balance_and_cash_criteria():
    out = _score(_good(sector='Financial Services', totalDebt=1e6, totalCash=0.0)).iloc[0]
    assert out['quality_avail'] == 8 + 9 + 7 + 10 + 7
    assert 'leverage' not in out['quality_flags']
    assert out['quality_score'] == 100.0


def test_two_negative_surprises_flag():
    q = pd.DataFrame({'ticker': 'GOOD', 'period_end': ['2026-03-31', '2026-06-30'],
                      'report_date': ['2026-05-01', '2026-08-01'],
                      'report_date_source': 'earnings_dates', 'surprise_pct': [-3.0, -1.0],
                      'ebit': np.nan, 'interest_expense': np.nan})
    assert 'surprise' in _score(_good(), q).iloc[0]['quality_flags']
    q.loc[1, 'surprise_pct'] = 2.0
    assert 'surprise' not in _score(_good(), q).iloc[0]['quality_flags']


def test_ttm_interest_cover_from_quarters():
    q = pd.DataFrame({'ticker': 'GOOD',
                      'period_end': ['2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30'],
                      'report_date': ['2026-02-01', '2026-05-01', '2026-08-01', '2026-11-01'],
                      'report_date_source': 'earnings_dates', 'surprise_pct': 1.0,
                      'ebit': [10.0, 10.0, 10.0, 10.0], 'interest_expense': [10.0] * 4})
    out = _score(_good(), q).iloc[0]
    assert out['interest_cover'] == 1.0 and out['interest_cover_src'] == 'ttm'
    assert 'interest' in out['quality_flags']


def test_fcf_yield_converts_the_report_currency(monkeypatch):
    from tradinglib.utils import DataUtils
    monkeypatch.setattr(DataUtils, 'get_exchange_rate',
                        classmethod(lambda cls, symbol='EUR', system_currency='EUR', **k: 0.8))
    out = _score(_good(currency='GBp', financialCurrency='USD')).iloc[0]
    # 400 USD / 0.8 GBP-per-USD... rate = units of GBP per USD -> 400 / 0.8 = 500 GBP
    assert out['fcf_yield'] == pytest.approx(500.0 / 5000.0)


def test_sector_rank_is_relative_and_direction_aware():
    rows = [_good(ticker=f'T{i}', returnOnEquity=0.05 * i, trailingPegRatio=3.0 - 0.2 * i)
            for i in range(1, 7)]
    out = qs.compute(pd.DataFrame(rows), today=pd.Timestamp('2026-11-16').date())
    s = out.set_index('ticker')['quality_sector']
    assert s['T6'] > s['T1']
    assert out['quality_pct'].between(0, 100).all()


def test_store_appends_history_only_on_change(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / 'a.db')
    f = _score(_good())
    qs.store(conn, f)
    qs.store(conn, f.assign(asof='2026-11-23'))
    assert conn.execute(f"SELECT COUNT(*) FROM {qs.HISTORY}").fetchone()[0] == 1
    qs.store(conn, f.assign(asof='2026-11-30', quality_score=50.0))
    assert conn.execute(f"SELECT COUNT(*) FROM {qs.HISTORY}").fetchone()[0] == 2


# ── funnel step ──────────────────────────────────────────────────────────────
def _funnel(monkeypatch, mode, direction='long', pct=15):
    from tradinglib import candidates as cand
    scores = pd.DataFrame({
        'ticker': ['A', 'B', 'C', 'D'],
        'quality_score': [90.0, 60.0, 40.0, 95.0], 'quality_tier': [1, 3, 4, 1],
        'quality_sector': [90.0, 50.0, 10.0, 99.0], 'quality_pct': [90.0, 50.0, 5.0, 99.0],
        'quality_flags': ['', '', 'leverage', 'surprise'], 'quality_n_flags': [0, 0, 1, 1],
        'quality_avail': [100, 100, 100, 100]})
    monkeypatch.setattr(qs, 'load', lambda db_path='database': scores)
    df = pd.DataFrame({'ticker': ['A', 'B', 'C', 'D', 'ETF']})
    steps = []
    out = cand._apply_quality(df, {'quality_mode': mode, 'quality_top_pct': pct}, direction,
                              'database', lambda *a, **k: steps.append(a))
    return sorted(out['ticker']), steps


def test_funnel_exclude_keeps_unscored(monkeypatch):
    kept, steps = _funnel(monkeypatch, 'exclude')
    assert kept == ['A', 'B', 'ETF'] and steps


def test_funnel_top_needs_rank_and_no_flags(monkeypatch):
    kept, _ = _funnel(monkeypatch, 'top')
    assert kept == ['A']                       # D is top but flagged, ETF unscored


def test_funnel_short_mirrors(monkeypatch):
    assert _funnel(monkeypatch, 'exclude', 'short')[0] == ['B', 'C', 'ETF']
    assert _funnel(monkeypatch, 'top', 'short')[0] == ['C']


def test_funnel_off_only_adds_columns(monkeypatch):
    kept, steps = _funnel(monkeypatch, 'off')
    assert kept == ['A', 'B', 'C', 'D', 'ETF'] and not steps


def test_fundamental_tab_locale_keys_exist():
    """Every key the quality block of the Fundamental tab builds, in both languages."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / 'locales'
    keys = {f'fund.qc_{k}' for k in qs.POINTS} | {f'fund.qg_{g}' for g in qs.GROUPS}
    keys |= {f'fund.qtier_{n}' for n in (1, 2, 3, 4)} | {f'cand.qflag_{f}' for f in qs.FLAGS}
    keys |= {'fund.q_growth_consensus', 'fund.q_growth_trailing', 'fund.q_cover_ttm',
             'fund.q_cover_annual'}
    import re
    src = (Path(qs.__file__).parent / 'fundamentals_page.py').read_text(encoding='utf-8')
    keys |= set(re.findall(r"t\('(fund\.q_[a-z_]+)'", src))
    for lang in ('de', 'en'):
        d = json.loads((root / f'{lang}.json').read_text(encoding='utf-8'))
        assert not keys - set(d), (lang, sorted(keys - set(d)))
