"""COT Edge Scorecard — Streamlit-Seite (Markt-Bereich).

Tabs: Scorecard je Markt, Forex-Paar-Bias, COT-Historie (Chart + Tabelle wie in
gängigen COT-Tools) und Methodik. Daten und Scoring liegen in
:mod:`tradinglib.cot_data`; die Seite lädt beim Öffnen automatisch nach, sobald
ein neuer CFTC-Report fällig ist.
"""
from __future__ import annotations

import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from tradinglib import cot_data as cd
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

_LONG_COLOR = "#3f51d6"
_SHORT_COLOR = "#e05a5a"
_PCT_COLOR = "#f2d600"

_BIAS_COLORS = {
    "strong_bull": "rgba(46,125,50,0.55)",
    "bull": "rgba(124,179,66,0.35)",
    "neutral": "rgba(158,158,158,0.15)",
    "bear": "rgba(239,108,0,0.35)",
    "strong_bear": "rgba(198,40,40,0.55)",
}


def _score_bg(v) -> str:
    if v is None or pd.isna(v):
        return ""
    if v >= 3:
        c = _BIAS_COLORS["strong_bull"]
    elif v >= 1:
        c = _BIAS_COLORS["bull"]
    elif v <= -3:
        c = _BIAS_COLORS["strong_bear"]
    elif v <= -1:
        c = _BIAS_COLORS["bear"]
    else:
        c = _BIAS_COLORS["neutral"]
    return f"background-color: {c}"


def _rank_bg(r) -> str:
    """Farbe des Perzentil-Rangs nach den Punkte-Schwellen der Positionierung."""
    if r is None or pd.isna(r):
        return ""
    if r >= 80:
        return f"background-color: {_BIAS_COLORS['strong_bull']}"
    if r >= 60:
        return f"background-color: {_BIAS_COLORS['bull']}"
    if r <= 20:
        return f"background-color: {_BIAS_COLORS['strong_bear']}"
    if r <= 40:
        return f"background-color: {_BIAS_COLORS['bear']}"
    return ""


def _bias_bg(label: str) -> str:
    return f"background-color: {_BIAS_COLORS.get(label, '')}"


def _signed_color(v) -> str:
    if v is None or pd.isna(v) or v == 0:
        return ""
    return "color: #43a047" if v > 0 else "color: #e53935"


@st.cache_data(ttl=900, show_spinner=False)
def _scorecard_cached(stamp: str) -> pd.DataFrame:
    # `stamp` = letzter Stichtag → neuer Report invalidiert den Cache
    return cd.scorecard()


@st.cache_data(ttl=900, show_spinner=False)
def _history_cached(key: str, stamp: str) -> pd.DataFrame:
    return cd.load_history(key)


class CotPage:
    def __init__(self, username: str = ""):
        self.username = username

    # ── Kopf ──────────────────────────────────────────────────────────────────
    def _header(self, side=None) -> str:
        """Titel, Stand und Aktualisieren-Knopf. Mit ``side`` (Spalte des
        Edge-Scores-Untermenüs) wandert der Knopf dorthin, sonst rechts neben
        den Titel."""
        from tradinglib.edge_scores_page import title
        title(t("page.cot"))
        if side is None:
            c1, c2 = st.columns([5, 1])
        else:
            c1, c2 = st.container(), side
        with c2:
            if st.button(t("cot.refresh"), key="_cot_refresh", width="stretch"):
                with st.spinner(t("cot.loading")):
                    try:
                        n = cd.fetch()
                        st.toast(t("cot.refresh_done", rows=n))
                    except Exception as e:
                        logger.exception("COT refresh failed")
                        st.error(t("cot.fetch_failed", error=e))
                st.cache_data.clear()
        updated, err = cd.ensure_fresh()
        if updated:
            st.cache_data.clear()
        latest = cd.latest_report_date()
        with c1:
            if latest:
                release = latest + pd.Timedelta(days=cd.RELEASE_LAG_DAYS)
                st.caption(t("cot.as_of", report=latest.strftime("%d.%m.%Y"),
                             release=release.strftime("%d.%m.%Y")))
            if err:
                st.warning(t("cot.fetch_failed", error=err))
        return latest.isoformat() if latest else ""

    # ── Tabs ──────────────────────────────────────────────────────────────────
    def _scorecard_tab(self, card: pd.DataFrame) -> None:
        st.caption(t("cot.scorecard_hint"))
        for group in dict.fromkeys(card["group"]):
            part = card[card["group"] == group]
            st.markdown(f"**{t('cot.group_' + group)}**")
            view = pd.DataFrame({
                t("cot.col_market"): part["key"].map(lambda k: t(f"cot.mkt_{k}")),
                t("cot.col_score"): part["score"],
                t("cot.col_bias"): part["bias"].map(lambda b: t(f"cot.bias_{b}")),
                t("cot.col_long_pct"): part["long_pct"],
                t("cot.col_long_rank"): part["long_rank"],
                t("cot.col_d_long_pct"): part["d_long_pct"],
                t("cot.col_net"): part["net"],
                t("cot.col_d_net"): part["d_net"],
                t("cot.col_cot_index"): part["cot_index"],
                t("cot.col_note"): part["cot_index"].map(self._crowding_note),
            })
            styled = (view.style
                      .map(_score_bg, subset=[t("cot.col_score")])
                      .map(_rank_bg, subset=[t("cot.col_long_rank")])
                      .map(_signed_color, subset=[t("cot.col_d_long_pct"), t("cot.col_d_net")])
                      .format({t("cot.col_long_pct"): "{:.1f} %",
                               t("cot.col_long_rank"): "{:.0f}",
                               t("cot.col_d_long_pct"): "{:+.2f}",
                               t("cot.col_net"): "{:,.0f}",
                               t("cot.col_d_net"): "{:+,.0f}",
                               t("cot.col_cot_index"): "{:.0f}"}, na_rep="–"))
            st.dataframe(styled, hide_index=True, width="stretch")

    @staticmethod
    def _crowding_note(idx) -> str:
        if idx is None or pd.isna(idx):
            return ""
        if idx >= 90:
            return t("cot.crowded_long")
        if idx <= 10:
            return t("cot.crowded_short")
        return ""

    def _pairs_tab(self, card: pd.DataFrame) -> None:
        pairs = cd.pair_bias(card)
        if pairs.empty:
            st.info(t("cot.no_data"))
            return
        st.caption(t("cot.pairs_hint"))
        left, right = st.columns([3, 2])
        with left:
            view = pd.DataFrame({
                t("cot.col_pair"): pairs["pair"],
                t("cot.col_score"): pairs["score"],
                t("cot.col_bias"): pairs["bias"].map(lambda b: t(f"cot.bias_{b}")),
                t("cot.col_base"): pairs["base_score"],
                t("cot.col_quote"): pairs["quote_score"],
            })
            styled = view.style.map(_bias_bg, subset=[t("cot.col_bias")]).map(
                _signed_color, subset=[t("cot.col_score")])
            st.dataframe(styled, hide_index=True, width="stretch", height=36 * 29)
        with right:
            fx = card[card["key"].isin(cd.FX_CCYS)].sort_values("score")
            fig = go.Figure(go.Bar(
                x=fx["score"], y=fx["key"], orientation="h",
                marker_color=[_LONG_COLOR if s > 0 else _SHORT_COLOR if s < 0 else "#9e9e9e"
                              for s in fx["score"]],
                text=fx["score"], textposition="outside"))
            fig.update_layout(title=t("cot.ccy_strength"), height=360,
                              xaxis=dict(range=[-3.5, 3.5], dtick=1),
                              margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    def _history_tab(self, stamp: str) -> None:
        labels = {m[0]: f"{t('cot.mkt_' + m[0])} ({m[0]})" for m in cd.MARKETS}
        c1, c2 = st.columns([2, 3])
        key = c1.selectbox(t("cot.market"), list(labels), format_func=labels.get, key="_cot_market")
        weeks = c2.select_slider(t("cot.weeks"), options=[26, 52, 78, 104, 156, 260],
                                 value=78, key="_cot_weeks")
        hist = _history_cached(key, stamp)
        if hist.empty:
            st.info(t("cot.no_data"))
            return
        h = hist.tail(weeks)
        x = pd.to_datetime(h["report_date"])

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_bar(x=x, y=h["nc_long"], name=t("cot.long_positions"), marker_color=_LONG_COLOR)
        fig.add_bar(x=x, y=h["nc_short"], name=t("cot.short_positions"), marker_color=_SHORT_COLOR)
        fig.add_scatter(x=x, y=h["long_pct"], name=t("cot.long_pct"), mode="lines",
                        line=dict(color=_PCT_COLOR, width=2, shape="hv"), secondary_y=True)
        fig.add_hline(y=50, line_dash="dash", line_color="rgba(200,200,200,0.6)", secondary_y=True)
        fig.update_layout(barmode="stack", height=430, hovermode="x unified",
                          legend=dict(orientation="h", y=1.08),
                          margin=dict(t=30, b=10, l=10, r=10))
        fig.update_yaxes(title_text=t("cot.contracts"), secondary_y=False)
        fig.update_yaxes(title_text="Long %", range=[0, 100], ticksuffix=" %", secondary_y=True)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        fig2.add_scatter(x=x, y=h["net"], name=t("cot.col_net"), mode="lines",
                         line=dict(color=_LONG_COLOR, width=2), fill="tozeroy")
        fig2.add_scatter(x=x, y=h["cot_index"], name=t("cot.col_cot_index"), mode="lines",
                         line=dict(color=_PCT_COLOR, width=1.5, dash="dot"), secondary_y=True)
        fig2.update_layout(height=230, hovermode="x unified", legend=dict(orientation="h", y=1.15),
                           margin=dict(t=30, b=10, l=10, r=10))
        fig2.update_yaxes(range=[0, 100], secondary_y=True)
        st.plotly_chart(fig2, width="stretch", config={"displayModeBar": False})

        tbl = h.iloc[::-1]
        view = pd.DataFrame({
            t("cot.col_report_date"): pd.to_datetime(tbl["report_date"]).dt.strftime("%d.%m.%Y"),
            t("cot.col_release_date"): tbl["release_date"].dt.strftime("%d.%m.%Y"),
            t("cot.col_d_long_pct"): tbl["d_long_pct"],
            t("cot.col_long"): tbl["nc_long"],
            t("cot.col_short"): tbl["nc_short"],
            t("cot.col_d_long"): tbl["d_long"],
            t("cot.col_d_short"): tbl["d_short"],
            t("cot.col_long_pct"): tbl["long_pct"],
            t("cot.col_net"): tbl["net"],
            t("cot.col_cot_index"): tbl["cot_index"],
            t("cot.col_score"): tbl["score"],
        })
        styled = (view.style
                  .map(_signed_color, subset=[t("cot.col_d_long_pct"), t("cot.col_d_long"),
                                              t("cot.col_d_short")])
                  .map(_score_bg, subset=[t("cot.col_score")])
                  .format({t("cot.col_d_long_pct"): "{:+.2f}",
                           t("cot.col_long"): "{:,.0f}", t("cot.col_short"): "{:,.0f}",
                           t("cot.col_d_long"): "{:+,.0f}", t("cot.col_d_short"): "{:+,.0f}",
                           t("cot.col_long_pct"): "{:.2f} %", t("cot.col_net"): "{:,.0f}",
                           t("cot.col_cot_index"): "{:.0f}"}, na_rep="–"))
        st.dataframe(styled, hide_index=True, width="stretch")

    # ── Seite ─────────────────────────────────────────────────────────────────
    def render(self, side=None) -> None:
        stamp = self._header(side)
        if not stamp:
            st.info(t("cot.no_data"))
            return
        card = _scorecard_cached(stamp)
        tab_card, tab_pairs, tab_hist, tab_method = st.tabs([
            t("cot.tab_scorecard"), t("cot.tab_pairs"), t("cot.tab_history"), t("cot.tab_method")])
        with tab_card:
            self._scorecard_tab(card)
        with tab_pairs:
            self._pairs_tab(card)
        with tab_hist:
            self._history_tab(stamp)
        with tab_method:
            st.markdown(t("cot.method_md"))
