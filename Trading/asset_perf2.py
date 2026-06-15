import concurrent.futures
import pandas as pd
from datetime import datetime, timedelta
import math
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
    'relvol':  ['relvol_ratio'],
    'atc':     ['atc_top_high', 'atc_bot_low'],
}

# Indicators that are additionally computed on higher timeframes during
# backfill. Each entry: (interval, period, column_suffix). The indicator runs
# on the wk/mo OHLCV and its columns from INDICATOR_BACKFILL_MAP are
# forward-filled onto the daily index (a wk/mo value holds until the next
# wk/mo bar closes) and stored as <col><suffix>, e.g. markov_regime_wk.
INDICATOR_BACKFILL_TF: dict = {
    'markov': [('1wk', '6y', '_wk'), ('1mo', '10y', '_mo')],
}


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

    # 3. Collect the DB columns we need to update
    target_cols: list[str] = []
    for ind_name in indicator_names:
        base_cols = INDICATOR_BACKFILL_MAP.get(ind_name, [])
        target_cols.extend(base_cols)
        for _, _, suffix in INDICATOR_BACKFILL_TF.get(ind_name, []):
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


# ---------------------------------------------------------------------------
# Vectorised re-score helpers
# ---------------------------------------------------------------------------

def _mo_trend_scalar(sma200: float, sma50: float, sma20: float,
                     close: float, first_close: float, ly_high: float) -> float:
    """Approximate get_mo_trend() using stored daily MA values."""
    try:
        if (sma200 > close) or (ly_high > 0 and ly_high > close) or (sma50 < sma200):
            return 0.0   # Sell
        if (close - first_close < 0) or (sma20 > close) or (sma50 > sma20):
            return 0.5   # Hold
        return 1.0       # Buy
    except Exception:
        return 0.0


def _d_trend_scalar(sma200: float, sma50: float, sma20: float, close: float) -> float:
    """Approximate get_trend() for daily/weekly using stored MA values."""
    try:
        trend = 0.5
        if sma50 < close or sma20 < close:
            trend = 1.0
        if sma50 < sma200:
            trend = 0.0
        return trend
    except Exception:
        return 0.5


def score_df(sim_df: pd.DataFrame, info_df: pd.DataFrame) -> pd.DataFrame:
    """Re-compute overallTrend and overallValueTrend from stored columns.

    *sim_df*  — all rows for one ticker, sorted by Date ascending.
    *info_df* — single-row DataFrame from asset_info for that ticker.

    Returns sim_df with updated 'overallTrend' and 'overallValueTrend' columns.
    """
    import numpy as np

    n = len(sim_df)
    if n == 0:
        return sim_df

    def col(name, default=0.0):
        if name in sim_df.columns:
            return sim_df[name].fillna(default).values.astype(float)
        return np.full(n, default)

    def info_val(name, default=0.0):
        if info_df is not None and name in info_df.columns:
            v = info_df[name].iloc[0]
            try:
                return float(v) if v == v else default
            except Exception:
                return default
        return default

    close     = col('close')
    sma200    = col('sma200')
    sma50     = col('sma50')
    sma20     = col('sma20')
    ly_high   = col('lyHigh')
    # first close of the series (used as proxy for 'start of year' close)
    first_close = close[0] if n > 0 else 0.0

    # per-row trend scalars (vectorised via list comprehension — fast enough)
    mo_trend = np.array([_mo_trend_scalar(sma200[i], sma50[i], sma20[i],
                                          close[i], first_close, ly_high[i])
                         for i in range(n)])
    d_trend  = np.array([_d_trend_scalar(sma200[i], sma50[i], sma20[i], close[i])
                         for i in range(n)])

    ewo_angle  = col('ewo_angle')
    ewo        = col('ewo')
    ewo_ema    = col('ewo_ema')
    ha_ema_lo  = col('ha_ema_low')
    ha_ema_hi  = col('ha_ema_high')
    vola       = col('vola')
    mo_trend_s = col('moTrend')     # price % change (proxy for monthly trend direction)
    macd_tr    = col('macd_trend')
    wk_macd_tr = col('macd_trend_wk')
    mo_macd_tr = col('macd_trend_mo')
    momentum   = col('momentum')
    rsi_ema    = col('rsi_ema')
    adx        = col('adx')
    rsi        = col('rsi')
    sharpe     = col('sharpe')
    sortino    = col('sortino')
    log_vola   = col('logVola')
    pred_hi    = col('predictedHigh')
    pred_lo    = col('predictedLow')
    day_low    = col('dayLow')
    roa        = col('roa')
    pct_tgt    = col('pctTargetHighPrice')

    # Fundamental scalars (single value per ticker from asset_info)
    forward_eps  = info_val('forwardEps')
    forward_pe   = info_val('forwardPE')
    trailing_pe  = info_val('trailingPE')
    dividend_rt  = info_val('dividendRate')
    earn_growth  = info_val('earningsGrowth')
    op_margins   = info_val('operatingMargins')
    p2book       = info_val('priceToBook')
    ent_val      = info_val('enterpriseValue')
    total_debt   = info_val('totalDebt')
    avg_vol      = info_val('averageVolume')

    # Trend direction (re-derive from stored columns)
    trend_dir = np.where((adx > 25) & (momentum > rsi_ema) & (momentum > 40), 2,
                np.where(momentum > rsi_ema, 1,
                np.where(momentum < rsi_ema, -1, 0))).astype(float)

    # Use stored wkTrend as a simple weekly trend proxy (>0 → 1.0 else 0.5)
    wk_trend = np.where(col('wkTrend') > 0, 1.0, 0.5)

    # --- Accumulate per-row scores ----------------------------------------
    ov_trend  = np.zeros(n)
    ov_val    = np.zeros(n)
    tw        = np.zeros(n)   # technical weight denominator
    vw        = np.zeros(n)   # value weight denominator

    def _up(condition, weight, points, is_value, ov_t, ov_v, tw_acc, vw_acc):
        """Vectorised equivalent of Sum.up()."""
        vw_acc += weight
        if not is_value:
            tw_acc += weight
        ov_v += np.where(condition, points, 0)
        if is_value:
            ov_t += np.where(condition, points, 0)
        return ov_t, ov_v, tw_acc, vw_acc

    # ---- Technical (isValue=False) ----
    # get_mo_trend + wk_trend + d_trend  (3,3,False)
    combined_trend = mo_trend + wk_trend + d_trend
    ov_trend, ov_val, tw, vw = _up(combined_trend, 3, 3, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(ewo_angle > 0, 0.5, 0.5, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(ewo > ewo_ema, 0.5, 0.5, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(ha_ema_lo < close, 1, 1, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(ha_ema_hi < close, 1, 1, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(ha_ema_lo > close, 1, -2, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(trend_dir >= 1, 3, 3, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(vola / 2 < mo_trend_s, 1, 1, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(mo_trend_s > vola, 1, 1, False, ov_trend, ov_val, tw, vw)
    # macd_trend / macd_trend_wk / macd_trend_mo are always-true sum.up (True, 1, val, False)
    vw += 1; tw += 1; ov_trend += macd_tr; ov_val += macd_tr
    vw += 1; tw += 1; ov_trend += wk_macd_tr; ov_val += wk_macd_tr
    vw += 1; tw += 1; ov_trend += mo_macd_tr; ov_val += mo_macd_tr
    ov_trend, ov_val, tw, vw = _up(avg_vol > 10_000_000, 0, 0.5, False, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(sortino > 1, 0.5, 0.5, False, ov_trend, ov_val, tw, vw)

    # ---- Value (isValue=True) ----
    ov_trend, ov_val, tw, vw = _up(roa > 0.2, 1, 1, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(roa < 0.05, 0, -1, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(log_vola < 0.1, 1, 1, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(forward_eps > 0.1, 0.5, 0.5, True, ov_trend, ov_val, tw, vw)
    # PE condition — conditional weight
    pe_cond = ((forward_pe < 25) & (forward_pe > 0)) | ((forward_pe == 0) & (trailing_pe < 25) & (trailing_pe > 0))
    ov_trend, ov_val, tw, vw = _up(pe_cond, 0.5, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(pct_tgt > 10, 1, 0.25, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(pct_tgt > 20, 0, 0.25, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(pct_tgt > 30, 0, 0.25, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(pct_tgt > 40, 1, 0.25, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(pct_tgt < 0, 0, -2, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(dividend_rt > 0, 0.5, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(earn_growth > 0, 0.5, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(op_margins > 0, 0.5, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(p2book > 50, 1, 1, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(p2book < 5, 0, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(p2book < 0, 0, 0.5, True, ov_trend, ov_val, tw, vw)
    if total_debt != 0:
        ev_ratio = ent_val / total_debt if total_debt != 0 else 0.0
        ov_trend, ov_val, tw, vw = _up(ev_ratio > 3, 1, 1, True, ov_trend, ov_val, tw, vw)
    else:
        vw += 1; ov_val += 0
    ov_trend, ov_val, tw, vw = _up(avg_vol > 1_000_000, 1, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up((rsi >= 20) & (rsi <= 80), 0.5, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(sharpe > 1, 0.5, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(sharpe > 2, 0.5, 0.5, True, ov_trend, ov_val, tw, vw)
    ov_trend, ov_val, tw, vw = _up(pred_hi < day_low, 0.5, 0.5, True, ov_trend, ov_val, tw, vw)

    # Normalise to [-100, 100]
    with np.errstate(divide='ignore', invalid='ignore'):
        ot_norm = np.where(tw != 0, np.clip(ov_trend * 100 / tw, -100, 100), 0)
        ovt_norm = np.where(vw != 0, np.clip(ov_val * 100 / vw, -100, 100), 0)

    sim_df = sim_df.copy()
    sim_df['overallTrend']      = np.round(ot_norm).astype(int)
    sim_df['overallValueTrend'] = np.round(ovt_norm).astype(int)
    return sim_df


def rescore_db(sim_db_path: str, info_db_path: str) -> None:
    """Re-compute overallTrend/overallValueTrend for every row in *sim_db_path*.

    Uses stored indicator columns + asset_info fundamentals. No yfinance needed.
    """
    table = 'asset_simulation'
    sim_conn = open_db(sim_db_path)
    info_conn = open_db(info_db_path, readonly=True)
    try:
        df_all = pd.read_sql_query(
            f"SELECT * FROM {table} ORDER BY ticker, Date", sim_conn)
        if df_all.empty:
            logger.warning("rescore: sim-DB is empty")
            return
        logger.info("rescore: %d rows, %d tickers",
                    len(df_all), df_all['ticker'].nunique())

        # Load asset_info fundamentals (one row per ticker)
        try:
            info_df_all = pd.read_sql_query("SELECT * FROM asset_info", info_conn)
        except Exception:
            info_df_all = pd.DataFrame()

        rescored_parts = []
        for sym, grp in df_all.groupby('ticker', sort=False):
            grp = grp.sort_values('Date').reset_index(drop=True)
            info_row = (info_df_all[info_df_all['ticker'] == sym]
                        if not info_df_all.empty and 'ticker' in info_df_all.columns
                        else pd.DataFrame())
            rescored = score_df(grp, info_row if not info_row.empty else None)
            rescored_parts.append(rescored[['ticker', 'Date', 'overallTrend', 'overallValueTrend']])

        updates = pd.concat(rescored_parts, ignore_index=True)

        # Write back via temp table + batch UPDATE
        updates.to_sql('_rescore_tmp', sim_conn, if_exists='replace', index=False)
        sim_conn.execute(f"""
            UPDATE {table}
            SET overallTrend      = (SELECT t.overallTrend      FROM _rescore_tmp t WHERE t.ticker=asset_simulation.ticker AND t.Date=asset_simulation.Date),
                overallValueTrend = (SELECT t.overallValueTrend FROM _rescore_tmp t WHERE t.ticker=asset_simulation.ticker AND t.Date=asset_simulation.Date)
            WHERE EXISTS (
                SELECT 1 FROM _rescore_tmp t WHERE t.ticker=asset_simulation.ticker AND t.Date=asset_simulation.Date)
        """)
        sim_conn.execute("DROP TABLE IF EXISTS _rescore_tmp")
        sim_conn.commit()
        logger.info("rescore: done — %d rows updated", len(updates))
    finally:
        sim_conn.close()
        info_conn.close()


class OvtEmaUpdater:
    def __init__(self, conn, ema: str = 9, table_name: str = "asset_simulation"):
        """Bind the updater to an open DB connection and configure the EMA period and table name."""
        self.table_name = table_name
        self.conn = conn
        self.ema = ema

    def ensure_column_exists(self):
        column_name = f"ovtEma{self.ema}"
        with self.conn as conn:
            cursor = conn.cursor()
            # Prüfen ob Spalte bereits existiert
            cursor.execute(f"PRAGMA table_info({self.table_name})")
            columns = [info[1] for info in cursor.fetchall()]
            if column_name not in columns:
                cursor.execute(f"ALTER TABLE {self.table_name} ADD COLUMN {column_name} REAL")
                conn.commit()

    def update_ovt_ema(self):

        self.ensure_column_exists()

        with self.conn as conn:
            query = f"""
            SELECT ticker, Date, overallValueTrend
            FROM {self.table_name}
            ORDER BY ticker, Date
            """
            df = pd.read_sql_query(query, conn, parse_dates=["Date"])

            # EMA pro Ticker berechnen
            df[f"ovtEma{self.ema}"] = (
                df.groupby("ticker")["overallValueTrend"]
                .transform(lambda x: x.ewm(span=self.ema, adjust=False).mean())
            )

            # Temporäre Tabelle erstellen
            tmp_table = "tmp_ovt_ema"
            df["Date"] = df["Date"].dt.strftime("%Y-%m-%d 00:00:00")
            df[["ticker", "Date", f"ovtEma{self.ema}"]].to_sql(tmp_table, conn, if_exists="replace", index=False)

            cursor = conn.cursor()
            update_stmt = f"""
            UPDATE {self.table_name}
            SET ovtEma{self.ema} = (
                SELECT tmp.ovtEma{self.ema}
                FROM {tmp_table} tmp
                WHERE tmp.ticker = {self.table_name}.ticker AND tmp.Date = {self.table_name}.Date
            )
            WHERE EXISTS (
                SELECT 1 FROM {tmp_table} tmp
                WHERE tmp.ticker = {self.table_name}.ticker AND tmp.Date = {self.table_name}.Date
            )
            """
            cursor.execute(update_stmt)
            cursor.execute(f"DROP TABLE {tmp_table}")
            conn.commit()

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

class Sum:

    total_value_weight = 0
    total_weight = 0
    overall_trend = 0
    overall_value_trend = 0

    def up(self, eval, weight=1, points=None, isValue=True): # isValue recognized for overallValueTrend

        if points == None:
            points = weight
            
        self.total_value_weight += weight
        if not isValue:
            self.total_weight += weight
        
        res = eval
        if res:
            self.overall_value_trend += points
            if isValue:
                self.overall_trend += points

        
    def __init__(self):

        pass

def get_mo_trend(df, last_year_high, last_year_low):

    long_ma = 200
    if df['Close'].count() < 200 and df['Close'].count() > 2:
        long_ma = df['Close'].count() - 1 # Let'S calculate at least a long MA 
        df[f'sma{long_ma}'] = df['Close'].rolling(int(long_ma)).mean()

    try:
        trend = 1  # Buy
        ma_long = DataUtils.safe_last(df, f'sma{long_ma}', default=0)
        close_last = DataUtils.safe_last(df, 'Close', default=0)
        ma50 = DataUtils.safe_last(df, 'sma50', default=0)
        ma20 = DataUtils.safe_last(df, 'sma20', default=0)
        first_close = DataUtils.safe_first(df, 'Close', default=0)

        if (ma_long > close_last) or (last_year_high > 0 and last_year_high > close_last) or (ma50 < ma_long):
            trend = 0  # Sell
        elif (close_last - first_close < 0) or (ma20 > close_last) or (ma50 > ma20):
            trend = 0.5  # Hold
    except Exception:
        trend = 0
        pass
    
    return trend

def get_trend(df):

    # we compare sma20, 50 and 200 to evaluate if we should invest
    trend = 0.5 # Hold
    try:
        ma50 = DataUtils.safe_last(df, 'sma50', default=0)
        ma20 = DataUtils.safe_last(df, 'sma20', default=0)
        close_last = DataUtils.safe_last(df, 'Close', default=0)
        ma200 = DataUtils.safe_last(df, 'sma200', default=0)
        if ma50 < close_last or ma20 < close_last:
            trend = 1  # Buy
        if ma50 < ma200:
            trend = 0  # Sell
    except Exception:
        pass
    return trend

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


def fill_pdict(symbol, ticker, df, df_weekly, df_monthly, simulate=True, year=None, ft=None, last_year_high = 0, last_year_low = 0):
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

        sum = Sum()
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
        atc_high = DataUtils.safe_last(df, 'atc_top_high', default=0)
        atc_low = DataUtils.safe_last(df, 'atc_bot_low', default=0)
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
        pdict['currency'] = get_ticker_value(ticker, 'currency')
        pdict['ath'] = df_monthly['Close'].max() if (df_monthly is not None and not df_monthly.empty and 'Close' in df_monthly.columns) else 0
        pdict['dTrend'] = indicator.trend_pct_df(df)
        pdict['moTrend'] = indicator.trend_pct_df(df_monthly, date=pdict['Date'])
        pdict['wkTrend'] = indicator.trend_pct_df(df_weekly, date=pdict['Date'])
        pdict['sharpe'] = indicator.sharpe_ratio(df)
        pdict['sortino'] = indicator.sortino_ratio(df)
        pdict['semiVola'] = indicator.semi_volatility(df)
        pdict['close'] = close
        pdict['atc_high'] = atc_high
        pdict['atc_low'] = atc_low
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

        pdict['predictedLow'] = getattr(ft.pre, 'pr_low', 0)
        pdict['predictedHigh'] = getattr(ft.pre, 'pr_high', 0)
        pdict['dayHigh'] = DataUtils.safe_last(df, 'High', default=0)
        pdict['dayLow'] = DataUtils.safe_last(df, 'Low', default=0)

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

        #up ->  eval, weight=1, points=None, isValue=True
#            sum.up(
#                    1,
#                    1,
#                )
        # if any of the 3 trends is 1 we count is as 3
        sum.up(
                get_mo_trend(df_monthly, last_year_high, last_year_low) + get_trend(df_weekly) + get_trend(df),
                3,
                3,
                isValue=False
            )
        sum.up(
                    df['ewo_angle'].iloc[-1] > 0,
                    0.5,
                    0.5,
                    isValue=False
                )
        sum.up(
                    df['ewo'].iloc[-1] > df['ewo_ema'].iloc[-1],
                    0.5,
                    0.5,
                    isValue=False
                )
        sum.up(
                (pdict['ha_ema_low'] < pdict['close']),
                1,
                1,
                isValue=False
                )
        sum.up(
                (pdict['ha_ema_high'] < pdict['close']),
                1,
                1,
                isValue=False
                )
        sum.up(
                pdict['ha_ema_low'] > pdict['close'],
                1,
                -2,
                isValue=False
                )

        sum.up(
                pdict['trendDirection'] >= 1,
                3,
                3,
                isValue=False
                )

        sum.up(
                float(pdict['vola']) / 2 < float(pdict['moTrend']),
                1,
                1,
                isValue=False
                )

        sum.up(
                pdict['moTrend'] > pdict['vola'],
                1,
                1,
                isValue=False
                )

        sum.up(
                True,
                1,
                pdict['macd_trend'],
                isValue=False
                )

        sum.up(
                True,
                1,
                pdict['macd_trend_wk'],
                isValue=False
                )

        sum.up(
                True,
                1,
                pdict['macd_trend_mo'],
                isValue=False
                )

        sum.up(
                pdict['roa'] > 0.2,
                1,
                1
                )

        sum.up(
                pdict['roa'] < 0.05,
                0,
                -1
                )

        sum.up(
                pdict['logVola'] < 0.1,
                
                )
            
        forwardEps = get_ticker_value(ticker, 'forwardEps')  
        try:
            sum.up(
                forwardEps > 0.1,
                0.5,
                0.5
                )
        except Exception:
            pass

        forwardPE = get_ticker_value(ticker, 'forwardPE')  
        try:
            if forwardPE < 25 and forwardPE > 0:
                sum.up(
                    True,
                    0.5,
                    0.5
                    )
        except Exception:
            forwardPE = 0
            pass

        try:
            if forwardPE == 0:
                if get_ticker_value(ticker, 'trailingPE') < 25 and get_ticker_value(ticker, 'trailingPE') > 0:
                    sum.up(
                        True,
                        0.5,
                        0.5
                        )
        except Exception:
            pass   

        sum.up(
            pdict['pctTargetHighPrice'] > 10,
            1,
            0.25
            )
            
        sum.up(
            pdict['pctTargetHighPrice'] > 20,
            0,
            0.25
            )

        sum.up(
            pdict['pctTargetHighPrice'] > 30,
            0,
            0.25
            )
            
        sum.up(
            pdict['pctTargetHighPrice'] > 40,
            1,
            0.25
            )

        sum.up(
            pdict['pctTargetHighPrice'] < 0,
            0,
            -2
            )

        sum.up(
            get_ticker_value(ticker, 'dividendRate') > 0,
            0.5,
            0.5
            )

        sum.up(
            get_ticker_value(ticker, 'earningsGrowth') > 0,
            0.5,
            0.5
            )

            
        sum.up(
            get_ticker_value(ticker, 'operatingMargins') > 0,
            0.5,
            0.5
            )

        sum.up(
            get_ticker_value(ticker, 'priceToBook') > 50,
            1,
            1
            )

        sum.up(
            get_ticker_value(ticker, 'priceToBook') < 5,
            0,
            0.5
            )

        sum.up(
            get_ticker_value(ticker, 'priceToBook') < 0,
            0,
            0.5
            )

        try:
            tv = get_ticker_value(ticker, 'totalDebt')
            if not tv == 0:
                sum.up(
                    get_ticker_value(ticker, 'enterpriseValue') / tv > 3, #get_ticker(ticker,'enterpriseValue') / get_ticker(ticker,'totalDebt') > 3:
                    1,
                    1
                )
        except Exception:
            sum.up(
                True,
                1,
                0
                )
            pass

        sum.up(
            get_ticker_value(ticker, 'averageVolume') > 1000000,
            1,
            0.5
            )
            
        sum.up(
            get_ticker_value(ticker, 'averageVolume') > 10000000,
            0,
            0.5,
            isValue=False
            )
            
        sum.up(
            (20 <= pdict['rsi'] <= 80),
            0.5,
            0.5
            )

        sum.up(
            pdict['sharpe'] > 1,
            0.5,
            0.5
            )

        sum.up(
            pdict['sharpe'] > 2,
            0.5,
            0.5
            )

        sum.up(
            pdict['sortino'] > 1,
            0.5,
            0.5,
            isValue=False
            )

        sum.up(
            pdict['predictedHigh'] < df['Low'].iloc[-1],
            0.5,
            0.5
            )
        
        # is price continously falling?
        pr_low_r1 = pdict['predictedLow'] * 0.99
        pr_low_r2 = pdict['predictedLow'] * 0.94
        df_d_last = df[-5:-1]
        pr_highest_h = df_d_last['High'].max()

        lt = df['lowTrend'].iloc[-1]
        if not lt == 0: 
            sum.up(
                    pr_highest_h < pr_low_r1 and pr_highest_h > pr_low_r2 and df['highTrend'].iloc[-6] > df['highTrend'].iloc[-1] and df['lowTrend'].iloc[-5] > df['lowTrend'].iloc[-1] and (df['lowTrend'].iloc[-5] / lt) > 1,
                    2,
                    -2,
                    isValue=False
                ) 
        
        # is price continously rising?
        pr_high_r1 = pdict['predictedHigh'] * 1.01
        pr_high_r2 = pdict['predictedHigh'] * 1.06
        df_d_last = df[-5:-1]
        pr_lowest_h = df_d_last['Low'].min()
        lt = df['lowTrend'].iloc[-1]
        if not lt == 0:
            sum.up(
                    pr_lowest_h > pr_high_r1 and pr_lowest_h < pr_high_r2 and df['highTrend'].iloc[-5] < df['highTrend'].iloc[-1] and df['lowTrend'].iloc[-6] < df['lowTrend'].iloc[-1] and (df['lowTrend'].iloc[-5] / lt) < 1,
                    0,
                    2,                    
                    isValue=False
                )

        pdict['overallTrend'] = 0
        if not sum.total_weight == 0:
            # harmonize to -100 to + 100%
            pdict['overallTrend'] = round(max(-100, min(100, sum.overall_trend *100/ sum.total_weight)))
            
        pdict['overallValueTrend'] = 0
        if not sum.total_value_weight == 0:
            # harmonize to -100 to + 100%
            pdict['overallValueTrend'] = round(max(-100, min(100, sum.overall_value_trend *100/ sum.total_value_weight)))

        return pdict

    except Exception as e:
        import traceback
        print(f"⚠️ fill_pdict exception for {symbol}: {e}")
        print(traceback.format_exc())
        return None


def process_symbol(symbol, simulate=True, add_current=False, year='', init=False):
    """Funktion, die pro Ticker in einem separaten Prozess läuft."""
    from tradinglib import fetch_data, indicator, ticker_tools as tt  # 🔹 Lokale Imports innerhalb des Prozesses
    from tradinglib.utils import DataUtils
    ft = fetch_data.FetchData(indicators=[ 'adx', 'macd', 'rsi', 'stoch', 'cci', 'fvg', 'bos', 'vol', 'don', 'fib', 'bol', 'gan', 'sup', 'pre', 'ewo','vwap','lqz','ici','bsz','heikin', 'atc', 'candle', 'zcr', 'relvol','dema','hor','qtrend','markov'])
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

        for date in df.index:
            res_df_d = df_r.loc[startdate:date]
            res_df_w = df_w.loc[:date] if df_w is not None else pd.DataFrame()
            res_df_m = df_m.loc[:date] if df_m is not None else pd.DataFrame()
            pdict = fill_pdict(symbol, ticker, res_df_d, res_df_w, res_df_m, year=year, ft=ft, last_year_high=last_year_high, last_year_low=last_year_low)
            if pdict:
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

    sys_conf = sysconf.SystemConfig(username="admin")
    system_currency = sys_conf.get_value("system_currency", "EUR")
    db_table = "asset_simulation"
    if all:
        sim_db_name = f"{db_table}_all.db"
    else:
        sim_db_name = f"{db_table}_{year}.db"

    # -----------------------------------------------------------------------
    # Fast modes — no yfinance, early exit
    # -----------------------------------------------------------------------
    rescore_flag   = args.get('rescore', False)
    backfill_inds  = args.get('backfill', None)   # list[str] or None

    if rescore_flag:
        _t = _Tools()
        sim_db_path  = _t.get_path(path='database', file_name=sim_db_name)
        info_db_path = _t.get_path(path='database', file_name='asset_info.db')
        logger.info("rescore mode: %s", sim_db_path)
        rescore_db(sim_db_path, info_db_path)
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
        exit(0)
    # -----------------------------------------------------------------------

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
            # /index:NAME wertet ausschließlich diesen einen Index aus. Das frühere
            # pauschale Anhängen von NON_STOCK_GROUPS ist ein Relikt aus der Zeit vor
            # /group und entfällt — Kategorie-Gruppen laufen jetzt über /group:NAME.
            ticker_query = f'SELECT s.Ticker FROM stocks s JOIN stock_indices si ON s.id = si.stock_id JOIN indices i ON si.index_id = i.id WHERE i.name = "{index_name}"'
    if all:
        ticker_query = f'SELECT Ticker FROM stocks;'
        logger.info("Using all tickers")
        simulate = True

    logger.debug("Running query: %s", ticker_query)
    tickers = pd.read_sql_query(ticker_query, info_db.conn)["Ticker"].unique().tolist()
    tickers = list(set(tickers))
    logger.info("%d tickers found.", len(tickers))
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
        futures = {executor.submit(process_symbol, sym, True, add_current, year=year, init=init): sym for sym in tickers}
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

    if year == '' and not all:
        if simulate and not silent:
            logger.info("notifying via pushover...")
            if mt is not None:
                mt.run_notifier()
            else:
                logger.warning("Pushover-Notifier nicht konfiguriert -- Benachrichtigung uebersprungen.")

