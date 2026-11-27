"""Forthright signed OpenAPI adapter for securities and dated futures."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import time
import threading
import uuid
import requests
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from .base import BrokerApiError, BrokerOrderResult, _HttpClient

_request_lock = threading.Lock()
_next_request = {}

GATEWAYS = {
    "sandbox": "https://test-openapi-gw.techfx88.com",
    "live": "https://openapi-gw.forthright-sec.com.hk",
}


def gateway_url(config: dict) -> str:
    selection = config.get("gateway_environment")
    if selection is not None:
        if selection not in GATEWAYS:
            raise ValueError("brokerAccounts.forthright.invalidEnvironment")
        return GATEWAYS[selection]
    # Older credentials used one deployment gateway, even when labelled live.
    return os.getenv("FORTHRIGHT_GATEWAY_URL", "").strip() or GATEWAYS["sandbox"]


def gateway_environment(config: dict) -> str:
    return "live" if gateway_url(config).rstrip("/") == GATEWAYS["live"] else "sandbox"


@dataclass(frozen=True)
class ForthrightConfig:
    app_id: str
    account_id: str
    private_key: str = field(repr=False)
    trade_password: str = field(repr=False)
    account_type: str = "securities"
    environment: str = "live"
    currency: str = "USD"
    timeout: int = 15
    gateway_environment: str | None = None


class ForthrightClient(_HttpClient):
    exchange_id = "forthright"

    def __init__(self, config: ForthrightConfig, *, strategy_units=False):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", config.app_id):
            raise ValueError("brokerAccounts.credentialsRequired")
        if not config.trade_password:
            raise ValueError("brokerAccounts.credentialsRequired")
        if config.account_type not in {"securities", "futures"}:
            raise ValueError("brokerAccounts.forthright.invalidAccountType")
        if config.environment not in {"live", "sandbox"}:
            raise ValueError("brokerAccounts.forthright.invalidEnvironment")
        if config.currency not in {"USD", "HKD", "CNY"}:
            raise ValueError("brokerAccounts.invalidOrder")
        try:
            self._key = serialization.load_pem_private_key(config.private_key.encode(), password=None)
            if not isinstance(self._key, (ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey)):
                raise ValueError()
            if isinstance(self._key, rsa.RSAPrivateKey) and self._key.key_size < 2048:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise ValueError("brokerAccounts.forthright.invalidPrivateKey") from exc
        gateway_config = {"gateway_environment": config.gateway_environment}
        gateway = gateway_url(gateway_config)
        parsed = urlsplit(gateway)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("brokerAccounts.forthright.gatewayRequired")
        super().__init__(gateway, config.timeout)
        self.config = config
        self.account_id = config.account_id.strip()
        self.environment = gateway_environment(gateway_config)
        self.is_futures = config.account_type == "futures"
        self.strategy_units = strategy_units
        self._prefix = "/future/trade" if self.is_futures else "/stock/trade"
        self._device_mac = ":".join(f"{uuid.getnode():012X}"[i:i + 2] for i in range(0, 12, 2))
        self._contracts = None

    def _headers(self, path: str, body: bytes, *, created=None, nonce=None) -> dict:
        # Forthright requires a SHA-256 Content-Digest of the exact HTTP body,
        # including unlock requests. This is not a stored password verifier.
        # https://dev-openapi-doc.techfx88.com/zh/quick-start/signature.html
        digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
        params = ('("@method" "@authority" "@path" "@query" "content-type" "content-digest")'
                  f';created={int(time.time()) if created is None else created};'
                  f'nonce="{nonce or secrets.token_hex(16)}";keyid="{self.config.app_id}"')
        url = urlsplit(self.base_url + path)
        authority = url.hostname.lower() + (f":{url.port}" if url.port and url.port != 443 else "")
        message = "\n".join([
            '"@method": POST', f'"@authority": {authority}', f'"@path": {url.path}',
            f'"@query": ?{url.query}', '"content-type": application/json',
            f'"content-digest": {digest}', f'"@signature-params": {params}',
        ]).encode()
        signature = (self._key.sign(message) if isinstance(self._key, ed25519.Ed25519PrivateKey)
                     else self._key.sign(message, padding.PKCS1v15(), hashes.SHA256()))
        return {"Content-Type": "application/json", "Content-Digest": digest,
                "Signature-Input": "sig1=" + params,
                "Signature": "sig1=:" + base64.b64encode(signature).decode() + ":",
                "Device-Mac": self._device_mac, "Version": "1.0.0"}

    def _post(self, path: str, payload: dict):
        mutating = path.endswith(("/place_order", "/cancel_order"))
        error_key = "brokerAccounts.forthright." + ("executionUnknown" if mutating else "serviceUnavailable")
        # Pace all clients sharing an application, including concurrent UI reads.
        with _request_lock:
            rate_key = (self.base_url, self.config.app_id)
            delay = _next_request.get(rate_key, 0) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            _next_request[rate_key] = time.monotonic() + 1.05
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        try:
            response = self.session.post(self.base_url + path, data=body, headers=self._headers(path, body),
                                         timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as exc:
            raise ConnectionError(error_key) from exc
        if getattr(response, "status_code", 200) >= 500:
            raise ConnectionError(error_key)
        try:
            result, decoding = self._decode_response(response)
        except ValueError as exc:
            raise ConnectionError(error_key) from exc
        if not isinstance(result, dict):
            raise ConnectionError(error_key)
        if not response.ok or result.get("status") != 0:
            code = result.get("code", result.get("status", response.status_code))
            details = self._error_details(result, response, decoding, payload)
            if code == 10000:
                exc = ConnectionError(error_key)
                exc.broker_details = details
                raise exc
            if code == 10007:
                raise BrokerApiError("brokerAccounts.forthright.marketSessionClosed")
            if code == 10008:
                raise BrokerApiError("brokerAccounts.forthright.serviceUnavailable")
            if code == 10003:
                raise BrokerApiError("brokerAccounts.forthright.secondAuthRequired")
            if code == 10005:
                raise BrokerApiError("brokerAccounts.forthright.unlockRequired")
            if code == "ip_forbidden":
                raise BrokerApiError("brokerAccounts.forthright.ipForbidden")
            # Upstream text can echo credentials; retain only a bounded status code.
            safe_code = str(code) if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(code)) else "invalid_response"
            exc = BrokerApiError(f"Forthright API error {safe_code}")
            exc.broker_details = details
            raise exc
        return result.get("data")

    @staticmethod
    def _decode_response(response):
        raw = getattr(response, "content", None)
        if not isinstance(raw, bytes):
            return response.json(), {"encoding": "json", "replacement_on_wire": False}
        try:
            text = raw.decode("utf-8-sig", errors="strict")
            result = json.loads(text)
            return result, {"encoding": "utf-8", "replacement_on_wire": "\ufffd" in json.dumps(result, ensure_ascii=False)}
        except UnicodeDecodeError:
            # Some upstream error messages contain GBK bytes inside an ASCII JSON envelope.
            # Never reinterpret a successful financial snapshot using a guessed encoding.
            result = json.loads(raw.decode("gb18030", errors="strict"))
            if not isinstance(result, dict) or result.get("status") in (None, 0):
                raise ValueError("Invalid UTF-8 broker snapshot")
            return result, {"encoding": "gb18030", "replacement_on_wire": False}

    def _error_details(self, result, response, decoding, payload):
        message = str(result.get("info") or "")
        message = re.sub(r"-----BEGIN[^-]*PRIVATE KEY-----[\s\S]*?-----END[^-]*PRIVATE KEY-----", "[redacted]", message)
        secrets_to_hide = [self.config.private_key, self.config.trade_password, self.config.app_id,
                           self.account_id, payload.get("sms_code"), payload.get("password")]
        for value in sorted((str(v) for v in secrets_to_hide if v), key=len, reverse=True):
            message = message.replace(value, "[redacted]")
        message = re.sub(r"[\x00-\x1f\x7f]", " ", message)[:500]
        code = str(result.get("status", ""))
        return {"code": code if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code) else "unknown",
                "message": message, "http_status": int(getattr(response, "status_code", 200)), **decoding}

    def discover_accounts(self):
        data = self._post("/account/list", {})
        if not isinstance(data, dict):
            raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
        accounts = []
        for category in ("securities", "futures"):
            values = data.get(category + "_accounts", [])
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
            accounts.extend({"account_type": category, "account_id": value} for value in dict.fromkeys(values))
        if not accounts:
            raise BrokerApiError("brokerAccounts.noAuthorizedAccounts")
        return accounts

    def _query(self, method: str, **payload):
        return self._post(f"{self._prefix}/{method}", {"trade_account": self.account_id, **payload})

    def _trade(self, method, **payload):
        try:
            return self._query(method, **payload)
        except BrokerApiError as exc:
            if str(exc) != "brokerAccounts.forthright.unlockRequired":
                raise
            # 10005 proves non-acceptance. Transport errors never reach this retry.
            self.unlock()
            return self._query(method, **payload)

    @staticmethod
    def _rows(data):
        if isinstance(data, list) and all(isinstance(row, dict) for row in data):
            return data
        if isinstance(data, dict) and "recordData" in data:
            rows = data["recordData"]
            if rows is None:
                rows = []
            if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
                return rows
        raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")

    def _pages(self, method, **payload):
        rows = []
        for page in range(1, 101):
            data = self._query(method, page=page, **payload)
            rows.extend(self._rows(data))
            if not isinstance(data, dict) or page >= int(data.get("totalPage") or 0):
                return rows
        raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")

    def connect(self):
        data = self._post("/account/list", {})
        key = "futures_accounts" if self.is_futures else "securities_accounts"
        accounts = data.get(key, []) if isinstance(data, dict) else []
        if not self.account_id and len(accounts) == 1:
            self.account_id = str(accounts[0])
        if self.account_id not in accounts:
            raise BrokerApiError("brokerAccounts.brokerAccountUnavailable")
        self.connected = True
        return True

    def unlock(self, sms_code=""):
        payload = {"trade_account": self.account_id, "trade_account_type": self.config.account_type}
        if sms_code:
            self._post("/account/second_auth", {**payload, "sms_code": str(sms_code)})
        else:
            self._post("/account/unlock", {**payload, "password": self.config.trade_password})
        return True

    def get_connection_status(self):
        return {"connected": self.connected, "account_id": self.account_id,
                "account_type": self.config.account_type, "gateway": self.base_url,
                "environment": self.environment}

    def get_account_summary(self, currency=None):
        currency = currency or self.config.currency
        if currency not in {"USD", "HKD", "CNY"}:
            raise ValueError("brokerAccounts.invalidOrder")
        if self.is_futures:
            rows = self._rows(self._query("fund_total"))
            detail = self._query("fund_detail")
            if not isinstance(detail, dict) or not detail.get("baseCcy"):
                raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
            base_currency = str(detail["baseCcy"]).removeprefix("HK-")
            def amount(field):
                value = detail.get(field)
                return None if value is None or value == "" else self._number(value)
            return {"account_id": self.account_id, "account_type": "futures", "currency": base_currency,
                    "reporting_currency": base_currency, "cash": amount("cashBal"),
                    "equity": amount("netEquity"), "buying_power": amount("avFund"),
                    "available_funds": amount("avFund"), "initial_margin": amount("imargin"),
                    "maintenance_margin": amount("mmargin"), "frozen_funds": amount("orderLockup"),
                    "margin_call": amount("marginCall"), "unrealized_pnl": amount("totalPl"),
                    "balances": rows,
                    "environment": self.environment}
        data = self._query("fund_total", to_ccy=currency)
        if not isinstance(data, dict) or "asset" not in data:
            raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
        balances = self.get_cash_balances()
        selected = next((row for row in balances if row["currency"] == currency), None)
        return {"account_id": self.account_id, "currency": data.get("ccy", currency),
                "reporting_currency": data.get("ccy", currency), "balances": balances,
                "available_cash": selected["available_cash"] if selected else 0,
                "cash_is_converted": True,
                "equity": self._number(data["asset"]), "cash": self._number(data.get("net_cash_amt", data.get("cash_buy_power"))),
                "buying_power": self._number(data.get("enable_buy_power")),
                "market_value": self._number(data.get("market_value")), "environment": self.environment}

    def get_cash_balances(self):
        rows = self._rows(self._query("fund_detail"))
        return [{"currency": str(row.get("ccy") or ""),
                 "available_cash": self._number(row.get("cash_buy_power")),
                 "settled_cash": self._number(row.get("t0")),
                 "withdrawable_cash": self._number(row.get("desirable_amt"))} for row in rows]

    @staticmethod
    def _number(value):
        try:
            number = float(value)
        except (ValueError, TypeError) as exc:
            raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot") from exc
        if not math.isfinite(number):
            raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
        return number

    def get_positions(self, *, raise_on_error=True):
        data = self._query("position")
        rows = self._rows([] if data is None and not self.is_futures else data)
        positions = []
        for row in rows:
            if self.is_futures:
                qty = self._number(row.get("signedNetQty"))
                symbol, market = str(row.get("prodCode") or ""), "Futures"
                avg = self._number(row.get("netAvg"))
                if self.strategy_units:
                    qty *= self.contract_size(symbol, row.get("instCode") or row.get("instmntCode"))
            else:
                if row.get("exchange_code") not in {"HK", "US"}:
                    continue
                qty = self._number(row.get("quality"))
                symbol, market = self._stock_identity(row)
                cost = row.get("avg_price") or row.get("avg_buying_price")
                avg = self._number(cost) if cost is not None else None
            if qty == 0:
                continue
            if not symbol:
                raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
            positions.append({"symbol": symbol, "quantity": qty, "qty": qty,
                              "side": "short" if qty < 0 else "long", "market": market,
                              "avg_entry_price": avg, "current_price": row.get("price"),
                              "market_value": row.get("market_value"), "unrealized_pnl": None,
                              "currency": row.get("ccy") or ({"HKStock": "HKD", "USStock": "USD"}.get(market)), "raw": row})
            if not self.is_futures and row.get("enable_qty") is not None:
                available = self._number(row["enable_qty"])
                positions[-1].update(salable_qty=available, available_qty=available)
        return positions

    @staticmethod
    def _stock_identity(row):
        symbol = str(row.get("product_code") or "")
        hk = row.get("exchange_code") == "HK"
        return (symbol.zfill(5) + ".HK" if hk else symbol), ("HKStock" if hk else "USStock")

    def get_contracts(self, instrument_code=""):
        if not self.is_futures:
            raise ValueError("brokerAccounts.forthright.unsupportedMarket")
        return self._pages("trade_contracts", instrument_code=instrument_code)

    def close_futures_position(self, symbol, expected_quantity):
        if not self.is_futures or self.strategy_units:
            raise ValueError("brokerAccounts.forthright.unsupportedMarket")
        expected = self._number(expected_quantity)
        if not expected or not float(expected).is_integer():
            raise ValueError("brokerAccounts.invalidOrder")
        symbol = str(symbol).removeprefix("Futures:")
        if self.get_orders(status="open", symbol=symbol):
            raise ValueError("brokerAccounts.forthright.closeOrdersPending")
        positions = [row for row in self.get_positions() if row["symbol"] == symbol]
        if len(positions) != 1 or positions[0]["quantity"] != expected:
            raise ValueError("brokerAccounts.forthright.positionChanged")
        # Forthright uses net positions; an opposite order offsets the observed lots.
        return self.place_market_order(symbol=symbol, market_type="Futures",
                                       side="sell" if expected > 0 else "buy", quantity=abs(expected))

    def contract_size(self, symbol, instrument_code=""):
        symbol = str(symbol).removeprefix("Futures:")
        # lotSize is the order lot, not the currency value of one index point.
        try:
            specs = json.loads(os.getenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", "{}"))
            spec = specs.get(symbol)
            if spec is None:
                if not instrument_code:
                    instrument_code = self._instrument(symbol, "Futures")[2]["instmntCode"]
                spec = specs.get(instrument_code, {})
        except (ValueError, TypeError, AttributeError) as exc:
            raise BrokerApiError("brokerAccounts.forthright.contractMetadataRequired") from exc
        if not isinstance(spec, dict) or spec.get("currency") != self.config.currency or not spec.get("point_value"):
            raise BrokerApiError("brokerAccounts.forthright.contractMetadataRequired")
        size = self._number(spec["point_value"])
        if size <= 0:
            raise BrokerApiError("brokerAccounts.forthright.contractRequired")
        return size

    def normalize_strategy_quantity(self, symbol, quantity):
        if not self.is_futures:
            if str(symbol).upper().endswith(".HK"):
                lot = Decimal(str(self.get_instrument_rules(symbol, "HKStock")["lot_size"]))
                normalized = (Decimal(str(quantity)) / lot).to_integral_value(rounding=ROUND_DOWN) * lot
                if normalized <= 0:
                    raise ValueError("brokerAccounts.forthright.belowBoardLot")
                return float(normalized)
            quantum = Decimal("0.0001")
            return float(Decimal(str(quantity)).quantize(quantum, rounding=ROUND_DOWN))
        size = Decimal(str(self.contract_size(symbol)))
        return float((Decimal(str(quantity)) / size).to_integral_value(rounding=ROUND_DOWN) * size)

    def validate_instrument(self, symbol, market):
        self._instrument(symbol, market)
        if not self.is_futures and str(market).lower() == "hkstock":
            self.get_instrument_rules(symbol, "HKStock")
        if self.is_futures:
            self.contract_size(symbol)
            from app.services.market.dated_futures import dated_future_feed
            dated_future_feed(str(symbol).removeprefix("Futures:"), required=True)

    def get_instrument_rules(self, symbol, market="HKStock"):
        if self.is_futures or str(market).lower() != "hkstock":
            raise ValueError("brokerAccounts.forthright.unsupportedMarket")
        product, _, _ = self._instrument(symbol, market)
        from app.services.market.hk_board_lots import board_lot_rule
        return board_lot_rule(product)

    def _instrument(self, symbol, market):
        symbol = str(symbol or "").strip()
        if ":" in symbol:
            prefix, symbol = symbol.split(":", 1)
            if prefix.lower() != str(market).lower():
                raise ValueError("brokerAccounts.forthright.unsupportedMarket")
        market = str(market).lower()
        if self.is_futures:
            if market != "futures" or not re.fullmatch(r"[A-Za-z0-9._-]+", symbol):
                raise ValueError("brokerAccounts.forthright.unsupportedMarket")
            if self._contracts is None:
                self._contracts = self.get_contracts()
            matches = [r for r in self._contracts if r.get("prodCode") == symbol]
            today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
            if len(matches) != 1 or str(matches[0].get("expiryYmd") or "") < today:
                raise ValueError("brokerAccounts.forthright.contractRequired")
            last_trade = str(matches[0].get("lastTradeDate") or "").replace("-", "")
            if last_trade and last_trade < today:
                raise ValueError("brokerAccounts.forthright.contractRequired")
            return symbol, "", matches[0]
        if market == "hkstock":
            original = symbol.upper()
            symbol = re.sub(r"\.HK$", "", symbol, flags=re.I)
            if not re.fullmatch(r"\d{1,5}", symbol):
                raise ValueError("brokerAccounts.forthright.unsupportedMarket")
            if self.strategy_units and original != symbol.zfill(5) + ".HK":
                raise ValueError("brokerAccounts.forthright.canonicalSymbolRequired")
            return symbol.zfill(5), "HK", None
        if market == "usstock":
            original = symbol.upper()
            symbol = re.sub(r"\.US$", "", symbol, flags=re.I).upper()
            if not re.fullmatch(r"[A-Z][A-Z.-]{0,14}", symbol):
                raise ValueError("brokerAccounts.forthright.unsupportedMarket")
            if self.strategy_units and original != symbol:
                raise ValueError("brokerAccounts.forthright.canonicalSymbolRequired")
            return symbol, "US", None
        raise ValueError("brokerAccounts.forthright.unsupportedMarket")

    @staticmethod
    def _client_id(value):
        return hashlib.sha256(str(value).encode()).hexdigest()[:16] if value else ""

    def _place(self, *, symbol, side, quantity, market_type="USStock", price=0,
               order_type="market", client_order_id="", reference_price=0, **kwargs):
        product, exchange, contract = self._instrument(symbol, market_type)
        qty = float(quantity)
        if self.is_futures and self.strategy_units:
            qty = float(Decimal(str(quantity)) / Decimal(str(self.contract_size(symbol))))
        price = float(price if order_type == "limit" else reference_price or 0)
        if side not in {"buy", "sell"} or not math.isfinite(qty) or qty <= 0 or not math.isfinite(price):
            raise ValueError("brokerAccounts.invalidOrder")
        if (self.is_futures or exchange == "HK") and not qty.is_integer():
            raise ValueError("brokerAccounts.invalidOrder")
        if exchange == "HK":
            lot = self.get_instrument_rules(symbol, "HKStock")["lot_size"]
            if Decimal(str(quantity)) % Decimal(lot):
                raise ValueError("brokerAccounts.forthright.wholeBoardLot")
        if exchange == "US" and abs(qty * 10000 - round(qty * 10000)) > 1e-7:
            raise ValueError("brokerAccounts.invalidOrder")
        if order_type not in {"market", "limit"} or price < 0:
            raise ValueError("brokerAccounts.invalidOrder")
        if order_type == "limit" and price <= 0:
            raise ValueError("brokerAccounts.invalidLimitPrice")
        if self.is_futures and order_type == "limit":
            from app.services.market.dated_futures import future_display_metadata
            tick = future_display_metadata(product).get("price_tick")
            if tick and Decimal(str(price)) % Decimal(str(tick)):
                raise ValueError("brokerAccounts.invalidLimitPrice")
        if not self.is_futures and price <= 0:
            raise ValueError("brokerAccounts.forthright.referencePriceRequired")
        payload = {"product_code": product, "side": "B" if side == "buy" else "S", "order_qty": qty,
                   "order_price": price}
        if self.is_futures:
            payload.update(order_qty=int(qty), order_type=0 if order_type == "limit" else 6,
                           valid_type=0, condition_type=0, clOrderId=self._client_id(client_order_id))
        else:
            payload.update(exchange_code=exchange, order_type="LO" if order_type == "limit" else "MO",
                           order_valid_type="0", external_serial_no=str(client_order_id)[:64])
        data = self._trade("place_order", **payload)
        if not isinstance(data, dict):
            raise ConnectionError("brokerAccounts.forthright.executionUnknown")
        if self.is_futures and data.get("returnCode") not in (None, 0):
            raise BrokerApiError(f"Forthright order rejected ({int(data['returnCode'])})")
        order_id = str(data.get("extOrderId") or data.get("orderId") or "") if self.is_futures else str(data.get("order_id") or "")
        if not order_id:
            raise ConnectionError("brokerAccounts.forthright.executionUnknown")
        return BrokerOrderResult(True, order_id=order_id, status="submitted",
                                 raw={**data, "client_order_id": client_order_id, "fee_status": "pending"})

    def place_market_order(self, **kwargs):
        return self._place(**kwargs, order_type="market")

    def place_limit_order(self, **kwargs):
        return self._place(**kwargs, order_type="limit")

    @staticmethod
    def _stock_time(date, clock):
        if date is None or clock is None:
            return None
        try:
            # The API supplies broker-local calendar fields, with no UTC offset.
            return datetime.strptime(f"{date}{str(clock).zfill(6)}", "%Y%m%d%H%M%S").isoformat()
        except (ValueError, TypeError):
            return None

    def _order_fees(self, order):
        fees = {"fee_status": "pending", "fee_amount": None, "fee_currency": order.get("currency"),
                "fee_scope": "order"}
        raw = order["raw"]
        if (not self.is_futures and order["filled_qty"] > 0
                and order["status"] in {"filled", "cancelled"}
                and raw.get("fare_amt") is not None and order.get("currency") in {"USD", "HKD"}):
            fee = self._number(raw["fare_amt"])
            fees.update(fee_amount=fee, commission=fee, commission_ccy=order["currency"], commission_quote=fee,
                        fees_by_ccy={order["currency"]: fee}, fee_status="actual" if fee else "actual_zero")
        return fees

    def _order(self, row):
        if self.is_futures:
            order_id = str(row.get("extOrderId") or row.get("orderId") or "")
            status = {8: "partially_filled", 9: "filled", 20: "filled", 10: "cancelled",
                      28: "cancelled", 29: "cancelled", 21: "rejected", 24: "rejected",
                      31: "rejected", 100: "rejected", 30: "rejected"}.get(row.get("status"), "submitted")
            filled = self._number(row.get("tradedQty", 0))
            avg = self._future_fill_price(row, filled) if filled else 0
            symbol, market = row.get("prodCode"), "Futures"
            qty, price, side = row.get("totalQty") or row.get("qty"), row.get("orderPrice"), row.get("buySell")
            client_id = row.get("clOrderId")
            if self.strategy_units:
                multiplier = self.contract_size(symbol, row.get("instCode"))
                filled *= multiplier
                qty = self._number(qty) * multiplier
        else:
            order_id = str(row.get("order_id") or "")
            status = {"8": "filled", "7": "partially_filled", "5": "cancelled", "6": "cancelled",
                      "9": "rejected", "J": "rejected"}.get(str(row.get("order_status")), "submitted")
            filled = self._number(row.get("last_qty", 0))
            avg = self._number(row.get("last_amt", 0)) / filled if filled else 0
            symbol, market = self._stock_identity(row)
            qty, price, side = row.get("order_qty"), row.get("order_price"), row.get("side")
            client_id = row.get("external_serial_no")
        order = {"id": order_id, "order_id": order_id, "symbol": symbol, "market": market,
                "side": "buy" if side == "B" else "sell", "status": status, "quantity": qty,
                "qty": qty,
                "filled_qty": filled, "filled_avg_price": avg, "limit_price": price,
                "order_type": "limit" if row.get("orderType", row.get("order_type")) in (0, "LO") else "market",
                "submitted_at": row.get("timeStampStr", row.get("order_time")), "client_order_id": client_id,
                "currency": row.get("ccy") or ({"HKStock": "HKD", "USStock": "USD"}.get(market)), "raw": row}
        if not self.is_futures:
            order.update(submitted_at=self._stock_time(row.get("curr_date"), row.get("curr_time")) or row.get("order_time"),
                         updated_at=self._stock_time(row.get("last_curr_date"), row.get("last_curr_time")))
        order.update(self._order_fees(order))
        return order

    def _future_fill_price(self, row, filled):
        # totalTrdPrc is an integer aggregate with undocumented scaling. Use
        # the execution endpoint's explicit decimal average and exact quantity.
        trades = self._pages("done_trade", limit=100, accOrderNo=int(row["accOrderNo"]), **self._dates())
        quantity = notional = 0.0
        seen = set()
        for trade in trades:
            if str(trade.get("extOrderId")) != str(row.get("extOrderId")):
                continue
            identity = (trade.get("tradeNo"), trade.get("recNo"))
            if identity in seen:
                continue
            seen.add(identity)
            qty = self._number(trade.get("tradeQty"))
            price = self._number(trade.get("avgTradedPrc") or trade.get("avgPrice"))
            if qty <= 0 or price <= 0:
                raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
            quantity += qty
            notional += qty * price
        if quantity != filled:
            raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
        return notional / quantity

    @staticmethod
    def _dates():
        today = datetime.now(ZoneInfo("Asia/Shanghai"))
        return {"trade_date_from": int((today - timedelta(days=90)).strftime("%Y%m%d")),
                "trade_date_to": int(today.strftime("%Y%m%d"))}

    def _history(self):
        if self.is_futures:
            return self._pages("history_order", limit=100, **self._dates())
        rows, cursor = [], ""
        for _ in range(100):
            data = self._query("history_order", page_size=100, **self._dates(),
                               **({"order_id": cursor} if cursor else {}))
            batch = self._rows([] if data is None else data)
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            next_cursor = str(batch[-1].get("order_id") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")

    def get_orders(self, *, status="all", limit=100, symbol="", raise_on_error=True):
        if self.is_futures:
            rows = self._pages("working_order", limit=100)
        else:
            data = self._query("today_order")
            rows = self._rows([] if data is None else data)
        if status != "open":
            rows = self._history() + rows
        orders = {order["id"]: order for order in map(self._order, rows)}
        result = list(orders.values())
        if status == "open":
            result = [r for r in result if r["status"] not in {"filled", "cancelled", "rejected"}]
        elif status == "filled":
            result = [r for r in result if r["status"] == "filled"]
        if symbol:
            symbol = str(symbol).removeprefix("HKStock:").removeprefix("USStock:")
            if not self.is_futures and re.fullmatch(r"\d{1,5}(?:\.HK)?", symbol, re.I):
                symbol = re.sub(r"\.HK$", "", symbol, flags=re.I).zfill(5) + ".HK"
            result = [r for r in result if r["symbol"] == symbol]
        result.sort(key=lambda row: (str(row.get("submitted_at") or ""), row["id"]), reverse=True)
        return result[:max(1, int(limit))]

    def get_transactions(self, symbol="", limit=100):
        if self.is_futures:
            raise ValueError("brokerAccounts.forthright.unsupportedMarket")
        rows = []
        for method, params in (("history_match", self._dates()), ("today_match", {})):
            data = self._query(method, **params)
            rows.extend(self._rows([] if data is None else data))
        transactions = {}
        for row in rows:
            if row.get("exchange_code") not in {"HK", "US"}:
                continue
            order_id = str(row.get("order_id") or "")
            product, market = self._stock_identity(row)
            quantity, price = self._number(row.get("last_qty")), self._number(row.get("last_px"))
            if (not order_id or not row.get("product_code") or row.get("serial_no") is None
                    or not row.get("trade_date") or row.get("side") not in {"B", "S"}
                    or quantity <= 0 or price <= 0):
                raise BrokerApiError("brokerAccounts.forthright.invalidSnapshot")
            identity = f"{row['trade_date']}:{row['exchange_code']}:{order_id}:{row['serial_no']}"
            transactions[identity] = {
                "id": identity, "order_id": order_id, "symbol": product, "market": market,
                "side": "buy" if row["side"] == "B" else "sell", "status": "filled",
                "filled_qty": quantity, "filled_avg_price": price,
                "amount": float(Decimal(str(row["last_qty"])) * Decimal(str(row["last_px"]))),
                "filled_at": self._stock_time(row.get("curr_date"), row.get("business_time")),
                "currency": row.get("ccy") or ("HKD" if market == "HKStock" else "USD"),
                "fee_status": "pending", "fee_scope": "order", "source": "exchange", "raw": row,
            }
        result = list(transactions.values())
        if symbol:
            canonical = str(symbol).upper().removeprefix("HKSTOCK:").removeprefix("USSTOCK:")
            if re.fullmatch(r"\d{1,5}(?:\.HK)?", canonical):
                canonical = canonical.removesuffix(".HK").zfill(5) + ".HK"
            result = [row for row in result if row["symbol"] == canonical]
        result.sort(key=lambda row: (str(row.get("filled_at") or ""), row["id"]), reverse=True)
        return result[:max(1, int(limit))]

    def _result(self, order):
        raw = {**order["raw"], **self._order_fees(order)}
        return BrokerOrderResult(True, order_id=order["id"], status=order["status"],
                                 filled=order["filled_qty"], avg_price=order["filled_avg_price"],
                                 raw=raw)

    def get_order_status(self, order_id):
        return self._result(self._order(self._find_order(order_id=str(order_id))))

    def get_order_status_by_client_id(self, client_order_id):
        expected = self._client_id(client_order_id) if self.is_futures else client_order_id
        return self._result(self._order(self._find_order(client_order_id=expected)))

    def _find_order(self, *, order_id="", client_order_id=""):
        def matches(row):
            identity = row.get("extOrderId") or row.get("orderId") if self.is_futures else row.get("order_id")
            client_id = row.get("clOrderId") if self.is_futures else row.get("external_serial_no")
            return str(identity) == order_id if order_id else client_id == client_order_id
        if self.is_futures:
            current = self._pages("working_order", limit=100)
        else:
            data = self._query("today_order", **({"order_id": order_id} if order_id else {}))
            current = self._rows([] if data is None else data)
        for row in current:
            if matches(row):
                return row
        for row in self._history():
            if matches(row):
                return row
        raise BrokerApiError("brokerAccounts.orderUnavailable")

    def cancel_order(self, order_id):
        if self.is_futures:
            row = self._find_order(order_id=str(order_id))
            payload = {"external_order_id": row.get("extOrderId"), "accOrderNo": row.get("accOrderNo"),
                       "product_code": row.get("prodCode"), "side": row.get("buySell")}
            if not all(payload.values()):
                raise BrokerApiError("brokerAccounts.orderUnavailable")
        else:
            payload = {"order_id": str(order_id)}
        self._trade("cancel_order", **payload)
        return True
