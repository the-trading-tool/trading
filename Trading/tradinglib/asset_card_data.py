"""Asset-Scorecard der Edge Scores — ein Markt, alle Komponenten im Detail.

Setzt auf denselben Bausteinen auf wie die Einzelkarten (COT, Makro, Trend) und
den Gesamt-Score; hier kommt nur hinzu:

* Technik-Details, die nicht in den Score eingehen (SMA 20/50/100/200, realisierte
  Volatilitaet, Ø Tagesbewegung 7/90 Tage).
* Der **Score-Verlauf**: Gesamt-Score je Woche, kausal nachgerechnet —
  COT nach Veroeffentlichungsdatum, Trend auf der bis dahin bekannten Kursreihe,
  Makro nur mit Werten, die zu dem Datum (geschaetzt) schon veroeffentlicht waren.

Makro zaehlt wie im Gesamt-Score nur fuer die acht Hauptwaehrungen. Fuer alle
anderen Maerkte zeigt die Karte die US-Daten als Kontext, ohne sie zu werten.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from tradinglib import cot_data as cd
from tradinglib import macro_data as md
from tradinglib import total_data as tot
from tradinglib import trend_data as td

# Tage ab Periodenbeginn, bis ein Makrowert typischerweise veroeffentlicht ist
# (Monatswert Oktober = Periode 01.10.). Grob, aber konservativ genug, dass der
# Verlauf keine Werte vor ihrer Veroeffentlichung nutzt.
PUBLICATION_LAG = {("rate", "M"): 42, ("cpi", "M"): 70, ("unemp", "M"): 70, ("unemp", "Q"): 160,
                   ("gdp", "Q"): 120, ("cli", "M"): 42, ("cpi", "Q"): 160}
DEFAULT_LAG = 120
SMA_WINDOWS = (20, 50, 100, 200)


def markets() -> list[tuple[str, str]]:
    """(key, Gruppe) aller Maerkte der Karte: COT-Maerkte, dann die Zusatzmaerkte der Trend-Karte."""
    return [(m[0], m[3]) for m in cd.MARKETS] + list(td.EXTRA_MARKETS)


def has_cot(key: str) -> bool:
    return key in cd._BY_KEY


def has_macro(key: str) -> bool:
    return key in md.CURRENCIES


# ── Technik ───────────────────────────────────────────────────────────────────

def technicals(s: pd.Series, sign: int = 1) -> dict:
    """Lage zu den SMAs (+1 darueber / −1 darunter, in Marktrichtung), realisierte
    Volatilitaet (20 T, annualisiert) mit ihrem Rang im letzten Jahr und Ø Tagesbewegung."""
    if len(s) < 30:
        return {}
    last = float(s.iloc[-1])
    out = {"last": last, "date": s.index[-1]}
    for w in SMA_WINDOWS:
        if len(s) >= w:
            sma = float(s.rolling(w).mean().iloc[-1])
            out[f"sma{w}"] = sma
            out[f"sma{w}_state"] = sign * (1 if last > sma else -1)
    rets = s.pct_change().dropna()
    vol = rets.rolling(20).std() * np.sqrt(252) * 100
    out["vol20"] = float(vol.iloc[-1]) if len(vol) and not pd.isna(vol.iloc[-1]) else np.nan
    year = vol.dropna().iloc[-252:]
    out["vol_rank"] = float((year <= year.iloc[-1]).mean() * 100) if len(year) >= 60 else np.nan
    out["move7"] = float(rets.iloc[-7:].abs().mean() * 100)
    out["move90"] = float(rets.iloc[-90:].abs().mean() * 100)
    return out


def vol_level(rank: float) -> str:
    """Einordnung der aktuellen Volatilitaet gegen das eigene letzte Jahr."""
    if rank is None or pd.isna(rank):
        return "na"
    return "low" if rank <= 33 else "high" if rank >= 67 else "normal"


def trend_at(s: pd.Series, day: dt.date, sign: int = 1) -> dict:
    """Trend-Punkte zum Datum ``day`` nur aus Kursen bis einschliesslich ``day``."""
    s = s[s.index <= pd.Timestamp(day)]
    m = td.trend_metrics(s)
    sea = td.seasonality(s, day)
    p1, p2, p3 = td.score_trend(m, sign)
    ps = td.score_season(sea, sign)
    return {"metrics": m, "season": sea, "p_sma200": p1, "p_cross": p2, "p_mom": p3,
            "p_season": ps, "score": p1 + p2 + p3 + ps if m else None}


# ── COT ───────────────────────────────────────────────────────────────────────

def cot_at(hist: pd.DataFrame, day: dt.date) -> pd.Series | None:
    """Letzte zum Datum veroeffentlichte COT-Woche (Stichtag Di, Veroeffentlichung Fr)."""
    if hist is None or hist.empty:
        return None
    known = hist[hist["release_date"] <= pd.Timestamp(day)]
    return None if known.empty else known.iloc[-1]


# ── Makro ─────────────────────────────────────────────────────────────────────

def available_from(obs: pd.DataFrame) -> pd.Series:
    """Geschaetztes Veroeffentlichungsdatum je Makrowert (Periodenbeginn + Verzug)."""
    lag = np.array([PUBLICATION_LAG.get((i, f), DEFAULT_LAG) for i, f in zip(obs["indicator"], obs["freq"])],
                   dtype="int64")
    return obs["period"] + pd.to_timedelta(lag, unit="D")


def obs_known_at(obs: pd.DataFrame, day: dt.date, avail: pd.Series | None = None) -> pd.DataFrame:
    """Makrowerte, die zum Datum ``day`` (geschaetzt) schon veroeffentlicht waren."""
    if obs.empty:
        return obs
    avail = available_from(obs) if avail is None else avail
    return obs[avail <= pd.Timestamp(day)]


def macro_at(obs: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    """Makro-Punkte je Waehrung zum Datum (wie :func:`macro_data.score_table`)."""
    known = obs_known_at(obs, day)
    return md.score_table(md.latest(known)) if not known.empty else pd.DataFrame()


# ── Gesamt & Verlauf ──────────────────────────────────────────────────────────

def total_of(cot: float | None, macro: float | None, trend: float | None) -> float | None:
    """Gesamt-Score (−10 … +10) wie :func:`total_data.combine` fuer einen Markt."""
    norm = [v / tot.SCALE[c] for c, v in (("cot", cot), ("macro", macro), ("trend", trend))
            if v is not None and not pd.isna(v)]
    return None if not norm else round(10 * sum(norm) / len(norm), 1)


def history(key: str, s: pd.Series, cot_hist: pd.DataFrame | None, obs: pd.DataFrame | None,
            weeks: int = 52, today: dt.date | None = None) -> pd.DataFrame:
    """Gesamt-Score und Komponenten je Freitag der letzten ``weeks`` Wochen, kausal."""
    today = today or dt.date.today()
    last_fri = today - dt.timedelta(days=(today.weekday() - 4) % 7)
    days = [last_fri - dt.timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    if days[-1] != today:
        days.append(today)
    sign = td.SIGN.get(key, 1)
    use_macro = has_macro(key) and obs is not None and not obs.empty
    rows, macro_memo = [], {}
    if use_macro:
        # fuer die Punkte reichen die letzten Jahre (laengster Vergleich: 6 Monate)
        obs = obs[obs["period"] >= pd.Timestamp(days[0]) - pd.DateOffset(years=2)]
        avail = available_from(obs)
    for d in days:
        c = cot_at(cot_hist, d)
        cot = None if c is None else float(c["score"])
        trend = trend_at(s, d, sign)["score"] if s is not None and not s.empty else None
        macro = None
        if use_macro:
            # heute: alles, was geladen ist, ist auch veroeffentlicht -> identisch zur Makro-Karte
            known = obs if d >= today else obs[avail <= pd.Timestamp(d)]
            # die bekannte Menge waechst nur mit dem Datum -> Zeilenzahl identifiziert sie
            if len(known) not in macro_memo:
                macro_memo[len(known)] = md.score_table(md.latest(known)) if not known.empty else pd.DataFrame()
            tab = macro_memo[len(known)]
            if key in tab.index:
                macro = float(tab.at[key, "score"])
        total = total_of(cot, macro, trend)
        rows.append({"date": pd.Timestamp(d), "cot": cot, "macro": macro, "trend": trend,
                     "total": total, "bias": tot.bias_label(total) if total is not None else "neutral"})
    return pd.DataFrame(rows)
