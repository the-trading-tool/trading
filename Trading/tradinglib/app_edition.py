"""
App edition switch for the trading app.

Two editions from the *same* codebase:

  'full'      — the full trading app (default; behaviour unchanged)
  'scalable'  — slim consumer edition for Scalable Capital users:
                entry point is "Own Transactions", only selected routes are free,
                all other features require an upgrade.

The edition is determined at import time from (in this order):
  1. ENV   TRADING_EDITION                      (primary — for deployment)
  2. config.db  key  '_app:app_edition'         (app-wide fallback)
  3. Default 'full'

Important: In the 'full' edition all guard functions are a no-op
(`is_route_allowed()` always returns True) — the existing app behaves
absolutely identically to before.
"""
import logging
import os

logger = logging.getLogger(__name__)

_VALID_EDITIONS = ('full', 'scalable')

# Pseudo-identity for the app-wide (not per-user namespaced) config slot.
# SystemConfig namespaces every key as '<username>:<key>'; with this fixed
# name the edition is readable regardless of the logged-in user.
_APP_SCOPE_USER = '_app'


def _read_edition() -> str:
    """Determine the active edition from ENV → config.db → default 'full'."""
    # 1. ENV
    env = (os.environ.get('TRADING_EDITION') or '').strip().lower()
    if env in _VALID_EDITIONS:
        return env

    # 2. config.db (app-wide slot '_app:app_edition')
    try:
        from tradinglib import system_config as sysconf
        val = sysconf.SystemConfig(username=_APP_SCOPE_USER, bare_mode=True).get_value('app_edition', None)
        if val and str(val).strip().lower() in _VALID_EDITIONS:
            return str(val).strip().lower()
    except Exception as e:
        logger.debug("app_edition: config.db read failed: %s", e)

    # 3. Default
    return 'full'


EDITION: str = _read_edition()
IS_SCALABLE: bool = EDITION == 'scalable'
IS_FULL: bool = EDITION == 'full'

logger.info("app_edition: running '%s' edition", EDITION)


# ─────────────────────────────────────────────────────────────────────────────
# Routen
# ─────────────────────────────────────────────────────────────────────────────

# Routes that are free in the Scalable edition. The route key is the
# determining query param; None = default route (Asset Viewer).
SCALABLE_FREE_ROUTES = frozenset({
    None,               # Asset Viewer (default route)
    'own_trades',       # entry / landing
    'marketmap',        # Market Map
    'rotation',         # Sector Rotation
    'rotation_hub',     # Rotation / Correlation (Sector+Global Rotation, Correlation, Fear & Greed)
    'market_overview',  # Market View
    'correlation',      # Correlation Index
    'compound',         # Lump Sum Investment
})

# All route-determining query params in check order. Mirrors the
# if/elif chain in asset_analyzer.AssetAnalyzer.render().
# 'upgrade' is a virtual route (no elif branch): it is never in
# SCALABLE_FREE_ROUTES and is therefore always directed by the guard
# to the upgrade page — used by the upgrade CTA in the sidebar.
_ROUTE_PARAMS = (
    'admin', 'live_chart', 'performance', 'multi', 'own_trades',
    'strategy_finder', 'trading', 'rotation', 'rotation_hub', 'market_overview',
    'correlation', 'compound', 'marketmap', 'summary', 'four_ps', 'option_calc',
    'upgrade',
)

# Routes/endpoints that are always allowed regardless of edition
# (infrastructure — handled in render() before login anyway).
_ALWAYS_ALLOWED = frozenset({'stream', 'download'})


def active_route(parms: dict):
    """Determine the route key from the query params.

    Returns the first set route param (order = elif chain) or
    None for the default route (Asset Viewer).
    """
    if parms:
        for key in _ROUTE_PARAMS:
            if parms.get(key):
                return key
    return None


def is_route_allowed(route) -> bool:
    """True if *route* may be rendered in the active edition.

    In the 'full' edition always True (no-op). In the Scalable edition only for
    the unlocked or infrastructure routes.
    """
    if IS_FULL:
        return True
    if route in _ALWAYS_ALLOWED:
        return True
    return route in SCALABLE_FREE_ROUTES
