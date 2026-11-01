CREATE TABLE IF NOT EXISTS qd_user_factors (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
  name VARCHAR(120) NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  category VARCHAR(40) NOT NULL DEFAULT 'custom',
  direction_hint VARCHAR(40) NOT NULL DEFAULT 'neutral',
  expression TEXT NOT NULL,
  default_params JSONB NOT NULL DEFAULT '{}'::jsonb,
  parameter_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
  required_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
  dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
  warmup_bars INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_factors_owner
  ON qd_user_factors(user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS qd_user_factor_versions (
  id BIGSERIAL PRIMARY KEY,
  factor_id BIGINT NOT NULL REFERENCES qd_user_factors(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  expression TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(factor_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_user_factor_versions_owner
  ON qd_user_factor_versions(user_id, factor_id, version_no DESC);
