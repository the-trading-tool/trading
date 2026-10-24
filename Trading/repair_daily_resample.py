"""Replace day_data rows that were resampled from h60_data in UTC.

Background: ``StockDataSaver._backfill_daily_from_hourly`` (debb567) was meant
for the few symbols without a Yahoo daily history (^VIXEQ). It also ran on
every FAILED 1d download (HTTP 429 in the big Saturday runs), resampled the
whole accumulated h60 history by UTC calendar day and wrote it over day_data
with INSERT OR REPLACE. Consequences:

- weekday rows replaced by hourly aggregates: pre/post-market prints in
  Open/High/Low (MSFT High up to +21 %, PLTR +56 %), hourly volume sums;
- phantom weekend rows: the US post-market bar stamped Sat 00:00 UTC, the
  Sunday-evening open of futures/FX/DXY, the Monday open of the ASX.

Later 1d runs only overwrite the last one or two months, and never delete a
date, so most of the damage is still there.

Detection is exact: a row is affected when Open/High/Low/Close AND Volume
equal the UTC resample of h60_data. A provider bar does not match the hourly
volume sum to the share (verified against fresh Yahoo data: 0 of ~4,300
unflagged rows differ, flagged US rows differ almost always).

Repair per ticker, only on flagged dates:
- the provider has the date      -> replace with the provider bar,
- the provider lacks the date    -> delete (weekend/holiday phantom),
- no usable provider history     -> delete flagged weekend rows only (unless
  the symbol trades weekends), keep the rest and report it.

Every database is copied with VACUUM INTO before its first write.

    python repair_daily_resample.py                       # dry run: counts only, no network
    python repair_daily_resample.py /tickers:MSFT,GC=F    # dry run incl. provider plan
    python repair_daily_resample.py /apply                # write (backup first)
    python repair_daily_resample.py /apply /tickers:MSFT  # write, targeted
"""
import glob
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime

import pandas as pd

from tradinglib import cli, logging_config
from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

# A provider answer with this many rows or fewer is a stunted symbol, not a history.
MIN_PROVIDER_ROWS = 5
# Pause between provider calls, same as StockDataSaver.save_all_intervals.
SLEEP_S = 0.8


def _db_dir():
    return os.path.dirname(Tools().get_path(path='database', file_name='asset_info.db'))


def _load(conn, table):
    df = pd.read_sql_query(
        f"SELECT Date, Open, High, Low, Close, Volume FROM {table}", conn)
    df['raw'] = df['Date']
    df.index = pd.to_datetime(df.pop('Date'), errors='coerce')
    df = df[~df.index.isna()].sort_index()
    for c in ('Open', 'High', 'Low', 'Close', 'Volume'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def flagged_rows(conn):
    """day_data rows equal to the UTC resample of h60_data (incl. volume)."""
    tabs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'day_data', 'h60_data'} <= tabs:
        return pd.DataFrame()
    d, h = _load(conn, 'day_data'), _load(conn, 'h60_data')
    if d.empty or h.empty:
        return pd.DataFrame()
    r = h.drop(columns='raw').resample('1D').agg(
        {'Open': 'first', 'High': 'max', 'Low': 'min',
         'Close': 'last', 'Volume': 'sum'}).dropna(subset=['Close'])
    j = d.join(r, rsuffix='_r', how='inner')
    eq = (j.Volume.fillna(0).sub(j.Volume_r.fillna(0)).abs() < 1)
    for c in ('Open', 'High', 'Low', 'Close'):
        eq &= j[c].sub(j[f'{c}_r']).abs() < 1e-6
    return j.loc[eq, ['raw', 'Open', 'High', 'Low', 'Close', 'Volume']]


def _quote_types():
    path = Tools().get_path(path='database', file_name='asset_info.db')
    try:
        with closing(open_db(path, readonly=True)) as conn:
            return {t: str(q or '').upper() for t, q in
                    conn.execute("SELECT ticker, quoteType FROM asset_info")}
    except Exception:
        logger.debug("asset_info not readable", exc_info=True)
        return {}


def trades_weekends(ticker, quote_types):
    return (quote_types.get(ticker) == 'CRYPTOCURRENCY'
            or bool(re.search(r'-(USD|EUR|USDT|BTC|ETH)$', ticker)))


def provider_daily(ticker):
    """Provider daily history, same call as StockDataSaver.fetch_data_dl('1d', 'max').

    Called directly rather than through StockDataSaver, which would open the
    ticker database read-write even in a dry run.
    """
    from tradinglib import market_data as md
    start = (datetime.now() - pd.Timedelta(days=20 * 365)).strftime('%Y-%m-%d')
    try:
        data = md.download(tickers=ticker, start=start, interval='1d', actions=False,
                           progress=False, auto_adjust=False, prepost=True, force_remote=True)
        if data is None or data.empty:
            return pd.DataFrame()
        if isinstance(data.columns, pd.MultiIndex):
            data = data.xs(ticker, axis=1, level='Ticker')
    except Exception:
        logger.info("%s: provider download failed", ticker, exc_info=True)
        return pd.DataFrame()
    data = data[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
    data.index = pd.to_datetime(data.index).normalize()
    return data[~data.index.duplicated(keep='last')]


def plan(flagged, provider, weekends):
    """(rows to replace, raw date strings to delete) for one ticker.

    Rows the provider reports identically are left out of the replacement:
    for thinly traded symbols (and h60 tables that hold one bar per day) the
    provider daily bar IS the hourly sum, so the fingerprint matches without
    any damage. Counting those as repairs overstated the extent.
    """
    days = flagged.index.normalize()
    if len(provider) > MIN_PROVIDER_ROWS:
        have = days.isin(provider.index)
        replace = provider.loc[days[have]]
        cur = flagged[have]
        same = pd.Series(True, index=replace.index)
        for c in ('Open', 'High', 'Low', 'Close'):
            same &= (replace[c].values - cur[c].values).__abs__() < 1e-6
        same &= (replace['Volume'].fillna(0).values - cur['Volume'].fillna(0).values).__abs__() < 1
        replace = replace[~same.values]
        gone = ~have & (days >= provider.index.min())
        return replace, list(flagged.raw[gone])
    if weekends:
        return pd.DataFrame(), []
    return pd.DataFrame(), list(flagged.raw[days.dayofweek >= 5])


def apply_plan(path, backup_dir, replace, delete):
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(backup_dir, os.path.basename(path))
    # The scheduler jobs write to the same files; wait for their locks.
    conn = sqlite3.connect(path, timeout=60)
    try:
        if not os.path.exists(target):
            conn.execute("VACUUM INTO ?", (target,))
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO day_data (Date, Open, High, Low, Close, Volume) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(ts.strftime('%Y-%m-%d %H:%M:%S'), float(r.Open), float(r.High),
                  float(r.Low), float(r.Close),
                  None if pd.isna(r.Volume) else int(r.Volume))
                 for ts, r in replace.iterrows()])
            conn.executemany("DELETE FROM day_data WHERE Date = ?", [(d,) for d in delete])
    finally:
        conn.close()


def main():
    args = cli.parse_args()
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))
    apply = bool(args.get('apply'))
    only = args.get('tickers')
    only = {t.strip() for t in str(only).split(',') if t.strip()} if only else None
    db_dir = _db_dir()
    backup_dir = os.path.join(db_dir, f"backup_daily_resample_{datetime.now():%Y%m%d}")
    quote_types = _quote_types()

    found = []
    for path in sorted(glob.glob(os.path.join(db_dir, 'yf_*.db'))):
        ticker = os.path.basename(path)[3:-3]
        if ticker == 'tickers' or (only and ticker not in only):
            continue
        try:
            with closing(open_db(path, readonly=True)) as conn:
                flagged = flagged_rows(conn)
        except Exception:
            logger.debug("%s: not readable", ticker, exc_info=True)
            continue
        if len(flagged):
            found.append((ticker, path, flagged))
    found.sort(key=lambda x: -len(x[2]))
    total = sum(len(f) for _, _, f in found)
    weekend = sum(int((f.index.dayofweek >= 5).sum()) for _, _, f in found)
    logger.info("%d tickers, %d resampled day rows (%d on weekends)", len(found), total, weekend)
    if not apply and not only:
        logger.info("Dry run without network. /tickers:... shows the provider plan, /apply writes.")
        return

    report = []
    for i, (ticker, path, flagged) in enumerate(found, 1):
        weekends = trades_weekends(ticker, quote_types)
        provider = provider_daily(ticker)
        replace, delete = plan(flagged, provider, weekends)
        kept = len(flagged) - len(replace) - len(delete)
        logger.info("[%d/%d] %s: %d flagged -> replace %d, delete %d, unchanged %d%s",
                    i, len(found), ticker, len(flagged), len(replace), len(delete), kept,
                    "" if len(provider) > MIN_PROVIDER_ROWS else " (no provider history)")
        error = ''
        if apply and (len(replace) or delete):
            try:
                apply_plan(path, backup_dir, replace, delete)
            except Exception as e:
                # One locked or broken file must not stop the run; rerunning
                # picks the ticker up again because its rows still match.
                logger.warning("%s: not written: %s", ticker, e)
                error = str(e)[:120]
        report.append(dict(ticker=ticker, flagged=len(flagged), replaced=len(replace),
                           deleted=len(delete), unchanged=kept, provider_rows=len(provider),
                           error=error))
        time.sleep(SLEEP_S)
    if apply and report:
        out = os.path.join(backup_dir, 'report.csv')
        pd.DataFrame(report).to_csv(out, index=False)
        logger.info("Report: %s", out)


if __name__ == '__main__':
    main()
