"""Small building blocks for PendingOrderWorker live execution.

The old live execution path grew inside one method. These helpers hold the
stable pieces first: context, notification, client ids, side mapping, and fill
accumulation. Exchange-specific order phases can then be extracted safely on
top of this API.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from app.services.live_trading.base import LiveTradingError
from app.services.pending_orders.error_classification import exit_size_rejection_kind
from app.utils.db import get_db_connection
from app.utils.pnl import calc_notional_value
from app.utils.logger import get_logger
from app.services.pending_orders.execution_contract import (
    ExecutionContractError,
    resolve_queued_execution_contract,
    validate_current_strategy_mode,
)

logger = get_logger(__name__)


@dataclass
class LiveOrderExecutionContext:
    order_id: int
    order_row: Dict[str, Any]
    payload: Dict[str, Any]
    strategy_id: int
    signal_type: str
    symbol: str
    amount: float
    cfg: Dict[str, Any]
    strategy_user_id: int
    exchange_config: Dict[str, Any]
    safe_exchange_config: Dict[str, Any]
    exchange_id: str
    market_category: str
    market_type: str


class LiveOrderRejected(Exception):
    def __init__(
        self,
        *,
        error: str,
        strategy_id: int = 0,
        console_message: str = "",
        strategy_log: str = "",
        fatal_exchange_error: bool = False,
    ):
        super().__init__(error)
        self.error = error
        self.strategy_id = int(strategy_id or 0)
        self.console_message = console_message
        self.strategy_log = strategy_log
        self.fatal_exchange_error = bool(fatal_exchange_error)


@dataclass
class FillAccumulator:
    total_base: float = 0.0
    total_quote: float = 0.0
    total_fee: float = 0.0
    fee_ccy: str = ""
    fees_by_ccy: Dict[str, float] = field(default_factory=dict)
    fee_status: str = "pending"

    def apply_fill(self, filled_qty: float, avg_px: float) -> None:
        fq = float(filled_qty or 0.0)
        px = float(avg_px or 0.0)
        if fq > 0 and px > 0:
            self.total_base += fq
            self.total_quote += fq * px

    def apply_fee(self, fee: float, ccy: str = "") -> None:
        try:
            fv = float(fee or 0.0)
        except Exception:
            fv = 0.0
        if fv != 0 or ccy:
            key = str(ccy or "").strip().upper() or "UNKNOWN"
            self.fees_by_ccy[key] = self.fees_by_ccy.get(key, 0.0) + fv
            if len(self.fees_by_ccy) == 1:
                self.fee_ccy = "" if key == "UNKNOWN" else key
                self.total_fee = next(iter(self.fees_by_ccy.values()))
            else:
                self.fee_ccy = "MIXED"
                self.total_fee = 0.0

    def avg_price(self) -> float:
        return float(self.total_quote / self.total_base) if self.total_base > 0 else 0.0


def apply_execution_result(fills: FillAccumulator, result: Any) -> None:
    """Apply one normalized executor result, including its exchange fees."""
    fills.apply_fill(
        float(getattr(result, "filled_qty", 0.0) or 0.0),
        float(getattr(result, "avg_price", 0.0) or 0.0),
    )
    breakdown = getattr(result, "fees_by_ccy", {}) or {}
    if not isinstance(breakdown, dict):
        return
    for fee_currency, fee_amount in breakdown.items():
        fills.apply_fee(float(fee_amount or 0.0), str(fee_currency or ""))
    status = str(getattr(result, "fee_status", "") or "").strip().lower()
    if status in {"actual", "actual_zero"}:
        fills.fee_status = status
    elif fills.fees_by_ccy:
        fills.fee_status = "actual"


@dataclass
class LiveOrderNotifier:
    """Best-effort notification facade for live order execution."""

    order_id: int
    strategy_id: int
    order_row: Dict[str, Any]
    payload: Dict[str, Any]
    notifier: Any
    load_notification_config: Callable[[int], Dict[str, Any]]
    load_strategy_name: Callable[[int], str]
    resolved_exchange_id: str = ""
    resolved_credential_id: int = 0

    def notify(
        self,
        *,
        status: str,
        error: str = "",
        exchange_id: str = "",
        exchange_order_id: str = "",
        price_hint: Optional[float] = None,
        amount_hint: Optional[float] = None,
    ) -> None:
        notice_claim = ("", "")
        try:
            notification_config = self.payload.get("notification_config") or {}
            if (not notification_config) and self.strategy_id:
                notification_config = self.load_notification_config(int(self.strategy_id))
            if not notification_config:
                return

            if status == "failed":
                notice_claim = self._claim_exit_failure_notice(error, exchange_id)
                if notice_claim is None:
                    return
            elif status in {"sent", "filled"}:
                self._clear_exit_failure_notices(exchange_id)

            strategy_name = str(self.payload.get("strategy_name") or "").strip()
            if not strategy_name:
                strategy_name = self.load_strategy_name(int(self.strategy_id)) or f"Strategy_{self.strategy_id}"

            sym0 = self.payload.get("symbol") or self.order_row.get("symbol") or ""
            sig0 = self.payload.get("signal_type") or self.order_row.get("signal_type") or ""
            ref0 = float(self.payload.get("ref_price") or self.payload.get("price") or self.order_row.get("price") or 0.0)
            amt0 = float(self.payload.get("amount") or self.order_row.get("amount") or 0.0)

            px = float(price_hint) if (price_hint is not None and float(price_hint or 0.0) > 0) else ref0
            amt = float(amount_hint) if (amount_hint is not None and float(amount_hint or 0.0) > 0) else amt0

            stake_quote = calc_notional_value(float(px or 0.0), float(amt or 0.0)) or float(amt or 0.0)
            results = self.notifier.notify_signal(
                strategy_id=int(self.strategy_id),
                strategy_name=str(strategy_name or ""),
                symbol=str(sym0 or ""),
                signal_type=str(sig0 or ""),
                price=float(px or 0.0),
                stake_amount=float(stake_quote),
                direction=("short" if "short" in str(sig0 or "").lower() else "long"),
                notification_config=notification_config if isinstance(notification_config, dict) else {},
                extra={
                    "pending_order_id": int(self.order_id),
                    "mode": "live",
                    "status": str(status or ""),
                    "error": str(error or ""),
                    "exchange_id": str(exchange_id or ""),
                    "exchange_order_id": str(exchange_order_id or ""),
                },
            )
            ok_channels = [c for c, r in (results or {}).items() if (r or {}).get("ok")]
            fail_channels = [c for c, r in (results or {}).items() if not (r or {}).get("ok")]
            if not ok_channels:
                self._release_exit_failure_notice(notice_claim)
            if ok_channels or fail_channels:
                logger.info(
                    "live notify: pending_id=%s, strategy_id=%s, ok=%s fail=%s",
                    self.order_id,
                    self.strategy_id,
                    ",".join(ok_channels) if ok_channels else "-",
                    ",".join(fail_channels) if fail_channels else "-",
                )
        except Exception as e:
            self._release_exit_failure_notice(notice_claim)
            logger.info("live notify skipped/failed: pending_id=%s, strategy_id=%s, err=%s", self.order_id, self.strategy_id, e)

    def _exit_notice_scope(self, exchange_id: str) -> tuple[int, str]:
        signal = str(self.payload.get("signal_type") or self.order_row.get("signal_type") or "")
        if not signal.startswith(("close_", "reduce_")):
            return 0, ""
        scope = [
            self.order_row.get("user_id", 0),
            self.resolved_credential_id or self.order_row.get("credential_id") or self.payload.get("credential_id") or 0,
            self.resolved_exchange_id or self.order_row.get("exchange_id") or exchange_id or "",
            self.payload.get("symbol") or self.order_row.get("symbol") or "",
            self.payload.get("market_type") or self.order_row.get("market_type") or "",
            signal,
        ]
        digest = hashlib.sha256(json.dumps(scope, default=str).encode()).hexdigest()[:32]
        return 0, f"exit_notice:{digest}:"

    def _claim_exit_failure_notice(self, error: str, exchange_id: str) -> tuple[str, str] | None:
        kind = exit_size_rejection_kind(error)
        run_id, prefix = self._exit_notice_scope(exchange_id)
        if not kind or not prefix or self.strategy_id <= 0:
            return "", ""
        # No expiry: a restart or a new timestamp does not make dust tradable.
        quantity = float(self.payload.get("amount") or self.order_row.get("amount") or 0)
        fingerprint = hashlib.sha256(f"{kind}:{quantity:.12g}".encode()).hexdigest()[:32]
        key, token = prefix + fingerprint, uuid.uuid4().hex
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO strategy_runtime_state
                      (strategy_run_id, strategy_id, state_key, state_json, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (strategy_run_id, strategy_id, state_key) DO NOTHING
                    RETURNING state_key
                    """,
                    (run_id, self.strategy_id, key, json.dumps({"claim": token, "order_id": self.order_id})),
                )
                claimed = bool(cur.fetchone())
                db.commit()
                cur.close()
            return (key, token) if claimed else None
        except Exception:
            logger.warning("Exit notification deduplication unavailable: strategy=%s", self.strategy_id, exc_info=True)
            return "", ""

    def _release_exit_failure_notice(self, claim: tuple[str, str] | None) -> None:
        if not claim or not claim[0]:
            return
        try:
            run_id, _ = self._exit_notice_scope("")
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """DELETE FROM strategy_runtime_state
                       WHERE strategy_run_id = %s AND strategy_id = %s AND state_key = %s
                         AND state_json->>'claim' = %s""",
                    (run_id, self.strategy_id, claim[0], claim[1]),
                )
                db.commit()
                cur.close()
        except Exception:
            logger.warning("Exit notification claim release failed: strategy=%s", self.strategy_id, exc_info=True)

    def _clear_exit_failure_notices(self, exchange_id: str) -> None:
        run_id, prefix = self._exit_notice_scope(exchange_id)
        if not prefix or self.strategy_id <= 0:
            return
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """DELETE FROM strategy_runtime_state
                       WHERE strategy_run_id = %s AND strategy_id = %s AND state_key LIKE %s
                         AND (state_json->>'order_id')::bigint <= %s""",
                    (run_id, self.strategy_id, prefix + "%", self.order_id),
                )
                db.commit()
                cur.close()
        except Exception:
            logger.warning("Exit notification recovery reset failed: strategy=%s", self.strategy_id, exc_info=True)


def console_print(msg: str) -> None:
    try:
        print(str(msg or ""), flush=True)
    except Exception:
        pass


def make_client_order_id(*, exchange_id: str, strategy_id: int, order_id: int, phase: str = "") -> str:
    """Build a compact client order id accepted by all current live clients."""
    ph = str(phase or "").strip().lower()
    if str(exchange_id or "").strip().lower() == "okx":
        base = f"qd{int(strategy_id)}{int(order_id)}{ph}"
        base = "".join([c for c in base if c.isalnum()])
        if not base:
            base = f"qd{int(strategy_id)}{int(order_id)}"
        return base[:32]
    return f"qd_{int(strategy_id)}_{int(order_id)}{('_' + ph) if ph else ''}"


def signal_to_side_pos_reduce(signal_type: str) -> Tuple[str, str, bool]:
    st = (signal_type or "").strip().lower()
    if st in ("open_long", "add_long"):
        return "buy", "long", False
    if st in ("open_short", "add_short"):
        return "sell", "short", False
    if st in ("close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing"):
        return "sell", "long", True
    if st in ("close_short", "reduce_short", "close_short_stop", "close_short_profit", "close_short_trailing"):
        return "buy", "short", True
    raise LiveTradingError(f"Unsupported signal_type: {signal_type}")


def bind_instrument_product_contract(
    exchange_config: Dict[str, Any],
    trading_config: Dict[str, Any],
    *,
    symbol: str,
    exchange_id: str,
    market_type: str,
) -> Dict[str, Any]:
    """Bind a strategy product contract to client configuration."""
    products = trading_config.get("instrument_products") or []
    if not isinstance(products, list):
        products = []
    equity_products = [
        item
        for item in products
        if isinstance(item, dict)
        and str(item.get("product_type") or "crypto").strip().lower() != "crypto"
    ]
    if not equity_products:
        return dict(exchange_config)
    symbol_key = str(symbol or "").strip().upper()
    exchange_key = str(exchange_id or "").strip().lower()
    market_key = str(market_type or "spot").strip().lower()
    matching = next(
        (
            item
            for item in equity_products
            if str(item.get("symbol") or "").strip().upper() == symbol_key
            and str(item.get("exchange_id") or "").strip().lower() == exchange_key
            and str(item.get("market_type") or "spot").strip().lower() == market_key
        ),
        None,
    )
    if not matching:
        raise ValueError("strategyV2.instrumentProductContractMismatch")
    result = dict(exchange_config)
    result["api_family"] = str(matching.get("api_family") or market_key).strip().lower()
    result["instrument_product_type"] = str(matching.get("product_type") or "").strip().lower()
    result["instrument_id"] = str(matching.get("instrument_id") or "").strip()
    result["instrument_product_meta"] = dict(matching.get("product_meta") or {})
    return result


def attach_instrument_product_contracts(
    candidates: list[Dict[str, Any]],
    trading_config: Dict[str, Any],
    *,
    exchange_id: str,
) -> None:
    """Attach immutable deployment product metadata to live candidates."""
    products = trading_config.get("instrument_products") or []
    if not isinstance(products, list):
        products = []
    exchange_key = str(exchange_id or "").strip().lower()
    catalog_checked = bool(trading_config.get("_instrument_product_catalog_checked"))
    index: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for item in products:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("symbol") or "").strip().upper(),
            str(item.get("exchange_id") or "").strip().lower(),
            str(item.get("market_type") or "spot").strip().lower(),
        )
        if all(key):
            index[key] = item
    from app.services.market.product_catalog import get_catalog_product

    repaired = False
    for member in candidates:
        if str(member.get("market") or "").strip() != "Crypto":
            continue
        member_exchange = str(member.get("exchange_id") or exchange_key).strip().lower()
        market_type = str(member.get("market_type") or "spot").strip().lower()
        symbol = str(member.get("symbol") or "").strip().upper()
        key = (symbol, member_exchange, market_type)
        stored = index.get(key)
        stored_type = str((stored or {}).get("product_type") or "crypto").strip().lower()
        if stored and stored_type != "crypto":
            continue
        if stored and catalog_checked:
            continue
        current = get_catalog_product(
            market="Crypto",
            symbol=symbol,
            exchange_id=member_exchange,
            market_type=market_type,
        )
        if not current:
            continue
        current_type = str(current.get("product_type") or "crypto").strip().lower()
        if stored and current_type == "crypto":
            continue
        product = {
            "market": "Crypto",
            "symbol": symbol,
            "exchange_id": member_exchange,
            "market_type": market_type,
            "instrument_id": str(current.get("instrument_id") or "").strip(),
            "product_type": current_type,
            "api_family": str(current.get("api_family") or market_type).strip().lower(),
            "underlying_market": str(current.get("underlying_market") or "").strip(),
            "underlying_symbol": str(current.get("underlying_symbol") or "").strip(),
            "product_meta": dict(current.get("product_meta") or {}),
        }
        if stored:
            products[products.index(stored)] = product
        else:
            products.append(product)
        index[key] = product
        repaired = True
    if repaired:
        trading_config["instrument_products"] = products
    trading_config["_instrument_product_catalog_checked"] = True
    for member in candidates:
        if str(member.get("market") or "").strip() != "Crypto":
            continue
        member_exchange = str(member.get("exchange_id") or exchange_key).strip().lower()
        market_type = str(member.get("market_type") or "spot").strip().lower()
        product = index.get(
            (str(member.get("symbol") or "").strip().upper(), member_exchange, market_type)
        )
        if not product:
            continue
        member["exchange_id"] = member_exchange
        member["instrument_id"] = str(product.get("instrument_id") or "").strip()
        member["product_type"] = str(product.get("product_type") or "crypto").strip().lower()
        member["api_family"] = str(product.get("api_family") or market_type).strip().lower()
        member["underlying_market"] = str(product.get("underlying_market") or "").strip()
        member["underlying_symbol"] = str(product.get("underlying_symbol") or "").strip()
        member["product_meta"] = dict(product.get("product_meta") or {})


def build_live_order_context(
    *,
    order_id: int,
    order_row: Dict[str, Any],
    payload: Dict[str, Any],
    load_strategy_configs: Callable[[int], Dict[str, Any]],
    resolve_exchange_config: Callable[..., Dict[str, Any]],
    safe_exchange_config_for_log: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> LiveOrderExecutionContext:
    """Load and validate immutable context for a live pending order."""
    try:
        contract = resolve_queued_execution_contract(order_row, payload)
    except ExecutionContractError as exc:
        raise LiveOrderRejected(error=f"execution_contract:{exc.code}") from exc
    if contract.mode != "live":
        raise LiveOrderRejected(error="execution_contract:live_mode_required")

    strategy_id = contract.strategy_id
    signal_type = contract.signal_type
    symbol = contract.symbol
    amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
    if not symbol or not signal_type:
        raise LiveOrderRejected(
            error="missing_symbol_or_signal_type",
            strategy_id=strategy_id,
            console_message=f"[worker] order rejected: strategy_id={strategy_id} pending_id={order_id} missing symbol/signal_type",
            strategy_log="Order rejected: missing symbol or signal_type",
        )

    cfg = load_strategy_configs(strategy_id)
    try:
        validate_current_strategy_mode(contract, cfg.get("execution_mode"))
    except ExecutionContractError as exc:
        raise LiveOrderRejected(
            error=f"execution_contract:{exc.code}",
            strategy_id=strategy_id,
        ) from exc
    strategy_status = str(cfg.get("status") or "").strip().lower()
    if (
        strategy_status
        and strategy_status != "running"
        and str(signal_type).strip().lower() in {
            "open_long",
            "add_long",
            "open_short",
            "add_short",
        }
    ):
        raise LiveOrderRejected(
            error="strategy_not_running",
            strategy_id=strategy_id,
            console_message=(
                f"[worker] entry rejected: strategy_id={strategy_id} "
                f"pending_id={order_id} status={strategy_status}"
            ),
            strategy_log="Entry order cancelled because the strategy is no longer running",
        )
    strategy_user_id = int(cfg.get("user_id") or 1)
    exchange_config = resolve_exchange_config(cfg.get("exchange_config") or {}, user_id=strategy_user_id)
    exchange_id = str(exchange_config.get("exchange_id") or "").strip().lower()
    market_category = str(cfg.get("market_category") or "Crypto").strip()

    pre_market_type = (
        payload.get("market_type")
        or order_row.get("market_type")
        or cfg.get("market_type")
        or exchange_config.get("market_type")
        or "swap"
    )
    trading_cfg = cfg.get("trading_config") or {}

    from app.services.broker_market_policy import validate_strategy_config

    try:
        validate_strategy_config(
            exchange_id=exchange_id,
            market_category=market_category,
            market_type=pre_market_type,
            trade_direction=trading_cfg.get("trade_direction"),
            bot_type=trading_cfg.get("bot_type"),
            require_exchange=True,
        )
    except ValueError as e:
        err = f"policy_violation:{e}"
        raise LiveOrderRejected(
            error=err,
            strategy_id=strategy_id,
            console_message=f"[worker] order rejected by policy: strategy_id={strategy_id} pending_id={order_id} err={e}",
            strategy_log=f"Order rejected: {e}",
        )

    market_type = str(pre_market_type or "swap").strip().lower()
    if market_type in ("futures", "future", "perp", "perpetual"):
        market_type = "swap"

    try:
        exchange_config = bind_instrument_product_contract(
            exchange_config,
            trading_cfg,
            symbol=str(symbol),
            exchange_id=exchange_id,
            market_type=market_type,
        )
    except ValueError as exc:
        raise LiveOrderRejected(
            error=str(exc),
            strategy_id=strategy_id,
            strategy_log="Order rejected: instrument product contract mismatch",
        ) from exc

    safe_cfg = safe_exchange_config_for_log(exchange_config)

    return LiveOrderExecutionContext(
        order_id=int(order_id),
        order_row=order_row,
        payload=payload,
        strategy_id=strategy_id,
        signal_type=str(signal_type),
        symbol=str(symbol),
        amount=float(amount or 0.0),
        cfg=cfg,
        strategy_user_id=strategy_user_id,
        exchange_config=exchange_config,
        safe_exchange_config=safe_cfg,
        exchange_id=exchange_id,
        market_category=market_category,
        market_type=market_type,
    )
