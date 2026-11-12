"""Trend- & Saisonalitäts-Scorecard — Streamlit-Karte der Edge Scores.

Tabs: Scorecard je Markt (gruppiert wie COT), Forex-Paare, Chart (Kurs mit
SMA50/200 und Ø-Saisonverlauf) und Methodik. Daten/Scoring: :mod:`tradinglib.trend_data`.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tradinglib import cot_data as cd
from tradinglib import trend_data as td
from tradinglib.cot_page import _BIAS_COLORS, _LONG_COLOR, _PCT_COLOR, _SHORT_COLOR, _bias_bg, _signed_color
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

_PAIR_STRONG, _PAIR_BIAS = 5, 3     # Paar-Spanne −8 … +8
_PTS = ["p_sma200", "p_cross", "p_mom", "p_season"]


def _fmt_price(v) -> str:
    if v is None or pd.isna(v):
        return "–"
    a = abs(v)
    return f"{v:,.0f}" if a >= 1000 else f"{v:,.2f}" if a >= 10 else f"{v:.4f}" if a >= 0.1 else f"{v:.5f}"


def _fmt(v, f: str) -> str:
    return "–" if v is None or pd.isna(v) else f.format(v)


def _pts_bg(p) -> str:
    if p is None or pd.isna(p) or p == 0:
        return ""
    return f"background-color: {_BIAS_COLORS['bull'] if p > 0 else _BIAS_COLORS['bear']}"


def _markets_sig() -> str:
    """Marktliste + Quellen als Cache-Schluessel: st.cache_data sieht nur den Code von
    _compute, nicht den von trend_data — ohne diesen Schluessel lieferte der Cache nach
    dem Hinzufuegen neuer Maerkte bis zum naechsten Tag die alte Liste."""
    return "|".join(f"{k}:{v}" for k, v in sorted(td.SOURCES.items()))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _compute(day: str, markets_sig: str = ""):
    # `day` haelt den Cache tagesaktuell; Kurse kommen taeglich aus den Scheduler-Laeufen
    return td.scorecard(dt.date.fromisoformat(day))


class TrendPage:
    def __init__(self, username: str = ""):
        self.username = username

    def _header(self, side=None):
        from tradinglib.edge_scores_page import title
        title(t("page.trend"))
        c1, c2 = (st.columns([5, 1]) if side is None else (st.container(), side))
        with c2:
            if st.button(t("cot.refresh"), key="_trend_refresh", width="stretch"):
                _compute.clear()
        with st.spinner(t("trend.loading")):
            card, series = _compute(dt.date.today().isoformat(), _markets_sig())
        with c1:
            st.caption(t("trend.sources"))
            missing = card.loc[card["missing"], "key"].tolist() if not card.empty else []
            if missing:
                st.warning(t("trend.missing", markets=", ".join(t(f"cot.mkt_{k}") for k in missing)))
        return card[~card["missing"]].reset_index(drop=True) if not card.empty else card, series

    def _scorecard_tab(self, card: pd.DataFrame) -> None:
        st.caption(t("trend.scorecard_hint"))
        order = ["fx", "commodities", "indices", "indices_world", "rates", "crypto"]
        groups = sorted(dict.fromkeys(card["group"]), key=lambda g: order.index(g) if g in order else 99)
        for group in groups:
            part = card[card["group"] == group]
            st.markdown(f"**{t('cot.group_' + group)}**")
            name = [t(f"cot.mkt_{k}") + (" ⇅" if inv else "") for k, inv in zip(part["key"], part["inverted"])]
            view = pd.DataFrame({
                t("cot.col_market"): name,
                t("cot.col_score"): part["score"].values,
                t("cot.col_bias"): [t(f"cot.bias_{b}") for b in part["bias"]],
                t("trend.col_last"): [_fmt_price(v) for v in part["last"]],
                t("trend.col_dist200"): [_fmt(v, "{:+.1f} %") for v in part["dist200"]],
                t("trend.col_r63"): [_fmt(v, "{:+.1f} %") for v in part["r63"]],
                t("trend.col_sea_mean"): [_fmt(v, "{:+.2f} %") for v in part["sea_mean"]],
                t("trend.col_sea_hit"): [_fmt(v, "{:.0f} %") + f" ({n})" for v, n in zip(part["sea_hit"], part["sea_n"])],
                t("trend.col_points"): [f"{a:+d} {b:+d} {c:+d} {d:+d}" for a, b, c, d in part[_PTS].values],
            })
            num = part.reset_index(drop=True)

            def _styles(df, num=num):
                out = pd.DataFrame("", index=df.index, columns=df.columns)
                out[t("cot.col_score")] = [_bias_bg(td.bias_label(v)) for v in num["score"]]
                out[t("trend.col_dist200")] = [_pts_bg(v) for v in num["p_sma200"]]
                out[t("trend.col_r63")] = [_pts_bg(v) for v in num["p_mom"]]
                out[t("trend.col_sea_hit")] = [_pts_bg(v) for v in num["p_season"]]
                return out

            st.dataframe(view.style.apply(_styles, axis=None), hide_index=True, width="stretch")
        st.caption(t("trend.inverted_note"))

    def _pairs_tab(self, card: pd.DataFrame) -> None:
        fx = card[card["key"].isin(td.FX_CCYS)]
        pairs = cd.pair_bias(fx[["key", "score"]], strong=_PAIR_STRONG, bias=_PAIR_BIAS)
        if pairs.empty:
            st.info(t("cot.no_data"))
            return
        st.caption(t("trend.pairs_hint"))
        left, right = st.columns([3, 2])
        with left:
            view = pd.DataFrame({
                t("cot.col_pair"): pairs["pair"], t("cot.col_score"): pairs["score"],
                t("cot.col_bias"): pairs["bias"].map(lambda b: t(f"cot.bias_{b}")),
                t("cot.col_base"): pairs["base_score"], t("cot.col_quote"): pairs["quote_score"],
            })
            st.dataframe(view.style.map(_bias_bg, subset=[t("cot.col_bias")])
                         .map(_signed_color, subset=[t("cot.col_score")]),
                         hide_index=True, width="stretch", height=36 * 29)
        with right:
            s = fx.set_index("key")["score"].sort_values()
            fig = go.Figure(go.Bar(x=s.values, y=s.index, orientation="h", text=s.values, textposition="outside",
                                   marker_color=[_LONG_COLOR if v > 0 else _SHORT_COLOR if v < 0 else "#9e9e9e"
                                                 for v in s.values]))
            fig.update_layout(title=t("cot.ccy_strength"), height=360, xaxis=dict(range=[-4.5, 4.5], dtick=1),
                              margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    def _chart_tab(self, card: pd.DataFrame, series: dict) -> None:
        keys = card["key"].tolist()
        c1, c2 = st.columns([2, 3])
        key = c1.selectbox(t("cot.market"), keys, format_func=lambda k: f"{t('cot.mkt_' + k)} ({k})",
                           key="_trend_market")
        years = c2.select_slider(t("macro.years"), options=[1, 2, 3, 5], value=2, key="_trend_years")
        s = series.get(key)
        if s is None or s.empty:
            st.info(t("cot.no_data"))
            return
        row = card.set_index("key").loc[key]
        st.caption(t("trend.source_of", source=row["source"].replace("(local)", f"({t('trend.src_local')})")))
        sma50, sma200 = s.rolling(50).mean(), s.rolling(200).mean()
        since = s.index[-1] - pd.DateOffset(years=years)
        fig = go.Figure()
        fig.add_scatter(x=s[s.index >= since].index, y=s[s.index >= since], name=t("trend.price"),
                        line=dict(color=_LONG_COLOR, width=2))
        fig.add_scatter(x=sma50[sma50.index >= since].index, y=sma50[sma50.index >= since], name="SMA 50",
                        line=dict(color=_PCT_COLOR, width=1.5))
        fig.add_scatter(x=sma200[sma200.index >= since].index, y=sma200[sma200.index >= since], name="SMA 200",
                        line=dict(color=_SHORT_COLOR, width=1.5))
        fig.update_layout(height=400, hovermode="x unified", legend=dict(orientation="h", y=1.08),
                          margin=dict(t=30, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

        today = dt.date.today()
        prof = td.seasonal_profile(s, today)
        if prof.empty:
            return
        base = pd.Timestamp(today.year, 1, 1)
        x = [base + pd.Timedelta(days=int(d) - 1) for d in prof["doy"]]
        fig2 = go.Figure()
        fig2.add_scatter(x=x, y=prof["avg"], name=t("trend.season_avg", n=int(prof["n"].iloc[0])),
                         line=dict(color=_PCT_COLOR, width=2))
        fig2.add_vrect(x0=pd.Timestamp(today), x1=pd.Timestamp(today) + pd.Timedelta(days=td.SEASON_DAYS),
                       fillcolor="rgba(63,81,214,0.15)", line_width=0)
        fig2.add_hline(y=100, line_dash="dash", line_color="rgba(200,200,200,0.6)")
        fig2.update_layout(title=t("trend.season_title"), height=280, showlegend=False,
                           xaxis=dict(tickformat="%b"), margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig2, width="stretch", config={"displayModeBar": False})
        sea = td.seasonality(s, today)
        if sea["n"]:
            st.caption(t("trend.season_years") + " "
                       + " · ".join(f"{y}: {r:+.1f} %" for y, r in zip(sea["years"], sea["rets"])))

    def render(self, side=None) -> None:
        card, series = self._header(side)
        if card.empty:
            st.info(t("cot.no_data"))
            return
        tab_card, tab_pairs, tab_chart, tab_method = st.tabs([
            t("cot.tab_scorecard"), t("cot.tab_pairs"), t("trend.tab_chart"), t("cot.tab_method")])
        with tab_card:
            self._scorecard_tab(card)
        with tab_pairs:
            self._pairs_tab(card)
        with tab_chart:
            self._chart_tab(card, series)
        with tab_method:
            st.markdown(t("trend.method_md"))
