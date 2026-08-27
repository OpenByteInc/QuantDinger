"""Local desktop broker policy for IBKR / Futu OpenD."""

from __future__ import annotations

import os
from typing import Set


LOCAL_DESKTOP_BROKERS: Set[str] = {"ibkr", "futu"}


def local_desktop_brokers_allowed() -> bool:
    """When False, IBKR/Futu credential creation and related flows are rejected."""
    v = os.getenv("ALLOW_LOCAL_DESKTOP_BROKERS", "true").strip().lower()
    return v in ("1", "true", "yes", "on")


def is_local_desktop_broker(exchange_id: str) -> bool:
    return str(exchange_id or "").strip().lower() in LOCAL_DESKTOP_BROKERS


def desktop_broker_cloud_reject_message(exchange_id: str = "ibkr") -> str:
    ex = str(exchange_id or "ibkr").strip().lower()
    if ex == "futu":
        return (
            "This server has disabled Futu local desktop broker access "
            "(requires FutuOpenD). Deploy QuantDinger on your own machine "
            "or private server and run FutuOpenD."
        )
    return (
        "This server has disabled IBKR local desktop broker access "
        "(requires local TWS or IB Gateway). Deploy QuantDinger on your own "
        "machine or private server and install IBKR TWS/Gateway."
    )
