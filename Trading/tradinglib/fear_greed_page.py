"""Fear & Greed Index — Streamlit-Seite (Market-Bereich).

Zeigt den zusammengesetzten 0–100-Wert als Gauge, die sieben Sub-Komponenten
als Balken und eine Prosa-Erklärung von Quellen und Methodik. Rechenlogik liegt
in :mod:`tradinglib.fear_greed`.
"""
from __future__ import annotations
import datetime as dt

import streamlit as st
import plotly.graph_objects as go

from tradinglib import fear_greed as fg
from tradinglib.i18n import t

# Reihenfolge + Locale-Key je Komponente
_COMPONENTS = [
    ("momentum",        "fg.comp_momentum"),
    ("breadth",         "fg.comp_breadth"),
    ("strength_52w",    "fg.comp_strength_52w"),
    ("volatility",      "fg.comp_volatility"),
    ("term_structure",  "fg.comp_term_structure"),
    ("safe_haven",      "fg.comp_safe_haven"),
    ("junk_demand",     "fg.comp_junk_demand"),
]

# Englische Modul-Labels → Locale-Keys für die Bänder
_BAND_KEYS = {
    "Extreme Fear":  "fg.band_extreme_fear",
    "Fear":          "fg.band_fear",
    "Neutral":       "fg.band_neutral",
    "Greed":         "fg.band_greed",
    "Extreme Greed": "fg.band_extreme_greed",
}

# Auswahl-Indizes (Breadth = lokal je Index; Makro-Reihen sind US/global)
_INDEX_OPTIONS = ["^SPX", "^RUT", "^GDAXI", "^MDAXI", "^SDAXI", "^N225", "^FTSE", "^IBEX", "^SSMI"]


def _color(score: float) -> str:
    """Rot (Angst) → Grau (Neutral) → Grün (Gier)."""
    if score < 25:
        return "#C62828"
    if score < 45:
        return "#EF6C00"
    if score <= 55:
        return "#9E9E9E"
    if score <= 75:
        return "#7CB342"
    return "#2E7D32"


@st.cache_data(ttl=1800, show_spinner=False)
def _compute_cached(index: str, day: str) -> dict:
    # `day` (ohne führenden Unterstrich → geht in den Cache-Key ein) hält den
    # Cache tagesaktuell: neuer Tag → Neuberechnung.
    return fg.compute(index)


class FearGreedPage:
    def __init__(self, username: str = "", db_path: str = "database"):
        self.username = username
        self.db_path = db_path

    # ── Teil-Renderer ─────────────────────────────────────────────────────────
    def _gauge(self, score: float, label: str):
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            number={"suffix": " / 100", "font": {"size": 34}},
            gauge={
                "axis": {"range": [0, 100], "tickvals": [0, 25, 45, 55, 75, 100]},
                "bar": {"color": _color(score), "thickness": 0.28},
                "steps": [
                    {"range": [0, 25],   "color": "rgba(198,40,40,0.18)"},
                    {"range": [25, 45],  "color": "rgba(239,108,0,0.16)"},
                    {"range": [45, 55],  "color": "rgba(158,158,158,0.16)"},
                    {"range": [55, 75],  "color": "rgba(124,179,66,0.16)"},
                    {"range": [75, 100], "color": "rgba(46,125,50,0.18)"},
                ],
                "threshold": {"line": {"color": _color(score), "width": 4},
                              "thickness": 0.75, "value": score},
            },
        ))
        fig.update_layout(height=260, margin=dict(t=10, b=10, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown(
            f"<div style='text-align:center;font-size:22px;font-weight:600;"
            f"color:{_color(score)};margin-top:-8px'>{label}</div>",
            unsafe_allow_html=True)

    def _components(self, result: dict):
        comps = result.get("components", {})
        detail = result.get("detail", {})
        st.markdown(f"##### {t('fg.components_header')}")
        for key, lkey in _COMPONENTS:
            if key not in comps:
                continue
            val = comps[key]
            c1, c2 = st.columns([0.32, 0.68])
            c1.markdown(f"**{t(lkey)}**")
            c2.progress(int(max(0, min(100, val))) / 100.0,
                        text=f"{val:.0f} / 100  ·  {detail.get(key, '')}")

    # ── Einstieg ──────────────────────────────────────────────────────────────
    def render(self):
        st.markdown(f"## {t('fg.title')}")
        st.caption(t('fg.subtitle'))

        col_idx, col_btn = st.columns([0.5, 0.5])
        index = col_idx.selectbox(t('fg.index_label'), _INDEX_OPTIONS, index=0,
                                  key="_fg_index")
        if col_btn.button(t('fg.refresh'), key="_fg_refresh"):
            _compute_cached.clear()

        with st.spinner(t('fg.computing')):
            result = _compute_cached(index, dt.date.today().isoformat())

        score = result.get("score")
        if score is None or (isinstance(score, float) and score != score):
            st.warning(t('fg.no_data', index=index))
            return

        band = t(_BAND_KEYS.get(result.get("label", ""), "fg.band_neutral"))
        left, right = st.columns([0.42, 0.58])
        with left:
            self._gauge(score, band)
        with right:
            self._components(result)

        st.caption(t('fg.count_note', n=result.get("n_components", 0)))

        # ── Prosa: Quellen & Methodik ──────────────────────────────────────────
        with st.expander(t('fg.methodology_header'), expanded=False):
            st.markdown(t('fg.methodology_md'))

        st.info(t('fg.macro_note'))


def render_compact_scale(index: str = "^SPX", region=st) -> None:
    """Kompakte Fear-&-Greed-Skala (horizontale Bullet-Gauge) zum Einbetten in
    andere Seiten (z. B. Market Map). Rendert nichts, wenn für den Index keine
    ausreichenden Daten vorliegen (z. B. 'ANY' oder Nicht-Index-Auswahl)."""
    if not index or not str(index).startswith("^"):
        return
    try:
        result = _compute_cached(index, dt.date.today().isoformat())
    except Exception:
        return
    score = result.get("score")
    if score is None or (isinstance(score, float) and score != score):
        return
    band = t(_BAND_KEYS.get(result.get("label", ""), "fg.band_neutral"))
    col = _color(score)
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"font": {"size": 20}, "suffix": " / 100"},
        gauge={
            "shape": "bullet",
            "axis": {"range": [0, 100], "tickvals": [0, 25, 45, 55, 75, 100]},
            "bar": {"color": col, "thickness": 0.55},
            "steps": [
                {"range": [0, 25],   "color": "rgba(198,40,40,0.18)"},
                {"range": [25, 45],  "color": "rgba(239,108,0,0.16)"},
                {"range": [45, 55],  "color": "rgba(158,158,158,0.16)"},
                {"range": [55, 75],  "color": "rgba(124,179,66,0.16)"},
                {"range": [75, 100], "color": "rgba(46,125,50,0.18)"},
            ],
            "threshold": {"line": {"color": col, "width": 3},
                          "thickness": 0.85, "value": score},
        },
    ))
    fig.update_layout(height=90, margin=dict(t=6, b=6, l=10, r=10))
    # Halbe Breite: in die linke von zwei Spalten rendern.
    half, _ = region.columns(2)
    half.markdown(
        f"<div style='font-size:14px;margin-bottom:-4px'>"
        f"<b>{t('fg.title')} — {str(index).lstrip('^')}</b> · "
        f"<span style='color:{col};font-weight:600'>{band}</span></div>",
        unsafe_allow_html=True)
    half.plotly_chart(fig, use_container_width=True,
                      config={"displayModeBar": False}, key=f"_fg_mini_{index}")
