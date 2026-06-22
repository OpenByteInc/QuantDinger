"""
OKX (direct REST) client for perpetual swap orders.

Signing:
- OK-ACCESS-SIGN = base64(hmac_sha256(secret, timestamp + method + request_path + body))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError

logger = logging.getLogger(__name__)
from app.services.live_trading.symbols import to_okx_swap_inst_id, to_okx_spot_inst_id


class OkxClient(BaseRestClient):
    _DEFAULT_BROKER_CODE = "56fa80b0ce8cBCDE"

    @staticmethod
    def _ensure_header_safe_ascii(value: str, *, field: str) -> str:
        """
        requests/http.client encodes header values using latin-1.
        OKX credentials should be ASCII; if a user pastes non-ASCII (Chinese, full-width chars, smart quotes),
        requests will crash before any network call is made.
        """
        s = (value or "").strip()
        try:
            s.encode("latin-1")
        except Exception:
            raise LiveTradingError(
                f"OKX {field} contains non-ASCII characters and cannot be sent in HTTP headers. "
                "Please re-copy the value from OKX (ASCII only; avoid Chinese characters, smart quotes, "
                "full-width symbols, newlines, and trailing spaces)."
            )
        return s

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        passphrase: str,
        base_url: str = "https://www.okx.com",
        timeout_sec: float = 15.0,
        broker_code: Optional[str] = None,
        simulated_trading: bool = False,
    ):
        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = self._ensure_header_safe_ascii(api_key, field="api_key")
        # secret_key is used for signing (utf-8 bytes), but we still strip to avoid invisible whitespace.
        self.secret_key = (secret_key or "").strip()
        self.passphrase = self._ensure_header_safe_ascii(passphrase, field="passphrase")
        self.simulated_trading = bool(simulated_trading)
        effective_broker = broker_code or self._DEFAULT_BROKER_CODE
        self.broker_code = str(effective_broker).strip() if effective_broker else None
        if not self.api_key or not self.secret_key or not self.passphrase:
            raise LiveTradingError("Missing OKX api_key/secret_key/passphrase")

        # Best-effort cache for public instrument metadata used to normalize order sizes.
        # Key: f"{inst_type}:{inst_id}" -> (fetched_at_ts, instrument_dict)
        self._inst_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._inst_cache_ttl_sec = 300.0

        # Best-effort cache for account config (position mode).
        # Key: "account_config" -> (fetched_at_ts, config_dict)
        self._acct_cfg_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._acct_cfg_cache_ttl_sec = 30.0

        # Best-effort cache for leverage settings to avoid spamming set-leverage on every tick.
        # Key: f"{inst_id}:{mgn_mode}:{pos_side}:{lever}" -> (fetched_at_ts, True)
        self._lev_cache: Dict[str, Tuple[float, bool]] = {}
        self._lev_cache_ttl_sec = 60.0

    @staticmethod
    def _dec_str(d: Decimal, max_decimals: int = 18, strict_precision: Optional[int] = None) -> str:
        """
        Convert Decimal to a non-scientific string with controlled precision.
        OKX expects plain decimal strings matching lotSz precision.
        
        Args:
            d: Decimal value to format
            max_decimals: Maximum decimal places (fallback if strict_precision not provided)
            strict_precision: If provided, strictly limit to this many decimal places
        """
        try:
            if d == 0:
                return "0"
            # Normalize to remove unnecessary trailing zeros
            normalized = d.normalize()
            
            # If strict_precision is provided, use it and strictly limit decimal places
            if strict_precision is not None:
                try:
                    prec = int(strict_precision)
                    if prec < 0:
                        prec = 0
                    if prec > 18:
                        prec = 18
                    # Use quantize to ensure exact precision
                    from decimal import ROUND_DOWN
                    q = Decimal("1").scaleb(-prec)
                    quantized = normalized.quantize(q, rounding=ROUND_DOWN)
                    s = format(quantized, f".{prec}f")
                    if '.' in s:
                        s = s.rstrip('0').rstrip('.')
                    return s if s else "0"
                except Exception:
                    pass
            
            # Format with max_decimals and remove trailing zeros
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
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
        if step is None:
            return value
        try:
            st = Decimal(step)
        except Exception:
            st = Decimal("0")
        if st <= 0:
            return value
        if value <= 0:
            return Decimal("0")
        try:
            n = (value / st).to_integral_value(rounding=ROUND_DOWN)
            return n * st
        except Exception:
            return Decimal("0")

    def _public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        code, data, text = self._request(method, path, params=params, json_body=None, headers=None, data=None)
        if code >= 400:
            raise LiveTradingError(f"OKX HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and str(data.get("code") or "") not in ("0", ""):
            raise LiveTradingError(f"OKX error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def get_instrument(self, *, inst_type: str, inst_id: str) -> Dict[str, Any]:
        """
        Fetch OKX instrument metadata from public endpoint:
        GET /api/v5/public/instruments?instType=...&instId=...
        """
        it = str(inst_type or "").strip().upper()
        iid = str(inst_id or "").strip()
        if not it or not iid:
            return {}

        key = f"{it}:{iid}"
        now = time.time()
        cached = self._inst_cache.get(key)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._inst_cache_ttl_sec or 300.0):
                return obj

        raw = self._public_request("GET", "/api/v5/public/instruments", params={"instType": it, "instId": iid})
        data = (raw.get("data") or []) if isinstance(raw, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        if isinstance(first, dict) and first:
            self._inst_cache[key] = (now, first)
        return first if isinstance(first, dict) else {}

    def _normalize_order_size(self, *, inst_id: str, market_type: str, size: float) -> Tuple[Decimal, Optional[int]]:
        """
        Normalize requested size to OKX constraints:
        - Spot: size is base currency quantity; align to lotSz/minSz.
        - Swap: OKX sz is in contracts; convert base qty -> contracts using ctVal, then align to lotSz/minSz.

        Note: this system passes `amount` around as base-asset quantity across exchanges.
        
        Returns:
            Tuple of (normalized_size, precision) where precision is the number of decimal places required.
        """
        mt = (market_type or "swap").strip().lower()
        iid = str(inst_id or "").strip()
        req = self._to_dec(size)
        if req <= 0:
            return (Decimal("0"), None)

        inst_type = "SPOT" if mt == "spot" else "SWAP"
        inst: Dict[str, Any] = {}
        if iid:
            try:
                inst = self.get_instrument(inst_type=inst_type, inst_id=iid) or {}
            except Exception:
                inst = {}

        lot_sz = self._to_dec((inst or {}).get("lotSz") or "0")
        min_sz = self._to_dec((inst or {}).get("minSz") or "0")

        # Convert base qty -> contracts for swaps if ctVal is provided.
        if mt != "spot":
            ct_val = self._to_dec((inst or {}).get("ctVal") or "0")
            if ct_val > 0:
                req = req / ct_val

        # Align to lot size step.
        if lot_sz > 0:
            req = self._floor_to_step(req, lot_sz)
        
        # Infer precision from lotSz
        size_precision = None
        if lot_sz > 0:
            try:
                lot_sz_normalized = lot_sz.normalize()
                lot_sz_str = str(lot_sz_normalized)
                if '.' in lot_sz_str:
                    decimal_part = lot_sz_str.split('.')[1]
                    size_precision = len(decimal_part)
                    if size_precision < 0:
                        size_precision = 0
                    if size_precision > 18:
                        size_precision = 18
                else:
                    size_precision = 0
            except Exception:
                pass

        # Enforce min size best-effort.
        if min_sz > 0 and req < min_sz:
            return (Decimal("0"), size_precision)
        return (req, size_precision)

    def _iso_ts(self) -> str:
        # OKX requires RFC3339 timestamp with milliseconds, e.g. 2020-12-08T09:08:57.715Z
        t = time.time()
        sec = int(t)
        ms = int((t - sec) * 1000)
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec)) + f".{ms:03d}Z"

    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        prehash = f"{ts}{method.upper()}{path}{body}"
        mac = hmac.new(self.secret_key.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(mac).decode("utf-8")

    def _headers(self, ts: str, sign: str) -> Dict[str, str]:
        h: Dict[str, str] = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.simulated_trading:
            h["x-simulated-trading"] = "1"
        return h

    def _signed_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Important: the signature must be computed over the exact request body string that is sent.
        Therefore we use `data=<serialized_json>` instead of `json=<dict>` to avoid re-serialization differences.

        For GET requests with params, the query string must be part of request_path in the prehash.
        """
        ts = self._iso_ts()
        body_str = self._json_dumps(json_body) if json_body is not None else ""

        qs = ""
        if params:
            # OKX expects the query string in the signed request path. Keep key order stable.
            # Convert all values to string to avoid "True"/"False" surprises.
            # Filter out empty strings and None values (OKX doesn't like empty params)
            norm = {str(k): str(v) for k, v in dict(params).items() if v is not None and str(v).strip() != ""}
            if norm:
                # Sort by key to ensure consistent ordering (OKX requirement)
                qs = urlencode(sorted(norm.items()), doseq=True)

        signed_path = f"{path}?{qs}" if qs else path
        sign = self._sign(ts, method, signed_path, body_str)
        
        # For GET requests with query params, we need to ensure the actual request URL matches the signed path
        # OKX requires exact match between signed path and actual request path
        if method.upper() == "GET" and qs:
            # Append query string directly to path to match signature exactly
            # Don't use params parameter to avoid double encoding
            request_path = f"{path}?{qs}"
            request_params = None
        else:
            request_path = path
            request_params = params
        
        code, data, text = self._request(
            method,
            request_path,
            params=request_params,
            data=body_str if body_str else None,
            headers=self._headers(ts, sign),
        )
        if code >= 400:
            # Provide more helpful error messages for common permission issues
            error_msg = text[:500] if text else f"HTTP {code}"
            if code == 401:
                error_code = ""
                if isinstance(data, dict):
                    error_code = str(data.get("code") or "")
                if error_code == "50120" or "permission" in error_msg.lower():
                    raise LiveTradingError(
                        f"OKX API permission error (HTTP {code}, code {error_code}): {error_msg}\n"
                        f"Solution: Please enable 'Trade' permission for your API key in OKX account.\n"
                        f"Path: OKX website -> API Management -> Edit API Key -> Enable 'Trade' permission"
                    )
            raise LiveTradingError(f"OKX HTTP {code}: {error_msg}")
        if isinstance(data, dict) and str(data.get("code") or "") not in ("0", ""):
            error_code = str(data.get("code") or "")
            error_msg = str(data.get("msg") or data)
            
            # Check for specific error codes in data array
            data_array = data.get("data", [])
            if isinstance(data_array, list) and data_array:
                first_item = data_array[0] if isinstance(data_array[0], dict) else {}
                s_code = str(first_item.get("sCode") or "")
                s_msg = str(first_item.get("sMsg") or "")
                
                # Error code 51008: Insufficient margin
                if s_code == "51008" or "insufficient" in s_msg.lower() or "margin" in s_msg.lower():
                    raise LiveTradingError(
                        f"OKX insufficient margin error (code {s_code}): {s_msg}\n"
                        f"Solution: Please ensure you have sufficient USDT margin in your account to place this order."
                    )
                # Error code 50120: Permission error
                if s_code == "50120" or error_code == "50120" or "permission" in str(error_msg).lower():
                    raise LiveTradingError(
                        f"OKX API permission error (code {s_code or error_code}): {s_msg or error_msg}\n"
                        f"Solution: Please enable 'Trade' permission for your API key in OKX account.\n"
                        f"Path: OKX website -> API Management -> Edit API Key -> Enable 'Trade' permission"
                    )
            
            # Fallback for permission errors
            if error_code == "50120" or "permission" in str(error_msg).lower():
                raise LiveTradingError(
                    f"OKX API permission error (code {error_code}): {error_msg}\n"
                    f"Solution: Please enable 'Trade' permission for your API key in OKX account.\n"
                    f"Path: OKX website -> API Management -> Edit API Key -> Enable 'Trade' permission"
                )
            raise LiveTradingError(f"OKX error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def ping(self) -> bool:
        code, data, _ = self._request("GET", "/api/v5/public/time")
        return code == 200 and isinstance(data, dict)

    def get_ticker(self, *, inst_id: str) -> Dict[str, Any]:
        """
        Get ticker price for an instrument.
        
        Endpoint: GET /api/v5/market/ticker?instId=...
        """
        if not inst_id:
            return {}
        raw = self._public_request("GET", "/api/v5/market/ticker", params={"instId": inst_id})
        data = (raw.get("data") or []) if isinstance(raw, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        return first if isinstance(first, dict) else {}

    def get_balance(self) -> Dict[str, Any]:
        """
        Private endpoint to validate credentials (best-effort).
        """
        return self._signed_request("GET", "/api/v5/account/balance")

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        inst_type = "SPOT" if market_type == "spot" else "SWAP"
        inst_id = symbol.upper()
        try:
            raw = self._signed_request("GET", "/api/v5/account/trade-fee", params={"instType": inst_type, "instId": inst_id})
            data = (raw.get("data") or []) if isinstance(raw, dict) else []
            if data and isinstance(data[0], dict):
                rec = data[0]
                maker = abs(float(rec.get("maker") or rec.get("makerU") or 0))
                taker = abs(float(rec.get("taker") or rec.get("takerU") or 0))
                if maker > 0 or taker > 0:
                    return {"maker": maker, "taker": taker}
        except Exception as e:
            logger.warning(f"OKX get_fee_rate({symbol}) failed: {e}")
        return None

    def get_positions(self, *, inst_id: str = "", inst_type: str = "SWAP") -> Dict[str, Any]:
        """
        Get positions (best-effort).
        
        Args:
            inst_id: Instrument ID (optional, for filtering)
            inst_type: Instrument type - "SPOT" or "SWAP" (default: "SWAP")

        Endpoint: GET /api/v5/account/positions
        """
        # Validate inst_type
        it = str(inst_type or "SWAP").strip().upper()
        if it not in ("SPOT", "SWAP", "FUTURES", "OPTION"):
            it = "SWAP"
        
        params: Dict[str, Any] = {"instType": it}
        # Only add instId if it's not empty
        if inst_id and str(inst_id).strip():
            params["instId"] = str(inst_id).strip()
        return self._signed_request("GET", "/api/v5/account/positions", params=params)

    def set_leverage(self, *, inst_id: str, lever: float, mgn_mode: str = "cross", pos_side: str = "") -> bool:
        """
        Set leverage for an instrument (best-effort).

        Endpoint: POST /api/v5/account/set-leverage
        Body:
          - instId
          - lever
          - mgnMode: cross / isolated
          - posSide: net / long / short (required depending on posMode)
        """
        iid = str(inst_id or "").strip()
        if not iid:
            return False
        try:
            lv = int(float(lever or 0))
        except Exception:
            lv = 0
        if lv <= 0:
            lv = 1

        mm = str(mgn_mode or "cross").strip().lower()
        if mm not in ("cross", "isolated"):
            mm = "cross"

        ps = str(pos_side or "").strip().lower()
        # In net_mode, OKX requires posSide=net. In long_short_mode, requires long/short.
        # Caller should pass already resolved posSide; but keep a safe fallback.
        if ps not in ("net", "long", "short"):
            try:
                cfg = self.get_account_config() or {}
                pm = str(cfg.get("posMode") or "").strip().lower()
                ps = "net" if pm in ("net_mode", "net") else ""
            except Exception:
                ps = ""

        cache_key = f"{iid}:{mm}:{ps}:{lv}"
        now = time.time()
        cached = self._lev_cache.get(cache_key)
        if cached:
            ts, ok = cached
            if ok and (now - float(ts or 0.0)) <= float(self._lev_cache_ttl_sec or 60.0):
                return True

        body: Dict[str, Any] = {"instId": iid, "lever": str(lv), "mgnMode": mm}
        if ps:
            body["posSide"] = ps
        _ = self._signed_request("POST", "/api/v5/account/set-leverage", json_body=body)
        self._lev_cache[cache_key] = (now, True)
        return True

    def get_account_config(self) -> Dict[str, Any]:
        """
        Get account configuration (best-effort).

        Endpoint: GET /api/v5/account/config
        Important field:
        - posMode: "net_mode" or "long_short_mode"
        """
        key = "account_config"
        now = time.time()
        cached = self._acct_cfg_cache.get(key)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._acct_cfg_cache_ttl_sec or 30.0):
                return obj

        raw = self._signed_request("GET", "/api/v5/account/config")
        data = (raw.get("data") or []) if isinstance(raw, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        if isinstance(first, dict) and first:
            self._acct_cfg_cache[key] = (now, first)
        return first if isinstance(first, dict) else {}

    def _resolve_pos_side(self, *, requested_pos_side: str, market_type: str) -> str:
        """
        OKX swap position mode compatibility:
        - long_short_mode: posSide must be "long" or "short"
        - net_mode: posSide must be "net" (and close orders should use reduceOnly=true)
        """
        mt = (market_type or "swap").strip().lower()
        if mt == "spot":
            return ""

        ps = (requested_pos_side or "").strip().lower()
        # Default to long/short requested.
        pos_mode = ""
        try:
            cfg = self.get_account_config() or {}
            pos_mode = str(cfg.get("posMode") or "").strip().lower()
        except Exception:
            pos_mode = ""

        if pos_mode in ("net_mode", "net"):
            return "net"
        if pos_mode in ("long_short_mode", "longshort_mode", "long_short", "longshort"):
            if ps not in ("long", "short"):
                raise LiveTradingError(f"Invalid posSide for long_short_mode: {requested_pos_side}")
            return ps

        # Unknown mode: be permissive but keep existing validation.
        if ps not in ("long", "short"):
            raise LiveTradingError(f"Invalid posSide: {requested_pos_side}")
        return ps

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        market_type: str = "swap",
        pos_side: str = "",
        td_mode: str = "cross",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        mt = (market_type or "swap").strip().lower()
        inst_id = to_okx_spot_inst_id(symbol) if mt == "spot" else to_okx_swap_inst_id(symbol)
        sd = (side or "").lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        sz_raw = float(size or 0.0)
        sz_dec, sz_precision = self._normalize_order_size(inst_id=inst_id, market_type=mt, size=sz_raw)
        if float(sz_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid size (below lot/min size): requested={sz_raw}")

        if mt == "spot":
            body: Dict[str, Any] = {
                "instId": inst_id,
                "tdMode": "cash",
                "side": sd,
                "ordType": "market",
                "sz": self._dec_str(sz_dec, strict_precision=sz_precision),
                # Follow hummingbot approach so "sz" is in base currency.
                "tgtCcy": "base_ccy",
            }
        else:
            ps = self._resolve_pos_side(requested_pos_side=pos_side, market_type=mt)
            td = (td_mode or "cross").lower()
            if td not in ("cross", "isolated"):
                td = "cross"
            body = {
                "instId": inst_id,
                "tdMode": td,
                "side": sd,
                "posSide": ps,
                "ordType": "market",
                "sz": self._dec_str(sz_dec),
            }
            if reduce_only:
                body["reduceOnly"] = "true"
        if client_order_id:
            body["clOrdId"] = str(client_order_id)
        if self.broker_code:
            body["tag"] = str(self.broker_code)

        raw = self._signed_request("POST", "/api/v5/trade/order", json_body=body)
        data = (raw.get("data") or []) if isinstance(raw, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        exchange_order_id = str(first.get("ordId") or first.get("clOrdId") or "")

        # OKX place-order does not guarantee fill fields. Keep them best-effort.
        filled = 0.0
        avg_price = 0.0
        return LiveOrderResult(
            exchange_id="okx",
            exchange_order_id=exchange_order_id,
            filled=filled,
            avg_price=avg_price,
            raw=raw,
        )

    def place_limit_order(
        self,
        *,
        market_type: str,
        symbol: str,
        side: str,
        size: float,
        price: float,
        pos_side: str = "",
        td_mode: str = "cross",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        mt = (market_type or "swap").strip().lower()
        sd = (side or "").lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        sz_raw = float(size or 0.0)
        px = float(price or 0.0)
        if sz_raw <= 0 or px <= 0:
            raise LiveTradingError("Invalid size/price")

        if mt == "spot":
            inst_id = to_okx_spot_inst_id(symbol)
            sz_dec, sz_precision = self._normalize_order_size(inst_id=inst_id, market_type=mt, size=sz_raw)
            if float(sz_dec or 0) <= 0:
                raise LiveTradingError(f"Invalid size (below lot/min size): requested={sz_raw}")
            body: Dict[str, Any] = {
                "instId": inst_id,
                "tdMode": "cash",
                "side": sd,
                "ordType": "limit",
                "sz": self._dec_str(sz_dec, strict_precision=sz_precision),
                "px": str(px),
            }
        else:
            inst_id = to_okx_swap_inst_id(symbol)
            ps = self._resolve_pos_side(requested_pos_side=pos_side, market_type=mt)
            sz_dec, sz_precision = self._normalize_order_size(inst_id=inst_id, market_type=mt, size=sz_raw)
            if float(sz_dec or 0) <= 0:
                raise LiveTradingError(f"Invalid size (below lot/min size): requested={sz_raw}")
            td = (td_mode or "cross").lower()
            if td not in ("cross", "isolated"):
                td = "cross"
            body = {
                "instId": inst_id,
                "tdMode": td,
                "side": sd,
                "posSide": ps,
                "ordType": "limit",
                "sz": self._dec_str(sz_dec, strict_precision=sz_precision),
                "px": str(px),
            }
            if reduce_only:
                body["reduceOnly"] = "true"

        if client_order_id:
            body["clOrdId"] = str(client_order_id)
        if self.broker_code:
            body["tag"] = str(self.broker_code)

        raw = self._signed_request("POST", "/api/v5/trade/order", json_body=body)
        data = (raw.get("data") or []) if isinstance(raw, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        exchange_order_id = str(first.get("ordId") or first.get("clOrdId") or "")
        return LiveOrderResult(exchange_id="okx", exchange_order_id=exchange_order_id, filled=0.0, avg_price=0.0, raw=raw)

    def cancel_order(self, *, market_type: str, symbol: str, ord_id: str = "", cl_ord_id: str = "") -> Dict[str, Any]:
        mt = (market_type or "swap").strip().lower()
        if mt == "spot":
            inst_id = to_okx_spot_inst_id(symbol)
        else:
            inst_id = to_okx_swap_inst_id(symbol)
        body: Dict[str, Any] = {"instId": inst_id}
        if ord_id:
            body["ordId"] = str(ord_id)
        elif cl_ord_id:
            body["clOrdId"] = str(cl_ord_id)
        else:
            raise LiveTradingError("OKX cancel_order requires ord_id or cl_ord_id")
        return self._signed_request("POST", "/api/v5/trade/cancel-order", json_body=body)

    def get_order(self, *, inst_id: str, ord_id: str = "", cl_ord_id: str = "") -> Dict[str, Any]:
        params: Dict[str, Any] = {"instId": str(inst_id)}
        if ord_id:
            params["ordId"] = str(ord_id)
        elif cl_ord_id:
            params["clOrdId"] = str(cl_ord_id)
        else:
            raise LiveTradingError("OKX get_order requires ord_id or cl_ord_id")
        resp = self._signed_request("GET", "/api/v5/trade/order", params=params)
        data = (resp.get("data") or []) if isinstance(resp, dict) else []
        first: Dict[str, Any] = data[0] if isinstance(data, list) and data else {}
        return first

    def get_order_fills(self, *, inst_id: str, ord_id: str, inst_type: str = "SWAP") -> Dict[str, Any]:
        params: Dict[str, Any] = {"instId": str(inst_id), "ordId": str(ord_id), "instType": str(inst_type)}
        return self._signed_request("GET", "/api/v5/trade/fills", params=params)

    def wait_for_fill(
        self,
        *,
        symbol: str,
        ord_id: str,
        cl_ord_id: str = "",
        market_type: str = "swap",
        max_wait_sec: float = 3.0,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Poll order detail / fills to obtain (best-effort) executed size and average price.

        Returns:
        {
          "filled": float,
          "avg_price": float,
          "fee": float,
          "fee_ccy": str,
          "state": str,
          "order": {...},
          "fills": {...}
        }
        """
        mt = (market_type or "swap").strip().lower()
        inst_id = to_okx_spot_inst_id(symbol) if mt == "spot" else to_okx_swap_inst_id(symbol)
        ct_val = Decimal("0")
        if mt != "spot":
            try:
                inst = self.get_instrument(inst_type="SWAP", inst_id=inst_id) or {}
                ct_val = self._to_dec(inst.get("ctVal") or "0")
            except Exception:
                ct_val = Decimal("0")
            if ct_val <= 0:
                # Fallback: keep quantities unchanged if ctVal is unavailable (best-effort).
                ct_val = Decimal("1")
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last_order: Dict[str, Any] = {}
        last_fills: Dict[str, Any] = {}

        while True:
            try:
                last_order = self.get_order(inst_id=inst_id, ord_id=str(ord_id or ""), cl_ord_id=str(cl_ord_id or ""))
            except Exception:
                last_order = last_order or {}

            state = str(last_order.get("state") or "")
            filled_str = str(last_order.get("accFillSz") or last_order.get("fillSz") or "0")
            avg_str = str(last_order.get("avgPx") or last_order.get("fillPx") or "0")
            try:
                filled_contracts = self._to_dec(filled_str or "0")
            except Exception:
                filled_contracts = Decimal("0")
            try:
                avg_price = float(avg_str or 0.0)
            except Exception:
                avg_price = 0.0

            filled_base_dec = filled_contracts
            if mt != "spot":
                filled_base_dec = filled_contracts * ct_val
            try:
                filled = float(filled_base_dec or 0)
            except Exception:
                filled = 0.0

            # Prefer fills endpoint for fee (and more reliable avg/filled aggregation).
            try:
                inst_type = "SPOT" if mt == "spot" else "SWAP"
                last_fills = self.get_order_fills(inst_id=inst_id, ord_id=str(ord_id), inst_type=inst_type)
                fills = (last_fills.get("data") or []) if isinstance(last_fills, dict) else []
                total_base = Decimal("0")
                total_quote = Decimal("0")
                total_fee = 0.0
                fee_ccy = ""
                got_any_fill = False
                if isinstance(fills, list):
                    for f in fills:
                        try:
                            sz_contracts = self._to_dec(f.get("fillSz") or "0")
                            px = self._to_dec(f.get("fillPx") or "0")
                            fee_v = f.get("fee")
                            if fee_v is None:
                                fee_v = f.get("fillFee")
                            try:
                                fee = float(fee_v or 0.0)
                            except Exception:
                                fee = 0.0
                            ccy = str(f.get("feeCcy") or f.get("fillFeeCcy") or "").strip()
                            sz_base = sz_contracts
                            if mt != "spot":
                                sz_base = sz_contracts * ct_val
                            if sz_base > 0 and px > 0:
                                total_base += sz_base
                                total_quote += sz_base * px
                                got_any_fill = True
                            if fee != 0.0:
                                # OKX fees are often negative for costs; store absolute cost.
                                total_fee += abs(float(fee))
                                if (not fee_ccy) and ccy:
                                    fee_ccy = ccy
                        except Exception:
                            continue
                # If fills are present, they are the best source of fee/avg aggregation.
                # However, OKX may lag in exposing fills right after an order is filled.
                # To avoid losing commission, do not fall back early when we haven't seen any fills yet.
                if got_any_fill and total_base > 0 and total_quote > 0:
                    return {
                        "filled": float(total_base),
                        "avg_price": float(total_quote / total_base),
                        "fee": float(total_fee),
                        "fee_ccy": str(fee_ccy or ""),
                        "state": state,
                        "order": last_order,
                        "fills": last_fills,
                        "filled_unit": "base",
                    }
            except Exception:
                pass

            # Fallback: order detail may include avg/filled but fee is not available.
            # IMPORTANT: If the order is already filled but fills endpoint hasn't returned data yet,
            # keep polling until timeout to give fills a chance to show up (so we can record fees).
            if filled > 0 and avg_price > 0 and time.time() >= end_ts:
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": 0.0,
                    "fee_ccy": "",
                    "state": state,
                    "order": last_order,
                    "fills": last_fills,
                    "filled_unit": "base",
                }

            # Terminal states: return whatever we have.
            if state in ("filled", "canceled", "cancelled"):
                if time.time() >= end_ts:
                    return {"filled": filled, "avg_price": avg_price, "fee": 0.0, "fee_ccy": "", "state": state, "order": last_order, "fills": last_fills}

            if time.time() >= end_ts:
                return {"filled": filled, "avg_price": avg_price, "fee": 0.0, "fee_ccy": "", "state": state, "order": last_order, "fills": last_fills}
            time.sleep(float(poll_interval_sec or 0.5))


