import os
import streamlit as st
import datetime as dt
import yaml
from yaml.loader import SafeLoader
import json
import pandas
import numpy as np

from streamlit_authenticator import Authenticate
from tradinglib import (
    portfolio as po, tiny_chart as tc, tools as ts, admin, market_map as mm, ticker_tools as tt,
    main_page as mp,
    multi_select as ms, search as sr, system_config as sysconf, banner_page as bp,
    live_ticker as lt, file_provider as fp, graph_tools as gt,
    performance_details as perfdetails, all_assets as aa,
    option_calculator as op, parity as pr, earnings_calendar as ec
)
from tradinglib.premium_availability import STRATEGY_ENGINE_AVAILABLE, PAPER_TRADING_AVAILABLE
from tradinglib.license_manager import has_feature, FEATURE_STRATEGY_ENGINE, FEATURE_PAPER_TRADING

# Premium modules — only imported when the files are present
ass = None
mu = None
if STRATEGY_ENGINE_AVAILABLE:
    try:
        from tradinglib.premium import asset_simulator as ass
        from tradinglib.premium import multi_transaction as mu
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning("Premium strategy modules could not be loaded: %s", _e)
from tradinglib.indicator import indicator
from tradinglib.tiny_chart_grid import ChartsGridRenderer

import pandas as pd
 #import numpy as np
from tradinglib import market_data as md
from tradinglib.fetch_data import FetchData
from concurrent.futures import ThreadPoolExecutor, as_completed
import plotly.express as px
import logging
from tradinglib import logging_config as lgc
from tradinglib import i18n
from tradinglib.i18n import t

# Enable debug logging to the console so module debug statements (e.g. tiny_chart)
# are visible when running Streamlit. Adjust level if too verbose.
lgc.enable_logging(to_console=False, level='DEBUG')

class PortfolioOptimizer:
    def __init__(self, tickers, investment_per_asset=2000):
        self.tickers = tickers
        self.total_budget = len(tickers) * investment_per_asset
        self.results = None
        # When allow_network is False we use only local sqlite data (FetchData)
        # When True we allow network fallbacks via the centralized market_data wrapper
        self.allow_network = True

    def set_allow_network(self, allow: bool):
        self.allow_network = bool(allow)

    def fetch_and_calculate(self, period="1y"):
        # Use threaded per-ticker local reads to avoid blocking Streamlit main thread
        def _load_series_for_ticker(ticker_symbol: str):
            try:
                # create a fresh FetchData per thread to ensure separate sqlite connections
                fd_thread = FetchData(database_path='database')
                df_t = fd_thread.load_price_data(ticker_symbol, period=period, interval='1d', aggregate=False)
                if df_t is None or df_t.empty:
                    # No local data available.
                    # If network usage is allowed, use the centralized market_data wrapper to fetch remote data.
                    if self.allow_network:
                        df_remote = md.ticker_history(ticker_symbol, period=period, interval='1d')
                        if df_remote is None or df_remote.empty:
                            return None
                        if 'Close' in df_remote.columns:
                            s = df_remote['Close']
                        else:
                            s = df_remote
                    else:
                        # Network disallowed and no local data -> skip this ticker
                        return None
                else:
                    try:
                        df_t.index = pd.to_datetime(df_t.index)
                    except Exception:
                        pass
                    if 'Close' in df_t.columns:
                        s = df_t['Close']
                    else:
                        return None
                # Normalize timezone using a central helper to ensure consistent behavior
                try:
                    from tradinglib.utils import DataUtils
                    DataUtils.normalize_index_to_utc_naive(s)
                except Exception:
                    try:
                        s.index = pd.to_datetime(s.index)
                    except Exception:
                        pass
                s = s.rename(ticker_symbol)
                return s
            except Exception:
                return None

        series_list = []
        max_workers = min(8, max(1, len(self.tickers)))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_load_series_for_ticker, t): t for t in self.tickers}
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    if res is not None:
                        series_list.append(res)
                except Exception:
                    # ignore individual failures
                    pass

        if series_list:
            df = pd.concat(series_list, axis=1)
        else:
            # fallback: try a bulk remote download only when network use is allowed
            if self.allow_network:
                df = md.download(self.tickers, period=period, interval='1d')
            else:
                # No data available locally and network is disabled -> empty
                df = pd.DataFrame()
        
        # Sicherer Zugriff auf die Schlusskurse (Close)
        # yfinance liefert bei mehreren Tickern meist einen MultiIndex (z.B. ['Close', 'AAPL'])
        if isinstance(df, pd.DataFrame) and 'Close' in df.columns:
            data = df['Close']
        else:
            # Fallback falls die Struktur anders ist
            data = df

        # Falls ein Ticker keine Daten hat, wird er hier entfernt, um dropna() nicht zu leeren
        data = data.dropna(axis=1, how='all')
        
        # Tägliche Renditen berechnen
        returns = data.pct_change().dropna()
        
        if returns.empty:
            raise ValueError("Der Dataframe ist leer. Prüfen Sie die Ticker oder den Zeitraum.")

        # Inverse Volatilität berechnen
        volatility = returns.std()
        inv_vol = 1 / volatility
        
        # Gewichtung berechnen (Summe = 1)
        weights = inv_vol / inv_vol.sum()
        
        # Ergebnis-Dataframe bauen
        self.results = pd.DataFrame({
            'Ticker': weights.index,
            'Gewichtung (%)': (weights.values * 100).round(2),
            'Anlagebetrag (€)': (weights.values * self.total_budget).round(2)
        })
        
        return self.results

    def plot_portfolio(self):
        if self.results is None:
            return
        
        self.fig = px.pie(
            self.results, 
            values='Gewichtung (%)', 
            names='Ticker',
            title=f"Optimierte Portfolio-Verteilung (Gesamtbudget: {self.total_budget} €)",
            hole=0.4,
            template="plotly_white"
        )
        self.fig.update_traces(textinfo='percent+label')
        
# ChartsGridRenderer provided by tradinglib.tiny_chart_grid

class TradingApp:

    login_dialog = st.session_state.get('login', "Login....")
    config_file = 'config.yaml'
    url="/?symbol="

    def __init__(self):
        self.title = "The Trading Tools"
        if 'title' not in st.session_state:
            st.set_page_config(self.title, initial_sidebar_state='collapsed', layout="wide" )
        else:
            st.set_page_config(st.session_state.title, initial_sidebar_state='collapsed',layout="wide" )
        self.authentication_status = None
        self.is_admin = False
        self.config = self.load_config()
        self.authenticator = self.init_authenticator()
        self.msg_text = st.empty()
#        self.init_session()
#        self.sys_config = sysconf.SystemConfig(username=self.username)
        self.markdown_main() 
        self.lt = None
        self.logger = logging.getLogger(__name__)

    def markdown_main(self):
        st.markdown(
            """
            <style>
                    .stAppHeader {
                        background-color: rgba(255, 255, 255, 0.0);  /* Transparent background */
                        visibility: visible;  /* Ensure the header is visible */
                    }

                   .block-container {
                        padding-top: 2rem;
                    }
            </style>
            """,
            unsafe_allow_html=True,
        )
#        # Inject custom CSS to set the width of the sidebar
#        st.markdown(
#            """
#                <style>
#                    section[data-testid="stSidebar"] {
#                        width: 420px !important; # Set the width to your desired value
#                    }
#                </style>
#                """,
#                unsafe_allow_html=True,
#            )
        
    def load_config(self):
        with open(ts.Tools().get_path('', self.config_file)) as file:
            return yaml.load(file, Loader=SafeLoader)    

    def local_css(self, file_name):
        with open(file_name) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

    def init_authenticator(self):
        return Authenticate(
            self.config['credentials'],
            self.config['cookie']['name'],  
            self.config['cookie']['key'],
            self.config['cookie']['expiry_days'],
        )
    
    def init_session(self):
        self.username = st.session_state.get("username", 'api_key')
        self.authentication_status = st.session_state.get("authentication_status", None)
        self.is_admin = st.session_state.get("is_admin", False)
        self.sys_config = sysconf.SystemConfig(username=self.username)
#        self.rt_prices = self.sys_config.get_value('rt_prices',False)
#        if self.rt_prices and sys.platform == 'win32':
    
    def set_page_title(self, title=None, icon=None):
        if title:
            st.session_state['title'] = title
        if icon:
            st.session_state['icon'] = icon
        self.sidebar_header.markdown(f"""<h2>{t('page.selected_view', title=title)}</h2>""", unsafe_allow_html=True)
#        self.sidebar_header.write(f"{title}\n")    
        
    def set_page_config(self, title):
        try:
            st.set_page_config(title, layout="wide", initial_sidebar_state='collapsed')
        except Exception:
            pass
        self.set_page_title(title)

        
    def login_overlay(self):

        try:
            self.authenticator.login()
        except Exception as e:
            print(e)
            pass

        self.init_session()
        self.sys_config = sysconf.SystemConfig(username=self.username)

    # get_selectors moved to tradinglib.tiny_chart_grid.ChartsGridRenderer

    @st.fragment(run_every='300s')
    def render_chart(self, index_columns, region = st):
        
        if not index_columns == 'ANY':

                renderer = ChartsGridRenderer()
                (interval, period, overlays, oszilators) = renderer.get_selectors(self.sys_config)
                
                region.plotly_chart(tc.tiny_chart( f'{index_columns}',f' {interval}/{period} trend',period,interval,True, True,range_breaks=True,add_sub_plots=oszilators, add_overlays=overlays,url=f"{self.url}").fig,
                    use_container_width = True,
                    #sharing="streamlit",
                    theme="streamlit",
                    config = gt.chart_config,
                )
                st.write(f'Last page refresh: {dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    
    @st.fragment(run_every='120s')
    def live_chart(self, region=st):
        #if self.rt_prices and sys.platform == 'win32':
#        st.write(f'Last page reload: {dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        if self.lt == None:
            
            self.lt = lt.LiveTicker(init=True,username=self.username, is_admin=self.is_admin, days_back=10)
        self.lt.render(region=region)                                       
        pass
    
#    @st.fragment(run_every='3600s')
    def render_summary(self):
    
#        @st.cache_data()
        def get_earings():
            past = 1
            future = 1
            eca = ec.EarningsCalendar(tickers=tickers, past_quarters=past, future_quarters=future)
            eca.run_app()

        mkt = sr.MarketSearch(show_market_only=True, hide_render=True)
#        fts = sr.FullTextSearch(region=sr_right, symbol='^GDAXI', search_ticker_only=True, is_admin=self.is_admin)

        results = mkt.get_index_list()
        pos = 1
        selected_ticker = 'INDEX'
        allow_show_earnings = False
        try:
            pos = results.index(selected_ticker)
        except Exception:
            pass

        sel = st.empty()
        (sel_m, _, sel_e, _, sel_a) = sel.columns([0.4,0.05,0.4,0.05,0.1])
        use_monitored = sel_m.checkbox(t('summary.use_monitored'), True)
        show_earnings = sel_e.checkbox(t('summary.earnings_calendar'), allow_show_earnings)
        show_assets = sel_a.button(t('summary.show_assets'))

        monitored_assets = self.sys_config.get_value("monitored_assets",'')
        if not use_monitored or monitored_assets == "":
            if results:
                selected_ticker = st.selectbox(
                    t('summary.select_market'),
                    results,
                    index=pos
                )
            mkt.get_df(index=selected_ticker,q=7)
            tickers = list(mkt.df['ticker'])
        else:
            tickers = monitored_assets

        if not isinstance(tickers, str):
            tickers.sort()
        ti = st.text_input(t('summary.members'), tickers)
        tickers = eval(ti)

        # Create optimizer and set whether network access is allowed from system config
        optimizer = PortfolioOptimizer(tickers, investment_per_asset=2000)
        try:
            allow_net = self.sys_config.get_value('allow_network', True)
            optimizer.set_allow_network(allow_net)
        except Exception:
            # keep default if sys_config unavailable
            pass
        df_results = optimizer.fetch_and_calculate(period="1mo")
        st.dataframe(df_results)
        tst = """
        optimizer.plot_portfolio()
        st.plotly_chart(
            optimizer.fig,
            use_container_width = True,
            #sharing="streamlit",
            theme="streamlit",
            config = gt.chart_config,
        )
        """

#        for ch in ['[',']',"'",'"']:
#           ti = ti.replace(ch,'')
#        ti = [t.strip().upper() for t in ti.split(",") if t.strip()]
        if show_earnings:
            if "^GDAXI" in tickers:
                allow_show_earnings = False
            with st.spinner(t('summary.wait_earnings'), show_time=True):
                try:       
                    get_earings()
                except Exception as e:
                    print(e)
                    pass
        renderer = ChartsGridRenderer()
        (interval, period, overlays, oszilators) = renderer.get_selectors(self.sys_config)
#        interval = "1d"
#        period = "1mo"
#        overlays = ['pre']
#        oszilators = ['macd']
        if show_assets:
    #        interval = self.sys_config.get_value("interval","30m")
    #        period = self.sys_config.get_value("period","2mo")
    #        overlays = ['mmm','bos','sup','atl']
    #        oszilators = ['ewo','zcr']
            asset_index = '^GDAXI'
            try:
                if not asset_index in tickers:
                    mkt.get_df(index=selected_ticker,q=6)
                    par  = pr.Parity(tickers,asset_index)
                    p_df = par.weights.to_frame(name="weight")
                    p_df = pandas.merge(p_df, mkt.df[['ticker','shortName','close']], left_on='Ticker', right_on='ticker', how='left')#.drop(['Ticker'], axis=1)
                    p_df['shares'] = round(p_df['weight']*1000 / p_df['close'],0)
                    p_df['invest'] = round(p_df['shares']*p_df['close'],0)
                    mtrans = mu.MultiTransactionProcessor(username=self.username, is_admin=self.is_admin)
#                    if not p_df['ticker'].isna().any():
#                        idc_exp = st.expander("Invest")
#                        with idc_exp:
#                            p_df['isin'] = ''
#                            p_df['stockIndex'] = selected_ticker
#                            p_df['currency'] = self.sys_config.get_value("currency",'EUR')
#                            p_df['buyVolume'] = 0
#                            p_df['sellVolume'] = 0
#                            p_df['buyPrice'] = 0
#                            p_df['sellPrice'] = 0
#                            mtrans.overlay_selector(selection=p_df.loc[p_df['ticker']==symbol])

#                            st.dataframe(p_df)
            except Exception as e:
                #st.write(f"Error: {e}")
                pass

            with st.spinner(t('summary.wait_graphs'), show_time=True):
                renderer = ChartsGridRenderer(columns=2)
                renderer.render(
                    tickers=tickers,
                    mkt=mkt,
                    tc=tc,
                    period=period,
                    interval=interval,
                    overlays=overlays,
                    oszilators=oszilators,
                    username=self.username,
                    url=self.url,
                    chart_config=gt.chart_config,
                )

                st.write(t('summary.last_chart_update', ts=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    def show_navigation_links(self):

        self.local_css("style.css")

#        # Logging toggle in the sidebar
#        try:
#            enabled = lgc.is_enabled()
#        except Exception:
#            enabled = False
#        new_state = st.sidebar.checkbox('Enable logging', value=enabled)
#        try:
#            if new_state and not enabled:
#                lgc.enable_logging(to_console=True, level='INFO', logfile='out.txt')
#                self.logger.info('Logging enabled via sidebar')
#            elif not new_state and enabled:
#                lgc.disable_logging()
#                self.logger.info('Logging disabled via sidebar')
#        except Exception:
#            pass

        main_links = {
            t('nav.asset_viewer'): '/',
            t('nav.asset_summary'): '/?summary=true',
            t('nav.performance'): '/?performance=true',
        }
        _links_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'external_links.json')
        try:
            with open(_links_file, encoding='utf-8') as _f:
                external_links = {e['label']: e['url'] for e in json.load(_f)}
        except Exception:
            external_links = {}
        self.sidebar_header = st.sidebar.empty()
        if self.is_admin:
            main_links[t('nav.admin')] = '/?admin=true'
            if STRATEGY_ENGINE_AVAILABLE and has_feature(FEATURE_STRATEGY_ENGINE):
                main_links[t('nav.strategy_finder')] = '/?strategy_finder=true'
                main_links[t('nav.multi_strategies')] = '/?multi=true'
            if PAPER_TRADING_AVAILABLE and has_feature(FEATURE_PAPER_TRADING):
                main_links[t('nav.trading')] = '/?trading=true'
            main_links[t('nav.own_transactions')] = '/?own_trades=true'
        st.sidebar.markdown("""---""")
        st.sidebar.link_button(t('nav.market_map'), '/?marketmap=true')
        st.sidebar.link_button(t('nav.sector_rotation'), '/?rotation=true')
        st.sidebar.markdown("""---""")
        for k, v in main_links.items():
            st.sidebar.link_button(k, v)
        st.sidebar.markdown("""---""")

        _ext_placeholder = t('nav.external_links_placeholder')
        selection = st.sidebar.selectbox(
            t('nav.external_links'),
            [_ext_placeholder] + list(external_links.keys()),
        )

        if selection != _ext_placeholder:
            url = external_links[selection]
            # JS-Injektion zum Öffnen des Links
            #js = f'window.open("{url}", "_blank");'
            js = f'window.parent.window.open("{url}", "_blank");'
            st.components.v1.html(f'<script>{js}</script>', height=0)#        external_link = st.sidebar.selectbox("External links", external_links.keys())

#        for k, v in external_links.items():
#            st.sidebar.link_button(k, v)
        st.sidebar.markdown("""---""")

    
#    @st.fragment(run_every='30s')
#    def update_price(self, region = st):

#        self.lt.load_from_db()
#        price_line = ""
#        symbols = self.lt.symbol_list
#        l_df = self.lt.df.copy()
#        l_df.sort_values(['timestamp'],ascending=[False],inplace=True)
#        for symbol in symbols:
#            df = l_df.loc[l_df['symbol']==symbol].iloc[0]
#            price_line += f"- {symbol} : {df['price']} @ {df['timestamp'][10:]} - "

#        region.write(price_line)
#        self.lt.render()


    def render(self):
        from tradinglib.first_run import maybe_show_setup
        if maybe_show_setup():
            st.stop()

        parms = st.query_params.to_dict()
        if parms.get('stream') == "api":
                data = json.loads(parms.get('data'))
                api_key = data["api_key"]

                if self.lt == None:
                    self.lt = lt.LiveTicker(init=True,username=self.username, is_admin=self.is_admin, days_back=5)
                if dt.datetime.now().strftime("%H:%M:%S") >= "21:59:00":
                        print("\nRunning cleanup...")
                        self.lt.cleanup()

                if api_key == self.sys_config.get_value('api_key',None):
                    st.write(f"Success:")                 
                    for d in data:
                        if not d == "api_key":
                            st.write(d)
                            try:
                                self.lt.add_tick_data(symbol=d,time_str=data[d]['time'], price=data[d]['price'])                            
                                st.write(data[d])
                            except Exception:
                                st.write(t('error.api_write'))
                                pass
        elif parms.get('download'):
            fp.FileProvider()
        else:

            self.login_overlay()

            # i18n init happens here — AFTER login_overlay() so the real username
            # is in session_state and sys_config reads the correct language from config.db.
            _username = st.session_state.get("username", "admin")
            _sc = sysconf.SystemConfig(username=_username, bare_mode=True)
            i18n.init_from_session(sys_config=_sc)

            self.authentication_status = st.session_state.get('authentication_status')
            if not self.authentication_status:
                
                bp.BannerPage()

            else:
                
                self.is_admin= self.config['credentials']['usernames'].get(self.username, {}).get('admin', False)
                self.show_navigation_links()
        
                if self.is_admin and parms.get('admin'):
                    self.set_page_config(t('page.admin'))
                    admin.Admin(scheduler_db=parms.get('scheduler', 'scheduler.db'), authenticator = self.authenticator)
                elif parms.get('live_chart'):
                    self.set_page_config(t('page.live_chart'))
                    self.live_chart(region=st)
                elif parms.get('performance'):
                    self.set_page_config(t('page.performance'))
                    if self.is_admin:
                        tab_perf, tab_all = st.tabs([t('page.performance'), t('nav.all_assets')])
                        with tab_perf:
                            perfdetails.Performance(username=self.username).render()
                        with tab_all:
                            aa.AllAssetsView(username=self.username, is_admin=self.is_admin)
                    else:
                        perfdetails.Performance(username=self.username).render()
                elif parms.get('multi'):
                    self.set_page_config(t('page.strategies'))
                    if mu is None:
                        st.error("Strategy Engine nicht verfügbar — bitte Lizenz prüfen.")
                    else:
                        mu.MultiTransactionProcessor(username=self.username, is_admin=self.is_admin).render()
                elif parms.get('own_trades'):
                    self.set_page_config(t('page.own_transactions'))
                    try:
                        from tradinglib.own_trades_analysis import render_import_export, render_portfolio_analysis, render_trade_entry
                        system_currency = self.sys_config.get_value('system_currency', 'EUR')
                        tab_portfolio, tab_entry, tab_trades = st.tabs([
                            t('own_trades.tab_portfolio'),
                            t('own_trades.tab_entry'),
                            t('own_trades.tab_trades'),
                        ])
                        with tab_portfolio:
                            render_portfolio_analysis(
                                region=tab_portfolio,
                                db_path='database',
                                username=self.username,
                                system_currency=system_currency,
                            )
                        with tab_entry:
                            render_trade_entry(
                                region=tab_entry,
                                db_path='database',
                                system_currency=system_currency,
                            )
                        with tab_trades:
                            # Trade Import/Export ist fuer alle Nutzer verfuegbar.
                            # Falls die Strategy Engine (ass) lizenziert ist, wird
                            # der Simulator mitgegeben fuer erweiterte Funktionen.
                            _simulator = None
                            if ass is not None:
                                try:
                                    _simulator = ass.AssetSimulator("yf_tickers.db", "asset_simulation_.db", "asset_info.db", db_path='database', username=self.username)
                                except Exception:
                                    pass
                            render_import_export(
                                tab_trades,
                                simulator=_simulator,
                                username=self.username,
                                db_path='database',
                            )
                    except Exception as e:
                        st.error(t('error.load_own_trades', error=e))
                elif parms.get('strategy_finder'):
                    self.set_page_config(t('page.finder'))
                    if ass is None:
                        st.error("Strategy Finder nicht verfügbar — bitte Lizenz prüfen.")
                    else:
                        ass.AssetSimulator("yf_tickers.db", "asset_simulation_.db", "asset_info.db", "GDAXI", db_path='database', username=self.username).render(index_filter=1)
                elif parms.get('trading'):
                    self.set_page_config(t('page.trading'))
                    if not PAPER_TRADING_AVAILABLE:
                        st.error("Trading nicht verfügbar — bitte Lizenz prüfen.")
                    else:
                        try:
                            from tradinglib.premium.trading_page import TradingPage
                            TradingPage(username=self.username, db_path='database').render()
                        except Exception as e:
                            st.error(t('error.load_trading', error=e))
                elif parms.get('rotation'):
                    self.set_page_config(t('page.sector_rotation'))
                    try:
                        from tradinglib.sector_rotation_page import SectorRotationPage
                        SectorRotationPage(username=self.username).render()
                    except Exception as e:
                        st.error(t('error.load_rotation', error=e))
                elif parms.get('marketmap'):
                    self.set_page_config(t('page.market_map'))
                    visualizer = mm.DataVisualizer("yf_tickers.db", "asset_simulation_.db", "asset_info.db", "GDAXI", db_path='database', username=self.username)
                    visualizer.render(index_filter=1)
                    self.render_chart(index_columns=visualizer.index_column)
                elif parms.get('summary'):
                    self.set_page_config(t('page.summary'))
                    self.render_summary()
                elif parms.get('option_calc'):
                    self.set_page_config(t('page.option_price'))
                    op.OptionCalculator()
                else:
                    self.set_page_config(t('page.asset_details'))
                    tab_details = False
                    if parms.get('details','false').lower() == 'true':
                        tab_details = True
                    mp.render_mainpage(region=st, symbol=parms.get('symbol', ''), search_ticker_only=True, username=self.username, is_admin=self.is_admin, interval=parms.get('interval', None), period=parms.get('period', None), multi_trends=False, tab_details=tab_details)

                self.authenticator.logout('Logout', 'sidebar')
                try:
                    import jwt as _jwt
                    _raw = st.context.cookies.get(self.config['cookie']['name'])
                    if _raw:
                        _tok = _jwt.decode(_raw, self.config['cookie']['key'], algorithms=['HS256'])
                        _exp = dt.datetime.fromtimestamp(_tok['exp_date'])
                        st.sidebar.write(t('session.expires_at', dt=_exp.strftime('%Y-%m-%d %H:%M')))
                    else:
                        st.sidebar.write(t('session.expires_in', days=self.config['cookie']['expiry_days']))
                except Exception:
                    st.sidebar.write(t('session.expires_in', days=self.config['cookie']['expiry_days']))
                    

if __name__ == "__main__":
    from tradinglib.first_run import ensure_config_yaml
    ensure_config_yaml()
    TradingApp().render()