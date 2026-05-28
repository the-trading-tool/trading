"""
IBKR broker — Phase 3 implementation placeholder.

Requirements (Phase 3):
  pip install ib_insync
  IB Gateway or TWS running locally:
    Port 7497 = paper trading
    Port 7496 = live trading

Connection example (Phase 3):
  from ib_insync import IB, Stock
  ib = IB()
  ib.connect('127.0.0.1', 7497, clientId=1)
  contract = Stock('SAP', 'XETRA', 'EUR')
  ib.qualifyContracts(contract)

IBKR covers all 4 strategies (SPX + GDAXI + MDAXI + SDAXI) because
it supports XETRA for German stocks unlike Alpaca (US-only).
"""
import logging
from typing import Optional
from tradinglib.broker_base import TradingBroker, AccountInfo, Position, OrderResult

logger = logging.getLogger(__name__)


class IBKRBroker(TradingBroker):
    BROKER_ID = 'ibkr'
    SUPPORTS_EU = True
    MODE = 'live'

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 7497,   # 7497 = paper, 7496 = live
        client_id: int = 1,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id

    def is_connected(self) -> bool:
        return False

    def get_account_info(self) -> AccountInfo:
        raise NotImplementedError("IBKR integration not yet implemented (Phase 3)")

    def get_positions(self) -> list[Position]:
        raise NotImplementedError("IBKR integration not yet implemented (Phase 3)")

    def submit_order(
        self,
        broker_symbol: str,
        qty: float,
        side: str,
        order_type: str = 'market',
        time_in_force: str = 'day',
        stop_price: Optional[float] = None,
    ) -> OrderResult:
        raise NotImplementedError("IBKR integration not yet implemented (Phase 3)")

    def close_position(self, broker_symbol: str) -> OrderResult:
        raise NotImplementedError("IBKR integration not yet implemented (Phase 3)")

    def get_orders(self, status: str = 'all') -> list[dict]:
        raise NotImplementedError("IBKR integration not yet implemented (Phase 3)")
