"""The current page is mirrored into the URL, so a reload (Android discards
background tabs) returns to it instead of the start page."""
from streamlit.testing.v1 import AppTest


def _app():
    import os
    import sys
    import streamlit as st
    sys.path.insert(0, os.getcwd())
    from tradinglib import route_url
    route_url.sync(st.session_state.get('route', {}))


def _run(route, url=None):
    at = AppTest.from_function(_app, default_timeout=120)
    for k, v in (url or {}).items():
        at.query_params[k] = v
    at.session_state['route'] = route
    at.run()
    assert not at.exception, at.exception
    return {k: v[0] if isinstance(v, list) else v for k, v in at.query_params.items()}


def test_route_goes_into_the_url():
    assert _run({'strategy_finder': 'true'}) == {'strategy_finder': 'true'}


def test_navigation_replaces_the_old_route():
    assert _run({'multi': 'true'}, url={'strategy_finder': 'true'}) == {'multi': 'true'}


def test_one_off_and_secret_params_stay_out():
    assert _run({'admin': 'true', 'download': 'x.xlsx', 'access_token': 'abc'}) == {'admin': 'true'}
