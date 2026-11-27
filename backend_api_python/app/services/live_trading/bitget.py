"""
Bitget (direct REST) client for USDT-margined perpetual orders.

Signing (Bitget):
- ACCESS-SIGN = base64(hmac_sha256(secret, timestamp + method + request_path + body))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.ws_order_transport import submit_bitget_order
from app.utils.numeric_precision import floor_decimal_to_step, format_decimal
from app.utils.exchange_request_budget import ExchangeRateLimitError

logger = logging.getLogger(__name__)
from app.services.live_trading.symbols import to_bitget_um_symbol


def _settlement_currency(symbol: str, product_type: str) -> str:
    product = str(product_type or "").strip().upper()
    if product.startswith("USDT"):
        return "USDT"
    if product.startswith("USDC"):
        return "USDC"
    from app.services.live_trading.fee_quote import symbol_currencies

    base, quote = symbol_currencies(symbol)
    return base if product.startswith("COIN") else quote


class BitgetMixClient(BaseRestClient):
    _CHANNEL_API_CODE = "qvz9x"
    _shared_contract_cache: Dict[
        Tuple[str, str, str], Tuple[float, Dict[str, Any]]
    ] = {}
    _shared_contract_cache_lock = threading.RLock()
    _shared_pos_mode_cache: Dict[
        Tuple[str, str, bool, str], Tuple[float, str]
    ] = {}
    _shared_pos_mode_cache_lock = threading.RLock()

    _CHANNEL_API_CODE_ORDER_PATHS = {
        "/api/v3/trade/place-order",
        "/api/v3/trade/place-batch",
        "/api/v3/trade/modify-order",
        "/api/v3/trade/cancel-order",
        "/api/v3/trade/cancel-batch",
        "/api/v3/trade/place-strategy-order",
    }

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        passphrase: str,
        base_url: str = "https://api.bitget.com",
        timeout_sec: float = 15.0,
        channel_api_code: str = "",
        simulated_trading: bool = False,
    ):
        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.passphrase = (passphrase or "").strip()
        self.channel_api_code = self._CHANNEL_API_CODE
        self.simulated_trading = bool(simulated_trading)
        if not self.api_key or not self.secret_key or not self.passphrase:
            raise LiveTradingError("Missing Bitget api_key/secret_key/passphrase")

        # Best-effort cache for public contract metadata used to normalize order sizes.
        # Key: f"{product_type}:{symbol}" -> (fetched_at_ts, contract_dict)
        self._contract_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._contract_cache_ttl_sec = 300.0

        # Best-effort cache for leverage settings to avoid spamming set-leverage on every tick.
        # Key: f"{product_type}:{symbol}:{margin_coin}:{margin_mode}:{hold_side}:{lever}" -> (fetched_at_ts, True)
        self._lev_cache: Dict[str, Tuple[float, bool]] = {}
        self._lev_cache_ttl_sec = 60.0

        # holdMode from GET /api/v3/account/settings, cached per contract.
        self._pos_mode_cache: Dict[str, Tuple[float, str]] = {}
        self._pos_mode_cache_ttl_sec = 60.0

    @staticmethod
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _parse_fee_detail(raw_fd: Any, *, received_currency: str = "") -> Tuple[Decimal, str]:
        from app.services.live_trading.bitget_fees import fee_storage
        return fee_storage(
            raw_fd,
            received_currency=received_currency,
            v3_positive_cost=True,
        )

    @staticmethod
    def _dec_str(d: Decimal, max_decimals: int = 18, strict_precision: Optional[int] = None) -> str:
        """
        Convert Decimal to string with controlled precision.
        Bitget requires quantities to match sizeStep/sizePlace precision.
        
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
        return floor_decimal_to_step(value, step)

    @staticmethod
    def _normalize_margin_mode(margin_mode: str) -> str:
        """
        Normalize margin mode for Bitget mix orders.

        Bitget expects:
        - crossed
        - isolated

        Our system often uses:
        - cross
        - isolated
        """
        m = str(margin_mode or "").strip().lower()
        if not m:
            return "crossed"
        if m in ("cross", "crossed"):
            return "crossed"
        if m in ("isolated", "iso"):
            return "isolated"
        return "crossed"

    def _sign(self, ts_ms: str, method: str, path: str, body: str) -> str:
        prehash = f"{ts_ms}{method.upper()}{path}{body}"
        mac = hmac.new(self.secret_key.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(mac).decode("utf-8")

    def _headers(self, ts_ms: str, sign: str, request_path: str = "") -> Dict[str, str]:
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts_ms,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.simulated_trading:
            headers["PAPTRADING"] = "1"
        clean_path = str(request_path or "").split("?", 1)[0]
        if self.channel_api_code and clean_path in self._CHANNEL_API_CODE_ORDER_PATHS:
            headers["X-CHANNEL-API-CODE"] = self.channel_api_code
        return headers

    def _signed_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Bitget signature is computed over (timestamp + method + request_path + body).

        - Use `data=<serialized_json>` to ensure the signed body matches the sent body.
        - For GET params, include query string into the signed request path.
        """
        ts_ms = str(int(time.time() * 1000))
        body_str = self._json_dumps(json_body) if json_body is not None else ""

        qs = ""
        request_params: Optional[Dict[str, str]] = None
        if params:
            norm = {str(k): "" if v is None else str(v) for k, v in dict(params).items()}
            request_params = dict(sorted(norm.items()))
            qs = urlencode(list(request_params.items()), doseq=True)
        signed_path = f"{path}?{qs}" if qs else path

        sign = self._sign(ts_ms, method, signed_path, body_str)
        code, data, text = self._request(
            method,
            path,
            params=request_params,
            data=body_str if body_str else None,
            headers=self._headers(ts_ms, sign, path),
        )
        if code >= 400:
            raise LiveTradingError(f"Bitget HTTP {code}: {text[:500]}")
        if isinstance(data, dict):
            # Bitget uses code == "00000" for success in many endpoints.
            c = str(data.get("code") or "")
            if c and c not in ("00000", "0"):
                raise LiveTradingError(f"Bitget error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def get_funding_payments(self, *, symbol: str, start_time_ms: int, end_time_ms: int, limit: int = 100):
        funding_types = (
            "CONTRACT_MAIN_SETTLE_FEE_USER_IN",
            "CONTRACT_MAIN_SETTLE_FEE_USER_OUT",
            "MARGIN_SETTLE_FEE_USER_IN",
            "MARGIN_SETTLE_FEE_USER_OUT",
        )
        rows = []
        for record_type in funding_types:
            raw = self._signed_request(
                "GET",
                "/api/v3/account/financial-records",
                params={
                    "type": record_type,
                    "startTime": int(start_time_ms),
                    "endTime": int(end_time_ms),
                    "limit": min(100, max(1, int(limit or 100))),
                },
            )
            data = raw.get("data") or {}
            if isinstance(data, dict):
                records = data.get("list")
                if not isinstance(records, list):
                    records = data.get("bills")
                if isinstance(records, list):
                    rows.extend(records)
        out = []
        seen = set()
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            amount = float(item.get("amount") or item.get("balanceChange") or 0.0)
            record_id = str(item.get("id") or item.get("billId") or "")
            record_time = int(item.get("ts") or item.get("cTime") or 0)
            dedupe_key = record_id or f"{record_time}:{amount}:{item.get('symbol') or symbol}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append({
                "id": record_id or f"{record_time}:{amount}",
                "symbol": str(item.get("symbol") or symbol), "amount": amount,
                "asset": str(item.get("coin") or "USDT").upper(), "time": record_time,
                "raw": item,
            })
        return out

    def _post_mix_place_order(
        self,
        body: Dict[str, Any],
        *,
        original_side: str,
        reduce_only: bool,
    ) -> Dict[str, Any]:
        del original_side, reduce_only
        ws_result = submit_bitget_order(
            api_key=self.api_key,
            secret_key=self.secret_key,
            passphrase=self.passphrase,
            body=body,
            simulated=self.simulated_trading,
            api_code=self.channel_api_code,
        )
        if ws_result is not None:
            return ws_result
        return self._signed_request("POST", "/api/v3/trade/place-order", json_body=body)

    def _public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        code, data, text = self._request(method, path, params=params, headers=None, json_body=None, data=None)
        if code >= 400:
            raise LiveTradingError(f"Bitget HTTP {code}: {text[:500]}")
        if isinstance(data, dict):
            c = str(data.get("code") or "")
            if c and c not in ("00000", "0"):
                raise LiveTradingError(f"Bitget error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def get_ticker(self, *, symbol: str, **kwargs: Any) -> Dict[str, Any]:
        """
        Public mix ticker (for USDT-notional -> base size conversion in quick trade).

        Endpoint: GET /api/v3/market/tickers
        """
        sym = to_bitget_um_symbol(symbol)
        pt = str(kwargs.get("product_type") or "USDT-FUTURES")
        if not sym:
            return {}
        try:
            raw = self._public_request(
                "GET",
                "/api/v3/market/tickers",
                params={"symbol": sym, "category": pt},
            )
        except Exception:
            return {}
        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return {}
        try:
            last = float(
                data.get("lastPrice")
                or data.get("lastPr")
                or data.get("last")
                or data.get("close")
                or data.get("markPrice")
                or data.get("indexPrice")
                or 0
            )
        except Exception:
            last = 0.0
        if last <= 0:
            return {}
        return {"last": last, "price": last, "close": last}

    def get_account_pos_mode(
        self,
        *,
        symbol: str,
        margin_coin: str = "USDT",
        product_type: str = "USDT-FUTURES",
    ) -> str:
        """
        Returns Bitget posMode for the contract account: 'hedge_mode', 'one_way_mode', or '' if unknown.

        GET /api/v3/account/settings
        """
        sym = to_bitget_um_symbol(symbol)
        if not sym:
            return ""
        mc = (margin_coin or "USDT").strip().upper()
        pt = str(product_type or "USDT-FUTURES")
        key = f"{pt}:{sym}:{mc}"
        shared_key = (self.base_url, self.api_key, self.simulated_trading, key)
        now = time.time()
        with self._shared_pos_mode_cache_lock:
            shared_cached = self._shared_pos_mode_cache.get(shared_key)
        if shared_cached:
            ts, mode = shared_cached
            if (now - float(ts or 0.0)) <= float(self._pos_mode_cache_ttl_sec or 60.0):
                self._pos_mode_cache[key] = shared_cached
                return str(mode)

        cached = self._pos_mode_cache.get(key)
        if cached:
            ts, mode = cached
            if (now - float(ts or 0.0)) <= float(self._pos_mode_cache_ttl_sec or 60.0) and mode is not None:
                return str(mode)

        try:
            resp = self._signed_request(
                "GET",
                "/api/v3/account/settings",
            )
            d = resp.get("data") if isinstance(resp, dict) else None
            mode = ""
            if isinstance(d, dict):
                mode = str(d.get("holdMode") or "").strip().lower()
            self._pos_mode_cache[key] = (now, mode)
            with self._shared_pos_mode_cache_lock:
                self._shared_pos_mode_cache[shared_key] = (now, mode)
            return mode
        except Exception:
            return ""

    def _mix_order_position_fields(
        self,
        *,
        symbol: str,
        side: str,
        reduce_only: bool,
        margin_coin: str,
        product_type: str,
        hold_side: str = "",
    ) -> Dict[str, Any]:
        """
        Build UTA V3 position fields for one-way or hedge mode.
        """
        sd = (side or "").lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        pos_mode = self.get_account_pos_mode(
            symbol=symbol, margin_coin=margin_coin, product_type=product_type
        )
        hedge = pos_mode == "hedge_mode"
        if hedge:
            hs = str(hold_side or "").strip().lower()
            if hs in ("long", "short"):
                direction_side = "buy" if hs == "long" else "sell"
            else:
                # The shared signal uses the physical order side. Bitget hedge
                # mode instead expects the position direction on closes.
                direction_side = (
                    ("sell" if sd == "buy" else "buy") if reduce_only else sd
                )
            out: Dict[str, Any] = {
                "side": sd,
                "posSide": "long" if direction_side == "buy" else "short",
            }
            return out
        return {"side": sd, "reduceOnly": "yes" if reduce_only else "no"}

    def get_contract(self, *, symbol: str, product_type: str = "USDT-FUTURES") -> Dict[str, Any]:
        """
        Fetch contract metadata (best-effort) from public endpoint.

        Endpoint: GET /api/v3/market/instruments
        """
        sym = to_bitget_um_symbol(symbol)
        pt = str(product_type or "USDT-FUTURES")
        if not sym:
            return {}
        key = f"{pt}:{sym}"
        shared_key = (self.base_url, pt, sym)
        now = time.time()
        with self._shared_contract_cache_lock:
            shared_cached = self._shared_contract_cache.get(shared_key)
        if shared_cached:
            ts, obj = shared_cached
            if obj and (now - float(ts or 0.0)) <= float(self._contract_cache_ttl_sec or 300.0):
                self._contract_cache[key] = shared_cached
                return obj

        cached = self._contract_cache.get(key)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._contract_cache_ttl_sec or 300.0):
                return obj

        raw = self._public_request(
            "GET",
            "/api/v3/market/instruments",
            params={"category": pt, "symbol": sym},
        )
        data = raw.get("data") if isinstance(raw, dict) else None
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        first: Dict[str, Any] = items[0] if isinstance(items, list) and items else {}
        if isinstance(first, dict) and first:
            self._contract_cache[key] = (now, first)
            with self._shared_contract_cache_lock:
                self._shared_contract_cache[shared_key] = (now, first)
        return first if isinstance(first, dict) else {}

    def prepare_ws_order_entry(self, *, symbol: str, market_type: str) -> None:
        """Prime stable order metadata before latency-sensitive submission."""
        if str(market_type or "swap").strip().lower() == "spot":
            return
        product_type = "USDT-FUTURES"
        self.get_contract(symbol=symbol, product_type=product_type)
        self.get_account_pos_mode(symbol=symbol, product_type=product_type)

    def _normalize_size(self, *, symbol: str, product_type: str, base_size: float) -> Tuple[Decimal, Optional[int]]:
        """
        Normalize a Bitget UTA order quantity in base-asset units.

        UTA v3 accepts ``qty`` directly and publishes its increment through
        ``quantityMultiplier`` (or ``quantityPrecision`` as a fallback).
        
        Returns:
            Tuple of (normalized_size, precision) where precision is the number of decimal places required.
        """
        req_base = self._to_dec(base_size)
        if req_base <= 0:
            return (Decimal("0"), None)

        contract: Dict[str, Any] = {}
        try:
            contract = self.get_contract(symbol=symbol, product_type=product_type) or {}
        except Exception:
            contract = {}

        qty = req_base

        # Determine step size.
        step = self._to_dec(
            contract.get("quantityMultiplier")
            or contract.get("sizeMultiplier")
            or "0"
        )
        size_precision = None
        if step <= 0:
            sp = contract.get("quantityPrecision")
            try:
                places = int(sp) if sp is not None else 0
            except Exception:
                places = 0
            if places >= 0 and places <= 18:
                step = Decimal("1") / (Decimal("10") ** Decimal(str(places)))
                size_precision = places

        if step > 0:
            qty = self._floor_to_step(qty, step)
            # Infer precision from step if not already set
            if size_precision is None:
                try:
                    exp = step.normalize().as_tuple().exponent
                    if isinstance(exp, int):
                        size_precision = min(max(0, -exp), 18)
                except Exception:
                    pass

        # Enforce min trade number if present.
        mn = self._to_dec(contract.get("minOrderQty") or "0")
        if mn > 0 and qty < mn:
            return (Decimal("0"), size_precision)
        return (qty, size_precision)

    def normalize_base_order_size(
        self,
        *,
        symbol: str,
        product_type: str = "USDT-FUTURES",
        base_size: float,
    ) -> float:
        """Return the base-asset quantity Bitget UTA can submit."""
        normalized_size, _precision = self._normalize_size(
            symbol=symbol,
            product_type=product_type,
            base_size=base_size,
        )
        if normalized_size <= 0:
            return 0.0
        return float(normalized_size)

    def _normalize_price(self, *, symbol: str, product_type: str, price: float) -> Tuple[Decimal, Optional[int]]:
        """
        Normalize Bitget mix limit price using contract metadata (best-effort).

        Bitget commonly exposes:
        - pricePlace: max decimals
        - priceEndStep: integer step within that precision

        Example:
        - pricePlace=2, priceEndStep=1 => price step = 0.01
        - pricePlace=1, priceEndStep=5 => price step = 0.5
        """
        px = self._to_dec(price)
        if px <= 0:
            return (Decimal("0"), None)

        contract: Dict[str, Any] = {}
        try:
            contract = self.get_contract(symbol=symbol, product_type=product_type) or {}
        except Exception:
            contract = {}

        price_precision = None
        step = Decimal("0")

        pp = contract.get("pricePrecision")
        pes = contract.get("priceMultiplier")
        try:
            places = int(pp) if pp is not None else None
        except Exception:
            places = None
        try:
            end_step = self._to_dec(pes if pes is not None else "0")
        except Exception:
            end_step = Decimal("0")

        if places is not None and 0 <= places <= 18:
            price_precision = places
            if end_step > 0:
                step = end_step
            else:
                step = Decimal("1").scaleb(-places)

        if step <= 0:
            step = self._to_dec(
                contract.get("priceStep")
                or contract.get("priceMultiplier")
                or contract.get("tickSize")
                or "0"
            )

        if step > 0:
            px = self._floor_to_step(px, step)
            if price_precision is None:
                try:
                    exp = step.normalize().as_tuple().exponent
                    if isinstance(exp, int):
                        price_precision = min(max(0, -exp), 18)
                except Exception:
                    pass

        min_px = self._to_dec(contract.get("minPrice") or "0")
        if min_px > 0 and px < min_px:
            return (Decimal("0"), price_precision)
        return (px, price_precision)

    def ping(self) -> bool:
        code, data, _ = self._request("GET", "/api/v3/market/time")
        return code == 200 and isinstance(data, dict)

    def get_accounts(self, *, product_type: str = "USDT-FUTURES") -> Dict[str, Any]:
        """
        Private endpoint to validate credentials (best-effort).
        """
        del product_type
        resp = self._signed_request("GET", "/api/v3/account/assets")
        if not isinstance(resp, dict):
            return resp
        data = resp.get("data")
        assets = data.get("assets") if isinstance(data, dict) else None
        if not isinstance(assets, list):
            return resp
        if not assets:
            settings = self._signed_request("GET", "/api/v3/account/settings")
            settings_data = settings.get("data") if isinstance(settings, dict) else None
            account_mode = str(
                settings_data.get("accountMode") if isinstance(settings_data, dict) else ""
            ).strip().lower()
            if account_mode in ("hybrid", "switching", "upgrading"):
                raise LiveTradingError("BITGET_UTA_HYBRID_BALANCE_UNAVAILABLE")
        out = dict(resp)
        out["data"] = assets
        return out

    def get_positions(self, *, product_type: str = "USDT-FUTURES", symbol: str = "") -> Dict[str, Any]:
        """
        Get positions (best-effort).

        Endpoint: GET /api/v3/position/current-position
        When ``symbol`` is set (e.g. ETH/USDT), filters the response list to that contract only.
        """
        resp = self._signed_request(
            "GET",
            "/api/v3/position/current-position",
            params={"category": str(product_type or "USDT-FUTURES")},
        )
        if not isinstance(resp, dict):
            return resp
        data = resp.get("data")
        rows = data.get("list") if isinstance(data, dict) else data
        rows = rows if isinstance(rows, list) else []
        want = (symbol or "").strip()
        if want:
            sym_key = to_bitget_um_symbol(want).upper()
            rows = [
                p for p in rows
                if isinstance(p, dict)
                and str(p.get("symbol") or "").strip().upper() == sym_key
            ]
        out = dict(resp)
        out["data"] = rows
        return out

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        mt = str(market_type or "swap").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual"):
            mt = "swap"
        sym = to_bitget_um_symbol(symbol)
        category = "SPOT" if mt == "spot" else "USDT-FUTURES"
        try:
            raw = self._signed_request(
                "GET",
                "/api/v3/account/fee-rate",
                params={"symbol": sym, "category": category},
            )
            data = raw.get("data") if isinstance(raw, dict) else None
            if isinstance(data, dict):
                maker = abs(float(data.get("makerFeeRate") or 0))
                taker = abs(float(data.get("takerFeeRate") or 0))
                if maker > 0 or taker > 0:
                    return {"maker": maker, "taker": taker}
        except Exception as e:
            logger.warning(
                "Bitget UTA get_fee_rate(%s, category=%s, symbol=%s) failed: %s",
                symbol,
                category,
                sym,
                e,
            )
        return None

    def set_leverage(
        self,
        *,
        symbol: str,
        leverage: float,
        margin_coin: str = "USDT",
        product_type: str = "USDT-FUTURES",
        margin_mode: str = "crossed",
        hold_side: str = "",
    ) -> bool:
        """
        Best-effort set leverage for Bitget mix.

        NOTE: Bitget requires leverage configured via a private endpoint; order placement may otherwise use defaults.
        Endpoint: POST /api/v3/account/set-leverage.
        """
        sym = to_bitget_um_symbol(symbol)
        pt = str(product_type or "USDT-FUTURES")
        mc = str(margin_coin or "USDT")
        mm = self._normalize_margin_mode(margin_mode)
        hs = str(hold_side or "").strip().lower()
        try:
            lv = int(float(leverage or 0))
        except Exception:
            lv = 0
        if not sym or lv <= 0:
            return False
        try:
            contract = self.get_contract(symbol=symbol, product_type=pt) or {}
        except Exception:
            contract = {}
        max_raw = (
            contract.get("maxLever")
            or contract.get("maxLeverage")
            or contract.get("maxLeverRate")
        )
        try:
            max_leverage = int(float(max_raw or 0))
        except (TypeError, ValueError):
            max_leverage = 0
        if max_leverage > 0 and lv > max_leverage:
            raise LiveTradingError(
                f"Bitget leverage {lv}x exceeds the current {sym} maximum {max_leverage}x"
            )

        cache_key = f"{pt}:{sym}:{mc}:{mm}:{hs}:{lv}"
        now = time.time()
        cached = self._lev_cache.get(cache_key)
        if cached:
            ts, ok = cached
            if ok and (now - float(ts or 0.0)) <= float(self._lev_cache_ttl_sec or 60.0):
                return True

        body: Dict[str, Any] = {
            "symbol": sym,
            "category": pt,
            "marginMode": mm,
            "leverage": str(lv),
        }
        if hs in ("long", "short"):
            body["posSide"] = hs

        try:
            resp = self._signed_request(
                "POST",
                "/api/v3/account/set-leverage",
                json_body=body,
            )
            ok = isinstance(resp, dict) and str(resp.get("code") or "") in ("00000", "0", "")
            data = resp.get("data") if isinstance(resp, dict) else None
            if ok and isinstance(data, dict):
                candidate_keys = (
                    ("longLeverage", "shortLeverage")
                    if hs not in ("long", "short")
                    else (f"{hs}Leverage", "leverage")
                )
                effective_values = []
                for key in candidate_keys:
                    if data.get(key) not in (None, ""):
                        try:
                            effective_values.append(int(float(data.get(key))))
                        except (TypeError, ValueError) as exc:
                            raise LiveTradingError(
                                f"Bitget returned an invalid effective leverage: {data.get(key)}"
                            ) from exc
                if effective_values and any(value != lv for value in effective_values):
                    raise LiveTradingError(
                        f"Bitget applied {effective_values} instead of requested {lv}x leverage"
                    )
            if ok:
                self._lev_cache[cache_key] = (now, True)
            return bool(ok)
        except LiveTradingError:
            raise
        except Exception:
            return False

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        margin_coin: str = "USDT",
        product_type: str = "USDT-FUTURES",
        margin_mode: str = "crossed",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
        hold_side: str = "",
    ) -> LiveOrderResult:
        sym = to_bitget_um_symbol(symbol)
        sd = (side or "").lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        req = float(size or 0.0)
        sz_dec, sz_precision = self._normalize_size(symbol=symbol, product_type=product_type, base_size=req)
        if float(sz_dec or 0) <= 0:
            raise LiveTradingError(
                f"Invalid size (below step/min): requested={format_decimal(req)}"
            )

        body: Dict[str, Any] = {
            "symbol": sym,
            "category": str(product_type or "USDT-FUTURES"),
            "marginMode": self._normalize_margin_mode(margin_mode),
            "orderType": "market",
            "qty": self._dec_str(sz_dec, strict_precision=sz_precision),
        }
        body.update(
            self._mix_order_position_fields(
                symbol=symbol,
                side=sd,
                reduce_only=reduce_only,
                margin_coin=str(margin_coin or "USDT"),
                product_type=str(product_type or "USDT-FUTURES"),
                hold_side=hold_side,
            )
        )
        if client_order_id:
            body["clientOid"] = str(client_order_id)

        raw = self._post_mix_place_order(body, original_side=sd, reduce_only=reduce_only)
        data = raw.get("data") if isinstance(raw, dict) else None
        exchange_order_id = ""
        if isinstance(data, dict):
            exchange_order_id = str(data.get("orderId") or data.get("clientOid") or "")

        return LiveOrderResult(
            exchange_id="bitget",
            exchange_order_id=exchange_order_id,
            filled=0.0,
            avg_price=0.0,
            raw=raw,
        )

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        price: float,
        margin_coin: str = "USDT",
        product_type: str = "USDT-FUTURES",
        margin_mode: str = "crossed",
        reduce_only: bool = False,
        post_only: bool = False,
        client_order_id: Optional[str] = None,
        hold_side: str = "",
    ) -> LiveOrderResult:
        sym = to_bitget_um_symbol(symbol)
        sd = (side or "").lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        req = float(size or 0.0)
        px = float(price or 0.0)
        if req <= 0 or px <= 0:
            raise LiveTradingError("Invalid size/price")
        sz_dec, sz_precision = self._normalize_size(symbol=symbol, product_type=product_type, base_size=req)
        if float(sz_dec or 0) <= 0:
            raise LiveTradingError(
                f"Invalid size (below step/min): requested={format_decimal(req)}"
            )
        px_dec, px_precision = self._normalize_price(symbol=symbol, product_type=product_type, price=px)
        if float(px_dec or 0) <= 0:
            raise LiveTradingError(
                f"Invalid price (below step/min): requested={format_decimal(px)}"
            )

        body: Dict[str, Any] = {
            "symbol": sym,
            "category": str(product_type or "USDT-FUTURES"),
            "marginMode": self._normalize_margin_mode(margin_mode),
            "orderType": "limit",
            "price": self._dec_str(px_dec, strict_precision=px_precision),
            "qty": self._dec_str(sz_dec, strict_precision=sz_precision),
        }
        body.update(
            self._mix_order_position_fields(
                symbol=symbol,
                side=sd,
                reduce_only=reduce_only,
                margin_coin=str(margin_coin or "USDT"),
                product_type=str(product_type or "USDT-FUTURES"),
                hold_side=hold_side,
            )
        )
        # Force maker behavior when requested (avoid taker fills).
        if post_only:
            body["timeInForce"] = "post_only"
        else:
            body["timeInForce"] = "gtc"
        if client_order_id:
            body["clientOid"] = str(client_order_id)
        raw = self._post_mix_place_order(body, original_side=sd, reduce_only=reduce_only)
        data = raw.get("data") if isinstance(raw, dict) else None
        exchange_order_id = str(data.get("orderId") or data.get("clientOid") or "") if isinstance(data, dict) else ""
        return LiveOrderResult(exchange_id="bitget", exchange_order_id=exchange_order_id, filled=0.0, avg_price=0.0, raw=raw)

    def cancel_order(self, *, symbol: str, product_type: str, margin_coin: str = "USDT", order_id: str = "", client_oid: str = "") -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "symbol": to_bitget_um_symbol(symbol),
            "category": str(product_type or "USDT-FUTURES"),
        }
        if order_id:
            body["orderId"] = str(order_id)
        elif client_oid:
            body["clientOid"] = str(client_oid)
        else:
            raise LiveTradingError("Bitget cancel_order requires order_id or client_oid")
        return self._signed_request("POST", "/api/v3/trade/cancel-order", json_body=body)

    def get_order_detail(
        self,
        *,
        symbol: str,
        product_type: str,
        order_id: str = "",
        client_oid: str = "",
    ) -> Dict[str, Any]:
        del symbol, product_type
        params: Dict[str, Any] = {}
        if order_id:
            params["orderId"] = str(order_id)
        elif client_oid:
            params["clientOid"] = str(client_oid)
        else:
            raise LiveTradingError("Bitget get_order_detail requires order_id or client_oid")
        raw = self._signed_request("GET", "/api/v3/trade/order-info", params=params)
        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, dict):
            row = dict(data)
            row.setdefault("baseVolume", row.get("cumExecQty"))
            row.setdefault("filledQty", row.get("cumExecQty"))
            row.setdefault("priceAvg", row.get("avgPrice"))
            row.setdefault("state", row.get("orderStatus"))
            return {**raw, "data": row}
        return raw

    def get_order_fills(
        self,
        *,
        symbol: str,
        product_type: str,
        order_id: str,
    ) -> Dict[str, Any]:
        del symbol
        params: Dict[str, Any] = {
            "orderId": str(order_id),
            "category": str(product_type or "USDT-FUTURES"),
        }
        rows = []
        for _ in range(10):
            raw = self._signed_request("GET", "/api/v3/trade/fills", params={**params, "limit": 100})
            data = raw.get('data') or {}
            page = data.get('list') or []
            normalized = []
            for item in page:
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                row.setdefault("baseVolume", row.get("execQty"))
                row.setdefault("fillPrice", row.get("execPrice"))
                row.setdefault("tradeId", row.get("execId"))
                row.setdefault("cTime", row.get("createdTime"))
                normalized.append(row)
            page = normalized
            rows.extend(page)
            if len(page) < 100:
                return {**raw, 'data': {**data, 'fillList': rows}}
            cursor = str(data.get('cursor') or page[-1].get('tradeId') or '')
            if not cursor or cursor == params.get('cursor'):
                break
            params['cursor'] = cursor
        raise LiveTradingError('strategyRuntime.fillSnapshotNotReady')

    def get_order(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
        product_type: str = "USDT-FUTURES",
    ) -> Dict[str, Any]:
        """Normalized order snapshot for grid fill polling (base qty + avg price)."""
        raw = self.get_order_detail(
            symbol=symbol,
            product_type=product_type,
            order_id=str(order_id or ""),
            client_oid=str(client_order_id or ""),
        )
        row = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(row, dict):
            row = {}
        filled = float(row.get("baseVolume") or row.get("filledQty") or 0)
        avg = float(row.get("priceAvg") or row.get("fillPrice") or 0)
        state = str(row.get("state") or row.get("status") or "").lower()
        return {
            "filled": filled,
            "filledSize": filled,
            "executedQty": filled,
            "avg_price": avg,
            "avgPrice": avg,
            "status": state,
            "state": state,
            "raw": raw,
        }

    def wait_for_fill(
        self,
        *,
        symbol: str,
        product_type: str = "USDT-FUTURES",
        order_id: str,
        client_oid: str = "",
        max_wait_sec: float = 3.0,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Poll order fills/detail to obtain (best-effort) executed size and average price.

        Returns:
        {
          "filled": float,
          "avg_price": float,
          "fee": float,
          "fee_ccy": str,
          "state": str,
          "detail": {...},
          "fills": {...}
        }
        """
        end_ts = time.time() + float(max_wait_sec or 0.0)
        settlement_currency = _settlement_currency(symbol, product_type)
        last_detail: Dict[str, Any] = {}
        last_fills: Dict[str, Any] = {}
        state = ""

        def _fee_from_order_detail_row(drow: Dict[str, Any]) -> Tuple[Decimal, str]:
            """Read a Bitget UTA order-level fee using the v3 cost convention."""
            if not isinstance(drow, dict):
                return Decimal("0"), ""
            fv = drow.get("fee")
            if fv is None:
                fv = drow.get("totalFee") or drow.get("deductFee") or drow.get("fillFee") or drow.get("cumExecFee")
            ccy = str(
                drow.get("feeCoin")
                or drow.get("feeCcy")
                or drow.get("fillFeeCoin")
                or drow.get("deductFeeCoin")
                or ""
            ).strip()
            # feeDetail may be a list, mapping, or JSON string.
            if fv is None or str(fv).strip() in ("", "0", "0.0"):
                fd_fee, fd_ccy = self._parse_fee_detail(
                    drow.get("feeDetail"),
                    received_currency=settlement_currency,
                )
                if fd_ccy:
                    logger.debug("Bitget order detail fee via feeDetail: %.8f %s", fd_fee, fd_ccy)
                    return fd_fee, fd_ccy or ccy
            fee = self._to_dec(fv or "0")
            return fee, ccy

        while True:
            now = time.time()
            timed_out = now >= end_ts

            # Prefer fills endpoint (has per-fill fee); detail often appears before fillList is populated.
            try:
                last_fills = self.get_order_fills(symbol=symbol, product_type=product_type, order_id=str(order_id))
                data = last_fills.get("data") if isinstance(last_fills, dict) else None
                fill_list = []
                if isinstance(data, dict):
                    fill_list = data.get("fillList") or data.get("fills") or []
                total_base = Decimal("0")
                total_quote = Decimal("0")
                total_fee = Decimal("0")
                fee_ccy = ""
                fees_by_ccy: Dict[str, float] = {}
                if isinstance(fill_list, list):
                    for f in fill_list:
                        try:
                            sz_base = self._to_dec(f.get("baseVolume") or "0")
                            px = self._to_dec(f.get("fillPrice") or f.get("price") or "0")

                            if sz_base > 0 and px > 0:
                                total_base += sz_base
                                total_quote += sz_base * px
                            from app.services.live_trading.bitget_fees import fee_breakdown
                            native = fee_breakdown(
                                f.get("feeDetail"),
                                received_currency=settlement_currency,
                                v3_positive_cost=True,
                            )
                            if not native and f.get("fee") is not None:
                                native = fee_breakdown(
                                    {"fee": f["fee"], "feeCoin": f.get("feeCoin") or f.get("feeCcy")},
                                    v3_positive_cost=True,
                                )
                            for currency, amount in native.items():
                                fees_by_ccy[currency] = fees_by_ccy.get(currency, 0.0) + amount
                            total_fee = sum(fees_by_ccy.values()) if len(fees_by_ccy) == 1 else 0
                            fee_ccy = next(iter(fees_by_ccy)) if len(fees_by_ccy) == 1 else "MIXED" if fees_by_ccy else ""

                        except Exception:
                            continue
                if total_base > 0 and total_quote > 0:
                    if not fees_by_ccy and not timed_out:
                        time.sleep(float(poll_interval_sec or 0.5))
                        continue
                    logger.debug(
                        "Bitget Mix fill result: filled=%s avg=%.8f fee=%.8f %s (order=%s)",
                        total_base, float(total_quote / total_base), float(total_fee), fee_ccy, order_id,
                    )
                    return {
                        "filled": float(total_base),
                        "avg_price": float(total_quote / total_base),
                        "fee": float(total_fee),
                        "fee_ccy": str(fee_ccy or ""),
                        "fees_by_ccy": fees_by_ccy,
                        "state": state,
                        "detail": last_detail,
                        "fills": last_fills,
                    }
            except ExchangeRateLimitError:
                timed_out = True
            except Exception:
                pass

            # Order detail: volume/avg often ready before fills API lists fees — do not return immediately
            # or commission stays 0 in qd_strategy_trades (seen on Bitget USDT-FUTURES).
            try:
                last_detail = self.get_order_detail(
                    symbol=symbol,
                    product_type=product_type,
                    order_id=str(order_id or ""),
                    client_oid=str(client_oid or ""),
                )
                d = last_detail.get("data") if isinstance(last_detail, dict) else None
                if isinstance(d, dict):
                    state = str(d.get("state") or d.get("status") or "")
                    avg = float(d.get("priceAvg") or d.get("fillPrice") or 0.0) if (d.get("priceAvg") or d.get("fillPrice")) else 0.0
                    filled = float(d.get("baseVolume") or d.get("filledQty") or 0.0) if (d.get("baseVolume") or d.get("filledQty")) else 0.0
                    dfee, dccy = _fee_from_order_detail_row(d)
                    from app.services.live_trading.bitget_fees import fee_breakdown
                    detail_fees = fee_breakdown(
                        d.get("feeDetail"),
                        received_currency=settlement_currency,
                        v3_positive_cost=True,
                    )
                    if not detail_fees and dccy and dccy != "MIXED":
                        detail_fees = {str(dccy).upper(): float(dfee)}
                    abs_fee = dfee

                    if filled > 0 and avg > 0:
                        if not timed_out and abs_fee == 0:
                            time.sleep(float(poll_interval_sec or 0.5))
                            continue
                        logger.debug(
                            "Bitget Mix detail result: filled=%.8f avg=%.8f fee=%.8f %s (order=%s, via=detail)",
                            filled, avg, float(abs_fee), dccy, order_id,
                        )
                        return {
                            "filled": filled,
                            "avg_price": avg,
                            "fee": float(abs_fee),
                            "fee_ccy": str(dccy or ""),
                            "fees_by_ccy": detail_fees,
                            "state": state,
                            "detail": last_detail,
                            "fills": last_fills,
                        }
                    if state in ("filled", "canceled", "cancelled"):
                        if not timed_out and filled > 0 and abs_fee == 0:
                            time.sleep(float(poll_interval_sec or 0.5))
                            continue
                        logger.debug(
                            "Bitget Mix detail result (terminal): filled=%.8f avg=%.8f fee=%.8f %s (order=%s, state=%s)",
                            filled, avg, float(abs_fee), dccy, order_id, state,
                        )
                        return {
                            "filled": filled,
                            "avg_price": avg,
                            "fee": float(abs_fee),
                            "fee_ccy": str(dccy or ""),
                            "fees_by_ccy": detail_fees,
                            "state": state,
                            "detail": last_detail,
                            "fills": last_fills,
                        }
            except Exception:
                pass

            if timed_out:
                d = last_detail.get("data") if isinstance(last_detail, dict) else None
                if isinstance(d, dict):
                    avg = float(d.get("priceAvg") or d.get("fillPrice") or 0.0) if (d.get("priceAvg") or d.get("fillPrice")) else 0.0
                    filled = float(d.get("baseVolume") or d.get("filledQty") or 0.0) if (d.get("baseVolume") or d.get("filledQty")) else 0.0
                    dfee, dccy = _fee_from_order_detail_row(d)
                    st = str(d.get("state") or d.get("status") or state or "")
                    return {
                        "filled": filled,
                        "avg_price": avg,
                        "fee": float(dfee),
                        "fee_ccy": str(dccy or ""),
                        "state": st,
                        "detail": last_detail,
                        "fills": last_fills,
                    }
                return {"filled": 0.0, "avg_price": 0.0, "fee": 0.0, "fee_ccy": "", "state": state, "detail": last_detail, "fills": last_fills}
            time.sleep(float(poll_interval_sec or 0.5))
