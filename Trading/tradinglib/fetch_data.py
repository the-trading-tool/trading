import pandas as pd
import yfinance as yf
from tradinglib import market_data as md
import logging
import sqlite3
import re
from zoneinfo import ZoneInfo
from datetime import datetime

from tradinglib.indicator import indicator

#from tradinglib import tools
from tradinglib import ticker_tools as tt
from tradinglib import system_config as sysconf
from datetime import datetime, timedelta
import streamlit as st

class CurrencyConverter:
    def __init__(self, local_currency="EUR"):
        self.local_currency = local_currency
        self.cache = pd.DataFrame(columns=["Date", "Currency", "LocalCurrency", "Rate"])
    
    def _parse_date(self, date_str):
        """Converts various date formats into a consistent datetime object."""
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                pass
        raise ValueError("Date not in supported format.")
    
    def _get_cached_rate(self, date, currency):
        """Checks if the exchange rate for the date and currency is already cached."""
        cached = self.cache[(self.cache["Date"] == date) & (self.cache["Currency"] == currency)]
        return cached["Rate"].values[0] if not cached.empty else None
    
    def _fetch_exchange_rate(self, date, currency):
        """Fetches the exchange rate from Yahoo Finance if not cached."""
        ticker = f"{currency}{self.local_currency}=X"
#        if isinstance(date,str):
#            end_date = datetime.strptime(date, "%Y-%m-%d") 
        start_date = date + timedelta(days=-5)
        end_date = date + timedelta(days=1)
        data = md.download(ticker, start=start_date, end=end_date, actions=False, progress=False, auto_adjust=False, prepost=True)
        
        rate = 1
        try:
            rate = data.loc[data.index.date == date, "Close"].iloc[-1]
        except (IndexError, KeyError):
            # exact date not in data — fall back to last available close
            try:
                rate = data["Close"].iloc[-1]
            except (IndexError, KeyError):
                pass

        self.cache = pd.concat([self.cache, pd.DataFrame([[date, currency, self.local_currency, rate]],
                                                         columns=self.cache.columns)], ignore_index=True)
        return rate
    
    def convert(self, date_str, close_price, currency):
        """Converts the close price to the local currency."""
        if self.local_currency == currency:
            return float(close_price)

        if date_str == None:
            return 0

        date = self._parse_date(date_str)
        rate = self._get_cached_rate(date, currency)
        
        if rate is None:
            rate = self._fetch_exchange_rate(date, currency)

        return round(float(close_price) * rate,2)


class FetchData(tt.TickerTools):

    def __init__(self, database_path = 'database', database_name = 'asset_info.db', tz_info = "Europe/Berlin", indicators=[], buy_query="(ewo>ewo_ema)", sell_query="(ewo<ewo_ema)", sys_conf=None):
        self.logger = logging.getLogger(__name__)

        self.database_path = database_path
        self.indicators = indicators
        self.tz_info = tz_info
        self.database_name = database_name
        self.database = self.get_path(path = database_path, file_name=database_name)
        # sys_conf von außen übernehmen (z.B. von tiny_chart) damit Username-konsistenz
        # mit dem speichernden Dialog gewährleistet ist. Fallback: eigene Instanz.
        if sys_conf is not None:
            self.sys_conf = sys_conf
            self.config = sys_conf
        else:
            self.config = sysconf.SystemConfig()
            self.sys_conf = self.config
        self.system_currency = self.sys_conf.get_value("system_currency", "EUR")
        self.buy_query = buy_query
        self.sell_query = sell_query

    def load_ticker_data(self, symbol, period = 'max', interval = '1d'):

        # Verbindung zur SQLite-Datenbank herstellen
        asset_db = self.get_path(path = self.database_path, file_name='asset_info.db')
        conn = sqlite3.connect(asset_db)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA cache_size = -32000")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 67108864")

        # Funktion, um die Daten für ein bestimmtes Tickersymbol in einen DataFrame zu laden
        def load_data_into_dataframe(conn, symbol):
            # Parametrisierte Abfragen — kein f-String mit Nutzerwerten (SQL-Injection-Schutz)
            if symbol == '':
                df = pd.read_sql_query("SELECT ticker FROM asset_info", conn)
            else:
                df = pd.read_sql_query(
                    "SELECT * FROM asset_info WHERE ticker = ?", conn, params=(symbol,)
                )

            # JSON-Spalten (Listen) zurück in Listen konvertieren
            for col in df.columns:
                if df[col].dtype == 'object':  # Nur Textspalten prüfen
                    try:
                        df[col] = df[col].apply(pd.json.loads)
                    except Exception:
                        pass  # Ignoriere Spalten, die keine JSON-Daten enthalten

            return pd.DataFrame(df)

        df = load_data_into_dataframe(conn, symbol)

        # Fallback: Ticker-Info von Yahoo holen und lokal speichern wenn nicht vorhanden
        if symbol != '' and df.empty:
            try:
                self.logger.info("load_ticker_data: %s nicht in asset_info.db -- lade von Yahoo", symbol)
                info = md.ticker_info(symbol, use_cache=False)
                if info:
                    import json as _json
                    row_map = {'ticker': symbol}
                    for key, value in info.items():
                        col = f"_{key}" if key and key[0].isdigit() else key
                        if isinstance(value, (list, dict)):
                            value = _json.dumps(value)
                        row_map[col] = value
                    from tradinglib.utils import DataUtils
                    conn2 = sqlite3.connect(asset_db)
                    DataUtils.bulk_upsert_dicts(conn2, 'asset_info', [row_map])
                    conn2.close()
                    # Neu laden
                    conn3 = sqlite3.connect(asset_db)
                    df = load_data_into_dataframe(conn3, symbol)
                    conn3.close()
            except Exception as e:
                self.logger.warning("load_ticker_data Yahoo-Fallback fuer %s fehlgeschlagen: %s", symbol, e)

        conn.close()
        return df
    
    def load_price_data(self, symbol, period, interval, aggregate=False):

        # (value_i,unit_i) = self.split_interval(interval)

        # Funktion, um die Daten für ein bestimmtes Tickersymbol in einen DataFrame zu laden
        def load_data_into_dataframe(conn, price_tbl, limit):
            # SQL-Abfrage zum Abrufen aller Daten aus der entsprechenden Tabelle
            query = f"SELECT * FROM {price_tbl} GROUP BY Date ORDER BY Date DESC LIMIT {limit}"
            try:
                df = pd.read_sql_query(query, conn)
            except Exception as e:
                print(f"Error: {symbol} - {e} {period} {interval}")
                df = pd.DataFrame()

            try:
                df.sort_values(['Date'], ascending=[True], inplace=True)
                df.reset_index(inplace=True)
            except Exception:
                pass

            return df

        self.logger.debug(f'load_price_data: symbol={symbol} period={period} interval={interval} aggregate={aggregate}')
        price_db = self.get_path(path=self.database_path, file_name=f'yf_{symbol}.db')
        (price_tbl, limit) = tt.OHLCQueryPlanner(interval=interval, period=period).get_plan()
        # Debug: report the SQL LIMIT (required rows) chosen by the planner
        try:
            self.logger.debug("load_price_data: OHLCQueryPlanner -> price_tbl=%s limit=%s", price_tbl, limit)
        except Exception:
            pass

        df = pd.DataFrame()
        if price_tbl:
            conn = sqlite3.connect(price_db)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA cache_size = -32000")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA mmap_size = 67108864")
            df = load_data_into_dataframe(conn, price_tbl, limit=limit)
            conn.close()

            if self.get_num_rows(df) > 0:
                df.set_index('Date', inplace=True)
                df.index = pd.to_datetime(df.index, format='mixed')

                diff = 1
                df.index = df.index + pd.Timedelta(hours=diff)

                df.index = df.index.strftime(self.ftime_str)
                df.index.name = 'Date'

            try:
                df = self.aggregate_ohlc(df, interval=interval)
                df = df[df['Low'] != 0]
            except Exception:
                df = pd.DataFrame()

        return df
            
    def load_performance_data(self, symbol=''):

        # Datenbankname — liest direkt aus asset_simulation_.db (WAL erlaubt
        # gleichzeitige Schreib- und Lesezugriffe ohne Lock-Konflikt)
        table = "asset_simulation"
        db_name = self.get_path(path = 'database', file_name=f"{table}_.db")

        conn = sqlite3.connect(db_name)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA cache_size = -64000")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 268435456")
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sim_ticker_date ON asset_simulation (ticker, Date)")
        except Exception:
            pass
        # Ticker-Wert via ? übergeben — kein f-String (SQL-Injection-Schutz)
        query = f"""
        SELECT p1.* FROM {table} p1
            INNER JOIN (
            SELECT ticker, MAX(Date) AS max_date
            FROM {table}
            WHERE ticker = ?
            GROUP BY ticker
        ) p2
        ON p1.ticker = p2.ticker AND p1.Date = p2.max_date;
        """
        try:
            df = pd.read_sql_query(query, conn, params=(symbol,))
        except Exception:
            df = pd.DataFrame()
        df.reset_index(inplace=True)
#        print(type(df))
        conn.close()
        return df

    def calc_max_periods(self, interval, period, max_periods = 512):

        # periods m, d, wk, mo, y, max        
        # intervals m, d, wk, mo
        m_pe = max_periods 
        (value_i,unit_i) = self.split_interval(interval)
        (value_p,unit_p) = self.split_interval(period)
        
        units = {
                'm:d'   : (60*9),
                'm:wk'  : (60*9*5),
                'm:mo'  : (60*9*5*4),
                'm:y'  : (60*9*5*4*12),
                'd:wk'  : 5,
                'd:mo'  : 22,
                'd:y'   : 256,
                'wk:mo' : 4,
                'wk:y' : 52,
                'mo:y' : 12,
                'y:y' : 20,
         }

        for m in units:
            if m == f"{unit_i}:{unit_p}":
                u = units[m]
                m_pe = int(int(u) * int(value_p) / int(value_i))
            
        if not unit_i == '' and not unit_p == '':
            if unit_i == unit_p:
                m_pe = int(int(value_p) / int(value_i))
        if m_pe == 1:
            m_pe = max_periods

        return m_pe
        

    def get_xrate(self, symbol = 'EUR'):

        if symbol == self.system_currency:
            return 1
    
        symbol = symbol.upper()
    
#    yf.pdr_override()
        logging.getLogger('yfinance').setLevel(logging.CRITICAL)
    
#        if 1:
        try:
            # Use centralized ticker history wrapper for FX symbols
            df = md.ticker_history(f'{self.system_currency}{symbol}=X', period='1d', interval='1m')
            if df is None or df.empty:
                return 1, None
            if hasattr(df.index, 'strftime'):
                try:
                    df.index = df.index.strftime(self.ftime_str)
                except Exception:
                    pass
        except Exception:
            return 1, None

        val = df['Close'].iloc[-1]
        date = df.index[-1]
        return val, date

    def aggregate_ohlc(self, df:pd.DataFrame, interval="5m"):

        # conversion
        conversion_map = {
            "m": "min",
            "d": "D",
            "h": "H",
            "wk": "W-Fri",
            "mo": "M",
            "y": "Y"
        }

        (days, unit) = self.split_interval(interval)  
        interval = f"{int(days)}{conversion_map[unit]}"
        df.reset_index(inplace=True)
        df = df.set_index('Date')
        df.index = pd.to_datetime(df.index)

        ohlc = df.resample(interval).agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()        
        if unit == "wk":
            ohlc.index = ohlc.index + pd.DateOffset(days=-4)

        ohlc.reset_index(inplace=True)
        try:
            ohlc['Date'] = pd.to_datetime(ohlc['Date']).dt.strftime(self.ftime_str)
        except Exception as e:
            #print(e)
            pass
        ohlc.set_index('Date', inplace=True)
        
        return ohlc


    def fetch_data(self, symbol, period='1y', interval='1d', max_periods = 360, add_current=False, aggregate=False, pips_select = 0, region = st):
                
        max_periods = self.calc_max_periods(interval=interval, period=period, max_periods=max_periods)
        # Debug: log computed max_periods which will be used to slice the dataframe
        try:
            self.logger.debug("fetch_data: symbol=%s period=%s interval=%s calc_max_periods=%s", symbol, period, interval, max_periods)
        except Exception:
            pass
        logging.getLogger('yfinance').setLevel(logging.CRITICAL)

        df_t = self.load_ticker_data(symbol)
        df_p = self.load_price_data(symbol, period, interval, aggregate=aggregate)
        df_perf = self.load_performance_data(symbol)
        
        # if data is not in our database then get it from yahoo
        if not self.get_num_rows(df_t) or not self.get_num_rows(df_p):
            df_p, df_t = self.fetch_yahoo_ticker_data(symbol=symbol, period=period, interval=interval)

        if add_current and not interval[-1:] == 'm': 
            df = self.add_current_price(df_p, symbol)
        else:
            df = df_p

        # Debug: log number of rows before slicing by max_periods
            try:
                rows_before = len(df)
                self.logger.debug("fetch_data: rows_before_slice=%s", rows_before)
            except Exception:
                rows_before = None

        if max_periods:
            try:
                df = df[-max_periods:]
            finally:
                try:
                    rows_after = len(df)
                    self.logger.debug("fetch_data: rows_after_slice=%s (max_periods=%s)", rows_after, max_periods)
                except Exception:
                    pass
        
        if pips_select:
            stop = len(df)
            (pips_forward, pips_back) = region.slider(
                f"Select range:",
                0,
                stop,
                (0, stop)
                )

            df = df[pips_forward:pips_back]

        # Add performance indicators needed for analysis
        # If self.indicators is empty => skip heavy precompute (lazy mode)
        if self.indicators:
            # Only compute requested indicators and light helpers
            for tf in [9,21,50]:
                try:
                    df = indicator.ema(df,tf,'Close')
                except Exception:
                    pass
            for tf in [20,50,100,200]:
                try:
                    df = indicator.sma(df,tf,'Close')
                except Exception:
                    pass
            try:
                df['daily_returns']=(df['Close'].pct_change(fill_method=None))*100
            except Exception:
                df['daily_returns'] = 0
            if interval == '1d':
                try:
                    df = indicator.trend(df)
                except Exception:
                    pass
            try:
                df = indicator.log_return(df)
            except Exception:
                pass

            # instantiate specific indicator modules requested
            for itm in list(self.indicators):
                try:
                    # use existing helper to create instance and attach to self
                    self.init_instance(itm, df=df, symbol=symbol)
                    # if instance has df, adopt columns into main df
                    inst = getattr(self, itm, None)
                    # Debug: log instance df size/columns to detect unexpected truncation
                    try:
                        if inst is not None and hasattr(inst, 'df'):
                            rows = len(inst.df) if inst.df is not None else 0
                            idx_name = getattr(inst.df.index, 'name', None)
                            has_date_col = 'Date' in inst.df.columns
                            self.logger.debug("fetch_data: instantiated indicator=%s rows=%s idx_name=%s has_Date_col=%s cols=%s", itm, rows, idx_name, has_date_col, list(getattr(inst, 'df', pd.DataFrame()).columns))
                    except Exception:
                        pass
                    if inst is not None and hasattr(inst, 'df'):
                        try:
                            # Ensure index alignment: convert inst.df index to the same format/type as df.index
                            try:
                                # if main df index are strings formatted with ftime_str, convert inst index to same
                                if len(df) > 0 and len(inst.df) > 0:
                                    main_idx_sample = df.index[0]
                                    inst_idx_sample = inst.df.index[0]
                                    # main index is string timestamps
                                    if isinstance(main_idx_sample, str) and not isinstance(inst_idx_sample, str):
                                        inst_idx = pd.to_datetime(inst.df.index, errors='coerce').strftime(self.ftime_str)
                                        inst.df.index = inst_idx
                                    # main index is datetime-like and inst index is string
                                    elif (hasattr(main_idx_sample, 'tz') or hasattr(main_idx_sample, 'day')) and isinstance(inst_idx_sample, str):
                                        inst.df.index = pd.to_datetime(inst.df.index, errors='coerce')
                            except Exception:
                                pass

                            # merge columns from inst.df into df (prefer existing df values)
                            cols = [c for c in inst.df.columns if c not in df.columns]
                            df = pd.concat([df, inst.df[cols]], axis=1)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"Error {symbol} instanciating {itm}: {e}")
                    pass

            try:
                df = indicator.buy_sell(df,buy_query=self.buy_query,sell_query=self.sell_query)
            except Exception as e:
                self.logger.error(f"Error {symbol} calculating buy/sell signals: {e}")
                pass
        else:
            # Lazy mode: keep only light transforms
            try:
                df['daily_returns']=(df['Close'].pct_change(fill_method=None))*100
            except Exception:
                df['daily_returns']=0
                pass
            
        if max_periods:
            df = df[-max_periods:]

        try:
            if type(df_t) is dict:
                df_t = pd.DataFrame(df_t, index=[0])
            df_t = pd.merge( df_t, df_perf, on=['symbol','ticker'])
        except Exception:
            pass

        return df, df_t


    def add_current_price(self, df, symbol = '', from_yahoo = False):
                
        if not symbol == '' and not df.empty:
            if from_yahoo:
                df_minute, _ = self.fetch_yahoo_ticker_data(symbol=symbol, period='1d', interval='1m')
                if self.get_num_rows(df_minute):
                    df_minute.drop(['Dividends','Stock Splits'], axis=1, inplace=True)
            else:
                df_minute = self.load_price_data(symbol, period='1d', interval='1m', aggregate=False)    

            try:
                df_ts = df.index[-1]
                if isinstance(df_ts, pd.Timestamp) or isinstance(df_ts, datetime):
                    df_ts = df_ts.strftime(self.ftime_str)
                df_minute_ts = df_minute.index[-1]

                df_minute.index = pd.to_datetime(df_minute.index)
                df_minute = df_minute.resample('D').agg({
                    'Open': 'first',
                    'High': 'max',
                    'Low': 'min',
                    'Close': 'last',
                    'Volume': 'sum'
                })
                df_minute.index = df_minute.index.strftime('%Y-%m-%d 00:00:00')
                day_minute = pd.to_datetime(df_minute_ts).strftime("%Y-%m-%d 00:00:00")
                existing_days = pd.to_datetime(df.index, errors="coerce").strftime("%Y-%m-%d 00:00:00")

                if day_minute in existing_days.values:
                    df = df.iloc[:-1]

                df = pd.concat([df, df_minute.iloc[-1:]])                
            except Exception as e:
                print(e)
                pass
        return df

    
"""
# Beispielhafte Nutzung der Klasse
if __name__ == "__main__":

        ft = FetchData(database_path='./database')

        tickers = ft.load_ticker_data(symbol='')
        for symbol in tickers['ticker']:
            print(f'Fetching data for {symbol}.')
            (df_d, ticker_d) = ft.fetch_data(symbol)
            (df_m, _) = ft.fetch_data(symbol, interval='1mo')
            (df_w, _) = ft.fetch_data(symbol, interval='1wk')
            print(df_d)        
            print(ticker_d)    
            exit()
"""