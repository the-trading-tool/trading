"""Centralized market data download wrapper around yfinance.

Provides a thin abstraction for downloading historical price data so callers
use a single point for logging, retries, and future caching.
"""
from typing import Optional, Union, List
import logging
import threading

try:
    import yfinance as yf
except Exception:
    yf = None
import pandas as pd

logger = logging.getLogger(__name__)

# yf.download() is not thread-safe (module-global yfinance.shared._DFS) —
# concurrent calls must be serialised (see also yahoo_provider.py).
_YF_DOWNLOAD_LOCK = threading.Lock()

# Maximum number of tickers kept in each metadata cache.
# A single ticker_info dict is typically 2-5 KB; 512 entries ≈ 1-2 MB.
_CACHE_MAX_SIZE: int = 512


def _put_cache(cache: dict, key: str, value) -> None:
    """Insert *value* into *cache* under *key*, evicting the oldest entry when full.

    Python dicts preserve insertion order (3.7+), so ``next(iter(cache))``
    reliably returns the least-recently-inserted key.
    """
    if len(cache) >= _CACHE_MAX_SIZE:
        try:
            del cache[next(iter(cache))]
        except StopIteration:
            pass  # cache was emptied concurrently — harmless
    cache[key] = value


def download(tickers: Union[str, List[str]], start: Optional[str] = None, end: Optional[str] = None,
             period: Optional[str] = None, interval: Optional[str] = None, actions: bool = False,
             progress: bool = False, auto_adjust: bool = False, prepost: bool = True, force_remote: bool = False):
    """Wrapper for yf.download with consistent defaults and logging.

    Args:
        tickers: ticker symbol or list of symbols
        start/end: date strings
        period: yf period string (e.g., '1d', '1mo'), optional
        interval: yf interval string (e.g., '1m', '1d')
    Returns:
        pandas.DataFrame (or empty DataFrame on failure)
    """
    logger.debug(f"market_data.download called tickers={tickers} period={period} interval={interval} force_remote={force_remote}")
    if yf is None:
        logger.warning("yfinance not available; returning empty DataFrame")
        import pandas as pd
        return pd.DataFrame()

    # keep yfinance quieter
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)

    try:
        # If caller explicitly requests remote, skip local cache
        if not force_remote:
            try:
                # lazy imports to avoid circular import problems
                from concurrent.futures import ThreadPoolExecutor, as_completed
                import importlib

                fetch_mod = importlib.import_module('tradinglib.fetch_data')
                FetchData = getattr(fetch_mod, 'FetchData')

                # normalize tickers to a list
                if isinstance(tickers, str):
                    ticker_list = [tickers]
                else:
                    ticker_list = list(tickers)

                pieces = []

                def _load_local(tk):
                    try:
                        fd = FetchData(database_path='database')
                        local_df = fd.load_price_data(tk, period='max', interval=interval or '1d', aggregate=False)
                    except Exception:
                        return None
                    if local_df is None or local_df.empty:
                        return None
                    try:
                        local_df.index = pd.to_datetime(local_df.index)
                    except Exception:
                        pass
                    if start is not None:
                        local_df = local_df[local_df.index >= pd.to_datetime(start)]
                    if end is not None:
                        local_df = local_df[local_df.index <= pd.to_datetime(end)]
                    if local_df.empty:
                        return None
                    cols = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in local_df.columns]
                    if not cols:
                        return None
                    local_df = local_df[cols]
                    local_df.columns = pd.MultiIndex.from_product([local_df.columns, [tk]])
                    return local_df

                max_workers = min(8, max(1, len(ticker_list)))
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futures = {ex.submit(_load_local, tk): tk for tk in ticker_list}
                    for fut in as_completed(futures):
                        try:
                            res = fut.result()
                            if res is not None:
                                pieces.append(res)
                        except Exception:
                            pass

                if pieces:
                    df = pd.concat(pieces, axis=1).sort_index()
                    try:
                        # Ensure an 'Adj Close' column exists for each ticker: some local caches
                        # provide only 'Close' (not 'Adj Close'). Create 'Adj Close' from 'Close'
                        # so callers expecting 'Adj Close' keep working.
                        if isinstance(df.columns, pd.MultiIndex):
                            top = df.columns.get_level_values(0)
                            if 'Adj Close' not in top and 'Close' in top:
                                # for each ticker (level 1) copy Close -> Adj Close when missing
                                ticks = list(dict.fromkeys(df.columns.get_level_values(1)))
                                created = []
                                for tk in ticks:
                                    if ('Close', tk) in df.columns and ('Adj Close', tk) not in df.columns:
                                        df[('Adj Close', tk)] = df[('Close', tk)]
                                        created.append(tk)
                                # re-sort columns to keep order stable
                                df = df.reindex(columns=sorted(df.columns))
                                if created:
                                    logging.getLogger(__name__).debug(f"market_data: synthesized 'Adj Close' for local tickers: {created}")
                        else:
                            if 'Adj Close' not in df.columns and 'Close' in df.columns:
                                df['Adj Close'] = df['Close']
                                logging.getLogger(__name__).debug("market_data: synthesized single-column 'Adj Close' from 'Close' for local data")

                        # normalize index to tz-naive UTC for consistency
                        from tradinglib.utils import DataUtils
                        DataUtils.normalize_index_to_tz_naive(df, tz='UTC')
                    except Exception:
                        pass
                    return df
            except Exception:
                logging.getLogger(__name__).debug('local cache fetch failed or disabled, falling back to yfinance')

        # fallback to configured provider (Yahoo or FMP)
        try:
            from tradinglib.providers import get_provider
            _provider = get_provider()
        except Exception:
            _provider = None

        if _provider is not None:
            df = _provider.download(tickers=tickers, start=start, end=end, period=period, interval=interval)
        elif yf is not None:
            call_interval = interval or '1d'
            with _YF_DOWNLOAD_LOCK:
                df = yf.download(tickers=tickers, start=start, end=end, period=period, interval=call_interval,
                                 actions=actions, progress=progress, auto_adjust=auto_adjust, prepost=prepost)
        else:
            return pd.DataFrame()
        try:
            # Ensure 'Adj Close' exists when yfinance returned only 'Close'
            if isinstance(df.columns, pd.MultiIndex):
                top = df.columns.get_level_values(0)
                if 'Adj Close' not in top and 'Close' in top:
                    ticks = list(dict.fromkeys(df.columns.get_level_values(1)))
                    created = []
                    for tk in ticks:
                        if ('Close', tk) in df.columns and ('Adj Close', tk) not in df.columns:
                            df[('Adj Close', tk)] = df[('Close', tk)]
                            created.append(tk)
                    df = df.reindex(columns=sorted(df.columns))
                    if created:
                        logging.getLogger(__name__).debug(f"market_data: synthesized 'Adj Close' for yf.download tickers: {created}")
            else:
                if 'Adj Close' not in df.columns and 'Close' in df.columns:
                    df['Adj Close'] = df['Close']
                    logging.getLogger(__name__).debug("market_data: synthesized single-column 'Adj Close' from 'Close' for yf.download")

            from tradinglib.utils import DataUtils
            DataUtils.normalize_index_to_tz_naive(df, tz='UTC')
        except Exception:
            pass
        return df
    except Exception as e:
        logging.getLogger(__name__).exception(f"market_data.download failed for {tickers}: {e}")
        import pandas as pd
        return pd.DataFrame()


def ticker_history(ticker: str, period: Optional[str] = None, interval: Optional[str] = None):
    """Wrapper around provider.ticker_history() — graceful on errors."""
    try:
        from tradinglib.providers import get_provider
        df = get_provider().ticker_history(ticker, period=period, interval=interval)
    except Exception:
        if yf is None:
            return pd.DataFrame()
        try:
            df = yf.Ticker(ticker).history(period=period, interval=interval)
        except Exception:
            return pd.DataFrame()
    try:
        from tradinglib.utils import DataUtils
        DataUtils.normalize_index_to_tz_naive(df, tz='UTC')
    except Exception:
        pass
    return df


# Simple metadata wrappers with in-memory caching to centralize info/financials access
_INFO_CACHE: dict = {}
_FINANCIALS_CACHE: dict = {}

def ticker_info(ticker: str, use_cache: bool = True) -> dict:
    """Return the yfinance info dict for ticker, with bounded in-memory caching.

    Returns an empty dict on failure.
    """
    if use_cache and ticker in _INFO_CACHE:
        return _INFO_CACHE[ticker]
    try:
        t = yf.Ticker(ticker)
        info = t.info if hasattr(t, 'info') else {}
        _put_cache(_INFO_CACHE, ticker, info)
        return info
    except Exception:
        return {}


def ticker_financials(ticker: str, use_cache: bool = True):
    """Return the yfinance financials DataFrame for ticker, with bounded in-memory caching.

    Returns an empty DataFrame on failure.
    """
    if use_cache and ticker in _FINANCIALS_CACHE:
        return _FINANCIALS_CACHE[ticker]
    try:
        t = yf.Ticker(ticker)
        fin = t.financials if hasattr(t, 'financials') else pd.DataFrame()
        _put_cache(_FINANCIALS_CACHE, ticker, fin)
        return fin
    except Exception:
        return pd.DataFrame()


_BALANCE_CACHE: dict = {}

def ticker_balance_sheet(ticker: str, use_cache: bool = True):
    """Return the yfinance balance_sheet DataFrame for ticker, with bounded in-memory caching.

    Returns an empty DataFrame on failure.
    """
    if use_cache and ticker in _BALANCE_CACHE:
        return _BALANCE_CACHE[ticker]
    try:
        t = yf.Ticker(ticker)
        bal = t.balance_sheet if hasattr(t, 'balance_sheet') else pd.DataFrame()
        _put_cache(_BALANCE_CACHE, ticker, bal)
        return bal
    except Exception:
        return pd.DataFrame()
