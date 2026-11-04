"""Background market catalog synchronization and operator-facing status."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from app.services.symbol_master_sync import (
    fetch_crypto_symbols_with_diagnostics,
    reclassify_stored_equity_products,
    upsert_symbol_master,
)
from app.utils.db import get_db_connection
from app.utils.logger import get_logger


logger = get_logger(__name__)
_thread_lock = threading.Lock()
_worker = None
MARKET_CATALOG_SCHEMA_VERSION = 3


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value or {}


def _claim_run(trigger: str):
    with get_db_connection() as db:
        cur = db.cursor()
        try:
            cur.execute(
                """
                UPDATE qd_market_sync_runs
                   SET status = 'failed', finished_at = NOW(),
                       result = '{"error":"interrupted"}'::jsonb
                 WHERE status = 'running'
                   AND started_at < NOW() - INTERVAL '1 hour'
                """
            )
            cur.execute(
                """
                INSERT INTO qd_market_sync_runs (trigger_type, status)
                VALUES (?, 'running')
                RETURNING id
                """,
                (trigger,),
            )
            row = cur.fetchone()
            db.commit()
            return int(row["id"])
        except Exception:
            db.rollback()
            return None
        finally:
            cur.close()


def _finish_run(run_id: int, status: str, result: dict) -> None:
    with get_db_connection() as db:
        cur = db.cursor()
        try:
            cur.execute(
                """
                UPDATE qd_market_sync_runs
                   SET status = ?, finished_at = NOW(), result = ?::jsonb
                 WHERE id = ?
                """,
                (status, json.dumps(result, ensure_ascii=False), run_id),
            )
            db.commit()
        finally:
            cur.close()


def _run_sync(run_id: int) -> None:
    try:
        rows, contexts = fetch_crypto_symbols_with_diagnostics()
        written = upsert_symbol_master(rows) if rows else 0
        reclassified = reclassify_stored_equity_products()
        succeeded = sum(1 for item in contexts if item.get("ok"))
        failed = len(contexts) - succeeded
        status = "success" if failed == 0 else ("partial" if succeeded else "failed")
        _finish_run(run_id, status, {
            "catalog_schema_version": MARKET_CATALOG_SCHEMA_VERSION,
            "rows": len(rows),
            "upserted": written,
            "reclassified": reclassified,
            "contexts_total": len(contexts),
            "contexts_succeeded": succeeded,
            "contexts_failed": failed,
            "contexts": contexts,
        })
        logger.info(
            "Market catalog sync finished status=%s rows=%s contexts=%s/%s",
            status, written, succeeded, len(contexts),
        )
    except Exception as exc:
        logger.error("Market catalog sync failed: %s", exc, exc_info=True)
        _finish_run(run_id, "failed", {"error": str(exc)})


def start_market_catalog_sync(trigger: str = "manual") -> dict:
    """Claim and start one non-blocking sync job across all backend workers."""
    global _worker
    with _thread_lock:
        run_id = _claim_run(trigger)
        if run_id is None:
            return {"started": False, "reason": "already_running"}
        _worker = threading.Thread(
            target=_run_sync,
            args=(run_id,),
            name=f"MarketCatalogSync-{run_id}",
            daemon=True,
        )
        _worker.start()
        return {"started": True, "run_id": run_id}


def run_market_catalog_sync_inline(trigger: str = "scheduled") -> dict:
    """Claim and execute one catalog sync in the current durable worker."""
    run_id = _claim_run(trigger)
    if run_id is None:
        return {"started": False, "reason": "already_running"}
    _run_sync(run_id)
    return {"started": True, "run_id": run_id}


def _market_catalog_is_initialized() -> bool:
    with get_db_connection() as db:
        cur = db.cursor()
        try:
            cur.execute(
                """
                SELECT COUNT(*) FILTER (
                           WHERE market = 'Crypto' AND is_active = 1
                       ) AS active_crypto,
                       (
                           SELECT result
                             FROM qd_market_sync_runs
                            WHERE status IN ('success', 'partial')
                            ORDER BY id DESC
                            LIMIT 1
                       ) AS latest_success_result
                  FROM qd_market_symbols
                """
            )
            row = dict(cur.fetchone() or {})
            result = _json_value(row.get("latest_success_result"))
            try:
                schema_version = int(result.get("catalog_schema_version") or 0)
            except (TypeError, ValueError):
                schema_version = 0
            return (
                int(row.get("active_crypto") or 0) > 0
                and schema_version >= MARKET_CATALOG_SCHEMA_VERSION
            )
        finally:
            cur.close()


def start_market_catalog_sync_on_boot() -> dict:
    """Start one initial sync only when the shared catalog is not initialized."""
    if os.getenv("MARKET_CATALOG_AUTO_SYNC", "true").strip().lower() not in ("1", "true", "yes", "on"):
        logger.info("Automatic market catalog sync is disabled")
        return {"started": False, "reason": "disabled"}
    if os.getenv("PYTHON_API_DEBUG", "false").lower() == "true" and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return {"started": False, "reason": "debug_parent"}
    if _market_catalog_is_initialized():
        result = {"started": False, "reason": "already_initialized"}
        logger.info("Initial market catalog sync skipped: %s", result)
        return result
    result = start_market_catalog_sync("startup")
    logger.info("Initial market catalog sync: %s", result)
    return result


def get_market_catalog_overview() -> dict:
    """Return catalog totals, venue coverage, and the most recent sync run."""
    with get_db_connection() as db:
        cur = db.cursor()
        try:
            cur.execute(
                """
                SELECT market, COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE is_active = 1) AS active
                  FROM qd_market_symbols
                 GROUP BY market
                 ORDER BY market
                """
            )
            markets = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT LOWER(exchange) AS exchange, market_type,
                       COUNT(*) FILTER (WHERE is_active = 1) AS active,
                       COUNT(DISTINCT symbol) FILTER (WHERE is_active = 1) AS symbols,
                       COUNT(*) FILTER (WHERE is_active = 1 AND asset_class = 'equity') AS equities,
                       COUNT(*) FILTER (WHERE is_active = 1 AND asset_class = 'rwa') AS rwa,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND market_type = 'spot'
                             AND COALESCE(product_type, 'crypto') = 'crypto'
                       ) AS ordinary_spot,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND market_type = 'swap'
                             AND COALESCE(product_type, 'crypto') = 'crypto'
                       ) AS ordinary_swap,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND product_type = 'tokenized_equity'
                       ) AS tokenized_equity,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND product_type = 'direct_equity'
                       ) AS direct_equity,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND product_type = 'stock_perpetual'
                       ) AS stock_perpetual
                  FROM qd_market_symbols
                 WHERE market = 'Crypto' AND exchange <> ''
                 GROUP BY LOWER(exchange), market_type
                 ORDER BY LOWER(exchange), market_type
                """
            )
            venues = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT COUNT(*) FILTER (WHERE is_active = 1) AS active,
                       COUNT(DISTINCT symbol) FILTER (WHERE is_active = 1) AS symbols,
                       COUNT(*) FILTER (WHERE is_active = 1 AND asset_class = 'equity') AS equities,
                       COUNT(*) FILTER (WHERE is_active = 1 AND asset_class = 'rwa') AS rwa,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND market_type = 'spot'
                             AND COALESCE(product_type, 'crypto') = 'crypto'
                       ) AS ordinary_spot,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND market_type = 'swap'
                             AND COALESCE(product_type, 'crypto') = 'crypto'
                       ) AS ordinary_swap,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND product_type = 'tokenized_equity'
                       ) AS tokenized_equity,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND product_type = 'direct_equity'
                       ) AS direct_equity,
                       COUNT(*) FILTER (
                           WHERE is_active = 1 AND product_type = 'stock_perpetual'
                       ) AS stock_perpetual
                  FROM qd_market_symbols
                 WHERE market = 'Crypto'
                """
            )
            crypto = dict(cur.fetchone() or {})
            cur.execute(
                """
                SELECT id, trigger_type, status, started_at, finished_at, result
                  FROM qd_market_sync_runs
                 ORDER BY id DESC LIMIT 1
                """
            )
            latest = cur.fetchone()
            if latest:
                latest = dict(latest)
                latest["result"] = _json_value(latest.get("result"))
            return {
                "markets": markets,
                "crypto": crypto,
                "venues": venues,
                "latest_sync": latest,
                "server_time": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            cur.close()
