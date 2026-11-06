CREATE TABLE IF NOT EXISTS qd_user_client_sessions (
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    client_type VARCHAR(16) NOT NULL CHECK (client_type IN ('pc', 'mobile')),
    token_version INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, client_type)
);

CREATE INDEX IF NOT EXISTS idx_user_client_sessions_updated_at
    ON qd_user_client_sessions(updated_at);

ALTER TABLE qd_oauth_states
    ADD COLUMN IF NOT EXISTS client_type VARCHAR(16) NOT NULL DEFAULT 'pc';
