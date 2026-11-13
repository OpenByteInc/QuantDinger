"""Canonical live fill event types shared by every venue adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def as_millis_datetime(value: Any) -> datetime:
    raw = as_float(value)
    if raw <= 0:
        return datetime.now(timezone.utc)
    if raw > 10_000_000_000_000:
        raw /= 1_000_000.0
    elif raw > 10_000_000_000:
        raw /= 1000.0
    return datetime.fromtimestamp(raw, tz=timezone.utc)


def normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"filled", "fill", "closed", "done", "fully_filled", "full_fill"}:
        return "filled"
    if raw in {"partiallyfilled", "partially_filled", "partial_fill", "partial"}:
        return "partial"
    if raw in {"cancelled", "canceled", "expired", "rejected", "failed"}:
        return "cancelled"
    if raw in {"new", "open", "live", "submitted", "accepted"}:
        return "open"
    return raw


def normalize_symbol(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    raw = raw.replace("-", "/").replace("_", "/")
    for contract_suffix in ("/SWAP", "/PERP", "/PERPETUAL"):
        if raw.endswith(contract_suffix):
            raw = raw[: -len(contract_suffix)]
    for suffix in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if "/" not in raw and raw.endswith(suffix) and len(raw) > len(suffix):
            return f"{raw[:-len(suffix)]}/{suffix}"
    return raw


@dataclass(frozen=True)
class FeeComponent:
    currency: str
    amount: float
    fee_type: str = "trade"
    source: str = "websocket"
    quote_amount: Optional[float] = None


@dataclass
class ExecutionEvent:
    exchange_id: str
    market_type: str
    symbol: str
    exchange_order_id: str = ""
    client_order_id: str = ""
    exchange_fill_id: str = ""
    side: str = ""
    position_side: str = ""
    order_status: str = ""
    price: float = 0.0
    quantity: float = 0.0
    cumulative_quantity: float = 0.0
    is_cumulative: bool = False
    cumulative_average_price: float = 0.0
    fees_cumulative: bool = False
    realized_pnl: Optional[float] = None
    maker: Optional[bool] = None
    fee_status: str = "pending"
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fees: List[FeeComponent] = field(default_factory=list)
    order_identifiers: List[Dict[str, str]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
    credential_id: int = 0
    user_id: int = 1
    account_id: str = ""

    def event_key(self) -> str:
        stable_fill = str(self.exchange_fill_id or "").strip()
        if stable_fill:
            basis = (
                f"{self.exchange_id}|{self.credential_id}|{self.market_type}|"
                f"{self.symbol}|{self.exchange_order_id}|{stable_fill}"
            )
        else:
            basis = json.dumps(
                {
                    "e": self.exchange_id,
                    "c": self.credential_id,
                    "m": self.market_type,
                    "o": self.exchange_order_id,
                    "co": self.client_order_id,
                    "s": self.symbol,
                    "q": round(float(self.quantity or 0.0), 12),
                    "cq": round(float(self.cumulative_quantity or 0.0), 12),
                    "p": round(float(self.price or 0.0), 12),
                    "t": self.occurred_at.isoformat(),
                    "st": self.order_status,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        if self.fees_cumulative:
            basis += json.dumps({"q": self.cumulative_quantity, "p": self.cumulative_average_price,
                "fees": sorted((f.currency, f.fee_type, f.amount) for f in self.fees),
                "status": self.fee_status}, sort_keys=True)
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()
