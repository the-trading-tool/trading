"""Yahoo Finance provider — thin wrapper around yfinance."""
import logging
import threading
from typing import Optional, Union, List

import pandas as pd

from tradinglib.providers.base import MarketDataProvider

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except Exception:
    yf = None
    _YF_AVAILABLE = False

# yf.download() sammelt Ergebnisse im modul-globalen yfinance.shared._DFS und
# leert es bei jedem Aufruf — parallele Aufrufe überschreiben sich gegenseitig
# (Thread A bekommt Daten von Thread B). Daher den Aufruf serialisieren.
_YF_DOWNLOAD_LOCK = threading.Lock()


class YahooProvider(MarketDataProvider):
    @property
    def name(self) -> str:
        return "yahoo"

    @property
    def available(self) -> bool:
        return _YF_AVAILABLE

    def download(
        self,
        tickers: Union[str, List[str]],
        start: Optional[str] = None,
        end: Optional[str] = None,
        period: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> pd.DataFrame:
        if not _YF_AVAILABLE:
            return pd.DataFrame()
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        call_interval = interval or "1d"
        try:
            with _YF_DOWNLOAD_LOCK:
                return yf.download(
                    tickers=tickers,
                    start=start,
                    end=end,
                    period=period,
                    interval=call_interval,
                    actions=False,
                    progress=False,
                    auto_adjust=False,
                    prepost=True,
                )
        except Exception as e:
            logger.exception("YahooProvider.download failed for %s: %s", tickers, e)
            return pd.DataFrame()

    def ticker_history(
        self,
        ticker: str,
        period: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> pd.DataFrame:
        if not _YF_AVAILABLE:
            return pd.DataFrame()
        try:
            return yf.Ticker(ticker).history(period=period, interval=interval)
        except Exception as e:
            logger.exception("YahooProvider.ticker_history failed for %s: %s", ticker, e)
            return pd.DataFrame()
