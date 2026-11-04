import json
from types import SimpleNamespace

import pytest

from app.services.strategy_review import StrategyReviewService
from app.services.strategy_review_evidence import build_evidence, normalize_ai_review, source_context, holding_evidence


def trade(index, pnl=-1, **overrides):
    return {"id": index, "created_at": index * 60, "type": "close_long", "symbol": "BTC/USDT",
            "profit": pnl, "commission": 0.5, "commission_ccy": "USDT", **overrides}


def evidence(rows=None, net=-12, fees=10, context=None, strategy=None, positions=None, logs=None):
    return build_evidence(
        metrics={"window_net_pnl": net, "total_commission": fees, "max_consecutive_losses": 5},
        trades=rows if rows is not None else [trade(i) for i in range(1, 7)],
        strategy=strategy or {"symbol": "BTC/USDT"}, context=context or source_context("", {}),
        since_ts=1, now_ts=1000, log_levels=["INFO", "ERROR"], positions=positions, logs=logs,
    )


def test_cost_decomposition_does_not_call_fee_reduction_profitable():
    report = evidence(net=-371.34, fees=344.568904)
    assert report["facts"]["before_commission_pnl"] == pytest.approx(-26.771096)
    assert report["issues"][0]["code"] == "negative_edge"
    assert all(item["validation_status"] == "not_run" for item in report["issues"])


def test_positive_pre_fee_result_gets_different_hypothesis():
    assert evidence(net=-8)["issues"][0]["code"] == "cost_drag"


def test_quote_conversion_is_used_in_metrics():
    service = StrategyReviewService.__new__(StrategyReviewService)
    rows = [trade(1, profit_gross=3, commission=0.01, commission_ccy="BNB", commission_quote=2, value=100)]
    result = service._build_metrics(strategy={"initial_capital": 100}, trading_config={}, all_trades=rows,
                                    trades=rows, positions=[], lookback_days=30)
    assert result["total_commission"] == 2


@pytest.mark.parametrize("row", [trade(1, commission_ccy="BNB"), trade(1, commission=None), trade(1, commission_quote=float("nan"))])
def test_unconverted_fees_block_cost_conclusions(row):
    report = evidence(rows=[row])
    assert report["facts"]["before_commission_pnl"] is None
    assert report["issues"][0]["code"] == "fee_data"
    assert "fee_conversion" in report["coverage"]["gaps"]


def test_unspecified_currency_is_disclosed_not_silently_verified():
    report = evidence(rows=[trade(1, commission_ccy=None)])
    assert "fee_currency_assumed" in report["coverage"]["gaps"]


def test_product_symbol_quote_is_recognized():
    report = evidence(rows=[trade(1, symbol="Crypto:BTC/USDT@swap")])
    assert report["coverage"]["unconverted_fees"] == 0


def test_profit_factor_and_payoff_are_different():
    report = evidence(rows=[trade(1, 6), trade(2, -2), trade(3, -2)])
    assert report["facts"]["profit_factor"] == 1.5
    assert report["facts"]["payoff_ratio"] == 3


def test_no_losses_is_not_currency_valued_profit_factor():
    report = evidence(rows=[trade(1, 20)], net=20)
    assert report["facts"]["profit_factor"] is None
    assert report["facts"]["profit_factor_state"] == "no_losses"
    assert report["facts"]["payoff_ratio"] is None


def test_empty_report_does_not_claim_success_or_zero_cost_performance():
    report = evidence(rows=[], net=0, fees=0)
    assert report["verdict"] == "insufficient_data"
    assert report["facts"]["before_commission_pnl"] is None
    assert report["facts"]["avg_net_per_close"] is None


def test_code_is_not_executed_and_strings_and_secrets_are_redacted():
    code = 'raise RuntimeError("DO_NOT_EXECUTE")\npassword = "private-value"\ndef handle_data(context, data):\n    fast_ma = data.tail(20).mean()\n    slow_ma = data.tail(60).mean()\n'
    context = source_context(code, {"fast_period": 20, "api_key": "private-value", "password": 1234, "url": "private"}, 12)
    assert context["available"]
    assert "DO_NOT_EXECUTE" not in context["outline"]
    assert "private-value" not in json.dumps(context)
    assert context["parameters"] == {"fast_period": 20}
    assert context["moving_average_calls"]
    assert context["version_id"] == 12
    report = evidence(context=context)
    assert "outline" not in report["source"]
    assert report["issues"][1]["experiment"] == "ma_filter"


def test_unknown_strategy_does_not_get_ma_or_grid_specific_plan():
    report = evidence()
    assert report["issues"][1]["experiment"] == "entry_filter"
    assert "grid" not in json.dumps(report)


def test_missing_or_invalid_source_is_explicit():
    context = source_context("not valid python !", {})
    assert not context["available"]
    assert "source" in evidence(context=context)["coverage"]["gaps"]


def test_trade_samples_are_bounded_deduplicated_and_do_not_include_free_text():
    report = evidence(rows=[trade(i, -i, action_note="SECRET", raw="SECRET") for i in range(1, 101)])
    samples = report["trade_samples"]
    assert len(samples) <= 30
    assert len({row["id"] for row in samples}) == len(samples)
    assert len(report["trade_ledger"]) == 100
    assert report["coverage"]["trade_ledger_complete"] is True
    assert "SECRET" not in json.dumps(report)


def test_complete_ledger_positions_and_redacted_logs_reach_evidence():
    rows = [trade(i, close_reason="signal_close") for i in range(1, 7)]
    positions = [{"id": 8, "symbol": "BTC/USDT", "side": "long", "size": 0.2,
                  "entry_price": 100, "current_price": 101, "unrealized_pnl": 0.2}]
    logs = [{"id": 9, "level": "INFO", "message": "api_key=private-value signal close confirmed",
             "created_at": 900}]
    report = evidence(rows=rows, positions=positions, logs=logs)
    coverage = report["coverage"]
    assert coverage["trade_rows_total"] == coverage["trade_rows_included"] == 6
    assert coverage["exit_reason_rows"] == coverage["closed_rows_total"] == 6
    assert coverage["exit_reason_recorded_rows"] == 6
    assert coverage["exit_reason_inferred_rows"] == 0
    assert coverage["current_position_rows"] == 1
    assert coverage["log_rows_total"] == coverage["log_rows_included"] == 1
    assert report["current_positions"][0]["symbol"] == "BTC/USDT"
    assert "signal close confirmed" in report["log_events"][0]["message"]
    assert "private-value" not in json.dumps(report)


def test_evidence_gaps_are_conditional_and_describe_actual_scope():
    empty = evidence(rows=[], net=0, fees=0)
    assert not {"market_path", "historical_positions", "version_continuity", "funding", "execution_cost"} & set(empty["coverage"]["gaps"])

    spot_signal = evidence(rows=[trade(1)], strategy={"symbol": "BTC/USDT", "execution_mode": "signal"})
    assert {"market_path", "historical_positions", "version_continuity"} <= set(spot_signal["coverage"]["gaps"])
    assert "exit_reason" not in spot_signal["coverage"]["gaps"]
    assert spot_signal["coverage"]["exit_reason_inferred_rows"] == 1
    assert not {"funding", "execution_cost", "fill_not_roundtrip"} & set(spot_signal["coverage"]["gaps"])

    derivative_live = evidence(rows=[trade(1)], strategy={
        "symbol": "BTC/USDT", "execution_mode": "live", "trading_config": {"market_type": "swap"},
    })
    assert {"funding", "execution_cost"} <= set(derivative_live["coverage"]["gaps"])


def test_large_trade_ledger_discloses_truncation():
    report = evidence(rows=[trade(i) for i in range(1, 402)])
    assert len(report["trade_ledger"]) == 400
    assert report["coverage"]["trade_ledger_complete"] is False
    assert "trade_ledger_truncated" in report["coverage"]["gaps"]


def test_ai_cannot_introduce_unknown_issues_or_claim_test_execution():
    item = {"code": "cost_check", "interpretation": "x", "proposed_change": "x", "validation_plan": "x", "tradeoff": "x", "status": "passed", "return_pct": 400}
    result = normalize_ai_review({"summary": "Review", "issues": [item, item, {**item, "code": "invented"}]}, evidence())
    assert len(result["issues"]) == 1
    assert "status" not in result["issues"][0]
    assert "return_pct" not in result["issues"][0]


@pytest.mark.parametrize("value", [None, {}, {"summary": "x", "issues": []}, {"summary": "x", "issues": [{"code": "negative_edge"}]}])
def test_invalid_ai_output_is_rejected_for_refundable_fallback(value):
    assert normalize_ai_review(value, evidence()) is None


def test_ai_receives_structured_evidence_and_not_legacy_samples(monkeypatch):
    captured = {}
    class FakeLLM:
        provider = SimpleNamespace(value="test")
        def get_default_model(self, provider): return "test"
        def get_api_key(self, provider): return True
        def safe_call_llm(self, **kwargs):
            captured.update(kwargs)
            return {"summary": "Review", "issues": [{"code": "cost_check", "interpretation": "x", "proposed_change": "x", "validation_plan": "x", "tradeoff": "x"}]}
    monkeypatch.setattr("app.services.strategy_review.LLMService", FakeLLM)
    service = StrategyReviewService.__new__(StrategyReviewService)
    report = evidence(
        rows=[trade(1, close_reason="signal_close")],
        positions=[{"id": 2, "symbol": "BTC/USDT", "side": "long", "size": 1}],
        logs=[{"id": 3, "level": "INFO", "message": "password=private-value order settled"}],
    )
    result = service._build_ai_review(base_report={"evidence": report, "source_context": source_context("x = 1", {}), "samples": {"recent_logs": ["LEGACY_SECRET"]}}, language="zh-CN")
    assert result["status"] == "ok"
    assert "LEGACY_SECRET" not in captured["user_prompt"]
    assert "private-value" not in captured["user_prompt"]
    assert "order settled" in captured["user_prompt"]
    assert '"trade_ledger"' in captured["user_prompt"]
    assert '"current_positions"' in captured["user_prompt"]
    assert "x = 1" in captured["user_prompt"]


def test_source_lookup_is_scoped_to_owner_and_pinned_version(monkeypatch):
    calls = []
    class Sources:
        def get_version(self, version_id, user_id):
            calls.append((version_id, user_id))
            return {"code": "x = 1"}
    monkeypatch.setattr("app.services.script_source.get_script_source_service", lambda: Sources())
    service = StrategyReviewService.__new__(StrategyReviewService)
    context = service._review_source_context({"source_version_id": 42, "trading_config": {"params": {"fast": 20}}}, 9)
    assert calls == [(42, 9)]
    assert context["available"]


def test_fifo_holding_uses_pre_window_entries_and_partial_quantities():
    rows = [trade(1, None, type="open_long", amount=2, created_at=10),
            trade(2, -1, amount=1, created_at=110), trade(3, -1, amount=1, created_at=210)]
    result = holding_evidence(rows, 100, 300, "1m")
    assert result["median_holding_seconds"] == 150
    assert result["matched_close_count"] == 2
    assert result["short_hold_threshold_seconds"] == 300
    assert result["short_hold_count"] == 2
    assert result["short_hold_net_pnl"] == -2


def test_holding_does_not_mix_symbols_or_position_sides():
    rows = [trade(1, None, type="open_long", amount=1), trade(2, -1, type="close_short", amount=1),
            trade(3, -1, symbol="ETH/USDT", amount=1)]
    result = holding_evidence(rows, 1, 500, "1m")
    assert result["median_holding_seconds"] is None
    assert result["unmatched_close_count"] == 2


def test_current_source_period_is_extracted_without_executing_it():
    context = source_context('context.subscribe(frequency="1m")', {})
    assert context["signal_timeframe"] == "1m"


def test_mixed_quote_ledgers_do_not_get_a_cost_waterfall():
    result = evidence(rows=[trade(1), trade(2, symbol="ETH/BTC", commission_ccy="BTC")])
    assert result["facts"]["before_commission_pnl"] is None
    assert "mixed_quote" in result["coverage"]["gaps"]


def test_invalid_ai_error_does_not_escape_to_user(monkeypatch):
    def broken():
        raise RuntimeError("private-credential-should-not-be-returned")
    monkeypatch.setattr("app.services.strategy_review.LLMService", broken)
    service = StrategyReviewService.__new__(StrategyReviewService)
    result = service._build_ai_review(base_report={"evidence": evidence()}, language="en-US")
    assert result["status"] == "fallback"
    assert result["error_code"] == "unavailable"
    assert "private-credential" not in json.dumps(result)


def test_malformed_ai_issue_code_is_ignored():
    assert normalize_ai_review({"summary": "Review", "issues": [{"code": []}]}, evidence()) is None


def test_numeric_string_pnls_are_sorted_numerically_in_evidence():
    report = evidence(rows=[trade(1, "-2"), trade(2, "-10")])
    assert report["trade_samples"][0]["id"] == 2


def test_exit_cohorts_are_exclusive_and_custom_reasons_remain_distinct():
    result = evidence(rows=[trade(1, -4, close_reason="server_stop_loss"), trade(2, 3, close_reason="take_profit"),
                            trade(3, -2, close_reason="custom_exit"), trade(4, None, close_reason="server_stop_loss")])
    groups = {group["code"]: group for group in result["exit_groups"]}
    assert sum(group["count"] for group in groups.values()) == 4
    assert groups["stop_loss"]["count"] == 2
    assert groups["stop_loss"]["pnl_count"] == 1
    assert groups["stop_loss"]["net_pnl"] == -4
    assert groups["other"]["count"] == 1
    assert groups["unknown"]["count"] == 0
    assert result["facts"]["exit_reason_coverage_pct"] == 100
    assert result["coverage"]["exit_reason_recorded_rows"] == 4
    assert next(c for c in result["checks"] if c["code"] == "exits_check")["status"] == "review"


def test_legacy_close_actions_are_classified_without_claiming_protective_triggers():
    result = evidence(rows=[trade(i) for i in range(1, 100)])
    checks = {check["code"]: check for check in result["checks"]}
    assert checks["frequency_check"]["status"] == "insufficient"
    assert checks["exits_check"]["finding"] == "exits_signal_only"
    assert checks["exits_check"]["status"] == "review"
    assert result["coverage"]["exit_reason_inferred_rows"] == 99
    assert result["coverage"]["exit_reason_recorded_rows"] == 0
    assert result["trade_ledger"][0]["review_close_reason"] == "signal_trigger"
    assert result["trade_ledger"][0]["review_close_reason_source"] == "inferred"
    assert checks["risk_check"]["finding"] == "risk_unknown"


def test_unconverted_costs_hide_exit_group_money_and_position_loss():
    result = evidence(rows=[trade(1, -5, close_reason="server_stop_loss", commission_ccy="BNB")])
    assert next(g for g in result["exit_groups"] if g["code"] == "stop_loss")["net_pnl"] is None
    assert result["facts"]["worst_close_loss"] is None


def test_current_leverage_is_not_effective_exposure_and_liquidation_is_prioritized():
    result = build_evidence(metrics={"window_net_pnl": -10, "total_commission": 1},
        trades=[trade(1, -10, close_reason="exchange_liquidation")],
        strategy={"initial_capital": 1000, "trading_config": {"leverage": 50, "leverage_enabled": False, "stop_loss_pct": 0.01}},
        context=source_context("", {}), since_ts=0, now_ts=86400, log_levels=[])
    assert result["facts"]["configured_leverage"] == 1
    assert result["facts"]["worst_close_initial_pct"] == 1
    assert result["checks"][0]["code"] == "risk_check"
    assert result["current_settings"] == [{"key": "stop_loss_pct", "value": 0.01, "origin": "deployment"}]


def test_daily_rates_use_full_window_not_observed_active_days():
    result = build_evidence(metrics={}, trades=[trade(1), trade(2)], strategy={}, context={},
                            since_ts=0, now_ts=2 * 86400, log_levels=[])
    assert result["facts"]["events_per_day"] == 1
    assert result["facts"]["configured_leverage"] is None


def test_old_evidence_still_accepts_old_issue_annotations():
    result = evidence()
    result.pop("checks")
    item = {"code": "negative_edge", "interpretation": "x", "proposed_change": "x", "validation_plan": "x", "tradeoff": "x"}
    assert normalize_ai_review({"summary": "Legacy", "issues": [item]}, result)["issues"][0]["code"] == "negative_edge"


def test_qa_response_needs_no_validation_plan_and_ignores_fake_execution_fields():
    item = {"code": "cost_check", "interpretation": "Net result is negative", "proposed_change": "Keep size fixed", "tradeoff": "Retain protection", "validation_plan": "invented", "passed": True}
    result = normalize_ai_review({"summary": "Review", "issues": [item]}, evidence())
    assert result is not None
    assert "validation_plan" not in result["issues"][0]
    assert "passed" not in result["issues"][0]


def test_holding_chart_uses_full_matched_population_and_exact_bucket_boundaries():
    rows = []
    for i, (duration, pnl) in enumerate([(300, -1), (301, -2), (1800, -3), (1801, 4), (7200, 5), (7201, 6)]):
        rows.extend([trade(i * 2 + 1, None, type="open_long", amount=1, symbol=str(i), created_at=1),
                     trade(i * 2 + 2, pnl, amount=1, symbol=str(i), created_at=duration + 1)])
    result = holding_evidence(rows, 0, 10000, "1m")
    assert [g["count"] for g in result["holding_groups"]] == [1, 2, 2, 1]
    assert [g["net_pnl"] for g in result["holding_groups"]] == [-1, -5, 9, 6]


def test_unmatched_holds_and_missing_pnl_are_not_fabricated_as_chart_zeroes():
    rows = [trade(1, None, type="open_long", amount=1, created_at=1),
            trade(2, None, amount=1, created_at=10), trade(3, -10, amount=1, created_at=20)]
    result = holding_evidence(rows, 0, 100, "1m")
    assert result["unmatched_close_count"] == 1
    assert result["holding_groups"][0]["count"] == 1
    assert result["holding_groups"][0]["pnl_count"] == 0
    assert result["holding_groups"][0]["net_pnl"] is None


def test_unconverted_fees_withhold_holding_chart_pnl():
    rows = [trade(1, None, type="open_long", amount=1), trade(2, -2, amount=1, commission_ccy="BNB")]
    result = evidence(rows=rows)
    assert all(g["net_pnl"] is None for g in result["holding_groups"])
