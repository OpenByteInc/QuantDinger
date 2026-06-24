"""Shared market module model types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class DataRequirement:
    """Configuration needed or recommended for a market data capability."""

    key: str
    label: str
    setting_keys: List[str] = field(default_factory=list)
    required: bool = False
    recommended: bool = False
    purpose: str = "market data"
    built_in: bool = False


@dataclass(frozen=True)
class MarketModule:
    """Canonical market capability declaration."""

    key: str
    label: str
    description: str
    asset_class: str
    symbol_hint: str
    base_currency: str = ""
    features: List[str] = field(default_factory=list)
    data_requirements: List[DataRequirement] = field(default_factory=list)
    live_brokers: List[str] = field(default_factory=list)
    supports: Dict[str, Any] = field(default_factory=dict)

