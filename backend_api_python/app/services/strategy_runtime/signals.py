"""Canonical strategy signal models for backtest, signal, and live paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional

import pandas as pd


CANONICAL_SIGNAL_ACTIONS = (
    "open_long",
    "close_long",
    "open_short",
    "close_short",
    "add_long",
    "add_short",
    "reduce_long",
    "reduce_short",
)

_QUOTE_FIELD_ALIASES = {
    "open_long": ("open_long_quote_amount",),
    "close_long": ("close_long_quote_amount",),
    "open_short": ("open_short_quote_amount",),
    "close_short": ("close_short_quote_amount",),
    "add_long": ("add_long_quote_amount",),
    "add_short": ("add_short_quote_amount",),
    "reduce_long": ("reduce_long_quote_amount",),
    "reduce_short": ("reduce_short_quote_amount",),
}

_BASE_FIELD_ALIASES = {
    "open_long": ("open_long_base_qty",),
    "close_long": ("close_long_base_qty",),
    "open_short": ("open_short_base_qty",),
    "close_short": ("close_short_base_qty",),
    "add_long": ("add_long_base_qty",),
    "add_short": ("add_short_base_qty",),
    "reduce_long": ("reduce_long_base_qty",),
    "reduce_short": ("reduce_short_base_qty",),
}

_PRICE_FIELD_ALIASES = {
    "open_long": ("open_long_price",),
    "close_long": ("close_long_price",),
    "open_short": ("open_short_price",),
    "close_short": ("close_short_price",),
    "add_long": ("add_long_price",),
    "add_short": ("add_short_price",),
    "reduce_long": ("reduce_long_price",),
    "reduce_short": ("reduce_short_price",),
}


def normalize_signal_action(action: Any) -> str:
    value = str(action or "").strip().lower()
    if value not in CANONICAL_SIGNAL_ACTIONS:
        raise ValueError(f"Unsupported signal action: {action}")
    return value


def signal_side(action: Any) -> str:
    action_norm = normalize_signal_action(action)
    if action_norm in ("open_long", "add_long", "close_short", "reduce_short"):
        return "buy"
    return "sell"


def signal_position_side(action: Any) -> str:
    action_norm = normalize_signal_action(action)
    if "long" in action_norm:
        return "long"
    if "short" in action_norm:
        return "short"
    return ""


def signal_reduce_only(action: Any) -> bool:
    action_norm = normalize_signal_action(action)
    return action_norm.startswith("close_") or action_norm.startswith("reduce_")


@dataclass(frozen=True)
class StrategySignal:
    timestamp: Any
    strategy_id: int = 0
    strategy_run_id: int = 0
    symbol: str = ""
    action: str = "open_long"
    market_type: str = "swap"
    amount: float = 0.0
    quote_amount: float = 0.0
    price_hint: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    enter_tag: str = ""
    exit_tag: str = ""
    source: str = "strategy"
    metadata: Dict[str, Any] = field(default_factory=dict)
    portfolio_id: str = ""
    universe_id: str = ""
    rebalance_group_id: str = ""
    target_weight: Optional[float] = None
    target_notional: Optional[float] = None
    target_position_qty: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", normalize_signal_action(self.action))
        object.__setattr__(self, "market_type", _normalize_market_type(self.market_type))
        object.__setattr__(self, "symbol", str(self.symbol or "").strip())
        object.__setattr__(self, "source", str(self.source or "strategy").strip() or "strategy")
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "enter_tag", str(self.enter_tag or "").strip())
        object.__setattr__(self, "exit_tag", str(self.exit_tag or "").strip())
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        object.__setattr__(self, "amount", _to_float(self.amount))
        object.__setattr__(self, "quote_amount", _to_float(self.quote_amount))
        object.__setattr__(self, "price_hint", _to_float(self.price_hint))
        object.__setattr__(self, "confidence", _to_float(self.confidence))

    @property
    def side(self) -> str:
        return signal_side(self.action)

    @property
    def position_side(self) -> str:
        return signal_position_side(self.action)

    @property
    def reduce_only(self) -> bool:
        return signal_reduce_only(self.action)

    @property
    def order_type(self) -> str:
        explicit = str(self.metadata.get("order_type") or "").strip().lower()
        if explicit in ("market", "limit"):
            return explicit
        algo = str(self.metadata.get("execution_algo") or "").strip().lower()
        if algo == "market":
            return "market"
        if algo in ("limit", "limit_then_market", "maker", "maker_then_market"):
            return "limit"
        return "limit" if self.price_hint > 0 else "market"

    @property
    def execution_algo(self) -> str:
        explicit = str(self.metadata.get("execution_algo") or "").strip().lower()
        if explicit in ("market", "limit", "limit_then_market", "maker", "maker_then_market"):
            return explicit
        return self.order_type

    def validate(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.amount < 0:
            raise ValueError("amount cannot be negative")
        if self.quote_amount < 0:
            raise ValueError("quote_amount cannot be negative")
        if self.market_type == "spot" and self.metadata.get("market_category") != "Futures" and "short" in self.action:
            raise ValueError("spot market does not support short signals")

    def to_signal_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "strategy_id": self.strategy_id,
            "strategy_run_id": self.strategy_run_id,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "action": self.action,
            "side": self.side,
            "position_side": self.position_side,
            "reduce_only": self.reduce_only,
            "order_type": self.order_type,
            "amount": self.amount,
            "quote_amount": self.quote_amount,
            "price_hint": self.price_hint,
            "confidence": self.confidence,
            "reason": self.reason,
            "enter_tag": self.enter_tag,
            "exit_tag": self.exit_tag,
            "source": self.source,
            "metadata": dict(self.metadata),
            "portfolio_id": self.portfolio_id,
            "universe_id": self.universe_id,
            "rebalance_group_id": self.rebalance_group_id,
            "target_weight": self.target_weight,
            "target_notional": self.target_notional,
            "target_position_qty": self.target_position_qty,
        }

    def to_order_intent_kwargs(self, *, leverage: float = 1.0) -> Dict[str, Any]:
        notional = self.quote_amount
        if notional <= 0 and self.amount > 0 and self.price_hint > 0:
            notional = self.amount * self.price_hint
        if self.market_type != "spot" and notional > 0:
            notional = notional * max(1.0, _to_float(leverage, 1.0))
        return {
            "symbol": self.symbol,
            "side": self.side,
            "market_type": self.market_type,
            "position_side": self.position_side,
            "reduce_only": self.reduce_only,
            "order_type": self.order_type,
            "quantity": abs(self.amount),
            "notional": abs(notional),
            "limit_price": self.price_hint if self.order_type == "limit" else 0.0,
            "execution_algo": self.execution_algo,
            "payload": self.to_signal_dict(),
        }

    @classmethod
    def from_script_order(
        cls,
        order: Mapping[str, Any],
        *,
        timestamp: Any,
        strategy_id: int = 0,
        strategy_run_id: int = 0,
        symbol: str = "",
        market_type: str = "swap",
        source: str = "script",
    ) -> "StrategySignal":
        action = _script_order_action(order)
        base_quantity = _to_float(order.get("script_base_qty"))
        if base_quantity <= 0:
            base_quantity = _to_float(order.get("amount"))
        quote_amount = _to_float(order.get("script_quote_amount"))
        return cls(
            timestamp=timestamp,
            strategy_id=strategy_id,
            strategy_run_id=strategy_run_id,
            symbol=symbol,
            action=action,
            market_type=market_type,
            amount=base_quantity,
            quote_amount=quote_amount,
            price_hint=_to_float(order.get("price")),
            reason=str(order.get("reason") or ""),
            source=source,
            metadata={k: v for k, v in dict(order).items() if k not in {"amount", "price", "reason"}},
        )


def normalize_signal_mapping(signals: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(signals, Mapping):
        raise ValueError("signals must be a mapping")
    return dict(signals)


def signal_frame_events(
    df: pd.DataFrame,
    signals: Mapping[str, Any],
    *,
    symbol: str = "",
    market_type: str = "swap",
    strategy_id: int = 0,
    strategy_run_id: int = 0,
    source: str = "signal_frame",
) -> list[StrategySignal]:
    normalized = normalize_signal_mapping(signals)
    events: list[StrategySignal] = []
    for action in CANONICAL_SIGNAL_ACTIONS:
        series = _series(normalized.get(action), df.index, False).fillna(False).astype(bool)
        if not bool(series.any()):
            continue
        amount_series = _series(_first_present(normalized, _BASE_FIELD_ALIASES[action]), df.index, 0.0)
        quote_series = _series(_first_present(normalized, _QUOTE_FIELD_ALIASES[action]), df.index, 0.0)
        price_series = _series(_first_present(normalized, _PRICE_FIELD_ALIASES[action]), df.index, 0.0)
        for ts in series.index[series]:
            events.append(StrategySignal(
                timestamp=ts,
                strategy_id=strategy_id,
                strategy_run_id=strategy_run_id,
                symbol=symbol,
                action=action,
                market_type=market_type,
                amount=_to_float(amount_series.loc[ts]),
                quote_amount=_to_float(quote_series.loc[ts]),
                price_hint=_to_float(price_series.loc[ts]),
                source=source,
            ))
    return events


def canonical_signal_columns() -> tuple[str, ...]:
    return CANONICAL_SIGNAL_ACTIONS


def _script_order_action(order: Mapping[str, Any]) -> str:
    intent = str(order.get("intent") or "").strip().lower()
    if not intent:
        raise ValueError("script order intent must use a canonical signal action")
    return normalize_signal_action(intent)


def _series(value: Any, index: Iterable[Any], default: Any) -> pd.Series:
    if hasattr(value, "reindex"):
        return value.reindex(index, fill_value=default)
    return pd.Series(default, index=index)


def _first_present(values: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in values:
            return values.get(key)
    return None


def _normalize_market_type(value: Any) -> str:
    mt = str(value or "swap").strip().lower()
    if mt in ("future", "futures", "perp", "perpetual"):
        return "swap"
    return mt if mt in ("spot", "swap") else "swap"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default
