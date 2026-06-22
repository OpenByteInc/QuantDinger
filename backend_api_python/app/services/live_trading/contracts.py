"""Typed live-trading contracts used by exchange adapters.

The concrete REST clients still expose exchange-specific methods. These small
dataclasses describe the stable domain language the rest of the backend should
move toward: order intent in, normalized order/fill/position data out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Tuple

from app.services.live_trading.base import LiveOrderResult, LiveTradingError


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    quantity: float
    market_type: str = "swap"
    price: float = 0.0
    pos_side: str = ""
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    leverage: float = 1.0
    margin_mode: str = "cross"
    post_only: bool = False
    quote_amount: float = 0.0
    exchange_config: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FillSnapshot:
    filled_qty: float
    avg_price: float
    status: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    side: str
    base_qty: float
    avg_price: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


class ExchangeOrderAdapter(Protocol):
    """Stable interface for order paths outside exchange-specific clients."""

    exchange_id: str

    def place_market_order(self, intent: OrderIntent) -> LiveOrderResult:
        ...

    def place_limit_order(self, intent: OrderIntent) -> LiveOrderResult:
        ...

    def cancel_order(self, intent: OrderIntent, *, order_id: str = "") -> Dict[str, Any]:
        ...

    def wait_for_fill(
        self,
        intent: OrderIntent,
        *,
        order_id: str = "",
        max_wait_sec: float = 15.0,
    ) -> FillSnapshot:
        ...

    def query_position(self, intent: OrderIntent) -> PositionSnapshot:
        ...


def normalize_order_side(side: str) -> str:
    sd = str(side or "").strip().lower()
    if sd not in ("buy", "sell"):
        raise LiveTradingError(f"Invalid order side: {side}")
    return sd


def normalize_order_market_type(market_type: str) -> str:
    mt = str(market_type or "swap").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        return "swap"
    return mt


def order_intent_from_signal(
    *,
    signal_type: str,
    symbol: str,
    quantity: float,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]] = None,
    client_order_id: Optional[str] = None,
    quote_amount: float = 0.0,
) -> OrderIntent:
    side, pos_side, reduce_only = signal_to_order_sides(signal_type)
    return OrderIntent(
        symbol=str(symbol or "").strip(),
        side=side,
        quantity=float(quantity or 0.0),
        market_type=normalize_order_market_type(market_type),
        pos_side=pos_side,
        reduce_only=reduce_only,
        client_order_id=client_order_id,
        quote_amount=float(quote_amount or 0.0),
        exchange_config=dict(exchange_config or {}),
    )


def signal_to_order_sides(signal_type: str) -> Tuple[str, str, bool]:
    sig = (signal_type or "").strip().lower()
    if sig in ("open_long", "add_long"):
        return "buy", "long", False
    if sig in ("open_short", "add_short"):
        return "sell", "short", False
    if sig in ("close_long", "reduce_long"):
        return "sell", "long", True
    if sig in ("close_short", "reduce_short"):
        return "buy", "short", True
    raise LiveTradingError(f"Unsupported signal_type: {signal_type}")
