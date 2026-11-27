"""Streamlit tab for the portfolio review (rules live in ``portfolio_review``).

Called from two places with a fixed source each:

* Own Transactions -> ``render(source='own')``
* Trading page     -> ``render(source='paper'|'live')`` following its mode switch

Nothing here sends an order. For own trades a breached stop can be staged as a
sell in the Scalable order basket (preview, sent from the basket by hand).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import streamlit as st

from tradinglib import portfolio_review as pr
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

_LEVEL_ICON = {'check': '🔴', 'note': '🟠', 'data': '🔵'}
_MACRO_ORDER = ('rate', 'cpi', 'unemp', 'gdp', 'cli')
_STOP_TAB = {'own': 'own_trades.tab_risk', 'paper': 'tp.tab_positions', 'live': 'tp.tab_positions'}


# The tab reruns on every widget change (thresholds, signal toggle); positions
# and price histories are the slow part (~3 s warm for 25 positions), so they are
# kept for a few minutes. Short on purpose: a trade should show up soon.
_CACHE_TTL = 300


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _positions_cached(source: str, username: str, db_path: str, system_currency: str,
                      _broker=None):
    # _broker is not hashed (leading underscore); source already tells paper from live
    return pr.load_positions(source, username, db_path, system_currency, broker=_broker)


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _prices_cached(tickers: tuple, db_path: str):
    return pr.load_prices(list(tickers), db_path)


def _profile(username: str) -> dict:
    try:
        from tradinglib import risk_profile as rp
        prof = rp.resolve(username)
        prof['label'] = t(f"risk.name_{prof['name']}")
        return prof
    except Exception:
        logger.debug('portfolio review: no risk profile', exc_info=True)
        return {}


def _signals(tickers: list, username: str, db_path: str, key: str):
    """Strategy signal per ticker, or None when switched off / no formula."""
    try:
        from tradinglib.system_config import SystemConfig
        cfg = SystemConfig(username=username)
        buy_q, sell_q = cfg.get_value('buy_query', ''), cfg.get_value('sell_query', '')
    except Exception:
        buy_q = sell_q = ''
    if not (buy_q or sell_q):
        st.caption(t('pr.signals_none'))
        return None
    if not st.toggle(t('pr.signals_toggle'), value=True, key=f'pr_signals_{key}',
                     help=t('pr.signals_help')):
        return None
    from tradinglib.portfolio_analysis import (SIGNAL_TIMEFRAMES, _compute_position_signals,
                                               signal_timeframe)
    iv, pe = SIGNAL_TIMEFRAMES[signal_timeframe(username)]
    sig, _warn = _compute_position_signals(list(tickers), buy_q, sell_q, db_path, username, iv, pe)
    if not sig:
        # e.g. a formula on a column the live chart path cannot compute
        st.caption(t('pr.signals_failed'))
        return None
    return sig


def _metrics(rv: pr.Review, system_currency: str) -> None:
    m = rv.metrics
    c = st.columns(5)
    c[0].metric(t('pr.m_total'), f"{m['total']:,.0f} {system_currency}")
    c[1].metric(t('pr.m_n'), m['n'])
    vol = m.get('depot_vol')
    c[2].metric(t('pr.m_vol'), f'{vol * 100:.1f} %' if vol is not None and np.isfinite(vol) else '–',
                help=t('pr.m_vol_help'))
    c[3].metric(t('pr.m_largest'), f"{m['largest_weight']:.1f} %", m['largest'] or None,
                delta_color='off')
    c[4].metric(t('pr.m_actions'), m['n_check'] + m['n_note'],
                t('pr.m_actions_val', check=m['n_check'], note=m['n_note']), delta_color='off')


def _findings(rv: pr.Review) -> None:
    st.markdown(f"#### {t('pr.findings_head')}")
    if not rv.findings:
        st.success(t('pr.no_findings'))
        return
    names = dict(zip(rv.positions['ticker'], rv.positions['longName']))
    lines = []
    for f in rv.findings:
        who = f"**{f.ticker}** {names.get(f.ticker, '')}".strip() if f.ticker else f"**{t('pr.depot')}**"
        lines.append(f"- {_LEVEL_ICON[f.level]} `{t('pr.lvl.' + f.level)}` {who}: {f.text()}")
    st.markdown('\n'.join(lines))


def _basket_button(rv: pr.Review, db_path: str) -> None:
    """Own trades only: stage breached stops as sells in the Scalable basket."""
    breached = [f for f in rv.findings if f.rule == 'stop_breached']
    if not breached:
        return
    if not st.button(t('pr.basket_btn', n=len(breached)), key='pr_basket'):
        return
    from tradinglib.scalable_orders import (OrderBasket, drafts_from_position_sells,
                                            isin_map_from_trades)
    held = rv.positions.set_index('ticker')
    isins = isin_map_from_trades(db_path)
    rows = [{'ticker': f.ticker, 'isin': isins.get(str(f.ticker).upper(), ''),
             'shares': float(held.at[f.ticker, 'shares'] or 0),
             'price': float(f.params.get('price') or 0),
             'reason': t('own_trades.trail_basket_reason', stop=f.params.get('stop', 0))}
            for f in breached]
    drafts, skipped = drafts_from_position_sells(rows, allow_network=True)
    added, rejected = OrderBasket(db_path=db_path).add_many(drafts)
    if added:
        st.success(t('own_trades.trail_basket_added', n=added))
    problems = ([f'{tk}: {why}' for tk, why in skipped]
                + [f'{d.ticker}: {"; ".join(p)}' for d, p in rejected])
    if problems:
        st.warning(t('own_trades.trail_basket_skipped', items=', '.join(problems)))


def _clusters(rv: pr.Review) -> None:
    st.markdown(f"#### {t('pr.clusters_head')}")
    c1, c2 = st.columns(2)
    for col, series, label in ((c1, rv.sectors, t('pr.by_sector')),
                               (c2, rv.currencies, t('pr.by_currency'))):
        df = series.round(1).rename('w').reset_index()
        df.columns = ['name', 'w']
        col.dataframe(df, hide_index=True, use_container_width=True, column_config={
            'name': st.column_config.TextColumn(label),
            'w': st.column_config.ProgressColumn('%', min_value=0, max_value=100, format='%.1f'),
        })


def _fmt_macro(row) -> str:
    v, ch = row['value'], row.get('change')
    if row['indicator'] == 'cli':
        s = f'{v:.1f}'
        return s + (f' ({ch:+.2f})' if pd.notna(ch) else '')
    s = f'{v:.2f}'
    return s + (f' ({ch:+.2f})' if pd.notna(ch) else '')


def _market(rv: pr.Review) -> pd.DataFrame:
    st.markdown(f"#### {t('pr.market_head')}")
    st.caption(t('pr.market_caption'))
    lat = pr.load_macro(rv.currencies)
    if lat.empty:
        st.info(t('pr.market_none'))
        return lat
    lat = lat.assign(txt=lat.apply(_fmt_macro, axis=1))
    wide = lat.pivot_table(index='ccy', columns='indicator', values='txt', aggfunc='first')
    wide = wide.reindex(columns=[c for c in _MACRO_ORDER if c in wide.columns])
    wide.insert(0, 'share', lat.groupby('ccy')['weight'].first().round(1))
    wide = wide.sort_values('share', ascending=False).reset_index()
    cfg = {'ccy': st.column_config.TextColumn(t('pr.col_ccy')),
           'share': st.column_config.NumberColumn(t('pr.col_share'), format='%.1f')}
    cfg.update({k: st.column_config.TextColumn(t(f'pr.ind.{k}')) for k in _MACRO_ORDER})
    st.dataframe(wide, hide_index=True, use_container_width=True, column_config=cfg)
    st.caption(t('pr.macro_asof', date=pd.to_datetime(lat['period']).max().strftime('%m/%Y')))
    return lat


def _positions(rv: pr.Review, system_currency: str) -> None:
    st.markdown(f"#### {t('pr.positions_head')}")
    sig_label = {'add': f"🟢 {t('ota.signal_add')}", 'reduce': f"🔴 {t('ota.signal_reduce')}",
                 'hold': f"⚪ {t('ota.signal_hold')}", 'conflict': f"🟠 {t('ota.signal_conflict')}"}
    df = rv.positions.copy()
    df['vol'] = df['vol'] * 100
    df['signal'] = df['signal'].map(lambda s: sig_label.get(s, '') if s else '')
    cols = ['ticker', 'longName', 'sector', 'currency', 'positionValue', 'gainPct', 'weight',
            'vol', 'risk_share', 'dist_stop', 'signal', 'flags']
    if not df['signal'].astype(bool).any():
        cols.remove('signal')
    num = st.column_config.NumberColumn
    st.dataframe(df[cols], hide_index=True, use_container_width=True, column_config={
        'ticker': st.column_config.TextColumn('Ticker'),
        'longName': st.column_config.TextColumn(t('pr.col_name')),
        'sector': st.column_config.TextColumn(t('pr.col_sector')),
        'currency': st.column_config.TextColumn(t('pr.col_currency')),
        'positionValue': num(f"{t('pr.col_value')} {system_currency}", format='%.0f'),
        'gainPct': num(t('pr.col_gain'), format='%.1f'),
        'weight': num(t('pr.col_weight'), format='%.1f'),
        'vol': num(t('pr.col_vol'), format='%.0f', help=t('pr.col_vol_help')),
        'risk_share': num(t('pr.col_risk'), format='%.1f', help=t('pr.col_risk_help')),
        'dist_stop': num(t('pr.col_stop'), format='%.1f'),
        'signal': st.column_config.TextColumn(t('pr.col_signal')),
        'flags': st.column_config.TextColumn(t('pr.col_flags')),
    })


def _settings(username: str) -> dict:
    th = pr.settings(username)
    with st.expander(t('pr.settings')):
        st.caption(t('pr.settings_caption'))
        c = st.columns(len(pr.THRESHOLDS))
        new = {}
        for col, key in zip(c, pr.THRESHOLDS):
            step = 0.5 if key == 'risk_share_factor' else 1.0
            new[key] = col.number_input(t(f'pr.th.{key}'), min_value=0.0, max_value=100.0,
                                        value=float(th[key]), step=step, key=f'pr_th_{key}')
        if st.button(t('pr.th_save'), key='pr_th_save'):
            pr.save_settings(username, new)
            st.success(t('pr.th_saved'))
        return new


def _ai(rv: pr.Review, lat: pd.DataFrame, source: str, username: str) -> None:
    st.markdown(f"#### {t('pr.ai_btn')}")
    st.caption(t('pr.ai_caption'))
    key = f'pr_ai_{source}'
    if st.button(t('pr.ai_btn'), key=f'{key}_btn'):
        macro_txt = ''
        if lat is not None and not lat.empty:
            try:
                from tradinglib import macro_data as md
                from tradinglib.market_overview_page import format_macro_block
                macro_txt = format_macro_block(lat.drop(columns=['weight', 'txt'], errors='ignore'),
                                               md.get_meta('last_success'))
            except Exception:
                logger.debug('portfolio review: macro block failed', exc_info=True)
        prompt = pr.build_prompt(rv, t(f'pr.src.{source}'), macro_txt)
        with st.spinner(t('pr.ai_spinner')):
            try:
                from tradinglib.ai_client import AiClient
                client = AiClient(username=username)
                text = client.run_question(prompt, max_tokens=1200)
                st.session_state[key] = (text, client.provider_name, client.model_used)
            except Exception as exc:
                st.session_state.pop(key, None)
                st.error(t('pr.ai_error', err=exc))
    if key in st.session_state:
        text, prov, model = st.session_state[key]
        st.markdown(text)
        st.caption(t('pr.ai_model', provider=prov, model=model))


def render(source: str, username: str = '', db_path: str = 'database',
           system_currency: str = 'EUR', broker=None) -> None:
    """The whole tab for one source ('own', 'paper', 'live', 'multi').

    *broker*: the trading page's connected broker, reused for paper/live.
    """
    st.markdown(f"### {t('pr.title')} · {t(f'pr.src.{source}')}")
    st.caption(t('pr.caption'))
    try:
        with st.spinner(t('pr.computing')):
            positions = _positions_cached(source, username, db_path, system_currency, broker)
    except Exception as exc:
        logger.warning('portfolio review: positions of %s failed', source, exc_info=True)
        st.error(t('pr.load_error', err=exc))
        return
    if positions.empty:
        st.info(t('pr.empty'))
        return

    thresholds = _settings(username)
    profile = _profile(username)
    tickers = positions['ticker'].tolist()
    signals = _signals(tickers, username, db_path, source)
    with st.spinner(t('pr.computing')):
        prices = _prices_cached(tuple(tickers), db_path)
        if source == 'own':
            stops = pr.own_stops(positions, db_path, system_currency)
        elif source in pr.BROKER_OF:
            stops = pr.broker_trail_stops(prices, source, db_path)
        else:
            stops = None
        rv = pr.review(positions, prices, stops=stops,
                       stop_tab=t(_STOP_TAB[source]) if source in _STOP_TAB else '',
                       signals=signals, quality=pr.load_quality(tickers, db_path),
                       profile=profile, thresholds=thresholds)

    st.caption(t('pr.profile_line', profile=profile.get('label') or '–',
                 date=rv.metrics.get('price_date') or '–'))
    _metrics(rv, system_currency)
    _findings(rv)
    if source == 'own':
        _basket_button(rv, db_path)
    elif source in pr.BROKER_OF:
        st.caption(t('pr.trail_note_broker'))
    _clusters(rv)
    lat = _market(rv)
    _positions(rv, system_currency)
    _ai(rv, lat, source, username)
