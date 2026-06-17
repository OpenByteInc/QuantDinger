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
import json
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.symbols import to_ktx_symbol

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# KTX API constants
# ------------------------------------------------------------------

# positionMerge – 持仓方向（合约必传）
POS_MERGE_LONG = "long"       # 合并多仓：开多 / 平多
POS_MERGE_SHORT = "short"     # 合并空仓：开空 / 平空
POS_MERGE_NONE = "none"       # 分仓（现货默认 / mini合约）

# marginMethod – 保证金模式（合约必传）
MARGIN_CROSS = "cross"        # 全仓模式
MARGIN_ISOLATE = "isolate"    # 逐仓模式

# close – 开平仓标志（合约必传）
CLOSE_OPEN = False            # 开仓
CLOSE_CLOSE = True            # 平仓

# market – 市场类型
MARKET_SPOT = "spot"          # 现货
MARKET_LPC = "lpc"            # U本位永续


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
        leverage: int = 0,
        margin_method: str = "",
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

        # Contract defaults – stored on the client so callers (execution.py,
        # pending_order_worker.py) use the same generic interface as other exchanges.
        self.default_leverage = int(leverage) if leverage and int(leverage) > 0 else 0
        mm = (margin_method or "").strip().lower()
        self.default_margin_method = mm if mm in (MARGIN_CROSS, MARGIN_ISOLATE) else MARGIN_CROSS

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
            body_str = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")) if json_body is not None else ""
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

    def _ktx_market_param(self, market: str = "") -> str:
        return market or (MARKET_SPOT if self.market_type == "spot" else MARKET_LPC)

    def _position_merge(self, side: str) -> str:
        """Return KTX positionMerge value based on market type and side.

        - Spot: always "none"
        - Swap buy (open long): "long"
        - Swap sell (open short): "short"
        """
        if self.market_type == "spot":
            return POS_MERGE_NONE
        return POS_MERGE_LONG if side == "buy" else POS_MERGE_SHORT

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
        # KTX uses quantityScale (int), not amount_scale
        qty_scale_raw = info.get("quantityScale")
        min_base_amount = info.get("min_base_amount") or info.get("minOrderSize")
        # amountIncrement is the step, quantityScale is precision
        step_raw = info.get("quantityIncrement") or info.get("amountIncrement") or "0"
        step = self._to_dec(step_raw) if step_raw else Decimal("0")
        mn = self._to_dec(min_base_amount) if min_base_amount else Decimal("0")

        qty_precision: Optional[int] = None
        if qty_scale_raw is not None:
            qty_precision = int(qty_scale_raw)
            # Also apply floor-to-step when we have an increment
            if step > 0:
                q = self._floor_to_step(q, step)

        if mn > 0 and q < mn:
            # KTX minOrderSize only applies to mini contracts;
            # cross-margin orders can be smaller.  Log a warning
            # but still allow the order through so the exchange can
            # validate server-side.
            import logging
            logging.getLogger(__name__).warning(
                f"qty {q} < minOrderSize {mn} for {symbol}; "
                f"proceeding (exchange will reject if invalid)"
            )

        # KTX also enforces minOrderValue (minimum notional)
        # We cannot check this without a price, so we just note it here.
        # The exchange will reject with -21108 if value < minOrderValue.
        return (q, qty_precision)

    def _normalize_price(self, *, symbol: str, price: float) -> Tuple[Decimal, Optional[int]]:
        p = self._to_dec(price)
        if p <= 0:
            return (Decimal("0"), None)
        info = self._get_product(symbol) or {}
        # KTX uses priceScale (int), not price_scale
        price_scale_raw = info.get("priceScale")
        tick_raw = info.get("priceIncrement") or info.get("price_tick") or "0"
        tick = self._to_dec(tick_raw) if tick_raw else Decimal("0")
        if tick > 0:
            p = self._floor_to_step(p, tick)

        price_precision: Optional[int] = None
        if price_scale_raw is not None:
            price_precision = int(price_scale_raw)

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

    def get_ticker(self, *, symbol: str, market: str = "") -> Dict[str, Any]:
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        j = self._public_request(
            "GET",
            "/v1/ticker",
            params={"symbol": ktx_sym, "market": self._ktx_market_param(market)},
        )
        result = (j.get("result") if isinstance(j, dict) else None) or []
        if isinstance(result, list) and result:
            cand = result[0]
            return cand if isinstance(cand, dict) else {}
        if isinstance(result, dict):
            return result
        return {}

    # ------------------------------------------------------------------
    # K-line (candles)
    # ------------------------------------------------------------------

    # KTX API timeframe → internal timeframe mapping
    _KLINE_TIMEFRAME_MAP: Dict[str, str] = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1H": "1h", "4H": "4h", "1D": "1d", "1W": "1w",
        # CCXT-style aliases
        "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
    }

    def get_kline(
        self,
        *,
        symbol: str,
        timeframe: str = "1H",
        limit: int = 500,
        market: str = "",
    ) -> List[Dict[str, Any]]:
        """Fetch OHLCV candles from KTX public API.

        Endpoint: GET /api/v1/candles?symbol=BTC_USDT_SWAP&market=lpc&time_frame=1h&limit=500

        KTX response format::

            {"result": {"t": 3600000, "e": [[open_time, open, high, low, close,
                                             volume, quote_vol, close_time, count], ...]}}

        Each element in ``e`` is a list of strings (numbers as strings):
        [0] open_time   – ms epoch
        [1] open
        [2] high
        [3] low
        [4] close
        [5] volume      – base currency volume
        [6] quote_volume – quote currency volume
        [7] close_time  – ms epoch
        [8] count       – number of trades

        Args:
            symbol: Trading pair, e.g. "BTC/USDT". Will be converted via ``to_ktx_symbol``.
            timeframe: Candle interval, e.g. "1H", "15m", "1D".
            limit: Max number of candles to return.
            market: Override market param ("spot"/"lpc"). Defaults to client's market_type.

        Returns:
            List of dicts with keys: open_time, open, high, low, close, volume (strings).
            Empty list on failure.
        """
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        tf = self._KLINE_TIMEFRAME_MAP.get(timeframe, "1h")
        params: Dict[str, Any] = {
            "symbol": ktx_sym,
            "market": self._ktx_market_param(market),
            "time_frame": tf,
            "limit": min(limit, 1000),  # KTX server-side cap
        }
        try:
            j = self._public_request("GET", "/v1/candles", params=params)
        except Exception as e:
            logger.warning("KTX get_kline failed for %s %s: %s", symbol, timeframe, e)
            return []

        result = (j.get("result") if isinstance(j, dict) else None) or None
        if not isinstance(result, dict):
            return []

        # KTX returns {"result": {"t": interval_ms, "e": [[...], ...]}}
        entries = result.get("e") or []
        if not isinstance(entries, list):
            return []

        candles: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 6:
                continue
            candles.append({
                "open_time": entry[0],
                "open": entry[1],
                "high": entry[2],
                "low": entry[3],
                "close": entry[4],
                "volume": entry[5],
                "quote_volume": entry[6] if len(entry) > 6 else "",
                "close_time": entry[7] if len(entry) > 7 else "",
                "count": entry[8] if len(entry) > 8 else "",
            })
        return candles

    def get_account(self) -> Dict[str, Any]:
        """Get wallet (main) account assets via POST /v1/main/accounts."""
        return self.get_wallet_balance()

    def get_balance(self, *, asset: str = "") -> Dict[str, Any]:
        """Get trade account assets (futures/spot collateral).

        This reads from /v1/trade/accounts – the account used for actual
        trading – consistent with other exchanges' ``get_balance()`` semantics.
        """
        return self.get_trade_balance(asset=asset)

    def get_trade_balance(self, *, asset: str = "") -> Dict[str, Any]:
        """Get trade account assets (futures/margin/spot collateral) via GET /v1/trade/accounts.

        Args:
            asset: Optional asset code (e.g. "BTC", "USDT").
                   If empty, returns all assets.
        """
        params: Dict[str, Any] = {}
        if asset:
            params["asset"] = asset
        return self._signed_request("GET", "/v1/trade/accounts", params=params if params else None)

    def get_wallet_balance(self, *, asset: str = "") -> Dict[str, Any]:
        """Get wallet (main) account assets via POST /v1/main/accounts.

        This is separate from the trade account. In KTX unified account mode,
        both spot and futures assets live in /v1/trade/accounts.
        The main/wallet account holds assets not yet transferred to the trade account.
        Use ``get_balance()`` (which reads /v1/trade/accounts) for the standard
        trading-balance query.

        Args:
            asset: Optional asset code (e.g. "BTC", "USDT"). If empty, returns all.
        """
        body: Dict[str, Any] = {"asset": asset} if asset else {}
        return self._signed_request("POST", "/v1/main/accounts", json_body=body)

    def get_positions(
        self,
        *,
        position_id: str = "",
        market: str = "",
        symbol: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Get futures positions.

        Args:
            position_id: Specific position ID (highest priority).
            market: Market type, e.g. "lpc" for USDT-M perpetuals.
            symbol: Trading pair, e.g. "BTC_USDT_SWAP". Must be used with market.
        """
        if self.market_type == "spot":
            return []
        params: Dict[str, Any] = {}
        if position_id:
            params["position_id"] = position_id
        if market:
            params["market"] = market
        else:
            # Always filter to lpc market for swap/futures mode (otherwise returns ALL markets)
            params["market"] = MARKET_LPC
        if symbol:
            params["symbol"] = symbol
        j = self._signed_request("GET", "/v1/positions", params=params if params else None)
        result = (j.get("result") if isinstance(j, dict) else None) or []
        if isinstance(result, dict):
            result = [result] if result else []
        # Filter out zero-size position slots (KTX returns closed slots with quantity=0)
        return [r for r in result if isinstance(r, dict) and r.get("quantity", "0") not in ("", "0")]

    # ------------------------------------------------------------------
    # Spot-only methods
    # ------------------------------------------------------------------

    def get_spot_balance(self, *, asset: str = "") -> Dict[str, Any]:
        """Alias for get_balance(). In KTX unified account mode,
        spot assets are in the trade account (/v1/trade/accounts).
        """
        return self.get_balance(asset=asset)

    def spot_transfer(self, *, symbol: str, amount: float, direction: str = "WALLET_TRADE") -> Dict[str, Any]:
        """
        Transfer assets between wallet and trade accounts.


        Args:
            symbol: Asset code (e.g. "USDT", "BTC").
            amount: Transfer amount.
            direction: "WALLET_TRADE" = wallet → trade, "TRADE_WALLET" = trade → wallet.
        """
        if direction not in ("WALLET_TRADE", "TRADE_WALLET"):
            raise LiveTradingError(f"Invalid transfer direction: {direction}")
        body = {
            "symbol": str(symbol or "").strip().upper(),
            "amount": str(amount),
            "type": direction,
        }
        return self._signed_request("POST", "/v1/transfer", json_body=body)

    def get_ledger(
        self,
        *,
        asset: str = "",
        start_time: int = 0,
        end_time: int = 0,
        ledger_type: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get account ledger (bill) records: transfers, trades, fees, funding.

        Args:
            asset: Asset code filter (e.g. "BTC", "USDT").
            start_time: Earliest record timestamp (ms).
            end_time: Latest record timestamp (ms).
            ledger_type: "transfer" | "trade" | "fee" | "rebate" | "funding".
            limit: Max records (default 100).
        """
        params: Dict[str, Any] = {"limit": limit}
        if asset:
            params["asset"] = asset
        if start_time > 0:
            params["start_time"] = start_time
        if end_time > 0:
            params["end_time"] = end_time
        if ledger_type:
            params["type"] = ledger_type
        j = self._signed_request("GET", "/v1/ledgers", params=params)
        result = (j.get("result") if isinstance(j, dict) else None) or []
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def _resolve_position_id(self, symbol: str, pos_side: str) -> str:
        """Best-effort lookup of positionId for close/reduce orders.

        When ``reduce_only`` is True the KTX API requires ``positionId`` so it
        knows *which* position to close.  We query ``get_positions`` for the
        matching symbol and direction.
        """
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        try:
            positions = self.get_positions(symbol=ktx_sym)
        except Exception:
            return ""
        target_side = (pos_side or "").strip().lower()
        for p in positions:
            if not isinstance(p, dict):
                continue
            p_side = str(p.get("side", "")).strip().lower()
            if p_side == target_side:
                pid = str(p.get("id", "")).strip()
                if pid:
                    return pid
        return ""

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
            "quantity": self._dec_str(q_dec, strict_precision=qty_precision),
            "market": self._ktx_market_param(),
            "positionMerge": pos_side if pos_side else self._position_merge(sd),
        }
        if self.market_type == "swap":
            body["marginMethod"] = self.default_margin_method
            body["close"] = CLOSE_CLOSE if reduce_only else CLOSE_OPEN
            if reduce_only:
                # KTX requires positionId for close orders – auto-resolve it.
                resolved_pos = pos_side if pos_side else (POS_MERGE_LONG if sd == "sell" else POS_MERGE_SHORT)
                pid = self._resolve_position_id(symbol, resolved_pos)
                if pid:
                    body["positionId"] = pid
        if self.default_leverage > 0:
            body["leverage"] = self.default_leverage
        if client_order_id:
            body["client_order_id"] = str(client_order_id)

        raw = self._signed_request("POST", "/v1/order", json_body=body)
        res = raw if isinstance(raw, dict) else {}
        result = res.get("result") if isinstance(res.get("result"), dict) else res
        oid = str((result or {}).get("orderId") or (result or {}).get("id") or "")
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
            "quantity": self._dec_str(q_dec, strict_precision=qty_precision),
            "price": self._dec_str(p_dec, strict_precision=price_precision),
            "timeInForce": (time_in_force or "GTC").lower(),
            "market": self._ktx_market_param(),
            "positionMerge": pos_side if pos_side else self._position_merge(sd),
        }
        if self.market_type == "swap":
            body["marginMethod"] = self.default_margin_method
            body["close"] = CLOSE_CLOSE if reduce_only else CLOSE_OPEN
            if reduce_only:
                # KTX requires positionId for close orders – auto-resolve it.
                resolved_pos = pos_side if pos_side else (POS_MERGE_LONG if sd == "sell" else POS_MERGE_SHORT)
                pid = self._resolve_position_id(symbol, resolved_pos)
                if pid:
                    body["positionId"] = pid
        if self.default_leverage > 0:
            body["leverage"] = self.default_leverage
        if client_order_id:
            body["client_order_id"] = str(client_order_id)

        raw = self._signed_request("POST", "/v1/order", json_body=body)
        res = raw if isinstance(raw, dict) else {}
        result = res.get("result") if isinstance(res.get("result"), dict) else res
        oid = str((result or {}).get("orderId") or (result or {}).get("id") or "")
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
        market: str = "",
    ) -> Dict[str, Any]:
        if not order_id and not client_order_id:
            raise LiveTradingError("KTX get_order requires order_id or client_order_id")
        if order_id:
            return self._signed_request("GET", "/v1/order", params={"id": order_id})
        # Query by client_order_id via pending orders scan
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        mkt = self._ktx_market_param(market)
        j = self._signed_request(
            "GET",
            "/v1/pending/orders",
            params={"symbol": ktx_sym, "market": mkt},
        )
        result = (j.get("result") if isinstance(j, dict) else None) or []
        if isinstance(result, list):
            for it in result:
                if isinstance(it, dict) and str(it.get("clientOrderId") or "") == str(client_order_id):
                    return it
        return {}

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
        market: str = "",
    ) -> Dict[str, Any]:
        if not order_id and not client_order_id:
            raise LiveTradingError("KTX cancel_order requires order_id or client_order_id")
        if order_id:
            return self._signed_request("POST", "/v1/order/delete", json_body={"id": order_id, "market": self._ktx_market_param(market)})
        # Resolve client_order_id -> exchange order_id first
        o = self.get_order(symbol=symbol, client_order_id=client_order_id, market=market)
        oid = str(o.get("orderId") or o.get("id") or "") if isinstance(o, dict) else ""
        if not oid:
            raise LiveTradingError(f"KTX cancel: cannot resolve client_order_id={client_order_id}")
        return self._signed_request("POST", "/v1/order/delete", json_body={"id": oid, "market": self._ktx_market_param(market)})

    def get_open_orders(self, *, symbol: str = "", market: str = "") -> List[Dict[str, Any]]:
        """
        Get pending (unfilled) orders. market param is REQUIRED by KTX API.
        """
        params: Dict[str, Any] = {"market": self._ktx_market_param(market)}
        if symbol:
            params["symbol"] = to_ktx_symbol(symbol, market_type=self.market_type)
        j = self._signed_request("GET", "/v1/pending/orders", params=params)
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
                    order.get("executedQty")
                    or order.get("filled_amount")
                    or order.get("deal_amount")
                    or order.get("executed_amount")
                    or order.get("filled")
                    or 0.0
                )
            except Exception:
                filled = 0.0
            try:
                avg_price = float(
                    order.get("executedCost")
                    or order.get("average_price")
                    or order.get("avg_price")
                    or order.get("price_avg")
                    or order.get("deal_avg_price")
                    or 0.0
                )
                # executedCost is total cost, not avg price; derive avg_price from filled
                if avg_price > 0 and filled > 0:
                    # If executedCost looks like total cost (value > price range), convert
                    raw_ep = order.get("average_price") or order.get("avg_price") or order.get("price_avg")
                    if not raw_ep:
                        avg_price = avg_price / filled
            except Exception:
                avg_price = 0.0
            try:
                # KTX fees is a list of dicts with "fee"/"feeCoin" keys
                raw_fees = order.get("fees")
                if isinstance(raw_fees, list) and raw_fees:
                    fee = abs(sum(float(f.get("fee", 0)) for f in raw_fees if isinstance(f, dict)))
                    fee_ccy = str(raw_fees[0].get("feeCoin", "")) if isinstance(raw_fees[0], dict) else ""
                else:
                    fee = abs(
                        float(
                            order.get("fee")
                            or order.get("fee_amount")
                            or order.get("deal_fee")
                            or 0.0
                        )
                    )
                    fee_ccy = str(order.get("fee_currency") or order.get("fee_ccy") or "")
            except Exception:
                fee = 0.0
                fee_ccy = ""
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

    def get_history_orders(
        self,
        *,
        symbol: str = "",
        market: str = "",
        start_time: int = 0,
        end_time: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get settled/filled/cancelled orders (last 3 months).
        market is REQUIRED by KTX API.
        """
        params: Dict[str, Any] = {
            "market": self._ktx_market_param(market),
            "limit": limit,
        }
        if symbol:
            params["symbol"] = to_ktx_symbol(symbol, market_type=self.market_type)
        if start_time > 0:
            params["start_time"] = start_time
        if end_time > 0:
            params["end_time"] = end_time
        j = self._signed_request("GET", "/v1/history/orders", params=params)
        result = (j.get("result") if isinstance(j, dict) else None) or []
        return result if isinstance(result, list) else []

    def set_leverage(self, *, symbol: str, leverage: float, position_id: str = "") -> Dict[str, Any]:
        """
        Set leverage for a KTX futures position.


        Args:
            symbol: Trading pair (e.g. "BTC_USDT_SWAP").
            leverage: Leverage multiplier (e.g. 10 for 10x).
            position_id: Position ID. If empty, will try to set by symbol (best-effort).
        """
        if self.market_type == "spot":
            return {"skipped": True, "reason": "spot"}
        lev = int(float(leverage or 0))
        if lev <= 0:
            return {"skipped": True, "reason": "invalid_leverage"}
        if position_id:
            body = {"positionId": position_id, "leverage": lev}
        else:
            ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
            body = {"positionId": position_id, "leverage": lev, "symbol": ktx_sym, "market": "lpc"}
        try:
            return self._signed_request("POST", "/v1/change/leverage", json_body=body)
        except LiveTradingError as e:
            logger.debug(f"KTX set_leverage failed: {e}")
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
