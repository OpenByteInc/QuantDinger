"""Localized text helpers for portfolio monitor alerts."""

from __future__ import annotations

from typing import Any, Dict

from app.utils import i18n

ALERT_MESSAGES: Dict[str, Dict[str, str]] = {
    "zh-CN": {
        "price_above": "价格突破预警：{symbol} 当前价格 ${current_price:.4f} 已高于 ${threshold:.4f}",
        "price_below": "价格跌破预警：{symbol} 当前价格 ${current_price:.4f} 已低于 ${threshold:.4f}",
        "pnl_above": "盈利预警：{symbol} 当前盈亏 {pnl_percent:.1f}% 已达到 {threshold:.1f}% 目标",
        "pnl_below": "亏损预警：{symbol} 当前盈亏 {pnl_percent:.1f}% 已触发 {threshold:.1f}% 止损线",
        "alert_title": "价格/盈亏预警",
    },
    "en-US": {
        "price_above": "Price alert: {symbol} current price ${current_price:.4f} has exceeded ${threshold:.4f}",
        "price_below": "Price alert: {symbol} current price ${current_price:.4f} has dropped below ${threshold:.4f}",
        "pnl_above": "Profit alert: {symbol} P&L {pnl_percent:.1f}% has reached {threshold:.1f}% target",
        "pnl_below": "Loss alert: {symbol} P&L {pnl_percent:.1f}% has hit {threshold:.1f}% stop-loss",
        "alert_title": "Price/P&L Alert",
    },
}


def normalize_language(language: str = "en-US") -> str:
    return "zh-CN" if language and str(language).startswith("zh") else "en-US"


def get_alert_message(alert_type: str, language: str = "en-US", **kwargs: Any) -> str:
    """Return a localized alert message.

    Prefer translations from the project's copilot-zh-cn JSON if available; fall back to
    the built-in ALERT_MESSAGES mapping otherwise.
    """
    lang = normalize_language(language)
    # Try project translations first
    tpl = i18n.t(alert_type, locale=lang)
    if tpl:
        try:
            return tpl.format(**kwargs)
        except Exception:
            # formatting failed; fall back
            pass
    # Fallback to built-in messages
    template = ALERT_MESSAGES.get(lang, ALERT_MESSAGES["en-US"]).get(alert_type, "")
    return template.format(**kwargs) if template else ""


def get_alert_title(language: str = "en-US") -> str:
    """Return a localized alert title."""
    lang = normalize_language(language)
    tpl = i18n.t("alert_title", locale=lang)
    if tpl:
        return tpl
    return ALERT_MESSAGES.get(lang, ALERT_MESSAGES["en-US"]).get("alert_title", "Alert")
