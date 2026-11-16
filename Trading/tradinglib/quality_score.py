"""Quality score: twelve fundamental criteria, 100 points, plus red flags.

Built after a points scheme the user brought in (2026-11-16): profitability,
balance sheet, cash quality, valuation and growth, each criterion with fixed
points. Three things differ from the original on purpose:

* **Points are scaled to what is available.** Yahoo does not report every line
  for every company; a missing criterion is neither passed nor failed. The score
  is ``100 x points / available points`` and needs at least ``MIN_AVAILABLE``
  points to exist at all. ``quality_avail`` says how much of it stood on data.
* **Banks and insurers skip the balance-sheet and cash-flow criteria.** Net
  debt, interest cover, current ratio and free cash flow describe the business
  model there, not its quality (``FINANCIAL_SKIP``).
* **A sector-relative rank sits next to the fixed thresholds.** "Operating
  margin above 15 %" fails retailers and industrials by construction; ranking
  each criterion within the sector and weighting by its points
  (``quality_sector``) avoids turning a quality filter into a sector bet.
  ``quality_pct`` is that rank as a percentile over all scored equities -- an
  absolute cut like ``quality_pct >= 85`` ("top 15 %") then means the same on
  every day and in every universe.

**Red flags** (``quality_flags``) are the exclusion side: high leverage, weak
interest cover, cash burn, losses without operating cash, and two negative
earnings surprises in a row -- the last one is the only part that has been
measured (2020-2026: -1.5 pp against the market over 63 days, negative in
7 of 7 years; see the surprise measurement in CLAUDE.md).

**Not measured as a whole, and not measurable yet.** The inputs come from
today's snapshot in ``asset_info``; there is no history before 2026-08. The
score is therefore meant for today's selection (Kandidaten), never for a
backtest -- written into asset_simulation it would be the same look-ahead as
``pctTargetHighPrice``. ``quality_score_history`` records every change so the
question can be asked once the series is long enough.

CLI::

    python -m tradinglib.quality_score                # compute, print summary (read-only)
    python -m tradinglib.quality_score /update        # compute and store
    python -m tradinglib.quality_score /show:SAP.DE   # one ticker in detail
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TABLE = 'quality_scores'
HISTORY = 'quality_score_history'

# key, group, points, higher_is_better (sector rank)
CRITERIA = (
    ('roe',             'profitability', 8,  True),
    ('op_margin',       'profitability', 9,  True),
    ('gm_stability',    'profitability', 8,  True),
    ('net_debt_ebitda', 'balance',       10, False),
    ('interest_cover',  'balance',       8,  True),
    ('current_ratio',   'balance',       7,  True),
    ('cfo_to_ni',       'cash',          10, True),
    ('fcf_years',       'cash',          8,  True),
    ('sbc_revenue',     'cash',          7,  False),
    ('peg',             'valuation',     10, False),
    ('fcf_yield',       'valuation',     8,  True),
    ('revenue_growth',  'growth',        7,  True),
)
POINTS = {k: p for k, _g, p, _h in CRITERIA}
HIGHER_BETTER = {k: h for k, _g, _p, h in CRITERIA}
GROUPS = ('profitability', 'balance', 'cash', 'valuation', 'growth')

# Pass rules, as in the scheme. gm_stability = latest gross margin minus the
# mean of the earlier years (fraction): "stable or expanding" allows a small dip.
THRESHOLDS = {
    'roe':             ('>', 0.15),
    'op_margin':       ('>', 0.15),
    'gm_stability':    ('>=', -0.02),
    'net_debt_ebitda': ('<', 2.5),
    'interest_cover':  ('>', 5.0),
    'current_ratio':   ('>', 1.5),
    'cfo_to_ni':       ('>=', 1.0),
    'fcf_years':       ('>=', 5),
    'sbc_revenue':     ('<', 0.05),
    'peg':             ('<', 1.5),
    'fcf_yield':       ('>', 0.04),
    'revenue_growth':  ('>', 0.08),
}

FINANCIAL_SECTORS = {'Financial Services'}
FINANCIAL_SKIP = {'gm_stability', 'net_debt_ebitda', 'interest_cover', 'current_ratio',
                  'cfo_to_ni', 'fcf_years', 'fcf_yield'}

MIN_AVAILABLE = 40          # points with data; banks reach 41 at most
MIN_PEERS = 5               # sector rank needs this many values, else all equities
TIERS = ((85, 1), (70, 2), (50, 3))     # score >= x -> tier; below: 4

# Red flags -- the exclusion side.
FLAG_LEVERAGE = 4.0         # net debt / EBITDA
FLAG_INTEREST = 1.5         # EBIT / interest
SURPRISE_MAX_AGE_DAYS = 200  # the latest report must be this recent to count
FLAGS = ('leverage', 'interest', 'cash_burn', 'losses', 'surprise')

INF = float('inf')


# ── helpers ──────────────────────────────────────────────────────────────────
def _f(value):
    try:
        if value is None:
            return None
        v = float(value)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _sheet(raw):
    from tradinglib.fundamentals import _parse_sheet
    return _parse_sheet(raw)


def _raw_line(raw, name: str) -> list:
    """One statement row straight from the JSON, newest year first, gaps dropped.

    ``fundamentals._parse_sheet`` discards Yahoo's oldest, mostly empty year --
    right for ratios over a whole statement, wrong for a single row like free
    cash flow, whose value for that year is usually there (AAPL: 5 years, the
    parser keeps 4). The five-year FCF criterion reads the row directly.
    """
    import json
    if not raw or not isinstance(raw, str):
        return []
    try:
        d = json.loads(raw)
        k = d['index'].index(name)
    except (ValueError, KeyError, TypeError):
        return []
    pairs = sorted(((c, v) for c, v in zip(d['columns'], d['data'][k]) if v is not None),
                   key=lambda cv: cv[0], reverse=True)
    return [float(v) for _c, v in pairs]


def _line(rows: dict, *names):
    for name in names:
        if name in rows:
            return rows[name]
    return None


def _fx(quote_currency, report_currency, rates: dict) -> float | None:
    """Units of quote currency per report currency; None when unknown.

    ``rates`` caches per pair. Pence listings (GBp) are handled by
    DataUtils.get_exchange_rate; Yahoo's marketCap of a pence listing is in GBP
    though, so the major unit is what we convert into.
    """
    from tradinglib.utils import DataUtils
    if not quote_currency or not report_currency:
        return 1.0 if not report_currency else None
    major, _factor = DataUtils.normalize_currency(quote_currency)
    if major == report_currency:
        return 1.0
    key = (major, report_currency)
    if key not in rates:
        try:
            rate = float(DataUtils.get_exchange_rate(symbol=major, system_currency=report_currency))
            rates[key] = rate if rate and rate != 1.0 else None
        except Exception:
            rates[key] = None
    return rates[key]


def _passes(key, value) -> bool:
    op, cut = THRESHOLDS[key]
    if value is None:
        return False
    return {'>': value > cut, '>=': value >= cut, '<': value < cut}[op]


# ── metrics for one company ──────────────────────────────────────────────────
def metrics(row: dict, quarters: pd.DataFrame | None = None, rates: dict | None = None,
            today: date | None = None) -> dict:
    """Criterion values, pass flags and red flags for one asset_info row.

    Returns ``{'values': {k: float|None}, 'passed': {k: bool|None}, 'flags': [...],
    'notes': {...}}``. ``passed[k] is None`` = no data (criterion not counted).
    """
    rates = {} if rates is None else rates
    today = pd.Timestamp(today or date.today())
    g = row.get
    fin = (g('sector') or '') in FINANCIAL_SECTORS
    values: dict = {}
    passed: dict = {}
    notes: dict = {}

    def put(key, value, ok=None):
        """ok: explicit pass/fail; default = threshold on value; value None + ok None = no data."""
        values[key] = value
        if fin and key in FINANCIAL_SKIP:
            passed[key] = None
        elif ok is not None:
            passed[key] = bool(ok)
        else:
            passed[key] = None if value is None else _passes(key, value)

    income, _ = _sheet(g('incomeSheet'))
    cash, cash_years = _sheet(g('cashflowSheet'))

    # Profitability
    put('roe', _f(g('returnOnEquity')))
    put('op_margin', _f(g('operatingMargins')))
    gp, rev = _line(income, 'Gross Profit'), _line(income, 'Total Revenue', 'Operating Revenue')
    gms = []
    if gp and rev:
        for a, b in zip(gp, rev):
            if a is not None and b:
                gms.append(a / b)
            else:
                break
    put('gm_stability', (gms[0] - float(np.mean(gms[1:]))) if len(gms) >= 3 else None)

    # Balance sheet
    debt, cash_now, ebitda = _f(g('totalDebt')), _f(g('totalCash')), _f(g('ebitda'))
    nde = None
    if debt is not None and ebitda is not None:
        net = debt - (cash_now or 0.0)
        if net <= 0:
            nde = 0.0                       # net cash
        elif ebitda > 0:
            nde = net / ebitda
        else:
            nde = INF                       # debt without operating earnings
    put('net_debt_ebitda', nde)

    cover = None
    q = quarters if quarters is not None else pd.DataFrame()
    if not q.empty and {'ebit', 'interest_expense'} <= set(q.columns):
        last4 = q.sort_values('period_end').tail(4)
        if len(last4) == 4 and last4['ebit'].notna().all():
            e = float(last4['ebit'].sum())
            i = last4['interest_expense'].abs().sum(min_count=1)
            cover = _cover(e, None if pd.isna(i) else float(i), nde)
            notes['interest_cover_src'] = 'ttm'
    if cover is None and income:
        e = _line(income, 'EBIT')
        i = _line(income, 'Interest Expense', 'Interest Expense Non Operating')
        if e and e[0] is not None:
            cover = _cover(e[0], abs(i[0]) if i and i[0] is not None else None, nde)
            notes['interest_cover_src'] = 'annual'
    put('interest_cover', cover)
    put('current_ratio', _f(g('currentRatio')))

    # Cash quality
    cfo, ni = _f(g('operatingCashflow')), _f(g('netIncomeToCommon'))
    if cfo is not None and ni is not None:
        # Without a profit there are no earnings to confirm: counts as failed.
        put('cfo_to_ni', cfo / ni if ni > 0 else None, ok=(ni > 0 and cfo >= ni))
    else:
        put('cfo_to_ni', None)
    fcf_hist = _raw_line(g('cashflowSheet'), 'Free Cash Flow')
    if fcf_hist:
        run = 0
        for v in fcf_hist:
            if v > 0:
                run += 1
            else:
                break
        if run >= 5:
            put('fcf_years', 5.0)
        elif run < len(fcf_hist):
            put('fcf_years', float(run))           # a negative year inside the window
        else:
            put('fcf_years', None)                  # all positive, but fewer than 5 years known
            values['fcf_years'] = float(run)
    else:
        put('fcf_years', None)
    sbc = _f(g('stockBasedCompensation'))
    if sbc is None:
        s = _line(cash, 'Stock Based Compensation')
        sbc = s[0] if s else None
    revenue = _f(g('totalRevenue'))
    put('sbc_revenue', (abs(sbc) / revenue) if (sbc is not None and revenue and revenue > 0) else None)

    # Valuation
    peg = _f(g('trailingPegRatio'))
    if peg is None:
        pe, growth = _f(g('forwardPE')), _f(g('earningsGrowth'))
        if pe is not None and growth is not None:
            peg = pe / (growth * 100) if (pe > 0 and growth > 0) else -1.0
    put('peg', peg, ok=None if peg is None else (0 < peg < THRESHOLDS['peg'][1]))
    fcf, mcap = _f(g('freeCashflow')), _f(g('marketCap'))
    fcfy = None
    if fcf is not None and mcap and mcap > 0:
        rate = _fx(g('currency'), g('financialCurrency') or g('currency'), rates)
        if rate:
            fcfy = fcf / rate / mcap
    put('fcf_yield', fcfy)

    # Growth: analyst consensus for the current fiscal year, else trailing.
    rg = _f(g('revenueGrowthEst'))
    notes['revenue_growth_src'] = 'consensus'
    if rg is None:
        rg = _f(g('revenueGrowth'))
        notes['revenue_growth_src'] = 'trailing' if rg is not None else ''
    put('revenue_growth', rg)

    # Red flags
    flags = []
    if not fin:
        if nde is not None and nde > FLAG_LEVERAGE:
            flags.append('leverage')
        if cover is not None and cover < FLAG_INTEREST:
            flags.append('interest')
        burn = [v < 0 for v in (fcf, fcf_hist[0] if fcf_hist else None) if v is not None]
        if burn and all(burn):
            flags.append('cash_burn')
    if ni is not None and cfo is not None and ni < 0 and cfo < 0:
        flags.append('losses')
    if _two_negative_surprises(q, today):
        flags.append('surprise')
    return {'values': values, 'passed': passed, 'flags': flags, 'notes': notes,
            'financial': fin}


def _cover(ebit, interest, net_debt_ebitda):
    if ebit is None:
        return None
    if not interest:
        # No interest line: fine for a company without net debt, unknown otherwise.
        return INF if (ebit > 0 and net_debt_ebitda == 0.0) else None
    return ebit / interest


def _two_negative_surprises(q: pd.DataFrame, today: pd.Timestamp) -> bool:
    if q is None or q.empty or 'surprise_pct' not in q.columns:
        return False
    s = q[q['surprise_pct'].notna()]
    if 'report_date_source' in s.columns:
        s = s[s['report_date_source'] == 'earnings_dates']
    s = s[pd.to_datetime(s['report_date'], errors='coerce') < today].sort_values('report_date')
    if len(s) < 2:
        return False
    last = pd.to_datetime(s['report_date'].iloc[-1])
    if (today - last).days > SURPRISE_MAX_AGE_DAYS:
        return False
    return bool((s['surprise_pct'].tail(2) <= 0).all())


# ── whole universe ───────────────────────────────────────────────────────────
def compute(info: pd.DataFrame, quarterly: pd.DataFrame | None = None,
            today: date | None = None, rates: dict | None = None) -> pd.DataFrame:
    """Score every equity in *info* (asset_info rows). Pure apart from FX lookups."""
    if info is None or info.empty:
        return pd.DataFrame()
    rates = {} if rates is None else rates
    today = today or date.today()
    by_ticker = ({t: f for t, f in quarterly.groupby('ticker')}
                 if quarterly is not None and not quarterly.empty else {})
    recs = []
    for row in info.to_dict('records'):
        m = metrics(row, by_ticker.get(row['ticker']), rates, today)
        rec = {'ticker': row['ticker'], 'sector': row.get('sector') or '',
               'financial': int(m['financial'])}
        for k in POINTS:
            v = m['values'].get(k)
            rec[k] = None if v is None else (1e9 if v == INF else v)
            p = m['passed'].get(k)
            rec[f'{k}_ok'] = None if p is None else int(p)
        avail = sum(POINTS[k] for k in POINTS if m['passed'].get(k) is not None)
        pts = sum(POINTS[k] for k in POINTS if m['passed'].get(k))
        rec['quality_avail'] = avail
        rec['quality_points'] = pts
        rec['quality_score'] = round(100.0 * pts / avail, 1) if avail >= MIN_AVAILABLE else None
        rec['quality_flags'] = ','.join(m['flags'])
        rec['quality_n_flags'] = len(m['flags'])
        rec['interest_cover_src'] = m['notes'].get('interest_cover_src', '')
        rec['revenue_growth_src'] = m['notes'].get('revenue_growth_src', '')
        recs.append(rec)
    out = pd.DataFrame(recs)
    out['quality_tier'] = out['quality_score'].map(tier)
    out['quality_sector'] = sector_rank(out)
    ranked = out['quality_sector'].where(out['quality_score'].notna())
    out['quality_pct'] = (ranked.rank(pct=True) * 100).round(1)
    out['asof'] = pd.Timestamp(today).strftime('%Y-%m-%d')
    return out


def tier(score) -> int | None:
    if score is None or pd.isna(score):
        return None
    for cut, t in TIERS:
        if score >= cut:
            return t
    return 4


def sector_rank(frame: pd.DataFrame) -> pd.Series:
    """Points-weighted mean of the per-criterion percentile within the sector (0..100).

    A criterion with fewer than MIN_PEERS values in the sector is ranked against
    all equities instead. Skipped criteria (no data, banks) do not count.
    """
    total = pd.Series(0.0, index=frame.index)
    weight = pd.Series(0.0, index=frame.index)
    for key, pts in POINTS.items():
        ok = frame[f'{key}_ok']
        v = pd.to_numeric(frame[key], errors='coerce').where(ok.notna())
        if key == 'peg':
            # A PEG without growth (stored -1) is the worst, not the cheapest.
            v = v.where(~(v <= 0), np.inf)
        if key == 'cfo_to_ni':
            # Failed for lack of profit: worst value.
            v = v.where(~(ok.eq(0) & v.isna()), -np.inf)
        asc = HIGHER_BETTER[key]
        pct_all = v.rank(pct=True, ascending=asc)
        sizes = v.groupby(frame['sector']).transform('count')
        pct_sec = v.groupby(frame['sector']).rank(pct=True, ascending=asc)
        pct = pct_sec.where(sizes >= MIN_PEERS, pct_all)
        has = pct.notna()
        total = total.add((pct * pts).fillna(0.0))
        weight = weight.add(has.astype(float) * pts)
    out = (100.0 * total / weight).where(weight >= MIN_AVAILABLE)
    return out.round(1)


# ── storage ──────────────────────────────────────────────────────────────────
def _db_path(db_path: str = 'database') -> str:
    from tradinglib.tools import Tools
    return Tools().get_path(path=db_path, file_name='asset_info.db')


_INFO_COLUMNS = ('ticker', 'sector', 'currency', 'financialCurrency', 'returnOnEquity',
                 'operatingMargins', 'totalDebt', 'totalCash', 'ebitda', 'currentRatio',
                 'operatingCashflow', 'netIncomeToCommon', 'freeCashflow', 'marketCap',
                 'totalRevenue', 'trailingPegRatio', 'forwardPE', 'earningsGrowth',
                 'revenueGrowth', 'revenueGrowthEst', 'stockBasedCompensation',
                 'incomeSheet', 'cashflowSheet')


def load_inputs(conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Equities from asset_info (columns that exist) and their quarterly rows."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(asset_info)")}
    cols = [c for c in _INFO_COLUMNS if c in have]
    info = pd.read_sql_query(
        f"SELECT {', '.join(cols)} FROM asset_info WHERE quoteType = 'EQUITY'", conn)
    for c in _INFO_COLUMNS:
        if c not in info.columns:
            info[c] = None
    for c in info.columns:
        if c not in ('ticker', 'sector', 'currency', 'financialCurrency',
                     'incomeSheet', 'cashflowSheet'):
            info[c] = pd.to_numeric(info[c], errors='coerce')
    quarterly = pd.DataFrame()
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='fundamentals_quarterly'").fetchone():
        qcols = {r[1] for r in conn.execute("PRAGMA table_info(fundamentals_quarterly)")}
        want = [c for c in ('ticker', 'period_end', 'report_date', 'report_date_source',
                            'surprise_pct', 'ebit', 'interest_expense') if c in qcols]
        quarterly = pd.read_sql_query(
            f"SELECT {', '.join(want)} FROM fundamentals_quarterly "
            f"WHERE report_date <= ?", conn, params=(date.today().isoformat(),))
        for c in ('ebit', 'interest_expense'):
            if c not in quarterly.columns:
                quarterly[c] = np.nan
    return info, quarterly


_HISTORY_COLS = ('quality_score', 'quality_tier', 'quality_sector', 'quality_pct',
                 'quality_avail', 'quality_flags')


def store(conn, frame: pd.DataFrame) -> int:
    """Replace the latest table; append changed scores to the history."""
    if frame is None or frame.empty:
        return 0
    frame.to_sql(TABLE, conn, if_exists='replace', index=False)
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_ticker ON {TABLE} (ticker)")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {HISTORY} (ticker TEXT NOT NULL, asof TEXT NOT NULL, "
        "quality_score REAL, quality_tier REAL, quality_sector REAL, quality_pct REAL, "
        "quality_avail REAL, quality_flags TEXT, PRIMARY KEY (ticker, asof))")
    prev = {r[0]: r[1:] for r in conn.execute(
        f"SELECT h.ticker, {', '.join('h.' + c for c in _HISTORY_COLS)} FROM {HISTORY} h "
        f"JOIN (SELECT ticker, MAX(asof) a FROM {HISTORY} GROUP BY ticker) l "
        f"ON h.ticker = l.ticker AND h.asof = l.a")}
    rows = []
    for rec in frame[['ticker', 'asof', *_HISTORY_COLS]].itertuples(index=False):
        vals = tuple(None if (isinstance(v, float) and math.isnan(v)) else v for v in rec[2:])
        old = prev.get(rec[0])
        if old is None or any(_differs(a, b) for a, b in zip(vals, old)):
            rows.append((rec[0], rec[1], *vals))
    conn.executemany(f"INSERT OR REPLACE INTO {HISTORY} VALUES ({','.join('?' * 8)})", rows)
    conn.commit()
    return len(frame)


def _differs(a, b) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    if isinstance(a, str) or isinstance(b, str):
        return str(a) != str(b)
    return abs(float(a) - float(b)) > 1e-6


def update(conn) -> int:
    """Compute from asset_info and store. Returns the number of scored equities."""
    info, quarterly = load_inputs(conn)
    frame = compute(info, quarterly)
    store(conn, frame)
    n = int(frame['quality_score'].notna().sum()) if not frame.empty else 0
    logger.info("quality score: %d of %d equities scored", n, len(frame))
    return n


def load(db_path: str = 'database') -> pd.DataFrame:
    """Latest stored scores (read-only); empty when never computed."""
    from tradinglib.tools import open_db
    path = _db_path(db_path)
    if not os.path.exists(path):
        return pd.DataFrame()
    conn = open_db(path, readonly=True)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                            (TABLE,)).fetchone():
            return pd.DataFrame()
        return pd.read_sql_query(f"SELECT * FROM {TABLE}", conn)
    finally:
        conn.close()


def load_one(ticker: str, db_path: str = 'database') -> dict | None:
    """Stored score row of one ticker as a dict (read-only); None when absent."""
    from tradinglib.tools import open_db
    path = _db_path(db_path)
    if not ticker or not os.path.exists(path):
        return None
    conn = open_db(path, readonly=True)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                            (TABLE,)).fetchone():
            return None
        df = pd.read_sql_query(f"SELECT * FROM {TABLE} WHERE ticker = ?", conn, params=(ticker,))
    finally:
        conn.close()
    if df.empty:
        return None
    return {k: (None if (isinstance(v, float) and math.isnan(v)) else v)
            for k, v in df.iloc[0].to_dict().items()}


def summary(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return 'no equities'
    s = frame['quality_score']
    lines = [f"equities {len(frame)}, scored {int(s.notna().sum())}",
             'tiers: ' + ', '.join(f"{int(k)}: {v}" for k, v in
                                   frame['quality_tier'].value_counts().sort_index().items())]
    for flag in FLAGS:
        n = int(frame['quality_flags'].str.contains(flag, regex=False).sum())
        lines.append(f"flag {flag}: {n}")
    lines.append(f"any flag: {int((frame['quality_n_flags'] > 0).sum())}")
    avail = frame['quality_avail']
    lines.append(f"available points: median {avail.median():.0f}, "
                 f">= {MIN_AVAILABLE}: {int((avail >= MIN_AVAILABLE).sum())}")
    return '\n'.join(lines)


def main(argv=None) -> int:
    import sys
    from tradinglib import cli
    from tradinglib.tools import open_db
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = cli.parse_args(argv or sys.argv)
    write = bool(args.get('update'))
    conn = open_db(_db_path(), timeout=30.0) if write else open_db(_db_path(), readonly=True)
    try:
        info, quarterly = load_inputs(conn)
        frame = compute(info, quarterly)
        if write:
            store(conn, frame)
    finally:
        conn.close()
    print(summary(frame))
    show = args.get('show')
    if show:
        row = frame[frame['ticker'] == show]
        print(row.T.to_string() if not row.empty else f'{show}: not an equity in asset_info')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
