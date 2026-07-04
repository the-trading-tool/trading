import pathlib
import streamlit as st
from pathlib import Path
import sys
import os
import math
import json
import sqlite3
import datetime as dt
import pandas as pd
import re
from typing import Optional
import logging

logger = logging.getLogger(__name__)
import numpy as np

ftime_str = '%Y-%m-%d %H:%M:%S'
BASE_URL = "https://trading.cloogidoo.com"

# Asset class groups in yf_tickers.db (table indices) that contain no stocks.
# They are excluded from the performance simulation and the stock download
# (/index_member). ETP (tradeable ETFs/ETCs) does NOT belong to this list.
NON_STOCK_GROUPS = ('INDEX', 'COMMODITIES', 'METALS', 'CURRENCIES', 'CRYPTO')


def open_db(path: str, readonly: bool = False, timeout: float = 5.0,
            check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection with standard PRAGMAs applied.

    Use readonly=True for pure read paths — avoids write locks and is slightly
    faster because WAL/synchronous settings are skipped.
    Pass check_same_thread=False for connections shared across threads.
    """
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout,
                               check_same_thread=check_same_thread)
    else:
        conn = sqlite3.connect(path, timeout=timeout, check_same_thread=check_same_thread)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA cache_size = -64000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")
    return conn

class Tools:

    ftime_str = ftime_str
    
    def split_interval(self, interval):
        """Parse an interval string like '5m' or '1wk' into a (value, unit) tuple."""
        s_str = r"(\d{1,3})(\D+)"
        mi = re.search(s_str,interval)
        if mi:
            return (float(mi.group(1)), mi.group(2))

        return(1,"")
    
    def get_ms_timestamp(self):
        """Return the current datetime as a string including microseconds."""
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

    def get_d_timestamp(self):
        """Return today's date as a datetime string with time set to midnight."""
        return dt.datetime.now().strftime("%Y-%m-%d 00:00:00")
                
    def get_position(self, l, v):
        """Return the zero-based index of the first occurrence of v in list l.

        Returns len(l) when v is not found.
        """
        index = 0
        for item in l:
            if not item == v:
                index += 1
            else:
                return index

        return len(l)

    def check_value(self, v):
        """Safely return v; returns an empty string if access raises any error."""
        value = ''
        try:
            value = v
        except Exception:
            pass
        return value

    def to_int(self, s, d=0):
        """Convert s to int, returning default d on ValueError or TypeError."""
        i = d
        try:
            i = int(s)
        except (ValueError, TypeError):
            pass
        return i

    def get_path(self, path='database', file_name='', ignore_env=False):
        """Resolve the full filesystem path for a database file.

        Respects the TradingDB environment variable to redirect all DB access to
        a custom directory (e.g. production databases). Falls back to
        <project_root>/database/ when the variable is not set.
        """
        # Path for the SQLite database file
        try:
            if file_name  == '':
                file_name = self.file_name
        except AttributeError:
            pass  # subclasses that don't define self.file_name are fine

        file_name = os.path.basename(file_name)

        tdb = ''
        try:
            if not ignore_env:
                tdb = os.environ['TradingDB']
        except KeyError:
            pass  # env var not set — use default path

        if not tdb == '':
                    
            directory = Path(tdb)
            directory.mkdir(exist_ok=True)

            return os.path.join(directory, file_name) 
        
        else:
            
            # Always relative to tools.py itself (tradinglib/ → parent = project root)
            # sys.argv[0] would point to the Streamlit launcher under Streamlit, not the project
            caller_directory = Path(__file__).resolve().parent.parent

            # Path to the 'database' directory inside the calling script's directory
            sub_directory = caller_directory / path

            # Create the 'database' directory if it does not yet exist
            sub_directory.mkdir(exist_ok=True)
        
            return os.path.join(sub_directory,file_name)

    # check if an element is not a number
    def isnan(self, value):
        """
        pandas Data frames return a nan for a cell not containing a value
        this routine helps to check if a returned value is nan
        returns nan
        or False if not
        """
        try:
            return math.isnan(float(value))
        except (TypeError, ValueError):
            return False

    def file_exists(self, filename):
        """
        test if a file exists in a folder
        returns True or False
        """
        file = pathlib.Path(filename)
        if file.exists():
            return True
        else:
            return False

        
    def __init__(self) -> None:

        pass

    def normalize_date(self, df: pd.DataFrame, col: str = 'Date') -> pd.DataFrame:
        """
        Ensure the given DataFrame has a datetime-typed `col` column.
        Returns the (possibly modified) DataFrame. Errors are coerced to NaT.
        """
        try:
            if isinstance(df, pd.DataFrame) and col in df.columns:
                logger = logging.getLogger(__name__)
                if logger.isEnabledFor(logging.DEBUG):
                    try:
                        before = df[col].dtype
                        logger.debug("normalize_date: before dtype for %s = %s", col, before)
                    except Exception:
                        pass
                df[col] = pd.to_datetime(df[col], errors='coerce')
                if logger.isEnabledFor(logging.DEBUG):
                    try:
                        after = df[col].dtype
                        logger.debug("normalize_date: after dtype for %s = %s", col, after)
                    except Exception:
                        pass
        except Exception:
            pass
        return df

    def populate_indicators_for_df(self, combined_df: pd.DataFrame, expressions: list, default_tf='D', params_map: dict=None, max_workers=6):
        """Compute and attach indicator columns that are referenced in expressions.

        - expressions: list of expression strings to scan for tokens
        - params_map: optional mapping token -> params dict (e.g. {'ema21':{'window':21}})
        This helper performs a two-phase compute: base indicators (no base_col) first,
        then dependent ones (e.g., ewoEma21 which needs 'ewo' first).
        """
        from tradinglib import tf_indicators as tf
        import re
        from concurrent.futures import ThreadPoolExecutor, as_completed

        params_map = params_map or {}
        TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_@]*")

        def extract_tokens(expr):
            toks = set()
            for e in expr if isinstance(expr, list) else [expr]:
                toks.update(TOKEN_RE.findall(e or ''))
            return toks

        tokens = set()
        for e in expressions:
            tokens |= extract_tokens(e)

        # filter out operators/keywords
        tokens = {t for t in tokens if t.lower() not in ('and','or','not','true','false')}
        # Build a case-folded set of existing columns so that e.g. 'Close'
        # is recognised as already satisfied by 'close' (simulation DB stores
        # OHLCV in lowercase while live queries may use yfinance capitalisation).
        existing_lower = {c.lower() for c in combined_df.columns}
        tokens = [t for t in tokens
                  if t not in combined_df.columns and t.lower() not in existing_lower]
        if not tokens:
            return combined_df

        # Normalize combined_df Date column to datetime to avoid dtype mismatch when merging
        combined_df = self.normalize_date(combined_df, 'Date')

        # parse tokens into (ind_name, params, tf, out_token)
        parsed = {t: tf.parse_indicator_token(t, default_tf) for t in tokens}

        # Phase 1: base indicators (no base_col in params)
        phase1 = {t:v for t,v in parsed.items() if 'base_col' not in v[1]}
        # Phase 2: dependent (have base_col)
        phase2 = {t:v for t,v in parsed.items() if 'base_col' in v[1]}

        tickers = combined_df['ticker'].unique().tolist()

        def compute_for_ticker(ticker, phase_items):
            df_t = combined_df[combined_df['ticker']==ticker].copy()
            df_t = self.normalize_date(df_t, 'Date')
            # prepare minimal output
            out_df = df_t[['Date','ticker']].copy()
            for token, (iname, params, tfm, out_token) in phase_items.items():
                merged_params = dict(params_map.get(token, {}))
                merged_params.update(params or {})
                try:
                    ind_df = tf.get_indicator(df_t, token, timeframe=tfm, params=merged_params)
                    if ind_df is None or ind_df.empty:
                        out_df[token] = np.nan
                        continue
                    # align on Date
                    if 'Date' in ind_df.columns:
                        merged = pd.merge(out_df[['Date']], ind_df, on='Date', how='left')
                        # pick first numeric/text column that is not Date
                        cols = [c for c in merged.columns if c!='Date']
                        if not cols:
                            out_df[token] = np.nan
                        else:
                            out_df[token] = merged[cols[0]].values
                    else:
                        series = ind_df.reset_index(drop=True).iloc[:,0]
                        out_df[token] = series.values
                except Exception:
                    logger.exception('Error computing %s for %s', token, ticker)
                    out_df[token] = np.nan
            return out_df

        results = []
        # run phase1
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(compute_for_ticker, t, phase1): t for t in tickers}
            for f in as_completed(futures):
                try:
                    results.append(f.result())
                except Exception as e:
                    logger.exception(f'Error in phase1 for ticker: {e}')

        if results:
            merged_all = pd.concat(results, axis=0, ignore_index=True)
            # ensure Date and ticker exist
            if 'Date' in merged_all.columns and 'ticker' in merged_all.columns:
                # align merged_all Date dtype with combined_df Date dtype
                merged_all = self.normalize_date(merged_all, 'Date')
                # ensure combined_df Date dtype matches merged_all Date dtype to avoid merge errors
                combined_df = self.normalize_date(combined_df, 'Date')
                combined_df = combined_df.merge(merged_all.drop(columns=['longName'], errors='ignore'), on=['Date','ticker'], how='left')
            else:
                # best-effort: join by index position
                merged_all = merged_all.reset_index(drop=True)
                combined_df = pd.concat([combined_df.reset_index(drop=True), merged_all], axis=1)

        # run phase2 (dependent tokens that rely on base cols)
        if phase2:
            results = []
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(compute_for_ticker, t, phase2): t for t in tickers}
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception:
                        logger.exception('Error in phase2 for ticker')
            if results:
                merged_all = pd.concat(results, axis=0, ignore_index=True)
                if 'Date' in merged_all.columns and 'ticker' in merged_all.columns:
                    merged_all = self.normalize_date(merged_all, 'Date')
                    combined_df = self.normalize_date(combined_df, 'Date')
                    combined_df = combined_df.merge(merged_all.drop(columns=['longName'], errors='ignore'), on=['Date','ticker'], how='left')
                else:
                    merged_all = merged_all.reset_index(drop=True)
                    combined_df = pd.concat([combined_df.reset_index(drop=True), merged_all], axis=1)

        return combined_df
    

class Db_tools(Tools):
    
    conn = None
    cursor = None
    
    
    """
    A utility class to encapsulate SQLite database operations.
    Supports both in-memory and file-based databases with automatic
    backup to/from disk and lifecycle management.
    """

    def __init__(self, db_path: str = 'database', database_name: str = 'asset_performance.db', db_type: str = ":file:"):
        """
        Initialize the database tool.

        :param db_path: Directory where the database file is stored.
        :param database_name: Name of the database file.
        :param db_type: Either ":memory:" for in-memory DB or ":file:" for file-based DB.
        """
        self.db_path = db_path
        self.database_name = database_name
        self.db_type = db_type
        self.conn: Optional[sqlite3.Connection] = None
        self.cursor: Optional[sqlite3.Cursor] = None
        self.memory_conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _get_file_path(self) -> str:
        """Constructs full file path for the database."""
        path =  self.get_path(path = self.db_path, file_name=self.database_name)
        return path
    
    def _connect(self):
        """Connects to the appropriate database depending on db_type."""
        if self.db_type == ":memory:":
            self.memory_conn = sqlite3.connect(":memory:")
            self.conn = self.memory_conn
            self.cursor = self.conn.cursor()

            # Load data from file if exists
            file_path = self._get_file_path()
            if os.path.exists(file_path):
                disk_conn = open_db(file_path, readonly=True)
                with self.memory_conn:
                    disk_conn.backup(self.memory_conn)
                disk_conn.close()

        elif self.db_type == ":file:":
            self.conn = open_db(self._get_file_path())
            self.cursor = self.conn.cursor()

        else:
            raise ValueError("db_type must be either ':memory:' or ':file:'")

    def commit(self):
        """Commits the current transaction."""
        if self.conn:
            self.conn.commit()

    def close(self):
        """Closes the database connection and writes to disk if in-memory DB is used."""
        if self.memory_conn:
            file_path = self._get_file_path()
            disk_conn = open_db(file_path)
            with disk_conn:
                self.memory_conn.backup(disk_conn)
            disk_conn.close()
            self.memory_conn.close()
            self.memory_conn = None

        if self.conn:
            self.conn.close()
            self.conn = None
            self.cursor = None

    def execute(self, sql: str, params: tuple = ()):
        """Executes a single SQL statement."""
        if not self.cursor:
            raise RuntimeError("Database not connected.")
        self.cursor.execute(sql, params)

    def executemany(self, sql: str, params_list: list):
        """Executes a SQL statement for many parameters."""
        if not self.cursor:
            raise RuntimeError("Database not connected.")
        self.cursor.executemany(sql, params_list)

    def fetchall(self):
        """Fetches all rows of a query result."""
        return self.cursor.fetchall() if self.cursor else []

    def fetchone(self):
        """Fetches the next row of a query result."""
        return self.cursor.fetchone() if self.cursor else None

    def ensure_index(self, table: str, columns: list, index_name: str = '', unique: bool = False):
        """Create an index if it doesn't exist. Safe to call repeatedly (IF NOT EXISTS)."""
        if not self.cursor:
            return
        if not index_name:
            index_name = f"idx_{table}_{'_'.join(columns)}"
        unique_kw = "UNIQUE " if unique else ""
        cols_sql = ', '.join(columns)
        try:
            self.cursor.execute(
                f"CREATE {unique_kw}INDEX IF NOT EXISTS {index_name} ON {table} ({cols_sql})"
            )
        except Exception:
            pass

    def create_table(self, keys=[], row_dict={}, database_name='asset_performance', primary_key=True):
        """Create the table with columns inferred from keys/row_dict value types.

        Column types are inferred from the values in row_dict (str→TEXT,
        int/float→REAL, list→TEXT as JSON). The first column becomes the
        PRIMARY KEY when primary_key=True.
        """

    
        columns = [] 
        primary = False

        if keys == []:
            keys = row_dict.keys()
            
        for key in keys:
            # Check whether the key starts with a digit
            if key[0].isdigit():
                column_name = f"_{key}"
            else:
                column_name = key

            # Determine the type of the first non-None value
            value = row_dict.get(key, None)
            if value is None:
                column_type = "REAL"
            elif isinstance(value, str):
                column_type = "TEXT"
            elif isinstance(value, (int, float)):
                column_type = "REAL"
            elif isinstance(value, list):
                column_type = "TEXT"  # Lists are stored as JSON strings
            else:
                column_type = "TEXT"  # Fallback to TEXT when type is unclear

            if not primary:
                if primary_key:
                    column_type += ' PRIMARY KEY'
                primary = True

            columns.append(f"{column_name} {column_type}")
            
        columns_sql = ', '.join(columns)
    
        self.cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {database_name} (
            {columns_sql}
        )
        """)

    def ensure_table_and_columns(self, keys=[], row_dict={}, database_name='asset_performance', primary_key=True):
        """Create the table if it doesn't exist, or add any missing columns.

        Uses case-insensitive column comparison to avoid duplicates. Safe to
        call on every insert — no-ops when the schema is already up to date.
        """

        if keys == []:
            keys = row_dict.keys()
            
        # Check whether the table exists
        self.cursor.execute(f"PRAGMA table_info('{database_name}')")
        fetched = [row[1] for row in self.cursor.fetchall()]
        existing_columns = {col for col in fetched}  # Set of existing column names (original case)
        existing_columns_lc = {col.lower().strip() for col in fetched}
        if len(existing_columns) == 0:
            # Make sure the table exists
            self.create_table(keys, row_dict, database_name, primary_key=primary_key)
        else:
            for key in keys:
                # Check whether the key starts with a digit
                if key[0].isdigit():
                    column_name = f"_{key}"
                else:
                    column_name = key
                # If the lowercase of the column is already present, skip adding (case-insensitive match)
                if column_name.lower().strip() not in existing_columns_lc:
                    value = row_dict.get(key, None)
                    if value is None:
                        column_type = "REAL"
                    elif isinstance(value, str):
                        column_type = "TEXT"
                    elif isinstance(value, (int, float)):
                        column_type = "REAL"
                    elif isinstance(value, list):
                        column_type = "TEXT"  # Lists are stored as JSON strings
                    else:
                        column_type = "TEXT"  # Fallback to TEXT when type is unclear

                    self.cursor.execute(f"ALTER TABLE {database_name} ADD COLUMN {column_name} {column_type}")


    def insert_data(self, keys=[], row_dict={}, database_name='asset_performance', replace=True):
        """Insert a single row from row_dict into database_name.

        Lists are serialized as JSON strings. replace=True uses INSERT OR REPLACE
        so the row is upserted on the PRIMARY KEY.
        """

        values = []
        column_names = []

        if keys == []:
            keys = row_dict.keys()
            
        for key in keys:
            # Check whether the key starts with a digit
            if key[0].isdigit():
                column_name = f"_{key}"
            else:
                column_name = key

            column_names.append(column_name)

            try:
                value = row_dict.get(key, None)  # Retrieve the value for the key
                if isinstance(value, list):
                    value = json.dumps(value)  # Convert lists to JSON strings
            except Exception as e:
                logger.warning("Error retrieving value for %s: %s", key, e)
                value = None  # Set value to None if an error occurs

            values.append(value)

        placeholders = ', '.join(['?'] * (len(values)))  # +1 for the timestamp

        or_rep = ''
        if replace:
            or_rep = ' OR REPLACE '

        self.cursor.execute(f"""
        INSERT {or_rep} INTO {database_name} ({', '.join(column_names)})
        VALUES ({placeholders})
        """, values)

    def read_data(self, query):
        """Execute a SELECT query and return the result as a DataFrame."""
        result = pd.DataFrame()
#        try:
        result = pd.read_sql_query(query, self.conn)
#        except Exception:
        return result

    def get_indices_names(self, delete_columns=[], table_name=''):
        """Return a sorted list of index names from table_name, excluding delete_columns.

        Deduplicates Yahoo-prefixed tickers: keeps '^GDAXI' when both 'GDAXI'
        and '^GDAXI' are present.
        """
        if not table_name == '':
            query = f"""
                SELECT name FROM {table_name};
                """
            data = self.read_data(query)
            filtered = data[~data["name"].isin(delete_columns)]
            names = list(filtered['name'].sort_values())
            # Deduplicate: if both 'GDAXI' and '^GDAXI' exist keep only '^GDAXI'
            with_hat = {n for n in names if n.startswith('^')}
            names = [n for n in names if n.startswith('^') or f'^{n}' not in with_hat]
            return sorted(names)
        return []


class St_tools(Tools):
    
    def __init__(self, screen_region=st):
        """Initialise with an optional Streamlit region (e.g. st.sidebar or a column)."""
        self.screen_region = screen_region
        pass

    def dataframe_with_selections(self, df=pd.DataFrame(), preselect=False, preselect_count=0):
        """Render a data_editor with a leading Select checkbox column.

        preselect=True selects all rows by default; preselect_count limits
        selection to the first N rows (0 = select rows with non-zero buyVolume).
        Returns only the selected rows without the Select column.
        """


        df_with_selections = df.copy()
        df_with_selections.insert(0, "Select", False)

        if preselect == False:
            #deselct all
            df_with_selections['Select'] = False
        else:
            #select based on count
            df_with_selections['Select'] = True
            if preselect_count == 0:
                # any with buyVolume
                try:
                    for i in range(0, len(df)):
                        if df.loc[i, 'buyVolume'] == 0:
                            df_with_selections.loc[i, 'Select'] = False
                except Exception:
                    pass
            else:
                #select first n
                if len(df) >= preselect_count:
                    for i in range(preselect_count, len(df)):
                        df_with_selections.loc[i, 'Select'] = False

        edited_df = self.screen_region.data_editor(
                df_with_selections,
                hide_index=True,
                column_config={"Select": st.column_config.CheckboxColumn(required=False)},
                use_container_width = True,
            )

        # Filter the dataframe using the temporary column, then drop the column
        selected_rows = edited_df[edited_df.Select]
        return selected_rows.drop('Select', axis=1)

    pass

class ExpressionEvaluator():
    valid_operators = ['>', '<', '>=', '*', '<=', '~', '/', '==', '!=', '&', '|', '(', ')', '+', '-']
    valid_methods = ['rolling', 'mean', 'shift']

    def __init__(self, dataframe: pd.DataFrame, dataframe_name='df'):
        """Bind the evaluator to a DataFrame so column names can be resolved in expressions."""
        self.df = dataframe
        self.dataframe_name = dataframe_name
        self.column_names = dataframe.columns.tolist()

    def validate_and_transform(self, expression: str) -> str:
        """
        Transforms an expression string into a valid Python expression using the provided dataframe.
        Supports indexed access (e.g. col[-1]), arithmetic, and pandas methods like shift(), rolling().
        """
        # Handle advanced patterns like ewo.rolling(5).mean()
        # This regex captures chains like: column.method(args).method(args)...
        method_pattern = re.compile(
            r'\b([a-zA-Z_][\w]*)((?:\.(?:' + '|'.join(self.valid_methods) + r')\([^\)]*\))+)'
        )

        # First, replace method chains
        def replace_method_chain(match):
            col = match.group(1)
            chain = match.group(2)
            if col not in self.column_names:
                raise ValueError(f"Unknown column: {col}")
            return f"{self.dataframe_name}['{col}']{chain}"

        expression = method_pattern.sub(replace_method_chain, expression)

        # Replace indexed access: ewo[-1] -> df['ewo'].iloc[-1]
        index_pattern = re.compile(r'\b([a-zA-Z_][\w]*)\[(\-?\d+)\]')
        expression = index_pattern.sub(
            lambda m: f"{self.dataframe_name}['{m.group(1)}'].iloc[{m.group(2)}]"
            if m.group(1) in self.column_names else m.group(0), expression
        )

        # Split into tokens to transform column names and constants
        tokens = re.split(r'(\>\=|\<\=|\=\=|\!\=|>|<|\/|\*|\+|\-|\(|\)|&|\||~)', expression)
        transformed = []

        for token in tokens:
            token = token.strip()
            if not token:
                continue

            if token in self.valid_operators:
                transformed.append(token)
            elif re.match(r'^[a-zA-Z_][\w]*$', token) and token in self.column_names:
                # Remaining column names
                transformed.append(f"{self.dataframe_name}['{token}']")
            elif re.match(r'^-?\d+(\.\d+)?$', token):
                transformed.append(token)
            else:
                transformed.append(token)

        return ' '.join(transformed)

    def evaluate_expression(self, transformed_expression: str) -> pd.Series:
        """Evaluate a pre-transformed expression string against the bound DataFrame.

        Raises ValueError when the result is not a Series or bool.
        """
        try:
            result = eval(transformed_expression)
            if not isinstance(result, (pd.Series, bool)):
                raise ValueError("Die Auswertung muss ein Series- oder bool-Wert ergeben.")
            return result
        except Exception as e:
            raise ValueError(f"Fehler bei Auswertung: {e}")


def compute_signal_mask(df: pd.DataFrame, condition: str, group_col: str = 'ticker') -> pd.Series:
    """Wertet eine Buy/Sell-Bedingung zu einer Bool-Serie aus (auf df.index ausgerichtet).

    Geteilte Evaluierungs-Schicht fuer Strategy Finder (BuySellSignalGenerator) und
    Multi Strategies. Die Auswertung erfolgt **pro Ticker-Gruppe** (group_col), damit
    Zeitreihen-Operatoren (rolling/shift, col[-1]) innerhalb eines Instruments bleiben
    und nicht ueber Ticker-Grenzen hinweg "bluten". Pro Gruppe wird zuerst der
    ExpressionEvaluator versucht, dann pandas df.eval als Fallback.

    Optimierung: Nur Bedingungen mit Zeitreihen-Operatoren (rolling/shift/mean-Kette
    oder col[-1]) brauchen die Pro-Gruppen-Auswertung. Rein row-wise Bedingungen
    (z.B. "ewo_angle < -20") liefern ueber den ganzen df in EINEM Pass dasselbe
    Ergebnis — das spart bei vielen Tickern (z.B. ^RUT, ~1900) Tausende Einzel-Evals.

    Wirft, wenn die Auswertung mit keiner Methode gelingt — die Aufrufer entscheiden
    dann ueber ihren jeweiligen Endfallback (z.B. all-False).
    """
    if not condition:
        return pd.Series(False, index=df.index)

    needs_group = bool(re.search(r'\.(rolling|shift|mean)\s*\(|\w\s*\[\s*-?\d+\s*\]', condition))
    if not (needs_group and group_col and group_col in df.columns):
        # Single-Pass ueber den ganzen df (row-wise -> identisch zu pro-Gruppe).
        try:
            ev = ExpressionEvaluator(df, dataframe_name='g')
            res = eval(ev.validate_and_transform(condition), {}, {'g': df})
        except Exception:
            res = df.eval(condition)
        if not isinstance(res, pd.Series):
            return pd.Series(bool(res), index=df.index)
        return res.astype(bool).reindex(df.index, fill_value=False)

    # Pro-Ticker-Gruppe; Transform einmal ueber den ganzen df (gleiche Spalten).
    mask = pd.Series(False, index=df.index)
    try:
        transformed = ExpressionEvaluator(df, dataframe_name='g').validate_and_transform(condition)
    except Exception:
        transformed = None
    for _key, g in df.groupby(group_col):
        try:
            res = eval(transformed, {}, {'g': g}) if transformed is not None else g.eval(condition)
        except Exception:
            res = g.eval(condition)
        if not isinstance(res, pd.Series):
            res = pd.Series(bool(res), index=g.index)
        mask.loc[g.index] = res.astype(bool).values
    return mask


def lazy_excel_download(data, button_label, file_name, region=st, state_key=None, spinner_text='…'):
    """Streamlit-Download fuer (potenziell teure) Excel-Exports — lazy.

    Die Excel-Konvertierung (DataUtils.get_bin_excel_data) laeuft erst auf
    Knopfdruck statt bei JEDEM Rerun. Wichtig fuer grosse DataFrames: ein voller
    Strategy-Finder-Datensatz (z.B. ^RUT, ~228k Zeilen x ~96 Spalten) braucht
    mehrere Minuten zum Serialisieren — das darf nicht bei jeder Interaktion und
    auch nicht fuer einen ggf. nie geklickten Download anfallen.

    Ablauf: Button -> baut Bytes einmalig in st.session_state -> Download-Button.
    state_key sollte den Datensatz eindeutig kennzeichnen (z.B. Index+Dateiname),
    sonst werden ueber Reruns/Indizes hinweg veraltete Bytes wiederverwendet.
    """
    from tradinglib.utils import DataUtils  # lazy: vermeidet Import-Zyklus tools<->utils
    key = state_key or f'_xlsx_{file_name}'
    if region.button(button_label, key=f'prep_{key}'):
        with st.spinner(spinner_text):
            st.session_state[key] = DataUtils.get_bin_excel_data(data)
    if st.session_state.get(key) is not None:
        region.download_button(label=f'⬇ {file_name}',
                               data=st.session_state[key],
                               file_name=file_name,
                               mime='application/octet-stream',
                               key=f'dl_{key}')


class BuySellSignalGenerator:
    # OHLCV columns that may appear in either capitalised (live df) or
    # lowercase (simulation db) form.  We add the missing alias so that
    # queries like "Close > 100" and "close > 100" both work regardless
    # of which form the underlying dataframe uses.
    _OHLCV_PAIRS = [('Close', 'close'), ('Open', 'open'),
                    ('High', 'high'),   ('Low', 'low'), ('Volume', 'volume')]

    def __init__(self, df: pd.DataFrame, buy_condition: str, sell_condition: str, buy_delay_days: int = 0):
        """Set up the signal generator with buy/sell expressions and an optional entry delay.

        OHLCV columns are aliased bidirectionally (Close↔close etc.) so
        expressions work regardless of the capitalisation convention in the
        underlying DataFrame. Alias columns are removed again in apply_signals().
        """
        self.df = df.copy()
        # Add bidirectional OHLCV aliases (only if the alias is absent).
        # Track which ones we added so apply_signals() can drop them again
        # before returning — they must not leak into the display DataFrame.
        self._added_aliases: list[str] = []
        for upper, lower in self._OHLCV_PAIRS:
            if lower in self.df.columns and upper not in self.df.columns:
                self.df[upper] = self.df[lower]
                self._added_aliases.append(upper)
            elif upper in self.df.columns and lower not in self.df.columns:
                self.df[lower] = self.df[upper]
                self._added_aliases.append(lower)
        self.buy_condition = buy_condition
        self.sell_condition = sell_condition
        self.buy_delay_days = buy_delay_days
        self.df['buySell'] = 0

    def apply_signals(self):
        """Apply buy and sell logic to self.df and return the annotated DataFrame.

        Sets buySell column: 1=buy, -1=sell, 0=hold. Handles optional
        buy_delay_days and stop-loss/take-profit from the tp/sl row columns.
        """
        try:
            self.df = self.df.sort_values(by=["ticker", "Date"])
        except Exception:
            pass
        try:
            self.df = self.df.reset_index(drop=True)
        except Exception:
            pass
        delayed_signals = []

        # Step 1: Evaluate buy condition group-wise (shared layer).
        try:
            buy_mask_global = compute_signal_mask(self.df, self.buy_condition)
        except Exception as e:
            logger.error("[BUY-ERROR] %s", e)
            # UI feedback only when a Streamlit context exists (headless-safe).
            try:
                st.error(f"[BUY-ERROR] {e}")
            except Exception:
                pass
            buy_mask_global = pd.Series(False, index=self.df.index)

        # Buy signals + delay: shift each match forward by buy_delay_days within
        # the ticker group, then record the global index.
        for _tk, _g in self.df.groupby('ticker'):
            gidx = _g.index.to_numpy()
            hits = np.nonzero(buy_mask_global.loc[gidx].to_numpy())[0]
            for pos in hits:
                dpos = pos + self.buy_delay_days
                if dpos < len(gidx):
                    delayed_signals.append(gidx[dpos])

        # Step 2: Set buy markers
        self.df.loc[delayed_signals, 'buySell'] = 1

        # Step 3: Sell logic
        #
        # Fast path (vectorised): the sell condition is evaluated ONCE per ticker
        # group (analogous to the buy side above) instead of building a fresh
        # ExpressionEvaluator + single-row DataFrame for every row. The result is
        # a bool Series aligned to self.df.index; the stateful open/close loop
        # below only reads from it — semantics (open position, tie bars, buy_delay)
        # remain unchanged.
        #
        # Fallback (per row): as soon as the sell condition references entry-relative
        # tp/sl, the truth value is position-dependent and cannot be pre-computed
        # -> the existing per-row logic applies exactly.
        uses_entry_state = bool(re.search(r'(?<![\w])(tp|sl)(?![\w])', self.sell_condition or ''))
        sell_mask_global = None
        if not uses_entry_state:
            try:
                sell_mask_global = compute_signal_mask(self.df, self.sell_condition)
            except Exception:
                # On any problem fall back completely to the per-row path.
                sell_mask_global = None

        # Stateful open/close pass over numpy arrays instead of df.iterrows()/df.at —
        # for ~228k rows (^RUT) the pure Python loop over arrays is many times faster
        # than per-row pandas access. Semantics are identical.
        tickers = self.df['ticker'].to_numpy()
        buysell = self.df['buySell'].to_numpy().copy()
        n = len(buysell)
        tp_col = self.df['take_profit'].to_numpy() if 'take_profit' in self.df.columns else np.full(n, np.nan)
        sl_col = self.df['stop_loss'].to_numpy() if 'stop_loss' in self.df.columns else np.full(n, np.nan)
        sell_arr = sell_mask_global.to_numpy() if sell_mask_global is not None else None

        open_positions = {}
        for i in range(n):
            ticker = tickers[i]

            # Sell when position is open
            if ticker in open_positions:
                if sell_arr is not None:
                    # Fast path: just read the pre-computed bool.
                    cond = bool(sell_arr[i])
                else:
                    # Fallback: per-row evaluation (e.g. tp/sl referenced).
                    tp, sl = open_positions[ticker]
                    row = self.df.iloc[i]
                    context = {**row.to_dict(), 'tp': tp, 'sl': sl}
                    try:
                        evaluator = ExpressionEvaluator(pd.DataFrame([row]), dataframe_name='r')
                        sell_res = eval(evaluator.validate_and_transform(self.sell_condition), {}, {'r': pd.DataFrame([row])})
                        cond = bool(sell_res) if not isinstance(sell_res, pd.Series) else bool(sell_res.iloc[0])
                    except Exception:
                        try:
                            cond = bool(eval(self.sell_condition, {}, context))
                        except Exception as e:
                            logger.error("[SELL-ERROR] Row %s, Ticker %s: %s", i, ticker, e)
                            try:
                                st.error(f"[SELL-ERROR] Row {i}, Ticker {ticker}: {e}")
                            except Exception:
                                pass
                            cond = False
                if cond:
                    buysell[i] = -1
                    del open_positions[ticker]
                    continue

            # Kauf, wenn markiert
            if buysell[i] == 1 and ticker not in open_positions:
                open_positions[ticker] = (tp_col[i], sl_col[i])

        self.df['buySell'] = buysell

        # Remove alias columns added in __init__ so they don't appear in
        # the display DataFrame or get persisted anywhere.
        if self._added_aliases:
            self.df = self.df.drop(columns=self._added_aliases, errors='ignore')
        return self.df


if __name__ == "__main__":

    pass

