"""AI Copilot chat routes.

The Copilot is intentionally thin: it stores conversations, accepts optional
chart screenshots, charges credits through the central billing service, and
delegates reasoning to the configured LLM provider.
"""
from __future__ import annotations

import json
import math
import re
from io import BytesIO
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from flask import Response, g, jsonify, request, stream_with_context

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.billing_service import get_billing_service
from app.services.ai_skill_registry import (
    build_skill_prompt,
    delete_installed_skill,
    get_skill,
    install_prompt_skill,
    match_skills,
    public_registry,
    render_prompt_template,
    set_skill_enabled,
)
from app.services.ai_tool_registry import build_tool_prompt, public_tool_registry
from app.services.kline import KlineService
from app.services.llm import LLMService
from app.services.search import get_search_service
from app.config.data_sources import AkshareConfig, TradingEconomicsConfig
from app.data.market_symbols_seed import search_symbols as seed_search_symbols
from app.data_providers.macro_series import get_macro_series_provider
from app.data_providers.news import get_economic_calendar_payload
from app.utils.auth import admin_required, login_required
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

ai_chat_blp = Blueprint("ai_chat", __name__)

MAX_IMAGES = 3
MAX_IMAGE_DATA_URL_CHARS = 4 * 1024 * 1024
ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _row_to_dict(row: Any) -> dict:
    return dict(row or {})


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _get_user_memories(cur, user_id: int, limit: int = 12) -> list[dict]:
    try:
        cur.execute(
            """
            SELECT id, category, title, content, confidence, updated_at
            FROM qd_ai_user_memories
            WHERE user_id = ? AND is_active = TRUE
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        rows = cur.fetchall() or []
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"Failed to load user memories: {e}")
        return []


def _detect_memory_candidates(message: str, language: str) -> list[dict]:
    text = (message or "").strip()
    if len(text) < 8:
        return []
    lower = text.lower()
    zh = (language or "").lower().startswith("zh")
    markers = [
        "我偏好", "我的偏好", "我喜欢", "我不喜欢", "不要", "不希望", "风险偏好", "交易周期",
        "timeframe", "risk profile", "i prefer", "i like", "i don't want", "avoid", "do not"
    ]
    if not any(m.lower() in lower for m in markers):
        return []
    title = "交易偏好" if zh else "Trading preference"
    if any(m in lower for m in ("不要", "不希望", "avoid", "don't want", "do not")):
        title = "交易限制" if zh else "Trading constraint"
    return [{
        "category": "preference",
        "title": title,
        "content": text[:500],
        "confidence": 75,
    }]


def _ensure_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qd_ai_copilot_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title VARCHAR(160),
            context_symbol VARCHAR(64),
            context_market VARCHAR(32),
            context_strategy_id INTEGER,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qd_ai_copilot_messages (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role VARCHAR(16) NOT NULL,
            content TEXT NOT NULL,
            attachments_json TEXT,
            actions_json TEXT,
            report_json TEXT,
            report_target_json TEXT,
            report_error TEXT,
            report_error_tone VARCHAR(32),
            intent VARCHAR(48),
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qd_ai_copilot_tool_calls (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            tool_name VARCHAR(64) NOT NULL,
            status VARCHAR(24) NOT NULL,
            input_json TEXT,
            output_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qd_ai_user_memories (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            category VARCHAR(48) NOT NULL DEFAULT 'preference',
            title VARCHAR(160) NOT NULL,
            content TEXT NOT NULL,
            source VARCHAR(48) DEFAULT 'copilot',
            confidence INTEGER DEFAULT 70,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    for ddl in (
        "ALTER TABLE qd_ai_copilot_messages ADD COLUMN IF NOT EXISTS actions_json TEXT",
        "ALTER TABLE qd_ai_copilot_messages ADD COLUMN IF NOT EXISTS report_json TEXT",
        "ALTER TABLE qd_ai_copilot_messages ADD COLUMN IF NOT EXISTS report_target_json TEXT",
        "ALTER TABLE qd_ai_copilot_messages ADD COLUMN IF NOT EXISTS report_error TEXT",
        "ALTER TABLE qd_ai_copilot_messages ADD COLUMN IF NOT EXISTS report_error_tone VARCHAR(32)",
    ):
        try:
            cur.execute(ddl)
        except Exception:
            try:
                cur.execute(ddl.replace(" IF NOT EXISTS", ""))
            except Exception:
                # SQLite compatibility in older local deployments.
                pass
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_qd_ai_copilot_sessions_user ON qd_ai_copilot_sessions(user_id, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_qd_ai_copilot_messages_session ON qd_ai_copilot_messages(session_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_qd_ai_user_memories_user ON qd_ai_user_memories(user_id, is_active, updated_at)",
    ):
        try:
            cur.execute(ddl)
        except Exception:
            pass


def _title_from_message(message: str) -> str:
    title = re.sub(r"\s+", " ", (message or "").strip())
    if not title:
        return "New Copilot Chat"
    return title[:60]


def _detect_intent(message: str, has_image: bool) -> str:
    text = (message or "").lower()
    if has_image:
        return "chart_image_analysis"
    if any(k in text for k in ("非农", "nfp", "cpi", "fomc", "fed", "利率", "就业", "失业", "pce", "gdp", "通胀", "inflation", "payroll")):
        return "market_analysis"
    if any(k in text for k in ("多少钱", "价格", "股价", "估值", "市值", "报价", "现价", "最新价", "quote", "valuation")):
        return "market_analysis"
    if any(k in text for k in ("策略", "indicator", "script", "代码", "code", "write strategy", "生成")):
        return "strategy_build"
    if any(k in text for k in ("诊断", "报错", "错误", "亏损", "日志", "debug", "bug", "why")):
        return "diagnosis"
    if any(k in text for k in ("行情", "走势", "标的", "分析", "price", "market", "trend")):
        return "market_analysis"
    if any(k in text for k in ("雷达", "机会", "扫描", "radar", "opportunity", "scan")):
        return "opportunity_radar"
    return "general"


def _fallback_agent_intent(message: str, has_image: bool, context: dict | None = None) -> dict:
    """Conservative intent fallback used only when the configured LLM is unavailable."""
    text = (message or "").lower()
    base_intent = _detect_intent(message, has_image)
    target_type = "none"
    workflow = "chat"
    should_execute = False
    required_missing: list[str] = []

    if base_intent == "strategy_build":
        should_execute = any(k in text for k in (
            "创建", "生成", "写", "做一个", "能跑", "可运行", "回测", "create",
            "generate", "build", "write", "runnable", "backtest"
        ))
        if any(k in text for k in ("机器人", "bot", "grid", "dca", "martingale", "网格", "马丁")):
            target_type = "bot"
            workflow = "trading_bot"
        elif any(k in text for k in ("脚本", "script", "python")):
            target_type = "script"
            workflow = "script_strategy"
        else:
            target_type = "indicator"
            workflow = "indicator_ide"
    elif base_intent in ("market_analysis", "chart_image_analysis"):
        workflow = "research"

    selected_symbol = (context or {}).get("symbol") or (context or {}).get("resolved_symbol") or ""
    if should_execute and not selected_symbol:
        required_missing.append("symbol")

    return {
        "intent": base_intent,
        "confidence": 45,
        "source": "fallback",
        "should_execute": should_execute and not required_missing,
        "target_type": target_type,
        "workflow": workflow,
        "required_missing": required_missing,
        "entities": {
            "symbol": selected_symbol,
            "market": (context or {}).get("market") or (context or {}).get("resolved_market") or "",
            "timeframe": "",
            "strategy_template": "",
        },
        "skills": [skill.to_public("zh-CN") for skill in match_skills(message, base_intent, limit=5)],
        "next_action": "ask_missing_fields" if required_missing else ("execute_workflow" if should_execute else "answer_chat"),
        "reason": "LLM intent router unavailable; used conservative fallback.",
    }


def _normalize_agent_intent(raw: dict, message: str, has_image: bool, context: dict, language: str) -> dict:
    """Normalize model output into the stable agent router contract."""
    if not isinstance(raw, dict):
        raw = {}
    intent = str(raw.get("intent") or _detect_intent(message, has_image)).strip() or "general"
    allowed_intents = {
        "general", "market_analysis", "chart_image_analysis", "strategy_build",
        "strategy_optimize", "backtest", "monitor_setup", "diagnosis",
        "opportunity_radar", "portfolio", "settings_help"
    }
    if intent not in allowed_intents:
        intent = _detect_intent(message, has_image)

    target_type = str(raw.get("target_type") or "none").strip()
    if target_type not in {"none", "indicator", "script", "bot", "monitor", "research"}:
        target_type = "none"
    workflow = str(raw.get("workflow") or "").strip()
    if workflow not in {"chat", "research", "indicator_ide", "script_strategy", "trading_bot", "scheduled_analysis", "backtest", "debug"}:
        workflow = "chat"
    if intent == "strategy_build" and target_type == "none":
        target_type = "indicator"
        workflow = "indicator_ide"

    entities = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    selected_symbol = context.get("resolved_symbol") or context.get("mentioned_symbol") or context.get("symbol") or context.get("selected_symbol") or ""
    selected_market = context.get("resolved_market") or context.get("mentioned_market") or context.get("market") or context.get("selected_market") or ""
    entities = {
        "symbol": str(entities.get("symbol") or selected_symbol or "").strip(),
        "market": str(entities.get("market") or selected_market or "").strip(),
        "timeframe": str(entities.get("timeframe") or "").strip(),
        "strategy_template": str(entities.get("strategy_template") or "").strip(),
        "asset_class": str(entities.get("asset_class") or "").strip(),
    }

    missing = raw.get("required_missing") if isinstance(raw.get("required_missing"), list) else []
    missing = [str(item).strip() for item in missing if str(item).strip()]
    should_execute = bool(raw.get("should_execute"))
    if should_execute and intent in {"strategy_build", "backtest", "monitor_setup"} and not entities["symbol"]:
        if "symbol" not in missing:
            missing.append("symbol")
        should_execute = False

    matched_skills = [skill.to_public(language) for skill in match_skills(message, intent, limit=6)]
    skills = raw.get("skills") if isinstance(raw.get("skills"), list) else []
    normalized_skill_ids = []
    for item in skills:
        if isinstance(item, str):
            normalized_skill_ids.append(item)
        elif isinstance(item, dict) and item.get("id"):
            normalized_skill_ids.append(str(item.get("id")))
    for skill in matched_skills:
        sid = skill.get("id")
        if sid and sid not in normalized_skill_ids:
            normalized_skill_ids.append(sid)

    return {
        "intent": intent,
        "confidence": max(0, min(100, int(raw.get("confidence") or 50))),
        "source": str(raw.get("source") or "llm").strip() or "llm",
        "should_execute": should_execute,
        "target_type": target_type,
        "workflow": workflow,
        "required_missing": missing,
        "entities": entities,
        "skills": normalized_skill_ids[:8],
        "skill_details": matched_skills,
        "next_action": str(raw.get("next_action") or ("ask_missing_fields" if missing else ("execute_workflow" if should_execute else "answer_chat"))),
        "reason": str(raw.get("reason") or "").strip(),
    }


def _classify_agent_intent(message: str, attachments: list[dict], context: dict, language: str) -> dict:
    """Use the configured LLM as the canonical Agent intent router."""
    has_image = bool(attachments)
    fallback = _fallback_agent_intent(message, has_image, context)
    system_prompt = (
        "You are the QuantDinger Agent Intent Router. Classify the user's message into a "
        "workflow plan for a global quantitative trading terminal. Return JSON only. "
        "Do not answer the user. Decide whether this is chat/research or an executable "
        "workflow such as strategy creation, backtest, scheduled analysis, bot creation, or debugging. "
        "For strategy creation, prefer QuantDinger native workflows: indicator_ide for chart/backtest "
        "strategies, script_strategy for Python ScriptStrategy, trading_bot for bot presets. "
        "If the user asks to create/build/write/generate a runnable strategy and enough target context "
        "is available, set should_execute=true. If required data is missing, list it in required_missing. "
        "Support Chinese, English, and mixed multilingual prompts."
    )
    schema = {
        "intent": fallback["intent"],
        "confidence": 50,
        "source": "llm",
        "should_execute": False,
        "target_type": "none",
        "workflow": "chat",
        "required_missing": [],
        "entities": {
            "symbol": "",
            "market": "",
            "timeframe": "",
            "strategy_template": "",
            "asset_class": "",
        },
        "skills": [],
        "next_action": "answer_chat",
        "reason": "",
    }
    user_prompt = _json_dumps({
        "message": message,
        "has_image": has_image,
        "language": language,
        "selected_context": {
            "market": context.get("market") or context.get("selected_market") or "",
            "symbol": context.get("symbol") or context.get("selected_symbol") or "",
            "resolved_market": context.get("resolved_market") or "",
            "resolved_symbol": context.get("resolved_symbol") or "",
        },
        "available_intents": [
            "general", "market_analysis", "chart_image_analysis", "strategy_build",
            "strategy_optimize", "backtest", "monitor_setup", "diagnosis",
            "opportunity_radar", "portfolio", "settings_help"
        ],
        "available_workflows": [
            "chat", "research", "indicator_ide", "script_strategy", "trading_bot",
            "scheduled_analysis", "backtest", "debug"
        ],
        "available_target_types": ["none", "indicator", "script", "bot", "monitor", "research"],
    })
    try:
        raw = LLMService().safe_call_llm(system_prompt, user_prompt, schema.copy())
        plan = _normalize_agent_intent(raw, message, has_image, context, language)
        report = str(raw.get("report") or "")
        if report.startswith("Analysis failed:") or report.startswith("Failed to parse"):
            fallback["error"] = raw.get("report")
            return fallback
        return plan
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback


def _get_or_classify_agent_intent(message: str, attachments: list[dict], context: dict, language: str) -> dict:
    existing = context.get("agent_intent") if isinstance(context, dict) else None
    if isinstance(existing, dict) and existing.get("intent"):
        return _normalize_agent_intent(existing, message, bool(attachments), context, language)
    return _classify_agent_intent(message, attachments, context, language)

def _normalize_attachments(raw_attachments: Any) -> list[dict]:
    if not raw_attachments:
        return []
    if not isinstance(raw_attachments, list):
        raise ValueError("attachments must be a list")
    if len(raw_attachments) > MAX_IMAGES:
        raise ValueError(f"Only {MAX_IMAGES} images can be attached at once")

    out: list[dict] = []
    for idx, item in enumerate(raw_attachments):
        if not isinstance(item, dict):
            raise ValueError("attachment item must be an object")
        data_url = (item.get("data_url") or "").strip()
        mime_type = (item.get("mime_type") or item.get("mime") or "").strip().lower()
        name = (item.get("name") or f"image-{idx + 1}").strip()[:120]
        if not data_url.startswith("data:image/"):
            raise ValueError("Only data URL images are supported")
        if ";base64," not in data_url:
            raise ValueError("Image must be base64 encoded")
        header = data_url.split(",", 1)[0]
        inferred_mime = header.replace("data:", "").split(";", 1)[0].lower()
        mime_type = mime_type or inferred_mime
        if mime_type not in ALLOWED_IMAGE_MIME:
            raise ValueError("Only PNG, JPEG and WebP images are supported")
        if len(data_url) > MAX_IMAGE_DATA_URL_CHARS:
            raise ValueError("Image is too large; please upload an image under about 3 MB")
        out.append({
            "name": name,
            "mime_type": mime_type,
            "data_url": data_url,
            "size": len(data_url),
        })
    return out


def _attachment_meta(attachments: list[dict]) -> list[dict]:
    stored: list[dict] = []
    for a in attachments:
        item = {
            "name": a.get("name"),
            "mime_type": a.get("mime_type"),
            "size": a.get("size"),
        }
        data_url = a.get("data_url")
        if isinstance(data_url, str) and data_url.startswith("data:image/"):
            item["data_url"] = data_url
        stored.append(item)
    return stored


def _get_session(cur, user_id: int, session_id: int | None) -> dict | None:
    if not session_id:
        return None
    cur.execute(
        "SELECT * FROM qd_ai_copilot_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _create_session(cur, user_id: int, title: str, context: dict) -> int:
    cur.execute(
        """
        INSERT INTO qd_ai_copilot_sessions
        (user_id, title, context_symbol, context_market, context_strategy_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            user_id,
            title,
            (context.get("symbol") or "")[:64],
            (context.get("market") or "")[:32],
            context.get("strategy_id"),
            _now_utc(),
            _now_utc(),
        ),
    )
    row = cur.fetchone()
    return int(row["id"] if isinstance(row, dict) else row[0])


def _insert_message(
    cur,
    session_id: int,
    user_id: int,
    role: str,
    content: str,
    attachments: list[dict],
    intent: str,
    actions: list[dict] | None = None,
    report: dict | None = None,
    report_target: dict | None = None,
    report_error: str | None = None,
    report_error_tone: str | None = None,
) -> int:
    cur.execute(
        """
        INSERT INTO qd_ai_copilot_messages
        (session_id, user_id, role, content, attachments_json, actions_json, report_json, report_target_json, report_error, report_error_tone, intent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            session_id,
            user_id,
            role,
            content,
            _json_dumps(_attachment_meta(attachments)) if attachments else None,
            _json_dumps(actions or []) if actions else None,
            _json_dumps(report) if isinstance(report, dict) and report else None,
            _json_dumps(report_target) if isinstance(report_target, dict) and report_target else None,
            str(report_error or "")[:1000] if report_error else None,
            str(report_error_tone or "")[:32] if report_error_tone else None,
            intent,
            _now_utc(),
        ),
    )
    row = cur.fetchone()
    return int(row["id"] if isinstance(row, dict) else row[0])


def _load_recent_messages(cur, session_id: int, limit: int = 12) -> list[dict]:
    cur.execute(
        """
        SELECT role, content, attachments_json, actions_json, intent, created_at
        FROM qd_ai_copilot_messages
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (session_id, limit),
    )
    rows = cur.fetchall() or []
    return [_row_to_dict(r) for r in reversed(rows)]


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        n = float(value)
        if math.isfinite(n):
            return n
    except Exception:
        pass
    return default


def _round_num(value: Any, digits: int = 4) -> float | None:
    n = _to_float(value)
    if n is None:
        return None
    return round(n, digits)


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    seed = sum(values[:period]) / period
    k = 2 / (period + 1)
    current = seed
    for value in values[period:]:
        current = (value - current) * k + current
    return current


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _timeframe_change(klines: list[dict], bars: int) -> float | None:
    if len(klines) <= bars:
        return None
    start = _to_float(klines[-bars - 1].get("close"))
    end = _to_float(klines[-1].get("close"))
    if not start or end is None:
        return None
    return (end - start) / start * 100


def _summarize_klines(klines: list[dict], timeframe: str) -> dict:
    clean = []
    for k in klines or []:
        close = _to_float(k.get("close"))
        high = _to_float(k.get("high"))
        low = _to_float(k.get("low"))
        open_ = _to_float(k.get("open"))
        volume = _to_float(k.get("volume") or k.get("vol"))
        if close is None or high is None or low is None:
            continue
        clean.append({"time": k.get("time"), "open": open_, "high": high, "low": low, "close": close, "volume": volume})
    if len(clean) < 5:
        return {"timeframe": timeframe, "available": False, "bars": len(clean)}

    closes = [x["close"] for x in clean]
    highs = [x["high"] for x in clean]
    lows = [x["low"] for x in clean]
    volumes = [x["volume"] for x in clean if x["volume"] is not None]
    last = clean[-1]
    ema20 = _ema(closes, 20)
    ema60 = _ema(closes, 60)
    rsi14 = _rsi(closes, 14)
    volume_avg20 = (sum(volumes[-20:]) / len(volumes[-20:])) if volumes else None
    last_volume = last["volume"]
    volume_ratio = (last_volume / volume_avg20) if (last_volume is not None and volume_avg20) else None
    closed_volume_avg20 = (sum(volumes[-21:-1]) / len(volumes[-21:-1])) if len(volumes) >= 21 else None
    prev_closed_volume = volumes[-2] if len(volumes) >= 2 else None
    closed_volume_ratio = (prev_closed_volume / closed_volume_avg20) if (prev_closed_volume is not None and closed_volume_avg20) else None
    true_ranges = []
    for i, bar in enumerate(clean[-15:]):
        prev_close = clean[-16 + i]["close"] if len(clean) >= 16 and i == 0 else clean[max(0, len(clean) - 15 + i - 1)]["close"]
        true_ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - prev_close), abs(bar["low"] - prev_close)))
    atr14 = (sum(true_ranges[-14:]) / len(true_ranges[-14:])) if true_ranges else None
    recent_window = clean[-40:] if len(clean) >= 40 else clean
    support = min(x["low"] for x in recent_window)
    resistance = max(x["high"] for x in recent_window)
    swing_high = max(x["high"] for x in clean[-20:])
    swing_low = min(x["low"] for x in clean[-20:])
    trend = "neutral"
    if ema20 is not None and ema60 is not None:
        if last["close"] > ema20 > ema60:
            trend = "bullish"
        elif last["close"] < ema20 < ema60:
            trend = "bearish"

    return {
        "timeframe": timeframe,
        "available": True,
        "bars": len(clean),
        "latest_time": last.get("time"),
        "last_close": _round_num(last["close"], 6),
        "change_1_bar_pct": _round_num(_timeframe_change(clean, 1), 2),
        "change_6_bar_pct": _round_num(_timeframe_change(clean, 6), 2),
        "change_20_bar_pct": _round_num(_timeframe_change(clean, 20), 2),
        "ema20": _round_num(ema20, 6),
        "ema60": _round_num(ema60, 6),
        "rsi14": _round_num(rsi14, 2),
        "atr14": _round_num(atr14, 6),
        "atr14_pct": _round_num((atr14 / last["close"] * 100) if atr14 and last["close"] else None, 2),
        "recent_support_40": _round_num(support, 6),
        "recent_resistance_40": _round_num(resistance, 6),
        "swing_low_20": _round_num(swing_low, 6),
        "swing_high_20": _round_num(swing_high, 6),
        "last_volume": _round_num(last_volume, 4),
        "volume_avg20": _round_num(volume_avg20, 4),
        "volume_ratio_vs_avg20": _round_num(volume_ratio, 2),
        "prev_closed_volume": _round_num(prev_closed_volume, 4),
        "prev_closed_volume_avg20": _round_num(closed_volume_avg20, 4),
        "prev_closed_volume_ratio_vs_avg20": _round_num(closed_volume_ratio, 2),
        "trend_bias": trend,
    }


def _build_market_snapshot(context: dict) -> dict | None:
    market = (context.get("market") or "").strip()
    symbol = (context.get("symbol") or "").strip()
    if not market or not symbol:
        return None

    service = KlineService()
    snapshot: dict[str, Any] = {
        "symbol": symbol,
        "market": market,
        "generated_at_utc": _now_utc().isoformat(),
        "price": None,
        "timeframes": {},
        "derivatives": {
            "funding_rate": "unavailable",
            "open_interest": "unavailable",
            "note": "Funding rate and open interest are not available from the current public snapshot provider.",
        },
        "data_warnings": [],
    }
    snapshot["data_warnings"].append("Latest candle may be still forming; prefer prev_closed_volume_ratio_vs_avg20 for volume confirmation.")
    try:
        price = service.get_realtime_price(market, symbol, force_refresh=True)
        if price and _to_float(price.get("price")):
            snapshot["price"] = {
                "last": _round_num(price.get("price"), 6),
                "change": _round_num(price.get("change"), 6),
                "change_percent": _round_num(price.get("changePercent"), 2),
                "high": _round_num(price.get("high"), 6),
                "low": _round_num(price.get("low"), 6),
                "open": _round_num(price.get("open"), 6),
                "source": price.get("source"),
            }
    except Exception as e:
        snapshot["data_warnings"].append(f"price unavailable: {e}")

    for timeframe, limit in (("1H", 120), ("4H", 120), ("1D", 120)):
        try:
            klines = service.get_kline(market, symbol, timeframe, limit)
            snapshot["timeframes"][timeframe] = _summarize_klines(klines, timeframe)
        except Exception as e:
            snapshot["timeframes"][timeframe] = {"timeframe": timeframe, "available": False, "error": str(e)}

    return snapshot


_PUBLIC_COMPANY_ALIASES = {
    "spacex": {
        "entity": "SpaceX",
        "market": "private_company",
        "note": "SpaceX is a private company and has no directly traded public stock ticker.",
        "search_terms": ("SPCX", "Space Exploration Technologies", "SpaceX"),
        "related_public_symbols": [
            {"market": "USStock", "symbol": "SPCX", "name": "Space Exploration Technologies Corp"},
            {"market": "USStock", "symbol": "TSLA", "name": "Tesla"},
        ],
    },
    "starlink": {
        "entity": "Starlink",
        "market": "private_business_unit",
        "note": "Starlink is part of SpaceX and does not currently have a standalone public ticker.",
        "search_terms": ("Starlink", "SpaceX"),
        "related_public_symbols": [],
    },
}


_COMMON_ENTITY_ALIASES = (
    {"keys": ("spacex", "space x", "space exploration"), "terms": ("SPCX", "Space Exploration Technologies", "SpaceX"), "symbol": "SPCX", "market": "USStock", "name": "Space Exploration Technologies Corp"},
    {"keys": ("英伟达", "輝達", "nvidia", "nvda"), "terms": ("NVDA", "NVIDIA"), "symbol": "NVDA", "market": "USStock", "name": "NVIDIA Corporation"},
    {"keys": ("博通", "broadcom", "avgo"), "terms": ("AVGO", "Broadcom"), "symbol": "AVGO", "market": "USStock", "name": "Broadcom Inc."},
    {"keys": ("微软", "microsoft", "msft"), "terms": ("MSFT", "Microsoft"), "symbol": "MSFT", "market": "USStock", "name": "Microsoft Corporation"},
    {"keys": ("苹果", "apple", "aapl"), "terms": ("AAPL", "Apple"), "symbol": "AAPL", "market": "USStock", "name": "Apple Inc."},
    {"keys": ("谷歌", "alphabet", "google", "googl", "goog"), "terms": ("GOOGL", "GOOG", "Alphabet", "Google"), "symbol": "GOOGL", "market": "USStock", "name": "Alphabet Inc."},
    {"keys": ("亚马逊", "amazon", "amzn"), "terms": ("AMZN", "Amazon"), "symbol": "AMZN", "market": "USStock", "name": "Amazon.com Inc."},
    {"keys": ("特斯拉", "tesla", "tsla"), "terms": ("TSLA", "Tesla"), "symbol": "TSLA", "market": "USStock", "name": "Tesla Inc."},
    {"keys": ("meta", "facebook", "脸书"), "terms": ("META", "Meta", "Facebook"), "symbol": "META", "market": "USStock", "name": "Meta Platforms Inc."},
    {"keys": ("amd", "超威"), "terms": ("AMD", "Advanced Micro Devices"), "symbol": "AMD", "market": "USStock", "name": "Advanced Micro Devices Inc."},
    {"keys": ("台积电", "臺積電", "tsmc", "tsm"), "terms": ("TSM", "Taiwan Semiconductor"), "symbol": "TSM", "market": "USStock", "name": "Taiwan Semiconductor Manufacturing Co."},
    {"keys": ("阿里巴巴", "alibaba", "baba"), "terms": ("BABA", "Alibaba", "9988"), "symbol": "BABA", "market": "USStock", "name": "Alibaba Group Holding Ltd."},
    {"keys": ("腾讯", "騰訊", "tencent"), "terms": ("0700", "TCEHY", "Tencent"), "symbol": "0700", "market": "HKStock", "name": "Tencent Holdings Ltd."},
    {"keys": ("特朗普媒体", "川普媒体", "trump media", "truth social", "djt"), "terms": ("DJT", "Trump Media"), "symbol": "DJT", "market": "USStock", "name": "Trump Media & Technology Group"},
    {"keys": ("palantir", "pltr", "帕兰提尔"), "terms": ("PLTR", "Palantir"), "symbol": "PLTR", "market": "USStock", "name": "Palantir Technologies Inc."},
    {"keys": ("coinbase", "coin"), "terms": ("COIN", "Coinbase"), "symbol": "COIN", "market": "USStock", "name": "Coinbase Global Inc."},
    {"keys": ("小鹏", "小鵬", "xpeng", "xpev"), "terms": ("XPEV", "9868", "XPeng"), "symbol": "XPEV", "market": "USStock", "name": "XPeng Inc."},
    {"keys": ("理想汽车", "理想汽車", "li auto", "li"), "terms": ("LI", "2015", "Li Auto"), "symbol": "LI", "market": "USStock", "name": "Li Auto Inc."},
    {"keys": ("蔚来", "蔚來", "nio"), "terms": ("NIO", "9866", "NIO"), "symbol": "NIO", "market": "USStock", "name": "NIO Inc."},
    {"keys": ("比亚迪", "比亞迪", "byd"), "terms": ("1211", "BYDDY", "BYD"), "symbol": "1211", "market": "HKStock", "name": "BYD Company Ltd."},
    {"keys": ("茅台", "贵州茅台", "貴州茅台", "moutai"), "terms": ("600519", "Kweichow Moutai"), "symbol": "600519", "market": "CNStock", "name": "Kweichow Moutai"},
    {"keys": ("宁德时代", "寧德時代", "catl"), "terms": ("300750", "CATL"), "symbol": "300750", "market": "CNStock", "name": "CATL"},
)


_TICKER_DISCOVERY_RE = (
    re.compile(r"\b(?:NASDAQ|NYSE|AMEX|NYSEARCA|OTC|HKEX|SEHK|SSE|SZSE)\s*[:：]\s*([A-Z0-9.]{1,8})\b", re.I),
    re.compile(r"\b(?:ticker|symbol)\s*(?:is|:|：)?\s*\$?([A-Z0-9.]{1,8})\b", re.I),
    re.compile(r"\$([A-Z]{1,8})(?:\b|[\/\-\._])"),
)


def _append_symbol_candidate(candidates: list[dict], seen: set[str], row: dict, match: str, source: str) -> bool:
    key = f"{row.get('market')}:{row.get('symbol')}"
    if key in seen:
        return False
    seen.add(key)
    candidates.append({
        "market": row.get("market"),
        "symbol": row.get("symbol"),
        "name": row.get("name") or "",
        "match": match,
        "source": source,
    })
    return True


def _local_symbol_rows_for_term(term: str, per_market_limit: int = 4) -> list[dict]:
    markets = ("USStock", "HKStock", "CNStock", "Crypto", "Forex", "Futures")
    rows: list[dict] = []
    for market in markets:
        try:
            rows.extend(seed_search_symbols(market=market, keyword=term, limit=per_market_limit))
        except Exception:
            continue
    return rows


def _needs_intelligence_context(message: str, intent: str) -> bool:
    text = (message or "").lower()
    hints = (
        "今天", "现在", "最新", "新闻", "消息", "上市", "ipo", "spac", "spacex",
        "宏观", "非农", "cpi", "fomc", "fed", "利率", "财报", "估值", "多少钱",
        "price", "latest", "news", "valuation", "market cap", "earnings",
    )
    return intent in {"market_analysis", "opportunity_radar", "general"} and any(h in text for h in hints)


def _extract_symbol_terms(message: str) -> list[str]:
    text = message or ""
    terms: list[str] = []
    for match in re.finditer(r"\$?([A-Z]{1,8})(?:\b|[\/\-\._])", text):
        token = match.group(1).upper()
        if token not in {"AI", "API", "LLM", "USD", "USDT", "ETF", "IPO", "CEO", "CPI", "GDP", "FOMC"}:
            terms.append(token)
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9\-.]{2,30}", text):
        token = match.group(0).strip()
        if token.lower() not in {"today", "latest", "price", "stock", "market", "news", "analysis"}:
            terms.append(token)
    seen = set()
    out = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            out.append(term)
    return out[:8]


def _alias_expanded_terms(message: str) -> list[str]:
    lower = (message or "").lower()
    expanded_terms: list[str] = []
    for alias in _COMMON_ENTITY_ALIASES:
        if any(str(key).lower() in lower for key in alias.get("keys", ())):
            expanded_terms.extend(str(x) for x in alias.get("terms", ()))
    for alias, info in _PUBLIC_COMPANY_ALIASES.items():
        if alias in lower:
            expanded_terms.extend(str(x) for x in (info.get("search_terms") or ()))
    return expanded_terms


def _alias_direct_candidates(message: str) -> list[dict]:
    lower = (message or "").lower()
    candidates: list[dict] = []
    seen: set[str] = set()
    for alias in _COMMON_ENTITY_ALIASES:
        if not any(str(key).lower() in lower for key in alias.get("keys", ())):
            continue
        market = str(alias.get("market") or "").strip()
        symbol = str(alias.get("symbol") or "").strip()
        if not market or not symbol:
            continue
        key = f"{market}:{symbol}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "market": market,
            "symbol": symbol,
            "name": alias.get("name") or symbol,
            "match": next((str(k) for k in alias.get("keys", ()) if str(k).lower() in lower), symbol),
            "source": "alias_symbol_map",
        })
    return candidates


def _local_symbol_candidates(message: str, limit: int = 8) -> list[dict]:
    terms = _extract_symbol_terms(message)
    lower = (message or "").lower()
    terms = _alias_expanded_terms(message) + terms
    candidates: list[dict] = []
    seen: set[str] = set()
    for term in terms:
        for row in _local_symbol_rows_for_term(term, per_market_limit=4):
            if _append_symbol_candidate(candidates, seen, row, term, "local_symbol_db"):
                if len(candidates) >= limit:
                    return candidates

    for item in _alias_direct_candidates(message):
        key = f"{item.get('market')}:{item.get('symbol')}"
        if key not in seen:
            seen.add(key)
            candidates.append(item)
            if len(candidates) >= limit:
                return candidates

    has_tradable_candidate = any(c.get("symbol") and c.get("market") not in {"private_company", "private_business_unit"} for c in candidates)
    for alias, info in _PUBLIC_COMPANY_ALIASES.items():
        if alias in lower and not has_tradable_candidate:
            candidates.append({
                "market": info["market"],
                "symbol": "",
                "name": info["entity"],
                "match": alias,
                "source": "built_in_entity_alias",
                "note": info["note"],
                "related_public_symbols": info.get("related_public_symbols") or [],
            })
    return candidates[:limit]


def _discover_symbol_candidates_from_search(search_context: dict, existing: list[dict], limit: int = 6) -> list[dict]:
    candidates: list[dict] = []
    seen = {f"{item.get('market')}:{item.get('symbol')}" for item in existing}
    false_positive = {"AI", "API", "CEO", "CFO", "ETF", "IPO", "LLM", "USD", "USDT", "THE", "AND", "FOR"}
    haystack_parts = []
    for item in (search_context.get("web_results") or [])[:8]:
        haystack_parts.append(str(item.get("title") or ""))
        haystack_parts.append(str(item.get("snippet") or ""))
    haystack = "\n".join(haystack_parts)
    terms: list[str] = []
    for pattern in _TICKER_DISCOVERY_RE:
        for match in pattern.finditer(haystack):
            token = (match.group(1) or "").upper().strip(".")
            if token and token not in false_positive:
                terms.append(token)
    for term in terms[:10]:
        for row in _local_symbol_rows_for_term(term, per_market_limit=3):
            if _append_symbol_candidate(candidates, seen, row, term, "search_ticker_discovery"):
                if len(candidates) >= limit:
                    return candidates
    return candidates


def _search_intelligence(message: str, candidates: list[dict], language: str) -> dict:
    query_base = (message or "").strip()
    if not query_base:
        return {"web_results": [], "news_results": [], "search_queries": [], "provider_status": []}
    entity = ""
    if candidates:
        entity = candidates[0].get("name") or candidates[0].get("symbol") or candidates[0].get("match") or ""
    query = f"{entity} {query_base} latest market news".strip() if entity else f"{query_base} latest market news"
    queries = [query]
    ticker_query = f"{entity or query_base} stock ticker symbol exchange".strip()
    if ticker_query not in queries:
        queries.append(ticker_query)
    if "spacex" in query_base.lower() and "SpaceX valuation public stock ticker latest" not in queries:
        queries.append("SpaceX valuation public stock ticker latest")

    web_results: list[dict] = []
    provider_status: list[dict] = []
    try:
        service = get_search_service()
        provider_status = service.provider_status() if hasattr(service, "provider_status") else []
        for q in queries[:3]:
            for item in service.search(q, num_results=5, days=14):
                web_results.append({
                    "title": item.get("title") or "",
                    "snippet": item.get("snippet") or "",
                    "link": item.get("link") or item.get("url") or "",
                    "source": item.get("source") or "",
                    "published": item.get("published") or "",
                    "query": q,
                })
    except Exception as e:
        web_results.append({"error": str(e), "query": query})

    return {
        "web_results": web_results[:8],
        "news_results": web_results[:5],
        "search_queries": queries,
        "provider_status": provider_status,
        "language": language,
    }


def _macro_intelligence(message: str) -> dict:
    text = (message or "").lower()
    if not any(k in text for k in ("非农", "nfp", "cpi", "fomc", "fed", "利率", "就业", "失业", "pce", "gdp", "通胀", "macro", "payroll")):
        return {}
    profile = _macro_question_profile(message)
    try:
        payload = get_economic_calendar_payload()
        events = payload.get("events") if isinstance(payload, dict) else payload
        if not isinstance(events, list):
            events = []
        relevant_events = _filter_macro_events(events, profile)
        release_lookup = _macro_release_lookup(message, profile, relevant_events, payload if isinstance(payload, dict) else {})
        return {
            "source": payload.get("source") if isinstance(payload, dict) else "economic_calendar",
            "status": payload.get("status") if isinstance(payload, dict) else "ok",
            "provider_message": payload.get("message") if isinstance(payload, dict) else "",
            "question_profile": profile,
            "release_lookup": release_lookup,
            "events": relevant_events[:12],
            "context_events": events[:20],
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "question_profile": profile, "events": []}


def _macro_question_profile(message: str) -> dict:
    text = (message or "").lower()
    indicator = "macro_event"
    aliases: list[str] = []
    country = ""
    if any(k in text for k in ("非农", "nfp", "nonfarm", "non farm", "payroll")):
        indicator = "US_NONFARM_PAYROLLS"
        aliases = ["nonfarm payroll", "non farm payroll", "nfp", "非农", "就业人口"]
        country = "US"
    elif any(k in text for k in ("cpi", "通胀", "inflation")):
        indicator = "US_CPI"
        aliases = ["cpi", "consumer price index", "通胀", "消费者物价"]
        country = "US" if any(k in text for k in ("美国", "us", "u.s", "america")) else ""
    elif any(k in text for k in ("fomc", "fed", "利率", "降息", "加息")):
        indicator = "FOMC_RATE_DECISION"
        aliases = ["fomc", "fed", "federal funds", "interest rate", "利率", "降息", "加息"]
        country = "US" if any(k in text for k in ("美国", "us", "u.s", "america", "fed", "fomc")) else ""
    elif "gdp" in text:
        indicator = "GDP"
        aliases = ["gdp", "gross domestic product", "国内生产总值"]
        country = "US" if any(k in text for k in ("美国", "us", "u.s", "america")) else ""
    elif any(k in text for k in ("pce", "核心pce")):
        indicator = "US_PCE"
        aliases = ["pce", "personal consumption expenditures", "核心pce"]
        country = "US"

    period_hint = "latest"
    if any(k in text for k in ("这个月", "本月", "this month", "latest", "最近", "最新")):
        period_hint = "latest_release"
    elif any(k in text for k in ("下次", "下一次", "什么时候", "when", "upcoming")):
        period_hint = "next_release"
    elif any(k in text for k in ("上次", "上个月", "previous", "last month")):
        period_hint = "previous_release"

    return {
        "indicator": indicator,
        "country": country,
        "aliases": aliases,
        "period_hint": period_hint,
        "needs_actual_value": any(k in text for k in ("多少", "actual", "数据", "number", "value", "公布")),
    }


def _filter_macro_events(events: list[dict], profile: dict) -> list[dict]:
    aliases = [str(x).lower() for x in (profile.get("aliases") or [])]
    country = str(profile.get("country") or "").lower()
    if not aliases:
        return events[:12]
    scored: list[tuple[int, dict]] = []
    for event in events or []:
        haystack = " ".join(
            str(event.get(key) or "")
            for key in ("event", "event_en", "name", "title", "country", "country_code", "category", "description")
        ).lower()
        score = sum(2 for alias in aliases if alias and alias in haystack)
        if score and country and country in haystack:
            score += 1
        if score:
            scored.append((score, event))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [event for _, event in scored]


def _macro_release_lookup(message: str, profile: dict, events: list[dict], payload: dict) -> dict:
    indicator = profile.get("indicator") or "macro_event"
    result = {
        "indicator": indicator,
        "status": "missing_data",
        "answerable": False,
        "actual": None,
        "forecast": None,
        "previous": None,
        "period": "",
        "release_time": "",
        "source_chain": [],
        "evidence": [],
        "provider_status": {
            "calendar_source": payload.get("source") or "",
            "calendar_status": payload.get("status") or "",
            "calendar_message": payload.get("message") or "",
        },
        "setup_guidance": [],
    }

    event_value = _extract_macro_value_from_events(events)
    if event_value.get("actual") is not None or event_value.get("forecast") is not None or event_value.get("previous") is not None:
        result.update(event_value)
        result["status"] = "ok"
        result["answerable"] = result.get("actual") is not None
        result["source_chain"].append("economic_calendar")
        return result

    if indicator == "US_NONFARM_PAYROLLS":
        akshare_value = _fetch_akshare_nonfarm_payrolls()
        result["source_chain"].append("akshare_macro_usa_non_farm")
        if akshare_value.get("status") == "ok":
            result.update(akshare_value)
            result["answerable"] = True
            return result
        result["provider_status"]["akshare_non_farm"] = akshare_value

        bls_value = _fetch_bls_nonfarm_payrolls()
        result["source_chain"].append("bls_public_api")
        if bls_value.get("status") == "ok":
            result.update(bls_value)
            result["answerable"] = True
            return result
        result["provider_status"]["bls"] = bls_value

    search_value = _macro_search_lookup(message, profile)
    result["source_chain"].append("web_search")
    if search_value.get("evidence"):
        result["evidence"] = search_value["evidence"]
    if search_value.get("status") == "ok":
        result.update(search_value)
        result["answerable"] = bool(search_value.get("actual") or search_value.get("evidence"))
        return result
    result["provider_status"]["web_search"] = search_value
    result["setup_guidance"] = _macro_setup_guidance(indicator, result["provider_status"])
    return result


def _extract_macro_value_from_events(events: list[dict]) -> dict:
    for event in events or []:
        actual = _first_present(event, ("actual", "actual_value", "value", "now", "reported"))
        forecast = _first_present(event, ("forecast", "consensus", "estimate", "expected"))
        previous = _first_present(event, ("previous", "prior", "prev"))
        if actual is None and forecast is None and previous is None:
            continue
        return {
            "actual": actual,
            "forecast": forecast,
            "previous": previous,
            "period": str(_first_present(event, ("period", "date", "time", "release_date")) or ""),
            "release_time": str(_first_present(event, ("datetime", "time", "date", "release_time")) or ""),
            "evidence": [{
                "source": "economic_calendar",
                "title": str(_first_present(event, ("event", "event_en", "name", "title")) or ""),
                "snippet": _json_dumps(event)[:500],
            }],
        }
    return {}


def _first_present(obj: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = obj.get(key)
        if value not in (None, "", "--", "-"):
            return value
    return None


def _fetch_bls_nonfarm_payrolls() -> dict:
    current_year = _now_utc().year
    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/CES0000000001"
    params = {"startyear": str(current_year - 1), "endyear": str(current_year)}
    try:
        response = requests.get(url, params=params, timeout=4)
        response.raise_for_status()
        data = response.json()
        series = (((data.get("Results") or {}).get("series") or [{}])[0].get("data") or [])
        points = []
        for item in series:
            period = str(item.get("period") or "")
            if not period.startswith("M") or period == "M13":
                continue
            year = int(item.get("year"))
            month = int(period[1:])
            value = float(item.get("value"))
            points.append((year, month, value, item))
        points.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if len(points) < 2:
            return {"status": "empty", "message": "BLS returned fewer than two monthly observations."}
        latest, previous = points[0], points[1]
        change_thousands = latest[2] - previous[2]
        return {
            "status": "ok",
            "actual": round(change_thousands),
            "forecast": None,
            "previous": None,
            "period": f"{latest[0]}-{latest[1]:02d}",
            "release_time": "",
            "unit": "thousand jobs, monthly change in total nonfarm payroll employment",
            "evidence": [{
                "source": "BLS public API",
                "title": "CES0000000001 All employees, total nonfarm, seasonally adjusted",
                "snippet": f"Latest level {latest[2]:.0f}k vs previous {previous[2]:.0f}k; computed change {change_thousands:.0f}k.",
                "url": url,
            }],
        }
    except Exception as exc:
        return {"status": "unavailable", "message": str(exc)}


def _fetch_akshare_nonfarm_payrolls() -> dict:
    try:
        import akshare as ak
        import pandas as pd

        df = ak.macro_usa_non_farm()
        if df is None or df.empty:
            return {"status": "empty", "message": "AkShare macro_usa_non_farm returned no rows."}
        date_col = "日期"
        actual_col = "今值"
        forecast_col = "预测值"
        previous_col = "前值"
        if date_col not in df.columns or actual_col not in df.columns:
            return {"status": "schema_mismatch", "columns": [str(c) for c in df.columns]}
        clean = df.copy()
        clean[date_col] = pd.to_datetime(clean[date_col], errors="coerce")
        clean = clean.dropna(subset=[date_col]).sort_values(date_col)
        released = clean[clean[actual_col].notna()]
        if released.empty:
            latest = clean.iloc[-1].to_dict()
            return {
                "status": "unreleased",
                "message": "AkShare has an NFP row but no actual value yet.",
                "period": str(latest.get(date_col).date()) if latest.get(date_col) is not None else "",
                "forecast": _safe_scalar(latest.get(forecast_col)),
                "previous": _safe_scalar(latest.get(previous_col)),
            }
        latest = released.iloc[-1].to_dict()
        release_date = latest.get(date_col)
        days_old = (_now_utc().date() - release_date.date()).days if release_date is not None else 9999
        payload = {
            "actual": _safe_scalar(latest.get(actual_col)),
            "forecast": _safe_scalar(latest.get(forecast_col)),
            "previous": _safe_scalar(latest.get(previous_col)),
            "period": str(release_date.date()) if release_date is not None else "",
            "release_time": "",
            "unit": "ten thousand jobs",
            "evidence": [{
                "source": "AkShare macro_usa_non_farm",
                "title": "美国非农就业人数",
                "snippet": f"actual={_safe_scalar(latest.get(actual_col))}, forecast={_safe_scalar(latest.get(forecast_col))}, previous={_safe_scalar(latest.get(previous_col))}, date={str(release_date.date()) if release_date is not None else ''}",
                "url": "https://datacenter.jin10.com/reportType/dc_nonfarm_payrolls",
            }],
        }
        if days_old > 45:
            payload.update({
                "status": "stale",
                "message": f"Latest AkShare NFP actual is {days_old} days old; do not use it as this month's value.",
            })
            return payload
        payload["status"] = "ok"
        return payload
    except Exception as exc:
        return {"status": "unavailable", "message": str(exc)}


def _safe_scalar(value: Any) -> Any:
    try:
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
    except Exception:
        pass
    return value


def _macro_search_lookup(message: str, profile: dict) -> dict:
    aliases = profile.get("aliases") or []
    query = " ".join([str(profile.get("country") or "US"), str(aliases[0] if aliases else message), "latest actual forecast previous"])
    try:
        service = get_search_service()
        providers = service.provider_status() if hasattr(service, "provider_status") else []
        response = service.search_with_fallback(query, max_results=5, days=45)
        evidence = []
        for item in response.to_list()[:5]:
            evidence.append({
                "title": item.get("title") or "",
                "snippet": item.get("snippet") or "",
                "url": item.get("link") or item.get("url") or "",
                "source": item.get("source") or response.provider,
            })
        return {
            "status": "ok" if evidence else "empty",
            "query": query,
            "provider": response.provider,
            "error": response.error_message,
            "providers": providers,
            "evidence": evidence,
        }
    except Exception as exc:
        return {"status": "unavailable", "query": query, "message": str(exc)}


def _macro_setup_guidance(indicator: str, provider_status: dict) -> list[dict]:
    guidance = [
        {
            "target": "Trading Economics",
            "settings": ["TRADING_ECONOMICS_CLIENT", "TRADING_ECONOMICS_KEY"],
            "reason": "Provides structured global macro calendar fields such as actual, forecast, and previous.",
        },
        {
            "target": "Google Custom Search fallback",
            "settings": ["SEARCH_GOOGLE_API_KEY", "SEARCH_GOOGLE_CX"],
            "reason": "Recommended low-cost fallback for current macro releases, company news, symbol discovery, and source verification.",
        },
        {
            "target": "Other search providers",
            "settings": ["SEARCH_SEARXNG_BASE_URL", "SEARCH_GOOGLE_API_KEY + SEARCH_GOOGLE_CX", "SEARCH_BING_API_KEY", "TAVILY_API_KEYS"],
            "reason": "Lets Copilot verify newly released macro figures when the calendar provider is missing them.",
        },
    ]
    if indicator == "US_NONFARM_PAYROLLS":
        guidance.insert(0, {
            "target": "BLS public API",
            "settings": ["Docker outbound HTTPS access to api.bls.gov"],
            "reason": "NFP can be computed from the official BLS total nonfarm payroll series when network access is available.",
        })
        guidance.insert(1, {
            "target": "AkShare macro_usa_non_farm",
            "settings": ["Backend network access to datacenter.jin10.com"],
            "reason": "Provides a no-key NFP fallback, but must pass freshness checks before answering current-month questions.",
        })
    return guidance


def _selected_context_conflict(context: dict, primary: dict | None) -> dict:
    selected_market = (context.get("market") or "").strip()
    selected_symbol = (context.get("symbol") or "").strip()
    if not selected_market or not selected_symbol or not primary:
        return {"has_conflict": False}
    primary_market = str(primary.get("market") or "").strip()
    primary_symbol = str(primary.get("symbol") or "").strip()
    if not primary_market or not primary_symbol:
        return {
            "has_conflict": True,
            "selected": {"market": selected_market, "symbol": selected_symbol},
            "message_entity": primary,
            "reason": "The user's natural-language entity is not a directly tradable symbol.",
        }
    has_conflict = selected_market != primary_market or selected_symbol.upper() != primary_symbol.upper()
    return {
        "has_conflict": has_conflict,
        "selected": {"market": selected_market, "symbol": selected_symbol},
        "message_entity": {"market": primary_market, "symbol": primary_symbol, "name": primary.get("name") or ""},
        "reason": "The selected UI symbol differs from the entity inferred from the user message." if has_conflict else "",
    }


def _snapshot_for_candidate(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    market = str(candidate.get("market") or "").strip()
    symbol = str(candidate.get("symbol") or "").strip()
    if not market or not symbol or market in {"private_company", "private_business_unit"}:
        return None
    try:
        return _build_market_snapshot({"market": market, "symbol": symbol})
    except Exception as e:
        return {"market": market, "symbol": symbol, "available": False, "error": str(e)}


def _research_task_flags(message: str, intent: str, has_image: bool = False) -> dict:
    text = (message or "").lower()
    wants_macro = any(k in text for k in ("nfp", "cpi", "fomc", "fed", "rates", "pce", "gdp", "inflation", "payroll", "非农", "利率", "通胀", "就业", "宏观"))
    wants_market_data = any(
        k in text for k in ("price", "quote", "trend", "support", "resistance", "行情", "价格", "走势", "支撑", "阻力", "k线", "kline", "多少钱", "股价", "现价")
    )
    return {
        "intent": intent,
        "needs_market_data": wants_market_data or (intent in {"market_analysis", "opportunity_radar"} and not wants_macro),
        "needs_news": any(k in text for k in ("latest", "news", "headline", "event", "ipo", "spac", "spacex", "新闻", "消息", "事件", "上市", "影响")),
        "needs_macro": wants_macro,
        "needs_fundamentals": any(k in text for k in ("valuation", "market cap", "earnings", "revenue", "fundamental", "估值", "市值", "财报", "营收", "基本面")),
        "needs_chart": has_image or any(k in text for k in ("chart", "screenshot", "kline", "k线图", "截图", "看图")),
        "needs_strategy": intent == "strategy_build" or any(k in text for k in ("strategy", "bot", "策略", "机器人", "写代码", "生成代码")),
    }


def _research_skill_plan(message: str, intent: str, language: str) -> list[dict]:
    skills = match_skills(message, intent, limit=8)
    return [
        {
            "id": skill.id,
            "category": skill.category,
            "label": skill.label.pick(language),
            "requires": list(skill.requires),
            "produces": list(skill.produces),
            "risk_level": skill.risk_level,
            "read_only": skill.read_only,
            "route": skill.route,
        }
        for skill in skills
    ]


def _company_fundamentals_context(candidates: list[dict], search_context: dict, flags: dict) -> dict:
    primary = candidates[0] if candidates else {}
    related = []
    for item in candidates:
        related.extend(item.get("related_public_symbols") or [])
    profile = {
        "primary_name": primary.get("name") or primary.get("match") or "",
        "primary_market": primary.get("market") or "",
        "primary_symbol": primary.get("symbol") or "",
        "is_directly_tradable": bool(primary.get("symbol")) and primary.get("market") not in {"private_company", "private_business_unit"},
        "private_company_note": primary.get("note") or "",
        "related_public_symbols": related,
    }
    evidence = []
    for item in (search_context.get("web_results") or [])[:5]:
        evidence.append({
            "title": item.get("title") or "",
            "source": item.get("source") or "",
            "snippet": item.get("snippet") or "",
            "link": item.get("link") or "",
            "published": item.get("published") or "",
        })
    return {
        "profile": profile,
        "evidence": evidence,
        "status": "search_context_only" if flags.get("needs_fundamentals") else "light_profile",
        "note": "Use search evidence as context only. Do not invent financial statements or private-company valuation numbers.",
    }


def _build_research_context(context: dict, has_image: bool = False) -> dict:
    message = str(context.get("user_message") or "")
    intent = str(context.get("intent") or "")
    language = str(context.get("language") or "zh-CN")
    flags = _research_task_flags(message, intent, has_image=has_image)
    if not _needs_intelligence_context(message, intent) and not any(flags.values()):
        return {}

    candidates = _local_symbol_candidates(message)
    needs_symbol_discovery = flags["needs_market_data"] and not candidates
    search_context = _search_intelligence(message, candidates, language) if (flags["needs_news"] or flags["needs_fundamentals"] or needs_symbol_discovery) else {
        "web_results": [],
        "news_results": [],
        "search_queries": [],
        "language": language,
    }
    if not candidates and search_context.get("web_results"):
        candidates.extend(_discover_symbol_candidates_from_search(search_context, candidates))
    primary = candidates[0] if candidates else None
    raw_macro_context = _macro_intelligence(message) if flags["needs_macro"] else {}
    macro_context = raw_macro_context if isinstance(raw_macro_context, dict) else {}

    selected_snapshot = context.get("market_snapshot")
    primary_snapshot = None
    conflict = _selected_context_conflict(context, primary)
    if flags["needs_market_data"]:
        if selected_snapshot and not conflict.get("has_conflict"):
            primary_snapshot = selected_snapshot
        else:
            primary_snapshot = _snapshot_for_candidate(primary)

    data_gaps = []
    if flags["needs_market_data"] and not (selected_snapshot or primary_snapshot):
        data_gaps.append("No usable quote/K-line snapshot was available for the inferred entity. Resolve the symbol or configure the relevant data source.")
    if flags["needs_news"] and not search_context.get("web_results"):
        data_gaps.append("No web/news search result was available. Check search engine configuration or network access.")
    macro_lookup = macro_context.get("release_lookup") or {}
    if not isinstance(macro_lookup, dict):
        macro_lookup = {}
    macro_events = macro_context.get("events") or []
    if flags["needs_macro"] and not (macro_lookup.get("answerable") or macro_events):
        data_gaps.append("No exact macro release value was available for this question. Check BLS/Trading Economics/search configuration.")
    if primary and primary.get("market") in {"private_company", "private_business_unit"}:
        data_gaps.append("The inferred entity is not directly exchange-traded; do not answer with a fake public stock price.")

    recommended_actions = []
    if primary_snapshot:
        recommended_actions.append({"type": "answer", "label": "Use market snapshot for technical levels and risk plan."})
    if search_context.get("web_results"):
        recommended_actions.append({"type": "answer", "label": "Use recent search/news evidence and cite title/source briefly."})
    if macro_events:
        recommended_actions.append({"type": "answer", "label": "Use macro event context and distinguish released values from upcoming events."})
    if primary and not primary.get("symbol"):
        recommended_actions.append({"type": "workflow", "label": "Explain non-tradable/private status and suggest related tradable proxies or search actions."})
    if flags["needs_strategy"]:
        recommended_actions.append({"type": "workflow", "label": "Clarify missing strategy requirements before generating code or creating a draft."})

    return {
        "version": "research-context-2026-06-15",
        "generated_at_utc": _now_utc().isoformat(),
        "request": {
            "message": message,
            "intent": intent,
            "language": language,
            "task_flags": flags,
        },
        "workflow": {
            "decision_order": [
                "1. Resolve the user's natural-language entity and compare it with selected UI context.",
                "2. Decide which registered skills are needed.",
                "3. Collect market snapshot, search/news, macro, and fundamentals context where applicable.",
                "4. Answer with conclusions, evidence, caveats, and executable next actions.",
            ],
            "recommended_skills": _research_skill_plan(message, intent, language),
            "recommended_actions": recommended_actions,
        },
        "entities": {
            "primary": primary or {},
            "candidates": candidates,
            "selected_context_conflict": conflict,
        },
        "market_data": {
            "selected_snapshot": selected_snapshot or {},
            "primary_snapshot": primary_snapshot or {},
        },
        "news": search_context,
        "macro": macro_context,
        "fundamentals": _company_fundamentals_context(candidates, search_context, flags),
        "data_gaps": data_gaps,
        "answer_policy": {
            "prefer_user_entity_over_stale_ui_selection": True,
            "do_not_invent_live_data": True,
            "do_not_fake_private_company_ticker": True,
            "cite_search_sources_briefly": True,
            "produce_next_actions": True,
        },
    }


def _legacy_intelligence_context(research_context: dict) -> dict:
    if not research_context:
        return {}
    return {
        "guidance": (
            "Use research_context before answering. Resolve entity, choose skills, use market/news/macro/fundamental context, "
            "then produce evidence-based conclusions and next actions."
        ),
        "symbol_candidates": (research_context.get("entities") or {}).get("candidates") or [],
        "search": research_context.get("news") or {},
        "macro": research_context.get("macro") or {},
        "data_gaps": research_context.get("data_gaps") or [],
    }


def _enrich_context(context: dict, has_image: bool = False) -> dict:
    enriched = dict(context or {})
    if "market_snapshot" not in enriched:
        snapshot = _build_market_snapshot(enriched)
        if snapshot:
            enriched["market_snapshot"] = snapshot
    research = _build_research_context(enriched, has_image=has_image)
    if research:
        enriched["research_context"] = research
        enriched["intelligence_context"] = _legacy_intelligence_context(research)
    return enriched


def _compact_memory_text(value: Any, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", _plain_text(value)).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return (match.group(1) if match.groups() else match.group(0)).strip()
    return ""


def _extract_session_known_fields(text: str, context: dict) -> dict:
    known: dict[str, Any] = {}
    market = context.get("market")
    symbol = context.get("symbol")
    if market or symbol:
        known["selected_target"] = {"market": market, "symbol": symbol}

    interval = _first_match(
        [
            r"\b(\d+\s*(?:m|min|minute|minutes|h|hour|hours|d|day|days|w|week|weeks))\b",
            r"\b(\d+[mhdw])\b",
            r"\b(daily|weekly|hourly|1h|4h|15m|30m)\b",
            r"(每(?:天|日|周|小时)|\d+\s*(?:分钟|小时|天|日|周)|15分钟|30分钟|1小时|4小时|日线|周线)",
        ],
        text,
    )
    if interval:
        known["interval_or_timeframe"] = interval

    channels: list[str] = []
    channel_patterns = {
        "in_app": r"(站内|站内消息|应用内|in[- ]?app|browser notification)",
        "email": r"(邮箱|邮件|email|e-mail)",
        "webhook": r"(webhook|回调)",
        "sms": r"(短信|sms)",
        "telegram": r"(telegram|tg)",
    }
    for channel, pattern in channel_patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            channels.append(channel)
    if channels:
        known["notification_channels"] = channels

    focus = _first_match(
        [
            r"(?:重点关注|关注条件|提醒条件|触发条件|监控条件|focus(?: on)?|watch(?: for)?|conditions?)[:：]?\s*([^。；;\n]{4,260})",
            r"(突破[^。；;\n]{2,180})",
            r"(跌破[^。；;\n]{2,180})",
        ],
        text,
    )
    if focus:
        known["focus_conditions"] = focus

    if re.search(r"(止损|stop loss|sl\b)", text, re.IGNORECASE):
        known["mentions_stop_loss"] = True
    if re.search(r"(止盈|take profit|tp\b)", text, re.IGNORECASE):
        known["mentions_take_profit"] = True
    if re.search(r"(策略|strategy|脚本|script|指标|indicator)", text, re.IGNORECASE):
        known["strategy_related"] = True
    if re.search(r"(新闻|事件|news|event|macro|宏观|经济数据)", text, re.IGNORECASE):
        known["research_related"] = True
    return known


def _build_session_working_memory(history: list[dict], current_message: str, context: dict, language: str) -> dict:
    user_facts: list[str] = []
    assistant_prompts: list[str] = []
    for item in history[-16:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = _compact_memory_text(item.get("content"), 900)
        if not content:
            continue
        if role == "user":
            user_facts.append(content)
        elif (
            "?" in content
            or "？" in content
            or re.search(r"(please provide|missing|need|补充|缺少|请选择|请填写|需要)", content, re.IGNORECASE)
        ):
            assistant_prompts.append(content)

    current = _compact_memory_text(current_message, 1200)
    if current:
        user_facts.append(current)

    combined = "\n".join(user_facts[-10:])
    known = _extract_session_known_fields(combined, context)
    agent_task = context.get("agent_task")
    if isinstance(agent_task, dict) and agent_task:
        known["active_agent_task"] = {
            "type": agent_task.get("type") or agent_task.get("id"),
            "title": agent_task.get("title") or agent_task.get("label"),
            "required_fields": agent_task.get("required_fields") or agent_task.get("missing_fields"),
        }

    memory = {
        "purpose": "session_task_state",
        "language": language,
        "known_fields": known,
        "recent_user_facts": user_facts[-8:],
        "recent_assistant_questions": assistant_prompts[-3:],
        "instruction": (
            "Use this as working memory for the current chat session. "
            "Do not ask again for fields already present in known_fields or recent_user_facts. "
            "When enough information has been provided, proceed to the next workflow step instead of restarting the checklist."
        ),
    }
    return memory


def _build_system_prompt(language: str, context: dict, intent: str, has_image: bool, json_response: bool = True) -> str:
    lang_name = "Chinese" if (language or "").lower().startswith("zh") else "English"
    context_bits = []
    if context.get("symbol"):
        context_bits.append(f"symbol={context.get('symbol')}")
    if context.get("market"):
        context_bits.append(f"market={context.get('market')}")
    if context.get("strategy_id"):
        context_bits.append(f"strategy_id={context.get('strategy_id')}")
    context_line = ", ".join(context_bits) or "no explicit selected symbol"
    image_line = "The user attached chart/K-line screenshots; analyze visible chart structure, indicators, labels and risk." if has_image else "No image is attached."
    base = (
        "You are QuantDinger Copilot, a trading system assistant for open-source quant users. "
        f"Reply in {lang_name}. Current intent={intent}; context: {context_line}. {image_line}\n"
        "Be practical and careful. Do not promise profit or invent unavailable live data. "
        "If the user asks to write strategy code, stay inside QuantDinger native workflows. "
        "Use Indicator IDE code for indicator strategies, Python ScriptStrategy for script strategies, and Trading Bot parameters for bot workflows. "
        "Never output Pine Script, TradingView-only code, MT4/MT5 MQL, or unrelated platform syntax unless the user explicitly asks for that platform. "
        "For strategy work, first clarify missing requirements, then propose design, then generate runnable code only when the user confirms or asks to generate. "
        "If the user asks for market/chart diagnosis, separate observable facts from inference. "
        "If market_snapshot is provided, use its actual numbers and avoid generic textbook checklists. "
        "Treat recent conversation history as active memory. Do not ask again for details already provided in the same session. "
        "Keep answers decision-first and compact: conclusion first, then evidence, then levels/plan/data gaps. Avoid long generic frameworks unless the user asks for a full report. "
        "Default to high-signal output: simple questions should be answered in no more than 220 Chinese characters or 120 English words; market diagnosis should use at most five bullets unless the user requests a full report. "
        "Avoid filler such as generic risk education, repeated disclaimers, long checklists, and process narration. Every useful answer should include a verdict, the key evidence, invalidation or next step, and only the missing data that truly blocks action. "
        "When information is missing, ask for at most two missing fields at a time and never re-ask for fields already present in the session memory. "
        "If research_context is provided, treat it as the structured research workspace. First resolve the entity, then choose skills, then use market snapshot, search/news, macro events, fundamentals context, and data gaps before answering. "
        "If intelligence_context is provided, treat it as a legacy compatibility summary of research_context. "
        "For macro/current-data questions, inspect provided system context, market_snapshot, economic_calendar_context, tools and skills before saying data is unavailable. "
        "If the exact value is missing, explain the missing field and the needed data-source configuration, then provide the best actionable fallback. "
        "For market analysis, start with a concrete directional read, then provide support/resistance levels, confirmation signals, invalidation, and risk controls. "
        "For scheduled analysis or monitor setup, first ask for missing interval, notification channels, and focus conditions. "
        "If symbol, interval, notification preference, and focus conditions are already clear, include an action with type=create_monitor_task and payload "
        "{\"target\":{\"market\":\"...\",\"symbol\":\"...\"},\"interval_min\":60,\"notify_channels\":[\"browser\"],\"focus_conditions\":\"...\",\"name\":\"...\"}. "
        "Never create tasks silently; the UI will ask the user to confirm the returned action. "
        "If funding/open interest or other data is unavailable, say unavailable and do not invent it. "
        "If evidence is insufficient, still provide a conditional plan using available data and list what is missing.\n"
    )
    base += "\n" + build_skill_prompt(language, str(context.get("user_message") or ""), intent) + "\n"
    base += "\n" + build_tool_prompt(language, intent) + "\n"
    if context.get("agent_task"):
        base += (
            f"\n[QuantDinger agent task]\n{_json_dumps(context.get('agent_task'))}\n"
            "Treat this as a workflow state, not a casual chat. Keep the next action explicit.\n"
        )
    session_memory = context.get("session_working_memory")
    if isinstance(session_memory, dict) and session_memory:
        base += (
            "\n[Session working memory]\n"
            + _json_dumps(session_memory)[:9000]
            + "\n"
            "This memory is authoritative for the current session. Merge new user answers into this task state. "
            "If the user has already supplied a requested field, acknowledge it briefly and ask only for the next missing field. "
            "If no required fields are missing, produce the result or action now.\n"
        )
    research_context = context.get("research_context")
    if isinstance(research_context, dict) and research_context:
        base += (
            "\n[QuantDinger Research Context]\n"
            + _json_dumps(research_context)[:14000]
            + "\n"
            "Use the Research Context decision_order. If selected_context_conflict.has_conflict is true, explain the mismatch and prefer the user's message entity unless the user explicitly chose the selected UI symbol. "
            "Your final answer must include concrete conclusions, evidence, caveats, and actionable next steps. "
            "When a workflow action is possible, include it in JSON actions or as a clear Markdown button-style next step.\n"
        )
    intelligence_context = context.get("intelligence_context")
    if isinstance(intelligence_context, dict) and intelligence_context:
        base += "\n[Copilot intelligence context]\n" + _json_dumps(intelligence_context)[:8000] + "\n"
    memories = context.get("user_memories")
    if isinstance(memories, list) and memories:
        memory_lines = []
        for item in memories[:12]:
            memory_lines.append(f"- {item.get('title')}: {item.get('content')}")
        base += "\n[User memory]\n" + "\n".join(memory_lines) + "\n"
    calendar_context = context.get("economic_calendar_context")
    if isinstance(calendar_context, list) and calendar_context:
        base += "\n[Economic calendar context]\n" + _json_dumps(calendar_context[:30])[:5000] + "\n"
    if not json_response:
        return base + (
            "Respond in clean Markdown. Prefer concise, evidence-dense analysis over broad frameworks. "
            "For symbol analysis, use at most three short sections by default: verdict, key evidence/levels, and action plan. "
            "Only expand into a full six-part report when the user asks for a report or deep analysis. "
            "If scenarios are useful, keep them to bull/base/bear with trigger, invalidation, and what to watch next. "
            "Use headings, bullet lists, tables when useful, and fenced code blocks for code. "
            "Do not wrap the full response in JSON."
        )
    return (
        base +
        "Return JSON only with this schema: "
        "{\"answer\":\"markdown answer\", \"summary\":\"short title\", \"confidence\":0-100, "
        "\"actions\":[{\"type\":\"analysis|strategy|debug|risk|todo|create_monitor_task\", \"label\":\"...\", \"payload\":{}}], "
        "\"artifact\":{\"type\":\"none|strategy_code|checklist|market_note\", \"title\":\"...\", \"content\":\"...\"}}."
    )


def _build_llm_messages(history: list[dict], message: str, attachments: list[dict], context: dict, language: str, intent: str, json_response: bool = True) -> list[dict]:
    context = dict(context or {})
    context["user_message"] = message or ""
    context["session_working_memory"] = _build_session_working_memory(history, message or "", context, language)
    messages: list[dict] = [
        {"role": "system", "content": _build_system_prompt(language, context, intent, bool(attachments), json_response=json_response)}
    ]
    for h in history[-12:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        content = str(h.get("content") or "")[:4000]
        hist_attachments = _json_loads(h.get("attachments_json"), [])
        if isinstance(hist_attachments, list) and hist_attachments:
            names = ", ".join(
                str(att.get("name") or "image")[:80]
                for att in hist_attachments
                if isinstance(att, dict)
            )
            if names:
                content += f"\n[Historical attachment(s): {names}. Image bytes are stored for UI history; ask the user to reattach if visual detail is needed again.]"
        messages.append({"role": role, "content": content})

    context_note = ""
    if context:
        context_note = f"\n\n[Selected context]\n{_json_dumps(context)}"
    user_text = (message or "").strip() + context_note
    if attachments:
        content: list[dict] = [{"type": "text", "text": user_text}]
        for att in attachments:
            content.append({"type": "image_url", "image_url": {"url": att["data_url"]}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_text})
    return messages


def _parse_llm_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text or "")
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return {
        "answer": text or "The model returned an empty response.",
        "summary": "Copilot response",
        "confidence": 50,
        "actions": [],
        "artifact": {"type": "none", "title": "", "content": ""},
    }


def _safe_count_table(cur, table: str, where: str = "", params: tuple = ()) -> int:
    try:
        sql = f"SELECT COUNT(*) AS cnt FROM {table}"
        if where:
            sql += f" WHERE {where}"
        cur.execute(sql, params)
        row = cur.fetchone() or {}
        return int(row.get("cnt") or 0)
    except Exception:
        return 0


def _build_preflight(user_id: int) -> dict:
    billing = get_billing_service()
    llm = LLMService()
    provider = llm.provider.value
    api_key = llm.get_api_key(llm.provider)
    custom_base_ok = provider == "custom" and bool(llm.get_base_url(llm.provider))
    llm_ready = bool(api_key) or custom_base_ok or provider == "litellm"
    billing_enabled = bool(billing.is_billing_enabled())
    credits = float(billing.get_user_credits(user_id))
    result = {
        "llm": {
            "ready": bool(llm_ready),
            "provider": provider,
            "model": llm.get_default_model(llm.provider),
            "action": {"path": "/settings", "query": {"section": "ai-llm"}},
        },
        "credits": {
            "ready": (not billing_enabled) or credits > 0,
            "balance": credits,
            "billing_enabled": billing_enabled,
            "action": {"path": "/billing"},
        },
        "data_source": {
            "ready": True,
            "action": {"path": "/settings", "query": {"section": "data-source"}},
        },
        "search": {
            "ready": False,
            "providers": [],
            "action": {"path": "/settings", "query": {"section": "ai-llm"}},
        },
        "macro_sources": {
            "calendar": [
                {
                    "provider": "TradingEconomics",
                    "configured": TradingEconomicsConfig.CONFIGURED,
                    "available": TradingEconomicsConfig.CONFIGURED,
                    "purpose": "Structured global macro calendar with actual/forecast/previous fields.",
                },
                {
                    "provider": "AkShare",
                    "configured": True,
                    "available": True,
                    "timeout": AkshareConfig.TIMEOUT,
                    "purpose": "Free fallback for selected China/US macro data and calendar feeds.",
                },
            ],
            "series": [],
            "action": {"path": "/settings", "query": {"section": "data-source"}},
        },
        "broker": {
            "ready": False,
            "count": 0,
            "action": {"path": "/broker-accounts"},
        },
        "blockers": [],
        "warnings": [],
    }
    try:
        search_service = get_search_service()
        providers = search_service.provider_status() if hasattr(search_service, "provider_status") else []
        result["search"]["providers"] = providers
        result["search"]["ready"] = any(bool(p.get("registered") and p.get("available")) for p in providers)
    except Exception as e:
        result["warnings"].append({"key": "search_check_failed", "message": str(e)})
    try:
        macro_provider = get_macro_series_provider()
        result["macro_sources"]["series"] = macro_provider.source_status()
    except Exception as e:
        result["warnings"].append({"key": "macro_source_check_failed", "message": str(e)})
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            _ensure_tables(cur)
            result["broker"]["count"] = _safe_count_table(cur, "qd_exchange_credentials", "user_id = ?", (user_id,))
            result["broker"]["ready"] = result["broker"]["count"] > 0
            cur.close()
    except Exception as e:
        result["warnings"].append({"key": "broker_check_failed", "message": str(e)})
    if not result["llm"]["ready"]:
        result["blockers"].append({
            "key": "llm_missing",
            "title": "LLM provider is not configured",
            "message": "Configure an LLM API key in System Settings before using AI Copilot.",
            "action": result["llm"]["action"],
        })
    if billing_enabled and not result["credits"]["ready"]:
        result["blockers"].append({
            "key": "credits_empty",
            "title": "Insufficient credits",
            "message": "Top up credits before running AI analysis or strategy generation.",
            "action": result["credits"]["action"],
        })
    if not result["broker"]["ready"]:
        result["warnings"].append({
            "key": "broker_missing",
            "message": "No broker/exchange account is connected. Strategy design can continue, but live execution needs a broker account.",
            "action": result["broker"]["action"],
        })
    return result


def _charge(user_id: int, has_image: bool, reference_id: str) -> tuple[bool, str, dict]:
    billing = get_billing_service()
    costs = {
        "chat": billing.get_feature_cost("ai_copilot_chat"),
        "image": billing.get_feature_cost("ai_copilot_image") if has_image else 0,
    }
    ok, msg = billing.check_and_consume(user_id, "ai_copilot_chat", reference_id)
    if not ok:
        return False, msg, costs
    if has_image:
        ok, msg = billing.check_and_consume(user_id, "ai_copilot_image", reference_id)
        if not ok:
            return False, msg, costs
    return True, "consumed", costs


@ai_chat_blp.route("/skills", methods=["GET"])
@login_required
def ai_skills():
    """Return the public Copilot skill registry."""
    language = (request.args.get("language") or request.headers.get("X-App-Lang") or "zh-CN").strip()
    include_disabled = str(request.args.get("include_disabled") or "").lower() in {"1", "true", "yes"}
    if include_disabled and getattr(g, "user_role", None) != "admin":
        return jsonify({"code": 403, "msg": "Admin access required", "data": None}), 403
    return jsonify({"code": 1, "msg": "success", "data": public_registry(language, include_disabled=include_disabled)})


@ai_chat_blp.route("/tools", methods=["GET"])
@login_required
@admin_required
def ai_tools():
    """Return the public Copilot tool registry."""
    language = (request.args.get("language") or request.headers.get("X-App-Lang") or "zh-CN").strip()
    return jsonify({"code": 1, "msg": "success", "data": public_tool_registry(language)})


@ai_chat_blp.route("/skills/install", methods=["POST"])
@login_required
@admin_required
def ai_skill_install():
    """Install a prompt-only skill manifest."""
    data = request.get_json(silent=True) or {}
    payload = data.get("skill") if isinstance(data.get("skill"), dict) else data
    language = (data.get("language") or request.headers.get("X-App-Lang") or "zh-CN").strip()
    try:
        installed = install_prompt_skill(payload, install_source=str(data.get("source") or "manual")[:80])
        skill = get_skill(str(installed.get("id")))
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {"skill": skill.to_public(language) if skill else installed},
        })
    except ValueError as e:
        return jsonify({"code": 0, "msg": str(e), "data": None}), 400
    except Exception as e:
        logger.error(f"ai_skill_install failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@ai_chat_blp.route("/skills/<skill_id>", methods=["PATCH"])
@login_required
@admin_required
def ai_skill_update(skill_id: str):
    """Enable or disable an installed prompt skill."""
    data = request.get_json(silent=True) or {}
    language = (data.get("language") or request.headers.get("X-App-Lang") or "zh-CN").strip()
    if "enabled" not in data:
        return jsonify({"code": 0, "msg": "enabled is required", "data": None}), 400
    try:
        set_skill_enabled(skill_id, bool(data.get("enabled")))
        return jsonify({"code": 1, "msg": "success", "data": public_registry(language, include_disabled=True)})
    except FileNotFoundError:
        return jsonify({"code": 0, "msg": "skill not found", "data": None}), 404
    except ValueError as e:
        return jsonify({"code": 0, "msg": str(e), "data": None}), 400


@ai_chat_blp.route("/skills/<skill_id>", methods=["DELETE"])
@login_required
@admin_required
def ai_skill_delete(skill_id: str):
    """Delete an installed prompt skill."""
    try:
        delete_installed_skill(skill_id)
        return jsonify({"code": 1, "msg": "success", "data": {"id": skill_id}})
    except FileNotFoundError:
        return jsonify({"code": 0, "msg": "skill not found", "data": None}), 404
    except ValueError as e:
        return jsonify({"code": 0, "msg": str(e), "data": None}), 400


@ai_chat_blp.route("/skills/<skill_id>/prompt", methods=["POST"])
@login_required
def ai_skill_prompt(skill_id: str):
    """Render a skill prompt for the current UI context."""
    data = request.get_json(silent=True) or {}
    language = (data.get("language") or request.headers.get("X-App-Lang") or "zh-CN").strip()
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    skill = get_skill(skill_id)
    if not skill:
        return jsonify({"code": 0, "msg": "skill not found", "data": None}), 404
    return jsonify({
        "code": 1,
        "msg": "success",
        "data": {
            "skill": skill.to_public(language),
            "prompt": render_prompt_template(skill, language, context),
        },
    })


@ai_chat_blp.route("/agent/preflight", methods=["GET"])
@login_required
def agent_preflight():
    """Return Copilot readiness checks for user guidance."""
    user_id = int(getattr(g, "user_id", 0) or 0)
    return jsonify({"code": 1, "msg": "success", "data": _build_preflight(user_id)})


@ai_chat_blp.route("/agent/intent", methods=["POST"])
@login_required
def agent_intent():
    """Classify a Copilot message into a structured agent workflow plan."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    language = (data.get("language") or request.headers.get("X-App-Lang") or "zh-CN").strip()
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    try:
        attachments = _normalize_attachments(data.get("attachments") or [])
    except ValueError as e:
        return jsonify({"code": 0, "msg": str(e), "data": None}), 400
    if not message and not attachments:
        return jsonify({"code": 0, "msg": "Missing message", "data": None}), 400
    plan = _classify_agent_intent(message, attachments, context, language)
    return jsonify({"code": 1, "msg": "success", "data": plan})


@ai_chat_blp.route("/memory", methods=["GET"])
@login_required
def list_user_memory():
    """List active user memories used by Copilot."""
    user_id = int(getattr(g, "user_id", 0) or 0)
    with get_db_connection() as db:
        cur = db.cursor()
        _ensure_tables(cur)
        items = _get_user_memories(cur, user_id, limit=50)
        cur.close()
    return jsonify({"code": 1, "msg": "success", "data": {"items": items}})


@ai_chat_blp.route("/memory", methods=["POST"])
@login_required
def save_user_memory():
    """Save a user-approved memory for future Copilot context."""
    user_id = int(getattr(g, "user_id", 0) or 0)
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()[:160]
    content = str(data.get("content") or "").strip()[:1000]
    category = str(data.get("category") or "preference").strip()[:48]
    confidence = int(data.get("confidence") or 70)
    if not title or not content:
        return jsonify({"code": 0, "msg": "title and content are required", "data": None}), 400
    with get_db_connection() as db:
        cur = db.cursor()
        _ensure_tables(cur)
        cur.execute(
            """
            INSERT INTO qd_ai_user_memories
            (user_id, category, title, content, source, confidence, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, TRUE, NOW(), NOW())
            RETURNING id
            """,
            (user_id, category, title, content, "copilot", max(1, min(100, confidence))),
        )
        row = cur.fetchone()
        db.commit()
        cur.close()
    memory_id = int(row["id"] if isinstance(row, dict) else row[0])
    return jsonify({"code": 1, "msg": "success", "data": {"id": memory_id}})


@ai_chat_blp.route("/memory/<int:memory_id>", methods=["DELETE"])
@login_required
def delete_user_memory(memory_id: int):
    """Deactivate a user memory."""
    user_id = int(getattr(g, "user_id", 0) or 0)
    with get_db_connection() as db:
        cur = db.cursor()
        _ensure_tables(cur)
        cur.execute(
            "UPDATE qd_ai_user_memories SET is_active = FALSE, updated_at = NOW() WHERE id = ? AND user_id = ?",
            (int(memory_id), user_id),
        )
        ok = cur.rowcount > 0
        db.commit()
        cur.close()
    return jsonify({"code": 1 if ok else 0, "msg": "success" if ok else "not found", "data": {"id": memory_id}})


@ai_chat_blp.route("/chat/message", methods=["POST"])
@login_required
def chat_message():
    """Send a Copilot message and get an LLM response."""
    user_id = int(getattr(g, "user_id", 0) or 0)
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    language = (data.get("language") or request.headers.get("X-App-Lang") or "zh-CN").strip()
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    session_id = data.get("session_id") or data.get("chatId")

    try:
        attachments = _normalize_attachments(data.get("attachments") or [])
    except ValueError as e:
        return jsonify({"code": 0, "msg": str(e), "data": None}), 400

    if not message and not attachments:
        return jsonify({"code": 0, "msg": "Missing message", "data": None}), 400

    agent_plan = _get_or_classify_agent_intent(message, attachments, context, language)
    intent = str(agent_plan.get("intent") or _detect_intent(message, bool(attachments)))
    context["user_message"] = message
    context["intent"] = intent
    context["agent_intent"] = agent_plan
    context["language"] = language
    context = _enrich_context(context, has_image=bool(attachments))

    try:
        with get_db_connection() as db:
            cur = db.cursor()
            _ensure_tables(cur)
            session = _get_session(cur, user_id, int(session_id)) if session_id else None
            if session:
                sid = int(session["id"])
            else:
                sid = _create_session(cur, user_id, _title_from_message(message or "Chart analysis"), context)
            user_message_id = _insert_message(cur, sid, user_id, "user", message or "[image]", attachments, intent)
            cur.execute("UPDATE qd_ai_copilot_sessions SET updated_at = NOW() WHERE id = ?", (sid,))
            db.commit()

            charged, charge_msg, costs = _charge(user_id, bool(attachments), f"copilot:{sid}:{user_message_id}")
            if not charged:
                return jsonify({
                    "code": 0,
                    "msg": charge_msg,
                    "data": {"costs": costs},
                }), 402

            context["user_memories"] = _get_user_memories(cur, user_id)
            history = _load_recent_messages(cur, sid, limit=20)
            llm_messages = _build_llm_messages(history[:-1], message or "Please analyze the attached chart image.", attachments, context, language, intent)
            raw = LLMService().call_llm_api(llm_messages, temperature=0.35, use_json_mode=True)
            parsed = _parse_llm_json(raw)
            answer = str(parsed.get("answer") or raw or "").strip()
            if not answer:
                answer = "The model did not return a usable answer."

            assistant_id = _insert_message(cur, sid, user_id, "assistant", answer, [], intent, actions=parsed.get("actions") or [])
            cur.execute(
                "UPDATE qd_ai_copilot_sessions SET title = COALESCE(NULLIF(title, ''), ?), updated_at = NOW() WHERE id = ?",
                ((parsed.get("summary") or _title_from_message(message or answer))[:120], sid),
            )
            db.commit()
            cur.close()

        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {
                "session_id": sid,
                "message_id": assistant_id,
                "reply": answer,
                "intent": intent,
                "agent_intent": agent_plan,
                "confidence": parsed.get("confidence", 50),
                "actions": parsed.get("actions") or [],
                "memory_candidates": _detect_memory_candidates(message, language),
                "artifact": parsed.get("artifact") or {"type": "none"},
                "costs": costs,
            },
        })
    except Exception as e:
        logger.error(f"chat_message failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {_json_dumps(payload)}\n\n"


@ai_chat_blp.route("/chat/message/stream", methods=["POST"])
@login_required
def chat_message_stream():
    """Send a Copilot message and stream a Markdown response."""
    user_id = int(getattr(g, "user_id", 0) or 0)
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    language = (data.get("language") or request.headers.get("X-App-Lang") or "zh-CN").strip()
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    session_id = data.get("session_id") or data.get("chatId")

    try:
        attachments = _normalize_attachments(data.get("attachments") or [])
    except ValueError as e:
        return jsonify({"code": 0, "msg": str(e), "data": None}), 400
    if not message and not attachments:
        return jsonify({"code": 0, "msg": "Missing message", "data": None}), 400

    agent_plan = _get_or_classify_agent_intent(message, attachments, context, language)
    intent = str(agent_plan.get("intent") or _detect_intent(message, bool(attachments)))
    context["user_message"] = message
    context["intent"] = intent
    context["agent_intent"] = agent_plan
    context["language"] = language
    context = _enrich_context(context, has_image=bool(attachments))

    @stream_with_context
    def generate():
        sid = None
        costs = {}
        chunks: list[str] = []
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                _ensure_tables(cur)
                session = _get_session(cur, user_id, int(session_id)) if session_id else None
                if session:
                    sid = int(session["id"])
                else:
                    sid = _create_session(cur, user_id, _title_from_message(message or "Chart analysis"), context)
                user_message_id = _insert_message(cur, sid, user_id, "user", message or "[image]", attachments, intent)
                cur.execute("UPDATE qd_ai_copilot_sessions SET updated_at = NOW() WHERE id = ?", (sid,))
                db.commit()

                charged, charge_msg, costs = _charge(user_id, bool(attachments), f"copilot:{sid}:{user_message_id}")
                if not charged:
                    yield _sse("error", {"msg": charge_msg, "costs": costs})
                    return

                context["user_memories"] = _get_user_memories(cur, user_id)
                history = _load_recent_messages(cur, sid, limit=20)
                llm_messages = _build_llm_messages(
                    history[:-1],
                    message or "Please analyze the attached chart image.",
                    attachments,
                    context,
                    language,
                    intent,
                    json_response=False,
                )
                yield _sse("meta", {"session_id": sid, "user_message_id": user_message_id, "intent": intent, "agent_intent": agent_plan, "costs": costs})
                for delta in LLMService().stream_llm_api(llm_messages, temperature=0.35):
                    chunks.append(delta)
                    yield _sse("delta", {"text": delta})

                answer = "".join(chunks).strip() or "The model did not return a usable answer."
                assistant_id = _insert_message(cur, sid, user_id, "assistant", answer, [], intent)
                cur.execute(
                    "UPDATE qd_ai_copilot_sessions SET title = COALESCE(NULLIF(title, ''), ?), updated_at = NOW() WHERE id = ?",
                    (_title_from_message(message or answer)[:120], sid),
                )
                db.commit()
                cur.close()
                yield _sse("done", {
                    "session_id": sid,
                    "message_id": assistant_id,
                    "intent": intent,
                    "confidence": 50,
                    "costs": costs,
                    "memory_candidates": _detect_memory_candidates(message, language),
                })
        except Exception as e:
            logger.error(f"chat_message_stream failed: {e}", exc_info=True)
            yield _sse("error", {"msg": str(e), "session_id": sid, "costs": costs})

    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


@ai_chat_blp.route("/chat/sessions", methods=["GET"])
@login_required
def get_chat_sessions():
    user_id = int(getattr(g, "user_id", 0) or 0)
    limit = max(1, min(int(request.args.get("limit", 20) or 20), 100))
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            _ensure_tables(cur)
            cur.execute(
                """
                SELECT id, title, context_symbol, context_market, context_strategy_id, created_at, updated_at
                FROM qd_ai_copilot_sessions
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = [_row_to_dict(r) for r in (cur.fetchall() or [])]
            cur.close()
        return jsonify({"code": 1, "msg": "success", "data": rows})
    except Exception as e:
        logger.error(f"get_chat_sessions failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@ai_chat_blp.route("/chat/sessions/<int:session_id>", methods=["DELETE"])
@login_required
def delete_chat_session(session_id: int):
    """Delete one Copilot session and all related rows for the current user."""
    user_id = int(getattr(g, "user_id", 0) or 0)
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            _ensure_tables(cur)
            session = _get_session(cur, user_id, session_id)
            if not session:
                return jsonify({"code": 0, "msg": "session_not_found", "data": None}), 404
            cur.execute("DELETE FROM qd_ai_copilot_tool_calls WHERE session_id = ? AND user_id = ?", (session_id, user_id))
            cur.execute("DELETE FROM qd_ai_copilot_messages WHERE session_id = ? AND user_id = ?", (session_id, user_id))
            cur.execute("DELETE FROM qd_ai_copilot_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
            db.commit()
            cur.close()
        return jsonify({"code": 1, "msg": "success", "data": {"session_id": session_id}})
    except Exception as e:
        logger.error(f"delete_chat_session failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@ai_chat_blp.route("/chat/history", methods=["GET"])
@login_required
def get_chat_history():
    user_id = int(getattr(g, "user_id", 0) or 0)
    session_id = request.args.get("session_id") or request.args.get("chatId")
    if not session_id:
        return get_chat_sessions()
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            _ensure_tables(cur)
            session = _get_session(cur, user_id, int(session_id))
            if not session:
                return jsonify({"code": 0, "msg": "session_not_found", "data": None}), 404
            cur.execute(
                """
                SELECT id, role, content, attachments_json, actions_json,
                       report_json, report_target_json, report_error, report_error_tone,
                       intent, created_at
                FROM qd_ai_copilot_messages
                WHERE session_id = ? AND user_id = ?
                ORDER BY id ASC
                """,
                (int(session_id), user_id),
            )
            messages = []
            for row in cur.fetchall() or []:
                item = _row_to_dict(row)
                item["attachments"] = _json_loads(item.get("attachments_json"), [])
                item["actions"] = _json_loads(item.get("actions_json"), [])
                report = _json_loads(item.get("report_json"), None)
                report_target = _json_loads(item.get("report_target_json"), None)
                if isinstance(report, dict):
                    item["report"] = report
                if isinstance(report_target, dict):
                    item["reportTarget"] = report_target
                if item.get("report_error"):
                    item["reportError"] = item.get("report_error")
                if item.get("report_error_tone"):
                    item["reportErrorTone"] = item.get("report_error_tone")
                for key in ("attachments_json", "actions_json", "report_json", "report_target_json", "report_error", "report_error_tone"):
                    item.pop(key, None)
                messages.append(item)
            cur.close()
        return jsonify({"code": 1, "msg": "success", "data": {"session": session, "messages": messages}})
    except Exception as e:
        logger.error(f"get_chat_history failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@ai_chat_blp.route("/chat/message/local", methods=["POST"])
@login_required
def save_local_chat_message():
    """Persist a local Copilot message, including frontend-generated actions."""
    user_id = int(getattr(g, "user_id", 0) or 0)
    data = request.get_json(silent=True) or {}
    role = str(data.get("role") or "assistant").strip().lower()
    if role not in ("user", "assistant"):
        role = "assistant"
    report = data.get("report") if isinstance(data.get("report"), dict) else None
    report_target = data.get("reportTarget") if isinstance(data.get("reportTarget"), dict) else None
    report_error = str(data.get("reportError") or "").strip()[:1000]
    report_error_tone = str(data.get("reportErrorTone") or "").strip()[:32]
    content = str(data.get("content") or "").strip()
    if not content and report:
        symbol = report.get("symbol") or (report_target or {}).get("symbol") or "report"
        market = report.get("market") or (report_target or {}).get("market") or ""
        content = f"Analysis report: {market}:{symbol}".strip(":")
    if not content and report_error:
        content = f"Analysis failed: {report_error}"
    if not content:
        return jsonify({"code": 0, "msg": "Missing message content", "data": None}), 400

    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    intent = str(data.get("intent") or data.get("meta") or "local_agent").strip()[:64]
    session_id = data.get("session_id") or data.get("chatId")
    message_id = data.get("message_id")
    actions = data.get("actions") if isinstance(data.get("actions"), list) else []

    try:
        attachments = _normalize_attachments(data.get("attachments") or [])
    except ValueError as e:
        return jsonify({"code": 0, "msg": str(e), "data": None}), 400
    context = _enrich_context(context, has_image=bool(attachments))

    try:
        with get_db_connection() as db:
            cur = db.cursor()
            _ensure_tables(cur)
            sid = None
            if message_id:
                cur.execute(
                    "SELECT id, session_id FROM qd_ai_copilot_messages WHERE id = ? AND user_id = ?",
                    (int(message_id), user_id),
                )
                row = _row_to_dict(cur.fetchone())
                if row:
                    sid = int(row["session_id"])
                    cur.execute(
                        """
                        UPDATE qd_ai_copilot_messages
                        SET role = ?, content = ?, attachments_json = ?, actions_json = ?,
                            report_json = ?, report_target_json = ?, report_error = ?, report_error_tone = ?, intent = ?
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            role,
                            content,
                            _json_dumps(attachments),
                            _json_dumps(actions),
                            _json_dumps(report) if report else None,
                            _json_dumps(report_target) if report_target else None,
                            report_error or None,
                            report_error_tone or None,
                            intent,
                            int(message_id),
                            user_id,
                        ),
                    )
                    cur.execute("UPDATE qd_ai_copilot_sessions SET updated_at = NOW() WHERE id = ?", (sid,))
                    db.commit()
                    cur.close()
                    return jsonify({"code": 1, "msg": "success", "data": {"session_id": sid, "message_id": int(message_id)}})

            session = _get_session(cur, user_id, int(session_id)) if session_id else None
            if session:
                sid = int(session["id"])
            else:
                sid = _create_session(cur, user_id, _title_from_message(content), context)
            mid = _insert_message(
                cur,
                sid,
                user_id,
                role,
                content,
                attachments,
                intent,
                actions=actions,
                report=report,
                report_target=report_target,
                report_error=report_error,
                report_error_tone=report_error_tone,
            )
            cur.execute("UPDATE qd_ai_copilot_sessions SET updated_at = NOW() WHERE id = ?", (sid,))
            db.commit()
            cur.close()
        return jsonify({"code": 1, "msg": "success", "data": {"session_id": sid, "message_id": mid}})
    except Exception as e:
        logger.error(f"save_local_chat_message failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


def _has_cjk_text(value: Any) -> bool:
    text = _plain_text(value)
    return bool(re.search(r"[\u2e80-\u9fff\uac00-\ud7af\u3040-\u30ff]", text))


def _register_report_pdf_font(prefer_cjk: bool = False) -> str:
    from pathlib import Path

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("C:/Windows/Fonts/msyh.ttc", True),
        ("C:/Windows/Fonts/msyh.ttf", True),
        ("C:/Windows/Fonts/simsun.ttc", True),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", True),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", True),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", True),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", False),
    ]
    for path, is_cjk in candidates:
        if prefer_cjk and not is_cjk:
            continue
        try:
            if Path(path).exists():
                pdfmetrics.registerFont(TTFont("QuantDingerSans", path))
                return "QuantDingerSans"
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


def _build_ai_report_pdf(report: dict, target: dict | None = None, language: str = "en-US") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen import canvas

    target = target or {}
    zh = str(language or "").lower().startswith("zh")
    prefer_cjk = zh or _has_cjk_text(report) or _has_cjk_text(target)
    font_name = _register_report_pdf_font(prefer_cjk=prefer_cjk)
    title_font = font_name
    width, height = A4
    margin = 18 * mm
    y = height - margin
    line_gap = 4
    page_no = 1
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    labels = {
        "title": "QuantDinger AI Analysis Report" if not zh else "QuantDinger AI 分析报告",
        "target": "Target" if not zh else "标的",
        "generated": "Generated" if not zh else "生成时间",
        "summary": "Summary" if not zh else "摘要",
        "plan": "Trading Plan" if not zh else "交易计划",
        "scores": "Scores" if not zh else "评分",
        "trend": "Trend Outlook" if not zh else "周期趋势预测",
        "crypto": "Crypto Market Structure" if not zh else "加密市场结构",
        "details": "Detailed Analysis" if not zh else "详细分析",
        "reasons": "Key Reasons" if not zh else "核心理由",
        "risks": "Risks" if not zh else "风险提示",
        "indicators": "Indicators" if not zh else "技术指标",
    }

    def fit_text(text: Any, max_width: float, font_size: int = 10) -> list[str]:
        raw = _plain_text(text).replace("\r\n", "\n").replace("\r", "\n")
        lines: list[str] = []
        for paragraph in raw.split("\n"):
            if not paragraph:
                lines.append("")
                continue
            current = ""
            tokens = paragraph.split(" ")
            if len(tokens) == 1:
                tokens = list(paragraph)
                joiner = ""
            else:
                joiner = " "
            for token in tokens:
                candidate = token if not current else current + joiner + token
                if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = token
            if current:
                lines.append(current)
        return lines

    def footer() -> None:
        c.setFont(font_name, 8)
        c.setFillColor(colors.HexColor("#8a94a6"))
        c.drawRightString(width - margin, 12 * mm, f"QuantDinger · {page_no}")

    def ensure(space: float) -> None:
        nonlocal y, page_no
        if y - space >= margin:
            return
        footer()
        c.showPage()
        page_no += 1
        y = height - margin

    def draw_text(text: Any, font_size: int = 10, color: str = "#1f2937", bold: bool = False, indent: float = 0) -> None:
        nonlocal y
        c.setFillColor(colors.HexColor(color))
        c.setFont(title_font if bold else font_name, font_size)
        max_width = width - margin * 2 - indent
        lines = fit_text(text, max_width, font_size)
        for line in lines:
            ensure(font_size + line_gap)
            c.drawString(margin + indent, y, line)
            y -= font_size + line_gap

    def draw_section(title: str) -> None:
        nonlocal y
        ensure(24)
        y -= 5
        c.setStrokeColor(colors.HexColor("#d9e2f2"))
        c.line(margin, y, width - margin, y)
        y -= 17
        draw_text(title, 13, "#0f172a", True)

    def draw_pairs(items: list[tuple[str, Any]]) -> None:
        nonlocal y
        for i in range(0, len(items), 2):
            ensure(20)
            row = items[i:i + 2]
            for col, (name, value) in enumerate(row):
                x = margin + col * ((width - margin * 2) / 2)
                c.setFont(font_name, 8)
                c.setFillColor(colors.HexColor("#7b8797"))
                c.drawString(x, y, _plain_text(name))
                c.setFont(font_name, 10)
                c.setFillColor(colors.HexColor("#111827"))
                c.drawString(x + 32 * mm, y, _plain_text(value)[:48])
            y -= 15

    def add_list(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            draw_text(f"• {_plain_text(item)}", 10, "#374151", False, 3 * mm)

    symbol = report.get("symbol") or target.get("symbol") or ""
    market = report.get("market") or target.get("market") or ""
    c.setTitle(labels["title"])
    c.setFont(title_font, 20)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.drawString(margin, y, labels["title"])
    y -= 20
    draw_pairs([
        (labels["target"], f"{market}:{symbol}" if market or symbol else "-"),
        (labels["generated"], _now_utc().strftime("%Y-%m-%d %H:%M UTC")),
    ])

    decision = _plain_text(report.get("decision") or "HOLD").upper()
    decision_color = "#16a34a" if decision == "BUY" else "#dc2626" if decision == "SELL" else "#d97706"
    ensure(36)
    c.setFillColor(colors.HexColor(decision_color))
    c.roundRect(margin, y - 24, 42 * mm, 22, 5, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(title_font, 13)
    c.drawCentredString(margin + 21 * mm, y - 17, decision)
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont(font_name, 10)
    c.drawString(margin + 48 * mm, y - 10, f"Confidence: {report.get('confidence', '-')}")
    y -= 34

    if report.get("summary"):
        draw_section(labels["summary"])
        draw_text(report.get("summary"), 10)

    market_data = report.get("market_data") if isinstance(report.get("market_data"), dict) else {}
    plan = report.get("trading_plan") if isinstance(report.get("trading_plan"), dict) else {}
    plan_items = [
        ("Current Price", market_data.get("current_price")),
        ("24h Change", market_data.get("change_24h")),
        ("Entry", plan.get("entry_price") or plan.get("entryPrice")),
        ("Stop Loss", plan.get("stop_loss") or plan.get("stopLoss")),
        ("Take Profit", plan.get("take_profit") or plan.get("takeProfit")),
        ("Risk/Reward", plan.get("risk_reward_ratio") or plan.get("riskRewardRatio")),
    ]
    if any(v not in (None, "") for _, v in plan_items):
        draw_section(labels["plan"])
        draw_pairs([(k, "-" if v in (None, "") else v) for k, v in plan_items])

    scores = report.get("scores") if isinstance(report.get("scores"), dict) else {}
    if scores:
        draw_section(labels["scores"])
        draw_pairs([(str(k).replace("_", " ").title(), v) for k, v in scores.items()])

    trend = report.get("trend_outlook") or report.get("trendOutlook")
    trend_summary = report.get("trend_outlook_summary") or report.get("trendOutlookSummary")
    if trend_summary or trend:
        draw_section(labels["trend"])
        if trend_summary:
            draw_text(trend_summary, 10)
        if isinstance(trend, dict):
            draw_pairs([(str(k), _plain_text(v)) for k, v in trend.items()])

    crypto_summary = report.get("crypto_factor_summary")
    crypto_factors = report.get("crypto_factors") if isinstance(report.get("crypto_factors"), dict) else {}
    if crypto_summary or crypto_factors:
        draw_section(labels["crypto"])
        if crypto_summary:
            draw_text(crypto_summary, 10)
        if crypto_factors:
            draw_pairs([(str(k).replace("_", " "), _plain_text(v)) for k, v in crypto_factors.items() if k != "signals"])

    details = report.get("detailed_analysis") if isinstance(report.get("detailed_analysis"), dict) else {}
    if details:
        draw_section(labels["details"])
        for key, value in details.items():
            draw_text(str(key).replace("_", " ").title(), 11, "#0f172a", True)
            draw_text(value, 10)

    if report.get("reasons"):
        draw_section(labels["reasons"])
        add_list(report.get("reasons"))
    if report.get("risks"):
        draw_section(labels["risks"])
        add_list(report.get("risks"))

    indicators = report.get("indicators") if isinstance(report.get("indicators"), dict) else {}
    if indicators:
        draw_section(labels["indicators"])
        draw_pairs([(str(k).replace("_", " "), _plain_text(v)) for k, v in indicators.items()])

    footer()
    c.save()
    return buf.getvalue()


@ai_chat_blp.route("/chat/report/pdf", methods=["POST"])
@login_required
def export_chat_report_pdf():
    data = request.get_json(silent=True) or {}
    report = data.get("report") if isinstance(data.get("report"), dict) else None
    if not report:
        return jsonify({"code": 0, "msg": "Missing report data", "data": None}), 400
    target = data.get("target") if isinstance(data.get("target"), dict) else {}
    language = str(data.get("language") or request.headers.get("X-App-Lang") or "en-US")
    try:
        pdf_bytes = _build_ai_report_pdf(report, target, language)
    except ImportError:
        return jsonify({"code": 0, "msg": "PDF export dependency missing: install reportlab", "data": None}), 500
    except Exception as e:
        logger.error(f"export_chat_report_pdf failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500

    symbol = re.sub(r"[^A-Za-z0-9._-]+", "_", _plain_text(report.get("symbol") or target.get("symbol") or "report")).strip("_")
    filename = f"QuantDinger_{symbol or 'report'}_{_now_utc().strftime('%Y%m%d')}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@ai_chat_blp.route("/chat/history/save", methods=["POST"])
@login_required
def save_chat_history():
    """Compatibility endpoint; chat/message persists automatically."""
    return jsonify({"code": 1, "msg": "success", "data": None})


# openapi-compat: legacy import name
ai_chat_bp = ai_chat_blp
