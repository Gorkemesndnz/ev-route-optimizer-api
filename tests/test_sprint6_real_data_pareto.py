from dataclasses import dataclass
from typing import Optional, List

from app.optimization.modes import OptimizationMode
from app.optimization.pareto_solver import ParetoSolver, StationOptimizationInput


@dataclass
class _H:
    segment_index: int
    soc_at_point: float
    distance_from_start_km: float
    location: Optional[object] = None
    remaining_distance_km: float = 0.0
    min_required_soc: float = 15.0
    recommended_charge_to: float = 80.0


@dataclass
class _S:
    index: int
    distance_km: float
    elevation_gain_m: float = 0.0
    cumulative_distance_km: float = 0.0
    start_point: Optional[object] = None
    end_point: Optional[object] = None


@dataclass
class _SC:
    segment: _S
    consumption_kwh: float


def _single_stop_scenario():
    segments: List[_SC] = []
    for i in range(20):
        segments.append(_SC(
            segment=_S(index=i, distance_km=10.0, cumulative_distance_km=10.0 * (i + 1)),
            consumption_kwh=2.0,
        ))
    hotspots = [_H(segment_index=9, soc_at_point=20.0, distance_from_start_km=100.0)]
    return hotspots, segments


def test_station_kw_changes_pareto_charge_time():
    hotspots, segments = _single_stop_scenario()
    slow = StationOptimizationInput(stop_index=0, power_kw=50.0, power_known=True, charger_type="DC")
    fast = StationOptimizationInput(stop_index=0, power_kw=200.0, power_known=True, charger_type="DC")

    slow_solution = ParetoSolver(OptimizationMode.BALANCED).solve(
        hotspots=hotspots,
        segments_with_consumption=segments,
        battery_kwh=60.0,
        start_soc=85.0,
        arrival_soc_target=10.0,
        total_distance_km=200.0,
        station_inputs=[slow],
    )
    fast_solution = ParetoSolver(OptimizationMode.BALANCED).solve(
        hotspots=hotspots,
        segments_with_consumption=segments,
        battery_kwh=60.0,
        start_soc=85.0,
        arrival_soc_target=10.0,
        total_distance_km=200.0,
        station_inputs=[fast],
    )

    assert slow_solution.station_inputs_used is True
    assert fast_solution.metrics.total_charge_time_min < slow_solution.metrics.total_charge_time_min


def test_unknown_dc_uses_90kw_and_data_confidence_penalty():
    station = StationOptimizationInput(
        stop_index=0,
        power_kw=0.0,
        power_known=False,
        charger_type="DC",
        availability_status="unknown",
    )
    assert station.planning_power_kw == 90.0
    assert station.data_confidence_penalty_min == 13.0

    hotspots, segments = _single_stop_scenario()
    solution = ParetoSolver(OptimizationMode.BALANCED).solve(
        hotspots=hotspots,
        segments_with_consumption=segments,
        battery_kwh=60.0,
        start_soc=85.0,
        arrival_soc_target=10.0,
        total_distance_km=200.0,
        station_inputs=[station],
    )

    assert solution.low_confidence_station_ratio == 1.0
    assert solution.metrics.total_wait_time_min >= 13.0
    assert any("estimated at 90 kW" in warning for warning in solution.warnings)


def test_known_available_station_scores_better_than_unknown_when_other_inputs_match():
    hotspots, segments = _single_stop_scenario()
    known = StationOptimizationInput(
        stop_index=0,
        power_kw=90.0,
        power_known=True,
        charger_type="DC",
        availability_status="available",
    )
    unknown = StationOptimizationInput(
        stop_index=0,
        power_kw=90.0,
        power_known=True,
        charger_type="DC",
        availability_status="unknown",
    )

    known_solution = ParetoSolver(OptimizationMode.BALANCED).solve(
        hotspots=hotspots,
        segments_with_consumption=segments,
        battery_kwh=60.0,
        start_soc=85.0,
        arrival_soc_target=10.0,
        total_distance_km=200.0,
        station_inputs=[known],
    )
    unknown_solution = ParetoSolver(OptimizationMode.BALANCED).solve(
        hotspots=hotspots,
        segments_with_consumption=segments,
        battery_kwh=60.0,
        start_soc=85.0,
        arrival_soc_target=10.0,
        total_distance_km=200.0,
        station_inputs=[unknown],
    )

    assert known_solution.breakdown.J < unknown_solution.breakdown.J
    assert unknown_solution.low_confidence_station_ratio == 1.0
