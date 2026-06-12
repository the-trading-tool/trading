from tradinglib import ticker_tools as tt
from tradinglib import cli, logging_config
import sys
import time
import logging
import json
import os
import concurrent.futures
from datetime import datetime

# Beispielhafte Nutzung der Klasse
if __name__ == "__main__":

    # configure logging and parse common CLI flags
    args = cli.parse_args(sys.argv)
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))

    logger = logging.getLogger(__name__)
    logger.info("get_asset_data args=%s", args)

    intervals = tt.TickerTools().intervals
    periods = tt.TickerTools().periods

    # if any of the below stays False then we read tickers from excel list
    select = False
    index_members_only = False
    index_only = False
    all = False
    inverse = False

    # reuse CLI helper to extract interval:period pairs while preserving
    # original script semantics
    intervals_parsed, periods_parsed, pos, arg, suf = cli.parse_interval_periods(sys.argv)
    if intervals_parsed:
        intervals = intervals_parsed
        periods = periods_parsed

    # get pref-style flags from parse_args (this preserves and centralizes behavior)
    select = args.get('select', False)
    index_members_only = args.get('index_member', False)
    index_only = args.get('index_only', False)
    all = args.get('all', False)
    inverse = args.get('inverse', False)
    group = args.get('group') or []

    # if we have no interval pairs and no actionable pref flags, show usage
    actionable = bool(intervals_parsed) or any([select, index_members_only, index_only, all, inverse, bool(group)])
    if not actionable:
        print("Wrong arguments. Use pairs of 'interval:period', e.g. '1d:1mo'")
        exit()

#    if intervals == []:
    # If there is a new List of ticker symbols, we load it into our database
    info_db_table_name = "indices"
    info_db = tt.tools.Db_tools(db_path='database', database_name=f'yf_tickers.db')
    delete_columns = list(tt.tools.NON_STOCK_GROUPS)
    ticker_list = []
    if index_members_only:
        filtered = info_db.get_indices_names(delete_columns=delete_columns, table_name=info_db_table_name)
        try:
            filtered.remove('nan')
        except Exception:
            pass

#        # Achte auf das Ende des join-Teils und das fehlende Quote vor %=X
        ticker_query = 'SELECT s.Ticker FROM stocks s JOIN stock_indices si ON s.id = si.stock_id JOIN indices i ON si.index_id = i.id WHERE i.name IN ("' + '","'.join(filtered) + '") OR s.Ticker LIKE "%=X"'
        #ticker_query = f'SELECT s.Ticker FROM stocks s JOIN stock_indices si ON s.id = si.stock_id JOIN indices i ON si.index_id = i.id WHERE i.name = "' + '" OR i.name = "'.join(filtered)+'"' + '" OR s.Ticker LIKE "%=X"'
#        ticker_query = f"SELECT Ticker FROM {info_db_table_name} WHERE " + " = 1 OR ".join(filtered) + " = 1;"
        ticker_list = info_db.read_data(ticker_query)['Ticker']
#        ticker_list.to_csv("ticker_list.csv",decimal=",",sep=";")
        ticker_list = ticker_list.tolist()    
    elif index_only:
        non_stock_sql = '","'.join(tt.tools.NON_STOCK_GROUPS)
        ticker_query = f'SELECT s.Ticker AS name FROM stocks s JOIN stock_indices si ON s.id = si.stock_id JOIN indices i ON si.index_id = i.id WHERE i.name IN ("{non_stock_sql}")' +' OR s.Ticker LIKE "%=X"'
#        ticker_query = 'SELECT Ticker FROM yf_tickers WHERE "OTHER" = 1 OR INVESTED = 1;'
        ticker_list = info_db.read_data(ticker_query)['name']
        ticker_list = ticker_list.tolist()
        try:
            ticker_list.remove('INDEX')
        except Exception:
            pass
#        for w in ticker_list:
#                ticker_list.remove(w)
    elif group:
        # Mitglieder beliebiger Gruppen aus der indices-Tabelle laden,
        # z.B. /group:CRYPTO oder /group:METALS,COMMODITIES.
        # Gruppennamen kommen aus cli.parse_args bereits uppercased.
        group_sql = '","'.join(group)
        ticker_query = (
            'SELECT s.Ticker FROM stocks s '
            'JOIN stock_indices si ON s.id = si.stock_id '
            'JOIN indices i ON si.index_id = i.id '
            f'WHERE UPPER(i.name) IN ("{group_sql}")'
        )
        ticker_list = info_db.read_data(ticker_query)['Ticker'].tolist()
        if not ticker_list:
            logger.warning("No tickers found for group(s): %s", group)
    elif select:
        # prefer selection from pref-style arg if provided (e.g. /select:WHERE ...),
        # otherwise fall back to last colon-pair's right-hand side
        selection = args.get('selection') or (suf if suf else '')  #'WHERE Ticker LIKE "%.MC"'
        ticker_query = f'SELECT Ticker FROM stocks {selection};'
        ticker_list = info_db.read_data(ticker_query)['Ticker']
        ticker_list = ticker_list.tolist()    
    elif all:
        ticker_query = f'SELECT Ticker FROM stocks;'
        ticker_list = info_db.read_data(ticker_query)['Ticker']
        ticker_list = ticker_list.tolist()    
    elif inverse:
        ticker_query = f'SELECT s.Ticker FROM stocks s LEFT JOIN stock_indices si ON s.id = si.stock_id WHERE si.index_id IS NULL;'
        ticker_list = info_db.read_data(ticker_query)['Ticker']
        ticker_list = ticker_list.tolist()    
    else:
        # read all tickers and if file available import new data first
        df = tt.StockDataSaver('').read_stock_list(excel_file='ALL_Assets.xlsx')
        try:
            ticker_list = df['Ticker']
            ticker_list = ticker_list.tolist()    
        except Exception:
            pass
    if not ticker_list == []:
        ticker_list = list(set(ticker_list))

    # --- Failed-Ticker-Tracking ---
    # Pfad zur persistenten Liste fehlgeschlagener Ticker
    failed_tickers_file = info_db.get_path(path='database', file_name='failed_tickers.json')

    # Vorhandene Liste laden (aus vorherigen Läufen)
    existing_failed: dict = {}
    try:
        if os.path.exists(failed_tickers_file):
            with open(failed_tickers_file, 'r', encoding='utf-8') as _f:
                existing_failed = {e['ticker']: e for e in json.load(_f)}
    except Exception:
        logger.warning("Konnte failed_tickers.json nicht laden – starte mit leerer Liste.")

#    ticker_list = ["AML.L"]
    def _fetch_one(ticker):
        """Download all intervals for one ticker in its own StockDataSaver/DB connection.

        Returns (ticker, failed_intervals) — empty list = all OK, None = total failure.
        """
        print(f'Fetching data for {ticker}')
        time.sleep(0.5)
        saver = tt.StockDataSaver(ticker)  # ,tz_ready='Europe/Berlin')
        try:
            # force_remote=True ensures we fetch from Yahoo (network) by default and do not loop against local DB
            failed_intervals = saver.save_all_intervals(intervals=intervals, periods=periods, force_remote=True)
            time.sleep(1)
            return ticker, failed_intervals
        except Exception:
            logger.exception("Error saving data for %s", ticker)
            return ticker, None
        finally:
            try:
                saver.close_connection()
            except Exception:
                logger.exception("Error closing saver connection for %s", ticker)

    # /worker:N → parallele Downloads; Default 1 = bisheriges sequenzielles Verhalten.
    # Mehr als 4 Worker reizen das Yahoo-Rate-Limit (HTTP 429) unnötig.
    max_workers = args.get('worker') or 1
    if max_workers > 1:
        logger.info("Using %d parallel download workers.", max_workers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in ticker_list}
        for future in concurrent.futures.as_completed(futures):
            # existing_failed wird nur hier im Main-Thread gepflegt (kein Lock nötig)
            ticker, failed_intervals = future.result()
            if failed_intervals is None:
                # Vollständiger Fehler → alle Intervalle als fehlgeschlagen markieren
                failed_intervals = intervals
            if failed_intervals:
                # Mindestens ein Intervall hat versagt → in Failed-Liste eintragen
                logger.warning("Fehlgeschlagene Intervalle für %s: %s", ticker, failed_intervals)
                existing_failed[ticker] = {
                    'ticker': ticker,
                    'failed_intervals': failed_intervals,
                    'last_seen': datetime.now().isoformat(),
                    'first_seen': existing_failed.get(ticker, {}).get(
                        'first_seen', datetime.now().isoformat()),
                }
            else:
                print(f"Data for {ticker} saved.")
                # Ticker hat diesmal funktioniert → aus Failed-Liste entfernen
                existing_failed.pop(ticker, None)

    # Aktualisierte Failed-Liste speichern
    try:
        os.makedirs(os.path.dirname(failed_tickers_file), exist_ok=True)
        with open(failed_tickers_file, 'w', encoding='utf-8') as _f:
            json.dump(list(existing_failed.values()), _f, indent=2, ensure_ascii=False)
        if existing_failed:
            logger.info("%d fehlgeschlagene Ticker in %s gespeichert.",
                        len(existing_failed), failed_tickers_file)
    except Exception:
        logger.exception("Konnte failed_tickers.json nicht schreiben.")

