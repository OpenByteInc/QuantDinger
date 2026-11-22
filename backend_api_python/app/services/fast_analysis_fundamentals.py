"""Formatting and presentation helpers for period-aware fundamental analysis."""

from __future__ import annotations

from typing import Any, Dict
from app.research.fundamentals import normalize_fundamentals


def format_fundamental_metric(fundamental: Dict[str, Any], key: str) -> str:
    """Render a metric with its unit, period basis and source for the LLM."""
    fundamental = normalize_fundamentals(fundamental)
    value = fundamental.get(key)
    if value is None:
        return "N/A"
    metadata = (fundamental.get("field_metadata") or {}).get(key) or {}
    qualifiers = [metadata.get("unit"), metadata.get("currency"), metadata.get("period_type"), metadata.get("period_end"), metadata.get("source")]
    suffix = ", ".join(str(item) for item in qualifiers if item)
    return f"{value} [{suffix}]" if suffix else str(value)


def objective_scores_for_ui(objective_score: Dict[str, Any]) -> Dict[str, int]:
    """Map deterministic component scores from [-100, 100] to UI [0, 100]."""
    def component(key: str) -> int:
        try:
            raw = float(objective_score.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            raw = 0.0
        return max(0, min(100, int(round(50.0 + raw * 0.5))))

    return {
        "technical": component("technical_score"),
        "fundamental": component("fundamental_score"),
        "sentiment": component("sentiment_score"),
    }


def build_score_payload(objective_score: Dict[str, Any], analysis: Dict[str, Any], overall: int) -> Dict[str, Any]:
    """Build auditable UI scores while retaining model scores for diagnostics."""
    scores = objective_scores_for_ui(objective_score)
    scores["overall"] = overall
    return {
        "scores": scores,
        "score_source": "deterministic_objective_v2",
        "llm_scores": {
            "technical": analysis.get("technical_score", 50),
            "fundamental": analysis.get("fundamental_score", 50),
            "sentiment": analysis.get("sentiment_score", 50),
        },
    }


def fundamental_provenance(fundamental: Dict[str, Any]) -> Dict[str, Any]:
    """Expose data quality and field provenance with the report result."""
    return {
        "fundamental_data_quality": fundamental.get("data_quality", {}),
        "fundamental_identity": fundamental.get("identity", {}),
        "fundamental_field_metadata": fundamental.get("field_metadata", {}),
    }


def format_financial_statements(statements: Dict[str, Any]) -> str:
    """Format quarterly, TTM and annual evidence without mixing periods."""
    if not statements:
        return "财务报表数据暂不可用"

    def amount(value: Any) -> str:
        if value is None:
            return "N/A"
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return "N/A"

    latest_quarter = statements.get("latest_quarter") or {}
    period_label = "LATEST REPORTED QUARTER" if latest_quarter else "LATEST REPORTED PERIOD (NOT NECESSARILY A QUARTER)"
    ttm = statements.get("ttm") or {}
    latest_annual = statements.get("latest_annual") or {}
    if not latest_quarter:
        latest_quarter = statements.get("latest_report") or {
            "period_end": (statements.get("income_statement") or {}).get("latest_date"),
            "period_type": "provider_latest",
            "currency": (statements.get("_meta") or {}).get("currency", ""),
            "balance_sheet": statements.get("balance_sheet") or {},
            "income_statement": statements.get("income_statement") or {},
            "cash_flow": statements.get("cash_flow") or {},
            "derived": {},
        }

    q_income = latest_quarter.get("income_statement") or {}
    q_balance = latest_quarter.get("balance_sheet") or {}
    q_cash = latest_quarter.get("cash_flow") or {}
    q_derived = latest_quarter.get("derived") or {}
    currency = q_income.get("currency") or latest_quarter.get("currency") or (statements.get("_meta") or {}).get("currency") or "reporting currency"
    lines = [
        f"{period_label} | period_end={latest_quarter.get('period_end') or q_income.get('latest_date') or 'N/A'} "
        f"| currency={currency} | source={latest_quarter.get('source') or (statements.get('_meta') or {}).get('source') or 'provider'}",
        "  Income: "
        f"revenue={amount(q_income.get('total_revenue'))}; operating_income={amount(q_income.get('operating_income'))}; "
        f"net_income={amount(q_income.get('net_income'))}; EPS={q_income.get('eps', 'N/A')}",
        "  Balance: "
        f"assets={amount(q_balance.get('total_assets'))}; liabilities={amount(q_balance.get('total_liabilities'))}; "
        f"equity={amount(q_balance.get('total_equity'))}; cash={amount(q_balance.get('cash'))}; debt={amount(q_balance.get('debt'))}",
        "  Cash flow: "
        f"operating_cash_flow={amount(q_cash.get('operating_cash_flow'))}; free_cash_flow={amount(q_cash.get('free_cash_flow'))}",
    ]
    if q_derived:
        lines.append(
            "  Derived period metrics: "
            f"revenue_growth_pct={q_derived.get('revenue_growth', 'N/A')}; net_margin_pct={q_derived.get('profit_margin', 'N/A')}; "
            f"current_ratio={q_derived.get('current_ratio', 'N/A')}; debt_to_equity={q_derived.get('debt_to_equity', 'N/A')}"
        )
    if ttm:
        ttm_income = ttm.get("income_statement") or {}
        ttm_cash = ttm.get("cash_flow") or {}
        ttm_derived = ttm.get("derived") or {}
        lines.extend([
            f"TTM THROUGH {ttm.get('period_end') or 'N/A'} | source={ttm.get('source') or 'provider'} "
            f"| complete_quarters={ttm.get('complete_quarters', 'N/A')}",
            f"  revenue={amount(ttm_income.get('total_revenue'))}; net_income={amount(ttm_income.get('net_income'))}; "
            f"operating_cash_flow={amount(ttm_cash.get('operating_cash_flow'))}; free_cash_flow={amount(ttm_cash.get('free_cash_flow'))}; "
            f"net_margin_pct={ttm_derived.get('profit_margin', 'N/A')}; ROE_pct={ttm_derived.get('roe', 'N/A')}",
        ])
    if latest_annual and latest_annual.get("period_end"):
        annual_income = latest_annual.get("income_statement") or {}
        annual_balance = latest_annual.get("balance_sheet") or {}
        lines.append(
            f"LATEST ANNUAL (STRUCTURAL CONTEXT ONLY) | period_end={latest_annual.get('period_end')} "
            f"| revenue={amount(annual_income.get('total_revenue'))}; net_income={amount(annual_income.get('net_income'))}; "
            f"assets={amount(annual_balance.get('total_assets'))}; equity={amount(annual_balance.get('total_equity'))}"
        )
    lines.append("PERIOD RULE: do not combine quarterly, TTM and annual figures as one reporting period.")
    return "\n".join(lines)
