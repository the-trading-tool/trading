import logging
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from typing import Optional

from tradinglib.system_config import SystemConfig
from tradinglib.trading_bridge import BrokerFactory, OrderLog, SignalEvaluator, calc_qty
from tradinglib.ticker_resolver import TickerResolver
from tradinglib import ksplib

logger = logging.getLogger(__name__)


class TradingPage:
    """Paper (Alpaca) / Live (IBKR) trading dashboard wired to Multi-Strategies signals."""

    def __init__(self, username: str, db_path: str = 'database'):
        self.username = username
        self.db_path = db_path
        self.sys_config = SystemConfig(username=username)
        self.order_log = OrderLog(db_path=db_path)
        self.resolver = TickerResolver(db_path=db_path)

    # ------------------------------------------------------------------ #
    #  Small helpers                                                       #
    # ------------------------------------------------------------------ #

    def _broker_config(self) -> dict:
        cfg: dict = {
            'alpaca_key':    '',
            'alpaca_secret': '',
            'ibkr_host':     self.sys_config.get_value('ibkr_host', '127.0.0.1'),
            'ibkr_port':     self.sys_config.get_value('ibkr_port', '7497'),
        }
        # Load Alpaca credentials from ksplib using the stored API name
        alpaca_ksp_name = self.sys_config.get_value('alpaca_ksp_name', 'av-paper')
        try:
            ksp = ksplib.Ksp(storage_path=self.db_path, secrets_path=self.db_path)
            creds = ksp.get_ksp(alpaca_ksp_name)
            if isinstance(creds, dict):
                cfg['alpaca_key']    = creds.get('user', '')
                cfg['alpaca_secret'] = creds.get('password', '')
        except Exception as e:
            logger.debug(f"ksplib lookup failed for '{alpaca_ksp_name}': {e}")
        return cfg

    def _broker_id(self) -> str:
        return st.session_state.get('trading_broker', 'alpaca')

    def _mode(self) -> str:
        return 'live' if self._broker_id() == 'ibkr' else 'paper'

    def _broker(self):
        return BrokerFactory.create(self._broker_id(), self._broker_config())

    def _strategies(self) -> dict:
        raw = self.sys_config.get_value('multi_transactions', None)
        if isinstance(raw, dict) and raw:
            return raw
        return self.sys_config.transactions

    def _enabled(self) -> list[str]:
        return self.sys_config.get_value('trading_enabled_strategies', []) or []

    def _dry_run(self) -> bool:
        return st.session_state.get('trading_dry_run', True)

    def _budget_per_position(self, strategy_cfg: dict) -> float:
        invest = strategy_cfg.get('invest', 1000)
        n = max(strategy_cfg.get('num_assets', 1), 1)
        return invest / n

    @staticmethod
    def _apply_inv_vola_sizing(
        signals: list[dict],
        strategy_cfg: dict,
    ) -> list[dict]:
        """Sort signals by order_by score, keep top num_assets, weight by inverse volatility.

        This mirrors the allocation logic used in the other portfolio apps:
          weight_i  = (1 / vola_i) / sum(1 / vola_j)   for j in top-N
          budget_i  = invest * weight_i
          qty_i     = floor(budget_i / price_i)  (min 1)
        """
        if not signals:
            return []

        invest     = float(strategy_cfg.get('invest',     1000))
        num_assets = max(int(strategy_cfg.get('num_assets', 1)),  1)

        # 1. Sort descending by score (= order_by metric, e.g. sortino)
        ranked = sorted(signals, key=lambda s: float(s.get('score', 0)), reverse=True)

        # 2. Keep only top num_assets
        selected = ranked[:num_assets]

        # 3. Inverse-volatility weights
        inv_volas   = [1.0 / max(float(s.get('vola', 1.0)), 1e-6) for s in selected]
        total_iv    = sum(inv_volas)

        result: list[dict] = []
        for s, iv in zip(selected, inv_volas):
            weight = iv / total_iv
            budget = invest * weight
            price  = s['price']
            qty    = calc_qty(budget, price)
            row    = dict(s)                         # copy — don't mutate original
            row['weight'] = round(weight * 100, 1)  # percentage of total invest
            row['budget'] = round(budget, 2)
            row['qty']    = int(qty)
            row['value']  = round(qty * price, 2)
            result.append(row)

        return result

    # ------------------------------------------------------------------ #
    #  Entry point                                                         #
    # ------------------------------------------------------------------ #

    def render(self):
        self._render_mode_switcher()
        st.markdown('---')

        if self._broker_id() == 'ibkr':
            st.error('⚠ LIVE MODE — Orders are executed with **real money** at IBKR!')

        tabs = st.tabs([
            '📊 Account',
            '🔔 Signals',
            '📂 Positions',
            '📋 History',
            '📈 Compare',
            '⚙ Settings',
        ])
        tab_account, tab_signals, tab_positions, tab_history, tab_compare, tab_settings = tabs

        with tab_account:
            self._tab_account()
        with tab_signals:
            self._tab_signals()
        with tab_positions:
            self._tab_positions()
        with tab_history:
            self._tab_history()
        with tab_compare:
            self._tab_compare()
        with tab_settings:
            self._tab_settings()

    # ------------------------------------------------------------------ #
    #  Mode switcher                                                       #
    # ------------------------------------------------------------------ #

    def _render_mode_switcher(self):
        col_paper, col_live, col_dry = st.columns([2, 2, 2])

        is_paper = self._broker_id() == 'alpaca'
        with col_paper:
            if st.button(
                f"{'●' if is_paper else '○'}  Paper Trading — Alpaca",
                use_container_width=True,
                type='primary' if is_paper else 'secondary',
            ):
                st.session_state['trading_broker'] = 'alpaca'
                st.rerun()

        with col_live:
            st.button(
                '○  Live Trading — IBKR  *(Phase 3)*',
                use_container_width=True,
                type='secondary',
                disabled=True,
                help='IBKR integration coming in Phase 3. Will also support XETRA (SDAX/MDAX/GDAX).',
            )

        with col_dry:
            st.toggle(
                '🧪 Dry run',
                value=self._dry_run(),
                key='trading_dry_run',
                help='Signals are calculated but no orders are sent',
            )
            if self._dry_run():
                st.caption('No real orders will be sent')

    # ------------------------------------------------------------------ #
    #  Tab: Account                                                        #
    # ------------------------------------------------------------------ #

    def _tab_account(self):
        broker = self._broker()
        connected = broker.is_connected()

        status_icon = '🟢 Connected' if connected else '🔴 Not connected'
        st.markdown(f'**Alpaca Paper:** {status_icon}')

        if not connected:
            st.info('Add API credentials under ⚙ Settings.')
        else:
            try:
                acct = broker.get_account_info()
                c1, c2, c3, c4 = st.columns(4)
                c1.metric('Equity',        f"{acct.equity:,.2f} {acct.currency}")
                c2.metric('Buying Power',  f"{acct.buying_power:,.2f} {acct.currency}")
                c3.metric('Cash',          f"{acct.cash:,.2f} {acct.currency}")
                c4.metric('Day P&L',       f"{acct.unrealized_pnl:+,.2f} {acct.currency}")
            except Exception as e:
                st.error(f'Failed to load account data: {e}')

            # ── Portfolio equity chart + allocation pie ─────────────────
            col_chart, col_pie = st.columns([3, 2])

            with col_chart:
                st.markdown('##### Portfolio-Entwicklung')
                period = st.radio(
                    'Zeitraum', ['1W', '1M', '3M', '1A'],
                    horizontal=True, index=1, key='portfolio_period',
                )
                try:
                    hist = broker.get_portfolio_history(period=period)
                    if hist.get('timestamps'):
                        import datetime as _dt
                        dates = [_dt.datetime.fromtimestamp(t) for t in hist['timestamps']]
                        eq    = hist['equity']
                        fig_eq = go.Figure()
                        fig_eq.add_trace(go.Scatter(
                            x=dates, y=eq,
                            fill='tozeroy', name='Equity',
                            line=dict(color='royalblue', width=2),
                        ))
                        base = hist.get('base_value', eq[0] if eq else 0)
                        if base:
                            pnl_pct = round((eq[-1] / base - 1) * 100, 2) if eq else 0
                            fig_eq.add_annotation(
                                x=dates[-1], y=eq[-1],
                                text=f'{pnl_pct:+.2f}%',
                                showarrow=False, xanchor='right',
                                font=dict(color='royalblue', size=13),
                            )
                        fig_eq.update_layout(
                            height=260, margin=dict(t=10, b=10, l=0, r=0),
                            xaxis_title='', yaxis_title='Equity (USD)',
                            showlegend=False,
                        )
                        st.plotly_chart(fig_eq, use_container_width=True)
                    else:
                        st.caption('Keine Portfolio-Historie verfügbar.')
                except Exception as e:
                    st.caption(f'Portfolio-Kurve nicht verfügbar: {e}')

            with col_pie:
                st.markdown('##### Allokation')
                try:
                    positions = broker.get_positions()
                    if positions:
                        import plotly.express as px
                        pie_df = pd.DataFrame([
                            {'Symbol': p.broker_symbol, 'Wert': p.market_value}
                            for p in positions if p.market_value > 0
                        ])
                        fig_pie = px.pie(pie_df, names='Symbol', values='Wert',
                                         height=260)
                        fig_pie.update_layout(margin=dict(t=10, b=10, l=0, r=0),
                                              showlegend=True)
                        st.plotly_chart(fig_pie, use_container_width=True)
                    else:
                        st.caption('Keine offenen Positionen.')
                except Exception as e:
                    st.caption(f'Allokation nicht verfügbar: {e}')

        st.markdown('#### Strategien aktivieren')
        strategies = self._strategies()
        enabled = self._enabled()
        new_enabled: list[str] = []
        cols = st.columns(max(len(strategies), 1))
        for i, (name, indices_or_cfg) in enumerate(strategies.items()):
            with cols[i]:
                # Two-level config: {strategy: {index: {buy, sell, invest, …}}}
                # Flat (legacy):    {index:    {buy, sell, invest, …}}
                is_two_level = (
                    isinstance(indices_or_cfg, dict)
                    and all(isinstance(v, dict) for v in indices_or_cfg.values())
                    and any('buy' not in v for v in indices_or_cfg.values())
                )
                if is_two_level:
                    n_idx = len(indices_or_cfg)
                    total_invest = sum(
                        v.get('invest', 0) for v in indices_or_cfg.values()
                        if isinstance(v, dict)
                    )
                    help_txt = (
                        f"{n_idx} Indizes  |  "
                        f"Gesamt-Invest: {total_invest:,.0f}  |  "
                        f"Indizes: {', '.join(indices_or_cfg.keys())}"
                    )
                else:
                    help_txt = (
                        f"Invest: {indices_or_cfg.get('invest', 0):,.0f}  |  "
                        f"Max assets: {indices_or_cfg.get('num_assets', 0)}  |  "
                        f"Sort by: {indices_or_cfg.get('order_by', '—')}"
                    )
                checked = st.checkbox(
                    name,
                    value=name in enabled,
                    key=f'strat_en_{name}',
                    help=help_txt,
                )
                if checked:
                    new_enabled.append(name)

        if set(new_enabled) != set(enabled):
            self.sys_config.set_value('trading_enabled_strategies', new_enabled)
            st.rerun()

    # ------------------------------------------------------------------ #
    #  Tab: Signals                                                        #
    # ------------------------------------------------------------------ #

    def _tab_signals(self):
        broker_id = self._broker_id()
        enabled = self._enabled()
        strategies = self._strategies()

        if not enabled:
            st.info('No strategy enabled. Please select under 📊 Account.')
            return

        active = {k: v for k, v in strategies.items() if k in enabled}

        evaluator = SignalEvaluator(username=self.username, db_path=self.db_path)
        all_signals: list[dict] = []
        eval_errors: dict[str, str] = {}

        for strategy_name, indices_or_cfg in active.items():
            # Support both config shapes:
            #   Two-level (current):  {strategy: {'^SPX': {buy, sell, invest, …}, …}}
            #   Flat (legacy):        {'^SPX':    {buy, sell, invest, …}}
            if isinstance(indices_or_cfg, dict):
                first = next(iter(indices_or_cfg.values()), None)
                if isinstance(first, dict) and 'buy' not in indices_or_cfg:
                    # Two-level: outer key is strategy name, inner keys are index names
                    index_cfg_pairs = list(indices_or_cfg.items())
                else:
                    # Flat legacy: outer key IS the index name
                    index_cfg_pairs = [(strategy_name, indices_or_cfg)]
            else:
                continue

            for index_name, cfg in index_cfg_pairs:
                if not isinstance(cfg, dict):
                    continue
                label = f'{strategy_name} / {index_name}'
                with st.spinner(f'Auswertung {label} …'):
                    sigs, err = evaluator.get_signals(strategy_name, index_name, cfg)
                    if err:
                        eval_errors[label] = err

                    buy_raw  = [s for s in sigs if s['signal'] == 'buy']
                    sell_raw = [s for s in sigs if s['signal'] == 'sell']

                    sized_buys  = self._apply_inv_vola_sizing(buy_raw,  cfg)
                    sized_sells = self._apply_inv_vola_sizing(sell_raw, cfg)

                    for s in sized_buys + sized_sells:
                        sym = self.resolver.resolve_for_broker(s['ticker'], broker_id)
                        s['broker_symbol'] = sym or '—'
                        s['tradeable']     = sym is not None
                        all_signals.append(s)

        if eval_errors:
            with st.expander('⚠ Signal evaluation errors', expanded=True):
                for strat, msg in eval_errors.items():
                    st.error(f'**{strat}**: {msg}')
                st.caption(
                    'Mögliche Ursachen: Index nicht in yf_tickers.db, '
                    'oder `python asset_perf2.py` ausführen um asset_simulation_.db zu befüllen.'
                )

        if not all_signals and not eval_errors:
            st.success('No current signals — all strategies are neutral.')
            return
        if not all_signals:
            return

        df = pd.DataFrame(all_signals)
        buy_df  = df[df['signal'] == 'buy']
        sell_df = df[df['signal'] == 'sell']

        col_b, col_s, col_info = st.columns([1, 1, 4])
        col_b.metric('🟢 Buy signals',  len(buy_df))
        col_s.metric('🔴 Sell signals', len(sell_df))
        with col_info:
            st.caption(
                'Sizing pro Strategie+Index: Top-N nach Score (order_by), '
                'Budget nach inverser Volatilität gewichtet. '
                'Jede Strategie/Index-Kombination hat ihr eigenes invest/num_assets.'
            )

        # ── ATR multiplier for stop-loss ──────────────────────────────
        atr_col, filter_col = st.columns([2, 3])
        with atr_col:
            atr_mult = st.number_input(
                'ATR-Multiplikator (Stop Loss)',
                min_value=0.5, max_value=10.0, value=2.0, step=0.5,
                help='Stop-Loss = Kurs − N × ATR  (nur für Kauf-Orders als OTO-Auftrag)',
                key='atr_mult',
            )
        with filter_col:
            sig_filter = st.radio(
                'Anzeigen:', ['Alle', 'Nur Käufe', 'Nur Verkäufe'],
                horizontal=True, key='sig_filter',
            )

        show_df = (df if sig_filter == 'Alle'
                   else (buy_df if sig_filter == 'Nur Käufe' else sell_df))

        # Compute stop_loss_price per row and embed it back in all_signals
        for s in all_signals:
            if s['signal'] == 'buy' and s.get('atr', 0) > 0:
                s['stop_loss_price'] = round(
                    s['price'] - atr_mult * s['atr'], 2
                )
            else:
                s['stop_loss_price'] = None

        # Build editable table: select-checkbox + asset viewer link
        edit_df = show_df.copy().reset_index(drop=True)
        # Re-sync stop_loss_price into edit_df (show_df is a filtered subset)
        edit_df['stop_loss_price'] = edit_df.apply(
            lambda r: (
                round(r['price'] - atr_mult * r['atr'], 2)
                if r.get('signal') == 'buy' and r.get('atr', 0) > 0
                else None
            ),
            axis=1,
        )

        edit_df.insert(0, 'select', True)
        # Link: /?details=true&symbol=<longName>
        # All non-alphanumeric characters are replaced with %20
        def _to_link(name: str) -> str:
            safe = ''.join(c if c.isalnum() else '%20' for c in str(name))
            return f'/?details=true&symbol={safe}'

        edit_df['view'] = edit_df['longName'].apply(_to_link)

        display_cols = [c for c in
            ['select', 'strategy', 'index', 'signal', 'ticker', 'longName',
             'price', 'atr', 'stop_loss_price', 'currency',
             'score', 'weight', 'budget', 'qty', 'value',
             'broker_symbol', 'tradeable', 'view']
            if c in edit_df.columns]

        edited = st.data_editor(
            edit_df[display_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                'select':           st.column_config.CheckboxColumn(
                                        '✓', width='small',
                                        help='Für Ausführung markieren'),
                'strategy':         st.column_config.TextColumn(
                                        'Strategie',
                                        help='Strategiename (z.B. "Support/Resistance Strategy")'),
                'index':            st.column_config.TextColumn(
                                        'Index',
                                        help='Marktindex (z.B. ^SPX, ^GDAXI)'),
                'signal':           st.column_config.TextColumn('Signal',       width='small'),
                'ticker':           st.column_config.TextColumn('Ticker'),
                'longName':         st.column_config.TextColumn('Name'),
                'price':            st.column_config.NumberColumn('Kurs',        format='%.2f'),
                'atr':              st.column_config.NumberColumn(
                                        'ATR', format='%.3f',
                                        help='Average True Range — Basis für den Stop-Loss'),
                'stop_loss_price':  st.column_config.NumberColumn(
                                        'Stop Loss', format='%.2f',
                                        help=f'Kurs − {atr_mult:.1f} × ATR  (nur bei Käufen)'),
                'currency':         st.column_config.TextColumn('CCY',           width='small'),
                'score':            st.column_config.NumberColumn(
                                        'Score', format='%.3f',
                                        help='Ranking-Metrik (order_by), z.B. Sortino'),
                'weight':           st.column_config.NumberColumn(
                                        'Gewicht %', format='%.1f',
                                        help='Inv.-Vola-Gewicht am Index-Budget'),
                'budget':           st.column_config.NumberColumn(
                                        'Budget', format='%.2f',
                                        help='invest × Gewicht (für diesen Index)'),
                'qty':              st.column_config.NumberColumn('Stück',        format='%d',  width='small'),
                'value':            st.column_config.NumberColumn('Wert',         format='%.2f'),
                'broker_symbol':    st.column_config.TextColumn('Broker-Symbol'),
                'tradeable':        st.column_config.CheckboxColumn('Handelbar',  width='small'),
                'view':             st.column_config.LinkColumn(
                                        '📊 Details',
                                        display_text='→ öffnen',
                                        help='Asset Viewer mit Details öffnen'),
            },
            disabled=[c for c in display_cols if c != 'select'],
        )

        # Collect the signals that are checked AND tradeable
        selected_signals: list[dict] = []
        n_not_tradeable = 0
        for i in edited[edited['select']].index.tolist():
            row_ed = edit_df.iloc[i]
            match = next(
                (s for s in all_signals
                 if s['ticker']   == row_ed['ticker']
                 and s['strategy'] == row_ed['strategy']
                 and s.get('index', '') == row_ed.get('index', '')
                 and s['signal']   == row_ed['signal']),
                None,
            )
            if match is None:
                continue
            if match['tradeable']:
                selected_signals.append(match)
            else:
                n_not_tradeable += 1

        if n_not_tradeable:
            st.warning(
                f'{n_not_tradeable} ausgewähltes Signal nicht handelbar auf '
                f'{broker_id.upper()} (kein Broker-Symbol) — übersprungen.'
            )

        n_sel = len(selected_signals)

        if n_sel == 0:
            if edited['select'].any():
                st.warning('Keine handelbaren Signale ausgewählt.')
            else:
                st.info('✓-Spalte ankreuzen um Signale zur Ausführung auszuwählen.')
            return

        dry = self._dry_run()
        if st.button(
            f'▶ {n_sel} markierte Order(s) ausführen',
            type='primary',
            disabled=dry,
            help=(
                'Dry-Run aktiv — Schalter oben deaktivieren, '
                'um echte Orders an Alpaca zu senden.'
            ) if dry else None,
        ):
            self._dialog_confirm_all(selected_signals, active, broker_id)

        if dry:
            stop_info = ', '.join(
                f"{s['ticker']} SL={s.get('stop_loss_price') or '—'}"
                for s in selected_signals
            )
            st.caption(f'🧪 Dry-Run — {n_sel} Order(s) würden gesendet: {stop_info}')

    @st.dialog('Aufträge bestätigen', width='large')
    def _dialog_confirm_all(self, signals: list[dict], strategies: dict, broker_id: str):
        st.warning(f'**{len(signals)} Order(s)** werden an Alpaca Paper gesendet.')

        prev_rows = []
        for s in signals:
            sl = s.get('stop_loss_price')
            prev_rows.append({
                'Strategie': s['strategy'],
                'Ticker':    s['ticker'],
                'Signal':    s['signal'],
                'Stück':     s['qty'],
                'Wert':      s.get('value', 0),
                'CCY':       s.get('currency', ''),
                'Stop Loss': f"{sl:.2f}" if sl else '—',
            })
        st.dataframe(pd.DataFrame(prev_rows), use_container_width=True, hide_index=True)

        col_ok, col_cancel = st.columns(2)
        with col_ok:
            if st.button('✅ Alle ausführen', type='primary', use_container_width=True):
                broker = self._broker()
                mode   = self._mode()
                ok, failed = 0, 0
                for s in signals:
                    stop_p = s.get('stop_loss_price')
                    result = broker.submit_order(
                        broker_symbol=s['broker_symbol'],
                        qty=s['qty'],
                        side=s['signal'],
                        stop_price=stop_p if stop_p and stop_p > 0 else None,
                    )
                    self.order_log.save(
                        mode=mode,
                        broker=broker_id,
                        strategy=s['strategy'],
                        ticker=s['ticker'],
                        broker_symbol=s['broker_symbol'],
                        action=s['signal'],
                        qty=s['qty'],
                        signal_price=s['price'],
                        order_id=result.order_id,
                        status=result.status,
                        signal_date=s['date'],
                        error_msg=result.error_msg or '',
                    )
                    if result.status == 'error':
                        st.error(f"{s['ticker']}: {result.error_msg}")
                        failed += 1
                    else:
                        ok += 1
                msg = f'{ok} Order(s) eingereicht'
                if failed:
                    msg += f', {failed} fehlgeschlagen'
                st.success(msg)
                st.rerun()
        with col_cancel:
            if st.button('Abbrechen', use_container_width=True):
                st.rerun()

    # ------------------------------------------------------------------ #
    #  Tab: Positions                                                      #
    # ------------------------------------------------------------------ #

    def _tab_positions(self):
        broker = self._broker()
        if not broker.is_connected():
            st.info('Broker not connected.')
            return

        mode, bid = self._mode(), self._broker_id()

        col_sync, _ = st.columns([1, 4])
        with col_sync:
            if st.button('↻ Refresh'):
                st.rerun()

        # ── Open Positions ──────────────────────────────────────────────
        st.markdown('#### Offene Positionen')
        try:
            positions = broker.get_positions()
        except Exception as e:
            st.error(f'Failed to load positions: {e}')
            positions = []

        if not positions:
            st.info('No open positions.')
        else:
            # Map broker symbol → strategy from order log
            strategy_map: dict[str, str] = {}
            for strat in self._enabled():
                for t in self.order_log.get_open_tickers(strat, mode, bid):
                    strategy_map[t] = strat

            rows = []
            for p in positions:
                rows.append({
                    'Ticker':       p.broker_symbol,
                    'Strategy':     strategy_map.get(p.broker_symbol, '—'),
                    'Qty':          p.qty,
                    'Avg Entry':    f"{p.avg_entry_price:,.2f}",
                    'Current':      f"{p.current_price:,.2f}",
                    'Market Value': f"{p.market_value:,.2f}",
                    'uPnL':         f"{p.unrealized_pnl:+,.2f}",
                    'uPnL %':       f"{p.unrealized_pnl_pct:+.2f}%",
                })

            sel_pos = st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                on_select='rerun',
                selection_mode='single-row',
                hide_index=True,
            )

            if sel_pos and sel_pos.get('selection', {}).get('rows'):
                row_idx = sel_pos['selection']['rows'][0]
                pos = positions[row_idx]
                st.markdown(f"**Ausgewählt:** {pos.broker_symbol}")
                if self._dry_run():
                    st.info('Dry run aktiv — Schließen nicht verfügbar.')
                else:
                    if st.button(f'✕ Position schließen: {pos.broker_symbol}', type='secondary'):
                        result = broker.close_position(pos.broker_symbol)
                        if result.status != 'error':
                            st.success(f'{pos.broker_symbol} wird geschlossen.')
                            self.order_log.save(
                                mode=mode, broker=bid,
                                strategy=strategy_map.get(pos.broker_symbol, ''),
                                ticker=pos.broker_symbol,
                                broker_symbol=pos.broker_symbol,
                                action='sell', qty=pos.qty,
                                signal_price=pos.current_price,
                                order_id=result.order_id,
                                status=result.status,
                                signal_date=pd.Timestamp.today().strftime('%Y-%m-%d'),
                            )
                            st.rerun()
                        else:
                            st.error(f'Fehler: {result.error_msg}')

        # ── Open Orders ─────────────────────────────────────────────────
        st.markdown('#### Offene Aufträge')
        try:
            open_orders = broker.get_orders('open')
        except Exception as e:
            st.error(f'Aufträge konnten nicht geladen werden: {e}')
            open_orders = []

        if not open_orders:
            st.info('Keine offenen Aufträge.')
        else:
            ord_rows = [{
                'ID':         o['id'][:8] + '…',
                'Symbol':     o['symbol'],
                'Seite':      o['side'],
                'Qty':        o['qty'],
                'Status':     o['status'],
                'Erstellt':   o['created_at'][:16],
                '_full_id':   o['id'],
            } for o in open_orders]
            ord_display = [c for c in ['ID', 'Symbol', 'Seite', 'Qty', 'Status', 'Erstellt']]

            sel_ord = st.dataframe(
                pd.DataFrame(ord_rows)[ord_display],
                use_container_width=True,
                on_select='rerun',
                selection_mode='single-row',
                hide_index=True,
            )

            if sel_ord and sel_ord.get('selection', {}).get('rows'):
                row_idx = sel_ord['selection']['rows'][0]
                chosen   = ord_rows[row_idx]
                full_id  = chosen['_full_id']
                symbol   = chosen['Symbol']
                st.markdown(f"**Ausgewählt:** {symbol} — ID: `{full_id[:16]}…`")
                if st.button(f'✕ Auftrag stornieren: {symbol}', type='secondary',
                             key='btn_cancel_order'):
                    result = broker.cancel_order(full_id)
                    if result.status == 'cancelled':
                        st.success(f'Auftrag {full_id[:8]}… storniert.')
                        st.rerun()
                    else:
                        st.error(f'Stornierung fehlgeschlagen: {result.error_msg}')

        # ── Manueller Auftrag ───────────────────────────────────────────
        st.markdown('#### Manueller Auftrag')
        with st.expander('📝 Order manuell eingeben', expanded=False):
            with st.form('form_manual_order'):
                col_sym, col_qty, col_side = st.columns([2, 1, 1])
                with col_sym:
                    man_symbol = st.text_input(
                        'Symbol  (z.B. AAPL)',
                        help='Alpaca-Symbol — Groß-/Kleinschreibung egal',
                    )
                with col_qty:
                    man_qty = st.number_input('Anzahl', min_value=1, step=1, value=1)
                with col_side:
                    man_side = st.selectbox('Seite', ['buy', 'sell'])
                submitted = st.form_submit_button('▶ Order senden', type='primary')

            if submitted:
                sym = man_symbol.strip().upper()
                if not sym:
                    st.error('Symbol darf nicht leer sein.')
                elif self._dry_run():
                    st.info(
                        f'🧪 Dry run: {man_side.upper()} {int(man_qty)}× {sym} — '
                        'kein Auftrag gesendet.'
                    )
                else:
                    result = broker.submit_order(
                        broker_symbol=sym,
                        qty=float(man_qty),
                        side=man_side,
                    )
                    if result.status != 'error':
                        short_id = result.order_id[:8] if result.order_id else '—'
                        st.success(
                            f'{man_side.upper()} {int(man_qty)}× {sym} eingereicht '
                            f'(ID: {short_id}…)'
                        )
                        self.order_log.save(
                            mode=mode, broker=bid,
                            strategy='manual',
                            ticker=sym,
                            broker_symbol=sym,
                            action=man_side,
                            qty=float(man_qty),
                            signal_price=0.0,
                            order_id=result.order_id,
                            status=result.status,
                            signal_date=pd.Timestamp.today().strftime('%Y-%m-%d'),
                        )
                        st.rerun()
                    else:
                        st.error(f'Order fehlgeschlagen: {result.error_msg}')

    # ------------------------------------------------------------------ #
    #  Tab: History                                                        #
    # ------------------------------------------------------------------ #

    def _tab_history(self):
        mode, bid = self._mode(), self._broker_id()
        df = self.order_log.get_orders_df(mode=mode, broker=bid)

        if df.empty:
            st.info('No executed orders yet.')
            return

        filled = df[df['status'] == 'filled'].copy()
        if not filled.empty and 'fill_price' in filled.columns and 'signal_price' in filled.columns:
            filled['pnl'] = (filled['fill_price'] - filled['signal_price']) * filled['qty']
            sells = filled[filled['action'] == 'sell']
            total_pnl = sells['pnl'].sum() if not sells.empty else 0.0
        else:
            total_pnl = 0.0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Total orders', len(df))
        c2.metric('Filled',       len(filled))
        c3.metric('Errors',       int((df['status'] == 'error').sum()))
        c4.metric('Realized PnL', f'{total_pnl:+,.2f}')

        show_cols = [c for c in
            ['submitted_at', 'strategy', 'ticker', 'action', 'qty',
             'signal_price', 'fill_price', 'status', 'error_msg']
            if c in df.columns]
        st.dataframe(df[show_cols].reset_index(drop=True), use_container_width=True)

    # ------------------------------------------------------------------ #
    #  Tab: Compare                                                        #
    # ------------------------------------------------------------------ #

    def _tab_compare(self):
        st.markdown('#### Backtest vs. Paper Trading')

        # ── 1. Load backtest data ──────────────────────────────────────────
        # Priority: session_state (set by multi_transaction on same page load)
        #           → trading.db (persisted from previous run)
        #           → calculate on demand via button
        backtest_df: Optional[pd.DataFrame] = st.session_state.get('multi_trades_df')

        if backtest_df is None or (isinstance(backtest_df, pd.DataFrame) and backtest_df.empty):
            # Try DB fallback (survives page navigation)
            backtest_df = self.order_log.get_backtest_df(self.username)
            if not backtest_df.empty:
                st.session_state['multi_trades_df'] = backtest_df  # warm cache

        col_info, col_btn = st.columns([4, 1])
        has_backtest = backtest_df is not None and not backtest_df.empty

        with col_info:
            if has_backtest:
                saved_at = ''
                if 'saved_at' in backtest_df.columns:
                    saved_at = backtest_df['saved_at'].iloc[0][:16]
                strategies = (backtest_df['strategy'].unique().tolist()
                              if 'strategy' in backtest_df.columns else [])
                st.caption(
                    f"Backtest: {len(backtest_df)} trades | "
                    f"Strategien: {', '.join(str(s) for s in strategies)}"
                    + (f" | Stand: {saved_at}" if saved_at else '')
                )
            else:
                st.info(
                    'Noch keine Backtest-Daten. Klicke **Berechnen** oder führe die '
                    '[Multi-Strategies-Simulation](/?multi=true) aus.'
                )

        with col_btn:
            st.link_button('▶ Multi-Strategies', '/?multi=true',
                           help='Simulation dort starten — Ergebnis erscheint automatisch hier')

        if not has_backtest:
            return

        # ── 2. Chart ──────────────────────────────────────────────────────
        paper_df = self.order_log.get_orders_df(mode='paper', broker='alpaca')

        fig = go.Figure()

        bt = backtest_df.sort_values('sellDate').copy()
        if 'cum_gain' not in bt.columns or bt['cum_gain'].isna().all():
            bt['cum_gain'] = bt['gain'].cumsum()
        fig.add_trace(go.Scatter(
            x=bt['sellDate'], y=bt['cum_gain'],
            name='Backtest (Simulation)',
            line=dict(color='royalblue', width=2),
        ))

        if not paper_df.empty:
            sells = paper_df[
                (paper_df['status'] == 'filled') & (paper_df['action'] == 'sell')
            ].copy()
            if not sells.empty:
                sells = sells.sort_values('filled_at')
                sells['gain'] = (sells['fill_price'] - sells['signal_price']) * sells['qty']
                sells['cum_gain'] = sells['gain'].cumsum()
                fig.add_trace(go.Scatter(
                    x=sells['filled_at'], y=sells['cum_gain'],
                    name='Paper Trading (Alpaca)',
                    line=dict(color='darkorange', width=2),
                ))

        fig.update_layout(
            xaxis_title='Date',
            yaxis_title='Kumulierter Gewinn',
            height=450,
            legend=dict(orientation='h', yanchor='bottom', y=1.02),
        )
        st.plotly_chart(fig, use_container_width=True)

        # ── 3. Summary table ──────────────────────────────────────────────
        if 'strategy' in bt.columns:
            summary = (bt.groupby('strategy')['gain']
                       .agg(Trades='count', TotalGain='sum', AvgGain='mean')
                       .reset_index())
            st.dataframe(summary, use_container_width=True, hide_index=True)

    # ------------------------------------------------------------------ #
    #  Tab: Settings                                                       #
    # ------------------------------------------------------------------ #

    def _tab_settings(self):
        cfg = self._broker_config()

        # ---- Alpaca -------------------------------------------------- #
        st.markdown('#### Alpaca Paper Trading — Connection')
        st.caption(
            'API credentials are read from the encrypted key store (ksplib). '
            'New entries can be added under **Admin → API Credentials**.'
        )

        current_ksp_name = self.sys_config.get_value('alpaca_ksp_name', 'av-paper')
        with st.form('form_alpaca_ksp'):
            ksp_name = st.text_input(
                'KSP API Name',
                value=current_ksp_name,
                help='Name of the key store entry, e.g. "av-paper". user = API Key, password = API Secret.',
            )
            if st.form_submit_button('Save'):
                self.sys_config.set_value('alpaca_ksp_name', ksp_name)
                st.success(f'KSP name "{ksp_name}" saved.')
                st.rerun()

        # Show live connection status after name is set
        if cfg.get('alpaca_key'):
            broker = self._broker()
            if broker.is_connected():
                st.success(f'🟢 Connected to Alpaca Paper (KSP: "{current_ksp_name}")')
            else:
                st.error(f'🔴 Connection failed — check KSP entry "{current_ksp_name}".')
        else:
            st.warning(f'KSP entry "{current_ksp_name}" not found or empty.')

        # ---- Manual connection test ---------------------------------- #
        with st.expander('🔌 Connection test — enter credentials manually'):
            st.caption(
                'Test credentials without storing them. Useful for verifying a new key '
                'before adding it to the key store.'
            )
            with st.form('form_alpaca_test'):
                col_l, col_r = st.columns(2)
                with col_l:
                    test_key = st.text_input('API Key', type='password', key='test_alpaca_key')
                with col_r:
                    test_secret = st.text_input('API Secret', type='password', key='test_alpaca_secret')
                test_paper = st.checkbox('Paper account', value=True, key='test_alpaca_paper')
                run_test = st.form_submit_button('▶ Run test', type='primary')

            if run_test:
                if not test_key or not test_secret:
                    st.error('API Key and API Secret are required.')
                else:
                    self._run_alpaca_connection_test(test_key, test_secret, test_paper)

        # ---- IBKR ---------------------------------------------------- #
        st.markdown('#### IBKR Live Trading — Connection *(Phase 3)*')
        with st.form('form_ibkr_creds'):
            host = st.text_input('IB Gateway / TWS Host', value=cfg.get('ibkr_host', '127.0.0.1'))
            port = st.text_input('Port  (7497 = Paper, 7496 = Live)', value=str(cfg.get('ibkr_port', '7497')))
            if st.form_submit_button('Save'):
                self.sys_config.set_value('ibkr_host', host)
                self.sys_config.set_value('ibkr_port', port)
                st.success('IBKR connection parameters saved.')

        # ---- Ticker mapping ------------------------------------------ #
        st.markdown('#### Ticker Mapping')
        st.caption(
            'Resolved automatically via suffix rules. Use this to manually fix edge cases '
            '(ADRs, dual listings). Manual entries always override the automatic cache.'
        )

        overrides = self.resolver.get_all_overrides()
        if overrides:
            over_df = pd.DataFrame(overrides)
            sel = st.dataframe(
                over_df,
                use_container_width=True,
                on_select='rerun',
                selection_mode='single-row',
            )
            if sel and sel.get('selection', {}).get('rows'):
                row_idx = sel['selection']['rows'][0]
                ticker_to_del = over_df.iloc[row_idx]['yahoo_ticker']
                if st.button(f'🗑 Delete override for {ticker_to_del}', type='secondary'):
                    self.resolver.delete_override(ticker_to_del)
                    st.rerun()
        else:
            st.info('No manual overrides defined.')

        with st.expander('Add new override'):
            with st.form('form_add_override'):
                yft  = st.text_input('Yahoo Ticker  (e.g. SAP.DE)')
                als  = st.text_input('Alpaca Symbol  (leave empty = not tradeable on Alpaca)')
                ibks = st.text_input('IBKR Symbol  (e.g. SAP)')
                ibkx = st.text_input('IBKR Exchange  (e.g. XETRA)')
                ibkc = st.text_input('IBKR Currency  (e.g. EUR)')
                note = st.text_input('Note  (optional)')
                if st.form_submit_button('Add / overwrite'):
                    if yft:
                        self.resolver.save_override(
                            yft, als or None, ibks or None, ibkx or None, ibkc or None, note
                        )
                        st.success(f'Override for {yft.upper()} saved.')
                        st.rerun()
                    else:
                        st.error('Yahoo Ticker must not be empty.')

    # ------------------------------------------------------------------ #
    #  Alpaca connection test                                              #
    # ------------------------------------------------------------------ #

    def _run_alpaca_connection_test(self, api_key: str, api_secret: str, paper: bool):
        import pandas as pd

        mode_label = 'Paper' if paper else 'Live'
        results: list[dict] = []

        def row(check: str, status: str, detail: str = '') -> dict:
            return {'Check': check, 'Status': status, 'Detail': detail}

        # 0. Package availability
        try:
            from alpaca.trading.client import TradingClient  # noqa: F401
            results.append(row('Package alpaca-py', '✅ Installed', ''))
        except ImportError as e:
            results.append(row('Package alpaca-py', '❌ Missing', f'{e} — run: pip install alpaca-py'))
            st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
            st.error('Install the package first: `pip install alpaca-py`')
            return

        from tradinglib.broker_alpaca import AlpacaBroker
        broker = AlpacaBroker(api_key=api_key, secret_key=api_secret, paper=paper)

        # 1. Basic connectivity
        try:
            connected = broker.is_connected()
            if connected:
                results.append(row('Connection', '✅ OK', f'Alpaca {mode_label} reachable'))
            else:
                results.append(row('Connection', '❌ Failed', 'is_connected() returned False'))
        except Exception as e:
            results.append(row('Connection', '❌ Error', str(e)))
            st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
            return

        # 2. Account info
        try:
            acct = broker.get_account_info()
            results.append(row(
                'Account',
                '✅ OK',
                f'Equity: {acct.equity:,.2f} {acct.currency} | '
                f'Buying power: {acct.buying_power:,.2f} | '
                f'Cash: {acct.cash:,.2f}',
            ))
        except Exception as e:
            results.append(row('Account', '❌ Error', str(e)))

        # 3. Positions
        try:
            positions = broker.get_positions()
            results.append(row('Positions', '✅ OK', f'{len(positions)} open position(s)'))
        except Exception as e:
            results.append(row('Positions', '❌ Error', str(e)))

        # 4. Orders (read-only)
        try:
            orders = broker.get_orders(status='open')
            results.append(row('Orders', '✅ OK', f'{len(orders)} open order(s)'))
        except Exception as e:
            results.append(row('Orders', '❌ Error', str(e)))

        # 5. Asset lookup (sanity check — no order placed)
        try:
            from alpaca.trading.client import TradingClient
            client = TradingClient(api_key=api_key, secret_key=api_secret, paper=paper)
            asset = client.get_asset('AAPL')
            results.append(row(
                'Asset lookup',
                '✅ OK',
                f'AAPL → {asset.name}, tradable={asset.tradable}, shortable={asset.shortable}',
            ))
        except Exception as e:
            results.append(row('Asset lookup', '❌ Error', str(e)))

        st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
