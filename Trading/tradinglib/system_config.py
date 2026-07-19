import ast
import sqlite3
import json
import sys
import streamlit as st
import logging
from tradinglib import license_manager as lm
from tradinglib.tools import open_db

for name, l in logging.root.manager.loggerDict.items():
    if "streamlit" in name:
        l.disabled = True

try:
	sys.path.insert(0, "../../tradinglib")
except ImportError:
	pass

from tradinglib import tools
from tradinglib import help_text
from tradinglib.indicator import indicator  # Import the base class
from tradinglib import logging_config as lgc
from tradinglib import i18n

# Sidebar menu visibility — configurable via the admin "System" section
# (rendered from admin.py, stored under the global '_sidebar_menu_config'
# user namespace so it applies to every user, not just the editing admin).
# Only these are shown out of the box; everything else is admin opt-in.
DEFAULT_SIDEBAR_ITEMS = {
    'marketmap': True,
    'rotation': False,
    'market_overview': False,
    'correlation': False,
    'asset': True,
    'summary': False,
    'performance': False,
    'compound': False,
    'strategy_finder': False,
    'multi': False,
    'trading': False,
    'own_trades': True,
    'admin_ticker': True,
    'admin_database': False,
    'admin_credentials': True,
    'admin_system': True,
    'admin_scheduler': False,
    'admin_pine': False,
}

# (group label i18n key, [item keys in the group]) — drives both the admin
# checkbox editor and the sidebar's own render order.
SIDEBAR_MENU_GROUPS = [
    ('nav.group_market', ['marketmap', 'rotation', 'market_overview', 'correlation']),
    ('nav.group_assets', ['asset', 'summary', 'performance', 'compound']),
    ('nav.group_portfolio', ['strategy_finder', 'multi', 'trading', 'own_trades']),
    ('nav.group_admin', ['admin_ticker', 'admin_database', 'admin_credentials', 'admin_system', 'admin_scheduler', 'admin_pine']),
]

SIDEBAR_ITEM_LABEL_KEYS = {
    'marketmap': 'nav.market_map',
    'rotation': 'nav.sector_rotation',
    'market_overview': 'nav.market_overview',
    'correlation': 'nav.correlation',
    'asset': 'nav.asset_viewer',
    'summary': 'nav.asset_summary',
    'performance': 'nav.performance',
    'compound': 'nav.compound_simulation',
    'strategy_finder': 'nav.strategy_finder',
    'multi': 'nav.multi_strategies',
    'trading': 'nav.trading',
    'own_trades': 'nav.own_transactions',
    'admin_ticker': 'nav.admin_ticker',
    'admin_database': 'nav.admin_database',
    'admin_credentials': 'nav.admin_credentials',
    'admin_system': 'nav.admin_system',
    'admin_scheduler': 'nav.admin_scheduler',
    'admin_pine': 'nav.admin_pine',
}

# Namespace the global sidebar config is stored under (independent of
# whichever user happens to be logged in when reading or editing it).
SIDEBAR_MENU_CONFIG_USER = '_sidebar_menu_config'


class SystemConfig(tools.Db_tools):
    
#        'SPX': {'buy': '(momentum > rsi_ema) & (overallValueTrend > 53)', 'sell': '(overallValueTrend < 58)', 'num_assets': 7, 'invest': 17000, 'order_by': 'sortino'}, 
#        'GDAXI': {'buy': '(momentum > rsi_ema)', 'sell': '(overallValueTrend < 58)', 'num_assets': 7, 'invest': 15000, 'order_by': 'sortino'}, 
#        'MDAXI': {'buy': '(momentum > rsi_ema)', 'sell': '(overallValueTrend < 55)', 'num_assets': 7, 'invest': 10000, 'order_by': 'sortino'}, 
#        'SDAXI': {'buy': '(overallValueTrend >= 51)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 41)', 'num_assets': 3, 'invest': 8000, 'order_by': 'sortino'}
#        }

    # 3-stufig (Strategie -> Index -> Feld-Dict) -- multi_transaction.py iteriert
    # zwingend "for item in transactions: for id in transactions[item]: ...['buy']"
    # ohne Fallback auf die alte 2-stufige Form (Index direkt als aeusserer Key).
    # banner_page.py erkennt zwar beide Formen automatisch, Multi Strategies nicht.
    transactions = {
        'Value Trend Strategy': {
            'SPX': {'buy': '(overallValueTrend >= 69)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 59)', 'num_assets': 5, 'invest': 15000, 'order_by': 'sortino'},
            'GDAXI': {'buy': '(overallValueTrend >= 67)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 57)', 'num_assets': 5, 'invest': 15000, 'order_by': 'sortino'},
            'MDAXI': {'buy': '(overallValueTrend >= 60)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 50)', 'num_assets': 2, 'invest': 7000, 'order_by': 'sortino'},
            'SDAXI': {'buy': '(overallValueTrend >= 56)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 46)', 'num_assets': 3, 'invest': 10000, 'order_by': 'sortino'},
        },
    }
    
    def __init__(self, db_path="database", db_name="config.db", username='admin', region=st, is_admin=False, bare_mode=False):
        """Open config.db and ensure the config table exists.

        username: all keys are namespaced per user (stored as '<username>:<key>').
        is_admin: enables admin-only UI sections (logging, provider, scaling controls).
        bare_mode: suppresses the help dialog content (used in CLI/headless contexts).
        """
        self._db_path = self.get_path(path=db_path, file_name=db_name)
        self.username = username        
        self.is_admin = is_admin
        self.region = region
        self.bare_mode = bare_mode

        self._initialize_db()

    def _initialize_db(self):
        """creates config db."""
        with open_db(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """
            )
            conn.commit()
    
    def set_value(self, key: str, value: str):
        """saves a config value."""
        value_str = json.dumps(value)  # Serialise
        with open_db(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (f"{self.username}:{key}", value_str))
            conn.commit()
    
    def get_value(self, key: str, default=None) -> str:
        """Read a config value for the current user; returns default when the key is absent."""
        with open_db(self._db_path, readonly=True) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM config WHERE key = ?", (f"{self.username}:{key}",))
            result = cursor.fetchone()
            if result and result[0]:
                try:
                    return json.loads(result[0])
                except json.JSONDecodeError:
                    return result[0]  # Fall back if it is a plain string
            return default
    
    def delete_value(self, key: str):
        """Deletes a config value."""
        with open_db(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM config WHERE key = ?", (f"{self.username}:{key}",))
            conn.commit()

    @classmethod
    def sidebar_menu_config(cls, db_path="database"):
        """Return a SystemConfig instance scoped to the global sidebar-menu namespace.

        Sidebar visibility is one app-wide setting (edited by an admin, read by
        every user's nav render), so it is stored under a fixed pseudo-username
        rather than the current session's username.
        """
        return cls(db_path=db_path, username=SIDEBAR_MENU_CONFIG_USER)

    def get_sidebar_items(self) -> dict:
        """Return the global sidebar menu visibility map, merged with factory defaults."""
        stored = self.get_value('sidebar_items', None)
        items = dict(DEFAULT_SIDEBAR_ITEMS)
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k in items:
                    items[k] = bool(v)
        return items

    def set_sidebar_items(self, items: dict):
        """Persists the global sidebar menu visibility map."""
        self.set_value('sidebar_items', {k: bool(v) for k, v in items.items() if k in DEFAULT_SIDEBAR_ITEMS})

    def get_selectors(self, interval=None, period=None, overlays=None, oszilators=None):
        """Return (interval, period, overlays, oscillators) resolved from config with sensible defaults.

        None or [] inputs fall back to the stored config value; missing config falls back
        to hard-coded defaults ('60m', '1mo', ['heikin','bar','sup'], ['macd']).
        """
        if not interval:  # None, [], or ''
            interval = self.get_value('interval', '60m') or '60m'
        if isinstance(interval, list):
            interval = interval[0] or '60m'

        if not period:  # None, [], or ''
            period = self.get_value('period', '1mo') or '1mo'
        if isinstance(period, list):
            period = period[0] or '1mo'

        def _parse_list(raw):
            """Return raw as a Python list regardless of whether it arrived as a
            list (get_value already decoded JSON) or as a legacy string literal."""
            if isinstance(raw, list):
                return raw
            if not raw:
                return None
            import json as _json
            try:
                result = _json.loads(raw)
                if isinstance(result, list):
                    return result
            except Exception:
                pass
            try:
                result = ast.literal_eval(raw)
                if isinstance(result, list):
                    return result
            except Exception:
                pass
            return None

        if overlays is None or overlays == []:
            raw = self.get_value('overlay', None)
            parsed = _parse_list(raw) if raw is not None else None
            # Use factory default when nothing is stored OR stored value is empty
            overlays = parsed if parsed else ['heikin', 'bar', 'sup']

        if oszilators is None or oszilators == []:
            raw = self.get_value('oszilator', None)
            parsed = _parse_list(raw) if raw is not None else None
            # Use factory default when nothing is stored OR stored value is empty
            oszilators = parsed if parsed else ['macd']

        return(interval, period, overlays, oszilators)

    def get_idx_selected(self, v_list, v_key, default=0):
        """Return the index of the stored config value in v_list, or default when not found."""
        try:
            default = v_list.index(self.get_value(v_key))
        except Exception:
            pass    
        return default

    @st.dialog('Configuration', width='large')
    def render(self):  # noqa: C901
        """Render the full configuration dialog (interval, period, overlays, queries, etc.)."""

        def check_list(lst = None, overlay = ''):
            error_text = "Select a combination of either/and: "        
            ol = ""
            for t in lst:
                ol += "'"+t.split(" - ")[0]+"', "
            ms_str = " ".join(lst)
            _ov_val = self.get_value(overlay)
            _ov_list = _ov_val if isinstance(_ov_val, list) else ast.literal_eval(_ov_val) if isinstance(_ov_val, str) else []
            for itm in _ov_list:
                try:
                    pos = ms_str.index(itm)
                except Exception:
                    st.error(f"{error_text} [{ol}]")
                    pass

        # Imported here, not at module level: multi_select is UI-only, while
        # SystemConfig itself is imported by headless callers (run_agent).
        from tradinglib import multi_select

        intervals = multi_select.MultiCheckboxSelector.lists[0][1]
        periods = multi_select.MultiCheckboxSelector.lists[1][1]
        overlays = multi_select.MultiCheckboxSelector.lists[2][1]
        oszilators  = multi_select.MultiCheckboxSelector.lists[3][1]
        b_select = [True, False]
        s_select =[1,2,3,5]
        sr_select =[0.01,0.1,1,10,100,1000]
        # Entry page on a fresh load (mobile always overrides this with the Asset
        # Viewer). Keys mirror _START_PAGE_ROUTES in asset_analyzer.py.
        start_pages = ['dashboard', 'asset', 'summary', 'performance',
                       'own_trades', 'market_overview', 'marketmap', 'rotation']

        idx_b_select = self.get_idx_selected(b_select, 'logging',1)
        idx_rt_select = self.get_idx_selected(b_select, 'rt_prices',1)
        idx_s_select = self.get_idx_selected(s_select, 'ovt_smoothing',2)
        idx_sr_select = self.get_idx_selected(sr_select, 'sr_scaling',4)
        idx_period = self.get_idx_selected(periods, 'period',3)
        idx_mp_details = self.get_idx_selected(b_select, 'mp_details',1)
        idx_pine_export = self.get_idx_selected(b_select, 'pine_export', 1)
        idx_require_isin = self.get_idx_selected(b_select, 'require_isin', 1)
        idx_trailing_stop_enabled = self.get_idx_selected(b_select, 'trailing_stop_enabled', 1)
        idx_show_regime = self.get_idx_selected(b_select, 'show_regime', 1)
        idx_mark_nontrading = self.get_idx_selected(b_select, 'mark_nontrading_times', 0)
        idx_interval = self.get_idx_selected(intervals, 'interval',3)
        idx_start_page = self.get_idx_selected(start_pages, 'start_page', 0)
        self.region.write(i18n.t("cfg.user", username=self.username))

        # --- Language switcher ---
        lang_keys = list(i18n.SUPPORTED_LANGUAGES.keys())
        current_lang = i18n.current_language()
        try:
            lang_idx = lang_keys.index(current_lang)
        except ValueError:
            lang_idx = 0
        selected_lang = st.selectbox(
            i18n.t("cfg.language"),
            options=lang_keys,
            format_func=lambda x: i18n.SUPPORTED_LANGUAGES[x],
            index=lang_idx,
        )
        if selected_lang != current_lang:
            self.set_value("language", selected_lang)
            i18n.load_language(selected_lang)
            st.rerun()

        def _coerce_names(raw, fallback):
            """Normalise a config value / user text into a clean list[str] of
            indicator short-names. Accepts a real list, a JSON or Python-repr
            list string, or a plain comma/space separated string. This keeps the
            stored format a proper JSON list regardless of how it was entered, so
            both the ⚙-dialog and the checkbox selector agree on the format and
            a bare name like 'gan' no longer silently falls back to the factory
            defaults."""
            import re as _re
            if isinstance(raw, list):
                names = raw
            else:
                s = (raw or '').strip()
                names = None
                if s:
                    for parser in (json.loads, ast.literal_eval):
                        try:
                            val = parser(s)
                            if isinstance(val, list):
                                names = val
                                break
                        except Exception:
                            pass
                    if names is None:
                        names = [p for p in _re.split(r'[\s,]+', s) if p]
            names = [str(n).split(' - ')[0].strip() for n in (names or [])]
            names = [n for n in names if n]
            return names or list(fallback)

        tab_labels = [i18n.t("cfg.tab_general"), i18n.t("cfg.tab_charts"), i18n.t("cfg.tab_strategies")]
        if self.is_admin:
            tab_labels.append(i18n.t("cfg.tab_admin"))
        tabs = st.tabs(tab_labels)

        with tabs[0]:
            self.set_value('system_currency', st.text_input(i18n.t("cfg.currency"), self.get_value('system_currency', 'EUR')))
            self.set_value('trading_cost_pct', st.text_input(i18n.t("cfg.trading_cost"), self.get_value('trading_cost_pct', '0.6')))
            self.set_value('default_ticker', st.text_input(i18n.t("cfg.default_ticker"), self.get_value('default_ticker', '^GDAXI')) or '^GDAXI')
            self.set_value('interval', st.selectbox(i18n.t("cfg.default_interval"), intervals, idx_interval))
            self.set_value('period', st.selectbox(i18n.t("cfg.default_period"), periods, idx_period))
            self.set_value('start_page', st.selectbox(
                i18n.t("cfg.start_page"), start_pages, idx_start_page,
                format_func=lambda k: i18n.t(f"cfg.start_page_opt.{k}"),
                help=i18n.t("cfg.start_page_help"),
            ))
            self.set_value('tz_info', st.text_input(i18n.t("cfg.timezone"), self.get_value('tz_info', "Europe/Berlin")))
            self.set_value('monitored_assets', st.text_input(i18n.t("cfg.monitored_assets"), self.get_value('monitored_assets', "")))

        with tabs[1]:
            _ov_list = _coerce_names(self.get_value('overlay', None), ['bos', 'pre'])
            _ov_in = st.text_input(i18n.t("cfg.default_overlay"), ", ".join(_ov_list))
            self.set_value('overlay', _coerce_names(_ov_in, ['bos', 'pre']))
            check_list(overlays, 'overlay')
            _oz_list = _coerce_names(self.get_value('oszilator', None), ['adx', 'cci', 'ewo'])
            _oz_in = st.text_input(i18n.t("cfg.default_oscillator"), ", ".join(_oz_list))
            self.set_value('oszilator', _coerce_names(_oz_in, ['adx', 'cci', 'ewo']))
            check_list(oszilators, 'oszilator')
            self.set_value('mp_details', st.selectbox(i18n.t("cfg.mp_details"), b_select, idx_mp_details))
            self.set_value('pine_export', st.selectbox(i18n.t("cfg.pine_export"), b_select, idx_pine_export))
            self.set_value('show_regime', st.selectbox(i18n.t("cfg.show_regime"), b_select, idx_show_regime))
            self.set_value('mark_nontrading_times', st.selectbox(i18n.t("cfg.mark_nontrading_times"), b_select, idx_mark_nontrading, help=i18n.t("cfg.mark_nontrading_times_help")))

        with tabs[2]:
            self.set_value('multi_transactions', st.text_area(i18n.t("cfg.transactions"), self.get_value('multi_transactions', self.transactions)))
            self.set_value('buy_query', st.text_input(i18n.t("cfg.buy_query"), self.get_value('buy_query', '(ha_close > ha_open) & (Close > ha_ema_high) & (macd > macd_signal) & (rsi > 50) & (markov_regime < 2)')))
            self.set_value('sell_query', st.text_input(i18n.t("cfg.sell_query"), self.get_value('sell_query', '(ha_close < ha_open) & (Close < ha_ema_low)')))
            self.set_value('require_isin', st.selectbox(i18n.t("cfg.require_isin"), b_select, idx_require_isin))
            self.set_value('trailing_stop_enabled', st.selectbox(i18n.t("cfg.trailing_stop_enabled"), b_select, idx_trailing_stop_enabled))

        if self.is_admin:
            with tabs[3]:
                with st.expander("License", expanded=False):
                    info = lm.get_license_info()
                    st.write(i18n.t("cfg.license_tier", tier=info['tier']))
                    if info.get("user"):
                        st.write(i18n.t("cfg.license_licensee", user=info['user']))
                    if info.get("expires"):
                        st.write(i18n.t("cfg.license_expires", expires=info['expires']))
                    active = [f for f in lm.ALL_FEATURES if lm.has_feature(f)]
                    st.write(i18n.t("cfg.license_features", features=', '.join(active)))
                    if info.get("error"):
                        st.error(info["error"])
                    if not info["path_exists"]:
                        st.info(i18n.t("cfg.license_no_file"))
                    if st.button(i18n.t("cfg.reload_license")):
                        lm.reset_cache()
                        st.rerun()

                with st.expander(i18n.t("cfg.data_provider_header"), expanded=False):
                    import json as _json

                    def _read_app(key, default=None):
                        try:
                            with open_db(self._db_path, readonly=True) as _c:
                                row = _c.execute("SELECT value FROM config WHERE key=?", (f"_app:{key}",)).fetchone()
                                if row and row[0]:
                                    try:
                                        return _json.loads(row[0])
                                    except Exception:
                                        return row[0]
                        except Exception:
                            pass
                        return default

                    def _write_app(key, value):
                        v = _json.dumps(value)
                        with open_db(self._db_path) as _c:
                            _c.execute(
                                "INSERT INTO config (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (f"_app:{key}", v),
                            )
                            _c.commit()

                    from tradinglib.providers import PROVIDERS, KEYED_PROVIDERS

                    _PROVIDER_LABELS = {
                        "yahoo": "Yahoo Finance (default)",
                        "fmp": "Financial Modeling Prep (FMP)",
                        "eodhd": "EOD Historical Data (EODHD)",
                    }
                    # Short names keep inline labels readable ("EODHD ticker"
                    # rather than "EOD Historical Data (EODHD) ticker").
                    _PROVIDER_SHORT = {"fmp": "FMP", "eodhd": "EODHD"}
                    _PROVIDER_SIGNUP = {
                        "fmp": "financialmodelingprep.com",
                        "eodhd": "eodhd.com",
                    }

                    providers = list(PROVIDERS)
                    cur_provider = _read_app("data_provider", "yahoo")
                    try:
                        p_idx = providers.index(cur_provider)
                    except ValueError:
                        p_idx = 0
                    new_provider = st.selectbox(i18n.t("cfg.data_provider"), providers, p_idx,
                                                format_func=lambda x: _PROVIDER_LABELS.get(x, x))
                    _write_app("data_provider", new_provider)

                    if new_provider in KEYED_PROVIDERS:
                        _pname = new_provider
                        _plabel = _PROVIDER_SHORT.get(_pname, _pname.upper())
                        # API key is managed via KSP — show status
                        try:
                            from tradinglib.ksplib import Ksp as _Ksp
                            _ksp_creds = _Ksp(storage_path=self._db_path.replace('config.db', ''),
                                              secrets_path=self._db_path.replace('config.db', '')).get_ksp(_pname)
                            _ksp_key = (_ksp_creds.get("password", "") if isinstance(_ksp_creds, dict) else "") or ""
                        except Exception:
                            _ksp_key = ""
                        if _ksp_key:
                            st.success(i18n.t("cfg.provider_ksp_found", provider=_pname))
                        else:
                            st.warning(i18n.t("cfg.provider_ksp_missing", provider=_pname,
                                              url=_PROVIDER_SIGNUP.get(_pname, "")))

                        _ov_key = f"{_pname}_ticker_overrides"
                        cur_overrides_raw = _read_app(_ov_key, {})
                        cur_overrides = cur_overrides_raw if isinstance(cur_overrides_raw, dict) else {}
                        st.caption(i18n.t("cfg.provider_overrides_caption", provider=_plabel))
                        col_y, col_f, col_del = st.columns([2, 2, 1])
                        new_yf = col_y.text_input(i18n.t("cfg.provider_override_yahoo"), key=f"_{_pname}_ov_yf")
                        new_target = col_f.text_input(i18n.t("cfg.provider_override_target", provider=_plabel),
                                                      key=f"_{_pname}_ov_target")
                        col_del.write("")
                        col_del.write("")
                        if col_del.button(i18n.t("cfg.provider_override_add"), key=f"_{_pname}_ov_add"):
                            if new_yf.strip() and new_target.strip():
                                cur_overrides[new_yf.strip()] = new_target.strip()
                                _write_app(_ov_key, cur_overrides)
                                st.rerun()
                        if cur_overrides:
                            for yf_tk, target_tk in list(cur_overrides.items()):
                                c1, c2, c3 = st.columns([2, 2, 1])
                                c1.code(yf_tk)
                                c2.code(target_tk)
                                if c3.button("✕", key=f"_{_pname}_del_{yf_tk}"):
                                    del cur_overrides[yf_tk]
                                    _write_app(_ov_key, cur_overrides)
                                    st.rerun()

                        if st.button(i18n.t("cfg.provider_test_btn"), key=f"_{_pname}_test"):
                            if not _ksp_key:
                                st.error(i18n.t("cfg.provider_test_no_key"))
                            else:
                                try:
                                    if _pname == "fmp":
                                        from tradinglib.providers.fmp_provider import FMPProvider
                                        ok = FMPProvider(api_key=_ksp_key).test_connection()
                                    else:
                                        from tradinglib.providers.eodhd_provider import EODHDProvider
                                        ok = EODHDProvider(api_key=_ksp_key).test_connection()
                                    if ok:
                                        st.success(i18n.t("cfg.provider_test_ok"))
                                    else:
                                        st.error(i18n.t("cfg.provider_test_fail"))
                                except Exception as _e:
                                    st.error(str(_e))

                with st.expander(i18n.t("cfg.ai_models_header"), expanded=False):
                    st.caption(i18n.t("cfg.ai_models_caption"))
                    from tradinglib.ai_client import (
                        _ANTHROPIC_MODELS, _GROQ_MODELS, _GEMINI_MODELS, _GITHUB_MODELS,
                        merge_provider_order,
                    )

                    # ── Provider-Reihenfolge (global; identisch zur Market-Overview-Seite,
                    #    schreibt denselben config.db-Key 'ai_provider_order') ──────────────
                    _all_providers = ['claude', 'groq', 'github', 'gemini', 'ollama']
                    _prov_labels   = {p: i18n.t(f'mv.prov.{p}') for p in _all_providers}
                    _saved_order   = self.get_value('ai_provider_order', _all_providers)
                    if not isinstance(_saved_order, list):
                        _saved_order = _all_providers
                    _saved_order = merge_provider_order(_saved_order, _all_providers)
                    st.markdown(i18n.t("mv.provider_order_head"))
                    st.caption(i18n.t("mv.provider_order_caption"))
                    _new_order = st.multiselect(
                        i18n.t("mv.provider_order_label"),
                        options=_all_providers,
                        default=_saved_order,
                        format_func=lambda p: _prov_labels.get(p, p),
                        key='_cfg_provider_order',
                    )
                    # Abgewählte Provider als Fallback hinten anhängen (kein Signalverlust).
                    _full_order = _new_order + [p for p in _all_providers if p not in _new_order]
                    if _full_order != _saved_order:
                        self.set_value('ai_provider_order', _full_order)
                    st.markdown("---")

                    def _models_input(cfg_key, default_models, label):
                        cur = self.get_value(cfg_key, None)
                        if isinstance(cur, list) and cur:
                            cur_str = ", ".join(str(m) for m in cur)
                        elif isinstance(cur, str) and cur.strip():
                            cur_str = cur
                        else:
                            cur_str = ", ".join(default_models)
                        raw = st.text_input(label, value=cur_str, key=f"_ai_models_{cfg_key}")
                        names = [m.strip() for m in raw.split(',') if m.strip()]
                        self.set_value(cfg_key, names or list(default_models))

                    _models_input('ai_models_anthropic', _ANTHROPIC_MODELS, i18n.t("cfg.ai_models_claude"))
                    _models_input('ai_models_groq', _GROQ_MODELS, i18n.t("cfg.ai_models_groq"))
                    _models_input('ai_models_gemini', _GEMINI_MODELS, i18n.t("cfg.ai_models_gemini"))
                    _models_input('ai_models_github', _GITHUB_MODELS, i18n.t("cfg.ai_models_github"))
                    st.markdown("---")
                    self.set_value('ai_model_ollama', st.text_input(
                        i18n.t("cfg.ai_model_ollama"),
                        self.get_value('ai_model_ollama', ''),
                        help=i18n.t("cfg.ai_model_ollama_help"),
                    ))

                with st.expander(i18n.t("cfg.logging_header"), expanded=False):
                    logging_choice = st.selectbox(i18n.t("cfg.logging"), b_select, idx_b_select)
                    self.set_value('logging', logging_choice)
                    # logfile and level controls
                    current_logfile = self.get_value('logfile', 'logfile.txt')
                    current_loglevel = self.get_value('loglevel', 'INFO')
                    logfile = st.text_input(i18n.t("cfg.logfile"), value=current_logfile)
                    loglevels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
                    try:
                        idx_level = loglevels.index(current_loglevel)
                    except Exception:
                        idx_level = 1
                    loglevel = st.selectbox(i18n.t("cfg.loglevel"), loglevels, idx_level)
                    self.set_value('logfile', logfile)
                    self.set_value('loglevel', loglevel)
                    # Apply logging setting immediately
                    try:
                        if logging_choice:
                            lgc.enable_logging(to_console=True, level=loglevel, logfile=logfile)
                        else:
                            lgc.disable_logging()
                    except Exception:
                        pass

                with st.expander(i18n.t("cfg.advanced_header"), expanded=False):
                    self.set_value('sr_scaling', st.selectbox(i18n.t("cfg.sr_scaling"), sr_select, idx_sr_select))
                    self.set_value('rt_prices', st.selectbox(i18n.t("cfg.rt_prices"), b_select, idx_rt_select))
                    self.set_value('ovt_smoothing', st.selectbox(i18n.t("cfg.ovt_smoothing"), s_select, idx_s_select))

        if st.button(i18n.t("cfg.save")):
            st.rerun()

    def get_plugin_params(self, plugin_name: str) -> dict:
        """Returns stored parameter overrides for a plugin, or an empty dict."""
        stored = self.get_value(f'plugin_params:{plugin_name}')
        return stored if isinstance(stored, dict) else {}

    def set_plugin_params(self, plugin_name: str, params: dict):
        """Persists parameter overrides for a plugin."""
        self.set_value(f'plugin_params:{plugin_name}', params)

    @st.dialog('Indicator Settings', width='large')
    def render_plugin_params(self, plugin_name: str, params_schema: dict):
        """Render a dialog for editing and persisting per-indicator parameter overrides."""
        st.write(f"**{plugin_name}**")
        current = self.get_plugin_params(plugin_name)
        new_params = {}

        for key, spec in params_schema.items():
            # Explicit session-state key prevents Streamlit from resetting the widget value
            # to the DB value on dialog reruns (bug with auto-generated keys).
            wk = f"_plgparam_{plugin_name}_{key}"

            # On first dialog open: populate session state from DB
            if wk not in st.session_state:
                raw = current.get(key, spec['default'])
                if spec['type'] == 'int':
                    st.session_state[wk] = int(raw)
                elif spec['type'] == 'float':
                    st.session_state[wk] = float(raw)
                elif spec['type'] == 'bool':
                    st.session_state[wk] = bool(raw)
                elif spec['type'] == 'color':
                    st.session_state[wk] = (raw if isinstance(raw, str) and raw.startswith('#') and len(raw) == 7 else '#1E90FF')
                else:  # select
                    opts = spec.get('options', [])
                    st.session_state[wk] = raw if raw in opts else spec['default']

            if spec['type'] == 'color':
                raw = current.get(key, spec['default'])
                if wk not in st.session_state:
                    st.session_state[wk] = raw if (isinstance(raw, str) and raw.startswith('#') and len(raw) == 7) else '#1E90FF'
                new_params[key] = st.color_picker(spec['label'], key=wk)
            elif spec['type'] == 'bool':
                new_params[key] = st.checkbox(spec['label'], key=wk)
            elif spec['type'] == 'int':
                new_params[key] = st.number_input(
                    spec['label'],
                    min_value=spec.get('min', 1),
                    max_value=spec.get('max', 500),
                    step=1,
                    key=wk,
                )
            elif spec['type'] == 'float':
                new_params[key] = st.number_input(
                    spec['label'],
                    min_value=float(spec['min']) if 'min' in spec else None,
                    max_value=float(spec['max']) if 'max' in spec else None,
                    step=float(spec.get('step', 0.01)),
                    key=wk,
                )
            elif spec['type'] == 'select':
                opts = spec.get('options', [])
                new_params[key] = st.selectbox(spec['label'], opts, key=wk)

        if st.button("Save"):
            self.set_plugin_params(plugin_name, new_params)
            # Clear session-state keys so the next dialog open loads freshly saved values from DB
            for k in params_schema:
                st.session_state.pop(f"_plgparam_{plugin_name}_{k}", None)
            st.rerun()

    def _render_help_pages(self, pages):
        """Render a list of (label, module) help pages as tabs — one tab per page.

        Replaces the previous per-page st.expander list so a category's pages sit
        side by side instead of stacking vertically. Streamlit scrolls the tab
        strip horizontally when there are many/long labels.

        Plain helper (NOT a dialog): it is called from inside render_help, which
        already owns the @st.dialog. Decorating this would open a nested dialog per
        call → StreamlitDuplicateElementId.
        """
        if not pages:
            return
        subtabs = st.tabs([label for label, _ in pages])
        for subtab, (_label, module) in zip(subtabs, pages):
            with subtab:
                st.html(help_text.load_help(module))

    @st.dialog('Help', width='large')
    def render_help(self):
        """Render the tabbed help dialog with HTML content from the HELP/ directory."""
        if self.bare_mode:
            return

        # Sync the help-page styling with the app theme (☀️/🌙 sidebar toggle)
        try:
            help_text.set_dark_mode(self.get_value('theme_mode', 'dark') != 'light')
        except Exception:
            pass

        # Scalable edition: focused help (getting started + free features), no
        # Premium/Admin/CLI tabs.
        try:
            from tradinglib import app_edition as appedition
            if appedition.IS_SCALABLE:
                self._render_scalable_help()
                return
        except Exception:
            pass

        if not self.bare_mode:

            tabs = st.tabs(["Scalable", "Hauptansichten", "Werkzeuge", "Premium", "Administration", "CLI-Skripte", "OVT-Indikator"])

            with tabs[0]:
                self._render_scalable_help()

            with tabs[1]:
                self._render_help_pages([
                    ("Asset Details", "main_page"),
                    ("Dashboard", "banner_page"),
                    ("Market Map", "market_map"),
                    ("Sektorrotation", "sector_rotation_page"),
                    ("Korrelationsindex", "correlation_index_page"),
                    ("Regime Flow", "regime_flow_page"),
                    ("Performance", "performance_details"),
                    ("Asset-Übersicht", "all_assets"),
                    ("Eigene Transaktionen", "own_trades_analysis"),
                ])

            with tabs[2]:
                self._render_help_pages([
                    ("Einstellungen (⚙)", "system_config"),
                    ("Zinseszins-Simulation", "compound_simulation"),
                    ("Earnings-Kalender", "earnings_calendar"),
                    ("Pine Script Export", "pine_exporter"),
                    ("KI-Integration", "ai_client"),
                    ("Signal-Benachrichtigung", "signal_notifier"),
                    ("Datenquellen (Yahoo / FMP / EODHD)", "providers"),
                ])

            with tabs[3]:
                self._render_help_pages([
                    ("Strategie-Suche", "asset_simulator"),
                    ("Multi-Strategien", "multi_transaction"),
                    ("Paper / Live Trading", "trading_page"),
                ])

            with tabs[4]:
                st.html(help_text.load_help("admin"))

            with tabs[5]:
                self._render_help_pages([
                    ("Performance-Simulation (asset_perf2.py)", "asset_perf2"),
                    ("Trading-Agent (run_agent.py)", "run_agent"),
                    ("Stop-Loss-Prüfung (check_stoploss.py)", "check_stoploss"),
                    ("Datenabruf (get_asset_data.py)", "get_asset_data"),
                    ("Asset-Info laden (get_asset_info.py)", "get_asset_info"),
                    ("Datenbank initialisieren (seed_db.py)", "seed_db"),
                    ("ISIN-Befüllung (backfill_isin.py)", "backfill_isin"),
                    ("Scheduler-Daemon (schedserver.py)", "schedserver"),
                    ("Anwendung starten (launcher.py)", "launcher"),
                    ("Setup & Scheduler (Empfehlungen)", "setup_scheduler"),
                ])

            with tabs[6]:
                lang_tabs = st.tabs(["🇩🇪 Deutsch", "🇬🇧 English"])
                with lang_tabs[0]:
                    st.html(help_text.load_help("ovt_de"))
                with lang_tabs[1]:
                    st.html(help_text.load_help("ovt_en"))

    def _render_scalable_help(self):
        """Focused help dialog for the Scalable edition (no Premium/Admin/CLI tabs)."""
        tabs = st.tabs(["🚀 Erste Schritte / Getting started", "📊 Funktionen / Features"])

        with tabs[0]:
            lang_tabs = st.tabs(["🇩🇪 Deutsch", "🇬🇧 English"])
            with lang_tabs[0]:
                st.html(help_text.load_help("scalable_de"))
            with lang_tabs[1]:
                st.html(help_text.load_help("scalable_en"))

        with tabs[1]:
            self._render_help_pages([
                ("Eigene Transaktionen / Own Transactions", "own_trades_analysis"),
                ("Asset-Ansicht / Asset viewer", "main_page"),
                ("Sektorrotation / Sector rotation", "sector_rotation_page"),
                ("Market Map", "market_map"),
                ("Einmalinvestition / Lump sum", "compound_simulation"),
                ("Datenquellen (Yahoo / FMP / EODHD)", "providers"),
            ])
