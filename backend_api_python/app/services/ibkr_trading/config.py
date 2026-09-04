"""IBKR connection settings resolved from stored exchange credentials.

The HTTP layer must not query credential tables directly (see
``docs/architecture/MODULE_BOUNDARIES.md``).  This module owns that lookup and
returns a ready-to-use :class:`IBKRConfig`.

The UI sends ``127.0.0.1`` by default, which inside a container points at the
container itself and can never reach a TWS/IB Gateway on the operator's desktop.
The saved credential therefore wins whenever the request carries no explicit
host, so a single click on "Connect" works from a Docker deployment.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.ibkr_trading.client import IBKRConfig
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7497
DEFAULT_CLIENT_ID = 1


def load_saved_ibkr_config(user_id: int) -> Dict[str, Any]:
    """Return the user's most recent stored IBKR credential settings.

    Returns an empty dict when the user has no IBKR credential or the lookup
    fails; callers fall back to request values and defaults.
    """
    try:
        with get_db_connection() as db:
            cursor = db.cursor()
            cursor.execute(
                "SELECT id FROM qd_exchange_credentials "
                "WHERE user_id = %s AND exchange_id = 'ibkr' "
                "ORDER BY id DESC LIMIT 1",
                (int(user_id),),
            )
            row = cursor.fetchone()
            cursor.close()
        if not row or not row.get("id"):
            return {}

        from app.services.exchange_execution import resolve_exchange_config

        return resolve_exchange_config(
            {"credential_id": int(row["id"])}, user_id=int(user_id)
        ) or {}
    except Exception as exc:
        logger.warning("Failed to load saved IBKR credential: %s", exc)
        return {}


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_ibkr_config(request_data: Dict[str, Any], *, user_id: int) -> IBKRConfig:
    """Merge request values over the stored credential into an IBKRConfig.

    Explicit request values always win.  Anything the request omits (or leaves
    at the UI's ``127.0.0.1`` placeholder host) comes from the stored
    credential, then from the IB defaults.
    """
    data = request_data if isinstance(request_data, dict) else {}

    host = _text_or_none(data.get("host"))
    port = _int_or_none(data.get("port"))
    client_id = _int_or_none(data.get("clientId"))
    account = _text_or_none(data.get("account"))

    # A localhost host is treated as "unset": it is the UI placeholder and is
    # unreachable from inside a container.
    if host in ("127.0.0.1", "localhost", "::1"):
        host = None

    # ``ibkr_client_id`` on the credential belongs to the strategy/order session
    # (default 7). Borrowing it here would make the UI evict live orders, so the
    # UI session only ever uses the request value or DEFAULT_CLIENT_ID.
    if host is None or port is None or account is None:
        saved = load_saved_ibkr_config(user_id)
        if saved:
            host = host or _text_or_none(saved.get("ibkr_host"))
            port = port or _int_or_none(saved.get("ibkr_port"))
            account = account or _text_or_none(saved.get("ibkr_account"))

    return IBKRConfig(
        host=host or DEFAULT_HOST,
        port=port or DEFAULT_PORT,
        client_id=client_id if client_id is not None else DEFAULT_CLIENT_ID,
        account=account or "",
        readonly=bool(data.get("readonly", False)),
    )


__all__ = ["build_ibkr_config", "load_saved_ibkr_config"]
