from app.routes.strategy_logs_routes import normalize_strategy_log_level


def test_direction_guard_rejections_are_warning_logs():
    assert normalize_strategy_log_level(
        "error",
        "Runtime cycle failed: strategyV2.directionModeViolation:long_only:short",
    ) == "warning"


def test_real_runtime_failures_remain_errors_and_warn_is_normalized():
    assert normalize_strategy_log_level("error", "Runtime cycle failed: database offline") == "error"
    assert normalize_strategy_log_level("warn", "temporary data delay") == "warning"

