import os
import re
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
from tradinglib import app_edition as appedition

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


# Theme palettes for the ☀️/🌙 sidebar toggle. Applied at runtime via
# streamlit config.set_option — no server restart, no config.toml rewrite.
_THEMES = {
    'dark': {
        'theme.base': 'dark',
        'theme.primaryColor': '#60a5fa',
        'theme.backgroundColor': '#0f172a',
        'theme.secondaryBackgroundColor': '#1e293b',
        'theme.textColor': '#e2e8f0',
    },
    'light': {
        'theme.base': 'light',
        'theme.primaryColor': '#3b82f6',
        'theme.backgroundColor': '#ffffff',
        'theme.secondaryBackgroundColor': '#eaf2fd',
        'theme.textColor': '#1e293b',
    },
}


def _apply_theme(mode: str) -> None:
    """Set the Streamlit theme options at runtime; takes effect on the next rerun."""
    from streamlit import config as _st_config
    for key, value in _THEMES.get(mode, _THEMES['dark']).items():
        _st_config.set_option(key, value)


# Entry-page routing: maps the user-facing 'start_page' config value (config.db)
# to the query-param route dict the router in TradingApp.render() understands.
# Keys double as the option list of the ⚙-config selectbox.
_START_PAGE_ROUTES = {
    'dashboard':       {'dashboard': 'true'},
    'asset':           {'asset': 'true'},
    'summary':         {'summary': 'true'},
    'performance':     {'performance': 'true'},
    'own_trades':      {'own_trades': 'true'},
    'market_overview': {'market_overview': 'true'},
    'marketmap':       {'marketmap': 'true'},
    'rotation':        {'rotation': 'true'},
}

# Server-side mobile detection via the request User-Agent. Not 100 % (recent
# iPadOS reports as desktop Safari), but dependency-free and good enough to send
# phones to the compact Asset Viewer on first load.
_MOBILE_UA_RE = re.compile(r'Android|iPhone|iPad|iPod|IEMobile|Opera Mini|Windows Phone', re.I)


def _is_mobile_client() -> bool:
    """True when the request User-Agent looks like a phone/tablet browser."""
    try:
        ua = st.context.headers.get('User-Agent', '') or ''
    except Exception:
        ua = ''
    return bool(_MOBILE_UA_RE.search(ua))


def _tab_overlay(label: str) -> str:
    """Full-viewport loading overlay for use with st.empty() outside tab context.
    Must be placed in a placeholder that lives OUTSIDE any `with tab_X:` block so
    that `position:fixed` is resolved against the real viewport, not the tab-panel's
    transform stacking context (which would make the overlay small and mis-positioned).
    """
    return (
        '<style>@keyframes _tt_spin{to{transform:rotate(360deg)}}</style>'
        '<div style="position:fixed;top:0;left:0;right:0;bottom:0;'
        'width:100vw;height:100vh;background:rgba(10,12,20,0.55);'
        'z-index:99999;display:flex;align-items:center;justify-content:center;">'
        '<div style="display:flex;flex-direction:column;align-items:center;'
        'gap:2rem;padding:3rem 4rem;border-radius:1.5rem;'
        'background:rgba(255,255,255,0.08);backdrop-filter:blur(12px);'
        '-webkit-backdrop-filter:blur(12px);">'
        '<div style="width:80px;height:80px;border-radius:50%;flex-shrink:0;'
        'border:6px solid rgba(255,255,255,0.15);border-top-color:#5b9cf6;'
        'animation:_tt_spin 0.85s linear infinite;"></div>'
        f'<p style="color:#dce4ef;font-size:1.4rem;font-weight:500;margin:0;'
        f'letter-spacing:0.04em;">'
        f'<span style="opacity:0.5;font-size:0.8em;font-weight:400;'
        f'letter-spacing:0.1em;text-transform:uppercase;">Loading: </span>'
        f'{label} …</p>'
        '</div></div>'
    )


class PortfolioOptimizer:
    def __init__(self, tickers, investment_per_asset=2000):
        """Set up the optimizer with a ticker list and a per-asset investment budget."""
        self.tickers = tickers
        self.total_budget = len(tickers) * investment_per_asset
        self.results = None
        # When allow_network is False we use only local sqlite data (FetchData)
        # When True we allow network fallbacks via the centralized market_data wrapper
        self.allow_network = True

    def set_allow_network(self, allow: bool):
        """Enable or disable remote yfinance fallback when local data is unavailable."""
        self.allow_network = bool(allow)

    def fetch_and_calculate(self, period="1y"):
        """Download close prices for all tickers and compute inverse-volatility weights.

        Prefers local SQLite data via FetchData; falls back to yfinance when allow_network=True.
        Returns a DataFrame with Ticker, Gewichtung (%), and Anlagebetrag (€) columns.
        """
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
        """Build a Plotly donut chart from self.results and store it in self.fig."""
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
        """Configure Streamlit page layout, load config.yaml, and initialize the authenticator."""
        self.title = "Silvesto, your portal for growth."
        if 'title' not in st.session_state:
            st.set_page_config(self.title, initial_sidebar_state='collapsed', layout="wide" )
        else:
            st.set_page_config(st.session_state.title, initial_sidebar_state='collapsed',layout="wide" )
        self.authentication_status = None
        self.is_admin = False
        self.config = self.load_config()
        self.authenticator = self.init_authenticator()
        self.msg_text = st.empty()
        self.markdown_main() 
        self.lt = None
        self.logger = logging.getLogger(__name__)

    def markdown_main(self):
        """Inject global CSS overrides (transparent header, reduced top padding)."""
        st.markdown(
            """
            <style>
                    .stAppHeader {
                        background-color: rgba(255, 255, 255, 0.0);  /* Transparent background */
                        visibility: visible;  /* Ensure the header is visible */
                    }

                   .block-container {
                        padding-top: 0.5rem !important;
                    }
                    /* Pull sidebar logo to top — remove Streamlit's default gap */
                    section[data-testid="stSidebar"] > div:first-child {
                        padding-top: 0 !important;
                    }
                    [data-testid="stSidebarContent"] {
                        padding-top: 0 !important;
                    }
                    [data-testid="stSidebarUserContent"] {
                        padding-top: 0.75rem !important;
                    }

                    /* Large full-screen spinner overlay */
                    @keyframes tt-spin {
                        to { transform: rotate(360deg); }
                    }
                    div[data-testid="stSpinner"] {
                        position: fixed !important;
                        top: 0 !important;
                        left: 0 !important;
                        right: 0 !important;
                        bottom: 0 !important;
                        width: 100vw !important;
                        height: 100vh !important;
                        margin: 0 !important;
                        background: rgba(10, 12, 20, 0.55) !important;
                        z-index: 99999 !important;
                        display: flex !important;
                        align-items: center !important;
                        justify-content: center !important;
                    }
                    div[data-testid="stSpinner"] > div {
                        display: flex !important;
                        flex-direction: column !important;
                        align-items: center !important;
                        gap: 2rem !important;
                        padding: 3rem 4rem !important;
                        border-radius: 1.5rem !important;
                        background: rgba(255, 255, 255, 0.08) !important;
                        backdrop-filter: blur(12px) !important;
                        -webkit-backdrop-filter: blur(12px) !important;
                    }
                    div[data-testid="stSpinner"] svg {
                        display: none !important;
                    }
                    div[data-testid="stSpinner"] > div::before {
                        content: '' !important;
                        display: block !important;
                        width: 80px !important;
                        height: 80px !important;
                        border-radius: 50% !important;
                        border: 6px solid rgba(255, 255, 255, 0.15) !important;
                        border-top-color: #5b9cf6 !important;
                        animation: tt-spin 0.85s linear infinite !important;
                        flex-shrink: 0 !important;
                    }
                    div[data-testid="stSpinner"] p {
                        color: #dce4ef !important;
                        font-size: 1.4rem !important;
                        font-weight: 500 !important;
                        margin: 0 !important;
                        letter-spacing: 0.04em !important;
                    }
                    div[data-testid="stSpinner"] p::before {
                        content: 'Loading: ' !important;
                        opacity: 0.5 !important;
                        font-size: 0.8em !important;
                        font-weight: 400 !important;
                        letter-spacing: 0.1em !important;
                        text-transform: uppercase !important;
                    }
            </style>
            """,
            unsafe_allow_html=True,
        )
        
    def load_config(self):
        """Parse config.yaml and return the configuration dict."""
        with open(ts.Tools().get_path('', self.config_file)) as file:
            config = yaml.load(file, Loader=SafeLoader)
        self._warn_insecure_defaults(config)
        return config

    def _warn_insecure_defaults(self, config):
        """Log a loud warning if config.yaml still uses the placeholder cookie key.

        Older installs were created with a fixed cookie signing key
        (`trading_app_secret_key_change_me`) baked into the public repo. Anyone
        who knows it can forge valid auth JWTs, so an unchanged key is a full
        auth bypass on a network-reachable deployment.
        """
        try:
            cookie_key = config.get('cookie', {}).get('key', '')
        except AttributeError:
            return
        if cookie_key == 'trading_app_secret_key_change_me':
            logging.getLogger(__name__).warning(
                "SECURITY: config.yaml uses the default cookie signing key — "
                "anyone with the public repo can forge admin sessions. "
                "Replace cookie.key with a random value, e.g. "
                "python -c \"import secrets; print(secrets.token_hex(32))\""
            )

    def local_css(self, file_name):
        """Inject a local CSS file into the Streamlit app via st.markdown."""
        with open(file_name) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

    def init_authenticator(self):
        """Create and return a streamlit-authenticator Authenticate instance from config.yaml."""
        return Authenticate(
            ts.Tools().get_path('', self.config_file),
            self.config['cookie']['name'],
            self.config['cookie']['key'],
            self.config['cookie']['expiry_days'],
        )
    
    def init_session(self):
        """Read username, auth status, and admin flag from session_state and build SystemConfig."""
        self.username = st.session_state.get("username", 'api_key')
        self.authentication_status = st.session_state.get("authentication_status", None)
        self.is_admin = st.session_state.get("is_admin", False)
        self.sys_config = sysconf.SystemConfig(username=self.username, is_admin=self.is_admin)
#        if self.rt_prices and sys.platform == 'win32':
    
    def set_page_title(self, title=None, icon=None):
        """Update the sidebar header with the active page title."""
        if title:
            st.session_state['title'] = title
        if icon:
            st.session_state['icon'] = icon
        if getattr(self, 'sidebar_header', None) is not None:
            self.sidebar_header.markdown(f"""<h2>{t('page.selected_view', title=title)}</h2>""", unsafe_allow_html=True)
#        self.sidebar_header.write(f"{title}\n")    
        
    def set_page_config(self, title):
        """Attempt to set the Streamlit page config and update the sidebar header title."""
        try:
            st.set_page_config(title, layout="wide", initial_sidebar_state='collapsed')
        except Exception:
            pass
        self.set_page_title(title)

        
    def login_overlay(self):
        """Process the session cookie without rendering any UI widget.

        The visible login widget is rendered by BannerPage / WelcomePage in the
        header row (next to the language selector).
        """
        try:
            self.authenticator.login(location='unrendered')
        except Exception as exc:
            self.logger.warning("Login (unrendered) error: %s", exc)
        self.init_session()
        self.sys_config = sysconf.SystemConfig(username=self.username, is_admin=self.is_admin)

    # get_selectors moved to tradinglib.tiny_chart_grid.ChartsGridRenderer

    @st.fragment(run_every='300s')
    def render_chart(self, index_columns, region=st):
        """Render a tiny trend chart for index_columns, auto-refreshing every 5 minutes."""
        if not index_columns == 'ANY':

                renderer = ChartsGridRenderer()
                (interval, period, overlays, oszilators) = renderer.get_selectors(self.sys_config)

                # Overlays/Oszillatoren, die der User auf "nur berechnen, nicht
                # zeichnen" gestellt hat (⚙-Dialog → overlay_no_plot /
                # oszilator_no_plot), dürfen — wie im Asset Viewer — nicht geplottet
                # werden. Ohne das zeigt die Market-Map-Grafik mehr Linien
                # (z.B. Trend-/Regressionslinien) als die Detailseite für denselben
                # Ticker. Mirrors main_page.py's hide_details path.
                def _no_plot_set(conf_key):
                    raw = self.sys_config.get_value(conf_key, [])
                    return {str(n).lower() for n in raw} if isinstance(raw, list) else set()
                _ov_no_plot = _no_plot_set('overlay_no_plot')
                _oz_no_plot = _no_plot_set('oszilator_no_plot')
                no_plot_overlays = [n for n in overlays if str(n).lower() in _ov_no_plot]
                no_plot_oszilators = [n for n in oszilators if str(n).lower() in _oz_no_plot]

                # username durchreichen, sonst liest tiny_chart die Indikator-Params
                # (z.B. markov forecast_pos/show_matrix) aus der leeren Default-Config
                # statt aus der User-Config → Forecast oben-rechts + Matrix sichtbar.
                region.plotly_chart(tc.tiny_chart( f'{index_columns}',f' {interval}/{period} trend',period,interval,True, True,range_breaks=True,add_sub_plots=oszilators, add_overlays=overlays,no_plot_overlays=no_plot_overlays,no_plot_oszilators=no_plot_oszilators,username=self.username,url=f"{self.url}").fig,
                    use_container_width = True,
                    theme="streamlit",
                    config = gt.chart_config,
                )
                st.write(f'Last page refresh: {dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    
    @st.fragment(run_every='120s')
    def live_chart(self, region=st):
        """Render the live ticker strip, auto-refreshing every 2 minutes."""
        if self.lt == None:
            
            self.lt = lt.LiveTicker(init=True,username=self.username, is_admin=self.is_admin, days_back=10)
        self.lt.render(region=region)                                       
        pass
    
    def render_summary(self):
        """Render the market summary overview. Regime Flow is a separate sidebar entry."""
        self._render_summary_overview()

    def _render_summary_overview(self):
        """Original summary content: portfolio optimizer, optional earnings, chart grid."""
        def get_earings():
            past = 1
            future = 1
            eca = ec.EarningsCalendar(tickers=tickers, past_quarters=past, future_quarters=future)
            eca.run_app()

        mkt = sr.MarketSearch(show_market_only=True, hide_render=True)

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
            tickers.sort()
            default_tickers = ", ".join(tickers)
        else:
            default_tickers = monitored_assets

        ti = st.text_input(t('summary.members'), default_tickers)
        tickers = [tck.strip() for tck in ti.split(',') if tck.strip()]

        if not tickers:
            st.warning(t('summary.empty_group'))
            return

        # Create optimizer and set whether network access is allowed from system config
        optimizer = PortfolioOptimizer(tickers, investment_per_asset=2000)
        try:
            allow_net = self.sys_config.get_value('allow_network', True)
            optimizer.set_allow_network(allow_net)
        except Exception:
            # keep default if sys_config unavailable
            pass
        try:
            df_results = optimizer.fetch_and_calculate(period="1mo")
            st.dataframe(df_results)
        except ValueError:
            st.warning(t('summary.no_price_data'))
        tst = """
        optimizer.plot_portfolio()
        st.plotly_chart(
            optimizer.fig,
            use_container_width = True,
            theme="streamlit",
            config = gt.chart_config,
        )
        """

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

        # Batch HTML-Reports für die (Monitored-)Assets — sequenziell, resumebar
        # nach AI-Limits. Nur mit strategy_engine-Lizenz (wie der Einzel-Asset-KI-Tab).
        try:
            from tradinglib import batch_reports as br
            br.render_batch_report_ui(
                st, tickers, self.sys_config, self.username,
                interval, period, overlays, oszilators,
            )
        except Exception as _e:
            logging.getLogger(__name__).debug("batch report UI failed: %s", _e, exc_info=True)

        if show_assets:
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

            except Exception as e:
                pass

            _spin = st.empty()
            _total = len(tickers)

            def _graphs_progress(done, total):
                _spin.markdown(
                    _tab_overlay(f"{t('summary.wait_graphs')} ({done}/{total})"),
                    unsafe_allow_html=True,
                )

            _graphs_progress(0, _total)
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
                progress_callback=_graphs_progress,
            )
            _spin.empty()

            st.write(t('summary.last_chart_update', ts=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    def show_navigation_links(self):
        """Build the sidebar navigation with grouped expanders and same-tab button navigation."""
        self.local_css("style.css")

        _links_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'external_links.json')
        try:
            with open(_links_file, encoding='utf-8') as _f:
                external_links = {e['label']: e['url'] for e in json.load(_f)}
        except Exception:
            external_links = {}

        st.sidebar.markdown(
            '<style>'
            '[data-testid="stSidebar"] hr{margin:1rem 0;}'
            '[data-testid="stSidebar"] h2{margin:0.1rem 0;}'
            '[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:0.35rem;}'
            '[data-testid="stSidebar"] div.stButton{margin:0;}'
            '</style>',
            unsafe_allow_html=True,
        )

        # 'Selected view: …' header + divider — hidden by default, opt-in via
        # config.db key 'show_view_header'
        _show_hdr = str(self.sys_config.get_value('show_view_header', False)).lower() in ('true', '1', 'yes', 'on')
        if _show_hdr:
            self.sidebar_header = st.sidebar.empty()
            st.sidebar.markdown("---")
        else:
            self.sidebar_header = None

        bp.render_logo(region=st.sidebar, max_width="85%", margin_bottom="1.3rem")
        st.sidebar.markdown("---")

        # ── Settings & Help ─────────────────────────────────────────────────
        # Moved here from the top search row (main_page.py): both open an
        # @st.dialog overlay, so the trigger button can live anywhere. Icon-only
        # side by side; the localized label is the hover tooltip (help=).

        # ── Theme toggle ─────────────────────────────────────────────────────
        _theme_mode = self.sys_config.get_value('theme_mode', 'dark')
        _is_dark = _theme_mode != 'light'
        # Re-apply on every run so the saved preference survives server restarts
        _apply_theme(_theme_mode if _theme_mode in ('dark', 'light') else 'dark')

        # Order (left→right): Home · Help · Theme · Settings
        _dash_col, _help_col, _theme_col, _cfg_col = st.sidebar.columns(4, gap="small")
        if _cfg_col.button("⚙", use_container_width=True, key='_nav_settings', help=t('nav.settings')):
            self.sys_config.render()
        if _help_col.button("❓", use_container_width=True, key='_nav_help', help=t('nav.help')):
            self.sys_config.render_help()
        if _dash_col.button("🏠", use_container_width=True, key='_nav_dashboard', help=t('nav.dashboard')):
            st.session_state['_nav_params'] = {'dashboard': 'true'}
            st.rerun()
        _theme_icon = "☀️" if _is_dark else "🌙"
        _theme_help = "Zu Light Mode wechseln" if _is_dark else "Zu Dark Mode wechseln"
        if _theme_col.button(_theme_icon, use_container_width=True, key='_nav_theme', help=_theme_help):
            _new_mode = 'light' if _is_dark else 'dark'
            self.sys_config.set_value('theme_mode', _new_mode)
            _apply_theme(_new_mode)
            st.rerun()

        def _nav(label, locked=False, **params):
            """Navigate within the same tab in one click via session state.

            Sets '_nav_params' in session_state and calls st.rerun() so the routing
            in render() picks up the new params immediately — no query_params race
            condition, no browser reload, no new tab.
            Must be called inside a with-block (expander/container).

            locked=True: prefixes the label with 🔒 (Scalable-Edition Upgrade-Feature).
            Beim Klick navigiert es regulär — der Edition-Guard fängt die gesperrte
            Route ab und zeigt die Upgrade-Seite.
            """
            _key = f'_nav_{"_".join(f"{k}{v}" for k, v in sorted(params.items())) or "home"}'
            disp = f'🔒 {label}' if locked else label
            if st.button(disp, use_container_width=True, key=_key):
                st.session_state['_nav_params'] = params
                st.rerun()
        st.sidebar.markdown("---")

        if appedition.IS_SCALABLE:
            # ── Scalable-Edition: schlanke Navigation ───────────────────────
            # Frei: Own Transactions (Einstieg), Asset-Ansicht, Markt-Features,
            # Lump Sum. Upgrade-Features werden mit 🔒 gezeigt (Klick → Upgrade-Seite).
            with st.sidebar.expander(t('nav.group_portfolio'), expanded=True):
                _nav(t('nav.own_transactions'), own_trades='true')

            with st.sidebar.expander(t('nav.group_assets'), expanded=False):
                _nav(t('nav.asset_viewer'), asset='true')
                _nav(t('nav.compound_simulation'), compound='true')
                _nav(t('nav.performance'), locked=True, performance='true')
                _nav(t('nav.asset_summary'), locked=True, summary='true')

            with st.sidebar.expander(t('nav.group_market'), expanded=False):
                _nav(t('nav.market_overview'), market_overview='true')
                _nav(t('nav.correlation'), correlation='true')
                _nav(t('nav.sector_rotation'), rotation='true')
                _nav(t('nav.market_map'), marketmap='true')

            with st.sidebar.expander(t('nav.group_upgrade'), expanded=False):
                if STRATEGY_ENGINE_AVAILABLE:
                    _nav(t('nav.strategy_finder'), locked=True, strategy_finder='true')
                    _nav(t('nav.multi_strategies'), locked=True, multi='true')
                if PAPER_TRADING_AVAILABLE:
                    _nav(t('nav.trading'), locked=True, trading='true')

            st.sidebar.markdown("---")
            if st.sidebar.button(t('upgrade.cta_button'), use_container_width=True,
                                 type='primary', key='_nav_upgrade_cta'):
                st.session_state['_nav_params'] = {'upgrade': 'true'}
                st.rerun()
        else:
            # Sidebar visibility per item — configurable via Admin → System →
            # "Sidebar menu" (see system_config.DEFAULT_SIDEBAR_ITEMS for the
            # factory defaults). Global setting, shared by all users.
            _sb_items = sysconf.SystemConfig.sidebar_menu_config().get_sidebar_items()

            # ── Markt ──────────────────────────────────────────────────────────
            if any(_sb_items.get(k) for k in ('marketmap', 'rotation', 'market_overview', 'correlation')):
                with st.sidebar.expander(t('nav.group_market'), expanded=False):
                    if _sb_items.get('marketmap'):
                        _nav(t('nav.market_map'), marketmap='true')
                    if _sb_items.get('rotation'):
                        _nav(t('nav.sector_rotation'), rotation='true')
                    if _sb_items.get('market_overview'):
                        _nav(t('nav.market_overview'), market_overview='true')
                    if _sb_items.get('correlation'):
                        _nav(t('nav.correlation'), correlation='true')

            # ── Assets & Performance ────────────────────────────────────────────
            if any(_sb_items.get(k) for k in ('asset', 'summary', 'performance', 'compound')):
                with st.sidebar.expander(t('nav.group_assets'), expanded=True):
                    if _sb_items.get('asset'):
                        _nav(t('nav.asset_viewer'), asset='true')
                    if _sb_items.get('summary'):
                        _nav(t('nav.asset_summary'), summary='true')
                        _nav(t('nav.summary_tab_flow'), summary='true', tab='flow')
                    if _sb_items.get('performance'):
                        _nav(t('nav.performance'), performance='true')
                        if self.is_admin:
                            _nav(t('nav.perf_tab_all'), performance='true', tab='all_assets')
                            _nav(t('nav.perf_tab_etp'), performance='true', tab='etp_assets')
                    if _sb_items.get('compound'):
                        _nav(t('nav.compound_simulation'), compound='true')

            # ── Portfolio ───────────────────────────────────────────────────────
            if self.is_admin and any(_sb_items.get(k) for k in ('strategy_finder', 'multi', 'trading', 'own_trades')):
                with st.sidebar.expander(t('nav.group_portfolio'), expanded=False):
                    if _sb_items.get('strategy_finder') and STRATEGY_ENGINE_AVAILABLE and has_feature(FEATURE_STRATEGY_ENGINE):
                        _nav(t('nav.strategy_finder'), strategy_finder='true')
                    if _sb_items.get('multi') and STRATEGY_ENGINE_AVAILABLE and has_feature(FEATURE_STRATEGY_ENGINE):
                        _nav(t('nav.multi_strategies'), multi='true')
                    if _sb_items.get('trading') and PAPER_TRADING_AVAILABLE and has_feature(FEATURE_PAPER_TRADING):
                        _nav(t('nav.trading'), trading='true')
                    if _sb_items.get('own_trades'):
                        _nav(t('nav.own_transactions'), own_trades='true')

            # ── Admin (always last) ─────────────────────────────────────────────
            if self.is_admin and any(_sb_items.get(k) for k in ('admin_ticker', 'admin_database', 'admin_credentials', 'admin_system', 'admin_scheduler', 'admin_pine')):
                st.sidebar.markdown("---")
                with st.sidebar.expander(t('nav.group_admin'), expanded=False):
                    if _sb_items.get('admin_ticker'):
                        _nav(t('nav.admin_ticker'),      admin='true', section='ticker')
                    if _sb_items.get('admin_database'):
                        _nav(t('nav.admin_database'),    admin='true', section='database')
                    if _sb_items.get('admin_credentials'):
                        _nav(t('nav.admin_credentials'), admin='true', section='credentials')
                    if _sb_items.get('admin_system'):
                        _nav(t('nav.admin_system'),      admin='true', section='system')
                    if _sb_items.get('admin_scheduler'):
                        _nav(t('nav.admin_scheduler'),   admin='true', section='scheduler')
                    if _sb_items.get('admin_pine'):
                        _nav(t('nav.admin_pine'),        admin='true', section='pine')

        # ── External links ──────────────────────────────────────────────────
        st.sidebar.markdown("---")
        _ext_placeholder = t('nav.external_links_placeholder')
        selection = st.sidebar.selectbox(
            t('nav.external_links'),
            [_ext_placeholder] + list(external_links.keys()),
        )
        if selection != _ext_placeholder:
            url = external_links[selection]
            js = f'window.parent.window.open("{url}", "_blank");'
            st.components.v1.html(f'<script>{js}</script>', height=0)

        st.sidebar.markdown("---")

    
#    @st.fragment(run_every='30s')
#    def update_price(self, region = st):

#        symbols = self.lt.symbol_list
#        for symbol in symbols:
#            price_line += f"- {symbol} : {df['price']} @ {df['timestamp'][10:]} - "

#        self.lt.render()


    def _resolve_entry_route(self):
        """Pick the entry page for a fresh load without an explicit route.

        Mobile clients always land on the Asset Viewer (compact, touch-first);
        otherwise the user-configured 'start_page' (config.db) decides, falling
        back to the Dashboard. Requires self.sys_config, so call only after login.
        """
        if _is_mobile_client():
            return {'asset': 'true'}
        start = str(self.sys_config.get_value('start_page', 'dashboard')).strip()
        return dict(_START_PAGE_ROUTES.get(start, {'dashboard': 'true'}))

    def render(self):
        """Route the request to the correct page based on URL query parameters.

        Handles login, all main pages (admin, performance, strategies, own trades,
        sector rotation, market map, summary, …), and the API stream endpoint.
        """
        from tradinglib.first_run import maybe_show_setup
        if maybe_show_setup():
            st.stop()

        # Session state takes priority over URL params (set by _nav() sidebar buttons).
        # _nav_params is NOT popped — it persists across widget-triggered reruns so
        # the current page stays active while the user interacts with dropdowns etc.
        # A new sidebar click overwrites it; a fresh page load initialises from URL.
        if '_nav_params' in st.session_state:
            parms = st.session_state['_nav_params']
        else:
            parms = st.query_params.to_dict()
            # Scalable-Edition: frischer Aufruf ohne explizite Route → Einstieg
            # auf "Own Transactions". (Wählt der Nutzer später die Asset-Ansicht,
            # ist _nav_params bereits gesetzt und dieser Zweig greift nicht mehr.)
            if appedition.IS_SCALABLE and not parms:
                parms = {'own_trades': 'true'}
            # Nicht-Scalable + leere Route: die Einstiegsseite wird erst NACH dem
            # Login aufgelöst (_resolve_entry_route braucht self.sys_config für
            # die konfigurierbare 'start_page' + Mobile-Erkennung). Ein leeres
            # Dict hier NICHT persistieren, sonst läuft der Resolver nie.
            if parms:
                st.session_state['_nav_params'] = parms
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

            with st.spinner("Silvesto, your portal for growth."):
                self.login_overlay()

                # i18n init happens here — AFTER login_overlay() so the real username
                # is in session_state and sys_config reads the correct language from config.db.
                _username = st.session_state.get("username", "admin")
                _sc = sysconf.SystemConfig(username=_username, bare_mode=True)
                i18n.init_from_session(sys_config=_sc)

            self.authentication_status = st.session_state.get('authentication_status')
            if not self.authentication_status:
                from tradinglib.first_run import welcome_shown, mark_welcome_shown
                if not welcome_shown():
                    mark_welcome_shown()
                    bp.WelcomePage(authenticator=self.authenticator)
                else:
                    bp.BannerPage(authenticator=self.authenticator)

            else:
                
                self.is_admin= self.config['credentials']['usernames'].get(self.username, {}).get('admin', False)
                # session_state['is_admin'] is read by init_session()/login_overlay() on
                # every rerun to build self.sys_config — nothing else ever wrote it, so it
                # was always False there (this is the first point the real flag is known).
                # Persist it and sync the already-built sys_config so the ⚙ dialog's
                # admin-only section (logging, license, …) actually renders.
                st.session_state['is_admin'] = self.is_admin
                self.sys_config.is_admin = self.is_admin
                self.show_navigation_links()

                # Fresh load without an explicit route: resolve the entry page now
                # that self.sys_config exists (mobile → Asset Viewer, else the
                # configured 'start_page'). Persist it so the choice sticks across
                # reruns; a later sidebar click overwrites _nav_params as usual.
                if not parms:
                    parms = self._resolve_entry_route()
                    st.session_state['_nav_params'] = parms

                # Edition-Guard: In der Scalable-Edition sind nur ausgewählte Routen
                # frei; alle übrigen Features sind ein Upgrade. In der 'full'-Edition
                # (Default) ist dies ein No-Op — jede Route ist erlaubt.
                _route = appedition.active_route(parms)
                if not appedition.is_route_allowed(_route):
                    self.set_page_config(t('page.upgrade'))
                    from tradinglib.upgrade_page import render_upgrade_teaser
                    render_upgrade_teaser(region=st, route=_route, username=self.username)
                    self.authenticator.logout('Logout', 'sidebar')
                    st.stop()

                if self.is_admin and parms.get('admin'):
                    self.set_page_config(t('page.admin'))
                    with st.spinner(t('page.admin') + " …"):
                        admin.Admin(username=self.username, scheduler_db=parms.get('scheduler', 'scheduler.db'), authenticator=self.authenticator, section=parms.get('section'))
                elif parms.get('live_chart'):
                    self.set_page_config(t('page.live_chart'))
                    with st.spinner(t('page.live_chart') + " …"):
                        self.live_chart(region=st)
                elif parms.get('performance'):
                    self.set_page_config(t('page.performance'))
                    with st.spinner(t('page.performance') + " …"):
                        if self.is_admin and parms.get('tab') == 'all_assets':
                            aa.AllAssetsView(username=self.username, is_admin=self.is_admin)
                        elif self.is_admin and parms.get('tab') == 'etp_assets':
                            aa.AllAssetsView(username=self.username, is_admin=self.is_admin,
                                             db_name='asset_simulation_etp')
                        else:
                            perfdetails.Performance(username=self.username).render()
                elif parms.get('multi'):
                    self.set_page_config(t('page.strategies'))
                    with st.spinner(t('page.strategies') + " …"):
                        if mu is None:
                            st.error("Strategy Engine nicht verfügbar — bitte Lizenz prüfen.")
                        else:
                            mu.MultiTransactionProcessor(username=self.username, is_admin=self.is_admin).render()
                elif parms.get('own_trades'):
                    self.set_page_config(t('page.own_transactions'))
                    try:
                        from tradinglib.own_trades_analysis import (
                            render_import_export, render_portfolio_analysis,
                            render_trade_entry, render_scalable_import,
                            render_risk_management,
                        )
                        system_currency = self.sys_config.get_value('system_currency', 'EUR')
                        with st.spinner(t('page.own_transactions') + " …"):
                            tab_scalable, tab_portfolio, tab_entry, tab_risk, tab_trades = st.tabs([
                                t('own_trades.tab_scalable'),
                                t('own_trades.tab_portfolio'),
                                t('own_trades.tab_entry'),
                                t('own_trades.tab_risk'),
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
                            with tab_risk:
                                render_risk_management(
                                    region=tab_risk,
                                    db_path='database',
                                    system_currency=system_currency,
                                )
                            with tab_scalable:
                                render_scalable_import(
                                    region=tab_scalable,
                                    db_path='database',
                                    system_currency=system_currency,
                                    username=self.username,
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
                    with st.spinner(t('page.finder') + " …"):
                        if ass is None:
                            st.error("Strategy Finder nicht verfügbar — bitte Lizenz prüfen.")
                        else:
                            ass.AssetSimulator("yf_tickers.db", "asset_simulation_.db", "asset_info.db", "GDAXI", db_path='database', username=self.username).render(index_filter=1)
                elif parms.get('trading'):
                    self.set_page_config(t('page.trading'))
                    with st.spinner(t('page.trading') + " …"):
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
                    with st.spinner(t('page.sector_rotation') + " …"):
                        try:
                            from tradinglib.sector_rotation_page import SectorRotationPage
                            SectorRotationPage(username=self.username).render()
                        except Exception as e:
                            st.error(t('error.load_rotation', error=e))
                elif parms.get('market_overview'):
                    self.set_page_config(t('page.market_overview'))
                    with st.spinner(t('page.market_overview') + " …"):
                        try:
                            from tradinglib.market_overview_page import MarketOverviewPage
                            MarketOverviewPage(username=self.username).render()
                        except Exception as e:
                            st.error(t('error.load_market_overview', error=e))
                elif parms.get('correlation'):
                    self.set_page_config(t('page.correlation'))
                    with st.spinner(t('page.correlation') + " …"):
                        try:
                            from tradinglib.correlation_index_page import CorrelationIndexPage
                            CorrelationIndexPage(username=self.username).render()
                        except Exception as e:
                            st.error(t('error.load_correlation', error=e))
                elif parms.get('compound'):
                    self.set_page_config(t('page.compound_simulation'))
                    with st.spinner(t('page.compound_simulation') + " …"):
                        try:
                            from tradinglib.compound_simulation import render_compound_simulation
                            render_compound_simulation()
                        except Exception as e:
                            st.error(t('error.load_compound_sim', error=e))
                elif parms.get('marketmap'):
                    self.set_page_config(t('page.market_map'))
                    with st.spinner(t('page.market_map') + " …"):
                        visualizer = mm.DataVisualizer("yf_tickers.db", "asset_simulation_.db", "asset_info.db", "GDAXI", db_path='database', username=self.username)
                        visualizer.render(index_filter=1)
                    self.render_chart(index_columns=visualizer.index_column)
                elif parms.get('summary'):
                    self.set_page_config(t('page.summary'))
                    with st.spinner(t('page.summary') + " …"):
                        if parms.get('tab') == 'flow':
                            try:
                                from tradinglib.regime_flow_page import RegimeFlowPage
                                RegimeFlowPage(username=self.username, sys_config=self.sys_config).render()
                            except Exception as _rfe:
                                st.error(f"Regime Flow: {_rfe}")
                        else:
                            self.render_summary()
                elif parms.get('option_calc'):
                    self.set_page_config(t('page.option_price'))
                    with st.spinner(t('page.option_price') + " …"):
                        op.OptionCalculator()
                elif parms.get('dashboard') or not parms:
                    self.set_page_config(t('page.dashboard'))
                    bp.BannerPage(username=self.username, dashboard_mode=True)
                else:
                    self.set_page_config(t('page.asset_details'))
                    tab_details = False
                    if parms.get('details','false').lower() == 'true':
                        tab_details = True
                    with st.spinner(t('page.asset_details') + " …"):
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
