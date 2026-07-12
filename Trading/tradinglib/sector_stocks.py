"""Sector Stocks — Best of Sector backend.

Queries local SQLite databases for stocks in a given GICS sector and
optionally enriches the result with Relative Strength vs the parent sector ETF.

Stage 1 — DB metrics only  (fast, no internet):
    query_sector_stocks(sector, rank_col, limit)

Stage 2 — RSC enrichment   (downloads ~3 months of price data):
    enrich_with_rsc(df, sector_etf, weeks=4)
"""
import logging
import os
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

from tradinglib import market_data as md
from tradinglib.tools import open_db, attach_db
from tradinglib.tools import Tools

logger = logging.getLogger(__name__)

# ─── Sector → ETF mapping (yfinance GICS names → SPDR ETF tickers) ────────────
#
# yfinance GICS names differ from the SPDR short labels used in sector_rotation.py:
#   "Healthcare"             (yf) ≠ "Health Care"       (SPDR label)
#   "Financial Services"     (yf) ≠ "Financials"         (SPDR label)
#   "Consumer Cyclical"      (yf) ≠ "Consumer Discret."  (SPDR label)
#   "Consumer Defensive"     (yf) ≠ "Consumer Staples"   (SPDR label)
#   "Communication Services" (yf) ≠ "Communication"      (SPDR label)
#   "Basic Materials"        (yf) ≠ "Materials"           (SPDR label)

SECTOR_ETF_MAP: dict[str, str] = {
    "Technology":             "XLK",
    "Financial Services":     "XLF",
    "Healthcare":             "XLV",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Energy":                 "XLE",
    "Utilities":              "XLU",
    "Basic Materials":        "XLB",
    "Industrials":            "XLI",
    "Real Estate":            "XLRE",
    "Communication Services": "XLC",
}

# ─── Ranking options (display label → SQL column expression) ──────────────────

RANK_OPTIONS: dict[str, str] = {
    "Overall Value Trend": "ap.overallValueTrend",
    "Sortino Ratio":       "ap.sortino",
    "Sharpe Ratio":        "ap.sharpe",
    "Monthly Trend %":     "ap.moTrend",
    "Weekly Trend %":      "ap.wkTrend",
    "Daily Trend %":       "ap.dTrend",
    "Momentum":            "ap.momentum",
    "Overall Trend":       "ap.overallTrend",
}

# Whitelist for SQL injection prevention
_RANK_COLUMNS: frozenset[str] = frozenset(RANK_OPTIONS.values())

# We use  ap.*  to avoid assuming any particular schema for asset_simulation —
# different DB generations may have different columns.  Only the three ai. columns
# below are treated as guaranteed to exist in asset_info.
_AI_COLS = ("ai.longName", "ai.exchange", "ai.sector")


# ─── Path resolution ──────────────────────────────────────────────────────────

_tools = Tools()


def _db_path(db_path: str, filename: str) -> str:
    """Resolve a database file path — tries three strategies in order.

    1. Current working directory (most reliable when running via `streamlit run`)
    2. Tools.get_path() (uses sys.argv[0] — works for CLI scripts)
    3. Path relative to this file (fallback for packaged installs)
    """
    fn = os.path.basename(filename)

    # Strategy 1: CWD — when Streamlit is started from the project root this
    # is always correct regardless of sys.argv[0].
    p1 = os.path.join(os.getcwd(), db_path, fn)
    if os.path.exists(p1):
        return p1

    # Strategy 2: Tools.get_path() (honours TradingDB env var, then sys.argv[0])
    try:
        p2 = _tools.get_path(path=db_path, file_name=fn)
        if os.path.exists(p2):
            return p2
    except Exception:
        pass

    # Strategy 3: relative to this file's parent directory
    p3 = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        db_path, fn,
    )
    if os.path.exists(p3):
        return p3

    # None found — return the CWD-based path so the caller gets a useful error
    return p1


# ─── Public API ───────────────────────────────────────────────────────────────

def get_available_sectors(db_path: str = "database") -> list[str]:
    """Return distinct GICS sector names present in asset_info.db.

    Falls back to the SECTOR_ETF_MAP keys if the DB is not reachable.
    """
    info_file = _db_path(db_path, "asset_info.db")
    if not os.path.exists(info_file):
        logger.debug("asset_info.db not found at %s — using static fallback", info_file)
        return sorted(SECTOR_ETF_MAP.keys())

    try:
        with open_db(info_file, readonly=True) as conn:
            cur = conn.execute(
                "SELECT DISTINCT sector FROM asset_info "
                "WHERE sector IS NOT NULL AND sector != '' "
                "ORDER BY sector"
            )
            sectors = [row[0] for row in cur.fetchall()]
        return sectors if sectors else sorted(SECTOR_ETF_MAP.keys())
    except Exception as exc:
        logger.warning("get_available_sectors failed: %s", exc)
        return sorted(SECTOR_ETF_MAP.keys())


def query_sector_stocks(
    sector: str,
    db_path: str = "database",
    rank_col: str = "ap.overallValueTrend",
    limit: int = 30,
) -> tuple[pd.DataFrame, str]:
    """Query the top *limit* stocks in *sector* ranked by *rank_col*.

    Tries ``asset_simulationall.db`` first; falls back to ``asset_simulation.db``.

    Returns:
        (DataFrame, debug_info)  — DataFrame is empty on failure, debug_info
        contains a human-readable description of what was tried and why it failed.
    """
    if rank_col not in _RANK_COLUMNS:
        logger.warning("Invalid rank_col %r — using ap.overallValueTrend", rank_col)
        rank_col = "ap.overallValueTrend"

    # Build guaranteed-safe SELECT: ap.* captures whatever columns the simulation
    # table actually has (schema varies by DB generation); ai. adds the three
    # identity columns that are known to exist in every asset_info build.
    ai_expr = ", ".join(_AI_COLS)

    debug_lines: list[str] = []

    for db_name in ("asset_simulation_all", "asset_simulation_", "asset_simulation"):
        sim_file  = _db_path(db_path, f"{db_name}.db")
        info_file = _db_path(db_path, "asset_info.db")

        debug_lines.append(f"Trying **{db_name}.db**")
        debug_lines.append(f"  sim:  `{sim_file}` — exists: {os.path.exists(sim_file)}")
        debug_lines.append(f"  info: `{info_file}` — exists: {os.path.exists(info_file)}")

        if not os.path.exists(sim_file):
            debug_lines.append("  → skipped (file not found)")
            continue

        try:
            conn = open_db(sim_file)
            attach_db(conn, info_file, "info_db")

            # Fetch the latest date as a DATE string first.
            # DATE() strips time-of-day so we match all records for the newest day,
            # not just the row with the exact MAX timestamp.
            cur = conn.execute("SELECT DATE(MAX(Date)) FROM asset_simulation")
            row = cur.fetchone()
            if not row or row[0] is None:
                debug_lines.append("  → skipped (MAX(Date) returned NULL — table is empty?)")
                conn.close()
                continue

            max_date = row[0]
            debug_lines.append(f"  max date: {max_date}, sector filter: '{sector}'")

            # NULLS LAST is SQLite ≥ 3.30 only; use a portable CASE expression instead.
            query = f"""
                SELECT ap.*, {ai_expr}
                FROM asset_simulation AS ap
                INNER JOIN info_db.asset_info AS ai ON ap.ticker = ai.ticker
                WHERE ai.sector = ?
                  AND DATE(ap.Date) = ?
                ORDER BY
                    CASE WHEN {rank_col} IS NULL THEN 1 ELSE 0 END,
                    {rank_col} DESC
                LIMIT ?
            """
            df = pd.read_sql_query(query, conn, params=(sector, max_date, limit))
            conn.close()

            debug_lines.append(f"  → {len(df)} rows returned")

            if not df.empty:
                return df, "\n".join(debug_lines)

        except Exception as exc:
            debug_lines.append(f"  → ERROR: {exc}")
            logger.warning("query_sector_stocks(%s): %s", db_name, exc)
            try:
                conn.close()
            except Exception:
                pass

    return pd.DataFrame(), "\n".join(debug_lines)


def enrich_with_rsc(
    df: pd.DataFrame,
    sector_etf: str,
    weeks: int = 4,
) -> pd.DataFrame:
    """Append RSC_vs_ETF column: stock return minus sector ETF return over N weeks.

    * Positive value → stock outperforms its sector ETF (genuine alpha).
    * Uses *weeks* × 5 trading days as the look-back window.
    * Downloads ~3 months of daily data via market_data.download().

    Args:
        df:         DataFrame returned by query_sector_stocks (must have 'ticker' column).
        sector_etf: Ticker symbol for the sector ETF (e.g. 'XLK').
        weeks:      Look-back in weeks (default 4 ≈ 1 month).

    Returns:
        A copy of *df* with an added ``RSC_vs_ETF`` column (float, in %).
    """
    if df.empty or "ticker" not in df.columns:
        return df

    tickers = list(df["ticker"].unique())
    all_tickers = tickers + [sector_etf]
    n_days = max(weeks * 5, 10)
    yf_period = "6mo" if weeks > 12 else "3mo"

    try:
        raw = md.download(
            tickers=all_tickers,
            period=yf_period,
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
    except Exception as exc:
        logger.warning("enrich_with_rsc download failed: %s", exc)
        result = df.copy()
        result["RSC_vs_ETF"] = float("nan")
        return result

    if raw is None or raw.empty:
        result = df.copy()
        result["RSC_vs_ETF"] = float("nan")
        return result

    def _get_close(ticker: str) -> Optional[pd.Series]:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                s = raw["Close"][ticker].dropna()
            else:
                s = raw["Close"].dropna()
            return s if not s.empty else None
        except (KeyError, Exception):
            return None

    etf_close = _get_close(sector_etf)

    rsc_map: dict[str, float] = {}
    for ticker in tickers:
        try:
            stk_close = _get_close(ticker)
            if stk_close is None or etf_close is None:
                rsc_map[ticker] = float("nan")
                continue

            aligned = pd.concat([stk_close, etf_close], axis=1, join="inner").dropna()
            if len(aligned) < n_days + 1:
                rsc_map[ticker] = float("nan")
                continue

            aligned.columns = ["stock", "etf"]
            stk_ret = (aligned["stock"].iloc[-1] / aligned["stock"].iloc[-n_days] - 1) * 100
            etf_ret = (aligned["etf"].iloc[-1]   / aligned["etf"].iloc[-n_days]   - 1) * 100
            rsc_map[ticker] = round(float(stk_ret - etf_ret), 2)

        except Exception as exc:
            logger.debug("RSC calc failed for %s: %s", ticker, exc)
            rsc_map[ticker] = float("nan")

    result = df.copy()
    result["RSC_vs_ETF"] = result["ticker"].map(rsc_map)
    return result
