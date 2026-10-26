"""
Alpaca Trading Client

Uses alpaca-py SDK to interact with Alpaca Markets REST API.
Supports US stocks, ETFs, and crypto on both paper and live accounts.
"""

import time
import threading
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Optional, Dict, Any, List, Union
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import requests

from app.utils.logger import get_logger
from app.services.alpaca_trading.symbols import parse_symbol

logger = get_logger(__name__)


_ALPACA_TRADING_HOSTS = {
    "api.alpaca.markets",
    "paper-api.alpaca.markets",
}


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


def _alpaca_error_status(message: str) -> Optional[int]:
    code_match = re.search(r'["\']?code["\']?\s*[:=]\s*(\d+)', message, re.IGNORECASE)
    if code_match:
        return int(code_match.group(1)[:3])
    status_match = re.search(
        r'\b(?:http(?:\s+status)?|status(?:\s+code)?|code)\s*[:=]?\s*(400|401|403)\b',
        message,
        re.IGNORECASE,
    )
    return int(status_match.group(1)) if status_match else None


def _normalize_equity_price(value: Any) -> float:
    price = Decimal(str(value or 0))
    if price <= 0:
        return 0.0
    increment = Decimal("0.01") if price >= Decimal("1") else Decimal("0.0001")
    return float(price.quantize(increment, rounding=ROUND_HALF_UP))


def _format_alpaca_error(err: Exception, *, context: str = "") -> str:
    """Turn Alpaca SDK/HTTP errors into actionable messages for operators."""
    msg = str(err or "").strip()
    low = msg.lower()
    status = _alpaca_error_status(msg)
    prefix = f"{context}: " if context else ""
    if "invalid syntax" in low or status == 400:
        return (
            prefix
            + "Alpaca 返回 400 invalid syntax。若使用行情 WebSocket，请确认："
            "① 连接后 10 秒内发送 {\"action\":\"auth\",\"key\":\"...\",\"secret\":\"...\"}；"
            "② 订阅格式为 {\"action\":\"subscribe\",\"trades\":[\"AAPL\"]}（美股）或 "
            "{\"action\":\"subscribe\",\"trades\":[\"BTC/USD\"]}（加密，勿用 BTC/USDT）。"
            "本系统「测试连接」仅走 REST 交易接口，不经过该 WebSocket。"
        )
    if status == 401 or "auth failed" in low or "not authenticated" in low or "unauthorized" in low:
        return prefix + f"Alpaca authentication failed. Check API Key/Secret and paper(PK*)/live(AK*) mode. Raw error: {msg}"
    if status == 403:
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
    scheme = (parts.scheme or "").lower()
    hostname = (parts.hostname or "").lower().rstrip(".")
    if scheme != "https":
        raise ValueError("Alpaca base URL must use HTTPS")
    if hostname not in _ALPACA_TRADING_HOSTS:
        raise ValueError("Alpaca base URL must use an official Alpaca trading host")
    if parts.username or parts.password or parts.port is not None:
        raise ValueError("Alpaca base URL must not contain credentials or a custom port")
    path = (parts.path or "").rstrip("/")
    if path.lower() == "/v2":
        path = ""
    elif path:
        raise ValueError("Alpaca base URL must not contain a path")
    normalized = urlunsplit(("https", hostname, path, "", ""))
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
                MarketOrderRequest, LimitOrderRequest, GetOrdersRequest,
                TakeProfitRequest, StopLossRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus, OrderClass
            from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest
            _alpaca_modules = {
                "TradingClient": TradingClient,
                "MarketOrderRequest": MarketOrderRequest,
                "LimitOrderRequest": LimitOrderRequest,
                "GetOrdersRequest": GetOrdersRequest,
                "TakeProfitRequest": TakeProfitRequest,
                "StopLossRequest": StopLossRequest,
                "OrderSide": OrderSide,
                "TimeInForce": TimeInForce,
                "QueryOrderStatus": QueryOrderStatus,
                "OrderClass": OrderClass,
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

    def _trading_base_url(self) -> str:
        expected_host = "paper-api.alpaca.markets" if self.config.paper else "api.alpaca.markets"
        base_url = normalize_base_url(self.config.base_url)
        if base_url and (urlsplit(base_url).hostname or "").lower() != expected_host:
            raise ValueError("Alpaca base URL does not match the selected account environment")
        return base_url or f"https://{expected_host}"

    def connect(self) -> bool:
        """Initialize Alpaca client and verify credentials by fetching account."""
        try:
            api_key = (self.config.api_key or "").strip()
            secret_key = (self.config.secret_key or "").strip()
            if not api_key or not secret_key:
                logger.error("Alpaca connect failed: empty api_key or secret_key")
                return False

            base_url = self._trading_base_url()
            modules = _ensure_alpaca()

            self._trading_client = modules["TradingClient"](
                api_key=api_key,
                secret_key=secret_key,
                paper=self.config.paper,
                url_override=base_url,
            )
            # Paper trading keys use the standard Alpaca market-data endpoint.
            # The SDK sandbox flag targets a separate data sandbox and rejects
            # normal PK-prefixed paper credentials.
            data_sandbox = False
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

    @staticmethod
    def _order_commission_snapshot(order: Any) -> Dict[str, Any]:
        commission = _num(getattr(order, "commission", 0))
        if commission <= 0:
            return {"commission": 0.0, "commission_ccy": ""}
        return {
            "commission": abs(commission),
            "commission_ccy": str(getattr(order, "commission_currency", "") or "USD").upper(),
            "commission_type": _enum_value(getattr(order, "commission_type", "")),
        }

    @classmethod
    def _order_fee_history_snapshot(cls, order: Any) -> Dict[str, Any]:
        snapshot = cls._order_commission_snapshot(order)
        commission = abs(float(snapshot.get("commission") or 0.0))
        currency = str(snapshot.get("commission_ccy") or "").strip().upper()
        if commission > 0 and currency:
            return {
                **snapshot,
                "commission_quote": commission if currency == "USD" else None,
                "fees_by_ccy": {currency: commission},
                "fee_status": "actual",
            }
        asset_class = _enum_value(getattr(order, "asset_class", "")).strip().lower()
        status = _enum_value(getattr(order, "status", "")).strip().lower()
        if status == "filled" and asset_class in {"us_equity", "equity"}:
            return {
                "commission": 0.0,
                "commission_ccy": "USD",
                "commission_quote": 0.0,
                "fees_by_ccy": {"USD": 0.0},
                "fee_status": "actual_zero",
            }
        return {
            "commission": 0.0,
            "commission_ccy": "",
            "commission_quote": None,
            "fees_by_ccy": {},
            "fee_status": "pending",
        }

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float = 0.0,
        market_type: str = "USStock",
        take_profit_price: float = 0.0,
        stop_loss_price: float = 0.0,
        client_order_id: str = "",
        notional: float = 0.0,
    ) -> OrderResult:
        """Place a market order. market_type: 'USStock' or 'crypto'."""
        try:
            self._ensure_connected()
            modules = _ensure_alpaca()
            sym, asset_class = parse_symbol(symbol, market_hint=_market_hint_from_type(market_type))
            requested_quantity = float(quantity or 0.0)
            requested_notional = float(notional or 0.0)
            if (requested_quantity > 0) == (requested_notional > 0):
                raise ValueError("Provide exactly one of quantity or notional")
            if asset_class == "us_equity":
                asset = self._trading_client.get_asset(sym)
                if not bool(getattr(asset, "tradable", False)):
                    raise ValueError("quickTrade.alpacaAssetNotTradable")
                if requested_notional > 0 and not bool(getattr(asset, "fractionable", False)):
                    raise ValueError("quickTrade.alpacaFractionalNotSupported")
            if side.lower() == "sell" and requested_quantity > 0:
                positions = self._trading_client.get_all_positions()
                matching = next(
                    (
                        position
                        for position in positions
                        if str(getattr(position, "symbol", "") or "").replace("/", "").upper()
                        == sym.replace("/", "").upper()
                    ),
                    None,
                )
                available_quantity = _num(getattr(matching, "qty", 0)) if matching else 0.0
                if 0 < available_quantity < requested_quantity:
                    quantity = available_quantity

            request_kwargs = {
                "symbol": sym,
                "side": modules["OrderSide"].BUY if side.lower() == "buy" else modules["OrderSide"].SELL,
                "time_in_force": modules["TimeInForce"].GTC if asset_class == "crypto" else modules["TimeInForce"].DAY,
            }
            if requested_notional > 0:
                request_kwargs["notional"] = requested_notional
            else:
                request_kwargs["qty"] = quantity
            if str(client_order_id or "").strip():
                request_kwargs["client_order_id"] = str(client_order_id).strip()[:48]
            take_profit_price = float(take_profit_price or 0.0)
            stop_loss_price = float(stop_loss_price or 0.0)
            if asset_class == "us_equity" and (take_profit_price > 0 or stop_loss_price > 0):
                take_profit_price = _normalize_equity_price(take_profit_price)
                stop_loss_price = _normalize_equity_price(stop_loss_price)
                request_kwargs["order_class"] = (
                    modules["OrderClass"].BRACKET
                    if take_profit_price > 0 and stop_loss_price > 0
                    else modules["OrderClass"].OTO
                )
                if take_profit_price > 0:
                    request_kwargs["take_profit"] = modules["TakeProfitRequest"](limit_price=take_profit_price)
                if stop_loss_price > 0:
                    request_kwargs["stop_loss"] = modules["StopLossRequest"](stop_price=stop_loss_price)
            req = modules["MarketOrderRequest"](
                **request_kwargs,
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
                    "requested_qty": requested_quantity,
                    "submitted_qty": float(quantity) if requested_quantity > 0 else 0.0,
                    "requested_notional": requested_notional,
                    "submitted_notional": requested_notional,
                    "submitted_at": str(order.submitted_at),
                    "client_order_id": str(getattr(order, "client_order_id", "") or client_order_id),
                    **self._order_commission_snapshot(order),
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
        take_profit_price: float = 0.0,
        stop_loss_price: float = 0.0,
        client_order_id: str = "",
    ) -> OrderResult:
        """Place a limit order. extended_hours=True for pre/post-market."""
        try:
            self._ensure_connected()
            modules = _ensure_alpaca()
            sym, asset_class = parse_symbol(symbol, market_hint=_market_hint_from_type(market_type))
            if side.lower() == "sell":
                positions = self._trading_client.get_all_positions()
                matching = next(
                    (
                        position
                        for position in positions
                        if str(getattr(position, "symbol", "") or "").replace("/", "").upper()
                        == sym.replace("/", "").upper()
                    ),
                    None,
                )
                available_quantity = _num(getattr(matching, "qty", 0)) if matching else 0.0
                if 0 < available_quantity < float(quantity):
                    quantity = available_quantity

            request_kwargs = {
                "symbol": sym,
                "qty": quantity,
                "side": modules["OrderSide"].BUY if side.lower() == "buy" else modules["OrderSide"].SELL,
                "time_in_force": modules["TimeInForce"].GTC if asset_class == "crypto" else modules["TimeInForce"].DAY,
                "limit_price": price,
                "extended_hours": extended_hours if asset_class == "us_equity" else False,
            }
            if str(client_order_id or "").strip():
                request_kwargs["client_order_id"] = str(client_order_id).strip()[:48]
            if asset_class == "us_equity":
                request_kwargs["limit_price"] = _normalize_equity_price(price)
            take_profit_price = float(take_profit_price or 0.0)
            stop_loss_price = float(stop_loss_price or 0.0)
            if asset_class == "us_equity" and (take_profit_price > 0 or stop_loss_price > 0):
                take_profit_price = _normalize_equity_price(take_profit_price)
                stop_loss_price = _normalize_equity_price(stop_loss_price)
                request_kwargs["order_class"] = (
                    modules["OrderClass"].BRACKET
                    if take_profit_price > 0 and stop_loss_price > 0
                    else modules["OrderClass"].OTO
                )
                if take_profit_price > 0:
                    request_kwargs["take_profit"] = modules["TakeProfitRequest"](limit_price=take_profit_price)
                if stop_loss_price > 0:
                    request_kwargs["stop_loss"] = modules["StopLossRequest"](stop_price=stop_loss_price)
            req = modules["LimitOrderRequest"](**request_kwargs)
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
                    "client_order_id": str(getattr(order, "client_order_id", "") or client_order_id),
                    **self._order_commission_snapshot(order),
                },
            )
        except Exception as e:
            logger.error("Alpaca limit order failed: %s", _format_alpaca_error(e))
            return OrderResult(success=False, message=_format_alpaca_error(e))

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        try:
            self._ensure_connected()
            order = self._trading_client.get_order_by_id(order_id)
            status = _enum_value(getattr(order, "status", "")).strip().lower()
            if status not in {"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding"}:
                return False
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
                **self._order_fee_history_snapshot(order),
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

    def get_order_status_by_client_id(self, client_order_id: str) -> OrderResult:
        """Fetch an order using Alpaca's idempotent client order id."""
        try:
            self._ensure_connected()
            requested = str(client_order_id or "").strip()
            if not requested:
                return OrderResult(success=False, message="Missing client_order_id")
            order = self._trading_client.get_order_by_client_id(requested)
            return self.get_order_status(str(getattr(order, "id", "") or ""))
        except Exception as e:
            err = _format_alpaca_error(e)
            logger.error("Alpaca get_order_status_by_client_id failed: %s", err)
            return OrderResult(success=False, message=err)

    def get_account_activities(
        self,
        *,
        activity_types: List[str],
        after: str = "",
        until: str = "",
        date: str = "",
        direction: str = "asc",
        page_size: int = 100,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return paginated Trading API account activities as plain dictionaries."""
        self._ensure_connected()
        types = ",".join(
            sorted({str(value or "").strip().upper() for value in activity_types if str(value or "").strip()})
        )
        if not types:
            return []
        host = self._trading_base_url()
        url = f"{host.rstrip('/')}/v2/account/activities"
        params: Dict[str, Any] = {
            "activity_types": types,
            "direction": "desc" if str(direction or "").strip().lower() == "desc" else "asc",
            "page_size": max(1, min(int(page_size or 100), 100)),
        }
        if date:
            params["date"] = str(date)
        else:
            if after:
                params["after"] = str(after)
            if until:
                params["until"] = str(until)
        headers = {
            "APCA-API-KEY-ID": self.config.api_key,
            "APCA-API-SECRET-KEY": self.config.secret_key,
            "Accept": "application/json",
        }
        output: List[Dict[str, Any]] = []
        for _ in range(max(1, int(max_pages or 1))):
            response = requests.get(url, params=params, headers=headers, timeout=self.config.timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError(f"Unexpected Alpaca activities response: {payload}")
            rows = [dict(row) for row in payload if isinstance(row, dict)]
            output.extend(rows)
            if len(rows) < params["page_size"]:
                break
            token = str(rows[-1].get("id") or rows[-1].get("ref_id") or "").strip()
            if not token or token == params.get("page_token"):
                break
            params["page_token"] = token
        return output

    def get_fill_history(
        self,
        *,
        symbol: str = "",
        limit: int = 100,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return account-owned Alpaca executions, one row per fill activity."""
        requested_limit = max(1, min(int(limit or 100), 500))
        requested_symbol = str(symbol or "").replace("/", "").replace("-", "").upper()
        activities = self.get_account_activities(
            activity_types=["FILL"],
            direction="desc",
            page_size=100,
            max_pages=max_pages,
        )
        output: List[Dict[str, Any]] = []
        for activity in activities:
            row_symbol = str(activity.get("symbol") or "").replace("/", "").replace("-", "").upper()
            if requested_symbol and row_symbol != requested_symbol:
                continue
            quantity = abs(_num(activity.get("qty") or activity.get("quantity")))
            price = _num(activity.get("price") or activity.get("avg_price"))
            activity_id = str(
                activity.get("id")
                or activity.get("activity_id")
                or activity.get("execution_id")
                or ""
            ).strip()
            side = str(activity.get("side") or "").strip().lower()
            if not activity_id or not row_symbol or quantity <= 0 or price <= 0 or side not in {"buy", "sell"}:
                continue
            executed_at = str(
                activity.get("transaction_time")
                or activity.get("executed_at")
                or activity.get("at")
                or ""
            )
            output.append({
                "id": f"exchange-alpaca-{activity_id}",
                "exchange_fill_id": activity_id,
                "exchange_order_id": str(activity.get("order_id") or ""),
                "client_order_id": str(activity.get("client_order_id") or ""),
                "symbol": row_symbol,
                "side": side,
                "order_type": "fill",
                "status": "filled",
                "market_type": "spot",
                "amount": quantity * price,
                "filled_amount": quantity,
                "avg_fill_price": price,
                "commission": 0.0,
                "commission_ccy": "USD",
                "commission_quote": None,
                "fees_by_ccy": {},
                "fee_status": "pending",
                "realized_pnl": None,
                "source": "exchange",
                "filled_at": executed_at,
                "created_at": executed_at,
                "activity_type": str(activity.get("activity_type") or "FILL"),
                "fill_type": str(activity.get("type") or "fill"),
            })
            if len(output) >= requested_limit:
                break
        return output

    # ==================== Query Methods ====================

    def get_account_summary(self) -> Dict[str, Any]:
        """Get account snapshot — mirrors IBKR's accountSummary shape loosely."""
        try:
            self._ensure_connected()
            acct = self._trading_client.get_account()
            account_id = _as_str_id(getattr(acct, "id", None))
            currency = getattr(acct, "currency", None) or "USD"
            buying_power = _str_attr(acct, "buying_power")
            regt_buying_power = _str_attr(acct, "regt_buying_power")
            daytrading_buying_power = _str_attr(acct, "daytrading_buying_power")
            non_marginable_buying_power = _str_attr(acct, "non_marginable_buying_power")
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
                "regt_buying_power": regt_buying_power,
                "daytrading_buying_power": daytrading_buying_power,
                "non_marginable_buying_power": non_marginable_buying_power,
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
                    "RegTBuyingPower": {"value": regt_buying_power, "currency": currency},
                    "DayTradingBuyingPower": {"value": daytrading_buying_power, "currency": currency},
                    "NonMarginableBuyingPower": {"value": non_marginable_buying_power, "currency": currency},
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

    def get_positions(self, *, raise_on_error: bool = False) -> List[Dict[str, Any]]:
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
            if raise_on_error:
                raise
            return []

    def get_orders(self, status: str = "all", limit: int = 100, *, raise_on_error: bool = False) -> List[Dict[str, Any]]:
        """Get recent orders, including filled orders by default."""
        try:
            self._ensure_connected()
            modules = _ensure_alpaca()
            query_status = (
                modules["QueryOrderStatus"].OPEN
                if str(status or "").strip().lower() == "open"
                else modules["QueryOrderStatus"].ALL
            )
            req = modules["GetOrdersRequest"](
                status=query_status,
                limit=max(1, min(int(limit), 500)),
            )
            orders = self._trading_client.get_orders(filter=req)
            return [
                {
                    "id": str(o.id),
                    "orderId": str(o.id),
                    "symbol": o.symbol,
                    "asset_class": _enum_value(getattr(o, "asset_class", "")),
                    "side": _enum_value(getattr(o, "side", "")).lower(),
                    "action": _enum_value(getattr(o, "side", "")).upper(),
                    "quantity": _num(getattr(o, "qty", None), default=None),
                    "qty": _num(getattr(o, "qty", None), default=None),
                    "notional": _num(getattr(o, "notional", 0), default=0.0),
                    "orderType": _enum_value(getattr(o, "order_type", None) or getattr(o, "type", "")),
                    "order_type": _enum_value(getattr(o, "order_type", None) or getattr(o, "type", "")),
                    "limitPrice": _num(getattr(o, "limit_price", None), default=None),
                    "limit_price": _num(getattr(o, "limit_price", None), default=None),
                    "status": _enum_value(getattr(o, "status", "")),
                    "filled": _num(getattr(o, "filled_qty", 0)),
                    "filled_qty": _num(getattr(o, "filled_qty", 0)),
                    "remaining": (
                        max(0.0, _num(o.qty) - _num(getattr(o, "filled_qty", 0)))
                        if getattr(o, "qty", None) is not None else None
                    ),
                    "avgFillPrice": _num(getattr(o, "filled_avg_price", None), default=None),
                    "filled_avg_price": _num(getattr(o, "filled_avg_price", None), default=None),
                    "submittedAt": str(getattr(o, "submitted_at", "") or ""),
                    "submitted_at": str(getattr(o, "submitted_at", "") or ""),
                    "filled_at": str(getattr(o, "filled_at", "") or ""),
                    "created_at": str(getattr(o, "created_at", "") or ""),
                    "extendedHours": bool(getattr(o, "extended_hours", False)),
                    "extended_hours": bool(getattr(o, "extended_hours", False)),
                    **self._order_fee_history_snapshot(o),
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"Alpaca get_orders failed: {e}")
            if raise_on_error:
                raise
            return []

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Backward-compatible open-order query."""
        return self.get_orders(status="open", limit=500)

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
            "base_url": self._trading_base_url(),
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
