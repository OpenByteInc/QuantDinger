"""Localized text for indicator signal notifications (Telegram, email, plain text)."""

from __future__ import annotations

from typing import Dict

SIGNAL_TEXTS: Dict[str, Dict[str, str]] = {
    "en-US": {
        "buy": "BUY",
        "sell": "SELL",
        "watch": "WATCH",
        "signal": "SIGNAL",
        "heading": "Indicator signal",
        "indicator": "Indicator",
        "market": "Market",
        "symbol": "Symbol",
        "timeframe": "Timeframe",
        "price": "Price",
        "signal_candle": "Signal candle",
        "notify_candle": "Sent on candle",
        "marker": "Chart marker",
        "rule": "Confirmed when the signal candle closed and sent on the next candle, so it does not repaint.",
        "rule_live": "Sent while the signal candle is still open; the marker can still disappear before the candle closes.",
    },
    "pt-BR": {
        "buy": "COMPRA",
        "sell": "VENDA",
        "watch": "OBSERVAR",
        "signal": "SINAL",
        "heading": "Sinal do indicador",
        "indicator": "Indicador",
        "market": "Mercado",
        "symbol": "Ativo",
        "timeframe": "Tempo gráfico",
        "price": "Preço",
        "signal_candle": "Vela do sinal",
        "notify_candle": "Enviado na vela",
        "marker": "Marcador no gráfico",
        "rule": "Confirmado no fechamento da vela do sinal e enviado na vela seguinte, por isso não muda depois.",
        "rule_live": "Enviado com a vela do sinal ainda aberta; o marcador ainda pode sumir antes de a vela fechar.",
    },
    "zh-CN": {
        "buy": "买入",
        "sell": "卖出",
        "watch": "观察",
        "signal": "信号",
        "heading": "指标信号",
        "indicator": "指标",
        "market": "市场",
        "symbol": "标的",
        "timeframe": "周期",
        "price": "价格",
        "signal_candle": "信号K线",
        "notify_candle": "通知K线",
        "marker": "图表标记",
        "rule": "信号在K线收盘后确认，并在下一根K线发送，不会重绘。",
        "rule_live": "信号K线尚未收盘时发送，收盘前该标记仍可能消失。",
    },
}


# Fallback time zone for alerts saved without the browser's: UI languages whose country
# keeps one zone (Brazil: the zone most of its population uses).
SIGNAL_TIMEZONES: Dict[str, str] = {
    "pt": "America/Sao_Paulo",
    "zh": "Asia/Shanghai",
    "zh-tw": "Asia/Taipei",
    "ja": "Asia/Tokyo",
    "ko": "Asia/Seoul",
    "vi": "Asia/Ho_Chi_Minh",
    "th": "Asia/Bangkok",
}


def indicator_signal_timezone(language: str = "") -> str:
    """Return the usual time zone for the alert's UI language, or '' when it spans several."""
    lang = str(language or "").lower()
    return SIGNAL_TIMEZONES.get(lang) or SIGNAL_TIMEZONES.get(lang.split("-")[0], "")


def indicator_signal_texts(language: str = "") -> Dict[str, str]:
    """Return notification text for the alert's saved UI language, falling back to English."""
    lang = str(language or "").lower()
    if lang.startswith("zh"):
        return SIGNAL_TEXTS["zh-CN"]
    if lang.startswith("pt"):
        return SIGNAL_TEXTS["pt-BR"]
    return SIGNAL_TEXTS["en-US"]
