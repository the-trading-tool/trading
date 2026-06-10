"""Financial Modeling Prep (FMP) data provider.

Docs: https://financialmodelingprep.com/developer/docs

Ticker compatibility with Yahoo Finance:
  - US equities:   identical  (AAPL → AAPL)
  - German stocks: .DE → .F   (SAP.DE → SAP.F)
  - Crypto:        BTC-USD → BTCUSD
  - Indices:       identical  (^GDAXI, ^TNX, ^SPX)
  - Other exchanges: usually identical; override via fmp_ticker_overrides in config.

Rate limits (free tier): 250 req/day, 10 req/min.
"""
import datetime as dt
import logging
from typing import Optional, Union, List, Dict

import pandas as pd

from tradinglib.providers.base import MarketDataProvider

logger = logging.getLogger(__name__)

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except Exception:
    _requests = None
    _REQUESTS_AVAILABLE = False

_BASE_URL = "https://financialmodelingprep.com/api"

# Yahoo exchange suffix → FMP exchange suffix for known differences.
_SUFFIX_MAP: Dict[str, str] = {
    ".DE": ".F",   # XETRA/Frankfurt
    ".MU": ".MU",  # Munich (same)
    ".BE": ".BE",  # Berlin (same)
}

# Yahoo period string → approximate days for date-range calculation.
_PERIOD_DAYS: Dict[str, int] = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 92, "6mo": 183,
    "1y": 365, "2y": 730, "5y": 1826, "10y": 3652,
    "ytd": 365, "max": 365 * 30,
}

# Yahoo intraday interval → FMP chart endpoint segment.
_INTRADAY_MAP: Dict[str, str] = {
    "1m": "1min", "2m": "5min", "5m": "5min",
    "15m": "15min", "30m": "30min",
    "60m": "1hour", "1h": "1hour",
}


def _map_ticker(yahoo_ticker: str, overrides: Optional[Dict[str, str]] = None) -> str:
    """Convert a Yahoo-style ticker to FMP format."""
    if overrides and yahoo_ticker in overrides:
        return overrides[yahoo_ticker]
    # Crypto: BTC-USD → BTCUSD
    if yahoo_ticker.endswith("-USD") and "-" in yahoo_ticker:
        return yahoo_ticker.replace("-USD", "USD")
    # Exchange suffix remapping
    for yf_sfx, fmp_sfx in _SUFFIX_MAP.items():
        if yahoo_ticker.endswith(yf_sfx):
            return yahoo_ticker[: -len(yf_sfx)] + fmp_sfx
    return yahoo_ticker


def _period_to_dates(period: Optional[str], start: Optional[str], end: Optional[str]):
    """Return (from_str, to_str) as YYYY-MM-DD strings."""
    today = dt.date.today()
    to_date = dt.date.fromisoformat(end) if end else today
    if start:
        from_date = dt.date.fromisoformat(start)
    else:
        days = _PERIOD_DAYS.get(period or "1y", 365)
        from_date = today - dt.timedelta(days=days)
    return from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d")


def _to_dataframe(historical: list, ticker: str) -> pd.DataFrame:
    """Convert FMP historical list to a yfinance-shaped DataFrame."""
    if not historical:
        return pd.DataFrame()
    df = pd.DataFrame(historical)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    rename = {
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume", "adjClose": "Adj Close",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume", "Adj Close"] if c in df.columns]
    df = df[keep]
    if "Adj Close" not in df.columns and "Close" in df.columns:
        df["Adj Close"] = df["Close"]
    return df


class FMPProvider(MarketDataProvider):
    def __init__(self, api_key: str, overrides: Optional[Dict[str, str]] = None):
        self._api_key = api_key or ""
        self._overrides = overrides or {}

    @property
    def name(self) -> str:
        return "fmp"

    @property
    def available(self) -> bool:
        return _REQUESTS_AVAILABLE and bool(self._api_key)

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        if not _REQUESTS_AVAILABLE:
            raise RuntimeError("requests library not installed")
        p = dict(params or {})
        p["apikey"] = self._api_key
        resp = _requests.get(f"{_BASE_URL}{path}", params=p, timeout=15)
        if resp.status_code == 429:
            logger.warning("FMP rate limit reached (429)")
            return {}
        resp.raise_for_status()
        return resp.json()

    def _fetch_single(
        self,
        yahoo_ticker: str,
        from_str: str,
        to_str: str,
        interval: Optional[str],
    ) -> pd.DataFrame:
        fmp_ticker = _map_ticker(yahoo_ticker, self._overrides)
        try:
            intraday_seg = _INTRADAY_MAP.get(interval or "")
            if intraday_seg:
                data = self._get(
                    f"/v3/historical-chart/{intraday_seg}/{fmp_ticker}",
                    {"from": from_str, "to": to_str},
                )
                return _to_dataframe(data if isinstance(data, list) else [], yahoo_ticker)
            else:
                data = self._get(
                    f"/v3/historical-price-full/{fmp_ticker}",
                    {"from": from_str, "to": to_str},
                )
                historical = data.get("historical", []) if isinstance(data, dict) else []
                return _to_dataframe(historical, yahoo_ticker)
        except Exception as e:
            logger.warning("FMPProvider fetch failed for %s: %s", fmp_ticker, e)
            return pd.DataFrame()

    def download(
        self,
        tickers: Union[str, List[str]],
        start: Optional[str] = None,
        end: Optional[str] = None,
        period: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> pd.DataFrame:
        if not self.available:
            logger.warning("FMPProvider not available (missing key or requests lib)")
            return pd.DataFrame()

        from_str, to_str = _period_to_dates(period, start, end)
        ticker_list = [tickers] if isinstance(tickers, str) else list(tickers)

        frames: Dict[str, pd.DataFrame] = {}
        for tk in ticker_list:
            df = self._fetch_single(tk, from_str, to_str, interval)
            if not df.empty:
                frames[tk] = df

        if not frames:
            return pd.DataFrame()

        if len(frames) == 1:
            return next(iter(frames.values()))

        # Multi-ticker: build MultiIndex columns like yfinance
        pieces = []
        for tk, df in frames.items():
            df.columns = pd.MultiIndex.from_product([df.columns, [tk]])
            pieces.append(df)
        return pd.concat(pieces, axis=1).sort_index()

    def ticker_history(
        self,
        ticker: str,
        period: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> pd.DataFrame:
        from_str, to_str = _period_to_dates(period, None, None)
        return self._fetch_single(ticker, from_str, to_str, interval)

    def test_connection(self) -> bool:
        """Quick connectivity check — returns True when the API key is valid."""
        try:
            data = self._get("/v3/profile/AAPL")
            return isinstance(data, list) and len(data) > 0
        except Exception:
            return False
