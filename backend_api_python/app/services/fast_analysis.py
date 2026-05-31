"""
Fast Analysis Service 3.0
系统性重构版本 - 使用统一的数据采集器

核心改进：
1. 数据源统一 - 使用 MarketDataCollector，与K线模块、自选列表完全一致
2. 宏观数据 - 新增美元指数、VIX、利率等宏观经济指标
3. 多维新闻 - 使用结构化API，无需深度阅读
4. 单次LLM调用 - 强约束prompt，输出结构化分析
"""
import json
import os
import re
import time
from typing import Dict, Any, Optional, List, Tuple
from decimal import Decimal, ROUND_HALF_UP

from app.utils.logger import get_logger
from app.services.llm import LLMService
from app.services.market_data_collector import get_market_data_collector

logger = get_logger(__name__)


def _safe_float_price(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Coerce LLM/string prices to float; invalid -> default."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN
            return default
        return float(value)
    try:
        s = str(value).strip().replace(",", "")
        if not s:
            return default
        return float(s)
    except (TypeError, ValueError):
        return default


def _build_trend_outlook_summary(trend_outlook: Dict[str, Any], language: str) -> str:
    """Human-readable multi-horizon outlook for API / legacy clients."""
    if not trend_outlook:
        return ""
    is_zh = str(language or "").lower().startswith("zh")

    def _lbl(trend: str) -> str:
        t = str(trend or "HOLD").upper()
        if is_zh:
            return {"BUY": "看多", "SELL": "看空", "HOLD": "震荡/中性"}.get(t, "震荡/中性")
        return {"BUY": "bullish", "SELL": "bearish", "HOLD": "neutral / range"}.get(t, "neutral / range")

    n24 = trend_outlook.get("next_24h") or {}
    d3 = trend_outlook.get("next_3d") or {}
    w1 = trend_outlook.get("next_1w") or {}
    m1 = trend_outlook.get("next_1m") or {}

    if is_zh:
        parts = [
            f"约24小时：{_lbl(n24.get('trend'))}（强度 {n24.get('strength', 'neutral')}）",
            f"约3天：{_lbl(d3.get('trend'))}（强度 {d3.get('strength', 'neutral')}）",
            f"约1周：{_lbl(w1.get('trend'))}（强度 {w1.get('strength', 'neutral')}）",
            f"约1月：{_lbl(m1.get('trend'))}（强度 {m1.get('strength', 'neutral')}）",
        ]
        return "；".join(parts)
    parts = [
        f"~24h: {_lbl(n24.get('trend'))} ({n24.get('strength', 'neutral')})",
        f"~3d: {_lbl(d3.get('trend'))} ({d3.get('strength', 'neutral')})",
        f"~1w: {_lbl(w1.get('trend'))} ({w1.get('strength', 'neutral')})",
        f"~1m: {_lbl(m1.get('trend'))} ({m1.get('strength', 'neutral')})",
    ]
    return " | ".join(parts)


# -----------------------------------------------------------------------------
# Geopolitical / major-conflict detection (word boundaries + tiers)
# Avoid false positives: "war" in "toward/award", "tension" in "extension",
# "us" in "focus/status", bare country names without conflict context, etc.
# -----------------------------------------------------------------------------
_GEO_SEVERE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(?:war|wars|warfare|wartime)\b", re.I),
    re.compile(r"\b(?:invasion|invaded|invading|invade)\b", re.I),
    re.compile(r"\b(?:airstrike|air\s*strikes?|missile\s+strike|drone\s+strike)\b", re.I),
    re.compile(r"\b(?:military\s+attack|armed\s+attack|troops?\s+(?:fire|attack|invade))\b", re.I),
    re.compile(r"\b(?:declare[sd]?\s+war|state\s+of\s+war|act\s+of\s+war)\b", re.I),
    re.compile(r"\b(?:martial\s+law|military\s+coup|coup\s+d['\u2019]?etat)\b", re.I),
    re.compile(r"\b(?:terror(?:ist)?\s+attack|mass\s+shooting\s+at)\b", re.I),
]
_GEO_MODERATE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bgeopolitical\b", re.I),
    re.compile(r"\b(?:armed|military)\s+conflict\b", re.I),
    re.compile(r"\b(?:international\s+)?sanctions?\s+(?:on|against|targeting|hit)\b", re.I),
    re.compile(r"\b(?:naval\s+blockade|border\s+clash|ceasefire\s+(?:broken|violated))\b", re.I),
    re.compile(r"\b(?:evacuat\w+\s+(?:the\s+)?embassy|embassy\s+evacuation)\b", re.I),
    re.compile(r"\b(?:nuclear\s+(?:threat|strike|weapon)|nuclear\s+war)\b", re.I),
]
# "Crisis" / "tension" only in clearly geopolitical phrases (not substring of "extension")
_GEO_CONTEXT_MODERATE: List[re.Pattern] = [
    re.compile(r"\b(?:geopolitical|diplomatic|border)\s+(?:crisis|tension|standoff)\b", re.I),
    re.compile(r"\b(?:tensions?\s+(?:rise|escalat|flare|mount)\s+(?:with|between))\b", re.I),
    re.compile(r"\b(?:middle\s+east|south\s+china\s+sea|taiwan\s+strait)\s+(?:crisis|tension|conflict)\b", re.I),
]
_GEO_ZH_SEVERE = (
    "宣战", "战争爆发", "全面战争", "武装冲突", "军事打击", "军事入侵", "空袭", "导弹袭击",
    "开战", "交火", "战火",
)
_GEO_ZH_MODERATE = (
    "地缘政治危机", "国际制裁升级", "断交", "撤侨", "军事对峙", "地区冲突升级",
)

# Optional: country/region + conflict verb (single pattern, avoids "NYSE" noise)
_GEO_REGION_CONFLICT: List[re.Pattern] = [
    re.compile(
        r"\b(?:russia|ukraine|iran|israel|gaza|hamas|taiwan|north\s+korea|dprk|"
        r"syria|yemen|lebanon|nato)\b.{0,40}\b(?:invade|attack|strike|war|conflict|sanction)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:invade|attack|strike|war|conflict|sanction)\b.{0,40}\b(?:russia|ukraine|iran|israel|"
        r"gaza|hamas|taiwan|north\s+korea|dprk|syria|nato)\b",
        re.I,
    ),
]

_GEO_MAJOR_NEWS_SEVERE = [
    re.compile(r"\b(?:war|wars|warfare)\b", re.I),
    re.compile(r"\b(?:invasion|invaded|military\s+attack|airstrike)\b", re.I),
    re.compile(r"\b(?:armed\s+conflict|military\s+conflict)\b", re.I),
]


def _geopolitical_match_level(combined_text: str) -> Tuple[str, Optional[str]]:
    """
    Returns (level, reason_tag) where level is 'none'|'severe'|'moderate'.
    combined_text: title + summary (original case OK; English patterns use lower via regex I flag).
    """
    if not combined_text or len(combined_text.strip()) < 4:
        return "none", None
    low = combined_text.lower()
    for pat in _GEO_SEVERE_PATTERNS:
        if pat.search(low):
            return "severe", pat.pattern[:48]
    for z in _GEO_ZH_SEVERE:
        if z in combined_text:
            return "severe", z
    for pat in _GEO_REGION_CONFLICT:
        if pat.search(low):
            return "severe", "region+conflict"
    for pat in _GEO_MODERATE_PATTERNS:
        if pat.search(low):
            return "moderate", pat.pattern[:48]
    for pat in _GEO_CONTEXT_MODERATE:
        if pat.search(low):
            return "moderate", pat.pattern[:48]
    for z in _GEO_ZH_MODERATE:
        if z in combined_text:
            return "moderate", z
    return "none", None


def _geopolitical_sentiment_penalty_delta(level: str) -> int:
    if level == "severe":
        return -42
    if level == "moderate":
        return -18
    return 0


def _is_major_geopolitical_news_text(combined_text: str) -> bool:
    """Stricter than sentiment: only clear conflict / war signals for _has_major_news."""
    if not combined_text:
        return False
    low = combined_text.lower()
    for pat in _GEO_MAJOR_NEWS_SEVERE:
        if pat.search(low):
            return True
    for z in _GEO_ZH_SEVERE:
        if z in combined_text:
            return True
    if any(p.search(low) for p in _GEO_REGION_CONFLICT):
        return True
    return False


class FastAnalysisService:
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
    ) -> Dict[str, Any]:
        """
        使用统一的数据采集器收集市场数据
        
        数据层次：
        1. 核心数据: 价格、K线、技术指标
        2. 基本面: 公司信息、财务数据
        3. 宏观数据: DXY、VIX、TNX、黄金等
        4. 情绪数据: 新闻、市场情绪
        """
        return self.data_collector.collect_all(
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            include_macro=include_macro,
            include_news=include_news,
            timeout=timeout,  # 增加超时时间，确保数据收集完成
        )
    
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
            
            if macd > macd_signal_line and macd_hist > 0:
                macd_signal = "bullish"
                macd_trend = "golden_cross" if macd_hist > 0 and len(kline_data) > 1 else "bullish"
            elif macd < macd_signal_line and macd_hist < 0:
                macd_signal = "bearish"
                macd_trend = "death_cross" if macd_hist < 0 and len(kline_data) > 1 else "bearish"
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
    
    def _get_memory_context(self, market: str, symbol: str, current_indicators: Dict) -> str:
        """
        Retrieve relevant historical analysis for similar market conditions.
        """
        try:
            from app.services.analysis_memory import get_analysis_memory
            memory = get_analysis_memory()
            
            # Get similar patterns
            patterns = memory.get_similar_patterns(market, symbol, current_indicators, limit=3)
            
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
    
    def _build_analysis_prompt(self, data: Dict[str, Any], language: str) -> tuple:
        """
        Build the single, comprehensive analysis prompt.
        Key: Strong constraints to prevent absurd recommendations.
        """
        price_data = data.get("price") or {}
        current_price = price_data.get("price", 0) if price_data else 0
        change_24h = price_data.get("changePercent", 0) if price_data else 0
        
        # Ensure all data fields have safe defaults (may be None from failed fetches)
        indicators = data.get("indicators") or {}
        fundamental = data.get("fundamental") or {}
        company = data.get("company") or {}
        crypto_factors = data.get("crypto_factors") or {}
        is_crypto = str(data.get("market") or "").strip().lower() == "crypto"
        news_summary = self._format_news_summary(data.get("news") or [])
        
        # Language instruction - MUST be enforced strictly
        lang_map = {
            'zh-CN': '⚠️ 重要：你必须用简体中文回答所有内容，包括summary、key_reasons、risks等所有文本字段。不要使用英文。',
            'zh-TW': '⚠️ 重要：你必須用繁體中文回答所有內容，包括summary、key_reasons、risks等所有文本字段。不要使用英文。',
            'en-US': '⚠️ IMPORTANT: You MUST answer ALL content in English, including summary, key_reasons, risks, and all text fields. Do NOT use Chinese.',
            'ja-JP': '⚠️ 重要：すべての内容を日本語で回答してください。summary、key_reasons、risksなど、すべてのテキストフィールドを日本語で記述してください。',
        }
        lang_instruction = lang_map.get(language, '⚠️ IMPORTANT: Answer ALL content in English.')
        
        # Get pre-calculated trading levels from technical analysis
        levels = indicators.get("levels", {})
        trading_levels = indicators.get("trading_levels", {})
        volatility = indicators.get("volatility", {})
        
        support = levels.get("support", current_price * 0.95)
        resistance = levels.get("resistance", current_price * 1.05)
        pivot = levels.get("pivot", current_price)
        
        # Use ATR-based suggestions if available, otherwise use percentage
        atr = volatility.get("atr", current_price * 0.02)
        suggested_stop_loss = trading_levels.get("suggested_stop_loss", current_price - 2 * atr)
        suggested_take_profit = trading_levels.get("suggested_take_profit", current_price + 3 * atr)
        risk_reward_ratio = trading_levels.get("risk_reward_ratio", 1.5)
        
        # Price bounds (still enforce max 10% deviation)
        if current_price > 0:
            price_lower_bound = round(max(suggested_stop_loss, current_price * 0.90), 6)
            price_upper_bound = round(min(suggested_take_profit, current_price * 1.10), 6)
            entry_range_low = round(current_price * 0.98, 6)
            entry_range_high = round(current_price * 1.02, 6)
        else:
            price_lower_bound = price_upper_bound = entry_range_low = entry_range_high = 0
        
        # Get technical indicator values for decision constraints
        rsi_value = indicators.get("rsi", {}).get("value", 50)
        macd_signal = indicators.get("macd", {}).get("signal", "neutral")
        ma_trend = indicators.get("moving_averages", {}).get("trend", "sideways")
        
        # Build decision guidance based on technical indicators
        decision_guidance = self._build_decision_guidance(rsi_value, macd_signal, ma_trend, change_24h)
        crypto_factor_block = self._format_crypto_factor_prompt(crypto_factors, language)
        crypto_system_rules = ""
        crypto_user_block = ""
        if is_crypto:
            crypto_system_rules = """
8. **Crypto Market Structure Override**:
   - For Crypto, DO NOT rely on stock-style valuation logic as your core thesis.
   - Prioritize derivatives positioning, funding rate, open interest, long/short ratio, exchange netflow, and stablecoin netflow.
   - Positive funding + rising OI can confirm bullish momentum, but extreme values may also indicate crowded longs and squeeze risk.
   - Exchange net outflow is generally constructive; large net inflow may imply sell pressure or risk-off hedging.
   - Stablecoin net inflow can imply fresh buying power entering the market.
   - If derivatives are crowded or squeeze risk is high, explicitly mention this in summary, reasons, and risks.
"""
            crypto_user_block = f"""
🪙 CRYPTO MARKET STRUCTURE:
{crypto_factor_block}
"""
        
        system_prompt = f"""You are QuantDinger's Senior Financial Analyst with 20+ years of experience. 
You are CONSERVATIVE and OBJECTIVE. Your analysis must be based on DATA, not speculation.

{lang_instruction}

🎯 CRITICAL DECISION RULES (MUST FOLLOW):
1. **Market Context**: This market supports BOTH long (BUY) and short (SELL) positions. SELL signals are VALID trading opportunities, not just risk warnings.
2. **Multi-Factor Analysis** (IMPORTANT - Consider ALL factors):
   - **Technical Indicators** (RSI, MACD, MA trends): Provide baseline direction
   - **Macro Environment** (DXY, VIX, interest rates, geopolitical events): Can override technical signals
   - **Breaking News & Events**: Major news can cause sudden reversals - pay attention!
   - **Fundamental Data**: Valuation, growth, financial health matter for medium/long-term
   - **Market Sentiment**: News sentiment, fear/greed index, market mood
3. **Decision Priority** (When factors conflict):
   - **Major macro events** (war, policy changes, major economic data) > Technical indicators
   - **Breaking news** (regulatory changes, major partnerships, scandals) > Short-term technical
   - **Technical indicators** > General news sentiment (when no major events)
   - **Fundamental data** > Short-term price movements (for long-term decisions)
4. **Balance Your Decisions** (IMPORTANT - Give SELL signals when appropriate):
   - BUY: When technical indicators show oversold (RSI < 40), bullish MACD, uptrend, OR strong macro/fundamental catalyst
   - SELL: When technical indicators show overbought (RSI > 60), bearish MACD, downtrend, OR major negative macro/news event
   - HOLD: Only when signals are truly mixed or unclear - DO NOT default to HOLD just because you're uncertain
   - **Remember**: SELL is a valid trading signal for short positions, not just a warning to avoid buying
5. **Confidence Thresholds**:
   - BUY requires confidence >= 60 AND (technical support OR macro/fundamental catalyst)
   - SELL requires confidence >= 60 AND (technical support OR negative event) - SELL signals are encouraged when indicators suggest downside
   - HOLD only when confidence < 60 AND signals are truly unclear
6. **Identify Trading Opportunities**:
   - When RSI > 60, MACD bearish, downtrend: Consider SELL (short position opportunity)
   - When RSI < 40, MACD bullish, uptrend: Consider BUY (long position opportunity)
   - Do NOT default to HOLD when clear technical signals exist
7. **Consider Macro Impact**: 
   - Strong USD (DXY ↑) usually negative for crypto/commodities → Consider SELL
   - High VIX (>30) indicates fear → Consider SELL or HOLD, avoid BUY
   - Rising interest rates usually negative for growth assets → Consider SELL
   - Geopolitical tensions can cause sudden volatility → Consider SELL if risk-off sentiment
{crypto_system_rules}

{decision_guidance}

📐 TECHNICAL LEVELS (Pre-calculated from chart data):
- Support: ${support} | Resistance: ${resistance} | Pivot: ${pivot}
- ATR (14-day): ${atr:.4f} ({volatility.get('pct', 0)}% volatility)
- Suggested Stop Loss: ${suggested_stop_loss:.4f} (based on 2x ATR below support)
- Suggested Take Profit: ${suggested_take_profit:.4f} (based on 3x ATR above resistance)
- Risk/Reward Ratio: {risk_reward_ratio}

⚠️ CRITICAL PRICE RULES:
1. Current price: ${current_price}
2. If decision=BUY: stop_loss should be below current price, take_profit above current price.
3. If decision=SELL (short): stop_loss MUST be above current price; take_profit MUST be below current price.
4. BUY stop_loss reference: near ${suggested_stop_loss:.4f} (range: ${price_lower_bound:.4f} ~ ${current_price})
5. BUY take_profit reference: near ${suggested_take_profit:.4f} (range: ${current_price} ~ ${price_upper_bound:.4f})
6. Entry price: ${entry_range_low:.4f} ~ ${entry_range_high:.4f}
7. These levels are based on ATR and support/resistance analysis - use them as reference!

📊 YOUR ANALYSIS MUST INCLUDE (ALL factors are important):
1. **Technical Analysis**: Objectively interpret RSI, MACD, MA, support/resistance. Be honest about conflicting signals.
2. **Macro Environment Analysis**: 
   - Analyze DXY, VIX, interest rates impact on the asset
   - Consider geopolitical events and their potential impact
   - Evaluate how macro trends affect this specific market/symbol
3. **News & Event Analysis**: 
   - **CRITICAL**: Pay special attention to GEOPOLITICAL EVENTS (wars, conflicts, military actions, sanctions)
   - These events can cause sudden and severe market movements, especially for crypto and global markets
   - Identify BREAKING NEWS or major events that could cause sudden moves
   - Assess news sentiment and its credibility
   - Consider regulatory changes, partnerships, scandals, geopolitical tensions, etc.
   - **DO NOT ignore major geopolitical news** (e.g., US-Iran conflict, Russia-Ukraine war) even if technical indicators look good
   - Global events like wars can override all technical analysis - treat them as HIGHEST PRIORITY
4. **Prediction Market Analysis**:
   - Review related prediction market events and their current probabilities
   - Prediction markets reflect collective market wisdom and can indicate future price movements
   - If prediction markets show high probability for bullish events (e.g., "BTC reaches $100k"), consider this as a positive signal
   - If prediction markets show high probability for bearish events, consider this as a risk factor
   - Use prediction market probabilities as a sentiment indicator alongside technical analysis
5. **Fundamental Analysis**: For Crypto, focus on market structure / flow / derivatives factors instead of stock-style valuation. For equities, evaluate valuation, growth, competitive position if data available.
6. **Risk Assessment**: 
   - Explain why the stop loss level is appropriate
   - List ALL significant risks (technical, macro, news, fundamental)
   - Consider tail risks from unexpected events
7. **Clear Recommendation**: BUY/SELL/HOLD with entry, stop loss (near suggested), take profit (near suggested)
   - **BUY**: For long positions when indicators suggest upside
   - **SELL**: For short positions when indicators suggest downside - this is a VALID trading opportunity
   - **HOLD**: Only when signals are truly unclear - DO NOT default to HOLD just to be safe
   - Your decision should reflect the WEIGHTED importance of ALL factors
   - If macro/news factors strongly contradict technical, explain why you prioritize one over the other
8. **Trading Opportunity Recognition**:
   - When you see RSI > 60, bearish MACD, downtrend → Give SELL signal (short opportunity)
   - When you see RSI < 40, bullish MACD, uptrend → Give BUY signal (long opportunity)
   - Only choose HOLD when signals are genuinely mixed or unclear

Output ONLY valid JSON (do NOT include word counts or format hints in your actual response):
{{
  "decision": "BUY" | "SELL" | "HOLD",
  "confidence": 0-100,
  "summary": "Executive summary in 2-3 sentences - be honest about uncertainty if present",
  "analysis": {{
    "technical": "Your detailed technical analysis here - interpret RSI, MACD, MA, support/resistance objectively",
    "fundamental": "Your fundamental assessment here - valuation, growth, competitive position. If data is limited, state that clearly.",
    "sentiment": "Your market sentiment analysis here - news impact, macro factors, mood. Don't overreact."
  }},
  "entry_price": number,
  "stop_loss": number,
  "take_profit": number,
  "position_size_pct": 1-100,
  "timeframe": "short" | "medium" | "long",
  "key_reasons": ["First key reason for this decision", "Second key reason", "Third key reason"],
  "risks": ["Primary risk with potential impact", "Secondary risk"],
  "technical_score": 0-100,
  "fundamental_score": 0-100,
  "sentiment_score": 0-100
}}

⚠️ IMPORTANT: 
- The analysis fields should contain your ACTUAL analysis text, NOT the format description above.
- Be HONEST and CONSERVATIVE. If you're not confident, choose HOLD with lower confidence.
- Do NOT make up facts or exaggerate. Base everything on the provided data.

📊 OBJECTIVE SCORING SYSTEM (Reference):
The system will calculate an objective score based on technical indicators, fundamentals, sentiment (including geopolitical events), and macro factors.
- Score >= +20: Bullish signal → BUY recommended
- Score <= -20: Bearish signal → SELL recommended  
- Score between -20 and +20: Neutral → HOLD recommended (narrow range)
- Score >= +70: Strong bullish → Strong BUY signal
- Score <= -70: Strong bearish → Strong SELL signal
- Geopolitical events (wars, conflicts) are heavily weighted in sentiment score and can cause severe negative scores
- Macro factors (VIX, DXY, interest rates) are also heavily weighted
Your decision should align with this objective score when it's significant (>=20 or <=-20).
When the score is neutral (-20 to +20), you can use your judgment, but still consider giving BUY/SELL if technical indicators are clear."""

        # Format indicator data for prompt (ensure safe defaults)
        rsi_data = indicators.get("rsi") or {}
        macd_data = indicators.get("macd") or {}
        ma_data = indicators.get("moving_averages") or {}
        vol_data = indicators.get("volatility") or {}
        levels = indicators.get("levels") or {}
        
        # Format macro data
        macro = data.get("macro") or {}
        macro_summary = self._format_macro_summary(macro, data.get("market", ""))
        
        user_prompt = f"""Analyze {data['symbol']} in {data['market']} market.

📊 REAL-TIME DATA:
- Current Price: ${current_price}
- 24h Change: {change_24h}%
- Support: ${support}
- Resistance: ${resistance}

📈 TECHNICAL INDICATORS:
- RSI(14): {rsi_data.get('value', 'N/A')} ({rsi_data.get('signal', 'N/A')})
- MACD: {macd_data.get('signal', 'N/A')} ({macd_data.get('trend', 'N/A')})
- MA Trend: {ma_data.get('trend', 'N/A')}
- Volatility: {vol_data.get('level', 'N/A')} ({vol_data.get('pct', 0)}%)
- Trend: {indicators.get('trend', 'N/A')}
- Price Position (20d): {indicators.get('price_position', 'N/A')}%
{crypto_user_block}

🌐 MACRO ENVIRONMENT:
{macro_summary}

📰 MARKET NEWS ({len(data.get('news') or [])} items):
{news_summary}

💼 FUNDAMENTALS / MARKET STRUCTURE:
- Company: {company.get('name', data['symbol'])}
- Industry: {company.get('industry', 'N/A')}
- P/E Ratio: {fundamental.get('pe_ratio', 'N/A')}
- P/B Ratio: {fundamental.get('pb_ratio', 'N/A')}
- Market Cap: {fundamental.get('market_cap', 'N/A')}
- 52W High/Low: {fundamental.get('52w_high', 'N/A')} / {fundamental.get('52w_low', 'N/A')}
- ROE: {fundamental.get('roe', 'N/A')}
- Revenue Growth: {fundamental.get('revenue_growth', 'N/A')}
- Profit Margin: {fundamental.get('profit_margin', 'N/A')}
- Debt to Equity: {fundamental.get('debt_to_equity', 'N/A')}
- Current Ratio: {fundamental.get('current_ratio', 'N/A')}
- Free Cash Flow: {fundamental.get('free_cash_flow', 'N/A')}

📊 FINANCIAL STATEMENTS (Latest Quarter):
{self._format_financial_statements(fundamental.get('financial_statements', {}))}

📈 EARNINGS DATA:
{self._format_earnings_data(fundamental.get('earnings', {}))}

📚 HISTORICAL PATTERNS (similar conditions in the past):
{self._get_memory_context(data.get('market', ''), data.get('symbol', ''), indicators)}

IMPORTANT: 
1. **CRITICAL**: Check for GEOPOLITICAL EVENTS (wars, conflicts, military actions) in the news section. These events have HIGHEST PRIORITY and can override all technical indicators.
2. Consider the macro environment (especially DXY, VIX, rates, geopolitical events) when making your recommendation.
3. Pay attention to BREAKING NEWS and international events that could cause sudden market moves. Geopolitical tensions (e.g., US-Iran conflict) can cause severe market volatility.
4. For Crypto, explicitly explain whether derivatives + capital flow data confirm or contradict price action. For US stocks, analyze financial statements and earnings trends to assess company health.
5. If you see news about wars, conflicts, or major geopolitical events, you MUST mention them in your analysis and adjust your recommendation accordingly.
6. Provide your analysis now. Remember: all prices must be within 10% of ${current_price}."""

        return system_prompt, user_prompt
    
    def _format_financial_statements(self, statements: Dict[str, Any]) -> str:
        """格式化财务报表数据用于提示词"""
        if not statements:
            return "财务报表数据暂不可用"
        
        lines = []
        
        # 资产负债表
        if 'balance_sheet' in statements:
            bs = statements['balance_sheet']
            lines.append("资产负债表 (Balance Sheet):")
            if bs.get('total_assets'):
                lines.append(f"  - 总资产: ${bs['total_assets']:,.0f}")
            if bs.get('total_liabilities'):
                lines.append(f"  - 总负债: ${bs['total_liabilities']:,.0f}")
            if bs.get('total_equity'):
                lines.append(f"  - 股东权益: ${bs['total_equity']:,.0f}")
            if bs.get('cash'):
                lines.append(f"  - 现金: ${bs['cash']:,.0f}")
            if bs.get('debt'):
                lines.append(f"  - 总债务: ${bs['debt']:,.0f}")
            if bs.get('current_assets') and bs.get('current_liabilities'):
                current_ratio = bs['current_assets'] / bs['current_liabilities'] if bs['current_liabilities'] > 0 else 0
                lines.append(f"  - 流动比率: {current_ratio:.2f}")
        
        # 利润表
        if 'income_statement' in statements:
            is_stmt = statements['income_statement']
            lines.append("利润表 (Income Statement):")
            if is_stmt.get('total_revenue'):
                lines.append(f"  - 总收入: ${is_stmt['total_revenue']:,.0f}")
            if is_stmt.get('gross_profit'):
                lines.append(f"  - 毛利润: ${is_stmt['gross_profit']:,.0f}")
            if is_stmt.get('operating_income'):
                lines.append(f"  - 营业利润: ${is_stmt['operating_income']:,.0f}")
            if is_stmt.get('net_income'):
                lines.append(f"  - 净利润: ${is_stmt['net_income']:,.0f}")
            if is_stmt.get('eps'):
                lines.append(f"  - 每股收益: ${is_stmt['eps']:.2f}")
        
        # 现金流量表
        if 'cash_flow' in statements:
            cf = statements['cash_flow']
            lines.append("现金流量表 (Cash Flow):")
            if cf.get('operating_cash_flow'):
                lines.append(f"  - 经营现金流: ${cf['operating_cash_flow']:,.0f}")
            if cf.get('free_cash_flow'):
                lines.append(f"  - 自由现金流: ${cf['free_cash_flow']:,.0f}")
        
        return "\n".join(lines) if lines else "财务报表数据暂不可用"
    
    def _format_earnings_data(self, earnings: Dict[str, Any]) -> str:
        """格式化盈利数据用于提示词"""
        if not earnings:
            return "盈利数据暂不可用"
        
        lines = []
        
        # 历史盈利
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
        
        # 未来盈利
        if 'upcoming' in earnings:
            upcoming = earnings['upcoming']
            if upcoming.get('next_earnings_date'):
                lines.append(f"下次盈利报告: {upcoming['next_earnings_date']}")
                if upcoming.get('eps_estimate'):
                    lines.append(f"  - EPS预期: ${upcoming['eps_estimate']:.2f}")
                if upcoming.get('revenue_estimate'):
                    lines.append(f"  - 收入预期: ${upcoming['revenue_estimate']:,.0f}")
        
        # 季度盈利
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
        
        # 美元指数
        if 'DXY' in macro:
            dxy = macro['DXY']
            direction = "↑" if dxy.get('change', 0) > 0 else "↓"
            lines.append(f"- {dxy.get('name', 'USD Index')}: {dxy.get('price', 'N/A')} ({direction}{abs(dxy.get('changePercent', 0)):.2f}%)")
            # 美元强弱对不同资产的影响
            if market == 'Crypto':
                impact = "利空加密货币" if dxy.get('change', 0) > 0 else "利好加密货币"
                lines.append(f"  ⚠️ 美元{direction} {impact}")
            elif market == 'Forex':
                lines.append(f"  ⚠️ 美元{direction} 直接影响外汇走势")
        
        # VIX恐慌指数
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
        
        # 美债收益率
        if 'TNX' in macro:
            tnx = macro['TNX']
            direction = "↑" if tnx.get('change', 0) > 0 else "↓"
            lines.append(f"- {tnx.get('name', '10Y Treasury')}: {tnx.get('price', 'N/A'):.3f}% ({direction})")
            if tnx.get('price', 0) > 4.5:
                lines.append("  ⚠️ 高利率环境，对估值不利")
        
        # 黄金
        if 'GOLD' in macro:
            gold = macro['GOLD']
            direction = "↑" if gold.get('change', 0) > 0 else "↓"
            lines.append(f"- {gold.get('name', 'Gold')}: ${gold.get('price', 'N/A'):.2f} ({direction}{abs(gold.get('changePercent', 0)):.2f}%)")
        
        # 标普500
        if 'SPY' in macro:
            spy = macro['SPY']
            direction = "↑" if spy.get('change', 0) > 0 else "↓"
            lines.append(f"- {spy.get('name', 'S&P 500')}: ${spy.get('price', 'N/A'):.2f} ({direction}{abs(spy.get('changePercent', 0)):.2f}%)")
        
        # 比特币 (作为风险指标)
        if 'BTC' in macro and market != 'Crypto':
            btc = macro['BTC']
            direction = "↑" if btc.get('change', 0) > 0 else "↓"
            lines.append(f"- {btc.get('name', 'BTC')}: ${btc.get('price', 'N/A'):,.0f} ({direction}{abs(btc.get('changePercent', 0)):.2f}%) [风险偏好指标]")
        
        return "\n".join(lines) if lines else "宏观数据暂不可用"
    
    # ==================== Main Analysis ====================
    
    def analyze(self, market: str, symbol: str, language: str = 'en-US', 
                model: str = None, timeframe: str = "1D", user_id: int = None) -> Dict[str, Any]:
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
            "analysis_time_ms": 0,
            "error": None,
        }
        
        try:
            # Phase 1: Data collection (multi-timeframe for consensus)
            logger.info(f"Fast analysis starting: {market}:{symbol}")

            # Consensus timeframes:
            # - 默认：用用户传入的 timeframe 作为主周期，再加一个上层周期（1D/4H）提升稳定性
            # - 也允许通过 env 覆盖（逗号分隔），例如 AI_ANALYSIS_CONSENSUS_TIMEFRAMES=1D,4H
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
                        timeout=25,
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

                # Weight by strength so strong cycles dominate
                w = 1.0 + min(1.5, abs_score / 100.0)
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
                        timeout=25,
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
                        timeout=18,
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
            
            # 优先从 price 数据获取
            if data.get("price") and data["price"].get("price"):
                current_price = data["price"]["price"]
            
            # Fallback: 从 indicators 获取 (如果 K 线成功计算了)
            if not current_price and data.get("indicators"):
                current_price = data["indicators"].get("current_price")
                if current_price:
                    logger.info(f"Using price from indicators: ${current_price}")
                    # 构建简化的 price 数据
                    data["price"] = {
                        "price": current_price,
                        "change": 0,
                        "changePercent": 0,
                        "source": "indicators_fallback"
                    }
            
            # Fallback: 从 kline 最后一根获取
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
            system_prompt, user_prompt = self._build_analysis_prompt(data, language)

            default_struct = {
                "decision": "HOLD",
                "confidence": 50,
                "summary": "Analysis failed",
                "entry_price": current_price,
                "stop_loss": current_price * 0.95,
                "take_profit": current_price * 1.05,
                "position_size_pct": 10,
                "timeframe": "medium",
                "key_reasons": ["Unable to analyze"],
                "risks": ["Analysis error"],
                "technical_score": 50,
                "fundamental_score": 50,
                "sentiment_score": 50,
            }

            # Phase 3: LLM call(s) - single or ensemble voting
            logger.info("Calling LLM for analysis...")
            llm_start = time.time()
            ensemble_models = []
            if os.getenv("ENABLE_AI_ENSEMBLE", "false").lower() == "true":
                env_models = (os.getenv("AI_ENSEMBLE_MODELS") or "").strip()
                if env_models:
                    ensemble_models = [m.strip() for m in env_models.split(",") if m.strip()]

            if len(ensemble_models) >= 2:
                analyses_list = []
                for em in ensemble_models[:3]:
                    a = self.llm_service.safe_call_llm(
                        system_prompt, user_prompt, default_structure=default_struct, model=em
                    )
                    analyses_list.append(a)
                decisions = [str(a.get("decision", "HOLD") or "HOLD").upper() for a in analyses_list]
                from collections import Counter
                vote = Counter(decisions).most_common(1)[0][0]
                idx = decisions.index(vote)
                analysis = analyses_list[idx].copy()
                analysis["decision"] = vote
                analysis["_ensemble_vote"] = dict(Counter(decisions))
                analysis["_ensemble_models"] = ensemble_models[:3]
            else:
                analysis = self.llm_service.safe_call_llm(
                    system_prompt, user_prompt, default_structure=default_struct, model=model
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
            trend_outlook_summary = _build_trend_outlook_summary(trend_outlook, language)

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
            if regime == "ranging":
                min_abs_override *= 1.2

            if consensus_abs >= min_abs_override:
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
                if is_zh:
                    level = "强烈" if consensus_abs >= 70 else "明显" if consensus_abs >= 40 else "轻微"
                    bias = "利多" if consensus_score > 0 else "利空"
                    consensus_note = (
                        f"[多周期客观共识：综合评分{consensus_score:.1f}分（{level}{bias}），建议{final_decision}]"
                    )
                else:
                    level = "strong" if consensus_abs >= 70 else "moderate" if consensus_abs >= 40 else "mild"
                    bias = "bullish" if consensus_score > 0 else "bearish"
                    consensus_note = (
                        f"[Multi-timeframe objective consensus: score {consensus_score:.1f} "
                        f"({level} {bias}), suggested decision {final_decision}]"
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
                    ps_scaled *= 0.25
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
                        raw_conf, market=market, symbol=symbol
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
                    "position_size_pct": analysis.get("position_size_pct", 10),
                    "timeframe": analysis.get("timeframe", "medium"),
                    # camelCase + 语义别名：供私有前端/旧版组件绑定（勿用 indicators.trading_levels 充当计划）
                    "entryPrice": analysis.get("entry_price"),
                    "stopLoss": analysis.get("stop_loss"),
                    "takeProfit": analysis.get("take_profit"),
                    "positionSizePct": analysis.get("position_size_pct", 10),
                    "decision": str(analysis.get("decision", "HOLD") or "HOLD").upper(),
                    # 与 stop_loss / take_profit 数值相同；命名强调「亏损离场 / 盈利目标」避免与多单参考线混淆
                    "loss_exit_price": analysis.get("stop_loss"),
                    "profit_target_price": analysis.get("take_profit"),
                },
                "reasons": analysis.get("key_reasons", []),
                "risks": analysis.get("risks", []),
                "scores": {
                    "technical": analysis.get("technical_score", 50),
                    "fundamental": analysis.get("fundamental_score", 50),
                    "sentiment": analysis.get("sentiment_score", 50),
                    "overall": self._calculate_overall_score(analysis),
                },
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
                "indicators": data.get("indicators", {}),
                "consensus": analysis.get("consensus", {}),
                "trend_outlook": trend_outlook,
                "trend_outlook_summary": trend_outlook_summary,
                "trendOutlook": trend_outlook,
                "trendOutlookSummary": trend_outlook_summary,
                "analysis_time_ms": total_time,
                "llm_time_ms": llm_time,
                "data_collection_time_ms": data.get("collection_time_ms", 0),
            })
            
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
        """
        根据技术指标构建决策指导，帮助AI做出更合理的决策。
        强调SELL信号是有效的做空机会。
        """
        guidance_parts = []
        
        # RSI 指导 - 更积极地识别做空机会
        if rsi_value > 70:
            guidance_parts.append("🔴 RSI > 70 (超买): 强烈建议SELL做空，避免BUY")
        elif rsi_value > 60:
            guidance_parts.append("🟠 RSI > 60 (偏超买): 建议SELL做空，谨慎BUY")
        elif rsi_value < 30:
            guidance_parts.append("🟢 RSI < 30 (超卖): 建议BUY做多，避免SELL")
        elif rsi_value < 40:
            guidance_parts.append("🟡 RSI < 40 (偏超卖): 可以考虑BUY做多")
        else:
            guidance_parts.append("⚪ RSI 40-60 (中性): 技术面中性，需要结合其他指标判断")
        
        # MACD 指导 - 明确做空信号
        if macd_signal == "bullish":
            guidance_parts.append("🟢 MACD 看涨: 支持BUY做多")
        elif macd_signal == "bearish":
            guidance_parts.append("🔴 MACD 看跌: 支持SELL做空，这是有效的做空机会")
        else:
            guidance_parts.append("⚪ MACD 中性: 无明显方向")
        
        # MA 趋势指导 - 识别趋势反转机会
        if "uptrend" in ma_trend.lower() or "strong_uptrend" in ma_trend.lower():
            if rsi_value > 60:
                guidance_parts.append("⚠️ 均线向上但RSI超买: 可能接近顶部，考虑SELL做空")
            else:
                guidance_parts.append("🟢 均线趋势向上: 支持BUY做多")
        elif "downtrend" in ma_trend.lower() or "strong_downtrend" in ma_trend.lower():
            guidance_parts.append("🔴 均线趋势向下: 这是SELL做空的良好机会，避免BUY")
        else:
            guidance_parts.append("⚪ 均线横盘: 趋势不明确")
        
        # 24小时涨跌幅指导 - 识别过度波动
        if change_24h > 5:
            guidance_parts.append("🔴 24h涨幅 > 5%: 可能已过度上涨，建议SELL做空或获利了结")
        elif change_24h < -5:
            guidance_parts.append("🟢 24h跌幅 > 5%: 可能已过度下跌，可以考虑BUY做多")
        
        # 综合建议
        sell_signals = sum([
            rsi_value > 60,
            macd_signal == "bearish",
            "downtrend" in ma_trend.lower(),
            change_24h > 5
        ])
        buy_signals = sum([
            rsi_value < 40,
            macd_signal == "bullish",
            "uptrend" in ma_trend.lower(),
            change_24h < -5
        ])
        
        if sell_signals >= 2:
            guidance_parts.append(f"📊 综合判断: {sell_signals}个做空信号，建议考虑SELL")
        elif buy_signals >= 2:
            guidance_parts.append(f"📊 综合判断: {buy_signals}个做多信号，建议考虑BUY")
        else:
            guidance_parts.append("📊 综合判断: 信号混合，需要结合宏观和新闻判断")
        
        return "\n".join(guidance_parts) if guidance_parts else "技术指标数据不足，请谨慎判断"
    
    def _has_major_news(self, news_data: List[Dict]) -> bool:
        """
        检查是否有重大新闻事件。
        重大新闻包括：监管变化、重大合作、丑闻、重大政策、地缘政治事件等。
        地缘类使用词边界与分级，避免 toward/extension/us 等子串误判。
        """
        if not news_data:
            return False

        # 子串关键词（较长词或中文，避免过短英文误匹配）
        major_keywords = [
            "regulation", "regulatory", "approval", "policy", "government", "central bank",
            "监管", "禁令", "批准", "政策", "政府", "央行",
            "partnership", "merger", "acquisition", "scandal", "lawsuit", "investigation",
            "合作", "合并", "收购", "丑闻", "诉讼", "调查",
            "sanctions", "embargo", "制裁", "中东", "海湾", "北约",
            "united states", "middle east",
        ]
        # 短英文词用词边界匹配（不用裸子串）
        major_short_patterns = [
            re.compile(r"\b(?:ban|banned|banning)\b", re.I),
            re.compile(r"\b(?:crisis|crises)\b", re.I),
            re.compile(r"\b(?:catastrophe|meltdown)\b", re.I),
        ]

        for news in news_data[:10]:
            title = news.get("title") or news.get("headline") or ""
            summary = news.get("summary") or ""
            sentiment = news.get("sentiment", "neutral")
            text_to_check = f"{title} {summary}"
            low = text_to_check.lower()

            if _is_major_geopolitical_news_text(text_to_check):
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
        
        # 检查VIX（恐慌指数）
        if "VIX" in macro_data:
            vix = macro_data["VIX"]
            vix_value = vix.get("price", 0)
            if vix_value > 30:  # VIX > 30 表示极度恐慌
                return True
        
        # 检查DXY大幅波动（>1%）
        if "DXY" in macro_data:
            dxy = macro_data["DXY"]
            change_pct = abs(dxy.get("changePercent", 0))
            if change_pct > 1.0:  # 美元指数波动超过1%
                return True
        
        # 检查利率变化（对股票和加密货币影响大）
        if "TNX" in macro_data and market in ["USStock", "Crypto"]:
            tnx = macro_data["TNX"]
            change_pct = abs(tnx.get("changePercent", 0))
            if change_pct > 2.0:  # 利率变化超过2%
                return True
        
        return False
    
    def _finalize_trading_plan_for_decision(
        self, analysis: Dict, current_price: float, indicators: Optional[Dict] = None
    ) -> Dict:
        """
        After decision is final: force correct stop/take-profit geometry and mirror long levels for shorts.
        BUY: stop_loss < current < take_profit
        SELL: take_profit < current < stop_loss (short: stop above, TP below)
        """
        if not current_price or current_price <= 0:
            return analysis
        indicators = indicators or {}
        decision = str(analysis.get("decision", "HOLD")).upper()
        if decision not in ("BUY", "SELL"):
            return analysis

        min_price = current_price * 0.90
        max_price = current_price * 1.10
        eps = max(abs(current_price) * 1e-6, 1e-8)

        tl = indicators.get("trading_levels") or {}
        sl_long = _safe_float_price(tl.get("suggested_stop_loss"))
        tp_long = _safe_float_price(tl.get("suggested_take_profit"))
        long_ok = (
            sl_long is not None
            and tp_long is not None
            and sl_long < current_price - eps
            and tp_long > current_price + eps
        )

        if decision == "SELL":
            if long_ok:
                mirrored_sl = round(2 * current_price - sl_long, 6)
                mirrored_tp = round(2 * current_price - tp_long, 6)
                mirrored_sl = min(max(mirrored_sl, current_price + eps), max_price)
                mirrored_tp = max(min(mirrored_tp, current_price - eps), min_price)
                if mirrored_sl > current_price and mirrored_tp < current_price:
                    analysis["stop_loss"] = mirrored_sl
                    analysis["take_profit"] = mirrored_tp
                else:
                    analysis["stop_loss"] = round(min(max_price, current_price * 1.05), 6)
                    analysis["take_profit"] = round(max(min_price, current_price * 0.95), 6)
            else:
                sl_f = _safe_float_price(analysis.get("stop_loss"))
                tp_f = _safe_float_price(analysis.get("take_profit"))
                if sl_f is not None and tp_f is not None and tp_f < current_price < sl_f:
                    analysis["stop_loss"] = round(min(max(sl_f, current_price + eps), max_price), 6)
                    analysis["take_profit"] = round(max(min(tp_f, current_price - eps), min_price), 6)
                else:
                    analysis["stop_loss"] = round(min(max_price, current_price * 1.05), 6)
                    analysis["take_profit"] = round(max(min_price, current_price * 0.95), 6)
        else:  # BUY
            if long_ok:
                sl = max(min(sl_long, current_price - eps), min_price)
                tp = min(max(tp_long, current_price + eps), max_price)
                analysis["stop_loss"] = round(sl, 6)
                analysis["take_profit"] = round(tp, 6)
            else:
                sl_f = _safe_float_price(analysis.get("stop_loss"))
                tp_f = _safe_float_price(analysis.get("take_profit"))
                if sl_f is not None and tp_f is not None and sl_f < current_price < tp_f:
                    analysis["stop_loss"] = round(max(min(sl_f, current_price - eps), min_price), 6)
                    analysis["take_profit"] = round(min(max(tp_f, current_price + eps), max_price), 6)
                else:
                    analysis["stop_loss"] = round(max(min_price, current_price * 0.95), 6)
                    analysis["take_profit"] = round(min(max_price, current_price * 1.05), 6)

        # Last-resort: fix inverted or equal levels
        sl_f = _safe_float_price(analysis.get("stop_loss"), current_price)
        tp_f = _safe_float_price(analysis.get("take_profit"), current_price)
        if sl_f is None or tp_f is None:
            return analysis
        if decision == "SELL":
            if not (tp_f < current_price < sl_f):
                analysis["stop_loss"] = round(min(max_price, current_price * 1.05), 6)
                analysis["take_profit"] = round(max(min_price, current_price * 0.95), 6)
        else:
            if not (sl_f < current_price < tp_f):
                analysis["stop_loss"] = round(max(min_price, current_price * 0.95), 6)
                analysis["take_profit"] = round(min(max_price, current_price * 1.05), 6)

        return analysis

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
        entry = _safe_float_price(analysis.get("entry_price"), current_price)
        if entry is not None and (entry < min_price or entry > max_price):
            logger.warning(f"Entry price {entry} out of bounds, constraining to current price {current_price}")
            analysis["entry_price"] = round(current_price, 6)
        elif entry is not None:
            analysis["entry_price"] = round(entry, 6)
        
        # Constrain stop loss / take profit by direction (numeric-safe).
        # BUY: stop_loss < current < take_profit
        # SELL: take_profit < current < stop_loss
        if decision == "SELL":
            stop_default = round(current_price * 1.05, 6)
            tp_default = round(current_price * 0.95, 6)
            stop_loss = _safe_float_price(analysis.get("stop_loss"), stop_default)
            take_profit = _safe_float_price(analysis.get("take_profit"), tp_default)
            if stop_loss is None or stop_loss <= current_price or stop_loss > max_price:
                analysis["stop_loss"] = stop_default
            else:
                analysis["stop_loss"] = round(stop_loss, 6)
            if take_profit is None or take_profit >= current_price or take_profit < min_price:
                analysis["take_profit"] = tp_default
            else:
                analysis["take_profit"] = round(take_profit, 6)
        else:
            stop_default = round(current_price * 0.95, 6)
            tp_default = round(current_price * 1.05, 6)
            stop_loss = _safe_float_price(analysis.get("stop_loss"), stop_default)
            take_profit = _safe_float_price(analysis.get("take_profit"), tp_default)
            if stop_loss is None or stop_loss < min_price or stop_loss >= current_price:
                analysis["stop_loss"] = stop_default
            else:
                analysis["stop_loss"] = round(stop_loss, 6)
            if take_profit is None or take_profit <= current_price or take_profit > max_price:
                analysis["take_profit"] = tp_default
            else:
                analysis["take_profit"] = round(take_profit, 6)
        
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
        
        # 基于技术指标验证决策合理性（允许宏观/新闻因素覆盖）
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
        
        # 如果置信度太低，强制改为HOLD
        if confidence < 60:
            if decision != "HOLD":
                logger.warning(f"Decision {decision} with low confidence {confidence}, forcing to HOLD")
                analysis["decision"] = "HOLD"
                analysis["confidence"] = max(confidence, 45)  # 降低置信度
            return analysis
        
        # 如果有重大新闻或宏观事件，允许覆盖技术指标（但记录警告）
        allow_override = has_major_news or has_macro_event
        
        # 检查BUY决策是否与技术指标矛盾
        if decision == "BUY":
            conflicts = []
            
            # RSI > 70 时不应该BUY（除非有重大利好）
            if rsi_value > 70:
                conflicts.append(f"RSI {rsi_value:.1f} > 70 (超买)")
            
            # MACD看跌时不应该BUY（除非有重大利好）
            if macd_signal == "bearish":
                conflicts.append("MACD bearish")
            
            # 均线趋势向下时不应该BUY（除非有重大利好）
            # 只有当趋势非常强烈时才认为是冲突（避免过于敏感）
            if "strong_downtrend" in ma_trend.lower() or ("downtrend" in ma_trend.lower() and rsi_value > 50):
                conflicts.append(f"MA trend: {ma_trend}")
            
            if conflicts:
                if allow_override:
                    # 允许覆盖，但降低置信度并添加说明
                    logger.info(f"BUY decision conflicts with indicators but major news/macro event allows override: {', '.join(conflicts)}")
                    analysis["confidence"] = max(confidence - 15, 50)
                    original_summary = analysis.get("summary", "")
                    analysis["summary"] = f"{original_summary} [注意：技术指标显示{', '.join(conflicts)}，但重大事件可能改变趋势]"
                else:
                    # 没有重大事件，强制改为HOLD
                    logger.warning(f"BUY decision conflicts with indicators and no major event: {', '.join(conflicts)}. Forcing to HOLD")
                    analysis["decision"] = "HOLD"
                    analysis["confidence"] = max(confidence - 20, 40)
                    original_summary = analysis.get("summary", "")
                    analysis["summary"] = f"{original_summary} [注意：技术指标显示{', '.join(conflicts)}，建议观望]"
        
        # 检查SELL决策是否与技术指标矛盾（放宽限制，因为SELL是有效的做空机会）
        elif decision == "SELL":
            conflicts = []
            
            # 只有在强烈看涨信号时才阻止SELL（放宽条件）
            # RSI < 30 且 MACD看涨 且 均线向上时，才认为矛盾
            if rsi_value < 30 and macd_signal == "bullish" and "uptrend" in ma_trend.lower():
                conflicts.append(f"Strong bullish signals (RSI {rsi_value:.1f} < 30, MACD bullish, uptrend)")
            # 或者 RSI < 30 且 均线强烈向上
            elif rsi_value < 30 and "strong_uptrend" in ma_trend.lower():
                conflicts.append(f"Very strong uptrend with oversold RSI {rsi_value:.1f}")
            
            if conflicts:
                if allow_override:
                    # 允许覆盖，但降低置信度并添加说明
                    logger.info(f"SELL decision conflicts with strong bullish indicators but major news/macro event allows override: {', '.join(conflicts)}")
                    analysis["confidence"] = max(confidence - 15, 50)
                    original_summary = analysis.get("summary", "")
                    analysis["summary"] = f"{original_summary} [注意：技术指标显示{', '.join(conflicts)}，但重大事件可能改变趋势]"
                else:
                    # 只有在非常强烈的看涨信号时才改为HOLD
                    logger.warning(f"SELL decision conflicts with very strong bullish indicators: {', '.join(conflicts)}. Forcing to HOLD")
                    analysis["decision"] = "HOLD"
                    analysis["confidence"] = max(confidence - 20, 40)
                    original_summary = analysis.get("summary", "")
                    analysis["summary"] = f"{original_summary} [注意：技术指标显示{', '.join(conflicts)}，建议观望]"
        
        return analysis
    
    def _calculate_objective_score(self, data: Dict[str, Any], current_price: float) -> Dict[str, float]:
        """
        基于客观数据计算量化评分系统
        
        返回一个-100到+100的分数：
        - +100: 强烈利多（强烈BUY）
        - +70到+100: 强烈利多（强烈BUY）
        - +40到+70: 利多（BUY）
        - -40到+40: 中性（HOLD）
        - -70到-40: 利空（SELL）
        - -100到-70: 强烈利空（强烈SELL）
        - -100: 强烈利空（强烈SELL）
        """
        indicators = data.get("indicators") or {}
        fundamental = data.get("fundamental") or {}
        news = data.get("news") or []
        macro = data.get("macro") or {}
        price_data = data.get("price") or {}
        crypto_factors = data.get("crypto_factors") or {}
        
        # 1. 技术指标评分 (-100 to +100)
        technical_score = self._calculate_technical_score(indicators, price_data)
        
        # 2. 基本面评分 (-100 to +100)
        fundamental_score = self._calculate_fundamental_score(fundamental, data.get("market", ""))
        crypto_factor_objective = self._calculate_crypto_factor_score(crypto_factors, price_data)
        crypto_factor_score = float(crypto_factor_objective.get("score", 0.0) or 0.0)
        if str(data.get("market") or "").strip() == "Crypto" and crypto_factors:
            fundamental_score = crypto_factor_score
        
        # 3. 新闻情绪评分 (-100 to +100)
        sentiment_score = self._calculate_sentiment_score(news)
        
        # 4. 宏观环境评分 (-100 to +100)
        macro_score = self._calculate_macro_score(macro, data.get("market", ""))
        
        # 5. 综合评分（加权平均）
        # 优化权重：默认技术35%，基本面20%，情绪25%（包含地缘政治），宏观20%（提高宏观权重）
        # 但要做“可用信息重加权”：当某些模块缺失（如新闻/宏观没取到），不要用0分去稀释整体强度，
        # 而是重新归一化权重，让技术信号在缺失时仍可发挥主导作用。
        market_type = str(data.get("market") or "")

        def _fundamental_meaningful(fund: Dict[str, Any]) -> bool:
            if not fund:
                return False
            for key in (
                "pe_ratio",
                "pb_ratio",
                "ps_ratio",
                "market_cap",
                "roe",
                "eps",
                "revenue_growth",
                "profit_margin",
                "dividend_yield",
            ):
                v = fund.get(key)
                if v is None or v == "":
                    continue
                try:
                    if isinstance(v, float) and v != v:  # NaN
                        continue
                    return True
                except Exception:
                    return True
            return False

        fundamental_present = (
            market_type in ("USStock", "CNStock", "HKStock") and _fundamental_meaningful(fundamental)
        )
        if market_type == "Crypto" and crypto_factors:
            fundamental_present = True
        sentiment_present = bool(news)
        macro_present = bool(macro)
        # indicators 一旦成功计算通常就存在，但这里也做一次保护
        technical_present = bool(indicators)

        weights = {
            "technical": 0.35,
            "fundamental": 0.20,
            "sentiment": 0.25,
            "macro": 0.20,
        }
        present_flags = {
            "technical": technical_present,
            "fundamental": fundamental_present,
            "sentiment": sentiment_present,
            "macro": macro_present,
        }

        total_w = sum(w for k, w in weights.items() if present_flags.get(k))
        if total_w <= 0:
            overall_score = technical_score
        else:
            overall_score = (
                (technical_score * weights["technical"] if present_flags.get("technical") else 0.0)
                + (fundamental_score * weights["fundamental"] if present_flags.get("fundamental") else 0.0)
                + (sentiment_score * weights["sentiment"] if present_flags.get("sentiment") else 0.0)
                + (macro_score * weights["macro"] if present_flags.get("macro") else 0.0)
            ) / total_w
        
        return {
            "technical_score": technical_score,
            "fundamental_score": fundamental_score,
            "sentiment_score": sentiment_score,
            "macro_score": macro_score,
            "overall_score": overall_score,
            "crypto_factor_score": crypto_factor_score,
            "crypto_factor_breakdown": crypto_factor_objective.get("breakdown", []),
            "crypto_factor_summary": crypto_factor_objective.get("summary") or (crypto_factors.get("summary") if crypto_factors else ""),
        }

    def _get_ai_calibration(self, market: str = "Crypto") -> Dict[str, Any]:
        """
        Load latest offline calibration thresholds for the given market.
        Cached briefly to avoid DB load on every request.
        """
        # Simple per-process cache
        now = time.time()
        if not hasattr(self, "_calibration_cache"):
            self._calibration_cache = {}
            self._calibration_cache_ts = {}
        ttl = int(os.getenv("AI_CALIBRATION_CACHE_TTL_SEC", "300"))
        key = (market or "").strip() or "Crypto"
        ts = self._calibration_cache_ts.get(key) or 0.0
        if ts and (now - float(ts)) < ttl:
            return self._calibration_cache.get(key) or {}

        try:
            from app.services.ai_calibration import AICalibrationService
            svc = AICalibrationService()
            cfg = svc.get_latest(key)
        except Exception as e:
            logger.warning(f"_get_ai_calibration failed (fallback): {e}", exc_info=True)
            cfg = {}

        self._calibration_cache[key] = cfg
        self._calibration_cache_ts[key] = now
        return cfg
    
    def _calculate_technical_score(self, indicators: Dict, price_data: Dict) -> float:
        """计算技术指标评分 (-100 to +100)"""
        score = 0.0
        weight_sum = 0.0
        
        # RSI 评分 (-50 to +50)
        rsi_data = indicators.get("rsi", {})
        rsi_value = rsi_data.get("value", 50)
        if rsi_value > 0:
            if rsi_value > 70:
                rsi_score = -50  # 超买，强烈利空
            elif rsi_value > 60:
                rsi_score = -30  # 偏超买，利空
            elif rsi_value < 30:
                rsi_score = +50  # 超卖，强烈利多
            elif rsi_value < 40:
                rsi_score = +30  # 偏超卖，利多
            else:
                rsi_score = (50 - rsi_value) * 0.6  # 40-60之间，线性映射
            score += rsi_score * 0.30
            weight_sum += 0.30
        
        # MACD 评分 (-40 to +40)
        macd_data = indicators.get("macd", {})
        macd_signal = macd_data.get("signal", "neutral")
        if macd_signal == "bullish":
            macd_score = +40
        elif macd_signal == "bearish":
            macd_score = -40
        else:
            macd_score = 0
        score += macd_score * 0.25
        weight_sum += 0.25
        
        # 均线趋势评分 (-40 to +40)
        ma_data = indicators.get("moving_averages", {})
        ma_trend = ma_data.get("trend", "sideways")
        if "strong_uptrend" in ma_trend.lower():
            ma_score = +40
        elif "uptrend" in ma_trend.lower():
            ma_score = +25
        elif "strong_downtrend" in ma_trend.lower():
            ma_score = -40
        elif "downtrend" in ma_trend.lower():
            ma_score = -25
        else:
            ma_score = 0
        score += ma_score * 0.25
        weight_sum += 0.25
        
        # 24小时涨跌幅评分 (-20 to +20)
        change_24h = price_data.get("changePercent", 0)
        if change_24h > 10:
            change_score = -20  # 过度上涨，利空
        elif change_24h > 5:
            change_score = -10
        elif change_24h < -10:
            change_score = +20  # 过度下跌，利多
        elif change_24h < -5:
            change_score = +10
        else:
            change_score = change_24h * 2  # 线性映射
        score += change_score * 0.20
        weight_sum += 0.20

        # ========== 额外技术特征（轻量增强，不改变主体结构） ==========
        # 这些特征来自 MarketDataCollector._calculate_indicators 的输出：
        # - price_position: 过去20根K线区间位置 0~100
        # - volume_ratio: 最新成交量 / 20期均量
        # - bollinger: BB_upper/BB_lower/BB_width
        # - volatility: atr, pct
        extra_score = 0.0
        extra_weight = 0.0

        # 1) 区间位置：接近区间顶部更偏利空，接近区间底部更偏利多
        try:
            pp = float(indicators.get("price_position", 50.0))
            # 0~100 -> -15~+15 (线性映射，中心50为0)
            pp_score = (50.0 - pp) * 0.3
            # 在极端区域增强信号
            if pp >= 85:
                pp_score -= 5
            elif pp <= 15:
                pp_score += 5
            extra_score += pp_score
            extra_weight += 0.20
        except Exception:
            pass

        # 2) 布林带触及：突破上轨偏利空，跌破下轨偏利多
        try:
            cur_px = float(indicators.get("current_price") or price_data.get("price") or 0.0)
            bb = indicators.get("bollinger") or {}
            bb_u = float(bb.get("BB_upper") or 0.0)
            bb_l = float(bb.get("BB_lower") or 0.0)
            if cur_px > 0 and bb_u > 0 and bb_l > 0 and bb_u > bb_l:
                if cur_px >= bb_u:
                    extra_score += -12
                    extra_weight += 0.20
                elif cur_px <= bb_l:
                    extra_score += +12
                    extra_weight += 0.20
                else:
                    # Within bands: small contribution by relative position
                    rel = (cur_px - bb_l) / (bb_u - bb_l)  # 0..1
                    extra_score += (0.5 - float(rel)) * 10
                    extra_weight += 0.10
        except Exception:
            pass

        # 3) 成交量放大：在趋势方向上加分，逆趋势减分（弱信号）
        try:
            vr = float(indicators.get("volume_ratio") or 1.0)
            trend = str(indicators.get("trend") or indicators.get("moving_averages", {}).get("trend") or "").lower()
            if vr >= 1.8:
                if "uptrend" in trend:
                    extra_score += +8
                    extra_weight += 0.15
                elif "downtrend" in trend:
                    extra_score += -8
                    extra_weight += 0.15
                else:
                    # 放量但无趋势：更偏不确定，略微降低（当作偏利空风险）
                    extra_score += -3
                    extra_weight += 0.10
            elif vr <= 0.6:
                # 缩量：趋势信号可信度下降（轻微回归到0）
                extra_score += 0
                extra_weight += 0.05
        except Exception:
            pass

        # 4) 高波动：减少强方向自信（用“缩放”形式实现，避免硬反转）
        try:
            vol = indicators.get("volatility") or {}
            vol_pct = float(vol.get("pct") or 0.0)
            if vol_pct >= 6.0:
                # 极高波动：把额外分数打折，并轻微把总体拉回0
                extra_score *= 0.6
                score *= 0.92
            elif vol_pct >= 3.5:
                extra_score *= 0.8
                score *= 0.96
        except Exception:
            pass

        # Combine extra into main score (treat as another component)
        if extra_weight > 0:
            # Normalize extra to roughly -100..+100 scale
            extra_norm = max(-100.0, min(100.0, float(extra_score)))
            score += extra_norm * 0.15
            weight_sum += 0.15
        
        # 归一化到-100到+100
        if weight_sum > 0:
            score = score / weight_sum * 100
        
        return max(-100, min(100, score))
    
    def _calculate_fundamental_score(self, fundamental: Dict, market: str) -> float:
        """计算基本面评分 (-100 to +100)"""
        if market not in ("USStock", "CNStock", "HKStock") or not fundamental:
            return 50.0

        score = 0.0
        factors = 0
        
        # PE Ratio 评分
        pe_ratio = fundamental.get("pe_ratio")
        if pe_ratio and pe_ratio > 0:
            if pe_ratio < 15:
                pe_score = +20  # 低PE，利多
            elif pe_ratio < 25:
                pe_score = +10
            elif pe_ratio > 50:
                pe_score = -20  # 高PE，利空
            elif pe_ratio > 35:
                pe_score = -10
            else:
                pe_score = 0
            score += pe_score
            factors += 1
        
        # ROE 评分
        roe = fundamental.get("roe")
        if roe:
            if roe > 20:
                roe_score = +20  # 高ROE，利多
            elif roe > 15:
                roe_score = +10
            elif roe < 5:
                roe_score = -20  # 低ROE，利空
            elif roe < 10:
                roe_score = -10
            else:
                roe_score = 0
            score += roe_score
            factors += 1
        
        # 营收增长评分
        revenue_growth = fundamental.get("revenue_growth")
        if revenue_growth:
            if revenue_growth > 20:
                growth_score = +20  # 高增长，利多
            elif revenue_growth > 10:
                growth_score = +10
            elif revenue_growth < -10:
                growth_score = -20  # 负增长，利空
            elif revenue_growth < 0:
                growth_score = -10
            else:
                growth_score = 0
            score += growth_score
            factors += 1
        
        # 利润率评分
        profit_margin = fundamental.get("profit_margin")
        if profit_margin:
            if profit_margin > 20:
                margin_score = +15  # 高利润率，利多
            elif profit_margin > 10:
                margin_score = +7
            elif profit_margin < 0:
                margin_score = -15  # 亏损，利空
            elif profit_margin < 5:
                margin_score = -7
            else:
                margin_score = 0
            score += margin_score
            factors += 1
        
        # 债务权益比评分
        debt_to_equity = fundamental.get("debt_to_equity")
        if debt_to_equity:
            if debt_to_equity < 0.5:
                debt_score = +10  # 低负债，利多
            elif debt_to_equity > 2.0:
                debt_score = -10  # 高负债，利空
            else:
                debt_score = 0
            score += debt_score
            factors += 1
        
        # 归一化（如果有多个因素）
        if factors > 0:
            score = score / factors * 100 / 4  # 最大可能分数是4个因素各20分=80，归一化到100
        else:
            return 50.0

        return max(-100, min(100, score))

    def _calculate_crypto_factor_score(self, crypto_factors: Dict[str, Any], price_data: Dict[str, Any]) -> Dict[str, Any]:
        """基于加密货币交易大数据因子计算可解释评分。"""
        if not crypto_factors:
            return {"score": 0.0, "breakdown": [], "summary": ""}

        breakdown = []
        score = 0.0

        def add(name: str, value: float, reason: str):
            nonlocal score
            score += float(value)
            breakdown.append({"factor": name, "score": round(float(value), 2), "reason": reason})

        funding_rate = crypto_factors.get("funding_rate")
        oi_change = crypto_factors.get("open_interest_change_24h")
        long_short_ratio = crypto_factors.get("long_short_ratio")
        exchange_netflow = crypto_factors.get("exchange_netflow")
        stablecoin_netflow = crypto_factors.get("stablecoin_netflow")
        volume_change = crypto_factors.get("volume_change_24h")
        change_24h = (price_data or {}).get("changePercent")

        try:
            if funding_rate is not None and oi_change is not None:
                fr = float(funding_rate)
                oi = float(oi_change)
                if fr > 0 and oi > 3:
                    add("funding_oi", 18, "资金费率偏正且 OI 上升，衍生品多头动能增强")
                elif fr < 0 and oi > 3:
                    add("funding_oi", -18, "资金费率偏负且 OI 上升，空头动能增强")
        except Exception:
            pass

        try:
            if exchange_netflow is not None:
                enf = float(exchange_netflow)
                if enf < 0:
                    add("exchange_netflow", 16, "交易所净流出，筹码倾向离场保管，通常偏利多")
                elif enf > 0:
                    add("exchange_netflow", -16, "交易所净流入，潜在卖压或风险对冲上升")
        except Exception:
            pass

        try:
            if stablecoin_netflow is not None:
                stf = float(stablecoin_netflow)
                if stf > 0:
                    add("stablecoin_netflow", 12, "稳定币净流入增强，潜在买盘增加")
                elif stf < 0:
                    add("stablecoin_netflow", -12, "稳定币净流出，边际买盘转弱")
        except Exception:
            pass

        try:
            if long_short_ratio is not None:
                lsr = float(long_short_ratio)
                if lsr > 1.6:
                    add("long_short_ratio", -10, "多空比过热，需警惕多头拥挤和长挤风险")
                elif lsr < 0.75:
                    add("long_short_ratio", 8, "空头占优过深，存在反向挤空可能")
        except Exception:
            pass

        try:
            if volume_change is not None and change_24h is not None:
                vol = float(volume_change)
                chg = float(change_24h)
                if vol > 15 and chg > 0:
                    add("volume_price", 10, "放量上涨，趋势确认度提升")
                elif vol > 15 and chg < 0:
                    add("volume_price", -10, "放量下跌，空头主导增强")
                elif vol < -15 and abs(chg) > 3:
                    add("volume_price", -6 if chg > 0 else 6, "价格波动与成交回落背离，趋势持续性存疑")
        except Exception:
            pass

        squeeze_risk = ((crypto_factors.get("signals") or {}).get("squeeze_risk") or "").lower()
        if squeeze_risk == "high":
            add("squeeze_risk", -8, "衍生品拥挤度高，短线波动放大风险上升")
        elif squeeze_risk == "medium":
            add("squeeze_risk", -3, "衍生品拥挤度抬升，需要控制追涨杀跌")

        summary = crypto_factors.get("summary") or ""
        return {
            "score": max(-100.0, min(100.0, score)),
            "breakdown": breakdown,
            "summary": summary,
        }
    
    def _calculate_sentiment_score(self, news: List[Dict]) -> float:
        """
        计算新闻情绪评分 (-100 to +100)
        地缘/冲突类：词边界 + 分级惩罚，单条封顶，避免 extension/toward 等误判叠加。
        """
        if not news:
            return 0.0  # 无新闻，中性

        positive_count = 0
        negative_count = 0
        neutral_count = 0
        geopolitical_penalty = 0
        max_geo_total = int(os.getenv("SENTIMENT_GEO_PENALTY_CAP", "-55"))

        for item in news[:15]:
            title = item.get("headline") or item.get("title") or ""
            summary = item.get("summary") or ""
            text = f"{title} {summary}"
            sentiment = item.get("sentiment", "neutral")
            is_global_event = item.get("is_global_event", False)

            level, tag = _geopolitical_match_level(text)
            if is_global_event and level == "none":
                level, tag = "moderate", "is_global_event"

            if level != "none":
                delta = _geopolitical_sentiment_penalty_delta(level)
                new_total = geopolitical_penalty + delta
                if new_total < max_geo_total:
                    delta = max_geo_total - geopolitical_penalty
                geopolitical_penalty += delta
                preview = (title or summary or "")[:72]
                logger.info(
                    f"Geopolitical sentiment ({level}, {tag}): {preview!r}, "
                    f"delta={delta}, cumulative={geopolitical_penalty}"
                )

            if sentiment == "positive":
                positive_count += 1
            elif sentiment == "negative":
                negative_count += 1
            else:
                neutral_count += 1

        total = positive_count + negative_count + neutral_count

        if total > 0:
            net_sentiment = (positive_count - negative_count) / total
            base_score = net_sentiment * 60
        else:
            base_score = 0

        if geopolitical_penalty != 0:
            final_score = base_score + geopolitical_penalty
            logger.info(
                f"Sentiment score: base={base_score:.1f}, "
                f"geopolitical_penalty={geopolitical_penalty}, final={final_score:.1f}"
            )
        else:
            final_score = base_score

        return max(-100, min(100, final_score))
    
    def _calculate_macro_score(self, macro: Dict, market: str) -> float:
        """
        计算宏观环境评分 (-100 to +100)
        包含VIX、DXY、利率等宏观经济指标
        """
        if not macro:
            return 0.0  # 无宏观数据，中性
        
        score = 0.0
        factors = 0
        
        # VIX 评分（恐慌指数）- 权重提高
        vix = macro.get("VIX", {})
        vix_value = vix.get("price", 0)
        if vix_value > 0:
            if vix_value > 35:
                vix_score = -50  # 极高恐慌（如战争期间），严重利空
            elif vix_value > 30:
                vix_score = -40  # 高恐慌，严重利空
            elif vix_value > 25:
                vix_score = -30  # 较高恐慌，利空
            elif vix_value > 20:
                vix_score = -15  # 中等恐慌，轻微利空
            elif vix_value < 12:
                vix_score = +20  # 低恐慌，利多
            elif vix_value < 15:
                vix_score = +10  # 较低恐慌，轻微利多
            else:
                vix_score = 0
            score += vix_score
            factors += 1
        
        # DXY 评分（美元指数）- 权重提高
        dxy = macro.get("DXY", {})
        dxy_value = dxy.get("price", 0)
        dxy_change = dxy.get("changePercent", 0)
        if dxy_value > 0:
            # 对于加密货币和商品，强美元通常是利空
            if market in ["Crypto", "Forex", "Futures"]:
                if dxy_change > 2:
                    dxy_score = -30  # 美元大幅走强，严重利空
                elif dxy_change > 1:
                    dxy_score = -20  # 美元走强，利空
                elif dxy_change < -2:
                    dxy_score = +30  # 美元大幅走弱，利多
                elif dxy_change < -1:
                    dxy_score = +20  # 美元走弱，利多
                else:
                    dxy_score = 0
            else:
                # 对股票也有影响，但较小
                if dxy_change > 2:
                    dxy_score = -10
                elif dxy_change < -2:
                    dxy_score = +10
                else:
                    dxy_score = 0
            score += dxy_score
            factors += 1
        
        # 利率评分（TNX）- 权重提高
        tnx = macro.get("TNX", {})
        tnx_change = tnx.get("changePercent", 0)
        tnx_value = tnx.get("price", 0)
        if tnx_change != 0 or tnx_value > 0:
            # 利率上升对成长股和加密货币通常是利空
            if market in ["Crypto", "USStock"]:
                if tnx_change > 3:
                    tnx_score = -30  # 利率大幅上升，严重利空
                elif tnx_change > 2:
                    tnx_score = -20  # 利率上升，利空
                elif tnx_change < -3:
                    tnx_score = +30  # 利率大幅下降，利多
                elif tnx_change < -2:
                    tnx_score = +20  # 利率下降，利多
                else:
                    tnx_score = 0
            else:
                tnx_score = 0
            score += tnx_score
            factors += 1

        # 恐惧贪婪指数（更适合 Crypto）：极端贪婪偏利空，极端恐惧偏利多（弱信号）
        try:
            fg = macro.get("FEAR_GREED", {}) or {}
            fg_value = float(fg.get("price") or 0.0)
            if fg_value > 0 and market in ["Crypto"]:
                if fg_value >= 80:
                    score += -15
                    factors += 1
                elif fg_value >= 65:
                    score += -8
                    factors += 1
                elif fg_value <= 20:
                    score += +10
                    factors += 1
                elif fg_value <= 35:
                    score += +5
                    factors += 1
        except Exception:
            pass
        
        # 归一化（考虑权重）
        if factors > 0:
            # 最大可能分数：VIX(-50~+20), DXY(-30~+30), TNX(-30~+30) = 约-110到+80
            # 归一化到-100到+100
            # 加上 Fear&Greed 的幅度（约 15），给点 buffer
            max_possible = 125  # 最大绝对值
            score = score / max_possible * 100
        
        return max(-100, min(100, score))
    
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
        # 优先使用客观评分
        if "objective_score" in analysis:
            objective = analysis["objective_score"]
            overall = objective.get("overall_score", 50)
            # 转换为0-100格式（原系统使用）
            return max(0, min(100, int(50 + overall * 0.5)))
        
        # 降级到LLM评分
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
