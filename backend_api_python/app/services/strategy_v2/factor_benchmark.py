"""Cross-factor benchmark ranking with out-of-sample and false-discovery controls."""

from __future__ import annotations

import math
from statistics import mean, stdev
from typing import Any, Mapping, Sequence


class FactorBenchmarkEngine:
    """Rank completed factor-research results without re-reading market data."""

    METHODOLOGY_VERSION = 2

    def run(
        self,
        results: Sequence[Mapping[str, Any]],
        *,
        train_fraction: float = 0.6,
        false_discovery_rate: float = 0.1,
        minimum_oos_observations: int = 5,
        minimum_abs_oos_ic: float = 0.02,
        minimum_coverage: float = 0.5,
        holding_period: int = 5,
    ) -> dict[str, Any]:
        split = min(0.8, max(0.5, float(train_fraction or 0.6)))
        alpha = min(0.5, max(0.001, float(false_discovery_rate or 0.1)))
        min_oos = max(3, int(minimum_oos_observations or 5))
        min_abs_ic = max(0.0, float(minimum_abs_oos_ic or 0.0))
        min_coverage = min(1.0, max(0.0, float(minimum_coverage or 0.0)))
        holding_period = max(1, int(holding_period or 5))

        rows = [
            self._summarize(
                result,
                train_fraction=split,
                minimum_oos_observations=min_oos,
                holding_period=holding_period,
            )
            for result in results
        ]
        q_values = self._benjamini_hochberg([row["pValue"] for row in rows])
        for row, q_value in zip(rows, q_values):
            row["qValue"] = q_value
            row["classification"] = self._classify(
                row,
                false_discovery_rate=alpha,
                minimum_abs_oos_ic=min_abs_ic,
                minimum_coverage=min_coverage,
                minimum_oos_observations=min_oos,
            )
            row["score"] = self._score(row, alpha)

        rows.sort(
            key=lambda item: (
                item["classification"] == "insufficient",
                -float(item["score"]),
                float(item["qValue"]),
                str(item["factorId"]),
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank

        counts = {
            label: sum(row["classification"] == label for row in rows)
            for label in ("alive", "reversed", "candidate", "decaying", "dead", "insufficient")
        }
        strongest_oos = max(
            rows,
            key=lambda item: abs(float(item["testRankIc"])),
            default=None,
        )
        return {
            "methodologyVersion": self.METHODOLOGY_VERSION,
            "rankings": rows,
            "summary": {
                "factorCount": len(rows),
                "counts": counts,
                "bestFactorId": rows[0]["factorId"] if rows else None,
                "positiveOosCount": sum(float(row["testRankIc"]) > 0 for row in rows),
                "negativeOosCount": sum(float(row["testRankIc"]) < 0 for row in rows),
                "zeroOosCount": sum(float(row["testRankIc"]) == 0 for row in rows),
                "strongestOosFactorId": strongest_oos["factorId"] if strongest_oos else None,
                "strongestOosRankIc": strongest_oos["testRankIc"] if strongest_oos else None,
            },
            "correlation": self._correlation_matrix(results),
            "validation": {
                "trainFraction": split,
                "testFraction": 1.0 - split,
                "falseDiscoveryRate": alpha,
                "multipleTesting": "benjamini_hochberg",
                "minimumOosObservations": min_oos,
                "minimumAbsOosIc": min_abs_ic,
                "minimumCoverage": min_coverage,
                "classificationOrder": [
                    "alive", "reversed", "candidate", "decaying", "dead", "insufficient"
                ],
            },
        }

    def _summarize(
        self,
        result: Mapping[str, Any],
        *,
        train_fraction: float,
        minimum_oos_observations: int,
        holding_period: int,
    ) -> dict[str, Any]:
        observations = self._ic_observations(result)
        values = [value for _, value in observations]
        split_index = max(1, int(len(values) * train_fraction))
        if len(values) >= minimum_oos_observations + 2:
            split_index = min(split_index, len(values) - minimum_oos_observations)
        train = values[:split_index]
        test = values[split_index:]
        train_ic, train_std = self._mean_std(train)
        test_ic, test_std = self._mean_std(test)
        full_ic, full_std = self._mean_std(values)
        annualization = float(
            (result.get("executionAssumptions") or {}).get("periodsPerYear") or 252.0
        )
        p_value = self._mean_p_value(test)
        same_sign = train_ic * test_ic > 0
        retention = (
            min(2.0, abs(test_ic) / abs(train_ic))
            if same_sign and abs(train_ic) > 1e-12
            else 0.0
        )
        direction = "inverse" if full_ic < 0 else "normal"
        net_return = self._number(result.get("netLongShortReturn"))
        oriented_return = -net_return if direction == "inverse" else net_return
        return {
            "factorId": str(result.get("factorId") or ""),
            "factorParams": dict(result.get("factorParams") or {}),
            "direction": direction,
            "observationCount": len(values),
            "trainObservationCount": len(train),
            "testObservationCount": len(test),
            "rankIc": full_ic,
            "trainRankIc": train_ic,
            "testRankIc": test_ic,
            "trainIcStd": train_std,
            "testIcStd": test_std,
            "testIcir": self._icir(test_ic, test_std, annualization, holding_period),
            "testPositiveRate": self._positive_rate(test),
            "sameSign": same_sign,
            "oosRetention": retention,
            "pValue": p_value,
            "qValue": 1.0,
            "coverage": self._number(result.get("coverage")),
            "averageTurnover": self._number(result.get("averageTurnover")),
            "netLongShortReturn": net_return,
            "orientedNetLongShortReturn": oriented_return,
            "monotonicity": self._number(result.get("monotonicity")),
            "pointInTimeUniverseApplied": bool(result.get("pointInTimeUniverseApplied")),
        }

    @staticmethod
    def _classify(
        row: Mapping[str, Any],
        *,
        false_discovery_rate: float,
        minimum_abs_oos_ic: float,
        minimum_coverage: float,
        minimum_oos_observations: int,
    ) -> str:
        if (
            int(row["testObservationCount"]) < minimum_oos_observations
            or float(row["coverage"]) < minimum_coverage
        ):
            return "insufficient"
        train_ic = float(row["trainRankIc"])
        test_ic = float(row["testRankIc"])
        stable = bool(row["sameSign"])
        significant = float(row["qValue"]) <= false_discovery_rate
        usable = (
            abs(test_ic) >= minimum_abs_oos_ic
            and float(row["coverage"]) >= minimum_coverage
        )
        if stable and significant and usable:
            return "alive" if test_ic > 0 else "reversed"
        if abs(train_ic) >= minimum_abs_oos_ic and (
            not stable or abs(test_ic) < abs(train_ic) * 0.35
        ):
            return "decaying"
        if stable and usable:
            return "candidate"
        return "dead"

    @staticmethod
    def _score(row: Mapping[str, Any], false_discovery_rate: float) -> float:
        if row.get("classification") == "insufficient":
            return 0.0
        test_ic = abs(float(row["testRankIc"]))
        edge = min(1.0, test_ic / 0.08)
        retention = min(1.0, float(row["oosRetention"])) if row["sameSign"] else 0.0
        significance = max(
            0.0,
            1.0 - float(row["qValue"]) / max(false_discovery_rate, 1e-12),
        )
        coverage = min(1.0, max(0.0, float(row["coverage"])))
        oriented_return = max(0.0, float(row["orientedNetLongShortReturn"]))
        return_score = min(1.0, oriented_return / 0.15)
        turnover_efficiency = 1.0 - min(1.0, max(0.0, float(row["averageTurnover"])) / 2.0)
        score = (
            edge * 35.0
            + retention * 20.0
            + significance * 15.0
            + coverage * 10.0
            + return_score * 10.0
            + turnover_efficiency * 10.0
        )
        return round(score, 4)

    @staticmethod
    def _ic_observations(result: Mapping[str, Any]) -> list[tuple[str, float]]:
        output: list[tuple[str, float]] = []
        for item in result.get("icSeries") or []:
            if not isinstance(item, Mapping):
                continue
            value = FactorBenchmarkEngine._optional_number(item.get("value"))
            if value is None:
                continue
            output.append((str(item.get("time") or ""), value))
        return sorted(output, key=lambda item: item[0])

    @staticmethod
    def _mean_std(values: Sequence[float]) -> tuple[float, float]:
        if not values:
            return 0.0, 0.0
        return float(mean(values)), float(stdev(values)) if len(values) > 1 else 0.0

    @staticmethod
    def _mean_p_value(values: Sequence[float]) -> float:
        average, deviation = FactorBenchmarkEngine._mean_std(values)
        if len(values) < 2 or deviation <= 1e-12:
            return 0.0 if abs(average) > 1e-12 else 1.0
        statistic = average / (deviation / math.sqrt(len(values)))
        return min(1.0, max(0.0, math.erfc(abs(statistic) / math.sqrt(2.0))))

    @staticmethod
    def _benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
        count = len(p_values)
        if not count:
            return []
        ordered = sorted(range(count), key=lambda index: float(p_values[index]))
        adjusted = [1.0] * count
        running = 1.0
        for reverse_rank in range(count - 1, -1, -1):
            index = ordered[reverse_rank]
            rank = reverse_rank + 1
            candidate = min(1.0, float(p_values[index]) * count / rank)
            running = min(running, candidate)
            adjusted[index] = running
        return adjusted

    @staticmethod
    def _icir(mean_ic: float, std_ic: float, periods_per_year: float, holding_period: int) -> float:
        if std_ic <= 1e-12:
            return 0.0
        return float(mean_ic / std_ic * math.sqrt(max(1.0, periods_per_year) / holding_period))

    @staticmethod
    def _positive_rate(values: Sequence[float]) -> float:
        return sum(value > 0 for value in values) / len(values) if values else 0.0

    @classmethod
    def _correlation_matrix(cls, results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        names = [str(result.get("factorId") or "") for result in results]
        series = [dict(cls._ic_observations(result)) for result in results]
        matrix: list[list[float | None]] = []
        observations: list[list[int]] = []
        for left_index, left in enumerate(series):
            value_row: list[float | None] = []
            count_row: list[int] = []
            for right_index, right in enumerate(series):
                shared = sorted(set(left) & set(right))
                count_row.append(len(shared))
                if left_index == right_index and shared:
                    value_row.append(1.0)
                    continue
                value_row.append(cls._pearson(
                    [left[key] for key in shared],
                    [right[key] for key in shared],
                ))
            matrix.append(value_row)
            observations.append(count_row)
        return {"factors": names, "matrix": matrix, "observations": observations}

    @staticmethod
    def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
        if len(left) < 3 or len(left) != len(right):
            return None
        left_mean = mean(left)
        right_mean = mean(right)
        numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
        left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
        right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
        if left_scale <= 1e-12 or right_scale <= 1e-12:
            return None
        return float(numerator / (left_scale * right_scale))

    @staticmethod
    def _number(value: Any) -> float:
        parsed = FactorBenchmarkEngine._optional_number(value)
        return parsed if parsed is not None else 0.0

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None
