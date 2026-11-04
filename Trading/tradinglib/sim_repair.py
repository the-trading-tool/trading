"""Repair simulation rows that sit on a stale price scale (unadjusted splits).

The simulation DBs (asset_simulation_*.db) store each day's values as computed
that day and are never re-adjusted. After a split the price DB (yf_<T>.db,
day_data) is adjusted on reload, the stored rows are not: GOSS (1:80 reverse
split, 2026-09-10) kept closes of 0.16 next to new ones of 11.41, and a held
position came out at +636 % instead of -91 %. Some backfilled columns (sup, atc,
...) were recomputed from the adjusted prices later, so such rows can even be
mixed within themselves.

Three steps, so that the slow part never collides with the scheduler:

1. ``scan``    -- read only. Every stored close against the adjusted daily close
                  of the same day; rows off by a split-like factor are stale.
2. ``compute`` -- writes only into ``<db dir>/sim_repair/``. Per ticker a copy of
                  its price DB is aligned to one scale (``align_price_tables``),
                  then every affected (DB, year) is recomputed with asset_perf2's
                  ``process_symbol`` against that copy, plus the backfill-only
                  columns. Can run any time.
3. ``merge``   -- writes the production DBs, only in a free window (no
                  asset_perf2 running or starting soon: it loads a sim DB into
                  memory and writes it back whole, which would undo the merge).
                  Backs up what it replaces, aligns the production price DB the
                  same way, swaps the rows, re-ranks mtt_rs and checks again.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import logging
import math
import os
import re
import sqlite3

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SIM_TABLE = 'asset_simulation'
STAGE_DIR = 'sim_repair'
STALE_FACTOR = 1.8          # |log(stored / adjusted)| above log(1.8) = other scale
PRICE_TABLES = ('month_data', 'week_data', 'h60_data', 'min_data')
_OHLC = ('Open', 'High', 'Low', 'Close')
SPLIT_RATIOS = (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 16, 20, 25, 30, 35, 40, 50, 60, 75, 80, 100, 150, 200)
PERF_SCRIPT = 'asset_perf2.py'
MERGE_MARGIN_MIN = 30       # no merge this close to a scheduled asset_perf2 start
MIN_STALE_DAYS = 3          # intraday bars off on fewer days are a real move, not a scale


# ------------------------------------------------------------------ paths

def db_dir(db_path: str = 'database') -> str:
    if db_path and os.path.isabs(db_path) and os.path.isdir(db_path):
        return db_path
    from tradinglib import tools
    return os.path.dirname(tools.Tools().get_path(path=db_path, file_name='x.db'))


def stage_dir(db_path: str = 'database') -> str:
    d = os.path.join(db_dir(db_path), STAGE_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def sim_dbs(folder: str) -> list[str]:
    """Production simulation DB file names (no backups, no legacy names)."""
    out = []
    for p in sorted(glob.glob(os.path.join(folder, 'asset_simulation_*.db'))):
        name = os.path.basename(p)
        if re.fullmatch(r'asset_simulation_(\d{4}|all|etp)?\.db', name):
            out.append(name)
    return out


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30.0)


def current_year() -> int:
    return dt.date.today().year


# ------------------------------------------------------------------ scale helpers

def clean_ratio(r: float, tol: float = 0.08):
    """*r* rounded to a common split ratio (n or 1/n), None if it is none."""
    if not r or not np.isfinite(r) or r <= 0:
        return None
    cands = [float(n) for n in SPLIT_RATIOS] + [1.0 / n for n in SPLIT_RATIOS]
    best = min(cands, key=lambda c: abs(math.log(r / c)))
    return best if abs(r / best - 1.0) < tol else None


def daily_close(path: str) -> pd.Series:
    """Adjusted daily closes of a price DB (empty if unavailable), by normalized date."""
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    con = _ro(path)
    try:
        d = pd.read_sql_query('SELECT Date, Close FROM day_data WHERE Close > 0', con)
    except Exception:
        return pd.Series(dtype=float)
    finally:
        con.close()
    s = pd.to_numeric(d['Close'], errors='coerce')
    s.index = pd.DatetimeIndex(pd.to_datetime(d['Date'], errors='coerce')).normalize()
    s = s[s.index.notna() & (s > 0)].dropna()
    return s[~s.index.duplicated(keep='last')].sort_index()


def stale_rows(sim: pd.DataFrame, ref: pd.Series) -> pd.DataFrame:
    """Rows of *sim* (Date, close) whose close is on another scale than *ref*.

    Returns the stale rows with their factor ref/close (e.g. 80 for GOSS before
    its 1:80 reverse split). Days without a reference close are not judged.
    """
    if sim.empty or ref.empty:
        return sim.iloc[0:0].assign(factor=[])
    d = pd.to_datetime(sim['Date'], errors='coerce').dt.normalize()
    c = pd.to_numeric(sim['close'], errors='coerce')
    r = d.map(ref)
    f = r / c
    bad = f.notna() & (c > 0) & (np.abs(np.log(f.where(f > 0))) > math.log(STALE_FACTOR))
    return sim[bad].assign(factor=f[bad])


# ------------------------------------------------------------------ 1. scan

def scan(db_path: str = 'database', dbs: list[str] | None = None,
         tickers: list[str] | None = None, progress=None) -> pd.DataFrame:
    """Affected (db, ticker, year) with the number of stale rows. Read only.

    Columns: db, ticker, year, rows, stale, factor (median ref/close of the
    stale rows). Every year of a ticker in a DB with at least one stale row is
    listed: a year is recomputed as a whole.
    """
    folder = db_dir(db_path)
    refs: dict = {}
    out = []
    for name in (dbs or sim_dbs(folder)):
        con = _ro(os.path.join(folder, name))
        try:
            sql = f'SELECT ticker, Date, close FROM {SIM_TABLE}'
            params: list = []
            if tickers:
                sql += f" WHERE ticker IN ({','.join('?' * len(tickers))})"
                params = list(tickers)
            sim = pd.read_sql_query(sql, con, params=params)
        finally:
            con.close()
        if sim.empty:
            continue
        sim['close'] = pd.to_numeric(sim['close'], errors='coerce')
        n_bad = 0
        for tk, g in sim.groupby('ticker', sort=False):
            if tk not in refs:
                refs[tk] = daily_close(os.path.join(folder, f'yf_{tk}.db'))
            bad = stale_rows(g, refs[tk])
            if bad.empty:
                continue
            years_all = g['Date'].astype(str).str[:4]
            years_bad = set(bad['Date'].astype(str).str[:4])
            for y in sorted(years_bad):
                sel = years_all == y
                yb = bad[bad['Date'].astype(str).str[:4] == y]
                out.append({'db': name, 'ticker': tk, 'year': int(y), 'rows': int(sel.sum()),
                            'stale': len(yb), 'factor': float(yb['factor'].median())})
                n_bad += len(yb)
        if progress:
            progress(name, n_bad)
        logger.info('scan %s: %d stale rows', name, n_bad)
    return pd.DataFrame(out, columns=['db', 'ticker', 'year', 'rows', 'stale', 'factor'])


# ------------------------------------------------------------------ price DB alignment

def _period_ref(ref: pd.Series, dates: pd.Series, table: str) -> pd.Series:
    """Reference daily close for each bar of *table*: same day, or the period's last day."""
    d = pd.to_datetime(dates, errors='coerce')
    if table == 'month_data':
        end = d.dt.normalize() + pd.offsets.MonthEnd(0)
    elif table == 'week_data':
        end = d.dt.normalize() + pd.Timedelta(days=6)
    else:
        return d.dt.normalize().map(ref)
    vals = ref.reindex(ref.index.union(end.dropna().unique())).ffill()
    out = end.map(vals)
    # an unfinished period has no bar beyond today; asof keeps its last close
    return out


def align_price_tables(path: str, apply: bool = False) -> dict:
    """Bring month/week/h60/min bars of a price DB onto the scale of its day_data.

    Each bar is compared with the adjusted daily close of its day (or its
    period's last day). Bars off by a clean split ratio are multiplied by it,
    their volume divided. Idempotent: aligned bars are within the ratio 1 and
    stay untouched. ``apply=False`` only counts. Returns {table: (rows, factor)}.
    """
    out: dict = {}
    ref = daily_close(path)
    if ref.empty:
        return out
    con = sqlite3.connect(path, timeout=60.0)
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for tbl in PRICE_TABLES:
            if tbl not in have:
                continue
            cols = {r[1] for r in con.execute(f'PRAGMA table_info({tbl})')}
            price = [c for c in _OHLC if c in cols]
            if 'Close' not in price:
                continue
            bars = pd.read_sql_query(f'SELECT rowid AS rid, Date, Close FROM {tbl}', con)
            if bars.empty:
                continue
            rc = _period_ref(ref, bars['Date'], tbl)
            f = rc / pd.to_numeric(bars['Close'], errors='coerce')
            off = f.notna() & (f > 0) & (np.abs(np.log(f.where(f > 0))) > math.log(STALE_FACTOR))
            if not off.any():
                continue
            factor = clean_ratio(float(f[off].median()))
            if factor is None:
                logger.warning('align %s/%s: %d bars off, no clean ratio (median %.3f) -- left',
                               os.path.basename(path), tbl, int(off.sum()), float(f[off].median()))
                out[tbl] = (int(off.sum()), None)
                continue
            # a bar belongs to the stale scale when it is closer to factor than to 1
            lf = np.log(f.where(f > 0))
            stale = off & ((lf - math.log(factor)).abs() < lf.abs())
            # Intraday bars before the news of a crash day sit at the old price
            # level (GOSS 2026-02-23: x0.2 to the close). A stale scale spans
            # days; a single day's bars are a real move.
            days = pd.to_datetime(bars.loc[stale, 'Date'], errors='coerce').dt.normalize().nunique()
            if tbl in ('h60_data', 'min_data') and days < MIN_STALE_DAYS:
                continue
            rids = bars.loc[stale, 'rid'].astype(int).tolist()
            out[tbl] = (len(rids), factor)
            if apply and rids:
                sets = ', '.join(f'{c} = {c} * ?' for c in price)
                params = [factor] * len(price)
                if 'Volume' in cols:
                    sets += ', Volume = CAST(Volume / ? AS INTEGER)'
                    params.append(factor)
                con.executemany(f'UPDATE {tbl} SET {sets} WHERE rowid = ?',
                                [(*params, r) for r in rids])
        if apply:
            con.commit()
    finally:
        con.close()
    return out


def yahoo_splits(ticker: str) -> pd.Series:
    """Split history reported by Yahoo (value = new shares per old share, 0.0125 = 1:80)."""
    try:
        import yfinance as yf
        s = yf.Ticker(ticker).splits
        if s is None or not len(s):
            return pd.Series(dtype=float)
        s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
        return s[s > 0].astype(float)
    except Exception:
        logger.debug('splits for %s not available', ticker, exc_info=True)
        return pd.Series(dtype=float)


def split_seams(close: pd.Series, splits: pd.Series, since: str = '2015-01-01') -> list[dict]:
    """Reported splits the daily series still jumps by -- it is not adjusted there.

    On an adjusted series a split day moves like any other day. Unadjusted, the
    close jumps by 1/value (a 1:80 reverse split: x80). A real crash on another
    day is no seam.
    """
    out = []
    if close is None or close.empty or splits is None or splits.empty:
        return out
    for day, value in splits[splits.index >= pd.Timestamp(since)].items():
        if abs(math.log(value)) < math.log(1.5):
            continue
        after = close[close.index >= day]
        before = close[close.index < day]
        if not after.empty and not before.empty:
            move = float(after.iloc[0] / before.iloc[-1])
            if abs(math.log(move * value)) < math.log(1.25):
                out.append({'date': day, 'split': float(value), 'move': move})
                continue
        # Yahoo sometimes adjusts a split only back to some earlier date (seen:
        # 2026 reverse splits adjusted back to Jan 2 only), so the jump sits
        # there. Only for large ratios -- a -50 % day matching a 2:1 is no proof.
        if abs(math.log(value)) < math.log(3):
            continue
        win = close[(close.index >= day - pd.Timedelta(days=400)) & (close.index <= day)]
        moves = (win / win.shift(1)).dropna()
        hit = moves[(np.log(moves * value)).abs() < math.log(1.25)]
        if not hit.empty:
            out.append({'date': hit.index[-1], 'split': float(value), 'move': float(hit.iloc[-1])})
    return out


SPIKE_BARS = 10             # a jump undone within this many bars is a bad print
MAX_SPIKES = 3              # from this many on the series is rejected
SHIFT_UP = 3.0              # one-day rise above this that stays = unexplained level shift
VET_SINCE = '2018-01-01'    # the sim years plus the indicators' lookback


def price_glitches(close: pd.Series, since: str = VET_SINCE) -> list[str]:
    """Jumps in a daily series no real price path makes.

    A move of STALE_FACTOR or more that is undone within SPIKE_BARS bars is a
    bad print (or rows of two listings mixed). A one-day rise of SHIFT_UP or
    more that stays is a level shift, typically an unadjusted reverse split.
    Large falls that stay are left alone: those are real crashes.
    """
    c = close[(close.index >= pd.Timestamp(since)) & (close > 0)]
    lr = np.log(c.astype(float)).diff().dropna()
    if lr.empty:
        return []
    spikes, shifts = [], []
    vals = lr.to_numpy()
    undo = set()                            # the bars that took a spike back
    for i in np.flatnonzero(np.abs(vals) > math.log(STALE_FACTOR)):
        if i in undo:
            continue
        back = vals[i] + np.cumsum(vals[i + 1:i + 1 + SPIKE_BARS])
        hit = np.flatnonzero(np.abs(back) < math.log(1.5))
        if hit.size:
            spikes.append(lr.index[i])
            undo.add(i + 1 + int(hit[0]))
        elif vals[i] > math.log(SHIFT_UP):
            shifts.append((lr.index[i], math.exp(vals[i])))
    out = []
    # One or two are real squeezes or a single bad print the production DB has
    # too; more point at two listings mixed in one series.
    if len(spikes) >= MAX_SPIKES:
        out.append(f'{len(spikes)} spikes from {str(spikes[0])[:10]}')
    out += [f'level shift {str(d)[:10]} x{f:.3g}' for d, f in shifts]
    return out


def vet_copy(ticker: str, copy_path: str, refreshed_rows: int | None = None,
             splits: pd.Series | None = None) -> list[str]:
    """Reasons the reloaded price copy must not be used; empty = accepted.

    *refreshed_rows* None = not reloaded (nothing to judge); a reload that
    brought back less than half of the daily rows since VET_SINCE did not
    replace the history (Yahoo's 'max' itself stops after ~20 years).
    """
    if not os.path.exists(copy_path):
        return ['no staged price copy']
    close = daily_close(copy_path)
    out = []
    recent = int((close.index >= pd.Timestamp(VET_SINCE)).sum())
    if refreshed_rows is not None and refreshed_rows < 0.5 * recent:
        out.append(f'refresh incomplete ({refreshed_rows} rows, {recent} daily rows since {VET_SINCE[:4]})')
    seams = split_seams(close, yahoo_splits(ticker) if splits is None else splits)
    if seams:
        out.append('provider series not adjusted: ' + ', '.join(
            f"{str(s['date'])[:10]} x{1 / s['split']:g}" for s in seams))
    return out + price_glitches(close)


def refresh_price_copy(ticker: str, copy_path: str) -> dict:
    """Full daily and monthly history from the provider into the staged price DB copy.

    The rows the download covers are deleted first (older price DBs have no
    unique Date key, so INSERT OR REPLACE would leave the stale rows next to
    the new ones). Rows older than the download keep their values.
    """
    from tradinglib import ticker_tools as tt
    out = {'ticker': ticker}
    undo = _redirect({f'yf_{ticker}.db': copy_path})
    try:
        saver = tt.StockDataSaver(ticker)
        for interval, table in (('1d', 'day_data'), ('1mo', 'month_data')):
            data = saver.fetch_data_dl(interval, period='max', force_remote=True)
            if data is None or data.empty:
                out[table] = 0
                continue
            saver.create_table(interval)
            first = pd.Timestamp(data.index.min()).strftime('%Y-%m-%d')
            saver.cursor.execute(f'DELETE FROM {table} WHERE DATE(Date) >= ?', (first,))
            saver.save_to_db(data, interval)
            saver.conn.commit()
            out[table] = len(data)
        saver.conn.close()
    finally:
        undo()
    return out


def copy_db(src: str, dst: str) -> None:
    """Consistent copy of a (possibly live) SQLite DB via the backup API."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    s = _ro(src)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


# ------------------------------------------------------------------ 2. compute

def _stage_db(folder: str, name: str) -> str:
    return os.path.join(stage_dir(folder), f'stage_{name}')


def _ensure_stage(con: sqlite3.Connection, columns) -> None:
    con.execute(f'CREATE TABLE IF NOT EXISTS {SIM_TABLE} (ticker, Date)')
    have = {r[1] for r in con.execute(f'PRAGMA table_info({SIM_TABLE})')}
    for c in columns:
        if c not in have:
            con.execute(f'ALTER TABLE {SIM_TABLE} ADD COLUMN "{c}"')
            have.add(c)
    con.execute('CREATE TABLE IF NOT EXISTS repair_items (db, ticker, year, rows, '
                'computed_at, merged_at, PRIMARY KEY (db, ticker, year))')


def _redirect(mapping: dict):
    """Point the price-DB lookups of this process at the staged copies in *mapping*.

    Covers both paths process_symbol uses: tools.Tools.get_path (fetch_data) and
    four_ps._p (indicators that read the full daily history). Returns an undo.
    """
    from tradinglib import four_ps, tools
    orig_get, orig_p = tools.Tools.get_path, four_ps._p

    def get_path(self, path='database', file_name='', ignore_env=False):
        hit = mapping.get(os.path.basename(file_name or ''))
        return hit if hit else orig_get(self, path=path, file_name=file_name, ignore_env=ignore_env)

    def _p(name, db_path=None):
        return mapping.get(os.path.basename(name)) or orig_p(name, db_path)

    tools.Tools.get_path, four_ps._p = get_path, _p

    def undo():
        tools.Tools.get_path, four_ps._p = orig_get, orig_p
    return undo


def ticker_items(folder: str, ticker: str) -> list[tuple[str, int]]:
    """Every (sim DB, year) in which *ticker* has rows."""
    out = []
    for name in sim_dbs(folder):
        con = _ro(os.path.join(folder, name))
        try:
            years = [int(r[0]) for r in con.execute(
                f'SELECT DISTINCT substr(Date, 1, 4) FROM {SIM_TABLE} WHERE ticker = ?', (ticker,))
                if str(r[0]).isdigit()]
        finally:
            con.close()
        out += [(name, y) for y in sorted(years)]
    return out


def _prices_db(folder: str) -> sqlite3.Connection:
    con = sqlite3.connect(os.path.join(stage_dir(folder), 'prices.db'), timeout=60.0)
    con.execute('CREATE TABLE IF NOT EXISTS repair_prices (ticker PRIMARY KEY, day_rows, month_rows, '
                'aligned, seams, refreshed_at, merged_at)')
    return con


def _refreshed_rows(folder: str, ticker: str) -> int | None:
    pc = _prices_db(folder)
    try:
        row = pc.execute('SELECT day_rows FROM repair_prices WHERE ticker = ?', (ticker,)).fetchone()
    finally:
        pc.close()
    return None if not row or row[0] is None else int(row[0])


def _record_problems(folder: str, problems: dict[str, list[str]]) -> None:
    """Store rejections in the seams column: blocked_tickers() then skips them for a while."""
    pc = _prices_db(folder)
    try:
        for tk, p in problems.items():
            pc.execute('UPDATE repair_prices SET seams = ? WHERE ticker = ?', (json.dumps(p), tk))
        pc.commit()
    finally:
        pc.close()


def compute_ticker(ticker: str, items: list[tuple[str, int]] | None, folder: str,
                   refresh: bool = True) -> dict:
    """Repair one ticker in the staging area: price DB copy first, then its sim rows.

    Runs in a worker process. The ticker's price DB is copied, reloaded from the
    provider (*refresh*) and aligned; every read of it in this process goes to
    the copy. If the reloaded daily series still jumps on a reported split day
    (the provider does not adjust it), nothing is recomputed -- the sim rows
    would only inherit the seam. *items* None = every (DB, year) of the ticker:
    a reloaded price history changes years that matched the old one too.
    """
    import asset_perf2
    from tradinglib import fetch_data
    sd = stage_dir(folder)
    price_copy = os.path.join(sd, 'yf', f'yf_{ticker}.db')
    copy_db(os.path.join(folder, f'yf_{ticker}.db'), price_copy)
    res = {'ticker': ticker}
    if refresh:
        try:
            res['refresh'] = refresh_price_copy(ticker, price_copy)
        except Exception as e:
            logger.warning('refresh %s failed', ticker, exc_info=True)
            return {**res, 'error': f'refresh: {e}'}
    problems = vet_copy(ticker, price_copy, (res.get('refresh') or {}).get('day_data'))
    # A rejected copy is not aligned either: a ratio taken against a broken
    # daily series would rescale the other tables wrongly.
    aligned = {} if problems else align_price_tables(price_copy, apply=True)
    pc = _prices_db(folder)
    try:
        pc.execute('INSERT OR REPLACE INTO repair_prices VALUES (?, ?, ?, ?, ?, ?, NULL)',
                   (ticker, (res.get('refresh') or {}).get('day_data'),
                    (res.get('refresh') or {}).get('month_data'), json.dumps(aligned),
                    json.dumps(problems), dt.datetime.now().isoformat(timespec='seconds')))
        pc.commit()
    finally:
        pc.close()
    if problems:
        purge_staged(folder, ticker)
        return {**res, 'error': '; '.join(problems)}
    items = items if items is not None else ticker_items(folder, ticker)
    undo = _redirect({f'yf_{ticker}.db': price_copy})
    done = {}
    try:
        for name, year in items:
            cy = current_year()
            rows = asset_perf2.process_symbol(ticker, True, False,
                                              year='' if year == cy else int(year),
                                              init=(year == cy))
            rows = [r for r in (rows or []) if str(r.get('Date', ''))[:4] == str(year)]
            if not rows:
                done[(name, year)] = 0
                continue
            cols = list(dict.fromkeys(k for r in rows for k in r))
            # Build the rows in a private file first: the backfill takes a while, and
            # holding the shared stage DB meanwhile locks out the other workers.
            tmp = os.path.join(sd, 'tmp', f'{ticker}_{name}_{year}.db')
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            if os.path.exists(tmp):
                os.remove(tmp)
            t = sqlite3.connect(tmp)
            try:
                _ensure_stage(t, cols)
                ph = ', '.join('?' * len(cols))
                qc = ', '.join(f'"{c}"' for c in cols)
                t.executemany(f'INSERT INTO {SIM_TABLE} ({qc}) VALUES ({ph})',
                              [tuple(r.get(c) for c in cols) for r in rows])
                # Columns only /backfill writes (ewo_diff, macd_diff, scr_*, ...).
                ft = fetch_data.FetchData(indicators=[])
                asset_perf2._backfill_symbol(ticker, list(BACKFILL_ONLY), t, SIM_TABLE,
                                             folder, ft)
                t.commit()
                all_cols = [r[1] for r in t.execute(f'PRAGMA table_info({SIM_TABLE})')]
            finally:
                t.close()
            con = sqlite3.connect(_stage_db(folder, name), timeout=600.0)
            try:
                con.isolation_level = None
                con.execute('ATTACH DATABASE ? AS t', (tmp,))      # not allowed inside a transaction
                con.execute('BEGIN IMMEDIATE')
                _ensure_stage(con, all_cols)
                con.execute(f"DELETE FROM {SIM_TABLE} WHERE ticker = ? AND substr(Date, 1, 4) = ?",
                            (ticker, str(year)))
                qc = ', '.join(f'"{c}"' for c in all_cols)
                con.execute(f'INSERT INTO main.{SIM_TABLE} ({qc}) SELECT {qc} FROM t.{SIM_TABLE}')
                con.execute('INSERT OR REPLACE INTO repair_items VALUES (?, ?, ?, ?, ?, NULL)',
                            (name, ticker, int(year), len(rows),
                             dt.datetime.now().isoformat(timespec='seconds')))
                con.execute('COMMIT')
                con.execute('DETACH DATABASE t')
            except Exception:
                if con.in_transaction:
                    con.execute('ROLLBACK')
                raise
            finally:
                con.close()
            os.remove(tmp)
            done[(name, year)] = len(rows)
    finally:
        undo()
    return {**res, 'aligned': aligned, 'rows': {f'{k[0]}:{k[1]}': v for k, v in done.items()}}


# Backfill indicators whose columns process_symbol does not write.
BACKFILL_ONLY = ('ewo', 'macd', 'scr')


def compute(plan: pd.DataFrame, db_path: str = 'database', workers: int = 2,
            progress=None, refresh: bool = True) -> list[dict]:
    """Repair every ticker of *plan* in the staging area (all its DBs and years)."""
    import concurrent.futures
    folder = db_dir(db_path)
    tickers = list(dict.fromkeys(plan['ticker'].astype(str)))
    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(compute_ticker, tk, None, folder, refresh): tk for tk in tickers}
        for fut in concurrent.futures.as_completed(futs):
            tk = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                logger.exception('compute %s failed', tk)
                res = {'ticker': tk, 'error': str(e)}
            results.append(res)
            if progress:
                progress(res)
    return results


def blocked_tickers(db_path: str = 'database', days: int = 7) -> set[str]:
    """Tickers whose reloaded series still had a seam within *days* -- not retried daily."""
    folder = db_dir(db_path)
    p = os.path.join(stage_dir(folder), 'prices.db')
    if not os.path.exists(p):
        return set()
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec='seconds')
    con = _ro(p)
    try:
        rows = con.execute("SELECT ticker FROM repair_prices WHERE seams NOT IN ('[]', '') "
                           'AND refreshed_at >= ?', (since,)).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    return {r[0] for r in rows}


def staged(db_path: str = 'database') -> pd.DataFrame:
    """Computed but not yet merged items of all stage DBs."""
    folder = db_dir(db_path)
    frames = []
    for p in glob.glob(os.path.join(stage_dir(folder), 'stage_*.db')):
        con = _ro(p)
        try:
            frames.append(pd.read_sql_query(
                'SELECT * FROM repair_items WHERE merged_at IS NULL', con))
        except Exception:
            pass
        finally:
            con.close()
    if not frames:
        return pd.DataFrame(columns=['db', 'ticker', 'year', 'rows', 'computed_at', 'merged_at'])
    return pd.concat(frames, ignore_index=True)


def purge_staged(folder: str, ticker: str) -> int:
    """Drop the not yet merged staged rows of *ticker* (its copy was rejected)."""
    n = 0
    for p in glob.glob(os.path.join(stage_dir(folder), 'stage_*.db')):
        con = sqlite3.connect(p, timeout=600.0)
        try:
            items = con.execute('SELECT year FROM repair_items WHERE ticker = ? AND merged_at IS NULL',
                                (ticker,)).fetchall()
            for (year,) in items:
                n += con.execute(f'DELETE FROM {SIM_TABLE} WHERE ticker = ? AND substr(Date, 1, 4) = ?',
                                 (ticker, str(year))).rowcount
            con.execute('DELETE FROM repair_items WHERE ticker = ? AND merged_at IS NULL', (ticker,))
            con.commit()
        except sqlite3.OperationalError:            # stage DB without the tables yet
            pass
        finally:
            con.close()
    return n


# ------------------------------------------------------------------ 3. merge

def _perf_jobs(folder: str) -> list[tuple[str, list[str]]]:
    """(HH:MM, weekdays) of the scheduler jobs that run asset_perf2."""
    p = os.path.join(folder, 'scheduler.db')
    if not os.path.exists(p):
        return []
    con = _ro(p)
    try:
        rows = con.execute('SELECT time, frequency, command, enabled FROM jobs').fetchall()
    finally:
        con.close()
    out = []
    for t, freq, cmd, enabled in rows:
        if not enabled or PERF_SCRIPT not in str(cmd or '') or not re.fullmatch(r'\d{1,2}:\d{2}', str(t or '')):
            continue
        days = [d.strip().lower() for d in str(freq or '').split(',') if d.strip()]
        out.append((str(t), days))
    return out


def running_perf() -> list[int]:
    """PIDs of running asset_perf2 processes.

    Only an argument that IS the script counts: other command lines can merely
    mention it (an editor or agent started with the project notes as argument).
    """
    import psutil
    pids = []
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            if any(os.path.basename(str(a).strip('"')).lower() == PERF_SCRIPT
                   for a in (proc.info.get('cmdline') or [])):
                pids.append(proc.info['pid'])
        except Exception:
            continue
    return pids


def merge_window(db_path: str = 'database', now: dt.datetime | None = None,
                 margin_min: int = MERGE_MARGIN_MIN) -> tuple[bool, str]:
    """(ok, reason): is it safe to write the simulation DBs now?"""
    folder = db_dir(db_path)
    pids = running_perf()
    if pids:
        return False, f'asset_perf2 running (pid {", ".join(map(str, pids))})'
    now = now or dt.datetime.now()
    for t, days in _perf_jobs(folder):
        h, m = (int(x) for x in t.split(':'))
        for add in (0, 1):
            start = (now + dt.timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
            if days and start.strftime('%A').lower() not in days:
                continue
            if now <= start <= now + dt.timedelta(minutes=margin_min):
                return False, f'asset_perf2 starts at {t}'
    return True, ''


def transfer_prices(folder: str, ticker: str) -> dict:
    """Daily/monthly rows of the staged (reloaded) price copy into the production DB.

    Production rows within the copy's date range are replaced; rows the download
    jobs added after the copy was made stay. Only for a refreshed, not yet
    transferred copy.
    """
    pc = _prices_db(folder)
    try:
        row = pc.execute('SELECT refreshed_at, merged_at, seams FROM repair_prices WHERE ticker = ?',
                         (ticker,)).fetchone()
        if not row or row[1] or json.loads(row[2] or '[]'):
            return {}
        copy = os.path.join(stage_dir(folder), 'yf', f'yf_{ticker}.db')
        prod = os.path.join(folder, f'yf_{ticker}.db')
        out = {}
        con = sqlite3.connect(prod, timeout=120.0)
        con.isolation_level = None
        try:
            con.execute('ATTACH DATABASE ? AS cp', (copy,))
            have = {r[0] for r in con.execute("SELECT name FROM cp.sqlite_master WHERE type='table'")}
            con.execute('BEGIN')
            for tbl in ('day_data', 'month_data'):
                if tbl not in have:
                    continue
                lo, hi = con.execute(f'SELECT MIN(Date), MAX(Date) FROM cp.{tbl}').fetchone()
                if lo is None:
                    continue
                con.execute(f'CREATE TABLE IF NOT EXISTS main.{tbl} (Date TEXT PRIMARY KEY, Open REAL, '
                            f'High REAL, Low REAL, Close REAL, Volume INTEGER)')
                con.execute(f'DELETE FROM main.{tbl} WHERE Date >= ? AND Date <= ?', (lo, hi))
                cur = con.execute(f'INSERT INTO main.{tbl} (Date, Open, High, Low, Close, Volume) '
                                  f'SELECT Date, Open, High, Low, Close, Volume FROM cp.{tbl}')
                out[tbl] = cur.rowcount
            con.execute('COMMIT')
        except Exception:
            try:
                con.execute('ROLLBACK')
            except Exception:
                pass
            raise
        finally:
            con.close()
        pc.execute('UPDATE repair_prices SET merged_at = ? WHERE ticker = ?',
                   (dt.datetime.now().isoformat(timespec='seconds'), ticker))
        pc.commit()
        return out
    finally:
        pc.close()


def merge(db_path: str = 'database', force: bool = False, dry: bool = False) -> dict:
    """Write the staged rows into the production DBs (see module doc)."""
    folder = db_dir(db_path)
    ok, why = merge_window(folder)
    if not ok and not force:
        return {'merged': False, 'reason': why}
    todo = staged(folder)
    if todo.empty:
        return {'merged': False, 'reason': 'nothing staged'}
    # Second look at every copy before it reaches production (also covers
    # rows staged before the acceptance check existed).
    rejected = {}
    for tk in sorted(todo['ticker'].unique()):
        problems = vet_copy(tk, os.path.join(stage_dir(folder), 'yf', f'yf_{tk}.db'),
                            _refreshed_rows(folder, tk))
        if problems:
            rejected[tk] = problems
    if rejected and not dry:
        for tk in rejected:
            purge_staged(folder, tk)
        _record_problems(folder, rejected)
    todo = todo[~todo['ticker'].isin(rejected)]
    ts = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    bdir = os.path.join(stage_dir(folder), 'backup', ts)
    report = {'merged': not dry, 'backup': bdir, 'dbs': {}, 'prices': {}, 'rejected': rejected}
    if todo.empty:
        return {**report, 'merged': False, 'reason': 'all staged tickers rejected'}
    if dry:
        report['plan'] = todo.groupby('db').size().to_dict()
        return report
    os.makedirs(bdir, exist_ok=True)

    # Price DBs first: back up the file, take over the reloaded daily/monthly
    # rows of the staged copy, then align the other tables (idempotent).
    for tk in sorted(todo['ticker'].unique()):
        p = os.path.join(folder, f'yf_{tk}.db')
        if os.path.exists(p):
            copy_db(p, os.path.join(bdir, f'yf_{tk}.db'))
            report['prices'][tk] = {'taken': transfer_prices(folder, tk),
                                    'aligned': align_price_tables(p, apply=True)}

    from tradinglib import minervini
    for name, items in todo.groupby('db'):
        prod = os.path.join(folder, name)
        stage = _stage_db(folder, name)
        tickers = sorted(items['ticker'].unique())
        con = sqlite3.connect(prod, timeout=120.0)
        con.isolation_level = None              # explicit BEGIN/COMMIT below
        try:
            con.execute("ATTACH DATABASE ? AS stg", (stage,))
            con.execute("ATTACH DATABASE ? AS bak", (os.path.join(bdir, f'rows_{name}'),))
            ph = ','.join('?' * len(tickers))
            con.execute(f'CREATE TABLE bak.{SIM_TABLE} AS SELECT * FROM main.{SIM_TABLE} '
                        f'WHERE ticker IN ({ph})', tickers)
            prod_cols = [r[1] for r in con.execute(f'PRAGMA main.table_info({SIM_TABLE})')]
            stg_cols = [r[1] for r in con.execute(f'PRAGMA stg.table_info({SIM_TABLE})')]
            for c in stg_cols:                     # a new indicator column
                if c not in prod_cols:
                    con.execute(f'ALTER TABLE main.{SIM_TABLE} ADD COLUMN "{c}" REAL')
                    prod_cols.append(c)
            cols = [c for c in stg_cols if c in prod_cols]
            qc = ', '.join(f'"{c}"' for c in cols)
            n = 0
            con.execute('BEGIN')
            for r in items.itertuples():
                cond = 'ticker = ? AND substr(Date, 1, 4) = ?'
                args = (r.ticker, str(int(r.year)))
                # Replace exactly the staged days production has: days the
                # nightly run added after the computation stay, and days
                # production never had (e.g. ETPs with a few stray rows in the
                # main DB) are not added -- this is a repair, not a fill.
                con.execute(f'DELETE FROM main.{SIM_TABLE} WHERE {cond} AND Date IN '
                            f'(SELECT Date FROM stg.{SIM_TABLE} WHERE {cond})', args + args)
                cur = con.execute(f'INSERT INTO main.{SIM_TABLE} ({qc}) SELECT {qc} '
                                  f'FROM stg.{SIM_TABLE} WHERE {cond} AND Date IN '
                                  f'(SELECT Date FROM bak.{SIM_TABLE} WHERE {cond})', args + args)
                n += cur.rowcount
                con.execute('UPDATE stg.repair_items SET merged_at = ? WHERE db = ? AND ticker = ? '
                            'AND year = ?', (ts, name, r.ticker, int(r.year)))
            con.execute('COMMIT')
            missing = [c for c in prod_cols if c not in stg_cols]
        except Exception:
            try:
                con.execute('ROLLBACK')
            except Exception:
                pass
            raise
        finally:
            con.close()
        since = f"{int(items['year'].min())}-01-01"
        try:
            rs = minervini.update_rs(prod, since=since)
        except Exception:
            logger.warning('mtt_rs update failed for %s', name, exc_info=True)
            rs = None
        after = scan(folder, dbs=[name], tickers=tickers)
        report['dbs'][name] = {'rows': n, 'tickers': len(tickers), 'not_recomputed': missing,
                               'mtt_rs_rows': rs, 'stale_after': int(after['stale'].sum())}
        logger.info('merge %s: %d rows, %d tickers, stale after: %d', name, n, len(tickers),
                    int(after['stale'].sum()))
    with open(os.path.join(bdir, 'report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, indent=1, default=str)
    return report
