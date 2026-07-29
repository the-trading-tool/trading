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
import datetime as dt

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


def _breadth_timeseries(index: str, start_year: int = 2020) -> pd.DataFrame:
    """Tägliche Breadth-Anteile (0..1) über die Indexmitglieder als MEHRJAHRES-
    Zeitreihe, damit der aktuelle Wert gegen seine eigene Historie (inkl. der
    Bärenmärkte 2020/2022) perzentil-normiert werden kann — statt als Rohprozent,
    der im Bullenmarkt systematisch über-liest.

    WICHTIG: `asset_simulation_all.db` enthält nur das laufende Jahr. Die Historie
    kommt aus den Jahres-DBs `asset_simulation_{YYYY}.db` (2020–Vorjahr), das
    laufende Jahr aus `_all.db`. Alle mit AVG(CASE …) je Handelstag aggregiert und
    zusammengefügt. `near_high`/`near_low` (lyHigh/lyLow) → Netto 52W-Hochs−Tiefs.
    """
    members = _members(index)
    if not members:
        return pd.DataFrame()
    ph = ",".join("?" * len(members))
    q = f"""SELECT Date,
               AVG(CASE WHEN sma200 > 0 AND close > sma200 THEN 1.0 ELSE 0 END) AS above200,
               AVG(CASE WHEN rsi > 50 THEN 1.0 ELSE 0 END)                       AS rsi50,
               AVG(CASE WHEN markov_regime = 1 THEN 1.0 ELSE 0 END)              AS bull,
               AVG(CASE WHEN markov_regime = 2 THEN 1.0 ELSE 0 END)              AS bear,
               AVG(CASE WHEN lyHigh > 0 AND close >= 0.95 * lyHigh THEN 1.0 ELSE 0 END) AS near_high,
               AVG(CASE WHEN lyLow  > 0 AND close <= 1.05 * lyLow  THEN 1.0 ELSE 0 END) AS near_low
            FROM asset_simulation
            WHERE ticker IN ({ph})
            GROUP BY Date ORDER BY Date"""
    cur_year = dt.date.today().year
    frames = []
    for yr in range(start_year, cur_year + 1):
        name = "asset_simulation_all.db" if yr == cur_year else f"asset_simulation_{yr}.db"
        p = _p(name)
        if not os.path.exists(p):
            continue
        con = sqlite3.connect(p)
        try:
            frames.append(pd.read_sql_query(q, con, params=members))
        except Exception:
            logger.debug("breadth ts: query failed for %s", name, exc_info=True)
        finally:
            con.close()
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).drop_duplicates("Date").sort_values("Date")
    for c in ("above200", "rsi50", "bull", "bear", "near_high", "near_low"):
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

    # 2. Breadth + 3. 52W-Stärke — HISTORISCH NORMIERT (Perzentil ggü. eigener
    #    Mehrjahres-Historie) statt Rohprozent. Grund: 71 % > SMA200 sind im
    #    Bullenmarkt normal, nicht "Gier" — erst der Vergleich mit der eigenen
    #    Verteilung macht daraus ein Stimmungssignal (wie bei CNN).
    bts = _breadth_timeseries(index)
    if not bts.empty and len(bts) >= 60:
        w = len(bts)
        bull_share = bts["bull"] / (bts["bull"] + bts["bear"]).replace(0, np.nan)
        p_above = _pct_rank_last(bts["above200"], win=w)
        p_rsi = _pct_rank_last(bts["rsi50"], win=w)
        p_bull = _pct_rank_last(bull_share, win=w)
        parts = [p for p in (p_above, p_rsi, p_bull) if p is not None]
        if parts:
            comps["breadth"] = 100.0 * float(np.mean(parts))
            detail["breadth"] = (f"{bts['above200'].iloc[-1]*100:.0f}% >SMA200 · "
                                 f"Perzentil {100*np.mean(parts):.0f}%")
        # 52W-Stärke: Netto neue 52W-Hochs minus Tiefs (via lyHigh/lyLow),
        # historisch perzentil-normiert — ersetzt das saturierende "% nahe Hoch".
        net = bts["near_high"] - bts["near_low"]
        p_net = _pct_rank_last(net, win=w)
        if p_net is not None:
            comps["strength_52w"] = 100.0 * p_net
            detail["strength_52w"] = (f"Netto 52W-Hochs−Tiefs "
                                      f"{net.iloc[-1]*100:+.0f}pp · Perzentil {100*p_net:.0f}%")

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
