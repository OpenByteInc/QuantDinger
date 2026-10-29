"""PDF rendering for AI Copilot research reports."""

import json
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


_EVIDENCE_METADATA_FIELDS = {
    "currency", "source", "period_end", "period_start", "latest_date", "date",
    "period_type", "timestamp", "time", "method", "kind", "name", "description",
    "category", "scope", "symbol", "security_type", "isin",
}
_EVIDENCE_PERCENT_FIELDS = {
    "change_percent", "revenue_growth", "profit_margin", "operating_margin",
    "gross_margin", "dividend_yield", "roe", "holding_ratio_pct",
    "holding_change_pct_1d", "short_percent_of_float_pct",
    "nearest_atm_implied_volatility_pct", "open_interest_change_24h",
    "volume_change_24h", "funding_rate",
}
_EVIDENCE_MONEY_FIELDS = {
    "market_cap", "enterprise_value", "total_revenue", "revenue", "gross_profit",
    "operating_income", "net_income", "operating_cash_flow", "financing_cash_flow",
    "capital_expenditure", "free_cash_flow", "total_assets", "total_liabilities",
    "total_equity", "current_assets", "current_liabilities", "cash", "debt",
    "volume_24h", "open_interest", "exchange_netflow", "stablecoin_netflow",
    "holding_market_value_hkd",
}
_EVIDENCE_PRICE_FIELDS = {
    "price", "current_price", "previous_close", "open", "high", "low", "close",
    "support", "resistance", "swing_low", "swing_high", "pivot", "bb_lower",
    "bb_middle", "bb_upper", "suggested_stop_loss", "suggested_take_profit",
    "target_price_median_hkd", "target_price_median_usd", "target_price_mean_usd",
    "target_price_low_usd", "target_price_high_usd", "target_price_low_hkd",
    "target_price_high_hkd",
}
_EVIDENCE_SHARE_FIELDS = {
    "shares_outstanding", "shares_short", "shares_short_prior_month",
    "holding_change_shares_1d", "holding_shares",
}
_EVIDENCE_CURRENCY_CODES = {
    "USD", "HKD", "CNY", "CNH", "EUR", "GBP", "JPY", "AUD", "CAD",
    "SGD", "KRW", "USDT", "USDC", "BTC", "ETH",
}


def _evidence_token(value: Any) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
    return re.sub(r"[^a-z0-9%]+", "_", text.lower()).strip("_")


def _evidence_metric_leaf(metric: Any) -> str:
    return _evidence_token(str(metric or "").split(".")[-1])


def _trimmed_number(value: float, *, maximum: int = 8, minimum: int = 0) -> str:
    rendered = f"{value:,.{maximum}f}"
    if maximum:
        rendered = rendered.rstrip("0").rstrip(".")
    if minimum:
        integer, dot, decimal = rendered.partition(".")
        rendered = f"{integer}.{decimal.ljust(minimum, '0')}" if dot else f"{integer}.{'0' * minimum}"
    return rendered


def _compact_evidence_number(value: float) -> str:
    absolute = abs(value)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if absolute >= threshold:
            return f"{_trimmed_number(value / threshold, maximum=2)}{suffix}"
    maximum = 8 if absolute and absolute < 0.01 else 6 if absolute < 1 else 4 if absolute < 100 else 2
    return _trimmed_number(value, maximum=maximum)


def _evidence_currency(item: dict) -> str:
    currency = str(item.get("currency") or "").strip().upper()
    if currency:
        return currency
    raw_unit = str(item.get("unit") or "").strip().upper()
    return raw_unit if raw_unit in _EVIDENCE_CURRENCY_CODES else ""


def _format_evidence_observation(item: dict, *, is_zh: bool = False) -> str:
    """Human-readable evidence value; the report artifact keeps the exact raw value."""
    value = item.get("value")
    if value in (None, ""):
        return "—"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return ("是" if value else "否") if is_zh else ("Yes" if value else "No")
    try:
        number = float(value)
    except (TypeError, ValueError):
        # Textual metadata such as dates, provider names and currency codes must
        # never inherit the parent financial statement's currency.
        return str(value)
    if not (number == number and abs(number) != float("inf")):
        return str(value)

    leaf = _evidence_metric_leaf(item.get("metric"))
    unit = _evidence_token(item.get("unit"))
    currency = _evidence_currency(item)
    exact = _trimmed_number(number)
    if leaf in _EVIDENCE_METADATA_FIELDS:
        return exact
    if re.search(r"(^|\.)rsi(\.|$)", str(item.get("metric") or ""), re.IGNORECASE):
        return _compact_evidence_number(number) if abs(number) >= 10000 else exact
    if unit in {"percent", "percentage", "pct", "%"} or leaf in _EVIDENCE_PERCENT_FIELDS or leaf.endswith("_pct"):
        absolute = abs(number)
        maximum = 6 if absolute and absolute < 0.01 else 4 if absolute < 1 else 2
        return f"{_trimmed_number(number, maximum=maximum)}%"
    if unit in {"bps", "basis_points"}:
        return f"{_trimmed_number(number, maximum=2)} {'个基点' if is_zh else 'bps'}"
    if unit == "currency_per_share" or leaf in {"eps", "book_value"}:
        maximum = 6 if abs(number) < 0.01 else 3
        suffix = f"{currency}/{'股' if is_zh else 'share'}" if currency else ("股" if is_zh else "share")
        return f"{_trimmed_number(number, maximum=maximum, minimum=2 if abs(number) >= 1 else 0)} {suffix}"
    if unit in {"multiple", "ratio"} or leaf.endswith("_ratio") or leaf in {"pe_ratio", "pb_ratio", "current_ratio", "quick_ratio", "debt_to_equity", "beta"}:
        return f"{_trimmed_number(number, maximum=3)}×"
    if unit in {"share", "shares"} or leaf in _EVIDENCE_SHARE_FIELDS:
        return f"{_compact_evidence_number(number)} {'股' if is_zh else 'shares'}"
    if unit in {"count", "items"} or leaf.endswith("_count") or leaf == "bar_count":
        count = _compact_evidence_number(number) if abs(number) >= 10000 else _trimmed_number(number, maximum=0)
        return f"{count} {'项' if is_zh else 'items'}"

    unit_is_currency = bool(currency) and unit == currency.lower()
    is_price = leaf in _EVIDENCE_PRICE_FIELDS
    is_money = leaf in _EVIDENCE_MONEY_FIELDS or unit_is_currency or unit == "currency"
    if is_price or is_money:
        if is_money and not is_price and abs(number) >= 1000:
            display = _compact_evidence_number(number)
        elif is_price:
            display = _trimmed_number(number, maximum=6 if abs(number) < 1 else 4, minimum=2)
        else:
            display = _compact_evidence_number(number)
        raw_unit = str(item.get("unit") or "").strip()
        amount_unit = raw_unit if not currency and unit not in {"", "currency", "price", "decimal"} else ""
        suffix = f" {currency}" if currency else f" {amount_unit}" if amount_unit else ""
        return f"{display}{suffix}"
    return _compact_evidence_number(number) if abs(number) >= 10000 else exact


def _format_evidence_provider(value: Any) -> str:
    tokens = list(dict.fromkeys(_evidence_token(item) for item in str(value or "").split("+") if _evidence_token(item)))
    if "yfinance_statements" in tokens:
        tokens = [item for item in tokens if item != "yfinance"]
    labels = {"yfinance": "Yahoo Finance", "yfinance_statements": "Yahoo Finance statements", "finnhub": "Finnhub"}
    return " + ".join(labels.get(item, item.replace("_", " ")) for item in tokens) or "—"

def _has_cjk_text(value: Any) -> bool:
    text = _plain_text(value)
    return bool(re.search(r"[\u2e80-\u9fff\uac00-\ud7af\u3040-\u30ff]", text))


def _professional_report_artifact(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    candidate = value.get("report") or value.get("professional_report") or value
    if not isinstance(candidate, dict):
        return None
    schema = candidate.get("schema_version")
    if schema == "professional_report_v1" or (
        schema == "1.0" and "instrument" in candidate and "decision_profile" in candidate
    ):
        return candidate
    return None


def _report_market_bias(decision: dict, dimensions: list[dict]) -> str:
    explicit = str(decision.get("market_bias") or "").upper()
    if explicit in {"BULLISH", "BEARISH", "NEUTRAL"}:
        return explicit
    raw_score = decision.get("market_bias_score")
    if raw_score is not None:
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0
    else:
        technical = next((item for item in dimensions if item.get("key") == "technical"), {})
        try:
            score = (float(technical.get("score")) - 50.0) * 2.0
        except (TypeError, ValueError):
            score = 0.0
    if score >= 5.0:
        return "BULLISH"
    if score <= -5.0:
        return "BEARISH"
    return "NEUTRAL"


def _professional_pdf_projection(value: dict) -> dict:
    """Project the v1 artifact into the generic PDF layout without legacy input data."""
    report = _professional_report_artifact(value)
    if not report:
        return value
    instrument = report.get("instrument") or {}
    decision = report.get("decision_profile") or {}
    risk = report.get("risk_plan") or {}
    candidate = risk.get("candidate_setup") or {}
    displayed_plan = candidate or risk
    observations = ((report.get("evidence_snapshot") or {}).get("observations") or [])
    observation_by_metric = {
        str(item.get("metric")): item
        for item in observations
        if isinstance(item, dict) and item.get("metric")
    }
    quote = observation_by_metric.get("quote.price") or {}
    change = observation_by_metric.get("quote.changePercent") or {}
    dimensions = [item for item in (report.get("dimensions") or []) if isinstance(item, dict)]
    market_bias = _report_market_bias(decision, dimensions)
    claims = [item for item in (report.get("claims") or []) if isinstance(item, dict)]
    scenarios = [item for item in (report.get("scenarios") or []) if isinstance(item, dict)]
    warnings = list(risk.get("warnings") or [])
    return {
        "market": instrument.get("market"),
        "symbol": instrument.get("canonical_symbol") or instrument.get("symbol"),
        "decision": decision.get("decision") or "HOLD",
        "market_bias": market_bias,
        "market_bias_score": decision.get("market_bias_score"),
        "confidence": decision.get("confidence"),
        "summary": report.get("executive_summary") or decision.get("rationale"),
        "market_data": {
            "current_price": quote.get("value"),
            "change_24h": change.get("value"),
        },
        "trading_plan": {
            "entry_price": displayed_plan.get("entry_price"),
            "stop_loss": displayed_plan.get("stop_loss"),
            "take_profit": displayed_plan.get("take_profit"),
            "risk_reward_ratio": displayed_plan.get("net_risk_reward"),
            "candidate_setup": bool(candidate),
            "rr_warning": any(
                item in {"net_risk_reward_below_one", "candidate_net_risk_reward_below_one"}
                for item in [*warnings, *(candidate.get("warnings") or [])]
            ),
        },
        "scores": {item.get("key", "dimension"): item.get("score") for item in dimensions},
        "detailed_analysis": {
            item.get("key", "dimension"): item.get("narrative")
            for item in dimensions
            if item.get("narrative")
        },
        "trend_outlook": {
            item.get("case", "scenario"): {
                "probability": item.get("probability"),
                "target_price": item.get("target_price"),
                "trigger": item.get("trigger") or item.get("triggers"),
                "invalidation": item.get("invalidation"),
            }
            for item in scenarios
        },
        "reasons": [item.get("text") for item in claims if item.get("kind") in {"thesis", "catalyst"}],
        "risks": [item.get("text") for item in claims if item.get("kind") in {"risk", "counter_argument"}],
        "indicators": {
            item.get("metric"): {
                "value": item.get("value"),
                "source": item.get("source"),
                "as_of": item.get("as_of"),
            }
            for item in observations[:30]
            if isinstance(item, dict) and item.get("metric")
        },
    }


def _register_report_pdf_font(language: str = "", prefer_cjk: bool = False) -> str:
    from pathlib import Path

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    language_key = _language_key(language)
    universal_candidates = [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    cjk_candidates = [
        ("C:/Windows/Fonts/msyh.ttc", True),
        ("C:/Windows/Fonts/msyh.ttf", True),
        ("C:/Windows/Fonts/simsun.ttc", True),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", True),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", True),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", True),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", True),
    ]
    script_candidates = {
        "ar": [
            ("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", False),
            ("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf", False),
            ("/System/Library/Fonts/GeezaPro.ttc", False),
        ],
        "th": [
            ("/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf", False),
            ("/System/Library/Fonts/ThonburiUI.ttc", False),
            ("/System/Library/Fonts/Supplemental/Thonburi.ttc", False),
        ],
    }
    candidates = (
        [(path, language_key in {"zh-CN", "zh-TW", "ja", "ko"}) for path in universal_candidates]
        + script_candidates.get(language_key, [])
        + (cjk_candidates if prefer_cjk else [])
        + [
            ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", False),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", False),
            ("C:/Windows/Fonts/arial.ttf", False),
        ]
    )
    font_name = f"QuantDingerSans_{re.sub(r'[^A-Za-z0-9]', '_', language_key)}"
    for path, is_cjk in candidates:
        if prefer_cjk and not is_cjk and path not in universal_candidates:
            continue
        try:
            if Path(path).exists():
                pdfmetrics.registerFont(TTFont(font_name, path))
                return font_name
        except Exception as e:
            logger.debug(f"Failed to register PDF font {path}: {e}")
    if prefer_cjk:
        try:
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont

            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            return "STSong-Light"
        except Exception as e:
            logger.debug(f"Failed to register built-in CJK PDF font: {e}")
    return "Helvetica"


def _language_key(language: str = "") -> str:
    lang = (language or "").replace("_", "-").lower()
    if lang.startswith("zh-tw") or lang.startswith("zh-hk") or lang.startswith("zh-hant"):
        return "zh-TW"
    if lang.startswith("zh"):
        return "zh-CN"
    for key in ("ja", "ko", "de", "fr", "ru", "ar", "th", "vi", "pt"):
        if lang.startswith(key):
            return key
    return "en"


def _outlook_labels(language: str = "") -> dict[str, str]:
    labels = {
        "en": {"BUY": "Buy", "SELL": "Sell", "HOLD": "Wait"},
        "zh-CN": {"BUY": "做多", "SELL": "做空", "HOLD": "观望"},
        "zh-TW": {"BUY": "做多", "SELL": "做空", "HOLD": "觀望"},
        "ja": {"BUY": "買い", "SELL": "売り", "HOLD": "様子見"},
        "ko": {"BUY": "매수", "SELL": "매도", "HOLD": "관망"},
        "de": {"BUY": "Kaufen", "SELL": "Verkaufen", "HOLD": "Abwarten"},
        "fr": {"BUY": "Acheter", "SELL": "Vendre", "HOLD": "Attendre"},
        "ru": {"BUY": "Покупать", "SELL": "Продавать", "HOLD": "Ждать"},
        "ar": {"BUY": "شراء", "SELL": "بيع", "HOLD": "انتظار"},
        "th": {"BUY": "ซื้อ", "SELL": "ขาย", "HOLD": "รอดู"},
        "vi": {"BUY": "Mua", "SELL": "Bán", "HOLD": "Chờ"},
        "pt": {"BUY": "Comprar", "SELL": "Vender", "HOLD": "Aguardar"},
    }
    return labels.get(_language_key(language), labels["en"])


def _market_bias_labels(language: str = "") -> dict[str, str]:
    labels = {
        "en": {"BULLISH": "Bullish", "BEARISH": "Bearish", "NEUTRAL": "Neutral"},
        "zh-CN": {"BULLISH": "偏多", "BEARISH": "偏空", "NEUTRAL": "中性"},
        "zh-TW": {"BULLISH": "偏多", "BEARISH": "偏空", "NEUTRAL": "中性"},
        "pt": {"BULLISH": "Altista", "BEARISH": "Baixista", "NEUTRAL": "Neutro"},
    }
    return labels.get(_language_key(language), labels["en"])


def _report_pdf_labels(language: str = "") -> dict[str, str]:
    labels = {
        "title": "QuantDinger AI Research Report",
        "subtitle": "AI-assisted market analysis for research use only",
        "target": "Target",
        "generated": "Generated",
        "decision": "Outlook",
        "confidence": "Confidence",
        "summary": "Executive Summary",
        "plan": "Trading Plan",
        "scores": "Model Scores",
        "trend": "Trend Outlook",
        "crypto": "Crypto Market Structure",
        "details": "Detailed Analysis",
        "reasons": "Key Reasons",
        "risks": "Risk Notes",
        "indicators": "Technical Indicators",
        "rr_warning": "Risk/reward warning",
        "rr_warning_text": "Potential reward is lower than the planned risk. The target was not stretched to hide this warning.",
        "disclaimer": "This report is generated by AI for research only and is not investment advice.",
    }
    overrides = {
        "zh-CN": {
            "title": "QuantDinger AI 研究报告",
            "subtitle": "AI 辅助市场分析，仅供研究参考",
            "target": "分析标的",
            "generated": "生成时间",
            "decision": "观点",
            "confidence": "置信度",
            "summary": "核心摘要",
            "plan": "交易计划",
            "scores": "模型评分",
            "trend": "趋势展望",
            "crypto": "加密市场结构",
            "details": "详细分析",
            "reasons": "核心理由",
            "risks": "风险提示",
            "indicators": "技术指标",
            "rr_warning": "风险收益警告",
            "rr_warning_text": "潜在收益低于计划风险；系统不会通过拉远止盈来隐藏这一警告。",
            "disclaimer": "本报告由 AI 生成，仅供研究参考，不构成投资建议。",
        },
        "zh-TW": {
            "title": "QuantDinger AI 研究報告",
            "subtitle": "AI 輔助市場分析，僅供研究參考",
            "target": "分析標的",
            "generated": "生成時間",
            "decision": "觀點",
            "confidence": "信心度",
            "summary": "核心摘要",
            "plan": "交易計畫",
            "scores": "模型評分",
            "trend": "趨勢展望",
            "crypto": "加密市場結構",
            "details": "詳細分析",
            "reasons": "核心理由",
            "risks": "風險提示",
            "indicators": "技術指標",
            "rr_warning": "風險收益警告",
            "rr_warning_text": "潛在收益低於計畫風險；系統不會透過拉遠止盈來隱藏此警告。",
            "disclaimer": "本報告由 AI 生成，僅供研究參考，不構成投資建議。",
        },
        "ja": {
            "title": "QuantDinger AI リサーチレポート",
            "subtitle": "AI による市場分析（調査目的のみ）",
            "target": "対象",
            "generated": "作成日時",
            "decision": "見通し",
            "confidence": "信頼度",
            "summary": "要約",
            "plan": "取引計画",
            "scores": "モデル評価",
            "trend": "トレンド見通し",
            "crypto": "暗号資産の市場構造",
            "details": "詳細分析",
            "reasons": "主な根拠",
            "risks": "リスク注意事項",
            "indicators": "テクニカル指標",
            "rr_warning": "リスクリワード警告",
            "rr_warning_text": "期待利益が計画損失を下回っています。この警告を隠すために利確目標を遠ざけることはありません。",
            "disclaimer": "本レポートは AI が調査目的で作成したもので、投資助言ではありません。",
        },
        "ko": {
            "title": "QuantDinger AI 리서치 보고서",
            "subtitle": "연구 목적의 AI 기반 시장 분석",
            "target": "분석 대상",
            "generated": "생성 시각",
            "decision": "전망",
            "confidence": "신뢰도",
            "summary": "핵심 요약",
            "plan": "거래 계획",
            "scores": "모델 점수",
            "trend": "추세 전망",
            "crypto": "암호화폐 시장 구조",
            "details": "상세 분석",
            "reasons": "주요 근거",
            "risks": "위험 참고사항",
            "indicators": "기술적 지표",
            "rr_warning": "손익비 경고",
            "rr_warning_text": "예상 수익이 계획된 위험보다 낮습니다. 이 경고를 감추기 위해 목표가를 임의로 늘리지 않습니다.",
            "disclaimer": "이 보고서는 AI가 연구 목적으로 생성했으며 투자 조언이 아닙니다.",
        },
        "de": {
            "title": "QuantDinger KI-Researchbericht",
            "subtitle": "KI-gestützte Marktanalyse nur zu Forschungszwecken",
            "target": "Instrument",
            "generated": "Erstellt",
            "decision": "Ausblick",
            "confidence": "Konfidenz",
            "summary": "Zusammenfassung",
            "plan": "Handelsplan",
            "scores": "Modellbewertungen",
            "trend": "Trendausblick",
            "crypto": "Kryptomarktstruktur",
            "details": "Detailanalyse",
            "reasons": "Hauptgründe",
            "risks": "Risikohinweise",
            "indicators": "Technische Indikatoren",
            "rr_warning": "Risiko-Rendite-Warnung",
            "rr_warning_text": "Die mögliche Rendite liegt unter dem geplanten Risiko. Das Kursziel wurde nicht künstlich erweitert, um diese Warnung zu verdecken.",
            "disclaimer": "Dieser Bericht wurde von KI zu Forschungszwecken erstellt und ist keine Anlageberatung.",
        },
        "fr": {
            "title": "Rapport de recherche IA QuantDinger",
            "subtitle": "Analyse de marché assistée par IA, à des fins de recherche uniquement",
            "target": "Actif analysé",
            "generated": "Généré le",
            "decision": "Perspective",
            "confidence": "Confiance",
            "summary": "Synthèse",
            "plan": "Plan de trading",
            "scores": "Scores du modèle",
            "trend": "Perspectives de tendance",
            "crypto": "Structure du marché crypto",
            "details": "Analyse détaillée",
            "reasons": "Principaux arguments",
            "risks": "Risques",
            "indicators": "Indicateurs techniques",
            "rr_warning": "Avertissement risque/rendement",
            "rr_warning_text": "Le gain potentiel est inférieur au risque prévu. L’objectif n’a pas été artificiellement éloigné pour masquer cet avertissement.",
            "disclaimer": "Ce rapport est généré par IA à des fins de recherche et ne constitue pas un conseil en investissement.",
        },
        "ru": {
            "title": "Аналитический отчёт QuantDinger AI",
            "subtitle": "Анализ рынка с помощью ИИ только для исследовательских целей",
            "target": "Инструмент",
            "generated": "Создан",
            "decision": "Прогноз",
            "confidence": "Уверенность",
            "summary": "Краткое резюме",
            "plan": "Торговый план",
            "scores": "Оценки модели",
            "trend": "Прогноз тренда",
            "crypto": "Структура крипторынка",
            "details": "Подробный анализ",
            "reasons": "Ключевые причины",
            "risks": "Риски",
            "indicators": "Технические индикаторы",
            "rr_warning": "Предупреждение о риске/доходности",
            "rr_warning_text": "Потенциальная доходность ниже планового риска. Цель не была искусственно отдалена, чтобы скрыть это предупреждение.",
            "disclaimer": "Этот отчёт создан ИИ только для исследований и не является инвестиционной рекомендацией.",
        },
        "ar": {
            "title": "تقرير أبحاث QuantDinger بالذكاء الاصطناعي",
            "subtitle": "تحليل للسوق بمساعدة الذكاء الاصطناعي لأغراض البحث فقط",
            "target": "الأصل محل التحليل",
            "generated": "تاريخ الإنشاء",
            "decision": "التوقعات",
            "confidence": "درجة الثقة",
            "summary": "الملخص التنفيذي",
            "plan": "خطة التداول",
            "scores": "درجات النموذج",
            "trend": "توقعات الاتجاه",
            "crypto": "هيكل سوق العملات الرقمية",
            "details": "التحليل التفصيلي",
            "reasons": "الأسباب الرئيسية",
            "risks": "ملاحظات المخاطر",
            "indicators": "المؤشرات الفنية",
            "rr_warning": "تحذير نسبة المخاطرة إلى العائد",
            "rr_warning_text": "العائد المحتمل أقل من المخاطرة المخطط لها. لم يتم إبعاد هدف الربح بشكل مصطنع لإخفاء هذا التحذير.",
            "disclaimer": "أُنشئ هذا التقرير بالذكاء الاصطناعي لأغراض البحث فقط ولا يُعد نصيحة استثمارية.",
        },
        "th": {
            "title": "รายงานวิจัย QuantDinger AI",
            "subtitle": "การวิเคราะห์ตลาดด้วย AI เพื่อการวิจัยเท่านั้น",
            "target": "สินทรัพย์ที่วิเคราะห์",
            "generated": "สร้างเมื่อ",
            "decision": "มุมมอง",
            "confidence": "ความเชื่อมั่น",
            "summary": "บทสรุป",
            "plan": "แผนการเทรด",
            "scores": "คะแนนโมเดล",
            "trend": "แนวโน้มตลาด",
            "crypto": "โครงสร้างตลาดคริปโท",
            "details": "การวิเคราะห์โดยละเอียด",
            "reasons": "เหตุผลสำคัญ",
            "risks": "ข้อควรระวังด้านความเสี่ยง",
            "indicators": "ตัวชี้วัดทางเทคนิค",
            "rr_warning": "คำเตือนอัตราความเสี่ยงต่อผลตอบแทน",
            "rr_warning_text": "ผลตอบแทนที่เป็นไปได้ต่ำกว่าความเสี่ยงตามแผน ระบบไม่ได้ขยายเป้าหมายกำไรเพื่อซ่อนคำเตือนนี้",
            "disclaimer": "รายงานนี้สร้างโดย AI เพื่อการวิจัยเท่านั้น ไม่ใช่คำแนะนำการลงทุน",
        },
        "vi": {
            "title": "Báo cáo nghiên cứu QuantDinger AI",
            "subtitle": "Phân tích thị trường có hỗ trợ của AI, chỉ dành cho mục đích nghiên cứu",
            "target": "Tài sản phân tích",
            "generated": "Thời gian tạo",
            "decision": "Nhận định",
            "confidence": "Độ tin cậy",
            "summary": "Tóm tắt chính",
            "plan": "Kế hoạch giao dịch",
            "scores": "Điểm mô hình",
            "trend": "Triển vọng xu hướng",
            "crypto": "Cấu trúc thị trường tiền mã hóa",
            "details": "Phân tích chi tiết",
            "reasons": "Lý do chính",
            "risks": "Lưu ý rủi ro",
            "indicators": "Chỉ báo kỹ thuật",
            "rr_warning": "Cảnh báo tỷ lệ rủi ro/lợi nhuận",
            "rr_warning_text": "Lợi nhuận tiềm năng thấp hơn rủi ro dự kiến. Mục tiêu chốt lời không bị kéo xa một cách giả tạo để che giấu cảnh báo này.",
            "disclaimer": "Báo cáo này do AI tạo cho mục đích nghiên cứu và không phải là lời khuyên đầu tư.",
        },
        "pt": {
            "title": "Relatório de Pesquisa IA QuantDinger",
            "subtitle": "Análise de mercado assistida por IA, apenas para fins de pesquisa",
            "target": "Ativo analisado",
            "generated": "Gerado em",
            "decision": "Perspectiva",
            "confidence": "Confiança",
            "summary": "Resumo executivo",
            "plan": "Plano de trading",
            "scores": "Pontuações do modelo",
            "trend": "Perspectiva de tendência",
            "crypto": "Estrutura do mercado cripto",
            "details": "Análise detalhada",
            "reasons": "Principais motivos",
            "risks": "Observações de risco",
            "indicators": "Indicadores técnicos",
            "rr_warning": "Alerta de risco/retorno",
            "rr_warning_text": "O retorno potencial é menor que o risco planejado. O alvo não foi afastado artificialmente para ocultar este alerta.",
            "disclaimer": "Este relatório foi gerado por IA apenas para fins de pesquisa e não constitui recomendação de investimento.",
        },
    }
    labels.update(overrides.get(_language_key(language), {}))
    labels["field_trend"] = {
        "en": "Trend", "zh-CN": "趋势", "zh-TW": "趨勢", "ja": "トレンド", "ko": "추세",
        "de": "Trend", "fr": "Tendance", "ru": "Тренд", "ar": "الاتجاه", "th": "แนวโน้ม", "vi": "Xu hướng", "pt": "Tendência",
    }.get(_language_key(language), "Trend")
    labels["field_direction"] = {
        "en": "Direction", "zh-CN": "方向", "zh-TW": "方向", "ja": "方向", "ko": "방향",
        "de": "Richtung", "fr": "Direction", "ru": "Направление", "ar": "الاتجاه", "th": "ทิศทาง", "vi": "Hướng", "pt": "Direção",
    }.get(_language_key(language), "Direction")
    labels["field_score"] = {
        "en": "Score", "zh-CN": "评分", "zh-TW": "評分", "ja": "スコア", "ko": "점수",
        "de": "Bewertung", "fr": "Score", "ru": "Оценка", "ar": "الدرجة", "th": "คะแนน", "vi": "Điểm", "pt": "Pontuação",
    }.get(_language_key(language), "Score")
    labels["field_strength"] = {
        "en": "Strength", "zh-CN": "强度", "zh-TW": "強度", "ja": "強さ", "ko": "강도",
        "de": "Stärke", "fr": "Force", "ru": "Сила", "ar": "القوة", "th": "ความแข็งแกร่ง", "vi": "Độ mạnh", "pt": "Força",
    }.get(_language_key(language), "Strength")
    labels["field_summary"] = labels["summary"]
    labels["field_value"] = {
        "en": "Value", "zh-CN": "数值", "zh-TW": "數值", "ja": "値", "ko": "값",
        "de": "Wert", "fr": "Valeur", "ru": "Значение", "ar": "القيمة", "th": "ค่า", "vi": "Giá trị", "pt": "Valor",
    }.get(_language_key(language), "Value")
    labels["field_signal"] = {
        "en": "Signal", "zh-CN": "信号", "zh-TW": "訊號", "ja": "シグナル", "ko": "신호",
        "de": "Signal", "fr": "Signal", "ru": "Сигнал", "ar": "الإشارة", "th": "สัญญาณ", "vi": "Tín hiệu", "pt": "Sinal",
    }.get(_language_key(language), "Signal")
    extra_labels = {
        "en": ["Current Price", "24h Change", "Entry", "Stop Loss", "Take Profit", "Risk/Reward", "Horizon", "Outlook"],
        "zh-CN": ["当前价格", "24 小时涨跌", "入场价", "止损价", "止盈价", "风险收益比", "周期", "预测"],
        "zh-TW": ["目前價格", "24 小時漲跌", "進場價", "停損價", "停利價", "風險收益比", "週期", "預測"],
        "ja": ["現在価格", "24時間変動", "エントリー", "損切り", "利確", "リスクリワード", "期間", "見通し"],
        "ko": ["현재 가격", "24시간 변동", "진입가", "손절가", "목표가", "손익비", "기간", "전망"],
        "de": ["Aktueller Kurs", "24h-Änderung", "Einstieg", "Stop-Loss", "Kursziel", "Risiko/Rendite", "Zeitraum", "Ausblick"],
        "fr": ["Cours actuel", "Variation sur 24 h", "Entrée", "Stop", "Objectif", "Risque/rendement", "Horizon", "Perspective"],
        "ru": ["Текущая цена", "Изменение за 24 ч", "Вход", "Стоп-лосс", "Цель", "Риск/доходность", "Период", "Прогноз"],
        "ar": ["السعر الحالي", "التغير خلال 24 ساعة", "الدخول", "وقف الخسارة", "جني الأرباح", "المخاطرة/العائد", "الفترة", "التوقعات"],
        "th": ["ราคาปัจจุบัน", "การเปลี่ยนแปลง 24 ชม.", "ราคาเข้า", "จุดตัดขาดทุน", "เป้าหมายกำไร", "ความเสี่ยง/ผลตอบแทน", "กรอบเวลา", "มุมมอง"],
        "vi": ["Giá hiện tại", "Thay đổi 24 giờ", "Điểm vào", "Cắt lỗ", "Chốt lời", "Rủi ro/lợi nhuận", "Khung thời gian", "Nhận định"],
        "pt": ["Preço atual", "Variação 24h", "Entrada", "Stop loss", "Take profit", "Risco/retorno", "Horizonte", "Perspectiva"],
    }.get(_language_key(language))
    (
        labels["current_price"],
        labels["change_24h"],
        labels["entry"],
        labels["stop_loss"],
        labels["take_profit"],
        labels["risk_reward"],
        labels["horizon"],
        labels["outlook"],
    ) = extra_labels
    return labels


def _build_professional_report_pdf(report: dict, target: dict | None, language: str) -> bytes:
    """Render the professional contract directly instead of flattening it to legacy fields."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        LongTable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    target = target or {}
    lang = _language_key(language)
    is_zh = lang in {"zh-CN", "zh-TW"}
    is_rtl = lang == "ar"
    font_name = _register_report_pdf_font(
        language=language,
        prefer_cjk=is_zh or _has_cjk_text(report) or _has_cjk_text(target),
    )
    page_width, _ = A4
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=18 * mm,
        bottomMargin=17 * mm,
        title="QuantDinger Professional Research Report",
        author="QuantDinger",
        subject="Evidence-backed market research",
    )
    content_width = page_width - doc.leftMargin - doc.rightMargin
    palette = {
        "navy": colors.HexColor("#0B1728"),
        "navy2": colors.HexColor("#13263E"),
        "green": colors.HexColor("#45B824"),
        "green_soft": colors.HexColor("#EDF8E9"),
        "ink": colors.HexColor("#172033"),
        "muted": colors.HexColor("#667085"),
        "line": colors.HexColor("#DCE4EA"),
        "soft": colors.HexColor("#F5F8FA"),
        "amber": colors.HexColor("#D97706"),
        "amber_soft": colors.HexColor("#FFF7E6"),
        "red": colors.HexColor("#C43D3D"),
        "red_soft": colors.HexColor("#FFF1F0"),
        "blue": colors.HexColor("#2563EB"),
        "blue_soft": colors.HexColor("#EFF6FF"),
        "white": colors.white,
    }
    base = getSampleStyleSheet()["Normal"]
    align = TA_RIGHT if is_rtl else TA_LEFT
    body = ParagraphStyle("ProBody", parent=base, fontName=font_name, fontSize=9, leading=14.5, textColor=palette["ink"], alignment=align)
    muted = ParagraphStyle("ProMuted", parent=body, fontSize=7.5, leading=10.5, textColor=palette["muted"])
    label_style = ParagraphStyle("ProLabel", parent=muted, fontSize=7, leading=9, textColor=palette["muted"], uppercase=True)
    value_style = ParagraphStyle("ProValue", parent=body, fontSize=11, leading=14, textColor=palette["ink"])
    title_style = ParagraphStyle("ProTitle", parent=body, fontSize=21, leading=26, textColor=palette["white"])
    subtitle_style = ParagraphStyle("ProSubtitle", parent=body, fontSize=8, leading=12, textColor=colors.HexColor("#C4D2E3"))
    section_style = ParagraphStyle("ProSection", parent=body, fontSize=12.5, leading=16, textColor=palette["navy"], keepWithNext=True)
    card_title_style = ParagraphStyle("ProCardTitle", parent=body, fontSize=9.5, leading=13, textColor=palette["navy"])
    centered_value = ParagraphStyle("ProCenteredValue", parent=value_style, alignment=TA_CENTER, fontSize=16, leading=20)
    table_head = ParagraphStyle("ProTableHead", parent=muted, fontSize=7.5, leading=10, textColor=palette["navy"])
    table_head_light = ParagraphStyle("ProTableHeadLight", parent=table_head, textColor=palette["white"])
    table_body = ParagraphStyle("ProTableBody", parent=body, fontSize=7.7, leading=11.5)

    copy = {
        "title": "专业市场分析报告" if is_zh else "Professional Market Research",
        "subtitle": "证据驱动 · 数据质量可审计 · 风险优先" if is_zh else "Evidence-backed · quality-audited · risk-first",
        "outlook": "研究观点" if is_zh else "RESEARCH OUTLOOK",
        "market_bias": "市场方向" if is_zh else "MARKET BIAS",
        "trade_action": "交易动作" if is_zh else "TRADE ACTION",
        "generated": "生成时间" if is_zh else "GENERATED",
        "as_of": "数据截止" if is_zh else "DATA AS OF",
        "tier": "数据等级" if is_zh else "DATA TIER",
        "confidence": "动作判断强度" if is_zh else "ACTION CONFIDENCE",
        "summary": "核心结论" if is_zh else "Executive conclusion",
        "quality": "数据质量审计" if is_zh else "Data quality audit",
        "quality_score": "综合质量" if is_zh else "Overall quality",
        "coverage": "核心指标覆盖率" if is_zh else "Core coverage",
        "freshness": "数据新鲜度" if is_zh else "Freshness",
        "conflict": "证据冲突率" if is_zh else "Conflict rate",
        "strength": "结论强度" if is_zh else "Conclusion strength",
        "dimensions": "多维分析" if is_zh else "Multi-dimensional analysis",
        "scenarios": "情景与触发条件" if is_zh else "Scenarios and triggers",
        "scenario": "情景" if is_zh else "Case",
        "probability": "权重" if is_zh else "Weight",
        "target": "参考目标" if is_zh else "Reference target",
        "trigger": "触发条件" if is_zh else "Trigger",
        "invalidation": "失效条件" if is_zh else "Invalidation",
        "risk": "风险与执行计划" if is_zh else "Risk and execution plan",
        "candidate_risk": "候选观察方案（非当前入场建议）" if is_zh else "Watch-only candidate setup (not an entry recommendation)",
        "entry": "参考入场" if is_zh else "Reference entry",
        "stop": "止损" if is_zh else "Stop",
        "take": "止盈" if is_zh else "Target",
        "rr": "净风险收益比" if is_zh else "Net risk/reward",
        "position": "建议仓位上限" if is_zh else "Position cap",
        "risk_budget": "账户风险预算" if is_zh else "Risk budget",
        "cost": "预估往返成本" if is_zh else "Est. round-trip cost",
        "claims": "核心论据与反方证据" if is_zh else "Thesis, risks and counter-evidence",
        "evidence": "证据附录" if is_zh else "Evidence appendix",
        "metric": "指标" if is_zh else "Metric",
        "observed": "观测值" if is_zh else "Observed value",
        "provider": "数据源" if is_zh else "Provider",
        "timestamp": "时间" if is_zh else "Timestamp",
        "evidence_id": "证据编号" if is_zh else "Evidence ID",
        "limitations": "数据缺口与限制" if is_zh else "Data gaps and limitations",
        "method": "方法与版本" if is_zh else "Methodology and versions",
        "disclaimer": "AI 辅助研究，仅供参考，不构成投资建议。请独立核验数据并评估风险。" if is_zh else "AI-assisted research only. Not investment advice. Verify data and assess risk independently.",
        "page": "第 {page} 页" if is_zh else "Page {page}",
    }

    def esc(value: Any) -> str:
        text = _plain_text(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def para(value: Any, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(esc(value).replace("\n", "<br/>"), style)

    def pct(value: Any, *, ratio: bool = False) -> str:
        try:
            number = float(value)
            if ratio or abs(number) <= 1:
                number *= 100
            return f"{number:.0f}%"
        except (TypeError, ValueError):
            return "—"

    def number(value: Any, digits: int = 2, suffix: str = "") -> str:
        try:
            return f"{float(value):,.{digits}f}{suffix}"
        except (TypeError, ValueError):
            return "—"

    def short_timestamp(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "—"
        return text[:19].replace("T", " ") + (" UTC" if "T" in text else "")

    def dimension_name(value: Any) -> str:
        key = str(value or "dimension")
        if not is_zh:
            return key.replace("_", " ").title()
        return {
            "technical": "技术结构",
            "fundamental": "基本面",
            "news_sentiment": "新闻与情绪",
            "sentiment": "市场情绪",
            "macro": "宏观环境",
            "market_specific": "市场特有维度",
            "crypto_market_structure": "加密市场结构",
        }.get(key, key.replace("_", " "))

    def section(title: str, note: str = "") -> list[Any]:
        heading = Table(
            [[[para(title, section_style), para(note, muted)]]],
            colWidths=[content_width],
        )
        heading.setStyle(TableStyle([
            ("LINEBEFORE", (0, 0), (0, 0), 3, palette["green"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]))
        return [Spacer(1, 7 * mm), heading, Spacer(1, 3 * mm)]

    def metric_cards(items: list[tuple[str, str]], columns: int = 3) -> Table:
        cells = []
        for label, value in items:
            cells.append([para(label, label_style), para(value, centered_value)])
        rows = [cells[index:index + columns] for index in range(0, len(cells), columns)]
        if rows and len(rows[-1]) < columns:
            rows[-1].extend([[para("", label_style), para("", value_style)]] * (columns - len(rows[-1])))
        table = Table(rows, colWidths=[content_width / columns] * columns)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), palette["soft"]),
            ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, palette["line"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return table

    def draw_page(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(palette["line"])
        canvas.line(doc.leftMargin, 13 * mm, page_width - doc.rightMargin, 13 * mm)
        canvas.setFont(font_name, 6.8)
        canvas.setFillColor(palette["muted"])
        canvas.drawString(doc.leftMargin, 8.5 * mm, copy["disclaimer"])
        canvas.drawRightString(page_width - doc.rightMargin, 8.5 * mm, copy["page"].format(page=document.page))
        if document.page > 1:
            canvas.setFillColor(palette["navy"])
            canvas.rect(0, A4[1] - 8 * mm, page_width, 8 * mm, fill=1, stroke=0)
            canvas.setFillColor(palette["white"])
            canvas.drawString(doc.leftMargin, A4[1] - 5.3 * mm, "QUANTDINGER  /  PROFESSIONAL RESEARCH")
        canvas.restoreState()

    instrument = report.get("instrument") or {}
    quality = report.get("data_quality") or {}
    decision = report.get("decision_profile") or {}
    risk = report.get("risk_plan") or {}
    dimensions = [item for item in (report.get("dimensions") or []) if isinstance(item, dict)]
    scenarios = [item for item in (report.get("scenarios") or []) if isinstance(item, dict)]
    claims = [item for item in (report.get("claims") or []) if isinstance(item, dict)]
    evidence = [item for item in ((report.get("evidence_snapshot") or {}).get("observations") or []) if isinstance(item, dict)]
    symbol = instrument.get("canonical_symbol") or instrument.get("symbol") or target.get("symbol") or "—"
    market = instrument.get("market") or target.get("market") or "—"
    name = instrument.get("name") or symbol
    outlook = str(decision.get("decision") or "HOLD").upper()
    outlook_display = _outlook_labels(language).get(outlook, outlook)
    market_bias = _report_market_bias(decision, dimensions)
    bias_display = _market_bias_labels(language).get(market_bias, market_bias)
    outlook_color = palette["green"] if market_bias == "BULLISH" else palette["red"] if market_bias == "BEARISH" else palette["amber"]
    overall_quality = quality.get("overall_score")
    if overall_quality is None:
        raw_quality = quality.get("quality_score")
        try:
            overall_quality = float(raw_quality) * 100 if float(raw_quality) <= 1 else float(raw_quality)
        except (TypeError, ValueError):
            overall_quality = None

    story: list[Any] = []
    hero = Table([
        [
            [para("QUANTDINGER", subtitle_style), para(copy["title"], title_style), para(copy["subtitle"], subtitle_style)],
            [para(copy["market_bias"], subtitle_style), para(bias_display, ParagraphStyle("ProOutlook", parent=title_style, fontSize=18, leading=22, textColor=outlook_color)), para(f"{copy['trade_action']}  {outlook_display}<br/>{copy['confidence']}  {pct(decision.get('confidence'))}", subtitle_style)],
        ]
    ], colWidths=[content_width * 0.69, content_width * 0.31])
    hero.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), palette["navy"]),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 13),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 13),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBEFORE", (1, 0), (1, 0), 1, colors.HexColor("#30445C")),
    ]))
    story.extend([hero, Spacer(1, 4 * mm)])
    instrument_table = Table([
        [para(f"{market}:{symbol}", ParagraphStyle("Instrument", parent=value_style, fontSize=14, leading=18)), para(copy["generated"], label_style), para(copy["as_of"], label_style), para(copy["tier"], label_style)],
        [para(name, muted), para(short_timestamp(report.get("generated_at")), table_body), para(short_timestamp(report.get("as_of")), table_body), para(report.get("data_tier") or "community", table_body)],
    ], colWidths=[content_width * 0.37, content_width * 0.21, content_width * 0.25, content_width * 0.17])
    instrument_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), palette["soft"]),
        ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, palette["line"]),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("SPAN", (0, 0), (0, 0)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(instrument_table)

    story.extend(section(copy["summary"]))
    summary = report.get("executive_summary") or decision.get("rationale") or "—"
    summary_box = Table([[para(summary, ParagraphStyle("Summary", parent=body, fontSize=10, leading=16))]], colWidths=[content_width])
    summary_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), palette["green_soft"]),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#B8DCAA")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(summary_box)

    story.extend(section(copy["quality"], f"{copy['quality_score']}: {number(overall_quality, 0, '%') if overall_quality is not None else '—'}"))
    story.append(metric_cards([
        (copy["coverage"], pct(quality.get("coverage_ratio"), ratio=True)),
        (copy["freshness"], pct(quality.get("freshness_ratio"), ratio=True)),
        (copy["conflict"], pct(quality.get("conflict_ratio"), ratio=True)),
        (copy["strength"], str(quality.get("max_conclusion_strength") or "—")),
        (copy["as_of"], short_timestamp(report.get("as_of"))),
        (copy["tier"], str(report.get("data_tier") or "community")),
    ]))

    if dimensions:
        story.extend(section(copy["dimensions"]))
        for item in dimensions:
            status = str(item.get("status") or "")
            score = number(item.get("score"), 0) if status == "available" else ("数据不足" if is_zh else "Insufficient")
            missing = item.get("missing_data") or []
            note = ("缺失：" if is_zh else "Missing: ") + ", ".join(map(str, missing)) if missing else ""
            card = Table([
                [para(dimension_name(item.get("key")), card_title_style), para(score, ParagraphStyle("DimensionScore", parent=value_style, alignment=TA_RIGHT, textColor=palette["green"] if status == "available" else palette["amber"]))],
                [para(item.get("narrative") or ("暂无可由证据支持的分析。" if is_zh else "No evidence-backed narrative is available."), body), ""],
                [para(note, muted), ""],
            ], colWidths=[content_width * 0.82, content_width * 0.18])
            card.setStyle(TableStyle([
                ("SPAN", (0, 1), (1, 1)), ("SPAN", (0, 2), (1, 2)),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white), ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEABOVE", (0, 0), (-1, 0), 2, palette["green"] if status == "available" else palette["amber"]),
            ]))
            story.extend([KeepTogether([card]), Spacer(1, 2.2 * mm)])

    if scenarios:
        story.extend(section(copy["scenarios"]))
        scenario_rows = [[para(copy[k], table_head_light) for k in ("scenario", "probability", "target", "trigger", "invalidation")]]
        for item in scenarios:
            trigger = item.get("trigger") or item.get("triggers") or item.get("thesis") or "—"
            invalidation = item.get("invalidation") or "—"
            scenario_rows.append([
                para(str(item.get("case") or "—").upper(), table_body),
                para(pct(item.get("probability"), ratio=True), table_body),
                para(number(item.get("target_price"), 2), table_body),
                para(trigger, table_body),
                para(invalidation, table_body),
            ])
        scenarios_table = Table(scenario_rows, colWidths=[content_width * x for x in (0.10, 0.09, 0.13, 0.42, 0.26)], repeatRows=1)
        scenarios_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), palette["navy2"]), ("TEXTCOLOR", (0, 0), (-1, 0), palette["white"]),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, palette["soft"]]),
            ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]), ("INNERGRID", (0, 0), (-1, -1), 0.35, palette["line"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(scenarios_table)

    if risk:
        candidate = risk.get("candidate_setup") or {}
        displayed_risk = candidate or risk
        story.extend(section(copy["candidate_risk"] if candidate else copy["risk"]))
        story.append(metric_cards([
            (copy["entry"], number(displayed_risk.get("entry_price"))),
            (copy["stop"], number(displayed_risk.get("stop_loss"))),
            (copy["take"], number(displayed_risk.get("take_profit"))),
            (copy["rr"], number(displayed_risk.get("net_risk_reward"))),
            (copy["position"], number(risk.get("recommended_position_pct"), 1, "%")),
            (copy["risk_budget"], number(risk.get("risk_budget_pct"), 1, "%")),
        ]))
        warnings = [str(item) for item in [*(risk.get("warnings") or []), *(candidate.get("warnings") or [])]]
        invalidations = [str(item) for item in (risk.get("invalidation_conditions") or [])]
        if warnings or invalidations:
            story.append(Spacer(1, 2 * mm))
            warning_box = Table([[para(" · ".join(warnings + invalidations), muted)]], colWidths=[content_width])
            warning_box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), palette["amber_soft"]), ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#F2C078")),
                ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.append(warning_box)

    if claims:
        story.extend(section(copy["claims"]))
        for item in claims:
            kind = str(item.get("kind") or "claim").replace("_", " ").upper()
            refs = ", ".join(map(str, item.get("evidence_refs") or []))
            claim_table = Table([
                [para(kind, ParagraphStyle("ClaimKind", parent=table_head, textColor=palette["blue"])), para(item.get("text") or "—", body)],
                ["", para(f"{copy['evidence_id']}: {refs or '—'}", muted)],
            ], colWidths=[content_width * 0.16, content_width * 0.84])
            claim_table.setStyle(TableStyle([
                ("SPAN", (0, 0), (0, 1)), ("BACKGROUND", (0, 0), (0, -1), palette["blue_soft"]),
                ("BOX", (0, 0), (-1, -1), 0.5, palette["line"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.extend([KeepTogether([claim_table]), Spacer(1, 1.5 * mm)])

    missing = list(dict.fromkeys((quality.get("missing_metrics") or []) + ((report.get("market_features") or {}).get("missing_capabilities") or [])))
    warnings = list(dict.fromkeys((report.get("warnings") or []) + (quality.get("warnings") or [])))
    if missing or warnings:
        story.extend(section(copy["limitations"]))
        for item in missing + warnings:
            story.append(para(f"• {item}", body))

    if evidence:
        story.extend([PageBreak(), *section(copy["evidence"], f"{len(evidence)} observations")])
        evidence_rows = [[para(copy[key], table_head_light) for key in ("metric", "observed", "provider", "timestamp", "evidence_id")]]
        for item in evidence:
            evidence_rows.append([
                para(item.get("metric") or "—", table_body),
                para(_format_evidence_observation(item, is_zh=is_zh), table_body),
                para(_format_evidence_provider(item.get("source")), table_body),
                para(short_timestamp(item.get("as_of")), table_body),
                para(item.get("evidence_id") or "—", muted),
            ])
        evidence_table = LongTable(evidence_rows, colWidths=[content_width * x for x in (0.25, 0.22, 0.16, 0.19, 0.18)], repeatRows=1)
        evidence_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), palette["navy2"]), ("TEXTCOLOR", (0, 0), (-1, 0), palette["white"]),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, palette["soft"]]),
            ("BOX", (0, 0), (-1, -1), 0.5, palette["line"]), ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(evidence_table)

    methodology = report.get("methodology") or {}
    versions = [
        f"Report ID: {report.get('report_id') or '—'}",
        f"Model: {report.get('model_version') or '—'}",
        f"Prompt: {report.get('prompt_version') or '—'}",
        f"Scoring: {report.get('scoring_version') or methodology.get('scoring_version') or '—'}",
        f"Builder: {methodology.get('report_builder_version') or '—'}",
    ]
    story.extend(section(copy["method"]))
    story.append(para(" · ".join(versions), muted))
    story.append(Spacer(1, 4 * mm))
    story.append(para(copy["disclaimer"], ParagraphStyle("FinalDisclaimer", parent=body, fontSize=8, leading=12, textColor=palette["muted"], alignment=TA_CENTER)))

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()


def build_ai_report_pdf(report: dict, target: dict | None = None, language: str = "en-US") -> bytes:
    professional = _professional_report_artifact(report)
    if professional:
        return _build_professional_report_pdf(professional, target, language)
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    report = _professional_pdf_projection(report)
    target = target or {}
    language_key = _language_key(language)
    prefer_cjk = language_key in {"zh-CN", "zh-TW", "ja", "ko"} or _has_cjk_text(report) or _has_cjk_text(target)
    font_name = _register_report_pdf_font(language=language, prefer_cjk=prefer_cjk)
    is_rtl = language_key == "ar"
    width, height = A4
    buf = BytesIO()
    labels = _report_pdf_labels(language)

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=labels["title"],
    )
    content_width = width - doc.leftMargin - doc.rightMargin
    styles = getSampleStyleSheet()
    base_style = ParagraphStyle(
        "ReportBase",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9.5,
        leading=15,
        textColor=colors.HexColor("#273449"),
        spaceAfter=4,
        shaping=is_rtl,
        alignment=TA_RIGHT if is_rtl else TA_LEFT,
    )
    small_style = ParagraphStyle(
        "ReportSmall",
        parent=base_style,
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#667085"),
    )
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=base_style,
        fontSize=22,
        leading=28,
        textColor=colors.white,
        spaceAfter=2,
    )
    section_style = ParagraphStyle(
        "ReportSection",
        parent=base_style,
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#0f2f55"),
        spaceBefore=10,
        spaceAfter=7,
    )
    table_head_style = ParagraphStyle(
        "ReportTableHead",
        parent=base_style,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#667085"),
    )
    table_value_style = ParagraphStyle(
        "ReportTableValue",
        parent=base_style,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#111827"),
    )

    def clean_text(value: Any) -> str:
        text = _plain_text(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def value_text(value: Any) -> str:
        if value in (None, ""):
            return "-"
        if isinstance(value, dict):
            parts = []
            preferred = ["trend", "direction", "score", "strength", "summary", "value", "signal"]
            keys = [key for key in preferred if key in value] + [key for key in value.keys() if key not in preferred]
            for key in keys[:4]:
                item = value.get(key)
                if item not in (None, "", [], {}):
                    field_label = labels.get(f"field_{key}", str(key).replace("_", " ").title())
                    parts.append(f"{field_label}: {value_text(item)}")
            return "; ".join(parts) or "-"
        if isinstance(value, (list, tuple)):
            return "; ".join(value_text(item) for item in value[:5] if item not in (None, "", [], {})) or "-"
        return clean_text(value)

    def p(text: Any, style: ParagraphStyle = base_style) -> Paragraph:
        return Paragraph(clean_text(text).replace("\n", "<br/>"), style)

    def section(title: str) -> list[Any]:
        return [Spacer(1, 5 * mm), Paragraph(clean_text(title), section_style)]

    def pair_table(items: list[tuple[str, Any]], columns: int = 2) -> Table:
        rows = []
        row = []
        for label, value in items:
            row.append([Paragraph(clean_text(label), table_head_style), Paragraph(value_text(value), table_value_style)])
            if len(row) == columns:
                rows.append(row)
                row = []
        if row:
            while len(row) < columns:
                row.append([Paragraph("", table_head_style), Paragraph("", table_value_style)])
            rows.append(row)

        col_width = content_width / columns
        table = Table(rows, colWidths=[col_width] * columns, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f9fc")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1ee")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e6edf5")),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return table

    def simple_table(headers: list[str], rows: list[list[Any]]) -> Table:
        data = [[Paragraph(clean_text(header), table_head_style) for header in headers]]
        data.extend([[Paragraph(value_text(value), table_value_style) for value in row] for row in rows])
        table = Table(data, colWidths=[content_width / len(headers)] * len(headers), hAlign="LEFT", repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef4fb")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#42526e")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafcff")]),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1ee")),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#e6edf5")),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return table

    def bullet_block(items: Any) -> list[Any]:
        if not isinstance(items, list):
            return []
        return [p(f"- {value_text(item)}") for item in items if value_text(item) != "-"]

    def draw_page(canvas_obj: Any, document: Any) -> None:
        canvas_obj.saveState()
        canvas_obj.setFillColor(colors.HexColor("#8a94a6"))
        canvas_obj.setFont(font_name, 7.5)
        if is_rtl:
            canvas_obj.drawRightString(
                width - doc.rightMargin,
                9 * mm,
                labels["disclaimer"],
                direction="RTL",
                shaping=True,
            )
            canvas_obj.drawString(doc.leftMargin, 9 * mm, f"QuantDinger Research · {document.page}")
        else:
            canvas_obj.drawString(doc.leftMargin, 9 * mm, labels["disclaimer"])
            canvas_obj.drawRightString(width - doc.rightMargin, 9 * mm, f"QuantDinger Research · {document.page}")
        canvas_obj.restoreState()

    symbol = report.get("symbol") or target.get("symbol") or ""
    market = report.get("market") or target.get("market") or ""
    decision = _plain_text(report.get("decision") or "HOLD").upper()
    decision_display = _outlook_labels(language).get(decision, decision)
    decision_color = colors.HexColor("#15803d" if decision == "BUY" else "#b91c1c" if decision == "SELL" else "#b7791f")
    story: list[Any] = []
    header = Table([
        [
            [Paragraph(labels["title"], title_style), Paragraph(labels["subtitle"], small_style)],
            [
                Paragraph(labels["decision"], small_style),
                Paragraph(decision_display, ParagraphStyle("Decision", parent=base_style, fontSize=20, leading=24, textColor=decision_color)),
            ],
        ]
    ], colWidths=[content_width * 0.72, content_width * 0.28])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#102033")),
        ("BOX", (0, 0), (-1, -1), 0, colors.HexColor("#102033")),
        ("LEFTPADDING", (0, 0), (-1, -1), 13),
        ("RIGHTPADDING", (0, 0), (-1, -1), 13),
        ("TOPPADDING", (0, 0), (-1, -1), 13),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 13),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(header)
    story.append(Spacer(1, 6 * mm))
    story.append(pair_table([
        (labels["target"], f"{market}:{symbol}" if market or symbol else "-"),
        (labels["generated"], _now_utc().strftime("%Y-%m-%d %H:%M UTC")),
        (labels["confidence"], report.get("confidence", "-")),
        (labels["decision"], decision_display),
    ], columns=2))

    if report.get("summary"):
        story.extend(section(labels["summary"]))
        story.append(p(report.get("summary")))

    market_data = report.get("market_data") if isinstance(report.get("market_data"), dict) else {}
    plan = report.get("trading_plan") if isinstance(report.get("trading_plan"), dict) else {}
    rr_value = plan.get("risk_reward_ratio")
    if rr_value is None:
        rr_value = plan.get("riskRewardRatio")
    plan_items = [
        (labels["current_price"], market_data.get("current_price")),
        (labels["change_24h"], market_data.get("change_24h")),
        (labels["entry"], plan.get("entry_price") or plan.get("entryPrice")),
        (labels["stop_loss"], plan.get("stop_loss") or plan.get("stopLoss")),
        (labels["take_profit"], plan.get("take_profit") or plan.get("takeProfit")),
        (labels["risk_reward"], rr_value),
    ]
    if any(v not in (None, "") for _, v in plan_items):
        story.extend(section(labels["plan"]))
        story.append(pair_table([(k, "-" if v in (None, "") else v) for k, v in plan_items]))
        if plan.get("rr_warning") or plan.get("rrWarning"):
            warning = Table(
                [[Paragraph(clean_text(labels["rr_warning"]), table_head_style),
                  Paragraph(clean_text(labels["rr_warning_text"]), table_value_style)]],
                colWidths=[content_width * 0.25, content_width * 0.75],
                hAlign="LEFT",
            )
            warning.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff7e6")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#faad14")),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(Spacer(1, 2 * mm))
            story.append(warning)

    scores = report.get("scores") if isinstance(report.get("scores"), dict) else {}
    if scores:
        story.extend(section(labels["scores"]))
        story.append(pair_table([(str(k).replace("_", " ").title(), v) for k, v in scores.items()]))

    trend = report.get("trend_outlook") or report.get("trendOutlook")
    trend_summary = report.get("trend_outlook_summary") or report.get("trendOutlookSummary")
    if trend_summary or trend:
        story.extend(section(labels["trend"]))
        if trend_summary:
            story.append(p(trend_summary))
        if isinstance(trend, dict):
            story.append(simple_table(
                [labels["horizon"], labels["outlook"]],
                [[str(k), v] for k, v in trend.items()],
            ))

    crypto_summary = report.get("crypto_factor_summary")
    crypto_factors = report.get("crypto_factors") if isinstance(report.get("crypto_factors"), dict) else {}
    if crypto_summary or crypto_factors:
        story.extend(section(labels["crypto"]))
        if crypto_summary:
            story.append(p(crypto_summary))
        if crypto_factors:
            story.append(pair_table([(str(k).replace("_", " "), v) for k, v in crypto_factors.items() if k != "signals"]))

    details = report.get("detailed_analysis") if isinstance(report.get("detailed_analysis"), dict) else {}
    if details:
        story.extend(section(labels["details"]))
        for key, value in details.items():
            story.append(KeepTogether([
                Paragraph(clean_text(str(key).replace("_", " ").title()), ParagraphStyle(
                    f"Detail{key}",
                    parent=base_style,
                    fontSize=10.5,
                    leading=14,
                    textColor=colors.HexColor("#0f2f55"),
                    spaceBefore=3,
                )),
                p(value),
            ]))

    if report.get("reasons"):
        story.extend(section(labels["reasons"]))
        story.extend(bullet_block(report.get("reasons")))
    if report.get("risks"):
        story.extend(section(labels["risks"]))
        story.extend(bullet_block(report.get("risks")))

    indicators = report.get("indicators") if isinstance(report.get("indicators"), dict) else {}
    if indicators:
        story.extend(section(labels["indicators"]))
        story.append(pair_table([(str(k).replace("_", " "), v) for k, v in indicators.items()]))

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buf.getvalue()
