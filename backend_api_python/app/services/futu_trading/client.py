"""
Futu OpenAPI trading client.

Wraps futu-api OpenQuoteContext / OpenSecTradeContext behind a surface
compatible with IBKRClient / AlpacaClient used by PendingOrderWorker.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.services.futu_trading.config import FutuConfig, validate_opend_host
from app.services.futu_trading.mappers import (
    account_row_to_dict,
    classify_futu_error,
    is_final_fill_status,
    normalize_order_status,
    order_row_to_raw,
    order_type_to_futu,
    position_row_to_dict,
    safe_float,
    side_from_futu,
    side_to_futu,
)
from app.services.futu_trading.symbols import (
    format_display_symbol,
    from_futu_code,
    infer_market_category,
    to_futu_code,
)
from app.services.futu_trading.timezones import futu_time_key_to_timestamp
from app.utils.logger import get_logger

logger = get_logger(__name__)

_futu_modules = None


def _ensure_futu():
    """Lazy-import futu-api so other brokers still work without it installed."""
    global _futu_modules
    if _futu_modules is None:
        try:
            import futu as ft
        except ImportError as exc:
            raise ImportError(
                "futu-api is not installed. Run: pip install futu-api"
            ) from exc
        _futu_modules = ft
    return _futu_modules


@dataclass
class OrderResult:
    """Order execution result (mirrors ibkr_trading.OrderResult)."""

    success: bool
    order_id: str = ""
    filled: float = 0.0
    avg_price: float = 0.0
    status: str = ""
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class FutuClient:
    """
    Futu securities trading client via OpenD.

    Usage:
        config = FutuConfig(host="127.0.0.1", port=11111, trade_env="demo")
        client = FutuClient(config)
        if client.connect():
            client.place_market_order("00700.HK", "buy", 100, "HKStock")
            client.disconnect()
    """

    def __init__(self, config: Optional[FutuConfig] = None):
        self.config = config or FutuConfig()
        self._quote_ctx = None
        self._trade_ctx = None
        self._connected = False
        self._lock = threading.RLock()
        self._acc_id = int(self.config.acc_id or 0)
        self._accounts: List[Dict[str, Any]] = []
        self._order_handlers: List[Callable[[Dict[str, Any]], None]] = []
        self._deal_handlers: List[Callable[[Dict[str, Any]], None]] = []
        self._push_handler = None
        self._need_quote = True

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        if not self._connected or self._trade_ctx is None:
            return False
        if self._need_quote and self._quote_ctx is None:
            return False
        return True

    def connect(self, need_quote: bool = True) -> bool:
        with self._lock:
            self._need_quote = bool(need_quote)
            if self.connected:
                return True
            try:
                validate_opend_host(self.config.host)
                ft = _ensure_futu()
                encrypt = self.config.is_encrypt
                quote_kwargs: Dict[str, Any] = {
                    "host": self.config.host,
                    "port": int(self.config.port),
                }
                trade_kwargs: Dict[str, Any] = {
                    "host": self.config.host,
                    "port": int(self.config.port),
                    "filter_trdmarket": self._trd_market_enum(ft),
                    "security_firm": self._security_firm_enum(ft),
                }
                if encrypt is not None:
                    quote_kwargs["is_encrypt"] = encrypt
                    trade_kwargs["is_encrypt"] = encrypt

                logger.info(
                    "Connecting to FutuOpenD %s:%s env=%s market=%s firm=%s need_quote=%s",
                    self.config.host,
                    self.config.port,
                    self.config.trade_env,
                    self.config.trade_market,
                    self.config.security_firm,
                    self._need_quote,
                )
                if self._need_quote:
                    self._quote_ctx = ft.OpenQuoteContext(**quote_kwargs)
                    ret, data = self._quote_ctx.get_global_state()
                    if ret != ft.RET_OK:
                        raise RuntimeError(f"OpenD quote probe failed: {data}")
                self._trade_ctx = ft.OpenSecTradeContext(**trade_kwargs)
                self._refresh_accounts_unlocked()
                if self.config.trade_env == "live" and self.config.unlock_password:
                    self._unlock_trade_unlocked()

                self._connected = True
                logger.info(
                    "Futu connected host=%s:%s acc_id=%s accounts=%s quote=%s",
                    self.config.host,
                    self.config.port,
                    self._acc_id or "auto",
                    len(self._accounts),
                    self._quote_ctx is not None,
                )
                return True
            except Exception as exc:
                logger.error("Futu connection failed: %s", exc)
                self._cleanup_contexts()
                self._connected = False
                return False

    def disconnect(self, force: bool = False) -> None:
        if getattr(self, "_futu_pooled", False) and not force:
            from app.services.futu_trading.session_pool import get_futu_session_pool

            get_futu_session_pool().release(self)
            return
        with self._lock:
            self._cleanup_contexts()
            self._connected = False
            logger.info("Futu disconnected")

    def _cleanup_contexts(self) -> None:
        for attr in ("_trade_ctx", "_quote_ctx"):
            ctx = getattr(self, attr, None)
            if ctx is None:
                continue
            try:
                ctx.close()
            except Exception as exc:
                logger.debug("Futu context close error: %s", exc)
            setattr(self, attr, None)
        self._push_handler = None

    def _ensure_connected(self) -> None:
        if not self.connected:
            if not self.connect(need_quote=self._need_quote):
                raise ConnectionError("Cannot connect to FutuOpenD")

    # ------------------------------------------------------------------
    # Enum helpers
    # ------------------------------------------------------------------

    def _trd_env(self, ft) -> Any:
        return ft.TrdEnv.SIMULATE if self.config.is_simulate else ft.TrdEnv.REAL

    def _trd_market_enum(self, ft) -> Any:
        market = (self.config.trade_market or "NONE").upper()
        mapping = {
            "HK": getattr(ft.TrdMarket, "HK", None),
            "US": getattr(ft.TrdMarket, "US", None),
            "NONE": getattr(ft.TrdMarket, "NONE", None),
            "CN": getattr(ft.TrdMarket, "CN", None),
        }
        return mapping.get(market) or ft.TrdMarket.NONE

    def _security_firm_enum(self, ft) -> Any:
        firm = (self.config.security_firm or "FUTUSECURITIES").upper()
        return getattr(ft.SecurityFirm, firm, ft.SecurityFirm.FUTUSECURITIES)

    def _side_enum(self, ft, side: str) -> Any:
        name = side_to_futu(side)
        return getattr(ft.TrdSide, name, ft.TrdSide.BUY)

    def _order_type_enum(self, ft, order_type: str) -> Any:
        name = order_type_to_futu(order_type)
        return getattr(ft.OrderType, name, ft.OrderType.MARKET)

    # ------------------------------------------------------------------
    # Accounts / unlock
    # ------------------------------------------------------------------

    def _refresh_accounts_unlocked(self) -> None:
        ft = _ensure_futu()
        ret, data = self._trade_ctx.get_acc_list()
        accounts: List[Dict[str, Any]] = []
        if ret == ft.RET_OK and data is not None:
            try:
                records = data.to_dict("records") if hasattr(data, "to_dict") else list(data)
            except Exception:
                records = []
            for row in records:
                try:
                    acc_id = int(row.get("acc_id") or row.get("accid") or 0)
                except Exception:
                    acc_id = 0
                accounts.append({
                    "acc_id": acc_id,
                    "trd_env": str(row.get("trd_env") or ""),
                    "acc_type": str(row.get("acc_type") or ""),
                    "uni_card_num": str(row.get("uni_card_num") or ""),
                    "card_num": str(row.get("card_num") or ""),
                    "security_firm": str(row.get("security_firm") or ""),
                    "sim_acc_type": str(row.get("sim_acc_type") or ""),
                    "trdmarket_auth": row.get("trdmarket_auth"),
                })
        self._accounts = accounts
        if self._acc_id <= 0 and accounts:
            # Prefer matching env when possible
            env_name = "SIMULATE" if self.config.is_simulate else "REAL"
            preferred = [
                a for a in accounts
                if env_name in str(a.get("trd_env") or "").upper()
            ]
            chosen = preferred[0] if preferred else accounts[0]
            self._acc_id = int(chosen.get("acc_id") or 0)

    def _unlock_trade_unlocked(self) -> None:
        ft = _ensure_futu()
        password = self.config.unlock_password
        if not password:
            return
        ret, data = self._trade_ctx.unlock_trade(password)
        if ret != ft.RET_OK:
            code, msg = classify_futu_error(data)
            raise RuntimeError(f"{code}:{msg}")

    def unlock_trade(self, password: Optional[str] = None) -> bool:
        with self._lock:
            self._ensure_connected()
            ft = _ensure_futu()
            pwd = password if password is not None else self.config.unlock_password
            if not pwd:
                # GUI OpenD may already be unlocked
                return True
            ret, data = self._trade_ctx.unlock_trade(pwd)
            if ret != ft.RET_OK:
                logger.error("Futu unlock_trade failed: %s", data)
                return False
            return True

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_connection_status(self) -> Dict[str, Any]:
        status = {
            "connected": self.connected,
            "host": self.config.host,
            "port": self.config.port,
            "trade_env": self.config.trade_env,
            "trade_market": self.config.trade_market,
            "security_firm": self.config.security_firm,
            "acc_id": self._acc_id or None,
            "accounts": [
                {k: v for k, v in acc.items() if k != "uni_card_num"}
                for acc in self._accounts
            ],
        }
        status["need_quote"] = bool(self._need_quote)
        status["quote_ctx"] = self._quote_ctx is not None
        status["trade_ctx"] = self._trade_ctx is not None
        if not self.connected or self._quote_ctx is None:
            return status
        try:
            ft = _ensure_futu()
            ret, data = self._quote_ctx.get_global_state()
            if ret == ft.RET_OK and isinstance(data, dict):
                status["opend"] = {
                    "quote_login": data.get("qot_logined") or data.get("market_sz"),
                    "trade_login": data.get("trd_logined"),
                    "server_ver": data.get("server_ver"),
                    "login_user_id": data.get("login_user_id"),
                }
        except Exception as exc:
            status["opend_error"] = str(exc)
        return status

    def probe_permissions(self) -> Dict[str, Any]:
        """Best-effort market / quote permission snapshot (no orders)."""
        self._ensure_connected()
        result: Dict[str, Any] = {
            "trade_env": self.config.trade_env,
            "accounts": self._accounts,
            "quote_ok": False,
            "trade_ok": False,
            "sample_quote": None,
            "errors": [],
        }
        sample_code = "HK.00700" if self.config.trade_market != "US" else "US.AAPL"
        try:
            quote = self.get_quote(format_display_symbol(sample_code), infer_market_category(sample_code))
            result["quote_ok"] = bool(quote.get("success"))
            result["sample_quote"] = quote
            if not quote.get("success"):
                result["errors"].append(quote.get("error") or "quote_failed")
        except Exception as exc:
            result["errors"].append(str(exc))
        try:
            acc = self.get_account_summary()
            result["trade_ok"] = bool(acc.get("success"))
            result["account"] = acc
            if not acc.get("success"):
                result["errors"].append(acc.get("error") or "account_failed")
        except Exception as exc:
            result["errors"].append(str(exc))
        return result

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def _acc_id_arg(self) -> int:
        return int(self._acc_id or 0)

    def _place_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str,
        market_type: str = "",
        remark: str = "",
    ) -> OrderResult:
        try:
            with self._lock:
                self._ensure_connected()
                ft = _ensure_futu()
                market = market_type or infer_market_category(symbol)
                if market not in ("HKStock", "USStock"):
                    return OrderResult(success=False, message=f"Unsupported market_type: {market}")
                code = to_futu_code(symbol, market)
                qty = float(quantity or 0.0)
                if qty <= 0:
                    return OrderResult(success=False, message="quantity must be > 0")

                # Lot-size validation (best effort)
                lot = self._query_lot_size_unlocked(code)
                if lot and lot > 1:
                    # Reject non-multiples instead of silently rounding up
                    if abs(qty % lot) > 1e-8:
                        return OrderResult(
                            success=False,
                            message=f"FUTU_INVALID_LOT_SIZE: qty={qty} lot_size={lot}",
                        )

                if self.config.trade_env == "live" and self.config.unlock_password:
                    self._unlock_trade_unlocked()

                ot = order_type_to_futu(order_type)
                px = float(price or 0.0)
                if ot == "MARKET" and px <= 0:
                    # Some markets still require a reference price for market orders.
                    snap = self._snapshot_unlocked(code)
                    px = safe_float(snap.get("last_price") or snap.get("price") or snap.get("last"))
                    if px <= 0:
                        px = 0.01

                kwargs = {
                    "price": px,
                    "qty": qty,
                    "code": code,
                    "trd_side": self._side_enum(ft, side),
                    "order_type": self._order_type_enum(ft, order_type),
                    "trd_env": self._trd_env(ft),
                    "acc_id": self._acc_id_arg(),
                }
                if remark:
                    kwargs["remark"] = str(remark)[:64]

                ret, data = self._trade_ctx.place_order(**kwargs)
                if ret != ft.RET_OK:
                    code_err, msg = classify_futu_error(data)
                    return OrderResult(success=False, message=f"{code_err}:{msg}", raw={"error": str(data)})

                row = None
                if hasattr(data, "iloc") and len(data) > 0:
                    row = data.iloc[0]
                elif isinstance(data, list) and data:
                    row = data[0]
                elif isinstance(data, dict):
                    row = data
                raw = order_row_to_raw(row)
                status = normalize_order_status(raw.get("status"))
                return OrderResult(
                    success=True,
                    order_id=str(raw.get("order_id") or ""),
                    filled=safe_float(raw.get("filled")),
                    avg_price=safe_float(raw.get("avg_price")),
                    status=status,
                    message="Order submitted",
                    raw=raw,
                )
        except Exception as exc:
            logger.error("Futu place_order failed: %s", exc)
            code_err, msg = classify_futu_error(exc)
            return OrderResult(success=False, message=f"{code_err}:{msg}")

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        market_type: str = "HKStock",
        remark: str = "",
        **_: Any,
    ) -> OrderResult:
        return self._place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=0.0,
            order_type="market",
            market_type=market_type,
            remark=remark,
        )

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        market_type: str = "HKStock",
        remark: str = "",
        **_: Any,
    ) -> OrderResult:
        if float(price or 0.0) <= 0:
            return OrderResult(success=False, message="limit price must be > 0")
        return self._place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=float(price),
            order_type="limit",
            market_type=market_type,
            remark=remark,
        )

    def cancel_order(self, order_id: str) -> bool:
        try:
            with self._lock:
                self._ensure_connected()
                ft = _ensure_futu()
                oid = str(order_id or "").strip()
                if not oid:
                    return False
                ret, data = self._trade_ctx.modify_order(
                    modify_order_op=ft.ModifyOrderOp.CANCEL,
                    order_id=oid,
                    qty=0,
                    price=0,
                    trd_env=self._trd_env(ft),
                    acc_id=self._acc_id_arg(),
                )
                if ret != ft.RET_OK:
                    logger.warning("Futu cancel_order failed: %s", data)
                    return False
                return True
        except Exception as exc:
            logger.error("Futu cancel_order exception: %s", exc)
            return False

    def get_order_status(self, order_id: str) -> OrderResult:
        try:
            with self._lock:
                self._ensure_connected()
                ft = _ensure_futu()
                oid = str(order_id or "").strip()
                if not oid:
                    return OrderResult(success=False, message="Missing order_id")
                ret, data = self._trade_ctx.order_list_query(
                    order_id=oid,
                    trd_env=self._trd_env(ft),
                    acc_id=self._acc_id_arg(),
                )
                if ret != ft.RET_OK:
                    code_err, msg = classify_futu_error(data)
                    return OrderResult(success=False, order_id=oid, message=f"{code_err}:{msg}")
                if data is None or (hasattr(data, "__len__") and len(data) == 0):
                    return OrderResult(
                        success=False,
                        order_id=oid,
                        message="Order not found in current query window",
                    )
                row = data.iloc[0] if hasattr(data, "iloc") else (data[0] if isinstance(data, list) else data)
                raw = order_row_to_raw(row)
                return OrderResult(
                    success=True,
                    order_id=str(raw.get("order_id") or oid),
                    filled=safe_float(raw.get("filled")),
                    avg_price=safe_float(raw.get("avg_price")),
                    status=normalize_order_status(raw.get("status")),
                    message=str(raw.get("message") or "OK"),
                    raw=raw,
                )
        except Exception as exc:
            logger.error("Futu get_order_status failed: %s", exc)
            return OrderResult(success=False, order_id=str(order_id or ""), message=str(exc))

    def find_order_by_remark(self, remark: str) -> Optional[OrderResult]:
        """Idempotency helper: locate an order by client remark after a timeout."""
        tag = str(remark or "").strip()
        if not tag:
            return None
        try:
            with self._lock:
                self._ensure_connected()
                ft = _ensure_futu()
                ret, data = self._trade_ctx.order_list_query(
                    trd_env=self._trd_env(ft),
                    acc_id=self._acc_id_arg(),
                )
                if ret != ft.RET_OK or data is None:
                    return None
                records = data.to_dict("records") if hasattr(data, "to_dict") else list(data)
                for row in records:
                    raw = order_row_to_raw(row)
                    if str(raw.get("remark") or "") == tag:
                        return OrderResult(
                            success=True,
                            order_id=str(raw.get("order_id") or ""),
                            filled=safe_float(raw.get("filled")),
                            avg_price=safe_float(raw.get("avg_price")),
                            status=normalize_order_status(raw.get("status")),
                            message="matched_by_remark",
                            raw=raw,
                        )
        except Exception as exc:
            logger.debug("find_order_by_remark failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Account / positions / quote
    # ------------------------------------------------------------------

    def get_account_summary(self) -> Dict[str, Any]:
        try:
            with self._lock:
                self._ensure_connected()
                ft = _ensure_futu()
                ret, data = self._trade_ctx.accinfo_query(
                    trd_env=self._trd_env(ft),
                    acc_id=self._acc_id_arg(),
                )
                if ret != ft.RET_OK:
                    return {"success": False, "error": str(data)}
                row = data.iloc[0] if hasattr(data, "iloc") and len(data) else data
                summary = account_row_to_dict(row)
                return {
                    "success": True,
                    "account": self._acc_id,
                    "trade_env": self.config.trade_env,
                    "summary": summary,
                }
        except Exception as exc:
            logger.error("Futu get_account_summary failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def get_positions(self) -> List[Dict[str, Any]]:
        try:
            with self._lock:
                self._ensure_connected()
                ft = _ensure_futu()
                ret, data = self._trade_ctx.position_list_query(
                    trd_env=self._trd_env(ft),
                    acc_id=self._acc_id_arg(),
                )
                if ret != ft.RET_OK or data is None:
                    logger.warning("Futu position_list_query failed: %s", data)
                    return []
                records = data.to_dict("records") if hasattr(data, "to_dict") else list(data)
                return [position_row_to_dict(row) for row in records]
        except Exception as exc:
            logger.error("Futu get_positions failed: %s", exc)
            return []

    def get_open_orders(self) -> List[Dict[str, Any]]:
        try:
            with self._lock:
                self._ensure_connected()
                ft = _ensure_futu()
                ret, data = self._trade_ctx.order_list_query(
                    trd_env=self._trd_env(ft),
                    acc_id=self._acc_id_arg(),
                    status_filter_list=[
                        ft.OrderStatus.SUBMITTED,
                        ft.OrderStatus.FILLED_PART,
                        ft.OrderStatus.WAITING_SUBMIT,
                        ft.OrderStatus.SUBMITTING,
                    ],
                )
                if ret != ft.RET_OK or data is None:
                    return []
                records = data.to_dict("records") if hasattr(data, "to_dict") else list(data)
                out = []
                for row in records:
                    raw = order_row_to_raw(row)
                    display, _ = from_futu_code(str(raw.get("code") or ""))
                    out.append({
                        "orderId": raw.get("order_id"),
                        "symbol": display,
                        "futu_code": raw.get("code"),
                        "action": raw.get("side"),
                        "quantity": raw.get("qty"),
                        "orderType": "limit" if safe_float(raw.get("price")) > 0 else "market",
                        "limitPrice": raw.get("price"),
                        "status": raw.get("status"),
                        "filled": raw.get("filled"),
                        "avgFillPrice": raw.get("avg_price"),
                        "remark": raw.get("remark"),
                    })
                return out
        except Exception as exc:
            logger.error("Futu get_open_orders failed: %s", exc)
            return []

    def get_quote(self, symbol: str, market_type: str = "HKStock") -> Dict[str, Any]:
        try:
            with self._lock:
                self._ensure_connected()
                if self._quote_ctx is None:
                    return {"success": False, "error": "quote context is not open"}
                code = to_futu_code(symbol, market_type or infer_market_category(symbol))
                snap = self._snapshot_unlocked(code)
                if not snap:
                    return {"success": False, "error": f"No quote for {code}"}
                last = safe_float(snap.get("last_price") or snap.get("price") or snap.get("last"))
                return {
                    "success": True,
                    "symbol": format_display_symbol(code),
                    "futu_code": code,
                    "bid": safe_float(snap.get("bid_price") or snap.get("bid")),
                    "ask": safe_float(snap.get("ask_price") or snap.get("ask")),
                    "last": last,
                    "high": safe_float(snap.get("high_price") or snap.get("high")),
                    "low": safe_float(snap.get("low_price") or snap.get("low")),
                    "volume": safe_float(snap.get("volume")),
                    "close": safe_float(snap.get("prev_close_price") or snap.get("close")),
                    "raw": snap,
                }
        except Exception as exc:
            code_err, msg = classify_futu_error(exc)
            logger.error("Futu get_quote failed: %s", msg)
            return {"success": False, "error": f"{code_err}:{msg}"}

    def _snapshot_unlocked(self, code: str) -> Dict[str, Any]:
        ft = _ensure_futu()
        ret, data = self._quote_ctx.get_market_snapshot([code])
        if ret != ft.RET_OK or data is None or len(data) == 0:
            raise RuntimeError(data if ret != ft.RET_OK else f"empty snapshot for {code}")
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        try:
            return dict(row)
        except Exception:
            return {"last_price": safe_float(getattr(row, "last_price", 0)), "code": code}

    def _query_lot_size_unlocked(self, code: str) -> int:
        if self._quote_ctx is None:
            return 0
        try:
            ft = _ensure_futu()
            ret, data = self._quote_ctx.get_market_snapshot([code])
            if ret != ft.RET_OK or data is None or len(data) == 0:
                return 0
            row = data.iloc[0]
            lot = int(safe_float(row.get("lot_size") if hasattr(row, "get") else getattr(row, "lot_size", 0)))
            return max(0, lot)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # History K-line (used by data_sources.futu)
    # ------------------------------------------------------------------

    def get_history_kline(
        self,
        symbol: str,
        *,
        market_type: str = "",
        ktype: str = "K_DAY",
        start: Optional[str] = None,
        end: Optional[str] = None,
        max_count: int = 500,
        autype: str = "QFQ",
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_connected()
            if self._quote_ctx is None:
                raise RuntimeError("quote context is not open")
            ft = _ensure_futu()
            market = market_type or infer_market_category(symbol)
            code = to_futu_code(symbol, market)
            ktype_enum = getattr(ft.KLType, ktype, ft.KLType.K_DAY)
            autype_enum = getattr(ft.AuType, autype, ft.AuType.QFQ)
            page_req_key = None
            rows: List[Dict[str, Any]] = []
            remaining = max(1, int(max_count or 500))
            while remaining > 0:
                batch = min(1000, remaining)
                ret, data, page_req_key = self._quote_ctx.request_history_kline(
                    code=code,
                    start=start,
                    end=end,
                    ktype=ktype_enum,
                    autype=autype_enum,
                    max_count=batch,
                    page_req_key=page_req_key,
                )
                if ret != ft.RET_OK:
                    code_err, msg = classify_futu_error(data)
                    raise RuntimeError(f"{code_err}:{msg}")
                if data is None or len(data) == 0:
                    break
                records = data.to_dict("records") if hasattr(data, "to_dict") else list(data)
                for rec in records:
                    ts = rec.get("time_key") or rec.get("time")
                    try:
                        unix_ts = futu_time_key_to_timestamp(ts, market)
                    except (TypeError, ValueError):
                        continue
                    rows.append({
                        "time": unix_ts,
                        "open": safe_float(rec.get("open")),
                        "high": safe_float(rec.get("high")),
                        "low": safe_float(rec.get("low")),
                        "close": safe_float(rec.get("close")),
                        "volume": safe_float(rec.get("volume")),
                    })
                remaining -= len(records)
                if not page_req_key:
                    break
            rows.sort(key=lambda x: x["time"])
            return rows

    # ------------------------------------------------------------------
    # Push handlers (execution stream)
    # ------------------------------------------------------------------

    def add_order_handler(self, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._order_handlers.append(handler)

    def add_deal_handler(self, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._deal_handlers.append(handler)

    def start_push(self) -> bool:
        """Subscribe trade order / deal push on the trade context."""
        try:
            with self._lock:
                self._ensure_connected()
                ft = _ensure_futu()

                client = self

                class _Handler(ft.TradeOrderHandlerBase if hasattr(ft, "TradeOrderHandlerBase") else object):
                    def on_recv_rsp(self, rsp_pb):  # type: ignore[no-untyped-def]
                        try:
                            ret, data = super().on_recv_rsp(rsp_pb)  # type: ignore[misc]
                        except Exception:
                            return
                        if ret != ft.RET_OK or data is None:
                            return
                        records = data.to_dict("records") if hasattr(data, "to_dict") else [data]
                        for row in records:
                            raw = order_row_to_raw(row)
                            for cb in list(client._order_handlers):
                                try:
                                    cb(raw)
                                except Exception as exc:
                                    logger.debug("Futu order handler error: %s", exc)

                class _DealHandler(ft.TradeDealHandlerBase if hasattr(ft, "TradeDealHandlerBase") else object):
                    def on_recv_rsp(self, rsp_pb):  # type: ignore[no-untyped-def]
                        try:
                            ret, data = super().on_recv_rsp(rsp_pb)  # type: ignore[misc]
                        except Exception:
                            return
                        if ret != ft.RET_OK or data is None:
                            return
                        records = data.to_dict("records") if hasattr(data, "to_dict") else [data]
                        for row in records:
                            payload = dict(row) if not isinstance(row, dict) else row
                            for cb in list(client._deal_handlers):
                                try:
                                    cb(payload)
                                except Exception as exc:
                                    logger.debug("Futu deal handler error: %s", exc)

                if hasattr(ft, "TradeOrderHandlerBase"):
                    self._trade_ctx.set_handler(_Handler())
                if hasattr(ft, "TradeDealHandlerBase"):
                    self._trade_ctx.set_handler(_DealHandler())
                return True
        except Exception as exc:
            logger.warning("Futu start_push failed: %s", exc)
            return False

    def subscribe_quote(self, symbols: List[str], market_type: str = "") -> bool:
        try:
            with self._lock:
                self._ensure_connected()
                if self._quote_ctx is None:
                    logger.warning("Futu subscribe_quote skipped: quote context is not open")
                    return False
                ft = _ensure_futu()
                codes = [to_futu_code(s, market_type or infer_market_category(s)) for s in symbols if s]
                if not codes:
                    return True
                ret, err = self._quote_ctx.subscribe(codes, [ft.SubType.QUOTE])
                if ret != ft.RET_OK:
                    code_err, msg = classify_futu_error(err)
                    logger.warning("Futu subscribe failed: %s:%s", code_err, msg)
                    return False
                return True
        except Exception as exc:
            logger.warning("Futu subscribe_quote exception: %s", exc)
            return False
