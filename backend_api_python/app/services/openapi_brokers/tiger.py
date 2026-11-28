from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, List

from .base import BrokerApiError, BrokerOrderResult, _number, _canonical_order_status
from app.utils.broker_order_validation import order_number


@dataclass(frozen=True)
class TigerConfig:
    tiger_id: str
    account_id: str
    private_key: str = field(repr=False)
    license: str = ""
    environment: str = "live"
    timeout: int = 15


class TigerClient:
    def __init__(self, config: TigerConfig):
        if not config.tiger_id.strip() or not config.account_id.strip() or not config.private_key.strip():
            raise ValueError("brokerAccounts.credentialsRequired")
        environment = config.environment.strip().lower()
        if environment not in ("live", "paper"):
            raise ValueError("brokerAccounts.tiger.invalidEnvironment")
        self.config = config
        self.account_id = config.account_id.strip()
        self.environment = environment
        self.connected = False
        self.client = None

    @staticmethod
    def _sdk():
        try:
            from tigeropen.common.consts import Currency, Market, SecurityType
            from tigeropen.common.util.contract_utils import stock_contract
            from tigeropen.common.util.order_utils import limit_order, market_order
            from tigeropen.tiger_open_config import TigerOpenClientConfig
            from tigeropen.trade.trade_client import TradeClient
            return {
                "Currency": Currency,
                "Market": Market,
                "SecurityType": SecurityType,
                "stock_contract": stock_contract,
                "limit_order": limit_order,
                "market_order": market_order,
                "TigerOpenClientConfig": TigerOpenClientConfig,
                "TradeClient": TradeClient,
            }
        except ImportError as exc:
            raise BrokerApiError("brokerAccounts.tiger.sdkUnavailable") from exc

    def _ensure_client(self):
        if self.client is not None:
            return self.client
        sdk = self._sdk()
        client_config = sdk["TigerOpenClientConfig"](
            sandbox_debug=False,
            enable_dynamic_domain=False,
        )
        client_config.tiger_id = self.config.tiger_id.strip()
        client_config.account = self.account_id
        client_config.private_key = self.config.private_key.strip()
        client_config.is_paper = self.environment == "paper"
        if self.config.license.strip():
            client_config.license = self.config.license.strip()
        client_config.timeout = max(3, min(int(self.config.timeout or 15), 60))
        if hasattr(client_config, "query_domains") and hasattr(client_config, "refresh_server_info"):
            client_config.enable_dynamic_domain = True
            client_config.domain_conf = client_config.query_domains()
            client_config.refresh_server_info()
        self.client = sdk["TradeClient"](client_config)
        return self.client

    def connect(self) -> bool:
        assets = self._ensure_client().get_assets(account=self.account_id, segment=False, market_value=False)
        if not assets:
            raise BrokerApiError("brokerAccounts.brokerAccountUnavailable")
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.client = None
        self.connected = False

    def get_connection_status(self) -> dict:
        return {
            "connected": self.connected,
            "account_id": self.account_id,
            "paper": self.environment == "paper",
            "environment": self.environment,
            "license": self.config.license or None,
        }

    @staticmethod
    def _status(value: Any) -> str:
        return str(getattr(value, "value", None) or getattr(value, "name", None) or value or "")

    @staticmethod
    def _contract_fields(contract: Any) -> dict:
        return {
            "symbol": getattr(contract, "symbol", None),
            "currency": getattr(contract, "currency", None),
            "market": getattr(contract, "market", None),
            "exchange": getattr(contract, "exchange", None),
        }

    @staticmethod
    def _charge_breakdown(order: Any) -> dict:
        currency = str(getattr(getattr(order, "contract", None), "currency", None) or "USD").upper()
        charge_total = 0.0
        for charge in getattr(order, "charges", None) or []:
            charge_total += _number(getattr(charge, "total", None))
        amount = charge_total or (_number(getattr(order, "commission", None)) + _number(getattr(order, "gst", None)))
        return {currency: amount} if amount else {}

    def get_account_summary(self) -> dict:
        assets = self._ensure_client().get_assets(account=self.account_id, segment=False, market_value=True) or []
        if not assets:
            raise BrokerApiError("brokerAccounts.brokerAccountUnavailable")
        summary = getattr(assets[0], "summary", None)
        values = dict(getattr(summary, "__dict__", {}) or {})
        return {
            **values,
            "account": self.account_id,
            "currency": values.get("currency") or "USD",
            "equity": values.get("net_liquidation") or values.get("equity_with_loan"),
            "cash": values.get("cash"),
            "buying_power": values.get("buying_power") or values.get("available_funds"),
        }

    def get_positions(self, **_: Any) -> List[dict]:
        sdk = self._sdk()
        positions = self._ensure_client().get_positions(
            account=self.account_id,
            sec_type=sdk["SecurityType"].STK,
            currency=sdk["Currency"].ALL,
            market=sdk["Market"].ALL,
        ) or []
        rows = []
        for position in positions:
            contract = self._contract_fields(getattr(position, "contract", None))
            rows.append({
                **contract,
                "quantity": getattr(position, "position_qty", None) or getattr(position, "quantity", 0),
                "avg_entry_price": getattr(position, "average_cost", None),
                "current_price": getattr(position, "market_price", None),
                "market_value": getattr(position, "market_value", None),
                "realized_pnl": getattr(position, "realized_pnl", None),
                "unrealized_pnl": getattr(position, "unrealized_pnl", None),
                "salable_qty": getattr(position, "salable_qty", None),
            })
        return rows

    def _normalize_order(self, order: Any) -> dict:
        contract = self._contract_fields(getattr(order, "contract", None))
        status = self._status(getattr(order, "status", None))
        fees = self._charge_breakdown(order)
        commission = sum(fees.values()) if fees else _number(getattr(order, "commission", None))
        order_type = self._status(getattr(order, "order_type", None)).lower()
        if order_type == "lmt":
            order_type = "limit"
        elif order_type == "mkt":
            order_type = "market"
        return {
            **contract,
            "id": str(getattr(order, "id", None) or getattr(order, "order_id", None) or ""),
            "order_id": str(getattr(order, "id", None) or getattr(order, "order_id", None) or ""),
            "account_order_id": getattr(order, "order_id", None),
            "side": str(getattr(order, "action", None) or "").lower(),
            "order_type": order_type,
            "status": _canonical_order_status(status),
            "quantity": getattr(order, "quantity", None),
            "filled_qty": getattr(order, "filled", None),
            "remaining_qty": getattr(order, "remaining", None),
            "filled_avg_price": getattr(order, "avg_fill_price", None),
            "limit_price": getattr(order, "limit_price", None),
            "realized_pnl": getattr(order, "realized_pnl", None),
            "commission": commission,
            "commission_ccy": next(iter(fees), contract.get("currency") or "USD"),
            "fee_breakdown": fees,
            "fee_status": "actual" if fees else "pending",
            "submitted_at": getattr(order, "order_time", None),
            "updated_at": getattr(order, "update_time", None),
            "filled_at": getattr(order, "trade_time", None),
            "reason": getattr(order, "reason", None),
        }

    def get_orders(self, status: str = "all", limit: int = 100, symbol: str = "", **_: Any) -> List[dict]:
        symbol = self._query_symbol(symbol)
        sdk = self._sdk()
        normalized_status = status.lower()
        if normalized_status in ("open", "pending"):
            method = "get_open_orders"
        elif normalized_status in ("filled", "completed"):
            method = "get_filled_orders"
        elif normalized_status in ("cancelled", "canceled"):
            method = "get_cancelled_orders"
        else:
            method = "get_orders"
        params = {
            "account": self.account_id,
            "sec_type": sdk["SecurityType"].STK,
            "market": sdk["Market"].ALL,
            "symbol": symbol or None,
        }
        if method == "get_orders":
            params["limit"] = min(max(int(limit), 1), 100)
        orders = getattr(self._ensure_client(), method)(
            **params,
        ) or []
        return [self._normalize_order(order) for order in orders[:min(max(int(limit), 1), 100)]]

    def get_transactions(self, symbol: str = "", limit: int = 100) -> List[dict]:
        symbol = self._query_symbol(symbol)
        sdk = self._sdk()
        transactions = self._ensure_client().get_transactions(
            account=self.account_id,
            symbol=symbol or None,
            sec_type=sdk["SecurityType"].STK,
            limit=min(max(int(limit), 1), 100),
        ) or []
        rows = []
        for transaction in transactions:
            contract = self._contract_fields(getattr(transaction, "contract", None))
            rows.append({
                **contract,
                "id": str(getattr(transaction, "id", None) or ""),
                "order_id": str(getattr(transaction, "order_id", None) or ""),
                "side": str(getattr(transaction, "action", None) or "").lower(),
                "filled_qty": getattr(transaction, "filled_quantity", None),
                "filled_avg_price": getattr(transaction, "filled_price", None),
                "filled_amount": getattr(transaction, "filled_amount", None),
                "filled_at": getattr(transaction, "transacted_at", None),
            })
        return rows

    def get_order_status(self, order_id: str) -> BrokerOrderResult:
        order = self._ensure_client().get_order(
            account=self.account_id,
            id=int(order_id),
            show_charges=True,
        )
        if order is None:
            raise BrokerApiError("brokerAccounts.orderUnavailable")
        raw = self._normalize_order(order)
        return BrokerOrderResult(
            True,
            order_id=raw["id"],
            filled=_number(raw.get("filled_qty")),
            avg_price=_number(raw.get("filled_avg_price")),
            status=raw["status"],
            raw=raw,
        )

    def get_order_status_by_client_id(self, client_order_id: str) -> BrokerOrderResult:
        orders = self._ensure_client().get_orders(account=self.account_id, limit=100) or []
        match = next((item for item in orders if str(getattr(item, "user_mark", None) or "") == str(client_order_id)), None)
        if match is None:
            raise BrokerApiError("brokerAccounts.orderUnavailable")
        return self.get_order_status(str(getattr(match, "id", None) or ""))

    @staticmethod
    def _query_symbol(symbol: str) -> str:
        from .symbols import equity_symbol
        value = equity_symbol(symbol)
        return value.removesuffix(".HK") if value.endswith(".HK") else value

    @staticmethod
    def _instrument(symbol: str, market_type: str) -> tuple[str, str]:
        value = str(symbol or "").strip().upper()
        value = value.removeprefix("USSTOCK:").removeprefix("HKSTOCK:")
        is_hk = str(market_type or "").strip().lower() in ("hkstock", "hk", "hongkong") or value.endswith(".HK")
        if is_hk:
            value = value.removesuffix(".HK").removeprefix("HK.")
            return (value.zfill(5) if value.isdigit() else value, "HKD")
        return value.removesuffix(".US").removeprefix("US."), "USD"

    def _place_order(self, *, symbol: str, side: str, quantity: float, order_type: str, price: float = 0.0,
                     market_type: str = "USStock", client_order_id: str = "", **_: Any) -> BrokerOrderResult:
        quantity = order_number(quantity)
        price = order_number(price, allow_zero=order_type != "limit")
        ticker, currency = self._instrument(symbol, market_type)
        if currency == "HKD":
            from app.services.market.hk_board_lots import validate_board_lot
            validate_board_lot(ticker, quantity)
        sdk = self._sdk()
        contract = sdk["stock_contract"](ticker, currency)
        action = str(side or "").strip().upper()
        if action not in ("BUY", "SELL"):
            raise ValueError("brokerAccounts.invalidOrder")
        if order_type == "limit":
            order = sdk["limit_order"](self.account_id, contract, action, quantity, float(price), time_in_force="DAY")
        else:
            order = sdk["market_order"](self.account_id, contract, action, quantity, time_in_force="DAY")
        order.user_mark = (client_order_id or uuid.uuid4().hex)[:64]
        order_id = self._ensure_client().place_order(order)
        raw = self._normalize_order(order)
        raw["id"] = str(order_id or getattr(order, "id", None) or "")
        raw["order_id"] = raw["id"]
        return BrokerOrderResult(
            True,
            order_id=raw["id"],
            status=raw.get("status") or "submitted",
            message="brokerAccounts.orderPlaced",
            raw=raw,
        )

    def normalize_strategy_quantity(self, symbol, quantity):
        from decimal import Decimal, ROUND_DOWN
        from app.services.market.hk_board_lots import board_lot_rule
        lot = Decimal(board_lot_rule(symbol)["lot_size"])
        result = (Decimal(str(quantity)) / lot).to_integral_value(rounding=ROUND_DOWN) * lot
        if result <= 0:
            raise ValueError("brokerAccounts.forthright.belowBoardLot")
        return float(result)

    def place_market_order(self, *, symbol: str, side: str, quantity: float = 0.0, size: float = 0.0,
                           market_type: str = "USStock", client_order_id: str = "", **kwargs: Any) -> BrokerOrderResult:
        return self._place_order(symbol=symbol, side=side, quantity=quantity or size, order_type="market",
                                 market_type=market_type, client_order_id=client_order_id, **kwargs)

    def place_limit_order(self, *, symbol: str, side: str, quantity: float = 0.0, size: float = 0.0,
                          price: float, market_type: str = "USStock", client_order_id: str = "", **kwargs: Any) -> BrokerOrderResult:
        return self._place_order(symbol=symbol, side=side, quantity=quantity or size, order_type="limit", price=price,
                                 market_type=market_type, client_order_id=client_order_id, **kwargs)

    def cancel_order(self, order_id: str, **_: Any) -> bool:
        return bool(self._ensure_client().cancel_order(account=self.account_id, id=int(order_id)))
