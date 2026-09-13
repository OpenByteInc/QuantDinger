#!/usr/bin/env python3
"""Build structured screening snapshot for idea-generation."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TICKER_FIELDS = ("order_book_id", "ticker", "stock_code", "symbol", "code")
NAME_FIELDS = ("display_name", "name", "stock_name", "company_name", "symbol_name", "symbol")
SECTOR_FIELDS = ("sector_code_name", "industry_name", "sector_name")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 idea-generation 结构化筛选快照")
    parser.add_argument("--strategy", required=True, choices=["value", "growth", "quality", "all"], help="筛选策略")
    parser.add_argument("--data-dir", required=True, help="原始 JSON 数据目录")
    parser.add_argument("--report-date", default=date.today().isoformat(), help="报告日期 (YYYY-MM-DD)")
    parser.add_argument("--output", help="输出结构化快照 JSON 路径")
    parser.add_argument("--top-n", type=int, default=10, help="每个策略展示的候选数量")
    parser.add_argument("--value-pe-median-multiplier", type=float, default=1.0, help="价值策略 PE 上限相对全池 PE 中位数的倍数")
    parser.add_argument("--value-pb-max", type=float, default=1.5, help="价值策略 PB 上限")
    parser.add_argument("--value-roe-min", type=float, default=10.0, help="价值策略 ROE 下限")
    parser.add_argument("--growth-revenue-yoy-min", type=float, default=15.0, help="成长策略营收同比下限")
    parser.add_argument("--growth-profit-yoy-min", type=float, default=20.0, help="成长策略净利润同比下限")
    parser.add_argument("--growth-roe-min", type=float, default=15.0, help="成长策略 ROE 下限")
    parser.add_argument("--quality-roe-min", type=float, default=15.0, help="质量策略 ROE 下限")
    parser.add_argument("--quality-debt-ratio-max", type=float, default=50.0, help="质量策略资产负债率上限")
    return parser.parse_args()


def parse_iso_date(value: Any) -> Optional[date]:
    if value in (None, "", "null"):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_quarter_key(value: str) -> Optional[Tuple[int, int]]:
    match = re.match(r"^(\d{4})q([1-4])$", str(value).strip().lower())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def read_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def extract_records(payload: Any) -> List[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "data" in payload:
            data = payload["data"]
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
            return []
        return [payload]
    return []


def pick_first(record: Dict[str, Any], fields: Sequence[str]) -> Any:
    for field in fields:
        if field in record and record[field] not in (None, ""):
            return record[field]
    return None


def normalize_ticker(record: Dict[str, Any]) -> str:
    value = pick_first(record, TICKER_FIELDS)
    return str(value).strip() if value not in (None, "") else ""


def normalize_name(record: Dict[str, Any]) -> str:
    value = pick_first(record, NAME_FIELDS)
    return str(value).strip() if value not in (None, "") else ""


def normalize_sector(record: Dict[str, Any]) -> str:
    value = pick_first(record, SECTOR_FIELDS)
    return str(value).strip() if value not in (None, "") else "未分类"


def float_or_none(value: Any) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def number_text(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}"


def percent_text(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "无数据"
    return f"{value:+.{digits}f}%"


def unsigned_percent_text(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}%"


def billion_yuan_text(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value / 1e8:.2f}亿元"


def x_text(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}x"


def median_or_none(values: Iterable[Optional[float]]) -> Optional[float]:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return float(median(valid))


def build_stock_pool(records: List[Any]) -> List[str]:
    order_book_ids: List[str] = []
    seen = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        order_book_id = normalize_ticker(item)
        if not order_book_id or order_book_id in seen:
            continue
        seen.add(order_book_id)
        order_book_ids.append(order_book_id)
    return order_book_ids


def build_instrument_map(records: List[Any], stock_ids: Sequence[str]) -> Dict[str, Dict[str, str]]:
    stock_set = set(stock_ids)
    result: Dict[str, Dict[str, str]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        if stock not in stock_set:
            continue
        result[stock] = {
            "name": normalize_name(item) or stock,
            "sector": normalize_sector(item),
        }
    return result


def dedupe_financial_records(records: List[Any], stock_ids: Sequence[str]) -> List[Dict[str, Any]]:
    stock_set = set(stock_ids)
    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        quarter = str(item.get("quarter") or "").lower()
        if stock not in stock_set or not parse_quarter_key(quarter):
            continue
        key = (stock, quarter)
        current = deduped.get(key)
        current_date = parse_iso_date(current.get("info_date")) if current else None
        item_date = parse_iso_date(item.get("info_date"))
        if current is None or (item_date and (current_date is None or item_date >= current_date)):
            deduped[key] = item
    return list(deduped.values())


def build_financial_snapshot(records: List[Any], stock_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    stock_set = set(stock_ids)
    deduped = dedupe_financial_records(records, stock_ids)
    grouped: Dict[str, List[Dict[str, Any]]] = {stock: [] for stock in stock_set}
    for item in deduped:
        stock = normalize_ticker(item)
        if stock in grouped:
            grouped[stock].append(item)

    snapshots: Dict[str, Dict[str, Any]] = {}
    for stock, items in grouped.items():
        if not items:
            continue
        items.sort(
            key=lambda item: (
                parse_quarter_key(str(item.get("quarter") or "").lower()) or (0, 0),
                parse_iso_date(item.get("info_date")) or date.min,
            )
        )
        latest = items[-1]
        latest_quarter = str(latest.get("quarter") or "").lower()
        latest_key = parse_quarter_key(latest_quarter)
        yoy = None
        if latest_key:
            yoy_quarter = f"{latest_key[0] - 1}q{latest_key[1]}"
            for item in items:
                if str(item.get("quarter") or "").lower() == yoy_quarter:
                    yoy = item
        snapshots[stock] = {"latest": latest, "yoy": yoy}
    return snapshots


def latest_factor_map(records: List[Any], stock_ids: Sequence[str], field_name: str) -> Dict[str, float]:
    stock_set = set(stock_ids)
    best: Dict[str, Tuple[date, float]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        if stock not in stock_set:
            continue
        event_date = parse_iso_date(item.get("date") or item.get("datetime"))
        value = float_or_none(item.get(field_name))
        if event_date is None or value is None:
            continue
        current = best.get(stock)
        if current is None or event_date >= current[0]:
            best[stock] = (event_date, value)
    return {stock: value for stock, (_, value) in best.items()}


def build_universe_rows(
    stock_ids: Sequence[str],
    instruments: List[Any],
    latest_financials: List[Any],
    history_financials: List[Any],
    roe_records: List[Any],
    market_cap_records: List[Any],
    pe_records: List[Any],
    pb_records: List[Any],
) -> List[Dict[str, Any]]:
    instrument_map = build_instrument_map(instruments, stock_ids)
    history_snapshot = build_financial_snapshot(history_financials, stock_ids)
    latest_snapshot = build_financial_snapshot(latest_financials, stock_ids)
    roe_map = latest_factor_map(roe_records, stock_ids, "return_on_equity_weighted_average")
    market_cap_map = latest_factor_map(market_cap_records, stock_ids, "market_cap")
    pe_map = latest_factor_map(pe_records, stock_ids, "pe_ratio")
    pb_map = latest_factor_map(pb_records, stock_ids, "pb_ratio")

    rows: List[Dict[str, Any]] = []
    for stock in stock_ids:
        meta = instrument_map.get(stock, {})
        snapshot = latest_snapshot.get(stock) or history_snapshot.get(stock) or {}
        latest = snapshot.get("latest") or {}
        yoy = snapshot.get("yoy") or (history_snapshot.get(stock) or {}).get("yoy") or {}

        revenue = float_or_none(latest.get("revenue"))
        net_profit = float_or_none(latest.get("net_profit"))
        total_assets = float_or_none(latest.get("total_assets"))
        total_liabilities = float_or_none(latest.get("total_liabilities"))
        yoy_revenue = float_or_none(yoy.get("revenue"))
        yoy_profit = float_or_none(yoy.get("net_profit"))

        revenue_yoy = ((revenue / yoy_revenue - 1.0) * 100.0) if revenue is not None and yoy_revenue not in (None, 0) else None
        profit_yoy = ((net_profit / yoy_profit - 1.0) * 100.0) if net_profit is not None and yoy_profit not in (None, 0) else None
        debt_ratio = ((total_liabilities / total_assets) * 100.0) if total_liabilities is not None and total_assets not in (None, 0) else None

        rows.append(
            {
                "order_book_id": stock,
                "name": meta.get("name") or stock,
                "sector": meta.get("sector") or "未分类",
                "latest_quarter": str(latest.get("quarter") or "无数据"),
                "revenue": revenue,
                "net_profit": net_profit,
                "total_assets": total_assets,
                "total_liabilities": total_liabilities,
                "revenue_yoy": revenue_yoy,
                "profit_yoy": profit_yoy,
                "debt_ratio": debt_ratio,
                "roe": roe_map.get(stock),
                "market_cap": market_cap_map.get(stock),
                "pe": pe_map.get(stock),
                "pb": pb_map.get(stock),
            }
        )
    return rows


def universe_statistics(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    latest_quarters = Counter(row["latest_quarter"] for row in rows if row.get("latest_quarter") and row["latest_quarter"] != "无数据")
    sectors = Counter(row["sector"] for row in rows if row.get("sector"))
    valid_financials = sum(1 for row in rows if row.get("revenue") is not None and row.get("net_profit") is not None)
    valid_roe = sum(1 for row in rows if row.get("roe") is not None)
    valid_pe = sum(1 for row in rows if row.get("pe") is not None)
    valid_pb = sum(1 for row in rows if row.get("pb") is not None)
    return {
        "universe_size": len(rows),
        "valid_financials": valid_financials,
        "valid_roe": valid_roe,
        "valid_pe": valid_pe,
        "valid_pb": valid_pb,
        "latest_quarters": latest_quarters,
        "top_sectors": sectors.most_common(5),
        "pe_median": median_or_none(row.get("pe") for row in rows),
        "pb_median": median_or_none(row.get("pb") for row in rows),
        "roe_median": median_or_none(row.get("roe") for row in rows),
        "revenue_yoy_median": median_or_none(row.get("revenue_yoy") for row in rows),
        "profit_yoy_median": median_or_none(row.get("profit_yoy") for row in rows),
        "market_cap_median": median_or_none(row.get("market_cap") for row in rows),
    }


def score_value(row: Dict[str, Any], pe_median: Optional[float]) -> float:
    score = 0.0
    pe = row.get("pe")
    pb = row.get("pb")
    roe = row.get("roe")
    if pe_median and pe not in (None, 0):
        score += max(pe_median - pe, 0.0) / pe_median * 45.0
    if pb is not None:
        score += max(1.5 - pb, 0.0) / 1.5 * 25.0
    if roe is not None:
        score += min(roe, 30.0) / 30.0 * 30.0
    return score


def score_growth(row: Dict[str, Any]) -> float:
    revenue_yoy = row.get("revenue_yoy") or 0.0
    profit_yoy = row.get("profit_yoy") or 0.0
    roe = row.get("roe") or 0.0
    return min(revenue_yoy, 80.0) * 0.3 + min(profit_yoy, 100.0) * 0.5 + min(roe, 30.0) * 0.2


def score_quality(row: Dict[str, Any]) -> float:
    roe = row.get("roe") or 0.0
    debt_ratio = row.get("debt_ratio")
    debt_score = max(50.0 - debt_ratio, 0.0) if debt_ratio is not None else 0.0
    return min(roe, 30.0) * 0.6 + debt_score * 0.4


def screen_value(
    rows: Sequence[Dict[str, Any]],
    top_n: int,
    pe_ceiling: Optional[float],
    pb_max: float,
    roe_min: float,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for row in rows:
        pe = row.get("pe")
        pb = row.get("pb")
        roe = row.get("roe")
        if pe is None or pb is None or roe is None or pe_ceiling is None:
            continue
        if pe < pe_ceiling and pb < pb_max and roe > roe_min:
            candidate = dict(row)
            candidate["score"] = score_value(row, pe_ceiling)
            result.append(candidate)
    result.sort(key=lambda item: (-item["score"], item.get("pe", float("inf"))))
    return result[:top_n]


def screen_growth(
    rows: Sequence[Dict[str, Any]],
    top_n: int,
    revenue_yoy_min: float,
    profit_yoy_min: float,
    roe_min: float,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for row in rows:
        revenue_yoy = row.get("revenue_yoy")
        profit_yoy = row.get("profit_yoy")
        roe = row.get("roe")
        if revenue_yoy is None or profit_yoy is None or roe is None:
            continue
        if revenue_yoy > revenue_yoy_min and profit_yoy > profit_yoy_min and roe > roe_min:
            candidate = dict(row)
            candidate["score"] = score_growth(row)
            result.append(candidate)
    result.sort(key=lambda item: (-item["score"], -(item.get("profit_yoy") or 0.0), -(item.get("revenue_yoy") or 0.0)))
    return result[:top_n]


def screen_quality(
    rows: Sequence[Dict[str, Any]],
    top_n: int,
    roe_min: float,
    debt_ratio_max: float,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for row in rows:
        roe = row.get("roe")
        debt_ratio = row.get("debt_ratio")
        if roe is None or debt_ratio is None:
            continue
        if roe > roe_min and debt_ratio < debt_ratio_max:
            candidate = dict(row)
            candidate["score"] = score_quality(row)
            result.append(candidate)
    result.sort(key=lambda item: (-item["score"], item.get("debt_ratio", float("inf"))))
    return result[:top_n]


def counter_rows(counter: Counter[str], limit: int = 5) -> List[Dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def metric_dict(rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> Dict[str, Optional[float]]:
    return {field: median_or_none(item.get(field) for item in rows) for field in fields}


def strategy_snapshot(
    name: str,
    enabled: bool,
    all_rows: Sequence[Dict[str, Any]],
    display_rows: Sequence[Dict[str, Any]],
    thresholds: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "candidate_count": len(all_rows),
        "display_count": len(display_rows),
        "thresholds": thresholds,
        "display_candidates": list(display_rows),
        "sector_counts": counter_rows(Counter(item.get("sector") or "未分类" for item in all_rows), 5),
        "median_metrics": metric_dict(
            all_rows,
            ("pe", "pb", "roe", "revenue_yoy", "profit_yoy", "debt_ratio", "market_cap", "net_profit"),
        ),
    }


def build_overlap_candidates(
    value_rows: Sequence[Dict[str, Any]],
    growth_rows: Sequence[Dict[str, Any]],
    quality_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    coverage: Dict[str, Dict[str, Any]] = {}
    for strategy, rows in (("value", value_rows), ("growth", growth_rows), ("quality", quality_rows)):
        for item in rows:
            current = coverage.setdefault(
                item["order_book_id"],
                {
                    "order_book_id": item["order_book_id"],
                    "name": item["name"],
                    "sector": item["sector"],
                    "latest_quarter": item["latest_quarter"],
                    "roe": item.get("roe"),
                    "pe": item.get("pe"),
                    "pb": item.get("pb"),
                    "revenue_yoy": item.get("revenue_yoy"),
                    "profit_yoy": item.get("profit_yoy"),
                    "strategies": [],
                },
            )
            current["strategies"].append(strategy)
    return sorted(
        [item for item in coverage.values() if len(item["strategies"]) >= 2],
        key=lambda item: (-len(item["strategies"]), item["name"]),
    )


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser()

    stock_pool_records = extract_records(read_json_file(data_dir / "stock_pool.json"))
    instrument_records = extract_records(read_json_file(data_dir / "instrument_meta.json"))
    latest_financials = extract_records(read_json_file(data_dir / "latest_financials.json"))
    financials_history = extract_records(read_json_file(data_dir / "financials_history.json"))
    roe_records = extract_records(read_json_file(data_dir / "roe.json"))
    market_cap_records = extract_records(read_json_file(data_dir / "market_cap.json"))
    pe_records = extract_records(read_json_file(data_dir / "pe_ratio.json"))
    pb_records = extract_records(read_json_file(data_dir / "pb_ratio.json"))

    stock_ids = build_stock_pool(stock_pool_records)
    if not stock_ids:
        stock_ids = list({normalize_ticker(item) for item in instrument_records if isinstance(item, dict) and normalize_ticker(item)})
    if not stock_ids:
        raise ValueError("无法从 stock_pool.json 或 instrument_meta.json 识别股票池。")

    rows = build_universe_rows(
        stock_ids,
        instrument_records,
        latest_financials,
        financials_history,
        roe_records,
        market_cap_records,
        pe_records,
        pb_records,
    )
    stats = universe_statistics(rows)

    value_pe_ceiling = (stats.get("pe_median") * args.value_pe_median_multiplier) if stats.get("pe_median") is not None else None
    value_thresholds = {
        "pe_lt": value_pe_ceiling,
        "pe_median_multiplier": args.value_pe_median_multiplier,
        "pb_lt": args.value_pb_max,
        "roe_gt": args.value_roe_min,
    }
    growth_thresholds = {
        "revenue_yoy_gt": args.growth_revenue_yoy_min,
        "profit_yoy_gt": args.growth_profit_yoy_min,
        "roe_gt": args.growth_roe_min,
    }
    quality_thresholds = {
        "roe_gt": args.quality_roe_min,
        "debt_ratio_lt": args.quality_debt_ratio_max,
    }

    value_all = screen_value(rows, len(rows), value_pe_ceiling, args.value_pb_max, args.value_roe_min)
    growth_all = screen_growth(rows, len(rows), args.growth_revenue_yoy_min, args.growth_profit_yoy_min, args.growth_roe_min)
    quality_all = screen_quality(rows, len(rows), args.quality_roe_min, args.quality_debt_ratio_max)

    value_candidates = value_all[: args.top_n]
    growth_candidates = growth_all[: args.top_n]
    quality_candidates = quality_all[: args.top_n]

    strategy_enabled = {
        "value": args.strategy in ("value", "all"),
        "growth": args.strategy in ("growth", "all"),
        "quality": args.strategy in ("quality", "all"),
    }

    overlap_rows = build_overlap_candidates(value_all, growth_all, quality_all)
    selected_rows = []
    if strategy_enabled["value"]:
        selected_rows.extend(value_candidates)
    if strategy_enabled["growth"]:
        selected_rows.extend(growth_candidates)
    if strategy_enabled["quality"]:
        selected_rows.extend(quality_candidates)
    selected_sector_counts = Counter(item["sector"] for item in selected_rows if item.get("sector"))
    top_sector = selected_sector_counts.most_common(1)

    snapshot = {
        "report_date": args.report_date,
        "strategy": args.strategy,
        "top_n": args.top_n,
        "inputs": {
            "stock_pool": len(stock_pool_records),
            "instrument_meta": len(instrument_records),
            "latest_financials": len(latest_financials),
            "financials_history": len(financials_history),
            "roe": len(roe_records),
            "market_cap": len(market_cap_records),
            "pe_ratio": len(pe_records),
            "pb_ratio": len(pb_records),
        },
        "universe": {
            "stock_pool_size": len(stock_ids),
            "valid_financials": stats["valid_financials"],
            "valid_roe": stats["valid_roe"],
            "valid_pe": stats["valid_pe"],
            "valid_pb": stats["valid_pb"],
            "latest_quarters": counter_rows(stats["latest_quarters"], 8),
            "top_sectors": [{"name": sector, "count": count} for sector, count in stats["top_sectors"]],
            "median_metrics": {
                "pe": stats.get("pe_median"),
                "pb": stats.get("pb_median"),
                "roe": stats.get("roe_median"),
                "revenue_yoy": stats.get("revenue_yoy_median"),
                "profit_yoy": stats.get("profit_yoy_median"),
                "market_cap": stats.get("market_cap_median"),
            },
        },
        "strategies": {
            "value": strategy_snapshot("value", strategy_enabled["value"], value_all, value_candidates, value_thresholds),
            "growth": strategy_snapshot("growth", strategy_enabled["growth"], growth_all, growth_candidates, growth_thresholds),
            "quality": strategy_snapshot("quality", strategy_enabled["quality"], quality_all, quality_candidates, quality_thresholds),
        },
        "overlap": {
            "candidate_count": len(overlap_rows),
            "display_candidates": overlap_rows[:8],
            "sector_counts": counter_rows(Counter(item.get("sector") or "未分类" for item in overlap_rows), 5),
        },
        "risk_flags": {
            "missing_financial_count": stats["universe_size"] - stats["valid_financials"],
            "selected_count": len({item["order_book_id"] for item in selected_rows}),
            "top_selected_sector": {"name": top_sector[0][0], "count": top_sector[0][1]} if top_sector else None,
            "growth_pe_median": median_or_none(item.get("pe") for item in growth_candidates),
            "value_quality_overlap_count": len({item["order_book_id"] for item in value_candidates} & {item["order_book_id"] for item in quality_candidates}),
            "value_growth_overlap_count": len({item["order_book_id"] for item in value_candidates} & {item["order_book_id"] for item in growth_candidates}),
        },
        "summaries": {
            "exec_summary": "",
            "universe_overview": "",
            "metric_scoreboard": "",
            "value_section": "",
            "growth_section": "",
            "quality_section": "",
            "overlap_section": "",
            "risk_section": "",
            "appendix": "",
        },
    }

    output_path = Path(args.output).expanduser() if args.output else data_dir / "idea_screening_snapshot.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 结构化筛选快照已生成：{output_path}")


if __name__ == "__main__":
    main()
