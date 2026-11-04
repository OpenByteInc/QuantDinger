"""Read-only, versioned evidence for strategy reviews; never executes source code."""

import ast
import hashlib
import math
import re
from collections import Counter, defaultdict, deque
from statistics import median

from app.utils.trade_close_reason import infer_legacy_close_reason, is_exit_trade_type


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def source_context(code, params, version_id=None):
    context = {
        "version_id": version_id, "available": False, "hash": None,
        "parameters": {}, "outline": "", "truncated": False,
        "scope": "current_deployment_only", "moving_average_calls": False,
    }
    # Only numeric strategy parameters leave this boundary; credentials and URLs do not.
    for key, value in (params.items() if isinstance(params, dict) else []):
        lowered = str(key).lower()
        if any(word in lowered for word in ("secret", "token", "password", "key", "credential")):
            continue
        if isinstance(value, (bool, int, float)) and number(value) is not None:
            context["parameters"][str(key)[:80]] = value
        if len(context["parameters"]) >= 50:
            break
    if not code:
        return context
    context["hash"] = hashlib.sha256(code.encode()).hexdigest()
    if len(code) > 200_000:
        return context
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "subscribe":
                for keyword in node.keywords:
                    if keyword.arg == "frequency" and isinstance(keyword.value, ast.Constant):
                        frequency = str(keyword.value.value)
                        if re.fullmatch(r"[1-9]\d*[mhd]", frequency):
                            context["signal_timeframe"] = frequency
        calls = {node.func.attr.lower() for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        names = {node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name)}
        context["moving_average_calls"] = bool(calls & {"sma", "ema", "ewm"}) or (
            "mean" in calls and any("fast" in name for name in names) and any("slow" in name for name in names)
        )
        class RedactStrings(ast.NodeTransformer):
            def visit_Constant(self, node):
                if isinstance(node.value, (str, bytes)):
                    return ast.copy_location(ast.Constant(value="<redacted>"), node)
                return node
        outline = ast.unparse(RedactStrings().visit(tree))
        context.update(available=True, outline=outline[:16000], truncated=len(outline) > 16000)
    except (SyntaxError, ValueError, RecursionError):
        pass
    return context


def holding_evidence(all_trades, since_ts, now_ts, timeframe):
    """Quantity-weighted FIFO holding times per fully matched close fill, including pre-window entries."""
    match = re.fullmatch(r"([1-9]\d*)([mhd])", str(timeframe or ""))
    threshold = int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]] * 5 if match else None
    lots = defaultdict(deque)
    durations, short_pnls = [], []
    buckets = [{"code": code, "count": 0, "pnl_count": 0, "net_pnl": 0.0}
               for code in ("under_5m", "5m_30m", "30m_2h", "over_2h")]
    unmatched = 0
    for row in sorted(all_trades, key=lambda t: (t.get("created_at") or 0, t.get("id") or 0)):
        stamp = number(row.get("created_at"))
        amount = number(row.get("amount"))
        kind = str(row.get("type") or "").lower()
        side = "long" if "long" in kind else "short" if "short" in kind else ""
        if stamp is None or stamp > now_ts or amount is None or amount <= 0 or not side:
            if is_exit_trade_type(kind) and stamp is not None and since_ts <= stamp <= now_ts:
                unmatched += 1
            continue
        queue = lots[(str(row.get("symbol") or ""), side)]
        if not is_exit_trade_type(kind):
            queue.append([amount, stamp])
            continue
        remaining, weighted_age = amount, 0.0
        while remaining > 1e-10 and queue:
            take = min(remaining, queue[0][0])
            weighted_age += take * (stamp - queue[0][1])
            remaining -= take
            queue[0][0] -= take
            if queue[0][0] <= 1e-10:
                queue.popleft()
        if stamp < since_ts:
            continue
        if remaining > max(1e-10, amount * 1e-8):
            unmatched += 1
            continue
        duration = weighted_age / amount
        durations.append(duration)
        pnl = number(row.get("profit"))
        bucket = buckets[0 if duration <= 300 else 1 if duration <= 1800 else 2 if duration <= 7200 else 3]
        bucket["count"] += 1
        if pnl is not None:
            bucket["pnl_count"] += 1
            bucket["net_pnl"] += pnl
        if threshold is not None and duration <= threshold and pnl is not None:
            short_pnls.append(pnl)
    return {
        "matched_close_count": len(durations), "unmatched_close_count": unmatched,
        "median_holding_seconds": median(durations) if durations else None,
        "short_hold_threshold_seconds": threshold, "short_hold_count": len(short_pnls),
        "short_hold_net_pnl": sum(short_pnls) if short_pnls else None,
        "holding_groups": [{**bucket, "net_pnl": bucket["net_pnl"] if bucket["pnl_count"] else None} for bucket in buckets],
    }


def settings_diagnostics(trades, metrics, strategy, context, facts, cost_usable, since_ts, now_ts):
    """Describe observed costs and exit cohorts without claiming causal parameter attribution."""
    reasons = {
        "stop_loss": {"server_stop_loss", "stop_loss", "grid_equity_stop_loss", "dca_equity_stop_loss", "robot_equity_stop_loss"},
        "take_profit": {"server_take_profit", "take_profit", "grid_equity_take_profit", "dca_equity_take_profit", "robot_equity_take_profit"},
        "trailing": {"server_trailing_stop", "trailing_stop", "grid_equity_trailing_stop", "dca_equity_trailing_stop", "robot_equity_trailing_stop"},
        "liquidation": {"exchange_liquidation"},
        "signal": {"indicator_signal", "signal_close", "signal_trigger", "legacy_signal_trigger"},
    }
    groups = {key: {"code": key, "count": 0, "recorded_count": 0, "inferred_count": 0,
                    "pnl_count": 0, "net_pnl": 0.0, "negative_pnl_abs": 0.0}
              for key in (*reasons, "other", "unknown")}
    config = strategy.get("trading_config")
    config = config if isinstance(config, dict) else {}
    bot_type = str(strategy.get("bot_type") or config.get("bot_type") or "")
    closed = [t for t in trades if is_exit_trade_type(str(t.get("type") or ""))]
    for trade in closed:
        recorded = str(trade.get("close_reason") or "").strip().lower()
        reason = recorded or infer_legacy_close_reason(str(trade.get("type") or ""), bot_type=bot_type)
        key = next((key for key, values in reasons.items() if reason in values), "other" if recorded else "unknown")
        group = groups[key]
        group["count"] += 1
        group["recorded_count" if recorded else "inferred_count"] += 1
        pnl = number(trade.get("profit"))
        if pnl is not None:
            group["pnl_count"] += 1
            group["net_pnl"] += pnl
            group["negative_pnl_abs"] += max(0, -pnl)
    for group in groups.values():
        if not group["pnl_count"] or not cost_usable:
            group["net_pnl"] = group["negative_pnl_abs"] = None
    days = (now_ts - since_ts) / 86400 if now_ts > since_ts else None
    capital = number(strategy.get("initial_capital") or config.get("initial_capital"))
    leverage = number(config.get("leverage", strategy.get("leverage")))
    if config.get("leverage_enabled") is False:
        leverage = 1.0
    if leverage is not None and leverage < 1:
        leverage = None
    losses = [number(t.get("profit")) for t in closed if number(t.get("profit")) is not None and number(t.get("profit")) < 0]
    worst = -min(losses) if losses and cost_usable else None
    facts.update({
        "events_per_day": len(trades) / days if days else None,
        "closes_per_day": len(closed) / days if days else None,
        "stop_loss_fills": groups["stop_loss"]["count"],
        "stop_loss_net": groups["stop_loss"]["net_pnl"],
        "take_profit_fills": groups["take_profit"]["count"],
        "take_profit_net": groups["take_profit"]["net_pnl"],
        "unknown_exit_fills": groups["unknown"]["count"],
        "recorded_exit_reason_fills": sum(group["recorded_count"] for group in groups.values()),
        "inferred_exit_reason_fills": sum(group["inferred_count"] for group in groups.values()),
        "exit_reason_coverage_pct": 100 * (len(closed) - groups["unknown"]["count"]) / len(closed) if closed else None,
        "configured_leverage": leverage,
        "worst_close_loss": worst,
        "worst_close_initial_pct": worst / capital * 100 if worst is not None and capital and capital > 0 else None,
        "liquidation_fills": groups["liquidation"]["count"],
    })
    settings = []
    for key in ("stop_loss_pct", "take_profit_pct", "equity_stop_loss_pct", "equity_take_profit_pct"):
        value = number(config.get(key))
        if value is not None:
            settings.append({"key": key, "value": value, "origin": "deployment"})
    for key, value in context.get("parameters", {}).items():
        if re.search(r"stop|profit|trailing|risk|position|size|leverage", key, re.I):
            settings.append({"key": key, "value": value, "origin": "parameter"})
    def check(code, status, finding, experiment, keys):
        return {"code": code, "status": status, "finding": finding, "experiment": experiment,
                "evidence_keys": keys, "validation_status": "not_run"}
    net = facts["window_net_pnl"]
    before = facts["before_commission_pnl"]
    cost_finding = "cost_unknown" if before is None else "cost_negative" if net < 0 and before < 0 else "cost_flipped" if net < 0 else "cost_recorded"
    short_loss = cost_usable and facts["short_hold_count"] >= 5 and (facts["short_hold_net_pnl"] or 0) < 0
    protective_exit_count = sum(groups[key]["count"] for key in ("stop_loss", "take_profit", "trailing", "liquidation"))
    classified_exit_count = len(closed) - groups["unknown"]["count"]
    exit_finding = "exits_observed" if protective_exit_count else "exits_signal_only" if classified_exit_count else "exits_unknown"
    checks = [
        check("cost_check", "observed" if before is not None else "insufficient", cost_finding,
              "cost_sensitivity" if cost_usable else "reconcile_fees", ["window_net_pnl", "commission", "before_commission_pnl"]),
        check("frequency_check", "review" if short_loss else "insufficient", "frequency_short" if short_loss else "frequency_unknown",
              "frequency_filter", ["events_per_day", "closes_per_day", "short_hold_count", "short_hold_net_pnl"]),
        check("exits_check", "review" if classified_exit_count else "insufficient", exit_finding,
              "exit_timing", ["stop_loss_fills", "stop_loss_net", "take_profit_fills", "take_profit_net",
                              "recorded_exit_reason_fills", "inferred_exit_reason_fills", "exit_reason_coverage_pct"]),
        check("risk_check", "review" if groups["liquidation"]["count"] else "insufficient", "risk_liquidation" if groups["liquidation"]["count"] else "risk_unknown",
              "risk_budget", ["configured_leverage", "worst_close_loss", "worst_close_initial_pct", "liquidation_fills"]),
    ]
    if groups["liquidation"]["count"]:
        checks.insert(0, checks.pop())
    return checks, list(groups.values()), settings[:30]


def _compact_trade_ledger(trades, limit=400, bot_type=""):
    fields = (
        "id", "created_at", "symbol", "type", "price", "amount", "value",
        "profit", "profit_gross", "commission", "commission_quote", "commission_ccy",
        "close_reason", "matched_entry_price",
    )
    ordered = sorted(trades, key=lambda row: (row.get("created_at") or 0, row.get("id") or 0))
    included = ordered if len(ordered) <= limit else ordered[-limit:]
    ledger = []
    for row in included:
        item = {key: row.get(key) for key in fields}
        recorded = str(row.get("close_reason") or "").strip()
        effective = recorded or infer_legacy_close_reason(str(row.get("type") or ""), bot_type=bot_type)
        item["review_close_reason"] = effective
        item["review_close_reason_source"] = "recorded" if recorded else "inferred" if effective else "missing"
        ledger.append(item)
    return ledger, len(ordered) <= limit


def _safe_log_events(logs, limit=80):
    events = []
    for row in list(logs or [])[:limit]:
        if not isinstance(row, dict):
            continue
        message = str(row.get("message") or "")[:1200]
        message = re.sub(r"(?i)\b(bearer)\s+\S+", r"\1 <redacted>", message)
        message = re.sub(r"(?i)\b(api[_-]?key|secret|token|password|credential)\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", message)
        message = re.sub(r"https?://\S+", "<redacted-url>", message)
        message = re.sub(r"\b[A-Fa-f0-9]{32,}\b", "<redacted-id>", message)
        events.append({
            "id": row.get("id"),
            "level": str(row.get("level") or "unknown")[:24],
            "message": message[:600],
            "created_at": row.get("created_at"),
        })
    return events


def _compact_positions(positions, limit=100):
    fields = (
        "id", "symbol", "side", "size", "entry_price", "current_price",
        "unrealized_pnl", "created_at", "updated_at",
    )
    return [{key: row.get(key) for key in fields} for row in list(positions or [])[:limit] if isinstance(row, dict)]


def build_evidence(*, metrics, trades, strategy, context, since_ts, now_ts, log_levels, all_trades=None,
                   positions=None, logs=None):
    all_closed = [t for t in trades if is_exit_trade_type(str(t.get("type") or ""))]
    closed = [t for t in trades if is_exit_trade_type(str(t.get("type") or "")) and number(t.get("profit")) is not None]
    pnls = [number(t["profit"]) for t in closed]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    win_sum, loss_sum = sum(wins), -sum(losses)
    pf = win_sum / loss_sum if loss_sum else None
    payoff = (win_sum / len(wins)) / (loss_sum / len(losses)) if wins and losses else None
    net = number(metrics.get("window_net_pnl")) or 0.0
    fee = number(metrics.get("total_commission")) or 0.0
    unconverted = 0
    unspecified = 0
    quote_currencies = set()
    for trade in trades:
        currency = str(trade.get("commission_ccy") or "").upper()
        symbol = str(trade.get("symbol") or strategy.get("symbol") or "").upper()
        symbol = symbol.split("@")[0]
        quote = symbol.split("/")[-1].split(":")[0] if "/" in symbol else ""
        if not quote:
            quote = next((s for s in ("USDT", "USDC", "USD", "BTC", "ETH") if symbol.endswith(s)), "")
        if quote:
            quote_currencies.add(quote)
        if trade.get("commission_quote") not in (None, ""):
            if number(trade.get("commission_quote")) is None:
                unconverted += 1
        elif number(trade.get("commission")) is None:
            unconverted += 1
        elif abs(number(trade.get("commission")) or 0) > 0:
            if currency and currency != quote:
                unconverted += 1
            elif not currency:
                unspecified += 1
    cost_usable = unconverted == 0 and len(quote_currencies) <= 1
    before = net + fee if cost_usable and trades else None
    ordered = sorted(closed, key=lambda t: (t.get("created_at") or 0, t.get("id") or 0))
    gaps = [b["created_at"] - a["created_at"] for a, b in zip(ordered, ordered[1:])
            if isinstance(a.get("created_at"), (int, float)) and isinstance(b.get("created_at"), (int, float))
            and b["created_at"] > a["created_at"]]
    facts = {
        "window_net_pnl": net, "commission": fee if cost_usable else None,
        "before_commission_pnl": before, "closed_fill_count": len(closed),
        "profit_factor": pf, "profit_factor_state": "finite" if loss_sum else ("no_losses" if wins else "no_decided_fills"),
        "payoff_ratio": payoff, "avg_net_per_close": sum(pnls) / len(pnls) if pnls else None,
        "median_close_gap_seconds": median(gaps) if gaps else None,
        "top_5_profit_share": sum(sorted(wins, reverse=True)[:5]) / win_sum if win_sum else None,
        "max_consecutive_losses": metrics.get("max_consecutive_losses", 0),
    }
    config = strategy.get("trading_config") or {}
    holding = holding_evidence(all_trades if all_trades is not None else trades, since_ts, now_ts,
                               config.get("timeframe") or context.get("signal_timeframe"))
    holding_groups = holding.pop("holding_groups")
    if not cost_usable:
        for group in holding_groups:
            group["net_pnl"] = None
    facts.update(holding)
    checks, exit_groups, settings = settings_diagnostics(trades, metrics, strategy, context, facts, cost_usable, since_ts, now_ts)
    issues = []
    def issue(code, priority, evidence, experiment):
        issues.append({"code": code, "priority": priority, "status": "hypothesis",
                       "evidence_keys": evidence, "experiment": experiment, "validation_status": "not_run"})
    if not cost_usable:
        issue("fee_data", "high", ["closed_fill_count"], "reconcile_fees")
    elif closed and net < 0:
        issue("cost_drag" if before >= 0 else "negative_edge", "high",
              ["window_net_pnl", "before_commission_pnl", "commission"], "cost_sensitivity")
    if cost_usable and closed and net < 0:
        short_losses = holding["short_hold_count"] >= 5 and (holding["short_hold_net_pnl"] or 0) < 0
        issue("short_holds" if short_losses else "signal_quality", "high",
              ["short_hold_count", "short_hold_net_pnl", "short_hold_threshold_seconds"] if short_losses else ["closed_fill_count", "avg_net_per_close", "profit_factor"],
              "ma_filter" if context.get("moving_average_calls") else "entry_filter")
    if cost_usable and len(closed) >= 5 and facts["top_5_profit_share"] is not None and facts["top_5_profit_share"] >= 0.5:
        issue("concentration", "medium", ["top_5_profit_share", "max_consecutive_losses"], "walk_forward")
    if not issues or len(closed) < 5:
        issue("sample_limit", "medium", ["closed_fill_count"], "forward_observe")
    issues = issues[:3]
    ordered_samples = sorted(closed, key=lambda t: (number(t.get("profit")), t.get("id") or 0))[:10]
    ordered_samples += trades[-20:]
    seen, samples = set(), []
    for trade in ordered_samples:
        identity = (trade.get("id"), trade.get("created_at"), trade.get("type"), trade.get("price"))
        if identity in seen:
            continue
        seen.add(identity)
        samples.append({key: trade.get(key) for key in (
            "id", "created_at", "symbol", "type", "price", "amount", "profit", "profit_gross", "commission_quote", "commission"
        )})
    bot_type = str(strategy.get("bot_type") or config.get("bot_type") or "")
    trade_ledger, trade_ledger_complete = _compact_trade_ledger(trades, bot_type=bot_type)
    log_events = _safe_log_events(logs)
    current_positions = _compact_positions(positions)
    execution_mode = str(strategy.get("execution_mode") or config.get("execution_mode") or "signal").lower()
    market_type = str(config.get("market_type") or strategy.get("market_type") or "").lower()
    derivative_market = any(token in market_type for token in ("future", "perpetual", "swap", "margin"))
    gaps_list = []
    if not trade_ledger_complete:
        gaps_list.append("trade_ledger_truncated")
    if all_closed and facts.get("exit_reason_coverage_pct") != 100:
        gaps_list.append("exit_reason")
    if trades:
        gaps_list.extend(("market_path", "historical_positions", "version_continuity"))
    if derivative_market:
        gaps_list.append("funding")
    if execution_mode != "signal" and trades:
        gaps_list.append("execution_cost")
    if not context.get("available"):
        gaps_list.append("source")
    if context.get("truncated"):
        gaps_list.append("source_truncated")
    if unconverted:
        gaps_list.append("fee_conversion")
    if len(quote_currencies) > 1:
        gaps_list.append("mixed_quote")
    if unspecified:
        gaps_list.append("fee_currency_assumed")
    if holding["unmatched_close_count"]:
        gaps_list.append("unmatched_holds")
    return {
        "version": 2, "window": {"start": since_ts, "end": now_ts},
        "facts": facts, "issues": issues, "checks": checks, "exit_groups": exit_groups,
        "current_settings": settings, "holding_groups": holding_groups,
        "coverage": {"gaps": gaps_list, "unconverted_fees": unconverted, "cost_usable": cost_usable,
                     "unspecified_fee_currency": unspecified, "source_available": bool(context.get("available")),
                     "trade_rows_total": len(trades), "trade_rows_included": len(trade_ledger),
                     "trade_ledger_complete": trade_ledger_complete, "closed_rows_total": len(all_closed),
                     "exit_reason_rows": len(all_closed) - facts.get("unknown_exit_fills", 0),
                     "exit_reason_recorded_rows": facts.get("recorded_exit_reason_fills", 0),
                     "exit_reason_inferred_rows": facts.get("inferred_exit_reason_fills", 0),
                     "current_position_rows": len(current_positions), "log_rows_total": len(logs or []),
                     "log_rows_included": len(log_events), "position_scope": "current_snapshot",
                     "source_scope": context.get("scope") or "current_deployment_only"},
        "source": {key: value for key, value in context.items() if key != "outline"},
        "trade_ledger": trade_ledger,
        "current_positions": current_positions,
        "log_events": log_events,
        "trade_samples": samples, "sample_policy": "worst_10_and_latest_20_not_representative",
        "log_levels": dict(Counter(log_levels)),
        "verdict": "insufficient_data" if len(closed) < 5 or not cost_usable else ("review_required" if net < 0 else "unvalidated"),
    }


def normalize_ai_review(result, evidence):
    """Accept only grounded issue references; never accept model-produced execution states."""
    if not isinstance(result, dict) or not isinstance(result.get("summary"), str) or not result["summary"].strip():
        return None
    allowed = {item["code"] for item in (evidence.get("checks") or evidence["issues"])}
    annotations = []
    seen = set()
    for item in result.get("issues", []) if isinstance(result.get("issues"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str) or item["code"] not in allowed or item["code"] in seen:
            continue
        fields = ("interpretation", "proposed_change", "tradeoff")
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in fields):
            continue
        seen.add(item["code"])
        annotations.append({"code": item["code"], **{key: item[key].strip()[:1600] for key in fields}})
    if not annotations:
        return None
    return {"summary": result["summary"].strip()[:2400], "issues": annotations}
