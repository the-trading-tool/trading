from tradinglib import ( tiny_chart as tc, search as sr,
        sentiment as se, headlines as hl, multi_select as ms, fetch_data,
        system_config as sysconf, graph_tools as gt, tools as ts)
from tradinglib.indicator import indicator  # Die Basisklasse importieren
from tradinglib.i18n import t
from tradinglib.premium_availability import PAPER_TRADING_AVAILABLE
import streamlit as st
import streamlit_nested_layout
import datetime as dt
import sqlite3
import pandas as pd
import logging

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
            self.symbol = self.sys_conf.get_value('last_ticker', '') or ''
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

        They open an order dialog pre-filled with the close price and the
        suggested investment amount (the same `d_txt` shown as help text on the
        Close metric, derived from `tt.calculate_investment(log_vola)`).
        """
        close_price = getattr(headlines, 'close_price', None)
        suggested_investment = getattr(headlines, 'suggested_investment', None)

        if buy_slot.button("🟢", help=t('main.order_buy_help', ticker=ticker), key=f'btn_quick_buy_{ticker}', use_container_width=True):
            self._render_order_dialog(ticker, 'buy', close_price, suggested_investment)
        if sell_slot.button("🔴", help=t('main.order_sell_help', ticker=ticker), key=f'btn_quick_sell_{ticker}', use_container_width=True):
            self._render_order_dialog(ticker, 'sell', close_price, suggested_investment)

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

        if st.button(t('main.order_queue_btn'), type='primary', use_container_width=True):
            resolver = TickerResolver(db_path='database')
            broker_symbol = resolver.resolve_for_broker(ticker, broker_id) or ticker

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
        def set_ticker(ticker):
            """Persist the selected ticker to sys_conf and update self.symbol."""
            self.ticker = ticker
            self.symbol = ticker
            if ticker:
                self.sys_conf.set_value('last_ticker', ticker)
            return ticker
            
        try:
            st.set_page_config(layout="wide")
        except Exception:
            pass

#        (pp_left, pp_right) = panel_pos.columns([0.01,.99],gap='small')
        pp_right = st

        # Kompaktere Abstände im Hauptbereich (Headlines, Selectoren, Slider, Tabs),
        # damit der Chart vollständig sichtbar ist statt am unteren Rand abgeschnitten.
        st.markdown(
            '<style>'
            '[data-testid="stMain"] .block-container{padding-top:2rem;padding-bottom:1rem;}'
            '[data-testid="stMain"] [data-testid="stVerticalBlock"]{gap:0.4rem;}'
            '[data-testid="stMain"] div[data-testid="stSlider"]{padding-top:0.1rem;padding-bottom:0.1rem;}'
            '[data-testid="stMain"] div[data-testid="stMetric"]{padding:0;}'
            '[data-testid="stMain"] [data-testid="stTabs"]{margin-top:-0.4rem;}'
            '</style>',
            unsafe_allow_html=True,
        )

#        exp_ = pp_right.expander('Asset details',expanded=True)
#        with exp_:
        srch_region = pp_right.empty()
        slctr_region = st.empty()
        head_row1 = st.empty()
        head_row2 = st.empty()
    
        slider_row = pp_right.empty()
    
        self.multi_selector = ms.MultiCheckboxSelector(region=slctr_region, sys_conf=self.sys_conf)
        (sr_left, sr_right, sr_conf, sr_hlp, sr_buy, sr_sell) = srch_region.columns([0.38,0.38,0.045,0.045,0.075,0.075])
        # Filled later, once ticker_selected/close_price/suggested_investment are known.
        buy_slot = sr_buy.empty()
        sell_slot = sr_sell.empty()

        if not self.hide_search:
            if sr_conf.button(":rosette:", use_container_width=True):
                self.sys_conf.render()
            if sr_hlp.button(":grey_question:",use_container_width=True):
                self.sys_conf.render_help()

#        (sr_left, sr_right,_,cfg_btn_c,cfg_btn_h) = srch_region.columns([0.35,0.35,0.06,0.07,0.07],gap='small')
        mkt = sr.MarketSearch(region=sr_left, default_ticker=self.symbol)
        fts = sr.FullTextSearch(region=sr_right, symbol=self.symbol, search_ticker_only=True, is_admin=self.is_admin)

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
        if not self.hide_details:

            tab_list = [
                t('main.tab_trend'), t('main.tab_info'),
                t('main.tab_income'), t('main.tab_balance'), t('main.tab_news'),
            ]
            if show_details:
                tab_list.append(t('main.tab_details'))

            tabs = pp_right.tabs(tab_list)
            tab_trend = tabs[0]
            tab_info = tabs[1]
            tab_income_sheet = tabs[2]
            tab_balance_sheet = tabs[3]
            tab_news = tabs[4]
            if show_details:
                tab_details = tabs[5]

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

            if 1:
#        try:
                headlines = hl.Headlines(self.df, self.ticker, self.data, screen_region_row1=head_row1, screen_region_row2=head_row2, interval = interval, index_name=fts.index_name, system_currency=self.sys_conf.get_value("system_currency","USD"))
                headlines.render()
#        except Exception:

            # Quick buy/sell — placed in the top row (next to config/help) so they
            # don't cost an extra line. Uses the same close price / suggested
            # investment (d_txt → calculate_investment) shown in the headlines.
            if PAPER_TRADING_AVAILABLE and not self.hide_search and ticker_selected:
                self._render_quick_trade_buttons(buy_slot, sell_slot, ticker_selected, headlines)

            # Tabs

            if ticker_selected:

                if self.hide_details:

                    self.render_trend(ticker_selected, ticker_selected_longname, interval=interval, period=period )
                    if self.sys_conf.get_value("pine_export", False):
                        self.multi_selector.render_pine_export()

                else:

                    # Spinner placeholder lives OUTSIDE any tab so position:fixed covers the viewport
                    _spin = st.empty()

                    with tab_trend:
                        _spin.markdown(_tab_overlay(t('main.tab_trend')), unsafe_allow_html=True)
                        self.render_trend(ticker_selected, ticker_selected_longname, interval=interval, period=period)
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

                    with tab_info:
                        _spin.markdown(_tab_overlay(t('main.tab_info')), unsafe_allow_html=True)
                        info = self.get_ticker_value(self.ticker,'longBusinessSummary')
                        if info:
                            st.info(info)
                        _spin.empty()

                    with tab_income_sheet:
                        _spin.markdown(_tab_overlay(t('main.tab_income')), unsafe_allow_html=True)
                        try:
                            sht_df = self.get_sheet_as_df(self.ticker, 'incomeSheet', 'Category')
                            if not sht_df.empty:
                                st.subheader(t('main.income_header', ticker=ticker_selected))
                                st.dataframe(sht_df,use_container_width=True)
                        except Exception:
                            pass
                        _spin.empty()

                    with tab_balance_sheet:
                        _spin.markdown(_tab_overlay(t('main.tab_balance')), unsafe_allow_html=True)
                        try:
                            sht_df = self.get_sheet_as_df(self.ticker, 'balanceSheet', 'Category')
                            if not sht_df.empty:
                                st.subheader(t('main.balance_header', ticker=ticker_selected))
                                st.dataframe(sht_df,use_container_width=True)
                        except Exception:
                            pass
                        _spin.empty()

                    with tab_news:
                        _spin.markdown(_tab_overlay(t('main.tab_news')), unsafe_allow_html=True)
                        _news_tab_fragment(ticker_selected)
                        _spin.empty()

