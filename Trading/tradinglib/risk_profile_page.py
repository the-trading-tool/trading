"""Risk profile — Streamlit page.

Pick how much movement you are willing to sit through, see what that leaves of
the universe, and take the expression away into a buy/sell formula.

The page deliberately shows the profile's *promise* next to its selection: a
risk profile is a claim about volatility and drawdown, and unlike a claim about
return that one can be checked. What the page does not do is suggest the profile
buys you extra return — measured over 2020-2026 it costs some, and the method
section says so.

Everything numeric lives in :mod:`tradinglib.risk_profile`; this module only
renders it.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import pandas as pd
import streamlit as st

from tradinglib import risk_profile as rp
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

_SNAPSHOT_KEY = '_risk_profile_snapshot'

# How many names the shortlist shows before it stops being a shortlist.
_SHORTLIST = 25


def _viewer_url(ticker: str) -> str:
    """Asset-viewer link, relative to the app's own origin."""
    return f"/?asset=true&symbol={quote(str(ticker))}"


class RiskProfilePage:
    def __init__(self, username: str = 'admin', db_path: str = 'database'):
        self.username = username
        self.db_path = db_path

    # ── Page ─────────────────────────────────────────────────────────────────
    def render(self):
        st.title(t('risk.title'))
        st.caption(t('risk.subtitle'))

        stored = rp.settings(self.username)
        chosen = self._picker(stored)
        profile = self._tuning(chosen, stored)

        self._promise(profile)
        self._preview(profile)
        self._expression(profile)
        self._method()

    # ── Profile choice ───────────────────────────────────────────────────────
    def _picker(self, stored: dict) -> str:
        names = rp.available()
        index = names.index(stored['profile']) if stored['profile'] in names else 0
        chosen = st.radio(
            t('risk.profile'), names, index=index, horizontal=True,
            format_func=lambda n: t(f'risk.name_{n}'),
            help=t('risk.profile_help'))
        st.caption(t(f'risk.desc_{chosen}'))
        return chosen

    def _tuning(self, chosen: str, stored: dict) -> dict:
        """Optional overrides on top of the preset, saved on an explicit click.

        No ``on_change`` callbacks here: Streamlit re-fires them when a widget is
        garbage-collected between reruns, which has already written half-finished
        selections into the config once (see the overlay defaults).
        """
        preset = rp.resolve(name=chosen)
        custom = dict(stored['custom']) if stored['profile'] == chosen else {}

        with st.expander(t('risk.tuning'), expanded=False):
            with st.form('_risk_profile_form'):
                c1, c2, c3 = st.columns(3)
                atr_default = custom.get('max_atr_pct', preset['max_atr_pct'])
                unlimited = preset['max_atr_pct'] is None
                max_atr = c1.slider(
                    t('risk.max_atr'), 0.5, 12.0,
                    float((atr_default or 0.12) * 100), step=0.1, format='%.1f',
                    disabled=unlimited, help=t('risk.max_atr_help'))
                stop = c2.slider(
                    t('risk.stop'), 3.0, 40.0,
                    float(custom.get('stop_loss_pct', preset['stop_loss_pct'])),
                    step=0.5, format='%.1f', help=t('risk.stop_help'))
                target_vol = c3.slider(
                    t('risk.target_vol'), 0.05, 1.00,
                    float(custom.get('target_position_vol',
                                     preset['target_position_vol'])),
                    step=0.01, format='%.2f', help=t('risk.target_vol_help'))

                c1, c2 = st.columns([0.25, 0.75])
                save = c1.form_submit_button(t('risk.save'), type='primary')
                reset = c2.form_submit_button(t('risk.reset'))

        if reset:
            rp.save_settings(self.username, {'profile': chosen, 'custom': {}})
            st.success(t('risk.reset_done'))
            custom = {}
        elif save:
            custom = {'stop_loss_pct': stop, 'target_position_vol': target_vol}
            if not unlimited:
                custom['max_atr_pct'] = round(max_atr / 100.0, 5)
            rp.save_settings(self.username, {'profile': chosen, 'custom': custom})
            st.success(t('risk.saved', profile=t(f'risk.name_{chosen}')))

        profile = rp.resolve(name=chosen)
        profile.update(custom)
        profile['customised'] = bool(custom)
        return profile

    # ── The promise ──────────────────────────────────────────────────────────
    def _promise(self, profile: dict):
        st.subheader(t('risk.promise'))
        calibration = rp.load_calibration()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(t('risk.typical_vol'), f"{profile['typical_vol'] * 100:.0f} %",
                  help=t('risk.typical_vol_help'))
        c2.metric(t('risk.vol_band'), f"{profile['vol_band'] * 100:.0f} %",
                  help=t('risk.vol_band_help',
                         q=int(calibration.get('vol_quantile', 0.85) * 100)))
        c3.metric(t('risk.drawdown'), f"{profile['drawdown_floor'] * 100:.1f} %",
                  help=t('risk.drawdown_help',
                         q=int(calibration.get('drawdown_quantile', 0.10) * 100)))
        c4.metric(t('risk.stop_metric'), f"{profile['stop_loss_pct']:.0f} %",
                  help=t('risk.stop_metric_help'))
        st.caption(t('risk.calibrated_on', source=calibration.get('source', '—'),
                     version=calibration.get('version', '—')))
        if profile.get('customised'):
            st.info(t('risk.customised_warning'))

    # ── What it leaves ───────────────────────────────────────────────────────
    def _snapshot(self) -> pd.DataFrame:
        cached = st.session_state.get(_SNAPSHOT_KEY)
        if isinstance(cached, pd.DataFrame):
            return cached
        with st.spinner(t('risk.loading')):
            frame = rp.universe_snapshot(self.db_path)
        st.session_state[_SNAPSHOT_KEY] = frame
        return frame

    def _preview(self, profile: dict):
        st.subheader(t('risk.preview'))
        frame = self._snapshot()
        if frame.empty:
            st.info(t('risk.no_data'))
            return

        scored = frame[frame['riskScore'].notna() & (frame['riskScore'] > 0)]
        if scored.empty:
            st.warning(t('risk.not_backfilled'))
            return
        if len(scored) < len(frame):
            st.caption(t('risk.partial', done=len(scored), total=len(frame)))

        mask = rp.apply(scored, profile)
        selected = scored[mask]
        c1, c2, c3 = st.columns(3)
        c1.metric(t('risk.universe'), f"{len(scored):,}".replace(',', '.'))
        c2.metric(t('risk.fits'), f"{len(selected):,}".replace(',', '.'),
                  delta=f"{len(selected) / len(scored) * 100:.0f} %",
                  delta_color='off')
        trending = selected[selected['trendScore'] >= 75] if len(selected) else selected
        c3.metric(t('risk.trending'), f"{len(trending):,}".replace(',', '.'),
                  help=t('risk.trending_help'))

        if selected.empty:
            st.info(t('risk.nothing_fits'))
            return

        st.bar_chart(scored['riskBucket'].value_counts().sort_index()
                     .rename(t('risk.bucket_chart')))

        shortlist = selected.sort_values('trendScore', ascending=False).head(_SHORTLIST)
        view = pd.DataFrame({
            t('risk.col_ticker'): shortlist['ticker'],
            t('risk.col_name'): shortlist.get('longName', pd.Series(dtype=str)),
            t('risk.col_sector'): shortlist.get('sector', pd.Series(dtype=str)),
            t('risk.col_close'): shortlist['close'],
            t('risk.col_risk'): shortlist['riskScore'],
            t('risk.col_trend'): shortlist['trendScore'],
            t('risk.col_link'): [_viewer_url(s) for s in shortlist['ticker']],
        })
        st.dataframe(
            view, hide_index=True, use_container_width=True,
            column_config={
                t('risk.col_close'): st.column_config.NumberColumn(format='%.2f'),
                t('risk.col_risk'): st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format='%.0f'),
                t('risk.col_trend'): st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format='%.0f'),
                t('risk.col_link'): st.column_config.LinkColumn(
                    display_text=t('risk.col_link_text')),
            })
        st.caption(t('risk.shortlist_note', n=_SHORTLIST))

    # ── Take-away ────────────────────────────────────────────────────────────
    def _expression(self, profile: dict):
        st.subheader(t('risk.expression'))
        expression = rp.filter_expression(profile)
        st.code(f"{expression} & (trendScore >= 75)", language='text')
        st.caption(t('risk.expression_help'))
        st.caption(t('risk.zero_warning'))

    # ── Method ───────────────────────────────────────────────────────────────
    def _method(self):
        with st.expander(t('risk.method'), expanded=False):
            st.markdown(t('risk.method_md'))
