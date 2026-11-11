"""
Gate.io (direct REST) clients:
- Spot: /api/v4/spot/*
- Futures USDT: /api/v4/futures/usdt/*

Signing (apiv4):
SIGN = hex(hmac_sha512(secret, method + "\\n" + url + "\\n" + query + "\\n" + hexencode(sha512(payload)) + "\\n" + timestamp))
Headers:
- KEY: api key
- Timestamp: unix seconds
- SIGN: signature hex
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.ws_order_transport import submit_gate_order
from app.services.live_trading.symbols import to_gate_currency_pair
from app.services.live_trading.gate_spot_fill import parse_gate_spot_fill
from app.utils.numeric_precision import floor_decimal_to_step

logger = logging.getLogger(__name__)


def _gate_decimal_text(value: Union[Decimal, float, int, str]) -> str:
    """Serialize numeric order fields without scientific notation.

    Gate spot rejects otherwise valid small amounts such as ``5e-05``.  Build
    the decimal from ``str`` first so binary-float artifacts are not exposed,
    then force fixed-point output for every Gate order path.
    """
    return format(Decimal(str(value)), "f")


def _gate_ticker_response_to_normalized(raw: Any) -> Dict[str, Any]:
    """Parse Gate spot/futures tickers API (array of one row) into a dict with float ``last`` for quick_trade."""
    row: Dict[str, Any] = {}
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        row = raw[0]
    elif isinstance(raw, dict) and raw:
        row = raw
    else:
        return {}
    last = 0.0
    for key in ("last", "mark_price", "index_price", "close", "price"):
        v = row.get(key)
        if v is not None and str(v).strip():
            try:
                last = float(str(v).replace(",", ""))
                break
            except Exception:
                continue
    out = dict(row)
    out["last"] = last
    out["close"] = last
    out["price"] = last
    return out


class _GateBase(BaseRestClient):
    _CHANNEL_ID = "dinger"

    def __init__(self, *, api_key: str, secret_key: str, base_url: str = "https://api.gateio.ws", timeout_sec: float = 15.0, channel_id: str = ""):
        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.channel_id = self._CHANNEL_ID
        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing Gate api_key/secret_key")

    def _sign(self, *, method: str, url: str, query_string: str, body_str: str, ts: str) -> str:
        # Per https://www.gate.com/docs/developers/apiv4/en/#authentication — payload slot is SHA512(body).hexdigest(),
        # not the raw body (GET / no body => hash of empty string).
        hashed_payload = hashlib.sha512((body_str or "").encode("utf-8")).hexdigest()
        msg = f"{method.upper()}\n{url}\n{query_string}\n{hashed_payload}\n{ts}"
        return hmac.new(self.secret_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha512).hexdigest()

    def _headers(self, ts: str, sign: str) -> Dict[str, str]:
        headers = {"KEY": self.api_key, "Timestamp": ts, "SIGN": sign, "Content-Type": "application/json", "X-Gate-Size-Decimal": "1"}
        if self.channel_id:
            headers["X-Gate-Channel-Id"] = self.channel_id[:19]
        return headers

    def _format_text(self, client_order_id: Optional[str]) -> str:
        raw = str(client_order_id or "").strip()
        if not raw:
            return ""
        normalized = []
        for ch in raw:
            if ch.isalnum() or ch in ("-", "_", "."):
                normalized.append(ch)
        text = "".join(normalized).strip()
        if not text:
            return ""
        if not text.startswith("t-"):
            text = f"t-{text}"
        return text[:28]

    def _signed_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        m = str(method or "GET").upper()
        if m == "POST" and isinstance(json_body, dict) and path in {
            "/api/v4/spot/orders",
            "/api/v4/futures/usdt/orders",
        }:
            ws_result = submit_gate_order(
                api_key=self.api_key,
                secret_key=self.secret_key,
                body=json_body,
                base_url=self.base_url,
                futures=path == "/api/v4/futures/usdt/orders",
                channel_id=self.channel_id,
            )
            if ws_result is not None:
                return ws_result
        ts = str(int(time.time()))
        body_str = self._json_dumps(json_body) if json_body is not None else ""
        qs = ""
        request_params = None
        if params:
            norm = {str(k): "" if v is None else str(v) for k, v in dict(params).items()}
            ordered_items = sorted(norm.items())
            qs = urlencode(ordered_items, doseq=True)
            request_params = dict(ordered_items)
        sign = self._sign(method=m, url=path, query_string=qs, body_str=body_str, ts=ts)
        hdrs = dict(self._headers(ts, sign))
        if extra_headers:
            hdrs.update({str(k): str(v) for k, v in extra_headers.items()})
        code, data, text = self._request(
            m,
            path,
            params=request_params,
            data=body_str if body_str else None,
            headers=hdrs,
        )
        if code >= 400:
            raise LiveTradingError(f"Gate HTTP {code}: {text[:500]}")
        return data

    def _public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
        code, data, text = self._request(method, path, params=params, headers=None, json_body=None, data=None)
        if code >= 400:
            raise LiveTradingError(f"Gate HTTP {code}: {text[:500]}")
        return data

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        pair = to_gate_currency_pair(symbol)
        try:
            if market_type == "spot":
                raw = self._signed_request("GET", "/api/v4/wallet/fee", params={"currency_pair": pair})
            else:
                settle = "usdt"
                contract = pair.replace("_", "").upper() + "_USDT" if "_" not in pair.upper() or "USDT" not in pair.upper() else pair
                raw = self._signed_request("GET", f"/api/v4/futures/{settle}/contracts/{contract}", params={})
            if isinstance(raw, dict):
                maker = abs(float(raw.get("maker_fee_rate") or raw.get("maker_fee") or 0))
                taker = abs(float(raw.get("taker_fee_rate") or raw.get("taker_fee") or 0))
                if maker > 0 or taker > 0:
                    return {"maker": maker, "taker": taker}
        except Exception as e:
            logger.warning(f"Gate get_fee_rate({symbol}) failed: {e}")
        return None


    def _order_trade_rows(self, path: str, params: Dict[str, Any]) -> list:
        rows = []
        for page in range(1, 11):
            result = self._signed_request('GET', path, params={**params, 'limit': 100, 'page': page})
            if not isinstance(result, list):
                raise LiveTradingError('strategyRuntime.fillSnapshotNotReady')
            rows.extend(result)
            if len(result) < 100:
                return rows
        raise LiveTradingError('strategyRuntime.fillSnapshotNotReady')


class GateSpotClient(_GateBase):
    def get_currency_pair(self, *, symbol: str) -> Dict[str, Any]:
        """Return native public spot amount/price precision and minimums."""
        pair = to_gate_currency_pair(symbol)
        return self._public_request("GET", f"/api/v4/spot/currency_pairs/{pair}")

    def ping(self) -> bool:
        try:
            _ = self._public_request("GET", "/api/v4/spot/time")
            return True
        except Exception:
            return False

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        pair = to_gate_currency_pair(symbol)
        raw = self._public_request("GET", "/api/v4/spot/tickers", params={"currency_pair": pair})
        return _gate_ticker_response_to_normalized(raw)

    def get_accounts(self) -> Any:
        return self._signed_request("GET", "/api/v4/spot/accounts")

    def get_open_orders(self, *, limit: int = 100) -> Any:
        """Return current spot orders across every currency pair."""
        page_limit = min(100, max(1, int(limit or 100)))
        return self._signed_request(
            "GET",
            "/api/v4/spot/open_orders",
            params={"page": 1, "limit": page_limit, "account": "spot"},
        )

    def place_limit_order(self, *, symbol: str, side: str, size: float, price: float, client_order_id: Optional[str] = None) -> LiveOrderResult:
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        qty = float(size or 0.0)
        px = float(price or 0.0)
        if qty <= 0 or px <= 0:
            raise LiveTradingError("Invalid size/price")
        body: Dict[str, Any] = {
            "currency_pair": to_gate_currency_pair(symbol),
            "side": sd,
            "type": "limit",
            "amount": _gate_decimal_text(qty),
            "price": _gate_decimal_text(px),
            "time_in_force": "gtc",
        }
        text = self._format_text(client_order_id)
        if text:
            body["text"] = text
        raw = self._signed_request("POST", "/api/v4/spot/orders", json_body=body)
        oid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        return LiveOrderResult(exchange_id="gate", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw if isinstance(raw, dict) else {"raw": raw})

    def place_market_order(self, *, symbol: str, side: str, size: float, client_order_id: Optional[str] = None) -> LiveOrderResult:
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        qty = float(size or 0.0)
        if qty <= 0:
            raise LiveTradingError("Invalid size")
        # Gate defaults time_in_force to gtc; market orders only accept ioc/fok.
        body: Dict[str, Any] = {
            "currency_pair": to_gate_currency_pair(symbol),
            "side": sd,
            "type": "market",
            "amount": _gate_decimal_text(qty),
            "time_in_force": "ioc",
        }
        text = self._format_text(client_order_id)
        if text:
            body["text"] = text
        raw = self._signed_request("POST", "/api/v4/spot/orders", json_body=body)
        oid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        return LiveOrderResult(exchange_id="gate", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw if isinstance(raw, dict) else {"raw": raw})

    def cancel_order(self, *, order_id: str, symbol: str) -> Any:
        if not order_id:
            raise LiveTradingError("Gate spot cancel_order requires order_id")
        if not str(symbol or "").strip():
            raise LiveTradingError("Gate spot cancel_order requires symbol")
        return self._signed_request("DELETE", f"/api/v4/spot/orders/{str(order_id)}",
                                    params={"currency_pair": to_gate_currency_pair(symbol)})

    def get_order(self, *, order_id: str, symbol: str) -> Any:
        if not order_id:
            raise LiveTradingError("Gate spot get_order requires order_id")
        if not str(symbol or "").strip():
            raise LiveTradingError("Gate spot get_order requires symbol")
        return self._signed_request("GET", f"/api/v4/spot/orders/{str(order_id)}",
                                    params={"currency_pair": to_gate_currency_pair(symbol)})

    def get_spot_trades_for_order(self, *, order_id: str, currency_pair: str, details=None) -> Tuple[float, str]:
        try:
            rows = self._order_trade_rows('/api/v4/spot/my_trades', {'currency_pair': currency_pair, 'order_id': str(order_id)})
        except Exception:
            return 0.0, ''
        fees = {}
        for row in rows:
            currency = str(row.get('fee_currency') or '').upper()
            if currency and row.get('fee') not in (None, ''):
                fees[currency] = fees.get(currency, 0.0) + float(row['fee'])
        if details is not None:
            details.update(fees)
        if len(fees) == 1:
            currency, amount = next(iter(fees.items()))
            return amount, currency
        return 0.0, 'MIXED' if fees else ''

    def wait_for_fill(self, *, order_id: str, symbol: str, max_wait_sec: float = 10.0, poll_interval_sec: float = 0.5) -> Dict[str, Any]:
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last: Dict[str, Any] = {}
        while True:
            timed_out = time.time() >= end_ts
            try:
                resp = self.get_order(order_id=str(order_id), symbol=symbol)
                last = resp if isinstance(resp, dict) else {"raw": resp}
            except Exception:
                last = last or {}
            status = str(last.get("status") or "")
            filled = 0.0
            avg_price = 0.0
            fee = 0.0
            fee_ccy = ""
            fees_by_ccy = {}
            filled, avg_price = parse_gate_spot_fill(last)
            # Extract fee from Gate API
            try:
                fee = float(last.get("fee") or 0.0)
            except Exception:
                fee = 0.0
            fee_ccy = str(last.get("fee_currency") or "").strip()
            # Gate updates ``fee`` on the spot order object asynchronously after
            # the underlying trade rows land in /spot/my_trades — if the order
            # already reports filled volume but no fee, query the authoritative
            # fills endpoint and use its sum. This is the same shape Binance /
            # OKX use (post-fill trades endpoint), without it Gate fills get
            # persisted with ``commission=0`` and P&L drifts.
            if filled > 0 and fee == 0:
                try:
                    mt_fee, mt_ccy = self.get_spot_trades_for_order(
                        order_id=str(order_id),
                        currency_pair=str(last.get("currency_pair") or to_gate_currency_pair(symbol)),
                        details=fees_by_ccy,
                    )
                except Exception:
                    mt_fee, mt_ccy = 0.0, ""
                if mt_ccy:
                    fee = mt_fee
                    if mt_ccy:
                        fee_ccy = mt_ccy
            if not fees_by_ccy and fee_ccy and fee != 0:
                fees_by_ccy = {fee_ccy: fee}
            # Fee may lag behind filled/avg on order object; keep polling until timeout (same idea as Bitget/OKX).
            if filled > 0 and avg_price > 0:
                if not fees_by_ccy and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "fees_by_ccy": fees_by_ccy, "status": status, "order": last}
            if status.lower() in ("closed", "cancelled", "canceled"):
                if fee == 0 and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "fees_by_ccy": fees_by_ccy, "status": status, "order": last}
            if timed_out:
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "fees_by_ccy": fees_by_ccy, "status": status, "order": last}
            time.sleep(float(poll_interval_sec or 0.5))


class GateStockClient(_GateBase):
    """Gate traditional-stock client for the dedicated ``/stock`` API family."""

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        base_url: str = "https://api.gateio.ws",
        timeout_sec: float = 15.0,
        channel_id: str = "",
        product_meta: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            api_key=api_key,
            secret_key=secret_key,
            base_url=base_url,
            timeout_sec=timeout_sec,
            channel_id=channel_id,
        )
        self._seed_product_meta = dict(product_meta or {})
        self._symbol_detail_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._symbol_detail_cache_ttl_sec = 30.0

    @staticmethod
    def _symbol(symbol: str) -> str:
        value = str(symbol or "").strip().upper()
        if ":" in value:
            value = value.split(":", 1)[0]
        if "/" in value:
            value = value.split("/", 1)[0]
        if not value:
            raise LiveTradingError("Gate stock symbol is required")
        return value

    @staticmethod
    def _rows(raw: Any) -> list[Dict[str, Any]]:
        data = raw.get("data") if isinstance(raw, dict) else None
        rows = data.get("list") if isinstance(data, dict) else None
        return [dict(item) for item in rows or [] if isinstance(item, dict)]

    def ping(self) -> bool:
        try:
            raw = self._public_request(
                "GET", "/api/v4/stock/symbols/detail", params={"symbols": "AAPL", "page_size": 1}
            )
            return isinstance(raw, dict)
        except Exception:
            return False

    def get_symbol_details(self, *, symbol: str) -> Dict[str, Any]:
        ticker = self._symbol(symbol)
        cached = self._symbol_detail_cache.get(ticker)
        now = time.time()
        if cached and now - cached[0] <= self._symbol_detail_cache_ttl_sec:
            return dict(cached[1])
        params: Dict[str, Any] = {"symbols": ticker, "page_size": 1}
        stock_exchange = str(self._seed_product_meta.get("stock_exchange") or "").strip().lower()
        if stock_exchange in {"us", "hk", "kr"}:
            params["exchange"] = stock_exchange
        raw = self._public_request(
            "GET",
            "/api/v4/stock/symbols/detail",
            params=params,
        )
        rows = self._rows(raw)
        detail = rows[0] if rows else {}
        if detail:
            self._symbol_detail_cache[ticker] = (now, dict(detail))
        return detail

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")

    def _order_rules(self, *, symbol: str) -> Dict[str, Any]:
        detail: Dict[str, Any] = {}
        try:
            detail = self.get_symbol_details(symbol=symbol)
        except Exception as exc:
            logger.warning("Gate stock symbol rule refresh failed for %s: %s", symbol, exc)
        return {**self._seed_product_meta, **detail}

    def _assert_side_allowed(self, *, symbol: str, side: str) -> Dict[str, Any]:
        rules = self._order_rules(symbol=symbol)
        try:
            trade_mode = int(rules.get("trade_mode") or 0)
        except Exception:
            trade_mode = 0
        allowed = {"buy": {1, 3, 4}, "sell": {2, 3, 4}}
        if trade_mode and trade_mode not in allowed[str(side)]:
            raise LiveTradingError(f"Gate stock {side} is disabled for {self._symbol(symbol)}")
        return rules

    def _normalize_quantity(
        self,
        *,
        symbol: str,
        quantity: float,
        for_market: bool,
    ) -> Tuple[Decimal, Optional[int]]:
        del for_market
        value = self._to_decimal(quantity)
        if value <= 0:
            return Decimal("0"), None
        rules = self._order_rules(symbol=symbol)
        step = self._to_decimal(rules.get("step_order_volume") or 0)
        minimum = self._to_decimal(rules.get("min_order_volume") or 0)
        maximum = self._to_decimal(rules.get("max_order_volume") or 0)
        if step > 0:
            value = floor_decimal_to_step(value, step)
        precision: Optional[int] = None
        try:
            precision = int(rules.get("volume_precision"))
        except Exception:
            if step > 0:
                precision = max(0, -step.normalize().as_tuple().exponent)
        if precision is not None and precision >= 0:
            quantum = Decimal("1").scaleb(-precision)
            value = value.quantize(quantum, rounding=ROUND_DOWN)
        if (minimum > 0 and value < minimum) or (maximum > 0 and value > maximum):
            return Decimal("0"), precision
        return value, precision

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        ticker = self._symbol(symbol)
        raw = self._public_request("GET", f"/api/v4/stock/market/{ticker}/orderbook")
        data = raw.get("data") if isinstance(raw, dict) else None
        bids = data.get("bids") if isinstance(data, dict) else []
        asks = data.get("asks") if isinstance(data, dict) else []
        bid = float((bids[0] if bids else {}).get("p") or 0.0)
        ask = float((asks[0] if asks else {}).get("p") or 0.0)
        last = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask)
        return {"symbol": ticker, "last": last, "close": last, "price": last, "bid": bid, "ask": ask}

    def get_accounts(self) -> Any:
        return self._signed_request("GET", "/api/v4/stock/users/assets")

    def get_positions(self, *, symbol: str = "") -> Any:
        params = {"symbol": self._symbol(symbol)} if str(symbol or "").strip() else None
        return self._signed_request("GET", "/api/v4/stock/positions", params=params)

    def get_open_orders(self, *, symbol: str = "", limit: int = 100) -> Any:
        params: Dict[str, Any] = {"page": 1, "page_size": min(500, max(1, int(limit or 100)))}
        if str(symbol or "").strip():
            params["symbol"] = self._symbol(symbol)
        return self._signed_request("GET", "/api/v4/stock/orders", params=params)

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        sd = str(side or "").strip().lower()
        if sd not in {"buy", "sell"}:
            raise LiveTradingError(f"Invalid side: {side}")
        self._assert_side_allowed(symbol=symbol, side=sd)
        quantity, _ = self._normalize_quantity(symbol=symbol, quantity=size, for_market=True)
        if quantity <= 0:
            raise LiveTradingError("Gate stock quantity violates the current symbol rules")
        body: Dict[str, Any] = {
            "volume": _gate_decimal_text(quantity),
            "symbol": self._symbol(symbol),
            "side": 2 if sd == "buy" else 1,
            "price_type": "market",
            "trading_session": "regular",
            "time_in_force": "day",
        }
        if client_order_id:
            body["client_order_id"] = str(client_order_id)[:64]
        raw = self._signed_request("POST", "/api/v4/stock/orders", json_body=body)
        data = raw.get("data") if isinstance(raw, dict) else None
        order_id = str(data.get("id") or data.get("order_id") or "") if isinstance(data, dict) else ""
        return LiveOrderResult(
            exchange_id="gate",
            exchange_order_id=order_id,
            filled=0.0,
            avg_price=0.0,
            raw=raw if isinstance(raw, dict) else {"raw": raw},
        )

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        price: float,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        sd = str(side or "").strip().lower()
        if sd not in {"buy", "sell"}:
            raise LiveTradingError(f"Invalid side: {side}")
        self._assert_side_allowed(symbol=symbol, side=sd)
        quantity, _ = self._normalize_quantity(symbol=symbol, quantity=size, for_market=False)
        limit_price = float(price or 0.0)
        if quantity <= 0 or limit_price <= 0:
            raise LiveTradingError("Invalid Gate stock limit order")
        body: Dict[str, Any] = {
            "volume": _gate_decimal_text(quantity),
            "symbol": self._symbol(symbol),
            "side": 2 if sd == "buy" else 1,
            "price_type": "limit",
            "trading_session": "all",
            "time_in_force": "day",
            "price": _gate_decimal_text(limit_price),
        }
        if client_order_id:
            body["client_order_id"] = str(client_order_id)[:64]
        raw = self._signed_request("POST", "/api/v4/stock/orders", json_body=body)
        data = raw.get("data") if isinstance(raw, dict) else None
        order_id = str(data.get("id") or data.get("order_id") or "") if isinstance(data, dict) else ""
        return LiveOrderResult(
            exchange_id="gate",
            exchange_order_id=order_id,
            filled=0.0,
            avg_price=0.0,
            raw=raw if isinstance(raw, dict) else {"raw": raw},
        )

    def cancel_order(self, *, order_id: str, symbol: str = "") -> Any:
        oid = str(order_id or "").strip()
        if not oid:
            raise LiveTradingError("Gate stock cancel_order requires order_id")
        return self._signed_request("DELETE", f"/api/v4/stock/orders/{oid}")

    def get_order(self, *, order_id: str, symbol: str = "") -> Dict[str, Any]:
        oid = str(order_id or "").strip()
        if not oid:
            raise LiveTradingError("Gate stock get_order requires order_id")
        requests = (
            lambda: self.get_open_orders(symbol=symbol, limit=500),
            lambda: self._signed_request(
                "GET", "/api/v4/stock/orders/history",
                params={"order_ids": oid, "page": 1, "page_size": 20},
            ),
        )
        for request in requests:
            try:
                raw = request()
            except Exception:
                continue
            for row in self._rows(raw):
                if str(row.get("order_id") or row.get("id") or "") == oid:
                    return row
        return {}

    def get_fee_rate(self, symbol: str, market_type: str = "spot") -> Optional[Dict[str, float]]:
        try:
            detail = self.get_symbol_details(symbol=symbol)
            fee = abs(float(detail.get("commission_rate") or 0.0))
            if fee != 0:
                return {"maker": fee, "taker": fee}
        except Exception as exc:
            logger.warning("Gate stock fee lookup failed for %s: %s", symbol, exc)
        return None

    def wait_for_fill(
        self,
        *,
        order_id: str,
        symbol: str,
        max_wait_sec: float = 10.0,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
        deadline = time.time() + max(0.0, float(max_wait_sec or 0.0))
        last: Dict[str, Any] = {}
        while True:
            try:
                last = self.get_order(order_id=order_id, symbol=symbol)
            except Exception:
                pass
            filled = float(last.get("fill_volume") or 0.0)
            avg_price = float(last.get("avg_fill_price") or 0.0)
            fee = float(last.get("commission") or 0.0)
            fee_currency = str(last.get("quote_currency") or self._seed_product_meta.get("quote_currency")
                               or (symbol.rsplit("/", 1)[-1] if "/" in symbol else "")).upper()
            fees = {fee_currency: fee} if fee_currency and last.get("commission") not in (None, "") else {}
            status = str(last.get("status_desc") or last.get("status") or "")
            terminal = status.lower() in {"filled", "cancelled", "canceled", "rejected", "failed"}
            if terminal or time.time() >= deadline:
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_currency if fees else "",
                    "fees_by_ccy": fees,
                    "status": status,
                    "order": last,
                }
            time.sleep(float(poll_interval_sec or 0.5))


class GateUsdtFuturesClient(_GateBase):
    def __init__(self, *, api_key: str, secret_key: str, base_url: str = "https://api.gateio.ws", timeout_sec: float = 15.0, channel_id: str = ""):
        super().__init__(api_key=api_key, secret_key=secret_key, base_url=base_url, timeout_sec=timeout_sec, channel_id=channel_id)
        # Best-effort cache for contract metadata to convert base qty -> contracts.
        self._contract_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._contract_cache_ttl_sec = 300.0
        self._position_mode_cache: Tuple[float, str] = (0.0, "")
        self._position_mode_cache_ttl_sec = 30.0

    @staticmethod
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _floor(value: Decimal) -> Decimal:
        return floor_decimal_to_step(value, Decimal("1"))

    def ping(self) -> bool:
        # Gate futures REST no longer serves /api/v4/futures/usdt/time (returns 400 on fx-api / api hosts).
        # Use a lightweight public list call instead.
        try:
            _ = self._public_request("GET", "/api/v4/futures/usdt/contracts", params={"limit": 1})
            return True
        except Exception:
            return False

    def get_funding_payments(self, *, symbol: str, start_time_ms: int, end_time_ms: int, limit: int = 100):
        contract = to_gate_currency_pair(symbol)
        raw = self._signed_request(
            "GET",
            "/api/v4/futures/usdt/account_book",
            params={"contract": contract, "type": "fund", "from": int(start_time_ms // 1000),
                    "to": int(end_time_ms // 1000), "limit": min(1000, max(1, int(limit or 100)))},
        )
        rows = raw if isinstance(raw, list) else (raw.get("data") or []) if isinstance(raw, dict) else []
        out = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            amount = float(item.get("change") or 0.0)
            seconds = int(float(item.get("time") or item.get("create_time") or 0))
            out.append({
                "id": str(item.get("id") or item.get("trade_id") or f"{seconds}:{amount}"),
                "symbol": str(item.get("contract") or contract), "amount": amount,
                "asset": "USDT", "time": seconds * 1000 if seconds < 10_000_000_000 else seconds,
                "raw": item,
            })
        return out

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        contract = to_gate_currency_pair(symbol)
        raw = self._public_request("GET", "/api/v4/futures/usdt/tickers", params={"contract": contract})
        return _gate_ticker_response_to_normalized(raw)

    def get_contract(self, *, contract: str) -> Dict[str, Any]:
        """Fetch contract metadata with ``X-Gate-Size-Decimal: 1`` to get accurate string-typed size fields."""
        c = str(contract or "").strip()
        if not c:
            return {}
        now = time.time()
        cached = self._contract_cache.get(c)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._contract_cache_ttl_sec or 300.0):
                return obj
        code, data, text = self._request(
            "GET", f"/api/v4/futures/usdt/contracts/{c}",
            params=None, headers={"X-Gate-Size-Decimal": "1"},
            json_body=None, data=None,
        )
        if code >= 400:
            raise LiveTradingError(f"Gate HTTP {code}: {text[:500]}")
        obj = data if isinstance(data, dict) else {}
        if obj:
            self._contract_cache[c] = (now, obj)
        return obj

    @staticmethod
    def _decimal_places(d: Decimal) -> int:
        """Return the number of decimal places in a Decimal value."""
        sign, digits, exponent = d.as_tuple()
        return max(0, -int(exponent))

    def _resolve_order_size(self, *, contract: str, side: str, base_size: float) -> Tuple[str, Optional[Dict[str, str]]]:
        """
        Convert base-asset qty to a signed Gate ``size`` string and determine whether to use
        the ``X-Gate-Size-Decimal`` header.

        Per Gate announcement (2025-12-18):
        - ``size`` is always in **contracts** (not base-asset units).
        - With ``X-Gate-Size-Decimal: 1``, ``size`` becomes a string that supports decimals.
        - A contract supports fractional ordering when ``order_size_min`` (queried with the
          decimal header) contains a fractional part (e.g. ``"0.1"``).
        - Precision must align with ``order_size_min`` (e.g. if min is ``"0.1"`` → 1 dp).
        """
        sd = (side or "").strip().lower()
        sign = Decimal("1") if sd == "buy" else Decimal("-1")
        req = self._to_dec(base_size)
        if req <= 0:
            return ("0", None)

        meta: Dict[str, Any] = {}
        try:
            meta = self.get_contract(contract=contract) or {}
        except Exception:
            meta = {}

        qm = self._to_dec(meta.get("quanto_multiplier") or meta.get("quantoMultiplier") or "0")
        if qm <= 0:
            raise LiveTradingError("strategyRuntime.fillContractMetadataUnavailable")

        contracts = req / qm

        order_min = self._to_dec(meta.get("order_size_min") or "1")
        if order_min <= 0:
            order_min = Decimal("1")
        dp = self._decimal_places(order_min)

        if dp > 0:
            step = Decimal(10) ** (-dp)
            q = floor_decimal_to_step(contracts, step)
            if q < order_min and contracts > 0:
                return ("0", {"X-Gate-Size-Decimal": "1"})
            signed_q = q * sign
            s = format(signed_q, "f")
            if "." in s:
                s = s.rstrip("0").rstrip(".")
            return (s if s and s not in ("-", "+", "-0", "+0", "0") else "0",
                    {"X-Gate-Size-Decimal": "1"})
        else:
            iv = int(self._floor(contracts))
            int_min = max(1, int(order_min))
            if iv < int_min and contracts > 0:
                return ("0", None)
            signed_iv = int(Decimal(iv) * sign)
            return (str(signed_iv), None)

    def _normalize_price(self, *, contract: str, price: float) -> Tuple[Decimal, Optional[int]]:
        """Align a futures price to Gate's contract price increment."""
        value = self._to_dec(price)
        if value <= 0:
            return (Decimal("0"), None)

        metadata: Dict[str, Any] = {}
        try:
            metadata = self.get_contract(contract=contract) or {}
        except Exception:
            metadata = {}

        tick_size = self._to_dec(
            metadata.get("order_price_round")
            or metadata.get("orderPriceRound")
            or "0"
        )
        if tick_size > 0:
            value = floor_decimal_to_step(value, tick_size)

        price_precision = None
        if tick_size > 0:
            try:
                exponent = tick_size.normalize().as_tuple().exponent
                if isinstance(exponent, int):
                    price_precision = min(max(0, -exponent), 18)
            except Exception:
                pass
        return (value, price_precision)

    def _base_to_contracts(self, *, contract: str, base_size: float) -> int:
        """Integer contracts estimate (for internal use like position sizing display)."""
        meta: Dict[str, Any] = {}
        try:
            meta = self.get_contract(contract=contract) or {}
        except Exception:
            meta = {}
        qm = self._to_dec(meta.get("quanto_multiplier") or "0")
        if qm <= 0:
            raise LiveTradingError("strategyRuntime.fillContractMetadataUnavailable")
        return max(1, int(self._floor(self._to_dec(base_size) / qm)))

    def contracts_signed_to_base_qty(self, *, contract: str, contracts_signed: float) -> float:
        """Convert signed position size (contracts) from Gate positions API to base-asset quantity."""
        try:
            ct = abs(float(contracts_signed or 0.0))
        except Exception:
            return 0.0
        if ct <= 0:
            return 0.0
        meta: Dict[str, Any] = {}
        try:
            meta = self.get_contract(contract=str(contract)) or {}
        except Exception:
            meta = {}
        qm = self._to_dec(
            meta.get("quanto_multiplier")
            or meta.get("quantoMultiplier")
            or meta.get("contract_size")
            or meta.get("contractSize")
            or "0"
        )
        if qm <= 0:
            raise LiveTradingError("strategyRuntime.fillContractMetadataUnavailable")
        return float(Decimal(str(ct)) * qm)

    def get_accounts(self) -> Any:
        return self._signed_request("GET", "/api/v4/futures/usdt/accounts")

    def get_position_mode(self) -> str:
        """Return Gate futures ``single`` / ``dual`` / ``dual_plus`` mode."""
        now = time.time()
        cached_at, cached_mode = getattr(self, "_position_mode_cache", (0.0, ""))
        ttl = float(getattr(self, "_position_mode_cache_ttl_sec", 30.0) or 30.0)
        if cached_mode and (now - float(cached_at or 0.0)) <= ttl:
            return cached_mode

        account = self.get_accounts() or {}
        if not isinstance(account, dict):
            return ""
        mode = str(account.get("position_mode") or "").strip().lower()
        if mode not in ("single", "dual", "dual_plus"):
            dual = account.get("in_dual_mode")
            if dual is True:
                mode = "dual"
            elif dual is False:
                mode = "single"
            else:
                mode = ""
        if mode:
            self._position_mode_cache = (now, mode)
        return mode

    def is_hedge_position_mode(self, *, symbol: str = "") -> Optional[bool]:
        _ = symbol
        mode = self.get_position_mode()
        if mode == "dual":
            return True
        if mode == "single":
            return False
        # dual_plus is Gate's split-position mode. It can create multiple
        # independent same-side positions and requires position/margin routing
        # fields that do not map to this system's one-long + one-short ledger.
        # Do not misreport it as the standard dual mode.
        return None

    def get_positions(self) -> Any:
        # Gate exposes one collection endpoint for every supported position
        # mode.  In dual mode the response contains separate ``dual_long`` and
        # ``dual_short`` rows.  ``dual_comp/positions`` is not a collection
        # endpoint: Gate only defines ``dual_comp/positions/{contract}``, so
        # routing a dual account there without a contract produces a bare 404.
        path = "/api/v4/futures/usdt/positions"
        return self._signed_request(
            "GET", path,
            extra_headers={"X-Gate-Size-Decimal": "1"},
        )

    def set_leverage(self, *, contract: str, leverage: float, margin_mode: str = "cross") -> bool:
        c = str(contract or "").strip()
        if not c:
            return False
        try:
            lv = int(float(leverage or 1.0))
        except Exception:
            lv = 1
        if lv < 1:
            lv = 1
        try:
            meta = self.get_contract(contract=c) or {}
        except Exception:
            meta = {}
        try:
            max_leverage = int(float(meta.get("leverage_max") or meta.get("leverageMax") or 0))
        except (TypeError, ValueError):
            max_leverage = 0
        if max_leverage > 0 and lv > max_leverage:
            raise LiveTradingError(
                f"Gate leverage {lv}x exceeds the current {c} maximum {max_leverage}x"
            )

        position_mode = self.get_position_mode()
        if position_mode in ("dual", "dual_plus"):
            path = f"/api/v4/futures/usdt/dual_comp/positions/{c}/leverage"
        else:
            path = f"/api/v4/futures/usdt/positions/{c}/leverage"
        lv_s = str(lv)
        # Gate expects ``leverage`` / ``cross_leverage_limit`` as **query parameters**, not JSON body
        # (see gateapi-python: update_position_leverage). Cross / portfolio mode: leverage=0 + cross_leverage_limit.
        mode = str(margin_mode or "cross").strip().lower()
        if mode in ("cross", "crossed"):
            params = {"leverage": "0", "cross_leverage_limit": lv_s}
        elif mode in ("isolated", "iso"):
            params = {"leverage": lv_s}
        else:
            return False
        response = self._signed_request("POST", path, params=params, json_body=None)
        if isinstance(response, dict):
            effective_raw = (
                response.get("cross_leverage_limit")
                if mode in ("cross", "crossed")
                else response.get("leverage")
            )
            if effective_raw not in (None, ""):
                try:
                    effective = int(float(effective_raw))
                except (TypeError, ValueError) as exc:
                    raise LiveTradingError(
                        f"Gate returned an invalid effective leverage: {effective_raw}"
                    ) from exc
                if effective != lv:
                    raise LiveTradingError(
                        f"Gate applied {effective}x instead of requested {lv}x leverage"
                    )
        return True

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        base_qty = float(size or 0.0)
        if base_qty <= 0:
            raise LiveTradingError("Invalid size (<= 0)")
        contract = to_gate_currency_pair(symbol)
        size_str, extra_headers = self._resolve_order_size(contract=contract, side=sd, base_size=base_qty)
        if size_str in ("0", "-0", ""):
            raise LiveTradingError("Invalid size (resolved contracts == 0)")
        logger.info("Gate futures market: contract=%s side=%s base_qty=%s size_str=%s decimal_hdr=%s",
                     contract, sd, base_qty, size_str, extra_headers is not None)
        body: Dict[str, Any] = {"contract": contract, "size": size_str, "price": "0", "tif": "ioc"}
        if reduce_only:
            body["reduce_only"] = True
        text = self._format_text(client_order_id)
        if text:
            body["text"] = text
        raw = self._signed_request(
            "POST",
            "/api/v4/futures/usdt/orders",
            json_body=body,
            extra_headers=extra_headers,
        )
        oid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        return LiveOrderResult(exchange_id="gate", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw if isinstance(raw, dict) else {"raw": raw})

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        price: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        base_qty = float(size or 0.0)
        if base_qty <= 0:
            raise LiveTradingError("Invalid size (<= 0)")
        px = float(price or 0.0)
        if px <= 0:
            raise LiveTradingError("Invalid price")
        contract = to_gate_currency_pair(symbol)
        size_str, extra_headers = self._resolve_order_size(contract=contract, side=sd, base_size=base_qty)
        if size_str in ("0", "-0", ""):
            raise LiveTradingError("Invalid size (resolved contracts == 0)")
        price_dec, _price_precision = self._normalize_price(contract=contract, price=px)
        if price_dec <= 0:
            raise LiveTradingError("Invalid price (below contract tick)")
        body: Dict[str, Any] = {
            "contract": contract,
            "size": size_str,
            "price": _gate_decimal_text(price_dec),
            "tif": "gtc",
        }
        if reduce_only:
            body["reduce_only"] = True
        text = self._format_text(client_order_id)
        if text:
            body["text"] = text
        raw = self._signed_request(
            "POST",
            "/api/v4/futures/usdt/orders",
            json_body=body,
            extra_headers=extra_headers,
        )
        oid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        return LiveOrderResult(exchange_id="gate", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw if isinstance(raw, dict) else {"raw": raw})

    def cancel_order(self, *, order_id: str) -> Any:
        if not order_id:
            raise LiveTradingError("Gate futures cancel_order requires order_id")
        return self._signed_request("DELETE", f"/api/v4/futures/usdt/orders/{str(order_id)}")

    def get_order(self, *, order_id: str) -> Any:
        if not order_id:
            raise LiveTradingError("Gate futures get_order requires order_id")
        return self._signed_request("GET", f"/api/v4/futures/usdt/orders/{str(order_id)}")

    def get_open_orders(self, *, limit: int = 100) -> Any:
        """Return current USDT-settled futures orders across every contract."""
        page_limit = min(100, max(1, int(limit or 100)))
        return self._signed_request(
            "GET",
            "/api/v4/futures/usdt/orders",
            params={"status": "open", "limit": page_limit, "offset": 0},
        )

    def get_futures_trades_for_order(self, *, order_id: str, contract: str) -> Tuple[float, str]:
        """Aggregate the actual fee from Gate USDT futures fill history.

        Gate's USDT futures ``order`` object often reports ``fee=0`` even after
        the order is fully filled — the fee is only finalised when the trade
        row appears in ``GET /api/v4/futures/usdt/my_trades``. We sum the
        ``fee`` column there (in USDT). Note that the filter parameter on this
        endpoint is ``order`` (not ``order_id``). Returns ``(0.0, "")`` on any
        failure so callers can transparently fall back to the order-level fee.
        Reference: https://www.gate.com/docs/developers/apiv4/#list-personal-trading-history-2
        """
        oid = str(order_id or "").strip()
        c = str(contract or "").strip()
        if not oid or not c:
            return 0.0, ""
        try:
            resp = self._order_trade_rows('/api/v4/futures/usdt/my_trades', {'contract': c, 'order': oid})
        except Exception:
            return 0.0, ''
        total = 0.0
        for t in resp:
            if not isinstance(t, dict):
                continue
            try:
                v = float(t.get("fee") or 0.0)
            except Exception:
                v = 0.0
            total += v
        if resp:
            # Gate USDT-margined perpetuals settle fees in USDT.
            return total, "USDT"
        return 0.0, ""

    def wait_for_fill(self, *, order_id: str, contract: str, max_wait_sec: float = 12.0, poll_interval_sec: float = 0.5) -> Dict[str, Any]:
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last: Dict[str, Any] = {}
        from app.services.live_trading.fill_accounting import contract_multiplier
        qm = Decimal(str(contract_multiplier(self, 'gate', str(contract).replace('_', '/'))))
        while True:
            timed_out = time.time() >= end_ts
            try:
                resp = self.get_order(order_id=str(order_id))
                last = resp if isinstance(resp, dict) else {"raw": resp}
            except Exception:
                last = last or {}
            status = str(last.get("status") or "")
            filled = 0.0
            avg_price = 0.0
            fee = 0.0
            fee_ccy = ""
            try:
                # Gate futures often returns "filled_size" in contracts.
                filled_ct = abs(float(last.get("filled_size") or last.get("filledSize") or 0.0))
                if filled_ct <= 0:
                    size_ct = abs(float(last.get("size") or 0.0))
                    left_ct = abs(float(last.get("left") or 0.0))
                    if size_ct > 0:
                        filled_ct = max(0.0, size_ct - left_ct)
                filled = float(Decimal(str(filled_ct)) * qm)
            except Exception:
                filled = 0.0
            try:
                avg_price = float(last.get("fill_price") or last.get("fillPrice") or 0.0)
            except Exception:
                avg_price = 0.0
            # Extract fee from Gate Futures API
            try:
                fee = float(last.get("fee") or 0.0)
            except Exception:
                fee = 0.0
            # Gate USDT futures fees are in USDT
            if fee != 0:
                fee_ccy = "USDT"
            # Gate USDT futures order objects routinely report ``fee=0`` even
            # after the order is fully filled; the authoritative source is
            # /futures/usdt/my_trades (filtered by the ``order`` param). Pull
            # from there so commissions stop landing in ``qd_strategy_trades``
            # as zero and P&L stops drifting.
            if filled > 0 and fee == 0:
                try:
                    mt_fee, mt_ccy = self.get_futures_trades_for_order(
                        order_id=str(order_id),
                        contract=str(contract),
                    )
                except Exception:
                    mt_fee, mt_ccy = 0.0, ""
                if mt_ccy:
                    fee = mt_fee
                    fee_ccy = mt_ccy or "USDT"
            if filled > 0 and avg_price > 0:
                if not fee_ccy and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "fees_by_ccy": {fee_ccy: fee} if fee_ccy else {}, "status": status, "order": last}
            if str(status).lower() in ("finished", "cancelled", "canceled"):
                if not fee_ccy and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "fees_by_ccy": {fee_ccy: fee} if fee_ccy else {}, "status": status, "order": last}
            if timed_out:
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "fees_by_ccy": {fee_ccy: fee} if fee_ccy else {}, "status": status, "order": last}
            time.sleep(float(poll_interval_sec or 0.5))
