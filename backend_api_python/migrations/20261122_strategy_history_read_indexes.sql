-- Latest-state reads must not sort and transfer an entire strategy's history.
CREATE INDEX IF NOT EXISTS idx_strategy_runs_strategy_latest
    ON strategy_runs(strategy_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_events_strategy_latest
    ON strategy_runtime_events(strategy_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_state_health_latest
    ON strategy_runtime_state(strategy_id, strategy_run_id DESC, updated_at DESC)
    WHERE state_key = 'health';
