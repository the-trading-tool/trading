from tradinglib import ( tiny_chart as tc, search as sr,
        sentiment as se, headlines as hl, multi_select as ms, fetch_data,
        system_config as sysconf, graph_tools as gt)
from tradinglib.indicator import indicator  # Die Basisklasse importieren
import streamlit as st
import streamlit_nested_layout
import datetime as dt
import logging

logger = logging.getLogger(__name__)

#### Main
class render_mainpage(fetch_data.FetchData):

    symbol = ''

    def __init__(self, symbol = '', region = st, search_ticker_only=False, hide_search = False, hide_details = False, username='', is_admin = False, interval = None, period = None, multi_trends = False, tab_details = False):
        self.region = region
        self.symbol = symbol
        self.ticker = symbol
        self.multi_trends = multi_trends
        self.interval = interval
        self.period = period
        self.tab_details = tab_details
        self.is_admin = is_admin
        self.username = username
        self.search_ticker_only = search_ticker_only
        self.hide_search = hide_search
        self.hide_details = hide_details
        self.sys_conf = sysconf.SystemConfig(region=region, username=username, is_admin=is_admin)
        self.overlays=['heikin','candle','atc']
        self.oszilators=['ewo','zcr']                                               
        self.render()
    
    def get_item(self, data, name, col, select):
        item = ''
        try:
            item = data.loc[data[col]==select][name].item()
        except Exception:
            pass
        return item

#    @st.fragment(run_every='300s')
    def render_trend(self, ticker_selected, ticker_selected_longname, interval, period, region=st):


                if not self.multi_trends:

                    region.plotly_chart(
                        self.t_chart.fig,
                        use_container_width = True,
                        #sharing="streamlit",
                        theme="streamlit",
                        config = gt.chart_config,
                        )

                else:

                    tr_charts = {
#                        0:{'interval':'1mo','period':'1y'},
                        0:{'interval':'1wk','period':'6mo'},
#                        2:{'interval':'1d','period':'2mo'},
                        1:{'interval':'1h','period':'1mo'},
                        2:{'interval':'30m','period':'2wk'},
                        3:{'interval':'5m','period':'2d'},
                        }
                
                    items = len(tr_charts)
                    if items > 0:

                        for p in range(0,items):

                            tr = tr_charts[p]
                            tr_iv = tr['interval']
                            tr_pr = period #tr['period']
#                                if 1:
                            try:
                                t_chart_n = tc.tiny_chart(ticker_selected,
                                                f' {tr_iv}/{tr_pr} trend',
                                                tr_pr,
                                                tr_iv,
                                                False, 
                                                username=self.username,
                                                add_overlays=['atl'],
                                                )
                                for trace in t_chart_n.fig.data :    
                                    t_chart_n.fig.add_trace(trace, row=1, col=1)
                                for shape in t_chart_n.fig.layout.shapes:    
                                    t_chart_n.fig.add_shape(shape, row=1, col=1)                    
                                #for annotation in t_chart_n.fig.layout.annotations:
                                #    t_chart.fig.add_annotation(annotation, row=1, col=1)              

                            except Exception:
                                pass

                        region.plotly_chart(
                            t_chart_n.fig,
                            use_container_width = True,
                            #sharing="streamlit",
                            theme="streamlit",
                            config = gt.chart_config,
                        )

                region.write(f'Last chart update: {dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

#                except Exception:
#                    pass

    def render(self):

        logger.debug(f'render_mainpage called symbol={self.symbol} username={self.username} is_admin={self.is_admin}')
        def set_ticker(ticker):
            self.ticker = ticker
            self.symbol = ticker
            return ticker
            
#        st.title('Asset viewer')
        try:
            st.set_page_config(layout="wide")
        except Exception:
            pass

#        panel_pos = st.empty()
#        (pp_left, pp_right) = panel_pos.columns([0.01,.99],gap='small')
        pp_right = st
        
#        exp_ = pp_right.expander('Asset details',expanded=True)
#        with exp_:
        srch_region = pp_right.empty()
        slctr_region = st.empty()
#            st.markdown("""---""")
        head_row1 = st.empty()
        head_row2 = st.empty()
    
        slider_row = pp_right.empty()
    
        self.multi_selector = ms.MultiCheckboxSelector(region=slctr_region, sys_conf=self.sys_conf)
        (sr_left, sr_right, sr_conf, sr_hlp) = srch_region.columns([0.49,0.49,0.05,0.05])

        if not self.hide_search:
            if sr_conf.button(":rosette:", use_container_width=True):
                self.sys_conf.render()
            if sr_hlp.button(":grey_question:",use_container_width=True):
                self.sys_conf.render_help()

#        (sr_left, sr_right,_,cfg_btn_c,cfg_btn_h) = srch_region.columns([0.35,0.35,0.06,0.07,0.07],gap='small')
        mkt = sr.MarketSearch(region=sr_left)
        fts = sr.FullTextSearch(region=sr_right, symbol=self.symbol, search_ticker_only=True, is_admin=self.is_admin)

        if not self.hide_search:
            mkt.render()
            fts.render()
        else:
            fts.symbol_search()
        
        # Create an instance of the class and display the selectors
        self.multi_selector.render()
        interval = self.multi_selector.get_selected_options('Interval')[:1]
        period = self.multi_selector.get_selected_options('Period')[:1]
        self.overlays = self.multi_selector.get_selected_options('Overlay')
        self.oszilators = self.multi_selector.get_selected_options('Oszilator')
        
        (interval, period, self.overlays, self.oszilators) = self.sys_conf.get_selectors(interval, period, self.overlays, self.oszilators)

        if not self.interval == None:
            interval = self.interval
        if not self.period == None:
            period = self.period

        try:
            if interval == "1m":
                if 'bsz' in self.overlays:
                    self.overlays.remove('bsz')
        except Exception:
            pass
        candle_chart = False
        if 'candle' in self.overlays:
            candle_chart = True
        trend_length = 21
        max_trend_length = self.calc_max_periods(interval,period)
        if trend_length > max_trend_length:
            trend_length = int(max_trend_length/2)

        self.url = f"/?symbol="        

        add_current = False
        if interval == "1d":
            add_current = True
        
        show_details = self.sys_conf.get_value("mp_details",False)
        if self.tab_details:
            show_details = True

#        refresh = st.button("Refresh", use_container_width=True)
        refresh = True
        if not self.hide_details:

            tab_list = ["Trend", "Info", "Income sheet", "Balance sheet", "News"]
            if show_details:
                tab_list.append('Details')

            tabs = pp_right.tabs(tab_list)
            tab_trend = tabs[0]
            tab_info = tabs[1]
            tab_income_sheet = tabs[2]
            tab_balance_sheet = tabs[3]
            tab_news = tabs[4]
            if show_details:
                tab_details = tabs[5]

        ticker_selected = set_ticker(fts.ticker_selected)
        ticker_selected_longname = fts.ticker_selected_longname
        self.data = fts.df

        if not ticker_selected:
            ticker_selected = set_ticker(mkt.ticker_selected)
            ticker_selected_longname = mkt.ticker_selected_longname
            self.data = mkt.df
        
        if refresh:
#        if 1:
#                try:
            self.t_chart = tc.tiny_chart(
                ticker_selected, 
                longname=f"{ticker_selected_longname} - {interval}/{period}",
                interval=interval, 
                period=period, 
                url=f'{self.url}', 
                candle_chart=candle_chart, 
                show_trend=False, 
                range_breaks=True,
                trend_length=trend_length,
                add_sub_plots=self.oszilators, 
                add_overlays=self.overlays,
                username=self.username, 
                zoom = True,
                pips_select = True,
                add_current=add_current,
                region = slider_row
                )
            self.df = self.t_chart.df
            self.ticker = self.t_chart.ticker


            if 1:
#        try:
                headlines = hl.Headlines(self.df, self.ticker, self.data, screen_region_row1=head_row1, screen_region_row2=head_row2, interval = interval, index_name=fts.index_name, system_currency=self.sys_conf.get_value("system_currency","USD"))
                headlines.render()
#        except Exception:
#            pass

            # Tabs

            if ticker_selected:

                if self.hide_details:

                    self.render_trend(ticker_selected, ticker_selected_longname, interval=interval, period=period )
                    if self.sys_conf.get_value("pine_export", False):
                        self.multi_selector.render_pine_export()

                else:

                    with tab_trend:

                        self.render_trend(ticker_selected, ticker_selected_longname, interval=interval, period=period)
                        if self.sys_conf.get_value("pine_export", False):
                            self.multi_selector.render_pine_export()
        
    #, add_sub_plots=['ewo']
                    if show_details:
                        with tab_details:
                
                            tr_charts = {
                                0:{'interval':'1mo','period':'max'},
                                1:{'interval':'1wk','period':'10y'},
                                #2:{'interval':'1d','period':'3y'},
                                }
                    
                            columns = len(tr_charts)
                            items = len(tr_charts)
                            if items > 0:

                                candle_chart = True
                                rows = round(items/columns)+1
                                ic = {}
                                ir_c = {}
                                for i in range(0,rows):
                                    ic[i] = st.empty()
                                    ir_c[i] = ic[i].columns(columns)
                                j = 0

                                for p in range(0,items):

                                    tr = tr_charts[p]
                                    tr_iv = tr['interval']
                                    tr_pr = tr['period']
    #                                if 1:
                                    try:
                                        i = p%columns
                                        if i == 0:
                                            j += 1
                                        t_chart = None     
                                        t_chart = tc.tiny_chart(ticker_selected,
                                                                f' {tr_iv}/{tr_pr} trend',
                                                                tr_pr,
                                                                tr_iv,
                                                                True, 
                                                                candle_chart=candle_chart,
                                                                url=f'{self.url}',
                                                                range_breaks=True,
                                                                ath=True, 
                                                                calc_ly_hl=True,
                                                                username=self.username,
                                                                add_overlays=self.overlays,
                                                                )
                                        fig = t_chart.fig
                                        ir_c[j][i].plotly_chart(fig,
                                            use_container_width = True,
                                            #sharing="streamlit",
                                            theme="streamlit",
                                            config = gt.chart_config,
                                        )
                                    except Exception:
                                        pass

                            full_df_ex1 = st.expander('Data')
                            with full_df_ex1:
#                                try:
##                                    st.dataframe(self.data)
                                    st.dataframe(self.t_chart.df)
#                                except Exception:
#                                    pass
                    
                    with tab_info:

                        info = self.get_ticker_value(self.ticker,'longBusinessSummary')
                        if info:
                            st.info(info)                

                        pass
                
                    with tab_income_sheet:       
    #                   if 1:
                        try:
                            sht_df = self.get_sheet_as_df(self.ticker, 'incomeSheet', 'Category')
                            if not sht_df.empty:
                                st.subheader("""**Income sheet** for """ + ticker_selected)
                                st.dataframe(sht_df,use_container_width=True)               
                    
                        except Exception:
                            pass
                
                    with tab_balance_sheet:
                    
                        try:
                            sht_df = self.get_sheet_as_df(self.ticker, 'balanceSheet', 'Category')
                            if not sht_df.empty:
                                st.subheader("""**Balance sheet** for """ + ticker_selected)
                                st.dataframe(sht_df,use_container_width=True)               
                    
                        except Exception:
                            pass
                
                    with tab_news:
                    
                        sentiment = se.YahooNewsSentiment(ticker_selected)
                        sentiment.render()
                        pass

