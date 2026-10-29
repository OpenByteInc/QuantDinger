"""Localized text helpers for portfolio monitor alerts."""

from __future__ import annotations

from typing import Any, Dict

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
    "pt-BR": {
        "price_above": "Alerta de preço: {symbol} preço atual ${current_price:.4f} ultrapassou ${threshold:.4f}",
        "price_below": "Alerta de preço: {symbol} preço atual ${current_price:.4f} caiu abaixo de ${threshold:.4f}",
        "pnl_above": "Alerta de lucro: {symbol} L/P {pnl_percent:.1f}% atingiu a meta de {threshold:.1f}%",
        "pnl_below": "Alerta de perda: {symbol} L/P {pnl_percent:.1f}% atingiu o stop loss de {threshold:.1f}%",
        "alert_title": "Alerta de Preço/L/P",
    },
}


def normalize_language(language: str = "en-US") -> str:
    lang = str(language or "").lower()
    if lang.startswith("zh"):
        return "zh-CN"
    if lang.startswith("pt"):
        return "pt-BR"
    return "en-US"


def get_alert_message(alert_type: str, language: str = "en-US", **kwargs: Any) -> str:
    """Return a localized alert message."""
    lang = normalize_language(language)
    template = ALERT_MESSAGES.get(lang, ALERT_MESSAGES["en-US"]).get(alert_type, "")
    return template.format(**kwargs) if template else ""


def get_alert_title(language: str = "en-US") -> str:
    """Return a localized alert title."""
    lang = normalize_language(language)
    return ALERT_MESSAGES.get(lang, ALERT_MESSAGES["en-US"]).get("alert_title", "Alert")
