import sqlite3
import os
from io import BytesIO
import numpy as np
import pandas as pd
from typing import Iterable
import json
from datetime import datetime
import logging
from tradinglib.tools import open_db

logger = logging.getLogger(__name__)

_XRATE_CACHE_MAX = 256


def _xrate_put(cache: dict, key, value) -> None:
    """Bounded insert for _xrate_cache — evicts oldest entry when full."""
    if len(cache) >= _XRATE_CACHE_MAX:
        del cache[next(iter(cache))]
    cache[key] = value


class DataUtils():
    """Utility helpers consolidated from multiple modules.

    Provides helpers for common small tasks used across the codebase:
    - table name mapping for intervals
    - safe num_rows extraction
    - binary excel export
    - efficient save of OHLC pandas DataFrame to sqlite using executemany
    """

    @staticmethod
    def get_table_name(interval: str) -> str:
        """Map a yfinance interval string to the corresponding SQLite table name (e.g. '1d' → 'day_data')."""
        mapping = {
            "1m": "min",
            "60m": "h60",
            "1h": "h60",
            "1d": "day",
            "1wk": "week",
            "1mo": "month",
            "max": "max",
        }
        key = interval
        # try fallback normalizations
        if interval in mapping:
            key = interval
        elif interval.lower() in mapping:
            key = interval.lower()

        tbl = mapping.get(key, None)
        if tbl is None:
            # Mehrstuendige Intervalle ('2h', '4h', ...) werden aus den
            # Stundendaten resampled -- ohne diesen Zweig lieferte der
            # Rest-Fallback unten 'h_data', eine Tabelle die es nicht gibt.
            low = str(interval).lower()
            if low.endswith('h') and low[:-1].isdigit():
                tbl = 'h60'
        if tbl is None:
            # best-effort fallback: use the part after digits
            tbl = ''.join([c for c in interval if not c.isdigit()]) or interval

        return f"{tbl}_data"

    # simple in-memory cache for exchange rates to avoid duplicate yfinance calls
    _xrate_cache: dict = {}

    @staticmethod
    def _is_currency_code(c) -> bool:
        """True nur für plausible ISO-artige Währungscodes (alphabetisch, 2–5 Zeichen).

        Filtert die Sentinels 0/'0'/''/None (fehlende currency-Spalte) heraus, die
        sonst zu unsinnigen FX-Tickern wie 'EUR0=X' führen. 'GBp' (Pence) passt, da
        weiterhin rein alphabetisch.
        """
        return isinstance(c, str) and c.strip().isalpha() and 2 <= len(c.strip()) <= 5

    @staticmethod
    def normalize_currency(code):
        """Map minor-unit quote currencies to their major unit: return (major_code, factor).

        Yahoo quotes some listings in minor units (LSE: 'GBp'/'GBX' = pence,
        JSE: 'ZAc' = SA cents, TASE: 'ILA' = agorot). price_major = price / factor.
        Regular codes are returned uppercased with factor 1.0.
        """
        if not isinstance(code, str) or not code.strip():
            return code, 1.0
        c = code.strip()
        cu = c.upper()
        # 'GBp' is case-sensitive vs the major 'GBP'; 'GBX'/'ZAC'/'ILA' have no major-unit collision
        if c == 'GBp' or cu == 'GBX':
            return 'GBP', 100.0
        if c == 'ZAc' or cu == 'ZAC':
            return 'ZAR', 100.0
        if cu == 'ILA':
            return 'ILS', 100.0
        return cu, 1.0

    @classmethod
    def get_exchange_rate(cls, symbol: str = 'EUR', system_currency: str = 'EUR', period: str = '1d', interval: str = '1m') -> float:
        """Return exchange rate for symbol->system_currency using yfinance with a small in-memory cache.

        Convention: rate = units of *symbol* per 1 *system_currency* (Yahoo ticker
        ``{SYS}{SYM}=X``), i.e. price_system = price_symbol / rate.
        Minor-unit currencies (GBp/GBX pence, ...) are converted via their major
        unit: rate('GBp') = rate('GBP') * 100.

        Kept intentionally minimal: returns 1 on any failure to avoid exceptions bubbling.
        """
        if symbol == system_currency:
            return 1.0
        # Pence & Co. auf die Hauptwährung abbilden (GBp -> GBP * 100)
        major, factor = cls.normalize_currency(symbol)
        if factor != 1.0:
            return cls.get_exchange_rate(symbol=major, system_currency=system_currency,
                                         period=period, interval=interval) * factor
        # Ungültiges Währungs-Sentinel (0/'0'/''/None) NICHT in einen FX-Ticker
        # bauen — sonst entstehen Yahoo-Fehlabfragen wie "EUR0=X" (404). Ohne
        # gültigen Code gibt es keine Umrechnung -> Faktor 1.0.
        if not cls._is_currency_code(symbol) or not cls._is_currency_code(system_currency):
            return 1.0

        key = (symbol, system_currency, period, interval)
        if key in cls._xrate_cache:
            return cls._xrate_cache[key]

        try:
            # use centralized market data wrapper to keep all downloads consistent
            from tradinglib import market_data as md
            df = md.ticker_history(f"{system_currency}{symbol}=X", period=period, interval=interval)
            if not df.empty:
                val = float(df['Close'].iloc[-1])
                _xrate_put(cls._xrate_cache, key, val)
                return val
        except Exception:
            pass
        return 1.0

    @classmethod
    def get_exchange_rate_for_date(cls, date_obj, currency: str, local_currency: str = 'EUR') -> float:
        """Fetch an exchange rate for a specific date (date_obj may be date or datetime).

        This downloads a small window around the requested date and picks the Close
        value for the requested day. Results are cached in-memory by (currency, local_currency, date).
        Returns 1.0 on failure.

        Convention (inverse of get_exchange_rate!): rate = units of *local_currency*
        per 1 *currency* (Yahoo ticker ``{CUR}{LOCAL}=X``), i.e. amount_local =
        amount_currency * rate. Minor units: rate('GBp') = rate('GBP') / 100.
        """
        if currency == local_currency:
            return 1.0
        # Pence & Co. auf die Hauptwährung abbilden (GBp -> GBP / 100)
        major, factor = cls.normalize_currency(currency)
        if factor != 1.0:
            return cls.get_exchange_rate_for_date(date_obj, major, local_currency) / factor
        # Siehe get_exchange_rate: ungültiges Sentinel -> keine Umrechnung,
        # kein "0EUR=X"/"EUR0=X"-Fehlabruf.
        if not cls._is_currency_code(currency) or not cls._is_currency_code(local_currency):
            return 1.0
        try:
            # normalize date
            if hasattr(date_obj, 'date'):
                d = date_obj.date()
            else:
                d = date_obj

            key = (currency, local_currency, d.isoformat())
            # in-memory cache
            if key in cls._xrate_cache:
                return cls._xrate_cache[key]

            # check persistent sqlite cache
            try:
                db_path = cls._get_xrate_db_path()
                conn = open_db(db_path)
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS exchange_rates (
                        currency TEXT,
                        local_currency TEXT,
                        date TEXT,
                        rate REAL,
                        ts TEXT,
                        PRIMARY KEY(currency, local_currency, date)
                    )
                """)
                cur.execute("SELECT rate FROM exchange_rates WHERE currency = ? AND local_currency = ? AND date = ?",
                            (currency, local_currency, d.isoformat()))
                row = cur.fetchone()
                if row:
                    _xrate_put(cls._xrate_cache, key, float(row[0]))
                    conn.close()
                    return cls._xrate_cache[key]
                conn.close()
            except Exception:
                # If DB access fails, fall back to network fetch
                pass

            from tradinglib import market_data as md
            from datetime import timedelta

            start_date = d - timedelta(days=7)
            end_date = d + timedelta(days=1)

            ticker = f"{currency}{local_currency}=X"
            df = md.download(tickers=ticker, start=start_date, end=end_date, actions=False, progress=False, auto_adjust=False, prepost=True, force_remote=True)
            if df is None or df.empty:
                return 1.0

            # try to pick value for the exact calendar date
            try:
                # df.index may be Timestamp with tz — compare dates
                val = float(df.loc[df.index.date == d, 'Close'].iloc[-1])
            except Exception:
                try:
                    val = float(df['Close'].iloc[-1])
                except Exception:
                    return 1.0

            # store in-memory and persistent cache
            _xrate_put(cls._xrate_cache, key, val)
            try:
                db_path = cls._get_xrate_db_path()
                conn = open_db(db_path)
                cur = conn.cursor()
                cur.execute("INSERT OR REPLACE INTO exchange_rates (currency, local_currency, date, rate, ts) VALUES (?, ?, ?, ?, datetime('now'))",
                            (currency, local_currency, d.isoformat(), val))
                conn.commit()
                conn.close()
            except Exception:
                pass

            return val
        except Exception:
            return 1.0

    @classmethod
    def _get_xrate_db_path(cls) -> str:
        """Return path to sqlite file used for persistent xrate caching, create dir if needed."""
        from tradinglib import tools
        base = tools.Db_tools().get_path()
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        return os.path.join(base, 'exchange_rates.db')

    @staticmethod
    def get_num_rows(df) -> int:
        """Return the number of rows in df, or 0 on any error (e.g. None input)."""
        try:
            return len(df.index)
        except Exception:
            return 0

    @staticmethod
    def normalize_index_to_utc_naive(obj):
        """Backwards-compatible wrapper for normalize_index_to_tz_naive with tz='UTC'."""
        return DataUtils.normalize_index_to_tz_naive(obj, tz='UTC')

    @staticmethod
    def normalize_index_to_tz_naive(obj, tz: str = 'UTC'):
        """Normalize a pandas Index/Series/DataFrame index to tz-naive in given tz.

        Accepts a pandas Series/DataFrame or an Index. If given a Series/DataFrame,
        the function will modify its index in-place and return the object. If given an
        Index, a normalized Index is returned.

        Behavior:
        - If index is tz-aware, convert to the requested tz then drop tz info.
        - If index is tz-naive, coerce to pandas datetime if possible.
        - On failure, returns the original object/index unchanged.
        """
        try:
            # determine input index
            if hasattr(obj, 'index'):
                idx = obj.index
                is_container = True
            else:
                idx = obj
                is_container = False

            # attempt to coerce to datetime index
            # First, try to detect if the index is already datetime-like.
            # If not, attempt conversion but if conversion yields only NaT values
            # we assume the index is not datetime-like and return the original.
            from pandas.api import types as ptypes

            idx_dt = pd.to_datetime(idx, errors='coerce')
            # if conversion produced only NaT for non-datetime types, return original
            if not (ptypes.is_datetime64_any_dtype(idx) or isinstance(idx, pd.DatetimeIndex)):
                try:
                    # idx_dt may be an Index/Series — check if all entries are NaT
                    all_nat = False
                    if hasattr(idx_dt, 'isna'):
                        all_nat = idx_dt.isna().all()
                    if all_nat:
                        # nothing parseable as datetime — return original untouched
                        return obj if is_container else idx
                except Exception:
                    # if anything unexpected happens, fall back to original
                    return obj if is_container else idx

            # If index contains tz information (tz-aware), convert to requested tz then drop tz
            tzinfo = getattr(idx_dt, 'tz', None)
            if tzinfo is not None:
                try:
                    idx_dt = idx_dt.tz_convert(tz).tz_localize(None)
                except Exception:
                    # Try alternative approach: localize then convert then drop
                    try:
                        idx_dt = idx_dt.tz_localize(tz).tz_convert(tz).tz_localize(None)
                    except Exception:
                        pass

            # assign back for containers
            if is_container:
                try:
                    obj.index = idx_dt
                    return obj
                except Exception:
                    return obj
            else:
                return idx_dt
        except Exception as e:
            logger.exception(f'normalize_index_to_tz_naive failed: {e}')
            return obj

    @staticmethod
    def convert_utc_naive_to_tz(idx, tz: str = 'Europe/Berlin'):
        """Convert a tz-naive datetime Index assumed to be UTC into tz-naive local time in `tz`.

        Used to display timestamps (which are stored/normalized as UTC-naive) in the
        exchange's local timezone, e.g. Xetra daily candles dated 00:00 Europe/Berlin.
        Returns the index unchanged if it is not datetime-like or conversion fails.
        """
        try:
            idx_dt = pd.to_datetime(idx, errors='coerce')
            if not isinstance(idx_dt, pd.DatetimeIndex):
                return idx
            if idx_dt.tz is None:
                idx_dt = idx_dt.tz_localize('UTC')
            return idx_dt.tz_convert(tz).tz_localize(None)
        except Exception:
            logger.exception('convert_utc_naive_to_tz failed')
            return idx

    @staticmethod
    def get_bin_excel_data(data: pd.DataFrame, tbl: str = 'results') -> BytesIO:
        """Serialize a DataFrame to an in-memory Excel file and return a BytesIO object."""
        output = BytesIO()
        df = data.copy()
        try:
            df = df.reset_index(drop=True)
        except Exception:
            pass

        writer = pd.ExcelWriter(output, engine='openpyxl')
        df.to_excel(writer, sheet_name=tbl)
        writer.close()
        output.seek(0)
        return output

    @staticmethod
    def ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure the DataFrame has a DatetimeIndex.

        - If df is None or empty, returns it unchanged.
        - If a 'Date' column exists, it will be set as the index (preserving the column).
        - The index will be coerced to datetime and any NaT rows dropped.
        - The index is sorted ascending so value-based slicing (df.loc[a:b])
          works — pandas rejects partial slicing on a non-monotonic DatetimeIndex.
        """
        try:
            if df is None:
                return df
            if getattr(df, 'empty', False):
                return df
            if not isinstance(df.index, pd.DatetimeIndex):
                if 'Date' in df.columns:
                    df = df.set_index('Date', drop=False)
                df.index = pd.to_datetime(df.index, errors='coerce')
            # drop invalid timestamps
            df = df[~df.index.isna()]
            # ensure monotonic index for value-based slicing (df.loc[start:end])
            if not df.index.is_monotonic_increasing:
                df = df.sort_index()
            return df
        except Exception:
            return df

    @staticmethod
    def safe_last(obj, col=None, default=0):
        """Return the last element safely.

        - If obj is a DataFrame and `col` is provided, returns the last value for that column or default.
        - If obj is a Series and `col` is None, returns the last value or default.

        numpy scalars are converted to native Python types via .item(): numpy.int64
        does not subclass int, so sqlite3 stores it as an 8-byte BLOB instead of an
        INTEGER, which later crashes st.dataframe's astype("string"). Returning a
        native int/float keeps persisted values typed correctly.
        """
        try:
            if col is not None:
                if obj is None or getattr(obj, 'empty', True):
                    return default
                if col not in obj.columns:
                    return default
                val = obj[col].iat[-1]
            else:
                # assume Series-like
                if obj is None or len(obj) == 0:
                    return default
                val = obj.iat[-1]
            return val.item() if isinstance(val, np.generic) else val
        except Exception:
            return default

    @staticmethod
    def safe_first(obj, col=None, default=0):
        """Return the first element safely (similar to safe_last)."""
        try:
            if col is not None:
                if obj is None or getattr(obj, 'empty', True):
                    return default
                if col not in obj.columns:
                    return default
                return obj[col].iat[0]
            else:
                if obj is None or len(obj) == 0:
                    return default
                return obj.iat[0]
        except Exception:
            return default

    @staticmethod
    def safe_nth_last(series, n=1, default=0):
        """Return the nth-from-last element from a Series safely.

        n=1 -> last element, n=2 -> second last, etc.
        """
        try:
            if series is None or len(series) < n:
                return default
            return series.iat[-n]
        except Exception:
            return default

    @staticmethod
    def get_last_index_str(df, fmt: str = '%Y-%m-%d %H:%M:%S', default='') -> str:
        """Return the last index value of df formatted as a string, or default on any failure."""
        try:
            if df is None or getattr(df, 'empty', True):
                return default
            idx = df.index[-1]
            if hasattr(idx, 'strftime'):
                return idx.strftime(fmt)
            return str(idx)
        except Exception:
            return default

    @staticmethod
    def save_ohlc_to_sql(conn: sqlite3.Connection, table_name: str, df: pd.DataFrame, date_format: str = '%Y-%m-%d %H:%M:%S') -> None:
        """Save DataFrame with OHLCV-like columns to sqlite using a single executemany call.

        Expects columns Date (or index), Open, High, Low, Close, Volume. Missing columns will be filled with None.
        """
        cur = conn.cursor()

        # Ensure date column exists
        working = df.copy()
        if 'Date' not in working.columns:
            # assume index contains the date
            working = working.reset_index()
            # rename first column if it's unnamed or if yfinance used 'Datetime' (intraday)
            for _alias in ('index', 'Datetime'):
                if _alias in working.columns and 'Date' not in working.columns:
                    working = working.rename(columns={_alias: 'Date'})
                    break

        # normalize column names
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col not in working.columns:
                working[col] = None

        # create table if not exists
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                Date TEXT PRIMARY KEY,
                Open REAL,
                High REAL,
                Low REAL,
                Close REAL,
                Volume INTEGER
            )
        """)

        # Build rows for executemany
        rows = []
        for _, r in working.iterrows():
            date_val = r.get('Date')
            try:
                if hasattr(date_val, 'strftime'):
                    date_val = date_val.strftime(date_format)
            except Exception:
                pass

            rows.append((date_val, r.get('Open'), r.get('High'), r.get('Low'), r.get('Close'), r.get('Volume')))

        # Bulk insert
        cur.executemany(f"INSERT OR REPLACE INTO {table_name} (Date, Open, High, Low, Close, Volume) VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()

    @staticmethod
    def _infer_column_type(rows: list[dict], col: str) -> str:
        """Return the SQLite column type for col based on the first non-None value.

        TEXT affinity would store numbers as strings — pd.read_sql then returns
        object dtype and arithmetic (e.g. div) crashes with 'str'/'int'.
        Therefore create numeric values as REAL.
        """
        for r in rows:
            v = r.get(col)
            if v is None:
                continue
            if isinstance(v, bool):
                return "INTEGER"
            if isinstance(v, (int, float)):
                return "REAL"
            return "TEXT"
        return "TEXT"

    @staticmethod
    def bulk_upsert_dicts(conn: sqlite3.Connection, table_name: str, rows: list[dict]) -> None:
        """Insert or replace a list of dict-like rows into a table using executemany.

        - rows: list of dicts mapping column_name -> value
        - will ensure the table exists and add missing columns (type inferred
          from the row values: REAL/INTEGER for numbers, sonst TEXT)
        - stores lists/dicts as JSON strings
        """
        if not rows:
            return

        cur = conn.cursor()

        # determine superset of columns (preserve first-seen order)
        superset_cols = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    superset_cols.append(k)

        # ensure table exists with timestamp and any existing columns
        cur.execute(f"PRAGMA table_info('{table_name}')")
        existing = {row[1] for row in cur.fetchall()}
        if not existing:
            # First key is set as UNIQUE PRIMARY KEY so that
            # INSERT OR REPLACE shows true upsert behaviour (no duplicates).
            first_col = superset_cols[0] if superset_cols else None
            cols_def_parts = []
            for c in superset_cols:
                if c == first_col:
                    cols_def_parts.append(f"{c} TEXT PRIMARY KEY")
                else:
                    cols_def_parts.append(f"{c} {DataUtils._infer_column_type(rows, c)}")
            cols_def = ', '.join(cols_def_parts)
            cur.execute(f"CREATE TABLE IF NOT EXISTS {table_name} (timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, {cols_def})")
            existing = set(superset_cols) | {'timestamp'}
        else:
            # add any missing columns
            for c in superset_cols:
                if c not in existing:
                    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {c} {DataUtils._infer_column_type(rows, c)}")
                    existing.add(c)

        # prepare executemany
        placeholders = ', '.join(['?'] * (len(superset_cols) + 1))
        cols_sql = ', '.join(superset_cols)

        # Echtes Upsert statt INSERT OR REPLACE.
        #
        # INSERT OR REPLACE loescht die bestehende Zeile und legt sie neu an —
        # jede Spalte, die im aktuellen Batch fehlt, faellt dabei auf NULL
        # zurueck. Bei asset_info (259 Spalten) war das ein Datenverlust bei
        # jedem Lauf: Yahoo laesst fehlende Felder einfach weg (JEEP.DE liefert
        # 66 Schluessel ohne longName, SAP.DE 170 mit), also enthaelt der Batch
        # nie alle Spalten — und der woechentliche get_asset_info-Lauf hat
        # zuvor geholte Namen, Sektoren und Waehrungen wieder ausgeloescht.
        #
        # ON CONFLICT ... DO UPDATE setzt ausschliesslich die geliefeten
        # Spalten; alles andere bleibt stehen. Fehlt der Tabelle ein
        # eindeutiger Schluessel, bleibt es beim alten Verhalten.
        pk_cols = [r[1] for r in sorted(
            (r for r in cur.execute(f"PRAGMA table_info('{table_name}')") if r[5]),
            key=lambda r: r[5])]

        if pk_cols and all(c in superset_cols for c in pk_cols):
            update_cols = ['timestamp'] + [c for c in superset_cols if c not in pk_cols]
            set_sql = ', '.join(f"{c}=excluded.{c}" for c in update_cols)
            sql = (f"INSERT INTO {table_name} (timestamp, {cols_sql}) "
                   f"VALUES ({placeholders}) "
                   f"ON CONFLICT({', '.join(pk_cols)}) DO UPDATE SET {set_sql}")
        else:
            logger.debug("bulk_upsert_dicts: %s ohne Primaerschluessel im Batch — "
                         "Fallback auf INSERT OR REPLACE", table_name)
            sql = (f"INSERT OR REPLACE INTO {table_name} (timestamp, {cols_sql}) "
                   f"VALUES ({placeholders})")

        params = []
        for r in rows:
            vals = []
            for c in superset_cols:
                v = r.get(c)
                if isinstance(v, (list, dict)):
                    v = json.dumps(v)
                vals.append(v)
            params.append([datetime.now()] + vals)

        cur.executemany(sql, params)
        conn.commit()


def display_name_sql(alias: str = '', out: str = 'longName') -> str:
    """SQL-Ausdruck fuer den Anzeigenamen eines asset_info-Datensatzes.

    **Die eine Stelle**, an der die Namensauflösung definiert ist. Jede Abfrage
    auf ``asset_info`` sollte statt eines rohen ``longName`` diesen Ausdruck
    verwenden — sonst taucht in der Oberfläche wieder ein leeres Feld oder
    'None' auf.

    Hintergrund: Yahoo laesst ``longName`` bei vielen ETFs und Futures einfach
    weg (gemessen: 549 von 8394 Zeilen ohne, davon 273 mit brauchbarem
    ``shortName``). Ein reiner ``longName``-Select zeigt dort nichts an.
    Deshalb dreistufig: longName -> shortName -> ticker, damit im schlimmsten
    Fall wenigstens das Kuerzel steht.

    Args:
        alias: Tabellen-Alias inkl. Punkt-Trennung, z. B. ``'ai'``.
        out:   Name der Ergebnisspalte.
    """
    p = f"{alias}." if alias else ""
    return (f"COALESCE(NULLIF(TRIM({p}longName), ''), "
            f"NULLIF(TRIM({p}shortName), ''), {p}ticker) AS {out}")


def get_display_name(row, name_col: str = 'longName', fallback_col: str = 'shortName',
                     ticker_col: str = 'ticker') -> str:
    """Best available display name for a ticker row (dict or DataFrame row).

    Gegenstueck zu :func:`display_name_sql` fuer bereits geladene Zeilen —
    gleiche dreistufige Kette longName -> shortName -> ticker. Der
    Ticker-Fallback fehlte hier frueher, weshalb Zeilen ohne beide Namen als
    leerer String erschienen, waehrend SQL-seitige Stellen das Kuerzel zeigten.

    Works with both dict-like objects (row.get) and pandas Series / namedtuples.
    """
    def _get(col):
        if not col:
            return ''
        try:
            v = row.get(col) if hasattr(row, 'get') else row[col]
        except Exception:
            return ''
        # NaN und der String 'None' zaehlen als leer
        if v is None or v != v:
            return ''
        v = str(v).strip()
        return '' if v.lower() in ('none', 'nan', 'null') else v

    try:
        return _get(name_col) or _get(fallback_col) or _get(ticker_col) or ''
    except Exception:
        return ''


__all__ = ["DataUtils", "get_display_name", "display_name_sql"]

