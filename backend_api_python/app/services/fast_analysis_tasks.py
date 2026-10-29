"""Async task and in-flight helpers for fast analysis routes."""

import hashlib
import os
import threading
import time
from functools import lru_cache

import redis

from app.config.redis_urls import celery_broker_url
from app.utils.logger import get_logger

logger = get_logger(__name__)

_analysis_inflight_lock = threading.Lock()
_analysis_inflight: dict[str, float] = {}


def build_inflight_key(user_id: int, market: str, symbol: str, timeframe: str) -> str:
    return (
        f"{int(user_id)}|{str(market or '').strip().upper()}|"
        f"{str(symbol or '').strip().upper()}|{str(timeframe or '').strip().upper()}"
    )


def acquire_inflight(key: str, ttl_sec: int = 90) -> bool:
    if _celery_enabled():
        client = _redis_client()
        redis_key = _redis_inflight_key(key)
        return bool(client.set(redis_key, "1", ex=max(1, int(ttl_sec)), nx=True))
    now = time.time()
    with _analysis_inflight_lock:
        stale = [k for k, exp in _analysis_inflight.items() if float(exp) <= now]
        for stale_key in stale[:1024]:
            _analysis_inflight.pop(stale_key, None)
        if key in _analysis_inflight and float(_analysis_inflight.get(key) or 0) > now:
            return False
        _analysis_inflight[key] = now + int(ttl_sec)
        return True


def release_inflight(key: str) -> None:
    if _celery_enabled():
        _redis_client().delete(_redis_inflight_key(key))
        return
    with _analysis_inflight_lock:
        _analysis_inflight.pop(key, None)


def _celery_enabled() -> bool:
    return os.getenv("CELERY_TASKS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


@lru_cache(maxsize=1)
def _redis_client():
    return redis.Redis.from_url(celery_broker_url(), socket_connect_timeout=2, socket_timeout=2)


def _redis_inflight_key(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"quantdinger:fast-analysis:inflight:{digest}"


def try_refund_credits(
    user_id: int,
    amount: int,
    remark: str,
    reference_id: str = "",
) -> None:
    """Best-effort refund when analysis fails after a pre-charge."""
    try:
        if int(amount or 0) <= 0:
            return
        from app.services.billing_service import get_billing_service

        get_billing_service().add_credits(
            user_id=int(user_id),
            amount=int(amount),
            action="refund",
            remark=remark,
            reference_id=reference_id,
        )
    except Exception as exc:
        logger.error("Async auto refund failed: %s", exc, exc_info=True)


def run_async_analysis_task(
    task_memory_id: int,
    market: str,
    symbol: str,
    language: str,
    model: str,
    timeframe: str,
    user_id: int,
    inflight_key: str,
    credits_charged: int = 0,
    research_mode: str = "research",
) -> None:
    """Execute analysis in a background worker and finalize pending history."""
    try:
        from app.services.analysis_memory import get_analysis_memory
        from app.services.fast_analysis import get_fast_analysis_service

        service = get_fast_analysis_service()
        memory = get_analysis_memory()
        result = service.analyze(
            market=market,
            symbol=symbol,
            language=language,
            model=model,
            timeframe=timeframe,
            user_id=user_id,
            research_mode=research_mode,
        )
        memory.finalize_pending_task(task_memory_id, result)
        if result.get("error"):
            try_refund_credits(
                user_id=int(user_id),
                amount=int(credits_charged or 0),
                remark=f"Auto refund: async fast-analysis failed ({market}:{symbol}:{timeframe})",
                reference_id=f"fast-analysis-refund:{int(task_memory_id)}",
            )

        auto_memory_id = result.get("memory_id")
        if auto_memory_id and int(auto_memory_id) != int(task_memory_id):
            try:
                memory.delete_history(int(auto_memory_id), user_id=user_id)
            except Exception:
                pass
    except Exception as exc:
        logger.error("Async analysis task failed: %s", exc, exc_info=True)
        try_refund_credits(
            user_id=int(user_id),
            amount=int(credits_charged or 0),
            remark=f"Auto refund: async fast-analysis exception ({market}:{symbol}:{timeframe})",
            reference_id=f"fast-analysis-refund:{int(task_memory_id)}",
        )
        try:
            from app.services.analysis_memory import get_analysis_memory

            get_analysis_memory().fail_pending_task(task_memory_id, str(exc))
        except Exception:
            pass
    finally:
        try:
            release_inflight(inflight_key)
        except Exception:
            pass


def start_async_analysis_task(*args, **kwargs):
    if _celery_enabled():
        from app.tasks.fast_analysis import execute_fast_analysis

        return execute_fast_analysis.delay(*args, **kwargs)
    thread = threading.Thread(target=run_async_analysis_task, args=args, kwargs=kwargs, daemon=True)
    thread.start()
    return thread

