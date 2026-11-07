"""Local desktop broker policy for IBKR."""

from __future__ import annotations

from app.utils.trading_venue_visibility import is_trading_venue_enabled


def local_desktop_brokers_allowed() -> bool:
    """Use the canonical supported-venue policy for every IBKR entry point."""
    return is_trading_venue_enabled("ibkr")


def desktop_broker_cloud_reject_message() -> str:
    return (
        "This server has disabled IBKR local desktop broker access "
        "(requires local TWS or IB Gateway). Deploy QuantDinger on your own "
        "machine or private server and install IBKR TWS/Gateway."
    )


def require_local_desktop_brokers_allowed() -> None:
    """Reject an IBKR operation when the deployment policy disables it."""
    if not local_desktop_brokers_allowed():
        raise PermissionError(desktop_broker_cloud_reject_message())
