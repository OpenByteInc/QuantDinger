"""Unified billing service.

The billing switch and per-feature costs are stored in environment-backed
settings. A cost of 0 makes the feature free; disabling billing bypasses all
credit deductions. Marketplace VIP/free pricing is handled in community purchase
flows, not in this global usage-metering layer.
"""
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.services.billing_config import FEATURE_NAMES, load_billing_config, load_membership_plans

logger = get_logger(__name__)


class BillingService:
    """Billing and credit accounting service."""
    
    def __init__(self):
        self._config_cache = None
        self._config_cache_time = 0
        self._cache_ttl = 60  # Config cache TTL in seconds
    
    def get_billing_config(self) -> Dict[str, Any]:
        """Return billing configuration from environment-backed settings."""
        now = time.time()
        if self._config_cache and (now - self._config_cache_time) < self._cache_ttl:
            return self._config_cache
        
        config = load_billing_config()
        
        self._config_cache = config
        self._config_cache_time = now
        return config
    
    def clear_config_cache(self):
        """Clear billing config cache."""
        self._config_cache = None
        self._config_cache_time = 0
    
    def is_billing_enabled(self) -> bool:
        """Return whether billing is enabled."""
        config = self.get_billing_config()
        return config.get('enabled', False)
    
    def get_feature_cost(self, feature: str) -> int:
        """Return feature credit cost; 0 means free."""
        config = self.get_billing_config()
        cost_key = f'cost_{feature}'
        return config.get(cost_key, 0)
    
    def get_user_credits(self, user_id: int) -> Decimal:
        """Return user credit balance."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT credits FROM qd_users WHERE id = ?",
                    (user_id,)
                )
                row = cur.fetchone()
                cur.close()
                
                if row:
                    return Decimal(str(row.get('credits', 0) or 0))
                return Decimal('0')
        except Exception as e:
            logger.error(f"get_user_credits failed: {e}")
            return Decimal('0')
    
    def get_user_vip_status(self, user_id: int) -> Tuple[bool, Optional[datetime]]:
        """Return VIP status and expiration time for a user."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                # Ensure lifetime membership monthly credits are granted (best-effort, silent on failure).
                self._ensure_membership_schema_best_effort(cur)
                self._grant_lifetime_monthly_credits_best_effort(cur, user_id)
                try:
                    db.commit()
                except Exception:
                    pass

                cur.execute("SELECT vip_expires_at FROM qd_users WHERE id = ?", (user_id,))
                row = cur.fetchone()
                cur.close()
                
                if row and row.get('vip_expires_at'):
                    expires_at = row['vip_expires_at']
                    if isinstance(expires_at, str):
                        expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    
                    now = datetime.now(timezone.utc)
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    
                    is_vip = expires_at > now
                    return is_vip, expires_at
                
                return False, None
        except Exception as e:
            logger.error(f"get_user_vip_status failed: {e}")
            return False, None

    # ==================== Membership Plans (VIP) ====================

    def get_membership_plans(self) -> Dict[str, Any]:
        """
        Get membership plans from .env (configured via Settings UI).

        Plan keys:
          - monthly: price_usd, credits_once, duration_days
          - yearly: price_usd, credits_once, duration_days
          - lifetime: price_usd, credits_monthly (granted every 30 days)
        """
        return load_membership_plans()

    def purchase_membership(
        self,
        user_id: int,
        plan: str,
        *,
        record_membership_order: bool = True,
        fulfillment_ref: str = "",
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Activate membership (VIP dates + bonus credits).

        When ``record_membership_order`` is True (legacy), inserts a row into ``qd_membership_orders``.
        USDT checkout sets it False so only ``qd_usdt_orders`` represents paid orders.
        """
        plan = (plan or "").strip().lower()
        plans = self.get_membership_plans()
        if plan not in plans:
            return False, "invalid_plan", {}

        try:
            with get_db_connection() as db:
                cur = db.cursor()
                self._ensure_membership_schema_best_effort(cur)
                if record_membership_order:
                    self._ensure_membership_orders_table_best_effort(cur)

                now = datetime.now(timezone.utc)

                # Read current VIP state to support
                #   (a) stacking expiry for monthly/yearly,
                #   (b) lifetime members buying monthly/yearly as a pure
                #       credit top-up without losing their lifetime status.
                cur.execute(
                    "SELECT vip_expires_at, vip_is_lifetime FROM qd_users WHERE id = ?",
                    (user_id,),
                )
                row = cur.fetchone() or {}
                current_expires = row.get("vip_expires_at")
                existing_is_lifetime = bool(row.get("vip_is_lifetime") or False)
                if isinstance(current_expires, str) and current_expires:
                    try:
                        current_expires = datetime.fromisoformat(current_expires.replace("Z", "+00:00"))
                    except Exception:
                        current_expires = None
                if current_expires and current_expires.tzinfo is None:
                    current_expires = current_expires.replace(tzinfo=timezone.utc)

                base_time = current_expires if (current_expires and current_expires > now) else now

                # Lifetime users buying monthly/yearly: keep them lifetime
                # (skip the VIP-field UPDATE below) but still grant credits.
                # Operators rely on this so heavy users can top up credits
                # mid-cycle without "downgrading" themselves to a fixed-term
                # plan.
                preserve_lifetime = existing_is_lifetime and plan in ("monthly", "yearly")

                vip_expires_at = None
                vip_plan = plan
                vip_is_lifetime = False

                if preserve_lifetime:
                    # Keep whatever the lifetime user already has on file.
                    vip_expires_at = current_expires
                    vip_plan = "lifetime"
                    vip_is_lifetime = True
                elif plan in ("monthly", "yearly"):
                    days = int(plans[plan].get("duration_days") or (30 if plan == "monthly" else 365))
                    vip_expires_at = base_time + timedelta(days=days)
                else:
                    # Lifetime upgrade (or first lifetime purchase): set very
                    # long expiry + mark lifetime flag.
                    vip_expires_at = now + timedelta(days=365 * 100)
                    vip_is_lifetime = True

                order_plan = plan
                order_price_usd = float(plans[plan].get("price_usd") or 0)
                order_id = None
                if record_membership_order:
                    try:
                        cur.execute(
                            """
                            INSERT INTO qd_membership_orders
                              (user_id, plan, price_usd, status, created_at, paid_at)
                            VALUES (?, ?, ?, 'paid', NOW(), NOW())
                            RETURNING id
                            """,
                            (user_id, order_plan, order_price_usd),
                        )
                        row2 = cur.fetchone() or {}
                        order_id = row2.get("id")
                    except Exception:
                        cur.execute(
                            """
                            INSERT INTO qd_membership_orders
                              (user_id, plan, price_usd, status, created_at, paid_at)
                            VALUES (?, ?, ?, 'paid', NOW(), NOW())
                            """,
                            (user_id, order_plan, order_price_usd),
                        )
                        order_id = getattr(cur, "lastrowid", None)
                    order_ref = str(order_id or "")
                else:
                    ref = (fulfillment_ref or "").strip()
                    order_ref = ref if ref else f"usdt:{user_id}:{int(now.timestamp())}"

                # Update user VIP fields, but only when something actually
                # changes. Lifetime members topping up via monthly/yearly
                # keep all three VIP columns intact so we don't accidentally
                # roll back a long expiry or flip the lifetime flag off.
                if not preserve_lifetime:
                    cur.execute(
                        """
                        UPDATE qd_users
                        SET vip_expires_at = ?,
                            vip_plan = ?,
                            vip_is_lifetime = ?,
                            updated_at = NOW()
                        WHERE id = ?
                        """,
                        (vip_expires_at, vip_plan, bool(vip_is_lifetime), user_id),
                    )

                # Credits grants
                if plan in ("monthly", "yearly"):
                    credits_once = int(plans[plan].get("credits_once") or 0)
                    if credits_once > 0:
                        remark = (
                            f"Lifetime top-up via {plan}"
                            if preserve_lifetime
                            else f"Membership bonus ({plan})"
                        )
                        # Use add_credits to update balance and log
                        # NOTE: add_credits opens its own connection, so we do a direct update here for atomicity.
                        self._add_credits_in_tx(cur, user_id, credits_once, action="membership_bonus",
                                                remark=remark, reference_id=order_ref)
                else:
                    # Lifetime: grant first month's credits immediately and set last grant time
                    monthly_credits = int(plans["lifetime"].get("credits_monthly") or 0)
                    if monthly_credits > 0:
                        self._add_credits_in_tx(cur, user_id, monthly_credits, action="membership_monthly",
                                                remark="Lifetime membership monthly credits", reference_id=order_ref)
                    try:
                        cur.execute(
                            "UPDATE qd_users SET vip_monthly_credits_last_grant = ?, updated_at = NOW() WHERE id = ?",
                            (now, user_id),
                        )
                    except Exception:
                        # Column may not exist; ignore
                        pass

                # NOTE: we used to also write a zero-amount `membership_purchase`
                # audit row here so the credits-log tab showed two rows per
                # purchase (one "you bought X" + one "you got N credits"). That
                # was redundant. The actual credit grant above already records
                # the action with a non-zero amount, and the membership
                # purchase itself is preserved in qd_membership_orders /
                # qd_usdt_orders. Dropping the duplicate keeps the user-facing
                # credits log clean (one row per real balance change).

                db.commit()
                cur.close()

            return True, "success", {
                "order_id": order_id,
                "plan": plan,
                "vip_expires_at": vip_expires_at.isoformat() if vip_expires_at else None,
            }
        except Exception as e:
            logger.error(f"purchase_membership failed: {e}", exc_info=True)
            return False, f"error:{str(e)}", {}

    def _ensure_membership_schema_best_effort(self, cur):
        """Best-effort schema upgrade for membership fields on qd_users."""
        try:
            # vip_plan / vip_is_lifetime / vip_monthly_credits_last_grant
            cur.execute("ALTER TABLE qd_users ADD COLUMN IF NOT EXISTS vip_plan VARCHAR(20) DEFAULT ''")
            cur.execute("ALTER TABLE qd_users ADD COLUMN IF NOT EXISTS vip_is_lifetime BOOLEAN DEFAULT FALSE")
            cur.execute("ALTER TABLE qd_users ADD COLUMN IF NOT EXISTS vip_monthly_credits_last_grant TIMESTAMP")
        except Exception:
            # Ignore schema upgrade failures (e.g., insufficient privileges)
            pass

    def _ensure_membership_orders_table_best_effort(self, cur):
        """Best-effort create membership orders table (legacy; optional row per mock checkout)."""
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qd_membership_orders (
                  id SERIAL PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  plan VARCHAR(20) NOT NULL,
                  price_usd DECIMAL(10,2) DEFAULT 0,
                  status VARCHAR(20) DEFAULT 'paid',
                  created_at TIMESTAMP DEFAULT NOW(),
                  paid_at TIMESTAMP
                )
                """
            )
        except Exception:
            pass

    def _add_credits_in_tx(self, cur, user_id: int, amount: int, action: str, remark: str, reference_id: str = ''):
        """Add credits within an existing DB transaction and write qd_credits_log."""
        try:
            cur.execute("SELECT credits FROM qd_users WHERE id = ?", (user_id,))
            row = cur.fetchone() or {}
            credits = Decimal(str(row.get("credits", 0) or 0))
            new_balance = credits + Decimal(str(amount))

            cur.execute("UPDATE qd_users SET credits = ?, updated_at = NOW() WHERE id = ?", (float(new_balance), user_id))
            cur.execute(
                """
                INSERT INTO qd_credits_log
                  (user_id, action, amount, balance_after, remark, operator_id, reference_id, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, NOW())
                """,
                (user_id, action, amount, float(new_balance), remark, reference_id),
            )
        except Exception as e:
            logger.debug(f"_add_credits_in_tx failed: {e}", exc_info=True)

    def _grant_lifetime_monthly_credits_best_effort(self, cur, user_id: int):
        """Grant lifetime monthly credits if due (best-effort)."""
        try:
            plans = self.get_membership_plans()
            monthly_credits = int(plans.get("lifetime", {}).get("credits_monthly") or 0)
            if monthly_credits <= 0:
                return

            cur.execute(
                "SELECT vip_is_lifetime, vip_expires_at, vip_monthly_credits_last_grant FROM qd_users WHERE id = ?",
                (user_id,),
            )
            row = cur.fetchone() or {}
            if not row.get("vip_is_lifetime"):
                return

            expires_at = row.get("vip_expires_at")
            if isinstance(expires_at, str) and expires_at:
                try:
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except Exception:
                    expires_at = None
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if expires_at and expires_at <= now:
                return

            last = row.get("vip_monthly_credits_last_grant")
            if isinstance(last, str) and last:
                try:
                    last = datetime.fromisoformat(last.replace("Z", "+00:00"))
                except Exception:
                    last = None
            if last and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)

            # First time: do nothing (purchase flow already grants), but set last to now if missing
            if not last:
                cur.execute(
                    "UPDATE qd_users SET vip_monthly_credits_last_grant = ?, updated_at = NOW() WHERE id = ?",
                    (now, user_id),
                )
                return

            # Use 30-day periods. Catch up up to 6 periods max to avoid abuse.
            delta_days = int((now - last).total_seconds() // 86400)
            periods = delta_days // 30
            if periods <= 0:
                return
            if periods > 6:
                periods = 6

            total = monthly_credits * periods
            self._add_credits_in_tx(cur, user_id, total, action="membership_monthly",
                                    remark=f"Lifetime membership monthly credits x{periods}", reference_id="")
            cur.execute(
                "UPDATE qd_users SET vip_monthly_credits_last_grant = ?, updated_at = NOW() WHERE id = ?",
                (now, user_id),
            )
        except Exception:
            # Best-effort; never break caller
            pass
    
    def check_and_consume(self, user_id: int, feature: str, reference_id: str = '') -> Tuple[bool, str]:
        """
        Check and consume credits for a feature.
        
        Args:
            user_id: User id.
            feature: Billing feature key, for example ai_analysis, ai_code_gen, or ai_tuning.
            reference_id: Optional related entity id.
        
        Returns:
            (success, message): Whether the charge succeeded and the status message.
        """
        if not self.is_billing_enabled():
            return True, 'billing_disabled'
        
        cost = self.get_feature_cost(feature)
        
        if cost <= 0:
            return True, 'free_feature'


        try:
            with get_db_connection() as db:
                cur = db.cursor()

                # Check and deduct in one statement so concurrent requests
                # cannot both spend the same credits.
                cur.execute(
                    """
                    UPDATE qd_users
                    SET credits = credits - ?, updated_at = NOW()
                    WHERE id = ? AND credits >= ?
                    RETURNING credits
                    """,
                    (cost, user_id, cost),
                )
                updated = cur.fetchone()
                if not updated:
                    cur.execute("SELECT credits FROM qd_users WHERE id = ?", (user_id,))
                    current = cur.fetchone() or {}
                    credits = Decimal(str(current.get('credits', 0) or 0))
                    db.rollback()
                    cur.close()
                    return False, f'insufficient_credits:{credits}:{cost}'

                new_balance = Decimal(str(updated.get('credits', 0) or 0))
                feature_name = FEATURE_NAMES.get(feature, feature)
                created_at_utc = datetime.now(timezone.utc)
                cur.execute(
                    """
                    INSERT INTO qd_credits_log 
                    (user_id, action, amount, balance_after, feature, reference_id, remark, created_at)
                    VALUES (?, 'consume', ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, -cost, float(new_balance), feature, reference_id, f'Consume: {feature_name}', created_at_utc)
                )
                
                db.commit()
                cur.close()
            
            logger.info(f"User {user_id} consumed {cost} credits for {feature}, balance: {new_balance}")
            return True, 'consumed'
            
        except Exception as e:
            logger.error(f"check_and_consume failed: {e}")
            return False, f'error:{str(e)}'
    
    def add_credits(self, user_id: int, amount: int, action: str = 'recharge', 
                    remark: str = '', operator_id: int = None, reference_id: str = '') -> Tuple[bool, str]:
        """
        Add credits to a user account.
        
        Args:
            user_id: User id.
            amount: Positive credit amount.
            action: Action type such as recharge, admin_adjust, refund, referral_bonus, or register_bonus.
            remark: Optional remark.
            operator_id: Operator user id for admin actions.
            reference_id: Related entity id such as invited user id or order number.
        
        Returns:
            (success, message)
        """
        if amount <= 0:
            return False, 'amount_must_be_positive'
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT credits FROM qd_users WHERE id = ? FOR UPDATE", (user_id,))
                row = cur.fetchone() or {}
                if reference_id:
                    cur.execute(
                        """
                        SELECT balance_after FROM qd_credits_log
                        WHERE user_id = ? AND action = ? AND reference_id = ?
                        ORDER BY id DESC LIMIT 1
                        """,
                        (user_id, action, reference_id),
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.close()
                        return True, str(existing.get("balance_after") or "")

                credits = Decimal(str(row.get("credits", 0) or 0))
                new_balance = credits + Decimal(str(amount))
                cur.execute(
                    "UPDATE qd_users SET credits = ?, updated_at = NOW() WHERE id = ?",
                    (float(new_balance), user_id)
                )
                
                cur.execute(
                    """
                    INSERT INTO qd_credits_log 
                    (user_id, action, amount, balance_after, remark, operator_id, reference_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, NOW())
                    """,
                    (user_id, action, amount, float(new_balance), remark, operator_id, reference_id)
                )
                
                db.commit()
                cur.close()
            
            logger.info(f"User {user_id} added {amount} credits ({action}), balance: {new_balance}")
            return True, str(new_balance)
            
        except Exception as e:
            logger.error(f"add_credits failed: {e}")
            return False, str(e)
    
    def set_credits(self, user_id: int, amount: int, remark: str = '', 
                    operator_id: int = None) -> Tuple[bool, str]:
        """
        Set user credits directly for admin adjustments.
        
        Args:
            user_id: User id.
            amount: New credit amount.
            remark: Optional remark.
            operator_id: Operator user id.
        
        Returns:
            (success, message)
        """
        if amount < 0:
            return False, 'amount_cannot_be_negative'
        
        try:
            old_credits = self.get_user_credits(user_id)
            diff = Decimal(str(amount)) - old_credits
            
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute(
                    "UPDATE qd_users SET credits = ?, updated_at = NOW() WHERE id = ?",
                    (amount, user_id)
                )
                
                cur.execute(
                    """
                    INSERT INTO qd_credits_log 
                    (user_id, action, amount, balance_after, remark, operator_id, created_at)
                    VALUES (?, 'admin_adjust', ?, ?, ?, ?, NOW())
                    """,
                    (user_id, float(diff), amount, remark or f'Admin adjust: {old_credits} -> {amount}', operator_id)
                )
                
                db.commit()
                cur.close()
            
            logger.info(f"User {user_id} credits set to {amount} by admin {operator_id}")
            return True, str(amount)
            
        except Exception as e:
            logger.error(f"set_credits failed: {e}")
            return False, str(e)
    
    def set_vip(self, user_id: int, expires_at: Optional[datetime], 
                remark: str = '', operator_id: int = None) -> Tuple[bool, str]:
        """
        Set user VIP status.
        
        Args:
            user_id: User id.
            expires_at: VIP expiration time; None cancels VIP.
            remark: Optional remark.
            operator_id: Operator user id.
        
        Returns:
            (success, message)
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute(
                    "UPDATE qd_users SET vip_expires_at = ?, updated_at = NOW() WHERE id = ?",
                    (expires_at, user_id)
                )
                
                action = 'vip_grant' if expires_at else 'vip_revoke'
                log_remark = remark or (f'VIP granted until {expires_at}' if expires_at else 'VIP revoked')
                cur.execute(
                    """
                    INSERT INTO qd_credits_log 
                    (user_id, action, amount, balance_after, remark, operator_id, created_at)
                    VALUES (?, ?, 0, (SELECT credits FROM qd_users WHERE id = ?), ?, ?, NOW())
                    """,
                    (user_id, action, user_id, log_remark, operator_id)
                )
                
                db.commit()
                cur.close()
            
            logger.info(f"User {user_id} VIP set to {expires_at} by admin {operator_id}")
            return True, 'success'
            
        except Exception as e:
            logger.error(f"set_vip failed: {e}")
            return False, str(e)
    
    def get_credits_log(self, user_id: int, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """Return user credit change logs."""
        offset = (page - 1) * page_size
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute(
                    "SELECT COUNT(*) as count FROM qd_credits_log WHERE user_id = ?",
                    (user_id,)
                )
                total = cur.fetchone()['count']
                
                cur.execute(
                    """
                    SELECT id, action, amount, balance_after, feature, reference_id, remark, created_at
                    FROM qd_credits_log
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (user_id, page_size, offset)
                )
                rows = cur.fetchall() or []
                cur.close()

                # Format created_at as ISO 8601 with Z (UTC) for correct frontend display
                logs = []
                for r in rows:
                    d = dict(r)
                    if d.get('created_at'):
                        dt = d['created_at']
                        if hasattr(dt, 'isoformat'):
                            if getattr(dt, 'tzinfo', None) is not None:
                                d['created_at'] = dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + 'Z'
                            else:
                                d['created_at'] = dt.strftime('%Y-%m-%dT%H:%M:%S') + 'Z'
                    logs.append(d)
                
                return {
                    'items': logs,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size
                }
        except Exception as e:
            logger.error(f"get_credits_log failed: {e}")
            return {'items': [], 'total': 0, 'page': 1, 'page_size': page_size, 'total_pages': 0}
    
    def get_user_billing_info(self, user_id: int) -> Dict[str, Any]:
        """Return billing and membership snapshot for frontend display.

        ``is_lifetime`` is exposed so the billing page can render the VIP
        snapshot as "Lifetime member" (instead of an awkward 100-year
        expiry date) and so the page can show a hint that lifetime users
        can still purchase monthly/yearly plans as credit top-ups.
        """
        credits = self.get_user_credits(user_id)
        is_vip, vip_expires_at = self.get_user_vip_status(user_id)
        config = self.get_billing_config()

        is_lifetime = False
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT vip_is_lifetime FROM qd_users WHERE id = ?",
                    (user_id,),
                )
                row = cur.fetchone() or {}
                is_lifetime = bool(row.get("vip_is_lifetime") or False)
                cur.close()
        except Exception:
            # Column may not exist on very old schemas; treat as non-lifetime
            is_lifetime = False

        return {
            'credits': float(credits),
            'is_vip': is_vip,
            'is_lifetime': is_lifetime,
            'vip_expires_at': vip_expires_at.isoformat() if vip_expires_at else None,
            'billing_enabled': config.get('enabled', False),
            'feature_costs': {
                'backtest': config.get('cost_backtest', 30),
                'ai_review': config.get('cost_ai_review', 10),
                'ai_analysis': config.get('cost_ai_analysis', 0),
                'ai_code_gen': config.get('cost_ai_code_gen', 0),
                'ai_indicator_to_strategy': config.get('cost_ai_indicator_to_strategy', 0),
                'ai_tuning': config.get('cost_ai_tuning', 0),
                'ai_copilot_chat': config.get('cost_ai_copilot_chat', 0),
                'ai_copilot_image': config.get('cost_ai_copilot_image', 0),
                'ai_copilot_radar': config.get('cost_ai_copilot_radar', 0),
            }
        }


_billing_service = None


def get_billing_service() -> BillingService:
    """Return the singleton billing service."""
    global _billing_service
    if _billing_service is None:
        _billing_service = BillingService()
    return _billing_service
