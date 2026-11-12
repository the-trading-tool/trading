"""Makro-Scorecard — Streamlit-Karte der Edge Scores.

Tabs: Scorecard je Währung (Werte, Veränderung, Punkte), Forex-Paare, Verlauf je
Indikator und Methodik. Daten/Scoring: :mod:`tradinglib.macro_data`; lädt beim
Öffnen automatisch nach (höchstens einmal täglich).
"""
from __future__ import annotations

import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tradinglib import cot_data as cd
from tradinglib import macro_data as md
from tradinglib.cot_page import _BIAS_COLORS, _LONG_COLOR, _SHORT_COLOR, _bias_bg, _signed_color
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

# Indikator → (Punkte-Spalte, Format Wert, Format Veränderung)
_IND = {
    "rate": ("p_rate", "{:.2f} %", "{:+.2f}"),
    "cpi": ("p_cpi", "{:.1f} %", "{:+.2f}"),
    "unemp": ("p_unemp", "{:.1f} %", "{:+.2f}"),
    "gdp": ("p_gdp", "{:+.2f} %", "{:+.2f}"),
    "cli": ("p_cli", "{:.1f}", "{:+.2f}"),
}
_PAIR_STRONG, _PAIR_BIAS = 6, 3     # Paar-Spanne −12 … +12


def _pts_bg(p) -> str:
    if p is None or pd.isna(p) or p == 0:
        return ""
    return f"background-color: {_BIAS_COLORS['bull'] if p > 0 else _BIAS_COLORS['bear']}"


def _total_bg(v) -> str:
    return _bias_bg(md.bias_label(v)) if v is not None and not pd.isna(v) else ""


@st.cache_data(ttl=900, show_spinner=False)
def _scorecard_cached(stamp: str):
    return md.scorecard()


@st.cache_data(ttl=900, show_spinner=False)
def _obs_cached(stamp: str) -> pd.DataFrame:
    return md.load_obs()


class MacroPage:
    def __init__(self, username: str = ""):
        self.username = username

    def _header(self, side=None) -> str:
        from tradinglib.edge_scores_page import title
        title(t("page.macro"))
        c1, c2 = (st.columns([5, 1]) if side is None else (st.container(), side))
        with c2:
            if st.button(t("cot.refresh"), key="_macro_refresh", width="stretch"):
                with st.spinner(t("macro.loading")):
                    status = md.fetch()
                bad = {k: v for k, v in status.items() if not v.startswith("ok")}
                if bad:
                    st.warning(t("macro.fetch_partial", sources=", ".join(t(f"macro.ind_{k}") for k in bad)))
                else:
                    st.toast(t("macro.refresh_done"))
                st.cache_data.clear()
        updated, errors = md.ensure_fresh()
        if updated:
            st.cache_data.clear()
        stamp = md.get_meta("last_success") or ""
        with c1:
            st.caption(t("macro.sources"))
            if stamp:
                st.caption(t("macro.as_of", ts=pd.Timestamp(stamp).strftime("%d.%m.%Y %H:%M")))
            failed = [k for k in md.INDICATORS if md.get_meta(f"err_{k}")]
            if errors or failed:
                names = ", ".join(t(f"macro.ind_{k}") for k in (failed or errors))
                st.warning(t("macro.fetch_partial", sources=names))
        return stamp

    # ── Tabs ──────────────────────────────────────────────────────────────────
    def _scorecard_tab(self, sc: pd.DataFrame, lat: pd.DataFrame) -> None:
        st.caption(t("macro.scorecard_hint"))
        val = lat.pivot(index="ccy", columns="indicator", values="value")
        chg = lat.pivot(index="ccy", columns="indicator", values="change")
        cols, fmt, styles, deltas = {}, {}, [], []
        ccys = list(sc.index)
        cols[t("macro.col_ccy")] = ccys
        cols[t("cot.col_score")] = sc["score"].values
        cols[t("cot.col_bias")] = [t(f"cot.bias_{b}") for b in sc["bias"]]
        for i, (ind, (pcol, vfmt, cfmt)) in enumerate(_IND.items()):
            # Spaltennamen muessen eindeutig sein (Inflation und Arbeitslosigkeit
            # haben beide "Δ 3 M") — unsichtbares Suffix statt laengerer Kopfzeile.
            vname = t(f"macro.ind_{ind}")
            cname = f"Δ {t(f'macro.lag_{ind}')}" + "​" * i
            cols[vname] = [val.at[c, ind] if ind in val and c in val.index else None for c in ccys]
            cols[cname] = [chg.at[c, ind] if ind in chg and c in chg.index else None for c in ccys]
            fmt[vname], fmt[cname] = vfmt, cfmt
            styles.append((vname, list(sc[pcol].values)))
            deltas.append((cname, -1 if ind == "unemp" else 1))   # steigende Arbeitslosigkeit = rot
        view = pd.DataFrame(cols)

        def _row_styles(df: pd.DataFrame) -> pd.DataFrame:
            out = pd.DataFrame("", index=df.index, columns=df.columns)
            for name, pts in styles:
                out[name] = [_pts_bg(p) for p in pts]
            for name, sign in deltas:
                out[name] = [_signed_color(None if pd.isna(v) else sign * v) for v in df[name]]
            out[t("cot.col_score")] = [_total_bg(v) for v in df[t("cot.col_score")]]
            return out

        # Farben aus den Zahlen, Anzeige als fertiger Text: st.dataframe zeigt NaN in
        # gestylten Zahlenspalten sonst als "None" (na_rep wird ignoriert).
        style_frame = _row_styles(view)
        disp = view.copy()
        for name, f in fmt.items():
            disp[name] = [("–" if pd.isna(x) else f.format(x)) for x in view[name]]
        styled = disp.style.apply(lambda _df: style_frame, axis=None)
        st.dataframe(styled, hide_index=True, width="stretch")

        with st.expander(t("macro.points_title")):
            pts = sc[[c for c, *_ in _IND.values()] + ["score"]].copy()
            pts.columns = [t(f"macro.ind_{k}") for k in _IND] + [t("cot.col_score")]
            st.dataframe(pts.style.map(_pts_bg).format("{:+d}"), width="stretch")
        with st.expander(t("macro.periods_title")):
            per = lat.assign(p=lat["period"].dt.strftime("%Y-%m") + " (" + lat["freq"] + ")")
            tbl = per.pivot(index="ccy", columns="indicator", values="p").reindex(ccys)
            src = per.pivot(index="ccy", columns="indicator", values="source").reindex(ccys)
            tbl.columns = [t(f"macro.ind_{k}") for k in tbl.columns]
            src.columns = [t(f"macro.ind_{k}") for k in src.columns]
            st.dataframe(tbl.fillna("–"), width="stretch")
            st.caption(t("macro.sources_title"))
            st.dataframe(src.fillna("–"), width="stretch")

    def _pairs_tab(self, sc: pd.DataFrame) -> None:
        card = pd.DataFrame({"key": sc.index, "score": sc["score"].values})
        pairs = cd.pair_bias(card, strong=_PAIR_STRONG, bias=_PAIR_BIAS)
        st.caption(t("macro.pairs_hint"))
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
            s = sc["score"].sort_values()
            fig = go.Figure(go.Bar(x=s.values, y=s.index, orientation="h", text=s.values,
                                   textposition="outside",
                                   marker_color=[_LONG_COLOR if v > 0 else _SHORT_COLOR if v < 0 else "#9e9e9e"
                                                 for v in s.values]))
            fig.update_layout(title=t("cot.ccy_strength"), height=360,
                              xaxis=dict(range=[-6.5, 6.5], dtick=1),
                              margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    def _history_tab(self, stamp: str) -> None:
        obs = _obs_cached(stamp)
        c1, c2, c3 = st.columns([2, 3, 2])
        ind = c1.selectbox(t("macro.indicator"), md.INDICATORS,
                           format_func=lambda k: t(f"macro.ind_{k}"), key="_macro_ind")
        ccys = c2.multiselect(t("macro.currencies"), md.CURRENCIES, default=md.CURRENCIES,
                              key="_macro_ccys")
        years = c3.select_slider(t("macro.years"), options=[2, 3, 5, 8, 10], value=5, key="_macro_years")
        since = pd.Timestamp.today() - pd.DateOffset(years=years)
        d = obs[(obs["indicator"] == ind) & (obs["ccy"].isin(ccys)) & (obs["period"] >= since)]
        if d.empty:
            st.info(t("cot.no_data"))
            return
        fig = go.Figure()
        for c in [c for c in md.CURRENCIES if c in ccys]:
            s = d[d["ccy"] == c]
            if not s.empty:
                fig.add_scatter(x=s["period"], y=s["value"], name=c, mode="lines",
                                line=dict(shape="hv" if ind == "rate" else "linear"))
        if ind == "cli":
            fig.add_hline(y=100, line_dash="dash", line_color="rgba(200,200,200,0.6)")
        if ind == "gdp":
            fig.add_hline(y=0, line_dash="dash", line_color="rgba(200,200,200,0.6)")
        fig.update_layout(height=460, hovermode="x unified", legend=dict(orientation="h", y=1.08),
                          margin=dict(t=30, b=10, l=10, r=10), yaxis_title=t(f"macro.unit_{ind}"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    def render(self, side=None) -> None:
        stamp = self._header(side)
        sc, lat = _scorecard_cached(stamp)
        if sc.empty:
            st.info(t("cot.no_data"))
            return
        tab_card, tab_pairs, tab_hist, tab_method = st.tabs([
            t("cot.tab_scorecard"), t("cot.tab_pairs"), t("macro.tab_history"), t("cot.tab_method")])
        with tab_card:
            self._scorecard_tab(sc, lat)
        with tab_pairs:
            self._pairs_tab(sc)
        with tab_hist:
            self._history_tab(stamp)
        with tab_method:
            st.markdown(t("macro.method_md"))
