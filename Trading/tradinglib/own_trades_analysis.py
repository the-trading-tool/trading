import datetime as dt
import logging

import numpy as np
import pandas as pd
import streamlit as st
import uuid

from tradinglib import tools, ticker_tools as tt
try:
    from tradinglib.premium import asset_simulator as ass
except ImportError:
    ass = None
from tradinglib.i18n import t as _t

# Sub-module re-exports — backward-compatible (asset_analyzer.py imports these by name)
from tradinglib.portfolio_analysis import (  # noqa: F401
    render_portfolio_analysis,
    _get_current_price,          # used by render_risk_management below
)
from tradinglib.scalable_import import render_scalable_import  # noqa: F401

logger = logging.getLogger(__name__)



# ─────────────────────────────────────────────────────────────────────────────
# Risk management helpers (trailing stops + ATR; _get_current_price re-exported
# from tradinglib.portfolio_analysis above)
# ─────────────────────────────────────────────────────────────────────────────

def _get_atr_for_own_trades(ticker: str, db_path: str = 'database') -> float:
    """Return the most recent ATR for a ticker from the simulation DB."""
    import os
    from tradinglib.tools import Tools
    candidates = ['asset_simulation_.db'] + [
        f'asset_simulation_{y}.db' for y in range(dt.datetime.now().year, dt.datetime.now().year - 4, -1)
    ] + ['asset_simulation_all.db']
    for fname in candidates:
        full = Tools().get_path(path=db_path, file_name=fname)
        if os.path.exists(full) and os.path.getsize(full) > 4096:
            # All simulation DBs store their data in a single table named
            # 'asset_simulation' (no filename-derived suffix).
            try:
                import sqlite3
                with sqlite3.connect(full) as conn:
                    row = conn.execute(
                        "SELECT atr FROM asset_simulation WHERE ticker=? ORDER BY date DESC LIMIT 1",
                        (ticker,)
                    ).fetchone()
                if row and row[0]:
                    return float(row[0])
            except Exception:
                pass
    return 0.0


def _get_open_positions_for_trails(db_path: str = 'database') -> list[dict]:
    """Return open positions from trades.db as [{ticker, avg_price, shares}]."""
    try:
        import sqlite3
        from tradinglib.tools import Tools
        db_file = Tools().get_path(path=db_path, file_name='trades.db')
        with sqlite3.connect(db_file) as conn:
            raw = pd.read_sql_query('SELECT * FROM trades ORDER BY timestamp ASC', conn)
    except Exception:
        return []
    if raw is None or raw.empty:
        return []

    raw.columns = [c.strip() for c in raw.columns]
    _col = {c.lower(): c for c in raw.columns}

    act_col = _col.get('action', 'action')
    if act_col not in raw.columns:
        return []
    raw[act_col] = raw[act_col].astype(str).str.lower().str.strip()

    ticker_col = _col.get('ticker', 'ticker')
    shares_col = _col.get('shares', 'shares')
    price_col  = _col.get('price',  'price')
    value_col  = _col.get('value',  'value')

    if ticker_col not in raw.columns:
        return []

    raw['_ticker'] = raw[ticker_col].astype(str).str.upper().str.strip()
    raw['_shares'] = pd.to_numeric(raw.get(shares_col, pd.Series()), errors='coerce').fillna(0)
    raw['_price']  = pd.to_numeric(raw.get(price_col,  pd.Series()), errors='coerce').fillna(0)
    raw['_value']  = pd.to_numeric(raw.get(value_col,  pd.Series()), errors='coerce').fillna(0)

    buys  = raw[raw[act_col] == 'buy'].copy()
    sells = raw[raw[act_col] == 'sell'].copy()

    buy_agg  = buys.groupby('_ticker',  as_index=False).agg(buy_sh=('_shares', 'sum'), buy_val=('_value', lambda x: abs(x).sum()))
    sell_agg = sells.groupby('_ticker', as_index=False).agg(sell_sh=('_shares', 'sum'))
    net = buy_agg.merge(sell_agg, on='_ticker', how='left')
    net['sell_sh'] = net['sell_sh'].fillna(0)
    net['open_sh'] = net['buy_sh'] - net['sell_sh']
    open_net = net[net['open_sh'] > 0.001]

    result = []
    for _, row in open_net.iterrows():
        avg = row['buy_val'] / row['buy_sh'] if row['buy_sh'] > 0 else 0.0
        result.append({'ticker': row['_ticker'], 'avg_price': round(avg, 6), 'shares': row['open_sh']})
    return result


def _ensure_own_trades_trails_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS own_trades_trails (
            ticker          TEXT PRIMARY KEY,
            entry_price     REAL,
            high_water_mark REAL,
            trail_stop      REAL,
            atr             REAL,
            atr_mult        REAL,
            last_price      REAL,
            breached        INTEGER DEFAULT 0,
            updated_at      TEXT
        )
    """)


class OwnTradesManager:
    """Lightweight manager for own trades so pages can import and use it.

    Provides load_and_process (wraps analyze_own_trades) and a simple render() to
    display counts and a preview. This is intentionally minimal so it won't
    trigger network calls or heavy processing at import time.
    """
    def __init__(self, simulator=None, sys_config=None, system_currency='EUR', db_path='database', db_name='trades.db', username=''):
        """Initialize the manager with optional simulator, config, and database references."""
        self.simulator = simulator
        self.sys_config = sys_config
        self.system_currency = system_currency
        self.db_path = db_path
        self.db_name = db_name
        self.username = username
        self.raw = pd.DataFrame()
        self.trades_df = pd.DataFrame()

    def load_and_process(self, asset_date=None, use_year=None):
        """Call analyze_own_trades and cache raw and processed results in self.raw / self.trades_df."""
        try:
            res = analyze_own_trades(db_path=self.db_path, db_name=self.db_name, asset_date=asset_date, system_currency=self.system_currency, year=use_year)
            self.raw = res.get('df', pd.DataFrame())
            self.trades_df = res.get('processed', pd.DataFrame())
            return self.raw, self.trades_df
        except Exception:
            self.raw = pd.DataFrame()
            self.trades_df = pd.DataFrame()
            return self.raw, self.trades_df

    def render(self, region=st):
        """Render a simple row-count summary and data preview for the loaded trades."""
        st_region = region
        try:
            row_count = 0 if self.raw is None else len(self.raw)
            st_region.write(f'OwnTradesManager: raw rows: {row_count}, processed rows: {len(self.trades_df) if self.trades_df is not None else 0}')
            if self.trades_df is not None and not self.trades_df.empty:
                st_region.dataframe(self.trades_df.head(20))
            else:
                st_region.info(_t('ota.no_processed_trades'))
        except Exception:
            pass


def analyze_own_trades(db_path='database', db_name='trades.db', asset_date=None, system_currency='EUR', year='', disable_streamlit=False):
    """
    Read trades from the local trades DB, process them into paired buy/sell rows
    and value open trades at the latest close price.

    Returns a dict with:
      - df: raw rows read from the trades table (may be empty)
      - processed: DataFrame with paired trades and derived columns
      - table_info: PRAGMA table_info result (list of tuples) or []
    """
    dbt = tools.Db_tools(db_path=db_path, database_name=db_name)
    db_table = 'trades'
    raw = pd.DataFrame()
    try:
        query = f"SELECT * FROM {db_table} ORDER BY timestamp DESC"
        raw = pd.read_sql_query(query, dbt.conn)
    except Exception:
        raw = pd.DataFrame()

    # Attempt to get table info for diagnostics
    tbl_info = []
    try:
        if dbt.cursor:
            dbt.cursor.execute("PRAGMA table_info('trades')")
            tbl_info = dbt.cursor.fetchall()
    except Exception:
        tbl_info = []

    # Use existing TradeProcessor to pair buys/sells
    processed = pd.DataFrame()
    try:
        proc = ass.TradeProcessor(data=raw)
        proc.process_trades()
        processed = proc.processed_data if proc.processed_data is not None else pd.DataFrame()
    except Exception:
        processed = pd.DataFrame()

    # Determine default sell date if not provided — use current date so open trades are valued to today
    default_sell_date = asset_date or dt.datetime.now().strftime("%Y-%m-%d 00:00:00")

    # Prepare a lightweight ticker tools instance to fetch intraday close when needed
    tt_tools = tt.TickerTools()

    # Value open trades (no sellDate or NaN sellVolume) using latest close
    if not processed.empty:
        for idx, row in processed.iterrows():
            sell_date = row.get('sellDate')
            sell_vol = row.get('sellVolume')
            fill_open = False
            if pd.isna(sell_date) or sell_date in (None, ''):
                fill_open = True
            if not fill_open and (sell_vol is None or (isinstance(sell_vol, float) and pd.isna(sell_vol)) or sell_vol == 0):
                fill_open = True

            if fill_open:
                # fetch latest minute data for ticker
                close_val = 0
                try:
                    df_min, info = tt_tools.fetch_yahoo_ticker_data(symbol=row.get('ticker', ''), period='1d', interval='1m')
                    currency = info.get('currency') if isinstance(info, dict) else system_currency
                    x_rate = tt_tools.get_exchange_rate(symbol=currency or system_currency, system_currency=system_currency)
                    if isinstance(df_min, pd.DataFrame) and not df_min.empty:
                        close_val = round(df_min['Close'].iloc[-1] / (x_rate or 1), 2)
                except Exception:
                    close_val = 0

                processed.at[idx, 'sellDate'] = default_sell_date
                processed.at[idx, 'sellPrice'] = close_val

        # Compute gain and percentage where possible
        try:
            processed['gainPct'] = round((1 - (processed['buyPrice']) / processed['sellPrice']) * 100, 1)
            processed['gain'] = round((processed['sellPrice'] - processed['buyPrice']) * processed['buyVolume'])
        except Exception:
            pass

        if 'ticker' not in processed.columns and 'Ticker' in processed.columns:
            processed['ticker'] = processed['Ticker'].astype(str)

        try:
            processed = processed.sort_values(['sellDate', 'ticker'], ascending=[True, True])
            processed['cumulative_gain'] = processed['gain'].cumsum()
        except Exception:
            pass

    return {'df': raw, 'processed': processed, 'table_info': tbl_info}


def df_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Convert a DataFrame to Excel bytes for download."""
    from io import BytesIO
    bio = BytesIO()
    try:
        with pd.ExcelWriter(bio, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False)
        return bio.getvalue()
    except Exception:
        return b''


def import_trades(df_u: pd.DataFrame, user_map: dict = None, username: str = '', db_path='database', db_name='trades.db'):
    """
    Import a DataFrame of uploaded trades into the trades DB.

    - df_u: raw uploaded DataFrame
    - user_map: optional mapping {canonical_field: source_column} where canonical_field is one of
      timestamp, action, ticker, price, shares, value, longName, isin, currency, stockIndex

    Returns (inserted_count, mapped_df, processed_df)
    """
    # expected canonical fields
    expected_keys = ['timestamp','action','ticker','price','shares','value','longName','isin','currency','stockIndex']

    # auto-mapping heuristics
    def _auto_map(col_name, candidates):
        """Heuristically match col_name to the best fitting column name from candidates."""
        n = col_name.lower()
        alternatives = {
            'timestamp': ['timestamp', 'date', 'datetime', 'time'],
            'action': ['action', 'type', 'side'],
            'ticker': ['ticker', 'symbol', 'isin', 'asset'],
            'price': ['price', 'buyprice', 'sellprice', 'price_per_share', 'pricepershare', 'px'],
            'shares': ['shares', 'volume', 'qty', 'quantity'],
            'value': ['value', 'total', 'amount'],
            'longname': ['longname', 'long_name', 'name', 'company'],
            'isin': ['isin', 'isin_code'],
            'currency': ['currency', 'curr'],
            'stockindex': ['stockindex', 'index']
        }
        pats = alternatives.get(n, [])
        for p in pats:
            for c in candidates:
                if c.lower() == p:
                    return c
        for p in pats:
            for c in candidates:
                if p in c.lower():
                    return c
        return None

    cols = list(df_u.columns)
    mapping = {}
    if user_map:
        mapping = user_map.copy()
    else:
        for k in expected_keys:
            mapping[k] = _auto_map(k, cols)

    # Construct mapped DataFrame
    mapped = pd.DataFrame()
    for k in expected_keys:
        src = mapping.get(k)
        if src and src in df_u.columns:
            mapped[k] = df_u[src]
        else:
            mapped[k] = None

    # Normalize timestamp
    if 'timestamp' in mapped.columns:
        try:
            mapped['timestamp'] = pd.to_datetime(mapped['timestamp'])
        except Exception:
            pass

    # compute value if missing
    if 'value' not in mapped.columns or mapped['value'].isnull().all():
        if 'price' in mapped.columns and 'shares' in mapped.columns:
            try:
                mapped['value'] = pd.to_numeric(mapped['price'], errors='coerce') * pd.to_numeric(mapped['shares'], errors='coerce')
            except Exception:
                mapped['value'] = None

    # normalize action and numeric fields
    try:
        mapped['action'] = mapped['action'].astype(str).str.lower().fillna('')
    except Exception:
        mapped['action'] = mapped.get('action', pd.Series([''] * len(mapped)))

    mapped['shares'] = pd.to_numeric(mapped.get('shares', pd.Series([0]*len(mapped))), errors='coerce').fillna(0)
    mapped['price'] = pd.to_numeric(mapped.get('price', pd.Series([0]*len(mapped))), errors='coerce').fillna(0)
    mapped['value'] = pd.to_numeric(mapped.get('value', pd.Series([pd.NA]*len(mapped))), errors='coerce')

    mapped['buyVolume'] = mapped.apply(lambda r: r['shares'] if str(r.get('action','')).lower() == 'buy' else 0, axis=1)
    mapped['sellVolume'] = mapped.apply(lambda r: r['shares'] if str(r.get('action','')).lower() == 'sell' else 0, axis=1)
    mapped['buyPrice'] = mapped.apply(lambda r: r['price'] if str(r.get('action','')).lower() == 'buy' else None, axis=1)
    mapped['sellPrice'] = mapped.apply(lambda r: r['price'] if str(r.get('action','')).lower() == 'sell' else None, axis=1)
    mapped['buyValue'] = mapped.apply(lambda r: r['value'] if str(r.get('action','')).lower() == 'buy' else 0, axis=1)
    mapped['sellValue'] = mapped.apply(lambda r: r['value'] if str(r.get('action','')).lower() == 'sell' else 0, axis=1)

    # ensure ticker
    if 'ticker' in mapped.columns:
        mapped['ticker'] = mapped['ticker'].astype(str)
    elif 'symbol' in mapped.columns:
        mapped['ticker'] = mapped['symbol'].astype(str)
    else:
        mapped['ticker'] = ''

    # sanitize column names to avoid duplicate column errors
    cols_list = list(mapped.columns)
    seen = {}
    new_cols = []
    for c in cols_list:
        base = c
        if base in seen:
            idx = seen[base] + 1
            new_name = f"{base}_{idx}"
            while new_name in seen or new_name in new_cols:
                idx += 1
                new_name = f"{base}_{idx}"
            seen[base] = idx
            new_cols.append(new_name)
        else:
            seen[base] = 0
            new_cols.append(base)
    if new_cols != cols_list:
        col_map = {old: new for old, new in zip(cols_list, new_cols)}
        mapped = mapped.rename(columns=col_map)

    # Insert into DB
    dbt = tools.Db_tools(db_path=db_path, database_name=db_name)
    sample_row = mapped.iloc[0].to_dict() if not mapped.empty else {}
    # ensure sample_row includes a uuid so table creation adds the column
    try:
        if 'uuid' not in sample_row:
            sample_row['uuid'] = uuid.uuid4().hex
    except Exception:
        sample_row['uuid'] = uuid.uuid4().hex

    # Ensure the trades table has all columns including uuid before inserts
    final_keys = list(mapped.columns)
    if 'uuid' not in final_keys:
        final_keys.append('uuid')
    # make uuid first so new tables use uuid as primary key when created
    final_keys = ['uuid'] + [k for k in final_keys if k != 'uuid']
    dbt.ensure_table_and_columns(keys=final_keys, row_dict=sample_row, database_name='trades')

    # detect primary key column in existing table (if present)
    pk_col = None
    try:
        dbt.cursor.execute("PRAGMA table_info('trades')")
        info = dbt.cursor.fetchall()
        for col in info:
            # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
            if len(col) >= 6 and col[5] == 1:
                pk_col = col[1]
                break
    except Exception:
        pk_col = None
    inserted = 0
    # Keep a counter for duplicates to create unique timestamps if timestamp is PK
    ts_counter = {}
    for _, r in mapped.iterrows():
        row_dict = {}
        for k, v in r.to_dict().items():
            try:
                if pd.isna(v):
                    row_dict[k] = None
                else:
                    # Convert pandas.Timestamp to string for sqlite
                    if hasattr(v, 'to_pydatetime') or isinstance(v, pd.Timestamp):
                        try:
                            row_dict[k] = pd.to_datetime(v).strftime('%Y-%m-%d %H:%M:%S')
                        except Exception:
                            row_dict[k] = str(v)
                    # numpy scalar types -> native python types
                    elif isinstance(v, (np.integer,)):
                        row_dict[k] = int(v)
                    elif isinstance(v, (np.floating,)):
                        row_dict[k] = float(v)
                    else:
                        # lists -> json handled in insert_data; keep strings/numbers as-is
                        row_dict[k] = v
            except Exception:
                row_dict[k] = None

        # ensure a unique uuid for each inserted row to avoid primary-key collisions
        try:
            if 'uuid' not in row_dict or not row_dict.get('uuid'):
                row_dict['uuid'] = uuid.uuid4().hex
        except Exception:
            row_dict['uuid'] = uuid.uuid4().hex

        try:
            # If DB primary key is timestamp, ensure uniqueness by appending a small counter
            try:
                if pk_col == 'timestamp' and row_dict.get('timestamp'):
                    ts0 = str(row_dict.get('timestamp'))
                    cnt = ts_counter.get(ts0, 0)
                    if cnt > 0:
                        # Append a small suffix to create a distinct value for the PK
                        row_dict['timestamp'] = f"{ts0}_{cnt}"
                    ts_counter[ts0] = cnt + 1
            except Exception:
                pass

            # Use replace=False to avoid replacing existing rows
            dbt.insert_data(keys=list(row_dict.keys()), row_dict=row_dict, database_name='trades', replace=False)
            inserted += 1
        except Exception as e:
            logger.warning(f'Could not insert mapped row: {e}')
    dbt.conn.commit()
    try:
        dbt.close()
    except Exception:
        pass

    # Process mapped rows for preview
    processed = pd.DataFrame()
    try:
        proc = ass.TradeProcessor(data=mapped, username=username)
        proc.process_trades()
        processed = proc.processed_data
    except Exception:
        processed = pd.DataFrame()

    return inserted, mapped, processed


# ─────────────────────────────────────────────────────────────────────────────
def render_trade_entry(region=st, db_path: str = 'database', system_currency: str = 'EUR'):
    """Tab for manually entering a single trade (buy or sell) into trades.db."""
    r = region
    r.markdown(f"### {_t('own_trades.entry_header')}")

    with r.form('trade_entry_form', clear_on_submit=True):
        col1, col2 = st.columns(2)
        trade_date = col1.date_input(_t('own_trades.entry_date'), value=dt.date.today(), format='YYYY-MM-DD')
        trade_time = col2.text_input(_t('own_trades.entry_time'), value='12:00')

        col3, col4 = st.columns(2)
        action = col3.selectbox(_t('own_trades.entry_action'), options=['buy', 'sell'])
        ticker = col4.text_input(_t('own_trades.entry_ticker'), placeholder='e.g. AAPL')

        col5, col6, col7 = st.columns(3)
        shares = col5.number_input(_t('own_trades.entry_shares'), min_value=0.0, step=1.0, format='%f')
        price = col6.number_input(_t('own_trades.entry_price'), min_value=0.0, step=0.01, format='%f')
        currency = col7.text_input(_t('own_trades.entry_currency'), value=system_currency)

        col_sl, col_tp = st.columns(2)
        stop_loss   = col_sl.number_input(_t('own_trades.entry_stop_loss'),   min_value=0.0, step=0.01, format='%f', value=0.0)
        take_profit = col_tp.number_input(_t('own_trades.entry_take_profit'), min_value=0.0, step=0.01, format='%f', value=0.0)

        col8, col9 = st.columns(2)
        longname = col8.text_input(_t('own_trades.entry_longname'), placeholder='e.g. Apple Inc.')
        isin = col9.text_input(_t('own_trades.entry_isin'), placeholder='e.g. US0378331005')

        submitted = st.form_submit_button(_t('own_trades.entry_submit'), type='primary')

    if submitted:
        ticker = ticker.strip().upper()
        if not ticker:
            r.error(_t('own_trades.entry_ticker_required'))
        elif shares <= 0:
            r.error(_t('own_trades.entry_shares_positive'))
        elif price <= 0:
            r.error(_t('own_trades.entry_price_positive'))
        else:
            try:
                time_str = trade_time.strip() if trade_time.strip() else '12:00'
                timestamp = f'{trade_date.strftime("%Y-%m-%d")} {time_str}:00'
                value = round(shares * price, 6)

                row_dict = {
                    'uuid':        uuid.uuid4().hex,
                    'timestamp':   timestamp,
                    'action':      action,
                    'ticker':      ticker,
                    'shares':      shares,
                    'price':       price,
                    'value':       value,
                    'currency':    currency.strip().upper() or system_currency,
                    'longName':    longname.strip() or None,
                    'isin':        isin.strip().upper() or None,
                    'stop_loss':   stop_loss   if stop_loss   > 0 else None,
                    'take_profit': take_profit if take_profit > 0 else None,
                }

                dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
                try:
                    dbt.ensure_table_and_columns(
                        keys=list(row_dict.keys()),
                        row_dict=row_dict,
                        database_name='trades',
                    )
                    dbt.insert_data(
                        keys=list(row_dict.keys()),
                        row_dict=row_dict,
                        database_name='trades',
                        replace=False,
                    )
                    dbt.conn.commit()
                finally:
                    try:
                        dbt.close()
                    except Exception:
                        pass

                r.success(_t('own_trades.entry_success',
                             action=action, shares=shares, ticker=ticker,
                             price=price, currency=currency))
            except Exception as e:
                r.error(_t('own_trades.entry_error', error=e))

    # ── Position Sizing Calculator ────────────────────────────────────────────
    with r.expander(_t('own_trades.sizing_header'), expanded=False):
        sz_c1, sz_c2 = st.columns(2)
        sz_account  = sz_c1.number_input(_t('own_trades.sizing_account'),   min_value=0.0, value=10000.0, step=500.0, format='%.2f')
        sz_risk_pct = sz_c2.number_input(_t('own_trades.sizing_risk_pct'), min_value=0.01, max_value=100.0, value=1.0, step=0.1, format='%.2f')
        sz_c3, sz_c4, sz_c5 = st.columns(3)
        sz_entry  = sz_c3.number_input(_t('own_trades.sizing_entry'),       min_value=0.0, value=0.0, step=0.01, format='%f')
        sz_stop   = sz_c4.number_input(_t('own_trades.sizing_stop'),        min_value=0.0, value=0.0, step=0.01, format='%f')
        sz_tp     = sz_c5.number_input(_t('own_trades.sizing_take_profit'), min_value=0.0, value=0.0, step=0.01, format='%f')

        if sz_entry > 0 and sz_stop > 0:
            if sz_stop >= sz_entry:
                st.warning(_t('own_trades.sizing_invalid'))
            else:
                risk_per_share  = sz_entry - sz_stop
                risk_amount     = sz_account * (sz_risk_pct / 100.0)
                rec_shares      = int(risk_amount / risk_per_share)
                pos_value       = rec_shares * sz_entry
                m1, m2, m3 = st.columns(3)
                m1.metric(_t('own_trades.sizing_result_shares'), f'{rec_shares:,}')
                m2.metric(_t('own_trades.sizing_result_value'),  f'{pos_value:,.2f}')
                if sz_tp > sz_entry:
                    reward = sz_tp - sz_entry
                    rr     = round(reward / risk_per_share, 2)
                    m3.metric(_t('own_trades.sizing_risk_reward'), f'1 : {rr}')
        else:
            st.caption(_t('own_trades.sizing_no_stop'))

    # Recent trades preview
    try:
        dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
        try:
            df_recent = pd.read_sql_query(
                'SELECT timestamp, action, ticker, shares, price, value, currency, longName, isin '
                'FROM trades ORDER BY timestamp DESC LIMIT 20',
                dbt.conn,
            )
        except Exception:
            df_recent = pd.DataFrame()
        finally:
            try:
                dbt.close()
            except Exception:
                pass

        if not df_recent.empty:
            r.markdown(f'**{_t("own_trades.entry_recent_header")}**')
            _recent_disp = df_recent.copy()
            if 'ticker' in _recent_disp.columns:
                _recent_disp.insert(0, 'details', _recent_disp['ticker'].apply(lambda t: f'/?symbol={t}&details=True'))
            r.dataframe(_recent_disp, hide_index=True, use_container_width=True,
                        column_config={
                            'details':   st.column_config.LinkColumn('Details', display_text='View'),
                            'timestamp': st.column_config.TextColumn('Date/Time'),
                            'shares':    st.column_config.NumberColumn(format='%.4f'),
                            'price':     st.column_config.NumberColumn(format='%.4f'),
                            'value':     st.column_config.NumberColumn(format='%.2f'),
                        })
    except Exception:
        pass


def render_risk_management(region=st, db_path: str = 'database', system_currency: str = 'EUR'):
    """Risk Management tab: position sizing calculator + trailing stop management."""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
    r = region

    # ── 1. Position Sizing Calculator ──────────────────────────────────────────
    r.markdown(f'### {_t("own_trades.sizing_header")}')
    sz_c1, sz_c2 = r.columns(2)
    sz_account  = sz_c1.number_input(_t('own_trades.sizing_account'),   min_value=0.0, value=10000.0, step=500.0, format='%.2f', key='rm_account')
    sz_risk_pct = sz_c2.number_input(_t('own_trades.sizing_risk_pct'), min_value=0.01, max_value=100.0, value=1.0, step=0.1, format='%.2f', key='rm_risk_pct')
    sz_c3, sz_c4, sz_c5 = r.columns(3)
    sz_entry = sz_c3.number_input(_t('own_trades.sizing_entry'),        min_value=0.0, value=0.0, step=0.01, format='%f', key='rm_entry')
    sz_stop  = sz_c4.number_input(_t('own_trades.sizing_stop'),         min_value=0.0, value=0.0, step=0.01, format='%f', key='rm_stop')
    sz_tp    = sz_c5.number_input(_t('own_trades.sizing_take_profit'),  min_value=0.0, value=0.0, step=0.01, format='%f', key='rm_tp')

    if sz_entry > 0 and sz_stop > 0:
        if sz_stop >= sz_entry:
            r.warning(_t('own_trades.sizing_invalid'))
        else:
            risk_per_share = sz_entry - sz_stop
            risk_amount    = sz_account * (sz_risk_pct / 100.0)
            rec_shares     = int(risk_amount / risk_per_share)
            pos_value      = rec_shares * sz_entry
            m1, m2, m3 = r.columns(3)
            m1.metric(_t('own_trades.sizing_result_shares'), f'{rec_shares:,}')
            m2.metric(_t('own_trades.sizing_result_value'),  f'{pos_value:,.2f} {system_currency}')
            if sz_tp > sz_entry:
                rr = round((sz_tp - sz_entry) / risk_per_share, 2)
                m3.metric(_t('own_trades.sizing_risk_reward'), f'1 : {rr}')
    else:
        r.caption(_t('own_trades.sizing_no_stop'))

    r.divider()

    # ── 2. Trailing Stop Management ────────────────────────────────────────────
    r.markdown(f'### {_t("own_trades.trail_header")}')
    r.caption(_t('own_trades.trail_caption'))

    trail_c1, trail_c2, _ = r.columns([1, 1, 2])
    atr_mult = trail_c1.number_input(
        _t('own_trades.trail_atr_mult'),
        min_value=0.5, max_value=10.0, value=2.5, step=0.1, format='%.1f',
        key='rm_trail_mult',
    )
    do_update = trail_c2.button(_t('own_trades.trail_update_btn'), key='rm_trail_update')

    if do_update:
        positions = _get_open_positions_for_trails(db_path)
        if not positions:
            r.info(_t('own_trades.trail_no_positions'))
        else:
            tickers = [p['ticker'] for p in positions]
            with st.spinner('Loading prices …'):
                with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as ex:
                    futs = {ex.submit(_get_current_price, t, db_path): t for t in tickers}
                    prices = {}
                    for fut in _as_completed(futs):
                        tk = futs[fut]
                        try:
                            prices[tk] = fut.result()
                        except Exception:
                            prices[tk] = 0.0

            now_ts = dt.datetime.now().isoformat()
            try:
                from tradinglib.tools import Tools
                db_file = Tools().get_path(path=db_path, file_name='trades.db')
                with sqlite3.connect(db_file) as conn:
                    _ensure_own_trades_trails_table(conn)
                    for pos in positions:
                        ticker  = pos['ticker']
                        entry   = pos['avg_price']
                        current = prices.get(ticker, 0.0)
                        atr     = _get_atr_for_own_trades(ticker, db_path)

                        if current <= 0:
                            continue

                        row = conn.execute(
                            "SELECT high_water_mark FROM own_trades_trails WHERE ticker=?",
                            (ticker,)
                        ).fetchone()
                        prev_hwm = float(row[0]) if row and row[0] else entry
                        new_hwm  = max(prev_hwm, current)

                        if atr > 0:
                            trail_stop = round(new_hwm - atr_mult * atr, 4)
                        else:
                            trail_stop = round(new_hwm * 0.92, 4)

                        breached = 1 if current <= trail_stop else 0
                        conn.execute("""
                            INSERT INTO own_trades_trails
                                (ticker, entry_price, high_water_mark, trail_stop,
                                 atr, atr_mult, last_price, breached, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(ticker) DO UPDATE SET
                                entry_price     = excluded.entry_price,
                                high_water_mark = excluded.high_water_mark,
                                trail_stop      = excluded.trail_stop,
                                atr             = excluded.atr,
                                atr_mult        = excluded.atr_mult,
                                last_price      = excluded.last_price,
                                breached        = excluded.breached,
                                updated_at      = excluded.updated_at
                        """, (ticker, entry, new_hwm, trail_stop, atr, atr_mult, current, breached, now_ts))
            except Exception as e:
                r.error(f'Error updating trails: {e}')

            r.success(_t('own_trades.trail_updated', n=len([p for p in positions if prices.get(p['ticker'], 0) > 0])))

    # ── Display stored trail state ─────────────────────────────────────────────
    try:
        from tradinglib.tools import Tools
        db_file = Tools().get_path(path=db_path, file_name='trades.db')
        with sqlite3.connect(db_file) as conn:
            _ensure_own_trades_trails_table(conn)
            trails_df = pd.read_sql_query(
                'SELECT ticker, entry_price, last_price, high_water_mark, trail_stop, '
                'atr, atr_mult, breached, updated_at FROM own_trades_trails ORDER BY ticker',
                conn,
            )
    except Exception:
        trails_df = pd.DataFrame()

    if trails_df.empty:
        r.info(_t('own_trades.trail_no_positions'))
    else:
        # Compute derived columns
        trails_df['gain_pct']     = np.where(
            trails_df['entry_price'] > 0,
            ((trails_df['last_price'] / trails_df['entry_price']) - 1) * 100, 0.0
        ).round(2)
        trails_df['dist_to_trail'] = np.where(
            trails_df['trail_stop'] > 0,
            ((trails_df['last_price'] / trails_df['trail_stop']) - 1) * 100, 0.0
        ).round(2)
        trails_df['atr_x_mult'] = (trails_df['atr'] * trails_df['atr_mult']).round(4)

        breached_rows = trails_df[trails_df['breached'] == 1]
        ok_rows       = trails_df[trails_df['breached'] == 0]
        if not breached_rows.empty:
            for _, brow in breached_rows.iterrows():
                r.warning(_t('own_trades.trail_breached',
                             ticker=brow['ticker'],
                             current=brow['last_price'],
                             stop=brow['trail_stop']))
        elif not trails_df.empty:
            r.success(_t('own_trades.trail_all_ok', n=len(trails_df)))

        def _trail_style(row):
            dist = row.get('dist_to_trail', 999)
            if row.get('breached', 0):
                return ['background-color: #f8d7da'] * len(row)
            if dist < 5:
                return ['background-color: #fff3cd'] * len(row)
            return [''] * len(row)

        disp = trails_df[['ticker', 'entry_price', 'last_price', 'high_water_mark',
                           'trail_stop', 'gain_pct', 'dist_to_trail', 'atr', 'atr_mult',
                           'atr_x_mult', 'updated_at']].copy()
        r.dataframe(
            disp.style.apply(_trail_style, axis=1),
            hide_index=True,
            use_container_width=True,
            column_config={
                'ticker':          st.column_config.TextColumn('Ticker'),
                'entry_price':     st.column_config.NumberColumn(_t('own_trades.trail_col_entry'),   format='%.4f'),
                'last_price':      st.column_config.NumberColumn(_t('own_trades.trail_col_current'), format='%.4f'),
                'high_water_mark': st.column_config.NumberColumn(_t('own_trades.trail_col_hwm'),     format='%.4f'),
                'trail_stop':      st.column_config.NumberColumn(_t('own_trades.trail_col_stop'),    format='%.4f'),
                'gain_pct':        st.column_config.NumberColumn(_t('own_trades.trail_col_gain'),    format='%.2f'),
                'dist_to_trail':   st.column_config.NumberColumn(_t('own_trades.trail_col_dist'),
                                       help='% above trailing stop', format='%.2f'),
                'atr':             st.column_config.NumberColumn(_t('own_trades.trail_col_atr'),     format='%.4f'),
                'atr_mult':        st.column_config.NumberColumn('Mult.', format='%.1f'),
                'atr_x_mult':      st.column_config.NumberColumn('ATR × Mult.',
                                       help='ATR × multiplier = distance subtracted from the high-water mark', format='%.4f'),
                'updated_at':      st.column_config.TextColumn(_t('own_trades.trail_col_updated')),
            },
        )


def render_import_export(region, simulator=None, username='', db_path='database'):
    """Render the Streamlit Import/Export UI for own trades.

    Delegates mapping/import to import_trades and shows previews and basic export/delete actions.
    region: Streamlit module or region (usually st)
    simulator: AssetSimulator instance (optional) used to run parity analysis on imported trades
    """
    if region is None:
        return

    st_region = region
    # Show processed/pairing preview from DB immediately (helps when no upload is provided)
    try:
        try:
            proc_res = analyze_own_trades(db_path=db_path, db_name='trades.db', system_currency='EUR')
            proc_preview = proc_res.get('processed', pd.DataFrame())
        except Exception:
            proc_preview = pd.DataFrame()
        if proc_preview is not None and not proc_preview.empty:
            st_region.write('Processed / paired trades (from DB):')
            try:
                st_region.dataframe(proc_preview.head(20))
            except Exception:
                st_region.write(proc_preview.head(20).to_string())
            # allow analyzing the stored processed trades
            try:
                if simulator is not None and st_region.button(_t('ota.analyze_stored'), key='analyze_stored'):
                    try:
                        simulator.render_parity(proc_preview, suffix='stored', invest=100000, region=st_region)
                    except Exception as e:
                        st_region.error(f'Failed to run parity analysis: {e}')
            except Exception:
                pass
    except Exception:
        pass
    uploaded = st_region.file_uploader(_t('ota.upload_label'), type=['csv','xlsx'], key='upload_trades_file')
    df_u = None
    if uploaded is not None:
        try:
            if uploaded.name.lower().endswith('.xlsx'):
                df_u = pd.read_excel(uploaded)
            else:
                df_u = pd.read_csv(uploaded)
        except Exception as e:
            st_region.error(_t('ota.upload_error', error=e))
            df_u = None

    if df_u is not None:
        df_u.columns = [c.strip() for c in df_u.columns]
        try:
            st_region.write(_t('ota.upload_preview'))
            st_region.dataframe(df_u.head())
        except Exception:
            pass


        # OwnTradesManager is provided at module level; instantiate it from the page when needed.


        # simple auto-mapping UI (only show when a file was uploaded)
        if df_u is not None:
            expected = [
                ('timestamp', _t('ota.map_col_timestamp')),
                ('action',    _t('ota.map_col_action')),
                ('ticker',    _t('ota.map_col_ticker')),
                ('price',     _t('ota.map_col_price')),
                ('shares',    _t('ota.map_col_shares')),
                ('value',     _t('ota.map_col_value')),
                ('longName',  _t('ota.map_col_longname')),
                ('isin',      _t('ota.map_col_isin')),
                ('currency',  _t('ota.map_col_currency')),
                ('stockIndex', _t('ota.map_col_index')),
            ]

            cols = list(df_u.columns)
            user_map = {}
            for key, label in expected:
                # naive default: match exact or contain
                default = None
                for c in cols:
                    if c.lower() == key or key in c.lower() or c.lower() == label.lower():
                        default = c
                        break
                sel = st_region.selectbox(f'{label}', options=['(none)'] + cols, index=(cols.index(default)+1) if default in cols else 0, key=f'map_{key}')
                user_map[key] = None if sel == '(none)' else sel

            if st_region.button(_t('ota.apply_import'), key='apply_mapping_import'):
                try:
                    inserted, mapped, processed = import_trades(df_u, user_map, username=username, db_path=db_path, db_name='trades.db')
                    st_region.success(_t('ota.import_success', n=inserted))
                    try:
                        st_region.write(_t('ota.mapped_preview'))
                        st_region.dataframe(mapped.head())
                    except Exception:
                        pass
                    try:
                        if not processed.empty:
                            st_region.write(_t('ota.processed_preview'))
                            st_region.dataframe(processed.head())
                            if simulator is not None and st_region.button(_t('ota.analyze_imported'), key='analyze_imported'):
                                try:
                                    simulator.render_parity(processed, suffix='imported', invest=100000, region=st_region)
                                except Exception as e:
                                    st_region.error(f'Failed to run parity analysis: {e}')
                            # Also refresh the DB-derived processed/paired preview so stored trades are visible
                            try:
                                db_proc_res = analyze_own_trades(db_path=db_path, db_name='trades.db', system_currency='EUR')
                                db_proc = db_proc_res.get('processed', pd.DataFrame())
                                if db_proc is not None and not db_proc.empty:
                                    st_region.write(_t('ota.db_preview'))
                                    try:
                                        st_region.dataframe(db_proc.head(20))
                                    except Exception:
                                        st_region.write(db_proc.head(20).to_string())
                                    try:
                                        if simulator is not None and st_region.button(_t('ota.analyze_stored'), key='analyze_stored_after_import'):
                                            try:
                                                simulator.render_parity(db_proc, suffix='stored_after_import', invest=100000, region=st_region)
                                            except Exception as e:
                                                st_region.error(f'Failed to run parity analysis: {e}')
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                    except Exception:
                        pass
                except Exception as e:
                    st_region.error(f'Failed to import mapped trades to DB: {e}')

    # Export current DB trades
    try:
        dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
        try:
            df_all = pd.read_sql_query('SELECT * FROM trades ORDER BY timestamp DESC', dbt.conn)
        except Exception:
            df_all = pd.DataFrame()

        # Always show a short preview (or empty table) and row count for diagnostics
        try:
            row_count = 0 if df_all is None else len(df_all)
            st_region.write(_t('ota.stored_rows', db=dbt.database_name, rows=row_count))
            try:
                st_region.dataframe(df_all.head(20))
            except Exception:
                st_region.write(df_all.head(20).to_string() if df_all is not None else 'No rows')

            try:
                csv_bytes = (df_all.to_csv(index=False).encode('utf-8')) if df_all is not None else b''
                if csv_bytes:
                    st_region.download_button(_t('ota.export_csv'), data=csv_bytes, file_name='trades_export.csv', mime='text/csv')
            except Exception:
                pass
            try:
                xlsx = df_to_excel_bytes(df_all) if df_all is not None else b''
                if xlsx:
                    st_region.download_button(_t('ota.export_xlsx'), data=xlsx, file_name='trades_export.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            except Exception:
                pass
        except Exception:
            pass

        # Also show the processed/pairing preview using analyze_own_trades (helps verify pairing)
        try:
            res = analyze_own_trades(db_path=db_path, db_name='trades.db', system_currency='EUR')
            proc = res.get('processed', pd.DataFrame())
            proc_rows = 0 if proc is None else len(proc)
            st_region.write(_t('ota.processed_rows', rows=proc_rows))
            try:
                if proc is not None and not proc.empty:
                    st_region.dataframe(proc.head(20))
                else:
                    st_region.write(_t('ota.no_processed_available'))
            except Exception:
                if proc is not None:
                    st_region.write(proc.head(20).to_string())
                else:
                    st_region.write(_t('ota.no_processed_available'))
        except Exception:
            pass

        # Dangerous operation: delete all trades
        st_region.markdown('---')
        st_region.markdown(_t('ota.danger_label'), unsafe_allow_html=True)
        delete_confirm = st_region.text_input(_t('ota.delete_confirm'), key='confirm_delete_trades')
        if st_region.button(_t('ota.delete_btn'), key='delete_trades_btn'):
            if delete_confirm == 'DELETE':
                try:
                    try:
                        dbt.cursor.execute('DELETE FROM trades')
                        dbt.conn.commit()
                        st_region.success(_t('ota.delete_success'))
                    except Exception:
                        st_region.warning(_t('ota.delete_no_table'))
                except Exception as e:
                    st_region.error(f'Failed to delete trades: {e}')
            else:
                st_region.warning(_t('ota.delete_confirm_warning'))

        try:
            dbt.close()
        except Exception:
            pass
    except Exception:
        pass
