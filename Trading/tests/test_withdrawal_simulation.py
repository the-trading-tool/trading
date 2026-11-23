"""Tests for the withdrawal simulator engine (tradinglib/withdrawal_simulation.py)."""
import numpy as np
import pandas as pd
import pytest

from tradinglib import withdrawal_simulation as ws


def _flat_returns(monthly: float, months: int = 240, k: int = 1) -> np.ndarray:
    return np.full((months, k), monthly)


# ── life table ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('sex', ['m', 'f'])
def test_life_expectancy_at_65_matches_destatis(sex):
    e65 = ws.life_expectancy(ws.Person.average(65, sex))
    assert e65 == pytest.approx(ws.DESTATIS_E65[sex], abs=0.1)


def test_women_live_longer_and_lifestyle_shifts_life_expectancy():
    m, f = ws.Person.average(65, 'm'), ws.Person.average(65, 'f')
    assert ws.life_expectancy(f) > ws.life_expectancy(m)
    healthy = ws.Person(65, 'm', health='very_good', activity='active')
    smoker = ws.Person(65, 'm', smoking='smoker', weight='obese')
    assert ws.life_expectancy(healthy) > ws.life_expectancy(m) > ws.life_expectancy(smoker)


def test_cohort_improvement_raises_life_expectancy():
    p = ws.Person(65, 'f')
    assert ws.life_expectancy(p, 0.01) > ws.life_expectancy(p, 0.0)


def test_joint_survival_beats_each_single_curve():
    a, b = ws.Person(65, 'm'), ws.Person(65, 'f')
    joint = ws.joint_survival([a, b])
    sa, sb = ws.survival_curve(a), ws.survival_curve(b)
    n = min(len(sa), len(sb))
    assert np.all(joint[:n] >= np.maximum(sa[:n], sb[:n]) - 1e-12)


def test_risk_factor_is_clamped():
    worst = ws.Person(health='severe', smoking='smoker', activity='inactive',
                      weight='severely_obese', alcohol='high')
    assert worst.risk_factor() == ws.FACTOR_MAX


# ── market data helpers ───────────────────────────────────────────────────────

def test_split_jumps_are_removed():
    idx = pd.date_range('2020-01-31', periods=6, freq='ME')
    closes = pd.Series([100, 102, 51, 52, 53, 26.5], index=idx)   # 2:1 splits
    r = ws._remove_split_jumps(closes)
    assert r.iloc[2] == 0.0 and r.iloc[5] == 0.0
    assert r.iloc[1] == pytest.approx(0.02)


def test_return_cap_and_proxy_fill():
    idx = pd.date_range('2000-01-31', periods=240, freq='ME')
    ret = pd.DataFrame({'HOT': 0.03, 'NEW': np.nan}, index=idx)
    ret.iloc[-60:, 1] = 0.005
    proxy = pd.Series(0.004, index=idx)
    M, stats = ws.build_return_matrix(ret, proxy, 20, cap=0.08)
    assert len(M) == 240 and not M.isna().any().any()
    assert stats.loc['HOT', 'cagr_used'] == pytest.approx(0.08, abs=1e-9)
    assert stats.loc['NEW', 'coverage'] == pytest.approx(0.25)


# ── simulation ────────────────────────────────────────────────────────────────

def test_no_spending_never_ruins_and_wealth_compounds():
    cfg = ws.SimConfig(capital=100_000, spending=0, fixed_years=10, tax='off', ter=0,
                       inflation_mode='constant', n_sims=50)
    res = ws.simulate(cfg, _flat_returns(0.005))
    assert not res['ruined'].any()
    assert res['wealth'][:, -1] == pytest.approx(100_000 * 1.005 ** 120)


def test_overspending_ruins_in_fixed_period():
    cfg = ws.SimConfig(capital=100_000, spending=2_000, fixed_years=10, tax='off', ter=0,
                       inflation=0.0, inflation_mode='constant', n_sims=20)
    res = ws.simulate(cfg, _flat_returns(0.0))
    assert res['ruined'].all()
    assert (res['ruin_month'] == 50).all()          # 100k / 2k = 50 months


def test_pension_covers_spending():
    p = ws.Person(65, 'm', pension=3_000, pension_age=65)
    cfg = ws.SimConfig(capital=50_000, spending=2_500, persons=[p], care_enabled=False,
                       inflation_mode='constant', n_sims=100)
    res = ws.simulate(cfg, _flat_returns(0.0))
    assert not res['ruined'].any()


def test_tax_reduces_what_is_left():
    base = dict(capital=500_000, spending=2_000, fixed_years=20, ter=0, initial_gain_share=0.5,
                inflation_mode='constant', n_sims=20)
    no_tax = ws.simulate(ws.SimConfig(tax='off', **base), _flat_returns(0.004))
    taxed = ws.simulate(ws.SimConfig(tax='none', **base), _flat_returns(0.004))
    assert taxed['wealth'][:, -1].mean() < no_tax['wealth'][:, -1].mean()


def test_deposit_earns_interest_and_buffer_shields_crash():
    base = dict(capital=100_000, spending=500, fixed_years=5, tax='off', ter=0,
                inflation_mode='constant', n_sims=10, cash_quota=0.5, cash_rate=0.03)
    R = np.r_[np.full((12, 1), -0.03), np.full((228, 1), 0.0)]   # one bad year
    reb = ws.simulate(ws.SimConfig(cash_mode='rebalance', block=240, **base), R)
    buf = ws.simulate(ws.SimConfig(cash_mode='bucket', block=240, **base), R)
    assert np.isfinite(reb['wealth']).all() and np.isfinite(buf['wealth']).all()
    assert not reb['ruined'].any() and not buf['ruined'].any()


def test_stochastic_inflation_has_spread_and_right_mean():
    cfg = ws.SimConfig(inflation=0.02, inflation_mode='stochastic', inflation_vol=0.012, seed=1)
    paths = ws.inflation_paths(cfg, np.random.default_rng(1), 4000, 30)
    assert paths.mean() == pytest.approx(0.02, abs=0.002)
    assert (paths > 0.04).mean() > 0.03


def test_couple_outlives_single_and_care_happens():
    single = ws.SimConfig(capital=1e6, spending=1_000, persons=[ws.Person(65, 'm')], n_sims=800)
    couple = ws.SimConfig(capital=1e6, spending=1_000,
                          persons=[ws.Person(65, 'm'), ws.Person(65, 'f')], n_sims=800)
    s1 = ws.summarize(ws.simulate(single, _flat_returns(0.004)))
    s2 = ws.summarize(ws.simulate(couple, _flat_returns(0.004)))
    assert s2['household_years_median'] > s1['household_years_median']
    assert 0.1 < s1['p_care_any'] < 0.6
    assert s2['p_care_any'] > s1['p_care_any']


def test_percentiles_and_sustainable_spending():
    cfg = ws.SimConfig(capital=500_000, spending=2_000, persons=[ws.Person(65, 'f')], n_sims=300)
    R = np.random.default_rng(3).normal(0.005, 0.04, (240, 1))
    pct = ws.wealth_percentiles(ws.simulate(cfg, R))
    assert {'p10', 'p50', 'p90', 'alive', 'solvent'} <= set(pct.columns)
    assert (pct['solvent'] <= pct['alive'] + 1e-12).all()
    s = ws.sustainable_spending(cfg, R, 0.10, n_sims=200, iters=8)
    assert 500 < s < 20_000


# ── asset classes ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('ticker,qt,name,expected', [
    ('SAP.DE', 'EQUITY', 'SAP SE', 'stock'),
    ('EUNL.DE', 'ETF', 'iShares Core MSCI World UCITS ETF', 'etf'),
    ('GC=F', 'FUTURE', '', 'commodity'),
    ('4GLD.DE', 'EQUITY', 'Xetra-Gold', 'commodity'),
    ('ABX.TO', 'EQUITY', 'Barrick Gold Corporation', 'stock'),
    ('XYZ.DE', 'ETF', 'Invesco Physical Gold ETC', 'commodity'),
])
def test_asset_class(ticker, qt, name, expected):
    assert ws.asset_class(ticker, qt, name) == expected


def test_default_inner_weights_per_class():
    classes = pd.Series({'A': 'stock', 'B': 'stock', 'E1': 'etf', 'E2': 'etf', 'G': 'commodity'})
    inner = ws.default_inner_weights(classes, pd.Series({'A': 300.0, 'B': 100.0}))
    assert inner['A'] == pytest.approx(75.0) and inner['B'] == pytest.approx(25.0)
    assert inner['E1'] == pytest.approx(50.0) and inner['G'] == pytest.approx(100.0)


def test_class_target_weights():
    classes = pd.Series({'A': 'stock', 'B': 'stock', 'E': 'etf', 'G': 'commodity'})
    inner = pd.Series({'A': 75.0, 'B': 25.0, 'E': 100.0, 'G': 100.0})
    w, shares = ws.class_target_weights(classes, inner, {'stock': 60, 'etf': 30, 'commodity': 10})
    assert w.sum() == pytest.approx(1.0)
    assert w['A'] == pytest.approx(0.45) and w['B'] == pytest.approx(0.15)
    assert w['E'] == pytest.approx(0.30) and w['G'] == pytest.approx(0.10)
    # target for a class without assets is dropped and the rest scaled up
    w2, shares2 = ws.class_target_weights(classes.drop('G'), inner.drop('G'),
                                          {'stock': 60, 'etf': 30, 'commodity': 10})
    assert shares2['commodity'] == 0 and shares2['stock'] == pytest.approx(60 / 90)
    assert w2.sum() == pytest.approx(1.0)
    # sum != 100 is normalised; all zero gives no weights
    w3, _ = ws.class_target_weights(classes, inner, {'stock': 120, 'etf': 60, 'commodity': 20})
    assert w3['A'] == pytest.approx(0.45)
    w4, _ = ws.class_target_weights(classes, inner, {})
    assert w4.sum() == 0


# ── withdrawal rules ──────────────────────────────────────────────────────────

def _rule_run(rule, R, **kw):
    base = dict(capital=500_000, spending=2_500, fixed_years=30, tax='off', ter=0,
                inflation_mode='constant', inflation=0.02, n_sims=300, withdrawal_rule=rule)
    base.update(kw)
    return ws.simulate(ws.SimConfig(**base), R)


def test_fixed_rule_spends_exactly_the_plan():
    res = _rule_run('fixed', _flat_returns(0.004), spending=1_500)
    assert not res['ruined'].any()
    assert np.nanmax(np.abs(res['consumption'] - res['planned'])) < 1e-6
    s = ws.summarize(res)
    assert s['spend_worst_ratio_p10'] == pytest.approx(1.0) and s['spend_cut_years'] == 0


@pytest.mark.parametrize('rule', ['guardrails', 'percent', 'vol', 'skip_infl'])
def test_dynamic_rules_respect_floor_and_ceiling(rule):
    R = np.random.default_rng(5).normal(0.004, 0.05, (240, 1))
    res = _rule_run(rule, R, spend_floor=0.8, spend_ceiling=1.2)
    ratio = res['consumption'] / res['planned']
    ok = np.isfinite(ratio) & ~res['ruined'][:, None]
    assert ratio[ok].min() >= 0.8 - 1e-9 and ratio[ok].max() <= 1.2 + 1e-9


def test_rules_cut_after_a_crash_and_fixed_does_not():
    R = np.r_[np.full((24, 1), -0.03), np.full((216, 1), 0.004)]   # two bad years, then calm
    for rule in ('guardrails', 'percent', 'vol', 'skip_infl'):
        res = _rule_run(rule, R, block=240, spend_floor=0.5, fixed_years=20)
        assert ws.summarize(res)['spend_worst_ratio_p50'] < 0.99, rule
    fixed = _rule_run('fixed', R, block=240, spending=1_200, fixed_years=20)
    assert not fixed['ruined'].any()
    assert ws.summarize(fixed)['spend_worst_ratio_p50'] == pytest.approx(1.0)


def test_cutting_spending_improves_survival():
    R = np.random.default_rng(9).normal(0.003, 0.05, (240, 1))
    fixed = _rule_run('fixed', R, spending=3_200, n_sims=600)
    skip = _rule_run('skip_infl', R, spending=3_200, n_sims=600)
    assert skip['ruined'].mean() < fixed['ruined'].mean()


def test_percent_rate_applies_from_the_first_month():
    res = _rule_run('percent', _flat_returns(0.0), spending=2_000, percent_rate=0.03,
                    spend_floor=0.1, spend_ceiling=3.0)
    # 3 % of 500k = 15k a year = 1250 a month (no pension)
    assert res['consumption'][0, 0] == pytest.approx(1250.0, rel=1e-6)


# ── switching from today's holdings to the target ─────────────────────────────

def test_switch_cost_taxes_only_sales():
    cur = pd.Series({'A': 100_000.0, 'B': 50_000.0})
    tgt = pd.Series({'A': 40_000.0, 'B': 50_000.0, 'E': 60_000.0})
    out = ws.switch_cost(cur, tgt, gain_share=0.5, tax_rate=0.25, allowance=1_000.0)
    assert out['sold'] == pytest.approx(60_000.0)
    assert out['tax'] == pytest.approx((60_000 * 0.5 - 1_000) * 0.25)
    assert out['kept_gain'] == pytest.approx((40_000 + 50_000) * 0.5)


def test_switch_cost_partial_exemption_and_no_sale():
    cur = pd.Series({'ETF': 10_000.0})
    out = ws.switch_cost(cur, pd.Series({'ETF': 0.0}), 1.0 / 0.99 * 0.99, 0.25, 0.0,
                         exemption=pd.Series({'ETF': 0.3}))
    assert out['tax'] == pytest.approx(10_000 * 0.99 * 0.7 * 0.25)
    none = ws.switch_cost(cur, pd.Series({'ETF': 20_000.0}), 0.5, 0.25)
    assert none['tax'] == 0 and none['sold'] == 0


# ── tax regimes (Germany / Spain) ─────────────────────────────────────────────

def test_spanish_brackets():
    es = ws.tax_regime('es')
    assert es['allowance'] == 0 and es['fund_exemption'] == 0
    assert ws.progressive_tax(10_000, es['brackets']) == pytest.approx(6_000 * 0.19 + 4_000 * 0.21)
    assert ws.progressive_tax(1_000, es['brackets'], already=5_500) == pytest.approx(500 * 0.19 + 500 * 0.21)
    assert list(ws.marginal_rate([0, 6_000, 60_000, 400_000], es['brackets'])) == [0.19, 0.21, 0.23, 0.30]


def test_legacy_tax_keys_still_work():
    assert ws.tax_regime('none') == ws.tax_regime('de')
    assert ws.tax_regime('kist9')['brackets'][0][1] == pytest.approx(0.27995)


def test_switch_cost_progressive_splits_over_persons():
    cur, tgt = pd.Series({'A': 100_000.0}), pd.Series({'A': 0.0})
    b = ws.tax_regime('es')['brackets']
    one = ws.switch_cost(cur, tgt, 0.5, 0.0, brackets=b, persons=1)['tax']
    two = ws.switch_cost(cur, tgt, 0.5, 0.0, brackets=b, persons=2)['tax']
    assert one == pytest.approx(ws.progressive_tax(50_000, b))
    assert two == pytest.approx(2 * ws.progressive_tax(25_000, b))
    assert two < one


def test_spain_vs_germany_in_simulation():
    base = dict(capital=500_000, spending=1_500, fixed_years=25, ter=0, initial_gain_share=0.6,
                inflation_mode='constant', n_sims=50, cash_quota=0.3, cash_rate=0.03)
    R = _flat_returns(0.005)
    de_stock = ws.simulate(ws.SimConfig(tax='de', partial_exemption=np.array([0.0]), **base), R)
    de_etf = ws.simulate(ws.SimConfig(tax='de', partial_exemption=np.array([0.3]), **base), R)
    es = ws.simulate(ws.SimConfig(tax='es', partial_exemption=np.array([0.0]), **base), R)
    off = ws.simulate(ws.SimConfig(tax='off', **base), R)
    end = lambda r: r['wealth'][:, -1].mean()
    assert not es['ruined'].any() and not de_stock['ruined'].any()
    # single stocks: Spain 19-21 % beats Germany 26.4 %
    assert end(off) > end(es) > end(de_stock)
    # equity ETFs: German partial exemption (26.4 % x 0.7 = 18.5 %) + allowance beat Spain
    assert end(de_etf) > end(es)
    custom = ws.simulate(ws.SimConfig(tax='es', tax_brackets=[(float('inf'), 0.5)], **base), R)
    assert custom['wealth'][:, -1].mean() < es['wealth'][:, -1].mean()


# ── Spanish wealth tax ────────────────────────────────────────────────────────

def test_wealth_tax_es_values():
    assert float(ws.wealth_tax_es(700_000)) == 0.0
    # 300k above the exemption: 167,129.45 at 0.2 % + rest at 0.3 %
    assert float(ws.wealth_tax_es(1_000_000)) == pytest.approx(167_129.45 * 0.002 + 132_870.55 * 0.003)
    assert float(ws.wealth_tax_es(2_000_000, bonus=1.0)) == 0.0          # Madrid: below 3 M + 700k
    # solidarity tax above 3.7 M with full rebate: 300k at 1.7 %
    assert float(ws.wealth_tax_es(4_000_000, bonus=1.0)) == pytest.approx(5_100.0)
    # without rebate the solidarity tax is fully credited by the wealth tax
    full = float(ws.wealth_tax_es(4_000_000))
    assert full == pytest.approx(float(ws.wealth_tax_es(4_000_000, solidarity=False)))


def test_wealth_tax_reduces_wealth_and_is_reported():
    base = dict(capital=3_000_000, spending=3_000, fixed_years=10, ter=0, tax='off',
                inflation_mode='constant', n_sims=20, persons=[ws.Person(65, 'm')])
    R = _flat_returns(0.004)
    no = ws.simulate(ws.SimConfig(**base), R)
    yes = ws.simulate(ws.SimConfig(wealth_tax=True, wt_cap60=False, **base), R)
    assert yes['wealth'][:, -1].mean() < no['wealth'][:, -1].mean()
    s = ws.summarize(yes)
    assert s['wealth_tax_year_p50'] > 10_000
    assert 'wealth_tax_total_p50' not in ws.summarize(no)


# ── 60 % joint cap (Spain) and stale configs ──────────────────────────────────

def test_wealth_tax_cap():
    wt = float(ws.wealth_tax_es(5_000_000))
    # low income: the cap would wipe it out, but 20 % always remains
    assert float(ws.wealth_tax_cap_es(wt, 20_000, 2_000)) == pytest.approx(0.2 * wt)
    # high income: no reduction
    assert float(ws.wealth_tax_cap_es(wt, 1_000_000, 0)) == pytest.approx(wt)
    # partial: reduced exactly by the excess over 60 %
    wt2 = float(ws.wealth_tax_es(3_000_000))
    it = float(ws.irpf_general_es(30_000)) + ws.progressive_tax(20_000, ws.tax_regime('es')['brackets'])
    expected = wt2 - (wt2 + it - 0.6 * 50_000)
    assert float(ws.wealth_tax_cap_es(wt2, 30_000, 20_000)) == pytest.approx(expected)


def test_cap_lowers_wealth_tax_in_simulation():
    base = dict(capital=5_000_000, spending=3_000, fixed_years=5, ter=0, tax='es',
                inflation_mode='constant', n_sims=10, persons=[ws.Person(70, 'f')], wealth_tax=True,
                wt_general_income=20_000)
    R = _flat_returns(0.004)
    capped = ws.summarize(ws.simulate(ws.SimConfig(wt_cap60=True, **base), R))
    full = ws.summarize(ws.simulate(ws.SimConfig(wt_cap60=False, **base), R))
    assert capped['wealth_tax_year_p50'] < 0.5 * full['wealth_tax_year_p50']


def test_simulate_accepts_config_of_an_older_version():
    import dataclasses

    @dataclasses.dataclass
    class OldConfig:                      # like a SimConfig kept in the session before an update
        capital: float = 100_000.0
        spending: float = 300.0
        fixed_years: int = 5
        n_sims: int = 5
        weights: np.ndarray = dataclasses.field(default_factory=lambda: np.array([1.0]))
    res = ws.simulate(OldConfig(), _flat_returns(0.0))
    assert res['months'] == 60


# ── Canary Islands income-tax scale ───────────────────────────────────────────

def test_canarias_scale_matches_state_plus_region():
    combined = ws.combine_brackets(ws.IRPF_STATE_ES, ws.IRPF_AUTONOMIC_CANARIAS)
    assert [(u, round(r, 6)) for u, r in combined] == [(u, round(r, 6)) for u, r in ws.IRPF_GENERAL_CANARIAS]
    assert float(ws.marginal_rate(10_000, ws.IRPF_GENERAL_CANARIAS)) == pytest.approx(0.185)
    assert float(ws.marginal_rate(400_000, ws.IRPF_GENERAL_CANARIAS)) == pytest.approx(0.505)


def test_region_changes_the_cap():
    wt = float(ws.wealth_tax_es(3_000_000))
    avg = float(ws.wealth_tax_cap_es(wt, 30_000, 20_000, region='avg'))
    can = float(ws.wealth_tax_cap_es(wt, 30_000, 20_000, region='canarias'))
    # lower income tax on 30k in the Canaries -> less excess -> slightly more wealth tax remains
    assert can > avg
    assert float(ws.irpf_general_es(30_000, 'canarias')) < float(ws.irpf_general_es(30_000))


def test_spending_percentiles_nominal_grows_with_inflation():
    cfg = ws.SimConfig(capital=500_000, spending=1_500, fixed_years=20, tax='off', ter=0,
                       inflation_mode='constant', inflation=0.03, n_sims=20)
    res = ws.simulate(cfg, _flat_returns(0.005))
    real = ws.spending_percentiles(res, real=True)
    nom = ws.spending_percentiles(res, real=False)
    assert real['p50'].iloc[-1] == pytest.approx(real['p50'].iloc[0], rel=1e-6)   # flat in today's money
    assert nom['p50'].iloc[-1] / nom['p50'].iloc[0] == pytest.approx(1.03 ** 19, rel=0.02)


def test_page_settings_filter():
    from tradinglib import withdrawal_simulation_page as page
    ok = page._is_setting
    assert ok('hh', 'couple') and ok('p0_age', 67) and ok('impr', 1.5) and ok('care', True)
    assert ok('extras', ['EUNL.DE', 'GC=F'])
    # results, buttons, editors and data-dependent choices are not settings
    for name, value in (('results', {}), ('run', False), ('set_save', False), ('cls_ed_123', {}),
                        ('tax_br_es', {}), ('scen', ['current']), ('chart_sc', 'target'), ('loaded', True),
                        ('shadow', {'hh': 'couple'}), ('saved_snapshot', {})):
        assert not ok(name, value), name
    assert not ok('weird', {'a': 1})


# ── asset classes over the lifetime ───────────────────────────────────────────

def test_class_growth_and_composition():
    R = np.c_[np.full(240, 0.01), np.full(240, 0.0)]          # stocks +1 %/month, commodity flat
    cfg = ws.SimConfig(capital=1_000_000, weights=np.array([0.5, 0.5]), asset_classes=['stock', 'commodity'],
                       cash_quota=0.2, cash_rate=0.024, spending=0, fixed_years=10, tax='off', ter=0,
                       inflation_mode='constant', inflation=0.0, n_sims=5)
    res = ws.simulate(cfg, R)
    g = ws.class_growth(res, R, ['stock', 'commodity'], [1.0, 1.0], cfg)
    assert set(g) == {'stock', 'commodity', 'cash'}
    assert g['stock']['p50'].iloc[1] == pytest.approx(1.01 ** 12)
    assert g['commodity']['p50'].iloc[5] == pytest.approx(1.0)
    assert g['cash']['p50'].iloc[1] == pytest.approx(1.002 ** 12)
    comp = ws.composition_percentiles(res)
    assert comp.loc[0, 'stock'] == pytest.approx(400_000) and comp.loc[0, 'cash'] == pytest.approx(200_000)
    assert comp.loc[0, 'etf'] == 0
    # stocks grow, so after a year (before the rebalancing at the next year start) they lead
    assert comp.loc[1, 'stock'] > comp.loc[1, 'commodity']


def test_without_withdrawals_keeps_more_wealth():
    R = np.random.default_rng(4).normal(0.004, 0.03, (240, 1))
    cfg = ws.SimConfig(capital=500_000, spending=2_500, persons=[ws.Person(65, 'm', pension=800)],
                       cash_quota=0.2, n_sims=200, tax='es')
    with_wd = ws.simulate(cfg, R)
    free_cfg = ws.without_withdrawals(cfg)
    assert free_cfg.spending == 0 and free_cfg.persons[0].pension == 0 and not free_cfg.care_enabled
    assert cfg.spending == 2_500 and cfg.persons[0].pension == 800        # original untouched
    free = ws.simulate(free_cfg, R)
    assert not free['ruined'].any()
    t_with, t_free = ws.composition_total(with_wd), ws.composition_total(free)
    assert t_with.iloc[0] == pytest.approx(t_free.iloc[0])
    assert (t_free.iloc[5:15] > t_with.iloc[5:15]).all()


# ── bucket mode invests deposits above the quota; net flow ────────────────────

def test_bucket_invests_surplus_deposits():
    P = [ws.Person(65, 'm', pension=3_000, pension_age=65)]
    R = _flat_returns(0.004)
    res = ws.simulate(ws.SimConfig(capital=500_000, cash_quota=0.3, cash_rate=0.02, cash_mode='bucket',
                                   spending=1_500, persons=P, care_enabled=False, fixed_years=15,
                                   tax='off', ter=0, inflation_mode='constant', n_sims=5), R)
    comp = ws.composition_percentiles(res)
    share = comp.loc[10, 'cash'] / comp.loc[10, ['stock', 'etf', 'commodity', 'cash']].sum()
    assert share < 0.35                     # stays near the 30 % quota instead of piling up
    nf = ws.net_flow_summary(res)
    assert nf['flow_p50'] == pytest.approx(-1_500 * 12, rel=0.01)    # pays in 1,500 a month


def test_net_flow_is_positive_when_withdrawing():
    res = ws.simulate(ws.SimConfig(capital=500_000, spending=1_500, fixed_years=10, tax='off', ter=0,
                                   inflation_mode='constant', n_sims=5), _flat_returns(0.004))
    assert ws.net_flow_summary(res)['flow_p50'] == pytest.approx(1_500 * 12, rel=0.01)


def test_explanation_lines_follow_the_numbers():
    from tradinglib import withdrawal_simulation_page as page
    P = [ws.Person(66, 'm', pension=1_500, pension_age=66), ws.Person(65, 'f', pension=1_500, pension_age=66)]
    cfg = ws.SimConfig(capital=500_000, spending=4_000, persons=P, n_sims=200, withdrawal_rule='vol')
    res = ws.simulate(cfg, np.random.default_rng(6).normal(0.004, 0.035, (240, 1)))
    r = {'sum': ws.summarize(res), 'cfg': cfg, 'res': res,
         'start': {'capital': 500_000, 'quota': 0.2, 'gain': 0.0, 'tax': 0.0, 'sold': 0.0}}
    text = '\n'.join(page.explain_lines(r))
    assert 'wd.expl_success' in text or '%' in text               # works with or without loaded locale
    lines = page.explain_lines(r)
    assert len(lines) >= 7
    # the later pension of person 2 is mentioned, the start tax line is not (nothing sold)
    assert any('expl_pension_later' in ln or '1.500' in ln for ln in lines)


# ── one-off expenses ──────────────────────────────────────────────────────────

def test_expand_expenses_calendar_and_repeat():
    import datetime as dt
    today = dt.date(2026, 11, 22)
    rows = [{'year': 2027, 'amount': 30_000}, {'year': 2026, 'amount': 1_000},
            {'year': 2030, 'amount': 25_000, 'every': 8, 'until': 2046},
            {'year': 2020, 'amount': 5_000}, {'year': 2028, 'amount': 0}]
    out = ws.expand_expenses(rows, today)
    assert (2, 30_000.0) in out                     # January 2027 = 2 months from November 2026
    assert (0, 1_000.0) in out                      # this year: now
    assert [m for m, a in out if a == 25_000.0] == [38, 134, 230]   # 2030, 2038, 2046
    assert all(a != 5_000 for _, a in out) and all(a > 0 for _, a in out)


def test_one_off_expenses_are_paid_with_inflation():
    cfg = ws.SimConfig(capital=200_000, spending=0, fixed_years=5, tax='off', ter=0,
                       inflation_mode='constant', inflation=0.02, n_sims=3,
                       one_off_expenses=[(2, 30_000.0), (26, 10_000.0)])
    res = ws.simulate(cfg, np.zeros((60, 1)))
    w = res['wealth'][0]
    assert w[2] - w[3] == pytest.approx(30_000 * 1.02 ** (2 / 12))
    assert w[26] - w[27] == pytest.approx(10_000 * 1.02 ** (26 / 12))
    assert ws.net_flow_summary(res)['flow_p50'] > 0
