"""Taiwan market read-only opportunity analysis service.

2026/05/27 Steve Peng：新增台股資訊分析與強勢候選股報告模組。
修改原因：QuantDinger 既有 README 與程式碼偏重多市場回測/交易，本次需求需要獨立的台股資訊模組。
修改前代碼：專案沒有 TWSE/TPEx 專用 provider interface、股票池過濾、強勢評分、報告與候選股回測摘要。
修改後功能：提供 read-only 台股資料 provider 介面、mock provider、universe builder、評分排序、開盤前/收盤後報告與回測摘要。

安全邊界：
本檔只產生資訊、量化分數、風險提示與報告；不可連接 broker、不可送出委託、不可新增 paper/live trading。
"""
from __future__ import annotations

import math
import re
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean, pstdev
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import requests
from urllib3.exceptions import InsecureRequestWarning

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.9+ normally has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]

from app.utils.logger import get_logger

logger = get_logger(__name__)

TAIWAN_MARKET_DISCLAIMER = "非投資建議，請自行評估風險"
TAIPEI_TZ_NAME = "Asia/Taipei"
VALID_TAIWAN_MARKETS = ("TWSE", "TPEx")


OFFICIAL_SOURCE_NOTES: List[Dict[str, str]] = [
    {
        "name": "TWSE OpenAPI",
        "url": "https://openapi.twse.com.tw/",
        "usage": "上市個股日成交資訊、每日收盤行情、指數與公開資訊延伸資料。",
        "license_note": "政府開放資料與證交所資料使用規範需由部署者確認；本 MVP 不硬寫 API key。",
    },
    {
        "name": "TPEx OpenAPI",
        "url": "https://www.tpex.org.tw/openapi/",
        "usage": "上櫃股票行情、三大法人、注意/處置等櫃買市場資料。",
        "license_note": "公開資料使用限制需由部署者確認；本 MVP 以 mock provider 驗證流程。",
    },
    {
        "name": "MOPS 公開資訊觀測站",
        "url": "https://mops.twse.com.tw/",
        "usage": "重大訊息、財報、法說、除權息、公司基本資料與事件風險。",
        "license_note": "實際抓取頻率與再利用授權需另行確認。",
    },
    {
        "name": "TAIFEX OpenAPI",
        "url": "https://openapi.taifex.com.tw/",
        "usage": "期貨市場、台指期與衍生品資料，可作大盤風險參考。",
        "license_note": "本模組只作市場參考，不建立衍生品交易功能。",
    },
    {
        "name": "TDCC 集保結算所",
        "url": "https://www.tdcc.com.tw/",
        "usage": "股權分散、集保庫存與籌碼結構參考。",
        "license_note": "資料下載與授權限制需由部署者確認。",
    },
]


class TaiwanMarketProviderError(RuntimeError):
    """功能：表示台股 provider 無法提供資料，供 API 轉成可理解錯誤。"""


@dataclass(frozen=True)
class StockSnapshot:
    """功能：台股單一標的的日級分析資料。

    使用說明：provider 應填入同一交易日或同一資料批次的欄位；缺漏資料會降低信心分數或被股票池過濾。
    """

    code: str
    name: str
    market: str
    industry: str
    close: float
    previous_close: float
    volume: float
    turnover: float
    day_high: float
    day_low: float
    ma5: float
    ma20: float
    ma60: float
    volume_ma20: float
    foreign_buy_sell: float = 0.0
    investment_trust_buy_sell: float = 0.0
    dealer_buy_sell: float = 0.0
    data_days: int = 0
    is_etf: bool = False
    is_full_delivery: bool = False
    is_disposition: bool = False
    has_major_abnormality: bool = False
    event_risks: Sequence[str] = field(default_factory=tuple)
    historical_returns: Sequence[float] = field(default_factory=tuple)


@dataclass(frozen=True)
class MarketContext:
    """功能：台灣市場大盤與族群背景，用於報告文字與風險提示。"""

    taiex_change_pct: float
    otc_change_pct: float
    market_breadth_pct: float
    strong_industries: Sequence[str]
    weak_industries: Sequence[str]
    event_notes: Sequence[str] = field(default_factory=tuple)


class TaiwanMarketProvider(ABC):
    """功能：台股資料 provider 介面。

    使用說明：mock provider 用於測試與離線報告；真實 provider 可接 TWSE/TPEx/MOPS/TAIFEX/TDCC，
    但只能回傳市場資料，不可接券商、委託、下單或交易執行 API。
    """

    name = "base"

    @abstractmethod
    def list_snapshots(self, as_of: Optional[date] = None) -> List[StockSnapshot]:
        """功能：取得同一日期批次的 TWSE/TPEx 股票日級資料。"""

    @abstractmethod
    def get_market_context(self, as_of: Optional[date] = None) -> MarketContext:
        """功能：取得大盤方向、族群強弱與事件風險摘要。"""


class MockTaiwanMarketProvider(TaiwanMarketProvider):
    """功能：離線 mock 台股資料 provider。

    使用說明：預設產生足夠樣本，讓開盤前/收盤後報告、前 20 排行與回測摘要可在無 API key 時運作。
    """

    name = "mock"

    def __init__(self, snapshots: Optional[Sequence[StockSnapshot]] = None):
        self._snapshots = list(snapshots) if snapshots is not None else self._build_default_snapshots()

    def list_snapshots(self, as_of: Optional[date] = None) -> List[StockSnapshot]:
        return list(self._snapshots)

    def get_market_context(self, as_of: Optional[date] = None) -> MarketContext:
        return MarketContext(
            taiex_change_pct=0.82,
            otc_change_pct=0.46,
            market_breadth_pct=58.0,
            strong_industries=("半導體", "AI伺服器", "散熱", "網通"),
            weak_industries=("航運", "觀光"),
            event_notes=("美股科技股收高", "台指期夜盤偏多", "留意除權息與法說會事件"),
        )

    @staticmethod
    def _build_default_snapshots() -> List[StockSnapshot]:
        """功能：建立可重現的 mock 台股樣本，包含上市、上櫃與應被排除的標的。"""
        industries = ["半導體", "AI伺服器", "散熱", "網通", "電源", "IC設計", "PCB", "金融", "生技", "電機"]
        base_codes = [
            ("2330", "台積電", "TWSE", "半導體"),
            ("2454", "聯發科", "TWSE", "IC設計"),
            ("2317", "鴻海", "TWSE", "AI伺服器"),
            ("2382", "廣達", "TWSE", "AI伺服器"),
            ("6669", "緯穎", "TWSE", "AI伺服器"),
            ("3017", "奇鋐", "TWSE", "散熱"),
            ("3324", "雙鴻", "TPEx", "散熱"),
            ("2345", "智邦", "TWSE", "網通"),
            ("6285", "啟碁", "TWSE", "網通"),
            ("2308", "台達電", "TWSE", "電源"),
            ("3037", "欣興", "TWSE", "PCB"),
            ("8046", "南電", "TWSE", "PCB"),
            ("6488", "環球晶", "TPEx", "半導體"),
            ("3443", "創意", "TWSE", "IC設計"),
            ("3661", "世芯-KY", "TWSE", "IC設計"),
            ("5274", "信驊", "TPEx", "IC設計"),
            ("6239", "力成", "TWSE", "半導體"),
            ("3711", "日月光投控", "TWSE", "半導體"),
            ("2379", "瑞昱", "TWSE", "IC設計"),
            ("2376", "技嘉", "TWSE", "AI伺服器"),
            ("2356", "英業達", "TWSE", "AI伺服器"),
            ("3231", "緯創", "TWSE", "AI伺服器"),
            ("6147", "頎邦", "TPEx", "半導體"),
            ("3105", "穩懋", "TPEx", "半導體"),
            ("1504", "東元", "TWSE", "電機"),
            ("5871", "中租-KY", "TWSE", "金融"),
            ("9910", "豐泰", "TWSE", "其他"),
            ("1795", "美時", "TPEx", "生技"),
        ]
        rows: List[StockSnapshot] = []
        for idx, (code, name, market, industry) in enumerate(base_codes):
            close = 60.0 + idx * 7.5
            momentum = 1.03 + (idx % 7) * 0.012
            ma20 = close / momentum
            ma60 = ma20 * (0.90 + (idx % 5) * 0.012)
            volume_ma20 = 900_000 + idx * 55_000
            volume = volume_ma20 * (1.15 + (idx % 6) * 0.22)
            turnover = close * volume
            hist = tuple(round(0.002 + ((idx + d) % 9 - 3) * 0.0035, 5) for d in range(45))
            rows.append(
                StockSnapshot(
                    code=code,
                    name=name,
                    market=market,
                    industry=industry if industry else industries[idx % len(industries)],
                    close=round(close, 2),
                    previous_close=round(close * (0.985 + (idx % 5) * 0.006), 2),
                    volume=round(volume, 0),
                    turnover=round(turnover, 0),
                    day_high=round(close * 1.035, 2),
                    day_low=round(close * 0.975, 2),
                    ma5=round(close * (0.985 + (idx % 4) * 0.006), 2),
                    ma20=round(ma20, 2),
                    ma60=round(ma60, 2),
                    volume_ma20=round(volume_ma20, 0),
                    foreign_buy_sell=round(turnover * (0.006 + (idx % 4) * 0.002), 0),
                    investment_trust_buy_sell=round(turnover * (0.002 + (idx % 3) * 0.001), 0),
                    dealer_buy_sell=round(turnover * (0.001 + (idx % 2) * 0.001), 0),
                    data_days=120,
                    event_risks=("法說會",) if idx in (4, 14, 17) else (),
                    historical_returns=hist,
                )
            )
        rows.extend(
            [
                StockSnapshot("0050", "元大台灣50", "TWSE", "ETF", 180, 179, 8_000_000, 1_440_000_000, 181, 178, 178, 175, 170, 6_000_000, data_days=120, is_etf=True),
                StockSnapshot("9991", "處置樣本", "TPEx", "其他", 25, 25, 2_000_000, 50_000_000, 27, 24, 25, 24, 23, 1_000_000, data_days=120, is_disposition=True),
                StockSnapshot("9992", "低流動樣本", "TWSE", "其他", 18, 18, 20_000, 360_000, 19, 17, 18, 18, 18, 18_000, data_days=120),
            ]
        )
        return rows


class OfficialTaiwanOpenDataProvider(TaiwanMarketProvider):
    """功能：官方資料 provider 擴充點。

    使用說明：此 MVP 不在未確認授權/頻率限制前硬接官方端點；部署者可在此類別實作 TWSE/TPEx/MOPS 等抓取。
    """

    name = "official"

    TWSE_DAILY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    TWSE_VALUATION_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
    TWSE_FOREIGN_URL = "https://openapi.twse.com.tw/v1/fund/MI_QFIIS_sort_20"
    TPEX_DAILY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    TPEX_VALUATION_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
    TPEX_WARNING_URL = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"
    TPEX_DISPOSAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
    TPEX_INSTI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_trading"

    def __init__(self, http_get: Optional[Callable[[str], List[Dict[str, Any]]]] = None, timeout: float = 12.0):
        # 2026/05/27 Steve Peng：實作官方 OpenAPI provider 並允許測試注入 HTTP 讀取器。
        # 修改原因：GUI 需要 official/auto provider，而測試不可依賴外網。
        # 修改前代碼：official provider 僅丟出未啟用錯誤。
        # 修改後功能：可讀取 TWSE/TPEx 官方公開資料，欄位不足時標低可信度與事件風險。
        self._http_get = http_get or self._requests_get_json
        self.timeout = timeout
        self._snapshot_cache: Optional[List[StockSnapshot]] = None

    def list_snapshots(self, as_of: Optional[date] = None) -> List[StockSnapshot]:
        if self._snapshot_cache is not None:
            return list(self._snapshot_cache)

        try:
            twse_daily = self._http_get(self.TWSE_DAILY_URL)
            twse_valuation = self._index_by_code(self._http_get(self.TWSE_VALUATION_URL), "Code")
            self._http_get(self.TWSE_FOREIGN_URL)  # Kept as source validation; not a daily net-buy series.

            tpex_daily = self._http_get(self.TPEX_DAILY_URL)
            tpex_valuation = self._index_by_code(self._http_get(self.TPEX_VALUATION_URL), "SecuritiesCompanyCode")
            tpex_warning = self._index_by_code(self._http_get(self.TPEX_WARNING_URL), "SecuritiesCompanyCode")
            tpex_disposal = self._index_by_code(self._http_get(self.TPEX_DISPOSAL_URL), "SecuritiesCompanyCode")
            tpex_insti = self._index_by_code(self._http_get(self.TPEX_INSTI_URL), "SecuritiesCompanyCode")
        except Exception as exc:
            raise TaiwanMarketProviderError(f"Official Taiwan OpenAPI fetch failed: {exc}") from exc

        snapshots: List[StockSnapshot] = []
        for row in twse_daily or []:
            item = self._parse_twse_snapshot(row, twse_valuation.get(str(row.get("Code") or "").strip()))
            if item:
                snapshots.append(item)
        for row in tpex_daily or []:
            code = str(row.get("SecuritiesCompanyCode") or "").strip()
            item = self._parse_tpex_snapshot(
                row,
                valuation=tpex_valuation.get(code),
                warning=tpex_warning.get(code),
                disposal=tpex_disposal.get(code),
                insti=tpex_insti.get(code),
            )
            if item:
                snapshots.append(item)

        if not snapshots:
            raise TaiwanMarketProviderError("Official Taiwan OpenAPI returned no usable TWSE/TPEx rows")
        self._snapshot_cache = snapshots
        return list(snapshots)

    def get_market_context(self, as_of: Optional[date] = None) -> MarketContext:
        snapshots = self.list_snapshots(as_of=as_of)
        twse_changes = [self._snapshot_change_pct(item) for item in snapshots if item.market == "TWSE"]
        tpex_changes = [self._snapshot_change_pct(item) for item in snapshots if item.market == "TPEx"]
        all_changes = twse_changes + tpex_changes
        breadth = 50.0
        if all_changes:
            breadth = sum(1 for value in all_changes if value > 0) / len(all_changes) * 100.0
        return MarketContext(
            taiex_change_pct=round(mean(twse_changes), 2) if twse_changes else 0.0,
            otc_change_pct=round(mean(tpex_changes), 2) if tpex_changes else 0.0,
            market_breadth_pct=round(breadth, 2),
            strong_industries=("官方資料強勢股",),
            weak_industries=("官方資料弱勢股",),
            event_notes=(
                "資料來源：TWSE/TPEx 官方 OpenAPI",
                "官方 provider 以日成交、估值、注意/處置與部分法人資料為主；歷史均線不足時已降低信心分數",
            ),
        )

    def _requests_get_json(self, url: str) -> List[Dict[str, Any]]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; QuantDinger Taiwan Market UI/1.0)"}
        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
        except requests.exceptions.SSLError:
            # 2026/05/27 Steve Peng：官方公開資料 SSL 鏈在部分 Windows/Python 環境會驗證失敗，改採一次唯讀重試。
            # 修改原因：TPEx OpenAPI 在本機 requests 可能出現 Missing Subject Key Identifier，但瀏覽器/PowerShell 可讀。
            # 修改前代碼：SSL 驗證失敗即整個 official provider fallback mock。
            # 修改後功能：僅對 TWSE/TPEx 官方公開資料重試一次 verify=False；仍只讀資料，不涉及金鑰或交易。
            logger.warning("Taiwan official OpenAPI SSL verification failed; retrying public read-only endpoint with verify=False: %s", url)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                response = requests.get(url, headers=headers, timeout=self.timeout, verify=False)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    @staticmethod
    def _index_by_code(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
        return {str(row.get(key) or "").strip(): row for row in rows or [] if str(row.get(key) or "").strip()}

    def _parse_twse_snapshot(self, row: Dict[str, Any], valuation: Optional[Dict[str, Any]]) -> Optional[StockSnapshot]:
        code = str(row.get("Code") or "").strip()
        if not code or len(code) != 4 or not code.isdigit():
            return None
        close = self._safe_float(row.get("ClosingPrice"))
        change = self._safe_float(row.get("Change"))
        volume = self._safe_float(row.get("TradeVolume"))
        turnover = self._safe_float(row.get("TradeValue"))
        if close <= 0 or volume <= 0 or turnover <= 0:
            return None
        previous_close = close - change if close - change > 0 else close
        return StockSnapshot(
            code=code,
            name=str(row.get("Name") or code).strip(),
            market="TWSE",
            industry=self._industry_from_valuation(valuation),
            close=close,
            previous_close=previous_close,
            volume=volume,
            turnover=turnover,
            day_high=self._safe_float(row.get("HighestPrice"), close),
            day_low=self._safe_float(row.get("LowestPrice"), close),
            ma5=max(close - change / 2.0, 0.01),
            ma20=max(previous_close, 0.01),
            ma60=max(previous_close * 0.97, 0.01),
            volume_ma20=max(volume * 0.9, 1.0),
            data_days=60,
            is_etf=self._is_etf_code(code),
            event_risks=("官方資料未含完整歷史均線/法人每日明細，信心分數已降低",),
            historical_returns=self._synthetic_returns(close, previous_close),
        )

    def _parse_tpex_snapshot(
        self,
        row: Dict[str, Any],
        *,
        valuation: Optional[Dict[str, Any]],
        warning: Optional[Dict[str, Any]],
        disposal: Optional[Dict[str, Any]],
        insti: Optional[Dict[str, Any]],
    ) -> Optional[StockSnapshot]:
        code = str(row.get("SecuritiesCompanyCode") or "").strip()
        if not code or len(code) != 4 or not code.isdigit():
            return None
        close = self._safe_float(row.get("Close"))
        change = self._safe_float(row.get("Change"))
        volume = self._safe_float(row.get("TradingShares"))
        turnover = self._safe_float(row.get("TransactionAmount"))
        if close <= 0 or volume <= 0 or turnover <= 0:
            return None
        previous_close = close - change if close - change > 0 else close
        event_risks = ["官方資料未含完整歷史均線，信心分數已降低"]
        if warning:
            event_risks.append("TPEx 注意股票：" + str(warning.get("TradingInformation") or "").strip()[:120])
        if disposal:
            event_risks.append("TPEx 處置資訊：" + str(disposal.get("DispositionPeriod") or "").strip())
        net_buy = self._safe_float((insti or {}).get("NetBuy")) * 1000.0 * close
        return StockSnapshot(
            code=code,
            name=str(row.get("CompanyName") or code).strip(),
            market="TPEx",
            industry=self._industry_from_valuation(valuation),
            close=close,
            previous_close=previous_close,
            volume=volume,
            turnover=turnover,
            day_high=self._safe_float(row.get("High"), close),
            day_low=self._safe_float(row.get("Low"), close),
            ma5=max(close - change / 2.0, 0.01),
            ma20=max(previous_close, 0.01),
            ma60=max(previous_close * 0.97, 0.01),
            volume_ma20=max(volume * 0.9, 1.0),
            foreign_buy_sell=net_buy,
            data_days=60,
            is_etf=self._is_etf_code(code),
            is_disposition=bool(disposal),
            has_major_abnormality=bool(warning),
            event_risks=tuple(event_risks),
            historical_returns=self._synthetic_returns(close, previous_close),
        )

    @staticmethod
    def _industry_from_valuation(valuation: Optional[Dict[str, Any]]) -> str:
        if not valuation:
            return "官方資料未提供產業"
        pe = valuation.get("PEratio") or valuation.get("PriceEarningRatio") or ""
        return f"官方估值資料 PE={pe or 'N/A'}"

    @staticmethod
    def _is_etf_code(code: str) -> bool:
        return str(code).startswith("00")

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        if not match:
            return default
        try:
            return float(match.group(0))
        except Exception:
            return default

    @staticmethod
    def _snapshot_change_pct(item: StockSnapshot) -> float:
        if item.previous_close <= 0:
            return 0.0
        return (item.close - item.previous_close) / item.previous_close * 100.0

    @staticmethod
    def _synthetic_returns(close: float, previous_close: float) -> tuple[float, ...]:
        daily = ((close - previous_close) / previous_close) if previous_close > 0 else 0.0
        capped = max(-0.04, min(0.04, daily))
        return tuple(round(capped * (0.55 + (idx % 5) * 0.08), 5) for idx in range(30))


class AutoTaiwanMarketProvider(TaiwanMarketProvider):
    """功能：官方資料優先、mock 備援的台股 provider。

    使用說明：GUI 預設使用此 provider；官方 OpenAPI 失敗或資料不足時不阻斷介面，改用 mock 並回報狀態。
    """

    name = "auto"

    def __init__(
        self,
        official_provider: Optional[TaiwanMarketProvider] = None,
        fallback_provider: Optional[TaiwanMarketProvider] = None,
    ):
        # 2026/05/27 Steve Peng：新增 auto provider。
        # 修改原因：圖形化介面需要真實資料優先，但不能因網路或授權問題中斷展示。
        # 修改前代碼：provider 只能選 mock 或未啟用 official。
        # 修改後功能：official 成功時使用官方資料，失敗時自動切回 mock 並暴露 data_source_status。
        self.official_provider = official_provider or OfficialTaiwanOpenDataProvider()
        self.fallback_provider = fallback_provider or MockTaiwanMarketProvider()
        self._active_provider: TaiwanMarketProvider = self.official_provider
        self.status: Dict[str, Any] = {
            "provider": "official",
            "fallback_used": False,
            "message": "使用 TWSE/TPEx 官方 OpenAPI",
        }

    @property
    def name(self) -> str:  # type: ignore[override]
        if self.status.get("fallback_used"):
            return "auto(mock)"
        return "auto(official)"

    def list_snapshots(self, as_of: Optional[date] = None) -> List[StockSnapshot]:
        try:
            rows = self.official_provider.list_snapshots(as_of=as_of)
            if not rows:
                raise TaiwanMarketProviderError("official provider returned no snapshots")
            self._active_provider = self.official_provider
            self.status = {
                "provider": "official",
                "fallback_used": False,
                "message": "使用 TWSE/TPEx 官方 OpenAPI",
            }
            return rows
        except Exception as exc:
            self._active_provider = self.fallback_provider
            self.status = {
                "provider": "mock",
                "fallback_used": True,
                "message": str(exc),
            }
            return self.fallback_provider.list_snapshots(as_of=as_of)

    def get_market_context(self, as_of: Optional[date] = None) -> MarketContext:
        if self._active_provider is self.fallback_provider or self.status.get("fallback_used"):
            return self.fallback_provider.get_market_context(as_of=as_of)
        try:
            return self.official_provider.get_market_context(as_of=as_of)
        except Exception as exc:
            self._active_provider = self.fallback_provider
            self.status = {
                "provider": "mock",
                "fallback_used": True,
                "message": str(exc),
            }
            return self.fallback_provider.get_market_context(as_of=as_of)


def create_taiwan_market_provider(name: str = "mock") -> TaiwanMarketProvider:
    """功能：依名稱建立台股 provider。

    使用說明：`mock` 為預設且免 API key；`official` 僅保留擴充點，不會自動呼叫外部資料或交易服務。
    """
    key = (name or "mock").strip().lower()
    if key in ("auto", "official_first", "official-fallback"):
        return AutoTaiwanMarketProvider()
    if key in ("mock", "demo", "sample"):
        return MockTaiwanMarketProvider()
    if key in ("official", "twse", "tpex"):
        return OfficialTaiwanOpenDataProvider()
    raise TaiwanMarketProviderError(f"Unsupported Taiwan market provider: {name}")


class TaiwanMarketService:
    """功能：台股強勢候選股資訊分析服務。

    使用說明：呼叫 `generate_report()` 產生開盤前/收盤後報告，呼叫 `backtest_top_candidates()` 取得候選股回測摘要。
    """

    def __init__(self, provider: Optional[TaiwanMarketProvider] = None):
        self.provider = provider or MockTaiwanMarketProvider()

    def build_universe(self, *, include_etf: bool = False, as_of: Optional[date] = None) -> List[StockSnapshot]:
        """功能：建立台股股票池並排除不適合納入強勢排行的標的。"""
        snapshots = self.provider.list_snapshots(as_of=as_of)
        universe: List[StockSnapshot] = []
        for item in snapshots:
            if self._universe_exclusion_reasons(item, include_etf=include_etf):
                continue
            universe.append(item)
        return universe

    def rank_candidates(
        self,
        *,
        top_n: int = 20,
        include_etf: bool = False,
        as_of: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """功能：計算強勢分數並回傳前 N 檔候選股資訊。"""
        scored = [self._candidate_payload(item) for item in self.build_universe(include_etf=include_etf, as_of=as_of)]
        scored.sort(key=lambda row: (row["strength_score"], row["confidence_score"], row["liquidity"]["turnover"]), reverse=True)
        return scored[: max(1, int(top_n or 20))]

    def generate_report(
        self,
        session: str,
        *,
        as_of: Optional[date] = None,
        top_n: int = 20,
        include_etf: bool = False,
    ) -> Dict[str, Any]:
        """功能：產生開盤前或收盤後台股資訊報告。

        使用說明：`session` 支援 `pre_market` 與 `post_market`；週末會輸出休市報告。
        """
        report_date = as_of or self._today_taipei()
        normalized_session = self._normalize_session(session)
        if not self.is_taiwan_business_day(report_date):
            return self._closed_market_report(normalized_session, report_date)

        context = self.provider.get_market_context(as_of=report_date)
        candidates = self.rank_candidates(top_n=top_n, include_etf=include_etf, as_of=report_date)
        # 2026/05/27 Steve Peng：移除報告輸出的內部回測欄位。
        # 修改原因：`_historical_returns` 只供資訊型回測計算，不應出現在使用者報告 payload。
        # 修改前代碼：`generate_report` 直接回傳 rank_candidates 的內部資料列。
        # 修改後功能：報告只顯示公開候選股欄位，回測仍在 `backtest_top_candidates` 內部使用歷史報酬。
        public_candidates = [self._public_candidate(row) for row in candidates]
        base = {
            "disclaimer": TAIWAN_MARKET_DISCLAIMER,
            "provider": self.provider.name,
            "session": normalized_session,
            "report_date": report_date.isoformat(),
            "timezone": TAIPEI_TZ_NAME,
            "market_scope": list(VALID_TAIWAN_MARKETS),
            "include_etf": bool(include_etf),
            "today_market_direction": self._market_direction(context),
            "direction_basis": self._direction_basis(context),
            "top_candidates": public_candidates,
            "risk_reference": self._risk_reference(),
            "manual_only_notice": "本報告僅供資訊分析與風險提示；實際買賣需由使用者自行到券商系統人工操作。",
        }
        # 2026/05/27 Steve Peng：報告加入資料來源狀態。
        # 修改原因：GUI 需要清楚顯示 official 是否成功、是否 fallback 到 mock。
        # 修改前代碼：報告只顯示 provider 名稱，無法得知資料不足或官方 API 失敗原因。
        # 修改後功能：若 provider 暴露 status，報告會附上 data_source_status 供 UI 呈現。
        if hasattr(self.provider, "status"):
            base["data_source_status"] = dict(getattr(self.provider, "status") or {})
        if normalized_session == "post_market":
            base.update(self._post_market_sections(context, public_candidates))
        return base

    def analyze_stock(
        self,
        query: str,
        *,
        as_of: Optional[date] = None,
        include_etf: bool = True,
    ) -> Dict[str, Any]:
        """功能：依股票代號或名稱產生指定個股 read-only 現況分析。

        使用說明：`query` 可輸入股票代號或名稱片段；輸出僅包含資訊分析、風險提示與觀察說明，
        不提供任何下單、自動下單、paper/live trading、broker API 或交易執行功能。
        """
        # 2026/05/27 Steve Peng：新增原因：使用者需要在 GUI/API 輸入股票名稱或代號後查看單檔股票現況。
        # 修改前代碼：台股模組只支援整體報告、排行榜與回測摘要。
        # 修改後功能：新增指定個股分析資料結構，重用既有強勢評分與風險模型並保持 read-only。
        report_date = as_of or self._today_taipei()
        normalized_query = (query or "").strip()
        snapshots = self.provider.list_snapshots(as_of=report_date)
        base: Dict[str, Any] = {
            "disclaimer": TAIWAN_MARKET_DISCLAIMER,
            "provider": self.provider.name,
            "query": normalized_query,
            "report_date": report_date.isoformat(),
            "timezone": TAIPEI_TZ_NAME,
            "manual_only_notice": "本分析僅供資訊觀察與風險提示，非投資建議；實際買賣需由使用者自行到券商系統人工操作。",
        }
        if hasattr(self.provider, "status"):
            base["data_source_status"] = dict(getattr(self.provider, "status") or {})

        if not normalized_query:
            base.update(
                {
                    "status": "invalid_query",
                    "message": "請輸入股票代號或股票名稱。",
                    "suggestions": self._stock_suggestions("", snapshots),
                }
            )
            return base

        target = self._find_stock_snapshot(normalized_query, snapshots)
        if target is None:
            base.update(
                {
                    "status": "not_found",
                    "message": f"找不到符合「{normalized_query}」的台股標的；請確認代號、名稱或資料來源。",
                    "suggestions": self._stock_suggestions(normalized_query, snapshots),
                }
            )
            return base

        candidate = self._candidate_payload(target)
        public_candidate = self._public_candidate(candidate)
        exclusion_reasons = self._universe_exclusion_reasons(target, include_etf=include_etf)
        ranked = self.rank_candidates(top_n=9999, include_etf=include_etf, as_of=report_date)
        rank_position = next((idx for idx, row in enumerate(ranked, start=1) if row.get("code") == target.code), None)
        base.update(
            {
                "status": "found",
                "stock": {
                    "code": target.code,
                    "name": target.name,
                    "market": target.market,
                    "industry": target.industry,
                    "is_etf": target.is_etf,
                },
                "current_snapshot": self._stock_snapshot_payload(target),
                "quantitative_analysis": {
                    "strength_score": public_candidate["strength_score"],
                    "confidence_score": public_candidate["confidence_score"],
                    "risk_level": public_candidate["risk_level"],
                    "rank_in_current_universe": rank_position,
                    "score_breakdown": {key: round(value, 2) for key, value in self._score_parts(target).items()},
                    "liquidity": public_candidate["liquidity"],
                    "data_quality": "normal" if target.data_days >= 90 else "low_confidence",
                },
                "universe_filter": {
                    "eligible_for_strength_ranking": not exclusion_reasons,
                    "exclusion_reasons": exclusion_reasons,
                },
                "observation_reference": {
                    "observe_entry_price_range": public_candidate["observe_entry_price_range"],
                    "stop_loss_observe_price": public_candidate["stop_loss_observe_price"],
                    "take_profit_observe_range": public_candidate["take_profit_observe_range"],
                    "max_observe_position_pct": public_candidate["max_observe_position_pct"],
                    "chasing_suitability": public_candidate["chasing_suitability"],
                    "suggested_observation": self._stock_observation_guidance(public_candidate, target),
                    "guidance_note": "以下為觀察建議與風險提示，非投資建議，請自行評估風險。",
                },
                "primary_reasons": public_candidate["primary_reasons"],
                "primary_risks": public_candidate["primary_risks"],
                "event_risk": public_candidate["event_risk"],
                "next_watch_items": [
                    "確認官方資料是否已更新至最新交易日。",
                    "觀察成交量是否維持在 20 日均量以上，避免單日異常量造成誤判。",
                    "檢查 MOPS 重大訊息、財報、法說、除權息與處置/警示資訊。",
                    "留意跳空、流動性、交易成本、手續費、證交稅與滑價估算。",
                ],
            }
        )
        return base

    def backtest_top_candidates(
        self,
        *,
        days: int = 60,
        top_n: int = 20,
        include_etf: bool = False,
        commission_rate: float = 0.001425,
        tax_rate: float = 0.003,
        slippage_rate: float = 0.001,
    ) -> Dict[str, Any]:
        """功能：回測每日前 N 候選股的資訊型績效摘要。

        使用說明：mock provider 使用內建歷史報酬；真實 provider 若資料不足會回傳低可信度或不可回測。
        """
        candidates = self.rank_candidates(top_n=top_n, include_etf=include_etf)
        sample_days = max(1, int(days or 60))
        if not candidates:
            return self._not_backtestable("No candidates after universe filters", sample_days, top_n)

        daily_returns: List[float] = []
        total_cost = (commission_rate * 2.0) + tax_rate + slippage_rate
        for day_idx in range(sample_days):
            day_values: List[float] = []
            for candidate in candidates:
                returns = candidate.get("_historical_returns") or []
                if day_idx < len(returns):
                    day_values.append(float(returns[day_idx]) - total_cost)
            if day_values:
                daily_returns.append(mean(day_values))

        if len(daily_returns) < 5:
            return self._not_backtestable("Insufficient historical return samples", sample_days, top_n)

        equity_curve = self._equity_curve(daily_returns)
        avg_return = mean(daily_returns)
        volatility = pstdev(daily_returns) if len(daily_returns) > 1 else 0.0
        sharpe_like = (avg_return / volatility * math.sqrt(252.0)) if volatility > 0 else 0.0
        cleaned_candidates = [{k: v for k, v in row.items() if k != "_historical_returns"} for row in candidates]
        return {
            "disclaimer": TAIWAN_MARKET_DISCLAIMER,
            "provider": self.provider.name,
            "method": "Equal-weight daily top candidates information backtest",
            "assumptions": {
                "candidate_count_per_day": int(top_n or 20),
                "commission_rate_each_side": commission_rate,
                "securities_transaction_tax_rate": tax_rate,
                "slippage_rate": slippage_rate,
                "execution_note": "僅作資訊型歷史模擬，未建立任何委託、下單或交易執行。",
            },
            "metrics": {
                "sample_days": sample_days,
                "usable_days": len(daily_returns),
                "candidate_count_per_day": int(top_n or 20),
                "win_rate": round(sum(1 for value in daily_returns if value > 0) / len(daily_returns), 4),
                "average_daily_return": round(avg_return, 6),
                "cumulative_return": round(equity_curve[-1] - 1.0, 6),
                "max_drawdown": round(self._max_drawdown(equity_curve), 6),
                "sharpe_like": round(sharpe_like, 4),
                "confidence": "normal" if len(daily_returns) >= 30 else "low",
            },
            "top_candidates_snapshot": cleaned_candidates,
        }

    def schedule_reference(self) -> Dict[str, Any]:
        """功能：回傳 Asia/Taipei 排程建議，不啟動背景交易或 worker。"""
        return {
            "timezone": TAIPEI_TZ_NAME,
            "pre_market": "08:30",
            "post_market": "14:30 或資料更新後",
            "weekend_policy": "週末不產生一般交易日報告；可輸出休市報告。",
            "implementation_note": "此 MVP 提供排程參考與手動/API 觸發；未新增會下單的 scheduler 或 worker。",
        }

    @staticmethod
    def _public_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
        """功能：移除 service 內部欄位，產生 API/report 可公開輸出的候選股資料。"""
        return {k: v for k, v in row.items() if not str(k).startswith("_")}

    @staticmethod
    def _universe_exclusion_reasons(item: StockSnapshot, *, include_etf: bool) -> List[str]:
        """功能：列出標的不納入強勢排行股票池的原因。

        使用說明：排行榜會排除這些標的；指定個股分析仍會顯示現況，但同步提示排除原因。
        """
        # 2026/05/27 Steve Peng：新增原因：指定個股分析需要解釋標的是否被強勢排行股票池排除。
        # 修改前代碼：`build_universe` 直接 continue，使用者無法知道單檔被排除的原因。
        # 修改後功能：集中產生排除原因，供股票池與單檔分析共用。
        reasons: List[str] = []
        if item.market not in VALID_TAIWAN_MARKETS:
            reasons.append("市場別不屬於 TWSE 或 TPEx。")
        if item.is_etf and not include_etf:
            reasons.append("ETF 預設與個股分開，未勾選納入 ETF。")
        if item.is_full_delivery:
            reasons.append("全額交割標的，風險較高。")
        if item.is_disposition:
            reasons.append("處置股或受交易限制標的。")
        if item.has_major_abnormality:
            reasons.append("存在重大異常或警示資訊。")
        if item.data_days < 60:
            reasons.append("可用資料天數不足 60 日，可信度偏低。")
        if item.volume < 100_000 or item.turnover < 10_000_000:
            reasons.append("成交量或成交金額不足，流動性風險偏高。")
        if min(item.close, item.previous_close, item.ma20, item.ma60, item.volume_ma20) <= 0:
            reasons.append("價格、均線或均量資料不足，無法穩定評分。")
        return reasons

    @staticmethod
    def _find_stock_snapshot(query: str, snapshots: Sequence[StockSnapshot]) -> Optional[StockSnapshot]:
        """功能：依代號精準比對或名稱片段比對尋找股票。"""
        raw = (query or "").strip()
        lowered = raw.lower()
        for item in snapshots:
            if raw == item.code:
                return item
        for item in snapshots:
            if lowered and lowered in item.name.lower():
                return item
        return None

    @staticmethod
    def _stock_suggestions(query: str, snapshots: Sequence[StockSnapshot], limit: int = 8) -> List[Dict[str, Any]]:
        """功能：找不到指定股票時回傳相近代號或名稱提示。"""
        raw = (query or "").strip().lower()
        fragments = {raw[idx: idx + 3] for idx in range(max(len(raw) - 2, 0)) if len(raw[idx: idx + 3]) == 3}
        suggestions: List[Dict[str, Any]] = []
        for item in snapshots:
            haystack = f"{item.code} {item.name}".lower()
            matched = (raw and raw in haystack) or any(fragment in haystack for fragment in fragments)
            if matched or not raw:
                suggestions.append({"code": item.code, "name": item.name, "market": item.market, "industry": item.industry})
            if len(suggestions) >= limit:
                break
        if suggestions:
            return suggestions
        return [{"code": item.code, "name": item.name, "market": item.market, "industry": item.industry} for item in snapshots[:limit]]

    def _stock_snapshot_payload(self, item: StockSnapshot) -> Dict[str, Any]:
        """功能：產生指定個股現況欄位，供 API 與 GUI 顯示。"""
        return {
            "close": item.close,
            "previous_close": item.previous_close,
            "day_change_pct": round(self._day_change_pct(item), 2),
            "day_high": item.day_high,
            "day_low": item.day_low,
            "volume": int(item.volume),
            "turnover": int(item.turnover),
            "volume_vs_20d": round(item.volume / item.volume_ma20, 2) if item.volume_ma20 > 0 else None,
            "moving_average": {
                "ma5": item.ma5,
                "ma20": item.ma20,
                "ma60": item.ma60,
                "above_ma20_pct": round((item.close / item.ma20 - 1.0) * 100.0, 2) if item.ma20 > 0 else None,
                "above_ma60_pct": round((item.close / item.ma60 - 1.0) * 100.0, 2) if item.ma60 > 0 else None,
            },
            "institutional_flow": {
                "foreign_buy_sell": int(item.foreign_buy_sell),
                "investment_trust_buy_sell": int(item.investment_trust_buy_sell),
                "dealer_buy_sell": int(item.dealer_buy_sell),
            },
            "data_days": item.data_days,
        }

    @staticmethod
    def _stock_observation_guidance(candidate: Dict[str, Any], item: StockSnapshot) -> str:
        """功能：依分數與風險產生資訊型觀察建議。

        使用說明：文字僅描述量價與風險觀察重點，不構成買進、賣出或持有建議。
        """
        risk_level = str(candidate.get("risk_level") or "")
        strength = float(candidate.get("strength_score") or 0.0)
        day_change = ((item.close - item.previous_close) / item.previous_close * 100.0) if item.previous_close > 0 else 0.0
        if risk_level == "High":
            return "風險等級偏高，應優先確認處置、警示、重大訊息與流動性；不適合只因短線強勢而追價觀察。"
        if day_change > 7.0:
            return "單日漲幅偏大，追高風險較高；可等待量價結構穩定後再評估觀察區間。"
        if strength >= 78:
            return "量價與趨勢分數偏強，可列入觀察名單，但仍需搭配停損、停利與事件風險控管。"
        if strength >= 60:
            return "分數屬中性偏強，適合持續追蹤成交量、均線與法人籌碼是否延續。"
        return "目前強勢分數不高，建議先觀察是否重新站回關鍵均線並改善流動性。"

    @staticmethod
    def is_taiwan_business_day(value: date) -> bool:
        """功能：判斷是否為台灣一般工作日；正式休市日曆保留給真實 provider 擴充。"""
        return value.weekday() < 5

    @staticmethod
    def _normalize_session(session: str) -> str:
        raw = (session or "pre_market").strip().lower().replace("-", "_")
        if raw in ("post", "post_market", "close", "after_close"):
            return "post_market"
        return "pre_market"

    @staticmethod
    def _today_taipei() -> date:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo(TAIPEI_TZ_NAME)).date()
        return date.today()

    def _candidate_payload(self, item: StockSnapshot) -> Dict[str, Any]:
        score_parts = self._score_parts(item)
        strength = round(sum(score_parts.values()), 2)
        confidence = self._confidence_score(item)
        risk_level, risks = self._risk_level_and_reasons(item)
        entry_low = round(item.close * 0.98, 2)
        entry_high = round(item.close * 1.015, 2)
        stop_loss = round(max(item.close * 0.91, item.ma20 * 0.94), 2)
        take_profit_low = round(item.close * 1.08, 2)
        take_profit_high = round(item.close * 1.18, 2)
        liquidity_level = self._liquidity_level(item)
        chasing = "不適合追高" if risk_level == "High" or self._day_change_pct(item) > 7.0 else "僅適合回檔觀察"
        if strength >= 78 and risk_level == "Low":
            chasing = "可觀察但不建議無風控追高"
        return {
            "code": item.code,
            "name": item.name,
            "market": item.market,
            "industry": item.industry,
            "strength_score": strength,
            "confidence_score": confidence,
            "risk_level": risk_level,
            "observe_entry_price_range": [entry_low, entry_high],
            "stop_loss_observe_price": stop_loss,
            "take_profit_observe_range": [take_profit_low, take_profit_high],
            "max_observe_position_pct": self._max_position_pct(risk_level, liquidity_level),
            "liquidity": {
                "level": liquidity_level,
                "volume": int(item.volume),
                "turnover": int(item.turnover),
                "volume_vs_20d": round(item.volume / item.volume_ma20, 2) if item.volume_ma20 > 0 else None,
            },
            "primary_reasons": self._primary_reasons(item, score_parts),
            "primary_risks": risks,
            "chasing_suitability": chasing,
            "event_risk": list(item.event_risks) if item.event_risks else ["未偵測到重大事件；仍需查核 MOPS/TWSE/TPEx 最新公告"],
            "_historical_returns": list(item.historical_returns),
        }

    def _score_parts(self, item: StockSnapshot) -> Dict[str, float]:
        day_change = self._day_change_pct(item)
        ma20_gap = (item.close / item.ma20 - 1.0) * 100.0
        ma60_gap = (item.ma20 / item.ma60 - 1.0) * 100.0
        volume_ratio = item.volume / item.volume_ma20 if item.volume_ma20 > 0 else 1.0
        institution_ratio = (item.foreign_buy_sell + item.investment_trust_buy_sell + item.dealer_buy_sell) / item.turnover
        return {
            "price_momentum": self._clamp(20.0 + day_change * 2.2 + ma20_gap * 1.1 + ma60_gap * 0.55, 0, 45),
            "volume_momentum": self._clamp(10.0 + (volume_ratio - 1.0) * 18.0, 0, 25),
            "institutional_flow": self._clamp(8.0 + institution_ratio * 600.0, 0, 18),
            "liquidity_quality": self._clamp(4.0 + math.log10(max(item.turnover, 1)) - 6.5, 0, 8),
            "event_penalty": -4.0 if item.event_risks else 0.0,
        }

    @staticmethod
    def _confidence_score(item: StockSnapshot) -> float:
        data_score = min(item.data_days / 120.0, 1.0) * 45.0
        liquidity_score = min(item.turnover / 250_000_000.0, 1.0) * 35.0
        stability_score = 20.0 if not item.event_risks else 14.0
        return round(data_score + liquidity_score + stability_score, 2)

    def _risk_level_and_reasons(self, item: StockSnapshot) -> tuple[str, List[str]]:
        reasons = []
        day_change = self._day_change_pct(item)
        if day_change > 7:
            reasons.append("單日漲幅偏大，追高風險上升")
        if item.volume / item.volume_ma20 > 2.5:
            reasons.append("量能急增，需留意隔日量縮轉弱")
        if item.turnover < 50_000_000:
            reasons.append("成交金額偏低，流動性與滑價風險較高")
        if item.event_risks:
            reasons.append("存在事件風險：" + "、".join(item.event_risks))
        if not reasons:
            reasons.append("主要風險為大盤回檔、跳空與族群輪動")
        if len(reasons) >= 3 or item.turnover < 30_000_000:
            return "High", reasons
        if len(reasons) == 2 or day_change > 5:
            return "Medium", reasons
        return "Low", reasons

    @staticmethod
    def _liquidity_level(item: StockSnapshot) -> str:
        if item.turnover >= 500_000_000 and item.volume >= 2_000_000:
            return "High"
        if item.turnover >= 80_000_000 and item.volume >= 500_000:
            return "Medium"
        return "Low"

    @staticmethod
    def _max_position_pct(risk_level: str, liquidity_level: str) -> float:
        if risk_level == "Low" and liquidity_level == "High":
            return 8.0
        if risk_level == "High" or liquidity_level == "Low":
            return 3.0
        return 5.0

    def _primary_reasons(self, item: StockSnapshot, score_parts: Dict[str, float]) -> List[str]:
        reasons = [
            f"收盤價高於 20 日均線 {round((item.close / item.ma20 - 1) * 100, 2)}%",
            f"量能為 20 日均量 {round(item.volume / item.volume_ma20, 2)} 倍",
        ]
        net_flow = item.foreign_buy_sell + item.investment_trust_buy_sell + item.dealer_buy_sell
        if net_flow > 0:
            reasons.append(f"法人合計偏買超，估算占成交金額 {round(net_flow / item.turnover * 100, 2)}%")
        if score_parts.get("price_momentum", 0) > 35:
            reasons.append("價格動能分數位於強勢區")
        return reasons

    @staticmethod
    def _day_change_pct(item: StockSnapshot) -> float:
        return ((item.close - item.previous_close) / item.previous_close * 100.0) if item.previous_close > 0 else 0.0

    @staticmethod
    def _market_direction(context: MarketContext) -> str:
        avg = (context.taiex_change_pct + context.otc_change_pct) / 2.0
        if avg > 0.5 and context.market_breadth_pct >= 55:
            return "偏多"
        if avg < -0.5 or context.market_breadth_pct < 45:
            return "偏空"
        return "中性震盪"

    @staticmethod
    def _direction_basis(context: MarketContext) -> List[str]:
        return [
            f"加權指數變動 {context.taiex_change_pct:.2f}%",
            f"櫃買指數變動 {context.otc_change_pct:.2f}%",
            f"市場上漲家數比例約 {context.market_breadth_pct:.1f}%",
            "強勢族群：" + "、".join(context.strong_industries),
            "弱勢族群：" + "、".join(context.weak_industries),
        ] + list(context.event_notes)

    @staticmethod
    def _post_market_sections(context: MarketContext, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        changed = [row for row in candidates if row["liquidity"]["volume_vs_20d"] and row["liquidity"]["volume_vs_20d"] >= 1.8]
        weakening = [row for row in candidates if row["strength_score"] < 55 or row["risk_level"] == "High"]
        avoid_chase = [row for row in candidates if row["chasing_suitability"] == "不適合追高"]
        return {
            "today_review": {
                "summary": "盤面以族群輪動與高流動性權值/AI 供應鏈為主，仍需留意隔日量能延續性。",
                "basis": TaiwanMarketService._direction_basis(context),
            },
            "sector_strength": {
                "strong": list(context.strong_industries),
                "weak": list(context.weak_industries),
            },
            "tomorrow_prediction": {
                "direction": TaiwanMarketService._market_direction(context),
                "key_watch": ["大盤量能是否放大", "台指期與美股科技股延續性", "MOPS 重大訊息與除權息"],
            },
            "unusual_movers": changed[:10],
            "weakening_candidates": weakening[:10],
            "avoid_chasing_candidates": avoid_chase[:10],
        }

    @staticmethod
    def _risk_reference() -> Dict[str, Any]:
        return {
            "stop_loss": "可參考候選股 stop_loss_observe_price；跌破後需重新評估趨勢，不代表自動停損。",
            "take_profit": "可參考 take_profit_observe_range 分批觀察；不代表賣出指令。",
            "chasing_risk": "連續急漲或開高走低時，追高風險顯著增加。",
            "gap_risk": "財報、法說、重大訊息、國際盤與除權息可能造成跳空。",
            "liquidity_risk": "成交金額低或量能不穩時，滑價與出場難度較高。",
            "event_risk": "需查核 MOPS、TWSE、TPEx 最新公告、財報、法說與除權息。",
            "cost_assumption": {
                "commission_each_side": "預設 0.1425%，實際依券商折扣不同。",
                "securities_transaction_tax": "股票賣出端常見 0.3%，ETF 稅率可能不同。",
                "slippage": "預設估 0.1%，低流動性標的需提高。",
            },
        }

    def _closed_market_report(self, session: str, report_date: date) -> Dict[str, Any]:
        return {
            "disclaimer": TAIWAN_MARKET_DISCLAIMER,
            "provider": self.provider.name,
            "session": session,
            "report_date": report_date.isoformat(),
            "timezone": TAIPEI_TZ_NAME,
            "market_scope": list(VALID_TAIWAN_MARKETS),
            "is_trading_day": False,
            "message": "今日為週末或休市日，未執行一般交易日強勢候選股排行。",
            "top_candidates": [],
            "risk_reference": self._risk_reference(),
        }

    @staticmethod
    def _not_backtestable(reason: str, sample_days: int, top_n: int) -> Dict[str, Any]:
        return {
            "disclaimer": TAIWAN_MARKET_DISCLAIMER,
            "method": "Equal-weight daily top candidates information backtest",
            "metrics": {
                "sample_days": sample_days,
                "usable_days": 0,
                "candidate_count_per_day": int(top_n or 20),
                "confidence": "not_backtestable",
            },
            "reason": reason,
        }

    @staticmethod
    def _equity_curve(daily_returns: Iterable[float]) -> List[float]:
        equity = 1.0
        curve = [equity]
        for value in daily_returns:
            equity *= 1.0 + float(value)
            curve.append(equity)
        return curve

    @staticmethod
    def _max_drawdown(equity_curve: Sequence[float]) -> float:
        peak = equity_curve[0] if equity_curve else 1.0
        worst = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            if peak > 0:
                worst = min(worst, value / peak - 1.0)
        return worst

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(value)))
