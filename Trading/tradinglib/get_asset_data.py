"""Compatibility wrapper for the canonical StockDataSaver implementation.

Historically this module implemented a StockDataSaver class which duplicated
the logic present in `tradinglib.ticker_tools.StockDataSaver`. To avoid
duplicate implementations we now re-export the canonical class from
`tradinglib.ticker_tools` while keeping this module as a thin compatibility
shim to avoid breaking external callers that import
`tradinglib.get_asset_data.StockDataSaver`.
"""

from .ticker_tools import StockDataSaver  # re-export for backward compatibility

__all__ = ["StockDataSaver"]
