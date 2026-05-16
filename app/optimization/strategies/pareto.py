"""
ParetoStrategy — Multi-objective Pareto Optimization
=====================================================

smart_plan_enabled=True ve user override yoksa devreye girer.

Akış:
  1) Baseline simülasyon (MAX_CHARGE_TARGET=%95, skip_smart_targets=True)
  2) ParetoSolver per-stop target_soc'ları üretir
  3) Final simülasyon per_stop_targets ile yeniden çalışır
  4) Pareto target → hotspot.recommended_charge_to senkronu (safety net)
  5) DecisionLogger karar kaydını JSONL'a yazar (fail-soft)

Hata durumunda fail-soft: GridSearchStrategy'ye düşer.
"""

from app.soc_simulator import SOCSimulator
from app.utils.logger import get_logger
from app.optimization.strategies.base import (
    ChargingContext,
    ChargingPlan,
    ChargeTargetStrategy,
)

logger = get_logger("strategy.pareto")


class ParetoStrategy(ChargeTargetStrategy):
    """Pareto multi-objective optimization."""

    @property
    def name(self) -> str:
        return "pareto"

    def plan(self, ctx: ChargingContext) -> ChargingPlan:
        # Lazy imports — soft circular önleme
        from app.optimization.modes import OptimizationMode
        from app.optimization.pareto_solver import ParetoSolver
        from app.optimization.decision_logger import build_record, get_decision_logger
        from app.optimization.backward_planner import MAX_CHARGE_TARGET

        # 0) Mode parse
        try:
            mode = OptimizationMode(ctx.optimization_mode)
        except (ValueError, AttributeError):
            mode = OptimizationMode.BALANCED

        # 1) Baseline — hotspot tespiti (MAX_CHARGE_TARGET, skip_smart_targets=True)
        baseline_sim = SOCSimulator(
            battery_capacity_kwh=ctx.battery_kwh,
            start_soc=ctx.start_soc,
            target_arrival_soc=ctx.arrival_soc,
            charge_min_soc=ctx.charge_min_soc,
            charge_target_soc=MAX_CHARGE_TARGET,
        )
        baseline_result = baseline_sim.simulate(
            ctx.segments_with_consumption,
            ctx.total_distance_km,
            skip_smart_targets=True,
        )

        # No charging needed → erken çıkış
        if not baseline_result.hotspots:
            logger.info("Pareto: no charging needed, returning baseline")
            return ChargingPlan(
                charge_target_soc=ctx.charge_target_soc,
                sim_result=baseline_result,
                per_stop_targets=[],
                strategy_name=self.name,
            )

        # 2) Pareto solver
        solver = ParetoSolver(mode=mode)
        try:
            solution = solver.solve(
                hotspots=baseline_result.hotspots,
                segments_with_consumption=ctx.segments_with_consumption,
                battery_kwh=ctx.battery_kwh,
                start_soc=ctx.start_soc,
                arrival_soc_target=ctx.arrival_soc,
                avg_speed_kmh=ctx.avg_speed_kmh,
                total_distance_km=ctx.total_distance_km,
            )
        except Exception as e:
            logger.warning(f"Pareto solver failed: {e}, falling back to grid search")
            # Fail-soft: GridSearchStrategy'ye düş (ChargingPlan dönüşü ile)
            from app.optimization.strategies.grid_search import GridSearchStrategy
            fallback_plan = GridSearchStrategy().plan(ctx)
            return ChargingPlan(
                charge_target_soc=fallback_plan.charge_target_soc,
                sim_result=fallback_plan.sim_result,
                per_stop_targets=fallback_plan.per_stop_targets,
                strategy_name=f"{self.name}_fallback_grid",
                fallback_used=True,
            )

        # 3) Final sim — Pareto per_stop_targets ile
        final_sim = SOCSimulator(
            battery_capacity_kwh=ctx.battery_kwh,
            start_soc=ctx.start_soc,
            target_arrival_soc=ctx.arrival_soc,
            charge_min_soc=ctx.charge_min_soc,
            charge_target_soc=ctx.charge_target_soc,
        )
        final_result = final_sim.simulate(
            ctx.segments_with_consumption,
            ctx.total_distance_km,
            per_stop_targets=solution.per_stop_target_soc,
        )

        # 4) Pareto target → hotspot.recommended_charge_to senkronu (safety net)
        n_pareto = len(solution.per_stop_target_soc)
        n_final = len(final_result.hotspots)
        if n_pareto != n_final:
            logger.warning(
                f"Stop mismatch: Pareto {n_pareto} stop planladı, "
                f"final_sim {n_final} stop üretti. "
                f"İlk {min(n_pareto, n_final)} stop Pareto target alıyor, fazla stoplar fallback."
            )
        for hotspot, target in zip(final_result.hotspots, solution.per_stop_target_soc):
            hotspot.recommended_charge_to = float(target)

        # 5) Decision log (fail-soft)
        try:
            record = build_record(
                origin=(ctx.origin_lat or 0.0, ctx.origin_lon or 0.0),
                destination=(ctx.destination_lat or 0.0, ctx.destination_lon or 0.0),
                total_distance_km=ctx.total_distance_km,
                vehicle_id=ctx.vehicle_id,
                battery_kwh=ctx.battery_kwh,
                start_soc=ctx.start_soc,
                arrival_soc_target=ctx.arrival_soc,
                smart_plan_enabled=True,
                optimization_mode=mode.value,
                weights=solver.weights.model_dump(),
                pareto_solution=solution,
                trip_id=ctx.trip_id,
            )
            get_decision_logger().write(record)
        except Exception as e:
            logger.warning(f"DecisionLogger build/write failed (non-fatal): {e}")

        # En yüksek per-stop target'ı temsili olarak ver (geriye uyumlu API için)
        avg_target = (
            max(solution.per_stop_target_soc) if solution.per_stop_target_soc
            else ctx.charge_target_soc
        )
        logger.info(
            f"Pareto solution: mode={mode.value}, stops={n_pareto}, "
            f"targets={solution.per_stop_target_soc}, "
            f"J={solution.breakdown.J:.4f}, fallback_used={solution.fallback_used}"
        )

        return ChargingPlan(
            charge_target_soc=avg_target,
            sim_result=final_result,
            per_stop_targets=[float(t) for t in solution.per_stop_target_soc],
            strategy_name=self.name,
            fallback_used=solution.fallback_used,
            metadata={
                "mode": mode.value,
                "objective_J": solution.breakdown.J,
                "weights": solver.weights.model_dump(),
            },
        )
