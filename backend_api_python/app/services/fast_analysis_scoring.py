"""Fast-analysis objective scoring and calibration policies."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from app.services.fast_analysis_geo import (
    geopolitical_match_level,
    geopolitical_sentiment_penalty_delta,
)
from app.utils.logger import get_logger
from app.research.fundamentals import normalize_fundamentals

logger = get_logger(__name__)


class FastAnalysisScoringMixin:
    def _calculate_objective_score(self, data: Dict[str, Any], current_price: float) -> Dict[str, float]:
        """
        Calculate a normalized objective score from market evidence.









        """
        indicators = data.get("indicators") or {}
        fundamental = normalize_fundamentals(data.get("fundamental"))
        news = [item for item in data.get("news") or [] if item.get("asset_relevance") != "background"]
        macro = data.get("macro") or {}
        price_data = data.get("price") or {}
        crypto_factors = data.get("crypto_factors") or {}

        technical_score = self._calculate_technical_score(indicators, price_data)

        fundamental_score = self._calculate_fundamental_score(fundamental, data.get("market", ""))
        crypto_factor_objective = self._calculate_crypto_factor_score(crypto_factors, price_data)
        crypto_factor_score = float(crypto_factor_objective.get("score", 0.0) or 0.0)
        if str(data.get("market") or "").strip() == "Crypto" and crypto_factors:
            fundamental_score = crypto_factor_score

        sentiment_score = self._calculate_sentiment_score(news)

        macro_score = self._calculate_macro_score(macro, data.get("market", ""))

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
        macro_present = bool(macro) and market_type not in {"CNStock", "HKStock"}
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

    def _technical_risk_context(self, indicators: Dict, price_data: Dict) -> Dict[str, Any]:
        """Classify whether oversold signals are likely reversal or breakdown risk."""
        indicators = indicators or {}
        price_data = price_data or {}
        ma = indicators.get("moving_averages") or {}
        macd = indicators.get("macd") or {}
        trend = str(ma.get("trend") or indicators.get("trend") or "sideways").lower()
        macd_signal = str(macd.get("signal") or "neutral").lower()
        try:
            change_24h = float(price_data.get("changePercent") or 0.0)
        except Exception:
            change_24h = 0.0
        try:
            volume_ratio = float(indicators.get("volume_ratio") or 1.0)
        except Exception:
            volume_ratio = 1.0
        try:
            rsi_value = float((indicators.get("rsi") or {}).get("value") or 50.0)
        except Exception:
            rsi_value = 50.0

        strong_downtrend = "strong_downtrend" in trend
        downtrend = "downtrend" in trend
        bearish_context = bool(downtrend or macd_signal == "bearish")
        panic_breakdown = bool(
            (strong_downtrend and macd_signal == "bearish" and change_24h <= -3.0)
            or (downtrend and macd_signal == "bearish" and change_24h <= -5.0)
            or (change_24h <= -8.0 and volume_ratio >= 1.3)
        )
        return {
            "trend": trend,
            "macd_signal": macd_signal,
            "rsi": rsi_value,
            "change_24h": change_24h,
            "volume_ratio": volume_ratio,
            "downtrend": downtrend,
            "strong_downtrend": strong_downtrend,
            "bearish_context": bearish_context,
            "panic_breakdown": panic_breakdown,
        }

    def _calculate_technical_score(self, indicators: Dict, price_data: Dict) -> float:
        """Calculate the technical score on a -100 to +100 scale."""
        score = 0.0
        weight_sum = 0.0
        risk = self._technical_risk_context(indicators, price_data)

        rsi_data = indicators.get("rsi", {})
        rsi_value = rsi_data.get("value", 50)
        if rsi_value > 0:
            if rsi_value > 70:
                rsi_score = -50
            elif rsi_value > 60:
                rsi_score = -30
            elif rsi_value < 30:
                rsi_score = +50
            elif rsi_value < 40:
                rsi_score = +30
            else:
                rsi_score = (50 - rsi_value) * 0.6
            if rsi_value < 30:
                if risk.get("panic_breakdown"):
                    rsi_score = -10
                elif risk.get("bearish_context"):
                    rsi_score = min(rsi_score, 8)
            elif rsi_value < 40 and risk.get("bearish_context"):
                rsi_score = min(rsi_score, 6)
            score += rsi_score * 0.30
            weight_sum += 0.30

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

        change_24h = price_data.get("changePercent", 0)
        if change_24h > 10:
            change_score = -20
        elif change_24h > 5:
            change_score = -10
        elif change_24h < -10:
            change_score = +20
        elif change_24h < -5:
            change_score = +10
        else:
            change_score = change_24h * 2
        if change_24h < -10:
            change_score = -20 if risk.get("panic_breakdown") else (min(change_score, 5) if risk.get("bearish_context") else change_score)
        elif change_24h < -5:
            change_score = -10 if risk.get("panic_breakdown") else (min(change_score, 3) if risk.get("bearish_context") else change_score)
        score += change_score * 0.20
        weight_sum += 0.20

        # - bollinger: BB_upper/BB_lower/BB_width
        # - volatility: atr, pct
        extra_score = 0.0
        extra_weight = 0.0

        try:
            pp = float(indicators.get("price_position", 50.0))
            pp_score = (50.0 - pp) * 0.3
            if pp >= 85:
                pp_score -= 5
            elif pp <= 15:
                pp_score += 5
            if risk.get("bearish_context") and pp <= 20:
                pp_score = min(pp_score, 3)
            if risk.get("panic_breakdown") and pp <= 20:
                pp_score = min(pp_score, -3)
            extra_score += pp_score
            extra_weight += 0.20
        except Exception:
            pass

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
                    extra_score += (-6 if risk.get("panic_breakdown") else (0 if risk.get("bearish_context") else +12))
                    extra_weight += 0.20
                else:
                    # Within bands: small contribution by relative position
                    rel = (cur_px - bb_l) / (bb_u - bb_l)  # 0..1
                    extra_score += (0.5 - float(rel)) * 10
                    extra_weight += 0.10
        except Exception:
            pass

        try:
            vr = float(indicators.get("volume_ratio") or 1.0)
            trend = str(indicators.get("trend") or indicators.get("moving_averages", {}).get("trend") or "").lower()
            if vr >= 1.8:
                if "uptrend" in trend:
                    extra_score += +8
                    extra_weight += 0.15
                elif "downtrend" in trend:
                    extra_score += (-16 if risk.get("change_24h", 0.0) < 0 else -8)
                    extra_weight += 0.15
                else:
                    extra_score += -3
                    extra_weight += 0.10
            elif vr <= 0.6:
                extra_score += 0
                extra_weight += 0.05
        except Exception:
            pass

        try:
            vol = indicators.get("volatility") or {}
            vol_pct = float(vol.get("pct") or 0.0)
            if vol_pct >= 6.0:
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

        if weight_sum > 0:
            score = score / max(1.0, weight_sum)
        if risk.get("panic_breakdown"):
            score = min(score, -25.0)
        elif risk.get("bearish_context") and risk.get("change_24h", 0.0) <= -3.0:
            score = min(score, 5.0)

        return max(-100, min(100, score))

    def _calculate_fundamental_score(self, fundamental: Dict, market: str) -> float:
        """Calculate the fundamental score on a -100 to +100 scale."""
        if market not in ("USStock", "CNStock", "HKStock") or not fundamental:
            return 0.0

        fundamental = normalize_fundamentals(fundamental)

        statements = fundamental.get("financial_statements") or {}
        latest_quarter = statements.get("latest_quarter") or {}
        quarter_derived = latest_quarter.get("derived") or {}
        ttm_derived = (statements.get("ttm") or {}).get("derived") or {}

        # Current operating health comes from the latest reported quarter;
        # TTM remains the fallback for profitability durability.
        roe = quarter_derived.get("roe")
        if roe is None:
            roe = ttm_derived.get("roe")
        if roe is None:
            roe = fundamental.get("roe")
        revenue_growth = quarter_derived.get("revenue_growth")
        if revenue_growth is None:
            revenue_growth = fundamental.get("revenue_growth")
        profit_margin = quarter_derived.get("profit_margin")
        if profit_margin is None:
            profit_margin = ttm_derived.get("profit_margin")
        if profit_margin is None:
            profit_margin = fundamental.get("profit_margin")
        debt_to_equity = quarter_derived.get("debt_to_equity")
        if debt_to_equity is None:
            debt_to_equity = fundamental.get("debt_to_equity")
        current_ratio = quarter_derived.get("current_ratio")
        if current_ratio is None:
            current_ratio = fundamental.get("current_ratio")

        score = 0.0
        factors = 0

        pe_ratio = fundamental.get("pe_ratio")
        if pe_ratio and pe_ratio > 0:
            if pe_ratio < 15:
                pe_score = +20
            elif pe_ratio < 25:
                pe_score = +10
            elif pe_ratio > 50:
                pe_score = -20
            elif pe_ratio > 35:
                pe_score = -10
            else:
                pe_score = 0
            score += pe_score
            factors += 1

        if roe is not None:
            if roe > 20:
                roe_score = +20
            elif roe > 15:
                roe_score = +10
            elif roe < 5:
                roe_score = -20
            elif roe < 10:
                roe_score = -10
            else:
                roe_score = 0
            score += roe_score
            factors += 1

        if revenue_growth is not None:
            if revenue_growth > 20:
                growth_score = +20
            elif revenue_growth > 10:
                growth_score = +10
            elif revenue_growth < -10:
                growth_score = -20
            elif revenue_growth < 0:
                growth_score = -10
            else:
                growth_score = 0
            score += growth_score
            factors += 1

        if profit_margin is not None:
            if profit_margin > 20:
                margin_score = +15
            elif profit_margin > 10:
                margin_score = +7
            elif profit_margin < 0:
                margin_score = -15
            elif profit_margin < 5:
                margin_score = -7
            else:
                margin_score = 0
            score += margin_score
            factors += 1

        if debt_to_equity is not None:
            if debt_to_equity < 0.5:
                debt_score = +10
            elif debt_to_equity > 2.0:
                debt_score = -10
            else:
                debt_score = 0
            score += debt_score
            factors += 1

        if current_ratio is not None:
            if current_ratio >= 1.5:
                liquidity_score = +10
            elif current_ratio < 1.0:
                liquidity_score = -10
            else:
                liquidity_score = 0
            score += liquidity_score
            factors += 1

        if factors <= 0:
            return 0.0

        return max(-100, min(100, score))

    def _calculate_crypto_factor_score(self, crypto_factors: Dict[str, Any], price_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate an explainable score from crypto market factors."""
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
                    add("funding_oi", 18, "Positive funding with rising open interest strengthens long momentum.")
                elif fr < 0 and oi > 3:
                    add("funding_oi", -18, "Negative funding with rising open interest strengthens short momentum.")
        except Exception:
            pass

        try:
            if exchange_netflow is not None:
                enf = float(exchange_netflow)
                if enf < 0:
                    add("exchange_netflow", 16, "Exchange net outflow indicates reduced immediate sell-side supply.")
                elif enf > 0:
                    add("exchange_netflow", -16, "Exchange net inflow indicates higher potential sell pressure.")
        except Exception:
            pass

        try:
            if stablecoin_netflow is not None:
                stf = float(stablecoin_netflow)
                if stf > 0:
                    add("stablecoin_netflow", 12, "Stablecoin net inflow indicates stronger potential buying power.")
                elif stf < 0:
                    add("stablecoin_netflow", -12, "Stablecoin net outflow indicates weaker marginal buying power.")
        except Exception:
            pass

        try:
            if long_short_ratio is not None:
                lsr = float(long_short_ratio)
                if lsr > 1.6:
                    add("long_short_ratio", -10, "An overheated long-short ratio indicates crowded long positioning.")
                elif lsr < 0.75:
                    add("long_short_ratio", 8, "Deep short positioning creates potential for a short squeeze.")
        except Exception:
            pass

        try:
            if volume_change is not None and change_24h is not None:
                vol = float(volume_change)
                chg = float(change_24h)
                if vol > 15 and chg > 0:
                    add("volume_price", 10, "Rising price with strong volume confirms the trend.")
                elif vol > 15 and chg < 0:
                    add("volume_price", -10, "Falling price with strong volume confirms bearish control.")
                elif vol < -15 and abs(chg) > 3:
                    add("volume_price", -6 if chg > 0 else 6, "Price movement with declining volume weakens trend confidence.")
        except Exception:
            pass

        squeeze_risk = ((crypto_factors.get("signals") or {}).get("squeeze_risk") or "").lower()
        if squeeze_risk == "high":
            add("squeeze_risk", -8, "High derivatives crowding increases short-term volatility risk.")
        elif squeeze_risk == "medium":
            add("squeeze_risk", -3, "Rising derivatives crowding calls for tighter risk control.")

        summary = crypto_factors.get("summary") or ""
        return {
            "score": max(-100.0, min(100.0, score)),
            "breakdown": breakdown,
            "summary": summary,
        }

    def _calculate_sentiment_score(self, news: List[Dict]) -> float:
        """
        Calculate the news sentiment score with capped geopolitical penalties.

        """
        if not news:
            return 0.0

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
            relevance = str(item.get("asset_relevance") or "direct").lower()

            # Background headlines can be shown for context, but are excluded
            # from target-asset scoring until an upstream source establishes a
            # direct or material transmission channel.
            if relevance not in {"direct", "material"}:
                continue

            level, tag = geopolitical_match_level(text)
            if is_global_event and level == "none":
                level, tag = "moderate", "is_global_event"

            if level != "none":
                delta = geopolitical_sentiment_penalty_delta(level)
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
        Calculate the macro environment score from volatility, currency, and rates.

        """
        # The legacy formula models US risk assets, not domestic equity transmission.
        if not macro or market in {"CNStock", "HKStock"}:
            return 0.0

        score = 0.0
        factors = 0

        vix = macro.get("VIX", {})
        vix_value = vix.get("price", 0)
        if vix_value > 0:
            if vix_value > 35:
                vix_score = -50
            elif vix_value > 30:
                vix_score = -40
            elif vix_value > 25:
                vix_score = -30
            elif vix_value > 20:
                vix_score = -15
            elif vix_value < 12:
                vix_score = +20
            elif vix_value < 15:
                vix_score = +10
            else:
                vix_score = 0
            score += vix_score
            factors += 1

        dxy = macro.get("DXY", {})
        dxy_value = dxy.get("price", 0)
        dxy_change = dxy.get("changePercent", 0)
        if dxy_value > 0:
            if market in ["Crypto", "Forex", "Futures"]:
                if dxy_change > 2:
                    dxy_score = -30
                elif dxy_change > 1:
                    dxy_score = -20
                elif dxy_change < -2:
                    dxy_score = +30
                elif dxy_change < -1:
                    dxy_score = +20
                else:
                    dxy_score = 0
            else:
                if dxy_change > 2:
                    dxy_score = -10
                elif dxy_change < -2:
                    dxy_score = +10
                else:
                    dxy_score = 0
            score += dxy_score
            factors += 1

        tnx = macro.get("TNX", {})
        tnx_change = tnx.get("changePercent", 0)
        tnx_value = tnx.get("price", 0)
        if tnx_change != 0 or tnx_value > 0:
            if market in ["Crypto", "USStock"]:
                if tnx_change > 3:
                    tnx_score = -30
                elif tnx_change > 2:
                    tnx_score = -20
                elif tnx_change < -3:
                    tnx_score = +30
                elif tnx_change < -2:
                    tnx_score = +20
                else:
                    tnx_score = 0
            else:
                tnx_score = 0
            score += tnx_score
            factors += 1

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

        if factors > 0:
            max_possible = 125
            score = score / max_possible * 100

        return max(-100, min(100, score))
