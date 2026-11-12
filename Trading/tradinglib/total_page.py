"""Gesamt-Score — Streamlit-Karte der Edge Scores.

Führt die drei Scorecards zusammen (:mod:`tradinglib.total_data`). Nutzt die
Caches der Einzelkarten, damit nichts doppelt geladen wird.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
import streamlit as st

from tradinglib import cot_data as cd
from tradinglib import macro_data as md
from tradinglib import total_data as tot
from tradinglib import trend_data as td
from tradinglib.cot_page import _BIAS_COLORS, _bias_bg, _signed_color
from tradinglib.i18n import t

logger = logging.getLogger(__name__)


def _comp_bg(v) -> str:
    if v is None or pd.isna(v) or v == 0:
        return ""
    return f"background-color: {_BIAS_COLORS['bull'] if v > 0 else _BIAS_COLORS['bear']}"


def _fmt(v, f="{:+.0f}") -> str:
    return "–" if v is None or pd.isna(v) else f.format(v)


def _load():
    """(Gesamt-Tabelle, Stand-Infos). Laedt faellige Daten der Einzelkarten nach."""
    from tradinglib.cot_page import _scorecard_cached as cot_cached
    from tradinglib.macro_page import _scorecard_cached as macro_cached
    from tradinglib.trend_page import _compute as trend_cached, _markets_sig
    errors = []
    if cd.ensure_fresh()[1]:
        errors.append(t("edge.card_cot"))
    _upd, merr = md.ensure_fresh()
    if merr:
        errors.append(t("edge.card_macro"))
    cot_date = cd.latest_report_date()
    cot = cot_cached(cot_date.isoformat()) if cot_date else pd.DataFrame(columns=["key", "score"])
    macro_stamp = md.get_meta("last_success") or ""
    macro, _lat = macro_cached(macro_stamp) if macro_stamp else (pd.DataFrame(), None)
    trend, _series = trend_cached(dt.date.today().isoformat(), _markets_sig())
    trend = trend[~trend["missing"]] if not trend.empty else trend
    info = {"cot": cot_date, "macro": macro_stamp, "trend": trend["date"].max() if not trend.empty else None}
    return tot.combine(cot, macro, trend), info, errors


class TotalPage:
    def __init__(self, username: str = ""):
        self.username = username

    def _header(self, side=None):
        from tradinglib.edge_scores_page import title
        title(t("page.total"))
        c1, c2 = (st.columns([5, 1]) if side is None else (st.container(), side))
        with c2:
            if st.button(t("cot.refresh"), key="_total_refresh", width="stretch"):
                st.cache_data.clear()
        with st.spinner(t("total.loading")):
            total, info, errors = _load()
        with c1:
            parts = []
            if info["cot"]:
                parts.append(t("total.asof_cot", d=info["cot"].strftime("%d.%m.%Y")))
            if info["macro"]:
                parts.append(t("total.asof_macro", d=pd.Timestamp(info["macro"]).strftime("%d.%m.%Y")))
            if info["trend"] is not None and not pd.isna(info["trend"]):
                parts.append(t("total.asof_trend", d=pd.Timestamp(info["trend"]).strftime("%d.%m.%Y")))
            st.caption(t("total.sources") + " · " + " · ".join(parts))
            if errors:
                st.warning(t("macro.fetch_partial", sources=", ".join(errors)))
        return total

    def _overview_tab(self, total: pd.DataFrame) -> None:
        st.caption(t("total.overview_hint"))
        only = st.checkbox(t("total.only_agree"), key="_total_only_agree")
        for group in dict.fromkeys(total["group"]):
            part = total[(total["group"] == group) & (total["agree"] if only else True)]
            if part.empty:
                continue
            part = part.sort_values("total", ascending=False).reset_index(drop=True)
            st.markdown(f"**{t('cot.group_' + group)}**")
            view = pd.DataFrame({
                t("cot.col_market"): [t(f"cot.mkt_{k}") for k in part["key"]],
                t("total.col_total"): [_fmt(v, "{:+.1f}") for v in part["total"]],
                t("cot.col_bias"): [t(f"cot.bias_{b}") for b in part["bias"]],
                t("total.col_agree"): ["✓" if a else "" for a in part["agree"]],
                t("total.col_cot"): [_fmt(v) for v in part["cot"]],
                t("total.col_macro"): [_fmt(v) for v in part["macro"]],
                t("total.col_trend"): [_fmt(v) for v in part["trend"]],
            })

            def _styles(df, part=part):
                out = pd.DataFrame("", index=df.index, columns=df.columns)
                out[t("total.col_total")] = [_bias_bg(b) for b in part["bias"]]
                for col, comp in (("total.col_cot", "cot"), ("total.col_macro", "macro"), ("total.col_trend", "trend")):
                    out[t(col)] = [_comp_bg(v) for v in part[comp]]
                return out

            st.dataframe(view.style.apply(_styles, axis=None), hide_index=True, width="stretch")

    def _setups_tab(self, total: pd.DataFrame) -> None:
        pairs = tot.pair_setups(total, td.FX_CCYS)
        if pairs.empty:
            st.info(t("cot.no_data"))
            return
        st.caption(t("total.setups_hint"))
        only = st.checkbox(t("total.only_agree"), value=True, key="_total_setups_agree")
        if only:
            pairs = pairs[pairs["agree"]]
        view = pd.DataFrame({
            t("cot.col_pair"): pairs["pair"].values,
            t("total.col_total"): [_fmt(v, "{:+.1f}") for v in pairs["score"]],
            t("cot.col_bias"): [t(f"cot.bias_{b}") for b in pairs["bias"]],
            t("total.col_direction"): [t("total.dir_long") if v > 0 else t("total.dir_short") if v < 0 else "–"
                                      for v in pairs["score"]],
            t("total.col_agree"): ["✓" if a else "" for a in pairs["agree"]],
            t("total.col_d_cot"): [_fmt(v) for v in pairs["d_cot"]],
            t("total.col_d_macro"): [_fmt(v) for v in pairs["d_macro"]],
            t("total.col_d_trend"): [_fmt(v) for v in pairs["d_trend"]],
        })
        num = pairs.reset_index(drop=True)

        def _styles(df):
            out = pd.DataFrame("", index=df.index, columns=df.columns)
            out[t("cot.col_bias")] = [_bias_bg(b) for b in num["bias"]]
            out[t("total.col_total")] = [_signed_color(v) for v in num["score"]]
            # gruen = stuetzt die Richtung des Paars (bei Short also ein negatives Δ)
            for col, comp in (("total.col_d_cot", "d_cot"), ("total.col_d_macro", "d_macro"),
                              ("total.col_d_trend", "d_trend")):
                out[t(col)] = [_comp_bg(None if pd.isna(v) else v * (1 if s >= 0 else -1))
                               for v, s in zip(num[comp], num["score"])]
            return out

        st.dataframe(view.style.apply(_styles, axis=None), hide_index=True, width="stretch",
                     height=min(36 * (len(view) + 1) + 4, 36 * 29))

    def render(self, side=None) -> None:
        total = self._header(side)
        if total.empty or total["total"].isna().all():
            st.info(t("cot.no_data"))
            return
        tab_setups, tab_overview, tab_method = st.tabs([
            t("total.tab_setups"), t("total.tab_overview"), t("cot.tab_method")])
        with tab_setups:
            self._setups_tab(total)
        with tab_overview:
            self._overview_tab(total)
        with tab_method:
            st.markdown(t("total.method_md"))
