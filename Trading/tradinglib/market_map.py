from tradinglib import tools
from tradinglib import ticker_tools as tt
from tradinglib.i18n import t
from tradinglib import main_page as mp
from tradinglib import make_query as mq
from tradinglib import system_config as sysconf
from tradinglib import tiny_chart as tc
from tradinglib.utils import DataUtils
from tradinglib.tools import open_db

import sqlite3
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


@st.cache_resource
def _cached_ticker_conn(ticker_path: str, perf_path: str, info_path: str):
    """Return a cached SQLite connection to yf_tickers.db with perf and info DBs attached."""
    conn = open_db(ticker_path, check_same_thread=False)
    conn.execute(f"ATTACH DATABASE '{perf_path}' AS performance_db")
    conn.execute(f"ATTACH DATABASE '{info_path}' AS info_db")
    return conn


@st.cache_resource
def _cached_info_conn(info_path: str):
    """Return a cached read-only SQLite connection to asset_info.db."""
    return open_db(info_path, readonly=True, check_same_thread=False)

import os
  
class DataVisualizer(tt.TickerTools):

    exch_column = 'ANY'
    
    def attach_dbs(self):
        """Resolve DB paths and populate self.ticker_conn via the cached connection helper."""
        t = tools.Tools()
        ticker_path = t.get_path(path=self.db_path, file_name=self.ticker_db)
        perf_path   = t.get_path(path=self.db_path, file_name=self.performance_db)
        info_path   = t.get_path(path=self.db_path, file_name=self.info_db)
        self.ticker_conn = _cached_ticker_conn(ticker_path, perf_path, info_path)

    def __init__(self, ticker_db, performance_db, info_db, index_column, db_path = 'database', username = ''):
        """
        Initializes the DataVisualizer class by connecting to the required databases.

        Args:
            ticker_db (str): Path to the ticker database (e.g., yf_tickers.db).
            performance_db (str): Path to the asset performance database.
            info_db (str): Path to the asset information database.
            index_column (str): Column name in yf_tickers table to filter data by index (e.g., 'GDAXI').
        """

        self.db_path=db_path
        self.ticker_db = ticker_db
        self.performance_db = performance_db
        self.info_db = info_db 
        self.username = username
        self.sys_config = sysconf.SystemConfig(username=username)
        self.system_currency = self.sys_config.get_value(f'system_currency')
        
        self.attach_dbs()     

        info_path = tools.Tools().get_path(path=self.db_path, file_name=info_db)
        self.info_conn = _cached_info_conn(info_path)
        self.index_column = index_column
        self.trend = 'moTrend'
        

    def dataframe_with_selections(self, df, column_config={}):
        """Render a data_editor with a Select checkbox and a Details link column."""
        df_with_selections = df.copy()
        try:
            column_config["details"] = st.column_config.LinkColumn(
                t('mm.col_details'), display_text=t('mm.col_view')
            )
            df_with_selections['details'] = df_with_selections['longName'].apply(self.add_url)
            cols = list(df_with_selections)
            cols.insert(0, cols.pop(cols.index('details')))
            df_with_selections = df_with_selections.loc[:, cols]
        except Exception:
            pass

        df_with_selections.insert(0, "Select", False)
        df_with_selections['Select'] = False

        edited_df = st.data_editor(
                df_with_selections,
                hide_index=True,
                column_config=dict(list(column_config.items()) + list({"Select": st.column_config.CheckboxColumn(required=False)}.items())),
            )

        # Filter the dataframe using the temporary column, then drop the column
        selected_rows = edited_df[edited_df.Select]
        return selected_rows.drop('Select', axis=1)


    def fetch_combined_data_with_attach(self, index_filter, c_size='', m_price='', o_by='', lim=0, query_on_yf=True):
        """Run the simulation query with optional market-cap/price filters and return the result DataFrame."""
        qry_ext = ''
        if not c_size == '' and not m_price == '':
            qry_ext = f' {qry_ext} AND {c_size} AND {m_price}'
        elif not c_size == '':
            qry_ext = f' {qry_ext} AND {c_size}'
        elif not m_price == '':
            qry_ext = f' {qry_ext} AND {m_price}'

        if qry_ext == '' and not self.exch_column == 'ANY':
            qry_ext = f" {qry_ext} AND ai.exchange = '{self.exch_column}'"
        elif not qry_ext == '' and not self.exch_column == 'ANY':
            qry_ext = f" {qry_ext} AND ai.exchange = '{self.exch_column}'"

        if not o_by == '':
            qry_ext = f" {qry_ext} ORDER BY {o_by} DESC"

        # avoid overloading yahoo servers           
        if not lim == 'ANY' or self.trend == 'day_change': 
            if lim == 'ANY':
                lim = 500
            qry_ext = f' {qry_ext} LIMIT {lim}'
            
        # SQL-Abfrage mit Prefixed-Tabellen
        # Pass self.ticker_conn so PRAGMA table_info reads columns from the
        # attached performance_db (asset_simulation_.db) directly.
        query = mq.make_query('asset_simulation', self.index_column, index_filter,
                               q_ext=qry_ext, conn=self.ticker_conn)
        combined_df = pd.read_sql_query(query, self.ticker_conn)
        dup_cols = [c for c in ['Date', 'ticker'] if c in combined_df.columns]
        if dup_cols:
            combined_df = combined_df.drop_duplicates(subset=dup_cols, keep='last')
        return combined_df
    
    def day_change(self, tickers):
        """
        Calculates the day-over-day percentage change from the last two closing
        prices stored in each ticker's local yf_<TICKER>.db (day_data table).

        Works correctly across weekends and holidays: the two most-recent rows
        in day_data are always the last two actual trading days, regardless of
        calendar gaps.

        Returns:
            pd.DataFrame with columns 'ticker', 'day_change', 'latestClose'.
        """
        changes = []
        for ticker in tickers:
            db_path = tools.Tools().get_path(path=self.db_path, file_name=f'yf_{ticker}.db')
            try:
                conn = open_db(db_path, readonly=True)
                df = pd.read_sql_query(
                    "SELECT Date, Close FROM day_data ORDER BY Date DESC LIMIT 2",
                    conn
                )
                conn.close()
                if len(df) == 2 and pd.notna(df['Close'].iloc[0]) and pd.notna(df['Close'].iloc[1]):
                    latest = df['Close'].iloc[0]
                    prev   = df['Close'].iloc[1]
                    changes.append({
                        "ticker":       ticker,
                        "day_change":   round((latest - prev) / prev * 100, 2),
                        "latestClose":  round(latest, 2),
                        "dc_date_cur":  df['Date'].iloc[0],
                        "dc_date_prev": df['Date'].iloc[1],
                    })
                else:
                    changes.append({"ticker": ticker, "day_change": None, "latestClose": None,
                                    "dc_date_cur": None, "dc_date_prev": None})
            except Exception:
                changes.append({"ticker": ticker, "day_change": None, "latestClose": None,
                                "dc_date_cur": None, "dc_date_prev": None})

        return (pd.DataFrame(changes) if changes
                else pd.DataFrame(columns=["ticker", "day_change", "latestClose", "dc_date_cur", "dc_date_prev"]))

    @st.dialog('Details', width='large')
    def overlay_chart(self, selection, col='ticker', region=st, ticker=None):
            """Open an asset detail dialog for the selected row."""
            def normalize(name, dig=1):
                """Extract and round a scalar value from the selection DataFrame."""
                value = ''
                try:
                    value = selection[name][selection[col].index[0]]
                    value = round(value,dig)
                except Exception:
                    pass

                return value
            mp_window = region.empty()     
            
            if ticker == None:
                ticker = selection['ticker'][selection[col].index[0]]
            mp.render_mainpage(ticker, search_ticker_only=True, region=mp_window, hide_search=True, hide_details=True, username=self.username)

    @st.dialog('Chart', width='large')
    def tiny_chart_overlay(self, selection):
        """Show a tiny_chart overlay for the selected ticker (same pattern as Performance data)."""
        try:
            ticker = selection['ticker'].iloc[0]
            longname = selection['longName'].iloc[0] if 'longName' in selection.columns else ticker
        except Exception:
            st.error("No asset selected.")
            return
        st.markdown(f"[View (Full details) →](/?details=true&symbol={ticker})")
        (interval, period, overlays, oszilators) = self.sys_config.get_selectors()
        with st.spinner(t('assets.spinner')):
            t_chart = tc.tiny_chart(
                symbol=ticker,
                longname=longname,
                interval=interval,
                period=period,
                add_overlays=overlays,
                add_sub_plots=oszilators,
                range_breaks=True,
                username=self.username,
                url="/?details=true&symbol=",
            )
        if t_chart.fig:
            st.plotly_chart(t_chart.fig, use_container_width=True)
        else:
            st.warning(f"No chart data available for {ticker}.")

    def _select_ticker_table(self, df, key_suffix):
        """Render a clickable ticker table; opens tiny_chart_overlay for the selected row."""
        if df.empty:
            st.caption("—")
            return
        df = df.reset_index(drop=True)
        event = st.dataframe(
            df, hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="single-row",
            key=f"mm_{key_suffix}",
        )
        session_key = f"mm_{key_suffix}_last"
        if event.selection.rows:
            sel_row = df.iloc[[event.selection.rows[0]]]
            sel_ticker = sel_row['ticker'].iloc[0]
            if st.session_state.get(session_key) != sel_ticker:
                st.session_state[session_key] = sel_ticker
                self.tiny_chart_overlay(sel_row)
        else:
            st.session_state.pop(session_key, None)

    def generate_treemap(self, combined_df, order_by):
        """
        Generates an interactive treemap using Plotly.

        Args:
            combined_df (pd.DataFrame): Combined DataFrame with required fields for visualization.
        
        Returns:
            plotly.graph_objects.Figure: Treemap figure.
        """
        """
        @st.dialog('info',width='large')
        def on_click(trace, points, state):
            # Index des angeklickten Punktes
            idx = points.point_inds[0]

            # Ticker aus dem angeklickten Punkt extrahieren
            ticker = combined_df['ticker'].iloc[idx]
        
            # Ausgabe oder Aktion
            st.write(f"Kachel geklickt! Ticker: {ticker}")
            # Hier könnten weitere Aktionen erfolgen, z. B. Datenabfragen oder Visualisierungen
        """

        # use central util for excel export

        tickers = combined_df["ticker"].tolist()
        # asset_info-Spalten können als TEXT gespeichert sein (alte, via
        # bulk_upsert_dicts angelegte DBs) — strings tolerant nach float wandeln
        for _num_col in ('marketCap', 'enterpriseValue', 'ebitdaMargins',
                         'totalDebt', 'totalRevenue'):
            combined_df[_num_col] = pd.to_numeric(combined_df[_num_col], errors='coerce')
        combined_df['dTrend']=combined_df['dTrend'].round(2)
        combined_df['wkTrend']=combined_df['wkTrend'].round(2)
        combined_df['moTrend']=combined_df['moTrend'].round(2)
        combined_df['sortino']=combined_df['sortino'].round(2)
        combined_df['sharpe']=combined_df['sharpe'].round(2)
        combined_df['momentum']=combined_df['momentum'].round(1)
        combined_df['marketCap']=combined_df['marketCap'].div(1000000).round(2)
        combined_df['enterpriseValue']=combined_df['enterpriseValue'].div(1000000).round(2)
        combined_df['enterpriseValue']=combined_df['enterpriseValue'].fillna(0)
        
        _inf = float('inf')
        column_names = {
            'sharpe':         [-_inf,-2,-0.5,-0.01,0.01,0.5,2,10,_inf],
            'sortino':        [-_inf,-2,-0.5,-0.01,0.01,0.5,2,10,_inf],
            'day_change':     [-_inf,-2,-0.5,-0.01,0.01,0.5,2,10,_inf],
            'wkTrend':        [-_inf,-2,-0.5,-0.01,0.01,0.5,2,10,_inf],
            'moTrend':        [-_inf,-2,-0.5,-0.01,0.01,0.5,2,10,_inf],
            'marketCap':      [0, 10000, 20000, 50000, 100000, 200000, 500000, 1000000, _inf],
            'enterpriseValue':[-_inf, -10000, 20000, 50000, 100000, 200000, 500000, 1000000, _inf],
            'momentum':       [-_inf,2,5,10,25,65,85,95,_inf],
            'overallTrend':   [-_inf,-10,15,20,30,50,65,85,_inf],
            'overallValueTrend':[-_inf,-10,15,20,30,50,65,85,_inf],
            'ebitdaMargins':  [-_inf,-.5,-0.2,-0.1,0.1,0.2,0.3,0.5,_inf],
        }
        
        # Selection
        self.trend = self.sel_tre.selectbox(
                t('mm.trend_by'),
                options = column_names,
                index = 2
            )

        if self.trend == 'day_change':
            day_changes_df = self.day_change(tickers)
            combined_df = pd.merge(combined_df, day_changes_df, on="ticker", how="left")
            combined_df['day_change'] = combined_df['day_change'].round(2)

            # Show which two trading days are being compared.
            # Use the most common date pair (handles mixed-exchange holidays).
            _day_abbr = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
            def _fmt_date(d):
                """Format a date value as a short 'Weekday DD.MM.YYYY' string."""
                try:
                    dt = pd.to_datetime(d)
                    return f"{_day_abbr[dt.weekday()]} {dt.strftime('%d.%m.%Y')}"
                except Exception:
                    return str(d)
            _valid = combined_df[combined_df['dc_date_cur'].notna()]
            if not _valid.empty:
                _pair = (_valid[['dc_date_cur', 'dc_date_prev']]
                         .apply(tuple, axis=1).value_counts().idxmax())
                st.caption(f"Tagesveränderung: {_fmt_date(_pair[1])} → {_fmt_date(_pair[0])}")

        bs_values = ['marketCap','totalDebt', 'totalRevenue','overallTrend','overallValueTrend']
        box_size = self.sel_size.selectbox(
                t('mm.size_by'),
                options = bs_values,
                index = 0
        )

        winner = combined_df.loc[combined_df[f'{self.trend}'] > 0, f'{box_size}'].sum().round(0)
        looser = combined_df.loc[combined_df[f'{self.trend}'] < 0, f'{box_size}'].sum().round(0)
        winner_count = combined_df.loc[combined_df[f'{self.trend}'] > 0, f'{box_size}'].count()
        looser_count = combined_df.loc[combined_df[f'{self.trend}'] < 0, f'{box_size}'].count()

        st.write(t('mm.market_stats', l=looser_count, w=winner_count, box=box_size, l_pct=round(looser/(winner+looser)*100,0), w_pct=round(winner/(winner+looser)*100,0)))

        color_labels = [ 'darkred', 'red','indianred','gray','lightgreen','lime','green', 'darkgreen']
        color_group = column_names[self.trend]

        # NaN trend values → 0 so pd.cut assigns the gray bucket instead of NaN
        combined_df[f'{self.trend}'] = combined_df[f'{self.trend}'].fillna(0)

        combined_df['colors'] = pd.cut(
            combined_df[f'{self.trend}'],
            bins=color_group,
            labels=color_labels
            )

        # Build a display price column: prefer live latestClose (day_change mode),
        # fall back to simulation close for rows where latestClose is missing/NaN.
        if 'latestClose' in combined_df.columns:
            combined_df['_display_price'] = combined_df['latestClose'].fillna(combined_df['close'])
        else:
            combined_df['_display_price'] = combined_df['close']
        price_data = '_display_price'

        combined_df['details'] = combined_df['longName'].apply(self.add_url)

        treemap_df = combined_df.copy()
        treemap_df['longName'] = treemap_df['longName'].str[:20]
        for col in treemap_df.select_dtypes(include=['category']).columns:
            treemap_df[col] = treemap_df[col].astype('str')
        fig = px.treemap(
            treemap_df,
            path=[px.Constant("all"), 'sector','ticker'],
            values = box_size,
            color='colors',
            height=700,
            color_discrete_map ={'(?)':'#262931', 'darkred':'darkred', 'red':'red', 'indianred':'indianred','gray':'gray', 'lightgreen':'lightgreen','lime':'lime','green':'green','darkgreen':'darkgreen'},
            hover_data = {f'{self.trend}':''}, #:.2p
            custom_data=[f'{self.trend}','sector','longName',f'{price_data}']#,'url']
        )

        fig.update_traces(
            hovertemplate="<br>".join([
            "%{label}",
            "%{customdata[2]}",
            "Price: %{customdata[3]:.2f}",
            "Market Cap(M): %{value}",
            "Sector: %{customdata[1]}",
#            "%{customdata[4]}",
            ])
        )
        fig.data[0].texttemplate = "<b>%{customdata[2]}<br>%{label}</b><br>%{customdata[0]}<br>Price: %{customdata[3]:.2f}"#:.2p


        return fig, combined_df, price_data

    def _render_index_winner_loser(self, df, trend_col, price_col):
        """Gewinner-/Verlierer-Ticker-Listen nach dem aktuell gewählten Trend
        (self.trend, siehe generate_treemap), klickbar via tiny_chart_overlay
        (analog Performance data)."""
        if trend_col not in df.columns:
            return

        show_cols = [c for c in ['ticker', 'longName', price_col, trend_col] if c in df.columns]
        rename = {price_col: 'close', trend_col: t('mm.trend_value')}

        winners = (df[df[trend_col] > 0]
                   .sort_values(trend_col, ascending=False)[show_cols]
                   .rename(columns=rename))
        losers  = (df[df[trend_col] < 0]
                   .sort_values(trend_col, ascending=True)[show_cols]
                   .rename(columns=rename))

        col_w, col_l = st.columns(2)
        with col_w:
            st.markdown(f"**{t('mm.winners')}** ({len(winners)})")
            self._select_ticker_table(winners, key_suffix='idx_win')
        with col_l:
            st.markdown(f"**{t('mm.losers')}** ({len(losers)})")
            self._select_ticker_table(losers, key_suffix='idx_los')

    def _render_underlying_data(self, combined_df):
        """Detailtabelle mit Auswahl/Chart-Overlay + Excel-Export
        (vormals Teil von generate_treemap, jetzt im Index-View-Expander)."""
        df_expander = st.expander(t('mm.underlying_data'), expanded=False)
        with df_expander:
            selection = self.dataframe_with_selections(combined_df[['details','Date','ticker','longName','close','roa','rsi_ema','dayLow','dayHigh','ebitdaMargins','momentum','trendDirection','buySell','targetLowPrice','targetMeanPrice','targetHighPrice','currency','revenueGrowth','sortino','sharpe','overallTrend','overallValueTrend','totalDebt','totalRevenue']]) #, sort_by='ticker')
            try:
                if not selection.empty:
                    self.overlay_chart(selection)
            except Exception:
                pass

            df_xlsx = DataUtils.get_bin_excel_data(combined_df)
            st.download_button(label=t('mm.download_btn'),
                                data=df_xlsx,
                                file_name='market_map_export.xlsx',
                                mime='application/octet-stream'
                                )

    def _render_markov_breadth(self, index_name: str):
        """Kompaktes Breadth-Widget: Bull/Bär/Seitwärts-Verteilung aller Mitglieder
        des aktuell gewählten Zielmarkts auf Tages-/Wochen-/Monatsbasis
        (Markov-Regime, gecached über compute_index_breadth), plus daraus
        abgeleiteter Gesamtzustand des Marktes (Score + Alignment-Hinweis).
        """
        from tradinglib.indicator.markov import Markov
        from tradinglib.regime_data_engine import compute_index_breadth, summarize_breadth

        idx_label = index_name.lstrip('^') or index_name

        with st.spinner(t('mm.breadth_loading', index=idx_label)):
            df = compute_index_breadth(index_name)
        summary = summarize_breadth(df)

        with st.expander(t('mm.breadth_title', index=idx_label), expanded=True):
            if df.empty or not summary:
                st.info(t('mm.breadth_no_data', index=idx_label))
                return

            _names    = {1: t('mm.breadth_bull'), 2: t('mm.breadth_bear'), 0: t('mm.breadth_side')}
            _keys     = {1: 'bull', 2: 'bear', 0: 'side'}
            _order    = [1, 2, 0]
            _colors   = {k: Markov._PALETTE[k]['solid'] for k in _order}
            _tf_label = {'day': t('mm.breadth_tf_day'), 'week': t('mm.breadth_tf_week'),
                         'month': t('mm.breadth_tf_month')}
            # Tages-Historie (Tendenz): älteste zuerst, damit sie im Chart direkt
            # unter der "Tag"-Zeile von unten nach oben Richtung heute läuft.
            for k in (4, 3, 2, 1):
                if (summary.get(f'day_{k}') or {}).get('n'):
                    _tf_label[f'day_{k}'] = t('mm.breadth_tf_day_back', n=k)

            # ── Stacked horizontal bars: eine Zeile je Zeitebene ──────────────────
            rows = [tf for tf in ('month', 'week', 'day_4', 'day_3', 'day_2', 'day_1', 'day')
                    if tf in _tf_label]   # von unten nach oben: Monat → … → Tag
            fig = go.Figure()
            n_rows = 0
            for tf in rows:
                stats = summary.get(tf) or {}
                if not stats.get('n'):
                    continue
                n_rows += 1
                for regime in _order:
                    pct = stats[_keys[regime]]
                    fig.add_trace(go.Bar(
                        y=[_tf_label[tf]], x=[pct], orientation='h',
                        name=_names[regime], marker_color=_colors[regime],
                        legendgroup=str(regime), showlegend=(tf == 'day'),
                        text=f"{pct:.0f}%" if pct >= 8 else '',
                        textposition='inside', insidetextanchor='middle',
                        textfont=dict(color='white', size=11),
                        hovertemplate=f"{_tf_label[tf]} · {_names[regime]}: %{{x:.0f}}%<extra></extra>",
                    ))

            fig.update_layout(
                barmode='stack',
                height=max(170, 80 + 30 * n_rows),
                margin=dict(l=60, r=20, t=10, b=30),
                xaxis=dict(range=[0, 100], ticksuffix='%', showgrid=False),
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
            )
            st.plotly_chart(fig, use_container_width=True)

            # ── Abgeleiteter Gesamtzustand ────────────────────────────────────────
            score, state_code, aligned = summary['score'], summary['state'], summary['aligned']
            _state_label = {
                'bull':    t('mm.breadth_state_bull'),
                'bear':    t('mm.breadth_state_bear'),
                'neutral': t('mm.breadth_state_neutral'),
            }
            arrow = '▲' if score > 0.20 else '▼' if score < -0.20 else '─'
            align_txt = t('mm.breadth_aligned') if aligned else t('mm.breadth_unaligned')
            st.caption(t(
                'mm.breadth_summary',
                arrow=arrow, state=_state_label[state_code],
                score=score, n=len(df), align=align_txt,
            ))

            # ── Gewinner/Verlierer (heute Bull/Bär, klickbar via tiny_chart_overlay) ──
            self._render_breadth_winner_loser(df, _names)

    def _render_breadth_winner_loser(self, df, regime_labels):
        """Ticker-Listen für Mitglieder im heutigen Bull- bzw. Bär-Regime,
        sortiert nach Übereinstimmung über Tag/Woche/Monat."""
        tf_cols = ['day', 'week', 'month']
        work = df.copy()
        work['bull_count'] = (work[tf_cols] == 1).sum(axis=1)
        work['bear_count'] = (work[tf_cols] == 2).sum(axis=1)

        winners_mask = work['day'] == 1
        losers_mask  = work['day'] == 2

        for c in tf_cols:
            work[c] = work[c].map(regime_labels).fillna('-')

        rename = {'day': t('mm.breadth_tf_day'), 'week': t('mm.breadth_tf_week'),
                  'month': t('mm.breadth_tf_month')}
        show_cols = ['ticker', *tf_cols]

        winners = (work[winners_mask].sort_values('bull_count', ascending=False)
                   [show_cols].rename(columns=rename))
        losers  = (work[losers_mask].sort_values('bear_count', ascending=False)
                   [show_cols].rename(columns=rename))

        col_w, col_l = st.columns(2)
        with col_w:
            st.markdown(f"**{t('mm.winners')}** ({len(winners)})")
            self._select_ticker_table(winners, key_suffix='breadth_win')
        with col_l:
            st.markdown(f"**{t('mm.losers')}** ({len(losers)})")
            self._select_ticker_table(losers, key_suffix='breadth_los')

    def render(self, index_filter=1):
        """
        Displays the Treemap in a Streamlit app with user inputs.

        Args:
            index_filter (int): Filter for the index column (e.g., GDAXI = 1).
        """

        self.sel = st.empty()
        (self.sel_size, self.sel_idx, self.sel_exch, self.sel_tre, self.sel_msize,self.sel_mprice,self.sel_ordby, self.sel_limit) = self.sel.columns(8)

        exch_query = f"""
        SELECT exchange FROM asset_info GROUP BY exchange
        """
        exch_df = pd.read_sql_query(exch_query, self.info_conn)
        exch_df = exch_df.dropna()
        exch_names = ['ANY']
        exch_names.extend(list(exch_df.exchange.tolist()))

        # Exch Selection
        self.exch_column = self.sel_exch.selectbox(
                t('mm.exchange'),
                options = exch_names,
                index = 0
            )

        limit_list = ['ANY', 500,200,100,50,20,10]
        limit_to = self.sel_limit.selectbox(
            t('mm.limit'),
            options = limit_list,
            index = 1
        )

        order_list = ['sharpe', 'sortino','ticker', 'marketCap' ]
        order_by = self.sel_ordby.selectbox(
            t('mm.order_by'),
            options = order_list,
            index = 0
        )

        # Query for tickers belonging to the specified index
        ticker_query = f'SELECT s.Ticker FROM stocks s JOIN stock_indices si ON s.id = si.stock_id JOIN indices i ON si.index_id = i.id WHERE i.name = "INDEX"'
        ticker_df = pd.read_sql_query(ticker_query, self.ticker_conn)

        tickers = list(ticker_df['Ticker']) #[3:])

        for w in tickers:
            if '^' not in w: 
                tickers.remove(w)
        for w in tickers:
            if '-' in w or '=' in w: 
                tickers.remove(w)

        # Selection
        tickers.sort()
        column_names = ['ANY']
        column_names.extend(tickers)
        try:
            pos = column_names.index("^GDAXI")
        except Exception:
            pos = 0
            pass

        self.index_column = self.sel_idx.selectbox(
                t('mm.index'),
                options = column_names,
                index = pos
            )

        #Price range
        price_range = {
            'ANY': '',
            '> 1000': 'ai.dayHigh >= 1000',
            '>= 100 < 1000': 'ai.dayHigh >= 100 AND ai.dayHigh < 1000',
            '>= 20 < 100': 'ai.dayHigh >= 20 AND ai.dayHigh < 100',
            '>= 10 < 20': 'ai.dayHigh >= 10 AND ai.dayHigh < 20',
            '< 10': 'ai.dayHigh < 10',            
        }
        p_range = self.sel_mprice.selectbox(
                t('mm.price_range'),
                options = price_range,
                index = 0
            )

        capitalization_size = {
            'ANY'   : '',
            #'XXL'   : 'ai.marketCap > 10000000000000',
            'XXL'    : 'ai.marketCap > 1000000000000',# AND ai.marketCap < 10000000000000',
            'XL'     : 'ai.marketCap > 100000000000 AND ai.marketCap < 1000000000000',
            'L'     : 'ai.marketCap > 10000000000 AND ai.marketCap < 100000000000',
            'M'     : 'ai.marketCap > 1000000000 AND ai.marketCap < 10000000000',
            'S'    : 'ai.marketCap < 1000000000',
        }
        cm_size = self.sel_msize.selectbox(
                t('mm.market_cap'),
                options = capitalization_size,
                index = 0
            )
        
        # Fetch combined data
        combined_df = self.fetch_combined_data_with_attach(index_filter, c_size=capitalization_size[cm_size], m_price=price_range[p_range], o_by=order_by, lim=limit_to)
        combined_df = combined_df.fillna(value={'sector':'Other'})

        if combined_df.empty:
            st.error(t('mm.no_data'))
            return

        # Market Map (Treemap) – ausserhalb der Expander, oben auf der Seite
        fig, treemap_df, price_col = self.generate_treemap(combined_df, order_by)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("---")

        # Index View: Gewinner/Verlierer + Detaildaten der Indexmitglieder
        idx_label = self.index_column.lstrip('^') or self.index_column
        with st.expander(t('mm.index_view_title', index=idx_label), expanded=True):
            self._render_index_winner_loser(treemap_df, self.trend, price_col)
            self._render_underlying_data(treemap_df)

        if self.index_column != 'ANY':
            st.markdown("---")
            self._render_markov_breadth(self.index_column)

# Usage example in a Streamlit script (streamlit run <script.py>):
#visualizer = DataVisualizer("yf_tickers.db", "asset_simulation.db", "asset_info.db", "GDAXI", db_path='C:/Users/Kurt/Development/database')
#visualizer.render(index_filter=1)

