"""
Shared Streamlit widgets for backtest result visualisation.

Used by:
  - tradinglib/banner_page.py                   (Banner Page)
  - tradinglib/premium/multi_transaction.py      (Multi Strategies)
  - tradinglib/premium/asset_simulator.py        (Strategy Finder)
"""
import logging

import pandas as pd
import plotly.express as px
import streamlit as st

from tradinglib.i18n import t

logger = logging.getLogger(__name__)

_MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _max_streak(series: pd.Series, positive: bool) -> int:
    """Return the longest consecutive run of positive (or non-positive) values in series."""
    best = cur = 0
    for v in series:
        try:
            hit = (v > 0) if positive else (v <= 0)
        except Exception:
            hit = False
        if hit:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _make_heatmap_pivot(df: pd.DataFrame) -> 'pd.DataFrame | None':
    """Compute a year×month gain pivot from closed trades; returns None when data is empty."""
    try:
        closed = df[df['sellDate'].notna()].copy()
        if closed.empty:
            return None
        closed['_sd'] = pd.to_datetime(closed['sellDate'], errors='coerce')
        closed = closed[closed['_sd'].notna()]
        if closed.empty:
            return None
        closed['_year']  = closed['_sd'].dt.year
        closed['_month'] = closed['_sd'].dt.month
        pivot = (
            closed.groupby(['_year', '_month'])['gain']
            .sum()
            .reset_index()
            .pivot(index='_year', columns='_month', values='gain')
        )
        for m in range(1, 13):
            if m not in pivot.columns:
                pivot[m] = float('nan')
        pivot = pivot[sorted(pivot.columns)]
        pivot.columns = [_MONTH_ABBR[m - 1] for m in pivot.columns]
        pivot.index.name = None
        return pivot
    except Exception:
        logger.exception("Heatmap pivot failed")
        return None


def _heatmap_figure(pivot: 'pd.DataFrame', title: str, system_currency: str = '',
                    height: 'int | None' = None):
    """Build a Plotly RdYlGn imshow figure from a precomputed year×month pivot."""
    ccy = f" ({system_currency})" if system_currency else ''
    fig = px.imshow(
        pivot,
        color_continuous_scale='RdYlGn',
        color_continuous_midpoint=0,
        title=title,
        labels={'color': t('banner.heatmap_gain_label') + ccy},
        aspect='auto',
        text_auto='.0f',
    )
    layout = dict(xaxis_title=None, yaxis_title=None)
    if height:
        layout['height'] = height
    fig.update_layout(**layout)
    return fig


def render_monthly_heatmap(df: pd.DataFrame, region=None, system_currency: str = '',
                            title: str = ''):
    """Render a RdYlGn Plotly heatmap of realised gain per month × year.

    title overrides the default i18n title when provided.
    """
    if region is None:
        region = st
    if df.empty or 'gain' not in df.columns or 'sellDate' not in df.columns:
        return
    pivot = _make_heatmap_pivot(df)
    if pivot is None:
        return
    fig = _heatmap_figure(
        pivot,
        title=title or t('banner.heatmap_title'),
        system_currency=system_currency,
    )
    region.plotly_chart(fig, use_container_width=True)


def render_per_strategy_heatmaps(df: pd.DataFrame, region=None, system_currency: str = ''):
    """Render one compact heatmap per strategy side-by-side in columns.

    Only strategies with at least one closed trade are shown (height 280 px each).
    """
    if region is None:
        region = st
    if df.empty or 'Strategy' not in df.columns or 'gain' not in df.columns:
        return

    strategies = sorted(df['Strategy'].dropna().unique().tolist())
    if not strategies:
        return

    # Nur Strategien mit tatsächlichen abgeschlossenen Trades
    strategies = [s for s in strategies
                  if not df[(df['Strategy'] == s) & df['sellDate'].notna()].empty]
    if not strategies:
        return

    region.divider()
    region.markdown(f"### 📅 {t('banner.heatmap_per_strategy_header')}")

    n = len(strategies)
    cols = region.columns(n)

    for i, strategy in enumerate(strategies):
        s_df  = df[df['Strategy'] == strategy]
        pivot = _make_heatmap_pivot(s_df)
        if pivot is None:
            cols[i].caption(f"{strategy} — {t('banner.heatmap_no_data')}")
            continue
        fig = _heatmap_figure(
            pivot,
            title=strategy,
            system_currency=system_currency,
            height=280,
        )
        cols[i].plotly_chart(fig, use_container_width=True)


def render_strategy_analysis(df: pd.DataFrame, region=None, system_currency: str = ''):
    """KPI block grouped by Strategy — closed trades only."""
    if region is None:
        region = st
    if df.empty or 'Strategy' not in df.columns:
        return

    now = pd.Timestamp.now()
    m3  = now - pd.DateOffset(months=3)
    y1  = now - pd.DateOffset(years=1)

    try:
        closed = df[df['sellVolume'].notna()].copy()
    except Exception:
        closed = df[df['sellDate'].notna()].copy()

    if closed.empty:
        return

    try:
        closed['_sd'] = pd.to_datetime(closed['sellDate'], errors='coerce')
    except Exception:
        closed['_sd'] = pd.NaT

    region.divider()
    region.markdown(f"## {t('banner.strategy_analysis_header')}")
    region.caption(t('banner.strategy_analysis_caption'))

    for strategy in sorted(closed['Strategy'].dropna().unique()):
        s_closed = closed[closed['Strategy'] == strategy].sort_values('_sd')
        s_all    = df[df['Strategy'] == strategy]

        total_trades = len(s_closed)
        if total_trades == 0:
            continue

        wins   = s_closed[s_closed['gain'] > 0]
        losses = s_closed[s_closed['gain'] <= 0]
        win_rate      = round(len(wins) / total_trades * 100, 1)
        loss_sum      = abs(losses['gain'].sum())
        profit_factor = round(wins['gain'].sum() / loss_sum, 2) if loss_sum > 0 else float('inf')
        avg_gain      = round(s_closed['gain'].mean(), 2)
        total_gain    = round(s_closed['gain'].sum(), 2)
        win_streak    = _max_streak(s_closed['gain'], positive=True)
        loss_streak   = _max_streak(s_closed['gain'], positive=False)

        try:
            open_pos = int(s_all['sellVolume'].isna().sum())
        except Exception:
            open_pos = 0

        s_3m = s_closed[s_closed['_sd'] >= m3]
        s_1y = s_closed[s_closed['_sd'] >= y1]

        def _best(sub):
            return sub.loc[sub['gainPct'].idxmax()] if not sub.empty else None
        def _worst(sub):
            return sub.loc[sub['gainPct'].idxmin()] if not sub.empty else None

        pf_str = f"{profit_factor}" if profit_factor != float('inf') else "∞"
        header = (
            f"**{strategy}**  —  {total_trades} {t('banner.kpi_trades_unit')}"
            f"  |  {t('banner.kpi_profit_factor')}: {pf_str}"
            f"  |  {t('banner.kpi_win_rate')}: {win_rate} %"
        )

        with region.expander(header, expanded=True):
            if 'value trend' in strategy.lower():
                st.warning(t('banner.hint_global_tensions'))

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric(t('banner.kpi_open_pos'),      open_pos)
            c2.metric(t('banner.kpi_profit_factor'),  pf_str)
            c3.metric(t('banner.kpi_win_rate'),       f"{win_rate} %")
            c4.metric(t('banner.kpi_avg_gain'),       f"{avg_gain:+,.2f}")
            c5.metric(t('banner.kpi_total_gain'),     f"{total_gain:+,.2f}",
                      delta=f"{total_gain:+,.0f} {system_currency}")
            st.markdown("")

            c6, c7, _, _ = st.columns(4)
            c6.metric(t('banner.kpi_win_streak'), f"{win_streak} {t('banner.kpi_trades_unit')}")
            c7.metric(t('banner.kpi_loss_streak'), f"{loss_streak} {t('banner.kpi_trades_unit')}")
            st.markdown("")

            def _row(row, period: str):
                if row is None:
                    return None
                try:
                    return {
                        t('banner.kpi_period'): period,
                        '%': f"{row.get('gainPct', 0):+.2f} %",
                        system_currency: f"{row.get('gain', 0):+,.2f}",
                        'Ticker': str(row.get('ticker', '')),
                    }
                except Exception:
                    return None

            c_best, c_worst = st.columns(2)
            with c_best:
                st.markdown(f"**🏆 {t('banner.kpi_best_trades')}**")
                _rows = [r for r in [_row(_best(s_3m), '3M'), _row(_best(s_1y), '1Y')] if r]
                if _rows:
                    st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
                else:
                    st.caption("—")
            with c_worst:
                st.markdown(f"**📉 {t('banner.kpi_worst_trades')}**")
                _rows = [r for r in [_row(_worst(s_3m), '3M'), _row(_worst(s_1y), '1Y')] if r]
                if _rows:
                    st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
                else:
                    st.caption("—")


def render_portfolio_overlap(df: pd.DataFrame, region=None):
    """Warning when the same ticker is held by more than one strategy simultaneously."""
    if region is None:
        region = st
    if df.empty or 'Strategy' not in df.columns:
        return

    try:
        open_df = df[df['sellVolume'].isna()].copy()
    except (KeyError, AttributeError):
        try:
            open_df = df[df['sellDate'].isna()].copy()
        except Exception:
            return

    if open_df.empty:
        return

    overlap = (
        open_df.groupby('ticker')['Strategy']
        .nunique()
        .reset_index()
        .rename(columns={'Strategy': 'strategy_count'})
    )
    overlap = overlap[overlap['strategy_count'] > 1]
    if overlap.empty:
        return

    detail_rows = []
    for _, row in overlap.iterrows():
        ticker = row['ticker']
        strategies = open_df[open_df['ticker'] == ticker]['Strategy'].dropna().unique().tolist()
        detail_rows.append({
            t('banner.overlap_ticker'):     ticker,
            t('banner.overlap_strategies'): ', '.join(str(s) for s in strategies),
            t('banner.overlap_count'):      int(row['strategy_count']),
        })

    region.divider()
    region.warning(
        f"**{t('banner.overlap_header')}** — "
        + t('banner.overlap_caption', n=len(detail_rows))
    )
    region.dataframe(detail_rows, hide_index=True, use_container_width=True)


def render_compact_analysis(df: pd.DataFrame, region=None, system_currency: str = ''):
    """Simplified KPI block for single-strategy views (no Strategy grouping)."""
    if region is None:
        region = st
    if df.empty or 'gain' not in df.columns:
        return

    try:
        closed = df[df['sellDate'].notna()].copy()
        if closed.empty:
            return
        closed['_sd'] = pd.to_datetime(closed['sellDate'], errors='coerce')
    except Exception:
        return

    now = pd.Timestamp.now()
    m3  = now - pd.DateOffset(months=3)
    y1  = now - pd.DateOffset(years=1)

    total_trades = len(closed)
    if total_trades == 0:
        return

    wins   = closed[closed['gain'] > 0]
    losses = closed[closed['gain'] <= 0]
    win_rate      = round(len(wins) / total_trades * 100, 1)
    loss_sum      = abs(losses['gain'].sum())
    profit_factor = round(wins['gain'].sum() / loss_sum, 2) if loss_sum > 0 else float('inf')
    avg_gain      = round(closed['gain'].mean(), 2)
    total_gain    = round(closed['gain'].sum(), 2)
    win_streak    = _max_streak(closed['gain'], positive=True)
    loss_streak   = _max_streak(closed['gain'], positive=False)

    pf_str = f"{profit_factor}" if profit_factor != float('inf') else "∞"

    region.divider()
    region.markdown(f"## {t('banner.strategy_analysis_header')}")

    c1, c2, c3, c4, c5 = region.columns(5)
    c1.metric(t('banner.kpi_profit_factor'), pf_str)
    c2.metric(t('banner.kpi_win_rate'),       f"{win_rate} %")
    c3.metric(t('banner.kpi_avg_gain'),       f"{avg_gain:+,.2f}")
    c4.metric(t('banner.kpi_total_gain'),     f"{total_gain:+,.2f}",
              delta=f"{total_gain:+,.0f} {system_currency}")
    c5.metric(t('banner.kpi_trades'),         str(total_trades))
    region.markdown("")

    c6, c7, _, _ = region.columns(4)
    c6.metric(t('banner.kpi_win_streak'), f"{win_streak} {t('banner.kpi_trades_unit')}")
    c7.metric(t('banner.kpi_loss_streak'), f"{loss_streak} {t('banner.kpi_trades_unit')}")

    if 'gainPct' not in closed.columns:
        return

    s_3m = closed[closed['_sd'] >= m3]
    s_1y = closed[closed['_sd'] >= y1]

    def _best(sub):
        return sub.loc[sub['gainPct'].idxmax()] if not sub.empty else None
    def _worst(sub):
        return sub.loc[sub['gainPct'].idxmin()] if not sub.empty else None

    def _row(row, period: str):
        if row is None:
            return None
        try:
            return {
                t('banner.kpi_period'): period,
                '%': f"{row.get('gainPct', 0):+.2f} %",
                system_currency: f"{row.get('gain', 0):+,.2f}",
                'Ticker': str(row.get('ticker', '')),
            }
        except Exception:
            return None

    c_best, c_worst = region.columns(2)
    with c_best:
        region.markdown(f"**🏆 {t('banner.kpi_best_trades')}**")
        _rows = [r for r in [_row(_best(s_3m), '3M'), _row(_best(s_1y), '1Y')] if r]
        if _rows:
            st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
        else:
            st.caption("—")
    with c_worst:
        region.markdown(f"**📉 {t('banner.kpi_worst_trades')}**")
        _rows = [r for r in [_row(_worst(s_3m), '3M'), _row(_worst(s_1y), '1Y')] if r]
        if _rows:
            st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
        else:
            st.caption("—")
