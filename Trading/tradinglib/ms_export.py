"""Strategy Finder settings as a Multi Strategies entry.

Multi Strategies reads ``multi_transactions`` as a Python literal
({strategy: {index: {...}}}), edited as text in the settings. This module turns
the Finder's current formula and portfolio settings into that literal for the
selected index, so a tested pair can be pasted instead of retyped.

Every field Multi Strategies would otherwise take from a global setting is
written explicitly (stops, sizing, fractional, ISIN filter): a missing
``stop_loss`` there falls back to the global ``stop_loss_pct``, which would
quietly turn a stop on that the Finder run did not have.
"""
from __future__ import annotations

import streamlit as st

from tradinglib.i18n import t as _t

_SIZING_WITH_FACTOR = ('factor',)


def _num(value):
    """Whole numbers as int, the rest as float (2.0 -> 2, 0.3 -> 0.3)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else f


def entry(*, buy: str, sell: str, num_assets, invest, order_by: str, signal_window=1,
          trailing_stop=0, stop_loss=0, sizing_cap: str = 'none', sizing_factor_max=None,
          fractional: bool = False, require_isin: bool = False, min_hold_days=0,
          cooldown_days=0, currency: str = 'ANY') -> dict:
    """One index entry with the fields Multi Strategies reads, in its usual order."""
    out = {
        'buy': (buy or '').strip(),
        'sell': (sell or '').strip(),
        'num_assets': int(_num(num_assets)),
        'invest': _num(invest),
        'order_by': order_by,
        'signal_window': max(int(_num(signal_window)), 1),
        'trailing_stop': _num(trailing_stop),
        'stop_loss': _num(stop_loss),
        'sizing_cap': sizing_cap or 'none',
    }
    if out['sizing_cap'] in _SIZING_WITH_FACTOR and sizing_factor_max is not None:
        out['sizing_factor_max'] = _num(sizing_factor_max)
    out['fractional'] = bool(fractional)
    out['require_isin'] = bool(require_isin)
    if _num(min_hold_days):
        out['min_hold_days'] = int(_num(min_hold_days))
    if _num(cooldown_days):
        out['cooldown_days'] = int(_num(cooldown_days))
    if currency and currency != 'ANY':
        out['currency'] = currency
    return out


def format_strategy(name: str, index: str, index_entry: dict) -> str:
    """The strategy block as it stands in ``multi_transactions`` (trailing commas included).

    Values go through repr(), so multi-line formulas keep their line breaks as
    ``\\n`` and the text stays a valid literal for ``ast.literal_eval``/``eval``.
    """
    lines = [f'{name!r}: {{', f'    {index!r}: {{']
    lines += [f'        {k!r}: {v!r},' for k, v in index_entry.items()]
    lines += ['    },', '},']
    return '\n'.join(lines)


def render(container, index: str, index_entry: dict, key: str = 'ms_export') -> None:
    """Expander with the strategy name and the ready-to-paste block."""
    with container.expander(_t('msx.title', index=index), expanded=False):
        name = st.text_input(_t('msx.name'), value=_t('msx.name_default'),
                             key=f'{key}_name').strip() or _t('msx.name_default')
        st.code(format_strategy(name, index, index_entry), language='python')
        st.caption(_t('msx.how'))
        st.caption(_t('msx.not_transferred'))
