import ast
from tradinglib import tools
import datetime as dt
import pandas as pd
import streamlit as st
from tradinglib import system_config as sysconf
from tradinglib import i18n as _i18n
from tradinglib.i18n import t, SUPPORTED_LANGUAGES
import plotly.express as px
import math
import logging

logger = logging.getLogger(__name__)


ARBOR_LOGO_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 195" role="img">
  <title>Silvesto, your growth tool.</title>
  <desc>Baum-Logo mit Balkendiagrammen</desc>

  <!-- Transparenter Hintergrund mit grauem Rahmen -->
  <rect width="198" height="198" x="1" y="1" fill="transparent" stroke="#888" stroke-width="0" rx="10"/>

  <!-- Baumstamm -->
  <rect x="94" y="118" width="12" height="30" fill="#888" rx="2"/>

  <!-- Wurzeln -->
  <path d="M94 145 Q83 151 76 149" fill="none" stroke="#666" stroke-width="1.2"/>
  <path d="M106 145 Q117 151 124 149" fill="none" stroke="#666" stroke-width="1.2"/>

  <!-- Baumkrone – unten -->
  <ellipse cx="100" cy="109" rx="36" ry="17" fill="#4a4a4a" stroke="#aaa" stroke-width="0.8"/>
  <!-- Baumkrone – Mitte -->
  <ellipse cx="100" cy="93" rx="27" ry="16" fill="#3a3a3a" stroke="#b8b8b8" stroke-width="0.8"/>
  <!-- Baumkrone – oben -->
  <ellipse cx="100" cy="79" rx="18" ry="13" fill="#2e2e2e" stroke="#d0d0d0" stroke-width="1"/>

  <!-- Balkendiagramm links -->
  <rect x="36" y="122" width="7" height="18" fill="#555" rx="1"/>
  <rect x="47" y="114" width="7" height="26" fill="#666" rx="1"/>
  <rect x="58" y="108" width="7" height="32" fill="#888" rx="1"/>
  <line x1="34" y1="140" x2="67" y2="140" stroke="#555" stroke-width="0.5"/>

  <!-- Balkendiagramm rechts -->
  <rect x="135" y="114" width="7" height="26" fill="#555" rx="1"/>
  <rect x="146" y="104" width="7" height="36" fill="#666" rx="1"/>
  <rect x="157" y="94" width="7" height="46" fill="#888" rx="1"/>
  <line x1="133" y1="140" x2="166" y2="140" stroke="#555" stroke-width="0.5"/>

  <!-- Tagline -->
  <text x="100" y="163" text-anchor="middle" font-family="Georgia, serif"
        font-size="16" fill="#aaa" letter-spacing="0.5">Silvesto,</text>
  <text x="100" y="182" text-anchor="middle" font-family="Georgia, serif"
        font-size="14" fill="#888" letter-spacing="0.5">the growth tool.</text>
</svg>
"""


def render_logo(region=st, max_width: str = "220px", margin_bottom: str = "1.5rem"):
    """Render the centered Silvesto Private Capital SVG logo into region, scaled to max_width."""
    style = (
        "<style>"
        ".arbor-logo-container{display:flex;justify-content:center;"
        f"margin-bottom:{margin_bottom};}}"
        f".arbor-logo-container svg{{width:{max_width};height:auto;}}"
        "</style>"
    )
    markup = ARBOR_LOGO_SVG.replace("\n", "")
    region.markdown(
        f'{style}<div class="arbor-logo-container">{markup}</div>',
        unsafe_allow_html=True,
    )


def _get_invest(transactions: dict, group_key: str) -> float:
    """Return the investment amount for a strategy group."""
    inner = transactions.get(group_key, {})
    if isinstance(inner, dict) and 'invest' in inner:
        return inner.get('invest', 0)
    # 3-level: take the first sub-element
    for pos in inner.values():
        if isinstance(pos, dict) and 'invest' in pos:
            return pos.get('invest', 0)
    return 0


class BannerPage():

    @property
    def ttl(self):
        """Return the localised page title string."""
        return t('banner.title')

    def __init__(self, username='admin', region=st, authenticator=None, dashboard_mode=False):
        """Initialize the dashboard page and immediately render it into region.

        dashboard_mode=True: skip login dialog and language selector (for use as
        the logged-in main page). False (default): full login-page layout.
        """
        self.db_path = 'database'
        self.username = username
        self.region = region
        self.authenticator = authenticator
        self.dashboard_mode = dashboard_mode
        self.sys_conf = sysconf.SystemConfig(username=self.username)
        self.system_currency = self.sys_conf.get_value('system_currency', 'EUR')
        self.render()

    # ── Strategy analysis ─────────────────────────────────────────────────────

    def _render_strategy_analysis(self, df: pd.DataFrame):
        """Render per-strategy KPI block (profit factor, win rate, best/worst trades)."""
        from tradinglib.backtest_widgets import render_strategy_analysis
        render_strategy_analysis(df, region=self.region, system_currency=self.system_currency)

    # ── Portfolio overlap ─────────────────────────────────────────────────────

    def _render_portfolio_overlap(self, df: pd.DataFrame):
        """Render a warning section when the same ticker appears in multiple open positions."""
        from tradinglib.backtest_widgets import render_portfolio_overlap
        render_portfolio_overlap(df, region=self.region)

    # ── Monthly heatmap ───────────────────────────────────────────────────────

    def _render_per_strategy_heatmaps(self, df: pd.DataFrame):
        """Render individual monthly return heatmaps, one column per strategy."""
        from tradinglib.backtest_widgets import render_per_strategy_heatmaps
        render_per_strategy_heatmaps(df, region=self.region, system_currency=self.system_currency)

    def _render_monthly_heatmap(self, df: pd.DataFrame):
        """Render the combined monthly return heatmap (all strategies aggregated)."""
        from tradinglib.backtest_widgets import render_monthly_heatmap
        self.region.divider()
        self.region.markdown(f"### 📅 {t('banner.heatmap_combined_header')}")
        render_monthly_heatmap(df, region=self.region, system_currency=self.system_currency)

    # ── Disclaimer ────────────────────────────────────────────────────────────

    def _render_disclaimer(self):
        """Render the collapsed disclaimer expander at the bottom of the page."""
        self.region.divider()
        with self.region.expander(t('banner.disclaimer_header'), expanded=False):
            st.markdown(t('banner.disclaimer_text'))

    # ── Scalable transactions dashboard ──────────────────────────────────────

    def _render_scalable_dashboard(self):
        """Render a summary dashboard for Scalable Capital own-transactions (trades.db)."""
        import sqlite3
        from tradinglib.tools import Tools
        from tradinglib.scalable_import import _compute_open_positions

        db_file = Tools().get_path(path=self.db_path, file_name='trades.db')
        try:
            with sqlite3.connect(db_file) as conn:
                raw = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
        except Exception:
            raw = pd.DataFrame()

        if raw is None or raw.empty:
            self.region.info(f"**{t('banner.scalable_no_data_title')}**\n\n{t('banner.scalable_no_data_body')}")
            return

        raw.columns = [c.strip() for c in raw.columns]
        col_lower = {c.lower(): c for c in raw.columns}

        def _col(name):
            return col_lower.get(name.lower(), name)

        act_col   = _col('action')
        price_col = _col('price')
        shares_col = _col('shares')
        value_col  = _col('value')
        ts_col     = _col('timestamp')

        if act_col in raw.columns:
            raw[act_col] = raw[act_col].astype(str).str.lower().str.strip()

        # ── Build a trades_df compatible with _compute_open_positions ─────
        trades_df = pd.DataFrame()
        if act_col in raw.columns:
            trade_mask = raw[act_col].isin(['buy', 'sell'])
            trades_df = raw[trade_mask].copy()
            # rename to match _compute_open_positions expectations
            rename_map = {}
            for src, dst in [('action', 'action'), ('ticker', 'ticker'), ('isin', 'isin'),
                              ('longname', 'longname'), ('shares', 'shares'),
                              ('price', 'price'), ('value', 'amount'), ('currency', 'currency'),
                              ('timestamp', 'timestamp')]:
                orig = col_lower.get(src)
                if orig and orig != dst:
                    rename_map[orig] = dst
                elif col_lower.get('longname') is None and col_lower.get('longName'):
                    rename_map[col_lower['longName']] = 'longname'
            if rename_map:
                trades_df = trades_df.rename(columns=rename_map)
            # normalise longname column name (could be 'longName')
            for candidate in ('longName', 'longname', 'description'):
                if candidate in trades_df.columns and 'longname' not in trades_df.columns:
                    trades_df = trades_df.rename(columns={candidate: 'longname'})
            if 'longname' not in trades_df.columns:
                trades_df['longname'] = ''
            # amount column must be numeric with buy negative / sell positive
            if 'amount' in trades_df.columns:
                trades_df['amount'] = pd.to_numeric(trades_df['amount'], errors='coerce').fillna(0.0)
                buy_mask = trades_df['action'] == 'buy'
                trades_df.loc[buy_mask, 'amount'] = -trades_df.loc[buy_mask, 'amount'].abs()
            if 'shares' in trades_df.columns:
                trades_df['shares'] = pd.to_numeric(trades_df['shares'], errors='coerce').fillna(0.0)
            if 'timestamp' in trades_df.columns:
                trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp'], errors='coerce')

        # ── Compute metrics ───────────────────────────────────────────────
        total_invested = 0.0
        total_proceeds = 0.0
        if not trades_df.empty and 'amount' in trades_df.columns:
            buy_rows  = trades_df[trades_df['action'] == 'buy']
            sell_rows = trades_df[trades_df['action'] == 'sell']
            total_invested = buy_rows['amount'].abs().sum()
            total_proceeds = sell_rows['amount'].abs().sum()

        open_pos_df = pd.DataFrame()
        try:
            with st.spinner('Lade aktuelle Kurse …'):
                open_pos_df = _compute_open_positions(trades_df, self.db_path)
        except Exception:
            pass

        current_value   = open_pos_df['current_value'].sum()  if not open_pos_df.empty else 0.0
        cost_basis_open = open_pos_df['cost_basis'].sum()      if not open_pos_df.empty else 0.0
        unrealized_pnl  = open_pos_df['unrealized_pnl'].sum()  if not open_pos_df.empty else 0.0
        realized_pnl    = total_proceeds - (total_invested - cost_basis_open) if total_invested else 0.0
        total_pnl       = unrealized_pnl + (realized_pnl if realized_pnl > -total_invested else 0.0)
        pnl_pct         = (total_pnl / total_invested * 100) if total_invested else 0.0
        n_open          = len(open_pos_df)

        # Dividends are already-realized cash income — reported separately, NOT
        # folded into the unrealized P&L above.
        total_dividends = 0.0
        if act_col in raw.columns and value_col in raw.columns:
            div_rows = raw[raw[act_col] == 'dividend']
            total_dividends = pd.to_numeric(div_rows[value_col], errors='coerce').abs().sum()

        _c1, _c2, _c3, _c4, _c5 = self.region.columns(5)
        _c1.metric("Investiert gesamt", f"{total_invested:,.2f} {self.system_currency}")
        _c2.metric("Marktwert (offen)", f"{current_value:,.2f} {self.system_currency}",
                   delta=f"{unrealized_pnl:+,.2f} {self.system_currency}")
        _c3.metric("Unreal. G/V", f"{unrealized_pnl:+,.2f} {self.system_currency}",
                   delta=f"{(unrealized_pnl / cost_basis_open * 100) if cost_basis_open else 0.0:+.1f} %")
        _c4.metric("Dividenden (real.)", f"{total_dividends:,.2f} {self.system_currency}")
        _c5.metric("Offene Positionen", n_open)

        # ── Trades table ──────────────────────────────────────────────────
        disp = raw.copy()
        longname_col = col_lower.get('longname') or col_lower.get('longName', '')
        disp_cols = [c for c in [ts_col, act_col, _col('ticker'), _col('isin'),
                                  longname_col, shares_col, price_col, value_col,
                                  col_lower.get('fee', ''), col_lower.get('tax', ''),
                                  col_lower.get('currency', '')]
                     if c and c in disp.columns]
        disp = disp.sort_values(ts_col, ascending=False) if ts_col in disp.columns else disp
        self.region.dataframe(disp[disp_cols] if disp_cols else disp,
                              use_container_width=True, hide_index=True)

        # Excel export in Scalable Capital CSV layout (round-trips through import)
        try:
            from tradinglib.scalable_import import build_scalable_export_df
            _export_df = build_scalable_export_df(raw)
            tools.excel_download_button(
                _export_df, t('banner.export_scalable_xlsx'),
                'scalable_transactions.xlsx', region=self.region,
                key='dl_scalable_trades')
        except Exception:
            logger.debug('Scalable export button failed', exc_info=True)

        # ── Portfolio value over time ──────────────────────────────────────
        if not trades_df.empty and 'timestamp' in trades_df.columns:
            try:
                from tradinglib.portfolio_analysis import _fetch_close_series_for_portfolio
                from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

                # Build daily net-shares per ticker from trade history
                tdf_sorted = trades_df.dropna(subset=['timestamp']).copy()
                tdf_sorted['timestamp'] = pd.to_datetime(tdf_sorted['timestamp'], errors='coerce')
                tdf_sorted = tdf_sorted.dropna(subset=['timestamp']).sort_values('timestamp')
                tdf_sorted['shares_num'] = pd.to_numeric(tdf_sorted.get('shares', pd.Series(dtype=float)), errors='coerce').fillna(0)
                tdf_sorted['signed_shares'] = tdf_sorted.apply(
                    lambda r: r['shares_num'] if r['action'] == 'buy' else -r['shares_num'], axis=1
                )

                all_tickers = [t for t in tdf_sorted['ticker'].dropna().unique() if t and len(str(t)) > 1]
                first_date = tdf_sorted['timestamp'].min().normalize()
                today      = pd.Timestamp.now().normalize()
                date_range = pd.date_range(first_date, today, freq='D')

                # Fetch price series for all tickers in parallel
                with st.spinner('Lade Kursdaten für Portfolio-Chart …'):
                    price_map = {}
                    with ThreadPoolExecutor(max_workers=min(8, len(all_tickers))) as ex:
                        futs = {ex.submit(_fetch_close_series_for_portfolio, t, self.db_path): t for t in all_tickers}
                        for fut in _as_completed(futs):
                            tk = futs[fut]
                            try:
                                s = fut.result()
                                if s is not None and not s.empty:
                                    s.index = pd.to_datetime(s.index, errors='coerce').normalize()
                                    price_map[tk] = s.groupby(level=0).last()
                            except Exception:
                                pass

                # Precompute daily portfolio state: shares held + cost basis per ticker
                # Process trades chronologically once; snapshot state per calendar day.
                has_price_col = 'price' in tdf_sorted.columns
                daily_states = {}   # date → {ticker: (shares, cost_basis)}
                state = {}          # {ticker: [shares, cost_basis]}  (mutable)
                prev_day = None

                for _, row in tdf_sorted.iterrows():
                    day = row['timestamp'].normalize()
                    # Snapshot at start of each new day (before today's trades)
                    if prev_day is not None and day != prev_day:
                        daily_states[prev_day] = {tk: (v[0], v[1]) for tk, v in state.items() if v[0] > 0.0001}
                    prev_day = day

                    tk  = row.get('ticker', '')
                    sh  = abs(float(row.get('shares_num', 0) or 0))
                    px_ = abs(float(row.get('price', 0) or 0)) if has_price_col else 0.0

                    if tk not in state:
                        state[tk] = [0.0, 0.0]

                    if row['action'] == 'buy' and sh > 0:
                        state[tk][0] += sh
                        state[tk][1] += sh * px_
                    elif row['action'] == 'sell' and sh > 0 and state[tk][0] > 0.0001:
                        sell_frac = min(sh / state[tk][0], 1.0)
                        state[tk][1] -= state[tk][1] * sell_frac
                        state[tk][0] = max(state[tk][0] - sh, 0.0)

                # Snapshot the final day
                if prev_day is not None:
                    daily_states[prev_day] = {tk: (v[0], v[1]) for tk, v in state.items() if v[0] > 0.0001}

                # Fill forward: carry last known state to each calendar day
                value_rows = []
                last_state = {}
                for day in date_range:
                    if day in daily_states:
                        last_state = daily_states[day]
                    if not last_state:
                        continue
                    day_value   = 0.0
                    day_invested = 0.0
                    for tk, (sh, cost) in last_state.items():
                        ps = price_map.get(tk)
                        if ps is None or ps.empty:
                            continue
                        past = ps[ps.index <= day]
                        if past.empty:
                            continue
                        day_value    += sh * float(past.iloc[-1])
                        day_invested += cost   # cost already = shares × avg_buy_price
                    if day_value > 0:
                        value_rows.append({'date': day,
                                           'Portfoliowert': round(day_value, 2),
                                           'Investiert':    round(day_invested, 2)})

                if value_rows:
                    import plotly.graph_objects as go
                    from plotly.subplots import make_subplots

                    vdf = pd.DataFrame(value_rows)
                    vdf['Gewinn'] = vdf['Portfoliowert'] - vdf['Investiert']

                    fig_sc = make_subplots(
                        rows=2, cols=1,
                        shared_xaxes=True,
                        row_heights=[0.65, 0.35],
                        vertical_spacing=0.06,
                        subplot_titles=('Portfolio-Entwicklung', 'Unrealisierter Gewinn / Verlust'),
                    )

                    # Upper panel: portfolio value + invested with fill between
                    fig_sc.add_trace(go.Scatter(
                        x=vdf['date'], y=vdf['Investiert'],
                        name='Investiert', line=dict(dash='dash', color='grey', width=1),
                        fill=None,
                    ), row=1, col=1)
                    fig_sc.add_trace(go.Scatter(
                        x=vdf['date'], y=vdf['Portfoliowert'],
                        name='Portfoliowert', line=dict(color='#4da6ff', width=2),
                        fill='tonexty',
                        fillcolor='rgba(77,166,255,0.15)',
                    ), row=1, col=1)

                    # Lower panel: gain/loss bar
                    colors = ['#2ecc71' if g >= 0 else '#e74c3c' for g in vdf['Gewinn']]
                    fig_sc.add_trace(go.Bar(
                        x=vdf['date'], y=vdf['Gewinn'],
                        name='G/V', marker_color=colors, showlegend=False,
                    ), row=2, col=1)
                    fig_sc.add_hline(y=0, line_dash='solid', line_color='grey', line_width=1, row=2, col=1)

                    fig_sc.update_layout(
                        height=480,
                        title_text='Portfolio-Entwicklung (Scalable Transactions)',
                        hovermode='x unified',
                        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                        margin=dict(t=60, b=40),
                    )
                    fig_sc.update_yaxes(title_text=self.system_currency, row=1, col=1)
                    fig_sc.update_yaxes(title_text=f'G/V ({self.system_currency})', row=2, col=1)
                    self.region.plotly_chart(fig_sc, use_container_width=True)

                    # ── Monthly performance heatmap ───────────────────────
                    try:
                        import plotly.graph_objects as _go_hm
                        _MONTH_ABBR = ['Jan','Feb','Mär','Apr','Mai','Jun',
                                       'Jul','Aug','Sep','Okt','Nov','Dez']
                        # Use Gewinn (unrealized G/V) delta per month — unaffected by new deposits
                        vdf_m = vdf.set_index('date')['Gewinn']
                        try:
                            monthly_end = vdf_m.resample('ME').last()
                        except Exception:
                            monthly_end = vdf_m.resample('M').last()
                        monthly_delta = monthly_end.diff()
                        monthly_delta = monthly_delta.dropna()
                        if not monthly_delta.empty:
                            ret_df = monthly_delta.reset_index()
                            ret_df.columns = ['date', 'gain']
                            ret_df['year']  = ret_df['date'].dt.year
                            ret_df['month'] = ret_df['date'].dt.month
                            pivot = (ret_df.groupby(['year', 'month'])['gain']
                                     .sum()
                                     .reset_index()
                                     .pivot(index='year', columns='month', values='gain'))
                            # Fill all 12 months (empty future months stay NaN)
                            for m in range(1, 13):
                                if m not in pivot.columns:
                                    pivot[m] = float('nan')
                            pivot = pivot[sorted(pivot.columns)]
                            col_labels = _MONTH_ABBR
                            row_labels  = [str(y) for y in pivot.index]
                            z_vals = pivot.values.tolist()
                            text_vals = [
                                [f'{v:+.0f}' if v == v else '' for v in row]
                                for row in z_vals
                            ]
                            fig_hm = _go_hm.Figure(_go_hm.Heatmap(
                                z=z_vals,
                                x=col_labels,
                                y=row_labels,
                                text=text_vals,
                                texttemplate='%{text}',
                                colorscale='RdYlGn',
                                zmid=0,
                                colorbar=dict(title=self.system_currency),
                                showscale=True,
                            ))
                            fig_hm.update_layout(
                                title=f'Monatliche Performance (Scalable Transactions, {self.system_currency})',
                                xaxis_title=None,
                                yaxis_title=None,
                                height=max(200, 100 + 80 * len(pivot)),
                                margin=dict(t=50, b=30),
                            )
                            self.region.plotly_chart(fig_hm, use_container_width=True)
                    except Exception as _hm_err:
                        st.warning(f'Heatmap-Fehler: {_hm_err}')
                        logger.debug('Scalable heatmap failed', exc_info=True)

            except Exception:
                logger.debug('Scalable portfolio chart failed', exc_info=True)

        # ── Open positions table ──────────────────────────────────────────
        if not open_pos_df.empty:
            self.region.markdown("#### Offene Positionen")
            disp_cols_op = [c for c in ['first_buy', 'ticker', 'longname', 'shares',
                                         'avg_price', 'current_price', 'pnl_pct',
                                         'cost_basis', 'current_value', 'unrealized_pnl',
                                         'weight_pct']
                            if c in open_pos_df.columns]
            _OPEN_FMT = dict(
                first_buy      = st.column_config.DatetimeColumn('Kauf ab', format='YYYY-MM-DD'),
                shares         = st.column_config.NumberColumn('Stück', format='%.4f'),
                avg_price      = st.column_config.NumberColumn('Ø Kaufkurs', format='%.4f'),
                current_price  = st.column_config.NumberColumn('Akt. Kurs', format='%.4f'),
                pnl_pct        = st.column_config.NumberColumn('Perf. %', format='%+.2f'),
                cost_basis     = st.column_config.NumberColumn(f'Einstand', format='%.2f'),
                current_value  = st.column_config.NumberColumn(f'Marktwert', format='%.2f'),
                unrealized_pnl = st.column_config.NumberColumn(f'G/V', format='%+.2f'),
                weight_pct     = st.column_config.NumberColumn('Gewicht %', format='%.1f'),
            )
            self.region.dataframe(
                open_pos_df[disp_cols_op],
                hide_index=True,
                use_container_width=True,
                column_config=_OPEN_FMT,
            )

    # ── render ────────────────────────────────────────────────────────────────

    def render(self):
            """Render the full dashboard: language selector, KPIs, AI tip, trades table, charts, and analysis."""

            st.markdown("""
<style>
div[data-testid="stMetric"] {
    background: var(--secondary-background-color);
    border: 1px solid rgba(49,51,63,.1);
    padding: 12px 14px;
    border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)

            # ── Language init ────────────────────────────────────────────
            _lang_options = list(SUPPORTED_LANGUAGES.keys())   # ['en', 'de']
            _lang_labels  = list(SUPPORTED_LANGUAGES.values()) # ['English', 'Deutsch']
            _current_lang = self.sys_conf.get_value('language', 'de') or 'de'
            if _current_lang not in _lang_options:
                _current_lang = 'de'
            _i18n.init_from_session(self.sys_conf)

            if not self.dashboard_mode:
                # ── Header row: login (collapsed) left | language selector right ──
                _login_col, _, _lang_col = st.columns([2, 5, 1])

                if self.authenticator:
                    with _login_col:
                        with st.expander("🔐 Anmelden", expanded=False):
                            try:
                                self.authenticator.login()
                            except Exception as _exc:
                                logger.warning("Login render error: %s", _exc)

                _sel_label = _lang_col.selectbox(
                    "🌐",
                    options=_lang_labels,
                    index=_lang_options.index(_current_lang),
                    key="banner_lang_sel",
                    label_visibility="collapsed",
                )
                _sel_code = _lang_options[_lang_labels.index(_sel_label)]
                if _sel_code != _current_lang:
                    self.sys_conf.set_value('language', _sel_code)
                    _i18n.load_language(_sel_code)
                    st.rerun()

            if not self.dashboard_mode:
                st.title(self.ttl)

            year = dt.datetime.now().year

            db = tools.Db_tools(db_path=self.db_path, database_name=f'trades{year}.db')
            db.conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT, buyDate TEXT, sellDate TEXT,
                    num_assets REAL, invest REAL, profit REAL, cumulative_gain REAL
                )
            """)
            db.conn.commit()
            df = pd.read_sql('select * from trades', db.conn)
            db.conn.close()

            # ── Parse transactions ────────────────────────────────────────
            self.total_invest = 0
            _raw_transactions = self.sys_conf.get_value('multi_transactions', self.sys_conf.transactions)
            transactions = ast.literal_eval(_raw_transactions) if isinstance(_raw_transactions, str) else _raw_transactions

            # Strategy names (outer keys for 3-level) and
            # index map {index_name: max_num_assets across all strategies}
            _strategy_names: list[str]           = []
            _index_max_assets: dict[str, int]    = {}   # {index: max(num_assets)}
            _strategy_detail: dict[str, dict]    = {}   # {strategy: {index: num_assets}}

            for outer_key, inner in transactions.items():
                if isinstance(inner, dict) and 'num_assets' in inner:
                    # 2-level: outer_key IS the index name
                    na  = int(inner.get('num_assets', 0))
                    inv = inner.get('invest', 0)
                    _index_max_assets[outer_key] = max(_index_max_assets.get(outer_key, 0), na)
                    self.total_invest += inv
                else:
                    # 3-level: outer_key = strategy name, inner.keys() = indices
                    _strategy_names.append(outer_key)
                    _strategy_detail[outer_key] = {}
                    for index_name, pos in inner.items():
                        if not isinstance(pos, dict):
                            continue
                        na  = int(pos.get('num_assets', 0))
                        inv = pos.get('invest', 0)
                        # Max per index across all strategies
                        _index_max_assets[index_name] = max(
                            _index_max_assets.get(index_name, 0), na
                        )
                        self.total_invest += inv
                        _strategy_detail[outer_key][index_name] = na

            self.num_assets = sum(_index_max_assets.values())

            _fmt_index = lambda k: f'^{k}' if not k.startswith('^') else k
            _indices_display   = '  ·  '.join(_fmt_index(k) for k in _index_max_assets)
            _strategies_display = '  ·  '.join(_strategy_names) if _strategy_names else '—'

            # ── Calculate gain ────────────────────────────────────────────
            gain = 0
            for _, row in df.iterrows():
                try:
                    if not math.isnan(row['cumulative_gain']):
                        gain = row['cumulative_gain']
                except Exception:
                    pass

            portfolio_value = round(gain + self.total_invest, 2)
            pct_gain = round((gain / self.total_invest) * 100, 1) if self.total_invest else 0

            # ── Banner note toggle ────────────────────────────────────────
            _bn_enabled = self.sys_conf.get_value('banner_note_enabled', True)
            if isinstance(_bn_enabled, str):
                _bn_enabled = _bn_enabled.lower() not in ('false', '0', 'no')

            # ── Scalable Transactions section ─────────────────────────────
            # Top divider only when the view header is shown (config.db
            # 'show_view_header', default off) — same switch as the sidebar header
            _show_hdr = str(self.sys_conf.get_value('show_view_header', False)).lower() in ('true', '1', 'yes', 'on')
            if _show_hdr:
                self.region.divider()
            self.region.markdown("## 💼 Scalable Transactions")
            self._render_scalable_dashboard()

            # ── Multi Strategies section ──────────────────────────────────
            self.region.divider()
            self.region.markdown("## 📈 Multi Strategies")

            if not _index_max_assets and df.empty:
                self.region.info(f"**{t('banner.multi_no_strategy_title')}**\n\n{t('banner.multi_no_strategy_body')}")
                self._render_disclaimer()
                return

            # Metrics row — belongs to Multi Strategies
            _c1, _c2, _c3, _c4 = self.region.columns(4)
            _c1.metric(t('banner.metric_invest', year=year),
                       f"{self.total_invest:,.0f} {self.system_currency}")
            _c2.metric(t('banner.metric_value'),
                       f"{portfolio_value:,.2f} {self.system_currency}",
                       delta=f"{gain:+,.0f} {self.system_currency}")
            _c3.metric(t('banner.metric_performance', year=year),
                       f"{pct_gain} %", delta=f"{pct_gain:+.1f} %")
            _c4.metric(t('banner.metric_positions'), self.num_assets)

            # Strategies & indices label
            if _strategy_names:
                self.region.markdown(
                    f"**{t('banner.strategies_label')}:** &nbsp; `{_strategies_display}`  &nbsp;|&nbsp;"
                    f"  **{t('banner.indices_label')}:** &nbsp; `{_indices_display}`",
                    unsafe_allow_html=True,
                )
            else:
                self.region.markdown(
                    f"**{t('banner.indices_label')}:** &nbsp; `{_indices_display}`",
                    unsafe_allow_html=True,
                )

            # Strategy explanation expander
            n_strat  = len(_strategy_names) if _strategy_names else 1
            n_idx    = len(_index_max_assets)
            sw       = t('banner.strategy_word_one') if n_strat == 1 else t('banner.strategy_word_many')

            if _bn_enabled:
                with self.region.expander(t('banner.strategy_expander'), expanded=False):
                    st.markdown(t('banner.strategy_how',
                                  n_strategies=n_strat, strategy_word=sw, n_indices=n_idx))
                    st.markdown("")
                    for step_key in ('banner.strategy_step1', 'banner.strategy_step2',
                                     'banner.strategy_step3', 'banner.strategy_step4'):
                        st.markdown(t(step_key))
                    st.markdown("")
                    st.info(t('banner.strategy_advantage'))
                    st.markdown("")

                    if _strategy_detail:
                        _rows = []
                        for sname, idx_dict in _strategy_detail.items():
                            for iname, na in idx_dict.items():
                                inv = 0
                                try:
                                    inv = transactions[sname][iname].get('invest', 0)
                                except Exception:
                                    pass
                                _rows.append({
                                    t('banner.strategy_col_strategy'): sname,
                                    t('banner.strategy_col_index'):    _fmt_index(iname),
                                    t('banner.strategy_col_max_assets'): na,
                                    t('banner.strategy_col_invest'):   f"{inv:,.0f} {self.system_currency}",
                                })
                        st.dataframe(_rows, use_container_width=True, hide_index=True)
                    else:
                        _rows = [
                            {t('banner.strategy_col_index'):    _fmt_index(k),
                             t('banner.strategy_col_max_assets'): v,
                             t('banner.strategy_col_invest'):   f"{_get_invest(transactions, k):,.0f} {self.system_currency}"}
                            for k, v in _index_max_assets.items()
                        ]
                        st.dataframe(_rows, use_container_width=True, hide_index=True)

            # AI tip
            if _bn_enabled:
                db_table = 'banner_notes'
                db = tools.Db_tools(db_path=self.db_path, database_name=f"{db_table}.db")
                db.conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {db_table} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticker TEXT,
                        text TEXT,
                        buyDate TEXT
                    )
                """)
                db.conn.commit()
                b_df = pd.read_sql(f'select * from {db_table}', db.conn)
                db.conn.close()
                try:
                    ticker   = b_df.iloc[-1]['ticker']
                    text     = b_df[b_df['ticker'] == ticker]['text'].iloc[0]
                    longname = ticker
                    date     = ''
                    try:
                        longname = df[df['ticker'] == ticker]['longName'].iloc[0]
                        date     = str(df[df['ticker'] == ticker]['buyDate'].iloc[-1])[:10]
                    except Exception:
                        pass
                    if text:
                        self.region.divider()
                        self.region.markdown(
                            f"### {t('banner.trading_tip', name=longname)}"
                            + (f"  <small style='color:grey'>({date})</small>" if date else ""),
                            unsafe_allow_html=True,
                        )
                        self.region.info(text)
                except Exception:
                    pass

            self.region.markdown(f"**{t('banner.trades_since', year=year)}**")
            df = df.sort_values(['sellDate','ticker'], ascending=[False,False])
            self.region.dataframe(df)
            
            if not df.empty and 'cumulative_gain' in df.columns:
                fig1 = px.line(
                            df,
                            x='sellDate',
                            y='cumulative_gain',
                           title=t('banner.total_gain'),
                            labels={'buyDate': 'Date', 'cumulative_gain': 'Gain'}
                        )
                st.plotly_chart(
                    fig1,
                    use_container_width=True,
                )

            # ── Analysis section (heatmaps + KPIs + overlap) ─────────────
            with st.spinner(t('banner.spinner_analysis')):
                self._render_per_strategy_heatmaps(df)   # einzeln, nebeneinander
                self._render_monthly_heatmap(df)          # zusammengefasst darunter
                self._render_strategy_analysis(df)
                self._render_portfolio_overlap(df)

            # ── Disclaimer ────────────────────────────────────────────────
            self._render_disclaimer()


class WelcomePage:
    """
    Onboarding page for first-time installations.

    Shows the feature set per license tier and explains the login process.
    Consistent with the setup wizard in first_run.py — uses hardcoded text
    because the language setting is not yet known on first start.

    Usage:
        from tradinglib.banner_page import WelcomePage
        WelcomePage()
        WelcomePage(show_start_button=True, on_start=lambda: st.rerun())
    """

    # Feature lists per tier: (name, short description)
    _FREE: list = [
        ("Asset Viewer",           "Charts, Kurs-Overlays, 36 Indikatoren"),
        ("Marktsuche",             "Ticker-Suche, Volltext, Live-Ticker"),
        ("Marktkarte",             "Branchen-Heat-Map"),
        ("Sektorrotation",         "RRG, Treemap, Sektor-Matrix"),
        ("Nachrichten & Sentiment","Yahoo Finance News + KI-Analyse"),
        ("Earnings Calendar",      "Quartalsergebnisse & Termine"),
        ("Eigene Transaktionen",   "Trades erfassen, Scalable CSV-Import"),
        ("Portfolio-Analyse",      "G&V, Allocation, Parity"),
        ("Pine Script Export",     "v5-Skripte für TradingView"),
        ("Compound-Simulation",    "Zinseszins-Rechner mit CPI-Daten"),
    ]

    _STRATEGY: list = [
        ("Strategie-Finder",       "Automatische Signal-Suche über alle Ticker"),
        ("Multi-Strategien",       "Kombinierte Buy/Sell-Bedingungen"),
        ("Performance Engine",     "Backtest-Historien, Score-Ranking"),
        ("Asset-Simulator",        "Portfolio-Simulation mit Signalen"),
        ("Alle Assets-Übersicht",  "Screener über gesamte Datenbank"),
        ("Scheduler",              "Automatische Daten-Updates im Hintergrund"),
    ]

    _TRADING: list = [
        ("Paper Trading",          "Simulation mit virtuellem Kapital"),
        ("Live Trading Bridge",    "Alpaca Paper & IBKR-Anbindung"),
        ("Order-Management",       "Limit-, Market- und Stop-Orders"),
        ("Positions-Übersicht",    "Offene & geschlossene Positionen"),
        ("Signalausführung",       "1-Klick-Execution aus Strategie-Signalen"),
    ]

    def __init__(self, region=None, show_start_button: bool = False, on_start=None, authenticator=None):
        """Set up and immediately render the welcome/onboarding page.

        show_start_button=True: show a primary 'Start app' button at the bottom.
        on_start: callable invoked when the start button is clicked.
        """
        self.region = region or st
        self.show_start_button = show_start_button
        self.on_start = on_start
        self.authenticator = authenticator
        self._inject_css()
        self.render()

    # ── CSS ──────────────────────────────────────────────────────────────────

    def _inject_css(self):
        """Inject the welcome-page CSS styles into the Streamlit app."""
        st.markdown("""
<style>
.wlc-hero {
    background: linear-gradient(135deg, #0f2027 0%, #203a43 55%, #2c5364 100%);
    border-radius: 14px;
    padding: 2.8rem 2rem 2.2rem;
    text-align: center;
    color: white;
    box-shadow: 0 4px 28px rgba(0,0,0,0.25);
}
.wlc-hero-title {
    font-size: 2.7rem;
    font-weight: 800;
    letter-spacing: -0.5px;
    margin: 0 0 0.55rem;
    line-height: 1.12;
}
.wlc-hero-sub {
    font-size: 1.15rem;
    opacity: 0.88;
    margin: 0 0 0.4rem;
}
.wlc-hero-tag {
    font-size: 0.8rem;
    opacity: 0.48;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    margin: 0;
}
.wlc-badge {
    display: inline-block;
    padding: 0.2rem 0.8rem;
    border-radius: 999px;
    font-size: 0.71rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.wlc-badge-free     { background: #d1fae5; color: #065f46; }
.wlc-badge-strategy { background: #dbeafe; color: #1e3a8a; }
.wlc-badge-trading  { background: #ede9fe; color: #4c1d95; }
.wlc-active {
    display: inline-block;
    background: #1d4ed8;
    color: white;
    font-size: 0.68rem;
    padding: 0.08rem 0.48rem;
    border-radius: 4px;
    font-weight: 700;
    margin-left: 0.4rem;
    vertical-align: middle;
}
.wlc-feat {
    font-size: 0.88rem;
    padding: 0.3rem 0;
    border-bottom: 1px solid #f2f2f2;
    line-height: 1.4;
}
.wlc-feat:last-child { border-bottom: none; }
.wlc-feat-name { font-weight: 600; color: #111; }
.wlc-feat-desc { color: #888; font-size: 0.78rem; }
.wlc-step-box {
    background: #f8fafc;
    border-left: 3px solid #2563eb;
    border-radius: 0 8px 8px 0;
    padding: 0.9rem 1rem;
    margin-bottom: 0.6rem;
}
.wlc-step-num {
    display: inline-block;
    background: #2563eb;
    color: white;
    border-radius: 50%;
    width: 1.5rem;
    height: 1.5rem;
    text-align: center;
    line-height: 1.5rem;
    font-size: 0.8rem;
    font-weight: 700;
    margin-right: 0.5rem;
    vertical-align: middle;
}
</style>""", unsafe_allow_html=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def render(self):
        """Render the full welcome page: hero, tier comparison, getting-started steps, license status."""
        # ── Login header (top right, collapsed) ───────────────────────────
        if self.authenticator:
            _, _login_col = st.columns([5, 2])
            with _login_col:
                with st.expander("🔐 Anmelden", expanded=False):
                    try:
                        self.authenticator.login()
                    except Exception as _exc:
                        logger.warning("Login render error: %s", _exc)

        self._hero()
        st.divider()
        self._tier_comparison()
        st.divider()
        self._getting_started()
        self._license_status()
        if self.show_start_button:
            st.divider()
            if st.button("▶ App starten", type="primary", use_container_width=True):
                if callable(self.on_start):
                    self.on_start()

    # ── Abschnitte ────────────────────────────────────────────────────────────

    def _hero(self):
        """Render the hero banner with the logo, app title, and tagline."""
        render_logo(region=st, max_width="200px", margin_bottom="0.8rem")
        st.markdown("""
<div class="wlc-hero">
  <p class="wlc-hero-title">📈 Silvesto, your growth tool.</p>
  <p class="wlc-hero-sub">Ihr persönliches Analyse-Werkzeug für den Kapitalmarkt</p>
  <p class="wlc-hero-tag">Offline &nbsp;·&nbsp; Lokal &nbsp;·&nbsp; Self-hosted</p>
</div>""", unsafe_allow_html=True)

    def _active_tier(self) -> str:
        """Return the current license tier as a lowercase string (e.g. 'free', 'strategy')."""
        try:
            from tradinglib.license_manager import get_license_info
            return (get_license_info().get("tier") or "Free").lower()
        except Exception:
            return "free"

    def _render_feature_list(self, features: list):
        """Render a styled HTML list of (feature_name, description) tuples."""
        for name, desc in features:
            st.markdown(
                f'<div class="wlc-feat">'
                f'✅ <span class="wlc-feat-name">{name}</span>'
                f' <span class="wlc-feat-desc">— {desc}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    def _tier_col(self, badge_cls: str, badge_label: str, title: str, caption: str,
                  features: list, includes_all: bool, excluded: list, is_active: bool):
        """Render a single tier column with badge, feature list, and active indicator."""
        active_tag = '<span class="wlc-active">Aktiv</span>' if is_active else ""
        st.markdown(
            f'<span class="wlc-badge {badge_cls}">{badge_label}</span>{active_tag}',
            unsafe_allow_html=True,
        )
        st.markdown(f"#### {title}")
        st.caption(caption)
        if includes_all:
            st.markdown("✅ **Alles aus Free**, plus:")
        self._render_feature_list(features)
        for item in excluded:
            st.markdown(f"❌ ~~{item}~~")

    def _tier_comparison(self):
        """Render the three-column Free / Strategy / Trading tier comparison."""
        st.subheader("Was ist in welcher Version enthalten?")
        active = self._active_tier()
        c_free, c_strat, c_trade = st.columns(3)

        with c_free:
            self._tier_col(
                "wlc-badge-free", "Inklusive",
                "🆓 Free", "Keine Lizenz erforderlich",
                self._FREE, False, [],
                is_active=(active == "free"),
            )
        with c_strat:
            self._tier_col(
                "wlc-badge-strategy", "Lizenzpflichtig",
                "⭐ Strategy Engine", "Backtesting & Strategie-Analyse",
                self._STRATEGY, True, ["Paper Trading", "Live Trading"],
                is_active=("strategy" in active),
            )
        with c_trade:
            self._tier_col(
                "wlc-badge-trading", "Lizenzpflichtig",
                "🚀 Paper / Live Trading", "Order-Ausführung & Trading-Bridge",
                self._TRADING, True, [],
                is_active=any(x in active for x in ("trading", "paper", "live")),
            )

    def _getting_started(self):
        """Render the step-by-step getting-started section with default login credentials."""
        st.subheader("🚀 Erste Schritte")

        # Step boxes across the full width
        st.markdown(
            '<div class="wlc-step-box">'
            '<span class="wlc-step-num">1</span> App im Browser öffnen — '
            'Standard: <code>http://localhost:8501</code>'
            '</div>'
            '<div class="wlc-step-box">'
            '<span class="wlc-step-num">2</span> Mit <b>admin</b> / <b>changeme</b> anmelden'
            '</div>'
            '<div class="wlc-step-box">'
            '<span class="wlc-step-num">3</span> Passwort über den ⚙️-Button ändern'
            '</div>'
            '<div class="wlc-step-box">'
            '<span class="wlc-step-num">4</span> Ticker konfigurieren & Kursdaten laden'
            '</div>',
            unsafe_allow_html=True,
        )

        # Login-Expander — schmale Mittelspalte
        _, col_mid, _ = st.columns([0.25, 0.5, 0.25])
        with col_mid:
            with st.expander("🔑 Standard-Zugangsdaten anzeigen", expanded=False):
                st.markdown(
                    "| | |\n"
                    "|---|---|\n"
                    "| **Benutzername** | `admin` |\n"
                    "| **Passwort** | `changeme` |\n"
                )
                st.warning(
                    "⚠️ Passwort nach dem ersten Login ändern!\n\n"
                    "Neuen Hash erzeugen:\n"
                    "```bash\n"
                    'python -c "import bcrypt; print(\n'
                    "  bcrypt.hashpw(b'MEIN_PW',\n"
                    "  bcrypt.gensalt()).decode())\"\n"
                    "```\n"
                    "Hash in **`config.yaml`** → `password:` eintragen, dann neu starten."
                )

        st.info(
            "**Tipp:** Die App benötigt keine Cloud-Verbindung — alle Daten liegen lokal "
            "in SQLite-Datenbanken unter `./database/`. "
            "Kurshistorie via `get_asset_data.py` oder über **Einstellungen → Scheduler** laden.",
            icon="💡",
        )

    def _license_status(self):
        """Render the collapsed license status expander showing tier, user, and expiry."""
        try:
            from tradinglib.license_manager import get_license_info
            info = get_license_info()
        except Exception:
            return

        with st.expander("📋 Lizenz-Status anzeigen", expanded=False):
            tier = info.get("tier", "Free")
            user = info.get("user", "")
            exp  = info.get("expires")
            err  = info.get("error")

            if err:
                st.warning(f"⚠️ {err}")
            elif tier == "Free":
                st.info(
                    "Keine `license.json` gefunden — läuft im **Free**-Modus.\n\n"
                    "Free enthält alle Analyse-Funktionen ohne Backtesting und Trading."
                )
            else:
                user_part = f" für **{user}**" if user else ""
                st.success(f"Lizenz aktiv — Tier: **{tier}**{user_part}")
                if exp:
                    st.caption(f"Gültig bis: {exp}")

            st.markdown(
                "Für Lizenz-Anfragen: `license.json` im App-Verzeichnis ablegen "
                "und die App neu starten."
            )

