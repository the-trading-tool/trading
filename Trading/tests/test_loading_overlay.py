"""Full-page loading overlay can be switched off per user (config 'loading_overlay')."""
import json
from pathlib import Path

import streamlit as st

from tradinglib import loading_overlay as lo


class _Cfg:
    def __init__(self, value):
        self.value = value

    def __call__(self, username=None, bare_mode=False, **_k):
        return self

    def get_value(self, key, default=None):
        assert key == 'loading_overlay'
        return default if self.value is None else self.value


def test_read_setting(monkeypatch):
    from tradinglib import system_config
    for stored, expect in ((None, True), (True, True), (False, False), ('False', False),
                           ('True', True)):
        monkeypatch.setattr(system_config, 'SystemConfig', _Cfg(stored))
        assert lo.read_setting('kurt') is expect, stored
        assert lo.enabled() is expect
    # no user yet (first load before login): overlay on
    monkeypatch.setattr(system_config, 'SystemConfig', _Cfg(False))
    assert lo.read_setting(None) is True


def test_tab_overlay_follows_the_switch():
    st.session_state['_loading_overlay'] = True
    assert 'position:fixed' in lo.tab_overlay('Charts')
    st.session_state['_loading_overlay'] = False
    html = lo.tab_overlay('Charts')
    assert 'position:fixed' not in html and 'Charts' in html
    st.session_state['_loading_overlay'] = True


def test_spinner_css_is_the_overlay():
    assert 'div[data-testid="stSpinner"]' in lo.SPINNER_CSS and 'position: fixed' in lo.SPINNER_CSS


def test_pages_share_one_overlay():
    from tradinglib import admin, main_page
    assert main_page._tab_overlay is lo.tab_overlay and admin._tab_overlay is lo.tab_overlay


def test_labels_in_both_languages():
    root = Path(__file__).resolve().parent.parent / 'locales'
    for lang in ('de', 'en'):
        d = json.loads((root / f'{lang}.json').read_text(encoding='utf-8'))
        assert 'cfg.loading_overlay' in d and 'cfg.loading_overlay_help' in d
