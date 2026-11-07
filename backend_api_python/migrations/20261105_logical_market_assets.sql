CREATE OR REPLACE FUNCTION qd_market_asset_key(
  p_market TEXT,
  p_symbol TEXT,
  p_product_type TEXT DEFAULT '',
  p_underlying_market TEXT DEFAULT '',
  p_underlying_symbol TEXT DEFAULT ''
) RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
AS $$
  SELECT LOWER(TRIM(COALESCE(p_market, ''))) || ':' ||
         UPPER(TRIM(COALESCE(p_symbol, ''))) || ':' ||
         CASE
           WHEN TRIM(COALESCE(p_market, '')) = 'Crypto'
             THEN COALESCE(NULLIF(LOWER(TRIM(COALESCE(p_product_type, ''))), ''), 'crypto')
           ELSE LOWER(TRIM(COALESCE(p_market, '')))
         END ||
         CASE
           WHEN TRIM(COALESCE(p_market, '')) = 'Crypto'
                AND COALESCE(NULLIF(LOWER(TRIM(COALESCE(p_product_type, ''))), ''), 'crypto') <> 'crypto'
             THEN ':' || LOWER(TRIM(COALESCE(p_underlying_market, ''))) ||
                  ':' || UPPER(TRIM(COALESCE(p_underlying_symbol, '')))
           ELSE ''
         END;
$$;

CREATE TABLE IF NOT EXISTS qd_market_assets (
  id BIGSERIAL PRIMARY KEY,
  asset_key VARCHAR(320) NOT NULL UNIQUE,
  market VARCHAR(50) NOT NULL,
  symbol VARCHAR(50) NOT NULL,
  name VARCHAR(255) NOT NULL DEFAULT '',
  asset_class VARCHAR(32) NOT NULL DEFAULT '',
  product_type VARCHAR(32) NOT NULL DEFAULT '',
  underlying_market VARCHAR(32) NOT NULL DEFAULT '',
  underlying_symbol VARCHAR(50) NOT NULL DEFAULT '',
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_assets_lookup
  ON qd_market_assets(market, UPPER(symbol), product_type, is_active);

INSERT INTO qd_market_assets (
  asset_key, market, symbol, name, asset_class, product_type,
  underlying_market, underlying_symbol, is_active
)
SELECT DISTINCT ON (asset_key)
       asset_key, market, symbol, name, asset_class, product_type,
       underlying_market, underlying_symbol, 1
FROM (
  SELECT qd_market_asset_key(
           market, symbol, product_type, underlying_market, underlying_symbol
         ) AS asset_key,
         market, UPPER(symbol) AS symbol, COALESCE(name, '') AS name,
         COALESCE(asset_class, '') AS asset_class,
         CASE WHEN market = 'Crypto'
              THEN COALESCE(NULLIF(LOWER(product_type), ''), 'crypto')
              ELSE LOWER(market)
         END AS product_type,
         COALESCE(underlying_market, '') AS underlying_market,
         UPPER(COALESCE(underlying_symbol, '')) AS underlying_symbol,
         metadata_updated_at, id
  FROM qd_market_symbols
) catalog
ORDER BY asset_key, metadata_updated_at DESC NULLS LAST, id DESC
ON CONFLICT (asset_key) DO UPDATE SET
  name = CASE WHEN EXCLUDED.name <> '' THEN EXCLUDED.name ELSE qd_market_assets.name END,
  asset_class = CASE WHEN EXCLUDED.asset_class <> '' THEN EXCLUDED.asset_class ELSE qd_market_assets.asset_class END,
  is_active = 1,
  updated_at = NOW();

ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS asset_id BIGINT;

UPDATE qd_market_symbols s
SET asset_id = a.id
FROM qd_market_assets a
WHERE a.asset_key = qd_market_asset_key(
  s.market, s.symbol, s.product_type, s.underlying_market, s.underlying_symbol
);

ALTER TABLE qd_market_symbols
  DROP CONSTRAINT IF EXISTS qd_market_symbols_asset_id_fkey;
ALTER TABLE qd_market_symbols
  ADD CONSTRAINT qd_market_symbols_asset_id_fkey
  FOREIGN KEY (asset_id) REFERENCES qd_market_assets(id) ON DELETE RESTRICT;
ALTER TABLE qd_market_symbols ALTER COLUMN asset_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_market_symbols_asset_context
  ON qd_market_symbols(asset_id, exchange, market_type, is_active);

CREATE OR REPLACE FUNCTION qd_assign_market_asset()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  v_key TEXT;
  v_asset_id BIGINT;
  v_product_type TEXT;
BEGIN
  v_product_type := CASE
    WHEN NEW.market = 'Crypto'
      THEN COALESCE(NULLIF(LOWER(TRIM(COALESCE(NEW.product_type, ''))), ''), 'crypto')
    ELSE LOWER(NEW.market)
  END;
  v_key := qd_market_asset_key(
    NEW.market, NEW.symbol, v_product_type,
    NEW.underlying_market, NEW.underlying_symbol
  );
  INSERT INTO qd_market_assets (
    asset_key, market, symbol, name, asset_class, product_type,
    underlying_market, underlying_symbol, is_active
  ) VALUES (
    v_key, NEW.market, UPPER(NEW.symbol), COALESCE(NEW.name, ''),
    COALESCE(NEW.asset_class, ''), v_product_type,
    COALESCE(NEW.underlying_market, ''), UPPER(COALESCE(NEW.underlying_symbol, '')), 1
  )
  ON CONFLICT (asset_key) DO UPDATE SET
    name = CASE WHEN EXCLUDED.name <> '' THEN EXCLUDED.name ELSE qd_market_assets.name END,
    asset_class = CASE WHEN EXCLUDED.asset_class <> '' THEN EXCLUDED.asset_class ELSE qd_market_assets.asset_class END,
    is_active = 1,
    updated_at = NOW()
  RETURNING id INTO v_asset_id;
  NEW.asset_id := v_asset_id;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_symbols_asset ON qd_market_symbols;
CREATE TRIGGER trg_market_symbols_asset
BEFORE INSERT OR UPDATE OF market, symbol, name, asset_class, product_type,
  underlying_market, underlying_symbol
ON qd_market_symbols
FOR EACH ROW EXECUTE FUNCTION qd_assign_market_asset();

CREATE TABLE IF NOT EXISTS qd_watchlist_migration_archive AS
SELECT *, NOW() AS archived_at, 'logical_asset_migration'::TEXT AS archived_reason
FROM qd_watchlist
WHERE FALSE;

INSERT INTO qd_watchlist_migration_archive
SELECT *, NOW(), 'logical_asset_migration'
FROM qd_watchlist;

ALTER TABLE qd_watchlist ADD COLUMN IF NOT EXISTS asset_id BIGINT;

INSERT INTO qd_market_assets (
  asset_key, market, symbol, name, asset_class, product_type,
  underlying_market, underlying_symbol, is_active
)
SELECT DISTINCT ON (asset_key)
  asset_key,
  market,
  symbol,
  name,
  asset_class,
  product_type,
  underlying_market,
  underlying_symbol,
  1
FROM (
SELECT
  qd_market_asset_key(w.market, w.symbol,
    CASE WHEN w.market = 'Crypto' THEN COALESCE(NULLIF(s.product_type, ''), 'crypto') ELSE LOWER(w.market) END,
    COALESCE(s.underlying_market, ''), COALESCE(s.underlying_symbol, '')) AS asset_key,
  w.market AS market,
  UPPER(w.symbol) AS symbol,
  COALESCE(NULLIF(w.name, ''), w.symbol) AS name,
  COALESCE(s.asset_class, CASE WHEN w.market = 'Crypto' THEN 'crypto' ELSE LOWER(w.market) END) AS asset_class,
  CASE WHEN w.market = 'Crypto' THEN COALESCE(NULLIF(s.product_type, ''), 'crypto') ELSE LOWER(w.market) END AS product_type,
  COALESCE(s.underlying_market, '') AS underlying_market,
  UPPER(COALESCE(s.underlying_symbol, '')) AS underlying_symbol,
  w.id AS watchlist_id
FROM qd_watchlist w
LEFT JOIN LATERAL (
  SELECT product_type, asset_class, underlying_market, underlying_symbol
  FROM qd_market_symbols s
  WHERE s.market = w.market AND UPPER(s.symbol) = UPPER(w.symbol)
    AND (w.exchange_id = '' OR s.exchange = w.exchange_id)
    AND (w.market_type = '' OR s.market_type = w.market_type)
    AND (w.instrument_id = '' OR s.instrument_id = w.instrument_id)
  ORDER BY s.is_active DESC, s.id DESC
  LIMIT 1
) s ON TRUE
) candidates
ORDER BY asset_key, watchlist_id DESC
ON CONFLICT (asset_key) DO UPDATE SET
  name = CASE WHEN EXCLUDED.name <> '' THEN EXCLUDED.name ELSE qd_market_assets.name END,
  is_active = 1,
  updated_at = NOW();

UPDATE qd_watchlist w
SET asset_id = a.id
FROM qd_market_assets a
WHERE a.asset_key = qd_market_asset_key(
  w.market, w.symbol,
  CASE WHEN w.market = 'Crypto' THEN COALESCE((
    SELECT NULLIF(s.product_type, '')
    FROM qd_market_symbols s
    WHERE s.market = w.market AND UPPER(s.symbol) = UPPER(w.symbol)
      AND (w.exchange_id = '' OR s.exchange = w.exchange_id)
      AND (w.market_type = '' OR s.market_type = w.market_type)
      AND (w.instrument_id = '' OR s.instrument_id = w.instrument_id)
    ORDER BY s.is_active DESC, s.id DESC LIMIT 1
  ), 'crypto') ELSE LOWER(w.market) END,
  COALESCE((
    SELECT s.underlying_market FROM qd_market_symbols s
    WHERE s.market = w.market AND UPPER(s.symbol) = UPPER(w.symbol)
      AND (w.exchange_id = '' OR s.exchange = w.exchange_id)
      AND (w.market_type = '' OR s.market_type = w.market_type)
      AND (w.instrument_id = '' OR s.instrument_id = w.instrument_id)
    ORDER BY s.is_active DESC, s.id DESC LIMIT 1
  ), ''),
  COALESCE((
    SELECT s.underlying_symbol FROM qd_market_symbols s
    WHERE s.market = w.market AND UPPER(s.symbol) = UPPER(w.symbol)
      AND (w.exchange_id = '' OR s.exchange = w.exchange_id)
      AND (w.market_type = '' OR s.market_type = w.market_type)
      AND (w.instrument_id = '' OR s.instrument_id = w.instrument_id)
    ORDER BY s.is_active DESC, s.id DESC LIMIT 1
  ), '')
);

DELETE FROM qd_watchlist older
USING qd_watchlist newer
WHERE older.user_id = newer.user_id
  AND older.asset_id = newer.asset_id
  AND older.id < newer.id;

ALTER TABLE qd_watchlist DROP CONSTRAINT IF EXISTS uq_watchlist_market_context;
DROP INDEX IF EXISTS uq_watchlist_market_context;
ALTER TABLE qd_watchlist DROP CONSTRAINT IF EXISTS uq_watchlist_asset;
DROP INDEX IF EXISTS uq_watchlist_asset;
ALTER TABLE qd_watchlist ALTER COLUMN asset_id SET NOT NULL;
ALTER TABLE qd_watchlist
  DROP CONSTRAINT IF EXISTS qd_watchlist_asset_id_fkey;
ALTER TABLE qd_watchlist
  ADD CONSTRAINT qd_watchlist_asset_id_fkey
  FOREIGN KEY (asset_id) REFERENCES qd_market_assets(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_watchlist_user_asset
  ON qd_watchlist(user_id, asset_id);

ALTER TABLE qd_watchlist DROP COLUMN IF EXISTS market;
ALTER TABLE qd_watchlist DROP COLUMN IF EXISTS symbol;
ALTER TABLE qd_watchlist DROP COLUMN IF EXISTS name;
ALTER TABLE qd_watchlist DROP COLUMN IF EXISTS exchange_id;
ALTER TABLE qd_watchlist DROP COLUMN IF EXISTS market_type;
ALTER TABLE qd_watchlist DROP COLUMN IF EXISTS instrument_id;
ALTER TABLE qd_watchlist DROP COLUMN IF EXISTS settle_currency;
