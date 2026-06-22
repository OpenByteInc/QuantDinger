"""
Community Service - 指标社区服务

处理指标市场、购买、评论等功能。
"""
import json
import time
import statistics
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.services.billing_service import get_billing_service
from app.services.experiment.scoring import StrategyScoringService
from app.services.indicator_translator import pick_localized

logger = get_logger(__name__)


# Field set we pull out of qd_backtest_runs.result_json. Centralised so
# get_market_indicators (list, N indicators) and get_indicator_performance
# (single indicator, deeper detail) agree on parsing & units.
#
# Units (matching what services/backtest.py:_run() writes):
#   totalReturn / annualReturn  – percent, e.g. 12.5 means +12.5%
#   maxDrawdown                 – percent, NEGATIVE, e.g. -8.3 means -8.3%
#   sharpeRatio                 – plain number
#   winRate                     – percent 0..100
#   profitFactor                – ratio, >1 means net winner
#   totalTrades                 – integer count of closing trades
def _parse_backtest_result(raw: str) -> Optional[Dict[str, Any]]:
    """Decode result_json string -> dict, returning None on any parse error.

    Returns the full result dict (not just KPI fields) because downstream
    callers may need ``equityCurve``, ``startDate``, etc.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        rj = json.loads(raw)
        return rj if isinstance(rj, dict) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _summarise_indicator_runs(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate one indicator's successful backtest runs into a single
    representative KPI block + a composite ``score``.

    Args:
        runs: list of qd_backtest_runs rows. Each row is expected to expose
            at least ``id``, ``symbol``, ``timeframe`` and ``result_json``.

    Strategy / why this is shaped like this:
      * We *score every run individually* through ``StrategyScoringService``
        (the same engine the parameter-optimisation page uses) and then
        take the **median** of those scores as the indicator's headline.
        Median, not mean, because one freak run on the most generous
        symbol shouldn't single-handedly drag a mediocre indicator into
        the top of the leaderboard.
      * For the KPI numbers shown on the card (return / sharpe / drawdown
        / win-rate) we also use the **median** across runs. Same reason.
      * ``best_run_id`` is the run with the highest *individual* score —
        that's the one whose equity curve we'll draw on the detail page,
        because picking by raw return is gameable (one lucky backtest
        with crazy DD wins it).
      * ``applicable_symbols`` / ``applicable_timeframes`` are the union
        of everywhere the author successfully ran a backtest. This is the
        "automatic inference" path the user explicitly chose over having
        authors hand-tag the publish form.
    """
    empty = {
        'score': 0.0,
        'total_return': 0.0,
        'annual_return': 0.0,
        'sharpe': 0.0,
        'max_drawdown': 0.0,
        'win_rate': 0.0,
        'profit_factor': 0.0,
        'sample_size': 0,
        'best_run_id': None,
        'symbols': [],
        'timeframes': [],
    }
    if not runs:
        return empty

    scorer = StrategyScoringService()
    scored: List[Tuple[float, int, Dict[str, Any]]] = []
    returns: List[float] = []
    annual_returns: List[float] = []
    sharpes: List[float] = []
    drawdowns: List[float] = []
    win_rates: List[float] = []
    profit_factors: List[float] = []
    symbols: List[str] = []
    timeframes: List[str] = []

    for run in runs:
        rj = _parse_backtest_result(run.get('result_json'))
        if not rj:
            continue
        try:
            score_info = scorer.score_result(rj)
            # StrategyScoringService returns 'overallScore' (not 'overall').
            # Reading the wrong key here previously made every indicator's
            # market-page composite score read 0, regardless of backtest quality.
            score_val = float(score_info.get('overallScore') or 0)
        except Exception:
            logger.debug("score_result failed for run %s", run.get('id'), exc_info=True)
            score_val = 0.0

        scored.append((score_val, int(run.get('id') or 0), rj))
        returns.append(float(rj.get('totalReturn') or 0))
        annual_returns.append(float(rj.get('annualReturn') or 0))
        sharpes.append(float(rj.get('sharpeRatio') or 0))
        drawdowns.append(float(rj.get('maxDrawdown') or 0))
        win_rates.append(float(rj.get('winRate') or 0))
        profit_factors.append(float(rj.get('profitFactor') or 0))

        sym = (run.get('symbol') or '').strip()
        tf = (run.get('timeframe') or '').strip()
        if sym:
            symbols.append(sym)
        if tf:
            timeframes.append(tf)

    if not scored:
        return empty

    score_values = [s for s, _, _ in scored]
    headline_score = round(statistics.median(score_values), 2)
    best = max(scored, key=lambda x: x[0])
    best_run_id = best[1] or None

    def _median(xs: List[float]) -> float:
        return round(statistics.median(xs), 2) if xs else 0.0

    # Dedupe preserving first-seen order so the UI shows the most-used
    # symbol first when truncating to "BTC/USDT +2 more".
    def _dedup(xs: List[str]) -> List[str]:
        seen = set()
        out = []
        for x in xs:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    return {
        'score': headline_score,
        'total_return': _median(returns),
        'annual_return': _median(annual_returns),
        'sharpe': _median(sharpes),
        'max_drawdown': _median(drawdowns),  # already negative %, keep sign
        'win_rate': _median(win_rates),
        'profit_factor': _median(profit_factors),
        'sample_size': len(scored),
        'best_run_id': best_run_id,
        'symbols': _dedup(symbols),
        'timeframes': _dedup(timeframes),
    }


def _fetch_indicator_kpis(cur, indicator_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Batch-load KPI summary for several indicator ids in one round-trip.

    Returns ``{indicator_id: kpi_dict}``. Indicators with zero successful
    backtests get an empty kpi dict (score=0, symbols=[], etc.) — they
    still appear in the dict so callers don't have to do KeyError dances.

    Why batching: ``get_market_indicators`` returns up to ``page_size``
    indicators per request. Running one ``SELECT … FROM qd_backtest_runs``
    per indicator would scale linearly with page size and dominate the
    request budget; a single ``IN (…)`` query plus an in-Python group-by
    is O(N + M) where M is total runs across the page.
    """
    if not indicator_ids:
        return {}
    # Bucket runs by indicator_id first, then summarise each bucket.
    buckets: Dict[int, List[Dict[str, Any]]] = {iid: [] for iid in indicator_ids}
    placeholders = ','.join(['%s'] * len(indicator_ids))
    try:
        cur.execute(f"""
            SELECT id, indicator_id, symbol, timeframe, result_json
            FROM qd_backtest_runs
            WHERE indicator_id IN ({placeholders})
              AND status = 'success'
              AND result_json IS NOT NULL AND result_json != ''
        """, tuple(indicator_ids))
        for row in cur.fetchall() or []:
            iid = int(row.get('indicator_id') or 0)
            if iid in buckets:
                buckets[iid].append(dict(row))
    except Exception:
        logger.debug("Batch KPI query failed; returning empty KPIs", exc_info=True)
        return {iid: _summarise_indicator_runs([]) for iid in indicator_ids}

    return {iid: _summarise_indicator_runs(rows) for iid, rows in buckets.items()}


class CommunityService:
    """指标社区服务类"""
    
    def __init__(self):
        self.billing = get_billing_service()
        # Best-effort: ensure compatibility columns exist (for old databases)
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS vip_free BOOLEAN DEFAULT FALSE")
                # source_indicator_id links a buyer's local copy back to the published
                # original indicator so we can re-sync the latest code on demand.
                cur.execute("ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS source_indicator_id INTEGER")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_indicator_codes_source "
                    "ON qd_indicator_codes USING btree (source_indicator_id)"
                )
                # Multi-language support: LLM-translated name/description payloads.
                # source_language stores the original language code (e.g. 'zh-CN') and
                # *_i18n stores a JSONB dict {"en-US": "...", "zh-CN": "...", ...} that
                # downstream get_market_indicators / get_indicator_detail will pick
                # from based on the request's Accept-Language header.
                cur.execute("ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS source_language VARCHAR(16)")
                cur.execute("ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS name_i18n JSONB")
                cur.execute("ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS description_i18n JSONB")
                # Asset taxonomy: indicator | script_template | bot_preset
                cur.execute(
                    "ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS asset_type VARCHAR(32) DEFAULT 'indicator'"
                )
                db.commit()
                cur.close()
        except Exception:
            pass
    
    # ==========================================
    # ==========================================
    
    def get_market_indicators(
        self,
        page: int = 1,
        page_size: int = 12,
        keyword: str = None,
        pricing_type: str = None,  # 'free' / 'paid' / None(all)
        sort_by: str = 'score',    # 'score' / 'newest' / 'hot' / 'price_asc' / 'price_desc' / 'rating'
        user_id: int = None,       # Current user id, used to mark purchased items.
        accept_language: str = 'en-US',  # Select name_i18n / description_i18n.
        asset_type: str = None,    # 'indicator' / 'script_template' / 'bot_preset' / None(all)
    ) -> Dict[str, Any]:
        """获取市场上已发布的指标列表

        About ``sort_by='score'`` (the new default):
            The composite score lives in qd_backtest_runs.result_json, which
            is opaque to SQL. We can't ORDER BY it cheaply. Instead, when
            the caller asks for score-sorted results, we:
              1. Pull the *full set* of approved + published indicators
                 (id-only, very cheap row).
              2. Batch-compute their scores via _fetch_indicator_kpis.
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

                _allowed_asset_types = ('indicator', 'script_template', 'bot_preset')
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
                        f"SELECT i.id FROM qd_indicator_codes i WHERE {where_sql}",
                        tuple(params)
                    )
                    all_ids = [int(r['id']) for r in (cur.fetchall() or [])]
                    kpi_by_id = _fetch_indicator_kpis(cur, all_ids)
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
                            'page_size': page_size, 'total_pages': 0
                        }
                    id_placeholders = ','.join(['?'] * len(page_ids))
                    cur.execute(f"""
                        SELECT
                            i.id, i.name, i.description, i.pricing_type, i.price, COALESCE(i.vip_free, FALSE) as vip_free,
                            COALESCE(i.asset_type, 'indicator') as asset_type,
                            i.preview_image, i.purchase_count, i.avg_rating, i.rating_count,
                            i.view_count, i.created_at, i.updated_at,
                            i.source_language, i.name_i18n, i.description_i18n,
                            u.id as author_id, u.username as author_username,
                            u.nickname as author_nickname, u.avatar as author_avatar
                        FROM qd_indicator_codes i
                        LEFT JOIN qd_users u ON i.user_id = u.id
                        WHERE i.id IN ({id_placeholders})
                    """, tuple(page_ids))
                    rows_unordered = cur.fetchall() or []
                    # Preserve our score-sorted order even though SQL won't
                    by_id = {r['id']: r for r in rows_unordered}
                    rows = [by_id[iid] for iid in page_ids if iid in by_id]
                    page_kpis = {iid: kpi_by_id.get(iid, _summarise_indicator_runs([])) for iid in page_ids}
                else:
                    order_sql = order_map.get(sort_by, 'i.created_at DESC')
                    query_sql = f"""
                        SELECT
                            i.id, i.name, i.description, i.pricing_type, i.price, COALESCE(i.vip_free, FALSE) as vip_free,
                            COALESCE(i.asset_type, 'indicator') as asset_type,
                            i.preview_image, i.purchase_count, i.avg_rating, i.rating_count,
                            i.view_count, i.created_at, i.updated_at,
                            i.source_language, i.name_i18n, i.description_i18n,
                            u.id as author_id, u.username as author_username,
                            u.nickname as author_nickname, u.avatar as author_avatar
                        FROM qd_indicator_codes i
                        LEFT JOIN qd_users u ON i.user_id = u.id
                        WHERE {where_sql}
                        ORDER BY {order_sql}
                        LIMIT ? OFFSET ?
                    """
                    cur.execute(query_sql, tuple(params + [page_size, offset]))
                    rows = cur.fetchall() or []
                    page_kpis = _fetch_indicator_kpis(cur, [r['id'] for r in rows])

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
                    kpi = page_kpis.get(row['id'], _summarise_indicator_runs([]))
                    _src_lang = row.get('source_language') if isinstance(row, dict) else None
                    localized_name = pick_localized(
                        row['name'],
                        row.get('name_i18n') if isinstance(row, dict) else None,
                        accept_language,
                        _src_lang,
                    )
                    localized_desc = pick_localized(
                        row['description'],
                        row.get('description_i18n') if isinstance(row, dict) else None,
                        accept_language,
                        _src_lang,
                    )
                    items.append({
                        'id': row['id'],
                        'name': localized_name,
                        'description': localized_desc[:200] if localized_desc else '',
                        'asset_type': (row.get('asset_type') if isinstance(row, dict) else None) or 'indicator',
                        'pricing_type': row['pricing_type'] or 'free',
                        'price': float(row['price'] or 0),
                        'vip_free': bool(row.get('vip_free') or False),
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
                        # New: backtest-derived KPIs and applicability hints.
                        # All fields are guaranteed present even when an
                        # indicator has zero backtests — values just degrade
                        # to 0 / empty lists.
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
                    'total_pages': (total + page_size - 1) // page_size if total > 0 else 0
                }

        except Exception as e:
            logger.error(f"get_market_indicators failed: {e}")
            return {'items': [], 'total': 0, 'page': 1, 'page_size': page_size, 'total_pages': 0}

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
        is_admin: bool = False,
        existing_indicator_id: int = 0,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Publish a script strategy's code to the marketplace as script_template."""
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
                try:
                    cur.execute(
                        "ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS asset_type VARCHAR(32) DEFAULT 'indicator'"
                    )
                except Exception:
                    pass

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
                            asset_type = 'script_template',
                            review_status = ?, review_note = '', reviewed_at = NOW(), reviewed_by = ?,
                            updatetime = ?, updated_at = NOW()
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            name, code, description, pricing_type, price,
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
                           publish_to_community, pricing_type, price, asset_type, review_status,
                           createtime, updatetime, created_at, updated_at)
                        VALUES (?, 0, 1, ?, ?, ?, 1, ?, ?, 'script_template', ?, ?, ?, NOW(), NOW())
                        """,
                        (
                            user_id, name, code, description, pricing_type, price,
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

    def _serialize_bot_preset_from_strategy(self, strategy: Dict[str, Any]) -> str:
        """Pack a bot strategy row into marketplace-safe JSON (no API secrets)."""
        tc = self._parse_json_dict(strategy.get('trading_config'))
        ex = self._parse_json_dict(strategy.get('exchange_config'))
        safe_ex: Dict[str, Any] = {}
        if ex.get('exchange_id'):
            safe_ex['exchange_id'] = ex.get('exchange_id')

        clean_tc = {
            k: v for k, v in tc.items()
            if k not in ('source_preset_id', 'from_marketplace', 'source_template_id')
        }
        bot_type = (
            clean_tc.get('bot_type')
            or strategy.get('bot_type')
            or ''
        )
        if bot_type and 'bot_type' not in clean_tc:
            clean_tc['bot_type'] = bot_type

        preset = {
            'version': 1,
            'bot_type': bot_type,
            'strategy_type': strategy.get('strategy_type') or 'ScriptStrategy',
            'strategy_mode': 'bot',
            'market_category': strategy.get('market_category') or 'Crypto',
            'execution_mode': strategy.get('execution_mode') or 'live',
            'strategy_code': strategy.get('strategy_code') or '',
            'trading_config': clean_tc,
            'exchange_template': safe_ex,
        }
        return json.dumps(preset, ensure_ascii=False)

    def _parse_bot_preset_json(self, raw: Any) -> Dict[str, Any]:
        data = self._parse_json_dict(raw)
        if not data:
            raise ValueError('invalid bot preset payload')
        if not data.get('bot_type') and isinstance(data.get('trading_config'), dict):
            data['bot_type'] = data['trading_config'].get('bot_type')
        return data

    def _bot_preset_has_sync_update(
        self,
        local_strategy: Dict[str, Any],
        preset_payload: Any,
        preset_id: int,
    ) -> bool:
        """Return whether syncing would actually change a local bot strategy."""
        preset = self._parse_bot_preset_json(preset_payload)
        tc = dict(preset.get('trading_config') or {})
        bot_type = preset.get('bot_type') or tc.get('bot_type')
        if bot_type and not tc.get('bot_type'):
            tc['bot_type'] = bot_type
        tc['source_preset_id'] = int(preset_id)
        tc['from_marketplace'] = True
        local_tc = self._parse_trading_config_json(local_strategy.get('trading_config'))
        merged_tc = {**local_tc, **tc}
        new_code = preset.get('strategy_code') or local_strategy.get('strategy_code') or ''
        return (
            (local_strategy.get('strategy_code') or '') != new_code
            or json.dumps(merged_tc, sort_keys=True, default=str)
            != json.dumps(local_tc, sort_keys=True, default=str)
        )

    def publish_bot_preset_from_strategy(
        self,
        *,
        user_id: int,
        strategy_id: int,
        name: str,
        description: str = '',
        pricing_type: str = 'free',
        price: float = 0.0,
        is_admin: bool = False,
        existing_indicator_id: int = 0,
        strategy: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Publish a bot strategy configuration to the marketplace as bot_preset."""
        name = (name or '').strip()
        if not name:
            return False, 'name is required', None
        if not strategy:
            return False, 'strategy not found', None

        strategy_mode = str(strategy.get('strategy_mode') or '').strip().lower()
        if strategy_mode != 'bot':
            return False, 'strategy is not a bot', None

        try:
            preset_code = self._serialize_bot_preset_from_strategy(strategy)
            self._parse_bot_preset_json(preset_code)
        except Exception as exc:
            return False, f'invalid bot preset: {exc}', None

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
                try:
                    cur.execute(
                        "ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS asset_type VARCHAR(32) DEFAULT 'indicator'"
                    )
                except Exception:
                    pass

                if existing_indicator_id and existing_indicator_id > 0:
                    cur.execute(
                        """
                        SELECT id FROM qd_indicator_codes
                        WHERE id = ? AND user_id = ? AND COALESCE(asset_type, 'indicator') = 'bot_preset'
                        """,
                        (existing_indicator_id, user_id),
                    )
                    if not cur.fetchone():
                        cur.close()
                        return False, 'preset not found', None
                    cur.execute(
                        """
                        UPDATE qd_indicator_codes
                        SET name = ?, code = ?, description = ?,
                            publish_to_community = 1, pricing_type = ?, price = ?,
                            asset_type = 'bot_preset',
                            review_status = ?, review_note = '', reviewed_at = NOW(), reviewed_by = ?,
                            updatetime = ?, updated_at = NOW()
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            name, preset_code, description, pricing_type, price,
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
                           publish_to_community, pricing_type, price, asset_type, review_status,
                           createtime, updatetime, created_at, updated_at)
                        VALUES (?, 0, 1, ?, ?, ?, 1, ?, ?, 'bot_preset', ?, ?, ?, NOW(), NOW())
                        """,
                        (
                            user_id, name, preset_code, description, pricing_type, price,
                            review_status, now_ts, now_ts,
                        ),
                    )
                    indicator_id = int(cur.lastrowid or 0)

                db.commit()
                cur.close()

            return True, 'success', {
                'indicator_id': indicator_id,
                'review_status': review_status,
                'asset_type': 'bot_preset',
                'strategy_id': strategy_id,
            }
        except Exception as e:
            logger.error(f"publish_bot_preset_from_strategy failed: {e}")
            return False, str(e), None

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
                        i.user_id, i.review_status,
                        COALESCE(i.asset_type, 'indicator') as asset_type,
                        i.source_language, i.name_i18n, i.description_i18n,
                        u.id as author_id, u.username as author_username, 
                        u.nickname as author_nickname, u.avatar as author_avatar
                    FROM qd_indicator_codes i
                    LEFT JOIN qd_users u ON i.user_id = u.id
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
                has_update = False
                local_copy_id = None
                purchased_strategy_id = None
                purchased_script_source_id = None
                if user_id:
                    cur.execute(
                        "SELECT id, price, created_at FROM qd_indicator_purchases "
                        "WHERE indicator_id = ? AND buyer_id = ? ORDER BY id DESC LIMIT 1",
                        (indicator_id, user_id)
                    )
                    purchase_row = cur.fetchone()
                    is_purchased = purchase_row is not None
                    if is_purchased:
                        try:
                            your_purchase_price = float(purchase_row['price'] or 0)
                        except (TypeError, ValueError):
                            your_purchase_price = 0.0
                        if purchase_row.get('created_at'):
                            your_purchase_time = purchase_row['created_at'].isoformat()
                        asset_type = str(row.get('asset_type') or 'indicator').strip().lower()
                        if asset_type == 'script_template':
                            cur.execute(
                                """
                                SELECT id, code FROM qd_script_sources
                                WHERE user_id = ? AND source_marketplace_indicator_id = ?
                                ORDER BY id DESC LIMIT 1
                                """,
                                (user_id, indicator_id),
                            )
                            source = cur.fetchone()
                            if source:
                                purchased_script_source_id = source['id']
                                cur.execute(
                                    "SELECT code FROM qd_indicator_codes WHERE id = ?",
                                    (indicator_id,)
                                )
                                original_row = cur.fetchone()
                                original_code = original_row['code'] if original_row else None
                                local_code = source.get('code')
                                has_update = (original_code or '') != (local_code or '')
                        elif asset_type == 'bot_preset':
                            strat = self._find_buyer_strategy_from_preset(
                                cur, buyer_id=user_id, preset_id=indicator_id
                            )
                            if strat:
                                purchased_strategy_id = strat['id']
                                cur.execute(
                                    "SELECT code FROM qd_indicator_codes WHERE id = ?",
                                    (indicator_id,)
                                )
                                original_row = cur.fetchone()
                                original_code = original_row['code'] if original_row else None
                                try:
                                    has_update = self._bot_preset_has_sync_update(
                                        strat, original_code, indicator_id
                                    )
                                except Exception:
                                    has_update = False
                        else:
                            local_copy = self._find_buyer_local_copy(
                                cur, buyer_id=user_id, indicator_id=indicator_id,
                                original_name=row['name']
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
                localized_desc = pick_localized(
                    row['description'], row.get('description_i18n'), accept_language, _src_lang,
                )

                return {
                    'id': row['id'],
                    'name': localized_name,
                    'description': localized_desc or '',
                    'pricing_type': row['pricing_type'] or 'free',
                    'price': float(row['price'] or 0),
                    'vip_free': bool(row.get('vip_free') or False),
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
                    'your_purchase_time': your_purchase_time,
                    'is_own': row['user_id'] == user_id,
                    'has_update': has_update,
                    'local_copy_id': local_copy_id,
                    'asset_type': str(row.get('asset_type') or 'indicator'),
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
                
                if pricing_type != 'free' and effective_price > 0:
                    buyer_credits = self.billing.get_user_credits(buyer_id)
                    if buyer_credits < effective_price:
                        cur.close()
                        return False, 'insufficient_credits', {
                            'required': effective_price,
                            'current': float(buyer_credits)
                        }
                    
                    new_buyer_balance = buyer_credits - Decimal(str(effective_price))
                    cur.execute(
                        "UPDATE qd_users SET credits = ?, updated_at = NOW() WHERE id = ?",
                        (float(new_buyer_balance), buyer_id)
                    )
                    
                    cur.execute("""
                        INSERT INTO qd_credits_log 
                        (user_id, action, amount, balance_after, feature, reference_id, remark, created_at)
                        VALUES (?, 'indicator_purchase', ?, ?, 'indicator_purchase', ?, ?, NOW())
                    """, (buyer_id, -effective_price, float(new_buyer_balance), str(indicator_id), 
                          f"购买指标: {indicator['name']}"))
                    
                    seller_credits = self.billing.get_user_credits(seller_id)
                    new_seller_balance = seller_credits + Decimal(str(effective_price))
                    cur.execute(
                        "UPDATE qd_users SET credits = ?, updated_at = NOW() WHERE id = ?",
                        (float(new_seller_balance), seller_id)
                    )
                    
                    cur.execute("""
                        INSERT INTO qd_credits_log 
                        (user_id, action, amount, balance_after, feature, reference_id, remark, created_at)
                        VALUES (?, 'indicator_sale', ?, ?, 'indicator_sale', ?, ?, NOW())
                    """, (seller_id, effective_price, float(new_seller_balance), str(indicator_id),
                          f"出售指标: {indicator['name']}"))
                
                cur.execute("""
                    INSERT INTO qd_indicator_purchases 
                    (indicator_id, buyer_id, seller_id, price, created_at)
                    VALUES (?, ?, ?, ?, NOW())
                """, (indicator_id, buyer_id, seller_id, effective_price))
                
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
                        },
                    )
                elif asset_type == 'bot_preset':
                    from app.services.strategy import get_strategy_service
                    preset = self._parse_bot_preset_json(indicator['code'])
                    tc = dict(preset.get('trading_config') or {})
                    bot_type = preset.get('bot_type') or tc.get('bot_type')
                    if bot_type and not tc.get('bot_type'):
                        tc['bot_type'] = bot_type
                    tc['source_preset_id'] = int(indicator_id)
                    tc['from_marketplace'] = True
                    delivered_strategy_id = get_strategy_service().create_strategy({
                        'user_id': buyer_id,
                        'strategy_name': indicator['name'],
                        'strategy_type': preset.get('strategy_type') or 'ScriptStrategy',
                        'strategy_mode': 'bot',
                        'strategy_code': preset.get('strategy_code') or '',
                        'market_category': preset.get('market_category') or 'Crypto',
                        'execution_mode': preset.get('execution_mode') or 'live',
                        'exchange_config': {},
                        'notification_config': {'channels': ['browser'], 'targets': {}},
                        'marketplace_delivery': True,
                        'trading_config': tc,
                    })
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
                
                logger.info(f"User {buyer_id} purchased indicator {indicator_id} for {effective_price} credits (vip_free={vip_free}, is_vip={is_vip})")
                return True, 'success', {
                    'indicator_name': indicator['name'],
                    'price': price,
                    'charged': effective_price,
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

    def _find_buyer_local_copy(self, cur, buyer_id: int, indicator_id: int, original_name: str = '') -> Optional[Dict[str, Any]]:
        """Find a buyer's local copy that originated from the given published indicator.

        Strategy:
          1. Prefer the explicit link via ``source_indicator_id`` (set on new purchases).
          2. Fall back to matching by ``(user_id, is_buy=1, name)`` for legacy copies
             created before the ``source_indicator_id`` column existed.

        Returns the row (id/name/code) or None if no candidate can be found.
        """
        try:
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
            if row:
                return {
                    'id': row['id'],
                    'name': row['name'],
                    'code': row.get('code'),
                    'is_encrypted': row.get('is_encrypted'),
                    'matched_by': 'source_id'
                }
        except Exception as e:
            # source_indicator_id column may be missing on very old DBs; ignore and fallback
            logger.debug(f"source_indicator_id lookup failed (likely legacy DB): {e}")

        if not original_name:
            return None

        try:
            cur.execute(
                """
                SELECT id, name, code, is_encrypted
                FROM qd_indicator_codes
                WHERE user_id = ? AND is_buy = 1 AND name = ?
                ORDER BY id DESC LIMIT 1
                """,
                (buyer_id, original_name)
            )
            row = cur.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'name': row['name'],
                    'code': row.get('code'),
                    'is_encrypted': row.get('is_encrypted'),
                    'matched_by': 'name'
                }
        except Exception as e:
            logger.debug(f"legacy name-based lookup failed: {e}")

        return None

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

    def _find_buyer_strategy_from_template(
        self, cur, buyer_id: int, template_id: int
    ) -> Optional[Dict[str, Any]]:
        """Locate a script strategy created from a marketplace script_template."""
        try:
            cur.execute(
                """
                SELECT id, strategy_name, strategy_code, trading_config
                FROM qd_strategies_trading
                WHERE user_id = ? AND strategy_mode = 'script'
                ORDER BY id DESC
                """,
                (int(buyer_id),),
            )
            rows = cur.fetchall() or []
        except Exception:
            return None
        tid = str(int(template_id))
        for row in rows:
            tc = self._parse_trading_config_json(row.get('trading_config'))
            if str(tc.get('source_template_id') or '') == tid:
                return {
                    'id': row['id'],
                    'strategy_name': row.get('strategy_name'),
                    'strategy_code': row.get('strategy_code'),
                }
        return None

    def _find_buyer_strategy_from_preset(
        self, cur, buyer_id: int, preset_id: int
    ) -> Optional[Dict[str, Any]]:
        """Locate a bot strategy created from a marketplace bot_preset."""
        try:
            cur.execute(
                """
                SELECT id, strategy_name, strategy_code, trading_config, strategy_mode,
                       strategy_type, market_category, execution_mode, exchange_config
                FROM qd_strategies_trading
                WHERE user_id = ? AND strategy_mode = 'bot'
                ORDER BY id DESC
                """,
                (int(buyer_id),),
            )
            rows = cur.fetchall() or []
        except Exception:
            return None
        pid = str(int(preset_id))
        for row in rows:
            tc = self._parse_trading_config_json(row.get('trading_config'))
            if str(tc.get('source_preset_id') or '') == pid:
                return {
                    'id': row['id'],
                    'strategy_name': row.get('strategy_name'),
                    'strategy_code': row.get('strategy_code'),
                    'trading_config': tc,
                    'strategy_mode': row.get('strategy_mode'),
                    'strategy_type': row.get('strategy_type'),
                    'market_category': row.get('market_category'),
                    'execution_mode': row.get('execution_mode'),
                    'exchange_config': self._parse_json_dict(row.get('exchange_config')),
                }
        return None

    def sync_purchased_indicator(self, buyer_id: int, indicator_id: int) -> Tuple[bool, str, Dict[str, Any]]:
        """Refresh a buyer's local copy with the publisher's latest code/description.

        The user must have already purchased ``indicator_id`` for this to succeed.
        The buyer's local copy (matched by ``source_indicator_id``, or by name for
        legacy copies) will be overwritten with the publisher's current content,
        and its ``source_indicator_id`` will be repaired if it was missing.

        If the original indicator has been unpublished/removed or the buyer's
        local copy no longer exists (e.g. user deleted it), a recoverable error
        is returned so the UI can explain what to do next.
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()

                # 1. Must have purchased this indicator
                cur.execute(
                    "SELECT id FROM qd_indicator_purchases WHERE indicator_id = ? AND buyer_id = ?",
                    (indicator_id, buyer_id)
                )
                if not cur.fetchone():
                    cur.close()
                    return False, 'not_purchased', {}

                # 2. Fetch the (still-published) original
                cur.execute(
                    """
                    SELECT id, user_id, name, code, description, preview_image, is_encrypted,
                           publish_to_community, review_status, updated_at,
                           COALESCE(asset_type, 'indicator') as asset_type
                    FROM qd_indicator_codes
                    WHERE id = ?
                    """,
                    (indicator_id,)
                )
                original = cur.fetchone()
                if not original:
                    cur.close()
                    return False, 'indicator_not_found', {}
                if not original.get('publish_to_community'):
                    cur.close()
                    return False, 'indicator_unpublished', {}
                if original.get('review_status') not in (None, '', 'approved'):
                    cur.close()
                    return False, 'indicator_unavailable', {}

                asset_type = str(original.get('asset_type') or 'indicator').strip().lower()
                if asset_type == 'script_template':
                    cur.execute(
                        """
                        SELECT id, code
                        FROM qd_script_sources
                        WHERE user_id = ? AND source_marketplace_indicator_id = ?
                        ORDER BY id DESC LIMIT 1
                        """,
                        (buyer_id, indicator_id),
                    )
                    local_source = cur.fetchone()
                    if not local_source:
                        cur.close()
                        return False, 'local_copy_not_found', {}
                    if (local_source.get('code') or '') == (original.get('code') or ''):
                        cur.close()
                        return True, 'already_latest', {
                            'script_source_id': local_source['id'],
                            'updated': False,
                        }
                    cur.execute(
                        """
                        UPDATE qd_script_sources
                        SET code = ?, name = ?, description = ?, updated_at = NOW()
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            original['code'],
                            original['name'],
                            original.get('description') or '',
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

                if asset_type == 'bot_preset':
                    local_strategy = self._find_buyer_strategy_from_preset(
                        cur, buyer_id=buyer_id, preset_id=indicator_id
                    )
                    if not local_strategy:
                        cur.close()
                        return False, 'local_copy_not_found', {}
                    try:
                        preset = self._parse_bot_preset_json(original.get('code'))
                    except Exception:
                        cur.close()
                        return False, 'invalid_preset_payload', {}
                    if not self._bot_preset_has_sync_update(
                        local_strategy, original.get('code'), indicator_id
                    ):
                        cur.close()
                        return True, 'already_latest', {
                            'strategy_id': local_strategy['id'],
                            'updated': False,
                        }
                    tc = dict(preset.get('trading_config') or {})
                    bot_type = preset.get('bot_type') or tc.get('bot_type')
                    if bot_type and not tc.get('bot_type'):
                        tc['bot_type'] = bot_type
                    tc['source_preset_id'] = int(indicator_id)
                    tc['from_marketplace'] = True
                    local_tc = self._parse_trading_config_json(local_strategy.get('trading_config'))
                    merged_tc = {**local_tc, **tc}
                    new_code = preset.get('strategy_code') or local_strategy.get('strategy_code') or ''
                    cur.execute(
                        """
                        UPDATE qd_strategies_trading
                        SET strategy_code = ?, strategy_name = ?, trading_config = ?, updated_at = NOW()
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            new_code,
                            original['name'],
                            json.dumps(merged_tc, ensure_ascii=False),
                            local_strategy['id'],
                            buyer_id,
                        ),
                    )
                    db.commit()
                    cur.close()
                    return True, 'success', {
                        'strategy_id': local_strategy['id'],
                        'updated': True,
                        'indicator_name': original['name'],
                    }

                # 3. Locate buyer's local copy (indicator assets)
                local = self._find_buyer_local_copy(
                    cur, buyer_id=buyer_id, indicator_id=indicator_id,
                    original_name=original['name']
                )
                if not local:
                    cur.close()
                    return False, 'local_copy_not_found', {}

                # 4. Short-circuit when already identical
                if (local.get('code') or '') == (original.get('code') or ''):
                    # Still repair source_indicator_id on legacy rows so future
                    # syncs take the fast path and "has_update" detection is accurate.
                    if local.get('matched_by') == 'name':
                        cur.execute(
                            "UPDATE qd_indicator_codes SET source_indicator_id = ? WHERE id = ?",
                            (indicator_id, local['id'])
                        )
                        db.commit()
                    cur.close()
                    return True, 'already_latest', {
                        'local_copy_id': local['id'],
                        'updated': False
                    }

                # 5. Overwrite the local copy with the latest publisher content
                now_ts = int(time.time())
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
                db.commit()
                cur.close()

                logger.info(
                    f"User {buyer_id} synced local indicator {local['id']} "
                    f"from published indicator {indicator_id} (matched_by={local.get('matched_by')})"
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
        """获取用户购买的指标列表"""
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
                        p.id as purchase_id, p.price as purchase_price, p.created_at as purchase_time,
                        i.id, i.name, i.description, i.preview_image, i.avg_rating,
                        COALESCE(i.asset_type, 'indicator') as asset_type,
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
                    purchased_strategy_id = None
                    purchased_script_source_id = None
                    if asset_type == 'script_template' and row.get('id'):
                        cur.execute(
                            """
                            SELECT id
                            FROM qd_script_sources
                            WHERE user_id = ? AND source_marketplace_indicator_id = ?
                            ORDER BY id DESC LIMIT 1
                            """,
                            (user_id, int(row['id'])),
                        )
                        source = cur.fetchone()
                        if source:
                            purchased_script_source_id = source['id']
                    elif asset_type == 'bot_preset' and row.get('id'):
                        strat = self._find_buyer_strategy_from_preset(
                            cur, buyer_id=user_id, preset_id=int(row['id'])
                        )
                        if strat:
                            purchased_strategy_id = strat['id']
                    items.append({
                        'purchase_id': row['purchase_id'],
                        'purchase_price': float(row['purchase_price'] or 0),
                        'purchase_time': row['purchase_time'].isoformat() if row['purchase_time'] else None,
                        'purchased_strategy_id': purchased_strategy_id,
                        'script_source_id': purchased_script_source_id,
                        'purchased_script_source_id': purchased_script_source_id,
                        'indicator': {
                            'id': row['id'],
                            'name': row['name'],
                            'description': row['description'][:100] if row['description'] else '',
                            'preview_image': row['preview_image'] or '',
                            'avg_rating': float(row['avg_rating'] or 0),
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
                    SELECT COALESCE(SUM(price), 0) AS total_revenue
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
                        i.created_at, i.updated_at,
                        COALESCE((
                            SELECT SUM(p.price)
                            FROM qd_indicator_purchases p
                            WHERE p.indicator_id = i.id
                        ), 0) AS revenue
                    FROM qd_indicator_codes i
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
                    items.append({
                        'id': row['id'],
                        'name': row['name'],
                        'description': (row['description'] or '')[:160],
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
                        'asset_type': row.get('asset_type') or 'indicator',
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
            return {'items': [], 'total': 0, 'page': 1, 'page_size': page_size, 'total_pages': 0}

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
                        'price': float(row['purchase_price'] or 0),
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
        review_status: str = 'pending'  # 'pending' / 'approved' / 'rejected' / 'all'
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
                
                where_sql = " AND ".join(where_clauses)
                
                count_sql = f"""
                    SELECT COUNT(*) as count 
                    FROM qd_indicator_codes i 
                    WHERE {where_sql}
                """
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()['count']
                
                query_sql = f"""
                    SELECT 
                        i.id, i.name, i.description, i.pricing_type, i.price,
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
                    ORDER BY i.created_at DESC
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
                
                cur.execute("DELETE FROM qd_indicator_comments WHERE indicator_id = ?", (indicator_id,))
                
                cur.execute("DELETE FROM qd_indicator_purchases WHERE indicator_id = ?", (indicator_id,))
                
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
        """
        获取指标的实盘表现统计（详情页用）。

        数据来源：
          1. qd_backtest_runs (result_json) – 全部成功回测
          2. qd_backtest_equity_points       – 最佳回测的净值曲线点
          3. qd_strategies_trading + qd_strategy_trades – 真实实盘记录

        Response keys
        -------------
        Backtest-derived (median across all successful runs, never NULL):
            score, total_return, annual_return, sharpe, max_drawdown,
            profit_factor, win_rate_backtest, sample_size,
            applicable_symbols, applicable_timeframes
        Live trading derived:
            live_strategy_count, live_trade_count, live_win_rate,
            live_total_profit
        Headline combined fields (preserved for backwards compatibility
        with the existing IndicatorDetail.vue template):
            strategy_count, trade_count, win_rate, total_profit
        Equity curve (best backtest only):
            best_run_id, best_run_meta { symbol, timeframe, total_return,
            sharpe, max_drawdown, started_at, ended_at },
            equity_curve [ { time, value } ]
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
        }

        try:
            with get_db_connection() as db:
                cur = db.cursor()

                # We re-use the same code path the list endpoint uses so
                # detail and list pages never disagree on score / KPI.
                cur.execute("""
                    SELECT id, indicator_id, symbol, timeframe, start_date, end_date, result_json
                    FROM qd_backtest_runs
                    WHERE indicator_id = %s AND status = 'success'
                          AND result_json IS NOT NULL AND result_json != ''
                """, (indicator_id,))
                bt_rows = [dict(r) for r in (cur.fetchall() or [])]
                kpi = _summarise_indicator_runs(bt_rows)

                # Surface the "best" run's metadata so the detail UI can
                # label the equity-curve panel with "this came from a
                # 4h BTC/USDT backtest, +12.4%, max DD -8.1%".
                # NB: schema columns are ``start_date`` / ``end_date``
                # (VARCHAR(20) yyyy-mm-dd), not ``started_at``/``ended_at``.
                best_run_meta = None
                if kpi['best_run_id']:
                    best_row = next((r for r in bt_rows if int(r.get('id') or 0) == kpi['best_run_id']), None)
                    if best_row:
                        rj = _parse_backtest_result(best_row.get('result_json')) or {}
                        best_run_meta = {
                            'symbol': best_row.get('symbol') or '',
                            'timeframe': best_row.get('timeframe') or '',
                            'total_return': float(rj.get('totalReturn') or 0),
                            'sharpe': float(rj.get('sharpeRatio') or 0),
                            'max_drawdown': float(rj.get('maxDrawdown') or 0),
                            'win_rate': float(rj.get('winRate') or 0),
                            'start_date': str(best_row.get('start_date') or '') or None,
                            'end_date': str(best_row.get('end_date') or '') or None,
                        }

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

                try:
                    cur.execute("""
                        SELECT id FROM qd_strategies_trading
                        WHERE indicator_config::text LIKE %s
                    """, (f'%"indicator_id": {indicator_id}%',))
                    strategy_rows = cur.fetchall()
                    if not strategy_rows:
                        cur.execute("""
                            SELECT id FROM qd_strategies_trading
                            WHERE indicator_config::text LIKE %s
                        """, (f'%"indicator_id":{indicator_id}%',))
                        strategy_rows = cur.fetchall()

                    if strategy_rows:
                        strategy_ids = [r['id'] for r in strategy_rows]
                        live_strategy_count = len(strategy_ids)

                        placeholders = ','.join(['%s'] * len(strategy_ids))
                        # ``profit IS NOT NULL`` excludes open events (they
                        # carry NULL profit). ``profit != 0`` was the legacy
                        # filter — it also worked because NULL comparisons
                        # return NULL (= falsy in SQL) but it accidentally
                        # dropped genuine break-even closes from the
                        # denominator. We use the more explicit NOT NULL
                        # check, in line with the dashboard fix.
                        cur.execute(f"""
                            SELECT
                                COUNT(*) as trade_count,
                                SUM(CASE WHEN (COALESCE(profit, 0) - COALESCE(commission, 0)) > 0 THEN 1 ELSE 0 END) as win_count,
                                SUM(CASE WHEN (COALESCE(profit, 0) - COALESCE(commission, 0)) < 0 THEN 1 ELSE 0 END) as loss_count,
                                SUM(COALESCE(profit, 0) - COALESCE(commission, 0)) as total_profit
                            FROM qd_strategy_trades
                            WHERE strategy_id IN ({placeholders})
                              AND profit IS NOT NULL
                        """, tuple(strategy_ids))
                        trade_row = cur.fetchone()

                        if trade_row and (trade_row['trade_count'] or 0) > 0:
                            live_trade_count = int(trade_row['trade_count'] or 0)
                            win_count = int(trade_row['win_count'] or 0)
                            loss_count = int(trade_row['loss_count'] or 0)
                            decided = win_count + loss_count
                            # Win rate over *decided* trades — same convention
                            # as the dashboard fix, so a strategy with
                            # "2 wins / 0 losses / 1 break-even" reads as 100%.
                            live_win_rate = round(win_count / decided * 100, 2) if decided > 0 else 0.0
                            live_total_profit = round(float(trade_row['total_profit'] or 0), 2)
                except Exception:
                    logger.debug("Live trading query skipped or failed", exc_info=True)

                cur.close()

                # ---------- Combine ----------
                total_strategy_count = kpi['sample_size'] + live_strategy_count
                # Trade count from backtests is approximate (sum of per-run
                # totalTrades) — we don't claim it as a precise metric, just
                # a "size of evidence" hint on the detail page.
                bt_trades_total = 0
                for row in bt_rows:
                    rj = _parse_backtest_result(row.get('result_json')) or {}
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

                if total_strategy_count == 0 and total_trade_count == 0 and not equity_curve:
                    return default_result

                return {
                    # Backwards-compatible headline fields
                    'strategy_count': total_strategy_count,
                    'trade_count': total_trade_count,
                    'win_rate': combined_win_rate,
                    'total_profit': round(combined_profit, 2),
                    # Backtest-derived stats (always populated, even with
                    # zero runs — values just degrade to 0)
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
                }

        except Exception as e:
            logger.error(f"get_indicator_performance failed: {e}")
            return default_result


_community_service = None


def get_community_service() -> CommunityService:
    """获取社区服务单例"""
    global _community_service
    if _community_service is None:
        _community_service = CommunityService()
    return _community_service
