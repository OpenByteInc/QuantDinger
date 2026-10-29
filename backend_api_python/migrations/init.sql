-- QuantDinger PostgreSQL Schema Initialization
-- This script runs automatically when PostgreSQL container starts for the first time.

-- =============================================================================
-- 1. Users & Authentication
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    email VARCHAR(100) UNIQUE,
    nickname VARCHAR(50),
    avatar VARCHAR(255) DEFAULT '/avatar2.jpg',
    status VARCHAR(20) DEFAULT 'active',  -- active/disabled/pending
    role VARCHAR(20) DEFAULT 'user',       -- admin/manager/user/viewer
    credits DECIMAL(20,2) DEFAULT 0,
    vip_expires_at TIMESTAMP,              -- VIP杩囨湡鏃堕棿
    vip_plan VARCHAR(64) DEFAULT '',
    vip_is_lifetime BOOLEAN DEFAULT FALSE,
    vip_monthly_credits_last_grant TIMESTAMP,
    email_verified BOOLEAN DEFAULT FALSE,
    referred_by INTEGER,                   -- 閭€璇蜂汉ID
    notification_settings TEXT DEFAULT '',
    chart_templates TEXT DEFAULT '',      -- 鐢ㄦ埛鍥捐〃妯℃澘 JSON锛堟寚鏍囧竷灞€/鏍峰紡锛?
    timezone VARCHAR(64) DEFAULT '',
    token_version INTEGER DEFAULT 1,
    password_changed_at TIMESTAMP,           -- NULL only prompts when bootstrap password is still 123456
    last_login_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_referred_by ON qd_users(referred_by);

-- Note: Admin user is created automatically by the application on startup
-- using ADMIN_USER and ADMIN_PASSWORD from environment variables

-- =============================================================================

-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_credits_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,            -- recharge/consume/refund/admin_adjust/vip_grant
    amount DECIMAL(20,2) NOT NULL,
    balance_after DECIMAL(20,2) NOT NULL,   -- 鍙樺姩鍚庝綑棰?
    feature VARCHAR(50) DEFAULT '',          -- 娑堣垂鐨勫姛鑳斤細ai_analysis/strategy_run/backtest 绛?
    reference_id VARCHAR(100) DEFAULT '',
    remark TEXT DEFAULT '',                  -- 澶囨敞
    operator_id INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credits_log_user_id ON qd_credits_log(user_id);
CREATE INDEX IF NOT EXISTS idx_credits_log_action ON qd_credits_log(action);
CREATE INDEX IF NOT EXISTS idx_credits_log_created_at ON qd_credits_log(created_at);

-- =============================================================================

-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_membership_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    plan VARCHAR(64) NOT NULL,
    price_usd DECIMAL(10,2) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'paid',
    created_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_membership_orders_user_id ON qd_membership_orders(user_id);

CREATE TABLE IF NOT EXISTS qd_billing_plans (
    code VARCHAR(64) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_usd DECIMAL(12,2) NOT NULL DEFAULT 0,
    duration_days INTEGER NOT NULL DEFAULT 0,
    credits_once INTEGER NOT NULL DEFAULT 0,
    credits_monthly INTEGER NOT NULL DEFAULT 0,
    strategy_limit INTEGER NOT NULL DEFAULT 10,
    referral_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    is_lifetime BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_popular BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    stripe_price_id VARCHAR(255) NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS qd_membership_fulfillments (
    provider_ref VARCHAR(255) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    plan VARCHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS qd_stripe_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    plan VARCHAR(64) NOT NULL,
    stripe_session_id VARCHAR(255) UNIQUE,
    amount_usd DECIMAL(12,2) NOT NULL DEFAULT 0,
    currency VARCHAR(10) NOT NULL DEFAULT 'usd',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    paid_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE qd_stripe_orders ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;

-- =============================================================================
-- 1.56. USDT Orders (multi-chain single-receiving-address + amount-suffix model)
-- =============================================================================
--
-- v3.0.6 reset: replaced xpub-derived per-order addresses with a single fixed
-- receiving address per chain. Orders are identified on-chain by a unique
-- amount suffix in the low decimals (e.g. 19.991234 -> suffix 0.001234).
-- This eliminates the consolidation step (funds land directly in the main
-- wallet) and removes per-sweep TRX/gas costs.
--
-- Supported chains: TRC20 (TRON), BEP20 (BSC), ERC20 (Ethereum), SOL (Solana SPL).
-- Each chain's address is configured via USDT_{CHAIN}_ADDRESS env var.

CREATE TABLE IF NOT EXISTS qd_usdt_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    plan VARCHAR(64) NOT NULL,
    chain VARCHAR(20) NOT NULL DEFAULT 'TRC20',                 -- TRC20/BEP20/ERC20/SOL
    currency VARCHAR(10) NOT NULL DEFAULT 'USDT',
    amount_usdt DECIMAL(20,8) NOT NULL DEFAULT 0,               -- final amount = base + suffix (6 dp typical)
    amount_suffix DECIMAL(20,8) NOT NULL DEFAULT 0,             -- the unique suffix portion used for matching
    address VARCHAR(120) NOT NULL DEFAULT '',                   -- fixed receiving address (per chain)
    payment_uri TEXT NOT NULL DEFAULT '',                       -- full deep link (EIP-681 / Solana Pay / tron URI)
    matched_via VARCHAR(20) NOT NULL DEFAULT 'amount_suffix',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',              -- pending/paid/confirmed/expired/cancelled/failed
    tx_hash VARCHAR(120) DEFAULT '',
    paid_at TIMESTAMP,
    confirmed_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usdt_orders_user_id ON qd_usdt_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_usdt_orders_status ON qd_usdt_orders(status);
-- v3.0.6 cleanup: drop the legacy unique index on (chain, address) that
-- was used by the per-order xpub-derived address scheme. In the current
-- "single fixed receiving address per chain + amount-suffix matching"
-- model, every active order on the same chain shares the same address,
-- so this old index would falsely reject every second pending order
-- (UniqueViolation on idx_usdt_orders_address_unique). Safe & idempotent.
DROP INDEX IF EXISTS idx_usdt_orders_address_unique;
-- Prevent two active orders on the same chain from claiming the same amount,
-- which is the foundation of the amount-suffix matching scheme.
-- Existing installations must gain the discriminator before the new index
-- references it; CREATE TABLE IF NOT EXISTS above does not alter old tables.
ALTER TABLE qd_usdt_orders
    ADD COLUMN IF NOT EXISTS currency VARCHAR(10) NOT NULL DEFAULT 'USDT';
DROP INDEX IF EXISTS idx_usdt_orders_amount_active;
CREATE UNIQUE INDEX IF NOT EXISTS idx_crypto_orders_amount_active
  ON qd_usdt_orders(currency, chain, amount_usdt)
  WHERE status IN ('pending', 'paid');

-- One-shot cleanup for installs that pre-date v3.0.6. address_index is no
-- longer used; we keep the column where it already exists to avoid breaking
-- old rows, but new installs do not need it. The DO block is idempotent and
-- safe to re-run.
DO $$
BEGIN
    -- amount_suffix
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='qd_usdt_orders' AND column_name='amount_suffix'
    ) THEN
        ALTER TABLE qd_usdt_orders ADD COLUMN amount_suffix DECIMAL(20,8) NOT NULL DEFAULT 0;
    END IF;
    -- payment_uri
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='qd_usdt_orders' AND column_name='payment_uri'
    ) THEN
        ALTER TABLE qd_usdt_orders ADD COLUMN payment_uri TEXT NOT NULL DEFAULT '';
    END IF;
    -- currency
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='qd_usdt_orders' AND column_name='currency'
    ) THEN
        ALTER TABLE qd_usdt_orders ADD COLUMN currency VARCHAR(10) NOT NULL DEFAULT 'USDT';
    END IF;
    -- matched_via
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='qd_usdt_orders' AND column_name='matched_via'
    ) THEN
        ALTER TABLE qd_usdt_orders ADD COLUMN matched_via VARCHAR(20) NOT NULL DEFAULT 'amount_suffix';
    END IF;
    -- widen amount_usdt to (20,8) so suffix at 6+ decimals fits exactly
    BEGIN
        ALTER TABLE qd_usdt_orders ALTER COLUMN amount_usdt TYPE DECIMAL(20,8);
    EXCEPTION WHEN others THEN NULL;
    END;
    -- widen address (TRC20 base58 ~34, Solana ~44; old col was 80)
    BEGIN
        ALTER TABLE qd_usdt_orders ALTER COLUMN address TYPE VARCHAR(120);
    EXCEPTION WHEN others THEN NULL;
    END;
    BEGIN
        ALTER TABLE qd_usdt_orders ALTER COLUMN plan TYPE VARCHAR(64);
        ALTER TABLE qd_membership_orders ALTER COLUMN plan TYPE VARCHAR(64);
        ALTER TABLE qd_users ALTER COLUMN vip_plan TYPE VARCHAR(64);
    EXCEPTION WHEN others THEN NULL;
    END;
END
$$;

-- =============================================================================

-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_oauth_states (
    state VARCHAR(128) PRIMARY KEY,
    provider VARCHAR(20) NOT NULL,
    redirect TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_states_expires ON qd_oauth_states(expires_at);

-- =============================================================================

-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_verification_codes (
    id SERIAL PRIMARY KEY,
    email VARCHAR(100) NOT NULL,
    code VARCHAR(10) NOT NULL,
    type VARCHAR(20) NOT NULL,              -- register/login/reset_password/change_email/change_password
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    ip_address VARCHAR(45),
    attempts INTEGER DEFAULT 0,             -- Failed verification attempts (anti-brute-force)
    last_attempt_at TIMESTAMP,              -- Last attempt time
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_verification_codes_email ON qd_verification_codes(email);
CREATE INDEX IF NOT EXISTS idx_verification_codes_type ON qd_verification_codes(type);
CREATE INDEX IF NOT EXISTS idx_verification_codes_expires ON qd_verification_codes(expires_at);

-- =============================================================================
-- 1.7. Login Attempts (鐧诲綍灏濊瘯璁板綍 - 闃茬垎鐮?
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_login_attempts (
    id SERIAL PRIMARY KEY,
    identifier VARCHAR(100) NOT NULL,       -- IP address or username
    identifier_type VARCHAR(10) NOT NULL,   -- 'ip' or 'account'
    attempt_time TIMESTAMP DEFAULT NOW(),
    success BOOLEAN DEFAULT FALSE,
    ip_address VARCHAR(45),
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_identifier ON qd_login_attempts(identifier, identifier_type);
CREATE INDEX IF NOT EXISTS idx_login_attempts_time ON qd_login_attempts(attempt_time);

-- =============================================================================

-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_oauth_links (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES qd_users(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL,          -- 'google' or 'github'
    provider_user_id VARCHAR(100) NOT NULL,
    provider_email VARCHAR(100),
    provider_name VARCHAR(100),
    provider_avatar VARCHAR(255),
    access_token TEXT,
    refresh_token TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(provider, provider_user_id)
);

CREATE INDEX IF NOT EXISTS idx_oauth_links_user_id ON qd_oauth_links(user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_links_provider ON qd_oauth_links(provider);

-- =============================================================================

-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_security_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    action VARCHAR(50) NOT NULL,            -- login/logout/register/reset_password/oauth_login/etc
    ip_address VARCHAR(45),
    user_agent TEXT,
    details TEXT,                           -- JSON with additional info
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_security_logs_user_id ON qd_security_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_security_logs_action ON qd_security_logs(action);
CREATE INDEX IF NOT EXISTS idx_security_logs_created_at ON qd_security_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_security_logs_action_ip_created_at
    ON qd_security_logs(action, ip_address, created_at);

-- =============================================================================
-- 1.10. User MFA (TOTP / Authenticator App)
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_user_mfa (
    user_id INTEGER PRIMARY KEY REFERENCES qd_users(id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT FALSE,
    secret_encrypted TEXT NOT NULL,
    recovery_codes_hash TEXT DEFAULT '',
    last_used_counter BIGINT DEFAULT 0,
    confirmed_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS qd_mfa_challenges (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    challenge_hash VARCHAR(128) UNIQUE NOT NULL,
    reason VARCHAR(50) DEFAULT 'risk_login',
    ip_address VARCHAR(45),
    user_agent TEXT,
    attempts INTEGER DEFAULT 0,
    expires_at TIMESTAMP NOT NULL,
    consumed_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mfa_challenges_user_id ON qd_mfa_challenges(user_id);
CREATE INDEX IF NOT EXISTS idx_mfa_challenges_expires ON qd_mfa_challenges(expires_at);

-- =============================================================================
-- 2. Trading Strategies
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_strategies_trading (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_name VARCHAR(255) NOT NULL,
    strategy_type VARCHAR(50) DEFAULT 'StrategyV2',
    market_category VARCHAR(50) DEFAULT 'Crypto',
    execution_mode VARCHAR(20) NOT NULL DEFAULT 'signal',
    notification_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) DEFAULT 'stopped',
    symbol VARCHAR(50),
    symbol_canonical VARCHAR(50) DEFAULT '',
    timeframe VARCHAR(10),
    initial_capital DECIMAL(20,8) DEFAULT 1000,
    leverage INTEGER DEFAULT 1,
    market_type VARCHAR(20) DEFAULT 'swap',
    exchange_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    trading_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_version_id INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategies_user_id ON qd_strategies_trading(user_id);
CREATE INDEX IF NOT EXISTS idx_strategies_status ON qd_strategies_trading(status);

-- Script source library: reusable code assets separated from live/runtime strategy rows.
CREATE TABLE IF NOT EXISTS qd_script_sources (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    code TEXT NOT NULL DEFAULT '',
    asset_type VARCHAR(32) NOT NULL DEFAULT 'script',
    template_key VARCHAR(80) DEFAULT '',
    param_schema JSONB DEFAULT '{}'::jsonb,
    source_marketplace_indicator_id INTEGER,
    source_script_source_id INTEGER,
    visibility VARCHAR(32) DEFAULT 'private',
    status VARCHAR(32) DEFAULT 'draft',
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_script_sources_user_id ON qd_script_sources(user_id);
CREATE INDEX IF NOT EXISTS idx_script_sources_marketplace ON qd_script_sources(source_marketplace_indicator_id);
CREATE INDEX IF NOT EXISTS idx_script_sources_asset_type ON qd_script_sources(user_id, asset_type);

CREATE TABLE IF NOT EXISTS qd_script_source_versions (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES qd_script_sources(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    code TEXT NOT NULL DEFAULT '',
    template_key VARCHAR(80) DEFAULT '',
    param_schema JSONB DEFAULT '{}'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(source_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_script_source_versions_source
ON qd_script_source_versions(source_id, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_script_source_versions_user
ON qd_script_source_versions(user_id);

ALTER TABLE qd_strategies_trading
ADD COLUMN IF NOT EXISTS source_version_id INTEGER;

DO $$
DECLARE
    trading_config_type TEXT;
BEGIN
    SELECT data_type
    INTO trading_config_type
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'qd_strategies_trading'
      AND column_name = 'trading_config';

    IF trading_config_type IS NOT NULL AND trading_config_type <> 'jsonb' THEN
        ALTER TABLE qd_strategies_trading
            ALTER COLUMN trading_config DROP DEFAULT;
        ALTER TABLE qd_strategies_trading
            ALTER COLUMN trading_config TYPE JSONB
            USING COALESCE(NULLIF(BTRIM(trading_config::TEXT), ''), '{}')::JSONB;
        ALTER TABLE qd_strategies_trading
            ALTER COLUMN trading_config SET DEFAULT '{}'::JSONB;
    END IF;
END
$$;

INSERT INTO qd_script_source_versions
    (source_id, user_id, version_no, name, description, code,
     template_key, param_schema, metadata, created_at)
SELECT source.id,
       source.user_id,
       COALESCE(latest.version_no, 0) + 1,
       source.name,
       COALESCE(source.description, ''),
       COALESCE(source.code, ''),
       COALESCE(source.template_key, ''),
       COALESCE(source.param_schema, '{}'::jsonb),
       COALESCE(source.metadata, '{}'::jsonb),
       NOW()
FROM qd_script_sources AS source
LEFT JOIN LATERAL (
    SELECT version.id, version.version_no, version.name, version.description,
           version.code, version.template_key, version.param_schema, version.metadata
    FROM qd_script_source_versions AS version
    WHERE version.source_id = source.id
      AND version.user_id = source.user_id
    ORDER BY version.version_no DESC
    LIMIT 1
) AS latest ON TRUE
WHERE latest.id IS NULL
   OR latest.name IS DISTINCT FROM source.name
   OR latest.description IS DISTINCT FROM COALESCE(source.description, '')
   OR latest.code IS DISTINCT FROM COALESCE(source.code, '')
   OR latest.template_key IS DISTINCT FROM COALESCE(source.template_key, '')
   OR latest.param_schema IS DISTINCT FROM COALESCE(source.param_schema, '{}'::jsonb)
   OR latest.metadata IS DISTINCT FROM COALESCE(source.metadata, '{}'::jsonb)
ON CONFLICT (source_id, version_no) DO NOTHING;

WITH deployment_version AS (
    SELECT deployment.id AS strategy_id,
           COALESCE(
               (
                   SELECT version.id
                   FROM qd_script_source_versions AS version
                   WHERE version.source_id = deployment.source_id
                     AND version.user_id = deployment.user_id
                     AND version.created_at <= deployment.created_at
                   ORDER BY version.created_at DESC, version.version_no DESC
                   LIMIT 1
               ),
               (
                   SELECT version.id
                   FROM qd_script_source_versions AS version
                   WHERE version.source_id = deployment.source_id
                     AND version.user_id = deployment.user_id
                   ORDER BY version.version_no DESC
                   LIMIT 1
               )
           ) AS version_id
    FROM (
        SELECT strategy.id,
               strategy.user_id,
               strategy.created_at,
               CASE
                   WHEN COALESCE(strategy.trading_config->>'script_source_id', '') ~ '^[0-9]+$'
                   THEN (strategy.trading_config->>'script_source_id')::INTEGER
                   ELSE NULL
               END AS source_id
        FROM qd_strategies_trading AS strategy
        WHERE strategy.source_version_id IS NULL
    ) AS deployment
    WHERE deployment.source_id IS NOT NULL
)
UPDATE qd_strategies_trading AS strategy
SET source_version_id = deployment_version.version_id,
    trading_config = jsonb_set(
        COALESCE(strategy.trading_config, '{}'::jsonb),
        '{script_source_version_id}',
        to_jsonb(deployment_version.version_id),
        TRUE
    )
FROM deployment_version
WHERE strategy.id = deployment_version.strategy_id
  AND deployment_version.version_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_strategy_source_version'
          AND conrelid = 'qd_strategies_trading'::regclass
    ) THEN
        ALTER TABLE qd_strategies_trading
        ADD CONSTRAINT fk_strategy_source_version
        FOREIGN KEY (source_version_id)
        REFERENCES qd_script_source_versions(id)
        ON DELETE RESTRICT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_strategies_source_version
ON qd_strategies_trading(source_version_id);

CREATE TABLE IF NOT EXISTS qd_script_templates (
    id SERIAL PRIMARY KEY,
    template_key VARCHAR(80) UNIQUE NOT NULL,
    asset_type VARCHAR(40) NOT NULL DEFAULT 'script',
    title VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    code TEXT NOT NULL DEFAULT '',
    param_schema JSONB DEFAULT '{}'::jsonb,
    tags JSONB DEFAULT '[]'::jsonb,
    icon VARCHAR(64) DEFAULT 'appstore',
    accent VARCHAR(32) DEFAULT 'blue',
    sort_order INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_script_templates_active
ON qd_script_templates(is_active, sort_order);

-- =============================================================================
-- 3. Strategy Positions
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_strategy_positions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    symbol VARCHAR(50),
    symbol_canonical VARCHAR(50) DEFAULT '',
    side VARCHAR(10),  -- long/short
    size DECIMAL(20,8),
    entry_price DECIMAL(20,8),
    current_price DECIMAL(20,8),
    highest_price DECIMAL(20,8) DEFAULT 0,
    lowest_price DECIMAL(20,8) DEFAULT 0,
    unrealized_pnl DECIMAL(20,8) DEFAULT 0,
    pnl_percent DECIMAL(10,4) DEFAULT 0,
    equity DECIMAL(20,8) DEFAULT 0,
    market_type VARCHAR(20) DEFAULT 'swap',
    credential_id INTEGER DEFAULT 0,
    inst_id VARCHAR(80) DEFAULT '',
    strategy_run_id INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(strategy_id, symbol, side)
);

CREATE INDEX IF NOT EXISTS idx_positions_user_id ON qd_strategy_positions(user_id);
CREATE INDEX IF NOT EXISTS idx_positions_strategy_id ON qd_strategy_positions(strategy_id);

-- =============================================================================
-- 4. Strategy Trades
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_strategy_trades (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    symbol VARCHAR(50),
    symbol_canonical VARCHAR(50) DEFAULT '',
    type VARCHAR(30),  -- open_long, close_short, etc.
    price DECIMAL(20,8),
    amount DECIMAL(20,8),
    value DECIMAL(20,8),
    commission DECIMAL(20,8) DEFAULT 0,
    commission_ccy VARCHAR(20) DEFAULT '',
    commission_quote DECIMAL(24,8),
    profit DECIMAL(20,8) DEFAULT 0,
    close_reason VARCHAR(64) DEFAULT '',
    matched_entry_price DECIMAL(20,8) DEFAULT 0,
    grid_matched_profit DECIMAL(20,8) DEFAULT 0,
    market_type VARCHAR(20) DEFAULT 'swap',
    credential_id INTEGER DEFAULT 0,
    inst_id VARCHAR(80) DEFAULT '',
    fill_source VARCHAR(32) DEFAULT '',
    pending_order_id INTEGER DEFAULT 0,
    strategy_run_id INTEGER DEFAULT 0,
    order_intent_id INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_user_id ON qd_strategy_trades(user_id);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_id ON qd_strategy_trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON qd_strategy_trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_latest ON qd_strategy_trades(strategy_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_symbol_canon ON qd_strategy_trades (strategy_id, market_type, symbol_canonical);
CREATE INDEX IF NOT EXISTS idx_positions_strategy_leg ON qd_strategy_positions (strategy_id, market_type, symbol_canonical, side);

-- Signal-only strategies use an isolated virtual account. These tables must
-- never be consumed by live broker reconciliation or exchange execution.
CREATE TABLE IF NOT EXISTS qd_strategy_virtual_accounts (
    strategy_id INTEGER PRIMARY KEY REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    initial_cash DECIMAL(24,8) NOT NULL DEFAULT 0,
    cash_balance DECIMAL(24,8) NOT NULL DEFAULT 0,
    realized_pnl DECIMAL(24,8) NOT NULL DEFAULT 0,
    total_commission DECIMAL(24,8) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE qd_billing_plans ADD COLUMN IF NOT EXISTS strategy_limit INTEGER NOT NULL DEFAULT 10;
ALTER TABLE qd_billing_plans ADD COLUMN IF NOT EXISTS referral_eligible BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS qd_referral_reward_accounts (
    user_id INTEGER PRIMARY KEY REFERENCES qd_users(id) ON DELETE CASCADE,
    available_balance DECIMAL(20,2) NOT NULL DEFAULT 0,
    pending_reward_balance DECIMAL(20,2) NOT NULL DEFAULT 0,
    pending_withdrawal_balance DECIMAL(20,2) NOT NULL DEFAULT 0,
    lifetime_earned DECIMAL(20,2) NOT NULL DEFAULT 0,
    lifetime_paid DECIMAL(20,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS qd_referral_withdrawals (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    currency VARCHAR(10) NOT NULL,
    chain VARCHAR(20) NOT NULL,
    address VARCHAR(160) NOT NULL,
    amount DECIMAL(20,2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    tx_hash VARCHAR(160) NOT NULL DEFAULT '',
    review_note TEXT NOT NULL DEFAULT '',
    operator_id INTEGER REFERENCES qd_users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMP,
    paid_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_referral_withdrawals_user ON qd_referral_withdrawals(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_referral_withdrawals_status ON qd_referral_withdrawals(status, created_at ASC);

CREATE TABLE IF NOT EXISTS qd_referral_reward_ledger (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    action VARCHAR(40) NOT NULL,
    amount DECIMAL(20,2) NOT NULL,
    available_delta DECIMAL(20,2) NOT NULL DEFAULT 0,
    pending_reward_delta DECIMAL(20,2) NOT NULL DEFAULT 0,
    pending_withdrawal_delta DECIMAL(20,2) NOT NULL DEFAULT 0,
    available_balance_after DECIMAL(20,2) NOT NULL DEFAULT 0,
    pending_reward_balance_after DECIMAL(20,2) NOT NULL DEFAULT 0,
    pending_withdrawal_balance_after DECIMAL(20,2) NOT NULL DEFAULT 0,
    source_key VARCHAR(255) UNIQUE,
    source_user_id INTEGER REFERENCES qd_users(id) ON DELETE SET NULL,
    source_order_ref VARCHAR(255) NOT NULL DEFAULT '',
    plan_code VARCHAR(64) NOT NULL DEFAULT '',
    reward_level INTEGER,
    reward_rate DECIMAL(8,4),
    status VARCHAR(20) NOT NULL DEFAULT 'posted',
    available_at TIMESTAMP,
    withdrawal_id INTEGER REFERENCES qd_referral_withdrawals(id) ON DELETE SET NULL,
    remark TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_referral_reward_ledger_user ON qd_referral_reward_ledger(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_referral_reward_ledger_release ON qd_referral_reward_ledger(status, available_at);

CREATE TABLE IF NOT EXISTS qd_strategy_virtual_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    pending_order_id INTEGER NOT NULL UNIQUE,
    order_intent_id INTEGER NOT NULL DEFAULT 0,
    symbol VARCHAR(80) NOT NULL,
    side VARCHAR(16) NOT NULL DEFAULT '',
    action VARCHAR(32) NOT NULL,
    order_type VARCHAR(16) NOT NULL DEFAULT 'market',
    requested_qty DECIMAL(24,10) NOT NULL DEFAULT 0,
    fill_qty DECIMAL(24,10) NOT NULL DEFAULT 0,
    reference_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    limit_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    fill_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    exchange_id VARCHAR(40) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
    leverage DECIMAL(12,4) NOT NULL DEFAULT 1,
    commission_rate DECIMAL(14,10) NOT NULL DEFAULT 0,
    commission_quote DECIMAL(24,8) NOT NULL DEFAULT 0,
    slippage_rate DECIMAL(14,10) NOT NULL DEFAULT 0,
    slippage_quote DECIMAL(24,8) NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL DEFAULT 'filled',
    reason VARCHAR(255) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    filled_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qd_strategy_virtual_positions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    symbol VARCHAR(80) NOT NULL,
    symbol_canonical VARCHAR(80) NOT NULL DEFAULT '',
    side VARCHAR(10) NOT NULL,
    size DECIMAL(24,10) NOT NULL DEFAULT 0,
    entry_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    current_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    highest_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    lowest_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    unrealized_pnl DECIMAL(24,8) NOT NULL DEFAULT 0,
    pnl_percent DECIMAL(14,6) NOT NULL DEFAULT 0,
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id, symbol_canonical, side)
);

CREATE TABLE IF NOT EXISTS qd_strategy_virtual_trades (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    virtual_order_id INTEGER NOT NULL UNIQUE REFERENCES qd_strategy_virtual_orders(id) ON DELETE CASCADE,
    pending_order_id INTEGER NOT NULL UNIQUE,
    order_intent_id INTEGER NOT NULL DEFAULT 0,
    symbol VARCHAR(80) NOT NULL,
    symbol_canonical VARCHAR(80) NOT NULL DEFAULT '',
    type VARCHAR(32) NOT NULL,
    side VARCHAR(10) NOT NULL,
    price DECIMAL(24,10) NOT NULL DEFAULT 0,
    amount DECIMAL(24,10) NOT NULL DEFAULT 0,
    value DECIMAL(24,8) NOT NULL DEFAULT 0,
    commission DECIMAL(24,8) NOT NULL DEFAULT 0,
    commission_quote DECIMAL(24,8) NOT NULL DEFAULT 0,
    profit DECIMAL(24,8) NOT NULL DEFAULT 0,
    close_reason VARCHAR(255) NOT NULL DEFAULT '',
    matched_entry_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    account_equity DECIMAL(24,8) NOT NULL DEFAULT 0,
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    exchange_id VARCHAR(40) NOT NULL DEFAULT '',
    leverage DECIMAL(12,4) NOT NULL DEFAULT 1,
    reference_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    commission_rate DECIMAL(14,10) NOT NULL DEFAULT 0,
    slippage_rate DECIMAL(14,10) NOT NULL DEFAULT 0,
    slippage_quote DECIMAL(24,8) NOT NULL DEFAULT 0,
    fill_source VARCHAR(32) NOT NULL DEFAULT 'virtual_signal',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_virtual_positions_strategy
ON qd_strategy_virtual_positions(strategy_id, symbol_canonical, side);
CREATE INDEX IF NOT EXISTS idx_virtual_trades_strategy_time
ON qd_strategy_virtual_trades(strategy_id, created_at);
CREATE INDEX IF NOT EXISTS idx_virtual_trades_strategy_latest
ON qd_strategy_virtual_trades(strategy_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_virtual_orders_strategy_time
ON qd_strategy_virtual_orders(strategy_id, created_at);

ALTER TABLE qd_strategy_virtual_orders
    ADD COLUMN IF NOT EXISTS exchange_id VARCHAR(40) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS limit_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
    ADD COLUMN IF NOT EXISTS leverage DECIMAL(12,4) NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS commission_rate DECIMAL(14,10) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS commission_quote DECIMAL(24,8) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS slippage_rate DECIMAL(14,10) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS slippage_quote DECIMAL(24,8) NOT NULL DEFAULT 0;

ALTER TABLE qd_strategy_virtual_trades
    ADD COLUMN IF NOT EXISTS exchange_id VARCHAR(40) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS leverage DECIMAL(12,4) NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS reference_price DECIMAL(24,10) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS commission_rate DECIMAL(14,10) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS slippage_rate DECIMAL(14,10) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS slippage_quote DECIMAL(24,8) NOT NULL DEFAULT 0;

-- Exchange-settled funding cash flow. Positive amount means the strategy
-- received funding; negative means it paid funding.
CREATE TABLE IF NOT EXISTS qd_strategy_funding_fees (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    credential_id INTEGER NOT NULL DEFAULT 0,
    exchange_id VARCHAR(40) NOT NULL DEFAULT '',
    symbol VARCHAR(50) NOT NULL DEFAULT '',
    asset VARCHAR(20) NOT NULL DEFAULT 'USDT',
    amount DECIMAL(24, 8) NOT NULL DEFAULT 0,
    allocation_ratio DECIMAL(20, 12) NOT NULL DEFAULT 1,
    external_id VARCHAR(160) NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (credential_id, exchange_id, external_id, strategy_id)
);
CREATE INDEX IF NOT EXISTS idx_strategy_funding_strategy_time
ON qd_strategy_funding_fees(strategy_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_strategy_funding_credential
ON qd_strategy_funding_fees(credential_id, exchange_id, external_id);

-- Broker-posted account activities attributed to strategy-generated orders.
-- Amount is the signed cash impact: negative is a fee/interest debit, positive is a credit.
CREATE TABLE IF NOT EXISTS qd_strategy_broker_activities (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    credential_id INTEGER NOT NULL DEFAULT 0,
    broker_id VARCHAR(40) NOT NULL DEFAULT '',
    activity_type VARCHAR(24) NOT NULL DEFAULT '',
    activity_subtype VARCHAR(24) NOT NULL DEFAULT '',
    symbol VARCHAR(50) NOT NULL DEFAULT '',
    currency VARCHAR(16) NOT NULL DEFAULT 'USD',
    amount DECIMAL(24, 8) NOT NULL DEFAULT 0,
    account_amount DECIMAL(24, 8) NOT NULL DEFAULT 0,
    allocation_ratio DECIMAL(20, 12) NOT NULL DEFAULT 1,
    allocation_reason VARCHAR(40) NOT NULL DEFAULT '',
    external_id VARCHAR(180) NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (credential_id, broker_id, external_id, strategy_id)
);
CREATE INDEX IF NOT EXISTS idx_broker_activity_strategy_time
ON qd_strategy_broker_activities(strategy_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_broker_activity_credential
ON qd_strategy_broker_activities(credential_id, broker_id, external_id);

-- Five-minute mark-to-market history used to calculate a strategy's true
-- local-day equity change, including the change in unrealized P&L on positions
-- carried across midnight.
CREATE TABLE IF NOT EXISTS qd_strategy_equity_snapshots (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    equity DECIMAL(24,8) NOT NULL DEFAULT 0,
    realized_pnl DECIMAL(24,8) NOT NULL DEFAULT 0,
    unrealized_pnl DECIMAL(24,8) NOT NULL DEFAULT 0,
    initial_capital DECIMAL(24,8),
    basis_reason VARCHAR(32) NOT NULL DEFAULT '',
    captured_at TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE qd_strategy_equity_snapshots
ADD COLUMN IF NOT EXISTS initial_capital DECIMAL(24,8);
ALTER TABLE qd_strategy_equity_snapshots
ADD COLUMN IF NOT EXISTS basis_reason VARCHAR(32) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_strategy_equity_snapshots_boundary
ON qd_strategy_equity_snapshots(strategy_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_equity_snapshots_user
ON qd_strategy_equity_snapshots(user_id, captured_at DESC);

-- Strategy AI review report history.
CREATE TABLE IF NOT EXISTS qd_strategy_review_reports (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    lookback_days INTEGER NOT NULL DEFAULT 30,
    language VARCHAR(20) DEFAULT 'zh-CN',
    include_ai BOOLEAN DEFAULT TRUE,
    ai_status VARCHAR(32) DEFAULT '',
    summary TEXT DEFAULT '',
    total_net_pnl DECIMAL(20,8) DEFAULT 0,
    total_return_pct DECIMAL(20,8) DEFAULT 0,
    win_rate DECIMAL(20,8) DEFAULT 0,
    profit_factor DECIMAL(20,8) DEFAULT 0,
    max_drawdown_pct DECIMAL(20,8) DEFAULT 0,
    report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_strategy_review_reports_strategy
    ON qd_strategy_review_reports(strategy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_review_reports_user
    ON qd_strategy_review_reports(user_id, created_at DESC);

-- L1 account position mirror (exchange truth per credential + inst_id + side)
CREATE TABLE IF NOT EXISTS qd_account_positions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    credential_id INTEGER NOT NULL DEFAULT 0,
    exchange_id VARCHAR(40) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    inst_id VARCHAR(80) NOT NULL DEFAULT '',
    symbol VARCHAR(50) NOT NULL DEFAULT '',
    side VARCHAR(10) NOT NULL DEFAULT '',
    size DECIMAL(24, 8) NOT NULL DEFAULT 0,
    entry_price DECIMAL(24, 8) DEFAULT 0,
    mark_price DECIMAL(24, 8) DEFAULT 0,
    unrealized_pnl DECIMAL(24, 8) DEFAULT 0,
    raw_json JSONB DEFAULT '{}'::jsonb,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (credential_id, market_type, inst_id, side)
);
CREATE INDEX IF NOT EXISTS idx_account_pos_user ON qd_account_positions(user_id);
CREATE INDEX IF NOT EXISTS idx_account_pos_cred ON qd_account_positions(credential_id, market_type);

-- L2 ownership layer: explicitly protected manual inventory plus drift state.
CREATE TABLE IF NOT EXISTS qd_position_reservations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    credential_id INTEGER NOT NULL DEFAULT 0,
    exchange_id VARCHAR(40) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    inst_id VARCHAR(80) NOT NULL DEFAULT '',
    symbol VARCHAR(50) NOT NULL DEFAULT '',
    symbol_canonical VARCHAR(50) NOT NULL DEFAULT '',
    side VARCHAR(10) NOT NULL DEFAULT '',
    coexistence_mode VARCHAR(20) NOT NULL DEFAULT 'strict',
    manual_reserved_qty DECIMAL(24, 8) NOT NULL DEFAULT 0,
    observed_account_qty DECIMAL(24, 8) NOT NULL DEFAULT 0,
    allocated_qty DECIMAL(24, 8) NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'ok',
    drift_reason VARCHAR(80) NOT NULL DEFAULT '',
    last_log_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, credential_id, market_type, symbol_canonical, side)
);
CREATE INDEX IF NOT EXISTS idx_position_reservation_leg
    ON qd_position_reservations(credential_id, market_type, symbol_canonical, side);
CREATE INDEX IF NOT EXISTS idx_position_reservation_blocked
    ON qd_position_reservations(user_id, status);

-- Grid cell ladder state (P2). Pre-placed limit orders / user-stream driven
-- fills will land here; today only the scaffolding lives in code (see
-- app.services.live_trading.grid_cells).
CREATE TABLE IF NOT EXISTS qd_grid_cells (
    id SERIAL PRIMARY KEY,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    symbol VARCHAR(50) NOT NULL,
    cell_index INTEGER NOT NULL,
    lower_price DECIMAL(20,8) NOT NULL,
    upper_price DECIMAL(20,8) NOT NULL,
    state VARCHAR(24) NOT NULL DEFAULT 'idle',
    leg_size DECIMAL(20,8) DEFAULT 0,
    leg_entry_price DECIMAL(20,8) DEFAULT 0,
    working_order_id VARCHAR(64) DEFAULT '',
    last_event_ts TIMESTAMP DEFAULT NOW(),
    extra JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT uniq_grid_cell UNIQUE(strategy_id, symbol, cell_index)
);
CREATE INDEX IF NOT EXISTS idx_grid_cells_strategy ON qd_grid_cells(strategy_id);
CREATE INDEX IF NOT EXISTS idx_grid_cells_state ON qd_grid_cells(strategy_id, state);

CREATE TABLE IF NOT EXISTS qd_grid_resting_orders (
    id SERIAL PRIMARY KEY,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    symbol VARCHAR(50) NOT NULL,
    cell_index INTEGER NOT NULL DEFAULT 0,
    purpose VARCHAR(24) NOT NULL,
    side VARCHAR(8) NOT NULL,
    pos_side VARCHAR(8) NOT NULL DEFAULT '',
    reduce_only BOOLEAN NOT NULL DEFAULT FALSE,
    price DECIMAL(24, 8) NOT NULL,
    quantity DECIMAL(24, 8) NOT NULL DEFAULT 0,
    quote_amount DECIMAL(24, 8) NOT NULL DEFAULT 0,
    client_order_id VARCHAR(64) NOT NULL DEFAULT '',
    exchange_order_id VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    filled_quantity DECIMAL(24, 8) NOT NULL DEFAULT 0,
    avg_fill_price DECIMAL(24, 8) NOT NULL DEFAULT 0,
    processed_fill_qty DECIMAL(24, 8) NOT NULL DEFAULT 0,
    extra JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_grid_resting_strategy ON qd_grid_resting_orders(strategy_id, status);

ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS grid_order_id BIGINT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_strategy_trades_grid_order
  ON qd_strategy_trades(grid_order_id) WHERE grid_order_id > 0;

-- =============================================================================
-- 5. Pending Orders Queue
-- =============================================================================

CREATE TABLE IF NOT EXISTS pending_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE SET NULL,
    symbol VARCHAR(50) NOT NULL,
    signal_type VARCHAR(30) NOT NULL,
    signal_ts BIGINT,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    order_intent_id INTEGER NOT NULL DEFAULT 0,
    idempotency_key VARCHAR(180) NOT NULL,
    market_type VARCHAR(20) DEFAULT 'swap',
    order_type VARCHAR(20) DEFAULT 'market',
    amount DECIMAL(20,8) DEFAULT 0,
    price DECIMAL(20,8) DEFAULT 0,
    execution_mode VARCHAR(20) DEFAULT 'signal',
    status VARCHAR(20) DEFAULT 'pending',
    priority INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 10,
    last_error TEXT DEFAULT '',
    payload_json TEXT DEFAULT '',
    dispatch_note TEXT DEFAULT '',
    exchange_id VARCHAR(50) DEFAULT '',
    credential_id INTEGER NOT NULL DEFAULT 0,
    inst_id VARCHAR(80) NOT NULL DEFAULT '',
    exchange_order_id VARCHAR(100) DEFAULT '',
    exchange_response_json TEXT DEFAULT '',
    filled DECIMAL(20,8) DEFAULT 0,
    avg_price DECIMAL(20,8) DEFAULT 0,
    executed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP,
    sent_at TIMESTAMP
);

UPDATE pending_orders
SET idempotency_key = 'pending-order-' || id::text
WHERE idempotency_key IS NULL OR idempotency_key = '';
ALTER TABLE pending_orders ALTER COLUMN idempotency_key SET NOT NULL;
ALTER TABLE pending_orders ALTER COLUMN idempotency_key DROP DEFAULT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_orders_idempotency_key ON pending_orders(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_pending_orders_user_id ON pending_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_pending_orders_status ON pending_orders(status);
CREATE INDEX IF NOT EXISTS idx_pending_orders_strategy_id ON pending_orders(strategy_id);
CREATE INDEX IF NOT EXISTS idx_pending_orders_strategy_run_id ON pending_orders(strategy_run_id);
ALTER TABLE pending_orders
    ADD COLUMN IF NOT EXISTS runtime_fencing_token BIGINT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_pending_orders_runtime_fence
    ON pending_orders(strategy_id, runtime_fencing_token)
    WHERE runtime_fencing_token > 0;

-- =============================================================================
-- 6. Strategy Notifications
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_strategy_notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    symbol VARCHAR(50) DEFAULT '',
    signal_type VARCHAR(30) DEFAULT '',
    channels VARCHAR(255) DEFAULT '',
    title VARCHAR(255) DEFAULT '',
    message TEXT DEFAULT '',
    payload_json TEXT DEFAULT '',
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON qd_strategy_notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_strategy_id ON qd_strategy_notifications(strategy_id);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON qd_strategy_notifications(is_read);

-- =============================================================================
-- 6a. Indicator Signal Alerts
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_indicator_signal_alerts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    indicator_id INTEGER NOT NULL,
    indicator_name VARCHAR(160) DEFAULT '',
    market VARCHAR(32) NOT NULL,
    symbol VARCHAR(64) NOT NULL,
    symbol_name VARCHAR(128) DEFAULT '',
    timeframe VARCHAR(16) NOT NULL DEFAULT '1D',
    signal_keys TEXT DEFAULT '[]',
    channels TEXT DEFAULT '["browser"]',
    target_json TEXT DEFAULT '{}',
    param_json TEXT DEFAULT '{}',
    status VARCHAR(16) NOT NULL DEFAULT 'running',
    last_bar_time VARCHAR(64) DEFAULT '',
    last_fingerprint VARCHAR(255) DEFAULT '',
    last_signal_payload TEXT DEFAULT '{}',
    last_error TEXT DEFAULT '',
    check_count INTEGER NOT NULL DEFAULT 0,
    trigger_count INTEGER NOT NULL DEFAULT 0,
    next_check_at TIMESTAMP DEFAULT NOW(),
    last_checked_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_indicator_signal_alerts_user_id ON qd_indicator_signal_alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_indicator_signal_alerts_status_next ON qd_indicator_signal_alerts(status, next_check_at);
CREATE INDEX IF NOT EXISTS idx_indicator_signal_alerts_indicator_id ON qd_indicator_signal_alerts(indicator_id);

-- =============================================================================
-- 6b. Strategy runtime logs (dashboard / API)
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_strategy_logs (
    id SERIAL PRIMARY KEY,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    level VARCHAR(20) DEFAULT 'info',
    message TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategy_logs_strategy_id ON qd_strategy_logs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_logs_timestamp ON qd_strategy_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_strategy_logs_strategy_latest ON qd_strategy_logs(strategy_id, id DESC);

-- =============================================================================
-- 7. Indicator Codes
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_indicator_codes (
   id serial4 NOT NULL,
   user_id int4 DEFAULT 1 NOT NULL,
   is_buy int4 DEFAULT 0 NOT NULL,
   end_time int8 DEFAULT 1 NOT NULL,
   name varchar(255) DEFAULT ''::character varying NOT NULL,
   code text NULL,
   description text DEFAULT ''::text NULL,
   publish_to_community int4 DEFAULT 0 NOT NULL,
   pricing_type varchar(20) DEFAULT 'free'::character varying NOT NULL,
   price numeric(10, 2) DEFAULT 0 NOT NULL,
   is_encrypted int4 DEFAULT 0 NOT NULL,
   preview_image varchar(500) DEFAULT ''::character varying NULL,
   vip_free boolean DEFAULT false,
   createtime int8 NULL,
   updatetime int8 NULL,
   created_at timestamp DEFAULT now(),
   updated_at timestamp DEFAULT now(),
   purchase_count int4 DEFAULT 0 NULL,
   avg_rating numeric(3, 2) DEFAULT 0 NULL,
   rating_count int4 DEFAULT 0 NULL,
   view_count int4 DEFAULT 0 NULL,
   review_status varchar(20) DEFAULT 'approved'::character varying NULL,
   review_note text DEFAULT ''::text NULL,
   reviewed_at timestamp NULL,
   reviewed_by int4 NULL,
   asset_type varchar(32) DEFAULT 'indicator'::character varying NULL,


    source_indicator_id int4 NULL,
    source_script_source_id int4 NULL,
    source_strategy_id int4 NULL,

    -- (zh-CN / en-US / ja-JP 绛?锛沶ame_i18n / description_i18n 鏄?LLM 缈昏瘧鐢熸垚鐨?
    -- JSONB锛岀粨鏋勫舰濡?{"en-US": "...", "zh-CN": "...", ...}銆?

    -- 瑙?app/services/indicator_translator.py 涓?community_service.py:_localize_indicator銆?
    source_language varchar(16) DEFAULT NULL,
    name_i18n        jsonb       DEFAULT NULL,
    description_i18n jsonb       DEFAULT NULL,
    CONSTRAINT qd_indicator_codes_pkey PRIMARY KEY (id),
   CONSTRAINT qd_indicator_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES qd_users(id) ON DELETE CASCADE

);

-- Upgrade existing installations created before marketplace asset metadata was
-- added. CREATE TABLE IF NOT EXISTS does not add columns to an existing table.
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS vip_free boolean DEFAULT false;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS review_status varchar(20) DEFAULT 'approved';
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS review_note text DEFAULT '';
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS reviewed_at timestamp NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS reviewed_by int4 NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS asset_type varchar(32) DEFAULT 'indicator';
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS source_indicator_id int4 NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS source_script_source_id int4 NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS source_strategy_id int4 NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS source_language varchar(16) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS name_i18n jsonb DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS description_i18n jsonb DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_contract jsonb DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_contract_version int4 DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_contract_hash varchar(64) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_binding_mode varchar(24) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_type varchar(24) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_direction_mode varchar(24) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_primary_frequency varchar(16) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_markets varchar(255) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_market_types varchar(255) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS strategy_timeframes varchar(255) DEFAULT NULL;
-- Canonical marketplace applicability metadata. Legacy strategy_* columns
-- above remain only for upgrade/rollback compatibility.
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_contract jsonb DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_contract_version int4 DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_contract_hash varchar(64) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_binding_mode varchar(24) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_strategy_type varchar(24) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_direction_mode varchar(24) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_execution_mode varchar(24) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_execution_frequency varchar(16) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_confirmation_frequencies varchar(255) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_markets varchar(255) DEFAULT NULL;
ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS marketplace_market_types varchar(255) DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_indicator_codes_user_id ON qd_indicator_codes USING btree (user_id);
CREATE INDEX IF NOT EXISTS idx_indicator_review_status ON qd_indicator_codes USING btree (review_status);
CREATE INDEX IF NOT EXISTS idx_indicator_codes_source ON qd_indicator_codes USING btree (source_indicator_id);
CREATE INDEX IF NOT EXISTS idx_indicator_codes_source_script ON qd_indicator_codes USING btree (source_script_source_id);
CREATE INDEX IF NOT EXISTS idx_indicator_codes_source_strategy ON qd_indicator_codes USING btree (source_strategy_id);

-- One strategy source owns one active marketplace listing per author.  Older
-- installations may already contain duplicates from the former insert-only
-- publish flow.  Keep the most established card active and only unpublish the
-- extras, preserving their purchase/comment/version records for auditability.
WITH ranked_script_listings AS (
  SELECT id,
         ROW_NUMBER() OVER (
           PARTITION BY user_id, source_script_source_id
           ORDER BY COALESCE(purchase_count, 0) DESC,
                    COALESCE(rating_count, 0) DESC,
                    COALESCE(view_count, 0) DESC,
                    id ASC
         ) AS listing_rank
  FROM qd_indicator_codes
  WHERE source_script_source_id IS NOT NULL
    AND COALESCE(asset_type, 'indicator') = 'script_template'
    AND publish_to_community = 1
)
UPDATE qd_indicator_codes AS listing
SET publish_to_community = 0,
    updated_at = NOW(),
    updatetime = EXTRACT(EPOCH FROM NOW())::bigint
FROM ranked_script_listings AS ranked
WHERE listing.id = ranked.id
  AND ranked.listing_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_indicator_codes_active_script_source
ON qd_indicator_codes USING btree (user_id, source_script_source_id)
WHERE source_script_source_id IS NOT NULL
  AND COALESCE(asset_type, 'indicator') = 'script_template'
  AND publish_to_community = 1;
CREATE INDEX IF NOT EXISTS idx_indicator_strategy_binding ON qd_indicator_codes USING btree (strategy_binding_mode);
CREATE INDEX IF NOT EXISTS idx_indicator_strategy_type ON qd_indicator_codes USING btree (strategy_type);
CREATE INDEX IF NOT EXISTS idx_indicator_strategy_frequency ON qd_indicator_codes USING btree (strategy_primary_frequency);
CREATE INDEX IF NOT EXISTS idx_indicator_marketplace_binding ON qd_indicator_codes USING btree (marketplace_binding_mode);
CREATE INDEX IF NOT EXISTS idx_indicator_marketplace_strategy_type ON qd_indicator_codes USING btree (marketplace_strategy_type);
CREATE INDEX IF NOT EXISTS idx_indicator_marketplace_execution_mode ON qd_indicator_codes USING btree (marketplace_execution_mode);
CREATE INDEX IF NOT EXISTS idx_indicator_marketplace_execution_frequency ON qd_indicator_codes USING btree (marketplace_execution_frequency);

-- Copy legacy marketplace metadata into the canonical namespace.  This is
-- idempotent and intentionally leaves the old columns intact for rollback.
UPDATE qd_indicator_codes
SET marketplace_contract = COALESCE(
        marketplace_contract,
        CASE WHEN strategy_contract IS NULL THEN NULL ELSE
          strategy_contract || jsonb_build_object(
            'contract_version', 2,
            'execution_mode', COALESCE(
              strategy_contract->>'execution_mode',
              CASE
                WHEN code ~ E'run_(daily|weekly|monthly)\\s*\\(' AND code ~ E'def\\s+handle_data\\s*\\(' THEN 'hybrid'
                WHEN code ~ E'run_(daily|weekly|monthly)\\s*\\(' THEN 'scheduled'
                ELSE 'bar'
              END
            ),
            'execution_frequency', COALESCE(
              strategy_contract->>'execution_frequency',
              strategy_contract->>'driving_frequency',
              strategy_contract->>'primary_frequency',
              ''
            ),
            'confirmation_frequencies', COALESCE(
              strategy_contract->'confirmation_frequencies',
              (SELECT COALESCE(jsonb_agg(value), '[]'::jsonb)
               FROM jsonb_array_elements_text(COALESCE(strategy_contract->'frequencies', '[]'::jsonb)) AS f(value)
               WHERE value <> COALESCE(
                 strategy_contract->>'execution_frequency',
                 strategy_contract->>'driving_frequency',
                 strategy_contract->>'primary_frequency',
                 ''
               ))
            )
          )
        END
    ),
    marketplace_contract_version = COALESCE(
      marketplace_contract_version,
      CASE WHEN strategy_contract IS NULL THEN NULL ELSE 2 END
    ),
    marketplace_contract_hash = COALESCE(marketplace_contract_hash, strategy_contract_hash),
    marketplace_binding_mode = COALESCE(marketplace_binding_mode, strategy_binding_mode),
    marketplace_strategy_type = COALESCE(marketplace_strategy_type, strategy_type),
    marketplace_direction_mode = COALESCE(marketplace_direction_mode, strategy_direction_mode),
    marketplace_execution_mode = COALESCE(
      marketplace_execution_mode,
      strategy_contract->>'execution_mode',
      CASE
        WHEN code ~ E'run_(daily|weekly|monthly)\\s*\\(' AND code ~ E'def\\s+handle_data\\s*\\(' THEN 'hybrid'
        WHEN code ~ E'run_(daily|weekly|monthly)\\s*\\(' THEN 'scheduled'
        ELSE 'bar'
      END
    ),
    marketplace_execution_frequency = COALESCE(
      marketplace_execution_frequency,
      strategy_contract->>'execution_frequency',
      strategy_primary_frequency
    ),
    marketplace_confirmation_frequencies = COALESCE(
      marketplace_confirmation_frequencies,
      CASE WHEN jsonb_exists(marketplace_contract, 'confirmation_frequencies') THEN
        '|' || array_to_string(
          ARRAY(SELECT jsonb_array_elements_text(marketplace_contract->'confirmation_frequencies')),
          '|'
        ) || '|'
      ELSE NULL END
    ),
    marketplace_markets = COALESCE(marketplace_markets, strategy_markets),
    marketplace_market_types = COALESCE(marketplace_market_types, strategy_market_types)
WHERE COALESCE(asset_type, 'indicator') = 'script_template';

-- Populate the confirmation-frequency search index after the canonical JSON
-- contract is visible (UPDATE expressions above read the pre-update row).
UPDATE qd_indicator_codes
SET marketplace_confirmation_frequencies = CASE
      WHEN jsonb_array_length(COALESCE(marketplace_contract->'confirmation_frequencies', '[]'::jsonb)) = 0
        THEN NULL
      ELSE '|' || array_to_string(
        ARRAY(SELECT jsonb_array_elements_text(marketplace_contract->'confirmation_frequencies')),
        '|'
      ) || '|'
    END
WHERE COALESCE(asset_type, 'indicator') = 'script_template'
  AND marketplace_contract IS NOT NULL
  AND marketplace_confirmation_frequencies IS NULL;

CREATE TABLE IF NOT EXISTS qd_indicator_code_versions (
   id serial4 NOT NULL,
   indicator_id int4 NOT NULL,
   user_id int4 NOT NULL,
   version_no int4 NOT NULL,
   name varchar(255) DEFAULT ''::character varying NOT NULL,
   description text DEFAULT ''::text NULL,
   code text NOT NULL,
   created_at timestamp DEFAULT now(),
   CONSTRAINT qd_indicator_code_versions_pkey PRIMARY KEY (id),
   CONSTRAINT qd_indicator_code_versions_indicator_fkey FOREIGN KEY (indicator_id) REFERENCES qd_indicator_codes(id) ON DELETE CASCADE,
   CONSTRAINT qd_indicator_code_versions_user_fkey FOREIGN KEY (user_id) REFERENCES qd_users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_indicator_code_versions_indicator ON qd_indicator_code_versions USING btree (indicator_id, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_indicator_code_versions_user ON qd_indicator_code_versions USING btree (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_indicator_code_versions_no ON qd_indicator_code_versions USING btree (indicator_id, version_no);

-- Bounded AI authoring memory and unapplied code candidates. These records are
-- intentionally separate from canonical indicator code/version history.
CREATE TABLE IF NOT EXISTS qd_ai_workspace_threads (
   id SERIAL PRIMARY KEY,
   user_id INTEGER NOT NULL,
   asset_type VARCHAR(32) NOT NULL,
   asset_id INTEGER NOT NULL,
   title VARCHAR(255) DEFAULT '',
   summary_json TEXT,
   summary_until_message_id INTEGER,
   summary_version INTEGER DEFAULT 0,
   created_at TIMESTAMP DEFAULT NOW(),
   updated_at TIMESTAMP DEFAULT NOW(),
   UNIQUE (user_id, asset_type, asset_id)
);
CREATE TABLE IF NOT EXISTS qd_ai_workspace_messages (
   id SERIAL PRIMARY KEY,
   thread_id INTEGER NOT NULL REFERENCES qd_ai_workspace_threads(id) ON DELETE CASCADE,
   user_id INTEGER NOT NULL,
   role VARCHAR(16) NOT NULL,
   content TEXT NOT NULL,
   message_type VARCHAR(32) DEFAULT 'chat',
   change_id INTEGER,
   metadata_json TEXT,
   created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS qd_ai_workspace_changes (
   id SERIAL PRIMARY KEY,
   thread_id INTEGER NOT NULL REFERENCES qd_ai_workspace_threads(id) ON DELETE CASCADE,
   user_id INTEGER NOT NULL,
   asset_type VARCHAR(32) NOT NULL,
   asset_id INTEGER NOT NULL,
   base_code_hash VARCHAR(64) NOT NULL,
   candidate_code TEXT NOT NULL,
   change_summary_json TEXT,
   validation_json TEXT,
   status VARCHAR(24) DEFAULT 'candidate',
   applied_version_no INTEGER,
   created_at TIMESTAMP DEFAULT NOW(),
   updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qd_ai_workspace_threads_asset ON qd_ai_workspace_threads(user_id, asset_type, asset_id);
CREATE INDEX IF NOT EXISTS idx_qd_ai_workspace_messages_thread ON qd_ai_workspace_messages(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_qd_ai_workspace_changes_thread ON qd_ai_workspace_changes(thread_id, id);

-- =============================================================================
-- 10. Watchlist
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_watchlist (
    id SERIAL PRIMARY KEY,
    user_id INTEGER DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    name VARCHAR(100) DEFAULT '',
    exchange_id VARCHAR(50) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
    instrument_id VARCHAR(120) NOT NULL DEFAULT '',
    settle_currency VARCHAR(20) NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_watchlist_market_context UNIQUE(
        user_id, market, symbol, exchange_id, market_type, instrument_id
    )
);

CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON qd_watchlist(user_id);

ALTER TABLE qd_watchlist ADD COLUMN IF NOT EXISTS exchange_id VARCHAR(50) NOT NULL DEFAULT '';
ALTER TABLE qd_watchlist ADD COLUMN IF NOT EXISTS market_type VARCHAR(20) NOT NULL DEFAULT 'spot';
ALTER TABLE qd_watchlist ADD COLUMN IF NOT EXISTS instrument_id VARCHAR(120) NOT NULL DEFAULT '';
ALTER TABLE qd_watchlist ADD COLUMN IF NOT EXISTS settle_currency VARCHAR(20) NOT NULL DEFAULT '';
ALTER TABLE qd_watchlist DROP CONSTRAINT IF EXISTS qd_watchlist_user_id_market_symbol_key;
ALTER TABLE qd_watchlist DROP CONSTRAINT IF EXISTS uq_watchlist_asset;
DROP INDEX IF EXISTS uq_watchlist_asset;
CREATE UNIQUE INDEX IF NOT EXISTS uq_watchlist_market_context
  ON qd_watchlist(user_id, market, symbol, exchange_id, market_type, instrument_id);

-- =============================================================================
-- 10A. Strategy universes and point-in-time membership
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_universes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES qd_users(id) ON DELETE CASCADE,
    code VARCHAR(80) NOT NULL,
    name VARCHAR(160) NOT NULL DEFAULT '',
    name_i18n_key VARCHAR(160) NOT NULL DEFAULT '',
    market VARCHAR(50) NOT NULL DEFAULT '',
    universe_type VARCHAR(32) NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'manual',
    source_ref VARCHAR(160) NOT NULL DEFAULT '',
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(24) NOT NULL DEFAULT 'active',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_universes_system_code
  ON qd_universes(code) WHERE is_system = TRUE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_universes_user_code
  ON qd_universes(user_id, code) WHERE is_system = FALSE;
CREATE INDEX IF NOT EXISTS idx_universes_user
  ON qd_universes(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS qd_universe_members (
    id BIGSERIAL PRIMARY KEY,
    universe_id INTEGER NOT NULL REFERENCES qd_universes(id) ON DELETE CASCADE,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(80) NOT NULL,
    name VARCHAR(160) NOT NULL DEFAULT '',
    exchange_id VARCHAR(50) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
    instrument_id VARCHAR(120) NOT NULL DEFAULT '',
    settle_currency VARCHAR(20) NOT NULL DEFAULT '',
    valid_from DATE NOT NULL DEFAULT DATE '1900-01-01',
    valid_to DATE,
    member_weight DOUBLE PRECISION,
    member_rank INTEGER,
    source_version VARCHAR(120) NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_universe_member_valid_range
      CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_universe_member_interval
  ON qd_universe_members(
    universe_id, market, symbol, exchange_id, market_type, instrument_id, valid_from
  );
CREATE INDEX IF NOT EXISTS idx_universe_members_asof
  ON qd_universe_members(universe_id, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_universe_members_symbol
  ON qd_universe_members(market, symbol);

CREATE TABLE IF NOT EXISTS qd_universe_snapshots (
    snapshot_id VARCHAR(36) PRIMARY KEY,
    universe_id INTEGER NOT NULL REFERENCES qd_universes(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    source_version VARCHAR(120) NOT NULL DEFAULT '',
    content_hash VARCHAR(64) NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 0,
    members_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_universe_snapshot_content
  ON qd_universe_snapshots(universe_id, user_id, as_of_date, content_hash);
CREATE INDEX IF NOT EXISTS idx_universe_snapshots_lookup
  ON qd_universe_snapshots(user_id, universe_id, as_of_date DESC);

CREATE TABLE IF NOT EXISTS qd_fundamental_snapshots (
    id BIGSERIAL PRIMARY KEY,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(80) NOT NULL,
    period_end DATE NOT NULL,
    available_at DATE NOT NULL,
    frequency VARCHAR(20) NOT NULL DEFAULT 'quarterly',
    currency VARCHAR(20) NOT NULL DEFAULT '',
    revenue DOUBLE PRECISION,
    net_income DOUBLE PRECISION,
    book_value DOUBLE PRECISION,
    shareholder_equity DOUBLE PRECISION,
    total_debt DOUBLE PRECISION,
    free_cash_flow DOUBLE PRECISION,
    shares_outstanding DOUBLE PRECISION,
    market_cap DOUBLE PRECISION,
    pe_ratio DOUBLE PRECISION,
    pb_ratio DOUBLE PRECISION,
    return_on_equity DOUBLE PRECISION,
    revenue_growth DOUBLE PRECISION,
    debt_to_equity DOUBLE PRECISION,
    source VARCHAR(80) NOT NULL DEFAULT 'manual',
    source_version VARCHAR(120) NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (market, symbol, period_end, available_at, source)
);

ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS revenue DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS net_income DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS book_value DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS shareholder_equity DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS total_debt DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS free_cash_flow DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS shares_outstanding DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS market_cap DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS pe_ratio DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS pb_ratio DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS return_on_equity DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS revenue_growth DOUBLE PRECISION;
ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS debt_to_equity DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_fundamental_snapshots_pit
  ON qd_fundamental_snapshots (market, symbol, available_at, period_end);

CREATE TABLE IF NOT EXISTS qd_portfolio_rebalance_plans (
    plan_id VARCHAR(36) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE SET NULL,
    portfolio_id VARCHAR(96) NOT NULL DEFAULT '',
    universe_id INTEGER REFERENCES qd_universes(id) ON DELETE SET NULL,
    universe_snapshot_id VARCHAR(36) NOT NULL DEFAULT '',
    rebalance_group_id VARCHAR(128) NOT NULL,
    execution_mode VARCHAR(24) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'planned',
    signal_time TIMESTAMP NOT NULL,
    scheduled_execution_time TIMESTAMP,
    equity DOUBLE PRECISION NOT NULL DEFAULT 0,
    cash DOUBLE PRECISION NOT NULL DEFAULT 0,
    target_weights_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_weights_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    diagnostics_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    notification_id INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_portfolio_execution_mode
      CHECK (execution_mode IN ('live', 'notify_only'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_rebalance_group
  ON qd_portfolio_rebalance_plans(user_id, strategy_id, rebalance_group_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_rebalance_plans_user
  ON qd_portfolio_rebalance_plans(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS qd_portfolio_rebalance_orders (
    id BIGSERIAL PRIMARY KEY,
    plan_id VARCHAR(36) NOT NULL REFERENCES qd_portfolio_rebalance_plans(plan_id) ON DELETE CASCADE,
    idempotency_key VARCHAR(180) NOT NULL,
    market VARCHAR(50) NOT NULL DEFAULT '',
    symbol VARCHAR(80) NOT NULL,
    exchange_id VARCHAR(50) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
    side VARCHAR(10) NOT NULL,
    action VARCHAR(24) NOT NULL,
    quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    reference_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    estimated_notional DOUBLE PRECISION NOT NULL DEFAULT 0,
    estimated_fee DOUBLE PRECISION NOT NULL DEFAULT 0,
    current_weight DOUBLE PRECISION NOT NULL DEFAULT 0,
    target_weight DOUBLE PRECISION NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'planned',
    order_intent_id INTEGER NOT NULL DEFAULT 0,
    pending_order_id BIGINT NOT NULL DEFAULT 0,
    actual_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    actual_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    acknowledged_at TIMESTAMP NULL,
    error_code VARCHAR(120) NOT NULL DEFAULT '',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_rebalance_order_key
  ON qd_portfolio_rebalance_orders(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_portfolio_rebalance_orders_plan
  ON qd_portfolio_rebalance_orders(plan_id, status);

INSERT INTO qd_universes
  (code, name_i18n_key, market, universe_type, source, source_ref, is_system, status)
VALUES
  ('watchlist', 'universe.catalog.watchlist', 'Mixed', 'watchlist', 'watchlist', 'current_user', TRUE, 'active'),
  ('csi300', 'universe.catalog.csi300', 'CNStock', 'index', 'provider', '000300.SH', TRUE, 'data_required'),
  ('csi500', 'universe.catalog.csi500', 'CNStock', 'index', 'provider', '000905.SH', TRUE, 'data_required'),
  ('csi1000', 'universe.catalog.csi1000', 'CNStock', 'index', 'public_snapshot', '000852.SH', TRUE, 'data_required'),
  ('csi800', 'universe.catalog.csi800', 'CNStock', 'index', 'public_snapshot', '000906.SH', TRUE, 'data_required'),
  ('sse50', 'universe.catalog.sse50', 'CNStock', 'index', 'public_snapshot', '000016.SH', TRUE, 'data_required'),
  ('sp500', 'universe.catalog.sp500', 'USStock', 'index', 'provider', 'SPX', TRUE, 'data_required'),
  ('nasdaq100', 'universe.catalog.nasdaq100', 'USStock', 'index', 'provider', 'NDX', TRUE, 'data_required'),
  ('dow30', 'universe.catalog.dow30', 'USStock', 'index', 'public_snapshot', 'DJIA', TRUE, 'data_required'),
  ('sp400', 'universe.catalog.sp400', 'USStock', 'index', 'public_snapshot', 'SP400', TRUE, 'data_required'),
  ('sp600', 'universe.catalog.sp600', 'USStock', 'index', 'public_snapshot', 'SP600', TRUE, 'data_required'),
  ('russell1000', 'universe.catalog.russell1000', 'USStock', 'index', 'public_snapshot', 'RUI', TRUE, 'data_required'),
  ('etf_pool', 'universe.catalog.etfPool', 'Mixed', 'etf', 'provider', 'etf_pool', TRUE, 'data_required'),
  ('crypto_top100', 'universe.catalog.cryptoTop100', 'Crypto', 'market_cap', 'provider', 'top100', TRUE, 'data_required'),
  ('hk_equities', 'universe.catalog.hkEquities', 'HKStock', 'market', 'provider', 'hk_equities', TRUE, 'data_required')
ON CONFLICT DO NOTHING;

INSERT INTO qd_universes
  (code, name_i18n_key, market, universe_type, source, source_ref, is_system, status)
VALUES
  ('hk_core', 'universe.catalog.hkCore', 'HKStock', 'market', 'symbol_master', 'HKStock:hot:equity', TRUE, 'active'),
  ('hk_etf', 'universe.catalog.hkEtf', 'HKStock', 'etf', 'symbol_master', 'HKStock:hot:etf', TRUE, 'active'),
  ('us_etf', 'universe.catalog.usEtf', 'USStock', 'etf', 'symbol_master', 'USStock:hot:etf', TRUE, 'active')
ON CONFLICT DO NOTHING;

INSERT INTO qd_universes
  (code, name, name_i18n_key, market, universe_type, source, source_ref, is_system, status)
VALUES
  ('hk_hsi', 'Hang Seng Index', 'universe.catalog.hkHsi', 'HKStock', 'index', 'public_snapshot', 'HSI', TRUE, 'data_required'),
  ('hk_tech30', 'Hang Seng TECH 30', 'universe.catalog.hkTech30', 'HKStock', 'index', 'public_snapshot', 'HSTECH', TRUE, 'data_required'),
  ('hk_china_enterprises50', 'Hang Seng China Enterprises 50', 'universe.catalog.hkChinaEnterprises50', 'HKStock', 'index', 'public_snapshot', 'HSCEI', TRUE, 'data_required'),
  ('hk_stock_connect', 'Hong Kong Stock Connect', 'universe.catalog.hkStockConnect', 'HKStock', 'market', 'public_snapshot', 'HK_STOCK_CONNECT', TRUE, 'data_required')
ON CONFLICT DO NOTHING;

INSERT INTO qd_universes
  (code, name, name_i18n_key, market, universe_type, source, source_ref, is_system, status)
VALUES
  ('us_sector_information_technology', 'US Information Technology', 'universe.catalog.usSectorInformationTechnology', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Information Technology', TRUE, 'data_required'),
  ('us_sector_health_care', 'US Health Care', 'universe.catalog.usSectorHealthCare', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Health Care', TRUE, 'data_required'),
  ('us_sector_financials', 'US Financials', 'universe.catalog.usSectorFinancials', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Financials', TRUE, 'data_required'),
  ('us_sector_consumer_discretionary', 'US Consumer Discretionary', 'universe.catalog.usSectorConsumerDiscretionary', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Consumer Discretionary', TRUE, 'data_required'),
  ('us_sector_communication_services', 'US Communication Services', 'universe.catalog.usSectorCommunicationServices', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Communication Services', TRUE, 'data_required'),
  ('us_sector_industrials', 'US Industrials', 'universe.catalog.usSectorIndustrials', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Industrials', TRUE, 'data_required'),
  ('us_sector_consumer_staples', 'US Consumer Staples', 'universe.catalog.usSectorConsumerStaples', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Consumer Staples', TRUE, 'data_required'),
  ('us_sector_energy', 'US Energy', 'universe.catalog.usSectorEnergy', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Energy', TRUE, 'data_required'),
  ('us_sector_utilities', 'US Utilities', 'universe.catalog.usSectorUtilities', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Utilities', TRUE, 'data_required'),
  ('us_sector_real_estate', 'US Real Estate', 'universe.catalog.usSectorRealEstate', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Real Estate', TRUE, 'data_required'),
  ('us_sector_materials', 'US Materials', 'universe.catalog.usSectorMaterials', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Materials', TRUE, 'data_required')
ON CONFLICT DO NOTHING;

UPDATE qd_universes SET status = 'deprecated', updated_at = NOW()
WHERE code IN ('hk_hsi_core50', 'hk_high_dividend50') AND is_system = TRUE;

UPDATE qd_universes SET source_ref = 'USStock:hot:etf', updated_at = NOW()
WHERE code = 'us_etf' AND is_system = TRUE;

UPDATE qd_universes SET source_ref = 'HKStock:hot:etf', updated_at = NOW()
WHERE code = 'hk_etf' AND is_system = TRUE;

UPDATE qd_universes SET status = 'deprecated', updated_at = NOW()
WHERE code IN ('etf_pool', 'hk_equities', 'hk_core') AND is_system = TRUE;

-- =============================================================================
-- 11. Analysis Tasks
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_analysis_tasks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    model VARCHAR(100) DEFAULT '',
    language VARCHAR(20) DEFAULT 'en-US',
    status VARCHAR(20) DEFAULT 'completed',
    result_json TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analysis_tasks_user_id ON qd_analysis_tasks(user_id);

CREATE TABLE IF NOT EXISTS qd_ai_strategy_decisions (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    decision_key VARCHAR(64) NOT NULL,
    profile_name VARCHAR(80) NOT NULL DEFAULT '',
    model_id VARCHAR(160) NOT NULL DEFAULT '',
    prompt_version VARCHAR(80) NOT NULL DEFAULT '',
    prompt_hash VARCHAR(64) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    symbol VARCHAR(80) NOT NULL DEFAULT '',
    as_of_time VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(24) NOT NULL,
    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code VARCHAR(120) NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_strategy_decision_key
  ON qd_ai_strategy_decisions(user_id, strategy_id, decision_key);
CREATE INDEX IF NOT EXISTS idx_ai_strategy_decisions_lookup
  ON qd_ai_strategy_decisions(user_id, strategy_id, symbol, created_at DESC);

-- =============================================================================
-- 12. Backtest Runs
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_backtest_runs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER,
    source_id INTEGER NOT NULL,
    strategy_name VARCHAR(255) DEFAULT '',
    market VARCHAR(50) NOT NULL DEFAULT '',
    symbol VARCHAR(50) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
    timeframe VARCHAR(10) NOT NULL DEFAULT '',
    start_date VARCHAR(20) NOT NULL DEFAULT '',
    end_date VARCHAR(20) NOT NULL DEFAULT '',
    initial_capital DECIMAL(20,8) DEFAULT 10000,
    commission DECIMAL(10,6) DEFAULT 0.001,
    slippage DECIMAL(10,6) DEFAULT 0,
    leverage INTEGER DEFAULT 1,
    params_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    engine_version VARCHAR(50) DEFAULT '',
    code_hash VARCHAR(128) DEFAULT '',
    status VARCHAR(20) DEFAULT 'success',
    error_message TEXT DEFAULT '',
    result_json TEXT DEFAULT '',
    result_compacted BOOLEAN NOT NULL DEFAULT FALSE,
    total_return DOUBLE PRECISION,
    win_rate DOUBLE PRECISION,
    total_trades INTEGER,
    total_executions INTEGER,
    max_drawdown DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    result_status VARCHAR(32) DEFAULT 'unknown',
    data_kind VARCHAR(32) DEFAULT 'unknown',
    benchmark_total_return DOUBLE PRECISION,
    summary_backfilled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS total_return DOUBLE PRECISION;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS win_rate DOUBLE PRECISION;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS total_trades INTEGER;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS total_executions INTEGER;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS max_drawdown DOUBLE PRECISION;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS sharpe_ratio DOUBLE PRECISION;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS result_status VARCHAR(32) DEFAULT 'unknown';
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS data_kind VARCHAR(32) DEFAULT 'unknown';
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS benchmark_total_return DOUBLE PRECISION;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS summary_backfilled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS result_compacted BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
DECLARE
    run_row RECORD;
BEGIN
    FOR run_row IN
        SELECT id, result_json
        FROM qd_backtest_runs
        WHERE summary_backfilled = FALSE
    LOOP
        BEGIN
            UPDATE qd_backtest_runs
            SET total_return = ((regexp_match(run_row.result_json, '"totalReturn"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION,
                win_rate = ((regexp_match(run_row.result_json, '"winRate"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION,
                total_trades = ((regexp_match(run_row.result_json, '"totalTrades"[[:space:]]*:[[:space:]]*([0-9]+)'))[1])::INTEGER,
                total_executions = ((regexp_match(run_row.result_json, '"totalExecutions"[[:space:]]*:[[:space:]]*([0-9]+)'))[1])::INTEGER,
                result_status = COALESCE((regexp_match(run_row.result_json, '"resultStatus"[[:space:]]*:[[:space:]]*"([^"]+)"'))[1], 'unknown'),
                data_kind = COALESCE((regexp_match(run_row.result_json, '"dataProvenance"[[:space:]]*:[[:space:]]*[{][^}]*"kind"[[:space:]]*:[[:space:]]*"([^"]+)"'))[1], 'unknown'),
                benchmark_total_return = ((regexp_match(run_row.result_json, '"benchmarkTotalReturn"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION,
                summary_backfilled = TRUE
            WHERE id = run_row.id;
        EXCEPTION WHEN OTHERS THEN
            UPDATE qd_backtest_runs
            SET result_status = 'unknown', data_kind = 'unknown', summary_backfilled = TRUE
            WHERE id = run_row.id;
        END;
    END LOOP;
END $$;

-- Historical backtests used to duplicate several large time-series arrays in
-- result_json. Keep the authoritative detail tables and scalar summaries, then
-- release the monolithic payload. Legacy rows stay marked non-compacted so the
-- service hydrates their bounded trades/equity detail tables on demand.
DO $$
DECLARE
    run_row RECORD;
BEGIN
    FOR run_row IN
        SELECT id, result_json
        FROM qd_backtest_runs
        WHERE result_compacted = FALSE AND result_json IS NOT NULL AND result_json <> '{}'
    LOOP
        BEGIN
            UPDATE qd_backtest_runs
            SET max_drawdown = COALESCE(
                    max_drawdown,
                    ((regexp_match(run_row.result_json, '"maxDrawdown"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION
                ),
                sharpe_ratio = COALESCE(
                    sharpe_ratio,
                    ((regexp_match(run_row.result_json, '"sharpeRatio"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION
                ),
                result_json = '{}'
            WHERE id = run_row.id;
        EXCEPTION WHEN OTHERS THEN
            UPDATE qd_backtest_runs SET result_json = '{}' WHERE id = run_row.id;
        END;
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_backtest_runs_user_id ON qd_backtest_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_id ON qd_backtest_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_source_id ON qd_backtest_runs(source_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_user_recent ON qd_backtest_runs(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_user_source_recent ON qd_backtest_runs(user_id, source_id, id DESC);

CREATE TABLE IF NOT EXISTS qd_backtest_trades (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    strategy_id INTEGER,
    trade_index INTEGER DEFAULT 0,
    trade_time VARCHAR(64) DEFAULT '',
    trade_type VARCHAR(64) DEFAULT '',
    side VARCHAR(32) DEFAULT '',
    price DOUBLE PRECISION DEFAULT 0,
    amount DOUBLE PRECISION DEFAULT 0,
    profit DOUBLE PRECISION DEFAULT 0,
    balance DOUBLE PRECISION DEFAULT 0,
    reason VARCHAR(64) DEFAULT '',
    payload_json TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_id ON qd_backtest_trades(run_id);

CREATE TABLE IF NOT EXISTS qd_backtest_equity_points (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL,
    point_index INTEGER DEFAULT 0,
    point_time VARCHAR(64) DEFAULT '',
    point_value DOUBLE PRECISION DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_equity_points_run_id ON qd_backtest_equity_points(run_id);

CREATE TABLE IF NOT EXISTS qd_factor_research_runs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    source_id INTEGER,
    source_name VARCHAR(255) DEFAULT '',
    universe_id INTEGER,
    universe_name VARCHAR(255) DEFAULT '',
    research_mode VARCHAR(20) NOT NULL DEFAULT 'strategy',
    market VARCHAR(100) DEFAULT '',
    timeframe VARCHAR(10) DEFAULT '',
    start_date VARCHAR(20) NOT NULL DEFAULT '',
    end_date VARCHAR(20) NOT NULL DEFAULT '',
    factor_id VARCHAR(64) NOT NULL DEFAULT '',
    groups_count INTEGER NOT NULL DEFAULT 5,
    holding_period INTEGER NOT NULL DEFAULT 5,
    commission DECIMAL(10,6) DEFAULT 0.001,
    slippage DECIMAL(10,6) DEFAULT 0,
    neutralize_industry BOOLEAN NOT NULL DEFAULT FALSE,
    universe_size INTEGER NOT NULL DEFAULT 0,
    manifest_json TEXT NOT NULL DEFAULT '{}',
    code_hash VARCHAR(128) DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    rank_ic DOUBLE PRECISION,
    icir DOUBLE PRECISION,
    coverage DOUBLE PRECISION,
    net_long_short_return DOUBLE PRECISION,
    observation_count INTEGER,
    summary_backfilled BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(20) DEFAULT 'success',
    error_message TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS rank_ic DOUBLE PRECISION;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS icir DOUBLE PRECISION;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS coverage DOUBLE PRECISION;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS net_long_short_return DOUBLE PRECISION;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS observation_count INTEGER;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS summary_backfilled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE qd_factor_research_runs ALTER COLUMN source_id DROP NOT NULL;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS universe_id INTEGER;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS universe_name VARCHAR(255) DEFAULT '';
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS research_mode VARCHAR(20) NOT NULL DEFAULT 'strategy';

DO $$
DECLARE
    run_row RECORD;
BEGIN
    FOR run_row IN
        SELECT id, result_json
        FROM qd_factor_research_runs
        WHERE summary_backfilled = FALSE
    LOOP
        BEGIN
            UPDATE qd_factor_research_runs
            SET rank_ic = ((regexp_match(run_row.result_json, '"rankIc"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION,
                icir = ((regexp_match(run_row.result_json, '"icir"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION,
                coverage = ((regexp_match(run_row.result_json, '"coverage"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION,
                net_long_short_return = ((regexp_match(run_row.result_json, '"netLongShortReturn"[[:space:]]*:[[:space:]]*(-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?)'))[1])::DOUBLE PRECISION,
                observation_count = 0,
                summary_backfilled = TRUE
            WHERE id = run_row.id;
        EXCEPTION WHEN OTHERS THEN
            UPDATE qd_factor_research_runs SET observation_count = 0, summary_backfilled = TRUE WHERE id = run_row.id;
        END;
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_factor_research_runs_user_id
  ON qd_factor_research_runs(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_factor_research_runs_source_id
  ON qd_factor_research_runs(source_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_factor_research_runs_user_source_recent
  ON qd_factor_research_runs(user_id, source_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_factor_research_runs_user_universe_recent
  ON qd_factor_research_runs(user_id, universe_id, id DESC);

-- =============================================================================
-- 13. Exchange Credentials
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_exchange_credentials (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    name VARCHAR(100) DEFAULT '',
    exchange_id VARCHAR(50) NOT NULL,
    api_key_hint VARCHAR(50) DEFAULT '',
    encrypted_config TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exchange_credentials_user_id ON qd_exchange_credentials(user_id);

-- =============================================================================
-- 14. Manual Positions
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_manual_positions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    name VARCHAR(100) DEFAULT '',
    side VARCHAR(10) DEFAULT 'long',
    quantity DECIMAL(20,8) NOT NULL DEFAULT 0,
    entry_price DECIMAL(20,8) NOT NULL DEFAULT 0,
    entry_time BIGINT,
    notes TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    group_name VARCHAR(100) DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, market, symbol, side, group_name),
    CONSTRAINT uq_manual_positions_id_user UNIQUE(id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_manual_positions_user_id ON qd_manual_positions(user_id);

-- =============================================================================
-- 15. Position Alerts
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_position_alerts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    position_id INTEGER,
    market VARCHAR(50) DEFAULT '',
    symbol VARCHAR(50) DEFAULT '',
    alert_type VARCHAR(30) NOT NULL,
    threshold DECIMAL(20,8) NOT NULL DEFAULT 0,
    notification_config TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    is_triggered INTEGER DEFAULT 0,
    last_triggered_at TIMESTAMP,
    trigger_count INTEGER DEFAULT 0,
    repeat_interval INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT fk_position_alerts_owned_position
        FOREIGN KEY (position_id, user_id)
        REFERENCES qd_manual_positions(id, user_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_position_alerts_user_id ON qd_position_alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_position_alerts_position_id ON qd_position_alerts(position_id);

-- =============================================================================
-- 16. Position Monitors
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_position_monitors (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    name VARCHAR(100) DEFAULT '',
    position_ids TEXT DEFAULT '',
    monitor_type VARCHAR(20) DEFAULT 'ai',
    config TEXT DEFAULT '',
    notification_config TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    last_run_at TIMESTAMP,
    next_run_at TIMESTAMP,
    last_result TEXT DEFAULT '',
    run_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_position_monitors_user_id ON qd_position_monitors(user_id);

-- =============================================================================
-- 17. Market Symbols (Seed Data)
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_market_symbols (
    id SERIAL PRIMARY KEY,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    name VARCHAR(255) DEFAULT '',
    exchange VARCHAR(50) DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
    instrument_id VARCHAR(120) NOT NULL DEFAULT '',
    settle_currency VARCHAR(20) NOT NULL DEFAULT '',
    asset_class VARCHAR(20) NOT NULL DEFAULT 'crypto',
    product_type VARCHAR(32) NOT NULL DEFAULT 'crypto',
    api_family VARCHAR(24) NOT NULL DEFAULT 'spot',
    underlying_market VARCHAR(32) NOT NULL DEFAULT '',
    underlying_symbol VARCHAR(50) NOT NULL DEFAULT '',
    product_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_updated_at TIMESTAMPTZ,
    currency VARCHAR(10) DEFAULT '',
    is_active INTEGER DEFAULT 1,
    is_hot INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(market, symbol, exchange, market_type, instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_market_symbols_market ON qd_market_symbols(market);
CREATE INDEX IF NOT EXISTS idx_market_symbols_is_hot ON qd_market_symbols(market, is_hot);
CREATE INDEX IF NOT EXISTS idx_market_symbols_market_upper_symbol
  ON qd_market_symbols(market, UPPER(symbol));

CREATE TABLE IF NOT EXISTS qd_market_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    trigger_type VARCHAR(20) NOT NULL DEFAULT 'manual',
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    result JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_sync_runs_running
  ON qd_market_sync_runs ((status)) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_market_sync_runs_started
  ON qd_market_sync_runs(started_at DESC);

ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS market_type VARCHAR(20) NOT NULL DEFAULT 'spot';
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS instrument_id VARCHAR(120) NOT NULL DEFAULT '';
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS settle_currency VARCHAR(20) NOT NULL DEFAULT '';
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS asset_class VARCHAR(20) NOT NULL DEFAULT 'crypto';
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS product_type VARCHAR(32) NOT NULL DEFAULT 'crypto';
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS api_family VARCHAR(24) NOT NULL DEFAULT 'spot';
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS underlying_market VARCHAR(32) NOT NULL DEFAULT '';
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS underlying_symbol VARCHAR(50) NOT NULL DEFAULT '';
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS product_meta JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS metadata_updated_at TIMESTAMPTZ;
UPDATE qd_market_symbols SET asset_class = 'equity'
WHERE market IN ('CNStock', 'HKStock', 'USStock', 'MOEX') AND asset_class = 'crypto';
UPDATE qd_market_symbols SET asset_class = 'forex'
WHERE market = 'Forex' AND asset_class = 'crypto';
UPDATE qd_market_symbols SET asset_class = 'futures'
WHERE market = 'Futures' AND asset_class = 'crypto';
UPDATE qd_market_symbols SET is_hot = 1, sort_order = GREATEST(sort_order, 80)
WHERE market = 'HKStock' AND asset_class = 'etf' AND symbol IN (
  '02800','02801','02823','02828','02840','02846','03032','03033','03037',
  '03040','03067','03075','03088','03110','03188','03191','03416','03437'
);
UPDATE qd_market_symbols SET is_hot = 1, sort_order = GREATEST(sort_order, 80)
WHERE market = 'USStock' AND asset_class = 'etf' AND symbol IN (
  'SPY','QQQ','IWM','DIA','VTI','VOO','IVV','EFA','EEM','AGG','BND','TLT','IEF',
  'GLD','SLV','USO','XLF','XLK','XLE','XLV','XLI','XLY','XLP','XLU','VNQ','ARKK',
  'HYG','LQD','SCHD','VUG','VTV'
);
ALTER TABLE qd_market_symbols DROP CONSTRAINT IF EXISTS qd_market_symbols_market_symbol_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_symbols_venue_instrument
  ON qd_market_symbols(market, symbol, exchange, market_type, instrument_id);
CREATE INDEX IF NOT EXISTS idx_market_symbols_product_catalog
  ON qd_market_symbols(market, exchange, market_type, product_type, is_active);

UPDATE qd_market_symbols
SET is_active = 0
WHERE market = 'Crypto'
  AND exchange <> ''
  AND exchange NOT IN ('binance', 'bitget', 'bybit', 'okx', 'gate', 'htx');

CREATE TABLE IF NOT EXISTS qd_market_symbol_aliases (
    id SERIAL PRIMARY KEY,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    alias VARCHAR(255) NOT NULL,
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(market, symbol, alias)
);

CREATE INDEX IF NOT EXISTS idx_market_symbol_aliases_lookup
  ON qd_market_symbol_aliases(market, alias);
CREATE INDEX IF NOT EXISTS idx_market_symbol_aliases_upper_lookup
  ON qd_market_symbol_aliases(market, UPPER(alias));

-- Seed data: Hot symbols for each market
INSERT INTO qd_market_symbols (market, symbol, name, exchange, currency, is_active, is_hot, sort_order) VALUES
-- USStock (US Stocks)
('USStock', 'AAPL', 'Apple Inc.', 'NASDAQ', 'USD', 1, 1, 100),
('USStock', 'MSFT', 'Microsoft Corporation', 'NASDAQ', 'USD', 1, 1, 99),
('USStock', 'GOOGL', 'Alphabet Inc.', 'NASDAQ', 'USD', 1, 1, 98),
('USStock', 'AMZN', 'Amazon.com Inc.', 'NASDAQ', 'USD', 1, 1, 97),
('USStock', 'TSLA', 'Tesla, Inc.', 'NASDAQ', 'USD', 1, 1, 96),
('USStock', 'META', 'Meta Platforms Inc.', 'NASDAQ', 'USD', 1, 1, 95),
('USStock', 'NVDA', 'NVIDIA Corporation', 'NASDAQ', 'USD', 1, 1, 94),
('USStock', 'JPM', 'JPMorgan Chase & Co.', 'NYSE', 'USD', 1, 1, 93),
('USStock', 'V', 'Visa Inc.', 'NYSE', 'USD', 1, 1, 92),
('USStock', 'JNJ', 'Johnson & Johnson', 'NYSE', 'USD', 1, 1, 91),
-- Crypto (major + popular altcoins)
('Crypto', 'BTC/USDT', 'Bitcoin', 'Binance', 'USDT', 1, 1, 100),
('Crypto', 'ETH/USDT', 'Ethereum', 'Binance', 'USDT', 1, 1, 99),
('Crypto', 'BNB/USDT', 'BNB', 'Binance', 'USDT', 1, 1, 98),
('Crypto', 'SOL/USDT', 'Solana', 'Binance', 'USDT', 1, 1, 97),
('Crypto', 'XRP/USDT', 'Ripple', 'Binance', 'USDT', 1, 1, 96),
('Crypto', 'ADA/USDT', 'Cardano', 'Binance', 'USDT', 1, 1, 95),
('Crypto', 'DOGE/USDT', 'Dogecoin', 'Binance', 'USDT', 1, 1, 94),
('Crypto', 'DOT/USDT', 'Polkadot', 'Binance', 'USDT', 1, 1, 93),
('Crypto', 'POL/USDT', 'Polygon', 'Binance', 'USDT', 1, 1, 92),
('Crypto', 'AVAX/USDT', 'Avalanche', 'Binance', 'USDT', 1, 1, 91),
-- Layer 1 / Layer 2
('Crypto', 'LINK/USDT', 'Chainlink', 'Binance', 'USDT', 1, 1, 90),
('Crypto', 'UNI/USDT', 'Uniswap', 'Binance', 'USDT', 1, 1, 89),
('Crypto', 'ATOM/USDT', 'Cosmos', 'Binance', 'USDT', 1, 1, 88),
('Crypto', 'LTC/USDT', 'Litecoin', 'Binance', 'USDT', 1, 1, 87),
('Crypto', 'FIL/USDT', 'Filecoin', 'Binance', 'USDT', 1, 1, 86),
('Crypto', 'NEAR/USDT', 'NEAR Protocol', 'Binance', 'USDT', 1, 1, 85),
('Crypto', 'APT/USDT', 'Aptos', 'Binance', 'USDT', 1, 1, 84),
('Crypto', 'SUI/USDT', 'Sui', 'Binance', 'USDT', 1, 1, 83),
('Crypto', 'ARB/USDT', 'Arbitrum', 'Binance', 'USDT', 1, 1, 82),
('Crypto', 'OP/USDT', 'Optimism', 'Binance', 'USDT', 1, 1, 81),
('Crypto', 'SEI/USDT', 'Sei', 'Binance', 'USDT', 1, 1, 80),
('Crypto', 'TIA/USDT', 'Celestia', 'Binance', 'USDT', 1, 1, 79),
('Crypto', 'INJ/USDT', 'Injective', 'Binance', 'USDT', 1, 1, 78),
('Crypto', 'FTM/USDT', 'Fantom', 'Binance', 'USDT', 1, 1, 77),
('Crypto', 'ALGO/USDT', 'Algorand', 'Binance', 'USDT', 1, 1, 76),
('Crypto', 'HBAR/USDT', 'Hedera', 'Binance', 'USDT', 1, 1, 75),
('Crypto', 'ICP/USDT', 'Internet Computer', 'Binance', 'USDT', 1, 1, 74),
('Crypto', 'VET/USDT', 'VeChain', 'Binance', 'USDT', 1, 1, 73),
('Crypto', 'SAND/USDT', 'The Sandbox', 'Binance', 'USDT', 1, 1, 72),
('Crypto', 'MANA/USDT', 'Decentraland', 'Binance', 'USDT', 1, 1, 71),
-- DeFi
('Crypto', 'AAVE/USDT', 'Aave', 'Binance', 'USDT', 1, 1, 70),
('Crypto', 'MKR/USDT', 'Maker', 'Binance', 'USDT', 1, 1, 69),
('Crypto', 'CRV/USDT', 'Curve DAO', 'Binance', 'USDT', 1, 1, 68),
('Crypto', 'COMP/USDT', 'Compound', 'Binance', 'USDT', 1, 1, 67),
('Crypto', 'SNX/USDT', 'Synthetix', 'Binance', 'USDT', 1, 1, 66),
('Crypto', 'SUSHI/USDT', 'SushiSwap', 'Binance', 'USDT', 1, 1, 65),
('Crypto', 'DYDX/USDT', 'dYdX', 'Binance', 'USDT', 1, 1, 64),
('Crypto', 'LDO/USDT', 'Lido DAO', 'Binance', 'USDT', 1, 1, 63),
('Crypto', 'PENDLE/USDT', 'Pendle', 'Binance', 'USDT', 1, 1, 62),
('Crypto', 'JUP/USDT', 'Jupiter', 'Binance', 'USDT', 1, 1, 61),
-- Meme coins
('Crypto', 'SHIB/USDT', 'Shiba Inu', 'Binance', 'USDT', 1, 1, 60),
('Crypto', 'PEPE/USDT', 'Pepe', 'Binance', 'USDT', 1, 1, 59),
('Crypto', 'WIF/USDT', 'dogwifhat', 'Binance', 'USDT', 1, 1, 58),
('Crypto', 'FLOKI/USDT', 'Floki', 'Binance', 'USDT', 1, 1, 57),
('Crypto', 'BONK/USDT', 'Bonk', 'Binance', 'USDT', 1, 1, 56),
('Crypto', 'MEME/USDT', 'Memecoin', 'Binance', 'USDT', 1, 1, 55),
('Crypto', 'TURBO/USDT', 'Turbo', 'Binance', 'USDT', 1, 1, 54),
('Crypto', 'NEIRO/USDT', 'Neiro', 'Binance', 'USDT', 1, 1, 53),
-- AI / Infra
('Crypto', 'RENDER/USDT', 'Render', 'Binance', 'USDT', 1, 1, 52),
('Crypto', 'FET/USDT', 'Fetch.ai', 'Binance', 'USDT', 1, 1, 51),
('Crypto', 'RNDR/USDT', 'Render Network', 'Binance', 'USDT', 1, 1, 50),
('Crypto', 'TAO/USDT', 'Bittensor', 'Binance', 'USDT', 1, 1, 49),
('Crypto', 'WLD/USDT', 'Worldcoin', 'Binance', 'USDT', 1, 1, 48),
('Crypto', 'AR/USDT', 'Arweave', 'Binance', 'USDT', 1, 1, 47),
('Crypto', 'STX/USDT', 'Stacks', 'Binance', 'USDT', 1, 1, 46),
('Crypto', 'ORDI/USDT', 'ORDI', 'Binance', 'USDT', 1, 1, 45),
-- Others
('Crypto', 'TRX/USDT', 'Tron', 'Binance', 'USDT', 1, 1, 44),
('Crypto', 'ETC/USDT', 'Ethereum Classic', 'Binance', 'USDT', 1, 1, 43),
('Crypto', 'THETA/USDT', 'Theta Network', 'Binance', 'USDT', 1, 1, 42),
('Crypto', 'EOS/USDT', 'EOS', 'Binance', 'USDT', 1, 1, 41),
('Crypto', 'XLM/USDT', 'Stellar', 'Binance', 'USDT', 1, 1, 40),
('Crypto', 'GALA/USDT', 'Gala', 'Binance', 'USDT', 1, 1, 39),
('Crypto', 'IMX/USDT', 'Immutable X', 'Binance', 'USDT', 1, 1, 38),
('Crypto', 'CFX/USDT', 'Conflux', 'Binance', 'USDT', 1, 1, 37),
('Crypto', 'JASMY/USDT', 'JasmyCoin', 'Binance', 'USDT', 1, 1, 36),
('Crypto', 'CHZ/USDT', 'Chiliz', 'Binance', 'USDT', 1, 1, 35),
('Crypto', 'GMT/USDT', 'STEPN', 'Binance', 'USDT', 1, 1, 34),
('Crypto', 'CAKE/USDT', 'PancakeSwap', 'Binance', 'USDT', 1, 1, 33),
('Crypto', '1INCH/USDT', '1inch', 'Binance', 'USDT', 1, 1, 32),
('Crypto', 'ENS/USDT', 'Ethereum Name Service', 'Binance', 'USDT', 1, 1, 31),
('Crypto', 'BLUR/USDT', 'Blur', 'Binance', 'USDT', 1, 1, 30),
-- Forex
('Forex', 'XAUUSD', 'Gold/USD', 'Forex', 'USD', 1, 1, 100),
('Forex', 'XAGUSD', 'Silver/USD', 'Forex', 'USD', 1, 1, 99),
('Forex', 'EURUSD', 'Euro/US Dollar', 'Forex', 'USD', 1, 1, 98),
('Forex', 'GBPUSD', 'British Pound/US Dollar', 'Forex', 'USD', 1, 1, 97),
('Forex', 'USDJPY', 'US Dollar/Japanese Yen', 'Forex', 'USD', 1, 1, 96),
('Forex', 'AUDUSD', 'Australian Dollar/US Dollar', 'Forex', 'USD', 1, 1, 95),
('Forex', 'USDCAD', 'US Dollar/Canadian Dollar', 'Forex', 'USD', 1, 1, 94),
('Forex', 'NZDUSD', 'New Zealand Dollar/US Dollar', 'Forex', 'USD', 1, 1, 93),
('Forex', 'USDCHF', 'US Dollar/Swiss Franc', 'Forex', 'EUR', 1, 1, 92),
('Forex', 'EURJPY', 'Euro/Japanese Yen', 'Forex', 'EUR', 1, 1, 91),
-- Futures
('Futures', 'CL', 'WTI Crude Oil', 'NYMEX', 'USD', 1, 1, 100),
('Futures', 'GC', 'Gold', 'COMEX', 'USD', 1, 1, 99),
('Futures', 'SI', 'Silver', 'COMEX', 'USD', 1, 1, 98),
('Futures', 'NG', 'Natural Gas', 'NYMEX', 'USD', 1, 1, 97),
('Futures', 'HG', 'Copper', 'COMEX', 'USD', 1, 1, 96),
('Futures', 'ZC', 'Corn', 'CBOT', 'USD', 1, 1, 95),
('Futures', 'ZS', 'Soybeans', 'CBOT', 'USD', 1, 1, 94),
('Futures', 'ZW', 'Wheat', 'CBOT', 'USD', 1, 1, 93),
('Futures', 'ES', 'S&P 500 E-mini', 'CME', 'USD', 1, 1, 92),
('Futures', 'NQ', 'NASDAQ 100 E-mini', 'CME', 'USD', 1, 1, 91),
-- A-share hot symbols use the canonical exchange identifier from the symbol master.
('CNStock', '600519', '贵州茅台', 'CN', 'CNY', 1, 1, 100),
('CNStock', '600036', '招商银行', 'CN', 'CNY', 1, 1, 99),
('CNStock', '601318', '中国平安', 'CN', 'CNY', 1, 1, 98),
('CNStock', '600900', '长江电力', 'CN', 'CNY', 1, 1, 97),
('CNStock', '601899', '紫金矿业', 'CN', 'CNY', 1, 1, 96),
('CNStock', '000858', '五粮液', 'CN', 'CNY', 1, 1, 95),
('CNStock', '000333', '美的集团', 'CN', 'CNY', 1, 1, 94),
('CNStock', '002594', '比亚迪', 'CN', 'CNY', 1, 1, 93),
('CNStock', '300750', '宁德时代', 'CN', 'CNY', 1, 1, 92),
('CNStock', '000001', '平安银行', 'CN', 'CNY', 1, 1, 91),
-- Hong Kong hot symbols.
('HKStock', '00700', '腾讯控股', 'HKEX', 'HKD', 1, 1, 100),
('HKStock', '09988', '阿里巴巴-W', 'HKEX', 'HKD', 1, 1, 99),
('HKStock', '03690', '美团-W', 'HKEX', 'HKD', 1, 1, 98),
('HKStock', '01810', '小米集团-W', 'HKEX', 'HKD', 1, 1, 97),
('HKStock', '00939', '建设银行', 'HKEX', 'HKD', 1, 1, 96),
('HKStock', '01299', '友邦保险', 'HKEX', 'HKD', 1, 1, 95),
('HKStock', '02318', '中国平安', 'HKEX', 'HKD', 1, 1, 94),
('HKStock', '00388', '香港交易所', 'HKEX', 'HKD', 1, 1, 93),
('HKStock', '00883', '中国海洋石油', 'HKEX', 'HKD', 1, 1, 92),
('HKStock', '01398', '工商银行', 'HKEX', 'HKD', 1, 1, 91),
-- MOEX (Moscow Exchange) blue chips
-- Tickers are the MOEX ISS instrument codes; resolve_symbol_name() upgrades
-- the display name from MOEX ISS securities/<sym>.json on first lookup.
('MOEX', 'SBER',  'Sberbank',          'MOEX', 'RUB', 1, 1, 100),
('MOEX', 'GAZP',  'Gazprom',           'MOEX', 'RUB', 1, 1, 99),
('MOEX', 'LKOH',  'Lukoil',            'MOEX', 'RUB', 1, 1, 98),
('MOEX', 'ROSN',  'Rosneft',           'MOEX', 'RUB', 1, 1, 97),
('MOEX', 'GMKN',  'Nornickel',         'MOEX', 'RUB', 1, 1, 96),
('MOEX', 'NVTK',  'Novatek',           'MOEX', 'RUB', 1, 1, 95),
('MOEX', 'TATN',  'Tatneft',           'MOEX', 'RUB', 1, 1, 94),
('MOEX', 'VTBR',  'VTB Bank',          'MOEX', 'RUB', 1, 1, 93),
('MOEX', 'MGNT',  'Magnit',            'MOEX', 'RUB', 1, 1, 92),
('MOEX', 'YNDX',  'Yandex',            'MOEX', 'RUB', 1, 1, 91),
('MOEX', 'SBERP', 'Sberbank Preferred','MOEX', 'RUB', 1, 1, 90),
('MOEX', 'PLZL',  'Polyus',            'MOEX', 'RUB', 1, 1, 89),
('MOEX', 'CHMF',  'Severstal',         'MOEX', 'RUB', 1, 1, 88),
('MOEX', 'ALRS',  'Alrosa',            'MOEX', 'RUB', 1, 1, 87),
('MOEX', 'MOEX',  'Moscow Exchange',   'MOEX', 'RUB', 1, 1, 86)
ON CONFLICT (market, symbol, exchange, market_type, instrument_id) DO NOTHING;

-- Remove legacy A-share rows that used venue-specific exchange identifiers.
-- Canonical symbol-master rows use exchange = 'CN'; the old identifiers caused
-- duplicate search results because exchange is part of the uniqueness key.
UPDATE qd_market_symbols canonical
SET is_hot = GREATEST(canonical.is_hot, legacy.is_hot),
    sort_order = GREATEST(canonical.sort_order, legacy.sort_order)
FROM qd_market_symbols legacy
WHERE canonical.market = 'CNStock'
  AND canonical.exchange = 'CN'
  AND legacy.market = canonical.market
  AND legacy.symbol = canonical.symbol
  AND legacy.exchange IN ('SSE', 'SZSE')
  AND legacy.market_type = canonical.market_type
  AND legacy.instrument_id = canonical.instrument_id;

DELETE FROM qd_market_symbols legacy
USING qd_market_symbols canonical
WHERE legacy.market = 'CNStock'
  AND legacy.exchange IN ('SSE', 'SZSE')
  AND canonical.market = legacy.market
  AND canonical.symbol = legacy.symbol
  AND canonical.exchange = 'CN'
  AND canonical.market_type = legacy.market_type
  AND canonical.instrument_id = legacy.instrument_id;

-- =============================================================================
-- 19.5. Analysis Memory (Fast AI Analysis Memory System)
-- =============================================================================
-- Stores AI analysis results for history, feedback, and learning.

CREATE TABLE IF NOT EXISTS qd_analysis_memory (
    id SERIAL PRIMARY KEY,
    user_id INT,                                -- User who created this analysis (for filtering)
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    decision VARCHAR(10) NOT NULL,
    confidence INT DEFAULT 50,
    price_at_analysis DECIMAL(24, 8),
    summary TEXT,
    reasons JSONB,
    scores JSONB,
    indicators_snapshot JSONB,
    raw_result JSONB,                           -- Full analysis result for history replay
    consensus_score DECIMAL(24, 8),
    consensus_abs DECIMAL(24, 8),
    agreement_ratio DECIMAL(10, 6),
    quality_multiplier DECIMAL(10, 6),
    created_at TIMESTAMP DEFAULT NOW(),
    validated_at TIMESTAMP,
    actual_outcome VARCHAR(20),
    actual_return_pct DECIMAL(10, 4),
    was_correct BOOLEAN,
    user_feedback VARCHAR(20),                  -- helpful/not_helpful
    feedback_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analysis_memory_symbol ON qd_analysis_memory(market, symbol);
CREATE INDEX IF NOT EXISTS idx_analysis_memory_created ON qd_analysis_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_memory_validated ON qd_analysis_memory(validated_at) WHERE validated_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_analysis_memory_user ON qd_analysis_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_analysis_memory_user_created ON qd_analysis_memory(user_id, created_at DESC);

-- Migration: Add user_id column to existing qd_analysis_memory table
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_analysis_memory' AND column_name = 'user_id'
    ) THEN
        ALTER TABLE qd_analysis_memory ADD COLUMN user_id INT;
        CREATE INDEX IF NOT EXISTS idx_analysis_memory_user ON qd_analysis_memory(user_id);
        RAISE NOTICE 'Added user_id column to qd_analysis_memory';
    END IF;
END $$;

-- =============================================================================
-- 20. Migration: Add token_version for single-client login
-- =============================================================================
-- This migration adds token_version column for enforcing single-client login.
-- When a user logs in from a new device, the token_version is incremented,
-- invalidating all previous tokens and forcing other sessions to logout.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_users' AND column_name = 'token_version'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN token_version INTEGER DEFAULT 1;
        RAISE NOTICE 'Added token_version column to qd_users table';
    END IF;
END $$;

-- =============================================================================
-- 20b. Migration: user profile timezone (IANA)
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_users' AND column_name = 'timezone'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN timezone VARCHAR(64) DEFAULT '';
        RAISE NOTICE 'Added timezone column to qd_users table';
    END IF;
END $$;

-- =============================================================================
-- 20c. Migration: password_changed_at (initial password reminder)
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_users' AND column_name = 'password_changed_at'
    ) THEN
        ALTER TABLE qd_users ADD COLUMN password_changed_at TIMESTAMP NULL;
        -- One-time backfill when upgrading old DBs (skip on fresh installs after bootstrap user exists)
        UPDATE qd_users
        SET password_changed_at = COALESCE(updated_at, created_at, NOW())
        WHERE password_changed_at IS NULL;
        RAISE NOTICE 'Added password_changed_at column to qd_users table (existing users backfilled)';
    END IF;
END $$;

-- =============================================================================
-- 20e. Stateful Strategy API runtime and order intent infrastructure
-- =============================================================================

CREATE TABLE IF NOT EXISTS strategy_runs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1,
    strategy_id INTEGER NOT NULL,
    source_version_id VARCHAR(64) NOT NULL DEFAULT '',
    code_hash VARCHAR(128) NOT NULL DEFAULT '',
    parameter_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    account_id VARCHAR(64) NOT NULL DEFAULT '',
    exchange_id VARCHAR(50) NOT NULL DEFAULT '',
    credential_id INTEGER NOT NULL DEFAULT 0,
    symbol VARCHAR(80) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    position_mode VARCHAR(20) NOT NULL DEFAULT '',
    runtime_status VARCHAR(32) NOT NULL DEFAULT 'running',
    runtime_epoch BIGINT NOT NULL DEFAULT 1,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    stopped_at TIMESTAMP,
    stop_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_strategy ON strategy_runs(strategy_id, runtime_status);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_started ON strategy_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS strategy_runtime_state (
    id SERIAL PRIMARY KEY,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    strategy_id INTEGER NOT NULL,
    state_key VARCHAR(128) NOT NULL,
    state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_run_id, strategy_id, state_key)
);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_state_strategy ON strategy_runtime_state(strategy_id);

CREATE TABLE IF NOT EXISTS qd_strategy_event_subscriptions (
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    event_type VARCHAR(80) NOT NULL,
    partition_key VARCHAR(320) NOT NULL,
    timeframe VARCHAR(20) NOT NULL DEFAULT '',
    strategy_shard INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (strategy_id, event_type, partition_key)
);
CREATE INDEX IF NOT EXISTS idx_strategy_event_subscription_lookup
ON qd_strategy_event_subscriptions(event_type, partition_key, strategy_shard, strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_event_subscription_shard
ON qd_strategy_event_subscriptions(strategy_shard, strategy_id);

CREATE TABLE IF NOT EXISTS strategy_order_intents (
    id SERIAL PRIMARY KEY,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    strategy_id INTEGER NOT NULL,
    idempotency_key VARCHAR(180) NOT NULL,
    symbol VARCHAR(80) NOT NULL,
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    side VARCHAR(10) NOT NULL,
    position_side VARCHAR(10) NOT NULL DEFAULT '',
    reduce_only BOOLEAN NOT NULL DEFAULT FALSE,
    order_type VARCHAR(24) NOT NULL DEFAULT 'market',
    quantity DECIMAL(28, 12) NOT NULL DEFAULT 0,
    notional DECIMAL(28, 12) NOT NULL DEFAULT 0,
    limit_price DECIMAL(28, 12) NOT NULL DEFAULT 0,
    execution_algo VARCHAR(32) NOT NULL DEFAULT 'market',
    portfolio_id VARCHAR(96) NOT NULL DEFAULT '',
    universe_id VARCHAR(96) NOT NULL DEFAULT '',
    rebalance_group_id VARCHAR(128) NOT NULL DEFAULT '',
    target_weight DECIMAL(18, 10),
    target_notional DECIMAL(28, 12),
    target_position_qty DECIMAL(28, 12),
    status VARCHAR(32) NOT NULL DEFAULT 'intent_created',
    client_order_id VARCHAR(100) NOT NULL DEFAULT '',
    runtime_fencing_token BIGINT NOT NULL DEFAULT 0,
    exchange_order_id VARCHAR(100) NOT NULL DEFAULT '',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_run_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_strategy_order_intents_strategy ON strategy_order_intents(strategy_id, status);
ALTER TABLE strategy_order_intents
    ADD COLUMN IF NOT EXISTS runtime_fencing_token BIGINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS strategy_order_fills (
    id SERIAL PRIMARY KEY,
    order_intent_id INTEGER NOT NULL DEFAULT 0,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    strategy_id INTEGER NOT NULL DEFAULT 0,
    exchange_id VARCHAR(50) NOT NULL DEFAULT '',
    exchange_order_id VARCHAR(100) NOT NULL DEFAULT '',
    exchange_fill_id VARCHAR(128) NOT NULL DEFAULT '',
    side VARCHAR(10) NOT NULL DEFAULT '',
    position_side VARCHAR(10) NOT NULL DEFAULT '',
    price DECIMAL(28, 12) NOT NULL DEFAULT 0,
    quantity DECIMAL(28, 12) NOT NULL DEFAULT 0,
    notional DECIMAL(28, 12) NOT NULL DEFAULT 0,
    fee DECIMAL(28, 12) NOT NULL DEFAULT 0,
    fee_ccy VARCHAR(20) NOT NULL DEFAULT '',
    filled_at TIMESTAMP NOT NULL DEFAULT NOW(),
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_strategy_order_fills_intent ON strategy_order_fills(order_intent_id);
CREATE INDEX IF NOT EXISTS idx_strategy_order_fills_strategy ON strategy_order_fills(strategy_id, filled_at DESC);

-- =============================================================================
-- Unified live execution stream ledger
-- =============================================================================

ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS client_order_id VARCHAR(100) NOT NULL DEFAULT '';
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS fee_status VARCHAR(24) NOT NULL DEFAULT 'pending';
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS fee_source VARCHAR(24) NOT NULL DEFAULT '';

ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS execution_event_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS exchange_fill_id VARCHAR(160) NOT NULL DEFAULT '';
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS fee_status VARCHAR(24) NOT NULL DEFAULT 'pending';
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS fee_source VARCHAR(24) NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_trades_execution_event
  ON qd_strategy_trades(execution_event_id) WHERE execution_event_id > 0;

ALTER TABLE strategy_order_fills ADD COLUMN IF NOT EXISTS credential_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE strategy_order_fills ADD COLUMN IF NOT EXISTS commission_quote DECIMAL(28, 12);
ALTER TABLE strategy_order_fills ADD COLUMN IF NOT EXISTS fee_status VARCHAR(24) NOT NULL DEFAULT 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_order_fills_exchange_fill
  ON strategy_order_fills(exchange_id, credential_id, exchange_fill_id)
  WHERE exchange_fill_id <> '';

CREATE TABLE IF NOT EXISTS qd_live_order_bindings (
    id BIGSERIAL PRIMARY KEY,
    credential_id INTEGER NOT NULL DEFAULT 0,
    exchange_id VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    owner_type VARCHAR(24) NOT NULL,
    owner_id BIGINT NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 1,
    strategy_id INTEGER NOT NULL DEFAULT 0,
    pending_order_id BIGINT NOT NULL DEFAULT 0,
    strategy_run_id BIGINT NOT NULL DEFAULT 0,
    order_intent_id BIGINT NOT NULL DEFAULT 0,
    symbol VARCHAR(80) NOT NULL DEFAULT '',
    signal_type VARCHAR(40) NOT NULL DEFAULT '',
    client_order_id VARCHAR(100) NOT NULL DEFAULT '',
    exchange_order_id VARCHAR(160) NOT NULL DEFAULT '',
    observed_filled DECIMAL(28, 12) NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL DEFAULT 'open',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_live_order_binding_owner
  ON qd_live_order_bindings(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_live_order_binding_client
  ON qd_live_order_bindings(credential_id, exchange_id, market_type, client_order_id);
CREATE INDEX IF NOT EXISTS idx_live_order_binding_exchange
  ON qd_live_order_bindings(credential_id, exchange_id, market_type, exchange_order_id);

CREATE TABLE IF NOT EXISTS qd_execution_events (
    id BIGSERIAL PRIMARY KEY,
    event_key VARCHAR(320) NOT NULL UNIQUE,
    credential_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 1,
    exchange_id VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    account_id VARCHAR(128) NOT NULL DEFAULT '',
    symbol VARCHAR(80) NOT NULL DEFAULT '',
    exchange_order_id VARCHAR(160) NOT NULL DEFAULT '',
    client_order_id VARCHAR(100) NOT NULL DEFAULT '',
    exchange_fill_id VARCHAR(160) NOT NULL DEFAULT '',
    side VARCHAR(12) NOT NULL DEFAULT '',
    position_side VARCHAR(12) NOT NULL DEFAULT '',
    order_status VARCHAR(24) NOT NULL DEFAULT '',
    price DECIMAL(28, 12) NOT NULL DEFAULT 0,
    quantity DECIMAL(28, 12) NOT NULL DEFAULT 0,
    cumulative_quantity DECIMAL(28, 12) NOT NULL DEFAULT 0,
    is_cumulative BOOLEAN NOT NULL DEFAULT FALSE,
    realized_pnl DECIMAL(28, 12),
    maker BOOLEAN,
    fee_status VARCHAR(24) NOT NULL DEFAULT 'pending',
    occurred_at TIMESTAMP NOT NULL,
    received_at TIMESTAMP NOT NULL DEFAULT NOW(),
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    processed_at TIMESTAMP,
    process_attempts INTEGER NOT NULL DEFAULT 0,
    process_error TEXT NOT NULL DEFAULT '',
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE qd_execution_events
  ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_execution_events_pending
  ON qd_execution_events(received_at, id) WHERE processed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_execution_events_order
  ON qd_execution_events(credential_id, exchange_id, market_type, exchange_order_id);

CREATE TABLE IF NOT EXISTS qd_grid_actor_state (
    strategy_id INTEGER PRIMARY KEY REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    strategy_run_id BIGINT NOT NULL DEFAULT 0,
    owner_id VARCHAR(160) NOT NULL DEFAULT '',
    fencing_token BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL DEFAULT 'starting',
    state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_execution_event_id BIGINT NOT NULL DEFAULT 0,
    heartbeat_at TIMESTAMP NOT NULL DEFAULT NOW(),
    version BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (status IN ('starting', 'running', 'handoff', 'stopped', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_grid_actor_state_owner
  ON qd_grid_actor_state(owner_id, heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS qd_grid_actor_events (
    id BIGSERIAL PRIMARY KEY,
    execution_event_id BIGINT NOT NULL UNIQUE
      REFERENCES qd_execution_events(id) ON DELETE CASCADE,
    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
    grid_order_id BIGINT NOT NULL REFERENCES qd_grid_resting_orders(id) ON DELETE CASCADE,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_by VARCHAR(160) NOT NULL DEFAULT '',
    fencing_token BIGINT NOT NULL DEFAULT 0,
    available_at TIMESTAMP NOT NULL DEFAULT NOW(),
    lease_expires_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (status IN ('pending', 'processing', 'failed', 'completed', 'dead'))
);
CREATE INDEX IF NOT EXISTS idx_grid_actor_events_ready
  ON qd_grid_actor_events(strategy_id, available_at, id)
  WHERE status IN ('pending', 'failed', 'processing');

CREATE TABLE IF NOT EXISTS qd_execution_fee_components (
    id BIGSERIAL PRIMARY KEY,
    execution_event_id BIGINT NOT NULL REFERENCES qd_execution_events(id) ON DELETE CASCADE,
    fee_type VARCHAR(24) NOT NULL DEFAULT 'trade',
    currency VARCHAR(24) NOT NULL DEFAULT '',
    amount DECIMAL(28, 12) NOT NULL DEFAULT 0,
    quote_amount DECIMAL(28, 12),
    source VARCHAR(24) NOT NULL DEFAULT 'websocket',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(execution_event_id, fee_type, currency)
);
CREATE INDEX IF NOT EXISTS idx_execution_fee_event
  ON qd_execution_fee_components(execution_event_id);

CREATE TABLE IF NOT EXISTS qd_execution_fee_projections (
    execution_event_id BIGINT PRIMARY KEY REFERENCES qd_execution_events(id) ON DELETE CASCADE,
    pending_order_id BIGINT NOT NULL DEFAULT 0,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS qd_execution_owner_projections (
    execution_event_id BIGINT NOT NULL REFERENCES qd_execution_events(id) ON DELETE CASCADE,
    owner_type VARCHAR(24) NOT NULL,
    owner_id BIGINT NOT NULL DEFAULT 0,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (execution_event_id, owner_type, owner_id)
);

CREATE TABLE IF NOT EXISTS qd_execution_stream_health (
    stream_key VARCHAR(180) PRIMARY KEY,
    credential_id INTEGER NOT NULL DEFAULT 0,
    exchange_id VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL DEFAULT '',
    state VARCHAR(24) NOT NULL DEFAULT 'stopped',
    last_event_at TIMESTAMP,
    last_connected_at TIMESTAMP,
    last_disconnected_at TIMESTAMP,
    reconnect_count INTEGER NOT NULL DEFAULT 0,
    rest_fallback BOOLEAN NOT NULL DEFAULT FALSE,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_runtime_events (
    id SERIAL PRIMARY KEY,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    strategy_id INTEGER NOT NULL DEFAULT 0,
    event_type VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL DEFAULT 'info',
    message TEXT NOT NULL DEFAULT '',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_events_run ON strategy_runtime_events(strategy_run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS strategy_runtime_locks (
    lock_key VARCHAR(180) PRIMARY KEY,
    strategy_run_id INTEGER NOT NULL DEFAULT 0,
    runtime_epoch BIGINT NOT NULL DEFAULT 1,
    owner VARCHAR(100) NOT NULL DEFAULT '',
    expires_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
-- =============================================================================
-- Durable process roles, strategy commands, runtime leases, and worker health
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_strategy_commands (
    id BIGSERIAL PRIMARY KEY,
    strategy_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    command_type VARCHAR(24) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMP NOT NULL DEFAULT NOW(),
    claimed_by VARCHAR(160) NOT NULL DEFAULT '',
    claimed_at TIMESTAMP,
    lease_expires_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (command_type IN ('start', 'stop', 'restart', 'reconcile')),
    CHECK (status IN ('pending', 'processing', 'succeeded', 'failed', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_strategy_commands_claim
    ON qd_strategy_commands(status, available_at, id);
CREATE INDEX IF NOT EXISTS idx_strategy_commands_strategy
    ON qd_strategy_commands(strategy_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_commands_active_action
    ON qd_strategy_commands(strategy_id, command_type)
    WHERE status IN ('pending', 'processing');

CREATE TABLE IF NOT EXISTS qd_strategy_runtime_leases (
    strategy_id INTEGER PRIMARY KEY,
    owner_id VARCHAR(160) NOT NULL,
    fencing_token BIGINT NOT NULL DEFAULT 1,
    lease_expires_at TIMESTAMP NOT NULL,
    heartbeat_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_leases_expiry
    ON qd_strategy_runtime_leases(lease_expires_at);

CREATE TABLE IF NOT EXISTS qd_worker_heartbeats (
    worker_id VARCHAR(160) PRIMARY KEY,
    role VARCHAR(32) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'running',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    heartbeat_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (role IN ('api', 'trading', 'scheduler', 'celery', 'celery-beat', 'kafka-audit', 'strategy-dispatcher', 'strategy-evaluator')),
    CHECK (status IN ('running', 'stopped', 'failed'))
);
ALTER TABLE qd_worker_heartbeats
    DROP CONSTRAINT IF EXISTS qd_worker_heartbeats_role_check;
ALTER TABLE qd_worker_heartbeats
    ADD CONSTRAINT qd_worker_heartbeats_role_check
    CHECK (role IN (
        'api', 'trading', 'scheduler', 'celery', 'celery-beat',
        'kafka-audit', 'strategy-dispatcher', 'strategy-evaluator'
    ));
CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_role
    ON qd_worker_heartbeats(role, heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS qd_event_inbox (
    consumer_group VARCHAR(160) NOT NULL,
    event_id VARCHAR(80) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    partition_key VARCHAR(320) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'processing',
    attempts INTEGER NOT NULL DEFAULT 1,
    claimed_by VARCHAR(160) NOT NULL DEFAULT '',
    lease_expires_at TIMESTAMP,
    error_message TEXT NOT NULL DEFAULT '',
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (consumer_group, event_id),
    CHECK (status IN ('processing', 'completed', 'failed', 'dead'))
);
CREATE INDEX IF NOT EXISTS idx_event_inbox_claim
    ON qd_event_inbox(consumer_group, status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_event_inbox_completed
    ON qd_event_inbox(completed_at)
    WHERE status = 'completed';

CREATE TABLE IF NOT EXISTS qd_strategy_shard_leases (
    strategy_shard INTEGER PRIMARY KEY,
    owner_id VARCHAR(160) NOT NULL,
    fencing_token BIGINT NOT NULL DEFAULT 1,
    lease_expires_at TIMESTAMP NOT NULL,
    heartbeat_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_strategy_shard_leases_expiry
    ON qd_strategy_shard_leases(lease_expires_at);

CREATE TABLE IF NOT EXISTS qd_process_leases (
    lease_key VARCHAR(128) PRIMARY KEY,
    owner_id VARCHAR(160) NOT NULL,
    lease_expires_at TIMESTAMP NOT NULL,
    heartbeat_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_process_leases_expiry
    ON qd_process_leases(lease_expires_at);

-- Settings page values shared by every process (see app/services/settings/store.py).
CREATE SEQUENCE IF NOT EXISTS qd_runtime_settings_version_seq;

CREATE TABLE IF NOT EXISTS qd_runtime_settings (
    setting_key VARCHAR(128) PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    is_secret BOOLEAN NOT NULL DEFAULT FALSE,
    version BIGINT NOT NULL,
    updated_by INTEGER,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS qd_account_positions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    credential_id INTEGER NOT NULL DEFAULT 0,
    exchange_id VARCHAR(40) NOT NULL DEFAULT '',
    market_type VARCHAR(20) NOT NULL DEFAULT 'swap',
    inst_id VARCHAR(80) NOT NULL DEFAULT '',
    symbol VARCHAR(50) NOT NULL DEFAULT '',
    side VARCHAR(10) NOT NULL DEFAULT '',
    size DECIMAL(24, 8) NOT NULL DEFAULT 0,
    entry_price DECIMAL(24, 8) DEFAULT 0,
    mark_price DECIMAL(24, 8) DEFAULT 0,
    unrealized_pnl DECIMAL(24, 8) DEFAULT 0,
    raw_json JSONB DEFAULT '{}'::jsonb,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (credential_id, market_type, inst_id, side)
);
CREATE INDEX IF NOT EXISTS idx_account_pos_user ON qd_account_positions(user_id);
CREATE INDEX IF NOT EXISTS idx_account_pos_cred ON qd_account_positions(credential_id, market_type);

-- =============================================================================
-- 21. Indicator Community Tables
-- =============================================================================


CREATE TABLE IF NOT EXISTS qd_indicator_purchases (
    id SERIAL PRIMARY KEY,
    indicator_id INTEGER NOT NULL REFERENCES qd_indicator_codes(id) ON DELETE CASCADE,
    buyer_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    seller_id INTEGER NOT NULL REFERENCES qd_users(id),
    price DECIMAL(10,2) NOT NULL DEFAULT 0,
    gross_price DECIMAL(10,2),
    platform_fee DECIMAL(10,2) DEFAULT 0,
    seller_amount DECIMAL(10,2),
    fee_rate DECIMAL(10,6) DEFAULT 0,
    asset_name_snapshot VARCHAR(255),
    asset_description_snapshot TEXT,
    asset_code_snapshot TEXT,
    asset_type_snapshot VARCHAR(32),
    asset_preview_image_snapshot VARCHAR(500),
    asset_is_encrypted_snapshot INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(indicator_id, buyer_id)
);

CREATE INDEX IF NOT EXISTS idx_purchases_indicator ON qd_indicator_purchases(indicator_id);
CREATE INDEX IF NOT EXISTS idx_purchases_buyer ON qd_indicator_purchases(buyer_id);
CREATE INDEX IF NOT EXISTS idx_purchases_seller ON qd_indicator_purchases(seller_id);


CREATE TABLE IF NOT EXISTS qd_indicator_comments (
    id SERIAL PRIMARY KEY,
    indicator_id INTEGER NOT NULL REFERENCES qd_indicator_codes(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    rating INTEGER DEFAULT 5 CHECK (rating >= 1 AND rating <= 5),
    content TEXT DEFAULT '',
    parent_id INTEGER REFERENCES qd_indicator_comments(id) ON DELETE CASCADE,
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_comments_indicator ON qd_indicator_comments(indicator_id);
CREATE INDEX IF NOT EXISTS idx_comments_user ON qd_indicator_comments(user_id);

-- =============================================================================
-- Quick Trades (manual / discretionary orders from Quick Trade Panel)
-- =============================================================================
CREATE TABLE IF NOT EXISTS qd_quick_trades (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    credential_id   INTEGER DEFAULT 0,
    exchange_id     VARCHAR(40) NOT NULL DEFAULT '',
    symbol          VARCHAR(60) NOT NULL DEFAULT '',
    side            VARCHAR(10) NOT NULL DEFAULT '',       -- buy / sell
    order_type      VARCHAR(20) NOT NULL DEFAULT 'market', -- market / limit
    amount          DECIMAL(24, 8) DEFAULT 0,
    price           DECIMAL(24, 8) DEFAULT 0,
    leverage        INTEGER DEFAULT 1,
    market_type     VARCHAR(20) DEFAULT 'swap',            -- swap / spot
    tp_price        DECIMAL(24, 8) DEFAULT 0,
    sl_price        DECIMAL(24, 8) DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'submitted',       -- submitted / filled / failed / cancelled
    exchange_order_id VARCHAR(120) DEFAULT '',
    filled_amount   DECIMAL(24, 8) DEFAULT 0,
    avg_fill_price  DECIMAL(24, 8) DEFAULT 0,
    commission      DECIMAL(24, 8) DEFAULT 0,              -- realised trading fee for this fill (best-effort)
    commission_ccy  VARCHAR(16) DEFAULT '',                -- e.g. 'USDT' / 'BNB'; empty when unknown
    commission_quote DECIMAL(24, 8),
    error_msg       TEXT DEFAULT '',
    source          VARCHAR(40) DEFAULT 'manual',          -- ai_radar / ai_analysis / indicator / manual
    raw_result      JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quick_trades_user    ON qd_quick_trades(user_id);
CREATE INDEX IF NOT EXISTS idx_quick_trades_created ON qd_quick_trades(created_at DESC);

CREATE TABLE IF NOT EXISTS qd_ai_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_uid VARCHAR(64) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL DEFAULT 0,
    source_type VARCHAR(24) NOT NULL,
    source_id BIGINT NOT NULL DEFAULT 0,
    strategy_run_id BIGINT NOT NULL DEFAULT 0,
    order_intent_id BIGINT NOT NULL DEFAULT 0,
    symbol VARCHAR(80) NOT NULL DEFAULT '',
    action VARCHAR(40) NOT NULL DEFAULT '',
    market_type VARCHAR(24) NOT NULL DEFAULT '',
    provider VARCHAR(24) NOT NULL DEFAULT 'none',
    model VARCHAR(120) NOT NULL DEFAULT '',
    decision VARCHAR(24) NOT NULL,
    allowed BOOLEAN NOT NULL DEFAULT TRUE,
    confidence DECIMAL(8, 6),
    reason TEXT NOT NULL DEFAULT '',
    fallback_reason TEXT NOT NULL DEFAULT '',
    probabilities_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    checks_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    request_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    billing_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE qd_ai_decisions
    ADD COLUMN IF NOT EXISTS billing_json JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_ai_decisions_strategy
    ON qd_ai_decisions(source_type, source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_user
    ON qd_ai_decisions(user_id, created_at DESC);

-- Migration: Add commission tracking columns to existing qd_quick_trades.
-- (Introduced in v3.0.8. Pre-existing rows default to 0 / '' which is the

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_quick_trades' AND column_name = 'commission'
    ) THEN
        ALTER TABLE qd_quick_trades ADD COLUMN commission DECIMAL(24, 8) DEFAULT 0;
        ALTER TABLE qd_quick_trades ADD COLUMN commission_ccy VARCHAR(16) DEFAULT '';
        RAISE NOTICE 'Added commission / commission_ccy columns to qd_quick_trades';
    END IF;
END $$;

ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS commission_quote DECIMAL(24,8);
ALTER TABLE qd_quick_trades ADD COLUMN IF NOT EXISTS commission DECIMAL(24,8) DEFAULT 0;
ALTER TABLE qd_quick_trades ADD COLUMN IF NOT EXISTS commission_ccy VARCHAR(16) DEFAULT '';
ALTER TABLE qd_quick_trades ADD COLUMN IF NOT EXISTS commission_quote DECIMAL(24,8);
ALTER TABLE qd_quick_trades ADD COLUMN IF NOT EXISTS client_order_id VARCHAR(100) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_quick_trades_client_order
    ON qd_quick_trades(credential_id, exchange_id, market_type, client_order_id)
    WHERE client_order_id <> '';
UPDATE qd_strategy_trades
SET commission_quote = commission
WHERE commission_quote IS NULL
  AND UPPER(COALESCE(commission_ccy, '')) IN ('USD', 'USDT', 'USDC', 'BUSD', 'FDUSD', 'TUSD');
UPDATE qd_quick_trades
SET commission_quote = commission
WHERE commission_quote IS NULL
  AND UPPER(COALESCE(commission_ccy, '')) IN ('USD', 'USDT', 'USDC', 'BUSD', 'FDUSD', 'TUSD');

-- =============================================================================
-- Polymarket (宸茬Щ闄?/ removed in v3.0.7)
-- =============================================================================


DROP TABLE IF EXISTS qd_polymarket_asset_opportunities CASCADE;
DROP TABLE IF EXISTS qd_polymarket_ai_analysis CASCADE;
DROP TABLE IF EXISTS qd_polymarket_markets CASCADE;

-- =============================================================================

-- =============================================================================
-- These tables back the Agent Gateway runtime (see docs/agent/agent-openapi.json).
-- They are tenant-scoped via user_id and stay isolated from human JWT sessions.

CREATE TABLE IF NOT EXISTS qd_agent_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    name VARCHAR(80) NOT NULL,
    token_prefix VARCHAR(24) NOT NULL,           -- e.g. "qd_agent_AbCdEf12" (shown to humans/audit only)
    token_hash VARCHAR(128) NOT NULL,            -- sha256(token) hex
    scopes TEXT NOT NULL DEFAULT 'R',            -- comma-separated subset of R,W,B,N,C,T
    markets TEXT NOT NULL DEFAULT '*',           -- comma-separated allowlist or '*'
    instruments TEXT NOT NULL DEFAULT '*',       -- comma-separated allowlist or '*'
    paper_only BOOLEAN NOT NULL DEFAULT TRUE,    -- T-class always starts paper-only
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    max_order_notional DECIMAL(24,8) NOT NULL DEFAULT 1000,
    max_daily_notional DECIMAL(24,8) NOT NULL DEFAULT 5000,
    status VARCHAR(20) NOT NULL DEFAULT 'active',-- active/revoked/expired
    expires_at TIMESTAMP,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_hash ON qd_agent_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_agent_tokens_user ON qd_agent_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_tokens_status ON qd_agent_tokens(status);
ALTER TABLE qd_agent_tokens
  ADD COLUMN IF NOT EXISTS max_order_notional DECIMAL(24,8) NOT NULL DEFAULT 1000;
ALTER TABLE qd_agent_tokens
  ADD COLUMN IF NOT EXISTS max_daily_notional DECIMAL(24,8) NOT NULL DEFAULT 5000;

CREATE TABLE IF NOT EXISTS qd_agent_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id VARCHAR(40) NOT NULL UNIQUE,          -- public id (uuid4 hex)
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    agent_token_id INTEGER REFERENCES qd_agent_tokens(id) ON DELETE SET NULL,
    kind VARCHAR(40) NOT NULL,                   -- backtest
    status VARCHAR(20) NOT NULL DEFAULT 'queued',-- queued/running/succeeded/failed/cancelled
    request JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    error TEXT,
    progress JSONB,
    idempotency_key VARCHAR(120),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_user ON qd_agent_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_status ON qd_agent_jobs(status);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_kind ON qd_agent_jobs(kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_jobs_idem
    ON qd_agent_jobs(agent_token_id, kind, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS qd_agent_audit (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    agent_token_id INTEGER,
    agent_name VARCHAR(80),
    route VARCHAR(160) NOT NULL,
    method VARCHAR(8) NOT NULL,
    scope_class VARCHAR(4) NOT NULL,             -- R / W / B / N / C / T
    status_code INTEGER NOT NULL,
    idempotency_key VARCHAR(120),
    request_summary JSONB,                       -- redacted (no secrets)
    response_summary JSONB,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_audit_user ON qd_agent_audit(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_audit_token ON qd_agent_audit(agent_token_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_audit_class ON qd_agent_audit(scope_class);

CREATE TABLE IF NOT EXISTS qd_agent_idempotency (
    id BIGSERIAL PRIMARY KEY,
    agent_token_id INTEGER NOT NULL REFERENCES qd_agent_tokens(id) ON DELETE CASCADE,
    method VARCHAR(8) NOT NULL,
    route VARCHAR(200) NOT NULL,
    idempotency_key VARCHAR(120) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'started',
    response_body JSONB,
    response_status INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(agent_token_id, method, route, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_idempotency_created
  ON qd_agent_idempotency(created_at);

CREATE TABLE IF NOT EXISTS qd_agent_notional_reservations (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    agent_token_id INTEGER NOT NULL REFERENCES qd_agent_tokens(id) ON DELETE CASCADE,
    idempotency_key VARCHAR(120) NOT NULL,
    notional DECIMAL(24,8) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'reserved',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(agent_token_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_notional_daily
  ON qd_agent_notional_reservations(agent_token_id, created_at);

-- Paper-only ledger so trading-class tokens can simulate without ever touching
-- live exchange credentials.  Real-money execution stays gated by paper_only=false
-- AND the existing TradingExecutor code path.
CREATE TABLE IF NOT EXISTS qd_agent_paper_orders (
    id BIGSERIAL PRIMARY KEY,
    order_uid VARCHAR(40) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    agent_token_id INTEGER REFERENCES qd_agent_tokens(id) ON DELETE SET NULL,
    market VARCHAR(40) NOT NULL,
    symbol VARCHAR(60) NOT NULL,
    side VARCHAR(8) NOT NULL,                    -- buy / sell
    order_type VARCHAR(16) NOT NULL DEFAULT 'market',
    qty DECIMAL(28,10) NOT NULL,
    limit_price DECIMAL(28,10),
    fill_price DECIMAL(28,10),
    fill_value DECIMAL(28,10),
    status VARCHAR(16) NOT NULL DEFAULT 'filled',-- filled / cancelled / rejected
    note TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_paper_orders_user ON qd_agent_paper_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_paper_orders_token ON qd_agent_paper_orders(agent_token_id);

-- Jobs created before progress JSONB existed (Agent Gateway v3.1)
ALTER TABLE qd_agent_jobs ADD COLUMN IF NOT EXISTS progress JSONB;

-- Strategy API V2 templates are seeded by strategy_v2_templates.sql.

-- =============================================================================
-- Completion Notice
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE 'QuantDinger PostgreSQL schema initialized successfully!';
END $$;

CREATE TABLE IF NOT EXISTS qd_fundamental_sync_jobs (
    id BIGSERIAL PRIMARY KEY,
    universe_id BIGINT NOT NULL REFERENCES qd_universes(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    mode VARCHAR(20) NOT NULL CHECK (mode IN ('history', 'current')),
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    fields_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fundamental_sync_active
    ON qd_fundamental_sync_jobs(universe_id) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS qd_fundamental_sync_items (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES qd_fundamental_sync_jobs(id) ON DELETE CASCADE,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(80) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    token VARCHAR(40),
    lease_until TIMESTAMPTZ,
    retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error VARCHAR(200) NOT NULL DEFAULT '',
    error_detail VARCHAR(500) NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(job_id, market, symbol)
);
CREATE INDEX IF NOT EXISTS idx_fundamental_sync_pending ON qd_fundamental_sync_items(status, retry_at);
CREATE TABLE IF NOT EXISTS qd_fundamental_sync_schedules (
    id BIGSERIAL PRIMARY KEY,
    universe_id BIGINT NOT NULL UNIQUE REFERENCES qd_universes(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    mode VARCHAR(20) NOT NULL DEFAULT 'history',
    fields_json JSONB NOT NULL DEFAULT '["revenue", "net_income", "net_income_ttm", "book_value", "shareholder_equity", "total_debt", "free_cash_flow", "shares_outstanding", "market_cap", "pe_ratio", "pb_ratio", "return_on_equity", "revenue_growth", "debt_to_equity"]'::jsonb,
    next_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE qd_fundamental_sync_jobs ADD COLUMN IF NOT EXISTS refresh_policy VARCHAR(20) NOT NULL DEFAULT 'full';
ALTER TABLE qd_fundamental_sync_jobs ADD COLUMN IF NOT EXISTS skipped_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE qd_fundamental_sync_items ADD COLUMN IF NOT EXISTS error_detail VARCHAR(500) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_fundamental_sync_symbol_check ON qd_fundamental_sync_items(market, symbol, updated_at DESC);

CREATE TABLE IF NOT EXISTS qd_exchange_order_pnl (
    credential_id INTEGER NOT NULL,
    exchange_id VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    symbol VARCHAR(80) NOT NULL,
    exchange_order_id VARCHAR(160) NOT NULL,
    expected_quantity NUMERIC(36,18) NOT NULL DEFAULT 0,
    report JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (credential_id, exchange_id, market_type, symbol, exchange_order_id)
);
CREATE INDEX IF NOT EXISTS idx_exchange_pnl_checked ON qd_exchange_order_pnl(credential_id, checked_at);

-- Keep strategy deletion atomic even when a caller bypasses the application service.
CREATE OR REPLACE FUNCTION qd_cleanup_deleted_strategy()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE qd_execution_events AS event
    SET processed_at = COALESCE(event.processed_at, NOW()),
        process_error = 'strategy_deleted',
        next_attempt_at = NOW()
    WHERE event.processed_at IS NULL
      AND EXISTS (
        SELECT 1
        FROM qd_live_order_bindings AS binding
        WHERE binding.strategy_id = OLD.id
          AND binding.credential_id = event.credential_id
          AND LOWER(binding.exchange_id) = LOWER(event.exchange_id)
          AND (
            (event.exchange_order_id <> '' AND binding.exchange_order_id = event.exchange_order_id)
            OR (event.client_order_id <> '' AND binding.client_order_id = event.client_order_id)
          )
      );

    DELETE FROM pending_orders
    WHERE strategy_id = OLD.id
       OR (NULLIF(payload_json, '')::jsonb ->> 'strategy_id') = OLD.id::text;
    DELETE FROM qd_live_order_bindings WHERE strategy_id = OLD.id;
    DELETE FROM strategy_runtime_locks
    WHERE strategy_run_id IN (
        SELECT id FROM strategy_runs WHERE strategy_id = OLD.id
    );
    DELETE FROM strategy_order_fills WHERE strategy_id = OLD.id;
    DELETE FROM strategy_order_intents WHERE strategy_id = OLD.id;
    DELETE FROM strategy_runtime_state WHERE strategy_id = OLD.id;
    DELETE FROM strategy_runtime_events WHERE strategy_id = OLD.id;
    DELETE FROM strategy_runs WHERE strategy_id = OLD.id;
    DELETE FROM qd_strategy_commands WHERE strategy_id = OLD.id;
    DELETE FROM qd_strategy_runtime_leases WHERE strategy_id = OLD.id;

    UPDATE qd_backtest_runs SET strategy_id = NULL WHERE strategy_id = OLD.id;
    UPDATE qd_backtest_trades SET strategy_id = NULL WHERE strategy_id = OLD.id;
    UPDATE qd_indicator_codes SET source_strategy_id = NULL WHERE source_strategy_id = OLD.id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cleanup_deleted_strategy ON qd_strategies_trading;
CREATE TRIGGER trg_cleanup_deleted_strategy
BEFORE DELETE ON qd_strategies_trading
FOR EACH ROW EXECUTE FUNCTION qd_cleanup_deleted_strategy();
