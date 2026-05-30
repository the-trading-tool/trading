import sqlite3
from tradinglib import tools

field_list = """
            ai.ticker,
            ai.sector,
            ai.shortName,
            ai.longName,
            ai.exchange,
            ai.industry,
            ai.longBusinessSummary,
            ai.beta,
            ai.lastDividendValue,
            ai.recommendationKey,
            ai.targetHighPrice,
            ai.targetMeanPrice,
            ai.targetLowPrice,
            ai.ebitdaMargins,
            ai.revenueGrowth,
            ai.marketCap,
            ai.totalDebt,
            ai.totalRevenue,
            ai.enterpriseValue,
            yt.ISIN
    """    


def make_query(perf_table = '', index = '', value = 1, q=1, q_ext="", conn=None):

    exclude_list = ['ticker']
    _opened_conn = None  # track connections opened here so we can close them
    if conn is None:
        # Prefer the new _-suffixed DB (post-rename migration); fall back to the
        # old name so the function still works on un-migrated installations.
        import os
        new_fname = f"{perf_table}_.db"
        old_fname = f"{perf_table}.db"
        new_path = tools.Tools().get_path(path='database', file_name=new_fname)
        old_path = tools.Tools().get_path(path='database', file_name=old_fname)
        db_file = new_path if os.path.exists(new_path) else old_path
        performance_conn = sqlite3.connect(db_file)
        _opened_conn = performance_conn
    else:
        # When an existing connection is provided it typically has performance_db
        # attached.  PRAGMA table_info() (without schema prefix) searches all
        # attached schemas, so the columns are read from the live attached DB —
        # guaranteed to match the SQL that will run on the same connection.
        performance_conn = conn
    def get_existing_columns(conn, table_name):
        """Liest Spaltennamen einer Tabelle aus."""
        cursor = conn.execute(f"PRAGMA table_info({table_name});")
        return {col[1] for col in cursor.fetchall()}

    ap_field_list = ""
    flds = get_existing_columns(performance_conn, perf_table)
    for f in flds:
        if not f in exclude_list:
            ap_field_list = f"ap.{f},\n{ap_field_list}"
    if len(ap_field_list) >2:
        ap_field_list = ap_field_list[:-2]
    if _opened_conn is not None:
        _opened_conn.close()


    if not index == '':
            q_ext = f"""
                WHERE i.name LIKE "{index}"
                {q_ext}"""
#                WHERE yt.{index} = "{value}" OR  yt.{index} = {value}

    # Nur Komma einfuegen wenn ap_field_list nicht leer ist -- sonst entsteht
    # ein haengender Komma im SELECT der zu einem SQL-Syntaxfehler fuehrt.
    fl = f"{field_list},{ap_field_list}" if ap_field_list else field_list

    if q == 1:
        query = f"""
            SELECT 
            {fl}
            , i.name as index_name
            FROM stocks AS yt
            JOIN stock_indices si ON yt.id = si.stock_id
            JOIN indices i ON si.index_id = i.id
            INNER JOIN (
                SELECT ap1.*
                FROM performance_db.{perf_table} ap1
                JOIN (
                    SELECT ticker, MAX(date) AS max_date
                    FROM performance_db.{perf_table}
                    GROUP BY ticker
                ) ap2 
                ON ap1.ticker = ap2.ticker AND ap1.Date = ap2.max_date
            ) AS ap ON yt.Ticker = ap.ticker
            INNER JOIN info_db.asset_info AS ai ON yt.Ticker = ai.ticker
            {q_ext}
        """

    if q == 2:
        query = f"""
            SELECT
            {fl}
            , i.name as index_name
            FROM stocks AS yt
            JOIN stock_indices si ON yt.id = si.stock_id
            JOIN indices i ON si.index_id = i.id
            INNER JOIN (
                SELECT *
                FROM performance_db.{perf_table} AS ap
                INNER JOIN (
                    SELECT ticker, MAX(Date) AS max_date
                    FROM performance_db.{perf_table}
                    GROUP BY ticker
                ) latest
                ON ap.ticker = latest.ticker AND ap.Date = latest.max_date
            ) AS ap
            ON yt.Ticker = ap.ticker
            INNER JOIN info_db.asset_info AS ai ON yt.Ticker = ai.ticker
            {q_ext}
            """
    
    if q == 3:
        query = f"""    
            SELECT     
            {fl}
            , i.name as index_name
            FROM stocks AS yt
            JOIN stock_indices si ON yt.id = si.stock_id
            JOIN indices i ON si.index_id = i.id
            INNER JOIN performance_db.{perf_table} AS ap ON yt.Ticker = ap.ticker
            INNER JOIN info_db.asset_info AS ai ON yt.Ticker = ai.ticker
            {q_ext}
            """

    if q == 4:
        query = f"""    
            SELECT     
            *
            FROM indices as yt 
            INNER JOIN info_db.asset_info AS ai ON yt.name = ai.ticker
            """
#            {q_ext}

    if q == 5:
        query = f"""    
            SELECT     
            *
            FROM info_db.asset_info WHERE ticker = "{index}"
            """

#    if q == 6:
#        query = f"""
#        SELECT yt.Ticker AS ticker FROM stocks AS yt JOIN stock_indices si ON yt.id = si.stock_id JOIN indices i ON si.index_id = i.id WHERE i.name = "{index}";
#        """

    if q == 7:
        query = f"""
            SELECT
            ai.ticker,
            ai.longName,
            ai.shortName
            FROM stocks AS yt
            JOIN stock_indices si ON yt.id = si.stock_id
            JOIN indices i ON si.index_id = i.id
            INNER JOIN info_db.asset_info AS ai ON yt.Ticker = ai.ticker
            WHERE i.name = "{index}"
        """

    if q == 6:
        query = f"""
            SELECT
            ai.ticker,
            ai.longName,
            ai.shortName,
            ap.Close,
            i.name as index_name
            FROM stocks AS yt
            JOIN stock_indices si ON yt.id = si.stock_id
            JOIN indices i ON si.index_id = i.id
            INNER JOIN info_db.asset_info AS ai ON yt.Ticker = ai.ticker
            INNER JOIN performance_db.{perf_table} AS ap ON yt.Ticker = ap.ticker
            WHERE i.name = "{index}"
        """
    return query



