import streamlit as st
import pandas as pd
import sqlite3
import os
import io
from tradinglib.utils import DataUtils
from tradinglib.tools import open_db

class ExcelSQLExecutor:
    def __init__(self):
        self.config = {
            "commit_query_after_execution": True,
            "replace_or_append": "append",  # Options: "replace" or "append"
        }

    def parse_parameter_tab(self, excel_data):
        """
        Extract parameters from the Parameter tab and update the configuration.
        """
        try:
            parameters = excel_data.parse("Parameter")
            if not parameters.empty:
                for _, row in parameters.iterrows():
                    key = row.get("Parameter")
                    value = row.get("Value")
                    if key == "Commit query after execution":
                        self.config["commit_query_after_execution"] = bool(value)
                    elif key == "Replace / Append Database":
                        self.config["replace_or_append"] = "replace" if value == "Replace" else "append"
        except Exception as e:
            self.main.warning(f"Error, parsing 'Parameters': {e}")

    def handle_database_paths(self, excel_data):
        """
        Create or connect to SQLite databases based on the Paths tab.
        """
        try:
            paths_tab = excel_data.parse("Paths")
        except Exception as e:
            self.main.warning(f"Path not loaded: {e}")
            return

        if paths_tab is None or paths_tab.empty:
            self.main.warning("Path tab not found.")
            return

        if "db_paths" not in st.session_state:
            st.session_state["db_paths"] = {}

        for _, row in paths_tab.iterrows():
            db_path = row.get("Path")
            if not db_path:
                self.main.warning("No path found in 'Path' sheet.")
                continue

            db_name = os.path.basename(db_path).replace(".db", "")  # Derive table name from filename
            if db_name in st.session_state["db_paths"]:
                self.main.info(f"Database '{db_name}' already connected.")
                continue

            # Save path; connection is created on demand
            st.session_state["db_paths"][db_name] = db_path
            self.main.success(f"Database '{db_path}' registered.")

    def get_connection(self, db_name):
        """
        Create and return a SQLite connection for the specified database.
        """
        db_path = st.session_state["db_paths"].get(db_name)
        if not db_path:
            self.main.error(f"Error, no database '{db_name}'.")
            return None

        # Create connection
        return open_db(db_path, check_same_thread=False)

    def initialize_table_from_tab(self, excel_data, db_name, table_name):
        """
        Create or update a table in the database based on the corresponding Excel tab.
        """
        conn = self.get_connection(db_name)
        if not conn:
            return

        try:
            data = excel_data.parse(table_name)
            if data.empty:
                self.main.warning(f"Empty sheet '{table_name}'.")
                return

            cursor = conn.cursor()
            # Check whether the table already exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            table_exists = cursor.fetchone() is not None

            if table_exists:
                self.main.info(f"SQL table '{table_name}' already exists. Create a backup first!")
                backup_data = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
                self.main.subheader(f"Backup for: {table_name}")
                self.main.dataframe(backup_data)

                # Download button for the backup
                buffer = DataUtils.get_bin_excel_data(backup_data)
                self.main.download_button(
                    label=f"Download backup for {table_name}",
                    data=buffer,
                    file_name=f"{table_name}_backup.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            # Initialise table
            data.to_sql(table_name, conn, if_exists=self.config["replace_or_append"], index=False)
            self.main.success(f"SQL table '{table_name}' successfully created.")
        except Exception as e:
            self.main.error(f"Error cannot initialize table: '{table_name}': {e}")
        finally:
            conn.close()

    def initialize_table_with_index(self, excel_data, db_name, table_name, index_column):
        """
        Create or update a SQLite table with an index column.

        Parameters:
            excel_data (ExcelFile): Parsed Excel data.
            db_name (str): Database name (path to the SQLite file).
            table_name (str): Name of the table to create.
            index_column (str): Name of the column to use as PRIMARY KEY.
        """
        conn = self.get_connection(db_name)
        if not conn:
            
            return

        self.main.text(table_name)
        try:
            # Convert Excel sheet to DataFrame
            data = excel_data.parse(table_name)
            if data.empty:
                self.main.error(f"Sheet '{table_name}' is empty.")
                return

            # Check whether the index column exists
            if index_column not in data.columns:
                self.main.error(f"Index-Column '{index_column}' not found in data.")
                return

            # Create table with PRIMARY KEY
            cursor = conn.cursor()
            cursor.execute(f"DROP TABLE IF EXISTS {table_name};")  # Drop old table (optional)

            # Build dynamic schema based on columns
            columns = [f'"{col}" TEXT' for col in data.columns if col != index_column]
            create_table_sql = f"""
            CREATE TABLE {table_name} (
                "{index_column}" TEXT PRIMARY KEY,
                {", ".join(columns)}
            );
            """
            cursor.execute(create_table_sql)
            conn.commit()

            # Insert data without index column into the table
            data.to_sql(table_name, conn, if_exists="append", index=False)
            self.main.text(f"Sheet '{table_name}' succefully created using '{index_column}' as PRIMARY KEY.")
        except Exception as e:
            self.main.error(f"Error, initializing sheet: {e}")
        finally:
            conn.commit()
            conn.close()


    def execute_queries(self, excel_data):
        """
        Execute the queries from the Query tab against the connected databases.
        """
        if "db_paths" not in st.session_state or not st.session_state["db_paths"]:
            self.main.warning("No databases connected. Mind to enter them into the 'Path'-sheet.")
            return

        try:
            query_tab = excel_data.parse("Query")
        except Exception as e:
            self.main.warning(f"Error, no Excel sheet named 'Query': {e}")
            return

        if query_tab is None or query_tab.empty:
            self.main.warning("No queries found.")
            return

        results = {}

        for _, row in query_tab.iterrows():
            query_name = row.get("QueryName", "Unnamed Query")
            query_sql = row.get("SQL", "")
            if not query_sql:
                self.main.warning(f"Query '{query_name}' is empty.")
                continue

            for db_name in st.session_state["db_paths"].keys():
                conn = self.get_connection(db_name)
                if not conn:
                    continue

                try:
                    cursor = conn.cursor()
                    cursor.execute(query_sql)
                    if cursor.description:  # Only for SELECT and similar statements
                        results[query_name] = pd.DataFrame(cursor.fetchall(), columns=[desc[0] for desc in cursor.description])
                    else:
                        results[query_name] = f"Query '{query_name}' execution succeful."

                    # Commit after each query (if configured)
                    if self.config["commit_query_after_execution"]:
                        conn.commit()
                except Exception as e:
                    self.main.error(f"Error in query: '{query_name}' database '{db_name}': {e}")
                finally:
                    conn.close()

        return results

    def run(self):
        """
        Startet die Streamlit-Anwendung.
        """
        frame = st.empty()
        (self.sb, _, self.main) = frame.columns([1,0.1,4])
    
        # Excel-Datei hochladen
        uploaded_file = self.sb.file_uploader("Upload an Excel file", type=["xlsx", "xls"])

        if uploaded_file:
            try:
                excel_data = pd.ExcelFile(uploaded_file)
                self.parse_parameter_tab(excel_data)

                # Automatische Verarbeitung des Paths-Tabs, um Datenbankverbindungen herzustellen
                self.handle_database_paths(excel_data)

                index_column = self.sb.text_input("Index (Primary Key):")
                if self.sb.button("Create sheet with Index"):
                    for db_name in st.session_state["db_paths"].keys():
                        if db_name in excel_data.sheet_names:
                            self.initialize_table_with_index(
                                excel_data=excel_data,
                                db_name=db_name,
                                table_name=db_name,
                                index_column=index_column
                            )

#                if self.sb.button("Import data"):
#                    for db_name in st.session_state["db_paths"].keys():
#                        if db_name in excel_data.sheet_names:
#                        else:
#                            self.main.text(f'Please name your data sheet correctly. Current sheets available: {excel_data.sheet_names}' )

                if self.sb.button("Execute queries"):
                    self.main.header("Query results")
                    results = self.execute_queries(excel_data)
                    if results:
                        for query_name, result in results.items():
                            self.main.subheader(query_name)
                            if isinstance(result, pd.DataFrame):
                                self.main.dataframe(result)
                            else:
                                self.main.text(result)

            except Exception as e:
                self.main.error(f"Error importing Excel file: {e}")


# Instanz der App-Klasse erstellen und starten
if __name__ == "__main__":
    app = ExcelSQLExecutor()
    app.run()

