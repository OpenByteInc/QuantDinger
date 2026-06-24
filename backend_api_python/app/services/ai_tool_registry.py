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
        route="/api/market",
        requires=("market", "symbol"),
        produces=("market_snapshot",),
        risk_level="read",
        priority=100,
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
        label_zh="生成策略研发草稿",
        label_en="Generate Strategy Lab draft",
        description_zh="根据用户确认的需求生成可落地到 QuantDinger 策略 IDE 的策略草稿。",
        description_en="Generate a QuantDinger Strategy IDE draft after requirements are confirmed.",
        route="/indicator-ide",
        requires=("strategy_requirements",),
        produces=("indicator_code", "backtest_plan"),
        risk_level="write_draft",
        read_only=False,
        priority=90,
    ),
    ToolDefinition(
        id="script_strategy.generate",
        category="strategy",
        label_zh="生成脚本策略",
        label_en="Generate script strategy",
        description_zh="根据用户确认的需求生成 Python ScriptStrategy 草稿。",
        description_en="Generate a Python ScriptStrategy draft after requirements are confirmed.",
        route="/strategy-script",
        requires=("strategy_requirements",),
        produces=("script_strategy_code", "backtest_plan"),
        risk_level="write_draft",
        read_only=False,
        priority=88,
    ),
    ToolDefinition(
        id="trading_bot.create_stopped",
        category="strategy",
        label_zh="创建停止状态机器人",
        label_en="Create stopped trading bot",
        description_zh="创建默认停止状态的交易机器人配置，启动必须由用户手动点击。",
        description_en="Create a stopped trading bot configuration; only the user can start it manually.",
        route="/trading-bot",
        requires=("bot_parameters", "risk_limits"),
        produces=("stopped_bot_config",),
        risk_level="write_draft",
        read_only=False,
        priority=86,
        safety="Never start live trading automatically.",
    ),
    ToolDefinition(
        id="backtest.run",
        category="research",
        label_zh="运行回测",
        label_en="Run backtest",
        description_zh="对策略草稿运行回测并返回收益、回撤、交易次数和日志。",
        description_en="Run a backtest for a strategy draft and return returns, drawdown, trades, and logs.",
        requires=("strategy_code", "symbol", "timeframe"),
        produces=("backtest_report",),
        risk_level="write_draft",
        read_only=False,
        priority=80,
        safety="Backtest only; never place orders.",
    ),
    ToolDefinition(
        id="logs.inspect",
        category="operations",
        label_zh="日志排查",
        label_en="Inspect logs",
        description_zh="读取策略、机器人或接口日志，定位异常原因和修复步骤。",
        description_en="Inspect strategy, bot, or API logs to identify causes and fixes.",
        requires=("logs_or_error",),
        produces=("debug_report",),
        risk_level="read",
        priority=78,
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
        id="mcp.create_strategy",
        category="strategy",
        label_zh="创建停止状态策略",
        label_en="Create stopped strategy",
        description_zh="创建默认 stopped 状态策略，可附带保存指标；不会自动启动。",
        description_en="Create a strategy in stopped status by default, optionally saving an indicator; never auto-starts.",
        route="/api/agent/v1/strategies",
        requires=("strategy_payload",),
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
        description_zh="更新策略字段；Agent Gateway 会阻止普通 Agent 将状态改成 running。",
        description_en="Patch strategy fields; Agent Gateway blocks ordinary agents from setting status=running.",
        route="/api/agent/v1/strategies/{strategy_id}",
        requires=("strategy_id", "patch"),
        produces=("updated_strategy",),
        risk_level="write_draft",
        read_only=False,
        priority=86,
        safety="Do not use this as a live-start path.",
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
        description_zh="提交异步回测任务，支持 strict_mode、策略配置和指标参数。",
        description_en="Submit an async backtest with strict_mode, strategy config, and indicator parameters.",
        route="/api/agent/v1/backtests",
        requires=("strategy_config", "market", "symbol", "timeframe"),
        produces=("backtest_job",),
        risk_level="write_draft",
        read_only=False,
        priority=89,
        safety="Backtest only; no orders are placed.",
    ),
    ToolDefinition(
        id="mcp.regime_detect",
        category="backtest",
        label_zh="市场状态检测",
        label_en="Regime detect",
        description_zh="同步检测市场状态，用于选择策略和过滤交易环境。",
        description_en="Synchronously detect market regime for strategy selection and environment filters.",
        route="/api/agent/v1/experiments/regime/detect",
        requires=("market_data",),
        produces=("regime_report",),
        risk_level="read",
        priority=83,
    ),
    ToolDefinition(
        id="mcp.submit_experiment_pipeline",
        category="backtest",
        label_zh="提交实验流水线",
        label_en="Submit experiment pipeline",
        description_zh="提交实验流水线任务，用于批量实验和策略比较。",
        description_en="Submit experiment pipeline jobs for batch experiments and strategy comparison.",
        route="/api/agent/v1/experiments/pipeline",
        requires=("experiment_config",),
        produces=("experiment_job",),
        risk_level="write_draft",
        read_only=False,
        priority=79,
    ),
    ToolDefinition(
        id="mcp.submit_structured_tune",
        category="backtest",
        label_zh="提交结构化调参",
        label_en="Submit structured tune",
        description_zh="提交网格/随机等结构化参数搜索任务。",
        description_en="Submit structured grid/random parameter search jobs.",
        route="/api/agent/v1/experiments/structured-tune",
        requires=("parameter_space",),
        produces=("tuning_job",),
        risk_level="write_draft",
        read_only=False,
        priority=82,
    ),
    ToolDefinition(
        id="mcp.submit_ai_optimize",
        category="backtest",
        label_zh="提交 AI 优化",
        label_en="Submit AI optimize",
        description_zh="提交 LLM 辅助优化任务，必须显式确认 LLM 用量。",
        description_en="Submit LLM-assisted optimization; requires explicit LLM usage confirmation.",
        route="/api/agent/v1/experiments/ai-optimize",
        requires=("strategy", "confirm_llm_usage"),
        produces=("ai_optimization_job",),
        risk_level="write_draft",
        read_only=False,
        priority=80,
        safety="Requires confirm_llm_usage=true.",
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
        "Live trading boundary: AI may create draft/stopped strategies or bots only; starting live execution must be a manual user action.",
    ]
    lines.extend(tool.prompt_line(language) for tool in tools)
    return "\n".join(lines)


def public_tool_registry(language: str = "zh-CN") -> dict[str, Any]:
    categories: dict[str, int] = {}
    for tool in (*TOOLS, *MCP_AGENT_TOOLS):
        if tool.enabled:
            categories[tool.category] = categories.get(tool.category, 0) + 1
    return {
        "version": "2026.06.15.1",
        "total": sum(categories.values()),
        "categories": categories,
        "tools": list_tools(language),
    }
