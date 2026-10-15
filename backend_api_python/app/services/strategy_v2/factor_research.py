"""Point-in-time cross-sectional factor research for Strategy API V2 universes."""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Mapping, Sequence

import pandas as pd

from app.services.factors import (
    FactorError,
    compute_panel_factor,
    compute_talib_indicator,
    get_factor,
    get_factor_metadata,
)


class FactorResearchEngine:
    _FUNDAMENTAL_FIELDS = {
        "value": "pe_ratio",
        "quality": "return_on_equity",
        "size": "market_cap",
    }
    _CORRELATION_FACTORS = (
        "momentum_20",
        "volatility_20",
        "reversal_5",
        "rsi",
        "amihud_illiquidity",
        "skewness",
        "value",
        "quality",
        "size",
    )

    @classmethod
    def required_fields(cls, factor_id: str) -> set[str]:
        cleaned = str(factor_id or "").strip().lower()
        field = cls._FUNDAMENTAL_FIELDS.get(cleaned)
        if field:
            return {field}
        try:
            definition = get_factor(cleaned)
        except FactorError:
            return set()
        return set(definition.required_fields) if definition.factor_type == "fundamental" else set()

    @classmethod
    def warmup_bars(cls, factor_id: str, params: Mapping[str, Any] | None = None) -> int:
        try:
            metadata = get_factor_metadata(factor_id)
        except ValueError:
            return 0
        base = int(metadata.get("default_warmup_bars") or 0)
        defaults = metadata.get("default_params") or {}
        requested = dict(params or {})
        default_periods = [
            int(value) for key, value in defaults.items()
            if key != "output" and isinstance(value, int) and not isinstance(value, bool)
        ]
        requested_periods = [
            int(value) for key, value in requested.items()
            if key != "output" and isinstance(value, int) and not isinstance(value, bool)
        ]
        return base + max(0, max(requested_periods or [0]) - max(default_periods or [0]))

    def run(
        self,
        *,
        frames: Mapping[str, pd.DataFrame],
        factor_id: str,
        factor_params: Mapping[str, Any] | None = None,
        start_date: Any,
        end_date: Any,
        groups: int = 5,
        holding_period: int = 5,
        commission: float = 0.0005,
        slippage: float = 0.0005,
        neutralize_industry: bool = False,
        members: Sequence[Mapping[str, Any]] | None = None,
        annualization_periods: float = 252.0,
    ) -> dict[str, Any]:
        requested_groups = max(2, min(10, int(groups or 5)))
        holding_period = max(1, int(holding_period or 5))
        factor_name = str(factor_id or "momentum_20").strip().lower()
        start = self._timestamp(start_date)
        end = self._timestamp(end_date)
        prepared = self._prepare_frames(frames, end)
        close = self._panel(prepared, "close")
        open_price = self._panel(prepared, "open")
        if close.empty or open_price.empty:
            raise ValueError("strategyV2.factorResearchNoData")

        membership = self._membership_panel(members or (), close.index, close.columns)
        research_index = close.index[(close.index >= start) & (close.index <= end)]
        research_dates = list(research_index[::holding_period])
        if not research_dates:
            raise ValueError("strategyV2.factorResearchInsufficientObservations")

        normalized_factor_params = dict(factor_params or {})
        factor = self._factor_panel(
            prepared,
            factor_name,
            close,
            normalized_factor_params,
            research_dates,
        )
        entry = open_price.shift(-1)
        exit_price = open_price.shift(-(holding_period + 1))
        forward_return = exit_price / entry - 1.0
        industry = self._industry_panel(prepared, close.index)
        cost_rate = max(0.0, float(commission)) + max(0.0, float(slippage))
        eligible_cross_sections = []
        for timestamp in research_dates:
            active = membership.loc[timestamp]
            active_symbols = list(active.index[active])
            valid = pd.concat(
                [
                    factor.loc[timestamp].reindex(active_symbols).rename("factor"),
                    forward_return.loc[timestamp].reindex(active_symbols).rename("return"),
                ],
                axis=1,
            ).replace([math.inf, -math.inf], pd.NA).dropna()
            if len(valid) >= 3:
                eligible_cross_sections.append(len(valid))
        if not eligible_cross_sections:
            raise ValueError("strategyV2.factorResearchInsufficientObservations")
        typical_cross_section = int(median(eligible_cross_sections))
        effective_groups = min(requested_groups, max(2, typical_cross_section // 2))

        ic_rows: list[dict[str, Any]] = []
        group_rows: list[dict[str, Any]] = []
        group_returns: dict[int, list[dict[str, Any]]] = {
            index: [] for index in range(1, effective_groups + 1)
        }
        previous_weights: dict[int, dict[str, float]] = {
            index: {} for index in range(1, effective_groups + 1)
        }
        rank_autocorrelations: list[float] = []
        monotonicities: list[float] = []
        cross_section_sizes: list[int] = []
        previous_ranks: pd.Series | None = None
        total_observations = 0
        valid_observations = 0

        for timestamp in research_dates:
            active = membership.loc[timestamp]
            active_symbols = list(active.index[active])
            if len(active_symbols) < 3:
                continue
            values = factor.loc[timestamp].reindex(active_symbols).replace([math.inf, -math.inf], pd.NA)
            returns = forward_return.loc[timestamp].reindex(active_symbols).replace([math.inf, -math.inf], pd.NA)
            has_forward_return = returns.notna()
            if int(has_forward_return.sum()) < 3:
                continue
            total_observations += int(has_forward_return.sum())
            valid = pd.concat(
                [values[has_forward_return].rename("factor"), returns[has_forward_return].rename("return")],
                axis=1,
            ).dropna()
            valid_observations += int(len(valid))
            if neutralize_industry and not valid.empty:
                sectors = industry.loc[timestamp].reindex(valid.index).fillna("Unclassified")
                valid["factor"] = self._neutralize(valid["factor"], sectors)
            if len(valid) < max(3, effective_groups):
                continue

            cross_section_sizes.append(len(valid))
            ranks = valid["factor"].rank(method="average", pct=True)
            return_ranks = valid["return"].rank(method="average", pct=True)
            ic = ranks.corr(return_ranks, method="pearson")
            if pd.isna(ic):
                continue
            ic_rows.append({"time": str(pd.Timestamp(timestamp)), "value": float(ic)})
            if previous_ranks is not None:
                aligned = pd.concat([previous_ranks, ranks], axis=1).dropna()
                if len(aligned) >= 3:
                    autocorrelation = aligned.iloc[:, 0].rank(method="average").corr(
                        aligned.iloc[:, 1].rank(method="average"),
                        method="pearson",
                    )
                    if not pd.isna(autocorrelation):
                        rank_autocorrelations.append(float(autocorrelation))
            previous_ranks = ranks

            labels = pd.qcut(
                valid["factor"].rank(method="first"),
                q=effective_groups,
                labels=False,
            ) + 1
            period_group_returns: list[tuple[int, float]] = []
            for group in range(1, effective_groups + 1):
                members_in_group = list(valid.index[labels == group])
                if not members_in_group:
                    continue
                current_weights = {
                    symbol: 1.0 / len(members_in_group) for symbol in members_in_group
                }
                previous = previous_weights[group]
                turnover = self._weight_turnover(previous, current_weights)
                is_initial = not previous
                cost_multiplier = 1.0 if is_initial else 2.0
                cost = turnover * cost_multiplier * cost_rate
                gross_return = float(valid.loc[members_in_group, "return"].mean())
                net_return = gross_return - cost
                previous_weights[group] = current_weights
                observation = {
                    "time": str(pd.Timestamp(timestamp)),
                    "group": group,
                    "grossReturn": gross_return,
                    "netReturn": net_return,
                    "turnover": turnover,
                    "cost": cost,
                    "isInitial": is_initial,
                    "members": sorted(members_in_group),
                    "memberCount": len(members_in_group),
                    "crossSectionSize": len(valid),
                }
                group_returns[group].append(observation)
                group_rows.append(observation)
                period_group_returns.append((group, gross_return))
            if len(period_group_returns) >= 3:
                group_numbers = pd.Series([item[0] for item in period_group_returns], dtype="float64")
                returns_by_group = pd.Series([item[1] for item in period_group_returns], dtype="float64")
                monotonicity = group_numbers.rank(method="average").corr(
                    returns_by_group.rank(method="average"),
                    method="pearson",
                )
                if not pd.isna(monotonicity):
                    monotonicities.append(float(monotonicity))

        if not ic_rows:
            raise ValueError("strategyV2.factorResearchInsufficientObservations")

        ic_series = pd.Series([float(item["value"]) for item in ic_rows], dtype="float64")
        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std(ddof=1)) if len(ic_series) > 1 else 0.0
        annualization = max(1.0, float(annualization_periods or 252.0))
        icir = ic_mean / ic_std * math.sqrt(annualization / holding_period) if ic_std > 0 else 0.0
        rolling = ic_series.rolling(20, min_periods=5).mean()
        for index, item in enumerate(ic_rows):
            item["rolling"] = None if pd.isna(rolling.iloc[index]) else float(rolling.iloc[index])

        curves = []
        for group, values in group_returns.items():
            gross_nav = 1.0
            net_nav = 1.0
            points = []
            for item in values:
                gross_nav *= 1.0 + float(item["grossReturn"])
                net_nav *= 1.0 + float(item["netReturn"])
                points.append({"time": item["time"], "gross": gross_nav, "net": net_nav})
            curves.append({
                "group": group,
                "points": points,
                "finalGross": gross_nav,
                "finalNet": net_nav,
            })

        long_short = self._long_short_curve(
            group_returns.get(effective_groups, []),
            group_returns.get(1, []),
        )
        coverage = valid_observations / total_observations if total_observations else 0.0
        rebalance_rows = [item for item in group_rows if not item["isInitial"]]
        average_turnover = (
            sum(float(item["turnover"]) for item in rebalance_rows) / len(rebalance_rows)
            if rebalance_rows else 0.0
        )
        first_half = ic_series.iloc[: max(1, len(ic_series) // 2)]
        second_half = ic_series.iloc[max(1, len(ic_series) // 2):]
        sample_diagnostics = self._sample_diagnostics(
            requested_groups=requested_groups,
            effective_groups=effective_groups,
            cross_section_sizes=cross_section_sizes,
            ic_observations=len(ic_rows),
        )

        return {
            "methodologyVersion": 2,
            "factorId": factor_name,
            "factorParams": normalized_factor_params,
            "rankIc": ic_mean,
            "icir": icir,
            "icPositiveRate": float((ic_series > 0).mean()),
            "icSeries": ic_rows,
            "groupCurves": curves,
            "longShortCurve": long_short,
            "groupObservations": group_rows,
            "monotonicity": sum(monotonicities) / len(monotonicities) if monotonicities else 0.0,
            "coverage": coverage,
            "missingRate": 1.0 - coverage,
            "averageTurnover": average_turnover,
            "grossLongShortReturn": float(long_short[-1]["gross"] - 1.0) if long_short else 0.0,
            "netLongShortReturn": float(long_short[-1]["net"] - 1.0) if long_short else 0.0,
            "neutralized": bool(neutralize_industry),
            "requestedGroups": requested_groups,
            "effectiveGroups": effective_groups,
            "sampleDiagnostics": sample_diagnostics,
            "pointInTimeUniverseApplied": self._has_dated_membership(members or ()),
            "factorCorrelation": self._factor_correlation(
                prepared,
                close,
                membership,
                research_dates,
                factor_name,
                normalized_factor_params,
            ),
            "stability": {
                "firstHalfIc": float(first_half.mean()) if not first_half.empty else 0.0,
                "secondHalfIc": float(second_half.mean()) if not second_half.empty else 0.0,
                "rankAutocorrelation": (
                    sum(rank_autocorrelations) / len(rank_autocorrelations)
                    if rank_autocorrelations else 0.0
                ),
            },
            "executionAssumptions": {
                "signal": "close_point_in_time",
                "entry": "next_bar_open",
                "exit": f"open_after_{holding_period}_bars",
                "commission": commission,
                "slippage": slippage,
                "periodsPerYear": annualization,
                "initialFormationExcludedFromAverageTurnover": True,
            },
        }

    @classmethod
    def _prepare_frames(
        cls,
        frames: Mapping[str, pd.DataFrame],
        end: pd.Timestamp,
    ) -> dict[str, pd.DataFrame]:
        prepared: dict[str, pd.DataFrame] = {}
        for symbol, source in frames.items():
            frame = source.copy()
            frame.index = pd.to_datetime(frame.index, utc=True).tz_localize(None)
            prepared[symbol] = frame.sort_index().loc[lambda item: item.index <= end].copy()
        return prepared

    @staticmethod
    def _timestamp(value: Any) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            return timestamp.tz_convert("UTC").tz_localize(None)
        return timestamp

    @staticmethod
    def _panel(frames: Mapping[str, pd.DataFrame], field: str) -> pd.DataFrame:
        columns = {}
        for symbol, frame in frames.items():
            if field in frame.columns:
                columns[symbol] = pd.to_numeric(frame[field], errors="coerce")
        return pd.DataFrame(columns).sort_index()

    def _factor_panel(
        self,
        frames: Mapping[str, pd.DataFrame],
        factor_id: str,
        close: pd.DataFrame,
        params: Mapping[str, Any] | None = None,
        timestamps: Sequence[pd.Timestamp] | None = None,
    ) -> pd.DataFrame:
        if factor_id.startswith("talib:"):
            normalized = dict(params or {})
            output = str(normalized.pop("output", "") or "")
            columns = {}
            for symbol, frame in frames.items():
                result = compute_talib_indicator(factor_id, frame, normalized)
                if isinstance(result, pd.DataFrame):
                    selected = output or str(result.columns[0])
                    if selected not in result.columns:
                        raise ValueError("factor.outputNotFound")
                    columns[symbol] = result[selected]
                else:
                    columns[symbol] = result
            return pd.DataFrame(columns).reindex(index=close.index, columns=close.columns)
        if factor_id.startswith("momentum_"):
            lookback = self._suffix_number(factor_id, 20)
            return close.pct_change(lookback, fill_method=None)
        if factor_id.startswith("volatility_"):
            lookback = self._suffix_number(factor_id, 20)
            return close.pct_change(fill_method=None).rolling(lookback).std()
        if factor_id in {"reversal", "reversal_5"}:
            return -close.pct_change(5, fill_method=None)
        try:
            get_factor(factor_id)
        except FactorError as exc:
            if exc.code != "factor.notFound":
                raise
        else:
            return self._registered_factor_panel(
                frames,
                factor_id,
                close,
                params,
                timestamps,
            )
        field = self._FUNDAMENTAL_FIELDS.get(factor_id, factor_id)
        panel = self._panel(frames, field)
        if field == "pe_ratio":
            panel = 1.0 / panel.where(panel > 0)
        if field == "market_cap":
            panel = panel.where(panel > 0).map(math.log)
        return panel.reindex(index=close.index, columns=close.columns)

    @staticmethod
    def _registered_factor_panel(
        frames: Mapping[str, pd.DataFrame],
        factor_id: str,
        close: pd.DataFrame,
        params: Mapping[str, Any] | None,
        timestamps: Sequence[pd.Timestamp] | None,
    ) -> pd.DataFrame:
        result = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        requested = close.index if timestamps is None else pd.Index(timestamps)
        requested = requested.intersection(close.index, sort=False)
        for timestamp in requested:
            point_in_time = {
                symbol: frame.loc[frame.index <= timestamp]
                for symbol, frame in frames.items()
                if not frame.empty and frame.index[0] <= timestamp
            }
            scores = compute_panel_factor(factor_id, point_in_time, params)
            for symbol, value in scores.items():
                if symbol in result.columns:
                    result.at[timestamp, symbol] = value
        return result

    @staticmethod
    def _suffix_number(value: str, default: int) -> int:
        try:
            return max(1, int(value.rsplit("_", 1)[-1]))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _industry_panel(frames: Mapping[str, pd.DataFrame], index: pd.Index) -> pd.DataFrame:
        columns = {}
        for symbol, frame in frames.items():
            if "industry" in frame.columns:
                columns[symbol] = frame["industry"].reindex(index).ffill()
        return pd.DataFrame(columns, index=index)

    @classmethod
    def _membership_panel(
        cls,
        members: Sequence[Mapping[str, Any]],
        index: pd.Index,
        columns: pd.Index,
    ) -> pd.DataFrame:
        if not members:
            return pd.DataFrame(True, index=index, columns=columns, dtype=bool)
        panel = pd.DataFrame(False, index=index, columns=columns, dtype=bool)
        by_key: dict[str, list[Mapping[str, Any]]] = {}
        for member in members:
            key = str(member.get("key") or "")
            if key:
                by_key.setdefault(key, []).append(member)
        for symbol in columns:
            entries = by_key.get(str(symbol), [])
            periods: list[tuple[Any, Any]] = []
            for entry in entries:
                raw_periods = entry.get("membership_periods")
                if isinstance(raw_periods, list):
                    periods.extend(
                        (item.get("valid_from"), item.get("valid_to"))
                        for item in raw_periods
                        if isinstance(item, Mapping)
                    )
                elif entry.get("valid_from") or entry.get("valid_to"):
                    periods.append((entry.get("valid_from"), entry.get("valid_to")))
            if not periods:
                if entries:
                    panel[symbol] = True
                continue
            active = pd.Series(False, index=index)
            for valid_from, valid_to in periods:
                lower = cls._timestamp(valid_from) if valid_from else index.min()
                upper = cls._timestamp(valid_to) if valid_to else None
                interval = index >= lower
                if upper is not None:
                    interval &= index < upper
                active |= interval
            panel[symbol] = active.astype(bool)
        return panel

    @staticmethod
    def _has_dated_membership(members: Sequence[Mapping[str, Any]]) -> bool:
        return any(
            member.get("valid_from")
            or member.get("valid_to")
            or member.get("membership_periods")
            for member in members
        )

    @staticmethod
    def _neutralize(values: pd.Series, sectors: pd.Series) -> pd.Series:
        counts = sectors.value_counts()
        group_means = values.groupby(sectors).transform("mean")
        global_centered = values - values.mean()
        neutralized = values - group_means
        singleton = sectors.map(counts).fillna(0) < 2
        return neutralized.where(~singleton, global_centered)

    @staticmethod
    def _weight_turnover(previous: Mapping[str, float], current: Mapping[str, float]) -> float:
        if not previous:
            return 1.0
        symbols = set(previous) | set(current)
        return 0.5 * sum(
            abs(float(current.get(item, 0.0)) - float(previous.get(item, 0.0)))
            for item in symbols
        )

    @staticmethod
    def _long_short_curve(
        long_values: list[dict[str, Any]],
        short_values: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        short_map = {item["time"]: item for item in short_values}
        gross_nav = 1.0
        net_nav = 1.0
        points = []
        for long_item in long_values:
            short_item = short_map.get(long_item["time"])
            if not short_item:
                continue
            gross_return = float(long_item["grossReturn"]) - float(short_item["grossReturn"])
            total_cost = float(long_item["cost"]) + float(short_item["cost"])
            gross_nav *= 1.0 + gross_return
            net_nav *= 1.0 + gross_return - total_cost
            points.append({"time": long_item["time"], "gross": gross_nav, "net": net_nav})
        return points

    def _factor_correlation(
        self,
        frames: Mapping[str, pd.DataFrame],
        close: pd.DataFrame,
        membership: pd.DataFrame,
        research_dates: Sequence[pd.Timestamp],
        selected_factor: str,
        selected_params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        names = list(dict.fromkeys((selected_factor, *self._CORRELATION_FACTORS)))
        panels: dict[str, pd.DataFrame] = {}
        for name in names:
            panel = self._factor_panel(
                frames,
                name,
                close,
                selected_params if name == selected_factor else None,
                research_dates,
            )
            visible = panel.reindex(index=research_dates)
            if int(visible.notna().sum().sum()) >= 3:
                panels[name] = panel
        names = list(panels)
        if not names:
            return {"factors": [], "matrix": [], "observations": []}

        values: list[list[float]] = []
        observations: list[list[int]] = []
        for row_name in names:
            value_row: list[float] = []
            observation_row: list[int] = []
            for column_name in names:
                correlations: list[float] = []
                for timestamp in research_dates:
                    active = membership.loc[timestamp]
                    active_symbols = list(active.index[active])
                    pair = pd.concat(
                        [
                            panels[row_name].loc[timestamp].reindex(active_symbols).rename("left"),
                            panels[column_name].loc[timestamp].reindex(active_symbols).rename("right"),
                        ],
                        axis=1,
                    ).dropna()
                    if len(pair) < 3:
                        continue
                    left_rank = pair["left"].rank(method="average")
                    right_rank = pair["right"].rank(method="average")
                    if left_rank.nunique() < 2 or right_rank.nunique() < 2:
                        continue
                    correlation = left_rank.corr(right_rank, method="pearson")
                    if not pd.isna(correlation):
                        correlations.append(float(correlation))
                value_row.append(sum(correlations) / len(correlations) if correlations else 0.0)
                observation_row.append(len(correlations))
            values.append(value_row)
            observations.append(observation_row)
        return {"factors": names, "matrix": values, "observations": observations}

    @staticmethod
    def _sample_diagnostics(
        *,
        requested_groups: int,
        effective_groups: int,
        cross_section_sizes: Sequence[int],
        ic_observations: int,
    ) -> dict[str, Any]:
        median_size = int(median(cross_section_sizes)) if cross_section_sizes else 0
        minimum_size = min(cross_section_sizes) if cross_section_sizes else 0
        members_per_group = median_size / effective_groups if effective_groups else 0.0
        warnings: list[str] = []
        if effective_groups < requested_groups:
            warnings.append("groupsReduced")
        if members_per_group < 5:
            warnings.append("smallCrossSection")
        if ic_observations < 30:
            warnings.append("fewObservations")
        if members_per_group >= 5 and ic_observations >= 30:
            quality = "robust"
        elif members_per_group >= 3 and ic_observations >= 12:
            quality = "limited"
        else:
            quality = "insufficient"
        return {
            "quality": quality,
            "warnings": warnings,
            "requestedGroups": requested_groups,
            "effectiveGroups": effective_groups,
            "medianCrossSectionSize": median_size,
            "minimumCrossSectionSize": minimum_size,
            "medianMembersPerGroup": members_per_group,
            "icObservations": ic_observations,
        }
