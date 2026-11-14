"""Kreditaufschlaege (Credit Spreads) als Marktumfeld: Kosten der Geldbeschaffung fuer Unternehmen.

Quelle FRED ``fredgraph.csv`` (ohne Key), gespeichert in ``<TradingDB>/macro.db`` Tabelle
``credit_daily``:

=============== ========================================================= ================
Reihe           Inhalt                                                    Historie
=============== ========================================================= ================
``BAA10Y``      Moody's Baa-Unternehmensanleihen minus 10-j. US-Treasury   taeglich ab 1986
``BAMLH0A0HYM2`` ICE BofA US High Yield OAS                               nur ~3 Jahre*
=============== ========================================================= ================

\\* FRED gibt die ICE-Reihen seit 2023 nur noch fuer drei Jahre heraus — fuer eine Messung zu
kurz, deshalb nur Kontext. Gemessen und bewertet wird ``BAA10Y``.

Merkmale (kausal, ein Handelstag Veroeffentlichungsverzug — FRED stellt den Wert von Tag D
am Tag D+1 ein):

* ``rank3y``: Perzentil des Spreads in den letzten 756 Handelstagen (3 Jahre, inkl. heute)
* ``chg13``: Veraenderung in Prozentpunkten ueber 63 Handelstage (13 Wochen)
* ``score`` −2 … +2: Niveau (Rang <= 20 % → +1, >= 80 % → −1) + Richtung (chg13 <= −0,20 Pp
  → +1, >= +0,20 Pp → −1). Positiv = Geld fuer Unternehmen billig bzw. billiger werdend.
  Die Schwellen sind vor der Messung festgelegt, nicht angepasst.

CLI: ``python -m tradinglib.credit_data``
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import os
import sqlite3
from contextlib import closing

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SERIES = {"BAA10Y": "Baa − 10Y Treasury", "BAMLH0A0HYM2": "US High Yield OAS"}
MAIN = "BAA10Y"
RANK_DAYS = 756
CHG_DAYS = 63
CHG_THRESHOLD = 0.20       # Pp
RANK_LOW, RANK_HIGH = 0.20, 0.80
LAG_DAYS = 1               # FRED publishes day D on D+1
REFRESH_HOURS = 20
_TIMEOUT = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS credit_daily (
    series TEXT NOT NULL, date TEXT NOT NULL, value REAL,
    PRIMARY KEY (series, date)
);
CREATE TABLE IF NOT EXISTS credit_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def db_path() -> str:
    return os.path.join(os.environ.get("TradingDB") or "database", "macro.db")


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.executescript(_SCHEMA)
    return con


def fetch_series(sid: str) -> pd.Series:
    """Ganze Reihe von FRED (fehlende Tage '.' fallen weg)."""
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid}, timeout=_TIMEOUT)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    return pd.Series(df["value"].to_numpy(), index=pd.to_datetime(df["date"]), name=sid)


def fetch() -> dict[str, str]:
    """Alle Reihen laden und speichern; Status je Reihe."""
    status = {}
    with closing(_connect()) as con:
        for sid in SERIES:
            try:
                s = fetch_series(sid)
                con.executemany("INSERT OR REPLACE INTO credit_daily (series, date, value) VALUES (?,?,?)",
                                [(sid, d.date().isoformat(), float(v)) for d, v in s.items()])
                status[sid] = f"ok {len(s)}"
            except Exception as e:
                logger.warning("credit: %s failed: %s", sid, e)
                status[sid] = f"error {e}"
        con.execute("INSERT OR REPLACE INTO credit_meta VALUES ('last_attempt', ?)",
                    (dt.datetime.now().isoformat(timespec="seconds"),))
        if any(v.startswith("ok") for v in status.values()):
            con.execute("INSERT OR REPLACE INTO credit_meta VALUES ('last_success', ?)",
                        (dt.datetime.now().isoformat(timespec="seconds"),))
        con.commit()
    return status


def get_meta(key: str) -> str | None:
    if not os.path.exists(db_path()):
        return None
    try:
        with closing(sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)) as con:
            row = con.execute("SELECT value FROM credit_meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def ensure_fresh() -> bool:
    """Hoechstens einmal je ``REFRESH_HOURS`` neu laden. True = geladen."""
    last = get_meta("last_attempt")
    if last and dt.datetime.now() - dt.datetime.fromisoformat(last) < dt.timedelta(hours=REFRESH_HOURS):
        return False
    try:
        fetch()
        return True
    except Exception as e:
        logger.warning("credit: refresh failed: %s", e)
        return False


def load(sid: str = MAIN) -> pd.Series:
    """Gespeicherte Reihe (nur lesend); leer ohne DB."""
    if not os.path.exists(db_path()):
        return pd.Series(dtype=float, name=sid)
    try:
        with closing(sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)) as con:
            df = pd.read_sql_query("SELECT date, value FROM credit_daily WHERE series = ? ORDER BY date",
                                   con, params=(sid,))
    except Exception:
        return pd.Series(dtype=float, name=sid)
    return pd.Series(df["value"].to_numpy(), index=pd.to_datetime(df["date"]), name=sid)


# ── Merkmale & Score (rein, testbar ohne Netz) ────────────────────────────────

def features(s: pd.Series) -> pd.DataFrame:
    """Spread, rank3y, chg13, score je Beobachtungstag (Index = Beobachtungsdatum)."""
    s = s.dropna().sort_index()
    out = pd.DataFrame({"spread": s})
    out["rank3y"] = s.rolling(RANK_DAYS, min_periods=RANK_DAYS // 3).rank(pct=True)
    out["chg13"] = s - s.shift(CHG_DAYS)
    lvl = (out["rank3y"] <= RANK_LOW).astype(int) - (out["rank3y"] >= RANK_HIGH).astype(int)
    chg = (out["chg13"] <= -CHG_THRESHOLD).astype(int) - (out["chg13"] >= CHG_THRESHOLD).astype(int)
    out["score"] = (lvl + chg).where(out["rank3y"].notna() & out["chg13"].notna())
    return out


def known_at(feat: pd.DataFrame, dates) -> pd.DataFrame:
    """Merkmale, die am Tag ``dates`` bekannt waren (Beobachtung <= Tag − LAG_DAYS)."""
    left = pd.DataFrame({"d": pd.to_datetime(pd.Index(dates)).normalize()})
    left["k"] = left["d"] - pd.Timedelta(days=LAG_DAYS)
    right = feat.reset_index().rename(columns={feat.index.name or "index": "k"}).sort_values("k")
    m = pd.merge_asof(left.reset_index().sort_values("k"), right, on="k", direction="backward")
    return m.sort_values("index").set_index("d").drop(columns=["index", "k"])


def bias_label(score: float | None) -> str:
    if score is None or pd.isna(score):
        return "neutral"
    return "strong_bull" if score >= 2 else "bull" if score >= 1 else \
        "strong_bear" if score <= -2 else "bear" if score <= -1 else "neutral"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch())
    f = features(load())
    print(f.tail(3).to_string())
