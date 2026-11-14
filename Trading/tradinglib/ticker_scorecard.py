"""Scorecard eines beliebigen Tickers fuer den Asset Viewer (Tab "Scorecard").

Zwei Faelle:

* **Der Ticker ist ein Edge-Markt** (z. B. ``^GDAXI``, ``GC=F``, ``EURUSD=X``):
  :func:`edge_market_for` liefert den Markt-Key, der Tab zeigt die Asset-Scorecard.
* **Einzeltitel**: eine eigene Scorecard mit drei Komponenten, alle aus lokalen
  Tageskursen (``yf_<TICKER>.db``, nur lesend):

  ============== ============ =========================================================
  Komponente     Spanne       Regel
  ============== ============ =========================================================
  Trend & Saison −4 … +4      wie die Trend-Karte (SMA200, SMA50/200, 3-M-Momentum, Saison)
  Rel. Staerke   −2 … +2      13-W-Rendite minus Index-Rendite: ±1 ab ±2 Pp, ±2 ab ±10 Pp
  Marktumfeld    −10 … +10    Gesamt-Score des Index (fuer Indizes = Trend, ×2,5)
  ============== ============ =========================================================

  Gesamt = 10 × Mittel der normierten vorhandenen Komponenten; Bias und Bremsen wie
  im Gesamt-Score (:func:`total_data.bias_for`: Kursbestaetigung, Deckel bei einer
  Komponente). Die Gewichtung ist nicht gemessen — das steht auch in der Methodik.

Nur Kontext (nicht im Score): Risikostufe, 4PS-Phase, Markov-Regime, Minervini-RS aus
der letzten ``asset_simulation``-Zeile sowie der Makro-Score der Handelswaehrung.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3

import pandas as pd

from tradinglib import asset_card_data as ac
from tradinglib import total_data as tot
from tradinglib import trend_data as td

logger = logging.getLogger(__name__)

SCALE = {"trend": 4.0, "rs": 2.0, "market": 10.0}
RS_STRONG = 10.0                     # Pp Abstand fuer ±2
CONTEXT_COLUMNS = ["riskScore", "riskBucket", "fps_phase", "markov_regime", "mtt_rs", "mtt_pass", "trendScore"]
# Index-Ticker, die als Edge-Markt unter anderem Symbol gefuehrt werden
_ALIASES = {"^GSPC": "SPX", "^SPX": "SPX"}


# ── Zuordnung ─────────────────────────────────────────────────────────────────

def edge_market_for(ticker: str | None) -> str | None:
    """Edge-Markt-Key fuer einen Yahoo-Ticker (oder None)."""
    if not ticker:
        return None
    if ticker in _ALIASES:
        return _ALIASES[ticker]
    for key, sources in td.SOURCES.items():
        if any(tk == ticker for _kind, tk in sources):
            return key
    return None


def index_series(index_name: str | None, today: dt.date) -> tuple[pd.Series, str | None]:
    """Kursreihe des Index (Edge-Markt bevorzugt, sonst lokal) und sein Edge-Key."""
    if not index_name:
        return pd.Series(dtype=float), None
    key = edge_market_for(index_name)
    if key:
        s, _src = td.price_series(key, today)
        if not s.empty:
            return s, key
    return td._local_close(index_name), key


# ── Komponenten ───────────────────────────────────────────────────────────────

def rs_points(diff: float | None) -> int | None:
    if diff is None or pd.isna(diff):
        return None
    if diff >= RS_STRONG:
        return 2
    if diff >= ac.RS_THRESHOLD:
        return 1
    if diff <= -RS_STRONG:
        return -2
    if diff <= -ac.RS_THRESHOLD:
        return -1
    return 0


def combine(trend: float | None, rs: float | None, market: float | None) -> float | None:
    norm = [v / SCALE[c] for c, v in (("trend", trend), ("rs", rs), ("market", market))
            if v is not None and not pd.isna(v)]
    return None if not norm else round(10 * sum(norm) / len(norm), 1)


def n_components(*vals) -> int:
    return sum(v is not None and not pd.isna(v) for v in vals)


def card_at(s: pd.Series, idx: pd.Series | None, day: dt.date) -> dict:
    """Alle Komponenten zum Datum ``day``, nur aus Kursen bis einschliesslich ``day``."""
    s = s[s.index <= pd.Timestamp(day)]
    tr = ac.trend_at(s, day, 1)
    out = {"trend_detail": tr, "trend": tr["score"], "ret": ac.period_return(s),
           "idx_ret": None, "rs_diff": None, "rs": None, "market": None, "market_trend": None}
    if idx is not None and not idx.empty:
        idx = idx[idx.index <= pd.Timestamp(day)]
        out["idx_ret"] = ac.period_return(idx)
        if out["ret"] is not None and out["idx_ret"] is not None:
            out["rs_diff"] = out["ret"] - out["idx_ret"]
            out["rs"] = rs_points(out["rs_diff"])
        mt = ac.trend_at(idx, day, 1)["score"]
        out["market_trend"] = mt
        # Index-Gesamt-Score wie in den Edge Scores (Indizes: Makro fehlt, COT zaehlt nicht)
        out["market"] = ac.total_of(None, None, mt)
    out["total"] = combine(out["trend"], out["rs"], out["market"])
    out["n"] = n_components(out["trend"], out["rs"], out["market"])
    out["bias"] = tot.bias_for(out["total"], out["n"], out["trend"], tr["p_sma200"])
    out["unconfirmed"] = tot.unconfirmed(out["total"], out["trend"], tr["p_sma200"])
    return out


def history(s: pd.Series, idx: pd.Series | None, weeks: int = 52,
            today: dt.date | None = None) -> pd.DataFrame:
    """Gesamt-Score je Freitag der letzten ``weeks`` Wochen (kausal) plus heute."""
    today = today or dt.date.today()
    last_fri = today - dt.timedelta(days=(today.weekday() - 4) % 7)
    days = [last_fri - dt.timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    if days[-1] != today:
        days.append(today)
    rows = []
    for d in days:
        c = card_at(s, idx, d)
        rows.append({"date": pd.Timestamp(d), "trend": c["trend"], "rs": c["rs"],
                     "market": c["market"], "total": c["total"], "bias": c["bias"]})
    return pd.DataFrame(rows)


def change(hist: pd.DataFrame, weeks: int) -> float | None:
    """Veraenderung des Gesamt-Scores gegen vor ``weeks`` Wochen aus dem Verlauf."""
    h = hist.dropna(subset=["total"])
    if h.empty:
        return None
    ref = h[h["date"] <= h["date"].iloc[-1] - pd.Timedelta(weeks=weeks)]
    return None if ref.empty else round(float(h["total"].iloc[-1] - ref["total"].iloc[-1]), 1)


# ── Kontext ───────────────────────────────────────────────────────────────────

def sim_context(ticker: str) -> dict:
    """Letzte Zeile aus ``asset_simulation_.db`` (nur lesend); fehlende Spalten → None."""
    path = os.path.join(td._db_dir(), "asset_simulation_.db")
    if not os.path.exists(path):
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        have = {r[1] for r in con.execute("PRAGMA table_info(asset_simulation)")}
        cols = [c for c in CONTEXT_COLUMNS if c in have]
        if not cols:
            return {}
        # nicht MAX(Date) global: der juengste Tag ist oft nur teilweise geschrieben
        row = con.execute(
            f"SELECT Date, {', '.join(cols)} FROM asset_simulation WHERE ticker = ? "
            f"ORDER BY Date DESC LIMIT 1", (ticker,)).fetchone()
    except sqlite3.Error:
        logger.exception("ticker scorecard: sim context for %s failed", ticker)
        return {}
    finally:
        con.close()
    if not row:
        return {}
    out = {"date": row[0]}
    for c, v in zip(cols, row[1:]):
        out[c] = pd.to_numeric(v, errors="coerce") if v is not None else None
    return out


def listing_currency(ticker: str) -> str | None:
    path = os.path.join(td._db_dir(), "asset_info.db")
    if not os.path.exists(path):
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT currency FROM asset_info WHERE ticker = ?", (ticker,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return str(row[0]).upper() if row and row[0] else None


# ── Kurzform fuer Chart-Raster (Asset-Uebersicht) ─────────────────────────────

BADGE_WEEKS = 4


def quick_scores(tickers: list[str], today: dt.date | None = None,
                 obs: pd.DataFrame | None = None) -> dict[str, dict]:
    """Gesamt-Score, Bias und Veraenderung (``BADGE_WEEKS``) je Ticker, wie im Scorecard-Tab.

    Edge-Maerkte bekommen den Gesamt-Score der Asset-Scorecard (Trend + Makro), Einzeltitel
    die Einzeltitel-Scorecard gegen ihren Index aus ``yf_tickers.db`` (Prioritaet wie
    :data:`edge_market.INDEX_PRIORITY`). Nur lesend; Ticker mit < 200 Kursen fehlen.
    """
    from tradinglib import edge_market as em
    from tradinglib import macro_data as md
    today = today or dt.date.today()
    past = today - dt.timedelta(weeks=BADGE_WEEKS)
    if obs is None:
        try:
            obs = md.load_obs()
        except Exception:
            obs = pd.DataFrame()
    try:
        idx_of = em.ticker_markets(list(tickers))
    except Exception:
        logger.exception("quick scores: index mapping failed")
        idx_of = {}
    idx_cache: dict[str, pd.Series] = {}

    def idx_series(key):
        if key not in idx_cache:
            idx_cache[key] = td.price_series(key, today)[0]
        return idx_cache[key]

    have_obs = obs is not None and not obs.empty
    # today: everything loaded counts as published -> same macro value as the scorecard
    macro_now = md.score_table(md.latest(obs)) if have_obs else pd.DataFrame()
    out = {}
    for tk in tickers:
        try:
            key = edge_market_for(tk)
            if key:
                s = idx_series(key)
                if len(s) < 200:
                    continue
                tr = ac.trend_at(s, today, td.SIGN.get(key, 1))
                macro = (float(macro_now.at[key, "score"])
                         if ac.has_macro(key) and key in macro_now.index else None)
                total = ac.total_of(None, macro, tr["score"])
                n = ac.n_components(None, macro, tr["score"])
                before = ac.past_totals({key: s}, obs if have_obs else None, past, [key]).get(key)
                out[tk] = {"kind": "market", "total": total, "n": n,
                           "bias": tot.bias_for(total, n, tr["score"], tr["p_sma200"]),
                           "chg": None if total is None or before is None else round(total - before, 1)}
                continue
            s = td._local_close(tk)
            if len(s) < 200:
                continue
            ik = idx_of.get(tk)
            idx = idx_series(ik) if ik else None
            c = card_at(s, idx, today)
            b = card_at(s, idx, past)
            out[tk] = {"kind": "stock", "total": c["total"], "n": c["n"], "bias": c["bias"], "index": ik,
                       "chg": None if c["total"] is None or b["total"] is None
                       else round(c["total"] - b["total"], 1)}
        except Exception:
            logger.exception("quick scores: %s failed", tk)
    return out
