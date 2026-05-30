import sqlite3
import streamlit as st
import pandas as pd
#from tradinglib import market_map
from tradinglib import tools
from tradinglib import make_query as mq
from tradinglib.i18n import t



class FullTextSearch(tools.Db_tools):

    ticker_selected = ''
    index_name = ''
    ticker_selected_longname = ''    
    ticker_exchange = 'GER'

    df = pd.DataFrame()
    
    def __init__(self, db_path = 'database', file_name = 'asset_info.db', table_name = 'asset_info', region = st, symbol = '', search_ticker_only = False, is_admin = False):
        self.db_path = self.get_path(path = db_path, file_name=file_name)
        self.table_name = table_name
        self.is_admin = is_admin
        self.fts_table_name = f"{table_name}_fts"
        self.region = region
        self.symbol = symbol
        self.search_ticker_only = search_ticker_only
        self.create_fts_table()

    def get_connection(self):
        """Erstellt eine Verbindung zur SQLite-Datenbank."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Zugriff auf Spalten per Namen
        return conn

    def update_fts_table(self):
        """Löscht die FTS5-Tabelle und füllt sie mit aktuellen Daten neu."""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Lösche die bestehende FTS-Tabelle
        cursor.execute(f"DROP TABLE IF EXISTS {self.fts_table_name};")

        # Erstelle die FTS-Tabelle erneut
        cursor.execute(f"""
        CREATE VIRTUAL TABLE {self.fts_table_name}
        USING fts5(ticker, longName);
        """)

        # Daten aus der Originaltabelle erneut einfügen
        cursor.execute(f"""
        INSERT INTO {self.fts_table_name} (ticker, longName)
        SELECT ticker, longName FROM {self.table_name};
        """)

        conn.commit()
        conn.close()

    def create_fts_table(self):
        """Erstellt eine FTS5-Tabelle für ticker und longName, falls sie nicht existiert."""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Erstelle FTS-Tabelle mit zwei Spalten (ticker und longName)
        cursor.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table_name}
        USING fts5(ticker, longName);
        """)

        # Überprüfen, ob bereits Daten vorhanden sind
        cursor.execute(f"SELECT COUNT(*) FROM {self.fts_table_name};")
        if cursor.fetchone()[0] == 0:
            # Pruefen ob Quell-Tabelle die benoetigten Spalten hat
            cursor.execute(f"PRAGMA table_info({self.table_name})")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if 'ticker' in existing_cols and 'longName' in existing_cols:
                cursor.execute(f"""
                INSERT INTO {self.fts_table_name} (ticker, longName)
                SELECT ticker, longName FROM {self.table_name};
                """)
            # Wenn Spalten fehlen (noch kein get_asset_info.py gelaufen),
            # bleibt die FTS-Tabelle leer -- Suche liefert dann keine Treffer.

        conn.commit()
        conn.close()

    def search(self, query, limit=10):
        """Durchsucht die FTS-Tabelle nach Übereinstimmungen."""
        conn = self.get_connection()
        cursor = conn.cursor()

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
        """Gibt die vollständige Datenzeile eines bestimmten Tickers zurück."""
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
        
        perf_table = 'asset_simulation'
        db_path = 'database'
        db = tools.Db_tools(db_path=db_path, database_name='yf_tickers.db')
        db.conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = db_path, file_name='asset_simulation_.db')}' AS performance_db")
        db.conn.execute(f"ATTACH DATABASE '{tools.Tools().get_path(path = db_path, file_name='asset_info.db')}' AS info_db")

        query = mq.make_query(perf_table, index=index, q=q, conn=db.conn)
        self.df = pd.read_sql_query(query, db.conn)
    
    def symbol_search(self):
        
        self.get_df()    
        self.df = self.df.loc[self.df['ticker'] == self.symbol]
        self.ticker_selected = self.symbol
        try:
            self.ticker_selected_longname = self.df['longName'].iloc[0]
        except Exception:
            pass
        
    
    def render(self):
        # Initialisiere die Klasse mit der Datenbank
        #db_path = "C:\\Users\\Kurt\\Development\\database\\asset_info.db"  # Pfad zur SQLite-Datenbank
        #table_name = "asset_info"  # Dein Tabellenname

        # Eingabefeld für die Suche
            
        expander = self.region.expander(t('search.fts_expander'))

        with expander:
            search_query = st.text_input(t('search.ticker_name'), self.symbol)
            if search_query:
                ctr = ['.','&','"']
                for c in ctr:
                    search_query = search_query.replace(c,' ')
                results = self.search(search_query)

                if results:
                	# Eine Liste von Ergebnissen für die Auswahl bereitstellen
                    if self.search_ticker_only:
                        org = results
                        results = [row for row in results if row['ticker'] == self.symbol]
                        if len(results) == 0:
                            results = org
                    ticker_list = []
                    for row in results:
                        longname = row['longName']
                        if longname == None:
                            self.get_df(index=row['ticker'],q=5)
#                            st.write(self.df['shortName'])
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
    
    def __init__(self, db_path = 'database', file_name = 'yf_tickers.db', table_name = 'yf_tickers', region = st, load_full_df = True, show_market_only = False, hide_render=False):
        self.db_path = self.get_path(path = db_path, file_name=file_name)
        self.table_name = table_name
        self.region = region
        self.hide_render = hide_render
        self.show_market_only = show_market_only
        self.load_full_df = load_full_df
        
    def get_connection(self):
        """Connect to db."""
        conn = sqlite3.connect(self.db_path)
        return conn

    def search(self,query):

        conn = self.get_connection()

        results = pd.read_sql_query(query, conn)
        conn.close()
        return results

    def get_df(self, index, q=2):
        
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
            # asset_info.db hat noch keine ticker-Spalte (get_asset_info.py
            # noch nicht gelaufen) -- leeren DataFrame zurueckgeben
            self.df = pd.DataFrame()

    def get_index_list(self):
        table_name = "indices"
        query = f"""
                SELECT name FROM {table_name};
                """

        results = self.search(query)['name'].tolist()
        # as INDEX is a special SQL token we need to map and remap this term
#        results = list(map(lambda x: 'INDEX' if x == 'OTHER' else x, results))
        results.sort()
        return results
    
    def render(self):

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
#                        selected_ticker = 'OTHER'
                    q=7
#                    if selected_ticker == "INDEX":
#                        q=7
                    self.get_df(selected_ticker, q=q) 
                    if not self.show_market_only:
                        ticker_list = []
                        for index, row in self.df.iterrows():
                            longname = ""
                            try:
                                longname = row['longName']
                            except Exception:
                                pass
                            if longname == None:
                                longname = ""
                            ticker_list.append(f"{row['ticker']} - {longname}")

                        ticker_list.sort()
                        try:
#                            pos = ticker_list.index("^GDAXI - ")
                            pos = ticker_list.index("^GDAXI - DAX P")
                        except Exception:
                            pos = 0
                            pass
                        if not self.hide_render:
                            selected = st.selectbox(
                                t('search.select_company'),
                                ticker_list,
                                pos
                            )
                        self.ticker_selected = selected_ticker
                        self.ticker_selected_longname = ""
                        try:
                            self.ticker_selected = selected.split(" - ")[0]
                            self.ticker_selected_longname = selected.split(" - ")[1]
                        except Exception:
                            pass