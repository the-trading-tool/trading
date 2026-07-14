from tradinglib import ( tiny_chart as tc, search as sr,
        sentiment as se, headlines as hl, multi_select as ms, fetch_data,
        system_config as sysconf, graph_tools as gt, tools as ts)
from tradinglib.indicator import indicator  # Die Basisklasse importieren
from tradinglib.i18n import t, current_language
from tradinglib.premium_availability import PAPER_TRADING_AVAILABLE, SEASONALITY_AVAILABLE
from tradinglib.license_manager import has_feature, FEATURE_SEASONALITY
import streamlit as st
import streamlit_nested_layout
import datetime as dt
import json
import sqlite3
import pandas as pd
import logging

# Premium module — only imported when the file is present
sn = None
if SEASONALITY_AVAILABLE:
    try:
        from tradinglib.premium import seasonality as sn
    except Exception as _e:
        logging.getLogger(__name__).warning("Premium seasonality module could not be loaded: %s", _e)

logger = logging.getLogger(__name__)


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


@st.fragment
def _news_tab_fragment(ticker_selected: str) -> None:
    """Render news sentiment; isolated as a fragment so overlay/oscillator changes don't re-fetch."""
    sentiment = se.YahooNewsSentiment(ticker_selected)
    sentiment.render()


#### Main
class render_mainpage(fetch_data.FetchData):

    symbol = ''

    def __init__(self, symbol='', region=st, search_ticker_only=False, hide_search=False, hide_details=False, username='', is_admin=False, interval=None, period=None, multi_trends=False, tab_details=False):
        """Initialize and immediately render the asset detail page for the given symbol."""
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
        # Track whether symbol came explicitly from a URL parameter (not last_ticker).
        # auto_resolve() must only run for explicit URL symbols, never for last_ticker —
        # otherwise it blocks the interactive market-search widget every rerun.
        self._symbol_from_url = bool(symbol)
        if not self.symbol:
            # Start view: the configured default ticker (⚙ dialog, key
            # 'default_ticker', default ^GDAXI) takes priority so that a deliberately
            # set default is actually applied on re-open. Only when no default is
            # configured do we fall back to the last selected ticker, so the chart
            # never starts empty.
            self.symbol = (self.sys_conf.get_value('default_ticker', '')
                           or self.sys_conf.get_value('last_ticker', '')
                           or '^GDAXI')
            self.ticker = self.symbol
        self.overlays=['heikin','candle','atc']
        self.oszilators=['ewo','zcr']
        self.no_plot_overlays=[]
        self.no_plot_oszilators=[]
        self.render()
    
    def get_item(self, data, name, col, select):
        """Extract a scalar value from a DataFrame by filtering on col==select and reading name."""
        item = ''
        try:
            item = data.loc[data[col]==select][name].item()
        except Exception:
            pass
        return item

    def _get_isin(self, ticker: str) -> str:
        """Look up the ISIN for ticker from the stocks table in yf_tickers.db."""
        try:
            db_file = ts.Tools().get_path('database', 'yf_tickers.db')
            with sqlite3.connect(db_file) as conn:
                row = conn.execute("SELECT ISIN FROM stocks WHERE Ticker = ?", (ticker,)).fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
        return ''

    def _inject_justetf_auto_open(self, ticker: str, url: str, tab_label: str) -> None:
        """Open justETF in a new tab on the first click on the Info tab per ticker/session.

        Best-effort DOM hack (like the external-links opener in asset_analyzer.py):
        finds the Streamlit tab button by its visible label and binds a single
        persistent click listener that reads the current ticker/URL from a shared
        state object (updated every rerun). A sessionStorage flag per ticker
        prevents repeat clicks from opening more tabs. If Streamlit's internal DOM
        ever changes, this silently does nothing -- the link_button stays as
        fallback.
        """
        storage_key = f"justetf_opened_{ticker}"
        js = f"""
        (function() {{
            var TAB_LABEL = {json.dumps(tab_label)};
            var STATE = {{url: {json.dumps(url)}, key: {json.dumps(storage_key)}}};
            window.parent.__justetfState = STATE;

            function attach() {{
                var doc = window.parent.document;
                var btns = doc.querySelectorAll('button[data-baseweb="tab"]');
                for (var i = 0; i < btns.length; i++) {{
                    var b = btns[i];
                    if (b.textContent.trim() === TAB_LABEL && !b.dataset.justetfBound) {{
                        b.dataset.justetfBound = "1";
                        b.addEventListener("click", function() {{
                            var s = window.parent.__justetfState;
                            if (s && s.url && !window.parent.sessionStorage.getItem(s.key)) {{
                                window.parent.sessionStorage.setItem(s.key, "1");
                                window.parent.window.open(s.url, "_blank");
                            }}
                        }});
                    }}
                }}
            }}
            attach();
            setTimeout(attach, 300);
            setTimeout(attach, 1000);
        }})();
        """
        st.components.v1.html(f"<script>{js}</script>", height=0)

#    @st.fragment(run_every='300s')
    def render_trend(self, ticker_selected, ticker_selected_longname, interval, period, region=st):
                """Render the tiny_chart for the selected ticker at the configured interval/period."""
                if not self.multi_trends:

                    if self.t_chart.fig is None:
                        region.warning("No data available for this symbol.")
                        return

                    region.plotly_chart(
                        self.t_chart.fig,
                        use_container_width = True,
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

                            except Exception:
                                pass

                        region.plotly_chart(
                            t_chart_n.fig,
                            use_container_width = True,
                            theme="streamlit",
                            config = gt.chart_config,
                        )

                region.write(f'Last chart update: {dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

#                except Exception:

    def _render_market_stress(self, index_name, region=st):
        """Prominent breadth-based early-warning banner below the chart.

        For assets bound to a real exchange index (^…) this reads the
        market-stress score (compute_market_stress) — derived from how the
        index members' Bull/Bear/Sideways breadth is *changing* plus a price/
        breadth divergence term — and renders a traffic-light box. Silently
        does nothing for non-index assets or when no breadth data is available.
        Failures never break the page (the chart already rendered above).

        Opt-in: only shown when the user config 'show_regime' is True (default
        False), so the breadth banner stays hidden unless explicitly enabled.
        """
        if not self.sys_conf.get_value('show_regime', False):
            return
        try:
            from tradinglib.regime_data_engine import compute_market_stress
            from tradinglib.regime_flow_page import render_market_stress_banner
            if not index_name or not str(index_name).startswith('^'):
                return
            s = compute_market_stress(index_name)
        except Exception:
            logger.debug('market-stress banner skipped', exc_info=True)
            return
        render_market_stress_banner(s, index_name, region=region)

    def _add_portfolio_markers(self, ticker: str) -> list:
        """Query Own Trades, Strategy Engine, and Paper Trading for this ticker.

        Returns a list of position dicts with keys: source, entry, exit, open, count.
        Per source at most ONE aggregated open entry line is drawn on the chart;
        closed positions are returned for the badge only (no extra lines).
        """
        positions = []
        _tools = ts.Tools()

        # ── 1. Own Trades (trades.db) ─────────────────────────────────────────
        try:
            db_file = _tools.get_path('database', 'trades.db')
            with sqlite3.connect(db_file) as conn:
                raw = pd.read_sql_query(
                    "SELECT action, price, shares FROM trades WHERE ticker=? ORDER BY timestamp",
                    conn, params=(ticker,)
                )
            if not raw.empty:
                buy_rows  = raw[raw['action'] == 'buy']
                sell_rows = raw[raw['action'] == 'sell']
                net_shares = buy_rows['shares'].sum() - sell_rows['shares'].sum()
                if net_shares > 0.001:
                    avg_buy = (buy_rows['price'] * buy_rows['shares']).sum() / buy_rows['shares'].sum()
                    positions.append({'source': 'Own', 'entry': avg_buy, 'exit': None, 'open': True, 'count': 1})
                elif not buy_rows.empty and not sell_rows.empty:
                    avg_buy  = (buy_rows['price'] * buy_rows['shares']).sum() / buy_rows['shares'].sum()
                    avg_sell = (sell_rows['price'] * sell_rows['shares']).sum() / sell_rows['shares'].sum()
                    positions.append({'source': 'Own', 'entry': avg_buy, 'exit': avg_sell, 'open': False, 'count': 1})
        except Exception:
            pass

        # ── 2. Strategy Engine (trades{year}.db) ─────────────────────────────
        # Aggregate all open rows into ONE averaged entry per source; collect closed summary.
        try:
            year = dt.datetime.now().year
            db_file = _tools.get_path('database', f'trades{year}.db')
            with sqlite3.connect(db_file) as conn:
                strat_df = pd.read_sql_query(
                    "SELECT buyPrice, sellDate, sellPrice FROM trades WHERE ticker=?",
                    conn, params=(ticker,)
                )
            if not strat_df.empty:
                open_mask = strat_df['sellDate'].isna() | strat_df['sellDate'].isin(['', 'None', 'nan'])
                open_rows   = strat_df[open_mask]
                closed_rows = strat_df[~open_mask]

                if not open_rows.empty:
                    avg_entry = open_rows['buyPrice'].mean()
                    positions.append({
                        'source': 'Strategy', 'entry': float(avg_entry),
                        'exit': None, 'open': True, 'count': len(open_rows),
                    })

                if not closed_rows.empty:
                    avg_entry = closed_rows['buyPrice'].mean()
                    avg_exit  = closed_rows['sellPrice'].dropna().mean()
                    positions.append({
                        'source': 'Strategy', 'entry': float(avg_entry),
                        'exit': float(avg_exit) if pd.notna(avg_exit) else None,
                        'open': False, 'count': len(closed_rows),
                    })
        except Exception:
            pass

        # ── 3. Paper Trading (trading.db / position_trails) ──────────────────
        try:
            db_file = _tools.get_path('database', 'trading.db')
            with sqlite3.connect(db_file) as conn:
                paper_df = pd.read_sql_query(
                    "SELECT entry_price FROM position_trails WHERE ticker=? AND mode='paper'",
                    conn, params=(ticker,)
                )
            if not paper_df.empty:
                avg_entry = paper_df['entry_price'].mean()
                positions.append({
                    'source': 'Paper', 'entry': float(avg_entry),
                    'exit': None, 'open': True, 'count': len(paper_df),
                })
        except Exception:
            pass

        # ── Draw hlines — only open positions, one line per source ───────────
        for pos in positions:
            if not pos['open']:
                continue  # closed positions: badge only, no chart line
            entry = pos['entry']
            src   = pos['source']
            count = pos.get('count', 1)
            label = f"{src} Entry: {entry:.2f}" if count == 1 else f"{src} Entry: {entry:.2f} (×{count})"
            if entry and entry > 0:
                self.t_chart._add_hline_outside(
                    y=entry,
                    text=label,
                    line_color='#1a9e3f',
                    line_dash='dash',
                    line_width=2,
                )

        return positions

    def _render_quick_trade_buttons(self, buy_slot, sell_slot, ticker, headlines):
        """Render compact buy/sell buttons in the top row's reserved slots.

        Always shown — in Free mode (PAPER_TRADING_AVAILABLE=False) the buttons
        are greyed out visually via the help tooltip and open a "premium required"
        info dialog instead of the order dialog.  This avoids the ugly gap that
        would appear if the slots stayed empty.
        """
        close_price = getattr(headlines, 'close_price', None)
        suggested_investment = getattr(headlines, 'suggested_investment', None)

        if PAPER_TRADING_AVAILABLE:
            buy_help  = t('main.order_buy_help',  ticker=ticker)
            sell_help = t('main.order_sell_help', ticker=ticker)
        else:
            buy_help  = t('main.order_premium_hint')
            sell_help = t('main.order_premium_hint')

        if buy_slot.button("🟢", help=buy_help, key=f'btn_quick_buy_{ticker}', use_container_width=True):
            if PAPER_TRADING_AVAILABLE:
                self._render_order_dialog(ticker, 'buy', close_price, suggested_investment)
            else:
                self._render_premium_required_dialog()
        if sell_slot.button("🔴", help=sell_help, key=f'btn_quick_sell_{ticker}', use_container_width=True):
            if PAPER_TRADING_AVAILABLE:
                self._render_order_dialog(ticker, 'sell', close_price, suggested_investment)
            else:
                self._render_premium_required_dialog()

    @st.dialog('🔒')
    def _render_premium_required_dialog(self):
        """Shown in Free mode when the user clicks a quick buy/sell button."""
        st.markdown(f"### {t('main.order_premium_title')}")
        st.info(t('main.order_premium_body'))

    @st.dialog('Order')
    def _render_order_dialog(self, ticker, side, price, suggested_investment):
        """Stage a quick buy/sell order for later review on the Trading page.

        Deliberately does NOT contact the broker directly: from the asset-detail
        page we don't know whether the user's *other* browser session has the
        Trading page's broker/dry-run toggles set the way they expect right now
        (those live in `st.session_state`, not in a DB). Instead this stores a
        'queued' row via OrderLog.save_queued — same `broker_orders` table the
        Trading page reads — where it shows up in a reviewable "Queued Orders"
        list with explicit Send/Discard actions.
        """
        from tradinglib.premium.trading_bridge import OrderLog
        from tradinglib.ticker_resolver import TickerResolver

        broker_id = st.session_state.get('trading_broker', 'alpaca')
        mode = 'live' if broker_id == 'ibkr' else 'paper'

        side_label = t('main.order_buy') if side == 'buy' else t('main.order_sell')
        st.markdown(f"### {side_label} — {ticker}")
        st.caption(t('main.order_mode_caption', mode=mode.upper(), broker=broker_id.upper()))
        st.caption(t('main.order_queue_hint'))

        if price:
            st.write(t('main.order_price', price=price, currency=self.sys_conf.get_value('system_currency', 'USD')))

        default_qty = 1
        if suggested_investment and price:
            default_qty = max(1, int(suggested_investment / price))

        qty = st.number_input(t('main.order_qty'), min_value=1, step=1, value=default_qty)

        # ── Tradability check (informational, not a hard block) ───────────
        # resolve_for_broker() returns None for symbols the active broker does
        # not support (e.g. .DE / other non-US tickers on Alpaca). Rather than
        # refusing to stage those, we let the user opt in: the order is added to
        # the Queued Orders list as a reminder and the user later decides there
        # whether to execute it (e.g. after switching broker) or discard it.
        resolver = TickerResolver(db_path='database')
        resolved = resolver.resolve_for_broker(ticker, broker_id)
        tradeable = resolved is not None

        allow_queue = True
        if not tradeable:
            st.warning(t('main.order_not_tradeable', ticker=ticker, broker=broker_id.upper()))
            allow_queue = st.checkbox(
                t('main.order_queue_anyway'),
                key=f'queue_anyway_{ticker}_{side}',
            )

        # For supported symbols use the resolved broker symbol; for an opt-in
        # non-tradeable order keep the original ticker so it stays recognisable.
        broker_symbol = resolved or ticker

        if st.button(t('main.order_queue_btn'), type='primary',
                     use_container_width=True, disabled=not allow_queue):
            OrderLog(db_path='database').save_queued(
                mode=mode, broker=broker_id, strategy='quick',
                ticker=ticker, broker_symbol=broker_symbol, action=side,
                qty=float(qty), signal_price=float(price or 0),
                signal_date=dt.datetime.now().strftime('%Y-%m-%d'),
            )
            st.success(t('main.order_queued', side=side_label, qty=int(qty), symbol=broker_symbol))
            st.rerun()

    def render(self):
        """Render the full asset detail page: search, chart, headlines, indicators, and sentiment."""
        logger.debug(f'render_mainpage called symbol={self.symbol} username={self.username} is_admin={self.is_admin}')
        try:
            st.set_page_config(layout="wide")
        except Exception:
            pass

#        (pp_left, pp_right) = panel_pos.columns([0.01,.99],gap='small')
        pp_right = st

        # Tighter spacing in the main area (headlines, selectors, slider, tabs)
        # so the chart is fully visible instead of being cut off at the bottom.
        st.markdown(
            '<style>'
            '[data-testid="stMain"] .block-container{padding-top:0.5rem !important;padding-bottom:1rem;}'
            '[data-testid="stMain"] [data-testid="stVerticalBlock"]{gap:0.4rem;}'
            '[data-testid="stMain"] div[data-testid="stSlider"]{padding-top:0.1rem;padding-bottom:0.1rem;}'
            '[data-testid="stMain"] div[data-testid="stMetric"]{padding:0;}'
            '[data-testid="stMain"] [data-testid="stTabs"]{margin-top:-0.4rem;}'
            '</style>',
            unsafe_allow_html=True,
        )

#        exp_ = pp_right.expander('Asset details',expanded=True)
#        with exp_:
        self._render_selector_and_chart(pp_right)

    @st.fragment
    def _render_selector_and_chart(self, pp_right):
        """Search row + pips slider + selector (Interval/Period/Overlay/
        Oszilator) + tabs (Trend, Kennzahlen/headlines, ...), top to bottom
        in that order.

        Isolated as an st.fragment: toggling a checkbox here reruns only this
        section, not the whole page (sidebar navigation, page CSS). The search
        boxes and quick-trade buttons live in this fragment (not just the
        selector/chart) — Streamlit forbids writing a widget into a container
        created outside the fragment's own render path, and DOM order follows
        creation order, so everything that must appear above the chart in a
        specific order has to be created here, in that order. The two
        headline rows render inside the "Kennzahlen" tab (built alongside
        Trend) rather than as their own top-level rows — except in
        hide_details (compact) mode, which has no tabs at all.
        """
        def set_ticker(ticker):
            """Persist the selected ticker to sys_conf and update self.symbol."""
            self.ticker = ticker
            self.symbol = ticker
            if ticker:
                self.sys_conf.set_value('last_ticker', ticker)
            return ticker

        # ── Search row: market search · full-text search · quick-trade buttons ──
        # Settings (⚙) and Help (❓) live in the sidebar (asset_analyzer.py
        # show_navigation_links), so the freed width goes to the search columns.
        srch_region = pp_right.empty()
        (sr_left, sr_right, buy_col, sell_col) = srch_region.columns([0.42, 0.42, 0.08, 0.08])
        mkt = sr.MarketSearch(region=sr_left, default_ticker=self.symbol)
        fts = sr.FullTextSearch(region=sr_right, symbol=self.symbol, search_ticker_only=True, is_admin=self.is_admin)
        buy_slot = buy_col.empty()
        sell_slot = sell_col.empty()

        # Auto-resolve URL-provided symbols (e.g. /?symbol=Holcim%20AG → HOLN.SW).
        # Rules:
        #   1. Only runs when the symbol was passed explicitly via URL parameter
        #      (self._symbol_from_url=True). Never runs for last_ticker — otherwise
        #      every rerun blocks the market-search widget.
        #   2. Only runs ONCE per URL symbol (stored in session_state). On subsequent
        #      reruns the user can override freely via market-search or FTS.
        _auto_ss_key = f'_auto_resolved_{self.symbol}'
        if self._symbol_from_url and self.symbol:
            if _auto_ss_key not in st.session_state:
                fts.auto_resolve()
                st.session_state[_auto_ss_key] = fts.ticker_selected
            # Restore the resolved ticker so it is used as long as the user hasn't
            # actively typed something in the FTS widget.
            if not fts.ticker_selected:
                fts.ticker_selected = st.session_state.get(_auto_ss_key, '')

        if not self.hide_search:
            mkt.render()
            fts.render()
            # After interactive render: if the user typed in FTS, that overrides
            # the auto-resolved value stored in session_state.
            if fts.ticker_selected and self._symbol_from_url:
                st.session_state[_auto_ss_key] = fts.ticker_selected
        else:
            fts.symbol_search()

        self.multi_selector = ms.MultiCheckboxSelector(region=st, sys_conf=self.sys_conf)

        if self.hide_details:
            # Compact / read-only mode (e.g. the Market Map chart overlay): skip the
            # interactive selector UI — it doesn't fit the compact dialog layout and
            # would show its own live widget state, which can drift from what the
            # user actually configured (and saved) in the full Asset Viewer.
            # Instead, read the persisted overlay/oscillator settings straight from
            # config.db, so this chart always matches the Asset Viewer's chart for
            # the same ticker/settings.
            interval = None
            period = None
            (interval, period, self.overlays, self.oszilators) = self.sys_conf.get_selectors(interval, period, None, None)

            def _no_plot_set(conf_key):
                raw = self.sys_conf.get_value(conf_key, [])
                return {str(n).lower() for n in raw} if isinstance(raw, list) else set()

            _ov_no_plot = _no_plot_set('overlay_no_plot')
            _oz_no_plot = _no_plot_set('oszilator_no_plot')
            self.no_plot_overlays = [n for n in self.overlays if str(n).lower() in _ov_no_plot]
            self.no_plot_oszilators = [n for n in self.oszilators if str(n).lower() in _oz_no_plot]
        else:
            # Create an instance of the class and display the selectors
            self.multi_selector.render()
            interval = self.multi_selector.get_selected_options('Interval')[:1]
            period = self.multi_selector.get_selected_options('Period')[:1]
            self.overlays = self.multi_selector.get_selected_options('Overlay')
            self.oszilators = self.multi_selector.get_selected_options('Oszilator')
            plot_overlays = set(self.multi_selector.get_plot_options('Overlay'))
            plot_oszilators = set(self.multi_selector.get_plot_options('Oszilator'))
            self.no_plot_overlays = [n for n in self.overlays if n not in plot_overlays]
            self.no_plot_oszilators = [n for n in self.oszilators if n not in plot_oszilators]

            (interval, period, self.overlays, self.oszilators) = self.sys_conf.get_selectors(interval, period, self.overlays, self.oszilators)

        # Pips slider — placed after the Interval/Period/Overlay/Oszilator
        # dropdowns and before the tabs (filled later, once tiny_chart runs).
        slider_row = pp_right.empty()

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

        refresh = True

        # Priority logic:
        #
        # Case A — URL symbol provided (e.g. /?symbol=Heidelberger):
        #   1. FTS text-input typed by user  → highest (user explicitly searched)
        #   2. Auto-resolved URL ticker      → use until user overrides via FTS
        #   (Market-search default is intentionally ignored here; its dropdown
        #    simply reflects whatever market is open, not the user's intent.)
        #
        # Case B — Normal load (localhost:8082, symbol from last_ticker):
        #   1. FTS text-input typed          → highest
        #   2. Market-search selection       → default visible selection
        _auto_resolved = st.session_state.get(f'_auto_resolved_{self.symbol}', '') if self._symbol_from_url else ''
        _fts_user_typed = bool(st.session_state.get('_fts_search_query', ''))

        if _auto_resolved:
            # Case A: URL-provided symbol
            if _fts_user_typed and fts.ticker_selected:
                ticker_selected = set_ticker(fts.ticker_selected)
                ticker_selected_longname = fts.ticker_selected_longname
                self.data = fts.df
            else:
                ticker_selected = set_ticker(_auto_resolved)
                ticker_selected_longname = fts.ticker_selected_longname
                self.data = fts.df
        else:
            # Case B: Normal load — FTS > market-search
            ticker_selected = set_ticker(fts.ticker_selected)
            ticker_selected_longname = fts.ticker_selected_longname
            self.data = fts.df
            if not ticker_selected:
                ticker_selected = set_ticker(mkt.ticker_selected)
                ticker_selected_longname = mkt.ticker_selected_longname
                self.data = mkt.df
        
        if not ticker_selected:
            st.info(t('main.search_hint'))
            return

        # Resolve index membership for the finally selected ticker — regardless of
        # whether it came from FTS, URL auto-resolve, or market search. fts.index_name
        # was otherwise only set during the FTS-box pick (render) and remained empty
        # otherwise (e.g. AAMI/^RUT), even though membership exists in yf_tickers.db.
        fts.resolve_index_name(ticker_selected)

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
                no_plot_overlays=self.no_plot_overlays,
                no_plot_oszilators=self.no_plot_oszilators,
                username=self.username, 
                zoom = True,
                pips_select = True,
                add_current=add_current,
                region = slider_row
                )
            self.df = self.t_chart.df
            self.ticker = self.t_chart.ticker

            if self.df is None or self.df.empty:
                st.warning(f"No data available for **{ticker_selected}**. The symbol may not exist locally or has no price history.")
                return

            # Portfolio markers — entry/exit hlines from all portfolios
            self._portfolio_positions = []
            if ticker_selected:
                try:
                    self._portfolio_positions = self._add_portfolio_markers(ticker_selected)
                except Exception:
                    self._portfolio_positions = []

            # Tabs
            if ticker_selected:

                if self.hide_details:
                    # Compact / read-only mode (e.g. the Market Map chart overlay) has
                    # no tabs — the headline rows keep their own two-row placeholders.
                    head_row1 = st.empty()
                    head_row2 = st.empty()
                    headlines = hl.Headlines(self.df, self.ticker, self.data, screen_region_row1=head_row1, screen_region_row2=head_row2, interval = interval, index_name=fts.index_name, system_currency=self.sys_conf.get_value("system_currency","USD"), compact=True)
                    headlines.render()

                    # Quick buy/sell — placed in the top row (next to config/help) so they
                    # don't cost an extra line. Uses the same close price / suggested
                    # investment (d_txt → calculate_investment) shown in the headlines.
                    if not self.hide_search and ticker_selected:
                        self._render_quick_trade_buttons(buy_slot, sell_slot, ticker_selected, headlines)

                    self.render_trend(ticker_selected, ticker_selected_longname, interval=interval, period=period )
                    if self.sys_conf.get_value("pine_export", False):
                        self.multi_selector.render_pine_export()

                else:

                    # Info/News/Income/Balance tabs nur anzeigen, wenn dafuer Daten vorhanden sind.
                    info_text = self.get_ticker_value(self.ticker, 'longBusinessSummary')
                    quote_type = self.get_ticker_value(self.ticker, 'quoteType')
                    isin = self._get_isin(ticker_selected) if str(quote_type).upper() == 'ETF' else ''
                    has_info = bool(info_text) or bool(isin)

                    income_df = self.get_sheet_as_df(self.ticker, 'incomeSheet', 'Category')
                    balance_df = self.get_sheet_as_df(self.ticker, 'balanceSheet', 'Category')
                    try:
                        news_articles = se.YahooNewsSentiment(ticker_selected).fetch_news()
                    except Exception:
                        news_articles = []

                    seasonality_enabled = SEASONALITY_AVAILABLE and has_feature(FEATURE_SEASONALITY) and sn is not None

                    # "Kennzahlen" / "Key Data" holds both headline rows (price/close/
                    # currency/open/low/high/52-week range/ratios) — right after Trend
                    # so it stays close to the default view.
                    tab_list = [t('main.tab_trend'), t('main.tab_overview')]
                    if seasonality_enabled:
                        tab_list.append(t('main.tab_seasonality'))
                    if has_info:
                        tab_list.append(t('main.tab_info'))
                    if not income_df.empty:
                        tab_list.append(t('main.tab_income'))
                    if not balance_df.empty:
                        tab_list.append(t('main.tab_balance'))
                    if news_articles:
                        tab_list.append(t('main.tab_news'))
                    if show_details:
                        tab_list.append(t('main.tab_details'))

                    tab_iter = iter(pp_right.tabs(tab_list))
                    tab_trend = next(tab_iter)
                    tab_overview = next(tab_iter)
                    tab_seasonality = next(tab_iter) if seasonality_enabled else None
                    tab_info = next(tab_iter) if has_info else None
                    tab_income_sheet = next(tab_iter) if not income_df.empty else None
                    tab_balance_sheet = next(tab_iter) if not balance_df.empty else None
                    tab_news = next(tab_iter) if news_articles else None
                    if show_details:
                        tab_details = next(tab_iter)

                    # Both headline rows render into the same "Kennzahlen" tab.
                    headlines = hl.Headlines(self.df, self.ticker, self.data, screen_region_row1=tab_overview, screen_region_row2=tab_overview, interval = interval, index_name=fts.index_name, system_currency=self.sys_conf.get_value("system_currency","USD"))
                    headlines.render()

                    # Quick buy/sell — placed in the top row (next to config/help) so they
                    # don't cost an extra line. Uses the same close price / suggested
                    # investment (d_txt → calculate_investment) shown in the headlines.
                    if not self.hide_search and ticker_selected:
                        self._render_quick_trade_buttons(buy_slot, sell_slot, ticker_selected, headlines)

                    # Spinner placeholder lives OUTSIDE any tab so position:fixed covers the viewport
                    _spin = st.empty()

                    with tab_trend:
                        _spin.markdown(_tab_overlay(t('main.tab_trend')), unsafe_allow_html=True)
                        self.render_trend(ticker_selected, ticker_selected_longname, interval=interval, period=period)
                        # Prominent early-warning banner directly below the chart
                        # for index-bound assets (breadth-derived market stress).
                        self._render_market_stress(fts.index_name, region=tab_trend)
                        _ppos = getattr(self, '_portfolio_positions', [])
                        if _ppos:
                            _open  = [p for p in _ppos if p['open']]
                            _closed = [p for p in _ppos if not p['open']]
                            if _open:
                                _parts = []
                                for p in _open:
                                    _cnt = p.get('count', 1)
                                    _lbl = f"{p['source']}: Invested: Entry: {p['entry']:.2f}"
                                    if _cnt > 1:
                                        _lbl += f" (×{_cnt})"
                                    _parts.append(_lbl)
                                tab_trend.success('  |  '.join(_parts))
                            if _closed:
                                _parts = []
                                for p in _closed:
                                    _cnt = p.get('count', 1)
                                    _exit_str = f" → Exit: {p['exit']:.2f}" if p.get('exit') else ''
                                    _lbl = f"{p['source']}: Entry: {p['entry']:.2f}{_exit_str}"
                                    if _cnt > 1:
                                        _lbl += f" (×{_cnt})"
                                    _parts.append(_lbl)
                                tab_trend.info(f"Closed: {'  |  '.join(_parts)}")
                        if self.sys_conf.get_value("pine_export", False):
                            self.multi_selector.render_pine_export()
                        _spin.empty()

                    if tab_seasonality is not None:
                        with tab_seasonality:
                            _spin.markdown(_tab_overlay(t('main.tab_seasonality')), unsafe_allow_html=True)
                            sn.render_seasonality(ticker_selected, ticker_selected_longname, region=tab_seasonality)
                            _spin.empty()

    #, add_sub_plots=['ewo']
                    if show_details:
                        with tab_details:
                            _spin.markdown(_tab_overlay(t('main.tab_details')), unsafe_allow_html=True)
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
                                                                add_sub_plots=self.oszilators,
                                                                no_plot_overlays=self.no_plot_overlays,
                                                                no_plot_oszilators=self.no_plot_oszilators,
                                                                )
                                        fig = t_chart.fig
                                        ir_c[j][i].plotly_chart(fig,
                                            use_container_width = True,
                                            theme="streamlit",
                                            config = gt.chart_config,
                                        )
                                    except Exception:
                                        pass

                            full_df_ex1 = st.expander(t('main.data_expander'))
                            with full_df_ex1:
#                                try:
##                                    st.dataframe(self.data)
                                    st.dataframe(self.t_chart.df)
#                                except Exception:
                            _spin.empty()

                    if tab_info is not None:
                        with tab_info:
                            _spin.markdown(_tab_overlay(t('main.tab_info')), unsafe_allow_html=True)
                            if info_text:
                                st.info(info_text)

                            # ETPs (quoteType=='ETF') haben i.d.R. keine longBusinessSummary --
                            # stattdessen Link auf justETF (per ISIN), falls vorhanden.
                            if isin:
                                st.markdown(t('main.justetf_isin', isin=isin))
                                justetf_url = f"https://www.justetf.com/{current_language()}/etf-profile.html?isin={isin}"
                                st.link_button(t('main.justetf_button'), justetf_url)
                                self._inject_justetf_auto_open(ticker_selected, justetf_url, t('main.tab_info'))
                            _spin.empty()

                    if tab_income_sheet is not None:
                        with tab_income_sheet:
                            _spin.markdown(_tab_overlay(t('main.tab_income')), unsafe_allow_html=True)
                            st.subheader(t('main.income_header', ticker=ticker_selected))
                            st.dataframe(income_df, use_container_width=True)
                            _spin.empty()

                    if tab_balance_sheet is not None:
                        with tab_balance_sheet:
                            _spin.markdown(_tab_overlay(t('main.tab_balance')), unsafe_allow_html=True)
                            st.subheader(t('main.balance_header', ticker=ticker_selected))
                            st.dataframe(balance_df, use_container_width=True)
                            _spin.empty()

                    if tab_news is not None:
                        with tab_news:
                            _spin.markdown(_tab_overlay(t('main.tab_news')), unsafe_allow_html=True)
                            _news_tab_fragment(ticker_selected)
                            _spin.empty()

