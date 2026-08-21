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
def _payload(ticker: str) -> dict | None:
    """Everything the tab needs for *ticker*, in one cached bundle.

    Both peer distributions are computed, industry and sector, so the panels can
    show them side by side. The two are cached separately by _peers, and the
    sector one is shared by every ticker in that sector, so the second lookup is
    usually already warm.
    """
    probe = fu.load(ticker)
    if probe is None or probe.n_years == 0:
        return None
    fx = _fx_rate(probe.currency, probe.report_currency)
    fund = fu.load(ticker, fx=fx)
    if fund is None:
        return None

    metrics = fund.metrics()
    val_hist = fu.valuation_history(ticker, fund, fx=fx)
    peers_sector = _peers(fund.sector or '', '')
    peers_industry = _peers('', fund.industry or '') if fund.industry else {}
    warn, good = fu.signals(fund, val_hist)

    # Narrow industries (an insurer may have twenty peers) leave a lot of industry
    # cells empty. Count them so the UI can say why rather than look broken.
    thin = sum(1 for key, (_g, _u, higher) in fu.METRICS.items()
               if metrics.get(key) is not None
               and fu.percentile(metrics[key], peers_industry.get(key), higher) is None
               and fu.percentile(metrics[key], peers_sector.get(key), higher) is not None)

    return {
        'metrics': metrics,
        'piotroski': fund.piotroski(),
        'history': fu.history(fund),
        'val_hist': val_hist,
        'peers_industry': peers_industry,
        'peers_sector': peers_sector,
        'ranks': {g: {'industry': fu.group_rank(metrics, peers_industry, g),
                      'sector': fu.group_rank(metrics, peers_sector, g)}
                  for g in fu.RANK_GROUPS},
        'n_industry': max((len(v) for v in peers_industry.values()), default=0),
        'n_sector': max((len(v) for v in peers_sector.values()), default=0),
        'thin_industry': thin,
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
def _rank_pair(label: str, industry, sector) -> str:
    """One headline card showing the same rank against both peer groups.

    Seeing 8.1 in the industry next to 4.2 in the sector is the point of the tab —
    a company can lead a weak industry and still sit mid-field in its sector.
    """
    def half(value, caption):
        shown = '–' if value is None else f'{value:.1f}'
        return (f"<div style='min-width:2.6rem'>"
                f"<div style='font-size:1.55rem;font-weight:600;line-height:1.15;"
                f"color:{_rank_color(value)}'>{shown}</div>"
                f"<div style='font-size:0.66rem;opacity:0.55'>{caption}</div></div>")

    return (f"<div style='text-align:center'>"
            f"<div style='font-size:0.8rem;opacity:0.7;margin-bottom:0.15rem'>{label}</div>"
            f"<div style='display:flex;justify-content:center;gap:0.9rem;align-items:flex-start'>"
            f"{half(industry, t('fund.scope_industry'))}"
            f"<div style='opacity:0.25;font-size:1.3rem;line-height:1.15'>|</div>"
            f"{half(sector, t('fund.scope_sector'))}"
            f"</div></div>")


def _render_header(data: dict, region) -> None:
    """Headline ranks (industry vs sector) plus the provenance line."""
    cols = region.columns(len(fu.RANK_GROUPS) + 1)
    for col, group in zip(cols, fu.RANK_GROUPS):
        ranks = data['ranks'].get(group) or {}
        col.markdown(_rank_pair(t(f'fund.rank_{group}'),
                                ranks.get('industry'), ranks.get('sector')),
                     unsafe_allow_html=True)

    price = data['price']
    cols[-1].markdown(
        f"<div style='text-align:center'>"
        f"<div style='font-size:0.8rem;opacity:0.7;margin-bottom:0.15rem'>{t('fund.price')}</div>"
        f"<div style='font-size:1.55rem;font-weight:600;line-height:1.15'>"
        f"{price:,.2f}<span style='font-size:0.85rem;opacity:0.6'> {data['currency']}</span>"
        f"</div></div>" if price else '', unsafe_allow_html=True)

    stamp = str(data['timestamp'] or '')[:10]
    region.caption(t('fund.provenance',
                     industry=data['industry'] or '—', n_industry=data['n_industry'],
                     sector=data['sector'] or '—', n_sector=data['n_sector'],
                     date=stamp, years=len(data['years']),
                     currency=data['report_currency']))
    if data['thin_industry']:
        region.caption(t('fund.industry_thin', n=data['thin_industry'],
                         industry=data['industry'] or '—', peers=data['n_industry']))
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


def _median(peer_values, unit: str) -> str:
    if peer_values is None or not len(peer_values):
        return ''
    return _fmt(float(pd.Series(peer_values).median()), unit)


def _render_panel(group: str, data: dict, region) -> None:
    """One metric panel: the value, then median and percentile for both peer groups."""
    metrics = data['metrics']
    industry, sector = data['peers_industry'], data['peers_sector']
    col_pct_ind, col_pct_sec = t('fund.col_pct_industry'), t('fund.col_pct_sector')

    rows = []
    for key, (grp, unit, higher) in fu.METRICS.items():
        if grp != group:
            continue
        value = metrics.get(key)
        if value is None:
            continue
        rows.append({
            t('fund.col_metric'): t(f'fund.m_{key}'),
            t('fund.col_value'): _fmt(value, unit),
            t('fund.col_median_industry'): _median(industry.get(key), unit),
            col_pct_ind: fu.percentile(value, industry.get(key), higher),
            t('fund.col_median_sector'): _median(sector.get(key), unit),
            col_pct_sec: fu.percentile(value, sector.get(key), higher),
        })
    if not rows:
        return

    def bar(label):
        return st.column_config.ProgressColumn(label, format='%.0f', min_value=0,
                                               max_value=100, help=t('fund.percentile_help'))

    region.dataframe(
        pd.DataFrame(rows), hide_index=True, use_container_width=True,
        column_config={col_pct_ind: bar(col_pct_ind), col_pct_sec: bar(col_pct_sec)})


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
def _year_labels(hist: pd.DataFrame) -> list[str]:
    """Fiscal-year labels for the x axis of the history charts.

    The index holds fiscal-year end dates. Handed to Plotly as ISO strings they
    become a date axis, which draws half-year ticks ("Jul 2024") between four
    annual bars and reads as if data were missing in between. Labelling by the
    year the fiscal year ended in is what a reader expects — unless two ends fall
    in the same calendar year, where the full date is needed to tell them apart.
    """
    years = [str(d.year) for d in hist.index]
    return years if len(set(years)) == len(years) else [str(d) for d in hist.index]


def _finish(fig: go.Figure, title: str, height: int = 320, category_x: bool = False,
            **layout) -> go.Figure:
    """Common chart chrome: a left-aligned title, legend below the plot.

    Every chart carries its own title — a reader landing on one of four small
    panels cannot be expected to infer what it shows from the colours. The legend
    sits *below* the plot because the Plotly modebar is permanently on
    (graph_tools.chart_config) and occupies the top-right corner, where a
    horizontal legend would run straight into it.
    """
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=44, b=10),
        title=dict(text=title, x=0, xanchor='left', font=dict(size=14)),
        legend=dict(orientation='h', yanchor='top', y=-0.13, x=0),
        **layout)
    # The margins above are a floor, not a budget: axis titles and tick labels
    # ("4B", "EUR") are wider than 10 px and would be clipped at narrow container
    # widths. automargin lets Plotly claim the room it actually needs.
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    if category_x:
        fig.update_xaxes(type='category')
    return fig


def _bar_line_chart(hist: pd.DataFrame, currency: str) -> go.Figure:
    """Revenue and net income as bars, net margin as a line on the second axis."""
    x = _year_labels(hist)
    fig = go.Figure()
    fig.add_bar(x=x, y=hist['revenue'], name=t('fund.h_revenue'), marker_color='#5b9cf6')
    fig.add_bar(x=x, y=hist['net_income'], name=t('fund.h_net_income'), marker_color='#2E7D32')
    fig.add_scatter(x=x, y=hist['net_margin'], name=t('fund.m_net_margin'),
                    yaxis='y2', mode='lines+markers', line=dict(color='#EF6C00', width=2))
    return _finish(fig, t('fund.h_title_income'), barmode='group', category_x=True,
                   yaxis=dict(title=currency),
                   yaxis2=dict(overlaying='y', side='right', title='%'))


def _margin_chart(hist: pd.DataFrame) -> go.Figure:
    x = _year_labels(hist)
    fig = go.Figure()
    for key, color in (('gross_margin', '#5b9cf6'), ('operating_margin', '#7CB342'),
                       ('net_margin', '#EF6C00')):
        if key in hist and hist[key].notna().any():
            fig.add_scatter(x=x, y=hist[key], name=t(f'fund.m_{key}'),
                            mode='lines+markers', line=dict(color=color, width=2))
    return _finish(fig, t('fund.h_title_margins'), category_x=True, yaxis=dict(title='%'))


def _balance_chart(hist: pd.DataFrame, currency: str) -> go.Figure:
    x = _year_labels(hist)
    fig = go.Figure()
    fig.add_bar(x=x, y=hist['equity'], name=t('fund.h_equity'), marker_color='#5b9cf6')
    fig.add_bar(x=x, y=hist['total_debt'], name=t('fund.h_debt'), marker_color='#C62828')
    return _finish(fig, t('fund.h_title_balance'), barmode='group', category_x=True,
                   yaxis=dict(title=currency))


def _shares_chart(hist: pd.DataFrame) -> go.Figure:
    """Share count — buybacks and dilution are invisible in per-share figures alone.

    Two things made this chart useless before: single-series figures hide the
    Plotly legend by default, so it rendered as unlabelled grey bars, and a share
    count that barely moves gives four bars of identical height. The title fixes
    the first; printing the value on each bar fixes the second — four bars reading
    "176M" say "no dilution" at a glance, which a zero-based axis cannot.
    """
    x = _year_labels(hist)
    fig = go.Figure()
    fig.add_bar(x=x, y=hist['shares'], name=t('fund.h_shares'), marker_color='#78909C',
                texttemplate='%{y:.3s}', textposition='outside', cliponaxis=False,
                hovertemplate='%{x}<br>%{y:,.0f}<extra></extra>')
    fig = _finish(fig, t('fund.h_title_shares'), showlegend=False, category_x=True)
    # Headroom so the outside labels are not cut off by the plot frame.
    fig.update_yaxes(rangemode='tozero')
    values = pd.to_numeric(hist['shares'], errors='coerce').dropna()
    if not values.empty:
        fig.update_yaxes(range=[0, float(values.max()) * 1.18])
    return fig


def _band_chart(series: pd.Series, label: str) -> go.Figure:
    """One valuation ratio over time with its median — the 'vs own history' view."""
    s = series.dropna()
    fig = go.Figure()
    fig.add_scatter(x=s.index, y=s, name=label, mode='lines', line=dict(color='#5b9cf6', width=2))
    median = float(s.median())
    fig.add_hline(y=median, line_dash='dash', line_color='#9E9E9E',
                  annotation_text=f'{t("fund.median")} {median:.1f}',
                  annotation_position='bottom left')
    return _finish(fig, label, height=260, showlegend=False)


def _plot(region, fig) -> None:
    region.plotly_chart(fig, use_container_width=True, theme='streamlit',
                        config=getattr(gt, 'chart_config', None) or {})


# ── Entry point ──────────────────────────────────────────────────────────────
def render(ticker: str, region=st) -> None:
    """Render the Fundamental tab for *ticker* into *region*."""
    with region.container():
        with st.spinner(t('fund.loading')):
            data = _payload(ticker)

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
