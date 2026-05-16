"""
GridSearchStrategy — Legacy ChargePlanOptimizer
=================================================

smart_plan_enabled=False ve user override yoksa devreye girer.
65-95% arası sabit grid'i deneyen eski ChargePlanOptimizer.

Eski yer: orchestrator._run_soc_simulation Path B.
"""

from app.soc_simulator import ChargePlanOptimizer
from app.utils.logger import get_logger
from app.optimization.strategies.base import (
    ChargingContext,
    ChargingPlan,
    ChargeTargetStrategy,
)

logger = get_logger("strategy.grid_search")


class GridSearchStrategy(ChargeTargetStrategy):
    """Legacy 65-95% grid search — Pareto kapalıyken kullanılır."""

    @property
    def name(self) -> str:
        return "grid_search"

    def plan(self, ctx: ChargingContext) -> ChargingPlan:
        optimizer = ChargePlanOptimizer(battery_capacity_kwh=ctx.battery_kwh)
        target_soc, sim_result = optimizer.find_optimal_plan(
            segments_with_consumption=ctx.segments_with_consumption,
            total_distance_km=ctx.total_distance_km,
            battery_capacity_kwh=ctx.battery_kwh,
            start_soc=ctx.start_soc,
            target_arrival_soc=ctx.arrival_soc,
            charge_min_soc=ctx.charge_min_soc,
            avg_speed_kmh=ctx.avg_speed_kmh,
        )

        # Per-stop targets: grid search homojen target üretir
        per_stop = [float(target_soc)] * len(sim_result.hotspots)

        logger.info(
            f"GridSearch optimal target_soc={target_soc}%, "
            f"stops={len(sim_result.hotspots)}, final_soc={sim_result.final_soc}%"
        )

        return ChargingPlan(
            charge_target_soc=float(target_soc),
            sim_result=sim_result,
            per_stop_targets=per_stop,
            strategy_name=self.name,
        )
