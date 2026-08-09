import yfinance as yf
import json
import pandas as pd
import logging
import time
from tradinglib import ticker_tools as tt
from tradinglib import tools as ts
from tradinglib.utils import DataUtils
from tradinglib import cli, logging_config, market_data
from tradinglib.tools import open_db


ftime_str = '%Y-%m-%d %H:%M:%S'
logger = logging.getLogger(__name__)

# Datenbankname
db_name = ts.Tools().get_path(path = 'database', file_name="asset_info.db")

# Verbindung zur SQLite-Datenbank herstellen (oder erstellen, falls sie nicht existiert)
conn = open_db(db_name, timeout=10)


def rebuild_fts_table(conn, table_name='asset_info'):
    """Suchtabelle (FTS5) <table_name>_fts neu aufbauen, damit frisch geladene
    Ticker sofort in der Volltextsuche der App findbar sind.

    Entspricht funktional dem Admin-Button „Update index" (search.py
    FullTextSearch.update_fts_table), kommt aber ohne Streamlit-Import aus –
    Struktur (fts5(ticker, longName)) muss identisch bleiben, damit
    create_fts_table() in der App die bestehende Tabelle übernimmt.
    """
    fts = f"{table_name}_fts"
    cur = conn.cursor()
    # Quell-Spalten prüfen — ohne ticker/longName keinen Rebuild versuchen
    # (z.B. wenn alle yfinance-Abrufe fehlschlugen und asset_info leer/teilbefüllt ist)
    cur.execute(f"PRAGMA table_info({table_name})")
    cols = {row[1] for row in cur.fetchall()}
    if not {'ticker', 'longName'} <= cols:
        logger.warning("FTS-Rebuild übersprungen: Spalten ticker/longName fehlen in %s.", table_name)
        return
    cur.execute(f"DROP TABLE IF EXISTS {fts};")
    cur.execute(f"CREATE VIRTUAL TABLE {fts} USING fts5(ticker, longName);")
    cur.execute(f"INSERT INTO {fts} (ticker, longName) SELECT ticker, longName FROM {table_name};")
    conn.commit()
    n = cur.execute(f"SELECT COUNT(*) FROM {fts}").fetchone()[0]
    logger.info("FTS-Suchtabelle %s neu aufgebaut (%d Einträge).", fts, n)

# Liste der Tickersymbole

class AssetList(tt.TickerTools):
    
    def read_assets(self):
        """Load the full ticker list from yf_tickers.db into a DataFrame."""
        df = self.read_stock_list(db_path='database', db_name='yf_tickers.db')
        return df
    
def build_repair_ticker_list():
    """Nur Ticker, denen in asset_info ein Anzeigename fehlt.

    Fuer den gezielten Nachlauf nach einem Datenverlust: bis zum Fix in
    ``DataUtils.bulk_upsert_dicts`` hat jeder Lauf Felder ausgeloescht, die
    Yahoo in der jeweiligen Antwort nicht mitgeliefert hat (INSERT OR REPLACE
    statt echtem Upsert). Betroffene Zeilen lassen sich so nachziehen, ohne
    alle ~8400 Ticker erneut abzufragen.

    Liefert Zeilen ohne longName UND ohne shortName — bei denen also gar kein
    Name mehr steht.
    """
    db_info = ts.Tools().get_path(path='database', file_name='asset_info.db')
    conn2 = open_db(db_info, readonly=True)
    try:
        rows = conn2.execute(
            "SELECT ticker FROM asset_info "
            "WHERE (longName IS NULL OR TRIM(longName) = '') "
            "  AND (shortName IS NULL OR TRIM(shortName) = '') "
            "ORDER BY ticker").fetchall()
    finally:
        conn2.close()
    return pd.DataFrame(rows, columns=['Ticker'])['Ticker']


def build_ticker_list(group=None):
    """Return the Series of tickers whose Stammdaten should be (re)fetched.

    group=['ETP', …] → nur Mitglieder dieser Gruppen aus der indices-Tabelle in
    yf_tickers.db (gezieltes Nachladen, z.B. nur die ETPs). Ohne Gruppenfilter
    wird die volle Liste verwendet (read_stock_list importiert dabei
    All_Assets.xlsx neu, falls vorhanden; sonst Fallback auf die stocks-Tabelle).
    """
    if group:
        db_info = ts.Tools().get_path(path='database', file_name='yf_tickers.db')
        conn2 = open_db(db_info)
        # Gruppennamen kommen aus cli.parse_args bereits uppercased; Query nutzt
        # zusätzlich UPPER(i.name) → case-insensitiv.
        group_sql = '","'.join(group)
        q = ('SELECT s.Ticker FROM stocks s '
             'JOIN stock_indices si ON s.id = si.stock_id '
             'JOIN indices i ON si.index_id = i.id '
             f'WHERE UPPER(i.name) IN ("{group_sql}")')
        rows = conn2.execute(q).fetchall()
        conn2.close()
        return pd.DataFrame(rows, columns=['Ticker'])['Ticker']

    df = AssetList().read_assets()
    if df.empty:
        db_info = ts.Tools().get_path(path='database', file_name='yf_tickers.db')
        conn2 = open_db(db_info)
        rows = conn2.execute("SELECT Ticker FROM stocks;").fetchall()
        conn2.close()
        df = pd.DataFrame(rows, columns=['Ticker'])
    return df['Ticker']

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

    except Exception:
        pass

    return df, ticker

def fetch_info_for(ticker):
    """Hole info/financials/balance_sheet für einen Ticker und baue die row_map.

    Gibt ein dict für bulk_upsert_dicts zurück, oder None bei Fehler. Läuft pro
    Ticker in eigenem yfinance-Request (threadsicher: kein gemeinsames
    yf.download-shared._DFS).
    """
    print('updating: ', end='')
    time.sleep(0.5)
    try:
        # get data from tradinglib.market_data (wraps yfinance) and force network fetch
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

        # Build mapping (normalize keys and serialize lists/dicts).
        # ticker muss als erstes eingetragen werden -- bulk_upsert_dicts erstellt
        # die Tabelle aus den row_map-Keys; ohne expliziten Eintrag wuerde die
        # ticker-Spalte fehlen und fetch_data.py mit "no such column: ticker"
        # scheitern.
        row_map = {'ticker': ticker}
        for key in info.keys():
            column_name = f"_{key}" if key and key[0].isdigit() else key
            try:
                value = info.get(key, None)
                if isinstance(value, (list, dict)):
                    value = json.dumps(value)
            except Exception:
                value = None
            row_map[column_name] = value
        return row_map
    except Exception:
        # ignore tickers that fail, but continue
        return None


# Daten für jedes Tickersymbol abrufen und speichern
if __name__ == '__main__':
    import concurrent.futures

    # configure logging and parse CLI
    args = cli.parse_args()
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))

    group = args.get('group') or []        # nur diese Gruppen, z.B. /group:ETP
    max_workers = args.get('worker') or 1   # parallele Info-Downloads
    repair = bool(args.get('repair'))       # nur Ticker ohne jeden Namen

    if repair:
        ticker_list = list(build_repair_ticker_list())
        logger.info("get_asset_info /repair: %d Ticker ohne Namen, %d Worker",
                    len(ticker_list), max_workers)
    else:
        ticker_list = list(build_ticker_list(group))
        logger.info("get_asset_info: %d Ticker%s, %d Worker",
                    len(ticker_list),
                    f" (Gruppen={group})" if group else "",
                    max_workers)

    # row_maps werden nur hier im Main-Thread eingesammelt (kein Lock nötig)
    batch = []
    if max_workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_info_for, t): t for t in ticker_list}
            for future in concurrent.futures.as_completed(futures):
                row_map = future.result()
                if row_map:
                    batch.append(row_map)
    else:
        for t in ticker_list:
            row_map = fetch_info_for(t)
            if row_map:
                batch.append(row_map)

    # flush batch using DataUtils helper
    if len(batch) > 0:
        DataUtils.bulk_upsert_dicts(conn, 'asset_info', batch)

    conn.commit()

    # FTS-Suchtabelle neu aufbauen, damit neue Ticker sofort volltextsuchbar sind
    # (ersetzt den manuellen Admin-Klick "Update index"). Fehler hier dürfen den
    # bereits committeten Upsert nicht gefährden.
    try:
        rebuild_fts_table(conn, 'asset_info')
    except Exception:
        logger.exception("FTS-Rebuild fehlgeschlagen — Stammdaten sind gespeichert, "
                         "Volltextsuche ggf. erst nach Admin-„Update index\" aktuell.")

    conn.close()

