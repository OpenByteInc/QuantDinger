"""
Analysis Memory System 2.0
Simplified memory for fast analysis service.

Features:
1. Store analysis decisions with market context
2. Retrieve similar historical patterns
3. Track decision outcomes for learning
"""
import json
import time
import hashlib
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from app.utils.logger import get_logger
from app.utils.db import get_db_connection

logger = get_logger(__name__)


def _safe_json_parse(val, default=None):
    """安全解析 JSON - 处理已是 Python 对象或字符串的情况"""
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val  # 已经是 Python 对象 (PostgreSQL JSONB 自动转换)
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


class AnalysisMemory:
    """
    Simple but effective memory system for AI analysis.
    Uses PostgreSQL for persistence.
    """
    
    def __init__(self):
        self._ensure_table()
    
    def _ensure_table(self):
        """Create memory table if not exists, and add missing columns if needed."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS qd_analysis_memory (
                        id SERIAL PRIMARY KEY,
                        user_id INT,
                        market VARCHAR(50) NOT NULL,
                        symbol VARCHAR(50) NOT NULL,
                        decision VARCHAR(10) NOT NULL,
                        confidence INT DEFAULT 50,
                        price_at_analysis DECIMAL(24, 8),
                        summary TEXT,
                        reasons JSONB,
                        scores JSONB,
                        indicators_snapshot JSONB,
                        raw_result JSONB,
                        consensus_score DECIMAL(24, 8),
                        consensus_abs DECIMAL(24, 8),
                        agreement_ratio DECIMAL(10, 6),
                        quality_multiplier DECIMAL(10, 6),
                        task_status VARCHAR(20) DEFAULT 'completed',
                        task_error TEXT,
                        updated_at TIMESTAMP DEFAULT NOW(),
                        created_at TIMESTAMP DEFAULT NOW(),
                        validated_at TIMESTAMP,
                        actual_outcome VARCHAR(20),
                        actual_return_pct DECIMAL(10, 4),
                        was_correct BOOLEAN,
                        user_feedback VARCHAR(20),
                        feedback_at TIMESTAMP
                    );
                """)
                
                cur.execute("""
                    DO $$
                    BEGIN
                        -- 添加 user_id 列（如果不存在）
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'user_id'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN user_id INT;
                        END IF;
                        
                        -- 添加 raw_result 列（如果不存在）
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'raw_result'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN raw_result JSONB;
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'consensus_score'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN consensus_score DECIMAL(24, 8);
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'consensus_abs'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN consensus_abs DECIMAL(24, 8);
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'agreement_ratio'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN agreement_ratio DECIMAL(10, 6);
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'quality_multiplier'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN quality_multiplier DECIMAL(10, 6);
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'task_status'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN task_status VARCHAR(20) DEFAULT 'completed';
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'task_error'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN task_error TEXT;
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'qd_analysis_memory' AND column_name = 'updated_at'
                        ) THEN
                            ALTER TABLE qd_analysis_memory ADD COLUMN updated_at TIMESTAMP DEFAULT NOW();
                        END IF;
                    END $$;
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_analysis_memory_symbol 
                    ON qd_analysis_memory(market, symbol);
                    
                    CREATE INDEX IF NOT EXISTS idx_analysis_memory_created 
                    ON qd_analysis_memory(created_at DESC);
                    
                    CREATE INDEX IF NOT EXISTS idx_analysis_memory_validated 
                    ON qd_analysis_memory(validated_at) WHERE validated_at IS NOT NULL;
                    
                    CREATE INDEX IF NOT EXISTS idx_analysis_memory_user
                    ON qd_analysis_memory(user_id);
                """)
                
                db.commit()
                cur.close()
                logger.debug("Analysis memory table ensured successfully")
        except Exception as e:
            logger.warning(f"Memory table creation/update skipped: {e}")
    
    def store(self, analysis_result: Dict[str, Any], user_id: int = None) -> Optional[int]:
        """
        Store an analysis result for future reference.
        
        Args:
            analysis_result: Result from FastAnalysisService.analyze()
            user_id: User ID who created this analysis
        
        Returns:
            Memory ID or None if failed
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                market = analysis_result.get("market")
                symbol = analysis_result.get("symbol")
                decision = analysis_result.get("decision")
                confidence = analysis_result.get("confidence")
                price = analysis_result.get("market_data", {}).get("current_price")
                summary = analysis_result.get("summary")
                reasons = json.dumps(analysis_result.get("reasons", []))
                scores = json.dumps(analysis_result.get("scores", {}))
                indicators = json.dumps(analysis_result.get("indicators", {}))
                raw = json.dumps(analysis_result)

                consensus = analysis_result.get("consensus") or {}
                consensus_score = consensus.get("consensus_score")
                consensus_abs = consensus.get("consensus_abs")
                agreement_ratio = consensus.get("agreement_ratio")
                quality_multiplier = consensus.get("quality_multiplier")
                
                cur.execute("""
                    INSERT INTO qd_analysis_memory (
                        user_id, market, symbol, decision, confidence,
                        price_at_analysis, summary, reasons, scores, indicators_snapshot, raw_result,
                        consensus_score, consensus_abs, agreement_ratio, quality_multiplier,
                        task_status, task_error, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING id
                """, (
                    user_id, market, symbol, decision, confidence,
                    price, summary, reasons, scores, indicators, raw,
                    consensus_score, consensus_abs, agreement_ratio, quality_multiplier,
                    "completed", "",
                ))
                
                memory_id = cur.lastrowid
                db.commit()
                cur.close()
                
                logger.info(f"Stored analysis memory #{memory_id} for {symbol} by user {user_id}")
                return memory_id
                
        except Exception as e:
            logger.error(f"Failed to store analysis memory: {e}", exc_info=True)
            return None
    
    def get_recent(self, market: str, symbol: str, days: int = 7, limit: int = 5) -> List[Dict]:
        """
        Get recent analysis history for a symbol.
        
        Args:
            market: Market type
            symbol: Symbol
            days: Look back period
            limit: Max results
        
        Returns:
            List of historical analyses
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                days_int = int(days)
                cur.execute("""
                    SELECT 
                        id, decision, confidence, price_at_analysis,
                        summary, reasons, scores,
                        created_at, validated_at, was_correct, actual_return_pct,
                        task_status, task_error, updated_at
                    FROM qd_analysis_memory
                    WHERE market = %s AND symbol = %s
                    AND created_at > NOW() - (%s || ' days')::interval
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (market, symbol, days_int, limit))
                
                rows = cur.fetchall() or []
                cur.close()
                
                results = []
                for row in rows:
                    results.append({
                        "id": row['id'],
                        "decision": row['decision'],
                        "confidence": row['confidence'],
                        "price": float(row['price_at_analysis']) if row['price_at_analysis'] else None,
                        "summary": row['summary'],
                        "reasons": _safe_json_parse(row['reasons'], []),
                        "scores": _safe_json_parse(row['scores'], {}),
                        "status": row.get('task_status') or 'completed',
                        "error_message": row.get('task_error') or '',
                        "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                        "updated_at": row['updated_at'].isoformat() if row.get('updated_at') else None,
                        "was_correct": row['was_correct'],
                        "actual_return_pct": float(row['actual_return_pct']) if row['actual_return_pct'] else None,
                    })
                
                return results
                
        except Exception as e:
            logger.error(f"Failed to get recent memories: {e}")
            return []

    def get_all_history(self, user_id: int = None, page: int = 1, page_size: int = 20) -> Dict:
        """
        Get all analysis history with pagination.
        
        Args:
            user_id: User ID filter (required to show only user's own history)
            page: Page number (1-indexed)
            page_size: Items per page
        
        Returns:
            Dict with items list and total count
        """
        try:
            offset = (page - 1) * page_size
            
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Build WHERE clause based on user_id
                where_clause = "WHERE user_id = %s" if user_id else ""
                params_count = (user_id,) if user_id else ()
                
                # Get total count
                cur.execute(f"SELECT COUNT(*) as cnt FROM qd_analysis_memory {where_clause}", params_count)
                total_row = cur.fetchone()
                total = total_row['cnt'] if total_row else 0
                
                # Get paginated results
                params = (user_id, page_size, offset) if user_id else (page_size, offset)
                cur.execute(f"""
                    SELECT 
                        id, market, symbol, decision, confidence, price_at_analysis,
                        summary, reasons, scores, indicators_snapshot, raw_result,
                        created_at, validated_at, was_correct, actual_return_pct,
                        task_status, task_error, updated_at
                    FROM qd_analysis_memory
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """, params)
                
                rows = cur.fetchall() or []
                cur.close()
                
                items = []
                for row in rows:
                    items.append({
                        "id": row['id'],
                        "market": row['market'],
                        "symbol": row['symbol'],
                        "decision": row['decision'],
                        "confidence": row['confidence'],
                        "price": float(row['price_at_analysis']) if row['price_at_analysis'] else None,
                        "summary": row['summary'],
                        "reasons": _safe_json_parse(row['reasons'], []),
                        "scores": _safe_json_parse(row['scores'], {}),
                        "indicators": _safe_json_parse(row['indicators_snapshot'], {}),
                        "full_result": _safe_json_parse(row['raw_result'], None),
                        "status": row.get('task_status') or 'completed',
                        "error_message": row.get('task_error') or '',
                        "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                        "updated_at": row['updated_at'].isoformat() if row.get('updated_at') else None,
                        "was_correct": row['was_correct'],
                        "actual_return_pct": float(row['actual_return_pct']) if row['actual_return_pct'] else None,
                    })
                
                return {
                    "items": items,
                    "total": total,
                    "page": page,
                    "page_size": page_size
                }
                
        except Exception as e:
            logger.error(f"Failed to get all history: {e}")
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

    def delete_history(self, memory_id: int, user_id: int = None) -> bool:
        """
        Delete a history record by ID.
        
        Args:
            memory_id: The ID of the analysis memory to delete
            user_id: User ID to ensure user can only delete their own records
            
        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if user_id:
                    # Only delete if it belongs to the user
                    cur.execute("DELETE FROM qd_analysis_memory WHERE id = %s AND user_id = %s", (memory_id, user_id))
                else:
                    cur.execute("DELETE FROM qd_analysis_memory WHERE id = %s", (memory_id,))
                db.commit()
                affected = cur.rowcount
                cur.close()
                return affected > 0
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            return False

    def create_pending_task(self, market: str, symbol: str, language: str, model: str, timeframe: str,
                            user_id: int = None) -> Optional[int]:
        """Create a processing record in history before long-running analysis starts."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                summary = f"Analysis submitted ({timeframe})..."
                reasons = json.dumps([])
                scores = json.dumps({})
                indicators = json.dumps({})
                raw = json.dumps({
                    "market": market,
                    "symbol": symbol,
                    "language": language,
                    "model": model,
                    "timeframe": timeframe,
                    "task_status": "processing",
                })
                cur.execute("""
                    INSERT INTO qd_analysis_memory (
                        user_id, market, symbol, decision, confidence,
                        summary, reasons, scores, indicators_snapshot, raw_result,
                        task_status, task_error, updated_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s,
                              %s, %s, NOW(), NOW())
                    RETURNING id
                """, (
                    user_id, market, symbol, "HOLD", 0,
                    summary, reasons, scores, indicators, raw,
                    "processing", "",
                ))
                memory_id = cur.lastrowid
                db.commit()
                cur.close()
                return memory_id
        except Exception as e:
            logger.error(f"Failed to create pending task: {e}")
            return None

    def finalize_pending_task(self, memory_id: int, result: Dict[str, Any]) -> bool:
        """Overwrite pending record with final analysis result."""
        try:
            consensus = result.get("consensus") or {}
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("""
                    UPDATE qd_analysis_memory
                    SET decision = %s,
                        confidence = %s,
                        price_at_analysis = %s,
                        summary = %s,
                        reasons = %s,
                        scores = %s,
                        indicators_snapshot = %s,
                        raw_result = %s,
                        consensus_score = %s,
                        consensus_abs = %s,
                        agreement_ratio = %s,
                        quality_multiplier = %s,
                        task_status = %s,
                        task_error = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    result.get("decision"),
                    result.get("confidence"),
                    result.get("market_data", {}).get("current_price"),
                    result.get("summary"),
                    json.dumps(result.get("reasons", [])),
                    json.dumps(result.get("scores", {})),
                    json.dumps(result.get("indicators", {})),
                    json.dumps(result),
                    consensus.get("consensus_score"),
                    consensus.get("consensus_abs"),
                    consensus.get("agreement_ratio"),
                    consensus.get("quality_multiplier"),
                    "completed" if not result.get("error") else "failed",
                    str(result.get("error") or ""),
                    int(memory_id),
                ))
                ok = cur.rowcount > 0
                db.commit()
                cur.close()
                return ok
        except Exception as e:
            logger.error(f"Failed to finalize pending task {memory_id}: {e}")
            return False

    def fail_pending_task(self, memory_id: int, error_message: str) -> bool:
        """Mark pending task as failed."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("""
                    UPDATE qd_analysis_memory
                    SET task_status = 'failed',
                        task_error = %s,
                        summary = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    str(error_message or "analysis failed"),
                    f"Analysis failed: {str(error_message or '')}",
                    int(memory_id),
                ))
                ok = cur.rowcount > 0
                db.commit()
                cur.close()
                return ok
        except Exception as e:
            logger.error(f"Failed to mark task failed {memory_id}: {e}")
            return False
    
    def get_similar_patterns(self, market: str, symbol: str, 
                             current_indicators: Dict, limit: int = 3) -> List[Dict]:
        """
        Find historical analyses with similar technical patterns.
        
        Multi-indicator weighted similarity:
        - RSI: ±15 range, weighted 0.3
        - MACD signal: exact match, weighted 0.3
        - MA trend: exact match, weighted 0.25
        - Volatility level: similar band, weighted 0.15
        - Time decay: prefer recent validated outcomes
        """
        try:
            rsi = float(current_indicators.get("rsi", {}).get("value") or 50)
            macd_signal = str(current_indicators.get("macd", {}).get("signal") or "neutral").lower()
            ma_trend = str(current_indicators.get("moving_averages", {}).get("trend") or "sideways").lower()
            vol_level = str(current_indicators.get("volatility", {}).get("level") or "normal").lower()
            
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("""
                    SELECT 
                        id, decision, confidence, price_at_analysis,
                        summary, reasons, indicators_snapshot,
                        created_at, was_correct, actual_return_pct
                    FROM qd_analysis_memory
                    WHERE market = %s AND symbol = %s
                    AND validated_at IS NOT NULL
                    AND was_correct IS NOT NULL
                    ORDER BY validated_at DESC NULLS LAST, created_at DESC
                    LIMIT %s
                """, (market, symbol, limit * 5))
                
                rows = cur.fetchall() or []
                cur.close()
                
                scored = []
                for row in rows:
                    ind = _safe_json_parse(row['indicators_snapshot'], {})
                    hist_rsi = float(ind.get("rsi", {}).get("value") or 50)
                    hist_macd = str(ind.get("macd", {}).get("signal") or "neutral").lower()
                    hist_ma = str(ind.get("moving_averages", {}).get("trend") or "sideways").lower()
                    hist_vol = str(ind.get("volatility", {}).get("level") or "normal").lower()
                    
                    rsi_diff = abs(hist_rsi - rsi)
                    rsi_score = max(0, 1 - rsi_diff / 30) * 0.3
                    macd_score = 0.3 if hist_macd == macd_signal else 0
                    ma_score = 0.25 if hist_ma == ma_trend else 0
                    vol_score = 0.15 if hist_vol == vol_level else (0.08 if _vol_bands_similar(vol_level, hist_vol) else 0)
                    
                    sim = rsi_score + macd_score + ma_score + vol_score
                    if sim < 0.25:
                        continue
                    
                    bonus = 0.1 if row['was_correct'] else 0
                    scored.append((sim + bonus, {
                        "id": row['id'],
                        "decision": row['decision'],
                        "confidence": row['confidence'],
                        "price": float(row['price_at_analysis']) if row['price_at_analysis'] else None,
                        "summary": row['summary'],
                        "was_correct": row['was_correct'],
                        "actual_return_pct": float(row['actual_return_pct']) if row['actual_return_pct'] else None,
                        "similarity_score": round(sim + bonus, 3),
                    }))
                
                scored.sort(key=lambda x: -x[0])
                return [p[1] for p in scored[:limit]]
                
        except Exception as e:
            logger.error(f"Failed to get similar patterns: {e}")
            return []

    def record_feedback(self, memory_id: int, feedback: str) -> bool:
        """
        Record user feedback on an analysis.
        
        Args:
            memory_id: Analysis memory ID
            feedback: 'helpful' | 'not_helpful' | 'accurate' | 'inaccurate'
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("""
                    UPDATE qd_analysis_memory
                    SET user_feedback = %s, feedback_at = NOW()
                    WHERE id = %s
                """, (feedback, memory_id))
                db.commit()
                cur.close()
                return True
        except Exception as e:
            logger.error(f"Failed to record feedback: {e}")
            return False
    
    def validate_past_decisions(self, days_ago: int = 7) -> Dict[str, Any]:
        """
        Validate historical decisions by comparing with actual price movements.
        Run this periodically (e.g., daily) to build learning data.
        
        Args:
            days_ago: Validate decisions from N days ago
        
        Returns:
            Validation statistics
        """
        from app.services.market_data_collector import MarketDataCollector
        collector = MarketDataCollector()
        
        stats = {
            "validated": 0,
            "correct": 0,
            "incorrect": 0,
            "errors": 0,
        }
        
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Get unvalidated decisions from N days ago
                days_ago_int = int(days_ago)
                cur.execute("""
                    SELECT id, market, symbol, decision, price_at_analysis
                    FROM qd_analysis_memory
                    WHERE validated_at IS NULL
                    AND created_at < NOW() - (%s || ' days')::interval
                    AND created_at > NOW() - (%s || ' days')::interval
                    LIMIT 50
                """, (days_ago_int, int(days_ago_int + 1)))
                
                rows = cur.fetchall() or []
                
                for row in rows:
                    try:
                        price_data = collector._get_price(row['market'], row['symbol'])
                        current_price = float(price_data.get('price', 0)) if price_data else None
                        if not current_price or current_price <= 0:
                            continue
                        analysis_price = float(row['price_at_analysis'])
                        
                        if analysis_price <= 0:
                            continue
                        
                        # Calculate return
                        return_pct = ((current_price - analysis_price) / analysis_price) * 100
                        
                        # Determine if decision was correct
                        decision = row['decision']
                        was_correct = False
                        
                        if decision == 'BUY' and return_pct > 2:  # 2% threshold
                            was_correct = True
                        elif decision == 'SELL' and return_pct < -2:
                            was_correct = True
                        elif decision == 'HOLD' and abs(return_pct) <= 5:
                            was_correct = True
                        
                        # Update record
                        cur.execute("""
                            UPDATE qd_analysis_memory
                            SET validated_at = NOW(),
                                actual_return_pct = %s,
                                was_correct = %s
                            WHERE id = %s
                        """, (return_pct, was_correct, row['id']))
                        
                        stats["validated"] += 1
                        if was_correct:
                            stats["correct"] += 1
                        else:
                            stats["incorrect"] += 1
                            
                    except Exception as e:
                        logger.warning(f"Failed to validate memory {row['id']}: {e}")
                        stats["errors"] += 1
                
                db.commit()
                cur.close()
                
        except Exception as e:
            logger.error(f"Validation batch failed: {e}")
        
        accuracy = (stats["correct"] / stats["validated"] * 100) if stats["validated"] > 0 else 0
        stats["accuracy_pct"] = round(accuracy, 2)
        
        logger.info(f"Validation completed: {stats}")
        return stats

    def validate_unvalidated_older_than(self, min_age_days: int = 7, limit: int = 200) -> Dict[str, Any]:
        """
        Best-effort backfill:
        Validate unvalidated decisions older than `min_age_days`.

        This is used by offline AI calibration so the system can tune itself automatically.
        """
        from app.services.market_data_collector import MarketDataCollector
        collector = MarketDataCollector()

        stats = {
            "validated": 0,
            "correct": 0,
            "incorrect": 0,
            "errors": 0,
        }

        try:
            with get_db_connection() as db:
                cur = db.cursor()
                min_age_days_int = int(min_age_days)
                limit_int = int(limit)
                cur.execute(
                    """
                    SELECT id, market, symbol, decision, price_at_analysis
                    FROM qd_analysis_memory
                    WHERE validated_at IS NULL
                      AND created_at < NOW() - (%s || ' days')::interval
                    LIMIT %s
                    """,
                    (min_age_days_int, limit_int),
                )
                rows = cur.fetchall() or []

                for row in rows:
                    try:
                        price_data = collector._get_price(row["market"], row["symbol"])
                        current_price = float(price_data.get("price", 0)) if price_data else None
                        if not current_price or current_price <= 0:
                            continue
                        analysis_price = float(row.get("price_at_analysis") or 0.0)
                        if analysis_price <= 0:
                            continue

                        return_pct = ((float(current_price) - analysis_price) / analysis_price) * 100.0
                        decision = str(row.get("decision") or "HOLD")

                        was_correct = False
                        if decision == "BUY" and return_pct > 2:
                            was_correct = True
                        elif decision == "SELL" and return_pct < -2:
                            was_correct = True
                        elif decision == "HOLD" and abs(return_pct) <= 5:
                            was_correct = True

                        cur.execute(
                            """
                            UPDATE qd_analysis_memory
                            SET validated_at = NOW(),
                                actual_return_pct = %s,
                                was_correct = %s
                            WHERE id = %s
                            """,
                            (return_pct, was_correct, int(row["id"])),
                        )

                        stats["validated"] += 1
                        if was_correct:
                            stats["correct"] += 1
                        else:
                            stats["incorrect"] += 1
                    except Exception as e:
                        logger.warning(f"Failed to validate memory {row.get('id')}: {e}", exc_info=True)
                        stats["errors"] += 1

                db.commit()
                cur.close()
        except Exception as e:
            logger.error(f"validate_unvalidated_older_than failed: {e}", exc_info=True)

        return stats
    
    def get_confidence_accuracy_by_bucket(
        self, market: str = None, symbol: str = None, days: int = 90
    ) -> Dict[str, float]:
        """
        Compute actual accuracy by confidence bucket for calibration.
        Buckets: (50,60), (60,70), (70,80), (80,90), (90,100).
        Returns e.g. {"60_70": 0.58, "70_80": 0.62} - bucket_key -> accuracy.
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                where = ["validated_at IS NOT NULL", "was_correct IS NOT NULL", "confidence IS NOT NULL"]
                params = []
                days_int = int(days)
                if market:
                    where.append("market = %s")
                    params.append(market)
                if symbol:
                    where.append("symbol = %s")
                    params.append(symbol)
                where.append("created_at > NOW() - (%s || ' days')::interval")
                params.append(days_int)
                params = tuple(params) if params else ()
                cur.execute(f"""
                    SELECT confidence, was_correct
                    FROM qd_analysis_memory
                    WHERE {' AND '.join(where)}
                """, params)
                rows = cur.fetchall() or []
                cur.close()

            buckets = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
            out = {}
            for lo, hi in buckets:
                subset = [r for r in rows if lo <= (r.get("confidence") or 0) < hi]
                if len(subset) < 5:
                    continue
                correct = sum(1 for r in subset if r.get("was_correct"))
                out[f"{lo}_{hi}"] = correct / len(subset)
            return out
        except Exception as e:
            logger.warning(f"get_confidence_accuracy_by_bucket failed: {e}")
            return {}

    def get_adjusted_confidence(
        self, raw_confidence: int, market: str = None, symbol: str = None
    ) -> int:
        """
        Adjust confidence based on historical accuracy in that bucket.
        If model is overconfident (low actual accuracy), dampen. Underconfident -> boost slightly.
        """
        buckets = [(50, 60, "50_60"), (60, 70, "60_70"), (70, 80, "70_80"), (80, 90, "80_90"), (90, 101, "90_100")]
        bucket_key = None
        for lo, hi, key in buckets:
            if lo <= raw_confidence < hi:
                bucket_key = key
                break
        if not bucket_key:
            return max(1, min(99, int(raw_confidence)))
        acc_map = self.get_confidence_accuracy_by_bucket(market=market, symbol=symbol)
        acc = acc_map.get(bucket_key)
        if acc is None or acc <= 0:
            return max(1, min(99, int(raw_confidence)))
        expected = 0.5 + (raw_confidence - 50) / 100
        if expected <= 0:
            return raw_confidence
        factor = acc / expected
        adjusted = int(raw_confidence * factor)
        return max(1, min(99, adjusted))

    def get_performance_stats(self, market: str = None, symbol: str = None, 
                              days: int = 30) -> Dict[str, Any]:
        """
        Get AI performance statistics.
        
        Returns:
            Performance metrics for display
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                where_clauses = ["validated_at IS NOT NULL"]
                params = []
                
                if market:
                    where_clauses.append("market = %s")
                    params.append(market)
                if symbol:
                    where_clauses.append("symbol = %s")
                    params.append(symbol)
                
                days_int = int(days)
                where_clauses.append("created_at > NOW() - (%s || ' days')::interval")
                params.append(days_int)
                
                where_sql = " AND ".join(where_clauses)
                
                cur.execute(f"""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN was_correct = true THEN 1 ELSE 0 END) as correct,
                        AVG(actual_return_pct) as avg_return,
                        SUM(CASE WHEN decision = 'BUY' THEN 1 ELSE 0 END) as buy_count,
                        SUM(CASE WHEN decision = 'SELL' THEN 1 ELSE 0 END) as sell_count,
                        SUM(CASE WHEN decision = 'HOLD' THEN 1 ELSE 0 END) as hold_count,
                        SUM(CASE WHEN user_feedback = 'helpful' THEN 1 ELSE 0 END) as helpful_count,
                        SUM(CASE WHEN user_feedback IS NOT NULL THEN 1 ELSE 0 END) as feedback_count
                    FROM qd_analysis_memory
                    WHERE {where_sql}
                """, tuple(params) if params else None)
                
                row = cur.fetchone()
                cur.close()
                
                if not row or not row['total']:
                    return {
                        "total_analyses": 0,
                        "accuracy_pct": 0,
                        "avg_return_pct": 0,
                        "user_satisfaction_pct": 0,
                    }
                
                total = row['total']
                correct = row['correct'] or 0
                
                return {
                    "total_analyses": total,
                    "accuracy_pct": round((correct / total * 100) if total > 0 else 0, 2),
                    "avg_return_pct": round(float(row['avg_return'] or 0), 2),
                    "decision_distribution": {
                        "buy": row['buy_count'] or 0,
                        "sell": row['sell_count'] or 0,
                        "hold": row['hold_count'] or 0,
                    },
                    "user_satisfaction_pct": round(
                        (row['helpful_count'] / row['feedback_count'] * 100) 
                        if row['feedback_count'] and row['feedback_count'] > 0 else 0, 2
                    ),
                    "period_days": days,
                }
                
        except Exception as e:
            logger.error(f"Failed to get performance stats: {e}")
            return {
                "total_analyses": 0,
                "accuracy_pct": 0,
                "error": str(e),
            }


def _vol_bands_similar(a: str, b: str) -> bool:
    """Check if two volatility levels are in similar band."""
    low = {"low", "normal", "normal_low"}
    high = {"high", "elevated", "volatile", "very_high"}
    a, b = a.lower(), b.lower()
    if a in low and b in low:
        return True
    if a in high and b in high:
        return True
    return False


# Singleton
_memory_instance = None

def get_analysis_memory() -> AnalysisMemory:
    """Get singleton AnalysisMemory instance."""
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = AnalysisMemory()
    return _memory_instance
