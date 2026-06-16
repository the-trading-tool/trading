"""
Upgrade-Teaser für die Scalable-Edition.

Wird vom Edition-Guard in asset_analyzer.render() aufgerufen, sobald ein Nutzer
der Scalable-Edition eine Route öffnet, die nur im vollen Funktionsumfang
("Upgrade") verfügbar ist. Stellt die freien Features der Scalable-Edition den
Upgrade-Features gegenüber und bietet einen Call-to-Action.
"""
import logging

import streamlit as st

from tradinglib.i18n import t as _t

logger = logging.getLogger(__name__)

# Mapping: blockierte Route → i18n-Key des Feature-Namens (für die Überschrift).
_ROUTE_LABELS = {
    'performance':     'nav.performance',
    'summary':         'nav.asset_summary',
    'multi':           'nav.multi_strategies',
    'strategy_finder': 'nav.strategy_finder',
    'trading':         'nav.trading',
    'admin':           'nav.group_admin',
    'live_chart':      'page.live_chart',
    'option_calc':     'page.option_price',
}


def render_upgrade_teaser(region=st, route=None, username: str = ''):
    """Render the upgrade page for a blocked route in the Scalable edition."""
    r = region

    # Feature-spezifischer Hinweis, falls bekannt
    feature_label = None
    if route and route in _ROUTE_LABELS:
        feature_label = _t(_ROUTE_LABELS[route])

    r.markdown(f"## 🔓 {_t('upgrade.title')}")
    if feature_label:
        r.info(_t('upgrade.feature_locked', feature=feature_label))
    else:
        r.info(_t('upgrade.locked_notice'))

    r.markdown(_t('upgrade.intro'))

    col_free, col_pro = r.columns(2)

    with col_free:
        st.markdown(f"### ✅ {_t('upgrade.free_header')}")
        st.markdown(
            f"- {_t('nav.own_transactions')}\n"
            f"- {_t('nav.asset_viewer')}\n"
            f"- {_t('nav.market_overview')}\n"
            f"- {_t('nav.sector_rotation')}\n"
            f"- {_t('nav.market_map')}\n"
            f"- {_t('nav.compound_simulation')}"
        )

    with col_pro:
        st.markdown(f"### ⭐ {_t('upgrade.unlock_header')}")
        st.markdown(
            f"- {_t('nav.strategy_finder')}\n"
            f"- {_t('nav.multi_strategies')}\n"
            f"- {_t('nav.trading')}\n"
            f"- {_t('nav.performance')}\n"
            f"- {_t('upgrade.unlock_signals')}\n"
            f"- {_t('upgrade.unlock_scoring')}"
        )

    r.divider()
    r.markdown(f"**{_t('upgrade.cta_hint')}**")
    r.link_button(
        _t('upgrade.cta_button'),
        url='https://trading.cloogidoo.com',
        type='primary',
    )
