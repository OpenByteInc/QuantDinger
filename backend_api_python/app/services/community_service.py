"""Community marketplace service."""
import json
import os
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List, Optional, Tuple

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.services.billing_service import get_billing_service
from app.services.community_kpis import (
    fetch_market_asset_kpis,
    parse_backtest_result,
    summarise_backtest_runs,
)
from app.services.indicator_translator import pick_localized

logger = get_logger(__name__)


def _strategy_contract_payload(
    manifest: Dict[str, Any],
    param_schema: Dict[str, Any],
    *,
    source: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(manifest, dict) or not manifest:
        return None

    universe = manifest.get('universe') if isinstance(manifest.get('universe'), dict) else {}
    instruments = universe.get('instruments') if isinstance(universe.get('instruments'), list) else []
    subscriptions = manifest.get('subscriptions') if isinstance(manifest.get('subscriptions'), list) else []
    parameters = param_schema.get('params') if isinstance(param_schema.get('params'), list) else []
    benchmark = manifest.get('benchmark') if isinstance(manifest.get('benchmark'), dict) else None

    normalized_parameters = []
    for item in parameters:
        if not isinstance(item, dict) or not str(item.get('name') or '').strip():
            continue
        normalized_parameters.append({
            'name': str(item.get('name') or ''),
            'label_key': str(item.get('labelKey') or item.get('label_key') or ''),
            'label': str(item.get('label') or ''),
            'type': str(item.get('type') or 'number'),
            'default': item.get('default'),
            'min': item.get('min'),
            'max': item.get('max'),
            'step': item.get('step'),
        })

    data_fields = []
    for subscription in subscriptions:
        if not isinstance(subscription, dict):
            continue
        for field in subscription.get('fields') or []:
            field_name = str(field or '').strip()
            if field_name and field_name not in data_fields:
                data_fields.append(field_name)

    return {
        'source': source,
        'api_version': int(manifest.get('apiVersion') or 2),
        'strategy_type': str(manifest.get('strategyType') or 'cta'),
        'primary_frequency': str(manifest.get('primaryFrequency') or ''),
        'driving_frequency': str(
            manifest.get('drivingFrequency') or manifest.get('primaryFrequency') or ''
        ),
        'frequencies': list(manifest.get('frequencies') or []),
        'markets': list(manifest.get('markets') or []),
        'universe_kind': str(universe.get('kind') or 'static'),
        'universe_reference': str(universe.get('reference') or ''),
        'instruments': [dict(item) for item in instruments if isinstance(item, dict)],
        'benchmark': dict(benchmark) if benchmark else None,
        'leverage_allowed': bool(manifest.get('leverageAllowed') or False),
        'max_leverage': float(manifest.get('maxLeverage') or 1.0),
        'warmup_bars': int(manifest.get('warmupBars') or 0),
        'factor_dependencies': list(manifest.get('factorDependencies') or []),
        'fundamental_dependencies': list(manifest.get('fundamentalDependencies') or []),
        'data_fields': data_fields,
        'parameters': normalized_parameters,
    }


def _marketplace_platform_fee_rate() -> Decimal:
    """Return marketplace platform fee as a 0..1 ratio.

    MARKETPLACE_PLATFORM_FEE_RATE accepts either ratios ("0.1") or percent
    strings ("10%"). Invalid values fall back to zero rather than blocking
    marketplace delivery.
    """
    raw = str(os.getenv("MARKETPLACE_PLATFORM_FEE_RATE", "0") or "0").strip()
    try:
        if raw.endswith("%"):
            rate = Decimal(raw[:-1].strip() or "0") / Decimal("100")
        else:
            rate = Decimal(raw)
    except Exception:
        logger.warning("Invalid MARKETPLACE_PLATFORM_FEE_RATE=%r; using 0", raw)
        return Decimal("0")
    if rate < 0:
        return Decimal("0")
    if rate > 1:
        return Decimal("1")
    return rate


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _split_marketplace_amounts(gross: Any) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    gross_amount = _money(gross)
    fee_rate = _marketplace_platform_fee_rate()
    platform_fee = _money(gross_amount * fee_rate)
    seller_amount = _money(gross_amount - platform_fee)
    return gross_amount, platform_fee, seller_amount, fee_rate


class CommunityService:
    """Marketplace service for indicators, script templates, purchases, and reviews."""
    
    def __init__(self):
        self.billing = get_billing_service()
    
    # ==========================================
    # ==========================================
    
    def get_market_indicators(
        self,
        page: int = 1,
        page_size: int = 12,
        keyword: str = None,
        pricing_type: str = None,  # 'free' / 'paid' / None(all)
        vip_free: bool = False,
        code_visibility: str = None,  # 'visible' / 'hidden' / None(all)
        min_price: float = None,
        max_price: float = None,
        sort_by: str = 'score',    # 'score' / 'newest' / 'hot' / 'price_asc' / 'price_desc' / 'rating'
        user_id: int = None,       # Current user id, used to mark purchased items.
        accept_language: str = 'en-US',  # Select name_i18n / description_i18n.
        asset_type: str = None,    # 'indicator' / 'script_template' / None(all)
    ) -> Dict[str, Any]:
        """获取市场上已发布的指标列表

        About ``sort_by='score'`` (the new default):
            The composite score lives in qd_backtest_runs.result_json, which
            is opaque to SQL. We can't ORDER BY it cheaply. Instead, when
            the caller asks for score-sorted results, we:
              1. Pull the *full set* of approved + published indicators
                 (id-only, very cheap row).
              2. Batch-compute their scores via fetch_market_asset_kpis.
              3. Sort by score in Python.
              4. Slice [offset:offset+page_size] and re-query the full row
                 for just that slice.

            For other sort_by values (newest / hot / price / rating), the
            sort can be done in SQL, so we keep the original cheap path
            and only batch-compute KPIs for the visible page.

            The trade-off: score-sort is O(N) per request in indicators
            count, but N here is "how many indicators have ever been
            published" — currently realistic in the low hundreds. If the
            community grows past ~5k we'll want to denormalise the score
            onto qd_indicator_codes via a periodic job; until then this
            is fine and saves a schema migration.
        """
        offset = (page - 1) * page_size

        try:
            with get_db_connection() as db:
                cur = db.cursor()

                where_clauses = ["i.publish_to_community = 1", "(i.review_status = 'approved' OR i.review_status IS NULL)"]
                params = []

                if keyword and keyword.strip():
                    where_clauses.append("(i.name ILIKE ? OR i.description ILIKE ?)")
                    search_term = f"%{keyword.strip()}%"
                    params.extend([search_term, search_term])

                if pricing_type == 'free':
                    where_clauses.append("(i.pricing_type = 'free' OR i.price <= 0)")
                elif pricing_type == 'paid':
                    where_clauses.append("(i.pricing_type != 'free' AND i.price > 0)")
                elif pricing_type == 'vip_free':
                    where_clauses.append("(COALESCE(i.vip_free, FALSE) = TRUE)")

                if vip_free:
                    where_clauses.append("(COALESCE(i.vip_free, FALSE) = TRUE)")

                if code_visibility == 'visible':
                    where_clauses.append("(COALESCE(i.is_encrypted, 0) = 0)")
                elif code_visibility == 'hidden':
                    where_clauses.append("(COALESCE(i.is_encrypted, 0) != 0)")

                if min_price is not None:
                    where_clauses.append("COALESCE(i.price, 0) >= ?")
                    params.append(float(min_price))

                if max_price is not None:
                    where_clauses.append("COALESCE(i.price, 0) <= ?")
                    params.append(float(max_price))

                # Count both marketplace categories before applying the active
                # category filter.  The UI can therefore show the number next
                # to both tabs without making a second listing request.  All
                # other active filters (keyword, price, visibility, etc.) are
                # intentionally reflected in these counts.
                category_where_sql = " AND ".join(where_clauses)
                cur.execute(
                    f"""
                    SELECT
                        SUM(CASE WHEN COALESCE(i.asset_type, 'indicator') = 'indicator' THEN 1 ELSE 0 END) AS indicator_count,
                        SUM(CASE WHEN COALESCE(i.asset_type, 'indicator') = 'script_template' THEN 1 ELSE 0 END) AS script_template_count
                    FROM qd_indicator_codes i
                    WHERE {category_where_sql}
                    """,
                    tuple(params),
                )
                category_count_row = cur.fetchone() or {}
                asset_type_counts = {
                    'indicator': int(category_count_row.get('indicator_count') or 0),
                    'script_template': int(category_count_row.get('script_template_count') or 0),
                }

                _allowed_asset_types = ('indicator', 'script_template')
                if asset_type and str(asset_type).strip() in _allowed_asset_types:
                    where_clauses.append("(COALESCE(i.asset_type, 'indicator') = ?)")
                    params.append(str(asset_type).strip())

                where_sql = " AND ".join(where_clauses)

                # SQL-friendly sorts:
                order_map = {
                    'newest': 'i.created_at DESC',
                    'hot': 'i.purchase_count DESC, i.view_count DESC',
                    'price_asc': 'i.price ASC, i.created_at DESC',
                    'price_desc': 'i.price DESC, i.created_at DESC',
                    'rating': 'i.avg_rating DESC, i.rating_count DESC'
                }

                count_sql = f"SELECT COUNT(*) as count FROM qd_indicator_codes i WHERE {where_sql}"
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()['count']

                if sort_by == 'score':
                    # Score sort path: fetch ALL matching ids, score them,
                    # sort in Python, then refetch full rows for the page.
                    cur.execute(
                        f"""
                        SELECT
                            i.id,
                            COALESCE(i.asset_type, 'indicator') as asset_type,
                            i.source_script_source_id,
                            i.source_strategy_id
                        FROM qd_indicator_codes i
                        WHERE {where_sql}
                        """,
                        tuple(params)
                    )
                    all_assets = [dict(r) for r in (cur.fetchall() or [])]
                    all_ids = [int(r['id']) for r in all_assets]
                    kpi_by_id = fetch_market_asset_kpis(cur, all_assets)
                    # Tie-break with created_at via id (newer id ≈ newer row)
                    # so deterministic ordering when many indicators score 0.
                    all_ids.sort(
                        key=lambda iid: (
                            -(kpi_by_id.get(iid, {}).get('score') or 0),
                            -iid
                        )
                    )
                    page_ids = all_ids[offset:offset + page_size]
                    if not page_ids:
                        cur.close()
                        return {
                            'items': [], 'total': total, 'page': page,
                            'page_size': page_size, 'total_pages': 0,
                            'asset_type_counts': asset_type_counts,
                        }
                    id_placeholders = ','.join(['?'] * len(page_ids))
                    cur.execute(f"""
                        SELECT
                            i.id, i.name, i.description, i.pricing_type, i.price, COALESCE(i.vip_free, FALSE) as vip_free,
                            COALESCE(i.is_encrypted, 0) as code_hidden,
                            COALESCE(i.asset_type, 'indicator') as asset_type,
                            i.source_script_source_id, i.source_strategy_id,
                            i.preview_image, i.purchase_count, i.avg_rating, i.rating_count,
                            i.view_count, i.created_at, i.updated_at,
                            i.source_language, i.name_i18n, i.description_i18n,
                            ss.description as source_description,
                            u.id as author_id, u.username as author_username,
                            u.nickname as author_nickname, u.avatar as author_avatar
                        FROM qd_indicator_codes i
                        LEFT JOIN qd_users u ON i.user_id = u.id
                        LEFT JOIN qd_script_sources ss ON ss.id = i.source_script_source_id
                        WHERE i.id IN ({id_placeholders})
                    """, tuple(page_ids))
                    rows_unordered = cur.fetchall() or []
                    # Preserve our score-sorted order even though SQL won't
                    by_id = {r['id']: r for r in rows_unordered}
                    rows = [by_id[iid] for iid in page_ids if iid in by_id]
                    page_kpis = {iid: kpi_by_id.get(iid, summarise_backtest_runs([])) for iid in page_ids}
                else:
                    order_sql = order_map.get(sort_by, 'i.created_at DESC')
                    query_sql = f"""
                        SELECT
                            i.id, i.name, i.description, i.pricing_type, i.price, COALESCE(i.vip_free, FALSE) as vip_free,
                            COALESCE(i.is_encrypted, 0) as code_hidden,
                            COALESCE(i.asset_type, 'indicator') as asset_type,
                            i.source_script_source_id, i.source_strategy_id,
                            i.preview_image, i.purchase_count, i.avg_rating, i.rating_count,
                            i.view_count, i.created_at, i.updated_at,
                            i.source_language, i.name_i18n, i.description_i18n,
                            ss.description as source_description,
                            u.id as author_id, u.username as author_username,
                            u.nickname as author_nickname, u.avatar as author_avatar
                        FROM qd_indicator_codes i
                        LEFT JOIN qd_users u ON i.user_id = u.id
                        LEFT JOIN qd_script_sources ss ON ss.id = i.source_script_source_id
                        WHERE {where_sql}
                        ORDER BY {order_sql}
                        LIMIT ? OFFSET ?
                    """
                    cur.execute(query_sql, tuple(params + [page_size, offset]))
                    rows = cur.fetchall() or []
                    page_kpis = fetch_market_asset_kpis(cur, [dict(r) for r in rows])

                purchased_ids = set()
                if user_id:
                    indicator_ids = [r['id'] for r in rows]
                    if indicator_ids:
                        placeholders = ','.join(['?'] * len(indicator_ids))
                        cur.execute(
                            f"SELECT indicator_id FROM qd_indicator_purchases WHERE buyer_id = ? AND indicator_id IN ({placeholders})",
                            tuple([user_id] + indicator_ids)
                        )
                        purchased_ids = {r['indicator_id'] for r in (cur.fetchall() or [])}

                cur.close()

                items = []
                for row in rows:
                    kpi = page_kpis.get(row['id'], summarise_backtest_runs([]))
                    row_asset_type = (row.get('asset_type') if isinstance(row, dict) else None) or 'indicator'
                    raw_description = row['description'] or ''
                    if str(row_asset_type).strip().lower() == 'script_template' and not raw_description:
                        raw_description = row.get('source_description') or ''
                    _src_lang = row.get('source_language') if isinstance(row, dict) else None
                    localized_name = pick_localized(
                        row['name'],
                        row.get('name_i18n') if isinstance(row, dict) else None,
                        accept_language,
                        _src_lang,
                    )
                    if str(row_asset_type).strip().lower() == 'script_template':
                        localized_desc = raw_description
                    else:
                        localized_desc = pick_localized(
                            raw_description,
                            row.get('description_i18n') if isinstance(row, dict) else None,
                            accept_language,
                            _src_lang,
                        )
                    items.append({
                        'id': row['id'],
                        'name': localized_name,
                        'description': localized_desc[:200] if localized_desc else '',
                        'asset_type': row_asset_type,
                        'pricing_type': row['pricing_type'] or 'free',
                        'price': float(row['price'] or 0),
                        'vip_free': bool(row.get('vip_free') or False),
                        'code_hidden': bool(row.get('code_hidden') or False),
                        'preview_image': row['preview_image'] or '',
                        'purchase_count': row['purchase_count'] or 0,
                        'avg_rating': float(row['avg_rating'] or 0),
                        'rating_count': row['rating_count'] or 0,
                        'view_count': row['view_count'] or 0,
                        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                        'author': {
                            'id': row['author_id'],
                            'username': row['author_username'],
                            'nickname': row['author_nickname'] or row['author_username'],
                            'avatar': row['author_avatar'] or '/avatar2.jpg'
                        },
                        'is_purchased': row['id'] in purchased_ids,
                        'is_own': row['author_id'] == user_id,
                        # Backtest-derived KPIs and applicability hints.
                        # All fields are guaranteed present even when an
                        # asset has no backtest samples; values degrade to
                        # 0 / empty lists.
                        'score': kpi['score'],
                        'total_return': kpi['total_return'],
                        'annual_return': kpi['annual_return'],
                        'sharpe': kpi['sharpe'],
                        'max_drawdown': kpi['max_drawdown'],
                        'win_rate_backtest': kpi['win_rate'],
                        'profit_factor': kpi['profit_factor'],
                        'sample_size': kpi['sample_size'],
                        'applicable_symbols': kpi['symbols'],
                        'applicable_timeframes': kpi['timeframes'],
                    })

                return {
                    'items': items,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size if total > 0 else 0,
                    'asset_type_counts': asset_type_counts,
                }

        except Exception as e:
            logger.error(f"get_market_indicators failed: {e}")
            return {
                'items': [], 'total': 0, 'page': 1, 'page_size': page_size,
                'total_pages': 0,
                'asset_type_counts': {'indicator': 0, 'script_template': 0},
            }

    def publish_script_template_from_strategy(
        self,
        *,
        user_id: int,
        strategy_id: int,
        code: str,
        name: str,
        description: str = '',
        pricing_type: str = 'free',
        price: float = 0.0,
        vip_free: bool = False,
        code_hidden: bool = False,
        is_admin: bool = False,
        existing_indicator_id: int = 0,
        source_id: int = 0,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Publish Strategy API V2 code to the marketplace as a script template."""
        code = (code or '').strip()
        name = (name or '').strip()
        if not code:
            return False, 'code is required', None
        if not name:
            return False, 'name is required', None

        pricing_type = (pricing_type or 'free').strip() or 'free'
        try:
            price = float(price or 0)
        except Exception:
            price = 0.0

        review_status = 'approved' if is_admin else 'pending'
        now_ts = int(time.time())

        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if existing_indicator_id and existing_indicator_id > 0:
                    cur.execute(
                        """
                        SELECT id FROM qd_indicator_codes
                        WHERE id = ? AND user_id = ? AND COALESCE(asset_type, 'indicator') = 'script_template'
                        """,
                        (existing_indicator_id, user_id),
                    )
                    if not cur.fetchone():
                        cur.close()
                        return False, 'template not found', None
                    cur.execute(
                        """
                        UPDATE qd_indicator_codes
                        SET name = ?, code = ?, description = ?,
                            publish_to_community = 1, pricing_type = ?, price = ?,
                            is_encrypted = ?, vip_free = ?,
                            asset_type = 'script_template',
                            source_script_source_id = ?, source_strategy_id = ?,
                            source_language = NULL, name_i18n = NULL, description_i18n = NULL,
                            review_status = ?, review_note = '', reviewed_at = NOW(), reviewed_by = ?,
                            updatetime = ?, updated_at = NOW()
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            name, code, description, pricing_type, price,
                            1 if code_hidden else 0, bool(vip_free),
                            int(source_id or 0) or None, int(strategy_id or 0) or None,
                            review_status, user_id if is_admin else None,
                            now_ts, existing_indicator_id, user_id,
                        ),
                    )
                    indicator_id = existing_indicator_id
                else:
                    cur.execute(
                        """
                        INSERT INTO qd_indicator_codes
                          (user_id, is_buy, end_time, name, code, description,
                           publish_to_community, pricing_type, price, is_encrypted, vip_free, asset_type,
                           source_script_source_id, source_strategy_id, review_status,
                           source_language, name_i18n, description_i18n,
                           createtime, updatetime, created_at, updated_at)
                        VALUES (?, 0, 1, ?, ?, ?, 1, ?, ?, ?, ?, 'script_template', ?, ?, ?, NULL, NULL, NULL, ?, ?, NOW(), NOW())
                        """,
                        (
                            user_id, name, code, description, pricing_type, price,
                            1 if code_hidden else 0, bool(vip_free),
                            int(source_id or 0) or None, int(strategy_id or 0) or None,
                            review_status, now_ts, now_ts,
                        ),
                    )
                    indicator_id = int(cur.lastrowid or 0)

                db.commit()
                cur.close()

            return True, 'success', {
                'indicator_id': indicator_id,
                'review_status': review_status,
                'asset_type': 'script_template',
                'strategy_id': strategy_id,
                'source_id': int(source_id or 0),
            }
        except Exception as e:
            logger.error(f"publish_script_template_from_strategy failed: {e}")
            return False, str(e), None

    @staticmethod
    def _parse_json_dict(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    def get_indicator_detail(
        self,
        indicator_id: int,
        user_id: int = None,
        accept_language: str = 'en-US',
    ) -> Optional[Dict[str, Any]]:
        """获取指标详情。

        ``accept_language`` 用于挑选 i18n 字段。未提供时退回 en-US。
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute("""
                    SELECT 
                        i.id, i.name, i.description, i.pricing_type, i.price, COALESCE(i.vip_free, FALSE) as vip_free,
                        i.preview_image, i.purchase_count, i.avg_rating, i.rating_count,
                        i.view_count, i.publish_to_community, i.created_at, i.updated_at,
                        i.user_id, i.review_status, COALESCE(i.is_encrypted, 0) as is_encrypted,
                        COALESCE(i.asset_type, 'indicator') as asset_type,
                        i.source_language, i.name_i18n, i.description_i18n,
                        ss.description as source_description,
                        u.id as author_id, u.username as author_username, 
                        u.nickname as author_nickname, u.avatar as author_avatar
                    FROM qd_indicator_codes i
                    LEFT JOIN qd_users u ON i.user_id = u.id
                    LEFT JOIN qd_script_sources ss ON ss.id = i.source_script_source_id
                    WHERE i.id = ?
                """, (indicator_id,))
                row = cur.fetchone()
                
                if not row:
                    cur.close()
                    return None
                
                is_owner = row['user_id'] == user_id
                is_approved = row.get('review_status') in (None, '', 'approved')
                if not is_owner and (not row['publish_to_community'] or not is_approved):
                    cur.close()
                    return None
                
                # We also pull `price` from the purchase row so the frontend can
                # show the buyer their *actual paid amount* (which can differ
                # from the indicator's current price after a discount / price
                # hike). The current price still lives in `row['price']`.
                is_purchased = False
                your_purchase_price = None
                your_purchase_time = None
                your_platform_fee = 0.0
                your_seller_amount = 0.0
                your_fee_rate = 0.0
                has_update = False
                local_copy_id = None
                purchased_strategy_id = None
                purchased_script_source_id = None
                local_copy_missing = False
                if user_id:
                    cur.execute(
                        "SELECT id, price, COALESCE(gross_price, price) AS gross_price, "
                        "COALESCE(platform_fee, 0) AS platform_fee, "
                        "COALESCE(seller_amount, price) AS seller_amount, "
                        "COALESCE(fee_rate, 0) AS fee_rate, created_at FROM qd_indicator_purchases "
                        "WHERE indicator_id = ? AND buyer_id = ? ORDER BY id DESC LIMIT 1",
                        (indicator_id, user_id)
                    )
                    purchase_row = cur.fetchone()
                    is_purchased = purchase_row is not None
                    if is_purchased:
                        try:
                            your_purchase_price = float(purchase_row.get('gross_price') or purchase_row['price'] or 0)
                        except (TypeError, ValueError):
                            your_purchase_price = 0.0
                        your_platform_fee = float(purchase_row.get('platform_fee') or 0)
                        your_seller_amount = float(purchase_row.get('seller_amount') or your_purchase_price or 0)
                        your_fee_rate = float(purchase_row.get('fee_rate') or 0)
                        if purchase_row.get('created_at'):
                            your_purchase_time = purchase_row['created_at'].isoformat()
                        asset_type = str(row.get('asset_type') or 'indicator').strip().lower()
                        if asset_type == 'script_template':
                            cur.execute(
                                """
                                SELECT id, code, metadata FROM qd_script_sources
                                WHERE user_id = ? AND source_marketplace_indicator_id = ?
                                ORDER BY id DESC LIMIT 1
                                """,
                                (user_id, indicator_id),
                            )
                            source = cur.fetchone()
                            if source:
                                purchased_script_source_id = source['id']
                                cur.execute(
                                    "SELECT code, is_encrypted FROM qd_indicator_codes WHERE id = ?",
                                    (indicator_id,)
                                )
                                original_row = cur.fetchone()
                                original_code = original_row['code'] if original_row else None
                                local_code = source.get('code')
                                local_meta = self._parse_trading_config_json(source.get('metadata'))
                                has_update = (
                                    (original_code or '') != (local_code or '')
                                    or bool(local_meta.get('code_hidden') or local_meta.get('hide_code') or False)
                                    != bool((original_row or {}).get('is_encrypted') or 0)
                                )
                            else:
                                local_copy_missing = True
                        else:
                            local_copy = self._find_buyer_local_copy(
                                cur, buyer_id=user_id, indicator_id=indicator_id
                            )
                            if local_copy is not None:
                                local_copy_id = local_copy['id']
                                cur.execute(
                                    "SELECT code FROM qd_indicator_codes WHERE id = ?",
                                    (indicator_id,)
                                )
                                original_row = cur.fetchone()
                                original_code = original_row['code'] if original_row else None
                                local_code = local_copy.get('code')
                                has_update = (original_code or '') != (local_code or '')
                            else:
                                local_copy_missing = True

                cur.execute(
                    "UPDATE qd_indicator_codes SET view_count = COALESCE(view_count, 0) + 1 WHERE id = ?",
                    (indicator_id,)
                )
                db.commit()
                cur.close()
                
                _src_lang = row.get('source_language') if isinstance(row, dict) else None
                localized_name = pick_localized(
                    row['name'], row.get('name_i18n'), accept_language, _src_lang,
                )
                detail_asset_type = str(row.get('asset_type') or 'indicator')
                raw_description = row['description'] or ''
                if detail_asset_type.strip().lower() == 'script_template' and not raw_description:
                    raw_description = row.get('source_description') or ''
                if detail_asset_type.strip().lower() == 'script_template':
                    localized_desc = raw_description
                else:
                    localized_desc = pick_localized(
                        raw_description, row.get('description_i18n'), accept_language, _src_lang,
                    )

                return {
                    'id': row['id'],
                    'name': localized_name,
                    'description': localized_desc or '',
                    'pricing_type': row['pricing_type'] or 'free',
                    'price': float(row['price'] or 0),
                    'vip_free': bool(row.get('vip_free') or False),
                    'code_hidden': bool(row.get('is_encrypted') or 0),
                    'code_visible': not bool(row.get('is_encrypted') or 0),
                    'preview_image': row['preview_image'] or '',
                    'purchase_count': row['purchase_count'] or 0,
                    'avg_rating': float(row['avg_rating'] or 0),
                    'rating_count': row['rating_count'] or 0,
                    'view_count': (row['view_count'] or 0) + 1,
                    'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                    'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
                    'author': {
                        'id': row['author_id'],
                        'username': row['author_username'],
                        'nickname': row['author_nickname'] or row['author_username'],
                        'avatar': row['author_avatar'] or '/avatar2.jpg'
                    },
                    'is_purchased': is_purchased,
                    'your_purchase_price': your_purchase_price,
                    'your_platform_fee': your_platform_fee,
                    'your_seller_amount': your_seller_amount,
                    'your_fee_rate': your_fee_rate,
                    'your_purchase_time': your_purchase_time,
                    'is_own': row['user_id'] == user_id,
                    'has_update': has_update,
                    'local_copy_missing': bool(local_copy_missing),
                    'local_copy_id': local_copy_id,
                    'asset_type': detail_asset_type,
                    'purchased_strategy_id': purchased_strategy_id,
                    'script_source_id': purchased_script_source_id,
                }
                
        except Exception as e:
            logger.error(f"get_indicator_detail failed: {e}")
            return None
    
    # ==========================================
    # ==========================================
    
    def purchase_indicator(self, buyer_id: int, indicator_id: int) -> Tuple[bool, str, Dict[str, Any]]:
        """
        购买指标
        
        Returns:
            (success, message, data)
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute("""
                    SELECT id, user_id, name, code, description, pricing_type, price, COALESCE(vip_free, FALSE) as vip_free,
                           preview_image, is_encrypted,
                           COALESCE(asset_type, 'indicator') as asset_type
                    FROM qd_indicator_codes
                    WHERE id = ? AND publish_to_community = 1
                      AND (review_status = 'approved' OR review_status IS NULL)
                """, (indicator_id,))
                indicator = cur.fetchone()
                
                if not indicator:
                    cur.close()
                    return False, 'indicator_not_found', {}
                
                seller_id = indicator['user_id']
                price = float(indicator['price'] or 0)
                pricing_type = indicator['pricing_type'] or 'free'
                vip_free = bool(indicator.get('vip_free') or False)
                asset_type = str(indicator.get('asset_type') or 'indicator').strip().lower()
                is_vip, _ = self.billing.get_user_vip_status(buyer_id)
                billing_enabled = self.billing.is_billing_enabled()

                # Global billing-off means marketplace delivery remains available
                # but no buyer/seller credit movement is recorded.
                effective_price = 0.0 if ((not billing_enabled) or (vip_free and is_vip)) else price
                gross_price, platform_fee, seller_amount, fee_rate = _split_marketplace_amounts(effective_price)
                
                if seller_id == buyer_id:
                    cur.close()
                    return False, 'cannot_buy_own', {}
                
                cur.execute(
                    "SELECT id FROM qd_indicator_purchases WHERE indicator_id = ? AND buyer_id = ?",
                    (indicator_id, buyer_id)
                )
                if cur.fetchone():
                    cur.close()
                    return False, 'already_purchased', {}
                
                if pricing_type != 'free' and gross_price > 0:
                    buyer_credits = self.billing.get_user_credits(buyer_id)
                    if buyer_credits < gross_price:
                        cur.close()
                        return False, 'insufficient_credits', {
                            'required': float(gross_price),
                            'current': float(buyer_credits)
                        }
                    
                    new_buyer_balance = buyer_credits - gross_price
                    cur.execute(
                        "UPDATE qd_users SET credits = ?, updated_at = NOW() WHERE id = ?",
                        (float(new_buyer_balance), buyer_id)
                    )
                    
                    cur.execute("""
                        INSERT INTO qd_credits_log 
                        (user_id, action, amount, balance_after, feature, reference_id, remark, created_at)
                        VALUES (?, 'indicator_purchase', ?, ?, 'indicator_purchase', ?, ?, NOW())
                    """, (
                        buyer_id,
                        -float(gross_price),
                        float(new_buyer_balance),
                        str(indicator_id),
                        f"Marketplace purchase: {indicator['name']}",
                    ))
                    
                    seller_credits = self.billing.get_user_credits(seller_id)
                    new_seller_balance = seller_credits + seller_amount
                    cur.execute(
                        "UPDATE qd_users SET credits = ?, updated_at = NOW() WHERE id = ?",
                        (float(new_seller_balance), seller_id)
                    )
                    
                    cur.execute("""
                        INSERT INTO qd_credits_log 
                        (user_id, action, amount, balance_after, feature, reference_id, remark, created_at)
                        VALUES (?, 'indicator_sale', ?, ?, 'indicator_sale', ?, ?, NOW())
                    """, (
                        seller_id,
                        float(seller_amount),
                        float(new_seller_balance),
                        str(indicator_id),
                        f"Marketplace sale: {indicator['name']} (gross={gross_price}, fee={platform_fee})",
                    ))
                
                cur.execute("""
                    INSERT INTO qd_indicator_purchases
                    (indicator_id, buyer_id, seller_id, price, gross_price, platform_fee, seller_amount, fee_rate,
                     asset_name_snapshot, asset_description_snapshot, asset_code_snapshot, asset_type_snapshot,
                     asset_preview_image_snapshot, asset_is_encrypted_snapshot, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
                """, (
                    indicator_id,
                    buyer_id,
                    seller_id,
                    float(gross_price),
                    float(gross_price),
                    float(platform_fee),
                    float(seller_amount),
                    float(fee_rate),
                    indicator['name'],
                    indicator.get('description') or '',
                    indicator.get('code') or '',
                    asset_type,
                    indicator.get('preview_image') or '',
                    indicator.get('is_encrypted') or 0,
                ))
                
                delivered_strategy_id = None
                delivered_source_id = None
                if asset_type == 'script_template':
                    from app.services.script_source import get_script_source_service
                    delivered_source_id = get_script_source_service().create_from_marketplace_asset(
                        buyer_id,
                        {
                            'id': indicator_id,
                            'name': indicator['name'],
                            'description': indicator['description'],
                            'code': indicator['code'],
                            'is_encrypted': indicator.get('is_encrypted') or 0,
                        },
                    )
                else:
                    now_ts = int(time.time())
                    # Get vip_free as boolean from indicator
                    vip_free_value = bool(indicator.get('vip_free') or False)
                    cur.execute("""
                        INSERT INTO qd_indicator_codes
                        (user_id, is_buy, end_time, name, code, description,
                         publish_to_community, pricing_type, price, is_encrypted, preview_image, vip_free,
                         source_indicator_id,
                         createtime, updatetime, created_at, updated_at)
                        VALUES (?, 1, 0, ?, ?, ?, 0, 'free', 0, ?, ?, ?, ?, ?, ?, NOW(), NOW())
                    """, (
                        buyer_id,
                        indicator['name'],
                        indicator['code'],
                        indicator['description'],
                        indicator['is_encrypted'] or 0,
                        indicator['preview_image'],
                        vip_free_value,  # Use boolean value instead of integer 0
                        indicator_id,  # source_indicator_id — link back to the original
                        now_ts, now_ts
                    ))
                
                cur.execute("""
                    UPDATE qd_indicator_codes 
                    SET purchase_count = COALESCE(purchase_count, 0) + 1 
                    WHERE id = ?
                """, (indicator_id,))
                
                db.commit()
                cur.close()
                
                logger.info(
                    "User %s purchased marketplace asset %s for gross=%s fee=%s seller=%s credits "
                    "(vip_free=%s, is_vip=%s)",
                    buyer_id, indicator_id, gross_price, platform_fee, seller_amount, vip_free, is_vip,
                )
                return True, 'success', {
                    'indicator_name': indicator['name'],
                    'price': price,
                    'charged': float(gross_price),
                    'gross_price': float(gross_price),
                    'platform_fee': float(platform_fee),
                    'seller_amount': float(seller_amount),
                    'fee_rate': float(fee_rate),
                    'billing_enabled': billing_enabled,
                    'vip_free': vip_free,
                    'asset_type': asset_type,
                    'strategy_id': delivered_strategy_id,
                    'script_source_id': delivered_source_id,
                }
                
        except Exception as e:
            logger.error(f"purchase_indicator failed: {e}")
            return False, f'error: {str(e)}', {}
    
    # ------------------------------------------------------------------
    # Local copy lookup / sync helpers
    # ------------------------------------------------------------------

    def _find_buyer_local_copy(self, cur, buyer_id: int, indicator_id: int) -> Optional[Dict[str, Any]]:
        """Find a buyer's local copy by its canonical marketplace source ID."""
        cur.execute(
            """
            SELECT id, name, code, is_encrypted
            FROM qd_indicator_codes
            WHERE user_id = ? AND source_indicator_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (buyer_id, indicator_id)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            'id': row['id'],
            'name': row['name'],
            'code': row.get('code'),
            'is_encrypted': row.get('is_encrypted'),
        }

    def _parse_trading_config_json(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    def _restore_script_template_copy(self, buyer_id: int, original: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.script_source import get_script_source_service
        source_id = get_script_source_service().create_from_marketplace_asset(
            buyer_id,
            {
                'id': original['id'],
                'name': original['name'],
                'description': original.get('description') or '',
                'code': original.get('code') or '',
                'is_encrypted': original.get('is_encrypted') or 0,
            },
        )
        return {
            'script_source_id': source_id,
            'updated': True,
            'restored': True,
            'indicator_name': original['name'],
        }

    def _restore_indicator_copy(self, cur, buyer_id: int, original: Dict[str, Any]) -> Dict[str, Any]:
        now_ts = int(time.time())
        cur.execute("""
            INSERT INTO qd_indicator_codes
            (user_id, is_buy, end_time, name, code, description,
             publish_to_community, pricing_type, price, is_encrypted, preview_image, vip_free,
            source_indicator_id,
             createtime, updatetime, created_at, updated_at)
            VALUES (?, 1, 0, ?, ?, ?, 0, 'free', 0, ?, ?, ?, ?, ?, ?, NOW(), NOW())
            RETURNING id
        """, (
            buyer_id,
            original['name'],
            original.get('code') or '',
            original.get('description') or '',
            original.get('is_encrypted') or 0,
            original.get('preview_image') or '',
            bool(original.get('vip_free') or False),
            original['id'],
            now_ts, now_ts,
        ))
        row = cur.fetchone()
        return {
            'local_copy_id': row['id'] if row else cur.lastrowid,
            'updated': True,
            'restored': True,
            'indicator_name': original['name'],
        }

    def _asset_from_purchase_snapshot(self, indicator_id: int, purchase_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build a restorable marketplace asset from the buyer's purchase-time snapshot."""
        if not purchase_row:
            return None
        code = purchase_row.get('asset_code_snapshot')
        name = purchase_row.get('asset_name_snapshot')
        if code is None or name is None:
            return None
        return {
            'id': indicator_id,
            'name': name or 'Purchased Asset',
            'description': purchase_row.get('asset_description_snapshot') or '',
            'code': code or '',
            'preview_image': purchase_row.get('asset_preview_image_snapshot') or '',
            'is_encrypted': purchase_row.get('asset_is_encrypted_snapshot') or 0,
            'vip_free': False,
            'asset_type': (purchase_row.get('asset_type_snapshot') or 'indicator'),
        }

    def sync_purchased_indicator(self, buyer_id: int, indicator_id: int) -> Tuple[bool, str, Dict[str, Any]]:
        """Refresh a buyer's local copy with the publisher's latest code/description.

        The user must have already purchased ``indicator_id`` for this to succeed.
        The buyer's local copy, linked by ``source_indicator_id``, will be
        overwritten with the publisher's current content.

        If the original indicator has been unpublished/removed or the buyer's
        local copy no longer exists (e.g. user deleted it), a recoverable error
        is returned so the UI can explain what to do next.
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()

                # 1. Must have purchased this indicator
                cur.execute(
                    """
                    SELECT id, price,
                           asset_name_snapshot, asset_description_snapshot, asset_code_snapshot,
                           asset_type_snapshot, asset_preview_image_snapshot, asset_is_encrypted_snapshot
                    FROM qd_indicator_purchases
                    WHERE indicator_id = ? AND buyer_id = ?
                    """,
                    (indicator_id, buyer_id)
                )
                purchase_row = cur.fetchone()
                if not purchase_row:
                    cur.close()
                    return False, 'not_purchased', {}

                # 2. Fetch the (still-published) original
                cur.execute(
                    """
                    SELECT id, user_id, name, code, description, preview_image, is_encrypted,
                           COALESCE(pricing_type, 'free') as pricing_type,
                           COALESCE(vip_free, FALSE) as vip_free,
                           publish_to_community, review_status, updated_at,
                           COALESCE(asset_type, 'indicator') as asset_type
                    FROM qd_indicator_codes
                    WHERE id = ?
                    """,
                    (indicator_id,)
                )
                original = cur.fetchone()
                if not original:
                    snapshot = self._asset_from_purchase_snapshot(indicator_id, purchase_row)
                    if not snapshot:
                        cur.close()
                        return False, 'indicator_not_found', {}
                    asset_type = str(snapshot.get('asset_type') or 'indicator').strip().lower()
                    if asset_type == 'script_template':
                        cur.execute(
                            """
                            SELECT id
                            FROM qd_script_sources
                            WHERE user_id = ? AND source_marketplace_indicator_id = ?
                            ORDER BY id DESC LIMIT 1
                            """,
                            (buyer_id, indicator_id),
                        )
                        local_source = cur.fetchone()
                        if local_source:
                            cur.close()
                            return True, 'listing_deleted_no_update', {
                                'script_source_id': local_source['id'],
                                'updated': False,
                            }
                        data = self._restore_script_template_copy(buyer_id, snapshot)
                        cur.close()
                        return True, 'restored', data
                    local = self._find_buyer_local_copy(
                        cur, buyer_id=buyer_id, indicator_id=indicator_id
                    )
                    if local:
                        cur.close()
                        return True, 'listing_deleted_no_update', {
                            'local_copy_id': local['id'],
                            'updated': False,
                        }
                    data = self._restore_indicator_copy(cur, buyer_id, snapshot)
                    db.commit()
                    cur.close()
                    return True, 'restored', data
                if not original.get('publish_to_community'):
                    snapshot = self._asset_from_purchase_snapshot(indicator_id, purchase_row)
                    asset_type = str((snapshot or original).get('asset_type') or 'indicator').strip().lower()
                    if asset_type == 'script_template':
                        cur.execute(
                            """
                            SELECT id
                            FROM qd_script_sources
                            WHERE user_id = ? AND source_marketplace_indicator_id = ?
                            ORDER BY id DESC LIMIT 1
                            """,
                            (buyer_id, indicator_id),
                        )
                        local_source = cur.fetchone()
                        if local_source:
                            cur.close()
                            return True, 'listing_unpublished_no_update', {
                                'script_source_id': local_source['id'],
                                'updated': False,
                            }
                        if snapshot:
                            data = self._restore_script_template_copy(buyer_id, snapshot)
                            cur.close()
                            return True, 'restored', data
                    else:
                        local = self._find_buyer_local_copy(
                            cur, buyer_id=buyer_id, indicator_id=indicator_id
                        )
                        if local:
                            cur.close()
                            return True, 'listing_unpublished_no_update', {
                                'local_copy_id': local['id'],
                                'updated': False,
                            }
                        if snapshot:
                            data = self._restore_indicator_copy(cur, buyer_id, snapshot)
                            db.commit()
                            cur.close()
                            return True, 'restored', data
                    cur.close()
                    return False, 'indicator_unpublished', {}
                if original.get('review_status') not in (None, '', 'approved'):
                    cur.close()
                    return False, 'indicator_unavailable', {}

                pricing_type = str(original.get('pricing_type') or 'free').strip().lower()
                is_vip_grant = pricing_type == 'paid' and bool(original.get('vip_free') or False) and float(purchase_row.get('price') or 0) <= 0
                if is_vip_grant:
                    is_vip, _ = self.billing.get_user_vip_status(buyer_id)
                    if not is_vip:
                        cur.close()
                        return False, 'vip_expired', {}

                asset_type = str(original.get('asset_type') or 'indicator').strip().lower()
                if asset_type == 'script_template':
                    cur.execute(
                        """
                        SELECT id, code, metadata
                        FROM qd_script_sources
                        WHERE user_id = ? AND source_marketplace_indicator_id = ?
                        ORDER BY id DESC LIMIT 1
                        """,
                        (buyer_id, indicator_id),
                    )
                    local_source = cur.fetchone()
                    if not local_source:
                        data = self._restore_script_template_copy(buyer_id, original)
                        cur.close()
                        return True, 'restored', data
                    metadata = self._parse_trading_config_json(local_source.get('metadata'))
                    original_hidden = bool(original.get('is_encrypted') or 0)
                    local_hidden = bool(metadata.get('code_hidden') or metadata.get('hide_code') or False)
                    if (local_source.get('code') or '') == (original.get('code') or '') and local_hidden == original_hidden:
                        cur.close()
                        return True, 'already_latest', {
                            'script_source_id': local_source['id'],
                            'updated': False,
                        }
                    metadata['code_hidden'] = original_hidden
                    metadata['from_marketplace'] = True
                    metadata['asset_type'] = 'script_template'
                    cur.execute(
                        """
                        UPDATE qd_script_sources
                        SET code = ?, name = ?, description = ?, metadata = ?::jsonb, updated_at = NOW()
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            original['code'],
                            original['name'],
                            original.get('description') or '',
                            json.dumps(metadata, ensure_ascii=False),
                            local_source['id'],
                            buyer_id,
                        ),
                    )
                    db.commit()
                    cur.close()
                    return True, 'success', {
                        'script_source_id': local_source['id'],
                        'updated': True,
                        'indicator_name': original['name'],
                    }

                # 3. Locate buyer's local copy (indicator assets)
                local = self._find_buyer_local_copy(
                    cur, buyer_id=buyer_id, indicator_id=indicator_id
                )
                if not local:
                    data = self._restore_indicator_copy(cur, buyer_id, original)
                    db.commit()
                    cur.close()
                    return True, 'restored', data

                # 4. Short-circuit when already identical
                if (local.get('code') or '') == (original.get('code') or ''):
                    cur.close()
                    return True, 'already_latest', {
                        'local_copy_id': local['id'],
                        'updated': False
                    }

                # 5. Overwrite the local copy with the latest publisher content
                now_ts = int(time.time())
                try:
                    from app.services.indicator_versions import insert_indicator_version
                    insert_indicator_version(
                        cur,
                        int(local['id']),
                        int(buyer_id),
                        str(local.get('name') or original['name'] or ''),
                        str(original.get('description') or ''),
                        str(local.get('code') or ''),
                    )
                except Exception as version_exc:
                    logger.warning(f"Failed to snapshot local indicator before sync: {version_exc}")
                cur.execute(
                    """
                    UPDATE qd_indicator_codes
                    SET code = ?,
                        description = ?,
                        preview_image = ?,
                        is_encrypted = ?,
                        source_indicator_id = ?,
                        updatetime = ?,
                        updated_at = NOW()
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        original['code'],
                        original['description'],
                        original['preview_image'],
                        original['is_encrypted'] or 0,
                        indicator_id,
                        now_ts,
                        local['id'],
                        buyer_id,
                    )
                )
                try:
                    from app.services.indicator_versions import insert_indicator_version
                    insert_indicator_version(
                        cur,
                        int(local['id']),
                        int(buyer_id),
                        str(original['name'] or ''),
                        str(original.get('description') or ''),
                        str(original.get('code') or ''),
                    )
                except Exception as version_exc:
                    logger.warning(f"Failed to record synced indicator version: {version_exc}")
                db.commit()
                cur.close()

                logger.info(
                    f"User {buyer_id} synced local indicator {local['id']} "
                    f"from published indicator {indicator_id}"
                )
                return True, 'success', {
                    'local_copy_id': local['id'],
                    'updated': True,
                    'indicator_name': original['name']
                }

        except Exception as e:
            logger.error(f"sync_purchased_indicator failed: {e}")
            return False, f'error: {str(e)}', {}

    def get_my_purchases(self, user_id: int, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """Return marketplace assets purchased by a user."""
        offset = (page - 1) * page_size
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute(
                    "SELECT COUNT(*) as count FROM qd_indicator_purchases WHERE buyer_id = ?",
                    (user_id,)
                )
                total = cur.fetchone()['count']
                
                cur.execute("""
                    SELECT 
                        p.id as purchase_id, p.price as purchase_price,
                        COALESCE(p.gross_price, p.price) as gross_price,
                        COALESCE(p.platform_fee, 0) as platform_fee,
                        COALESCE(p.seller_amount, p.price) as seller_amount,
                        COALESCE(p.fee_rate, 0) as fee_rate,
                        p.created_at as purchase_time,
                        p.indicator_id,
                        i.id as live_indicator_id,
                        COALESCE(i.name, p.asset_name_snapshot) as name,
                        COALESCE(i.description, p.asset_description_snapshot, '') as description,
                        COALESCE(i.preview_image, p.asset_preview_image_snapshot, '') as preview_image,
                        i.avg_rating,
                        COALESCE(i.pricing_type, 'free') as pricing_type,
                        COALESCE(i.price, p.gross_price, p.price, 0) as price,
                        COALESCE(i.vip_free, FALSE) as vip_free,
                        COALESCE(i.asset_type, p.asset_type_snapshot, 'indicator') as asset_type,
                        u.nickname as seller_nickname, u.avatar as seller_avatar
                    FROM qd_indicator_purchases p
                    LEFT JOIN qd_indicator_codes i ON p.indicator_id = i.id
                    LEFT JOIN qd_users u ON p.seller_id = u.id
                    WHERE p.buyer_id = ?
                    ORDER BY p.created_at DESC
                    LIMIT ? OFFSET ?
                """, (user_id, page_size, offset))
                rows = cur.fetchall() or []
                
                items = []
                for row in rows:
                    asset_type = str(row.get('asset_type') or 'indicator').strip().lower()
                    local_copy_id = None
                    purchased_strategy_id = None
                    purchased_script_source_id = None
                    local_copy_exists = False
                    marketplace_id = int(row.get('indicator_id') or row.get('live_indicator_id') or 0)
                    if asset_type == 'script_template' and marketplace_id:
                        cur.execute(
                            """
                            SELECT id
                            FROM qd_script_sources
                            WHERE user_id = ? AND source_marketplace_indicator_id = ?
                            ORDER BY id DESC LIMIT 1
                            """,
                            (user_id, marketplace_id),
                        )
                        source = cur.fetchone()
                        if source:
                            purchased_script_source_id = source['id']
                            local_copy_exists = True
                    elif marketplace_id:
                        local = self._find_buyer_local_copy(
                            cur, buyer_id=user_id, indicator_id=marketplace_id
                        )
                        if local:
                            local_copy_id = local['id']
                            local_copy_exists = True
                    items.append({
                        'purchase_id': row['purchase_id'],
                        'purchase_price': float(row.get('gross_price') or row['purchase_price'] or 0),
                        'gross_price': float(row.get('gross_price') or row['purchase_price'] or 0),
                        'platform_fee': float(row.get('platform_fee') or 0),
                        'seller_amount': float(row.get('seller_amount') or row['purchase_price'] or 0),
                        'fee_rate': float(row.get('fee_rate') or 0),
                        'purchase_time': row['purchase_time'].isoformat() if row['purchase_time'] else None,
                        'purchased_strategy_id': purchased_strategy_id,
                        'local_copy_id': local_copy_id,
                        'script_source_id': purchased_script_source_id,
                        'purchased_script_source_id': purchased_script_source_id,
                        'local_copy_exists': local_copy_exists,
                        'local_copy_missing': bool(marketplace_id) and not local_copy_exists,
                        'restore_available': bool(marketplace_id) and not local_copy_exists,
                        'indicator': {
                            'id': marketplace_id,
                            'name': row['name'],
                            'description': row['description'][:100] if row['description'] else '',
                            'preview_image': row['preview_image'] or '',
                            'avg_rating': float(row['avg_rating'] or 0),
                            'pricing_type': row.get('pricing_type') or 'free',
                            'price': float(row.get('price') or 0),
                            'vip_free': bool(row.get('vip_free') or False),
                            'asset_type': asset_type,
                        },
                        'seller': {
                            'nickname': row['seller_nickname'],
                            'avatar': row['seller_avatar'] or '/avatar2.jpg'
                        }
                    })
                cur.close()
                
                return {
                    'items': items,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size if total > 0 else 0
                }
                
        except Exception as e:
            logger.error(f"get_my_purchases failed: {e}")
            return {'items': [], 'total': 0, 'page': 1, 'page_size': page_size, 'total_pages': 0}

    # ==========================================
    # ==========================================
    #

    def get_author_summary(self, user_id: int) -> Dict[str, Any]:
        """获取作者的总览统计：发布数 / 已通过数 / 待审核数 / 总销量 / 总收入 / 平均评分。

        Returns dict with int/float scalars (永远返回结构完整的 dict，
        即使数据库出错也回退到全 0，保证前端不需要做空判断)。
        """
        empty = {
            'published_total': 0,
            'approved_count': 0,
            'pending_count': 0,
            'rejected_count': 0,
            'total_sales': 0,
            'total_revenue': 0.0,
            'avg_rating': 0.0,
            'rating_count': 0,
        }
        try:
            with get_db_connection() as db:
                cur = db.cursor()

                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS published_total,
                        COALESCE(SUM(CASE WHEN review_status = 'approved' OR review_status IS NULL THEN 1 ELSE 0 END), 0) AS approved_count,
                        COALESCE(SUM(CASE WHEN review_status = 'pending'  THEN 1 ELSE 0 END), 0) AS pending_count,
                        COALESCE(SUM(CASE WHEN review_status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected_count,
                        COALESCE(SUM(purchase_count), 0) AS total_sales,
                        COALESCE(SUM(rating_count), 0)   AS rating_count_total
                    FROM qd_indicator_codes
                    WHERE user_id = ? AND publish_to_community = 1
                      AND (is_buy IS NULL OR is_buy = 0)
                    """,
                    (user_id,),
                )
                row = cur.fetchone() or {}

                cur.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(seller_amount, price)), 0) AS total_revenue
                    FROM qd_indicator_purchases
                    WHERE seller_id = ?
                    """,
                    (user_id,),
                )
                rev_row = cur.fetchone() or {}

                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(avg_rating * rating_count), 0) AS weighted_sum,
                        COALESCE(SUM(rating_count), 0)              AS rating_count
                    FROM qd_indicator_codes
                    WHERE user_id = ? AND publish_to_community = 1
                      AND rating_count > 0
                    """,
                    (user_id,),
                )
                rate_row = cur.fetchone() or {}
                cur.close()

                rating_count = int(rate_row.get('rating_count') or 0)
                weighted_sum = float(rate_row.get('weighted_sum') or 0)
                avg_rating = round(weighted_sum / rating_count, 2) if rating_count > 0 else 0.0

                return {
                    'published_total': int(row.get('published_total') or 0),
                    'approved_count':  int(row.get('approved_count') or 0),
                    'pending_count':   int(row.get('pending_count') or 0),
                    'rejected_count':  int(row.get('rejected_count') or 0),
                    'total_sales':     int(row.get('total_sales') or 0),
                    'total_revenue':   float(rev_row.get('total_revenue') or 0),
                    'avg_rating':      avg_rating,
                    'rating_count':    rating_count,
                }
        except Exception as e:
            logger.error(f"get_author_summary failed: {e}")
            return empty

    def get_author_published(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """获取作者「我发布的指标」列表。

        每条记录附带：销量、评分、评分数、累计收入(基于 purchases.price 求和)、
        当前价格、定价类型、审核状态。
        """
        offset = (max(page, 1) - 1) * page_size
        try:
            with get_db_connection() as db:
                cur = db.cursor()

                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM qd_indicator_codes
                    WHERE user_id = ? AND publish_to_community = 1
                      AND (is_buy IS NULL OR is_buy = 0)
                    """,
                    (user_id,),
                )
                total = int((cur.fetchone() or {}).get('count') or 0)

                cur.execute(
                    """
                    SELECT
                        i.id, i.name, i.description, i.preview_image,
                        i.pricing_type, i.price, i.vip_free,
                        i.purchase_count, i.avg_rating, i.rating_count,
                        i.view_count, i.review_status, i.review_note,
                        COALESCE(i.asset_type, 'indicator') as asset_type,
                        ss.description as source_description,
                        i.created_at, i.updated_at,
                        COALESCE((
                            SELECT SUM(COALESCE(p.seller_amount, p.price))
                            FROM qd_indicator_purchases p
                            WHERE p.indicator_id = i.id
                        ), 0) AS revenue
                    FROM qd_indicator_codes i
                    LEFT JOIN qd_script_sources ss ON ss.id = i.source_script_source_id
                    WHERE i.user_id = ? AND i.publish_to_community = 1
                      AND (i.is_buy IS NULL OR i.is_buy = 0)
                    ORDER BY i.purchase_count DESC, i.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (user_id, page_size, offset),
                )
                rows = cur.fetchall() or []
                cur.close()

                items = []
                for row in rows:
                    row_asset_type = row.get('asset_type') or 'indicator'
                    row_description = row['description'] or ''
                    if str(row_asset_type).strip().lower() == 'script_template' and not row_description:
                        row_description = row.get('source_description') or ''
                    items.append({
                        'id': row['id'],
                        'name': row['name'],
                        'description': row_description[:160],
                        'preview_image': row['preview_image'] or '',
                        'pricing_type': row['pricing_type'] or 'free',
                        'price': float(row['price'] or 0),
                        'vip_free': bool(row.get('vip_free') or False),
                        'purchase_count': int(row['purchase_count'] or 0),
                        'avg_rating': float(row['avg_rating'] or 0),
                        'rating_count': int(row['rating_count'] or 0),
                        'view_count': int(row['view_count'] or 0),
                        'review_status': row.get('review_status') or 'approved',
                        'review_note': row.get('review_note') or '',
                        'asset_type': row_asset_type,
                        'revenue': float(row.get('revenue') or 0),
                        'created_at': row['created_at'].isoformat() if row.get('created_at') else None,
                        'updated_at': row['updated_at'].isoformat() if row.get('updated_at') else None,
                    })

                return {
                    'items': items,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size if total > 0 else 0,
                }
        except Exception as e:
            logger.error(f"get_author_published failed: {e}")
            raise

    def get_author_sales(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        indicator_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """获取作者「销售明细」(按购买记录 by 用户为 seller_id)。

        可选 indicator_id 过滤：只看某一个指标的销售记录。
        """
        offset = (max(page, 1) - 1) * page_size
        try:
            with get_db_connection() as db:
                cur = db.cursor()

                where = ["p.seller_id = ?"]
                params: List[Any] = [user_id]
                if indicator_id:
                    where.append("p.indicator_id = ?")
                    params.append(indicator_id)
                where_sql = " AND ".join(where)

                cur.execute(
                    f"SELECT COUNT(*) AS count FROM qd_indicator_purchases p WHERE {where_sql}",
                    tuple(params),
                )
                total = int((cur.fetchone() or {}).get('count') or 0)

                cur.execute(
                    f"""
                    SELECT
                        p.id          AS purchase_id,
                        p.indicator_id,
                        p.buyer_id,
                        p.price       AS purchase_price,
                        COALESCE(p.gross_price, p.price) AS gross_price,
                        COALESCE(p.platform_fee, 0) AS platform_fee,
                        COALESCE(p.seller_amount, p.price) AS seller_amount,
                        COALESCE(p.fee_rate, 0) AS fee_rate,
                        p.created_at  AS purchase_time,
                        i.name        AS indicator_name,
                        i.pricing_type,
                        u.nickname    AS buyer_nickname,
                        u.avatar      AS buyer_avatar
                    FROM qd_indicator_purchases p
                    LEFT JOIN qd_indicator_codes i ON p.indicator_id = i.id
                    LEFT JOIN qd_users           u ON p.buyer_id     = u.id
                    WHERE {where_sql}
                    ORDER BY p.created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params + [page_size, offset]),
                )
                rows = cur.fetchall() or []
                cur.close()

                items = []
                for row in rows:
                    items.append({
                        'purchase_id': row['purchase_id'],
                        'indicator_id': row['indicator_id'],
                        'indicator_name': row['indicator_name'] or '',
                        'pricing_type': row.get('pricing_type') or 'free',
                        'price': float(row.get('seller_amount') or row['purchase_price'] or 0),
                        'gross_price': float(row.get('gross_price') or row['purchase_price'] or 0),
                        'platform_fee': float(row.get('platform_fee') or 0),
                        'seller_amount': float(row.get('seller_amount') or row['purchase_price'] or 0),
                        'fee_rate': float(row.get('fee_rate') or 0),
                        'purchase_time': row['purchase_time'].isoformat() if row.get('purchase_time') else None,
                        'buyer': {
                            'id': row['buyer_id'],
                            'nickname': row['buyer_nickname'] or '',
                            'avatar': row['buyer_avatar'] or '/avatar2.jpg',
                        },
                    })

                return {
                    'items': items,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size if total > 0 else 0,
                }
        except Exception as e:
            logger.error(f"get_author_sales failed: {e}")
            return {'items': [], 'total': 0, 'page': 1, 'page_size': page_size, 'total_pages': 0}

    # ==========================================
    # ==========================================
    
    def get_comments(self, indicator_id: int, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """获取指标评论列表"""
        offset = (page - 1) * page_size
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute("""
                    SELECT COUNT(*) as count FROM qd_indicator_comments 
                    WHERE indicator_id = ? AND parent_id IS NULL AND is_deleted = 0
                """, (indicator_id,))
                total = cur.fetchone()['count']
                
                cur.execute("""
                    SELECT 
                        c.id, c.rating, c.content, c.created_at,
                        u.id as user_id, u.nickname, u.avatar
                    FROM qd_indicator_comments c
                    LEFT JOIN qd_users u ON c.user_id = u.id
                    WHERE c.indicator_id = ? AND c.parent_id IS NULL AND c.is_deleted = 0
                    ORDER BY c.created_at DESC
                    LIMIT ? OFFSET ?
                """, (indicator_id, page_size, offset))
                rows = cur.fetchall() or []
                cur.close()
                
                items = []
                for row in rows:
                    items.append({
                        'id': row['id'],
                        'rating': row['rating'],
                        'content': row['content'],
                        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                        'user': {
                            'id': row['user_id'],
                            'nickname': row['nickname'],
                            'avatar': row['avatar'] or '/avatar2.jpg'
                        }
                    })
                
                return {
                    'items': items,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size if total > 0 else 0
                }
                
        except Exception as e:
            logger.error(f"get_comments failed: {e}")
            return {'items': [], 'total': 0, 'page': 1, 'page_size': page_size, 'total_pages': 0}
    
    def add_comment(
        self, 
        user_id: int, 
        indicator_id: int, 
        rating: int, 
        content: str
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        添加评论（只有购买过的用户可以评论，且只能评论一次）
        """
        try:
            rating = max(1, min(5, int(rating)))
            content = (content or '').strip()[:500]  # Limit review content length.
            
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute(
                    """
                    SELECT id, user_id
                    FROM qd_indicator_codes
                    WHERE id = ? AND publish_to_community = 1
                      AND (review_status = 'approved' OR review_status IS NULL)
                    """,
                    (indicator_id,)
                )
                indicator = cur.fetchone()
                if not indicator:
                    cur.close()
                    return False, 'indicator_not_found', {}
                
                if indicator['user_id'] == user_id:
                    cur.close()
                    return False, 'cannot_comment_own', {}
                
                cur.execute(
                    "SELECT id FROM qd_indicator_purchases WHERE indicator_id = ? AND buyer_id = ?",
                    (indicator_id, user_id)
                )
                if not cur.fetchone():
                    cur.close()
                    return False, 'not_purchased', {}
                
                cur.execute(
                    "SELECT id FROM qd_indicator_comments WHERE indicator_id = ? AND user_id = ? AND parent_id IS NULL",
                    (indicator_id, user_id)
                )
                if cur.fetchone():
                    cur.close()
                    return False, 'already_commented', {}
                
                cur.execute("""
                    INSERT INTO qd_indicator_comments 
                    (indicator_id, user_id, rating, content, created_at, updated_at)
                    VALUES (?, ?, ?, ?, NOW(), NOW())
                """, (indicator_id, user_id, rating, content))
                comment_id = cur.lastrowid
                
                cur.execute("""
                    UPDATE qd_indicator_codes 
                    SET 
                        rating_count = COALESCE(rating_count, 0) + 1,
                        avg_rating = (
                            SELECT AVG(rating) FROM qd_indicator_comments 
                            WHERE indicator_id = ? AND parent_id IS NULL AND is_deleted = 0
                        )
                    WHERE id = ?
                """, (indicator_id, indicator_id))
                
                db.commit()
                cur.close()
                
                logger.info(f"User {user_id} commented on indicator {indicator_id} with rating {rating}")
                return True, 'success', {'comment_id': comment_id}
                
        except Exception as e:
            logger.error(f"add_comment failed: {e}")
            return False, f'error: {str(e)}', {}
    
    def update_comment(
        self,
        user_id: int,
        comment_id: int,
        indicator_id: int,
        rating: int,
        content: str
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        更新评论（只能修改自己的评论）
        """
        try:
            rating = max(1, min(5, int(rating)))
            content = (content or '').strip()[:500]
            
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute("""
                    SELECT id, rating as old_rating FROM qd_indicator_comments 
                    WHERE id = ? AND user_id = ? AND indicator_id = ? AND is_deleted = 0
                """, (comment_id, user_id, indicator_id))
                comment = cur.fetchone()
                
                if not comment:
                    cur.close()
                    return False, 'comment_not_found', {}
                
                old_rating = comment['old_rating']
                
                cur.execute("""
                    UPDATE qd_indicator_comments 
                    SET rating = ?, content = ?, updated_at = NOW()
                    WHERE id = ?
                """, (rating, content, comment_id))
                
                if old_rating != rating:
                    cur.execute("""
                        UPDATE qd_indicator_codes 
                        SET avg_rating = (
                            SELECT AVG(rating) FROM qd_indicator_comments 
                            WHERE indicator_id = ? AND parent_id IS NULL AND is_deleted = 0
                        )
                        WHERE id = ?
                    """, (indicator_id, indicator_id))
                
                db.commit()
                cur.close()
                
                logger.info(f"User {user_id} updated comment {comment_id}")
                return True, 'success', {'comment_id': comment_id}
                
        except Exception as e:
            logger.error(f"update_comment failed: {e}")
            return False, f'error: {str(e)}', {}
    
    def get_user_comment(self, user_id: int, indicator_id: int) -> Optional[Dict[str, Any]]:
        """获取用户对某个指标的评论"""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("""
                    SELECT id, rating, content, created_at, updated_at
                    FROM qd_indicator_comments
                    WHERE user_id = ? AND indicator_id = ? AND parent_id IS NULL AND is_deleted = 0
                """, (user_id, indicator_id))
                row = cur.fetchone()
                cur.close()
                
                if not row:
                    return None
                
                return {
                    'id': row['id'],
                    'rating': row['rating'],
                    'content': row['content'],
                    'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                    'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
                }
                
        except Exception as e:
            logger.error(f"get_user_comment failed: {e}")
            return None
    
    # ==========================================
    # ==========================================
    
    def get_pending_indicators(
        self,
        page: int = 1,
        page_size: int = 20,
        review_status: str = 'pending',  # 'pending' / 'approved' / 'rejected' / 'all'
        keyword: str = None,
        asset_type: str = None,
        pricing_type: str = None,
        sort_by: str = 'newest',
    ) -> Dict[str, Any]:
        """获取待审核的指标列表（管理员用）"""
        offset = (page - 1) * page_size
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                where_clauses = ["i.publish_to_community = 1"]
                params = []
                
                if review_status and review_status != 'all':
                    where_clauses.append("i.review_status = ?")
                    params.append(review_status)

                if keyword and keyword.strip():
                    where_clauses.append("(i.name ILIKE ? OR i.description ILIKE ? OR u.username ILIKE ? OR u.nickname ILIKE ?)")
                    search_term = f"%{keyword.strip()}%"
                    params.extend([search_term, search_term, search_term, search_term])

                allowed_asset_types = {'indicator', 'script_template'}
                if asset_type and str(asset_type).strip() in allowed_asset_types:
                    where_clauses.append("COALESCE(i.asset_type, 'indicator') = ?")
                    params.append(str(asset_type).strip())

                if pricing_type == 'free':
                    where_clauses.append("(i.pricing_type = 'free' OR COALESCE(i.price, 0) <= 0)")
                elif pricing_type == 'paid':
                    where_clauses.append("(i.pricing_type != 'free' AND COALESCE(i.price, 0) > 0)")
                elif pricing_type == 'vip_free':
                    where_clauses.append("(COALESCE(i.vip_free, FALSE) = TRUE)")
                
                where_sql = " AND ".join(where_clauses)
                order_map = {
                    'newest': 'i.created_at DESC',
                    'oldest': 'i.created_at ASC',
                    'price_asc': 'COALESCE(i.price, 0) ASC, i.created_at DESC',
                    'price_desc': 'COALESCE(i.price, 0) DESC, i.created_at DESC',
                    'name': 'LOWER(i.name) ASC, i.created_at DESC',
                }
                order_sql = order_map.get(sort_by, order_map['newest'])
                
                count_sql = f"""
                    SELECT COUNT(*) as count 
                    FROM qd_indicator_codes i 
                    LEFT JOIN qd_users u ON i.user_id = u.id
                    WHERE {where_sql}
                """
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()['count']
                
                query_sql = f"""
                    SELECT 
                        i.id, i.name, i.description, i.pricing_type, i.price,
                        COALESCE(i.vip_free, FALSE) as vip_free,
                        COALESCE(i.is_encrypted, 0) as code_hidden,
                        i.preview_image, i.code, i.review_status, i.review_note,
                        COALESCE(i.asset_type, 'indicator') as asset_type,
                        i.reviewed_at, i.reviewed_by, i.created_at,
                        u.id as author_id, u.username as author_username, 
                        u.nickname as author_nickname, u.avatar as author_avatar,
                        r.username as reviewer_username
                    FROM qd_indicator_codes i
                    LEFT JOIN qd_users u ON i.user_id = u.id
                    LEFT JOIN qd_users r ON i.reviewed_by = r.id
                    WHERE {where_sql}
                    ORDER BY {order_sql}
                    LIMIT ? OFFSET ?
                """
                cur.execute(query_sql, tuple(params + [page_size, offset]))
                rows = cur.fetchall() or []
                cur.close()
                
                items = []
                for row in rows:
                    items.append({
                        'id': row['id'],
                        'name': row['name'],
                        'description': row['description'][:300] if row['description'] else '',
                        'pricing_type': row['pricing_type'] or 'free',
                        'price': float(row['price'] or 0),
                        'vip_free': bool(row.get('vip_free') or False),
                        'code_hidden': bool(row.get('code_hidden') or False),
                        'preview_image': row['preview_image'] or '',
                        'code': row['code'] or '',  # Admin review can inspect source code.
                        'review_status': row['review_status'] or 'pending',
                        'review_note': row['review_note'] or '',
                        'asset_type': row.get('asset_type') or 'indicator',
                        'reviewed_at': row['reviewed_at'].isoformat() if row['reviewed_at'] else None,
                        'reviewer_username': row['reviewer_username'],
                        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                        'author': {
                            'id': row['author_id'],
                            'username': row['author_username'],
                            'nickname': row['author_nickname'] or row['author_username'],
                            'avatar': row['author_avatar'] or '/avatar2.jpg'
                        }
                    })
                
                return {
                    'items': items,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size if total > 0 else 0
                }
                
        except Exception as e:
            logger.error(f"get_pending_indicators failed: {e}")
            return {'items': [], 'total': 0, 'page': 1, 'page_size': page_size, 'total_pages': 0}
    
    def review_indicator(
        self,
        admin_id: int,
        indicator_id: int,
        action: str,  # 'approve' / 'reject'
        note: str = ''
    ) -> Tuple[bool, str]:
        """审核指标"""
        try:
            new_status = 'approved' if action == 'approve' else 'rejected'
            note = (note or '').strip()[:500]
            
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute("""
                    SELECT id, name, user_id FROM qd_indicator_codes 
                    WHERE id = ? AND publish_to_community = 1
                """, (indicator_id,))
                indicator = cur.fetchone()
                
                if not indicator:
                    cur.close()
                    return False, 'indicator_not_found'
                
                cur.execute("""
                    UPDATE qd_indicator_codes 
                    SET review_status = ?, review_note = ?, reviewed_at = NOW(), reviewed_by = ?
                    WHERE id = ?
                """, (new_status, note, admin_id, indicator_id))
                
                db.commit()
                cur.close()
                
                logger.info(f"Admin {admin_id} {action}d indicator {indicator_id}")
                return True, 'success'
                
        except Exception as e:
            logger.error(f"review_indicator failed: {e}")
            return False, f'error: {str(e)}'
    
    def unpublish_indicator(self, admin_id: int, indicator_id: int, note: str = '') -> Tuple[bool, str]:
        """下架指标（取消发布）"""
        try:
            note = (note or '').strip()[:500]
            
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute("""
                    SELECT id, name FROM qd_indicator_codes WHERE id = ?
                """, (indicator_id,))
                indicator = cur.fetchone()
                
                if not indicator:
                    cur.close()
                    return False, 'indicator_not_found'
                
                cur.execute("""
                    UPDATE qd_indicator_codes 
                    SET publish_to_community = 0, review_status = 'rejected', 
                        review_note = ?, reviewed_at = NOW(), reviewed_by = ?
                    WHERE id = ?
                """, (f"下架: {note}" if note else "管理员下架", admin_id, indicator_id))
                
                db.commit()
                cur.close()
                
                logger.info(f"Admin {admin_id} unpublished indicator {indicator_id}")
                return True, 'success'
                
        except Exception as e:
            logger.error(f"unpublish_indicator failed: {e}")
            return False, f'error: {str(e)}'

    def author_unpublish_asset(self, user_id: int, indicator_id: int, note: str = '') -> Tuple[bool, str]:
        """Let an author remove their own asset from the marketplace."""
        try:
            note = (note or '').strip()[:500]
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, name
                    FROM qd_indicator_codes
                    WHERE id = ? AND user_id = ? AND (is_buy IS NULL OR is_buy = 0)
                    """,
                    (indicator_id, user_id),
                )
                indicator = cur.fetchone()
                if not indicator:
                    cur.close()
                    return False, 'indicator_not_found'

                cur.execute(
                    """
                    UPDATE qd_indicator_codes
                    SET publish_to_community = 0,
                        review_status = 'rejected',
                        review_note = ?,
                        updated_at = NOW()
                    WHERE id = ? AND user_id = ?
                    """,
                    (f"Author unpublished: {note}" if note else "Author unpublished", indicator_id, user_id),
                )
                db.commit()
                cur.close()

            logger.info("Author %s unpublished marketplace asset %s", user_id, indicator_id)
            return True, 'success'
        except Exception as e:
            logger.error(f"author_unpublish_asset failed: {e}")
            return False, f'error: {str(e)}'
    
    def admin_delete_indicator(self, admin_id: int, indicator_id: int) -> Tuple[bool, str]:
        """管理员删除指标"""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute("SELECT id, name FROM qd_indicator_codes WHERE id = ?", (indicator_id,))
                indicator = cur.fetchone()
                
                if not indicator:
                    cur.close()
                    return False, 'indicator_not_found'
                
                cur.execute("SELECT COUNT(*) AS count FROM qd_indicator_purchases WHERE indicator_id = ?", (indicator_id,))
                purchase_count = int((cur.fetchone() or {}).get('count') or 0)

                if purchase_count > 0:
                    cur.execute("""
                        UPDATE qd_indicator_codes
                        SET publish_to_community = 0,
                            review_status = 'rejected',
                            review_note = 'Admin removed from market; buyer purchase records preserved',
                            reviewed_at = NOW(),
                            reviewed_by = ?
                        WHERE id = ?
                    """, (admin_id, indicator_id))
                else:
                    cur.execute("DELETE FROM qd_indicator_comments WHERE indicator_id = ?", (indicator_id,))
                    cur.execute("DELETE FROM qd_indicator_codes WHERE id = ?", (indicator_id,))
                
                db.commit()
                cur.close()
                
                logger.info(f"Admin {admin_id} deleted indicator {indicator_id}")
                return True, 'success'
                
        except Exception as e:
            logger.error(f"admin_delete_indicator failed: {e}")
            return False, f'error: {str(e)}'
    
    def get_review_stats(self) -> Dict[str, int]:
        """获取审核统计"""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("""
                    SELECT 
                        COUNT(*) FILTER (WHERE review_status = 'pending') as pending_count,
                        COUNT(*) FILTER (WHERE review_status = 'approved' OR review_status IS NULL) as approved_count,
                        COUNT(*) FILTER (WHERE review_status = 'rejected') as rejected_count
                    FROM qd_indicator_codes
                    WHERE publish_to_community = 1
                """)
                row = cur.fetchone()
                cur.close()
                
                return {
                    'pending': row['pending_count'] or 0,
                    'approved': row['approved_count'] or 0,
                    'rejected': row['rejected_count'] or 0
                }
        except Exception as e:
            logger.error(f"get_review_stats failed: {e}")
            return {'pending': 0, 'approved': 0, 'rejected': 0}
    
    # ==========================================
    # ==========================================

    def get_indicator_performance(self, indicator_id: int) -> Dict[str, Any]:
        """Return marketplace performance details for an indicator-like asset.

        Chart-only indicators do not own backtest or live-trading records.
        Script-template assets can surface their representative script
        backtest and equity curve.
        """
        default_result = {
            'strategy_count': 0,
            'trade_count': 0,
            'win_rate': 0.0,
            'total_profit': 0.0,
            'score': 0.0,
            'total_return': 0.0,
            'annual_return': 0.0,
            'sharpe': 0.0,
            'max_drawdown': 0.0,
            'profit_factor': 0.0,
            'win_rate_backtest': 0.0,
            'sample_size': 0,
            'applicable_symbols': [],
            'applicable_timeframes': [],
            'live_strategy_count': 0,
            'live_trade_count': 0,
            'live_win_rate': 0.0,
            'live_total_profit': 0.0,
            'best_run_id': None,
            'best_run_meta': None,
            'equity_curve': [],
            'strategy_contract': None,
        }

        try:
            with get_db_connection() as db:
                cur = db.cursor()

                cur.execute(
                    """
                    SELECT
                        i.id,
                        COALESCE(i.asset_type, 'indicator') as asset_type,
                        i.source_script_source_id,
                        i.source_strategy_id,
                        i.code,
                        ss.param_schema
                    FROM qd_indicator_codes i
                    LEFT JOIN qd_script_sources ss ON ss.id = i.source_script_source_id
                    WHERE i.id = %s
                    """,
                    (indicator_id,),
                )
                asset_row = dict(cur.fetchone() or {})
                if not asset_row:
                    cur.close()
                    return default_result

                # Re-use the list endpoint KPI path so cards and details
                # use the same representative backtest and never disagree.
                kpi = fetch_market_asset_kpis(cur, [asset_row]).get(indicator_id, summarise_backtest_runs([]))
                bt_rows: List[Dict[str, Any]] = []
                if kpi['best_run_id']:
                    cur.execute("""
                        SELECT id, symbol, timeframe, start_date, end_date,
                               leverage, market_type, manifest_json, result_json
                        FROM qd_backtest_runs
                        WHERE id = %s
                    """, (kpi['best_run_id'],))
                    best_only = cur.fetchone()
                    bt_rows = [dict(best_only)] if best_only else []

                # Surface the "best" run's metadata so the detail UI can
                # label the equity-curve panel with "this came from a
                # 4h BTC/USDT backtest, +12.4%, max DD -8.1%".
                # NB: schema columns are ``start_date`` / ``end_date``
                # (VARCHAR(20) yyyy-mm-dd), not ``started_at``/``ended_at``.
                best_run_meta = None
                best_run_manifest = {}
                if kpi['best_run_id']:
                    best_row = next((r for r in bt_rows if int(r.get('id') or 0) == kpi['best_run_id']), None)
                    if best_row:
                        rj = parse_backtest_result(best_row.get('result_json')) or {}
                        best_run_manifest = self._parse_json_dict(best_row.get('manifest_json'))
                        market_type = str(best_row.get('market_type') or '').strip().lower()
                        leverage = int(best_row.get('leverage') or 1)
                        if market_type not in ('spot', 'swap'):
                            market_type = 'swap' if leverage > 1 else 'spot'
                        if market_type == 'spot':
                            leverage = 1
                        start_date = str(best_row.get('start_date') or '') or None
                        end_date = str(best_row.get('end_date') or '') or None
                        duration_days = 0
                        if start_date and end_date:
                            try:
                                start_dt = datetime.strptime(start_date[:10], '%Y-%m-%d')
                                end_dt = datetime.strptime(end_date[:10], '%Y-%m-%d')
                                duration_days = max((end_dt - start_dt).days + 1, 1)
                            except Exception:
                                duration_days = 0
                        best_run_meta = {
                            'symbol': best_row.get('symbol') or '',
                            'timeframe': best_row.get('timeframe') or '',
                            'market_type': market_type,
                            'leverage': leverage,
                            'duration_days': duration_days,
                            'total_return': float(rj.get('totalReturn') or 0),
                            'sharpe': float(rj.get('sharpeRatio') or 0),
                            'max_drawdown': float(rj.get('maxDrawdown') or 0),
                            'win_rate': float(rj.get('winRate') or 0),
                            'start_date': start_date,
                            'end_date': end_date,
                        }

                strategy_contract = None
                if str(asset_row.get('asset_type') or '').strip().lower() in {
                    'script_template', 'script', 'strategy'
                }:
                    contract_manifest = {}
                    contract_source = 'published_code'
                    try:
                        from app.services.strategy_v2 import compile_strategy_v2

                        contract_manifest = compile_strategy_v2(
                            str(asset_row.get('code') or '')
                        ).manifest.metadata()
                    except Exception:
                        logger.debug(
                            "Marketplace strategy contract compilation failed for asset %s",
                            indicator_id,
                            exc_info=True,
                        )
                        contract_manifest = best_run_manifest
                        contract_source = 'backtest_snapshot'
                    strategy_contract = _strategy_contract_payload(
                        contract_manifest,
                        self._parse_json_dict(asset_row.get('param_schema')),
                        source=contract_source,
                    )

                # Equity curve for the best run. Pulled from
                # qd_backtest_equity_points (one row per sample point) so
                # this works even if the run's result_json doesn't embed
                # the full curve.
                equity_curve: List[Dict[str, Any]] = []
                if kpi['best_run_id']:
                    try:
                        cur.execute("""
                            SELECT point_index, point_time, point_value
                            FROM qd_backtest_equity_points
                            WHERE run_id = %s
                            ORDER BY point_index ASC
                        """, (kpi['best_run_id'],))
                        for p in (cur.fetchall() or []):
                            equity_curve.append({
                                'time': p.get('point_time') or '',
                                'value': float(p.get('point_value') or 0),
                            })
                    except Exception:
                        logger.debug("equity_points query failed", exc_info=True)

                live_strategy_count = 0
                live_trade_count = 0
                live_win_rate = 0.0
                live_total_profit = 0.0

                cur.close()

                # ---------- Combine ----------
                total_strategy_count = kpi['sample_size'] + live_strategy_count
                # Trade count from backtests is approximate (sum of per-run
                # totalTrades) — we don't claim it as a precise metric, just
                # a "size of evidence" hint on the detail page.
                bt_trades_total = 0
                for row in bt_rows:
                    rj = parse_backtest_result(row.get('result_json')) or {}
                    bt_trades_total += int(rj.get('totalTrades') or 0)
                total_trade_count = bt_trades_total + live_trade_count

                # (Previously this used the *mean* of backtest win-rates. We
                # switched to median because one weirdly successful run can
                # otherwise drag the rate from 45% to 70% on three samples.)
                if live_trade_count > 0:
                    combined_win_rate = live_win_rate
                    combined_profit = live_total_profit
                else:
                    combined_win_rate = kpi['win_rate']
                    combined_profit = kpi['total_return']

                if (
                    total_strategy_count == 0
                    and total_trade_count == 0
                    and not equity_curve
                    and not strategy_contract
                ):
                    return default_result

                return {
                    # Summary headline fields
                    'strategy_count': total_strategy_count,
                    'trade_count': total_trade_count,
                    'win_rate': combined_win_rate,
                    'total_profit': round(combined_profit, 2),
                    # Backtest-derived stats (always populated, even with
                    # zero runs; values just degrade to 0.
                    'score': kpi['score'],
                    'total_return': kpi['total_return'],
                    'annual_return': kpi['annual_return'],
                    'sharpe': kpi['sharpe'],
                    'max_drawdown': kpi['max_drawdown'],
                    'profit_factor': kpi['profit_factor'],
                    'win_rate_backtest': kpi['win_rate'],
                    'sample_size': kpi['sample_size'],
                    'applicable_symbols': kpi['symbols'],
                    'applicable_timeframes': kpi['timeframes'],
                    # Live-only breakdown so the UI can show
                    # "live: X / backtest: Y" side by side if it wants.
                    'live_strategy_count': live_strategy_count,
                    'live_trade_count': live_trade_count,
                    'live_win_rate': live_win_rate,
                    'live_total_profit': live_total_profit,
                    # Equity curve panel data
                    'best_run_id': kpi['best_run_id'],
                    'best_run_meta': best_run_meta,
                    'equity_curve': equity_curve,
                    'strategy_contract': strategy_contract,
                }

        except Exception as e:
            logger.error(f"get_indicator_performance failed: {e}")
            return default_result


_community_service = None


def get_community_service() -> CommunityService:
    """Return the shared community service instance."""
    global _community_service
    if _community_service is None:
        _community_service = CommunityService()
    return _community_service
