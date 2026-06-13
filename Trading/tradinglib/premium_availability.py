"""
Checks which premium modules are physically present on this installation.

Usage
-----
    from tradinglib.premium_availability import STRATEGY_ENGINE_AVAILABLE, PAPER_TRADING_AVAILABLE

These flags are True only when the relevant files exist in tradinglib/premium/.
They are combined with license checks in asset_analyzer.py to decide what
to show in the sidebar.
"""

from pathlib import Path

_p = Path(__file__).parent / "premium"


def _has(*filenames: str) -> bool:
    """Return True when all given filenames exist inside the premium/ package directory."""
    return all((_p / f).exists() for f in filenames)


# Strategy Engine: Multi-Strategy + Strategy Finder
STRATEGY_ENGINE_AVAILABLE: bool = _has("multi_transaction.py", "asset_simulator.py")

# Paper / Live Trading
PAPER_TRADING_AVAILABLE: bool = _has("trading_page.py", "trading_bridge.py")

# Seasonality (yearly trend overlay)
SEASONALITY_AVAILABLE: bool = _has("seasonality.py")
