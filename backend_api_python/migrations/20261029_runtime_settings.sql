-- Settings page values shared by every process. Replaces per-container .env writes.
CREATE SEQUENCE IF NOT EXISTS qd_runtime_settings_version_seq;

CREATE TABLE IF NOT EXISTS qd_runtime_settings (
    setting_key VARCHAR(128) PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    is_secret BOOLEAN NOT NULL DEFAULT FALSE,
    version BIGINT NOT NULL,
    updated_by INTEGER,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
