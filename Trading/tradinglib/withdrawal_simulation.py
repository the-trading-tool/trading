"""Withdrawal simulator (Entnahme-Simulator) — Monte Carlo over the whole retirement.

Pure computation, no Streamlit (the page lives in ``withdrawal_simulation_page``).

What one simulation path does, month by month:

* Markets: monthly returns in the system currency, drawn as 12-month blocks from
  the joint history of all assets (block bootstrap). Drawing the same months for
  every asset keeps their correlation and the momentum/crash clustering that a
  normal-distribution model loses.
* Portfolio: risky part (stocks, ETFs, commodities) + a fixed-deposit part
  (Festgeld) at a fixed interest rate. Annual rebalancing of the risky part;
  the deposit is either held at its quota (``rebalance``) or used as a buffer
  that is drawn first and refilled only after a positive market year (``bucket``).
* Costs and taxes: running costs (TER) monthly; German flat-rate tax
  (Abgeltungsteuer + Soli, optional church tax) on the gain share of every sale
  and on deposit interest, Sparerpauschbetrag per living person and year,
  Teilfreistellung for equity funds.
* People: one person or a couple. Each year every living person may die or
  become a care case (Pflegefall). Mortality follows a Gompertz-Makeham curve
  calibrated to the Destatis Periodensterbetafel 2023/2025, adjusted for health,
  lifestyle and the yearly mortality improvement (cohort effect). A care case
  costs an extra monthly co-payment and raises mortality.
* Cash flow: the household spends a fixed net amount (inflation-adjusted,
  optionally falling with age), less the statutory pensions; when one partner
  dies, spending drops to the survivor factor and the survivor gets the widow's
  share of the partner's pension.

Ruin = the household is alive and the portfolio cannot pay the month's need.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Mortality — Gompertz-Makeham, calibrated to Destatis Sterbetafel 2023/2025
# ─────────────────────────────────────────────────────────────────────────────
# mu(x) = A + B * exp(c * x). c from the shape of the German old-age curve,
# B solved so that the remaining life expectancy at 65 matches Destatis
# 2023/2025 (men 18.0, women 21.1 years). Check: e0 comes out at 79.0 / 84.0
# (Destatis 78.8 / 83.4; the model ignores infant and accident mortality).
LIFE_TABLE_SOURCE = "Destatis Periodensterbetafel 2023/2025"
MAKEHAM_A = 0.0002
GOMPERTZ = {
    'm': (1.7775886446e-05, 0.1013),
    'f': (2.0564869954e-06, 0.1230),
}
DESTATIS_E65 = {'m': 18.0, 'f': 21.1}
DESTATIS_E0 = {'m': 78.8, 'f': 83.4}
MAX_AGE = 110

# Mortality multipliers relative to the population average (the life table
# already mixes healthy and sick, smokers and non-smokers). Rounded hazard
# ratios from large cohort studies — guide values, not a medical assessment.
HEALTH_FACTORS = {'very_good': 0.80, 'good': 1.00, 'chronic': 1.50, 'severe': 2.50}
SMOKING_FACTORS = {'never': 0.85, 'former': 1.05, 'smoker': 1.80}
ACTIVITY_FACTORS = {'active': 0.80, 'normal': 1.00, 'inactive': 1.30}
WEIGHT_FACTORS = {'normal': 1.00, 'overweight': 1.00, 'obese': 1.30, 'severely_obese': 1.80}
ALCOHOL_FACTORS = {'low': 1.00, 'moderate': 1.05, 'high': 1.35}
FACTOR_MIN, FACTOR_MAX = 0.4, 4.0

# Care (Pflegefall with co-payment): yearly onset probability at age x,
# 0.25 % at 60, doubling about every 6 years (≈2.5 % at 80, ≈8 % at 90).
CARE_ONSET_BASE, CARE_ONSET_SLOPE, CARE_ONSET_MAX = 0.0025, 0.115, 0.6
# Mortality in care: three times the normal rate, at least 20 % a year.
CARE_MORTALITY_MULT, CARE_MORTALITY_MIN = 3.0, 0.20

# German flat-rate tax: 25 % + 5.5 % Soli; church tax replaces part of it.
TAX_RATES = {'none': 0.26375, 'kist8': 0.27819, 'kist9': 0.27995}
SAVER_ALLOWANCE = 1000.0            # Sparerpauschbetrag per person and year
PARTIAL_EXEMPTION_EQUITY_FUND = 0.30

# Tax on capital income, per person and calendar year: brackets as (upper limit, rate)
# with the last limit = inf, a yearly allowance per person and the partial exemption
# of equity funds. Limits are nominal (not indexed to inflation — Spain does not index).
INF = float('inf')
TAX_REGIMES = {
    'de': {'brackets': [(INF, 0.26375)], 'allowance': 1000.0, 'fund_exemption': 0.30},
    'de_kist8': {'brackets': [(INF, 0.27819)], 'allowance': 1000.0, 'fund_exemption': 0.30},
    'de_kist9': {'brackets': [(INF, 0.27995)], 'allowance': 1000.0, 'fund_exemption': 0.30},
    # Spain, base imponible del ahorro (since 2025): gains, interest and dividends
    'es': {'brackets': [(6_000.0, 0.19), (50_000.0, 0.21), (200_000.0, 0.23),
                        (300_000.0, 0.27), (INF, 0.30)],
           'allowance': 0.0, 'fund_exemption': 0.0},
    'off': {'brackets': [(INF, 0.0)], 'allowance': 0.0, 'fund_exemption': 0.0},
}
_LEGACY_TAX = {'none': 'de', 'kist8': 'de_kist8', 'kist9': 'de_kist9', 'off': 'off'}


# Spanish wealth tax (Impuesto sobre el Patrimonio), state scale (Ley 19/1991, art. 30) on the
# net wealth per person above the exemption, and the solidarity tax on large fortunes (ITSGF):
# 0 % up to 3 M€ of the same base, regional wealth tax paid is credited. Limits are nominal.
WEALTH_TAX_EXEMPTION = 700_000.0
WEALTH_TAX_SCALE_ES = [(167_129.45, 0.002), (334_252.88, 0.003), (668_499.75, 0.005),
                       (1_336_999.51, 0.009), (2_673_999.01, 0.013), (5_347_998.03, 0.017),
                       (10_695_996.06, 0.021), (INF, 0.035)]
SOLIDARITY_SCALE_ES = [(3_000_000.0, 0.0), (5_347_998.03, 0.017), (10_695_996.06, 0.021), (INF, 0.035)]


def progressive_tax_vec(amounts, brackets) -> np.ndarray:
    """Vectorised progressive tax on each element of *amounts* (from zero)."""
    uppers, rates = _bracket_arrays(brackets)
    a = np.maximum(np.asarray(amounts, dtype=float), 0.0)
    lowers = np.r_[0.0, uppers[:-1]]
    out = np.zeros_like(a)
    for lo, up, r in zip(lowers, uppers, rates):
        out += r * np.clip(a - lo, 0.0, up - lo)
    return out


def wealth_tax_es(net_wealth_per_person, exemption: float = WEALTH_TAX_EXEMPTION,
                  scale=None, bonus: float = 0.0, solidarity: bool = True) -> np.ndarray:
    """Wealth tax + solidarity tax per person for net wealth per person (nominal €).

    *bonus* = regional rebate on the wealth tax (1.0 = 100 %, e.g. Madrid/Andalucía).
    The solidarity tax uses the state exemption of 700 k€ and credits the wealth tax paid.
    """
    w = np.maximum(np.asarray(net_wealth_per_person, dtype=float), 0.0)
    ip = progressive_tax_vec(w - exemption, scale or WEALTH_TAX_SCALE_ES) * (1.0 - np.clip(bonus, 0.0, 1.0))
    if not solidarity:
        return ip
    sol = progressive_tax_vec(w - WEALTH_TAX_EXEMPTION, SOLIDARITY_SCALE_ES)
    return ip + np.maximum(sol - ip, 0.0)


# Spanish IRPF, general base (state + average regional scale 2025) and the personal minimum
# from 65 — used only for the joint 60 % cap of the wealth tax (art. 31 Ley 19/1991).
IRPF_GENERAL_ES = [(12_450.0, 0.19), (20_200.0, 0.24), (35_200.0, 0.30), (60_000.0, 0.37),
                   (300_000.0, 0.45), (INF, 0.47)]
IRPF_PERSONAL_MIN_65 = 6_700.0
# State half 2025 (9.5 / 12 / 15 / 18.5 / 22.5 / 24.5 %) + autonomic scale of the Canary Islands 2025
# (9 / 11.5 / 14 / 18.5 / 23.5 / 25 / 26 %; AEAT Manual Renta 2025, top limit 123,745 € per calcuenta.es).
IRPF_STATE_ES = [(12_450.0, 0.095), (20_200.0, 0.12), (35_200.0, 0.15), (60_000.0, 0.185),
                 (300_000.0, 0.225), (INF, 0.245)]
IRPF_AUTONOMIC_CANARIAS = [(13_748.0, 0.09), (19_422.0, 0.115), (35_924.0, 0.14), (57_566.0, 0.185),
                           (93_268.0, 0.235), (123_745.0, 0.25), (INF, 0.26)]


def combine_brackets(*scales) -> list:
    """Sum of several progressive scales as one scale (state half + regional half)."""
    cuts = sorted({float(u) for sc in scales for u, _ in sc})
    out = []
    for u in cuts:
        r = sum(float(marginal_rate(u - 1e-6, sc)) for sc in scales)
        out.append((u, r))
    return out


# = combine_brackets(IRPF_STATE_ES, IRPF_AUTONOMIC_CANARIAS), written out (checked in the tests)
IRPF_GENERAL_CANARIAS = [(12_450.0, 0.185), (13_748.0, 0.21), (19_422.0, 0.235), (20_200.0, 0.26),
                         (35_200.0, 0.29), (35_924.0, 0.325), (57_566.0, 0.37), (60_000.0, 0.42),
                         (93_268.0, 0.46), (123_745.0, 0.475), (300_000.0, 0.485), (INF, 0.505)]
IRPF_GENERAL_REGIONS = {
    'avg': IRPF_GENERAL_ES,
    'canarias': IRPF_GENERAL_CANARIAS,
}
WEALTH_TAX_CAP_SHARE = 0.60          # wealth tax + income tax <= 60 % of taxable income
WEALTH_TAX_MIN_SHARE = 0.20          # ... but at least 20 % of the wealth tax is paid


def irpf_general_es(income, region: str = 'avg') -> np.ndarray:
    """Approximate Spanish income tax on general income (e.g. gross pension) per person.

    *region*: 'avg' (state + average regional scale) or 'canarias'.
    """
    scale = IRPF_GENERAL_REGIONS.get(region, IRPF_GENERAL_ES)
    inc = np.maximum(np.asarray(income, dtype=float), 0.0)
    return np.maximum(progressive_tax_vec(inc, scale)
                      - progressive_tax_vec(np.minimum(inc, IRPF_PERSONAL_MIN_65), scale), 0.0)


def wealth_tax_cap_es(wealth_tax, general_income, savings_income, savings_brackets=None,
                      region: str = 'avg') -> np.ndarray:
    """Wealth tax after the joint 60 % cap (límite conjunto), per person.

    Income for the cap: general income (pensions etc.) + savings income WITHOUT gains on
    assets held for more than a year (interest, dividends). If wealth tax + income tax on
    that income exceed 60 % of it, the wealth tax is reduced by the excess, by at most 80 %.
    """
    wt = np.maximum(np.asarray(wealth_tax, dtype=float), 0.0)
    gi = np.maximum(np.asarray(general_income, dtype=float), 0.0)
    si = np.maximum(np.asarray(savings_income, dtype=float), 0.0)
    income_tax = irpf_general_es(gi, region) + progressive_tax_vec(si, savings_brackets or TAX_REGIMES['es']['brackets'])
    excess = np.maximum(wt + income_tax - WEALTH_TAX_CAP_SHARE * (gi + si), 0.0)
    return wt - np.minimum(excess, (1.0 - WEALTH_TAX_MIN_SHARE) * wt)


def tax_regime(name: str) -> dict:
    """Preset by name ('de', 'es', …; old keys 'none'/'kist8'/'kist9' still work)."""
    key = _LEGACY_TAX.get(name, name)
    r = TAX_REGIMES.get(key, TAX_REGIMES['de'])
    return {'brackets': list(r['brackets']), 'allowance': r['allowance'],
            'fund_exemption': r['fund_exemption']}


def _bracket_arrays(brackets) -> tuple[np.ndarray, np.ndarray]:
    b = sorted(((float(u) if u is not None and np.isfinite(float(u)) else INF), float(r))
               for u, r in brackets) or [(INF, 0.0)]
    if b[-1][0] != INF:
        b.append((INF, b[-1][1]))
    return np.array([u for u, _ in b]), np.array([r for _, r in b])


def marginal_rate(base, brackets) -> np.ndarray:
    """Rate of the bracket the next euro falls into, for income *base* already taxed."""
    uppers, rates = _bracket_arrays(brackets)
    i = np.searchsorted(uppers, np.asarray(base, dtype=float), side='right')
    return rates[np.minimum(i, len(rates) - 1)]


def progressive_tax(amount, brackets, already: float = 0.0) -> float:
    """Tax on *amount* on top of income *already* taxed this year (one person)."""
    uppers, rates = _bracket_arrays(brackets)
    lo, tax, left, pos = 0.0, 0.0, max(float(amount), 0.0), max(float(already), 0.0)
    for u, r in zip(uppers, rates):
        if left <= 0:
            break
        if pos < u:
            take = min(left, u - pos)
            tax += take * r
            left -= take
            pos += take
        lo = u
    return tax


@dataclass
class Person:
    age: int = 65
    sex: str = 'm'                  # 'm' | 'f'
    health: str = 'good'
    smoking: str = 'never'
    activity: str = 'normal'
    weight: str = 'normal'
    alcohol: str = 'low'
    pension: float = 0.0            # net statutory pension per month, today's money
    pension_age: int = 67
    table_average: bool = False     # True = population average of the life table (factor 1)

    @classmethod
    def average(cls, age: int, sex: str) -> 'Person':
        return cls(age=age, sex=sex, table_average=True)

    def risk_factor(self) -> float:
        if self.table_average:
            return 1.0
        f = (HEALTH_FACTORS.get(self.health, 1.0) * SMOKING_FACTORS.get(self.smoking, 1.0)
             * ACTIVITY_FACTORS.get(self.activity, 1.0) * WEIGHT_FACTORS.get(self.weight, 1.0)
             * ALCOHOL_FACTORS.get(self.alcohol, 1.0))
        return float(np.clip(f, FACTOR_MIN, FACTOR_MAX))


def _factor_at_age(factor, age):
    """Risk factors fade with age (hazard ratios converge towards 1 in the very old)."""
    w = np.clip(1.0 - (np.asarray(age, dtype=float) - 60.0) / 60.0, 0.4, 1.0)
    return np.power(factor, w)


def base_qx(age, sex: str):
    """One-year death probability of the average person (life table)."""
    B, c = GOMPERTZ['f' if sex == 'f' else 'm']
    x = np.asarray(age, dtype=float)
    hazard = MAKEHAM_A + B * np.exp(c * x) * (np.exp(c) - 1.0) / c
    return 1.0 - np.exp(-hazard)


def person_qx(age, sex: str, factor: float = 1.0, years_ahead=0, improvement: float = 0.0):
    """Death probability for a person with *factor*, *years_ahead* in the future."""
    q = base_qx(age, sex) * _factor_at_age(factor, age)
    q = q * np.power(1.0 - improvement, np.asarray(years_ahead, dtype=float))
    return np.clip(q, 0.0, 1.0)


def care_onset(age, factor: float = 1.0):
    """Yearly probability of becoming a care case with co-payment."""
    p = CARE_ONSET_BASE * np.exp(CARE_ONSET_SLOPE * (np.asarray(age, dtype=float) - 60.0))
    return np.clip(p * np.sqrt(_factor_at_age(factor, age)), 0.0, CARE_ONSET_MAX)


def survival_curve(person: Person, improvement: float = 0.0, max_age: int = MAX_AGE) -> np.ndarray:
    """P(alive) at the start of each year from now until *max_age* (index 0 = today = 1.0)."""
    years = np.arange(0, max(1, max_age - person.age))
    q = person_qx(person.age + years, person.sex, person.risk_factor(), years, improvement)
    return np.r_[1.0, np.cumprod(1.0 - q)]


def life_expectancy(person: Person, improvement: float = 0.0) -> float:
    """Remaining life expectancy in years (curtate + 1/2)."""
    s = survival_curve(person, improvement)
    return float(s[1:].sum() + 0.5)


def joint_survival(persons: list[Person], improvement: float = 0.0) -> np.ndarray:
    """P(at least one person alive) per year, aligned to the youngest horizon."""
    curves = [survival_curve(p, improvement) for p in persons]
    n = max(len(c) for c in curves)
    dead_all = np.ones(n)
    for c in curves:
        dead_all *= 1.0 - np.pad(c, (0, n - len(c)))
    return 1.0 - dead_all


# ─────────────────────────────────────────────────────────────────────────────
# Market data — monthly returns in the system currency from the local DBs
# ─────────────────────────────────────────────────────────────────────────────

_SPLIT_RATIOS = np.array([2, 3, 4, 5, 8, 10, 15, 20, 25, 50, 100], dtype=float)


def _db_dir(db_path: str = 'database') -> str:
    from tradinglib.fx_history import _db_dir as fx_dir
    return fx_dir(db_path)


def _read_closes(ticker: str, db_dir: str) -> pd.Series:
    """Monthly closes (month-end index) from yf_<T>.db: month_data, extended by day_data."""
    path = os.path.join(db_dir, f'yf_{ticker}.db')
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    parts = []
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        for tbl in ('month_data', 'day_data'):
            try:
                d = pd.read_sql_query(f'SELECT Date, Close FROM {tbl}', con)   # noqa: S608 (fixed names)
            except Exception:
                continue
            if not d.empty:
                parts.append(d)
    finally:
        con.close()
    if not parts:
        return pd.Series(dtype=float)
    d = pd.concat(parts, ignore_index=True)
    d['Date'] = pd.to_datetime(d['Date'], errors='coerce', utc=True).dt.tz_localize(None)
    d['Close'] = pd.to_numeric(d['Close'], errors='coerce')
    d = d.dropna().sort_values('Date')
    d = d[d['Close'] > 0]
    if d.empty:
        return pd.Series(dtype=float)
    s = d.groupby(d['Date'].dt.to_period('M'))['Close'].last()
    s.index = s.index.to_timestamp(how='end').normalize()
    return s


def _remove_split_jumps(closes: pd.Series) -> pd.Series:
    """Monthly returns with split-like jumps (price ratio ≈ 1:n or n:1) set to 0."""
    r = closes.pct_change()
    ratio = (closes / closes.shift(1)).to_numpy()
    with np.errstate(divide='ignore', invalid='ignore'):
        big = np.where(ratio >= 1, ratio, 1.0 / ratio)
        near = np.min(np.abs(big[:, None] / _SPLIT_RATIOS[None, :] - 1.0), axis=1) < 0.03
    r[near & (big >= 1.9)] = 0.0
    return r


def asset_meta(tickers: list[str], db_path: str = 'database') -> pd.DataFrame:
    """currency, quoteType, name and dividend yield (fraction) from asset_info.db."""
    out = pd.DataFrame({'ticker': tickers}).set_index('ticker')
    out['currency'], out['quoteType'], out['name'], out['divYield'] = 'EUR', '', '', 0.0
    path = os.path.join(_db_dir(db_path), 'asset_info.db')
    if not tickers or not os.path.exists(path):
        return out
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        ph = ','.join('?' * len(tickers))
        df = pd.read_sql_query(
            f'SELECT ticker, currency, quoteType, longName, shortName, dividendYield '
            f'FROM asset_info WHERE ticker IN ({ph})', con, params=tuple(tickers))
    except Exception:
        logger.debug('asset_info lookup failed', exc_info=True)
        return out
    finally:
        con.close()
    for r in df.drop_duplicates('ticker', keep='last').itertuples():
        out.loc[r.ticker, 'currency'] = r.currency or 'EUR'
        out.loc[r.ticker, 'quoteType'] = r.quoteType or ''
        out.loc[r.ticker, 'name'] = r.longName or r.shortName or ''
        dy = pd.to_numeric(r.dividendYield, errors='coerce')
        # yfinance >= 0.2.5x reports dividendYield in percent (3.58 = 3.58 %)
        out.loc[r.ticker, 'divYield'] = float(dy) / 100.0 if pd.notna(dy) and 0 < dy < 25 else 0.0
    return out


def is_commodity(ticker: str, quote_type: str) -> bool:
    return ticker.endswith('=F') or quote_type == 'FUTURE'


# ─────────────────────────────────────────────────────────────────────────────
# Asset classes: stocks / ETFs / commodities
# ─────────────────────────────────────────────────────────────────────────────
ASSET_CLASSES = ('stock', 'etf', 'commodity')
# Exchange-traded commodities that Yahoo files as EQUITY or ETF.
COMMODITY_ETC = {'4GLD.DE', 'GLDA.DE', 'IGLD.DE', 'EWG2.DE', 'PPFB.DE', 'XAD5.DE', 'XAD1.DE',
                 'PHAU.L', 'SGLN.L', 'SGLD.L', 'GLD', 'IAU', 'SLV', 'PPLT', 'PALL', 'USO', 'DBC',
                 '3GLD.DE', 'ALGLD.PA'}
_COMMODITY_NAME = re.compile(
    r'xetra-gold|euwax gold|physical (gold|silver|platinum|palladium|metal)|\betc\b|'
    r'\bgold (bullion|trust|shares)\b|commodit|rohstoff|\bbloomberg .*commodity', re.I)


def asset_class(ticker: str, quote_type: str = '', name: str = '') -> str:
    """'stock', 'etf' or 'commodity' — a default the user can override on the page."""
    qt = str(quote_type or '').upper()
    nm = str(name or '')
    if is_commodity(ticker, qt) or ticker in COMMODITY_ETC:
        return 'commodity'
    if qt in ('ETF', 'MUTUALFUND'):
        return 'commodity' if _COMMODITY_NAME.search(nm) else 'etf'
    if qt != 'EQUITY' and _COMMODITY_NAME.search(nm):
        return 'commodity'
    if qt == 'EQUITY' and re.search(r'xetra-gold|euwax gold|physical (gold|silver)|\betc\b', nm, re.I):
        return 'commodity'
    return 'stock'


def switch_cost(current: pd.Series, target: pd.Series, gain_share: float, tax_rate: float,
                allowance: float = 0.0, exemption: Optional[pd.Series] = None,
                brackets: Optional[list] = None, persons: int = 1) -> dict:
    """Tax for moving today's holdings (*current*, value per ticker) to *target* values.

    Only sales of held positions are taxed: the sold amount times the unrealised gain
    share, less the partial exemption of the asset and the saver's allowance.
    With *brackets* the tax is progressive, the gain split evenly over *persons*
    (one tax year, nothing else taxed that year); otherwise *tax_rate* is flat.
    Returns tax, sold amount and the unrealised gain that stays in the kept positions.
    """
    cur = pd.to_numeric(current, errors='coerce').fillna(0.0).clip(lower=0.0)
    tgt = pd.to_numeric(target.reindex(cur.index), errors='coerce').fillna(0.0).clip(lower=0.0)
    sold = (cur - tgt).clip(lower=0.0)
    tf = (exemption.reindex(cur.index).fillna(0.0) if exemption is not None
          else pd.Series(0.0, index=cur.index))
    g = float(np.clip(gain_share, 0.0, 0.99))
    taxable = float((sold * g * (1.0 - tf)).sum())
    base = max(taxable - max(allowance, 0.0), 0.0)
    if brackets:
        k = max(int(persons), 1)
        tax = k * progressive_tax(base / k, brackets)
    else:
        tax = base * max(tax_rate, 0.0)
    kept_gain = float((np.minimum(cur, tgt) * g).sum())
    return {'tax': tax, 'sold': float(sold.sum()), 'kept_gain': kept_gain}


def default_inner_weights(classes: pd.Series, values: pd.Series) -> pd.Series:
    """Weight of each asset inside its class, in percent (sums to 100 per class).

    Assets with a position value keep their value ratio; assets without one
    (newly chosen ETFs/commodities) get the average weight of their class.
    """
    raw = pd.to_numeric(values.reindex(classes.index), errors='coerce')
    out = pd.Series(0.0, index=classes.index)
    for cls in classes.unique():
        idx = classes.index[classes == cls]
        r = raw.reindex(idx)
        fill = r[r > 0].mean() if (r > 0).any() else 1.0
        r = r.where(r > 0, fill)
        out[idx] = r / r.sum() * 100.0
    return out


def class_target_weights(classes: pd.Series, inner: pd.Series, targets: dict) -> tuple[pd.Series, dict]:
    """Portfolio weights from class targets (e.g. stocks 60 / ETFs 30 / commodities 10).

    *inner* = weight of each asset inside its class (any scale). A class with a
    target but no asset is dropped and the other targets are scaled up.
    Returns (weights summing to 1, effective class shares summing to 1).
    """
    inner = pd.to_numeric(inner.reindex(classes.index), errors='coerce').fillna(0.0).clip(lower=0.0)
    usable = {c: float(max(targets.get(c, 0.0), 0.0)) for c in ASSET_CLASSES
              if inner[classes == c].sum() > 0}
    total = sum(usable.values())
    w = pd.Series(0.0, index=classes.index)
    if total <= 0:
        return w, {c: 0.0 for c in ASSET_CLASSES}
    shares = {c: usable.get(c, 0.0) / total for c in ASSET_CLASSES}
    for c, share in shares.items():
        if share <= 0:
            continue
        idx = classes.index[classes == c]
        w[idx] = inner[idx] / inner[idx].sum() * share
    return w, shares


def monthly_returns(tickers: list[str], db_path: str = 'database',
                    system_currency: str = 'EUR') -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monthly price returns in the system currency, one column per ticker.

    Returns (returns, meta). Tickers without local data are missing from returns.
    """
    from tradinglib.fx_history import per_unit
    db_dir = _db_dir(db_path)
    meta = asset_meta(tickers, db_path)
    cols = {}
    for tk in tickers:
        closes = _read_closes(tk, db_dir)
        if len(closes) < 13:
            logger.info('withdrawal sim: too little history for %s', tk)
            continue
        cur = str(meta.loc[tk, 'currency'] or 'EUR')
        if cur.upper() != str(system_currency).upper() or cur in ('GBp', 'GBX', 'ZAc', 'ILA'):
            try:
                fx = per_unit(cur, system_currency, db_dir)
            except Exception:
                fx = None
            if fx is not None and not fx.empty:
                fx = fx.copy()
                fx.index = pd.to_datetime(fx.index)
                fxm = fx.groupby(fx.index.to_period('M')).last()
                fxm.index = fxm.index.to_timestamp(how='end').normalize()
                fxm = fxm.reindex(closes.index).ffill().bfill()
                closes = closes * fxm
        cols[tk] = _remove_split_jumps(closes)
    if not cols:
        return pd.DataFrame(), meta
    ret = pd.DataFrame(cols).sort_index()
    return ret.iloc[1:] if len(ret) > 1 else ret, meta


def build_return_matrix(returns: pd.DataFrame, proxy: Optional[pd.Series], years: int,
                        div_yield: Optional[pd.Series] = None, cap: Optional[float] = None,
                        haircut: float = 0.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Window of the last *years*, gaps filled with the proxy, dividends added, return cap.

    Returns (matrix, stats) — stats per asset: coverage, CAGR before and after the
    cap, volatility, worst 12-month return.
    """
    if returns.empty:
        return returns, pd.DataFrame()
    end = returns.index.max()
    start = end - pd.DateOffset(years=years)
    win = returns[returns.index > start].copy()
    coverage = win.notna().mean()
    if proxy is not None and not proxy.empty:
        p = proxy.reindex(win.index)
        for c in win.columns:
            win[c] = win[c].fillna(p)
    win = win.dropna(how='any')
    stats = pd.DataFrame(index=win.columns)
    stats['coverage'] = coverage.reindex(win.columns)
    stats['cagr_hist'] = np.expm1(np.log1p(win).mean() * 12)
    if div_yield is not None:
        win = win + div_yield.reindex(win.columns).fillna(0.0) / 12.0
    if haircut:
        win = (1.0 + win) * np.exp(-haircut / 12.0) - 1.0
    if cap is not None:
        g = np.log1p(win).mean() * 12
        excess = np.maximum(g - np.log1p(cap), 0.0)
        win = (1.0 + win) * np.exp(-excess / 12.0) - 1.0
    stats['cagr_used'] = np.expm1(np.log1p(win).mean() * 12)
    stats['vol'] = win.std() * np.sqrt(12)
    stats['worst_12m'] = (np.exp(np.log1p(win).rolling(12).sum()) - 1).min()
    return win, stats


# ─────────────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimConfig:
    capital: float = 500_000.0
    weights: np.ndarray = field(default_factory=lambda: np.array([1.0]))   # risky part, sums to 1
    partial_exemption: np.ndarray = field(default_factory=lambda: np.array([0.0]))
    cash_quota: float = 0.0             # share of the capital in fixed deposits
    cash_rate: float = 0.025            # deposit interest p.a.
    cash_mode: str = 'rebalance'        # 'rebalance' | 'bucket'
    spending: float = 2_000.0           # net household spending per month, today's money
    spending_profile: str = 'constant'  # 'constant' | 'declining'
    survivor_factor: float = 0.70       # spending share after the first death
    widow_share: float = 0.55           # survivor's share of the partner's pension
    inflation: float = 0.02             # mean inflation p.a.
    inflation_mode: str = 'stochastic'  # 'constant' | 'stochastic' (AR(1) per year)
    inflation_vol: float = 0.012        # yearly shock size of the AR(1) model
    inflation_persistence: float = 0.7  # how long a shock lasts (0 = one year)
    pension_real: float = 0.0           # pension increase above inflation p.a.
    care_real: float = 0.015            # care-cost increase above inflation p.a.
    cash_follows_inflation: bool = False  # deposit rate = inflation + spread
    cash_spread: float = 0.0
    ter: float = 0.002
    tax: str = 'none'                   # preset: 'de' | 'de_kist8' | 'de_kist9' | 'es' | 'off'
                                        # (old: 'none' | 'kist8' | 'kist9')
    tax_brackets: Optional[list] = None  # overrides the preset: [(upper, rate), …]
    tax_allowance: Optional[float] = None  # overrides the preset: per person and year
    # Spanish wealth tax (paid once a year from the portfolio, on wealth split over living persons)
    wealth_tax: bool = False
    wt_exemption: float = WEALTH_TAX_EXEMPTION
    wt_scale: Optional[list] = None       # None = state scale
    wt_bonus: float = 0.0                 # regional rebate on the wealth tax, 0..1
    wt_solidarity: bool = True
    wt_other: float = 0.0                 # further taxable wealth outside the portfolio (today's €)
    wt_cap60: bool = True                 # joint 60 % cap with the income tax
    wt_general_income: float = 0.0        # gross general income per person and year (today's €)
    wt_income_region: str = 'avg'         # general income-tax scale: 'avg' | 'canarias'
    initial_gain_share: float = 0.0     # unrealised gain share of the risky value
    care_enabled: bool = True
    care_cost: float = 2_000.0          # extra co-payment per person in care, per month
    improvement: float = 0.01           # yearly mortality improvement (cohort effect)
    fixed_years: Optional[int] = None   # ignore mortality, run exactly this long
    crash_at_start: float = 0.0         # one-off drop of the risky part in month 1
    # withdrawal rule — how spending reacts to the markets (see WITHDRAWAL_RULES)
    withdrawal_rule: str = 'fixed'
    spend_floor: float = 0.75           # spending never below this share of the plan
    spend_ceiling: float = 1.25         # ... and never above this share
    guard_band: float = 0.20            # guardrails: allowed deviation of the withdrawal rate
    guard_step: float = 0.10            # guardrails: cut / raise per trigger
    vol_sensitivity: float = 1.0        # vol rule: strength of the reaction
    max_change: float = 0.15            # vol rule: max change of spending per year
    percent_rate: float = 0.0           # percent rule: share of wealth per year (0 = start rate)
    n_sims: int = 2000
    block: int = 12
    seed: int = 42
    persons: list = field(default_factory=lambda: [Person()])
    asset_classes: Optional[list] = None   # class per asset ('stock'/'etf'/'commodity'), for the charts
    one_off_expenses: Optional[list] = None  # [(month index from today, amount in today's money), ...]


def _block_indices(rng, n: int, months: int, T: int, block: int) -> np.ndarray:
    block = max(1, min(block, T))
    n_blocks = int(np.ceil(months / block))
    starts = rng.integers(0, T - block + 1, size=(n, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)
    return idx[:, :months]


def inflation_paths(cfg: 'SimConfig', rng, n: int, years: int) -> np.ndarray:
    """(n x years) yearly inflation rates — constant or AR(1) around the mean."""
    if cfg.inflation_mode != 'stochastic' or cfg.inflation_vol <= 0:
        return np.full((n, years), cfg.inflation)
    phi = float(np.clip(cfg.inflation_persistence, 0.0, 0.98))
    out = np.empty((n, years))
    prev = np.full(n, cfg.inflation)
    for y in range(years):
        prev = cfg.inflation + phi * (prev - cfg.inflation) + rng.normal(0.0, cfg.inflation_vol, n)
        prev = np.maximum(prev, -0.02)
        out[:, y] = prev
    return out


WITHDRAWAL_RULES = ('fixed', 'guardrails', 'percent', 'vol', 'skip_infl')


def _rule_update(cfg: 'SimConfig', adj, W, plan_nom, income_nom, init_rate, sigma12, sigma_long,
                 drawdown, last_return, last_infl, ahead=None):
    """New spending factor per path (1.0 = spend as planned), applied once a year.

    plan_nom / income_nom: planned spending and pension income of the last month (nominal).
    W: current wealth. init_rate: portfolio withdrawal rate at the start (per year / capital).

    * guardrails (Guyton-Klinger): withdrawal rate more than ``guard_band`` above the start
      rate -> spend ``guard_step`` less; more than ``guard_band`` below -> spend more.
    * percent: the portfolio pays ``percent_rate`` (default: the start rate) of today's wealth
      every year. Applied from the first year on.
    * vol: spend more when the last 12 months were calmer than the long-run volatility of
      the portfolio, less when rougher or when the portfolio is more than 10 % below its peak.
      Raises only while the real wealth is still above the starting capital (*ahead*).
    * skip_infl: no inflation raise after a year with a portfolio loss (no catch-up).
    Every rule is clamped to [spend_floor, spend_ceiling] of the plan.
    """
    rule = cfg.withdrawal_rule
    lo, hi = cfg.spend_floor, cfg.spend_ceiling
    if rule == 'guardrails' and init_rate > 0:
        rate = 12.0 * np.maximum(plan_nom * adj - income_nom, 0.0) / np.maximum(W, 1e-9)
        adj = np.where(rate > init_rate * (1.0 + cfg.guard_band), adj * (1.0 - cfg.guard_step),
                       np.where(rate < init_rate * (1.0 - cfg.guard_band), adj * (1.0 + cfg.guard_step), adj))
    elif rule == 'percent' and (cfg.percent_rate > 0 or init_rate > 0):
        wd = (cfg.percent_rate or init_rate) * np.maximum(W, 0.0) / 12.0
        adj = np.divide(wd + income_nom, plan_nom, out=np.ones_like(W), where=plan_nom > 0)
    elif rule == 'vol':
        calm = np.power(np.divide(sigma_long, np.maximum(sigma12, 1e-6)), 0.5 * cfg.vol_sensitivity)
        calm = np.where(np.isfinite(sigma12), calm, 1.0)
        target = calm * (1.0 - cfg.vol_sensitivity * np.maximum(drawdown - 0.10, 0.0))
        if ahead is not None:
            target = np.where(ahead, target, np.minimum(target, 1.0))
        adj = np.clip(target, adj * (1.0 - cfg.max_change), adj * (1.0 + cfg.max_change))
    elif rule == 'skip_infl':
        adj = np.where(last_return < 0.0, adj / (1.0 + np.maximum(last_infl, 0.0)), adj)
    return np.clip(adj, lo, hi)


def expand_expenses(rows, today=None, horizon_years: int = 75) -> list:
    """Calendar rows -> [(month index, amount in today's money)].

    Each row: {'year': 2027, 'amount': 30000, 'every': 0 (once) or N years, 'until': last year or None}.
    An expense of calendar year Y falls in January of Y (or now, if Y is the current year).
    """
    import datetime as _dt
    today = today or _dt.date.today()
    out = []
    for r in rows or []:
        try:
            year = int(r.get('year'))
            amount = float(r.get('amount') or 0.0)
        except (TypeError, ValueError):
            continue
        if amount <= 0 or year < today.year:
            continue
        every = int(r.get('every') or 0)
        until = r.get('until')
        try:
            until = int(until) if until not in (None, '') else None
        except (TypeError, ValueError):
            until = None
        last = until if (every > 0 and until) else (year + horizon_years if every > 0 else year)
        y = year
        while y <= last and y < today.year + horizon_years:
            m = max(0, (y - today.year) * 12 - today.month + 1)
            out.append((m, amount))
            if every <= 0:
                break
            y += every
    return out


def _spend_profile(age):
    """Go-go / slow-go / no-go: 100 % below 75, 90 % to 84, 80 % from 85."""
    return np.where(age < 75, 1.0, np.where(age < 85, 0.9, 0.8))


def upgrade_config(cfg) -> 'SimConfig':
    """Copy *cfg* into the current SimConfig; fields an older version lacked get their default."""
    import dataclasses
    if type(cfg) is SimConfig and all(hasattr(cfg, f.name) for f in dataclasses.fields(SimConfig)):
        return cfg
    kw = {}
    for f in dataclasses.fields(SimConfig):
        if hasattr(cfg, f.name):
            kw[f.name] = getattr(cfg, f.name)
    return SimConfig(**kw)


def simulate(cfg: SimConfig, R: np.ndarray) -> dict:
    """Run the Monte Carlo. *R* is the (T x k) monthly return matrix of the risky assets."""
    cfg = upgrade_config(cfg)
    rng = np.random.default_rng(cfg.seed)
    n, persons = int(cfg.n_sims), list(cfg.persons) or [Person()]
    P = len(persons)
    R = np.asarray(R, dtype=float)
    if R.ndim == 1:
        R = R[:, None]
    T, k = R.shape
    w = np.asarray(cfg.weights, dtype=float)
    w = w / w.sum() if w.sum() > 0 else np.full(k, 1.0 / k)

    ages0 = np.array([p.age for p in persons], dtype=float)
    if cfg.fixed_years:
        months = int(cfg.fixed_years) * 12
    else:
        months = int(min(MAX_AGE - ages0.min(), 75)) * 12
    months = max(months, 12)
    idx = _block_indices(rng, n, months, T, cfg.block)
    infl_y = inflation_paths(cfg, rng, n, months // 12 + 1)
    price = np.ones((n, months + 1))      # price level, 1.0 = today

    regime = tax_regime(cfg.tax)
    brackets = cfg.tax_brackets if cfg.tax_brackets else regime['brackets']
    allowance_pp = regime['allowance'] if cfg.tax_allowance is None else float(cfg.tax_allowance)
    ytd = np.zeros(n)                       # taxable capital income of this year (household)

    def rate_now():
        per_person = ytd / np.maximum(alive.sum(1), 1)
        return marginal_rate(per_person, brackets)
    tf = float(np.dot(w, np.asarray(cfg.partial_exemption, dtype=float))) if len(
        np.atleast_1d(cfg.partial_exemption)) == k else 0.0

    cash_target = float(np.clip(cfg.cash_quota, 0.0, 1.0))
    risky0 = cfg.capital * (1.0 - cash_target)
    H = np.tile(w * risky0, (n, 1))
    cash = np.full(n, cfg.capital * cash_target)
    basis = np.full(n, risky0 * (1.0 - np.clip(cfg.initial_gain_share, 0.0, 0.99)))
    if cfg.crash_at_start:
        H *= (1.0 - cfg.crash_at_start)

    factors = np.array([p.risk_factor() for p in persons])
    sexes = [p.sex for p in persons]
    pensions = np.array([p.pension for p in persons], dtype=float)
    pension_ages = np.array([p.pension_age for p in persons], dtype=float)
    alive = np.ones((n, P), dtype=bool)
    in_care = np.zeros((n, P), dtype=bool)
    ever_care = np.zeros((n, P), dtype=bool)
    care_months = np.zeros((n, P))
    death_age = np.full((n, P), np.nan)

    ruined = np.zeros(n, dtype=bool)
    ruin_month = np.full(n, -1)
    shortfall = np.zeros(n)
    end_month = np.full(n, months)         # month the last person died (or horizon)
    wealth = np.zeros((n, months + 1))
    wealth[:, 0] = H.sum(1) + cash
    household_alive = np.zeros((n, months + 1), dtype=bool)
    household_alive[:, 0] = True
    allowance = np.zeros(n)
    risky_year_start = H.sum(1)
    risky_up = np.ones(n, dtype=bool)
    # withdrawal rule state
    dynamic = cfg.withdrawal_rule != 'fixed'
    adj = np.ones(n)
    port_r = R @ w
    sigma_long = float(np.std(port_r) * np.sqrt(12.0))
    ret_buf = np.full((n, 12), np.nan)
    level = np.ones(n)
    level_peak = np.ones(n)
    level_year = np.ones(n)
    plan_last = np.zeros(n)
    income_last = np.zeros(n)
    init_rate = 0.0
    one_off = np.zeros(months)
    for m, a in (cfg.one_off_expenses or []):
        if 0 <= int(m) < months:
            one_off[int(m)] += float(a)
    wt_due = np.zeros(n)
    wt_paid_real = np.zeros(n)
    cls_list = list(cfg.asset_classes) if cfg.asset_classes is not None and len(cfg.asset_classes) == k \
        else ['stock'] * k
    class_mask = np.array([[c == cl for c in cls_list] for cl in ASSET_CLASSES])
    comp = np.zeros((n, months // 12 + 1, 4))         # stock, etf, commodity, deposits (nominal)
    for ci in range(3):
        if class_mask[ci].any():
            comp[:, 0, ci] = H[:, class_mask[ci]].sum(1)
    comp[:, 0, 3] = cash
    interest_year = np.zeros(n)
    flow_real = np.zeros((n, months // 12 + 1))     # net outflow per year (real): + out, - in
    consumption = np.full((n, months), np.nan)       # real spending actually financed
    planned = np.full((n, months), np.nan)           # real spending of the fixed plan

    for t in range(months):
        year = t // 12
        ages = ages0 + year
        if t % 12 == 0:
            if not cfg.fixed_years:
                was_alive = alive.any(1)
                for j in range(P):
                    q = person_qx(ages[j], sexes[j], factors[j], year, cfg.improvement)
                    q_care = np.maximum(q * CARE_MORTALITY_MULT, CARE_MORTALITY_MIN)
                    qj = np.where(in_care[:, j], min(q_care, 1.0), q)
                    dies = alive[:, j] & (rng.random(n) < qj)
                    death_age[dies, j] = ages[j] + rng.random(dies.sum())
                    alive[dies, j] = False
                    in_care[dies, j] = False
                    if cfg.care_enabled:
                        onset = alive[:, j] & ~in_care[:, j] & (rng.random(n) < care_onset(ages[j], factors[j]))
                        in_care[onset, j] = True
                        ever_care[onset, j] = True
                ended = was_alive & ~alive.any(1)
                end_month[ended] = t
            allowance = allowance_pp * alive.sum(1)
            ytd = np.zeros(n)
            if cfg.wealth_tax and t > 0:
                n_live = alive.sum(1) if not cfg.fixed_years else np.full(n, P)
                per = (H.sum(1) + cash + cfg.wt_other * price[:, t]) / np.maximum(n_live, 1)
                wt = wealth_tax_es(per, cfg.wt_exemption, cfg.wt_scale, cfg.wt_bonus, cfg.wt_solidarity)
                if cfg.wt_cap60:
                    gi = cfg.wt_general_income * price[:, t] * (1.0 + cfg.pension_real) ** (t / 12.0)
                    wt = wealth_tax_cap_es(wt, gi, interest_year / np.maximum(n_live, 1), brackets,
                                           cfg.wt_income_region)
                wt_due = np.where(n_live > 0, wt * n_live, 0.0)
                wt_paid_real += wt_due / price[:, t]
            # annual rebalancing of the risky part
            tot = H.sum(1)
            pos = tot > 0
            H[pos] = tot[pos, None] * w[None, :]
            if t > 0:
                risky_up = tot >= risky_year_start
            if cash_target > 0:
                total = tot + cash
                target_cash = total * cash_target
                if cfg.cash_mode == 'bucket':
                    # refill the buffer only after an up year; deposits above the quota
                    # (e.g. pension surpluses) are invested every year
                    move = np.where(cash < target_cash,
                                    np.where(risky_up, target_cash - cash, 0.0),
                                    target_cash - cash)
                else:
                    move = target_cash - cash
                move = np.clip(move, -cash, tot)
                frac = np.divide(move, tot, out=np.zeros(n), where=tot > 0)
                frac = np.clip(frac, -np.inf, 1.0)
                # selling risky to fill the deposit realises gains: tax them
                sell = np.maximum(move, 0.0)
                gain_frac = np.clip(1.0 - np.divide(basis, tot, out=np.ones(n), where=tot > 0), 0.0, 1.0)
                taxable = sell * gain_frac * (1.0 - tf)
                used = np.minimum(taxable, allowance)
                allowance -= used
                tax = rate_now() * (taxable - used)
                ytd += taxable - used
                H *= (1.0 - np.clip(frac, 0.0, 1.0))[:, None]
                basis *= (1.0 - np.clip(frac, 0.0, 1.0))
                # buying risky from the deposit raises the basis
                buy = np.maximum(-move, 0.0)
                H += buy[:, None] * w[None, :]
                basis += buy
                cash += move - tax
            risky_year_start = H.sum(1)
            if dynamic and t > 0:
                with np.errstate(invalid='ignore'):
                    sigma12 = np.nanstd(ret_buf, axis=1) * np.sqrt(12.0)
                adj = _rule_update(cfg, adj, H.sum(1) + cash, plan_last, income_last, init_rate,
                                   sigma12, sigma_long, 1.0 - level / level_peak,
                                   level / level_year - 1.0, infl_y[:, year - 1],
                                   ahead=(H.sum(1) + cash) / price[:, t] >= cfg.capital)
                level_year = level.copy()

        live = alive.any(1) if not cfg.fixed_years else np.ones(n, dtype=bool)
        if cfg.fixed_years:
            live = np.full(n, t < months)

        # markets
        H *= 1.0 + R[idx[:, t]]
        if dynamic:
            r_p = port_r[idx[:, t]]
            ret_buf[:, t % 12] = r_p
            level *= 1.0 + r_p
            level_peak = np.maximum(level_peak, level)
        H *= 1.0 - cfg.ter / 12.0
        rate = np.maximum(infl_y[:, year] + cfg.cash_spread, 0.0) if cfg.cash_follows_inflation             else cfg.cash_rate
        interest = cash * rate / 12.0
        interest_year = interest_year * (0.0 if t % 12 == 0 else 1.0) + interest
        used = np.minimum(interest, allowance)
        allowance -= used
        cash += interest - rate_now() * (interest - used)
        ytd += interest - used

        # cash flow need of the month (nominal)
        infl = price[:, t]
        pinfl = infl * (1.0 + cfg.pension_real) ** (t / 12.0)
        cinfl = infl * (1.0 + cfg.care_real) ** (t / 12.0)
        price[:, t + 1] = infl * (1.0 + infl_y[:, year]) ** (1.0 / 12.0)
        n_alive = alive.sum(1)
        if cfg.fixed_years:
            hh = np.ones(n)
        else:
            hh = np.where(n_alive >= P, 1.0, np.where(n_alive > 0, cfg.survivor_factor, 0.0))
            if P == 1:
                hh = n_alive.astype(float)
        youngest = np.where(alive, ages[None, :], np.inf).min(1) if not cfg.fixed_years \
            else np.full(n, ages.min())
        prof = _spend_profile(youngest) if cfg.spending_profile == 'declining' else 1.0
        plan_nom = cfg.spending * infl * hh * prof
        spend_nom = plan_nom * adj
        need = spend_nom
        if wt_due.any():
            need = need + wt_due
            wt_due = np.zeros(n)
        if one_off[t]:
            need = need + one_off[t] * infl
        if cfg.care_enabled and not cfg.fixed_years:
            need = need + cfg.care_cost * cinfl * in_care.sum(1)
            care_months += in_care
        income = np.zeros(n)
        for j in range(P):
            on = ages[j] >= pension_ages[j]
            if not on:
                continue
            own = pensions[j] * pinfl
            if cfg.fixed_years:
                income += own
                continue
            income += np.where(alive[:, j], own, 0.0)
            if P > 1:
                other_alive = np.delete(alive, j, axis=1).any(1)
                income += np.where(~alive[:, j] & other_alive, own * cfg.widow_share, 0.0)
        if t == 0:
            init_rate = float(np.mean(12.0 * np.maximum(plan_nom - income, 0.0))) / max(cfg.capital, 1e-9)
            if cfg.withdrawal_rule == 'percent' and cfg.percent_rate > 0:
                # an explicit rate applies from the first year on
                adj = _rule_update(cfg, adj, H.sum(1) + cash, plan_nom, income, init_rate,
                                   None, sigma_long, None, None, None)
                need = need - spend_nom
                spend_nom = plan_nom * adj
                need = need + spend_nom
        need = np.where(live, need - income, 0.0)
        plan_last, income_last = plan_nom, income

        # surplus: into the deposit (or risky part when there is no deposit)
        surplus = np.maximum(-need, 0.0)
        if cash_target > 0:
            cash += surplus
        else:
            H += surplus[:, None] * w[None, :]
            basis += surplus
        need = np.maximum(need, 0.0)

        # 1) from the deposit
        if cfg.cash_mode == 'bucket' or cash_target <= 0:
            from_cash = np.minimum(need, cash)
        else:
            from_cash = np.minimum(need * cash_target, cash)
        cash -= from_cash
        rest = need - from_cash

        # 2) from the risky part, grossed up for the flat-rate tax
        V = H.sum(1)
        gain_frac = np.clip(1.0 - np.divide(basis, V, out=np.ones(n), where=V > 0), 0.0, 1.0)
        tfrac = gain_frac * (1.0 - tf)
        tax_rate = rate_now()
        allow_before = allowance
        within = rest * tfrac <= allowance
        denom = np.maximum(1.0 - tax_rate * tfrac, 1e-9)
        gross = np.where(within, rest, (rest - tax_rate * allowance) / denom)
        allowance = np.where(within, allowance - rest * tfrac, 0.0)
        sell_all = gross > V
        frac = np.divide(np.minimum(gross, V), V, out=np.zeros(n), where=V > 0)
        H *= (1.0 - frac)[:, None]
        basis *= (1.0 - frac)
        # what the risky part could not pay comes from what is left in the deposit
        net_paid = np.where(sell_all, V * (1.0 - tax_rate * tfrac), rest)
        ytd += np.maximum(np.minimum(gross, V) * tfrac - allow_before, 0.0)
        gap = np.maximum(rest - net_paid, 0.0)
        extra = np.minimum(gap, cash)
        cash -= extra
        gap -= extra
        flow_real[:, year] += (from_cash + np.minimum(gross, V) + extra - surplus) / infl
        broke = (gap > 1e-6) & live
        new_ruin = broke & ~ruined
        ruin_month[new_ruin] = t
        ruined |= broke
        shortfall += gap
        consumption[:, t] = np.where(live, np.maximum(spend_nom - gap, 0.0) / infl, np.nan)
        planned[:, t] = np.where(live, plan_nom / infl, np.nan)

        wealth[:, t + 1] = H.sum(1) + cash
        if (t + 1) % 12 == 0:
            y = (t + 1) // 12
            for ci, cl in enumerate(ASSET_CLASSES):
                if class_mask[ci].any():
                    comp[:, y, ci] = H[:, class_mask[ci]].sum(1)
            comp[:, y, 3] = cash
        household_alive[:, t + 1] = live & alive.any(1) if not cfg.fixed_years else live

    return {
        'months': months,
        'ages0': ages0,
        'wealth': wealth,
        'household_alive': household_alive,
        'ruined': ruined,
        'ruin_month': ruin_month,
        'shortfall': shortfall,
        'end_month': end_month,
        'death_age': death_age,
        'ever_care': ever_care,
        'care_months': care_months,
        'inflation': cfg.inflation,
        'price': price,
        'infl_mean': infl_y[:, :max(1, months // 12)].mean(1),
        'fixed': bool(cfg.fixed_years),
        'consumption': consumption,
        'planned': planned,
        'wealth_tax_real': wt_paid_real,
        'rule': cfg.withdrawal_rule,
        'idx': idx.astype(np.int32),
        'composition': comp,
        'asset_classes': cls_list,
        'net_flow_real': flow_real,
    }


def summarize(res: dict) -> dict:
    """Key figures of one simulation run."""
    n = len(res['ruined'])
    end = np.clip(res['end_month'], 0, res['months'])
    w_end = res['wealth'][np.arange(n), end]
    real_end = w_end / res['price'][np.arange(n), end]
    ruin_age = np.where(res['ruin_month'] >= 0,
                        res['ages0'].min() + res['ruin_month'] / 12.0, np.nan)
    out = {
        'p_ruin': float(res['ruined'].mean()),
        'n_ruined': int(res['ruined'].sum()),
        'n_paths': int(len(res['ruined'])),
        'p_success': float(1.0 - res['ruined'].mean()),
        'ruin_age_median': float(np.nanmedian(ruin_age)) if np.isfinite(ruin_age).any() else np.nan,
        'bequest_real_p10': float(np.percentile(real_end, 10)),
        'bequest_real_p50': float(np.percentile(real_end, 50)),
        'bequest_real_p90': float(np.percentile(real_end, 90)),
        'bequest_nominal_p50': float(np.percentile(w_end, 50)),
        'shortfall_mean': float(res['shortfall'].mean()),
        'inflation_mean_p50': float(np.median(res['infl_mean'])),
        'inflation_mean_p90': float(np.percentile(res['infl_mean'], 90)),
        'price_end_p50': float(np.median(res['price'][np.arange(n), end])),
    }
    out.update(spending_stats(res))
    wt = res.get('wealth_tax_real')
    if wt is not None and np.any(wt > 0):
        years = np.maximum(end / 12.0, 1.0)
        out['wealth_tax_total_p50'] = float(np.median(wt))
        out['wealth_tax_year_p50'] = float(np.median(wt / years))
        out['wealth_tax_total_p90'] = float(np.percentile(wt, 90))
    if not res['fixed']:
        da = res['death_age']
        last = np.nanmax(da, axis=1) if da.shape[1] > 1 else da[:, 0]
        out['household_years_median'] = float(np.nanmedian(end / 12.0))
        out['last_death_age_median'] = float(np.nanmedian(last))
        out['p_care_any'] = float(res['ever_care'].any(1).mean())
        out['care_years_mean'] = float(res['care_months'].sum(1).mean() / 12.0)
        out['death_age_median'] = [float(np.nanmedian(da[:, j])) for j in range(da.shape[1])]
    return out


def _yearly(a: np.ndarray) -> np.ndarray:
    """Monthly (n x months) -> yearly means (n x years), NaN-aware."""
    import warnings
    n, m = a.shape
    y = max(m // 12, 1)
    a = a[:, :y * 12] if m >= 12 else np.pad(a, ((0, 0), (0, 12 - m)), constant_values=np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        return np.nanmean(a.reshape(n, y, 12), axis=2)


def spending_stats(res: dict) -> dict:
    """How much the household could actually spend (real, per month)."""
    import warnings
    cons, plan = res.get('consumption'), res.get('planned')
    if cons is None:
        return {}
    cy, py = _yearly(cons), _yearly(plan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        ratio = np.divide(cy, py, out=np.full_like(cy, np.nan), where=py > 0)
        avg = np.nanmean(cons, axis=1)
        worst = np.nanmin(ratio, axis=1)
        cut = np.nanmean(np.where(np.isfinite(ratio), (ratio < 0.9).astype(float), np.nan), axis=1)
        return {
            'spend_avg_p50': float(np.nanmedian(avg)),
            'spend_avg_p10': float(np.nanpercentile(avg, 10)),
            'spend_worst_ratio_p50': float(np.nanmedian(worst)),
            'spend_worst_ratio_p10': float(np.nanpercentile(worst, 10)),
            'spend_cut_years': float(np.nanmean(cut)),
            'spend_vs_plan_p50': float(np.nanmedian(np.nanmean(ratio, axis=1))),
        }


def spending_percentiles(res: dict, q=(10, 50, 90), real: bool = True) -> pd.DataFrame:
    """Yearly percentiles of the monthly spending of households still alive.

    real=True: today's purchasing power; real=False: nominal euros (grow with inflation).
    """
    import warnings
    cons, plan_m = res['consumption'], res['planned']
    if not real:
        level = res['price'][:, :cons.shape[1]]
        cons, plan_m = cons * level, plan_m * level
    cy = _yearly(cons)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        pct = np.nanpercentile(cy, q, axis=0)
        plan = np.nanmedian(_yearly(plan_m), axis=0)
    df = pd.DataFrame(pct.T, columns=[f'p{x}' for x in q])
    df['plan'] = plan
    df['year'] = np.arange(len(df))
    return df


CHART_CLASSES = ASSET_CLASSES + ('cash',)


def class_growth(res: dict, R: np.ndarray, classes, inner, cfg: 'SimConfig', real: bool = True,
                 q=(10, 50, 90)) -> dict:
    """What 1 € grows to in each asset class (and in deposits) on the simulated paths.

    Before withdrawals and taxes, after running costs. Inside a class the assets are held
    at their class weights (*inner*), rebalanced monthly. Returns {class: DataFrame(year, p10, p50, p90)}.
    """
    import warnings
    R = np.asarray(R, dtype=float)
    idx = res['idx']
    n, months = idx.shape
    price = res['price']
    classes = list(classes)
    inner = np.asarray(inner, dtype=float)
    years = np.arange(0, months // 12 + 1)
    out = {}

    def _pct(growth):
        g = np.c_[np.ones(n), growth[:, 11::12]][:, :len(years)]
        if real:
            g = g / price[:, years * 12]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            pc = np.nanpercentile(g, q, axis=0)
        df = pd.DataFrame(pc.T, columns=[f'p{x}' for x in q])
        df['year'] = years[:len(df)]
        return df

    fee = 1.0 - cfg.ter / 12.0
    for cl in ASSET_CLASSES:
        mask = np.array([c == cl for c in classes])
        if not mask.any():
            continue
        wc = np.where(mask, np.maximum(inner, 0.0), 0.0)
        if wc.sum() <= 0:
            wc = mask.astype(float)
        r_c = R @ (wc / wc.sum())
        out[cl] = _pct(np.cumprod((1.0 + r_c[idx]) * fee, axis=1))
    # deposits: fixed rate, or inflation of the path + spread
    if cfg.cash_follows_inflation:
        infl_m = price[:, 1:months + 1] / price[:, :months]
        rate = np.maximum(infl_m ** 12 - 1.0 + cfg.cash_spread, 0.0) / 12.0
    else:
        rate = np.full((n, months), cfg.cash_rate / 12.0)
    out['cash'] = _pct(np.cumprod(1.0 + rate, axis=1))
    return out


def without_withdrawals(cfg: 'SimConfig') -> 'SimConfig':
    """Same scenario and random paths, but nothing is spent: no spending, pensions or care costs.

    Running costs, taxes on interest and dividends and a wealth tax still apply.
    """
    import copy
    import dataclasses
    c = copy.copy(upgrade_config(cfg))
    c.spending = 0.0
    c.care_enabled = False
    c.withdrawal_rule = 'fixed'
    c.persons = [dataclasses.replace(p, pension=0.0) for p in (cfg.persons or [Person()])]
    return c


def net_flow_summary(res: dict, years: int = 10) -> dict:
    """Median net flow per year over the first *years* (real, today's €).

    Positive = the household takes money out of the portfolio, negative = it pays in
    (e.g. pensions above spending). Only years in which the household is alive count.
    """
    import warnings
    flow = res.get('net_flow_real')
    if flow is None:
        return {}
    ny = min(years, flow.shape[1] - 1)
    alive = res['household_alive'][:, np.minimum(np.arange(ny) * 12 + 11, res['months'])]
    f = np.where(alive, flow[:, :ny], np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        per_path = np.nanmean(f, axis=1)
        return {'flow_p50': float(np.nanmedian(per_path)), 'years': int(ny)}


def composition_total(res: dict, real: bool = True) -> pd.Series:
    """Median of the total (all classes + deposits) per year, households still alive."""
    import warnings
    comp = res['composition'].astype(float)
    ny = comp.shape[1]
    years = np.arange(ny)
    cols = np.minimum(years * 12, res['months'])
    tot = comp.sum(axis=2)
    if real:
        tot = tot / res['price'][:, cols]
    tot = np.where(res['household_alive'][:, cols], tot, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        return pd.Series(np.nanmedian(tot, axis=0), index=years)


def composition_percentiles(res: dict, real: bool = True) -> pd.DataFrame:
    """Median value per class and deposits per year, households still alive."""
    import warnings
    comp = res['composition'].astype(float).copy()
    ny = comp.shape[1]
    years = np.arange(ny)
    alive = res['household_alive'][:, np.minimum(years * 12, res['months'])]
    if real:
        comp = comp / res['price'][:, np.minimum(years * 12, res['months'])][:, :, None]
    comp = np.where(alive[:, :, None], comp, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        med = np.nanmedian(comp, axis=0)
    df = pd.DataFrame(med, columns=list(CHART_CLASSES))
    df['year'] = years
    df['alive'] = alive.mean(0)
    return df


def ruin_share_by_year(res: dict) -> pd.Series:
    """Cumulative share of ALL households whose money ran out while alive, by year from today.

    Reaches ``summarize()['p_ruin']`` at the end; unlike "alive with wealth" it is not
    lowered by deaths.
    """
    rm = np.asarray(res['ruin_month'])
    years = np.arange(0, res['months'] // 12 + 1)
    share = [float(((rm >= 0) & (rm < y * 12)).mean()) for y in years]
    return pd.Series(share, index=years)


def wealth_percentiles(res: dict, real: bool = True, q=(10, 25, 50, 75, 90)) -> pd.DataFrame:
    """Yearly percentiles of the wealth of households still alive."""
    m = res['months']
    t = np.arange(0, m + 1, 12)
    w = res['wealth'][:, t].astype(float)
    if real:
        w = w / res['price'][:, t]
    w = np.where(res['household_alive'][:, t], w, np.nan)
    with np.errstate(all='ignore'):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            pct = np.nanpercentile(w, q, axis=0)
    df = pd.DataFrame(pct.T, columns=[f'p{x}' for x in q])
    df['year'] = t // 12
    df['alive'] = res['household_alive'][:, t].mean(0)
    df['solvent'] = (res['household_alive'][:, t] & (res['wealth'][:, t] > 0)).mean(0)
    return df


def sustainable_spending(cfg: SimConfig, R: np.ndarray, target_ruin: float = 0.10,
                         n_sims: int = 600, iters: int = 14, tolerance: float = 10.0) -> float:
    """Highest monthly net spending whose ruin probability stays <= *target_ruin*."""
    import copy
    c = copy.copy(cfg)
    c.n_sims = n_sims
    lo, hi = 0.0, max(cfg.spending, 500.0)
    # widen until it fails
    for _ in range(12):
        c.spending = hi
        if simulate(c, R)['ruined'].mean() > target_ruin:
            break
        lo, hi = hi, hi * 2.0
    for _ in range(iters):
        if hi - lo <= tolerance:            # precise enough (default 10 € a month)
            break
        mid = (lo + hi) / 2.0
        c.spending = mid
        if simulate(c, R)['ruined'].mean() <= target_ruin:
            lo = mid
        else:
            hi = mid
    return lo
