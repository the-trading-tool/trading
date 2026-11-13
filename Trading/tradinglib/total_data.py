"""Gesamt-Score der Edge Scores (Stufe 4) — führt COT, Makro und Trend zusammen.

Jede Karte hat ihre eigene Spanne; für den Gesamt-Score wird jede Komponente auf
−1 … +1 normiert (COT /3, Makro /6, Trend /4) und der Mittelwert der **vorhandenen**
Komponenten auf −10 … +10 skaliert. Makro gibt es nur für die acht Hauptwährungen;
bei allen anderen Märkten (Rohstoffe, Indizes, Anleihen, Bitcoin, MXN) zählen COT
und Trend allein.

Gleichgewichtet, weil (noch) keine Messung eine andere Gewichtung begründet.

"Einig" = alle vorhandenen Komponenten zeigen in dieselbe Richtung (keine 0, kein
Widerspruch) — oft aussagekräftiger als die Höhe des Scores.
"""
from __future__ import annotations

import pandas as pd

SCALE = {"cot": 3.0, "macro": 6.0, "trend": 4.0}
COMPONENTS = ["cot", "macro", "trend"]


def bias_label(score: float, n: int | None = None) -> str:
    """Spanne −10 … +10. ``n`` = Zahl der vorhandenen Komponenten: bei nur einer
    (z. B. Welt-Indizes, nur Trend) gibt es hoechstens bull/bear — eine einzelne
    starke Komponente wuerde sonst so "stark" wirken wie drei einige."""
    if n == 1:
        score = max(min(score, 4.9), -4.9)
    if score >= 5:
        return "strong_bull"
    if score >= 2:
        return "bull"
    if score <= -5:
        return "strong_bear"
    if score <= -2:
        return "bear"
    return "neutral"


def unconfirmed(total: float | None, trend: float | None, p_sma200: float | None = None) -> bool:
    """Kurs bestaetigt die Richtung des Gesamt-Scores nicht: Trend-Score ODER die Lage
    zum SMA200 (``p_sma200`` ±1, in Marktrichtung) zeigt dagegen."""
    if total is None or pd.isna(total) or total == 0:
        return False
    return any(v is not None and not pd.isna(v) and v * total < 0 for v in (trend, p_sma200))


def bias_for(total: float | None, n: int, trend: float | None, p_sma200: float | None = None) -> str:
    """Bias eines Marktes mit zwei Bremsen gegen Ueberbewertung:

    * nur eine Komponente → hoechstens bull/bear (:func:`bias_label`),
    * **Kursbestaetigung**: zeigt der Trend-Score oder die Lage zum SMA200 gegen die
      Richtung des Gesamt-Scores (z. B. COT bullish, Kurs unter SMA200), ist der Bias
      neutral — Positionierung und Makro allein machen keinen Markt bullish, solange
      der Kurs nicht mitgeht. Der SMA200 zaehlt extra, weil ein positives 3-M-Momentum
      nach einem Tief den Trend-Score ins Plus heben kann, obwohl der Kurs faellt.
    """
    if total is None or pd.isna(total):
        return "neutral"
    if unconfirmed(total, trend, p_sma200):
        return "neutral"
    return bias_label(total, n)


def combine(cot: pd.DataFrame, macro: pd.DataFrame, trend: pd.DataFrame,
            markets: list[tuple] | None = None) -> pd.DataFrame:
    """``cot``/``trend``: Spalten key, score; ``macro``: Index = Währung, Spalte score.
    Ergebnis je Markt: Rohscores, normierte Werte, Gesamt (−10…+10), Bias, Einigkeit."""
    if markets is None:
        from tradinglib.cot_data import MARKETS as markets
    raw = {
        "cot": dict(zip(cot["key"], cot["score"])) if not cot.empty else {},
        "macro": dict(zip(macro.index, macro["score"])) if not macro.empty else {},
        "trend": dict(zip(trend["key"], trend["score"])) if not trend.empty else {},
    }
    p200 = dict(zip(trend["key"], trend["p_sma200"])) if "p_sma200" in trend else {}
    rows = []
    for key, _code, _label, group, _ccy in markets:
        r = {"key": key, "group": group}
        norm = []
        for comp in COMPONENTS:
            v = raw[comp].get(key)
            v = None if v is None or pd.isna(v) else float(v)
            r[comp] = v
            if v is not None:
                norm.append(v / SCALE[comp])
        r["n"] = len(norm)
        if not norm:
            r.update(total=None, bias="neutral", agree=False)
        else:
            total = round(10 * sum(norm) / len(norm), 1)
            signs = {(x > 0) - (x < 0) for x in norm}
            r.update(total=total, bias=bias_for(total, len(norm), r["trend"], p200.get(key)),
                     agree=len(norm) >= 2 and len(signs) == 1 and 0 not in signs)
        rows.append(r)
    return pd.DataFrame(rows)


def pair_setups(total: pd.DataFrame, ccys: list[str]) -> pd.DataFrame:
    """Forex-Paare aus den Gesamt-Scores (Basis − Quote, −20 … +20) mit der
    Differenz je Komponente, nach Stärke sortiert."""
    from tradinglib.cot_data import pair_bias
    fx = total[total["key"].isin(ccys) & total["total"].notna()]
    if fx.empty:
        return pd.DataFrame()
    pairs = pair_bias(fx[["key", "total"]].rename(columns={"total": "score"}), strong=10, bias=4)
    t = fx.set_index("key")
    for comp in COMPONENTS:
        pairs[f"d_{comp}"] = [
            (t.at[p[:3], comp] - t.at[p[3:], comp])
            if pd.notna(t.at[p[:3], comp]) and pd.notna(t.at[p[3:], comp]) else None
            for p in pairs["pair"]]

    def _agree(row):
        ds = [row[f"d_{c}"] for c in COMPONENTS if row[f"d_{c}"] is not None and not pd.isna(row[f"d_{c}"])]
        signs = {(d > 0) - (d < 0) for d in ds}
        return len(ds) >= 2 and len(signs) == 1 and 0 not in signs

    pairs["agree"] = pairs.apply(_agree, axis=1)
    pairs["score"] = pairs["score"].round(1)
    return pairs
