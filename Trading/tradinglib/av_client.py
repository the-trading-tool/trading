import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Optional
import sqlite3
import hashlib
import json
import time
from tqdm import tqdm
from tradinglib.tools import open_db

# -----------------------------
# 🔹 Simple SQLite Cache Layer
# -----------------------------
class AlphaVantageCache:
    """SQLite-backed cache for Alpha Vantage API responses."""

    def __init__(self, db_path="av_cache.db"):
        """Open or create the cache database at db_path."""
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Create the cache table if it doesn't exist."""
        with open_db(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    response TEXT,
                    timestamp REAL
                )
            """)

    def get(self, key: str, max_age_hours: int = 24) -> Optional[dict]:
        """Return the cached response for key if it is younger than max_age_hours, else None."""
        with open_db(self.db_path, readonly=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT response, timestamp FROM cache WHERE key=?", (key,))
            row = cur.fetchone()
            if not row:
                return None
            response, ts = row
            if (time.time() - ts) / 3600 > max_age_hours:
                return None
            return json.loads(response)

    def set(self, key: str, response: dict):
        """Store or replace the API response for key in the cache database."""
        with open_db(self.db_path) as conn:
            conn.execute("REPLACE INTO cache (key, response, timestamp) VALUES (?, ?, ?)",
                         (key, json.dumps(response), time.time()))

# -----------------------------
# 🔹 Hauptklasse: AlphaVantageData
# -----------------------------
class AlphaVantageData:
    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, symbol: str, api_key: Optional[str] = None, cache: bool = True):
        """Initialize the Alpha Vantage client for a single symbol with optional SQLite caching."""
        self.symbol = symbol.upper()
        self.api_key = api_key or os.getenv("ALPHAVANTAGE_API_KEY")
        if not self.api_key:
            raise ValueError("Bitte gib einen Alpha Vantage API Key an oder setze ALPHAVANTAGE_API_KEY in deiner Umgebung.")
        self.cache = AlphaVantageCache() if cache else None

    def _cache_key(self, function: str, params: dict) -> str:
        """Return a SHA-256 cache key derived from the function name, symbol, and params."""
        m = hashlib.sha256()
        m.update((function + self.symbol + json.dumps(params, sort_keys=True)).encode())
        return m.hexdigest()

    def _fetch(self, function: str, params: dict) -> dict:
        """Execute an Alpha Vantage API request with optional cache lookup."""
        params.update({
            "function": function,
            "symbol": self.symbol,
            "apikey": self.api_key
        })
        key = self._cache_key(function, params)
        if self.cache:
            cached = self.cache.get(key)
            if cached:
                return cached

        r = requests.get(self.BASE_URL, params=params)
        r.raise_for_status()
        data = r.json()
        if "Error Message" in data:
            raise ValueError(f"Alpha Vantage Fehler: {data['Error Message']}")
        if self.cache:
            self.cache.set(key, data)
        return data

    # -----------------------------
    # 🔸 Helper: Zeitraum aus period ableiten
    # -----------------------------
    def _resolve_period(self, period: str):
        """Convert a period string like '6mo' to a (start_date, end_date) tuple."""
        period_map = {
            "1d": timedelta(days=1),
            "5d": timedelta(days=5),
            "1mo": timedelta(days=30),
            "3mo": timedelta(days=90),
            "6mo": timedelta(days=180),
            "1y": timedelta(days=365),
            "2y": timedelta(days=730),
            "5y": timedelta(days=1825),
            "10y": timedelta(days=3650),
            "ytd": None,
            "max": None
        }
        if period not in period_map:
            raise ValueError(f"Ungültiger period-Wert: {period}")
        if period in ["ytd", "max"]:
            return period
        end = datetime.utcnow()
        start = end - period_map[period]
        return start.date(), end.date()

    # -----------------------------
    # 🔸 Helper: JSON → DataFrame
    # -----------------------------
    def _to_dataframe(self, data: dict) -> pd.DataFrame:
        """Convert an Alpha Vantage time-series JSON response to a clean OHLCV DataFrame."""
        key = [k for k in data.keys() if "Time Series" in k][0]
        ts_data = data[key]
        df = pd.DataFrame.from_dict(ts_data, orient="index", dtype=float)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df.columns = [c.split(". ")[1].capitalize() for c in df.columns]
        df = df.rename(columns={
            "Adjusted close": "Adj Close",
            "Dividend amount": "Dividends",
            "Split coefficient": "Splits"
        })
        return df

    # -----------------------------
    # 🔸 yfinance-kompatibler Download
    # -----------------------------
    def download(self, start=None, end=None, period=None, interval="1d",
                 outputsize="full", actions=False, auto_adjust=False,
                 progress=False, prepost=False) -> pd.DataFrame:
        """Download historical price data from Alpha Vantage with a yfinance-compatible signature."""

        # Period-Handling
        if period and not start:
            resolved = self._resolve_period(period)
            if isinstance(resolved, tuple):
                start, end = resolved
            elif resolved == "ytd":
                start = datetime(datetime.now().year, 1, 1)
                end = datetime.utcnow()
            elif resolved == "max":
                start, end = None, None

        # Alpha Vantage Mapping
        interval_map = {
            "1min": "TIME_SERIES_INTRADAY",
            "5min": "TIME_SERIES_INTRADAY",
            "15min": "TIME_SERIES_INTRADAY",
            "30min": "TIME_SERIES_INTRADAY",
            "60min": "TIME_SERIES_INTRADAY",
            "1d": "TIME_SERIES_DAILY_ADJUSTED",
            "1wk": "TIME_SERIES_WEEKLY_ADJUSTED",
            "1mo": "TIME_SERIES_MONTHLY_ADJUSTED"
        }

        if interval not in interval_map:
            raise ValueError(f"Ungültiges Interval: {interval}")

        function = interval_map[interval]
        params = {"outputsize": outputsize}
        if "INTRADAY" in function:
            params["interval"] = interval

        data = self._fetch(function, params)
        df = self._to_dataframe(data)

        # Filter
        if start:
            df = df[df.index >= pd.to_datetime(start)]
        if end:
            df = df[df.index <= pd.to_datetime(end)]

        # Auto Adjust
        if auto_adjust and "Adj Close" in df.columns:
            factor = df["Adj Close"] / df["Close"]
            df["Open"] *= factor
            df["High"] *= factor
            df["Low"] *= factor
            df["Close"] = df["Adj Close"]

        if not actions:
            df = df.drop(columns=[c for c in ["Dividends", "Splits"] if c in df.columns], errors="ignore")

        return df

    # -----------------------------
    # 🔸 Fundamental / Economic Data
    # -----------------------------
    def fundamentals(self, datatype="json"):
        """Return company overview fundamentals from Alpha Vantage as a pandas Series."""
        params = {"datatype": datatype}
        data = self._fetch("OVERVIEW", params)
        return pd.Series(data)

    def gdp(self):
        """Return annual global GDP data from the Alpha Vantage Macro API as a DataFrame."""
        data = self._fetch("REAL_GDP", {"interval": "annual"})
        df = pd.DataFrame(data["data"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df["value"] = df["value"].astype(float)
        return df

    def inflation(self):
        """Return US CPI inflation data from Alpha Vantage as a DataFrame."""
        data = self._fetch("INFLATION", {})
        df = pd.DataFrame(data["data"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df["value"] = df["value"].astype(float)
        return df


# -----------------------------
# 🔹 Multi-Ticker-Klasse mit Fortschrittsbalken
# -----------------------------
class AlphaVantageTickers:
    def __init__(self, tickers: List[str], api_key: Optional[str] = None, cache=True):
        """Initialize the multi-ticker Alpha Vantage client with a list of symbols."""
        self.tickers = [t.strip().upper() for t in tickers]
        self.api_key = api_key or os.getenv("ALPHAVANTAGE_API_KEY")
        self.cache = cache

    def download(self, show_progress=True, **kwargs) -> pd.DataFrame:
        """Download all tickers sequentially and combine into a MultiIndex DataFrame."""
        all_dfs = {}
        iterator = tqdm(self.tickers, desc="Downloading tickers", disable=not show_progress)
        for t in iterator:
            av = AlphaVantageData(t, api_key=self.api_key, cache=self.cache)
            df = av.download(**kwargs)
            all_dfs[t] = df
        return pd.concat(all_dfs, axis=1)
