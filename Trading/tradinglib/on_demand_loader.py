"""
On-Demand-Laden von Kurs- und Stammdaten für die Scalable-Edition.

Nach dem Scalable-Import werden nur die tatsächlich hochgeladenen Ticker
nachgeladen — kein Bulk-Lauf über tausende Titel. Geladen werden:
  - OHLCV       → yf_<TICKER>.db   (via StockDataSaver, wie get_asset_data.py)
  - Stammdaten  → asset_info.db    (via get_asset_info.fetch_info_for + Upsert + FTS)

Bewusst NICHT geladen: asset_simulation_* (Scoring/Signale) — das ist Teil des
Upgrades auf die volle Trading-Plattform.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

# Dateien kleiner als das gelten als "leer/unvollständig" und werden neu geladen.
_MIN_DB_BYTES = 8192

# 2 Buchstaben (Ländercode) + 10 alphanumerische Zeichen = ISIN. Solche Werte
# bedeuten, dass die ISIN→Ticker-Auflösung fehlschlug → kein Yahoo/FMP-Download
# möglich, daher überspringen.
_ISIN_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$')


def _looks_like_isin(symbol: str) -> bool:
    return bool(_ISIN_RE.match(symbol or ''))


def _ohlc_present(ticker: str, db_path: str = 'database') -> bool:
    """True, wenn yf_<ticker>.db existiert und nicht offensichtlich leer ist."""
    from tradinglib import tools
    path = tools.Tools().get_path(path=db_path, file_name=f'yf_{ticker}.db')
    try:
        return os.path.exists(path) and os.path.getsize(path) > _MIN_DB_BYTES
    except OSError:
        return False


def _info_present(ticker: str, db_path: str = 'database') -> bool:
    """True, wenn asset_info eine Zeile für diesen Ticker hat."""
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
    """OHLCV für einen Ticker laden und in yf_<ticker>.db speichern."""
    from tradinglib import ticker_tools as tt
    t_tools = tt.TickerTools()
    saver = tt.StockDataSaver(ticker, db_path=db_path)
    try:
        failed = saver.save_all_intervals(
            intervals=tt_intervals(t_tools),
            periods=tt_periods(t_tools),
            force_remote=True,
        )
        # Mindestens ein Intervall erfolgreich = brauchbar
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
    """Kanonische Intervalle (wie get_asset_data.py: TickerTools().intervals)."""
    return getattr(t_tools, 'intervals', ['1m', '1h', '1d', '1wk', '1mo'])


def tt_periods(t_tools):
    """Kanonische Perioden (wie get_asset_data.py: TickerTools().periods)."""
    return getattr(t_tools, 'periods', ['7d', '60d', 'max', 'max', 'max'])


def _load_info(tickers: list, db_path: str = 'database') -> int:
    """Stammdaten für die Ticker laden (Batch-Upsert in asset_info + FTS-Rebuild)."""
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
    """Lade fehlende Kurs- und Stammdaten für die gegebenen Assets nach.

    Scalable liefert zu jeder Position die ISIN mit — diese ist der autoritative
    Schlüssel. Ist der Ticker noch eine ISIN (Auflösung übersprungen/fehlgeschlagen),
    wird hier über die ISIN aufgelöst (lokal → FMP → yfinance), statt die Position
    stillschweigend zu verwerfen.

    Args:
        assets:   Liste von Tickern (str) ODER von {ticker, isin}-Dicts/(ticker, isin)-Tupeln.
        db_path:  Datenbankverzeichnis.
        progress: optionales callable(done:int, total:int, label:str) für UI-Feedback.

    Returns:
        dict mit Zählern: tickers, resolved_from_isin, unresolved,
        ohlc_loaded, info_loaded, info_attempted.
    """
    # ISIN-getriebene Auflösung + Dedupe
    norm, seen = [], set()
    resolved_from_isin, unresolved = 0, 0
    pair_seen = set()
    for ticker, isin in _normalize_assets(assets):
        # identische (ticker, isin)-Paare nicht doppelt auflösen/laden
        if (ticker, isin) in pair_seen:
            continue
        pair_seen.add((ticker, isin))
        tk = ticker
        # Ticker fehlt oder ist noch eine ISIN → über die (immer vorhandene) ISIN auflösen
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
