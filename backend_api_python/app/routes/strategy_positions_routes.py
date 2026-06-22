"""Strategy live position route facade."""
import time
import traceback

from flask import g, jsonify, request

from app.data_sources import DataSourceFactory
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
        from app.data_sources.crypto import resolve_crypto_venue

        price_exchange_id, price_market_type = resolve_crypto_venue(
            exchange_config=exchange_config,
            trading_config=trading_config,
            market_type=market_type,
        )
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_id, symbol, side, size, entry_price, current_price, highest_price,
                       unrealized_pnl, pnl_percent, equity, updated_at
                FROM qd_strategy_positions
                WHERE strategy_id = ?
                ORDER BY id DESC
                """,
                (strategy_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        execution_mode = str(st.get("execution_mode") or "signal").strip().lower()
        if execution_mode == "live":
            try:
                from app.services.live_trading.strategy_position_sync import sync_strategy_positions_from_exchange

                sync_strategy_positions_from_exchange(strategy_id)
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        """
                        SELECT id, strategy_id, symbol, side, size, entry_price, current_price, highest_price,
                               unrealized_pnl, pnl_percent, equity, updated_at
                        FROM qd_strategy_positions
                        WHERE strategy_id = ?
                        ORDER BY id DESC
                        """,
                        (strategy_id,),
                    )
                    rows = cur.fetchall() or []
                    cur.close()
            except Exception as e:
                logger.warning("sync_strategy_positions_from_exchange failed for strategy %s: %s", strategy_id, e)
        elif not rows:
            try:
                from app.services.live_trading.records import rebuild_positions_from_trades

                if rebuild_positions_from_trades(strategy_id):
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute(
                            """
                            SELECT id, strategy_id, symbol, side, size, entry_price, current_price, highest_price,
                                   unrealized_pnl, pnl_percent, equity, updated_at
                            FROM qd_strategy_positions
                            WHERE strategy_id = ?
                            ORDER BY id DESC
                            """,
                            (strategy_id,),
                        )
                        rows = cur.fetchall() or []
                        cur.close()
            except Exception as e:
                logger.warning("rebuild_positions_from_trades failed for strategy %s: %s", strategy_id, e)

        # Sync current price and PnL on read (frontend polls every few seconds).
        now = int(time.time())
        # Fetch prices once per symbol to reduce API calls.
        sym_to_price: dict[str, float] = {}

        def _fetch_symbol_price(sym: str) -> float:
            sym = (sym or "").strip()
            if not sym:
                return 0.0
            if sym in sym_to_price:
                return sym_to_price[sym]
            try:
                t = DataSourceFactory.get_ticker(
                    "Crypto",
                    sym,
                    exchange_id=price_exchange_id,
                    market_type=price_market_type,
                ) or {}
                px = float(t.get("last") or t.get("close") or 0.0)
                if px > 0:
                    sym_to_price[sym] = px
                    return px
            except Exception:
                pass
            return 0.0

        for r in rows:
            _fetch_symbol_price((r.get("symbol") or "").strip())

        # Apply to rows and persist best-effort
        out = []
        with get_db_connection() as db:
            cur = db.cursor()
            for r in rows:
                sym = (r.get("symbol") or "").strip()
                side = (r.get("side") or "").strip().lower()
                size = float(r.get("size") or 0.0)
                if size <= 1e-12:
                    continue
                entry = float(r.get("entry_price") or 0.0)
                cp = float(sym_to_price.get(sym) or r.get("current_price") or 0.0)
                pnl = calc_unrealized_pnl(side, entry, cp, size)
                pct = calc_pnl_percent(entry, size, pnl, leverage=leverage, market_type=market_type)
                notional = calc_notional_value(entry, size)
                margin_value = calc_margin_notional(notional, leverage, market_type)
                notional_pct = (pnl / notional * 100.0) if notional > 0 else 0.0
                capital_pct = (pnl / initial_capital * 100.0) if initial_capital > 0 else 0.0

                rr = dict(r)
                # Ensure entry_price is populated; use the calculated entry when the database value is NULL.
                if not rr.get("entry_price") or float(rr.get("entry_price") or 0.0) <= 0:
                    rr["entry_price"] = float(entry or 0.0)
                else:
                    rr["entry_price"] = float(rr.get("entry_price") or 0.0)
                rr["current_price"] = float(cp or 0.0)
                rr["unrealized_pnl"] = float(pnl)
                rr["pnl_percent"] = float(pct)
                rr["position_margin_pnl_percent"] = float(pct)
                rr["position_notional_pnl_percent"] = float(notional_pct)
                rr["strategy_capital_pnl_percent"] = float(capital_pct)
                rr["capital_contribution_percent"] = float(capital_pct)
                rr["notional_value"] = float(notional)
                rr["margin_value"] = margin_value
                rr["updated_at"] = now
                out.append(rr)

                try:
                    cur.execute(
                        """
                        UPDATE qd_strategy_positions
                        SET current_price = ?, unrealized_pnl = ?, pnl_percent = ?, updated_at = NOW()
                        WHERE id = ?
                        """,
                        (float(cp or 0.0), float(pnl), float(pct), int(rr.get("id"))),
                    )
                except Exception:
                    pass
            db.commit()
            cur.close()

        from app.services.live_trading.records import normalize_strategy_symbol, strategy_allowed_symbols

        # Strategy positions come only from qd_strategy_positions (L3 ledger).
        # Never substitute the credential-wide account mirror; that made the UI
        # show the entire exchange wallet as "strategy holdings".
        allowed = strategy_allowed_symbols(
            {
                "symbol": st.get("symbol"),
                "trading_config": trading_config,
            }
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
                    list_account_positions,
                    reconcile_strategy_vs_account,
                )
                from app.services.live_trading.leg_context import credential_id_from_exchange_config

                resolved_ex = resolve_exchange_config(exchange_config, user_id=int(user_id or 1))
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
                if allowed:
                    account_rows = [
                        r for r in account_rows
                        if normalize_strategy_symbol(str(r.get("symbol") or "")).upper() in allowed_upper
                    ]
                account_reconciliation = reconcile_strategy_vs_account(out, account_rows)
                account_reconciliation["account_positions"] = account_rows
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
            "source": "fill_ledger" if uses_fill_ledger else "strategy_ledger",
            "synced_from_exchange": False,
            "hint_zh": (
                "\u4ee5\u4e0b\u4e3a\u7b56\u7565\u8d26\u672c\u6301\u4ed3\uff08\u7531\u6210\u4ea4\u8bb0\u5f55\u7d2f\u8ba1\uff09\uff0c"
                "\u7f51\u683c\u7b56\u7565\u4e0d\u4e0e\u4ea4\u6613\u6240\u5b9e\u65f6\u5bf9\u8d26\u3002"
                "\u8bf7\u5bf9\u7167 exchange_snapshot \u67e5\u770b\u4ea4\u6613\u6240\u771f\u5b9e\u6301\u4ed3\u3002"
                if uses_fill_ledger
                else "\u4ee5\u4e0b\u4e3a\u7b56\u7565\u8d26\u672c\u6301\u4ed3\uff0c\u5df2\u4e0e\u4ea4\u6613\u6240\u5bf9\u8d26\u6216\u6309\u6210\u4ea4\u8bb0\u5f55\u91cd\u5efa\u3002"
            ),
            "hint_en": (
                "Strategy ledger positions (from fills). Grid bots skip live exchange reconciliation; "
                "compare exchange_snapshot for actual exchange holdings."
                if uses_fill_ledger
                else "Strategy ledger positions from this strategy's own fills. Exchange positions are only reconciliation hints."
            ),
        }

        exchange_snapshot = None
        bot_type = str(st.get("bot_type") or trading_config.get("bot_type") or "").strip().lower()
        if execution_mode == "live" and bot_type == "grid":
            try:
                from app.services.exchange_execution import resolve_exchange_config
                from app.services.live_trading.factory import create_client
                from app.services.grid.exchange_requirements import fetch_exchange_dual_leg_snapshot

                resolved_ex = resolve_exchange_config(exchange_config, user_id=int(user_id or 1))
                sym = str(st.get("symbol") or trading_config.get("symbol") or "").strip()
                if sym and resolved_ex:
                    client = create_client(resolved_ex, market_type=market_type)
                    exchange_snapshot = fetch_exchange_dual_leg_snapshot(
                        client,
                        symbol=sym,
                        market_type=market_type,
                        exchange_config=resolved_ex,
                    )
                    exchange_snapshot["symbol"] = sym
            except Exception as e:
                logger.debug("grid exchange_snapshot for strategy %s: %s", strategy_id, e)

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'positions': out,
                'items': out,
                'position_meta': position_meta,
                'exchange_snapshot': exchange_snapshot,
                'account_reconciliation': account_reconciliation,
            },
        })
    except Exception as e:
        logger.error(f"get_positions failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'positions': [], 'items': []}}), 500


