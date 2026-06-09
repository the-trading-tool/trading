# alpaca_client.py
import os
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union, Tuple
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient  # optional für Order etc.
import logging

class AlpacaData:
    def __init__(self, symbol: str, api_key: Optional[str] = None, secret_key: Optional[str] = None,
                 base_url: Optional[str] = None, data_url: Optional[str] = None):
        """Initialize the Alpaca data client for a single symbol, reading keys from env when absent."""
        self.symbol = symbol.upper()
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("APCA_API_SECRET_KEY")
        self.base_url = base_url or os.getenv("APCA_API_BASE_URL")  # z.B. paper vs live
        self.data_url = data_url or os.getenv("APCA_API_DATA_URL")
        if not (self.api_key and self.secret_key):
            raise ValueError("Bitte API Key und Secret für Alpaca angeben oder in Umgebung setzen.")

        # Initialisiere Data-Client
        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
        )

    def _resolve_period(self, period: str) -> Optional[Tuple[datetime, datetime]]:
        """Convert a period string like '6mo' or '1y' to a (start, end) datetime tuple."""
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

        if period == "ytd":
            now = datetime.now(timezone.utc)
            start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
            return start, now
        elif period == "max":
            return None  # später handled das download()

        end = datetime.now(timezone.utc) - timedelta(days=1)
        start = end - period_map[period]
        return start, end


    def _aggregate_bars(self, df: pd.DataFrame, target_interval: str) -> pd.DataFrame:
        """Resample fine-grained Alpaca bars (e.g. 1m → 15m, 1h, 1d) using standard OHLCV rules."""
        # Mapping von Zeitintervall zu Pandas-Frequenz
        freq_map = {
            "1m": "1T",
            "5m": "5T",
            "15m": "15T",
            "30m": "30T",
            "1h": "1H",
            "1d": "1D"
        }

        if target_interval not in freq_map:
            raise ValueError(f"Ungültiges Intervall: {target_interval}")

        rule = freq_map[target_interval]

        # Sicherstellen, dass der Index datetime ist
        if not pd.api.types.is_datetime64_any_dtype(df.index):
            df.index = pd.to_datetime(df.index)

        # Resampling und Aggregation (klassische OHLCV-Aggregation)
        agg = {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        }

        resampled = df.resample(rule, label="right", closed="right").agg(agg).dropna()
        return resampled

    def download(self,
                 start: Optional[Union[str, datetime]] = None,
                 end: Optional[Union[str, datetime]] = None,
                 period: Optional[str] = None,
                 interval: str = "1d") -> pd.DataFrame:
        """Download historical OHLCV bars from Alpaca with a yfinance-compatible signature."""
        # Period‐Handling
        if period and not start:
            start_dt, end_dt = self._resolve_period(period)
            start = start_dt
            end = end_dt
        # Umwandlung falls Strings
        if isinstance(start, str):
            start = pd.to_datetime(start)
        if isinstance(end, str):
            end = pd.to_datetime(end)

        # Interval → TimeFrame Mapping
        tf_map = {
            "1m": TimeFrame.Minute,
            "5m": TimeFrame.Minute,  # eventuell eigene Logik für 5m
            "15m": TimeFrame.Minute,
            "1h": TimeFrame.Hour,
            "1d": TimeFrame.Day,
            "1wk": TimeFrame.Week,
            "1mo": TimeFrame.Month
        }
        if interval not in tf_map:
            raise ValueError(f"Ungültiges Interval: {interval}")
        timeframe = tf_map[interval]

        req = StockBarsRequest(
            symbol_or_symbols=[self.symbol],
            timeframe=timeframe,
            start=start,
            end=end
        )
        bars = self.data_client.get_stock_bars(req)
        df = bars.df  # liefert DataFrame mit MultiIndex wenn mehrere Symbole
        df = df.droplevel("symbol") if "symbol" in df.index.names else df

        df.index = df.index.tz_convert("Europe/Berlin")

        # Wenn nur ein Symbol, kolonnenbereinigung:
#        if self.symbol in df.columns.get_level_values(0):
        # Optionally: Rename columns etc.
        # z. B. df.columns = ['open', 'high', 'low', 'close', 'volume']
        df = df.rename(columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        }, errors="ignore")


        # ✅ Automatische Aggregation
        if interval not in ["1m", "1Min", "minute"]:
            df = self._aggregate_bars(df, target_interval=interval)
        return df

    def _map_interval(self, interval: str):
        """Map a yfinance-style interval string to an Alpaca TimeFrame object."""
        from alpaca.data.timeframe import TimeFrame
        mapping = {
            "1m": TimeFrame.Minute,
            "5m": TimeFrame(5, "Min"),
            "15m": TimeFrame(15, "Min"),
            "30m": TimeFrame(30, "Min"),
            "1h": TimeFrame.Hour,
            "1d": TimeFrame.Day
        }
        return mapping.get(interval, TimeFrame.Minute)

    # Optional: Trading-Methoden
    def submit_order(self,
                     qty: float,
                     side: str,
                     order_type: str = "market",
                     time_in_force: str = "day",
                     symbol: Optional[str] = None):
        """Submit a market order for the given symbol via the Alpaca trading client."""
        symbol = symbol or self.symbol
        trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            base_url=self.base_url
        )
        resp = trading_client.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type=order_type,
            time_in_force=time_in_force
        )
        return resp

class AlpacaTickers:
    def __init__(self, tickers: List[str], api_key: Optional[str] = None, secret_key: Optional[str] = None,
                 base_url: Optional[str] = None, data_url: Optional[str] = None):
        """Initialize the multi-ticker Alpaca client with a list of symbols and credentials."""
        self.tickers = [t.strip().upper() for t in tickers]
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("APCA_API_SECRET_KEY")
        self.base_url = base_url or os.getenv("APCA_API_BASE_URL")
        self.data_url = data_url or os.getenv("APCA_API_DATA_URL")
        if not (self.api_key and self.secret_key):
            raise ValueError("Bitte API Key und Secret für Alpaca angeben oder in Umgebung setzen.")
        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            base_url=self.base_url,
            data_url=self.data_url
        )

    def download(self, start=None, end=None, period=None, interval: str = "1d",
                 show_progress: bool = True) -> pd.DataFrame:
        """Download bars for all tickers and combine into a MultiIndex DataFrame."""
        from tqdm import tqdm
        results = {}
        iterator = self.tickers
        if show_progress:
            iterator = tqdm(self.tickers, desc="Downloading tickers")
        for t in iterator:
            ad = AlpacaData(t,
                             api_key=self.api_key,
                             secret_key=self.secret_key,
                             base_url=self.base_url,
                             data_url=self.data_url)
            df = ad.download(start=start, end=end, period=period, interval=interval)
            results[t] = df
        # MultiIndex DataFrame erstellen
        combined = pd.concat(results, axis=1)
        return combined

