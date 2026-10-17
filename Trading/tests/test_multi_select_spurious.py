"""Overlay/oscillator selection: a form, saved only on an explicit click.

2026-10-17: kurt's overlay list had shrunk to ["sup"], the oscillators to
["rsi"], and the no-plot lists had grown to nearly every indicator. Streamlit
re-fires on_change without a click (rerun after a code change, widget
re-created after a run that did not render it); each spurious "off" removed one
entry through the per-checkbox writers. Indicators now sit in a form (no rerun
per click) and persist only through the "apply + save" submit button.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

from tradinglib import multi_select as ms


def _app():
    import streamlit as st
    from tradinglib import multi_select as ms_

    class Conf:
        store = st.session_state.setdefault('_conf_store', {
            'overlay': ['heikin', 'sup'], 'oszilator': ['rsi', 'ewo'],
            'overlay_no_plot': [], 'oszilator_no_plot': ['ewo'],
            'interval': '1d', 'period': '1y'})

        def get_value(self, key, default=None):
            return self.store.get(key, default)

        def set_value(self, key, value):
            self.store[key] = value

        def get_plugin_params(self, name):
            return {}

        def render_plugin_params(self, name, schema):
            st.session_state['_dialog'] = (name, sorted(schema)[:1])

    lists = [('Interval', ['1d', '1wk']), ('Period', ['1y', '2y']),
             ('Overlay', ['heikin', 'sup', 'atc']), ('Oszilator', ['rsi', 'ewo', 'macd'])]
    sel = ms_.MultiCheckboxSelector(list_data=lists, sys_conf=Conf())
    sel.render()
    st.session_state['_result'] = sel.get_selected_options()
    st.session_state['_plot'] = sel.get_plot_options()


def _uid(at):
    return at.session_state['1_uuid']


def test_config_vorgaben_und_plot_fuer_alle():
    at = AppTest.from_function(_app).run()
    assert not at.exception
    assert at.session_state['_result']['Overlay'] == ['heikin', 'sup']
    assert at.session_state['_plot']['Oszilator'] == ['rsi']          # ewo selected, not plotted
    uid = _uid(at)
    assert at.checkbox(key=f'plot_Overlay_atc_{uid}').value          # Plot shown for unselected too


def test_klicks_im_formular_wirken_erst_beim_uebernehmen():
    at = AppTest.from_function(_app).run()
    uid = _uid(at)
    at.checkbox(key=f'Overlay_atc_{uid}').check()
    at.checkbox(key=f'Overlay_sup_{uid}').uncheck()
    at.button(key=f'apply_Overlay_{uid}').click().run()
    assert at.session_state['_result']['Overlay'] == ['heikin', 'atc']
    assert at.session_state['_conf_store']['overlay'] == ['heikin', 'sup']   # not saved
    at.run()                                                                  # plain rerun keeps it
    assert at.session_state['_result']['Overlay'] == ['heikin', 'atc']


def test_speichern_schreibt_auswahl_und_plot():
    at = AppTest.from_function(_app).run()
    uid = _uid(at)
    at.checkbox(key=f'Oszilator_macd_{uid}').check()
    at.checkbox(key=f'plot_Oszilator_rsi_{uid}').uncheck()
    at.checkbox(key=f'plot_Oszilator_ewo_{uid}').check()
    at.button(key=f'save_Oszilator_{uid}').click().run()
    store = at.session_state['_conf_store']
    assert store['oszilator'] == ['rsi', 'ewo', 'macd']
    assert store['oszilator_no_plot'] == ['rsi']
    assert store['overlay'] == ['heikin', 'sup']                              # other form untouched


def test_zahnrad_uebernimmt_und_oeffnet_den_dialog():
    at = AppTest.from_function(_app).run()
    uid = _uid(at)
    at.checkbox(key=f'Overlay_atc_{uid}').check()
    at.button(key=f'cfg_Overlay_heikin_{uid}').click().run()
    assert not at.exception
    assert at.session_state['_dialog'][0] == 'heikin'
    assert 'atc' in at.session_state['_result']['Overlay']                    # form applied


class _State(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def test_meldung_ohne_vorherige_anzeige_wird_ignoriert(monkeypatch):
    """Guard for the Interval/Period callbacks, which still persist on change."""
    state = _State()
    monkeypatch.setattr(ms.st, 'session_state', state)
    sel = ms.MultiCheckboxSelector.__new__(ms.MultiCheckboxSelector)
    sel._init_key, sel._shown_key, sel._shown_at_key = 'i', 's', 'a'
    key = 'Interval_1d_x'
    state.update({'i': 5, 'a': 5, 's': {key: True}})
    state[key] = False
    assert sel._is_user_change(key)             # rendered last run as on, now off: a click
    state[key] = True
    assert not sel._is_user_change(key)         # same value as displayed: spurious re-fire
    state.update({'i': 6, 'a': 5})              # last run built the selector but did not render it
    state[key] = False
    assert not sel._is_user_change(key)
    state.update({'i': 6, 'a': 6, 's': {}})     # widget was not displayed
    assert not sel._is_user_change(key)
