"""EOD Historical Data (EODHD) data provider.

Docs: https://eodhd.com/financial-apis/

Ticker compatibility with Yahoo Finance:
  EODHD always uses an explicit CODE.EXCHANGE format, so every Yahoo ticker
  needs a suffix — unlike FMP, where US symbols pass through unchanged.

  - US equities:   AAPL      -> AAPL.US       (suffix added)
  - German stocks: SAP.DE    -> SAP.XETRA
  - London:        VOD.L     -> VOD.LSE
  - Indices:       ^GSPC     -> GSPC.INDX
  - Crypto:        BTC-USD   -> BTC-USD.CC
  - Forex:         EURUSD=X  -> EURUSD.FOREX
  - Most other exchange suffixes are identical (.PA, .MI, .SW, .TO, ...).
  - Futures (=F) have no reliable EODHD equivalent -> use eodhd_ticker_overrides.

Intervals: EODHD serves only 1m / 5m / 1h intraday bars. Other Yahoo intraday
intervals (2m/15m/30m) are fetched at the next finer bar size and resampled
locally, so callers get the granularity they asked for instead of a silent
substitution.

Rate limits: free plan 20 req/day; paid plans 100k req/day (EOD ~1 req/ticker).
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

_BASE_URL = "https://eodhd.com/api"

# Yahoo exchange suffix -> EODHD exchange code, only where the two differ.
# Suffixes not listed here are passed through unchanged (.PA, .MI, .SW, .AS,
# .BR, .MC, .LS, .VI, .CO, .ST, .OL, .HE, .IR, .TO, .V, .HK, .SA, .MX, .BA,
# .TW, .AT, .PR, .JK, .BK are identical on both sides).
_SUFFIX_MAP: Dict[str, str] = {
    ".DE": ".XETRA",  # Yahoo XETRA -> EODHD XETRA
    ".L": ".LSE",     # London
    ".AX": ".AU",     # Australia
    ".WA": ".WAR",    # Warsaw
    ".KS": ".KO",     # Korea
    ".SS": ".SHG",    # Shanghai
    ".SZ": ".SHE",    # Shenzhen
    ".KL": ".KLSE",   # Kuala Lumpur
    ".BD": ".BUD",    # Budapest
}

# Reverse of _SUFFIX_MAP for converting EODHD symbols back to Yahoo style.
_SUFFIX_MAP_INV: Dict[str, str] = {v: k for k, v in _SUFFIX_MAP.items()}

# Yahoo period string -> approximate days for date-range calculation.
_PERIOD_DAYS: Dict[str, int] = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 92, "6mo": 183,
    "1y": 365, "2y": 730, "5y": 1826, "10y": 3652,
    "ytd": 365, "max": 365 * 30,
}

# Yahoo interval -> EODHD EOD 'period' parameter.
_EOD_PERIOD_MAP: Dict[str, str] = {"1d": "d", "1wk": "w", "1mo": "m"}

# Yahoo intraday interval -> (EODHD interval to fetch, pandas resample rule).
# EODHD only serves 1m/5m/1h; anything else is aggregated locally.
_INTRADAY_MAP: Dict[str, tuple] = {
    "1m": ("1m", None),
    "2m": ("1m", "2min"),
    "5m": ("5m", None),
    "15m": ("5m", "15min"),
    "30m": ("5m", "30min"),
    "60m": ("1h", None),
    "1h": ("1h", None),
}

# Maximum lookback EODHD serves per intraday interval (days).
_INTRADAY_MAX_DAYS: Dict[str, int] = {"1m": 120, "5m": 600, "1h": 7200}


def _map_ticker(yahoo_ticker: str, overrides: Optional[Dict[str, str]] = None) -> str:
    """Convert a Yahoo-style ticker to EODHD's CODE.EXCHANGE format."""
    if not yahoo_ticker:
        return ""
    if overrides and yahoo_ticker in overrides:
        return overrides[yahoo_ticker]

    # Indices: ^GSPC -> GSPC.INDX
    if yahoo_ticker.startswith("^"):
        return yahoo_ticker[1:] + ".INDX"

    # Forex: EURUSD=X -> EURUSD.FOREX, JPY=X -> USDJPY.FOREX
    if yahoo_ticker.endswith("=X"):
        pair = yahoo_ticker[:-2]
        if len(pair) == 3:
            pair = "USD" + pair
        return pair + ".FOREX"

    # Crypto: BTC-USD -> BTC-USD.CC
    if "-" in yahoo_ticker and yahoo_ticker.rsplit("-", 1)[-1] in (
        "USD", "EUR", "USDT", "GBP", "JPY",
    ):
        return yahoo_ticker + ".CC"

    # Exchange suffix remapping
    for yf_sfx, eod_sfx in _SUFFIX_MAP.items():
        if yahoo_ticker.endswith(yf_sfx):
            return yahoo_ticker[: -len(yf_sfx)] + eod_sfx

    # Already carries an exchange suffix (identical on both sides) -> unchanged
    if "." in yahoo_ticker:
        return yahoo_ticker

    # Bare symbol -> US
    return yahoo_ticker + ".US"


def _eodhd_to_yahoo(code: str, exchange: str) -> str:
    """Convert an EODHD Code/Exchange pair back to a Yahoo-style ticker.

    Used for ISIN resolution, where downstream consumers (charts, market_data)
    expect Yahoo-style tickers.
    """
    code = (code or "").strip()
    exchange = (exchange or "").strip().upper()
    if not code:
        return ""
    if exchange == "INDX":
        return "^" + code
    if exchange == "FOREX":
        return code + "=X"
    if exchange == "CC":
        return code
    if exchange in ("US", "NYSE", "NASDAQ", "BATS", "AMEX", "NMFQS", "OTC", "PINK"):
        return code
    sfx = _SUFFIX_MAP_INV.get("." + exchange)
    if sfx:
        return code + sfx
    return f"{code}.{exchange}"


def _period_to_dates(period: Optional[str], start: Optional[str], end: Optional[str]):
    """Return (from_date, to_date) as date objects."""
    today = dt.date.today()
    to_date = dt.date.fromisoformat(end) if end else today
    if start:
        from_date = dt.date.fromisoformat(start)
    else:
        days = _PERIOD_DAYS.get(period or "1y", 365)
        from_date = today - dt.timedelta(days=days)
    return from_date, to_date


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate finer bars up to `rule` (e.g. 5m bars -> 15min bars)."""
    if df.empty:
        return df
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in df.columns:
        agg["Volume"] = "sum"
    try:
        out = df.resample(rule).agg(agg).dropna(subset=["Open", "High", "Low", "Close"])
    except Exception as e:
        logger.debug("EODHD resample to %s failed: %s", rule, e)
        return df
    if "Adj Close" not in out.columns and "Close" in out.columns:
        out["Adj Close"] = out["Close"]
    return out


def _to_dataframe(rows: list, intraday: bool) -> pd.DataFrame:
    """Convert an EODHD JSON response to a yfinance-shaped DataFrame."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)

    time_col = "datetime" if intraday else "date"
    if time_col not in df.columns:
        return pd.DataFrame()
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=intraday)
    df = df.dropna(subset=[time_col]).set_index(time_col).sort_index()
    df.index.name = "Date"
    if intraday and getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)

    rename = {
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume", "adjusted_close": "Adj Close",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume", "Adj Close"] if c in df.columns]
    df = df[keep]
    for col in keep:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Adj Close" not in df.columns and "Close" in df.columns:
        df["Adj Close"] = df["Close"]
    return df


class EODHDProvider(MarketDataProvider):
    def __init__(self, api_key: str, overrides: Optional[Dict[str, str]] = None):
        self._api_key = api_key or ""
        self._overrides = overrides or {}

    @property
    def name(self) -> str:
        return "eodhd"

    @property
    def available(self) -> bool:
        return _REQUESTS_AVAILABLE and bool(self._api_key)

    def _get(self, path: str, params: Optional[dict] = None):
        if not _REQUESTS_AVAILABLE:
            raise RuntimeError("requests library not installed")
        p = dict(params or {})
        p["api_token"] = self._api_key
        p.setdefault("fmt", "json")
        resp = _requests.get(f"{_BASE_URL}{path}", params=p, timeout=15)
        if resp.status_code == 401:
            logger.warning("EODHD rejected the API key (401)")
            return None
        if resp.status_code == 402:
            logger.warning("EODHD: ticker/endpoint not covered by the current plan (402)")
            return None
        if resp.status_code == 429:
            logger.warning("EODHD rate limit reached (429)")
            return None
        if resp.status_code == 404:
            logger.debug("EODHD: no data for %s (404)", path)
            return None
        resp.raise_for_status()
        return resp.json()

    def _fetch_single(
        self,
        yahoo_ticker: str,
        from_date: dt.date,
        to_date: dt.date,
        interval: Optional[str],
    ) -> pd.DataFrame:
        eod_ticker = _map_ticker(yahoo_ticker, self._overrides)
        try:
            intraday = _INTRADAY_MAP.get(interval or "")
            if intraday:
                eod_interval, resample_rule = intraday
                max_days = _INTRADAY_MAX_DAYS.get(eod_interval)
                if max_days:
                    earliest = dt.date.today() - dt.timedelta(days=max_days)
                    if from_date < earliest:
                        logger.debug(
                            "EODHD: clamping %s start %s to %s (%s limit)",
                            yahoo_ticker, from_date, earliest, eod_interval,
                        )
                        from_date = earliest
                data = self._get(
                    f"/intraday/{eod_ticker}",
                    {
                        "interval": eod_interval,
                        "from": int(dt.datetime.combine(from_date, dt.time.min).timestamp()),
                        "to": int(dt.datetime.combine(to_date, dt.time.max).timestamp()),
                    },
                )
                df = _to_dataframe(data if isinstance(data, list) else [], intraday=True)
                if resample_rule and not df.empty:
                    df = _resample_ohlcv(df, resample_rule)
                return df

            data = self._get(
                f"/eod/{eod_ticker}",
                {
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "period": _EOD_PERIOD_MAP.get(interval or "1d", "d"),
                    "order": "a",
                },
            )
            return _to_dataframe(data if isinstance(data, list) else [], intraday=False)
        except Exception as e:
            logger.warning("EODHDProvider fetch failed for %s: %s", eod_ticker, e)
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
            logger.warning("EODHDProvider not available (missing key or requests lib)")
            return pd.DataFrame()

        from_date, to_date = _period_to_dates(period, start, end)
        ticker_list = [tickers] if isinstance(tickers, str) else list(tickers)

        frames: Dict[str, pd.DataFrame] = {}
        for tk in ticker_list:
            df = self._fetch_single(tk, from_date, to_date, interval)
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
        from_date, to_date = _period_to_dates(period, None, None)
        return self._fetch_single(ticker, from_date, to_date, interval)

    def search_isin(self, isin: str) -> str:
        """Resolve an ISIN to a Yahoo-style ticker symbol via EODHD. '' on failure.

        Prefers the primary listing when the ISIN is quoted on several venues.
        """
        if not self.available or not isin:
            return ""
        isin = isin.strip().upper()
        try:
            data = self._get(f"/search/{isin}", {"limit": 10})
        except Exception as e:
            logger.debug("EODHD /search failed for %s: %s", isin, e)
            return ""
        if not isinstance(data, list) or not data:
            return ""

        hits = [h for h in data if isinstance(h, dict)]
        # Exact ISIN match first, then primary listing, then first hit.
        exact = [h for h in hits if (h.get("ISIN") or "").strip().upper() == isin]
        pool = exact or hits
        primary = [h for h in pool if h.get("isPrimary")]
        pick = (primary or pool)[0]
        return _eodhd_to_yahoo(pick.get("Code", ""), pick.get("Exchange", "")).upper()

    def profile_isin(self, yahoo_ticker: str) -> str:
        """Resolve a ticker to its ISIN via EODHD search. '' on failure.

        Inverse of search_isin(): used to backfill ISINs for tickers where
        yfinance returns nothing (common for young US small-caps).
        """
        if not self.available or not yahoo_ticker:
            return ""
        eod_ticker = _map_ticker(yahoo_ticker, self._overrides)
        code = eod_ticker.rsplit(".", 1)[0]
        try:
            data = self._get(f"/search/{code}", {"limit": 10})
        except Exception as e:
            logger.debug("EODHD /search failed for %s: %s", eod_ticker, e)
            return ""
        if not isinstance(data, list):
            return ""
        for hit in data:
            if not isinstance(hit, dict):
                continue
            if (hit.get("Code") or "").strip().upper() != code.upper():
                continue
            found = (hit.get("ISIN") or "").strip().upper()
            if len(found) == 12:
                return found
        return ""

    def test_connection(self) -> bool:
        """Quick connectivity check — returns True when the API key is valid.

        Uses the `filter` parameter so the response is a single scalar rather
        than a full history (cheap against the daily request quota).
        """
        try:
            data = self._get("/eod/AAPL.US", {"filter": "last_close"})
            return isinstance(data, (int, float)) and data > 0
        except Exception:
            return False
