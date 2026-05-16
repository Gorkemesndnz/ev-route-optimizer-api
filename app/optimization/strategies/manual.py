"""
ManualOverrideStrategy — Kullanıcı override Path
=================================================

Kullanıcı charge_target_soc_percent gönderdiğinde devreye girer.
Optimizasyon yapılmaz; verilen target ile düz simülasyon koşulur.

Eski yer: orchestrator._run_soc_simulation Path A.
"""

from app.soc_simulator import SOCSimulator
from app.utils.logger import get_logger
from app.optimization.strategies.base import (
    ChargingContext,
    ChargingPlan,
    ChargeTargetStrategy,
)

logger = get_logger("strategy.manual")


class ManualOverrideStrategy(ChargeTargetStrategy):
    """Kullanıcı override — sabit target, optimizasyon yok."""

    @property
    def name(self) -> str:
        return "manual"

    def plan(self, ctx: ChargingContext) -> ChargingPlan:
        # user_target_soc_override None ise factory bunu seçmemeli; defansif kontrol.
        target = ctx.user_target_soc_override or ctx.charge_target_soc

        simulator = SOCSimulator(
            battery_capacity_kwh=ctx.battery_kwh,
            start_soc=ctx.start_soc,
            target_arrival_soc=ctx.arrival_soc,
            charge_min_soc=ctx.charge_min_soc,
            charge_target_soc=target,
            user_override_target=True,
        )
        sim_result = simulator.simulate(ctx.segments_with_consumption, ctx.total_distance_km)

        # Per-stop targets: tüm hotspot'lar aynı target alır (homojen plan)
        per_stop = [float(target)] * len(sim_result.hotspots)

        logger.info(
            f"Manual override target_soc={target}%, stops={len(sim_result.hotspots)}, "
            f"final_soc={sim_result.final_soc}%"
        )

        return ChargingPlan(
            charge_target_soc=float(target),
            sim_result=sim_result,
            per_stop_targets=per_stop,
            strategy_name=self.name,
        )
