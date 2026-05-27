import os
import sqlite3
from datetime import datetime, timedelta
import time
from io import BytesIO
import pandas as pd
# yfinance access is centralized in tradinglib.market_data (md).
# Avoid direct yfinance calls here to keep data access through the wrapper.
import json
from math import floor
from urllib.parse import quote

from tradinglib import tools
import tradinglib.indicator as indicator_pkg
import pkgutil
import importlib
import re
from typing import Tuple
import streamlit as st 
from tradinglib.utils import DataUtils
from tradinglib import market_data as md


def calculate_investment(asset_log_vola=0.20, target_avg=2000, ref_vola=0.1 ):
    # Skalierungsfaktor: Weniger investieren bei hoher Vola, mehr bei niedriger
    factor = ref_vola / asset_log_vola
    
    # Optional: Cap setzen, um Klumpenrisiken zu vermeiden (z.B. max. 3x Durchschnitt)
    final_amount = target_avg * min(factor, 3.0) 
    
    return round(final_amount, 2)

class OHLCQueryPlanner:
    """
    Plant, welche Tabelle und wie viele Zeilen geladen werden müssen,
    um einen bestimmten Aggregationsintervall und Zeitraum abbilden zu können.
    Handelszeiten (z. B. 480 min/Tag) werden berücksichtigt.
    """

    # Max. Handelswerte pro Tag je Datenquelle
    DATA_RESOLUTION_PER_DAY = {
        "min": 480,     # 8h * 60min
        "h60": 8,         # 8 Stunden pro Tag
        "day": 1,          # 1 pro Handelstag
        "month": 1 / 21,   # ca. 1 pro 21 Handelstage
    }

    # Mapping von Zielintervall zu Quelldatentyp
    INTERVAL_TO_SOURCE = [
        ("min", ["m"]),
        ("h60",   ["h"]),
        ("day",    ["d", "wk"]),
        ("month",  ["mo", "y"]),
    ]

    # Handelskalender: wie viele Handelstage pro Woche/Monat/Jahr
    PERIOD_TO_TRADING_DAYS = {
        "h": 1/8,
        "d": 1,
        "wk": 5,
        "mo": 21,
        "y": 252,  # 21 Tage * 12 Monate
        "max": 25*252,  # 21 Tage * 12 Monate
    }

    def __init__(self, interval: str, period: str):
        self.interval = interval
        self.period = period
        self.source_table = self._resolve_source_table()
        self.required_rows = self._calculate_required_rows()

    def _parse_freq(self, s: str) -> Tuple[int, str]:
        if s == "max":
            return 1, s
        match = re.match(r"(\d+)([a-z]+)", s)
        if not match:
            raise ValueError(f"unknown format: {s}")
        return int(match[1]), match[2]

    def _resolve_source_table(self) -> str:
        (_, unit) = self._parse_freq(self.interval) 
        for base_table, intervals in self.INTERVAL_TO_SOURCE:
            if unit in intervals:
                return base_table
        raise ValueError(f"unknown intervall: {self.interval}")

    def _calculate_required_rows(self) -> int:
        interval_val, _ = self._parse_freq(self.interval)
        period_val, period_unit = self._parse_freq(self.period)

        base = self.source_table
        resolution_per_day = self.DATA_RESOLUTION_PER_DAY[base]

        if period_unit not in self.PERIOD_TO_TRADING_DAYS:
            raise ValueError(f"unknown periods: {period_unit}")

        total_trading_days = period_val * self.PERIOD_TO_TRADING_DAYS[period_unit]
        rows_per_day = resolution_per_day

        return int(total_trading_days * rows_per_day)

    def get_plan(self) -> Tuple[str, int]:
        return f"{self.source_table}_data", self.required_rows


class TickerTools(tools.Tools):

    xr = {}
    imported_modules = {}
    intervals=["1m","60m","1d", "1mo"]
    periods=['7d','60d','max','max']

    def add_url(self, search, interval:str = '30m', period:str ='1mo' ):
        try:
            search = search[:20]
            for r in [',','-',"'",'\"',".",";","/","#","+","&","(",")","[","]","=","@"]:
                search = search.replace(r, ' ')
            search = quote(search, safe='')
        except Exception:
            pass
        return f'/?symbol={search}&details=True'#&interval=1mo&period=10y&details=False'

    def load_tradinglib_indicator_instances(self):

        # Dynamisch alle Module im indicator-Paket importieren, außer die mit "_"
        for loader, module_name, is_pkg in pkgutil.iter_modules(indicator_pkg.__path__):
            if not module_name.startswith("_"):
                full_module_name = f"{indicator_pkg.__name__}.{module_name}"
                self.imported_modules[module_name] = importlib.import_module(full_module_name)


    def init_instance(self, instance_name = None, replace_values_in_df = False, df=pd.DataFrame(), symbol=None):

        # Load instances — reload if the requested module is missing (picks up
        # newly added indicator files without requiring a server restart)
        if len(self.imported_modules) == 0 or (
            instance_name is not None and instance_name not in self.imported_modules
        ):
            self.load_tradinglib_indicator_instances()

        sys_conf = getattr(self, 'sys_conf', None)

        def instanciate(name, module):

            class_name = name.capitalize()
            try:
                cls = getattr(module, class_name)

                # Merge stored plugin params over defaults (if any)
                extra = {}
                if sys_conf and hasattr(cls, 'params') and cls.params:
                    extra = sys_conf.get_plugin_params(name)

                if df.empty:
                    instance = cls(df=self.df, **extra)
                else:
                    instance = cls(df=df, symbol=symbol, **extra)
                    replace_values_in_df = False

                setattr(self, name, instance)
                if replace_values_in_df:
                    self.df = instance.df

            except AttributeError:
                print(f"⚠️ Not found '{class_name}' in '{name}'")
            except Exception as e:
                print(f"❌ Error in '{class_name}': {e}")

        if instance_name is None:
            for name, module in self.imported_modules.items():
                instanciate(name, module)
        elif instance_name in self.imported_modules:
            instanciate(instance_name, self.imported_modules[instance_name])
        else:
            print(f"⚠️ Indicator module '{instance_name}' not found in imported_modules")


    def get_sheet_as_df(self, ticker, item, name):

#        if 1:
        try:
            sht = self.get_ticker_value(ticker,item, True)                
            columns = []
            try:
                for key in sht['columns']:
                    time = datetime.fromtimestamp(int(key/1000)).strftime("%Y-%m-%d")
                    columns.append(time)
            except Exception:
                pass

            del sht['columns']
            sht_df = pd.DataFrame(sht['data'], columns=columns, index=sht['index'])
            sht_df.index.name = name

            return sht_df

        except Exception:
            pass

        return pd.DataFrame()
    

    def get_ticker_value(self, ticker, key, from_json=False, digits = 2):

        def _get_value(id, key):
            return ticker[key].iloc[id]

#        if 1:
        try:
            v = _get_value(0,key)
        except Exception:
            pass

        try:
            if from_json:
                v  = json.loads(v.replace("'", "\""))
        except Exception:
            pass
    
        value = 0
        try:
            value = v
        except Exception:
            pass
    
        # 1703980800
        if value != value:
            value = 0
        if value == None:
            value = 0

        if not type(value) == str and not type(value) == dict:
            return round(float(value),digits)
            
        return value


    def transform_df(self, df):
        
        df = df.reset_index()
        df = df[df.columns[1:]]
        df = df.transpose()
        new_header = df.iloc[0] #grab the first row for the header
        df = df[1:] #take the data less the header row
        df.columns = new_header #set the header row as the df header

        return df


    def get_income_statement(self, t, symbol):

        df = t.income_statement()
        return self.transform_df(df)


    def get_exchange_rate(self, symbol = 'EUR', system_currency = 'EUR', period='1d', interval='1m'):
        # Delegate to the centralized helper in DataUtils which provides caching
        try:
            return DataUtils.get_exchange_rate(symbol=symbol, system_currency=system_currency, period=period, interval=interval)
        except Exception:
            return 1


    def get_table_name(self, interval):
        # Delegiere an zentrale Utility-Funktion
        return DataUtils.get_table_name(interval)
        
    def get_num_rows(self, df):
        return DataUtils.get_num_rows(df)


    def fetch_yahoo_ticker_data(self, symbol, period, interval):
        
        df = pd.DataFrame()
        info = {}
        try:
            # Use centralized wrapper for history to allow unified error handling / caching
            df = md.ticker_history(symbol, period=period, interval=interval)
            try:
                from tradinglib.utils import DataUtils
                DataUtils.normalize_index_to_tz_naive(df, tz='UTC')
            except Exception:
                pass
            if self.get_num_rows(df) > 0:
                df.index = df.index.strftime(self.ftime_str)
                df.index = pd.to_datetime(df.index).strftime(self.ftime_str)
                df.index.name = 'Date'
        except Exception:
            pass

        try:
            # use centralized metadata wrappers to allow caching and consistent error handling
            info = md.ticker_info(symbol)
            financials = md.ticker_financials(symbol)
            balance_sheet = md.ticker_balance_sheet(symbol)
        except Exception:
            info = {}
            financials = pd.DataFrame()
            balance_sheet = pd.DataFrame()
        
        try:
            # Get income sheet data
            info['incomeSheet'] = financials.to_json(orient="split")
        except Exception:
            pass

        try:
            # Get balance sheet data
            info['balanceSheet'] = balance_sheet.to_json(orient="split")
        except Exception:
            pass

        return df, info


    def read_stock_list(self, 
                        excel_file='All_Stocks.xlsx', 
                        db_path = 'database', 
                        db_name = "yf_tickers.db", 
                        tbl_columns = ['id','Ticker','Date', 'Invested','ISIN']
                        ):
        
        db_name =  self.get_path(path = db_path, file_name=db_name)
    
        if os.path.isfile(excel_file):

            date = datetime.now().strftime(self.ftime_str)
            conn = sqlite3.connect(db_name)
            cursor = conn.cursor()
            table_name = 'stocks'

            cursor.executescript(f"""
-- Tabelle für Ticker-Daten ohne Index-Zugehörigkeit
CREATE TABLE IF NOT EXISTS {table_name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Ticker TEXT,
    Date TEXT,
    INVESTED REAL,
    ISIN TEXT
);

-- Tabelle für Indices
CREATE TABLE IF NOT EXISTS indices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE
);

-- Verbindungstabelle zwischen stocks und indices
CREATE TABLE IF NOT EXISTS stock_indices (
    stock_id INTEGER,
    index_id INTEGER,
    FOREIGN KEY(stock_id) REFERENCES stocks(id),
    FOREIGN KEY(index_id) REFERENCES indices(id)
);
""")
            
            df = pd.read_excel(excel_file, index_col=0, comment='#')
#            print(df)
            for _, row in df.iterrows():
                ticker = row["symbol"]
                invested = row["invested"]
                isin = row["isin"]
                index_list = [i.strip() for i in str(row["indices"]).split(",") if i.strip()]

                # Prüfen, ob Stock-Eintrag schon existiert (z. B. per Ticker + Date)
                cursor.execute(f"""SELECT id FROM {table_name} WHERE Ticker = ?""", 
                               [ticker])
                result = cursor.fetchone()

                if result:
                    stock_id = result[0]
                    # Optional: bestehende Daten aktualisieren
                    cursor.execute(f"""
                        UPDATE {table_name} SET INVESTED = ?, ISIN = ? WHERE id = ?
                    """, (invested, isin, stock_id))
                else:
                    # Neu anlegen
                    cursor.execute(f"""
                        INSERT INTO {table_name} (Ticker, Date, INVESTED, ISIN)
                        VALUES (?, ?, ?, ?)
                    """, (ticker, date, invested, isin))
                    stock_id = cursor.lastrowid

                # Indices behandeln
                for index_name in index_list:
                    # Index sicherstellen
                    cursor.execute("INSERT OR IGNORE INTO indices (name) VALUES (?)", (index_name,))
                    cursor.execute("SELECT id FROM indices WHERE name = ?", (index_name,))
                    index_id = cursor.fetchone()[0]

                    # Verknüpfung prüfen, nur einfügen wenn nicht vorhanden
                    cursor.execute(f"""
                        SELECT 1 FROM stock_indices
                        WHERE stock_id = ? AND index_id = ?
                    """, (stock_id, index_id))
                    if not cursor.fetchone():
                        cursor.execute("""
                            INSERT INTO stock_indices (stock_id, index_id)
                            VALUES (?, ?)
                        """, (stock_id, index_id))

            conn.commit()

            if df.empty:        
                cursor.execute(f"SELECT * FROM {table_name}")
                rows = cursor.fetchall()
                conn.close()
    
                # In ein pandas DataFrame umwandeln
                df = pd.DataFrame(rows, columns=tbl_columns)
            else:
                df.rename(columns={'symbol': 'Ticker'}, inplace=True)

            return df
        else:
            return pd.DataFrame()


    def read_asset_data(self, ticker, db_path = 'database'):
        
        db_assets =  self.get_path(path = db_path, file_name='asset_info.db')
        db_info =  self.get_path(path = db_path, file_name='yf_tickers.db')
        
        conn = sqlite3.connect(db_assets)

        # Zweite Datenbank anhängen
        conn.execute("ATTACH DATABASE 'db_info' AS db2;")

        # SQL-Abfrage ausführen, um die Tabellen zu verknüpfen
        cursor = conn.cursor()
        cursor.execute('''
            SELECT c.customer_name, o.order_id
            FROM customers c
            JOIN db2.orders o ON c.customer_id = o.customer_id;
        ''')

        # Ergebnisse abrufen und anzeigen
        rows = cursor.fetchall()
        for row in rows:
            print(row)

        # Verbindung schließen
        conn.close()            
        
    def export_to_excel(self, data, button_label = 'Export', file_name = 'data.xlsx', region = st ): 
        df_xlsx = self.get_bin_excel_data(data) # portfolio.get_transaction_dataframe())
        region.download_button(label=button_label,
            data=df_xlsx,
            file_name= file_name,
            mime='application/octet-stream'
        )

    def get_bin_excel_data(self, data = pd.DataFrame(), tbl='results'):
        return DataUtils.get_bin_excel_data(data, tbl=tbl)


class StockDataSaver(TickerTools):

    def __init__(self, ticker, db_path = 'database', tz_ready = ""): # Europe/Berlin
        self.ticker = ticker        
        self.tz_ready = tz_ready
        self.db_path = db_path
        db = tools.Db_tools(db_path=self.db_path, database_name=f"yf_{self.ticker}.db")
        self.conn = db.conn
        self.cursor = db.cursor

    def _parse_date(self, date_str):
        """Converts various date formats into a consistent datetime object."""
        for fmt in (self.ftime_str, "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                pass
        raise ValueError("Date not in supported format.")


    def fetch_data(self, interval:str="1m", period:str ='max'):
        # Use centralized wrapper instead of calling Ticker.history directly
        data = md.ticker_history(self.ticker, period=period, interval=interval)
        return data

    def fetch_data_dl(self, interval, period="max", force_remote=False):
        
        interval = interval.lower()
        interval_length = {
            '30m': '1h',
            '60m': '1h',
            '2h': '1h'   
        }
        if interval in interval_length:        
            interval = interval_length[interval] 
        
        period_length = {
            '1m':-6,
            '5m':-6,
            '15m':-59,
            '60m':-59,
            '1h':-729,
            '1d':-729,
            '1wk':-20*365,
            '1mo':-20*365 
        }

        if period == 'max':        
            days = -6
            if interval in period_length:
                days = period_length[interval]
        else:
            (days, unt) = self.split_interval(period)  
            units = {
                "d": 1,
                "wk": 5,
                "mo": 21,     
                "y": 252,           
            }
            days = -int(days * units[unt])
        if days < period_length[interval]:
            days = period_length[interval]

        date = datetime.now() #.strptime(date_str, self.ftime_str).date()
        start_date = (date + timedelta(days=days)).strftime("%Y-%m-%d")
        data = md.download(tickers=self.ticker, start=start_date, interval=interval, actions=False, progress=False, auto_adjust=False, prepost=True, force_remote=force_remote)
#        data.to_csv("data.csv",sep=";",decimal=",")
        if not self.tz_ready == "":
            try:
                data.index = data.index.tz_convert(self.tz_ready)
            except Exception:
                pass
        data = data.xs(self.ticker, axis=1, level='Ticker')
        data.drop(columns=['Adj Close'], axis=1, inplace=True)

        return data

    def create_table(self, interval):
        table_name = self.get_table_name(interval)
        self.cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                Date TEXT PRIMARY KEY,
                Open REAL,
                High REAL,
                Low REAL,
                Close REAL,
                Volume INTEGER
            )
        """)

    def save_to_db(self, data, interval):
        table_name = self.get_table_name(interval)
        # Use utility bulk saver for better performance
        DataUtils.save_ohlc_to_sql(self.conn, table_name, data, date_format=self.ftime_str)

    def save_all_intervals(self, intervals=['1m','1h','1d','1wk','1mo'], periods=['7d','60d','max','max'], force_remote=False):
        """Downloads and saves OHLC data for all given intervals.

        Returns a list of intervals for which the download failed (empty list = all OK).
        """
        import logging
        _log = logging.getLogger(__name__)
        failed_intervals = []

        for interval in intervals:
            print(f"Fetching data for interval: {interval}")
            period = periods[intervals.index(interval)]
            try:
                data = self.fetch_data_dl(interval, period=period, force_remote=force_remote)
                self.save_to_db(data, interval)
            except Exception:
                _log.warning("Download failed for %s interval=%s", self.ticker, interval)
                failed_intervals.append(interval)
            time.sleep(0.8)

        return failed_intervals

    def get_data(self, interval):
        table_name = self.get_table_name(interval)
        self.cursor.execute(f"SELECT * FROM {table_name}")
        rows = self.cursor.fetchall()

        df = pd.DataFrame(rows, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Date'] = pd.to_datetime(df['Date'])  # Datum in Datetime-Format umwandeln
        return df

    def close_connection(self):
        self.conn.close()

