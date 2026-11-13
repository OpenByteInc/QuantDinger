"""Strategy live position route facade."""
import traceback

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.routes.strategy_services import get_strategy_service
from app.utils.auth import login_required
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.pnl import (
    calc_margin_notional,
    calc_notional_value,
    calc_pnl_percent,
    calc_unrealized_pnl,
    is_derivatives_market,
)


logger = get_logger(__name__)


@strategy_blp.route('/strategies/positions', methods=['GET'])
@login_required
def get_positions():
    """Get position records for the current user's strategy."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': {'positions': [], 'items': []}}), 400
        
        # Verify strategy belongs to user
        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': {'positions': [], 'items': []}}), 404

        trading_config = st.get("trading_config") if isinstance(st.get("trading_config"), dict) else {}
        try:
            leverage = float(trading_config.get("leverage") or st.get("leverage") or 1.0)
        except Exception:
            leverage = 1.0
        if leverage <= 0:
            leverage = 1.0
        market_type = str(trading_config.get("market_type") or st.get("market_type") or "swap").strip().lower()
        if is_derivatives_market(market_type):
            market_type = "swap"
        try:
            initial_capital = float(st.get("initial_capital") or trading_config.get("initial_capital") or 0.0)
        except Exception:
            initial_capital = 0.0

        exchange_config = st.get("exchange_config") if isinstance(st.get("exchange_config"), dict) else {}
        execution_mode = str(st.get("execution_mode") or "signal").strip().lower()
        if execution_mode == "signal":
            from app.services.virtual_trading import list_virtual_positions

            rows = list_virtual_positions(strategy_id)
        else:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, strategy_id, symbol, side, size, entry_price, current_price, highest_price,
                           unrealized_pnl, pnl_percent, equity, position_state, tradable_size,
                           residual_reason, residual_classified_at, updated_at
                    FROM qd_strategy_positions
                    WHERE strategy_id = ?
                    ORDER BY id DESC
                    """,
                    (strategy_id,)
                )
                rows = cur.fetchall() or []
                cur.close()

        # This route is a read model. Exchange reconciliation and price
        # refreshes are owned by the trading worker, so a UI request never
        # waits on an exchange or mutates the position ledger.
        out = []
        for r in rows:
            side = (r.get("side") or "").strip().lower()
            size = float(r.get("size") or 0.0)
            if size <= 1e-12:
                continue
            entry = float(r.get("entry_price") or 0.0)
            current_price = float(r.get("current_price") or entry or 0.0)
            pnl = calc_unrealized_pnl(side, entry, current_price, size)
            pct = calc_pnl_percent(
                entry,
                size,
                pnl,
                leverage=leverage,
                market_type=market_type,
            )
            notional = calc_notional_value(entry, size)
            margin_value = calc_margin_notional(notional, leverage, market_type)
            notional_pct = (pnl / notional * 100.0) if notional > 0 else 0.0
            capital_pct = (pnl / initial_capital * 100.0) if initial_capital > 0 else 0.0

            rr = dict(r)
            rr["entry_price"] = entry
            rr["current_price"] = current_price
            rr["unrealized_pnl"] = pnl
            rr["pnl_percent"] = pct
            rr["position_margin_pnl_percent"] = pct
            rr["position_notional_pnl_percent"] = notional_pct
            rr["strategy_capital_pnl_percent"] = capital_pct
            rr["capital_contribution_percent"] = capital_pct
            rr["notional_value"] = notional
            rr["margin_value"] = margin_value
            out.append(rr)

        from app.services.live_trading.records import normalize_strategy_symbol, strategy_allowed_symbols

        # Strategy positions come only from qd_strategy_positions (L3 ledger).
        # Never substitute the credential-wide account mirror; that made the UI
        # show the entire exchange wallet as "strategy holdings".
        allowed = strategy_allowed_symbols(
            {
                "symbol": st.get("symbol"),
                "trading_config": trading_config,
            },
            strategy_id=int(strategy_id),
        )
        if allowed:
            allowed_upper = {
                normalize_strategy_symbol(str(s or "")).upper()
                for s in allowed
                if normalize_strategy_symbol(str(s or ""))
            }
            out = [
                r
                for r in out
                if normalize_strategy_symbol(str(r.get("symbol") or "")).upper() in allowed_upper
            ]

        account_reconciliation = {
            "status": "not_checked",
            "notes": [],
            "account_positions": [],
        }
        if execution_mode == "live":
            try:
                from app.services.exchange_execution import resolve_exchange_config
                from app.services.live_trading.account_positions import (
                    filter_position_rows_by_symbols,
                    list_account_positions,
                    list_strategy_allocations_for_account,
                    reconcile_strategy_vs_account,
                )
                from app.services.live_trading.leg_context import credential_id_from_exchange_config

                resolved_ex = resolve_exchange_config(exchange_config, user_id=int(user_id or 1))
                if str(resolved_ex.get("exchange_id") or "").lower() == "alpaca":
                    market_type = "spot"
                cred_id = int(
                    credential_id_from_exchange_config(resolved_ex)
                    or credential_id_from_exchange_config(exchange_config)
                    or 0
                )
                account_rows = list_account_positions(
                    user_id=int(user_id),
                    credential_id=cred_id if cred_id > 0 else None,
                    market_type=market_type,
                )
                account_rows = filter_position_rows_by_symbols(account_rows, allowed)
                allocated_rows = list_strategy_allocations_for_account(
                    user_id=int(user_id),
                    credential_id=cred_id,
                    market_type=market_type,
                    allowed_symbols=allowed,
                    exchange_id=str(resolved_ex.get("exchange_id") or ""),
                )
                from app.services.live_trading.position_ownership import (
                    build_ownership_rows,
                    list_reservations,
                )

                protected_rows = list_reservations(
                    user_id=int(user_id),
                    credential_id=cred_id,
                    market_type=market_type,
                )
                protected_rows = filter_position_rows_by_symbols(protected_rows, allowed)
                account_reconciliation = reconcile_strategy_vs_account(
                    out,
                    account_rows,
                    allocated_rows=allocated_rows,
                    protected_rows=protected_rows,
                )
                account_reconciliation["account_positions"] = account_rows
                account_reconciliation["ownership"] = build_ownership_rows(
                    account_rows=account_rows,
                    allocated_rows=allocated_rows,
                    reservation_rows=protected_rows,
                )
            except Exception as e:
                account_reconciliation = {
                    "status": "error",
                    "notes": [str(e)],
                    "account_positions": [],
                }

        from app.services.live_trading.strategy_position_sync import strategy_uses_fill_ledger

        uses_fill_ledger = strategy_uses_fill_ledger(
            {
                "strategy_type": st.get("strategy_type"),
                "bot_type": st.get("bot_type") or trading_config.get("bot_type"),
                "trading_config": trading_config,
            }
        )
        position_meta = {
            "source": (
                "virtual_account"
                if execution_mode == "signal"
                else "fill_ledger" if uses_fill_ledger else "strategy_ledger"
            ),
            "hint_key": (
                "strategyCenter.console.virtualPositionsHint"
                if execution_mode == "signal"
                else ""
            ),
            "synced_from_exchange": False,
            "hint_zh": (
                ""
                if execution_mode == "signal"
                else
                "\u4ee5\u4e0b\u4e3a\u7b56\u7565\u8d26\u672c\u6301\u4ed3\uff08\u7531\u6210\u4ea4\u8bb0\u5f55\u7d2f\u8ba1\uff09\uff0c"
                "\u7f51\u683c\u7b56\u7565\u4e0d\u4e0e\u4ea4\u6613\u6240\u5b9e\u65f6\u5bf9\u8d26\u3002"
                "\u8bf7\u5bf9\u7167 exchange_snapshot \u67e5\u770b\u4ea4\u6613\u6240\u771f\u5b9e\u6301\u4ed3\u3002"
                if uses_fill_ledger
                else "\u4ee5\u4e0b\u4e3a\u7b56\u7565\u8d26\u672c\u6301\u4ed3\uff0c\u5df2\u4e0e\u4ea4\u6613\u6240\u5bf9\u8d26\u6216\u6309\u6210\u4ea4\u8bb0\u5f55\u91cd\u5efa\u3002"
            ),
            "hint_en": (
                ""
                if execution_mode == "signal"
                else
                "Strategy ledger positions (from fills). Grid bots skip live exchange reconciliation; "
                "compare exchange_snapshot for actual exchange holdings."
                if uses_fill_ledger
                else "Strategy ledger positions from this strategy's own fills. Exchange positions are only reconciliation hints."
            ),
        }

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'positions': out,
                'items': out,
                'position_meta': position_meta,
                'exchange_snapshot': None,
                'account_reconciliation': account_reconciliation,
            },
        })
    except Exception as e:
        logger.error(f"get_positions failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'positions': [], 'items': []}}), 500
