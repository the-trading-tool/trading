import sqlite3
import streamlit as st
import pandas as pd
from tradinglib import tools
from tradinglib import make_query as mq
from tradinglib.i18n import t
from tradinglib.tools import open_db
from tradinglib.utils import get_display_name


class FullTextSearch(tools.Db_tools):

    ticker_selected = ''
    index_name = ''
    ticker_selected_longname = ''    
    ticker_exchange = 'GER'

    df = pd.DataFrame()
    
    def __init__(self, db_path='database', file_name='asset_info.db', table_name='asset_info', region=st, symbol='', search_ticker_only=False, is_admin=False):
        """Open asset_info.db and create the FTS5 virtual table if it doesn't exist yet."""
        self.db_path = self.get_path(path=db_path, file_name=file_name)
        self.table_name = table_name
        self.is_admin = is_admin
        self.fts_table_name = f"{table_name}_fts"
        self.region = region
        self.symbol = symbol
        self.search_ticker_only = search_ticker_only
        self.create_fts_table()

    def get_connection(self):
        """Open and return a SQLite connection with row_factory set to sqlite3.Row."""
        conn = open_db(self.db_path)
        conn.row_factory = sqlite3.Row  # Access columns by name
        return conn

    def update_fts_table(self):
        """Drop and rebuild the FTS5 table from the current source table data."""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Drop the existing FTS table
        cursor.execute(f"DROP TABLE IF EXISTS {self.fts_table_name};")

        # Recreate the FTS table
        cursor.execute(f"""
        CREATE VIRTUAL TABLE {self.fts_table_name}
        USING fts5(ticker, longName);
        """)

        # Re-insert data from the original table
        cursor.execute(f"""
        INSERT INTO {self.fts_table_name} (ticker, longName)
        SELECT ticker, longName FROM {self.table_name};
        """)

        conn.commit()
        conn.close()

    def create_fts_table(self):
        """Create the FTS5 virtual table for ticker/longName search if it doesn't exist yet."""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Create FTS table with two columns (ticker and longName)
        cursor.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table_name}
        USING fts5(ticker, longName);
        """)

        # Check if data already exists
        cursor.execute(f"SELECT COUNT(*) FROM {self.fts_table_name};")
        if cursor.fetchone()[0] == 0:
            # Check if the source table has the required columns
            cursor.execute(f"PRAGMA table_info({self.table_name})")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if 'ticker' in existing_cols and 'longName' in existing_cols:
                cursor.execute(f"""
                INSERT INTO {self.fts_table_name} (ticker, longName)
                SELECT DISTINCT ticker, longName FROM {self.table_name};
                """)
            # If columns are missing (get_asset_info.py has not run yet),
            # the FTS table stays empty — searches will return no results.

        conn.commit()
        conn.close()

    # Characters that cause FTS5 syntax errors when unquoted
    _FTS5_SPECIAL = frozenset('=+-*^():"\\')

    def search(self, query, limit=10):
        """Search for prefix matches and return up to limit results.

        Uses FTS5 for normal queries; falls back to LIKE on the base table
        when the query contains characters that would cause an FTS5 syntax
        error (e.g. tickers like DX=F, ES=F).
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        if any(c in self._FTS5_SPECIAL for c in query):
            cursor.execute(f"""
                SELECT ticker, longName FROM {self.table_name}
                WHERE ticker LIKE ? OR longName LIKE ?
                ORDER BY ticker
                LIMIT ?;
                """, (query + '%', '%' + query + '%', limit))
        else:
            cursor.execute(f"""
                SELECT ticker, longName FROM {self.fts_table_name}
                WHERE {self.fts_table_name} MATCH ?
                ORDER BY ticker
                LIMIT ?;
                """, (query + '*', limit))

        results = cursor.fetchall()
        conn.close()
        return results

    def get_full_record(self, ticker):
        """Return the full asset_info row for the given ticker, or None when not found."""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
        SELECT * FROM {self.table_name}
        WHERE ticker = ?;
        """, (ticker,))

        record = cursor.fetchone()
        conn.close()
        return record

    def get_df(self, index="", q=2):
        """Load the simulation + ticker + asset_info joined DataFrame into self.df."""
        perf_table = 'asset_simulation'
        db_path = 'database'
        db = tools.Db_tools(db_path=db_path, database_name='yf_tickers.db')
        db.conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = db_path, file_name='asset_simulation_.db')}' AS performance_db")
        db.conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = db_path, file_name='asset_info.db')}' AS info_db")

        query = mq.make_query(perf_table, index=index, q=q, conn=db.conn)
        self.df = pd.read_sql_query(query, db.conn)
    
    def symbol_search(self):
        """Load self.df filtered to self.symbol and set ticker_selected / ticker_selected_longname."""
        self.get_df()
        self.df = self.df.loc[self.df['ticker'] == self.symbol]
        self.ticker_selected = self.symbol
        try:
            self.ticker_selected_longname = self.df['longName'].iloc[0]
        except Exception:
            pass

    def auto_resolve(self):
        """Resolve self.symbol to a ticker automatically.

        1. Exact ticker match (fast path — symbol is already a valid ticker).
        2. FTS lookup — useful when a longName or partial name is passed via URL
           (e.g. /?symbol=Holcim%20AG).

        Sets self.ticker_selected and self.ticker_selected_longname if a match
        is found.  Does nothing when self.symbol is empty.
        """
        if not self.symbol:
            return
        # 1. Exact ticker match
        try:
            record = self.get_full_record(self.symbol)
            if record:
                self.ticker_selected = record['ticker']
                self.ticker_selected_longname = record['longName'] or ''
                return
        except Exception:
            pass
        # 2. FTS lookup (handles long names, partial matches)
        try:
            # Sanitise query — FTS5 special chars must not break the query
            safe_query = self.symbol
            for ch in ('.', '&', '"', "'", '(', ')'):
                safe_query = safe_query.replace(ch, ' ')
            results = self.search(safe_query.strip())
            if results:
                best = results[0]
                self.ticker_selected = best['ticker']
                self.ticker_selected_longname = best['longName'] or ''
        except Exception:
            pass

    def resolve_index_name(self, ticker=None):
        """Resolve a ticker to its primary index name directly from yf_tickers.db.

        Uses a direct stocks -> stock_indices -> indices join, with NO dependency
        on asset_simulation / asset_info. The index_name shown in the Asset Viewer
        otherwise only got set when the user picked a result from the FTS box
        (render()); tickers reached via URL auto-resolve or market-search kept the
        empty class default. This method makes index_name resolve for every path.

        Real exchange indices (^-prefixed) win over category groups (INDEX, ETP, …).
        Sets and returns self.index_name ('' when the ticker is in no index).
        """
        t_sel = (ticker or self.ticker_selected or '').strip()
        if not t_sel:
            self.index_name = ''
            return ''
        try:
            db = tools.Tools().get_path(path='database', file_name='yf_tickers.db')
            with open_db(db, readonly=True) as conn:
                row = conn.execute(
                    "SELECT i.name FROM stocks s "
                    "JOIN stock_indices si ON si.stock_id = s.id "
                    "JOIN indices i ON i.id = si.index_id "
                    "WHERE s.Ticker = ? "
                    "ORDER BY (i.name LIKE '^%') DESC LIMIT 1",
                    (t_sel,),
                ).fetchone()
            self.index_name = row[0] if row and row[0] else ''
        except Exception:
            self.index_name = ''
        return self.index_name


    
    def render(self):
        """Render the full-text search expander with query input and ticker select box."""
        expander = self.region.expander(t('search.fts_expander'))

        with expander:
            search_query = st.text_input(t('search.ticker_name'), '', placeholder=self.symbol, key='_fts_search_query')
            if search_query:
                ctr = ['.','&','"']
                for c in ctr:
                    search_query = search_query.replace(c,' ')
                results = self.search(search_query)

                if results:
                    # Provide a list of results for the selection.
                    # Note: Previously, when `search_ticker_only` was set, results
                    # were filtered to `row['ticker'] == self.symbol`. This sabotaged
                    # the free full-text search — as soon as the current ticker
                    # (e.g. from a rerun default) matched the input, only that one
                    # result remained and nothing else could be selected.
                    # The typed search now always shows all matches.
                    ticker_list = []
                    for row in results:
                        longname = row['longName']
                        if longname == None:
                            self.get_df(index=row['ticker'],q=5)
                            longname = self.df['shortName'][0]
                        ticker_list.append(f"{row['ticker']} - {longname}")

                    selected_ticker = st.selectbox(
                        t('search.select'),
                        ticker_list
                    )
                 
                    if selected_ticker:
                        self.ticker_selected = selected_ticker
                        self.ticker_selected_longname = ""
                        try:
                            self.ticker_selected = selected_ticker.split(" - ")[0]
                            self.ticker_selected_longname = selected_ticker.split(" - ")[1]
                        except Exception:
                            pass
                        self.get_df()    
                        self.df = self.df.loc[self.df['ticker'] == self.ticker_selected]
                        try:
                            self.index_name = self.df['index_name'].loc[self.df['ticker'] == self.ticker_selected].iloc[0]
                        except Exception:
                            self.index_name = "unknown"
                            pass
                    if self.is_admin:
                        if st.button(t('search.update_index')):
                            self.update_fts_table()
                            st.success(t('search.index_updated'))

                else:
                    st.write(t('search.no_results'))


class MarketSearch(tools.Db_tools):
    
    ticker_selected = ''
    ticker_selected_longname = ''
    ticker_exchange = ""
    df = pd.DataFrame()
    
    def __init__(self, db_path='database', file_name='yf_tickers.db', table_name='yf_tickers', region=st, load_full_df=True, show_market_only=False, hide_render=False, default_ticker=''):
        """Open yf_tickers.db for market/index-based ticker search."""
        self.db_path = self.get_path(path=db_path, file_name=file_name)
        self.table_name = table_name
        self.region = region
        self.hide_render = hide_render
        self.show_market_only = show_market_only
        self.load_full_df = load_full_df
        self.default_ticker = default_ticker
        
    def get_connection(self):
        """Connect to db."""
        conn = open_db(self.db_path)
        return conn

    def search(self, query):
        """Execute query against the database and return results as a DataFrame."""
        conn = self.get_connection()

        results = pd.read_sql_query(query, conn)
        conn.close()
        return results

    def get_df(self, index, q=2):
        """Load the simulation + ticker + info joined DataFrame for the given index into self.df."""
        # Reads directly from asset_simulation_.db — WAL mode allows concurrent
        # reads during daily writes without lock contention.
        perf_table = "asset_simulation"
        perf_db_file = "asset_simulation_.db"
        db_path = 'database'
        db = tools.Db_tools(db_path=db_path, database_name='yf_tickers.db')
        db.conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = db_path, file_name=perf_db_file)}' AS performance_db")
        db.conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = db_path, file_name='asset_info.db')}' AS info_db")
        query = mq.make_query(perf_table, index=index, q=q, q_ext="", conn=db.conn)
        try:
            self.df = pd.read_sql_query(query, db.conn)
        except Exception:
            # asset_info.db has no ticker column yet (get_asset_info.py
            # has not run yet) — return empty DataFrame
            self.df = pd.DataFrame()

    def get_index_list(self):
        """Return a deduplicated, sorted list of index names from the indices table."""
        table_name = "indices"
        query = f"""
                SELECT name FROM {table_name};
                """

        results = self.search(query)['name'].tolist()
        # Deduplicate: if both 'GDAXI' and '^GDAXI' exist keep only '^GDAXI'
        with_hat = {n for n in results if n.startswith('^')}
        results = [n for n in results if n.startswith('^') or f'^{n}' not in with_hat]
        results = sorted(set(results))
        return results
    
    def render(self):
        """Render the market/index selector expander with index and ticker dropdown menus."""
        self.markets_selected = []
        selected_ticker = 'INDEX'

        results = self.get_index_list()        
        #set default selcetion to list of indices
        pos = 1
        try:
            pos = results.index('INDEX')
        except Exception:
            pass
        if results:
            expander = self.region.expander(t('search.market_expander'))
            with expander:

                if not self.hide_render:
                    selected_ticker = st.selectbox(
                        t('search.select_market'),
                        results,
                        index=pos
                    )
                else:
                    selected_ticker = results[pos]

                if selected_ticker:
                    # Extract selection but first correct the token/column name issue
#                    if selected_ticker == 'INDEX':
                    q=7
#                    if selected_ticker == "INDEX":
                    self.get_df(selected_ticker, q=q) 
                    if not self.show_market_only:
                        ticker_list = []
                        for index, row in self.df.iterrows():
                            ticker_list.append(f"{row['ticker']} - {get_display_name(row)}")

                        ticker_list = sorted(set(ticker_list))
                        pos = 0
                        _key = '_mkt_ticker_select'
                        if _key not in st.session_state:
                            if self.default_ticker:
                                try:
                                    pos = next(i for i, s in enumerate(ticker_list) if s.startswith(f"{self.default_ticker} - "))
                                except StopIteration:
                                    pass
                            else:
                                try:
                                    pos = ticker_list.index("^GDAXI - DAX P")
                                except Exception:
                                    pass
                        if not self.hide_render:
                            selected = st.selectbox(
                                t('search.select_company'),
                                ticker_list,
                                index=pos,
                                key=_key
                            )
                        self.ticker_selected = selected_ticker
                        self.ticker_selected_longname = ""
                        try:
                            self.ticker_selected = selected.split(" - ")[0]
                            self.ticker_selected_longname = selected.split(" - ")[1]
                        except Exception:
                            pass
