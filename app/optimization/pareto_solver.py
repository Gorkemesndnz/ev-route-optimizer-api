"""
Pareto Solver — Çok-Kriterli Şarj Stratejisi Çözücü
=====================================================

Greedy "her durakta %80'e şarj" yerine:
1. Backward induction ile her durağın MİN gerekli target_soc'unu al
2. Her durak için [min, 95] arası 5%'lik grid kombinasyonları üret
3. Her kombinasyonu simüle et, J(plan) skorunu hesapla
4. En düşük J'li kombinasyonu seç

5+ duraklı rotalarda kombinasyonel patlama önlemek için greedy-per-stop'a düşer.
"""

import itertools
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from app.soc_simulator import ChargeHotspot, SegmentWithConsumption
from app.charging_model import calculate_charge_time
from app.optimization.modes import OptimizationMode, ParetoWeights, get_weights
from app.optimization.objective import (
    PlanMetrics,
    ObjectiveBreakdown,
    calculate_objective,
)
from app.optimization.backward_planner import (
    PlannedStop,
    plan_backwards,
    MAX_CHARGE_TARGET,
)
from app.utils.logger import get_logger

logger = get_logger("ParetoSolver")


# =============================================================================
# CONSTANTS
# =============================================================================

GRID_STEP = 5                       # %5'lik adım
MAX_PARETO_STOPS = 5                # 5+ stop → greedy fallback
COMFORT_CHARGE_TARGET = 80.0        # Feasible ise yüksek SOC yerine konfor hedefi
DEFAULT_AVG_PRICE_TL_PER_KWH = 8.0  # TR public DC ortalama (Faz 2.5: pricing_service)
DEFAULT_AVG_CHARGER_KW = 100.0      # Faz 2.5: per-stop seçilen istasyon gücü
UNKNOWN_AC_POWER_PLANNING_KW = 22.0
UNKNOWN_DC_POWER_PLANNING_KW = 90.0
UNKNOWN_TYPE_POWER_PLANNING_KW = 50.0
UNKNOWN_POWER_CONFIDENCE_PENALTY_MIN = 8.0
UNKNOWN_AVAILABILITY_CONFIDENCE_PENALTY_MIN = 5.0


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class StationOptimizationInput:
    """Per-stop station data used by station-aware Pareto."""
    stop_index: int
    station_id: str = ""
    station_name: str = ""
    power_kw: float = DEFAULT_AVG_CHARGER_KW
    power_known: bool = True
    charger_type: str = "DC"
    availability_status: str = "available"
    price_tl_per_kwh: float = DEFAULT_AVG_PRICE_TL_PER_KWH
    wait_time_min: float = 0.0
    source_provider: Optional[str] = None
    source_id: Optional[str] = None

    @property
    def normalized_charger_type(self) -> str:
        return (self.charger_type or "").strip().upper()

    @property
    def planning_power_kw(self) -> float:
        if self.power_known and self.power_kw > 0:
            return float(self.power_kw)
        ctype = self.normalized_charger_type
        if ctype == "AC":
            return UNKNOWN_AC_POWER_PLANNING_KW
        if ctype in ("DC", "HPC"):
            return UNKNOWN_DC_POWER_PLANNING_KW
        return UNKNOWN_TYPE_POWER_PLANNING_KW

    @property
    def data_confidence_penalty_min(self) -> float:
        penalty = 0.0
        if not self.power_known:
            penalty += UNKNOWN_POWER_CONFIDENCE_PENALTY_MIN
        if (self.availability_status or "").lower() == "unknown":
            penalty += UNKNOWN_AVAILABILITY_CONFIDENCE_PENALTY_MIN
        return penalty

    @property
    def is_low_confidence(self) -> bool:
        return (not self.power_known) or (self.availability_status or "").lower() == "unknown"


@dataclass
class ParetoSolution:
    """Solver çıktısı."""
    per_stop_target_soc: List[float]
    metrics: PlanMetrics
    breakdown: ObjectiveBreakdown
    planned_stops: List[PlannedStop]
    mode: OptimizationMode
    fallback_used: bool = False  # 5+ stop greedy fallback'e düştü mü?
    station_inputs_used: bool = False
    low_confidence_station_ratio: float = 0.0
    warnings: List[str] = field(default_factory=list)


# =============================================================================
# SOLVER
# =============================================================================

class ParetoSolver:
    """Pareto-weighted çok-kriterli optimizasyon."""

    def __init__(self, mode: OptimizationMode = OptimizationMode.BALANCED):
        self.mode = mode
        self.weights = get_weights(mode)

    def solve(
        self,
        hotspots: List[ChargeHotspot],
        segments_with_consumption: List[SegmentWithConsumption],
        battery_kwh: float,
        start_soc: float,
        arrival_soc_target: float,
        avg_charger_power_kw: float = DEFAULT_AVG_CHARGER_KW,
        avg_price_tl_per_kwh: float = DEFAULT_AVG_PRICE_TL_PER_KWH,
        avg_speed_kmh: float = 80.0,
        weather_per_leg: Optional[List] = None,
        traffic_factors: Optional[List[float]] = None,
        user_anxiety_factor: float = 0.0,
        temperature_c: Optional[float] = None,
        station_inputs: Optional[List[StationOptimizationInput]] = None,
        vehicle_dc_max_kw: Optional[float] = None,
        total_distance_km: float = 500.0,  # 🔧 F-24: normalization ölçekleme için
    ) -> ParetoSolution:
        """
        Ana çözüm fonksiyonu.

        Returns:
            ParetoSolution with per-stop target_soc'lar, metrics, breakdown.
        """
        # 🔧 F-24: Objective normalization'da mesafe ölçeklemesi için sakla
        self._total_distance_km = total_distance_km
        station_inputs_by_stop = self._normalize_station_inputs(station_inputs, len(hotspots))
        low_confidence_ratio = self._low_confidence_ratio(station_inputs_by_stop)
        station_warnings = self._station_warnings(station_inputs_by_stop)

        n = len(hotspots)

        # 0 durak → şarj gerekmez, boş çözüm
        if n == 0:
            return self._no_charge_solution(arrival_soc_target, segments_with_consumption, avg_speed_kmh)

        # 1) Backward induction
        planned = plan_backwards(
            hotspots=hotspots,
            segments=segments_with_consumption,
            battery_kwh=battery_kwh,
            arrival_soc_target=arrival_soc_target,
            weather_per_leg=weather_per_leg,
            traffic_factors=traffic_factors,
            user_anxiety_factor=user_anxiety_factor,
        )

        # 2) Greedy fallback (kombinasyonel patlama önleme)
        if n > MAX_PARETO_STOPS:
            logger.warning(
                f"ParetoSolver: {n} stops > {MAX_PARETO_STOPS} limit, using greedy-per-stop"
            )
            return self._greedy_fallback(
                planned, battery_kwh, start_soc, arrival_soc_target,
                avg_charger_power_kw, avg_price_tl_per_kwh, avg_speed_kmh, temperature_c,
                station_inputs_by_stop, vehicle_dc_max_kw, low_confidence_ratio, station_warnings,
            )

        # 3) Per-stop grid: her durak için [min_target, 95] arası 5%'lik adım
        # MAX_CHARGE_TARGET endpoint explicit ekleniyor; range() bunu garanti etmiyor
        # (örn. range(81, 96, 5) = [81, 86, 91] — 95 missing).
        grids = [
            sorted(set(
                list(range(int(p.min_target_soc), int(MAX_CHARGE_TARGET) + 1, GRID_STEP))
                + ([int(COMFORT_CHARGE_TARGET)] if p.min_target_soc <= COMFORT_CHARGE_TARGET else [])
                + [int(MAX_CHARGE_TARGET)]
            ))
            for p in planned
        ]
        total_combinations = 1
        for g in grids:
            total_combinations *= len(g)

        logger.info(
            f"ParetoSolver mode={self.mode.value}: {n} stops, "
            f"{total_combinations} kombinasyon test edilecek"
        )

        # 4) Tüm kombinasyonları skorla
        best_solution: Optional[ParetoSolution] = None
        best_J = float("inf")

        for combo in itertools.product(*grids):
            metrics = self._evaluate_combo(
                combo=combo,
                planned=planned,
                battery_kwh=battery_kwh,
                start_soc=start_soc,
                arrival_soc_target=arrival_soc_target,
                avg_charger_power_kw=avg_charger_power_kw,
                avg_price_tl_per_kwh=avg_price_tl_per_kwh,
                avg_speed_kmh=avg_speed_kmh,
                temperature_c=temperature_c,
                station_inputs=station_inputs_by_stop,
                vehicle_dc_max_kw=vehicle_dc_max_kw,
            )
            breakdown = calculate_objective(metrics, self.weights, self._total_distance_km)

            if breakdown.J < best_J:
                best_J = breakdown.J
                best_solution = ParetoSolution(
                    per_stop_target_soc=[float(t) for t in combo],
                    metrics=metrics,
                    breakdown=breakdown,
                    planned_stops=planned,
                    mode=self.mode,
                    station_inputs_used=bool(station_inputs_by_stop),
                    low_confidence_station_ratio=low_confidence_ratio,
                    warnings=station_warnings,
                )

        if best_solution is None or best_solution.breakdown.is_hard_violation:
            # Hiçbir kombinasyon güvenli değil — backward planner'ın
            # sınır değerlerini kullan (saf min_target'lar)
            logger.warning("No safe Pareto combo found, falling back to min_target_soc values")
            return self._greedy_fallback(
                planned, battery_kwh, start_soc, arrival_soc_target,
                avg_charger_power_kw, avg_price_tl_per_kwh, avg_speed_kmh, temperature_c,
                station_inputs_by_stop, vehicle_dc_max_kw, low_confidence_ratio, station_warnings,
            )

        logger.info(
            f"ParetoSolver: best J={best_J:.4f} "
            f"target_socs={best_solution.per_stop_target_soc} "
            f"T={best_solution.metrics.total_drive_time_min + best_solution.metrics.total_charge_time_min:.0f}dk "
            f"C={best_solution.metrics.total_cost_tl:.1f}TL"
        )
        return best_solution

    # -------------------------------------------------------------------------
    # COMBO EVALUATION
    # -------------------------------------------------------------------------

    def _evaluate_combo(
        self,
        combo: Tuple[int, ...],
        planned: List[PlannedStop],
        battery_kwh: float,
        start_soc: float,
        arrival_soc_target: float,
        avg_charger_power_kw: float,
        avg_price_tl_per_kwh: float,
        avg_speed_kmh: float,
        temperature_c: Optional[float],
        station_inputs: Optional[List[StationOptimizationInput]] = None,
        vehicle_dc_max_kw: Optional[float] = None,
    ) -> PlanMetrics:
        """
        Bir kombinasyonun ham metriklerini hesapla.

        🔧 F-21: SOC akışı artık dinamik hesaplanıyor.
        Eski kod: her stop için `arrival_at_stop = planned[i].hotspot.soc_at_point`
        → baseline'dan gelen SABİT değer. Pareto target_soc değiştirince bir sonraki
        durağa geliş SOC'u güncellenmiyordu; dolayısıyla şarj süresi/maliyet skorları
        yanlış hesaplanıyordu (özellikle yüksek target_soc tercihinin avantajı gizleniyordu).

        Yeni: İlk durak hâlâ baseline soc_at_point kullanır (çünkü başlangıçtan oraya
        kadar tüketim sabit). Sonraki duraklar için: önceki durak target_soc → leg_kwh
        düşülerek gerçek geliş SOC hesaplanır.
        """
        total_charge_time = 0.0
        total_cost = 0.0
        high_soc_minutes = 0.0
        total_kwh_added = 0.0
        total_drive_time = 0.0
        total_wait_time = 0.0

        # current_soc: son şarj sonrası SOC (ilk durak öncesi = start_soc)
        current_soc = start_soc

        for i, target_soc in enumerate(combo):
            target_soc = float(target_soc)

            # 🔧 F-21: arrival_at_stop dinamik hesap
            # İlk durak: başlangıçtan gelen SOC (baseline soc_at_point güvenilir — tüketim sabit)
            # Sonraki duraklar: önceki durak target_soc'undan leg_kwh düşülerek hesaplanır
            if i == 0:
                # Başlangıçtan ilk durağa: SOC sabit tüketim → baseline güvenilir
                arrival_at_stop = planned[0].hotspot.soc_at_point
            else:
                # Önceki stop target_soc'undan önceki leg tüketimini düş
                prev_leg_soc_drop = (planned[i - 1].leg_to_next_kwh / battery_kwh) * 100.0
                arrival_at_stop = current_soc - prev_leg_soc_drop
                # Gerçekçilik kontrolü: negatif SOC → bu combo fiziksel imkânsız
                if arrival_at_stop < 0.0:
                    # Hard violation — J=inf ile reddedilecek
                    return PlanMetrics(
                        total_drive_time_min=total_drive_time,
                        total_charge_time_min=total_charge_time,
                        total_wait_time_min=0.0,
                        total_cost_tl=total_cost,
                        high_soc_minutes=high_soc_minutes,
                        min_arrival_soc_margin=-999.0,  # hard violation sinyali
                        num_stops=len(combo),
                    )

            # Sürüş süresi (km / hız)
            if i == 0:
                drive_km = planned[i].hotspot.distance_from_start_km
            else:
                drive_km = (planned[i].hotspot.distance_from_start_km
                            - planned[i - 1].hotspot.distance_from_start_km)
            total_drive_time += (drive_km / max(avg_speed_kmh, 1.0)) * 60.0

            # Şarj zamanı: arrival_soc → target_soc
            if target_soc > arrival_at_stop:
                station_input = self._station_for_stop(station_inputs, i)
                station_power_kw = (
                    station_input.planning_power_kw
                    if station_input is not None
                    else avg_charger_power_kw
                )
                if vehicle_dc_max_kw and vehicle_dc_max_kw > 0:
                    station_power_kw = min(station_power_kw, float(vehicle_dc_max_kw))
                price_tl_per_kwh = (
                    station_input.price_tl_per_kwh
                    if station_input is not None
                    else avg_price_tl_per_kwh
                )
                if station_input is not None:
                    total_wait_time += max(0.0, station_input.wait_time_min)
                    total_wait_time += station_input.data_confidence_penalty_min

                charge_result = calculate_charge_time(
                    start_soc=arrival_at_stop,
                    target_soc=target_soc,
                    battery_capacity_kwh=battery_kwh,
                    peak_power_kw=station_power_kw,
                    temperature_c=temperature_c,
                )
                total_charge_time += max(5.0, charge_result.duration_minutes)
                total_cost += charge_result.energy_added_kwh * price_tl_per_kwh
                total_kwh_added += charge_result.energy_added_kwh

                # High SOC süresi: target_soc > 80 olduğu kısımdaki süre
                if target_soc > 80:
                    high_portion = (target_soc - 80) / max(1.0, target_soc - arrival_at_stop)
                    high_soc_minutes += charge_result.duration_minutes * max(0.0, min(1.0, high_portion))

            # 🔧 F-21: current_soc'u güncelle — bir sonraki iterasyon bunu kullanır
            current_soc = target_soc

        # Son durak → varış sürüş süresi
        last_drive_km = planned[-1].leg_to_next_distance_km
        total_drive_time += (last_drive_km / max(avg_speed_kmh, 1.0)) * 60.0

        # Varış SOC: son target'tan son leg'in tüketimini çıkar
        last_leg_soc_drop = (planned[-1].leg_to_next_kwh / battery_kwh) * 100.0
        arrival_soc_actual = combo[-1] - last_leg_soc_drop

        # Marj: gerçek varış - hedef varış
        margin = arrival_soc_actual - arrival_soc_target

        return PlanMetrics(
            total_drive_time_min=total_drive_time,
            total_charge_time_min=total_charge_time,
            total_wait_time_min=total_wait_time,
            total_cost_tl=total_cost,
            high_soc_minutes=high_soc_minutes,
            min_arrival_soc_margin=margin,
            num_stops=len(combo),
        )

    # -------------------------------------------------------------------------
    # FALLBACKS
    # -------------------------------------------------------------------------

    def _greedy_fallback(
        self,
        planned: List[PlannedStop],
        battery_kwh: float,
        start_soc: float,
        arrival_soc_target: float,
        avg_charger_power_kw: float,
        avg_price_tl_per_kwh: float,
        avg_speed_kmh: float,
        temperature_c: Optional[float],
        station_inputs: Optional[List[StationOptimizationInput]] = None,
        vehicle_dc_max_kw: Optional[float] = None,
        low_confidence_ratio: float = 0.0,
        warnings: Optional[List[str]] = None,
    ) -> ParetoSolution:
        """
        5+ stop için greedy fallback.

        80% konfor hedefi feasible ise onu seçer; 80 üstüne yalnızca
        min_required zorlarsa çıkar.
        """
        combo = tuple(
            int(min(MAX_CHARGE_TARGET, max(COMFORT_CHARGE_TARGET, p.min_target_soc)))
            for p in planned
        )
        metrics = self._evaluate_combo(
            combo, planned, battery_kwh, start_soc, arrival_soc_target,
            avg_charger_power_kw, avg_price_tl_per_kwh, avg_speed_kmh, temperature_c,
            station_inputs, vehicle_dc_max_kw,
        )
        dist_km = getattr(self, "_total_distance_km", 500.0)
        breakdown = calculate_objective(metrics, self.weights, dist_km)
        return ParetoSolution(
            per_stop_target_soc=[float(t) for t in combo],
            metrics=metrics,
            breakdown=breakdown,
            planned_stops=planned,
            mode=self.mode,
            fallback_used=True,
            station_inputs_used=bool(station_inputs),
            low_confidence_station_ratio=low_confidence_ratio,
            warnings=warnings or [],
        )

    def _no_charge_solution(
        self,
        arrival_soc_target: float,
        segments_with_consumption: List[SegmentWithConsumption],
        avg_speed_kmh: float,
    ) -> ParetoSolution:
        """0 durak → şarj gerekmez, sadece sürüş süresi."""
        total_distance = sum(
            getattr(getattr(s, "segment", s), "distance_km", 0.0)
            for s in segments_with_consumption
        )
        drive_time = (total_distance / max(avg_speed_kmh, 1.0)) * 60.0
        metrics = PlanMetrics(
            total_drive_time_min=drive_time,
            total_charge_time_min=0.0,
            min_arrival_soc_margin=10.0,  # nominal, gerçek hesap yok
            num_stops=0,
        )
        dist_km = getattr(self, "_total_distance_km", 500.0)
        breakdown = calculate_objective(metrics, self.weights, dist_km)
        return ParetoSolution(
            per_stop_target_soc=[],
            metrics=metrics,
            breakdown=breakdown,
            planned_stops=[],
            mode=self.mode,
        )

    @staticmethod
    def _station_for_stop(
        station_inputs: Optional[List[StationOptimizationInput]],
        index: int,
    ) -> Optional[StationOptimizationInput]:
        if not station_inputs or index >= len(station_inputs):
            return None
        return station_inputs[index]

    @staticmethod
    def _normalize_station_inputs(
        station_inputs: Optional[List[StationOptimizationInput]],
        stop_count: int,
    ) -> Optional[List[StationOptimizationInput]]:
        if not station_inputs:
            return None
        normalized = sorted(station_inputs, key=lambda s: s.stop_index)
        if len(normalized) < stop_count:
            logger.warning(
                "ParetoSolver: station input count is lower than hotspot count",
                station_inputs=len(normalized),
                hotspots=stop_count,
            )
        return normalized[:stop_count]

    @staticmethod
    def _low_confidence_ratio(
        station_inputs: Optional[List[StationOptimizationInput]],
    ) -> float:
        if not station_inputs:
            return 0.0
        low_count = sum(1 for station in station_inputs if station.is_low_confidence)
        return low_count / len(station_inputs)

    @staticmethod
    def _station_warnings(
        station_inputs: Optional[List[StationOptimizationInput]],
    ) -> List[str]:
        if not station_inputs:
            return ["Pareto used average charger assumptions; no station inputs were provided."]
        warnings: List[str] = []
        for station in station_inputs:
            label = station.station_name or station.station_id or f"stop {station.stop_index + 1}"
            if not station.power_known:
                warnings.append(
                    f"{label}: charger power is estimated at {station.planning_power_kw:.0f} kW."
                )
            if (station.availability_status or "").lower() == "unknown":
                warnings.append(f"{label}: availability is unknown.")
        return warnings
