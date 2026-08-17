"""4 Phase Sequence (4PS) — Streamlit page.

Three parts:
  * index regime — the reference index vs its weekly 200 SMA plus the size of its
    past up-legs (the method's context: an outperformer must beat that),
  * screener — every member of the selected universes scored by phase,
  * detail — weekly/monthly chart of one candidate with base, breakout, stop,
    target and the past trend legs.

The maths lives in :mod:`tradinglib.four_ps`; the same numbers are available as
the ``fps`` chart indicator and as ``fps_*`` columns in ``asset_simulation_*.db``.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from tradinglib import four_ps as fps
from tradinglib import rotation_cache
from tradinglib import system_config as sysconf
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

# Universes offered first in the multiselect (the rest of yf_tickers follows)
_PREFERRED = ['^GDAXI', '^MDAXI', '^SDAXI', '^SPX', '^NDX', '^DJI', '^TECDAX',
              '^STOXX50E', '^FTSE', '^IBEX', '^SSMI', '^RUT', '^N225']
_DEFAULT_UNIVERSES = ['^GDAXI', '^MDAXI', '^SDAXI', '^SPX']
_BENCHMARKS = ['^SPX', '^GDAXI', '^STOXX50E', '^NDX', '^N225']

# Parameters the sidebar/expander exposes — the rest stays at four_ps.DEFAULTS
_EDITABLE = ('trend_min_pct', 'min_trends', 'base_weeks', 'base_depth_pct',
             'near_high_pct', 'breakout_pct', 'confirm_weeks', 'trend_sma_weeks',
             'stop_pct', 'trail_pct', 'target_pct', 'min_years')

_PHASE_COLORS = {0: '#9E9E9E', 1: '#7F8C8D', 2: '#E8890C', 3: '#7CB518', 4: '#2E7D32'}


def _phase_label(phase: int) -> str:
    return t(f'fps.phase_{int(phase)}')


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_regime(benchmark: str, db_path: str, day: str) -> dict:
    """Index regime for one day — `day` keeps the cache fresh without a TTL race."""
    res = fps.index_regime(benchmark, db_path)
    # The raw weekly frames are only needed by the CLI; dropping them keeps the
    # cached payload small.
    res.pop('weekly', None)
    res.pop('sma_series', None)
    return res


def _params_signature(params: dict) -> str:
    """Stable signature of the full parameter set (cache key input)."""
    return json.dumps({k: params.get(k) for k in sorted(fps.DEFAULTS)}, default=str)


def _cached_scan_lookup(universes: list[str], params: dict):
    """Return an already persisted scan for this configuration, else None."""
    return rotation_cache.get(rotation_cache.four_ps_key(
        universes, _params_signature(params)))


class FourPsPage:
    def __init__(self, username: str = "", db_path: str = "database"):
        self.username = username
        self.db_path = db_path
        self.sys_config = sysconf.SystemConfig(username=username)

    # ── Configuration ────────────────────────────────────────────────────────
    def _load_params(self) -> dict:
        """Merged parameter set: four_ps defaults ← stored config ← nothing else."""
        stored = self.sys_config.get_value('fps_params', '')
        params = dict(fps.DEFAULTS)
        if stored:
            try:
                params.update(json.loads(stored) if isinstance(stored, str) else dict(stored))
            except Exception:
                logger.debug("four_ps_page: stored fps_params unreadable", exc_info=True)
        return params

    def _save_params(self, params: dict) -> None:
        self.sys_config.set_value('fps_params', json.dumps(
            {k: params[k] for k in _EDITABLE if k in params}))

    def _universes(self) -> list[str]:
        stored = self.sys_config.get_value('fps_universes', '')
        if isinstance(stored, str) and stored.strip():
            picked = [u.strip() for u in stored.split(',') if u.strip()]
            if picked:
                return picked
        return list(_DEFAULT_UNIVERSES)

    # ── Screener ─────────────────────────────────────────────────────────────
    def _members(self, universes: list[str]) -> list[str]:
        seen: list[str] = []
        for uni in universes:
            for tk in fps.index_members(uni, self.db_path):
                if tk not in seen:
                    seen.append(tk)
        return seen

    def _run_scan(self, universes: list[str], params: dict, force: bool = False) -> pd.DataFrame:
        """Scan with a persistent per-day cache (survives reruns and restarts)."""
        key = rotation_cache.four_ps_key(universes, _params_signature(params))
        if force:
            rotation_cache.drop(key)
        else:
            hit = rotation_cache.get(key)
            if hit is not None and not (isinstance(hit, pd.DataFrame) and hit.empty):
                return hit

        members = self._members(universes)
        if not members:
            return pd.DataFrame()

        bar = st.progress(0.0, text=t('fps.scanning', done=0, total=len(members)))

        def _progress(done, total):
            bar.progress(min(1.0, done / max(1, total)),
                         text=t('fps.scanning', done=done, total=total))

        df = fps.scan(members, db_path=self.db_path, progress=_progress, **params)
        bar.empty()
        if not df.empty:
            rotation_cache.put(key, df)
        return df

    def _screener_table(self, df: pd.DataFrame, params: dict) -> None:
        view = pd.DataFrame({
            t('fps.col_ticker'): df['ticker'],
            t('fps.col_name'): df['name'].str.slice(0, 38),
            t('fps.col_phase'): [f"{int(p)} · {_phase_label(p)}" for p in df['phase']],
            t('fps.col_base_weeks'): df['base_weeks'],
            t('fps.col_to_breakout'): df['to_breakout'].round(1),
            t('fps.col_best_trend'): df['best_trend'].round(0),
            t('fps.col_trend_gain'): df['trend_gain'].round(0),
            t('fps.col_rs'): df['rs'].round(1),
            t('fps.col_dist_high'): df['dist_high'].round(1),
            t('fps.col_price'): df['price'].round(2),
            t('fps.col_stop'): df['stop'].round(2),
            t('fps.col_target'): df['target'].round(2),
            t('fps.col_signal'): [
                (f"{t('fps.signal_' + s)} · {d:%Y-%m-%d}" if s and pd.notna(d) else '')
                for s, d in zip(df['signal'], df['signal_date'])],
        })
        event = st.dataframe(view, use_container_width=True, hide_index=True,
                             height=460, on_select='rerun',
                             selection_mode='single-row', key='_fps_table')
        rows = (event.selection.rows if event and getattr(event, 'selection', None) else [])
        if rows:
            st.session_state['_fps_detail_ticker'] = str(df['ticker'].iloc[rows[0]])

        c1, c2, c3 = st.columns([0.3, 0.35, 0.35])
        tickers = list(df['ticker'])
        if c1.button(t('fps.save_monitored'), key='_fps_save_monitored',
                     use_container_width=True):
            self.sys_config.set_value('monitored_assets', ", ".join(tickers))
            st.success(t('fps.saved_monitored', n=len(tickers)))
        if rows and c2.button(t('fps.open_viewer'), key='_fps_open_viewer',
                              use_container_width=True):
            st.session_state['_nav_params'] = {'asset': 'true',
                                               'symbol': str(df['ticker'].iloc[rows[0]])}
            st.rerun()
        c3.caption(t('fps.select_hint'))
        with st.expander(t('fps.ticker_list'), expanded=False):
            st.code(", ".join(tickers), language='text')

    # ── Charts ───────────────────────────────────────────────────────────────
    def _weekly_chart(self, res: dict, params: dict):
        daily, frame = res['daily'], res['frame']
        weekly = fps.period_bars(daily, 'W-FRI').tail(260)
        idx = weekly.index.to_timestamp()

        # Project the daily levels/signals onto the weekly grid: last value per week
        fw = frame.copy()
        fw['_w'] = fw.index.to_period('W-FRI')
        lvl = fw.groupby('_w')[['fps_base_high', 'fps_base_low', 'fps_stop',
                                'fps_target', 'fps_phase']].last().reindex(weekly.index)
        sig = fw.groupby('_w')[['fps_buy', 'fps_sell']].max().reindex(weekly.index)

        fig = go.Figure()
        fig.add_trace(go.Candlestick(x=idx, open=weekly['Open'], high=weekly['High'],
                                     low=weekly['Low'], close=weekly['Close'],
                                     name=res['ticker'], showlegend=False))
        for col, color, dash, width, name in (
                ('fps_base_high', '#7CB518', 'solid', 2, t('fps.lg_base_high')),
                ('fps_base_low', '#7CB518', 'dot', 1, t('fps.lg_base_low')),
                ('fps_stop', '#C0392B', 'dash', 1, t('fps.lg_stop')),
                ('fps_target', '#2E7D32', 'dot', 1, t('fps.lg_target'))):
            s = lvl[col].where(lvl[col] > 0)
            fig.add_trace(go.Scatter(x=idx, y=s, name=name, connectgaps=False,
                                     line=dict(color=color, width=width, dash=dash)))
        sma = weekly['Close'].rolling(int(params['trend_sma_weeks'])).mean()
        fig.add_trace(go.Scatter(x=idx, y=sma, name=t('fps.lg_trend_sma',
                                                      n=int(params['trend_sma_weeks'])),
                                 line=dict(color='#5B8DEF', width=1.5)))
        fig.add_trace(go.Scatter(x=idx, y=sig['fps_buy'], mode='markers', name=t('fps.lg_buy'),
                                 marker_symbol='triangle-up', marker_size=13,
                                 marker_color='#7CB518'))
        fig.add_trace(go.Scatter(x=idx, y=sig['fps_sell'], mode='markers', name=t('fps.lg_sell'),
                                 marker_symbol='triangle-down', marker_size=13,
                                 marker_color='#C0392B'))
        fig.update_layout(height=470, margin=dict(t=20, b=10, l=10, r=10),
                          xaxis_rangeslider_visible=False,
                          legend=dict(orientation='h', y=1.06, x=0))
        st.plotly_chart(fig, use_container_width=True, key=f"_fps_wk_{res['ticker']}")

    def _monthly_chart(self, res: dict, params: dict):
        daily = res['daily']
        monthly = fps.period_bars(daily, 'M')
        _b, _c, _l, legs = fps.zigzag(monthly['Close'], params['reversal_pct'],
                                      params['trend_min_pct'])
        idx = monthly.index.to_timestamp()

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=idx, y=monthly['Close'], name=t('fps.lg_close'),
                                 line=dict(color='#455A64', width=1.6)))
        for leg in legs:
            if leg['gain'] <= 0:
                continue
            qualified = leg['gain'] >= float(params['trend_min_pct'])
            x0, x1 = leg['start'].to_timestamp(), leg['end'].to_timestamp()
            y0 = float(monthly['Close'].get(leg['start'], np.nan))
            y1 = float(monthly['Close'].get(leg['end'], np.nan))
            if np.isnan(y0) or np.isnan(y1):
                continue
            fig.add_trace(go.Scatter(
                x=[x0, x1], y=[y0, y1], mode='lines+text', showlegend=False,
                line=dict(color='#2E7D32' if qualified else '#B0BEC5',
                          width=3 if qualified else 1.5),
                text=['', f"{leg['gain']:.0f} %"], textposition='top center',
                textfont=dict(color='#2E7D32' if qualified else '#90A4AE', size=12),
                hovertemplate=f"{leg['gain']:.0f} %<extra></extra>"))
        fig.update_layout(height=380, margin=dict(t=20, b=10, l=10, r=10),
                          yaxis_type='log', showlegend=False)
        st.plotly_chart(fig, use_container_width=True, key=f"_fps_mo_{res['ticker']}")
        st.caption(t('fps.monthly_caption', pct=int(params['trend_min_pct'])))

    # ── Page sections ────────────────────────────────────────────────────────
    def _regime(self, benchmark: str):
        regime = _cached_regime(benchmark, self.db_path, dt.date.today().isoformat())
        if not regime.get('ok'):
            st.warning(t('fps.regime_no_data', index=benchmark))
            return
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(t('fps.regime_close', index=benchmark), f"{regime['close']:,.0f}")
        c2.metric(t('fps.regime_sma'), f"{regime['sma']:,.0f}",
                  f"{regime['dist_sma']:+.1f} %")
        c3.metric(t('fps.regime_leg'), f"{regime['current_leg']:+.0f} %")
        verdict = t('fps.regime_on') if regime['above_sma'] else t('fps.regime_off')
        c4.metric(t('fps.regime_state'), verdict)

        legs = [l for l in regime['legs'] if l['gain'] > 0][-8:]
        if legs:
            with st.expander(t('fps.regime_legs_header'), expanded=False):
                tbl = pd.DataFrame({
                    t('fps.col_from'): [f"{l['start']:%Y-%m}" for l in legs],
                    t('fps.col_to'): [f"{l['end']:%Y-%m}" for l in legs],
                    t('fps.col_gain'): [round(l['gain'], 1) for l in legs],
                })
                st.dataframe(tbl, hide_index=True, use_container_width=True)
                st.caption(t('fps.regime_legs_caption'))

    def _params_editor(self, params: dict) -> dict:
        with st.expander(t('fps.params_header'), expanded=False):
            c = st.columns(4)
            out = dict(params)
            out['trend_min_pct'] = c[0].number_input(
                t('fps.p_trend_min'), 20.0, 500.0, float(params['trend_min_pct']), 5.0)
            out['min_trends'] = c[1].number_input(
                t('fps.p_min_trends'), 1, 10, int(params['min_trends']))
            out['base_weeks'] = c[2].number_input(
                t('fps.p_base_weeks'), 3, 52, int(params['base_weeks']))
            out['base_depth_pct'] = c[3].number_input(
                t('fps.p_base_depth'), 5.0, 60.0, float(params['base_depth_pct']), 1.0)
            c = st.columns(4)
            out['near_high_pct'] = c[0].number_input(
                t('fps.p_near_high'), 2.0, 60.0, float(params['near_high_pct']), 1.0)
            out['breakout_pct'] = c[1].number_input(
                t('fps.p_breakout'), 0.0, 10.0, float(params['breakout_pct']), 0.5)
            out['confirm_weeks'] = c[2].number_input(
                t('fps.p_confirm'), 1, 12, int(params['confirm_weeks']))
            out['trend_sma_weeks'] = c[3].number_input(
                t('fps.p_trend_sma'), 5, 60, int(params['trend_sma_weeks']))
            c = st.columns(4)
            out['stop_pct'] = c[0].number_input(
                t('fps.p_stop'), 1.0, 30.0, float(params['stop_pct']), 0.5)
            out['trail_pct'] = c[1].number_input(
                t('fps.p_trail'), 0.0, 50.0, float(params['trail_pct']), 1.0)
            out['target_pct'] = c[2].number_input(
                t('fps.p_target'), 10.0, 300.0, float(params['target_pct']), 5.0)
            out['min_years'] = c[3].number_input(
                t('fps.p_min_years'), 3, 30, int(params['min_years']))
            if st.button(t('fps.params_save'), key='_fps_save_params'):
                self._save_params(out)
                st.success(t('fps.params_saved'))
            return out

    def _tab_screener(self, params: dict):
        options = _PREFERRED + [u for u in fps.index_list(self.db_path)
                                if u not in _PREFERRED]
        c1, c2 = st.columns([0.75, 0.25])
        universes = c1.multiselect(t('fps.universe'), options,
                                   default=[u for u in self._universes() if u in options],
                                   key='_fps_universes')
        params['benchmark'] = c2.selectbox(
            t('fps.benchmark'), _BENCHMARKS,
            index=_BENCHMARKS.index(params['benchmark'])
            if params['benchmark'] in _BENCHMARKS else 0, key='_fps_benchmark')

        params = self._params_editor(params)

        c1, c2, c3, c4 = st.columns([0.28, 0.24, 0.24, 0.24])
        run = c1.button(t('fps.scan_btn'), type='primary', use_container_width=True,
                        key='_fps_scan')
        phases = c2.multiselect(t('fps.filter_phase'), [4, 3, 2, 1, 0], default=[4, 3, 2],
                                format_func=lambda p: f"{p} · {_phase_label(p)}",
                                key='_fps_filter_phase')
        min_rs = c3.number_input(t('fps.filter_rs'), -200.0, 500.0, -100.0, 5.0,
                                 key='_fps_filter_rs')
        max_to_break = c4.number_input(t('fps.filter_to_break'), 0.0, 100.0, 100.0, 1.0,
                                       key='_fps_filter_break')

        if not universes:
            st.info(t('fps.pick_universe'))
            return

        if run:
            st.session_state['_fps_result'] = self._run_scan(universes, params, force=True)
        elif '_fps_result' not in st.session_state:
            # Silent warm start: only serves an already cached run, never scans
            cached = _cached_scan_lookup(universes, params)
            if cached is not None and not cached.empty:
                st.session_state['_fps_result'] = cached

        df = st.session_state.get('_fps_result')
        if df is None or df.empty:
            st.info(t('fps.no_scan_yet'))
            return

        # Keep the frame stable across reruns — a table whose data changes every
        # rerun drops the user's row selection.
        flt = df[df['phase'].isin(phases) & (df['rs'] >= min_rs)]
        flt = flt[(flt['phase'] >= 3) | (flt['to_breakout'] <= max_to_break)]
        st.caption(t('fps.result_count', shown=len(flt), total=len(df)))
        if flt.empty:
            st.warning(t('fps.no_candidates'))
            return
        self._screener_table(flt.reset_index(drop=True), params)

    def _tab_detail(self, params: dict):
        default_tk = st.session_state.get('_fps_detail_ticker', '')
        c1, c2 = st.columns([0.4, 0.6])
        ticker = c1.text_input(t('fps.detail_ticker'), value=default_tk,
                               key='_fps_detail_input').strip()
        if not ticker:
            st.info(t('fps.detail_hint'))
            return
        with st.spinner(t('fps.computing', ticker=ticker)):
            res = fps.analyze(ticker, self.db_path, **params)
        if not res.get('ok'):
            st.warning(t('fps.detail_no_data', ticker=ticker, reason=res.get('reason', '')))
            return

        phase = res['phase']
        c2.markdown(
            f"<div style='padding-top:1.9rem;font-size:20px;font-weight:600;"
            f"color:{_PHASE_COLORS.get(phase, '#9E9E9E')}'>"
            f"{t('fps.phase_badge', n=phase, name=_phase_label(phase))}</div>",
            unsafe_allow_html=True)

        m = st.columns(5)
        m[0].metric(t('fps.m_price'), f"{res['price']:,.2f}")
        m[1].metric(t('fps.m_best_trend'), f"{res['best_trend']:,.0f} %")
        m[2].metric(t('fps.m_trend_gain'), f"{res['trend_gain']:+,.0f} %")
        m[3].metric(t('fps.m_rs'), f"{res['rs']:+,.1f} pp")
        m[4].metric(t('fps.m_dist_high'), f"{res['dist_high']:,.1f} %")

        # Trade plan
        if phase >= 3 and res['stop'] > 0:
            entry, stop, target = res['price'], res['stop'], res['target']
            risk = (entry / stop - 1.0) * 100.0
            reward = (target / entry - 1.0) * 100.0
            st.info(t('fps.plan_open', stop=f"{stop:,.2f}", risk=f"{risk:.1f}",
                      target=f"{target:,.2f}", reward=f"{reward:.0f}",
                      rr=f"{(reward / risk) if risk else 0:.1f}"))
        elif phase == 2 and res['base_high'] > 0:
            trigger = res['base_high'] * (1 + float(params['breakout_pct']) / 100.0)
            stop = max(res['base_low'], res['base_high'] *
                       (1 - float(params['stop_pct']) / 100.0))
            st.info(t('fps.plan_base', weeks=res['base_weeks'],
                      low=f"{res['base_low']:,.2f}", high=f"{res['base_high']:,.2f}",
                      trigger=f"{trigger:,.2f}", to_break=f"{res['to_breakout']:+.1f}",
                      stop=f"{stop:,.2f}"))
        else:
            st.info(t('fps.plan_watch', pct=int(params['trend_min_pct'])))

        self._weekly_chart(res, params)
        self._monthly_chart(res, params)

        if st.button(t('fps.open_viewer'), key='_fps_detail_open'):
            st.session_state['_nav_params'] = {'asset': 'true', 'symbol': ticker}
            st.rerun()

    def _tab_method(self, params: dict):
        st.markdown(t('fps.method_md'))
        st.markdown(f"##### {t('fps.integration_header')}")
        st.markdown(t('fps.integration_md'))
        st.code(
            "'4PS Breakout': {\n"
            "  '^SPX': {\n"
            "    'buy':  '(fps_buy > 0)',\n"
            "    'sell': '(fps_sell > 0)',\n"
            "    'num_assets': 5,\n"
            "    'invest': 8000,\n"
            "    'order_by': 'fps_best_trend'\n"
            "  }\n"
            "}", language='python')
        st.caption(t('fps.integration_note'))

    # ── Entry point ──────────────────────────────────────────────────────────
    def render(self):
        st.markdown(f"## {t('fps.title')}")
        st.caption(t('fps.subtitle'))

        params = self._load_params()
        bm = params.get('benchmark', '^SPX')
        self._regime(bm)
        st.markdown("---")

        tab_scan, tab_detail, tab_method = st.tabs(
            [t('fps.tab_screener'), t('fps.tab_detail'), t('fps.tab_method')])
        with tab_scan:
            self._tab_screener(params)
        with tab_detail:
            self._tab_detail(params)
        with tab_method:
            self._tab_method(params)
        st.caption(t('fps.updated', ts=dt.datetime.now().strftime('%Y-%m-%d %H:%M')))
