"""Edge Scores — Uebersicht: alle Maerkte als Kacheln mit Mini-Tacho.

Je Markt eine Kachel mit Gesamt-Score (Tacho −10 … +10), Bias, den Komponenten
(Trend, Makro, COT nur als Kontext), relativer Staerke und dem Kursverlauf der
letzten sechs Monate. "Details" oeffnet die Asset-Scorecard des Marktes.

Die Tachos sind kleines Inline-SVG statt Plotly — 43 Plotly-Grafiken wuerden die
Seite spuerbar bremsen. Gerechnet wird nichts Neues: dieselben Caches wie die
Einzelkarten und :func:`asset_card_data.overview` (= Logik des Gesamt-Scores).
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import math

import pandas as pd
import streamlit as st

from tradinglib import asset_card_data as ac
from tradinglib import cot_data as cd
from tradinglib import macro_data as md
from tradinglib import total_data as tot
from tradinglib.cot_page import _BIAS_COLORS
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

GROUP_ORDER = ["fx", "commodities", "indices", "indices_world", "rates", "crypto"]
_PER_ROW = 4
_SPARK_DAYS = 126
# solid colours for the gauge arc (the table colours are translucent backgrounds)
_ARC = [(-10, -5, "#c62828"), (-5, -2, "#ef6c00"), (-2, 2, "#9e9e9e"), (2, 5, "#7cb342"), (5, 10, "#2e7d32")]

_CSS = """<style>
.ov-tile{border:1px solid rgba(128,128,128,.35);border-radius:8px;padding:.45rem .6rem .3rem;margin-bottom:.25rem}
.ov-name{font-weight:700;font-size:.95rem;display:flex;justify-content:space-between;align-items:center;gap:.3rem}
.ov-pill{font-size:.72rem;font-weight:600;padding:.05rem .45rem;border-radius:4px;white-space:nowrap}
.ov-rows{width:100%;font-size:.8rem;border-collapse:collapse}
.ov-rows td{padding:.05rem 0;border:none}
.ov-rows td:last-child{text-align:right;font-weight:600}
.ov-ctx{opacity:.55}
.ov-chg{font-size:.78rem;text-align:center;margin:-.1rem 0 .2rem}
.ov-warn{font-size:.74rem;color:#ef6c00;margin-top:.15rem;min-height:1.1rem}
</style>"""


# ── SVG-Bausteine ─────────────────────────────────────────────────────────────

def _xy(v: float, r: float, cx: float = 60, cy: float = 56) -> tuple[float, float]:
    a = math.pi * (1 - (max(min(v, 10), -10) + 10) / 20)
    return cx + r * math.cos(a), cy - r * math.sin(a)


def gauge_svg(total: float | None) -> str:
    """Halbkreis-Tacho −10 … +10 als Inline-SVG mit Nadel auf ``total`` (leer: keine Nadel)."""
    parts = []
    for lo, hi, col in _ARC:
        x1, y1 = _xy(lo, 44)
        x2, y2 = _xy(hi, 44)
        parts.append(f"<path d='M{x1:.1f},{y1:.1f} A44,44 0 0 1 {x2:.1f},{y2:.1f}' "
                     f"stroke='{col}' stroke-width='10' fill='none'/>")
    if total is not None and not pd.isna(total):
        x, y = _xy(float(total), 38)
        parts.append(f"<line x1='60' y1='56' x2='{x:.1f}' y2='{y:.1f}' stroke='#1e88e5' "
                     "stroke-width='3' stroke-linecap='round'/><circle cx='60' cy='56' r='4' fill='#1e88e5'/>")
        label = f"{float(total):+.1f}"
    else:
        label = "–"
    parts.append(f"<text x='60' y='77' text-anchor='middle' font-size='15' font-weight='700' "
                 f"fill='currentColor'>{label}</text>")
    return ("<svg viewBox='0 0 120 80' width='120' height='80' style='display:block;margin:auto'>"
            + "".join(parts) + "</svg>")


def spark_svg(s: pd.Series | None, days: int = _SPARK_DAYS) -> str:
    """Kursverlauf der letzten ``days`` Handelstage (gruen gestiegen, rot gefallen)."""
    if s is None or len(s) < 10:
        return ""
    v = s.iloc[-days:].astype(float).to_numpy()
    lo, hi = float(v.min()), float(v.max())
    span = hi - lo or 1.0
    w, h = 200, 28
    pts = " ".join(f"{i * w / (len(v) - 1):.1f},{h - 2 - (x - lo) / span * (h - 4):.1f}"
                   for i, x in enumerate(v))
    col = "#43a047" if v[-1] >= v[0] else "#e53935"
    return (f"<svg viewBox='0 0 {w} {h}' preserveAspectRatio='none' width='100%' height='{h}' "
            f"style='display:block;margin-top:.2rem'>"
            f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='1.5'/></svg>")


def _num(v, fmt: str = "{:+.0f}") -> str:
    return "–" if v is None or pd.isna(v) else fmt.format(v)


def _chg(v) -> str:
    if v is None or pd.isna(v):
        return "–"
    col = "#43a047" if v > 0 else "#e53935" if v < 0 else "inherit"
    arrow = "▲" if v > 0 else "▼" if v < 0 else "■"
    return f"<span style='color:{col};font-weight:600'>{arrow} {v:+.1f}</span>"


def tile_html(r: dict, series: pd.Series | None) -> str:
    esc = html.escape
    bias = r["bias"]
    pill = (f"<span class='ov-pill' style='background:{_BIAS_COLORS[bias]}'>"
            f"{esc(t('cot.bias_' + bias))}</span>")
    rows = [(t("ov.trend"), _num(r["trend"]), ""),
            (t("ov.macro"), _num(r["macro"]), ""),
            (t("ov.cot"), _num(r["cot"]), "ov-ctx")]
    rank = r.get("rs_rank")
    rows.append((t("ov.rs"), f"{r['rs_diff']:+.1f} Pp · {int(rank)}/{int(r['rs_n'])}"
                 if rank is not None and not pd.isna(rank) else "–", ""))
    body = "".join(f"<tr class='{c}'><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b, c in rows)
    warn = esc("⚠ " + t("ov.unconfirmed")) if r["unconfirmed"] else "&nbsp;"
    return (f"<div class='ov-tile'><div class='ov-name'><span>{esc(t('cot.mkt_' + r['key']))}</span>{pill}</div>"
            f"{gauge_svg(r['total'])}"
            f"<div class='ov-chg' title='{esc(t('ov.chg_help'))}'>{esc(t('ov.chg_1w'))} {_chg(r.get('chg_1w'))}"
            f" &nbsp; {esc(t('ov.chg_4w'))} {_chg(r.get('chg_4w'))}</div>"
            f"<table class='ov-rows'>{body}</table>"
            f"<div class='ov-warn'>{warn}</div>{spark_svg(series)}</div>")


# ── Daten ─────────────────────────────────────────────────────────────────────

def _load():
    from tradinglib.cot_page import _scorecard_cached as cot_cached
    from tradinglib.macro_page import _scorecard_cached as macro_cached
    from tradinglib.trend_page import _compute as trend_cached, _markets_sig
    cd.ensure_fresh()
    md.ensure_fresh()
    cot_date = cd.latest_report_date()
    cot = cot_cached(cot_date.isoformat()) if cot_date else pd.DataFrame(columns=["key", "score"])
    stamp = md.get_meta("last_success") or ""
    macro, _lat = macro_cached(stamp) if stamp else (pd.DataFrame(), None)
    day = dt.date.today().isoformat()
    trend, series = trend_cached(day, _markets_sig())
    df = ac.overview(cot, macro, trend, series)
    now = dict(zip(df["key"], df["total"]))
    for w in ac.CHANGE_WEEKS:
        try:
            past = _past_totals(day, w, stamp, cot_date.isoformat() if cot_date else "", _markets_sig())
        except Exception:
            logger.exception("edge overview: past totals (%s w) failed", w)
            past = {}
        df[f"chg_{w}w"] = [None if pd.isna(now[k]) or past.get(k) is None else round(now[k] - past[k], 1)
                           for k in df["key"]]
    return df, series, cot_date


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _past_totals(day: str, weeks: int, macro_stamp: str, cot_stamp: str, markets_sig: str) -> dict:
    """Gesamt-Score je Markt vor ``weeks`` Wochen (Stempel nur als Cache-Schluessel)."""
    from tradinglib.trend_page import _compute as trend_cached
    _card, series = trend_cached(day, markets_sig)
    obs = md.load_obs()
    return ac.past_totals(series, obs, dt.date.fromisoformat(day) - dt.timedelta(weeks=weeks))


def _open_asset(key: str) -> None:
    """Klick auf "Details": Asset-Scorecard mit diesem Markt (laeuft vor dem Rerun)."""
    st.session_state["_edge_card"] = "asset"
    st.session_state["_asset_card_key"] = key


def top_setup(df: pd.DataFrame) -> pd.Series | None:
    """Staerkstes Forex-Paar, bei dem alle zaehlenden Komponenten einig sind."""
    fx = df[(df["group"] == "fx") & df["total"].notna()]
    if fx.empty:
        return None
    pairs = tot.pair_setups(fx, list(fx["key"]))
    if pairs.empty:
        return None
    pairs = pairs[pairs["agree"] & (pairs["bias"] != "neutral")]
    return None if pairs.empty else pairs.iloc[0]


# ── Seite ─────────────────────────────────────────────────────────────────────

class EdgeOverviewPage:
    def __init__(self, username: str = ""):
        self.username = username

    def _header(self, df: pd.DataFrame) -> None:
        bull = int(df["bias"].isin(["bull", "strong_bull"]).sum())
        bear = int(df["bias"].isin(["bear", "strong_bear"]).sum())
        unconf = int(df["unconfirmed"].sum())
        c = st.columns(4)
        c[0].metric(t("ov.n_bull"), bull)
        c[1].metric(t("ov.n_bear"), bear)
        c[2].metric(t("ov.n_neutral"), len(df) - bull - bear, help=t("ov.n_neutral_help", n=unconf))
        p = top_setup(df)
        if p is None:
            c[3].metric(t("ov.top_setup"), "–")
        else:
            c[3].metric(t("ov.top_setup"), p["pair"], f"{p['score']:+.1f} · {t('cot.bias_' + p['bias'])}",
                        delta_color="normal" if p["score"] > 0 else "inverse")

    def render(self, side=None) -> None:
        from tradinglib.edge_scores_page import title
        title(t("edge.card_overview"))
        st.markdown(_CSS, unsafe_allow_html=True)
        if side is not None:
            with side:
                if st.button(t("cot.refresh"), key="_ov_refresh", width="stretch"):
                    from tradinglib.trend_page import _compute
                    _compute.clear()
                    _past_totals.clear()
        with st.spinner(t("asset.loading")):
            df, series, cot_date = _load()
        df = df[df["total"].notna()]
        if df.empty:
            st.info(t("cot.no_data"))
            return
        self._header(df)
        groups = [g for g in GROUP_ORDER if g in set(df["group"])]
        f1, f2, f3 = st.columns([3, 2, 2])
        sel = f1.multiselect(t("ov.groups"), groups, default=groups, key="_ov_groups",
                             format_func=lambda g: t("cot.group_" + g))
        only = f2.checkbox(t("ov.only_bias"), key="_ov_only_bias")
        sort = f3.selectbox(t("ov.sort"), ["group", "strength", "chg_1w", "chg_4w"], key="_ov_sort",
                            format_func=lambda o: t("ov.sort_" + o))
        st.caption(t("ov.caption", cot=cot_date.strftime("%d.%m.%Y") if cot_date else "–"))
        for g in [g for g in groups if g in sel]:
            part = df[df["group"] == g]
            if only:
                part = part[part["bias"] != "neutral"]
            if part.empty:
                continue
            if sort == "strength":
                part = part.reindex(part["total"].abs().sort_values(ascending=False, kind="stable").index)
            elif sort in ("chg_1w", "chg_4w"):
                part = part.reindex(pd.to_numeric(part[sort], errors="coerce").abs()
                                    .sort_values(ascending=False, kind="stable", na_position="last").index)
            st.markdown(f"**{t('cot.group_' + g)}**")
            recs = part.to_dict("records")
            for i in range(0, len(recs), _PER_ROW):
                cols = st.columns(_PER_ROW)
                for col, r in zip(cols, recs[i:i + _PER_ROW]):
                    with col:
                        st.markdown(tile_html(r, series.get(r["key"])), unsafe_allow_html=True)
                        st.button(t("ov.details"), key=f"_ov_open_{r['key']}", width="stretch",
                                  on_click=_open_asset, args=(r["key"],))
        with st.expander(t("cot.tab_method")):
            st.markdown(t("ov.method_md"))
