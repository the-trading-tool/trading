"""Signal badge per chart of the asset summary (no Streamlit).

Own signal first: the latest buy/sell marker the chart drew from the user's
buy/sell formula (column 'position', see indicator.buy_sell), with its date.
Without one in the chart's period the analysts' recommendation from the Yahoo
info (asset_info.recommendationKey), dated by when that info was fetched.
"""
from __future__ import annotations

import logging
import os
import sqlite3

import pandas as pd

from tradinglib.tools import Tools

logger = logging.getLogger(__name__)

YAHOO_KINDS = ('strong_buy', 'buy', 'hold', 'underperform', 'sell')
_COLORS = {'strong_buy': '#14773a', 'buy': '#2e9e4f', 'hold': '#8a6d00',
           'underperform': '#d7301f', 'sell': '#a50f15'}


def own_signal(df: pd.DataFrame) -> dict | None:
    """Latest chart marker: {'source': 'own', 'kind': 'buy'|'sell', 'date', 'price'} or None."""
    if df is None or df.empty or 'position' not in df.columns:
        return None
    pos = pd.to_numeric(df['position'], errors='coerce').fillna(0)
    hits = pos[pos != 0]
    if hits.empty:
        return None
    i = hits.index[-1]
    date = df.loc[i, 'Date'] if 'Date' in df.columns else i
    price = df.loc[i, 'Close'] if 'Close' in df.columns else None
    return {'source': 'own', 'kind': 'buy' if hits.iloc[-1] > 0 else 'sell',
            'date': pd.to_datetime(date, errors='coerce'),
            'price': float(price) if price is not None and pd.notna(price) else None}


def yahoo_signals(tickers, db_path: str = 'database') -> dict:
    """{ticker: {'source': 'yahoo', 'kind', 'date', 'mean', 'n'}} for tickers with a rating."""
    tickers = [str(t) for t in tickers if t]
    path = Tools().get_path(path=db_path, file_name='asset_info.db')
    if not tickers or not os.path.exists(path):
        return {}
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            ph = ','.join('?' * len(tickers))
            df = pd.read_sql_query(
                'SELECT ticker, recommendationKey, recommendationMean, numberOfAnalystOpinions, '
                f'timestamp FROM asset_info WHERE ticker IN ({ph})', con, params=tickers)
        finally:
            con.close()
    except Exception:
        logger.debug('yahoo recommendations not readable', exc_info=True)
        return {}
    out = {}
    for r in df.drop_duplicates('ticker', keep='last').itertuples():
        kind = str(r.recommendationKey or '').strip().lower()
        if kind not in YAHOO_KINDS:
            continue
        mean = pd.to_numeric(r.recommendationMean, errors='coerce')
        n = pd.to_numeric(r.numberOfAnalystOpinions, errors='coerce')
        out[r.ticker] = {'source': 'yahoo', 'kind': kind,
                         'date': pd.to_datetime(r.timestamp, errors='coerce'),
                         'mean': float(mean) if pd.notna(mean) else None,
                         'n': int(n) if pd.notna(n) else None}
    return out


def _fmt_date(d, intraday: bool) -> str:
    if d is None or pd.isna(d):
        return '–'
    return d.strftime('%d.%m.%Y %H:%M' if intraday and (d.hour or d.minute) else '%d.%m.%Y')


def badge_html(sig: dict | None, t) -> str:
    """One-line badge; *t* is the i18n function."""
    if not sig:
        return (f'<div style="font-size:0.8rem;opacity:0.7;margin:2px 0">'
                f'{t("sig.none")}</div>')
    color = _COLORS.get(sig['kind'], '#5c6370')
    label = t(f"sig.kind_{sig['kind']}")
    if sig['source'] == 'own':
        detail = t('sig.own_detail', date=_fmt_date(sig['date'], True),
                   price='' if sig.get('price') is None else f" @ {sig['price']:,.2f}")
    else:
        extra = []
        if sig.get('mean') is not None:
            extra.append(f"Ø {sig['mean']:.1f}")
        if sig.get('n'):
            extra.append(t('sig.analysts', n=sig['n']))
        detail = t('sig.yahoo_detail', date=_fmt_date(sig['date'], False),
                   extra=(' · '.join(extra) + ' · ') if extra else '')
    return (f'<div style="font-size:0.8rem;margin:2px 0">'
            f'<span style="background:{color};color:#fff;padding:1px 8px;border-radius:3px;'
            f'font-weight:600">{label}</span> <span style="opacity:0.8">{detail}</span></div>')
