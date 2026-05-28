from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class AccountInfo:
    equity: float
    buying_power: float
    cash: float
    unrealized_pnl: float
    currency: str


@dataclass
class Position:
    ticker: str
    broker_symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    market_value: float
    currency: str
    strategy: Optional[str] = None


@dataclass
class OrderResult:
    order_id: str
    ticker: str
    broker_symbol: str
    side: str            # 'buy' | 'sell'
    qty: float
    status: str          # 'submitted' | 'filled' | 'error'
    fill_price: Optional[float] = None
    error_msg: Optional[str] = None


class TradingBroker(ABC):
    BROKER_ID: str = ''
    SUPPORTS_EU: bool = False
    MODE: str = 'paper'  # 'paper' | 'live'

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_account_info(self) -> AccountInfo: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def submit_order(
        self,
        broker_symbol: str,
        qty: float,
        side: str,
        order_type: str = 'market',
        time_in_force: str = 'day',
        stop_price: Optional[float] = None,
    ) -> OrderResult: ...

    @abstractmethod
    def close_position(self, broker_symbol: str) -> OrderResult: ...

    @abstractmethod
    def get_orders(self, status: str = 'all') -> list[dict]: ...

    def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel a pending order by broker order ID.  Default: not supported."""
        return OrderResult(
            order_id=order_id, ticker='', broker_symbol='',
            side='', qty=0, status='error',
            error_msg='cancel_order not supported by this broker',
        )

    def get_portfolio_history(self, period: str = '1M') -> dict:
        """Return portfolio equity history as {timestamps, equity, profit_loss}.
        Default: empty dict (broker does not support this endpoint)."""
        return {}

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        """Return the latest trade price for each symbol.
        Keys are the broker symbols as passed in.
        Default: empty dict (broker does not support this endpoint)."""
        return {}
