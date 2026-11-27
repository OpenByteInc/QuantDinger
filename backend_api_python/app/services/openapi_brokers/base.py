from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_json(value: Optional[dict]) -> str:
    return "" if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _canonical_order_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in {"filled", "filled_all", "full_filled", "completed"}:
        return "filled"
    if status in {"cancelled", "canceled", "cancelled_all", "canceled_all"}:
        return "cancelled"
    if status in {"failed", "rejected", "expired", "disabled"}:
        return "rejected"
    if status in {"filled_part", "partially_filled", "partial_filled"}:
        return "partially_filled"
    return status or "submitted"


def _data_list(payload: Any, *keys: str) -> List[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _data_list(value, *keys)
            if nested:
                return nested
    for key in ("data", "items", "list", "rows", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _data_list(value, *keys)
            if nested:
                return nested
    return []


@dataclass
class BrokerOrderResult:
    success: bool
    order_id: str = ""
    filled: float = 0.0
    avg_price: float = 0.0
    status: str = ""
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class BrokerApiError(RuntimeError):
    pass


class _HttpClient:
    def __init__(self, base_url: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = max(3, min(int(timeout or 15), 60))
        self.session = requests.Session()
        self.connected = False

    def disconnect(self) -> None:
        self.connected = False
        self.session.close()

    @staticmethod
    def _decode_response(response: requests.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerApiError(f"HTTP {response.status_code}: invalid JSON response") from exc
        if not response.ok:
            detail = payload.get("msg") or payload.get("message") or payload.get("error") if isinstance(payload, dict) else payload
            raise BrokerApiError(f"HTTP {response.status_code}: {detail}")
        return payload
