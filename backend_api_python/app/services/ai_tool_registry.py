"""Metadata registry for QuantDinger agent tools.

This registry describes system workflows that the AI may plan around. It is not
an execution engine; routes that mutate state still enforce their own auth and
safety checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    category: str
    label_zh: str
    label_en: str
    description_zh: str
    description_en: str
    route: str | None = None
    action: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    risk_level: str = "read"
    read_only: bool = True
    enabled: bool = True
    priority: int = 50
    safety: str = ""

    def pick(self, language: str, zh: str, en: str) -> str:
        return zh if (language or "").lower().startswith("zh") else en

    def to_public(self, language: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "label": self.pick(language, self.label_zh, self.label_en),
            "description": self.pick(language, self.description_zh, self.description_en),
            "route": self.route,
            "action": self.action,
            "parameters": dict(self.parameters or {}),
            "requires": list(self.requires),
            "produces": list(self.produces),
            "risk_level": self.risk_level,
            "read_only": self.read_only,
            "enabled": self.enabled,
            "priority": self.priority,
            "safety": self.safety,
        }

    def prompt_line(self, language: str) -> str:
        label = self.pick(language, self.label_zh, self.label_en)
        description = self.pick(language, self.description_zh, self.description_en)
        requires = ", ".join(self.requires) if self.requires else "none"
        produces = ", ".join(self.produces) if self.produces else "tool result"
        safety = f" Safety: {self.safety}" if self.safety else ""
        return (
            f"- {self.id}: {label}. {description} "
            f"Requires: {requires}. Produces: {produces}. "
            f"Risk: {self.risk_level}. Read-only: {self.read_only}.{safety}"
        )


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        id="market_data.lookup",
        category="market",
        label_zh="行情与K线查询",
        label_en="Market data lookup",
        description_zh="查询系统已有数据源中的价格、K线、成交量和基础指标。",
        description_en="Read prices, klines, volume, and basic indicators from configured data sources.",
        route="/api/indicator/kline",
        requires=("market", "symbol"),
        produces=("market_snapshot",),
        risk_level="read",
        priority=100,
    ),
    ToolDefinition(
        id="market_query.plan",
        category="market",
        label_zh="行情需求规划",
        label_en="Market query planning",
        description_zh="把自然语言问题归一为标的、周期、指标、回看长度和数据新鲜度要求。",
        description_en="Normalize a natural-language question into instruments, timeframes, metrics, lookback and freshness requirements.",
        requires=("user_question",),
        produces=("market_query_plan",),
        risk_level="read",
        priority=101,
    ),
    ToolDefinition(
        id="technical_analysis.compute",
        category="market",
        label_zh="技术证据计算",
        label_en="Technical evidence calculation",
        description_zh="基于已完成OHLCV计算指标、枢轴支撑阻力、量价关系与突破确认。",
        description_en="Compute indicators, pivot levels, volume-price evidence and breakout confirmation from closed OHLCV bars.",
        requires=("ohlcv", "market_query_plan"),
        produces=("technical_evidence",),
        risk_level="read",
        priority=99,
    ),
    ToolDefinition(
        id="settings.preflight",
        category="operations",
        label_zh="部署配置检查",
        label_en="Setup preflight",
        description_zh="检查 LLM、数据源、券商账户、积分和通知配置是否可用。",
        description_en="Check LLM, data source, broker, credits, and notification readiness.",
        route="/api/ai/agent/preflight",
        produces=("setup_checklist",),
        risk_level="read",
        priority=96,
    ),
    ToolDefinition(
        id="watchlist.add",
        category="workspace",
        label_zh="添加自选",
        label_en="Add watchlist item",
        description_zh="将用户确认的标的加入自选列表。",
        description_en="Add a user-confirmed symbol to the watchlist.",
        route="/api/market/watchlist/add",
        requires=("market", "symbol"),
        produces=("watchlist_item",),
        risk_level="write_config",
        read_only=False,
        priority=82,
    ),
    ToolDefinition(
        id="scheduled_analysis.create",
        category="automation",
        label_zh="创建定时分析任务",
        label_en="Create scheduled analysis",
        description_zh="在用户确认周期、通知方式和触发条件后创建 AI 定时分析任务。",
        description_en="Create an AI scheduled analysis after interval, notification, and trigger conditions are confirmed.",
        requires=("market", "symbol", "interval", "notification", "conditions"),
        produces=("scheduled_task",),
        risk_level="write_config",
        read_only=False,
        priority=84,
        safety="Ask for missing schedule fields before creating the task.",
    ),
    ToolDefinition(
        id="indicator.generate",
        category="strategy",
        label_zh="生成指标研发草稿",
        label_en="Generate indicator draft",
        description_zh="根据用户确认的需求生成只用于图表展示的 QuantDinger 指标草稿。",
        description_en="Generate a chart-only QuantDinger indicator draft after requirements are confirmed.",
        route="/indicator-ide",
        requires=("indicator_requirements",),
        produces=("indicator_code", "visualization_plan"),
        risk_level="write_draft",
        read_only=False,
        priority=90,
    ),
    ToolDefinition(
        id="script_strategy.generate",
        category="strategy",
        label_zh="生成脚本策略",
        label_en="Generate script strategy",
        description_zh="根据已确认需求生成 Strategy API V2 Python 草稿。",
        description_en="Generate a Strategy API V2 Python draft after requirements are confirmed.",
        route="/strategy-ide?tab=script",
        requires=("strategy_requirements",),
        produces=("strategy_source", "backtest_plan"),
        risk_level="write_draft",
        read_only=False,
        priority=88,
    ),
)


MCP_AGENT_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        id="mcp.whoami",
        category="mcp",
        label_zh="MCP 身份检查",
        label_en="MCP whoami",
        description_zh="检查当前 Agent Token 身份、权限范围和租户边界。",
        description_en="Inspect current Agent Token identity, scopes, and tenant boundary.",
        route="/api/agent/v1/whoami",
        produces=("token_identity",),
        risk_level="read",
        priority=99,
    ),
    ToolDefinition(
        id="mcp.check_health",
        category="mcp",
        label_zh="MCP 健康检查",
        label_en="MCP health check",
        description_zh="检查 Agent Gateway 和 MCP 连接是否可用。",
        description_en="Check whether Agent Gateway and MCP connectivity are available.",
        route="/api/agent/v1/health",
        produces=("health_status",),
        risk_level="read",
        priority=98,
    ),
    ToolDefinition(
        id="mcp.list_markets",
        category="market",
        label_zh="列出市场",
        label_en="List markets",
        description_zh="列出 Agent Token 允许访问的市场。",
        description_en="List markets allowed by the Agent Token.",
        route="/api/agent/v1/markets",
        produces=("market_list",),
        risk_level="read",
        priority=95,
    ),
    ToolDefinition(
        id="mcp.search_symbols",
        category="market",
        label_zh="搜索标的",
        label_en="Search symbols",
        description_zh="在指定市场里搜索代码、名称和别名。",
        description_en="Search tickers, names, and aliases in a market.",
        route="/api/agent/v1/markets/{market}/symbols",
        requires=("market", "query"),
        produces=("symbol_candidates",),
        risk_level="read",
        priority=94,
    ),
    ToolDefinition(
        id="mcp.get_klines",
        category="market",
        label_zh="读取K线",
        label_en="Get klines",
        description_zh="读取 OHLCV K线，用于分析、指标、回测准备。",
        description_en="Read OHLCV bars for analysis, indicators, and backtest preparation.",
        route="/api/agent/v1/klines",
        requires=("market", "symbol", "timeframe"),
        produces=("klines",),
        risk_level="read",
        priority=94,
    ),
    ToolDefinition(
        id="mcp.get_price",
        category="market",
        label_zh="读取实时价格",
        label_en="Get price",
        description_zh="读取标的最新价格快照。",
        description_en="Read the latest symbol price snapshot.",
        route="/api/agent/v1/price",
        requires=("market", "symbol"),
        produces=("price_snapshot",),
        risk_level="read",
        priority=93,
    ),
    ToolDefinition(
        id="mcp.list_strategies",
        category="strategy",
        label_zh="列出策略",
        label_en="List strategies",
        description_zh="读取当前用户策略列表，敏感字段会被隐藏。",
        description_en="Read current user's strategy list with secrets redacted.",
        route="/api/agent/v1/strategies",
        produces=("strategy_list",),
        risk_level="read",
        priority=87,
    ),
    ToolDefinition(
        id="mcp.get_strategy",
        category="strategy",
        label_zh="读取策略详情",
        label_en="Get strategy",
        description_zh="读取单个策略详情，敏感字段会被隐藏。",
        description_en="Read one strategy with secrets redacted.",
        route="/api/agent/v1/strategies/{strategy_id}",
        requires=("strategy_id",),
        produces=("strategy_detail",),
        risk_level="read",
        priority=86,
    ),
    ToolDefinition(
        id="mcp.runtime_overview",
        category="strategy",
        label_zh="运行概览",
        label_en="Runtime overview",
        description_zh="读取当前租户的运行中策略、持仓、挂单和模拟订单概览。",
        description_en="Read running strategies, positions, pending orders, and paper-order overview.",
        route="/api/agent/v1/runtime/overview",
        produces=("runtime_overview",),
        risk_level="read",
        priority=88,
    ),
    ToolDefinition(
        id="mcp.stop_strategy",
        category="strategy",
        label_zh="停止策略",
        label_en="Stop strategy",
        description_zh="停止当前租户名下的一条策略，需要 T 权限和明确确认。",
        description_en="Stop one tenant-owned strategy; requires T scope and explicit confirmation.",
        route="/api/agent/v1/strategies/{strategy_id}/stop",
        requires=("strategy_id", "confirm_stop"),
        produces=("stopped_strategy",),
        risk_level="runtime_control",
        read_only=False,
        priority=87,
        safety="Only stop strategies after explicit user approval. This does not place orders.",
    ),
    ToolDefinition(
        id="mcp.place_quick_order",
        category="trading",
        label_zh="快捷下单",
        label_en="Place quick order",
        description_zh="通过 Agent Gateway 提交快捷订单；默认模拟，实盘需要 T 权限、非 paper token、服务器实盘开关和明确确认。",
        description_en="Submit a quick order through Agent Gateway; paper by default, live only with T scope, a non-paper token, server live flag, and explicit confirmation.",
        route="/api/agent/v1/quick-trade/orders",
        requires=("market", "symbol", "side", "qty", "confirm_order"),
        produces=("quick_order_result",),
        risk_level="trading",
        read_only=False,
        priority=89,
        safety="Requires explicit user approval. Live trading also requires confirm_live_trading=true and server-side live enablement.",
    ),
    ToolDefinition(
        id="mcp.create_strategy",
        category="strategy",
        label_zh="创建停止状态策略",
        label_en="Create stopped strategy",
        description_zh="从已保存的 Strategy API V2 源码创建默认 stopped 状态的部署；不会自动启动。",
        description_en="Create a stopped deployment from a saved Strategy API V2 source; never auto-starts.",
        route="/api/agent/v1/strategies",
        requires=("name", "sourceId", "initialCapital"),
        produces=("stopped_strategy",),
        risk_level="write_draft",
        read_only=False,
        priority=90,
        safety="Created strategies must remain stopped until the user starts them manually.",
    ),
    ToolDefinition(
        id="mcp.update_strategy",
        category="strategy",
        label_zh="更新策略草稿",
        label_en="Update strategy draft",
        description_zh="更新 Strategy API V2 部署配置；运行状态不是此接口允许的字段。",
        description_en="Patch Strategy API V2 deployment settings; runtime status is not an accepted field.",
        route="/api/agent/v1/strategies/{strategy_id}",
        requires=("strategy_id", "patch"),
        produces=("updated_strategy",),
        risk_level="write_draft",
        read_only=False,
        priority=86,
        safety="Do not use this as a live-start path.",
    ),
    ToolDefinition(
        id="mcp.get_strategy_authoring_contract",
        category="strategy",
        label_zh="读取 Strategy API V2 开发契约",
        label_en="Get Strategy API V2 authoring contract",
        description_zh="读取策略源码归属、运行时 API、禁止项和 starter template。",
        description_en="Read the source-ownership rules, runtime APIs, forbidden patterns, and starter template.",
        route="/api/agent/v1/strategy-sources/authoring-contract",
        produces=("strategy_contract",),
        risk_level="read",
        priority=96,
    ),
    ToolDefinition(
        id="mcp.list_strategy_templates",
        category="strategy",
        label_zh="List Strategy V2 templates",
        label_en="List Strategy V2 templates",
        description_zh="Read system Strategy API V2 templates and starter source code.",
        description_en="Read system Strategy API V2 templates and starter source code.",
        route="/api/agent/v1/strategy-sources/templates",
        produces=("strategy_templates",),
        risk_level="read",
        priority=94,
    ),
    ToolDefinition(
        id="mcp.compile_strategy_code",
        category="strategy",
        label_zh="Compile Strategy V2 source",
        label_en="Compile Strategy V2 source",
        description_zh="Compile Strategy API V2 source and return its canonical manifest without saving.",
        description_en="Compile Strategy API V2 source and return its canonical manifest without saving.",
        route="/api/agent/v1/strategy-sources/compile",
        requires=("code_or_source_id",),
        produces=("strategy_manifest",),
        risk_level="write_draft",
        read_only=False,
        priority=93,
    ),
    ToolDefinition(
        id="mcp.list_strategy_sources",
        category="strategy",
        label_zh="List Strategy V2 sources",
        label_en="List Strategy V2 sources",
        description_zh="List tenant-owned Strategy API V2 sources without code bodies.",
        description_en="List tenant-owned Strategy API V2 sources without code bodies.",
        route="/api/agent/v1/strategy-sources",
        produces=("strategy_source_list",),
        risk_level="read",
        priority=91,
    ),
    ToolDefinition(
        id="mcp.get_strategy_source",
        category="strategy",
        label_zh="Get Strategy V2 source",
        label_en="Get Strategy V2 source",
        description_zh="Read one tenant-owned Strategy API V2 source including its code when visible.",
        description_en="Read one tenant-owned Strategy API V2 source including its code when visible.",
        route="/api/agent/v1/strategy-sources/{source_id}",
        requires=("source_id",),
        produces=("strategy_source",),
        risk_level="read",
        priority=90,
    ),
    ToolDefinition(
        id="mcp.save_strategy_source",
        category="strategy",
        label_zh="Save Strategy V2 source",
        label_en="Save Strategy V2 source",
        description_zh="Compile and save a private Strategy API V2 source with an immutable version snapshot.",
        description_en="Compile and save a private Strategy API V2 source with an immutable version snapshot.",
        route="/api/agent/v1/strategy-sources",
        requires=("name", "code"),
        produces=("strategy_source", "source_version"),
        risk_level="write_draft",
        read_only=False,
        priority=92,
        safety="Sources remain private drafts and deployments remain stopped until separately created.",
    ),
    ToolDefinition(
        id="mcp.list_strategy_source_versions",
        category="strategy",
        label_zh="List source versions",
        label_en="List source versions",
        description_zh="List immutable snapshots for one Strategy API V2 source.",
        description_en="List immutable snapshots for one Strategy API V2 source.",
        route="/api/agent/v1/strategy-sources/{source_id}/versions",
        requires=("source_id",),
        produces=("strategy_source_versions",),
        risk_level="read",
        priority=86,
    ),
    ToolDefinition(
        id="mcp.restore_strategy_source_version",
        category="strategy",
        label_zh="Restore source version",
        label_en="Restore source version",
        description_zh="Restore a prior Strategy API V2 source snapshot and create a new version.",
        description_en="Restore a prior Strategy API V2 source snapshot and create a new version.",
        route="/api/agent/v1/strategy-sources/{source_id}/versions/{version_id}/restore",
        requires=("source_id", "version_id", "confirm_restore"),
        produces=("restored_strategy_source",),
        risk_level="write_draft",
        read_only=False,
        priority=85,
        safety="Requires explicit user approval because the current draft is overwritten.",
    ),
    ToolDefinition(
        id="mcp.get_indicator_authoring_contract",
        category="indicator",
        label_zh="读取指标开发契约",
        label_en="Get indicator authoring contract",
        description_zh="读取指标 IDE 的输入输出契约和 starter template。",
        description_en="Read Indicator IDE I/O contract and starter template.",
        route="/api/agent/v1/indicators/authoring-contract",
        produces=("indicator_contract",),
        risk_level="read",
        priority=92,
    ),
    ToolDefinition(
        id="mcp.validate_indicator_code",
        category="indicator",
        label_zh="验证指标代码",
        label_en="Validate indicator code",
        description_zh="在保存前验证指标 Python 代码，不产生持久写入。",
        description_en="Validate indicator Python code before saving, without persistence.",
        route="/api/agent/v1/indicators/validate",
        requires=("indicator_code",),
        produces=("validation_report",),
        risk_level="read",
        priority=91,
    ),
    ToolDefinition(
        id="mcp.save_indicator",
        category="indicator",
        label_zh="保存指标",
        label_en="Save indicator",
        description_zh="将验证后的指标保存到指标库。",
        description_en="Persist a validated indicator to the indicator library.",
        route="/api/agent/v1/indicators",
        requires=("indicator_code", "name"),
        produces=("indicator",),
        risk_level="write_draft",
        read_only=False,
        priority=88,
    ),
    ToolDefinition(
        id="mcp.list_indicators",
        category="indicator",
        label_zh="列出指标",
        label_en="List indicators",
        description_zh="读取当前用户指标列表。",
        description_en="Read current user's indicator list.",
        route="/api/agent/v1/indicators",
        produces=("indicator_list",),
        risk_level="read",
        priority=85,
    ),
    ToolDefinition(
        id="mcp.get_indicator",
        category="indicator",
        label_zh="读取指标",
        label_en="Get indicator",
        description_zh="读取单个指标及其代码。",
        description_en="Read one indicator and its code.",
        route="/api/agent/v1/indicators/{indicator_id}",
        requires=("indicator_id",),
        produces=("indicator_detail",),
        risk_level="read",
        priority=84,
    ),
    ToolDefinition(
        id="mcp.submit_backtest",
        category="backtest",
        label_zh="提交回测",
        label_en="Submit backtest",
        description_zh="提交异步 Strategy API V2 回测任务；标的、市场和周期由源码声明。",
        description_en="Submit an asynchronous Strategy API V2 backtest; the source owns instruments, market, and frequency.",
        route="/api/agent/v1/backtest/run",
        requires=("code", "startDate", "endDate"),
        produces=("backtest_job",),
        risk_level="write_draft",
        read_only=False,
        priority=89,
        safety="Backtest only; no orders are placed.",
    ),
    ToolDefinition(
        id="mcp.list_jobs",
        category="jobs",
        label_zh="列出任务",
        label_en="List jobs",
        description_zh="列出近期异步任务。",
        description_en="List recent async jobs.",
        route="/api/agent/v1/jobs",
        produces=("job_list",),
        risk_level="read",
        priority=78,
    ),
    ToolDefinition(
        id="mcp.get_job",
        category="jobs",
        label_zh="读取任务",
        label_en="Get job",
        description_zh="读取单个异步任务状态和产物。",
        description_en="Read one async job status and artifacts.",
        route="/api/agent/v1/jobs/{job_id}",
        requires=("job_id",),
        produces=("job_status",),
        risk_level="read",
        priority=78,
    ),
    ToolDefinition(
        id="mcp.wait_for_job",
        category="jobs",
        label_zh="等待任务完成",
        label_en="Wait for job",
        description_zh="在硬超时范围内轮询回测任务，直到成功、失败、取消或超时。",
        description_en="Poll a backtest job within a hard timeout until it succeeds, fails, is cancelled, or times out.",
        route="/api/agent/v1/jobs/{job_id}",
        requires=("job_id",),
        produces=("job_status",),
        risk_level="read",
        priority=77,
        safety="Polling duration and interval must be capped.",
    ),
    ToolDefinition(
        id="mcp.stream_job_until_done",
        category="jobs",
        label_zh="流式跟踪任务",
        label_en="Stream job until done",
        description_zh="有边界地消费任务 SSE 进度，直到完成或超时。",
        description_en="Consume bounded job SSE progress until done or timeout.",
        route="/api/agent/v1/jobs/{job_id}/stream",
        requires=("job_id",),
        produces=("job_progress",),
        risk_level="read",
        priority=77,
        safety="Event count and duration must be capped.",
    ),
    ToolDefinition(
        id="mcp.list_portfolio_positions",
        category="portfolio",
        label_zh="列出组合持仓",
        label_en="List portfolio positions",
        description_zh="读取手动组合持仓，用于监控和风险分析。",
        description_en="Read manual portfolio positions for monitoring and risk analysis.",
        route="/api/agent/v1/portfolio/positions",
        produces=("positions",),
        risk_level="read",
        priority=76,
    ),
    ToolDefinition(
        id="mcp.list_paper_orders",
        category="portfolio",
        label_zh="列出模拟订单",
        label_en="List paper orders",
        description_zh="读取近期 paper orders，用于策略验证和审计。",
        description_en="Read recent paper orders for strategy validation and audit.",
        route="/api/agent/v1/portfolio/paper-orders",
        produces=("paper_orders",),
        risk_level="read",
        priority=75,
    ),
    ToolDefinition(
        id="mcp.cancel_open_paper_orders",
        category="trading",
        label_zh="Agent trading emergency stop",
        label_en="Agent trading emergency stop",
        description_zh="Compatibility alias for the tenant emergency stop: cancel agent orders and revoke T tokens.",
        description_en="Compatibility alias for the tenant emergency stop: cancel agent orders and revoke T tokens.",
        route="/api/agent/v1/quick-trade/kill-switch",
        requires=("confirm_cancel",),
        produces=("emergency_stop_result",),
        risk_level="runtime_control",
        read_only=False,
        priority=88,
        safety="Requires explicit user approval. Review every reported live cancellation failure.",
    ),
)


_MCP_EXTENSION_ROUTES = {
    "emergency_stop_trading": "/api/agent/v1/quick-trade/kill-switch",
    "cancel_job": "/api/agent/v1/jobs/{job_id}/cancel",
    "link_indicator_config": "/api/agent/v1/indicators/link-config",
    "list_universes": "/api/agent/v1/research/universes",
    "get_universe": "/api/agent/v1/research/universes/{universe_id}",
    "list_universe_members": "/api/agent/v1/research/universes/{universe_id}/members",
    "list_factors": "/api/agent/v1/research/factors",
    "get_factor": "/api/agent/v1/research/factors/{factor_id}",
    "list_watchlist": "/api/agent/v1/research/watchlist",
    "add_watchlist": "/api/agent/v1/research/watchlist",
    "remove_watchlist": "/api/agent/v1/research/watchlist",
    "list_trading_accounts": "/api/agent/v1/trading/accounts",
    "get_account_snapshot": "/api/agent/v1/trading/accounts/{credential_id}/snapshot",
    "list_account_positions": "/api/agent/v1/trading/accounts/{credential_id}/positions",
    "list_strategy_positions": "/api/agent/v1/trading/strategies/{strategy_id}/positions",
    "list_strategy_trades": "/api/agent/v1/trading/strategies/{strategy_id}/trades",
    "list_strategy_pending_orders": "/api/agent/v1/trading/strategies/{strategy_id}/pending-orders",
    "list_agent_quick_trades": "/api/agent/v1/trading/quick-trades",
    "list_signal_alerts": "/api/agent/v1/notifications/signal-alerts",
    "create_signal_alert": "/api/agent/v1/notifications/signal-alerts",
    "update_signal_alert": "/api/agent/v1/notifications/signal-alerts/{task_id}",
    "set_signal_alert_status": "/api/agent/v1/notifications/signal-alerts/{task_id}/status",
    "delete_signal_alert": "/api/agent/v1/notifications/signal-alerts/{task_id}",
    "run_signal_alert": "/api/agent/v1/notifications/signal-alerts/{task_id}/run",
}
_MCP_EXTENSION_WRITES = {
    "emergency_stop_trading",
    "cancel_job",
    "link_indicator_config",
    "add_watchlist",
    "remove_watchlist",
    "create_signal_alert",
    "update_signal_alert",
    "set_signal_alert_status",
    "delete_signal_alert",
    "run_signal_alert",
}
MCP_AGENT_TOOLS = MCP_AGENT_TOOLS + tuple(
    ToolDefinition(
        id=f"mcp.{name}",
        category=(
            "research" if name in {
                "list_universes", "get_universe", "list_universe_members",
                "list_factors", "get_factor", "list_watchlist", "add_watchlist",
                "remove_watchlist",
            }
            else "notifications" if "signal_alert" in name
            else "jobs" if name == "cancel_job"
            else "indicator" if name == "link_indicator_config"
            else "trading"
        ),
        label_zh=name.replace("_", " "),
        label_en=name.replace("_", " "),
        description_zh=f"Agent Gateway MCP tool: {name}.",
        description_en=f"Agent Gateway MCP tool: {name}.",
        route=route,
        produces=("tool_result",),
        risk_level="write_config" if name in _MCP_EXTENSION_WRITES else "read",
        read_only=name not in _MCP_EXTENSION_WRITES,
        priority=80,
        safety=(
            "Mutating calls require an Idempotency-Key; destructive or delivery actions also require explicit confirmation."
            if name in _MCP_EXTENSION_WRITES
            else ""
        ),
    )
    for name, route in _MCP_EXTENSION_ROUTES.items()
)


def list_tools(language: str = "zh-CN") -> list[dict[str, Any]]:
    items = sorted((tool for tool in (*TOOLS, *MCP_AGENT_TOOLS) if tool.enabled), key=lambda tool: (-tool.priority, tool.id))
    return [tool.to_public(language) for tool in items]


def build_tool_prompt(language: str = "zh-CN", intent: str = "") -> str:
    intent_text = (intent or "").lower()
    tools = [tool for tool in (*TOOLS, *MCP_AGENT_TOOLS) if tool.enabled]
    if intent_text:
        targeted = [
            tool for tool in tools
            if tool.category in intent_text or any(token in tool.id for token in intent_text.split("_"))
        ]
        if targeted:
            tools = targeted
    tools = sorted(tools, key=lambda tool: (-tool.priority, tool.id))[:8]
    lines = [
        "[QuantDinger tool registry]",
        "These are available system workflows. Treat write tools as user-confirmed handoffs, not autonomous execution.",
        "Live trading boundary: strategy deployments are created stopped. Quick live orders require explicit user confirmation, T scope, a live-capable token, and server-side enablement.",
    ]
    lines.extend(tool.prompt_line(language) for tool in tools)
    return "\n".join(lines)


def public_tool_registry(language: str = "zh-CN") -> dict[str, Any]:
    categories: dict[str, int] = {}
    for tool in (*TOOLS, *MCP_AGENT_TOOLS):
        if tool.enabled:
            categories[tool.category] = categories.get(tool.category, 0) + 1
    return {
        "version": "2026.07.31.1",
        "total": sum(categories.values()),
        "categories": categories,
        "tools": list_tools(language),
    }
