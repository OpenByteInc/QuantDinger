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
    credits DECIMAL(20,2) DEFAULT 0,       -- 积分余额
    vip_expires_at TIMESTAMP,              -- VIP过期时间
    vip_plan VARCHAR(20) DEFAULT '',       -- VIP套餐：monthly/yearly/lifetime
    vip_is_lifetime BOOLEAN DEFAULT FALSE, -- 是否永久会员
    vip_monthly_credits_last_grant TIMESTAMP, -- 永久会员上次发放月度积分时间
    email_verified BOOLEAN DEFAULT FALSE,  -- 邮箱是否已验证
    referred_by INTEGER,                   -- 邀请人ID
    notification_settings TEXT DEFAULT '', -- 用户通知配置 JSON (telegram_chat_id, default_channels等)
    chart_templates TEXT DEFAULT '',      -- 用户图表模板 JSON（指标布局/样式）
    timezone VARCHAR(64) DEFAULT '',       -- IANA 时区标识，空表示跟随客户端/浏览器
    token_version INTEGER DEFAULT 1,       -- Token版本号，用于单一客户端登录控制
    password_changed_at TIMESTAMP,           -- NULL only prompts when bootstrap password is still 123456
    last_login_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_referred_by ON qd_users(referred_by);

-- Note: Admin user is created automatically by the application on startup
-- using ADMIN_USER and ADMIN_PASSWORD from environment variables

-- =============================================================================
-- 1.5. Credits Log (积分变动日志)
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_credits_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,            -- recharge/consume/refund/admin_adjust/vip_grant
    amount DECIMAL(20,2) NOT NULL,          -- 变动金额（正数增加，负数减少）
    balance_after DECIMAL(20,2) NOT NULL,   -- 变动后余额
    feature VARCHAR(50) DEFAULT '',          -- 消费的功能：ai_analysis/strategy_run/backtest 等
    reference_id VARCHAR(100) DEFAULT '',    -- 关联ID（如订单号、分析任务ID等）
    remark TEXT DEFAULT '',                  -- 备注
    operator_id INTEGER,                     -- 操作人ID（管理员调整时记录）
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credits_log_user_id ON qd_credits_log(user_id);
CREATE INDEX IF NOT EXISTS idx_credits_log_action ON qd_credits_log(action);
CREATE INDEX IF NOT EXISTS idx_credits_log_created_at ON qd_credits_log(created_at);

-- =============================================================================
-- 1.55. Membership Orders (会员订单 - Mock支付)
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_membership_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    plan VARCHAR(20) NOT NULL,             -- monthly/yearly/lifetime
    price_usd DECIMAL(10,2) DEFAULT 0,     -- 订单金额（USD）
    status VARCHAR(20) DEFAULT 'paid',     -- paid/pending/failed/refunded (mock 默认 paid)
    created_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_membership_orders_user_id ON qd_membership_orders(user_id);

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
    plan VARCHAR(20) NOT NULL,                                  -- monthly/yearly/lifetime
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_usdt_orders_amount_active
  ON qd_usdt_orders(chain, amount_usdt)
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
END
$$;

-- =============================================================================
-- 1.59. OAuth CSRF State (多 worker / 多实例共享，避免 Invalid state)
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
-- 1.6. Verification Codes (邮箱验证码)
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
-- 1.7. Login Attempts (登录尝试记录 - 防爆破)
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
-- 1.8. OAuth Links (第三方账号关联)
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
-- 1.9. Security Audit Log (安全审计日志)
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
    strategy_type VARCHAR(50) DEFAULT 'IndicatorStrategy',
    market_category VARCHAR(50) DEFAULT 'Crypto',
    execution_mode VARCHAR(20) DEFAULT 'signal',
    notification_config TEXT DEFAULT '',
    status VARCHAR(20) DEFAULT 'stopped',
    symbol VARCHAR(50),
    timeframe VARCHAR(10),
    initial_capital DECIMAL(20,8) DEFAULT 1000,
    leverage INTEGER DEFAULT 1,
    market_type VARCHAR(20) DEFAULT 'swap',
    exchange_config TEXT,
    indicator_config TEXT,
    trading_config TEXT,
    ai_model_config TEXT,
    decide_interval INTEGER DEFAULT 300,
    strategy_group_id VARCHAR(100) DEFAULT '',
    group_base_name VARCHAR(255) DEFAULT '',
    strategy_mode VARCHAR(20) DEFAULT 'signal',
    strategy_code TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategies_user_id ON qd_strategies_trading(user_id);
CREATE INDEX IF NOT EXISTS idx_strategies_status ON qd_strategies_trading(status);
CREATE INDEX IF NOT EXISTS idx_strategies_group_id ON qd_strategies_trading(strategy_group_id);

-- Script source library: reusable code assets separated from live/runtime strategy rows.
CREATE TABLE IF NOT EXISTS qd_script_sources (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    code TEXT NOT NULL DEFAULT '',
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

-- Add strategy_mode and strategy_code columns (script strategy support)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_strategies_trading' AND column_name = 'strategy_mode'
    ) THEN
        ALTER TABLE qd_strategies_trading ADD COLUMN strategy_mode VARCHAR(20) DEFAULT 'signal';
        RAISE NOTICE 'Added strategy_mode column to qd_strategies_trading';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'qd_strategies_trading' AND column_name = 'strategy_code'
    ) THEN
        ALTER TABLE qd_strategies_trading ADD COLUMN strategy_code TEXT DEFAULT '';
        RAISE NOTICE 'Added strategy_code column to qd_strategies_trading';
    END IF;
END$$;

-- Add last_rebalance_at column for cross-sectional strategies (if not exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_strategies_trading' AND column_name = 'last_rebalance_at'
    ) THEN
        ALTER TABLE qd_strategies_trading ADD COLUMN last_rebalance_at TIMESTAMP;
        RAISE NOTICE 'Added last_rebalance_at column to qd_strategies_trading';
    END IF;
END $$;

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
    profit DECIMAL(20,8) DEFAULT 0,
    close_reason VARCHAR(64) DEFAULT '',
    matched_entry_price DECIMAL(20,8) DEFAULT 0,
    grid_matched_profit DECIMAL(20,8) DEFAULT 0,
    market_type VARCHAR(20) DEFAULT 'swap',
    credential_id INTEGER DEFAULT 0,
    inst_id VARCHAR(80) DEFAULT '',
    fill_source VARCHAR(32) DEFAULT '',
    pending_order_id INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_user_id ON qd_strategy_trades(user_id);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_id ON qd_strategy_trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON qd_strategy_trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_symbol_canon ON qd_strategy_trades (strategy_id, market_type, symbol_canonical);
CREATE INDEX IF NOT EXISTS idx_positions_strategy_leg ON qd_strategy_positions (strategy_id, market_type, symbol_canonical, side);

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

CREATE INDEX IF NOT EXISTS idx_pending_orders_user_id ON pending_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_pending_orders_status ON pending_orders(status);
CREATE INDEX IF NOT EXISTS idx_pending_orders_strategy_id ON pending_orders(strategy_id);

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
   vip_free boolean DEFAULT false, -- VIP免费指标：VIP可免扣积分使用
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
    -- 对已购用户而言，本地副本通过此字段关联到市场上的原始指标，
    -- 用于后续"同步代码"功能拉取发布者的最新版本
    source_indicator_id int4 NULL,
    -- 多语言支持：用户上传的 name / description 用 source_language 标识原始语言
    -- (zh-CN / en-US / ja-JP 等)；name_i18n / description_i18n 是 LLM 翻译生成的
    -- JSONB，结构形如 {"en-US": "...", "zh-CN": "...", ...}。
    -- 市场/详情接口按 Accept-Language 命中：先查 i18n 对应键，未命中再回退到原始 name。
    -- 见 app/services/indicator_translator.py 与 community_service.py:_localize_indicator。
    source_language varchar(16) DEFAULT NULL,
    name_i18n        jsonb       DEFAULT NULL,
    description_i18n jsonb       DEFAULT NULL,
    CONSTRAINT qd_indicator_codes_pkey PRIMARY KEY (id),
   CONSTRAINT qd_indicator_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES qd_users(id) ON DELETE CASCADE

);

CREATE INDEX IF NOT EXISTS idx_indicator_codes_user_id ON qd_indicator_codes USING btree (user_id);
CREATE INDEX IF NOT EXISTS idx_indicator_review_status ON qd_indicator_codes USING btree (review_status);
CREATE INDEX IF NOT EXISTS idx_indicator_codes_source ON qd_indicator_codes USING btree (source_indicator_id);

-- =============================================================================
-- 10. Watchlist
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_watchlist (
    id SERIAL PRIMARY KEY,
    user_id INTEGER DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    market VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    name VARCHAR(100) DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, market, symbol)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON qd_watchlist(user_id);

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

-- =============================================================================
-- 12. Backtest Runs
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd_backtest_runs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
    indicator_id INTEGER,
    strategy_id INTEGER,
    strategy_name VARCHAR(255) DEFAULT '',
    run_type VARCHAR(50) DEFAULT 'indicator',
    market VARCHAR(50) NOT NULL DEFAULT '',
    symbol VARCHAR(50) NOT NULL DEFAULT '',
    timeframe VARCHAR(10) NOT NULL DEFAULT '',
    start_date VARCHAR(20) NOT NULL DEFAULT '',
    end_date VARCHAR(20) NOT NULL DEFAULT '',
    initial_capital DECIMAL(20,8) DEFAULT 10000,
    commission DECIMAL(10,6) DEFAULT 0.001,
    slippage DECIMAL(10,6) DEFAULT 0,
    leverage INTEGER DEFAULT 1,
    trade_direction VARCHAR(20) DEFAULT 'long',
    strategy_config TEXT DEFAULT '',
    config_snapshot TEXT DEFAULT '',
    engine_version VARCHAR(50) DEFAULT '',
    code_hash VARCHAR(128) DEFAULT '',
    status VARCHAR(20) DEFAULT 'success',
    error_message TEXT DEFAULT '',
    result_json TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_user_id ON qd_backtest_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_indicator_id ON qd_backtest_runs(indicator_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_id ON qd_backtest_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_run_type ON qd_backtest_runs(run_type);

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
    UNIQUE(user_id, market, symbol, side, group_name)
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
    updated_at TIMESTAMP DEFAULT NOW()
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
    currency VARCHAR(10) DEFAULT '',
    is_active INTEGER DEFAULT 1,
    is_hot INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(market, symbol)
);

CREATE INDEX IF NOT EXISTS idx_market_symbols_market ON qd_market_symbols(market);
CREATE INDEX IF NOT EXISTS idx_market_symbols_is_hot ON qd_market_symbols(market, is_hot);

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
-- A股 (CNStock)
('CNStock', '600519', '贵州茅台', 'SSE', 'CNY', 1, 1, 100),
('CNStock', '600036', '招商银行', 'SSE', 'CNY', 1, 1, 99),
('CNStock', '601318', '中国平安', 'SSE', 'CNY', 1, 1, 98),
('CNStock', '600900', '长江电力', 'SSE', 'CNY', 1, 1, 97),
('CNStock', '601899', '紫金矿业', 'SSE', 'CNY', 1, 1, 96),
('CNStock', '000858', '五粮液', 'SZSE', 'CNY', 1, 1, 95),
('CNStock', '000333', '美的集团', 'SZSE', 'CNY', 1, 1, 94),
('CNStock', '002594', '比亚迪', 'SZSE', 'CNY', 1, 1, 93),
('CNStock', '300750', '宁德时代', 'SZSE', 'CNY', 1, 1, 92),
('CNStock', '000001', '平安银行', 'SZSE', 'CNY', 1, 1, 91),
-- 港股/H股 (HKStock)
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
('MOEX', 'SBER',  'Сбербанк',       'MOEX', 'RUB', 1, 1, 100),
('MOEX', 'GAZP',  'Газпром',        'MOEX', 'RUB', 1, 1, 99),
('MOEX', 'LKOH',  'Лукойл',         'MOEX', 'RUB', 1, 1, 98),
('MOEX', 'ROSN',  'Роснефть',       'MOEX', 'RUB', 1, 1, 97),
('MOEX', 'GMKN',  'Норильский Никель', 'MOEX', 'RUB', 1, 1, 96),
('MOEX', 'NVTK',  'Новатэк',        'MOEX', 'RUB', 1, 1, 95),
('MOEX', 'TATN',  'Татнефть',       'MOEX', 'RUB', 1, 1, 94),
('MOEX', 'VTBR',  'ВТБ',            'MOEX', 'RUB', 1, 1, 93),
('MOEX', 'MGNT',  'Магнит',         'MOEX', 'RUB', 1, 1, 92),
('MOEX', 'YNDX',  'Яндекс',         'MOEX', 'RUB', 1, 1, 91),
('MOEX', 'SBERP', 'Сбербанк-п',     'MOEX', 'RUB', 1, 1, 90),
('MOEX', 'PLZL',  'Полюс',          'MOEX', 'RUB', 1, 1, 89),
('MOEX', 'CHMF',  'Северсталь',     'MOEX', 'RUB', 1, 1, 88),
('MOEX', 'ALRS',  'АЛРОСА',         'MOEX', 'RUB', 1, 1, 87),
('MOEX', 'MOEX',  'Московская Биржа', 'MOEX', 'RUB', 1, 1, 86)
ON CONFLICT (market, symbol) DO NOTHING;

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
-- 20d. Migration: strategy trade close reason & grid matched PnL (old DBs)
-- =============================================================================

ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS close_reason VARCHAR(64) DEFAULT '';
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS matched_entry_price DECIMAL(20,8) DEFAULT 0;
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS grid_matched_profit DECIMAL(20,8) DEFAULT 0;
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS market_type VARCHAR(20) DEFAULT 'swap';
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS credential_id INTEGER DEFAULT 0;
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS inst_id VARCHAR(80) DEFAULT '';
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS symbol_canonical VARCHAR(50) DEFAULT '';
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS fill_source VARCHAR(32) DEFAULT '';
ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS pending_order_id INTEGER DEFAULT 0;
ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS market_type VARCHAR(20) DEFAULT 'swap';
ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS credential_id INTEGER DEFAULT 0;
ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS inst_id VARCHAR(80) DEFAULT '';
ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS symbol_canonical VARCHAR(50) DEFAULT '';
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS credential_id INTEGER DEFAULT 0;
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS inst_id VARCHAR(80) DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_trades_strategy_symbol_canon ON qd_strategy_trades (strategy_id, market_type, symbol_canonical);
CREATE INDEX IF NOT EXISTS idx_positions_strategy_leg ON qd_strategy_positions (strategy_id, market_type, symbol_canonical, side);
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

-- Indicator Purchases (购买记录)
CREATE TABLE IF NOT EXISTS qd_indicator_purchases (
    id SERIAL PRIMARY KEY,
    indicator_id INTEGER NOT NULL REFERENCES qd_indicator_codes(id) ON DELETE CASCADE,
    buyer_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    seller_id INTEGER NOT NULL REFERENCES qd_users(id),
    price DECIMAL(10,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(indicator_id, buyer_id)
);

CREATE INDEX IF NOT EXISTS idx_purchases_indicator ON qd_indicator_purchases(indicator_id);
CREATE INDEX IF NOT EXISTS idx_purchases_buyer ON qd_indicator_purchases(buyer_id);
CREATE INDEX IF NOT EXISTS idx_purchases_seller ON qd_indicator_purchases(seller_id);

-- Indicator Comments (评论)
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

-- Add community stats columns to qd_indicator_codes
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'purchase_count'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN purchase_count INTEGER DEFAULT 0;
        RAISE NOTICE 'Added purchase_count column to qd_indicator_codes';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'avg_rating'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN avg_rating DECIMAL(3,2) DEFAULT 0;
        RAISE NOTICE 'Added avg_rating column to qd_indicator_codes';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'rating_count'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN rating_count INTEGER DEFAULT 0;
        RAISE NOTICE 'Added rating_count column to qd_indicator_codes';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'qd_indicator_codes' AND column_name = 'view_count'
    ) THEN
        ALTER TABLE qd_indicator_codes ADD COLUMN view_count INTEGER DEFAULT 0;
        RAISE NOTICE 'Added view_count column to qd_indicator_codes';
    END IF;
END $$;

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
    error_msg       TEXT DEFAULT '',
    source          VARCHAR(40) DEFAULT 'manual',          -- ai_radar / ai_analysis / indicator / manual
    raw_result      JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quick_trades_user    ON qd_quick_trades(user_id);
CREATE INDEX IF NOT EXISTS idx_quick_trades_created ON qd_quick_trades(created_at DESC);

-- Migration: Add commission tracking columns to existing qd_quick_trades.
-- (Introduced in v3.0.8. Pre-existing rows default to 0 / '' which is the
-- accurate value — those orders were never enriched with exchange fee data.)
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

-- =============================================================================
-- Polymarket (已移除 / removed in v3.0.7)
-- =============================================================================
-- 预测市场相关功能已下线，相关后台 LLM worker、API、数据源全部删除。
-- 老库一次性清理对应 3 张表与索引；若是全新部署，下面 DROP 是 no-op。
DROP TABLE IF EXISTS qd_polymarket_asset_opportunities CASCADE;
DROP TABLE IF EXISTS qd_polymarket_ai_analysis CASCADE;
DROP TABLE IF EXISTS qd_polymarket_markets CASCADE;

-- =============================================================================
-- 30. Agent Gateway (/api/agent/v1) — tokens, async jobs, audit, idempotency
-- =============================================================================
-- These tables back the multi-agent runtime (see docs/agent/AI_INTEGRATION_DESIGN.md).
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
    status VARCHAR(20) NOT NULL DEFAULT 'active',-- active/revoked/expired
    expires_at TIMESTAMP,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_hash ON qd_agent_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_agent_tokens_user ON qd_agent_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_tokens_status ON qd_agent_tokens(status);

CREATE TABLE IF NOT EXISTS qd_agent_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id VARCHAR(40) NOT NULL UNIQUE,          -- public id (uuid4 hex)
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE CASCADE,
    agent_token_id INTEGER REFERENCES qd_agent_tokens(id) ON DELETE SET NULL,
    kind VARCHAR(40) NOT NULL,                   -- backtest / experiment_pipeline / ai_optimize / ...
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

-- =============================================================================
-- Completion Notice
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE 'QuantDinger PostgreSQL schema initialized successfully!';
END $$;
