from tradinglib import (
        ticker_tools as tt,
        graph_tools as gt,
        search as sr,
        system_config as sysconf,
        tiny_chart as tc,
        fetch_data as fd,
    )
try:
    from tradinglib.premium import asset_simulator as ass
except ImportError:
    ass = None
from tradinglib.tiny_chart_grid import ChartsGridRenderer
from tradinglib.i18n import t
import pandas as pd
import streamlit as st 

class AllAssetsView(tt.TickerTools):

    url="/?details=true&symbol="
    
    def __init__(self, username=None, is_admin=False):
        """Initialize the all-assets screener view and immediately render it."""
        self.username = username
        self.is_admin = is_admin
        self.sys_config = sysconf.SystemConfig(username=username, is_admin = self.is_admin)
        self.system_currency = self.sys_config.get_value("system_currency",default="EUR")
        self.render()


    @st.dialog('Chart', width='large')
    def overlay_chart(self, selection):
        """Show a tiny_chart overlay for the selected row; keep full-viewer link."""
        try:
            ticker = selection['ticker'].iloc[0]
            longname = selection['longName'].iloc[0] if 'longName' in selection.columns else ticker
        except Exception:
            st.error("No asset selected.")
            return
        viewer_url = f"{self.url}{ticker}"
        st.markdown(f"[{t('assets.view_text')} (Full details) →]({viewer_url})")
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
                url=self.url,
            )
        if t_chart.fig:
            st.plotly_chart(t_chart.fig, use_container_width=True)
        else:
            st.warning(f"No chart data available for {ticker}.")

    def render(self):
        """Render the full screener: filtered asset table, Excel export, and optional mini-chart grid."""
        db_name = "asset_simulation_all"
#        if qv:
        db = tt.tools.Db_tools(db_path='database', database_name=f'{db_name}.db')
        # Attach die anderen Datenbanken
        buy_query = self.sys_config.get_value("buy_query",default="(ewo>ewo_ema)")
        sell_query = self.sys_config.get_value("sell_query",default="(ewo<ewo_ema)")
        db.conn.execute(f"ATTACH DATABASE '{self.get_path(path = 'database', file_name='asset_info.db')}' AS info_db")
        bq_input = st.text_input(t('assets.filter_label'), buy_query)
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
            st.error(t('assets.no_data_error'))
            df = pd.DataFrame()
            pass
        try:
            cols = list(df)
            cols.insert(0, cols.pop(cols.index('invest')))
            df = df.loc[:, cols]
        except Exception:
            pass


        if not df.empty:

            event = st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                key="aa_asset_table",
            )
            if event.selection.rows:
                selected_ticker = df.iloc[event.selection.rows[0]]['ticker']
                if st.session_state.get("aa_last_shown") != selected_ticker:
                    st.session_state["aa_last_shown"] = selected_ticker
                    self.overlay_chart(df.iloc[[event.selection.rows[0]]])
            else:
                st.session_state.pop("aa_last_shown", None)

            self.export_to_excel(df, button_label=t('assets.download_btn'), file_name='Asset_dataset.xlsx', region=st)
            col_chk, col_sel = st.columns([1, 2])
            show_grid = col_chk.checkbox(t('perf.show_grid'), value=False, key='aa_show_grid')
            grid_count = col_sel.selectbox(t('perf.grid_count'), [5, 10, 20], index=1, key='aa_grid_count') if show_grid else 10
            if show_grid:
                date = df['Date'].iloc[0] if not df.empty else None
                tickers = df[df['Date']==date].sort_values('sortino', ascending=False)['ticker'].head(grid_count)

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
                        url=self.url,
                        chart_config=gt.chart_config,
                        name_df=df,
                        name_lookup_col='ticker',
                        name_field='longName',
                        log_exceptions=True,
                    )


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
                st.error(t('assets.no_data_error'))
                df = pd.DataFrame()
                pass

            # we fake some columns
            df['take_profit'] = None
            df['stop_loss'] = None
            df['ISIN'] = None
            df['stockIndex'] = None
            df['vola'] = None
            self.simulator = ass.AssetSimulator("yf_tickers.db", f"{db_name}.db", "asset_info.db", db_path='database', username=self.username)

            df = df.sort_values(['Date'], ascending=[True])

            try:
                signal_gen = ass.tools.BuySellSignalGenerator(df=df, 
                                                    buy_condition=bq_input, 
                                                    sell_condition=sq_input,
                                                    buy_delay_days = 1)

                combined_df = signal_gen.apply_signals()
                portfolio = ass.PortfolioSimulator(data = combined_df, initial_cash=iv_input, max_assets=na_input)
                portfolio.simulate()

                # use DataUtils helper for conversions instead of creating converter instance
                transact_df = portfolio.get_transaction_dataframe()
                if not transact_df.empty:
                    transact_df = transact_df.sort_values(['timestamp','ticker'], ascending=[False,True])
                    #for idx, row in transact_df.iterrows():
                    #    if pd.isna(transact_df.loc[idx,f'sellValue']):
                    #transact_df[f'sellValue{self.system_currency}'] = transact_df.apply(lambda row: float(converter.convert(row["sellDate"], row["sellValue"], row["currency"])), axis=1)
                    #transact_df[f'buyValue{self.system_currency}'] = transact_df.apply(lambda row: float(converter.convert(row["buyDate"], row["buyValue"], row["currency"])), axis=1)


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
