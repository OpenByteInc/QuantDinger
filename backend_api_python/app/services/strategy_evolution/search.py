"""Grid, random, and lightweight TPE parameter samplers."""

from __future__ import annotations

import itertools
import math
import random
from collections import Counter
from typing import Any

from .models import SearchParameter


class ParameterSampler:
    def __init__(self, parameters: list[SearchParameter], *, seed: int = 42) -> None:
        self.parameters = parameters
        self.random = random.Random(seed)

    def grid(self, limit: int) -> list[dict[str, Any]]:
        candidates, _stats = self.grid_with_stats(limit)
        return candidates

    def grid_with_stats(self, limit: int, *, accept=None) -> tuple[list[dict[str, Any]], dict[str, int]]:
        axes = [self._grid_values(item, limit) for item in self.parameters]
        combination_count = math.prod(len(axis) for axis in axes)
        max_scan = max(limit * 30, limit + 100)
        if combination_count <= limit:
            combinations = itertools.product(*axes)
        else:
            sample_count = min(combination_count, max_scan)
            primary_indices = self._spread_indices(combination_count, min(limit, combination_count))
            primary_set = set(primary_indices)
            backup_indices = tuple(
                index
                for index in self._spread_indices(combination_count, sample_count)
                if index not in primary_set
            )
            sampled_indices = primary_indices + backup_indices
            combinations = (self._combination_at(axes, index) for index in sampled_indices)
        candidates: list[dict[str, Any]] = []
        scanned = 0
        rejected = 0
        eligible = 0
        for values in combinations:
            scanned += 1
            candidate = {item.name: value for item, value in zip(self.parameters, values)}
            if callable(accept) and not accept(candidate):
                rejected += 1
                continue
            eligible += 1
            candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return (
            candidates,
            {"scanned": scanned, "eligible": eligible, "rejected": rejected},
        )

    def grid_size(self, limit: int) -> int:
        size = 1
        for item in self.parameters:
            size *= len(self._grid_values(item, limit))
            if size >= limit:
                return limit
        return size

    def space_size(self, cap: int = 10_000_000) -> int:
        size = 1
        for item in self.parameters:
            size *= self._domain_size(item)
            if size >= cap:
                return cap
        return size

    @staticmethod
    def _domain_size(item: SearchParameter) -> int:
        if item.kind == "boolean":
            return 2
        if item.choices:
            return max(1, len(item.choices))
        low = float(item.minimum or 0)
        high = float(item.maximum if item.maximum is not None else low)
        step = float(item.step or ((high - low) / 9.0) or 1.0)
        if high <= low:
            return 1
        span = high - low
        regular_steps = max(0, int(math.floor((span / step) + 1e-12)))
        regular_maximum = low + regular_steps * step
        tolerance = max(1.0, abs(low), abs(high)) * 1e-10
        return regular_steps + 1 + (1 if regular_maximum < high - tolerance else 0)

    def random_candidate(self) -> dict[str, Any]:
        return {item.name: self._random_value(item) for item in self.parameters}

    def stratified_candidates(self, count: int) -> list[dict[str, Any]]:
        """Return deterministic space-filling candidates for random search."""
        target = min(max(0, int(count)), self.space_size())
        if target <= 0:
            return []
        if target >= self.space_size() and self.space_size() <= 100_000:
            candidates = self.grid(self.space_size())
            self.random.shuffle(candidates)
            return candidates[:target]

        axes: dict[str, list[Any]] = {}
        for item in self.parameters:
            domain_size = self._domain_size(item)
            strata = list(range(target))
            self.random.shuffle(strata)
            values: list[Any] = []
            for stratum in strata:
                quantile = (stratum + self.random.random()) / target
                domain_index = min(domain_size - 1, int(quantile * domain_size))
                values.append(self._value_at_domain_index(item, domain_index, domain_size))
            axes[item.name] = values

        candidates: list[dict[str, Any]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for row_index in range(target):
            candidate = {item.name: axes[item.name][row_index] for item in self.parameters}
            identity = tuple(sorted((str(key), repr(value)) for key, value in candidate.items()))
            if identity not in seen:
                seen.add(identity)
                candidates.append(candidate)

        attempts = 0
        while len(candidates) < target and attempts < target * 30:
            attempts += 1
            candidate = self.random_candidate()
            identity = tuple(sorted((str(key), repr(value)) for key, value in candidate.items()))
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(candidate)
        return candidates

    def tpe_candidate(self, history: list[tuple[dict[str, Any], float]], *, samples: int = 48) -> dict[str, Any]:
        if len(history) < max(5, len(self.parameters) + 1):
            return self.random_candidate()
        ranked = sorted(history, key=lambda row: row[1], reverse=True)
        split = max(3, int(len(ranked) * 0.25))
        good = [row[0] for row in ranked[:split]]
        bad = [row[0] for row in ranked[split:]]
        seen = {
            tuple(sorted((str(key), repr(value)) for key, value in params.items()))
            for params, _score in history
        }
        best = None
        best_ratio = -math.inf
        for _ in range(samples):
            candidate = {item.name: self._tpe_value(item, good) for item in self.parameters}
            identity = tuple(sorted((str(key), repr(value)) for key, value in candidate.items()))
            if identity in seen:
                continue
            ratio = sum(self._density_ratio(item, candidate[item.name], good, bad) for item in self.parameters)
            if ratio > best_ratio:
                best, best_ratio = candidate, ratio
        return best or self.random_candidate()

    def _grid_values(self, item: SearchParameter, limit: int) -> list[Any]:
        if item.kind == "boolean":
            return [False, True]
        if item.choices:
            return list(item.choices)
        low = float(item.minimum or 0)
        high = float(item.maximum if item.maximum is not None else low)
        step = float(item.step or ((high - low) / max(1, min(limit, 10) - 1)) or 1)
        domain_size = self._domain_size(item)
        if domain_size <= limit:
            indices = range(domain_size)
        elif limit <= 1:
            indices = (0,)
        else:
            indices = tuple(sorted({round(index * (domain_size - 1) / (limit - 1)) for index in range(limit)}))
        values = [self._domain_value(item, index, domain_size, low, high, step) for index in indices]
        return [self._cast(item, value) for value in values]

    def _random_value(self, item: SearchParameter) -> Any:
        if item.kind == "boolean":
            return bool(self.random.getrandbits(1))
        if item.choices:
            return self.random.choice(item.choices)
        low = float(item.minimum or 0)
        high = float(item.maximum if item.maximum is not None else low)
        value = self.random.uniform(low, high)
        if item.step:
            value = self._snap_to_domain(item, value)
        return self._cast(item, min(high, max(low, value)))

    def _value_at_domain_index(self, item: SearchParameter, index: int, domain_size: int) -> Any:
        if item.kind == "boolean":
            return bool(index)
        if item.choices:
            return item.choices[index]
        low = float(item.minimum or 0)
        high = float(item.maximum if item.maximum is not None else low)
        step = float(item.step or ((high - low) / 9.0) or 1.0)
        return self._cast(item, self._domain_value(item, index, domain_size, low, high, step))

    def _tpe_value(self, item: SearchParameter, good: list[dict[str, Any]]) -> Any:
        if item.kind == "boolean" or item.choices:
            values = [row.get(item.name, item.default) for row in good]
            counts = Counter(values)
            population = list(counts)
            weights = [counts[value] + 1 for value in population]
            return self.random.choices(population, weights=weights, k=1)[0]
        values = [float(row[item.name]) for row in good if row.get(item.name) is not None]
        if not values:
            return self._random_value(item)
        center = self.random.choice(values)
        low = float(item.minimum or 0)
        high = float(item.maximum if item.maximum is not None else low)
        bandwidth = max((high - low) / max(6.0, math.sqrt(len(values)) * 3), float(item.step or 0))
        value = center
        for _ in range(8):
            proposal = self.random.gauss(center, bandwidth)
            if low <= proposal <= high:
                value = proposal
                break
        else:
            value = self.random.uniform(low, high)
        if item.step:
            value = self._snap_to_domain(item, value)
        return self._cast(item, min(high, max(low, value)))

    @staticmethod
    def _combination_at(axes: list[list[Any]], flat_index: int) -> tuple[Any, ...]:
        values: list[Any] = [None] * len(axes)
        remaining = flat_index
        for index in range(len(axes) - 1, -1, -1):
            axis = axes[index]
            remaining, offset = divmod(remaining, len(axis))
            values[index] = axis[offset]
        return tuple(values)

    @staticmethod
    def _spread_indices(total: int, count: int) -> tuple[int, ...]:
        if count >= total:
            return tuple(range(total))
        if count <= 1:
            return (0,)
        return tuple(
            round(index * (total - 1) / (count - 1))
            for index in range(count)
        )

    @staticmethod
    def _domain_value(item: SearchParameter, index: int, domain_size: int, low: float, high: float, step: float) -> Any:
        if index >= domain_size - 1:
            return high
        return min(high, low + index * step)

    def _snap_to_domain(self, item: SearchParameter, value: float) -> float:
        low = float(item.minimum or 0)
        high = float(item.maximum if item.maximum is not None else low)
        step = float(item.step or 1.0)
        domain_size = self._domain_size(item)
        if domain_size <= 1:
            return low
        raw_index = (value - low) / step
        indices = {
            max(0, min(domain_size - 1, int(math.floor(raw_index)))),
            max(0, min(domain_size - 1, int(math.ceil(raw_index)))),
            domain_size - 1,
        }
        candidates = [self._domain_value(item, index, domain_size, low, high, step) for index in indices]
        return min(candidates, key=lambda candidate: (abs(float(candidate) - value), float(candidate)))

    def _density_ratio(self, item: SearchParameter, value: Any, good: list[dict[str, Any]], bad: list[dict[str, Any]]) -> float:
        if item.kind == "boolean" or item.choices:
            good_count = sum(1 for row in good if row.get(item.name) == value) + 1
            bad_count = sum(1 for row in bad if row.get(item.name) == value) + 1
            return math.log(good_count / (len(good) + 2)) - math.log(bad_count / (len(bad) + 2))
        low = float(item.minimum or 0)
        high = float(item.maximum if item.maximum is not None else low)
        bandwidth = max((high - low) / 12, float(item.step or 1e-9))
        return math.log(self._kernel_density(float(value), good, item.name, bandwidth) + 1e-12) - math.log(
            self._kernel_density(float(value), bad, item.name, bandwidth) + 1e-12
        )

    @staticmethod
    def _kernel_density(value: float, rows: list[dict[str, Any]], name: str, bandwidth: float) -> float:
        values = [float(row[name]) for row in rows if row.get(name) is not None]
        if not values:
            return 1e-12
        return sum(math.exp(-0.5 * ((value - item) / bandwidth) ** 2) for item in values) / (len(values) * bandwidth)

    @staticmethod
    def _cast(item: SearchParameter, value: float) -> Any:
        if item.kind == "integer":
            return int(round(value))
        return round(float(value), 10)
