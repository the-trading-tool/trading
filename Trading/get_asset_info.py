import sqlite3
import yfinance as yf
from datetime import datetime
import json
import pandas as pd
import logging
import time
from tradinglib import ticker_tools as tt
from tradinglib import tools as ts
from tradinglib.utils import DataUtils
from tradinglib import cli, logging_config, market_data

#import os
#os.environ["TradingDB"]=r'C:\\Users\\Kurt\\Documents\\Trading2\\database'

ftime_str = '%Y-%m-%d %H:%M:%S'

# Datenbankname
db_name = ts.Tools().get_path(path = 'database', file_name="asset_info.db")

# Verbindung zur SQLite-Datenbank herstellen (oder erstellen, falls sie nicht existiert)
conn = sqlite3.connect(db_name, timeout=10)
cursor = conn.cursor()

# Liste der Tickersymbole
#excel_file = 'ALL_Stocks_May_2024.xlsx'
#df = pd.read_excel(excel_file, index_col=0, comment='#')

class AssetList(tt.TickerTools):
    
    def read_assets(self):
        df = self.read_stock_list(db_path='database', db_name='yf_tickers.db')
        return df
    
df = AssetList().read_assets()
#print(df)
#exit()
if df.empty:

        db_info =  ts.Tools().get_path(path = 'database', file_name='yf_tickers.db')
        conn2 = sqlite3.connect(db_info)

        # Zweite Datenbank anhängen
        cursor2 = conn2.cursor()
        cursor2.execute("SELECT Ticker FROM stocks;")
        # Ergebnisse abrufen und anzeigen
        rows = cursor2.fetchall()
        conn2.close()
        df = pd.DataFrame(rows, columns=['Ticker'])

ticker_list = df['Ticker']

#ticker_list = ['CLNX.MC','ANA.MC','LOG.MC','GRF.MC','NTGY.MC','BBVA.MC','TEF.MC','COL.MC','IBE.MC','UNI.MC','IAG.MC','AENA.MC','CABK.MC','ITX.MC','MRL.MC','SAN.MC','RED.MC','BKT.MC','PUIG.MC','ELE.MC','ANE.MC','SAB.MC','ACX.MC','ENG.MC','FDR.MC','FER.MC','AMS.MC','MTS.MC','MAP.MC','ACS.MC']
#['0002.HK','0883.HK','0003.HK','0012.HK','2688.HK','1038.HK','0669.HK','1398.HK','1209.HK','0027.HK','9633.HK','0267.HK','0101.HK','2319.HK','1044.HK','6690.HK','0017.HK','1109.HK','2015.HK','0992.HK','2020.HK','2331.HK','2628.HK','1093.HK','9988.HK','3690.HK','1810.HK','9618.HK','2269.HK','0241.HK']
#ticker_list = [
#    'ABBN.SW',	'ALC.SW',	'GEBN.SW',	'GIVN.SW',	'HOLN.SW',	'KNIN.SW',	'LOGN.SW',	'LONN.SW',	'NESN.SW',	'NOVN.SW',	'PGHN.SW',	'CFR.SW',	'ROG.SW',	'SIKA.SW',	'SOON.SW',	'SLHN.SW',	'SREN.SW',	'SCMN.SW',	'UBSG.SW',	'ZURN.SW'
#    ]
#ticker_list = [
#    '^FTSE'
#    ]

period = 'max'
interval = '1m'

def get_num_rows(df):
    num_rows = 0
    try:
        num_rows = len(df.index)
    except Exception:
        pass

    return num_rows

def get_data_yf(symbol, period='1d', interval='1m'):

#    yf.pdr_override()
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)
    ticker = None
    df = {}

#    if 1:
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period = period, interval = interval)
        if get_num_rows(df) > 0:
            df.index = df.index.strftime(ftime_str)
            df.index = pd.to_datetime(df.index).strftime(ftime_str)
            df.index.name = 'Date'
#            info = ticker.get_info()

    except Exception:
        pass

    return df, ticker

# Funktion zur Erstellung der Tabelle, falls sie nicht existiert
def create_table(cursor, keys, info):

    columns = ['ticker TEXT PRIMARY KEY']

    for key in keys:
        # Überprüfen, ob der Schlüssel mit einer Zahl beginnt
        if key[0].isdigit():
            column_name = f"_{key}"
        else:
            column_name = key
        
        # Typ des ersten nicht-None Wertes bestimmen
        value = info.get(key, None)
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

        columns.append(f"{column_name} {column_type}")
    
    columns_sql = ', '.join(columns)
    
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS asset_info (
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        {columns_sql}
    )
    """)

# Funktion zur Erstellung oder Ergänzung der Tabelle, falls sie nicht existiert oder neue Spalten hinzugefügt werden müssen
def ensure_table_and_columns(cursor, ticker, keys, info):
    # Prüfe, ob die datenbank existiert
    cursor.execute(f"PRAGMA table_info('asset_info')")
    existing_columns = {row[1] for row in cursor.fetchall()}  # Set von existierenden Spaltennamen
    if len(existing_columns) == 0:
        # Sicherstellen, dass die Tabelle existiert
        create_table(cursor, keys, info)
    
    else:
        for key in keys:
            # Überprüfen, ob der Schlüssel mit einer Zahl beginnt
            if key[0].isdigit():
                column_name = f"_{key}"
            else:
                column_name = key
        
            # Wenn die Spalte noch nicht existiert, füge sie hinzu
            if column_name not in existing_columns:
                value = info.get(key, None)
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

                cursor.execute(f"ALTER TABLE asset_info ADD COLUMN {column_name} {column_type}")

# Funktion, um die Tabelle mit den Daten zu befüllen
def insert_data(cursor, ticker, keys, info):
    values = [ticker]
    column_names = ['ticker']

    for key in keys:
        # Überprüfen, ob der Schlüssel mit einer Zahl beginnt
        if key[0].isdigit():
            column_name = f"_{key}"
        else:
            column_name = key
        
        column_names.append(column_name)
        
        try:
            value = info.get(key, None)  # Abrufen des Werts für den Schlüssel
            if isinstance(value, list):
                value = json.dumps(value)  # Listen in JSON-Strings umwandeln
        except Exception as e:
            print(f"Fehler beim Abrufen des Werts für {key} in {ticker}: {e}")
            value = None  # Setze den Wert auf None, falls ein Fehler auftritt
        
        values.append(value)
    
    print(f'{ticker}')
    placeholders = ', '.join(['?'] * (len(values) + 1))  # +1 für den timestamp
    cursor.execute(f"""
    INSERT OR REPLACE INTO asset_info (timestamp, {', '.join(column_names)})
    VALUES ({placeholders})
    """, [datetime.now()] + values)

count = 0
batch = []
batch_size = 100

#ticker_list = ['B4B.DE']
#ticker_list = ['EURUSD=X','GBPUSD=X']
# Daten für jedes Tickersymbol abrufen und speichern
if __name__ == '__main__':
    # configure logging and parse CLI
    args = cli.parse_args()
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))

    for ticker in ticker_list:
        print(f'updating: ',end='')
        time.sleep(0.5)
        try:
            # get data from tradinglib.market_data (wraps yfinance) and force network fetch
            # use_cache=False forces fresh data from Yahoo
            info = market_data.ticker_info(ticker, use_cache=False)
            financials = market_data.ticker_financials(ticker, use_cache=False)
            balance_sheet = market_data.ticker_balance_sheet(ticker, use_cache=False)

            # Get income sheet data
            try:
                info['incomeSheet'] = financials.to_json(orient="split")
            except Exception:
                pass

            # Get balance sheet data
            try:
                info['balanceSheet'] = balance_sheet.to_json(orient="split")
            except Exception:
                pass

            # Erhalte alle verfügbaren Schlüssel aus den Info-Daten
            keys = info.keys()

            # Build mapping (normalize keys and serialize lists/dicts)
            # ticker muss als erstes eingetragen werden -- bulk_upsert_dicts
            # erstellt die Tabelle aus den row_map-Keys; ohne expliziten Eintrag
            # wuerde die ticker-Spalte fehlen und fetch_data.py mit
            # "no such column: ticker" scheitern.
            row_map = {'ticker': ticker}
            for key in keys:
                if key[0].isdigit():
                    column_name = f"_{key}"
                else:
                    column_name = key
                try:
                    value = info.get(key, None)
                    if isinstance(value, (list, dict)):
                        value = json.dumps(value)
                except Exception:
                    value = None
                row_map[column_name] = value

            batch.append(row_map)

        except Exception:
            # ignore tickers that fail, but continue
            continue

        # Änderungen speichern und die Verbindung schließen
        count += 1
        if count % 100 == 0:
            conn.commit()

# flush remaining batch using DataUtils helper
if len(batch) > 0:
    DataUtils.bulk_upsert_dicts(conn, 'asset_info', batch)

conn.commit()
conn.close()

