"""
Experiment runner service.
"""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.services.backtest import BacktestService
from app.services.experiment.evolution import StrategyEvolutionService
from app.services.experiment.overrides import enrich_experiment_candidate, enrich_experiment_overrides
from app.services.experiment.optimizers import make_optimizer
from app.services.experiment.prompts import (
    SYSTEM_PROMPT,
    build_round_prompt,
    extract_indicator_params,
    parse_llm_candidates,
)
from app.services.experiment.regime import MarketRegimeService
from app.services.experiment.scoring import StrategyScoringService
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_ROUNDS = 3
DEFAULT_CANDIDATES_PER_ROUND = 5
EARLY_STOP_SCORE = 82.0


class ExperimentRunnerService:
    """Orchestrate market regime detection, batch backtests, scoring and evolution."""

    def __init__(
        self,
        *,
        backtest_service: Optional[BacktestService] = None,
        regime_service: Optional[MarketRegimeService] = None,
        scoring_service: Optional[StrategyScoringService] = None,
        evolution_service: Optional[StrategyEvolutionService] = None,
    ):
        self.backtest_service = backtest_service or BacktestService()
        self.regime_service = regime_service or MarketRegimeService()
        self.scoring_service = scoring_service or StrategyScoringService()
        self.evolution_service = evolution_service or StrategyEvolutionService()

    # ------------------------------------------------------------------
    # NEW: LLM-driven multi-round AI pipeline
    # ------------------------------------------------------------------

    def _scorer_for_payload(self, payload: Dict[str, Any]) -> StrategyScoringService:
        """Return a scorer honouring per-request ``scoring.customWeights``.

        Falls back to the shared service when no overrides are supplied so we
        keep regime-aware switching as the default behaviour.
        """
        scoring_cfg = (payload or {}).get('scoring') or {}
        custom = scoring_cfg.get('customWeights')
        if isinstance(custom, dict) and custom:
            return StrategyScoringService(custom_weights=custom)
        return self.scoring_service

    def run_ai_pipeline(
        self,
        *,
        user_id: int,
        payload: Dict[str, Any],
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Multi-round LLM-driven optimization pipeline.

        Flow per round:
          1. Build prompt from indicator code + regime + previous results
          2. LLM proposes N candidate parameter sets
          3. Batch-backtest each candidate
          4. Score & rank
          5. If best score >= threshold or max rounds reached -> stop

        Args:
            user_id: Current user.
            payload: Request body (base config + optional overrides).
            on_progress: Optional callback invoked after each round with
                         partial results (used for SSE streaming).

        Returns:
            Full experiment result dict.
        """
        base = payload.get('base') or payload
        max_rounds = int(payload.get('maxRounds') or DEFAULT_MAX_ROUNDS)
        n_per_round = int(payload.get('candidatesPerRound') or DEFAULT_CANDIDATES_PER_ROUND)
        early_stop = float(payload.get('earlyStopScore') or EARLY_STOP_SCORE)
        scorer = self._scorer_for_payload(payload)

        snapshot, start_date, end_date = self._build_snapshot(base=base, user_id=user_id)
        indicator_code = snapshot.get('code') or ''
        indicator_params = extract_indicator_params(indicator_code)

        # OOS 70/30 split — same semantics as `run_structured_tune`. We train
        # on the first 70% to keep LLM round backtests deterministic, then
        # validate the final ranked list on the held-out 30%. Disabled
        # automatically when the window is too short to split cleanly.
        oos_enabled_input = payload.get('oosValidation', True)
        oos_window = self._compute_oos_window(start_date, end_date) if oos_enabled_input else None
        if oos_window is not None:
            train_start = oos_window['train_start']
            train_end = oos_window['train_end']
            oos_start = oos_window['oos_start']
            oos_end = oos_window['oos_end']
        else:
            train_start, train_end = start_date, end_date
            oos_start = oos_end = None

        # --- Step 1: detect market regime ---
        self._emit(on_progress, 'regime', {'status': 'running'})
        try:
            regime = self.detect_regime(base)
        except Exception as exc:
            logger.warning("Regime detection failed, continuing without: %s", exc)
            regime = None
        self._emit(on_progress, 'regime', {'status': 'done', 'regime': regime})

        # --- Step 2..N: multi-round LLM optimization ---
        from app.services.llm import LLMService
        llm = LLMService()

        all_rounds: List[Dict[str, Any]] = []
        global_best: Optional[Dict[str, Any]] = None
        global_best_score = -1.0
        previous_results: Optional[List[Dict[str, Any]]] = None

        for round_num in range(1, max_rounds + 1):
            round_start = time.time()
            self._emit(on_progress, 'round_start', {
                'round': round_num,
                'maxRounds': max_rounds,
                'status': 'running',
            })

            # 2a. Build prompt
            prompt = build_round_prompt(
                indicator_code=indicator_code,
                indicator_params=indicator_params,
                regime=regime,
                previous_results=previous_results,
                round_number=round_num,
                n_candidates=n_per_round,
            )

            # 2b. Call LLM
            try:
                raw_response = llm.call_llm_api(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.7 + round_num * 0.05,
                    use_json_mode=True,
                )
                candidates_raw = parse_llm_candidates(raw_response)
            except Exception as exc:
                logger.error("LLM call failed in round %d: %s", round_num, exc)
                candidates_raw = []

            if not candidates_raw:
                logger.warning("Round %d produced no candidates, skipping", round_num)
                all_rounds.append({
                    'round': round_num,
                    'candidates': [],
                    'bestScore': global_best_score,
                    'error': 'LLM returned no valid candidates',
                })
                continue

            # 2c. Backtest each candidate
            round_ranked: List[Dict[str, Any]] = []
            n_cand = len(candidates_raw)
            for idx, cand in enumerate(candidates_raw, start=1):
                self._emit(on_progress, "candidate_backtest", {
                    "round": round_num,
                    "index": idx,
                    "total": n_cand,
                })
                cand_snapshot = self._apply_candidate_to_snapshot(
                    snapshot, cand, indicator_params,
                )
                try:
                    result = self.backtest_service.run_strategy_snapshot(
                        cand_snapshot,
                        start_date=train_start,
                        end_date=train_end,
                    )
                except Exception as exc:
                    logger.error("Backtest failed for %s: %s", cand.get('name'), exc)
                    result = {}

                score = scorer.score_result(result, regime=regime)
                round_ranked.append({
                    'name': cand.get('name', f'R{round_num}_{idx}'),
                    'reasoning': cand.get('reasoning', ''),
                    'source': f'ai_round_{round_num}',
                    'overrides': {
                        'indicatorParams': cand.get('indicatorParams', {}),
                        'riskParams': cand.get('riskParams', {}),
                    },
                    'snapshot': cand_snapshot,
                    'score': score,
                    'result': self._slim_result(result),
                })

            round_ranked = scorer.rank_results(round_ranked)
            round_best = round_ranked[0] if round_ranked else None
            round_best_score = float((round_best or {}).get('score', {}).get('overallScore', 0))

            if round_best and round_best_score > global_best_score:
                global_best = round_best
                global_best_score = round_best_score

            round_info = {
                'round': round_num,
                'candidates': round_ranked,
                'bestScore': round_best_score,
                'globalBestScore': global_best_score,
                'elapsed': round(time.time() - round_start, 1),
            }
            all_rounds.append(round_info)

            self._emit(on_progress, 'round_done', round_info)

            previous_results = round_ranked

            if global_best_score >= early_stop:
                logger.info("Early stop: score %.1f >= %.1f at round %d",
                            global_best_score, early_stop, round_num)
                break

        # --- Final output ---
        all_candidates = []
        for rd in all_rounds:
            all_candidates.extend(rd.get('candidates') or [])
        all_candidates = scorer.rank_results(all_candidates)

        oos_meta = None
        if oos_start is not None and oos_end is not None:
            self._evaluate_oos(
                all_candidates, oos_start=oos_start, oos_end=oos_end, regime=regime, scorer=scorer,
            )
            oos_meta = {
                'enabled': True,
                'trainStart': train_start.strftime('%Y-%m-%d'),
                'trainEnd': train_end.strftime('%Y-%m-%d'),
                'oosStart': oos_start.strftime('%Y-%m-%d'),
                'oosEnd': oos_end.strftime('%Y-%m-%d'),
                'trainRatio': 0.7,
            }

        output = {
            'regime': regime,
            'generatorHints': self._build_generator_hints(regime) if regime else {},
            'indicatorParams': indicator_params,
            'rounds': [{
                'round': r['round'],
                'bestScore': r.get('bestScore', 0),
                'globalBestScore': r.get('globalBestScore', 0),
                'candidateCount': len(r.get('candidates') or []),
                'elapsed': r.get('elapsed', 0),
                'error': r.get('error'),
            } for r in all_rounds],
            'rankedStrategies': all_candidates[:20],
            'bestStrategyOutput': self._build_best_output(global_best),
            'oosValidation': oos_meta,
            'scoringWeights': scorer.resolve_weights(regime),
            'experiment': {
                'totalRounds': len(all_rounds),
                'totalCandidates': len(all_candidates),
                'globalBestScore': global_best_score,
            },
        }
        # Final payload is sent once via SSE route (__final__); avoid duplicating huge JSON on progress.
        return output

    def save_as_strategy(
        self,
        *,
        user_id: int,
        best_output: Dict[str, Any],
        strategy_name: str,
        market_category: str = 'Crypto',
    ) -> int:
        """Persist the best experiment candidate as a strategy record."""
        from app.services.strategy import StrategyService
        svc = StrategyService()

        snap = best_output.get('snapshot') or {}
        strategy_config = snap.get('strategy_config') or {}

        payload = {
            'user_id': user_id,
            'strategy_name': strategy_name,
            'strategy_type': 'IndicatorStrategy',
            'market_category': market_category,
            'execution_mode': 'signal',
            'status': 'stopped',
            'indicator_config': {
                'indicator_id': snap.get('indicator_id'),
                'code': snap.get('code'),
                'indicator_params': snap.get('indicator_params') or {},
            },
            'trading_config': {
                'symbol': snap.get('symbol'),
                'timeframe': snap.get('timeframe'),
                'initial_capital': snap.get('initial_capital', 10000),
                'leverage': snap.get('leverage', 1),
                'commission': snap.get('commission', 0),
                'slippage': snap.get('slippage', 0),
                'trade_direction': snap.get('trade_direction', 'long'),
                'market_type': 'swap',
                'strategy_config': strategy_config,
                'strict_mode': snap.get('strict_mode', True),
            },
            'exchange_config': {},
        }
        return svc.create_strategy(payload)

    # ------------------------------------------------------------------
    # Helpers for AI pipeline
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_candidate_to_snapshot(
        base_snapshot: Dict[str, Any],
        candidate: Dict[str, Any],
        indicator_params_def: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a backtest snapshot by merging LLM candidate params."""
        snap = copy.deepcopy(base_snapshot)

        ind_params = candidate.get('indicatorParams') or {}
        if ind_params:
            snap['indicator_params'] = {**(snap.get('indicator_params') or {}), **ind_params}

        risk = candidate.get('riskParams') or {}
        sc = snap.get('strategy_config') or {}

        if risk.get('stopLossPct') is not None:
            sc.setdefault('risk', {})['stopLossPct'] = risk['stopLossPct']
        if risk.get('takeProfitPct') is not None:
            sc.setdefault('risk', {})['takeProfitPct'] = risk['takeProfitPct']
        if risk.get('entryPct') is not None:
            sc.setdefault('position', {})['entryPct'] = risk['entryPct']

        trailing = risk.get('trailingStop') or {}
        if trailing.get('enabled'):
            sc.setdefault('risk', {})['trailing'] = {
                'enabled': True,
                'pct': trailing.get('pct', 0.02),
                'activationPct': trailing.get('activationPct', 0.01),
            }

        if risk.get('leverage') is not None:
            snap['leverage'] = int(risk['leverage'])

        snap['strategy_config'] = sc
        return snap

    @staticmethod
    def _slim_result(result: Dict[str, Any]) -> Dict[str, Any]:
        """Strip heavy fields (equityCurve, trades list) to keep payload small."""
        if not result:
            return {}
        return {
            'totalReturn': result.get('totalReturn'),
            'annualReturn': result.get('annualReturn'),
            'maxDrawdown': result.get('maxDrawdown'),
            'sharpeRatio': result.get('sharpeRatio'),
            'profitFactor': result.get('profitFactor'),
            'winRate': result.get('winRate'),
            'totalTrades': result.get('totalTrades'),
        }

    @staticmethod
    def _emit(callback: Optional[Callable], event: str, data: Dict[str, Any]) -> None:
        if callback:
            try:
                callback({'event': event, **data})
            except Exception:
                pass

    def run_pipeline(
        self,
        *,
        user_id: int,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        base = payload.get('base') or payload
        snapshot, start_date, end_date = self._build_snapshot(base=base, user_id=user_id)

        regime = self.detect_regime(base)
        candidates = self._build_candidates(
            base_snapshot=snapshot,
            variants=payload.get('variants') or [],
            parameter_space=payload.get('parameterSpace') or {},
            evolution=payload.get('evolution') or {},
        )

        ranked: List[Dict[str, Any]] = []
        for candidate in candidates:
            result = self.backtest_service.run_strategy_snapshot(
                candidate['snapshot'],
                start_date=start_date,
                end_date=end_date,
            )
            score = self.scoring_service.score_result(result, regime=regime)
            ranked.append({
                'name': candidate['name'],
                'source': candidate['source'],
                'overrides': candidate.get('overrides') or {},
                'snapshot': candidate['snapshot'],
                'score': score,
                'result': self._slim_result(result),
            })

        ranked = self.scoring_service.rank_results(ranked)
        best = ranked[0] if ranked else None

        return {
            'regime': regime,
            'generatorHints': self._build_generator_hints(regime),
            'experiment': {
                'candidateCount': len(ranked),
                'rankedCount': len(ranked),
            },
            'rankedStrategies': ranked,
            'bestStrategyOutput': self._build_best_output(best),
        }

    def run_structured_tune(
        self,
        *,
        user_id: int,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Grid or random search over parameterSpace (strategy_config paths, leverage, etc.).
        No LLM. Scores on full backtest result, then returns slim payloads for the client.
        """
        import time

        base = payload.get('base') or payload
        parameter_space = payload.get('parameterSpace') or {}
        if not isinstance(parameter_space, dict) or not parameter_space:
            raise ValueError('parameterSpace is required and must be a non-empty object')

        evolution = payload.get('evolution') or {}
        max_variants = int(evolution.get('maxVariants') or 48)
        method = str(evolution.get('method') or 'grid').lower()
        if method not in ('grid', 'random', 'de', 'tpe'):
            method = 'grid'
        include_baseline = payload.get('includeBaseline', True)
        scorer = self._scorer_for_payload(payload)

        t0 = time.time()
        snapshot, start_date, end_date = self._build_snapshot(base=base, user_id=user_id)
        regime = self.detect_regime(base)

        # OOS validation 70/30: train on the first 70% of the window, then
        # re-backtest the top-K on the last 30%. Off when the window is too
        # short to split meaningfully OR when the caller explicitly disables.
        oos_enabled_input = payload.get('oosValidation', True)
        oos_window = self._compute_oos_window(start_date, end_date) if oos_enabled_input else None
        if oos_window is not None:
            train_start = oos_window['train_start']
            train_end = oos_window['train_end']
            oos_start = oos_window['oos_start']
            oos_end = oos_window['oos_end']
        else:
            train_start, train_end = start_date, end_date
            oos_start = oos_end = None

        if method in ('de', 'tpe'):
            ranked = self._run_iterative_optimizer(
                method=method,
                base_snapshot=snapshot,
                parameter_space=parameter_space,
                regime=regime,
                train_start=train_start,
                train_end=train_end,
                max_evals=max_variants,
                include_baseline=include_baseline,
                scorer=scorer,
            )
        else:
            candidates = self._build_candidates(
                base_snapshot=snapshot,
                variants=payload.get('variants') or [],
                parameter_space=parameter_space,
                evolution={
                    'method': method,
                    'maxVariants': max_variants,
                    'parameterSpace': parameter_space,
                },
            )
            if not include_baseline:
                candidates = [c for c in candidates if c.get('source') != 'baseline']

            ranked = []
            for candidate in candidates:
                try:
                    result = self.backtest_service.run_strategy_snapshot(
                        candidate['snapshot'],
                        start_date=train_start,
                        end_date=train_end,
                    )
                except Exception as exc:
                    logger.error("structured_tune backtest failed for %s: %s", candidate.get('name'), exc)
                    result = {}
                score = scorer.score_result(result, regime=regime)
                ranked.append(enrich_experiment_candidate({
                    'name': candidate['name'],
                    'reasoning': '',
                    'source': candidate['source'],
                    'overrides': candidate.get('overrides') or {},
                    'snapshot': candidate['snapshot'],
                    'score': score,
                    'result': self._slim_result(result),
                }))

        ranked = scorer.rank_results(ranked)
        if oos_start is not None and oos_end is not None:
            self._evaluate_oos(ranked, oos_start=oos_start, oos_end=oos_end, regime=regime, scorer=scorer)
        best = ranked[0] if ranked else None
        elapsed = round(time.time() - t0, 1)
        global_best_score = float((best or {}).get('score', {}).get('overallScore', 0) or 0)

        indicator_code = snapshot.get('code') or ''
        indicator_params = extract_indicator_params(indicator_code)

        oos_meta = None
        if oos_start is not None and oos_end is not None:
            oos_meta = {
                'enabled': True,
                'trainStart': train_start.strftime('%Y-%m-%d'),
                'trainEnd': train_end.strftime('%Y-%m-%d'),
                'oosStart': oos_start.strftime('%Y-%m-%d'),
                'oosEnd': oos_end.strftime('%Y-%m-%d'),
                'trainRatio': 0.7,
            }

        return {
            'regime': regime,
            'generatorHints': self._build_generator_hints(regime) if regime else {},
            'indicatorParams': indicator_params,
            'rounds': [{
                'round': 1,
                'bestScore': global_best_score,
                'globalBestScore': global_best_score,
                'candidateCount': len(ranked),
                'elapsed': elapsed,
                'error': None,
            }],
            'rankedStrategies': [enrich_experiment_candidate(c) for c in ranked[:50]],
            'bestStrategyOutput': self._build_best_output(best),
            'oosValidation': oos_meta,
            'scoringWeights': scorer.resolve_weights(regime),
            'experiment': {
                'totalRounds': 1,
                'totalCandidates': len(ranked),
                'globalBestScore': global_best_score,
                'mode': 'structured',
                'method': method,
                'maxVariants': max_variants,
            },
        }

    def detect_regime(self, base: Dict[str, Any]) -> Dict[str, Any]:
        market = str(base.get('market') or 'Crypto')
        symbol = str(base.get('symbol') or '')
        timeframe = str(base.get('timeframe') or '1D')
        start_date, end_date = self._parse_dates(base)
        df = self.backtest_service._fetch_kline_data(market, symbol, timeframe, start_date, end_date)
        return self.regime_service.detect(df, symbol=symbol, market=market, timeframe=timeframe)

    def _run_iterative_optimizer(
        self,
        *,
        method: str,
        base_snapshot: Dict[str, Any],
        parameter_space: Dict[str, Any],
        regime: Dict[str, Any],
        train_start: datetime,
        train_end: datetime,
        max_evals: int,
        include_baseline: bool = True,
        scorer: Optional[StrategyScoringService] = None,
    ) -> List[Dict[str, Any]]:
        """Drive a DE/TPE optimizer through ``ask`` / ``tell`` batches.

        Each ``ask`` returns a batch of override dicts; we materialise them
        into snapshots, backtest, score, then feed the scores back via
        ``tell``. The loop exits once the optimizer's eval budget is used up
        OR it stops proposing new candidates.
        """
        active_scorer = scorer or self.scoring_service
        try:
            optimizer = make_optimizer(
                method, parameter_space, max_evals=max(8, int(max_evals)),
            )
        except ValueError as exc:
            logger.warning("Failed to build %s optimizer: %s — falling back to grid", method, exc)
            optimizer = None

        if optimizer is None:
            # Defensive fallback: caller should have routed grid/random to the
            # legacy path, but if we get here just emulate a one-shot grid.
            candidates = self._build_candidates(
                base_snapshot=base_snapshot,
                variants=[],
                parameter_space=parameter_space,
                evolution={
                    'method': 'grid',
                    'maxVariants': max(8, int(max_evals)),
                    'parameterSpace': parameter_space,
                },
            )
            ranked: List[Dict[str, Any]] = []
            for candidate in candidates:
                try:
                    result = self.backtest_service.run_strategy_snapshot(
                        candidate['snapshot'],
                        start_date=train_start,
                        end_date=train_end,
                    )
                except Exception as exc:
                    logger.error("fallback backtest failed: %s", exc)
                    result = {}
                score = active_scorer.score_result(result, regime=regime)
                ranked.append({
                    'name': candidate['name'],
                    'reasoning': '',
                    'source': candidate['source'],
                    'overrides': candidate.get('overrides') or {},
                    'snapshot': candidate['snapshot'],
                    'score': score,
                    'result': self._slim_result(result),
                })
            return ranked

        ranked: List[Dict[str, Any]] = []

        # Optionally evaluate the baseline first so the user always sees how
        # the unmodified strategy compares to optimizer-found candidates.
        if include_baseline:
            try:
                base_result = self.backtest_service.run_strategy_snapshot(
                    copy.deepcopy(base_snapshot),
                    start_date=train_start,
                    end_date=train_end,
                )
            except Exception as exc:
                logger.error("baseline backtest failed: %s", exc)
                base_result = {}
            base_score = active_scorer.score_result(base_result, regime=regime)
            ranked.append({
                'name': 'baseline',
                'reasoning': '',
                'source': 'baseline',
                'overrides': {},
                'snapshot': copy.deepcopy(base_snapshot),
                'score': base_score,
                'result': self._slim_result(base_result),
            })

        gen_idx = 0
        while True:
            batch = optimizer.ask()
            if not batch:
                break
            gen_idx += 1
            tell_buffer: List[tuple[Dict[str, Any], float]] = []
            for cand_idx, overrides in enumerate(batch, start=1):
                snap = self._apply_overrides_to_snapshot(base_snapshot, overrides)
                try:
                    result = self.backtest_service.run_strategy_snapshot(
                        snap, start_date=train_start, end_date=train_end,
                    )
                except Exception as exc:
                    logger.error("%s backtest failed for gen=%d cand=%d: %s",
                                 method, gen_idx, cand_idx, exc)
                    result = {}
                score = active_scorer.score_result(result, regime=regime)
                overall = float((score or {}).get('overallScore') or 0.0)
                tell_buffer.append((overrides, overall))
                ranked.append({
                    'name': f'{method}_g{gen_idx}_c{cand_idx}',
                    'reasoning': '',
                    'source': f'evolution_{method}',
                    'overrides': overrides,
                    'snapshot': snap,
                    'score': score,
                    'result': self._slim_result(result),
                })
            optimizer.tell(tell_buffer)

        return ranked

    @staticmethod
    def _apply_overrides_to_snapshot(
        base_snapshot: Dict[str, Any],
        overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Materialise an override dict (dot-paths → values) into a snapshot."""
        from app.services.experiment.evolution import StrategyEvolutionService
        evo = StrategyEvolutionService()
        snap = copy.deepcopy(base_snapshot)
        for key, value in (overrides or {}).items():
            parts = evo._normalize_key(key).split('.')
            evo._set_nested(snap, parts, value)
        return snap

    def _build_candidates(
        self,
        *,
        base_snapshot: Dict[str, Any],
        variants: List[Dict[str, Any]],
        parameter_space: Dict[str, Any],
        evolution: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        candidates = [{
            'name': 'baseline',
            'snapshot': copy.deepcopy(base_snapshot),
            'overrides': {},
            'source': 'baseline',
        }]

        for idx, variant in enumerate(variants, start=1):
            snapshot = copy.deepcopy(base_snapshot)
            overrides = variant.get('overrides') or variant
            for key, value in overrides.items():
                self.evolution_service._set_nested(
                    snapshot,
                    self.evolution_service._normalize_key(key).split('.'),
                    value,
                )
            candidates.append({
                'name': str(variant.get('name') or f'candidate_{idx}'),
                'snapshot': snapshot,
                'overrides': overrides,
                'source': 'manual_variant',
            })

        evo_conf = evolution or {}
        max_variants = int(evo_conf.get('maxVariants') or 0)
        effective_space = parameter_space or evo_conf.get('parameterSpace') or {}
        if effective_space:
            generated = self.evolution_service.build_variants(
                base_snapshot=base_snapshot,
                parameter_space=effective_space,
                max_variants=max_variants or 12,
                method=str(evo_conf.get('method') or 'grid'),
            )
            candidates.extend(generated)

        unique: List[Dict[str, Any]] = []
        seen = set()
        for candidate in candidates:
            key = str(candidate.get('snapshot'))
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _build_snapshot(self, *, base: Dict[str, Any], user_id: int) -> tuple[Dict[str, Any], datetime, datetime]:
        start_date, end_date = self._parse_dates(base)
        snapshot = copy.deepcopy(base.get('snapshot') or {})
        if not snapshot:
            snapshot = {
                'code': base.get('indicatorCode') or base.get('code') or '',
                'market': base.get('market') or 'Crypto',
                'symbol': base.get('symbol') or '',
                'timeframe': base.get('timeframe') or '1D',
                'initial_capital': float(base.get('initialCapital') or 10000),
                'commission': float(base.get('commission') or 0),
                'slippage': float(base.get('slippage') or 0),
                'leverage': int(base.get('leverage') or 1),
                'trade_direction': str(base.get('tradeDirection') or 'long'),
                'strategy_config': base.get('strategyConfig') or {},
                'indicator_params': base.get('indicatorParams') or {},
                'indicator_id': base.get('indicatorId'),
                'user_id': user_id,
                'strict_mode': bool(base.get('strictMode', base.get('strict_mode', True))),
                'run_type': str(base.get('runType') or 'indicator'),
            }
        snapshot['user_id'] = user_id
        return snapshot, start_date, end_date

    @staticmethod
    def _parse_dates(base: Dict[str, Any]) -> tuple[datetime, datetime]:
        start_raw = base.get('startDate') or base.get('start_date')
        end_raw = base.get('endDate') or base.get('end_date')
        if not start_raw or not end_raw:
            raise ValueError('startDate/start_date and endDate/end_date are required (YYYY-MM-DD)')
        start_date = datetime.strptime(str(start_raw), '%Y-%m-%d')
        end_date = datetime.strptime(str(end_raw), '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        return start_date, end_date

    @staticmethod
    def _compute_oos_window(
        start_date: datetime,
        end_date: datetime,
        train_ratio: float = 0.7,
        min_total_days: int = 30,
        min_oos_days: int = 7,
    ) -> Optional[Dict[str, datetime]]:
        """Split the backtest window into in-sample (train) + out-of-sample (test).

        Returns ``None`` when the window is too short to split meaningfully —
        callers should then fall back to the full window. We require at least
        ``min_total_days`` of data and ``min_oos_days`` of OOS to avoid trying
        to validate on a noisy 2-day tail.
        """
        from datetime import timedelta as _td
        if not start_date or not end_date or end_date <= start_date:
            return None
        total_seconds = (end_date - start_date).total_seconds()
        total_days = total_seconds / 86400.0
        if total_days < min_total_days:
            return None
        train_seconds = total_seconds * float(train_ratio)
        train_end = start_date + _td(seconds=train_seconds)
        oos_start = train_end
        oos_days = (end_date - oos_start).total_seconds() / 86400.0
        if oos_days < min_oos_days:
            return None
        return {
            'train_start': start_date,
            'train_end': train_end,
            'oos_start': oos_start,
            'oos_end': end_date,
        }

    def _evaluate_oos(
        self,
        ranked: List[Dict[str, Any]],
        *,
        oos_start: datetime,
        oos_end: datetime,
        regime: Dict[str, Any],
        top_k: int = 5,
        scorer: Optional[StrategyScoringService] = None,
    ) -> List[Dict[str, Any]]:
        """Re-backtest top-K candidates on OOS data and annotate the rank list.

        We attach ``oosScore``/``oosResult`` and a degradation flag so the
        client can flag overfit candidates. The mutation is in-place; the
        same list is returned for chaining.
        """
        if not ranked or oos_end <= oos_start:
            return ranked
        active_scorer = scorer or self.scoring_service
        for candidate in ranked[:top_k]:
            try:
                oos_result = self.backtest_service.run_strategy_snapshot(
                    candidate.get('snapshot') or {},
                    start_date=oos_start,
                    end_date=oos_end,
                )
            except Exception as exc:
                logger.warning(
                    "OOS backtest failed for %s: %s", candidate.get('name'), exc
                )
                oos_result = {}
            oos_score = active_scorer.score_result(oos_result, regime=regime)
            is_overall = float(((candidate.get('score') or {}).get('overallScore') or 0))
            oos_overall = float((oos_score or {}).get('overallScore') or 0)
            degradation = None
            if is_overall > 0:
                degradation = round((is_overall - oos_overall) / is_overall, 4)
            candidate['oosScore'] = oos_score
            candidate['oosResult'] = self._slim_result(oos_result)
            candidate['oosDegradation'] = degradation
            # Severely overfit if OOS score collapses by 40%+ from IS.
            candidate['oosOverfit'] = bool(
                degradation is not None and degradation > 0.4
            )
        return ranked

    @staticmethod
    def _build_generator_hints(regime: Dict[str, Any]) -> Dict[str, Any]:
        families = regime.get('strategyFamilies') or []
        return {
            'preferredFamilies': families[:3],
            'regime': regime.get('regime'),
            'promptHint': (
                f"Focus on {', '.join(families[:2]) or 'robust'} setups under "
                f"{regime.get('label') or 'current'} conditions with risk controls."
            ),
        }

    @staticmethod
    def _build_best_output(best: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Shape the best candidate for the frontend.

        Includes both IS (training-window) and, when available, OOS
        (held-out 30% window) summaries so the UI can show them side
        by side. Previously only IS was returned, which made the
        headline +X% return look like the candidate's real expected
        performance and led to surprises when users re-ran the
        candidate on the full window.
        """
        if not best:
            return None
        result = best.get('result') or {}
        oos_result = best.get('oosResult') or {}
        oos_summary = None
        if oos_result:
            oos_summary = {
                'totalReturn': oos_result.get('totalReturn'),
                'maxDrawdown': oos_result.get('maxDrawdown'),
                'sharpeRatio': oos_result.get('sharpeRatio'),
                'totalTrades': oos_result.get('totalTrades'),
            }
        return {
            'name': best.get('name'),
            'score': best.get('score'),
            'source': best.get('source'),
            'overrides': enrich_experiment_overrides(best.get('overrides') or {}),
            'snapshot': best.get('snapshot'),
            'summary': {
                'totalReturn': result.get('totalReturn'),
                'maxDrawdown': result.get('maxDrawdown'),
                'sharpeRatio': result.get('sharpeRatio'),
                'totalTrades': result.get('totalTrades'),
            },
            'oosSummary': oos_summary,
            'oosScore': best.get('oosScore'),
            'oosDegradation': best.get('oosDegradation'),
            'oosOverfit': bool(best.get('oosOverfit')),
        }
