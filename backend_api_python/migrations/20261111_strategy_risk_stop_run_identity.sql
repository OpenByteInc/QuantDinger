-- Keep live position ownership and risk accounting scoped to one deployment run.
ALTER TABLE qd_strategy_positions
    ADD COLUMN IF NOT EXISTS strategy_run_id INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_positions_strategy_run
    ON qd_strategy_positions(strategy_id, strategy_run_id);
