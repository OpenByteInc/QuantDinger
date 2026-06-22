"""Strategy backtest route facade."""
from datetime import datetime
import traceback

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.routes.strategy_services import get_backtest_service, get_strategy_service
from app.services.backtest_limits import validate_backtest_range
from app.services.script_source import get_script_source_service
from app.services.strategy_snapshot import StrategySnapshotResolver
from app.utils.auth import login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)


def _build_script_source_strategy(source: dict, script_source_id: int, override_config: dict) -> dict:
    override = override_config if isinstance(override_config, dict) else {}
    metadata = source.get('metadata') or {}
    last_run_config = metadata.get('last_run_config') or {}
    market = str(
        override.get('market')
        or override.get('market_category')
        or last_run_config.get('market_category')
        or 'Crypto'
    ).strip() or 'Crypto'
    return {
        'id': None,
        'strategy_name': override.get('strategy_name') or source.get('name') or f'Script Source #{script_source_id}',
        'strategy_type': 'ScriptStrategy',
        'strategy_mode': 'script',
        'strategy_code': '',
        'market_category': market,
        'status': 'draft',
        'trading_config': {
            **override,
            'market_category': market,
            'script_source_id': script_source_id,
        },
    }


@strategy_blp.route('/strategies/backtest', methods=['POST'])
@login_required
def run_strategy_backtest():
    try:
        payload = request.get_json() or {}
        user_id = g.user_id
        strategy_id = int(payload.get('strategyId') or 0)
        script_source_id = int(payload.get('scriptSourceId') or payload.get('sourceId') or 0)
        if not strategy_id and not script_source_id:
            return jsonify({'code': 0, 'msg': 'strategyId or scriptSourceId is required', 'data': None}), 400

        start_date_str = str(payload.get('startDate') or '').strip()
        end_date_str = str(payload.get('endDate') or '').strip()
        if not start_date_str or not end_date_str:
            return jsonify({'code': 0, 'msg': 'startDate and endDate are required', 'data': None}), 400

        override_config = payload.get('overrideConfig') or {}
        if not isinstance(override_config, dict):
            override_config = {}
        if strategy_id:
            strategy = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
            if not strategy:
                return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404
        else:
            source = get_script_source_service().get_source(script_source_id, user_id=user_id)
            if not source:
                return jsonify({'code': 0, 'msg': 'Script source not found', 'data': None}), 404
            strategy = _build_script_source_strategy(source, script_source_id, override_config)

        resolver = StrategySnapshotResolver(user_id=user_id)
        snapshot = resolver.resolve(strategy, override_config)
        snapshot['user_id'] = user_id

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)

        timeframe = snapshot.get('timeframe') or '1D'
        svc = get_backtest_service()
        warmup_bars = svc._estimate_warmup_bars(
            snapshot.get('code') or '',
            (snapshot.get('strategy_config') or {}).get('indicator_params')
            if isinstance(snapshot.get('strategy_config'), dict)
            else None,
        )
        range_error = validate_backtest_range(
            market=snapshot.get('market') or '',
            symbol=snapshot.get('symbol') or '',
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=warmup_bars,
        )
        if range_error:
            return jsonify({
                'code': 0,
                'msg': range_error['msg'],
                'data': range_error
            }), 400

        result = svc.run_strategy_snapshot(snapshot, start_date=start_date, end_date=end_date)
        ea = dict(result.get('executionAssumptions') or {})
        ea['commission'] = round(float(snapshot.get('commission') or 0), 6)
        ea['slippage'] = round(float(snapshot.get('slippage') or 0), 6)
        ea['strictMode'] = bool(snapshot.get('strict_mode', True))
        result['executionAssumptions'] = ea
        run_id = svc.persist_run(
            user_id=user_id,
            indicator_id=snapshot.get('indicator_id'),
            strategy_id=snapshot.get('strategy_id'),
            strategy_name=snapshot.get('strategy_name') or '',
            run_type=snapshot.get('run_type') or 'strategy_indicator',
            market=snapshot.get('market') or '',
            symbol=snapshot.get('symbol') or '',
            timeframe=snapshot.get('timeframe') or '',
            start_date_str=start_date_str,
            end_date_str=end_date_str,
            initial_capital=float(snapshot.get('initial_capital') or 0),
            commission=float(snapshot.get('commission') or 0),
            slippage=float(snapshot.get('slippage') or 0),
            leverage=int(snapshot.get('leverage') or 1),
            trade_direction=str(snapshot.get('trade_direction') or 'long'),
            strategy_config=snapshot.get('strategy_config') or {},
            config_snapshot=snapshot.get('config_snapshot') or {},
            status='success',
            error_message='',
            result=result,
            code=snapshot.get('code') or '',
        )
        if strategy_id:
            try:
                get_strategy_service().patch_trading_config(
                    strategy_id,
                    {
                        'lifecycle_backtested_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                    },
                    user_id=user_id,
                )
            except Exception as _lc_err:
                logger.warning(f"lifecycle_backtested patch skipped: {_lc_err}")
        return jsonify({'code': 1, 'msg': 'success', 'data': {'runId': run_id, 'result': result}})
    except ValueError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
    except Exception as e:
        logger.error(f"run_strategy_backtest failed: {str(e)}")
        logger.error(traceback.format_exc())
        try:
            payload = payload if isinstance(payload, dict) else {}
            strategy_id = int(payload.get('strategyId') or 0)
            script_source_id = int(payload.get('scriptSourceId') or payload.get('sourceId') or 0)
            override_config = payload.get('overrideConfig') or {}
            if not isinstance(override_config, dict):
                override_config = {}
            strategy = get_strategy_service().get_strategy(strategy_id, user_id=g.user_id) if strategy_id else None
            if not strategy and script_source_id:
                source = get_script_source_service().get_source(script_source_id, user_id=g.user_id)
                if source:
                    strategy = _build_script_source_strategy(source, script_source_id, override_config)
            if strategy:
                resolver = StrategySnapshotResolver(user_id=g.user_id)
                snapshot = resolver.resolve(strategy, override_config)
                snapshot['user_id'] = g.user_id
                get_backtest_service().persist_run(
                    user_id=g.user_id,
                    indicator_id=snapshot.get('indicator_id'),
                    strategy_id=snapshot.get('strategy_id'),
                    strategy_name=snapshot.get('strategy_name') or '',
                    run_type=snapshot.get('run_type') or 'strategy_indicator',
                    market=snapshot.get('market') or '',
                    symbol=snapshot.get('symbol') or '',
                    timeframe=snapshot.get('timeframe') or '',
                    start_date_str=str(payload.get('startDate') or ''),
                    end_date_str=str(payload.get('endDate') or ''),
                    initial_capital=float(snapshot.get('initial_capital') or 0),
                    commission=float(snapshot.get('commission') or 0),
                    slippage=float(snapshot.get('slippage') or 0),
                    leverage=int(snapshot.get('leverage') or 1),
                    trade_direction=str(snapshot.get('trade_direction') or 'long'),
                    strategy_config=snapshot.get('strategy_config') or {},
                    config_snapshot=snapshot.get('config_snapshot') or {},
                    status='failed',
                    error_message=str(e),
                    result=None,
                    code=snapshot.get('code') or '',
                )
        except Exception:
            pass
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/backtest/history', methods=['GET'])
@login_required
def get_strategy_backtest_history():
    try:
        user_id = g.user_id
        strategy_id = int(request.args.get('strategyId') or request.args.get('id') or 0)
        script_source_id = int(request.args.get('scriptSourceId') or request.args.get('sourceId') or 0)
        if not strategy_id and not script_source_id:
            return jsonify({'code': 0, 'msg': 'strategyId or scriptSourceId is required', 'data': None}), 400
        limit = max(1, min(int(request.args.get('limit') or 50), 200))
        offset = max(0, int(request.args.get('offset') or 0))
        symbol = (request.args.get('symbol') or '').strip()
        market = (request.args.get('market') or '').strip()
        timeframe = (request.args.get('timeframe') or '').strip()
        if script_source_id and not strategy_id:
            # Script source backtests intentionally do not create rows in
            # qd_strategies_trading. The durable identity lives in
            # config_snapshot.strategyMeta.scriptSourceId.
            candidate_rows = get_backtest_service().list_runs(
                user_id=user_id,
                run_type='strategy_script',
                limit=max(limit + offset, 200),
                offset=0,
                symbol=symbol,
                market=market,
                timeframe=timeframe,
            )
            rows = []
            for row in candidate_rows:
                cfg = row.get('config_snapshot') or {}
                meta = cfg.get('strategyMeta') or {}
                signal = cfg.get('signalConfig') or {}
                meta_source_id = meta.get('scriptSourceId') or signal.get('scriptSourceId')
                if str(meta_source_id or '') == str(script_source_id):
                    rows.append(row)
            rows = rows[offset:offset + limit]
        else:
            rows = get_backtest_service().list_runs(
                user_id=user_id,
                strategy_id=strategy_id,
                limit=limit,
                offset=offset,
                symbol=symbol,
                market=market,
                timeframe=timeframe,
            )
            rows = [r for r in rows if str(r.get('run_type') or '').startswith('strategy_')]
        return jsonify({'code': 1, 'msg': 'success', 'data': rows})
    except Exception as e:
        logger.error(f"get_strategy_backtest_history failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/backtest/get', methods=['GET'])
@login_required
def get_strategy_backtest_run():
    try:
        user_id = g.user_id
        run_id = int(request.args.get('runId') or 0)
        if not run_id:
            return jsonify({'code': 0, 'msg': 'runId is required', 'data': None}), 400
        row = get_backtest_service().get_run(user_id=user_id, run_id=run_id)
        if not row or not str(row.get('run_type') or '').startswith('strategy_'):
            return jsonify({'code': 0, 'msg': 'run not found', 'data': None}), 404
        return jsonify({'code': 1, 'msg': 'success', 'data': row})
    except Exception as e:
        logger.error(f"get_strategy_backtest_run failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
