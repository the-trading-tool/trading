"""Daily exchange-rate series from the local price databases (read only).

``per_unit(currency, system_currency)`` is the daily series "units of the system
currency per 1 unit of *currency*", built from the FX histories the daily
download keeps in ``yf_<PAIR>=X.db`` (day_data):

* ``{CCY}EUR=X`` -- EUR per CCY (AUD, BRL, CAD, CHF, CNY, HKD, INR, JPY, KRW, ...)
* ``EURUSD=X``   -- USD per EUR (USD is the inverse)
* ``GBPUSD=X``   -- USD per GBP (GBP = GBPUSD / EURUSD)

Every other system currency goes through EUR (sys per ccy = EUR per ccy / EUR per
sys). Minor units (GBp, ZAc, ILA) are their major currency / 100. None when no
series exists -- the caller then falls back to today's rate.

No network, no writes: a connection with ``mode=ro``; a missing file stays missing.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE: dict = {}       # (db_dir, ticker, day) -> Series; a day's data is read once


def _db_dir(db_path: str = 'database') -> str:
    if db_path and os.path.isabs(db_path) and os.path.isdir(db_path):
        return db_path
    from tradinglib import tools
    return os.path.dirname(tools.Tools().get_path(path=db_path, file_name='x.db'))


def _series(ticker: str, db_path: str = 'database') -> pd.Series:
    """Daily closes of a local FX price DB, indexed by date (empty if unavailable)."""
    folder = _db_dir(db_path)
    key = (folder, ticker, dt.date.today().isoformat())
    if key in _CACHE:
        return _CACHE[key]
    path = os.path.join(folder, f'yf_{ticker}.db')
    s = pd.Series(dtype=float)
    if os.path.exists(path):
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            df = pd.read_sql_query('SELECT Date, Close FROM day_data WHERE Close IS NOT NULL', con)
            s = pd.to_numeric(df['Close'], errors='coerce')
            s.index = pd.to_datetime(df['Date'], errors='coerce').dt.normalize()
            s = s[s > 0].dropna()
            s = s[~s.index.duplicated(keep='last')].sort_index()
        except Exception:
            logger.debug('fx series %s not readable', ticker, exc_info=True)
            s = pd.Series(dtype=float)
        finally:
            con.close()
    _CACHE[key] = s
    return s


def _eur_per(ccy: str, db_path: str) -> pd.Series | None:
    """EUR per 1 unit of *ccy* (a major currency), None without a series."""
    if ccy == 'EUR':
        return None
    if ccy == 'USD':
        eu = _series('EURUSD=X', db_path)
        return (1.0 / eu) if not eu.empty else None
    if ccy == 'GBP':
        g, eu = _series('GBPUSD=X', db_path), _series('EURUSD=X', db_path)
        if g.empty or eu.empty:
            return None
        j = pd.concat([g.rename('g'), eu.rename('e')], axis=1).sort_index().ffill().dropna()
        return j['g'] / j['e']
    s = _series(f'{ccy}EUR=X', db_path)
    return s if not s.empty else None


def per_unit(currency: str, system_currency: str = 'EUR',
             db_path: str = 'database') -> pd.Series | None:
    """Daily series: system-currency units per 1 *currency*; None if not available."""
    from tradinglib.utils import DataUtils
    if not currency or currency == system_currency:
        return None
    major, factor = DataUtils.normalize_currency(currency)
    sys_major, sys_factor = DataUtils.normalize_currency(system_currency)
    if not (DataUtils._is_currency_code(major) and DataUtils._is_currency_code(sys_major)):
        return None
    if major == sys_major:               # e.g. GBp in GBP: a fixed factor, no series
        return None
    num = _eur_per(major, db_path) if major != 'EUR' else None
    den = _eur_per(sys_major, db_path) if sys_major != 'EUR' else None
    if major != 'EUR' and num is None:
        return None
    if sys_major != 'EUR' and den is None:
        return None
    if den is None:                      # system currency EUR
        out = num
    elif num is None:                    # currency EUR
        out = 1.0 / den
    else:
        j = pd.concat([num.rename('n'), den.rename('d')], axis=1).sort_index().ffill().dropna()
        out = j['n'] / j['d']
    return (out / factor * sys_factor).dropna()
