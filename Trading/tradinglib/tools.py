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

class Tools:

    ftime_str = ftime_str
    
    def split_interval(self, interval):
        s_str = r"(\d{1,3})(\D+)"
        mi = re.search(s_str,interval)
        if mi:
            return (float(mi.group(1)), mi.group(2))

        return(1,"")
    
    def get_ms_timestamp(self):
        
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

    def get_d_timestamp(self):
        
        return dt.datetime.now().strftime("%Y-%m-%d 00:00:00")
                
    def get_position(self,l, v):
        index = 0
        for item in l:
            if not item == v:
                index += 1
            else:
                return index

        return len(l)

    def check_value(self, v):
        value = ''
        try:
            value = v
        except Exception:
            pass
        return value

    def to_int(self, s, d = 0):
        i = d
        try:
            i = int(s)
        except (ValueError, TypeError):
            pass
        return i

    def get_path(self, path = 'database', file_name ='', ignore_env=False):

        # Pfad für die SQLite-Datenbank-Datei
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
            
            # Der Pfad des aufrufenden Skripts
            caller_directory = Path(sys.argv[0]).resolve().parent

            # Der Pfad zum 'database' Verzeichnis im Verzeichnis des aufrufenden Skripts
            sub_directory = caller_directory / path

            # Falls das 'database' Verzeichnis noch nicht existiert, erstellen
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
#        print(f"using {path}")
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
                disk_conn = sqlite3.connect(file_path)
                disk_conn.execute("PRAGMA busy_timeout = 5000")
                with self.memory_conn:
                    #print("Saving in memory database to disk.")
                    disk_conn.backup(self.memory_conn)
                disk_conn.close()

        elif self.db_type == ":file:":
            self.conn = sqlite3.connect(self._get_file_path())
            self.conn.execute("PRAGMA busy_timeout = 5000")
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA cache_size = -64000")
            self.conn.execute("PRAGMA temp_store = MEMORY")
            self.conn.execute("PRAGMA mmap_size = 268435456")
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
            disk_conn = sqlite3.connect(file_path)
            disk_conn.execute("PRAGMA busy_timeout = 5000")
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

    # Funktion zur Erstellung der Tabelle, falls sie nicht existiert
    def create_table(self, keys=[], row_dict = {}, database_name='asset_performance', primary_key=True):

    
        columns = [] 
        primary = False

        if keys == []:
            keys = row_dict.keys()
            
        for key in keys:
            # Überprüfen, ob der Schlüssel mit einer Zahl beginnt
            if key[0].isdigit():
                column_name = f"_{key}"
            else:
                column_name = key
        
            # Typ des ersten nicht-None Wertes bestimmen
            value = row_dict.get(key, None)
            if value is None:
                column_type = "REAL"
            elif isinstance(value, str):
                column_type = "TEXT"
            elif isinstance(value, (int, float)):
                column_type = "REAL"
            elif isinstance(value, list):
                column_type = "TEXT"  # Listen werden als JSON-Strings gespeichert
            else:
                column_type = "TEXT"  # Fallback zu TEXT, wenn der Typ nicht klar ist

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

    # Funktion zur Erstellung oder Ergänzung der Tabelle, falls sie nicht existiert oder neue Spalten hinzugefügt werden müssen
    def ensure_table_and_columns(self, keys=[], row_dict={}, database_name = 'asset_performance', primary_key=True):

        if keys == []:
            keys = row_dict.keys()
            
        # Prüfe, ob die datenbank existiert
        self.cursor.execute(f"PRAGMA table_info('{database_name}')")
        fetched = [row[1] for row in self.cursor.fetchall()]
        existing_columns = {col for col in fetched}  # Set von existierenden Spaltennamen (original case)
        existing_columns_lc = {col.lower().strip() for col in fetched}
        if len(existing_columns) == 0:
            # Sicherstellen, dass die Tabelle existiert
            self.create_table(keys, row_dict, database_name, primary_key=primary_key)
        else:
            for key in keys:
                # Überprüfen, ob der Schlüssel mit einer Zahl beginnt
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
                        column_type = "TEXT"  # Listen werden als JSON-Strings gespeichert
                    else:
                        column_type = "TEXT"  # Fallback zu TEXT, wenn der Typ nicht klar ist

                    self.cursor.execute(f"ALTER TABLE {database_name} ADD COLUMN {column_name} {column_type}")


    # Funktion, um die Tabelle mit den Daten zu befüllen
    def insert_data(self, keys = [], row_dict = {}, database_name = 'asset_performance', replace=True):

        values = []
        column_names = []

        if keys == []:
            keys = row_dict.keys()
            
        for key in keys:
            # Überprüfen, ob der Schlüssel mit einer Zahl beginnt
            if key[0].isdigit():
                column_name = f"_{key}"
            else:
                column_name = key
        
            column_names.append(column_name)
        
            try:
                value = row_dict.get(key, None)  # Abrufen des Werts für den Schlüssel
                if isinstance(value, list):
                    value = json.dumps(value)  # Listen in JSON-Strings umwandeln
            except Exception as e:
                print(f"Fehler beim Abrufen des Werts für {key}: {e}")
                value = None  # Setze den Wert auf None, falls ein Fehler auftritt
        
            values.append(value)
    
#       print(f'Ticker: {ticker}')
        placeholders = ', '.join(['?'] * (len(values)))  # +1 für den timestamp

        or_rep = ''
        if replace:
            or_rep = ' OR REPLACE '

        self.cursor.execute(f"""
        INSERT {or_rep} INTO {database_name} ({', '.join(column_names)})
        VALUES ({placeholders})
        """, values)

    def read_data(self, query):
        
        result = pd.DataFrame()
#        try:
        result = pd.read_sql_query(query, self.conn)
#        except Exception:
#            pass
        return result

    def get_indices_names(self, delete_columns=[], table_name=''):
        if not table_name == '':
            query = f"""
                SELECT name FROM {table_name};
                """
            data = self.read_data(query)
            filtered = data[~data["name"].isin(delete_columns)]
        
            return list(filtered['name'].sort_values())
        return []


class St_tools(Tools):
    
    def __init__(self, screen_region = st):
        self.screen_region = screen_region
        pass

    def dataframe_with_selections(self, df = pd.DataFrame(), preselect=False, preselect_count = 0): #, sort_by = 'ticker'):


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

class ExpressionEvaluatorNew():
    valid_operators = ['>', '<', '>=', '*', '<=', '~', '/', '==', '!=', '&', '|', '(', ')', '+', '-']
    valid_methods = ['rolling', 'mean', 'shift']

    def __init__(self, dataframe: pd.DataFrame, dataframe_name='df'):
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
                raise ValueError(f"Unbekannte Spalte: {col}")
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
        try:
            result = eval(transformed_expression)
            if not isinstance(result, (pd.Series, bool)):
                raise ValueError("Die Auswertung muss ein Series- oder bool-Wert ergeben.")
            return result
        except Exception as e:
            raise ValueError(f"Fehler bei Auswertung: {e}")


class ExpressionEvaluator:

    valid_operators = ['>', '<', '>=', '*', '<=', '~', '/', '==', '!=', '&', '|', '(', ')']
    
    def __init__(self, dataframe: pd.DataFrame, dataframe_name = 'df'):
        self.df = dataframe
        self.dataframe_name = dataframe_name
        self.column_names = dataframe.columns.tolist()
        

    def validate_and_transform(self, expression: str) -> str:
        """
        Validates the expression and transforms it into the correct syntax for eval().
        """
        # Split the expression into tokens
        tokens = re.split(r'(\>\=|\<\=|\=\=|\!\=|>|<|\/|\s+|[~*&|!()])', expression)
        transformed_expression = []

        for token in tokens:
            token = token.strip()
            if not token:
                continue

            if token in self.valid_operators:
                # Valid operator, add as is
                transformed_expression.append(token)
            elif token in self.column_names:
                # Valid column name, transform to DataFrame syntax
                transformed_expression.append(f"{self.dataframe_name}['{token}']")
            elif re.match(r'^-?\d+(\.\d+)?$', token):
                # Numeric constant
                transformed_expression.append(token)
            else:
                # Invalid token
#                st.write(f"Invalid token in expression: {token}")
                pass
            
        return ' '.join(transformed_expression)

    def evaluate_expression(self, transformed_expression: str) -> pd.Series:
        """
        Evaluates the transformed expression using eval() and returns a boolean Series.
        """
        try:
            result = eval(transformed_expression)
            if not isinstance(result, pd.Series):
                raise ValueError("The evaluated expression must return a Pandas Series.")
            return result
        except Exception as e:
            raise ValueError(f"Error while evaluating the expression: {e}")


class BuySellSignalGenerator:
    # OHLCV columns that may appear in either capitalised (live df) or
    # lowercase (simulation db) form.  We add the missing alias so that
    # queries like "Close > 100" and "close > 100" both work regardless
    # of which form the underlying dataframe uses.
    _OHLCV_PAIRS = [('Close', 'close'), ('Open', 'open'),
                    ('High', 'high'),   ('Low', 'low'), ('Volume', 'volume')]

    def __init__(self, df: pd.DataFrame, buy_condition: str, sell_condition: str, buy_delay_days: int = 0):
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
        try:
            self.df.sort_values(by=["ticker", "Date"], inplace=True)
        except Exception:
            pass
        try:
            self.df.reset_index(drop=True, inplace=True)
        except Exception:
            pass
        delayed_signals = []

        # Schritt 1: Kaufbedingung merken
        for ticker, group in self.df.groupby('ticker'):
            group = group.reset_index()
            context_df = group.copy()

            try:
                # Prefer the newer evaluator which can transform expressions like ewo[-1] or ewo.rolling(5).mean()
                try:
                    evaluator = ExpressionEvaluatorNew(context_df, dataframe_name='context_df')
                    transformed = evaluator.validate_and_transform(self.buy_condition)
                    buy_mask = eval(transformed, {}, {'context_df': context_df})
                    # ensure boolean Series
                    if not isinstance(buy_mask, (pd.Series,)):
                        raise ValueError('Transformed expression did not return a Series')
                except Exception:
                    # fallback to pandas eval on the dataframe
                    try:
                        buy_mask = context_df.eval(self.buy_condition)
                    except Exception as e:
                        st.error(f"[BUY-ERROR] {e}")
                        buy_mask = pd.Series([False]*len(group))
            except Exception as e:
                st.error(f"[BUY-ERROR] {e}")
                buy_mask = pd.Series([False]*len(group))

            # Kaufsignale + Delay
            for idx in group[buy_mask].index:
                delayed_idx = idx + self.buy_delay_days
                if delayed_idx < len(group):
                    delayed_signals.append(group.loc[delayed_idx, 'index'])  # merke globale DF-Index

        # Schritt 2: Kaufmarkierungen setzen
        self.df.loc[delayed_signals, 'buySell'] = 1

        # Schritt 3: Verkaufslogik (wie bisher)
        open_positions = {}

        for i, row in self.df.iterrows():
            ticker = row['ticker']

            # Verkauf bei offener Position
            if ticker in open_positions:
                tp = open_positions[ticker]['tp']
                sl = open_positions[ticker]['sl']

                context = {**row.to_dict(), 'tp': tp, 'sl': sl}
                try:
                    # Try to evaluate sell_condition using a safe transformed expression
                    try:
                        evaluator = ExpressionEvaluatorNew(pd.DataFrame([row]), dataframe_name='r')
                        transformed_sell = evaluator.validate_and_transform(self.sell_condition)
                        # r is a single-row df, so evaluate transformed expression
                        sell_res = eval(transformed_sell, {}, {'r': pd.DataFrame([row])})
                        cond = bool(sell_res) if not isinstance(sell_res, pd.Series) else bool(sell_res.iloc[0])
                        if cond:
                            self.df.at[i, 'buySell'] = -1
                            del open_positions[ticker]
                            continue
                    except Exception:
                        # fallback to old style eval using context dict
                        if eval(self.sell_condition, {}, context):
                            self.df.at[i, 'buySell'] = -1
                            del open_positions[ticker]
                            continue
                except Exception as e:
                    st.error(f"[SELL-ERROR] Row {i}, Ticker {ticker}: {e}")

            # Kauf, wenn markiert
            if self.df.at[i, 'buySell'] == 1 and ticker not in open_positions:
                open_positions[ticker] = {
                    'tp': row['take_profit'],
                    'sl': row['stop_loss'],
                    'entry_index': i
                }

        # Remove alias columns added in __init__ so they don't appear in
        # the display DataFrame or get persisted anywhere.
        if self._added_aliases:
            self.df.drop(columns=self._added_aliases, inplace=True, errors='ignore')
        return self.df


if __name__ == "__main__":

    pass
