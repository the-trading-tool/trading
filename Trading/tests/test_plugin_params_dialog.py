"""The indicator settings dialog persists what the user changed.

The widgets sit in a form so every value is committed with the Save button --
st.number_input alone only commits on Enter/blur, which made a single click on
a plain Save button store the previous value.

Run: .venv/Scripts/python.exe -m pytest tests/test_plugin_params_dialog.py -q
"""
import json
import sqlite3
import textwrap

import pytest

from streamlit.testing.v1 import AppTest

SCHEMA = {
    'wave_atr_mult': {'type': 'float', 'default': 3.0, 'min': 1.0, 'max': 10.0, 'step': 0.5, 'label': 'ATR'},
    'show_abc': {'type': 'bool', 'default': True, 'label': 'ABC'},
    'wave_ewo_basis': {'type': 'select', 'default': 'ewo', 'options': ['ewo', 'joseph_5_35'], 'label': 'Basis'},
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A one-widget app around the dialog, pointed at a scratch database."""
    db = tmp_path / 'database'
    db.mkdir()
    monkeypatch.setenv('TradingDB', str(db))       # never touch the real config.db
    script = tmp_path / 'app.py'
    script.write_text(textwrap.dedent(f'''
        import os
        os.environ['TradingDB'] = {str(db)!r}
        from tradinglib import system_config
        sc = system_config.SystemConfig(username='tester')
        sc.render_plugin_params('ewo', {SCHEMA!r})
    '''))
    return AppTest.from_file(str(script), default_timeout=60).run(), db


def stored(db):
    path = db / 'config.db'
    if not path.exists():
        return None
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as con:
        rows = dict(con.execute("SELECT key, value FROM config WHERE key = 'tester:plugin_params:ewo'"))
    return json.loads(rows['tester:plugin_params:ewo']) if rows else None


def test_one_click_on_save_stores_the_changed_values(app):
    at, db = app
    assert stored(db) is None
    at.number_input[0].set_value(2.0)
    at.checkbox[0].set_value(False)
    at.selectbox[0].set_value('joseph_5_35')
    at.button[0].click().run()
    assert stored(db) == {'wave_atr_mult': 2.0, 'show_abc': False, 'wave_ewo_basis': 'joseph_5_35'}


def test_untouched_values_keep_their_defaults(app):
    at, db = app
    at.number_input[0].set_value(4.5)
    at.button[0].click().run()
    assert stored(db) == {'wave_atr_mult': 4.5, 'show_abc': True, 'wave_ewo_basis': 'ewo'}
