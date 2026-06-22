"""
Alpaca Trading Client

Uses alpaca-py SDK to interact with Alpaca Markets REST API.
Supports US stocks, ETFs, and crypto on both paper and live accounts.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from app.utils.logger import get_logger
from app.services.alpaca_trading.symbols import normalize_symbol, format_display_symbol, parse_symbol

logger = get_logger(__name__)


def _market_hint_from_type(market_type: str) -> str:
    return "Crypto" if (market_type or "").strip().lower() == "crypto" else "USStock"


def _as_str_id(value: Union[str, UUID, None]) -> str:
    """Normalize Alpaca ids (alpaca-py may return uuid.UUID for account/order ids)."""
    if value is None:
        return ""
    return str(value)


def _id_log_prefix(value: Union[str, UUID, None], length: int = 12) -> str:
    s = _as_str_id(value)
    if not s:
        return ""
    return s[:length] if len(s) > length else s


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value.value) if hasattr(value, "value") else str(value)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _str_attr(obj: Any, name: str, default: str = "") -> str:
    return str(getattr(obj, name, default) or default)


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
    if "401" in low or "auth failed" in low or "not authenticated" in low or "unauthorized" in low:
        return prefix + f"Alpaca authentication failed. Check API Key/Secret and paper(PK*)/live(AK*) mode. Raw error: {msg}"
    if "403" in low:
        return prefix + f"Alpaca rejected the request. Raw error: {msg}"
    return prefix + msg


def normalize_base_url(base_url: Optional[str]) -> Optional[str]:
    """
    Normalize Alpaca SDK base URL overrides.

    Alpaca REST docs show endpoints such as /v2/account, but alpaca-py
    TradingClient expects a host-level base URL and appends /v2 internally.
    Accepting a user-entered trailing /v2 prevents accidental /v2/v2 requests.
    """
    raw = (base_url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw

    parts = urlsplit(raw)
    path = (parts.path or "").rstrip("/")
    if path.lower() == "/v2":
        path = ""
    normalized = urlunsplit((parts.scheme or "https", parts.netloc, path, "", ""))
    return normalized.rstrip("/") or None


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

    def __post_init__(self):
        self.api_key = (self.api_key or "").strip()
        self.secret_key = (self.secret_key or "").strip()
        self.base_url = normalize_base_url(self.base_url)


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
            self._account_id = _as_str_id(account.id)
            mode = "paper" if self.config.paper else "live"
            logger.info(
                f"Alpaca connected ({mode}), account={_id_log_prefix(self._account_id)}..., "
                f"status={account.status}"
            )
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
            logger.info(f"Alpaca order {_id_log_prefix(order_id)}... cancelled")
            return True
        except Exception as e:
            logger.error(f"Alpaca cancel order failed: {e}")
            return False

    def get_order_status(self, order_id: str) -> OrderResult:
        """Fetch one order by id and return the latest fill snapshot."""
        try:
            self._ensure_connected()
            if not str(order_id or "").strip():
                return OrderResult(success=False, message="Missing order_id")
            order = self._trading_client.get_order_by_id(str(order_id).strip())
            status = _enum_value(getattr(order, "status", ""))
            filled_qty = _num(getattr(order, "filled_qty", 0))
            avg_price = _num(getattr(order, "filled_avg_price", 0))
            raw = {
                "id": str(getattr(order, "id", "") or ""),
                "status": status,
                "filled_qty": filled_qty,
                "filled_avg_price": avg_price,
                "qty": _num(getattr(order, "qty", 0)),
                "symbol": str(getattr(order, "symbol", "") or ""),
                "side": _enum_value(getattr(order, "side", "")),
                "submitted_at": str(getattr(order, "submitted_at", "") or ""),
                "filled_at": str(getattr(order, "filled_at", "") or ""),
                "canceled_at": str(getattr(order, "canceled_at", "") or ""),
            }
            failed = status.lower() in ("rejected", "expired")
            return OrderResult(
                success=not failed,
                order_id=raw["id"],
                filled=filled_qty,
                avg_price=avg_price,
                status=status,
                message=f"Order {status}",
                raw=raw,
            )
        except Exception as e:
            err = _format_alpaca_error(e)
            logger.error("Alpaca get_order_status failed: %s", err)
            return OrderResult(success=False, message=err)

    # ==================== Query Methods ====================

    def get_account_summary(self) -> Dict[str, Any]:
        """Get account snapshot — mirrors IBKR's accountSummary shape loosely."""
        try:
            self._ensure_connected()
            acct = self._trading_client.get_account()
            account_id = _as_str_id(getattr(acct, "id", None))
            currency = getattr(acct, "currency", None) or "USD"
            buying_power = _str_attr(acct, "buying_power")
            cash = _str_attr(acct, "cash")
            portfolio_value = _str_attr(acct, "portfolio_value")
            equity = _str_attr(acct, "equity", portfolio_value)
            last_equity = _str_attr(acct, "last_equity")
            daytrade_count = _str_attr(acct, "daytrade_count", "0")
            status = _enum_value(getattr(acct, "status", ""))
            pattern_day_trader = bool(getattr(acct, "pattern_day_trader", False))
            trading_blocked = bool(getattr(acct, "trading_blocked", False))
            transfers_blocked = bool(getattr(acct, "transfers_blocked", False))
            account_blocked = bool(getattr(acct, "account_blocked", False))
            return {
                "account": account_id,
                "account_id": account_id,
                "currency": currency,
                "buying_power": buying_power,
                "cash": cash,
                "portfolio_value": portfolio_value,
                "equity": equity,
                "last_equity": last_equity,
                "daytrade_count": daytrade_count,
                "pattern_day_trader": pattern_day_trader,
                "trading_blocked": trading_blocked,
                "transfers_blocked": transfers_blocked,
                "account_blocked": account_blocked,
                "status": status,
                "paper": self.config.paper,
                "summary": {
                    "BuyingPower": {"value": buying_power, "currency": currency},
                    "Cash": {"value": cash, "currency": currency},
                    "PortfolioValue": {"value": portfolio_value, "currency": currency},
                    "Equity": {"value": equity, "currency": currency},
                    "LastEquity": {"value": last_equity, "currency": currency},
                    "DayTradeCount": {"value": daytrade_count, "currency": ""},
                    "PatternDayTrader": {"value": str(pattern_day_trader), "currency": ""},
                    "TradingBlocked": {"value": str(trading_blocked), "currency": ""},
                    "TransfersBlocked": {"value": str(transfers_blocked), "currency": ""},
                    "AccountBlocked": {"value": str(account_blocked), "currency": ""},
                    "Status": {"value": status, "currency": ""},
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
                    "asset_class": _enum_value(getattr(p, "asset_class", "")),
                    "quantity": _num(getattr(p, "qty", 0)),
                    "qty": _num(getattr(p, "qty", 0)),
                    "avgCost": _num(getattr(p, "avg_entry_price", 0)),
                    "avg_entry_price": _num(getattr(p, "avg_entry_price", 0)),
                    "marketValue": _num(getattr(p, "market_value", 0)),
                    "market_value": _num(getattr(p, "market_value", 0)),
                    "unrealizedPnL": _num(getattr(p, "unrealized_pl", 0)),
                    "unrealized_pnl": _num(getattr(p, "unrealized_pl", 0)),
                    "unrealized_plpc": _num(getattr(p, "unrealized_plpc", 0)),
                    "currentPrice": _num(getattr(p, "current_price", 0)),
                    "current_price": _num(getattr(p, "current_price", 0)),
                    "side": _enum_value(getattr(p, "side", "")),
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
                    "id": str(o.id),
                    "orderId": str(o.id),
                    "symbol": o.symbol,
                    "side": _enum_value(getattr(o, "side", "")).lower(),
                    "action": _enum_value(getattr(o, "side", "")).upper(),
                    "quantity": _num(getattr(o, "qty", 0)),
                    "qty": _num(getattr(o, "qty", 0)),
                    "notional": _num(getattr(o, "notional", 0), default=0.0),
                    "orderType": _enum_value(getattr(o, "order_type", "")),
                    "order_type": _enum_value(getattr(o, "order_type", "")),
                    "limitPrice": _num(getattr(o, "limit_price", None), default=None),
                    "limit_price": _num(getattr(o, "limit_price", None), default=None),
                    "status": _enum_value(getattr(o, "status", "")),
                    "filled": _num(getattr(o, "filled_qty", 0)),
                    "filled_qty": _num(getattr(o, "filled_qty", 0)),
                    "remaining": _num(getattr(o, "qty", 0)) - _num(getattr(o, "filled_qty", 0)),
                    "avgFillPrice": _num(getattr(o, "filled_avg_price", 0)),
                    "filled_avg_price": _num(getattr(o, "filled_avg_price", 0)),
                    "submittedAt": str(getattr(o, "submitted_at", "") or ""),
                    "submitted_at": str(getattr(o, "submitted_at", "") or ""),
                    "extendedHours": bool(getattr(o, "extended_hours", False)),
                    "extended_hours": bool(getattr(o, "extended_hours", False)),
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
