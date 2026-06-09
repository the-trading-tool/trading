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
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" role="img">
  <title>Trading Tool</title>
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
  <rect x="135" y="118" width="7" height="22" fill="#555" rx="1"/>
  <rect x="146" y="110" width="7" height="30" fill="#666" rx="1"/>
  <rect x="157" y="102" width="7" height="38" fill="#888" rx="1"/>
  <line x1="133" y1="140" x2="166" y2="140" stroke="#555" stroke-width="0.5"/>
</svg>
"""


def render_logo(region=st, max_width: str = "220px", margin_bottom: str = "1.5rem"):
    """Render the centered Arbor Private Capital SVG logo into region, scaled to max_width."""
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
    """Gibt den Investitionsbetrag für eine Strategie-Gruppe zurück."""
    inner = transactions.get(group_key, {})
    if isinstance(inner, dict) and 'invest' in inner:
        return inner.get('invest', 0)
    # 3-stufig: erstes Sub-Element nehmen
    for pos in inner.values():
        if isinstance(pos, dict) and 'invest' in pos:
            return pos.get('invest', 0)
    return 0


class BannerPage():

    @property
    def ttl(self):
        """Return the localised page title string."""
        return t('banner.title')

    def __init__(self, username='admin', region=st, authenticator=None):
        """Initialize the dashboard page and immediately render it into region."""
        self.db_path = 'database'
        self.username = username
        self.region = region
        self.authenticator = authenticator
        self.sys_conf = sysconf.SystemConfig(username=self.username)
        self.system_currency = self.sys_conf.get_value('system_currency', 'EUR')
        self.render()

    # ── Strategie-Analyse ─────────────────────────────────────────────────────

    def _render_strategy_analysis(self, df: pd.DataFrame):
        """Render per-strategy KPI block (profit factor, win rate, best/worst trades)."""
        from tradinglib.backtest_widgets import render_strategy_analysis
        render_strategy_analysis(df, region=self.region, system_currency=self.system_currency)

    # ── Portfolio-Overlap ─────────────────────────────────────────────────────

    def _render_portfolio_overlap(self, df: pd.DataFrame):
        """Render a warning section when the same ticker appears in multiple open positions."""
        from tradinglib.backtest_widgets import render_portfolio_overlap
        render_portfolio_overlap(df, region=self.region)

    # ── Monatliche Heatmap ────────────────────────────────────────────────────

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

    # ── render ────────────────────────────────────────────────────────────────

    def render(self):
            """Render the full dashboard: language selector, KPIs, AI tip, trades table, charts, and analysis."""

            # ── Sprachauswahl (ganz oben, Deutsch als Voreinstellung) ─────
            _lang_options = list(SUPPORTED_LANGUAGES.keys())   # ['en', 'de']
            _lang_labels  = list(SUPPORTED_LANGUAGES.values()) # ['English', 'Deutsch']
            _current_lang = self.sys_conf.get_value('language', 'de') or 'de'
            if _current_lang not in _lang_options:
                _current_lang = 'de'
            _i18n.init_from_session(self.sys_conf)

            # ── Header-Zeile: Login (eingeklappt) links | Sprachauswahl rechts ──
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

            # ── Transaktionen parsen ──────────────────────────────────────
            self.total_invest = 0
            _raw_transactions = self.sys_conf.get_value('multi_transactions', self.sys_conf.transactions)
            transactions = ast.literal_eval(_raw_transactions) if isinstance(_raw_transactions, str) else _raw_transactions

            # Strategie-Namen (äußere Keys bei 3-stufig) und
            # Index-Map {index_name: max_num_assets über alle Strategien}
            _strategy_names: list[str]           = []
            _index_max_assets: dict[str, int]    = {}   # {index: max(num_assets)}
            _strategy_detail: dict[str, dict]    = {}   # {strategy: {index: num_assets}}

            for outer_key, inner in transactions.items():
                if isinstance(inner, dict) and 'num_assets' in inner:
                    # 2-stufig: outer_key IST der Index-Name
                    na  = int(inner.get('num_assets', 0))
                    inv = inner.get('invest', 0)
                    _index_max_assets[outer_key] = max(_index_max_assets.get(outer_key, 0), na)
                    self.total_invest += inv
                else:
                    # 3-stufig: outer_key = Strategie-Name, inner.keys() = Indizes
                    _strategy_names.append(outer_key)
                    _strategy_detail[outer_key] = {}
                    for index_name, pos in inner.items():
                        if not isinstance(pos, dict):
                            continue
                        na  = int(pos.get('num_assets', 0))
                        inv = pos.get('invest', 0)
                        # Max pro Index über alle Strategien
                        _index_max_assets[index_name] = max(
                            _index_max_assets.get(index_name, 0), na
                        )
                        self.total_invest += inv
                        _strategy_detail[outer_key][index_name] = na

            self.num_assets = sum(_index_max_assets.values())

            _fmt_index = lambda k: f'^{k}' if not k.startswith('^') else k
            _indices_display   = '  ·  '.join(_fmt_index(k) for k in _index_max_assets)
            _strategies_display = '  ·  '.join(_strategy_names) if _strategy_names else '—'

            # ── Gain berechnen ────────────────────────────────────────────
            gain = 0
            for _, row in df.iterrows():
                try:
                    if not math.isnan(row['cumulative_gain']):
                        gain = row['cumulative_gain']
                except Exception:
                    pass

            portfolio_value = round(gain + self.total_invest, 2)
            pct_gain = round((gain / self.total_invest) * 100, 1) if self.total_invest else 0

            # ── Metriken-Zeile ────────────────────────────────────────────
            _c1, _c2, _c3, _c4 = self.region.columns(4)
            _c1.metric(t('banner.metric_invest', year=year),
                       f"{self.total_invest:,.0f} {self.system_currency}")
            _c2.metric(t('banner.metric_value'),
                       f"{portfolio_value:,.2f} {self.system_currency}",
                       delta=f"{gain:+,.0f} {self.system_currency}")
            _c3.metric(t('banner.metric_performance', year=year),
                       f"{pct_gain} %", delta=f"{pct_gain:+.1f} %")
            _c4.metric(t('banner.metric_positions'), self.num_assets)

            # ── Strategien & Indizes ──────────────────────────────────────
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

            # ── Strategie-Erklärung (Expander) ────────────────────────────
            n_strat  = len(_strategy_names) if _strategy_names else 1
            n_idx    = len(_index_max_assets)
            sw       = t('banner.strategy_word_one') if n_strat == 1 else t('banner.strategy_word_many')

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

                # Detail-Tabelle: Strategie × Index × num_assets
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
            self.region.html(f"<h3>{t('banner.trades_since', year=year)}</h3>")
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

            # ── Analyse-Sektion (Heatmaps + KPIs + Overlap) ──────────────
            with st.spinner(t('banner.spinner_analysis')):
                self._render_per_strategy_heatmaps(df)   # einzeln, nebeneinander
                self._render_monthly_heatmap(df)          # zusammengefasst darunter
                self._render_strategy_analysis(df)
                self._render_portfolio_overlap(df)

            # ── Disclaimer ────────────────────────────────────────────────
            self._render_disclaimer()


class WelcomePage:
    """
    Onboarding-Seite für Erstinstallationen.

    Zeigt den Feature-Umfang je Lizenz-Tier und erklärt den Login-Prozess.
    Konsistent mit dem Setup-Wizard in first_run.py — verwendet hardcoded
    Text, da die Spracheinstellung beim ersten Start noch nicht bekannt ist.

    Verwendung:
        from tradinglib.banner_page import WelcomePage
        WelcomePage()
        WelcomePage(show_start_button=True, on_start=lambda: st.rerun())
    """

    # Feature-Listen je Tier: (Name, Kurzbeschreibung)
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

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def render(self):
        """Render the full welcome page: hero, tier comparison, getting-started steps, license status."""
        # ── Login-Header (oben rechts, eingeklappt) ───────────────────────
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
  <p class="wlc-hero-title">📈 Trading Tools</p>
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

        # Schritt-Boxen über die volle Breite
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

