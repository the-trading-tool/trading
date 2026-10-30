"""Strategy Finder formula history: storage and restoring a pair."""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

from tradinglib import formula_history as fh


class _Conf:
    def __init__(self):
        self.values = {}

    def get_value(self, key, default=None):
        return self.values.get(key, default)

    def set_value(self, key, value):
        self.values[key] = value


def test_neuestes_oben_doppelte_wandern_nach_oben():
    conf = _Conf()
    fh.add(conf, '(rsi > 50)', '(rsi < 50)', '^SPX', now=datetime(2026, 10, 30, 10, 0))
    fh.add(conf, '(ewo > 0)', '(ewo < 0)', '^GDAXI', now=datetime(2026, 10, 30, 11, 0))
    fh.add(conf, ' (rsi > 50) ', '(rsi < 50)', '^SPX', now=datetime(2026, 10, 30, 12, 0))
    entries = fh.load(conf)
    assert [e['buy'] for e in entries] == ['(rsi > 50)', '(ewo > 0)']
    assert entries[0]['ts'] == '2026-10-30 12:00'


def test_hoechstens_30_eintraege_und_leeres_wird_nicht_gespeichert():
    conf = _Conf()
    for i in range(35):
        fh.add(conf, f'(rsi > {i})', '(rsi < 50)')
    fh.add(conf, '', '  ')
    entries = fh.load(conf)
    assert len(entries) == fh.MAX_ENTRIES and entries[0]['buy'] == '(rsi > 34)'


def test_leere_historie_startet_mit_den_gespeicherten_formeln():
    conf = _Conf()
    conf.set_value('sf_buy_query', '(rsi > 50)')
    conf.set_value('sf_sell_query', '(rsi < 50)')
    entries = fh.seed_from_saved(conf)
    assert [(e['buy'], e['sell']) for e in entries] == [('(rsi > 50)', '(rsi < 50)')]
    fh.add(conf, '(ewo > 0)', '(ewo < 0)')
    assert len(fh.seed_from_saved(conf)) == 2                   # seeds only an empty history


def test_kaputter_wert_liest_sich_als_leer():
    conf = _Conf()
    conf.set_value(fh.CONFIG_KEY, '{not json')
    assert fh.load(conf) == []
    conf.set_value(fh.CONFIG_KEY, json.dumps([{'buy': 1}, 'x', {'buy': 'a', 'sell': 'b'}]))
    assert fh.load(conf) == [{'buy': 'a', 'sell': 'b'}]


def _app():
    import streamlit as st
    from tradinglib import formula_history as fh

    class Conf:
        def get_value(self, key, default=None):
            return st.session_state.setdefault('_conf', {}).get(key, default)

        def set_value(self, key, value):
            st.session_state.setdefault('_conf', {})[key] = value

    conf = Conf()
    fh.picker(st.sidebar, conf, 'buy', 'sell', 'History', 'Load…')
    # as in the Strategy Finder: start values via session_state, no value=
    st.session_state.setdefault('buy', '(ewo_angle>20)')
    st.session_state.setdefault('sell', '(ewo_angle<-20)')
    buy = st.sidebar.text_area('Buy', key='buy')
    sell = st.sidebar.text_area('Sell', key='sell')
    if st.sidebar.button('Calculate'):
        fh.add(conf, buy, sell, '^SPX')


def test_auswahl_setzt_beide_formeln_wieder_ein():
    at = AppTest.from_function(_app)
    at.run()
    empty = at.sidebar.selectbox[0]                            # shown, but disabled while empty
    assert empty.disabled and len(empty.options) == 1
    at.sidebar.text_area[0].input('(rsi > 50)')
    at.sidebar.text_area[1].input('(rsi < 50)')
    at.sidebar.button[0].click().run()                         # first calculation stored
    at.sidebar.text_area[0].input('(macd > 0)')
    at.sidebar.text_area[1].input('(macd < 0)').run()
    box = at.sidebar.selectbox[0]
    assert len(box.options) == 2 and box.value == 'Load…'
    box.select(box.options[1]).run()
    assert at.sidebar.text_area[0].value == '(rsi > 50)'
    assert at.sidebar.text_area[1].value == '(rsi < 50)'
    assert at.sidebar.selectbox[0].value == 'Load…'          # falls back, applies only once
    at.sidebar.text_area[0].input('(rsi > 55)').run()          # editing afterwards sticks
    assert at.sidebar.text_area[0].value == '(rsi > 55)' and not at.exception
    assert not [w for w in at.warning if 'Session State' in str(w.value)]   # no warning box
