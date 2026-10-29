"""Sidebar picker for external links: opens the chosen page once, then resets."""
import json

import streamlit as st

_KEY = '_ext_link_select'
_OPEN = '_ext_link_open'


def link_picker(region, links: dict, label: str, placeholder: str) -> str | None:
    """Render the picker; return the URL to open on this run, else None.

    The choice opens the link once and the box falls back to the placeholder.
    Keeping the choice selected re-opened the page on every rerun, i.e. on any
    click anywhere in the app.
    """
    options = [placeholder] + list(links.keys())
    if st.session_state.get(_KEY) not in options:
        st.session_state[_KEY] = placeholder          # e.g. after a language switch

    def _chosen():
        choice = st.session_state.get(_KEY)
        if choice in links:
            st.session_state[_OPEN] = links[choice]
        st.session_state[_KEY] = placeholder

    region.selectbox(label, options, key=_KEY, on_change=_chosen)
    return st.session_state.pop(_OPEN, None)


def open_in_new_tab(url: str) -> None:
    """Open *url* in a new browser tab from the Streamlit page."""
    js = f'window.parent.window.open({json.dumps(url)}, "_blank");'
    st.components.v1.html(f'<script>{js}</script>', height=0)
