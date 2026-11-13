"""Account-level exposure controls for multi-strategy hedge accounts."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.services.live_trading.leg_context import credential_id_from_exchange_config
from app.services.live_trading.records import normalize_strategy_symbol
from app.utils.db import get_db_connection


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _positive(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return max(0.0, float(default or 0.0))


def _ratio(value: Any, default: float = 0.0) -> float:
    out = _positive(value, default)
    if out > 1.0:
        out = out / 100.0
    return out


def _market_type(value: Any) -> str:
    out = str(value or "swap").strip().lower()
    if out in {"future", "futures", "perp", "perpetual"}:
        return "swap"
    return out or "swap"


def _strategy_credential_id(row: Dict[str, Any]) -> int:
    trading_config = _json_object(row.get("trading_config"))
    exchange_config = _json_object(row.get("exchange_config"))
    return int(
        credential_id_from_exchange_config(exchange_config)
        or trading_config.get("credential_id")
        or 0
    )


def _strategy_leverage(row: Dict[str, Any]) -> float:
    trading_config = _json_object(row.get("trading_config"))
    return max(1.0, _positive(trading_config.get("leverage") or row.get("leverage"), 1.0))


def _strategy_fee_rate(row: Dict[str, Any]) -> float:
    trading_config = _json_object(row.get("trading_config"))
    account_risk = _json_object(trading_config.get("account_risk"))
    return _ratio(
        account_risk.get("fee_rate")
        or trading_config.get("commission")
        or 0.001
    )


def _strategy_funding_rate(row: Dict[str, Any]) -> float:
    trading_config = _json_object(row.get("trading_config"))
    account_risk = _json_object(trading_config.get("account_risk"))
    return _ratio(
        account_risk.get("funding_rate_estimate")
        or trading_config.get("funding_rate_estimate")
        or 0.0
    )


def _load_account_rows(*, user_id: int) -> List[Dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT s.id AS strategy_id, s.status, s.initial_capital, s.leverage,
                   s.market_type AS strategy_market_type, s.exchange_config, s.trading_config,
                   p.symbol, p.symbol_canonical, p.side, p.size, p.entry_price,
                   p.current_price, p.market_type, p.credential_id
            FROM qd_strategies_trading s
            LEFT JOIN qd_strategy_positions p
              ON p.strategy_id = s.id
             AND p.size > 0
             AND COALESCE(p.position_state, 'open') <> 'exit_residual'
            WHERE s.user_id = %s AND s.execution_mode = 'live'
            """,
            (int(user_id),),
        )
        rows = cur.fetchall() or []
        cur.close()
    return [dict(row) for row in rows]


def account_risk_snapshot(
    *,
    user_id: int,
    credential_id: int,
    market_type: str,
    strategy_id: int = 0,
    proposed_symbol: str = "",
    proposed_side: str = "",
    proposed_quantity: float = 0.0,
    proposed_price: float = 0.0,
    proposed_leverage: float = 1.0,
    limits: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return projected gross, margin, fee, funding, and limit violations."""
    cred = int(credential_id or 0)
    mt = _market_type(market_type)
    sid = int(strategy_id or 0)
    rows = _load_account_rows(user_id=int(user_id))
    matched: List[Dict[str, Any]] = []
    strategy_rows: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        row_sid = int(row.get("strategy_id") or 0)
        row_mt = _market_type(row.get("strategy_market_type") or row.get("market_type"))
        row_cred = int(row.get("credential_id") or 0) or _strategy_credential_id(row)
        if row_mt != mt or (cred > 0 and row_cred != cred):
            continue
        strategy_rows.setdefault(row_sid, row)
        if row.get("symbol") or row.get("symbol_canonical"):
            matched.append(row)

    if sid > 0 and sid not in strategy_rows:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id AS strategy_id, status, initial_capital, leverage, market_type AS strategy_market_type,
                       exchange_config, trading_config
                FROM qd_strategies_trading
                WHERE id = %s AND user_id = %s
                """,
                (sid, int(user_id)),
            )
            current = cur.fetchone() or {}
            cur.close()
        if current:
            strategy_rows[sid] = dict(current)

    long_notional = 0.0
    short_notional = 0.0
    margin_estimate = 0.0
    fee_estimate = 0.0
    funding_estimate = 0.0
    symbol_gross: Dict[str, float] = {}
    unpriced_position_count = 0
    for row in matched:
        quantity = _positive(row.get("size"))
        price = _positive(row.get("current_price") or row.get("entry_price"))
        notional = quantity * price
        if quantity > 0 and price <= 0:
            unpriced_position_count += 1
        if notional <= 0:
            continue
        row_sid = int(row.get("strategy_id") or 0)
        strategy_row = strategy_rows.get(row_sid, row)
        leverage = _strategy_leverage(strategy_row)
        fee_rate = _strategy_fee_rate(strategy_row)
        funding_rate = _strategy_funding_rate(strategy_row)
        side = str(row.get("side") or "long").strip().lower()
        if side == "short":
            short_notional += notional
        else:
            long_notional += notional
        symbol = normalize_strategy_symbol(
            str(row.get("symbol_canonical") or row.get("symbol") or "")
        )
        symbol_gross[symbol] = symbol_gross.get(symbol, 0.0) + notional
        margin_estimate += notional / leverage
        fee_estimate += notional * fee_rate * 2.0
        funding_estimate += notional * funding_rate

    proposed_notional = _positive(proposed_quantity) * _positive(proposed_price)
    proposed_price_missing = _positive(proposed_quantity) > 0 and _positive(proposed_price) <= 0
    proposed_side_norm = str(proposed_side or "").strip().lower()
    proposed_symbol_norm = normalize_strategy_symbol(proposed_symbol)
    if proposed_notional > 0:
        if proposed_side_norm == "short":
            short_notional += proposed_notional
        else:
            long_notional += proposed_notional
        symbol_gross[proposed_symbol_norm] = symbol_gross.get(proposed_symbol_norm, 0.0) + proposed_notional
        current_row = strategy_rows.get(sid, {})
        fee_rate = _strategy_fee_rate(current_row)
        funding_rate = _strategy_funding_rate(current_row)
        margin_estimate += proposed_notional / max(1.0, _positive(proposed_leverage, 1.0))
        fee_estimate += proposed_notional * fee_rate * 2.0
        funding_estimate += proposed_notional * funding_rate

    capital_budget = sum(
        _positive(row.get("initial_capital"))
        for row in strategy_rows.values()
        if str(row.get("status") or "").lower() == "running" or int(row.get("strategy_id") or 0) == sid
    )
    gross_capacity = sum(
        _positive(row.get("initial_capital")) * _strategy_leverage(row)
        for row in strategy_rows.values()
        if str(row.get("status") or "").lower() == "running" or int(row.get("strategy_id") or 0) == sid
    )
    gross_notional = long_notional + short_notional
    net_notional = long_notional - short_notional
    hedge_ratio = (
        min(long_notional, short_notional) / max(long_notional, short_notional)
        if max(long_notional, short_notional) > 0
        else 0.0
    )
    gross_leverage = gross_notional / capital_budget if capital_budget > 0 else 0.0

    policy = dict(limits or {})
    max_gross_notional = _positive(policy.get("max_gross_notional")) or gross_capacity
    max_margin_estimate = _positive(policy.get("max_margin_estimate")) or capital_budget
    max_round_trip_fee = _positive(policy.get("max_round_trip_fee")) or capital_budget * _ratio(
        policy.get("max_fee_budget_ratio"), 0.05
    )
    max_funding_per_interval = _positive(policy.get("max_funding_per_interval")) or capital_budget * _ratio(
        policy.get("max_funding_budget_ratio"), 0.02
    )
    max_gross_leverage = _positive(policy.get("max_gross_leverage")) or (
        gross_capacity / capital_budget if capital_budget > 0 else 0.0
    )
    max_symbol_gross_notional = _positive(policy.get("max_symbol_gross_notional")) or max_gross_notional

    violations: List[str] = []
    tolerance = 1e-8
    if unpriced_position_count > 0:
        violations.append("accountRisk.positionPriceMissing")
    if proposed_price_missing:
        violations.append("accountRisk.proposedPriceMissing")
    if max_gross_notional > 0 and gross_notional > max_gross_notional + tolerance:
        violations.append("accountRisk.grossNotionalExceeded")
    if max_margin_estimate > 0 and margin_estimate > max_margin_estimate + tolerance:
        violations.append("accountRisk.marginEstimateExceeded")
    if max_round_trip_fee > 0 and fee_estimate > max_round_trip_fee + tolerance:
        violations.append("accountRisk.feeBudgetExceeded")
    if max_funding_per_interval > 0 and funding_estimate > max_funding_per_interval + tolerance:
        violations.append("accountRisk.fundingBudgetExceeded")
    if max_gross_leverage > 0 and gross_leverage > max_gross_leverage + tolerance:
        violations.append("accountRisk.grossLeverageExceeded")
    if max_symbol_gross_notional > 0 and proposed_symbol_norm:
        if symbol_gross.get(proposed_symbol_norm, 0.0) > max_symbol_gross_notional + tolerance:
            violations.append("accountRisk.symbolGrossNotionalExceeded")

    return {
        "allowed": not violations,
        "violations": violations,
        "credential_id": cred,
        "market_type": mt,
        "strategy_count": len(strategy_rows),
        "long_notional": long_notional,
        "short_notional": short_notional,
        "gross_notional": gross_notional,
        "net_notional": net_notional,
        "hedge_ratio": hedge_ratio,
        "gross_leverage": gross_leverage,
        "margin_estimate": margin_estimate,
        "round_trip_fee_estimate": fee_estimate,
        "funding_per_interval_estimate": funding_estimate,
        "capital_budget": capital_budget,
        "gross_capacity": gross_capacity,
        "symbol_gross_notional": symbol_gross,
        "unpriced_position_count": unpriced_position_count,
        "limits": {
            "max_gross_notional": max_gross_notional,
            "max_symbol_gross_notional": max_symbol_gross_notional,
            "max_margin_estimate": max_margin_estimate,
            "max_round_trip_fee": max_round_trip_fee,
            "max_funding_per_interval": max_funding_per_interval,
            "max_gross_leverage": max_gross_leverage,
        },
    }


def account_risk_limits(strategy_config: Dict[str, Any]) -> Dict[str, Any]:
    trading_config = _json_object(strategy_config.get("trading_config"))
    value = trading_config.get("account_risk")
    return _json_object(value)
