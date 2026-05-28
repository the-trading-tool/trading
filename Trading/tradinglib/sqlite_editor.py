import sqlite3
import pandas as pd
import streamlit as st
from tradinglib import tools
from tradinglib import ticker_tools as tt
from tradinglib.utils import DataUtils
import io

class SQLiteEditor(tt.TickerTools):

    def __init__(self, db_path="asset_simulation_.db"):
        """
        Initialisiert den SQLite-Editor mit einem Datenbankpfad.
        :param db_path: Standardpfad zur SQLite-Datenbank
        """
        self.db_path = tools.Tools().get_path(path = "database", file_name=db_path)

    def set_database(self, db_path):
        """
        Setzt den Pfad zur SQLite-Datenbank.
        :param db_path: Pfad zur SQLite-Datenbank
        """
        self.db_path = tools.Tools().get_path(path = "database", file_name=db_path)

    def list_tables(self):
        """
        Listet alle Tabellen in der Datenbank auf.
        :return: Liste von Tabellennamen
        """
        try:
            conn = sqlite3.connect(self.db_path)
        except Exception as e:
            self.main.error(f"Error: {e}")
        try:
            query = "SELECT name FROM sqlite_master WHERE type='table';"
            tables = pd.read_sql_query(query, conn)["name"].tolist()
            return tables
        except Exception as e:
            self.main.error(f"Error, looking up data from sheets: {e}")
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def list_columns(self, table_name):
        """
        Listet alle Spalten einer Tabelle auf.
        :param table_name: Name der Tabelle
        :return: Liste von Spaltennamen
        """
        conn = sqlite3.connect(self.db_path)
        try:
            query = f"PRAGMA table_info({table_name});"
            columns = pd.read_sql_query(query, conn)["name"].tolist()
            return columns
        except Exception as e:
            self.main.error(f"Error, looking up sheet columns: {e}")
            return []
        finally:
            conn.close()

    def execute_query(self, query):
        """
        Führt eine SQL-Abfrage aus. Unterstützt sowohl SELECT als auch schreibende Abfragen.
        :param query: SQL-Abfrage
        :return: DataFrame bei SELECT-Abfragen, sonst None
        """
        conn = sqlite3.connect(self.db_path)
        try:
            if query.strip().upper().startswith("SELECT") or query.strip().upper().startswith("PRAGMA"):
                # SELECT-Abfrage ausführen und DataFrame zurückgeben
                df = pd.read_sql_query(query, conn)
                return df
            else:
                # Schreibende Abfragen (UPDATE, DELETE, INSERT)
                cursor = conn.cursor()
                cursor.execute(query)
                conn.commit()
                self.main.success("Query successfully executed!")
                return None
        except Exception as e:
            self.main.error(f"Error, query failed: {e}")
            return None
        finally:
            conn.close()
        
    def update_table(self, df, table_name):
        """
        Überschreibt eine Tabelle in der Datenbank mit einem DataFrame.
        :param df: DataFrame mit den neuen Daten
        :param table_name: Name der Tabelle, die aktualisiert werden soll
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            # Löscht bestehende Daten in der Tabelle und fügt die neuen ein
            cursor.execute(f"DELETE FROM {table_name}")
            df.to_sql(table_name, conn, if_exists="append", index=False)
            conn.commit()
            self.main.success("Data succefully written to database!")
        except Exception as e:
            self.main.error(f"Error, writing to database: {e}")
        finally:
            conn.close()

    def render_editor(self):
        """
        Stellt die gesamte Oberfläche für den SQLite-Editor in Streamlit bereit.
        """
        frame = st.empty()
        (self.sb, _, self.main) = frame.columns([1,0.1, 4])

        # Eingabe für den Datenbankpfad
        db_path = self.main.text_input("Path to SQLite database:", value=self.db_path)
        if db_path != self.db_path:
            self.set_database(db_path)

        # Tabellen auflisten
        tables = self.list_tables()
        if tables:
#            self.sb.write("Accessible Tables:")
            selected_table = self.sb.selectbox("Table", tables)

            if selected_table:
                # Spalten auflisten
                columns = self.list_columns(selected_table)
                self.sb.write("Columns:")
                self.sb.write(", ".join(columns))

        # Initialisierung der Session State Variable
        if 'recent_queries' not in st.session_state:
            st.session_state['recent_queries'] = []

        if 'query_result' not in st.session_state:
            st.session_state['query_result'] = None  # Zum Speichern des Abfrageergebnisses        
        
        # Funktion zur Aktualisierung der Liste der letzten Abfragen
        def update_recent_queries(new_query):
            if new_query and new_query not in st.session_state['recent_queries']:
                st.session_state['recent_queries'].insert(0, new_query)  # Neue Abfrage an den Anfang setzen
                if len(st.session_state['recent_queries']) > 10:  # Nur 10 Abfragen speichern
                    st.session_state['recent_queries'].pop()  # Älteste Abfrage entfernen

        # Dropdown zur Auswahl einer früheren Abfrage
        selected_query = self.main.selectbox(
            "Select a previous SQL query:",
            options=[""] + st.session_state['recent_queries'],  # Leere Option für neue Abfragen
            format_func=lambda x: "Select a query" if x == "" else x
        )

        # Eingabefeld für SQL-Abfrage
        query = self.main.text_area(
            "Enter a SQL query:",
            value=selected_query or f"SELECT * FROM {selected_table} LIMIT 20;" if tables else "",
            height=150
        )

        if self.main.button("Execute query"):
            # SQL-Abfrage ausführen und anzeigen
            update_recent_queries(query)
            data = None  # Hier wird deine execute_query-Methode aufgerufen
            data = self.execute_query(query)
            if data is not None: 
                st.session_state['query_result'] = data  # Ergebnis speichern

        if st.session_state['query_result'] is not None:
            if st.session_state['query_result'] is not None:
                self.main.write("Result:")
                edited_data = self.main.data_editor(st.session_state['query_result'], use_container_width=True)
                buffer = DataUtils.get_bin_excel_data(edited_data)
                self.main.download_button(
                    label=f"Download backup",
                    data=buffer,
                    file_name=f"table_backup.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                # Eingabefeld für den Tabellennamen
                table_name = self.main.text_input(
                    "Sheet name:"
                )
                if self.main.button("Save changes"):
                    if table_name:
                        self.update_table(edited_data, table_name)
                    else:
                        self.main.error("Enter sheet name.")