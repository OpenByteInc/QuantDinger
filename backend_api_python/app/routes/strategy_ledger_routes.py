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
    for k in (
        "price",
        "amount",
        "value",
        "commission",
        "profit",
        "profit_gross",
        "net_pnl",
        "open_commission_allocated",
        "close_commission",
        "total_commission",
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

        from app.services.live_trading.records import ensure_strategy_trades_close_reason_column
        ensure_strategy_trades_close_reason_column()

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
                SELECT id, strategy_id, symbol, type, price, amount, value,
                       commission, commission_ccy, profit, close_reason,
                       matched_entry_price, grid_matched_profit, created_at
                FROM qd_strategy_trades
                WHERE strategy_id = ?
                ORDER BY id DESC
                """,
                (strategy_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        from app.utils.trade_close_reason import enrich_trade_row
        from app.utils.trade_net_pnl import enrich_trades_net_pnl
        processed_rows = []
        for row in rows:
            trade = dict(row)
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
        processed_rows = [
            _normalize_trade_row_for_api(trade, leverage=leverage, market_type=market_type)
            for trade in processed_rows
        ]

        return jsonify({'code': 1, 'msg': 'success', 'data': {'trades': processed_rows, 'items': processed_rows}})
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

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT created_at, profit, commission
            FROM qd_strategy_trades
            WHERE strategy_id = ?
            ORDER BY created_at ASC
            """,
            (strategy_id,)
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

    trade_rows = [dict(r) for r in rows]
    enrich_trades_net_pnl(trade_rows)
    for r in trade_rows:
        try:
            equity += float(net_pnl_for_equity_step(r))
        except Exception:
            pass
        curve.append({'time': _trade_row_timestamp(r), 'equity': round(equity, 2)})

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
