"""External-link picker: opens once, then falls back to the placeholder."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest


def _app():
    import streamlit as st
    from tradinglib import external_links as ext
    links = {'Tradegate': 'https://www.tradegate.de', 'Yahoo': 'https://finance.yahoo.com'}
    url = ext.link_picker(st.sidebar, links, 'External links', 'Choose...')
    st.session_state.setdefault('opened', [])
    if url:
        st.session_state['opened'].append(url)


def test_link_oeffnet_einmal_und_auswahl_springt_zurueck():
    at = AppTest.from_function(_app)
    at.run()
    assert at.session_state['opened'] == []
    at.sidebar.selectbox[0].select('Tradegate').run()
    assert at.session_state['opened'] == ['https://www.tradegate.de']
    assert at.sidebar.selectbox[0].value == 'Choose...'
    at.run()                                       # any later rerun (a click elsewhere)
    at.run()
    assert at.session_state['opened'] == ['https://www.tradegate.de']


def test_derselbe_link_laesst_sich_erneut_oeffnen():
    at = AppTest.from_function(_app)
    at.run()
    at.sidebar.selectbox[0].select('Yahoo').run()
    at.sidebar.selectbox[0].select('Yahoo').run()
    assert at.session_state['opened'] == ['https://finance.yahoo.com'] * 2


def test_unbekannter_alter_wert_wird_zurueckgesetzt():
    at = AppTest.from_function(_app)
    at.session_state['_ext_link_select'] = 'Wählen...'   # placeholder of the other language
    at.run()
    assert at.sidebar.selectbox[0].value == 'Choose...' and not at.exception
