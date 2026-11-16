"""Earnings surprise per trading day, point in time.

Measured 2026-11-16 over 80,858 quarterly reports since 2020 (4,151 tickers,
real report dates): stocks in the top surprise quintile of their quarter beat the
average report by +1.18 pp over the following 63 trading days, the bottom
quintile lagged by -1.40 pp; the gap (+2.59 pp, t 4.5) was positive in all seven
years and in 78 % of the months, monotone across the quintiles, and it grows
from 21 to 63 days -- the price keeps drifting after the release. Two negative
surprises in a row: -1.5 pp against the market over 63 days, negative in 7 of 7
years. Hit rates stay below 50 % in every bucket; the edge comes from the right
tail, not from the typical stock.

Columns (indicator ``eps``, also stored in asset_simulation):

* ``eps_surprise``      -- surprise of the latest report in percent
  (reported vs. estimated EPS, as Yahoo computes it),
* ``eps_surprise_prev`` -- the report before that,
* ``eps_days``          -- trading days since the latest report took effect:
  1 on the first trading day **after** the report date. 0 = no report known.

**Causal by construction.** A report counts from the first trading day after its
report date -- Yahoo gives the date, not whether the release came before the
open or after the close, so the same day might not know it yet. Only rows with a
real release date (``report_date_source = 'earnings_dates'``) and a surprise are
used; an estimated date would be a guess about the past.

``eps_surprise == 0`` is a valid value (estimate met exactly); "no data" is
``eps_days == 0``. Formulas should therefore check ``eps_days >= 1``.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

COLUMNS = ('eps_surprise', 'eps_surprise_prev', 'eps_days')


def reports(ticker: str, db_path: str | None = None) -> pd.DataFrame:
    """Releases of *ticker* with a real date and a surprise, oldest first (read-only)."""
    from tradinglib.tools import Tools, open_db
    path = Tools().get_path(path=db_path or 'database', file_name='asset_info.db')
    if not ticker or not os.path.exists(path):
        return pd.DataFrame(columns=['report_date', 'surprise_pct'])
    conn = open_db(path, readonly=True)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='fundamentals_quarterly'").fetchone():
            return pd.DataFrame(columns=['report_date', 'surprise_pct'])
        df = pd.read_sql_query(
            "SELECT report_date, surprise_pct FROM fundamentals_quarterly "
            "WHERE ticker = ? AND report_date_source = 'earnings_dates' "
            "AND surprise_pct IS NOT NULL ORDER BY report_date", conn, params=(ticker,))
    finally:
        conn.close()
    df['report_date'] = pd.to_datetime(df['report_date'], errors='coerce')
    df['surprise_pct'] = pd.to_numeric(df['surprise_pct'], errors='coerce')
    return df.dropna().drop_duplicates('report_date', keep='first').reset_index(drop=True)


def daily_columns(dates, rep: pd.DataFrame) -> pd.DataFrame:
    """The three columns on the trading days *dates* (ascending, daily).

    A report dated D takes effect on the first date strictly after D. Two
    reports landing on the same trading day: the later one is the latest, the
    earlier one its predecessor.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Index(dates), errors='coerce')).normalize()
    out = pd.DataFrame(np.nan, index=idx, columns=list(COLUMNS))
    out['eps_days'] = 0.0
    if len(idx) == 0 or rep is None or rep.empty:
        return out
    pos = idx.searchsorted(pd.DatetimeIndex(rep['report_date']).normalize(), side='right')
    surprise = rep['surprise_pct'].to_numpy(dtype=float)
    latest = np.full(len(idx), np.nan)    # set on the day a report takes effect
    prev = np.full(len(idx), np.nan)
    last_val = np.nan
    for k, p in enumerate(pos):
        if p >= len(idx):
            continue                      # released after the last date
        prev[p], last_val = last_val, surprise[k]
        latest[p] = last_val
    effective = ~np.isnan(latest)
    # Position of the report in effect on each day, carried forward.
    marker = pd.Series(np.where(effective, np.arange(len(idx)), np.nan)).ffill().to_numpy()
    known = ~np.isnan(marker)
    at = np.where(known, marker, 0).astype(int)
    out['eps_surprise'] = np.where(known, latest[at], np.nan)
    out['eps_surprise_prev'] = np.where(known, prev[at], np.nan)
    out['eps_days'] = np.where(known, np.arange(len(idx)) - at + 1, 0).astype(float)
    return out
