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
        equity = float(acct.equity or 0)
        last_equity = float(acct.last_equity or equity)
        return AccountInfo(
            equity=equity,
            buying_power=float(acct.buying_power or 0),
            cash=float(acct.cash or 0),
            unrealized_pnl=round(equity - last_equity, 2),  # day change
            currency=str(acct.currency or 'USD'),
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
        stop_price: Optional[float] = None,
    ) -> OrderResult:
        """Submit a market order, optionally with an attached stop-loss (OTO).

        If *stop_price* is set and the order is a buy, the order is submitted as
        an OTO (One-Triggers-Other) bracket: the main market order triggers a
        stop-market order at *stop_price* as soon as it fills.
        For sell orders *stop_price* is silently ignored.
        """
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        side_enum = OrderSide.BUY if side == 'buy' else OrderSide.SELL
        tif_enum = TimeInForce.DAY if time_in_force == 'day' else TimeInForce.GTC

        use_stop = (
            stop_price is not None
            and float(stop_price) > 0
            and side == 'buy'
        )

        try:
            kwargs: dict = dict(
                symbol=broker_symbol,
                qty=qty,
                side=side_enum,
                time_in_force=tif_enum,
            )
            if use_stop:
                kwargs['order_class'] = OrderClass.OTO
                kwargs['stop_loss'] = StopLossRequest(
                    stop_price=round(float(stop_price), 2)
                )

            req = MarketOrderRequest(**kwargs)
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
                'filled_at':        str(o.filled_at) if o.filled_at else '',
            }
            for o in orders
        ]

    def cancel_order(self, order_id: str) -> OrderResult:
        try:
            self._get_client().cancel_order_by_id(order_id)
            return OrderResult(
                order_id=order_id, ticker='', broker_symbol='',
                side='', qty=0, status='cancelled',
            )
        except Exception as e:
            logger.error(f"Alpaca cancel_order failed {order_id}: {e}")
            return OrderResult(
                order_id=order_id, ticker='', broker_symbol='',
                side='', qty=0, status='error', error_msg=str(e),
            )

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        """Return the latest trade price for each US symbol via the Alpaca Data API.

        Uses ``StockHistoricalDataClient`` (same credentials as the trading client).
        Returns only the symbols for which a price was found; missing symbols are
        silently skipped so the caller can fall back to the sim-DB price.
        """
        if not symbols:
            return {}
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestTradeRequest

            data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
            )
            req = StockLatestTradeRequest(symbol_or_symbols=symbols)
            trades = data_client.get_stock_latest_trade(req)
            return {sym: float(trade.price) for sym, trade in trades.items()}
        except Exception as e:
            logger.debug(f"get_latest_prices failed: {e}")
            return {}

    def get_portfolio_history(self, period: str = '1M') -> dict:
        """Return Alpaca portfolio history for the equity curve chart.

        period examples: '1D', '1W', '1M', '3M', '1A' (= 1 year)
        Returns dict with keys: timestamps (list[int]), equity (list[float]),
        profit_loss (list[float]), base_value (float).
        """
        try:
            from alpaca.trading.requests import GetPortfolioHistoryRequest
            req = GetPortfolioHistoryRequest(period=period, timeframe='1D')
            hist = self._get_client().get_portfolio_history(req)
            return {
                'timestamps':  list(hist.timestamp or []),
                'equity':      [float(v) for v in (hist.equity or [])],
                'profit_loss': [float(v) for v in (hist.profit_loss or [])],
                'base_value':  float(hist.base_value or 0),
            }
        except Exception as e:
            logger.debug(f"get_portfolio_history failed: {e}")
            return {}
