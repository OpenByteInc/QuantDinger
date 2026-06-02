"""
Deepcoin (direct REST) client for spot / perpetual swap orders.

Based on official Deepcoin Python SDK example.
API Base URL: https://api.deepcoin.com

Signing:
- DC-ACCESS-SIGN = base64(hmac_sha256(secret, timestamp + method + uri + body))
- For GET: message = timestamp + method + uri (with query params)
- For POST: message = timestamp + method + uri + json_body
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import requests

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.symbols import to_deepcoin_symbol


class DeepcoinClient(BaseRestClient):
    """
    Deepcoin REST client for spot and perpetual swap trading.
    
    Based on official Deepcoin Python SDK.
    Supports both spot and swap (perpetual futures) markets.
    """
    
    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        passphrase: str = "",
        base_url: str = "https://api.deepcoin.com",
        timeout_sec: float = 15.0,
        market_type: str = "swap",  # "swap" (perpetual) or "spot"
    ):
        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.passphrase = (passphrase or "").strip()
        self.market_type = (market_type or "swap").strip().lower()
        if self.market_type not in ("swap", "spot"):
            self.market_type = "swap"
        
        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing Deepcoin api_key/secret_key")

        # Best-effort cache for instrument metadata (qty step, min qty, etc.)
        # Key: f"{market_type}:{symbol}" -> (fetched_at_ts, info_dict)
        self._inst_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._inst_cache_ttl_sec = 300.0

        # Best-effort cache for leverage settings
        self._lev_cache: Dict[str, Tuple[float, bool]] = {}
        self._lev_cache_ttl_sec = 60.0

    @staticmethod
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _dec_str(d: Decimal, max_decimals: int = 18, strict_precision: Optional[int] = None) -> str:
        """
        Convert Decimal to string with controlled precision.
        Deepcoin requires quantities to match lotSz/qtyStep precision.
        
        Args:
            d: Decimal value to format
            max_decimals: Maximum decimal places (fallback if strict_precision not provided)
            strict_precision: If provided, strictly limit to this many decimal places
        """
        try:
            if d == 0:
                return "0"
            normalized = d.normalize()
            
            if strict_precision is not None:
                try:
                    prec = int(strict_precision)
                    if 0 <= prec <= 18:
                        from decimal import ROUND_DOWN
                        q = Decimal("1").scaleb(-prec)
                        quantized = normalized.quantize(q, rounding=ROUND_DOWN)
                        s = format(quantized, f".{prec}f")
                        if '.' in s:
                            s = s.rstrip('0').rstrip('.')
                        return s if s else "0"
                except Exception:
                    pass
            
            s = format(normalized, f".{max_decimals}f")
            if '.' in s:
                s = s.rstrip('0').rstrip('.')
            return s if s else "0"
        except Exception:
            try:
                f = float(d)
                if f == 0:
                    return "0"
                if strict_precision is not None:
                    try:
                        prec = int(strict_precision)
                        if 0 <= prec <= 18:
                            s = format(f, f".{prec}f")
                            if '.' in s:
                                s = s.rstrip('0').rstrip('.')
                            return s if s else "0"
                    except Exception:
                        pass
                s = format(f, f".{max_decimals}f")
                if '.' in s:
                    s = s.rstrip('0').rstrip('.')
                return s if s else "0"
            except Exception:
                s = str(d)
                if 'e' in s.lower() or 'E' in s:
                    try:
                        f = float(s)
                        if strict_precision is not None:
                            try:
                                prec = int(strict_precision)
                                if 0 <= prec <= 18:
                                    s = format(f, f".{prec}f")
                                    if '.' in s:
                                        s = s.rstrip('0').rstrip('.')
                                    return s if s else "0"
                            except Exception:
                                pass
                        s = format(f, f".{max_decimals}f")
                        if '.' in s:
                            s = s.rstrip('0').rstrip('.')
                    except Exception:
                        pass
                return s if s else "0"

    @staticmethod
    def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
        if step is None:
            return value
        if value <= 0:
            return Decimal("0")
        try:
            st = Decimal(step)
        except Exception:
            st = Decimal("0")
        if st <= 0:
            return value
        try:
            n = (value / st).to_integral_value(rounding=ROUND_DOWN)
            return n * st
        except Exception:
            return Decimal("0")

    def _get_iso_time(self) -> str:
        """
        Generate ISO 8601 timestamp for Deepcoin API.
        Format: 2024-07-29T11:12:00.123Z
        """
        ticks = time.time()
        localdate = datetime.datetime.utcfromtimestamp(ticks)
        iso_time = localdate.isoformat()
        # Ensure milliseconds and Z suffix
        iso_time_change = iso_time[:23] + "Z"
        return iso_time_change

    def _build_uri_with_params(self, uri: str, params: Optional[Dict[str, Any]], method: str) -> str:
        """
        Build URI with query parameters for GET requests.
        For POST requests, return URI as-is.
        """
        if method.upper() == "GET" and params and params != {}:
            query_parts = []
            for key, value in params.items():
                query_parts.append(f"{key}={value}")
            query_string = "&".join(query_parts)
            return f"{uri}?{query_string}"
        return uri

    def _sign(self, iso_time: str, method: str, uri: str, data: Optional[Dict[str, Any]] = None) -> str:
        """
        Generate HMAC-SHA256 signature for request authentication.
        
        For POST: message = timestamp + method + uri + json_body
        For GET: message = timestamp + method + uri (with query params)
        """
        method_upper = method.upper()
        if method_upper == "POST" and data:
            # Convert dict to JSON string with double quotes
            data_str = json.dumps(data, separators=(',', ':'))
            message = f"{iso_time}{method_upper}{uri}{data_str}"
        else:
            message = f"{iso_time}{method_upper}{uri}"
        
        message_bytes = message.encode('utf-8')
        key_bytes = self.secret_key.encode('utf-8')
        sign = base64.b64encode(
            hmac.new(key=key_bytes, msg=message_bytes, digestmod=hashlib.sha256).digest()
        ).decode('utf-8')
        return sign

    def _headers(self, iso_time: str, sign: str) -> Dict[str, str]:
        """
        Build authenticated request headers.
        """
        headers = {
            "DC-ACCESS-KEY": self.api_key,
            "DC-ACCESS-SIGN": sign,
            "DC-ACCESS-TIMESTAMP": iso_time,
            "DC-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "appid": "200103",
        }
        return headers

    def _public_request(self, method: str, uri: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make public (unauthenticated) API request.
        """
        full_uri = self._build_uri_with_params(uri, params, method)
        url = f"{self.base_url}{full_uri}"
        
        try:
            if method.upper() == "GET":
                resp = requests.get(url=url, timeout=self.timeout_sec)
            else:
                resp = requests.post(url=url, json=params, timeout=self.timeout_sec)
            
            if resp.status_code >= 400:
                raise LiveTradingError(f"Deepcoin HTTP {resp.status_code}: {resp.text[:500]}")
            
            data = resp.json()
            if isinstance(data, dict):
                code = data.get("code") or data.get("retCode")
                if code not in (0, "0", None, "", "00000"):
                    raise LiveTradingError(f"Deepcoin error: {data}")
            return data if isinstance(data, dict) else {"raw": data}
        except requests.RequestException as e:
            raise LiveTradingError(f"Deepcoin request failed: {str(e)}")

    def _signed_request(
        self,
        method: str,
        uri: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make authenticated API request following Deepcoin signing spec.
        
        For GET requests: params are appended to URI as query string
        For POST requests: params are sent as JSON body
        """
        iso_time = self._get_iso_time()
        method_upper = method.upper()
        
        # Build full URI (with query params for GET)
        full_uri = self._build_uri_with_params(uri, params, method)
        
        # Generate signature
        if method_upper == "POST":
            sign = self._sign(iso_time, method_upper, uri, params)
        else:
            sign = self._sign(iso_time, method_upper, full_uri, None)
        
        headers = self._headers(iso_time, sign)
        url = f"{self.base_url}{full_uri}"
        
        try:
            if method_upper == "POST":
                body_str = json.dumps(params, separators=(',', ':')) if params else ""
                resp = requests.post(url=url, headers=headers, data=body_str, timeout=self.timeout_sec)
            else:
                resp = requests.get(url=url, headers=headers, timeout=self.timeout_sec)
            
            if resp.status_code >= 400:
                raise LiveTradingError(f"Deepcoin HTTP {resp.status_code}: {resp.text[:500]}")
            
            data = resp.json()
            if isinstance(data, dict):
                code = data.get("code") or data.get("retCode")
                if code not in (0, "0", None, "", "00000"):
                    raise LiveTradingError(f"Deepcoin error: {data}")
            return data if isinstance(data, dict) else {"raw": data}
        except requests.RequestException as e:
            raise LiveTradingError(f"Deepcoin request failed: {str(e)}")

    def ping(self) -> bool:
        """
        Test API connectivity using public endpoint.
        """
        try:
            # Try public endpoint to check connectivity
            url = f"{self.base_url}/deepcoin/market/time"
            resp = requests.get(url=url, timeout=self.timeout_sec)
            return resp.status_code == 200
        except Exception:
            return False

    def get_balance(self) -> Dict[str, Any]:
        """
        Get account balance.
        
        Endpoint: GET /deepcoin/account/balances
        """
        params = {"instType": "SWAP" if self.market_type == "swap" else "SPOT"}
        return self._signed_request("GET", "/deepcoin/account/balances", params=params)

    def get_positions(self, *, symbol: str = "") -> Dict[str, Any]:
        """
        Get open positions.
        
        Endpoint: GET /deepcoin/account/positions
        """
        params: Dict[str, Any] = {"instType": "SWAP" if self.market_type == "swap" else "SPOT"}
        if symbol:
            params["instId"] = to_deepcoin_symbol(symbol)
        return self._signed_request("GET", "/deepcoin/account/positions", params=params)

    def set_leverage(self, *, symbol: str, leverage: float, mgn_mode: str = "cross") -> bool:
        """
        Set leverage for a trading pair.
        
        Endpoint: POST /deepcoin/account/set-leverage
        """
        sym = to_deepcoin_symbol(symbol)
        if not sym:
            return False
        
        try:
            lv = int(float(leverage or 1.0))
        except Exception:
            lv = 1
        if lv < 1:
            lv = 1
        
        mm = str(mgn_mode or "cross").strip().lower()
        if mm not in ("cross", "isolated"):
            mm = "cross"

        # Check cache
        cache_key = f"{sym}:{mm}:{lv}"
        now = time.time()
        cached = self._lev_cache.get(cache_key)
        if cached:
            ts, ok = cached
            if ok and (now - float(ts or 0.0)) <= float(self._lev_cache_ttl_sec or 60.0):
                return True

        params = {
            "instId": sym,
            "lever": str(lv),
            "mgnMode": mm,
            "mrgPosition": "merge",
        }
        
        try:
            self._signed_request("POST", "/deepcoin/account/set-leverage", params=params)
            self._lev_cache[cache_key] = (now, True)
            return True
        except Exception:
            return False

    def get_instrument_info(self, *, symbol: str) -> Dict[str, Any]:
        """
        Get instrument metadata (min qty, qty step, etc.).
        
        Endpoint: GET /deepcoin/market/instruments
        """
        sym = to_deepcoin_symbol(symbol)
        if not sym:
            return {}
        
        key = f"{self.market_type}:{sym}"
        now = time.time()
        cached = self._inst_cache.get(key)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._inst_cache_ttl_sec or 300.0):
                return obj

        inst_type = "SWAP" if self.market_type == "swap" else "SPOT"
        params = {"instType": inst_type, "instId": sym}
        
        try:
            raw = self._public_request("GET", "/deepcoin/market/instruments", params=params)
            data = (raw.get("data") or []) if isinstance(raw, dict) else []
            first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
            if isinstance(first, dict) and first:
                self._inst_cache[key] = (now, first)
            return first if isinstance(first, dict) else {}
        except Exception:
            return {}

    def _normalize_qty(self, *, symbol: str, qty: float) -> Tuple[Decimal, Optional[int]]:
        """
        Normalize order quantity to exchange requirements.
        
        Returns:
            Tuple of (normalized_quantity, precision) where precision is the number of decimal places required.
        """
        q = self._to_dec(qty)
        if q <= 0:
            return (Decimal("0"), None)
        
        sym = to_deepcoin_symbol(symbol)
        try:
            info = self.get_instrument_info(symbol=sym) or {}
        except Exception:
            info = {}
        
        # Extract lot size filter
        step = self._to_dec(info.get("lotSz") or info.get("qtyStep") or "0")
        mn = self._to_dec(info.get("minSz") or info.get("minOrderQty") or "0")
        
        if step > 0:
            q = self._floor_to_step(q, step)
        
        # Infer precision from step
        qty_precision = None
        if step > 0:
            try:
                step_normalized = step.normalize()
                step_str = str(step_normalized)
                if '.' in step_str:
                    decimal_part = step_str.split('.')[1]
                    qty_precision = len(decimal_part)
                    if qty_precision < 0:
                        qty_precision = 0
                    if qty_precision > 18:
                        qty_precision = 18
                else:
                    qty_precision = 0
            except Exception:
                pass
        
        if mn > 0 and q < mn:
            return (Decimal("0"), qty_precision)
        return (q, qty_precision)

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        reduce_only: bool = False,
        pos_side: str = "",
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        """
        Place a market order.
        
        Endpoint: POST /deepcoin/trade/order
        
        Args:
            symbol: Trading pair (e.g., "BTC/USDT:USDT" or "BTCUSDT")
            side: "buy" or "sell"
            qty: Order quantity in base currency
            reduce_only: If True, only reduce position (for futures)
            pos_side: Position side for hedge mode ("long" or "short")
            client_order_id: Optional client order ID
        """
        sym = to_deepcoin_symbol(symbol)
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        
        q_req = float(qty or 0.0)
        q_dec, qty_precision = self._normalize_qty(symbol=symbol, qty=q_req)
        if float(q_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid qty (below step/min): requested={q_req}")

        params: Dict[str, Any] = {
            "instId": sym,
            "tdMode": "cash" if self.market_type == "spot" else "cross",
            "side": sd,
            "ordType": "market",
            "sz": self._dec_str(q_dec, strict_precision=qty_precision),
        }
        
        if self.market_type != "spot":
            ps = (pos_side or "").strip().lower()
            if ps in ("long", "short", "net"):
                params["posSide"] = ps
            if reduce_only:
                params["reduceOnly"] = True

        if client_order_id:
            params["clOrdId"] = str(client_order_id)

        raw = self._signed_request("POST", "/deepcoin/trade/order", params=params)
        data = (raw.get("data") or []) if isinstance(raw, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        oid = str(first.get("ordId") or first.get("orderId") or first.get("clOrdId") or "")
        
        return LiveOrderResult(
            exchange_id="deepcoin",
            exchange_order_id=oid,
            filled=0.0,
            avg_price=0.0,
            raw=raw,
        )

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        reduce_only: bool = False,
        pos_side: str = "",
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        """
        Place a limit order.
        
        Endpoint: POST /deepcoin/trade/order
        """
        sym = to_deepcoin_symbol(symbol)
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        
        q_req = float(qty or 0.0)
        px = float(price or 0.0)
        if q_req <= 0 or px <= 0:
            raise LiveTradingError("Invalid qty/price")
        
        q_dec, qty_precision = self._normalize_qty(symbol=symbol, qty=q_req)
        if float(q_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid qty (below step/min): requested={q_req}")

        params: Dict[str, Any] = {
            "instId": sym,
            "tdMode": "cash" if self.market_type == "spot" else "cross",
            "side": sd,
            "ordType": "limit",
            "sz": self._dec_str(q_dec, strict_precision=qty_precision),
            "px": str(px),
        }
        
        if self.market_type != "spot":
            ps = (pos_side or "").strip().lower()
            if ps in ("long", "short", "net"):
                params["posSide"] = ps
            if reduce_only:
                params["reduceOnly"] = True

        if client_order_id:
            params["clOrdId"] = str(client_order_id)

        raw = self._signed_request("POST", "/deepcoin/trade/order", params=params)
        data = (raw.get("data") or []) if isinstance(raw, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        oid = str(first.get("ordId") or first.get("orderId") or first.get("clOrdId") or "")
        
        return LiveOrderResult(
            exchange_id="deepcoin",
            exchange_order_id=oid,
            filled=0.0,
            avg_price=0.0,
            raw=raw,
        )

    def cancel_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        """
        Cancel an order.
        
        Endpoint: POST /deepcoin/trade/cancel-order
        """
        sym = to_deepcoin_symbol(symbol)
        params: Dict[str, Any] = {"instId": sym}
        
        if order_id:
            params["ordId"] = str(order_id)
        elif client_order_id:
            params["clOrdId"] = str(client_order_id)
        else:
            raise LiveTradingError("Deepcoin cancel_order requires order_id or client_order_id")

        return self._signed_request("POST", "/deepcoin/trade/cancel-order", params=params)

    def get_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        """
        Get order details.
        
        Endpoint: GET /deepcoin/trade/order
        """
        sym = to_deepcoin_symbol(symbol)
        params: Dict[str, Any] = {"instId": sym}
        
        if order_id:
            params["ordId"] = str(order_id)
        elif client_order_id:
            params["clOrdId"] = str(client_order_id)
        else:
            raise LiveTradingError("Deepcoin get_order requires order_id or client_order_id")

        resp = self._signed_request("GET", "/deepcoin/trade/order", params=params)
        data = (resp.get("data") or []) if isinstance(resp, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        return first

    def get_open_orders(self, *, symbol: str = "") -> Dict[str, Any]:
        """
        Get open orders.
        
        Endpoint: GET /deepcoin/trade/orders-pending
        """
        params: Dict[str, Any] = {"instType": "SWAP" if self.market_type == "swap" else "SPOT"}
        if symbol:
            params["instId"] = to_deepcoin_symbol(symbol)
        return self._signed_request("GET", "/deepcoin/trade/orders-pending", params=params)

    def get_order_history(self, *, symbol: str = "", limit: int = 100) -> Dict[str, Any]:
        """
        Get order history.
        
        Endpoint: GET /deepcoin/trade/orders-history
        """
        params: Dict[str, Any] = {
            "instType": "SWAP" if self.market_type == "swap" else "SPOT",
            "limit": str(limit),
        }
        if symbol:
            params["instId"] = to_deepcoin_symbol(symbol)
        return self._signed_request("GET", "/deepcoin/trade/orders-history", params=params)

    def wait_for_fill(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
        max_wait_sec: float = 3.0,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Poll order status until filled or timeout.
        
        Returns:
            {
                "filled": float,
                "avg_price": float,
                "fee": float,
                "fee_ccy": str,
                "status": str,
                "order": {...}
            }
        """
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last: Dict[str, Any] = {}

        # SWAP accFillSz/fillSz are contracts (OKX-compatible); SPOT is base currency.
        ct_val = Decimal("1")
        if self.market_type != "spot":
            try:
                info = self.get_instrument_info(symbol=str(symbol)) or {}
                ct_val = self._to_dec(
                    info.get("ctVal") or info.get("ct_val") or info.get("contractSize") or "0"
                )
            except Exception:
                ct_val = Decimal("0")
            if ct_val <= 0:
                ct_val = Decimal("1")

        while True:
            timed_out = time.time() >= end_ts
            try:
                last = self.get_order(
                    symbol=symbol,
                    order_id=str(order_id or ""),
                    client_order_id=str(client_order_id or ""),
                )
            except Exception:
                last = last or {}
            
            status = str(last.get("state") or last.get("status") or last.get("orderStatus") or "")

            try:
                filled_raw = self._to_dec(
                    last.get("accFillSz") or last.get("fillSz") or last.get("cumExecQty") or "0"
                )
            except Exception:
                filled_raw = Decimal("0")

            filled_base = filled_raw if self.market_type == "spot" else filled_raw * ct_val
            try:
                filled = float(filled_base or 0)
            except Exception:
                filled = 0.0
            
            try:
                avg_price = float(last.get("avgPx") or last.get("fillPx") or last.get("avgPrice") or 0.0)
            except Exception:
                avg_price = 0.0
            
            # Extract fee
            fee = 0.0
            fee_ccy = ""
            try:
                fee = abs(float(last.get("fee") or last.get("cumExecFee") or 0.0))
                fee_ccy = str(last.get("feeCcy") or "")
            except Exception:
                pass

            if filled > 0 and avg_price > 0:
                if fee <= 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "status": status,
                    "order": last,
                }

            if status.lower() in ("filled", "cancelled", "canceled", "rejected"):
                if fee <= 0 and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "status": status,
                    "order": last,
                }

            if timed_out:
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "status": status,
                    "order": last,
                }
            
            time.sleep(float(poll_interval_sec or 0.5))
