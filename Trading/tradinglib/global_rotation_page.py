"""Global Rotation — Streamlit-Seite (Market-Bereich).

Visualisiert die Kapital-Rotation zwischen Märkten (Proxy über relative Stärke,
EUR-normiert): RRG-Quadranten mit Rotations-Schweif, Zu-/Abfluss-Ranking
(Mansfield RSC) und eine paarweise Relative-Stärke-Matrix. Rechenkern:
:mod:`tradinglib.global_rotation`.
"""
from __future__ import annotations
import datetime as dt

import streamlit as st
import plotly.graph_objects as go

from tradinglib import global_rotation as gr
from tradinglib.i18n import t

# Quadrant → (Farbe, x-Anker, y-Anker) für den RRG-Hintergrund
_QUAD_COLOR = {
    "Leading":   "rgba(46,125,50,0.10)",
    "Weakening": "rgba(249,171,0,0.12)",
    "Lagging":   "rgba(198,40,40,0.10)",
    "Improving": "rgba(31,120,209,0.10)",
}
_QUAD_LABEL = {
    "Leading": "gr.q_leading", "Weakening": "gr.q_weakening",
    "Lagging": "gr.q_lagging", "Improving": "gr.q_improving",
}

# Farbe je Anlageklasse (Cross-Asset-Balken)
_CLASS_COLOR = {"Equity": "#1f77b4", "Bond": "#795548", "Metal": "#C9A227",
                "Energy": "#E64A19", "Agri": "#689F38", "Crypto": "#7E57C2"}


@st.cache_data(ttl=1800, show_spinner=False)
def _compute_cached(day: str) -> dict:
    return gr.compute()               # nur Aktien (RRG + Paar-Matrix)


@st.cache_data(ttl=1800, show_spinner=False)
def _compute_flows_cached(day: str) -> dict:
    return gr.compute(gr.ASSETS_ALL)  # Cross-Asset (Zu-/Abfluss-Balken)


@st.cache_data(ttl=1800, show_spinner=False)
def _compute_universe_cached(day: str, universe: str) -> dict:
    # RRG je Anlageklasse (eigener EUR-Korb-Benchmark → vergleichbare Vola).
    return gr.compute(gr.UNIVERSES.get(universe, gr.EQUITIES))


class GlobalRotationPage:
    def __init__(self, username: str = "", db_path: str = "database"):
        self.username = username
        self.db_path = db_path

    # ── RRG-Streudiagramm ─────────────────────────────────────────────────────
    def _rrg(self, markets: list):
        xs = [m["rs_ratio"] for m in markets] + \
             [v for m in markets for v in m["tail_ratio"]]
        ys = [m["rs_momentum"] for m in markets] + \
             [v for m in markets for v in m["tail_momentum"]]
        pad = 1.5
        x0, x1 = min(xs) - pad, max(xs) + pad
        y0, y1 = min(ys) - pad, max(ys) + pad
        fig = go.Figure()
        # Quadranten-Hintergrund (Grenzen bei 100/100)
        for quad, (qx0, qx1, qy0, qy1) in {
            "Leading":   (100, x1, 100, y1),
            "Weakening": (100, x1, y0, 100),
            "Lagging":   (x0, 100, y0, 100),
            "Improving": (x0, 100, 100, y1),
        }.items():
            fig.add_shape(type="rect", x0=qx0, x1=qx1, y0=qy0, y1=qy1,
                          fillcolor=_QUAD_COLOR[quad], line_width=0, layer="below")
            fig.add_annotation(x=(qx0 + qx1) / 2, y=(qy0 + qy1) / 2,
                               text=t(_QUAD_LABEL[quad]), showarrow=False,
                               font=dict(size=12, color="rgba(120,120,120,0.8)"))
        fig.add_hline(y=100, line_color="rgba(150,150,150,0.5)", line_width=1)
        fig.add_vline(x=100, line_color="rgba(150,150,150,0.5)", line_width=1)
        # je Markt: Schweif + aktueller Punkt + Label
        for m in markets:
            fig.add_trace(go.Scatter(
                x=m["tail_ratio"], y=m["tail_momentum"], mode="lines",
                line=dict(color="rgba(120,120,120,0.5)", width=1.5),
                showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(
                x=[m["rs_ratio"]], y=[m["rs_momentum"]], mode="markers+text",
                marker=dict(size=13, color="#37474F"),
                text=[m["code"]], textposition="top center",
                textfont=dict(size=12, color="#37474F"),
                name=m["code"],
                hovertemplate=(f"<b>{m['code']} · {m.get('name', m['index'])}</b><br>"
                               f"Quadrant: {t(_QUAD_LABEL[m['quadrant']])}<br>"
                               f"RS-Ratio %{{x:.1f}}<br>RS-Momentum %{{y:.1f}}<extra></extra>"),
                showlegend=False))
        fig.update_xaxes(title_text="RS-Ratio →", range=[x0, x1])
        fig.update_yaxes(title_text="RS-Momentum →", range=[y0, y1])
        fig.update_layout(height=520, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ── Zu-/Abfluss (Mansfield RSC), cross-asset, nach Klasse gefärbt ─────────
    def _flows(self, markets: list):
        rows = [m for m in markets if m.get("rsc") is not None]
        rows.sort(key=lambda m: m["rsc"])
        labels = [m["code"] for m in rows]
        vals = [m["rsc"] for m in rows]
        colors = [_CLASS_COLOR.get(m.get("class"), "#455A64") for m in rows]
        customdata = [[m.get("class", ""), m.get("name", m.get("index", ""))] for m in rows]
        fig = go.Figure(go.Bar(
            x=vals, y=labels, orientation="h", marker_color=colors,
            customdata=customdata,
            hovertemplate=("%{y} — %{customdata[1]} (%{customdata[0]}): "
                           "RSC %{x:+.2f}%<extra></extra>")))
        fig.add_vline(x=0, line_color="rgba(150,150,150,0.6)", line_width=1)
        fig.update_layout(height=40 + 22 * len(rows), margin=dict(t=10, b=10, l=10, r=10),
                          xaxis_title=t("gr.rsc_axis"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.caption(t("gr.class_legend"))

    # ── Paarweise relative Stärke ─────────────────────────────────────────────
    def _pairs(self, mat, pair_weeks: int, names: dict | None = None):
        codes = list(mat.columns)
        z = mat.values.astype(float)
        fig = go.Figure(go.Heatmap(
            z=z, x=codes, y=codes,
            colorscale=[[0, "#C62828"], [0.5, "#FFFFFF"], [1, "#2E7D32"]],
            zmid=0, colorbar=dict(title="%"),
            hovertemplate="%{y} vs %{x}: %{z:+.1f}%<extra></extra>"))
        fig.update_yaxes(autorange="reversed", title_text=t("gr.pairs_row"))
        fig.update_xaxes(title_text=t("gr.pairs_col"), side="top")
        fig.update_layout(height=460, margin=dict(t=30, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.caption(t("gr.pairs_note", weeks=pair_weeks))
        self._pairs_prose(mat, pair_weeks, names)

    # ── Prosa: von wo nach wo fließt Kapital (Reihe ← Spalte) ─────────────────
    def _pairs_prose(self, mat, pair_weeks: int, names: dict | None = None):
        names = names or {}
        def _lbl(code):
            nm = names.get(code)
            return f"{code} ({nm})" if nm and nm != code else str(code)
        try:
            row_max = mat.max(axis=1).dropna()        # je Zeile stärkster Vorsprung
            net = mat.mean(axis=1, skipna=True).dropna()
        except Exception:
            return
        if row_max.empty or net.empty:
            return
        top_row = row_max.idxmax()                    # Zeile mit dem größten Vorsprung
        top_col = mat.loc[top_row].idxmax()           # zugehörige Spalte
        top_val = float(mat.loc[top_row, top_col])
        inflow, outflow = net.idxmax(), net.idxmin()  # Netto-Anzieher / -Abgeber

        with st.expander(t("gr.pairs_prose_header"), expanded=True):
            st.markdown(t("gr.pairs_read", weeks=pair_weeks))
            if top_val > 0:
                st.markdown(t("gr.pairs_top", frm=_lbl(top_col), to=_lbl(top_row),
                              val=f"{top_val:+.1f}"))
            if inflow != outflow:
                st.markdown(t("gr.pairs_net", inflow=_lbl(inflow),
                              in_val=f"{net[inflow]:+.1f}", outflow=_lbl(outflow),
                              out_val=f"{net[outflow]:+.1f}"))

    # ── Einstieg ──────────────────────────────────────────────────────────────
    def render(self):
        st.markdown(f"## {t('gr.title')}")
        st.caption(t("gr.subtitle"))

        with st.spinner(t("gr.computing")):
            result = _compute_cached(dt.date.today().isoformat())

        markets = result.get("markets", [])
        if not markets:
            st.warning(t("gr.no_data"))
            return

        st.caption(t("gr.as_of", date=result.get("as_of", "–"), n=result.get("n", 0)))

        tab_rrg, tab_flows, tab_pairs = st.tabs(
            [t("gr.tab_rrg"), t("gr.tab_flows"), t("gr.tab_pairs")])
        with tab_rrg:
            uni = st.radio(
                t("gr.rrg_universe"),
                options=["equity", "bond", "metal", "energy", "agri", "crypto"],
                format_func=lambda k: t(f"gr.uni_{k}"), horizontal=True,
                key="_gr_rrg_uni")
            ures = _compute_universe_cached(dt.date.today().isoformat(), uni)
            umarkets = ures.get("markets", [])
            if len(umarkets) >= 2:
                self._rrg(umarkets)
            else:
                st.info(t("gr.rrg_empty"))
        with tab_flows:
            # Cross-Asset: Aktien + Metalle + Rohstoffe + Krypto, nach Klasse gefärbt.
            st.caption(t("gr.flows_note"))
            flows = _compute_flows_cached(dt.date.today().isoformat())
            self._flows(flows.get("markets", []))
        with tab_pairs:
            if result.get("pair_matrix") is not None:
                _names = {m["code"]: m.get("name", m["code"]) for m in markets}
                self._pairs(result["pair_matrix"], result.get("pair_weeks", 13), _names)

        if result.get("skipped"):
            st.caption(t("gr.skipped", items=", ".join(
                f"{c}/{i}" for c, i, _ in result["skipped"])))

        with st.expander(t("gr.methodology_header"), expanded=False):
            st.markdown(t("gr.methodology_md"))

        st.info(t("gr.proxy_note"))
