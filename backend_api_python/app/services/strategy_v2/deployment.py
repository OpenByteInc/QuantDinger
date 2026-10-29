"""Persistence boundary for Strategy API V2 deployments."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services.script_source import get_script_source_service
from app.services.live_trading.capabilities import (
    supported_crypto_exchange_ids,
    supports_equity_product,
)
from app.services.market.instrument_products import PRODUCT_CRYPTO
from app.services.market.product_catalog import get_catalog_product
from app.services.strategy_direction import (
    direction_mode_position_side,
    normalize_direction_mode,
)
from app.utils.db import get_db_connection
from app.utils.trading_venue_visibility import is_trading_venue_enabled

from .contract import PLATFORM_MAX_LEVERAGE, StrategyV2ContractError, compile_strategy_v2


class StrategyV2DeploymentService:
    def save(self, *, user_id: int, payload: dict[str, Any], strategy_id: int | None = None) -> int:
        source_id = int(payload.get("sourceId") or 0)
        source_service = get_script_source_service()
        source = source_service.get_source(source_id, user_id=user_id) if source_id else None
        if not source:
            raise StrategyV2ContractError("strategyV2.sourceNotFound")
        source_version = source_service.get_latest_version(source_id, user_id=user_id)
        if not source_version:
            raise StrategyV2ContractError("strategyV2.sourceVersionRequired")
        source_version_id = int(source_version.get("id") or 0)
        source = {
            **source,
            **source_version,
            "id": source_id,
            "source_version_id": source_version_id,
        }
        program = compile_strategy_v2(str(source.get("code") or ""))
        manifest = program.manifest
        source_metadata = self._object(source.get("metadata"))
        adaptation = self._object(source_metadata.get("marketplace_adaptation"))
        if adaptation.get("requires_backtest") and not self._has_current_version_backtest(
            user_id=int(user_id),
            source_id=source_id,
            code_hash=str(manifest.code_hash or ""),
        ):
            raise StrategyV2ContractError("strategyV2.backtestRequiredForAdaptedStrategy")
        name = str(payload.get("name") or source.get("name") or "").strip()
        if not name:
            raise StrategyV2ContractError("strategyV2.nameRequired")
        initial_capital = float(payload.get("initialCapital") or 0)
        if initial_capital <= 0:
            raise StrategyV2ContractError("strategyV2.invalidInitialCapital")

        risk_supplied = any(
            key in payload
            for key in ("equityTakeProfitPct", "equityStopLossPct")
        )
        try:
            equity_take_profit_pct = float(payload.get("equityTakeProfitPct") or 0)
            equity_stop_loss_pct = float(payload.get("equityStopLossPct") or 0)
        except (TypeError, ValueError) as exc:
            raise StrategyV2ContractError("strategyV2.invalidEquityRiskLimit") from exc
        if not 0 <= equity_stop_loss_pct <= 1 or not 0 <= equity_take_profit_pct <= 10:
            raise StrategyV2ContractError("strategyV2.invalidEquityRiskLimit")

        execution_mode = str(payload.get("executionMode") or "signal").strip().lower()
        if execution_mode not in {"signal", "live"}:
            raise StrategyV2ContractError("strategyV2.invalidExecutionMode")
        credential_id = int(payload.get("credentialId") or 0)
        exchange_id = self._credential_exchange(user_id, credential_id) if execution_mode == "live" else ""
        if execution_mode == "live" and not exchange_id:
            raise StrategyV2ContractError("strategyV2.credentialRequired")
        self._validate_execution_account(manifest.markets, exchange_id, execution_mode)
        live_instruments = self._resolve_live_instruments(
            user_id=user_id,
            manifest=manifest,
            execution_mode=execution_mode,
        )
        instrument_products = self._validate_manifest_products(
            manifest.metadata(), exchange_id, execution_mode,
            instruments=live_instruments,
        )
        if execution_mode == "live" and any(
            item.get("exchange_id") == "gate" and item.get("api_family") == "stock"
            for item in instrument_products
        ):
            from app.services.exchange_execution import resolve_exchange_config
            from app.services.market.product_catalog import validate_product_account_environment

            account_config = resolve_exchange_config(
                {"credential_id": credential_id, "exchange_id": exchange_id},
                user_id=user_id,
            )
            try:
                validate_product_account_environment(instrument_products, account_config)
            except ValueError as exc:
                raise StrategyV2ContractError(str(exc)) from exc
        quote_currency = self._validate_live_quote_currency(
            markets=manifest.markets,
            instruments=live_instruments or (
                (manifest.metadata().get("universe") or {}).get("instruments") or []
            ),
            products=instrument_products,
            execution_mode=execution_mode,
        )

        leverage_enabled = bool(payload.get("leverageEnabled"))
        leverage = float(payload.get("leverage") or 1)
        if leverage_enabled and not self._supports_contract_leverage(manifest.metadata()):
            raise StrategyV2ContractError("strategyV2.leverageCryptoSwapOnly")
        if leverage_enabled and not manifest.leverage_allowed:
            raise StrategyV2ContractError("strategyV2.leverageNotAllowed")
        if leverage_enabled and leverage > PLATFORM_MAX_LEVERAGE:
            raise StrategyV2ContractError("strategyV2.leverageExceedsStrategyLimit")
        leverage = max(1.0, leverage if leverage_enabled else 1.0)
        declared_payload_direction = payload.get("directionMode") or payload.get("direction_mode") or ""
        requested_direction = declared_payload_direction or (
            payload.get("positionSide") or payload.get("position_side") or ""
        )
        requested_direction_mode = normalize_direction_mode(requested_direction)
        if str(requested_direction or "").strip() and not requested_direction_mode:
            raise StrategyV2ContractError("strategyV2.directionModeInvalid")
        if (
            manifest.direction_mode
            and str(declared_payload_direction or "").strip()
            and requested_direction_mode
            and requested_direction_mode != manifest.direction_mode
        ):
            raise StrategyV2ContractError("strategyV2.directionModeMismatch")
        direction_mode = manifest.direction_mode or requested_direction_mode
        manifest_market_type = self._manifest_market_type(manifest.metadata())
        if execution_mode == "live" and manifest_market_type == "swap" and not direction_mode:
            raise StrategyV2ContractError("strategyV2.directionModeRequired")
        position_side = direction_mode_position_side(direction_mode)
        notification_config = {
            "channels": list(payload.get("notificationChannels") or []),
            "targets": payload.get("notificationTargets") or {},
        }
        source_runtime = self._object(source_metadata.get("last_run_config"))
        # Sources generated by older robot builders only stored the executor
        # contract at the metadata root.  Recover it so an existing grid does
        # not silently fall back to the generic Strategy V2 bar loop after it
        # is edited or redeployed.
        legacy_executor_type = str(source_metadata.get("executor_type") or "").strip().lower()
        legacy_executor_config = self._object(source_metadata.get("executor_config"))
        if legacy_executor_type in {
            "grid",
            "dca",
            "martingale",
            "layered_martingale",
        }:
            legacy_runtime = {
                "strategy_family": "robot",
                "executor_type": legacy_executor_type,
                "executor_config": legacy_executor_config,
                "executor_preview": self._object(source_metadata.get("executor_preview")),
                "entry_trigger_mode": (
                    "exchange_resting_orders"
                    if legacy_executor_type == "grid"
                    else "realtime_price"
                    if legacy_executor_type in {"martingale", "layered_martingale"}
                    else "schedule"
                ),
            }
            if legacy_executor_type == "grid":
                count = max(2, int(legacy_executor_config.get("grid_count") or 2))
                total_amount = max(
                    0.0,
                    float(legacy_executor_config.get("total_amount_quote") or 0.0),
                )
                direction = str(legacy_executor_config.get("side") or "long").strip().lower()
                legacy_runtime.update({
                    "bot_type": "grid",
                    "bot_params": {
                        "upperPrice": float(legacy_executor_config.get("end_price") or 0.0),
                        "lowerPrice": float(legacy_executor_config.get("start_price") or 0.0),
                        "gridCount": count,
                        "gridCountUnit": "cells",
                        "amountPerGrid": total_amount / count,
                        "amountPerGridPct": 1.0 / count,
                        "gridMode": str(legacy_executor_config.get("grid_mode") or "arithmetic"),
                        "gridDirection": direction,
                        "initialPositionPct": (
                            0.0
                            if direction == "neutral"
                            else float(legacy_executor_config.get("initial_position_pct") or 0.0)
                        ),
                        "orderMode": "maker",
                        "boundaryAction": "pause",
                        "maxOpenOrders": int(legacy_executor_config.get("max_open_orders") or 4),
                        "minSpreadBetweenOrders": float(
                            legacy_executor_config.get("min_spread_between_orders") or 0.0
                        ),
                        "orderFrequency": int(legacy_executor_config.get("order_frequency") or 0),
                        "dynamicAnchor": bool(legacy_executor_config.get("dynamic_anchor")),
                    },
                })
            source_runtime = {**legacy_runtime, **source_runtime}
        generated_runtime = payload.get("strategyRuntimeConfig") or payload.get("strategy_runtime_config") or {}
        if not isinstance(generated_runtime, dict):
            raise StrategyV2ContractError("strategyV2.runtimeConfigInvalid")
        allowed_runtime_keys = {
            "strategy_family",
            "executor_type",
            "executor_config",
            "executor_preview",
            "bot_type",
            "bot_params",
            "symbol",
            "market_type",
            "margin_mode",
            "stop_loss_pct",
            "take_profit_pct",
            "entry_trigger_mode",
            "risk_tick_seconds",
            "price_stale_after_seconds",
            "equity_take_profit_pct",
            "equity_stop_loss_pct",
            "equity_trailing_enabled",
            "equity_trailing_activation_pct",
            "equity_trailing_callback_pct",
        }
        # A visual robot is saved as a normal Strategy API V2 source before the
        # live wizard opens.  The wizard only sends the source id and runtime
        # account settings, so recover the whitelisted robot/executor contract
        # from the source metadata.  Without this merge a grid source silently
        # falls back to the generic bar-driven script runtime instead of the
        # durable resting-order GridEngine.
        runtime_config = {
            key: value
            for key, value in source_runtime.items()
            if key in allowed_runtime_keys
        }
        runtime_config.update({
            key: value
            for key, value in generated_runtime.items()
            if key in allowed_runtime_keys
        })
        manifest_metadata = manifest.metadata()
        manifest_market_type = self._manifest_market_type(manifest_metadata)
        symbol = self._manifest_symbol(manifest_metadata)
        declared_family = str(
            self._object(manifest_metadata.get("metadata")).get("strategy_family") or ""
        ).strip().lower().replace("-", "_")
        if declared_family and declared_family not in {
            "robot",
            "grid",
            "dca",
            "martingale",
            "layered_martingale",
        }:
            for stale_key in (
                "executor_type",
                "executor_config",
                "executor_preview",
                "bot_type",
                "bot_params",
            ):
                runtime_config.pop(stale_key, None)
            runtime_config["strategy_family"] = declared_family
        # Source metadata also contains the IDE's last run configuration.  That
        # configuration may still carry the editor defaults (Crypto/BTC/USDT)
        # even when the compiled source contract declares another instrument
        # such as USStock:SPY.  A deployment must always follow the immutable
        # compiled contract; otherwise the UI and parts of the runtime can
        # observe different instruments for the same strategy.
        runtime_config["symbol"] = symbol
        runtime_config["market_type"] = manifest_market_type
        # Older visual-builder sources persisted executor_type but omitted
        # bot_type. Canonicalize it before grid budget/ledger setup so a grid
        # can never silently deploy as a generic bar-driven strategy.
        from app.services.strategy_runtime.bot_type import resolve_bot_type

        resolved_bot_type = resolve_bot_type(source, runtime_config)
        if resolved_bot_type:
            runtime_config["bot_type"] = resolved_bot_type
        self._validate_bot_product_compatibility(
            bot_type=str(runtime_config.get("bot_type") or ""),
            instrument_products=instrument_products,
        )
        self._normalize_grid_runtime_budget(runtime_config)
        if str(runtime_config.get("bot_type") or "").strip().lower() == "grid":
            runtime_config["position_ledger"] = "fills"
        if risk_supplied:
            runtime_config.update({
                "equity_take_profit_pct": equity_take_profit_pct,
                "equity_stop_loss_pct": equity_stop_loss_pct,
                "deployment_risk": {
                    "basis": "initial_capital",
                    "action": "close_and_stop",
                    "equity_take_profit_pct": equity_take_profit_pct,
                    "equity_stop_loss_pct": equity_stop_loss_pct,
                },
            })
        runtime_config.update({
            "api_version": 2,
            "script_source_id": source_id,
            "script_source_version_id": source_version_id,
            "strategy_manifest": manifest_metadata,
            "initial_capital": initial_capital,
            "leverage_enabled": leverage_enabled,
            "leverage": leverage,
            "params": dict(payload.get("params") or {}),
            "credential_id": credential_id or None,
            "exchange_id": exchange_id,
            "direction_mode": direction_mode,
            "position_side": position_side,
            "instrument_products": instrument_products,
            "quote_currency": quote_currency,
            "ai_decision_filter": bool(payload.get("aiDecisionFilter")),
        })
        market_category = manifest.markets[0] if len(manifest.markets) == 1 else "Mixed"
        exchange_config = {"credential_id": credential_id, "exchange_id": exchange_id} if credential_id else {}

        with get_db_connection() as db:
            cur = db.cursor()
            values = (
                name,
                market_category,
                execution_mode,
                json.dumps(notification_config, ensure_ascii=False),
                symbol,
                manifest.driving_frequency,
                initial_capital,
                int(leverage),
                manifest_market_type,
                json.dumps(exchange_config, ensure_ascii=False),
                json.dumps(runtime_config, ensure_ascii=False),
                source_version_id,
            )
            if strategy_id:
                cur.execute(
                    """
                    UPDATE qd_strategies_trading
                    SET strategy_name = ?, market_category = ?, execution_mode = ?, notification_config = ?,
                        symbol = ?, timeframe = ?, initial_capital = ?, leverage = ?, market_type = ?,
                        exchange_config = ?, trading_config = ?, source_version_id = ?, strategy_type = 'StrategyV2',
                        updated_at = NOW()
                    WHERE id = ? AND user_id = ?
                    """,
                    (*values, int(strategy_id), int(user_id)),
                )
                if not cur.rowcount:
                    raise StrategyV2ContractError("strategyV2.strategyNotFound")
                deployment_id = int(strategy_id)
            else:
                cur.execute(
                    """
                    INSERT INTO qd_strategies_trading
                      (user_id, strategy_name, strategy_type, market_category, execution_mode,
                       notification_config, status, symbol, timeframe, initial_capital, leverage,
                       market_type, exchange_config, trading_config, source_version_id, created_at, updated_at)
                    VALUES (?, ?, 'StrategyV2', ?, ?, ?, 'stopped', ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
                    """,
                    (int(user_id), *values),
                )
                deployment_id = int(cur.lastrowid or 0)
            db.commit()
            cur.close()
        return deployment_id

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            return dict(parsed) if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def _normalize_grid_runtime_budget(cls, runtime_config: dict[str, Any]) -> None:
        if str(runtime_config.get("bot_type") or "").strip().lower() != "grid":
            return
        bot_params = cls._object(runtime_config.get("bot_params"))
        executor_config = cls._object(runtime_config.get("executor_config"))
        try:
            count = max(
                1,
                int(
                    bot_params.get("gridCount")
                    or bot_params.get("grid_count")
                    or executor_config.get("grid_count")
                    or 1
                ),
            )
        except (TypeError, ValueError):
            count = 1
        if not (
            bot_params.get("amountPerGridPct")
            or bot_params.get("amount_per_grid_pct")
        ):
            bot_params["amountPerGridPct"] = 1.0 / float(count)
        runtime_config["bot_params"] = bot_params

    @staticmethod
    def _credential_exchange(user_id: int, credential_id: int) -> str:
        if not credential_id:
            return ""
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT exchange_id FROM qd_exchange_credentials WHERE id = ? AND user_id = ?",
                (credential_id, int(user_id)),
            )
            row = cur.fetchone() or {}
            cur.close()
        return str(row.get("exchange_id") or "").strip().lower()

    @staticmethod
    def _has_current_version_backtest(*, user_id: int, source_id: int, code_hash: str) -> bool:
        if not source_id or not code_hash:
            return False
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id FROM qd_backtest_runs
                WHERE user_id = ? AND source_id = ? AND code_hash = ? AND status = 'success'
                ORDER BY id DESC LIMIT 1
                """,
                (int(user_id), int(source_id), str(code_hash)),
            )
            found = cur.fetchone() is not None
            cur.close()
        return found

    @staticmethod
    def _validate_execution_account(markets: tuple[str, ...], exchange_id: str, execution_mode: str) -> None:
        if execution_mode != "live":
            return
        market_set = set(markets)
        if len(market_set) != 1:
            raise StrategyV2ContractError("strategyV2.mixedMarketLiveUnsupported")
        market = next(iter(market_set), "")
        if market == "Crypto" and exchange_id not in supported_crypto_exchange_ids():
            raise StrategyV2ContractError("strategyV2.cryptoCredentialRequired")
        if market == "USStock" and exchange_id not in {"alpaca", "ibkr", "tiger", "webull"}:
            raise StrategyV2ContractError("strategyV2.stockCredentialRequired")
        if market == "HKStock" and exchange_id != "tiger":
            raise StrategyV2ContractError("strategyV2.stockCredentialRequired")
        if market not in {"Crypto", "USStock", "HKStock"}:
            raise StrategyV2ContractError("strategyV2.liveMarketUnsupported")
        if not is_trading_venue_enabled(exchange_id):
            raise StrategyV2ContractError("strategyV2.tradingVenueDisabled")

    @staticmethod
    def _validate_manifest_products(
        manifest: dict[str, Any], exchange_id: str, execution_mode: str,
        *, instruments: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if execution_mode != "live":
            return []
        instruments = instruments if instruments is not None else (
            (manifest.get("universe") or {}).get("instruments") or []
        )
        products: list[dict[str, Any]] = []
        for item in instruments:
            if str(item.get("market") or "") != "Crypto":
                continue
            declared_exchange = str(item.get("exchange_id") or "").strip().lower()
            if declared_exchange and declared_exchange != exchange_id:
                raise StrategyV2ContractError("strategyV2.instrumentVenueMismatch")
            venue = declared_exchange or exchange_id
            market_type = str(item.get("market_type") or "spot").strip().lower()
            symbol = str(item.get("symbol") or "").strip().upper()
            product = get_catalog_product(
                market="Crypto",
                symbol=symbol,
                exchange_id=venue,
                market_type=market_type,
            )
            if not product:
                raise StrategyV2ContractError("strategyV2.instrumentCatalogMissing")
            product_type = str(product.get("product_type") or PRODUCT_CRYPTO).strip().lower()
            if product_type != PRODUCT_CRYPTO:
                if not declared_exchange:
                    raise StrategyV2ContractError("strategyV2.equityProductVenueRequired")
                if not supports_equity_product(
                    venue,
                    product_type,
                    market_type,
                    str(product.get("api_family") or market_type),
                ):
                    raise StrategyV2ContractError("strategyV2.equityProductUnsupported")
            products.append({
                "market": "Crypto",
                "symbol": symbol,
                "exchange_id": venue,
                "market_type": market_type,
                "instrument_id": str(product.get("instrument_id") or ""),
                "product_type": product_type,
                "api_family": str(product.get("api_family") or market_type),
                "underlying_market": str(product.get("underlying_market") or ""),
                "underlying_symbol": str(product.get("underlying_symbol") or ""),
                "product_meta": dict(product.get("product_meta") or {}),
            })
        deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in products:
            key = (
                str(item.get("symbol") or "").upper(),
                str(item.get("exchange_id") or "").lower(),
                str(item.get("market_type") or "spot").lower(),
            )
            deduped[key] = item
        return list(deduped.values())

    @staticmethod
    def _resolve_live_instruments(
        *, user_id: int, manifest: Any, execution_mode: str,
    ) -> list[dict[str, Any]] | None:
        if execution_mode != "live" or getattr(manifest.universe, "kind", "static") == "static":
            return None
        from app.services.strategy_v2.service import StrategyV2BacktestService

        now = datetime.now(timezone.utc)
        candidates, _ = StrategyV2BacktestService().resolve_candidates(
            user_id=int(user_id),
            manifest=manifest,
            start_date=now,
            end_date=now,
        )
        return candidates

    @staticmethod
    def _validate_live_quote_currency(
        *,
        markets: tuple[str, ...],
        instruments: list[dict[str, Any]] | None,
        products: list[dict[str, Any]],
        execution_mode: str,
    ) -> str:
        if execution_mode != "live":
            return ""
        currencies: set[str] = set()
        product_index = {
            (
                str(item.get("symbol") or "").strip().upper(),
                str(item.get("exchange_id") or "").strip().lower(),
                str(item.get("market_type") or "spot").strip().lower(),
            ): item
            for item in products
            if isinstance(item, dict)
        }
        source = instruments or []
        for item in source:
            market = str(item.get("market") or "").strip()
            if market == "USStock":
                currencies.add("USD")
                continue
            if market == "HKStock":
                currencies.add("HKD")
                continue
            if market != "Crypto":
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            product = product_index.get((
                symbol,
                str(item.get("exchange_id") or "").strip().lower(),
                str(item.get("market_type") or "spot").strip().lower(),
            )) or {}
            meta = product.get("product_meta") if isinstance(product.get("product_meta"), dict) else {}
            currency = str(
                meta.get("settlement_currency")
                or meta.get("quote_currency")
                or product.get("settle_currency")
                or (symbol.rsplit("/", 1)[1] if "/" in symbol else "")
            ).strip().upper()
            if currency:
                currencies.add(currency)
        if not source and len(markets) == 1:
            market = str(markets[0])
            if market == "USStock":
                currencies.add("USD")
            elif market == "HKStock":
                currencies.add("HKD")
        if len(currencies) > 1:
            raise StrategyV2ContractError("strategyV2.mixedQuoteCurrencyLiveUnsupported")
        return next(iter(currencies), "USDT")

    @staticmethod
    def _validate_bot_product_compatibility(
        *, bot_type: str, instrument_products: list[dict[str, Any]],
    ) -> None:
        if str(bot_type or "").strip().lower() != "grid":
            return
        if any(
            str(item.get("api_family") or "").strip().lower() == "stock"
            for item in instrument_products
            if isinstance(item, dict)
        ):
            raise StrategyV2ContractError("strategyV2.equityRobotUnsupported")

    @staticmethod
    def _manifest_symbol(manifest: dict[str, Any]) -> str:
        universe = manifest.get("universe") or {}
        if universe.get("reference"):
            return f"universe:{universe['reference']}"
        instruments = universe.get("instruments") or []
        if len(instruments) == 1:
            return str(instruments[0].get("symbol") or "")
        return f"basket:{len(instruments)}"

    @staticmethod
    def _manifest_market_type(manifest: dict[str, Any]) -> str:
        instruments = (manifest.get("universe") or {}).get("instruments") or []
        values = {str(item.get("market_type") or "spot") for item in instruments}
        return next(iter(values)) if len(values) == 1 else "mixed"

    @staticmethod
    def _supports_contract_leverage(manifest: dict[str, Any]) -> bool:
        universe = manifest.get("universe") or {}
        instruments = universe.get("instruments") or []
        return bool(instruments) and all(
            str(item.get("market") or "") == "Crypto"
            and str(item.get("market_type") or "").lower() == "swap"
            for item in instruments
        )


_service: StrategyV2DeploymentService | None = None


def get_strategy_v2_deployment_service() -> StrategyV2DeploymentService:
    global _service
    if _service is None:
        _service = StrategyV2DeploymentService()
    return _service
