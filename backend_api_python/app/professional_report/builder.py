"""Assemble the backward-compatible ProfessionalReportV1 payload."""

from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .features.equity import build_equity_features
from .features.cross_asset import build_forex_features, build_futures_features
from .narrative import normalize_report_text, validate_evidence_claims
from .providers import list_providers, provider_configuration_status
from .risk import build_risk_plan
from .snapshot import build_evidence_snapshot


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"cannot serialize {type(value)!r}")


def _score(value: Any, default: float = 50.0) -> float:
    try:
        return round(max(0.0, min(100.0, float(value))), 2)
    except (TypeError, ValueError):
        return default


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _chart_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OSError, OverflowError, ValueError):
            return ""
    return str(value or "").strip()


def _build_price_chart(collector_payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep a bounded copy of the real daily candles used by the analysis."""
    bars: list[dict[str, Any]] = []
    for item in list(collector_payload.get("kline") or [])[-60:]:
        if not isinstance(item, Mapping):
            continue
        timestamp = _chart_timestamp(item.get("timestamp") or item.get("time") or item.get("date"))
        open_price = _finite_float(item.get("open"))
        high = _finite_float(item.get("high"))
        low = _finite_float(item.get("low"))
        close = _finite_float(item.get("close"))
        if not timestamp or any(value is None or value <= 0 for value in (open_price, high, low, close)):
            continue
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            continue
        volume = _finite_float(item.get("volume"))
        bars.append({
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume if volume is not None and volume >= 0 else None,
        })
    if not bars:
        return None
    levels = (collector_payload.get("indicators") or {}).get("levels") or {}
    support = _finite_float(levels.get("support"))
    resistance = _finite_float(levels.get("resistance"))
    return {
        "timeframe": str(collector_payload.get("timeframe") or "1D"),
        "bars": bars,
        "moving_average_periods": [5, 10, 20],
        "support": support if support and support > 0 else None,
        "resistance": resistance if resistance and resistance > 0 else None,
    }


def _market_bias(analysis_result: Mapping[str, Any]) -> tuple[str, float]:
    """Keep technical market direction separate from the execution action."""
    objective = analysis_result.get("objective_score") or {}
    raw_value = objective.get("technical_score")
    if raw_value is None:
        displayed = _score((analysis_result.get("scores") or {}).get("technical"), 50.0)
        raw_value = (displayed - 50.0) * 2.0
    try:
        score = max(-100.0, min(100.0, float(raw_value)))
    except (TypeError, ValueError):
        score = 0.0
    if score >= 5.0:
        return "BULLISH", round(score, 2)
    if score <= -5.0:
        return "BEARISH", round(score, 2)
    return "NEUTRAL", round(score, 2)


def _refs(observations: list[Mapping[str, Any]], *categories: str) -> list[str]:
    wanted = set(categories)
    return [
        str(item["evidence_id"])
        for item in observations
        if item.get("category") in wanted and item.get("evidence_id")
    ]


def _quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    from .quality import assess_snapshot_quality

    try:
        result = assess_snapshot_quality(snapshot)
    except Exception:
        # The Pydantic contract is the canonical input when a caller wants to
        # instantiate it explicitly; accepting a plain mapping keeps legacy
        # fast-analysis integration lightweight.
        from .contracts import EvidenceSnapshotV1

        result = assess_snapshot_quality(EvidenceSnapshotV1.model_validate(snapshot))
    return _dump(result)


def _apply_gate(decision: str, confidence: int, quality: Mapping[str, Any]) -> tuple[str, int, list[str]]:
    from .quality import apply_quality_gate

    try:
        gated = apply_quality_gate(decision, confidence, quality)
    except Exception:
        from .contracts import DataQualitySummary

        gated = apply_quality_gate(decision, confidence, DataQualitySummary.model_validate(quality))
    if isinstance(gated, tuple) and len(gated) == 3:
        return str(gated[0]), int(gated[1]), list(gated[2])
    if isinstance(gated, Mapping):
        return (
            str(gated.get("decision") or decision),
            int(gated.get("confidence") or confidence),
            list(gated.get("reasons") or []),
        )
    raise TypeError("unexpected quality-gate result")


def _dimension(
    key: str,
    score: Any,
    observations: list[Mapping[str, Any]],
    categories: Iterable[str],
    narrative: str,
    *,
    missing: Iterable[str] = (),
    evidence_refs: Iterable[str] | None = None,
) -> dict[str, Any]:
    refs = list(dict.fromkeys(evidence_refs)) if evidence_refs is not None else _refs(observations, *categories)
    missing_items = list(missing)
    return {
        "key": key,
        "score": _score(score),
        "score_kind": "deterministic_signal_strength",
        "status": "available" if refs else "insufficient_data",
        "narrative": normalize_report_text(narrative or ""),
        "evidence_refs": refs,
        "missing_data": missing_items,
    }


def _metric_refs(observations: list[Mapping[str, Any]], *metric_prefixes: str) -> list[str]:
    prefixes = tuple(str(item) for item in metric_prefixes)
    return [
        str(item["evidence_id"])
        for item in observations
        if item.get("evidence_id")
        and any(str(item.get("metric") or "").startswith(prefix) for prefix in prefixes)
    ]


def _plain_number(value: Any, digits: int = 2) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return f"{number:,.{digits}f}"


def _guard_southbound_proxy_text(value: Any) -> str:
    """Prevent a holdings-change proxy from being presented as transaction flow."""
    text = str(value or "")
    parts = re.split(r"([。！？；.!?;])", text)
    guarded: list[str] = []
    for part in parts:
        if "南向" in part:
            part = part.replace("南向资金", "南向持股").replace("南向资本", "南向持股")
            part = part.replace("净流入", "增加（持仓变化代理）").replace("净买入", "增加（持仓变化代理）")
            part = part.replace("净流出", "减少（持仓变化代理）").replace("净卖出", "减少（持仓变化代理）")
        elif "southbound" in part.lower():
            part = re.sub(r"(?i)(?:capital|fund)\s+flow", "holdings", part)
            part = re.sub(r"(?i)net\s+(?:inflow|buying)", "increase (holdings-change proxy)", part)
            part = re.sub(r"(?i)net\s+(?:outflow|selling)", "decrease (holdings-change proxy)", part)
        guarded.append(part)
    return "".join(guarded)


def _apply_hk_proxy_guard(
    collector_payload: Mapping[str, Any], analysis_result: Mapping[str, Any]
) -> dict[str, Any]:
    southbound = collector_payload.get("southbound_flow") or {}
    if southbound.get("scope") != "stock_connect_holdings_change_proxy":
        return dict(analysis_result)
    guarded = dict(analysis_result)
    for key in ("summary",):
        guarded[key] = _guard_southbound_proxy_text(guarded.get(key))
    detailed = dict(guarded.get("detailed_analysis") or {})
    for key, value in detailed.items():
        detailed[key] = _guard_southbound_proxy_text(value)
    guarded["detailed_analysis"] = detailed
    for key in ("reasons", "risks"):
        guarded[key] = [_guard_southbound_proxy_text(item) for item in guarded.get(key) or []]
    guarded["evidence_claims"] = [
        {**claim, "text": _guard_southbound_proxy_text(claim.get("text"))}
        if isinstance(claim, Mapping) else claim
        for claim in guarded.get("evidence_claims") or []
    ]
    return guarded


def _macro_dimension_narrative(
    payload: Mapping[str, Any], language: str, market: str = ""
) -> str:
    macro = payload.get("macro") or {}
    zh = str(language or "").lower().startswith("zh")
    facts: list[str] = []
    vix = macro.get("VIX") or {}
    dxy = macro.get("DXY") or {}
    tnx = macro.get("TNX") or {}
    fear_greed = macro.get("FEAR_GREED") or {}
    hkma = macro.get("HKMA") or {}
    if (value := _plain_number(vix.get("price"))) is not None:
        facts.append((f"VIX为{value}" if zh else f"VIX is {value}"))
    if (value := _plain_number(dxy.get("price"))) is not None:
        facts.append((f"美元指数为{value}" if zh else f"DXY is {value}"))
    if (value := _plain_number(tnx.get("price"), 3)) is not None:
        facts.append((f"美国10年期收益率为{value}%" if zh else f"the US 10-year yield is {value}%"))
    if (value := _plain_number(fear_greed.get("price"), 0)) is not None:
        facts.append((f"风险情绪指数为{value}" if zh else f"the risk-sentiment index is {value}"))
    if (value := _plain_number(hkma.get("one_month_pct"), 3)) is not None:
        facts.append((f"1个月HIBOR为{value}%" if zh else f"one-month HIBOR is {value}%"))
    if not facts:
        return (
            "本次宏观数据未在采集时限内返回，因此该维度不参与方向判断。"
            if zh else
            "Macro observations did not return within the collection deadline, so this dimension is excluded from the directional view."
        )
    joined = "、".join(facts) if zh else ", ".join(facts)
    market_context = {
        "HKStock": ("全球及香港资金环境", "global and Hong Kong funding conditions"),
        "USStock": ("全球及美国风险资产环境", "global and US risk-asset conditions"),
        "Crypto": ("全球流动性及加密风险偏好环境", "global liquidity and crypto risk-sentiment conditions"),
    }.get(market, ("全球风险资产环境", "global risk-asset conditions"))
    if zh:
        return f"可用宏观证据显示：{joined}。这些指标仅代表{market_context[0]}背景；若缺少明确的标的传导机制，不应单独作为买卖依据。"
    return f"Available macro evidence shows {joined}. These observations describe {market_context[1]} and are not a standalone trade signal without an asset-specific transmission mechanism."


def _hk_market_dimension_narrative(
    features: Mapping[str, Any],
    language: str,
) -> str:
    zh = str(language or "").lower().startswith("zh")
    market_specific = features.get("market_specific") or {}
    facts: list[str] = []
    southbound = market_specific.get("southbound_flow") or {}
    analysts = market_specific.get("analyst_expectations") or {}
    short_selling = market_specific.get("short_selling") or {}
    ccass = market_specific.get("ccass") or {}
    ah_premium = market_specific.get("ah_premium") or {}
    announcements = market_specific.get("hkex_announcements") or []

    if (change := _plain_number(southbound.get("holding_change_pct_1d"))) is not None:
        facts.append(
            f"南向持股一日变化{change}%（持仓变化代理，并非净买入额）"
            if zh else
            f"one-day southbound holdings changed {change}% (a holdings proxy, not net purchase value)"
        )
    if analysts:
        rating = str(analysts.get("rating_direction") or "").strip()
        target = _plain_number(analysts.get("target_price_median_hkd"))
        if rating or target:
            if zh:
                facts.append(f"券商共识{rating or '未评级'}" + (f"、目标价中位数{target}港元" if target else ""))
            else:
                facts.append(f"broker consensus is {rating or 'unrated'}" + (f" with a median target of HKD {target}" if target else ""))
    if short_selling and (ratio := _plain_number(short_selling.get("turnover_ratio_pct"))) is not None:
        facts.append(f"卖空成交占比{ratio}%" if zh else f"short turnover is {ratio}%")
    if ccass and (concentration := _plain_number(ccass.get("top_participant_concentration_pct"))) is not None:
        facts.append(f"CCASS头部集中度{concentration}%" if zh else f"top CCASS concentration is {concentration}%")
    if ah_premium and (premium := _plain_number(ah_premium.get("premium_pct"))) is not None:
        facts.append(f"A/H溢价{premium}%" if zh else f"the A/H premium is {premium}%")
    if announcements:
        facts.append(f"取得{len(announcements)}条港交所公告" if zh else f"{len(announcements)} HKEX announcements were retrieved")

    missing = list(features.get("missing_capabilities") or [])
    if not facts:
        return (
            "本次未取得可验证的港股特有证据，相关缺口已单独列示，不会用中性分数代替缺失数据。"
            if zh else
            "No verifiable Hong Kong-specific evidence was retrieved. The gaps are listed explicitly rather than replaced with a neutral score."
        )
    joined = "；".join(facts) if zh else "; ".join(facts)
    if missing:
        return (
            f"已取得的港股特有证据包括：{joined}。仍有{len(missing)}项能力缺失，因此只能作为辅助证据。"
            if zh else
            f"Available Hong Kong-specific evidence includes: {joined}. {len(missing)} capabilities remain missing, so this dimension is supporting evidence only."
        )
    return (f"港股特有证据包括：{joined}。" if zh else f"Hong Kong-specific evidence includes: {joined}.")


def _us_market_dimension_narrative(features: Mapping[str, Any], language: str) -> str:
    zh = str(language or "").lower().startswith("zh")
    market_specific = features.get("market_specific") or {}
    facts: list[str] = []
    filings = market_specific.get("filings") or []
    expectations = market_specific.get("analyst_expectations") or {}
    options = market_specific.get("options") or {}
    short_interest = market_specific.get("short_interest") or {}
    insider = market_specific.get("insider_activity") or {}
    if filings:
        latest = filings[0] if isinstance(filings[0], Mapping) else {}
        form = latest.get("form")
        date = latest.get("filing_date")
        detail = " ".join(str(item) for item in (form, date) if item)
        facts.append(
            (f"{len(filings)}项SEC披露（最新{detail}）" if detail else f"{len(filings)}项SEC披露")
            if zh else
            (f"{len(filings)} SEC filings (latest {detail})" if detail else f"{len(filings)} SEC filings")
        )
    if expectations:
        rating = expectations.get("rating_direction")
        target = expectations.get("target_price_median_usd") or expectations.get("target_price_mean_usd")
        count = expectations.get("analyst_count")
        detail = []
        if rating:
            detail.append(str(rating))
        if target is not None:
            detail.append((f"目标价{float(target):.2f}美元" if zh else f"target USD {float(target):.2f}"))
        if count is not None:
            detail.append((f"{int(float(count))}位分析师" if zh else f"{int(float(count))} analysts"))
        facts.append(("分析师一致预期" if zh else "analyst consensus") + (f"（{'，'.join(detail)}）" if zh and detail else f" ({', '.join(detail)})" if detail else ""))
    if options:
        expiry = options.get("expiry")
        ratio = options.get("put_call_open_interest_ratio") or options.get("put_call_volume_ratio")
        iv = options.get("nearest_atm_implied_volatility_pct")
        detail = []
        if expiry:
            detail.append((f"到期日{expiry}" if zh else f"expiry {expiry}"))
        if ratio is not None:
            detail.append((f"Put/Call比{float(ratio):.2f}" if zh else f"put/call {float(ratio):.2f}"))
        if iv is not None:
            detail.append((f"近ATM IV {float(iv):.1f}%" if zh else f"near-ATM IV {float(iv):.1f}%"))
        facts.append(("最近到期期权快照" if zh else "nearest-expiry options snapshot") + (f"（{'，'.join(detail)}）" if zh and detail else f" ({', '.join(detail)})" if detail else ""))
    if short_interest:
        short_float = short_interest.get("short_percent_of_float_pct")
        short_ratio = short_interest.get("short_ratio_days")
        detail = []
        if short_float is not None:
            detail.append((f"流通股做空比例{float(short_float):.2f}%" if zh else f"short float {float(short_float):.2f}%"))
        if short_ratio is not None:
            detail.append((f"回补天数{float(short_ratio):.2f}" if zh else f"days to cover {float(short_ratio):.2f}"))
        facts.append(("报告期空头仓位" if zh else "reported short interest") + (f"（{'，'.join(detail)}）" if zh and detail else f" ({', '.join(detail)})" if detail else ""))
    if insider:
        count = insider.get("recent_form4_filing_count")
        facts.append(
            (f"近期{int(count)}份Form 4申报（仅代表申报活动，不表示买卖方向）" if count is not None else "Form 4申报活动（不表示买卖方向）")
            if zh else
            (f"{int(count)} recent Form 4 filings (filing activity, not trade direction)" if count is not None else "Form 4 filing activity (not trade direction)")
        )
    missing = list(features.get("missing_capabilities") or [])
    if not facts:
        return (
            "本次未取得可验证的美股特有数据；监管披露、分析师预期、期权、空头及内部人数据不会以默认值替代。"
            if zh else
            "No verifiable US-market-specific evidence was collected; filings, estimates, options, short data and insider activity are not replaced with defaults."
        )
    joined = "、".join(facts) if zh else ", ".join(facts)
    suffix = (f"；仍缺少{len(missing)}类数据。" if zh else f"; {len(missing)} data categories remain missing.") if missing else "。" if zh else "."
    return (f"当前覆盖{joined}{suffix}" if zh else f"Current coverage includes {joined}{suffix}")


def _market_dimension_narrative(market: str, features: Mapping[str, Any], language: str) -> str:
    if market == "HKStock":
        return _hk_market_dimension_narrative(features, language)
    return _us_market_dimension_narrative(features, language)


def _claim_refs(text: str, observations: list[Mapping[str, Any]]) -> list[str]:
    low = str(text or "").lower()
    if any(token in low for token in ("pe", "roe", "营收", "利润", "估值", "财务", "cash flow", "revenue")):
        categories = ("fundamental",)
    elif any(token in low for token in ("新闻", "事件", "政策", "宏观", "vix", "dxy", "news", "rate")):
        categories = ("news", "macro")
    elif any(token in low for token in ("funding", "资金费率", "持仓量", "oi", "清算", "链上", "netflow")):
        categories = ("crypto",)
    else:
        categories = ("technical", "market")
    refs = _refs(observations, *categories)
    return refs[:8]


def _build_claims(items: Iterable[Any], kind: str, observations: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    claims = []
    for item in items or []:
        text = normalize_report_text(str(item or ""))
        if not text:
            continue
        refs = _claim_refs(text, observations)
        if not refs:
            continue
        claims.append({"kind": kind, "text": text, "evidence_refs": refs})
    return claims


def _validated_llm_claims(
    items: Iterable[Any], observations: list[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Retain only explicitly grounded model claims at the report boundary."""
    available = {
        str(item.get("evidence_id"))
        for item in observations
        if item.get("evidence_id")
    }
    claims: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        text = normalize_report_text(str(item.get("text") or ""))
        refs = list(dict.fromkeys(
            str(ref) for ref in item.get("evidence_refs") or [] if str(ref) in available
        ))
        if text and refs:
            claims.append({
                "kind": str(item.get("kind") or "thesis"),
                "text": text,
                "evidence_refs": refs,
            })
    return claims


def _warning_codes(items: Iterable[Any]) -> list[str]:
    codes: list[str] = []
    for item in items or []:
        if isinstance(item, Mapping):
            value = item.get("code") or item.get("message")
        else:
            value = item
        text = str(value or "").strip()
        if text:
            codes.append(text)
    return codes


def _build_scenarios(
    decision: str,
    current_price: float,
    trading_plan: Mapping[str, Any],
    market_data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    stop = trading_plan.get("stop_loss") or trading_plan.get("stopLoss")
    target = trading_plan.get("take_profit") or trading_plan.get("takeProfit")
    support = market_data.get("support")
    resistance = market_data.get("resistance")
    if decision == "SELL":
        bull_target, bear_target = stop or resistance, target or support
        base_target = support or current_price
    elif decision == "BUY":
        bull_target, bear_target = target or resistance, stop or support
        base_target = resistance or current_price
    else:
        bull_target, bear_target = resistance, support
        base_target = current_price
    return [
        {
            "case": "bull",
            "probability": None,
            "target_price": bull_target,
            "trigger": "Price and evidence confirm the upside thesis.",
            "invalidation": bear_target,
        },
        {
            "case": "base",
            "probability": None,
            "target_price": base_target,
            "trigger": "Current evidence remains mixed or follows the central path.",
            "invalidation": None,
        },
        {
            "case": "bear",
            "probability": None,
            "target_price": bear_target,
            "trigger": "Downside catalyst or technical breakdown is confirmed.",
            "invalidation": bull_target,
        },
    ]


def _provider_payload(market: str) -> dict[str, Any]:
    rows = []
    for provider in list_providers(market=market):
        status = provider_configuration_status(provider, environ=os.environ)
        rows.append({
            "key": provider.key,
            "name": provider.name,
            "tier": provider.tier,
            "capabilities": sorted(provider.capabilities),
            "configured": bool(status["configured"]),
            "keyless": provider.keyless,
            "cost_level": provider.cost_level,
            "license_warning": provider.license_warning,
            "missing_env_keys": list(status["missing_env_keys"]),
            "integration_status": provider.integration_status,
        })
    return {
        "community": [row for row in rows if row["tier"] == "community"],
        "professional": [row for row in rows if row["tier"] == "professional"],
    }


def _effective_data_tier(
    requested_tier: str, observations: list[Mapping[str, Any]]
) -> tuple[str, list[str]]:
    professional_tokens = {
        provider.key.replace("_", " ")
        for provider in list_providers(tier="professional")
    } | {"polygon", "coin metrics", "hkex omd", "hkex data marketplace"}
    observed_sources = " ".join(
        str(item.get("source") or "").lower().replace("_", " ")
        for item in observations
    )
    has_professional_evidence = any(
        token and token in observed_sources for token in professional_tokens
    )
    effective = "professional" if has_professional_evidence else "community"
    warnings = []
    if requested_tier == "professional" and effective != "professional":
        warnings.append("professional_tier_requested_but_no_professional_evidence")
    return effective, warnings


def _typed_crypto_factors(
    factors: Mapping[str, Any], observations: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """Attach the collector's explicit unit/scope metadata to legacy scalars."""
    metadata = factors.get("metric_metadata") or {}
    ref_by_metric = {
        str(item.get("metric") or "").removeprefix("crypto."): item.get("evidence_id")
        for item in observations
        if str(item.get("metric") or "").startswith("crypto.")
    }
    result: dict[str, Any] = {}
    for key in (
        "funding_rate",
        "open_interest",
        "basis",
        "liquidations",
        "long_short_ratio",
        "market_cap",
        "volume_24h",
        "turnover_24h",
    ):
        value = factors.get(key)
        if value is None:
            continue
        meta = metadata.get(key) or {}
        result[key] = {
            "value": value,
            "unit": meta.get("unit"),
            "currency": meta.get("currency"),
            "venue": meta.get("venue"),
            "product_type": meta.get("product_type"),
            "evidence_ref": ref_by_metric.get(key),
        }
    return result


def build_professional_report(
    collector_payload: Mapping[str, Any],
    analysis_result: Mapping[str, Any],
    *,
    data_tier: str = "community",
    account_risk_budget_pct: float = 1.0,
) -> dict[str, Any]:
    """Build an evidence-first report while preserving the legacy response.

    ``confidence`` is deliberately labelled as model strength.  It must not be
    presented as a calibrated probability until the outcome-calibration layer
    has sufficient point-in-time samples.
    """
    if data_tier not in {"community", "professional"}:
        raise ValueError("data_tier must be community or professional")
    analysis_result = _apply_hk_proxy_guard(collector_payload, analysis_result)
    snapshot = build_evidence_snapshot(collector_payload)
    observations = snapshot["observations"]
    effective_data_tier, tier_warnings = _effective_data_tier(data_tier, observations)
    quality = _quality(snapshot)
    market = str(collector_payload.get("market") or analysis_result.get("market") or "")
    prebuilt_market_features: dict[str, Any] | None = None
    hard_gate_reasons: list[str] = []
    if market in {"USStock", "HKStock"}:
        prebuilt_market_features = build_equity_features(market, collector_payload, observations)
        if (prebuilt_market_features.get("identity") or {}).get("verified") is False:
            hard_gate_reasons.append("instrument_identity_unverified")
    elif market == "Crypto":
        from .features.crypto import build_crypto_features

        legacy_crypto_factors = collector_payload.get("crypto_factors") or {}
        prebuilt_market_features = build_crypto_features({
            "crypto_factors": _typed_crypto_factors(legacy_crypto_factors, observations),
            "instrument": snapshot["instrument"],
        })
        if not bool(
            (prebuilt_market_features.get("quality_flags") or {}).get(
                "usable_for_directional_analysis"
            )
        ):
            hard_gate_reasons.append("crypto_scope_or_unit_validation_failed")
    elif market == "Forex":
        prebuilt_market_features = build_forex_features(collector_payload, observations)
    elif market == "Futures":
        prebuilt_market_features = build_futures_features(collector_payload, observations)
    raw_decision = str(analysis_result.get("decision") or "HOLD").upper()
    market_bias, market_bias_score = _market_bias(analysis_result)
    raw_confidence = int(_score(analysis_result.get("confidence"), 50))
    decision, confidence, gate_reasons = _apply_gate(raw_decision, raw_confidence, quality)
    if hard_gate_reasons:
        if decision != "HOLD":
            decision = "HOLD"
            gate_reasons.append("directional_decision_blocked")
        confidence = min(confidence, 35)
        gate_reasons.extend(hard_gate_reasons)
    gate_reasons = list(dict.fromkeys(gate_reasons))
    market_data = analysis_result.get("market_data") or {}
    current_price = float(market_data.get("current_price") or (collector_payload.get("price") or {}).get("price") or 0)
    trading_plan = analysis_result.get("trading_plan") or {}
    risk_plan = build_risk_plan(
        decision,
        current_price,
        trading_plan,
        data_quality_score=float(quality.get("overall_score") or quality.get("score") or 0),
        market=market,
        account_risk_budget_pct=account_risk_budget_pct,
        market_bias=market_bias,
        indicators=collector_payload.get("indicators") or {},
    )
    if decision != "HOLD" and not risk_plan.get("valid", False):
        decision = "HOLD"
        confidence = min(confidence, 35)
        gate_reasons = list(dict.fromkeys(
            gate_reasons + ["directional_decision_blocked", "invalid_risk_plan"]
        ))

    detailed = analysis_result.get("detailed_analysis") or {}
    scores = analysis_result.get("scores") or {}
    objective = analysis_result.get("objective_score") or {}
    report_language = str(analysis_result.get("language") or "en-US")
    dimensions = [
        _dimension("technical", scores.get("technical"), observations, ("technical", "market"), detailed.get("technical", "")),
        _dimension("fundamental", scores.get("fundamental"), observations, ("fundamental",), detailed.get("fundamental", "")),
        _dimension("news_sentiment", scores.get("sentiment"), observations, ("news",), detailed.get("sentiment", "")),
        _dimension(
            "macro",
            50.0 + float(objective.get("macro_score") or 0.0) * 0.5,
            observations,
            ("macro",),
            _macro_dimension_narrative(collector_payload, report_language, market),
        ),
    ]

    if market in {"USStock", "HKStock"}:
        market_features = prebuilt_market_features or build_equity_features(
            market, collector_payload, observations
        )
        market_missing = market_features.get("missing_capabilities") or []
        market_metric_prefixes = (
            (
                "filings.hkex", "southbound_flow.snapshot", "short_selling.snapshot",
                "ccass.snapshot", "ah_premium.snapshot", "expectations.analyst",
            )
            if market == "HKStock" else
            (
                "filings.sec", "expectations.analyst", "options.snapshot",
                "short_interest.snapshot", "short_volume.snapshot", "insider_activity.snapshot",
                "ownership.snapshot", "earnings.transcript",
            )
        )
        market_dimension = _dimension(
            "market_specific",
            0 if market_missing else 50,
            observations,
            (),
            _market_dimension_narrative(market, market_features, report_language),
            missing=market_missing,
            evidence_refs=_metric_refs(observations, *market_metric_prefixes),
        )
        if market_missing:
            market_dimension["status"] = "insufficient_data"
        dimensions.append(market_dimension)
    elif market == "Crypto":
        market_features = prebuilt_market_features or {}
        crypto_quality = market_features.get("quality_flags") or {}
        crypto_usable = bool(crypto_quality.get("usable_for_directional_analysis"))
        crypto_dimension = _dimension(
            "crypto_market_structure",
            (50.0 + float(analysis_result.get("crypto_factor_score") or 0.0) * 0.5)
            if crypto_usable else 0,
            observations,
            ("crypto",),
            analysis_result.get("crypto_factor_summary") or "",
            missing=[
                item.get("code")
                for item in market_features.get("warnings") or []
                if isinstance(item, Mapping) and str(item.get("code") or "").startswith("MISSING")
            ],
        )
        if not crypto_usable:
            crypto_dimension["status"] = "insufficient_data"
        dimensions.append(crypto_dimension)
    elif market in {"Forex", "Futures"}:
        market_features = prebuilt_market_features or (
            build_forex_features(collector_payload, observations)
            if market == "Forex" else
            build_futures_features(collector_payload, observations)
        )
        missing = market_features.get("missing_capabilities") or []
        dimension_key = "fx_relative_value" if market == "Forex" else "futures_curve_positioning"
        dimension = _dimension(
            dimension_key,
            0 if missing else 50,
            observations,
            ("macro", "positioning", "market"),
            "",
            missing=missing,
            evidence_refs=market_features.get("evidence_refs") or [],
        )
        if missing:
            dimension["status"] = "insufficient_data"
        dimensions.append(dimension)
    else:
        market_features = {"market": market, "warnings": ["unsupported_professional_report_market"]}

    claims = _validated_llm_claims(analysis_result.get("evidence_claims") or [], observations)
    if not claims:
        claims = _build_claims(analysis_result.get("reasons") or [], "thesis", observations)
        claims.extend(_build_claims(analysis_result.get("risks") or [], "risk", observations))
    claim_errors = validate_evidence_claims(claims, observations)
    warnings = sorted(set(
        _warning_codes(quality.get("warnings") or [])
        + list(gate_reasons)
        + _warning_codes(market_features.get("warnings") or [])
        + _warning_codes((analysis_result.get("llm_contract") or {}).get("warnings") or [])
        + tier_warnings
        + claim_errors
    ))
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    from app.research import build_research_intelligence

    scenarios = _build_scenarios(decision, current_price, trading_plan, market_data)
    research_intelligence = build_research_intelligence(
        collector_payload,
        quality=quality,
        summary=normalize_report_text(analysis_result.get("summary") or ""),
        claims=claims,
        scenarios=scenarios,
        risk_plan=risk_plan,
    )
    report = {
        "schema_version": "professional_report_v1",
        "report_id": f"{market}:{snapshot['instrument']['canonical_symbol']}:{generated_at}",
        "generated_at": generated_at,
        "as_of": snapshot["as_of"],
        "language": analysis_result.get("language") or "en-US",
        "data_tier": effective_data_tier,
        "instrument": snapshot["instrument"],
        "decision_profile": {
            "decision": decision,
            "raw_decision": raw_decision,
            "market_bias": market_bias,
            "market_bias_score": market_bias_score,
            "market_bias_basis": "technical_score",
            "confidence": confidence,
            "raw_confidence": raw_confidence,
            "confidence_kind": "calibrated_probability"
            if os.getenv("ENABLE_CONFIDENCE_CALIBRATION", "false").lower() == "true"
            else "model_strength",
            "quality_gate_reasons": gate_reasons,
            "conclusion_strength": quality.get("max_conclusion_strength") or "none",
            "rationale": normalize_report_text(analysis_result.get("summary") or ""),
            "horizon": analysis_result.get("timeframe"),
            "score": float((analysis_result.get("consensus") or {}).get("consensus_score") or 0.0),
            "evidence_ids": _refs(observations, "market", "technical")[:12],
        },
        "executive_summary": normalize_report_text(analysis_result.get("summary") or ""),
        "dimensions": dimensions,
        "claims": claims,
        "scenarios": scenarios,
        "risk_plan": risk_plan,
        "price_chart": _build_price_chart(collector_payload),
        "data_quality": quality,
        "market_features": market_features,
        "research_intelligence": research_intelligence,
        "evidence_snapshot": snapshot,
        "provider_options": _provider_payload(market) if market in {"USStock", "HKStock", "Crypto", "Forex", "Futures"} else {},
        "warnings": warnings,
        "methodology": {
            "scoring_version": analysis_result.get("score_source") or "deterministic_objective_v2",
            "report_builder_version": "professional_report_builder_v1.1",
            "llm_role": "evidence_explanation_only",
            "probabilities_calibrated": os.getenv("ENABLE_CONFIDENCE_CALIBRATION", "false").lower() == "true",
            "requested_data_tier": data_tier,
            "effective_data_tier": effective_data_tier,
        },
        "model_version": analysis_result.get("model"),
        "prompt_version": "professional_analysis_prompt_v1",
        "scoring_version": analysis_result.get("score_source") or "deterministic_objective_v2",
    }
    # Validate when the strict contract is available. Returning ``model_dump``
    # keeps the Flask response JSON-compatible and avoids leaking model objects.
    try:
        from .contracts import ProfessionalReportV1

        report = ProfessionalReportV1.model_validate(report).model_dump(mode="json")
    except Exception as exc:
        # Contract mismatches are visible in the payload and logs rather than
        # silently dropping the new report during the compatibility rollout.
        report["contract_validation"] = {"valid": False, "error": str(exc)}
    else:
        report["contract_validation"] = {"valid": True, "error": None}
    return normalize_report_text(report)


__all__ = ["build_professional_report"]
