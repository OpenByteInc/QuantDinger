"""Shared position-protection semantics for backtest and live execution."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

import pandas as pd


@dataclass(frozen=True)
class ProtectionSpec:
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_stop_pct: float = 0.0
    trailing_activation_pct: float = 0.0
    trailing_stop_points: float = 0.0
    trailing_activation_points: float = 0.0
    exit_fraction: float = 1.0
    stop_loss_exit_fraction: float = 0.0
    take_profit_exit_fraction: float = 0.0
    trailing_exit_fraction: float = 0.0
    time_limit_exit_fraction: float = 0.0
    time_limit_seconds: int = 0
    trailing_rebase_on_scale_in: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "stop_loss_pct", _ratio(self.stop_loss_pct, maximum=1.0))
        object.__setattr__(self, "take_profit_pct", _ratio(self.take_profit_pct, maximum=5.0))
        object.__setattr__(self, "stop_loss_price", _nonnegative(self.stop_loss_price))
        object.__setattr__(self, "take_profit_price", _nonnegative(self.take_profit_price))
        object.__setattr__(self, "trailing_stop_pct", _ratio(self.trailing_stop_pct, maximum=1.0))
        object.__setattr__(
            self,
            "trailing_activation_pct",
            _ratio(self.trailing_activation_pct, maximum=5.0),
        )
        object.__setattr__(self, "trailing_stop_points", _nonnegative(self.trailing_stop_points))
        object.__setattr__(
            self,
            "trailing_activation_points",
            _nonnegative(self.trailing_activation_points),
        )
        object.__setattr__(self, "exit_fraction", _exit_fraction(self.exit_fraction))
        for name in (
            "stop_loss_exit_fraction",
            "take_profit_exit_fraction",
            "trailing_exit_fraction",
            "time_limit_exit_fraction",
        ):
            object.__setattr__(self, name, _optional_exit_fraction(getattr(self, name)))
        object.__setattr__(self, "time_limit_seconds", max(0, int(self.time_limit_seconds or 0)))
        object.__setattr__(
            self,
            "trailing_rebase_on_scale_in",
            _boolean(self.trailing_rebase_on_scale_in, default=True),
        )

    @property
    def enabled(self) -> bool:
        return any((
            self.stop_loss_pct > 0,
            self.take_profit_pct > 0,
            self.stop_loss_price > 0,
            self.take_profit_price > 0,
            self.trailing_stop_pct > 0,
            self.trailing_stop_points > 0,
            self.time_limit_seconds > 0,
        ))

    def metadata(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_value(cls, value: object = None, **overrides: Any) -> "ProtectionSpec | None":
        source: dict[str, Any] = {}
        if isinstance(value, ProtectionSpec):
            source.update(value.metadata())
        elif isinstance(value, Mapping):
            source.update(value)
        source.update({key: item for key, item in overrides.items() if item is not None})
        trailing = source.get("trailing")
        if isinstance(trailing, Mapping):
            source.setdefault("trailing_stop_pct", trailing.get("pct"))
            source.setdefault("trailing_activation_pct", trailing.get("activation_pct"))
            source.setdefault("trailing_stop_points", trailing.get("points"))
            source.setdefault("trailing_activation_points", trailing.get("activation_points"))
        spec = cls(
            stop_loss_pct=source.get("stop_loss_pct"),
            take_profit_pct=source.get("take_profit_pct"),
            stop_loss_price=source.get("stop_loss_price"),
            take_profit_price=source.get("take_profit_price"),
            trailing_stop_pct=source.get("trailing_stop_pct"),
            trailing_activation_pct=source.get("trailing_activation_pct"),
            trailing_stop_points=source.get("trailing_stop_points"),
            trailing_activation_points=source.get("trailing_activation_points"),
            exit_fraction=source.get("exit_fraction", 1.0),
            stop_loss_exit_fraction=source.get("stop_loss_exit_fraction"),
            take_profit_exit_fraction=source.get("take_profit_exit_fraction"),
            trailing_exit_fraction=source.get("trailing_exit_fraction"),
            time_limit_exit_fraction=source.get("time_limit_exit_fraction"),
            time_limit_seconds=source.get("time_limit_seconds"),
            trailing_rebase_on_scale_in=source.get(
                "trailing_rebase_on_scale_in",
                True,
            ),
        )
        return spec if spec.enabled else None

    def exit_fraction_for(self, reason: str) -> float:
        override = {
            "stop_loss": self.stop_loss_exit_fraction,
            "take_profit": self.take_profit_exit_fraction,
            "trailing_stop": self.trailing_exit_fraction,
            "time_limit": self.time_limit_exit_fraction,
        }.get(str(reason), 0.0)
        return override if override > 0 else self.exit_fraction

    def enabled_reasons(self) -> frozenset[str]:
        reasons: set[str] = set()
        if self.stop_loss_price > 0 or self.stop_loss_pct > 0:
            reasons.add("stop_loss")
        if self.take_profit_price > 0 or self.take_profit_pct > 0:
            reasons.add("take_profit")
        if self.trailing_stop_points > 0 or self.trailing_stop_pct > 0:
            reasons.add("trailing_stop")
        if self.time_limit_seconds > 0:
            reasons.add("time_limit")
        return frozenset(reasons)


@dataclass
class ProtectionState:
    symbol: str
    side: str
    entry_price: float
    spec: ProtectionSpec
    opened_at: pd.Timestamp
    highest_price: float
    lowest_price: float
    trailing_active: bool = False
    disabled_reasons: set[str] = field(default_factory=set)

    @classmethod
    def open(
        cls,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        spec: ProtectionSpec,
        opened_at: object,
    ) -> "ProtectionState":
        price = float(entry_price or 0.0)
        return cls(
            symbol=str(symbol),
            side=str(side).lower(),
            entry_price=price,
            spec=spec,
            opened_at=pd.Timestamp(opened_at),
            highest_price=price,
            lowest_price=price,
            trailing_active=False,
        )

    def apply_scale_in(
        self,
        *,
        entry_price: float,
        fill_price: float,
        spec: ProtectionSpec | None = None,
        scaled_at: object = None,
    ) -> None:
        """Update a same-side basket without discarding active protection."""
        self.entry_price = float(entry_price or self.entry_price)
        if spec is not None:
            self.spec = spec
            self.disabled_reasons.clear()
        reference = float(fill_price or self.entry_price)
        if reference <= 0:
            reference = self.entry_price
        if self.spec.trailing_rebase_on_scale_in:
            # Compatibility mode: same behavior as the original runtime,
            # where a protected add-on opened a fresh protection state.
            self.trailing_active = False
            self.highest_price = max(self.entry_price, reference)
            self.lowest_price = min(self.entry_price, reference)
            if scaled_at is not None:
                self.opened_at = pd.Timestamp(scaled_at)
            return
        if self.trailing_active:
            return
        # Before activation the enlarged basket starts a fresh peak/trough
        # reference. Historical extremes belong to a smaller position.
        self.highest_price = max(self.entry_price, reference)
        self.lowest_price = min(self.entry_price, reference)

    def metadata(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "spec": self.spec.metadata(),
            "opened_at": self.opened_at.isoformat(),
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price,
            "trailing_active": bool(self.trailing_active),
            "disabled_reasons": sorted(self.disabled_reasons),
        }

    @classmethod
    def from_metadata(cls, value: Mapping[str, Any]) -> "ProtectionState | None":
        spec = ProtectionSpec.from_value(value.get("spec"))
        entry_price = _number(value.get("entry_price"))
        if spec is None or entry_price <= 0:
            return None
        state = cls.open(
            symbol=str(value.get("symbol") or ""),
            side=str(value.get("side") or "long"),
            entry_price=entry_price,
            spec=spec,
            opened_at=value.get("opened_at") or pd.Timestamp.utcnow(),
        )
        state.highest_price = max(entry_price, _number(value.get("highest_price"), entry_price))
        lowest = _number(value.get("lowest_price"), entry_price)
        state.lowest_price = min(entry_price, lowest if lowest > 0 else entry_price)
        state.trailing_active = bool(value.get("trailing_active"))
        state.disabled_reasons = {
            str(item)
            for item in (value.get("disabled_reasons") or [])
            if str(item) in spec.enabled_reasons()
        }
        return state

    @property
    def has_active_protection(self) -> bool:
        return bool(self.spec.enabled_reasons() - self.disabled_reasons)


@dataclass(frozen=True)
class ProtectionDecision:
    symbol: str
    side: str
    reason: str
    price: float
    trigger_price: float
    timestamp: pd.Timestamp


class ProtectionEngine:
    """Deterministic protection evaluator used by both execution modes."""

    VALID_INTRABAR_MODES = {"conservative", "balanced", "aggressive"}

    def __init__(self, *, intrabar_mode: str = "conservative") -> None:
        mode = str(intrabar_mode or "conservative").strip().lower()
        self.intrabar_mode = mode if mode in self.VALID_INTRABAR_MODES else "conservative"

    def evaluate_bar(
        self,
        state: ProtectionState,
        *,
        timestamp: object,
        open_price: float,
        high_price: float,
        low_price: float,
    ) -> ProtectionDecision | None:
        ts = pd.Timestamp(timestamp)
        open_ = _number(open_price)
        high = max(open_, _number(high_price, open_))
        low = min(open_, _number(low_price, open_))
        candidates = self._bar_candidates(state, ts=ts, open_=open_, high=high, low=low)
        decision = self._choose(candidates, open_)
        if decision is None:
            state.highest_price = max(state.highest_price, high)
            state.lowest_price = min(state.lowest_price, low)
        return decision

    def evaluate_price(
        self,
        state: ProtectionState,
        *,
        timestamp: object,
        price: float,
    ) -> ProtectionDecision | None:
        ts = pd.Timestamp(timestamp)
        current = _number(price)
        if current <= 0:
            return None
        previous_high = state.highest_price
        previous_low = state.lowest_price
        state.highest_price = max(state.highest_price, current)
        state.lowest_price = min(state.lowest_price, current)
        candidates = self._price_candidates(state, ts=ts, price=current)
        if candidates:
            return self._choose(candidates, current)
        if state.highest_price == previous_high and state.lowest_price == previous_low:
            return None
        return None

    def _bar_candidates(
        self,
        state: ProtectionState,
        *,
        ts: pd.Timestamp,
        open_: float,
        high: float,
        low: float,
    ) -> list[ProtectionDecision]:
        spec = state.spec
        entry = state.entry_price
        is_long = state.side == "long"
        candidates: list[ProtectionDecision] = []
        trigger = None if "stop_loss" in state.disabled_reasons else self._stop_trigger(state)
        if trigger is not None:
            touched = low <= trigger if is_long else high >= trigger
            gapped = open_ <= trigger if is_long else open_ >= trigger
            if touched:
                candidates.append(self._decision(state, "stop_loss", open_ if gapped else trigger, trigger, ts))
        trailing = (
            None
            if "trailing_stop" in state.disabled_reasons
            else self._trailing_trigger(state)
        )
        if trailing is not None:
            touched = low <= trailing if is_long else high >= trailing
            gapped = open_ <= trailing if is_long else open_ >= trailing
            if touched:
                candidates.append(self._decision(state, "trailing_stop", open_ if gapped else trailing, trailing, ts))
        trigger = (
            None
            if "take_profit" in state.disabled_reasons
            else self._take_profit_trigger(state)
        )
        if trigger is not None:
            touched = high >= trigger if is_long else low <= trigger
            gapped = open_ >= trigger if is_long else open_ <= trigger
            if touched:
                candidates.append(self._decision(state, "take_profit", open_ if gapped else trigger, trigger, ts))
        if "time_limit" not in state.disabled_reasons and self._time_limit_reached(state, ts):
            candidates.append(self._decision(state, "time_limit", open_, open_, ts))
        return candidates

    def _price_candidates(
        self,
        state: ProtectionState,
        *,
        ts: pd.Timestamp,
        price: float,
    ) -> list[ProtectionDecision]:
        spec = state.spec
        entry = state.entry_price
        is_long = state.side == "long"
        candidates: list[ProtectionDecision] = []
        trigger = None if "stop_loss" in state.disabled_reasons else self._stop_trigger(state)
        if trigger is not None:
            if (is_long and price <= trigger) or (not is_long and price >= trigger):
                candidates.append(self._decision(state, "stop_loss", price, trigger, ts))
        trailing = (
            None
            if "trailing_stop" in state.disabled_reasons
            else self._trailing_trigger(state)
        )
        if trailing is not None and ((is_long and price <= trailing) or (not is_long and price >= trailing)):
            candidates.append(self._decision(state, "trailing_stop", price, trailing, ts))
        trigger = (
            None
            if "take_profit" in state.disabled_reasons
            else self._take_profit_trigger(state)
        )
        if trigger is not None:
            if (is_long and price >= trigger) or (not is_long and price <= trigger):
                candidates.append(self._decision(state, "take_profit", price, trigger, ts))
        if "time_limit" not in state.disabled_reasons and self._time_limit_reached(state, ts):
            candidates.append(self._decision(state, "time_limit", price, price, ts))
        return candidates

    def _trailing_trigger(self, state: ProtectionState) -> float | None:
        spec = state.spec
        if spec.trailing_stop_points <= 0 and spec.trailing_stop_pct <= 0:
            return None
        if state.side == "long":
            activation = (
                state.entry_price + spec.trailing_activation_points
                if spec.trailing_activation_points > 0
                else state.entry_price * (1 + spec.trailing_activation_pct)
            )
            if (
                not state.trailing_active
                and state.highest_price >= activation
            ):
                state.trailing_active = True
            if not state.trailing_active:
                return None
            if spec.trailing_stop_points > 0:
                return state.highest_price - spec.trailing_stop_points
            return state.highest_price * (1 - spec.trailing_stop_pct)
        activation = (
            state.entry_price - spec.trailing_activation_points
            if spec.trailing_activation_points > 0
            else state.entry_price * (1 - spec.trailing_activation_pct)
        )
        if (
            not state.trailing_active
            and state.lowest_price <= activation
        ):
            state.trailing_active = True
        if not state.trailing_active:
            return None
        if spec.trailing_stop_points > 0:
            return state.lowest_price + spec.trailing_stop_points
        return state.lowest_price * (1 + spec.trailing_stop_pct)

    @staticmethod
    def _stop_trigger(state: ProtectionState) -> float | None:
        spec = state.spec
        is_long = state.side == "long"
        if spec.stop_loss_price > 0:
            if (is_long and spec.stop_loss_price < state.entry_price) or (
                not is_long and spec.stop_loss_price > state.entry_price
            ):
                return spec.stop_loss_price
        if spec.stop_loss_pct <= 0:
            return None
        return state.entry_price * (
            1 - spec.stop_loss_pct if is_long else 1 + spec.stop_loss_pct
        )

    @staticmethod
    def _take_profit_trigger(state: ProtectionState) -> float | None:
        spec = state.spec
        is_long = state.side == "long"
        if spec.take_profit_price > 0:
            if (is_long and spec.take_profit_price > state.entry_price) or (
                not is_long and spec.take_profit_price < state.entry_price
            ):
                return spec.take_profit_price
        if spec.take_profit_pct <= 0:
            return None
        return state.entry_price * (
            1 + spec.take_profit_pct if is_long else 1 - spec.take_profit_pct
        )

    @staticmethod
    def _time_limit_reached(state: ProtectionState, timestamp: pd.Timestamp) -> bool:
        limit = state.spec.time_limit_seconds
        return limit > 0 and (timestamp - state.opened_at).total_seconds() >= limit

    def _choose(
        self,
        candidates: list[ProtectionDecision],
        reference_price: float,
    ) -> ProtectionDecision | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if self.intrabar_mode == "aggressive":
            priority = {"take_profit": 0, "trailing_stop": 1, "time_limit": 2, "stop_loss": 3}
            return min(candidates, key=lambda item: priority.get(item.reason, 9))
        if self.intrabar_mode == "balanced":
            return min(candidates, key=lambda item: abs(item.trigger_price - reference_price))
        priority = {"stop_loss": 0, "trailing_stop": 1, "time_limit": 2, "take_profit": 3}
        return min(candidates, key=lambda item: priority.get(item.reason, 9))

    @staticmethod
    def _decision(
        state: ProtectionState,
        reason: str,
        price: float,
        trigger_price: float,
        timestamp: pd.Timestamp,
    ) -> ProtectionDecision:
        return ProtectionDecision(
            symbol=state.symbol,
            side=state.side,
            reason=reason,
            price=float(price),
            trigger_price=float(trigger_price),
            timestamp=timestamp,
        )


def _ratio(value: object, *, maximum: float) -> float:
    number = _number(value)
    return min(float(maximum), max(0.0, number))


def _nonnegative(value: object) -> float:
    return max(0.0, _number(value))


def _exit_fraction(value: object) -> float:
    number = _number(1.0 if value is None else value, float("nan"))
    if not 0 < number <= 1:
        raise ValueError("exit_fraction must be greater than 0 and at most 1")
    return number


def _optional_exit_fraction(value: object) -> float:
    if value is None:
        return 0.0
    number = _number(value, float("nan"))
    if number == 0:
        return 0.0
    if not 0 < number <= 1:
        raise ValueError("protection exit fraction overrides must be greater than 0 and at most 1")
    return number


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _boolean(value: object, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)
