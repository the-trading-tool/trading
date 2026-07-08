"""
correlation_index_page.py — Market → Korrelationsindex.

Renders the rolling cross-asset correlations stored by
``tradinglib.correlation_index`` and derives a plain-language trend reading for
each pair (sign · strength · direction · economic meaning).

The heavy computation is done by the daily scheduler job; this page only reads
``correlation_index.db``. A manual "compute now" button is offered as a fallback
when the DB is still empty.

All user-facing text is localised via i18n (``corr.*`` keys in locales/*.json);
the language follows the user's config, English by default.
"""
import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tradinglib import correlation_index as ci
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

# Correlation-strength buckets on |r|
_STRONG = 0.6
_MODERATE = 0.3
_WEAK = 0.15

# Trend threshold: change in correlation over the look-back that counts as a move
_TREND_DELTA = 0.12
_TREND_LOOKBACK = 21   # ~1 trading month

# Theme-neutral colours per window (visible in dark and light mode)
_WIN_COLOR = {30: '#f59e0b', 90: '#3b82f6'}

# Language-neutral trend tokens → arrow + locale key
_TREND_ARROW = {'rising': '↗', 'falling': '↘', 'stable': '→', 'na': '—'}
_TREND_KEY = {
    'rising':  'corr.trend_rising',
    'falling': 'corr.trend_falling',
    'stable':  'corr.trend_stable',
    'na':      'corr.trend_na',
}


def _trend_label(token: str) -> str:
    """Localised word for a trend token."""
    return t(_TREND_KEY.get(token, 'corr.trend_na'))


def _strength_label(r: float) -> str:
    """Localised strength + direction description for a correlation value."""
    a = abs(r)
    if a >= _STRONG:
        base = t('corr.strength_very_strong') if a >= 0.8 else t('corr.strength_strong')
    elif a >= _MODERATE:
        base = t('corr.strength_moderate')
    elif a >= _WEAK:
        base = t('corr.strength_weak')
    else:
        return t('corr.strength_decoupled')
    direction = t('corr.dir_positive') if r > 0 else t('corr.dir_negative')
    return f'{base} {direction}'


def _trend_reading(sub: pd.DataFrame, col: str) -> tuple[str, float | None, float | None]:
    """Return (trend_token, current, delta) for one correlation column.

    current = latest non-NaN value; delta = current minus the value
    ~_TREND_LOOKBACK rows earlier. trend_token ∈ {rising, falling, stable, na}.
    """
    ser = sub[col].dropna()
    if ser.empty:
        return 'na', None, None
    current = float(ser.iloc[-1])
    if len(ser) <= _TREND_LOOKBACK:
        return 'stable', current, None
    prior = float(ser.iloc[-(_TREND_LOOKBACK + 1)])
    delta = current - prior
    if delta > _TREND_DELTA:
        token = 'rising'
    elif delta < -_TREND_DELTA:
        token = 'falling'
    else:
        token = 'stable'
    return token, current, delta


def _interpret(pair: dict, current: float | None, trend: str,
               delta: float | None) -> str:
    """Compose the localised interpretation sentence(s) for a pair."""
    if current is None:
        return t('corr.interp_no_history')

    pid = pair['id']
    narrative = t(f'corr.pair.{pid}.narr_pos') if current > 0 else t(f'corr.pair.{pid}.narr_neg')
    strength = _strength_label(current)

    parts = [t('corr.interp_current', val=f'{current:+.2f}', strength=strength), narrative]

    if delta is not None and trend != 'stable':
        if trend == 'rising':
            parts.append(t('corr.interp_rising', dir=t('corr.dir_up'), delta=f'{delta:+.2f}'))
        else:
            parts.append(t('corr.interp_falling', dir=t('corr.dir_down'), delta=f'{delta:+.2f}'))
    return ' '.join(parts)


class CorrelationIndexPage:
    """Market page: rolling cross-asset correlation index + trend interpretation."""

    def __init__(self, username: str = 'admin'):
        self.username = username

    # ── Render ────────────────────────────────────────────────────────────────

    def render(self):
        st.title(t('page.correlation'))
        st.caption(t('corr.caption'))

        series, meta, updated_at = ci.load_from_db()

        # Empty DB → offer manual compute
        if series is None or series.empty:
            st.info(t('corr.empty_info'))
            if st.button(t('corr.compute_now'), type="primary"):
                with st.spinner(t('corr.computing')):
                    try:
                        ci.run(quiet=True)
                    except Exception as exc:
                        logger.exception("correlation_index manual run failed")
                        st.error(t('corr.compute_failed', error=exc))
                        return
                st.rerun()
            return

        col_win, col_info = st.columns([1, 3])
        with col_win:
            window = st.radio(
                t('corr.window'), options=list(ci.WINDOWS),
                format_func=lambda w: t('corr.window_opt', n=w),
                horizontal=True, index=len(ci.WINDOWS) - 1,
                help=t('corr.window_help'),
            )
        with col_info:
            if updated_at:
                st.caption(t('corr.last_computed', ts=updated_at))
            st.caption(t('corr.btc_note'))

        corr_col = f'corr_{window}'

        # ── Overview table: current value + trend per pair ─────────────────────
        self._render_overview(series, corr_col, window)
        self._render_methodology()
        st.markdown("---")

        # ── Per-pair chart + interpretation ────────────────────────────────────
        meta_by_id = {r['pair_id']: r for _, r in meta.iterrows()} if not meta.empty else {}
        for pair in ci.PAIRS:
            sub = series[series['pair_id'] == pair['id']].copy()
            self._render_pair(pair, sub, corr_col, window, meta_by_id.get(pair['id']))

    # ── Methodik ──────────────────────────────────────────────────────────────

    def _render_methodology(self):
        """Explain what is measured — so a comparison with external tools is fair."""
        with st.expander(t('corr.methodology_title')):
            st.markdown(t('corr.methodology_body'))

    # ── Overview ──────────────────────────────────────────────────────────────

    def _render_overview(self, series: pd.DataFrame, corr_col: str, window: int):
        st.subheader(t('corr.overview_header', n=window))

        col_pair = t('corr.col_pair')
        col_corr = t('corr.col_correlation')
        col_trend = t('corr.col_trend')
        col_class = t('corr.col_classification')

        rows = []
        for pair in ci.PAIRS:
            sub = series[series['pair_id'] == pair['id']]
            trend, current, _delta = _trend_reading(sub, corr_col)
            arrow = _TREND_ARROW.get(trend, '—')
            rows.append({
                col_pair:  t(f"corr.pair.{pair['id']}.label"),
                col_corr:  None if current is None else round(current, 2),
                col_trend: f"{arrow} {_trend_label(trend)}",
                col_class: '—' if current is None else _strength_label(current),
            })
        df = pd.DataFrame(rows)

        def _color(val):
            if not isinstance(val, (int, float)):
                return ''
            if val >= _STRONG:
                return 'background-color:#14532d; color:#bbf7d0'
            if val >= _MODERATE:
                return 'background-color:#1a3d28; color:#86efac'
            if val <= -_STRONG:
                return 'background-color:#7f1d1d; color:#fecaca'
            if val <= -_MODERATE:
                return 'background-color:#451a1a; color:#fca5a5'
            return 'color:#94a3b8'

        st.dataframe(
            df.style.map(_color, subset=[col_corr]),
            use_container_width=True, hide_index=True,
            column_config={col_corr: st.column_config.NumberColumn(format="%.2f")},
        )

    # ── Single pair ───────────────────────────────────────────────────────────

    def _render_pair(self, pair: dict, sub: pd.DataFrame, corr_col: str,
                     window: int, meta_row):
        st.subheader(t(f"corr.pair.{pair['id']}.label"))
        a_lbl = t(f"corr.sym.{pair['a']}")
        b_lbl = t(f"corr.sym.{pair['b']}")
        st.caption(f"{a_lbl}  ·  {b_lbl}")

        if sub.empty or sub[corr_col].dropna().empty:
            st.warning(t('corr.no_history'))
            return

        sub = sub.copy()
        sub['Date'] = pd.to_datetime(sub['Date'])
        sub = sub.sort_values('Date')

        trend, current, delta = _trend_reading(sub, corr_col)

        # KPIs
        c1, c2, c3 = st.columns(3)
        c1.metric(t('corr.kpi_correlation', n=window),
                  f"{current:+.2f}" if current is not None else "—",
                  f"{delta:+.2f}" if delta is not None else None)
        full_corr = None
        if meta_row is not None and pd.notna(meta_row.get('full_corr')):
            full_corr = float(meta_row['full_corr'])
        c2.metric(t('corr.kpi_full'),
                  f"{full_corr:+.2f}" if full_corr is not None else "—")
        c3.metric(t('corr.kpi_trend'),
                  f"{_TREND_ARROW.get(trend, '—')} {_trend_label(trend)}")

        # Chart — both windows for context, selected one emphasised
        fig = go.Figure()
        for w in ci.WINDOWS:
            col = f'corr_{w}'
            if col not in sub.columns:
                continue
            emphasised = (col == corr_col)
            fig.add_trace(go.Scatter(
                x=sub['Date'], y=sub[col], mode='lines',
                name=t('corr.window_opt', n=w), line=dict(
                    color=_WIN_COLOR.get(w, '#94a3b8'),
                    width=2.4 if emphasised else 1.2,
                ),
                opacity=1.0 if emphasised else 0.45,
            ))
        # Reference bands: strong positive / strong negative / zero
        fig.add_hline(y=0, line=dict(color='#64748b', width=1, dash='dot'))
        fig.add_hline(y=_STRONG, line=dict(color='#22c55e', width=0.8, dash='dash'))
        fig.add_hline(y=-_STRONG, line=dict(color='#ef4444', width=0.8, dash='dash'))
        fig.update_layout(
            height=300, margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(range=[-1, 1], title=t('corr.col_correlation'), zeroline=False),
            xaxis=dict(title=None),
            legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='right', x=1.0),
            hovermode='x unified',
        )
        st.plotly_chart(fig, use_container_width=True,
                        theme="streamlit", key=f"corr_chart_{pair['id']}")

        # Interpretation
        st.markdown(f"**{t('corr.interpretation')}:** {_interpret(pair, current, trend, delta)}")
        with st.expander(t('corr.background')):
            st.caption(t(f"corr.pair.{pair['id']}.context"))
        st.markdown("")
