"""
USDT payment service + background reconciler worker.

Public entry points (kept stable for existing imports in routes/billing.py
and app/__init__.py):

    get_usdt_payment_service() -> UsdtPaymentService
    get_usdt_order_worker()    -> UsdtOrderWorker
    UsdtPaymentService.create_order(user_id, plan, chain=None)
    UsdtPaymentService.get_order(user_id, order_id, refresh=True)
    UsdtPaymentService.refresh_all_active_orders() -> int
    UsdtPaymentService.list_chains() -> list[dict]

v3.0.6 rewrite: replaced the per-order xpub-derived TRC20 address scheme with
a fixed receiving address per chain + amount-suffix order identification.
The order DB schema and column layout are documented in migrations/init.sql.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.services.billing_service import get_billing_service

from .chains import (
    CHAIN_SPECS,
    build_amount_with_suffix,
    build_payment_uri,
    chain_metadata,
    format_amount_display,
    list_enabled_chains,
)
from .watchers import get_watcher

logger = get_logger(__name__)


_CREATE_RETRY_LIMIT = 10
_ACTIVE_STATUSES = ("pending", "paid")


class UsdtPaymentService:
    _schema_ensured = False

    def __init__(self) -> None:
        self.billing = get_billing_service()

    # ---------------------------------------------------------------- config

    def _get_cfg(self) -> Dict[str, Any]:
        return {
            "enabled": str(os.getenv("USDT_PAY_ENABLED", "False")).lower() in ("1", "true", "yes"),
            "confirm_seconds": int(float(os.getenv("USDT_PAY_CONFIRM_SECONDS", "30") or 30)),
            "order_expire_minutes": int(float(os.getenv("USDT_PAY_EXPIRE_MINUTES", "30") or 30)),
            "debug_reconcile_log": str(os.getenv("USDT_PAY_DEBUG_LOG", "true")).lower() in ("1", "true", "yes"),
        }

    def list_chains(self) -> List[Dict[str, Any]]:
        """Frontend uses this to render the chain picker (hides chains
        whose receiving address is not configured)."""
        return list_enabled_chains()

    # ---------------------------------------------------------------- schema

    def _ensure_schema_best_effort(self, cur) -> None:
        # We rely on migrations/init.sql for the canonical DDL. This is only
        # a paranoid fallback so unit tests against a fresh sqlite/postgres
        # don't blow up if init.sql wasn't applied yet.
        if UsdtPaymentService._schema_ensured:
            return
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qd_usdt_orders (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    plan VARCHAR(20) NOT NULL,
                    chain VARCHAR(20) NOT NULL DEFAULT 'TRC20',
                    currency VARCHAR(10) NOT NULL DEFAULT 'USDT',
                    amount_usdt DECIMAL(20,8) NOT NULL DEFAULT 0,
                    amount_suffix DECIMAL(20,8) NOT NULL DEFAULT 0,
                    address VARCHAR(120) NOT NULL DEFAULT '',
                    payment_uri TEXT NOT NULL DEFAULT '',
                    matched_via VARCHAR(20) NOT NULL DEFAULT 'amount_suffix',
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    tx_hash VARCHAR(120) DEFAULT '',
                    paid_at TIMESTAMP,
                    confirmed_at TIMESTAMP,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
            # v3.0.6 cleanup: drop the legacy unique index on (chain, address)
            # that came from the pre-v3.0.6 per-order xpub-derived address
            # scheme. Under the current "single fixed receiving address per
            # chain + amount-suffix matching" model, all active orders on
            # the same chain share the same address, so this old index would
            # reject every second pending order with UniqueViolation on
            # idx_usdt_orders_address_unique. Wrapped in its own try/except
            # so even if the drop fails the rest of the bootstrap proceeds.
            try:
                cur.execute("DROP INDEX IF EXISTS idx_usdt_orders_address_unique")
            except Exception as drop_exc:
                logger.debug("USDT drop legacy address index skipped: %s", drop_exc)
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_usdt_orders_amount_active "
                "ON qd_usdt_orders(chain, amount_usdt) WHERE status IN ('pending','paid')"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_usdt_orders_user_id ON qd_usdt_orders(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_usdt_orders_status ON qd_usdt_orders(status)")
            UsdtPaymentService._schema_ensured = True
        except Exception as exc:  # pragma: no cover — best-effort path
            logger.debug("USDT _ensure_schema_best_effort skipped: %s", exc)

    # ---------------------------------------------------------------- orders

    def create_order(
        self,
        user_id: int,
        plan: str,
        chain: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        cfg = self._get_cfg()
        if not cfg["enabled"]:
            return False, "usdt_pay_disabled", {}

        plan = (plan or "").strip().lower()
        if plan not in ("monthly", "yearly", "lifetime"):
            return False, "invalid_plan", {}

        # Default to the first enabled chain when caller didn't specify one.
        enabled = self.list_chains()
        if not enabled:
            return False, "no_chain_configured", {}
        if not chain:
            chain = enabled[0]["code"]
        chain = chain.strip().upper()
        meta = chain_metadata(chain)
        if meta is None:
            return False, "chain_not_available", {"chain": chain}

        plans = self.billing.get_membership_plans()
        base_amount = Decimal(str(plans.get(plan, {}).get("price_usd") or 0))
        if base_amount <= 0:
            return False, "invalid_amount", {}

        now = datetime.now(timezone.utc)

        # Idempotency: if the user already has an open (pending, not-expired)
        # order for the same (plan, chain), return it instead of creating
        # a new one. This is the "user closed the payment modal and is
        # reopening it" path — otherwise we'd leak rows in the DB and the
        # user would see a fresh amount suffix every time the modal opens.
        existing = self._find_active_order(user_id, plan, chain, now)
        if existing is not None:
            out = self._row_to_dict(existing)
            out["reused"] = True
            out["decimals"] = meta["decimals"]
            return True, "success_reused", out

        expires_at = now + timedelta(minutes=cfg["order_expire_minutes"])

        # Retry on the unique-index collision for (chain, amount_usdt) where
        # status IN ('pending','paid'). The retry counter feeds the seed so
        # every attempt yields a different suffix.
        last_error = "unknown"
        for attempt in range(_CREATE_RETRY_LIMIT):
            final_amount, suffix = build_amount_with_suffix(base_amount, attempt=attempt)
            try:
                with get_db_connection() as db:
                    cur = db.cursor()
                    self._ensure_schema_best_effort(cur)
                    cur.execute(
                        """
                        INSERT INTO qd_usdt_orders
                          (user_id, plan, chain, currency, amount_usdt, amount_suffix,
                           address, payment_uri, matched_via, status,
                           expires_at, created_at, updated_at)
                        VALUES (?, ?, ?, 'USDT', ?, ?, ?, '', 'amount_suffix', 'pending', ?, NOW(), NOW())
                        RETURNING id
                        """,
                        (
                            user_id,
                            plan,
                            chain,
                            final_amount,
                            suffix,
                            meta["address"],
                            expires_at,
                        ),
                    )
                    row = cur.fetchone() or {}
                    order_id = row.get("id")
                    if order_id is None:
                        db.rollback()
                        last_error = "insert_no_id"
                        continue

                    uri = build_payment_uri(
                        chain,
                        meta["address"],
                        final_amount,
                        contract=meta["contract"],
                        order_id=int(order_id),
                    )
                    cur.execute(
                        "UPDATE qd_usdt_orders SET payment_uri = ?, updated_at = NOW() WHERE id = ?",
                        (uri, order_id),
                    )
                    db.commit()
                    cur.close()

                return True, "success", {
                    "order_id": order_id,
                    "plan": plan,
                    "chain": chain,
                    "currency": "USDT",
                    "amount_usdt": format_amount_display(final_amount),
                    "amount_suffix": format_amount_display(suffix),
                    "address": meta["address"],
                    "payment_uri": uri,
                    "decimals": meta["decimals"],
                    "status": "pending",
                    "expires_at": expires_at.isoformat(),
                    "wallet_compat_note": _wallet_compat_note(chain),
                    "reused": False,
                }
            except Exception as exc:
                # Detect unique-violation by message; psycopg2 uses
                # IntegrityError, sqlite uses sqlite3.IntegrityError; we
                # don't want to import driver-specific classes here.
                msg = str(exc)
                # Legacy unique index `idx_usdt_orders_address_unique` on
                # (chain, address) was retired in v3.0.6 because all active
                # orders on a chain now share the same receiving address.
                # If we still hit it (e.g. DROP INDEX failed earlier because
                # the running DB user is not the table owner), there's no
                # point retrying — every retry on the same chain still uses
                # the same address. Return a dedicated error so the caller
                # can surface "the DBA still needs to drop the old index"
                # instead of silently spinning through 10 attempts.
                if "idx_usdt_orders_address_unique" in msg:
                    logger.error(
                        "USDT create_order blocked by stale unique index "
                        "idx_usdt_orders_address_unique on qd_usdt_orders(chain,address). "
                        "Run: DROP INDEX IF EXISTS idx_usdt_orders_address_unique;"
                    )
                    return False, "legacy_address_index", {}
                if "idx_usdt_orders_amount_active" in msg or "UNIQUE" in msg.upper():
                    logger.info(
                        "USDT create_order suffix collision (attempt=%s chain=%s amount=%s); retrying",
                        attempt, chain, final_amount,
                    )
                    last_error = "amount_collision"
                    continue
                logger.error("create_order failed: %s", exc, exc_info=True)
                return False, f"error:{exc}", {}

        logger.error("USDT create_order: exhausted %s suffix retries (chain=%s)", _CREATE_RETRY_LIMIT, chain)
        return False, last_error, {}

    def _find_active_order(
        self,
        user_id: int,
        plan: str,
        chain: str,
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
        """Return the user's open (pending + not yet expired) order for
        the given plan+chain, if any. Used by ``create_order`` to make the
        API idempotent across UI re-opens.
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                self._ensure_schema_best_effort(cur)
                cur.execute(
                    """
                    SELECT id, user_id, plan, chain, currency, amount_usdt, amount_suffix,
                           address, payment_uri, status, tx_hash, matched_via,
                           paid_at, confirmed_at, expires_at, created_at, updated_at
                    FROM qd_usdt_orders
                    WHERE user_id = ? AND plan = ? AND chain = ?
                      AND status = 'pending'
                      AND (expires_at IS NULL OR expires_at > ?)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (user_id, plan, chain, now),
                )
                row = cur.fetchone()
                cur.close()
            return row or None
        except Exception as exc:
            # Don't fail order creation just because the idempotency probe
            # crashed (e.g. on a freshly migrated DB). We fall through to
            # the "create new" path; the unique index on (chain, amount_usdt)
            # still keeps the DB consistent.
            logger.debug("USDT _find_active_order probe failed: %s", exc)
            return None

    def get_order(
        self,
        user_id: int,
        order_id: int,
        refresh: bool = True,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                self._ensure_schema_best_effort(cur)
                cur.execute(
                    """
                    SELECT id, user_id, plan, chain, currency, amount_usdt, amount_suffix,
                           address, payment_uri, status, tx_hash, matched_via,
                           paid_at, confirmed_at, expires_at, created_at, updated_at
                    FROM qd_usdt_orders
                    WHERE id = ? AND user_id = ?
                    """,
                    (order_id, user_id),
                )
                row = cur.fetchone()
                cur.close()

            if not row:
                return False, "order_not_found", {}

            if refresh:
                try:
                    self._refresh_one_order(row)
                except Exception as exc:
                    logger.warning("get_order refresh failed order_id=%s: %s", order_id, exc)

                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        """
                        SELECT id, user_id, plan, chain, currency, amount_usdt, amount_suffix,
                               address, payment_uri, status, tx_hash, matched_via,
                               paid_at, confirmed_at, expires_at, created_at, updated_at
                        FROM qd_usdt_orders
                        WHERE id = ? AND user_id = ?
                        """,
                        (order_id, user_id),
                    )
                    row = cur.fetchone()
                    cur.close()

            return True, "success", self._row_to_dict(row)
        except Exception as exc:
            logger.error("get_order failed: %s", exc, exc_info=True)
            return False, f"error:{exc}", {}

    # ----------------------------------------------------------- reconciler

    def refresh_all_active_orders(self) -> int:
        """Worker entry point. Scan pending/paid USDT orders across all
        chains and apply state transitions. Returns the number of orders
        whose status changed.

        We deliberately load candidate rows in a short read txn and release
        the connection before any HTTP work, so a slow explorer never
        keeps a DB connection in `idle in transaction`.
        """
        cfg = self._get_cfg()
        if not cfg["enabled"]:
            return 0
        updated = 0
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                self._ensure_schema_best_effort(cur)
                cur.execute(
                    """
                    SELECT id, user_id, plan, chain, currency, amount_usdt, amount_suffix,
                           address, payment_uri, status, tx_hash, matched_via,
                           paid_at, confirmed_at, expires_at, created_at, updated_at
                    FROM qd_usdt_orders
                    WHERE status IN ('pending', 'paid')
                    ORDER BY created_at ASC
                    LIMIT 100
                    """
                )
                rows = cur.fetchall() or []
                cur.close()

            for row in rows:
                old = (row.get("status") or "").lower()
                try:
                    self._refresh_one_order(row)
                except Exception as exc:
                    logger.debug("refresh_all order %s error: %s", row.get("id"), exc)
                    continue
                try:
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute("SELECT status FROM qd_usdt_orders WHERE id = ?", (row["id"],))
                        new_row = cur.fetchone() or {}
                        cur.close()
                    new = (new_row.get("status") or "").lower()
                    if new and new != old:
                        updated += 1
                        logger.info("USDT order %s: %s -> %s", row["id"], old, new)
                except Exception:
                    pass
        except Exception as exc:
            logger.error("refresh_all_active_orders error: %s", exc, exc_info=True)
        return updated

    # ----------------------------------------------------- single-order flow

    def _refresh_one_order(self, row: Dict[str, Any]) -> None:
        """HTTP-out-of-txn refresh of a single order. Writes happen in short
        txns. Used by both the worker and ``get_order(refresh=True)``."""
        cfg = self._get_cfg()
        status = (row.get("status") or "").lower()
        chain = (row.get("chain") or "").upper()
        order_id = row.get("id")
        now = datetime.now(timezone.utc)

        if chain not in CHAIN_SPECS:
            logger.debug("USDT refresh skip order_id=%s reason=unknown_chain chain=%s", order_id, chain)
            return
        if status not in ("pending", "paid", "expired"):
            return

        address = (row.get("address") or "").strip()
        amount = Decimal(str(row.get("amount_usdt") or 0))
        if not address or amount <= 0:
            return

        # 'paid' state just waits for the confirmation delay
        if status == "paid":
            confirm_sec = int(cfg.get("confirm_seconds") or 30)
            paid_at = _coerce_utc_datetime(row.get("paid_at"))
            ready = False
            if paid_at and (now - paid_at).total_seconds() >= confirm_sec:
                ready = True
            elif paid_at is None and confirm_sec <= 0:
                ready = True
            if ready:
                self._confirm_and_activate(order_id, row.get("user_id"), row.get("plan"), row.get("tx_hash") or "")
            return

        # pending / expired: run the chain watcher (HTTP) outside any DB txn
        watcher = get_watcher(chain)
        if watcher is None:
            logger.warning("USDT refresh: no watcher for chain=%s", chain)
            return
        tx, note = watcher(address, amount, row.get("created_at"))

        if cfg.get("debug_reconcile_log"):
            logger.info(
                "USDT reconcile chain=%s order_id=%s user_id=%s status=%s amount=%s addr=%s note=%s",
                chain, order_id, row.get("user_id"), status, amount, address,
                note if tx is None else f"matched_tx={tx.tx_hash}",
            )

        if tx is not None:
            tx_hash = tx.tx_hash
            paid_at = datetime.now(timezone.utc)
            try:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        "UPDATE qd_usdt_orders SET status='paid', tx_hash=?, paid_at=?, updated_at=NOW() "
                        "WHERE id=? AND status IN ('pending','expired')",
                        (tx_hash, paid_at, order_id),
                    )
                    db.commit()
                    cur.close()
            except Exception as exc:
                logger.error("USDT mark_paid UPDATE failed order_id=%s: %s", order_id, exc)
                return

            confirm_sec = int(cfg.get("confirm_seconds") or 30)
            if tx.block_timestamp_ms:
                tx_time = datetime.fromtimestamp(tx.block_timestamp_ms / 1000.0, tz=timezone.utc)
                if (now - tx_time).total_seconds() >= confirm_sec:
                    self._confirm_and_activate(order_id, row.get("user_id"), row.get("plan"), tx_hash)
            elif confirm_sec <= 0:
                self._confirm_and_activate(order_id, row.get("user_id"), row.get("plan"), tx_hash)
            return

        # No incoming transfer yet: pending → expired when the window closes
        if status == "pending":
            exp = _coerce_utc_datetime(row.get("expires_at"))
            if exp is not None and exp <= now:
                try:
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute(
                            "UPDATE qd_usdt_orders SET status='expired', updated_at=NOW() "
                            "WHERE id=? AND status='pending'",
                            (order_id,),
                        )
                        db.commit()
                        cur.close()
                except Exception as exc:
                    logger.warning("USDT mark_expired UPDATE failed order_id=%s: %s", order_id, exc)

    def _confirm_and_activate(self, order_id: int, user_id: int, plan: str, tx_hash: str) -> None:
        """Idempotent: mark confirmed and activate membership."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT status FROM qd_usdt_orders WHERE id = ?", (order_id,))
                cur_row = cur.fetchone() or {}
                if (cur_row.get("status") or "").lower() == "confirmed":
                    cur.close()
                    return
                cur.execute(
                    "UPDATE qd_usdt_orders SET status='confirmed', confirmed_at=NOW(), updated_at=NOW() "
                    "WHERE id=? AND status IN ('paid','pending')",
                    (order_id,),
                )
                db.commit()
                cur.close()
        except Exception as exc:
            logger.error("USDT confirm UPDATE failed order_id=%s: %s", order_id, exc)
            return

        try:
            ok, msg, _ = self.billing.purchase_membership(
                int(user_id),
                str(plan),
                record_membership_order=False,
                fulfillment_ref=f"usdt_order:{order_id}",
            )
            logger.info("USDT activate membership: order=%s user=%s plan=%s ok=%s msg=%s", order_id, user_id, plan, ok, msg)
        except Exception as exc:
            logger.error("USDT activate membership failed order=%s err=%s", order_id, exc, exc_info=True)

    # ------------------------------------------------------------ serializers

    @staticmethod
    def _row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
        if not row:
            return {}
        chain_code = (row.get("chain") or "").upper()
        spec = CHAIN_SPECS.get(chain_code)
        return {
            "order_id": row.get("id"),
            "plan": row.get("plan"),
            "chain": row.get("chain"),
            "currency": row.get("currency") or "USDT",
            # NUMERIC(20,8) round-trips with trailing zero padding; the
            # display formatter quantizes back to suffix_decimals() places
            # so the UI never has to deal with two amounts that look
            # different but represent the same value.
            "amount_usdt": format_amount_display(row.get("amount_usdt")),
            "amount_suffix": format_amount_display(row.get("amount_suffix")),
            "address": row.get("address") or "",
            "payment_uri": row.get("payment_uri") or "",
            "status": row.get("status") or "",
            "tx_hash": row.get("tx_hash") or "",
            "matched_via": row.get("matched_via") or "",
            "decimals": spec.decimals if spec else 6,
            "paid_at": row.get("paid_at").isoformat() if row.get("paid_at") else None,
            "confirmed_at": row.get("confirmed_at").isoformat() if row.get("confirmed_at") else None,
            "expires_at": row.get("expires_at").isoformat() if row.get("expires_at") else None,
            "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
            "wallet_compat_note": _wallet_compat_note(row.get("chain") or ""),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_utc_datetime(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _wallet_compat_note(chain: str) -> str:
    """Short user-facing string to be surfaced on the order page so users
    know which wallets will prefill the amount automatically vs which will
    only auto-fill the address.
    """
    chain = (chain or "").upper()
    if chain in ("BEP20", "ERC20"):
        return "evm_eip681"      # MetaMask / TrustWallet / TokenPocket / imToken / OKX
    if chain == "SOL":
        return "solana_pay"      # Phantom / Solflare / TokenPocket / OKX
    if chain == "TRC20":
        return "tron_partial"    # TokenPocket / imToken yes; older TronLink no
    return ""


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class UsdtOrderWorker:
    def __init__(self, poll_interval_sec: float = 30.0) -> None:
        self.poll_interval_sec = float(poll_interval_sec)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._pay_disabled_logged = False

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="UsdtOrderWorker", daemon=True)
            self._thread.start()
            logger.info("UsdtOrderWorker started (interval=%ss)", self.poll_interval_sec)
            return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            logger.info("UsdtOrderWorker stopped")

    def _run(self) -> None:
        # Defer the first scan a few seconds so app init can finish first.
        self._stop_event.wait(timeout=10)
        # Heartbeat: even when no orders change state, log a single tick
        # summary every N ticks so operators can confirm the thread is alive
        # without grep'ing for the rarer state-transition logs.
        heartbeat_every_ticks = max(1, int(300 / max(1.0, self.poll_interval_sec)))
        tick = 0
        while not self._stop_event.is_set():
            tick += 1
            try:
                svc = get_usdt_payment_service()
                cfg = svc._get_cfg()
                billing_enabled = get_billing_service().is_billing_enabled()
                if cfg["enabled"] and billing_enabled:
                    updated = svc.refresh_all_active_orders()
                    if updated > 0:
                        logger.info("UsdtOrderWorker tick #%s: %s orders changed state", tick, updated)
                    elif tick == 1 or tick % heartbeat_every_ticks == 0:
                        # First tick proves the worker reached refresh; later
                        # heartbeats every ~5 min keep the "alive" signal
                        # visible in operator logs without spamming.
                        logger.info(
                            "UsdtOrderWorker tick #%s heartbeat: 0 orders changed (still scanning)",
                            tick,
                        )
                elif not self._pay_disabled_logged:
                    logger.info(
                        "UsdtOrderWorker idle; enable both BILLING_ENABLED and "
                        "USDT_PAY_ENABLED to scan orders."
                    )
                    self._pay_disabled_logged = True
            except Exception as exc:
                logger.error("UsdtOrderWorker loop error: %s", exc, exc_info=True)
            self._stop_event.wait(timeout=self.poll_interval_sec)


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------


_svc: Optional[UsdtPaymentService] = None
_worker: Optional[UsdtOrderWorker] = None


def get_usdt_payment_service() -> UsdtPaymentService:
    global _svc
    if _svc is None:
        _svc = UsdtPaymentService()
    return _svc


def get_usdt_order_worker() -> UsdtOrderWorker:
    global _worker
    if _worker is None:
        interval = float(os.getenv("USDT_WORKER_POLL_INTERVAL", "30"))
        _worker = UsdtOrderWorker(poll_interval_sec=interval)
    return _worker
