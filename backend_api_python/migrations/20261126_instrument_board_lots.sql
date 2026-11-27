ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS board_lot INTEGER CHECK (board_lot > 0);
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS rules_as_of DATE;
ALTER TABLE qd_market_symbols ADD COLUMN IF NOT EXISTS rules_source TEXT;
