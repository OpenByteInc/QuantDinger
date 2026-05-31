"""
Gate.io (direct REST) clients:
- Spot: /api/v4/spot/*
- Futures USDT: /api/v4/futures/usdt/*

Signing (apiv4):
SIGN = hex(hmac_sha512(secret, method + "\\n" + url + "\\n" + query + "\\n" + hexencode(sha512(payload)) + "\\n" + timestamp))
Headers:
- KEY: api key
- Timestamp: unix seconds
- SIGN: signature hex
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import urlencode

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.symbols import to_gate_currency_pair

logger = logging.getLogger(__name__)


def _gate_ticker_response_to_normalized(raw: Any) -> Dict[str, Any]:
    """Parse Gate spot/futures tickers API (array of one row) into a dict with float ``last`` for quick_trade."""
    row: Dict[str, Any] = {}
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        row = raw[0]
    elif isinstance(raw, dict) and raw:
        row = raw
    else:
        return {}
    last = 0.0
    for key in ("last", "mark_price", "index_price", "close", "price"):
        v = row.get(key)
        if v is not None and str(v).strip():
            try:
                last = float(str(v).replace(",", ""))
                break
            except Exception:
                continue
    out = dict(row)
    out["last"] = last
    out["close"] = last
    out["price"] = last
    return out


class _GateBase(BaseRestClient):
    def __init__(self, *, api_key: str, secret_key: str, base_url: str = "https://api.gateio.ws", timeout_sec: float = 15.0, channel_id: str = ""):
        super().__init__(base_url=base_url, timeout_sec=timeout_sec)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.channel_id = (channel_id or "").strip()
        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing Gate api_key/secret_key")

    def _sign(self, *, method: str, url: str, query_string: str, body_str: str, ts: str) -> str:
        # Per https://www.gate.com/docs/developers/apiv4/en/#authentication — payload slot is SHA512(body).hexdigest(),
        # not the raw body (GET / no body => hash of empty string).
        hashed_payload = hashlib.sha512((body_str or "").encode("utf-8")).hexdigest()
        msg = f"{method.upper()}\n{url}\n{query_string}\n{hashed_payload}\n{ts}"
        return hmac.new(self.secret_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha512).hexdigest()

    def _headers(self, ts: str, sign: str) -> Dict[str, str]:
        headers = {"KEY": self.api_key, "Timestamp": ts, "SIGN": sign, "Content-Type": "application/json"}
        if self.channel_id:
            headers["X-Gate-Channel-Id"] = self.channel_id[:19]
        return headers

    def _format_text(self, client_order_id: Optional[str]) -> str:
        raw = str(client_order_id or "").strip()
        if not raw:
            return ""
        normalized = []
        for ch in raw:
            if ch.isalnum() or ch in ("-", "_", "."):
                normalized.append(ch)
        text = "".join(normalized).strip()
        if not text:
            return ""
        if not text.startswith("t-"):
            text = f"t-{text}"
        return text[:28]

    def _signed_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        m = str(method or "GET").upper()
        ts = str(int(time.time()))
        body_str = self._json_dumps(json_body) if json_body is not None else ""
        qs = ""
        if params:
            norm = {str(k): "" if v is None else str(v) for k, v in dict(params).items()}
            qs = urlencode(sorted(norm.items()), doseq=True)
        sign = self._sign(method=m, url=path, query_string=qs, body_str=body_str, ts=ts)
        hdrs = dict(self._headers(ts, sign))
        if extra_headers:
            hdrs.update({str(k): str(v) for k, v in extra_headers.items()})
        code, data, text = self._request(m, path, params=params, data=body_str if body_str else None, headers=hdrs)
        if code >= 400:
            raise LiveTradingError(f"Gate HTTP {code}: {text[:500]}")
        return data

    def _public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
        code, data, text = self._request(method, path, params=params, headers=None, json_body=None, data=None)
        if code >= 400:
            raise LiveTradingError(f"Gate HTTP {code}: {text[:500]}")
        return data

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        pair = to_gate_currency_pair(symbol)
        try:
            if market_type == "spot":
                raw = self._signed_request("GET", "/api/v4/wallet/fee", params={"currency_pair": pair})
            else:
                settle = "usdt"
                contract = pair.replace("_", "").upper() + "_USDT" if "_" not in pair.upper() or "USDT" not in pair.upper() else pair
                raw = self._signed_request("GET", f"/api/v4/futures/{settle}/contracts/{contract}", params={})
            if isinstance(raw, dict):
                maker = abs(float(raw.get("maker_fee_rate") or raw.get("maker_fee") or 0))
                taker = abs(float(raw.get("taker_fee_rate") or raw.get("taker_fee") or 0))
                if maker > 0 or taker > 0:
                    return {"maker": maker, "taker": taker}
        except Exception as e:
            logger.warning(f"Gate get_fee_rate({symbol}) failed: {e}")
        return None


class GateSpotClient(_GateBase):
    def ping(self) -> bool:
        try:
            _ = self._public_request("GET", "/api/v4/spot/time")
            return True
        except Exception:
            return False

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        pair = to_gate_currency_pair(symbol)
        raw = self._public_request("GET", "/api/v4/spot/tickers", params={"currency_pair": pair})
        return _gate_ticker_response_to_normalized(raw)

    def get_accounts(self) -> Any:
        return self._signed_request("GET", "/api/v4/spot/accounts")

    def place_limit_order(self, *, symbol: str, side: str, size: float, price: float, client_order_id: Optional[str] = None) -> LiveOrderResult:
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        qty = float(size or 0.0)
        px = float(price or 0.0)
        if qty <= 0 or px <= 0:
            raise LiveTradingError("Invalid size/price")
        body: Dict[str, Any] = {
            "currency_pair": to_gate_currency_pair(symbol),
            "side": sd,
            "type": "limit",
            "amount": str(qty),
            "price": str(px),
            "time_in_force": "gtc",
        }
        text = self._format_text(client_order_id)
        if text:
            body["text"] = text
        raw = self._signed_request("POST", "/api/v4/spot/orders", json_body=body)
        oid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        return LiveOrderResult(exchange_id="gate", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw if isinstance(raw, dict) else {"raw": raw})

    def place_market_order(self, *, symbol: str, side: str, size: float, client_order_id: Optional[str] = None) -> LiveOrderResult:
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        qty = float(size or 0.0)
        if qty <= 0:
            raise LiveTradingError("Invalid size")
        # Gate defaults time_in_force to gtc; market orders only accept ioc/fok.
        body: Dict[str, Any] = {
            "currency_pair": to_gate_currency_pair(symbol),
            "side": sd,
            "type": "market",
            "amount": str(qty),
            "time_in_force": "ioc",
        }
        text = self._format_text(client_order_id)
        if text:
            body["text"] = text
        raw = self._signed_request("POST", "/api/v4/spot/orders", json_body=body)
        oid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        return LiveOrderResult(exchange_id="gate", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw if isinstance(raw, dict) else {"raw": raw})

    def cancel_order(self, *, order_id: str) -> Any:
        if not order_id:
            raise LiveTradingError("Gate spot cancel_order requires order_id")
        return self._signed_request("DELETE", f"/api/v4/spot/orders/{str(order_id)}")

    def get_order(self, *, order_id: str) -> Any:
        if not order_id:
            raise LiveTradingError("Gate spot get_order requires order_id")
        return self._signed_request("GET", f"/api/v4/spot/orders/{str(order_id)}")

    def get_spot_trades_for_order(self, *, order_id: str, currency_pair: str) -> Tuple[float, str]:
        """Aggregate the actual fee from Gate spot fill history for a given order.

        Gate updates ``fee`` on the spot order object asynchronously after the
        underlying fills are settled; the authoritative source is
        ``GET /api/v4/spot/my_trades`` which we sum here. Returns ``(0.0, "")``
        on any failure so callers can transparently fall back to the order-level
        fee. Reference: https://www.gate.com/docs/developers/apiv4/#list-personal-trading-history
        """
        oid = str(order_id or "").strip()
        pair = str(currency_pair or "").strip()
        if not oid or not pair:
            return 0.0, ""
        try:
            resp = self._signed_request(
                "GET", "/api/v4/spot/my_trades",
                params={"currency_pair": pair, "order_id": oid, "limit": 100},
            )
        except Exception:
            return 0.0, ""
        if not isinstance(resp, list):
            return 0.0, ""
        total = 0.0
        ccy = ""
        for t in resp:
            if not isinstance(t, dict):
                continue
            try:
                v = abs(float(t.get("fee") or 0.0))
            except Exception:
                v = 0.0
            if v > 0:
                total += v
                if not ccy:
                    ccy = str(t.get("fee_currency") or "").strip()
        return total, ccy

    def wait_for_fill(self, *, order_id: str, max_wait_sec: float = 10.0, poll_interval_sec: float = 0.5) -> Dict[str, Any]:
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last: Dict[str, Any] = {}
        while True:
            timed_out = time.time() >= end_ts
            try:
                resp = self.get_order(order_id=str(order_id))
                last = resp if isinstance(resp, dict) else {"raw": resp}
            except Exception:
                last = last or {}
            status = str(last.get("status") or "")
            filled = 0.0
            avg_price = 0.0
            fee = 0.0
            fee_ccy = ""
            try:
                filled = float(last.get("filled_amount") or 0.0)
            except Exception:
                filled = 0.0
            try:
                filled_total = float(last.get("filled_total") or 0.0)
                if filled > 0 and filled_total > 0:
                    avg_price = filled_total / filled
            except Exception:
                avg_price = 0.0
            # Extract fee from Gate API
            try:
                fee = abs(float(last.get("fee") or 0.0))
            except Exception:
                fee = 0.0
            fee_ccy = str(last.get("fee_currency") or "").strip()
            # Gate updates ``fee`` on the spot order object asynchronously after
            # the underlying trade rows land in /spot/my_trades — if the order
            # already reports filled volume but no fee, query the authoritative
            # fills endpoint and use its sum. This is the same shape Binance /
            # OKX use (post-fill trades endpoint), without it Gate fills get
            # persisted with ``commission=0`` and P&L drifts.
            if filled > 0 and fee <= 0:
                try:
                    mt_fee, mt_ccy = self.get_spot_trades_for_order(
                        order_id=str(order_id),
                        currency_pair=str(last.get("currency_pair") or ""),
                    )
                except Exception:
                    mt_fee, mt_ccy = 0.0, ""
                if mt_fee > 0:
                    fee = mt_fee
                    if mt_ccy:
                        fee_ccy = mt_ccy
            # Fee may lag behind filled/avg on order object; keep polling until timeout (same idea as Bitget/OKX).
            if filled > 0 and avg_price > 0:
                if fee <= 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            if status.lower() in ("closed", "cancelled", "canceled"):
                if fee <= 0 and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            if timed_out:
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            time.sleep(float(poll_interval_sec or 0.5))


class GateUsdtFuturesClient(_GateBase):
    def __init__(self, *, api_key: str, secret_key: str, base_url: str = "https://api.gateio.ws", timeout_sec: float = 15.0, channel_id: str = ""):
        super().__init__(api_key=api_key, secret_key=secret_key, base_url=base_url, timeout_sec=timeout_sec, channel_id=channel_id)
        # Best-effort cache for contract metadata to convert base qty -> contracts.
        self._contract_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._contract_cache_ttl_sec = 300.0

    @staticmethod
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _floor(value: Decimal) -> Decimal:
        try:
            return value.to_integral_value(rounding=ROUND_DOWN)
        except Exception:
            return Decimal("0")

    def ping(self) -> bool:
        # Gate futures REST no longer serves /api/v4/futures/usdt/time (returns 400 on fx-api / api hosts).
        # Use a lightweight public list call instead.
        try:
            _ = self._public_request("GET", "/api/v4/futures/usdt/contracts", params={"limit": 1})
            return True
        except Exception:
            return False

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        contract = to_gate_currency_pair(symbol)
        raw = self._public_request("GET", "/api/v4/futures/usdt/tickers", params={"contract": contract})
        return _gate_ticker_response_to_normalized(raw)

    def get_contract(self, *, contract: str) -> Dict[str, Any]:
        """Fetch contract metadata with ``X-Gate-Size-Decimal: 1`` to get accurate string-typed size fields."""
        c = str(contract or "").strip()
        if not c:
            return {}
        now = time.time()
        cached = self._contract_cache.get(c)
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0.0)) <= float(self._contract_cache_ttl_sec or 300.0):
                return obj
        code, data, text = self._request(
            "GET", f"/api/v4/futures/usdt/contracts/{c}",
            params=None, headers={"X-Gate-Size-Decimal": "1"},
            json_body=None, data=None,
        )
        if code >= 400:
            raise LiveTradingError(f"Gate HTTP {code}: {text[:500]}")
        obj = data if isinstance(data, dict) else {}
        if obj:
            self._contract_cache[c] = (now, obj)
        return obj

    @staticmethod
    def _decimal_places(d: Decimal) -> int:
        """Return the number of decimal places in a Decimal value."""
        sign, digits, exponent = d.as_tuple()
        return max(0, -int(exponent))

    def _resolve_order_size(self, *, contract: str, side: str, base_size: float) -> Tuple[str, Optional[Dict[str, str]]]:
        """
        Convert base-asset qty to a signed Gate ``size`` string and determine whether to use
        the ``X-Gate-Size-Decimal`` header.

        Per Gate announcement (2025-12-18):
        - ``size`` is always in **contracts** (not base-asset units).
        - With ``X-Gate-Size-Decimal: 1``, ``size`` becomes a string that supports decimals.
        - A contract supports fractional ordering when ``order_size_min`` (queried with the
          decimal header) contains a fractional part (e.g. ``"0.1"``).
        - Precision must align with ``order_size_min`` (e.g. if min is ``"0.1"`` → 1 dp).
        """
        sd = (side or "").strip().lower()
        sign = Decimal("1") if sd == "buy" else Decimal("-1")
        req = self._to_dec(base_size)
        if req <= 0:
            return ("0", None)

        meta: Dict[str, Any] = {}
        try:
            meta = self.get_contract(contract=contract) or {}
        except Exception:
            meta = {}

        qm = self._to_dec(meta.get("quanto_multiplier") or meta.get("quantoMultiplier") or "0")
        if qm <= 0:
            qm = Decimal("1")

        contracts = req / qm

        order_min = self._to_dec(meta.get("order_size_min") or "1")
        if order_min <= 0:
            order_min = Decimal("1")
        dp = self._decimal_places(order_min)

        if dp > 0:
            step = Decimal(10) ** (-dp)
            q = contracts.quantize(step, rounding=ROUND_DOWN)
            if q < order_min and contracts > 0:
                q = order_min
            signed_q = q * sign
            s = format(signed_q, "f")
            if "." in s:
                s = s.rstrip("0").rstrip(".")
            return (s if s and s not in ("-", "+", "-0", "+0", "0") else "0",
                    {"X-Gate-Size-Decimal": "1"})
        else:
            iv = int(self._floor(contracts))
            int_min = max(1, int(order_min))
            if iv < int_min and contracts > 0:
                iv = int_min
            signed_iv = int(Decimal(iv) * sign)
            return (str(signed_iv), None)

    def _base_to_contracts(self, *, contract: str, base_size: float) -> int:
        """Integer contracts estimate (for internal use like position sizing display)."""
        meta: Dict[str, Any] = {}
        try:
            meta = self.get_contract(contract=contract) or {}
        except Exception:
            meta = {}
        qm = self._to_dec(meta.get("quanto_multiplier") or "0")
        if qm <= 0:
            qm = Decimal("1")
        return max(1, int(self._floor(self._to_dec(base_size) / qm)))

    def contracts_signed_to_base_qty(self, *, contract: str, contracts_signed: float) -> float:
        """Convert signed position size (contracts) from Gate positions API to base-asset quantity."""
        try:
            ct = abs(float(contracts_signed or 0.0))
        except Exception:
            return 0.0
        if ct <= 0:
            return 0.0
        meta: Dict[str, Any] = {}
        try:
            meta = self.get_contract(contract=str(contract)) or {}
        except Exception:
            meta = {}
        qm = self._to_dec(
            meta.get("quanto_multiplier")
            or meta.get("quantoMultiplier")
            or meta.get("contract_size")
            or meta.get("contractSize")
            or "0"
        )
        if qm <= 0:
            qm = Decimal("1")
        return float(Decimal(str(ct)) * qm)

    def get_accounts(self) -> Any:
        return self._signed_request("GET", "/api/v4/futures/usdt/accounts")

    def get_positions(self) -> Any:
        return self._signed_request(
            "GET", "/api/v4/futures/usdt/positions",
            extra_headers={"X-Gate-Size-Decimal": "1"},
        )

    def set_leverage(self, *, contract: str, leverage: float) -> bool:
        c = str(contract or "").strip()
        if not c:
            return False
        try:
            lv = int(float(leverage or 1.0))
        except Exception:
            lv = 1
        if lv < 1:
            lv = 1
        path = f"/api/v4/futures/usdt/positions/{c}/leverage"
        lv_s = str(lv)
        # Gate expects ``leverage`` / ``cross_leverage_limit`` as **query parameters**, not JSON body
        # (see gateapi-python: update_position_leverage). Cross / portfolio mode: leverage=0 + cross_leverage_limit.
        attempts: Tuple[Dict[str, str], ...] = (
            {"leverage": lv_s},
            {"leverage": "0", "cross_leverage_limit": lv_s},
            {"leverage": lv_s, "cross_leverage_limit": lv_s},
        )
        last_err: Optional[Exception] = None
        for qp in attempts:
            try:
                _ = self._signed_request("POST", path, params=qp, json_body=None)
                return True
            except LiveTradingError as e:
                last_err = e
            except Exception as e:
                last_err = e
        logger.warning("Gate set_leverage failed contract=%s leverage=%s: %s", c, lv, last_err)
        return False

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        base_qty = float(size or 0.0)
        if base_qty <= 0:
            raise LiveTradingError("Invalid size (<= 0)")
        contract = to_gate_currency_pair(symbol)
        size_str, extra_headers = self._resolve_order_size(contract=contract, side=sd, base_size=base_qty)
        if size_str in ("0", "-0", ""):
            raise LiveTradingError("Invalid size (resolved contracts == 0)")
        logger.info("Gate futures market: contract=%s side=%s base_qty=%s size_str=%s decimal_hdr=%s",
                     contract, sd, base_qty, size_str, extra_headers is not None)
        body: Dict[str, Any] = {"contract": contract, "size": size_str, "price": "0", "tif": "ioc"}
        if reduce_only:
            body["reduce_only"] = True
        text = self._format_text(client_order_id)
        if text:
            body["text"] = text
        raw = self._signed_request(
            "POST",
            "/api/v4/futures/usdt/orders",
            json_body=body,
            extra_headers=extra_headers,
        )
        oid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        return LiveOrderResult(exchange_id="gate", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw if isinstance(raw, dict) else {"raw": raw})

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        price: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        sd = (side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        base_qty = float(size or 0.0)
        if base_qty <= 0:
            raise LiveTradingError("Invalid size (<= 0)")
        px = float(price or 0.0)
        if px <= 0:
            raise LiveTradingError("Invalid price")
        contract = to_gate_currency_pair(symbol)
        size_str, extra_headers = self._resolve_order_size(contract=contract, side=sd, base_size=base_qty)
        if size_str in ("0", "-0", ""):
            raise LiveTradingError("Invalid size (resolved contracts == 0)")
        body: Dict[str, Any] = {"contract": contract, "size": size_str, "price": str(px), "tif": "gtc"}
        if reduce_only:
            body["reduce_only"] = True
        text = self._format_text(client_order_id)
        if text:
            body["text"] = text
        raw = self._signed_request(
            "POST",
            "/api/v4/futures/usdt/orders",
            json_body=body,
            extra_headers=extra_headers,
        )
        oid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        return LiveOrderResult(exchange_id="gate", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw if isinstance(raw, dict) else {"raw": raw})

    def cancel_order(self, *, order_id: str) -> Any:
        if not order_id:
            raise LiveTradingError("Gate futures cancel_order requires order_id")
        return self._signed_request("DELETE", f"/api/v4/futures/usdt/orders/{str(order_id)}")

    def get_order(self, *, order_id: str) -> Any:
        if not order_id:
            raise LiveTradingError("Gate futures get_order requires order_id")
        return self._signed_request("GET", f"/api/v4/futures/usdt/orders/{str(order_id)}")

    def get_futures_trades_for_order(self, *, order_id: str, contract: str) -> Tuple[float, str]:
        """Aggregate the actual fee from Gate USDT futures fill history.

        Gate's USDT futures ``order`` object often reports ``fee=0`` even after
        the order is fully filled — the fee is only finalised when the trade
        row appears in ``GET /api/v4/futures/usdt/my_trades``. We sum the
        ``fee`` column there (in USDT). Note that the filter parameter on this
        endpoint is ``order`` (not ``order_id``). Returns ``(0.0, "")`` on any
        failure so callers can transparently fall back to the order-level fee.
        Reference: https://www.gate.com/docs/developers/apiv4/#list-personal-trading-history-2
        """
        oid = str(order_id or "").strip()
        c = str(contract or "").strip()
        if not oid or not c:
            return 0.0, ""
        try:
            resp = self._signed_request(
                "GET", "/api/v4/futures/usdt/my_trades",
                params={"contract": c, "order": oid, "limit": 100},
            )
        except Exception:
            return 0.0, ""
        if not isinstance(resp, list):
            return 0.0, ""
        total = 0.0
        for t in resp:
            if not isinstance(t, dict):
                continue
            try:
                v = abs(float(t.get("fee") or 0.0))
            except Exception:
                v = 0.0
            total += v
        if total > 0:
            # Gate USDT-margined perpetuals settle fees in USDT.
            return total, "USDT"
        return 0.0, ""

    def wait_for_fill(self, *, order_id: str, contract: str, max_wait_sec: float = 12.0, poll_interval_sec: float = 0.5) -> Dict[str, Any]:
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last: Dict[str, Any] = {}
        qm = Decimal("1")
        try:
            meta = self.get_contract(contract=str(contract)) or {}
            qm = self._to_dec(meta.get("quanto_multiplier") or meta.get("contract_size") or "1")
            if qm <= 0:
                qm = Decimal("1")
        except Exception:
            qm = Decimal("1")
        while True:
            timed_out = time.time() >= end_ts
            try:
                resp = self.get_order(order_id=str(order_id))
                last = resp if isinstance(resp, dict) else {"raw": resp}
            except Exception:
                last = last or {}
            status = str(last.get("status") or "")
            filled = 0.0
            avg_price = 0.0
            fee = 0.0
            fee_ccy = ""
            try:
                # Gate futures often returns "filled_size" in contracts.
                filled_ct = abs(float(last.get("filled_size") or last.get("filledSize") or 0.0))
                filled = float(Decimal(str(filled_ct)) * qm)
            except Exception:
                filled = 0.0
            try:
                avg_price = float(last.get("fill_price") or last.get("fillPrice") or last.get("price") or 0.0)
            except Exception:
                avg_price = 0.0
            # Extract fee from Gate Futures API
            try:
                fee = abs(float(last.get("fee") or 0.0))
            except Exception:
                fee = 0.0
            # Gate USDT futures fees are in USDT
            if fee > 0:
                fee_ccy = "USDT"
            # Gate USDT futures order objects routinely report ``fee=0`` even
            # after the order is fully filled; the authoritative source is
            # /futures/usdt/my_trades (filtered by the ``order`` param). Pull
            # from there so commissions stop landing in ``qd_strategy_trades``
            # as zero and P&L stops drifting.
            if filled > 0 and fee <= 0:
                try:
                    mt_fee, mt_ccy = self.get_futures_trades_for_order(
                        order_id=str(order_id),
                        contract=str(contract),
                    )
                except Exception:
                    mt_fee, mt_ccy = 0.0, ""
                if mt_fee > 0:
                    fee = mt_fee
                    fee_ccy = mt_ccy or "USDT"
            if filled > 0 and avg_price > 0:
                if fee <= 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            if str(status).lower() in ("finished", "cancelled", "canceled"):
                if fee <= 0 and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            if timed_out:
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            time.sleep(float(poll_interval_sec or 0.5))


