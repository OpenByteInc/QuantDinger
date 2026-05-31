"""
Binance Spot (direct REST) client.
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


class BinanceSpotClient(BaseRestClient):
    def __init__(self, *, api_key: str, secret_key: str, base_url: str = None, enable_demo_trading: bool = False, timeout_sec: float = 15.0, broker_id: str = ""):
        if not base_url:
            # Binance Spot Testnet (official): https://testnet.binance.vision
            base_url = "https://testnet.binance.vision" if enable_demo_trading else "https://api.binance.com"

        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.broker_id = (broker_id or "").strip()
        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing Binance api_key/secret_key")

        # Best-effort cache for public symbol filters used to normalize quantities.
        self._sym_filter_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._sym_filter_cache_ttl_sec = 300.0

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
                    # Use fixed-point format to ensure we don't exceed precision
                    s = format(quantized, f".{prec}f")
                    # Remove trailing zeros and decimal point if not needed
                    # This is safe because we've already quantized to the correct precision
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
                        if prec < 0:
                            prec = 0
                        if prec > 18:
                            prec = 18
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
    def _decimal_places_from_step(step: Decimal) -> Optional[int]:
        """Infer max decimal places from a Binance LOT stepSize string."""
        try:
            st = Decimal(step)
        except Exception:
            return None
        if st <= 0:
            return None
        try:
            normalized = st.normalize()
            step_str = str(normalized)
            if "." in step_str:
                return min(18, max(0, len(step_str.split(".")[1])))
            return 0
        except Exception:
            return None

    @staticmethod
    def _pick_lot_filter(fdict: Dict[str, Any], *, for_market: bool) -> Dict[str, Any]:
        """
        Choose LOT_SIZE / MARKET_LOT_SIZE filter.

        Binance often sets MARKET_LOT_SIZE.stepSize to 0; in that case we must fall
        back to LOT_SIZE or market orders fail LOT_SIZE (-1013).
        """
        lot = fdict.get("LOT_SIZE") if isinstance(fdict.get("LOT_SIZE"), dict) else {}
        if not for_market:
            return lot or {}
        market = fdict.get("MARKET_LOT_SIZE") if isinstance(fdict.get("MARKET_LOT_SIZE"), dict) else {}
        try:
            mstep = Decimal(str((market or {}).get("stepSize") or "0"))
        except Exception:
            mstep = Decimal("0")
        if mstep > 0:
            return market
        return lot or {}

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
        return hmac.new(self.secret_key.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

    def _signed_headers(self) -> Dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key}

    def _ensure_server_time(self, *, force: bool = False) -> None:
        """Align signed request timestamps with Binance (GET /api/v3/time)."""
        now_m = time.monotonic()
        if not force and (now_m - float(self._time_sync_monotonic or 0.0)) < 300.0:
            return
        try:
            code, data, _ = self._request("GET", "/api/v3/time")
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

    def _hint_binance_spot_2015(self, text: str) -> str:
        """
        Binance -2015 is vague; expand common root causes for operators.
        This path uses **spot** REST (api.binance.com or testnet.binance.vision), not futures fapi.
        """
        t = str(text or "")
        if "-2015" not in t:
            return ""
        host = (getattr(self, "base_url", None) or "").replace("https://", "").strip("/") or "?"
        return (
            f" | hint[-2015 host={host}]: "
            "确认 API 为**现货**权限(需勾选「允许现货及杠杆交易」/Enable Spot & Margin Trading)，"
            "仅有合约权限会失败；"
            "若凭证开启 Demo/模拟，密钥须来自币安测试网(https://testnet.binance.vision)，"
            "需到 testnet.binance.vision 用 GitHub 登录后单独申请 Spot Testnet Key，主网 Key 在测试网无效；"
            "IP 白名单填**本服务出站公网 IP**(Docker/云主机出口，非本机宽带)；"
            "核对 Key/Secret 无多余空格且与币安控制台一致。"
        )

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
                err = LiveTradingError(
                    f"BinanceSpot HTTP {code}: {text[:500]}{self._hint_binance_spot_2015(text)}"
                )
                if attempt == 0 and ("-1021" in text or "1021" in text):
                    self._ensure_server_time(force=True)
                    last_err = err
                    continue
                raise err
            if isinstance(data, dict) and data.get("code") and int(data.get("code")) < 0:
                err_txt = str(data)
                err = LiveTradingError(f"BinanceSpot error: {data}{self._hint_binance_spot_2015(err_txt)}")
                if attempt == 0 and int(data.get("code") or 0) == -1021:
                    self._ensure_server_time(force=True)
                    last_err = err
                    continue
                raise err
            return data if isinstance(data, dict) else {"raw": data}
        if last_err:
            raise last_err
        raise LiveTradingError("BinanceSpot signed request failed")

    def ping(self) -> bool:
        """
        Public connectivity check.

        Endpoint: GET /api/v3/time
        """
        code, data, _ = self._request("GET", "/api/v3/time")
        return code == 200 and isinstance(data, dict)

    def _public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        code, data, text = self._request(method, path, params=params, headers=None, json_body=None, data=None)
        if code >= 400:
            raise LiveTradingError(f"BinanceSpot HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and data.get("code") and int(data.get("code")) < 0:
            raise LiveTradingError(f"BinanceSpot error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def get_symbol_filters(self, *, symbol: str) -> Dict[str, Any]:
        """
        Get spot symbol filters from exchangeInfo (best-effort).

        Endpoint: GET /api/v3/exchangeInfo?symbol=...
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

        raw = self._public_request("GET", "/api/v3/exchangeInfo", params={"symbol": sym})
        symbols = raw.get("symbols") if isinstance(raw, dict) else None
        # Defensive: some gateways/proxies may strip query params; Binance may then return full list.
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
            # ``quantityPrecision`` is the order qty field; ``baseAssetPrecision`` is
            # wallet precision and is often looser than LOT_SIZE (causes -1013).
            qty_prec = None
            if isinstance(first, dict):
                qty_prec = first.get("quantityPrecision")
                if qty_prec is None:
                    qty_prec = first.get("baseAssetPrecision")
            price_prec = None
            if isinstance(first, dict):
                price_prec = first.get("quotePrecision")
                if price_prec is None:
                    price_prec = first.get("quoteAssetPrecision")
            meta = {
                "symbol": str(first.get("symbol") or "") if isinstance(first, dict) else "",
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
            q = Decimal("1").scaleb(-p)
            return value.quantize(q, rounding=ROUND_DOWN)
        except Exception:
            return value

    def _normalize_price(self, *, symbol: str, price: float) -> Decimal:
        """
        Normalize spot limit price using PRICE_FILTER tickSize (best-effort).
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

    def _normalize_quote_order_qty(self, *, symbol: str, quote_amount: float) -> Tuple[Decimal, Optional[int]]:
        """
        Normalize ``quoteOrderQty`` (USDT notional) for spot MARKET BUY.

        Binance only enforces ``MIN_NOTIONAL`` / ``NOTIONAL`` on this field;
        we still cap the precision so the wire body never carries scientific
        notation or 18 trailing zeros (which sometimes triggers ``-1100``).
        """
        amt = self._to_dec(quote_amount)
        if amt <= 0:
            return (Decimal("0"), None)
        fdict: Dict[str, Any] = {}
        try:
            fdict = self.get_symbol_filters(symbol=symbol) or {}
        except Exception:
            fdict = {}

        # Pull min-notional from either NOTIONAL or legacy MIN_NOTIONAL.
        min_notional = Decimal("0")
        for key in ("NOTIONAL", "MIN_NOTIONAL"):
            filt = fdict.get(key) if isinstance(fdict.get(key), dict) else None
            if not filt:
                continue
            for f in ("minNotional", "notional"):
                v = self._to_dec((filt or {}).get(f) or "0")
                if v > 0:
                    min_notional = v
                    break
            if min_notional > 0:
                break

        # Use ``quotePrecision`` (USDT decimals); default 8 (Binance accepts up to 8).
        quote_precision = None
        try:
            meta = fdict.get("_meta") or {}
            if isinstance(meta, dict) and meta.get("pricePrecision") is not None:
                quote_precision = int(meta.get("pricePrecision"))
        except Exception:
            quote_precision = None
        if quote_precision is None or quote_precision < 0 or quote_precision > 8:
            quote_precision = 8

        amt = self._floor_to_precision(amt, quote_precision)
        if min_notional > 0 and amt < min_notional:
            return (Decimal("0"), quote_precision)
        return (amt, quote_precision)

    def _normalize_quantity(self, *, symbol: str, quantity: float, for_market: bool) -> Tuple[Decimal, Optional[int]]:
        """
        Normalize spot order quantity using LOT_SIZE / MARKET_LOT_SIZE filters (best-effort).
        
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

        filt = self._pick_lot_filter(fdict, for_market=for_market)

        step = self._to_dec((filt or {}).get("stepSize") or "0")
        min_qty = self._to_dec((filt or {}).get("minQty") or "0")

        if step > 0:
            q = self._floor_to_step(q, step)

        step_precision = self._decimal_places_from_step(step)
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

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
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
        try:
            raw = self._signed_request("POST", "/api/v3/order", params=params)
        except LiveTradingError as e:
            raise LiveTradingError(
                f"{e} | debug: symbol={sym} side={sd} "
                f"qty_req={q_req} qty_norm={self._dec_str(q_dec, strict_precision=qty_precision)} "
                f"price_req={px} price_norm={self._dec_str(px_dec)}"
            )
        return LiveOrderResult(
            exchange_id="binance",
            exchange_order_id=str(raw.get("orderId") or raw.get("clientOrderId") or ""),
            filled=float(raw.get("executedQty") or 0.0),
            avg_price=float(raw.get("cummulativeQuoteQty") or 0.0) / float(raw.get("executedQty") or 1.0) if float(raw.get("executedQty") or 0.0) > 0 else 0.0,
            raw=raw,
        )

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float = 0.0,
        quote_order_qty: float = 0.0,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        """
        Place a spot MARKET order.

        - SELL: requires ``quantity`` (base asset, e.g. 0.001 BTC).
        - BUY:  prefer ``quote_order_qty`` (USDT notional) so we sidestep
                LOT_SIZE / MIN_NOTIONAL arithmetic; falls back to ``quantity``
                when only base is supplied.
        """
        sym = to_binance_futures_symbol(symbol)
        sd = (side or "").upper()
        if sd not in ("BUY", "SELL"):
            raise LiveTradingError(f"Invalid side: {side}")

        params: Dict[str, Any] = {
            "symbol": sym,
            "side": sd,
            "type": "MARKET",
        }

        debug_extra = ""
        if sd == "BUY" and float(quote_order_qty or 0.0) > 0:
            q_req = float(quote_order_qty)
            q_dec, quote_precision = self._normalize_quote_order_qty(
                symbol=symbol, quote_amount=q_req
            )
            if float(q_dec or 0) <= 0:
                raise LiveTradingError(
                    f"Invalid quoteOrderQty (below MIN_NOTIONAL or precision): requested={q_req}"
                )
            params["quoteOrderQty"] = self._dec_str(q_dec, strict_precision=quote_precision)
            debug_extra = (
                f"mode=quoteOrderQty quote_req={q_req} "
                f"quote_norm={self._dec_str(q_dec, strict_precision=quote_precision)}"
            )
        else:
            q_req = float(quantity or 0.0)
            q_dec, qty_precision = self._normalize_quantity(
                symbol=symbol, quantity=q_req, for_market=True
            )
            if float(q_dec or 0) <= 0:
                raise LiveTradingError(f"Invalid quantity (below step/minQty): requested={q_req}")
            params["quantity"] = self._dec_str(q_dec, strict_precision=qty_precision)
            debug_extra = (
                f"mode=quantity qty_req={q_req} "
                f"qty_norm={self._dec_str(q_dec, strict_precision=qty_precision)}"
            )

        client_order_id_norm = self._format_client_order_id(client_order_id)
        if client_order_id_norm:
            params["newClientOrderId"] = client_order_id_norm
        try:
            raw = self._signed_request("POST", "/api/v3/order", params=params)
        except LiveTradingError as e:
            raise LiveTradingError(f"{e} | debug: symbol={sym} side={sd} {debug_extra}")
        return LiveOrderResult(
            exchange_id="binance",
            exchange_order_id=str(raw.get("orderId") or raw.get("clientOrderId") or ""),
            filled=float(raw.get("executedQty") or 0.0),
            avg_price=float(raw.get("cummulativeQuoteQty") or 0.0) / float(raw.get("executedQty") or 1.0) if float(raw.get("executedQty") or 0.0) > 0 else 0.0,
            raw=raw,
        )

    def get_account(self) -> Dict[str, Any]:
        """
        Get spot account balances.

        Endpoint: GET /api/v3/account
        """
        return self._signed_request("GET", "/api/v3/account", params={})

    def get_my_trades(self, *, symbol: str, order_id: str = "", limit: int = 100) -> Any:
        """
        Fetch spot trade fills.

        Endpoint: GET /api/v3/myTrades
        """
        sym = to_binance_futures_symbol(symbol)
        if not sym:
            return []
        params: Dict[str, Any] = {"symbol": sym}
        if order_id:
            params["orderId"] = str(order_id)
        try:
            lim = int(limit or 100)
        except Exception:
            lim = 100
        lim = max(1, min(1000, lim))
        params["limit"] = lim
        data = self._signed_request("GET", "/api/v3/myTrades", params=params)
        return data

    def get_fee_for_order(self, *, symbol: str, order_id: str, max_retries: int = 3) -> Tuple[float, str]:
        """
        Best-effort: sum commissions from fills for a specific spot order.
        Retries a few times because myTrades may lag behind order fill.

        Returns: (total_fee, fee_ccy)
        """
        for attempt in range(max(1, max_retries)):
            try:
                trades = self.get_my_trades(symbol=symbol, order_id=str(order_id or ""), limit=200)
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

    def get_fee_rate(self, symbol: str, market_type: str = "spot") -> Optional[Dict[str, float]]:
        sym = symbol.upper().replace("-", "").replace("/", "")
        try:
            data = self._signed_request("GET", "/sapi/v1/asset/tradeFee", params={"symbol": sym})
            if isinstance(data, list) and data and isinstance(data[0], dict):
                rec = data[0]
                maker = abs(float(rec.get("makerCommission") or 0))
                taker = abs(float(rec.get("takerCommission") or 0))
                if maker > 0 or taker > 0:
                    return {"maker": maker, "taker": taker}
        except Exception as e:
            logger.warning(f"BinanceSpot get_fee_rate({symbol}) failed: {e}")
        return None

    def cancel_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        sym = to_binance_futures_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if order_id:
            params["orderId"] = str(order_id)
        elif client_order_id:
            params["origClientOrderId"] = str(client_order_id)
        else:
            raise LiveTradingError("BinanceSpot cancel_order requires order_id or client_order_id")
        return self._signed_request("DELETE", "/api/v3/order", params=params)

    def get_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        sym = to_binance_futures_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if order_id:
            params["orderId"] = str(order_id)
        elif client_order_id:
            params["origClientOrderId"] = str(client_order_id)
        else:
            raise LiveTradingError("BinanceSpot get_order requires order_id or client_order_id")
        return self._signed_request("GET", "/api/v3/order", params=params)

    def wait_for_fill(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
        max_wait_sec: float = 10.0,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
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
                cum_quote = float(last.get("cummulativeQuoteQty") or 0.0)
                if filled > 0 and cum_quote > 0:
                    avg_price = cum_quote / filled
            except Exception:
                pass

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
        """Fetch real commission from myTrades; fall back to tradeFee rate calculation."""
        oid = str(order_id or "").strip()
        # Method 1: myTrades (up to 3 attempts with 1.5s delay)
        for attempt in range(3):
            try:
                trades = self.get_my_trades(symbol=symbol, order_id=oid, limit=200) if oid else []
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
                    logger.debug("BinanceSpot fee via myTrades: %.8f %s (order=%s)", total_fee, fee_ccy, oid)
                    return total_fee, fee_ccy
                if attempt < 2:
                    time.sleep(1.5)
            except Exception as e:
                logger.warning("BinanceSpot myTrades fee query failed (attempt=%d): %s", attempt, e)
                if attempt < 2:
                    time.sleep(1.0)

        # Method 2: calculate from tradeFee rate
        if filled > 0 and avg_price > 0:
            try:
                rate_info = self.get_fee_rate(symbol=symbol)
                if rate_info:
                    taker_rate = float(rate_info.get("taker") or 0.0)
                    if taker_rate > 0:
                        calc_fee = filled * avg_price * taker_rate
                        logger.info("BinanceSpot fee via tradeFee rate: %.8f USDT (rate=%.6f, order=%s)", calc_fee, taker_rate, oid)
                        return calc_fee, "USDT"
            except Exception as e:
                logger.warning("BinanceSpot tradeFee rate fallback failed: %s", e)

        logger.warning("BinanceSpot could not obtain fee for order=%s symbol=%s", oid, symbol)
        return 0.0, ""


