"""Bounded retries for exchange responses that prove an order was not accepted."""

from __future__ import annotations

import math
import os
import random
import re
import time
from typing import Any, Callable, TypeVar

from app.services.pending_orders.error_classification import classify_exchange_order_error
from app.utils.logger import get_logger


_ResultT = TypeVar("_ResultT")
_SAFE_RETRY_CATEGORIES = frozenset({"rate_limit", "market_unavailable"})
_NON_TRANSIENT_MARKET_TOKENS = (
    "contract delisted",
    "market is closed",
    "trading is currently not allowed",
)
logger = get_logger(__name__)


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


def is_safe_explicit_order_retry(error: Any) -> bool:
    """Return true only when the response proves the venue rejected the submit."""
    classified = classify_exchange_order_error(error)
    raw = str(classified.get("raw") or "").lower()
    if classified.get("category") == "market_unavailable" and any(
        token in raw for token in _NON_TRANSIENT_MARKET_TOKENS
    ):
        return False
    if classified.get("category") == "market_unavailable" and not (
        re.search(r"(?:retcode|code)\s*['\" :=-]*10019(?!\d)", raw)
        or "settlement in progress" in raw
        or "temporarily closed for maintenance" in raw
    ):
        return False
    return bool(
        classified.get("retryable")
        and classified.get("category") in _SAFE_RETRY_CATEGORIES
    )


def submit_with_safe_retry(
    submit: Callable[[], _ResultT],
    *,
    result_error: Callable[[_ResultT], Any] | None = None,
    retry_exception_types: tuple[type[BaseException], ...] = (Exception,),
    on_retry: Callable[[int, float, str], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    uniform_fn: Callable[[float, float], float] | None = None,
) -> _ResultT:
    """Retry only explicit no-accept responses; ambiguous transport errors escape."""
    retries = _bounded_int_env(
        "LIVE_TRADING_ORDER_SAFE_RETRIES",
        1,
        minimum=0,
        maximum=3,
    )
    backoff = _bounded_float_env(
        "LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC",
        0.5,
        minimum=0.0,
        maximum=30.0,
    )
    jitter = _bounded_float_env(
        "LIVE_TRADING_ORDER_RETRY_JITTER_RATIO",
        0.2,
        minimum=0.0,
        maximum=1.0,
    )
    sleeper = sleep_fn or time.sleep
    uniform = uniform_fn or random.uniform

    for attempt in range(retries + 1):
        try:
            result = submit()
        except retry_exception_types as exc:
            if attempt >= retries or not is_safe_explicit_order_retry(exc):
                raise
            error = exc
        else:
            error = result_error(result) if result_error is not None else ""
            if not error or attempt >= retries or not is_safe_explicit_order_retry(error):
                return result

        base_delay = backoff * (2 ** attempt)
        delay = base_delay * uniform(max(0.0, 1.0 - jitter), 1.0 + jitter)
        category = str(classify_exchange_order_error(error).get("category") or "exchange")
        logger.warning(
            "Retrying explicitly rejected order category=%s attempt=%s/%s retry_in_sec=%.3f",
            category,
            attempt + 1,
            retries,
            delay,
        )
        if on_retry is not None:
            try:
                on_retry(attempt + 1, delay, category)
            except Exception:
                logger.warning(
                    "Order retry observer failed category=%s attempt=%s/%s",
                    category,
                    attempt + 1,
                    retries,
                    exc_info=True,
                )
        if delay > 0:
            sleeper(delay)

    raise RuntimeError("safe_order_retry_exhausted")


__all__ = ["is_safe_explicit_order_retry", "submit_with_safe_retry"]
