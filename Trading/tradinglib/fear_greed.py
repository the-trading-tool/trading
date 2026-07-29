"""Silvesto Fear & Greed Index.

Aggregiert Markt-Breite (über die Indexmitglieder in asset_simulation) und
Makro-Sentiment (VIX, VIX-Terminstruktur, Safe-Haven- und Credit-Spreads) zu
einem 0–100-Wert je Index. 0 = Extreme Fear, 100 = Extreme Greed.

Komponenten (v1, gleichgewichtet — Gewichte sind bewusst als erste Näherung
gesetzt und leicht anpassbar):
  1. Momentum        – Index-Close vs. SMA125 (z-Score → logistisch)
  2. Breadth         – Mittel aus %>SMA200, Bull-Regime-Anteil, %RSI>50
  3. 52W-Stärke      – % der Mitglieder nahe ihrem 52W-Hoch
  4. Volatilität     – VIX-Perzentil (invertiert: hoher VIX = Angst)
  5. Terminstruktur  – VIX/VIX3M-Perzentil (invertiert: Backwardation = Angst)
  6. Safe-Haven      – 20-Tage-Rendite Index minus TLT (Aktien schlagen Anleihen = Gier)
  7. Junk-Bond       – 20-Tage-Rendite HYG minus LQD (Junk schlägt IG = Risk-on)

CLI: python -m tradinglib.fear_greed [/index:^SPX]
"""
from __future__ import annotations
import os
import math
import sqlite3
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_DB = os.environ.get("TradingDB") or "database"


def _p(name: str) -> str:
    return os.path.join(_DB, name)


# ── Datenzugriff ──────────────────────────────────────────────────────────────

def _series(ticker: str) -> pd.Series:
    """Tages-Close-Reihe eines Tickers als Series (Index = Datum), leer bei Fehler."""
    path = _p(f"yf_{ticker}.db")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    con = sqlite3.connect(path)
    try:
        df = pd.read_sql_query(
            "SELECT Date, Close FROM day_data WHERE Close IS NOT NULL ORDER BY Date", con)
    except Exception:
        return pd.Series(dtype=float)
    finally:
        con.close()
    if df.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(df["Close"], errors="coerce")
    s.index = pd.to_datetime(df["Date"], errors="coerce")
    return s.dropna()


def _members(index: str) -> list[str]:
    con = sqlite3.connect(_p("yf_tickers.db"))
    try:
        rows = con.execute(
            """SELECT s.Ticker FROM stocks s
               JOIN stock_indices si ON si.stock_id = s.id
               JOIN indices i ON i.id = si.index_id
               WHERE i.name = ?""", (index,)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        con.close()


def _member_snapshot(index: str) -> pd.DataFrame:
    """Letzte asset_simulation-Zeile je Indexmitglied (Breadth-Basis)."""
    members = _members(index)
    if not members:
        return pd.DataFrame()
    con = sqlite3.connect(_p("asset_simulation_all.db"))
    try:
        ph = ",".join("?" * len(members))
        df = pd.read_sql_query(
            f"""SELECT ticker, close, sma200, sma50, rsi, markov_regime, lyHigh
                FROM asset_simulation
                WHERE ticker IN ({ph})
                  AND Date = (SELECT MAX(Date) FROM asset_simulation a2
                              WHERE a2.ticker = asset_simulation.ticker)""",
            con, params=members)
    except Exception:
        return pd.DataFrame()
    finally:
        con.close()
    for c in ("close", "sma200", "sma50", "rsi", "markov_regime", "lyHigh"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ── Normierung ────────────────────────────────────────────────────────────────

def _logistic(z: float, k: float = 1.1) -> float:
    """z-Score → 0..100 (z=0 → 50, steigend = mehr Gier)."""
    return 100.0 / (1.0 + math.exp(-k * z))


def _zscore_last(series: pd.Series, win: int = 252) -> float | None:
    s = series.dropna()
    if len(s) < 30:
        return None
    s = s.iloc[-win:]
    sd = s.std()
    if not sd:
        return None
    return float((s.iloc[-1] - s.mean()) / sd)


def _pct_rank_last(series: pd.Series, win: int = 252) -> float | None:
    """Perzentil (0..1) des letzten Werts in seiner rollierenden Verteilung."""
    s = series.dropna()
    if len(s) < 30:
        return None
    s = s.iloc[-win:]
    return float((s < s.iloc[-1]).mean())


def _ret(series: pd.Series, days: int = 20) -> pd.Series:
    """days-Tage prozentuale Rendite als Reihe."""
    return series / series.shift(days) - 1.0


# ── Komponenten ───────────────────────────────────────────────────────────────

def _pct(mask: pd.Series) -> float:
    m = mask.dropna()
    return float(100.0 * m.mean()) if len(m) else float("nan")


def compute(index: str = "^SPX") -> dict:
    comps: dict[str, float] = {}
    detail: dict[str, str] = {}

    # 1. Momentum: Index-Close vs SMA125
    idx = _series(index)
    if len(idx) >= 60:
        win_ma = min(125, len(idx) // 2)
        sma = idx.rolling(win_ma).mean()
        dev = (idx / sma - 1.0).dropna()
        z = _zscore_last(dev)
        if z is not None:
            comps["momentum"] = _logistic(z)
            detail["momentum"] = f"{index} {dev.iloc[-1]*100:+.1f}% vs SMA{win_ma}"

    # 2. Breadth + 3. 52W-Stärke aus dem Mitglieder-Snapshot
    snap = _member_snapshot(index)
    if not snap.empty:
        above200 = _pct(snap["close"] > snap["sma200"])
        rsi50 = _pct(snap["rsi"] > 50)
        bull = _pct(snap["markov_regime"] == 1)
        bear = _pct(snap["markov_regime"] == 2)
        bull_share = 100.0 * bull / (bull + bear) if (bull + bear) else 50.0
        comps["breadth"] = float(np.nanmean([above200, rsi50, bull_share]))
        detail["breadth"] = f"{above200:.0f}% >SMA200, {rsi50:.0f}% RSI>50, {bull_share:.0f}% Bull-Anteil"
        near_high = _pct(snap["close"] >= 0.90 * snap["lyHigh"])
        comps["strength_52w"] = near_high
        detail["strength_52w"] = f"{near_high:.0f}% nahe 52W-Hoch"

    # 4. Volatilität: VIX invertiert
    vix = _series("^VIX")
    pr = _pct_rank_last(vix)
    if pr is not None:
        comps["volatility"] = 100.0 * (1.0 - pr)
        detail["volatility"] = f"VIX {vix.iloc[-1]:.1f} (Perzentil {pr*100:.0f}%)"

    # 5. Terminstruktur: VIX/VIX3M invertiert (Backwardation = Angst)
    vix3m = _series("^VIX3M")
    if not vix.empty and not vix3m.empty:
        j = pd.concat([vix.rename("v"), vix3m.rename("v3")], axis=1).dropna()
        if len(j) >= 30:
            ratio = j["v"] / j["v3"]
            pr2 = _pct_rank_last(ratio)
            if pr2 is not None:
                comps["term_structure"] = 100.0 * (1.0 - pr2)
                detail["term_structure"] = (f"VIX/VIX3M {ratio.iloc[-1]:.2f} "
                                            f"(Stand {j.index[-1].date()})")

    # 6. Safe-Haven: Index- minus TLT-20T-Rendite
    tlt = _series("TLT")
    if len(idx) > 25 and len(tlt) > 25:
        j = pd.concat([_ret(idx).rename("a"), _ret(tlt).rename("b")], axis=1).dropna()
        spread = (j["a"] - j["b"])
        z = _zscore_last(spread)
        if z is not None:
            comps["safe_haven"] = _logistic(z)
            detail["safe_haven"] = f"Index−TLT 20T {spread.iloc[-1]*100:+.1f}pp"

    # 7. Junk-Bond: HYG minus LQD 20T-Rendite
    hyg, lqd = _series("HYG"), _series("LQD")
    if len(hyg) > 25 and len(lqd) > 25:
        j = pd.concat([_ret(hyg).rename("a"), _ret(lqd).rename("b")], axis=1).dropna()
        spread = (j["a"] - j["b"])
        z = _zscore_last(spread)
        if z is not None:
            comps["junk_demand"] = _logistic(z)
            detail["junk_demand"] = f"HYG−LQD 20T {spread.iloc[-1]*100:+.1f}pp"

    score = float(np.nanmean(list(comps.values()))) if comps else float("nan")
    return {"index": index, "score": round(score, 1), "label": _label(score),
            "components": {k: round(v, 1) for k, v in comps.items()},
            "detail": detail, "n_components": len(comps)}


def _label(score: float) -> str:
    if math.isnan(score):
        return "–"
    if score < 25:
        return "Extreme Fear"
    if score < 45:
        return "Fear"
    if score <= 55:
        return "Neutral"
    if score <= 75:
        return "Greed"
    return "Extreme Greed"


if __name__ == "__main__":
    import sys
    index = "^SPX"
    for a in sys.argv[1:]:
        if a.lower().startswith("/index:"):
            index = a.split(":", 1)[1]
    r = compute(index)
    print(f"\n  Silvesto Fear & Greed — {r['index']}")
    print(f"  ────────────────────────────────")
    print(f"  SCORE: {r['score']}/100  →  {r['label']}   ({r['n_components']} Komponenten)\n")
    for k, v in r["components"].items():
        print(f"    {k:16s} {v:5.1f}   {r['detail'].get(k,'')}")
    print()
