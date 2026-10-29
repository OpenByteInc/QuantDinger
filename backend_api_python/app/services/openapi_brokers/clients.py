from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urlsplit

import requests
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_json(value: Optional[dict]) -> str:
    return "" if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _webull_body_checksum(body: str) -> str:
    """Return the checksum required by Webull OpenAPI signature version 1.0."""
    # Webull mandates MD5 here as a protocol checksum; it is not used for
    # password storage, integrity decisions, or any other security boundary.
    payload = body.encode("utf-8")
    return hashlib.md5(payload, usedforsecurity=False).hexdigest().upper()  # lgtm[py/weak-sensitive-data-hashing]  # NOSONAR


def _canonical_order_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in {"filled", "filled_all", "full_filled", "completed"}:
        return "filled"
    if status in {"cancelled", "canceled", "cancelled_all", "canceled_all"}:
        return "cancelled"
    if status in {"failed", "rejected", "expired", "disabled"}:
        return "rejected"
    if status in {"filled_part", "partially_filled", "partial_filled"}:
        return "partially_filled"
    return status or "submitted"


def _data_list(payload: Any, *keys: str) -> List[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _data_list(value, *keys)
            if nested:
                return nested
    for key in ("data", "items", "list", "rows", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _data_list(value, *keys)
            if nested:
                return nested
    return []


@dataclass
class BrokerOrderResult:
    success: bool
    order_id: str = ""
    filled: float = 0.0
    avg_price: float = 0.0
    status: str = ""
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class BrokerApiError(RuntimeError):
    pass


class _HttpClient:
    def __init__(self, base_url: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = max(3, min(int(timeout or 15), 60))
        self.session = requests.Session()
        self.connected = False

    def disconnect(self) -> None:
        self.connected = False
        self.session.close()

    @staticmethod
    def _decode_response(response: requests.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerApiError(f"HTTP {response.status_code}: invalid JSON response") from exc
        if not response.ok:
            detail = payload.get("msg") or payload.get("message") or payload.get("error") if isinstance(payload, dict) else payload
            raise BrokerApiError(f"HTTP {response.status_code}: {detail}")
        return payload


@dataclass(frozen=True)
class TigerConfig:
    tiger_id: str
    account_id: str
    private_key: str
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
        if quantity <= 0:
            raise ValueError("brokerAccounts.invalidOrder")
        sdk = self._sdk()
        ticker, currency = self._instrument(symbol, market_type)
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


@dataclass(frozen=True)
class WebullConfig:
    app_key: str
    app_secret: str
    access_token: str = ""
    environment: str = "sandbox"
    account_id: str = ""
    timeout: int = 15


class WebullClient(_HttpClient):
    HOSTS = {
        "sandbox": "api.sandbox.webull.com",
        "live": "api.webull.com",
    }

    def __init__(self, config: WebullConfig):
        if not config.app_key.strip() or not config.app_secret.strip():
            raise ValueError("brokerAccounts.credentialsRequired")
        environment = config.environment.strip().lower()
        if environment not in self.HOSTS:
            raise ValueError("brokerAccounts.webull.invalidEnvironment")
        self.host = self.HOSTS[environment]
        super().__init__(f"https://{self.host}", config.timeout)
        self.config = config
        self.environment = environment
        self.account_id = config.account_id.strip()

    def build_signature(self, method: str, path: str, params: dict, body: str, timestamp: str, nonce: str) -> str:
        signing = {
            **{str(k): str(v) for k, v in (params or {}).items() if v is not None},
            "host": self.host,
            "x-app-key": self.config.app_key.strip(),
            "x-signature-algorithm": "HMAC-SHA1",
            "x-signature-nonce": nonce,
            "x-signature-version": "1.0",
            "x-timestamp": timestamp,
        }
        canonical = "&".join(f"{k}={signing[k]}" for k in sorted(signing))
        parts = [path, canonical]
        if body:
            parts.append(_webull_body_checksum(body))
        source = "&".join(parts)
        encoded = quote(source, safe="")
        # Webull declares HMAC-SHA1 in the signed protocol headers above, so
        # changing this digest would make valid requests fail authentication.
        digest = hmac.new((self.config.app_secret + "&").encode("utf-8"), encoded.encode("utf-8"), hashlib.sha1).digest()  # NOSONAR
        return base64.b64encode(digest).decode("ascii")

    def _request(self, method: str, path: str, *, params: Optional[dict] = None, body: Optional[dict] = None) -> Any:
        params = {str(k): v for k, v in (params or {}).items() if v is not None and v != ""}
        raw_query = urlencode(sorted(params.items()), doseq=True)
        raw_body = _compact_json(body)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        nonce = uuid.uuid4().hex
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Host": self.host,
            "x-app-key": self.config.app_key.strip(),
            "x-timestamp": timestamp,
            "x-signature-algorithm": "HMAC-SHA1",
            "x-signature-version": "1.0",
            "x-signature-nonce": nonce,
            "x-version": "v3",
        }
        if self.config.access_token.strip():
            headers["x-access-token"] = self.config.access_token.strip()
        headers["x-signature"] = self.build_signature(method, path, params, raw_body, timestamp, nonce)
        url = f"{self.base_url}{path}" + (f"?{raw_query}" if raw_query else "")
        response = self.session.request(method.upper(), url, headers=headers, data=raw_body or None, timeout=self.timeout)
        payload = self._decode_response(response)
        if isinstance(payload, dict):
            code = payload.get("code")
            error_code = payload.get("error_code")
            if error_code:
                raise BrokerApiError(str(payload.get("message") or error_code))
            if code not in (None, 0, "0", "SUCCESS", "success"):
                raise BrokerApiError(str(payload.get("msg") or payload.get("message") or payload))
            return payload.get("data", payload)
        return payload

    def get_accounts(self) -> List[dict]:
        payload = self._request("GET", "/trading/accounts/list")
        accounts = _data_list(payload, "accounts", "account_list")
        if not accounts and isinstance(payload, dict) and any(k in payload for k in ("account_id", "id")):
            accounts = [payload]
        return accounts

    def connect(self) -> bool:
        accounts = self.get_accounts()
        if not accounts:
            raise BrokerApiError("brokerAccounts.noAuthorizedAccounts")
        ids = [str(a.get("account_id") or a.get("id") or "") for a in accounts]
        if self.account_id and self.account_id not in ids:
            raise BrokerApiError("brokerAccounts.brokerAccountUnavailable")
        if not self.account_id:
            self.account_id = next((value for value in ids if value), "")
        self.connected = bool(self.account_id)
        return self.connected

    def get_connection_status(self) -> dict:
        return {
            "connected": self.connected,
            "account_id": self.account_id or None,
            "base_url": self.base_url,
            "paper": self.environment == "sandbox",
            "environment": self.environment,
        }

    def _account_params(self, extra: Optional[dict] = None) -> dict:
        if not self.account_id:
            raise BrokerApiError("brokerAccounts.brokerAccountRequired")
        return {"account_id": self.account_id, **(extra or {})}

    def get_account_summary(self) -> dict:
        payload = self._request("GET", "/trading/assets/balances/get", params=self._account_params())
        data = payload if isinstance(payload, dict) else {}
        return {
            **data,
            "account": self.account_id,
            "currency": data.get("currency") or data.get("base_currency") or "USD",
            "equity": data.get("total_net_liquidation_value") or data.get("net_liquidation") or data.get("equity"),
            "cash": data.get("total_cash_balance") or data.get("cash_balance") or data.get("cash"),
            "buying_power": data.get("buying_power") or data.get("margin_excess") or data.get("total_cash_balance"),
        }

    def get_positions(self, **_: Any) -> List[dict]:
        payload = self._request("GET", "/trading/assets/positions/list", params=self._account_params())
        rows = _data_list(payload, "positions", "position_list")
        return [self._normalize_position(row) for row in rows]

    def get_orders(self, status: str = "all", limit: int = 100, symbol: str = "", **_: Any) -> List[dict]:
        path = "/trading/orders/open-orders/list" if status.lower() in ("open", "pending") else "/trading/orders/historical-orders/list"
        payload = self._request("GET", path, params=self._account_params())
        rows = _data_list(payload, "orders", "order_list")
        normalized = [self._normalize_order(row) for row in rows]
        requested_status = status.strip().lower()
        if requested_status in ("filled", "completed"):
            normalized = [row for row in normalized if row.get("status") == "filled"]
        if symbol:
            requested_symbol = str(symbol).strip().upper()
            normalized = [row for row in normalized if str(row.get("symbol") or "").strip().upper() == requested_symbol]
        return normalized[:min(max(int(limit), 1), 100)]

    def _get_order_status(self, *, client_order_id: str = "", order_id: str = "") -> BrokerOrderResult:
        lookup = {"client_order_id": client_order_id} if client_order_id else {"order_id": order_id}
        payload = self._request(
            "GET", "/trading/orders/get",
            params=self._account_params(lookup),
        )
        rows = _data_list(payload, "orders", "order_list")
        raw = rows[0] if rows else (payload if isinstance(payload, dict) else {})
        normalized = self._normalize_order(raw)
        commission = raw.get("commission") if isinstance(raw.get("commission"), dict) else {}
        fees = raw.get("fees") or raw.get("fee_list") or commission.get("fees") or commission.get("fee_list") or []
        fee_breakdown = {}
        for fee in fees if isinstance(fees, list) else []:
            currency = str(fee.get("currency") or fee.get("fee_currency") or "USD").upper()
            amount = fee.get("actual_value")
            if amount is None:
                amount = fee.get("receivable_value")
            if amount is None:
                amount = fee.get("amount") or fee.get("fee")
            fee_breakdown[currency] = fee_breakdown.get(currency, 0.0) + _number(amount)
        actual = commission.get("actual_commission")
        if actual is not None and not fee_breakdown:
            fee_breakdown["USD"] = _number(actual)
        raw = {
            **raw,
            "commission": _number(actual),
            "commission_ccy": "USD" if actual is not None else "",
            "fee_breakdown": fee_breakdown,
            "fee_status": "actual" if actual is not None else "pending",
        }
        return BrokerOrderResult(
            True, order_id=str(raw.get("order_id") or order_id or client_order_id),
            filled=_number(normalized.get("filled_qty")), avg_price=_number(normalized.get("filled_avg_price")),
            status=_canonical_order_status(raw.get("status") or raw.get("order_status")), raw=raw,
        )

    def get_order_status_by_client_id(self, client_order_id: str) -> BrokerOrderResult:
        return self._get_order_status(client_order_id=str(client_order_id))

    def get_order_status(self, order_id: str) -> BrokerOrderResult:
        try:
            return self._get_order_status(client_order_id=str(order_id))
        except BrokerApiError:
            return self._get_order_status(order_id=str(order_id))

    @staticmethod
    def _normalize_position(row: dict) -> dict:
        return {
            **row,
            "symbol": row.get("symbol") or row.get("ticker"),
            "quantity": row.get("quantity") or row.get("qty") or row.get("position"),
            "avg_entry_price": row.get("avg_entry_price") or row.get("cost_price") or row.get("average_cost"),
            "current_price": row.get("current_price") or row.get("last_price") or row.get("market_price"),
            "market_value": row.get("market_value") or row.get("position_value"),
            "unrealized_pnl": row.get("unrealized_pnl") or row.get("unrealized_pl") or row.get("unrealized_profit_loss"),
        }

    @staticmethod
    def _normalize_order(row: dict) -> dict:
        client_order_id = row.get("client_order_id") or row.get("clientOrderId")
        return {
            **row,
            "id": client_order_id or row.get("order_id") or row.get("id"),
            "order_id": row.get("order_id") or row.get("id"),
            "status": _canonical_order_status(row.get("status") or row.get("order_status")),
            "quantity": row.get("quantity") or row.get("qty"),
            "filled_qty": row.get("filled_qty") or row.get("filled_quantity") or row.get("filled"),
            "filled_avg_price": row.get("filled_price") or row.get("filled_avg_price") or row.get("average_filled_price") or row.get("avg_price"),
            "submitted_at": row.get("place_time_at") or row.get("submitted_at") or row.get("create_time") or row.get("created_at"),
        }

    def _place_order(self, *, symbol: str, side: str, quantity: float, order_type: str, price: float = 0.0,
                     market_type: str = "USStock", client_order_id: str = "", **_: Any) -> BrokerOrderResult:
        if str(market_type).strip().lower() not in ("usstock", "stock", "equity", "spot"):
            raise BrokerApiError("brokerAccounts.webull.usStocksOnly")
        ticker = str(symbol or "").strip().upper()
        ticker = ticker.removeprefix("USSTOCK:").removesuffix(".US").removeprefix("US.")
        if not ticker:
            raise ValueError("brokerAccounts.invalidOrder")
        order = {
            "client_order_id": (client_order_id or uuid.uuid4().hex)[:32],
            "combo_type": "NORMAL",
            "symbol": ticker,
            "instrument_type": "EQUITY",
            "market": "US",
            "order_type": order_type.upper(),
            "quantity": str(quantity),
            "support_trading_session": "CORE",
            "side": str(side).upper(),
            "time_in_force": "DAY",
            "entrust_type": "QTY",
        }
        if order_type.upper() == "LIMIT":
            order["limit_price"] = str(price)
        payload = self._request("POST", "/trading/orders/place", body={"account_id": self.account_id, "new_orders": [order]})
        raw = payload if isinstance(payload, dict) else {"data": payload}
        rows = _data_list(raw, "orders", "order_list")
        result = rows[0] if rows else raw
        client_id = str(result.get("client_order_id") or result.get("clientOrderId") or order["client_order_id"])
        raw = {
            **raw,
            "client_order_id": client_id,
            "broker_order_id": result.get("order_id") or result.get("orderId"),
        }
        return BrokerOrderResult(
            True,
            order_id=client_id,
            status=_canonical_order_status(result.get("status") or result.get("order_status") or "submitted"),
            message="brokerAccounts.orderPlaced",
            raw=raw,
        )

    def place_market_order(self, *, symbol: str, side: str, quantity: float = 0.0, size: float = 0.0,
                           market_type: str = "USStock", client_order_id: str = "", **kwargs: Any) -> BrokerOrderResult:
        return self._place_order(symbol=symbol, side=side, quantity=quantity or size, order_type="MARKET",
                                 market_type=market_type, client_order_id=client_order_id, **kwargs)

    def place_limit_order(self, *, symbol: str, side: str, quantity: float = 0.0, size: float = 0.0,
                          price: float, market_type: str = "USStock", client_order_id: str = "", **kwargs: Any) -> BrokerOrderResult:
        return self._place_order(symbol=symbol, side=side, quantity=quantity or size, order_type="LIMIT", price=price,
                                 market_type=market_type, client_order_id=client_order_id, **kwargs)

    def cancel_order(self, order_id: str, **_: Any) -> bool:
        self._request("POST", "/trading/orders/cancel", body={"account_id": self.account_id, "client_order_id": str(order_id)})
        return True
