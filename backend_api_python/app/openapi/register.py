"""
Register all human-facing API blueprints with flask-smorest.

Legacy handler bodies stay in ``app/routes/*``; this module wires them into
the OpenAPI generator. Agent Gateway remains on ``/api/agent/v1`` with its
own hand-maintained spec (``docs/agent/agent-openapi.json``).
"""
from __future__ import annotations

from flask_smorest import Api

from app.openapi.tags import ALL_TAGS


# url_prefix -> OpenAPI tag (English)
_PREFIX_TAGS: list[tuple[str, str]] = [
    ("/api/policy", "Policy"),
    ("/api/auth", "Auth"),
    ("/api/users", "Users"),
    ("/api/indicator", "Indicator"),
    ("/api/market", "Market"),
    ("/api/market-modules", "Market"),
    ("/api/ai", "AIChat"),
    ("/api/strategies", "Strategy"),
    ("/api/bots", "Strategy"),
    ("/api/credentials", "Credentials"),
    ("/api/dashboard", "Dashboard"),
    ("/api/settings", "Settings"),
    ("/api/portfolio", "Portfolio"),
    ("/api/ibkr", "IBKR"),
    ("/api/alpaca", "Alpaca"),
    ("/api/mt5", "MT5"),
    ("/api/global-market", "GlobalMarket"),
    ("/api/community", "Community"),
    ("/api/fast-analysis", "FastAnalysis"),
    ("/api/billing", "Billing"),
    ("/api/quick-trade", "QuickTrade"),
    ("/api/experiment", "Experiment"),
]


def _tag_for_prefix(prefix: str) -> str | None:
    for p, tag in _PREFIX_TAGS:
        if prefix == p or prefix.startswith(p + "/"):
            return tag
    if prefix in ("", "/"):
        return "Health"
    if prefix == "/api":
        return "Strategy"
    return None


def register_human_blueprints(api: Api) -> None:
    """Mount every human web blueprint on the shared smorest Api instance."""
    from app.openapi.routes.health import blp as health_blp
    from app.routes.policy import policy_blp
    from app.routes.auth import auth_blp
    from app.routes.user import user_blp
    from app.routes.kline import kline_blp
    from app.routes.backtest import backtest_blp
    from app.routes.market import market_blp
    from app.routes.market_modules import market_modules_blp
    from app.routes.ai_chat import ai_chat_blp
    from app.routes.indicator import indicator_blp
    from app.routes.strategy import strategy_blp
    from app.routes.credentials import credentials_blp
    from app.routes.dashboard import dashboard_blp
    from app.routes.settings import settings_blp
    from app.routes.portfolio import portfolio_blp
    from app.routes.ibkr import ibkr_blp
    from app.routes.alpaca import alpaca_blp
    from app.routes.mt5 import mt5_blp
    from app.routes.global_market import global_market_blp
    from app.routes.community import community_blp
    from app.routes.fast_analysis import fast_analysis_blp
    from app.routes.billing import billing_blp
    from app.routes.quick_trade import quick_trade_blp
    from app.routes.experiment import experiment_blp

    registrations: list[tuple] = [
        (health_blp, ""),
        (policy_blp, "/api/policy"),
        (auth_blp, "/api/auth"),
        (user_blp, "/api/users"),
        (kline_blp, "/api/indicator"),
        (backtest_blp, "/api/indicator"),
        (market_blp, "/api/market"),
        (market_modules_blp, "/api/market-modules"),
        (ai_chat_blp, "/api/ai"),
        (indicator_blp, "/api/indicator"),
        (strategy_blp, "/api"),
        (credentials_blp, "/api/credentials"),
        (dashboard_blp, "/api/dashboard"),
        (settings_blp, "/api/settings"),
        (portfolio_blp, "/api/portfolio"),
        (ibkr_blp, "/api/ibkr"),
        (alpaca_blp, "/api/alpaca"),
        (mt5_blp, "/api/mt5"),
        (global_market_blp, "/api/global-market"),
        (community_blp, "/api/community"),
        (fast_analysis_blp, "/api/fast-analysis"),
        (billing_blp, "/api/billing"),
        (quick_trade_blp, "/api/quick-trade"),
        (experiment_blp, "/api/experiment"),
    ]

    for blp, prefix in registrations:
        api.register_blueprint(blp, url_prefix=prefix)

    # Ensure tag metadata exists in the spec.
    opts = api.spec.options.setdefault("tags", [])
    existing = {t["name"] for t in opts if isinstance(t, dict) and "name" in t}
    for tag in ALL_TAGS:
        if tag["name"] not in existing:
            opts.append(tag)


_SSE_PATHS = frozenset({
    "/api/indicator/aiGenerate",
    "/api/strategies/ai-generate",
    "/api/experiment/ai-optimize",
})


def enrich_spec(spec_dict: dict) -> dict:
    """Fill missing operationId / summary / tags on exported OpenAPI paths."""
    import re

    paths = spec_dict.get("paths") or {}

    def _camel(name: str) -> str:
        parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").split()
        if not parts:
            return name
        return parts[0].lower() + "".join(p.title() for p in parts[1:])

    def _title(name: str) -> str:
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").strip()
        return s[:1].upper() + s[1:] if s else name

    _http_tail = re.compile(
        r"\.\s*(GET|POST|PUT|PATCH|DELETE)\s+/",
        re.IGNORECASE,
    )

    def _short_summary(raw: str, fallback: str) -> str:
        text = (raw or "").strip()
        if not text:
            return fallback
        first_line = text.split("\n", 1)[0].strip()
        m = _http_tail.search(first_line)
        if m:
            first_line = first_line[: m.start()].strip().rstrip(".")
        if "Body:" in first_line:
            first_line = first_line.split("Body:", 1)[0].strip().rstrip(".")
        if len(first_line) > 80:
            return fallback
        return first_line or fallback

    def _normalize_operation_docs(op: dict) -> None:
        fallback = _title(op.get("operationId") or "")
        raw_summary = (op.get("summary") or "").strip()
        raw_desc = (op.get("description") or "").strip()

        bloated = (
            len(raw_summary) > 80
            or "Body:" in raw_summary
            or _http_tail.search(raw_summary)
            or "\n" in raw_summary
        )

        if bloated:
            op["summary"] = _short_summary(raw_summary, fallback)
            if raw_summary and raw_summary != op["summary"]:
                op["description"] = (
                    f"{raw_desc}\n\n{raw_summary}".strip()
                    if raw_desc and raw_desc not in raw_summary
                    else raw_summary
                )
        elif not raw_summary:
            op["summary"] = fallback
        elif _http_tail.search(raw_summary):
            op["summary"] = _short_summary(raw_summary, fallback)
            tail = raw_summary[len(op["summary"]) :].lstrip(". ")
            if tail:
                op["description"] = f"{raw_desc}\n\n{tail}".strip() if raw_desc else tail

        responses = op.get("responses") or {}
        if isinstance(responses, dict) and "default" in responses:
            responses.pop("default", None)

    for path, item in paths.items():
        tag = None
        for prefix, t in _PREFIX_TAGS:
            if path.startswith(prefix):
                tag = t
                break
        if path in ("/", "/health", "/api/health"):
            tag = "Health"
        if path.startswith("/api/indicator") and "/backtest" in path:
            tag = "Backtest"

        for method, op in item.items():
            if method.startswith("x-") or not isinstance(op, dict):
                continue
            if not op.get("operationId"):
                # Prefer explicit operationId; fall back to path-derived id.
                slug = re.sub(r"[^a-zA-Z0-9]+", "_", path.strip("/")).strip("_")
                op["operationId"] = _camel(f"{method}_{slug}")
            if not op.get("summary"):
                op["summary"] = _title(op["operationId"])
            _normalize_operation_docs(op)
            if tag:
                op["tags"] = [tag]
            responses = op.setdefault("responses", {})
            has_success = any(str(code).startswith(("2", "3")) for code in responses)
            if not has_success:
                if path in _SSE_PATHS:
                    responses["200"] = {
                        "description": "Server-Sent Events stream",
                        "content": {
                            "text/event-stream": {
                                "schema": {"type": "string"},
                            }
                        },
                    }
                else:
                    responses["200"] = {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/HumanSuccessEnvelope"},
                            }
                        },
                    }
            if "x-visibility" not in op:
                if tag in ("Community", "Market", "Indicator", "Backtest", "Policy", "Auth", "GlobalMarket", "FastAnalysis", "Health"):
                    op["x-visibility"] = "public"
                elif tag in ("Credentials", "QuickTrade", "Billing"):
                    op["x-visibility"] = "private"
                else:
                    op["x-visibility"] = "internal"

    return spec_dict
