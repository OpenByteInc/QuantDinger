from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional
from urllib.parse import quote, urlencode

import requests

from .base import (BrokerApiError, BrokerOrderResult, _HttpClient, _number,
                   _compact_json, _canonical_order_status, _data_list)


def _webull_body_checksum(body: str) -> str:
    """Return the checksum required by Webull OpenAPI signature version 1.0."""
    # Webull mandates MD5(body) inside its HMAC-SHA1 signing input over HTTPS.
    # This is not password storage; replacing the digest breaks authentication.
    # https://developer.webull.com/apis/docs/authentication/signature/
    payload = body.encode("utf-8")
    return hashlib.md5(payload, usedforsecurity=False).hexdigest().upper()  # lgtm[py/weak-sensitive-data-hashing]  # NOSONAR


@dataclass(frozen=True)
class WebullConfig:
    app_key: str
    app_secret: str = field(repr=False)
    access_token: str = field(default="", repr=False)
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
