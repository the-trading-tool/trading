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
RS_WEEKS = 13                 # wie die Pair-Matrix der Global Rotation
RS_THRESHOLD = 2.0            # Pp Abstand zum Gruppen-Median fuer bullish/bearish
RS_MIN_PEERS = 4
# Vergleichsgruppe je Markt-Gruppe; FX (Kurse gegen USD, USD selbst als Index) und
# Anleihen (ein Markt) haben keine sinnvolle Gruppe.
PEER_GROUPS = {"indices": "equity", "indices_world": "equity", "commodities": "commodities",
               "crypto": "crypto"}


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


def period_return(s: pd.Series, weeks: int = RS_WEEKS) -> float | None:
    """Rendite in % ueber ``weeks`` Wochen (Kalender), bis zum letzten Kurs."""
    if s is None or len(s) < 2:
        return None
    start = s[s.index <= s.index[-1] - pd.Timedelta(weeks=weeks)]
    return None if start.empty else float(s.iloc[-1] / start.iloc[-1] - 1) * 100


def relative_strength(key: str, series: dict[str, pd.Series], weeks: int = RS_WEEKS) -> dict | None:
    """Rendite des Marktes gegen den Median seiner Vergleichsgruppe (in Landeswaehrung).

    Kontext, nicht im Score. None, wenn der Markt keine Gruppe hat oder zu wenige
    Vergleichsmaerkte Kurse haben."""
    group = dict(markets()).get(key)
    peer = PEER_GROUPS.get(group)
    if peer is None or key not in series:
        return None
    members = [k for k, g in markets() if PEER_GROUPS.get(g) == peer and k in series]
    rets = {k: period_return(series[k], weeks) for k in members}
    rets = {k: v for k, v in rets.items() if v is not None}
    others = [v for k, v in rets.items() if k != key]
    if key not in rets or len(others) < RS_MIN_PEERS:
        return None
    r, med = rets[key], float(np.median(others))
    diff = r - med
    rank = 1 + sum(v > r for v in rets.values())
    state = 1 if diff > RS_THRESHOLD else -1 if diff < -RS_THRESHOLD else 0
    return {"ret": r, "median": med, "diff": diff, "rank": rank, "n": len(rets),
            "peer": peer, "weeks": weeks, "state": state}


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
            if c in tot.SCORED and v is not None and not pd.isna(v)]
    return None if not norm else round(10 * sum(norm) / len(norm), 1)


def n_components(cot: float | None, macro: float | None, trend: float | None) -> int:
    return sum(c in tot.SCORED and v is not None and not pd.isna(v)
               for c, v in (("cot", cot), ("macro", macro), ("trend", trend)))


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
        tr = trend_at(s, d, sign) if s is not None and not s.empty else {"score": None, "p_sma200": None}
        trend = tr["score"]
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
                     "total": total, "bias": tot.bias_for(total, n_components(cot, macro, trend), trend, tr["p_sma200"])})
    return pd.DataFrame(rows)


# ── Uebersicht aller Maerkte ──────────────────────────────────────────────────

def overview(cot: pd.DataFrame, macro: pd.DataFrame, trend: pd.DataFrame,
             series: dict[str, pd.Series] | None = None) -> pd.DataFrame:
    """Gesamt-Score fuer alle Maerkte der Asset-Scorecard (COT-Maerkte + Zusatzmaerkte
    der Trend-Karte) wie :func:`total_data.combine`, dazu Kursbestaetigung und
    relative Staerke (Kontext). ``trend`` ist die Tabelle von :func:`trend_data.scorecard`."""
    mk = [(k, "", "", g, None) for k, g in markets()]
    tr = trend[~trend["missing"]] if "missing" in trend else trend
    out = tot.combine(cot, macro, tr, markets=mk)
    p200 = dict(zip(tr["key"], tr["p_sma200"])) if "p_sma200" in tr else {}
    out["p_sma200"] = [p200.get(k) for k in out["key"]]
    out["unconfirmed"] = [tot.unconfirmed(x, tv, p) for x, tv, p in
                          zip(out["total"], out["trend"], out["p_sma200"])]
    rs = [relative_strength(k, series) if series else None for k in out["key"]]
    out["rs_diff"] = [r["diff"] if r else None for r in rs]
    out["rs_rank"] = [r["rank"] if r else None for r in rs]
    out["rs_n"] = [r["n"] if r else None for r in rs]
    out["rs_peer"] = [r["peer"] if r else None for r in rs]
    return out


CHANGE_WEEKS = (1, 4)


def past_totals(series: dict[str, pd.Series], obs: pd.DataFrame | None, day: dt.date,
                keys: list[str] | None = None) -> dict[str, float | None]:
    """Gesamt-Score je Markt zum frueheren Datum ``day``, kausal wie :func:`history`:
    Trend nur aus Kursen bis ``day``, COT nach Veroeffentlichung, Makro nur mit Werten,
    die zu dem Tag (geschaetzt) schon veroeffentlicht waren."""
    tab = macro_at(obs, day) if obs is not None and not obs.empty else pd.DataFrame()
    out = {}
    for key in keys or [k for k, _g in markets()]:
        s = series.get(key)
        if s is None or s.empty:
            out[key] = None
            continue
        tr = trend_at(s, day, td.SIGN.get(key, 1))
        c = cot_at(cd.load_history(key), day) if has_cot(key) and "cot" in tot.SCORED else None
        cot = None if c is None else float(c["score"])
        macro = float(tab.at[key, "score"]) if has_macro(key) and key in tab.index else None
        out[key] = total_of(cot, macro, tr["score"])
    return out


def score_changes(current: pd.DataFrame, series: dict[str, pd.Series], obs: pd.DataFrame | None,
                  today: dt.date, weeks: tuple[int, ...] = CHANGE_WEEKS) -> pd.DataFrame:
    """Veraenderung des Gesamt-Scores gegen vor ``weeks`` Wochen (Spalten ``chg_<w>w``).

    Der heutige Wert kommt aus ``current`` (= Karte), der fruehere aus
    :func:`past_totals`. Wie im Verlauf der Asset-Scorecard gilt: heute zaehlen alle
    geladenen Makrowerte, frueher nur die (geschaetzt) veroeffentlichten."""
    out = current[["key"]].copy()
    now = dict(zip(current["key"], current["total"]))
    for w in weeks:
        past = past_totals(series, obs, today - dt.timedelta(weeks=w), list(current["key"]))
        out[f"chg_{w}w"] = [None if now.get(k) is None or pd.isna(now.get(k)) or past.get(k) is None
                            else round(now[k] - past[k], 1) for k in out["key"]]
    return out
