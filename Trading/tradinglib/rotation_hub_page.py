"""Rotation / Correlation Hub — combines Sector Rotation, Global Rotation,
Correlation Index and Fear & Greed Index into one page with tabs, so the
sidebar exposes a single entry point instead of four separate buttons.
"""
import logging

import streamlit as st

from tradinglib.i18n import t

logger = logging.getLogger(__name__)


class RotationHubPage:
    """Streamlit page: tabbed container for the four rotation/correlation dashboards."""

    def __init__(self, username: str = "") -> None:
        self.username = username

    def render(self) -> None:
        tab_global, tab_sector, tab_corr, tab_fg = st.tabs([
            t('nav.global_rotation'),
            t('nav.sector_rotation'),
            t('nav.correlation'),
            t('nav.fear_greed'),
        ])

        with tab_global:
            try:
                from tradinglib.global_rotation_page import GlobalRotationPage
                GlobalRotationPage(username=self.username).render()
            except Exception as e:
                logger.exception("rotation_hub: global_rotation tab failed")
                st.error(t('error.load_global_rotation', error=e))

        with tab_sector:
            try:
                from tradinglib.sector_rotation_page import SectorRotationPage
                SectorRotationPage(username=self.username).render()
            except Exception as e:
                logger.exception("rotation_hub: sector_rotation tab failed")
                st.error(t('error.load_rotation', error=e))

        with tab_corr:
            try:
                from tradinglib.correlation_index_page import CorrelationIndexPage
                CorrelationIndexPage(username=self.username).render()
            except Exception as e:
                logger.exception("rotation_hub: correlation tab failed")
                st.error(t('error.load_correlation', error=e))

        with tab_fg:
            try:
                from tradinglib.fear_greed_page import FearGreedPage
                FearGreedPage(username=self.username).render()
            except Exception as e:
                logger.exception("rotation_hub: fear_greed tab failed")
                st.error(t('error.load_fear_greed', error=e))
