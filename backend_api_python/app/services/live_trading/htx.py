"""
HTX (Huobi) direct REST client for spot and USDT-margined perpetual swap.

- Spot: https://api.htx.com (v1 private/public)
- USDT-M swap private: https://api.hbdm.com /v5/*
- USDT-M swap public (contract spec, ticker): linear-swap-api / linear-swap-ex
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import datetime
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading import htx_v5
from app.services.live_trading.symbols import to_htx_contract_code, to_htx_spot_symbol

logger = logging.getLogger(__name__)


class HtxClient(BaseRestClient):
    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        base_url: str = "https://api.htx.com",
        futures_base_url: str = "https://api.hbdm.com",
        timeout_sec: float = 15.0,
        market_type: str = "swap",
        broker_id: str = "",
    ):
        chosen_base = futures_base_url if str(market_type or "").strip().lower() == "swap" else base_url
        super().__init__(base_url=chosen_base, timeout_sec=timeout_sec)
        self.spot_base_url = (base_url or "https://api.htx.com").rstrip("/")
        self.futures_base_url = (futures_base_url or "https://api.hbdm.com").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.market_type = (market_type or "swap").strip().lower()
        self.broker_id = (broker_id or "").strip()
        if self.market_type not in ("spot", "swap"):
            self.market_type = "swap"
        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing HTX api_key/secret_key")

        self._spot_account_id: Optional[str] = None
        self._contract_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._contract_cache_ttl_sec = 300.0
        self._lever_cache: Dict[str, int] = {}
        self._v5_asset_mode: Optional[int] = None
        self._v5_asset_mode_ts: float = 0.0
        self._v5_multi_asset_switch_tried: bool = False
        self._pos_mode_cache: Dict[str, Tuple[float, bool]] = {}
        self._pos_mode_cache_ttl_sec: float = 60.0

    @staticmethod
    def _format_swap_client_order_id(client_order_id: Optional[str]) -> Optional[int]:
        if not client_order_id:
            return None
        digits = "".join(c for c in str(client_order_id) if c.isdigit())
        if not digits:
            digits = str(int(time.time() * 1000))
        val = int(digits[-18:])
        return val if 0 < val <= 9223372036854775807 else None

    def _format_spot_client_order_id(self, client_order_id: Optional[str]) -> str:
        prefix = str(self.broker_id or "").strip()
        raw = str(client_order_id or "").strip()
        if not prefix and not raw:
            return ""
        if not raw:
            raw = str(int(time.time() * 1000))
        allowed = []
        for ch in raw:
            if ch.isalnum() or ch in ("_", "-"):
                allowed.append(ch)
        suffix = "".join(allowed).strip("-_")
        if not suffix:
            suffix = str(int(time.time() * 1000))
        if prefix:
            combined = suffix if suffix.startswith(prefix) else f"{prefix}-{suffix}"
        else:
            combined = suffix
        return combined[:64]

    @staticmethod
    def _utc_ts() -> str:
        return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _floor_to_int(value: Decimal) -> int:
        try:
            return int(value.to_integral_value(rounding=ROUND_DOWN))
        except Exception:
            return 0

    def _sign_params(self, *, method: str, base_url: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        signed = dict(params or {})
        signed["AccessKeyId"] = self.api_key
        signed["SignatureMethod"] = "HmacSHA256"
        signed["SignatureVersion"] = "2"
        signed["Timestamp"] = self._utc_ts()
        encoded = urlencode(sorted((str(k), str(v)) for k, v in signed.items()))
        host = urlparse(base_url).netloc
        payload = "\n".join([str(method or "GET").upper(), host, path, encoded])
        digest = hmac.new(self.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        signed["Signature"] = base64.b64encode(digest).decode("utf-8")
        return signed

    def _spot_public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        old_base = self.base_url
        self.base_url = self.spot_base_url
        try:
            code, data, text = self._request(method, path, params=params)
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX spot HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and str(data.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX spot error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def _spot_private_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        signed_params = self._sign_params(method=method, base_url=self.spot_base_url, path=path, params=params or {})
        old_base = self.base_url
        self.base_url = self.spot_base_url
        try:
            code, data, text = self._request(method, path, params=signed_params, json_body=json_body)
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX spot HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and str(data.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX spot error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def _swap_public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        old_base = self.base_url
        self.base_url = self.futures_base_url
        try:
            code, data, text = self._request(method, path, params=params)
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX swap HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and str(data.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX swap error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def _swap_private_request_raw(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        signed_params = self._sign_params(
            method=method, base_url=self.futures_base_url, path=path, params=params or {}
        )
        old_base = self.base_url
        self.base_url = self.futures_base_url
        try:
            code, data, text = self._request(method, path, params=signed_params, json_body=json_body)
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX swap HTTP {code}: {text[:500]}")
        return data if isinstance(data, dict) else {"raw": data}

    def _raise_v5_error(self, path: str, raw: Dict[str, Any]) -> None:
        if isinstance(raw, dict) and str(raw.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX swap error: {raw}")
        err = htx_v5.v5_err_message(raw)
        if htx_v5.is_single_asset_mode_unavailable(err):
            raise LiveTradingError(
                "HTX V5 已暂停「单币种保证金」私有接口。请在 HTX App/Web → 合约 → 设置 中切换为「联合保证金」"
                "（Multi-Assets Collateral），或确保无持仓/挂单后由系统调用 asset_mode=1 升级。"
                f" 原始错误: {err}"
            )
        raise LiveTradingError(f"HTX V5 {path}: {err}")

    def _try_upgrade_to_multi_asset_mode(self) -> bool:
        """Switch account to multi-asset collateral (asset_mode=1) for V5 private APIs."""
        if self._v5_multi_asset_switch_tried:
            return self._v5_asset_mode == 1
        self._v5_multi_asset_switch_tried = True
        current = self._fetch_v5_asset_mode()
        if current == 1:
            return True
        logger.info(
            "HTX V5 single-asset unavailable (current asset_mode=%s); switching to multi-asset (1)...",
            current,
        )
        if self.switch_asset_mode(1):
            return True
        logger.warning("HTX failed to switch asset_mode to 1 (multi-asset). Close positions/orders and retry in HTX app.")
        return False

    def _swap_v5_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        _allow_mode_upgrade: bool = True,
    ) -> Dict[str, Any]:
        raw = self._swap_private_request_raw(method, path, params=params, json_body=json_body)
        if htx_v5.v5_ok(raw):
            return raw
        err = htx_v5.v5_err_message(raw)
        if _allow_mode_upgrade and htx_v5.is_single_asset_mode_unavailable(err):
            if self._try_upgrade_to_multi_asset_mode():
                return self._swap_v5_request(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    _allow_mode_upgrade=False,
                )
        self._raise_v5_error(path, raw)
        return raw  # unreachable

    def ping(self) -> bool:
        try:
            if self.market_type == "spot":
                self._spot_public_request("GET", "/v1/common/timestamp")
            else:
                self._swap_v5_request("GET", "/v5/account/balance")
            return True
        except Exception:
            try:
                self._swap_public_request("GET", "/linear-swap-api/v1/swap_contract_info")
                return True
            except Exception:
                return False

    def _fetch_v5_asset_mode(self) -> Optional[int]:
        now = time.time()
        if self._v5_asset_mode is not None and (now - self._v5_asset_mode_ts) < 600:
            return self._v5_asset_mode
        try:
            raw = self._swap_private_request_raw("GET", "/v5/account/asset_mode")
            if not htx_v5.v5_ok(raw):
                return None
            data = htx_v5.v5_data(raw)
            if isinstance(data, dict) and data.get("asset_mode") is not None:
                self._v5_asset_mode = int(data.get("asset_mode"))
                self._v5_asset_mode_ts = now
                logger.info("HTX V5 asset_mode=%s", self._v5_asset_mode)
                return self._v5_asset_mode
        except Exception as e:
            logger.debug("HTX V5 asset_mode probe failed: %s", e)
        return None

    def switch_asset_mode(self, asset_mode: int) -> bool:
        """POST /v5/account/asset_mode — 0=单币种(已逐步停用), 1=联合保证金 (HTX V5 推荐)."""
        raw = self._swap_private_request_raw(
            "POST", "/v5/account/asset_mode", json_body={"asset_mode": int(asset_mode)}
        )
        if htx_v5.v5_ok(raw):
            data = htx_v5.v5_data(raw) or {}
            if isinstance(data, dict) and data.get("asset_mode") is not None:
                self._v5_asset_mode = int(data.get("asset_mode"))
            else:
                self._v5_asset_mode = int(asset_mode)
            self._v5_asset_mode_ts = time.time()
            logger.info("HTX asset_mode set to %s", self._v5_asset_mode)
            return True
        logger.warning("HTX switch asset_mode failed: %s", raw)
        return False

    def _default_margin_mode(self) -> str:
        return "cross"

    def get_swap_hedge_mode(self, *, symbol: str) -> bool:
        """
        True when account uses HTX dual_side (hedge) position mode for ``symbol``.

        GET /v5/position/mode — defaults to one-way when the API call fails.
        """
        if self.market_type != "swap":
            return False
        contract_code = to_htx_contract_code(symbol)
        if not contract_code:
            return False
        key = contract_code.upper()
        now = time.time()
        cached = self._pos_mode_cache.get(key)
        if cached:
            ts, hedge = cached
            if (now - float(ts or 0.0)) <= float(self._pos_mode_cache_ttl_sec or 60.0):
                return bool(hedge)
        hedge: Optional[bool] = None
        margin_mode = self._default_margin_mode()
        for params in (
            {"contract_code": contract_code, "margin_mode": margin_mode},
            {"contract_code": contract_code},
            {"margin_account": "USDT", "margin_mode": margin_mode},
            {"margin_account": "USDT"},
        ):
            if hedge is not None:
                break
            try:
                raw = self._swap_v5_request("GET", "/v5/position/mode", params=params)
                hedge = htx_v5.parse_position_mode_hedged(htx_v5.v5_data(raw))
            except Exception as e:
                logger.debug("HTX V5 position/mode probe %s for %s: %s", params, contract_code, e)
        if hedge is None:
            try:
                pos_raw = self._swap_v5_request(
                    "GET",
                    "/v5/trade/position/opens",
                    params={"contract_code": contract_code},
                )
                hedge = htx_v5.parse_position_mode_hedged(htx_v5.v5_data(pos_raw))
            except Exception as e_pos:
                logger.debug(
                    "HTX V5 position/opens probe for %s: %s", contract_code, e_pos
                )
        if hedge is None:
            for json_body in (
                {"contract_code": contract_code},
                {"margin_account": "USDT"},
            ):
                if hedge is not None:
                    break
                try:
                    raw_v1 = self._swap_private_request_raw(
                        "POST",
                        "/linear-swap-api/v1/swap_cross_account_position_info",
                        json_body=json_body,
                    )
                    if str(raw_v1.get("status") or "").lower() != "ok":
                        continue
                    data_v1 = raw_v1.get("data")
                    if isinstance(data_v1, list) and data_v1:
                        hedge = htx_v5.parse_position_mode_hedged(data_v1[0])
                    elif isinstance(data_v1, dict):
                        hedge = htx_v5.parse_position_mode_hedged(data_v1)
                        if hedge is None:
                            positions = data_v1.get("positions")
                            if isinstance(positions, list) and positions:
                                hedge = htx_v5.parse_position_mode_hedged(positions[0])
                except Exception as e2:
                    logger.debug(
                        "HTX V1 position_mode probe %s for %s: %s", json_body, contract_code, e2
                    )
        if hedge is None:
            hedge = False
            logger.info(
                "HTX position mode unknown for %s; assuming one-way (single_side)",
                contract_code,
            )
        else:
            logger.info(
                "HTX position mode for %s: %s",
                contract_code,
                "dual_side" if hedge else "single_side",
            )
        self._pos_mode_cache[key] = (now, bool(hedge))
        return bool(hedge)

    def _get_spot_account_id(self) -> str:
        if self._spot_account_id:
            return self._spot_account_id
        raw = self._spot_private_request("GET", "/v1/account/accounts")
        data = raw.get("data") or []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").lower() == "spot" and str(item.get("state") or "").lower() in ("working", ""):
                    self._spot_account_id = str(item.get("id") or "")
                    if self._spot_account_id:
                        return self._spot_account_id
            for item in data:
                if isinstance(item, dict) and item.get("id"):
                    self._spot_account_id = str(item.get("id"))
                    return self._spot_account_id
        raise LiveTradingError("HTX spot account id not found")

    def get_accounts(self) -> Any:
        if self.market_type == "spot":
            return self._spot_private_request("GET", "/v1/account/accounts")
        return self.get_balance()

    def get_balance(self) -> Any:
        if self.market_type == "spot":
            account_id = self._get_spot_account_id()
            return self._spot_private_request("GET", f"/v1/account/accounts/{account_id}/balance")
        raw = self._swap_v5_request("GET", "/v5/account/balance")
        return htx_v5.normalize_balance(raw)

    def get_positions(self, *, symbol: str = "") -> Any:
        if self.market_type == "spot":
            balance = self.get_balance()
            items = (((balance.get("data") or {}).get("list")) if isinstance(balance, dict) else None) or []
            base_asset = ""
            if symbol:
                base_asset = str(symbol).split("/", 1)[0].split(":", 1)[0].strip().upper()
            rows = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                ccy = str(item.get("currency") or "").upper()
                if not ccy or (base_asset and ccy != base_asset):
                    continue
                bal = self._to_dec(item.get("balance") or "0")
                if bal <= 0:
                    continue
                rows.append({
                    "symbol": f"{ccy}/USDT",
                    "bal": float(bal),
                    "availBal": float(self._to_dec(item.get("balance") or "0")),
                    "cost_open": 0,
                    "profit_unreal": 0,
                })
            return {"data": rows}

        contract_code = to_htx_contract_code(symbol) if symbol else ""
        params: Dict[str, Any] = {}
        if contract_code:
            params["contract_code"] = contract_code
        try:
            raw = self._swap_v5_request("GET", "/v5/trade/position/opens", params=params or None)
        except LiveTradingError:
            body: Dict[str, Any] = {}
            if contract_code:
                body["contract_code"] = contract_code
            raw = self._swap_v5_request("POST", "/v5/trade/position_all", json_body=body)
        return htx_v5.normalize_positions(raw)

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        if self.market_type == "spot":
            raw = self._spot_public_request("GET", "/market/detail/merged", params={"symbol": to_htx_spot_symbol(symbol)})
        else:
            raw = self._swap_public_request(
                "GET", "/linear-swap-ex/market/detail/merged", params={"contract_code": to_htx_contract_code(symbol)}
            )
        tick = raw.get("tick") if isinstance(raw, dict) else {}
        return tick if isinstance(tick, dict) else {}

    def get_contract_info(self, *, symbol: str) -> Dict[str, Any]:
        key = to_htx_contract_code(symbol)
        cached = self._contract_cache.get(key)
        now = time.time()
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0)) <= float(self._contract_cache_ttl_sec or 300):
                return obj
        raw = self._swap_public_request("GET", "/linear-swap-api/v1/swap_contract_info", params={"contract_code": key})
        data = raw.get("data") or []
        obj = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        if obj:
            self._contract_cache[key] = (now, obj)
        return obj

    def _base_to_contracts(self, *, symbol: str, qty: float) -> int:
        req = self._to_dec(qty)
        if req <= 0:
            return 0
        info = self.get_contract_info(symbol=symbol) or {}
        contract_size = self._to_dec(info.get("contract_size") or info.get("contractSize") or "1")
        if contract_size <= 0:
            contract_size = Decimal("1")
        contracts = req / contract_size
        val = self._floor_to_int(contracts)
        return val if val > 0 else 1

    def set_leverage(self, *, symbol: str, leverage: float) -> bool:
        if self.market_type == "spot":
            return False
        contract_code = to_htx_contract_code(symbol)
        try:
            lv = int(float(leverage or 1))
        except Exception:
            lv = 1
        if lv < 1:
            lv = 1
        body = htx_v5.build_lever_body(
            contract_code=contract_code,
            lever_rate=lv,
            margin_mode=self._default_margin_mode(),
        )
        self._swap_v5_request("POST", "/v5/position/lever", json_body=body)
        self._lever_cache[contract_code] = lv
        return True

    def _place_swap_order_v1(self, body: Dict[str, Any]) -> LiveOrderResult:
        raw = self._swap_private_request_raw(
            "POST", "/linear-swap-api/v1/swap_cross_order", json_body=body
        )
        if str(raw.get("status") or "").lower() != "ok":
            err = raw.get("err_msg") or raw.get("err_code") or raw
            raise LiveTradingError(f"HTX swap_cross_order: {err}")
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        oid = str(data.get("order_id_str") or data.get("order_id") or "")
        logger.info("HTX V1 cross order placed order_id=%s", oid)
        return LiveOrderResult(exchange_id="htx", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)

    def _place_swap_order(self, body: Dict[str, Any]) -> LiveOrderResult:
        req = dict(body)
        req.setdefault("margin_mode", self._default_margin_mode())
        raw = self._swap_v5_request("POST", "/v5/trade/order", json_body=req)
        norm = htx_v5.normalize_order_place(raw)
        data = norm.get("data") or {}
        oid = str(data.get("order_id_str") or data.get("order_id") or "")
        logger.info("HTX V5 order placed order_id=%s", oid)
        return LiveOrderResult(exchange_id="htx", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)

    @staticmethod
    def _is_position_mode_param_error(err: Exception) -> bool:
        text = str(err or "").lower()
        return "position mode" in text and ("parameter" in text or "passing" in text)

    @staticmethod
    def _is_illegal_parameter_type_error(err: Exception) -> bool:
        text = str(err or "").lower()
        return "illegal parameter type" in text or "invalid-type" in text

    def _place_swap_order_with_mode_fallback(
        self,
        *,
        symbol: str,
        contract_code: str,
        body: Dict[str, Any],
        pos_side: str = "",
    ) -> LiveOrderResult:
        reduce_only = body.get("reduce_only") in (1, "1", True) or str(
            body.get("offset") or ""
        ).lower() == "close"
        side = str(body.get("side") or body.get("direction") or "buy")
        lever_rate = int(body.get("lever_rate") or 5)
        has_price = body.get("price") is not None
        order_price_type = "limit" if has_price else "opponent"
        price = float(body["price"]) if has_price else None
        client_order_id = body.get("client_order_id")
        channel_code = str(body.get("channel_code") or self.broker_id or "")
        volume = int(body.get("volume") or 0)
        preferred = self.get_swap_hedge_mode(symbol=symbol)

        v1_candidates = htx_v5.v1_order_body_variants(
            contract_code=str(body.get("contract_code") or contract_code),
            volume=volume,
            side=side,
            lever_rate=lever_rate,
            order_price_type=order_price_type,
            price=price,
            client_order_id=client_order_id,
            channel_code=channel_code,
            reduce_only=reduce_only,
            preferred_hedge=preferred,
        )
        margin_mode = str(body.get("margin_mode") or self._default_margin_mode())
        v5_common = dict(
            contract_code=str(body.get("contract_code") or contract_code),
            volume=volume,
            side=side,
            order_price_type=order_price_type,
            price=price,
            client_order_id=client_order_id,
            channel_code=channel_code,
            margin_mode=margin_mode,
            reduce_only=reduce_only,
            pos_side=pos_side,
        )

        asset_mode = self._fetch_v5_asset_mode()
        skip_v1 = asset_mode == 1
        last_err: Optional[Exception] = None
        tried_v1 = False
        if not skip_v1:
            for req in v1_candidates:
                tried_v1 = True
                try:
                    hedge = str(req.get("offset") or "").lower() in ("open", "close")
                    self._pos_mode_cache[contract_code.upper()] = (time.time(), hedge)
                    return self._place_swap_order_v1(req)
                except LiveTradingError as e:
                    last_err = e
                    if htx_v5.is_multi_asset_v1_unavailable(str(e)):
                        skip_v1 = True
                        logger.info(
                            "HTX account is multi-asset collateral (asset_mode=%s); skipping V1 orders",
                            asset_mode,
                        )
                        break
                    logger.warning("HTX V1 order attempt failed for %s: %s", contract_code, e)
        else:
            logger.debug(
                "HTX V1 swap_cross_order skipped for %s (asset_mode=1 multi-asset)",
                contract_code,
            )

        if tried_v1 and not skip_v1:
            logger.info("HTX V1 swap_cross_order failed for %s; falling back to V5", contract_code)

        v5_mode = preferred
        logger.info(
            "HTX V5 multi-asset order for %s: position_mode=%s pos_side_hint=%s reduce_only=%s",
            contract_code,
            "dual_side" if v5_mode else "single_side",
            pos_side or "(auto)",
            reduce_only,
        )
        v5_candidates = htx_v5.swap_order_body_variants(
            preferred_hedge=v5_mode, **v5_common
        )
        idx = 0
        while idx < len(v5_candidates):
            req = v5_candidates[idx]
            try:
                hedge = req.get("position_side") in ("long", "short")
                self._pos_mode_cache[contract_code.upper()] = (time.time(), hedge)
                return self._place_swap_order(req)
            except LiveTradingError as e:
                last_err = e
                err_text = str(e)
                if self._is_illegal_parameter_type_error(e):
                    logger.warning(
                        "HTX V5 illegal type for %s (type=%s position_side=%s); trying next",
                        contract_code,
                        req.get("type"),
                        req.get("position_side"),
                    )
                    idx += 1
                    continue
                if htx_v5.is_oneway_order_rejected_on_hedge(err_text) and v5_mode:
                    logger.warning(
                        "HTX V5 hedge params rejected on %s (likely single_side); retry one-way",
                        contract_code,
                    )
                    v5_mode = False
                    v5_candidates = htx_v5.swap_order_body_variants(
                        oneway_only=True, **v5_common
                    )
                    idx = 0
                    continue
                if htx_v5.is_hedge_order_rejected_on_oneway(err_text) and not v5_mode:
                    logger.warning(
                        "HTX V5 one-way params rejected on %s (likely dual_side); retry hedge",
                        contract_code,
                    )
                    v5_mode = True
                    v5_candidates = htx_v5.swap_order_body_variants(
                        hedge_only=True, **v5_common
                    )
                    idx = 0
                    continue
                if not self._is_position_mode_param_error(e):
                    raise
                logger.warning(
                    "HTX V5 position-mode mismatch for %s (type=%s position_side=%s reduce_only=%s); trying next",
                    contract_code,
                    req.get("type"),
                    req.get("position_side"),
                    req.get("reduce_only"),
                )
                idx += 1
        if last_err:
            raise last_err
        raise LiveTradingError("HTX order failed after V1 and V5 retries")

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
        if self.market_type == "spot":
            account_id = self._get_spot_account_id()
            sd = str(side or "").strip().lower()
            if sd not in ("buy", "sell"):
                raise LiveTradingError(f"Invalid side: {side}")
            amount = float(qty or 0)
            if amount <= 0:
                raise LiveTradingError("Invalid qty")
            order_type = f"{sd}-market"
            if sd == "buy":
                tick = self.get_ticker(symbol=symbol)
                last = float(tick.get("close") or tick.get("price") or tick.get("lastPrice") or 0)
                if last <= 0:
                    raise LiveTradingError("HTX spot market buy requires latest price for qty->value conversion")
                amount = amount * last
            body = {
                "account-id": account_id,
                "symbol": to_htx_spot_symbol(symbol),
                "type": order_type,
                "amount": f"{amount:.12f}".rstrip("0").rstrip("."),
                "source": "spot-api",
            }
            formatted_client_order_id = self._format_spot_client_order_id(client_order_id)
            if formatted_client_order_id:
                body["client-order-id"] = formatted_client_order_id
            raw = self._spot_private_request("POST", "/v1/order/orders/place", json_body=body)
            data = raw.get("data")
            oid = str(data or "")
            return LiveOrderResult(exchange_id="htx", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)

        contract_code = to_htx_contract_code(symbol)
        volume = self._base_to_contracts(symbol=symbol, qty=qty)
        if volume <= 0:
            raise LiveTradingError("Invalid HTX swap volume")
        sd = str(side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        hedge_mode = self.get_swap_hedge_mode(symbol=symbol)
        ps = htx_v5.resolve_v5_position_side(
            side=sd, reduce_only=reduce_only, hedge_mode=hedge_mode, pos_side=pos_side
        )
        body = htx_v5.build_swap_order_body(
            contract_code=contract_code,
            volume=volume,
            side=sd,
            order_price_type="opponent",
            channel_code=self.broker_id or "",
            margin_mode=self._default_margin_mode(),
            reduce_only=reduce_only,
            position_side=ps,
        )
        swap_coid = self._format_swap_client_order_id(client_order_id)
        if swap_coid is not None:
            body["client_order_id"] = swap_coid
        return self._place_swap_order_with_mode_fallback(
            symbol=symbol, contract_code=contract_code, body=body, pos_side=pos_side
        )

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        price: float,
        reduce_only: bool = False,
        pos_side: str = "",
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        px = float(price or 0)
        qty = float(size or 0)
        if px <= 0 or qty <= 0:
            raise LiveTradingError("Invalid size/price")
        sd = str(side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")

        if self.market_type == "spot":
            account_id = self._get_spot_account_id()
            body = {
                "account-id": account_id,
                "symbol": to_htx_spot_symbol(symbol),
                "type": f"{sd}-limit",
                "amount": f"{qty:.12f}".rstrip("0").rstrip("."),
                "price": f"{px:.12f}".rstrip("0").rstrip("."),
                "source": "spot-api",
            }
            formatted_client_order_id = self._format_spot_client_order_id(client_order_id)
            if formatted_client_order_id:
                body["client-order-id"] = formatted_client_order_id
            raw = self._spot_private_request("POST", "/v1/order/orders/place", json_body=body)
            data = raw.get("data")
            oid = str(data or "")
            return LiveOrderResult(exchange_id="htx", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)

        contract_code = to_htx_contract_code(symbol)
        volume = self._base_to_contracts(symbol=symbol, qty=qty)
        hedge_mode = self.get_swap_hedge_mode(symbol=symbol)
        ps = htx_v5.resolve_v5_position_side(
            side=sd, reduce_only=reduce_only, hedge_mode=hedge_mode, pos_side=pos_side
        )
        body = htx_v5.build_swap_order_body(
            contract_code=contract_code,
            volume=volume,
            side=sd,
            order_price_type="limit",
            price=px,
            channel_code=self.broker_id or "",
            margin_mode=self._default_margin_mode(),
            reduce_only=reduce_only,
            position_side=ps,
        )
        swap_coid = self._format_swap_client_order_id(client_order_id)
        if swap_coid is not None:
            body["client_order_id"] = swap_coid
        return self._place_swap_order_with_mode_fallback(
            symbol=symbol, contract_code=contract_code, body=body, pos_side=pos_side
        )

    def cancel_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        if self.market_type == "spot":
            if order_id:
                return self._spot_private_request("POST", f"/v1/order/orders/{str(order_id)}/submitcancel")
            if client_order_id:
                return self._spot_private_request(
                    "POST",
                    "/v1/order/orders/submitCancelClientOrder",
                    json_body={"client-order-id": str(client_order_id)},
                )
            raise LiveTradingError("HTX cancel_order requires order_id or client_order_id")

        if not order_id and not client_order_id:
            raise LiveTradingError("HTX cancel_order requires order_id or client_order_id")
        body = htx_v5.build_cancel_body(
            contract_code=to_htx_contract_code(symbol),
            order_id=order_id,
            client_order_id=client_order_id,
        )
        return self._swap_v5_request("POST", "/v5/trade/cancel_order", json_body=body)

    def get_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        if self.market_type == "spot":
            if order_id:
                raw = self._spot_private_request("GET", f"/v1/order/orders/{str(order_id)}")
                data = raw.get("data") if isinstance(raw, dict) else {}
                return data if isinstance(data, dict) else {}
            if client_order_id:
                raw = self._spot_private_request(
                    "GET", "/v1/order/orders/getClientOrder", params={"clientOrderId": str(client_order_id)}
                )
                data = raw.get("data") if isinstance(raw, dict) else {}
                return data if isinstance(data, dict) else {}
            raise LiveTradingError("HTX get_order requires order_id or client_order_id")

        if not order_id and not client_order_id:
            raise LiveTradingError("HTX get_order requires order_id or client_order_id")
        params = htx_v5.build_order_query_params(
            contract_code=to_htx_contract_code(symbol),
            order_id=order_id,
            client_order_id=client_order_id,
        )
        try:
            raw = self._swap_v5_request("GET", "/v5/trade/order", params=params)
        except LiveTradingError:
            raw = self._swap_v5_request("GET", "/v5/trade/order/details", params=params)
        return htx_v5.normalize_order_detail(raw)

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
        contract_size = 1.0
        if self.market_type != "spot":
            try:
                info = self.get_contract_info(symbol=str(symbol)) or {}
                cs = float(info.get("contract_size") or info.get("contractSize") or 0.0)
                if cs > 0:
                    contract_size = cs
            except Exception:
                contract_size = 1.0

        while True:
            timed_out = time.time() >= end_ts
            try:
                last = self.get_order(
                    symbol=symbol, order_id=str(order_id or ""), client_order_id=str(client_order_id or "")
                ) or {}
            except Exception:
                last = last or {}

            filled = 0.0
            avg_price = 0.0
            fee = 0.0
            fee_ccy = "USDT"
            status = str(last.get("status") or last.get("state") or "")
            try:
                if self.market_type == "spot":
                    filled = float(
                        last.get("field-amount")
                        or last.get("filled_amount")
                        or last.get("filled_qty")
                        or 0.0
                    )
                else:
                    contracts = float(
                        last.get("trade_volume")
                        or last.get("tradeVolume")
                        or 0.0
                    )
                    if contracts > 0:
                        filled = abs(contracts) * contract_size
                    else:
                        filled = float(
                            last.get("field-amount")
                            or last.get("filled_amount")
                            or last.get("filled_qty")
                            or 0.0
                        )
            except Exception:
                filled = 0.0
            try:
                avg_price = float(last.get("field-cash-amount") or 0.0)
                if filled > 0 and avg_price > 0:
                    avg_price = avg_price / filled
                else:
                    avg_price = float(
                        last.get("field-avg-price")
                        or last.get("trade_avg_price")
                        or last.get("avg_fill_price")
                        or last.get("price")
                        or 0.0
                    )
                    if avg_price <= 0 and self.market_type != "spot":
                        turnover = float(
                            last.get("trade_turnover")
                            or last.get("tradeTurnover")
                            or 0.0
                        )
                        if filled > 0 and turnover > 0:
                            avg_price = turnover / filled
            except Exception:
                avg_price = 0.0
            try:
                fee = abs(float(last.get("fee") or last.get("trade_fee") or 0.0))
            except Exception:
                fee = 0.0
            fee_ccy = str(last.get("fee_asset") or last.get("fee_currency") or fee_ccy or "").strip() or "USDT"

            if filled > 0 and avg_price > 0:
                if fee <= 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "status": status,
                    "order": last,
                }
            if str(status).lower() in (
                "filled", "partial-filled", "partial_filled", "canceled", "cancelled", "6", "7", "3", "4"
            ):
                if fee <= 0 and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "status": status,
                    "order": last,
                }
            if timed_out:
                return {
                    "filled": filled,
                    "avg_price": avg_price,
                    "fee": fee,
                    "fee_ccy": fee_ccy,
                    "status": status,
                    "order": last,
                }
            time.sleep(float(poll_interval_sec or 0.5))
