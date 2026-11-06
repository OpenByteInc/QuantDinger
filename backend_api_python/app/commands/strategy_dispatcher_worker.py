"""Kafka worker that maps market events to strategy-shard evaluation batches."""

from __future__ import annotations

import os
import time


def main() -> None:
    os.environ["QD_PROCESS_ROLE"] = "strategy-dispatcher"

    from app import create_app
    from app.events.bar_recovery import StrategyBarRecoveryPublisher
    from app.events.dispatcher import StrategyDueDispatcher
    from app.events.kafka import KafkaEventConsumer
    from app.events.protocol import MarketBarClosedV1
    from app.events.topics import topic_for_event
    from app.runtime.process import ShutdownSignal
    from app.services.strategy_command_repository import StrategyCommandRepository
    from app.utils.logger import get_logger
    from app.workers.trading import build_worker_id

    logger = get_logger(__name__)
    app = create_app(register_http_routes=False)
    shutdown = ShutdownSignal()
    shutdown.install()
    heartbeat_repository = StrategyCommandRepository()
    worker_id = build_worker_id()
    dispatcher = StrategyDueDispatcher()
    recovery = StrategyBarRecoveryPublisher()
    consumer = KafkaEventConsumer(
        topics=[topic_for_event(MarketBarClosedV1.event_type)],
        group_id=os.getenv(
            "KAFKA_STRATEGY_DISPATCH_GROUP_ID",
            "quantdinger-strategy-dispatch-v1",
        ),
    )
    last_heartbeat = 0.0
    last_recovery = 0.0
    try:
        recovery_interval = max(
            1.0,
            float(os.getenv("STRATEGY_BAR_RECOVERY_INTERVAL_SEC", "5")),
        )
    except (TypeError, ValueError):
        recovery_interval = 5.0

    def dispatch(event):
        recovery.observe(event)
        return dispatcher.dispatch(event)

    with app.app_context():
        try:
            logger.info("Strategy dispatcher worker started: %s", worker_id)
            while not shutdown.event.is_set():
                consumer.poll_once(dispatch, timeout=1.0)
                now = time.monotonic()
                if now - last_recovery >= recovery_interval:
                    try:
                        recovery.publish_due()
                    except Exception:
                        logger.exception("Strategy bar recovery cycle failed")
                    last_recovery = now
                if now - last_heartbeat >= 10.0:
                    heartbeat_repository.record_worker_heartbeat(
                        worker_id=worker_id,
                        role="strategy-dispatcher",
                        metadata={
                            **consumer.snapshot(),
                            **dispatcher.snapshot(),
                            **recovery.snapshot(),
                        },
                    )
                    last_heartbeat = now
        finally:
            consumer.close()
            dispatcher.close()
            recovery.close()
            heartbeat_repository.mark_worker_stopped(worker_id)
            logger.info("Strategy dispatcher worker stopped: %s", worker_id)


if __name__ == "__main__":
    main()
