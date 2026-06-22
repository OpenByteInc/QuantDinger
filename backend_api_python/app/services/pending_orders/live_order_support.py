"""Small building blocks for PendingOrderWorker live execution.

The old live execution path grew inside one method. These helpers hold the
stable pieces first: context, notification, client ids, side mapping, and fill
accumulation. Exchange-specific order phases can then be extracted safely on
top of this API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from app.services.live_trading.base import LiveTradingError
from app.utils.pnl import calc_notional_value
from app.utils.logger import get_logger

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

    def apply_fill(self, filled_qty: float, avg_px: float) -> None:
        fq = float(filled_qty or 0.0)
        px = float(avg_px or 0.0)
        if fq > 0 and px > 0:
            self.total_base += fq
            self.total_quote += fq * px

    def apply_fee(self, fee: float, ccy: str = "") -> None:
        try:
            fv = abs(float(fee or 0.0))
        except Exception:
            fv = 0.0
        if fv > 0:
            self.total_fee += fv
            if (not self.fee_ccy) and ccy:
                self.fee_ccy = str(ccy or "")

    def avg_price(self) -> float:
        return float(self.total_quote / self.total_base) if self.total_base > 0 else 0.0


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
        try:
            notification_config = self.payload.get("notification_config") or {}
            if (not notification_config) and self.strategy_id:
                notification_config = self.load_notification_config(int(self.strategy_id))
            if not notification_config:
                return

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
            if ok_channels or fail_channels:
                logger.info(
                    "live notify: pending_id=%s, strategy_id=%s, ok=%s fail=%s",
                    self.order_id,
                    self.strategy_id,
                    ",".join(ok_channels) if ok_channels else "-",
                    ",".join(fail_channels) if fail_channels else "-",
                )
        except Exception as e:
            logger.info("live notify skipped/failed: pending_id=%s, strategy_id=%s, err=%s", self.order_id, self.strategy_id, e)


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
    strategy_id = int(payload.get("strategy_id") or order_row.get("strategy_id") or 0)
    if strategy_id <= 0:
        raise LiveOrderRejected(error="missing_strategy_id")

    signal_type = payload.get("signal_type") or order_row.get("signal_type")
    symbol = payload.get("symbol") or order_row.get("symbol")
    amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
    if not symbol or not signal_type:
        raise LiveOrderRejected(
            error="missing_symbol_or_signal_type",
            strategy_id=strategy_id,
            console_message=f"[worker] order rejected: strategy_id={strategy_id} pending_id={order_id} missing symbol/signal_type",
            strategy_log="Order rejected: missing symbol or signal_type",
        )

    cfg = load_strategy_configs(strategy_id)
    strategy_user_id = int(cfg.get("user_id") or 1)
    exchange_config = resolve_exchange_config(cfg.get("exchange_config") or {}, user_id=strategy_user_id)
    safe_cfg = safe_exchange_config_for_log(exchange_config)
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
