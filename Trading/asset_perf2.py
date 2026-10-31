import concurrent.futures
import pandas as pd
from datetime import datetime, timedelta
import glob
import math
import shutil
import time
import sqlite3
import json
import warnings
import sys
import os
warnings.filterwarnings("ignore")

from tradinglib.indicator import indicator
from tradinglib import (fetch_data, ticker_tools as tt,
                        system_config as sysconf)
from tradinglib.tools import open_db, NON_STOCK_GROUPS
try:
    from tradinglib.premium import multi_transaction as mt
except ImportError:
    mt = None
from tradinglib.tools import Tools as _Tools
import logging
from tradinglib.utils import DataUtils
from tradinglib import cli, logging_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Indicator backfill map  — maps indicator short-name → DB columns it produces
# ---------------------------------------------------------------------------
INDICATOR_BACKFILL_MAP: dict = {
    'heikin':  ['ha_close', 'ha_open', 'ha_ema_high', 'ha_ema_low'],
    'markov':  ['markov_regime'],
    'macd':    ['macd', 'macd_diff', 'macd_signal', 'macd_trend'],
    'rsi':     ['rsi', 'rsi_ema', 'momentum'],
    'ewo':     ['ewo', 'ewo_ema', 'ewo_diff', 'ewo_angle', 'ewo_trend'],
    'adx':     ['adx', 'adx_plus', 'adx_minus', 'adx_angle'],
    'dema':    ['dema_ema_fast', 'dema_ema_slow', 'dema_buy', 'dema_sell'],
    'hor':     ['hor_val', 'hor_threshold'],
    'sup':     ['sup_support', 'sup_resistance'],
    'relvol':  ['relvol_ratio', 'relvol_direction'],
    # atc_mid_zero ist die Mittellinie des Null-Steigungs-Kanals -- das Niveau,
    # um das der Kurs pendelt, wenn man den Trend herausrechnet. Als Spalte hier,
    # damit Buy/Sell-Formeln sie auch im Backtest und in der Kandidatensuche
    # verwenden koennen; live lag sie ohnehin schon vor.
    # atc_mid_high: middle of the channel anchored at the highest high — used
    # by sell formulas like (Low > atc_mid_high); without it here such a
    # formula ran in the chart but never in the backtest.
    'atc':     ['atc_top_high', 'atc_bot_low', 'atc_mid_zero', 'atc_mid_high'],
    # 4 Phase Sequence — phases, base levels and signals (tradinglib/four_ps.py).
    # The indicator reloads the full local daily history itself, so /backfill:fps
    # yields the same values as a full init run.
    'fps':     ['fps_phase', 'fps_best_trend', 'fps_trend_gain', 'fps_base_high',
                'fps_base_low', 'fps_base_weeks', 'fps_breakout', 'fps_buy',
                'fps_sell', 'fps_stop', 'fps_target', 'fps_rs', 'fps_dist_high',
                'fps_setup'],
    'scr':     ['scr_1mo', 'scr_3mo', 'scr_eoy', 'scr_buy', 'scr_sell'],
    # Calibrated profile scores (tradinglib/risk_profile.py). The indicator
    # reloads the full local daily history itself — ATR and the record high are
    # both path-dependent — so /backfill:prof yields the same values a full init
    # run writes, and the live chart shows the same numbers again.
    'prof':    ['riskScore', 'riskBucket', 'trendScore'],
    # Minervini Trend Template (tradinglib/minervini.py), per ticker on the full
    # local daily history. The RS rating mtt_rs is cross-sectional and not in
    # this list: minervini.update_rs ranks mtt_rs_raw after each run/backfill.
    'mtt':     ['mtt_sma150', 'mtt_slope200', 'mtt_high52', 'mtt_low52',
                'mtt_rs_raw', 'mtt_score', 'mtt_pass'],
    # Range Filtered Trend (tradinglib/indicator/rft.py, AlgoAlpha port). On
    # daily frames the indicator puts local history in front as warm-up, so a
    # 2-year run, a 10-year backfill and the chart agree.
    'rft':     ['rft_kalman', 'rft_supertrend', 'rft_upper', 'rft_lower',
                'rft_trend', 'rft_ktrend', 'rft_state', 'rft_signal'],
    # Raw OHLC values copied straight from local OHLCV (no indicator to run).
    # Lets `/backfill:ohlc` populate Open (new) and refresh High/Low on existing rows.
    'ohlc':    ['Open', 'High', 'Low'],
    # Weekly trend, causal: week-to-date close vs. the last COMPLETED week's close
    # (indicator.trend_pct_columns, also used by the chart). The old value came
    # from the last weekly bar up to the day -- dated Monday, closing Friday --
    # so backfilled rows saw the rest of their week.
    'trend':   ['wkTrend'],
}

# Map entries that are not indicator modules: their columns are built below.
NON_INDICATOR_BACKFILL = {'ohlc', 'trend'}

# Indicators that are additionally computed on higher timeframes during
# backfill. Each entry: (interval, period, column_suffix). The indicator runs
# on the wk/mo OHLCV and its columns from INDICATOR_BACKFILL_MAP are
# forward-filled onto the daily index (a wk/mo value holds until the next
# wk/mo bar closes) and stored as <col><suffix>, e.g. markov_regime_wk.
INDICATOR_BACKFILL_TF: dict = {
    'markov': [('1wk', '6y', '_wk'), ('1mo', '10y', '_mo')],
}

# Higher-timeframe columns derived causally from the DAILY frame (as of each day,
# in-progress period included up to that day) instead of forward-filling wk/mo
# bars, which would leak the rest of the week/month into earlier days.
# indicator -> [(pandas period freq, suffix)]
INDICATOR_BACKFILL_FROM_DAILY: dict = {
    'sup': [('W', '_wk'), ('M', '_mo')],
}


def sup_htf_series(daily: pd.DataFrame) -> dict:
    """{'sup_support_wk': Series, ...} as of each daily bar (causal), see
    INDICATOR_BACKFILL_FROM_DAILY. Empty dict when High/Low are missing."""
    out = {}
    if daily is None or daily.empty or not {'High', 'Low'} <= set(daily.columns):
        return out
    from tradinglib.indicator.indicator import support_resistance_htf
    for freq, suffix in INDICATOR_BACKFILL_FROM_DAILY['sup']:
        try:
            out[f'sup_support{suffix}'], out[f'sup_resistance{suffix}'] = \
                support_resistance_htf(daily, freq)
        except Exception as e:
            logger.warning("sup%s: %s", suffix, e)
    return out


def wk_trend_series(daily: pd.DataFrame) -> pd.Series:
    """wkTrend as of each daily bar (causal): close vs. last completed week's close.

    Same definition the chart uses (indicator.trend_pct_columns) and the value a
    single-day run always produced, when the running weekly bar was still open.
    """
    from tradinglib.indicator.indicator import trend_pct_columns
    if daily is None or daily.empty or 'Close' not in daily.columns:
        return pd.Series(dtype=float)
    return trend_pct_columns(daily[['Close']])['wkTrend']


def _ensure_sim_columns(conn, table: str, columns: list[str]) -> None:
    """Add any missing REAL columns to *table* (ALTER TABLE … ADD COLUMN)."""
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col in columns:
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL DEFAULT 0")
    conn.commit()


def _backfill_symbol(symbol: str, indicator_names: list[str],
                     sim_conn, table: str, ohlcv_dir: str,
                     ft) -> int:
    """Recompute *indicator_names* for *symbol* from local OHLCV and UPDATE sim-DB.

    Returns the number of rows updated.
    Only uses local yf_<symbol>.db — never calls Yahoo Finance.
    """
    # 1. Load local OHLCV only — skip Yahoo fallback
    try:
        df_raw = ft.load_price_data(symbol, '10y', '1d')
    except Exception as e:
        logger.warning("backfill %s: load_price_data failed: %s", symbol, e)
        return 0

    if df_raw is None or df_raw.empty:
        logger.debug("backfill %s: no local OHLCV data, skipping", symbol)
        return 0

    df_raw = DataUtils.ensure_datetime_index(df_raw)

    # 2. Run each requested indicator on the full df
    for ind_name in indicator_names:
        if ind_name in NON_INDICATOR_BACKFILL:
            continue
        try:
            ft.init_instance(ind_name, df=df_raw, symbol=symbol)
            inst = getattr(ft, ind_name, None)
            if inst is not None and hasattr(inst, 'df'):
                new_cols = [c for c in inst.df.columns if c not in df_raw.columns]
                df_raw = pd.concat([df_raw, inst.df[new_cols]], axis=1)
        except Exception as e:
            logger.warning("backfill %s / %s: indicator error: %s", symbol, ind_name, e)

    # 2b. Higher-timeframe variants (INDICATOR_BACKFILL_TF) — run the indicator
    #     on local wk/mo OHLCV and forward-fill onto the daily index
    for ind_name in indicator_names:
        for interval, period, suffix in INDICATOR_BACKFILL_TF.get(ind_name, []):
            try:
                df_tf = ft.load_price_data(symbol, period, interval)
                if df_tf is None or df_tf.empty:
                    continue
                df_tf = DataUtils.ensure_datetime_index(df_tf)
                ft.init_instance(ind_name, df=df_tf, symbol=symbol)
                inst = getattr(ft, ind_name, None)
                if inst is None or not hasattr(inst, 'df'):
                    continue
                for base_col in INDICATOR_BACKFILL_MAP.get(ind_name, []):
                    if base_col in inst.df.columns:
                        s = inst.df[base_col].sort_index()
                        df_raw[f'{base_col}{suffix}'] = s.reindex(
                            df_raw.index, method='ffill')
            except Exception as e:
                logger.warning("backfill %s / %s%s: indicator error: %s",
                               symbol, ind_name, suffix, e)

    # 2c. Higher-timeframe variants derived from the daily frame (causal)
    if 'sup' in indicator_names:
        for col, series in sup_htf_series(df_raw).items():
            df_raw[col] = series
    if 'trend' in indicator_names:
        df_raw['wkTrend'] = wk_trend_series(df_raw)

    # 3. Collect the DB columns we need to update
    target_cols: list[str] = []
    for ind_name in indicator_names:
        base_cols = INDICATOR_BACKFILL_MAP.get(ind_name, [])
        target_cols.extend(base_cols)
        for _, _, suffix in INDICATOR_BACKFILL_TF.get(ind_name, []):
            target_cols.extend(f'{c}{suffix}' for c in base_cols)
        for _, suffix in INDICATOR_BACKFILL_FROM_DAILY.get(ind_name, []):
            target_cols.extend(f'{c}{suffix}' for c in base_cols)
    target_cols = [c for c in target_cols if c in df_raw.columns]
    if not target_cols:
        logger.debug("backfill %s: no matching columns found in df", symbol)
        return 0

    _ensure_sim_columns(sim_conn, table, target_cols)

    # 4. Normalise index to "YYYY-MM-DD 00:00:00" strings for JOIN with sim-DB
    try:
        idx_norm = pd.to_datetime(df_raw.index, errors='coerce').normalize()
        df_raw = df_raw.copy()
        df_raw.index = idx_norm.strftime('%Y-%m-%d 00:00:00')
    except Exception as e:
        logger.warning("backfill %s: index normalisation failed: %s", symbol, e)
        return 0

    # 5. Batch UPDATE
    set_clause = ', '.join(f"{c} = ?" for c in target_cols)
    sql = (f"UPDATE {table} SET {set_clause} "
           f"WHERE ticker = ? AND Date = ?")
    rows_updated = 0
    batch = []
    for date_str, row in df_raw.iterrows():
        vals = tuple(float(row[c]) if not pd.isna(row[c]) else 0.0
                     for c in target_cols)
        batch.append(vals + (symbol, date_str))
    try:
        before = sim_conn.total_changes
        sim_conn.executemany(sql, batch)
        rows_updated = sim_conn.total_changes - before
        sim_conn.commit()
    except Exception as e:
        logger.error("backfill %s: DB update failed: %s", symbol, e)
    return rows_updated


def backfill_db(sim_db_path: str, ohlcv_dir: str, indicators_list: list[str],
                force: bool = False) -> None:
    """Backfill indicator columns for every ticker in *sim_db_path*.

    Uses local yf_<TICKER>.db files — no internet required.
    force=True skips the already-filled check and re-computes every ticker
    (useful when all rows contain the ALTER TABLE default of 0).
    """
    from tradinglib import fetch_data as fd

    # FetchData with ONLY the requested indicators (no scoring, no buy/sell)
    ft = fd.FetchData(database_path=ohlcv_dir,
                      indicators=indicators_list,
                      buy_query='', sell_query='')

    table = 'asset_simulation'
    sim_conn = open_db(sim_db_path)

    # Ensure a composite index on (ticker, Date) — crucial for UPDATE speed.
    # Without it every UPDATE does a full table scan (~0.45s per row on 400k rows).
    sim_conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table}_ticker_date "
        f"ON {table} (ticker, Date)"
    )
    sim_conn.commit()
    logger.info("backfill: index on (ticker, Date) ready")

    try:
        tickers = [r[0] for r in
                   sim_conn.execute(f"SELECT DISTINCT ticker FROM {table}").fetchall()]
        logger.info("backfill: %d tickers in %s", len(tickers), sim_db_path)

        # Collect all DB columns we will write so we can check for existing data
        all_target_cols: list[str] = []
        for ind in indicators_list:
            all_target_cols.extend(INDICATOR_BACKFILL_MAP.get(ind, []))

        # Determine which tickers already have all target columns filled.
        # Use IS NOT NULL — 0 is a valid regime value (Sideways), so != 0 would
        # wrongly re-process fully-filled Sideways tickers on the next run.
        # force=True bypasses the check entirely (re-computes everything).
        skip_set: set[str] = set()
        if all_target_cols and not force:
            _ensure_sim_columns(sim_conn, table, all_target_cols)
            check_col = all_target_cols[0]   # representative column
            try:
                filled = {r[0] for r in sim_conn.execute(
                    f"SELECT DISTINCT ticker FROM {table} "
                    f"WHERE {check_col} IS NOT NULL"
                ).fetchall()}
                skip_set = filled
                logger.info("backfill: %d tickers already have %s — skipping",
                            len(skip_set), check_col)
            except Exception as e:
                logger.warning("backfill: could not determine filled tickers: %s", e)
        elif force:
            logger.info("backfill: force mode — skipping already-filled check")

        skipped = 0
        for i, sym in enumerate(tickers):
            if sym in skip_set:
                skipped += 1
                continue
            n = _backfill_symbol(sym, indicators_list, sim_conn, table,
                                 ohlcv_dir, ft)
            logger.info("[%d/%d] backfill %s: %d rows updated",
                        i + 1, len(tickers), sym, n)
        if skipped:
            logger.info("backfill: skipped %d already-filled tickers", skipped)
    finally:
        sim_conn.close()


def collect_missing_dates(sim_db_path: str, tickers: list[str],
                          start: str, end: str,
                          table: str = 'asset_simulation') -> dict:
    """Map each ticker to the days it is missing in the simulation DB.

    A day counts as missing when the local daily OHLC series has it inside
    [start, end] but the simulation table does not. Dates are compared as
    'YYYY-MM-DD'.

    Comparing the dates themselves rather than the row counts is what catches
    interior holes — a day the nightly run skipped sits in the middle of an
    otherwise complete series, and a count comparison hides it as soon as one
    extra row exists elsewhere.
    """
    missing: dict = {}
    if not os.path.exists(sim_db_path):
        logger.warning("fill: %s does not exist — nothing to compare against",
                       sim_db_path)
        return missing

    sim_conn = open_db(sim_db_path, readonly=True)
    try:
        have: dict = {}
        for tk, day in sim_conn.execute(
                f"SELECT ticker, Date FROM {table} WHERE Date BETWEEN ? AND ?",
                (start, end)):
            have.setdefault(tk, set()).add(str(day)[:10])
    finally:
        sim_conn.close()

    tools = _Tools()
    for tk in tickers:
        path = tools.get_path(path='database', file_name=f'yf_{tk}.db')
        if not os.path.exists(path):
            continue  # no local price series — nothing could be computed anyway
        try:
            conn = open_db(path, readonly=True)
            try:
                days = {str(r[0])[:10] for r in conn.execute(
                    "SELECT Date FROM day_data WHERE Date BETWEEN ? AND ?",
                    (start, end))}
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.debug("fill: %s unreadable (%s)", tk, e)
            continue
        gap = days - have.get(tk, set())
        if gap:
            missing[tk] = gap
    return missing


# Groups whose /all run goes to asset_simulation_etp.db. ETF is a subset of ETP
# (all 9 members sit in both), so /group:ETP,ETF means the same run. Before,
# only exactly ['ETP'] counted: the scheduler's "/all /group:ETP,ETF" silently
# fell through to "all tickers except ETP" -- a second full _all.db run every
# night, while the ETP simulation stopped being updated.
ETP_GROUPS = frozenset({'ETP', 'ETF'})


def is_etp_run(group) -> bool:
    """True when an /all run with these groups is the ETP/ETF run."""
    return bool(group) and set(group) <= ETP_GROUPS


def etp_ticker_query(group) -> str:
    """Members of the ETP/ETF groups named in *group* (uppercased names)."""
    names = '","'.join(sorted(set(group)))
    return ('SELECT s.Ticker FROM stocks s '
            'JOIN stock_indices si ON s.id = si.stock_id '
            'JOIN indices i ON si.index_id = i.id '
            f'WHERE UPPER(i.name) IN ("{names}")')


def _fmt_size(num_bytes: float) -> str:
    """Format a byte count for log output."""
    for unit in ('B', 'KB', 'MB'):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.2f} GB"


def _db_size(db_path: str) -> int:
    """Bytes occupied by a SQLite database including its WAL sidecar files."""
    total = 0
    for suffix in ('', '-wal', '-shm'):
        try:
            total += os.path.getsize(db_path + suffix)
        except OSError:
            pass  # sidecar missing = nothing to count
    return total


def vacuum_db(db_path: str) -> dict:
    """Rebuild *db_path* with VACUUM and report how much space came back.

    SQLite never hands deleted pages back to the filesystem — it keeps them on
    an internal free list and reuses them for later inserts. After a large
    delete (dropping a ticker group, say) the file therefore stays as big as it
    ever was. VACUUM writes a fresh, compact copy and swaps it in.

    Two constraints the caller has to live with:
      * It needs exclusive access. Any other open connection — a running
        Streamlit session, a scheduler job — makes it fail with
        "database is locked". Nothing is changed in that case.
      * It needs room for a second copy of the file while it runs.

    Returns a dict with before/after sizes and an 'error' key when skipped.
    """
    result = {'path': db_path, 'before': 0, 'after': 0, 'error': None}
    if not os.path.exists(db_path):
        result['error'] = 'not found'
        return result

    before = _db_size(db_path)
    result['before'] = before

    # VACUUM builds the compacted copy before replacing the original, so the
    # peak requirement is roughly twice the current size.
    try:
        free = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path))).free
        if free < before * 1.1:
            result['error'] = (f"not enough free space ({_fmt_size(free)} left, "
                               f"~{_fmt_size(before)} needed)")
            return result
    except OSError:
        pass  # cannot determine free space — let SQLite decide

    # Deliberately NOT open_db(): that sets temp_store=MEMORY, which would make
    # VACUUM build the whole copy in RAM. isolation_level=None keeps sqlite3
    # from wrapping the statement in a transaction (VACUUM cannot run in one).
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    was_wal = False
    try:
        conn.execute("PRAGMA temp_store = FILE")
        # Fold the WAL back into the main file first, otherwise its pages stay
        # behind as a separate sidecar.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        was_wal = (conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == 'wal')
        if was_wal:
            # In WAL mode the main file is only truncated once the LAST
            # connection closes. With another one open — a Streamlit session,
            # or this process holding config.db — VACUUM reports success while
            # the file keeps its old size and the freed pages pile up in the
            # WAL, so the database briefly looks bigger than before. Switching
            # to the rollback journal makes that case an honest
            # "database is locked" instead, and truncates immediately when the
            # database really is free.
            conn.execute("PRAGMA journal_mode = DELETE")
        try:
            conn.execute("VACUUM")
        finally:
            if was_wal:
                conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError as e:
        result['error'] = str(e)
        return result
    finally:
        conn.close()

    result['after'] = _db_size(db_path)
    return result


def calc_atr_percent(df, period=14):
    """
    Berechnet den Average True Range (ATR) als Prozentsatz für einen gegebenen DataFrame.

    Args:
        df (pd.DataFrame): DataFrame mit 'High', 'Low', und 'Close'-Spalten.
        period (int): Die Periode für die ATR-Berechnung.

    Returns:
        pd.Series: Eine Series mit dem ATR-Prozentsatz für jeden Zeitpunkt.
    """
    # 1. True Range (TR) berechnen
    df['H-L'] = abs(df['High'] - df['Low'])
    df['H-C'] = abs(df['High'] - df['Close'].shift(1))
    df['L-C'] = abs(df['Low'] - df['Close'].shift(1))
    df['TR'] = df[['H-L', 'H-C', 'L-C']].max(axis=1)

    # 2. Average True Range (ATR) berechnen
    atr = df['TR'].rolling(window=period).mean()

    from tradinglib.utils import DataUtils
    return round(DataUtils.safe_last(atr, default=0), 2)

def get_roa(ticker):
    
    def get_data(ticker, sheet, name, id):

        value = 0
        sheet = get_ticker_value(ticker, sheet, from_json=True)
        data = sheet['data']
        index = sheet['index']
        pos = index.index(name)
        value = data[pos][id]

        return value

    # calc the Return on Asset to understand the underlying business performance
    roa = 0
    try:
        # Get balance sheet data where 0 is most current year
        total_assets = get_data(ticker, 'balanceSheet','Total Assets', 0)
        net_income = get_data(ticker, 'incomeSheet', 'Net Income', 0)        

        # Calculate ROA
        if total_assets:
            roa = net_income / total_assets

    except Exception:
        pass
    
    return roa


def convert_x_rate(val, conversion_rate):
    
    # try except to avoid div by 0
    try:
        if isinstance(val, int) or isinstance(val, float):
            val = val / conversion_rate
    except Exception:
        pass                

    return val

def get_ticker_value(ticker, key, from_json=False):

    try:
        v = ticker._get_value(0,key)
    except Exception:
        pass

    try:
        if from_json:
            v  = json.loads(v)
    except Exception:
        pass
    
    value = 0
    try:
        value = v
    except Exception:
        pass
# 1703980800
    # asset_info.db speichert alle Spalten als TEXT -- numerische Werte
    # als float zurueckgeben damit Vergleiche wie > 0 funktionieren.
    if isinstance(value, str):
        try:
            value = float(value)
        except (ValueError, TypeError):
            pass  # String-Wert bleibt String (z.B. 'USD', 'XETRA')

    if value:
        if type(value) == int or type(value) == float:
            value = round(value, 2)
    if value != value:
        value = 0
    if value is None:
        value = 0

    return value


# Hinweis: macd_trend/ewo_trend werden jetzt direkt als DataFrame-Spalten
# (`macd_trend` in macd.py, `ewo_trend` in ewo.py) berechnet und über
# DataUtils.safe_last(df[_weekly/_monthly], ...) ins pdict übernommen
# (siehe fill_pdict) — die früheren lokalen Helper-Funktionen macd_trend()/
# trend() wurden dadurch obsolet und entfernt.


def fill_pdict(symbol, ticker, df, df_weekly, df_monthly, simulate=True, year=None, ft=None, last_year_high = 0, last_year_low = 0,
               wk_trend=None):
    """
    
    Args:
    symbol str: ticker symbol
    ticker dict: ticker info data
    df pd.Dataframe: daily data since beginning of curr year
    df_weekls pd.Dataframe: weekly data 
    df_monthly pd.Dataframe: monthly data 
    year str: year
    ft object: of fetch data
    
    Returns:
    dict: performance data
    """
    try:

        pdict = {}
        pdict["ticker"] = symbol

        try:
            date = DataUtils.get_last_index_str(df)
            pdict['Date'] = date
            if simulate and date:
                pdict['Date'] = f"{date[:10]} 00:00:00"
        except Exception as e:
            logger.exception("Error determining last date for %s: %s", symbol, e)
            return None

        # Safe retrievals for many last-row values
        close = DataUtils.safe_last(df, 'Close', default=0)
        relVol = DataUtils.safe_last(df, 'relvol_ratio', default=0)
        wkRelVol = DataUtils.safe_last(df_weekly, 'relvol_ratio', default=0)
        moRelVol = DataUtils.safe_last(df_monthly, 'relvol_ratio', default=0)
        relVolDir = DataUtils.safe_last(df, 'relvol_direction', default=0)
        wkRelVolDir = DataUtils.safe_last(df_weekly, 'relvol_direction', default=0)
        moRelVolDir = DataUtils.safe_last(df_monthly, 'relvol_direction', default=0)
        atc_top_high = DataUtils.safe_last(df, 'atc_top_high', default=0)
        atc_bot_low = DataUtils.safe_last(df, 'atc_bot_low', default=0)
        atc_mid_zero = DataUtils.safe_last(df, 'atc_mid_zero', default=0)
        atc_mid_high = DataUtils.safe_last(df, 'atc_mid_high', default=0)
        rsi = DataUtils.safe_last(df, 'rsi', default=0)
        cci = DataUtils.safe_last(df, 'cci', default=0)
        adx = DataUtils.safe_last(df, 'adx', default=0)
        adx_angle = DataUtils.safe_last(df, 'adx_angle', default=0)
        adx_plus = DataUtils.safe_last(df, 'adx_plus', default=0)
        adx_minus = DataUtils.safe_last(df, 'adx_minus', default=0)
        momentum = DataUtils.safe_last(df, 'momentum', default=0)
        momentum_ema = DataUtils.safe_last(df, 'momentum_ema', default=0)
        momentum_ema_angle = DataUtils.safe_last(df, 'momentum_ema_angle', default=0)
        rsi_ema = DataUtils.safe_last(df, 'rsi_ema', default=0)
        vola = round(df['daily_returns'].std() * math.sqrt(21), 1) if 'daily_returns' in df.columns else 0
        ewo_angle = DataUtils.safe_last(df, 'ewo_angle', default=0)

        pdict["relvol_ratio"] = relVol
        pdict["relvol_ratio_wk"] = wkRelVol
        pdict["relvol_ratio_mo"] = moRelVol
        pdict["relvol_direction"] = relVolDir
        pdict["relvol_direction_wk"] = wkRelVolDir
        pdict["relvol_direction_mo"] = moRelVolDir
        pdict['currency'] = get_ticker_value(ticker, 'currency')
        pdict['ath'] = df_monthly['Close'].max() if (df_monthly is not None and not df_monthly.empty and 'Close' in df_monthly.columns) else 0
        pdict['dTrend'] = indicator.trend_pct_df(df)
        pdict['moTrend'] = indicator.trend_pct_df(df_monthly, date=pdict['Date'])
        # wk_trend: causal value from wk_trend_series (process_symbol). The weekly
        # bar fallback is only right for a single-day run with the week still open.
        pdict['wkTrend'] = (float(wk_trend) if wk_trend is not None and not pd.isna(wk_trend)
                            else indicator.trend_pct_df(df_weekly, date=pdict['Date']))
        pdict['sharpe'] = indicator.sharpe_ratio(df)
        pdict['sortino'] = indicator.sortino_ratio(df)
        pdict['semiVola'] = indicator.semi_volatility(df)
        pdict['close'] = close
        pdict['atc_top_high'] = atc_top_high
        pdict['atc_bot_low'] = atc_bot_low
        pdict['atc_mid_zero'] = atc_mid_zero
        pdict['atc_mid_high'] = atc_mid_high
        pdict['rsi'] = rsi
        pdict['cci'] = cci
        pdict['adx'] = adx
        pdict['adx_angle'] = adx_angle
        pdict['adx_plus'] = adx_plus
        pdict['adx_minus'] = adx_minus
        pdict['momentum'] = momentum
        pdict['momentum_ema'] = momentum_ema
        pdict['momentum_ema_angle'] = momentum_ema_angle
        pdict['rsi_ema'] = rsi_ema
        pdict['vola'] = vola
        pdict['ewo_trend_day'] = DataUtils.safe_last(df, 'ewo_trend', default=0)
        pdict['ewo_trend_wk'] = DataUtils.safe_last(df_weekly, 'ewo_trend', default=0)
        pdict['ewo_trend_mo'] = DataUtils.safe_last(df_monthly, 'ewo_trend', default=0)
        pdict['ewo_angle'] = ewo_angle
        try:
            pdict['atr'] = calc_atr_percent(df=df)
        except Exception:
            pdict['atr'] = 0
        pdict['take_profit'] = pdict['close'] + 4 * pdict['atr']
        pdict['stop_loss'] = pdict['close'] - 2 * pdict['atr']

        pdict['buySell'] = 0
        try:
            pdict['buySell'] = int(DataUtils.safe_last(df, 'position', default=0))
        except Exception:
            pdict['buySell'] = 0
        pdict['roa'] = get_roa(ticker)
        pdict['macd_trend'] = DataUtils.safe_last(df, 'macd_trend', default=0)
        pdict['macd_trend_wk'] = DataUtils.safe_last(df_weekly, 'macd_trend', default=0)
        pdict['macd_trend_mo'] = DataUtils.safe_last(df_monthly, 'macd_trend', default=0)
        pdict['pctTargetHighPrice'] = indicator.trend_pct(get_ticker_value(ticker, 'targetHighPrice'), pdict['close'])
        pdict['logVola'] = float(DataUtils.safe_last(df, 'log_vola', default=0))

        # 4PS columns — the signal columns are NaN except on a signal day, so
        # they are stored as 0 to keep the sim-DB plain REALs.
        # Profile scores — same shape as the fps block below: NaN becomes 0.0 so a
        # missing indicator never writes NULL into a numeric column.
        for _prof_col in ('riskScore', 'riskBucket', 'trendScore'):
            _prof_val = DataUtils.safe_last(df, _prof_col, default=0)
            pdict[_prof_col] = 0.0 if _prof_val is None or pd.isna(_prof_val) else float(_prof_val)

        for _mtt_col in INDICATOR_BACKFILL_MAP['mtt'] + INDICATOR_BACKFILL_MAP['rft']:
            _mtt_val = DataUtils.safe_last(df, _mtt_col, default=0)
            pdict[_mtt_col] = 0.0 if _mtt_val is None or pd.isna(_mtt_val) else float(_mtt_val)

        for _fps_col in ('fps_phase', 'fps_best_trend', 'fps_trend_gain', 'fps_base_high',
                         'fps_base_low', 'fps_base_weeks', 'fps_breakout', 'fps_buy',
                         'fps_sell', 'fps_stop', 'fps_target', 'fps_rs', 'fps_dist_high',
                         'fps_setup'):
            _fps_val = DataUtils.safe_last(df, _fps_col, default=0)
            pdict[_fps_col] = 0.0 if _fps_val is None or pd.isna(_fps_val) else float(_fps_val)

        pdict['predictedLow'] = getattr(ft.pre, 'pr_low', 0)
        pdict['predictedHigh'] = getattr(ft.pre, 'pr_high', 0)
        pdict['High'] = DataUtils.safe_last(df, 'High', default=0)
        pdict['Low'] = DataUtils.safe_last(df, 'Low', default=0)
        pdict['Open'] = DataUtils.safe_last(df, 'Open', default=0)

        # determine trend direction using already-extracted safe values
        if pdict['adx'] > 25 and pdict['momentum'] > pdict['rsi_ema'] and pdict['momentum'] > 40:
            pdict['trendDirection'] = 2
        elif pdict['momentum'] > pdict['rsi_ema']:
            pdict['trendDirection'] = 1
        elif pdict['momentum'] < pdict['rsi_ema']:
            pdict['trendDirection'] = -1
        else:
            pdict['trendDirection'] = 0

        pdict['lyHigh'] = last_year_high
        pdict['lyLow'] = last_year_low

        pdict['dema_ema_fast'] = DataUtils.safe_last(df, 'dema_ema_fast', default=0)
        pdict['dema_ema_slow'] = DataUtils.safe_last(df, 'dema_ema_slow', default=0)
        pdict['dema_buy'] = DataUtils.safe_last(df, 'dema_buy', default=0)
        pdict['dema_sell'] = DataUtils.safe_last(df, 'dema_sell', default=0)
        pdict['sup_resistance'] = DataUtils.safe_last(df, 'sup_resistance', default=0)
        pdict['sup_resistance_wk'] = DataUtils.safe_last(df_weekly, 'sup_resistance', default=0)
        pdict['sup_resistance_mo'] = DataUtils.safe_last(df_monthly, 'sup_resistance', default=0)
        pdict['sup_support'] = DataUtils.safe_last(df, 'sup_support', default=0)
        pdict['sup_support_wk'] = DataUtils.safe_last(df_weekly, 'sup_support', default=0)
        pdict['sup_support_mo'] = DataUtils.safe_last(df_monthly, 'sup_support', default=0)
        pdict['sma20'] = DataUtils.safe_last(df, 'sma20', default=0)
        pdict['sma50'] = DataUtils.safe_last(df, 'sma50', default=0)
        pdict['sma200'] = DataUtils.safe_last(df, 'sma200', default=0)
        pdict['ema9'] = DataUtils.safe_last(df, 'ema9', default=0)
        pdict['ema9_angle'] = DataUtils.safe_last(df, 'ema9_angle', default=0)
        pdict['ema21'] = DataUtils.safe_last(df, 'ema21', default=0)
        pdict['ema50'] = DataUtils.safe_last(df, 'ema50', default=0)
        pdict['cci'] = DataUtils.safe_last(df, 'cci', default=0)
        pdict['fvg'] = DataUtils.safe_last(df, 'fvg', default=0)
        pdict['logRet'] = DataUtils.safe_last(df, 'log_ret', default=0)
        pdict['macd'] = DataUtils.safe_last(df, 'macd', default=0)
        pdict['macd_signal'] = DataUtils.safe_last(df, 'macd_signal', default=0)
        pdict['ewo'] = DataUtils.safe_last(df, 'ewo', default=0)
        pdict['ewo_mo'] = DataUtils.safe_last(df_monthly, 'ewo', default=0)
        pdict['ewo_wk'] = DataUtils.safe_last(df_weekly, 'ewo', default=0)
        pdict['ewo_ema'] = DataUtils.safe_last(df, 'ewo_ema', default=0)
        pdict['zcr'] = DataUtils.safe_last(df, 'zcr', default=0)
        pdict['ha_ema_high'] = DataUtils.safe_last(df, 'ha_ema_high', default=0)
        pdict['ha_ema_low'] = DataUtils.safe_last(df, 'ha_ema_low', default=0)
        pdict['ha_close'] = DataUtils.safe_last(df, 'ha_close', default=0)
        pdict['ha_open'] = DataUtils.safe_last(df, 'ha_open', default=0)
        pdict['markov_regime'] = DataUtils.safe_last(df, 'markov_regime', default=0)
        pdict['markov_regime_wk'] = DataUtils.safe_last(df_weekly, 'markov_regime', default=0)
        pdict['markov_regime_mo'] = DataUtils.safe_last(df_monthly, 'markov_regime', default=0)
        pdict['hor_val'] = DataUtils.safe_last(df, 'hor_val', default=0)
        pdict['hor_threshold'] = DataUtils.safe_last(df, 'hor_threshold', default=0)
        pdict['qtrend_entry_price'] = DataUtils.safe_last(df, 'qtrend_entry_price', default=0)

        return pdict

    except Exception as e:
        import traceback
        print(f"⚠️ fill_pdict exception for {symbol}: {e}")
        print(traceback.format_exc())
        return None


def process_symbol(symbol, simulate=True, add_current=False, year='', init=False,
                   only_dates=None):
    """Funktion, die pro Ticker in einem separaten Prozess läuft.

    *only_dates* (a set of 'YYYY-MM-DD' strings, used by /fill) restricts the
    output to exactly those days. Every row is built from `df_r.loc[startdate:date]`,
    so a row only ever sees data up to its own date — computing a subset therefore
    yields the same values a full run would write for those days.
    """
    from tradinglib import fetch_data, indicator, ticker_tools as tt  # 🔹 Lokale Imports innerhalb des Prozesses
    from tradinglib.utils import DataUtils
    ft = fetch_data.FetchData(indicators=[ 'adx', 'macd', 'rsi', 'stoch', 'cci', 'fvg', 'bos', 'vol', 'don', 'fib', 'bol', 'gan', 'sup', 'pre', 'ewo','vwap','lqz','ici','bsz','heikin', 'atc', 'candle', 'zcr', 'relvol','dema','hor','qtrend','markov','fps','prof','mtt','rft'])
    results = []

    # Use DataUtils.ensure_datetime_index for consistent index handling

    now = datetime.now()
    try:
        (df_m, _) = ft.fetch_data(symbol, interval="1mo", period="10y")
        (df_w, _) = ft.fetch_data(symbol, interval="1wk", period="6y")
        if not year == '':
            periods = (2 + now.year - year)
            (df, ticker) = ft.fetch_data(symbol,interval = '1d', period = f"{periods}y")
        else:
            (df, ticker) = ft.fetch_data(symbol,interval = '1d', period = '2y', add_current=add_current)

        df = DataUtils.ensure_datetime_index(df)
        df_m = DataUtils.ensure_datetime_index(df_m)
        df_w = DataUtils.ensure_datetime_index(df_w)
        if df is None or df.empty:
            return None

        # --- Zeitraumbegrenzung ---
        if year == "":
            last_year = now.year - 1
        else:
            last_year = year - 1

        t_string = "%Y-%m-%d 00:00:00"
        enddate = datetime.strptime(f"{last_year}-12-31 00:00:00", t_string).date().strftime(t_string)
        startdate = datetime.strptime(f"{last_year}-01-01 00:00:00", t_string).date().strftime(t_string)

        df_r = df.loc[startdate:enddate]
        last_year_high = DataUtils.safe_last(df_r, 'Close', default=0)
        last_year_low = DataUtils.safe_first(df_r, 'Close', default=0)
        df_r = df.copy()

        # by default we calculate last 5 days only
        start_of_year = (datetime.strptime(f"{now.year}-{now.month}-{now.day} 00:00:00", t_string).date() + timedelta(days=-6)).strftime(t_string)
        end_of_year = f"{str(pd.Timestamp(now))[:10]} 00:00:00" #datetime.strptime(now, t_string).date().strftime(t_string)
        #otherwise we use the current year
        if init:
            start_of_year = datetime.strptime(f"{now.year}-01-01 00:00:00", t_string).date().strftime(t_string)
        #or the year we enforce            
        if not year == '':
            start_of_year = datetime.strptime(f"{year}-01-01 00:00:00", t_string).date().strftime(t_string)
            end_of_year = datetime.strptime(f"{year}-12-31 00:00:00", t_string).date().strftime(t_string)
        # /fill: narrow the window to the span of the missing days. Must come
        # last — it overrides both the init and the /year window.
        if only_dates:
            start_of_year = f"{min(only_dates)} 00:00:00"
            end_of_year = f"{max(only_dates)} 00:00:00"

        mask = (df.index >= pd.Timestamp(start_of_year)) & (df.index <= pd.Timestamp(end_of_year))
        df = df.loc[mask]

        if df.empty:
            logger.warning("%s: no data", symbol)
            return []

        # --- Index sicherstellen ---
        if df is not None and "Date" in df.columns:
            df = df.set_index("Date")
        if df_m is not None and "Date" in df_m.columns:
            df_m = df_m.set_index("Date")
        if df_w is not None and "Date" in df_w.columns:
            df_w = df_w.set_index("Date")
        # set_index("Date") above re-installs the raw (possibly object-dtype,
        # unsorted) Date column as index -> coerce back to a sorted DatetimeIndex
        # so the df_w/df_m.loc[:date] slices below don't compare str vs Timestamp.
        df = DataUtils.ensure_datetime_index(df)
        df_m = DataUtils.ensure_datetime_index(df_m)
        df_w = DataUtils.ensure_datetime_index(df_w)

        # Weekly/monthly support/resistance as of each day, from the daily bars.
        # The last row of df_w/df_m.loc[:date] is a bar dated at the START of its
        # week/month, which in a multi-day run already holds the whole period --
        # Monday's row would have known Friday's low. Same function as /backfill:sup.
        sup_htf = sup_htf_series(df_r)
        # Same reason for wkTrend: week-to-date close vs. the last completed week.
        wk_trend = wk_trend_series(df_r)

        for date in df.index:
            if only_dates is not None and date.strftime('%Y-%m-%d') not in only_dates:
                continue  # already in the DB — nothing to recompute
            res_df_d = df_r.loc[startdate:date]
            res_df_w = df_w.loc[:date] if df_w is not None else pd.DataFrame()
            res_df_m = df_m.loc[:date] if df_m is not None else pd.DataFrame()
            pdict = fill_pdict(symbol, ticker, res_df_d, res_df_w, res_df_m, year=year, ft=ft, last_year_high=last_year_high, last_year_low=last_year_low,
                               wk_trend=wk_trend.get(date))
            if pdict:
                for _col, _series in sup_htf.items():
                    _v = _series.get(date)
                    pdict[_col] = 0.0 if _v is None or pd.isna(_v) else float(_v)
                results.append(pdict)

        return results

    except Exception as e:
        logger.exception("Error processing %s: %s", symbol, e)
        return None

# --- Hauptprogramm ---
if __name__ == "__main__":

    # parse CLI args (use tradinglib.cli for reuse)
    if len(sys.argv) <= 1:
        cli.print_help()
        exit()

    args = cli.parse_args(sys.argv)
    simulate = args.get('simulate', True)
    init = args.get('init', False)
    all = args.get('all', False)
    year = args.get('year', '')
    inverse = args.get('inverse', False)
    index_name = args.get('index_name', '')
    group = args.get('group') or []   # list[str] aus /group:NAME1,NAME2 (uppercased)
    silent = args.get('silent', False)
    add_current = args.get('add_current', False)
    memory = args.get('memory', False)
    worker = args.get('worker', 0)

    # configure logging centrally
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))

    db_table = "asset_simulation"
    if all:
        sim_db_name = f"{db_table}_etp.db" if is_etp_run(group) else f"{db_table}_all.db"
    else:
        sim_db_name = f"{db_table}_{year}.db"

    # -----------------------------------------------------------------------
    # Fast modes — no yfinance, early exit
    #
    # Note the order: /vacuum runs BEFORE SystemConfig is instantiated. That
    # call opens config.db and keeps it open, which would keep VACUUM from
    # truncating exactly that file.
    # -----------------------------------------------------------------------
    backfill_inds  = args.get('backfill', None)   # list[str] or None
    vacuum_flag    = args.get('vacuum', False)
    vacuum_pattern = args.get('vacuum_pattern', None)   # glob, None = the resolved sim-DB

    if vacuum_flag:
        db_dir = os.path.dirname(_Tools().get_path(path='database', file_name=sim_db_name))
        if vacuum_pattern:
            # basename() keeps the pattern inside the database directory; the
            # .db suffix lets '/vacuum:*' mean "every database", not every file.
            pattern = os.path.basename(vacuum_pattern)
            if not pattern.endswith('.db'):
                pattern += '.db'
            targets = sorted(glob.glob(os.path.join(db_dir, pattern)))
            if not targets:
                logger.warning("vacuum: no database matches '%s' in %s", pattern, db_dir)
        else:
            targets = [os.path.join(db_dir, sim_db_name)]

        total_before = total_after = 0
        for path in targets:
            res = vacuum_db(path)
            name = os.path.basename(path)
            if res['error']:
                # Locked / missing / no space — reported per file, the rest still runs.
                logger.warning("vacuum: %s skipped — %s", name, res['error'])
                continue
            saved = res['before'] - res['after']
            total_before += res['before']
            total_after += res['after']
            logger.info("vacuum: %s  %s -> %s  (%s freed, %.1f %%)",
                        name, _fmt_size(res['before']), _fmt_size(res['after']),
                        _fmt_size(saved),
                        (saved / res['before'] * 100) if res['before'] else 0.0)
        if len(targets) > 1 and total_before:
            logger.info("vacuum: total %s -> %s (%s freed)",
                        _fmt_size(total_before), _fmt_size(total_after),
                        _fmt_size(total_before - total_after))
        exit(0)

    if backfill_inds:
        sim_db_path = _Tools().get_path(path='database', file_name=sim_db_name)
        force_flag  = args.get('force', False)
        unknown = [i for i in backfill_inds if i not in INDICATOR_BACKFILL_MAP]
        if unknown:
            logger.warning("backfill: unknown indicators ignored: %s", unknown)
            backfill_inds = [i for i in backfill_inds if i in INDICATOR_BACKFILL_MAP]
        if backfill_inds:
            logger.info("backfill mode: %s -> indicators=%s force=%s",
                        sim_db_path, backfill_inds, force_flag)
            backfill_db(sim_db_path, 'database', backfill_inds, force=force_flag)
            if 'mtt' in backfill_inds:
                from tradinglib import minervini
                minervini.update_rs(sim_db_path)
        exit(0)
    # -----------------------------------------------------------------------

    sys_conf = sysconf.SystemConfig(username="admin")
    system_currency = sys_conf.get_value("system_currency", "EUR")

    info_db = tt.tools.Db_tools(db_path="database", database_name="yf_tickers.db")
    sim_db = tt.tools.Db_tools(db_path="database", database_name=sim_db_name, db_type=":memory:")

    ft = fetch_data.FetchData(indicators=[ 'adx', 'macd', 'rsi', 'stoch', 'cci', 'fvg', 'bos', 'vol', 'don', 'fib', 'bol', 'gan', 'sup', 'pre', 'ewo','vwap','lqz','ici','bsz','heikin', 'atc', 'candle', 'zcr', 'relvol','dema','markov'])

    delete_columns = list(NON_STOCK_GROUPS)
    filtered = info_db.get_indices_names(delete_columns=delete_columns, table_name='indices')
    # Default-Lauf = ausschließlich echte Börsen-Indizes: deren Namen beginnen per
    # Konvention mit '^' (^GDAXI, ^SPX, …). Kategorie-Gruppen wie ETP/COMMODITIES/
    # CRYPTO/CURRENCIES sind nur über /group:NAME ansprechbar und werden hier nicht
    # subsumiert (analog get_asset_data.py).
    filtered = [name for name in filtered if name.startswith('^')]
    if inverse:
        ticker_query = f'SELECT s.Ticker FROM stocks s LEFT JOIN stock_indices si ON s.id = si.stock_id WHERE si.index_id IS NULL;'
    elif group:
        # Mitglieder beliebiger Gruppen aus der indices-Tabelle, z.B. /group:ETP
        # oder /group:METALS,COMMODITIES. Gruppennamen kommen bereits uppercased.
        group_sql = '","'.join(group)
        ticker_query = (
            'SELECT s.Ticker FROM stocks s '
            'JOIN stock_indices si ON s.id = si.stock_id '
            'JOIN indices i ON si.index_id = i.id '
            f'WHERE UPPER(i.name) IN ("{group_sql}")'
        )
    else:
        ticker_query = f'SELECT s.Ticker FROM stocks s JOIN stock_indices si ON s.id = si.stock_id JOIN indices i ON si.index_id = i.id WHERE i.name = "' + '" OR i.name = "'.join(filtered)+'"'

    if simulate:
        if not index_name == '':
            # /index:NAME[,NAME2,...] wertet ausschließlich die genannten Indizes aus.
            # Mehrere kommagetrennt (analog /group); Case egal (UPPER-Vergleich, die
            # DB-Namen sind ohnehin uppercase: ^DJI/^SPX/^RUT/…). Das frühere pauschale
            # Anhängen von NON_STOCK_GROUPS ist ein Relikt aus der Zeit vor /group und
            # entfällt — Kategorie-Gruppen laufen über /group:NAME.
            idx_names = [s.strip().upper() for s in index_name.split(',') if s.strip()]
            idx_sql = '","'.join(idx_names)
            ticker_query = (
                'SELECT s.Ticker FROM stocks s '
                'JOIN stock_indices si ON s.id = si.stock_id '
                'JOIN indices i ON si.index_id = i.id '
                f'WHERE UPPER(i.name) IN ("{idx_sql}")'
            )
    if all:
        if is_etp_run(group):
            ticker_query = etp_ticker_query(group)
            logger.info("Using all %s tickers", "/".join(sorted(set(group))))
        else:
            ticker_query = ('SELECT Ticker FROM stocks WHERE id NOT IN ('
                             'SELECT si.stock_id FROM stock_indices si '
                             'JOIN indices i ON si.index_id = i.id '
                             'WHERE UPPER(i.name) = "ETP")')
            logger.info("Using all tickers (excl. ETP)")
        simulate = True

    logger.debug("Running query: %s", ticker_query)
    tickers = pd.read_sql_query(ticker_query, info_db.conn)["Ticker"].unique().tolist()
    tickers = list(set(tickers))
    logger.info("%d tickers found.", len(tickers))

    # The default run (index members) also covers everything currently held:
    # a ticker that left its index would otherwise drop out of the current-year
    # simulation while still in the depot (AAD.DE, unlinked 2026-08-09).
    if not (all or inverse or group or index_name):
        try:
            from tradinglib.summary_sources import held_tickers
            extra = sorted(set(held_tickers()) - set(tickers))
        except Exception as e:
            logger.warning("held tickers not added: %s", e)
            extra = []
        if extra:
            logger.info("adding %d held ticker(s) outside the indices: %s",
                        len(extra), ", ".join(extra))
            tickers += extra

    # -----------------------------------------------------------------------
    # /fill — compute only what is missing.
    #
    # /init recomputes every day since 1 January for every ticker, which is
    # wasted work once most of the year is already stored. /fill compares the
    # simulation against the local daily series and hands each worker just the
    # days it lacks: the stretch before an asset was added, plus any interior
    # day a nightly run skipped.
    # -----------------------------------------------------------------------
    only_dates_map = None
    if args.get('fill', False):
        _now = datetime.now()
        fill_year = year if not year == '' else _now.year
        win_start = f"{fill_year}-01-01 00:00:00"
        win_end = (f"{fill_year}-12-31 23:59:59" if not year == ''
                   else f"{_now.strftime('%Y-%m-%d')} 23:59:59")
        sim_path = _Tools().get_path(path='database', file_name=sim_db_name)
        only_dates_map = collect_missing_dates(sim_path, tickers, win_start, win_end)
        gap_rows = sum(len(v) for v in only_dates_map.values())
        logger.info("fill: %s .. %s — %d of %d tickers incomplete, %d rows missing",
                    win_start[:10], win_end[:10], len(only_dates_map),
                    len(tickers), gap_rows)
        if args.get('dry', False):
            for tk in sorted(only_dates_map, key=lambda t: -len(only_dates_map[t]))[:25]:
                days = sorted(only_dates_map[tk])
                logger.info("  %-14s %4d missing  (%s .. %s)",
                            tk, len(days), days[0], days[-1])
            if len(only_dates_map) > 25:
                logger.info("  ... and %d more tickers", len(only_dates_map) - 25)
            exit(0)
        if not only_dates_map:
            logger.info("fill: nothing to do")
            exit(0)
        tickers = sorted(only_dates_map)

    all_results = []
    start = time.time()

    # === Parallel: pro Symbol rechnen ===
    max_workers = 1
    if sys.platform == 'win32':
        # Auf Windows begrenzen wir auf max. 2 Worker um OOM zu vermeiden.
        # Jeder Worker laedt einen vollen DataFrame + Indikatoren in den RAM.
        # Mit cpu_count/2-1 koennen auf vielkernigen Systemen zu viele
        # Prozesse entstehen und die Sandbox / den PC zum Absturz bringen.
        max_workers = min(2, max(1, (os.cpu_count() // 2) - 1))
    if worker > 0:
        max_workers = worker
    logger.info("Using %d threads.", max_workers)
    if not year == "":
        logger.info("Year to analyze is %s", year)

    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        # 3. Positionsargument = add_current: die geparste /add_current-Option
        # durchreichen (vorher hart False → Flag war wirkungslos).
        futures = {executor.submit(process_symbol, sym, True, add_current, year=year, init=init,
                                   only_dates=(only_dates_map.get(sym) if only_dates_map else None)): sym
                   for sym in tickers}
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            try:
                res = future.result()
                if res is None:
                    logger.warning("%s: keine Daten (lokale DB leer oder nicht vorhanden)", symbol)
                else:
                    all_results.extend(res)
                    logger.info("%s: %d rows", symbol, len(res))
            except Exception as e:
                logger.exception("Error processing symbol %s: %s", symbol, e)

    logger.info("Parallel processing done in %.2fs", time.time()-start)

    # === Seriell: in SQLite schreiben ===
    db_table = "asset_simulation"
    for pdict in all_results:
        sim_db.ensure_table_and_columns(pdict.keys(), pdict, db_table, primary_key=False)
        sim_db.insert_data(pdict.keys(), pdict, db_table, replace=False)

    sim_db.conn.commit()
    sim_db.close() # write memory db to file

    # reopen db
    sim_db = tt.tools.Db_tools(db_path='database', database_name=sim_db_name, db_type=":memory:")

    logger.info("removing duplicates...")
    # avoid duplicate entries
    try:
        sim_db.cursor.execute( f"""
        DELETE FROM {db_table}
            WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM {db_table}
                GROUP BY ticker, Date
            );
        """)
        sim_db.conn.commit()
    except sqlite3.Error as e:
        logger.warning("Duplikate konnten nicht entfernt werden (Tabelle evtl. leer): %s", e)

    sim_db.close()

    # Minervini RS rating for the days just written: a cross-sectional rank, so
    # it can only be computed once all tickers of the run are stored -- and
    # before the notifier, whose formulas may use mtt_rs.
    written_days = [str(p.get('Date', ''))[:10] for p in all_results if p.get('Date')]
    if written_days:
        try:
            from tradinglib import minervini
            minervini.update_rs(_Tools().get_path(path='database', file_name=sim_db_name),
                                since=min(written_days))
        except Exception as e:
            logger.warning("RS rating update failed: %s", e)

    # Market breadth (market_context.db) for the days just written. Runs after
    # the index-member run only -- breadth is computed from index members -- and
    # before the notifier, whose multi-strategy run may use mkt_breadth* in its
    # formulas. Recomputing ~60 days takes a second or two.
    if year == '' and not (all or group or inverse):
        try:
            from tradinglib import market_context
            logger.info("market breadth: %d index-days updated", market_context.update())
        except Exception as e:
            logger.warning("market breadth update failed: %s", e)

    if year == '' and not all:
        if simulate and not silent:
            logger.info("notifying via pushover...")
            if mt is not None:
                mt.run_notifier()
            else:
                logger.warning("Pushover-Notifier nicht konfiguriert -- Benachrichtigung uebersprungen.")

