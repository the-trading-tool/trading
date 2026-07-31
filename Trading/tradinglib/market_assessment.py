"""Markt-Beurteilung fürs Dashboard.

Verdichtet die vier Markt-Bereiche der App zu einer kompakten Lage-Einschätzung
für den in ``system_config`` hinterlegten Default-Ticker:

  * **Fear & Greed** (:mod:`tradinglib.fear_greed`) — Stimmung 0–100 des Index.
  * **Global Rotation** (:mod:`tradinglib.global_rotation`) — RRG-Quadrant + Zu-/
    Abfluss (Mansfield RSC) des Heimatmarkts sowie der stärkste/schwächste
    Cross-Asset-Wert.
  * **Korrelationsindex** (:mod:`tradinglib.correlation_index`) — aktuelle
    90-Tage-Korrelation Aktien↔Anleihen als Risiko-Regime-Signal.
  * **Sector Rotation** (:mod:`tradinglib.sector_rotation`) — führender und
    zurückfallender Sektor der Heimatregion (Mansfield RSC).

Jeder Block ist einzeln in ``try/except`` gekapselt: fällt eine Quelle aus,
bleibt der Rest sichtbar. Die Einzelsignale werden zu einer groben
Risk-on/Neutral/Risk-off-Beurteilung zusammengefasst.
"""
from __future__ import annotations
import logging

import numpy as np
import streamlit as st

from tradinglib.i18n import t

logger = logging.getLogger(__name__)

# US-Indizes → US-Sektoren; alles andere → EU-Sektoren (App ist EUR-/EU-zentriert).
_US_INDICES = {"^SPX", "^GSPC", "^DJI", "^NDX", "^IXIC", "^RUT", "^OEX"}

# Fear & Greed unterstützt nur diese Indizes (lokale Breadth je Index) — sonst ^SPX.
_FG_INDICES = {"^SPX", "^RUT", "^GDAXI", "^MDAXI", "^SDAXI", "^N225", "^FTSE", "^IBEX", "^SSMI"}

# Sektor-Charakter für die Risk-on/off-Wertung (Namen aus sector_rotation.py).
_DEFENSIVE = {"Utilities", "Consumer Staples", "Health Care", "Food & Beverage",
              "Real Estate", "Telecom"}
_CYCLICAL = {"Technology", "Financials", "Banks", "Fin. Services", "Consumer Discret.",
             "Consumer Discr.", "Industrials", "Materials", "Basic Resources", "Energy",
             "Oil & Gas", "Automobiles", "Chemicals", "Communication", "Insurance"}


def _fg_index_for(ticker: str) -> str:
    return ticker if ticker in _FG_INDICES else "^SPX"


def _sector_universe_for(ticker: str) -> str:
    return "US Sectors" if ticker in _US_INDICES else "EU Sectors"


# ── Einzelblöcke ──────────────────────────────────────────────────────────────

def _block_fear_greed(ticker: str) -> dict | None:
    try:
        from tradinglib import fear_greed as fg
        idx = _fg_index_for(ticker)
        r = fg.compute(idx)
        score = r.get("score")
        if score is None or (isinstance(score, float) and np.isnan(score)):
            return None
        return {"index": idx, "score": float(score), "band": r.get("label", "")}
    except Exception as exc:
        logger.warning("market_assessment: fear_greed block failed: %s", exc)
        return None


def _block_rotation(ticker: str) -> dict | None:
    """Heimatmarkt-Quadrant/RSC (aus dem Aktien-Universum) + Cross-Asset-Extreme."""
    try:
        from tradinglib import global_rotation as gr
        home = None
        eq = gr.compute(gr.EQUITIES)
        # Heimatmarkt = der Markt, dessen Index == default_ticker.
        home_code = next((c for c, idx, _, _ in gr.EQUITIES if idx == ticker), None)
        if home_code:
            home = next((m for m in eq.get("markets", []) if m["code"] == home_code), None)

        cross = gr.compute(gr.ASSETS_ALL).get("markets", [])
        cross = [m for m in cross if m.get("rsc") is not None]
        leader = laggard = None
        if cross:
            leader = max(cross, key=lambda m: m["rsc"])
            laggard = min(cross, key=lambda m: m["rsc"])
        if home is None and leader is None:
            return None
        return {
            "home": ({"code": home["code"], "quadrant": home["quadrant"],
                      "rsc": home.get("rsc"), "rsc_prev": home.get("rsc_prev")}
                     if home else None),
            "leader": ({"code": leader["code"], "rsc": leader["rsc"],
                        "cls": leader.get("class")} if leader else None),
            "laggard": ({"code": laggard["code"], "rsc": laggard["rsc"],
                         "cls": laggard.get("class")} if laggard else None),
        }
    except Exception as exc:
        logger.warning("market_assessment: rotation block failed: %s", exc)
        return None


def _block_correlation() -> dict | None:
    """Aktuelle 90-Tage-Korrelation Aktien↔Anleihen (zn_spx) als Regime-Signal."""
    try:
        from tradinglib import correlation_index as ci
        series, _meta, _ = ci.load_from_db()
        if series is None or series.empty:
            return None
        sub = series[series["pair_id"] == "zn_spx"]
        if sub.empty:
            return None
        sub = sub.sort_values("Date")
        val = sub["corr_90"].dropna()
        if val.empty:
            return None
        c = float(val.iloc[-1])
        if c <= -0.2:
            regime = "hedge"           # negativ: Anleihen federn Aktien ab (normal)
        elif c >= 0.2:
            regime = "coupled"         # positiv: zinsgetrieben, beide laufen gleich
        else:
            regime = "neutral"
        return {"value": c, "regime": regime}
    except Exception as exc:
        logger.warning("market_assessment: correlation block failed: %s", exc)
        return None


def _block_sector(ticker: str) -> dict | None:
    try:
        from tradinglib import sector_rotation as sr
        uni_key = _sector_universe_for(ticker)
        uni = sr.UNIVERSES[uni_key]
        engine = sr.SectorRotation(sector_etfs=uni["etfs"], benchmark=uni["benchmark"],
                                   weights=uni.get("weights"))
        engine.fetch_all()
        ranked = []
        for name, etf in uni["etfs"].items():
            s = engine.calc_mansfield_rsc_series(etf)
            if s is None or s.empty:
                continue
            cur = float(s.iloc[-1])
            prev = float(s.iloc[-2]) if len(s) >= 2 else None
            if not np.isnan(cur):
                ranked.append((name, cur, prev))
        if not ranked:
            return None
        ranked.sort(key=lambda x: x[1], reverse=True)
        return {"universe": uni_key,
                "leader": ranked[0], "laggard": ranked[-1]}
    except Exception as exc:
        logger.warning("market_assessment: sector block failed: %s", exc)
        return None


# ── Verdichtung ───────────────────────────────────────────────────────────────

_QUAD_SCORE = {"Leading": 1.0, "Improving": 0.5, "Weakening": -0.5, "Lagging": -1.0}


def _verdict(fg_b, rot_b, sec_b) -> tuple[str, float]:
    score = 0.0
    if fg_b:
        s = fg_b["score"]
        score += 1.0 if s > 55 else (-1.0 if s < 45 else 0.0)
    if rot_b and rot_b.get("home"):
        score += _QUAD_SCORE.get(rot_b["home"]["quadrant"], 0.0)
    if sec_b:
        lead = sec_b["leader"][0]
        if lead in _CYCLICAL:
            score += 1.0
        elif lead in _DEFENSIVE:
            score -= 1.0
    if score > 0.5:
        return "risk_on", score
    if score < -0.5:
        return "risk_off", score
    return "neutral", score


@st.cache_data(ttl=1800, show_spinner=False)
def assess(ticker: str, day: str) -> dict:
    """Vier-Block-Marktbeurteilung für ``ticker``. ``day`` hält den Cache
    tagesaktuell (geht in den Cache-Key ein)."""
    fg_b = _block_fear_greed(ticker)
    rot_b = _block_rotation(ticker)
    corr_b = _block_correlation()
    sec_b = _block_sector(ticker)
    verdict, vscore = _verdict(fg_b, rot_b, sec_b)
    return {"ticker": ticker, "fg": fg_b, "rotation": rot_b, "correlation": corr_b,
            "sector": sec_b, "verdict": verdict, "verdict_score": vscore}


# ── Rendering ─────────────────────────────────────────────────────────────────

_VERDICT_COLOR = {"risk_on": "#2E7D32", "neutral": "#9E9E9E", "risk_off": "#C62828"}
_FG_BAND_KEYS = {
    "Extreme Fear": "fg.band_extreme_fear", "Fear": "fg.band_fear",
    "Neutral": "fg.band_neutral", "Greed": "fg.band_greed",
    "Extreme Greed": "fg.band_extreme_greed",
}
_QUAD_KEYS = {
    "Leading": "gr.q_leading", "Weakening": "gr.q_weakening",
    "Lagging": "gr.q_lagging", "Improving": "gr.q_improving",
}
_CLASS_KEYS = {
    "Equity": "gr.uni_equity", "Bond": "gr.uni_bond", "Metal": "gr.uni_metal",
    "Energy": "gr.uni_energy", "Agri": "gr.uni_agri", "Crypto": "gr.uni_crypto",
}


def _rsc_text(cur, prev) -> str | None:
    """RSC-Zeile: aktueller Wert plus Wochen-Veränderung (Δ, in pp)."""
    if cur is None:
        return None
    if prev is not None:
        return t("ma.rsc_delta_change", val=f"{cur:+.1f}", chg=f"{cur - prev:+.1f}")
    return t("ma.rsc_delta", val=f"{cur:+.1f}")


def _fg_color(score: float) -> str:
    if score < 25:
        return "#C62828"
    if score < 45:
        return "#EF6C00"
    if score <= 55:
        return "#9E9E9E"
    if score <= 75:
        return "#7CB342"
    return "#2E7D32"


def render(region=st, username: str = "admin", db_path: str = "database") -> None:
    """Kompakter Marktlage-Header fürs Dashboard. Robust — bei Fehlern still."""
    try:
        from tradinglib import system_config as sysconf
        import datetime as dt
        ticker = (sysconf.SystemConfig(username=username)
                  .get_value("default_ticker", "^GDAXI")) or "^GDAXI"
    except Exception:
        ticker = "^GDAXI"

    try:
        with region.spinner(t("ma.computing")):
            import datetime as dt
            data = assess(ticker, dt.date.today().isoformat())
    except Exception as exc:
        logger.warning("market_assessment.render failed: %s", exc)
        return

    if not any((data.get("fg"), data.get("rotation"),
                data.get("sector"), data.get("correlation"))):
        return

    tk = str(ticker).lstrip("^")
    verdict = data["verdict"]
    vcolor = _VERDICT_COLOR.get(verdict, "#9E9E9E")
    vlabel = t(f"ma.verdict_{verdict}")

    region.markdown(
        f"### {t('ma.header', ticker=tk)} "
        f"<span style='font-size:0.7em;vertical-align:middle;background:{vcolor};"
        f"color:#fff;padding:2px 10px;border-radius:12px;margin-left:8px'>"
        f"{vlabel}</span>", unsafe_allow_html=True)
    region.caption(t("ma.subtitle"))

    c1, c2, c3, c4 = region.columns(4)

    # 1. Fear & Greed
    fg_b = data.get("fg")
    if fg_b:
        band = t(_FG_BAND_KEYS.get(fg_b["band"], "fg.band_neutral"))
        c1.metric(t("ma.fg_label", index=str(fg_b["index"]).lstrip("^")),
                  f"{fg_b['score']:.0f}", delta=band, delta_color="off")
    else:
        c1.metric(t("ma.fg_label", index=tk), t("ma.na"))

    # 2. Global Rotation (Heimatmarkt)
    rot_b = data.get("rotation")
    home = rot_b.get("home") if rot_b else None
    if home:
        quad = t(_QUAD_KEYS.get(home["quadrant"], "gr.q_lagging"))
        delta = _rsc_text(home.get("rsc"), home.get("rsc_prev"))
        c2.metric(t("ma.rotation_label", code=home["code"]), quad,
                  delta=delta, delta_color="off")
    else:
        c2.metric(t("ma.rotation_label", code=tk), t("ma.na"))

    # 3. Korrelation Aktien↔Anleihen
    corr_b = data.get("correlation")
    if corr_b:
        regime = t(f"ma.corr_regime_{corr_b['regime']}")
        c3.metric(t("ma.corr_label"), f"{corr_b['value']:+.2f}",
                  delta=regime, delta_color="off")
    else:
        c3.metric(t("ma.corr_label"), t("ma.na"))

    # 4. Sector Rotation (führender Sektor)
    sec_b = data.get("sector")
    if sec_b:
        name, rsc, prev = sec_b["leader"]
        c4.metric(t("ma.sector_label"), name,
                  delta=_rsc_text(rsc, prev), delta_color="off")
    else:
        c4.metric(t("ma.sector_label"), t("ma.na"))

    # Synthese-Zeile: Cross-Asset-Extreme + schwächster Sektor
    bits = []
    if rot_b and rot_b.get("leader"):
        ld = rot_b["leader"]
        cls = t(_CLASS_KEYS.get(ld.get("cls"), "")) if ld.get("cls") else ""
        bits.append(t("ma.cross_leader", name=ld["code"], cls=cls, val=f"{ld['rsc']:+.1f}"))
    if rot_b and rot_b.get("laggard"):
        lg = rot_b["laggard"]
        cls = t(_CLASS_KEYS.get(lg.get("cls"), "")) if lg.get("cls") else ""
        bits.append(t("ma.cross_laggard", name=lg["code"], cls=cls, val=f"{lg['rsc']:+.1f}"))
    if sec_b:
        nm, rsc = sec_b["laggard"][0], sec_b["laggard"][1]
        bits.append(t("ma.sector_laggard", name=nm, val=f"{rsc:+.1f}"))
    if bits:
        region.caption(" · ".join(bits))
    region.divider()
