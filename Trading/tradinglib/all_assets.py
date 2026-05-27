from tradinglib import (
        ticker_tools as tt,
        graph_tools as gt,
        asset_simulator as ass,
        search as sr,
        system_config as sysconf,
        tiny_chart as tc,
        fetch_data as fd,
    )
from tradinglib.tiny_chart_grid import ChartsGridRenderer
import pandas as pd
import streamlit as st 

class AllAssetsView(tt.TickerTools):

    url="/?details=true&symbol="
    
    def __init__(self, username=None, is_admin=False):
#        super().__init__()
        self.username = username
        self.is_admin = is_admin
        self.sys_config = sysconf.SystemConfig(username=username, is_admin = self.is_admin)
        self.system_currency = self.sys_config.get_value("system_currency",default="EUR")
        self.render()


    def render(self):
        
        db_name = "asset_simulation_all"
#        qv = st.checkbox("Quick view",False)
#        if qv:
#            db_name = "asset_short"            
        db = tt.tools.Db_tools(db_path='database', database_name=f'{db_name}.db')
        # Attach die anderen Datenbanken
        buy_query = self.sys_config.get_value("buy_query",default="(ewo>ewo_ema)")
        sell_query = self.sys_config.get_value("sell_query",default="(ewo<ewo_ema)")
        db.conn.execute(f"ATTACH DATABASE '{self.get_path(path = 'database', file_name='asset_info.db')}' AS info_db")
        bq_input = st.text_input('Filter data by: ',buy_query)
        o_by = "ORDER BY Date DESC, ap.currency, ap.sortino DESC, ap.overallValueTrend DESC Limit 7000;"
#        if qv:
#            o_by = "ORDER BY Date DESC, ap.currency Limit 7000;"

        query = f"""SELECT ai.longName, ai.exchange, ap.* FROM asset_simulation as ap 
        INNER JOIN info_db.asset_info as ai on ap.ticker = ai.ticker
        WHERE {bq_input}  
        {o_by}
        """
        try:
            df = pd.read_sql_query(query, db.conn)
        except Exception:
            st.error("No matching data found, please refine your Filter!")
            df = pd.DataFrame()
            pass
        column_config = {
            "details": st.column_config.LinkColumn( "Details", display_text="View"),
            }
        try:
            df['details'] = df['longName'].apply(self.add_url)
#            df['invest'] = df['logVola'].apply(tt.calculate_investment)
            cols = list(df)
            cols.insert(0, cols.pop(cols.index('invest')))
            df = df.loc[:, cols]
#            cols.insert(0, cols.pop(cols.index('details')))
#            df = df.loc[:, cols]

        except Exception:
            pass


        if not df.empty:

            selection = ass.AssetSimulator().dataframe_with_selections(df=df,column_config=column_config)
            self.export_to_excel(df, button_label = f'📥 Download Assets', file_name = f'Asset_dataset.xlsx', region = st )
            date = df['Date'].iloc[0] if not df.empty else None
            tickers = df[df['Date']==date]['ticker']

            # use renderer to obtain selectors and render charts
            renderer = ChartsGridRenderer(columns=2)
            (interval, period, overlays, oszilators) = renderer.get_selectors(self.sys_config)

            with st.spinner("Wait for graphs to load...", show_time=True):
                renderer.render(
                    tickers=tickers,
                    tc=tc,
                    period=period,
                    interval=interval,
                    overlays=overlays,
                    oszilators=oszilators,
                    username=self.username,
                    url=self.url,
                    chart_config=gt.chart_config,
                    name_df=df,
                    name_lookup_col='ticker',
                    name_field='longName',
                    log_exceptions=True,
                )

#                st.write(f'Last chart update: {dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

        if False:
#        if qv:

            invest = 2000000
            no_assets = 6
            limit = 100000
            r_entries = st.empty()
            (fe, ie, ne, li) = r_entries.columns([3,1,1,1])
            sq_input = fe.text_input('Filter data by: ',sell_query)
            iv_input = int(ie.text_input('Invest: ', invest))
            na_input = int(ne.text_input('no of assets: ',no_assets))
            li_input = int(li.text_input('Limit: ',limit))

            o_by = f" ORDER BY Date DESC, ticker LIMIT {li_input}"
            query = f"""SELECT ai.longName, ai.exchange, ap.* FROM asset_simulation as ap 
            INNER JOIN info_db.asset_info as ai on ap.ticker = ai.ticker {o_by}
            """
            try:
                df = pd.read_sql_query(query, db.conn)
            except Exception:
                st.error("No matching data found, please refine your Filter!")
                df = pd.DataFrame()
                pass

            # we fake some columns
            df['take_profit'] = None
            df['stop_loss'] = None
            df['ISIN'] = None
            df['stockIndex'] = None
            df['vola'] = None
            self.simulator = ass.AssetSimulator("yf_tickers.db", f"{db_name}.db", "asset_info.db", db_path='database', username=self.username)

            df.sort_values(['Date'], inplace=True, ascending=[True])

            try:
                signal_gen = ass.tools.BuySellSignalGenerator(df=df, 
                                                    buy_condition=bq_input, 
                                                    sell_condition=sq_input,
                                                    buy_delay_days = 1)

                combined_df = signal_gen.apply_signals()
                portfolio = ass.PortfolioSimulator(data = combined_df, initial_cash=iv_input, max_assets=na_input)
                portfolio.simulate()

                # use DataUtils helper for conversions instead of creating converter instance
                # converter = fd.CurrencyConverter(self.system_currency)
                transact_df = portfolio.get_transaction_dataframe()
                if not transact_df.empty:
                    transact_df.sort_values(['timestamp','ticker'], inplace=True, ascending=[False,True])
                    #for idx, row in transact_df.iterrows():
                    #    if pd.isna(transact_df.loc[idx,f'sellValue']):
                    #        transact_df.loc[idx,f'sellValue'] = round(transact_df.loc[idx,'sellPrice'] * transact_df.loc[idx,'buyVolume'],2)
                    #transact_df[f'buyValue{self.system_currency}'] = 0
                    #transact_df[f'sellValue{self.system_currency}'] = 0
                    #transact_df[f'sellValue{self.system_currency}'] = transact_df.apply(lambda row: float(converter.convert(row["sellDate"], row["sellValue"], row["currency"])), axis=1)
                    #transact_df[f'buyValue{self.system_currency}'] = transact_df.apply(lambda row: float(converter.convert(row["buyDate"], row["buyValue"], row["currency"])), axis=1)
                    #transact_df[f'buyPrice{self.system_currency}'] = -round(transact_df[f'buyValue{self.system_currency}'] / transact_df[f'buyVolume'],2)
                    #transact_df[f'sellPrice{self.system_currency}'] = round(transact_df[f'sellValue{self.system_currency}'] / transact_df[f'buyVolume'],2)


                    selection = self.simulator.dataframe_with_selections(transact_df, region = st)    

                    """
                    total_invest = 0
                    for n in portfolio.bought_assets:
                        total_invest += transact_df.loc[transact_df['ticker']==n].value.sum()
                    
                    total_invest = round(total_invest,2)

                    gain = round(portfolio.cash+(-total_invest),0)
                    t_gain = round((gain/invest*100)-100,1)
                    st.write(f"Gain: {gain}, Total gain in percent: {t_gain}%")
                    """
                else:
                    st.write('No data')

            except Exception as e:
                st.write(e)
                pass