"""Provider factory for market data.

Usage:
    from tradinglib.providers import get_provider
    provider = get_provider()          # uses config.db setting
    df = provider.download("AAPL", period="1y")

Active provider: '_app:data_provider' in config.db (default: 'yahoo').
API keys:        KSP entry named like the provider ('fmp' / 'eodhd'),
                 field 'password'.
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
_KSP_EODHD_NAME = "eodhd"

# Providers that need an API key from KSP, in the order shown in the UI.
KEYED_PROVIDERS = ("fmp", "eodhd")
PROVIDERS = ("yahoo",) + KEYED_PROVIDERS


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


def _read_provider_key(provider: str, db_path: str = "database") -> str:
    """Read a provider's API key from KSP (entry '{provider}', field 'password')."""
    try:
        from tradinglib.ksplib import Ksp
        creds = Ksp(storage_path=db_path, secrets_path=db_path).get_ksp(provider)
        if isinstance(creds, dict):
            return creds.get("password", "") or ""
    except Exception as e:
        logger.debug("KSP lookup for %s key failed: %s", provider, e)
    return ""


def _read_fmp_key(db_path: str = "database") -> str:
    """Read the FMP API key from KSP (entry 'fmp', field 'password')."""
    return _read_provider_key(_KSP_FMP_NAME, db_path)


def _read_eodhd_key(db_path: str = "database") -> str:
    """Read the EODHD API key from KSP (entry 'eodhd', field 'password')."""
    return _read_provider_key(_KSP_EODHD_NAME, db_path)


def _build_keyed_provider(name: str, db_path: str = "database"):
    """Build an API-key-based provider, or None when key/library is missing."""
    api_key = _read_provider_key(name, db_path)
    if not api_key:
        return None
    overrides_raw = _read_app_config(f"{name}_ticker_overrides", {})
    overrides = overrides_raw if isinstance(overrides_raw, dict) else {}
    if name == "fmp":
        from tradinglib.providers.fmp_provider import FMPProvider
        provider = FMPProvider(api_key=api_key, overrides=overrides)
    elif name == "eodhd":
        from tradinglib.providers.eodhd_provider import EODHDProvider
        provider = EODHDProvider(api_key=api_key, overrides=overrides)
    else:
        return None
    return provider if provider.available else None


def get_provider(name: Optional[str] = None, db_path: str = "database") -> MarketDataProvider:
    """Return the configured provider instance.

    Args:
        name:    Override ('yahoo', 'fmp' or 'eodhd'). None = read from config.db.
        db_path: Path to the database directory (for KSP and config.db lookup).
    """
    if name is None:
        name = _read_app_config("data_provider") or "yahoo"

    if name in KEYED_PROVIDERS:
        provider = _build_keyed_provider(name, db_path)
        if provider is not None:
            return provider
        logger.warning(
            "%s provider selected but API key missing in KSP (entry '%s'). "
            "Falling back to Yahoo Finance.",
            name.upper(), name,
        )

    from tradinglib.providers.yahoo_provider import YahooProvider
    return YahooProvider()


def get_fmp_provider(db_path: str = "database"):
    """Return an FMPProvider when an API key is configured, else None.

    Independent of the configured default provider — used e.g. for ISIN→ticker
    resolution, regardless of which provider serves prices.
    """
    return _build_keyed_provider("fmp", db_path)


def get_eodhd_provider(db_path: str = "database"):
    """Return an EODHDProvider when an API key is configured, else None.

    Counterpart to get_fmp_provider() — EODHD also resolves ISINs via its
    search endpoint.
    """
    return _build_keyed_provider("eodhd", db_path)


def get_isin_resolver(db_path: str = "database"):
    """Return a provider able to resolve ISINs (search_isin/profile_isin), else None.

    Prefers the configured data provider when it supports ISIN lookups, so a
    user running EODHD is not forced to also hold an FMP key. Falls back to
    whichever keyed provider has a key.
    """
    configured = _read_app_config("data_provider") or "yahoo"
    order = [configured] + [p for p in KEYED_PROVIDERS if p != configured]
    for name in order:
        if name not in KEYED_PROVIDERS:
            continue
        provider = _build_keyed_provider(name, db_path)
        if provider is not None:
            return provider
    return None
