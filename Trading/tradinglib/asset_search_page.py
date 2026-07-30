"""Asset-Suche — Volltextsuche über asset_info und direkter Sprung zum Asset Viewer.

Findet Assets per FTS5 (Ticker/Name) und öffnet den gewählten Treffer über einen
Button im Asset Viewer (Route ``?asset=true&symbol=<ticker>``).
"""
from __future__ import annotations

import streamlit as st

from tradinglib.search import FullTextSearch
from tradinglib.i18n import t


class AssetSearchPage:
    def __init__(self, username: str = "", db_path: str = "database"):
        self.username = username
        self.db_path = db_path

    def render(self):
        st.markdown(f"## {t('as.title')}")
        st.caption(t("as.subtitle"))

        col_q, col_n = st.columns([0.75, 0.25])
        query = col_q.text_input(t("as.input_label"), key="_as_query",
                                 placeholder=t("as.placeholder"))
        limit = col_n.number_input(t("as.limit"), min_value=5, max_value=100,
                                   value=25, step=5, key="_as_limit")

        q = (query or "").strip()
        if not q:
            st.info(t("as.hint"))
            return

        try:
            results = FullTextSearch(db_path=self.db_path).search(q, limit=int(limit))
        except Exception as e:
            st.error(t("as.error", error=e))
            return

        if not results:
            st.warning(t("as.no_results", query=q))
            return

        st.caption(t("as.count", n=len(results)))
        st.markdown("---")
        for row in results:
            # sqlite3.Row → per Spaltenname; robust gegen Tupel-Fallback
            try:
                ticker = row["ticker"]; name = row["longName"] or ""
            except (KeyError, IndexError, TypeError):
                ticker, name = (list(row) + ["", ""])[:2]
            c1, c2, c3 = st.columns([0.22, 0.58, 0.20])
            c1.markdown(f"**{ticker}**")
            c2.markdown(name or "—")
            if c3.button(t("as.open_btn"), key=f"_as_open_{ticker}",
                         use_container_width=True):
                st.session_state["_nav_params"] = {"asset": "true", "symbol": ticker}
                st.rerun()
