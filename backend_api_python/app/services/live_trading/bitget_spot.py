"""
Bitget Spot (direct REST) client.

Endpoints are aligned with hummingbot constants:
- POST /api/v2/spot/trade/place-order
- POST /api/v2/spot/trade/cancel-order
- GET  /api/v2/spot/trade/orderInfo
- GET  /api/v2/spot/trade/fills
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.symbols import to_bitget_um_symbol

logger = logging.getLogger(__name__)


class BitgetSpotClient(BaseRestClient):
    _CHANNEL_API_CODE_ORDER_PATHS = {
        "/api/v2/spot/trade/place-order",
        "/api/v2/spot/trade/batch-orders",
        "/api/v2/spot/trade/place-plan-order",
        "/api/v3/trade/place-order",
        "/api/v3/trade/place-batch",
        "/api/v3/trade/modify-order",
    }

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        passphrase: str,
        base_url: str = "https://api.bitget.com",
        timeout_sec: float = 15.0,
        channel_api_code: str = "qvz9x",
        simulated_trading: bool = False,
    ):
        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.passphrase = (passphrase or "").strip()
        self.channel_api_code = (channel_api_code or "").strip()
        self.simulated_trading = bool(simulated_trading)
        if not self.api_key or not self.secret_key or not self.passphrase:
            raise LiveTradingError("Missing Bitget api_key/secret_key/passphrase")

        # Best-effort cache for public symbol metadata used to normalize order sizes.
        # Key: symbol -> (fetched_at_ts, meta_dict)
        self._sym_meta_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._sym_meta_cache_ttl_sec = 300.0

    @staticmethod
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _parse_fee_detail(raw_fd: Any) -> Tuple[Decimal, str]:
        """Parse Bitget feeDetail (list, dict, or JSON string) into (abs_fee, ccy).

        Sums ALL entries when feeDetail is a list.
        """
        if raw_fd is None:
            return Decimal("0"), ""

        if isinstance(raw_fd, str):
            raw_fd = raw_fd.strip()
            if not raw_fd or raw_fd in ("0", "null"):
                return Decimal("0"), ""
            try:
                raw_fd = json.loads(raw_fd)
            except (json.JSONDecodeError, ValueError):
                return Decimal("0"), ""

        entries: List[Dict[str, Any]] = []
        if isinstance(raw_fd, list):
            entries = [e for e in raw_fd if isinstance(e, dict)]
        elif isinstance(raw_fd, dict):
            entries = [raw_fd]

        total_fee = Decimal("0")
        ccy = ""
        for entry in entries:
            fv = entry.get("totalFee") or entry.get("totalDeductionFee") or entry.get("fee")
            try:
                fee = Decimal(str(fv))
            except Exception:
                fee = Decimal("0")
            total_fee += abs(fee)
            if not ccy:
                ccy = str(entry.get("feeCoin") or entry.get("feeCcy") or "").strip()
        return total_fee, ccy

    @staticmethod
    def _dec_str(d: Decimal, max_decimals: int = 18, strict_precision: Optional[int] = None) -> str:
        """
        Convert Decimal to string with controlled precision.
        Bitget requires quantities to match quantityStep/quantityScale precision.
        
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

    def _sign(self, ts_ms: str, method: str, path: str, body: str) -> str:
        prehash = f"{ts_ms}{method.upper()}{path}{body}"
        mac = hmac.new(self.secret_key.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(mac).decode("utf-8")

    def _headers(self, ts_ms: str, sign: str, request_path: str = "") -> Dict[str, str]:
        h = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts_ms,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.simulated_trading:
            h["PAPTRADING"] = "1"
        clean_path = str(request_path or "").split("?", 1)[0]
        if self.channel_api_code and clean_path in self._CHANNEL_API_CODE_ORDER_PATHS:
            h["X-CHANNEL-API-CODE"] = self.channel_api_code
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
        Bitget signature must match the exact body string sent over the wire.
        """
        ts_ms = str(int(time.time() * 1000))
        body_str = self._json_dumps(json_body) if json_body is not None else ""

        qs = ""
        if params:
            norm = {str(k): "" if v is None else str(v) for k, v in dict(params).items()}
            qs = urlencode(sorted(norm.items()), doseq=True)
        signed_path = f"{path}?{qs}" if qs else path

        sign = self._sign(ts_ms, method, signed_path, body_str)
        code, data, text = self._request(
            method,
            path,
            params=params,
            data=body_str if body_str else None,
            headers=self._headers(ts_ms, sign, path),
        )
        if code >= 400:
            raise LiveTradingError(f"BitgetSpot HTTP {code}: {text[:500]}")
        if isinstance(data, dict):
            c = str(data.get("code") or "")
            if c and c not in ("00000", "0"):
                raise LiveTradingError(f"BitgetSpot error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def _public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        code, data, text = self._request(method, path, params=params, headers=None, json_body=None, data=None)
        if code >= 400:
            raise LiveTradingError(f"BitgetSpot HTTP {code}: {text[:500]}")
        if isinstance(data, dict):
            c = str(data.get("code") or "")
            if c and c not in ("00000", "0"):
                raise LiveTradingError(f"BitgetSpot error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def get_symbol_meta(self, *, symbol: str) -> Dict[str, Any]:
        """
        Fetch spot symbol metadata (best-effort).

        Endpoint (Bitget v2 spot): GET /api/v2/spot/public/symbols
        """
        sym = to_bitget_um_symbol(symbol)
        if not sym:
            return {}
        now = time.time()
        cached = self._sym_meta_cache.get(sym)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._sym_meta_cache_ttl_sec or 300.0):
                return obj

        raw = self._public_request("GET", "/api/v2/spot/public/symbols")
        data = raw.get("data") if isinstance(raw, dict) else None
        items = data if isinstance(data, list) else []
        found: Dict[str, Any] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            s = str(it.get("symbol") or it.get("symbolName") or "")
            if s and s.upper() == sym.upper():
                found = it
                break
        if found:
            self._sym_meta_cache[sym] = (now, found)
        return found

    @staticmethod
    def _precision_to_step(precision: Any) -> Tuple[Decimal, Optional[int]]:
        """Map Bitget ``quantityPrecision`` / ``quotePrecision`` (decimal places) to step size."""
        try:
            places = int(precision)
        except Exception:
            return Decimal("0"), None
        if places < 0 or places > 18:
            return Decimal("0"), None
        if places == 0:
            return Decimal("1"), 0
        step = Decimal("1") / (Decimal("10") ** Decimal(str(places)))
        return step, places

    def _normalize_base_size(self, *, symbol: str, base_size: float) -> Tuple[Decimal, Optional[int]]:
        """
        Normalize spot base size to lot/step constraints (best-effort).
        
        Returns:
            Tuple of (normalized_size, precision) where precision is the number of decimal places required.
        """
        req = self._to_dec(base_size)
        if req <= 0:
            return (Decimal("0"), None)

        meta: Dict[str, Any] = {}
        try:
            meta = self.get_symbol_meta(symbol=symbol) or {}
        except Exception:
            meta = {}

        # Bitget v2: ``quantityPrecision`` is decimal places (e.g. 6 for BTC), not a step multiplier.
        step = Decimal("0")
        size_precision = None
        for key in ("quantityStep", "sizeStep", "minTradeIncrement"):
            raw = meta.get(key)
            if raw is not None and str(raw).strip() not in ("", "0"):
                try:
                    st = self._to_dec(raw)
                except Exception:
                    st = Decimal("0")
                if st > 0 and st < 1:
                    step = st
                    break
        if step <= 0:
            step, size_precision = self._precision_to_step(
                meta.get("quantityPrecision") or meta.get("quantityPlace") or meta.get("sizePlace")
            )

        if step > 0:
            req = self._floor_to_step(req, step)

        mn = self._to_dec(
            meta.get("minTradeAmount")
            or meta.get("minTradeNum")
            or meta.get("minQty")
            or meta.get("minSize")
            or "0"
        )
        if mn > 0 and req < mn:
            return (Decimal("0"), size_precision)
        return (req, size_precision)

    def _normalize_quote_size(self, *, symbol: str, quote_size: float) -> Tuple[Decimal, Optional[int]]:
        """Normalize USDT (quote) size for market BUY — Bitget ``size`` is quote coin amount."""
        req = self._to_dec(quote_size)
        if req <= 0:
            return (Decimal("0"), None)

        meta: Dict[str, Any] = {}
        try:
            meta = self.get_symbol_meta(symbol=symbol) or {}
        except Exception:
            meta = {}

        step, quote_precision = self._precision_to_step(
            meta.get("quotePrecision") or meta.get("pricePrecision")
        )
        if step > 0:
            req = self._floor_to_step(req, step)

        try:
            min_usdt = self._to_dec(meta.get("minTradeUSDT") or meta.get("minTradeAmount") or "0")
        except Exception:
            min_usdt = Decimal("0")
        if min_usdt > 0 and req < min_usdt:
            return (Decimal("0"), quote_precision)
        return (req, quote_precision)

    def _normalize_limit_price(self, *, symbol: str, price: float) -> Tuple[Decimal, Optional[int]]:
        req = self._to_dec(price)
        if req <= 0:
            return (Decimal("0"), None)

        meta: Dict[str, Any] = {}
        try:
            meta = self.get_symbol_meta(symbol=symbol) or {}
        except Exception:
            meta = {}

        step = Decimal("0")
        price_precision = None
        for key in ("priceStep", "tickSize", "priceTick", "minPriceIncrement"):
            raw = meta.get(key)
            if raw is not None and str(raw).strip() not in ("", "0"):
                try:
                    st = self._to_dec(raw)
                except Exception:
                    st = Decimal("0")
                if st > 0:
                    step = st
                    break
        if step <= 0:
            step, price_precision = self._precision_to_step(
                meta.get("pricePrecision") or meta.get("pricePlace") or meta.get("priceScale")
            )

        if step > 0:
            req = self._floor_to_step(req, step)
        return (req, price_precision)

    def place_limit_order(self, *, symbol: str, side: str, size: float, price: float, client_order_id: Optional[str] = None) -> LiveOrderResult:
        sym = to_bitget_um_symbol(symbol)
        sd = (side or "").lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        req = float(size or 0.0)
        px = float(price or 0.0)
        if req <= 0 or px <= 0:
            raise LiveTradingError("Invalid size/price")
        sz_dec, sz_precision = self._normalize_base_size(symbol=symbol, base_size=req)
        if float(sz_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid size (below step/min): requested={req}")
        px_dec, px_precision = self._normalize_limit_price(symbol=symbol, price=px)
        if float(px_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid price (below tick/min): requested={px}")

        body: Dict[str, Any] = {
            "side": sd,
            "symbol": sym,
            "size": self._dec_str(sz_dec, strict_precision=sz_precision),
            "orderType": "limit",
            "force": "gtc",
            "price": self._dec_str(px_dec, strict_precision=px_precision),
        }
        if client_order_id:
            body["clientOid"] = str(client_order_id)
        raw = self._signed_request("POST", "/api/v2/spot/trade/place-order", json_body=body)
        data = raw.get("data") if isinstance(raw, dict) else None
        order_id = str(data.get("orderId") or "") if isinstance(data, dict) else ""
        return LiveOrderResult(exchange_id="bitget", exchange_order_id=order_id, filled=0.0, avg_price=0.0, raw=raw)

    def place_market_order(self, *, symbol: str, side: str, size: float, client_order_id: Optional[str] = None) -> LiveOrderResult:
        """
        NOTE: Bitget spot market BUY may expect quote amount. We accept `size` as base size,
        but the caller can also pass a quote-sized value if desired.
        """
        sym = to_bitget_um_symbol(symbol)
        sd = (side or "").lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        req = float(size or 0.0)
        if req <= 0:
            raise LiveTradingError("Invalid size")

        if sd == "sell":
            sz_dec, sz_precision = self._normalize_base_size(symbol=symbol, base_size=req)
            if float(sz_dec or 0) <= 0:
                raise LiveTradingError(f"Invalid size (below step/min): requested={req}")
            sz_str = self._dec_str(sz_dec, strict_precision=sz_precision)
        else:
            sz_dec, sz_precision = self._normalize_quote_size(symbol=symbol, quote_size=req)
            if float(sz_dec or 0) <= 0:
                raise LiveTradingError(
                    f"Invalid quote size (below minTradeUSDT/precision): requested={req}"
                )
            sz_str = self._dec_str(sz_dec, strict_precision=sz_precision)

        body: Dict[str, Any] = {
            "side": sd,
            "symbol": sym,
            "size": sz_str,
            "orderType": "market",
            "force": "gtc",
        }
        if client_order_id:
            body["clientOid"] = str(client_order_id)
        raw = self._signed_request("POST", "/api/v2/spot/trade/place-order", json_body=body)
        data = raw.get("data") if isinstance(raw, dict) else None
        order_id = str(data.get("orderId") or "") if isinstance(data, dict) else ""
        return LiveOrderResult(exchange_id="bitget", exchange_order_id=order_id, filled=0.0, avg_price=0.0, raw=raw)

    def cancel_order(self, *, symbol: str, client_order_id: str) -> Dict[str, Any]:
        sym = to_bitget_um_symbol(symbol)
        if not client_order_id:
            raise LiveTradingError("BitgetSpot cancel_order requires client_order_id")
        body = {"symbol": sym, "clientOid": str(client_order_id)}
        return self._signed_request("POST", "/api/v2/spot/trade/cancel-order", json_body=body)

    def get_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        sym = to_bitget_um_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if order_id:
            params["orderId"] = str(order_id)
        elif client_order_id:
            params["clientOid"] = str(client_order_id)
        else:
            raise LiveTradingError("BitgetSpot get_order requires order_id or client_order_id")
        return self._signed_request("GET", "/api/v2/spot/trade/orderInfo", params=params)

    def get_fills(self, *, symbol: str, order_id: str) -> Dict[str, Any]:
        sym = to_bitget_um_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym, "orderId": str(order_id)}
        return self._signed_request("GET", "/api/v2/spot/trade/fills", params=params)

    def wait_for_fill(
        self,
        *,
        symbol: str,
        order_id: str,
        client_order_id: str = "",
        max_wait_sec: float = 12.0,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last_order: Dict[str, Any] = {}
        last_fills: Dict[str, Any] = {}
        state = ""

        def _spot_order_row(raw: Dict[str, Any]) -> Dict[str, Any]:
            od = raw.get("data") if isinstance(raw, dict) else None
            if isinstance(od, dict):
                return od
            if isinstance(od, list) and od and isinstance(od[0], dict):
                return od[0]
            return {}

        while True:
            timed_out = time.time() >= end_ts
            # Prefer fills to compute weighted average + fee (may lag vs orderInfo).
            try:
                last_fills = self.get_fills(symbol=symbol, order_id=str(order_id))
                data = last_fills.get("data") if isinstance(last_fills, dict) else None
                fills = data if isinstance(data, list) else []
                total_base = 0.0
                total_quote = 0.0
                total_fee = 0.0
                fee_ccy = ""
                if isinstance(fills, list):
                    for f in fills:
                        try:
                            sz = float(f.get("size") or 0.0)
                            px = float(f.get("priceAvg") or f.get("price") or 0.0)
                            if sz > 0 and px > 0:
                                total_base += sz
                                total_quote += sz * px
                            fee_v = f.get("fee")
                            if fee_v is None:
                                fee_v = f.get("fillFee")
                            if fee_v is None:
                                fee_v = f.get("tradeFee")
                            ccy = str(f.get("feeCoin") or f.get("feeCcy") or f.get("fillFeeCoin") or f.get("fillFeeCcy") or "").strip()
                            # Bitget V2: fee is inside feeDetail (list/dict/JSON string)
                            if fee_v is None or str(fee_v).strip() in ("", "0", "0.0"):
                                fd_fee, fd_ccy = self._parse_fee_detail(f.get("feeDetail"))
                                if fd_fee > 0:
                                    fee = float(fd_fee)
                                    if not ccy and fd_ccy:
                                        ccy = fd_ccy
                                else:
                                    try:
                                        fee = float(fee_v or 0.0)
                                    except Exception:
                                        fee = 0.0
                            else:
                                try:
                                    fee = float(fee_v or 0.0)
                                except Exception:
                                    fee = 0.0
                            if fee != 0.0:
                                total_fee += abs(float(fee))
                                if (not fee_ccy) and ccy:
                                    fee_ccy = ccy
                        except Exception:
                            continue
                if total_base > 0 and total_quote > 0:
                    if total_fee <= 0 and not timed_out:
                        time.sleep(float(poll_interval_sec or 0.5))
                        continue
                    logger.debug(
                        "Bitget Spot fill result: filled=%.8f avg=%.8f fee=%.8f %s (order=%s)",
                        total_base, total_quote / total_base, total_fee, fee_ccy, order_id,
                    )
                    return {
                        "filled": total_base,
                        "avg_price": total_quote / total_base,
                        "fee": float(total_fee),
                        "fee_ccy": str(fee_ccy or ""),
                        "state": state,
                        "order": last_order,
                        "fills": last_fills,
                    }
            except Exception:
                pass

            try:
                last_order = self.get_order(symbol=symbol, order_id=str(order_id or ""), client_order_id=str(client_order_id or ""))
                row = _spot_order_row(last_order)
                if row:
                    state = str(row.get("status") or row.get("state") or "")
            except Exception:
                pass

            if timed_out:
                row = _spot_order_row(last_order)
                filled = 0.0
                avg_price = 0.0
                fee = 0.0
                fee_ccy = ""
                try:
                    filled = float(row.get("baseVolume") or row.get("filledQty") or row.get("dealSize") or row.get("size") or 0.0)
                except Exception:
                    filled = 0.0
                try:
                    quote_amt = float(row.get("quoteVolume") or row.get("filledTotalAmount") or row.get("dealFunds") or 0.0)
                    if filled > 0 and quote_amt > 0:
                        avg_price = quote_amt / filled
                    else:
                        avg_price = float(row.get("priceAvg") or row.get("price") or 0.0)
                except Exception:
                    avg_price = 0.0
                try:
                    fee = abs(float(row.get("fee") or row.get("fillFee") or 0.0))
                    fee_ccy = str(row.get("feeCoin") or row.get("feeCcy") or "").strip()
                except Exception:
                    pass
                if fee <= 0 and isinstance(row, dict):
                    fd_fee, fd_ccy = self._parse_fee_detail(row.get("feeDetail"))
                    if fd_fee > 0:
                        fee = float(fd_fee)
                        if not fee_ccy and fd_ccy:
                            fee_ccy = fd_ccy
                        logger.debug("Bitget Spot timeout fallback fee via feeDetail: %.8f %s", fee, fee_ccy)
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "state": state,
                    "order": last_order,
                    "fills": last_fills,
                }
            time.sleep(float(poll_interval_sec or 0.5))

    def get_assets(self) -> Dict[str, Any]:
        """
        Spot assets/balances.

        Endpoint: GET /api/v2/spot/account/assets
        """
        return self._signed_request("GET", "/api/v2/spot/account/assets")

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        sym = to_bitget_um_symbol(symbol)
        raw = self._public_request("GET", "/api/v2/spot/market/tickers", params={"symbol": sym})
        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        return {}


