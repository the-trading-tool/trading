"""
On-demand loading of price and master data for the Scalable edition.

After a Scalable import only the actually uploaded tickers are loaded —
no bulk run over thousands of instruments. Loaded:
  - OHLCV       → yf_<TICKER>.db   (via StockDataSaver, like get_asset_data.py)
  - Master data → asset_info.db    (via get_asset_info.fetch_info_for + upsert + FTS)

Deliberately NOT loaded: asset_simulation_* (scoring/signals) — that is part of the
upgrade to the full trading platform.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

# Files smaller than this are considered "empty/incomplete" and will be reloaded.
_MIN_DB_BYTES = 8192

# 2 letters (country code) + 10 alphanumeric characters = ISIN. Such values indicate
# that ISIN→ticker resolution failed → no Yahoo/FMP download possible, skip them.
_ISIN_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$')


def _looks_like_isin(symbol: str) -> bool:
    return bool(_ISIN_RE.match(symbol or ''))


def _ohlc_present(ticker: str, db_path: str = 'database') -> bool:
    """True when yf_<ticker>.db exists and is not obviously empty."""
    from tradinglib import tools
    path = tools.Tools().get_path(path=db_path, file_name=f'yf_{ticker}.db')
    try:
        return os.path.exists(path) and os.path.getsize(path) > _MIN_DB_BYTES
    except OSError:
        return False


def _info_present(ticker: str, db_path: str = 'database') -> bool:
    """True when asset_info contains a row for this ticker."""
    from tradinglib import tools
    from tradinglib.tools import open_db
    path = tools.Tools().get_path(path=db_path, file_name='asset_info.db')
    if not os.path.exists(path):
        return False
    try:
        with open_db(path, readonly=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM asset_info WHERE ticker = ? LIMIT 1", (ticker,)
            ).fetchone()
            return row is not None
    except Exception:
        return False


def _load_ohlc(ticker: str, db_path: str = 'database') -> bool:
    """Load OHLCV data for a ticker and save it to yf_<ticker>.db."""
    from tradinglib import ticker_tools as tt
    t_tools = tt.TickerTools()
    saver = tt.StockDataSaver(ticker, db_path=db_path)
    try:
        failed = saver.save_all_intervals(
            intervals=tt_intervals(t_tools),
            periods=tt_periods(t_tools),
            force_remote=True,
        )
        # At least one interval succeeded = usable
        return len(failed) < len(tt_intervals(t_tools))
    except Exception as e:
        logger.warning("On-demand OHLC load failed for %s: %s", ticker, e)
        return False
    finally:
        try:
            saver.close_connection()
        except Exception:
            pass


def tt_intervals(t_tools):
    """Canonical intervals (as in get_asset_data.py: TickerTools().intervals)."""
    return getattr(t_tools, 'intervals', ['1m', '1h', '1d', '1wk', '1mo'])


def tt_periods(t_tools):
    """Canonical periods (as in get_asset_data.py: TickerTools().periods)."""
    return getattr(t_tools, 'periods', ['7d', '60d', 'max', 'max', 'max'])


def _load_info(tickers: list, db_path: str = 'database') -> int:
    """Load master data for the tickers (batch upsert into asset_info + FTS rebuild)."""
    if not tickers:
        return 0
    import get_asset_info as gai
    from tradinglib.utils import DataUtils
    from tradinglib import tools
    from tradinglib.tools import open_db

    batch = []
    for t in tickers:
        try:
            row_map = gai.fetch_info_for(t)
        except Exception as e:
            logger.warning("On-demand info fetch failed for %s: %s", t, e)
            row_map = None
        if row_map:
            batch.append(row_map)

    if not batch:
        return 0

    path = tools.Tools().get_path(path=db_path, file_name='asset_info.db')
    with open_db(path, timeout=10) as conn:
        DataUtils.bulk_upsert_dicts(conn, 'asset_info', batch)
        conn.commit()
        try:
            gai.rebuild_fts_table(conn, 'asset_info')
        except Exception:
            logger.exception("FTS rebuild failed after on-demand info load")
    return len(batch)


def _normalize_assets(assets):
    """Yield (ticker, isin) pairs from a list of strings, dicts, or tuples."""
    out = []
    for item in assets or []:
        if item is None:
            continue
        if isinstance(item, str):
            out.append((item.strip().upper(), ''))
        elif isinstance(item, dict):
            out.append(((str(item.get('ticker') or '')).strip().upper(),
                        (str(item.get('isin') or '')).strip().upper()))
        elif isinstance(item, (tuple, list)) and item:
            tk = (str(item[0]) if len(item) > 0 and item[0] else '').strip().upper()
            isin = (str(item[1]) if len(item) > 1 and item[1] else '').strip().upper()
            out.append((tk, isin))
    return out


def ensure_assets_loaded(assets, db_path: str = 'database', progress=None) -> dict:
    """Load missing price and master data for the given assets on demand.

    Scalable supplies the ISIN for each position — this is the authoritative key.
    If the ticker is still an ISIN (resolution skipped/failed), it is resolved here
    via the ISIN (local → FMP → yfinance) instead of silently discarding the position.

    Args:
        assets:   List of ticker strings OR {ticker, isin} dicts / (ticker, isin) tuples.
        db_path:  Database directory.
        progress: Optional callable(done:int, total:int, label:str) for UI feedback.

    Returns:
        dict with counters: tickers, resolved_from_isin, unresolved,
        ohlc_loaded, info_loaded, info_attempted.
    """
    # ISIN-driven resolution + deduplication
    norm, seen = [], set()
    resolved_from_isin, unresolved = 0, 0
    pair_seen = set()
    for ticker, isin in _normalize_assets(assets):
        # do not resolve/load identical (ticker, isin) pairs twice
        if (ticker, isin) in pair_seen:
            continue
        pair_seen.add((ticker, isin))
        tk = ticker
        # Ticker missing or still an ISIN → resolve via the (always available) ISIN
        if (not tk or _looks_like_isin(tk)) and _looks_like_isin(isin):
            try:
                from tradinglib.scalable_import import _resolve_isin_to_ticker
                tk = (_resolve_isin_to_ticker(isin, db_path) or '').strip().upper()
                if tk and not _looks_like_isin(tk):
                    resolved_from_isin += 1
            except Exception as e:
                logger.debug("ISIN resolution in loader failed for %s: %s", isin, e)
        if not tk or _looks_like_isin(tk):
            unresolved += 1
            continue
        if tk in seen:
            continue
        seen.add(tk)
        norm.append(tk)

    total = len(norm)
    ohlc_loaded = 0
    info_needed = []

    for i, t in enumerate(norm):
        if progress:
            try:
                progress(i, total, t)
            except Exception:
                pass
        if not _ohlc_present(t, db_path):
            if _load_ohlc(t, db_path):
                ohlc_loaded += 1
        if not _info_present(t, db_path):
            info_needed.append(t)

    info_loaded = _load_info(info_needed, db_path) if info_needed else 0

    if progress:
        try:
            progress(total, total, '')
        except Exception:
            pass

    stats = {
        'tickers': total,
        'resolved_from_isin': resolved_from_isin,
        'unresolved': unresolved,
        'ohlc_loaded': ohlc_loaded,
        'info_loaded': info_loaded,
        'info_attempted': len(info_needed),
    }
    logger.info("ensure_assets_loaded: %s", stats)
    return stats
