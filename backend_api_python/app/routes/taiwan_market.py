"""Taiwan market read-only report APIs.

2026/05/27 Steve Peng：新增台股資訊分析 API。
修改原因：提供手動觸發台股開盤前/收盤後報告、候選股排行、回測摘要與資料來源說明。
修改前代碼：後端沒有 `/api/taiwan-market/*` 資訊型端點。
修改後功能：新增 read-only API；不連接 broker、不送出委託、不建立 paper/live trading。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from flask import Blueprint, jsonify, request

from app.services.taiwan_market import (
    OFFICIAL_SOURCE_NOTES,
    TaiwanMarketProviderError,
    TaiwanMarketService,
    create_taiwan_market_provider,
)
from app.utils.auth import login_required
from app.utils.logger import get_logger

logger = get_logger(__name__)

taiwan_market_bp = Blueprint("taiwan_market", __name__)


def _parse_date(raw: Optional[str]):
    """功能：解析 `YYYY-MM-DD` 報告日期；空值表示使用 Asia/Taipei 今日。"""
    value = (raw or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _bool_arg(value: Optional[str], default: bool = False) -> bool:
    """功能：解析 query/body 的布林選項。"""
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _service_from_request() -> TaiwanMarketService:
    """功能：依請求建立台股分析服務；預設使用 mock provider。"""
    provider_name = (
        request.args.get("provider")
        or ((request.get_json(silent=True) or {}) if request.is_json else {}).get("provider")
        or "mock"
    )
    return TaiwanMarketService(provider=create_taiwan_market_provider(str(provider_name)))


@taiwan_market_bp.route("/report", methods=["GET", "POST"])
@login_required
def generate_taiwan_market_report():
    """功能：手動觸發台股開盤前或收盤後資訊報告。"""
    try:
        body = request.get_json(silent=True) if request.method == "POST" else {}
        body = body if isinstance(body, dict) else {}
        session = request.args.get("session") or body.get("session") or "pre_market"
        top_n = int(request.args.get("top") or body.get("top") or 20)
        include_etf = _bool_arg(request.args.get("include_etf") or body.get("include_etf"), False)
        as_of = _parse_date(request.args.get("date") or body.get("date"))
        data = _service_from_request().generate_report(
            session=session,
            as_of=as_of,
            top_n=top_n,
            include_etf=include_etf,
        )
        return jsonify({"code": 1, "msg": "success", "data": data})
    except TaiwanMarketProviderError as exc:
        logger.info("Taiwan market provider unavailable: %s", exc)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 502
    except Exception as exc:
        logger.error("generate_taiwan_market_report failed: %s", exc, exc_info=True)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400


@taiwan_market_bp.route("/candidates", methods=["GET"])
@login_required
def list_taiwan_market_candidates():
    """功能：回傳台股強勢候選股排行榜，供前端資訊頁或手動查詢使用。"""
    try:
        top_n = int(request.args.get("top") or 20)
        include_etf = _bool_arg(request.args.get("include_etf"), False)
        as_of = _parse_date(request.args.get("date"))
        data = _service_from_request().rank_candidates(top_n=top_n, include_etf=include_etf, as_of=as_of)
        for row in data:
            row.pop("_historical_returns", None)
        return jsonify({"code": 1, "msg": "success", "data": data})
    except TaiwanMarketProviderError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 502
    except Exception as exc:
        logger.error("list_taiwan_market_candidates failed: %s", exc, exc_info=True)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400


@taiwan_market_bp.route("/backtest", methods=["GET"])
@login_required
def backtest_taiwan_market_candidates():
    """功能：回傳每日前 20 候選股的資訊型回測摘要。"""
    try:
        days = int(request.args.get("days") or 60)
        top_n = int(request.args.get("top") or 20)
        include_etf = _bool_arg(request.args.get("include_etf"), False)
        data = _service_from_request().backtest_top_candidates(days=days, top_n=top_n, include_etf=include_etf)
        return jsonify({"code": 1, "msg": "success", "data": data})
    except TaiwanMarketProviderError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 502
    except Exception as exc:
        logger.error("backtest_taiwan_market_candidates failed: %s", exc, exc_info=True)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400


@taiwan_market_bp.route("/sources", methods=["GET"])
@login_required
def taiwan_market_sources():
    """功能：回傳台股模組資料來源與授權注意事項。"""
    return jsonify({
        "code": 1,
        "msg": "success",
        "data": {
            "default_provider": "mock",
            "official_sources": OFFICIAL_SOURCE_NOTES,
            "notice": "真實 provider 啟用前需確認資料授權、頻率限制與欄位可用性；不得加入下單或券商連線功能。",
        },
    })


@taiwan_market_bp.route("/schedule", methods=["GET"])
@login_required
def taiwan_market_schedule():
    """功能：回傳 Asia/Taipei 開盤前/收盤後報告排程建議。"""
    return jsonify({"code": 1, "msg": "success", "data": TaiwanMarketService().schedule_reference()})
