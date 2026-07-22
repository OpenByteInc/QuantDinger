from typing import Any, Dict, Optional, Tuple

from app.routes.strategy_services import get_strategy_service
from app.services.strategy_direction import (
    direction_mode_allows,
    direction_mode_from_manifest,
    direction_mode_owned_legs,
    direction_mode_position_side,
    infer_direction_mode_from_code,
    normalize_direction_mode,
)
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


class StrategyDirectionModeViolation(RuntimeError):
    """A strategy requested a direction outside its declared live capability."""


def normalize_strategy_position_side(value: Any) -> str:
    side = str(value or "").strip().lower()
    if side in {"neutral", "hedged", "both", "dual"}:
        return "neutral"
    if side in {"long", "buy", "1", "+1"}:
        return "long"
    if side in {"short", "sell", "-1"}:
        return "short"
    return ""


def resolve_strategy_position_side(strategy: Dict[str, Any]) -> str:
    """Resolve the position leg or dual-leg ownership of a live strategy."""
    return direction_mode_position_side(resolve_strategy_direction_mode(strategy))


def resolve_strategy_direction_mode(strategy: Dict[str, Any]) -> str:
    """Resolve the declared trading-direction capability of a live strategy."""
    trading_config = strategy.get("trading_config") if isinstance(strategy.get("trading_config"), dict) else {}
    market_type = str(
        trading_config.get("market_type")
        or strategy.get("market_type")
        or "swap"
    ).strip().lower()
    if market_type == "spot":
        return "long_only"

    executor_config = trading_config.get("executor_config") if isinstance(trading_config.get("executor_config"), dict) else {}
    manifest = trading_config.get("strategy_manifest") if isinstance(trading_config.get("strategy_manifest"), dict) else {}
    metadata_fields = (
        manifest.get("metadata")
        or manifest.get("metadataFields")
        or manifest.get("metadata_fields")
        or {}
    )
    if not isinstance(metadata_fields, dict):
        metadata_fields = {}
    bot_params = trading_config.get("bot_params") if isinstance(trading_config.get("bot_params"), dict) else {}

    candidates = (
        strategy.get("direction_mode"),
        trading_config.get("direction_mode"),
        direction_mode_from_manifest(manifest),
        strategy.get("position_side"),
        strategy.get("trade_direction"),
        trading_config.get("position_side"),
        trading_config.get("trade_direction"),
        trading_config.get("direction"),
        trading_config.get("side"),
        executor_config.get("side"),
        bot_params.get("side"),
        bot_params.get("grid_direction"),
        metadata_fields.get("position_side"),
        metadata_fields.get("trade_direction"),
        metadata_fields.get("direction"),
        metadata_fields.get("side"),
    )
    for value in candidates:
        mode = normalize_direction_mode(value)
        if mode:
            return mode

    source_id = int(trading_config.get("script_source_id") or 0)
    if source_id <= 0:
        return ""
    try:
        from app.services.script_source import get_script_source_service

        source = get_script_source_service().get_source(
            source_id,
            user_id=int(strategy.get("user_id") or 0),
        )
        code = str((source or {}).get("code") or "")
        mode = infer_direction_mode_from_code(code)
        if mode:
            return mode
    except Exception as exc:
        logger.debug("strategy direction discovery failed for %s: %s", strategy.get("id"), exc)
    return ""


def validate_strategy_signal_direction(strategy: Dict[str, Any], signal_type: Any) -> None:
    """Reject a signal that exceeds the strategy's declared capability."""
    signal = str(signal_type or "").strip().lower()
    if signal.startswith(("close_", "reduce_")):
        return
    side = "short" if "short" in signal else ("long" if "long" in signal else "")
    mode = resolve_strategy_direction_mode(strategy)
    if mode and side and not direction_mode_allows(mode, side):
        raise StrategyDirectionModeViolation(
            f"strategyV2.directionModeViolation:{mode}:{side}"
        )


def strategy_live_lock_key(strategy: Dict[str, Any], user_id: int) -> Optional[Tuple[Any, ...]]:
    """Return the account/symbol/leg key that cannot run twice for live strategies."""
    execution_mode = str(strategy.get("execution_mode") or "signal").strip().lower()
    if execution_mode != "live":
        return None

    trading_config = strategy.get("trading_config") if isinstance(strategy.get("trading_config"), dict) else {}
    exchange_config = strategy.get("exchange_config") if isinstance(strategy.get("exchange_config"), dict) else {}

    try:
        from app.services.exchange_execution import resolve_exchange_config
        from app.services.live_trading.leg_context import credential_id_from_exchange_config
        from app.services.live_trading.records import normalize_strategy_symbol

        resolved_exchange = resolve_exchange_config(exchange_config, user_id=int(user_id or strategy.get("user_id") or 1))
        exchange_id = str(
            resolved_exchange.get("exchange_id")
            or exchange_config.get("exchange_id")
            or ""
        ).strip().lower()
        if not exchange_id:
            return None

        credential_id = int(
            credential_id_from_exchange_config(resolved_exchange)
            or credential_id_from_exchange_config(exchange_config)
            or 0
        )
        credential_key: Any = credential_id if credential_id > 0 else f"inline:{exchange_id}"

        market_type = str(
            trading_config.get("market_type")
            or strategy.get("market_type")
            or resolved_exchange.get("market_type")
            or "swap"
        ).strip().lower()
        if market_type in ("futures", "future", "perp", "perpetual"):
            market_type = "swap"

        symbol = strategy.get("symbol") or trading_config.get("symbol") or ""
        symbol = normalize_strategy_symbol(str(symbol or "").strip()).upper()
        if not symbol:
            return None

        position_side = resolve_strategy_position_side(strategy)
        return (
            int(user_id or strategy.get("user_id") or 0),
            credential_key,
            exchange_id,
            market_type,
            symbol,
            position_side or "unknown",
        )
    except Exception as exc:
        logger.warning("strategy live lock key failed for strategy %s: %s", strategy.get("id"), exc)
        return None


def find_live_strategy_conflict(
    strategy: Dict[str, Any],
    user_id: int,
    *,
    allow_opposite_leg: bool = True,
) -> Optional[Dict[str, Any]]:
    """Find another running live strategy owning the same account instrument."""
    key = strategy_live_lock_key(strategy, user_id)
    if not key:
        return None

    strategy_id = int(strategy.get("id") or 0)
    requested_mode = resolve_strategy_direction_mode(strategy)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id
            FROM qd_strategies_trading
            WHERE user_id = ? AND status = 'running' AND execution_mode = 'live' AND id <> ?
            """,
            (int(user_id), strategy_id),
        )
        rows = cur.fetchall() or []
        cur.close()

    service = get_strategy_service()
    for row in rows:
        other_id = int(row.get("id") or 0)
        other = service.get_strategy(other_id, user_id=user_id)
        if not other:
            continue
        other_key = strategy_live_lock_key(other, user_id)
        if not other_key or other_key[:-1] != key[:-1]:
            continue
        other_mode = resolve_strategy_direction_mode(other)
        if (
            not allow_opposite_leg
            or bool(
                direction_mode_owned_legs(requested_mode)
                & direction_mode_owned_legs(other_mode)
            )
        ):
            return {
                "strategy_id": other_id,
                "strategy_name": other.get("strategy_name") or other.get("name") or str(other_id),
                "symbol": key[-2],
                "market_type": key[-3],
                "exchange_id": key[-4],
                "position_side": str(key[-1] or "unknown"),
            }
    return None


def live_conflict_message(conflict: Dict[str, Any]) -> str:
    return f"strategyV2.liveLegConflict:{int(conflict.get('strategy_id') or 0)}"
