-- Durable evaluation and email aggregation for portfolio order batches.
CREATE TABLE IF NOT EXISTS qd_strategy_order_batches (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    strategy_id INTEGER NOT NULL,
    strategy_run_id INTEGER NOT NULL,
    group_id VARCHAR(128) NOT NULL,
    execution_mode VARCHAR(16) NOT NULL,
    runtime_fencing_token BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    sealed_at TIMESTAMP,
    evaluated_at TIMESTAMP,
    notice_claimed_at TIMESTAMP,
    next_check_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_run_id, strategy_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_strategy_order_batches_unfinished
    ON qd_strategy_order_batches(next_check_at, id)
    WHERE evaluated_at IS NULL OR notice_claimed_at IS NULL;
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS rebalance_group_id VARCHAR(128) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_pending_orders_rebalance_batch
    ON pending_orders(strategy_run_id, strategy_id, rebalance_group_id)
    WHERE rebalance_group_id <> '';
