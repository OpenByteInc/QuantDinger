-- Align signal-ledger capital with the strategy capital used for sizing and PnL.
UPDATE qd_strategy_virtual_trades AS vt
SET account_equity = vt.account_equity + (s.initial_capital - va.initial_cash)
FROM qd_strategy_virtual_accounts AS va,
     qd_strategies_trading AS s
WHERE va.strategy_id = vt.strategy_id
  AND s.id = va.strategy_id
  AND LOWER(COALESCE(s.execution_mode, 'signal')) = 'signal'
  AND s.initial_capital > 0
  AND ABS(va.initial_cash - s.initial_capital) > GREATEST(0.00000001, s.initial_capital * 0.0000000001);

UPDATE qd_strategy_virtual_accounts AS va
SET initial_cash = s.initial_capital,
    cash_balance = s.initial_capital + va.realized_pnl,
    updated_at = NOW()
FROM qd_strategies_trading AS s
WHERE s.id = va.strategy_id
  AND LOWER(COALESCE(s.execution_mode, 'signal')) = 'signal'
  AND s.initial_capital > 0
  AND ABS(va.initial_cash - s.initial_capital) > GREATEST(0.00000001, s.initial_capital * 0.0000000001);
