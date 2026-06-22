"""
Bybit (direct REST) client for spot / linear perpetual orders (v5).

Signing (v5):
- X-BAPI-SIGN = hex(hmac_sha256(secret, timestamp + api_key + recv_window + payload))
- payload:
  - GET: query string (sorted, urlencoded)
  - POST: raw body string
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError

logger = logging.getLogger(__name__)
from app.services.live_trading.symbols import to_bybit_symbol


class BybitClient(BaseRestClient):
    _DEFAULT_BROKER_REFERER = "Ri001020"

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        base_url: str = "https://api.bybit.com",
        timeout_sec: float = 15.0,
        category: str = "linear",  # "linear" (USDT perpetual) or "spot"
        recv_window_ms: int = 12000,
        broker_referer: str = "",
        hedge_mode: bool = False,
    ):
        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.category = (category or "linear").strip().lower()
        self.broker_referer = (broker_referer or self._DEFAULT_BROKER_REFERER).strip()
        self.hedge_mode = bool(hedge_mode)
        if self.category not in ("linear", "spot"):
            self.category = "linear"
        try:
            self.recv_window_ms = int(recv_window_ms or 12000)
        except Exception:
            self.recv_window_ms = 12000
        if self.recv_window_ms < 5000:
            self.recv_window_ms = 5000
        if self.recv_window_ms > 60000:
            self.recv_window_ms = 60000

        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing Bybit api_key/secret_key")

        # Best-effort cache for linear instrument metadata (qty step, min qty, etc.)
        # Key: f"{category}:{symbol}" -> (fetched_at_ts, info_dict)
        self._inst_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._inst_cache_ttl_sec = 300.0

        # Bybit v5 rejects requests if local clock diverges from server (retCode 10002).
        # Offset = server_ms - local_ms; signed timestamp uses local_ms + offset.
        self._time_offset_ms: int = 0
        self._time_offset_at: float = 0.0
        self._time_sync_ttl_sec: float = 55.0

        # linear: symbol -> (fetched_at, is_hedge_mode) from GET /v5/position/list
        self._pos_mode_cache: Dict[str, Tuple[float, bool]] = {}
        self._pos_mode_cache_ttl_sec: float = 60.0

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
        Bybit requires quantities to match qtyStep precision.
        
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

    def _sign(self, prehash: str) -> str:
        return hmac.new(self.secret_key.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _parse_server_time_ms_from_market_time(raw: Dict[str, Any]) -> int:
        """Parse milliseconds from GET /v5/market/time (or similar) JSON."""
        if not isinstance(raw, dict):
            raise LiveTradingError("Bybit market/time: invalid response")
        res = raw.get("result")
        if isinstance(res, dict):
            nano = res.get("timeNano")
            if nano is not None and str(nano).strip() != "":
                try:
                    return int(int(str(nano)) // 1_000_000)
                except Exception:
                    pass
            sec = res.get("timeSecond")
            if sec is not None and str(sec).strip() != "":
                try:
                    return int(float(sec) * 1000)
                except Exception:
                    pass
        t = raw.get("time")
        if t is not None:
            try:
                return int(t)
            except Exception:
                pass
        raise LiveTradingError("Bybit market/time: missing time fields")

    def sync_server_time_offset(self, *, force: bool = False) -> None:
        """Align signing timestamp with Bybit server (public /v5/market/time)."""
        now = time.time()
        if (
            not force
            and self._time_offset_at > 0
            and (now - self._time_offset_at) < float(self._time_sync_ttl_sec or 55.0)
        ):
            return
        raw = self._public_request("GET", "/v5/market/time")
        srv_ms = self._parse_server_time_ms_from_market_time(raw)
        local_ms = int(time.time() * 1000)
        self._time_offset_ms = int(srv_ms - local_ms)
        self._time_offset_at = now

    def is_hedge_position_mode(self, *, symbol: str) -> bool:
        """
        Whether the Bybit account uses hedge (both-side) mode for ``symbol``.

        Queries GET /v5/position/list (returns slots even when flat). Falls back to
        ``hedge_mode`` from credentials when the API call fails.
        """
        if self.category != "linear":
            return bool(self.hedge_mode)
        sym = to_bybit_symbol(symbol)
        if not sym:
            return bool(self.hedge_mode)
        key = sym.upper()
        now = time.time()
        cached = self._pos_mode_cache.get(key)
        if cached:
            ts, hedge = cached
            if (now - float(ts or 0.0)) <= float(self._pos_mode_cache_ttl_sec or 60.0):
                return bool(hedge)
        detected: Optional[bool] = None
        try:
            raw = self.get_positions(symbol=symbol)
            lst = ((raw.get("result") or {}).get("list") or []) if isinstance(raw, dict) else []
            idxs: Set[int] = set()
            for row in lst:
                if not isinstance(row, dict):
                    continue
                try:
                    idxs.add(int(row.get("positionIdx") or 0))
                except Exception:
                    continue
            if 1 in idxs or 2 in idxs:
                detected = True
            elif idxs == {0} or (len(idxs) == 1 and 0 in idxs):
                detected = False
            elif len(idxs) >= 2:
                detected = True
        except Exception:
            detected = None
        hedge = bool(self.hedge_mode) if detected is None else bool(detected)
        self._pos_mode_cache[key] = (now, hedge)
        return hedge

    def _resolve_position_idx(self, pos_side: str, *, symbol: str = "") -> int:
        """
        Bybit linear positionIdx: 0 one-way; 1 hedge long slot; 2 hedge short slot.
        """
        if self.category != "linear":
            return 0
        if not self.is_hedge_position_mode(symbol=symbol):
            return 0
        ps = str(pos_side or "").strip().lower()
        if ps == "short":
            return 2
        return 1

    def _headers(self, ts_ms: str, sign: str) -> Dict[str, str]:
        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": sign,
            "X-BAPI-TIMESTAMP": ts_ms,
            "X-BAPI-RECV-WINDOW": str(self.recv_window_ms),
            "X-BAPI-SIGN-TYPE": "2",
            "Content-Type": "application/json",
        }
        if self.broker_referer:
            headers["Referer"] = self.broker_referer
        return headers

    def _signed_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        m = str(method or "GET").upper()
        body_str = self._json_dumps(json_body) if json_body is not None else ""
        qs_base = ""
        if params:
            norm = {str(k): "" if v is None else str(v) for k, v in dict(params).items()}
            qs_base = urlencode(sorted(norm.items()), doseq=True)
        payload_get = qs_base
        payload_post = body_str

        last_err: Optional[LiveTradingError] = None
        for attempt in range(2):
            try:
                self.sync_server_time_offset(force=(attempt > 0))
            except Exception as e:
                if attempt == 0:
                    # First attempt: still try with raw local time; second pass may recover.
                    pass
                else:
                    raise LiveTradingError(f"Bybit time sync failed: {e}") from e

            ts_ms = str(int(time.time() * 1000) + int(self._time_offset_ms or 0))
            payload = payload_get if m == "GET" else payload_post
            prehash = f"{ts_ms}{self.api_key}{self.recv_window_ms}{payload}"
            sign = self._sign(prehash)

            code, data, text = self._request(
                m,
                path,
                params=params if (m == "GET" and params) else (params or None),
                data=body_str if body_str else None,
                headers=self._headers(ts_ms, sign),
            )
            if code >= 400:
                raise LiveTradingError(f"Bybit HTTP {code}: {text[:500]}")
            if isinstance(data, dict):
                rc = data.get("retCode")
                try:
                    rc_int = int(rc) if rc is not None and str(rc).strip() != "" else 0
                except Exception:
                    rc_int = -1
                if rc_int == 10002 and attempt == 0:
                    last_err = LiveTradingError(f"Bybit error: {data}")
                    continue
                if rc not in (0, "0", None, ""):
                    raise LiveTradingError(f"Bybit error: {data}")
            return data if isinstance(data, dict) else {"raw": data}

        if last_err:
            raise last_err
        raise LiveTradingError("Bybit signed request failed after time resync")

    def _public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        code, data, text = self._request(method, path, params=params, headers=None, json_body=None, data=None)
        if code >= 400:
            raise LiveTradingError(f"Bybit HTTP {code}: {text[:500]}")
        if isinstance(data, dict):
            rc = data.get("retCode")
            if rc not in (0, "0", None, ""):
                raise LiveTradingError(f"Bybit error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def ping(self) -> bool:
        try:
            data = self._public_request("GET", "/v5/market/time")
            return isinstance(data, dict) and (data.get("retCode") in (0, "0", None, ""))
        except Exception:
            return False

    @staticmethod
    def _row_to_ticker_out(row: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(row, dict):
            return {}
        last_raw = row.get("lastPrice") or row.get("last") or row.get("markPrice") or row.get("indexPrice") or 0
        try:
            px = float(str(last_raw).replace(",", "").strip() or 0)
        except Exception:
            px = 0.0
        if px <= 0:
            return {}
        out: Dict[str, Any] = dict(row)
        out["last"] = px
        out["price"] = px
        return out

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        """
        Public market price for USDT notional -> base qty (quick_trade / execution).

        Tries: ``/v5/market/tickers`` (by symbol) → ``/v5/market/orderbook`` (mid) →
        ``/v5/market/tickers`` (category-only, scan list). Some environments return an empty
        ticker list for filtered queries; fallbacks avoid silent failure.
        """
        sym = to_bybit_symbol(symbol)
        if not sym:
            return {}
        cat = "spot" if (self.category or "").strip().lower() == "spot" else "linear"
        sym_u = sym.upper()

        # 1) Filtered tickers (preferred)
        try:
            raw = self._public_request("GET", "/v5/market/tickers", params={"category": cat, "symbol": sym_u})
            lst = (((raw or {}).get("result") or {}).get("list")) if isinstance(raw, dict) else None
            if isinstance(lst, list) and lst:
                out = self._row_to_ticker_out(lst[0] if isinstance(lst[0], dict) else {})
                if out:
                    return out
        except LiveTradingError:
            pass
        except Exception:
            pass

        # 2) Order book mid (bid/ask)
        try:
            ob = self._public_request(
                "GET",
                "/v5/market/orderbook",
                params={"category": cat, "symbol": sym_u, "limit": 25},
            )
            res = (ob.get("result") or {}) if isinstance(ob, dict) else {}
            bids = res.get("b") or []
            asks = res.get("a") or []
            bid_p = 0.0
            ask_p = 0.0
            if isinstance(bids, list) and bids and isinstance(bids[0], (list, tuple)) and len(bids[0]) > 0:
                try:
                    bid_p = float(str(bids[0][0]).replace(",", ""))
                except Exception:
                    bid_p = 0.0
            if isinstance(asks, list) and asks and isinstance(asks[0], (list, tuple)) and len(asks[0]) > 0:
                try:
                    ask_p = float(str(asks[0][0]).replace(",", ""))
                except Exception:
                    ask_p = 0.0
            mid = 0.0
            if bid_p > 0 and ask_p > 0:
                mid = (bid_p + ask_p) / 2.0
            else:
                mid = bid_p or ask_p
            if mid > 0:
                return {
                    "symbol": sym_u,
                    "last": mid,
                    "price": mid,
                    "bid1Price": bid_p,
                    "ask1Price": ask_p,
                }
        except LiveTradingError:
            pass
        except Exception:
            pass

        # 3) Full category ticker list, match symbol (larger payload; last resort)
        try:
            raw = self._public_request("GET", "/v5/market/tickers", params={"category": cat})
            lst = (((raw or {}).get("result") or {}).get("list")) if isinstance(raw, dict) else None
            if isinstance(lst, list):
                for row in lst:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("symbol") or "").strip().upper() != sym_u:
                        continue
                    out = self._row_to_ticker_out(row)
                    if out:
                        return out
        except LiveTradingError:
            pass
        except Exception:
            pass

        return {}

    def get_wallet_balance(self, *, account_type: str = "UNIFIED") -> Dict[str, Any]:
        return self._signed_request("GET", "/v5/account/wallet-balance", params={"accountType": str(account_type or "UNIFIED")})

    def get_instrument_info(self, *, category: str, symbol: str) -> Dict[str, Any]:
        cat = str(category or self.category or "linear").strip().lower()
        sym = to_bybit_symbol(symbol)
        if not sym:
            return {}
        key = f"{cat}:{sym}"
        now = time.time()
        cached = self._inst_cache.get(key)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._inst_cache_ttl_sec or 300.0):
                return obj
        raw = self._public_request("GET", "/v5/market/instruments-info", params={"category": cat, "symbol": sym})
        lst = (((raw.get("result") or {}).get("list")) if isinstance(raw, dict) else None) or []
        first: Dict[str, Any] = lst[0] if isinstance(lst, list) and lst else {}
        if isinstance(first, dict) and first:
            self._inst_cache[key] = (now, first)
        return first if isinstance(first, dict) else {}

    def _normalize_qty(self, *, symbol: str, qty: float) -> Tuple[Decimal, Optional[int]]:
        q = self._to_dec(qty)
        if q <= 0:
            return (Decimal("0"), None)
        sym = to_bybit_symbol(symbol)
        try:
            info = self.get_instrument_info(category=self.category, symbol=sym) or {}
        except Exception:
            info = {}
        lot = (info.get("lotSizeFilter") if isinstance(info, dict) else None) or {}
        step = self._to_dec((lot or {}).get("qtyStep") or "0")
        mn = self._to_dec((lot or {}).get("minOrderQty") or "0")
        if step > 0:
            q = self._floor_to_step(q, step)
        
        # Infer precision from qtyStep
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
        if qty_precision is None and self.category == "spot":
            # Avoid sending unrounded base qty when instrument metadata is missing.
            qty_precision = 4
        return (q, qty_precision)

    def _normalize_quantity(
        self,
        *,
        symbol: str,
        quantity: float,
        for_market: bool = True,
    ) -> Tuple[Decimal, Optional[int]]:
        """Spot sizing helper (``spot_sizing.normalize_spot_base_quantity``)."""
        _ = for_market
        return self._normalize_qty(symbol=symbol, qty=quantity)

    def get_spot_holdings(self, *, symbol: str = "") -> Dict[str, Any]:
        """
        Wallet coin balances for quick-trade spot position display.

        Bybit spot category has no ``/v5/position/list``; use wallet balances instead.
        """
        base_filter = ""
        if (symbol or "").strip():
            base_filter = str(symbol).split("/", 1)[0].split(":", 1)[0].strip().upper()
        rows: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for acct_type in ("UNIFIED", "SPOT"):
            try:
                raw = self.get_wallet_balance(account_type=acct_type)
            except Exception:
                continue
            lst = ((raw.get("result") or {}).get("list") or []) if isinstance(raw, dict) else []
            if not isinstance(lst, list):
                continue
            for acct in lst:
                if not isinstance(acct, dict):
                    continue
                for coin in acct.get("coin") or []:
                    if not isinstance(coin, dict):
                        continue
                    ccy = str(coin.get("coin") or "").upper()
                    if not ccy or ccy in seen:
                        continue
                    if base_filter and ccy != base_filter:
                        continue
                    try:
                        bal = float(
                            coin.get("walletBalance")
                            or coin.get("equity")
                            or coin.get("availableToWithdraw")
                            or 0
                        )
                    except Exception:
                        bal = 0.0
                    if bal <= 0:
                        continue
                    seen.add(ccy)
                    try:
                        avail = float(coin.get("availableToWithdraw") or coin.get("walletBalance") or bal)
                    except Exception:
                        avail = bal
                    sym_out = to_bybit_symbol(f"{ccy}/USDT") if ccy != "USDT" else ccy
                    rows.append(
                        {
                            "symbol": sym_out,
                            "bal": bal,
                            "availBal": avail,
                        }
                    )
        return {"result": {"list": rows}}

    def _normalize_price(self, *, symbol: str, price: float) -> Tuple[Decimal, Optional[int]]:
        p = self._to_dec(price)
        if p <= 0:
            return (Decimal("0"), None)
        sym = to_bybit_symbol(symbol)
        try:
            info = self.get_instrument_info(category=self.category, symbol=sym) or {}
        except Exception:
            info = {}
        pf = (info.get("priceFilter") if isinstance(info, dict) else None) or {}
        tick = self._to_dec((pf or {}).get("tickSize") or "0")
        if tick > 0:
            p = self._floor_to_step(p, tick)

        price_precision = None
        if tick > 0:
            try:
                tick_normalized = tick.normalize()
                tick_str = str(tick_normalized)
                if "." in tick_str:
                    price_precision = len(tick_str.split(".")[1])
                    if price_precision < 0:
                        price_precision = 0
                    if price_precision > 18:
                        price_precision = 18
                else:
                    price_precision = 0
            except Exception:
                pass
        return (p, price_precision)

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
        sym = to_bybit_symbol(symbol)
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        q_req = float(qty or 0.0)
        q_dec, qty_precision = self._normalize_qty(symbol=symbol, qty=q_req)
        if float(q_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid qty (below step/min): requested={q_req}")
        body: Dict[str, Any] = {
            "category": self.category,
            "symbol": sym,
            "side": "Buy" if sd == "buy" else "Sell",
            "orderType": "Market",
            "qty": self._dec_str(q_dec, strict_precision=qty_precision),
            "timeInForce": "IOC",
        }
        if self.category == "spot":
            body["marketUnit"] = "baseCoin"
        if self.category == "linear":
            body["positionIdx"] = self._resolve_position_idx(pos_side, symbol=symbol)
        if reduce_only and self.category == "linear":
            body["reduceOnly"] = True
        if client_order_id:
            body["orderLinkId"] = str(client_order_id)
        raw = self._signed_request("POST", "/v5/order/create", json_body=body)
        res = (raw.get("result") or {}) if isinstance(raw, dict) else {}
        oid = str(res.get("orderId") or res.get("orderLinkId") or "")
        return LiveOrderResult(exchange_id="bybit", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)

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
        sym = to_bybit_symbol(symbol)
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        q_req = float(qty or 0.0)
        px_req = float(price or 0.0)
        if q_req <= 0 or px_req <= 0:
            raise LiveTradingError("Invalid qty/price")
        q_dec, qty_precision = self._normalize_qty(symbol=symbol, qty=q_req)
        px_dec, price_precision = self._normalize_price(symbol=symbol, price=px_req)
        if float(q_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid qty (below step/min): requested={q_req}")
        if float(px_dec or 0) <= 0:
            raise LiveTradingError(f"Invalid price (below tick/min): requested={px_req}")
        body: Dict[str, Any] = {
            "category": self.category,
            "symbol": sym,
            "side": "Buy" if sd == "buy" else "Sell",
            "orderType": "Limit",
            "qty": self._dec_str(q_dec, strict_precision=qty_precision),
            "price": self._dec_str(px_dec, strict_precision=price_precision),
            "timeInForce": "GTC",
        }
        if self.category == "linear":
            body["positionIdx"] = self._resolve_position_idx(pos_side, symbol=symbol)
        if reduce_only and self.category == "linear":
            body["reduceOnly"] = True
        if client_order_id:
            body["orderLinkId"] = str(client_order_id)
        raw = self._signed_request("POST", "/v5/order/create", json_body=body)
        res = (raw.get("result") or {}) if isinstance(raw, dict) else {}
        oid = str(res.get("orderId") or res.get("orderLinkId") or "")
        return LiveOrderResult(exchange_id="bybit", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)

    def cancel_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        sym = to_bybit_symbol(symbol)
        body: Dict[str, Any] = {"category": self.category, "symbol": sym}
        if order_id:
            body["orderId"] = str(order_id)
        elif client_order_id:
            body["orderLinkId"] = str(client_order_id)
        else:
            raise LiveTradingError("Bybit cancel_order requires order_id or client_order_id")
        return self._signed_request("POST", "/v5/order/cancel", json_body=body)

    def get_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        sym = to_bybit_symbol(symbol)
        params: Dict[str, Any] = {"category": self.category, "symbol": sym}
        if order_id:
            params["orderId"] = str(order_id)
        elif client_order_id:
            params["orderLinkId"] = str(client_order_id)
        else:
            raise LiveTradingError("Bybit get_order requires order_id or client_order_id")
        raw = self._signed_request("GET", "/v5/order/realtime", params=params)
        lst = (((raw.get("result") or {}).get("list")) if isinstance(raw, dict) else None) or []
        first: Dict[str, Any] = lst[0] if isinstance(lst, list) and lst else {}
        return first if isinstance(first, dict) else {}

    def wait_for_fill(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
        max_wait_sec: float = 3.0,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last: Dict[str, Any] = {}
        while True:
            timed_out = time.time() >= end_ts
            try:
                last = self.get_order(symbol=symbol, order_id=str(order_id or ""), client_order_id=str(client_order_id or ""))
            except Exception:
                last = last or {}
            status = str(last.get("orderStatus") or last.get("order_status") or "")
            try:
                filled = float(last.get("cumExecQty") or 0.0)
            except Exception:
                filled = 0.0
            avg_price = 0.0
            try:
                avg_price = float(last.get("avgPrice") or 0.0)
            except Exception:
                avg_price = 0.0
            # Extract fee from cumExecFee (Bybit API field for cumulative execution fee)
            fee = 0.0
            fee_ccy = ""
            fee_detail = last.get("cumFeeDetail") if isinstance(last, dict) else None
            if isinstance(fee_detail, dict) and fee_detail:
                total_fee = 0.0
                fee_keys = []
                for k, v in fee_detail.items():
                    try:
                        fv = abs(float(v or 0.0))
                    except Exception:
                        fv = 0.0
                    if fv > 0:
                        total_fee += fv
                        fee_keys.append(str(k))
                fee = total_fee
                if len(fee_keys) == 1:
                    fee_ccy = fee_keys[0]
            if fee <= 0:
                try:
                    fee = abs(float(last.get("cumExecFee") or 0.0))
                except Exception:
                    fee = 0.0
                if fee > 0 and self.category == "linear":
                    fee_ccy = "USDT"
            # cumExecFee / cumFeeDetail can lag slightly after fill shows up.
            if filled > 0 and avg_price > 0:
                if fee <= 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            if status.lower() in ("filled", "cancelled", "canceled", "rejected"):
                if fee <= 0 and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            if timed_out:
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            time.sleep(float(poll_interval_sec or 0.5))

    def get_positions(
        self,
        *,
        symbol: str = "",
        settle_coin: str = "",
    ) -> Dict[str, Any]:
        """
        GET /v5/position/list — Bybit v5 requires ``symbol`` OR ``settleCoin`` with ``category``.

        - Pass ``symbol`` (e.g. ETH/USDT) to query one contract.
        - Omit ``symbol`` and pass ``settle_coin`` (default USDT) to list all USDT-linear positions.
        """
        if self.category == "spot":
            return self.get_spot_holdings(symbol=symbol)
        if self.category != "linear":
            raise LiveTradingError("Bybit positions are only supported for linear category in this client")
        params: Dict[str, Any] = {"category": "linear"}
        sym = to_bybit_symbol(symbol) if (symbol or "").strip() else ""
        if sym:
            params["symbol"] = sym
        else:
            sc = (settle_coin or "USDT").strip().upper() or "USDT"
            params["settleCoin"] = sc
        return self._signed_request("GET", "/v5/position/list", params=params)

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        cat = "spot" if market_type == "spot" else "linear"
        sym = to_bybit_symbol(symbol)
        try:
            raw = self._signed_request("GET", "/v5/account/fee-rate", params={"category": cat, "symbol": sym})
            result = raw.get("result") or {}
            items = result.get("list") or []
            if items and isinstance(items[0], dict):
                rec = items[0]
                maker = abs(float(rec.get("makerFeeRate") or 0))
                taker = abs(float(rec.get("takerFeeRate") or 0))
                if maker > 0 or taker > 0:
                    return {"maker": maker, "taker": taker}
        except Exception as e:
            logger.warning(f"Bybit get_fee_rate({symbol}) failed: {e}")
        return None

    def set_leverage(self, *, symbol: str, leverage: float) -> bool:
        if self.category != "linear":
            return False
        sym = to_bybit_symbol(symbol)
        try:
            lv = int(float(leverage or 1.0))
        except Exception:
            lv = 1
        if lv < 1:
            lv = 1
        # Bybit leverage caps vary per symbol; keep best-effort.
        body = {"category": "linear", "symbol": sym, "buyLeverage": str(lv), "sellLeverage": str(lv)}
        try:
            resp = self._signed_request("POST", "/v5/position/set-leverage", json_body=body)
            ok = isinstance(resp, dict) and (resp.get("retCode") in (0, "0", None, ""))
            return bool(ok)
        except Exception:
            return False


