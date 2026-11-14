"""Asset Viewer, Tab "Scorecard": Edge-Score-Sicht fuer den gewaehlten Ticker.

Ist der Ticker selbst ein Edge-Markt, erscheint die Asset-Scorecard der Edge Scores.
Fuer Einzeltitel rechnet :mod:`tradinglib.ticker_scorecard` Trend, relative Staerke
zum Index und das Marktumfeld (Gesamt-Score des Index); Risiko, 4PS-Phase, Markov
und Minervini-RS stehen nur als Kontext da.
"""
from __future__ import annotations

import datetime as dt
import html
import logging

import pandas as pd
import streamlit as st

from tradinglib import macro_data as md
from tradinglib import ticker_scorecard as tsc
from tradinglib import total_data as tot
from tradinglib import trend_data as td
from tradinglib.asset_card_page import (_BIAS_COLORS, _CSS, AssetCardPage, _f, _gauge, _history_chart, _panel,
                                        _score_cell, _state)
from tradinglib.i18n import t

logger = logging.getLogger(__name__)


def _rs_label(v: float) -> str:
    return "strong_bull" if v >= 2 else "bull" if v >= 1 else "strong_bear" if v <= -2 else "bear" if v <= -1 else "neutral"


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _data(ticker: str, index_name: str, day: str, weeks: int):
    today = dt.date.fromisoformat(day)
    s = td._local_close(ticker)
    idx, idx_key = tsc.index_series(index_name, today)
    hist = tsc.history(s, idx, weeks=weeks, today=today) if len(s) >= 200 else pd.DataFrame()
    return s, idx, idx_key, hist, tsc.sim_context(ticker), tsc.listing_currency(ticker)


def _macro_score(ccy: str | None) -> float | None:
    if ccy not in md.CURRENCIES:
        return None
    try:
        stamp = md.get_meta("last_success") or ""
        if not stamp:
            return None
        from tradinglib.macro_page import _scorecard_cached
        tab, _lat = _scorecard_cached(stamp)
        return float(tab.at[ccy, "score"]) if ccy in tab.index else None
    except Exception:
        logger.exception("ticker scorecard: macro score failed")
        return None


def render(ticker: str, index_name: str | None, region=None) -> None:
    """Inhalt des Tabs. ``region`` = Tab-Container (Default: aktueller Kontext)."""
    region = region or st.container()
    with region:
        market = tsc.edge_market_for(ticker)
        if market:
            st.caption(t("tsc.is_market", market=t(f"cot.mkt_{market}")))
            AssetCardPage().render_market(market, 52)
            return
        _render_stock(ticker, index_name or "")


def _render_stock(ticker: str, index_name: str) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    with st.spinner(t("asset.loading")):
        s, idx, idx_key, hist, ctx, ccy = _data(ticker, index_name, dt.date.today().isoformat(), 52)
    if len(s) < 200:
        st.info(t("tsc.too_short"))
        return
    c = tsc.card_at(s, idx, dt.date.today())
    idx_label = t(f"cot.mkt_{idx_key}") if idx_key else (index_name or "–")
    st.caption(t("tsc.caption", index=idx_label) if index_name else t("tsc.caption_no_index"))

    left, right = st.columns([2, 3])
    with left:
        bias = c["bias"]
        notes = ""
        if c["n"] == 1:
            notes += f"<div style='font-size:.8rem;font-weight:500'>{html.escape(t('tsc.only_one'))}</div>"
        if c["unconfirmed"]:
            notes += f"<div style='font-size:.8rem;font-weight:500'>{html.escape(t('asset.unconfirmed'))}</div>"
        st.markdown(f"<div class='ac-banner' style='background:{_BIAS_COLORS[bias]}'>"
                    f"{html.escape(t('cot.bias_' + bias))}{notes}</div>", unsafe_allow_html=True)
        st.plotly_chart(_gauge(c["total"]), width="stretch", config={"displayModeBar": False})
        comp = [
            [t("asset.total"), (_f(c["total"], "{:+.1f}"), _BIAS_COLORS[bias]), "−10 … +10"],
            [t("asset.comp_trend"), _score_cell(c["trend"], td.bias_label), "−4 … +4"],
            [t("tsc.comp_rs"), _score_cell(c["rs"], _rs_label), "−2 … +2"],
            [t("tsc.comp_market", index=idx_label), _score_cell(c["market"], tot.bias_label), "−10 … +10"],
        ]
        if not hist.empty:
            for w in (1, 4):
                comp.append([t(f"tsc.chg_{w}w"), _f(tsc.change(hist, w), "{:+.1f}"), ""])
        st.markdown(_panel(t("asset.scores"), None, [], comp), unsafe_allow_html=True)
        if not hist.empty:
            st.markdown(f"**{t('asset.history_title')}**")
            st.plotly_chart(_history_chart(hist), width="stretch", config={"displayModeBar": False})
            st.caption(t("tsc.history_hint"))
    with right:
        page = AssetCardPage()
        st.markdown(page._trend_panel(c["trend_detail"], s, 1), unsafe_allow_html=True)
        rs_rows = [
            [t("tsc.ret_13w"), _f(c["ret"], "{:+.1f} %"), ""],
            [t("tsc.idx_ret_13w", index=idx_label), _f(c["idx_ret"], "{:+.1f} %"), ""],
            [t("tsc.rs_diff"), _f(c["rs_diff"], "{:+.1f} Pp"), _state(c["rs"])],
            [t("tsc.market_trend", index=idx_label), _f(c["market_trend"], "{:+.0f}"), _state(c["market"])],
        ]
        st.markdown(_panel(t("tsc.panel_rs"), None,
                           [t("asset.col_component"), t("asset.col_value"), t("asset.col_signal")], rs_rows),
                    unsafe_allow_html=True)
        st.markdown(_context_panel(ctx, ccy), unsafe_allow_html=True)
        st.markdown(page._tech_panel(s, 1), unsafe_allow_html=True)
    with st.expander(t("asset.method_title")):
        st.markdown(t("tsc.method_md"))


def _context_panel(ctx: dict, ccy: str | None) -> str:
    def num(k, fmt):
        v = ctx.get(k)
        return "–" if v is None or pd.isna(v) or (v == 0 and k in ("riskBucket", "trendScore", "mtt_rs")) else fmt.format(v)

    rows = [
        [t("tsc.ctx_risk"), num("riskBucket", "{:.0f} / 4") + " · " + num("riskScore", "{:.0f}")],
        [t("tsc.ctx_trendscore"), num("trendScore", "{:.0f}")],
        [t("tsc.ctx_fps"), num("fps_phase", "{:.0f}")],
        [t("tsc.ctx_markov"), {1: t("cot.bias_bull"), 0: t("cot.bias_neutral"), -1: t("cot.bias_bear")}.get(
            ctx.get("markov_regime"), "–")],
        [t("tsc.ctx_mtt"), num("mtt_rs", "{:.0f}") + (" · ✓" if ctx.get("mtt_pass") == 1 else "")],
    ]
    macro = _macro_score(ccy)
    rows.append([t("tsc.ctx_macro", ccy=ccy or "–"),
                 _score_cell(macro, md.bias_label) if macro is not None else "–"])
    title = t("tsc.panel_ctx", d=str(ctx.get("date", "–"))[:10])
    return _panel(title, None, [t("asset.col_component"), t("asset.col_value")], rows)
