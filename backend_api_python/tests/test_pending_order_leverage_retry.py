from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

from app.services.pending_order_worker import PendingOrderWorker


def test_schedule_leverage_retry_requeues_pending_order():
    worker = PendingOrderWorker.__new__(PendingOrderWorker)
    payload = {"strategy_id": 11, "signal_type": "add_short"}

    with patch("app.services.pending_order_worker.get_db_connection") as mock_conn:
        db = MagicMock()
        cur = MagicMock()
        mock_conn.return_value.__enter__.return_value = db
        db.cursor.return_value = cur

        worker._schedule_leverage_retry(
            order_id=14,
            payload=payload,
            attempt_count=2,
            max_retries=3,
            retry_delay_sec=60,
            err_str="OKX 59669",
        )

        cur.execute.assert_called_once()
        sql, params = cur.execute.call_args[0]
        assert "UPDATE pending_orders" in sql
        assert "status = 'pending'" in sql
        assert params[0] == "[auto-retry-2]OKX 59669"
        stored_payload = json.loads(params[2])
        assert stored_payload["leverage_retry_attempt"] == 2
        assert stored_payload["leverage_retry_not_before"] > time.time()
        db.commit.assert_called_once()


def test_fetch_pending_orders_skips_leverage_retry_backoff():
    worker = PendingOrderWorker.__new__(PendingOrderWorker)
    worker._stale_processing_sec = 0

    future_payload = json.dumps({"leverage_retry_not_before": time.time() + 3600})
    ready_payload = json.dumps({"leverage_retry_not_before": time.time() - 1})

    with patch("app.services.pending_order_worker.get_db_connection") as mock_conn:
        db = MagicMock()
        cur = MagicMock()
        mock_conn.return_value.__enter__.return_value = db
        db.cursor.return_value = cur
        cur.fetchall.return_value = [
            {"id": 1, "payload_json": future_payload, "status": "pending"},
            {"id": 2, "payload_json": ready_payload, "status": "pending"},
        ]

        rows = worker._fetch_pending_orders(limit=10)

    assert len(rows) == 1
    assert rows[0]["id"] == 2
