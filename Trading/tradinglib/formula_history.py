"""History of buy/sell formula pairs for the Strategy Finder.

Every Calculate stores the pair (with index and time) per user in config.db;
picking an entry puts both formulas back into the input fields. Newest first,
a repeated pair moves to the top instead of appearing twice.
"""
import json
from datetime import datetime

import streamlit as st

CONFIG_KEY = 'sf_formula_history'
MAX_ENTRIES = 30
_PICK_KEY = '_sf_formula_history_pick'


def load(sys_config) -> list[dict]:
    """Stored entries, newest first; damaged or foreign values read as empty."""
    raw = sys_config.get_value(CONFIG_KEY, '[]')
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [e for e in entries
            if isinstance(e, dict) and isinstance(e.get('buy'), str) and isinstance(e.get('sell'), str)]


def add(sys_config, buy: str, sell: str, index: str = '', now: datetime | None = None) -> list[dict]:
    """Put the pair on top (dropping an older copy) and keep MAX_ENTRIES."""
    buy, sell = (buy or '').strip(), (sell or '').strip()
    entries = load(sys_config)
    if not buy and not sell:
        return entries
    entries = [e for e in entries if (e['buy'].strip(), e['sell'].strip()) != (buy, sell)]
    entries.insert(0, {'buy': buy, 'sell': sell, 'index': index or '',
                       'ts': (now or datetime.now()).strftime('%Y-%m-%d %H:%M')})
    entries = entries[:MAX_ENTRIES]
    sys_config.set_value(CONFIG_KEY, json.dumps(entries, ensure_ascii=False))
    return entries


def _short(text: str, width: int) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= width else text[:width - 1] + '…'


def label(entry: dict) -> str:
    """One line for the picker: time, index, buy and sell, shortened."""
    head = entry.get('ts', '')
    if entry.get('index'):
        head += f" · {entry['index']}"
    return f"{head} · B: {_short(entry['buy'], 40)} · S: {_short(entry['sell'], 30)}"


def seed_from_saved(sys_config) -> list[dict]:
    """Start an empty history with the pair the Finder last saved, if any."""
    entries = load(sys_config)
    if entries:
        return entries
    buy = sys_config.get_value('sf_buy_query', '') or ''
    sell = sys_config.get_value('sf_sell_query', '') or ''
    if isinstance(buy, str) and isinstance(sell, str) and (buy.strip() or sell.strip()):
        return add(sys_config, buy, sell)
    return entries


def picker(region, sys_config, buy_key: str, sell_key: str, title: str,
           placeholder: str, help_text: str = '', empty_text: str = '') -> None:
    """Selectbox over the history; a pick fills the two input widgets.

    Must run before the widgets *buy_key* / *sell_key* are created: the
    callback writes their values into session_state for the coming run, and
    the box falls back to the placeholder so it never re-applies an entry.
    Always shown -- empty and disabled until the first entry exists.
    """
    entries = seed_from_saved(sys_config)
    if not entries:
        region.selectbox(title, [empty_text or placeholder], disabled=True,
                         key=_PICK_KEY + '_empty', help=help_text or None)
        return
    # Numbered, so two entries whose shortened text looks alike stay distinct.
    options = [placeholder] + [f"{i}. {label(e)}" for i, e in enumerate(entries, 1)]
    if st.session_state.get(_PICK_KEY) not in options:
        st.session_state[_PICK_KEY] = placeholder

    def _picked():
        choice = st.session_state.get(_PICK_KEY)
        if choice in options[1:]:
            entry = entries[options.index(choice) - 1]
            st.session_state[buy_key] = entry['buy']
            st.session_state[sell_key] = entry['sell']
        st.session_state[_PICK_KEY] = placeholder

    region.selectbox(title, options, key=_PICK_KEY, on_change=_picked, help=help_text or None)
