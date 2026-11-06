"""Mirror the current page route into the address bar.

Navigation lives in st.session_state ('_nav_params'), which a new session does
not have. Android (Chrome and others) discards a background tab after a few
minutes; returning reloads the page, and without the route in the URL the app
opened the start page instead of the last one. asset_analyzer.render() reads
st.query_params when there is no '_nav_params' yet, so a route kept in the URL
survives the reload.
"""
import logging

import streamlit as st

logger = logging.getLogger(__name__)

# Never echoed into the address bar: one-off actions and secrets.
SKIP_PARAMS = frozenset({'stream', 'data', 'download', 'access_token'})


def sync(parms: dict) -> None:
    """Write *parms* into st.query_params (no rerun, address bar only).

    Only rewritten when it differs, so the URL is not touched on every widget rerun.
    """
    want = {str(k): str(v) for k, v in (parms or {}).items()
            if k not in SKIP_PARAMS and v is not None}
    try:
        if st.query_params.to_dict() != want:
            st.query_params.from_dict(want)
    except Exception:
        logger.debug('route -> URL sync failed', exc_info=True)
