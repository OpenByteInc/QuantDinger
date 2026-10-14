import json
import re
from pathlib import Path

import pandas as pd

from app.services.strategy_v2 import StrategyV2BacktestRunner, compile_strategy_v2


SEED_PATH = Path(__file__).resolve().parents[1] / "migrations" / "strategy_v2_templates.sql"
ENTRY_PATTERN = re.compile(
    r"\('(?P<key>strategy_v2_[^']+)', "
    r"'(?P<asset_type>script|portfolio_strategy)', .*?, "
    r"\$(?P<tag>[a-z]+)\$(?P<code>.*?)\$(?P=tag)\$, "
    r"'(?P<schema>\{.*?\})'::jsonb",
    re.DOTALL,
)


def _seed_entries():
    sql = SEED_PATH.read_text(encoding="utf-8")
    return [match.groupdict() for match in ENTRY_PATTERN.finditer(sql)]


def test_strategy_v2_seed_has_explicit_cta_and_portfolio_catalogs():
    entries = _seed_entries()
    assert len(entries) == 12
    seed_sql = SEED_PATH.read_text(encoding="utf-8")
    assert seed_sql.count('"version":11') == 10
    assert seed_sql.count('"version":12') == 1
    assert seed_sql.count('"version":13') == 1
    assert sum(item["asset_type"] == "script" for item in entries) == 8
    assert sum(item["asset_type"] == "portfolio_strategy" for item in entries) == 4

    by_key = {item["key"]: item for item in entries}
    assert by_key["strategy_v2_supertrend"]["asset_type"] == "script"
    assert by_key["strategy_v2_market_cap_barbell"]["asset_type"] == "portfolio_strategy"


def test_strategy_v2_seed_templates_compile_and_expose_parameters():
    for item in _seed_entries():
        schema = json.loads(item["schema"])
        params = schema.get("params") or []
        assert params, item["key"]
        for param in params:
            assert f'# @param {param["name"]} ' in item["code"]
            assert f'context.params.get("{param["name"]}"' in item["code"]

        manifest = compile_strategy_v2(item["code"]).manifest
        expected_type = "portfolio" if item["asset_type"] == "portfolio_strategy" else "cta"
        assert manifest.strategy_type == expected_type


def test_strategy_v2_seed_templates_declare_current_direction_contract():
    for item in _seed_entries():
        manifest = compile_strategy_v2(item["code"]).manifest
        expected = "one_way" if item["key"] == "strategy_v2_double_ma" else "long_only"
        assert manifest.direction_mode == expected, item["key"]


def test_swap_seed_template_uses_signed_one_way_position():
    entry = next(
        item for item in _seed_entries()
        if item["key"] == "strategy_v2_double_ma"
    )

    assert 'get_position(g.symbol)' in entry["code"]
    assert "position_side=" not in entry["code"]
    assert "dual_ma_close_short" in entry["code"]
    assert "dual_ma_open_long" in entry["code"]


def test_swap_seed_templates_advertise_account_mode_compatibility():
    seed_sql = SEED_PATH.read_text(encoding="utf-8")

    assert seed_sql.count('"positionModeCompatibility":["one_way","hedge"]') == 2
    assert seed_sql.count('"account-mode-adaptive"') == 2
    assert '"single-position"' in seed_sql


def test_stateful_seed_templates_are_restart_safe():
    entries = {item["key"]: item["code"] for item in _seed_entries()}

    assert "position.avg_cost" in entries["strategy_v2_turtle"]
    assert "g.entry_price" not in entries["strategy_v2_turtle"]
    assert "PERSIST_RUNTIME_STATE = True" in entries["strategy_v2_supertrend"]


def test_macd_kdj_default_exposure_is_safe_without_user_enabled_leverage():
    entry = next(
        item for item in _seed_entries()
        if item["key"] == "strategy_v2_macd_kdj"
    )
    schema = json.loads(entry["schema"])
    target = next(
        param for param in schema["params"]
        if param["name"] == "target_pct"
    )

    assert target["default"] == 0.95
    assert '# @param target_pct float 0.95 ' in entry["code"]
    assert 'context.params.get("target_pct", 0.95)' in entry["code"]


def test_portfolio_templates_use_fixed_ten_symbol_universe():
    portfolios = [item for item in _seed_entries() if item["asset_type"] == "portfolio_strategy"]
    for item in portfolios:
        manifest = compile_strategy_v2(item["code"]).manifest
        assert manifest.universe.kind == "static"
        assert len(manifest.universe.instruments) == 10
        assert all(instrument.market == "USStock" for instrument in manifest.universe.instruments)
        assert "get_universe_stocks()" not in item["code"]


def _template_frame(rank: int, periods: int = 320) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=periods, freq="D")
    prices = []
    for offset in range(periods):
        trend = 80.0 + rank * 7.0 + offset * (0.08 + rank * 0.006)
        cycle = ((offset % 30) - 15) * (0.08 + rank * 0.003)
        prices.append(max(5.0, trend + cycle))
    return pd.DataFrame(
        {
            "open": [price * 0.998 for price in prices],
            "high": [price * 1.012 for price in prices],
            "low": [price * 0.988 for price in prices],
            "close": prices,
            "volume": [1_000_000.0 + rank * 50_000.0] * periods,
            "market_cap": [1_000_000_000.0 * (rank + 1)] * periods,
            "return_on_equity": [0.12 + rank * 0.01] * periods,
            "revenue_growth": [0.08 + rank * 0.008] * periods,
            "debt_to_equity": [0.4 + rank * 0.05] * periods,
        },
        index=index,
    )


def test_every_seed_template_completes_a_synthetic_v2_backtest():
    for item in _seed_entries():
        program = compile_strategy_v2(item["code"])
        frames = {
            instrument.key: _template_frame(index)
            for index, instrument in enumerate(program.manifest.universe.instruments)
        }
        frames.setdefault("USStock:SPY", _template_frame(11))

        result = StrategyV2BacktestRunner(
            code=item["code"],
            frames=frames,
            initial_capital=100_000,
            commission=0,
            slippage=0,
        ).run()

        assert result["engine"]["version"] == "quantdinger-strategy-api-v2", item["key"]
        assert result["manifest"]["apiVersion"] == 2, item["key"]
        assert result["equityCurve"], item["key"]
