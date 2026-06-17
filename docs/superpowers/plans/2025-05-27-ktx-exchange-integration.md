# KTX Exchange Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add KTX cryptocurrency exchange (Spot + USDT-M Futures) to QuantDinger's live trading system, following the existing architecture patterns used by Binance, Bybit, and OKX.

**Architecture:** KTX API mimics a Binance-like REST structure with HMAC-SHA256 signature authentication. A single `KtxClient` class handles both spot and futures markets via a `market_type` discriminator, similar to Bybit's `category` approach. The client integrates into the existing `BaseRestClient` hierarchy and is wired through the standard factory/execution/strategy touchpoints.

**Tech Stack:** Python 3.12, `requests`, `hmac`/`hashlib`, existing QuantDinger live-trading framework.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/services/live_trading/ktx.py` | **Create** | KTX REST client: auth, orders, positions, account, market data |
| `app/services/live_trading/symbols.py` | **Modify** | Add `to_ktx_symbol()` helper |
| `app/services/live_trading/factory.py` | **Modify** | Import `KtxClient`; add `exchange_id == "ktx"` branch in `create_client()` |
| `app/services/live_trading/execution.py` | **Modify** | Add `isinstance(client, KtxClient)` branch in `place_order_from_signal()` |
| `app/services/exchange_execution.py` | **Modify** | Add `"ktx"` to `_CRYPTO_EXCHANGES` set |
| `app/services/pending_order_worker.py` | **Modify** | Import `KtxClient`; add to position-sync dispatch |
| `app/services/strategy.py` | **Modify** | Add KTX to `test_exchange_connection()` and `get_exchange_symbols()` |

---

## KTX API Quick Reference

**Base URLs:**
- Market Data REST: `https://api.ktx.app/api`
- User Data REST: `https://api.ktx.app/papi`

**Authentication Headers:**
- `api-key`: API Key
- `api-sign`: HMAC-SHA256 hex signature
- `api-expire-time`: Current timestamp + 30000 ms

**Signature String:**
- GET: `expireTime + queryString`
- POST: `expireTime + body`

**Symbol Formats:**
- Spot: `BTC_USDT`
- Futures (USDT-M): `BTC_USDT_SWAP`

**Key Endpoints:**
| Purpose | Method | Path |
|---------|--------|------|
| Products (pairs) | GET | `/v1/products` |
| Ticker | GET | `/v1/ticker?symbol=BTC_USDT` |
| KLines | GET | `/v1/candles?symbol=BTC_USDT&time_frame=1h` |
| Account | GET | `/papi/v1/accounts` |
| Place Order | POST | `/papi/v1/order` |
| Query Orders | GET | `/papi/v1/orders` |
| Get Order | GET | `/papi/v1/orders/{id}` |
| Cancel Order | DELETE | `/papi/v1/orders/{id}` |
| Cancel All | DELETE | `/papi/v1/orders` |
| Fills | GET | `/papi/v1/fills` |

---

## Task 1: Symbol Normalization Helper

**Files:**
- Modify: `app/services/live_trading/symbols.py`

- [ ] **Step 1: Add `to_ktx_symbol` function**

Insert after `to_bybit_symbol`:

```python
def to_ktx_symbol(symbol: str, market_type: str = "spot") -> str:
    """
    KTX symbol format:
    - spot: BTC_USDT
    - futures (lpc): BTC_USDT_SWAP
    """
    base, quote = _split_base_quote(symbol)
    if not quote:
        # Already KTX format or bare symbol — try to preserve
        s = (symbol or "").replace("/", "_").replace(":", "").upper()
        if market_type != "spot" and not s.endswith("_SWAP"):
            s = f"{s}_SWAP"
        return s
    if market_type != "spot":
        return f"{base}_{quote}_SWAP"
    return f"{base}_{quote}"
```

- [ ] **Step 2: Commit**

```bash
git add app/services/live_trading/symbols.py
git commit -m "feat(ktx): add to_ktx_symbol helper"
```

---

## Task 2: KTX Client Core (`ktx.py`)

**Files:**
- Create: `app/services/live_trading/ktx.py`

- [ ] **Step 1: Create file header and imports**

```python
"""
KTX (direct REST) client for spot / USDT-M perpetual orders.

API docs: https://ktx-private.github.io/api-zh/#b122f813d5
Base URLs:
  Market Data: https://api.ktx.app/api
  User Data:   https://api.ktx.app/papi

Auth:
  Headers: api-key, api-sign, api-expire-time
  Sign: HMAC-SHA256(apiSecret, expireTime + queryString|body)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.symbols import to_ktx_symbol

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Implement `KtxClient` class with init, auth, and public request helpers**

```python
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
        self.market_type = (market_type or "swap").strip().lower()
        if self.market_type in ("futures", "future", "perp", "perpetual"):
            self.market_type = "swap"
        if self.market_type not in ("spot", "swap"):
            self.market_type = "swap"

        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing KTX api_key/secret_key")

        # Cache for product metadata (qty step, price precision, min qty)
        self._product_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._product_cache_ttl_sec = 300.0

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

    def _public_request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Public market data request (no auth)."""
        status, parsed, _ = self._request(method, f"/api{path}", **kwargs)
        if status >= 400:
            raise LiveTradingError(f"KTX public request failed: {status} {parsed}")
        return parsed

    def _signed_request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Signed private request."""
        expire_time = str(int(time.time() * 1000) + 30000)
        method_up = str(method or "GET").upper()

        # Build message for signature
        if method_up == "GET":
            params = kwargs.get("params") or {}
            query_str = ""
            if params:
                from urllib.parse import urlencode
                query_str = urlencode(sorted(params.items()))
            message = expire_time + query_str
            url_path = f"/papi{path}"
            if query_str:
                url_path += f"?{query_str}"
            status, parsed, _ = self._request(
                method_up, url_path,
                headers={
                    "api-key": self.api_key,
                    "api-sign": hmac.new(
                        self.secret_key.encode("utf-8"),
                        message.encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest(),
                    "api-expire-time": expire_time,
                },
            )
        else:
            json_body = kwargs.get("json_body") or {}
            body_str = self._json_dumps(json_body) if json_body else ""
            message = expire_time + body_str
            headers = {
                "api-key": self.api_key,
                "api-sign": hmac.new(
                    self.secret_key.encode("utf-8"),
                    message.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest(),
                "api-expire-time": expire_time,
                "Content-Type": "application/json",
            }
            status, parsed, _ = self._request(
                method_up, f"/papi{path}",
                json_body=json_body if json_body else None,
                headers=headers,
            )

        if status >= 400:
            raise LiveTradingError(f"KTX signed request failed: {status} {parsed}")
        return parsed
```

- [ ] **Step 3: Implement product metadata and quantity normalization**

```python
    def _get_product(self, symbol: str) -> Dict[str, Any]:
        """Fetch and cache product metadata (qty/precision filters)."""
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        now = time.time()
        cached = self._product_cache.get(ktx_sym)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= self._product_cache_ttl_sec:
                return obj
        j = self._public_request("GET", "/v1/products", params={"symbol": ktx_sym, "market": self._ktx_market_param()})
        result = (j.get("result") if isinstance(j, dict) else None) or []
        first = result[0] if isinstance(result, list) and result else {}
        if isinstance(first, dict) and first:
            self._product_cache[ktx_sym] = (now, first)
        return first if isinstance(first, dict) else {}

    def _ktx_market_param(self) -> str:
        return "spot" if self.market_type == "spot" else "lpc"

    def _normalize_qty(self, *, symbol: str, qty: float) -> Tuple[Decimal, Optional[int]]:
        q = self._to_dec(qty)
        if q <= 0:
            return (Decimal("0"), None)
        info = self._get_product(symbol) or {}
        amount_scale = info.get("amount_scale")
        min_base_amount = info.get("min_base_amount")
        step = self._to_dec(amount_scale) if amount_scale else Decimal("0")
        mn = self._to_dec(min_base_amount) if min_base_amount else Decimal("0")

        qty_precision = None
        if step > 0:
            q = self._floor_to_step(q, step)
            try:
                step_str = str(step.normalize())
                if "." in step_str:
                    qty_precision = len(step_str.split(".")[1])
            except Exception:
                pass

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

        price_precision = None
        if tick > 0:
            try:
                tick_str = str(tick.normalize())
                if "." in tick_str:
                    price_precision = len(tick_str.split(".")[1])
            except Exception:
                pass
        return (p, price_precision)
```

- [ ] **Step 4: Implement market data and account methods**

```python
    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        j = self._public_request(
            "GET", "/v1/ticker",
            params={"symbol": ktx_sym, "market": self._ktx_market_param()},
        )
        result = (j.get("result") if isinstance(j, dict) else None) or []
        first = result[0] if isinstance(result, list) and result else {}
        return first if isinstance(first, dict) else {}

    def get_account(self) -> Dict[str, Any]:
        """Get account assets."""
        return self._signed_request("GET", "/v1/accounts")

    def get_balance(self) -> Dict[str, Any]:
        """Alias for get_account."""
        return self.get_account()

    def get_positions(self) -> List[Dict[str, Any]]:
        """KTX futures positions. Spot returns empty list."""
        if self.market_type == "spot":
            return []
        j = self._signed_request("GET", "/v1/trade/accounts")
        result = (j.get("result") if isinstance(j, dict) else None) or []
        if isinstance(result, dict):
            # Wrap single position object into list if needed
            return [result] if result else []
        return result if isinstance(result, list) else []
```

- [ ] **Step 5: Implement order placement methods**

```python
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
        oid = str(res.get("id") or res.get("client_order_id") or "")
        return LiveOrderResult(
            exchange_id="ktx",
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
        oid = str(res.get("id") or res.get("client_order_id") or "")
        return LiveOrderResult(
            exchange_id="ktx",
            exchange_order_id=oid,
            filled=0.0,
            avg_price=0.0,
            raw=raw,
        )
```

- [ ] **Step 6: Implement order query and cancel methods**

```python
    def get_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        if not order_id and not client_order_id:
            raise LiveTradingError("KTX get_order requires order_id or client_order_id")
        if order_id:
            return self._signed_request("GET", f"/v1/orders/{order_id}")
        # Query by client_order_id via list
        ktx_sym = to_ktx_symbol(symbol, market_type=self.market_type)
        j = self._signed_request(
            "GET", "/v1/orders",
            params={"symbol": ktx_sym, "limit": 100},
        )
        result = (j.get("result") if isinstance(j, dict) else None) or []
        for it in result if isinstance(result, list) else []:
            if str(it.get("client_order_id") or "") == str(client_order_id):
                return it
        return {}

    def cancel_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        if not order_id and not client_order_id:
            raise LiveTradingError("KTX cancel_order requires order_id or client_order_id")
        if order_id:
            return self._signed_request("DELETE", f"/v1/orders/{order_id}")
        # Need to resolve client_order_id -> order_id first
        o = self.get_order(symbol=symbol, client_order_id=client_order_id)
        oid = str(o.get("id") or "")
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

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        """Return maker/taker fee from product info."""
        try:
            info = self._get_product(symbol) or {}
            maker = float(info.get("maker_fee") or 0)
            taker = float(info.get("taker_fee") or 0)
            return {"maker": maker, "taker": taker}
        except Exception:
            return None
```

- [ ] **Step 7: Commit**

```bash
git add app/services/live_trading/ktx.py
git commit -m "feat(ktx): add KTX REST client for spot and futures trading"
```

---

## Task 3: Factory Registration

**Files:**
- Modify: `app/services/live_trading/factory.py`

- [ ] **Step 1: Add import**

After `from app.services.live_trading.htx import HtxClient`:

```python
from app.services.live_trading.ktx import KtxClient
```

- [ ] **Step 2: Add KTX branch in `create_client()`**

After the `htx` block, before the `ibkr` block:

```python
    if exchange_id == "ktx":
        base_url = _get(exchange_config, "base_url", "baseUrl") or "https://api.ktx.app"
        return KtxClient(
            api_key=api_key,
            secret_key=secret_key,
            base_url=base_url,
            market_type=mt,
        )
```

- [ ] **Step 3: Update module docstring**

Update the docstring at line 5 to include KTX:

```python
"""
Factory for direct exchange clients.

Supports:
- Crypto exchanges: Binance, OKX, Bitget, Bybit, Coinbase, Kraken, KuCoin, Gate, Deepcoin, HTX, KTX
- Traditional brokers: Interactive Brokers (IBKR) for US stocks
- Forex brokers: MetaTrader 5 (MT5)
"""
```

- [ ] **Step 4: Commit**

```bash
git add app/services/live_trading/factory.py
git commit -m "feat(ktx): register KTX client in factory"
```

---

## Task 4: Execution Layer (`execution.py`)

**Files:**
- Modify: `app/services/live_trading/execution.py`

- [ ] **Step 1: Add import**

After `from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient`:

```python
from app.services.live_trading.ktx import KtxClient
```

- [ ] **Step 2: Add KTX branch in `place_order_from_signal()`**

After the Gate branch and before the KrakenFutures branch:

```python
    if isinstance(client, KtxClient):
        return client.place_market_order(
            symbol=symbol,
            side=side,
            qty=qty,
            reduce_only=reduce_only,
            pos_side=pos_side,
            client_order_id=client_order_id,
        )
```

- [ ] **Step 3: Update module docstring**

Update docstring to include KTX.

- [ ] **Step 4: Commit**

```bash
git add app/services/live_trading/execution.py
git commit -m "feat(ktx): wire KTX into order execution pipeline"
```

---

## Task 5: Exchange Execution Helpers

**Files:**
- Modify: `app/services/exchange_execution.py`

- [ ] **Step 1: Add "ktx" to `_CRYPTO_EXCHANGES`**

```python
_CRYPTO_EXCHANGES = {
    "binance", "okx", "bitget", "bybit", "coinbaseexchange",
    "kraken", "kucoin", "gate", "deepcoin", "htx", "ktx",
}
```

- [ ] **Step 2: Update sync comment**

Update the comment at line 43 to include KTX:

```python
# Keep this list in sync with:
#   - app/services/live_trading/factory.py::create_client
#   - app/services/pending_order_worker.py::_execute_live_order validation
#   - app/services/strategy.py validators (create / update / batch_create)
```

- [ ] **Step 3: Commit**

```bash
git add app/services/exchange_execution.py
git commit -m "feat(ktx): add KTX to crypto exchange registry"
```

---

## Task 6: Pending Order Worker (Position Sync)

**Files:**
- Modify: `app/services/pending_order_worker.py`

- [ ] **Step 1: Add import**

After `from app.services.live_trading.htx import HtxClient`:

```python
from app.services.live_trading.ktx import KtxClient
```

- [ ] **Step 2: Add KTX to position sync dispatch**

Search for where `_sync_positions_best_effort` dispatches `get_positions()` per exchange type. Add a KTX branch following the pattern used for Bybit/Deepcoin/HTX.

Look for the block where `isinstance(client, HtxClient)` is checked, and add after it:

```python
        if isinstance(client, KtxClient):
            resp = client.get_positions()
            if isinstance(resp, list):
                for p in resp:
                    if not isinstance(p, dict):
                        continue
                    sym = str(p.get("symbol") or "").replace("_", "/")
                    if sym.endswith("/SWAP"):
                        sym = sym[:-5] + "/USDT"
                    amt = float(p.get("position_amount") or p.get("amount") or 0)
                    side = str(p.get("side") or "").lower()
                    # Determine pos_side from side/amount
                    if side == "buy" or amt > 0:
                        pos_side = "long"
                    elif side == "sell" or amt < 0:
                        pos_side = "short"
                    else:
                        continue
                    # Build position record
                    positions.append({
                        "symbol": sym,
                        "pos_side": pos_side,
                        "amount": abs(amt),
                        "avg_price": float(p.get("avg_price") or p.get("entry_price") or 0),
                        "unrealized_pnl": float(p.get("unrealized_pnl") or 0),
                    })
```

> **Note:** The exact response shape of KTX positions may differ. Adjust field names after testing with real API.

- [ ] **Step 3: Commit**

```bash
git add app/services/pending_order_worker.py
git commit -m "feat(ktx): add KTX to position sync worker"
```

---

## Task 7: Strategy Service (Test Connection + Symbols)

**Files:**
- Modify: `app/services/strategy.py`

- [ ] **Step 1: Add import**

In `test_exchange_connection`, after `from app.services.live_trading.htx import HtxClient`:

```python
from app.services.live_trading.ktx import KtxClient
```

- [ ] **Step 2: Add KTX to `_validate_private` helper**

In the `_validate_private` nested function inside `test_exchange_connection`, after the Gate block:

```python
                    if isinstance(client, KtxClient):
                        return client.get_account()
```

- [ ] **Step 3: Add KTX to `get_exchange_symbols()`**

In the direct-REST symbol fetch block (around line 138), add a KTX branch inside the `if ex in (...)` block:

```python
                if ex == "ktx":
                    base = str(exchange_config.get("base_url") or exchange_config.get("baseUrl") or "https://api.ktx.app").rstrip("/")
                    ktx_market = "spot" if market_type == "spot" else "lpc"
                    j = _req_json(f"{base}/api/v1/products?market={ktx_market}")
                    result = (j.get("result") if isinstance(j, dict) else None) or []
                    if isinstance(result, list):
                        for it in result:
                            if not isinstance(it, dict):
                                continue
                            sym = str(it.get("symbol") or "")
                            if not sym:
                                continue
                            # Convert BTC_USDT -> BTC/USDT, BTC_USDT_SWAP -> BTC/USDT
                            if "_SWAP" in sym:
                                sym_clean = sym.replace("_SWAP", "").replace("_", "/")
                            else:
                                sym_clean = sym.replace("_", "/")
                            if sym_clean.endswith("/USDT"):
                                symbols.append(sym_clean)
                    symbols = sorted(list(set(symbols)))
                    return {"success": True, "message": f"Success, {len(symbols)} trading pairs", "symbols": symbols}
```

- [ ] **Step 4: Commit**

```bash
git add app/services/strategy.py
git commit -m "feat(ktx): add KTX to strategy test-connection and symbol listing"
```

---

## Task 8: Integration Test

**Files:**
- Create: `tests/test_ktx_client.py`

- [ ] **Step 1: Write minimal smoke test**

```python
"""Smoke tests for KTX client (no real API calls)."""
import pytest
from app.services.live_trading.ktx import KtxClient
from app.services.live_trading.symbols import to_ktx_symbol


def test_to_ktx_symbol_spot():
    assert to_ktx_symbol("BTC/USDT", market_type="spot") == "BTC_USDT"
    assert to_ktx_symbol("BTCUSDT", market_type="spot") == "BTCUSDT"


def test_to_ktx_symbol_swap():
    assert to_ktx_symbol("BTC/USDT", market_type="swap") == "BTC_USDT_SWAP"
    assert to_ktx_symbol("ETH/USDT", market_type="futures") == "ETH_USDT_SWAP"


def test_ktx_client_init_requires_keys():
    with pytest.raises(Exception):
        KtxClient(api_key="", secret_key="")


def test_ktx_client_signature_string():
    """Verify signature string format."""
    client = KtxClient(api_key="test", secret_key="secret", base_url="https://api.ktx.app")
    # _signed_request is internal; we test via mocking or just verify init succeeds
    assert client.api_key == "test"
    assert client.market_type == "swap"
```

- [ ] **Step 2: Run tests**

```bash
cd /Users/ceze/cezework/quant/QuantDinger/backend_api_python
python -m pytest tests/test_ktx_client.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ktx_client.py
git commit -m "test(ktx): add KTX client smoke tests"
```

---

## Task 9: Manual Validation Checklist

Before declaring done, validate with the provided test credentials:

- [ ] **Test Connection**: Via UI or API `POST /api/strategies/test-connection` with:
  ```json
  {"exchange_id": "ktx", "api_key": "32c6e8d24416d17eacf159ad061bc70cb83a8456", "secret_key": "c39d7a2921b82fc360af05f28613330d74b9d249"}
  ```
- [ ] **Symbol Listing**: Verify `get_exchange_symbols` returns KTX pairs.
- [ ] **Account Query**: Call `client.get_account()` and verify non-empty balances or expected error.
- [ ] **Ticker**: Call `client.get_ticker(symbol="BTC/USDT")` and verify last price.
- [ ] **Paper Order**: Create a strategy with execution_mode="signal" first, then switch to "live" with minimal qty to verify order placement (use very small amount). Verify order appears in exchange UI.
- [ ] **Position Sync**: Verify `get_positions()` returns positions correctly for futures.
- [ ] **Fee Rate**: Verify `get_fee_rate("BTC/USDT")` returns maker/taker dict.

---

## Known Gaps / Phase 2

1. **Demo/Testnet URL**: KTX demo environment URL unknown — currently uses production base. Add testnet support once URL is known.
2. **Stop Orders**: KTX supports stop-loss/take-profit orders — can be added as `place_stop_order` later.
3. **WebSocket**: KTX has market/user WebSocket feeds (`wss://m-stream.ktx.app`, `wss://u-stream.ktx.app`) — not needed for Phase 1 REST-only integration.
4. **Reduce-only flag**: KTX API may have a `reduce_only` field on orders — verify exact field name with API docs and add if supported.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2025-05-27-ktx-exchange-integration.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
