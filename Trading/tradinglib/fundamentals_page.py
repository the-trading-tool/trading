"""Fundamental tab of the Asset Viewer — GuruFocus-style panels on local data.

Five blocks: headline ranks, rule-based signals, metric panels with peer
percentiles, four years of statement history, and the valuation ratios plotted
over time against their own median.

All numbers come from :mod:`tradinglib.fundamentals`; this module only renders and
owns the caching. Peer distributions are the expensive part (about two seconds for
a large sector), so they are cached per peer group rather than per ticker.

There is no fair-value or price-target block. Four fiscal years are enough to rank
a company against its peers and to show where its multiples have been; they are
not enough to say what it is worth. See the module docstring of
:mod:`tradinglib.fundamentals` for why.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tradinglib import fundamentals as fu
from tradinglib import graph_tools as gt
from tradinglib.i18n import t
from tradinglib.utils import DataUtils

# Rank colours: red → amber → green across 0–10.
_RANK_COLORS = ['#C62828', '#EF6C00', '#F9A825', '#7CB342', '#2E7D32']

# Altman Z bands (distress / grey / safe) for the strength panel caption.
_ALTMAN_DISTRESS, _ALTMAN_SAFE = 1.81, 2.99


# ── Cached data access ───────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _fx_rate(quote_currency: str, report_currency: str) -> float:
    """Units of *quote_currency* per one *report_currency*, 1.0 when equal.

    Handles the pence listings (GBp) through DataUtils.normalize_currency, so a
    London price in pence lines up with a GBP income statement.
    """
    if not quote_currency or not report_currency or quote_currency == report_currency:
        return 1.0
    try:
        return float(DataUtils.get_exchange_rate(symbol=quote_currency,
                                                 system_currency=report_currency)) or 1.0
    except Exception:
        return 1.0


@st.cache_data(ttl=1800, show_spinner=False)
def _peers(sector: str, industry: str) -> dict:
    return fu.peer_metrics(sector=sector, industry=industry)


@st.cache_data(ttl=1800, show_spinner=False)
def _payload(ticker: str, peer_scope: str) -> dict | None:
    """Everything the tab needs for *ticker*, in one cached bundle.

    ``peer_scope`` is 'industry' or 'sector' and goes into the cache key so
    switching the comparison group recomputes only what depends on it.
    """
    probe = fu.load(ticker)
    if probe is None or probe.n_years == 0:
        return None
    fx = _fx_rate(probe.currency, probe.report_currency)
    fund = fu.load(ticker, fx=fx)
    if fund is None:
        return None

    val_hist = fu.valuation_history(ticker, fund, fx=fx)
    peers = _peers(fund.sector or '', fund.industry if peer_scope == 'industry' else '')
    warn, good = fu.signals(fund, val_hist)

    return {
        'metrics': fund.metrics(),
        'piotroski': fund.piotroski(),
        'history': fu.history(fund),
        'val_hist': val_hist,
        'peers': peers,
        'ranks': {g: fu.group_rank(fund.metrics(), peers, g) for g in fu.RANK_GROUPS},
        'warnings': warn,
        'positives': good,
        'sector': fund.sector,
        'industry': fund.industry,
        'currency': fund.currency,
        'report_currency': fund.report_currency,
        'timestamp': fund.flat.get('timestamp'),
        'price': fund.price(),
        'years': [y.date().isoformat() for y in fund.years],
        'fx_missing': fund.currency != fund.report_currency and not fund.fx,
    }


# ── Formatting ───────────────────────────────────────────────────────────────
def _fmt(value, unit: str) -> str:
    """Render a metric value for display; empty string for missing values."""
    if value is None or (isinstance(value, float) and value != value):
        return ''
    if unit == '%':
        return f'{value:,.1f} %'
    if unit == 'd':
        return f'{value:,.0f}'
    if unit == 'score':
        return f'{value:,.0f}'
    return f'{value:,.2f}'


def _rank_color(rank) -> str:
    if rank is None:
        return '#9E9E9E'
    return _RANK_COLORS[min(int(rank / 2.5), len(_RANK_COLORS) - 1)]


# ── Blocks ───────────────────────────────────────────────────────────────────
def _render_header(data: dict, region) -> None:
    """Headline ranks plus the provenance line (peer group, data age, currency)."""
    cols = region.columns(len(fu.RANK_GROUPS) + 1)
    for col, group in zip(cols, fu.RANK_GROUPS):
        rank = data['ranks'].get(group)
        shown = '–' if rank is None else f'{rank:.1f}'
        col.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:0.8rem;opacity:0.7'>{t(f'fund.rank_{group}')}</div>"
            f"<div style='font-size:1.9rem;font-weight:600;color:{_rank_color(rank)}'>"
            f"{shown}<span style='font-size:0.9rem;opacity:0.6'> / 10</span></div>"
            f"</div>", unsafe_allow_html=True)

    price = data['price']
    cols[-1].markdown(
        f"<div style='text-align:center'>"
        f"<div style='font-size:0.8rem;opacity:0.7'>{t('fund.price')}</div>"
        f"<div style='font-size:1.9rem;font-weight:600'>"
        f"{price:,.2f}<span style='font-size:0.9rem;opacity:0.6'> {data['currency']}</span>"
        f"</div></div>" if price else '', unsafe_allow_html=True)

    peer_group = data['industry'] or data['sector'] or '—'
    n_peers = max((len(v) for v in data['peers'].values()), default=0)
    stamp = str(data['timestamp'] or '')[:10]
    region.caption(t('fund.provenance', peers=peer_group, n=n_peers, date=stamp,
                     years=len(data['years']), currency=data['report_currency']))
    if data['fx_missing']:
        region.warning(t('fund.fx_missing', quote=data['currency'],
                         report=data['report_currency']))


def _render_signals(data: dict, region) -> None:
    warn, good = data['warnings'], data['positives']
    if not warn and not good:
        return
    left, right = region.columns(2)
    for col, items, prefix, title in ((left, warn, 'warn', t('fund.warnings')),
                                      (right, good, 'good', t('fund.positives'))):
        col.markdown(f"**{title}**")
        if not items:
            col.caption(t('fund.none'))
            continue
        for key, params in items:
            col.markdown(f"- {t(f'fund.{prefix}_{key}', **params)}")


def _render_panel(group: str, data: dict, region) -> None:
    """One metric panel: value, peer median and the direction-adjusted percentile."""
    metrics, peers = data['metrics'], data['peers']
    rows = []
    for key, (grp, unit, higher) in fu.METRICS.items():
        if grp != group:
            continue
        value = metrics.get(key)
        if value is None:
            continue
        peer_values = peers.get(key)
        median = float(pd.Series(peer_values).median()) if peer_values is not None and len(peer_values) else None
        rows.append({
            t('fund.col_metric'): t(f'fund.m_{key}'),
            t('fund.col_value'): _fmt(value, unit),
            t('fund.col_peer_median'): _fmt(median, unit),
            t('fund.col_percentile'): fu.percentile(value, peer_values, higher),
        })
    if not rows:
        return
    region.dataframe(
        pd.DataFrame(rows), hide_index=True, use_container_width=True,
        column_config={t('fund.col_percentile'): st.column_config.ProgressColumn(
            t('fund.col_percentile'), format='%.0f', min_value=0, max_value=100,
            help=t('fund.percentile_help'))})


def _render_method(region) -> None:
    """Formula reference for every metric the tab can show.

    Rows are generated from the registry rather than hand-listed, so a metric
    added to the engine cannot silently end up undocumented.
    """
    rows = []
    for key, (group, _unit, higher) in fu.METRICS.items():
        rows.append({
            t('fund.col_group'): t(f'fund.group_{group}'),
            t('fund.col_metric'): t(f'fund.m_{key}'),
            t('fund.col_formula'): t(f'fund.f_{key}'),
            t('fund.col_source'): t(f'fund.src_{fu.METRIC_SOURCE.get(key, "stmt")}'),
            t('fund.col_better'): t('fund.better_high') if higher else t('fund.better_low'),
        })
    region.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    region.caption(t('fund.method_fallbacks'))
    region.caption(t('fund.method_sources'))


def _render_piotroski(data: dict, region) -> None:
    score, maximum, checks = data['piotroski']
    if not maximum:
        return
    known = [(k, v) for k, v in checks if v is not None]
    marks = ' '.join(f"{'✅' if passed else '❌'} {t(f'fund.pio_{key}')}" for key, passed in known)
    region.caption(t('fund.piotroski_detail', score=score, max=maximum, checks=marks))
    if maximum < 9:
        region.caption(t('fund.piotroski_partial', missing=9 - maximum))
    # State the substitution whenever a cash-flow criterion actually counted.
    if any(key in ('cfo_positive', 'accruals') for key, _ in known):
        region.caption(t('fund.piotroski_cashflow'))


# ── Charts ───────────────────────────────────────────────────────────────────
def _bar_line_chart(hist: pd.DataFrame, currency: str) -> go.Figure:
    """Revenue and net income as bars, net margin as a line on the second axis."""
    x = [str(d) for d in hist.index]
    fig = go.Figure()
    fig.add_bar(x=x, y=hist['revenue'], name=t('fund.h_revenue'), marker_color='#5b9cf6')
    fig.add_bar(x=x, y=hist['net_income'], name=t('fund.h_net_income'), marker_color='#2E7D32')
    fig.add_scatter(x=x, y=hist['net_margin'], name=t('fund.m_net_margin'),
                    yaxis='y2', mode='lines+markers', line=dict(color='#EF6C00', width=2))
    fig.update_layout(
        barmode='group', height=300, margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(title=currency), yaxis2=dict(overlaying='y', side='right', title='%'),
        legend=dict(orientation='h', y=1.15, x=0))
    return fig


def _margin_chart(hist: pd.DataFrame) -> go.Figure:
    x = [str(d) for d in hist.index]
    fig = go.Figure()
    for key, color in (('gross_margin', '#5b9cf6'), ('operating_margin', '#7CB342'),
                       ('net_margin', '#EF6C00')):
        if key in hist and hist[key].notna().any():
            fig.add_scatter(x=x, y=hist[key], name=t(f'fund.m_{key}'),
                            mode='lines+markers', line=dict(color=color, width=2))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis=dict(title='%'), legend=dict(orientation='h', y=1.15, x=0))
    return fig


def _balance_chart(hist: pd.DataFrame, currency: str) -> go.Figure:
    x = [str(d) for d in hist.index]
    fig = go.Figure()
    fig.add_bar(x=x, y=hist['equity'], name=t('fund.h_equity'), marker_color='#5b9cf6')
    fig.add_bar(x=x, y=hist['total_debt'], name=t('fund.h_debt'), marker_color='#C62828')
    fig.update_layout(barmode='group', height=300, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis=dict(title=currency), legend=dict(orientation='h', y=1.15, x=0))
    return fig


def _shares_chart(hist: pd.DataFrame) -> go.Figure:
    """Share count — buybacks and dilution are invisible in per-share figures alone."""
    x = [str(d) for d in hist.index]
    fig = go.Figure()
    fig.add_bar(x=x, y=hist['shares'], name=t('fund.h_shares'), marker_color='#9E9E9E')
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation='h', y=1.15, x=0))
    return fig


def _band_chart(series: pd.Series, label: str) -> go.Figure:
    """One valuation ratio over time with its median — the 'vs own history' view."""
    s = series.dropna()
    fig = go.Figure()
    fig.add_scatter(x=s.index, y=s, name=label, mode='lines', line=dict(color='#5b9cf6', width=2))
    median = float(s.median())
    fig.add_hline(y=median, line_dash='dash', line_color='#9E9E9E',
                  annotation_text=f'{t("fund.median")} {median:.1f}', annotation_position='top left')
    fig.update_layout(height=240, margin=dict(l=10, r=10, t=30, b=10), showlegend=False,
                      title=dict(text=label, font=dict(size=13)))
    return fig


def _plot(region, fig) -> None:
    region.plotly_chart(fig, use_container_width=True, theme='streamlit',
                        config=getattr(gt, 'chart_config', None) or {})


# ── Entry point ──────────────────────────────────────────────────────────────
def render(ticker: str, region=st) -> None:
    """Render the Fundamental tab for *ticker* into *region*."""
    scope_key = f'_fund_scope_{ticker}'
    scope = region.radio(
        t('fund.peer_scope'), options=['industry', 'sector'],
        format_func=lambda v: t(f'fund.scope_{v}'), horizontal=True,
        key=scope_key, help=t('fund.peer_scope_help'))

    with region.container():
        with st.spinner(t('fund.loading')):
            data = _payload(ticker, scope)

    if data is None:
        region.info(t('fund.no_data', ticker=ticker))
        return

    _render_header(data, region)
    region.divider()
    _render_signals(data, region)
    region.divider()

    # Metric panels — one expander per group so the tab opens compact.
    for group in fu.GROUPS:
        with region.expander(t(f'fund.group_{group}'), expanded=group in ('strength', 'value')):
            _render_panel(group, data, st)
            if group == 'strength':
                _render_piotroski(data, st)
                z = data['metrics'].get('altman_z')
                if z is not None:
                    band = ('distress' if z < _ALTMAN_DISTRESS
                            else 'safe' if z > _ALTMAN_SAFE else 'grey')
                    st.caption(t('fund.altman_band', z=round(z, 2), band=t(f'fund.altman_{band}')))

    hist = data['history']
    if not hist.empty:
        region.divider()
        region.markdown(f"**{t('fund.history_header', years=len(hist))}**")
        left, right = region.columns(2)
        _plot(left, _bar_line_chart(hist, data['report_currency']))
        _plot(right, _margin_chart(hist))
        left2, right2 = region.columns(2)
        _plot(left2, _balance_chart(hist, data['report_currency']))
        _plot(right2, _shares_chart(hist))

    val_hist = data['val_hist']
    if val_hist is not None and not val_hist.empty:
        region.divider()
        region.markdown(f"**{t('fund.bands_header')}**")
        cols = region.columns(3)
        for col, (key, label) in zip(cols, (('pe', t('fund.m_pe')), ('ps', t('fund.m_ps')),
                                            ('pb', t('fund.m_pb')))):
            if val_hist[key].notna().any():
                _plot(col, _band_chart(val_hist[key], label))
        region.caption(t('fund.bands_note'))

    region.divider()
    with region.expander(t('fund.method_header'), expanded=False):
        _render_method(st)

    region.caption(t('fund.disclaimer'))
