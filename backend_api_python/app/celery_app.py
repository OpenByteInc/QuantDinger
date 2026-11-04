"""Celery application with lazy Flask application context integration."""

from __future__ import annotations

import os

from celery import Celery, Task
from app.config.redis_urls import celery_broker_url, celery_result_backend_url


class FlaskContextTask(Task):
    abstract = True
    _flask_app = None

    def __call__(self, *args, **kwargs):
        if self._flask_app is None:
            os.environ["QD_PROCESS_ROLE"] = "celery"
            from app import create_app

            self._flask_app = create_app(register_http_routes=False)
        with self._flask_app.app_context():
            return self.run(*args, **kwargs)


celery_app = Celery("quantdinger", task_cls=FlaskContextTask)
_max_memory_per_child_kb = int(os.getenv("CELERY_MAX_MEMORY_PER_CHILD_KB", "0"))
celery_app.conf.update(
    broker_url=celery_broker_url(),
    result_backend=celery_result_backend_url(),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=os.getenv("TZ", "Asia/Shanghai"),
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=max(1, int(os.getenv("CELERY_WORKER_PREFETCH", "1"))),
    worker_max_tasks_per_child=max(1, int(os.getenv("CELERY_MAX_TASKS_PER_CHILD", "100"))),
    worker_max_memory_per_child=(
        _max_memory_per_child_kb if _max_memory_per_child_kb > 0 else None
    ),
    task_soft_time_limit=max(60, int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "3300"))),
    task_time_limit=max(120, int(os.getenv("CELERY_TASK_TIME_LIMIT", "3600"))),
    result_expires=max(3600, int(os.getenv("CELERY_RESULT_EXPIRES", "86400"))),
    broker_transport_options={
        "visibility_timeout": max(3600, int(os.getenv("CELERY_VISIBILITY_TIMEOUT", "7200"))),
    },
    imports=(
        "app.tasks.agent_jobs",
        "app.tasks.fast_analysis",
        "app.tasks.indicator_translation",
        "app.tasks.maintenance",
        "app.tasks.fundamental_sync",
    ),
    task_routes={
        "quantdinger.tasks.fast_analysis": {"queue": "ai"},
        "quantdinger.tasks.indicator_translation": {"queue": "ai"},
        "quantdinger.tasks.agent_job": {"queue": "backtest"},
        "quantdinger.tasks.expire_agent_jobs": {"queue": "maintenance"},
        "quantdinger.tasks.reflection": {"queue": "maintenance"},
        "quantdinger.tasks.ai_calibration": {"queue": "maintenance"},
        "quantdinger.tasks.market_catalog_sync": {"queue": "maintenance"},
        "quantdinger.tasks.fundamental_sync_tick": {"queue": "maintenance"},
        "quantdinger.tasks.worker_heartbeat": {"queue": "maintenance"},
        "quantdinger.tasks.cleanup_runtime_metadata": {"queue": "maintenance"},
    },
    beat_schedule={
        "fundamental-sync": {
            "task": "quantdinger.tasks.fundamental_sync_tick",
            "schedule": 60.0,
        },
        "expire-billed-agent-jobs": {
            "task": "quantdinger.tasks.expire_agent_jobs",
            "schedule": 60.0,
        },
        "reflection-cycle": {
            "task": "quantdinger.tasks.reflection",
            "schedule": max(300, int(os.getenv("REFLECTION_WORKER_INTERVAL_SEC", "86400"))),
        },
        "ai-calibration-cycle": {
            "task": "quantdinger.tasks.ai_calibration",
            "schedule": max(3600, int(os.getenv("AI_CALIBRATION_INTERVAL_SEC", "86400"))),
        },
        "market-catalog-sync": {
            "task": "quantdinger.tasks.market_catalog_sync",
            "schedule": max(900, int(os.getenv("MARKET_CATALOG_SYNC_INTERVAL_SEC", "86400"))),
        },
        "celery-worker-heartbeat": {
            "task": "quantdinger.tasks.worker_heartbeat",
            "schedule": 10.0,
        },
        "runtime-metadata-cleanup": {
            "task": "quantdinger.tasks.cleanup_runtime_metadata",
            "schedule": 86400.0,
        },
    },
)

__all__ = ["celery_app"]
