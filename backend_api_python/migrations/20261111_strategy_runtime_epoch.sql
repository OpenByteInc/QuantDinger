-- Separate desired runtime lifecycle from generic strategy record updates.
ALTER TABLE qd_strategies_trading
    ADD COLUMN IF NOT EXISTS desired_runtime_epoch BIGINT NOT NULL DEFAULT 0;

ALTER TABLE qd_strategies_trading
    ADD COLUMN IF NOT EXISTS runtime_requested_at TIMESTAMP;

WITH latest_run AS (
    SELECT DISTINCT ON (strategy_id)
           strategy_id,
           GREATEST(runtime_epoch, 1) AS runtime_epoch,
           started_at
    FROM strategy_runs
    ORDER BY strategy_id, id DESC
),
active_run AS (
    SELECT strategy_id, MAX(started_at) AS started_at
    FROM strategy_runs
    WHERE runtime_status IN ('running', 'recovering', 'paused', 'needs_review')
    GROUP BY strategy_id
)
UPDATE qd_strategies_trading AS strategy
SET desired_runtime_epoch = CASE
        WHEN strategy.desired_runtime_epoch > 0
            THEN strategy.desired_runtime_epoch
        WHEN latest_run.runtime_epoch IS NOT NULL
            THEN latest_run.runtime_epoch
        WHEN strategy.status = 'running'
            THEN 1
        ELSE 0
    END,
    runtime_requested_at = CASE
        WHEN strategy.runtime_requested_at IS NOT NULL
            THEN strategy.runtime_requested_at
        WHEN strategy.status = 'running'
            THEN COALESCE(active_run.started_at, NOW())
        ELSE NULL
    END
FROM latest_run
LEFT JOIN active_run ON active_run.strategy_id = latest_run.strategy_id
WHERE latest_run.strategy_id = strategy.id
  AND (
      strategy.desired_runtime_epoch <= 0
      OR (strategy.status = 'running' AND strategy.runtime_requested_at IS NULL)
  );

UPDATE qd_strategies_trading
SET desired_runtime_epoch = 1,
    runtime_requested_at = COALESCE(runtime_requested_at, updated_at, NOW())
WHERE status = 'running'
  AND desired_runtime_epoch <= 0;
