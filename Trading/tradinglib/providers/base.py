"""Abstract base class for market data providers."""
from abc import ABC, abstractmethod
from typing import Optional, Union, List

import pandas as pd


class MarketDataProvider(ABC):
    """Minimal interface every provider must implement.

    All methods return a pandas DataFrame shaped like yfinance output so that
    existing callers in market_data.py need no changes.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string, e.g. 'yahoo' or 'fmp'."""

    @property
    def available(self) -> bool:
        """Return False when the provider cannot be used (missing library / key)."""
        return True

    @abstractmethod
    def download(
        self,
        tickers: Union[str, List[str]],
        start: Optional[str] = None,
        end: Optional[str] = None,
        period: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data for one or more tickers.

        Returns a DataFrame with columns Open/High/Low/Close/Volume (and
        optionally Adj Close).  For a single ticker the index is DatetimeIndex;
        for multiple tickers the columns are a MultiIndex (field, ticker).
        """

    @abstractmethod
    def ticker_history(
        self,
        ticker: str,
        period: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV history for a single ticker (yf.Ticker.history equivalent)."""
