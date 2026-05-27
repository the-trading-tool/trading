import sqlite3
import json
import sys
import streamlit as st
import logging

for name, l in logging.root.manager.loggerDict.items():
    if "streamlit" in name:
        l.disabled = True

try:
	sys.path.insert(0, "../../tradinglib")
except ImportError:
	print('No Import')

from tradinglib import tools
from tradinglib import help_text
from tradinglib import multi_select
from tradinglib.indicator import indicator  # Die Basisklasse importieren
from tradinglib import logging_config as lgc

class SystemConfig(tools.Db_tools):
    
#    transactions = {
#        'SPX': {'buy': '(momentum > rsi_ema) & (overallValueTrend > 53)', 'sell': '(overallValueTrend < 58)', 'num_assets': 7, 'invest': 17000, 'order_by': 'sortino'}, 
#        'GDAXI': {'buy': '(momentum > rsi_ema)', 'sell': '(overallValueTrend < 58)', 'num_assets': 7, 'invest': 15000, 'order_by': 'sortino'}, 
#        'MDAXI': {'buy': '(momentum > rsi_ema)', 'sell': '(overallValueTrend < 55)', 'num_assets': 7, 'invest': 10000, 'order_by': 'sortino'}, 
#        'SDAXI': {'buy': '(overallValueTrend >= 51)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 41)', 'num_assets': 3, 'invest': 8000, 'order_by': 'sortino'}
#        }

    transactions = {
        'SPX': {'buy': '(overallValueTrend >= 69)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 59)', 'num_assets': 5, 'invest': 15000, 'order_by': 'sortino'}, 
        'GDAXI': {'buy': '(overallValueTrend >= 67)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 57)', 'num_assets': 5, 'invest': 15000, 'order_by': 'sortino'}, 
        'MDAXI': {'buy': '(overallValueTrend >= 60)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 50)', 'num_assets': 2, 'invest': 7000, 'order_by': 'sortino'}, 
        'SDAXI': {'buy': '(overallValueTrend >= 56)', 'sell': '(rsi <= rsi_ema) | (overallValueTrend <= 46)', 'num_assets': 3, 'invest': 10000, 'order_by': 'sortino'}, 
        }                 
    
    def __init__(self, db_path="database", db_name = "config.db", username = 'admin', region = st, is_admin = False, bare_mode = False):
        self._db_path = self.get_path( path=db_path, file_name=db_name)
        self.username = username        
        self.is_admin = is_admin
        self.region = region
        self.bare_mode = bare_mode

        self._initialize_db()

    def _initialize_db(self):
        """creates config db."""
        with sqlite3.connect(self._db_path) as conn:
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
        value_str = json.dumps(value)  # Serialisieren
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (f"{self.username}:{key}", value_str))
            conn.commit()
    
    def get_value(self, key: str, default=None) -> str:
        """creates a config value."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM config WHERE key = ?", (f"{self.username}:{key}",))
            result = cursor.fetchone()
            if result and result[0]:
                try:
                    return json.loads(result[0])
                except json.JSONDecodeError:
                    return result[0]  # Falls es ein einfacher String ist
            return default
    
    def delete_value(self, key: str):
        """Deletes a config value."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM config WHERE key = ?", (f"{self.username}:{key}",))
            conn.commit()

    def render_cfg_row(self):
        cfg_line = self.region.empty()
        (_, cfg_btn_c, cfg_btn_h) = cfg_line.columns([.95,.05,.05], gap='small')
        #(cfg_btn_c, cfg_btn_h) = cfg_line.columns(2, gap='small')
        if cfg_btn_c.button(":rosette:", use_container_width=True):
            self.render()
        if cfg_btn_h.button(":grey_question:", use_container_width=True):
            self.render_help()

    def get_selectors(self, interval=None, period=None, overlays=None, oszilators=None):
        
        if interval == None or interval == []:
            interval = self.get_value('interval','60m')
        if isinstance(interval, list):
            interval = interval[0]

        if period == None or period == []:
            period = self.get_value('period','1mo')
        if isinstance(period, list):
            period = period[0]

        ol = ""
        for t in multi_select.MultiCheckboxSelector.lists[2][1]:
            ol += "'"+t.split(" - ")[0]+"', "
        error_text = "Select a combination of either/and: "        
        if overlays == None or overlays == []:
            try:
                overlays = [self.get_value('overlay','')]
                overlays = eval(overlays[0])
            except Exception:
                overlays = ['pre']
                if not self.bare_mode:
                    st.error(f"{error_text} [{ol}]")
                pass

        oz = ""
        for t in multi_select.MultiCheckboxSelector.lists[3][1]:
            oz += "'"+t.split(" - ")[0]+"', "
        if oszilators == None or oszilators == []:
            try:
                oszilators = [self.get_value('oszilator','')]
                oszilators = eval(oszilators[0])
            except Exception:
                oszilators = ['macd']
                if not self.bare_mode:
                    st.error(f"{error_text} [{oz}]")
                pass

        return(interval, period, overlays, oszilators)

    def get_idx_selected(self, v_list, v_key, default=0):
        
        try:
            default = v_list.index(self.get_value(v_key))
        except Exception:
            pass    
        return default

    @st.dialog('Configuration',width='large')
    def render(self):

        def check_list(lst = None, overlay = ''):
            error_text = "Select a combination of either/and: "        
            ol = ""
            for t in lst:
                ol += "'"+t.split(" - ")[0]+"', "
            ms_str = " ".join(lst)
            for itm in eval(self.get_value(overlay)):
                try:
                    pos = ms_str.index(itm)
                except Exception:
                    st.error(f"{error_text} [{ol}]")
                    pass

        intervals = multi_select.MultiCheckboxSelector.lists[0][1]
        periods = multi_select.MultiCheckboxSelector.lists[1][1]
        overlays = multi_select.MultiCheckboxSelector.lists[2][1]
        oszilators  = multi_select.MultiCheckboxSelector.lists[3][1]
        b_select = [True, False]
        s_select =[1,2,3,5]
        sr_select =[0.01,0.1,1,10,100,1000]

        idx_b_select = self.get_idx_selected(b_select, 'logging',1)
        idx_rt_select = self.get_idx_selected(b_select, 'rt_prices',1)
        idx_s_select = self.get_idx_selected(s_select, 'ovt_smoothing',2)
        idx_sr_select = self.get_idx_selected(sr_select, 'sr_scaling',4)
        idx_period = self.get_idx_selected(periods, 'period',3)
        idx_mp_details = self.get_idx_selected(b_select, 'mp_details',1)
        idx_pine_export = self.get_idx_selected(b_select, 'pine_export', 1)
        idx_interval = self.get_idx_selected(intervals, 'interval',3)
        self.region.write(f"user: {self.username}")
        if self.is_admin:
            logging_choice = st.selectbox("Logging: ", b_select, idx_b_select)
            self.set_value('logging', logging_choice)
            # logfile and level controls
            current_logfile = self.get_value('logfile', 'out.txt')
            current_loglevel = self.get_value('loglevel', 'INFO')
            logfile = st.text_input('Log file name:', value=current_logfile)
            loglevels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
            try:
                idx_level = loglevels.index(current_loglevel)
            except Exception:
                idx_level = 1
            loglevel = st.selectbox('Log level:', loglevels, idx_level)
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
            self.set_value('sr_scaling',st.selectbox("S/R scaling: ", sr_select, idx_sr_select))
            self.set_value('rt_prices',st.selectbox("Realtime prices: ", b_select, idx_rt_select))
            self.set_value('ovt_smoothing', st.selectbox("ovt Smoothing: ", s_select,idx_s_select))
        self.set_value('system_currency',st.text_input("Currency: ",self.get_value('system_currency', 'EUR')))
        self.set_value('multi_transactions',st.text_area("Transactions: ",self.get_value('multi_transactions', self.transactions)))
        self.set_value('trading_cost_pct',st.text_input("Trading cost percentage: ",self.get_value('trading_cost_pct', '0.6')))
        self.set_value('interval',st.selectbox("Default chart interval: ",intervals,idx_interval)) #60m
        self.set_value('period',st.selectbox("Default chart period: ",periods,idx_period)) #1mo        
        self.set_value('overlay',st.text_input("Default chart overlay: ",self.get_value('overlay',"['bos','pre']"))) #predict
        self.set_value('monitored_assets',st.text_input("Monitored assets: ",self.get_value('monitored_assets',"")))
        check_list(overlays, 'overlay')
        self.set_value('oszilator',st.text_input("Default chart oszilator: ",self.get_value('oszilator',"['adx','cci','ewo']"))) #rsi       
        check_list(oszilators, 'oszilator')
        self.set_value('tz_info',st.text_input("Default timezone: ",self.get_value('tz_info',"Europe/Berlin"))) #predict
        self.set_value('mp_details', st.selectbox("Show Main page Details-Tab: ", b_select,idx_mp_details))
        self.set_value('pine_export', st.selectbox("Pine Script Export anzeigen: ", b_select, idx_pine_export))
        self.set_value('buy_query', st.text_input("Buy query: ", self.get_value('buy_query', '(ha_close > ha_open) & (Close > ha_ema_high) & (macd > macd_signal) & (rsi > 50) & (markov_regime < 2)')))
        self.set_value('sell_query', st.text_input("Sell query: ", self.get_value('sell_query', '(ha_close < ha_open) & (Close < ha_ema_low)')))

        if st.button("Save"):
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
        st.write(f"**{plugin_name}**")
        current = self.get_plugin_params(plugin_name)
        new_params = {}

        for key, spec in params_schema.items():
            # Expliziter Session-State-Key verhindert, dass Streamlit den Widget-Wert
            # bei Dialog-Reruns auf den DB-Wert zurücksetzt (Bug bei auto-generierten Keys).
            wk = f"_plgparam_{plugin_name}_{key}"

            # Beim ersten Öffnen des Dialogs: Session-State aus DB befüllen
            if wk not in st.session_state:
                raw = current.get(key, spec['default'])
                if spec['type'] == 'int':
                    st.session_state[wk] = int(raw)
                elif spec['type'] == 'float':
                    st.session_state[wk] = float(raw)
                elif spec['type'] == 'bool':
                    st.session_state[wk] = bool(raw)
                elif spec['type'] == 'color':
                    # st.color_picker needs a valid #RRGGBB string
                    st.session_state[wk] = (raw if isinstance(raw, str) and raw.startswith('#') and len(raw) == 7 else '#1E90FF')
                else:  # select
                    opts = spec.get('options', [])
                    st.session_state[wk] = raw if raw in opts else spec['default']

            if spec['type'] == 'color':
                raw = current.get(key, spec['default'])
                if wk not in st.session_state:
                    # st.color_picker requires a non-empty #RRGGBB string
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
            # Session-State-Keys löschen damit der nächste Dialog-Aufruf
            # die frisch gespeicherten Werte aus der DB lädt
            for k in params_schema:
                st.session_state.pop(f"_plgparam_{plugin_name}_{k}", None)
            st.rerun()

    @st.dialog('Help',width='large')
    def render_help(self):
        
        if not self.bare_mode:

            help = help_text.helptext_general
            st.html(help) 
        
        pass