import pandas as pd
import numpy as np
import datetime as dt
import logging
import streamlit as st
import uuid
import plotly.express as px
import plotly.graph_objs as go
from concurrent.futures import ThreadPoolExecutor, as_completed

from tradinglib import tools, ticker_tools as tt, asset_simulator as ass, main_page as mp
from tradinglib.i18n import t

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
        r.info(t('ota.no_trades_db'))
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
        r.error(t('ota.no_action_col'))
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

    r.markdown(t('ota.trades_loaded', total=len(raw), open=len(open_tickers), closed=len(closed_tickers)))

    # ══════════════════════════════════════════════════════════════════════════
    # A) ABGESCHLOSSENE TRADES – Normierter Kursverlauf
    # ══════════════════════════════════════════════════════════════════════════
    with r.expander(t('ota.section_closed'), expanded=not closed_df.empty):

        if closed_df.empty:
            st.info(t('ota.no_closed_trades'))
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
            st.markdown(t('ota.norm_chart_closed'))
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
                                  title=t('ota.cum_pnl_title'), template='plotly_white')
                st.plotly_chart(fig_cum, use_container_width=True)
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    # B) OFFENE POSITIONEN – Rebalancing & Paritäts-Analyse
    # ══════════════════════════════════════════════════════════════════════════
    with r.expander(t('ota.section_open'), expanded=True):

        if open_df.empty:
            st.info(t('ota.no_open_positions'))
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
            st.markdown(t('ota.open_positions_header'))
            disp_cols_o = [c for c in ['ticker', 'longName', 'isin', 'shares', 'avg_price',
                                        'current_price', 'buy_value', 'current_value',
                                        'unrealized_pnl', 'pnl_pct', 'weight_pct'] if c in agg.columns]
            st.dataframe(agg[disp_cols_o], hide_index=True, use_container_width=True,
                         column_config={
                             'ticker':        st.column_config.TextColumn(t('ota.col_ticker')),
                             'longName':      st.column_config.TextColumn(t('ota.col_name')),
                             'shares':        st.column_config.NumberColumn(t('ota.col_shares'),       format='%.2f'),
                             'avg_price':     st.column_config.NumberColumn(t('ota.col_avg_buy'),      format='%.4f'),
                             'current_price': st.column_config.NumberColumn(t('ota.col_current_price'), format='%.4f'),
                             'buy_value':     st.column_config.NumberColumn(t('ota.col_cost_basis', currency=system_currency),   format='%.2f'),
                             'current_value': st.column_config.NumberColumn(t('ota.col_market_value', currency=system_currency), format='%.2f'),
                             'unrealized_pnl':st.column_config.NumberColumn(t('ota.col_pnl', currency=system_currency),         format='%.2f'),
                             'pnl_pct':       st.column_config.NumberColumn(t('ota.col_pnl_pct'),      format='%.1f'),
                             'weight_pct':    st.column_config.NumberColumn(t('ota.col_weight_pct'),   format='%.1f'),
                         })

            total_invest = agg['buy_value'].sum()
            total_gain   = agg['unrealized_pnl'].sum()
            gain_pct     = (total_gain / total_invest * 100) if total_invest > 0 else 0.0
            # Investitionsgewichtung (Basis: Einstands-Kapital, nicht Marktwert)
            agg['invest_pct'] = np.where(
                total_invest > 0, (agg['buy_value'] / total_invest * 100).round(2), 0.0
            )
            m1, m2, m3 = st.columns(3)
            m1.metric(t('ota.metric_total_value'), f'{total_open_value:,.2f} {system_currency}')
            m2.metric(t('ota.metric_cost_basis'),  f'{total_invest:,.2f} {system_currency}')
            m3.metric(t('ota.metric_pnl'),          f'{total_gain:+,.2f} {system_currency}', delta=f'{gain_pct:+.1f}%')

            # ── Normalised trend chart – open positions ───────────────────
            st.markdown(t('ota.norm_chart_open'))
            st.caption(t('ota.norm_chart_caption'))

            date_c1, date_c2 = st.columns(2)
            try:
                earliest_buy = open_df['_ts'].min()
                default_start = earliest_buy.date() if not pd.isna(earliest_buy) else dt.date.today() - dt.timedelta(days=365)
            except Exception:
                default_start = dt.date.today() - dt.timedelta(days=365)

            start_date = date_c1.date_input(
                t('ota.date_start'), default_start, format='YYYY-MM-DD', key='ptf_open_start'
            ).strftime('%Y-%m-%d')
            end_date = date_c2.date_input(
                t('ota.date_end'), dt.date.today(), format='YYYY-MM-DD', key='ptf_open_end'
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
            st.markdown(t('ota.parity_header'))
            st.caption(t('ota.parity_caption', invest=f'{total_invest:,.2f}', currency=system_currency))

            opt_weights = _compute_parity_weights(open_series, window=30)
            if opt_weights.empty:
                st.warning(t('ota.parity_no_data'))
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
                    lambda d: t('ota.action_buy') if d > 1 else (t('ota.action_sell') if d < -1 else t('ota.action_ok'))
                )
                # Target and current amounts both anchored to cost basis
                parity_df['Target']    = (parity_df['Parity %']  / 100 * total_invest).round(2)
                parity_df['Invested']  = (parity_df['Current %'] / 100 * total_invest).round(2)
                parity_df['Delta']     = (parity_df['Target'] - parity_df['Invested']).round(2)

                st.dataframe(
                    parity_df[['Ticker', 'Current %', 'Parity %', 'Diff %',
                               'Action', 'Target', 'Invested', 'Delta']],
                    column_config={
                        'Current %': st.column_config.NumberColumn(t('ota.col_cost_basis_pct'), format='%.2f'),
                        'Parity %':  st.column_config.NumberColumn(t('ota.col_parity_pct'),     format='%.2f'),
                        'Diff %':    st.column_config.NumberColumn(t('ota.col_diff_pct'),        format='%.2f'),
                        'Target':    st.column_config.NumberColumn(t('ota.col_target',   currency=system_currency), format='%.2f'),
                        'Invested':  st.column_config.NumberColumn(t('ota.col_invested', currency=system_currency), format='%.2f'),
                        'Delta':     st.column_config.NumberColumn(t('ota.col_delta',    currency=system_currency), format='%.2f'),
                    },
                    hide_index=True,
                    use_container_width=True,
                )

                # Pie charts: current (cost basis) vs. parity target
                pie_c1, pie_c2 = st.columns(2)
                fig_ist = px.pie(
                    agg, values='invest_pct', names='ticker',
                    title=t('ota.pie_current'), hole=0.4,
                )
                fig_ist.update_traces(textinfo='percent+label')
                pie_c1.plotly_chart(fig_ist, use_container_width=True)

                fig_soll = px.pie(
                    parity_df, values='Parity %', names='Ticker',
                    title=t('ota.pie_target'), hole=0.4,
                )
                fig_soll.update_traces(textinfo='percent+label')
                pie_c2.plotly_chart(fig_soll, use_container_width=True)

                # Action items
                st.markdown(t('ota.action_items'))
                buys  = parity_df[parity_df['Diff %'] >  1].sort_values('Diff %', ascending=False)
                sells = parity_df[parity_df['Diff %'] < -1].sort_values('Diff %')
                if not buys.empty:
                    st.success(t('ota.add_position'))
                    st.dataframe(buys[['Ticker', 'Current %', 'Parity %', 'Delta']],
                                 hide_index=True, use_container_width=True)
                if not sells.empty:
                    st.warning(t('ota.reduce_position'))
                    st.dataframe(sells[['Ticker', 'Current %', 'Parity %', 'Delta']],
                                 hide_index=True, use_container_width=True)
                if buys.empty and sells.empty:
                    st.info(t('ota.balanced'))


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
                st_region.info(t('ota.no_processed_trades'))
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
                if simulator is not None and st_region.button(t('ota.analyze_stored'), key='analyze_stored'):
                    try:
                        simulator.render_parity(proc_preview, suffix='stored', invest=100000, region=st_region)
                    except Exception as e:
                        st_region.error(f'Failed to run parity analysis: {e}')
            except Exception:
                pass
    except Exception:
        pass
    uploaded = st_region.file_uploader(t('ota.upload_label'), type=['csv','xlsx'], key='upload_trades_file')
    df_u = None
    if uploaded is not None:
        try:
            if uploaded.name.lower().endswith('.xlsx'):
                df_u = pd.read_excel(uploaded)
            else:
                df_u = pd.read_csv(uploaded)
        except Exception as e:
            st_region.error(t('ota.upload_error', error=e))
            df_u = None

    if df_u is not None:
        df_u.columns = [c.strip() for c in df_u.columns]
        try:
            st_region.write(t('ota.upload_preview'))
            st_region.dataframe(df_u.head())
        except Exception:
            pass


        # OwnTradesManager is provided at module level; instantiate it from the page when needed.


        # simple auto-mapping UI (only show when a file was uploaded)
        if df_u is not None:
            expected = [
                ('timestamp', t('ota.map_col_timestamp')),
                ('action',    t('ota.map_col_action')),
                ('ticker',    t('ota.map_col_ticker')),
                ('price',     t('ota.map_col_price')),
                ('shares',    t('ota.map_col_shares')),
                ('value',     t('ota.map_col_value')),
                ('longName',  t('ota.map_col_longname')),
                ('isin',      t('ota.map_col_isin')),
                ('currency',  t('ota.map_col_currency')),
                ('stockIndex', t('ota.map_col_index')),
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

            if st_region.button(t('ota.apply_import'), key='apply_mapping_import'):
                try:
                    inserted, mapped, processed = import_trades(df_u, user_map, username=username, db_path=db_path, db_name='trades.db')
                    st_region.success(t('ota.import_success', n=inserted))
                    try:
                        st_region.write(t('ota.mapped_preview'))
                        st_region.dataframe(mapped.head())
                    except Exception:
                        pass
                    try:
                        if not processed.empty:
                            st_region.write(t('ota.processed_preview'))
                            st_region.dataframe(processed.head())
                            if simulator is not None and st_region.button(t('ota.analyze_imported'), key='analyze_imported'):
                                try:
                                    simulator.render_parity(processed, suffix='imported', invest=100000, region=st_region)
                                except Exception as e:
                                    st_region.error(f'Failed to run parity analysis: {e}')
                            # Also refresh the DB-derived processed/paired preview so stored trades are visible
                            try:
                                db_proc_res = analyze_own_trades(db_path=db_path, db_name='trades.db', system_currency='EUR')
                                db_proc = db_proc_res.get('processed', pd.DataFrame())
                                if db_proc is not None and not db_proc.empty:
                                    st_region.write(t('ota.db_preview'))
                                    try:
                                        st_region.dataframe(db_proc.head(20))
                                    except Exception:
                                        st_region.write(db_proc.head(20).to_string())
                                    try:
                                        if simulator is not None and st_region.button(t('ota.analyze_stored'), key='analyze_stored_after_import'):
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
            st_region.write(t('ota.stored_rows', db=dbt.database_name, rows=row_count))
            try:
                st_region.dataframe(df_all.head(20))
            except Exception:
                st_region.write(df_all.head(20).to_string() if df_all is not None else 'No rows')

            try:
                csv_bytes = (df_all.to_csv(index=False).encode('utf-8')) if df_all is not None else b''
                if csv_bytes:
                    st_region.download_button(t('ota.export_csv'), data=csv_bytes, file_name='trades_export.csv', mime='text/csv')
            except Exception:
                pass
            try:
                xlsx = df_to_excel_bytes(df_all) if df_all is not None else b''
                if xlsx:
                    st_region.download_button(t('ota.export_xlsx'), data=xlsx, file_name='trades_export.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            except Exception:
                pass
        except Exception:
            pass

        # Also show the processed/pairing preview using analyze_own_trades (helps verify pairing)
        try:
            res = analyze_own_trades(db_path=db_path, db_name='trades.db', system_currency='EUR')
            proc = res.get('processed', pd.DataFrame())
            proc_rows = 0 if proc is None else len(proc)
            st_region.write(t('ota.processed_rows', rows=proc_rows))
            try:
                if proc is not None and not proc.empty:
                    st_region.dataframe(proc.head(20))
                else:
                    st_region.write(t('ota.no_processed_available'))
            except Exception:
                if proc is not None:
                    st_region.write(proc.head(20).to_string())
                else:
                    st_region.write(t('ota.no_processed_available'))
        except Exception:
            pass

        # Dangerous operation: delete all trades
        st_region.markdown('---')
        st_region.markdown(t('ota.danger_label'), unsafe_allow_html=True)
        delete_confirm = st_region.text_input(t('ota.delete_confirm'), key='confirm_delete_trades')
        if st_region.button(t('ota.delete_btn'), key='delete_trades_btn'):
            if delete_confirm == 'DELETE':
                try:
                    try:
                        dbt.cursor.execute('DELETE FROM trades')
                        dbt.conn.commit()
                        st_region.success(t('ota.delete_success'))
                    except Exception:
                        st_region.warning(t('ota.delete_no_table'))
                except Exception as e:
                    st_region.error(f'Failed to delete trades: {e}')
            else:
                st_region.warning(t('ota.delete_confirm_warning'))

        try:
            dbt.close()
        except Exception:
            pass
    except Exception:
        pass
