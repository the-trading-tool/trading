"""Edge Scores — Sammelseite der Bias-Scorecards (Markt-Bereich).

Aufbau wie der Strategy Finder: links ein eigenes Untermenü (Spalte neben der
Sidebar), rechts die gewählte Scorecard. Neue Scorecards werden nur in
``CARDS`` eingetragen; Einträge ohne Renderer erscheinen als „geplant“
(deaktiviert), damit die Struktur der späteren Gesamtbewertung sichtbar ist.

Route: ``?edge=true[&card=<key>]``; das alte ``?cot=true`` landet ebenfalls auf der
Start-Karte (Asset-Scorecard).
"""
from __future__ import annotations

import logging
from typing import Callable

import streamlit as st

from tradinglib.i18n import t

logger = logging.getLogger(__name__)


def title(text: str) -> None:
    """Gemeinsame Ueberschrift fuer Untermenue und Scorecards: gleiches Element,
    kein oberer Zusatzabstand — sonst stehen die beiden Spaltenkoepfe versetzt
    (h4 und st.subheader haben unterschiedliche Margins) und tiefer als die
    Icon-Leiste der Sidebar."""
    st.markdown(
        f"<h3 style='margin:0;padding:0 0 0.4rem 0;line-height:1.3;font-size:1.6rem'>{text}</h3>",
        unsafe_allow_html=True)


def _render_cot(username: str, side) -> None:
    from tradinglib.cot_page import CotPage
    CotPage(username=username).render(side=side)


def _render_macro(username: str, side) -> None:
    from tradinglib.macro_page import MacroPage
    MacroPage(username=username).render(side=side)


def _render_trend(username: str, side) -> None:
    from tradinglib.trend_page import TrendPage
    TrendPage(username=username).render(side=side)


def _render_total(username: str, side) -> None:
    from tradinglib.total_page import TotalPage
    TotalPage(username=username).render(side=side)


def _render_asset(username: str, side) -> None:
    from tradinglib.asset_card_page import AssetCardPage
    AssetCardPage(username=username).render(side=side)


# key, Locale-Key, Icon, Renderer (None = geplant)
CARDS: list[tuple[str, str, str, Callable | None]] = [
    ("asset", "edge.card_asset", "🔎", _render_asset),
    ("cot", "edge.card_cot", "📊", _render_cot),
    ("macro", "edge.card_macro", "🏛️", _render_macro),
    ("trend", "edge.card_trend", "📈", _render_trend),
    ("total", "edge.card_total", "🎯", _render_total),
]
DEFAULT_CARD = "asset"
_STATE_KEY = "_edge_card"


def _available() -> list[str]:
    return [k for k, *_rest, fn in CARDS if fn is not None]


def resolve_card(requested: str | None, current: str | None) -> str:
    """Gewählte Scorecard: Sitzungszustand vor URL-Parameter vor Default;
    geplante/unbekannte Keys fallen auf den Default zurück."""
    avail = _available()
    for cand in (current, requested):
        if cand in avail:
            return cand
    return DEFAULT_CARD


class EdgeScoresPage:
    def __init__(self, username: str = "", card: str | None = None):
        self.username = username
        self.requested = card

    def _menu(self, col, active: str) -> None:
        with col:
            title(t("nav.edge_scores"))
            st.caption(t("edge.menu_hint"))
            for key, label_key, icon, fn in CARDS:
                label = f"{icon} {t(label_key)}"
                if fn is None:
                    st.button(f"{label} · {t('edge.planned')}", key=f"_edge_btn_{key}",
                              width="stretch", disabled=True)
                    continue
                if st.button(label, key=f"_edge_btn_{key}", width="stretch",
                             type="primary" if key == active else "secondary"):
                    if key != active:
                        st.session_state[_STATE_KEY] = key
                        st.rerun()
            st.markdown("---")

    def render(self) -> None:
        active = resolve_card(self.requested, st.session_state.get(_STATE_KEY))
        st.session_state[_STATE_KEY] = active
        menu_col, _, main_col = st.columns([1, 0.1, 4])
        self._menu(menu_col, active)
        renderer = next(fn for k, *_r, fn in CARDS if k == active)
        with main_col:
            try:
                renderer(self.username, menu_col)
            except Exception as e:
                logger.exception("edge_scores: card %s failed", active)
                st.error(t("error.load_edge_scores", error=e))
