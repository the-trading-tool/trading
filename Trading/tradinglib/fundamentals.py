"""Fundamental analysis engine for the Asset Viewer.

Derives ratios, composite scores and peer percentiles from the annual income
statement / balance sheet that ``get_asset_info.py`` stores as JSON in
``asset_info.db`` (four usable fiscal years per ticker), combined with the flat
TTM columns of the same table and the daily price history in ``yf_*.db``.

No Streamlit import — the rendering lives in :mod:`tradinglib.fundamentals_page`,
which also owns the caching.

Scope
-----
Four fiscal years carry ratios, trends, composite scores and a cross-sectional
peer comparison. They do not carry a fair value: every anchor for one (Graham
number, median multiple, discounted cash flow) either needs a decade of history
or free parameters that are not in the data, and produces confident numbers that
move with the assumption rather than the company. Valuation is therefore shown as
what it is — the ratio and its path — and never as a price target.

Currency
--------
Statement figures are denominated in ``financialCurrency``, market figures
(``marketCap``, ``enterpriseValue``, price) in ``currency``. The two differ for
roughly nine percent of the equities (ADRs, GBp/pence listings, HKD-quoted
mainland companies, ...). Every metric that mixes both is computed only when a
conversion factor is supplied; for peer distributions no factor is fetched, so
those tickers simply do not contribute to the affected metrics rather than
silently mixing units.

Cash flow
---------
Only the TTM ``freeCashflow`` / ``operatingCashflow`` snapshots are stored, not a
per-year cash flow statement. Two of the nine Piotroski criteria therefore use
the TTM figure instead of the fiscal-year one (both are level tests, not deltas,
so the substitution is defensible) and no FCF margin history is available.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3

import numpy as np
import pandas as pd

from tradinglib import tools as _tools

# ── Metric registry ──────────────────────────────────────────────────────────
# key -> (group, unit, higher_is_better)
#   unit: 'x' plain ratio · '%' percent · 'd' days · 'score' bounded score
#   higher_is_better drives both the percentile direction and the colour.
METRICS: dict[str, tuple[str, str, bool]] = {
    # Financial strength
    'cash_to_debt':          ('strength', 'x', True),
    'equity_to_asset':       ('strength', 'x', True),
    'debt_to_equity':        ('strength', 'x', False),
    'debt_to_ebitda':        ('strength', 'x', False),
    'interest_coverage':     ('strength', 'x', True),
    'altman_z':              ('strength', 'x', True),
    'piotroski':             ('strength', 'score', True),
    # Growth (four fiscal years → three-year CAGR is the longest window)
    'rev_cagr3':             ('growth', '%', True),
    'ebitda_cagr3':          ('growth', '%', True),
    'eps_cagr3':             ('growth', '%', True),
    'book_cagr3':            ('growth', '%', True),
    'rev_yoy':               ('growth', '%', True),
    'eps_yoy':               ('growth', '%', True),
    # Profitability
    'gross_margin':          ('profit', '%', True),
    'operating_margin':      ('profit', '%', True),
    'net_margin':            ('profit', '%', True),
    'ebitda_margin':         ('profit', '%', True),
    'fcf_margin':            ('profit', '%', True),
    'roe':                   ('profit', '%', True),
    'roa':                   ('profit', '%', True),
    'roic':                  ('profit', '%', True),
    'roce':                  ('profit', '%', True),
    'roc_greenblatt':        ('profit', '%', True),
    'years_profitable':      ('profit', 'x', True),
    # Valuation — cheaper is better, hence higher_is_better=False.
    # No PEG: Yahoo fills trailingPegRatio for barely a quarter of the equities,
    # too thin for a peer percentile to mean anything.
    'pe':                    ('value', 'x', False),
    'forward_pe':            ('value', 'x', False),
    'ps':                    ('value', 'x', False),
    'pb':                    ('value', 'x', False),
    'p_tangible_book':       ('value', 'x', False),
    'p_fcf':                 ('value', 'x', False),
    'p_ocf':                 ('value', 'x', False),
    'ev_ebit':               ('value', 'x', False),
    'ev_ebitda':             ('value', 'x', False),
    'ev_revenue':            ('value', 'x', False),
    'earnings_yield':        ('value', '%', True),
    'fcf_yield':             ('value', '%', True),
    # Liquidity / working capital
    'current_ratio':         ('liquidity', 'x', True),
    'quick_ratio':           ('liquidity', 'x', True),
    'cash_ratio':            ('liquidity', 'x', True),
    'days_inventory':        ('liquidity', 'd', False),
    'days_sales_outstanding': ('liquidity', 'd', False),
    'days_payable':          ('liquidity', 'd', True),
    # Dividend & buy back
    'dividend_yield':        ('dividend', '%', True),
    'payout_ratio':          ('dividend', '%', False),
    'avg_yield_5y':          ('dividend', '%', True),
    'buyback_yield':         ('dividend', '%', True),
    'shareholder_yield':     ('dividend', '%', True),
}
# Deliberately no momentum panel. RSI and skip-month returns would need each peer's
# price history to be comparable, and the 52-week figures that do sit in the flat
# table are already on the Trend and Kennzahlen tabs — a second, worse copy of the
# technical view does not belong in a fundamental one.

# Order of the panels in the UI.
GROUPS = ['strength', 'growth', 'profit', 'value', 'liquidity', 'dividend']

# Groups that feed a 0–10 headline rank. Liquidity and dividend are shown as plain
# tables — a bank with no dividend is not "bad", it is a different animal.
RANK_GROUPS = ['strength', 'growth', 'profit', 'value']

# Metrics that combine a market figure (quote currency) with a statement figure
# (report currency). Without a conversion factor they stay None instead of
# mixing units.
_FX_SENSITIVE = {'altman_z', 'p_tangible_book', 'p_fcf', 'p_ocf', 'ev_ebit',
                 'earnings_yield', 'fcf_yield'}

# Balance-sheet structure ratios are meaningless for banks and insurers: their
# "debt" is funding, they have no operating working-capital cycle, and enterprise
# value is not a sensible numerator when deposits and reserves count as debt.
# Showing them anyway would be worse than showing nothing.
_FINANCIAL_SECTOR = 'Financial Services'
_SUPPRESSED_FOR_FINANCIALS = {'altman_z', 'debt_to_ebitda', 'current_ratio', 'quick_ratio',
                              'cash_ratio', 'days_inventory', 'days_sales_outstanding',
                              'days_payable', 'roc_greenblatt',
                              'ev_ebit', 'ev_ebitda', 'ev_revenue', 'earnings_yield'}

# Flat asset_info columns the engine reads. Kept explicit so the peer query stays
# narrow — the table has some 250 columns.
_FLAT_FIELDS = [
    'ticker', 'longName', 'shortName', 'sector', 'industry', 'country', 'quoteType',
    'currency', 'financialCurrency', 'timestamp',
    'marketCap', 'enterpriseValue', 'sharesOutstanding', 'floatShares',
    'currentPrice', 'regularMarketPrice', 'previousClose',
    'trailingPE', 'forwardPE', 'priceToBook',
    'priceToSalesTrailing12Months', 'enterpriseToEbitda', 'enterpriseToRevenue',
    'trailingEps', 'forwardEps', 'bookValue',
    'totalRevenue', 'ebitda', 'netIncomeToCommon', 'grossProfits',
    'freeCashflow', 'operatingCashflow', 'totalCash', 'totalDebt',
    'currentRatio', 'quickRatio', 'debtToEquity',
    'returnOnEquity', 'returnOnAssets',
    'grossMargins', 'operatingMargins', 'ebitdaMargins', 'profitMargins',
    'revenueGrowth', 'earningsGrowth',
    'dividendYield', 'dividendRate', 'payoutRatio', 'fiveYearAvgDividendYield',
    'beta', 'heldPercentInsiders', 'heldPercentInstitutions', 'fullTimeEmployees',
    'targetMeanPrice', 'recommendationKey', 'numberOfAnalystOpinions',
]


# ── Small helpers ────────────────────────────────────────────────────────────
def _db(file_name: str) -> str:
    return _tools.Tools().get_path(path='database', file_name=file_name)


def _f(value):
    """Coerce to a finite float, or None."""
    try:
        if value is None:
            return None
        v = float(value)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return None


def _div(a, b):
    """a / b, or None when either side is missing or the denominator is zero."""
    a, b = _f(a), _f(b)
    if a is None or b in (None, 0):
        return None
    v = a / b
    return None if (math.isnan(v) or math.isinf(v)) else v


def _p100(value):
    """Scale a fraction to percent, keeping None as None."""
    v = _f(value)
    return None if v is None else v * 100.0


def _cagr(new, old, years):
    """Compound annual growth in percent. Undefined for a non-positive base."""
    new, old = _f(new), _f(old)
    if new is None or old is None or old <= 0 or years <= 0:
        return None
    if new <= 0:                       # a swing into losses has no meaningful CAGR
        return None
    return ((new / old) ** (1.0 / years) - 1.0) * 100.0


def _parse_sheet(raw):
    """Parse a ``to_json(orient='split')`` sheet into ``(line_items, years)``.

    ``line_items`` maps a line-item name to a list of floats ordered newest year
    first, ``years`` holds the matching fiscal-year end timestamps. Deliberately
    plain dicts and lists rather than a DataFrame: peer distributions build this
    for several hundred tickers per sector, and DataFrame construction alone cost
    more than the rest of the engine put together.
    """
    if not raw or not isinstance(raw, str) or len(raw) < 50:
        return {}, []
    try:
        d = json.loads(raw)
        years = [pd.to_datetime(c, unit='ms') for c in d['columns']]
        rows = {name: list(values) for name, values in zip(d['index'], d['data'])}
    except Exception:
        return {}, []
    if not years:
        return {}, []

    # Yahoo pads the oldest fiscal year with nulls (a few memo rows survive), so
    # "has any value" is not enough — require a third of the line items before a
    # year counts as reported.
    n_rows = max(len(rows), 1)
    keep = [k for k in range(len(years))
            if sum(1 for v in rows.values() if k < len(v) and v[k] is not None) / n_rows >= 0.3]
    # Newest fiscal year first.
    keep.sort(key=lambda k: years[k], reverse=True)
    if not keep:
        return {}, []
    out = {name: [_f(values[k]) if k < len(values) else None for k in keep]
           for name, values in rows.items()}
    return out, [years[k] for k in keep]


# ── Core object ──────────────────────────────────────────────────────────────
class Fundamentals:
    """Metric set for one ticker, built from its asset_info row.

    ``fx`` converts a market figure into the report currency:
    ``value_report = value_quote / fx``. Pass None to leave FX-sensitive metrics
    undefined (used for peers, where fetching a rate per ticker is not worth it).
    """

    def __init__(self, flat: dict, income, balance, fx: float | None = None):
        self.flat = flat or {}
        self.income, income_years = income if income else ({}, [])
        self.balance, balance_years = balance if balance else ({}, [])
        self._years = income_years or balance_years
        self.ticker = str(self.flat.get('ticker') or '')
        self.sector = self.flat.get('sector') or ''
        self.industry = self.flat.get('industry') or ''
        self.currency = self.flat.get('currency') or ''
        self.report_currency = self.flat.get('financialCurrency') or self.currency
        self.fx = _f(fx)
        self._metrics = None
        self._piotroski = None

    # -- raw access ---------------------------------------------------------
    @property
    def years(self) -> list:
        return list(self._years)

    @property
    def n_years(self) -> int:
        return len(self._years)

    def inc(self, item, i=0):
        """Income-statement line item for fiscal year *i* (0 = most recent)."""
        return self._line(self.income, item, i)

    def bal(self, item, i=0):
        """Balance-sheet line item for fiscal year *i* (0 = most recent)."""
        return self._line(self.balance, item, i)

    @staticmethod
    def _line(store, item, i):
        values = store.get(item)
        if not values or i >= len(values):
            return None
        return values[i]

    def g(self, key):
        """Flat asset_info column as a finite float, or None."""
        return _f(self.flat.get(key))

    # -- derived line items with fallbacks -----------------------------------
    def ebit(self, i=0):
        return self.inc('EBIT', i) or self.inc('Operating Income', i)

    def ebitda(self, i=0):
        v = self.inc('EBITDA', i) or self.inc('Normalized EBITDA', i)
        if v is not None:
            return v
        e, d = self.ebit(i), self.inc('Reconciled Depreciation', i)
        return None if (e is None or d is None) else e + d

    def revenue(self, i=0):
        return self.inc('Total Revenue', i) or self.inc('Operating Revenue', i)

    def net_income(self, i=0):
        return self.inc('Net Income', i) or self.inc('Net Income Common Stockholders', i)

    def gross_profit(self, i=0):
        v = self.inc('Gross Profit', i)
        if v is not None:
            return v
        r, c = self.revenue(i), self.inc('Cost Of Revenue', i)
        return None if (r is None or c is None) else r - c

    def eps(self, i=0):
        return self.inc('Diluted EPS', i) or self.inc('Basic EPS', i)

    def equity(self, i=0):
        return self.bal('Stockholders Equity', i) or self.bal('Common Stock Equity', i)

    def total_liabilities(self, i=0):
        v = self.bal('Total Liabilities Net Minority Interest', i)
        if v is not None:
            return v
        ta = self.bal('Total Assets', i)
        eq = self.bal('Total Equity Gross Minority Interest', i) or self.equity(i)
        return None if (ta is None or eq is None) else ta - eq

    def working_capital(self, i=0):
        v = self.bal('Working Capital', i)
        if v is not None:
            return v
        ca, cl = self.bal('Current Assets', i), self.bal('Current Liabilities', i)
        return None if (ca is None or cl is None) else ca - cl

    def shares(self, i=0):
        return (self.bal('Ordinary Shares Number', i) or self.bal('Share Issued', i)
                or self.inc('Diluted Average Shares', i))

    def tax_rate(self, i=0):
        """Effective tax rate, clamped to a sane band; 21 % when not derivable."""
        r = _div(self.inc('Tax Provision', i), self.inc('Pretax Income', i))
        if r is None or r < 0 or r > 0.6:
            return 0.21
        return r

    # -- currency ------------------------------------------------------------
    def _to_report(self, value):
        """Convert a market figure (quote currency) into the report currency."""
        v = _f(value)
        if v is None:
            return None
        if self.currency == self.report_currency:
            return v
        if not self.fx:
            return None
        return v / self.fx

    @property
    def market_cap_report(self):
        return self._to_report(self.g('marketCap'))

    @property
    def enterprise_value_report(self):
        ev = self._to_report(self.g('enterpriseValue'))
        if ev is not None:
            return ev
        # Fall back to market cap + net debt, all in report currency.
        mc = self.market_cap_report
        nd = self.bal('Net Debt') or _div(
            (self.bal('Total Debt') or 0) - (self.bal('Cash And Cash Equivalents') or 0), 1)
        return None if mc is None else mc + (nd or 0)

    # -- Piotroski -----------------------------------------------------------
    def piotroski(self):
        """(score, max_score, [(criterion_key, passed_or_None), ...]).

        Seven criteria come from two fiscal years of statements. The two cash flow
        tests fall back to the TTM ``operatingCashflow`` snapshot — both are level
        tests, not year-over-year deltas, so the substitution holds; when that
        column is empty the criteria are dropped and ``max_score`` shrinks.
        """
        if self._piotroski is not None:
            return self._piotroski

        checks: list[tuple[str, bool | None]] = []

        roa0 = _div(self.net_income(0), self.bal('Total Assets', 0))
        roa1 = _div(self.net_income(1), self.bal('Total Assets', 1))
        checks.append(('roa_positive', None if roa0 is None else roa0 > 0))
        checks.append(('roa_rising', None if (roa0 is None or roa1 is None) else roa0 > roa1))

        cfo = self.g('operatingCashflow')
        ni_ttm = self.g('netIncomeToCommon')
        checks.append(('cfo_positive', None if cfo is None else cfo > 0))
        checks.append(('accruals', None if (cfo is None or ni_ttm is None) else cfo > ni_ttm))

        lev0 = _div(self.bal('Long Term Debt', 0), self.bal('Total Assets', 0))
        lev1 = _div(self.bal('Long Term Debt', 1), self.bal('Total Assets', 1))
        checks.append(('leverage_falling', None if (lev0 is None or lev1 is None) else lev0 <= lev1))

        cr0 = _div(self.bal('Current Assets', 0), self.bal('Current Liabilities', 0))
        cr1 = _div(self.bal('Current Assets', 1), self.bal('Current Liabilities', 1))
        checks.append(('liquidity_rising', None if (cr0 is None or cr1 is None) else cr0 > cr1))

        sh0, sh1 = self.shares(0), self.shares(1)
        checks.append(('no_dilution', None if (sh0 is None or sh1 is None) else sh0 <= sh1 * 1.001))

        gm0 = _div(self.gross_profit(0), self.revenue(0))
        gm1 = _div(self.gross_profit(1), self.revenue(1))
        checks.append(('margin_rising', None if (gm0 is None or gm1 is None) else gm0 > gm1))

        at0 = _div(self.revenue(0), self.bal('Total Assets', 0))
        at1 = _div(self.revenue(1), self.bal('Total Assets', 1))
        checks.append(('turnover_rising', None if (at0 is None or at1 is None) else at0 > at1))

        known = [c for c in checks if c[1] is not None]
        score = sum(1 for c in known if c[1])
        self._piotroski = (score, len(known), checks)
        return self._piotroski

    # -- Altman Z ------------------------------------------------------------
    def altman_z(self):
        """Classic five-factor Altman Z. None for financials (no meaning there)."""
        if self.sector == _FINANCIAL_SECTOR:
            return None
        ta = self.bal('Total Assets')
        tl = self.total_liabilities()
        wc = self.working_capital()
        re = self.bal('Retained Earnings')
        ebit = self.ebit()
        rev = self.revenue()
        mc = self.market_cap_report
        if None in (ta, tl, wc, re, ebit, rev, mc) or ta <= 0 or tl <= 0:
            return None
        return (1.2 * wc / ta + 1.4 * re / ta + 3.3 * ebit / ta
                + 0.6 * mc / tl + 1.0 * rev / ta)

    # -- the metric set ------------------------------------------------------
    def metrics(self) -> dict:
        """All registry metrics as ``key -> float|None`` (percent already scaled)."""
        if self._metrics is not None:
            return self._metrics

        m: dict = {}
        rev, ebit, ebitda = self.revenue(), self.ebit(), self.ebitda()
        ta = self.bal('Total Assets')
        eq = self.equity()
        mc_r = self.market_cap_report
        ev_r = self.enterprise_value_report

        # --- strength
        m['cash_to_debt'] = _div(self.bal('Cash And Cash Equivalents') or self.g('totalCash'),
                                 self.bal('Total Debt') or self.g('totalDebt'))
        m['equity_to_asset'] = _div(eq, ta)
        d2e = self.g('debtToEquity')
        m['debt_to_equity'] = (d2e / 100.0) if d2e is not None else _div(self.bal('Total Debt'), eq)
        m['debt_to_ebitda'] = _div(self.bal('Total Debt') or self.g('totalDebt'), ebitda)
        m['interest_coverage'] = _div(ebit, self.inc('Interest Expense'))
        m['altman_z'] = self.altman_z()
        p_score, p_max, _ = self.piotroski()
        m['piotroski'] = float(p_score) if p_max else None

        # --- growth (three-year CAGR over the four stored fiscal years)
        last = self.n_years - 1
        span = min(3, last) if last > 0 else 0
        if span:
            m['rev_cagr3'] = _cagr(self.revenue(0), self.revenue(span), span)
            m['ebitda_cagr3'] = _cagr(self.ebitda(0), self.ebitda(span), span)
            m['eps_cagr3'] = _cagr(self.eps(0), self.eps(span), span)
            m['book_cagr3'] = _cagr(self.equity(0), self.equity(span), span)
        rg = self.g('revenueGrowth')
        m['rev_yoy'] = rg * 100.0 if rg is not None else None
        eg = self.g('earningsGrowth')
        m['eps_yoy'] = eg * 100.0 if eg is not None else None

        # --- profitability (statement first, Yahoo TTM as fallback)
        def _margin(stmt_value, flat_key):
            """Statement margin in percent, falling back to Yahoo's TTM fraction."""
            r = _p100(_div(stmt_value, rev))
            return r if r is not None else _p100(self.g(flat_key))

        m['gross_margin'] = _margin(self.gross_profit(), 'grossMargins')
        m['operating_margin'] = _margin(self.inc('Operating Income'), 'operatingMargins')
        m['net_margin'] = _margin(self.net_income(), 'profitMargins')
        m['ebitda_margin'] = _margin(ebitda, 'ebitdaMargins')
        m['fcf_margin'] = _p100(_div(self.g('freeCashflow'), self.g('totalRevenue') or rev))

        roe = _p100(self.g('returnOnEquity'))
        m['roe'] = roe if roe is not None else _p100(_div(self.net_income(), eq))
        roa = _p100(self.g('returnOnAssets'))
        m['roa'] = roa if roa is not None else _p100(_div(self.net_income(), ta))

        nopat = None if ebit is None else ebit * (1.0 - self.tax_rate())
        m['roic'] = _p100(_div(nopat, self.bal('Invested Capital')))
        cl = self.bal('Current Liabilities')
        cap_employed = None if (ta is None or cl is None) else ta - cl
        m['roce'] = _p100(_div(ebit, cap_employed))
        wc, ppe = self.working_capital(), self.bal('Net PPE')
        gb_capital = None if (wc is None or ppe is None) else max(wc, 0.0) + ppe
        m['roc_greenblatt'] = _p100(_div(ebit, gb_capital))
        prof_years = [self.net_income(i) for i in range(self.n_years)]
        prof_years = [v for v in prof_years if v is not None]
        m['years_profitable'] = float(sum(1 for v in prof_years if v > 0)) if prof_years else None

        # --- valuation (Yahoo's own ratios where they exist — already unit-consistent)
        m['pe'] = self.g('trailingPE')
        m['forward_pe'] = self.g('forwardPE')
        m['ps'] = self.g('priceToSalesTrailing12Months')
        m['pb'] = self.g('priceToBook')
        m['ev_ebitda'] = self.g('enterpriseToEbitda')
        m['ev_revenue'] = self.g('enterpriseToRevenue')
        m['p_tangible_book'] = _div(mc_r, self.bal('Tangible Book Value'))
        m['p_fcf'] = _div(mc_r, self.g('freeCashflow'))
        m['p_ocf'] = _div(mc_r, self.g('operatingCashflow'))
        m['ev_ebit'] = _div(ev_r, ebit)
        m['earnings_yield'] = _p100(_div(ebit, ev_r))
        m['fcf_yield'] = _p100(_div(self.g('freeCashflow'), mc_r))

        # --- liquidity
        m['current_ratio'] = self.g('currentRatio') or _div(self.bal('Current Assets'), cl)
        m['quick_ratio'] = self.g('quickRatio')
        m['cash_ratio'] = _div(self.bal('Cash And Cash Equivalents'), cl)
        cogs = self.inc('Cost Of Revenue')

        def days(ratio):
            return None if ratio is None else ratio * 365.0

        m['days_inventory'] = days(_div(self.bal('Inventory'), cogs))
        m['days_sales_outstanding'] = days(_div(self.bal('Accounts Receivable'), rev))
        m['days_payable'] = days(_div(self.bal('Accounts Payable'), cogs))

        # --- dividend & buy back (dividendYield is already a percent in asset_info)
        m['dividend_yield'] = self.g('dividendYield')
        m['payout_ratio'] = _p100(self.g('payoutRatio'))
        m['avg_yield_5y'] = self.g('fiveYearAvgDividendYield')
        sh0, sh1 = self.shares(0), self.shares(1)
        bb = None
        if sh0 and sh1 and sh1 > 0:
            bb = -(sh0 / sh1 - 1.0) * 100.0
        m['buyback_yield'] = bb
        if bb is not None or m['dividend_yield'] is not None:
            m['shareholder_yield'] = (m['dividend_yield'] or 0.0) + (bb or 0.0)
        else:
            m['shareholder_yield'] = None

        # Blank out what cannot be trusted for this ticker.
        if self.currency != self.report_currency and not self.fx:
            for k in _FX_SENSITIVE:
                m[k] = None
        if self.sector == _FINANCIAL_SECTOR:
            for k in _SUPPRESSED_FOR_FINANCIALS:
                m[k] = None

        for k in METRICS:
            m.setdefault(k, None)
        self._metrics = m
        return m

    # -- quote ---------------------------------------------------------------
    def price(self):
        return self.g('currentPrice') or self.g('regularMarketPrice') or self.g('previousClose')


# ── Loading ──────────────────────────────────────────────────────────────────
def load(ticker: str, fx: float | None = None) -> Fundamentals | None:
    """Build the Fundamentals object for *ticker* straight from asset_info.db."""
    path = _db('asset_info.db')
    if not os.path.exists(path):
        return None
    cols = ', '.join(_FLAT_FIELDS)
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    except Exception:
        return None
    try:
        row = con.execute(
            f'SELECT {cols}, incomeSheet, balanceSheet FROM asset_info WHERE ticker = ?',
            (ticker,)).fetchone()
    except Exception:
        return None
    finally:
        con.close()
    if not row:
        return None
    flat = dict(zip(_FLAT_FIELDS, row[:len(_FLAT_FIELDS)]))
    return Fundamentals(flat, _parse_sheet(row[-2]), _parse_sheet(row[-1]), fx=fx)


def peer_metrics(sector: str = '', industry: str = '') -> dict[str, np.ndarray]:
    """Metric distributions across the peer group, as ``key -> array of values``.

    Pass *industry* for the narrow comparison, *sector* for the broad one. No FX
    rate is fetched for peers, so tickers whose quote and report currency differ
    contribute to every metric except the FX-sensitive ones. Momentum is left out
    — it would mean opening one price database per peer.

    Roughly 0.15 s for a sector of ~700 equities; the caller is expected to cache.
    """
    path = _db('asset_info.db')
    if not os.path.exists(path) or not (sector or industry):
        return {}
    cols = ', '.join(_FLAT_FIELDS)
    where, params = ('industry = ?', (industry,)) if industry else ('sector = ?', (sector,))
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    except Exception:
        return {}
    try:
        rows = con.execute(
            f"SELECT {cols}, incomeSheet, balanceSheet FROM asset_info "
            f"WHERE quoteType = 'EQUITY' AND {where}", params).fetchall()
    except Exception:
        return {}
    finally:
        con.close()

    buckets: dict[str, list] = {k: [] for k in METRICS}
    for row in rows:
        flat = dict(zip(_FLAT_FIELDS, row[:len(_FLAT_FIELDS)]))
        try:
            fund = Fundamentals(flat, _parse_sheet(row[-2]), _parse_sheet(row[-1]))
            values = fund.metrics()
        except Exception:
            continue
        for k, v in values.items():
            if v is not None:
                buckets[k].append(v)
    return {k: np.asarray(v, dtype=float) for k, v in buckets.items() if v}


def percentile(value, peers: np.ndarray, higher_better: bool):
    """Percentile rank of *value* within *peers*, direction-adjusted (0–100).

    100 always means "best in the peer group". Returns None when the comparison
    would be meaningless (no value, fewer than ten peers).
    """
    if value is None or peers is None or len(peers) < 10:
        return None
    arr = peers[np.isfinite(peers)]
    if len(arr) < 10:
        return None
    below = float((arr < value).mean() * 100.0)
    return below if higher_better else 100.0 - below


def group_rank(metrics: dict, peers: dict, group: str):
    """0–10 headline rank for a metric group: the mean peer percentile of its
    metrics, rescaled. None when fewer than three metrics can be ranked."""
    scores = []
    for key, (grp, _unit, higher) in METRICS.items():
        if grp != group:
            continue
        p = percentile(metrics.get(key), peers.get(key), higher)
        if p is not None:
            scores.append(p)
    if len(scores) < 3:
        return None
    return round(float(np.mean(scores)) / 10.0, 1)


# ── History series for the charts ────────────────────────────────────────────
def history(fund: Fundamentals) -> pd.DataFrame:
    """Per-fiscal-year series for the history charts (oldest year first).

    Columns: revenue, gross_profit, ebitda, net_income, eps, equity, total_debt,
    total_assets, shares, plus the three margins in percent.
    """
    years = list(reversed(fund.years))
    if not years:
        return pd.DataFrame()
    idx = [y.date() for y in years]
    n = len(years)
    # Column i in the sheets is the newest year, so walk the index backwards.
    def series(fn):
        return [fn(n - 1 - k) for k in range(n)]

    df = pd.DataFrame({
        'revenue': series(fund.revenue),
        'gross_profit': series(fund.gross_profit),
        'ebitda': series(fund.ebitda),
        'operating_income': series(lambda i: fund.inc('Operating Income', i)),
        'net_income': series(fund.net_income),
        'eps': series(fund.eps),
        'equity': series(fund.equity),
        'total_assets': series(lambda i: fund.bal('Total Assets', i)),
        'total_debt': series(lambda i: fund.bal('Total Debt', i)),
        'shares': series(fund.shares),
    }, index=idx)
    for name, num in (('gross_margin', 'gross_profit'), ('operating_margin', 'operating_income'),
                      ('net_margin', 'net_income'), ('ebitda_margin', 'ebitda')):
        df[name] = df[num] / df['revenue'] * 100.0
    return df


def valuation_history(ticker: str, fund: Fundamentals, fx: float | None = None) -> pd.DataFrame:
    """Daily P/E, P/S and P/B since the oldest stored fiscal year.

    The per-share fundamentals step at each fiscal-year end and are converted from
    the report currency into the quote currency, so the ratios line up with the
    price series. Returns an empty frame when either side is missing.
    """
    hist = history(fund)
    if hist.empty:
        return pd.DataFrame()
    close = daily_close(ticker)
    if close.empty:
        return pd.DataFrame()

    rate = 1.0
    if fund.currency != fund.report_currency:
        if not fx:
            return pd.DataFrame()
        rate = fx                      # value_quote = value_report * fx

    shares = hist['shares']
    per_share = pd.DataFrame({
        'eps': hist['eps'].where(hist['eps'].notna(), hist['net_income'] / shares),
        'sps': hist['revenue'] / shares,
        'bps': hist['equity'] / shares,
    }) * rate
    per_share.index = pd.to_datetime(per_share.index)

    close = close[close.index >= per_share.index.min()]
    if close.empty:
        return pd.DataFrame()
    stepped = per_share.reindex(per_share.index.union(close.index)).ffill().reindex(close.index)

    out = pd.DataFrame(index=close.index)
    out['close'] = close
    out['pe'] = (close / stepped['eps']).where(stepped['eps'] > 0)
    out['ps'] = (close / stepped['sps']).where(stepped['sps'] > 0)
    out['pb'] = (close / stepped['bps']).where(stepped['bps'] > 0)
    # The stepped per-share figures behind the ratios, in the quote currency —
    # kept so a caller can see what a ratio was divided by.
    out['eps'] = stepped['eps']
    out['sps'] = stepped['sps']
    out['bps'] = stepped['bps']
    return out


def daily_close(ticker: str) -> pd.Series:
    """Daily close series from yf_<ticker>.db, empty on any problem."""
    path = _db(f'yf_{ticker}.db')
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    except Exception:
        return pd.Series(dtype=float)
    try:
        df = pd.read_sql_query(
            'SELECT Date, Close FROM day_data WHERE Close IS NOT NULL ORDER BY Date', con)
    except Exception:
        return pd.Series(dtype=float)
    finally:
        con.close()
    if df.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(df['Close'], errors='coerce')
    s.index = pd.to_datetime(df['Date'], errors='coerce')
    return s.dropna()


# ── Rule-based signals ───────────────────────────────────────────────────────
def signals(fund: Fundamentals, val_hist: pd.DataFrame | None = None) -> tuple[list, list]:
    """(warnings, positives) as ``[(key, params_dict), ...]``.

    Every entry carries the numbers that triggered it, so the reader can check the
    claim instead of trusting a badge. Wording lives in the locale files; this
    function returns data only.
    """
    m = fund.metrics()
    warn: list[tuple[str, dict]] = []
    good: list[tuple[str, dict]] = []

    def w(key, cond, **params):
        if cond:
            warn.append((key, params))

    def g(key, cond, **params):
        if cond:
            good.append((key, params))

    def r(value, digits=1):
        return None if value is None else round(value, digits)

    z = m.get('altman_z')
    w('altman_distress', z is not None and z < 1.81, z=r(z, 2))
    g('altman_safe', z is not None and z > 2.99, z=r(z, 2))

    dte = m.get('debt_to_ebitda')
    w('leverage', dte is not None and dte > 4, ratio=r(dte))
    ic = m.get('interest_coverage')
    w('interest', ic is not None and ic < 2, ratio=r(ic))
    g('interest_strong', ic is not None and ic > 10, ratio=r(ic))

    rc = m.get('rev_cagr3')
    w('revenue_shrinking', rc is not None and rc < 0, cagr=r(rc))
    g('revenue_growing', rc is not None and rc > 10, cagr=r(rc))

    bb = m.get('buyback_yield')
    w('dilution', bb is not None and bb < -2, pct=r(-bb if bb is not None else None))
    g('buyback', bb is not None and bb > 1, pct=r(bb))

    fcf = fund.g('freeCashflow')
    w('negative_fcf', fcf is not None and fcf < 0, value=r(fcf / 1e6 if fcf else None, 0))
    fy = m.get('fcf_yield')
    g('fcf_yield', fy is not None and fy > 5, pct=r(fy))

    roic = m.get('roic')
    w('low_roic', roic is not None and roic < 5, pct=r(roic))
    g('high_roic', roic is not None and roic > 15, pct=r(roic))

    pr = m.get('payout_ratio')
    w('payout', pr is not None and pr > 100, pct=r(pr, 0))

    hist = history(fund)
    if not hist.empty:
        nm = hist['net_margin'].dropna()
        if len(nm) >= 3:
            # hist runs oldest → newest, so a decreasing series is margin erosion.
            w('margin_erosion', nm.is_monotonic_decreasing,
              first=r(nm.iloc[0]), last=r(nm.iloc[-1]))
            g('margin_expansion', nm.is_monotonic_increasing,
              first=r(nm.iloc[0]), last=r(nm.iloc[-1]))

    if val_hist is not None and not val_hist.empty:
        # dropna first: a company with no profitable year has an all-NaN P/E series,
        # and taking the median of that warns about an empty slice.
        pe_series = val_hist['pe'].dropna()
        pe_now = m.get('pe')
        pe_med = float(pe_series.median()) if not pe_series.empty else None
        if pe_now and pe_med and pe_med > 0:
            w('rich_vs_history', pe_now > pe_med * 1.5, pe=r(pe_now), median=r(float(pe_med)))
            g('cheap_vs_history', pe_now < pe_med * 0.7, pe=r(pe_now), median=r(float(pe_med)))

    p_score, p_max, _ = fund.piotroski()
    if p_max >= 7:
        w('piotroski_weak', p_score <= 3, score=p_score, max=p_max)
        g('piotroski_strong', p_score >= 7, score=p_score, max=p_max)

    return warn, good
