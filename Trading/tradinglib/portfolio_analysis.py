"""
Portfolio analysis module: helpers and UI for closed trades and open positions.

Extracted from own_trades_analysis.py.
Public API:
  render_portfolio_analysis(region, db_path, username, system_currency)
"""
import datetime as dt
import logging

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objs as go
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed

from tradinglib import tools
from tradinglib.utils import get_display_name
from tradinglib.i18n import t as _t

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Price helpers (also imported by render_risk_management)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_close_series_for_portfolio(ticker: str, db_path: str = 'database') -> pd.Series:
    """Load close prices for a ticker from the local DB, falling back to network."""
    try:
        from tradinglib.fetch_data import FetchData
        fd = FetchData(database_path=db_path)
        df_t = fd.load_price_data(ticker, period='2y', interval='1d', aggregate=False)
        if df_t is not None and not df_t.empty and 'Close' in df_t.columns:
            df_t.index = pd.to_datetime(df_t.index, errors='coerce')
            s = df_t['Close'].dropna()
            if not s.empty:
                return s.rename(ticker)
    except Exception:
        pass
    try:
        from tradinglib import market_data as md
        df_r = md.ticker_history(ticker, period='2y', interval='1d')
        if df_r is not None and not df_r.empty and 'Close' in df_r.columns:
            df_r.index = pd.to_datetime(df_r.index, errors='coerce')
            return df_r['Close'].dropna().rename(ticker)
    except Exception:
        pass
    return pd.Series(dtype=float, name=ticker)


def _get_current_price(ticker: str, db_path: str = 'database') -> float:
    """Retrieve the last known closing price for a ticker."""
    s = _fetch_close_series_for_portfolio(ticker, db_path)
    if s is not None and not s.empty:
        return float(s.iloc[-1])
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Chart helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_normalized_trend_fig(series_dict: dict, label_map: dict = None, start_date: str = None) -> go.Figure:
    """
    Normalised trend chart: all tickers rebased to 100 at start_date.
    series_dict: {ticker: pd.Series(Close, DatetimeIndex)}
    label_map:   {ticker: 'display name'} (optional)
    start_date:  'YYYY-MM-DD' (optional; defaults to first available date)
    """
    fig = go.Figure()
    for ticker, s in series_dict.items():
        if s is None or s.empty:
            continue
        s = s.copy()
        s.index = pd.to_datetime(s.index, errors='coerce')
        s = s.dropna()
        if start_date:
            try:
                s = s[s.index >= pd.Timestamp(start_date)]
            except Exception:
                pass
        if s.empty:
            continue
        base = s.iloc[0]
        if base == 0:
            continue
        norm = (s / base) * 100
        label = (label_map or {}).get(ticker, ticker)
        fig.add_trace(go.Scatter(
            x=norm.index,
            y=norm.values.round(2),
            mode='lines',
            name=label,
            hovertemplate=f'<b>{label}</b><br>%{{x|%Y-%m-%d}}<br>%{{y:.1f}} (Base 100)<extra></extra>',
        ))
    fig.update_layout(
        xaxis_title='Date',
        yaxis_title='Indexed (Start = 100)',
        template='plotly_white',
        height=500,
        hovermode='x unified',
        margin=dict(t=70),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0),
    )
    return fig


def _build_since_buy_figs(series_dict: dict, buy_dates: dict, label_map: dict = None,
                          end_date: str = None, xaxis_days: str = 'Trading days held') -> tuple:
    """
    Two normalised charts – each open position rebased to 100 at its OWN buy date.
    Returns (fig_calendar, fig_days):
      - fig_calendar: x = calendar date; each line starts on the day it was bought
                      → unrealised P&L trajectory per position.
      - fig_days:     x = trading days held (0, 1, 2, …); all lines start at the left
                      → compares the post-entry trajectory across positions,
                        independent of when each was bought.
    series_dict: {ticker: pd.Series(Close, DatetimeIndex)}
    buy_dates:   {ticker: Timestamp of the first buy}
    """
    fig_cal  = go.Figure()
    fig_days = go.Figure()
    for ticker, s in series_dict.items():
        if s is None or s.empty:
            continue
        bd = buy_dates.get(ticker)
        if bd is None or pd.isna(bd):
            continue
        s = s.copy()
        s.index = pd.to_datetime(s.index, errors='coerce')
        s = s.dropna().sort_index()
        try:
            seg = s[s.index >= pd.Timestamp(bd)]
            if end_date:
                seg = seg[seg.index <= pd.Timestamp(end_date)]
        except Exception:
            seg = s
        if seg.empty or seg.iloc[0] == 0:
            continue
        norm  = (seg / seg.iloc[0]) * 100
        label = (label_map or {}).get(ticker, ticker)
        fig_cal.add_trace(go.Scatter(
            x=norm.index, y=norm.values.round(2), mode='lines', name=label,
            hovertemplate=f'<b>{label}</b><br>%{{x|%Y-%m-%d}}<br>%{{y:.1f}} (Buy=100)<extra></extra>',
        ))
        fig_days.add_trace(go.Scatter(
            x=list(range(len(norm))), y=norm.values.round(2), mode='lines', name=label,
            hovertemplate=f'<b>{label}</b><br>+%{{x}}d<br>%{{y:.1f}} (Buy=100)<extra></extra>',
        ))
    # No plotly title – the column sub-header labels each chart; this avoids the
    # title overlapping the horizontal legend (the per-ticker entries above the plot).
    _common = dict(template='plotly_white', height=460, hovermode='x unified',
                   margin=dict(t=70),
                   legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0))
    fig_cal.update_layout(xaxis_title='Date',
                          yaxis_title='Indexed (Buy = 100)', **_common)
    fig_days.update_layout(xaxis_title=xaxis_days,
                           yaxis_title='Indexed (Buy = 100)', **_common)
    # Break-even reference line at 100
    for _f in (fig_cal, fig_days):
        _f.add_hline(y=100, line_dash='dot', line_color='#888', opacity=0.6)
    return fig_cal, fig_days


def _compute_parity_weights(series_dict: dict, window: int = 30) -> pd.Series:
    """
    Inverse-volatility weights based on the last `window` daily data points.
    Returns a pd.Series with ticker index and weights (sum=1).
    """
    vols = {}
    for ticker, s in series_dict.items():
        if s is None or s.empty:
            continue
        try:
            tail = s.dropna().tail(window)
            if len(tail) < 5:
                continue
            daily_ret = tail.pct_change().dropna()
            v = float(daily_ret.std()) * np.sqrt(252)
            if v > 0:
                vols[ticker] = v
        except Exception:
            continue
    if not vols:
        return pd.Series(dtype=float)
    inv_vol = pd.Series({k: 1 / v for k, v in vols.items()})
    return inv_vol / inv_vol.sum()


def _lookup_asset_info(tickers: list, db_path: str = 'database') -> dict:
    """
    Look up longName and current price from asset_info.db for the given tickers.
    Returns {TICKER: {'longName': str, 'currentPrice': float}}.
    Silently falls back to {} if the DB is unreachable.
    """
    if not tickers:
        return {}
    result = {}
    try:
        dbt = tools.Db_tools(db_path=db_path, database_name='asset_info.db')
        try:
            ph = ','.join(['?'] * len(tickers))
            df = pd.read_sql_query(
                f'SELECT ticker, symbol, longName, shortName, '
                f'currentPrice, regularMarketPrice, previousClose '
                f'FROM asset_info '
                f'WHERE ticker IN ({ph}) OR symbol IN ({ph}) '
                f'ORDER BY timestamp DESC',
                dbt.conn, params=tickers * 2,
            )
        except Exception:
            df = pd.DataFrame()
        finally:
            try:
                dbt.close()
            except Exception:
                pass

        if df.empty:
            return {}

        # Normalise key, keep the most recent row per ticker
        df['_key'] = (
            df['ticker'].combine_first(df['symbol'])
            .astype(str).str.upper().str.strip()
        )
        df = df.drop_duplicates(subset='_key', keep='first')

        for _, row in df.iterrows():
            t = row['_key']
            long_name  = get_display_name(row)
            cur_price  = (
                row.get('currentPrice') or
                row.get('regularMarketPrice') or
                row.get('previousClose') or 0.0
            )
            result[t] = {
                'longName':     str(long_name).strip() if long_name else '',
                'currentPrice': float(cur_price) if cur_price else 0.0,
            }
    except Exception:
        pass
    return result


def _fetch_fx_daily(currency: str, system_currency: str, daily: pd.DatetimeIndex,
                    db_path: str = 'database') -> pd.Series:
    """Daily FX series (units of *currency* per 1 *system_currency*) aligned to *daily*.

    Uses the Yahoo FX ticker ``{SYS}{CUR}=X`` (same convention as
    DataUtils.get_exchange_rate). Conversion of native amount to system: amount / rate.
    Falls back to 1.0 when no FX data is available (no conversion applied).
    """
    if not currency or currency == system_currency:
        return pd.Series(1.0, index=daily)
    try:
        fx = _fetch_close_series_for_portfolio(f"{system_currency}{currency}=X", db_path)
    except Exception:
        fx = None
    if fx is None or fx.empty:
        return pd.Series(1.0, index=daily)
    fx = fx[~fx.index.duplicated(keep='last')].sort_index()
    fx.index = pd.to_datetime(fx.index, errors='coerce').normalize()
    return fx.reindex(daily).ffill().bfill().replace(0, np.nan).ffill().bfill().fillna(1.0)


def _build_portfolio_history(events: pd.DataFrame, db_path: str = 'database',
                             system_currency: str = 'EUR',
                             currency_map: dict = None) -> pd.DataFrame:
    """Actual portfolio history reconstructed from transactions (trades.db).

    Unlike a plain price history for a single ticker, this reconstructs the
    actually HELD share counts over time (cumulative buys minus sells) and
    values them daily at the closing price. The curve therefore starts at the
    first trade — not at the beginning of the (much older) price history.

    Amounts are converted to the system currency on a daily basis using a
    historical FX series per foreign currency.

    Args:
        events: DataFrame with columns
                _ts  (datetime), _ticker,
                _signed_sh  (buys +, sells −),
                _signed_val (capital deployed: buy +, sell −),
                _cur (trade currency, optional).
        currency_map: {ticker: currency} for price conversion.
    Returns:
        DataFrame, index=daily date, columns:
          'value'    – market value of held shares (system currency)
          'invested' – cumulative net capital deployed (system currency)
    """
    if events is None or events.empty:
        return pd.DataFrame()

    ev = events.dropna(subset=['_ts']).copy()
    ev['_date'] = pd.to_datetime(ev['_ts'], errors='coerce').dt.normalize()
    ev = ev.dropna(subset=['_date'])
    if ev.empty:
        return pd.DataFrame()

    sys_cur = (system_currency or 'EUR').upper()
    currency_map = {k: (str(v).upper().strip() or sys_cur)
                    for k, v in (currency_map or {}).items()}
    if '_cur' not in ev.columns:
        ev['_cur'] = sys_cur
    ev['_cur'] = ev['_cur'].astype(str).str.upper().str.strip().replace(
        {'': sys_cur, 'NAN': sys_cur, 'NONE': sys_cur})

    start = ev['_date'].min()
    # Extend the calendar to the latest trade date if it lies in the future
    # (e.g. a mis-dated import). Otherwise such rows fall outside `daily` and are
    # silently dropped from BOTH value and invested — a hidden data loss.
    end = max(pd.Timestamp.today().normalize(), ev['_date'].max())
    if end < start:
        end = ev['_date'].max()
    daily = pd.date_range(start, end, freq='D')

    tickers = sorted([t for t in ev['_ticker'].dropna().unique().tolist() if t])

    # Load prices per ticker (local → network fallback)
    series_map: dict = {}
    if tickers:
        with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as exr:
            futs = {exr.submit(_fetch_close_series_for_portfolio, t, db_path): t for t in tickers}
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    s = fut.result()
                except Exception:
                    s = None
                if s is not None and not s.empty:
                    s = s[~s.index.duplicated(keep='last')].sort_index()
                    s.index = pd.to_datetime(s.index, errors='coerce').normalize()
                    series_map[t] = s.astype(float)

    # Daily FX series per required foreign currency (CUR per SYS)
    needed = {currency_map.get(t, sys_cur) for t in tickers} | set(ev['_cur'].unique())
    fx_daily = {cur: _fetch_fx_daily(cur, sys_cur, daily, db_path)
                for cur in needed if cur and cur != sys_cur}

    def _to_sys(series: pd.Series, cur: str) -> pd.Series:
        if not cur or cur == sys_cur:
            return series
        rate = fx_daily.get(cur)
        return series / rate if rate is not None else series

    # ── Market value: held shares × price, in system currency ───────────────
    value_total = pd.Series(0.0, index=daily)
    for t in tickers:
        delta = ev[ev['_ticker'] == t].groupby('_date')['_signed_sh'].sum()
        held = delta.reindex(daily, fill_value=0.0).cumsum().clip(lower=0)  # rounding residuals
        s = series_map.get(t)
        if s is None:
            continue
        close = s.reindex(daily).ffill().bfill()
        native_val = held * close
        val_sys = _to_sys(native_val, currency_map.get(t, sys_cur)).fillna(0.0)
        value_total = value_total.add(val_sys, fill_value=0.0)

    # ── Capital deployed: convert each trade using the trade-date FX rate ───
    def _val_sys(row) -> float:
        cur = row['_cur']
        v = float(row['_signed_val'])
        if cur == sys_cur:
            return v
        rate = fx_daily.get(cur)
        if rate is None:
            return v
        try:
            rr = float(rate.loc[row['_date']])
        except Exception:
            rr = 0.0
        return v / rr if rr else v

    ev['_val_sys'] = ev.apply(_val_sys, axis=1)
    daily_cf = ev.groupby('_date')['_val_sys'].sum().reindex(daily, fill_value=0.0)
    invested = daily_cf.cumsum()

    out = pd.DataFrame({'value': value_total, 'invested': invested})

    # ── Pure total trend (time-weighted, excluding cash flows) ─────────────
    # TWR: daily return relative to capital at the start of day
    # (V_t-1 + Cashflow_t), so purchases/sales do not create phantom gains.
    v_prev = value_total.shift(1)
    base = v_prev + daily_cf
    r = (value_total / base) - 1.0
    r = r.where(base > 0, 0.0).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    if len(r) > 0:
        r.iloc[0] = 0.0  # first day = base (index 100)
    out['trend_index'] = 100.0 * (1.0 + r).cumprod()

    return out[out.index >= start]


# ─────────────────────────────────────────────────────────────────────────────
# Main render function
# ─────────────────────────────────────────────────────────────────────────────

def render_portfolio_analysis(region=st, db_path: str = 'database', username: str = '', system_currency: str = 'EUR'):
    """
    Portfolio analysis read directly from trades.db (no upload needed).
    A) Closed trades   – normalised price chart over the holding period
    B) Open positions  – current portfolio value, parity analysis, rebalancing
    """
    r = region

    # ── Read raw data directly from trades.db (TradeProcessor is not used
    #    because column names in the DB may differ in case) ───────────────────
    dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
    raw = pd.DataFrame()
    try:
        dbt.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                ticker    TEXT,
                side      TEXT,
                qty       REAL,
                price     REAL,
                currency  TEXT,
                broker    TEXT
            )
        """)
        dbt.conn.commit()
        raw = pd.read_sql_query('SELECT * FROM trades ORDER BY timestamp ASC', dbt.conn)
    except Exception as e:
        r.error(f'Error reading trades.db: {e}')
        return
    finally:
        try:
            dbt.close()
        except Exception:
            pass

    if raw is None or raw.empty:
        r.info(_t('ota.no_trades_db'))
        return

    # Normalise column names (case-insensitive lookup)
    raw.columns = [c.strip() for c in raw.columns]
    _col = {c.lower(): c for c in raw.columns}   # lower → Originalname

    def _col_val(row, *names):
        """Return the first matching column value from row, trying each name in priority order."""
        for n in names:
            c = _col.get(n.lower())
            if c and c in row.index:
                return row[c]
        return None

    def _df_col(df, *names):
        """Return the first matching column Series from df, trying each name in priority order."""
        for n in names:
            c = _col.get(n.lower())
            if c and c in df.columns:
                return df[c]
        return pd.Series([None] * len(df), index=df.index)

    # Normalise the action column
    act_col = _col.get('action', 'action')
    if act_col not in raw.columns:
        r.error(_t('ota.no_action_col'))
        return
    raw[act_col] = raw[act_col].astype(str).str.lower().str.strip()

    # Normalise shares / price / value
    for col_alias, std in [('shares', '_shares'), ('price', '_price'), ('value', '_value'),
                            ('ticker', '_ticker'), ('timestamp', '_ts'),
                            ('longname', '_longname'), ('isin', '_isin'),
                            ('currency', '_currency')]:
        src = _col.get(col_alias)
        if src and src in raw.columns:
            raw[std] = raw[src]
        else:
            raw[std] = None

    raw['_shares'] = pd.to_numeric(raw['_shares'], errors='coerce').fillna(0)
    raw['_price']  = pd.to_numeric(raw['_price'],  errors='coerce').fillna(0)
    raw['_value']  = pd.to_numeric(raw['_value'],  errors='coerce').fillna(0)
    raw['_ts']     = pd.to_datetime(raw['_ts'],    errors='coerce')
    raw['_ticker'] = raw['_ticker'].astype(str).str.upper().str.strip()

    buy_raw  = raw[raw[act_col] == 'buy'].copy()
    sell_raw = raw[raw[act_col] == 'sell'].copy()

    # ── Determine open vs. closed positions ───────────────────────────────
    # Net shares per ticker: open = buy_shares - sell_shares > 0
    buy_agg  = buy_raw.groupby('_ticker', as_index=False)['_shares'].sum().rename(columns={'_shares': '_buy_sh'})
    sell_agg = sell_raw.groupby('_ticker', as_index=False)['_shares'].sum().rename(columns={'_shares': '_sell_sh'})
    net      = buy_agg.merge(sell_agg, on='_ticker', how='left')
    net['_sell_sh']  = net['_sell_sh'].fillna(0)
    net['_open_sh']  = net['_buy_sh'] - net['_sell_sh']
    open_tickers   = set(net[net['_open_sh'] > 0.001]['_ticker'])
    closed_tickers = set(sell_raw['_ticker'])

    open_df   = buy_raw[buy_raw['_ticker'].isin(open_tickers)].copy()
    closed_df = sell_raw[sell_raw['_ticker'].isin(closed_tickers)].copy()

    r.markdown(_t('ota.trades_loaded', total=len(raw), open=len(open_tickers), closed=len(closed_tickers)))

    # Display order of expanders (independent of code order):
    #   A) Open positions, B) History, C) Closed trades
    _sec_open    = r.container()
    _sec_history = r.container()
    _sec_closed  = r.container()

    # ══════════════════════════════════════════════════════════════════════════
    # C) CLOSED TRADES – Normalised price history
    # ══════════════════════════════════════════════════════════════════════════
    with _sec_closed.expander(_t('ota.section_closed'), expanded=False):

        if closed_df.empty:
            st.info(_t('ota.no_closed_trades'))
        else:
            # Find the earliest matching buy transaction for each sell trade
            closed_display = []
            for _, sell_row in closed_df.iterrows():
                t = sell_row['_ticker']
                sell_ts = sell_row['_ts']
                sell_price = sell_row['_price']
                sell_shares = sell_row['_shares']
                # Look for a matching buy (same ticker, same share count before the sell)
                matching_buys = buy_raw[
                    (buy_raw['_ticker'] == t) &
                    (buy_raw['_shares'] == sell_shares) &
                    (buy_raw['_ts'] <= sell_ts)
                ]
                if not matching_buys.empty:
                    buy_row_m = matching_buys.iloc[-1]  # LIFO: most recent matching buy
                    buy_ts    = buy_row_m['_ts']
                    buy_price = buy_row_m['_price']
                else:
                    # No exact match → earliest buy for the ticker
                    earliest = buy_raw[buy_raw['_ticker'] == t]
                    buy_ts    = earliest['_ts'].min() if not earliest.empty else pd.NaT
                    buy_price = earliest['_price'].iloc[0] if not earliest.empty else 0.0

                gain = round((sell_price - buy_price) * sell_shares, 2)
                gain_pct = round((sell_price / buy_price - 1) * 100, 2) if buy_price else 0.0
                hold_days = (sell_ts - buy_ts).days if (not pd.isna(sell_ts) and not pd.isna(buy_ts)) else None
                closed_display.append({
                    'Ticker':          t,
                    'Name':            sell_row.get('_longname') or '',
                    'Buy Date':        buy_ts,
                    'Sell Date':       sell_ts,
                    'Hold (days)':     hold_days,
                    'Buy Price':       round(buy_price, 4),
                    'Sell Price':      round(sell_price, 4),
                    'Shares':          sell_shares,
                    f'P&L ({system_currency})': gain,
                    'P&L %':           gain_pct,
                    '_buy_ts':         buy_ts,
                    '_sell_ts':        sell_ts,
                    '_gain_pct':       gain_pct,
                })

            closed_tbl = pd.DataFrame(closed_display)
            disp_cols_c = [c for c in ['Ticker', 'Name', 'Buy Date', 'Sell Date', 'Hold (days)',
                                        'Buy Price', 'Sell Price', 'Shares',
                                        f'P&L ({system_currency})', 'P&L %'] if c in closed_tbl.columns]
            st.dataframe(closed_tbl[disp_cols_c], hide_index=True, use_container_width=True,
                         column_config={
                             'Buy Date':  st.column_config.DatetimeColumn('Buy Date',  format='YYYY-MM-DD'),
                             'Sell Date': st.column_config.DatetimeColumn('Sell Date', format='YYYY-MM-DD'),
                             'Buy Price':  st.column_config.NumberColumn(format='%.4f'),
                             'Sell Price': st.column_config.NumberColumn(format='%.4f'),
                             f'P&L ({system_currency})': st.column_config.NumberColumn(format='%.2f'),
                             'P&L %': st.column_config.NumberColumn(format='%.2f'),
                         })
            tools.excel_download_button(
                closed_tbl[disp_cols_c], _t('ota.export_table_xlsx'),
                'closed_trades.xlsx', region=st, key='dl_closed_trades')

            # Normalised chart: each position over its holding period
            st.markdown(_t('ota.norm_chart_closed'))
            tickers_closed = list(set(r['Ticker'] for r in closed_display))
            with st.spinner('Loading historical price data …'):
                with ThreadPoolExecutor(max_workers=min(8, len(tickers_closed))) as ex:
                    futs = {ex.submit(_fetch_close_series_for_portfolio, t, db_path): t for t in tickers_closed}
                    closed_series = {}
                    for fut in as_completed(futs):
                        t = futs[fut]
                        try:
                            closed_series[t] = fut.result()
                        except Exception:
                            closed_series[t] = pd.Series(dtype=float, name=t)

            fig_closed = go.Figure()
            # Distinct colour per trade so each line is identifiable against the
            # legend; win/loss is still encoded via the line style (solid = gain,
            # dotted = loss) plus the ▲/▼ and signed % in the label.
            _palette = px.colors.qualitative.D3 + px.colors.qualitative.Set2
            for _i, entry in enumerate(closed_display):
                ticker   = entry['Ticker']
                buy_ts   = entry['_buy_ts']
                sell_ts  = entry['_sell_ts']
                s = closed_series.get(ticker)
                if s is None or s.empty:
                    continue
                s = s.copy()
                s.index = pd.to_datetime(s.index, errors='coerce')
                # Sort chronologically and drop duplicate timestamps – otherwise an
                # unsorted DB read makes the line zig-zag / run back to a prior day.
                s = s[~s.index.isna()].sort_index()
                s = s[~s.index.duplicated(keep='last')]
                try:
                    segment = s[(s.index >= pd.Timestamp(buy_ts)) & (s.index <= pd.Timestamp(sell_ts))]
                except Exception:
                    segment = s
                if segment.empty or segment.iloc[0] == 0:
                    continue
                norm  = (segment / segment.iloc[0]) * 100
                _gain = entry['_gain_pct']
                _arrow = '▲' if _gain >= 0 else '▼'
                _buy_str = pd.Timestamp(buy_ts).strftime("%Y-%m-%d") if not pd.isna(buy_ts) else "?"
                label = f'{_arrow} {ticker} ({_buy_str}, {_gain:+.1f}%)'
                color = _palette[_i % len(_palette)]
                fig_closed.add_trace(go.Scatter(
                    x=norm.index, y=norm.values.round(2),
                    mode='lines', name=label,
                    line=dict(color=color, dash='solid' if _gain >= 0 else 'dot'),
                    hovertemplate=f'<b>{label}</b><br>%{{x|%Y-%m-%d}}<br>%{{y:.1f}} (Base 100)<extra></extra>',
                ))
            fig_closed.update_layout(
                xaxis_title='Date', yaxis_title='Indexed (Buy = 100)',
                template='plotly_white', height=480, hovermode='x unified',
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0),
            )
            st.plotly_chart(fig_closed, use_container_width=True)

            # Cumulative realised P&L
            try:
                cum_df = closed_tbl.dropna(subset=['Sell Date']).sort_values('Sell Date').copy()
                cum_df[f'P&L ({system_currency})'] = pd.to_numeric(cum_df[f'P&L ({system_currency})'], errors='coerce').fillna(0)
                cum_df['Cumulative P&L'] = cum_df[f'P&L ({system_currency})'].cumsum()
                fig_cum = px.line(cum_df, x='Sell Date', y='Cumulative P&L',
                                  labels={'Sell Date': 'Date', 'Cumulative P&L': f'Cum. P&L ({system_currency})'},
                                  title=_t('ota.cum_pnl_title'), template='plotly_white')
                st.plotly_chart(fig_cum, use_container_width=True)
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    # A) OPEN POSITIONS – Rebalancing & parity analysis
    # ══════════════════════════════════════════════════════════════════════════
    with _sec_open.expander(_t('ota.section_open'), expanded=True):

        if open_df.empty:
            st.info(_t('ota.no_open_positions'))
        else:
            # Aggregate multiple lots for the same ticker (normalised columns _ticker, _shares, _value, …)
            agg = open_df.groupby('_ticker', as_index=False).agg(
                longName   = ('_longname', 'first'),
                isin       = ('_isin',     'first'),
                currency   = ('_currency', 'first'),
                buy_shares = ('_shares',   'sum'),
                buy_value  = ('_value',    lambda x: abs(x).sum()),
                buy_ts     = ('_ts',       'min'),
            ).rename(columns={'_ticker': 'ticker'})
            # Average buy price across ALL purchases (not divided by the net remaining shares)
            agg['avg_price'] = np.where(agg['buy_shares'] > 0, agg['buy_value'] / agg['buy_shares'], 0.0)
            # Net open share count = buys − sells (from the net DataFrame computed above)
            agg = agg.merge(
                net[['_ticker', '_open_sh']].rename(columns={'_ticker': 'ticker', '_open_sh': 'shares'}),
                on='ticker', how='left',
            )
            agg['shares'] = agg['shares'].fillna(agg['buy_shares'])
            # Cost basis applied only to the open remaining shares
            agg['buy_value'] = agg['shares'] * agg['avg_price']

            tickers_open = agg['ticker'].tolist()

            # ── Enrich metadata from asset_info.db (longName + price fallback) ──
            info_map = _lookup_asset_info(tickers_open, db_path)
            for i, row_i in agg.iterrows():
                t = row_i['ticker']
                info = info_map.get(t, {})
                cur_name = str(row_i.get('longName') or '').strip()
                if info.get('longName') and cur_name in ('', 'None', 'nan'):
                    agg.at[i, 'longName'] = info['longName']

            # Load current prices
            with st.spinner(f'Loading current prices for {len(tickers_open)} assets …'):
                with ThreadPoolExecutor(max_workers=min(8, len(tickers_open))) as ex:
                    futs = {ex.submit(_fetch_close_series_for_portfolio, t, db_path): t for t in tickers_open}
                    open_series = {}
                    for fut in as_completed(futs):
                        t = futs[fut]
                        try:
                            open_series[t] = fut.result()
                        except Exception:
                            open_series[t] = pd.Series(dtype=float, name=t)

            current_prices = {
                t: (float(s.iloc[-1]) if s is not None and not s.empty else 0.0)
                for t, s in open_series.items()
            }
            # Fallback: currentPrice from asset_info.db when OHLC data is missing
            for t in tickers_open:
                if current_prices.get(t, 0.0) == 0.0:
                    fb = info_map.get(t, {}).get('currentPrice', 0.0)
                    if fb:
                        current_prices[t] = fb

            agg['current_price']  = agg['ticker'].map(current_prices).fillna(0)
            agg['current_value']  = agg['shares'] * agg['current_price']
            agg['unrealized_pnl'] = agg['current_value'] - agg['buy_value']
            total_open_value = agg['current_value'].sum()
            agg['weight_pct'] = np.where(
                total_open_value > 0, (agg['current_value'] / total_open_value * 100).round(2), 0.0
            )
            agg['pnl_pct'] = np.where(
                agg['buy_value'] > 0, (agg['unrealized_pnl'] / agg['buy_value'] * 100).round(2), 0.0
            )

            # Portfolio table
            st.markdown(_t('ota.open_positions_header'))
            disp_cols_o = [c for c in ['ticker', 'longName', 'isin', 'shares', 'avg_price',
                                        'current_price', 'buy_value', 'current_value',
                                        'unrealized_pnl', 'pnl_pct', 'weight_pct'] if c in agg.columns]
            _agg_disp = agg[disp_cols_o].copy()
            if 'ticker' in _agg_disp.columns:
                _agg_disp.insert(0, 'details', _agg_disp['ticker'].apply(lambda t: f'/?symbol={t}&details=True'))
            st.dataframe(_agg_disp, hide_index=True, use_container_width=True,
                         column_config={
                             'details':       st.column_config.LinkColumn('Details', display_text='View'),
                             'ticker':        st.column_config.TextColumn(_t('ota.col_ticker')),
                             'longName':      st.column_config.TextColumn(_t('ota.col_name')),
                             'shares':        st.column_config.NumberColumn(_t('ota.col_shares'),       format='%.2f'),
                             'avg_price':     st.column_config.NumberColumn(_t('ota.col_avg_buy'),      format='%.4f'),
                             'current_price': st.column_config.NumberColumn(_t('ota.col_current_price'), format='%.4f'),
                             'buy_value':     st.column_config.NumberColumn(_t('ota.col_cost_basis', currency=system_currency),   format='%.2f'),
                             'current_value': st.column_config.NumberColumn(_t('ota.col_market_value', currency=system_currency), format='%.2f'),
                             'unrealized_pnl':st.column_config.NumberColumn(_t('ota.col_pnl', currency=system_currency),         format='%.2f'),
                             'pnl_pct':       st.column_config.NumberColumn(_t('ota.col_pnl_pct'),      format='%.1f'),
                             'weight_pct':    st.column_config.NumberColumn(_t('ota.col_weight_pct'),   format='%.1f'),
                         })
            tools.excel_download_button(
                _agg_disp[disp_cols_o], _t('ota.export_table_xlsx'),
                'open_positions.xlsx', region=st, key='dl_open_positions')

            total_invest = agg['buy_value'].sum()
            total_gain   = agg['unrealized_pnl'].sum()
            gain_pct     = (total_gain / total_invest * 100) if total_invest > 0 else 0.0
            # Investment weight (basis: cost basis, not market value)
            agg['invest_pct'] = np.where(
                total_invest > 0, (agg['buy_value'] / total_invest * 100).round(2), 0.0
            )
            m1, m2, m3 = st.columns(3)
            m1.metric(_t('ota.metric_total_value'), f'{total_open_value:,.2f} {system_currency}')
            m2.metric(_t('ota.metric_cost_basis'),  f'{total_invest:,.2f} {system_currency}')
            m3.metric(_t('ota.metric_pnl'),          f'{total_gain:+,.2f} {system_currency}', delta=f'{gain_pct:+.1f}%')

            # ── Normalised trend chart – open positions ───────────────────
            st.markdown(_t('ota.norm_chart_open'))
            st.caption(_t('ota.norm_chart_caption'))

            date_c1, date_c2 = st.columns(2)
            try:
                earliest_buy = open_df['_ts'].min()
                default_start = earliest_buy.date() if not pd.isna(earliest_buy) else dt.date.today() - dt.timedelta(days=365)
            except Exception:
                default_start = dt.date.today() - dt.timedelta(days=365)

            start_date = date_c1.date_input(
                _t('ota.date_start'), default_start, format='YYYY-MM-DD', key='ptf_open_start'
            ).strftime('%Y-%m-%d')
            end_date = date_c2.date_input(
                _t('ota.date_end'), dt.date.today(), format='YYYY-MM-DD', key='ptf_open_end'
            ).strftime('%Y-%m-%d')

            label_map = {
                row['ticker']: f"{row['ticker']} – {str(row.get('longName') or '')[:20]}"
                for _, row in agg.iterrows()
            }
            filtered_series = {}
            for t, s in open_series.items():
                if s is None or s.empty:
                    continue
                s2 = s.copy()
                s2.index = pd.to_datetime(s2.index, errors='coerce')
                try:
                    filtered_series[t] = s2[
                        (s2.index >= pd.Timestamp(start_date)) &
                        (s2.index <= pd.Timestamp(end_date))
                    ]
                except Exception:
                    filtered_series[t] = s2

            trend_fig = _build_normalized_trend_fig(filtered_series, label_map=label_map, start_date=start_date)
            st.plotly_chart(trend_fig, use_container_width=True)

            # ── Since-buy charts (B: calendar, C: holding period) ─────────
            st.markdown(_t('ota.norm_chart_since_buy'))
            st.caption(_t('ota.norm_chart_since_buy_caption'))
            buy_dates = {
                row['ticker']: pd.Timestamp(row['buy_ts'])
                for _, row in agg.iterrows() if pd.notna(row.get('buy_ts'))
            }
            fig_since_cal, fig_since_days = _build_since_buy_figs(
                open_series, buy_dates, label_map=label_map, end_date=end_date,
                xaxis_days=_t('ota.since_buy_xdays'),
            )
            _sb_c1, _sb_c2 = st.columns(2)
            with _sb_c1:
                st.markdown(f"**{_t('ota.since_buy_calendar')}**")
                st.plotly_chart(fig_since_cal, use_container_width=True)
            with _sb_c2:
                st.markdown(f"**{_t('ota.since_buy_holding')}**")
                st.plotly_chart(fig_since_days, use_container_width=True)

            # ── Parity analysis ───────────────────────────────────────────
            st.markdown(_t('ota.parity_header'))
            st.caption(_t('ota.parity_caption', invest=f'{total_invest:,.2f}', currency=system_currency))

            opt_weights = _compute_parity_weights(open_series, window=30)
            if opt_weights.empty:
                st.warning(_t('ota.parity_no_data'))
            else:
                # Use invested capital (cost basis) as the weighting base, not fluctuating market value
                invest_w_map = dict(zip(agg['ticker'], agg['invest_pct']))  # already in %
                parity_df = pd.DataFrame({
                    'Ticker':    opt_weights.index,
                    'Parity %':  (opt_weights.values * 100).round(2),
                })
                parity_df['Current %'] = parity_df['Ticker'].map(invest_w_map).fillna(0).round(2)
                parity_df['Diff %']    = (parity_df['Parity %'] - parity_df['Current %']).round(2)
                parity_df['Action']    = parity_df['Diff %'].apply(
                    lambda d: _t('ota.action_buy') if d > 1 else (_t('ota.action_sell') if d < -1 else _t('ota.action_ok'))
                )
                # Target and current amounts both anchored to cost basis
                parity_df['Target']    = (parity_df['Parity %']  / 100 * total_invest).round(2)
                parity_df['Invested']  = (parity_df['Current %'] / 100 * total_invest).round(2)
                parity_df['Delta']     = (parity_df['Target'] - parity_df['Invested']).round(2)

                _parity_disp = parity_df[['Ticker', 'Current %', 'Parity %', 'Diff %',
                               'Action', 'Target', 'Invested', 'Delta']].copy()
                _parity_disp.insert(0, 'details', _parity_disp['Ticker'].apply(lambda t: f'/?symbol={t}&details=True'))
                st.dataframe(
                    _parity_disp,
                    column_config={
                        'details':   st.column_config.LinkColumn('Details', display_text='View'),
                        'Current %': st.column_config.NumberColumn(_t('ota.col_cost_basis_pct'), format='%.2f'),
                        'Parity %':  st.column_config.NumberColumn(_t('ota.col_parity_pct'),     format='%.2f'),
                        'Diff %':    st.column_config.NumberColumn(_t('ota.col_diff_pct'),        format='%.2f'),
                        'Target':    st.column_config.NumberColumn(_t('ota.col_target',   currency=system_currency), format='%.2f'),
                        'Invested':  st.column_config.NumberColumn(_t('ota.col_invested', currency=system_currency), format='%.2f'),
                        'Delta':     st.column_config.NumberColumn(_t('ota.col_delta',    currency=system_currency), format='%.2f'),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
                tools.excel_download_button(
                    parity_df[['Ticker', 'Current %', 'Parity %', 'Diff %',
                               'Action', 'Target', 'Invested', 'Delta']],
                    _t('ota.export_table_xlsx'), 'parity_analysis.xlsx',
                    region=st, key='dl_parity')

                # Pie charts: current (cost basis) vs. parity target
                pie_c1, pie_c2 = st.columns(2)
                fig_ist = px.pie(
                    agg, values='invest_pct', names='ticker',
                    title=_t('ota.pie_current'), hole=0.4,
                )
                fig_ist.update_traces(textinfo='percent+label')
                pie_c1.plotly_chart(fig_ist, use_container_width=True)

                fig_soll = px.pie(
                    parity_df, values='Parity %', names='Ticker',
                    title=_t('ota.pie_target'), hole=0.4,
                )
                fig_soll.update_traces(textinfo='percent+label')
                pie_c2.plotly_chart(fig_soll, use_container_width=True)

                # Action items
                st.markdown(_t('ota.action_items'))
                buys  = parity_df[parity_df['Diff %'] >  1].sort_values('Diff %', ascending=False)
                sells = parity_df[parity_df['Diff %'] < -1].sort_values('Diff %')
                if not buys.empty:
                    st.success(_t('ota.add_position'))
                    st.dataframe(buys[['Ticker', 'Current %', 'Parity %', 'Delta']],
                                 hide_index=True, use_container_width=True)
                if not sells.empty:
                    st.warning(_t('ota.reduce_position'))
                    st.dataframe(sells[['Ticker', 'Current %', 'Parity %', 'Delta']],
                                 hide_index=True, use_container_width=True)
                if buys.empty and sells.empty:
                    st.info(_t('ota.balanced'))

    # ══════════════════════════════════════════════════════════════════════════
    # B) HISTORY – Actual portfolio history reconstructed from transactions
    # ══════════════════════════════════════════════════════════════════════════
    with _sec_history.expander(_t('ota.section_history'), expanded=False):
        st.caption(_t('ota.history_caption'))
        # Only buy/sell rows drive share counts and deployed capital. Dividends,
        # interest and fees are cash income/expense — treating them as sells
        # (as a blanket "not buy → negative" rule does) wrongly shrinks the
        # invested capital and distorts the P&L.
        _bs = raw[raw[act_col].isin(['buy', 'sell'])].copy()
        events = pd.DataFrame({
            '_ts':         _bs['_ts'],
            '_ticker':     _bs['_ticker'],
            '_cur':        _bs['_currency'],
            '_signed_sh':  np.where(_bs[act_col] == 'buy',  _bs['_shares'],      -_bs['_shares']),
            '_signed_val': np.where(_bs[act_col] == 'buy',  _bs['_value'].abs(), -_bs['_value'].abs()),
        })
        # Currency per ticker (first reliable trade currency) for price conversion
        def _first_currency(s):
            for x in s:
                xs = str(x).upper().strip()
                if xs and xs not in ('NAN', 'NONE'):
                    return xs
            return system_currency
        currency_map = _bs.groupby('_ticker')['_currency'].apply(_first_currency).to_dict()
        with st.spinner(_t('ota.history_loading')):
            hist = _build_portfolio_history(events, db_path,
                                            system_currency=system_currency,
                                            currency_map=currency_map)

        if hist is None or hist.empty or len(hist) < 2:
            st.info(_t('ota.history_no_data'))
        else:
            cur_val   = float(hist['value'].iloc[-1])
            start_val = float(hist['value'].iloc[0])
            cur_inv   = float(hist['invested'].iloc[-1])
            pnl       = cur_val - cur_inv
            pnl_pct   = (pnl / cur_inv * 100) if cur_inv else 0.0

            line_color = '#1e8e3e' if cur_val >= start_val else '#c5221f'
            fill_color = 'rgba(30,142,62,0.12)' if cur_val >= start_val else 'rgba(197,34,31,0.12)'

            fig_h = go.Figure()
            fig_h.add_trace(go.Scatter(
                x=hist.index, y=hist['value'].round(2),
                mode='lines', name=_t('ota.history_value'),
                line=dict(color=line_color, width=2),
                fill='tozeroy', fillcolor=fill_color,
                hovertemplate='%{x|%Y-%m-%d}<br>%{y:,.2f} ' + system_currency + '<extra></extra>',
            ))
            fig_h.add_trace(go.Scatter(
                x=hist.index, y=hist['invested'].round(2),
                mode='lines', name=_t('ota.history_invested'),
                line=dict(color='#5f6368', width=1.5, dash='dash'),
                hovertemplate='%{x|%Y-%m-%d}<br>%{y:,.2f} ' + system_currency + '<extra></extra>',
            ))
            fig_h.update_layout(
                template='plotly_white', height=340,
                margin=dict(l=10, r=10, t=10, b=10),
                yaxis_title=system_currency, xaxis_title=None,
                legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='left', x=0),
            )
            st.plotly_chart(fig_h, use_container_width=True)

            st.metric(_t('ota.history_metric_value'), f'{cur_val:,.2f} {system_currency}')

            # ── Pure total trend (time-weighted, excluding cash flows) ──────
            if 'trend_index' in hist.columns and hist['trend_index'].notna().any():
                st.markdown(f"**{_t('ota.history_trend_header')}**")
                st.caption(_t('ota.history_trend_caption'))
                trend = hist['trend_index'].dropna()
                trend_ret = float(trend.iloc[-1]) - 100.0 if len(trend) else 0.0
                t_color = '#1e8e3e' if trend_ret >= 0 else '#c5221f'

                fig_tr = go.Figure()
                fig_tr.add_trace(go.Scatter(
                    x=trend.index, y=trend.round(2),
                    mode='lines', name=_t('ota.history_trend_label'),
                    line=dict(color=t_color, width=2),
                    hovertemplate='%{x|%Y-%m-%d}<br>%{y:.2f} (Basis 100)<extra></extra>',
                ))
                fig_tr.add_hline(y=100, line_dash='dot', line_color='#9aa0a6')
                fig_tr.update_layout(
                    template='plotly_white', height=300,
                    margin=dict(l=10, r=10, t=30, b=10),
                    yaxis_title=_t('ota.history_trend_label'), xaxis_title=None,
                    showlegend=False,
                    title=dict(text=f'{trend_ret:+.1f}% ({_t("ota.history_trend_twr")})',
                               font=dict(size=14)),
                )
                st.plotly_chart(fig_tr, use_container_width=True)
