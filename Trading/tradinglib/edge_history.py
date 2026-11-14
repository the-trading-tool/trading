"""Wochen-Verlauf der Edge Scores: Gesamt-Score und Komponenten je Markt und Freitag.

Gespeichert in ``<TradingDB>/edge_scores.db``, Tabelle ``edge_weekly`` (PK date+key).
Jeder Punkt wird kausal gerechnet wie der Verlauf der Asset-Scorecard
(:func:`asset_card_data.history`): Trend nur aus Kursen bis zum Freitag, COT ab
Veroeffentlichung, Makro nur mit Werten, die zu dem Tag (geschaetzt) schon
veroeffentlicht waren. Gespeichert werden nur abgeschlossene Freitage (vor dem
Lauftag), damit ein Punkt beim naechsten Lauf nicht anders aussieht.

Der Lauf ist inkrementell: je Markt werden nur die Freitage nach dem letzten
gespeicherten gerechnet; ein leerer Markt wird ``BACKFILL_WEEKS`` zurueck gefuellt (alle Maerkte ~95 s).

CLI (oder Scheduler, ueber ``log_edge_scores.py``)::

    python log_edge_scores.py                  # fehlende Freitage nachtragen
    python log_edge_scores.py /weeks:260       # Erstbefuellung tiefer
    python log_edge_scores.py /force /weeks:8  # letzte 8 Wochen neu rechnen
    python log_edge_scores.py /key:DAX,GOLD    # nur diese Maerkte
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3

import pandas as pd

from tradinglib import asset_card_data as ac
from tradinglib import cot_data as cd
from tradinglib import macro_data as md
from tradinglib import trend_data as td

logger = logging.getLogger(__name__)

DB_NAME = "edge_scores.db"
BACKFILL_WEEKS = 364               # ~7 years: from late 2019, COT history starts 2018

_SCHEMA = """
CREATE TABLE IF NOT EXISTS edge_weekly (
    date TEXT NOT NULL,
    key TEXT NOT NULL,
    grp TEXT,
    cot REAL, macro REAL, trend REAL,
    total REAL, bias TEXT, n INTEGER,
    computed_at TEXT,
    PRIMARY KEY (date, key)
)"""


def db_path() -> str:
    return os.path.join(os.environ.get("TradingDB") or "database", DB_NAME)


def _connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or db_path())
    con.execute(_SCHEMA)
    return con


def last_friday(day: dt.date) -> dt.date:
    """Letzter Freitag VOR ``day`` (ein Freitag selbst zaehlt erst ab Samstag)."""
    back = (day.weekday() - 4) % 7 or 7
    return day - dt.timedelta(days=back)


def last_dates(con: sqlite3.Connection) -> dict[str, dt.date]:
    rows = con.execute("SELECT key, MAX(date) FROM edge_weekly GROUP BY key").fetchall()
    return {k: dt.date.fromisoformat(d) for k, d in rows if d}


def weeks_needed(last: dt.date | None, upto: dt.date, backfill: int) -> int:
    """Anzahl Freitage bis ``upto`` (inkl.), die nach ``last`` fehlen."""
    if last is None:
        return backfill
    return max(0, (upto - last).days // 7)


def market_rows(key: str, group: str, upto: dt.date, weeks: int, obs: pd.DataFrame | None) -> pd.DataFrame:
    """Kausale Wochenpunkte eines Marktes fuer die ``weeks`` Freitage bis ``upto``."""
    if weeks <= 0:
        return pd.DataFrame()
    s, _src = td.price_series(key, upto + dt.timedelta(days=1))
    if s.empty:
        logger.warning("edge history: no prices for %s", key)
        return pd.DataFrame()
    s = s[s.index <= pd.Timestamp(upto)]
    cot_hist = cd.load_history(key) if ac.has_cot(key) else pd.DataFrame()
    # today = Samstag nach upto -> alle Punkte liegen in der Vergangenheit (Makro mit Lag)
    h = ac.history(key, s, cot_hist, obs if ac.has_macro(key) else None,
                   weeks=weeks, today=upto + dt.timedelta(days=1))
    h = h[h["date"] <= pd.Timestamp(upto)].copy()
    h["key"], h["grp"] = key, group
    h["n"] = [ac.n_components(c, m, t) for c, m, t in zip(h["cot"], h["macro"], h["trend"])]
    return h


def save(con: sqlite3.Connection, rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    now = dt.datetime.now().isoformat(timespec="seconds")

    def f(v):
        return None if v is None or pd.isna(v) else float(v)

    data = [(r.date.date().isoformat(), r.key, r.grp, f(r.cot), f(r.macro), f(r.trend), f(r.total),
             r.bias, int(r.n), now) for r in rows.itertuples()]
    con.executemany("INSERT OR REPLACE INTO edge_weekly (date, key, grp, cot, macro, trend, total, bias, n, "
                    "computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)", data)
    con.commit()
    return len(data)


def update(today: dt.date | None = None, keys: list[str] | None = None, weeks: int | None = None,
           force: bool = False, path: str | None = None) -> dict:
    """Fehlende (oder mit ``force`` die letzten ``weeks``) Freitage je Markt rechnen und speichern."""
    today = today or dt.date.today()
    upto = last_friday(today)
    try:
        cd.ensure_fresh()
        md.ensure_fresh()
    except Exception:
        logger.exception("edge history: refresh of COT/macro failed, using stored data")
    obs = md.load_obs()
    con = _connect(path)
    stats = {"upto": upto.isoformat(), "rows": 0, "markets": 0, "failed": []}
    try:
        last = last_dates(con)
        for key, group in ac.markets():
            if keys and key not in keys:
                continue
            n = weeks if force and weeks else weeks_needed(last.get(key), upto, weeks or BACKFILL_WEEKS)
            if n <= 0:
                continue
            try:
                written = save(con, market_rows(key, group, upto, n, obs))
            except Exception:
                logger.exception("edge history: %s failed", key)
                stats["failed"].append(key)
                continue
            stats["rows"] += written
            stats["markets"] += bool(written)
            logger.info("edge history: %s %d week(s)", key, written)
    finally:
        con.close()
    return stats


def load(keys: list[str] | None = None, since: dt.date | None = None, path: str | None = None) -> pd.DataFrame:
    """Gespeicherte Wochenpunkte (nur lesend); leer, wenn es die DB noch nicht gibt."""
    p = path or db_path()
    if not os.path.exists(p):
        return pd.DataFrame()
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        q, params = "SELECT * FROM edge_weekly WHERE 1=1", []
        if keys:
            q += f" AND key IN ({','.join('?' * len(keys))})"
            params += list(keys)
        if since:
            q += " AND date >= ?"
            params.append(since.isoformat())
        df = pd.read_sql_query(q + " ORDER BY key, date", con, params=params)
    except sqlite3.Error:
        return pd.DataFrame()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    return df
