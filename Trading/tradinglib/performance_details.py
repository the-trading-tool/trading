from tradinglib import ( ticker_tools as tt, tools, parity as pr, portfolio as po,
            main_page as mp, make_query as mq, system_config as sysconf,
            graph_tools as gt, tiny_chart as tc )
from tradinglib.tiny_chart_grid import ChartsGridRenderer
from tradinglib.utils import DataUtils
from tradinglib.i18n import t
from tradinglib.tools import open_db

from tradinglib.indicator import ewo
import datetime as dt
import numpy as np
from io import BytesIO
import sqlite3
import pandas as pd
import streamlit as st
import plotly.express as px
import re
import os
import logging

logger = logging.getLogger(__name__)

try:
    import duckdb as _duckdb
    _DUCKDB_OK = True
except ImportError:
    _DUCKDB_OK = False


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


@st.cache_data(ttl=300, show_spinner=False)
def _duckdb_fetch_years(
    sim_db_paths: tuple,   # tuple for st.cache_data hashability
    ticker_db_path: str,
    info_db_path: str,
    index_column: str,
    index_filter,
    qry_ext: str,
    reference_date: str = "",   # ISO date — empty = no cutoff
    latest_only: bool = True,   # True = performance view (1 row/ticker)
                                # False = simulation (all rows = time series)
) -> pd.DataFrame:
    """Load performance data from multiple annual simulation DBs via DuckDB.

    latest_only=True  → performance page: most-recent row per ticker (MAX Date).
    latest_only=False → strategy finder: full time series for all tickers.
    Results are cached for 300 seconds via st.cache_data.
    """
    if not _DUCKDB_OK:
        raise ImportError("duckdb nicht installiert — pip install duckdb")

    con = _duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")

    # --- Jedes Jahres-Sim-DB attachieren ---
    union_parts = []
    for i, path in enumerate(sim_db_paths):
        alias = f"sim_{i}"
        con.execute(f"ATTACH '{path}' AS {alias} (TYPE SQLITE, READ_ONLY TRUE)")
        union_parts.append(f"SELECT *, '{path}' AS _source FROM {alias}.asset_simulation")

    union_sql = " UNION ALL ".join(union_parts)

    # --- yf_tickers + asset_info attachieren ---
    con.execute(f"ATTACH '{ticker_db_path}' AS tkr (TYPE SQLITE, READ_ONLY TRUE)")
    con.execute(f"ATTACH '{info_db_path}' AS inf (TYPE SQLITE, READ_ONLY TRUE)")

    date_filter = f"WHERE Date <= '{reference_date}'" if reference_date else ""

    if qry_ext.strip().upper().startswith("ORDER BY"):
        order_clause = qry_ext
        filter_clause = ""
    else:
        filter_clause = qry_ext
        order_clause = ""

    sim_cols = """
        ap.*,
        yt.ISIN,
        ai.sector, ai.shortName, ai.longName, ai.exchange,
        ai.industry, ai.beta, ai.lastDividendValue,
        ai.recommendationKey, ai.targetHighPrice, ai.targetMeanPrice,
        ai.targetLowPrice, ai.ebitdaMargins, ai.revenueGrowth,
        ai.marketCap, ai.totalDebt, ai.totalRevenue, ai.enterpriseValue
    """

    if latest_only:
        # Performance-Ansicht: 1 Zeile pro Ticker (neuester Stand)
        query = f"""
            WITH sim_all AS ({union_sql}),
            latest AS (
                SELECT ticker, MAX(Date) AS max_date
                FROM sim_all {date_filter}
                GROUP BY ticker
            )
            SELECT {sim_cols}, '{index_column}' AS index_name
            FROM sim_all ap
            INNER JOIN latest
                ON ap.ticker = latest.ticker AND ap.Date = latest.max_date
            LEFT JOIN tkr.stocks yt ON ap.ticker = yt.Ticker
            LEFT JOIN inf.asset_info ai ON ap.ticker = ai.ticker
            {filter_clause} {order_clause}
        """
    else:
        # Simulation: alle Zeilen (Zeitreihe), doppelte (Date,ticker) entfernen.
        # INNER JOIN auf stocks+indices → nur Ticker im gewählten Index (wie q=3).
        date_cutoff = f"AND ap.Date <= '{reference_date}'" if reference_date else ""
        index_where = f"AND idx.name LIKE '{index_column}'" if index_column else ""
        query = f"""
            WITH sim_all AS ({union_sql}),
            deduped AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY ticker, Date ORDER BY _source DESC
                ) AS _rn
                FROM sim_all
            )
            SELECT {sim_cols}, COALESCE(idx.name, '{index_column}') AS index_name
            FROM deduped ap
            INNER JOIN tkr.stocks yt ON ap.ticker = yt.Ticker
            INNER JOIN tkr.stock_indices si ON yt.id = si.stock_id
            INNER JOIN tkr.indices idx ON si.index_id = idx.id
            LEFT JOIN inf.asset_info ai ON ap.ticker = ai.ticker
            WHERE ap._rn = 1 {index_where} {date_cutoff}
            {filter_clause} {order_clause}
        """

    try:
        df = con.execute(query).df()
    finally:
        con.close()
    return df


class Performance(tt.TickerTools):

    def export_to_excel(self, data, button_label='Export', file_name='data.xlsx', region=st):
        """Render a Streamlit download button that exports data as an Excel file."""
        df_xlsx = self.get_bin_excel_data(data) # portfolio.get_transaction_dataframe())
        region.download_button(label=button_label,
            data=df_xlsx,
            file_name= file_name,
            mime='application/octet-stream'
        )
    
    def find_files_with_pattern(self, directory, pattern):
        """Return a list of the captured groups from filenames in directory that match pattern."""
        extracted_parts = []
    
        # Durchsucht das Verzeichnis
        for filename in os.listdir(self.get_path(directory)):
            # Überprüfen, ob die Datei zum Muster passt
            match = re.match(pattern, filename)
            if match:
                # Den dynamischen Teil extrahieren
                dynamic_part = match.group(1)
                if dynamic_part:  # Leere Strings ausschließen
                    extracted_parts.append(dynamic_part)
    
        return extracted_parts

    @st.dialog('Chart', width='large')
    def tiny_chart_overlay(self, selection):
        """Show a tiny_chart overlay for the selected row; keep full-viewer link."""
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
                username=self.username,
                url="/?details=true&symbol=",
            )
        if t_chart.fig:
            st.plotly_chart(t_chart.fig, use_container_width=True)
        else:
            st.warning(f"No chart data available for {ticker}.")

    def select_chart(self, data, limit=100, region=st):
        """Render a selectable data table; opens a tiny-chart overlay when a row is clicked."""
        df = data.copy()
        if limit > 0:
            df = df[:limit]
        df = df.drop(columns=['details'], errors='ignore')

        event = region.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            key="pd_asset_table",
        )
        if event.selection.rows:
            selected_ticker = df.iloc[event.selection.rows[0]]['ticker']
            if st.session_state.get("pd_last_shown") != selected_ticker:
                st.session_state["pd_last_shown"] = selected_ticker
                self.tiny_chart_overlay(df.iloc[[event.selection.rows[0]]])
        else:
            st.session_state.pop("pd_last_shown", None)

    def attach_dbs(self):
        """Resolve DB paths and populate self.ticker_conn via the cached connection helper."""
        t = tools.Tools()
        ticker_path = t.get_path(path=self.db_path, file_name=self.ticker_db)
        perf_path   = t.get_path(path=self.db_path, file_name=self.performance_db)
        info_path   = t.get_path(path=self.db_path, file_name=self.info_db)
        self.ticker_conn = _cached_ticker_conn(ticker_path, perf_path, info_path)

    def __init__(self, ticker_db="yf_tickers.db", performance_db="asset_simulation_.db", info_db="asset_info.db", index_column = '', db_path = 'database', username='', is_admin = False):
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
        self.is_admin = is_admin
        self.sys_config = sysconf.SystemConfig(username=username)
        self.system_currency = self.sys_config.get_value(f'system_currency')

        self.attach_dbs()
        info_path = tools.Tools().get_path(path=self.db_path, file_name=info_db)
        self.info_conn = _cached_info_conn(info_path)
        self.index_column = index_column

    def get_bin_excel_data(self, data=pd.DataFrame(), tbl='results'):
        """Serialize data to an in-memory Excel BytesIO (delegates to DataUtils)."""
        return DataUtils.get_bin_excel_data(data, tbl=tbl)

    def dataframe_with_selections(self, df, preselect=False, preselect_count=0, region=st, column_config={}, filter=[]) -> pd.DataFrame:
        """Render an editable DataFrame with a Select checkbox column; returns only selected rows."""
        session_key = "df_selections"
        st.session_state[session_key] = []
        if not filter == []:
            df_with_selections = df.filter(filter, axis=1)
        else:
            df_with_selections = df.copy()
        
        # Prüfen, ob vorherige Selektionen existieren
        if session_key in st.session_state:
            selected_indices = st.session_state[session_key]
        else:
            selected_indices = set()

        df_with_selections.insert(0, "Select", False)

        if preselect == False:
            #deselct all
            df_with_selections['Select'] = False
        else:
            #select based on count
            df_with_selections['Select'] = True
            if preselect_count == 0:
                # any with buyVolume
                try:
                    for i in range(0, len(df)):
                        if df.loc[i, 'buyVolume'] == 0:
                            df_with_selections.loc[i, 'Select'] = False
                except Exception:
                    pass
            else:
                #select first n
                if len(df) >= preselect_count:
                    for i in range(preselect_count, len(df)):
                        df_with_selections.loc[i, 'Select'] = False

        # Wiederherstellen der Selektion aus Session State
        for i in selected_indices:
            if i < len(df_with_selections):  # Falls sich die Datenlänge ändert
                df_with_selections.loc[i, "Select"] = True

        edited_df = region.data_editor(
                df_with_selections,
                hide_index=True,
                column_config=dict(list(column_config.items()) + list({"Select": st.column_config.CheckboxColumn(required=False)}.items())),
                use_container_width = True,
            )

        # Aktuelle Selektionen speichern
        st.session_state[session_key] = edited_df[edited_df["Select"]].index.tolist()
        
        # Gefilterte Ausgabe ohne die "Select"-Spalte
        return edited_df[edited_df["Select"]].drop(columns=["Select"])    
    
#    @st.cache_data
    def fetch_combined_data_with_attach(_self, index_filter, c_size='', m_price='', o_by='', lim=0, sector='ANY', exchange='ANY', index_column='', curr_column='ANY'):
        """Run the main simulation query with optional sector/exchange/price filters and return a DataFrame."""

        def add_url(search):
            return f'/?symbol="{search}"'

        qry_ext = ''
        if not sector == 'ANY':
            qry_ext = f' {qry_ext} AND ai.sector = "{sector}" '
        if not exchange == 'ANY':
            qry_ext = f' {qry_ext}  AND ai.exchange = "{exchange}" '
        if not curr_column == 'ANY':
            qry_ext = f' {qry_ext}  AND ai.currency = "{curr_column}" '
        if not c_size == '' and not m_price == '':
            qry_ext = f' {qry_ext}  AND {c_size} AND {m_price}'
        elif not c_size == '':
            qry_ext = f' {qry_ext}  AND {c_size}'
        elif not m_price == '':
            qry_ext = f' {qry_ext}  AND {m_price}'

        if not o_by == '':
            qry_ext = f"{qry_ext} ORDER BY {o_by} DESC"

            
        # SQL-Abfrage mit Prefixed-Tabellen
        if not index_column == '':
            _self.index_column = index_column

        query = mq.make_query('asset_simulation', _self.index_column, index_filter, q=1, q_ext=f"{qry_ext}",
                               conn=_self.ticker_conn)
        
        combined_df = pd.read_sql_query(query, _self.ticker_conn)
        combined_df['stockIndex'] = _self.index_column 
        combined_df['details'] = combined_df['longName'].apply(add_url)

        # just for compatibility to older version
        if not 'ewo' in combined_df.columns: 
            combined_df['ewo'] = 0.0
            combined_df['ewo_ema'] = 0.0

#        # just for compatibility to older version

        combined_df = combined_df.drop_duplicates(subset=['Date', 'ticker'], keep='last')
        if 'isin' in combined_df:
            combined_df['isin'] = combined_df['isin'].astype(str)

        return combined_df

    def fetch_data(self, ticker=''):
        """Fetch the latest simulation row for a single ticker."""
        query = f"""
            {mq.make_query('asset_simulation', q=2)}
            WHERE yt.ticker = '{ticker}'
            LIMIT 1
        """
        df = pd.read_sql_query(query, self.ticker_conn)
        return df

    def chart_details(self, df, region=st):
            """Render the main asset detail page for the ticker in df."""
            def normalize(name, dig=1):
                value = self.check_value(df[name].iloc[-1])
                try:
                    value = self.to_int(value,dig)
                except Exception:
                    pass

                return value

            ticker = df['ticker'].iloc[-1]
            mp.render_mainpage(symbol=ticker, search_ticker_only=True, hide_details=False, hide_search=True, username=self.username, is_admin=self.is_admin)
            

    @st.dialog('Asset details', width='large')
    def overlay_chart(self, selection, col='ticker'):
        """Open a full-screen Streamlit dialog showing the asset detail page for the selected row."""
                        
        ticker = selection['ticker'][selection[col].index[0]]
        df = self.fetch_data(ticker=ticker)
        self.chart_details(df, region = st)


    def chart(self, selection, col='ticker'):
        """Render the asset detail page inline (non-dialog version of overlay_chart)."""
        ticker = selection['ticker'][selection[col].index[0]]
        df = self.fetch_data(ticker=ticker)
            
        self.chart_details(df, region = st)

    def render(self, index_filter=1):
        """
        Displays the Treemap in a Streamlit app with user inputs.

        Args:
            index_filter (int): Filter for the index column (e.g., GDAXI = 1).
        """

        # Immer aktuelle DB laden — kein Jahr-Picker auf dieser Seite
        combined_df = self.fetch_combined_data_with_attach("")
        combined_df = combined_df.fillna(value={'sector': 'Other'})

        if combined_df.empty:
            st.error(t('perf.no_data'))
            return

        """
        selection = self.dataframe_with_selections(combined_df[['Date','ticker','longName','sector','close','roa','rsi_ema','dayLow','dayHigh','ebitdaMargins','momentum','trendDirection','buySell','targetLowPrice','targetMeanPrice','targetHighPrice','currency','revenueGrowth','sortino','sharpe','overallTrend','overallValueTrend']], region = self.ss_sidebar) #, sort_by='ticker')
        try:
            if not selection.empty:
                self.chart(selection)
        except Exception:
            pass
            
        """
        combined_df = combined_df.sort_values(['overallValueTrend','sortino','ticker'], ascending=[False,False,True])
        self.select_chart(combined_df, limit=1000, region = st)
        self.export_to_excel(combined_df, button_label=t('perf.download_btn'), file_name='simulation_dataset.xlsx', region=st)

        col_chk, col_sel = st.columns([1, 2])
        show_grid = col_chk.checkbox(t('perf.show_grid'), value=False, key='perf_show_grid')
        grid_count = col_sel.selectbox(t('perf.grid_count'), [5, 10, 20], index=1, key='perf_grid_count') if show_grid else 10
        if show_grid:
            date = combined_df['Date'].iloc[0] if not combined_df.empty else None
            tickers = combined_df[combined_df['Date'] == date].sort_values('sortino', ascending=False)['ticker'].head(grid_count)
            renderer = ChartsGridRenderer(columns=2)
            (interval, period, overlays, oszilators) = renderer.get_selectors(self.sys_config)
            with st.spinner(t('assets.spinner'), show_time=True):
                renderer.render(
                    tickers=tickers,
                    tc=tc,
                    period=period,
                    interval=interval,
                    overlays=overlays,
                    oszilators=oszilators,
                    username=self.username,
                    url='/?details=true&symbol=',
                    chart_config=gt.chart_config,
                    name_df=combined_df,
                    name_lookup_col='ticker',
                    name_field='longName',
                    log_exceptions=True,
                )

