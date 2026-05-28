"""
Alpaca Trading Client

Uses alpaca-py SDK to interact with Alpaca Markets REST API.
Supports US stocks, ETFs, and crypto on both paper and live accounts.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from app.utils.logger import get_logger
from app.services.alpaca_trading.symbols import normalize_symbol, format_display_symbol, parse_symbol

logger = get_logger(__name__)


def _market_hint_from_type(market_type: str) -> str:
    return "Crypto" if (market_type or "").strip().lower() == "crypto" else "USStock"


def _format_alpaca_error(err: Exception, *, context: str = "") -> str:
    """Turn Alpaca SDK/HTTP errors into actionable messages for operators."""
    msg = str(err or "").strip()
    low = msg.lower()
    prefix = f"{context}: " if context else ""
    if "invalid syntax" in low or '"code":400' in low or "code 400" in low:
        return (
            prefix
            + "Alpaca 返回 400 invalid syntax。若使用行情 WebSocket，请确认："
            "① 连接后 10 秒内发送 {\"action\":\"auth\",\"key\":\"...\",\"secret\":\"...\"}；"
            "② 订阅格式为 {\"action\":\"subscribe\",\"trades\":[\"AAPL\"]}（美股）或 "
            "{\"action\":\"subscribe\",\"trades\":[\"BTC/USD\"]}（加密，勿用 BTC/USDT）。"
            "本系统「测试连接」仅走 REST 交易接口，不经过该 WebSocket。"
        )
    if "401" in low or "403" in low or "auth failed" in low or "not authenticated" in low:
        return prefix + f"Alpaca 鉴权失败，请检查 API Key/Secret 与 paper(PK*)/live(AK*) 是否匹配。原始错误: {msg}"
    return prefix + msg


# Lazy import alpaca-py to allow other features to work without it installed
_alpaca_modules = None


def _ensure_alpaca():
    """Ensure alpaca-py is imported."""
    global _alpaca_modules
    if _alpaca_modules is None:
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import (
                MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
            )
            from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
            from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest
            _alpaca_modules = {
                "TradingClient": TradingClient,
                "MarketOrderRequest": MarketOrderRequest,
                "LimitOrderRequest": LimitOrderRequest,
                "GetOrdersRequest": GetOrdersRequest,
                "OrderSide": OrderSide,
                "TimeInForce": TimeInForce,
                "QueryOrderStatus": QueryOrderStatus,
                "StockHistoricalDataClient": StockHistoricalDataClient,
                "CryptoHistoricalDataClient": CryptoHistoricalDataClient,
                "StockLatestQuoteRequest": StockLatestQuoteRequest,
                "CryptoLatestQuoteRequest": CryptoLatestQuoteRequest,
            }
        except ImportError:
            raise ImportError(
                "alpaca-py is not installed. Run: pip install alpaca-py"
            )
    return _alpaca_modules


@dataclass
class AlpacaConfig:
    """Alpaca connection configuration."""
    api_key: str = ""
    secret_key: str = ""
    paper: bool = True  # True = paper-api.alpaca.markets, False = api.alpaca.markets
    base_url: Optional[str] = None  # Optional override
    timeout: float = 15.0


@dataclass
class OrderResult:
    """Order execution result (mirrors ibkr_trading.OrderResult)."""
    success: bool
    order_id: str = ""  # Alpaca uses UUID strings, not ints
    filled: float = 0.0
    avg_price: float = 0.0
    status: str = ""
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class AlpacaClient:
    """
    Alpaca Trading Client

    Wraps alpaca-py SDK to provide an interface compatible with QuantDinger's
    broker abstraction (mirrors IBKRClient surface).
    """

    def __init__(self, config: Optional[AlpacaConfig] = None):
        self.config = config or AlpacaConfig()
        self._trading_client = None
        self._stock_data_client = None
        self._crypto_data_client = None
        self._account_id: Optional[str] = None

    @property
    def connected(self) -> bool:
        """Connection is verified by a successful account fetch."""
        return self._trading_client is not None and self._account_id is not None

    def connect(self) -> bool:
        """Initialize Alpaca client and verify credentials by fetching account."""
        try:
            modules = _ensure_alpaca()
            api_key = (self.config.api_key or "").strip()
            secret_key = (self.config.secret_key or "").strip()
            if not api_key or not secret_key:
                logger.error("Alpaca connect failed: empty api_key or secret_key")
                return False

            self._trading_client = modules["TradingClient"](
                api_key=api_key,
                secret_key=secret_key,
                paper=self.config.paper,
                url_override=self.config.base_url,
            )
            # Align market-data REST with paper sandbox when using PK* keys.
            data_sandbox = bool(self.config.paper)
            self._stock_data_client = modules["StockHistoricalDataClient"](
                api_key=api_key,
                secret_key=secret_key,
                sandbox=data_sandbox,
            )
            self._crypto_data_client = modules["CryptoHistoricalDataClient"](
                api_key=api_key,
                secret_key=secret_key,
                sandbox=data_sandbox,
            )
            # Verify by fetching account
            account = self._trading_client.get_account()
            self._account_id = account.id
            mode = "paper" if self.config.paper else "live"
            logger.info(f"Alpaca connected ({mode}), account={self._account_id[:12]}..., status={account.status}")
            return True
        except Exception as e:
            logger.error("Alpaca connect failed: %s", _format_alpaca_error(e, context="REST account"))
            self._trading_client = None
            self._account_id = None
            return False

    def disconnect(self):
        """Alpaca is stateless REST — disconnect just clears local state."""
        self._trading_client = None
        self._stock_data_client = None
        self._crypto_data_client = None
        self._account_id = None
        logger.info("Alpaca client cleared")

    def _ensure_connected(self):
        if not self.connected:
            if not self.connect():
                raise RuntimeError("Not connected to Alpaca")

    # ==================== Order Methods ====================

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        market_type: str = "USStock",
    ) -> OrderResult:
        """Place a market order. market_type: 'USStock' or 'crypto'."""
        try:
            self._ensure_connected()
            modules = _ensure_alpaca()
            sym, asset_class = parse_symbol(symbol, market_hint=_market_hint_from_type(market_type))

            req = modules["MarketOrderRequest"](
                symbol=sym,
                qty=quantity,
                side=modules["OrderSide"].BUY if side.lower() == "buy" else modules["OrderSide"].SELL,
                time_in_force=modules["TimeInForce"].GTC if asset_class == "crypto" else modules["TimeInForce"].DAY,
            )
            order = self._trading_client.submit_order(order_data=req)

            # Brief poll for fill status
            time.sleep(2)
            order = self._trading_client.get_order_by_id(order.id)

            filled_qty = float(order.filled_qty or 0)
            avg_price = float(order.filled_avg_price or 0)
            status = str(order.status.value) if hasattr(order.status, 'value') else str(order.status)
            rejected = status.lower() in ("rejected", "cancelled", "canceled", "expired")

            return OrderResult(
                success=not rejected,
                order_id=str(order.id),
                filled=filled_qty,
                avg_price=avg_price,
                status=status,
                message=f"Order {status}" if rejected else "Order submitted",
                raw={
                    "id": str(order.id),
                    "status": status,
                    "filled_qty": filled_qty,
                    "submitted_at": str(order.submitted_at),
                },
            )
        except Exception as e:
            logger.error("Alpaca market order failed: %s", _format_alpaca_error(e))
            return OrderResult(success=False, message=_format_alpaca_error(e))

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        market_type: str = "USStock",
        extended_hours: bool = False,
    ) -> OrderResult:
        """Place a limit order. extended_hours=True for pre/post-market."""
        try:
            self._ensure_connected()
            modules = _ensure_alpaca()
            sym, asset_class = parse_symbol(symbol, market_hint=_market_hint_from_type(market_type))

            req = modules["LimitOrderRequest"](
                symbol=sym,
                qty=quantity,
                side=modules["OrderSide"].BUY if side.lower() == "buy" else modules["OrderSide"].SELL,
                time_in_force=modules["TimeInForce"].GTC if asset_class == "crypto" else modules["TimeInForce"].DAY,
                limit_price=price,
                extended_hours=extended_hours if asset_class == "us_equity" else False,
            )
            order = self._trading_client.submit_order(order_data=req)
            time.sleep(1)
            order = self._trading_client.get_order_by_id(order.id)

            filled_qty = float(order.filled_qty or 0)
            avg_price = float(order.filled_avg_price or 0)
            status = str(order.status.value) if hasattr(order.status, 'value') else str(order.status)
            rejected = status.lower() in ("rejected", "cancelled", "canceled", "expired")

            return OrderResult(
                success=not rejected,
                order_id=str(order.id),
                filled=filled_qty,
                avg_price=avg_price,
                status=status,
                message=f"Limit order {status}" if rejected else "Limit order submitted",
                raw={
                    "id": str(order.id),
                    "status": status,
                    "limit_price": price,
                    "extended_hours": extended_hours,
                },
            )
        except Exception as e:
            logger.error("Alpaca limit order failed: %s", _format_alpaca_error(e))
            return OrderResult(success=False, message=_format_alpaca_error(e))

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        try:
            self._ensure_connected()
            self._trading_client.cancel_order_by_id(order_id)
            logger.info(f"Alpaca order {order_id[:12]}... cancelled")
            return True
        except Exception as e:
            logger.error(f"Alpaca cancel order failed: {e}")
            return False

    # ==================== Query Methods ====================

    def get_account_summary(self) -> Dict[str, Any]:
        """Get account snapshot — mirrors IBKR's accountSummary shape loosely."""
        try:
            self._ensure_connected()
            acct = self._trading_client.get_account()
            return {
                "account": str(acct.id),
                "summary": {
                    "BuyingPower": {"value": str(acct.buying_power), "currency": "USD"},
                    "Cash": {"value": str(acct.cash), "currency": "USD"},
                    "PortfolioValue": {"value": str(acct.portfolio_value), "currency": "USD"},
                    "Equity": {"value": str(acct.equity), "currency": "USD"},
                    "DayTradeCount": {"value": str(acct.daytrade_count), "currency": ""},
                    "PatternDayTrader": {"value": str(acct.pattern_day_trader), "currency": ""},
                    "TradingBlocked": {"value": str(acct.trading_blocked), "currency": ""},
                    "Status": {"value": str(acct.status), "currency": ""},
                },
                "success": True,
            }
        except Exception as e:
            logger.error(f"Alpaca get_account_summary failed: {e}")
            return {"success": False, "error": str(e)}

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions."""
        try:
            self._ensure_connected()
            positions = self._trading_client.get_all_positions()
            return [
                {
                    "symbol": p.symbol,
                    "asset_class": str(p.asset_class.value) if hasattr(p.asset_class, 'value') else str(p.asset_class),
                    "quantity": float(p.qty),
                    "avgCost": float(p.avg_entry_price),
                    "marketValue": float(p.market_value),
                    "unrealizedPnL": float(p.unrealized_pl),
                    "currentPrice": float(p.current_price) if p.current_price else 0.0,
                    "side": str(p.side.value) if hasattr(p.side, 'value') else str(p.side),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Alpaca get_positions failed: {e}")
            return []

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Get all open orders."""
        try:
            self._ensure_connected()
            modules = _ensure_alpaca()
            req = modules["GetOrdersRequest"](status=modules["QueryOrderStatus"].OPEN, limit=500)
            orders = self._trading_client.get_orders(filter=req)
            return [
                {
                    "orderId": str(o.id),
                    "symbol": o.symbol,
                    "action": (str(o.side.value) if hasattr(o.side, 'value') else str(o.side)).upper(),
                    "quantity": float(o.qty),
                    "orderType": str(o.order_type.value) if hasattr(o.order_type, 'value') else str(o.order_type),
                    "limitPrice": float(o.limit_price) if o.limit_price else None,
                    "status": str(o.status.value) if hasattr(o.status, 'value') else str(o.status),
                    "filled": float(o.filled_qty or 0),
                    "remaining": float(o.qty) - float(o.filled_qty or 0),
                    "avgFillPrice": float(o.filled_avg_price or 0),
                    "submittedAt": str(o.submitted_at),
                    "extendedHours": bool(o.extended_hours),
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"Alpaca get_open_orders failed: {e}")
            return []

    def get_quote(self, symbol: str, market_type: str = "USStock") -> Dict[str, Any]:
        """Get latest quote (bid/ask). Routes to stock or crypto data client per asset class."""
        try:
            self._ensure_connected()
            modules = _ensure_alpaca()
            sym, asset_class = parse_symbol(symbol, market_hint=_market_hint_from_type(market_type))

            if asset_class == "crypto":
                req = modules["CryptoLatestQuoteRequest"](symbol_or_symbols=[sym])
                quotes = self._crypto_data_client.get_crypto_latest_quote(req)
            else:
                req = modules["StockLatestQuoteRequest"](symbol_or_symbols=[sym])
                quotes = self._stock_data_client.get_stock_latest_quote(req)

            q = quotes.get(sym) if isinstance(quotes, dict) else None
            if q is None:
                return {"success": False, "error": f"No quote returned for {sym}"}
            return {
                "success": True,
                "symbol": sym,
                "bid": float(q.bid_price) if q.bid_price else None,
                "ask": float(q.ask_price) if q.ask_price else None,
                "bid_size": float(q.bid_size) if q.bid_size else None,
                "ask_size": float(q.ask_size) if q.ask_size else None,
                "timestamp": str(q.timestamp),
            }
        except Exception as e:
            err = _format_alpaca_error(e)
            logger.error("Alpaca get_quote failed: %s", err)
            return {"success": False, "error": err}

    def get_connection_status(self) -> Dict[str, Any]:
        """Get connection status."""
        return {
            "connected": self.connected,
            "paper": self.config.paper,
            "base_url": self.config.base_url or (
                "https://paper-api.alpaca.markets" if self.config.paper else "https://api.alpaca.markets"
            ),
            "account_id": self._account_id,
        }


# Global singleton
_global_client: Optional[AlpacaClient] = None
_global_lock = threading.Lock()


def get_alpaca_client(config: Optional[AlpacaConfig] = None) -> AlpacaClient:
    """Get global Alpaca client singleton."""
    global _global_client
    with _global_lock:
        if _global_client is None:
            _global_client = AlpacaClient(config)
        return _global_client


def reset_alpaca_client():
    """Reset global client (disconnect and clear instance)."""
    global _global_client
    with _global_lock:
        if _global_client is not None:
            _global_client.disconnect()
            _global_client = None
