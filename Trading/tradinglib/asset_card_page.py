"""Asset-Scorecard — Streamlit-Karte der Edge Scores.

Ein Markt auf einer Seite: Gesamt-Score mit Tacho, die drei Komponenten mit
ihren Einzelpunkten, technische Zusatzinfos und der Score-Verlauf.
Daten: :mod:`tradinglib.asset_card_data`.
"""
from __future__ import annotations

import datetime as dt
import html
import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tradinglib import asset_card_data as ac
from tradinglib import cot_data as cd
from tradinglib import macro_data as md
from tradinglib import total_data as tot
from tradinglib import trend_data as td
from tradinglib.cot_page import _BIAS_COLORS
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

_STATE_KEY = "_asset_card_key"
DEFAULT_MARKET = "DAX"
_BULL, _BEAR = "#43a047", "#e53935"
_GROUP_ORDER = ["fx", "commodities", "indices", "indices_world", "rates", "crypto"]


# ── Formatierung ──────────────────────────────────────────────────────────────

def _f(v, fmt: str) -> str:
    return "–" if v is None or pd.isna(v) else fmt.format(v)


def _price(v) -> str:
    if v is None or pd.isna(v):
        return "–"
    a = abs(v)
    return f"{v:,.0f}" if a >= 1000 else f"{v:,.2f}" if a >= 10 else f"{v:.4f}" if a >= 0.1 else f"{v:.5f}"


def _state(p) -> tuple[str, str]:
    """(Text, Hintergrund) fuer einen Punktwert/Zustand +/−/0."""
    if p is None or pd.isna(p):
        return "–", ""
    if p > 0:
        return t("cot.bias_bull"), _BIAS_COLORS["bull"]
    if p < 0:
        return t("cot.bias_bear"), _BIAS_COLORS["bear"]
    return t("cot.bias_neutral"), ""


def _panel(title: str, bias: str | None, header: list[str], rows: list[list]) -> str:
    """Kleine Tabelle im Stil der EdgeFinder-Panels. Jede Zelle ist Text oder
    (Text, Hintergrund). ``bias`` (Label) faerbt die Kopfzeile rechts."""
    esc = html.escape
    cell = lambda c, tag="td": (f"<{tag} style='background:{c[1]}'>{esc(str(c[0]))}</{tag}>"
                                if isinstance(c, tuple) else f"<{tag}>{esc(str(c))}</{tag}>")
    head_bias = (f"<span class='ac-bias' style='background:{_BIAS_COLORS.get(bias, '')}'>"
                 f"{esc(t('cot.bias_' + bias))}</span>") if bias else ""
    out = [f"<div class='ac-panel'><div class='ac-head'><span>{esc(title)}</span>{head_bias}</div>",
           "<table class='ac-tab'>"]
    if header:
        out.append("<tr>" + "".join(f"<th>{esc(h)}</th>" for h in header) + "</tr>")
    for r in rows:
        out.append("<tr>" + "".join(cell(c) for c in r) + "</tr>")
    out.append("</table></div>")
    return "".join(out)


_CSS = """<style>
.ac-panel{border:1px solid rgba(128,128,128,.35);border-radius:6px;margin-bottom:.8rem;overflow:hidden}
.ac-head{display:flex;justify-content:space-between;align-items:center;padding:.3rem .6rem;
 background:rgba(128,128,128,.12);font-weight:600}
.ac-bias{padding:.05rem .6rem;border-radius:4px;font-weight:600}
.ac-tab{width:100%;border-collapse:collapse;font-size:.85rem}
.ac-tab th{font-weight:500;opacity:.7;text-align:left;padding:.15rem .6rem}
.ac-tab td{padding:.2rem .6rem;border-top:1px solid rgba(128,128,128,.18)}
.ac-banner{text-align:center;font-size:1.3rem;font-weight:700;padding:.35rem;border-radius:6px}
</style>"""


# ── Daten (gecacht) ───────────────────────────────────────────────────────────

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _asset_data(key: str, day: str, cot_stamp: str, macro_stamp: str, weeks: int, store_stamp: float = 0.0):
    """Kursreihe, COT-Historie, Makrodaten und Verlauf eines Marktes.

    Der Verlauf kommt aus dem gespeicherten Wochenverlauf (``edge_scores.db``), soweit vorhanden;
    nur fehlende Wochen und der heutige Punkt werden live gerechnet. ``store_stamp`` (mtime der DB)
    ist nur Cache-Schluessel."""
    from tradinglib import edge_history as eh
    today = dt.date.fromisoformat(day)
    s, src = td.price_series(key, today)
    cot_hist = cd.load_history(key) if ac.has_cot(key) else pd.DataFrame()
    obs = md.load_obs()
    try:
        stored = eh.load(keys=[key], since=today - dt.timedelta(weeks=weeks + 1))
    except Exception:
        logger.exception("asset card: stored history for %s failed", key)
        stored = pd.DataFrame()
    hist, n_stored = ac.history_with_store(key, s, cot_hist, obs if ac.has_macro(key) else None, stored,
                                           weeks=weeks, today=today)
    return s, src, cot_hist, obs, hist, n_stored


def _store_stamp() -> float:
    import os
    from tradinglib import edge_history as eh
    try:
        return os.path.getmtime(eh.db_path())
    except OSError:
        return 0.0


def _gauge(total: float | None) -> go.Figure:
    v = 0 if total is None else total
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=v, number={"valueformat": "+.1f", "font": {"size": 34}},
        gauge={"axis": {"range": [-10, 10], "tickvals": [-10, -5, -2, 0, 2, 5, 10]},
               "bar": {"color": "rgba(0,0,0,0)"},
               "threshold": {"line": {"color": "#1e88e5", "width": 5}, "thickness": 0.9, "value": v},
               "steps": [{"range": [-10, -5], "color": _BIAS_COLORS["strong_bear"]},
                         {"range": [-5, -2], "color": _BIAS_COLORS["bear"]},
                         {"range": [-2, 2], "color": _BIAS_COLORS["neutral"]},
                         {"range": [2, 5], "color": _BIAS_COLORS["bull"]},
                         {"range": [5, 10], "color": _BIAS_COLORS["strong_bull"]}]}))
    fig.update_layout(height=220, margin=dict(t=20, b=0, l=25, r=25))
    return fig


def _history_chart(hist: pd.DataFrame) -> go.Figure:
    h = hist.dropna(subset=["total"])
    fig = go.Figure(go.Bar(x=h["date"], y=h["total"],
                           marker_color=[_BULL if v > 0 else _BEAR if v < 0 else "#9e9e9e" for v in h["total"]],
                           name=t("asset.total"),
                           hovertemplate="%{x|%d.%m.%Y}: %{y:+.1f}<extra></extra>"))
    for y in (2, -2, 5, -5):
        fig.add_hline(y=y, line_dash="dot", line_color="rgba(128,128,128,.5)", line_width=1)
    fig.update_layout(height=230, yaxis=dict(range=[-10.5, 10.5], dtick=5), showlegend=False,
                      margin=dict(t=10, b=10, l=10, r=10))
    return fig


# ── Seite ─────────────────────────────────────────────────────────────────────

class AssetCardPage:
    def __init__(self, username: str = ""):
        self.username = username

    def _select(self, side) -> tuple[str, int]:
        keys = sorted(ac.markets(), key=lambda m: _GROUP_ORDER.index(m[1]) if m[1] in _GROUP_ORDER else 99)
        opts = [k for k, _g in keys]
        group = dict(keys)
        # The widget owns the state via `key`. Passing `index=` derived from the state
        # changes the widget's identity on every pick, so Streamlit resets it and the
        # new choice only took effect on the second selection.
        if st.session_state.get(_STATE_KEY) not in opts:
            st.session_state[_STATE_KEY] = DEFAULT_MARKET if DEFAULT_MARKET in opts else opts[0]
        c1, c2 = st.columns([3, 2])
        key = c1.selectbox(t("asset.symbol"), opts, key=_STATE_KEY,
                           format_func=lambda k: f"{t('cot.mkt_' + k)} · {t('cot.group_' + group[k])}")
        weeks = c2.select_slider(t("asset.history_weeks"), options=[26, 52, 104], value=52,
                                 key="_asset_card_weeks")
        if side is not None:
            with side:
                if st.button(t("cot.refresh"), key="_asset_refresh", width="stretch"):
                    _asset_data.clear()
        return key, weeks

    def render(self, side=None) -> None:
        from tradinglib.edge_scores_page import title
        title(t("page.asset_card"))
        st.markdown(_CSS, unsafe_allow_html=True)
        key, weeks = self._select(side)
        self.render_market(key, weeks)

    def render_market(self, key: str, weeks: int = 52) -> None:
        """Scorecard eines festen Marktes (ohne Auswahl) — auch fuer den Asset Viewer."""
        st.markdown(_CSS, unsafe_allow_html=True)
        cd.ensure_fresh()
        md.ensure_fresh()
        cot_date = cd.latest_report_date()
        with st.spinner(t("asset.loading")):
            s, src, cot_hist, obs, hist, n_stored = _asset_data(
                key, dt.date.today().isoformat(), cot_date.isoformat() if cot_date else "",
                md.get_meta("last_success") or "", weeks, _store_stamp())
        if s.empty:
            st.warning(t("trend.missing", markets=t(f"cot.mkt_{key}")))
            return
        sign = td.SIGN.get(key, 1)
        trend = ac.trend_at(s, dt.date.today(), sign)
        cot_row = cot_hist.iloc[-1] if not cot_hist.empty else None
        macro_lat = md.latest(obs) if not obs.empty else pd.DataFrame()
        macro_tab = md.score_table(macro_lat) if not macro_lat.empty else pd.DataFrame()
        macro_score = float(macro_tab.at[key, "score"]) if ac.has_macro(key) and key in macro_tab.index else None
        cot_score = None if cot_row is None else float(cot_row["score"])
        total = ac.total_of(cot_score, macro_score, trend["score"])
        n_comp = ac.n_components(cot_score, macro_score, trend["score"])
        bias = tot.bias_for(total, n_comp, trend["score"], trend["p_sma200"])
        unconfirmed = tot.unconfirmed(total, trend["score"], trend["p_sma200"])

        st.caption(t("asset.caption", source=src) + (" · " + t("asset.inverted") if sign < 0 else ""))
        left, right = st.columns([2, 3])
        with left:
            st.markdown(f"<div class='ac-banner' style='background:{_BIAS_COLORS[bias]}'>"
                        f"{html.escape(t('cot.bias_' + bias))}"
                        + (f"<div style='font-size:.8rem;font-weight:500'>{html.escape(t('asset.only_trend'))}</div>"
                           if n_comp == 1 else "")
                        + (f"<div style='font-size:.8rem;font-weight:500'>{html.escape(t('asset.unconfirmed'))}</div>"
                           if unconfirmed else "") + "</div>", unsafe_allow_html=True)
            st.plotly_chart(_gauge(total), width="stretch", config={"displayModeBar": False})
            comp = [
                [t("asset.total"), (_f(total, "{:+.1f}"), _BIAS_COLORS[bias]), "−10 … +10"],
                [t("asset.comp_trend"), _score_cell(trend["score"], td.bias_label), "−4 … +4"],
                [t("asset.comp_cot"), _score_cell(cot_score, cd.bias_label), "−3 … +3"],
                [t("asset.comp_macro"), _score_cell(macro_score, md.bias_label), "−6 … +6"],
            ]
            st.markdown(_panel(t("asset.scores"), None, [], comp), unsafe_allow_html=True)
            st.markdown(f"**{t('asset.history_title')}**")
            st.plotly_chart(_history_chart(hist), width="stretch", config={"displayModeBar": False})
            st.caption(t("asset.history_hint") + " " + t("asset.history_source", stored=n_stored,
                                                               live=max(len(hist) - n_stored, 0)))
        with right:
            st.markdown(self._trend_panel(trend, s, sign), unsafe_allow_html=True)
            st.markdown(self._cot_panel(key, cot_row), unsafe_allow_html=True)
            st.markdown(self._macro_panel(key, macro_tab, macro_lat), unsafe_allow_html=True)
            st.markdown(self._tech_panel(s, sign, self._rel_strength(key)), unsafe_allow_html=True)
        with st.expander(t("asset.method_title")):
            st.markdown(t("asset.method_md"))

    # ── Panels ────────────────────────────────────────────────────────────────
    def _trend_panel(self, tr: dict, s: pd.Series, sign: int) -> str:
        m, sea = tr["metrics"], tr["season"]
        if not m:
            return _panel(t("asset.panel_trend"), None, [], [[t("asset.too_short")]])
        rows = [
            [t("asset.tr_sma200"), _f(m["dist200"], "{:+.1f} %"), _state(tr["p_sma200"])],
            [t("asset.tr_cross"), f"{_price(m['sma50'])} / {_price(m['sma200'])}", _state(tr["p_cross"])],
            [t("asset.tr_mom"), _f(m["r63"], "{:+.1f} %"), _state(tr["p_mom"])],
            [t("asset.tr_season"),
             (_f(sea["mean"], "{:+.2f} %") + " · " + _f(sea["hit"], "{:.0f} %") + f" ({sea['n']} {t('asset.years_short')})")
             if sea["n"] else "–", _state(tr["p_season"])],
        ]
        return _panel(t("asset.panel_trend"), td.bias_label(tr["score"]),
                      [t("asset.col_component"), t("asset.col_value"), t("asset.col_signal")], rows)

    def _cot_panel(self, key: str, r) -> str:
        if r is None:
            return _panel(t("asset.panel_cot"), None, [], [[t("asset.no_cot")]])
        rows = [
            [t("asset.cot_pos"), _f(r["long_rank"], "{:.0f}") + " / 100", _state(r["score_pos"])],
            [t("asset.cot_mom"), _f(r["d_long_pct"], "{:+.2f} Pp"), _state(r["score_mom"])],
            [t("asset.cot_long_short"), f"{_f(r['long_pct'], '{:.1f} %')} / {_f(r['short_pct'], '{:.1f} %')}", ""],
            [t("asset.cot_net"), f"{int(r['net']):,} · Δ " + _f(r["d_net"], "{:+,.0f}"), ""],
            [t("cot.col_cot_index"), _f(r["cot_index"], "{:.0f}"), ""],
            [t("asset.cot_asof"), pd.Timestamp(r["report_date"]).strftime("%d.%m.%Y") + " · "
             + t("cot.col_release_date") + " " + r["release_date"].strftime("%d.%m.%Y"), ""],
        ]
        return _panel(t("asset.panel_cot"), cd.bias_label(r["score"]),
                      [t("asset.col_component"), t("asset.col_value"), t("asset.col_signal")], rows)

    def _macro_panel(self, key: str, tab: pd.DataFrame, lat: pd.DataFrame) -> str:
        counts = ac.has_macro(key)
        ccy = key if counts else "USD"
        if tab.empty or ccy not in tab.index:
            return _panel(t("asset.panel_macro"), None, [], [[t("asset.no_macro")]])
        pts = tab.loc[ccy]
        rows = []
        units = {"rate": "{:.2f} %", "cpi": "{:.1f} %", "unemp": "{:.1f} %", "gdp": "{:+.2f} %", "cli": "{:.1f}"}
        for ind in md.INDICATORS:
            r = lat[(lat["ccy"] == ccy) & (lat["indicator"] == ind)]
            if r.empty:
                rows.append([t(f"macro.ind_{ind}"), "–", "–", "–", _state(None)])
                continue
            r = r.iloc[0]
            per = pd.Timestamp(r["period"])
            per_txt = f"Q{(per.month - 1) // 3 + 1}/{per.year}" if r["freq"] == "Q" else per.strftime("%m/%Y")
            p = pts[f"p_{ind}"]
            rows.append([t(f"macro.ind_{ind}"), _f(r["value"], units[ind]), _f(r["change"], "{:+.2f}"),
                         per_txt, _state(p) if counts else (_state(p)[0], "")])
        title = t("asset.panel_macro") if counts else t("asset.panel_macro_context", ccy=ccy)
        return _panel(title, md.bias_label(pts["score"]) if counts else None,
                      [t("asset.col_component"), t("asset.col_value"), "Δ", t("asset.col_period"),
                       t("asset.col_signal")], rows)

    def _rel_strength(self, key: str) -> dict | None:
        if ac.PEER_GROUPS.get(dict(ac.markets()).get(key)) is None:
            return None
        from tradinglib.trend_page import _compute, _markets_sig
        try:
            _card, series = _compute(dt.date.today().isoformat(), _markets_sig())
        except Exception:
            logger.exception("asset card: peer series failed")
            return None
        return ac.relative_strength(key, series)

    def _tech_panel(self, s: pd.Series, sign: int, rs: dict | None = None) -> str:
        x = ac.technicals(s, sign)
        if not x:
            return ""
        rows = [[f"SMA {w}", _price(x.get(f"sma{w}")), _state(x.get(f"sma{w}_state"))] for w in ac.SMA_WINDOWS]
        rows += [
            [t("asset.vol20"), _f(x["vol20"], "{:.1f} %"), t(f"asset.vol_{ac.vol_level(x['vol_rank'])}")],
            [t("asset.move7"), _f(x["move7"], "{:.2f} %"), ""],
            [t("asset.move90"), _f(x["move90"], "{:.2f} %"), ""],
        ]
        if rs:
            rows.append([t("asset.rs_label", weeks=rs["weeks"], group=t(f"asset.peer_{rs['peer']}")),
                         t("asset.rs_value", ret=f"{rs['ret']:+.1f}", med=f"{rs['median']:+.1f}",
                           diff=f"{rs['diff']:+.1f}", rank=rs["rank"], n=rs["n"]),
                         _state(rs["state"])])
        return _panel(t("asset.panel_tech", d=x["date"].strftime("%d.%m.%Y"), last=_price(x["last"])), None,
                      [t("asset.col_component"), t("asset.col_value"), t("asset.col_signal")], rows)


def _score_cell(v, label_fn) -> tuple[str, str] | str:
    if v is None or pd.isna(v):
        return "–"
    return f"{v:+.0f}", _BIAS_COLORS[label_fn(v)]
