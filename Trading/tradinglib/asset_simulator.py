from tradinglib import ( ticker_tools as tt, tools, parity as pr, portfolio as po,
            main_page as mp, make_query as mq, system_config as sysconf,
            graph_tools as gt )
from tradinglib.i18n import t as _t

import datetime as dt
import numpy as np
import sqlite3
import pandas as pd
import streamlit as st
import plotly.express as px
import re
import os
import subprocess
import sys
import logging

logger = logging.getLogger(__name__)

#os.environ["TradingDB"]=r'C:\\Users\\Kurt\\Documents\\Trading2\\database'

class Parity:
    
    def __init__(self, data, total_invest = 100000, start_date = '2023-01-02', end_date = '2024-12-31', benchmark='^GDAXI', region = st, system_currency = 'EUR'):

        self.df = self.calculate(data=data, total_invest = total_invest, start_date = start_date, end_date = end_date, benchmark=benchmark, region = region, system_currency = system_currency)

        pass

    def calculate(self, data, total_invest, start_date, end_date, benchmark, region, system_currency):

            try:
                tickers = data['ticker'].to_list()
                par = pr.Parity(assets=tickers, benchmark=benchmark)
                df = par.weights_df[-1:].T
                df.rename(columns={df.columns[0]: 'weights'}, inplace=True)            
                df.index.name = 'ticker'            
            
                df = pd.merge(data, df, left_on=['ticker'],
                      right_on=['ticker'], how='left')

#                region.write('Total invest:', round(total_invest,0))
                df['numSharesToBuy'] = total_invest * df['weights'] # / df[f'closeIn{system_currency}']
                df['numSharesToBuy'] = df['numSharesToBuy'].apply(lambda x: round(x, 0))
#                df['estimatedDividend'] = df['numSharesToBuy'] * df['dividendRate']
                df['proposedChangeInAssets'] = df['numSharesToBuy'] - df['buyVolume']
                df['invest'] = round(df['numSharesToBuy'] )#* df[f'closeIn{system_currency}'],0)
#                region.write(df. loc[:, ['ticker', 'longName','buyVolume', 'numSharesToBuy','proposedChangeInAssets','estimatedDividend','invest']])
#                region.write(f"Total dividend: {round(df['estimatedDividend'].sum(),0)} {system_currency}, {round(df['estimatedDividend'].sum()/total_invest*100,2)}%")

                ticker_val = {}
                ticker_names = {}
                for index, data in df.iterrows():
                    if data['invest'] > 0:
                        ticker_val[data['ticker']] = data['invest']
                        ticker_names[data['ticker']] = data['longName']

                ptf = po.portfolio(ticker_val, ticker_names=ticker_names, start_date=start_date, end_date=end_date, benchmark=benchmark)
                
                expander_distribution = region.expander("Distribution:", expanded=False)
                with expander_distribution:
                    try:
                        st.plotly_chart(
                            ptf.fig_portfolio_distribution,
                            use_container_width = True,
                            #sharing="streamlit",
                            config = gt.chart_config,
                        )
                    except Exception:
                        pass
                
                expander_asset_trend = region.expander("Asset trends:", expanded=False)
                with expander_asset_trend:
                    try:
                        st.plotly_chart(
                            ptf.fig_portfolio_trend,
                            use_container_width = True,
                            #sharing="streamlit",
                            config = gt.chart_config
                            )
                    except Exception:
                        pass
                
                expander_sharpe_ratio = region.expander("Portfolio sharpe ratio:", expanded=False)
                with expander_sharpe_ratio:
                    try:
                        st.plotly_chart(
                            ptf.fig_portfolio_sharp_ratio,
                            use_container_width = True,
                            #sharing="streamlit",
                            config = gt.chart_config
                            )
                    except Exception:
                        pass

                expander_portfolio_summary = region.expander("Portfolio summary:", expanded=True)
                with expander_portfolio_summary:
                    try:
                        st.plotly_chart(
                            ptf.fig_portfolio_trend_summary,
                            use_container_width = True,
                            #sharing="streamlit",
                            config = gt.chart_config
                            )
                    except Exception:
                        pass
                          
                logger.debug('Parity.calculate completed successfully')
                return ptf

            except Exception as e:
                logger.exception(f'Parity.calculate failed: {e}')
                region.write('No data')
                pass


class TradeProcessor:
    def __init__(self, input_file = None, data = None, username = ''):
        """
        Initialize the TradeProcessor class with the input file.

        :param input_file: Path to the input Excel file.
        """
        self.input_file = input_file
        self.data = data
        self.processed_data = None
        self.username = username
        self.sys_config = sysconf.SystemConfig(username=username)
        self.system_currency = self.sys_config.get_value(f'system_currency')


    def load_data(self):
        """
        Load the Excel data into a pandas DataFrame.
        """
        self.data = pd.read_excel(self.input_file)
        self.data['timestamp'] = pd.to_datetime(self.data['timestamp'])

    def process_trades(self):
        """
        Process the trades by pairing buy and sell actions for each ticker.
        """
        if self.data is None:
            raise ValueError("Data not loaded. Call load_data() first.")

        if self.data.empty:
            st.warning(_t('sf.no_data_warning'))
            return pd.DataFrame()
        
        self.processed_data = pd.DataFrame()

        # Separate buy and sell data
        buy_data = self.data[self.data['action'] == 'buy']
        sell_data = self.data[self.data['action'] == 'sell']
        
        # Create an empty list to store processed rows
        processed_rows = []

        # Iterate over buy data to find corresponding sells
        for _, buy_row in buy_data.iterrows():
            ticker = buy_row['ticker']
            sell_row = sell_data[(sell_data['ticker'] == ticker) & (sell_data['shares'] == buy_row['shares'])]

            if not sell_row.empty:
                sell_row = sell_row.iloc[0]
                sell_data = sell_data.drop(sell_row.name)
                
                processed_rows.append({
                    'buyDate': buy_row['timestamp'],
                    'ticker': buy_row['ticker'],
                    'longName' : buy_row['longName'],
                    'isin': buy_row['isin'],
                    'currency': buy_row['currency'],
                    'stockIndex': buy_row['stockIndex'],
                    'buyPrice': buy_row['price'],
                    'buyVolume': buy_row['shares'],
                    'buyValue': buy_row['value'],
                    'sellDate': sell_row['timestamp'],
                    'sellPrice': sell_row['price'],
                    'sellVolume': sell_row['shares'],
                    'sellValue': sell_row['value'],
                    'gainPct': round((1-(-buy_row['value'])/sell_row['value'])*100,1),
                    'gain': sell_row['value']+buy_row['value'],
                })
            else:
                
                processed_rows.append({
                    'buyDate': buy_row['timestamp'],
                    'ticker': buy_row['ticker'],
                    'longName' : buy_row['longName'],
                    'isin': buy_row['isin'],
                    'stockIndex': buy_row['stockIndex'],
                    'currency': buy_row['currency'],
                    'buyPrice': buy_row['price'],
                    'buyVolume': buy_row['shares'],
                    'buyValue': buy_row['value'],
                    'sellDate': None,
                    'sellPrice': None,
                    'sellVolume': None,
                    'sellValue': None,
                    'gainPct': None,
                    'gain': None #sell_row['close']+buy_row['value'],
                })

        self.processed_data = pd.DataFrame(processed_rows)

    def detect_format(self):
        """
        Automatically detect whether the data is in the original or processed format.
        """
        if 'buyDate' in self.data.columns and 'sellDate' in self.data.columns:
            return 'processed'
        elif 'action' in self.data.columns:
            return 'original'
        else:
            raise ValueError("Unrecognized data format.")

    def convert_to_original_format(self):
        """
        Convert the processed data back to the original format.
        """
        if self.processed_data is None:
            raise ValueError("Processed data is not available. Call process_trades() first.")

        original_rows = []

        for _, row in self.processed_data.iterrows():
            # Add buy row
            original_rows.append({
                'timestamp': row['buyDate'],
                'action': 'buy',
                'ticker': row['ticker'],
                'price': row['buyPrice'],
                'shares': row['buyVolume'],
                'value': row['buyValue'],
            })

            # Add sell row if exists
            if pd.notna(row['sellDate']):
                original_rows.append({
                    'timestamp': row['sellDate'],
                    'action': 'sell',
                    'ticker': row['ticker'],
                    'price': row['sellPrice'],
                    'shares': row['sellVolume'],
                    'value': row['sellValue'],
                })

        return pd.DataFrame(original_rows)
    
    
class PortfolioSimulator(tools.Tools):


    def __init__(self, data, initial_cash=100000, max_assets=40, username = '', fee_pct=0.0):

        """
        Initialisiert die Portfolio-Simulation.

        :param data: DataFrame mit den Börsendaten (Close, Buy/Sell, Sortino, Vola)
        :param initial_cash: Startkapital in Euro.
        :param max_assets: Maximale Anzahl von Werten im Portfolio.
        :param fee_pct: Handelsgebühr in Prozent (pro Transaktion, gilt für Kauf und Verkauf).
        """
        self.data = data #data.sort_values(order_by, ascending=False)  # Nach Sortino sortieren
        self.initial_cash = initial_cash
        self.max_assets = max_assets
        self.fee_pct = fee_pct
        self.cash = initial_cash
        self.portfolio = {}  # Speichert die gehaltenen Werte: {Aktie: Anzahl}
        self.history = []  # Speichert Transaktionen
        self.bought_assets = set()
        self.username = username
        self.sys_config = sysconf.SystemConfig(username=username)
        self.system_currency = self.sys_config.get_value(f'system_currency')
    
    def simulate(self):
        """
        Führt die Simulation durch.
        """
        for index, row in self.data.iterrows():
            ticker = row.ticker  # Name oder Identifier der Aktie
            name = row.longName
            ISIN = row.ISIN
            currency = row.currency
            stockIndex = row.stockIndex
            date = row.Date
            close_price = row['close']
            signal = row['buySell']
            if not close_price:
                close_price = round(row['dayHigh'] -(row['dayHigh']-row['dayLow'])/2,2)
                close_price = 0

            if signal == 1:  # Buy-Signal
                self.buy_asset(ticker, close_price, date, name=name, ISIN = ISIN, stockIndex=stockIndex, currency=currency)
            elif signal == -1:  # Sell-Signal
                self.sell_asset(ticker, close_price, date, name=name, ISIN = ISIN, stockIndex=stockIndex, currency=currency)

#        final_value = self.calculate_portfolio_value()
#        return final_value
    
    def buy_asset(self, ticker, price, timestamp, name = '', ISIN = '', stockIndex='', currency = 'EUR'):
        """
        Kauft eine Aktie, falls genügend Geld verfügbar ist.
        """
        if len(self.portfolio) < self.max_assets and self.cash > price and ticker not in self.portfolio and ticker not in self.bought_assets:

            asset_volatility = self.data[self.data['ticker'] == ticker].vola.iloc[0]            
            avg_volatility = self.data['vola'].sum() / self.data['vola'].count()
            try:
                num_shares = round(((avg_volatility / (self.max_assets * asset_volatility)) * self.initial_cash) / price,0)
            except Exception:
                num_shares = round(self.cash // (self.max_assets - len(self.portfolio)) // price,0)
                pass
            if num_shares > 0:
                fee = round(num_shares * price * self.fee_pct / 100, 2)
                total_cost = round(num_shares * price + fee, 2)
                self.cash -= total_cost
                self.portfolio[ticker] = {'shares': self.portfolio.get(ticker, {}).get('shares', 0) + num_shares,
                                          'buy_price': price}
                self.bought_assets.add(ticker)  # Markiere das Asset als gekauft

                self.history.append({
                    'timestamp': timestamp,
                    'action': 'buy',
                    'ticker': ticker,
                    'longName': name,
                    'isin': ISIN,
                    'stockIndex': stockIndex,
                    'currency': currency,
                    'price': price,
                    'shares': num_shares,
                    'value': -total_cost
                })
    
    def sell_asset(self, ticker, price, timestamp, name = '', ISIN = '', stockIndex = '', currency = 'EUR'):
        """
        Verkauft eine Aktie, wenn sie im Portfolio ist.
        """
        if ticker in self.portfolio:
            asset_info = self.portfolio.pop(ticker)
            num_shares = asset_info['shares']
            fee = round(num_shares * price * self.fee_pct / 100, 2)
            net_proceeds = round(num_shares * price - fee, 2)
            self.cash += net_proceeds
            self.history.append({
                'timestamp': timestamp,
                'action': 'sell',
                'ticker': ticker,
                'longName' : name,
                'isin' : ISIN,
                'stockIndex' : stockIndex,
                'currency': currency,
                'price': price,
                'shares': num_shares,
                'value': net_proceeds
            })
            self.bought_assets.discard(ticker)  # Entferne das Asset aus der Liste der gekauften Werte
                
    def calculate_portfolio_value(self):
        """
        Berechnet den Gesamtwert des Portfolios (Cash + Wert der gehaltenen Aktien).
        """
        total_value = self.cash
        for ticker, asset_info in self.portfolio.items():
            # Nimm den letzten Preis der Aktie aus self.data für den aktuellen Zeitpunkt
            last_price = self.data.loc[self.data.index == ticker, 'close'].iloc[-1]
            if not last_price:
                last_price = 0
                last_price = round(self.data.loc[self.data.index == ticker, 'dayHigh'].iloc[-1] - (self.data.loc[self.data.index == ticker, 'dayHigh'].iloc[-1]-self.data.loc[self.data.index == ticker, 'dayLow'].iloc[-1])/2,2)
            total_value += asset_info['shares'] * last_price
        return total_value
    
        
    def get_transaction_dataframe(self):
        """
        Gibt ein DataFrame mit allen Käufen und Verkäufen zurück.
        """
        return pd.DataFrame(self.history)


class AssetSimulator(tt.TickerTools):


    def export_to_excel(self, data, button_label = 'Export', file_name = 'data.xlsx', region = st ): 
        df_xlsx = self.get_bin_excel_data(data) # portfolio.get_transaction_dataframe())
        region.download_button(label=button_label,
            data=df_xlsx,
            file_name= file_name,
            mime='application/octet-stream'
        )
    
    def select_chart(self, data, limit = 100, region = st):
        
        df = data.copy()
        if limit > 0:
            df = df[:limit]

        selection = self.dataframe_with_selections(df, region = region, select_row=True)    
        try:
            t = selection['ticker'].iloc[-1]
            if not selection.empty:
#                self.chart(selection)
                self.overlay_chart(selection)
        except Exception:
            pass

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

    def attach_dbs(self):

        self.ticker_conn = sqlite3.connect(tools.Tools().get_path(path = self.db_path, file_name=self.ticker_db))
        # Attach die anderen Datenbanken
        self.ticker_conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = self.db_path, file_name=self.performance_db)}' AS performance_db")
        self.ticker_conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = self.db_path, file_name=self.info_db)}' AS info_db")

    def __init__(self, ticker_db = "yf_tickers.db", performance_db = "asset_simulation_.db", info_db = "asset_info.db", index_column = '', db_path = 'database', username='', is_admin = False):
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
        #self.performance_conn = sqlite3.connect(tools.Tools().get_path(path = self.db_path, file_name=performance_db))
        self.info_conn = sqlite3.connect(tools.Tools().get_path(path = self.db_path, file_name=info_db))
        self.index_column = index_column

    def dataframe_with_selections(self, df, preselect=False, preselect_count = 0, region = st, column_config={}, filter = [], select_row=False, show_df_only=False) -> pd.DataFrame: #, sort_by = 'ticker'):

        session_key = "df_selections"
        # Only initialize the session key once; don't overwrite user selections on every rerun
        if session_key not in st.session_state:
            st.session_state[session_key] = []
        if not filter == []:
            df_with_selections = df.filter(filter, axis=1)
        else:
            df_with_selections = df.copy()
        
        try:
            column_config["details"] = st.column_config.LinkColumn(
                _t('sf.details_col'), display_text=_t('sf.view_text')
            )
            df_with_selections['details'] = df_with_selections['longName'].apply(self.add_url,interval=self.sys_config.get_value('interval','30'),period=self.sys_config.get_value('period','1mo'))
            cols = list(df_with_selections)
            cols.insert(0, cols.pop(cols.index('details')))
            df_with_selections = df_with_selections.loc[:, cols]
        except Exception:
            pass

    # Prüfen, ob vorherige Selektionen existieren
        # Use the stored list (or empty list) consistently
        selected_indices = list(st.session_state.get(session_key, []))

        # record original dtypes so we can convert edited datetime strings back
        original_dtypes = {c: df_with_selections[c].dtype for c in df_with_selections.columns}

        if select_row:
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

            # Prepare a display DataFrame: convert any datetime columns to strings
            # so they are compatible with TextColumn in Streamlit's data_editor.
            display_df = df_with_selections.copy()
            try:
                for col in display_df.columns:
                    if pd.api.types.is_datetime64_any_dtype(display_df[col]):
                        display_df[col] = display_df[col].dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                display_df = df_with_selections.copy()

            edited_df = region.data_editor(
                    display_df,
                    hide_index=True,
                    column_config=dict(list(column_config.items()) + list({"Select": st.column_config.CheckboxColumn(required=False)}.items())),
                    use_container_width = True,
                )

            # Convert edited datetime-like columns back to datetimes using original dtypes
            try:
                for c, dtp in original_dtypes.items():
                    if c in edited_df.columns and pd.api.types.is_datetime64_any_dtype(dtp):
                        edited_df[c] = pd.to_datetime(edited_df[c], errors='coerce')
            except Exception:
                pass

            # Aktuelle Selektionen speichern
            st.session_state[session_key] = edited_df[edited_df["Select"]].index.tolist()
            
            # Gefilterte Ausgabe ohne die "Select"-Spalte
            return edited_df[edited_df["Select"]].drop(columns=["Select"])    

        else:    

            display_df = df_with_selections.copy()
            try:
                for col in display_df.columns:
                    if pd.api.types.is_datetime64_any_dtype(display_df[col]):
                        display_df[col] = display_df[col].dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                display_df = df_with_selections.copy()

            edited_df = region.data_editor(
                    display_df,
                    hide_index=True,
                    column_config=dict(list(column_config.items()) + list({"Select": st.column_config.CheckboxColumn(required=False)}.items())),
                    use_container_width = True,
                )

            try:
                for c, dtp in original_dtypes.items():
                    if c in edited_df.columns and pd.api.types.is_datetime64_any_dtype(dtp):
                        edited_df[c] = pd.to_datetime(edited_df[c], errors='coerce')
            except Exception:
                pass
            
            if show_df_only:
                return pd.DataFrame()
            else:
                return edited_df 
        
    def fetch_combined_data_with_attach(self, index_filter, c_size = '', m_price = '', o_by = '', lim = 0, sector = 'ANY', exchange = 'ANY', index_column = '', curr_column='ANY'):
        
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
            self.index_column = index_column

        query = mq.make_query('asset_simulation', self.index_column, index_filter, q=3, q_ext=f"{qry_ext}", conn=self.ticker_conn)
        
        combined_df = pd.read_sql_query(query, self.ticker_conn)

        combined_df['stockIndex'] = self.index_column 

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
            {mq.make_query('asset_simulation', q=2, conn=ticker_conn)}
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
            mp.render_mainpage(symbol=ticker, search_ticker_only=True, hide_details=True, hide_search=True, username=self.username, is_admin=self.is_admin)
            

    @st.dialog('Asset details',width='large')
    def overlay_chart(self, selection, col = 'ticker'):
                        
        ticker = selection['ticker'][selection[col].index[0]]
        df = self.fetch_data(ticker=ticker)
        self.chart_details(df, region = st)


    def chart(self, selection, col = 'ticker'):

        ticker = selection['ticker'][selection[col].index[0]]
        df = self.fetch_data(ticker=ticker)
            
        self.chart_details(df, region = self.ss_main)

                                
    def render_parity(self, data = pd.DataFrame(), suffix = '', invest = 100000, preselect_count=0, region = st):
        
        info_db_name = "yf_tickers"
        info_db = tt.tools.Db_tools(db_path='database', database_name=f'{info_db_name}.db')
        delete_columns = []
        bm_index = info_db.get_indices_names(delete_columns=delete_columns, table_name='indices')
        idx = 0
        search_term = f'{self.index_column}'
        try:
            if search_term in bm_index:
                idx = bm_index.index(search_term)  
        except Exception:
            pass

        benchmark = self.ss_main.selectbox(
            "Compare to Index",
            bm_index,
            index=idx,
            key=f'asset_sim_benchmark_{self.use_year}_{self.index_column}'
        )

        benchmark = f'{benchmark}'
        
        pre_sel = region.empty()
        ( ps_amnt, ps, _) = pre_sel.columns(3)

        if preselect_count == 0:

            preselect = ps.checkbox(
                "Preselect",
                value = False,
                key=f'asset_sim_preselect_{self.use_year}_{self.index_column}'
            )
            preselect_count = ps_amnt.selectbox(
                "Count:",
                (0,10,20,40,50),
                preselect,
                key=f'asset_sim_preselect_count_{self.use_year}_{self.index_column}'
            )

        else:
            preselect = True
            data.drop_duplicates(subset='ticker', keep="first", inplace=True)
            data = data.head(preselect_count)
        try:
            data.sort_values(['sellDate'], ascending=[False], inplace=True)    
        except Exception:
            pass
        partiy_set_expander = region.expander(f'Parity set:', expanded = False)
        with partiy_set_expander:
            data = self.dataframe_with_selections(data, preselect=preselect, preselect_count=preselect_count, region=st ) 
            self.export_to_excel(data, button_label = '📥 Download Parity set', file_name = f'simulation_{self.use_year}_{suffix}.xlsx',region = st )                

        t_sell = 0
        t_buy = 0
        try:
            t_sell = round((data.sellPrice.fillna(0)*data.sellVolume.fillna(0)).sum())
            t_buy = round((data.buyPrice.fillna(0)*data.buyVolume.fillna(0)).sum())
        except Exception:
            pass

        inv_inp = region.empty()
        (sd, ed, _, _) = inv_inp.columns(4) #ti, sa
        t_invest = t_buy - t_sell
        if t_invest <= 0 or preselect_count > 0 or preselect == False:
            t_invest = invest
                
        start_date = ''
        t_date = dt.date(self.use_year, 1, 1)
        t_date_e = dt.date(self.use_year, 12, 31)
        try:
            start_date =  f"{data[data['buyDate'].str.split().str.len().gt(1)]['buyDate'].astype('datetime64[ns]').min().strftime('%Y-%m-%d')}"
            t_date = dt.date(int(float(start_date[0:4])),int(float(start_date[5:7])),int(float(start_date[8:10])))
        except Exception:
            pass
            
        start_date = sd.date_input("Start date: ", t_date,format="YYYY-MM-DD", key=f'parity_start_{self.use_year}_{self.index_column}').strftime('%Y-%m-%d')            
        end_date = ed.date_input("End date: ", t_date_e,format="YYYY-MM-DD", key=f'parity_end_{self.use_year}_{self.index_column}').strftime('%Y-%m-%d')            
        ptf = Parity(data, total_invest=invest, start_date=start_date, end_date=end_date, benchmark=benchmark, region = region, system_currency = self.system_currency)

    def render(self, index_filter=1):
        """
        Displays the Treemap in a Streamlit app with user inputs.

        Args:
            index_filter (int): Filter for the index column (e.g., GDAXI = 1).
        """

        split_screen = st.empty()
        (self.ss_sidebar, _, self.ss_main) = split_screen.columns([1,0.1,4])

        
        self.ss_sidebar.write(f'Simulation based on selected (buy/sell) criterias:')

        self.sel1 = self.ss_sidebar.empty()
        self.sel2 = self.ss_sidebar.empty()
        self.sel3 = self.ss_sidebar.empty()
        self.sel4 = self.ss_sidebar.empty()
        (self.sel_idx, self.sel_exch) = self.sel1.columns(2)
        (self.sel_msize,self.sel_mprice) = self.sel2.columns(2)
        (self.sel_ordby, self.sel_sector) = self.sel3.columns(2)
        (self.sel_curr, self.sel_year) = self.sel4.columns(2)

        exch_query = f"""
        SELECT exchange FROM asset_info GROUP BY exchange
        """
        exch_df = pd.read_sql_query(exch_query, self.info_conn)
        exch_df.dropna(inplace=True)
        exch_names = ['ANY']
        exch_names.extend(list(exch_df.exchange.tolist()))
        exch_names.sort()
        # Exch Selection
        self.exch_column = self.sel_exch.selectbox(
                _t('sf.exchange'),
                options = exch_names,
                index = 1,
                key='asset_sim_exchange'
            )


        # Query for tickers belonging to the specified sector
        info_query = f"""
        SELECT sector FROM asset_info GROUP BY sector
        """ 
        sector_df = pd.read_sql_query(info_query, self.info_conn)
        sector_df.fillna(value={'sector':'Other'}, inplace=True)

        sector_names =  ['ANY']
        sector_names.extend(list(sector_df.sector.tolist()))
        # Exch Selection
        sector_names = list(filter(None, sector_names))
        sector_names.sort()
        self.sector_col = self.sel_sector.selectbox(
                _t('sf.sector'),
                options = sector_names,
                index = 0,
                key='asset_sim_sector'
            )


        curr_query = f"""
        SELECT currency FROM asset_info GROUP BY currency
        """
        curr_df = pd.read_sql_query(curr_query, self.info_conn)
        curr_df.dropna(inplace=True)
        curr_names = ['ANY']
        curr_names.extend(list(curr_df.currency.tolist()))
        curr_names.sort()
        # Curr Selection
        self.curr_column = self.sel_curr.selectbox(
                _t('sf.currency'),
                options = curr_names,
                index = 0,
                key='asset_sim_currency'
            )

        _EARLIEST_SIM_YEAR = 2020
        _current_year = dt.datetime.now().year
        _available_years = set(
            int(y) for y in self.find_files_with_pattern(self.db_path, r'asset_simulation_(\d+)\.db')
        )
        _available_years.add(_current_year)
        _year_labels = [
            f'{y}' if y in _available_years else f'{y} (no data)'
            for y in range(_current_year, _EARLIEST_SIM_YEAR - 1, -1)
        ]
        _selected_label = self.sel_year.selectbox(
            _t('sf.data_as_of'),
            options=_year_labels,
            index=0,
            key='asset_sim_use_year'
        )
        self.use_year = int(_selected_label.split(' ')[0])
        _year_has_data = self.use_year in _available_years

        # ── Hintergrundprozess-Status ──────────────────────────────────────────
        _proc_key = f'_sim_proc_{self.use_year}'
        _running_proc = st.session_state.get(_proc_key)
        if _running_proc is not None:
            _exit_code = _running_proc.poll()
            if _exit_code is None:
                # Prozess läuft noch
                st.info(_t('sf.calc_running', year=self.use_year, pid=_running_proc.pid))
                if st.button(_t('sf.check_status'), key=f'check_sim_{self.use_year}'):
                    st.rerun()
                return
            else:
                # Prozess beendet — Session-State bereinigen
                del st.session_state[_proc_key]
                if _exit_code == 0:
                    st.success(_t('sf.calc_done', year=self.use_year))
                    st.rerun()
                else:
                    st.error(_t('sf.calc_failed', year=self.use_year, code=_exit_code))
                    return
        # ──────────────────────────────────────────────────────────────────────

        if self.use_year != _current_year:
            if _year_has_data:
                self.performance_db = f'asset_simulation_{self.use_year}.db'
                self.attach_dbs()

        # order selection
        order_list = ['sharpe', 'sortino', 'marketCap','overallTrend','overallValueTrend','roa','beta','revenueGrowth','ebitMargins' ]
        order_by = self.sel_ordby.selectbox(
            _t('sf.order_by'),
            options = order_list,
            index = 1,
            key='asset_sim_order_by'
        )

        # get ticker names
        info_db_name = "yf_tickers"
        info_db = tt.tools.Db_tools(db_path='database', database_name=f'{info_db_name}.db')
        delete_columns = []
        column_names = info_db.get_indices_names(delete_columns=delete_columns, table_name='indices')
#        column_names.extend(['INVESTED'])

        try:
            pos = column_names.index("^GDAXI")
        except Exception:
            pos = 2
            pass
        # Selection
        self.index_column = self.sel_idx.selectbox(
                _t('sf.index'),
                options = column_names,
                index = pos,
                key='asset_sim_index'
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
                _t('sf.price_range'),
                options = price_range,
                index = 0,
                key='asset_sim_price_range'
            )

        #Size        
        capitalization_size = {
            'ANY'   : '',
            #'XXL'   : 'ai.marketCap > 10000000000000',
            'XXL'    : 'ai.marketCap > 1000000000000', #  AND ai.marketCap < 10000000000000',
            'XL'     : 'ai.marketCap > 100000000000 AND ai.marketCap < 1000000000000',
            'L'     : 'ai.marketCap > 10000000000 AND ai.marketCap < 100000000000',
            'M'     : 'ai.marketCap > 1000000000 AND ai.marketCap < 10000000000',
            'S'    : 'ai.marketCap < 1000000000',
        }
        cm_size = self.sel_msize.selectbox(
                _t('sf.market_cap'),
                options = capitalization_size,
                index = 0,
                key='asset_sim_cap_size'
            )
        
        # Fetch combined data
        if _year_has_data:
            combined_df = self.fetch_combined_data_with_attach(index_filter, c_size=capitalization_size[cm_size], m_price=price_range[p_range], o_by=order_by, sector=self.sector_col, exchange=self.exch_column, curr_column=self.curr_column)
            combined_df.fillna(value={'sector':'Other'}, inplace=True)
        else:
            combined_df = pd.DataFrame()

        if combined_df.empty:
            if not _year_has_data:
                st.info(_t('sf.no_data_no_db', year=self.use_year))
            else:
                st.info(_t('sf.no_data_index', year=self.use_year))
            if self.is_admin:
                _script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'asset_perf2.py')
                _cwd = os.path.dirname(_script)
                if st.button(_t('sf.start_calc', year=self.use_year), key=f'gen_sim_{self.use_year}'):
                    _proc = subprocess.Popen([sys.executable, _script, f'/year:{self.use_year}'], cwd=_cwd)
                    st.session_state[f'_sim_proc_{self.use_year}'] = _proc
                    st.rerun()
            return


        # Ensure all data is ordered by date
        combined_df.sort_values(['Date',order_by], inplace=True, ascending=[True, False])

        self.sel5 = self.ss_sidebar.empty()
        (self.sel_invest, self.sel_no_assets) = self.sel5.columns(2)
        self.sel6 = self.ss_sidebar.empty()
        (self.sel_dely, self.sel_fee) = self.sel6.columns(2)

        no_assets = self.to_int(self.sel_no_assets.text_input(
            _t('sf.no_assets'),
            '5',
            key='asset_sim_no_assets'
            ),5)

        invest = self.to_int(self.sel_invest.text_input(
            _t('sf.invest'),
            '100000',
            key='asset_sim_invest'
            ),100000)

        buy_delay = 1
        buy_delay = self.to_int(self.sel_dely.text_input(
            _t('sf.buy_delay'),
            '1',
            key='asset_sim_buy_delay'
            ),buy_delay)

        try:
            fee_pct = round(float(self.sel_fee.text_input(
                _t('sf.fee_pct'),
                '0.3',
                key='asset_sim_fee_pct'
            )), 4)
        except Exception:
            fee_pct = 0.0

        evaluator = tools.ExpressionEvaluator(combined_df, dataframe_name='combined_df')
        operators =  ', '.join(evaluator.valid_operators)

        buy_assets = self.ss_sidebar.text_input(
            _t('sf.buy_when'),
            self.sys_config.get_value("buy_query",default="(ewo_angle>20)"),
            key = 'asset_sim_buy_assets',
            help  = f"Please only use the following operators: {operators}"
        )
#        if not order_by in buy_assets:
#            buy_assets = f"{buy_assets}&({order_by}>0)"
        sell_assets = self.ss_sidebar.text_input(
            _t('sf.sell_when'),
            self.sys_config.get_value("sell_query",default="(ewo_angle<-20"),
            key = 'asset_sim_sell_assets',
            help  = f"Please only use the following operators: {operators}"
        )

        # Gate heavy fetch/compute behind a Calculate button stored in session_state

        self.calc_btn = self.ss_sidebar.empty()
        (self.cal_l, self.cal_r) = self.calc_btn.columns(2)
        calc_key = f'asset_sim_calc_{self.index_column}_{order_by}_{self.use_year}'
        if calc_key not in st.session_state:
            st.session_state[calc_key] = False

        if self.cal_l.button(_t('sf.calc_btn'), key=f'calc_{calc_key}', use_container_width=True):
            st.session_state[calc_key] = True
        if self.cal_r.button(_t('sf.reset_btn'), key=f'reset_{calc_key}', use_container_width=True):
            st.session_state[calc_key] = False

        if not st.session_state[calc_key]:
            st.info(_t('sf.calc_hint'))
            return

        # Populate any missing indicators referenced in buy/sell expressions (lazy, cached)
        try:
            combined_df = tools.Tools().populate_indicators_for_df(combined_df, [buy_assets, sell_assets], default_tf='D', params_map=None, max_workers=6)
        except Exception as e:
            logger.exception('populate_indicators_for_df failed: %s', e)
            try:
                self.ss_sidebar.error(f'Indicator population failed: {e}')
            except Exception:
                pass

        signal_gen = tools.BuySellSignalGenerator(df=combined_df, 
                                            buy_condition=buy_assets, 
                                            sell_condition=sell_assets,
                                            buy_delay_days = buy_delay)
        combined_df = signal_gen.apply_signals()

        combined_df.sort_values(['Date',order_by], inplace=True, ascending=[True, False])
        portfolio = PortfolioSimulator(data = combined_df, initial_cash=invest, max_assets=no_assets, fee_pct=fee_pct)
        portfolio.simulate()

        transact_df = portfolio.get_transaction_dataframe()

        total_invest = 0
        for n in portfolio.bought_assets:
            total_invest += transact_df.loc[transact_df['ticker']==n].value.sum()
        total_invest = round(total_invest,2)

        gain = round(portfolio.cash+(-total_invest),0)
        t_gain = round((gain/invest*100)-100,1)

        processor = TradeProcessor(data=portfolio.get_transaction_dataframe(),username=self.username)
        processor.process_trades()

        combined_df.sort_values(['Date','overallValueTrend','ticker'],ascending=[False,False,True], inplace=True)
        dataset_expander = self.ss_main.expander(_t('sf.full_dataset'), expanded=False)
        with dataset_expander:

                limit = st.selectbox(
                    _t('sf.limit'),
                    options = [100, 1000, 5000, 999999],
                    index = 0
                    )

                self.select_chart(combined_df, limit=limit, region=st)
                self.export_to_excel(combined_df, button_label=_t('sf.download_dataset'), file_name=f'simulation_{self.use_year}_dataset.xlsx', region=st)


        data = processor.processed_data
        try:
            data['details'] = data['longName'].apply(self.add_url,interval=self.sys_config.get_value('interval','30'),period=self.sys_config.get_value('period','1mo'))
            data.sort_values(['buyDate'], ascending=[False], inplace=True)    
        except Exception:
            pass    
        if not data is None:

            if not data.empty:
            
                implied_vola = round(combined_df['vola'].sum() / combined_df['vola'].count())
                avg_value = round(combined_df['overallValueTrend'].sum() / combined_df['overallValueTrend'].count())
                num_trades = len(data)

                # ── Offene Trades zum letzten verfügbaren Kurs bewerten ──────
                unrealized_gain = 0.0
                try:
                    if portfolio.portfolio and not combined_df.empty:
                        _last_by_ticker = (
                            combined_df.sort_values('Date')
                            .drop_duplicates('ticker', keep='last')
                            .set_index('ticker')
                        )
                        for _t, _pos in portfolio.portfolio.items():
                            if _t not in _last_by_ticker.index:
                                continue
                            _lp       = float(_last_by_ticker.loc[_t, 'close'])
                            _ld       = _last_by_ticker.loc[_t, 'Date']
                            _sh       = float(_pos['shares'])
                            _buy_cost = round(_sh * _pos['buy_price'] * (1 + fee_pct / 100), 2)
                            _sell_val = round(_sh * _lp * (1 - fee_pct / 100), 2)
                            _t_gain   = round(_sell_val - _buy_cost, 2)
                            unrealized_gain += _t_gain
                            _mask = data['ticker'] == _t
                            if _mask.any():
                                data.loc[_mask, 'sellPrice'] = round(_lp, 2)
                                data.loc[_mask, 'sellDate']  = pd.to_datetime(_ld)
                                data.loc[_mask, 'sellValue'] = _sell_val
                                data.loc[_mask, 'gain']      = _t_gain
                                data.loc[_mask, 'gainPct']   = round(_t_gain / _buy_cost * 100, 1) if _buy_cost > 0 else 0.0
                except Exception:
                    pass
                unrealized_gain = round(unrealized_gain, 0)
                t_gain_total = round((gain - invest + unrealized_gain) / invest * 100, 1) if invest > 0 else t_gain
                # ─────────────────────────────────────────────────────────────

                self.ss_main.write(_t('sf.dataset_stats', vola=implied_vola, trend=avg_value, min=int(combined_df["overallValueTrend"].min()), max=int(combined_df["overallValueTrend"].max())))
                fee_note = _t('sf.fee_note', pct=fee_pct) if fee_pct > 0 else ''
                unrealized_note = (
                    _t('sf.unrealized_note', unrealized=unrealized_gain, total=t_gain_total)
                    if portfolio.portfolio else ''
                )
                try:
                    _completed = data.dropna(subset=['gain'])
                    _wins   = int((_completed['gain'] > 0).sum())
                    _losses = int((_completed['gain'] < 0).sum())
                    if _losses > 0:
                        _win_rate_str = f'{round(_wins / _losses, 1)}:1'
                    elif _wins > 0:
                        _win_rate_str = '∞:1'
                    else:
                        _win_rate_str = 'n/a'
                except Exception:
                    _win_rate_str = 'n/a'
                self.ss_main.write(_t('sf.sim_stats', cash=round(portfolio.cash,2), invested=-total_invest, value=gain, gain=t_gain, fee=fee_note, unrealized=unrealized_note, winrate=_win_rate_str, trades=num_trades))
                data.sort_values(['sellDate'],ascending=[True], inplace=True)
                data['cumulative_gain'] = data['gain'].cumsum()
                data.sort_values(['sellDate'],ascending=[False], inplace=True)

                portfolio_expander = self.ss_main.expander(f'Portfolio:', expanded = False)
                with portfolio_expander:    
                
                    if portfolio.bought_assets:
                        st.write(f'assets still in portfolio: {portfolio.bought_assets}')

                    self.select_chart(data, limit=-1, region = st)
                    self.export_to_excel(processor.processed_data, button_label = '📥 Download Trades', file_name = f'simulation_{self.use_year}_trades.xlsx', region = st )                

                total_gain_expander = self.ss_main.expander(f'Cumulative gain:', expanded = True)
                with total_gain_expander:    
                    fig = px.line(
                        data,
                        x='sellDate',
                        y='cumulative_gain',
#                       title='Total gain',
                        labels={'buyDate': 'Date', 'cumulative_gain': 'Gain'}
                    )
                    st.plotly_chart(
                                fig,
                                use_container_width = True,
                                #sharing="streamlit",
                                config = gt.chart_config,
                                )


            if not processor.processed_data.empty:

                df2 = combined_df.drop_duplicates(['ticker'], keep='last')
                data = pd.merge(processor.processed_data, df2[['ticker','longName']], on=['ticker','longName'])
                data.sort_values('buyDate', ascending=True, inplace=True)
                suffix = f'{order_by}_{self.index_column}_{invest}_{no_assets}_{t_gain}'
                self.ss_main.write(f"Simulation based on asset parity (buy once and rebalance based on volatility). Compared to trading {no_assets} different assets during same time : {t_gain}% ")
                self.render_parity(data, suffix=suffix, region = self.ss_main, invest = invest, preselect_count = no_assets)
                
