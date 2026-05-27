from tradinglib import tools as ts

class SQLiteCopy:
    def __init__(self, source_db, target_db):
        """Initialisiert die Quelldatenbank und Zieldatenbank-Pfade"""
        self.source_db = source_db
        self.target_db = target_db

    def fetch_data(self, table_name, query = None):
        """Liest alle Daten aus der angegebenen Tabelle der Quelldatenbank"""
        if query == None:
                query = f"""
                SELECT t1.*
                FROM {table_name} t1
                JOIN (
                    SELECT ticker, MAX(date) AS max_date
                    FROM {table_name}
                    GROUP BY ticker
                ) t2 ON t1.ticker = t2.ticker AND t1.date = t2.max_date
                """
        self.source_db.cursor.execute(query)
        data = self.source_db.cursor.fetchall()
        return data

    def get_table_schema(self, table_name):
        """Liest die Tabellenstruktur aus (Spaltennamen + Datentypen)"""
        self.source_db.cursor.execute(f"PRAGMA table_info({table_name})")
        schema_info = self.source_db.cursor.fetchall()  # Enthält (ID, Name, Typ, NotNull, Default, PK)
        columns = [(col[1], col[2]) for col in schema_info]  # [(Spaltenname, Datentyp), ...]
        return columns


    def create_table_and_insert(self, table_name, data, columns):
        """Erstellt die Tabelle in der Zieldatenbank mit exaktem Schema und kopiert die Daten"""
        # Tabellenstruktur beibehalten
        table_sql = f'DROP TABLE IF EXISTS "{table_name}"'
        self.target_db.cursor.execute(table_sql)

        column_definitions = ", ".join([f'"{name}" {dtype}' for name, dtype in columns])
        create_table_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({column_definitions})'
        self.target_db.cursor.execute(create_table_sql)

#        table_sql = f'DELETE FROM "{table_name}"'
#        self.target_db.cursor.execute(table_sql)

        # Daten einfügen
        placeholders = ", ".join(["?" for _ in columns])
        insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'
        self.target_db.cursor.executemany(insert_sql, data)
        self.target_db.conn.commit()

    def copy_table(self, source_table, target_table):
        """Führt den gesamten Kopierprozess durch"""
        columns = self.get_table_schema(source_table)
        data = self.fetch_data(source_table)
        self.create_table_and_insert(target_table, data, columns)
#        print(f"Tabelle '{source_table}' wurde erfolgreich kopiert.")

