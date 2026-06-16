"""
App-Edition-Schalter für die Trading-App.

Zwei Editionen aus *derselben* Codebasis:

  'full'      — die vollständige Trading-App (Default; Verhalten unverändert)
  'scalable'  — schlanke Consumer-Edition für Scalable-Capital-Nutzer:
                Einstieg auf "Own Transactions", nur ausgewählte Routen sind frei,
                alle übrigen Features sind ein Upgrade.

Die Edition wird beim Import bestimmt aus (in dieser Reihenfolge):
  1. ENV   TRADING_EDITION                      (primär — für das Deployment)
  2. config.db  key  '_app:app_edition'         (App-weiter Fallback)
  3. Default 'full'

Wichtig: In der 'full'-Edition sind alle Guard-Funktionen ein No-Op
(`is_route_allowed()` liefert immer True) — die bestehende App verhält sich
damit absolut identisch zu vorher.
"""
import logging
import os

logger = logging.getLogger(__name__)

_VALID_EDITIONS = ('full', 'scalable')

# Pseudo-Identität für den App-weiten (nicht pro-User genamespacten) Config-Slot.
# SystemConfig namespaced jeden Key als '<username>:<key>'; mit diesem festen
# Namen ist die Edition unabhängig vom eingeloggten Nutzer lesbar.
_APP_SCOPE_USER = '_app'


def _read_edition() -> str:
    """Bestimme die aktive Edition aus ENV → config.db → Default 'full'."""
    # 1. ENV
    env = (os.environ.get('TRADING_EDITION') or '').strip().lower()
    if env in _VALID_EDITIONS:
        return env

    # 2. config.db (App-weiter Slot '_app:app_edition')
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

# Routen, die in der Scalable-Edition frei sind. Der Routen-Key ist der
# bestimmende query-param; None = Default-Route (Asset Viewer).
SCALABLE_FREE_ROUTES = frozenset({
    None,               # Asset Viewer (Default-Route)
    'own_trades',       # Einstieg / Landing
    'marketmap',        # Market Map
    'rotation',         # Sector Rotation
    'market_overview',  # Market View
    'compound',         # Lump Sum Investment
})

# Alle Routen-bestimmenden query-params in Prüf-Reihenfolge. Spiegelt die
# if/elif-Kette in asset_analyzer.AssetAnalyzer.render() wider.
_ROUTE_PARAMS = (
    'admin', 'live_chart', 'performance', 'multi', 'own_trades',
    'strategy_finder', 'trading', 'rotation', 'market_overview',
    'compound', 'marketmap', 'summary', 'option_calc',
)

# Routen/Endpunkte, die unabhängig von der Edition immer durchlaufen
# (Infrastruktur — werden in render() ohnehin vor dem Login behandelt).
_ALWAYS_ALLOWED = frozenset({'stream', 'download'})


def active_route(parms: dict):
    """Bestimme den Routen-Key aus den query-params.

    Liefert den ersten gesetzten Routen-Param (Reihenfolge = elif-Kette) oder
    None für die Default-Route (Asset Viewer).
    """
    if parms:
        for key in _ROUTE_PARAMS:
            if parms.get(key):
                return key
    return None


def is_route_allowed(route) -> bool:
    """True, wenn *route* in der aktiven Edition gerendert werden darf.

    In der 'full'-Edition immer True (No-Op). In der Scalable-Edition nur für
    die freigeschalteten bzw. Infrastruktur-Routen.
    """
    if IS_FULL:
        return True
    if route in _ALWAYS_ALLOWED:
        return True
    return route in SCALABLE_FREE_ROUTES
