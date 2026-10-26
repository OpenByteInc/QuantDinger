"""Strategy ledger, equity curve, and performance routes."""
from datetime import datetime, timezone
import time
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
    is_derivatives_market,
)


logger = get_logger(__name__)


def _normalize_trade_row_for_api(trade: dict, *, leverage: float = 1.0, market_type: str = "spot") -> dict:
    """Ensure numeric fields are JSON-friendly floats."""
    try:
        from decimal import Decimal
    except Exception:  # pragma: no cover
        Decimal = ()  # type: ignore
    out = dict(trade)
    commission_ccy = str(out.get("commission_ccy") or "").strip().upper()
    exchange_id = str(out.get("exchange_id") or "").strip().lower()
    if (
        out.get("commission_quote") is None
        and commission_ccy in {"", "UNKNOWN"}
        and (commission_ccy != "UNKNOWN" or exchange_id == "bitget")
    ):
        from app.services.live_trading.fee_quote import STABLE_QUOTES, symbol_currencies

        _, quote_currency = symbol_currencies(str(out.get("symbol") or ""))
        if quote_currency in STABLE_QUOTES and out.get("commission") is not None:
            out["commission_quote"] = out.get("commission")
            out["commission_ccy"] = quote_currency
    for k in (
        "price",
        "amount",
        "value",
        "commission",
        "commission_quote",
        "profit",
        "profit_gross",
        "net_pnl",
        "open_commission_allocated",
        "close_commission",
        "total_commission",
        "reference_price",
        "commission_rate",
        "slippage_rate",
        "slippage_quote",
        "leverage",
    ):
        v = out.get(k)
        if isinstance(v, Decimal):
            out[k] = float(v)
    try:
        price = float(out.get("price") or 0.0)
        amount = float(out.get("amount") or 0.0)
        value = float(out.get("value") or 0.0)
        if value <= 0 and price > 0 and amount > 0:
            value = calc_notional_value(price, amount)
            out["value"] = value
        out["notional_value"] = value
        out["margin_value"] = calc_margin_notional(value, leverage, market_type)
        profit = out.get("profit")
        if profit is not None:
            gross = out.get("profit_gross")
            if gross is None:
                gross = profit
            try:
                gross_f = float(gross)
            except Exception:
                gross_f = float(profit or 0.0)
            open_comm = float(out.get("open_commission_allocated") or 0.0)
            close_comm = float(
                out.get("close_commission")
                if out.get("close_commission") is not None
                else out.get("commission") or 0.0
            )
            net = float(profit)
            if out.get("net_pnl") is None:
                net = gross_f - close_comm - open_comm
                out["net_pnl"] = round(net, 8)
            if out.get("profit_gross") is None:
                out["profit_gross"] = gross_f
            if out.get("total_commission") is None:
                out["total_commission"] = round(close_comm + open_comm, 8)
            out["profit"] = round(net, 8)
            margin = float(out.get("margin_value") or 0.0)
            if margin > 0:
                out["profit_pct_on_margin"] = round(net / margin * 100.0, 4)
            else:
                out["profit_pct_on_margin"] = 0.0
            if value > 0:
                out["profit_pct_on_notional"] = round(net / value * 100.0, 4)
            else:
                out["profit_pct_on_notional"] = 0.0
    except Exception:
        pass
    return out


def _virtual_trades_payload(strategy_id: int, *, leverage: float, market_type: str) -> dict:
    from app.services.virtual_trading import list_virtual_trades
    from app.utils.trade_net_pnl import enrich_trades_net_pnl
    from app.utils.trade_close_reason import is_exit_trade_type

    ledger_rows = []
    opening_commission = 0.0
    closing_commission = 0.0
    total_slippage = 0.0
    gross_realized = 0.0
    for raw in list_virtual_trades(strategy_id):
        trade = dict(raw)
        created_at = trade.get("created_at")
        if hasattr(created_at, "timestamp"):
            dt = created_at
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            trade["created_at"] = int(dt.timestamp())
        fee = float(trade.get("commission_quote") or trade.get("commission") or 0.0)
        slippage = float(trade.get("slippage_quote") or 0.0)
        reference_price = float(trade.get("reference_price") or 0.0)
        fill_price = float(trade.get("price") or 0.0)
        if reference_price > 0:
            trade["price_deviation_pct"] = round(
                (fill_price - reference_price) / reference_price * 100.0,
                8,
            )
        gross = float(trade.get("profit") or 0.0)
        exit_trade = is_exit_trade_type(str(trade.get("type") or ""))
        opening_commission += 0.0 if exit_trade else fee
        closing_commission += fee if exit_trade else 0.0
        gross_realized += gross if exit_trade else 0.0
        total_slippage += slippage
        trade.update({
            "profit": gross if exit_trade else None,
            "profit_gross": gross if exit_trade else None,
            "net_pnl": None,
            "open_commission_allocated": 0.0,
            "close_commission": fee if exit_trade else 0.0,
            "total_commission": fee,
            "pnl_source": "virtual_account",
            "pnl_status": "complete",
            "fee_status": "actual",
            "fee_source": "virtual_simulation",
            "liquidity_role": "taker",
        })
        ledger_rows.append(trade)
    enrich_trades_net_pnl(ledger_rows)
    processed_rows = [
        _normalize_trade_row_for_api(trade, leverage=leverage, market_type=market_type)
        for trade in ledger_rows
    ]
    trading_fees = opening_commission + closing_commission
    return {
        "trades": processed_rows,
        "items": processed_rows,
        "ledger_mode": "virtual",
        "cost_summary": {
            "gross_realized_pnl": round(gross_realized, 8),
            "opening_commission": round(opening_commission, 8),
            "closing_commission": round(closing_commission, 8),
            "trading_commission": round(trading_fees, 8),
            "slippage_cost": round(total_slippage, 8),
            "funding_payment": 0.0,
            "funding_cost": 0.0,
            "broker_activity_payment": 0.0,
            "regulatory_payment": 0.0,
            "adr_payment": 0.0,
            "margin_interest_payment": 0.0,
            "other_broker_payment": 0.0,
            "broker_activity_applicable": False,
            "net_realized_pnl": round(gross_realized - trading_fees, 8),
            "pnl_pending_count": 0,
            "pnl_status": "complete",
        },
    }


@strategy_blp.route('/strategies/trades', methods=['GET'])
@login_required
def get_trades():
    """Get trade records for the current user's strategy."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': {'trades': [], 'items': []}}), 400

        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': {'trades': [], 'items': []}}), 404

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

        if str(st.get("execution_mode") or "").strip().lower() == "signal":
            return jsonify({
                "code": 1,
                "msg": "success",
                "data": _virtual_trades_payload(
                    strategy_id,
                    leverage=leverage,
                    market_type=market_type,
                ),
            })

        from app.services.live_trading.funding_reconciliation import (
            load_strategy_funding_summary,
        )
        from app.services.live_trading.alpaca_activity_reconciliation import (
            is_alpaca_strategy,
            load_strategy_broker_activity_summary,
        )

        bot_type = str(trading_config.get("bot_type") or "").strip().lower()
        lang = str(request.args.get("lang") or request.headers.get("Accept-Language") or "zh")[:2].lower()
        if not lang.startswith("zh"):
            lang = "en"
        else:
            lang = "zh"

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT t.id, t.strategy_id, t.symbol, t.type, t.price, t.amount, t.value,
                       t.commission, t.commission_ccy, t.commission_quote, t.profit, t.close_reason,
                       t.matched_entry_price, t.grid_matched_profit, t.fee_status, t.fee_source, t.created_at,
                       t.fill_source, t.exchange_order_id, t.exchange_fill_id,
                       t.credential_id, t.market_type, t.strategy_run_id, t.grid_order_id, t.commission_breakdown,
                       i.client_order_id AS grid_client_reference,
                       c.exchange_id,
                       r.purpose AS grid_order_purpose, r.extra AS grid_order_extra,
                       p.price AS request_price, p.payload_json AS request_payload,
                       r.price AS grid_request_price
                FROM qd_strategy_trades t
                LEFT JOIN qd_exchange_credentials c ON c.id = t.credential_id AND c.user_id = t.user_id
                LEFT JOIN pending_orders p ON p.id = t.pending_order_id AND p.strategy_id = t.strategy_id
                LEFT JOIN strategy_order_intents i ON i.id = t.order_intent_id AND i.strategy_id = t.strategy_id
                LEFT JOIN qd_grid_resting_orders r ON r.id = t.grid_order_id AND r.strategy_id = t.strategy_id
                    AND r.exchange_order_id = t.exchange_order_id AND t.exchange_order_id <> ''
                WHERE t.strategy_id = ?
                ORDER BY t.id DESC
                """,
                (strategy_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        from app.utils.trade_close_reason import enrich_trade_row
        from app.utils.trade_net_pnl import enrich_trades_net_pnl
        from app.utils.trade_execution import enrich_execution_reference
        processed_rows = []
        for row in rows:
            trade = enrich_execution_reference(row)
            created_at = trade.get('created_at')
            if created_at:
                if hasattr(created_at, 'timestamp'):
                    dt = created_at
                    if getattr(dt, 'tzinfo', None) is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    trade['created_at'] = int(dt.timestamp())
                elif isinstance(created_at, str):
                    try:
                        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        if getattr(dt, 'tzinfo', None) is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        trade['created_at'] = int(dt.timestamp())
                    except Exception:
                        pass

            trade = enrich_trade_row(trade, bot_type=bot_type, lang=lang)
            processed_rows.append(trade)

        enrich_trades_net_pnl(processed_rows)
        from app.services.grid.order_pnl import enrich_grid_order_pnl, execution_fee
        enrich_grid_order_pnl(processed_rows)
        from app.services.execution_streams.pnl_reconciliation import enrich_reported_order_pnl
        try:
            enrich_reported_order_pnl(processed_rows, user_id=user_id, trading_config=trading_config)
        except Exception:
            logger.warning("Reported P&L unavailable for strategy=%s", strategy_id, exc_info=True)
        processed_rows = [
            _normalize_trade_row_for_api(trade, leverage=leverage, market_type=market_type)
            for trade in processed_rows
        ]

        from app.utils.trade_close_reason import is_exit_trade_type
        paired_grid = any(t.get("pnl_source") == "grid_exchange_order_pairs" for t in processed_rows)
        opening_commission = 0.0
        closing_commission = 0.0
        gross_realized = 0.0
        for trade in processed_rows:
            fee = float(execution_fee(trade) or 0) if paired_grid else float(
                trade.get("commission_quote") if trade.get("commission_quote") is not None else trade.get("commission") or 0.0
            )
            if is_exit_trade_type(str(trade.get("type") or "")):
                closing_commission += fee
                if trade.get("profit_gross") is not None:
                    gross_realized += float(trade.get("profit_gross") or 0.0)
            else:
                opening_commission += fee
        funding = load_strategy_funding_summary(strategy_id)
        funding_payment = float(funding.get("funding_payment") or 0.0)
        broker = load_strategy_broker_activity_summary(strategy_id)
        broker_payment = float(broker.get("broker_activity_payment") or 0.0)
        trading_fees = opening_commission + closing_commission
        net_realized = gross_realized - trading_fees + funding_payment + broker_payment
        pending_fees = [
            trade for trade in processed_rows
            if str(trade.get("fee_status") or "").strip().lower() == "pending"
            or (paired_grid and execution_fee(trade) is None)
        ]
        pending_fee_ids = {int(trade["id"]) for trade in pending_fees}
        pnl_pending = len({int(t["id"]) for t in processed_rows
                           if t.get("pnl_status") in {"unmatched", "fees_pending"}
                           or int(t["id"]) in pending_fee_ids})
        cost_summary = {
            "gross_realized_pnl": round(gross_realized, 8) if not any(t.get("pnl_status") == "unmatched" for t in processed_rows) else None,
            "opening_commission": round(opening_commission, 8) if not any(not is_exit_trade_type(t["type"]) for t in pending_fees) else None,
            "closing_commission": round(closing_commission, 8) if not any(is_exit_trade_type(t["type"]) for t in pending_fees) else None,
            "trading_commission": round(trading_fees, 8) if not pending_fees else None,
            "funding_payment": round(funding_payment, 8),
            "funding_cost": round(-funding_payment, 8),
            "broker_activity_payment": round(broker_payment, 8),
            "regulatory_payment": round(float(broker.get("regulatory_payment") or 0.0), 8),
            "adr_payment": round(float(broker.get("adr_payment") or 0.0), 8),
            "margin_interest_payment": round(float(broker.get("margin_interest_payment") or 0.0), 8),
            "other_broker_payment": round(float(broker.get("other_broker_payment") or 0.0), 8),
            "broker_activity_applicable": is_alpaca_strategy(strategy_id, user_id=user_id),
            "net_realized_pnl": round(net_realized, 8) if not pnl_pending else None,
            "pnl_pending_count": pnl_pending,
            "pnl_status": "pending" if pnl_pending else "complete",
        }

        return jsonify({'code': 1, 'msg': 'success', 'data': {
            'trades': processed_rows, 'items': processed_rows, 'cost_summary': cost_summary,
        }})
    except Exception as e:
        logger.error(f"get_trades failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'trades': [], 'items': []}}), 500


def _trade_row_timestamp(row: dict) -> int:
    created_at = row.get("created_at")
    if created_at and hasattr(created_at, "timestamp"):
        dt = created_at
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    if created_at:
        try:
            return int(created_at)
        except Exception:
            pass
    return int(time.time())


def _strategy_performance_summary(initial: float, curve: list) -> dict:
    """Unified KPI math for strategy detail header + performance tab."""
    init = float(initial or 0.0)
    if init <= 0:
        init = 1000.0
    latest = float(curve[-1].get("equity") or init) if curve else init
    total_return = latest - init
    total_return_pct = (total_return / init * 100.0) if init > 0 else 0.0

    peak = init
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    for pt in curve or []:
        eq = float(pt.get("equity") or 0.0)
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_drawdown:
            max_drawdown = dd
        if peak > 0:
            dd_pct = dd / peak * 100.0
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct

    return {
        "initial_equity": round(init, 2),
        "latest_equity": round(latest, 2),
        "total_return": round(total_return, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
    }


def _build_strategy_equity_curve(user_id: int, strategy_id: int):
    st = get_strategy_service().get_strategy(strategy_id, user_id=user_id) or {}
    if not st:
        return None, 'Strategy not found'

    initial = float(st.get('initial_capital') or (st.get('trading_config') or {}).get('initial_capital') or 0)
    if initial <= 0:
        initial = 1000.0

    if str(st.get("execution_mode") or "signal").strip().lower() == "signal":
        from app.services.virtual_trading import build_virtual_equity_curve

        return build_virtual_equity_curve(strategy_id, initial), None

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT created_at, profit, commission, commission_quote, 'trade' AS event_type
            FROM qd_strategy_trades
            WHERE strategy_id = ?
            UNION ALL
            SELECT occurred_at AS created_at, amount AS profit, 0 AS commission,
                   0 AS commission_quote, 'funding' AS event_type
            FROM qd_strategy_funding_fees
            WHERE strategy_id = ?
            UNION ALL
            SELECT occurred_at AS created_at, amount AS profit, 0 AS commission,
                   0 AS commission_quote, 'broker_activity' AS event_type
            FROM qd_strategy_broker_activities
            WHERE strategy_id = ?
            ORDER BY created_at ASC
            """,
            (strategy_id, strategy_id, strategy_id)
        )
        rows = cur.fetchall() or []
        cur.execute(
            """
            SELECT COALESCE(SUM(unrealized_pnl), 0) AS u
            FROM qd_strategy_positions
            WHERE strategy_id = ?
            """,
            (strategy_id,),
        )
        prow = cur.fetchone() or {}
        cur.close()

    equity = initial
    curve = []
    if rows:
        anchor_ts = _trade_row_timestamp(rows[0])
        curve.append({"time": anchor_ts, "equity": round(initial, 2)})

    from app.utils.trade_net_pnl import enrich_trades_net_pnl, net_pnl_for_equity_step

    trade_rows = [dict(r) for r in rows if str(r.get("event_type") or "trade") == "trade"]
    enrich_trades_net_pnl(trade_rows)
    events = []
    for r in trade_rows:
        try:
            delta = float(net_pnl_for_equity_step(r))
        except Exception:
            delta = 0.0
        events.append((_trade_row_timestamp(r), delta))

    funding_rows = [dict(r) for r in rows if str(r.get("event_type") or "") == "funding"]
    broker_rows = [dict(r) for r in rows if str(r.get("event_type") or "") == "broker_activity"]
    for r in funding_rows + broker_rows:
        try:
            delta = float(r.get("profit") or 0.0)
        except Exception:
            delta = 0.0
        events.append((_trade_row_timestamp(r), delta))
    for event_time, delta in sorted(events, key=lambda item: item[0]):
        equity += delta
        curve.append({'time': event_time, 'equity': round(equity, 2)})

    try:
        unreal = float(prow.get('u') or prow.get('U') or 0)
    except Exception:
        unreal = 0.0
    live_equity = float(equity) + unreal
    now_ts = int(time.time())
    if abs(unreal) > 1e-12 or not curve:
        curve.append({'time': now_ts, 'equity': round(live_equity, 2)})

    return curve, None


@strategy_blp.route('/strategies/equityCurve', methods=['GET'])
@login_required
def get_equity_curve():
    """Get equity curve for the current user's strategy."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': []}), 400

        curve, error = _build_strategy_equity_curve(user_id, strategy_id)
        if error:
            return jsonify({'code': 0, 'msg': error, 'data': []}), 404

        return jsonify({'code': 1, 'msg': 'success', 'data': curve})
    except Exception as e:
        logger.error(f"get_equity_curve failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500


@strategy_blp.route('/strategies/performance', methods=['GET'])
@login_required
def get_strategy_performance():
    """Get strategy performance metrics."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Strategy ID required'})

        equity_data, error = _build_strategy_equity_curve(user_id, strategy_id)
        if error:
            return jsonify({'code': 0, 'msg': error, 'data': None}), 404

        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id) or {}
        initial = float(st.get('initial_capital') or (st.get('trading_config') or {}).get('initial_capital') or 0)
        summary = _strategy_performance_summary(initial, equity_data)
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'equity_curve': equity_data,
                'latest_equity': summary['latest_equity'],
                'initial_equity': summary['initial_equity'],
                'total_return': summary['total_return'],
                'total_return_pct': summary['total_return_pct'],
                'max_drawdown': summary['max_drawdown'],
                'max_drawdown_pct': summary['max_drawdown_pct'],
                'points': len(equity_data),
            }
        })
    except Exception as e:
        logger.error(f"get_strategy_performance failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e)}), 500
