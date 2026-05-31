import pandas as pd
import numpy as np
import datetime as dt
import logging
import streamlit as st
import uuid
import plotly.express as px
import plotly.graph_objs as go
from concurrent.futures import ThreadPoolExecutor, as_completed

from tradinglib import tools, ticker_tools as tt, main_page as mp
try:
    from tradinglib.premium import asset_simulator as ass
except ImportError:
    ass = None
from tradinglib.i18n import t as _t

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio Analysis helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_close_series_for_portfolio(ticker: str, db_path: str = 'database') -> pd.Series:
    """Close-Preise für einen Ticker aus der lokalen DB laden, Fallback auf Netzwerk."""
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
    """Letzten bekannten Schlusskurs für einen Ticker ermitteln."""
    s = _fetch_close_series_for_portfolio(ticker, db_path)
    if s is not None and not s.empty:
        return float(s.iloc[-1])
    return 0.0


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
        title='Normalised Price Chart (Base 100)',
        xaxis_title='Date',
        yaxis_title='Indexed (Start = 100)',
        template='plotly_white',
        height=500,
        hovermode='x unified',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0),
    )
    return fig


def _compute_parity_weights(series_dict: dict, window: int = 30) -> pd.Series:
    """
    Inverse-Volatility Gewichte auf Basis der letzten `window` Tagesdaten.
    Gibt pd.Series mit Ticker-Index und Gewichten (Summe=1) zurück.
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
            long_name  = row.get('longName') or row.get('shortName') or ''
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


def render_portfolio_analysis(region=st, db_path: str = 'database', username: str = '', system_currency: str = 'EUR'):
    """
    Portfolio analysis read directly from trades.db (no upload needed).
    A) Closed trades   – normalised price chart over the holding period
    B) Open positions  – current portfolio value, parity analysis, rebalancing
    """
    r = region

    # ── Rohdaten direkt aus trades.db lesen (TradeProcessor wird nicht verwendet,
    #    da Spaltennamen in der DB case-sensitiv abweichen können) ─────────────
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

    # Spaltennamen normalisieren (case-insensitive Lookup)
    raw.columns = [c.strip() for c in raw.columns]
    _col = {c.lower(): c for c in raw.columns}   # lower → Originalname

    def _col_val(row, *names):
        for n in names:
            c = _col.get(n.lower())
            if c and c in row.index:
                return row[c]
        return None

    def _df_col(df, *names):
        for n in names:
            c = _col.get(n.lower())
            if c and c in df.columns:
                return df[c]
        return pd.Series([None] * len(df), index=df.index)

    # action-Spalte normalisieren
    act_col = _col.get('action', 'action')
    if act_col not in raw.columns:
        r.error(_t('ota.no_action_col'))
        return
    raw[act_col] = raw[act_col].astype(str).str.lower().str.strip()

    # shares / price / value normalisieren
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

    # ── Offene vs. geschlossene Positionen bestimmen ──────────────────────
    # Netto-Shares pro Ticker: open = buy_shares - sell_shares > 0
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

    # ══════════════════════════════════════════════════════════════════════════
    # A) ABGESCHLOSSENE TRADES – Normierter Kursverlauf
    # ══════════════════════════════════════════════════════════════════════════
    with r.expander(_t('ota.section_closed'), expanded=not closed_df.empty):

        if closed_df.empty:
            st.info(_t('ota.no_closed_trades'))
        else:
            # Für jeden Sell-Trade die früheste passende Buy-Transaktion ermitteln
            closed_display = []
            for _, sell_row in closed_df.iterrows():
                t = sell_row['_ticker']
                sell_ts = sell_row['_ts']
                sell_price = sell_row['_price']
                sell_shares = sell_row['_shares']
                # Passenden Kauf suchen (gleicher Ticker, gleiche Stückzahl vor dem Verkauf)
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
                    # Kein exakter Match → frühester Kauf des Tickers
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
            for entry in closed_display:
                ticker   = entry['Ticker']
                buy_ts   = entry['_buy_ts']
                sell_ts  = entry['_sell_ts']
                s = closed_series.get(ticker)
                if s is None or s.empty:
                    continue
                s = s.copy()
                s.index = pd.to_datetime(s.index, errors='coerce')
                try:
                    segment = s[(s.index >= pd.Timestamp(buy_ts)) & (s.index <= pd.Timestamp(sell_ts))]
                except Exception:
                    segment = s
                if segment.empty or segment.iloc[0] == 0:
                    continue
                norm  = (segment / segment.iloc[0]) * 100
                label = f'{ticker} ({pd.Timestamp(buy_ts).strftime("%Y-%m-%d") if not pd.isna(buy_ts) else "?"})'
                color = '#2ca02c' if entry['_gain_pct'] >= 0 else '#d62728'
                fig_closed.add_trace(go.Scatter(
                    x=norm.index, y=norm.values.round(2),
                    mode='lines', name=label, line=dict(color=color),
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
    # B) OFFENE POSITIONEN – Rebalancing & Paritäts-Analyse
    # ══════════════════════════════════════════════════════════════════════════
    with r.expander(_t('ota.section_open'), expanded=True):

        if open_df.empty:
            st.info(_t('ota.no_open_positions'))
        else:
            # Mehrere Lots desselben Tickers aggregieren (normierte Spalten _ticker, _shares, _value, …)
            agg = open_df.groupby('_ticker', as_index=False).agg(
                longName  = ('_longname', 'first'),
                isin      = ('_isin',     'first'),
                currency  = ('_currency', 'first'),
                shares    = ('_shares',   'sum'),
                buy_value = ('_value',    lambda x: abs(x).sum()),
            ).rename(columns={'_ticker': 'ticker'})
            agg['avg_price'] = np.where(agg['shares'] > 0, agg['buy_value'] / agg['shares'], 0.0)

            tickers_open = agg['ticker'].tolist()

            # ── Metadaten aus asset_info.db ergänzen (longName + Kurs-Fallback) ──
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
            # Fallback: currentPrice aus asset_info.db wenn OHLC-Daten fehlen
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
            st.dataframe(agg[disp_cols_o], hide_index=True, use_container_width=True,
                         column_config={
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

            total_invest = agg['buy_value'].sum()
            total_gain   = agg['unrealized_pnl'].sum()
            gain_pct     = (total_gain / total_invest * 100) if total_invest > 0 else 0.0
            # Investitionsgewichtung (Basis: Einstands-Kapital, nicht Marktwert)
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

                st.dataframe(
                    parity_df[['Ticker', 'Current %', 'Parity %', 'Diff %',
                               'Action', 'Target', 'Invested', 'Delta']],
                    column_config={
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


class OwnTradesManager:
    """Lightweight manager for own trades so pages can import and use it.

    Provides load_and_process (wraps analyze_own_trades) and a simple render() to
    display counts and a preview. This is intentionally minimal so it won't
    trigger network calls or heavy processing at import time.
    """
    def __init__(self, simulator=None, sys_config=None, system_currency='EUR', db_path='database', db_name='trades.db', username=''):
        self.simulator = simulator
        self.sys_config = sys_config
        self.system_currency = system_currency
        self.db_path = db_path
        self.db_name = db_name
        self.username = username
        self.raw = pd.DataFrame()
        self.trades_df = pd.DataFrame()

    def load_and_process(self, asset_date=None, use_year=None):
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
            processed.sort_values(['sellDate', 'ticker'], ascending=[True, True], inplace=True)
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
# Scalable Capital CSV import
# ─────────────────────────────────────────────────────────────────────────────

_SCALABLE_COLS = {'date', 'time', 'status', 'reference', 'description',
                  'assettype', 'type', 'isin', 'shares', 'price',
                  'amount', 'fee', 'tax', 'currency'}

_SCALABLE_ACTION_MAP = {
    'buy':               'buy',
    'sell':              'sell',
    'distribution':      'dividend',
    'interest':          'interest',
    'fee':               'fee',
    'cash transfer out': 'transfer_out',
    'cash transfer in':  'transfer_in',
}


def _is_scalable_csv(df: pd.DataFrame) -> bool:
    """Return True if df looks like a Scalable Capital export."""
    cols = {c.lower().strip() for c in df.columns}
    return _SCALABLE_COLS.issubset(cols)


def _parse_de_number(val) -> float:
    """Parse German-format number string → float. Returns 0.0 on failure."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    try:
        # Remove thousands separator, replace decimal comma
        s = s.replace('.', '').replace(',', '.')
        return float(s)
    except Exception:
        return 0.0


def _resolve_isin_to_ticker(isin: str, db_path: str = 'database') -> str:
    """
    Look up Yahoo Finance ticker for an ISIN.

    Resolution order:
      1. broker_symbol_cache in asset_info.db  (keyed by yf_isin)
      2. asset_info table in asset_info.db      (ticker/symbol + isin columns)
      3. yfinance: yf.Ticker(isin).info['symbol']
      4. Fallback: return isin unchanged

    Successful results from step 3 are written back to broker_symbol_cache.
    """
    if not isin or not isinstance(isin, str):
        return isin or ''
    isin = isin.strip().upper()

    # ── 1. broker_symbol_cache ─────────────────────────────────────────────
    try:
        import sqlite3 as _sq
        import os
        info_db = os.path.join(db_path, 'asset_info.db')
        with _sq.connect(info_db) as conn:
            row = conn.execute(
                "SELECT yahoo_ticker FROM broker_symbol_cache WHERE yf_isin=? LIMIT 1",
                (isin,),
            ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass

    # ── 2. asset_info table ────────────────────────────────────────────────
    try:
        with _sq.connect(info_db) as conn:
            row = conn.execute(
                "SELECT ticker, symbol FROM asset_info WHERE isin=? LIMIT 1",
                (isin,),
            ).fetchone()
        if row:
            t = row[0] or row[1]
            if t:
                return str(t).strip().upper()
    except Exception:
        pass

    # ── 3. yfinance lookup ─────────────────────────────────────────────────
    try:
        import yfinance as yf
        info = yf.Ticker(isin).info
        symbol = info.get('symbol') or ''
        if symbol and symbol != isin:
            # Cache result
            try:
                with _sq.connect(info_db) as conn:
                    conn.execute("""
                        INSERT INTO broker_symbol_cache
                            (yahoo_ticker, yf_isin, resolved_at, source)
                        VALUES (?, ?, ?, 'isin_lookup')
                        ON CONFLICT(yahoo_ticker) DO UPDATE SET
                            yf_isin=excluded.yf_isin,
                            resolved_at=excluded.resolved_at,
                            source=excluded.source
                    """, (symbol.upper(), isin, dt.datetime.now().isoformat()))
            except Exception:
                pass
            return symbol.upper()
    except Exception:
        pass

    return isin  # fallback: use ISIN as ticker


def parse_scalable_csv(df_raw: pd.DataFrame) -> dict:
    """
    Parse a raw Scalable Capital CSV DataFrame into categorised sub-frames.

    Returns a dict with keys:
      trades      – Buy/Sell rows mapped to the internal trades schema
      dividends   – Distribution rows
      interest    – Interest rows
      fees        – Fee rows
      transfers   – Cash Transfer rows
      skipped     – Cancelled / unrecognised rows
    """
    df = df_raw.copy()
    df.columns = [c.strip() for c in df.columns]

    # Normalise column names to lowercase for reliable access
    col_lower = {c.lower(): c for c in df.columns}

    def _col(name: str) -> pd.Series:
        orig = col_lower.get(name.lower())
        return df[orig] if orig else pd.Series([''] * len(df), index=df.index)

    # ── Filter: keep only Executed rows ───────────────────────────────────
    status = _col('status').astype(str).str.strip().str.lower()
    df = df[status == 'executed'].copy()
    skipped_count = len(df_raw) - len(df)

    if df.empty:
        empty = pd.DataFrame()
        return dict(trades=empty, dividends=empty, interest=empty,
                    fees=empty, transfers=empty, skipped=skipped_count)

    # Re-derive col_lower after filter
    col_lower = {c.lower(): c for c in df.columns}

    def _s(name):
        orig = col_lower.get(name.lower())
        return df[orig].copy() if orig else pd.Series([''] * len(df), index=df.index)

    # ── timestamp ─────────────────────────────────────────────────────────
    date_s = _s('date').astype(str).str.strip()
    time_s = _s('time').astype(str).str.strip()
    df['_timestamp'] = pd.to_datetime(
        date_s + ' ' + time_s, format='%d.%m.%Y %H:%M:%S', errors='coerce'
    )

    # ── numeric columns ───────────────────────────────────────────────────
    for src, dst in [('shares', '_shares'), ('price', '_price'),
                     ('amount', '_amount'), ('fee', '_fee'), ('tax', '_tax')]:
        df[dst] = _s(src).apply(_parse_de_number)

    # ── string columns ────────────────────────────────────────────────────
    df['_type']     = _s('type').astype(str).str.strip().str.lower()
    df['_action']   = df['_type'].map(_SCALABLE_ACTION_MAP).fillna('other')
    df['_isin']     = _s('isin').astype(str).str.strip().str.upper().replace('NAN', '')
    df['_longname'] = _s('description').astype(str).str.strip()
    df['_currency'] = _s('currency').astype(str).str.strip().str.upper()
    df['_ref']      = _s('reference').astype(str).str.strip()

    # ── split by category ─────────────────────────────────────────────────
    is_trade    = df['_action'].isin(['buy', 'sell'])
    is_dividend = df['_action'] == 'dividend'
    is_interest = df['_action'] == 'interest'
    is_fee      = df['_action'] == 'fee'
    is_transfer = df['_action'].isin(['transfer_out', 'transfer_in'])

    def _make_display(mask, extra_cols=None):
        sub = df[mask].copy()
        cols = ['_timestamp', '_action', '_isin', '_longname',
                '_shares', '_price', '_amount', '_fee', '_tax', '_currency', '_ref']
        if extra_cols:
            cols += extra_cols
        sub = sub[[c for c in cols if c in sub.columns]]
        sub.columns = [c.lstrip('_') for c in sub.columns]
        return sub.reset_index(drop=True)

    trades_raw    = _make_display(is_trade)
    dividends_raw = _make_display(is_dividend)
    interest_raw  = _make_display(is_interest)
    fees_raw      = _make_display(is_fee)
    transfers_raw = _make_display(is_transfer)

    return dict(
        trades=trades_raw,
        dividends=dividends_raw,
        interest=interest_raw,
        fees=fees_raw,
        transfers=transfers_raw,
        skipped=skipped_count,
        raw_executed=df,
    )


def _resolve_tickers_for_df(df: pd.DataFrame, db_path: str, status_placeholder=None) -> pd.DataFrame:
    """
    Add a 'ticker' column to a parsed sub-frame by resolving each ISIN.
    Updates status_placeholder (st.empty) with progress if provided.
    """
    if df.empty or 'isin' not in df.columns:
        return df
    df = df.copy()
    isins = df['isin'].dropna().unique().tolist()
    isins = [i for i in isins if i and i != 'NAN' and len(i) > 3]
    isin_ticker_map = {}
    for idx, isin in enumerate(isins):
        if status_placeholder:
            try:
                status_placeholder.caption(f'Resolving {isin} ({idx+1}/{len(isins)}) …')
            except Exception:
                pass
        isin_ticker_map[isin] = _resolve_isin_to_ticker(isin, db_path)
    if status_placeholder:
        try:
            status_placeholder.empty()
        except Exception:
            pass
    df['ticker'] = df['isin'].map(isin_ticker_map).fillna(df['isin'])
    return df


def render_scalable_import(region=st, db_path: str = 'database', system_currency: str = 'EUR'):
    """
    Full Scalable Capital CSV import UI.

    Parses, resolves tickers, shows categorised preview tabs,
    and writes Buy/Sell (+ optionally Dividends) into trades.db.
    """
    r = region
    r.markdown('### Scalable Capital — CSV Import')
    r.caption(
        'Export: Scalable Capital App → Konto → Transaktionen → CSV exportieren. '
        'Nur Zeilen mit Status **Executed** werden verarbeitet.'
    )

    uploaded = r.file_uploader(
        'Scalable Capital CSV hochladen',
        type=['csv'],
        key='scalable_upload',
    )
    if uploaded is None:
        return

    # ── Parse ─────────────────────────────────────────────────────────────
    try:
        df_raw = pd.read_csv(
            uploaded,
            sep=';',
            decimal=',',
            thousands='.',
            dtype=str,          # read everything as string first
            skip_blank_lines=True,
        ).dropna(how='all')
    except Exception as e:
        r.error(f'CSV konnte nicht gelesen werden: {e}')
        return

    if not _is_scalable_csv(df_raw):
        r.error(
            'Die Datei sieht nicht wie ein Scalable Capital Export aus. '
            f'Gefundene Spalten: {list(df_raw.columns)}'
        )
        return

    parsed = parse_scalable_csv(df_raw)
    trades_df    = parsed['trades']
    dividends_df = parsed['dividends']
    interest_df  = parsed['interest']
    fees_df      = parsed['fees']
    transfers_df = parsed['transfers']
    skipped      = parsed['skipped']

    total_executed = sum(
        len(v) for k, v in parsed.items()
        if isinstance(v, pd.DataFrame)
    )
    r.success(
        f'**{total_executed}** ausgeführte Transaktionen gefunden '
        f'({skipped} Cancelled-Einträge ignoriert).'
    )

    # ── Ticker-Auflösung ──────────────────────────────────────────────────
    resolve_now = r.checkbox(
        'ISIN → Ticker auflösen (benötigt Internet, dauert einen Moment)',
        value=True,
        key='scalable_resolve_tickers',
    )
    status_ph = r.empty()
    if resolve_now:
        with r.spinner('Ticker werden aufgelöst …'):
            trades_df    = _resolve_tickers_for_df(trades_df,    db_path, status_ph)
            dividends_df = _resolve_tickers_for_df(dividends_df, db_path, status_ph)
            interest_df  = _resolve_tickers_for_df(interest_df,  db_path, status_ph)
    else:
        for df_ in [trades_df, dividends_df, interest_df]:
            if not df_.empty and 'isin' in df_.columns:
                df_['ticker'] = df_['isin']

    # ── Preview tabs ──────────────────────────────────────────────────────
    tab_t, tab_d, tab_i, tab_f, tab_tf = r.tabs([
        f'Trades ({len(trades_df)})',
        f'Dividenden ({len(dividends_df)})',
        f'Zinsen ({len(interest_df)})',
        f'Gebühren ({len(fees_df)})',
        f'Transfers ({len(transfers_df)})',
    ])

    _NUM_FMT = dict(
        shares   = st.column_config.NumberColumn('Stück',    format='%.4f'),
        price    = st.column_config.NumberColumn('Kurs',     format='%.4f'),
        amount   = st.column_config.NumberColumn(f'Betrag ({system_currency})', format='%.2f'),
        fee      = st.column_config.NumberColumn('Gebühr',   format='%.2f'),
        tax      = st.column_config.NumberColumn('Steuer',   format='%.2f'),
        timestamp= st.column_config.DatetimeColumn('Datum/Zeit', format='YYYY-MM-DD HH:mm'),
    )

    with tab_t:
        if trades_df.empty:
            st.info('Keine Trades gefunden.')
        else:
            _show_scalable_df(trades_df, _NUM_FMT)
            # Summary
            buys  = trades_df[trades_df['action'] == 'buy' ]['amount'].apply(abs).sum()
            sells = trades_df[trades_df['action'] == 'sell']['amount'].sum()
            tax_t = trades_df['tax'].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric('Käufe (investiert)', f'{buys:,.2f} {system_currency}')
            c2.metric('Verkäufe (Erlöse)',  f'{sells:,.2f} {system_currency}')
            c3.metric('Steuern (Trades)',   f'{tax_t:,.2f} {system_currency}')

    with tab_d:
        if dividends_df.empty:
            st.info('Keine Dividenden gefunden.')
        else:
            _show_scalable_df(dividends_df, _NUM_FMT)
            total_div = dividends_df['amount'].sum()
            total_tax = dividends_df['tax'].sum()
            c1, c2 = st.columns(2)
            c1.metric('Brutto-Dividenden', f'{total_div:,.2f} {system_currency}')
            c2.metric('Einbehaltene Steuer', f'{total_tax:,.2f} {system_currency}')

    with tab_i:
        if interest_df.empty:
            st.info('Keine Zinsen gefunden.')
        else:
            _show_scalable_df(interest_df, _NUM_FMT)
            st.metric('Zinsen gesamt', f'{interest_df["amount"].sum():,.2f} {system_currency}')

    with tab_f:
        if fees_df.empty:
            st.info('Keine Gebühren gefunden.')
        else:
            _show_scalable_df(fees_df, _NUM_FMT)
            st.metric('Gebühren gesamt', f'{fees_df["amount"].sum():,.2f} {system_currency}')

    with tab_tf:
        if transfers_df.empty:
            st.info('Keine Transfers gefunden.')
        else:
            _show_scalable_df(transfers_df, _NUM_FMT)
            st.metric('Transfers gesamt', f'{transfers_df["amount"].sum():,.2f} {system_currency}')

    # ── Cashflow-Zusammenfassung ──────────────────────────────────────────
    r.markdown('---')
    r.markdown('#### Cashflow-Übersicht')
    inv    = trades_df[trades_df['action'] == 'buy' ]['amount'].apply(abs).sum() if not trades_df.empty else 0
    rev    = trades_df[trades_df['action'] == 'sell']['amount'].sum()              if not trades_df.empty else 0
    div    = dividends_df['amount'].sum()  if not dividends_df.empty else 0
    intr   = interest_df['amount'].sum()   if not interest_df.empty else 0
    fee_s  = abs(fees_df['amount'].sum())  if not fees_df.empty else 0
    tax_s  = sum([
        trades_df['tax'].sum()    if not trades_df.empty    else 0,
        dividends_df['tax'].sum() if not dividends_df.empty else 0,
    ])
    net_cash = rev + div + intr - inv - fee_s - tax_s

    cf1, cf2, cf3, cf4, cf5, cf6 = r.columns(6)
    cf1.metric('Investiert',  f'{inv:,.2f} {system_currency}')
    cf2.metric('Erlöse',      f'{rev:,.2f} {system_currency}')
    cf3.metric('Dividenden',  f'{div:,.2f} {system_currency}')
    cf4.metric('Zinsen',      f'{intr:,.2f} {system_currency}')
    cf5.metric('Gebühren',    f'-{fee_s:,.2f} {system_currency}')
    cf6.metric('Steuern',     f'-{tax_s:,.2f} {system_currency}')
    r.metric('Netto-Cashflow', f'{net_cash:+,.2f} {system_currency}',
             delta=f'{net_cash:+,.2f}')

    # ── Import-Button ─────────────────────────────────────────────────────
    r.markdown('---')
    import_dividends = r.checkbox(
        'Dividenden und Zinsen ebenfalls importieren',
        value=True,
        key='scalable_import_dividends',
    )
    import_fees = r.checkbox(
        'Gebühren ebenfalls importieren',
        value=False,
        key='scalable_import_fees',
    )

    if r.button('In trades.db importieren', type='primary', key='scalable_do_import'):
        frames_to_import = [trades_df]
        if import_dividends:
            frames_to_import += [dividends_df, interest_df]
        if import_fees:
            frames_to_import.append(fees_df)

        all_rows = pd.concat([f for f in frames_to_import if not f.empty], ignore_index=True)

        if all_rows.empty:
            r.warning('Keine Zeilen zum Importieren.')
            return

        inserted = _insert_scalable_rows(all_rows, db_path=db_path)
        r.success(f'{inserted} Zeilen erfolgreich in trades.db importiert.')


def _show_scalable_df(df: pd.DataFrame, col_cfg: dict):
    """Helper: show a parsed Scalable sub-frame with appropriate column config."""
    disp_cols = [c for c in ['timestamp', 'action', 'ticker', 'isin', 'longname',
                              'shares', 'price', 'amount', 'fee', 'tax', 'currency']
                 if c in df.columns]
    st.dataframe(
        df[disp_cols],
        hide_index=True,
        use_container_width=True,
        column_config={k: v for k, v in col_cfg.items() if k in disp_cols},
    )


def _insert_scalable_rows(df: pd.DataFrame, db_path: str = 'database') -> int:
    """Insert parsed Scalable Capital rows into trades.db. Returns inserted count."""
    dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
    inserted = 0
    try:
        for _, row in df.iterrows():
            ts_val = row.get('timestamp')
            if hasattr(ts_val, 'strftime'):
                ts_str = ts_val.strftime('%Y-%m-%d %H:%M:%S')
            else:
                ts_str = str(ts_val) if ts_val else ''

            rd = {
                'uuid':      uuid.uuid4().hex,
                'timestamp': ts_str,
                'action':    str(row.get('action', '')),
                'ticker':    str(row.get('ticker', row.get('isin', ''))),
                'isin':      str(row.get('isin', '')),
                'longName':  str(row.get('longname', '')),
                'shares':    float(row.get('shares', 0) or 0),
                'price':     float(row.get('price', 0) or 0),
                'value':     abs(float(row.get('amount', 0) or 0)),
                'fee':       float(row.get('fee', 0) or 0),
                'tax':       float(row.get('tax', 0) or 0),
                'currency':  str(row.get('currency', 'EUR')),
                'broker':    'ScalableCapital',
            }
            # Clean None/nan strings
            for k, v in rd.items():
                if isinstance(v, str) and v.lower() in ('nan', 'none', ''):
                    rd[k] = None if k not in ('uuid', 'action', 'currency', 'broker') else rd[k]

            try:
                dbt.ensure_table_and_columns(
                    keys=list(rd.keys()), row_dict=rd, database_name='trades'
                )
                dbt.insert_data(
                    keys=list(rd.keys()), row_dict=rd,
                    database_name='trades', replace=False,
                )
                inserted += 1
            except Exception as e:
                logger.warning(f'Scalable import: row skipped: {e}')

        dbt.conn.commit()
    finally:
        try:
            dbt.close()
        except Exception:
            pass
    return inserted


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
                    'uuid':      uuid.uuid4().hex,
                    'timestamp': timestamp,
                    'action':    action,
                    'ticker':    ticker,
                    'shares':    shares,
                    'price':     price,
                    'value':     value,
                    'currency':  currency.strip().upper() or system_currency,
                    'longName':  longname.strip() or None,
                    'isin':      isin.strip().upper() or None,
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
            r.dataframe(df_recent, hide_index=True, use_container_width=True,
                        column_config={
                            'timestamp': st.column_config.TextColumn('Date/Time'),
                            'shares':    st.column_config.NumberColumn(format='%.4f'),
                            'price':     st.column_config.NumberColumn(format='%.4f'),
                            'value':     st.column_config.NumberColumn(format='%.2f'),
                        })
    except Exception:
        pass


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
