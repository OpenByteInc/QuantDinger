"""
Binance USDT-M Futures (direct REST) client.

API docs (reference):
- Signed endpoints use HMAC SHA256 over query string.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError

logger = logging.getLogger(__name__)
from app.services.live_trading.symbols import to_binance_futures_symbol


class BinanceFuturesClient(BaseRestClient):
    def __init__(self, *, api_key: str, secret_key: str, base_url: str = None, enable_demo_trading: bool = False, timeout_sec: float = 15.0, broker_id: str = ""):
        if not base_url:
            # Binance USDT-M Futures Testnet (official): https://testnet.binancefuture.com
            base_url = "https://testnet.binancefuture.com" if enable_demo_trading else "https://fapi.binance.com"

        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.broker_id = (broker_id or "").strip()
        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing Binance api_key/secret_key")

        # Best-effort cache for public symbol filters used to normalize quantities.
        # Key: symbol -> (fetched_at_ts, filters_dict)
        self._sym_filter_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._sym_filter_cache_ttl_sec = 300.0

        # Best-effort cache for account position mode (Hedge vs One-way).
        # Binance endpoint: GET /fapi/v1/positionSide/dual -> {"dualSidePosition": true/false}
        self._dual_side_cache: Optional[Tuple[float, bool]] = None
        self._dual_side_cache_ttl_sec = 60.0

        # serverTime - local_ms; avoids Binance -1021 when the host clock is ahead of Binance.
        self._time_offset_ms: int = 0
        self._time_sync_monotonic: float = 0.0

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
        Binance requires quantities/prices to match LOT_SIZE/PRICE_FILTER precision.
        This method ensures the output string doesn't exceed the required precision.
        
        Args:
            d: Decimal value to format
            max_decimals: Maximum decimal places (fallback if strict_precision not provided)
            strict_precision: If provided, strictly limit to this many decimal places (no trailing zero removal)
        """
        try:
            if d == 0:
                return "0"
            # Normalize to remove unnecessary trailing zeros from internal representation
            normalized = d.normalize()
            
            # If strict_precision is provided, use it and strictly limit decimal places
            # This ensures we match the stepSize requirement exactly
            if strict_precision is not None:
                try:
                    prec = int(strict_precision)
                    if prec < 0:
                        prec = 0
                    if prec > 18:
                        prec = 18
                    # Use quantize to ensure exact precision (round down to match stepSize)
                    q = Decimal("1").scaleb(-prec)
                    quantized = normalized.quantize(q, rounding=ROUND_DOWN)
                    # Format with exact precision - this will produce at most 'prec' decimal places
                    s = format(quantized, f".{prec}f")
                    # Remove trailing zeros and decimal point if not needed
                    if '.' in s:
                        s = s.rstrip('0').rstrip('.')
                    return s if s else "0"
                except Exception:
                    pass
            
            # Fallback to original logic if strict_precision not provided or failed
            # Convert to string using fixed-point notation
            s = format(normalized, f".{max_decimals}f")
            # Remove trailing zeros and decimal point if not needed
            if '.' in s:
                s = s.rstrip('0').rstrip('.')
            return s if s else "0"
        except Exception:
            # Fallback: try to convert safely
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
                # Format with max_decimals and remove trailing zeros
                s = format(f, f".{max_decimals}f")
                if '.' in s:
                    s = s.rstrip('0').rstrip('.')
                return s if s else "0"
            except Exception:
                # Last resort: convert to string
                s = str(d)
                # Try to remove scientific notation if present
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

    def _sign(self, query_string: str) -> str:
        sig = hmac.new(self.secret_key.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        return sig

    def _signed_headers(self) -> Dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key}

    def _ensure_server_time(self, *, force: bool = False) -> None:
        """
        Align signed request timestamps with Binance server time (GET /fapi/v1/time).
        """
        now_m = time.monotonic()
        if not force and (now_m - float(self._time_sync_monotonic or 0.0)) < 300.0:
            return
        try:
            code, data, _ = self._request("GET", "/fapi/v1/time")
            if code != 200 or not isinstance(data, dict):
                return
            server_ms = int(data.get("serverTime") or 0)
            if server_ms <= 0:
                return
            local_ms = int(time.time() * 1000)
            self._time_offset_ms = server_ms - local_ms
            self._time_sync_monotonic = now_m
        except Exception:
            pass

    def _format_client_order_id(self, client_order_id: Optional[str]) -> str:
        raw = str(client_order_id or "").strip()
        broker_id = str(self.broker_id or "").strip()
        if not raw:
            return ""
        if not broker_id:
            return raw[:36]
        prefix = f"x-{broker_id}"
        if raw.startswith(prefix):
            return raw[:36]
        suffix_budget = max(0, 36 - len(prefix))
        if suffix_budget <= 0:
            return prefix[:36]
        return f"{prefix}{raw[:suffix_budget]}"

    def _signed_request(self, method: str, path: str, *, params: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_server_time()
        last_err: Optional[LiveTradingError] = None
        for attempt in range(2):
            p = dict(params or {})
            p["timestamp"] = int(time.time() * 1000) + int(self._time_offset_ms)
            if "recvWindow" not in p:
                p["recvWindow"] = 10000
            qs = urlencode(p, doseq=True)
            p["signature"] = self._sign(qs)
            code, data, text = self._request(method, path, params=p, headers=self._signed_headers())
            if code >= 400:
                err = LiveTradingError(f"Binance HTTP {code}: {text[:500]}")
                if attempt == 0 and ("-1021" in text or "1021" in text):
                    self._ensure_server_time(force=True)
                    last_err = err
                    continue
                raise err
            if isinstance(data, dict) and data.get("code") and int(data.get("code")) < 0:
                err = LiveTradingError(f"Binance error: {data}")
                if attempt == 0 and int(data.get("code") or 0) == -1021:
                    self._ensure_server_time(force=True)
                    last_err = err
                    continue
                raise err
            return data if isinstance(data, dict) else {"raw": data}
        if last_err:
            raise last_err
        raise LiveTradingError("Binance signed request failed")

    def _public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        code, data, text = self._request(method, path, params=params, headers=None, json_body=None, data=None)
        if code >= 400:
            raise LiveTradingError(f"Binance HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and data.get("code") and int(data.get("code")) < 0:
            raise LiveTradingError(f"Binance error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def get_mark_price(self, *, symbol: str) -> float:
        """
        Best-effort mark price for MIN_NOTIONAL validation.

        Endpoint: GET /fapi/v1/premiumIndex?symbol=...
        """
        sym = to_binance_futures_symbol(symbol)
        if not sym:
            return 0.0
        try:
            data = self._public_request("GET", "/fapi/v1/premiumIndex", params={"symbol": sym})
        except Exception:
            return 0.0
        try:
            return float(data.get("markPrice") or 0.0)
        except Exception:
            return 0.0

    def get_symbol_filters(self, *, symbol: str) -> Dict[str, Any]:
        """
        Get futures symbol filters from exchangeInfo (best-effort).

        Endpoint: GET /fapi/v1/exchangeInfo?symbol=...
        """
        sym = to_binance_futures_symbol(symbol)
        if not sym:
            return {}
        now = time.time()
        cached = self._sym_filter_cache.get(sym)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._sym_filter_cache_ttl_sec or 300.0):
                return obj

        raw = self._public_request("GET", "/fapi/v1/exchangeInfo", params={"symbol": sym})
        symbols = raw.get("symbols") if isinstance(raw, dict) else None
        # Important: Binance may still return the full symbols list even when `symbol=...` is provided.
        # Never assume `symbols[0]` matches the requested symbol.
        first: Dict[str, Any] = {}
        if isinstance(symbols, list) and symbols:
            picked = None
            try:
                picked = next((s for s in symbols if isinstance(s, dict) and str(s.get("symbol") or "") == sym), None)
            except Exception:
                picked = None
            first = picked if isinstance(picked, dict) else (symbols[0] if isinstance(symbols[0], dict) else {})
        filters = first.get("filters") if isinstance(first, dict) else None
        fdict: Dict[str, Any] = {}
        if isinstance(filters, list):
            for f in filters:
                if isinstance(f, dict) and f.get("filterType"):
                    fdict[str(f.get("filterType"))] = f
        # Also keep precision metadata when available (used to avoid -1111).
        try:
            qty_prec = first.get("quantityPrecision") if isinstance(first, dict) else None
            price_prec = first.get("pricePrecision") if isinstance(first, dict) else None
            meta = {
                "symbol": str(first.get("symbol") or "") if isinstance(first, dict) else "",
                "contractType": str(first.get("contractType") or "") if isinstance(first, dict) else "",
                "quantityPrecision": int(qty_prec) if qty_prec is not None else None,
                "pricePrecision": int(price_prec) if price_prec is not None else None,
            }
            fdict["_meta"] = meta
        except Exception:
            pass
        if fdict:
            self._sym_filter_cache[sym] = (now, fdict)
        return fdict

    @staticmethod
    def _floor_to_precision(value: Decimal, precision: Optional[int]) -> Decimal:
        try:
            if precision is None:
                return value
            p = int(precision)
        except Exception:
            return value
        if p < 0 or p > 18:
            return value
        try:
            q = Decimal("1").scaleb(-p)  # 1e-precision
            return value.quantize(q, rounding=ROUND_DOWN)
        except Exception:
            return value

    def _normalize_price(self, *, symbol: str, price: float) -> Decimal:
        """
        Normalize futures limit price using PRICE_FILTER tickSize (best-effort).

        Binance rejects prices/quantities whose precision exceeds allowed decimals (-1111),
        so we must quantize to tickSize and send as string.
        """
        px = self._to_dec(price)
        if px <= 0:
            return Decimal("0")
        fdict: Dict[str, Any] = {}
        try:
            fdict = self.get_symbol_filters(symbol=symbol) or {}
        except Exception:
            fdict = {}

        filt = fdict.get("PRICE_FILTER") or {}
        tick = self._to_dec((filt or {}).get("tickSize") or "0")
        min_px = self._to_dec((filt or {}).get("minPrice") or "0")

        if tick > 0:
            px = self._floor_to_step(px, tick)
        # Enforce price precision cap (some symbols reject more decimals even if tick looks permissive).
        try:
            meta = fdict.get("_meta") or {}
            px = self._floor_to_precision(px, (meta.get("pricePrecision") if isinstance(meta, dict) else None))
        except Exception:
            pass
        if min_px > 0 and px < min_px:
            return Decimal("0")
        return px

    def _normalize_quantity(self, *, symbol: str, quantity: float, for_market: bool) -> Tuple[Decimal, Optional[int]]:
        """
        Normalize futures order quantity using LOT_SIZE / MARKET_LOT_SIZE filters (best-effort).
        
        Returns:
            Tuple of (normalized_quantity, precision) where precision is the number of decimal places required.
        """
        q = self._to_dec(quantity)
        if q <= 0:
            return (Decimal("0"), None)
        fdict: Dict[str, Any] = {}
        try:
            fdict = self.get_symbol_filters(symbol=symbol) or {}
        except Exception:
            fdict = {}

        from app.services.live_trading.binance_spot import BinanceSpotClient as _BSC

        filt = _BSC._pick_lot_filter(fdict, for_market=for_market)

        step = self._to_dec((filt or {}).get("stepSize") or "0")
        min_qty = self._to_dec((filt or {}).get("minQty") or "0")

        if step > 0:
            q = self._floor_to_step(q, step)

        step_precision = _BSC._decimal_places_from_step(step)
        qty_precision = step_precision
        try:
            meta = fdict.get("_meta") or {}
            if isinstance(meta, dict) and meta.get("quantityPrecision") is not None:
                meta_prec = int(meta.get("quantityPrecision"))
                if step_precision is None:
                    qty_precision = meta_prec
                else:
                    qty_precision = min(meta_prec, step_precision)
        except Exception:
            pass

        if qty_precision is not None:
            q = self._floor_to_precision(q, qty_precision)
        
        if min_qty > 0 and q < min_qty:
            return (Decimal("0"), qty_precision)
        return (q, qty_precision)

    def ping(self) -> bool:
        code, data, _ = self._request("GET", "/fapi/v1/time")
        return code == 200 and isinstance(data, dict)

    def get_account(self) -> Dict[str, Any]:
        """
        Private endpoint to validate credentials.
        """
        return self._signed_request("GET", "/fapi/v2/account", params={})

    def get_user_trades(self, *, symbol: str, order_id: str = "", limit: int = 100) -> Any:
        """
        Fetch user trades (fills).

        Endpoint: GET /fapi/v1/userTrades

        Note: Binance order endpoints do NOT include commissions; commissions live on fills.
        """
        sym = to_binance_futures_symbol(symbol)
        if not sym:
            return []
        params: Dict[str, Any] = {"symbol": sym}
        if order_id:
            # Binance expects numeric orderId; keep as string and let server validate.
            params["orderId"] = str(order_id)
        try:
            lim = int(limit or 100)
        except Exception:
            lim = 100
        lim = max(1, min(1000, lim))
        params["limit"] = lim
        data = self._signed_request("GET", "/fapi/v1/userTrades", params=params)
        return data

    def get_fee_for_order(self, *, symbol: str, order_id: str, max_retries: int = 3) -> Tuple[float, str]:
        """
        Best-effort: sum commissions from fills for a specific order.
        Retries a few times because userTrades may lag behind order fill.

        Returns: (total_fee, fee_ccy)
        """
        for attempt in range(max(1, max_retries)):
            try:
                trades = self.get_user_trades(symbol=symbol, order_id=str(order_id or ""), limit=200)
            except Exception:
                trades = []
            if not isinstance(trades, list):
                trades = []
            total_fee = 0.0
            fee_ccy = ""
            for t in trades:
                if not isinstance(t, dict):
                    continue
                try:
                    fee = float(t.get("commission") or 0.0)
                except Exception:
                    fee = 0.0
                ccy = str(t.get("commissionAsset") or "").strip()
                if fee != 0.0:
                    total_fee += abs(float(fee))
                    if (not fee_ccy) and ccy:
                        fee_ccy = ccy
            if total_fee > 0 or attempt >= max_retries - 1:
                return float(total_fee), str(fee_ccy or "")
            time.sleep(1.0)
        return 0.0, ""

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        sym = symbol.upper().replace("-", "").replace("/", "")
        try:
            data = self._signed_request("GET", "/fapi/v1/commissionRate", params={"symbol": sym})
            if isinstance(data, dict):
                maker = abs(float(data.get("makerCommissionRate") or 0))
                taker = abs(float(data.get("takerCommissionRate") or 0))
                if maker > 0 or taker > 0:
                    return {"maker": maker, "taker": taker}
        except Exception as e:
            logger.warning(f"Binance get_fee_rate({symbol}) failed: {e}")
        return None

    def set_leverage(self, *, symbol: str, leverage: float) -> Dict[str, Any]:
        """
        Set futures leverage for a symbol (USDT-M).

        Endpoint: POST /fapi/v1/leverage

        Notes:
        - Binance applies leverage per symbol.
        - If leverage is not set, the exchange may keep a default (often 1x),
          which will change the actual margin used for a given notional.
        """
        sym = to_binance_futures_symbol(symbol)
        if not sym:
            raise LiveTradingError(f"Invalid symbol: {symbol}")
        try:
            lev = int(float(leverage or 1.0))
        except Exception:
            lev = 1
        if lev < 1:
            lev = 1
        if lev > 125:
            lev = 125
        return self._signed_request("POST", "/fapi/v1/leverage", params={"symbol": sym, "leverage": lev})

    def get_dual_side_position(self) -> Optional[bool]:
        """
        Best-effort read of position mode:
        - True  => Hedge Mode (dual-side position enabled): orders must specify positionSide=LONG/SHORT
        - False => One-way Mode: orders should NOT specify LONG/SHORT

        Endpoint: GET /fapi/v1/positionSide/dual
        """
        now = time.time()
        cached = self._dual_side_cache
        if cached:
            ts, val = cached
            if (now - float(ts or 0.0)) <= float(self._dual_side_cache_ttl_sec or 60.0):
                return bool(val)
        try:
            data = self._signed_request("GET", "/fapi/v1/positionSide/dual", params={})
            v = data.get("dualSidePosition") if isinstance(data, dict) else None
            if v is None:
                return None
            val = bool(v)
            self._dual_side_cache = (now, val)
            return val
        except Exception:
            return None

    @staticmethod
    def _is_err_code(err: Exception, code: int) -> bool:
        try:
            s = str(err or "")
        except Exception:
            s = ""
        return f'\"code\":{int(code)}' in s or f"'code': {int(code)}" in s or f"'code':{int(code)}" in s

    @staticmethod
    def _normalize_position_side(pos_side: Optional[str]) -> str:
        p = (pos_side or "").strip().lower()
        if p in ("long", "l"):
            return "LONG"
        if p in ("short", "s"):
            return "SHORT"
        if p in ("both", "net"):
            return "BOTH"
        return ""

    @staticmethod
    def _infer_position_side(*, side: str, reduce_only: bool) -> str:
        sd = (side or "").strip().upper()
        ro = bool(reduce_only)
        # Open:
        # - BUY  => LONG
        # - SELL => SHORT
        # Reduce/Close:
        # - SELL reduceOnly => close LONG
        # - BUY  reduceOnly => close SHORT
        if ro:
            return "LONG" if sd == "SELL" else "SHORT"
        return "LONG" if sd == "BUY" else "SHORT"

    def get_order(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> Dict[str, Any]:
        """
        Query order status/details.

        Endpoint: GET /fapi/v1/order
        """
        sym = to_binance_futures_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if order_id:
            params["orderId"] = str(order_id)
        elif client_order_id:
            params["origClientOrderId"] = str(client_order_id)
        else:
            raise LiveTradingError("Binance get_order requires order_id or client_order_id")
        return self._signed_request("GET", "/fapi/v1/order", params=params)

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
        Poll order detail to obtain (best-effort) executed quantity, average price,
        and commission (via userTrades).

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

        while True:
            try:
                last = self.get_order(symbol=symbol, order_id=str(order_id or ""), client_order_id=str(client_order_id or ""))
            except Exception:
                last = last or {}

            status = str(last.get("status") or "")
            try:
                filled = float(last.get("executedQty") or 0.0)
            except Exception:
                filled = 0.0

            avg_price = 0.0
            try:
                if last.get("avgPrice") is not None and str(last.get("avgPrice")).strip() != "":
                    avg_price = float(last.get("avgPrice") or 0.0)
            except Exception:
                avg_price = 0.0
            if avg_price <= 0 and filled > 0:
                try:
                    cum_quote = float(last.get("cumQuote") or 0.0)
                    if cum_quote > 0:
                        avg_price = cum_quote / filled
                except Exception:
                    pass
            if avg_price <= 0:
                try:
                    avg_price = float(last.get("price") or 0.0)
                except Exception:
                    avg_price = 0.0

            if filled > 0 and avg_price > 0:
                fee, fee_ccy = self._fetch_commission_for_order(symbol=symbol, order_id=order_id, filled=filled, avg_price=avg_price)
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}

            if status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                fee, fee_ccy = 0.0, ""
                if filled > 0:
                    fee, fee_ccy = self._fetch_commission_for_order(symbol=symbol, order_id=order_id, filled=filled, avg_price=avg_price)
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}

            if time.time() >= end_ts:
                fee, fee_ccy = 0.0, ""
                if filled > 0:
                    fee, fee_ccy = self._fetch_commission_for_order(symbol=symbol, order_id=order_id, filled=filled, avg_price=avg_price)
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            time.sleep(float(poll_interval_sec or 0.5))

    def _fetch_commission_for_order(self, *, symbol: str, order_id: str, filled: float, avg_price: float) -> Tuple[float, str]:
        """Fetch real commission from userTrades; fall back to commissionRate calculation."""
        oid = str(order_id or "").strip()
        # Method 1: userTrades (up to 3 attempts with 1s delay)
        for attempt in range(3):
            try:
                trades = self.get_user_trades(symbol=symbol, order_id=oid, limit=200) if oid else []
                if not isinstance(trades, list):
                    trades = []
                total_fee = 0.0
                fee_ccy = ""
                for t in trades:
                    if not isinstance(t, dict):
                        continue
                    try:
                        c = float(t.get("commission") or 0.0)
                    except (ValueError, TypeError):
                        c = 0.0
                    ccy = str(t.get("commissionAsset") or "").strip()
                    if c != 0.0:
                        total_fee += abs(c)
                        if not fee_ccy and ccy:
                            fee_ccy = ccy
                if total_fee > 0:
                    logger.debug("Binance fee via userTrades: %.8f %s (order=%s, attempt=%d)", total_fee, fee_ccy, oid, attempt)
                    return total_fee, fee_ccy
                if attempt < 2:
                    time.sleep(1.5)
            except Exception as e:
                logger.warning("Binance userTrades fee query failed (attempt=%d): %s", attempt, e)
                if attempt < 2:
                    time.sleep(1.0)

        # Method 2: calculate from commissionRate
        if filled > 0 and avg_price > 0:
            try:
                rate_info = self.get_fee_rate(symbol=symbol)
                if rate_info:
                    taker_rate = float(rate_info.get("taker") or 0.0)
                    if taker_rate > 0:
                        calc_fee = filled * avg_price * taker_rate
                        logger.info("Binance fee via commissionRate: %.8f USDT (rate=%.6f, order=%s)", calc_fee, taker_rate, oid)
                        return calc_fee, "USDT"
            except Exception as e:
                logger.warning("Binance commissionRate fallback failed: %s", e)

        logger.warning("Binance could not obtain fee for order=%s symbol=%s", oid, symbol)
        return 0.0, ""

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        reduce_only: bool = False,
        position_side: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        sym = to_binance_futures_symbol(symbol)
        sd = (side or "").upper()
        if sd not in ("BUY", "SELL"):
            raise LiveTradingError(f"Invalid side: {side}")
        q_req = float(quantity or 0.0)
        q_dec, qty_precision = self._normalize_quantity(symbol=symbol, quantity=q_req, for_market=True)
        if float(q_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid quantity (below step/minQty): requested={q_req}")

        # Best-effort MIN_NOTIONAL validation (common reason for "open still fails" with small qty).
        # Use markPrice as an approximation for MARKET order notional.
        min_notional = Decimal("0")
        mark_price = 0.0
        notional = Decimal("0")
        try:
            fdict = self.get_symbol_filters(symbol=symbol) or {}
            mn = (fdict.get("MIN_NOTIONAL") or {}).get("notional")
            min_notional = self._to_dec(mn or "0")
            if min_notional > 0:
                mark_price = float(self.get_mark_price(symbol=symbol) or 0.0)
                if mark_price > 0:
                    notional = q_dec * self._to_dec(mark_price)
                    if notional < min_notional:
                        raise LiveTradingError(
                            "Order notional is below MIN_NOTIONAL. "
                            f"symbol={sym} side={sd} qty={self._dec_str(q_dec, strict_precision=qty_precision)} "
                            f"markPrice={mark_price} notional={self._dec_str(notional)} "
                            f"minNotional={self._dec_str(min_notional)}"
                        )
        except LiveTradingError:
            raise
        except Exception:
            # Never block order placement due to a best-effort validation failure.
            pass

        params: Dict[str, Any] = {
            "symbol": sym,
            "side": sd,
            "type": "MARKET",
            "quantity": self._dec_str(q_dec, strict_precision=qty_precision),
        }
        client_order_id_norm = self._format_client_order_id(client_order_id)
        if client_order_id_norm:
            params["newClientOrderId"] = client_order_id_norm

        # Hedge mode requires explicit positionSide (LONG/SHORT). One-way mode should not use LONG/SHORT.
        dual_side = self.get_dual_side_position()
        pos_norm = self._normalize_position_side(position_side)
        if dual_side is True:
            params["positionSide"] = (pos_norm if pos_norm in ("LONG", "SHORT") else self._infer_position_side(side=sd, reduce_only=reduce_only))
        elif dual_side is False:
            # Keep default (BOTH) by omitting positionSide.
            params.pop("positionSide", None)
        else:
            # Unknown mode: try without positionSide first; we may retry on -4061.
            params.pop("positionSide", None)

        # reduceOnly after positionSide: in hedge mode, Binance returns -1106 if both are sent.
        if reduce_only and not (dual_side is True and params.get("positionSide") in ("LONG", "SHORT")):
            params["reduceOnly"] = "true"

        try:
            raw = self._signed_request("POST", "/fapi/v1/order", params=params)
        except LiveTradingError as e:
            # Retry once if position mode mismatch (-4061).
            if self._is_err_code(e, -4061):
                params2 = dict(params)
                if params2.get("positionSide"):
                    # Likely one-way mode but we sent LONG/SHORT
                    params2.pop("positionSide", None)
                    try:
                        raw = self._signed_request("POST", "/fapi/v1/order", params=params2)
                        # Cache for future calls.
                        self._dual_side_cache = (time.time(), False)
                        return LiveOrderResult(
                            exchange_id="binance",
                            exchange_order_id=str(raw.get("orderId") or raw.get("clientOrderId") or ""),
                            filled=float(raw.get("executedQty") or 0.0),
                            avg_price=float(raw.get("avgPrice") or raw.get("price") or 0.0),
                            raw=raw,
                        )
                    except Exception:
                        pass
                else:
                    # Likely hedge mode; retry with inferred positionSide.
                    params2["positionSide"] = (pos_norm if pos_norm in ("LONG", "SHORT") else self._infer_position_side(side=sd, reduce_only=reduce_only))
                    params2.pop("reduceOnly", None)
                    try:
                        raw = self._signed_request("POST", "/fapi/v1/order", params=params2)
                        self._dual_side_cache = (time.time(), True)
                        return LiveOrderResult(
                            exchange_id="binance",
                            exchange_order_id=str(raw.get("orderId") or raw.get("clientOrderId") or ""),
                            filled=float(raw.get("executedQty") or 0.0),
                            avg_price=float(raw.get("avgPrice") or raw.get("price") or 0.0),
                            raw=raw,
                        )
                    except Exception:
                        pass

            # Attach normalized params for easier debugging of precision issues (-1111).
            # Also attach best-effort public filters and minNotional diagnostics.
            step = "n/a"
            qty_prec = "n/a"
            min_not = "n/a"
            filt_symbol = "n/a"
            contract_type = "n/a"
            dual_mode = "n/a"
            pos_side_used = "n/a"
            try:
                fdict = self.get_symbol_filters(symbol=symbol) or {}
                lot = fdict.get("MARKET_LOT_SIZE") or fdict.get("LOT_SIZE") or {}
                step = str(lot.get("stepSize") or "n/a")
                meta = fdict.get("_meta") or {}
                if isinstance(meta, dict) and meta.get("quantityPrecision") is not None:
                    qty_prec = str(meta.get("quantityPrecision"))
                if isinstance(meta, dict) and meta.get("symbol"):
                    filt_symbol = str(meta.get("symbol"))
                if isinstance(meta, dict) and meta.get("contractType"):
                    contract_type = str(meta.get("contractType"))
                mn = fdict.get("MIN_NOTIONAL") or {}
                min_not = str(mn.get("notional") or "n/a")
                dm = self.get_dual_side_position()
                dual_mode = "true" if dm is True else ("false" if dm is False else "unknown")
                pos_side_used = str((params or {}).get("positionSide") or "n/a")
            except Exception:
                pass
            raise LiveTradingError(
                f"{e} | debug: symbol={sym} side={sd} "
                f"qty_req={q_req} qty_norm={self._dec_str(q_dec, strict_precision=qty_precision)} "
                f"base_url={self.base_url} filtersSymbol={filt_symbol} contractType={contract_type} "
                f"stepSize={step} quantityPrecision={qty_prec} minNotional={min_not} "
                f"dualSidePosition={dual_mode} positionSide={pos_side_used} "
                f"markPrice={mark_price} notional={self._dec_str(notional)}"
            )

        # Best-effort parse fill info.
        exchange_order_id = str(raw.get("orderId") or raw.get("clientOrderId") or "")
        filled = float(raw.get("executedQty") or 0.0)
        avg_price = float(raw.get("avgPrice") or raw.get("price") or 0.0)

        return LiveOrderResult(
            exchange_id="binance",
            exchange_order_id=exchange_order_id,
            filled=filled,
            avg_price=avg_price,
            raw=raw,
        )

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        reduce_only: bool = False,
        position_side: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        sym = to_binance_futures_symbol(symbol)
        sd = (side or "").upper()
        if sd not in ("BUY", "SELL"):
            raise LiveTradingError(f"Invalid side: {side}")
        q_req = float(quantity or 0.0)
        px = float(price or 0.0)
        if q_req <= 0 or px <= 0:
            raise LiveTradingError("Invalid quantity/price")
        q_dec, qty_precision = self._normalize_quantity(symbol=symbol, quantity=q_req, for_market=False)
        if float(q_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid quantity (below step/minQty): requested={q_req}")
        px_dec = self._normalize_price(symbol=symbol, price=px)
        if float(px_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid price (bad tick/minPrice): requested={px}")

        params: Dict[str, Any] = {
            "symbol": sym,
            "side": sd,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": self._dec_str(q_dec, strict_precision=qty_precision),
            "price": self._dec_str(px_dec),
        }
        client_order_id_norm = self._format_client_order_id(client_order_id)
        if client_order_id_norm:
            params["newClientOrderId"] = client_order_id_norm

        dual_side = self.get_dual_side_position()
        pos_norm = self._normalize_position_side(position_side)
        if dual_side is True:
            params["positionSide"] = (pos_norm if pos_norm in ("LONG", "SHORT") else self._infer_position_side(side=sd, reduce_only=reduce_only))
        elif dual_side is False:
            params.pop("positionSide", None)
        else:
            params.pop("positionSide", None)

        if reduce_only and not (dual_side is True and params.get("positionSide") in ("LONG", "SHORT")):
            params["reduceOnly"] = "true"
        try:
            raw = self._signed_request("POST", "/fapi/v1/order", params=params)
        except LiveTradingError as e:
            if self._is_err_code(e, -4061):
                params2 = dict(params)
                if params2.get("positionSide"):
                    params2.pop("positionSide", None)
                    try:
                        raw = self._signed_request("POST", "/fapi/v1/order", params=params2)
                        self._dual_side_cache = (time.time(), False)
                        return LiveOrderResult(
                            exchange_id="binance",
                            exchange_order_id=str(raw.get("orderId") or raw.get("clientOrderId") or ""),
                            filled=float(raw.get("executedQty") or 0.0),
                            avg_price=float(raw.get("avgPrice") or raw.get("price") or 0.0),
                            raw=raw,
                        )
                    except Exception:
                        pass
                else:
                    params2["positionSide"] = (pos_norm if pos_norm in ("LONG", "SHORT") else self._infer_position_side(side=sd, reduce_only=reduce_only))
                    params2.pop("reduceOnly", None)
                    try:
                        raw = self._signed_request("POST", "/fapi/v1/order", params=params2)
                        self._dual_side_cache = (time.time(), True)
                        return LiveOrderResult(
                            exchange_id="binance",
                            exchange_order_id=str(raw.get("orderId") or raw.get("clientOrderId") or ""),
                            filled=float(raw.get("executedQty") or 0.0),
                            avg_price=float(raw.get("avgPrice") or raw.get("price") or 0.0),
                            raw=raw,
                        )
                    except Exception:
                        pass
            raise LiveTradingError(
                f"{e} | debug: symbol={sym} side={sd} "
                f"qty_req={q_req} qty_norm={self._dec_str(q_dec, strict_precision=qty_precision)} "
                f"price_req={px} price_norm={self._dec_str(px_dec)}"
            )
        exchange_order_id = str(raw.get("orderId") or raw.get("clientOrderId") or "")
        filled = float(raw.get("executedQty") or 0.0)
        avg_price = float(raw.get("avgPrice") or raw.get("price") or 0.0)
        return LiveOrderResult(exchange_id="binance", exchange_order_id=exchange_order_id, filled=filled, avg_price=avg_price, raw=raw)

    def cancel_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        sym = to_binance_futures_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if order_id:
            params["orderId"] = str(order_id)
        elif client_order_id:
            params["origClientOrderId"] = str(client_order_id)
        else:
            raise LiveTradingError("Binance cancel_order requires order_id or client_order_id")
        return self._signed_request("DELETE", "/fapi/v1/order", params=params)

    def set_margin_type(self, *, symbol: str, margin_mode: str) -> Dict[str, Any]:
        """
        Set symbol margin mode on USDT-M futures.

        Endpoint: POST /fapi/v1/marginType
        margin_mode: cross | crossed | isolated
        """
        sym = to_binance_futures_symbol(symbol)
        m = (margin_mode or "").strip().lower()
        if m in ("cross", "crossed"):
            mt = "CROSSED"
        elif m in ("isolated", "iso"):
            mt = "ISOLATED"
        else:
            raise LiveTradingError(f"Invalid margin_mode for Binance: {margin_mode}")
        return self._signed_request("POST", "/fapi/v1/marginType", params={"symbol": sym, "marginType": mt})

    def get_positions(self, *, symbol: str = "") -> Any:
        """
        Futures positions (position risk). Optional ``symbol`` filters to one contract.

        Endpoint: GET /fapi/v2/positionRisk
        """
        raw = self._signed_request("GET", "/fapi/v2/positionRisk", params={})
        rows: list
        if isinstance(raw, list):
            rows = raw
        elif isinstance(raw, dict) and isinstance(raw.get("raw"), list):
            rows = raw["raw"]
        else:
            rows = []
        want = (symbol or "").strip()
        if not want:
            return rows
        sym = to_binance_futures_symbol(want)
        return [p for p in rows if isinstance(p, dict) and str(p.get("symbol") or "") == sym]


