"""Marktumfeld je Titel: Edge-Gesamt-Score seines Index als Tagesreihe.

Quelle ist der Wochen-Verlauf ``edge_scores.db / edge_weekly`` (kausal je Freitag,
:mod:`tradinglib.edge_history`). Ein Tag D traegt den Score des letzten Freitags
F <= D: der Freitags-Score nutzt den Freitagsschluss, also dieselbe Information wie
ein Signal auf diesem Schluss.

Zuordnung Titel -> Index ueber ``yf_tickers.db`` (stock_indices). Bei mehreren
Indizes gewinnt der erste in :data:`INDEX_PRIORITY` (breiter / nationaler Index vor
Teil- oder Regionalindex). Titel ohne zugeordneten Edge-Markt bekommen nichts.

Spalten in ``asset_simulation``: ``edge_market`` (−10 … +10) und ``edge_market_ok``
(1 = berechnet). Ohne ``edge_market_ok`` ist 0 doppeldeutig (neutral oder nicht
zugeordnet), weil neue Spalten mit DEFAULT 0 entstehen.
"""
from __future__ import annotations

import logging
import os
import sqlite3

import pandas as pd

from tradinglib import edge_history as eh

logger = logging.getLogger(__name__)

# Index name in yf_tickers.db -> Edge market key, in priority order.
INDEX_PRIORITY: list[tuple[str, str]] = [
    ("^SPX", "SPX"), ("^RUT", "RUT"), ("^NDX", "NDX"), ("^DJI", "DJI"),
    ("^GDAXI", "DAX"), ("^MDAXI", "MDAX"), ("^FTSE", "FTSE"), ("^IBEX", "IBEX"),
    ("^SSMI", "SMI"), ("^N225", "N225"), ("^KS200", "KOSPI"), ("^ASXJO", "ASX200"),
    ("^HSI", "HSI"), ("^BVSP", "BOVESPA"), ("^STOXX50E", "ESTX50"),
]
COLUMNS = ["edge_market", "edge_market_ok"]


def _db_dir() -> str:
    return os.environ.get("TradingDB") or "database"


def ticker_markets(tickers: list[str] | None = None) -> dict[str, str]:
    """{ticker: Edge-Key} fuer alle Titel mit zuordenbarem Index (nur lesend)."""
    path = os.path.join(_db_dir(), "yf_tickers.db")
    if not os.path.exists(path):
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(
            "SELECT s.Ticker AS ticker, i.name AS idx FROM stocks s "
            "JOIN stock_indices si ON s.id = si.stock_id JOIN indices i ON i.id = si.index_id", con)
    finally:
        con.close()
    rank = {name: n for n, (name, _k) in enumerate(INDEX_PRIORITY)}
    key = dict(INDEX_PRIORITY)
    df = df[df["idx"].isin(rank)]
    if tickers is not None:
        df = df[df["ticker"].isin(set(tickers))]
    df = df.assign(r=df["idx"].map(rank)).sort_values("r").drop_duplicates("ticker")
    return {t: key[i] for t, i in zip(df["ticker"], df["idx"])}


def weekly_scores(keys: list[str] | None = None) -> pd.DataFrame:
    """Freitags-Scores (date, key, total) aus edge_scores.db."""
    df = eh.load(keys=keys)
    return df[["date", "key", "total"]].dropna() if not df.empty else df


def series_for(dates: pd.DatetimeIndex, key: str | None, weekly: pd.DataFrame) -> pd.Series:
    """Score je Tag: letzter Freitag <= Tag. NaN vor dem ersten Freitag / ohne Markt."""
    if key is None or weekly.empty:
        return pd.Series(float("nan"), index=dates)
    w = weekly[weekly["key"] == key].sort_values("date")
    if w.empty:
        return pd.Series(float("nan"), index=dates)
    left = pd.DataFrame({"d": pd.to_datetime(dates).normalize()}).reset_index()
    out = pd.merge_asof(left.sort_values("d"), w.rename(columns={"date": "d"})[["d", "total"]],
                        on="d", direction="backward").sort_values("index")
    return pd.Series(out["total"].to_numpy(), index=dates)


def attach(panel: pd.DataFrame, weekly: pd.DataFrame | None = None,
           mapping: dict[str, str] | None = None) -> pd.DataFrame:
    """``edge_market``/``edge_mkt_key`` an einen Frame mit ticker/Date haengen (Messung)."""
    weekly = weekly_scores() if weekly is None else weekly
    mapping = ticker_markets(list(panel["ticker"].unique())) if mapping is None else mapping
    df = panel.copy()
    df["edge_mkt_key"] = df["ticker"].map(mapping)
    w = weekly.rename(columns={"date": "Date", "key": "edge_mkt_key", "total": "edge_market"})
    w = w.sort_values("Date")
    left = df[["Date", "edge_mkt_key"]].reset_index().dropna(subset=["edge_mkt_key"]).sort_values("Date")
    m = pd.merge_asof(left, w, on="Date", by="edge_mkt_key", direction="backward")
    df["edge_market"] = m.set_index("index")["edge_market"].reindex(df.index)
    return df
