"""Regime-Strömung — Capital-flow shift analysis across Markov Regime states.

Data is computed on-the-fly from local yf_*.db OHLCV files (no dependency on
asset_simulation_all.db).  Charts aggregate to sector level for readability.

Visualisations
--------------
1. Tidenwasser   Stacked-area: % of assets in Bull / Bear / Sideways over time
2. Sektoren      Sankey: sectors → current regime (weighted by volume score)
3. Migration     Scatter: sector bubbles with arrows from prev → now period
4. Flow-Tabelle  Per-asset flow score with sector column
"""
import logging

import numpy as np
import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
import streamlit as st

from tradinglib.i18n import t
from tradinglib.regime_data_engine import (
    compute_breadth_stress,
    compute_market_stress,
    compute_regimes,
    load_index_members,
    load_index_names,
    load_sector_map,
)

logger = logging.getLogger(__name__)

# ── Palette ───────────────────────────────────────────────────────────────────

_REGIME_NAME  = {1: 'Bull', 2: 'Bear', 0: 'Sideways'}
_REGIME_SOLID = {1: 'rgba(132,187,161,0.85)', 2: 'rgba(197,127,134,0.85)', 0: 'rgba(164,171,183,0.70)'}
_REGIME_FILL  = {1: 'rgba(132,187,161,0.40)', 2: 'rgba(197,127,134,0.40)', 0: 'rgba(164,171,183,0.22)'}
_REGIME_HEX   = {1: '#84BBA1', 2: '#C57F86', 0: '#A4ABB7'}

_INTERVALS     = ['1wk', '1mo', '6mo', '1y', '2y']
_INTERVAL_DAYS = {'1wk': 7, '1mo': 30, '6mo': 183, '1y': 365, '2y': 730}

_DARK = 'rgba(0,0,0,0)'
_SECTOR_PALETTE = pc.qualitative.Plotly  # 10 distinct colors


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f'rgba({r},{g},{b},{alpha})'


def _sector_color(sector: str, sector_list: list) -> str:
    try:
        idx = sector_list.index(sector) % len(_SECTOR_PALETTE)
        return _SECTOR_PALETTE[idx]
    except ValueError:
        return '#888888'


# ── Shared early-warning banner ───────────────────────────────────────────────

# Traffic-light palettes: (border/accent color, icon).
_STRESS_PALETTE = {
    'warning':  ('#C57F86', '🔴'),
    'elevated': ('#E0A458', '🟡'),
    'calm':     ('#84BBA1', '🟢'),
    'subdued':  ('#A4ABB7', '⚪'),   # bearish-but-stable / washed out, no turn
}
_STRESS_LEVEL_KEY = {
    'warning':  'main.stress_level_warning',
    'elevated': 'main.stress_level_elevated',
    'calm':     'main.stress_level_calm',
}
_STRESS_COMP_KEYS = {
    'bull_drop':  'main.stress_comp_bull_drop',
    'bear_rise':  'main.stress_comp_bear_rise',
    'side_rise':  'main.stress_comp_side_rise',
    'relvol':     'main.stress_comp_relvol',
    'divergence': 'main.stress_comp_divergence',
}
# Recovery / breadth-thrust (bottom-turn counterpart). Distinct icons so the
# green recovery light can't be confused with the green "calm" state.
_RECOVERY_PALETTE = {
    'turning':  ('#3C9D6E', '📈'),
    'building': ('#7CB89A', '🌱'),
}
_RECOVERY_LEVEL_KEY = {
    'turning':  'main.recovery_level_turning',
    'building': 'main.recovery_level_building',
}
_RECOVERY_COMP_KEYS = {
    'bull_rise':  'main.recovery_comp_bull_rise',
    'bear_drop':  'main.recovery_comp_bear_drop',
    'relvol':     'main.recovery_comp_relvol',
    'washed_out': 'main.recovery_comp_washed_out',
}


def _top_drivers(components: dict, key_map: dict, threshold: float = 15.0,
                 n: int = 3) -> list:
    """Translated labels of the n strongest contributing components (≥ threshold)."""
    return [t(key_map[k]) for k, v in
            sorted(components.items(), key=lambda kv: kv[1], reverse=True)
            if k in key_map and v >= threshold][:n]


def _render_stress_box(region, color, icon, headline, detail, why_line, disclaimer):
    """Shared coloured banner markup."""
    region.markdown(
        f'<div style="border-left:6px solid {color};background:{color}1f;'
        f'padding:0.7rem 1rem;border-radius:6px;margin:0.4rem 0;">'
        f'<div style="font-size:1.05rem;font-weight:600;">{headline}</div>'
        f'<div style="font-size:0.9rem;opacity:0.92;margin-top:0.25rem;">{detail}</div>'
        f'<div style="font-size:0.9rem;opacity:0.92;margin-top:0.15rem;">{why_line}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    region.caption(disclaimer)


def render_market_stress_banner(stress: dict, index_name: str, region=st) -> None:
    """Prominent breadth traffic-light banner. Shared by the Asset Viewer (below
    the chart) and the Regime Flow page (below the summary metrics).

    Picks ONE coherent banner by priority:
      1. active deterioration  → red/amber early-warning box
      2. else breadth turning  → green recovery / thrust box (bottom-turn)
      3. else healthy breadth  → green "market calm" box (Bull > Bear)
      4. else                  → grey "subdued / washed out" box (Bear ≥ Bull):
                                 a low score on a fallen market is NOT reassuring,
                                 so it must not show the green calm colour.
    Renders nothing for an empty dict.
    """
    if not stress:
        return

    idx_label = str(index_name).lstrip('^') or str(index_name)
    detail = t('main.stress_detail', bull=stress['bull'], bear=stress['bear'],
               side=stress['side'], n=stress['n'], as_of=stress['as_of'])

    s_level   = stress.get('level')
    rec_level = stress.get('recovery_level', 'none')

    # 1. Active deterioration warning takes precedence.
    if s_level in ('warning', 'elevated'):
        color, icon = _STRESS_PALETTE[s_level]
        headline = t('main.stress_headline', icon=icon,
                     level=t(_STRESS_LEVEL_KEY[s_level]),
                     index=idx_label, score=stress['score'])
        drivers = _top_drivers(stress.get('components', {}), _STRESS_COMP_KEYS)
        why = ', '.join(drivers) if drivers else t('main.stress_no_drivers')
        why_line = t('main.stress_why', drivers=why)
        if stress.get('divergence'):
            why_line += ' ' + t('main.stress_divergence_note', change=stress['index_change'])
        _render_stress_box(region, color, icon, headline, detail, why_line,
                           t('main.stress_disclaimer'))

    # 2. Breadth turning up from a washed-out base → green recovery light.
    elif rec_level in ('turning', 'building'):
        color, icon = _RECOVERY_PALETTE[rec_level]
        headline = t('main.recovery_headline', icon=icon,
                     level=t(_RECOVERY_LEVEL_KEY[rec_level]),
                     index=idx_label, score=stress['recovery_score'])
        drivers = _top_drivers(stress.get('recovery_components', {}), _RECOVERY_COMP_KEYS)
        why = ', '.join(drivers) if drivers else t('main.recovery_no_drivers')
        why_line = t('main.stress_why', drivers=why)
        _render_stress_box(region, color, icon, headline, detail, why_line,
                           t('main.recovery_disclaimer'))

    # 3./4. Low stress + no recovery → split by breadth level so a deeply
    #       bearish (just-fallen) market is NOT painted reassuring green.
    else:
        if stress.get('bull', 0.0) > stress.get('bear', 0.0):
            color, icon = _STRESS_PALETTE['calm']      # healthy → green
            level_label = t(_STRESS_LEVEL_KEY['calm'])
            why_line = t('main.stress_why', drivers=t('main.stress_no_drivers'))
            disclaimer = t('main.stress_disclaimer')
        else:
            color, icon = _STRESS_PALETTE['subdued']   # washed out → grey
            level_label = t('main.stress_level_subdued')
            why_line = t('main.stress_subdued_why')
            disclaimer = t('main.stress_subdued_disclaimer')
        headline = t('main.stress_headline', icon=icon, level=level_label,
                     index=idx_label, score=stress['score'])
        _render_stress_box(region, color, icon, headline, detail, why_line,
                           disclaimer)


# ── Data preparation ──────────────────────────────────────────────────────────

def _split_periods(df_all: pd.DataFrame, interval_days: int):
    """Split df_all into (df_now, df_prev) equal-length windows."""
    cutoff = pd.Timestamp.today() - pd.Timedelta(days=interval_days)
    prev_start = cutoff - pd.Timedelta(days=interval_days)
    df_now  = df_all[df_all['Date'] >= cutoff].copy()
    df_prev = df_all[(df_all['Date'] >= prev_start) & (df_all['Date'] < cutoff)].copy()
    return df_now, df_prev


def _latest_per_ticker(df: pd.DataFrame) -> pd.DataFrame:
    """Return the most recent row per ticker. Safe against empty / column-less DataFrames."""
    if df.empty or 'Date' not in df.columns or 'ticker' not in df.columns:
        return pd.DataFrame(columns=['ticker', 'Date', 'regime', 'relvol_ratio', 'ewo'])
    return df.sort_values('Date').groupby('ticker').last().reset_index()


def _sector_snapshot(df_period: pd.DataFrame, sector_map: pd.DataFrame) -> pd.DataFrame:
    """Per-sector: count of assets in each regime + avg relvol + flow score."""
    if df_period.empty or 'Date' not in df_period.columns:
        return pd.DataFrame()
    latest = _latest_per_ticker(df_period)
    if latest.empty:
        return pd.DataFrame()
    merged = latest.merge(sector_map, on='ticker', how='left')
    merged['sector_group'] = merged['sector_group'].fillna('Sonstige')
    merged['longName']     = merged['longName'].fillna(merged['ticker'])

    # flow score per asset
    rd = merged['regime'].map({1: 1.0, 2: -1.0, 0: 0.0}).fillna(0.0)
    ewo_factor = (merged['ewo'].clip(-10, 10) / 10).abs() + 1.0
    merged['flow_score'] = (rd * merged['relvol_ratio'] * ewo_factor).round(3)

    total = merged.groupby('sector_group').agg(
        total=('ticker', 'count'),
        avg_relvol=('relvol_ratio', 'mean'),
        sector_flow=('flow_score', 'mean'),
    ).reset_index()

    regime_cnt = merged.groupby(['sector_group', 'regime']).size().reset_index(name='cnt')
    pivot = regime_cnt.pivot(index='sector_group', columns='regime', values='cnt').fillna(0)
    pivot.columns = [f'regime_{int(c)}' for c in pivot.columns]
    for r in [0, 1, 2]:
        col = f'regime_{r}'
        if col not in pivot.columns:
            pivot[col] = 0.0

    result = total.merge(pivot.reset_index(), on='sector_group', how='left')
    result['bull_frac'] = result['regime_1'] / result['total'].replace(0, np.nan)
    result['bear_frac'] = result['regime_2'] / result['total'].replace(0, np.nan)
    result['side_frac'] = result['regime_0'] / result['total'].replace(0, np.nan)
    return result.fillna(0)


# ── Chart builders ────────────────────────────────────────────────────────────

def _build_tidal(df_now: pd.DataFrame) -> go.Figure:
    """Stacked-area: % of assets in each regime over the now-period."""
    if df_now.empty:
        return go.Figure()

    daily = (
        df_now.groupby(['Date', 'regime'])['ticker']
        .nunique()
        .reset_index(name='cnt')
    )
    total = df_now.groupby('Date')['ticker'].nunique().reset_index(name='total')
    merged = daily.merge(total, on='Date')
    merged['pct'] = merged['cnt'] / merged['total'] * 100

    fig = go.Figure()
    for r, label in [(2, 'Bear'), (0, 'Sideways'), (1, 'Bull')]:
        sub = merged[merged['regime'] == r].sort_values('Date')
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub['Date'], y=sub['pct'].round(1),
            name=label, mode='lines',
            stackgroup='regime',
            fillcolor=_REGIME_FILL[r],
            line=dict(color=_REGIME_SOLID[r], width=1.5),
            hovertemplate=f'<b>{label}</b>: %{{y:.1f}}%<extra></extra>',
        ))

    fig.update_layout(
        height=300, margin=dict(l=4, r=4, t=10, b=4),
        legend=dict(orientation='h', x=0.5, xanchor='center', y=1.08),
        yaxis=dict(title='% Assets', range=[0, 100], ticksuffix='%',
                   gridcolor='rgba(128,128,128,0.12)'),
        xaxis=dict(gridcolor='rgba(128,128,128,0.12)'),
        paper_bgcolor=_DARK, plot_bgcolor=_DARK, hovermode='x unified',
    )
    return fig


def _build_transition_sankey(df_now: pd.DataFrame, df_prev: pd.DataFrame,
                              sector_map: pd.DataFrame) -> go.Figure:
    """Transition Sankey: regime at START of interval → regime NOW.

    Left nodes  = Bull/Bear/Sideways at the beginning of the interval.
    Right nodes = Bull/Bear/Sideways today.
    Link width  = number of assets × mean relvol_ratio.
    Link color  = source (prev) regime colour.

    This chart changes with every interval because the prev-period snapshot
    is taken from a different calendar date.
    """
    if df_prev.empty:
        return go.Figure()

    latest_now  = _latest_per_ticker(df_now)
    latest_prev = _latest_per_ticker(df_prev)

    if latest_now.empty or latest_prev.empty:
        return go.Figure()

    merged = latest_now[['ticker', 'regime', 'relvol_ratio']].merge(
        latest_prev[['ticker', 'regime']].rename(columns={'regime': 'prev_regime'}),
        on='ticker', how='inner',
    )
    if merged.empty:
        return go.Figure()

    order = [1, 2, 0]   # Bull, Bear, Sideways
    node_labels = (
        [f'{_REGIME_NAME[r]}\n(vorher)' for r in order] +
        [f'{_REGIME_NAME[r]}\n(jetzt)'  for r in order]
    )
    node_colors = [_REGIME_HEX[r] for r in order] * 2
    idx_prev_map = {r: i   for i, r in enumerate(order)}
    idx_now_map  = {r: i+3 for i, r in enumerate(order)}

    flows = (
        merged.groupby(['prev_regime', 'regime'])['relvol_ratio']
        .agg(vol='sum', cnt='count')
        .reset_index()
    )

    sources, targets, values, link_colors, hover_texts = [], [], [], [], []
    for _, row in flows.iterrows():
        pr, nr = int(row['prev_regime']), int(row['regime'])
        if pr not in idx_prev_map or nr not in idx_now_map:
            continue
        vol = float(row['vol'])
        if vol <= 0:
            continue
        sources.append(idx_prev_map[pr])
        targets.append(idx_now_map[nr])
        values.append(vol)
        link_colors.append(_hex_to_rgba(_REGIME_HEX[pr], 0.42))
        same = '(unverändert)' if pr == nr else f'{_REGIME_NAME[pr]} → {_REGIME_NAME[nr]}'
        hover_texts.append(
            f'{same}<br>{int(row["cnt"])} Assets<br>Vol-Score: {vol:.2f}'
        )

    if not sources:
        return go.Figure()

    fig = go.Figure(go.Sankey(
        arrangement='snap',
        node=dict(
            pad=22, thickness=24,
            line=dict(color='rgba(255,255,255,0.08)', width=0.5),
            label=node_labels, color=node_colors,
            hovertemplate='<b>%{label}</b><br>Vol-Score: %{value:.2f}<extra></extra>',
        ),
        link=dict(
            source=sources, target=targets,
            value=values, color=link_colors,
            customdata=hover_texts,
            hovertemplate='%{customdata}<extra></extra>',
        ),
        textfont=dict(size=13, color='#1a1a2e'),
    ))
    fig.update_layout(
        height=420,
        margin=dict(l=100, r=100, t=10, b=10),
        paper_bgcolor=_DARK,
        font=dict(size=13, color='#1a1a2e'),
    )
    return fig


def _build_sector_mini_sankey(sector_name: str,
                               df_now: pd.DataFrame, df_prev: pd.DataFrame,
                               sector_map: pd.DataFrame) -> go.Figure:
    """One compact Sankey showing regime transitions for a single sector."""
    sm = sector_map[sector_map['sector_group'] == sector_name][['ticker']]
    dn = df_now.merge(sm, on='ticker', how='inner')
    dp = df_prev.merge(sm, on='ticker', how='inner') if not df_prev.empty else pd.DataFrame()

    if dn.empty:
        return go.Figure()

    latest_now  = _latest_per_ticker(dn)
    latest_prev = _latest_per_ticker(dp) if not dp.empty else pd.DataFrame()

    order = [1, 2, 0]
    node_labels = (
        [f'{_REGIME_NAME[r]}\n(vorher)' for r in order] +
        [f'{_REGIME_NAME[r]}\n(jetzt)'  for r in order]
    )
    node_colors = [_REGIME_HEX[r] for r in order] * 2
    idx_p = {r: i   for i, r in enumerate(order)}
    idx_n = {r: i+3 for i, r in enumerate(order)}

    if not latest_prev.empty:
        merged = latest_now[['ticker', 'regime', 'relvol_ratio']].merge(
            latest_prev[['ticker', 'regime']].rename(columns={'regime': 'prev_regime'}),
            on='ticker', how='inner',
        )
    else:
        # No prev: show only current distribution (self-links)
        merged = latest_now[['ticker', 'regime', 'relvol_ratio']].copy()
        merged['prev_regime'] = merged['regime']

    if merged.empty:
        return go.Figure()

    flows = (merged.groupby(['prev_regime', 'regime'])['relvol_ratio']
             .agg(vol='sum', cnt='count').reset_index())

    sources, targets, values, link_colors, hovers = [], [], [], [], []
    for _, row in flows.iterrows():
        pr, nr = int(row['prev_regime']), int(row['regime'])
        if pr not in idx_p or nr not in idx_n or float(row['vol']) <= 0:
            continue
        sources.append(idx_p[pr])
        targets.append(idx_n[nr])
        values.append(float(row['vol']))
        link_colors.append(_hex_to_rgba(_REGIME_HEX[pr], 0.42))
        lbl = '(gleich)' if pr == nr else f'{_REGIME_NAME[pr]}→{_REGIME_NAME[nr]}'
        hovers.append(f'{lbl}<br>{int(row["cnt"])} Assets')

    if not sources:
        return go.Figure()

    fig = go.Figure(go.Sankey(
        arrangement='snap',
        node=dict(
            pad=10, thickness=14,
            line=dict(color='rgba(255,255,255,0.06)', width=0.5),
            label=node_labels, color=node_colors,
            hovertemplate='<b>%{label}</b><extra></extra>',
        ),
        link=dict(
            source=sources, target=targets, value=values,
            color=link_colors, customdata=hovers,
            hovertemplate='%{customdata}<extra></extra>',
        ),
        textfont=dict(size=11, color='#1a1a2e'),
    ))
    fig.update_layout(
        height=210,
        margin=dict(l=70, r=70, t=24, b=4),
        paper_bgcolor=_DARK,
        font=dict(size=11, color='#1a1a2e'),
        title=dict(text=f'<b>{_shorten_label(sector_name)}</b>',
                   font=dict(size=12, color='#444'), x=0.5, xanchor='center'),
    )
    return fig


def _build_sector_shift_bars(snap_now: pd.DataFrame, snap_prev: pd.DataFrame,
                              sector_list: list) -> go.Figure:
    """Horizontal stacked-bar chart: for each sector two rows — 'vorher' and 'jetzt'.

    Gives an instant visual diff of how each sector's Bull/Bear/Sideways mix
    has changed over the selected interval.
    """
    if snap_now.empty:
        return go.Figure()

    prev_by_sg = snap_prev.set_index('sector_group') if not snap_prev.empty else pd.DataFrame()
    has_prev   = not prev_by_sg.empty

    # Build y-label pairs and corresponding % values.
    # Reverse so the first sector appears at the top of the chart.
    y_labels, bull_vals, bear_vals, side_vals, opacity_vals = [], [], [], [], []

    for sg in reversed(sector_list):
        row = snap_now[snap_now['sector_group'] == sg]
        if row.empty:
            continue
        r = row.iloc[0]
        short = _shorten_label(sg)

        # "jetzt" row (solid)
        y_labels.append(f'{short} — jetzt')
        bull_vals.append(float(r['bull_frac']) * 100)
        bear_vals.append(float(r['bear_frac']) * 100)
        side_vals.append(max(0.0, 100 - float(r['bull_frac'])*100 - float(r['bear_frac'])*100))
        opacity_vals.append(0.90)

        # "vorher" row (lighter)
        y_labels.append(f'{short} — vorher')
        if has_prev and sg in prev_by_sg.index:
            pr = prev_by_sg.loc[sg]
            bull_vals.append(float(pr['bull_frac']) * 100)
            bear_vals.append(float(pr['bear_frac']) * 100)
            side_vals.append(max(0.0, 100 - float(pr['bull_frac'])*100 - float(pr['bear_frac'])*100))
        else:
            bull_vals.append(0.0)
            bear_vals.append(0.0)
            side_vals.append(100.0)
        opacity_vals.append(0.45)

    if not y_labels:
        return go.Figure()

    def _bar(name, vals, color):
        return go.Bar(
            y=y_labels, x=vals, name=name,
            orientation='h',
            marker=dict(color=color, opacity=0.88),
            text=[f'{v:.0f}%' if v >= 6 else '' for v in vals],
            textposition='inside',
            insidetextanchor='middle',
            textfont=dict(size=10, color='white'),
            hovertemplate=f'<b>%{{y}}</b><br>{name}: %{{x:.1f}}%<extra></extra>',
        )

    fig = go.Figure([
        _bar('Sideways', side_vals, _REGIME_SOLID[0]),
        _bar('Bear',     bear_vals, _REGIME_SOLID[2]),
        _bar('Bull',     bull_vals, _REGIME_SOLID[1]),
    ])
    fig.update_layout(
        barmode='stack',
        height=max(280, len(sector_list) * 54),
        margin=dict(l=4, r=30, t=6, b=4),
        xaxis=dict(ticksuffix='%', range=[0, 100],
                   gridcolor='rgba(128,128,128,0.10)'),
        yaxis=dict(tickfont=dict(size=11)),
        paper_bgcolor=_DARK, plot_bgcolor=_DARK,
        legend=dict(orientation='h', x=0.5, xanchor='center', y=1.04,
                    traceorder='reversed'),
        showlegend=True,
        hovermode='y unified',
    )
    return fig


def _shorten_label(label: str, max_len: int = 22) -> str:
    """Shorten long sector names so they fit next to the Sankey nodes."""
    replacements = [
        ('Konsumgüter (zyklisch)',  'Konsum zyklisch'),
        ('Konsumgüter (defensiv)',  'Konsum defensiv'),
        ('Rohstoffe — Edelmetalle', 'Edelmetalle'),
        ('Rohstoffe — Energie',     'Energie (Roh.)'),
        ('Communication Services',  'Kommunikation'),
        ('Financial Services',      'Finanzen'),
    ]
    for long, short in replacements:
        label = label.replace(long, short)
    return label[:max_len] if len(label) > max_len else label


def _build_sector_sankey(snap: pd.DataFrame, sector_list: list) -> go.Figure:
    """Two-layer Sankey: sectors → regime (current state, volume-weighted)."""
    order = [1, 2, 0]
    regime_labels = [_REGIME_NAME[r] for r in order]
    n_s = len(sector_list)

    # Shorten sector labels so they fit beside the narrow nodes
    short_sectors = [_shorten_label(s) for s in sector_list]
    node_labels = short_sectors + regime_labels
    node_colors = (
        [_sector_color(s, sector_list) for s in sector_list]
        + [_REGIME_HEX[r] for r in order]
    )

    regime_idx = {r: n_s + i for i, r in enumerate(order)}

    sources, targets, values, link_colors = [], [], [], []
    for _, row in snap.iterrows():
        si = sector_list.index(row['sector_group']) if row['sector_group'] in sector_list else None
        if si is None:
            continue
        for r, col in [(1, 'regime_1'), (2, 'regime_2'), (0, 'regime_0')]:
            cnt = float(row.get(col, 0))
            if cnt <= 0:
                continue
            vol_weight = max(float(row.get('avg_relvol', 1.0)), 0.1)
            sources.append(si)
            targets.append(regime_idx[r])
            values.append(cnt * vol_weight)
            link_colors.append(_hex_to_rgba(_REGIME_HEX[r], 0.38))

    if not sources:
        return go.Figure()

    # Count chars of longest left-side label to set left margin
    max_label_px = max((len(s) for s in short_sectors), default=10) * 7 + 12

    fig = go.Figure(go.Sankey(
        arrangement='snap',
        node=dict(
            pad=20, thickness=22,
            line=dict(color='rgba(255,255,255,0.08)', width=0.5),
            label=node_labels, color=node_colors,
            hovertemplate='<b>%{label}</b><br>Vol-Score: %{value:.2f}<extra></extra>',
        ),
        link=dict(
            source=sources, target=targets,
            value=values, color=link_colors,
            hovertemplate='%{source.label} → %{target.label}<br>'
                          'Score: %{value:.2f}<extra></extra>',
        ),
        textfont=dict(size=13, color='#1a1a2e'),
    ))
    fig.update_layout(
        height=max(460, n_s * 52),
        margin=dict(l=max_label_px, r=90, t=10, b=10),
        paper_bgcolor=_DARK,
        font=dict(size=13, color='#1a1a2e'),
    )
    return fig


def _build_migration_scatter(snap_now: pd.DataFrame, snap_prev: pd.DataFrame,
                              sector_list: list) -> go.Figure:
    """Sector bubbles: X=bull_frac, Y=bear_frac. Arrows show shift over interval.

    Axes auto-zoom to the actual data range with padding; quadrant dividers
    and corner labels are placed dynamically within that range.
    """
    fig = go.Figure()
    if snap_now.empty:
        return fig

    prev_idx = snap_prev.set_index('sector_group') if not snap_prev.empty else pd.DataFrame()

    # ── Collect all plotted coordinates for auto-zoom ─────────────────────────
    all_x = list(snap_now['bull_frac'].fillna(0))
    all_y = list(snap_now['bear_frac'].fillna(0))
    if not prev_idx.empty:
        all_x += list(snap_prev['bull_frac'].fillna(0))
        all_y += list(snap_prev['bear_frac'].fillna(0))

    pad   = 0.12
    x_min = max(0.0, min(all_x) - pad)
    x_max = min(1.0, max(all_x) + pad)
    y_min = max(0.0, min(all_y) - pad)
    y_max = min(1.0, max(all_y) + pad)
    # Ensure minimum visible range
    if x_max - x_min < 0.25: x_min, x_max = max(0, x_min - 0.12), min(1, x_max + 0.12)
    if y_max - y_min < 0.25: y_min, y_max = max(0, y_min - 0.12), min(1, y_max + 0.12)

    # Dynamic quadrant dividers at median of current data
    x_mid = float(np.median(snap_now['bull_frac'].fillna(0)))
    y_mid = float(np.median(snap_now['bear_frac'].fillna(0)))
    # Clamp to visible range
    x_mid = max(x_min + 0.05, min(x_max - 0.05, x_mid))
    y_mid = max(y_min + 0.05, min(y_max - 0.05, y_mid))

    # Corner label positions (80%/20% of visible range)
    bx_hi = x_min + (x_max - x_min) * 0.82
    bx_lo = x_min + (x_max - x_min) * 0.06
    by_hi = y_min + (y_max - y_min) * 0.88
    by_lo = y_min + (y_max - y_min) * 0.05

    x_range = x_max - x_min
    y_range = y_max - y_min

    def _text_pos(bx: float, by: float) -> str:
        """Choose label anchor so it stays inside the visible area."""
        near_right = bx > x_min + x_range * 0.80
        near_left  = bx < x_min + x_range * 0.20
        near_top   = by > y_min + y_range * 0.80
        v = 'bottom' if near_top else 'top'
        h = 'left'   if near_right else ('right' if near_left else 'center')
        return f'{v} {h}'

    for _, row in snap_now.iterrows():
        sg    = row['sector_group']
        color = _sector_color(sg, sector_list)
        size  = max(float(row['total']) ** 0.5 * 7, 12)
        bx    = float(row['bull_frac'])
        by    = float(row['bear_frac'])

        # Arrow from prev position (skip when prev is at origin — likely missing data)
        if not prev_idx.empty and sg in prev_idx.index:
            pr     = prev_idx.loc[sg]
            ax_val = float(pr.get('bull_frac', bx))
            ay_val = float(pr.get('bear_frac', by))
            dx     = bx - ax_val
            dy     = by - ay_val
            if (abs(dx) > 0.01 or abs(dy) > 0.01) and not (ax_val < 0.01 and ay_val < 0.01):
                fig.add_annotation(
                    x=bx,    y=by,
                    ax=ax_val, ay=ay_val,
                    xref='x', yref='y', axref='x', ayref='y',
                    showarrow=True, arrowhead=3, arrowsize=1.0,
                    arrowwidth=2.2, arrowcolor=color, opacity=0.70,
                )

        fig.add_trace(go.Scatter(
            x=[bx], y=[by],
            mode='markers+text',
            name=sg,
            text=[sg],
            textposition=_text_pos(bx, by),
            textfont=dict(size=11, color=color),
            marker=dict(color=color, size=size,
                        line=dict(color='rgba(255,255,255,0.4)', width=1.5),
                        opacity=0.88),
            cliponaxis=False,
            hovertemplate=(
                f'<b>{sg}</b><br>Bull: %{{x:.0%}}<br>Bear: %{{y:.0%}}<br>'
                f'Assets: {int(row["total"])}<extra></extra>'
            ),
            showlegend=False,
        ))

    # Dynamic quadrant dividers
    fig.add_hline(y=y_mid, line_dash='dot', line_color='rgba(160,160,160,0.30)', line_width=1)
    fig.add_vline(x=x_mid, line_dash='dot', line_color='rgba(160,160,160,0.30)', line_width=1)

    for txt, x, y, col in [
        ('Opportunity', bx_hi, by_lo, '#84BBA1'),
        ('Gefahr',      bx_lo, by_hi, '#C57F86'),
        ('Stagnation',  bx_lo, by_lo, '#A4ABB7'),
        ('Volatil',     bx_hi, by_hi, '#f0a500'),
    ]:
        fig.add_annotation(
            x=x, y=y, xref='x', yref='y',
            text=f'<i>{txt}</i>', showarrow=False,
            font=dict(color=col, size=11), opacity=0.50,
        )

    fig.update_layout(
        height=520, margin=dict(l=60, r=110, t=40, b=60),
        xaxis=dict(title='Bull-Anteil aktuell', tickformat='.0%',
                   range=[x_min, x_max], gridcolor='rgba(128,128,128,0.10)'),
        yaxis=dict(title='Bear-Anteil aktuell', tickformat='.0%',
                   range=[y_min, y_max], gridcolor='rgba(128,128,128,0.10)'),
        paper_bgcolor=_DARK, plot_bgcolor=_DARK,
        hovermode='closest', showlegend=False,
    )
    return fig


def _build_flow_table(df_period: pd.DataFrame, sector_map: pd.DataFrame) -> pd.DataFrame:
    """Per-asset flow-score table with sector grouping."""
    latest = _latest_per_ticker(df_period)
    merged = latest.merge(sector_map, on='ticker', how='left')
    merged['sector_group'] = merged['sector_group'].fillna('Sonstige')
    merged['longName']     = merged['longName'].fillna(merged['ticker'])

    rd = merged['regime'].map({1: 1.0, 2: -1.0, 0: 0.0}).fillna(0.0)
    ewo_factor = (merged['ewo'].clip(-10, 10) / 10).abs() + 1.0
    merged['flow_score']   = (rd * merged['relvol_ratio'] * ewo_factor).round(3)
    merged['regime_label'] = merged['regime'].map({1: '▲ Bull', 2: '▼ Bear', 0: '─ Side'})
    merged['ewo_dir']      = merged['ewo'].apply(
        lambda v: '↗' if v > 0.5 else ('↘' if v < -0.5 else '→')
    )

    cols = ['sector_group', 'ticker', 'longName', 'regime_label',
            'flow_score', 'relvol_ratio', 'ewo', 'ewo_dir']
    out = merged[cols].sort_values('flow_score', ascending=False, key=abs)
    return out.rename(columns={
        'sector_group':  'Sektor',
        'ticker':        'Ticker',
        'longName':      'Name',
        'regime_label':  'Regime',
        'flow_score':    'Flow Score',
        'relvol_ratio':  'Rel.Vol.',
        'ewo':           'EWO %',
        'ewo_dir':       '↕',
    }).reset_index(drop=True)


# ── Page class ────────────────────────────────────────────────────────────────

class RegimeFlowPage:
    """Regime-Strömung: sector-level capital-flow analysis from local OHLCV."""

    def __init__(self, username=None, sys_config=None):
        self.username   = username
        self.sys_config = sys_config

    def _get_monitored(self) -> list:
        if self.sys_config is None:
            return []
        try:
            v = self.sys_config.get_value('monitored_assets', [])
            if isinstance(v, str):
                return [tck.strip() for tck in v.split(',') if tck.strip()]
            return list(v) if v else []
        except Exception:
            return []

    def render(self):
        # ── Controls row ──────────────────────────────────────────────────────
        col_src, col_int = st.columns([3, 2])

        with col_src:
            src_options = [t('flow.src_monitored'), t('flow.src_index')]
            src = st.radio(t('flow.ticker_source'), src_options,
                           horizontal=True, key='rf_src')

        interval_key = col_int.radio(
            t('flow.interval'), _INTERVALS, index=2,
            horizontal=True, key='rf_interval',
        )
        interval_days = _INTERVAL_DAYS[interval_key]

        # ── Ticker list ───────────────────────────────────────────────────────
        tickers: tuple = ()
        stress_index = None   # set only for real ^-index sources (for the banner)

        if src == t('flow.src_monitored'):
            mon = self._get_monitored()
            if mon:
                tickers = tuple(sorted(set(mon)))
            else:
                st.info(t('flow.no_monitored'))
                return

        else:  # Index
            idx_names = load_index_names()
            if not idx_names:
                st.warning(t('flow.no_indices'))
                return
            sel_idx = st.selectbox(t('flow.select_index'), idx_names,
                                   key='rf_idx_select')
            stress_index = sel_idx
            tickers = load_index_members(sel_idx)
            if not tickers:
                st.warning(t('flow.no_members', index=sel_idx))
                return

        st.caption(t('flow.loading_n', n=len(tickers)))

        # ── Load & compute ────────────────────────────────────────────────────
        with st.spinner(t('flow.loading')):
            df_all = compute_regimes(tickers, interval_days)

        if df_all.empty:
            st.warning(t('flow.no_data'))
            return

        sector_map  = load_sector_map()
        df_now, df_prev = _split_periods(df_all, interval_days)

        # ── Summary metrics ───────────────────────────────────────────────────
        latest = _latest_per_ticker(df_now if not df_now.empty else df_all)
        n_total = len(latest)
        n_bull  = int((latest['regime'] == 1).sum())
        n_bear  = int((latest['regime'] == 2).sum())
        n_side  = int((latest['regime'] == 0).sum())
        as_of   = str(latest['Date'].max())[:10] if not latest.empty else '—'

        # Delta vs previous period (percentage-point change)
        bull_d = bear_d = side_d = None
        if not df_prev.empty:
            prev_l  = _latest_per_ticker(df_prev)
            np_tot  = max(len(prev_l), 1)
            bull_d  = f'{(n_bull/max(n_total,1) - (prev_l["regime"]==1).sum()/np_tot)*100:+.1f}pp'
            bear_d  = f'{(n_bear/max(n_total,1) - (prev_l["regime"]==2).sum()/np_tot)*100:+.1f}pp'
            side_d  = f'{(n_side/max(n_total,1) - (prev_l["regime"]==0).sum()/np_tot)*100:+.1f}pp'

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric(t('flow.metric_total'), n_total)
        c2.metric('▲ Bull',     f'{n_bull/max(n_total,1)*100:.0f}% ({n_bull})',
                  bull_d, delta_color='normal')
        c3.metric('▼ Bear',     f'{n_bear/max(n_total,1)*100:.0f}% ({n_bear})',
                  bear_d, delta_color='inverse')
        c4.metric('─ Sideways', f'{n_side/max(n_total,1)*100:.0f}% ({n_side})',
                  side_d, delta_color='off')
        c5.metric(t('flow.metric_date'), as_of)

        # ── Early-warning banner ──────────────────────────────────────────────
        # Real ^-index → full score incl. price/breadth divergence.
        # Grouping / monitored assets → slimmed breadth-only score (no own
        # index price for a divergence term).
        try:
            if stress_index and str(stress_index).startswith('^'):
                stress = compute_market_stress(stress_index)
                banner_label = stress_index
            else:
                stress = compute_breadth_stress(tuple(sorted(set(tickers))))
                banner_label = stress_index or t('flow.src_monitored')
            render_market_stress_banner(stress, banner_label)
        except Exception:
            logger.debug('regime-flow stress banner skipped', exc_info=True)

        # ── Sector aggregation ────────────────────────────────────────────────
        snap_now  = _sector_snapshot(df_now  if not df_now.empty  else df_all, sector_map)
        snap_prev = _sector_snapshot(df_prev, sector_map)
        sector_list = sorted(snap_now['sector_group'].unique().tolist())

        # ── Tabs ──────────────────────────────────────────────────────────────
        tab_tidal, tab_sankey, tab_scatter, tab_table = st.tabs([
            t('flow.tab_tidal'),
            t('flow.tab_sankey'),
            t('flow.tab_scatter'),
            t('flow.tab_table'),
        ])

        with tab_tidal:
            st.caption(t('flow.tidal_caption'))
            tidal_df = df_now if not df_now.empty else df_all
            if not tidal_df.empty:
                st.plotly_chart(_build_tidal(tidal_df), use_container_width=True)
            else:
                st.info(t('flow.no_ts_data'))

        with tab_sankey:
            st.caption(t('flow.sankey_caption'))
            if not df_prev.empty:
                fig_sk = _build_transition_sankey(df_now if not df_now.empty else df_all,
                                                  df_prev, sector_map)
                if fig_sk.data:
                    st.plotly_chart(fig_sk, use_container_width=True)
                else:
                    st.info(t('flow.no_transitions'))

                # ── Per-sector mini-Sankeis in 2-column grid ──────────────
                st.markdown('---')
                st.caption('**Regime-Verschiebung pro Sektor** — vorher → jetzt')
                df_now_used = df_now if not df_now.empty else df_all
                cols2 = st.columns(2)
                for i, sg in enumerate(sector_list):
                    mini = _build_sector_mini_sankey(sg, df_now_used, df_prev,
                                                     sector_map)
                    if mini.data:
                        with cols2[i % 2]:
                            st.plotly_chart(mini, use_container_width=True)
            else:
                # No prev data: show current-only mini-Sankeis
                st.info(t('flow.no_prev_data', interval=interval_key))
                st.caption('**Aktuelles Regime pro Sektor**')
                df_now_used = df_now if not df_now.empty else df_all
                cols2 = st.columns(2)
                for i, sg in enumerate(sector_list):
                    mini = _build_sector_mini_sankey(sg, df_now_used,
                                                     pd.DataFrame(), sector_map)
                    if mini.data:
                        with cols2[i % 2]:
                            st.plotly_chart(mini, use_container_width=True)

        with tab_scatter:
            st.caption(t('flow.scatter_caption'))
            if not snap_now.empty:
                fig_sc = _build_migration_scatter(snap_now, snap_prev, sector_list)
                st.plotly_chart(fig_sc, use_container_width=True)
                if snap_prev.empty:
                    st.caption(t('flow.no_prev_data', interval=interval_key))
            else:
                st.info(t('flow.no_data'))

        with tab_table:
            st.caption(t('flow.table_caption'))
            # Use df_all so every asset with any OHLCV data appears,
            # not just those with data within the current interval window.
            tbl = _build_flow_table(df_all, sector_map)
            st.dataframe(
                tbl,
                use_container_width=True,
                height=520,
                column_config={
                    'Flow Score': st.column_config.NumberColumn(format='%.3f'),
                    'Rel.Vol.':   st.column_config.NumberColumn(format='%.2f'),
                    'EWO %':      st.column_config.NumberColumn(format='%.2f'),
                },
            )
