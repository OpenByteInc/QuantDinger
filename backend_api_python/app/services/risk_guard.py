"""QuantDinger 实盘风控守卫 (Risk Guard) — 实盘下单最后一道闸门。

集成点:StrategyV2OrderGateway.submit() 在 _validate() 之后、持久化之前调用。
规则:
  1. 账户回撤熔断:连续亏损达到阈值,停所有策略。
  2. 单标的仓位上限:单个标的市值 / 净值 <= ratio。
  3. 马丁死锁:马丁策略累计加仓次数达到上限,强制平仓。
  4. 单笔金额上限:单笔下单金额 <= 绝对上限且 <= 账户净值比例。
  5. 下单频率限制:滑动窗口内下单次数 <= max_orders_per_window。
配置:全部通过环境变量 RISK_GUARD_* 覆盖,默认值见 RiskConfig。
"""

from __future__ import annotations

import os
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Tuple

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _env_decimal(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class RiskConfig:
    max_drawdown_pct: float = _env_decimal("RISK_GUARD_MAX_DRAWDOWN_PCT", 0.20)
    max_position_ratio: float = _env_decimal("RISK_GUARD_MAX_POSITION_RATIO", 0.30)
    martingale_max_layers: int = _env_int("RISK_GUARD_MARTINGALE_MAX_LAYERS", 6)
    martingale_max_leverage: float = _env_decimal("RISK_GUARD_MARTINGALE_MAX_LEVERAGE", 4.0)
    max_order_notional: float = _env_decimal("RISK_GUARD_MAX_ORDER_NOTIONAL", 100000.0)
    max_order_ratio: float = _env_decimal("RISK_GUARD_MAX_ORDER_RATIO", 0.10)
    max_orders_per_window: int = _env_int("RISK_GUARD_MAX_ORDERS_PER_WINDOW", 20)
    order_window_seconds: int = _env_int("RISK_GUARD_ORDER_WINDOW_SECONDS", 60)
    cooldown_seconds: int = _env_int("RISK_GUARD_COOLDOWN_SECONDS", 300)


@dataclass
class AccountState:
    net_value: float
    peak_net_value: float
    positions: dict = field(default_factory=dict)
    drawdown_halted: bool = False
    halt_until: float = 0.0


@dataclass
class RiskOrder:
    symbol: str
    side: str
    notional: float
    strategy_type: str = "signal"
    martingale_layers: int = 0
    martingale_leverage: float = 1.0


class RiskGuard:
    """Thread-safe risk guard for live order pre-checks."""

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def shared(cls, cfg: RiskConfig | None = None) -> "RiskGuard":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(cfg or RiskConfig())
            return cls._instance

    def __init__(self, cfg: RiskConfig | None = None) -> None:
        self.cfg = cfg or RiskConfig()
        self._order_times: Deque[float] = deque()
        self._lock = threading.Lock()

    def _drawdown_ratio(self, acct: AccountState) -> float:
        if acct.peak_net_value <= 0:
            return 0.0
        return (acct.peak_net_value - acct.net_value) / acct.peak_net_value

    def _check_drawdown(self, acct: AccountState) -> Tuple[bool, List[str]]:
        now = time.time()
        if acct.drawdown_halted and now < acct.halt_until:
            return False, [f"drawdown_halted: cooldown until {int(acct.halt_until)}"]
        if self._drawdown_ratio(acct) >= self.cfg.max_drawdown_pct:
            acct.drawdown_halted = True
            acct.halt_until = now + self.cfg.cooldown_seconds
            logger.warning(
                "RiskGuard drawdown halt: %.2f%% >= %.2f%%",
                self._drawdown_ratio(acct) * 100,
                self.cfg.max_drawdown_pct * 100,
            )
            return False, [
                f"drawdown_halted: drawdown {self._drawdown_ratio(acct):.2%} >= "
                f"{self.cfg.max_drawdown_pct:.2%}"
            ]
        return True, []

    def _check_position_ratio(self, order: RiskOrder, acct: AccountState) -> Tuple[bool, List[str]]:
        current = float(acct.positions.get(order.symbol, 0))
        after = current + (order.notional if order.side == "buy" else -order.notional)
        after = max(after, 0.0)
        ratio = after / acct.net_value if acct.net_value > 0 else 1.0
        if ratio > self.cfg.max_position_ratio:
            return False, [
                f"position_ratio: {order.symbol} {ratio:.2%} > "
                f"{self.cfg.max_position_ratio:.2%}"
            ]
        return True, []

    def _check_martingale(self, order: RiskOrder) -> Tuple[bool, List[str]]:
        if order.strategy_type != "martingale":
            return True, []
        reasons: List[str] = []
        if order.martingale_layers >= self.cfg.martingale_max_layers:
            reasons.append(
                f"martingale_layers: {order.martingale_layers} >= "
                f"{self.cfg.martingale_max_layers}"
            )
        if order.martingale_leverage > self.cfg.martingale_max_leverage:
            reasons.append(
                f"martingale_leverage: {order.martingale_leverage}x > "
                f"{self.cfg.martingale_max_leverage}x"
            )
        return (False, reasons) if reasons else (True, [])

    def _check_order_size(self, order: RiskOrder, acct: AccountState) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if order.notional > self.cfg.max_order_notional:
            reasons.append(f"order_notional: {order.notional} > {self.cfg.max_order_notional}")
        ratio = order.notional / acct.net_value if acct.net_value > 0 else 1.0
        if ratio > self.cfg.max_order_ratio:
            reasons.append(f"order_ratio: {ratio:.2%} > {self.cfg.max_order_ratio:.2%}")
        return (False, reasons) if reasons else (True, [])

    def _check_rate(self) -> Tuple[bool, List[str]]:
        now = time.time()
        while self._order_times and self._order_times[0] < now - self.cfg.order_window_seconds:
            self._order_times.popleft()
        if len(self._order_times) >= self.cfg.max_orders_per_window:
            return False, [
                f"rate_limit: {len(self._order_times)} orders/{self.cfg.order_window_seconds}s >= "
                f"{self.cfg.max_orders_per_window}"
            ]
        return True, []

    def check(self, order: RiskOrder, acct: AccountState) -> Tuple[bool, List[str]]:
        with self._lock:
            reasons: List[str] = []
            for fn, args in (
                (self._check_drawdown, (acct,)),
                (self._check_rate, ()),
                (self._check_order_size, (order, acct)),
                (self._check_position_ratio, (order, acct)),
                (self._check_martingale, (order,)),
            ):
                ok, r = fn(*args)
                if not ok:
                    reasons += r
            allowed = not reasons
            if allowed:
                self._order_times.append(time.time())
            else:
                logger.warning(
                    "RiskGuard blocked %s %s: %s", order.symbol, order.side, reasons
                )
            return allowed, reasons
