"""Holdings of the own portfolios as rows for the market map (treemap).

Three sources, one shape -- ticker, longName, strategy, shares, positionValue
(system currency) and gainPct (since the buy):

  own    trades.db (Scalable import / manual entry), FIFO net shares
  paper  live positions of the paper broker (premium trading bridge)
  multi  open positions of the Multi Strategies run (trades{year}.db)

``enrich`` adds what the treemap colours by: sector and size data from
asset_info, the latest scores from the simulation DB. No Streamlit here.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3

import numpy as np
import pandas as pd

from tradinglib import tools

logger = logging.getLogger(__name__)

SOURCES = ('own', 'paper', 'multi')
COLUMNS = ['ticker', 'longName', 'strategy', 'shares', 'positionValue', 'gainPct']
INFO_COLUMNS = ['longName', 'sector', 'marketCap', 'enterpriseValue', 'ebitdaMargins',
                'totalDebt', 'totalRevenue']
# Size columns in the listing (marketCap, EV) or reporting currency (debt, revenue):
# a portfolio mixes markets, so they go to the system currency like the values.
CAP_COLUMNS = ('marketCap', 'enterpriseValue')
FIN_COLUMNS = ('totalDebt', 'totalRevenue')
SIM_COLUMNS = ['Date', 'close', 'sharpe', 'sortino', 'dTrend', 'wkTrend', 'moTrend', 'momentum']
SIM_WINDOW_DAYS = 15        # the newest sim day is often only partly written


def _path(db_path: str, name: str) -> str:
    return tools.Tools().get_path(path=db_path, file_name=name)


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def _to_system(value: float, currency: str, system_currency: str) -> float:
    if not currency or str(currency).upper() == str(system_currency).upper():
        return value
    from tradinglib.portfolio_analysis import _price_to_system
    return _price_to_system(value, str(currency), system_currency)


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    for c in ('shares', 'positionValue', 'gainPct'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['strategy'] = df['strategy'].fillna('').astype(str)
    return df


# ------------------------------------------------------------------ sources

def _own_trades(db_path: str) -> pd.DataFrame:
    """trades.db buys/sells in the shape scalable_import._compute_open_positions expects."""
    p = _path(db_path, 'trades.db')
    if not os.path.exists(p):
        return pd.DataFrame()
    con = _ro(p)
    try:
        raw = pd.read_sql_query('SELECT * FROM trades', con)
    except Exception:
        return pd.DataFrame()
    finally:
        con.close()
    if raw.empty:
        return raw
    raw.columns = [c.strip() for c in raw.columns]
    lower = {c.lower(): c for c in raw.columns}
    ren = {lower[k]: v for k, v in (('action', 'action'), ('ticker', 'ticker'), ('shares', 'shares'),
                                    ('value', 'amount'), ('currency', 'currency'),
                                    ('timestamp', 'timestamp'), ('longname', 'longname'))
           if k in lower}
    df = raw.rename(columns=ren)
    if 'action' not in df.columns or 'ticker' not in df.columns:
        return pd.DataFrame()
    df['action'] = df['action'].astype(str).str.lower().str.strip()
    df = df[df['action'].isin(['buy', 'sell'])].copy()
    for c, default in (('longname', ''), ('currency', 'EUR'), ('amount', 0.0), ('shares', 0.0)):
        if c not in df.columns:
            df[c] = default
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0.0)
    df['shares'] = pd.to_numeric(df['shares'], errors='coerce').fillna(0.0)
    df['timestamp'] = pd.to_datetime(df.get('timestamp'), errors='coerce')
    return df


def own_positions(db_path: str = 'database', system_currency: str = 'EUR') -> pd.DataFrame:
    """Open positions from trades.db, valued at the latest close."""
    from tradinglib.scalable_import import _compute_open_positions
    trades = _own_trades(db_path)
    if trades.empty:
        return _frame([])
    pos = _compute_open_positions(trades, db_path)
    rows = []
    for r in pos.itertuples():
        rows.append({'ticker': r.ticker, 'longName': r.longname, 'strategy': '', 'shares': r.shares,
                     'positionValue': _to_system(r.current_value, r.currency, system_currency),
                     'gainPct': r.pnl_pct if r.current_price else np.nan})
    return _frame(rows)


def multi_positions(db_path: str = 'database', system_currency: str = 'EUR',
                    year: int | None = None) -> pd.DataFrame:
    """Open positions of the Multi Strategies run of *year* (default: this year).

    The run values open trades at the latest close: sellVolume stays empty,
    sellPriceEUR carries the current price.
    """
    year = year or dt.date.today().year
    p = _path(db_path, f'trades{year}.db')
    if not os.path.exists(p):
        return _frame([])
    con = _ro(p)
    try:
        df = pd.read_sql_query('SELECT * FROM trades WHERE sellVolume IS NULL', con)
    except Exception:
        return _frame([])
    finally:
        con.close()
    if df.empty:
        return _frame([])
    vol = pd.to_numeric(df['buyVolume'], errors='coerce')
    price = pd.to_numeric(df.get('sellPriceEUR', df.get('sellPrice')), errors='coerce')
    buy = pd.to_numeric(df.get('buyPriceEUR', df.get('buyPrice')), errors='coerce')
    value = vol * price
    if str(system_currency).upper() != 'EUR':
        value = value.map(lambda v: _to_system(v, 'EUR', system_currency))
    out = pd.DataFrame({
        'ticker': df['ticker'].astype(str), 'longName': df.get('longName', ''),
        'strategy': df.get('Strategy', ''), 'shares': vol, 'positionValue': value,
        'gainPct': np.where(buy > 0, (price / buy - 1) * 100, np.nan),
    })
    return _frame(out.to_dict('records'))


def paper_positions(username: str = '', db_path: str = 'database', system_currency: str = 'EUR',
                    broker_id: str = 'alpaca') -> pd.DataFrame:
    """Live positions of the paper broker (needs the premium trading bridge)."""
    from tradinglib import ksplib
    from tradinglib.premium.trading_bridge import BrokerFactory, OrderLog
    from tradinglib.system_config import SystemConfig
    cfg = SystemConfig(username=username)
    bcfg = {'alpaca_key': '', 'alpaca_secret': '',
            'ibkr_host': cfg.get_value('ibkr_host', '127.0.0.1'),
            'ibkr_port': cfg.get_value('ibkr_port', '7497')}
    try:
        creds = ksplib.Ksp(storage_path=db_path, secrets_path=db_path).get_ksp(
            cfg.get_value('alpaca_ksp_name', 'av-paper'))
        if isinstance(creds, dict):
            bcfg['alpaca_key'], bcfg['alpaca_secret'] = creds.get('user', ''), creds.get('password', '')
    except Exception:
        logger.debug('paper broker credentials not available', exc_info=True)
    broker = BrokerFactory.create(broker_id, bcfg)
    if not broker.is_connected():
        raise ConnectionError(f'{broker_id} not connected')
    mode = 'live' if broker_id == 'ibkr' else 'paper'
    try:
        strategies = OrderLog(db_path=db_path).get_open_position_strategies(mode, broker_id)
    except Exception:
        strategies = {}
    rows = []
    for p in broker.get_positions():
        rows.append({'ticker': p.ticker, 'longName': '',
                     'strategy': p.strategy or strategies.get(p.ticker) or strategies.get(p.broker_symbol, ''),
                     'shares': p.qty,
                     'positionValue': _to_system(p.market_value, p.currency, system_currency),
                     'gainPct': (p.current_price / p.avg_entry_price - 1) * 100
                     if p.avg_entry_price else np.nan})
    return _frame(rows)


def load(source: str, username: str = '', db_path: str = 'database',
         system_currency: str = 'EUR') -> pd.DataFrame:
    if source == 'own':
        return own_positions(db_path, system_currency)
    if source == 'multi':
        return multi_positions(db_path, system_currency)
    if source == 'paper':
        return paper_positions(username, db_path, system_currency)
    raise ValueError(f'unknown portfolio source {source!r}')


# ------------------------------------------------------------------ enrich

def by_ticker(df: pd.DataFrame) -> pd.DataFrame:
    """One row per ticker: a ticker held by two strategies is one tile.

    Values and shares add up, the gain is weighted by position value.
    """
    if df.empty or not df['ticker'].duplicated().any():
        return df
    work = df.assign(_w=df['positionValue'].fillna(0) * df['gainPct'].fillna(0),
                     _v=df['positionValue'].where(df['gainPct'].notna(), 0).fillna(0))
    out = work.groupby('ticker', as_index=False).agg(
        longName=('longName', 'first'), shares=('shares', 'sum'),
        positionValue=('positionValue', 'sum'), _w=('_w', 'sum'), _v=('_v', 'sum'),
        strategy=('strategy', lambda s: ', '.join(sorted({x for x in s if x}))))
    out['gainPct'] = np.where(out['_v'] > 0, out['_w'] / out['_v'], np.nan)
    return out.drop(columns=['_w', '_v'])[COLUMNS]


def _fx_factor(currency, system_currency: str, cache: dict) -> float:
    """Multiplier from *currency* to the system currency (1.0 if unknown)."""
    cur = str(currency or '').strip()
    if cur in ('GBp', 'GBX'):                 # caps are quoted in pounds, prices in pence
        cur = 'GBP'
    if not cur or cur.lower() in ('none', 'nan') or cur.upper() == system_currency.upper():
        return 1.0
    if cur not in cache:
        try:
            from tradinglib.utils import DataUtils
            rate = DataUtils.get_exchange_rate(symbol=cur, system_currency=system_currency)
            cache[cur] = 1.0 / float(rate) if rate else 1.0
        except Exception:
            cache[cur] = 1.0
    return cache[cur]


def enrich(df: pd.DataFrame, db_path: str = 'database', sim_db: str = 'asset_simulation_.db',
           system_currency: str = 'EUR') -> pd.DataFrame:
    """Add sector/size data (asset_info, sizes in system currency) and the latest scores."""
    out = df.copy()
    tickers = sorted(set(out['ticker']))
    info = pd.DataFrame(columns=['ticker'] + INFO_COLUMNS)
    sim = pd.DataFrame(columns=['ticker'] + SIM_COLUMNS)
    if tickers:
        ph = ','.join('?' * len(tickers))
        p = _path(db_path, 'asset_info.db')
        if os.path.exists(p):
            con = _ro(p)
            try:
                have = {r[1] for r in con.execute('PRAGMA table_info(asset_info)')}
                cols = [c for c in INFO_COLUMNS + ['currency', 'financialCurrency'] if c in have]
                info = pd.read_sql_query(
                    f'SELECT ticker, {", ".join(cols)} FROM asset_info WHERE ticker IN ({ph})', con,
                    params=tickers).drop_duplicates('ticker')
            finally:
                con.close()
            fx = {}
            cur = info.get('currency', pd.Series(index=info.index, dtype=object))
            fin = info.get('financialCurrency', pd.Series(index=info.index, dtype=object)).fillna(cur)
            for cols_, src in ((CAP_COLUMNS, cur), (FIN_COLUMNS, fin)):
                f = src.map(lambda c: _fx_factor(c, system_currency, fx))
                for c in cols_:
                    if c in info.columns:
                        info[c] = pd.to_numeric(info[c], errors='coerce') * f
            info = info.drop(columns=[c for c in ('currency', 'financialCurrency') if c in info.columns])
        p = _path(db_path, sim_db)
        if os.path.exists(p):
            since = (dt.date.today() - dt.timedelta(days=SIM_WINDOW_DAYS)).isoformat()
            con = _ro(p)
            try:
                have = {r[1] for r in con.execute('PRAGMA table_info(asset_simulation)')}
                cols = [c for c in SIM_COLUMNS if c in have]
                sim = pd.read_sql_query(
                    f'SELECT ticker, {", ".join(cols)} FROM asset_simulation '
                    f'WHERE ticker IN ({ph}) AND Date >= ? ORDER BY Date', con,
                    params=tickers + [since]).drop_duplicates('ticker', keep='last')
            finally:
                con.close()
    out = out.merge(info.rename(columns={'longName': '_info_name'}), on='ticker', how='left')
    out = out.merge(sim, on='ticker', how='left')
    for c in INFO_COLUMNS + SIM_COLUMNS:
        if c not in out.columns and c != 'longName':
            out[c] = np.nan
    for c in INFO_COLUMNS[2:] + SIM_COLUMNS[1:]:
        out[c] = pd.to_numeric(out[c], errors='coerce')
    name = out['longName'].fillna('').astype(str).str.strip()
    name = name.where(~name.isin(['', 'None', 'nan']), np.nan)
    out['longName'] = name.fillna(out['_info_name']).fillna(out['ticker']).astype(str)
    out['sector'] = out['sector'].fillna('Other').replace('', 'Other')
    return out.drop(columns=['_info_name'])
