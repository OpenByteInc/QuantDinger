"""Fast analysis orchestration built on the shared market-data collector."""
import json
import os
import re
import time
from typing import Dict, Any, Optional, List

from app.utils.logger import get_logger
from app.services.llm import LLMService
from app.services.market_data_collector import get_market_data_collector
from app.services.fast_analysis_formatters import build_trend_outlook_summary, safe_float_price
from app.services.fast_analysis_fundamentals import (
    build_score_payload,
    format_financial_statements,
    fundamental_provenance,
)
from app.services.fast_analysis_geo import is_major_geopolitical_news_text
from app.services.fast_analysis_plan import finalize_trading_plan, trading_plan_risk_fields
from app.services.fast_analysis_policy import direction_supported_by_consensus, should_override_with_consensus
from app.services.fast_analysis_scoring import FastAnalysisScoringMixin
from app.professional_report import build_professional_report
from app.professional_report.llm_contract import validate_llm_analysis
from app.professional_report.snapshot import build_evidence_snapshot
from app.professional_report.prompt import build_professional_analysis_prompt
from app.data_sources.factory import DataSourceFactory

logger = get_logger(__name__)


class FastAnalysisService(FastAnalysisScoringMixin):
    """
    快速分析服务 3.0
    
    架构：
    1. 数据采集层 - MarketDataCollector (统一数据源)
    2. 分析层 - 单次LLM调用 (强约束prompt)
    3. 记忆层 - 分析历史存储和检索
    """
    
    def __init__(self):
        self.llm_service = LLMService()
        self.data_collector = get_market_data_collector()
        self._memory_db = None  # Lazy init

    @staticmethod
    def _normalize_analysis_market(market: str) -> str:
        """Return the canonical market value required by report contracts."""
        return DataSourceFactory.normalize_market(market or "")
    
    # ==================== Data Collection Layer ====================
    
    def _collect_market_data(
        self,
        market: str,
        symbol: str,
        timeframe: str = "1D",
        *,
        include_macro: bool = True,
        include_news: bool = True,
        timeout: int = 45,
        recovery_target: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        使用统一的数据采集器收集市场数据
        
        数据层次：
        1. 核心数据: 价格、K线、技术指标
        2. 基本面: 公司信息、财务数据
        3. 宏观数据: DXY、VIX、TNX、黄金等
        4. 情绪数据: 新闻、市场情绪
        """
        collected = self.data_collector.collect_all(
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            include_macro=include_macro,
            include_news=include_news,
            timeout=timeout,  # 增加超时时间，确保数据收集完成
        )
        if recovery_target is not None:
            self._backfill_primary_enrichment(recovery_target, collected)
        return collected

    @staticmethod
    def _backfill_primary_enrichment(
        primary_data: Dict[str, Any], candidate_data: Dict[str, Any]
    ) -> None:
        """Recover slow, timeframe-independent enrichment from later fetches.

        Quotes and K-lines are timeframe-specific; company/fundamental data are
        not.  If a cold primary request reaches its deadline but a subsequent
        timeframe fetch succeeds, retain that result for the final report.
        """
        if not isinstance(primary_data, dict) or not isinstance(candidate_data, dict):
            return
        meta = primary_data.setdefault("_meta", {})
        success_items = meta.setdefault("success_items", [])
        failed_items = meta.setdefault("failed_items", [])
        for key in ("fundamental", "company"):
            if primary_data.get(key) or not candidate_data.get(key):
                continue
            primary_data[key] = candidate_data[key]
            if key not in success_items:
                success_items.append(key)
            while key in failed_items:
                failed_items.remove(key)
            logger.info("Recovered primary %s from a later timeframe fetch", key)
    
    def _calculate_indicators(self, kline_data: List[Dict]) -> Dict[str, Any]:
        """
        Calculate technical indicators using rules (no LLM).
        Returns actionable signals, not raw numbers.
        """
        if not kline_data or len(kline_data) < 5:
            return {"error": "Insufficient data"}
        
        try:
            # Use tools' built-in calculation
            raw_indicators = self.tools.calculate_technical_indicators(kline_data)
            
            # Extract key values
            closes = [float(k.get("close", 0)) for k in kline_data if k.get("close")]
            if not closes:
                return {"error": "No close prices"}
            
            current_price = closes[-1]
            
            # RSI interpretation
            rsi = raw_indicators.get("RSI", 50)
            if rsi < 30:
                rsi_signal = "oversold"
                rsi_action = "potential_buy"
            elif rsi > 70:
                rsi_signal = "overbought"
                rsi_action = "potential_sell"
            else:
                rsi_signal = "neutral"
                rsi_action = "hold"
            
            # MACD interpretation
            macd = raw_indicators.get("MACD", 0)
            macd_signal_line = raw_indicators.get("MACD_Signal", 0)
            macd_hist = raw_indicators.get("MACD_Hist", 0)
            previous_macd = previous_signal = None
            if len(kline_data) > 34:
                previous_raw = self.tools.calculate_technical_indicators(kline_data[:-1])
                previous_macd = previous_raw.get("MACD")
                previous_signal = previous_raw.get("MACD_Signal")

            cross_event = None
            if previous_macd is not None and previous_signal is not None:
                if previous_macd <= previous_signal and macd > macd_signal_line:
                    cross_event = "golden_cross"
                elif previous_macd >= previous_signal and macd < macd_signal_line:
                    cross_event = "death_cross"
            
            if macd > macd_signal_line and macd_hist > 0:
                macd_signal = "bullish"
                macd_trend = cross_event or "bullish_alignment"
            elif macd < macd_signal_line and macd_hist < 0:
                macd_signal = "bearish"
                macd_trend = cross_event or "bearish_alignment"
            else:
                macd_signal = "neutral"
                macd_trend = "consolidating"
            
            # Moving averages
            ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else current_price
            ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else current_price
            ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
            
            if current_price > ma5 > ma10 > ma20:
                ma_trend = "strong_uptrend"
            elif current_price > ma20:
                ma_trend = "uptrend"
            elif current_price < ma5 < ma10 < ma20:
                ma_trend = "strong_downtrend"
            elif current_price < ma20:
                ma_trend = "downtrend"
            else:
                ma_trend = "sideways"
            
            # Support/Resistance (simple: recent highs/lows)
            recent_highs = [float(k.get("high", 0)) for k in kline_data[-14:] if k.get("high")]
            recent_lows = [float(k.get("low", 0)) for k in kline_data[-14:] if k.get("low")]
            
            resistance = max(recent_highs) if recent_highs else current_price * 1.05
            support = min(recent_lows) if recent_lows else current_price * 0.95
            
            # Volatility (ATR-like)
            if len(kline_data) >= 14:
                ranges = []
                for k in kline_data[-14:]:
                    h = float(k.get("high", 0))
                    l = float(k.get("low", 0))
                    if h > 0 and l > 0:
                        ranges.append(h - l)
                atr = sum(ranges) / len(ranges) if ranges else 0
                volatility_pct = (atr / current_price * 100) if current_price > 0 else 0
                
                if volatility_pct > 5:
                    volatility = "high"
                elif volatility_pct > 2:
                    volatility = "medium"
                else:
                    volatility = "low"
            else:
                volatility = "unknown"
                volatility_pct = 0
            
            return {
                "current_price": round(current_price, 6),
                "rsi": {
                    "value": round(rsi, 2),
                    "signal": rsi_signal,
                    "action": rsi_action,
                },
                "macd": {
                    "value": round(macd, 6),
                    "signal_line": round(macd_signal_line, 6),
                    "histogram": round(macd_hist, 6),
                    "signal": macd_signal,
                    "trend": macd_trend,
                },
                "moving_averages": {
                    "ma5": round(ma5, 6),
                    "ma10": round(ma10, 6),
                    "ma20": round(ma20, 6),
                    "trend": ma_trend,
                },
                "levels": {
                    "support": round(support, 6),
                    "resistance": round(resistance, 6),
                },
                "volatility": {
                    "level": volatility,
                    "pct": round(volatility_pct, 2),
                },
                "raw": raw_indicators,
            }
        except Exception as e:
            logger.error(f"Indicator calculation failed: {e}")
            return {"error": str(e)}
    
    def _format_news_summary(self, news_data: List[Dict], max_items: int = 5) -> str:
        """Format news into a concise summary for the prompt."""
        if not news_data:
            return "No recent news available."
        
        summaries = []
        for item in news_data[:max_items]:
            title = item.get("title", item.get("headline", ""))
            sentiment = item.get("sentiment", "neutral")
            date = item.get("date", item.get("datetime", ""))[:10] if item.get("date") or item.get("datetime") else ""
            
            if title:
                summaries.append(f"- [{sentiment}] {title} ({date})")
        
        return "\n".join(summaries) if summaries else "No recent news available."
    
    def _format_crypto_factor_prompt(self, crypto_factors: Dict[str, Any], language: str) -> str:
        """Format crypto-specific market structure data for prompts."""
        if not crypto_factors:
            return "Crypto flow / derivatives data unavailable."

        is_zh = str(language or "").lower().startswith("zh")
        signals = crypto_factors.get("signals") or {}

        def _fmt_num(v: Any, suffix: str = "") -> str:
            if v is None or v == "":
                return "N/A"
            try:
                n = float(v)
            except Exception:
                return str(v)
            if abs(n) >= 1_000_000_000:
                return f"{n / 1_000_000_000:.2f}B{suffix}"
            if abs(n) >= 1_000_000:
                return f"{n / 1_000_000:.2f}M{suffix}"
            if abs(n) >= 1_000:
                return f"{n / 1_000:.2f}K{suffix}"
            return f"{n:.4f}{suffix}" if abs(n) < 1 else f"{n:.2f}{suffix}"

        def _fmt_pct(v: Any) -> str:
            if v is None or v == "":
                return "N/A"
            try:
                return f"{float(v):.2f}%"
            except Exception:
                return str(v)

        if is_zh:
            return (
                f"- 24h成交额: {_fmt_num(crypto_factors.get('volume_24h'), ' USD')}\n"
                f"- 成交活跃度变化: {_fmt_pct(crypto_factors.get('volume_change_24h'))}\n"
                f"- 资金费率: {_fmt_pct(crypto_factors.get('funding_rate'))}\n"
                f"- 未平仓量(OI): {_fmt_num(crypto_factors.get('open_interest'), ' USD')}\n"
                f"- OI变化(24h): {_fmt_pct(crypto_factors.get('open_interest_change_24h'))}\n"
                f"- 多空比: {_fmt_num(crypto_factors.get('long_short_ratio'))}\n"
                f"- 交易所净流: {_fmt_num(crypto_factors.get('exchange_netflow'), ' USD')}\n"
                f"- 稳定币净流: {_fmt_num(crypto_factors.get('stablecoin_netflow'), ' USD')}\n"
                f"- 衍生品偏向: {signals.get('derivatives_bias', 'neutral')}\n"
                f"- 资金流偏向: {signals.get('flow_bias', 'neutral')}\n"
                f"- 挤仓风险: {signals.get('squeeze_risk', 'low')}\n"
                f"- 因子摘要: {crypto_factors.get('summary') or '暂无'}"
            )

        return (
            f"- 24h volume: {_fmt_num(crypto_factors.get('volume_24h'), ' USD')}\n"
            f"- Volume activity change: {_fmt_pct(crypto_factors.get('volume_change_24h'))}\n"
            f"- Funding rate: {_fmt_pct(crypto_factors.get('funding_rate'))}\n"
            f"- Open interest: {_fmt_num(crypto_factors.get('open_interest'), ' USD')}\n"
            f"- OI change (24h): {_fmt_pct(crypto_factors.get('open_interest_change_24h'))}\n"
            f"- Long/short ratio: {_fmt_num(crypto_factors.get('long_short_ratio'))}\n"
            f"- Exchange netflow: {_fmt_num(crypto_factors.get('exchange_netflow'), ' USD')}\n"
            f"- Stablecoin netflow: {_fmt_num(crypto_factors.get('stablecoin_netflow'), ' USD')}\n"
            f"- Derivatives bias: {signals.get('derivatives_bias', 'neutral')}\n"
            f"- Flow bias: {signals.get('flow_bias', 'neutral')}\n"
            f"- Squeeze risk: {signals.get('squeeze_risk', 'low')}\n"
            f"- Factor summary: {crypto_factors.get('summary') or 'N/A'}"
        )
    
    # ==================== Memory Layer ====================
    
    def _get_memory_context(
        self,
        market: str,
        symbol: str,
        current_indicators: Dict,
        user_id: int | None = None,
    ) -> str:
        """
        Retrieve relevant historical analysis for similar market conditions.
        """
        try:
            from app.services.analysis_memory import get_analysis_memory
            memory = get_analysis_memory()
            
            # Get similar patterns
            patterns = memory.get_similar_patterns(
                market, symbol, current_indicators, limit=3, user_id=user_id
            )
            
            if not patterns:
                return "No similar historical patterns found in memory."
            
            context_lines = ["Historical patterns with similar conditions:"]
            for p in patterns:
                outcome = ""
                if p.get("was_correct") is not None:
                    outcome = f" (Outcome: {'Correct' if p['was_correct'] else 'Incorrect'}"
                    if p.get("actual_return_pct"):
                        outcome += f", Return: {p['actual_return_pct']:.2f}%"
                    outcome += ")"
                
                context_lines.append(
                    f"- Decision: {p['decision']} at ${p.get('price', 'N/A')}{outcome}"
                )
            
            return "\n".join(context_lines)
            
        except Exception as e:
            logger.warning(f"Memory retrieval failed: {e}")
            return "Memory retrieval failed."
    
    # ==================== Prompt Engineering ====================
    
    def _build_analysis_prompt(
        self, data: Dict[str, Any], language: str, user_id: int | None = None
    ) -> tuple:
        """Build the active evidence-first prompt from the versioned report contract."""
        indicators = data.get("indicators") or {}
        memory_context = self._get_memory_context(
            data.get("market", ""), data.get("symbol", ""), indicators, user_id=user_id
        )
        return build_professional_analysis_prompt(
            data, language, memory_context=memory_context
        )

    def _format_financial_statements(self, statements: Dict[str, Any]) -> str:
        return format_financial_statements(statements)
    
    def _format_earnings_data(self, earnings: Dict[str, Any]) -> str:
        """格式化盈利数据用于提示词"""
        if not earnings:
            return "盈利数据暂不可用"
        
        lines = []
        
        if 'history' in earnings and earnings['history']:
            lines.append("历史盈利 (Earnings History):")
            for i, hist in enumerate(earnings['history'][:4], 1):
                date = hist.get('date', 'N/A')
                eps_actual = hist.get('eps_actual')
                eps_estimate = hist.get('eps_estimate')
                surprise = hist.get('surprise')
                
                if eps_actual is not None:
                    line = f"  {i}. {date}: EPS实际={eps_actual:.2f}"
                    if eps_estimate is not None:
                        line += f", 预期={eps_estimate:.2f}"
                    if surprise is not None:
                        surprise_str = f"{surprise:+.1f}%"
                        line += f", 超预期={surprise_str}"
                    lines.append(line)
        
        if 'upcoming' in earnings:
            upcoming = earnings['upcoming']
            if upcoming.get('next_earnings_date'):
                lines.append(f"下次盈利报告: {upcoming['next_earnings_date']}")
                if upcoming.get('eps_estimate'):
                    lines.append(f"  - EPS预期: ${upcoming['eps_estimate']:.2f}")
                if upcoming.get('revenue_estimate'):
                    lines.append(f"  - 收入预期: ${upcoming['revenue_estimate']:,.0f}")
        
        if 'quarterly' in earnings:
            q = earnings['quarterly']
            if q.get('latest_quarter'):
                lines.append(f"最新季度 ({q['latest_quarter']}):")
                if q.get('revenue'):
                    lines.append(f"  - 收入: ${q['revenue']:,.0f}")
                if q.get('earnings'):
                    lines.append(f"  - 盈利: ${q['earnings']:,.0f}")
        
        return "\n".join(lines) if lines else "盈利数据暂不可用"
    
    def _format_macro_summary(self, macro: Dict[str, Any], market: str) -> str:
        """格式化宏观数据摘要"""
        if not macro:
            return "宏观数据暂不可用"
        
        lines = []
        
        if 'DXY' in macro:
            dxy = macro['DXY']
            direction = "↑" if dxy.get('change', 0) > 0 else "↓"
            lines.append(f"- {dxy.get('name', 'USD Index')}: {dxy.get('price', 'N/A')} ({direction}{abs(dxy.get('changePercent', 0)):.2f}%)")
            if market == 'Crypto':
                impact = "利空加密货币" if dxy.get('change', 0) > 0 else "利好加密货币"
                lines.append(f"  ⚠️ 美元{direction} {impact}")
            elif market == 'Forex':
                lines.append(f"  ⚠️ 美元{direction} 直接影响外汇走势")
        
        if 'VIX' in macro:
            vix = macro['VIX']
            vix_value = vix.get('price', 0)
            if vix_value > 30:
                level = "极度恐慌 (>30)"
            elif vix_value > 20:
                level = "较高恐慌 (20-30)"
            elif vix_value > 15:
                level = "正常 (15-20)"
            else:
                level = "低波动 (<15)"
            lines.append(f"- {vix.get('name', 'VIX')}: {vix_value:.2f} - {level}")
        
        if 'TNX' in macro:
            tnx = macro['TNX']
            direction = "↑" if tnx.get('change', 0) > 0 else "↓"
            lines.append(f"- {tnx.get('name', '10Y Treasury')}: {tnx.get('price', 'N/A'):.3f}% ({direction})")
            if tnx.get('price', 0) > 4.5:
                lines.append("  ⚠️ 高利率环境，对估值不利")
        
        if 'GOLD' in macro:
            gold = macro['GOLD']
            direction = "↑" if gold.get('change', 0) > 0 else "↓"
            lines.append(f"- {gold.get('name', 'Gold')}: ${gold.get('price', 'N/A'):.2f} ({direction}{abs(gold.get('changePercent', 0)):.2f}%)")
        
        if 'SPY' in macro:
            spy = macro['SPY']
            direction = "↑" if spy.get('change', 0) > 0 else "↓"
            lines.append(f"- {spy.get('name', 'S&P 500')}: ${spy.get('price', 'N/A'):.2f} ({direction}{abs(spy.get('changePercent', 0)):.2f}%)")
        
        if 'BTC' in macro and market != 'Crypto':
            btc = macro['BTC']
            direction = "↑" if btc.get('change', 0) > 0 else "↓"
            lines.append(f"- {btc.get('name', 'BTC')}: ${btc.get('price', 'N/A'):,.0f} ({direction}{abs(btc.get('changePercent', 0)):.2f}%) [风险偏好指标]")
        
        return "\n".join(lines) if lines else "宏观数据暂不可用"
    
    # ==================== Main Analysis ====================

    def _call_analysis_models(
        self,
        system_prompt: str,
        user_prompt: str,
        default_struct: Dict[str, Any],
        model: Optional[str],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Call one or more models and enforce the narrative schema."""
        evidence_ids = {
            str(item.get("evidence_id"))
            for item in build_evidence_snapshot(data).get("observations") or []
            if item.get("evidence_id")
        }
        ensemble_models = []
        if os.getenv("ENABLE_AI_ENSEMBLE", "false").lower() == "true":
            ensemble_models = [
                item.strip()
                for item in (os.getenv("AI_ENSEMBLE_MODELS") or "").split(",")
                if item.strip()
            ][:3]
        if len(ensemble_models) < 2:
            raw = self.llm_service.safe_call_llm(
                system_prompt, user_prompt, default_structure=default_struct, model=model
            )
            return validate_llm_analysis(
                raw, default_struct, known_evidence_ids=evidence_ids
            )

        from collections import Counter

        analyses = [
            validate_llm_analysis(
                self.llm_service.safe_call_llm(
                    system_prompt, user_prompt, default_structure=default_struct, model=item
                ),
                default_struct,
                known_evidence_ids=evidence_ids,
            )
            for item in ensemble_models
        ]
        decisions = [str(item.get("decision") or "HOLD").upper() for item in analyses]
        vote = Counter(decisions).most_common(1)[0][0]
        selected = analyses[decisions.index(vote)].copy()
        selected.update({
            "decision": vote,
            "_ensemble_vote": dict(Counter(decisions)),
            "_ensemble_models": ensemble_models,
        })
        return selected

    @staticmethod
    def _replace_failed_llm_narrative(
        analysis: Dict[str, Any],
        trend_outlook_summary: str,
        market_factor_summary: str = "",
    ) -> bool:
        """Replace provider-error copy with deterministic analysis already computed locally."""
        summary = str(analysis.get("summary") or "").strip()
        report = str(analysis.get("report") or "").strip()
        failure_markers = ("analysis failed", "failed to parse")
        if not any(
            value.lower().startswith(failure_markers)
            for value in (summary, report)
            if value
        ):
            return False

        deterministic_summary = str(
            trend_outlook_summary or market_factor_summary or ""
        ).strip()
        analysis["summary"] = deterministic_summary
        analysis["key_reasons"] = [
            value
            for value in (trend_outlook_summary, market_factor_summary)
            if str(value or "").strip()
        ]
        analysis["risks"] = []
        analysis["_llm_fallback_used"] = True
        return True

    @staticmethod
    def _attach_professional_report(
        result: Dict[str, Any],
        data: Dict[str, Any],
        market: str,
        symbol: str,
    ) -> None:
        """Attach V1 and apply its deterministic quality gate to legacy fields."""
        try:
            report = build_professional_report(
                data,
                result,
                data_tier=os.getenv("PROFESSIONAL_REPORT_DATA_TIER", "community").strip().lower(),
                account_risk_budget_pct=float(
                    os.getenv("PROFESSIONAL_REPORT_RISK_BUDGET_PCT", "1.0") or 1.0
                ),
            )
            result["professional_report"] = report
            profile = report.get("decision_profile") or {}
            decision = str(profile.get("decision") or result["decision"]).upper()
            confidence = int(float(profile.get("confidence") or result["confidence"]))
            if decision != str(result.get("decision") or "HOLD").upper():
                logger.warning(
                    "Professional data-quality gate changed decision %s -> %s for %s:%s",
                    result.get("decision"), decision, market, symbol,
                )
                result["decision"] = decision
                result["trading_plan"]["decision"] = decision
            if decision == "HOLD":
                # HOLD must never retain a stale model-proposed position, even
                # when the deterministic gate agrees with the model decision.
                result["trading_plan"].update({
                    "position_size_pct": 0,
                    "positionSizePct": 0,
                })
            else:
                position_cap = (report.get("risk_plan") or {}).get(
                    "recommended_position_pct"
                )
                if position_cap is not None:
                    result["trading_plan"].update({
                        "position_size_pct": position_cap,
                        "positionSizePct": position_cap,
                    })
            result["confidence"] = confidence
        except Exception as exc:
            logger.error(
                "Professional report build failed for %s:%s: %s",
                market, symbol, exc, exc_info=True,
            )
            result["professional_report_error"] = str(exc)

    def analyze(self, market: str, symbol: str, language: str = 'en-US',
                model: str = None, timeframe: str = "1D", user_id: int = None,
                research_mode: str = "research") -> Dict[str, Any]:
        """
        Run fast single-call analysis.
        
        Args:
            market: Market type (Crypto, USStock, etc.)
            symbol: Trading pair or stock symbol
            language: Response language (zh-CN or en-US)
            model: LLM model to use
            timeframe: Analysis timeframe (1D, 4H, etc.)
            user_id: User ID for storing analysis history
        
        Returns:
            Complete analysis result with actionable recommendations.
        """
        market = self._normalize_analysis_market(market)
        start_time = time.time()
        # Get default model if not specified
        if not model:
            model = self.llm_service.get_default_model()
            logger.debug(f"Using default model: {model}")
        
        result = {
            "market": market,
            "symbol": symbol,
            "language": language,
            "model": model,  # Include model in result from the start
            "timeframe": timeframe,
            "research_mode": research_mode,
            "analysis_time_ms": 0,
            "error": None,
        }
        
        try:
            # Phase 1: Data collection (multi-timeframe for consensus)
            logger.info(f"Fast analysis starting: {market}:{symbol}")

            # Consensus timeframes:
            env_tfs = os.getenv("AI_ANALYSIS_CONSENSUS_TIMEFRAMES", "").strip()
            if env_tfs:
                consensus_timeframes = [t.strip() for t in env_tfs.split(",") if t.strip()]
            else:
                # Heuristic defaults
                tf0 = (timeframe or "").strip().upper()
                # Primary first
                consensus_timeframes = [tf0] if tf0 else [timeframe]
                # Add 4H/1D depending on primary
                if tf0 in ("1H", "1HOUR", "60M"):
                    consensus_timeframes += ["4H", "1D"]
                elif tf0 in ("4H",):
                    consensus_timeframes += ["1D"]
                elif tf0 in ("1D", "1DAY", "D"):
                    consensus_timeframes += ["4H"]
                else:
                    # Generic fallback
                    consensus_timeframes += ["1D", "4H"]
                # Dedup keep order
                seen = set()
                consensus_timeframes = [x for x in consensus_timeframes if not (x in seen or seen.add(x))]

            primary_tf = (timeframe or "").strip().upper() or "1D"
            # Always include the primary timeframe in consensus,
            # even when env overrides timeframes.
            if primary_tf and primary_tf not in consensus_timeframes:
                consensus_timeframes = [primary_tf] + list(consensus_timeframes)
                # De-dup keep order
                seen = set()
                consensus_timeframes = [x for x in consensus_timeframes if not (x in seen or seen.add(x))]
            # Collect primary data (macro + news) for prompt quality
            primary_data = self._collect_market_data(
                market,
                symbol,
                primary_tf,
                include_macro=True,
                include_news=True,
            )
            primary_data["research_mode"] = research_mode

            # Collect extra timeframes for objective consensus (technical-only for cost)
            objective_by_tf: Dict[str, Dict[str, Any]] = {}
            decision_votes: Dict[str, int] = {"BUY": 0, "SELL": 0, "HOLD": 0}
            weighted_score_sum = 0.0
            weighted_score_w_sum = 0.0

            def _extract_current_price(d: Dict[str, Any]) -> Optional[float]:
                if d.get("price") and d["price"].get("price"):
                    try:
                        return float(d["price"]["price"])
                    except Exception:
                        return None
                ind = d.get("indicators") or {}
                cp = ind.get("current_price")
                try:
                    if cp:
                        return float(cp)
                except Exception:
                    pass
                # fallback to kline close
                kl = d.get("kline") or []
                if kl:
                    try:
                        return float(kl[-1].get("close") or 0)
                    except Exception:
                        return None
                return None

            logger.info(f"Consensus timeframes: {consensus_timeframes}")
            for tf in consensus_timeframes:
                tf_norm = (tf or "").strip().upper()
                if not tf_norm:
                    continue

                if tf_norm == primary_tf:
                    d_tf = primary_data
                else:
                    d_tf = self._collect_market_data(
                        market,
                        symbol,
                        tf_norm,
                        include_macro=False,
                        include_news=False,
                        timeout=25, recovery_target=primary_data,
                    )

                current_price_tf = _extract_current_price(d_tf) or 0.0
                objective = self._calculate_objective_score(d_tf, current_price_tf)
                overall_score = float(objective.get("overall_score", 0.0) or 0.0)
                decision = self._score_to_decision(overall_score, market=market)
                abs_score = abs(overall_score)

                objective_by_tf[tf_norm] = {
                    "objective_score": objective,
                    "overall_score": overall_score,
                    "decision": decision,
                    "abs_score": abs_score,
                }
                decision_votes[decision] = decision_votes.get(decision, 0) + 1

                # Weight by timeframe and strength. Longer frames should anchor
                # regime direction; short-frame oversold bounces must not
                # dominate a 1D/1W downtrend.
                tf_base_weights = {
                    "1M": 0.75,
                    "3M": 0.75,
                    "5M": 0.80,
                    "15M": 0.85,
                    "30M": 0.90,
                    "1H": 0.95,
                    "4H": 1.10,
                    "1D": 1.30,
                    "1W": 1.35,
                }
                w = float(tf_base_weights.get(tf_norm, 1.0)) * (1.0 + min(1.0, abs_score / 100.0))
                weighted_score_sum += overall_score * w
                weighted_score_w_sum += w

            # Extra horizon score (not used in consensus override):
            # add 1W objective score for short/medium trend outlook.
            if "1W" not in objective_by_tf:
                try:
                    d_1w = self._collect_market_data(
                        market,
                        symbol,
                        "1W",
                        include_macro=False,
                        include_news=False,
                        timeout=25, recovery_target=primary_data,
                    )
                    cp_1w = _extract_current_price(d_1w) or 0.0
                    obj_1w = self._calculate_objective_score(d_1w, cp_1w)
                    sc_1w = float(obj_1w.get("overall_score", 0.0) or 0.0)
                    objective_by_tf["1W"] = {
                        "objective_score": obj_1w,
                        "overall_score": sc_1w,
                        "decision": self._score_to_decision(sc_1w, market=market),
                        "abs_score": abs(sc_1w),
                    }
                except Exception as e:
                    logger.debug(f"1W outlook score skipped: {e}")

            # Short-horizon outlook: 1H bar (24h-style), not 1D close
            if "1H" not in objective_by_tf:
                try:
                    d_1h = self._collect_market_data(
                        market,
                        symbol,
                        "1H",
                        include_macro=False,
                        include_news=False,
                        timeout=18, recovery_target=primary_data,
                    )
                    cp_1h = _extract_current_price(d_1h) or 0.0
                    obj_1h = self._calculate_objective_score(d_1h, cp_1h)
                    sc_1h = float(obj_1h.get("overall_score", 0.0) or 0.0)
                    objective_by_tf["1H"] = {
                        "objective_score": obj_1h,
                        "overall_score": sc_1h,
                        "decision": self._score_to_decision(sc_1h, market=market),
                        "abs_score": abs(sc_1h),
                    }
                except Exception as e:
                    logger.debug(f"1H outlook score skipped: {e}")

            consensus_score = weighted_score_sum / weighted_score_w_sum if weighted_score_w_sum > 0 else 0.0
            consensus_decision = self._score_to_decision(consensus_score, market=market)
            consensus_abs = abs(consensus_score)

            # Agreement factor: how many timeframes support the consensus decision
            tf_count = max(1, len(objective_by_tf))
            agreement_cnt = sum(1 for x in objective_by_tf.values() if str(x.get("decision") or "").upper() == consensus_decision)
            agreement_ratio = agreement_cnt / tf_count

            # Data quality degradation: derive from primary_data meta
            meta = primary_data.get("_meta") or {}
            failed_items = set(meta.get("failed_items") or [])
            quality_multiplier = 1.0
            if "macro" in failed_items:
                quality_multiplier *= 0.85
            if "news" in failed_items:
                quality_multiplier *= 0.8
            # If indicators missing key sections, reduce confidence more
            ind = primary_data.get("indicators") or {}
            if not ind or not ind.get("rsi") or not ind.get("moving_averages"):
                quality_multiplier *= 0.65

            logger.info(
                f"Consensus decision={consensus_decision}, score={consensus_score:.2f}, "
                f"agreement_ratio={agreement_ratio:.2f}, quality_multiplier={quality_multiplier:.2f}"
            )

            data = primary_data  # keep original variable usage for prompt/LLM input
            
            # Validate we have essential data - with fallback to indicators
            current_price = None
            
            if data.get("price") and data["price"].get("price"):
                current_price = data["price"]["price"]
            
            if not current_price and data.get("indicators"):
                current_price = data["indicators"].get("current_price")
                if current_price:
                    logger.info(f"Using price from indicators: ${current_price}")
                    data["price"] = {
                        "price": current_price,
                        "change": 0,
                        "changePercent": 0,
                        "source": "indicators_fallback"
                    }
            
            if not current_price and data.get("kline"):
                klines = data["kline"]
                if klines and len(klines) > 0:
                    current_price = float(klines[-1].get("close", 0))
                    if current_price > 0:
                        logger.info(f"Using price from kline: ${current_price}")
                        prev_close = float(klines[-2].get("close", current_price)) if len(klines) > 1 else current_price
                        change = current_price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                        data["price"] = {
                            "price": current_price,
                            "change": round(change, 6),
                            "changePercent": round(change_pct, 2),
                            "source": "kline_fallback"
                        }
            
            if not current_price or current_price <= 0:
                result["error"] = "Failed to fetch current price from all sources"
                logger.error(f"Price fetch failed for {market}:{symbol}, all sources exhausted")
                return result
            
            # Phase 2: Build prompt
            system_prompt, user_prompt = self._build_analysis_prompt(
                data, language, user_id=user_id
            )

            default_struct = {
                "decision": "HOLD",
                "confidence": 50,
                "summary": "Analysis failed",
                "analysis": {"technical": "", "fundamental": "", "sentiment": ""},
                "entry_price": current_price,
                "stop_loss": current_price * 0.95,
                "take_profit": current_price * 1.05,
                "position_size_pct": 10,
                "timeframe": "medium",
                "key_reasons": ["Unable to analyze"],
                "risks": ["Analysis error"],
                "evidence_claims": [],
                "technical_score": 50,
                "fundamental_score": 50,
                "sentiment_score": 50,
            }

            # Phase 3: LLM call(s) - single or ensemble voting
            logger.info("Calling LLM for analysis...")
            llm_start = time.time()
            analysis = self._call_analysis_models(
                system_prompt, user_prompt, default_struct, model, data
            )

            llm_time = int((time.time() - llm_start) * 1000)
            logger.info(f"LLM call completed in {llm_time}ms")
            
            # Phase 4: Objective score (primary tf) + consensus calibration
            objective_score = self._calculate_objective_score(data, current_price)
            logger.info(
                f"Primary objective score: {objective_score['overall_score']:.1f} "
                f"(Technical: {objective_score['technical_score']:.1f}, Fundamental: {objective_score['fundamental_score']:.1f}, "
                f"Sentiment: {objective_score['sentiment_score']:.1f}, Macro: {objective_score['macro_score']:.1f})"
            )
            crypto_factor_score = objective_score.get("crypto_factor_score")
            crypto_factor_summary = objective_score.get("crypto_factor_summary") or (data.get("crypto_factors") or {}).get("summary", "")

            score_based_decision = self._score_to_decision(objective_score["overall_score"], market=market)
            llm_decision = str(analysis.get("decision", "HOLD") or "HOLD").upper()
            if market == "Crypto" and crypto_factor_score is not None:
                analysis["fundamental_score"] = max(0, min(100, int(round((float(crypto_factor_score) + 100.0) / 2.0))))

            # Horizon trend outlook for users (short/medium/long decision reference)
            score_1d = float((objective_by_tf.get("1D") or {}).get("overall_score", objective_score.get("overall_score", 0.0)) or 0.0)
            score_4h = float((objective_by_tf.get("4H") or {}).get("overall_score", score_1d) or score_1d)
            score_1h = float((objective_by_tf.get("1H") or {}).get("overall_score", score_4h) or score_4h)
            # ~24h: prefer 1H bar objective; fall back 4H -> 1D
            score_24h = float(score_1h)
            score_1w = float((objective_by_tf.get("1W") or {}).get("overall_score", score_1d) or score_1d)
            score_3d = score_1d * 0.7 + score_4h * 0.3
            score_1m = score_1w * 0.55 + float(objective_score.get("fundamental_score", 0.0)) * 0.30 + float(objective_score.get("macro_score", 0.0)) * 0.15
            horizon_risk = self._technical_risk_context(data.get("indicators") or {}, data.get("price") or {})
            if horizon_risk.get("panic_breakdown"):
                score_24h = min(score_24h, -20.0)
                score_3d = min(score_3d, -10.0)
                score_1w = min(score_1w, 0.0)
                score_1m = min(score_1m, 10.0)
            elif horizon_risk.get("bearish_context") and horizon_risk.get("change_24h", 0.0) <= -3.0:
                score_24h = min(score_24h, 5.0)
                score_3d = min(score_3d, 10.0)

            def _trend_strength(score_val: float) -> str:
                a = abs(float(score_val))
                if a >= 70:
                    return "strong"
                if a >= 40:
                    return "moderate"
                if a >= 20:
                    return "mild"
                return "neutral"

            trend_outlook = {
                "next_24h": {
                    "score": round(score_24h, 2),
                    "trend": self._score_to_decision(score_24h, market=market),
                    "strength": _trend_strength(score_24h),
                },
                "next_3d": {
                    "score": round(score_3d, 2),
                    "trend": self._score_to_decision(score_3d, market=market),
                    "strength": _trend_strength(score_3d),
                },
                "next_1w": {
                    "score": round(score_1w, 2),
                    "trend": self._score_to_decision(score_1w, market=market),
                    "strength": _trend_strength(score_1w),
                },
                "next_1m": {
                    "score": round(score_1m, 2),
                    "trend": self._score_to_decision(score_1m, market=market),
                    "strength": _trend_strength(score_1m),
                },
            }
            trend_outlook_summary = build_trend_outlook_summary(trend_outlook, language)
            self._replace_failed_llm_narrative(
                analysis,
                trend_outlook_summary,
                crypto_factor_summary,
            )

            # Consensus confidence:
            consensus_conf = int(max(40, min(98, 50 + consensus_abs * 0.35)))
            # Agreement boosts, disagreement reduces
            consensus_conf = int(max(35, min(98, consensus_conf * (0.85 + 0.3 * agreement_ratio))))
            consensus_conf = int(max(0, min(100, consensus_conf * quality_multiplier)))

            # Decide whether to enforce consensus over LLM / primary-score decision
            cfg = self._get_ai_calibration(market=market)
            min_abs_override = float(cfg.get("min_consensus_abs_override") or 15.0)
            quality_hold_thr = float(cfg.get("quality_hold_threshold") or 0.7)
            regime = self._detect_market_regime(data.get("indicators") or {})
            risk_context = self._technical_risk_context(data.get("indicators") or {}, data.get("price") or {})
            if regime == "ranging":
                min_abs_override *= 1.2
            if (
                consensus_decision == "BUY"
                and llm_decision in ("SELL", "HOLD")
                and (risk_context.get("panic_breakdown") or risk_context.get("bearish_context"))
            ):
                min_abs_override = max(min_abs_override, 55.0 if risk_context.get("panic_breakdown") else 40.0)

            if should_override_with_consensus(consensus_decision, consensus_abs, min_abs_override):
                final_decision = consensus_decision
                if llm_decision != final_decision:
                    logger.warning(
                        f"Override: llm_decision={llm_decision}, consensus_decision={final_decision}, "
                        f"consensus_score={consensus_score:.1f}, consensus_abs={consensus_abs:.1f}"
                    )
                analysis["decision"] = final_decision
                analysis["confidence"] = consensus_conf
                original_summary = analysis.get("summary", "")
                is_zh = str(language or "").lower().startswith("zh")
                zh_outlook = {"BUY": "利多", "SELL": "利空", "HOLD": "中性"}.get(final_decision, "中性")
                en_outlook = {"BUY": "bullish", "SELL": "bearish", "HOLD": "neutral"}.get(final_decision, "neutral")
                if is_zh:
                    level = "强烈" if consensus_abs >= 70 else "明显" if consensus_abs >= 40 else "轻微"
                    bias = "利多" if consensus_score > 0 else "利空"
                    consensus_note = (
                        f"[多周期客观共识：综合评分{consensus_score:.1f}分（{level}{bias}），AI倾向{zh_outlook}]"
                    )
                else:
                    level = "strong" if consensus_abs >= 70 else "moderate" if consensus_abs >= 40 else "mild"
                    bias = "bullish" if consensus_score > 0 else "bearish"
                    consensus_note = (
                        f"[Multi-timeframe objective consensus: score {consensus_score:.1f} "
                        f"({level} {bias}), AI outlook {en_outlook}]"
                    )
                analysis["summary"] = f"{original_summary} {consensus_note}".strip()
            else:
                # Near-neutral: keep LLM but shrink confidence by quality and enforce HOLD if quality is poor
                analysis["confidence"] = int(max(0, min(100, int(analysis.get("confidence", 50) or 50) * quality_multiplier)))

                if quality_multiplier < quality_hold_thr:
                    analysis["decision"] = "HOLD"
                    analysis["confidence"] = min(int(analysis.get("confidence", 50) or 50), 55)

            # Add objective scores and consensus to analysis
            analysis["objective_score"] = objective_score
            analysis["score_based_decision"] = score_based_decision
            analysis["objective_scores_by_timeframe"] = {
                k: {
                    "overall_score": v.get("overall_score"),
                    "decision": v.get("decision"),
                    "abs_score": v.get("abs_score"),
                }
                for k, v in objective_by_tf.items()
            }
            analysis["consensus"] = {
                "consensus_score": consensus_score,
                "consensus_decision": consensus_decision,
                "consensus_abs": consensus_abs,
                "agreement_ratio": agreement_ratio,
                "quality_multiplier": quality_multiplier,
                "market_regime": regime,
                "risk_context": risk_context,
            }
            
            # Phase 5: Validate and constrain output (pass indicators for decision validation)
            # Check for major news or macro events that could override technical indicators
            news_data = data.get("news") or []
            macro_data = data.get("macro") or {}
            has_major_news = self._has_major_news(news_data)
            has_macro_event = self._has_macro_event(macro_data, data.get("market", ""))
            
            analysis = self._validate_and_constrain(
                analysis, 
                current_price, 
                indicators=data.get("indicators"),
                has_major_news=has_major_news,
                has_macro_event=has_macro_event
            )

            # Post-validate: adjust position sizing based on quality + agreement
            try:
                ps = analysis.get("position_size_pct", 10)
                ps = int(float(ps or 10))
                # Lower position size if data is incomplete or multi-timeframe disagreement exists
                # agreement_ratio in [0..1]
                agreement_scale = 0.6 + 0.4 * float(agreement_ratio)
                ps_scaled = ps * float(quality_multiplier) * agreement_scale
                if str(analysis.get("decision") or "").upper() == "HOLD":
                    analysis["position_size_pct"] = 0
                else:
                    analysis["position_size_pct"] = max(1, min(100, int(round(ps_scaled))))
            except Exception:
                # Keep model-provided position_size_pct
                pass

            # Confidence calibration: adjust by historical accuracy in bucket
            if os.getenv("ENABLE_CONFIDENCE_CALIBRATION", "false").lower() == "true":
                try:
                    from app.services.analysis_memory import get_analysis_memory
                    raw_conf = int(analysis.get("confidence", 50) or 50)
                    analysis["confidence"] = get_analysis_memory().get_adjusted_confidence(
                        raw_conf,
                        user_id=int(user_id),
                        market=market,
                        symbol=symbol,
                    )
                except Exception as e:
                    logger.debug(f"Confidence calibration skipped: {e}")
            
            # Build final result
            total_time = int((time.time() - start_time) * 1000)
            
            # Extract detailed analysis sections
            detailed_analysis = analysis.get("analysis", {})
            if isinstance(detailed_analysis, str):
                # If AI returned a string instead of dict, use it as technical analysis
                detailed_analysis = {"technical": detailed_analysis, "fundamental": "", "sentiment": ""}
            if market == "Crypto" and not detailed_analysis.get("fundamental"):
                detailed_analysis["fundamental"] = crypto_factor_summary or (data.get("crypto_factors") or {}).get("summary", "")

            score_payload = build_score_payload(objective_score, analysis, self._calculate_overall_score(analysis))
            provenance_payload = fundamental_provenance(data.get("fundamental") or {})
            
            result.update({
                "decision": analysis.get("decision", "HOLD"),
                "confidence": analysis.get("confidence", 50),
                "summary": analysis.get("summary", ""),
                "model": model,  # Model is already set in result initialization
                "language": language,  # Ensure language is included for task record
                "detailed_analysis": {
                    "technical": detailed_analysis.get("technical", ""),
                    "fundamental": detailed_analysis.get("fundamental", ""),
                    "sentiment": detailed_analysis.get("sentiment", ""),
                },
                "trading_plan": {
                    "entry_price": analysis.get("entry_price"),
                    "stop_loss": analysis.get("stop_loss"),
                    "take_profit": analysis.get("take_profit"),
                    **trading_plan_risk_fields(analysis),
                    "position_size_pct": analysis.get("position_size_pct", 10),
                    "timeframe": analysis.get("timeframe", "medium"),
                    "entryPrice": analysis.get("entry_price"),
                    "stopLoss": analysis.get("stop_loss"),
                    "takeProfit": analysis.get("take_profit"),
                    "positionSizePct": analysis.get("position_size_pct", 10),
                    "decision": str(analysis.get("decision", "HOLD") or "HOLD").upper(),
                    "loss_exit_price": analysis.get("stop_loss"),
                    "profit_target_price": analysis.get("take_profit"),
                },
                "reasons": analysis.get("key_reasons", []),
                "risks": analysis.get("risks", []),
                "evidence_claims": analysis.get("evidence_claims", []),
                "llm_contract": analysis.get("_llm_contract", {}),
                **score_payload,
                "objective_score": analysis.get("objective_score", {}),
                "crypto_factors": data.get("crypto_factors", {}),
                "crypto_factor_score": crypto_factor_score,
                "crypto_factor_breakdown": objective_score.get("crypto_factor_breakdown", []),
                "crypto_factor_summary": crypto_factor_summary,
                "score_based_decision": analysis.get("score_based_decision", "HOLD"),
                "market_data": {
                    "current_price": current_price,
                    "change_24h": data["price"].get("changePercent", 0),
                    "support": data["indicators"].get("levels", {}).get("support"),
                    "resistance": data["indicators"].get("levels", {}).get("resistance"),
                },
                **provenance_payload,
                "indicators": data.get("indicators", {}),
                "consensus": analysis.get("consensus", {}),
                "trend_outlook": trend_outlook,
                "trend_outlook_summary": trend_outlook_summary,
                "trendOutlook": trend_outlook,
                "trendOutlookSummary": trend_outlook_summary,
                "analysis_time_ms": total_time,
                "llm_time_ms": llm_time,
                "data_collection_time_ms": (data.get("_meta") or {}).get("duration_ms", 0),
            })

            self._attach_professional_report(result, data, market, symbol)
            
            # Store in memory for future retrieval and get memory_id for feedback
            memory_id = self._store_analysis_memory(result, user_id=user_id)
            if memory_id:
                result["memory_id"] = memory_id
            
            logger.info(f"Fast analysis completed in {total_time}ms: {market}:{symbol} -> {result['decision']} (memory_id={memory_id}, user_id={user_id})")
            
        except Exception as e:
            logger.error(f"Fast analysis failed: {e}", exc_info=True)
            result["error"] = str(e)
        
        return result
    
    def _build_decision_guidance(self, rsi_value: float, macd_signal: str, ma_trend: str, change_24h: float) -> str:
        """Build symmetric, confirmation-aware directional guidance."""
        guidance_parts = []
        ma_trend_low = str(ma_trend or "").lower()
        uptrend = "uptrend" in ma_trend_low
        downtrend = "downtrend" in ma_trend_low
        
        if rsi_value > 70:
            guidance_parts.append("RSI > 70 (overbought): pullback risk is elevated, but this alone is not a SELL signal.")
        elif rsi_value > 60:
            guidance_parts.append("RSI > 60: momentum is extended; require confirmation before a counter-trend SELL.")
        elif rsi_value < 30:
            guidance_parts.append("RSI < 30 (oversold): rebound potential is elevated, but this alone is not a BUY signal.")
        elif rsi_value < 40:
            guidance_parts.append("RSI < 40: downside momentum is extended; require confirmation before a counter-trend BUY.")
        else:
            guidance_parts.append("RSI 40-60: neutral; use trend, momentum and catalysts for direction.")
        
        if macd_signal == "bullish":
            guidance_parts.append("MACD bullish: positive momentum confirmation.")
        elif macd_signal == "bearish":
            guidance_parts.append("MACD bearish: negative momentum confirmation.")
        else:
            guidance_parts.append("MACD neutral: no momentum confirmation.")
        
        if uptrend:
            if rsi_value > 60:
                guidance_parts.append(
                    "Uptrend plus overbought RSI: do not short without trend damage, bearish momentum/volume, a negative catalyst, or multi-timeframe bearish confirmation."
                )
            else:
                guidance_parts.append("MA trend up: trend-following BUY evidence; still validate entry risk.")
        elif downtrend:
            if rsi_value < 40:
                guidance_parts.append(
                    "Downtrend plus oversold RSI: do not buy without trend recovery, bullish momentum/volume, a positive catalyst, or multi-timeframe bullish confirmation."
                )
            else:
                guidance_parts.append("MA trend down: trend-following SELL evidence; still validate entry risk.")
        else:
            guidance_parts.append("MA trend sideways: prefer HOLD unless range boundaries provide a confirmed setup.")
        
        if change_24h > 5:
            guidance_parts.append("24h rise > 5%: extended move is a risk flag, not an automatic short.")
        elif change_24h < -5:
            guidance_parts.append("24h drop > 5%: extended move is a risk flag, not an automatic long.")

        bullish_confirmations = int(macd_signal == "bullish") + int(uptrend)
        bearish_confirmations = int(macd_signal == "bearish") + int(downtrend)
        if bullish_confirmations >= 2:
            guidance_parts.append("Combined view: trend and momentum confirm upside; BUY may be considered.")
        elif bearish_confirmations >= 2:
            guidance_parts.append("Combined view: trend and momentum confirm downside; SELL may be considered.")
        else:
            guidance_parts.append("Combined view: directional confirmation is incomplete; HOLD is appropriate unless other strong evidence exists.")
        
        return "\n".join(guidance_parts) if guidance_parts else "Technical data is insufficient; prefer HOLD."
    
    def _has_major_news(self, news_data: List[Dict]) -> bool:
        """
        检查是否有重大新闻事件。
        重大新闻包括：监管变化、重大合作、丑闻、重大政策、地缘政治事件等。
        地缘类使用词边界与分级，避免 toward/extension/us 等子串误判。
        """
        if not news_data:
            return False

        major_keywords = [
            "regulation", "regulatory", "approval", "policy", "government", "central bank",
            "监管", "禁令", "批准", "政策", "政府", "央行",
            "partnership", "merger", "acquisition", "scandal", "lawsuit", "investigation",
            "合作", "合并", "收购", "丑闻", "诉讼", "调查",
            "sanctions", "embargo", "制裁", "中东", "海湾", "北约",
            "united states", "middle east",
        ]
        major_short_patterns = [
            re.compile(r"\b(?:ban|banned|banning)\b", re.I),
            re.compile(r"\b(?:crisis|crises)\b", re.I),
            re.compile(r"\b(?:catastrophe|meltdown)\b", re.I),
        ]

        for news in news_data[:10]:
            if str(news.get("asset_relevance") or "direct").lower() not in {"direct", "material"}:
                continue
            title = news.get("title") or news.get("headline") or ""
            summary = news.get("summary") or ""
            sentiment = news.get("sentiment", "neutral")
            text_to_check = f"{title} {summary}"
            low = text_to_check.lower()

            if is_major_geopolitical_news_text(text_to_check):
                logger.info(f"Detected major geopolitical event in news: {low[:80]}")
                return True

            if any(kw in low for kw in major_keywords) and sentiment != "neutral":
                logger.info(f"Detected major news event: {low[:80]}")
                return True
            if sentiment != "neutral" and any(p.search(low) for p in major_short_patterns):
                logger.info(f"Detected major news event (pattern): {low[:80]}")
                return True

        return False
    
    def _has_macro_event(self, macro_data: Dict, market: str) -> bool:
        """
        检查是否有重大宏观事件。
        重大宏观事件包括：VIX异常高、DXY大幅波动、利率政策变化等。
        """
        if not macro_data:
            return False
        
        if "VIX" in macro_data:
            vix = macro_data["VIX"]
            vix_value = vix.get("price", 0)
            if vix_value > 30:  # VIX > 30 表示极度恐慌
                return True
        
        if "DXY" in macro_data:
            dxy = macro_data["DXY"]
            change_pct = abs(dxy.get("changePercent", 0))
            if change_pct > 1.0:  # 美元指数波动超过1%
                return True
        
        if "TNX" in macro_data and market in ["USStock", "Crypto"]:
            tnx = macro_data["TNX"]
            change_pct = abs(tnx.get("changePercent", 0))
            if change_pct > 2.0:  # 利率变化超过2%
                return True
        
        return False
    
    def _finalize_trading_plan_for_decision(
        self, analysis: Dict, current_price: float, indicators: Optional[Dict] = None
    ) -> Dict:
        return finalize_trading_plan(analysis, current_price, indicators)

    def _validate_and_constrain(self, analysis: Dict, current_price: float, indicators: Dict = None,
                                 has_major_news: bool = False, has_macro_event: bool = False) -> Dict:
        """
        Validate LLM output and constrain prices to reasonable ranges.
        Also validate decision against technical indicators to prevent absurd recommendations.
        """
        if not current_price or current_price <= 0:
            return analysis
        
        # Price bounds
        min_price = current_price * 0.90
        max_price = current_price * 1.10
        decision = str(analysis.get("decision", "HOLD")).upper()
        
        # Constrain entry price
        entry = safe_float_price(analysis.get("entry_price"), current_price)
        if decision != "HOLD" and entry is not None and (entry < min_price or entry > max_price):
            logger.warning(f"Entry price {entry} out of bounds, constraining to current price {current_price}")
            analysis["entry_price"] = round(current_price, 6)
        elif entry is not None:
            analysis["entry_price"] = round(entry, 6)
        
        # Constrain confidence
        confidence = analysis.get("confidence", 50)
        analysis["confidence"] = max(0, min(100, int(confidence)))
        
        # Constrain scores
        for score_key in ["technical_score", "fundamental_score", "sentiment_score"]:
            score = analysis.get(score_key, 50)
            analysis[score_key] = max(0, min(100, int(score)))
        
        # Validate decision
        if decision not in ["BUY", "SELL", "HOLD"]:
            analysis["decision"] = "HOLD"
        else:
            analysis["decision"] = decision
        
        if indicators:
            analysis = self._validate_decision_against_indicators(
                analysis, indicators, confidence, 
                has_major_news=has_major_news, 
                has_macro_event=has_macro_event
            )

        # Final geometry after any decision change (e.g. forced HOLD skips finalize in caller — still safe)
        analysis = self._finalize_trading_plan_for_decision(analysis, current_price, indicators)
        
        return analysis
    
    def _validate_decision_against_indicators(self, analysis: Dict, indicators: Dict, confidence: int, 
                                               has_major_news: bool = False, has_macro_event: bool = False) -> Dict:
        """
        根据技术指标验证决策的合理性，但允许宏观/新闻因素覆盖技术指标。
        
        Args:
            analysis: AI分析结果
            indicators: 技术指标数据
            confidence: 置信度
            has_major_news: 是否有重大新闻事件
            has_macro_event: 是否有重大宏观事件
        """
        decision = analysis.get("decision", "HOLD")
        rsi_data = indicators.get("rsi", {})
        macd_data = indicators.get("macd", {})
        ma_data = indicators.get("moving_averages", {})
        
        rsi_value = rsi_data.get("value", 50)
        macd_signal = macd_data.get("signal", "neutral")
        ma_trend = ma_data.get("trend", "sideways")
        trend_low = str(ma_trend or "sideways").lower()
        current_indicator_price = safe_float_price(indicators.get("current_price"))
        ma20 = safe_float_price(ma_data.get("ma20"))
        try:
            price_position = float(indicators.get("price_position", 50) or 50)
        except Exception:
            price_position = 50.0
        try:
            volume_ratio = float(indicators.get("volume_ratio", 1) or 1)
        except Exception:
            volume_ratio = 1.0
        objective_by_tf = analysis.get("objective_scores_by_timeframe") or {}
        bearish_tf_count = sum(
            1
            for value in objective_by_tf.values()
            if str((value or {}).get("decision") or "").upper() == "SELL"
        )
        bullish_tf_count = sum(
            1
            for value in objective_by_tf.values()
            if str((value or {}).get("decision") or "").upper() == "BUY"
        )
        bearish_trend_damage = bool(
            (current_indicator_price is not None and ma20 is not None and current_indicator_price < ma20)
            or price_position < 45
        )
        bullish_trend_recovery = bool(
            (current_indicator_price is not None and ma20 is not None and current_indicator_price > ma20)
            or price_position > 55
        )
        bearish_reversal_confirmed = bool(
            macd_signal == "bearish"
            or bearish_tf_count >= 2
            or (bearish_trend_damage and volume_ratio >= 1.1)
        )
        bullish_reversal_confirmed = bool(
            macd_signal == "bullish"
            or bullish_tf_count >= 2
            or (bullish_trend_recovery and volume_ratio >= 1.1)
        )
        
        if confidence < 60:
            if decision == "HOLD":
                return analysis
            if not direction_supported_by_consensus(analysis, decision):
                logger.warning(f"Decision {decision} with low confidence {confidence}, forcing to HOLD")
                analysis["decision"] = "HOLD"
                analysis["confidence"] = max(confidence, 45)  # 降低置信度
                analysis["decision_guard"] = "low_confidence_without_consensus"
                return analysis
            logger.info(
                f"Keeping low-confidence {decision} because directional consensus confirms it "
                f"(confidence={confidence})"
            )
        
        allow_override = has_major_news or has_macro_event
        
        if decision == "BUY":
            conflicts = []
            
            if rsi_value > 70:
                conflicts.append(f"RSI {rsi_value:.1f} > 70 (超买)")
            
            if macd_signal == "bearish":
                conflicts.append("MACD bearish")
            
            if "strong_downtrend" in ma_trend.lower() or ("downtrend" in ma_trend.lower() and rsi_value > 50):
                conflicts.append(f"MA trend: {ma_trend}")
            
            if conflicts:
                if allow_override:
                    logger.info(f"BUY decision conflicts with indicators but major news/macro event allows override: {', '.join(conflicts)}")
                    analysis["confidence"] = max(confidence - 15, 50)
                    original_summary = analysis.get("summary", "")
                    analysis["summary"] = f"{original_summary} [注意：技术指标显示{', '.join(conflicts)}，但重大事件可能改变趋势]"
                else:
                    logger.warning(f"BUY decision conflicts with indicators and no major event: {', '.join(conflicts)}. Forcing to HOLD")
                    analysis["decision"] = "HOLD"
                    analysis["confidence"] = max(confidence - 20, 40)
                    original_summary = analysis.get("summary", "")
                    analysis["summary"] = f"{original_summary} [注意：技术指标显示{', '.join(conflicts)}，建议观望]"
        
        elif decision == "SELL":
            conflicts = []

            if (
                "uptrend" in trend_low
                and rsi_value >= 60
                and not bearish_reversal_confirmed
                and not allow_override
            ):
                conflicts.append(
                    "Overbought RSI inside an uptrend without bearish reversal confirmation"
                )
                analysis["decision_guard"] = "countertrend_sell_unconfirmed"
            elif rsi_value < 30 and macd_signal == "bullish" and "uptrend" in trend_low:
                conflicts.append(f"Strong bullish signals (RSI {rsi_value:.1f} < 30, MACD bullish, uptrend)")
            elif rsi_value < 30 and "strong_uptrend" in trend_low:
                conflicts.append(f"Very strong uptrend with oversold RSI {rsi_value:.1f}")
            
            if conflicts:
                if allow_override:
                    logger.info(f"SELL decision conflicts with strong bullish indicators but major news/macro event allows override: {', '.join(conflicts)}")
                    analysis["confidence"] = max(confidence - 15, 50)
                    original_summary = analysis.get("summary", "")
                    analysis["summary"] = f"{original_summary} [注意：技术指标显示{', '.join(conflicts)}，但重大事件可能改变趋势]"
                else:
                    logger.warning(f"SELL decision conflicts with very strong bullish indicators: {', '.join(conflicts)}. Forcing to HOLD")
                    analysis["decision"] = "HOLD"
                    analysis["confidence"] = max(confidence - 20, 40)
                    original_summary = analysis.get("summary", "")
                    analysis["summary"] = f"{original_summary} [注意：技术指标显示{', '.join(conflicts)}，建议观望]"

        if (
            decision == "BUY"
            and "downtrend" in trend_low
            and rsi_value <= 40
            and not bullish_reversal_confirmed
            and not allow_override
        ):
            logger.warning("Counter-trend BUY lacks bullish reversal confirmation; forcing HOLD")
            analysis["decision"] = "HOLD"
            analysis["decision_guard"] = "countertrend_buy_unconfirmed"
            analysis["confidence"] = max(confidence - 20, 40)
        
        return analysis
    
    def _detect_market_regime(self, indicators: Dict) -> str:
        """Detect trending vs ranging from MA trend. trending | ranging"""
        ma = indicators.get("moving_averages") or {}
        trend = str(ma.get("trend", "sideways")).lower()
        if "uptrend" in trend or "downtrend" in trend or "strong" in trend:
            return "trending"
        return "ranging"

    def _score_to_decision(self, score: float, *, market: str = "Crypto") -> str:
        """
        根据客观评分转换为决策
        
        优化后的阈值（大幅缩小HOLD区间，使决策更明确）：
        - score >= +20: BUY（利多）
        - score <= -20: SELL（利空）
        - -20 < score < +20: HOLD（中性）
        
        分级决策（用于更细粒度的判断）：
        - score >= +70: 强烈BUY
        - +40 <= score < +70: 明显BUY
        - +20 <= score < +40: BUY
        - +10 < score < +20: 弱利多（倾向于BUY，但可HOLD）
        - -10 <= score <= +10: 中性HOLD（真正的中性区间）
        - -20 < score < -10: 弱利空（倾向于SELL，但可HOLD）
        - -40 < score <= -20: SELL
        - -70 < score <= -40: 明显SELL
        - score <= -70: 强烈SELL
        """
        cfg = self._get_ai_calibration(market=market)
        buy_thr = float(cfg.get("buy_threshold") or 20.0)
        sell_thr = float(cfg.get("sell_threshold") or -20.0)

        if score >= buy_thr:
            return "BUY"
        elif score <= sell_thr:
            return "SELL"
        else:
            return "HOLD"

    def _calculate_overall_score(self, analysis: Dict) -> int:
        """Calculate weighted overall score (legacy method, now uses objective score if available)."""
        if "objective_score" in analysis:
            objective = analysis["objective_score"]
            overall = objective.get("overall_score", 50)
            return max(0, min(100, int(50 + overall * 0.5)))
        
        tech = analysis.get("technical_score", 50)
        fund = analysis.get("fundamental_score", 50)
        sent = analysis.get("sentiment_score", 50)
        
        # Weights: technical 40%, fundamental 35%, sentiment 25%
        overall = tech * 0.40 + fund * 0.35 + sent * 0.25
        
        # Adjust based on decision
        decision = analysis.get("decision", "HOLD")
        confidence = analysis.get("confidence", 50)
        
        if decision == "BUY":
            overall = overall * 0.6 + (50 + confidence * 0.5) * 0.4
        elif decision == "SELL":
            overall = overall * 0.6 + (50 - confidence * 0.5) * 0.4
        
        return max(0, min(100, int(overall)))
    
    def _store_analysis_memory(self, result: Dict, user_id: int = None) -> Optional[int]:
        """Store analysis result for future learning. Returns memory_id."""
        try:
            from app.services.analysis_memory import get_analysis_memory
            memory = get_analysis_memory()
            memory_id = memory.store(result, user_id=user_id)
            
            # Also save to qd_analysis_tasks for admin statistics
            self._save_analysis_task(result, user_id=user_id)
            
            return memory_id
        except Exception as e:
            logger.warning(f"Memory storage failed: {e}")
            return None
    
    def _save_analysis_task(self, result: Dict, user_id: int = None) -> Optional[int]:
        """
        Save analysis record to qd_analysis_tasks table for admin statistics.
        
        Args:
            result: Analysis result dictionary
            user_id: User ID who created this analysis
            
        Returns:
            Task ID or None if failed
        """
        try:
            from app.utils.db import get_db_connection
            
            market = result.get("market", "")
            symbol = result.get("symbol", "")
            model = result.get("model", "")
            # If model is empty, get default model
            if not model:
                from app.services.llm import LLMService
                llm_service = LLMService()
                model = llm_service.get_default_model()
            language = result.get("language", "en-US")
            status = "completed" if not result.get("error") else "failed"
            result_json = json.dumps(result, ensure_ascii=False)
            error_message = result.get("error", "")
            
            if not market or not symbol:
                logger.warning(f"Cannot save analysis task: missing market or symbol")
                return None
            
            with get_db_connection() as db:
                cur = db.cursor()
                # PostgreSQL: Use RETURNING to get the inserted ID
                cur.execute(
                    """
                    INSERT INTO qd_analysis_tasks
                    (user_id, market, symbol, model, language, status, result_json, error_message, created_at, completed_at)
                    VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
                    RETURNING id
                    """,
                    (
                        int(user_id) if user_id else 1,  # Default to user 1 if not provided
                        str(market),
                        str(symbol),
                        str(model) if model else '',
                        str(language),
                        str(status),
                        str(result_json),
                        str(error_message) if error_message else ''
                    )
                )
                row = cur.fetchone()
                task_id = row['id'] if row else None
                db.commit()
                cur.close()
                
                if task_id:
                    logger.debug(f"Saved analysis task {task_id} for user {user_id}: {market}:{symbol}")
                return task_id
                
        except Exception as e:
            logger.warning(f"Failed to save analysis task: {e}")
            return None
    
# Singleton instance
_fast_analysis_service = None

def get_fast_analysis_service() -> FastAnalysisService:
    """Get singleton FastAnalysisService instance."""
    global _fast_analysis_service
    if _fast_analysis_service is None:
        _fast_analysis_service = FastAnalysisService()
    return _fast_analysis_service


def fast_analyze(market: str, symbol: str, language: str = 'en-US', 
                 model: str = None, timeframe: str = "1D") -> Dict[str, Any]:
    """Convenience function for fast analysis."""
    service = get_fast_analysis_service()
    return service.analyze(market, symbol, language, model, timeframe)
