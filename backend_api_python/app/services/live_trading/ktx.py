"""
KTX (direct REST) client for spot / USDT-M perpetual orders.

API docs: https://ktx-private.github.io/api-zh/
Base URL: https://api.ktx.app
  Market Data: /api/...
  User Data:   /papi/...

Auth:
  Headers: api-key, api-sign, api-expire-time
  Sign:    HMAC-SHA256(apiSecret, expireTime + queryString|body)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.symbols import to_ktx_symbol

logger = logging.getLogger(__name__)


class KtxClient(BaseRestClient):
    """KTX direct REST client supporting spot and USDT-M futures."""

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        base_url: str = "https://api.ktx.app",
        timeout_sec: float = 15.0,
        market_type: str = "swap",  # "spot" or "swap"
    ):
        super().__init__(base_url=base_url.rstrip("/"), timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        mt = (market_type or "swap").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual", "lpc"):
            mt = "swap"
        if mt not in ("spot", "swap"):
            mt = "swap"
        self.market_type = mt

        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing KTX api_key/secret_key")

        # Cache for product metadata (qty step, price precision, min qty)
        self._product_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._product_cache_ttl_sec = 300.0

    # ------------------------------------------------------------------
    # Numeric helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _dec_str(d: Decimal, max_decimals: int = 18, strict_precision: Optional[int] = None) -> str:
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
                        if "." in s:
                            s = s.rstrip("0").rstrip(".")
                        return s if s else "0"
                except Exception:
                    pass
            s = format(normalized, f".{max_decimals}f")
            if "." in s:
                s = s.rstrip("0").rstrip(".")
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
                            if "." in s:
                                s = s.rstrip("0").rstrip(".")
                            return s if s else "0"
                    except Exception:
                        pass
                s = format(f, f".{max_decimals}f")
                if "." in s:
                    s = s.rstrip("0").rstrip(".")
                return s if s else "0"
            except Exception:
                s = str(d)
                if "e" in s.lower() or "E" in s:
                    try:
                        f = float(s)
                        s = format(f, f".{max_decimals}f")
                        if "." in s:
                            s = s.rstrip("0").rstrip(".")
                    except Exception:
                        pass
                return s if s else "0"

    @staticmethod
    def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            return value
        try:
            return (value // step) * step
        except Exception:
            return value

    # ------------------------------------------------------------------
    # Auth / request helpers
    # ------------------------------------------------------------------

    def _sign(self, message: str) -> str:
        return hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _public_request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Public market data request (no auth). ``path`` should start with ``/v1/...``."""
        url_path = f"/api{path}"
        status, parsed, text = self._request(method, url_path, **kwargs)
        if status >= 400:
            raise LiveTradingError(f"KTX public {method} {url_path} HTTP {status}: {text[:500]}")
        return parsed if isinstance(parsed, dict) else {"raw": parsed}

    def _signed_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Signed private request. ``path`` should start with ``/v1/...`` (``/papi`` prefix is added)."""
        method_up = str(method or "GET").upper()
        expire_time = str(int(time.time() * 1000) + 30000)

        if method_up == "GET":
            query_str = ""
            if params:
                norm = {str(k): "" if v is None else str(v) for k, v in dict(params).items()}
                query_str = urlencode(sorted(norm.items()), doseq=True)
            message = expire_time + query_str
            sign = self._sign(message)
            url_path = f"/papi{path}"
            if query_str:
                url_path += f"?{query_str}"
            headers = {
                "api-key": self.api_key,
                "api-sign": sign,
                "api-expire-time": expire_time,
            }
            status, parsed, text = self._request(method_up, url_path, headers=headers)
        else:
            body_str = self._json_dumps(json_body) if json_body else ""
            message = expire_time + body_str
            sign = self._sign(message)
            headers = {
                "api-key": self.api_key,
                "api-sign": sign,
                "api-expire-time": expire_time,
                "Content-Type": "application/json",
            }
            url_path = f"/papi{path}"
            # NOTE: pass ``data=body_str`` (raw) so the wire bytes match the signed payload.
            # ``json=...`` would re-serialize with different separators and break the signature.
            status, parsed, text = self._request(
                method_up,
                url_path,
                data=body_str if body_str else None,
                headers=headers,
            )

        if status >= 400:
            raise LiveTradingError(f"KTX signed {method_up} {url_path} HTTP {status}: {text[:500]}")
        return parsed if isinstance(parsed, dict) else {"raw": parsed}

    def _ktx_market_param(self) -> str:
        return "spot" if self.market_type == "spot" else "lpc"

    # ------------------------------------------------------------------
    # Product metadata / normalization
    # ------------------------------------------------------------------

    def _get_product(self, symbol: str) -> Dict[str, Any]:
        """Fetch and cache product metadata (qty/precision filters)."""
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        now = time.time()
        cached = self._product_cache.get(ktx_sym)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= self._product_cache_ttl_sec:
                return obj
        j = self._public_request(
            "GET",
            "/v1/products",
            params={"symbol": ktx_sym, "market": self._ktx_market_param()},
        )
        result = (j.get("result") if isinstance(j, dict) else None) or []
        first: Dict[str, Any] = {}
        if isinstance(result, list) and result:
            cand = result[0]
            if isinstance(cand, dict):
                first = cand
        elif isinstance(result, dict):
            first = result
        if first:
            self._product_cache[ktx_sym] = (now, first)
        return first

    def _normalize_qty(self, *, symbol: str, qty: float) -> Tuple[Decimal, Optional[int]]:
        q = self._to_dec(qty)
        if q <= 0:
            return (Decimal("0"), None)
        info = self._get_product(symbol) or {}
        amount_scale = info.get("amount_scale")
        min_base_amount = info.get("min_base_amount")
        step = self._to_dec(amount_scale) if amount_scale else Decimal("0")
        mn = self._to_dec(min_base_amount) if min_base_amount else Decimal("0")

        qty_precision: Optional[int] = None
        if step > 0:
            q = self._floor_to_step(q, step)
            try:
                step_str = str(step.normalize())
                if "." in step_str:
                    qty_precision = len(step_str.split(".")[1])
                else:
                    qty_precision = 0
            except Exception:
                qty_precision = None

        if mn > 0 and q < mn:
            return (Decimal("0"), qty_precision)
        return (q, qty_precision)

    def _normalize_price(self, *, symbol: str, price: float) -> Tuple[Decimal, Optional[int]]:
        p = self._to_dec(price)
        if p <= 0:
            return (Decimal("0"), None)
        info = self._get_product(symbol) or {}
        price_scale = info.get("price_scale")
        tick = self._to_dec(price_scale) if price_scale else Decimal("0")
        if tick > 0:
            p = self._floor_to_step(p, tick)

        price_precision: Optional[int] = None
        if tick > 0:
            try:
                tick_str = str(tick.normalize())
                if "." in tick_str:
                    price_precision = len(tick_str.split(".")[1])
                else:
                    price_precision = 0
            except Exception:
                price_precision = None
        return (p, price_precision)

    # ------------------------------------------------------------------
    # Market data / account
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Public connectivity check."""
        try:
            _ = self._public_request(
                "GET",
                "/v1/products",
                params={"market": self._ktx_market_param()},
            )
            return True
        except Exception:
            return False

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        j = self._public_request(
            "GET",
            "/v1/ticker",
            params={"symbol": ktx_sym, "market": self._ktx_market_param()},
        )
        result = (j.get("result") if isinstance(j, dict) else None) or []
        if isinstance(result, list) and result:
            cand = result[0]
            return cand if isinstance(cand, dict) else {}
        if isinstance(result, dict):
            return result
        return {}

    def get_account(self) -> Dict[str, Any]:
        """Get wallet account assets (main account)."""
        return self._signed_request("GET", "/v1/main/accounts")

    def get_balance(self) -> Dict[str, Any]:
        """Get wallet account assets (main account)."""
        return self.get_account()

    def get_trade_balance(self, *, asset: str = "") -> Dict[str, Any]:
        """
        Get trade account assets (futures/margin collateral).

        Args:
            asset: Optional asset code (e.g. "BTC", "USDT"). 
                   If empty, returns all assets.
        """
        params: Dict[str, Any] = {}
        if asset:
            params["asset"] = asset
        return self._signed_request("GET", "/v1/trade/accounts", params=params if params else None)

    def get_positions(self) -> List[Dict[str, Any]]:
        """KTX futures positions from trade account. Spot returns empty list."""
        if self.market_type == "spot":
            return []
        # get_trade_balance returns the full trade account response
        j = self.get_trade_balance()
        result = (j.get("result") if isinstance(j, dict) else None) or []
        if isinstance(result, dict):
            return [result] if result else []
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

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
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")

        q_req = float(qty or 0.0)
        q_dec, qty_precision = self._normalize_qty(symbol=symbol, qty=q_req)
        if float(q_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid qty (below step/min): requested={q_req}")

        body: Dict[str, Any] = {
            "symbol": ktx_sym,
            "side": sd,
            "type": "market",
            "amount": self._dec_str(q_dec, strict_precision=qty_precision),
        }
        if client_order_id:
            body["client_order_id"] = str(client_order_id)

        raw = self._signed_request("POST", "/v1/order", json_body=body)
        res = raw if isinstance(raw, dict) else {}
        # Some KTX responses wrap result in "result"
        result = res.get("result") if isinstance(res.get("result"), dict) else res
        oid = str((result or {}).get("id") or (result or {}).get("client_order_id") or "")
        return LiveOrderResult(
            exchange_id="ktx",
            exchange_order_id=oid,
            filled=0.0,
            avg_price=0.0,
            raw=res,
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
        time_in_force: str = "GTC",
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")

        q_req = float(qty or 0.0)
        q_dec, qty_precision = self._normalize_qty(symbol=symbol, qty=q_req)
        if float(q_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid qty (below step/min): requested={q_req}")

        p_req = float(price or 0.0)
        p_dec, price_precision = self._normalize_price(symbol=symbol, price=p_req)
        if float(p_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid price: {p_req}")

        body: Dict[str, Any] = {
            "symbol": ktx_sym,
            "side": sd,
            "type": "limit",
            "amount": self._dec_str(q_dec, strict_precision=qty_precision),
            "price": self._dec_str(p_dec, strict_precision=price_precision),
            "time_in_force": (time_in_force or "GTC").upper(),
        }
        if client_order_id:
            body["client_order_id"] = str(client_order_id)

        raw = self._signed_request("POST", "/v1/order", json_body=body)
        res = raw if isinstance(raw, dict) else {}
        result = res.get("result") if isinstance(res.get("result"), dict) else res
        oid = str((result or {}).get("id") or (result or {}).get("client_order_id") or "")
        return LiveOrderResult(
            exchange_id="ktx",
            exchange_order_id=oid,
            filled=0.0,
            avg_price=0.0,
            raw=res,
        )

    def get_order(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> Dict[str, Any]:
        if not order_id and not client_order_id:
            raise LiveTradingError("KTX get_order requires order_id or client_order_id")
        if order_id:
            return self._signed_request("GET", f"/v1/orders/{order_id}")
        # Query by client_order_id via list scan
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        j = self._signed_request(
            "GET",
            "/v1/orders",
            params={"symbol": ktx_sym, "limit": 100},
        )
        result = (j.get("result") if isinstance(j, dict) else None) or []
        if isinstance(result, list):
            for it in result:
                if isinstance(it, dict) and str(it.get("client_order_id") or "") == str(client_order_id):
                    return it
        return {}

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> Dict[str, Any]:
        if not order_id and not client_order_id:
            raise LiveTradingError("KTX cancel_order requires order_id or client_order_id")
        if order_id:
            return self._signed_request("DELETE", f"/v1/orders/{order_id}")
        # Resolve client_order_id -> exchange order_id first
        o = self.get_order(symbol=symbol, client_order_id=client_order_id)
        oid = str(o.get("id") or "") if isinstance(o, dict) else ""
        if not oid:
            raise LiveTradingError(f"KTX cancel: cannot resolve client_order_id={client_order_id}")
        return self._signed_request("DELETE", f"/v1/orders/{oid}")

    def get_open_orders(self, *, symbol: str = "") -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"status": "open"}
        if symbol:
            params["symbol"] = to_ktx_symbol(symbol, market_type=self.market_type)
        j = self._signed_request("GET", "/v1/orders", params=params)
        result = (j.get("result") if isinstance(j, dict) else None) or []
        return result if isinstance(result, list) else []

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
        Poll KTX order status until filled / cancelled / rejected / timeout.

        Returns ``{"filled": float, "avg_price": float, "fee": float, "fee_ccy": str, "status": str, "order": dict}``.
        KTX order fields (best-effort, may need adjustment after live testing):
            - ``status``: "open" | "filled" | "cancelled" | "rejected" | "partial_filled"
            - ``filled_amount`` / ``deal_amount``: cumulative base filled
            - ``average_price`` / ``avg_price`` / ``price_avg``: VWAP
            - ``fee`` / ``fee_amount`` / ``deal_fee``: cumulative fee
            - ``fee_currency`` / ``fee_ccy``: fee currency
        """
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last: Dict[str, Any] = {}
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
            # Some KTX responses wrap the order under "result"
            order = last.get("result") if isinstance(last, dict) and isinstance(last.get("result"), dict) else last
            if not isinstance(order, dict):
                order = {}
            status = str(order.get("status") or order.get("state") or "").lower()
            try:
                filled = float(
                    order.get("filled_amount")
                    or order.get("deal_amount")
                    or order.get("executed_amount")
                    or order.get("filled")
                    or 0.0
                )
            except Exception:
                filled = 0.0
            try:
                avg_price = float(
                    order.get("average_price")
                    or order.get("avg_price")
                    or order.get("price_avg")
                    or order.get("deal_avg_price")
                    or 0.0
                )
            except Exception:
                avg_price = 0.0
            try:
                fee = abs(
                    float(
                        order.get("fee")
                        or order.get("fee_amount")
                        or order.get("deal_fee")
                        or 0.0
                    )
                )
            except Exception:
                fee = 0.0
            fee_ccy = str(order.get("fee_currency") or order.get("fee_ccy") or "")
            terminal = status in ("filled", "cancelled", "canceled", "rejected", "expired")
            if (filled > 0 and avg_price > 0) or terminal:
                # Wait one extra poll if fee not yet reported but we still have time.
                if fee <= 0 and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "status": status,
                    "order": order,
                }
            if timed_out:
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "status": status,
                    "order": order,
                }
            time.sleep(float(poll_interval_sec or 0.5))

    def set_leverage(self, *, symbol: str, leverage: float) -> Dict[str, Any]:
        """
        Best-effort leverage setter for KTX futures.

        KTX (lpc) leverage is configured per-account/per-symbol via ``/papi/v1/trade/leverage``
        in the public docs. Endpoint payload may differ between deployments — callers should
        wrap this in ``try/except`` because not all KTX accounts support runtime adjustment.
        Spot market is a no-op.
        """
        if self.market_type == "spot":
            return {"skipped": True, "reason": "spot"}
        try:
            lev = int(float(leverage or 0))
        except Exception:
            lev = 0
        if lev <= 0:
            return {"skipped": True, "reason": "invalid_leverage"}
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        body = {"symbol": ktx_sym, "leverage": lev}
        try:
            return self._signed_request("POST", "/v1/trade/leverage", json_body=body)
        except LiveTradingError as e:
            logger.debug(f"KTX set_leverage best-effort failed: {e}")
            return {"skipped": True, "error": str(e)}

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        """Return maker/taker fee from product info."""
        try:
            info = self._get_product(symbol) or {}
            maker = float(info.get("maker_fee") or 0)
            taker = float(info.get("taker_fee") or 0)
            return {"maker": maker, "taker": taker}
        except Exception:
            return None
