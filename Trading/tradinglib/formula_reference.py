"""Column reference for buy/sell formulas.

The formula fields accept any column of the loaded frame, but nothing on the
page said which ones exist or what they mean. ``render`` puts example formulas
and a searchable, grouped list under the fields: the columns actually present
(so it never offers something the run cannot see), the tokens that are computed
or joined on demand, and a short syntax note.

Descriptions live in the locales (``fref.c.<column>``, groups ``fref.g.<group>``).
A column the catalog does not know still shows up, under "other", without a
description -- a new indicator is visible before anyone documents it.
"""
from __future__ import annotations

import streamlit as st

from tradinglib.i18n import t as _t

# Display order of the groups and the columns they hold.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('price', ('close', 'Open', 'High', 'Low', 'ath', 'lyHigh', 'lyLow')),
    ('ma', ('sma20', 'sma50', 'sma200', 'ema9', 'ema21', 'ema50', 'ema9_angle')),
    ('trend', ('dTrend', 'wkTrend', 'moTrend', 'trendDirection', 'trendScore')),
    ('momentum', ('rsi', 'rsi_ema', 'momentum', 'momentum_ema', 'momentum_ema_angle',
                  'cci', 'zcr')),
    ('macd', ('macd', 'macd_signal', 'macd_diff', 'macd_trend', 'macd_trend_wk',
              'macd_trend_mo')),
    ('ewo', ('ewo', 'ewo_ema', 'ewo_diff', 'ewo_angle', 'ewo_trend', 'ewo_trend_day',
             'ewo_trend_wk', 'ewo_trend_mo', 'ewo_wk', 'ewo_mo')),
    ('adx', ('adx', 'adx_plus', 'adx_minus', 'adx_angle')),
    ('volume', ('relvol_ratio', 'relvol_ratio_wk', 'relvol_ratio_mo', 'relvol_direction',
                'relvol_direction_wk', 'relvol_direction_mo')),
    ('sup', ('sup_support', 'sup_resistance', 'sup_support_wk', 'sup_resistance_wk',
             'sup_support_mo', 'sup_resistance_mo')),
    ('atc', ('atc_top_high', 'atc_mid_high', 'atc_mid_zero', 'atc_bot_low')),
    ('heikin', ('ha_close', 'ha_open', 'ha_ema_high', 'ha_ema_low')),
    ('dema', ('dema_ema_fast', 'dema_ema_slow', 'dema_buy', 'dema_sell')),
    ('regime', ('markov_regime', 'markov_regime_wk', 'markov_regime_mo')),
    ('rft', ('rft_kalman', 'rft_supertrend', 'rft_upper', 'rft_lower', 'rft_trend',
             'rft_ktrend', 'rft_state', 'rft_signal')),
    ('fps', ('fps_phase', 'fps_setup', 'fps_buy', 'fps_sell', 'fps_breakout', 'fps_base_high',
             'fps_base_low', 'fps_base_weeks', 'fps_best_trend', 'fps_trend_gain', 'fps_stop',
             'fps_target', 'fps_rs', 'fps_dist_high')),
    ('mtt', ('mtt_pass', 'mtt_score', 'mtt_rs', 'mtt_rs_raw', 'mtt_sma150', 'mtt_slope200',
             'mtt_high52', 'mtt_low52')),
    ('scr', ('scr_1mo', 'scr_3mo', 'scr_eoy', 'scr_buy', 'scr_sell')),
    ('misc', ('hor_val', 'hor_threshold', 'fvg', 'qtrend_entry_price')),
    ('risk', ('vola', 'logVola', 'semiVola', 'atr', 'logRet', 'sharpe', 'sortino', 'riskScore',
              'riskBucket', 'stop_loss', 'take_profit', 'buySell')),
    ('snapshot', ('pctTargetHighPrice', 'predictedHigh', 'predictedLow', 'roa', 'beta',
                  'lastDividendValue', 'targetHighPrice', 'targetMeanPrice', 'targetLowPrice',
                  'ebitdaMargins', 'revenueGrowth', 'marketCap', 'totalDebt', 'totalRevenue',
                  'enterpriseValue', 'recommendationKey')),
    ('master', ('sector', 'industry', 'exchange', 'currency')),
)

# Joined from market_context when a formula names them; never in the frame beforehand.
ON_DEMAND: tuple[str, ...] = ('mkt_breadth200', 'mkt_breadth50', 'mkt_breadth_chg', 'fg_score')

# Measured 2026-10-31: these carry information from after the bar they sit on
# (today's asset_info snapshot, or a weekly value that already knows the week's close).
LOOKAHEAD = frozenset({
    'pctTargetHighPrice', 'predictedHigh', 'predictedLow', 'roa', 'macd_trend_wk',
    'beta', 'lastDividendValue', 'targetHighPrice', 'targetMeanPrice', 'targetLowPrice',
    'ebitdaMargins', 'revenueGrowth', 'marketCap', 'totalDebt', 'totalRevenue',
    'enterpriseValue', 'recommendationKey',
})

# Example pairs (key, buy, sell). Titles and explanations: fref.ex.<key>.title/.desc.
# Patterns for the syntax, not measured strategies. Every pair was checked to
# evaluate and fire on real data (^GDAXI + ^SPX 2025); test_formula_reference
# keeps them parseable.
EXAMPLES: tuple[tuple[str, str, str], ...] = (
    ('channel', '(Low <= atc_bot_low) & (ewo >= ewo_ema) & (close > Open)',
     '(High >= atc_top_high) & (rsi >= 75)'),
    ('dip', '(close <= sup_support * 1.02) & (close > sma200) & (rsi < 40)',
     '(close >= sup_resistance) | (rsi >= 70)'),
    ('ewo_cross', '(close > sma200) & (ewo > ewo_ema) & (ewo.shift(1) <= ewo_ema.shift(1))',
     '(ewo < ewo_ema)'),
    ('breakout', '(close >= High.rolling(21).max().shift(1)) & (relvol_ratio > 1.5) '
                 '& (adx > 25) & (adx_plus > adx_minus)',
     '(close < ema21) | (adx_plus < adx_minus)'),
    ('multiline', '(close <= sup_support * 1.02)\n(relvol_ratio > 1)\n(macd > macd_signal)',
     '(High >= atc_top_high)\n(rsi >= 72)'),
    ('ema_calc', '(ema100 > ema100.shift(5)) & (close < ema21) & (rsi7 < 30)', '(rsi7 > 70)'),
    ('atr', '(atr / close <= 0.03) & (close > ema50) & (rsi < 45)', '(rsi >= 65)'),
    ('risk', '(riskBucket >= 1) & (riskBucket <= 2) & (trendScore >= 75) & (close > sma50)',
     '(close < sma50)'),
    ('fps', '(fps_breakout == 1) & (fps_setup >= 3)', '(fps_sell > 0)'),
    ('rft', '(rft_signal == 1) & (markov_regime == 1)', '(rft_state < 0) | (rft_ktrend < 0)'),
    ('breadth', '(mkt_breadth50 <= 25) & (close > sma200) & (rsi < 35)', '(rsi >= 65)'),
    ('season', '(scr_1mo >= 30) & (close > sma50)', '(scr_1mo <= -30)'),
)

# Identifiers and free text: not useful in a formula.
HIDDEN = frozenset({'Date', 'ticker', 'stockIndex', 'index_name', 'shortName', 'longName',
                    'longBusinessSummary', 'isin', 'ISIN'})

_GROUP_OF = {c: g for g, cols in GROUPS for c in cols}


def known_columns() -> list[str]:
    """Every column the catalog describes, in display order."""
    return [c for _g, cols in GROUPS for c in cols] + list(ON_DEMAND)


def catalog(columns) -> list[tuple[str, list[str]]]:
    """Group *columns* for display: catalog order first, unknown ones under 'other'.

    Columns not in *columns* are left out -- the list shows what this run can use.
    """
    present = [c for c in dict.fromkeys(columns if columns is not None else [])
               if c not in HIDDEN]
    have = set(present)
    out = []
    for group, cols in GROUPS:
        found = [c for c in cols if c in have]
        if found:
            out.append((group, found))
    other = sorted((c for c in present if c not in _GROUP_OF and c not in ON_DEMAND),
                   key=str.lower)
    if other:
        out.append(('other', other))
    return out


def ordered_columns(columns) -> list[str]:
    """All of *columns*, re-ordered so each indicator's columns sit side by side.

    Identifiers (Date, ticker, names) keep their place at the front, then the
    catalog groups in display order, then unknown columns alphabetically -- which
    still keeps a new indicator's ``prefix_*`` columns together. Nothing is dropped.
    """
    # Not `columns or []`: a pandas Index has no truth value.
    cols = list(dict.fromkeys(columns if columns is not None else []))
    lead = [c for c in cols if c in HIDDEN]
    grouped = [c for _g, found in catalog(cols) for c in found]
    rest = [c for c in cols if c not in set(lead) | set(grouped)]
    return lead + grouped + rest


def describe(column: str) -> str:
    """Locale description of *column*, or '' when none exists."""
    key = f'fref.c.{column}'
    text = _t(key)
    return '' if text == key else text


def _matches(column: str, group: str, query: str) -> bool:
    if not query:
        return True
    q = query.lower()
    return (q in column.lower() or q in describe(column).lower()
            or q in _t(f'fref.g.{group}').lower())


def _line(column: str) -> str:
    desc = describe(column)
    mark = ' ⚠' if column in LOOKAHEAD else ''
    return f'`{column}`{mark} — {desc}' if desc else f'`{column}`{mark}'


def _apply_example(buy_key: str, sell_key: str, buy: str, sell: str) -> None:
    # Button callback: runs before the next script run, so the formula widgets
    # (created earlier on the page) pick the values up without a warning.
    st.session_state[buy_key] = buy
    st.session_state[sell_key] = sell


def _render_examples(key: str, buy_key: str | None, sell_key: str | None) -> None:
    placeholder = _t('fref.examples_placeholder')
    titles = {_t(f'fref.ex.{k}.title'): (k, b, s) for k, b, s in EXAMPLES}
    choice = st.selectbox(_t('fref.examples'), [placeholder, *titles], key=f'{key}_example')
    if choice not in titles:
        st.caption(_t('fref.examples_note'))
        return
    ex_key, buy, sell = titles[choice]
    st.caption(_t(f'fref.ex.{ex_key}.desc'))
    st.markdown(f'**{_t("fref.buy")}**')
    st.code(buy, language=None)
    st.markdown(f'**{_t("fref.sell")}**')
    st.code(sell, language=None)
    if buy_key and sell_key:
        st.button(_t('fref.apply'), key=f'{key}_apply', on_click=_apply_example,
                  args=(buy_key, sell_key, buy, sell), use_container_width=True)
    st.caption(_t('fref.examples_note'))


def render(container, columns, key: str = 'fref', buy_key: str | None = None,
           sell_key: str | None = None) -> None:
    """Expander with example formulas, the grouped column list, a filter and a syntax note.

    With *buy_key*/*sell_key* (the session keys of the formula fields) each
    example gets a button that puts it into both fields.
    """
    groups = catalog(columns)
    n = sum(len(cols) for _g, cols in groups)
    with container.expander(_t('fref.title', n=n), expanded=False):
        _render_examples(key, buy_key, sell_key)
        st.divider()
        query = st.text_input(_t('fref.filter'), key=f'{key}_filter',
                              placeholder=_t('fref.filter_placeholder')).strip()
        shown = 0
        for group, cols in groups:
            hits = [c for c in cols if _matches(c, group, query)]
            if not hits:
                continue
            shown += len(hits)
            st.markdown(f'**{_t(f"fref.g.{group}")}**  \n' + '  \n'.join(_line(c) for c in hits))
        demand = [c for c in ON_DEMAND if _matches(c, 'context', query)]
        if demand:
            shown += len(demand)
            st.markdown(f'**{_t("fref.g.context")}**  \n' + '  \n'.join(_line(c) for c in demand))
        if not shown:
            st.caption(_t('fref.no_match'))
        if any(c in LOOKAHEAD for _g, cols in groups for c in cols):
            st.caption(_t('fref.lookahead_note'))
        st.markdown(_t('fref.syntax_md'))
