import plotly.express as px
import pandas as pd
import numpy as np
import sys
import os
import re
import streamlit as st
import math 
import uuid
import time
import datetime as dt
import logging

#os.environ["TradingDB"]=r'C:\\Users\\Kurt\\Documents\\Trading2\\database'

try:
	sys.path.insert(0, "./")
except ImportError:
	print('No Import')

from tradinglib import (asset_simulator as ass,
        tools, ticker_tools as tt,
        tiny_chart as tc,
        multi_select as ms,
        main_page as mp, system_config as sysconf,
        fetch_data as fd, pushover_notifier as pn, graph_tools as gt)
from tradinglib.i18n import t as _t
from tradinglib.indicator import indicator
from tradinglib.utils import DataUtils

logger = logging.getLogger(__name__)

class MultiTransactionProcessor(tt.TickerTools):

    order_by = ''
    total_invest = 0
    overall_vola = 0
    overall_trend = 0  
    total_cash = 0
    still_invested = 0
    total_value = 0
    total_gain = 0
    trades_df = pd.DataFrame()
    url="/?symbol="
    
    def __init__(self, disable_streamlit = False, show_details = True, username = '', db_path = 'database', perf_db = "", region = st, is_admin = False, db_name='asset_simulation', transactions = {} ):

        self.disable_streamlit = disable_streamlit
        self.show_details = show_details
        self.db_path = db_path
        self.perf_db = perf_db
        self.transactions = transactions
        self.region = region
        self.combined_df = pd.DataFrame()
        self.buy_df = pd.DataFrame()
        self.sell_df = pd.DataFrame()
        self.asset_date = ''
        self.db_name = db_name
        self.username = username
        self.is_admin = is_admin
        self.investments = []
        self.sys_config = sysconf.SystemConfig(username=username, is_admin = self.is_admin)
        self.system_currency = self.sys_config.get_value(f'system_currency')

    def calculate_asset_allocation(self, budget, asset_price, asset_volatility, avg_volatility, num_assets):
        weight = avg_volatility / (num_assets * asset_volatility)
        allocation = weight * budget 
        shares = allocation / asset_price
    
        return int(shares) 

    def clean_html(self, html):
        clean = re.compile('<.*?>|&([a-z0-9]+|#[0-9]{1,6}|#x[0-9a-f]{1,6});')
        return re.sub(clean, '', html)

    def send_esc(self, with_timeout=False):

        time.sleep(1)
        if with_timeout:
            st.markdown("""
                <script>
                setTimeout(function() { 
                 window.dispatchEvent(new KeyboardEvent('keydown', {'key': 'Escape'}));
                }, 3000);
            </script>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
                <script>
                function simulateKeyPress(key) {
                 const event = new KeyboardEvent('keydown', { key });
                 textField.dispatchEvent(event);
                }
                simulateKeyPress(27);
            """, unsafe_allow_html=True)

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
                
    @st.dialog('Asset details', width='large')
    def overlay_selector(self, selection, col = 'ticker', region = st, action_type = 'buy'):
        show = False
        ticker = selection['ticker'][selection[col].index[0]]
        if not selection.empty:
            show = True

        try:
            if show:

                show_mp_dialog = True
                db_table = 'trades'
                db_t = tools.Db_tools(db_path='database', database_name=f'{db_table}.db')
                longname = selection['longName'][selection[col].index[0]]
                ISIN = selection['isin'][selection[col].index[0]]
                stockIndex = selection['stockIndex'][selection[col].index[0]]
                currency = selection['currency'][selection[col].index[0]]
                x_rate = self.get_exchange_rate(symbol=currency,system_currency=self.system_currency)
                help_txt = ''
                values = region.empty()
                mp_window = region.empty()
                row = {
                    'uuid': uuid.uuid4().hex,
                    'timestamp': db_t.get_d_timestamp(),
                    'ticker': ticker,
                    'longName': longname,
                    'isin': ISIN,
                    'stockIndex': stockIndex,
                    'action': 'buy',
                }
                if action_type == 'view':
                    (v_s,v_p,v_c) = values.columns(3)

                    buy_volume = selection['buyVolume'][selection[col].index[0]]
                    num_shares = float(v_s.text_input(_t('mt.shares_to_sell'), buy_volume, key=f'sell_shares_{ticker}'))
                    currency = v_c.text_input(_t('mt.currency'), currency, key=f'sell_currency_{ticker}')
                    sell_price = float(v_p.text_input(_t('mt.share_price'), 0, key=f'sell_price_{ticker}'))
                    sell_price_sc = sell_price / x_rate
                    if not currency == self.system_currency:
                        help_txt = f"/ {round(sell_price_sc*num_shares,0)} {self.system_currency}"
                    sell_b = region.button(_t('mt.sell_for', value=round(sell_price*num_shares,0), currency=currency, help=help_txt))
                    if sell_b:
                        row['action'] = 'sell'
                        row['shares'] = num_shares
                        row['currency'] = self.system_currency
                        row['price'] = sell_price
                        row['value'] = sell_price*num_shares
                        region.write(_t('mt.done'))
                        show_mp_dialog = False

                elif action_type == 'trades':
                    (v_s,v_p,v_c) = values.columns(3)

                    if selection['action'][selection[col].index[0]] == 'buy':
                        shares = selection['shares'][selection[col].index[0]]
                        shares = float(v_s.text_input(_t('mt.shares'), shares, key=f'trades_shares_{ticker}'))
                        currency = v_c.text_input(_t('mt.currency'), currency, key=f'trades_currency_{ticker}')
                        price = round(float(selection['price'][selection[col].index[0]]),2)
                        price = round(float(v_p.text_input(_t('mt.price'), price, key=f'trades_price_{ticker}')),2)
                        price_sc = price / x_rate
                        if not currency == self.system_currency:
                            help_txt = f"/ {round(price_sc*shares,0)} {self.system_currency}"
                        sell_b = region.button(_t('mt.sell_for', value=round(price*shares,0), currency=currency, help=help_txt))
                        if sell_b:
                            row['action'] = 'sell'
                            row['shares'] = shares
                            row['currency'] = self.system_currency
                            row['price'] = price_sc
                            row['value'] = price_sc*num_shares
                            region.write(_t('mt.done'))
                            show_mp_dialog = False

                elif action_type == 'buy':
                    (v_s,v_p,v_c) = values.columns(3)

                    shares_to_buy = selection['buyVolume'][selection[col].index[0]]
                    buy_price = round(selection['buyPrice'][selection[col].index[0]],2)
                    num_shares = float(v_s.text_input(_t('mt.shares_to_buy'), shares_to_buy, key=f'buy_shares_{ticker}'))
                    currency = v_c.text_input(_t('mt.currency'), currency, key=f'buy_currency_{ticker}')
                    buy_price = float(v_p.text_input(_t('mt.price'), buy_price, key=f'buy_price_{ticker}'))
                    buy_price_sc = buy_price / x_rate
                    if not currency == self.system_currency:
                        help_txt = f"/ {round(buy_price_sc*num_shares,0)} {self.system_currency}"
                    buy_b = region.button(_t('mt.buy_for', value=round(buy_price*num_shares,0), currency=currency, help=help_txt))
                    if buy_b:
                        row['shares'] = num_shares
                        row['currency'] = self.system_currency
                        row['price'] = buy_price_sc
                        row['value'] = -buy_price_sc*num_shares
                        region.write(_t('mt.done'))
                        show_mp_dialog = False
                else:
                    df = pd.DataFrame()
                    query = f"SELECT * FROM {db_table} WHERE ticker = ? AND buy = 1 ORDER BY Date DESC"
                    shares_to_sell = 0
                    try:
                        df = pd.read_sql_query(query, db_t.conn, params=(ticker,))
                        shares_to_sell = df['shares']
                    except Exception:
                        pass
                    if not shares_to_sell > 0:
                        shares_to_sell = selection['sellVolume'][selection[col].index[0]]
                    sell_price = round(selection['sellPrice'][selection[col].index[0]],2)
                    (v_s, v_p, v_c) = values.columns(3)
                    num_shares = float(v_s.text_input(_t('mt.shares_to_sell'), shares_to_sell, key=f'view_sell_shares_{ticker}'))
                    sell_price = float(v_p.text_input(_t('mt.share_price'), sell_price, key=f'view_sell_price_{ticker}'))
                    currency = v_c.text_input(_t('mt.currency'), currency, key=f'view_sell_currency_{ticker}')
                    sell_price_sc = sell_price / x_rate
                    if not currency == self.system_currency:
                        help_txt = f"/ {round(sell_price_sc*num_shares,0)} {self.system_currency}"
                    sell_b = region.button(_t('mt.sell_for', value=round(sell_price*num_shares,0), currency=currency, help=help_txt))
                    if sell_b:
                        row['action'] = 'sell'
                        row['shares'] = num_shares
                        row['currency'] = self.system_currency
                        row['price'] = sell_price_sc
                        row['value'] = sell_price_sc*num_shares
                        region.write(_t('mt.done'))
                        show_mp_dialog = False
                    
                if show_mp_dialog:
                    mp.render_mainpage(ticker, search_ticker_only=True, region=mp_window, hide_search=True, hide_details=True, username=self.username)
                else:
#                    st.write(row)
                    db_t.ensure_table_and_columns(row_dict=row, database_name=db_table)
                    db_t.insert_data(row_dict=row, database_name=db_table)
                    db_t.conn.commit()
                    mp_window.html(f"<h3>{_t('mt.data_stored')}</h3>")
                    self.send_esc(True)
            else:
                region.write(_t('mt.nothing_to_show'))
        except Exception as e:
            region.error(_t('mt.sorry', error=e))
            pass
            

    def render(self):
        
        def get_selectors(list_data=None):
        
            if list_data == None:
                indicators = indicator.IndicatorLoader('./tradinglib/indicator')
                list_data = ms.MultiCheckboxSelector.lists           

            # Create an instance of the class and display the selectors
            multi_selector = ms.MultiCheckboxSelector(list_data=list_data, sys_conf=self.sys_config)
            multi_selector.render()
            if self.sys_config.get_value("pine_export", False):
                multi_selector.render_pine_export()
            interval = multi_selector.get_selected_options('Interval')[:1]
            period = multi_selector.get_selected_options('Period')[:1]
            overlays = multi_selector.get_selected_options('Overlay')
            oszilators = multi_selector.get_selected_options('Oszilator')

            (interval, period, overlays, oszilators) = self.sys_config.get_selectors(interval, period, overlays, oszilators)
            return(interval, period, overlays, oszilators)

        def show_tickers(tickers):

            columns = 2
            items = len(tickers)
            rows = round(items/columns)+2

    #        (interval, period, overlays, oszilators) = get_selectors()
            interval = self.sys_config.get_value("interval","1d")
            period = self.sys_config.get_value("period","3mo")
            overlays = ['atl','candle','mmm','sup','oft','vwap']
            oszilators = ['ewo','dema','relvol']

            p = 0        
            ic = {}
            ic_c = {}
            for i in range(0,rows):
                ic[i] = st.empty()
                ic_c[i] = ic[i].columns(columns)
            j = 0

            for p in range(0,items):

                    try:
                        symbol = tickers[p]
                        name = ""
                        t_chart = tc.tiny_chart(symbol, name, url=f"{self.url}", period = period, interval=interval, range_breaks=True, candle_chart=True, add_sub_plots=oszilators, show_trend = False,trend_length = 0, add_overlays=overlays, username=self.username, zoom=True)
                    
                        # now add the charts to the right rows / cols
                        i = p%columns
                        if i == 0:
                            j += 1            
                        ic_c[j][i].plotly_chart(
                            t_chart.fig,
                            use_container_width = True,
                            #sharing="streamlit",
                            theme="streamlit",
                            config = gt.chart_config,
                            )
                
                    except Exception as e:
                        st.write(f"Error: {e}")
                        pass

        def get_close_price(symbol, apply_x_rate = False, year = ''):
            if year == '' or year == None:
                (data,ticker) = self.fetch_yahoo_ticker_data(symbol=symbol, period='1d', interval='1m')
            else:
                (_,ticker) = self.fetch_yahoo_ticker_data(symbol=symbol, period='1d', interval='1m')
                dbt = tools.Db_tools(database_name=f"yf_{symbol}.db")
                # year via ? übergeben (SQL-Injection-Schutz)
                query = "SELECT * FROM month_data WHERE Date LIKE ?"
                try:
                    data = pd.read_sql_query(query, dbt.conn, params=(f"{year}-12-%",))
                except Exception as e:
                    print(f"Error: {e}")
                    data = pd.DataFrame()
                    pass
            currency = ticker.get('currency')
            x_rate = 1
            if apply_x_rate:
                x_rate = self.get_exchange_rate(symbol=currency,system_currency=self.system_currency)
            close = 0
            try:
                close = round(data['Close'].iloc[-1] / x_rate,2)
            except Exception:
                pass
            return (close)

        def filter_buy_df(apply_date_filter=True):
            # Ensure buyDate column exists and is a datetime; missing values become NaT
            if 'buyDate' not in self.buy_df.columns:
                self.buy_df['buyDate'] = pd.NaT
            else:
                try:
                    self.buy_df['buyDate'] = pd.to_datetime(self.buy_df['buyDate'])
                except Exception:
                    self.buy_df['buyDate'] = pd.NaT

            if apply_date_filter:
                # parse asset_date into Timestamp for comparison
                try:
                    asset_cut = pd.to_datetime(self.asset_date)
                except Exception:
                    asset_cut = pd.NaT

                if pd.isna(asset_cut):
                    filtered = self.buy_df
                else:
                    filtered = self.buy_df.loc[self.buy_df['buyDate'].notna() & (self.buy_df['buyDate'] >= asset_cut)]
            else:
                filtered = self.buy_df

            self.buy_df = filtered.copy()
            # ensure ticker exists for sorting/display; only include in sort if present
            if 'ticker' not in self.buy_df.columns and 'Ticker' in self.buy_df.columns:
                self.buy_df['ticker'] = self.buy_df['Ticker'].astype(str)
            sort_cols = ['buyDate'] + (['ticker'] if 'ticker' in self.buy_df.columns else [])
            asc = [False] + ([True] if 'ticker' in self.buy_df.columns else [])
            try:
                self.buy_df.sort_values(sort_cols, ascending=asc, inplace=True)
            except Exception:
                # if sorting still fails, ignore and continue
                pass
        
        def filter_sell_df():
            # Ensure sellDate column exists and is a datetime; missing values become NaT
            if 'sellDate' not in self.sell_df.columns:
                self.sell_df['sellDate'] = pd.NaT
            else:
                try:
                    self.sell_df['sellDate'] = pd.to_datetime(self.sell_df['sellDate'])
                except Exception:
                    self.sell_df['sellDate'] = pd.NaT

            try:
                asset_cut = pd.to_datetime(self.asset_date)
            except Exception:
                asset_cut = pd.NaT

            if pd.isna(asset_cut):
                filtered = self.sell_df.loc[self.sell_df.get('sellVolume', 0) > 0]
            else:
                filtered = self.sell_df.loc[self.sell_df['sellDate'].notna() & (self.sell_df['sellDate'] >= asset_cut) & (self.sell_df.get('sellVolume', 0) > 0)]

            self.sell_df = filtered.copy()
            # ensure ticker exists for sorting/display; only include in sort if present
            if 'ticker' not in self.sell_df.columns and 'Ticker' in self.sell_df.columns:
                self.sell_df['ticker'] = self.sell_df['Ticker'].astype(str)
            sort_cols = ['sellDate'] + (['ticker'] if 'ticker' in self.sell_df.columns else [])
            asc = [False] + ([True] if 'ticker' in self.sell_df.columns else [])
            try:
                self.sell_df.sort_values(sort_cols, ascending=asc, inplace=True)
            except Exception:
                pass
            
    # use DataUtils helper instead of creating a separate converter instance
    # converter = fd.CurrencyConverter(self.system_currency)
        if self.transactions == {}:
            try:
                self.transactions = eval(self.sys_config.get_value('multi_transactions',self.sys_config.transactions))
            except Exception as e:
                print(f"issue with json: {e}")
                pass

        if not self.disable_streamlit:

            self.sys_config.render_cfg_row()
                
#            self.transactions = eval(self.transactions)
            
            summary_section = st.empty()
            day_diff = 4 - dt.datetime.today().weekday()
            if day_diff >= 0:
                day_diff = -1
#            self.asset_date = st.text_input('Filter buy/sell calc date from: ',self.asset_date)
            self.asset_date = st.date_input(
                _t('mt.filter_date'),
                (dt.datetime.now() + dt.timedelta(days=day_diff)).strftime("%Y-%m-%d"),
                format="YYYY-MM-DD",
                key='multi_trans_asset_date'
            ).strftime("%Y-%m-%d")
            self.show_details = st.checkbox(_t('mt.show_details'), self.show_details)
#            summary_expander = summary_section.expander("Summary:", expanded=True)

#            self.transactions = eval(st.text_area(
#                'Transaction',
#                self.transactions
#             ))
#            self.sys_config.set_value('multi_transactions', self.sys_config.transactions)
        
        self.use_year = dt.datetime.now().year
        years = [f'{self.use_year}']
        years.extend(list(self.find_files_with_pattern(self.db_path, rf'{self.db_name}_(\d+)\.db')))
        if not self.disable_streamlit:
            self.use_year = int(st.selectbox(
                _t('mt.data_as_of'),
                options=sorted(years, reverse=True),
                index=0,
                key='multi_trans_use_year'
            ))
            
        if self.perf_db == "":
            use_perf_db = f'{self.db_name}_.db'
            if not self.disable_streamlit:
                if not self.use_year == dt.datetime.now().year:
                    use_perf_db = f'{self.db_name}_{self.use_year}.db'
        else:
            use_perf_db = self.perf_db 
        
        self.simulator = ass.AssetSimulator("yf_tickers.db", use_perf_db, "asset_info.db", db_path='database', username=self.username)

#        self.trades_df[f'sellValue{self.system_currency}'] = self.trades_df.apply(lambda row: float(converter.convert(row["sellDate"], row["sellValue"], row["currency"])), axis=1)

        # Gate heavy transaction calculations behind a Calculate button so uploads and edits can be done first
        calc_key = f'multi_trans_calc_{self.use_year}'
        if not self.disable_streamlit:
            if calc_key not in st.session_state:
                st.session_state[calc_key] = False
            # Use a top-level placeholder for the calculate buttons so they are not
            # overwritten later when we write the summary into `summary_section`.
            calc_row = st.empty()
            (calc_l, calc_r) = calc_row.columns([0.2, 0.8])
            if calc_l.button(_t('mt.calc_btn'), key=f'calc_{calc_key}', use_container_width=True):
                st.session_state[calc_key] = True
            if calc_r.button(_t('mt.reset_btn'), key=f'reset_{calc_key}', use_container_width=True):
                st.session_state[calc_key] = False

        total_transactions = 0
        if not st.session_state.get(calc_key, False):
            if not self.disable_streamlit:
                st.info(_t('mt.calc_hint'))
        else:
            for item in self.transactions:
                if not self.disable_streamlit:
                    st.markdown(f"""<h2>{_t('mt.strategy_header', name=item)}</h2>""", unsafe_allow_html=True)
                for id in self.transactions[item]:
                    total_transactions += 1
                    buy_assets = self.transactions[item][id]['buy']
                    sell_assets = self.transactions[item][id]['sell']
                    num_assets = self.transactions[item][id]['num_assets']
                    invest = self.transactions[item][id]['invest']
                    ta_currency = 'ANY'
                    try:
                        ta_currency = self.transactions[item][id]['currency']
                    except Exception:
                        pass
                    self.total_invest += invest
                    self.order_by = self.transactions[item][id]['order_by']
                    self.simulator.index_column = id
                    combined_df = self.simulator.fetch_combined_data_with_attach(index_filter=1,o_by=self.order_by,curr_column=ta_currency)
                    combined_df.sort_values(['Date',self.order_by], inplace=True, ascending=[True, False])
        #            st.dataframe(combined_df[:1000])
                    evaluator = tools.ExpressionEvaluator(combined_df, dataframe_name='combined_df')
                    buy_assets_transformed = evaluator.validate_and_transform(buy_assets)        
                    sell_assets_transformed =evaluator.validate_and_transform(sell_assets)        
        #            st.write(buy_assets_transformed, " ", buy_assets)
        #            st.write(sell_assets_transformed, " ", sell_assets)
                    combined_df['buySell'] = 0
                    try:
                        combined_df['buySell'] = np.where((eval(buy_assets_transformed)), 1,combined_df['buySell'])
                    except Exception:
                        pass
                    try:
                        combined_df['buySell'] = np.where((eval(sell_assets_transformed)), -1,combined_df['buySell'])
                    except Exception:
                        pass
                    
                    # invest is in local currency to calulate the correct weights we need to adjust it
                    x_rate = 1 
                    try:
                        symb = combined_df['currency'].iloc[0]
                        if not symb == 0 and not symb == self.system_currency:
                            x_rate = self.get_exchange_rate(symbol=symb,system_currency=self.system_currency)
                    except Exception:
                        pass
                    
        #            self.delayed_bs = 1
        #            combined_df['buySell'] = combined_df.groupby('ticker')['buySell'].shift(self.delayed_bs).fillna(0)
    
                    combined_df.sort_values(['Date',self.order_by], inplace=True, ascending=[True, False])
                    portfolio = ass.PortfolioSimulator(data = combined_df, initial_cash=round(invest * x_rate,2), max_assets=num_assets)
                    portfolio.simulate()
    
                    transaction_df = portfolio.get_transaction_dataframe()        
    #                transaction_df['Strategy'] = item
        #            if not self.disable_streamlit:
        #                st.write(f'Transaction data {id}')
        #                st.dataframe(transaction_df, use_container_width=True)
        #            self.export_to_excel(combined_df, button_label = f'📥 Download {id} buy sell', file_name = f'{id}_buy_sell_dataset.xlsx', region = st )                
        #            self.export_to_excel(transaction_df, button_label = f'📥 Download {id} transactions', file_name = f'{id}_transactions_dataset.xlsx', region = st )                
    
                    processor = ass.TradeProcessor(data=transaction_df, username=self.username)
                    processor.process_trades()
    
                    data = processor.processed_data
        #            if not data.empty:
                    if isinstance(data, pd.DataFrame):
    
                        data['Strategy'] = item
                        data.sort_values(['sellDate','ticker'],ascending=[True,True], inplace=True)
    
                        data['cumulative_gain'] = data['gain'].cumsum()
                        self.trades_df = pd.concat([self.trades_df, data])        
    
                        total_invest = 0
                        for n in portfolio.bought_assets:
                            total_invest += transaction_df.loc[transaction_df['ticker']==n].value.sum()
                        total_invest = round(total_invest / x_rate,2)
                        p_cash = round(portfolio.cash / x_rate, 2)
                        gain = round(p_cash+(-total_invest),0)
                        t_gain = round((gain/invest*100)-100,1)
                
                        implied_vola = round(combined_df['vola'].sum() / combined_df['vola'].count())
                        avg_value = 0
                        if 'overallValueTrend' in combined_df.columns:
                            avg_value = round(combined_df['overallValueTrend'].sum() / combined_df['overallValueTrend'].count())
    
                        self.overall_vola += implied_vola
                        self.overall_trend += avg_value
                        self.total_cash += round(portfolio.cash / x_rate,2)
                        self.still_invested += total_invest
                        self.total_value += gain
                        self.total_gain += t_gain
                        self.investments = [*self.investments, *portfolio.bought_assets]
                        
                        if not self.disable_streamlit:
                            if self.show_details and not combined_df.empty:
            #                eval(f"expander_{id} = st.expander('Performance {id}')")
            #                eval(f"with expander_{id}:")
                                st.subheader(_t('mt.performance_header', id=id))
                                st.write(_t('mt.dataset_stats', vola=implied_vola, trend=avg_value))
                                st.write(_t('mt.invest_stats', invest=invest, cash=round(portfolio.cash/x_rate,2), invested=-total_invest, value=gain, gain=round((1-(invest/gain))*100,1)))
                                st.write(_t('mt.conditions', n=num_assets, buy=buy_assets, sell=sell_assets))
                                st.write(f'<h5>{_t("mt.trades_for", id=id)}</h5>', unsafe_allow_html=True)
                                try:
                                    selection5 = self.simulator.dataframe_with_selections(pd.DataFrame(portfolio.history).sort_values(['timestamp'], ascending=False),region=st, show_df_only=True)    
                                    if not selection5.empty:
                                        self.overlay_selector(selection5, region = st, action_type='trades')            
                                        self.export_to_excel(selection5, button_label=_t('mt.download_trades'), file_name='trades.xlsx', region=st)
            #                        st.dataframe(pd.DataFrame(portfolio.history).sort_values(['timestamp'], ascending=False))
    
                                    fig = px.line(
                                        data,
                                        x='sellDate',
                                        y='cumulative_gain',
            #                           title='Total gain',
                                        labels={'buyDate': 'Date', 'cumulative_gain': 'Gain'}
                                    )
                
                                    st.plotly_chart(
                                            fig,
                                            use_container_width = True,
                                            #sharing="streamlit",
                                            config = gt.chart_config)
                                except Exception:
                                    pass
    
        ###
        if 'isin' in self.trades_df:
            self.trades_df['isin'] = self.trades_df['isin'].astype(str)
        self.trades_df.reset_index(inplace = True)

        year = dt.datetime.now().year
        self.trades_df.drop(['index'], axis=1, inplace=True)
        sell_date = dt.datetime.now().strftime(f"{self.use_year}-%m-%d 00:00:00")                
        for idx, row in self.trades_df.iterrows():
            if self.trades_df.loc[idx,'sellDate'] == None:
                self.trades_df.loc[idx,'sellDate'] = sell_date
                use_year = ""
                if not self.use_year == year:
                    use_year = self.use_year
                self.trades_df.loc[idx,'sellPrice'] = get_close_price(self.trades_df.loc[idx,'ticker'],year = use_year)

        # For open trades (sellVolume == 0) or missing sellPrice, value them at current market price
        now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for idx, row in self.trades_df.iterrows():
            try:
                sv = self.trades_df.loc[idx].get('sellVolume', 0)
            except Exception:
                sv = 0
            try:
                sp = self.trades_df.loc[idx].get('sellPrice', None)
            except Exception:
                sp = None

            if (sv == 0) or (sp is None) or (pd.isna(sp)) or (sp == 0):
                try:
                    # get latest close (no year filter) and set as current sellPrice
                    current_price = get_close_price(self.trades_df.loc[idx,'ticker'])
                    if current_price is not None and current_price != 0:
                        self.trades_df.loc[idx,'sellPrice'] = current_price
                        # if buyVolume present, set sellValue accordingly
                        try:
                            bv = float(self.trades_df.loc[idx].get('buyVolume', 0))
                        except Exception:
                            bv = 0
                        self.trades_df.loc[idx,'sellValue'] = round(current_price * bv, 2)
                        # mark sellDate as now so currency conversion uses today's rate
                        self.trades_df.loc[idx,'sellDate'] = now_str
                except Exception:
                    pass

#        if 1:
        try:
            for idx, row in self.trades_df.iterrows():
                if pd.isna(self.trades_df.loc[idx,f'sellValue']):
                    self.trades_df.loc[idx,f'sellValue'] = round(self.trades_df.loc[idx,'sellPrice'] * self.trades_df.loc[idx,'buyVolume'],2)
            self.trades_df[f'buyValue{self.system_currency}'] = 0
            self.trades_df[f'sellValue{self.system_currency}'] = 0
            # convert historical values using centralized DataUtils helper
            # Use a safe conversion: if the date-specific rate is missing or suspicious (e.g. fallback==1.0
            # for a non-system currency), try a fallback recent rate. If still not available, mark as NaN
            # so the trade will be excluded from aggregated sums rather than producing extreme values.

            def _get_safe_xrate(date_obj, currency):
                try:
                    rate = DataUtils.get_exchange_rate_for_date(date_obj, currency, self.system_currency)
                except Exception:
                    rate = None

                # If currency differs and the returned rate looks like the "failure" sentinel (1.0),
                # try a non-date-specific recent rate as fallback.
                if currency == self.system_currency:
                    return 1.0

                if rate is None or rate <= 0 or rate == 1.0:
                    logger.debug(f"xrate: date-specific rate missing or suspicious for {currency}->{self.system_currency} on {date_obj}: {rate}")
                    # try non-date-specific recent rate
                    try:
                        fallback = DataUtils.get_exchange_rate(symbol=currency, system_currency=self.system_currency, period='1mo', interval='1d')
                        if fallback and fallback > 0 and not (fallback == 1.0 and currency != self.system_currency):
                            logger.info(f"xrate: using fallback recent rate for {currency}->{self.system_currency}: {fallback}")
                            # cache it
                            try:
                                key = (currency, self.system_currency, date_obj.date(), 'fallback')
                                DataUtils._xrate_cache[key] = float(fallback)
                            except Exception:
                                pass
                            return float(fallback)
                    except Exception as e:
                        logger.debug(f"xrate fallback lookup failed: {e}")

                    # last resort: try a direct remote download of a wider window and inspect values
                    try:
                        from tradinglib import market_data as md
                        from datetime import timedelta
                        d = date_obj.date() if hasattr(date_obj, 'date') else date_obj
                        start_date = d - timedelta(days=10)
                        end_date = d + timedelta(days=2)
                        ticker = f"{currency}{self.system_currency}=X"
                        logger.info(f"xrate: attempting direct remote download for {ticker} {start_date}..{end_date}")
                        df = md.download(tickers=ticker, start=start_date, end=end_date, actions=False, progress=False, auto_adjust=False, prepost=True, force_remote=True)
                        if df is not None and not df.empty:
                            try:
                                val = float(df.loc[df.index.date == d, 'Close'].iloc[-1])
                            except Exception:
                                val = float(df['Close'].iloc[-1])
                            logger.info(f"xrate: got remote rate for {ticker} on {d}: {val}")
                            # persist in in-memory cache
                            try:
                                keyc = (currency, self.system_currency, d.isoformat())
                                DataUtils._xrate_cache[keyc] = val
                            except Exception:
                                pass
                            return val
                    except Exception as e:
                        logger.warning(f"xrate: remote retry failed for {currency}->{self.system_currency} on {date_obj}: {e}")

                    # nothing worked
                    return None

                return float(rate)

            def _safe_convert(date_val, currency, value):
                try:
                    if value is None or (isinstance(value, float) and math.isnan(value)):
                        return float('nan')
                    d = pd.to_datetime(date_val)
                    rate = _get_safe_xrate(d, currency)
                    if rate is None:
                        return float('nan')
                    return float(rate) * float(value)
                except Exception:
                    return float('nan')

            self.trades_df[f'sellValue{self.system_currency}'] = self.trades_df.apply(
                lambda row: _safe_convert(pd.to_datetime(row["sellDate"]), row["currency"], row["sellValue"]),
                axis=1,
            )
            self.trades_df[f'buyValue{self.system_currency}'] = self.trades_df.apply(
                lambda row: _safe_convert(pd.to_datetime(row["buyDate"]), row["currency"], row["buyValue"]),
                axis=1,
            )
            self.trades_df[f'buyPrice{self.system_currency}'] = -round(self.trades_df[f'buyValue{self.system_currency}'] / self.trades_df[f'buyVolume'],2)
            self.trades_df[f'sellPrice{self.system_currency}'] = round(self.trades_df[f'sellValue{self.system_currency}'] / self.trades_df[f'buyVolume'],2)

        except Exception as e:
#            st.error(f'Missing valid configuration! Check queries for typos!')
#            self.sys_config.render()
            pass
        # Compute gain and percentage in a safe vectorized way. If conversion was not possible
        # (NaN in converted buy/sell values), propagate NaN and exclude from summaries.
        bv_col = f'buyValue{self.system_currency}'
        sv_col = f'sellValue{self.system_currency}'

        # avoid division by zero or invalid operations
        valid_mask = self.trades_df[bv_col].notna() & self.trades_df[sv_col].notna() & (self.trades_df[sv_col] != 0)

        # gainPct: using buyPrice/sellPrice where both available
        self.trades_df['gainPct'] = np.nan
        try:
            self.trades_df.loc[valid_mask, 'gainPct'] = (
                (1 - ( -self.trades_df.loc[valid_mask, bv_col] / self.trades_df.loc[valid_mask, sv_col])) * 100
            ).round(1)
        except Exception:
            # fallback to elementwise safe calculation
            for idx, row in self.trades_df.iterrows():
                try:
                    if pd.notna(row[bv_col]) and pd.notna(row[sv_col]) and row[sv_col] != 0:
                        self.trades_df.at[idx, 'gainPct'] = round((1 - (-row[bv_col] / row[sv_col])) * 100, 1)
                except Exception:
                    self.trades_df.at[idx, 'gainPct'] = float('nan')

        # gain: total value (sell + buy). Keep NaN where either side conversion missing.
        self.trades_df['gain'] = np.nan
        try:
            self.trades_df.loc[valid_mask, 'gain'] = (
                (self.trades_df.loc[valid_mask, sv_col] + self.trades_df.loc[valid_mask, bv_col]).round(0)
            )
        except Exception:
            for idx, row in self.trades_df.iterrows():
                try:
                    if pd.notna(row[bv_col]) and pd.notna(row[sv_col]):
                        self.trades_df.at[idx, 'gain'] = round(row[sv_col] + row[bv_col], 0)
                except Exception:
                    self.trades_df.at[idx, 'gain'] = float('nan')

        # For downstream presentation, replace NaN gains with 0 for cumulative sum but report how many were affected
        num_missing = int(self.trades_df['gain'].isna().sum())
        if num_missing > 0 and not self.disable_streamlit:
            st.warning(_t('mt.missing_xrate', n=num_missing, currency=self.system_currency))
            # show details of affected trades
            cols_to_show = [c for c in ['ticker','buyDate','sellDate','currency','buyValue','sellValue'] if c in self.trades_df.columns]
            missing_df = self.trades_df[self.trades_df['gain'].isna()][cols_to_show]
            try:
                st.dataframe(missing_df)
            except Exception:
                pass
            # also log details for offline inspection
            try:
                for idx, r in missing_df.iterrows():
                    logger.warning(f"Missing xrate for trade: ticker={r['ticker']}, buyDate={r['buyDate']}, sellDate={r['sellDate']}, currency={r['currency']}, buyValue={r['buyValue']}, sellValue={r['sellValue']}")
            except Exception:
                pass

        self.trades_df['gain'] = self.trades_df['gain'].fillna(0)

        # Defensive checks: ensure expected date columns exist and are datetime; ensure ticker exists
        if 'sellDate' not in self.trades_df.columns:
            self.trades_df['sellDate'] = pd.NaT
        else:
            try:
                self.trades_df['sellDate'] = pd.to_datetime(self.trades_df['sellDate'])
            except Exception:
                self.trades_df['sellDate'] = pd.NaT

        if 'ticker' not in self.trades_df.columns and 'Ticker' in self.trades_df.columns:
            self.trades_df['ticker'] = self.trades_df['Ticker']

        sort_cols = [c for c in ['sellDate', 'ticker'] if c in self.trades_df.columns]
        if sort_cols:
            try:
                self.trades_df.sort_values(sort_cols, ascending=[True]*len(sort_cols), inplace=True)
            except Exception:
                pass

        self.trades_df['cumulative_gain'] = self.trades_df['gain'].cumsum()
        gain = 0
        for i, row in self.trades_df.iterrows():
            if not math.isnan(row['cumulative_gain']):
                gain = row['cumulative_gain']
        self.total_value = gain + self.total_invest

        # Save the data to database
        db = tools.Db_tools(db_path=self.db_path, database_name=f"trades{year}.db")
        db.cursor.execute('''DROP TABLE IF EXISTS trades''')
        self.trades_df.to_sql('trades', db.conn, if_exists='replace', index=False) # - writes the pd.df to SQLIte DB
#        new_df = pd.read_sql('select * from trades', db.conn)
#        st.write(new_df)
        db.conn.commit()
        db.conn.close()

        # Ensure a longName column exists (some imports use different headers)
        if 'longName' not in self.trades_df.columns:
            alt = next((c for c in ['long_name', 'longname', 'name', 'company'] if c in self.trades_df.columns), None)
            if alt:
                self.trades_df['longName'] = self.trades_df[alt]
            else:
                # Fallback to ticker or empty string
                if 'ticker' in self.trades_df.columns:
                    self.trades_df['longName'] = self.trades_df['ticker'].astype(str)
                else:
                    self.trades_df['longName'] = ''

        try:
            self.trades_df['details'] = self.trades_df['longName'].apply(self.simulator.add_url)
        except Exception:
            # Fallback: build details from ticker only
            try:
                if 'ticker' in self.trades_df.columns:
                    self.trades_df['details'] = self.trades_df['ticker'].apply(lambda t: self.simulator.add_url(t) if t else '')
                else:
                    self.trades_df['details'] = ''
            except Exception:
                self.trades_df['details'] = ''
        try:
            cols = list(self.trades_df)
            cols.insert(0, cols.pop(cols.index('Strategy')))
            cols.insert(0, cols.pop(cols.index('details')))
            self.trades_df = self.trades_df.loc[:, cols]
        except Exception:
            pass

        if not self.disable_streamlit:
            try:
                st.session_state['multi_trades_df'] = self.trades_df.copy()
            except Exception:
                pass
            # Also persist to trading.db so the Compare-Tab survives page navigation
            try:
                from tradinglib.trading_bridge import OrderLog
                OrderLog(db_path=self.db_path).save_backtest(
                    self.trades_df, username=self.username
                )
            except Exception as _e:
                logger.debug("multi_transaction: save_backtest failed: %s", _e)

        if not self.disable_streamlit:
#            ct_expander = st.expander("Closed Trades:")
#            with ct_expander:
#               self.trades_df.sort_values(['buyDate'], ascending=[True],inplace=True)
            column_config = {
                            "Select": st.column_config.CheckboxColumn(required=False),
                            'sellDate':  st.column_config.TextColumn(_t('mt.col_sell_date')),
                            'buyDate':   st.column_config.TextColumn(_t('mt.col_buy_date')),
                            "ticker":    st.column_config.TextColumn(_t('mt.col_ticker')),
                            "isin":      st.column_config.TextColumn(_t('mt.col_isin')),
                            "longName":  st.column_config.TextColumn(_t('mt.col_company')),
                            'costBasis': st.column_config.NumberColumn(_t('mt.col_costs'),      format="%.2f"),
                            "cumulative_gain": st.column_config.NumberColumn(_t('mt.col_cum_gain'), format="%.2f"),
                            "buyPrice":  st.column_config.NumberColumn(_t('mt.col_buy_price'),  format="%.2f"),
                            "buyValue":  st.column_config.NumberColumn(_t('mt.col_buy_value'),  format="%.2f"),
                            "sellPrice": st.column_config.NumberColumn(_t('mt.col_sell_price'), format="%.2f"),
                            "sellValue": st.column_config.NumberColumn(_t('mt.col_sell_value'), format="%.2f"),
                            'gain':      st.column_config.NumberColumn(_t('mt.col_gain'),       format="%.2f"),
                            'gainPct':   st.column_config.NumberColumn(_t('mt.col_gain_pct'),   format="%.2f"),
                            'buyVolume': st.column_config.NumberColumn(_t('mt.col_buy_volume'), format="%.0f"),
                            'sellVolume':st.column_config.NumberColumn(_t('mt.col_sell_volume'),format="%.0f"),
                            'stockIndex':st.column_config.TextColumn(_t('mt.col_index')),
                            'currency':  st.column_config.TextColumn(_t('mt.col_currency')),
                            f'buyValue{self.system_currency}':  st.column_config.NumberColumn(f"{_t('mt.col_buy_value')} {self.system_currency}",  format="%.2f"),
                            f'sellValue{self.system_currency}': st.column_config.NumberColumn(f"{_t('mt.col_sell_value')} {self.system_currency}", format="%.2f"),
                            f'buyPrice{self.system_currency}':  st.column_config.NumberColumn(f"{_t('mt.col_buy_price')} {self.system_currency}",  format="%.2f"),
                            f'sellPrice{self.system_currency}': st.column_config.NumberColumn(f"{_t('mt.col_sell_price')} {self.system_currency}", format="%.2f"),
                            'sellAt':    st.column_config.NumberColumn("Sell At",    format="%.2f"),
                            'sellTarget':st.column_config.NumberColumn("Sell Target",format="%.2f"),
                            "details":   st.column_config.LinkColumn(_t('mt.col_details'), display_text=_t('mt.col_view')),
                            }
#            if 1:
            day_diff = -1
            sell_date = (dt.datetime.now() + dt.timedelta(days=day_diff)).strftime("%Y-%m-%d 00:00:00")
            if self.show_details:
                st.html(f'<h3>{_t("mt.simulation_header")}</h3>')
                if not self.trades_df.empty:
#                    st.write(self.trades_df)
                    for idx, _ in self.trades_df.iterrows():
                    
                        # invest is in local currency to calulate the correct weights we need to adjust it
                        x_rate = 1 
                        try:
                            x_rate = self.get_exchange_rate(symbol=self.trades_df.loc[idx,'currency'],system_currency=self.system_currency)
                        except Exception: 
                            pass

#                        self.trades_df.loc[idx,f'sellPrice{self.system_currency}'] = self.trades_df.loc[idx,f'sellPrice']/ x_rate
#                        if self.trades_df.loc[idx,f'sellPrice'] == 0:
#                            self.trades_df.loc[idx,f'sellPrice'] = get_close_price(self.trades_df.loc[idx,'ticker'],year = self.use_year)
#                        self.trades_df.loc[idx,f'sellPrice{self.system_currency}'] = round(self.trades_df.loc[idx,f'sellPrice'] / x_rate,2)

#                        self.trades_df.loc[idx,f'sellValue{self.system_currency}'] = round(self.trades_df.loc[idx,f'sellPrice{self.system_currency}'] *self.trades_df.loc[idx,f'buyVolume'] ,2)
#                        st.error(f"Missing price {self.trades_df.loc[idx,f'sellPrice{self.system_currency}']}")

                    self.trades_df['costBasis'] = abs(round(self.trades_df[f'sellValue{self.system_currency}'] * float(self.sys_config.get_value('trading_cost_pct',0.6))/100,2))
                    self.trades_df['gainPct'] = round((1-(self.trades_df['buyPrice'])/self.trades_df['sellPrice'])*100,1)
                    self.trades_df['gain'] = round((self.trades_df[f'sellValue{self.system_currency}'] + self.trades_df[f'buyValue{self.system_currency}']) - self.trades_df['costBasis'],2)
                    self.trades_df.loc[self.trades_df['gain'] > 10000, 'gain'] = 0
                    self.trades_df.loc[self.trades_df['gain'] < -10000, 'gain'] = 0

                    # Defensive: ensure a 'ticker' column exists before sorting. Some imports use
                    # alternate header names (Ticker, symbol, Asset, etc.). Map common
                    # alternatives to 'ticker' or create a safe empty string column so
                    # downstream sorts don't raise KeyError.
                    if 'ticker' not in self.trades_df.columns:
                        for alt in ['Ticker','symbol','Symbol','asset','Asset','Assets','asset_name','symbol ']:
                            if alt in self.trades_df.columns:
                                try:
                                    self.trades_df['ticker'] = self.trades_df[alt].astype(str)
                                except Exception:
                                    self.trades_df['ticker'] = self.trades_df[alt].apply(lambda v: str(v) if not pd.isna(v) else '')
                                break
                        else:
                            # try a fuzzy match for any column containing 'tick'
                            found = False
                            for c in self.trades_df.columns:
                                if 'tick' in c.lower():
                                    try:
                                        self.trades_df['ticker'] = self.trades_df[c].astype(str)
                                    except Exception:
                                        self.trades_df['ticker'] = self.trades_df[c].apply(lambda v: str(v) if not pd.isna(v) else '')
                                    found = True
                                    break
                            if not found:
                                # Last resort: create an empty string column so sort can proceed
                                self.trades_df['ticker'] = ''

                    # Only process and plot trades_df when it has data. If empty, avoid
                    # sorting by columns that might not exist and inform the user how to
                    # produce data (press Calculate or import own trades).
                    if not self.trades_df.empty:
                        self.trades_df.sort_values(['sellDate','ticker'],ascending=[True,True], inplace=True)
                        self.trades_df['cumulative_gain'] = self.trades_df['gain'].cumsum()

                        self.trades_df.sort_values(['sellDate','ticker'], ascending=[False,False], inplace=True)
                        selection1 = self.simulator.dataframe_with_selections(self.trades_df,region=st, column_config=column_config, show_df_only=True)

                        if not selection1.empty:
                            self.overlay_selector(selection1, region = st, action_type='sell')
                        self.export_to_excel(self.trades_df, button_label=_t('mt.download_trades'), file_name='trades.xlsx', region=st)

                        # Potential gain plot
                        self.trades_df.sort_values(['sellDate','ticker'],ascending=[True,True], inplace=True)
                        st.html(f'<h3>{_t("mt.potential_gain_header")}</h3>')
                        fig1 = px.line(
                                    self.trades_df,
                                    x='sellDate',
                                    y='cumulative_gain',
                                    labels={'buyDate': 'Date', 'cumulative_gain': 'Gain'}
                                )
                        if not self.disable_streamlit:
                            st.plotly_chart(
                                fig1,
                                use_container_width = True,
                                config = gt.chart_config)
#                    else:
#                        if not self.disable_streamlit:
#                            st.info('No simulated transactions were produced. Press "Calculate transactions" to run simulations, or import your own trades in the Import / Export panel and then analyze them.')
#            nt_expander = st.expander("New Trades:")
#            with nt_expander:

#                tickers = self.trades_df['ticker'].tolist()
#                tickers = list(set(tickers))
#                show_tickers(tickers)

        buy_gain = float(self.trades_df.loc[self.trades_df['gain'] >0].gain.sum()) if not self.trades_df.empty else 0.0
        sell_gain = float(self.trades_df.loc[self.trades_df['gain'] <0].gain.sum()) if not self.trades_df.empty else 0.0
        try:
            ratio = round( -buy_gain / sell_gain,1)
        except Exception:
            ratio = 0.0

        # Defensive numeric coercion: some attributes may become tuples or sequences
        # during iterative processing; coerce to scalars for display.
        def _num(v):
            try:
                if v is None:
                    return 0.0
                if isinstance(v, (int, float, np.number)):
                    return float(v)
                # handle numpy scalars
                try:
                    import numpy as _np
                    if isinstance(v, _np.generic):
                        return float(v)
                except Exception:
                    pass
                # iterable types (list/tuple/series/ndarray)
                if isinstance(v, (list, tuple, set)):
                    s = 0.0
                    for e in v:
                        try:
                            s += float(e)
                        except Exception:
                            pass
                    return s
                # pandas objects
                try:
                    import pandas as _pd
                    if isinstance(v, (_pd.Series, _pd.DataFrame)):
                        # sum values if possible
                        try:
                            return float(v.sum().sum()) if isinstance(v, _pd.DataFrame) else float(v.sum())
                        except Exception:
                            return 0.0
                except Exception:
                    pass
                return float(v)
            except Exception:
                return 0.0

        s_total_invest = _num(self.total_invest)
        s_total_cash = _num(self.total_cash)
        s_still_invested = _num(self.still_invested)
        s_overall_trend = _num(self.overall_trend)
        s_overall_vola = _num(self.overall_vola)

        total_value_calc = -s_still_invested + s_total_cash
        total_gain_pct = 0.0
        if s_total_invest and s_total_invest != 0:
            total_gain_pct = round(((total_value_calc / s_total_invest) - 1) * 100, 1)

        summary = f"""
            <h3>{_t('mt.summary_header')}</h3><br>
            <table>
            <tr>
            <td>{_t('mt.summary_currency')} :</td><td>{self.system_currency}</td>
            </tr><tr>
            <td>{_t('mt.summary_invest')} :</td><td>{round(s_total_invest,0)}</td>
            </tr><tr>
            <td>{_t('mt.summary_cash')} :</td><td>{round(s_total_cash,0)}</td>
            </tr><tr>
            <td>{_t('mt.summary_still_invested')} :</td><td>{round(-s_still_invested,0)} into:</br>{self.investments}</td>
            </tr><tr>
            <td>{_t('mt.summary_total_value')} :</td><td>{round(total_value_calc,0)}</td>
            </tr><tr>
            <td>{_t('mt.summary_total_gain')} :</td><td>{total_gain_pct}%</td>
            </tr><tr>
            <td>{_t('mt.summary_trend')} :</td><td>{round(s_overall_trend/total_transactions,0) if total_transactions and total_transactions != 0 else 0}</td>
            </tr><tr>
            <td>{_t('mt.summary_vola')} :</td><td>{round(s_overall_vola/total_transactions,0) if total_transactions and total_transactions != 0 else 0}</td>
            </tr><tr>
            <td>{_t('mt.summary_ratio')} :</td><td>{ratio}:1</td>
            </tr>
            </table>
            <br><br>
        """
        
        if not self.disable_streamlit:

#           with summary_expander:
            summary_section.write(summary, unsafe_allow_html=True)
                
        # Ensure expected numeric columns exist so downstream selections and drops
        # don't raise KeyError when users upload minimal data or run partial simulations.
        expected_numeric_cols = [
            'sellVolume', 'buyVolume', 'gain', 'gainPct',
            f'sellValue{self.system_currency}', f'buyValue{self.system_currency}',
            f'buyPrice{self.system_currency}', f'sellPrice{self.system_currency}'
        ]
        for col in expected_numeric_cols:
            if col not in self.trades_df.columns:
                self.trades_df[col] = 0

        # Safe fill and splits
        self.trades_df.fillna(0,inplace=True)
        # select buys (no sell volume) and sells (sellVolume > 0)
        self.buy_df = self.trades_df.loc[self.trades_df.get('sellVolume', 0) == 0].copy()
        # drop non-buy columns if present; ignore missing columns
        self.buy_df.drop(['gainPct',f'sellValue{self.system_currency}',f'sellPrice{self.system_currency}','sellDate','sellPrice','sellVolume','sellValue','gain','cumulative_gain'], axis = 1, inplace=True, errors='ignore')
        self.sell_df = self.trades_df.loc[self.trades_df.get('sellVolume', 0) > 0].copy()
        self.sell_df.drop(['buyDate','buyPrice','buyVolume','buyValue'], axis = 1, inplace=True, errors='ignore')

        try:
            self.buy_df['stopLoss'] = round(self.buy_df[f'buyPrice{self.system_currency}'] * (1-self.overall_vola/len(self.transactions)/400),2)
            (self.buy_df['buyAt'],self.buy_df['buyTarget'],self.buy_df['sellAt'],self.buy_df['sellTarget'],_,_,_,_,_,_) = zip(*self.buy_df[f'buyPrice{self.system_currency}'].apply(indicator.target_prices))
        except Exception:
            st.write(_t('mt.no_buy_data'))
            pass
        try:
            (_,_,self.sell_df['sellAt'],self.sell_df['sellTarget'],_,_,_,_,_,_) = zip(*self.sell_df[f'sellPrice{self.system_currency}'].apply(indicator.target_prices))
        except Exception:
            st.write(_t('mt.no_sell_data'))
            pass

        if not self.disable_streamlit:
            if 1:

#                self.asset_date = st.text_input('Filter date from: ', f"{dt.datetime.now().year}-01-01")#,(dt.datetime.now() + dt.timedelta(days=day_diff)).strftime("%Y-%m-%d"))

                st.html(f'<h3>{_t("mt.buy_header")}</h3>')
                filter_buy_df()
#                st.write(self.asset_date)
                selection2 = self.simulator.dataframe_with_selections(self.buy_df,region=st, column_config=column_config)    
                if not selection2.empty:
                    #self.overlay_selector(selection2, region = st, action_type='buy')            
                    tickers = self.buy_df['ticker'].tolist()
                    tickers = list(set(tickers))
                    show_tickers(tickers)
                self.export_to_excel(self.buy_df, button_label=_t('mt.download_buy'), file_name='buy_trades.xlsx', region=st)

                st.html(f'<h3>{_t("mt.sell_header")}</h3>')
                filter_sell_df()
                selection3 = self.simulator.dataframe_with_selections(self.sell_df,region=st, column_config=column_config)    
                if not selection3.empty:
#                    try:
#                        self.overlay_selector(selection3, region = st, action_type='sell')            
#                    except Exception:
#                        pass
                    tickers = self.sell_df['ticker'].tolist()
                    tickers = list(set(tickers))
                    show_tickers(tickers)
                self.export_to_excel(self.sell_df, button_label=_t('mt.download_sell'), file_name='sell_trades.xlsx', region=st)
        else:

            # last days
            day_diff = -1
                                
            self.asset_date = (dt.datetime.now() + dt.timedelta(days=day_diff)).strftime("%Y-%m-%d")
            filter_sell_df()
            filter_buy_df()
                
 #       if not self.disable_streamlit:

            # Import / Export trades UI (delegated)
 #           try:
                # Helpful instruction placed near the Import/Export panel so users see it
#                st.info('Press "Calculate transactions" to run simulations. You can upload your own trades first in Import/Export.')
#                from tradinglib.own_trades_analysis import render_import_export
#                render_import_export(st, simulator=self.simulator, username=self.username, db_path='database')
#            except Exception as e:
                # If the dedicated own-trades UI fails to render we show a concise error here
#                st.error(f'Failed to render import/export UI: {e}')

            # Executed trades UI moved to `asset_analyzer` -> 'Own transactions' (uses OwnTradesManager)
            # This section was intentionally removed so all own-trades import/analysis UI is centralized.

#        else:
            print(self.clean_html(summary))
        
def run_notifier(notifier_cache="pushover_notifier.json", db_name="asset_simulation", transactions = {}, username = 'kurt'):
    
    mtp = MultiTransactionProcessor(disable_streamlit=True, username=username, db_name=db_name, transactions=transactions)
    mtp.render()
    url = "https://trading.cloogidoo.com/?symbol="

    try:
        notfr = pn.PushoverNotifier(storage_file=mtp.get_path(file_name=notifier_cache))
    except Exception:
        print("Error, check key and credentials file.")
        exit()
    
#    mtp.buy_df.to_csv("buy_df.csv",decimal=",",sep=";",mode='a+')
#    mtp.sell_df.to_csv("sell_df.csv",decimal=",",sep=";",mode='a+')
    for idx, row in mtp.buy_df.iterrows():
        try:
            x_rate = mtp.get_exchange_rate(symbol=row['currency'],system_currency=mtp.system_currency)
            message = f"""
            Strategy: {row['Strategy']} 
            Date: {row['buyDate']} 
            Buy: {row['longName']}
            Volume: {row['buyVolume']}
            Currency: {row['currency']}
            Buy Price: {round(row['buyPrice'],2)}
            Buy value: {row[f'buyValue']}
            Price target: {round(row['buyTarget'],2)}
            Stop loss: {round(row['stopLoss'],2)}
            Stop loss 2%: {round(row['buyPrice']*0.98,2)}
            Url: {url}"{row['ticker']}"
            """
            if not mtp.system_currency == row['currency']:
                message = f"""{message}
                Buy price {mtp.system_currency}: {round(row[f'buyValue{mtp.system_currency}']/row['buyVolume'],2)}
                Buy value {mtp.system_currency}: {row[f'buyValue{mtp.system_currency}']}
                Price target {mtp.system_currency}: {round(row['buyTarget'],2)}
                Stop loss {mtp.system_currency}: {round(row['stopLoss'],2)}
                Stop loss 2% {mtp.system_currency}: {round(row['buyPrice']*0.98/x_rate,2)}
                """
            notfr.send_notification(ticker=row['ticker'],price=row['buyPrice'], date=row['buyDate'],message=message, title="Buy asset")
        except Exception:
            pass

    for idx, row in mtp.sell_df.iterrows():
        try:
            x_rate = mtp.get_exchange_rate(symbol=row['currency'],system_currency=mtp.system_currency)
            message = f"""
            Strategy: {row['Strategy']} 
            Date: {row['sellDate']} 
            Sell: {row['longName']}
            Volume: {row['sellVolume']}
            Currency: {row['currency']}
            Sell price: {round(row['sellPrice'],2)}
            Sell value: {row[f'sellValue']}
            Url: {url}"{row['ticker']}"
            """
            if not mtp.system_currency == row['currency']:
                message = f"""{message}
                Sell price {mtp.system_currency}: {round(row[f'sellPrice{mtp.system_currency}'],2)}
                Sell value {mtp.system_currency}: {row[f'sellValue{mtp.system_currency}']}
                Price target {mtp.system_currency}: {round(row['sellTarget'],2)}
                """
            notfr.send_notification(ticker=row['ticker'],price=row['sellPrice'],date=row['sellDate'],message=message, title="Sell asset")
        except Exception:
            pass

    pass

if __name__ == "__main__":
    
    run_notifier()
