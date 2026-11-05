import json

from app.services.community_kpis import fetch_market_asset_kpis, summarise_backtest_runs


def test_summary_reads_strategy_v2_annualized_return():
    summary = summarise_backtest_runs([
        {
            "id": 7,
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "result_json": json.dumps({
                "totalReturn": 0.94,
                "annualizedReturn": 12.07,
                "sharpeRatio": 1.79,
                "maxDrawdown": -1.62,
                "totalTrades": 8,
            }),
        }
    ])

    assert summary["total_return"] == 0.94
    assert summary["annual_return"] == 12.07


def test_summary_keeps_legacy_annual_return_fields_compatible():
    camel_case = summarise_backtest_runs([
        {"id": 1, "result_json": json.dumps({"annualReturn": 8.5})}
    ])
    snake_case = summarise_backtest_runs([
        {"id": 2, "result_json": json.dumps({"annual_return": 6.25})}
    ])

    assert camel_case["annual_return"] == 8.5
    assert snake_case["annual_return"] == 6.25


def test_summary_separates_profit_factor_from_payoff_ratio():
    summary = summarise_backtest_runs([
        {
            "id": 25,
            "result_json": json.dumps({
                "totalTrades": 89,
                "profitFactor": 1020.950291,
                "profitLossRatio": 11.601708,
                "winningTrades": 88,
                "losingTrades": 1,
            }),
        }
    ])

    assert summary["profit_factor"] == 1020.95
    assert summary["profit_loss_ratio"] == 11.6
    assert summary["winning_trades"] == 88
    assert summary["losing_trades"] == 1


def test_summary_derives_payoff_ratio_from_average_trade_fields():
    summary = summarise_backtest_runs([
        {
            "id": 26,
            "result_json": json.dumps({
                "totalTrades": 4,
                "avgWin": 6.0,
                "avgLoss": -2.0,
            }),
        }
    ])

    assert summary["profit_loss_ratio"] == 3.0


def test_marketplace_score_caps_extreme_returns_and_rewards_evidence():
    extreme_single_trade = summarise_backtest_runs([{
        "id": 31,
        "result_json": json.dumps({
            "totalTrades": 1,
            "totalReturn": 10000,
            "sharpeRatio": 10,
            "maxDrawdown": 0,
            "winRate": 100,
            "profitFactor": 100,
        }),
    }])
    robust_sample = summarise_backtest_runs([{
        "id": 32,
        "result_json": json.dumps({
            "totalTrades": 30,
            "totalReturn": 20,
            "sharpeRatio": 1.5,
            "maxDrawdown": -10,
            "winRate": 60,
            "profitFactor": 1.5,
        }),
    }])

    assert 0 < extreme_single_trade["score"] < 100
    assert robust_sample["score"] > extreme_single_trade["score"]
    assert robust_sample["score_version"] == 2


def test_marketplace_kpis_batch_backtest_queries_by_identity_type():
    source_rows = [
        {
            "id": 101,
            "source_id": 11,
            "strategy_id": None,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "result_json": json.dumps({"totalTrades": 3, "annualizedReturn": 12.5}),
        },
        {
            "id": 102,
            "source_id": 12,
            "strategy_id": None,
            "symbol": "ETH/USDT",
            "timeframe": "4h",
            "result_json": json.dumps({"totalTrades": 4, "annualizedReturn": 8.25}),
        },
    ]
    strategy_rows = [
        {
            "id": 103,
            "source_id": None,
            "strategy_id": 21,
            "symbol": "SOL/USDT",
            "timeframe": "15m",
            "result_json": json.dumps({"totalTrades": 5, "annualizedReturn": 6.75}),
        }
    ]

    class Cursor:
        def __init__(self):
            self.calls = []
            self.rows = []

        def execute(self, sql, params):
            self.calls.append((sql, params))
            self.rows = source_rows if "WHERE source_id IN" in sql else strategy_rows

        def fetchall(self):
            return self.rows

    cursor = Cursor()
    result = fetch_market_asset_kpis(cursor, [
        {"id": 1, "asset_type": "script_template", "source_script_source_id": 11},
        {"id": 2, "asset_type": "script_template", "source_script_source_id": 12},
        {"id": 3, "asset_type": "script_template", "source_strategy_id": 21},
        {"id": 4, "asset_type": "indicator"},
    ])

    assert len(cursor.calls) == 2
    assert cursor.calls[0][1] == (11, 12)
    assert cursor.calls[1][1] == (21,)
    assert result[1]["annual_return"] == 12.5
    assert result[2]["annual_return"] == 8.25
    assert result[3]["annual_return"] == 6.75
    assert result[4]["sample_size"] == 0


def test_marketplace_kpis_only_score_current_source_code_hash():
    class Cursor:
        def execute(self, sql, params):
            self.rows = [
                {
                    "id": 301,
                    "source_id": 77,
                    "strategy_id": None,
                    "code_hash": "old-hash",
                    "result_json": json.dumps({"totalTrades": 30, "totalReturn": 200}),
                },
                {
                    "id": 302,
                    "source_id": 77,
                    "strategy_id": None,
                    "code_hash": "current-hash",
                    "result_json": json.dumps({"totalTrades": 30, "totalReturn": 12}),
                },
            ]

        def fetchall(self):
            return self.rows

    result = fetch_market_asset_kpis(Cursor(), [{
        "id": 10,
        "asset_type": "script_template",
        "source_script_source_id": 77,
        "source_code_hash": "current-hash",
    }])

    assert result[10]["best_run_id"] == 302
    assert result[10]["total_return"] == 12


def test_marketplace_kpis_use_publication_snapshot_without_backtest_queries():
    class Cursor:
        def execute(self, sql, params):
            raise AssertionError("persisted marketplace KPIs must not query backtests")

    result = fetch_market_asset_kpis(Cursor(), [{
        "id": 8,
        "asset_type": "script_template",
        "source_script_source_id": 44,
        "marketplace_kpis": {
            "score_version": 2,
            "score": 82.5,
            "annual_return": 31.25,
            "sample_size": 2,
        },
    }])

    assert result[8]["score"] == 82.5
    assert result[8]["annual_return"] == 31.25
    assert result[8]["sample_size"] == 2


def test_marketplace_kpis_persist_missing_legacy_snapshot_once():
    run_rows = [{
        "id": 201,
        "source_id": 55,
        "strategy_id": None,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "result_json": json.dumps({"totalTrades": 3, "annualizedReturn": 9.5}),
    }]

    class Cursor:
        def __init__(self):
            self.updates = []

        def execute(self, sql, params):
            self.rows = run_rows

        def fetchall(self):
            return self.rows

        def executemany(self, sql, args):
            self.updates.extend(args)

    cursor = Cursor()
    result = fetch_market_asset_kpis(cursor, [{
        "id": 9,
        "asset_type": "script_template",
        "source_script_source_id": 55,
        "marketplace_kpis": None,
    }], persist_missing=True)

    assert result[9]["annual_return"] == 9.5
    assert len(cursor.updates) == 1
    assert cursor.updates[0][1] == 9
    assert json.loads(cursor.updates[0][0])["annual_return"] == 9.5
    assert json.loads(cursor.updates[0][0])["score_version"] == 2
