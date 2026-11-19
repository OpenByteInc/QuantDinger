"""Central role capabilities shared by human and agent authentication."""
from __future__ import annotations


TRADING_ROLES = frozenset({"admin", "user"})


def role_can_trade(role: str | None) -> bool:
    """Return whether an authoritative database role may mutate trading state."""
    return str(role or "").strip().lower() in TRADING_ROLES
