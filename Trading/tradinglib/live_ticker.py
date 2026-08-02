import streamlit as st
from tradinglib import (
    ticker_tools as tt, tiny_chart as tc, multi_select as ms, system_config as sysconf,
    fetch_data, pushover_notifier as pn,
    graph_tools as gt
)

import glob
import os
import datetime as dt
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import time
import logging

logger = logging.getLogger(__name__)

for name, l in logging.root.manager.loggerDict.items():
    if "streamlit" in name:
        l.disabled = True

class LiveTicker(fetch_data.FetchData):

    charts_config = {
                    "scrollZoom": True,
                    "displayModeBar": True,
                    'editSelection': True,
                    'editable': False,
                    "modeBarButtonsToAdd": [
                        "drawline",
                        "drawopenpath",
                        "drawclosedpath",
                        "drawcircle",
                        "drawrect",
                        "eraseshape"]
                    }

    def __init__(self, db_path='database', db_table="ticker_data", init=False, region=st, username='admin', is_admin=False, days_back=10):
        """Initialize the live ticker, optionally create the DB table, and load historical tick data."""
        # create empty index (timestamp + symbol)
        self.df = pd.DataFrame(columns=["timestamp", "symbol", "price"])
        # set path
        self.db_path = db_path
        self.db_table = db_table
        self.days_back = days_back
        self.region = region
        self.init = init
        self.value = 0
        self.momentum = 0
        self.trend = 0
        self.market_price = 0
        self.trend_ticker = ''
        self.username = username
        self.is_admin = is_admin
        self.sys_conf = sysconf.SystemConfig(region=region, username=self.username, is_admin=self.is_admin)
        self.notfr = pn.PushoverNotifier(storage_file=self.get_path(file_name="pushover_notifier_momentum.json"))
        self.multi_selector = ms.MultiCheckboxSelector(region=st, sys_conf=self.sys_conf)

        if self.init:
            # Create database if it does not exist
            self._initialize_db()

        # Load database
        self.load_from_db(db_name=f"{db_table}.db")
    
    def _connect_db(self, db_name=''):
        """Open and return a SQLite connection to the ticker_data database."""
        if db_name == "":
            db_name = f"{self.db_table}.db"
        db = tt.tools.Db_tools(db_path=self.db_path, database_name=db_name)            
        return db.conn
        
    def _initialize_db(self):
        """Create the ticker_data table with a (timestamp, symbol) primary key if absent."""
        
        conn = self._connect_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticker_data (
                timestamp TEXT,
                symbol TEXT,
                price REAL,
                PRIMARY KEY (timestamp, symbol)
            )
        """)
        conn.commit()
        conn.close()
    
    def save_to_db(self):
        """Persist current tick data to SQLite using INSERT OR REPLACE to avoid duplicates."""
        conn = self._connect_db()
        self.df = self.df[~self.df.index.duplicated(keep='last')]  # Remove duplicate entries
        for idx, row  in self.df.iterrows():
            timestamp = row["timestamp"]
            symbol = row["symbol"]
            price = row["price"]
            conn.execute("""
                INSERT OR REPLACE INTO ticker_data (timestamp, symbol, price)
                VALUES (?, ?, ?)
            """, (timestamp, symbol, price)) # timestamp.isoformat()
#                ON CONFLICT(timestamp, symbol) DO UPDATE SET price=excluded.price
        
        conn.commit()
        conn.close()

    def get_time_strings(self):
        """Return (now, today_str, yesterday_str, tomorrow_str) as formatted date strings."""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        return (now, today_str, yesterday_str, tomorrow_str) 
       
    def resolve_timestamp(self, time_str):
        """Determine whether an HH:MM:SS string belongs to today or yesterday and return the full datetime string."""
        (now, today_str, yesterday_str, tomorrow_str) = self.get_time_strings()

        full_timestamp_today = datetime.strptime(f"{today_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        full_timestamp_yesterday = datetime.strptime(f"{yesterday_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        
        if full_timestamp_today < now:
            return full_timestamp_today.strftime("%Y-%m-%d %H:%M:%S")  # Belongs to today
        if full_timestamp_today >= now:
            return full_timestamp_yesterday.strftime("%Y-%m-%d %H:%M:%S")  # Belongs to yesterday
   
    def add_tick_data(self, time_str, symbol, price):
        """Append a new tick to self.df and persist it to the database without duplicates."""
        timestamp = self.resolve_timestamp(time_str)
        
        new_data = pd.DataFrame({"timestamp": [timestamp], 
                                  "symbol": [symbol], 
                                  "price": [price]})
        
        self.df = pd.concat([self.df, new_data]).drop_duplicates()
        self.save_to_db()
    
    def rename_db(self, file_date=''):
        """Archive the current database file with a timestamp suffix and create a fresh one."""
        path = self.get_path(path=self.db_path, file_name=f"{self.db_table}.db")
        day_diff = 0
        if file_date == '':
            file_date = (dt.datetime.now() + dt.timedelta(days=day_diff)).strftime("%Y-%m-%d.%f")[:-3]
        os.rename(path, f"{path[:-3]}_{file_date}.db")
        time.sleep(5)
        os.unlink(path)
        time.sleep(5)
        self._initialize_db()
        pass

    def load_from_file_backwards(self, db_name: str):
        """Load and merge tick data from up to days_back archived database files, starting from db_name."""

        days = self.days_back    
        db_files = []
        base_name = os.path.basename(db_name)
        db_path = self.get_path(path=self.db_path)
        # Determine start date
        if base_name == "ticker_data.db":
            start_datetime = datetime.now()
        else:
            try:
                ts = base_name.replace("ticker_data_", "").replace(".db", "")
                start_datetime = datetime.strptime(ts, "%Y-%m-%d %H-%M-%S")
            except ValueError as ve:
                logger.warning("Invalid start file format: %s", db_name)
                return

        # Insert start file first
        full_start_path = os.path.join(db_path, db_name)
        if os.path.exists(full_start_path):
            db_files.append((start_datetime, full_start_path))
        else:
            logger.warning("Start file not found: %s", full_start_path)
            return

        # Backwards for n-1 additional days
        for i in range(1, days):
            target_day = (start_datetime - timedelta(days=i)).date()
            pattern = os.path.join(db_path, f"ticker_data_{target_day.strftime('%Y-%m-%d')}*.db")
            matched_files = sorted(glob.glob(pattern), reverse=True)

            for file in matched_files:
                try:
                    ts = os.path.basename(file).replace("ticker_data_", "").replace(".db", "")
                    file_time = datetime.strptime(ts, "%Y-%m-%d %H-%M-%S")
                    db_files.append((file_time, file))
                    break  # nur eine Datei pro Tag
                except ValueError:
                    continue

        # Sort chronologically
        db_files.sort()

        # Load data
        all_data = []
        for _, db_file in db_files:
            conn = self._connect_db(db_name=db_file)
            try:
                df = pd.read_sql(f"SELECT * FROM {self.db_table}", conn)
                all_data.append(df)
            except Exception as e:
                logger.error("Error loading from %s: %s", db_file, e)
            finally:
                conn.close()

        # Merge
        if all_data:
            self.df = pd.concat(all_data, ignore_index=True)
            self.df = self.df.sort_values(by=self.df.columns[0])
        else:
            self.df = pd.DataFrame()

        self.get_symbol_list()
    
    def load_from_db(self, timestamp="", db_name = ''):
        """Load stored tick data from the SQLite database."""
        where = ""
#        if not timestamp == "" and db_name == "":
#            where = f' WHERE timestamp > "{start_date}"'
        query = f"SELECT * FROM {self.db_table} {where}"
        conn = self._connect_db(db_name=db_name)
        try:
            self.df = pd.read_sql(query, conn)
        except Exception as e:
            logger.error("Error, loading data from %s: %s", self.db_table, e)
        finally:
            conn.close()
        self.get_symbol_list()
        
    def get_price_line(self):
        """Query the latest price for each symbol and format a price-line summary string."""
        query = f"SELECT MAX(timestamp),timestamp,symbol,price FROM  {self.db_table} GROUP BY symbol"
        conn = self._connect_db()
        try:
            ticker_df = pd.read_sql(query, conn)
        except Exception as e:
            logger.warning("Keine gespeicherten Daten gefunden oder Fehler beim Laden: %s", e)
        finally:
            conn.close()
        price_line = ""
        try:
            for idx, row in ticker_df.iterrows():
                try:
                    price_line += f'- {row["symbol"]}  {row["price"]} @ {row["timestamp"][10:]} -'
                except Exception:
                    pass
        except Exception:
            pass
        return price_line

    def aggregate_ohlc(self, interval="5min", symbol=''):
        """Aggregate tick data into OHLC values for a given time interval."""
        ohlc = pd.DataFrame()

        df_combined = self.df[self.df['symbol']==symbol].copy()
        # Sort values by symbol and timestamp to ensure correct EMA calculation
        df_combined = df_combined.sort_values(by=["symbol", "timestamp"])

        df_combined["timestamp"] = pd.to_datetime(df_combined.get("timestamp", []))
        df_combined = df_combined.set_index(["timestamp", "symbol"])
        if not df_combined.empty:
            ohlc = df_combined.groupby("symbol").resample(interval, level=0).agg({"price": ["first", "max", "min", "last"]}).dropna()
            ohlc.columns = ["Open", "High", "Low", "Close"]
            # Calculate MAs for each symbol
            mas = [9,21,50,100,200]
            for ema in mas:
                ohlc[f"ema{ema}"] = ohlc["Close"].transform(lambda x: x.ewm(span=ema, adjust=False).mean())
            for sma in mas:
                ohlc[f"sma{sma}"] = ohlc["Close"].transform(lambda x: x.rolling(int(sma)).mean()) 

        return ohlc       


    def plot_candlestick(self, symbol, interval="5min", oszillators=['ewo','rsi'], overlays=['atc','fvg','pre','bos','candle'], limit_start=False):
        """Create a Plotly candlestick chart for a symbol."""

        ohlc_df = self.aggregate_ohlc(symbol=symbol, interval=interval)
        if symbol not in ohlc_df.index:
            logger.debug("No data for %s", symbol)
            return None

        ohlc_df = ohlc_df.reset_index()
        ohlc_df = ohlc_df.rename(columns={"timestamp":"Date"})
        ohlc_df['Date'] = pd.to_datetime(ohlc_df['Date'], format='%Y-%m-%d %H:%M:%S')
        ohlc_df = ohlc_df.loc[ohlc_df['symbol']==symbol]


        if ohlc_df.empty:
            st.write(f"Still no data for {interval}")
            return
        
        rows = len(oszillators)
        row_width = []
        for i in range(rows):
            row_width.append(0.4 / rows)
        row_width.append(0.6)
        fig = go.Figure()
        fig = make_subplots(
                rows = rows+1,
                cols = 1,
                shared_xaxes=True,
                vertical_spacing=0.1,
                row_width=row_width,
        )

        fig.update_layout(
            autosize = False,            
            height=800,
            width=500
        )       

        if "heikin" in overlays:
            self.init_instance("heikin", df=ohlc_df)
            for trace in self.heikin.fig.data:    
                fig.add_trace(trace, row=1, col=1)
            for shape in self.heikin.fig.layout.shapes:    
                fig.add_shape(shape, row=1, col=1)                    
            for annotation in self.heikin.fig.layout.annotations:
                fig.add_annotation(annotation, row=1, col=1)              

        if "candle" in overlays:
            self.init_instance("candle", df=ohlc_df)
            self.candle.add_fig()
            for trace in self.candle.fig.data:    
                fig.add_trace(trace, row=1, col=1)
            for shape in self.candle.fig.layout.shapes:    
                fig.add_shape(shape, row=1, col=1)                    
            for annotation in self.candle.fig.layout.annotations:
                fig.add_annotation(annotation, row=1, col=1)              

        emas = {
                9:"darkorange",
                21:"darkblue",
                50:"black",
        }
        smas = {
                100:"grey",
                200:"darkred",
            }

#        if interval == "1min":
        if 1:
            for ma in emas:
                color = emas[ma]
                fig.add_trace(
                    go.Scatter(x = ohlc_df['Date'], y = ohlc_df[f'ema{ma}'], name = f'EMA {ma}',
                        line_color = color,
                        line = { 'width':0.8},
                        showlegend = False,
                        ),
                    row = 1,
                    col = 1,
                )           

            for ma in smas:
                color = smas[ma]
                fig.add_trace(
                    go.Scatter(x = ohlc_df['Date'], y = ohlc_df[f'sma{ma}'], name = f'SMA {ma}',
                        line_color = color,
                        line = { 'width':0.8},
                        showlegend = False,
                        ),
                    row = 1,
                    col = 1,
                )           
        else:
            pass

        row = 1
        for ovl in overlays:
#            if interval == "1min" or interval == "5min":# and (ovl == 'fvg' or ovl == "bsz" or 'ici' or 'lzq'):
            try:
                self.init_instance(ovl, df=ohlc_df, symbol=symbol)
                obj = getattr(self, ovl)
                obj.add_fig()
                for trace in obj.fig.data :    
                    fig.add_trace(trace, row=row, col=1)
                for shape in obj.fig.layout.shapes:    
                    fig.add_shape(shape, row=row, col=1)
                for annotation in obj.fig.layout.annotations:
                    fig.add_annotation(annotation, row=1, col=1)              
                fig['layout'][f'yaxis{row}']['title'] = ovl
            except Exception:
                pass
        row = 2
        try:
            for osz in oszillators:
                self.init_instance(osz, df=ohlc_df)
                obj = getattr(self, osz)
                obj.add_fig()
                for trace in obj.fig.data :    
                    fig.add_trace(trace, row=row, col=1)
                for shape in obj.fig.layout.shapes:    
                    fig.add_shape(shape, row=row, col=1)
                for annotation in obj.fig.layout.annotations:
                    fig.add_annotation(annotation, row=1, col=1)              
                fig['layout'][f'yaxis{row}']['title'] = osz
                row+=1

                # We need the following values to identify trend signals
                if osz == "ewo":
                    try:
                        ohlc_df = pd.concat([ohlc_df.reset_index(drop=True),
                            obj.df[['ewo', 'ewo_ema', 'ewo_diff']].reset_index(drop=True)], axis=1)
                    except Exception:
                        pass
                if osz == "rsi":
                    try:
                        ohlc_df = pd.concat([ohlc_df.reset_index(drop=True), 
                            obj.df[['rsi', 'rsi_ema', 'stoch']].reset_index(drop=True)], axis=1)
                    except Exception:
                        pass
        except Exception:
            pass

        fig.update_xaxes(
            rangeslider_visible = False,
            zeroline=False,
            spikedash='solid',
            spikemode='across',
            spikesnap='cursor',
            showspikes=True,
            spikethickness=0.5,
            )

        fig.update_yaxes(
            showticklabels=True,
            showspikes=True,
            spikethickness=0.5,
            spikemode='across',
            spikesnap='cursor',
            spikedash='solid'
            )

        min_close = ohlc_df['Low'].min()
        max_close = ohlc_df['High'].max()
        fig.update_layout(yaxis=dict(range=[min_close, max_close],))
        fig.update_layout(title=f"{symbol}-{interval}", yaxis_title="price")

        if interval == "1min":
            self.momentum = 0
            self.value = 0
            self.trend = 0
            self.trend_ticker = ''
            self.market_price = ''

        ohlc_df = ohlc_df.fillna(0).infer_objects(copy=False)
        if 1:
#        if self.symbol == "^GDAXI":

            self.trend_ticker = self.symbol
            self.market_price = round(ohlc_df['Close'].iloc[-1],1)
            try:
                self.value += ohlc_df['ewo'].iloc[-2] - ohlc_df['ewo_ema'].iloc[-2]
                self.momentum += (ohlc_df['stoch'].iloc[-2])# - ohlc_df['rsi_ema'].iloc[-2])
            except Exception:
                pass
            try:
                if ohlc_df['ewo_diff'].iloc[-2] < 0:
                    self.trend += -1
                elif ohlc_df['ewo_diff'].iloc[-2] > 0:
                    self.trend += 1
            except Exception:
                pass

            (value_i, unit_i) = self.split_interval(interval)
            
            if unit_i == "min":

                end = ohlc_df['Date'].iloc[-1]
                length = 120 # pips                
                
                if value_i > 1:
                    length *= 5 # 5h min
                    
                if not type(end) == int:

                    start = end - timedelta(minutes=length)

                    fig.update_xaxes(
                        type="date", 
                        range=[start, end]
                        )

                    # DataFrame auf Zoom-Bereich filtern
                    visible_df = ohlc_df[(ohlc_df['Date'] >= start) & (ohlc_df['Date'] <= end)]

                    # Min/Max der sichtbaren Y-Werte berechnen
                    min = visible_df['Low'].min()
                    max = visible_df['High'].max()
                    padding = (max - min) * 0.05
                    fig.update_yaxes(
                        range=[min-padding, max+padding],
                        row=1, col=1
                        )

        fig.update_xaxes(
            rangebreaks = [
                dict(bounds=["sat", "mon"]),
                dict(bounds=[22, 8], pattern="hour"),
                ]
        )

        return fig
    
    def get_idx_selected(self, v_list, v_key, default=0):
        """Return the index of v_key in v_list, or default when not found."""
        try:
            default = v_list.index(v_key)
        except Exception:
            pass    
        return default

    def get_symbol_list(self):
        """Populate self.symbol_list with the unique symbol names present in self.df."""
        self.symbol_list = self.df["symbol"].unique().tolist()

    def cleanup(self):
        """Archive the current database file when no file for today exists yet."""
        now = dt.datetime.now()
        file_date = now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            file_date = self.df['timestamp'].iloc[-2]
        except Exception:
            pass
        date = now.strftime("%Y-%m-%d")
        files = self.get_database_files(f"{date} *")
        if files == []:           
            self.rename_db(file_date=file_date.replace(":","-"))
    
    def get_database_files(self, asterik='*'):
        """Return a sorted list of archived database filenames matching the glob pattern."""
        path = self.get_path(path=self.db_path, file_name=f"{self.db_table}_{asterik}.db")
        db_files = sorted(glob.glob(path), reverse=True)
        return [os.path.basename(db) for db in db_files]

    def render(self, default="^GDAXI", region=st, bare_mode=False):
        """Render the live candlestick chart and price ticker in the Streamlit app."""
#        if not bare_mode:
        
        databases = self.get_database_files()
        databases.insert(0, "")
        limit_start = True
        if databases and not bare_mode:
            # Dropdown to select the database
            selected_db = st.selectbox("Choose database:", databases)

            if selected_db and selected_db != "":
                
                # Load data from the selected database
                self.load_from_file_backwards(selected_db)
                limit_start = False

        if not bare_mode:
                # Create an instance of the class and display the selectors
                self.multi_selector.render()
                if self.sys_conf.get_value("pine_export", False):
                    self.multi_selector.render_pine_export()
                interval = self.multi_selector.get_selected_options('Interval')[:1]
                period = self.multi_selector.get_selected_options('Period')[:1]
                overlays = self.multi_selector.get_selected_options('Overlay')
                oszilators = self.multi_selector.get_selected_options('Oszilator')

                (interval, period, overlays, oszilators) = self.sys_conf.get_selectors(interval, period, overlays, oszilators)
                    
                trend_length = 21
                max_trend_length = self.calc_max_periods(interval,period)
                if trend_length > max_trend_length:
                    trend_length = int(max_trend_length/2)

                self.url = f"/?symbol="        

        if limit_start:
            self.load_from_db(timestamp='06:00',db_name="ticker_data.db") # timestamp='06:00'

        self.symbol_list.sort()
        idx = self.get_idx_selected(self.symbol_list,default,2)
        self.symbol = default
        if not bare_mode:
            try:
                self.symbol = st.selectbox("Choose Symbol", self.symbol_list if self.symbol_list else ["No data"],index=idx)
            except Exception:
                pass
        
        price_line = self.get_price_line()

        if not bare_mode:
            region.write(price_line)      
        
        charts = ["1min","5min","15min"]
        if bare_mode:
            oszilators=['ewo','rsi']
            overlays=['atc','candle','bos','pre','sup','heikin','obd']
        for chrt in charts:
            fig = self.plot_candlestick(self.symbol, chrt, oszillators=oszilators, overlays=overlays, limit_start=limit_start)        
            if not bare_mode and fig:
                st.plotly_chart(fig,
                        use_container_width = True,
                        theme="streamlit",
                        config = self.charts_config,
                        )


        message = f"""Index: {self.symbol} - Indicator: {round(self.value,1)} / {round(self.momentum,1)}, trend: {self.trend}"""
        if not bare_mode:
            st.write(message)
        else:
            logger.info("%s", message)

        del_btn = st.button("Delete cached ticker entries")
        if del_btn:
            self.cleanup()
            if not bare_mode:
                st.rerun()
        try:
            if not bare_mode:

                show_history = st.checkbox("Show history: ",False)
                if show_history:

                    st.plotly_chart(tc.tiny_chart(self.symbol,f' {interval} / {period} trend',period,interval,True, True,range_breaks=True,add_sub_plots=oszilators, add_overlays=overlays, trend_length=trend_length, zoom=True).fig,
                        use_container_width = True,
                        theme="streamlit",
                        config = self.charts_config
                    )
        except Exception:
            pass
        
    def notifier(self, bare_mode=False):
        """Send a Pushover notification with the current trend signal and price information."""
        message = f"""Symbol: {self.trend_ticker}
Indicator: {round(self.value,1)}, Trend: {self.trend}
Momentum: {round(self.momentum,1)}
Market price: {self.market_price}
"""
        if not bare_mode:
            pass
        else:
            if abs(self.trend) > 0 and abs(self.value) >= 30:
                logger.info("notifiying: %s", message)
                self.notfr.send_notification(ticker=self.symbol,price=self.market_price, date=self.df['timestamp'].iloc[-1],message=message, title=f"""Dax {"SHORT" if self.value < 0 else "LONG"} indicator""")

