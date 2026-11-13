"""Trend- & Saisonalitäts-Scorecard (Edge Scores, Stufe 3).

Gleiche 20 Märkte wie die COT-Scorecard (gleiche Keys → später Gesamt-Score).

Kursquelle: die lokalen ``yf_<TICKER>.db`` der App, **nur lesend** (``mode=ro``).
Ist eine Reihe lokal nicht vorhanden oder älter als ``STALE_DAYS``, wird sie über
``market_data.download(force_remote=True)`` geholt — das schreibt nichts in die
Kurs-Datenbanken. FX-Kurse gegen USD entstehen bei Bedarf aus dem lokalen
Kreuzkurs gegen EUR × EURUSD (z. B. JPYUSD = JPYEUR × EURUSD).

Scoring je Markt (eigene, einfache Methodik):

| Komponente     | Regel                                                         | Punkte |
|----------------|---------------------------------------------------------------|--------|
| Langfristtrend | Schluss über / unter SMA200                                   | ±1     |
| Trendstruktur  | SMA50 über / unter SMA200                                     | ±1     |
| Momentum       | Rendite 63 Handelstage > +1 % / < −1 %                         | ±1     |
| Saisonalität   | Folgemonat (30 Kalendertage ab heutigem Datum) in den letzten  | ±1     |
|                | 10 Jahren: Trefferquote ≥ 70 % bzw. ≤ 30 % mit passendem Ø     |        |

Summe −4 … +4. Saisonalität bewusst nur ±1: eine 10-Jahres-Statistik soll einen
klaren Trend nicht aufheben (Silber 11/2026: Trend −3, Saison +2 hätte "neutral"
ergeben). ``sign = −1`` dreht die Richtung (10J-Treasury über die Rendite
``^TNX``: steigende Rendite = fallender Anleihekurs).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

STALE_DAYS = 7
HISTORY_START = "2014-01-01"
SEASON_YEARS = 10
SEASON_DAYS = 30            # Kalendertage des Saison-Fensters
MIN_SEASON_YEARS = 7

# key → Liste von Quellen in Reihenfolge; jede Quelle: (art, ticker)
#   "px":    Kurs direkt
#   "xeur":  Kreuzkurs gegen EUR, wird mit EURUSD multipliziert
#   "inv":   Kehrwert (USDxxx → xxxUSD)
SOURCES: dict[str, list[tuple[str, str]]] = {
    "USD": [("px", "DX-Y.NYB")],
    "EUR": [("px", "EURUSD=X")],
    "GBP": [("px", "GBPUSD=X")],
    "JPY": [("xeur", "JPYEUR=X"), ("inv", "JPY=X")],
    "CHF": [("xeur", "CHFEUR=X"), ("inv", "CHF=X")],
    "CAD": [("xeur", "CADEUR=X"), ("inv", "CAD=X")],
    "AUD": [("xeur", "AUDEUR=X"), ("px", "AUDUSD=X")],
    "NZD": [("px", "NZDUSD=X")],
    "MXN": [("xeur", "MXNEUR=X"), ("inv", "MXN=X")],
    "GOLD": [("px", "GC=F")],
    "SILVER": [("px", "SI=F")],
    "COPPER": [("px", "HG=F")],
    "WTI": [("px", "CL=F"), ("px", "BZ=F")],
    "NATGAS": [("px", "NG=F")],
    "SPX": [("px", "^SPX"), ("px", "^GSPC")],
    "NDX": [("px", "^NDX")],
    "DJI": [("px", "^DJI")],
    "RUT": [("px", "^RUT")],
    "UST10Y": [("px", "^TNX")],
    "BTC": [("xeur", "BTC-EUR"), ("px", "BTC-USD")],
}
# Weitere Aktienindizes nur in dieser Karte (keine COT-/Makrodaten → nicht im Gesamt-Score).
# key, Gruppe; Quellen wie oben. Lokal fehlende kommen von Yahoo.
EXTRA_MARKETS: list[tuple[str, str]] = [
    ("DAX", "indices_world"), ("MDAX", "indices_world"), ("ESTX50", "indices_world"),
    ("FTSE", "indices_world"), ("CAC", "indices_world"), ("IBEX", "indices_world"),
    ("SMI", "indices_world"), ("FTSEMIB", "indices_world"), ("AEX", "indices_world"),
    ("N225", "indices_world"), ("HSI", "indices_world"), ("ASX200", "indices_world"),
    ("KOSPI", "indices_world"), ("BOVESPA", "indices_world"), ("TSX", "indices_world"),
    ("NIFTY", "indices_world"),
    ("ETH", "crypto"), ("SOL", "crypto"), ("XRP", "crypto"), ("BNB", "crypto"),
    ("DOGE", "crypto"), ("TRX", "crypto"), ("XMR", "crypto"),
]
SOURCES.update({
    "DAX": [("px", "^GDAXI")], "MDAX": [("px", "^MDAXI")], "ESTX50": [("px", "^STOXX50E")],
    "FTSE": [("px", "^FTSE")], "CAC": [("px", "^FCHI")], "IBEX": [("px", "^IBEX")],
    "SMI": [("px", "^SSMI")], "FTSEMIB": [("px", "FTSEMIB.MI")], "AEX": [("px", "^AEX")],
    "N225": [("px", "^N225")], "HSI": [("px", "^HSI")], "ASX200": [("px", "^AXJO")],
    "KOSPI": [("px", "^KS11")], "BOVESPA": [("px", "^BVSP")], "TSX": [("px", "^GSPTSE")],
    "NIFTY": [("px", "^NSEI")],
    **{c: [("xeur", f"{c}-EUR"), ("px", f"{c}-USD")] for c in ("ETH", "SOL", "XRP", "BNB", "DOGE", "TRX", "XMR")},
})
SIGN = {"UST10Y": -1}
FX_CCYS = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]


def _db_dir() -> str:
    return os.environ.get("TradingDB") or "database"


# ── Kurse laden ───────────────────────────────────────────────────────────────

def _local_close(ticker: str) -> pd.Series:
    path = os.path.join(_db_dir(), f"yf_{ticker}.db")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(
            "SELECT Date, Close FROM day_data WHERE Close IS NOT NULL AND Date >= ? ORDER BY Date",
            con, params=(HISTORY_START,))
    except Exception:
        return pd.Series(dtype=float)
    finally:
        con.close()
    s = pd.Series(pd.to_numeric(df["Close"], errors="coerce").values,
                  index=pd.DatetimeIndex(pd.to_datetime(df["Date"], errors="coerce")).normalize())
    return _clean(s)


def _remote_close(ticker: str) -> pd.Series:
    from tradinglib import market_data
    try:
        df = market_data.download(ticker, start=HISTORY_START, interval="1d", force_remote=True)
    except Exception as e:
        logger.warning("Trend: Abruf %s fehlgeschlagen: %s", ticker, e)
        return pd.Series(dtype=float)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    close = df["Close"] if "Close" in df.columns.get_level_values(0) else df.iloc[:, 0]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return _clean(close)


def _clean(s: pd.Series) -> pd.Series:
    s = s[~s.index.isna()].dropna()
    s = s[s > 0]
    return s[~s.index.duplicated(keep="last")].sort_index()


def _fresh(s: pd.Series, today: dt.date) -> bool:
    return not s.empty and (pd.Timestamp(today) - s.index[-1]).days <= STALE_DAYS


def _long_enough(s: pd.Series, today: dt.date) -> bool:
    """Reicht die Historie fuer SMA200 und die Saisonalitaet (SEASON_YEARS volle Jahre)?"""
    return not s.empty and s.index[0] <= pd.Timestamp(today.year - SEASON_YEARS, 1, 31)


def price_series(key: str, today: dt.date | None = None) -> tuple[pd.Series, str]:
    """(Kursreihe in Richtung des Marktes, Quellbeschreibung). Leer, wenn nichts geht.

    Lokal zuerst; ist die lokale Reihe veraltet ODER zu kurz fuer die Saisonalitaet
    (z. B. ^SSMI lokal erst ab 2023), wird Yahoo versucht und die laengere Reihe genommen."""
    today = today or dt.date.today()
    for kind, ticker in SOURCES[key]:
        short_local = None
        for loader, where in ((_local_close, "local"), (_remote_close, "Yahoo")):
            s = loader(ticker)
            if kind == "xeur" and not s.empty:
                eurusd = _local_close("EURUSD=X")
                if not _fresh(eurusd, today):
                    eurusd = _remote_close("EURUSD=X")
                s = (s * eurusd.reindex(s.index).ffill()).dropna()
            elif kind == "inv" and not s.empty:
                s = 1.0 / s
            label = f"{ticker} × EURUSD" if kind == "xeur" else f"1/{ticker}" if kind == "inv" else ticker
            if _fresh(s, today):
                # nur bei direkten Kursen: Kreuzkurse (Kryptos in EUR) haben bei Yahoo dieselbe Historie
                if where == "local" and kind == "px" and not _long_enough(s, today):
                    short_local = (s, f"{label} ({where})")
                    continue
                if short_local is not None and len(s) <= len(short_local[0]):
                    return short_local
                return s, f"{label} ({where})"
        if short_local is not None:
            return short_local
    return pd.Series(dtype=float), ""


# ── Kennzahlen (reine Funktionen) ─────────────────────────────────────────────

def trend_metrics(s: pd.Series) -> dict:
    """Schluss, SMA50/200, Abstände und 63-Tage-Rendite der Reihe ``s``."""
    if len(s) < 200:
        return {}
    sma50, sma200 = s.rolling(50).mean().iloc[-1], s.rolling(200).mean().iloc[-1]
    last = float(s.iloc[-1])
    # 3 Kalendermonate statt 63 Zeilen: Kryptos handeln 7 Tage/Woche, dort waeren
    # 63 Zeilen nur ~9 Wochen (Bitcoin 11/2026: −11 % statt +9 % ueber 13 Wochen)
    start = s[s.index <= s.index[-1] - pd.DateOffset(months=3)]
    r63 = float(s.iloc[-1] / start.iloc[-1] - 1) * 100 if not start.empty else np.nan
    return {"last": last, "date": s.index[-1], "sma50": float(sma50), "sma200": float(sma200),
            "dist200": (last / sma200 - 1) * 100, "r63": r63}


def seasonality(s: pd.Series, today: dt.date, years: int = SEASON_YEARS,
                days: int = SEASON_DAYS) -> dict:
    """Rendite der nächsten ``days`` Kalendertage ab heutigem Monat/Tag in den
    letzten ``years`` abgeschlossenen Jahren (Einstieg = erster Schluss am/nach dem
    Datum, Ausstieg = letzter Schluss am/vor Datum + ``days``)."""
    rets, yrs = [], []
    for y in range(today.year - years, today.year):
        try:
            start = pd.Timestamp(dt.date(y, today.month, min(today.day, 28 if today.month == 2 else today.day)))
        except ValueError:
            continue
        end = start + pd.Timedelta(days=days)
        a, b = s[s.index >= start], s[s.index <= end]
        if a.empty or b.empty or a.index[0] >= b.index[-1] or (a.index[0] - start).days > 5:
            continue
        rets.append(float(b.iloc[-1] / a.iloc[0] - 1) * 100)
        yrs.append(y)
    if not rets:
        return {"n": 0, "mean": np.nan, "hit": np.nan, "rets": [], "years": []}
    arr = np.array(rets)
    return {"n": len(arr), "mean": float(arr.mean()), "hit": float((arr > 0).mean() * 100),
            "rets": rets, "years": yrs}


def score_trend(m: dict, sign: int = 1) -> tuple[int, int, int]:
    if not m:
        return 0, 0, 0
    p1 = 1 if m["last"] > m["sma200"] else -1
    p2 = 1 if m["sma50"] > m["sma200"] else -1
    r = m["r63"]
    p3 = 0 if pd.isna(r) else 1 if r > 1 else -1 if r < -1 else 0
    return sign * p1, sign * p2, sign * p3


def score_season(sea: dict, sign: int = 1) -> int:
    """Trefferquote und Ø in Marktrichtung (bei sign −1 gespiegelt)."""
    if sea["n"] < MIN_SEASON_YEARS:
        return 0
    hit = sea["hit"] if sign > 0 else 100 - sea["hit"]
    mean = sign * sea["mean"]
    if hit >= 70 and mean > 0:
        return 1
    if hit <= 30 and mean < 0:
        return -1
    return 0


def bias_label(score: float) -> str:
    """Spanne −4 … +4."""
    if score >= 3:
        return "strong_bull"
    if score >= 2:
        return "bull"
    if score <= -3:
        return "strong_bear"
    if score <= -2:
        return "bear"
    return "neutral"


def scorecard(today: dt.date | None = None) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """(Tabelle je Markt, Kursreihen je Key für Charts)."""
    from tradinglib.cot_data import MARKETS
    today = today or dt.date.today()
    rows, series = [], {}
    markets = [(m[0], m[3]) for m in MARKETS] + EXTRA_MARKETS
    for key, group in markets:
        s, src = price_series(key, today)
        if s.empty:
            rows.append({"key": key, "group": group, "source": "", "missing": True})
            continue
        series[key] = s
        sign = SIGN.get(key, 1)
        m = trend_metrics(s)
        sea = seasonality(s, today)
        p1, p2, p3 = score_trend(m, sign)
        ps = score_season(sea, sign)
        score = p1 + p2 + p3 + ps
        rows.append({
            "key": key, "group": group, "source": src, "missing": False,
            "date": m.get("date"), "last": m.get("last"), "dist200": m.get("dist200"),
            "r63": m.get("r63"), "sea_mean": sea["mean"], "sea_hit": sea["hit"], "sea_n": sea["n"],
            "p_sma200": p1, "p_cross": p2, "p_mom": p3, "p_season": ps,
            "score": score, "bias": bias_label(score), "inverted": sign < 0,
        })
    return pd.DataFrame(rows), series


def seasonal_profile(s: pd.Series, today: dt.date, years: int = SEASON_YEARS) -> pd.DataFrame:
    """Ø-Jahresverlauf (Index 100 am Jahresanfang) der letzten ``years`` vollen
    Jahre, je Kalendertag; Spalten ``doy`` (1..366) und ``avg``."""
    paths = []
    for y in range(today.year - years, today.year):
        ys = s[(s.index.year == y)]
        if len(ys) < 200:
            continue
        p = ys / ys.iloc[0] * 100
        p.index = p.index.dayofyear
        paths.append(p.reindex(range(1, 367)).ffill())
    if not paths:
        return pd.DataFrame(columns=["doy", "avg"])
    avg = pd.concat(paths, axis=1).mean(axis=1)
    return pd.DataFrame({"doy": avg.index, "avg": avg.values, "n": len(paths)})


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    card, _ = scorecard()
    pd.set_option("display.width", 220)
    print(card.drop(columns=["group"]).round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
