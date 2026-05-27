import logging
import os
from typing import Optional

from tradinglib.broker_base import TradingBroker, AccountInfo, Position, OrderResult

logger = logging.getLogger(__name__)


class AlpacaBroker(TradingBroker):
    BROKER_ID = 'alpaca'
    SUPPORTS_EU = False
    MODE = 'paper'

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
    ):
        self.api_key = api_key or os.getenv('APCA_API_KEY_ID', '')
        self.secret_key = secret_key or os.getenv('APCA_API_SECRET_KEY', '')
        self.paper = paper
        self._client = None

    def _get_client(self):
        if self._client is None:
            from alpaca.trading.client import TradingClient
            self._client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.paper,
            )
        return self._client

    def is_connected(self) -> bool:
        if not self.api_key or not self.secret_key:
            return False
        try:
            self._get_client().get_account()
            return True
        except ImportError:
            # Re-raise so callers can show a meaningful "package missing" message
            raise
        except Exception as e:
            logger.debug(f"Alpaca connection check failed: {e}")
            return False

    def get_account_info(self) -> AccountInfo:
        acct = self._get_client().get_account()
        return AccountInfo(
            equity=float(acct.equity),
            buying_power=float(acct.buying_power),
            cash=float(acct.cash),
            unrealized_pnl=float(acct.unrealized_pl or 0),
            currency='USD',
        )

    def get_positions(self) -> list[Position]:
        raw = self._get_client().get_all_positions()
        result = []
        for p in raw:
            pnl = float(p.unrealized_pl or 0)
            cost = float(p.cost_basis or 1)
            result.append(Position(
                ticker=p.symbol,
                broker_symbol=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price or 0),
                unrealized_pnl=pnl,
                unrealized_pnl_pct=round(pnl / cost * 100, 2) if cost else 0.0,
                market_value=float(p.market_value or 0),
                currency='USD',
            ))
        return result

    def submit_order(
        self,
        broker_symbol: str,
        qty: float,
        side: str,
        order_type: str = 'market',
        time_in_force: str = 'day',
    ) -> OrderResult:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        side_enum = OrderSide.BUY if side == 'buy' else OrderSide.SELL
        tif_enum = TimeInForce.DAY if time_in_force == 'day' else TimeInForce.GTC

        try:
            req = MarketOrderRequest(
                symbol=broker_symbol,
                qty=qty,
                side=side_enum,
                time_in_force=tif_enum,
            )
            order = self._get_client().submit_order(req)
            return OrderResult(
                order_id=str(order.id),
                ticker=broker_symbol,
                broker_symbol=broker_symbol,
                side=side,
                qty=qty,
                status='submitted',
            )
        except Exception as e:
            logger.error(f"Alpaca order failed {broker_symbol} {side} {qty}: {e}")
            return OrderResult(
                order_id='',
                ticker=broker_symbol,
                broker_symbol=broker_symbol,
                side=side,
                qty=qty,
                status='error',
                error_msg=str(e),
            )

    def close_position(self, broker_symbol: str) -> OrderResult:
        try:
            resp = self._get_client().close_position(broker_symbol)
            return OrderResult(
                order_id=str(resp.id),
                ticker=broker_symbol,
                broker_symbol=broker_symbol,
                side='sell',
                qty=float(resp.qty),
                status='submitted',
            )
        except Exception as e:
            logger.error(f"Alpaca close_position failed {broker_symbol}: {e}")
            return OrderResult(
                order_id='',
                ticker=broker_symbol,
                broker_symbol=broker_symbol,
                side='sell',
                qty=0.0,
                status='error',
                error_msg=str(e),
            )

    def get_orders(self, status: str = 'all') -> list[dict]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        status_map = {
            'all':    QueryOrderStatus.ALL,
            'open':   QueryOrderStatus.OPEN,
            'closed': QueryOrderStatus.CLOSED,
        }
        req = GetOrdersRequest(status=status_map.get(status, QueryOrderStatus.ALL))
        orders = self._get_client().get_orders(req)
        return [
            {
                'id':               str(o.id),
                'symbol':           o.symbol,
                'side':             str(o.side),
                'qty':              float(o.qty or 0),
                'status':           str(o.status),
                'filled_avg_price': float(o.filled_avg_price or 0),
                'created_at':       str(o.created_at),
            }
            for o in orders
        ]
