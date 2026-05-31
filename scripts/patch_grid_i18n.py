#!/usr/bin/env python3
"""Patch grid resting i18n keys into locale JS files."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "QuantDinger-Vue-src" / "src" / "locales" / "lang"

# Keys to upsert: key -> value per locale file stem
LOCALE_PATCHES: dict[str, dict[str, str]] = {
    "zh-TW": {
        "trading-bot.tab.gridRestingOrders": "即時掛單",
        "trading-bot.wizard.gridTickModeHint": "Live 網格在交易所預掛限價單，成交後自動掛配對單；後台輪詢成交，不依賴 K 線週期",
        "trading-bot.detail.gridNote": "Live 網格：系統在格線預掛限價單，成交後自動掛配對止盈/開倉單；後台約每 3 秒輪詢成交。下方預覽為預估掛單分佈。",
        "trading-bot.detail.gridNoteLong": "做多網格：啟動時可按比例市價買入底倉；當前價下方掛買入，上方掛賣出平多。",
        "trading-bot.detail.gridNoteShort": "做空網格：啟動時可按比例市價開空；當前價上方掛開空，下方掛買入平空。",
        "trading-bot.detail.gridNoteNeutral": "中性網格：當前價下方掛多、上方掛空；適合橫盤震盪（合約）。",
        "trading-bot.detail.triggerDrop": "買入限價 @ 格線下沿",
        "trading-bot.detail.triggerRise": "賣出限價 @ 格線上沿",
        "trading-bot.grid.direction": "網格方向",
        "trading-bot.grid.neutral": "中性網格",
        "trading-bot.grid.long": "做多網格",
        "trading-bot.grid.short": "做空網格",
        "trading-bot.grid.neutralHint": "低買高賣，雙向獲利，適合震盪行情",
        "trading-bot.grid.longHint": "啟動時可選按比例建底倉；下方掛買入限價，上方掛賣出止盈，適合震盪偏多",
        "trading-bot.grid.shortHint": "啟動時可選按比例建空倉；上方掛開空限價，下方掛平空止盈，適合震盪偏空",
        "trading-bot.grid.initialPositionPct": "初始倉位比例",
        "trading-bot.grid.initialPositionPctHint": "Live 實盤啟動時先市價建倉的比例，0 表示不先建倉、只等格線成交",
        "trading-bot.grid.initialPositionPctInvalid": "初始倉位比例需在 0–100 之間",
        "trading-bot.grid.initialPositionTooSmall": "初始倉位金額過小，建議不低於半格投資額",
        "trading-bot.grid.initialPositionUsdtHint": "約 {usdt} USDT（{pct}% 初始資金）將在啟動時市價建倉",
        "trading-bot.grid.boundaryAction": "出界處理",
        "trading-bot.grid.boundaryPause": "暫停新開倉（保留已有止盈單）",
        "trading-bot.grid.boundaryStopLoss": "停止並平倉",
        "trading-bot.grid.boundaryHold": "僅告警不操作",
        "trading-bot.grid.boundaryActionHint": "價格跑出網格上下界時的處理方式（與「網格越界緩衝」配合使用）",
        "trading-bot.grid.limitOrderHintResting": "推薦：在格線預掛限價單，成交後自動掛配對單；手續費更低",
        "trading-bot.grid.advancedTitle": "進階選項",
        "trading-bot.grid.executionMode": "Live 執行模式",
        "trading-bot.grid.executionResting": "預掛限價（推薦）",
        "trading-bot.grid.executionSignal": "穿線信號（舊版腳本）",
        "trading-bot.grid.executionModeHint": "預掛模式在交易所掛限價單並輪詢成交；穿線模式使用舊版價格穿越腳本，僅供回測或相容。",
        "trading-bot.detail.restingOrdersEmpty": "暫無未成交的預掛限價單",
        "trading-bot.detail.restingOrdersStopped": "啟動機器人後可查看交易所預掛限價單",
        "trading-bot.detail.restingOrdersSignalMode": "目前為穿線信號模式，不提供預掛單追蹤",
        "trading-bot.detail.restingOrderCell": "格位",
        "trading-bot.detail.restingOrderPurpose": "用途",
        "trading-bot.detail.restingOrderSide": "方向",
        "trading-bot.detail.restingOrderPrice": "價格",
        "trading-bot.detail.restingOrderQty": "數量",
        "trading-bot.detail.restingOrderFilled": "已成交",
        "trading-bot.detail.restingOrderStatus": "狀態",
        "trading-bot.detail.restingOrderExchangeId": "交易所單號",
        "trading-bot.detail.restingPurpose.long_entry": "買入開多",
        "trading-bot.detail.restingPurpose.long_exit": "賣出平多",
        "trading-bot.detail.restingPurpose.short_entry": "賣出開空",
        "trading-bot.detail.restingPurpose.short_exit": "買入平空",
        "trading-bot.detail.restingStatus.pending": "待提交",
        "trading-bot.detail.restingStatus.open": "掛單中",
        "trading-bot.detail.restingStatus.partial": "部分成交",
        "trading-bot.detail.restingStatus.filled": "已成交",
        "trading-bot.detail.restingStatus.cancelled": "已取消",
    },
    "ja-JP": {
        "trading-bot.tab.gridRestingOrders": "リアルタイム注文",
        "trading-bot.wizard.gridTickModeHint": "Liveグリッドは取引所に指値を事前配置し、約定後にペア注文を自動配置。約定はポーリングで検知",
        "trading-bot.grid.longHint": "開始時に任意で底値を建玉；下に買い指値、上に売り決済。緩やかな上昇レンジ向け",
        "trading-bot.grid.shortHint": "開始時に任意で空売り建玉；上に売り指値、下に買い決済。緩やかな下落レンジ向け",
        "trading-bot.grid.initialPositionPct": "初期ポジション比率",
        "trading-bot.grid.initialPositionPctHint": "Live開始時に成行で建てる資金比率。0=グリッド約定のみ待機",
        "trading-bot.grid.initialPositionPctInvalid": "初期ポジション比率は0〜100の間",
        "trading-bot.grid.initialPositionTooSmall": "初期ポジションが小さすぎます（半グリッド以上推奨）",
        "trading-bot.grid.initialPositionUsdtHint": "約 {usdt} USDT（{pct}%）を開始時に成行建玉",
        "trading-bot.grid.boundaryAction": "レンジ外の処理",
        "trading-bot.grid.boundaryPause": "新規停止（決済注文は維持）",
        "trading-bot.grid.boundaryStopLoss": "停止して全決済",
        "trading-bot.grid.boundaryHold": "通知のみ",
        "trading-bot.grid.boundaryActionHint": "価格がグリッド範囲外に出たときの動作",
        "trading-bot.grid.limitOrderHintResting": "推奨：グリッドに指値を配置、約定後にペア注文 — 手数料低",
        "trading-bot.grid.advancedTitle": "詳細設定",
        "trading-bot.grid.executionMode": "Live実行モード",
        "trading-bot.grid.executionResting": "指値事前配置（推奨）",
        "trading-bot.grid.executionSignal": "シグナル/クロス（旧版）",
        "trading-bot.grid.executionModeHint": "指値モードは取引所に指値を置き約定をポーリング。シグナルモードは旧クロススクリプト用",
        "trading-bot.detail.gridNote": "Liveグリッド：グリッドに指値を配置し、約定後にペア注文を自動配置。約3秒ごとにポーリング。",
        "trading-bot.detail.gridNoteLong": "ロング：開始時に任意で成行買い；現在価格の下に買い、上に売り決済。",
        "trading-bot.detail.gridNoteShort": "ショート：開始時に任意で成行売り；上に売り、下に買い決済。",
        "trading-bot.detail.gridNoteNeutral": "ニュートラル：基準線の下はロング、上はショート。",
        "trading-bot.detail.triggerDrop": "買い指値 @ セル下限",
        "trading-bot.detail.triggerRise": "売り指値 @ セル上限",
        "trading-bot.detail.restingOrdersEmpty": "未約定の指値注文はありません",
        "trading-bot.detail.restingOrdersStopped": "ボット起動後にリアルタイム注文を表示",
        "trading-bot.detail.restingOrdersSignalMode": "シグナルモードのため指値追跡は利用不可",
        "trading-bot.detail.restingOrderCell": "セル",
        "trading-bot.detail.restingOrderPurpose": "用途",
        "trading-bot.detail.restingOrderSide": "方向",
        "trading-bot.detail.restingOrderPrice": "価格",
        "trading-bot.detail.restingOrderQty": "数量",
        "trading-bot.detail.restingOrderFilled": "約定",
        "trading-bot.detail.restingOrderStatus": "状態",
        "trading-bot.detail.restingOrderExchangeId": "取引所ID",
        "trading-bot.detail.restingPurpose.long_entry": "ロングエントリー",
        "trading-bot.detail.restingPurpose.long_exit": "ロング利確",
        "trading-bot.detail.restingPurpose.short_entry": "ショートエントリー",
        "trading-bot.detail.restingPurpose.short_exit": "ショート決済",
        "trading-bot.detail.restingStatus.pending": "待機",
        "trading-bot.detail.restingStatus.open": "注文中",
        "trading-bot.detail.restingStatus.partial": "部分約定",
        "trading-bot.detail.restingStatus.filled": "約定済",
        "trading-bot.detail.restingStatus.cancelled": "取消",
    },
    "ko-KR": {
        "trading-bot.tab.gridRestingOrders": "실시간 주문",
        "trading-bot.wizard.gridTickModeHint": "Live 그리드는 거래소에 지정가를 미리 배치하고 체결 후 페어 주문을 자동 배치합니다",
        "trading-bot.grid.longHint": "시작 시 선택적 초기 롱; 아래 매수 지정가, 위 매도 청산",
        "trading-bot.grid.shortHint": "시작 시 선택적 초기 숏; 위 매도 지정가, 아래 매수 청산",
        "trading-bot.grid.initialPositionPct": "초기 포지션 비율",
        "trading-bot.grid.initialPositionPctHint": "Live 시작 시 시장가로 진입할 자본 비율. 0=그리드 체결만 대기",
        "trading-bot.grid.initialPositionPctInvalid": "초기 포지션 비율은 0~100 사이",
        "trading-bot.grid.initialPositionTooSmall": "초기 포지션 금액이 너무 작습니다",
        "trading-bot.grid.initialPositionUsdtHint": "약 {usdt} USDT ({pct}%)가 시작 시 시장가 진입",
        "trading-bot.grid.boundaryAction": "범위 이탈 처리",
        "trading-bot.grid.boundaryPause": "신규 진입 중지",
        "trading-bot.grid.boundaryStopLoss": "중지 및 전량 청산",
        "trading-bot.grid.boundaryHold": "알림만",
        "trading-bot.grid.boundaryActionHint": "가격이 그리드 범위를 벗어날 때 동작",
        "trading-bot.grid.limitOrderHintResting": "권장: 그리드 지정가 + 체결 후 페어 주문",
        "trading-bot.grid.advancedTitle": "고급",
        "trading-bot.grid.executionMode": "Live 실행 모드",
        "trading-bot.grid.executionResting": "지정가 대기 (권장)",
        "trading-bot.grid.executionSignal": "시그널/크로스 (레거시)",
        "trading-bot.grid.executionModeHint": "대기 모드는 거래소 지정가 + 체결 폴링. 시그널 모드는 구버전 스크립트",
        "trading-bot.detail.gridNote": "Live 그리드: 그리드에 지정가 배치, 체결 후 페어 주문 자동 배치. 약 3초 폴링.",
        "trading-bot.detail.gridNoteLong": "롱 그리드: 시작 시 선택적 시장가 매수; 아래 매수, 위 매도.",
        "trading-bot.detail.gridNoteShort": "숏 그리드: 시작 시 선택적 시장가 숏; 위 매도, 아래 매수.",
        "trading-bot.detail.gridNoteNeutral": "중립: 기준선 아래 롱, 위 숏.",
        "trading-bot.detail.triggerDrop": "매수 지정가 @ 셀 하단",
        "trading-bot.detail.triggerRise": "매도 지정가 @ 셀 상단",
        "trading-bot.detail.restingOrdersEmpty": "미체결 지정가 없음",
        "trading-bot.detail.restingOrdersStopped": "봇 시작 후 실시간 주문 표시",
        "trading-bot.detail.restingOrdersSignalMode": "시그널 모드 — 대기 주문 추적 불가",
        "trading-bot.detail.restingOrderCell": "셀",
        "trading-bot.detail.restingOrderPurpose": "용도",
        "trading-bot.detail.restingOrderSide": "방향",
        "trading-bot.detail.restingOrderPrice": "가격",
        "trading-bot.detail.restingOrderQty": "수량",
        "trading-bot.detail.restingOrderFilled": "체결",
        "trading-bot.detail.restingOrderStatus": "상태",
        "trading-bot.detail.restingOrderExchangeId": "거래소 ID",
        "trading-bot.detail.restingPurpose.long_entry": "롱 진입",
        "trading-bot.detail.restingPurpose.long_exit": "롱 익절",
        "trading-bot.detail.restingPurpose.short_entry": "숏 진입",
        "trading-bot.detail.restingPurpose.short_exit": "숏 청산",
        "trading-bot.detail.restingStatus.pending": "대기",
        "trading-bot.detail.restingStatus.open": "주문중",
        "trading-bot.detail.restingStatus.partial": "부분체결",
        "trading-bot.detail.restingStatus.filled": "체결",
        "trading-bot.detail.restingStatus.cancelled": "취소",
    },
}

# English fallback for de/fr/ar/vi/th
EN_FALLBACK = {
    k: v
    for k, v in LOCALE_PATCHES.get("ja-JP", {}).items()
}
# Load from en-US file for complete set
en_us = (ROOT / "en-US.js").read_text(encoding="utf-8")
for m in re.finditer(r"'(trading-bot\.(?:grid|detail|tab|wizard)\.[^']+)': '((?:\\'|[^'])*)'", en_us):
    EN_FALLBACK[m.group(1)] = m.group(2).replace("\\'", "'")

for stem in ("de-DE", "fr-FR", "ar-SA", "vi-VN", "th-TH"):
    LOCALE_PATCHES[stem] = dict(EN_FALLBACK)


def upsert_key(text: str, key: str, value: str) -> str:
    esc = value.replace("\\", "\\\\").replace("'", "\\'")
    line = f"  '{key}': '{esc}',"
    pat = re.compile(rf"^\s*'{re.escape(key)}':.*$", re.M)
    if pat.search(text):
        return pat.sub(line, text, count=1)
    body = text
    export_idx = body.find("\nexport default")
    if export_idx >= 0:
        body = body[:export_idx]
        suffix = text[export_idx:]
    else:
        suffix = ""
    anchor_pat = re.compile(
        r"^(\s*)'trading-bot\.martingale\.initialAmount'",
        re.M,
    )
    m = anchor_pat.search(body)
    if m:
        indent = m.group(1) or "  "
        insert_line = f"{indent}'{key}': '{esc}',"
        return body.replace(m.group(0), insert_line + "\n" + m.group(0), 1) + suffix
    anchor2_pat = re.compile(r"^(\s*)'trading-bot\.grid\.totalInvest'.*$", re.M)
    m2 = anchor2_pat.search(body)
    if m2:
        indent = m2.group(1) or "  "
        insert_line = f"{indent}'{key}': '{esc}',"
        return body.replace(m2.group(0), m2.group(0) + ",\n" + insert_line, 1) + suffix
    raise ValueError(f"cannot find anchor to insert key {key!r}")


def main() -> None:
    for stem, patches in LOCALE_PATCHES.items():
        path = ROOT / f"{stem}.js"
        if not path.exists():
            print(f"skip missing {path}")
            continue
        text = path.read_text(encoding="utf-8")
        for key, val in patches.items():
            text = upsert_key(text, key, val)
        path.write_text(text, encoding="utf-8")
        print(f"patched {path.name} ({len(patches)} keys)")


if __name__ == "__main__":
    main()
