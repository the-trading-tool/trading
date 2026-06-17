import os
import re
import sqlite3
import pandas as pd
import streamlit as st
from tradinglib import tools
from tradinglib import ticker_tools as tt
from tradinglib.utils import DataUtils
from tradinglib.tools import open_db
import io


class SQLiteEditor(tt.TickerTools):

    def __init__(self, db_path="asset_simulation_.db"):
        """Initialize the SQLite editor and resolve the full database file path."""
        self.db_path = tools.Tools().get_path(path="database", file_name=db_path)

    def set_database(self, db_path):
        """Resolve and update the active database file path."""
        self.db_path = tools.Tools().get_path(path="database", file_name=db_path)

    @staticmethod
    def _db_alias(file_path: str) -> str:
        """Derive a valid SQLite identifier from a database file path."""
        name = os.path.splitext(os.path.basename(file_path))[0]
        name = re.sub(r'[^a-zA-Z0-9]', '_', name).strip('_') or 'db'
        return ('_' + name) if name[0].isdigit() else name

    def _discover_dbs(self) -> dict:
        """Return {alias: full_path} for all *.db files in the database folder, excluding the primary DB."""
        db_dir = os.path.dirname(self.db_path)
        result = {}
        try:
            for fname in sorted(os.listdir(db_dir)):
                if not fname.endswith('.db'):
                    continue
                full = os.path.join(db_dir, fname)
                if full == self.db_path:
                    continue
                alias = self._db_alias(full)
                base, i = alias, 2
                while alias in result:
                    alias, i = f"{base}_{i}", i + 1
                result[alias] = full
        except Exception:
            pass
        return result

    def list_tables(self):
        """Return a list of all table names in the current database."""
        try:
            conn = open_db(self.db_path, readonly=True)
        except Exception as e:
            self.main.error(f"Error: {e}")
            return []
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
        """Return a list of column names for table_name via PRAGMA table_info."""
        conn = open_db(self.db_path, readonly=True)
        try:
            query = f"PRAGMA table_info({table_name});"
            columns = pd.read_sql_query(query, conn)["name"].tolist()
            return columns
        except Exception as e:
            self.main.error(f"Error, looking up sheet columns: {e}")
            return []
        finally:
            conn.close()

    def _schema_attached(self, alias: str, path: str) -> dict:
        """Return {table: [columns]} for all tables in an attached database."""
        conn = open_db(self.db_path, readonly=True)
        try:
            conn.execute(f"ATTACH DATABASE ? AS {alias}", (path,))
            rows = conn.execute(
                f"SELECT name FROM {alias}.sqlite_master WHERE type='table';"
            ).fetchall()
            schema = {}
            for (tname,) in rows:
                try:
                    col_rows = conn.execute(
                        f"PRAGMA {alias}.table_info('{tname}');"
                    ).fetchall()
                    schema[tname] = [r[1] for r in col_rows]
                except Exception:
                    schema[tname] = []
            return schema
        except Exception:
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def execute_query(self, query, attached_dbs=None):
        """Execute a SQL query with optional additional databases ATTACHed."""
        conn = open_db(self.db_path)
        try:
            if attached_dbs:
                for alias, path in attached_dbs.items():
                    conn.execute(f"ATTACH DATABASE ? AS {alias}", (path,))
            if query.strip().upper().startswith(("SELECT", "PRAGMA")):
                return pd.read_sql_query(query, conn)
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
        """Replace all rows in table_name with the contents of df."""
        conn = open_db(self.db_path)
        try:
            conn.execute(f"DELETE FROM {table_name}")
            df.to_sql(table_name, conn, if_exists="append", index=False)
            conn.commit()
            self.main.success("Data successfully written to database!")
        except Exception as e:
            self.main.error(f"Error, writing to database: {e}")
        finally:
            conn.close()

    def _query_templates(self, primary_tables: list, attached_dbs: dict) -> dict:
        """Build cross-DB query templates based on which databases are attached."""
        pt = primary_tables[0] if primary_tables else "table"
        attached = set(attached_dbs.keys())
        tpl = {}

        if "asset_info" in attached:
            tpl["Ticker + Firmeninfo"] = (
                f"SELECT t.*, ai.longName, ai.sector, ai.country, ai.marketCap\n"
                f"FROM {pt} t\n"
                f"JOIN asset_info.asset_info ai ON t.ticker = ai.ticker\n"
                f"ORDER BY ai.longName\n"
                f"LIMIT 50;"
            )

        if "yf_tickers" in attached:
            tpl["Ticker nach Index filtern"] = (
                f"SELECT t.ticker, i.name AS markt\n"
                f"FROM {pt} t\n"
                f"JOIN yf_tickers.stock_indices si ON t.ticker = si.Ticker\n"
                f"JOIN yf_tickers.indices i ON si.index_id = i.id\n"
                f"WHERE i.name = '^SPX'\n"
                f"LIMIT 50;"
            )
            tpl["Alle Indexmitgliedschaften je Ticker"] = (
                f"SELECT t.ticker, GROUP_CONCAT(i.name, ', ') AS maerkte\n"
                f"FROM {pt} t\n"
                f"JOIN yf_tickers.stock_indices si ON t.ticker = si.Ticker\n"
                f"JOIN yf_tickers.indices i ON si.index_id = i.id\n"
                f"GROUP BY t.ticker\n"
                f"ORDER BY t.ticker\n"
                f"LIMIT 50;"
            )

        if "asset_info" in attached and "yf_tickers" in attached:
            tpl["Vollständige Ansicht (Info + Markt)"] = (
                f"SELECT t.ticker, ai.longName, ai.sector, ai.country,\n"
                f"       GROUP_CONCAT(i.name, ', ') AS maerkte\n"
                f"FROM {pt} t\n"
                f"JOIN asset_info.asset_info ai ON t.ticker = ai.ticker\n"
                f"LEFT JOIN yf_tickers.stock_indices si ON t.ticker = si.Ticker\n"
                f"LEFT JOIN yf_tickers.indices i ON si.index_id = i.id\n"
                f"GROUP BY t.ticker\n"
                f"ORDER BY ai.sector, ai.longName\n"
                f"LIMIT 100;"
            )

        # Simulation-DB in attached → cross-sim-Abfragen
        sim_aliases = [a for a in attached if "simulation" in a or a.startswith("sim_")]
        for sim_alias in sim_aliases[:1]:
            if "asset_info" in attached:
                tpl[f"Top Scores [{sim_alias}] + Firmeninfo"] = (
                    f"SELECT s.ticker, ai.longName, ai.sector,\n"
                    f"       s.score, s.ewo, s.rsi, s.adx\n"
                    f"FROM {sim_alias}.assets s\n"
                    f"JOIN asset_info.asset_info ai ON s.ticker = ai.ticker\n"
                    f"WHERE s.score > 0\n"
                    f"ORDER BY s.score DESC\n"
                    f"LIMIT 100;"
                )
            if len(sim_aliases) > 1:
                other = sim_aliases[1]
                tpl[f"Score-Vergleich [{sim_alias}] vs [{other}]"] = (
                    f"SELECT a.ticker, a.score AS score_{sim_alias},\n"
                    f"       b.score AS score_{other},\n"
                    f"       (a.score - b.score) AS diff\n"
                    f"FROM {sim_alias}.assets a\n"
                    f"JOIN {other}.assets b ON a.ticker = b.ticker\n"
                    f"ORDER BY diff DESC\n"
                    f"LIMIT 50;"
                )

        return tpl

    def render_editor(self):
        """Render the SQLite editor UI with multi-database ATTACH support."""
        frame = st.empty()
        (self.sb, _, self.main) = frame.columns([1, 0.1, 4])

        # --- Primäre Datenbank ---
        db_path_input = self.main.text_input("Primäre SQLite-Datenbank:", value=self.db_path)
        if db_path_input != self.db_path:
            self.set_database(db_path_input)

        tables = self.list_tables()
        selected_table = None
        if tables:
            selected_table = self.sb.selectbox("Tabelle (Primär-DB)", tables)
            if selected_table:
                columns = self.list_columns(selected_table)
                self.sb.caption(", ".join(columns))

        # --- Weitere DBs einbinden ---
        self.sb.divider()
        self.sb.markdown("**Weitere DBs einbinden**")
        known_dbs = self._discover_dbs()
        label_to_alias = {
            f"{alias}  ({os.path.basename(path)})": alias
            for alias, path in known_dbs.items()
        }
        chosen_labels = self.sb.multiselect("Alias  (Dateiname)", list(label_to_alias.keys()))
        attached_dbs = {
            label_to_alias[lbl]: known_dbs[label_to_alias[lbl]]
            for lbl in chosen_labels
        }

        # Schema der eingebundenen DBs
        for alias, path in attached_dbs.items():
            schema = self._schema_attached(alias, path)
            with self.sb.expander(f"[{alias}]", expanded=False):
                for tname, cols in schema.items():
                    st.markdown(f"**{tname}**")
                    st.caption(", ".join(cols) if cols else "–")

        # --- Session State ---
        if 'recent_queries' not in st.session_state:
            st.session_state['recent_queries'] = []
        if 'query_result' not in st.session_state:
            st.session_state['query_result'] = None

        def update_recent_queries(q):
            if q and q not in st.session_state['recent_queries']:
                st.session_state['recent_queries'].insert(0, q)
                if len(st.session_state['recent_queries']) > 10:
                    st.session_state['recent_queries'].pop()

        # --- Query-Templates (nur wenn DBs eingebunden) ---
        default_query = f"SELECT * FROM {selected_table} LIMIT 20;" if selected_table else ""
        if attached_dbs:
            templates = self._query_templates(tables, attached_dbs)
            if templates:
                options = ["– Eigene Abfrage –"] + list(templates.keys())
                chosen = self.main.selectbox("Query-Template:", options)
                if chosen != "– Eigene Abfrage –":
                    default_query = templates[chosen]

        # --- Letzte Abfragen ---
        selected_query = self.main.selectbox(
            "Letzte Abfragen:",
            options=[""] + st.session_state['recent_queries'],
            format_func=lambda x: "Abfrage auswählen …" if x == "" else x,
        )

        # --- Query-Eingabe ---
        query = self.main.text_area(
            "SQL-Abfrage:",
            value=selected_query or default_query,
            height=150,
        )

        if self.main.button("Ausführen"):
            update_recent_queries(query)
            data = self.execute_query(query, attached_dbs or None)
            if data is not None:
                st.session_state['query_result'] = data

        # --- Ergebnis ---
        if st.session_state['query_result'] is not None:
            self.main.write("Ergebnis:")
            edited_data = self.main.data_editor(
                st.session_state['query_result'], use_container_width=True
            )
            buffer = DataUtils.get_bin_excel_data(edited_data)
            self.main.download_button(
                label="Backup herunterladen",
                data=buffer,
                file_name="table_backup.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            table_name = self.main.text_input("Tabellenname zum Speichern:")
            if self.main.button("Änderungen speichern"):
                if table_name:
                    self.update_table(edited_data, table_name)
                else:
                    self.main.error("Tabellenname eingeben.")
