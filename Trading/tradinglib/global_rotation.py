"""Global Rotation — Kapital-Rotation zwischen Märkten (Proxy).

Echte Fondsflüsse stehen nicht in den Daten; als etablierten Proxy nutzt dieses
Modul **relative Stärke / Rotation** der großen Markt-Indizes, alle in eine
gemeinsame Währung (EUR) umgerechnet (die FX-Bewegung selbst ist Teil des
Kapitalflusses). Kennzahlen — bewusst identisch zur Sektor-Rotation
(``sector_rotation.py``):

  * **JdK RS-Ratio / RS-Momentum** → RRG-Quadranten Leading/Weakening/Lagging/
    Improving (mit Rotations-Schweif = Richtung der Migration).
  * **Mansfield RSC** vs. gleichgewichtetem Markt-Korb → Zufluss/Abfluss-Proxy.
  * **Paarweise relative Stärke** (Markt_i / Markt_j) → „Staffelübergaben".

Benchmark = gleichgewichteter EUR-Korb der Märkte selbst (kein Extra-Ticker).

CLI: python -m tradinglib.global_rotation
"""
from __future__ import annotations
import os
import sqlite3
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_DB = os.environ.get("TradingDB") or "database"

# (Kürzel, Ticker, Notierungswährung, Anlageklasse)
_MARKETS = [
    ("US", "^SPX",   "USD", "Equity"),
    ("EU", "^GDAXI", "EUR", "Equity"),
    ("UK", "^FTSE",  "GBP", "Equity"),
    ("CH", "^SSMI",  "CHF", "Equity"),
    ("JP", "^N225",  "JPY", "Equity"),
    ("HK", "^HSI",   "HKD", "Equity"),
    ("ES", "^IBEX",  "EUR", "Equity"),
    ("KR", "^KS11",  "KRW", "Equity"),
]

# Cross-Asset-Ergänzung (Metalle/Rohstoffe in USD → via EURUSD; Krypto schon EUR).
# `=F`-Futures sind aktuell (Falle: abgelaufene Kontrakte, z. B. CT=F, gemieden).
_CROSS_ASSETS = [
    ("Gold",     "GC=F",    "USD", "Metal"),
    ("Silber",   "SI=F",    "USD", "Metal"),
    ("Brent",    "BZ=F",    "USD", "Commodity"),
    ("Kupfer",   "HG=F",    "USD", "Commodity"),
    # Krypto (schon EUR). Stablecoins (USDC/USDT) bewusst ausgelassen — USD-gepeggt,
    # würden den Krypto-Korb verzerren und zeigen keine echte Rotation.
    ("BTC",      "BTC-EUR",  "EUR", "Crypto"),
    ("ETH",      "ETH-EUR",  "EUR", "Crypto"),
    ("BNB",      "BNB-EUR",  "EUR", "Crypto"),
    ("SOL",      "SOL-EUR",  "EUR", "Crypto"),
    ("XRP",      "XRP-EUR",  "EUR", "Crypto"),
    ("DOGE",     "DOGE-EUR", "EUR", "Crypto"),
    ("TRX",      "TRX-EUR",  "EUR", "Crypto"),
    ("XMR",      "XMR-EUR",  "EUR", "Crypto"),
]

EQUITIES = _MARKETS
ASSETS_ALL = _MARKETS + _CROSS_ASSETS
# Anlageklassen-Universen für den RRG (Scatter nur unter vergleichbarer Vola sinnvoll)
COMMODITIES_METALS = [a for a in _CROSS_ASSETS if a[3] in ("Metal", "Commodity")]
CRYPTO = [a for a in _CROSS_ASSETS if a[3] == "Crypto"]
UNIVERSES = {"equity": EQUITIES, "commodity": COMMODITIES_METALS, "crypto": CRYPTO}

_QUADRANTS = {  # (rs_ratio>=100, rs_mom>=100) → Label
    (True, True):   "Leading",
    (True, False):  "Weakening",
    (False, False): "Lagging",
    (False, True):  "Improving",
}


def _p(name: str) -> str:
    return os.path.join(_DB, name)


def _series(ticker: str) -> pd.Series:
    """Tages-Close-Reihe eines Tickers (Index = Datum), leer bei Fehler."""
    path = _p(f"yf_{ticker}.db")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    con = sqlite3.connect(path)
    try:
        tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "day_data" not in tabs:
            return pd.Series(dtype=float)
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


def _eur_per(ccy: str) -> pd.Series | None:
    """Tagesreihe „EUR je 1 Einheit ccy". Nutzt {CCY}EUR=X direkt bzw. komponiert
    USD/GBP über EURUSD=X/GBPUSD=X. None, wenn keine Reihe verfügbar."""
    ccy = ccy.upper()
    if ccy == "EUR":
        return None  # Basis — keine Umrechnung nötig (Aufrufer behandelt es)
    if ccy == "USD":
        eu = _series("EURUSD=X")           # USD je EUR
        return (1.0 / eu).dropna() if not eu.empty else None
    if ccy == "GBP":
        g = _series("GBPUSD=X"); eu = _series("EURUSD=X")   # USD/GBP, USD/EUR
        if g.empty or eu.empty:
            return None
        j = pd.concat([g.rename("g"), eu.rename("e")], axis=1).dropna()
        return (j["g"] / j["e"]).dropna()   # EUR je GBP
    s = _series(f"{ccy}EUR=X")
    return s if not s.empty else None


def _eur_daily(index: str, ccy: str) -> pd.Series:
    """Index-Level in EUR (Tagesbasis). Leere Reihe, wenn Index oder FX fehlt."""
    idx = _series(index)
    if idx.empty:
        return pd.Series(dtype=float)
    if ccy.upper() == "EUR":
        return idx
    fx = _eur_per(ccy)
    if fx is None or fx.empty:
        return pd.Series(dtype=float)
    j = pd.concat([idx.rename("i"), fx.rename("fx")], axis=1).sort_index()
    j["fx"] = j["fx"].ffill()          # FX auf Handelstage des Index ziehen
    j = j.dropna(subset=["i", "fx"])
    return (j["i"] * j["fx"]).dropna()


def _weekly(s: pd.Series) -> pd.Series:
    """Wochen-Schluss (Freitag). Leer bei leerer/nicht-datierter Reihe."""
    if s is None or s.empty or not isinstance(s.index, pd.DatetimeIndex):
        return pd.Series(dtype=float)
    return s.resample("W-FRI").last().dropna()


# ── Berechnung ────────────────────────────────────────────────────────────────

def compute(markets=None, tail_weeks: int = 8, rsc_weeks: int = 30,
            pair_weeks: int = 13) -> dict:
    """Rotation über das Markt-Universum. Gibt dict zurück mit:
       markets (Liste dict: code/index/quadrant/rs_ratio/rs_momentum/tail/rsc),
       benchmark_note, pair_matrix (DataFrame-artig), skipped."""
    markets = markets or _MARKETS

    # 1. EUR-Wochenreihen je Markt (fehlende FX/Indizes überspringen)
    eur_w: dict[str, pd.Series] = {}
    meta: dict[str, tuple] = {}
    skipped = []
    for code, index, ccy, cls in markets:
        d = _eur_daily(index, ccy)
        w = _weekly(d)
        if len(w) < 55:                 # ~1 Jahr Wochen nötig
            skipped.append((code, index, ccy))
            continue
        eur_w[code] = w
        meta[code] = (index, ccy, cls)
    if len(eur_w) < 2:
        return {"markets": [], "skipped": skipped, "pair_matrix": None,
                "n": 0}

    # 2. Gemeinsames Wochenraster + auf 100 rebasieren → gleichgewichteter Korb
    frame = pd.concat(eur_w, axis=1).dropna()
    if len(frame) < 55:
        # weniger strenge Kürzung, falls kurze Reihen (^IBEX/^KS11) das Fenster drücken
        frame = pd.concat(eur_w, axis=1).ffill().dropna()
    rebased = frame / frame.iloc[0] * 100.0
    benchmark = rebased.mean(axis=1)    # equal-weight EUR-Korb

    # 3. RRG-Koordinaten + Mansfield-RSC je Markt (Formeln = sector_rotation.py)
    out = []
    for code in rebased.columns:
        sec = rebased[code]
        rs = (sec / benchmark).dropna()
        if len(rs) < 52:
            continue
        rs_ratio = 100 + (rs.ewm(span=10, adjust=False).mean()
                          / rs.ewm(span=40, adjust=False).mean() - 1) * 100
        rs_mom = 100 + (rs_ratio / rs_ratio.ewm(span=5, adjust=False).mean() - 1) * 100
        # Mansfield RSC (Zufluss/Abfluss-Proxy)
        ma = rs.rolling(rsc_weeks).mean()
        rsc_series = (rs / ma - 1) * 100
        cr, cm = float(rs_ratio.iloc[-1]), float(rs_mom.iloc[-1])
        tsl = slice(-(tail_weeks + 1), None)
        out.append({
            "code": code,
            "index": meta[code][0],
            "currency": meta[code][1],
            "class": meta[code][2],
            "quadrant": _QUADRANTS[(cr >= 100, cm >= 100)],
            "rs_ratio": cr,
            "rs_momentum": cm,
            "tail_ratio": rs_ratio.iloc[tsl].round(2).tolist(),
            "tail_momentum": rs_mom.iloc[tsl].round(2).tolist(),
            "rsc": round(float(rsc_series.iloc[-1]), 2) if pd.notna(rsc_series.iloc[-1]) else None,
        })
    # nach RSC (Zufluss) absteigend sortieren
    out.sort(key=lambda m: (m["rsc"] is None, -(m["rsc"] or 0)))

    # 4. Paarweise relative Stärke: %-Trend von Markt_i/Markt_j über pair_weeks
    codes = list(rebased.columns)
    mat = pd.DataFrame(index=codes, columns=codes, dtype=float)
    for i in codes:
        for j in codes:
            if i == j:
                mat.loc[i, j] = np.nan
                continue
            ratio = (rebased[i] / rebased[j]).dropna()
            if len(ratio) > pair_weeks:
                mat.loc[i, j] = round((ratio.iloc[-1] / ratio.iloc[-pair_weeks - 1] - 1) * 100, 2)

    return {"markets": out, "skipped": skipped, "n": len(out),
            "pair_matrix": mat, "pair_weeks": pair_weeks,
            "as_of": str(rebased.index[-1].date())}


if __name__ == "__main__":
    r = compute()
    print(f"\n  Global Rotation — Stand {r.get('as_of')}  ({r['n']} Märkte)")
    print("  " + "─" * 60)
    print(f"  {'Markt':6s}{'Index':9s}{'Quadrant':12s}{'RS-Ratio':>9s}{'RS-Mom':>8s}{'RSC%':>8s}")
    for m in r["markets"]:
        print(f"  {m['code']:6s}{m['index']:9s}{m['quadrant']:12s}"
              f"{m['rs_ratio']:9.1f}{m['rs_momentum']:8.1f}"
              f"{(m['rsc'] if m['rsc'] is not None else float('nan')):8.2f}")
    if r["skipped"]:
        print("  übersprungen (fehlende Daten):", [f"{c}/{i}" for c, i, _ in r["skipped"]])
    print("\n  Paarweise relative Stärke (%, Zeile schlägt Spalte über "
          f"{r['pair_weeks']} Wochen):")
    print(r["pair_matrix"].to_string())
    print()
