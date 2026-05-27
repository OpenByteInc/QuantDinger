"""Gradio UI for Taiwan market read-only reports.

2026/05/27 Steve Peng：新增台股報告圖形化介面。
修改原因：使用者需要可雙擊啟動、能直接以 UI 顯示報告與下載 JSON/CSV 的工具。
修改前代碼：只有 CLI 與 API，可讀性較低，無圖形化顯示。
修改後功能：提供繁體中文 Gradio UI，顯示開盤前/收盤後報告、候選股表格、個股明細、風險與回測摘要。

安全邊界：
本 UI 只呼叫 TaiwanMarketService 的資訊分析方法；不登入、不呼叫 broker、order、quick trade、paper/live trading。
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pandas as pd

from app.services.taiwan_market import (
    OFFICIAL_SOURCE_NOTES,
    TAIWAN_MARKET_DISCLAIMER,
    TaiwanMarketService,
    create_taiwan_market_provider,
)

try:
    import gradio as gr
except Exception:  # pragma: no cover - tests import helper functions without requiring Gradio.
    gr = None  # type: ignore[assignment]


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
REPORT_DIR = PROJECT_ROOT / "reports" / "taiwan-market"


def _parse_date(value: str | None):
    """功能：解析 UI 日期輸入；空白時由 service 使用 Asia/Taipei 今日。"""
    text = (value or "").strip()
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


def _provider_key(label: str) -> str:
    """功能：將 UI 中文資料來源選項轉為 provider key。"""
    # 2026/05/27 Steve Peng：修正原因：Gradio Dropdown 若以中文長字串作為實際 value，
    # 第一次透過 Windows/HTTP callback 時可能因編碼轉換失敗而被判定不在 choices 內。
    # 修改前代碼：直接比對「auto：官方優先，失敗時使用示範資料」等中文選項字串。
    # 修改後功能：優先接受穩定 ASCII key，同時保留舊中文 label 的相容判斷。
    if (label or "").strip().lower() in {"auto", "official", "mock"}:
        return (label or "").strip().lower()
    if "official" in (label or "").lower() or "官方" in (label or ""):
        return "official"
    if "mock" in (label or "").lower() or "示範" in (label or ""):
        return "mock"
    return "auto"


def candidate_rows(candidates: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    """功能：把候選股 JSON 轉成 GUI 可讀表格。

    使用說明：只保留資訊欄位，不加入下單、買賣或券商相關欄位。
    """
    rows: List[Dict[str, Any]] = []
    for item in candidates or []:
        liquidity = item.get("liquidity") or {}
        rows.append(
            {
                "代號": item.get("code", ""),
                "名稱": item.get("name", ""),
                "市場": item.get("market", ""),
                "產業": item.get("industry", ""),
                "強勢分數": item.get("strength_score", 0),
                "信心分數": item.get("confidence_score", 0),
                "風險等級": item.get("risk_level", ""),
                "觀察買入區間": _range_text(item.get("observe_entry_price_range")),
                "停損觀察價": item.get("stop_loss_observe_price", ""),
                "停利觀察區間": _range_text(item.get("take_profit_observe_range")),
                "最大觀察部位%": item.get("max_observe_position_pct", ""),
                "流動性": liquidity.get("level", ""),
                "成交金額": liquidity.get("turnover", ""),
                "量比": liquidity.get("volume_vs_20d", ""),
                "追高適合度": item.get("chasing_suitability", ""),
            }
        )
    return pd.DataFrame(rows)


def detail_markdown(candidates: Sequence[Dict[str, Any]]) -> str:
    """功能：產生候選股逐檔明細 Markdown。"""
    if not candidates:
        return "尚無候選股。請檢查資料來源、日期或是否為休市日。"
    lines = ["## 個股明細"]
    for idx, item in enumerate(candidates, start=1):
        lines.append(f"### {idx}. {item.get('code')} {item.get('name')}｜{item.get('market')}｜{item.get('industry')}")
        lines.append(f"- 強勢分數：{item.get('strength_score')}；信心分數：{item.get('confidence_score')}；風險等級：{item.get('risk_level')}")
        lines.append(f"- 觀察買入區間：{_range_text(item.get('observe_entry_price_range'))}")
        lines.append(f"- 停損觀察價：{item.get('stop_loss_observe_price')}；停利觀察區間：{_range_text(item.get('take_profit_observe_range'))}")
        lines.append(f"- 最大觀察部位比例：{item.get('max_observe_position_pct')}%")
        lines.append("- 主要理由：" + "；".join(map(str, item.get("primary_reasons") or [])))
        lines.append("- 主要風險：" + "；".join(map(str, item.get("primary_risks") or [])))
        lines.append("- 事件風險：" + "；".join(map(str, item.get("event_risk") or [])))
    return "\n".join(lines)


def risk_markdown(report: Dict[str, Any]) -> str:
    """功能：產生風險提示 Markdown。"""
    risk = report.get("risk_reference") or {}
    lines = [
        "## 風險提示",
        f"> {report.get('disclaimer') or TAIWAN_MARKET_DISCLAIMER}",
        "",
        f"- 停損：{risk.get('stop_loss', '')}",
        f"- 停利：{risk.get('take_profit', '')}",
        f"- 追高風險：{risk.get('chasing_risk', '')}",
        f"- 跳空風險：{risk.get('gap_risk', '')}",
        f"- 流動性風險：{risk.get('liquidity_risk', '')}",
        f"- 事件風險：{risk.get('event_risk', '')}",
    ]
    cost = risk.get("cost_assumption") or {}
    if cost:
        lines.extend(
            [
                "",
                "### 成本與滑價假設",
                f"- 手續費：{cost.get('commission_each_side', '')}",
                f"- 證交稅：{cost.get('securities_transaction_tax', '')}",
                f"- 滑價：{cost.get('slippage', '')}",
            ]
        )
    return "\n".join(lines)


def summary_markdown(report: Dict[str, Any]) -> str:
    """功能：產生報告摘要 Markdown。"""
    status = report.get("data_source_status") or {}
    basis = report.get("direction_basis") or []
    lines = [
        f"# 台股資訊分析報告｜{report.get('report_date', '')}",
        f"> {report.get('disclaimer') or TAIWAN_MARKET_DISCLAIMER}",
        "",
        f"- 報告類型：{_session_label(report.get('session'))}",
        f"- 資料來源：{report.get('provider', '')}",
        f"- 今日大盤方向：{report.get('today_market_direction', report.get('message', ''))}",
        f"- ETF 納入：{'是' if report.get('include_etf') else '否'}",
    ]
    if status:
        lines.append(f"- 資料狀態：{status.get('provider')}；fallback：{status.get('fallback_used')}；{status.get('message')}")
    if basis:
        lines.append("")
        lines.append("## 依據")
        lines.extend([f"- {item}" for item in basis])
    if report.get("today_review"):
        lines.append("")
        lines.append("## 今日回顧")
        lines.append(str((report.get("today_review") or {}).get("summary", "")))
    if report.get("tomorrow_prediction"):
        prediction = report.get("tomorrow_prediction") or {}
        lines.append("")
        lines.append("## 明日觀察")
        lines.append(f"- 方向：{prediction.get('direction', '')}")
        lines.extend([f"- {item}" for item in prediction.get("key_watch", [])])
    lines.append("")
    lines.append(str(report.get("manual_only_notice", "本報告僅供資訊分析與風險提示。")))
    return "\n".join(lines)


def build_report_outputs(report: Dict[str, Any], report_kind: str):
    """功能：將報告轉成 Gradio 多輸出格式。

    使用說明：回傳摘要 Markdown、候選股 DataFrame、明細 Markdown、風險 Markdown、JSON 路徑與 CSV 路徑。
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidates = report.get("top_candidates") or []
    table = candidate_rows(candidates)
    json_path = REPORT_DIR / f"taiwan_{report_kind}_{stamp}.json"
    csv_path = REPORT_DIR / f"taiwan_{report_kind}_{stamp}.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + os.linesep, encoding="utf-8")
    table.to_csv(csv_path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    return (
        summary_markdown(report),
        table,
        detail_markdown(candidates),
        risk_markdown(report),
        str(json_path),
        str(csv_path),
    )


def build_backtest_outputs(backtest: Dict[str, Any]):
    """功能：將回測摘要轉成 Gradio 輸出格式。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    metrics = backtest.get("metrics") or {}
    rows = [{"指標": key, "數值": value} for key, value in metrics.items()]
    table = pd.DataFrame(rows)
    json_path = REPORT_DIR / f"taiwan_backtest_{stamp}.json"
    csv_path = REPORT_DIR / f"taiwan_backtest_{stamp}.csv"
    json_path.write_text(json.dumps(backtest, ensure_ascii=False, indent=2) + os.linesep, encoding="utf-8")
    table.to_csv(csv_path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    summary = "\n".join(
        [
            "# 台股候選股資訊型回測摘要",
            f"> {backtest.get('disclaimer') or TAIWAN_MARKET_DISCLAIMER}",
            "",
            f"- 方法：{backtest.get('method', '')}",
            f"- 信心：{metrics.get('confidence', '')}",
            f"- 勝率：{metrics.get('win_rate', '')}",
            f"- 平均日報酬：{metrics.get('average_daily_return', '')}",
            f"- 最大回撤：{metrics.get('max_drawdown', '')}",
            f"- Sharpe-like：{metrics.get('sharpe_like', '')}",
            "",
            "此回測僅作資訊型歷史模擬，不代表交易建議或委託指令。",
        ]
    )
    return summary, table, str(json_path), str(csv_path)


def stock_analysis_markdown(analysis: Dict[str, Any]) -> str:
    """功能：將指定個股分析 JSON 轉成 GUI 可讀 Markdown。"""
    # 2026/05/27 Steve Peng：新增原因：GUI 需要直接顯示指定股票現況、風險與觀察建議。
    # 修改前代碼：UI 只有整體報告、排行榜與回測摘要，無單檔分析顯示格式。
    # 修改後功能：把單檔分析轉為繁體中文 Markdown，且明確標示非投資建議。
    lines = [
        "# 指定個股分析",
        f"> {analysis.get('disclaimer') or TAIWAN_MARKET_DISCLAIMER}",
        "",
    ]
    if analysis.get("status") != "found":
        lines.append(f"## 查詢結果：{analysis.get('message', '找不到符合條件的股票。')}")
        suggestions = analysis.get("suggestions") or []
        if suggestions:
            lines.append("")
            lines.append("### 可能的相近標的")
            for item in suggestions:
                lines.append(f"- {item.get('code')} {item.get('name')}｜{item.get('market')}｜{item.get('industry')}")
        return "\n".join(lines)

    stock = analysis.get("stock") or {}
    snapshot = analysis.get("current_snapshot") or {}
    quantitative = analysis.get("quantitative_analysis") or {}
    observation = analysis.get("observation_reference") or {}
    moving_average = snapshot.get("moving_average") or {}
    universe_filter = analysis.get("universe_filter") or {}
    lines.extend(
        [
            f"## {stock.get('code')} {stock.get('name')}｜{stock.get('market')}｜{stock.get('industry')}",
            f"- 報告日期：{analysis.get('report_date')}（{analysis.get('timezone')}）",
            f"- 資料來源：{analysis.get('provider')}",
            f"- 收盤價：{snapshot.get('close')}，日漲跌幅：{snapshot.get('day_change_pct')}%",
            f"- 日高/日低：{snapshot.get('day_high')} / {snapshot.get('day_low')}",
            f"- 成交量：{snapshot.get('volume')}，成交金額：{snapshot.get('turnover')}",
            f"- 量能相對 20 日均量：{snapshot.get('volume_vs_20d')} 倍",
            f"- MA5/MA20/MA60：{moving_average.get('ma5')} / {moving_average.get('ma20')} / {moving_average.get('ma60')}",
            "",
            "## 量化現況",
            f"- 強勢分數：{quantitative.get('strength_score')}",
            f"- 信心分數：{quantitative.get('confidence_score')}",
            f"- 風險等級：{quantitative.get('risk_level')}",
            f"- 目前排行榜名次：{quantitative.get('rank_in_current_universe') or '未納入排行'}",
            f"- 資料品質：{quantitative.get('data_quality')}",
            f"- 是否納入強勢排行股票池：{'是' if universe_filter.get('eligible_for_strength_ranking') else '否'}",
        ]
    )
    exclusions = universe_filter.get("exclusion_reasons") or []
    if exclusions:
        lines.append("- 排除原因：" + "；".join(map(str, exclusions)))
    lines.extend(
        [
            "",
            "## 觀察參考",
            f"- 觀察買入價位區間：{_range_text(observation.get('observe_entry_price_range'))}",
            f"- 停損觀察價位：{observation.get('stop_loss_observe_price')}",
            f"- 停利/賣出觀察區間：{_range_text(observation.get('take_profit_observe_range'))}",
            f"- 最大觀察部位比例：{observation.get('max_observe_position_pct')}%",
            f"- 是否適合追高：{observation.get('chasing_suitability')}",
            f"- 觀察建議：{observation.get('suggested_observation')}",
            f"- 說明：{observation.get('guidance_note')}",
            "",
            "## 主要理由",
        ]
    )
    lines.extend([f"- {item}" for item in analysis.get("primary_reasons") or []])
    lines.append("")
    lines.append("## 主要風險")
    lines.extend([f"- {item}" for item in analysis.get("primary_risks") or []])
    lines.append("")
    lines.append("## 事件風險")
    lines.extend([f"- {item}" for item in analysis.get("event_risk") or []])
    lines.append("")
    lines.append("## 下一步觀察項目")
    lines.extend([f"- {item}" for item in analysis.get("next_watch_items") or []])
    return "\n".join(lines)


def build_stock_analysis_outputs(analysis: Dict[str, Any]):
    """功能：將指定個股分析轉成 Markdown 與 JSON 下載檔。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    query = str(analysis.get("query") or "stock").strip() or "stock"
    safe_query = "".join(ch for ch in query if ch.isalnum() or ch in ("-", "_"))[:32] or "stock"
    json_path = REPORT_DIR / f"taiwan_stock_analysis_{safe_query}_{stamp}.json"
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + os.linesep, encoding="utf-8")
    return stock_analysis_markdown(analysis), str(json_path)


def generate_report_ui(report_type: str, provider_label: str, report_date: str, top_n: int, include_etf: bool):
    """功能：Gradio 按鈕 callback，產生開盤前或收盤後報告。"""
    provider = create_taiwan_market_provider(_provider_key(provider_label))
    session = "post_market" if "收盤" in report_type else "pre_market"
    report = TaiwanMarketService(provider=provider).generate_report(
        session,
        as_of=_parse_date(report_date),
        top_n=int(top_n or 20),
        include_etf=bool(include_etf),
    )
    return build_report_outputs(report, session)


def generate_stock_analysis_ui(provider_label: str, query: str, report_date: str, include_etf: bool):
    """功能：Gradio 按鈕 callback，依股票名稱或代號產生指定個股分析。"""
    # 2026/05/27 Steve Peng：新增原因：使用者需要在圖形化介面直接輸入股票名稱或代號分析單檔股票。
    # 修改前代碼：Gradio UI 無指定個股分析 callback。
    # 修改後功能：呼叫 TaiwanMarketService.analyze_stock 並輸出 Markdown/JSON，保持 read-only。
    provider = create_taiwan_market_provider(_provider_key(provider_label))
    analysis = TaiwanMarketService(provider=provider).analyze_stock(
        query=query,
        as_of=_parse_date(report_date),
        include_etf=bool(include_etf),
    )
    return build_stock_analysis_outputs(analysis)


def generate_backtest_ui(provider_label: str, top_n: int, include_etf: bool, days: int):
    """功能：Gradio 按鈕 callback，產生資訊型回測摘要。"""
    provider = create_taiwan_market_provider(_provider_key(provider_label))
    backtest = TaiwanMarketService(provider=provider).backtest_top_candidates(
        days=int(days or 60),
        top_n=int(top_n or 20),
        include_etf=bool(include_etf),
    )
    return build_backtest_outputs(backtest)


def sources_markdown() -> str:
    """功能：產生資料來源與授權說明。"""
    lines = [
        "# 資料來源與授權說明",
        f"> {TAIWAN_MARKET_DISCLAIMER}",
        "",
        "本介面預設 `auto`：先嘗試 TWSE/TPEx 官方 OpenAPI，失敗或資料不足時回退 mock provider。",
        "啟用真實資料前仍需由部署者確認官方資料授權、頻率限制與再利用條款。",
        "",
    ]
    for item in OFFICIAL_SOURCE_NOTES:
        lines.append(f"## {item.get('name')}")
        lines.append(f"- URL：{item.get('url')}")
        lines.append(f"- 用途：{item.get('usage')}")
        lines.append(f"- 授權注意：{item.get('license_note')}")
    lines.append("")
    lines.append("本 UI 不含任何下單、券商連線、paper trading、live trading 或交易執行功能。")
    return "\n".join(lines)


def create_app():
    """功能：建立 Gradio Blocks 應用。"""
    if gr is None:
        raise RuntimeError("gradio is not installed. Run `pip install -r requirements.txt` first.")

    with gr.Blocks(title="QuantDinger 台股資訊分析") as demo:
        gr.Markdown("# QuantDinger 台股資訊分析與強勢候選股報告")
        gr.Markdown(f"> {TAIWAN_MARKET_DISCLAIMER}。本工具僅供資訊分析與風險提示，不提供任何交易執行。")

        with gr.Row():
            provider = gr.Dropdown(
                # 2026/05/27 Steve Peng：修正原因：中文長字串作為 Dropdown value 會讓首次 callback
                # 在部分 Windows 編碼環境中變成問號，造成 Gradio 回報「不在 choices 內」。
                # 修改前代碼：choices/value 都使用中文完整描述。
                # 修改後功能：UI 顯示繁體中文 label，但實際傳遞穩定 ASCII value。
                choices=[
                    ("auto：官方優先，失敗時使用示範資料", "auto"),
                    ("official：TWSE/TPEx 官方資料", "official"),
                    ("mock：離線示範資料", "mock"),
                ],
                value="auto",
                label="資料來源",
            )
            date_box = gr.Textbox(label="日期（YYYY-MM-DD，可留空使用今日）", placeholder="2026-05-27")
            top_n = gr.Slider(5, 50, value=20, step=1, label="候選股數量")
            include_etf = gr.Checkbox(value=False, label="納入 ETF")

        with gr.Tab("開盤前報告"):
            pre_btn = gr.Button("產生開盤前報告", variant="primary")
            pre_summary = gr.Markdown()
            pre_table = gr.Dataframe(label="強勢候選股排行榜", wrap=True)
            pre_detail = gr.Markdown()
            pre_risk = gr.Markdown()
            with gr.Row():
                pre_json = gr.File(label="下載 JSON")
                pre_csv = gr.File(label="下載 CSV")

        with gr.Tab("收盤後報告"):
            post_btn = gr.Button("產生收盤後報告", variant="primary")
            post_summary = gr.Markdown()
            post_table = gr.Dataframe(label="明日候選股排行榜", wrap=True)
            post_detail = gr.Markdown()
            post_risk = gr.Markdown()
            with gr.Row():
                post_json = gr.File(label="下載 JSON")
                post_csv = gr.File(label="下載 CSV")

        with gr.Tab("強勢候選股排行榜 / 個股明細"):
            gr.Markdown("請先在開盤前或收盤後分頁產生報告；表格與個股明細會在該分頁顯示。")

        with gr.Tab("指定個股分析"):
            # 2026/05/27 Steve Peng：新增原因：使用者需要輸入股票名稱或代號查看單檔股票現況。
            # 修改前代碼：UI 只能看整體報告與排行榜，無法直接查詢單一股票。
            # 修改後功能：新增 read-only 單檔分析分頁，不提供下單或交易執行入口。
            stock_query = gr.Textbox(label="股票名稱或代號", placeholder="例如：2330 或 台積電")
            stock_btn = gr.Button("產生指定個股分析", variant="primary")
            stock_analysis = gr.Markdown()
            stock_json = gr.File(label="下載 JSON")

        with gr.Tab("回測摘要"):
            days = gr.Slider(5, 180, value=60, step=1, label="回測天數")
            backtest_btn = gr.Button("產生資訊型回測摘要", variant="primary")
            backtest_summary = gr.Markdown()
            backtest_table = gr.Dataframe(label="回測指標", wrap=True)
            with gr.Row():
                backtest_json = gr.File(label="下載 JSON")
                backtest_csv = gr.File(label="下載 CSV")

        with gr.Tab("資料來源/授權說明"):
            gr.Markdown(sources_markdown())

        pre_btn.click(
            fn=lambda p, d, t, e: generate_report_ui("開盤前", p, d, t, e),
            inputs=[provider, date_box, top_n, include_etf],
            outputs=[pre_summary, pre_table, pre_detail, pre_risk, pre_json, pre_csv],
        )
        post_btn.click(
            fn=lambda p, d, t, e: generate_report_ui("收盤後", p, d, t, e),
            inputs=[provider, date_box, top_n, include_etf],
            outputs=[post_summary, post_table, post_detail, post_risk, post_json, post_csv],
        )
        stock_btn.click(
            fn=generate_stock_analysis_ui,
            inputs=[provider, stock_query, date_box, include_etf],
            outputs=[stock_analysis, stock_json],
        )
        backtest_btn.click(
            fn=generate_backtest_ui,
            inputs=[provider, top_n, include_etf, days],
            outputs=[backtest_summary, backtest_table, backtest_json, backtest_csv],
        )
    return demo


def launch() -> None:
    """功能：啟動本機 Gradio UI。"""
    # 2026/05/27 Steve Peng：修正原因：Gradio 6 預設禁止 File 元件回傳工作目錄外的檔案，
    # 但本模組依規劃將 JSON/CSV 報告輸出到專案根目錄 reports/taiwan-market。
    # 修改前代碼：未設定 allowed_paths，首次產生報告時 File 下載元件會觸發 InvalidPathError。
    # 修改後功能：明確允許 read-only 報告輸出資料夾，並開啟 show_error 方便本機除錯。
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    create_app().launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=True,
        allowed_paths=[str(REPORT_DIR)],
        show_error=True,
    )


def _range_text(value: Any) -> str:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return f"{value[0]} - {value[1]}"
    return str(value or "")


def _session_label(value: Any) -> str:
    return "收盤後報告" if value == "post_market" else "開盤前報告"


if __name__ == "__main__":
    launch()
