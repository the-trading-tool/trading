"""Provider factory for market data.

Usage:
    from tradinglib.providers import get_provider
    provider = get_provider()          # uses config.db setting
    df = provider.download("AAPL", period="1y")

Active provider: '_app:data_provider' in config.db (default: 'yahoo').
FMP API key:     KSP entry named 'fmp', field 'password'.
"""
import json
import logging
import os
import sqlite3
from typing import Optional

from tradinglib.providers.base import MarketDataProvider
from tradinglib.tools import open_db

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join("database", "config.db")
_KSP_FMP_NAME = "fmp"


def _read_app_config(key: str, default=None):
    """Read a value stored under the '_app:{key}' prefix in config.db."""
    try:
        with open_db(_DB_PATH, readonly=True) as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?", (f"_app:{key}",)
            ).fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    return row[0]
    except Exception:
        pass
    return default


def _read_fmp_key(db_path: str = "database") -> str:
    """Read the FMP API key from KSP (entry 'fmp', field 'password')."""
    try:
        from tradinglib.ksplib import Ksp
        creds = Ksp(storage_path=db_path, secrets_path=db_path).get_ksp(_KSP_FMP_NAME)
        if isinstance(creds, dict):
            return creds.get("password", "") or ""
    except Exception as e:
        logger.debug("KSP lookup for FMP key failed: %s", e)
    return ""


def get_provider(name: Optional[str] = None, db_path: str = "database") -> MarketDataProvider:
    """Return the configured provider instance.

    Args:
        name:    Override ('yahoo' or 'fmp'). None = read from config.db.
        db_path: Path to the database directory (for KSP and config.db lookup).
    """
    if name is None:
        name = _read_app_config("data_provider") or "yahoo"

    if name == "fmp":
        from tradinglib.providers.fmp_provider import FMPProvider
        api_key = _read_fmp_key(db_path)
        overrides_raw = _read_app_config("fmp_ticker_overrides", {})
        overrides = overrides_raw if isinstance(overrides_raw, dict) else {}
        provider = FMPProvider(api_key=api_key, overrides=overrides)
        if not provider.available:
            logger.warning(
                "FMP provider selected but API key missing in KSP (entry '%s'). "
                "Falling back to Yahoo Finance.",
                _KSP_FMP_NAME,
            )
            from tradinglib.providers.yahoo_provider import YahooProvider
            return YahooProvider()
        return provider

    from tradinglib.providers.yahoo_provider import YahooProvider
    return YahooProvider()


def get_fmp_provider(db_path: str = "database"):
    """Return an FMPProvider when an API key is configured, else None.

    Independent of the configured default provider — used e.g. for ISIN→ticker
    resolution, which only FMP offers, regardless of which provider serves prices.
    """
    api_key = _read_fmp_key(db_path)
    if not api_key:
        return None
    from tradinglib.providers.fmp_provider import FMPProvider
    overrides_raw = _read_app_config("fmp_ticker_overrides", {})
    overrides = overrides_raw if isinstance(overrides_raw, dict) else {}
    provider = FMPProvider(api_key=api_key, overrides=overrides)
    return provider if provider.available else None
