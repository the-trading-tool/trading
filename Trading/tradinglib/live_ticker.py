import streamlit as st
from tradinglib import (
    ticker_tools as tt, tiny_chart as tc, multi_select as ms, system_config as sysconf,
    fetch_data, pushover_notifier as pn,
    graph_tools as gt
)
from tradinglib.i18n import t

import glob
import os
import datetime as dt
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

for name, l in logging.root.manager.loggerDict.items():
    if "streamlit" in name:
        l.disabled = True

# A quote time up to this many minutes ahead of the local clock is still treated
# as today's (source clock skew), not as yesterday's.
FUTURE_TOLERANCE_MIN = 5


def resolve_timestamp(time_str, tolerance_min=FUTURE_TOLERANCE_MIN):
    """Map a quote time onto a full timestamp string.

    A full "YYYY-MM-DD HH:MM:SS" is taken as-is — a collector that knows both
    the source's timezone and its own is the only party that can resolve the
    date correctly, so its verdict wins.

    A bare HH:MM:SS is resolved against the local clock: slightly ahead still
    counts as today (the source's clock may run a few seconds fast), further
    ahead means yesterday. Returns None when the input holds no usable time.

    ⚠ With a bare clock time, a collector running in a different timezone than
    the source pushes every quote a day back. Send the full timestamp instead.
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(time_str).strip(), fmt).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            continue

    parsed = None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(str(time_str).strip(), fmt).time()
            break
        except (TypeError, ValueError):
            continue
    if parsed is None:
        return None

    now = datetime.now()
    stamp = datetime.combine(now.date(), parsed)
    if stamp > now + timedelta(minutes=tolerance_min):
        stamp -= timedelta(days=1)
    return stamp.strftime("%Y-%m-%d %H:%M:%S")


def to_price(value):
    """Coerce a scraped price into a float, accepting German and plain formats."""
    if isinstance(value, (int, float)):
        price = float(value)
        return price if price == price and abs(price) != float('inf') else None
    text = str(value).strip()
    if not text:
        return None
    if ',' in text:
        # "24.004,02" -> 24004.02 ; a lone comma is the decimal separator
        text = text.replace('.', '').replace(',', '.') if '.' in text else text.replace(',', '.')
    try:
        return float(text)
    except ValueError:
        return None

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

    # A quote time up to this many minutes ahead of the local clock is still
    # treated as today's (source clock skew), not as yesterday's.
    future_tolerance_min = 5

    # Moving averages drawn in the intraday charts — nothing else is computed.
    ema_spans = (9, 21, 50)
    sma_spans = (100, 200)

    # Oscillator columns that feed the trend/momentum signal of notifier().
    signal_columns = {
        'ewo': ['ewo', 'ewo_ema', 'ewo_diff'],
        'rsi': ['rsi', 'rsi_ema', 'momentum'],
    }

    # Resampling intervals of the live tick chart. All of them are computed for
    # the trend signal; the user picks which one is drawn.
    tick_intervals = ("1min", "5min", "15min")
    tick_interval_key = 'live_tick_interval'

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
        self.symbol_list = []
        self._ohlc_cache = {}
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
        self.ensure_table(db_path=self.db_path, db_table=self.db_table)

    @classmethod
    def ensure_table(cls, db_path='database', db_table='ticker_data'):
        """Create the tick table if it is absent — usable without an instance."""
        db = tt.tools.Db_tools(db_path=db_path, database_name=f"{db_table}.db")
        try:
            db.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {db_table} (
                    timestamp TEXT,
                    symbol TEXT,
                    price REAL,
                    PRIMARY KEY (timestamp, symbol)
                )
            """)
            db.conn.commit()
        finally:
            db.conn.close()

    @classmethod
    def store_ticks(cls, ticks, db_path='database', db_table='ticker_data'):
        """Parse and persist ticks without building a LiveTicker instance.

        The ingest endpoint only needs to write: constructing the full class
        would load the day's ticks into a DataFrame and set up config, notifier
        and selector objects for nothing.
        """
        rows = cls.prepare_rows(ticks)
        if not rows:
            return 0
        cls.ensure_table(db_path=db_path, db_table=db_table)
        return cls.write_rows(rows, db_path=db_path, db_table=db_table)

    @classmethod
    def prepare_rows(cls, ticks):
        """Turn (time_str, symbol, price) tuples into storable rows.

        Entries with an unusable time or price are skipped and logged, so one
        bad value never costs the whole batch.
        """
        rows = []
        for time_str, symbol, price in ticks:
            timestamp = resolve_timestamp(time_str)
            value = to_price(price)
            if timestamp is None or value is None or not symbol:
                logger.warning("Skipping tick %s / %s / %s", symbol, time_str, price)
                continue
            rows.append((timestamp, str(symbol), value))
        return rows

    @classmethod
    def write_rows(cls, rows, db_path='database', db_table='ticker_data'):
        """Write (timestamp, symbol, price) tuples in a single transaction."""
        rows = list(rows)
        if not rows:
            return 0
        db = tt.tools.Db_tools(db_path=db_path, database_name=f"{db_table}.db")
        conn = db.conn
        try:
            with conn:
                conn.executemany(
                    f"INSERT OR REPLACE INTO {db_table} (timestamp, symbol, price) "
                    f"VALUES (?, ?, ?)", rows)
        except Exception as e:
            logger.error("Error writing %s ticks: %s", len(rows), e)
            return 0
        finally:
            conn.close()
        return len(rows)
    
    def save_to_db(self):
        """Persist the whole in-memory tick frame to SQLite (INSERT OR REPLACE).

        Duplicates are resolved on (timestamp, symbol) — the previous version
        deduplicated on the *row index*, which after a concat is a repeated
        counter, so it silently dropped all but one row.
        """
        if self.df.empty:
            return 0
        self.df = self.df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
        return self._write_ticks(
            self.df[["timestamp", "symbol", "price"]].itertuples(index=False, name=None))

    def _write_ticks(self, rows):
        """Write (timestamp, symbol, price) tuples in a single transaction."""
        return self.write_rows(rows, db_path=self.db_path, db_table=self.db_table)

    _to_price = staticmethod(to_price)   # kept as a method for existing callers

    def get_time_strings(self):
        """Return (now, today_str, yesterday_str, tomorrow_str) as formatted date strings."""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        return (now, today_str, yesterday_str, tomorrow_str) 
       
    def resolve_timestamp(self, time_str):
        """Instance wrapper around the module-level resolve_timestamp()."""
        return resolve_timestamp(time_str, tolerance_min=self.future_tolerance_min)

    def add_tick_data(self, time_str, symbol, price):
        """Append a single tick and persist it. Returns True when it was stored."""
        return self.add_tick_batch([(time_str, symbol, price)]) == 1

    def add_tick_batch(self, ticks):
        """Append many ticks in one transaction and return how many were stored.

        `ticks` is an iterable of (time_str, symbol, price). Entries with an
        unusable time or price are skipped and logged, so one bad value never
        costs the whole batch. Only the new rows are written — the previous
        implementation rewrote the complete frame on every single tick.
        """
        rows = self.prepare_rows(ticks)
        if not rows:
            return 0

        new_data = pd.DataFrame(rows, columns=["timestamp", "symbol", "price"])
        if self.df is None or self.df.empty:
            self.df = new_data
        else:
            self.df = pd.concat([self.df, new_data], ignore_index=True)
        self.df = self.df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
        stored = self._write_ticks(rows)
        self._ohlc_cache = {}
        self.get_symbol_list()
        return stored


    def rename_db(self, file_date=''):
        """Archive the current database file with a timestamp suffix and create a fresh one.

        The old implementation unlinked the source path *after* renaming it,
        which raised FileNotFoundError and aborted the daily cleanup.
        """
        path = self.get_path(path=self.db_path, file_name=f"{self.db_table}.db")
        if file_date == '':
            file_date = dt.datetime.now().strftime("%Y-%m-%d.%f")[:-3]
        archive = f"{path[:-3]}_{file_date}.db"
        try:
            os.replace(path, archive)
            logger.info("Archived tick database as %s", os.path.basename(archive))
        except OSError as e:
            logger.error("Could not archive %s: %s", path, e)
            return False
        self._initialize_db()
        self.df = pd.DataFrame(columns=["timestamp", "symbol", "price"])
        self._ohlc_cache = {}
        self.get_symbol_list()
        return True

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
        """Load stored tick data from the SQLite database.

        Rows come back in chronological order — cleanup() and notifier() read
        the last row and would otherwise pick an arbitrary tick.
        """
        params = ()
        where = ""
        if timestamp:
            where = " WHERE timestamp >= ?"
            params = (timestamp,)
        query = f"SELECT timestamp, symbol, price FROM {self.db_table}{where} ORDER BY timestamp"
        conn = self._connect_db(db_name=db_name)
        try:
            self.df = pd.read_sql(query, conn, params=params)
        except Exception as e:
            logger.error("Error, loading data from %s: %s", self.db_table, e)
        finally:
            conn.close()
        self._ohlc_cache = {}
        self.get_symbol_list()
        
    def get_price_line(self):
        """Query the latest price for each symbol and format a price-line summary string."""
        query = (f"SELECT symbol, MAX(timestamp) AS timestamp, price "
                 f"FROM {self.db_table} GROUP BY symbol ORDER BY symbol")
        conn = self._connect_db()
        ticker_df = pd.DataFrame()
        try:
            ticker_df = pd.read_sql(query, conn)
        except Exception as e:
            logger.warning("No stored tick data found, or loading failed: %s", e)
        finally:
            conn.close()

        parts = []
        for _, row in ticker_df.iterrows():
            try:
                parts.append(f'{row["symbol"]} {row["price"]} @ {str(row["timestamp"])[11:]}')
            except (KeyError, TypeError):
                continue
        return " - ".join(parts)

    def tick_series(self, symbol):
        """Return one symbol's ticks as a time-indexed float Series."""
        if self.df is None or self.df.empty or 'symbol' not in self.df.columns:
            return pd.Series(dtype='float64')
        ticks = self.df.loc[self.df['symbol'] == symbol, ['timestamp', 'price']]
        if ticks.empty:
            return pd.Series(dtype='float64')
        index = pd.to_datetime(ticks['timestamp'], errors='coerce')
        series = pd.Series(pd.to_numeric(ticks['price'], errors='coerce').values, index=index)
        return series.dropna().sort_index()

    # Thin space (U+2009): groups the digits without committing to a decimal
    # convention, which differs between the German and the English UI.
    thousands_separator = " "

    @classmethod
    def format_price(cls, value):
        """Format a price with a precision that suits its magnitude.

        A single format cannot serve both an index (26491.29) and an FX rate;
        the raw float would print 1.153499960899353.
        """
        try:
            value = float(value)
        except (TypeError, ValueError):
            return ''
        if abs(value) >= 10:
            return f"{value:,.2f}".replace(",", cls.thousands_separator)
        return f"{value:.4f}"

    def price_summary(self):
        """Return one row per symbol: last price, change since session start, age.

        Built from the loaded tick frame rather than a fresh query, so the table
        always matches what the charts show (including an archived database).
        """
        empty = pd.DataFrame(columns=['symbol', 'price', 'change', 'time', 'age'])
        if self.df is None or self.df.empty or 'symbol' not in self.df.columns:
            return empty

        frame = self.df.copy()
        frame['price'] = pd.to_numeric(frame['price'], errors='coerce')
        frame = frame.dropna(subset=['price']).sort_values('timestamp')
        if frame.empty:
            return empty

        grouped = frame.groupby('symbol')['price']
        summary = pd.DataFrame({
            'price': grouped.last(),
            'first': grouped.first(),
            'time': frame.groupby('symbol')['timestamp'].last(),
        }).reset_index()

        summary['change'] = (summary['price'] - summary['first']) / summary['first'] * 100
        newest = pd.to_datetime(summary['time'], errors='coerce').max()
        summary['age'] = (newest - pd.to_datetime(summary['time'], errors='coerce')
                          ).dt.total_seconds().div(60).round(0)
        summary['time'] = summary['time'].astype(str).str.slice(11, 19)
        return summary[['symbol', 'price', 'change', 'time', 'age']].sort_values('symbol')

    def render_price_summary(self, region=st, stale_after_min=15, columns=3):
        """Show the latest quote per symbol as a compact table.

        Replaces the single run-on text line, which neither rounded sensibly nor
        showed that a quote had stopped updating. The rows are spread over
        several columns so fifteen symbols do not push the chart off-screen.
        """
        summary = self.price_summary()
        if summary.empty:
            region.info(t('live.no_quotes'))
            return summary

        table = pd.DataFrame({
            t('live.col_symbol'): summary['symbol'],
            t('live.col_price'): summary['price'].map(self.format_price),
            t('live.col_change'): summary['change'],
            t('live.col_time'): [f"{stamp} ⏳" if age and age >= stale_after_min else stamp
                                 for stamp, age in zip(summary['time'], summary['age'])],
        })
        config = {
            t('live.col_change'): st.column_config.NumberColumn(
                t('live.col_change'), format="%+.2f %%", help=t('live.col_change_help')),
        }

        columns = max(1, min(columns, len(table)))
        per_column = -(-len(table) // columns)          # ceil
        height = 36 * per_column + 38                   # show every row, no scrolling
        try:
            slots = region.columns(columns)
        except Exception:
            slots = [region]
            per_column = len(table)

        for index, slot in enumerate(slots):
            chunk = table.iloc[index * per_column:(index + 1) * per_column]
            if chunk.empty:
                continue
            slot.dataframe(chunk, hide_index=True, use_container_width=True,
                           height=height, column_config=config)

        stale = int((summary['age'] >= stale_after_min).sum())
        if stale:
            region.caption(t('live.stale_hint', count=stale, minutes=stale_after_min))
        return summary

    def aggregate_ticks(self, interval="5min", symbol=''):
        """Aggregate one symbol's ticks into an OHLC frame plus its moving averages.

        Results are cached per (symbol, interval) until new ticks arrive —
        render() asks for three intervals of the same symbol in a row.
        Named differently from FetchData.aggregate_ohlc(df, interval) on purpose:
        overriding that method with an incompatible signature broke every
        inherited price-loading path.
        """
        cache_key = (symbol, interval, len(self.df) if self.df is not None else 0)
        cached = self._ohlc_cache.get(cache_key)
        if cached is not None:
            return cached.copy()

        series = self.tick_series(symbol)
        if series.empty:
            return pd.DataFrame()

        ohlc = series.resample(interval).agg(["first", "max", "min", "last"]).dropna()
        if ohlc.empty:
            return pd.DataFrame()
        ohlc.columns = ["Open", "High", "Low", "Close"]
        ohlc.index.name = "timestamp"
        # Only the averages that are actually drawn — the old code built ten.
        for span in self.ema_spans:
            ohlc[f"ema{span}"] = ohlc["Close"].ewm(span=span, adjust=False).mean()
        for window in self.sma_spans:
            ohlc[f"sma{window}"] = ohlc["Close"].rolling(int(window)).mean()

        self._ohlc_cache = {cache_key: ohlc}
        return ohlc.copy()

    def prepare_frame(self, symbol, interval="5min"):
        """Return the resampled frame in the shape the indicator classes expect."""
        ohlc = self.aggregate_ticks(symbol=symbol, interval=interval)
        if ohlc.empty:
            logger.debug("No data for %s / %s", symbol, interval)
            return pd.DataFrame()
        ohlc = ohlc.reset_index().rename(columns={"timestamp": "Date"})
        ohlc['Date'] = pd.to_datetime(ohlc['Date'])
        ohlc['symbol'] = symbol
        return ohlc

    def merge_signal_columns(self, ohlc_df, name, obj):
        """Copy an oscillator's signal columns into the working frame."""
        columns = [c for c in self.signal_columns.get(name, []) if c in obj.df.columns]
        if not columns:
            return ohlc_df
        return pd.concat([ohlc_df.reset_index(drop=True),
                          obj.df[columns].reset_index(drop=True)], axis=1)

    def compute_signals(self, symbol, interval="5min", oszillators=('ewo', 'rsi')):
        """Compute the oscillator signals for one interval without building any figure.

        This is the headless path used by the collector (bare_mode): it used to
        render three full Plotly figures per cycle only to throw them away.
        """
        ohlc_df = self.prepare_frame(symbol, interval)
        if ohlc_df.empty:
            return ohlc_df
        for osz in oszillators:
            if osz not in self.signal_columns:
                continue
            try:
                self.init_instance(osz, df=ohlc_df)
                ohlc_df = self.merge_signal_columns(ohlc_df, osz, getattr(self, osz))
            except Exception:
                logger.debug("Could not compute %s for %s", osz, symbol, exc_info=True)
        self.update_signals(ohlc_df, interval)
        return ohlc_df

    def plot_candlestick(self, symbol, interval="5min", oszillators=['ewo','rsi'], overlays=['atc','fvg','pre','bos','candle'], limit_start=False):
        """Create a Plotly candlestick chart for a symbol."""

        ohlc_df = self.prepare_frame(symbol, interval)
        if ohlc_df.empty:
            return None

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
                ohlc_df = self.merge_signal_columns(ohlc_df, osz, obj)
        except Exception:
            logger.debug("Oscillator rendering failed for %s", symbol, exc_info=True)

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

        ohlc_df = self.update_signals(ohlc_df, interval)

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

                # Limit the frame to the zoom range
                visible_df = ohlc_df[(ohlc_df['Date'] >= start) & (ohlc_df['Date'] <= end)]

                # Min/max of the visible y values
                low = visible_df['Low'].min()
                high = visible_df['High'].max()
                padding = (high - low) * 0.05
                fig.update_yaxes(
                    range=[low - padding, high + padding],
                    row=1, col=1
                    )

        fig.update_xaxes(
            rangebreaks = [
                dict(bounds=["sat", "mon"]),
                dict(bounds=[22, 8], pattern="hour"),
                ]
        )

        return fig

    def update_signals(self, ohlc_df, interval):
        """Accumulate value / momentum / trend over the rendered intervals.

        The 1min pass resets the accumulators, the later intervals add to them —
        notifier() reads the sum. Returns the frame with NaNs filled.
        """
        if interval == "1min":
            self.momentum = 0
            self.value = 0
            self.trend = 0
            self.trend_ticker = ''
            self.market_price = ''

        if ohlc_df is None or ohlc_df.empty:
            return ohlc_df

        ohlc_df = ohlc_df.fillna(0).infer_objects(copy=False)
        self.trend_ticker = self.symbol
        self.market_price = round(ohlc_df['Close'].iloc[-1], 1)

        # The last bar is still forming — read the previous, closed one.
        if len(ohlc_df) < 2:
            return ohlc_df

        try:
            self.value += ohlc_df['ewo'].iloc[-2] - ohlc_df['ewo_ema'].iloc[-2]
            self.momentum += ohlc_df['momentum'].iloc[-2]
        except (KeyError, IndexError):
            logger.debug("No ewo/rsi columns for %s / %s", self.symbol, interval)
        try:
            if ohlc_df['ewo_diff'].iloc[-2] < 0:
                self.trend += -1
            elif ohlc_df['ewo_diff'].iloc[-2] > 0:
                self.trend += 1
        except (KeyError, IndexError):
            logger.debug("No ewo_diff column for %s / %s", self.symbol, interval)

        return ohlc_df


    def get_idx_selected(self, v_list, v_key, default=0):
        """Return the index of v_key in v_list, or default when not found."""
        try:
            default = v_list.index(v_key)
        except Exception:
            pass    
        return default

    def get_symbol_list(self):
        """Populate self.symbol_list with the unique symbol names present in self.df."""
        if self.df is None or self.df.empty or "symbol" not in self.df.columns:
            self.symbol_list = []
        else:
            self.symbol_list = sorted(self.df["symbol"].dropna().unique().tolist())
        return self.symbol_list

    def cleanup(self):
        """Archive the current database file when no file for today exists yet."""
        now = dt.datetime.now()
        file_date = now.strftime("%Y-%m-%d %H:%M:%S")
        if self.df is not None and not self.df.empty and 'timestamp' in self.df.columns:
            # The frame is sorted, so the last row is the most recent tick.
            file_date = str(self.df['timestamp'].iloc[-1])

        files = self.get_database_files(f"{now.strftime('%Y-%m-%d')} *")
        if files:
            logger.info("Tick database for today is already archived (%s)", files[0])
            return False
        return self.rename_db(file_date=file_date.replace(":", "-"))
    
    def get_database_files(self, asterik='*'):
        """Return a sorted list of archived database filenames matching the glob pattern."""
        path = self.get_path(path=self.db_path, file_name=f"{self.db_table}_{asterik}.db")
        db_files = sorted(glob.glob(path), reverse=True)
        return [os.path.basename(db) for db in db_files]

    def select_tick_interval(self, region=st):
        """Render the interval selector for the streamed tick chart.

        This is deliberately separate from the multi-selector's Interval/Period,
        which belong to the historical chart: the live view resamples ticks and
        only offers the intervals that make sense for them. The choice is kept
        in config.db, but written *after* the widget returned — an on_change
        handler would re-fire on widget garbage collection and could persist a
        half-finished selection.
        """
        intervals = list(self.tick_intervals)
        stored = str(self.sys_conf.get_value(self.tick_interval_key, intervals[0]))
        index = intervals.index(stored) if stored in intervals else 0
        try:
            selected = region.radio(t('live.tick_interval'), intervals, index=index,
                                    horizontal=True, key='_live_tick_interval',
                                    help=t('live.tick_interval_help'))
        except Exception:
            logger.debug("interval selector failed", exc_info=True)
            return intervals[index]

        if selected != stored:
            try:
                self.sys_conf.set_value(self.tick_interval_key, selected)
            except Exception:
                logger.debug("could not store the tick interval", exc_info=True)
        return selected

    def render(self, default="^GDAXI", region=st, bare_mode=False):
        """Render the live candlestick chart and price ticker in the Streamlit app.

        bare_mode is the headless path used by the collector: it only refreshes
        the trend signals (no Plotly figures, no Streamlit widgets).

        Layout order: database · quote table · symbol · live interval · chart ·
        signal line · Interval/Period/Overlay/Oszilator · history.
        """
        interval = period = None
        overlays = oszilators = []
        limit_start = True

        if not bare_mode:
            databases = self.get_database_files()
            databases.insert(0, "")
            # Dropdown to select the database
            selected_db = st.selectbox("Choose database:", databases)

            if selected_db and selected_db != "":

                # Load data from the selected database
                self.load_from_file_backwards(selected_db)
                limit_start = False

        if limit_start:
            # Only today's session — everything older lives in the archived files.
            self.load_from_db(timestamp=datetime.now().strftime("%Y-%m-%d 06:00:00"),
                              db_name=f"{self.db_table}.db")

        charts = self.tick_intervals

        if bare_mode:
            # Headless: compute the signals only, no figures.
            for chrt in charts:
                self.compute_signals(self.symbol, chrt, oszillators=['ewo', 'rsi'])
            logger.info("Index: %s - Indicator: %s / %s, trend: %s", self.symbol,
                        round(self.value, 1), round(self.momentum, 1), self.trend)
            return

        self.render_price_summary(region=region)

        idx = self.get_idx_selected(self.symbol_list, default, 2)
        self.symbol = default
        try:
            self.symbol = st.selectbox("Choose Symbol", self.symbol_list if self.symbol_list else ["No data"],index=idx)
        except Exception:
            logger.debug("symbol selectbox failed", exc_info=True)

        selected = self.select_tick_interval()

        # Containers reserve their spot in the layout while the code that fills
        # them runs later. That is what lets the Interval/Period/Overlay row sit
        # below the chart although the chart is drawn *from* its values: a
        # Streamlit widget only yields its value where it is created.
        chart_slot = st.container()
        signal_slot = st.container()

        self.multi_selector.render()
        if self.sys_conf.get_value("pine_export", False):
            self.multi_selector.render_pine_export()
        interval = self.multi_selector.get_selected_options('Interval')[:1]
        period = self.multi_selector.get_selected_options('Period')[:1]
        overlays = self.multi_selector.get_selected_options('Overlay')
        oszilators = self.multi_selector.get_selected_options('Oszilator')

        # "Selected but not plotted" — an indicator can be computed for the
        # buy/sell expressions without cluttering the chart. Reading this the
        # same way the Asset Viewer does is what keeps both charts identical.
        plot_overlays = set(self.multi_selector.get_plot_options('Overlay'))
        plot_oszilators = set(self.multi_selector.get_plot_options('Oszilator'))
        no_plot_overlays = [n for n in overlays if n not in plot_overlays]
        no_plot_oszilators = [n for n in oszilators if n not in plot_oszilators]

        (interval, period, overlays, oszilators) = self.sys_conf.get_selectors(interval, period, overlays, oszilators)

        trend_length = 21
        max_trend_length = self.calc_max_periods(interval, period)
        if trend_length > max_trend_length:
            trend_length = int(max_trend_length / 2)

        self.url = f"/?symbol="

        # Every interval is still computed — value/momentum/trend are the sum
        # across all three and the notifier's thresholds are calibrated on it —
        # but only the selected one is drawn. The order matters: the 1min pass
        # resets the accumulators.
        for chrt in charts:
            if chrt != selected:
                self.compute_signals(self.symbol, chrt, oszillators=oszilators)
                continue
            fig = self.plot_candlestick(self.symbol, chrt, oszillators=oszilators, overlays=overlays, limit_start=limit_start)
            if fig:
                chart_slot.plotly_chart(fig,
                        use_container_width = True,
                        theme="streamlit",
                        config = self.charts_config,
                        )

        signal_slot.write(f"Index: {self.symbol} - Indicator: {round(self.value,1)} / "
                          f"{round(self.momentum,1)}, trend: {self.trend}")

        if signal_slot.button("Delete cached ticker entries"):
            self.cleanup()
            st.rerun()
        try:
            show_history = st.checkbox("Show history: ",False)
            if show_history:
                # Same parameters as the Asset Viewer's chart (main_page), so the
                # history looks identical for the same ticker and settings.
                # `username` is the important one: tiny_chart builds its own
                # SystemConfig from it, and without a user every per-user setting
                # (indicator parameters, zoom factor, …) silently falls back to
                # the defaults.
                slider_row = st.empty()
                history = tc.tiny_chart(
                    self.symbol,
                    longname=f"{self.symbol} - {interval}/{period}",
                    interval=interval,
                    period=period,
                    url=f'{self.url}',
                    candle_chart='candle' in (overlays or []),
                    show_trend=False,
                    range_breaks=True,
                    trend_length=trend_length,
                    add_sub_plots=oszilators,
                    add_overlays=overlays,
                    no_plot_overlays=no_plot_overlays,
                    no_plot_oszilators=no_plot_oszilators,
                    username=self.username,
                    zoom=True,
                    pips_select=True,
                    add_current=(interval == "1d"),
                    region=slider_row,
                )
                st.plotly_chart(history.fig,
                    use_container_width = True,
                    theme="streamlit",
                    config = self.charts_config
                )
        except Exception:
            logger.debug("history chart failed", exc_info=True)


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

