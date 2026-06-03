"""
Grid fill quantity normalization 鈥?convert exchange order fields to base-asset qty.

Sources (official API docs):
- OKX v5: SWAP sz/accFillSz are contracts; base = contracts * ctVal
  https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-order-details
- Bitget mix order detail: baseVolume = "Amount of coins traded" (base currency)
  https://www.bitget.com/api-doc/contract/trade/Get-Order-Details
- Binance USDT-M futures: executedQty is base-asset quantity
  https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Query-Order
- Bybit v5: cumExecQty = cumulative executed order qty (base for linear)
  https://bybit-exchange.github.io/docs/v5/order/open-order
- Gate v4 futures: size/filled_size are contracts; base = contracts * quanto_multiplier
  https://www.gate.com/docs/developers/apiv4/en/
- HTX USDT swap: trade_volume is in contracts (cont); base = trade_volume * contract_size
  https://huobiapi.github.io/docs/usdt_swap/v1/en/#get-order-information
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.services.live_trading.base import BaseRestClient
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient, to_gate_currency_pair
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.symbols import to_okx_spot_inst_id, to_okx_swap_inst_id
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _okx_ct_val(client: OkxClient, symbol: str, market_type: str) -> float:
    mt = str(market_type or "swap").strip().lower()
    if mt == "spot":
        return 1.0
    try:
        inst_id = to_okx_swap_inst_id(str(symbol))
        inst = client.get_instrument(inst_type="SWAP", inst_id=inst_id) or {}
        ct = _float(inst.get("ctVal"))
        return ct if ct > 0 else 1.0
    except Exception as e:
        logger.debug("okx ctVal lookup failed symbol=%s: %s", symbol, e)
        return 1.0


def okx_swap_position_base_size(
    pos: Dict[str, Any],
    *,
    client: Optional[OkxClient] = None,
) -> float:
    """Convert OKX SWAP ``pos`` field (contracts) to base-asset size."""
    contracts = abs(_float(pos.get("pos")))
    if contracts <= 0:
        return 0.0
    ct_val = _float(pos.get("ctVal"))
    if ct_val <= 0 and client is not None:
        inst_id = str(pos.get("instId") or "").strip()
        sym = inst_id.replace("-SWAP", "").replace("-", "/") if inst_id else ""
        if sym:
            ct_val = _okx_ct_val(client, sym, "swap")
    if ct_val <= 0:
        return 0.0
    return contracts * ct_val


def _gate_quanto_multiplier(client: GateUsdtFuturesClient, symbol: str) -> float:
    try:
        contract = to_gate_currency_pair(str(symbol))
        meta = client.get_contract(contract=contract) or {}
        qm = _float(meta.get("quanto_multiplier") or meta.get("quantoMultiplier"))
        return qm if qm > 0 else 1.0
    except Exception as e:
        logger.debug("gate quanto_multiplier lookup failed symbol=%s: %s", symbol, e)
        return 1.0


def _htx_contract_size(client: HtxClient, symbol: str) -> float:
    try:
        info = client.get_contract_info(symbol=str(symbol)) or {}
        cs = _float(info.get("contract_size") or info.get("contractSize"))
        return cs if cs > 0 else 1.0
    except Exception as e:
        logger.debug("htx contract_size lookup failed symbol=%s: %s", symbol, e)
        return 1.0


def _bitget_contract_size(
    client: BitgetMixClient,
    symbol: str,
    exchange_config: Optional[Dict[str, Any]],
) -> float:
    ex_cfg = exchange_config if isinstance(exchange_config, dict) else {}
    product_type = str(ex_cfg.get("product_type") or ex_cfg.get("productType") or "USDT-FUTURES")
    try:
        contract = client.get_contract(symbol=str(symbol), product_type=product_type) or {}
        cs = _float(
            contract.get("contractSize")
            or contract.get("contractSz")
            or contract.get("ctVal")
        )
        return cs if cs > 0 else 1.0
    except Exception as e:
        logger.debug("bitget contractSize lookup failed symbol=%s: %s", symbol, e)
        return 1.0


def extract_grid_fill_base_qty(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]],
    data: Dict[str, Any],
) -> float:
    """Return filled quantity in base-asset units for one grid order snapshot."""
    if not isinstance(data, dict) or not data:
        return 0.0
    mt = str(market_type or "swap").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"
    ex_cfg = exchange_config if isinstance(exchange_config, dict) else {}

    if isinstance(client, (BinanceFuturesClient, BinanceSpotClient)):
        return _float(data.get("executedQty"))

    if isinstance(client, BybitClient):
        return _float(data.get("cumExecQty"))

    if isinstance(client, BitgetMixClient):
        # Official: baseVolume = amount of coins traded (base currency).
        base_vol = _float(data.get("baseVolume"))
        if base_vol > 0:
            return base_vol
        # Fallback: some responses only expose contract fillSize.
        fill_sz = _float(data.get("fillSize") or data.get("filledQty"))
        if fill_sz > 0 and mt == "swap":
            cs = _bitget_contract_size(client, symbol, ex_cfg)
            if cs != 1.0:
                return fill_sz * cs
        return _float(data.get("filled") or data.get("filledQty") or fill_sz)

    if isinstance(client, BitgetSpotClient):
        return _float(data.get("baseVolume") or data.get("dealSize") or data.get("filled"))

    if isinstance(client, OkxClient):
        contracts = _float(data.get("accFillSz") or data.get("fillSz"))
        if mt == "spot":
            return contracts
        return contracts * _okx_ct_val(client, symbol, mt)

    if isinstance(client, GateUsdtFuturesClient):
        contracts = _float(data.get("filled_size") or data.get("filledSize"))
        if contracts <= 0:
            return 0.0
        return abs(contracts) * _gate_quanto_multiplier(client, symbol)

    if isinstance(client, GateSpotClient):
        return _float(data.get("filled_amount") or data.get("filledAmount"))

    if isinstance(client, CoinbaseExchangeClient):
        return _float(data.get("filled_size") or data.get("filledSize"))

    if isinstance(client, KrakenClient):
        return _float(data.get("vol_exec") or data.get("filled"))

    if isinstance(client, HtxClient):
        if mt == "spot":
            return _float(data.get("field-amount") or data.get("filled-amount") or data.get("filled_amount"))
        contracts = _float(
            data.get("trade_volume")
            or data.get("tradeVolume")
            or data.get("volume")
        )
        if contracts <= 0:
            return 0.0
        return abs(contracts) * _htx_contract_size(client, symbol)

    if isinstance(client, KrakenFuturesClient):
        return _float(data.get("filledSize") or data.get("filled_size"))

    if client is None and data.get("filled_total") and data.get("filled_amount"):
        filled_amt = _float(data.get("filled_amount"))
        filled_total = _float(data.get("filled_total"))
        if filled_amt > 0 and filled_total > 0:
            return filled_total / filled_amt

    # Generic fallback (legacy path 鈥?may be wrong for contract-denominated exchanges).
    return _float(
        data.get("filled")
        or data.get("executedQty")
        or data.get("cumExecQty")
        or data.get("baseVolume")
        or data.get("dealSize")
        or data.get("accFillSz")
        or data.get("filled_size")
    )


def extract_grid_fill_avg_price(
    client: BaseRestClient,
    *,
    data: Dict[str, Any],
    filled_base: float,
) -> float:
    if not isinstance(data, dict) or not data:
        return 0.0

    if isinstance(client, (BinanceFuturesClient, BinanceSpotClient)):
        return _float(data.get("avgPrice"))

    if isinstance(client, BybitClient):
        return _float(data.get("avgPrice"))

    if isinstance(client, (BitgetMixClient, BitgetSpotClient)):
        return _float(data.get("priceAvg") or data.get("avgPrice") or data.get("avg_price"))

    if isinstance(client, OkxClient):
        return _float(data.get("avgPx") or data.get("fillPx"))

    if isinstance(client, GateSpotClient):
        avg = _float(data.get("fill_price") or data.get("fillPrice") or data.get("price"))
        if avg <= 0:
            filled_amt = _float(data.get("filled_amount") or data.get("filledAmount"))
            filled_total = _float(data.get("filled_total") or data.get("filledTotal"))
            if filled_amt > 0 and filled_total > 0:
                return filled_total / filled_amt
        return avg

    if isinstance(client, (GateUsdtFuturesClient,)):
        return _float(data.get("fill_price") or data.get("fillPrice") or data.get("price"))

    if isinstance(client, CoinbaseExchangeClient):
        filled = _float(data.get("filled_size") or data.get("filledSize"))
        executed_value = _float(data.get("executed_value") or data.get("executedValue"))
        if filled > 0 and executed_value > 0:
            return executed_value / filled
        return _float(data.get("price"))

    if isinstance(client, KrakenClient):
        filled = _float(data.get("vol_exec") or data.get("filled"))
        cost = _float(data.get("cost"))
        if filled > 0 and cost > 0:
            return cost / filled
        return _float(data.get("price"))

    if isinstance(client, HtxClient):
        avg = _float(data.get("trade_avg_price") or data.get("tradeAvgPrice"))
        if avg > 0:
            return avg
        turnover = _float(data.get("trade_turnover") or data.get("tradeTurnover"))
        vol = _float(data.get("trade_volume") or data.get("tradeVolume"))
        if turnover > 0 and vol > 0:
            return turnover / vol
        return _float(data.get("price"))

    if isinstance(client, KrakenFuturesClient):
        return _float(data.get("avgFillPrice") or data.get("avg_fill_price"))

    avg = _float(
        data.get("avgPx")
        or data.get("avgPrice")
        or data.get("avg_price")
        or data.get("fill_price")
        or data.get("trade_avg_price")
        or data.get("price")
    )
    if avg <= 0 and data.get("filled_total") and data.get("filled_amount"):
        filled_amt = _float(data.get("filled_amount"))
        filled_total = _float(data.get("filled_total"))
        if filled_amt > 0 and filled_total > 0:
            return filled_total / filled_amt
    return avg


def order_status_from_data(data: Dict[str, Any]) -> str:
    """Normalize exchange order dict -> status: open | partial | filled | cancelled | unknown."""
    if not data:
        return "unknown"
    st_raw = str(
        data.get("state")
        or data.get("status")
        or data.get("orderStatus")
        or data.get("order_status")
        or ""
    ).lower()
    if st_raw in ("filled", "full_fill", "full-fill", "fullfill", "success", "done", "closed", "finished"):
        return "filled"
    if st_raw in ("canceled", "cancelled", "expired", "rejected", "deactivated"):
        return "cancelled"
    if st_raw in ("partially_filled", "partial_fill", "partial-fill", "partially-filled", "partial"):
        return "partial"
    if st_raw in ("live", "new", "open", "not_deal", "notdeal", "submitted"):
        return "open"
    if "fill" in st_raw and "partial" not in st_raw:
        return "filled"
    if "cancel" in st_raw:
        return "cancelled"
    # HTX numeric status: 4=partial, 5=partial cancel, 6=filled, 7=cancelled
    try:
        st_num = int(st_raw)
        if st_num == 6:
            return "filled"
        if st_num in (5, 7, 11):
            return "cancelled"
        if st_num == 4:
            return "partial"
        if st_num in (1, 2, 3):
            return "open"
    except (TypeError, ValueError):
        pass
    filled_hint = _float(
        data.get("accFillSz")
        or data.get("executedQty")
        or data.get("cumExecQty")
        or data.get("baseVolume")
        or data.get("dealSize")
        or data.get("trade_volume")
        or data.get("filled_size")
    )
    return "partial" if filled_hint > 0 else "open"


def parse_grid_order_fill(
    client: BaseRestClient,
    *,
    symbol: str,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]],
    data: Dict[str, Any],
) -> Tuple[float, float, str]:
    """Exchange-aware (filled_base_qty, avg_price, status)."""
    filled = extract_grid_fill_base_qty(
        client,
        symbol=str(symbol),
        market_type=str(market_type or "swap"),
        exchange_config=exchange_config,
        data=data,
    )
    avg = extract_grid_fill_avg_price(client, data=data, filled_base=filled)
    status = order_status_from_data(data)
    return filled, avg, status
