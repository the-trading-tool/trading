"""Sector Rotation Dashboard — 3-level UI.

Level 1: Relative Rotation Graph (RRG)   — macro orientation
Level 2: Capital Flow Treemap (CMF)      — sector comparison at a glance
Level 3: Sector Matrix (interactive)     — structured deep-dive
"""
import io
import json
import logging

import numpy as np
import pandas as pd
import plotly.colors as pc
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from tradinglib.sector_rotation import (
    DEFAULT_PERIOD,
    UNIVERSES,
    US_INDUSTRY_ETFS,
    US_SECTOR_ETFS,
    SectorRotation,
)

logger = logging.getLogger(__name__)

# ─── Status colours ───────────────────────────────────────────────────────────

_STATUS_COLOR = {
    "Leading":   "#2ecc71",
    "Weakening": "#f39c12",
    "Lagging":   "#e74c3c",
    "Improving": "#3498db",
    "N/A":       "#7f8c8d",
}

_CMF_LABEL_COLOR = {
    "Strong Inflow":  "#006400",
    "Inflow":         "#228B22",
    "Neutral":        "#808080",
    "Outflow":        "#B22222",
    "Strong Outflow": "#8B0000",
}


def _cmf_label(v: float) -> str:
    if np.isnan(v):
        return "Neutral"
    if v >  0.15: return "Strong Inflow"
    if v >  0.05: return "Inflow"
    if v > -0.05: return "Neutral"
    if v > -0.15: return "Outflow"
    return "Strong Outflow"


def _num_to_rgba(val: float, vmin: float, vmax: float, colorscale: str = "RdYlGn", alpha: float = 0.55) -> str:
    """Map a numeric value onto a Plotly colorscale and return an rgba string."""
    if pd.isna(val) or val != val:
        return "rgba(128,128,128,0.08)"
    t = max(0.0, min(1.0, (val - vmin) / (vmax - vmin)))
    rgb_str = pc.sample_colorscale(colorscale, [t])[0]   # e.g. 'rgb(0, 104, 55)'
    inner = rgb_str[4:-1]                                  # '0, 104, 55'
    return f"rgba({inner},{alpha})"


_STATUS_RGBA = {
    "Leading":   "rgba(46,204,113,0.45)",
    "Weakening": "rgba(243,156,18,0.45)",
    "Lagging":   "rgba(231,76,60,0.45)",
    "Improving": "rgba(52,152,219,0.45)",
    "N/A":       "rgba(128,128,128,0.15)",
}

_TREND_RGBA = {
    "↗ Strong":  "rgba(39,174,96,0.50)",
    "↗ Turning": "rgba(46,204,113,0.50)",
    "→ Stable":  "rgba(128,128,128,0.12)",
    "↘ Fading":  "rgba(243,156,18,0.50)",
    "↘ Weak":    "rgba(231,76,60,0.50)",
    "N/A":       "rgba(128,128,128,0.08)",
}


# ─── Cached data loader ───────────────────────────────────────────────────────

def _bench_options(universe_name: str, default: str) -> list[str]:
    """Return benchmark choices with the universe default first."""
    all_options = {
        "US Sectors":   ["SPY", "QQQ", "IWM", "^GSPC"],
        "EU Sectors":   ["EXSA.DE", "EXW1.DE", "^STOXX", "^STOXX50E"],
        "EM Countries": ["EEM", "VWO", "IEMG"],
    }
    opts = all_options.get(universe_name, [default])
    if default not in opts:
        opts = [default] + opts
    return opts


@st.cache_data(ttl=3600, show_spinner=False)
def _load_data(benchmark: str, period: str, etfs_json: str, weights_json: str, include_pe: bool):
    etfs    = json.loads(etfs_json)
    weights = json.loads(weights_json)
    rot = SectorRotation(
        sector_etfs=etfs, benchmark=benchmark, period=period, weights=weights
    )
    rot.fetch_all()
    summary        = rot.build_summary(include_pe=include_pe)
    rrg_weekly     = rot.calc_rrg_coordinates(tail_weeks=5)
    rrg_daily      = rot.calc_rrg_coordinates_daily(tail_days=15)
    return summary, rrg_weekly, rrg_daily


# ─── Page class ───────────────────────────────────────────────────────────────

class SectorRotationPage:
    """Streamlit page: 3-level sector rotation dashboard."""

    def __init__(self, username: str = "") -> None:
        self.username = username

    def render(self) -> None:
        st.header("Sector Rotation Analysis")
        st.caption(
            "Tracks institutional capital flows using RSC, Chaikin Money Flow, OBV "
            "and the Relative Rotation Graph (RRG) — for US sectors, European sectors, "
            "or Emerging Market countries."
        )

        # ── Top controls ──────────────────────────────────────────────────────
        c_univ, c_bench, c_period, c_pe, c_refresh = st.columns([0.22, 0.20, 0.14, 0.12, 0.32])

        universe_name = c_univ.selectbox(
            "Universe",
            list(UNIVERSES.keys()),
            index=0,
            help="US Sectors = SPDR S&P ETFs | EU Sectors = iShares STOXX 600 (XETRA, EUR) | EM Countries = MSCI country ETFs (USD)",
        )
        universe = UNIVERSES[universe_name]

        # Benchmark options: universe default first, then alternatives
        bench_options = _bench_options(universe_name, universe["benchmark"])
        benchmark = c_bench.selectbox(
            "Benchmark",
            bench_options,
            index=0,
            help="Reference index for all relative-strength calculations.",
        )

        period = c_period.selectbox(
            "History", ["1y", "2y", "3y"], index=1,
            help="Longer history = more stable EWM baselines.",
        )
        include_pe = c_pe.toggle(
            "Fwd P/E", value=False,
            help="Fetches forwardPE from Yahoo Finance — adds ~10 s.",
        )
        if c_refresh.button("Refresh data", use_container_width=True):
            st.cache_data.clear()

        # Universe-specific note (FX warning for EM, etc.)
        if universe["note"]:
            st.info(universe["note"], icon="ℹ️")

        with st.spinner(f"Fetching {universe_name} data and computing indicators…"):
            summary, rrg_weekly, rrg_daily = _load_data(
                benchmark,
                period,
                json.dumps(universe["etfs"]),
                json.dumps(universe["weights"]),
                include_pe,
            )

        if summary.empty:
            st.error("No data returned. Check your internet connection.")
            return

        # ── 6-level tabs ──────────────────────────────────────────────────────
        tab_rrg, tab_rrg_d, tab_treemap, tab_matrix, tab_drill, tab_stocks = st.tabs([
            "RRG Weekly",
            "RRG Daily",
            "Capital Flow Treemap",
            "Sector Matrix",
            "Industry Drill-down",
            "Best of Sector",
        ])

        with tab_rrg:
            self._render_rrg(rrg_weekly, summary, universe_name=universe_name)

        with tab_rrg_d:
            self._render_rrg(
                rrg_daily, summary,
                universe_name=universe_name,
                height=500,
                tail_label="15 trading days",
            )

        with tab_treemap:
            self._render_treemap(summary, universe_name=universe_name)

        with tab_matrix:
            self._render_matrix(summary)

        with tab_drill:
            self._render_industry_drilldown(universe_name=universe_name, period=period)

        with tab_stocks:
            self._render_sector_stocks(universe_name=universe_name)

    # ── Level 1: Relative Rotation Graph ──────────────────────────────────────

    def _render_rrg(self, rrg_data: dict, summary: pd.DataFrame, universe_name: str = "", height: int = 620, tail_label: str = "5 weeks") -> None:
        label = "sector" if "EM" not in universe_name else "country"
        st.subheader("Relative Rotation Graph (RRG)")
        st.caption(
            f"Each {label} is shown as a snake over the last **{tail_label}**. "
            "Rotation runs clockwise: **Leading → Weakening → Lagging → Improving → Leading**."
        )

        status_map = summary.set_index("Sector")["Status"].to_dict()
        fig = go.Figure()

        # Quadrant shading and labels
        quadrants = [
            (100, 115, 100, 115, "rgba(46,204,113,0.08)",  "Leading",   "right", "top"),
            (100, 115, 85,  100, "rgba(243,156,18,0.08)",  "Weakening", "right", "bottom"),
            (85,  100, 85,  100, "rgba(231,76,60,0.08)",   "Lagging",   "left",  "bottom"),
            (85,  100, 100, 115, "rgba(52,152,219,0.08)",  "Improving", "left",  "top"),
        ]
        for x0, x1, y0, y1, fill, label, xanch, yanch in quadrants:
            fig.add_shape(
                type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                fillcolor=fill, line_width=0, layer="below",
            )
            fig.add_annotation(
                x=x1 if xanch == "right" else x0,
                y=y1 if yanch == "top" else y0,
                text=f"<b>{label}</b>",
                font=dict(size=12, color="gray"),
                showarrow=False, xanchor=xanch, yanchor=yanch,
            )

        fig.add_hline(y=100, line_dash="dash", line_color="gray", line_width=0.8)
        fig.add_vline(x=100, line_dash="dash", line_color="gray", line_width=0.8)

        for sector, data in rrg_data.items():
            xs, ys = data["rs_ratio"], data["rs_momentum"]
            if not xs or not ys:
                continue
            status = status_map.get(sector, "N/A")
            color = _STATUS_COLOR.get(status, "#888")
            n = len(xs)

            # Fading tail (older = more transparent)
            for i in range(n - 1):
                alpha = round(0.15 + 0.65 * (i / max(n - 2, 1)), 2)
                fig.add_trace(go.Scatter(
                    x=[xs[i], xs[i + 1]], y=[ys[i], ys[i + 1]],
                    mode="lines",
                    line=dict(color=color, width=2),
                    opacity=alpha,
                    showlegend=False,
                    hoverinfo="skip",
                ))

            # Current endpoint with label
            fig.add_trace(go.Scatter(
                x=[xs[-1]], y=[ys[-1]],
                mode="markers+text",
                name=sector,
                text=[f"<b>{sector}</b>"],
                textposition="top center",
                textfont=dict(size=10),
                marker=dict(
                    size=13, color=color,
                    line=dict(width=1.5, color="white"),
                ),
                hovertemplate=(
                    f"<b>{sector}</b> ({data['ticker']})<br>"
                    f"RS-Ratio:    {xs[-1]:.2f}<br>"
                    f"RS-Momentum: {ys[-1]:.2f}<br>"
                    f"Status: {status}<extra></extra>"
                ),
            ))

        fig.update_layout(
            height=height,
            xaxis=dict(
                title="RS-Ratio  →  (>100 = outperforming)",
                range=[85, 115],
                showgrid=True, gridcolor="rgba(128,128,128,0.15)",
            ),
            yaxis=dict(
                title="RS-Momentum  →  (>100 = accelerating)",
                range=[85, 115],
                showgrid=True, gridcolor="rgba(128,128,128,0.15)",
            ),
            showlegend=True,
            legend=dict(orientation="h", y=-0.18, font=dict(size=11)),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=20, b=80),
        )

        st.plotly_chart(fig, use_container_width=True, theme="streamlit")

        # Status summary metrics
        sc = summary["Status"].value_counts()
        m1, m2, m3, m4 = st.columns(4)
        for col, status in zip([m1, m2, m3, m4], ["Leading", "Weakening", "Lagging", "Improving"]):
            col.metric(status, sc.get(status, 0))

    # ── Level 2: CMF Treemap ──────────────────────────────────────────────────

    def _render_treemap(self, summary: pd.DataFrame, universe_name: str = "") -> None:
        weight_label = {
            "US Sectors":   "S&P 500 sector weight",
            "EU Sectors":   "STOXX 600 sector weight",
            "EM Countries": "MSCI EM country weight",
        }.get(universe_name, "index weight")
        st.subheader("Capital Flow Treemap")
        st.caption(
            f"**Tile size** = approximate {weight_label}. "
            "**Colour** = Chaikin Money Flow (14D): dark green = strong inflow, dark red = strong outflow."
        )

        df = summary.copy()
        df["CMF_label"] = df["CMF_14D"].apply(_cmf_label)
        df["CMF_display"] = df["CMF_14D"].map(
            lambda v: f"{v:.3f}" if pd.notna(v) else "N/A"
        )
        df["RSC_display"] = df["RSC_30W"].map(
            lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
        )

        fig = px.treemap(
            df,
            path=[px.Constant("All Sectors"), "Sector"],
            values="Weight_%",
            color="CMF_label",
            color_discrete_map={"(?)": "#262931", **_CMF_LABEL_COLOR},
            custom_data=["ETF", "RSC_display", "CMF_display", "Status", "OBV_Trend"],
            height=540,
        )

        fig.update_traces(
            texttemplate=(
                "<b>%{label}</b><br>"
                "CMF: %{customdata[2]}<br>"
                "<i>%{customdata[3]}</i>"
            ),
            hovertemplate=(
                "<b>%{label}</b> (%{customdata[0]})<br>"
                "RSC (30W): %{customdata[1]}<br>"
                "CMF (14D): %{customdata[2]}<br>"
                "OBV Trend: %{customdata[4]:.2f}<br>"
                "Status: %{customdata[3]}<extra></extra>"
            ),
        )

        st.plotly_chart(fig, use_container_width=True, theme="streamlit")

        st.caption(
            "**CMF thresholds:**  "
            ">0.15 = strong buying pressure &nbsp;|&nbsp; "
            "0.05–0.15 = accumulation &nbsp;|&nbsp; "
            "±0.05 = neutral &nbsp;|&nbsp; "
            "-0.15 to -0.05 = distribution &nbsp;|&nbsp; "
            "<-0.15 = strong selling pressure"
        )

    # ── Level 4: Industry Drill-down ──────────────────────────────────────────

    def _render_industry_drilldown(self, universe_name: str, period: str) -> None:
        if universe_name != "US Sectors":
            st.info(
                "Industry drill-down is currently available for **US Sectors** only. "
                "Switch the Universe selector to 'US Sectors' to use this feature.",
                icon="ℹ️",
            )
            return

        st.subheader("Industry Drill-down")
        st.caption(
            "Select a sector to see how its industries rotate against each other. "
            "**Benchmark = the parent sector ETF** — RS-Ratio > 100 means the industry "
            "outperforms its own sector, not the overall market."
        )

        available_sectors = list(US_INDUSTRY_ETFS.keys())
        sector = st.selectbox(
            "Drill into sector:",
            available_sectors,
            index=0,
            key="drill_sector",
        )

        industries   = US_INDUSTRY_ETFS[sector]
        sector_etf   = US_SECTOR_ETFS.get(sector, "SPY")
        equal_weights = {name: 1.0 for name in industries}

        st.caption(
            f"Benchmark: **{sector_etf}** ({sector} ETF) &nbsp;|&nbsp; "
            f"{len(industries)} industries &nbsp;|&nbsp; "
            f"Tickers: {', '.join(industries.values())}"
        )

        with st.spinner(f"Loading {sector} industry data…"):
            try:
                summary, rrg_weekly, rrg_daily = _load_data(
                    sector_etf,
                    period,
                    json.dumps(industries),
                    json.dumps(equal_weights),
                    False,
                )
            except Exception as exc:
                st.error(f"Failed to load industry data: {exc}")
                return

        if summary.empty:
            st.warning("No industry data returned — some ETFs may have insufficient history.")
            return

        # Weekly and daily mini-RRG side by side via sub-tabs
        sub_weekly, sub_daily = st.tabs(["Weekly RRG", "Daily RRG (15D)"])
        with sub_weekly:
            self._render_rrg(
                rrg_weekly, summary,
                universe_name=f"{sector} industries",
                height=460,
            )
        with sub_daily:
            self._render_rrg(
                rrg_daily, summary,
                universe_name=f"{sector} industries",
                height=460,
                tail_label="15 trading days",
            )

        # Compact status strip
        sc = summary["Status"].value_counts()
        cols = st.columns(4)
        for col, status in zip(cols, ["Leading", "Weakening", "Lagging", "Improving"]):
            col.metric(status, sc.get(status, 0))

        st.markdown("---")

        # Industry comparison matrix (reuses existing method)
        self._render_matrix(summary, key=f"industry_{sector.lower().replace(' ', '_').replace('.', '')}")

    # ── Level 3: Sector Matrix ────────────────────────────────────────────────

    def _render_matrix(self, summary: pd.DataFrame, key: str = "sector") -> None:
        st.subheader("Sector Comparison Matrix")
        st.caption(
            "Green = bullish, red = bearish. "
            "RSC centred on 0, RS-Ratio / RS-Momentum centred on 100. "
            "Sorted strongest → weakest by RSC."
        )

        display_order = [
            "Sector", "ETF",
            "RSC_4W", "RSC_12W", "RSC_30W", "RSC_Trend",
            "CMF_14D", "OBV_Trend",
            "Above_SMA50", "Above_SMA200",
            "RS_Ratio", "RS_Momentum", "Status",
        ]
        if "Forward_PE" in summary.columns:
            display_order.insert(-1, "Forward_PE")

        df = summary[[c for c in display_order if c in summary.columns]].copy()
        df = df.rename(columns={
            "RSC_4W":       "RSC 4W %",
            "RSC_12W":      "RSC 12W %",
            "RSC_30W":      "RSC 30W %",
            "RSC_Trend":    "RSC Trend",
            "CMF_14D":      "CMF 14D",
            "OBV_Trend":    "OBV Trend",
            "Above_SMA50":  "> SMA 50",
            "Above_SMA200": "> SMA 200",
            "Forward_PE":   "Fwd P/E",
            "RS_Ratio":     "RS-Ratio",
            "RS_Momentum":  "RS-Mom.",
        })
        df = df.sort_values("RSC 30W %", ascending=False, na_position="last")

        # ── Format display values ─────────────────────────────────────────────
        _RSC_COLS = ("RSC 4W %", "RSC 12W %", "RSC 30W %")

        def fmt_col(series: pd.Series, col: str) -> list[str]:
            if col in _RSC_COLS:
                return [f"{v:+.2f}%" if pd.notna(v) else "N/A" for v in series]
            if col == "CMF 14D":
                return [f"{v:.3f}" if pd.notna(v) else "N/A" for v in series]
            if col in ("OBV Trend", "RS-Ratio", "RS-Mom."):
                return [f"{v:.2f}" if pd.notna(v) else "N/A" for v in series]
            if col == "Fwd P/E":
                return [f"{v:.1f}" if pd.notna(v) and v == v else "N/A" for v in series]
            return [str(v) if pd.notna(v) else "N/A" for v in series]

        # ── Per-cell fill colours ─────────────────────────────────────────────
        _NEUTRAL = "rgba(128,128,128,0.08)"
        _GRAD_CFG = {
            "RSC 4W %":   (-6.0,   6.0),
            "RSC 12W %":  (-6.0,   6.0),
            "RSC 30W %":  (-6.0,   6.0),
            "CMF 14D":    (-0.3,   0.3),
            "OBV Trend":  (-5.0,   5.0),
            "RS-Ratio":   (90.0, 110.0),
            "RS-Mom.":    (90.0, 110.0),
        }

        def col_colors(col: str) -> list[str]:
            if col == "Status":
                return [_STATUS_RGBA.get(v, _NEUTRAL) for v in df[col]]
            if col == "RSC Trend":
                return [_TREND_RGBA.get(v, _NEUTRAL) for v in df[col]]
            if col in _GRAD_CFG:
                vmin, vmax = _GRAD_CFG[col]
                return [_num_to_rgba(v, vmin, vmax) for v in df[col]]
            return [_NEUTRAL] * len(df)

        _WIDE = {"Sector", "RSC Trend"}
        columns = list(df.columns)
        cell_values  = [fmt_col(df[c], c) for c in columns]
        cell_colors  = [col_colors(c) for c in columns]

        # ── Build Plotly table ────────────────────────────────────────────────
        fig = go.Figure(data=[go.Table(
            columnwidth=[2.2 if c in _WIDE else 1.0 for c in columns],
            header=dict(
                values=[f"<b>{c}</b>" for c in columns],
                fill_color="rgba(40,40,60,0.85)",
                font=dict(color="white", size=12),
                align="center",
                height=32,
            ),
            cells=dict(
                values=cell_values,
                fill_color=cell_colors,
                align=["left" if c in ("Sector", "ETF", "Status", "RSC Trend") else "right" for c in columns],
                font=dict(size=12),
                height=28,
            ),
        )])

        fig.update_layout(
            margin=dict(t=4, b=4, l=4, r=4),
            height=max(360, 34 + len(df) * 28 + 40),
        )

        st.plotly_chart(fig, use_container_width=True, theme="streamlit")

        # Download button
        xlsx = self._to_excel(df)
        st.download_button(
            label="Download as Excel",
            data=xlsx,
            file_name=f"sector_rotation_{key}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_matrix_{key}",
        )

    # ── Level 6: Best of Sector ───────────────────────────────────────────────

    def _render_sector_stocks(self, universe_name: str) -> None:
        """Show ranked stocks within a selected sector from the local simulation DB.

        Works for all universes — sector names come from the GICS taxonomy stored
        in asset_info.db (same for US, EU and EM stocks).
        RSC is always computed vs the SPDR US sector ETF; for EU/EM stocks this
        gives a USD-denominated global-sector benchmark, which is intentional.
        """
        st.subheader("Best of Sector")
        st.caption(
            "Rank the stocks in your local simulation database by performance metrics "
            "within a chosen GICS sector.  "
            "Optionally compute **4-week RSC vs the sector ETF** "
            "(downloads ~3 months of price data — takes a few seconds)."
        )

        try:
            from tradinglib.sector_stocks import (
                RANK_OPTIONS,
                SECTOR_ETF_MAP,
                get_available_sectors,
                query_sector_stocks,
                enrich_with_rsc,
            )
        except ImportError as exc:
            st.error(f"sector_stocks module not available: {exc}")
            return

        # ── Controls ──────────────────────────────────────────────────────────
        c_sec, c_rank, c_top, c_rsc = st.columns([0.28, 0.28, 0.12, 0.32])

        available_sectors = get_available_sectors()
        if not available_sectors:
            st.warning("No sector data found in the local database.")
            return

        sector = c_sec.selectbox(
            "Sector", available_sectors, key="stocks_sector",
        )
        rank_label = c_rank.selectbox(
            "Rank by", list(RANK_OPTIONS.keys()), key="stocks_rank",
        )
        top_n = c_top.selectbox(
            "Top N", [10, 20, 30, 50], index=1, key="stocks_topn",
        )
        sector_etf = SECTOR_ETF_MAP.get(sector, "")
        etf_help = (
            f"Computes 4-week return of each stock minus 4-week return of "
            f"**{sector_etf}** (SPDR sector ETF).  "
            "Positive = stock outperforms its sector."
            if sector_etf else
            "No sector ETF mapped for this sector — RSC unavailable."
        )
        show_rsc = c_rsc.toggle(
            f"RSC vs {sector_etf or 'ETF'}",
            value=False,
            key="stocks_rsc",
            help=etf_help,
            disabled=not sector_etf,
        )

        rank_col = RANK_OPTIONS[rank_label]

        # ── Load DB data ───────────────────────────────────────────────────────
        with st.spinner(f"Loading top {top_n} stocks in {sector}…"):
            df, debug_info = query_sector_stocks(
                sector=sector, rank_col=rank_col, limit=top_n,
            )

        if df.empty:
            st.warning(
                f"No simulation data found for sector **{sector}**. "
                "Check that the daily simulation has been run "
                "(`get_asset_data.py` + scheduler)."
            )
            with st.expander("🔍 Diagnostic info", expanded=True):
                st.markdown(debug_info)
            return

        # ── Optional RSC enrichment ────────────────────────────────────────────
        if show_rsc and sector_etf:
            with st.spinner(f"Computing 4-week RSC vs {sector_etf}…"):
                df = enrich_with_rsc(df, sector_etf=sector_etf, weeks=4)

        # ── Metadata strip ─────────────────────────────────────────────────────
        data_date = df["Date"].iloc[0] if ("Date" in df.columns and not df.empty) else "N/A"
        etf_badge = f" · Sector ETF: **{sector_etf}**" if sector_etf else ""
        st.caption(
            f"**{len(df)} stocks** · sector: **{sector}** · "
            f"ranked by: **{rank_label}**{etf_badge} · "
            f"data as of: **{data_date}**"
        )

        # ── Column display setup ───────────────────────────────────────────────
        _rsc_label = f"RSC vs {sector_etf or 'ETF'} %"
        _RENAME = {
            "ticker":            "Ticker",
            "longName":          "Name",
            "exchange":          "Exchange",
            "close":             "Price",
            "currency":          "Ccy",
            "sortino":           "Sortino",
            "sharpe":            "Sharpe",
            "moTrend":           "Mo Trend %",
            "wkTrend":           "Wk Trend %",
            "dTrend":            "Day Trend %",
            "roa":               "ROA %",
            "revenueGrowth":     "Rev Growth",
            "marketCap":         "Mkt Cap",
            "momentum":          "Momentum",
            "overallTrend":      "Ovrl Trend",
            "overallValueTrend": "OVT",
            "Date":              "Date",
            "RSC_vs_ETF":        _rsc_label,
        }

        _BASE_COLS   = ["ticker", "longName", "close", "currency"]
        _METRIC_COLS = [
            "overallValueTrend", "sortino", "sharpe",
            "moTrend", "wkTrend", "dTrend",
            "roa", "revenueGrowth", "momentum",
        ]
        # Ensure the active ranking column is visible
        rank_raw = rank_col.split(".")[-1]
        if rank_raw not in _METRIC_COLS and rank_raw in df.columns:
            _METRIC_COLS.insert(0, rank_raw)

        if show_rsc and "RSC_vs_ETF" in df.columns:
            _METRIC_COLS.append("RSC_vs_ETF")

        wanted     = _BASE_COLS + _METRIC_COLS
        display_df = df[[c for c in wanted if c in df.columns]].copy()
        display_df = display_df.rename(columns=_RENAME)

        # ── ProgressColumn configs for numeric metrics ─────────────────────────
        _PROG_RANGES: dict[str, tuple[float, float]] = {
            "OVT":         (0,    60),
            "Sortino":     (0,     5),
            "Sharpe":      (0,     5),
            "Mo Trend %":  (-20,  20),
            "Wk Trend %":  (-10,  10),
            "Day Trend %": (-5,    5),
            "ROA %":       (0,    30),
            "Rev Growth":  (-0.5,  1.0),
            "Momentum":    (0,    60),
            "Ovrl Trend":  (0,    60),
        }
        _PROG_RANGES[_rsc_label] = (-20, 20)

        col_cfg: dict = {}
        for col_name, (lo, hi) in _PROG_RANGES.items():
            if col_name in display_df.columns:
                col_cfg[col_name] = st.column_config.ProgressColumn(
                    col_name,
                    min_value=lo,
                    max_value=hi,
                    format="%.2f",
                )

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config=col_cfg,
        )

        # Download button
        st.download_button(
            label="Download as Excel",
            data=self._to_excel(df.rename(columns=_RENAME)),
            file_name=f"sector_stocks_{sector.lower().replace(' ', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_stocks",
        )

        # ── Charts expander ────────────────────────────────────────────────────
        tickers_for_charts = df["ticker"] if "ticker" in df.columns else pd.Series(dtype=str)
        if not tickers_for_charts.empty:
            with st.expander(
                f"Charts — Top {len(df)} {sector} stocks", expanded=False
            ):
                st.caption(
                    "Charts use the same interval/indicator settings as the All Assets view."
                )
                try:
                    from tradinglib import tiny_chart as tc
                    from tradinglib import graph_tools as gt
                    from tradinglib.tiny_chart_grid import ChartsGridRenderer
                    from tradinglib import system_config as sysconf

                    sys_config = sysconf.SystemConfig(username=self.username)
                    renderer   = ChartsGridRenderer(columns=2)
                    interval, period_sel, overlays, oszilators = renderer.get_selectors(sys_config)

                    with st.spinner("Loading charts…"):
                        renderer.render(
                            tickers=tickers_for_charts,
                            tc=tc,
                            period=period_sel,
                            interval=interval,
                            overlays=overlays,
                            oszilators=oszilators,
                            username=self.username,
                            url="/?details=true&symbol=",
                            chart_config=gt.chart_config,
                            name_df=df,
                            name_lookup_col="ticker",
                            name_field="longName",
                            log_exceptions=True,
                        )
                except Exception as exc:
                    st.warning(f"Charts not available: {exc}")

    @staticmethod
    def _to_excel(df: pd.DataFrame) -> bytes:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Sector Rotation")
        return buf.getvalue()
