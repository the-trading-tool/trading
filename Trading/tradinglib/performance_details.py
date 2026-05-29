from tradinglib import ( ticker_tools as tt, tools, parity as pr, portfolio as po,
            main_page as mp, make_query as mq, system_config as sysconf,
            graph_tools as gt )
from tradinglib.utils import DataUtils
from tradinglib.i18n import t

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


class Performance(tt.TickerTools):

    def export_to_excel(self, data, button_label = 'Export', file_name = 'data.xlsx', region = st ): 
        df_xlsx = self.get_bin_excel_data(data) # portfolio.get_transaction_dataframe())
        region.download_button(label=button_label,
            data=df_xlsx,
            file_name= file_name,
            mime='application/octet-stream'
        )
    
    def find_files_with_pattern(self, directory, pattern):
        # Liste, um die extrahierten Teile zu speichern
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

    def select_chart(self, data, limit = 100, region = st):
        
        df = data.copy()
        if limit > 0:
            df = df[:limit]
        column_config = {
            "details": st.column_config.LinkColumn(
                "Details", display_text="View"
            )
        }
        cols = list(df)
        cols.insert(0, cols.pop(cols.index('details')))
        df = df.loc[:, cols]

        selection = self.dataframe_with_selections(df, region = region, column_config=column_config)    
        try:
            t = selection['ticker'].iloc[-1]
            if not selection.empty:
#                self.chart(selection)
                self.overlay_chart(selection)
        except Exception:
            pass

    def attach_dbs(self):

        self.ticker_conn = sqlite3.connect(tools.Tools().get_path(path = self.db_path, file_name=self.ticker_db))
        # Attach die anderen Datenbanken
        self.ticker_conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = self.db_path, file_name=self.performance_db)}' AS performance_db")
        self.ticker_conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = self.db_path, file_name=self.info_db)}' AS info_db")

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
        self.info_conn = sqlite3.connect(tools.Tools().get_path(path = self.db_path, file_name=info_db))
        self.index_column = index_column

    def get_bin_excel_data(self, data = pd.DataFrame(), tbl='results'):
        return DataUtils.get_bin_excel_data(data, tbl=tbl)

    def dataframe_with_selections(self, df, preselect=False, preselect_count = 0, region = st, column_config={}, filter = []) -> pd.DataFrame: #, sort_by = 'ticker'):

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
    def fetch_combined_data_with_attach(_self, index_filter, c_size = '', m_price = '', o_by = '', lim = 0, sector = 'ANY', exchange = 'ANY', index_column = '', curr_column='ANY'):

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
        
#        st.write(query)
        combined_df = pd.read_sql_query(query, _self.ticker_conn)
#        st.write(combined_df)
#        combined_df.to_excel("seclection.xlsx","Default")
        combined_df['stockIndex'] = _self.index_column 
        combined_df['details'] = combined_df['longName'].apply(add_url)

        # just for compatibility to older version
        if not 'ewo' in combined_df.columns: 
            combined_df['ewo'] = 0.0
            combined_df['ewo_ema'] = 0.0

#        # just for compatibility to older version
#        if not 'ovtEma9' in combined_df.columns: 
#            combined_df['ovtEma9'] = 0.0
#            combined_df['ovtEma21'] = 0.0

        combined_df = combined_df.drop_duplicates(subset=['Date', 'ticker'], keep='last')
        if 'isin' in combined_df:
            combined_df['isin'] = combined_df['isin'].astype(str)

        return combined_df

    def fetch_data(self, ticker=''):

        ticker_conn = sqlite3.connect(tools.Tools().get_path(path = self.db_path, file_name=self.ticker_db))
        # Attach die anderen Datenbanken
        ticker_conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = self.db_path, file_name='asset_simulation_.db')}' AS performance_db")
        ticker_conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = self.db_path, file_name=self.info_db)}' AS info_db")

        query = f"""
            {mq.make_query('asset_simulation', q=2)}
            WHERE yt.ticker = '{ticker}'
            LIMIT 1
        """

        df = pd.read_sql_query(query, ticker_conn)
        return df

    def chart_details(self, df, region = st):

            def normalize(name, dig=1):
                value = self.check_value(df[name].iloc[-1])
                try:
                    value = self.to_int(value,dig)
                except Exception:
                    pass

                return value

            ticker = df['ticker'].iloc[-1]
            mp.render_mainpage(symbol=ticker, search_ticker_only=True, hide_details=False, hide_search=True, username=self.username, is_admin=self.is_admin)
            

    @st.dialog('Asset details',width='large')
    def overlay_chart(self, selection, col = 'ticker'):
                        
        ticker = selection['ticker'][selection[col].index[0]]
        df = self.fetch_data(ticker=ticker)
        self.chart_details(df, region = st)


    def chart(self, selection, col = 'ticker'):

        ticker = selection['ticker'][selection[col].index[0]]
        df = self.fetch_data(ticker=ticker)
            
        self.chart_details(df, region = st)

    def render(self, index_filter=1):
        """
        Displays the Treemap in a Streamlit app with user inputs.

        Args:
            index_filter (int): Filter for the index column (e.g., GDAXI = 1).
        """

        years = [f'{dt.datetime.now().year}']
        #st.write(self.get_path(self.db_path,''))
        years.extend(list(self.find_files_with_pattern(self.db_path, r'asset_simulation_(\d+)\.db')))
        self.use_year = int(st.selectbox(
            t('perf.data_as_of'),
            options=sorted(years, reverse=True),
            index=0,
        ))

        if not self.use_year == dt.datetime.now().year:
            self.performance_db = f'asset_simulation_{self.use_year}.db'
            self.attach_dbs()

        # Fetch combined data
        combined_df = self.fetch_combined_data_with_attach("")
#        st.data_editor(combined_df)
        combined_df.fillna(value={'sector':'Other'}, inplace=True)

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
        combined_df.sort_values(['overallValueTrend','sortino','ticker'],ascending=[False,False,True], inplace=True)
        self.select_chart(combined_df, limit=1000, region = st)
        self.export_to_excel(combined_df, button_label=t('perf.download_btn'), file_name=f'simulation_{self.use_year}_dataset.xlsx', region=st)                


                
