"""Strategy API V2 public surface."""

from .contract import (
    CompiledStrategyV2,
    DiscoveryContext,
    StateNamespace,
    StrategyV2ContractError,
    canonical_source_metadata,
    compile_strategy_v2,
    inspect_strategy_v2,
    is_strategy_v2_code,
    strategy_source_code_hash,
)
from .instruments import InstrumentParseError, infer_market, normalize_frequency, parse_instrument
from .models import InstrumentSpec, ScheduleSpec, StrategyManifest, SubscriptionSpec, UniverseSpec
from .data import MultiAssetDataPortal, StrategyDataError
from .runtime import OrderIntent, StrategyRuntimeContext, StrategyV2BacktestRunner, StrategyV2LiveSession
from .protection import ProtectionDecision, ProtectionEngine, ProtectionSpec, ProtectionState
from .service import StrategyV2BacktestService
from .deployment import StrategyV2DeploymentService, get_strategy_v2_deployment_service
from .storage import FactorResearchRepository, StrategyBacktestRepository

__all__ = [
    "CompiledStrategyV2",
    "DiscoveryContext",
    "InstrumentParseError",
    "InstrumentSpec",
    "MultiAssetDataPortal",
    "ScheduleSpec",
    "StateNamespace",
    "StrategyManifest",
    "StrategyDataError",
    "StrategyRuntimeContext",
    "OrderIntent",
    "ProtectionDecision",
    "ProtectionEngine",
    "ProtectionSpec",
    "ProtectionState",
    "StrategyV2BacktestRunner",
    "StrategyV2LiveSession",
    "StrategyV2BacktestService",
    "StrategyV2DeploymentService",
    "StrategyBacktestRepository",
    "FactorResearchRepository",
    "get_strategy_v2_deployment_service",
    "StrategyV2ContractError",
    "SubscriptionSpec",
    "UniverseSpec",
    "compile_strategy_v2",
    "inspect_strategy_v2",
    "strategy_source_code_hash",
    "canonical_source_metadata",
    "infer_market",
    "is_strategy_v2_code",
    "normalize_frequency",
    "parse_instrument",
]
