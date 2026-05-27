"""Taiwan market Gradio UI helper tests.

2026/05/27 Steve Peng：新增台股 GUI helper 測試。
修改原因：圖形化介面需能把報告直接顯示為中文摘要、表格與下載檔，而不是只輸出 JSON。
修改前代碼：尚無 Gradio UI helper。
修改後功能：驗證報告摘要、排行榜表格、個股明細與 JSON/CSV 下載檔產生。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from app.services.taiwan_market import MockTaiwanMarketProvider, TaiwanMarketService
from app.ui.taiwan_market_app import (
    build_report_outputs,
    candidate_rows,
    detail_markdown,
    generate_report_ui,
)


def test_ui_helpers_convert_report_to_tables_and_download_files(tmp_path, monkeypatch):
    """功能：GUI helper 將報告轉為摘要、候選股表格、明細與下載檔。"""
    monkeypatch.setattr("app.ui.taiwan_market_app.REPORT_DIR", tmp_path)
    report = TaiwanMarketService(provider=MockTaiwanMarketProvider()).generate_report(
        "pre_market",
        as_of=date(2026, 5, 27),
        top_n=20,
    )

    summary, table, detail, risk, json_path, csv_path = build_report_outputs(report, "pre_market")

    assert "非投資建議，請自行評估風險" in summary
    assert "今日大盤方向" in summary
    assert len(table) == 20
    assert "代號" in table.columns
    assert "停損觀察價" in table.columns
    assert "主要風險" in detail
    assert "停損" in risk
    assert Path(json_path).exists()
    assert Path(csv_path).exists()


def test_detail_markdown_handles_empty_candidates():
    """功能：沒有候選股時，個股明細顯示清楚的中文空狀態。"""
    assert "尚無候選股" in detail_markdown([])


def test_candidate_rows_keep_read_only_columns():
    """功能：候選股表格只保留資訊欄位，不出現下單或交易欄位。"""
    report = TaiwanMarketService(provider=MockTaiwanMarketProvider()).generate_report(
        "pre_market",
        as_of=date(2026, 5, 27),
        top_n=3,
    )
    table = candidate_rows(report["top_candidates"])

    forbidden = {"下單", "買進", "賣出", "broker", "order", "live_trading", "paper_trading"}
    assert forbidden.isdisjoint(set(map(str, table.columns)))
    assert len(table) == 3


def test_generate_report_ui_accepts_ascii_provider_key(tmp_path, monkeypatch):
    """功能：確認 GUI callback 可接受穩定 ASCII provider key。

    2026/05/27 Steve Peng：修正原因：第一次使用 UI 時，中文 Dropdown value 可能被 Windows/HTTP
    編碼轉成問號，造成 Gradio 判定 value 不在 choices 內並顯示錯誤。
    修改前代碼：測試只覆蓋中文 provider label，未覆蓋 UI 實際應傳遞的穩定 key。
    修改後功能：明確驗證 callback 使用 `mock` 這類 ASCII key 也能產生完整輸出。
    """
    monkeypatch.setattr("app.ui.taiwan_market_app.REPORT_DIR", tmp_path)

    summary, table, detail, risk, json_path, csv_path = generate_report_ui(
        "開盤前",
        "mock",
        "2026-05-27",
        5,
        False,
    )

    assert "非投資建議" in summary
    assert len(table) == 5
    assert "個股明細" in detail
    assert "風險" in risk
    assert Path(json_path).exists()
    assert Path(csv_path).exists()
