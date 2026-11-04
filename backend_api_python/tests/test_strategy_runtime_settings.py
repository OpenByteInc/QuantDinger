from __future__ import annotations

from pathlib import Path

from app.routes.settings import (
    AGENT_RUNTIME_SETTINGS,
    ADVANCED_KEYS,
    CONFIG_SCHEMA,
    RESTART_REQUIRED_SETTINGS,
    STRATEGY_RUNTIME_SETTINGS,
)


def test_strategy_runtime_settings_are_exposed_and_restart_scoped():
    items = CONFIG_SCHEMA["strategy_runtime"]["items"]
    keys = {item["key"] for item in items}

    assert keys == {
        "AGENT_JOBS_MAX_WORKERS",
        "STRATEGY_MAX_ACTIVE",
        "STRATEGY_EVALUATOR_THREADS",
        "STRATEGY_EVALUATION_BATCH_SIZE",
        "STRATEGY_EVALUATOR_BATCH_WORKERS",
        "STRATEGY_EVALUATION_TIMEOUT_SEC",
        "BAR_CLOSE_EVENT_GRACE_SEC",
    }
    assert keys <= RESTART_REQUIRED_SETTINGS
    assert keys - {"AGENT_JOBS_MAX_WORKERS"} < STRATEGY_RUNTIME_SETTINGS
    assert "AGENT_JOBS_MAX_WORKERS" in AGENT_RUNTIME_SETTINGS
    assert {
        "KAFKA_EVENT_PUBLISH_ENABLED",
        "STRATEGY_EVALUATOR_MODE",
        "STRATEGY_DISTRIBUTED_BAR_ENABLED",
    }.isdisjoint(keys)
    assert "STRATEGY_SHARD_COUNT" in ADVANCED_KEYS


def test_compose_does_not_override_settings_owned_runtime_values():
    repository_root = Path(__file__).resolve().parents[2]
    settings_owned = (
        "KAFKA_EVENT_PUBLISH_ENABLED",
        "STRATEGY_DISTRIBUTED_BAR_ENABLED",
        "STRATEGY_EVALUATOR_MODE",
        "STRATEGY_SHARD_COUNT",
        "STRATEGY_EVALUATION_BATCH_SIZE",
        "STRATEGY_EVALUATOR_LEASE_SEC",
        "STRATEGY_EVALUATOR_MAX_ATTEMPTS",
        "STRATEGY_EVALUATOR_BATCH_WORKERS",
        "STRATEGY_EVALUATION_TIMEOUT_SEC",
        "KAFKA_MAX_POLL_INTERVAL_MS",
    )

    for compose_name in ("docker-compose.yml", "docker-compose.ghcr.yml"):
        compose = (repository_root / compose_name).read_text(encoding="utf-8")
        for key in settings_owned:
            assert f"{key}=" not in compose
            assert f"{key}:" not in compose


def test_first_install_defaults_to_one_replica_per_runtime_role():
    repository_root = Path(__file__).resolve().parents[2]
    env_template = (repository_root / ".env.example").read_text(encoding="utf-8")

    for key in (
        "TRADING_WORKER_REPLICAS",
        "STRATEGY_DISPATCHER_REPLICAS",
        "STRATEGY_EVALUATOR_REPLICAS",
    ):
        assert f"{key}=1" in env_template

    expected_fallbacks = (
        "${TRADING_WORKER_REPLICAS:-1}",
        "${STRATEGY_DISPATCHER_REPLICAS:-1}",
        "${STRATEGY_EVALUATOR_REPLICAS:-1}",
    )
    for compose_name in ("docker-compose.yml", "docker-compose.ghcr.yml"):
        compose = (repository_root / compose_name).read_text(encoding="utf-8")
        for fallback in expected_fallbacks:
            assert fallback in compose
