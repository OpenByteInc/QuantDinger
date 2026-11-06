"""Durable routing index from market events to strategy shards."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from app.events.bar_clock import BarStreamKey, supports_bar_close_events
from app.events.protocol import MarketBarClosedV1
from app.utils.db import get_db_connection


def strategy_shard(strategy_id: int, shard_count: int | None = None) -> int:
    configured = shard_count
    if configured is None:
        try:
            configured = int(os.getenv("STRATEGY_SHARD_COUNT", "128"))
        except (TypeError, ValueError):
            configured = 128
    return int(strategy_id) % max(1, int(configured))


class StrategyEventSubscriptionRepository:
    def active_bar_routes(self) -> list[dict[str, str]]:
        """Return distinct bar routes still referenced by running strategies."""
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    SELECT DISTINCT subscription.partition_key, subscription.timeframe
                    FROM qd_strategy_event_subscriptions AS subscription
                    JOIN qd_strategies_trading AS strategy
                      ON strategy.id = subscription.strategy_id
                    WHERE subscription.event_type = %s
                      AND strategy.status = 'running'
                    ORDER BY subscription.partition_key
                    """,
                    (MarketBarClosedV1.event_type,),
                )
                rows: list[dict[str, Any]] = cur.fetchall() or []
            finally:
                cur.close()
        return [
            {
                "partition_key": str(row.get("partition_key") or ""),
                "timeframe": str(row.get("timeframe") or ""),
            }
            for row in rows
            if str(row.get("partition_key") or "").strip()
        ]

    def replace_bar_streams(
        self,
        strategy_id: int,
        streams: Iterable[BarStreamKey],
    ) -> int:
        sid = int(strategy_id)
        rows = sorted({(stream.partition_key, stream.timeframe) for stream in streams})
        shard = strategy_shard(sid)
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    "DELETE FROM qd_strategy_event_subscriptions WHERE strategy_id = %s",
                    (sid,),
                )
                if rows:
                    cur.executemany(
                        """
                        INSERT INTO qd_strategy_event_subscriptions
                            (strategy_id, event_type, partition_key, timeframe, strategy_shard)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (strategy_id, event_type, partition_key) DO UPDATE
                        SET timeframe = EXCLUDED.timeframe,
                            strategy_shard = EXCLUDED.strategy_shard,
                            updated_at = NOW()
                        """,
                        [
                            (sid, MarketBarClosedV1.event_type, partition_key, timeframe, shard)
                            for partition_key, timeframe in rows
                        ],
                    )
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                cur.close()
        return len(rows)

    def remove_strategy(self, strategy_id: int) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    "DELETE FROM qd_strategy_event_subscriptions WHERE strategy_id = %s",
                    (int(strategy_id),),
                )
                db.commit()
            finally:
                cur.close()

    def strategy_ids_for_event(self, event_type: str, partition_key: str) -> list[int]:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    SELECT subscription.strategy_id
                    FROM qd_strategy_event_subscriptions AS subscription
                    JOIN qd_strategies_trading AS strategy
                      ON strategy.id = subscription.strategy_id
                    WHERE subscription.event_type = %s
                      AND subscription.partition_key = %s
                      AND strategy.status = 'running'
                    ORDER BY subscription.strategy_shard, subscription.strategy_id
                    """,
                    (str(event_type), str(partition_key)),
                )
                rows: list[dict[str, Any]] = cur.fetchall() or []
            finally:
                cur.close()
        return [int(row["strategy_id"]) for row in rows]

    def running_strategy_ids(self, strategy_ids: Iterable[int]) -> list[int]:
        normalized = sorted({int(strategy_id) for strategy_id in strategy_ids})
        if not normalized:
            return []
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    SELECT id
                    FROM qd_strategies_trading
                    WHERE id = ANY(%s) AND status = 'running'
                    ORDER BY id
                    """,
                    (normalized,),
                )
                rows: list[dict[str, Any]] = cur.fetchall() or []
            finally:
                cur.close()
        return [int(row["id"]) for row in rows]


class StrategyEventSubscriptionPlanner:
    def __init__(
        self,
        repository: StrategyEventSubscriptionRepository | None = None,
    ) -> None:
        self.repository = repository or StrategyEventSubscriptionRepository()

    def register(self, strategy_id: int) -> set[BarStreamKey]:
        streams = self.plan(strategy_id)
        self.repository.replace_bar_streams(strategy_id, streams)
        return streams

    @staticmethod
    def plan(strategy_id: int) -> set[BarStreamKey]:
        from datetime import datetime, timezone

        from app.services.exchange_execution import resolve_exchange_config
        from app.services.pending_orders.live_order_support import (
            attach_instrument_product_contracts,
        )
        from app.services.strategy import StrategyService
        from app.services.strategy_runtime.bot_type import resolve_bot_type
        from app.services.strategy_runtime.timeframes import (
            daily_equity_execution_policy,
        )
        from app.services.strategy_v2 import compile_strategy_v2
        from app.services.strategy_v2.service import StrategyV2BacktestService
        from app.services.trading_executor import TradingExecutor, _json_object

        strategy = StrategyService().get_strategy(int(strategy_id))
        if not strategy or str(strategy.get("status") or "").lower() != "running":
            return set()
        _source_version_id, code = TradingExecutor._load_source(strategy)
        program = compile_strategy_v2(code)
        user_id = int(strategy.get("user_id") or 0)
        trading_config = _json_object(strategy.get("trading_config"))
        execution_mode = str(strategy.get("execution_mode") or "signal").lower()
        exchange_config = _json_object(strategy.get("exchange_config"))
        if execution_mode == "live":
            exchange_config = resolve_exchange_config(exchange_config, user_id=user_id)
        account_exchange = str(
            exchange_config.get("exchange_id") or exchange_config.get("exchangeId") or ""
        ).strip().lower()
        service = StrategyV2BacktestService()
        now = datetime.now(timezone.utc)
        candidates, _universe_id = service.resolve_candidates(
            user_id=user_id,
            manifest=program.manifest,
            start_date=now,
            end_date=now,
        )
        if execution_mode == "live" and account_exchange:
            for member in candidates:
                if member.get("market") == "Crypto":
                    member["exchange_id"] = account_exchange
            attach_instrument_product_contracts(
                candidates,
                trading_config,
                exchange_id=account_exchange,
            )
        frequency = program.manifest.driving_frequency
        bot_type = resolve_bot_type(
            strategy,
            trading_config,
            source_code=code,
        )
        daily_policy = daily_equity_execution_policy(
            frequency,
            candidates,
            execution_mode=execution_mode,
            schedules=program.manifest.schedules,
        )
        eligible = bool(
            daily_policy is None
            and bot_type not in {"grid", "martingale", "layered_martingale"}
            and not callable(program.namespace.get("on_price_tick"))
            and supports_bar_close_events(frequency, candidates)
        )
        if not eligible:
            return set()
        return {
            BarStreamKey.from_member(
                member,
                frequency,
                default_venue=account_exchange,
            )
            for member in candidates
        }


__all__ = [
    "StrategyEventSubscriptionPlanner",
    "StrategyEventSubscriptionRepository",
    "strategy_shard",
]
