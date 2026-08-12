"""Persistence boundary for Strategy API V2 deployments."""

from __future__ import annotations

import json
from typing import Any

from app.services.script_source import get_script_source_service
from app.services.strategy_direction import (
    direction_mode_position_side,
    normalize_direction_mode,
)
from app.utils.db import get_db_connection

from .contract import StrategyV2ContractError, compile_strategy_v2


class StrategyV2DeploymentService:
    def save(self, *, user_id: int, payload: dict[str, Any], strategy_id: int | None = None) -> int:
        source_id = int(payload.get("sourceId") or 0)
        source = get_script_source_service().get_source(source_id, user_id=user_id) if source_id else None
        if not source:
            raise StrategyV2ContractError("strategyV2.sourceNotFound")
        program = compile_strategy_v2(str(source.get("code") or ""))
        manifest = program.manifest
        name = str(payload.get("name") or source.get("name") or "").strip()
        if not name:
            raise StrategyV2ContractError("strategyV2.nameRequired")
        initial_capital = float(payload.get("initialCapital") or 0)
        if initial_capital <= 0:
            raise StrategyV2ContractError("strategyV2.invalidInitialCapital")

        execution_mode = str(payload.get("executionMode") or "signal").strip().lower()
        if execution_mode not in {"signal", "live"}:
            raise StrategyV2ContractError("strategyV2.invalidExecutionMode")
        credential_id = int(payload.get("credentialId") or 0)
        exchange_id = self._credential_exchange(user_id, credential_id) if execution_mode == "live" else ""
        if execution_mode == "live" and not exchange_id:
            raise StrategyV2ContractError("strategyV2.credentialRequired")
        self._validate_execution_account(manifest.markets, exchange_id, execution_mode)

        leverage_enabled = bool(payload.get("leverageEnabled"))
        leverage = float(payload.get("leverage") or 1)
        if leverage_enabled and not self._supports_contract_leverage(manifest.metadata()):
            raise StrategyV2ContractError("strategyV2.leverageCryptoSwapOnly")
        if leverage_enabled and not manifest.leverage_allowed:
            raise StrategyV2ContractError("strategyV2.leverageNotAllowed")
        if leverage_enabled and leverage > manifest.max_leverage:
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
        account_risk = payload.get("accountRisk") or payload.get("account_risk") or {}
        if not isinstance(account_risk, dict):
            raise StrategyV2ContractError("strategyV2.accountRiskInvalid")

        notification_config = {
            "channels": list(payload.get("notificationChannels") or []),
            "targets": payload.get("notificationTargets") or {},
        }
        source_metadata = self._object(source.get("metadata"))
        source_runtime = self._object(source_metadata.get("last_run_config"))
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
        self._normalize_grid_runtime_budget(runtime_config)
        if str(runtime_config.get("bot_type") or "").strip().lower() == "grid":
            runtime_config["position_ledger"] = "fills"
        runtime_config.update({
            "api_version": 2,
            "script_source_id": source_id,
            "strategy_manifest": manifest_metadata,
            "initial_capital": initial_capital,
            "leverage_enabled": leverage_enabled,
            "leverage": leverage,
            "params": dict(payload.get("params") or {}),
            "credential_id": credential_id or None,
            "exchange_id": exchange_id,
            "direction_mode": direction_mode,
            "position_side": position_side,
            "account_risk": dict(account_risk),
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
                manifest.primary_frequency,
                initial_capital,
                int(leverage),
                manifest_market_type,
                json.dumps(exchange_config, ensure_ascii=False),
                json.dumps(runtime_config, ensure_ascii=False),
            )
            if strategy_id:
                cur.execute(
                    """
                    UPDATE qd_strategies_trading
                    SET strategy_name = ?, market_category = ?, execution_mode = ?, notification_config = ?,
                        symbol = ?, timeframe = ?, initial_capital = ?, leverage = ?, market_type = ?,
                        exchange_config = ?, trading_config = ?, strategy_type = 'StrategyV2',
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
                       market_type, exchange_config, trading_config, created_at, updated_at)
                    VALUES (?, ?, 'StrategyV2', ?, ?, ?, 'stopped', ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
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
    def _validate_execution_account(markets: tuple[str, ...], exchange_id: str, execution_mode: str) -> None:
        if execution_mode != "live":
            return
        market_set = set(markets)
        if len(market_set) != 1:
            raise StrategyV2ContractError("strategyV2.mixedMarketLiveUnsupported")
        market = next(iter(market_set), "")
        if market == "Crypto" and exchange_id not in {"binance", "bitget", "bybit", "okx", "gate", "htx"}:
            raise StrategyV2ContractError("strategyV2.cryptoCredentialRequired")
        if market == "USStock" and exchange_id not in {"alpaca", "ibkr", "futu"}:
            raise StrategyV2ContractError("strategyV2.stockCredentialRequired")
        if market == "HKStock" and exchange_id not in {"futu"}:
            raise StrategyV2ContractError("strategyV2.hkStockCredentialRequired")
        if market not in {"Crypto", "USStock", "HKStock"}:
            raise StrategyV2ContractError("strategyV2.liveMarketUnsupported")

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
