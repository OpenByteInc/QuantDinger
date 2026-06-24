"""Central registry for QuantDinger Copilot skills.

The registry is intentionally metadata-first. It gives the UI, prompts, and
future tool-calling layer one shared source of truth for what the assistant can
do, what each skill requires, and where the user can continue the workflow.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillText:
    zh: str
    en: str

    def pick(self, language: str) -> str:
        return self.zh if (language or "").lower().startswith("zh") else self.en


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    category: str
    icon: str
    label: SkillText
    description: SkillText
    prompt_template: SkillText
    system_instruction: str
    keywords: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    route: str | None = None
    action_type: str = "prompt"
    risk_level: str = "read"
    read_only: bool = True
    priority: int = 50
    ui: dict[str, Any] = field(default_factory=dict)

    def to_public(self, language: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "icon": self.icon,
            "label": self.label.pick(language),
            "description": self.description.pick(language),
            "prompt": self.prompt_template.pick(language),
            "requires": list(self.requires),
            "produces": list(self.produces),
            "route": self.route,
            "action_type": self.action_type,
            "risk_level": self.risk_level,
            "read_only": self.read_only,
            "priority": self.priority,
            "ui": dict(self.ui or {}),
        }

    def prompt_line(self, language: str) -> str:
        requires = ", ".join(self.requires) if self.requires else "none"
        produces = ", ".join(self.produces) if self.produces else "assistant response"
        return (
            f"- {self.id}: {self.label.pick(language)}. "
            f"{self.description.pick(language)} "
            f"Requires: {requires}. Produces: {produces}. "
            f"Risk: {self.risk_level}. Instruction: {self.system_instruction}"
        )


def _s(zh: str, en: str) -> SkillText:
    return SkillText(zh=zh, en=en)


REGISTRY_VERSION = "2026.06.15.2"
USER_SKILLS_DIR = Path("/app/data/ai_skills")
_SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_\-]{2,63}$")


_SKILLS: tuple[SkillDefinition, ...] = (
    SkillDefinition(
        id="market_diagnosis",
        category="research",
        icon="line-chart",
        label=_s("诊断标的", "Diagnose"),
        description=_s("趋势、量能、支撑阻力、资金面和风险", "Trend, volume, levels, capital flow, and risk"),
        prompt_template=_s(
            "请基于 {symbol_label} 做一份专业投研诊断：先给当前结论，再分析趋势、量能、关键支撑阻力、资金面、交易计划和风险。数据不足时请说明缺口并给出条件化判断。",
            "Analyze {symbol_label} professionally. Start with the current read, then cover trend, volume, key support/resistance, capital flow, trading plan, and risks. If data is missing, state the gap and give conditional conclusions.",
        ),
        system_instruction="Use QuantDinger market snapshots first; do not invent live data; include triggers, invalidation, and risk controls.",
        keywords=("走势", "趋势", "行情", "支撑", "阻力", "trend", "support", "resistance", "market"),
        requires=("market_data",),
        produces=("market_report", "risk_plan"),
        priority=100,
        ui={"tone": "analysis"},
    ),
    SkillDefinition(
        id="chart_review",
        category="research",
        icon="picture",
        label=_s("看图诊断", "Chart review"),
        description=_s("粘贴或上传K线图，判断入场、止损和失效条件", "Paste or upload a chart for entry, stop, and invalidation review"),
        prompt_template=_s(
            "我会粘贴或上传一张K线图。请结合图形结构、趋势位置、量能、支撑阻力和风险收益比，判断当前是否适合入场，并给出止损、止盈和失效条件。",
            "I will paste or upload a chart. Review structure, trend location, volume, support/resistance, and risk/reward, then decide whether entry is appropriate with stop, take-profit, and invalidation conditions.",
        ),
        system_instruction="When an image is attached, prioritize visible chart evidence and separate observations from inference.",
        keywords=("截图", "K线图", "图", "chart", "screenshot", "image"),
        requires=("image_or_chart",),
        produces=("chart_report",),
        priority=95,
        ui={"tone": "chart"},
    ),
    SkillDefinition(
        id="indicator_strategy",
        category="strategy",
        icon="line-chart",
        label=_s("策略研发", "Strategy Lab"),
        description=_s("从标的上下文出发，生成可落地到策略 IDE 的研究、代码和回测方案", "Develop a strategy workflow from the selected market context, including research, code draft, and backtest plan"),
        prompt_template=_s(
            "请基于 {symbol_label} 做一次策略研发。我的想法/偏好：\n交易周期：\n风险偏好：\n希望利用的信号或逻辑：\n不希望出现的行为：\n请先理解需求并补充关键问题；如信息足够，再生成适合 QuantDinger 策略 IDE 的草稿和回测验证步骤。",
            "Run strategy research for {symbol_label}. My preferences:\nTimeframe:\nRisk profile:\nSignals or logic I want:\nBehaviors to avoid:\nFirst understand the requirement and ask key questions; if enough information is available, generate a QuantDinger Strategy IDE draft and backtest validation steps.",
        ),
        system_instruction="Use the QuantDinger Strategy IDE workflow. Prefer IndicatorStrategy code for signal-based strategies; never output Pine Script unless explicitly requested.",
        keywords=("指标", "indicator", "ide", "图表策略"),
        requires=("market_data", "strategy_requirements"),
        produces=("indicator_code", "backtest_plan"),
        route="/indicator-ide",
        action_type="strategy",
        risk_level="write_draft",
        read_only=False,
        priority=90,
        ui={"tone": "strategy", "workflow": "indicator"},
    ),
    SkillDefinition(
        id="script_strategy",
        category="strategy",
        icon="code",
        label=_s("脚本策略", "Script strategy"),
        description=_s("生成 Python ScriptStrategy，适合复杂逻辑和自动执行", "Generate Python ScriptStrategy for complex automated logic"),
        prompt_template=_s(
            "请为 {symbol_label} 设计一个脚本策略。我的想法/偏好：\n交易周期：\n风险偏好：\n信号逻辑：\n仓位/止损/止盈规则：\n请先和我确认需求，再生成 QuantDinger Python ScriptStrategy。",
            "Design a Python ScriptStrategy for {symbol_label}. Preferences:\nTimeframe:\nRisk profile:\nSignal logic:\nSizing/stop/take-profit rules:\nConfirm requirements with me before generating QuantDinger Python ScriptStrategy code.",
        ),
        system_instruction="Use QuantDinger ScriptStrategy contracts and include parameter, risk, and test notes.",
        keywords=("脚本策略", "python", "scriptstrategy", "自动策略"),
        requires=("market_data", "strategy_requirements"),
        produces=("script_strategy_code", "backtest_plan"),
        route="/strategy-script",
        action_type="strategy",
        risk_level="write_draft",
        read_only=False,
        priority=88,
        ui={"tone": "strategy", "workflow": "script"},
    ),
    SkillDefinition(
        id="trading_bot_plan",
        category="strategy",
        icon="robot",
        label=_s("交易机器人", "Trading bot"),
        description=_s("推荐网格、趋势、DCA等机器人参数和风控", "Recommend grid, trend, DCA, and bot parameters with risk controls"),
        prompt_template=_s(
            "请基于 {symbol_label} 帮我设计一个交易机器人方案。请先问我资金规模、风险偏好、运行周期、是否允许加仓和最大回撤限制，再给参数建议。",
            "Design a trading bot plan for {symbol_label}. First ask about capital, risk profile, runtime horizon, whether averaging down is allowed, and max drawdown limit before suggesting parameters.",
        ),
        system_instruction="Do not place orders. Produce parameter suggestions, risk limits, and a handoff action to Trading Bot.",
        keywords=("机器人", "网格", "dca", "bot", "grid", "martingale"),
        requires=("market_data", "risk_profile"),
        produces=("bot_plan", "risk_limits"),
        route="/trading-bot",
        action_type="strategy",
        risk_level="write_draft",
        read_only=False,
        priority=86,
        ui={"tone": "strategy", "workflow": "bot"},
    ),
    SkillDefinition(
        id="scheduled_analysis",
        category="automation",
        icon="clock-circle",
        label=_s("定时跟踪", "Scheduled scan"),
        description=_s("按周期自动复盘标的变化并保存结果", "Track a symbol on a schedule and save results"),
        prompt_template=_s(
            "请帮我创建一个AI定时分析任务。我想跟踪的标的是 {symbol_label}。请先问我周期、通知方式和重点关注条件，然后整理成可提交的任务配置。",
            "Help me create an AI scheduled analysis task for {symbol_label}. First ask for interval, notification channel, and monitor conditions, then prepare a submit-ready task config.",
        ),
        system_instruction="Never guess missing schedule fields. Ask for interval, notification channel, and conditions before creating the task.",
        keywords=("定时", "提醒", "监控", "通知", "schedule", "alert", "monitor"),
        requires=("market_data", "notification_preference"),
        produces=("scheduled_task_config",),
        action_type="workflow",
        risk_level="write_config",
        read_only=False,
        priority=84,
        ui={"tone": "monitor"},
    ),
    SkillDefinition(
        id="watchlist_manage",
        category="workspace",
        icon="star",
        label=_s("添加自选", "Add watch"),
        description=_s("加入右侧自选列表并持续观察", "Track it in the watchlist"),
        prompt_template=_s(
            "请把 {symbol_label} 加入自选，并说明我后续应该重点观察哪些条件。",
            "Add {symbol_label} to the watchlist and explain what conditions I should monitor next.",
        ),
        system_instruction="If the symbol is missing, ask the user to search or mention one. Do not fake a symbol.",
        keywords=("自选", "watchlist", "关注"),
        requires=("symbol",),
        produces=("watchlist_action",),
        action_type="addWatch",
        risk_level="write_config",
        read_only=False,
        priority=82,
        ui={"tone": "watch"},
    ),
    SkillDefinition(
        id="debug_logs",
        category="operations",
        icon="bug",
        label=_s("排查日志", "Debug logs"),
        description=_s("定位策略、机器人、接口异常", "Find strategy, bot, or API failures"),
        prompt_template=_s(
            "我会粘贴策略、交易机器人或接口日志。请帮我定位异常原因，说明影响范围，并给出可执行的修复步骤。",
            "I will paste strategy, bot, or API logs. Find the root cause, explain impact, and suggest actionable fixes.",
        ),
        system_instruction="Identify symptom, likely cause, impact, fix steps, and how to verify. Ask for missing logs when needed.",
        keywords=("日志", "报错", "错误", "debug", "error", "exception", "bug"),
        requires=("logs_or_error",),
        produces=("debug_report",),
        priority=80,
        ui={"tone": "debug"},
    ),
    SkillDefinition(
        id="setup_doctor",
        category="operations",
        icon="tool",
        label=_s("配置检查", "Setup check"),
        description=_s("检查 LLM、数据源、券商、积分和通知配置", "Check LLM, data source, broker, credits, and notifications"),
        prompt_template=_s(
            "请帮我检查 QuantDinger 当前部署是否完整：LLM/API Key、数据源、券商账户、积分余额、网络代理、通知配置。请按问题现象、可能原因、配置入口、验证方式输出。",
            "Check whether my QuantDinger deployment is complete: LLM/API key, data source, broker account, credits, proxy, and notifications. Output symptoms, causes, where to configure, and how to verify.",
        ),
        system_instruction="Use preflight status when present. Give direct navigation actions for missing setup.",
        keywords=("配置", "部署", "key", "api", "数据源", "券商", "setup", "preflight"),
        requires=("system_status",),
        produces=("setup_checklist",),
        route="/settings",
        action_type="workflow",
        priority=78,
        ui={"tone": "setup"},
    ),
    SkillDefinition(
        id="opportunity_radar",
        category="research",
        icon="radar-chart",
        label=_s("机会雷达", "Radar"),
        description=_s("筛选未来24小时可能触发的机会条件", "Find 24h opportunity triggers"),
        prompt_template=_s(
            "请基于 {symbol_label} 当前上下文，判断未来24小时是否有交易机会，并给出触发条件、失效条件和需要重点观察的数据。",
            "Based on {symbol_label}, judge whether there is a trading opportunity in the next 24 hours and list trigger conditions, invalidation, and data to watch.",
        ),
        system_instruction="Prefer actionable triggers over broad commentary; include bull/base/bear scenarios.",
        keywords=("机会", "雷达", "扫描", "opportunity", "radar", "scan"),
        requires=("market_data",),
        produces=("opportunity_plan",),
        priority=76,
        ui={"tone": "radar"},
    ),
)


_EXTRA_SKILLS: tuple[SkillDefinition, ...] = (
    SkillDefinition(
        id="market_scanner",
        category="research",
        icon="radar-chart",
        label=_s("市场扫描", "Market scanner"),
        description=_s("按涨跌幅、波动率、成交量和趋势结构筛选机会", "Screen opportunities by change, volatility, volume, and trend structure"),
        prompt_template=_s(
            "请基于系统可用数据源做一次市场扫描。范围：{symbol_label} 或我指定的市场；请输出候选标的、触发条件、风险点、数据缺口和下一步动作。",
            "Run a market scan using available data sources. Scope: {symbol_label} or my requested market. Return candidates, triggers, risks, data gaps, and next actions.",
        ),
        system_instruction="Use available market data and be explicit about unsupported universes or missing feeds.",
        keywords=("扫描", "筛选", "机会", "排行", "scanner", "screen", "ranking"),
        requires=("market_data",),
        produces=("screening_list", "opportunity_plan"),
        priority=94,
    ),
    SkillDefinition(
        id="symbol_resolver",
        category="data",
        icon="search",
        label=_s("标的识别", "Symbol resolver"),
        description=_s("从自然语言里识别市场、代码、别名和数据源上下文", "Resolve market, symbol, aliases, and data context from natural language"),
        prompt_template=_s(
            "请从我的问题中识别真实标的和市场类型。如果不确定，请列出候选项并说明需要我确认什么；不要把当前下拉框标的强行当成我的问题标的。",
            "Resolve the true symbol and market from my request. If ambiguous, list candidates and ask what must be confirmed; do not force the selected dropdown symbol onto the request.",
        ),
        system_instruction="Prefer user message entities over UI selection. Ask only when ambiguity cannot be resolved safely.",
        keywords=("代码", "标的", "股票", "币", "symbol", "ticker", "resolve"),
        requires=("user_message", "symbol_search"),
        produces=("resolved_symbol",),
        priority=93,
    ),
    SkillDefinition(
        id="data_source_doctor",
        category="data",
        icon="database",
        label=_s("数据源诊断", "Data source doctor"),
        description=_s("检查行情、K线、成交量、财务和新闻数据源可用性", "Check price, kline, volume, financial, and news feed readiness"),
        prompt_template=_s(
            "请检查 QuantDinger 当前数据源配置是否足够支撑我的任务。请按行情、K线、成交量、财务、新闻、衍生品数据分别说明可用性、缺口、配置入口和验证方式。",
            "Check whether current QuantDinger data sources can support my task. Cover price, klines, volume, fundamentals, news, and derivatives data with gaps, config entry, and verification.",
        ),
        system_instruction="Use preflight and data warnings when present. Do not mask missing feeds.",
        keywords=("数据源", "行情源", "K线", "成交量", "data source", "feed"),
        requires=("system_status",),
        produces=("data_readiness_report",),
        route="/settings?group=data_source",
        priority=92,
    ),
    SkillDefinition(
        id="market_data_lookup",
        category="data",
        icon="stock",
        label=_s("行情数据查询", "Market data lookup"),
        description=_s("查询价格、K线、成交量和周期快照，作为分析、回测和策略设计的输入", "Query price, klines, volume, and timeframe snapshots for analysis, backtesting, and strategy design"),
        prompt_template=_s(
            "请使用 QuantDinger 系统数据源查询 {symbol_label} 的行情上下文。优先读取最新价格、1H/4H/1D K线、成交量、关键高低点和数据缺口；如果标的不明确，请先识别候选标的，不要直接套用下拉框。",
            "Use QuantDinger data sources to retrieve market context for {symbol_label}. Prefer latest price, 1H/4H/1D klines, volume, key highs/lows, and data gaps. If the symbol is ambiguous, resolve candidates first instead of forcing the dropdown symbol.",
        ),
        system_instruction="Use system market data before answering price, kline, or trend questions. If data is unavailable, explain the missing source and guide setup.",
        keywords=("行情", "价格", "实时价格", "K线", "k线", "K线数据", "成交量", "ohlcv", "kline", "klines", "price", "market data", "quote"),
        requires=("market", "symbol", "timeframe"),
        produces=("market_snapshot", "klines", "data_gap_report"),
        route="/market-data",
        action_type="workflow",
        risk_level="read",
        priority=96,
    ),
    SkillDefinition(
        id="entity_discovery",
        category="research",
        icon="search",
        label=_s("标的/公司发现", "Entity discovery"),
        description=_s("从自然语言识别公司、私有企业、股票代码、别名、IPO/SPAC 和相关可交易标的", "Resolve companies, private entities, tickers, aliases, IPO/SPAC context, and tradable proxies from natural language"),
        prompt_template=_s(
            "请从我的问题里识别真实研究对象。你需要先判断它是上市公司、私有公司、代币、基金、指数、外汇、期货还是宏观事件；如果不是直接可交易标的，请说明原因，并给出相关可交易标的、新闻来源和后续确认问题。",
            "Resolve the true research object from my question. First classify whether it is a public company, private company, token, fund, index, forex, futures, or macro event. If it is not directly tradable, explain why and provide related tradable proxies, news sources, and confirmation questions.",
        ),
        system_instruction="Prefer natural-language entity resolution over the selected dropdown symbol. Explain private-company or ticker ambiguity clearly.",
        keywords=("spac", "spacex", "ipo", "上市", "私有", "未上市", "公司", "ticker", "symbol", "valuation", "entity"),
        requires=("user_message", "symbol_search", "web_search"),
        produces=("entity_resolution", "tradable_proxy_list", "data_gap_report"),
        action_type="workflow",
        risk_level="read",
        priority=98,
    ),
    SkillDefinition(
        id="news_research",
        category="research",
        icon="global",
        label=_s("新闻/事件检索", "News research"),
        description=_s("联网检索公司、资产、宏观事件和行业新闻，形成可引用的投研上下文", "Search company, asset, macro, and sector news to build cited research context"),
        prompt_template=_s(
            "请围绕我的问题做新闻和事件检索，优先使用最近、可信、与交易相关的信息。输出时要区分事实、市场解读和不确定性，并列出关键来源标题。",
            "Search news and events around my question, prioritizing recent, credible, trade-relevant information. Separate facts, market interpretation, and uncertainty, and list key source titles.",
        ),
        system_instruction="Use search results when available. Do not overstate snippets as verified facts; cite source titles/domains briefly.",
        keywords=("新闻", "消息", "事件", "影响", "为什么", "latest", "news", "headline", "event", "impact"),
        requires=("web_search"),
        produces=("news_context", "event_summary", "source_list"),
        action_type="workflow",
        risk_level="read",
        priority=97,
    ),
    SkillDefinition(
        id="macro_economic_data",
        category="macro",
        icon="global",
        label=_s("宏观经济数据", "Macro economic data"),
        description=_s("查询非农、CPI、FOMC、利率、GDP、PCE 等全球宏观事件和市场影响", "Retrieve NFP, CPI, FOMC, rates, GDP, PCE, and global macro events with market impact"),
        prompt_template=_s(
            "请查询与我的问题相关的宏观经济数据或日历事件。先说明数据是否可用、来源、公布时间、实际值/预期值/前值；如果字段缺失，说明缺什么，并给出对美元、美债、美股、黄金、BTC 等资产的影响框架。",
            "Retrieve macro data or calendar events relevant to my question. State availability, source, release time, actual/forecast/previous values; if fields are missing, say what is missing and provide an impact framework for DXY, yields, equities, gold, BTC, and related assets.",
        ),
        system_instruction="Use economic calendar context first. If exact macro values are missing, provide the missing fields and setup guidance instead of hallucinating.",
        keywords=("非农", "cpi", "fomc", "fed", "利率", "pce", "gdp", "就业", "失业", "宏观", "nfp", "payroll", "inflation"),
        requires=("economic_calendar", "web_search"),
        produces=("macro_context", "asset_impact_map", "data_gap_report"),
        action_type="workflow",
        risk_level="read",
        priority=96,
    ),
    SkillDefinition(
        id="indicator_authoring",
        category="strategy",
        icon="function",
        label=_s("指标开发契约", "Indicator authoring"),
        description=_s("读取指标 IDE 输入输出契约，生成符合平台规范的指标", "Use the Indicator IDE contract to author compliant indicators"),
        prompt_template=_s(
            "请先读取 QuantDinger 指标开发契约，再帮我设计指标。必须说明输入、输出、参数、绘图元素、信号标记和验证步骤。",
            "Read the QuantDinger indicator authoring contract first, then design the indicator. Cover inputs, outputs, parameters, drawings, signals, and validation steps.",
        ),
        system_instruction="Follow the platform authoring contract before generating code.",
        keywords=("指标开发", "authoring", "contract", "绘图", "信号标记"),
        requires=("indicator_contract", "requirements"),
        produces=("indicator_design", "indicator_code"),
        route="/indicator-ide",
        action_type="strategy",
        risk_level="write_draft",
        read_only=False,
        priority=91,
    ),
    SkillDefinition(
        id="indicator_validation",
        category="strategy",
        icon="check-circle",
        label=_s("指标代码验证", "Indicator validation"),
        description=_s("保存前验证指标代码大小、语法、输出契约和安全边界", "Validate indicator code size, syntax, output contract, and safety boundaries before saving"),
        prompt_template=_s(
            "请帮我验证这段指标代码是否符合 QuantDinger 指标 IDE 规范。请指出语法问题、契约问题、运行风险和修改建议。",
            "Validate this indicator code against QuantDinger Indicator IDE rules. Report syntax issues, contract mismatches, runtime risks, and fixes.",
        ),
        system_instruction="Use validation before suggesting save or backtest.",
        keywords=("验证", "检查代码", "validate", "lint", "代码质量"),
        requires=("indicator_code",),
        produces=("validation_report",),
        action_type="workflow",
        risk_level="read",
        priority=89,
    ),
    SkillDefinition(
        id="strategy_requirements_interview",
        category="strategy",
        icon="form",
        label=_s("策略需求访谈", "Strategy interview"),
        description=_s("在写代码前补齐周期、市场、风控、仓位和执行约束", "Collect timeframe, market, risk, sizing, and execution constraints before coding"),
        prompt_template=_s(
            "请像专业量化 PM 一样先访谈我的策略需求。必须问清：交易市场、周期、方向、多空、仓位、止损止盈、过滤条件、回测区间、上线边界。不要直接写代码。",
            "Interview me like a quant PM before coding. Clarify market, timeframe, direction, long/short, sizing, stops, filters, backtest range, and launch boundary. Do not write code yet.",
        ),
        system_instruction="Clarify missing requirements before generating any runnable strategy.",
        keywords=("策略需求", "先问", "访谈", "requirements", "clarify"),
        requires=("user_goal",),
        produces=("strategy_brief",),
        priority=87,
    ),
    SkillDefinition(
        id="backtest_runner",
        category="backtest",
        icon="experiment",
        label=_s("回测执行", "Backtest runner"),
        description=_s("提交回测任务并解释收益、回撤、胜率、交易和偏差", "Submit backtests and explain returns, drawdown, win rate, trades, and bias"),
        prompt_template=_s(
            "请帮我为当前策略准备回测。先确认标的、周期、时间范围、初始资金、手续费、滑点和参数，然后提交回测或给出可提交配置。",
            "Prepare a backtest for the strategy. Confirm symbol, timeframe, date range, capital, fees, slippage, and parameters, then submit or produce a submit-ready config.",
        ),
        system_instruction="Use strict mode when possible and explain data limitations.",
        keywords=("回测", "backtest", "收益", "回撤", "胜率"),
        requires=("strategy", "market_data", "backtest_config"),
        produces=("backtest_report",),
        action_type="workflow",
        risk_level="write_draft",
        read_only=False,
        priority=88,
    ),
    SkillDefinition(
        id="parameter_tuning",
        category="backtest",
        icon="control",
        label=_s("参数调优", "Parameter tuning"),
        description=_s("运行结构化调参、网格/随机搜索和稳健性比较", "Run structured tuning, grid/random search, and robustness comparison"),
        prompt_template=_s(
            "请帮我为策略设计参数调优实验。请给出参数范围、目标函数、过拟合控制、样本内/样本外划分、稳健性判断和最终候选参数。",
            "Design a tuning experiment for the strategy. Provide parameter ranges, objective, overfit controls, in/out-of-sample split, robustness checks, and final candidates.",
        ),
        system_instruction="Warn about overfitting and require out-of-sample validation before live use.",
        keywords=("调参", "优化", "网格", "随机", "tune", "optimize"),
        requires=("strategy", "parameter_space"),
        produces=("tuning_plan", "candidate_params"),
        action_type="workflow",
        risk_level="write_draft",
        read_only=False,
        priority=84,
    ),
    SkillDefinition(
        id="regime_detection",
        category="backtest",
        icon="partition",
        label=_s("市场状态识别", "Regime detection"),
        description=_s("识别趋势、震荡、高波动、低波动等状态并选择策略", "Detect trend, range, high-vol, and low-vol regimes for strategy selection"),
        prompt_template=_s(
            "请对 {symbol_label} 做市场状态识别，输出当前状态、证据、适配策略、不适配策略、状态切换触发条件。",
            "Detect the market regime for {symbol_label}. Return current regime, evidence, suitable strategies, unsuitable strategies, and regime-switch triggers.",
        ),
        system_instruction="Use regime tools when available and map regimes to strategy choices.",
        keywords=("市场状态", "震荡", "趋势", "regime", "volatility"),
        requires=("market_data",),
        produces=("regime_report",),
        priority=83,
    ),
    SkillDefinition(
        id="portfolio_monitoring",
        category="portfolio",
        icon="fund",
        label=_s("持仓监控", "Portfolio monitoring"),
        description=_s("监控手动持仓、盈亏、止损止盈、风险暴露和提醒", "Monitor manual positions, PnL, stops, exposure, and alerts"),
        prompt_template=_s(
            "请帮我设计持仓监控方案。请确认持仓标的、成本、数量、风险阈值、提醒方式，并输出监控条件和异常处理流程。",
            "Design a portfolio monitoring plan. Confirm positions, cost, size, risk thresholds, notification channel, then output monitor conditions and escalation flow.",
        ),
        system_instruction="Do not assume broker positions. Separate manual portfolio from live broker state.",
        keywords=("持仓", "资产", "组合", "监控", "portfolio", "position"),
        requires=("positions", "risk_thresholds"),
        produces=("monitor_config",),
        route="/portfolio",
        action_type="workflow",
        risk_level="write_config",
        read_only=False,
        priority=82,
    ),
    SkillDefinition(
        id="risk_guard",
        category="risk",
        icon="safety",
        label=_s("风控闸门", "Risk guard"),
        description=_s("检查仓位、止损、杠杆、最大回撤、熔断和上线条件", "Check sizing, stops, leverage, max drawdown, kill switch, and launch readiness"),
        prompt_template=_s(
            "请对我的策略/机器人做上线前风控检查。请输出风险评分、必须补齐项、最大仓位、止损止盈、熔断条件、禁止启动条件和上线检查清单。",
            "Run a pre-launch risk review for my strategy/bot. Return risk score, missing requirements, max size, stops, kill switch, no-go conditions, and launch checklist.",
        ),
        system_instruction="Be conservative. Never recommend starting live execution automatically.",
        keywords=("风控", "止损", "杠杆", "熔断", "risk", "kill switch"),
        requires=("strategy_or_bot", "risk_profile"),
        produces=("risk_checklist",),
        priority=92,
    ),
    SkillDefinition(
        id="broker_account_doctor",
        category="broker",
        icon="bank",
        label=_s("券商账户检查", "Broker account doctor"),
        description=_s("检查交易所/券商账户连接、市场权限、密钥和实盘前置条件", "Check exchange/broker connection, market permission, keys, and live prerequisites"),
        prompt_template=_s(
            "请检查我的券商/交易所账户是否满足任务要求。请说明连接状态、支持市场、权限、缺失配置、测试方式和实盘前注意事项。",
            "Check whether my broker/exchange account supports the task. Cover connection status, markets, permissions, missing config, tests, and live precautions.",
        ),
        system_instruction="Do not expose secrets. Redact credentials and separate paper/live capability.",
        keywords=("券商", "交易所", "API Key", "broker", "exchange", "account"),
        requires=("broker_status",),
        produces=("broker_readiness_report",),
        route="/broker-accounts",
        priority=81,
    ),
    SkillDefinition(
        id="agent_token_advisor",
        category="operations",
        icon="api",
        label=_s("Agent Token 顾问", "Agent token advisor"),
        description=_s("为外部 AI、MCP、Cursor 等配置最小权限 Agent Token", "Design least-privilege Agent Tokens for external AI, MCP, Cursor, and agents"),
        prompt_template=_s(
            "请根据我的外部 AI/MCP 使用场景，设计最小权限 Agent Token。请说明需要的 R/W/B/N/T 范围、市场白名单、标的白名单、过期时间和安全注意事项。",
            "Design a least-privilege Agent Token for my external AI/MCP use case. Cover R/W/B/N/T scopes, market allowlist, instrument allowlist, expiry, and safety notes.",
        ),
        system_instruction="Default to no T scope and paper-only unless user explicitly needs more.",
        keywords=("agent token", "MCP", "Cursor", "权限", "令牌"),
        requires=("integration_goal",),
        produces=("token_policy",),
        route="/agent-tokens",
        priority=79,
    ),
    SkillDefinition(
        id="mcp_integration",
        category="integration",
        icon="deployment-unit",
        label=_s("MCP 集成", "MCP integration"),
        description=_s("配置 QuantDinger MCP Server、连接外部 AI 客户端并验证工具", "Configure QuantDinger MCP Server, connect external AI clients, and verify tools"),
        prompt_template=_s(
            "请帮我配置 QuantDinger MCP Server。请根据我的客户端说明环境变量、Agent Token 权限、连接方式、可用工具、验证步骤和常见故障。",
            "Help configure QuantDinger MCP Server. Based on my client, explain env vars, Agent Token scopes, transport, available tools, verification, and troubleshooting.",
        ),
        system_instruction="Keep MCP live trading disabled; explain REST-only live boundary when needed.",
        keywords=("mcp", "server", "外部AI", "Claude", "Cursor"),
        requires=("client_type", "agent_token_policy"),
        produces=("mcp_setup_guide",),
        route="/agent-tokens",
        priority=78,
    ),
    SkillDefinition(
        id="job_monitor",
        category="operations",
        icon="clock-circle",
        label=_s("任务监控", "Job monitor"),
        description=_s("跟踪异步回测、实验、优化任务状态和日志", "Track async backtest, experiment, and optimization jobs with logs"),
        prompt_template=_s(
            "请帮我检查异步任务状态。请读取任务进度、终态、错误原因、输出产物和下一步处理建议。",
            "Check async job status. Read progress, terminal state, error cause, artifacts, and recommended next steps.",
        ),
        system_instruction="Use bounded polling/streaming and summarize failures clearly.",
        keywords=("任务", "进度", "job", "stream", "pipeline"),
        requires=("job_id_or_context",),
        produces=("job_status_report",),
        priority=77,
    ),
    SkillDefinition(
        id="notification_setup",
        category="operations",
        icon="notification",
        label=_s("通知配置", "Notification setup"),
        description=_s("配置站内、邮件、短信、Telegram、Webhook 等通知链路", "Configure in-app, email, SMS, Telegram, webhook notification paths"),
        prompt_template=_s(
            "请帮我配置通知链路。请先确认通知类型、触发条件、静默时段、频率限制和验证方式，再输出配置步骤。",
            "Help configure notification delivery. Confirm channel, trigger conditions, quiet hours, rate limits, and verification before outputting setup steps.",
        ),
        system_instruction="Ask for channel and quiet hours before creating alerting workflows.",
        keywords=("通知", "邮件", "短信", "telegram", "webhook", "提醒"),
        requires=("notification_preference",),
        produces=("notification_config",),
        route="/settings?group=contact_support",
        action_type="workflow",
        risk_level="write_config",
        read_only=False,
        priority=76,
    ),
    SkillDefinition(
        id="audit_and_compliance",
        category="operations",
        icon="audit",
        label=_s("审计与合规", "Audit and compliance"),
        description=_s("检查 Agent 调用、用户行为、策略变更和敏感操作审计", "Review agent calls, user actions, strategy changes, and sensitive-operation audit"),
        prompt_template=_s(
            "请帮我做系统审计检查。请关注 Agent 调用、权限范围、敏感操作、策略变更、异常失败和需要追踪的日志。",
            "Run an audit review. Focus on agent calls, scopes, sensitive operations, strategy changes, failures, and logs to investigate.",
        ),
        system_instruction="Treat audit as admin-only and avoid exposing secrets.",
        keywords=("审计", "日志", "合规", "audit", "security"),
        requires=("admin_context",),
        produces=("audit_report",),
        risk_level="read",
        priority=75,
    ),
    SkillDefinition(
        id="knowledge_memory",
        category="workspace",
        icon="book",
        label=_s("用户记忆", "User memory"),
        description=_s("维护用户偏好、交易约束、常用市场和策略风格", "Maintain user preferences, trading constraints, favorite markets, and strategy style"),
        prompt_template=_s(
            "请从我们的对话中提炼可以长期记住的用户偏好，但必须先让我确认。重点包括交易周期、风险偏好、市场偏好、禁止行为和输出格式偏好。",
            "Extract long-term user preferences from our conversation, but ask for confirmation first. Cover timeframe, risk profile, markets, forbidden behavior, and output format.",
        ),
        system_instruction="Never store sensitive credentials. Ask for user confirmation before saving memory.",
        keywords=("记忆", "偏好", "以后记住", "memory", "preference"),
        requires=("user_confirmation",),
        produces=("memory_candidate",),
        priority=74,
    ),
    SkillDefinition(
        id="system_release_doctor",
        category="operations",
        icon="tool",
        label=_s("发布体检", "Release doctor"),
        description=_s("检查前端、后端、Docker、数据库迁移和关键工作流是否正常", "Check frontend, backend, Docker, migrations, and critical workflows"),
        prompt_template=_s(
            "请帮我做一次 QuantDinger 发布体检。请检查前端、后端、Docker、数据库迁移、LLM、数据源、MCP、回测和实盘安全边界。",
            "Run a QuantDinger release health check. Cover frontend, backend, Docker, migrations, LLM, data sources, MCP, backtesting, and live safety boundaries.",
        ),
        system_instruction="Produce a practical checklist with verification commands and rollback notes.",
        keywords=("发布", "体检", "docker", "部署", "release", "health"),
        requires=("system_status",),
        produces=("release_checklist",),
        priority=73,
    ),
)


def _builtin_ids() -> set[str]:
    return {skill.id for skill in (*_SKILLS, *_EXTRA_SKILLS)}


def _text_from_payload(value: Any, fallback: str = "") -> SkillText:
    if isinstance(value, dict):
        zh = str(value.get("zh") or value.get("zh_CN") or value.get("zh-CN") or value.get("en") or fallback)[:1200]
        en = str(value.get("en") or value.get("en_US") or value.get("en-US") or value.get("zh") or fallback)[:1200]
        return SkillText(zh=zh, en=en)
    text = str(value or fallback)[:1200]
    return SkillText(zh=text, en=text)


def _clean_tuple(value: Any, limit: int = 20) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out = []
    for item in value[:limit]:
        text = str(item or "").strip()
        if text:
            out.append(text[:80])
    return tuple(out)


def _ensure_user_dir() -> None:
    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _skill_path(skill_id: str) -> Path:
    return USER_SKILLS_DIR / f"{skill_id}.json"


def _validate_skill_payload(payload: dict[str, Any], *, updating: bool = False) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload must be an object"
    skill_id = str(payload.get("id") or "").strip()
    if not _SKILL_ID_RE.match(skill_id):
        return False, "skill id must match ^[a-z][a-z0-9_-]{2,63}$"
    if not updating and skill_id in _builtin_ids():
        return False, "builtin skills cannot be overridden"
    kind = str(payload.get("kind") or payload.get("action_type") or "prompt").strip()
    if kind != "prompt":
        return False, "only prompt skills can be installed in this version"
    forbidden = {"code", "script", "python", "shell", "command", "commands", "exec", "runtime", "webhook"}
    if any(key in payload for key in forbidden):
        return False, "prompt skills cannot include executable fields"
    risk_level = str(payload.get("risk_level") or "read").strip()
    if risk_level not in {"read", "write_draft", "write_config"}:
        return False, "risk_level must be read, write_draft, or write_config"
    label = payload.get("label")
    prompt_template = payload.get("prompt_template") or payload.get("prompt")
    if not label or not prompt_template:
        return False, "label and prompt_template are required"
    return True, "ok"


def _skill_from_payload(payload: dict[str, Any]) -> SkillDefinition:
    action_type = str(payload.get("action_type") or payload.get("kind") or "prompt").strip()
    risk_level = str(payload.get("risk_level") or "read").strip()
    return SkillDefinition(
        id=str(payload.get("id")).strip(),
        category=str(payload.get("category") or "custom").strip()[:48],
        icon=str(payload.get("icon") or "experiment").strip()[:48],
        label=_text_from_payload(payload.get("label")),
        description=_text_from_payload(payload.get("description"), "Custom prompt skill"),
        prompt_template=_text_from_payload(payload.get("prompt_template") or payload.get("prompt")),
        system_instruction=str(payload.get("system_instruction") or "Follow the installed prompt skill and ask for missing requirements.")[:2000],
        keywords=_clean_tuple(payload.get("keywords")),
        requires=_clean_tuple(payload.get("requires")),
        produces=_clean_tuple(payload.get("produces")),
        route=str(payload.get("route") or "").strip()[:160] or None,
        action_type=action_type,
        risk_level=risk_level,
        read_only=bool(payload.get("read_only", risk_level == "read")),
        priority=int(payload.get("priority") or 40),
        ui=payload.get("ui") if isinstance(payload.get("ui"), dict) else {},
    )


def _load_installed_payloads() -> list[dict[str, Any]]:
    try:
        _ensure_user_dir()
    except Exception:
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(USER_SKILLS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            ok, _ = _validate_skill_payload(payload, updating=True)
            if ok:
                payloads.append(payload)
        except Exception:
            continue
    return payloads


def _all_skill_entries(include_disabled: bool = False) -> list[tuple[SkillDefinition, dict[str, Any]]]:
    entries: list[tuple[SkillDefinition, dict[str, Any]]] = []
    for skill in (*_SKILLS, *_EXTRA_SKILLS):
        entries.append((skill, {"source": "builtin", "builtin": True, "enabled": True, "kind": "prompt"}))
    for payload in _load_installed_payloads():
        enabled = bool(payload.get("enabled", True))
        if not enabled and not include_disabled:
            continue
        skill = _skill_from_payload(payload)
        entries.append((
            skill,
            {
                "source": str(payload.get("source") or "user"),
                "builtin": False,
                "enabled": enabled,
                "kind": "prompt",
                "install_source": str(payload.get("install_source") or "manual"),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
            },
        ))
    return entries


def list_skills(language: str = "zh-CN", category: str | None = None, include_disabled: bool = False) -> list[dict[str, Any]]:
    items = [
        (skill, meta)
        for skill, meta in _all_skill_entries(include_disabled=include_disabled)
        if not category or skill.category == category
    ]
    items.sort(key=lambda item: (-item[0].priority, item[0].id))
    result = []
    for skill, meta in items:
        public = skill.to_public(language)
        public.update(meta)
        result.append(public)
    return result


def get_skill(skill_id: str) -> SkillDefinition | None:
    for skill, _ in _all_skill_entries(include_disabled=False):
        if skill.id == skill_id:
            return skill
    return None


def install_prompt_skill(payload: dict[str, Any], install_source: str = "manual") -> dict[str, Any]:
    ok, msg = _validate_skill_payload(payload)
    if not ok:
        raise ValueError(msg)
    _ensure_user_dir()
    skill_id = str(payload["id"]).strip()
    if _skill_path(skill_id).exists():
        raise ValueError("skill already installed")
    now = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    stored = dict(payload)
    stored["kind"] = "prompt"
    stored["enabled"] = bool(stored.get("enabled", True))
    stored["install_source"] = install_source
    stored["created_at"] = now
    stored["updated_at"] = now
    _skill_path(skill_id).write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    return stored


def set_skill_enabled(skill_id: str, enabled: bool) -> dict[str, Any]:
    if skill_id in _builtin_ids():
        raise ValueError("builtin skills cannot be disabled")
    if not _SKILL_ID_RE.match(skill_id or ""):
        raise ValueError("invalid skill id")
    path = _skill_path(skill_id)
    if not path.exists():
        raise FileNotFoundError("skill not found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["enabled"] = bool(enabled)
    payload["updated_at"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def delete_installed_skill(skill_id: str) -> None:
    if skill_id in _builtin_ids():
        raise ValueError("builtin skills cannot be deleted")
    if not _SKILL_ID_RE.match(skill_id or ""):
        raise ValueError("invalid skill id")
    path = _skill_path(skill_id)
    if not path.exists():
        raise FileNotFoundError("skill not found")
    path.unlink()


def render_prompt_template(skill: SkillDefinition, language: str, context: dict[str, Any] | None = None) -> str:
    context = context or {}
    symbol = str(context.get("symbol") or "").strip()
    market = str(context.get("market") or "").strip()
    if symbol and market:
        symbol_label = f"{market}:{symbol}"
    elif symbol:
        symbol_label = symbol
    else:
        symbol_label = "当前标的" if (language or "").lower().startswith("zh") else "the current symbol"
    return skill.prompt_template.pick(language).format(symbol_label=symbol_label)


def match_skills(message: str, intent: str = "", limit: int = 5) -> list[SkillDefinition]:
    text = (message or "").lower()
    intent = (intent or "").lower()
    wants_strategy = any(token in text for token in ("策略", "写策略", "生成策略", "创建策略", "设计策略", "交易策略", "strategy", "bot"))
    wants_market_data = any(token in text for token in ("行情", "价格", "实时价格", "k线", "kline", "klines", "ohlcv", "成交量", "quote", "price"))
    wants_code = any(token in text for token in ("代码", "指标", "脚本", "indicator", "script", "code", "ide"))
    wants_news = any(token in text for token in ("新闻", "消息", "事件", "影响", "为什么", "latest", "news", "headline", "event"))
    wants_macro = any(token in text for token in ("非农", "cpi", "fomc", "fed", "利率", "pce", "gdp", "就业", "失业", "宏观", "nfp", "payroll", "inflation"))
    wants_entity = any(token in text for token in ("spac", "spacex", "ipo", "上市", "私有", "未上市", "ticker", "symbol", "多少钱", "估值"))
    scored: list[tuple[int, SkillDefinition]] = []
    for skill, _ in _all_skill_entries(include_disabled=False):
        score = 0
        if skill.category in intent or skill.id in intent:
            score += 20
        for keyword in skill.keywords:
            if keyword.lower() in text:
                score += 10
        if intent == "market_analysis" and skill.id in {"market_diagnosis", "opportunity_radar"}:
            score += 8
        if intent == "strategy_build" and skill.category == "strategy":
            score += 8
        if intent == "diagnosis" and skill.id in {"debug_logs", "setup_doctor"}:
            score += 8
        if wants_strategy and skill.id == "strategy_requirements_interview":
            score += 22
        if wants_strategy and skill.id in {"indicator_strategy", "script_strategy", "trading_bot_plan"}:
            score += 14
        if wants_code and skill.id in {"indicator_strategy", "script_strategy", "indicator_authoring", "indicator_validation"}:
            score += 12
        if wants_market_data and skill.id == "market_data_lookup":
            score += 24
        if wants_market_data and skill.id in {"market_diagnosis", "symbol_resolver", "data_source_doctor"}:
            score += 8
        if wants_news and skill.id == "news_research":
            score += 24
        if wants_macro and skill.id == "macro_economic_data":
            score += 26
        if wants_entity and skill.id in {"entity_discovery", "symbol_resolver"}:
            score += 24
        if score:
            scored.append((score + skill.priority // 10, skill))
    scored.sort(key=lambda item: (-item[0], -item[1].priority, item[1].id))
    return [skill for _, skill in scored[:limit]]


def build_skill_prompt(language: str, message: str = "", intent: str = "") -> str:
    matched = match_skills(message, intent, limit=6)
    if not matched:
        matched = [skill for skill, _ in sorted(_all_skill_entries(False), key=lambda item: (-item[0].priority, item[0].id))[:6]]
    lines = [
        "[QuantDinger skill registry]",
        "Use these registered skills as the allowed workflow map. Skills describe capabilities, requirements, outputs, and safety level.",
        "When a task matches a skill, follow that skill's instruction and ask for missing requirements instead of inventing them.",
    ]
    lines.extend(skill.prompt_line(language) for skill in matched)
    return "\n".join(lines)


def public_registry(language: str = "zh-CN", include_disabled: bool = False) -> dict[str, Any]:
    categories: dict[str, int] = {}
    entries = _all_skill_entries(include_disabled=include_disabled)
    for skill, _ in entries:
        categories[skill.category] = categories.get(skill.category, 0) + 1
    return {
        "version": REGISTRY_VERSION,
        "total": len(entries),
        "categories": categories,
        "skills": list_skills(language, include_disabled=include_disabled),
    }
