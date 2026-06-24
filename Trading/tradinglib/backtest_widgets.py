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


def _max_drawdown(gains: pd.Series) -> float:
    """Largest peak-to-trough decline of the cumulative gain curve (EUR, <= 0).

    gains must be ordered chronologically (by sell date). Returns 0.0 when the
    curve never falls below a prior peak.
    """
    try:
        equity = gains.cumsum()
        if equity.empty:
            return 0.0
        drawdown = equity - equity.cummax()
        return float(min(drawdown.min(), 0.0))
    except Exception:
        return 0.0


def _split_closed_open(df: pd.DataFrame):
    """Split trades into (closed, open). Open positions are those without a sell
    volume (still held); they carry a mark-to-market 'gain' from the simulation
    post-processing. Falls back to sellDate when sellVolume is unavailable."""
    if 'sellVolume' in df.columns:
        open_mask = df['sellVolume'].isna()
    elif 'sellDate' in df.columns:
        open_mask = df['sellDate'].isna()
    else:
        open_mask = pd.Series(False, index=df.index)
    return df[~open_mask], df[open_mask]


def _kpi_set(sub: pd.DataFrame) -> dict:
    """Compute the full KPI bundle from a set of trades (each row needs a 'gain').

    sub should be pre-sorted by sell date so the win/loss streaks are chronological.
    The same formula is used for realised (closed only) and potential
    (closed + open, mark-to-market) so both are directly comparable.
    """
    n = len(sub)
    if n == 0:
        return dict(trades=0, win_rate=0.0, profit_factor=float('inf'),
                    avg_gain=0.0, total_gain=0.0, max_dd=0.0,
                    win_streak=0, loss_streak=0)
    g = sub['gain']
    wins   = g[g > 0]
    losses = g[g <= 0]
    loss_sum = abs(losses.sum())
    return dict(
        trades=n,
        win_rate=round(len(wins) / n * 100, 1),
        profit_factor=(round(wins.sum() / loss_sum, 2) if loss_sum > 0 else float('inf')),
        avg_gain=round(g.mean(), 2),
        total_gain=round(g.sum(), 2),
        max_dd=round(_max_drawdown(g), 2),
        win_streak=_max_streak(g, positive=True),
        loss_streak=_max_streak(g, positive=False),
    )


def _render_kpi_row(container, kpi: dict, gain_label: str, system_currency: str,
                    gain_delta: 'str | None' = None, dd_base: 'float | None' = None):
    """Render one KPI row (profit factor, win rate, avg gain, total gain,
    max drawdown, streaks).

    dd_base: capital base (budget) for expressing the max drawdown as a percent.
    Falls back to an absolute EUR value when no base is available.
    """
    pf = kpi['profit_factor']
    pf_str = f"{pf}" if pf != float('inf') else "∞"
    if dd_base and dd_base > 0:
        dd_str = f"{kpi['max_dd'] / dd_base * 100:.1f} %"
    else:
        dd_str = f"{kpi['max_dd']:,.0f} {system_currency}"
    cols = container.columns(7)
    cols[0].metric(t('banner.kpi_profit_factor'), pf_str)
    cols[1].metric(t('banner.kpi_win_rate'),      f"{kpi['win_rate']} %")
    cols[2].metric(t('banner.kpi_avg_gain'),      f"{kpi['avg_gain']:+,.2f}")
    cols[3].metric(gain_label,                    f"{kpi['total_gain']:+,.2f}",
                   delta=(gain_delta if gain_delta is not None
                          else f"{kpi['total_gain']:+,.0f} {system_currency}"))
    cols[4].metric(t('banner.kpi_max_dd'),        dd_str)
    cols[5].metric(t('banner.kpi_win_streak'),    f"{kpi['win_streak']} {t('banner.kpi_trades_unit')}")
    cols[6].metric(t('banner.kpi_loss_streak'),   f"{kpi['loss_streak']} {t('banner.kpi_trades_unit')}")


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


def render_strategy_analysis(df: pd.DataFrame, region=None, system_currency: str = '',
                             budgets: 'dict | None' = None):
    """KPI block grouped by Strategy. budgets ({strategy: budget_eur}) expresses
    the max drawdown as a percent of capital."""
    if region is None:
        region = st
    if df.empty or 'Strategy' not in df.columns:
        return
    budgets = budgets or {}

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

        # Realised KPIs (closed trades only) and potential KPIs (closed + open,
        # open positions valued mark-to-market) — identical formula via _kpi_set.
        s_all_sorted = s_all.copy()
        try:
            s_all_sorted['_sd'] = pd.to_datetime(s_all_sorted['sellDate'], errors='coerce')
            s_all_sorted = s_all_sorted.sort_values('_sd')
        except Exception:
            pass

        real_kpi = _kpi_set(s_closed)
        pot_kpi  = _kpi_set(s_all_sorted)
        unrealised_gain = round(pot_kpi['total_gain'] - real_kpi['total_gain'], 2)

        win_rate      = real_kpi['win_rate']
        profit_factor = real_kpi['profit_factor']

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

            st.metric(t('banner.kpi_open_pos'), open_pos)
            st.markdown("")

            _dd_base = budgets.get(strategy)

            st.markdown(f"**📌 {t('banner.kpi_section_realised')}**")
            _render_kpi_row(st, real_kpi, t('banner.kpi_total_gain'), system_currency,
                            dd_base=_dd_base)
            st.markdown("")

            st.markdown(f"**🔮 {t('banner.kpi_section_potential')}**")
            _render_kpi_row(
                st, pot_kpi, t('banner.kpi_potential_gain'), system_currency,
                gain_delta=f"{unrealised_gain:+,.0f} {system_currency} {t('banner.kpi_unrealised_gain')}",
                dd_base=_dd_base,
            )
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


def compute_buy_hold_benchmark(df: pd.DataFrame, budgets: dict, price_lookup,
                               eval_date=None) -> list:
    """Compare each strategy against a buy-and-hold blend of the indices it trades.

    df          : trades (buyDate, sellDate, buyValueEUR, gain, sellVolume, Strategy, stockIndex)
    budgets     : {strategy: {index: invest_eur}} — per-index capital budget
    price_lookup: callable(index, date, mode) -> close|None, mode in {'after','before'}
    eval_date   : valuation date for the buy-and-hold leg (defaults to today)

    Returns one dict per strategy with realised/potential P&L, capital exposure
    (avg capital-days deployed / period), buy-and-hold blend P&L and the edge.
    The buy-and-hold leg invests each index's budget at the strategy's first buy
    and marks it to eval_date — i.e. 100 % exposure for the whole window, which is
    the honest yardstick for an exposure-minimising strategy.
    """
    if eval_date is None:
        eval_date = pd.Timestamp.now().normalize()
    else:
        eval_date = pd.Timestamp(eval_date)

    df = df.copy()
    df['_bd'] = pd.to_datetime(df['buyDate'], errors='coerce')
    df['_sd'] = pd.to_datetime(df['sellDate'], errors='coerce')

    rows = []
    for strat in sorted(df['Strategy'].dropna().unique()):
        per_idx = budgets.get(strat, {}) or {}
        budget = sum(per_idx.values())
        g = df[df['Strategy'] == strat]
        if g.empty or budget <= 0:
            continue
        start = g['_bd'].min()
        if pd.isna(start):
            continue
        period_days = max((eval_date - start).days, 1)

        if 'sellVolume' in g.columns:
            realised = float(g[g['sellVolume'].notna()]['gain'].sum())
        else:
            realised = float(g['gain'].sum())
        potential = float(g['gain'].sum())

        # Capital exposure: average EUR tied up = capital-days / period days.
        hold = (g['_sd'].fillna(eval_date) - g['_bd']).dt.days.clip(lower=0)
        bv   = g['buyValueEUR'].abs()
        avg_cap = float((bv * hold).sum()) / period_days

        # Time in market: union of all open intervals / period.
        intervals = sorted((s, e if pd.notna(e) else eval_date)
                           for s, e in zip(g['_bd'], g['_sd']) if pd.notna(s))
        merged = []
        for s, e in intervals:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        tim_days = sum((e - s).days for s, e in merged)

        # Buy-and-hold blend: each index's budget held from start to eval_date.
        bh_gain = 0.0
        bh_cov  = 0.0
        for idx, inv in per_idx.items():
            c0 = price_lookup(idx, start, 'after')
            c1 = price_lookup(idx, eval_date, 'before')
            if c0 and c1 and c0 > 0:
                bh_gain += inv * (c1 / c0 - 1.0)
                bh_cov  += inv

        rows.append(dict(
            strategy=strat,
            budget=budget,
            start=start,
            eval_date=eval_date,
            period_days=period_days,
            realised=round(realised, 2),
            potential=round(potential, 2),
            avg_cap=round(avg_cap, 2),
            exposure_pct=round(100 * avg_cap / budget, 1) if budget else 0.0,
            time_in_market_pct=round(100 * tim_days / period_days, 1),
            bh_gain=round(bh_gain, 2),
            bh_pct=round(100 * bh_gain / budget, 1) if budget else 0.0,
            strat_pct=round(100 * realised / budget, 1) if budget else 0.0,
            pot_pct=round(100 * potential / budget, 1) if budget else 0.0,
            roc_pct=round(100 * realised / avg_cap, 1) if avg_cap > 0 else 0.0,
            roc_pot_pct=round(100 * potential / avg_cap, 1) if avg_cap > 0 else 0.0,
            edge=round(realised - bh_gain, 2),
            edge_pot=round(potential - bh_gain, 2),
            bh_coverage=round(100 * bh_cov / budget, 0) if budget else 0.0,
        ))
    return rows


def render_buy_hold_benchmark(df: pd.DataFrame, budgets: dict, price_lookup,
                              region=None, system_currency: str = '', eval_date=None):
    """Render the buy-and-hold benchmark table (one row per strategy)."""
    if region is None:
        region = st
    if df.empty or 'Strategy' not in df.columns or not budgets:
        return
    try:
        rows = compute_buy_hold_benchmark(df, budgets, price_lookup, eval_date)
    except Exception:
        logger.exception("buy_hold_benchmark failed")
        return
    if not rows:
        return

    region.divider()
    region.markdown(f"## {t('banner.bh_header')}")
    region.caption(t('banner.bh_caption'))

    cur = system_currency
    table = []
    for r in rows:
        table.append({
            t('banner.bh_col_strategy'):  r['strategy'],
            t('banner.bh_col_exposure'):  f"{r['exposure_pct']:.0f} %",
            t('banner.bh_col_strat_real'): f"{r['realised']:+,.0f} {cur} ({r['strat_pct']:+.1f} %)",
            t('banner.bh_col_strat_pot'):  f"{r['potential']:+,.0f} {cur} ({r['pot_pct']:+.1f} %)",
            t('banner.bh_col_bh'):         f"{r['bh_gain']:+,.0f} {cur} ({r['bh_pct']:+.1f} %)",
            t('banner.bh_col_edge'):       f"{r['edge']:+,.0f} {cur}",
            t('banner.bh_col_edge_pot'):   f"{r['edge_pot']:+,.0f} {cur}",
            t('banner.bh_col_roc'):        f"{r['roc_pct']:+.0f} %",
            t('banner.bh_col_roc_pot'):    f"{r['roc_pot_pct']:+.0f} %",
        })
    region.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
    region.caption(t('banner.bh_legend'))


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


def render_compact_analysis(df: pd.DataFrame, region=None, system_currency: str = '',
                            budget: 'float | None' = None):
    """Simplified KPI block for single-strategy views (no Strategy grouping).
    budget expresses the max drawdown as a percent of capital."""
    if region is None:
        region = st
    if df.empty or 'gain' not in df.columns:
        return

    # Split into realised (closed) and potential (closed + open, mark-to-market).
    s_closed, _s_open = _split_closed_open(df)
    s_closed = s_closed.copy()
    if s_closed.empty:
        return
    try:
        s_closed['_sd'] = pd.to_datetime(s_closed['sellDate'], errors='coerce')
    except Exception:
        s_closed['_sd'] = pd.NaT

    now = pd.Timestamp.now()
    m3  = now - pd.DateOffset(months=3)
    y1  = now - pd.DateOffset(years=1)

    total_trades = len(s_closed)
    if total_trades == 0:
        return

    s_all_sorted = df.copy()
    try:
        s_all_sorted['_sd'] = pd.to_datetime(s_all_sorted['sellDate'], errors='coerce')
        s_all_sorted = s_all_sorted.sort_values('_sd')
    except Exception:
        pass

    real_kpi = _kpi_set(s_closed.sort_values('_sd'))
    pot_kpi  = _kpi_set(s_all_sorted)
    unrealised_gain = round(pot_kpi['total_gain'] - real_kpi['total_gain'], 2)

    region.divider()
    region.markdown(f"## {t('banner.strategy_analysis_header')}")

    region.markdown(f"**📌 {t('banner.kpi_section_realised')}**")
    _render_kpi_row(region, real_kpi, t('banner.kpi_total_gain'), system_currency,
                    dd_base=budget)
    region.markdown("")

    region.markdown(f"**🔮 {t('banner.kpi_section_potential')}**")
    _render_kpi_row(
        region, pot_kpi, t('banner.kpi_potential_gain'), system_currency,
        gain_delta=f"{unrealised_gain:+,.0f} {system_currency} {t('banner.kpi_unrealised_gain')}",
        dd_base=budget,
    )
    region.markdown("")

    closed = s_closed
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
