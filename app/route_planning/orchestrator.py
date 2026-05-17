"""
Route Planner v3.0
===================

Clean Architecture - Modüler yapı.

Akis:
1. Route Selector - En iyi rota
2. Google Elevation - Rakim verisi
3. Route Segmenter V2 - Geometrik segmentler
4. Main Calculator - Her segment icin tuketim (TEK KAYNAK)
5. SOC Simulator - SOC simulasyonu + Hotspot tespiti
6. Station Finder - Hotspotlara istasyon

Modüller (route_planning/):
- soc_params: SOC parametreleri ve varsayılanlar
- weather_pipeline: Hava durumu pipeline
- leg_builder: Multi-leg yapısı oluşturma
- response_builder: Final response + uyarılar
"""

import asyncio
import os
import time
from dataclasses import asdict
from typing import List, Optional, Dict, Any

from app.models import (
    RouteRequest, 
    MultiStopRouteResponse, 
    GeoPoint,
    WeatherInfo,
    WeatherCondition,
    DecisionReason,
    PlanQuality,
)

from app.soc_simulator import SOCSimulator
# NOT: ChargePlanOptimizer artık doğrudan kullanılmıyor — GridSearchStrategy içine taşındı.
from app.infrastructure.vehicle_catalog import get_vehicle_model, resolve_vehicle_spec
from app.route_selector import find_best_route
from app.routing import (
    CanonicalRoute,
    ConsumptionEngineEnergyEstimator,
    EnergyEstimateRequest,
    SegmentFeatureBuilder,
)
from app.services.weather_service import WeatherService
from app.services.google_service import google_maps
from app.utils.logger import get_logger
from app.constants import (
    HARD_MIN_SOC,
    TARGET_ARRIVAL_SOC,
    TARGET_CHARGE_MIN_SOC,
    MIN_CHARGE_THRESHOLD_PERCENT,
    DEFAULT_PASSENGER_COUNT,
    DEFAULT_CHILD_COUNT,
    DEFAULT_EXTRA_LOAD_KG,
    DEFAULT_TEMPERATURE_C,
    DEFAULT_CHARGE_TARGET_SOC,
    MIN_SOC_RANGE,
    TARGET_SOC_RANGE,
    ARRIVAL_SOC_RANGE
)

# --- Modüler alt bileşenler ---
from app.route_planning.soc_params import (
    resolve_defaults,
    calculate_base_soc_params,
    create_error_response,
)
from app.route_planning.weather_pipeline import (
    extract_weather_from_forecast,
    fetch_weather_checkpoints,
    fetch_start_end_weather,
    refine_weather_pass2,
)
from app.route_planning.leg_builder import build_multi_legs
from app.route_planning.response_builder import (
    calculate_co2,
    build_message,
    build_warnings,
    build_route_response,
    log_training_data,
    validate_plan_sanity,
    validate_plan_feasibility,
)
from app.services.insight_service import insight_engine
from app.optimization.decision_logger import build_route_decision_record, get_decision_logger
from app.utils.cache_manager import begin_cache_trace, end_cache_trace, get_cache_metrics_snapshot

logger = get_logger("route_planner")
weather_service = WeatherService()
DEFAULT_MAX_LOGGED_STATION_CANDIDATES = 50
DEFAULT_MAX_LOGGED_SEGMENT_FEATURES = 80
MAX_HOTSPOT_STATION_ALIGNMENT_DRIFT_KM = 120.0


# =============================================================================
# Backward Compatibility Re-exports
# =============================================================================
# Test ve diğer modüller bu fonksiyonları doğrudan route_planner'dan import eder.
# Yeni konumlarına yönlendir.
_resolve_defaults = resolve_defaults
_calculate_base_soc_params = calculate_base_soc_params
_create_error_response = create_error_response
_extract_weather_from_forecast = extract_weather_from_forecast


# =============================================================================
# SOC Simülasyonu — Strategy Pattern (Refactor 1, 2026-05-05)
# =============================================================================
# Eski 3-yollu if/elif (manual / grid / Pareto) tek interface altına alındı.
# select_strategy(ctx) doğru implementation'ı döndürür → strategy.plan(ctx)
# uniform ChargingPlan döndürür. Dış API geriye uyumlu (charge_target_soc, sim_result).

def _run_soc_simulation(
    segments_with_consumption,
    route_distance_km: float,
    battery_kwh: float,
    start_soc: float,
    arrival_soc: float,
    charge_min_soc: float,
    charge_target_soc: float,
    user_target_soc_override,
    avg_speed_kmh: float,
    request=None,
    trip_id: Optional[str] = None,
):
    """
    SOC simülasyonu çalıştır (Pass 1 ve Pass 2 için ortak).

    Refactor 1: Strategy pattern — manual/grid/pareto seçimi factory'ye delege.

    Returns:
        (charge_target_soc, sim_result)  — geriye uyumlu tuple
    """
    from app.optimization.strategies import ChargingContext, select_strategy

    smart_plan_enabled = bool(getattr(request, "smart_plan_enabled", False)) if request else False
    optimization_mode = getattr(request, "optimization_mode", "balanced") if request else "balanced"

    # ChargingContext'i RouteRequest'ten doldur (decoupling)
    ctx = ChargingContext(
        segments_with_consumption=segments_with_consumption,
        total_distance_km=route_distance_km,
        battery_kwh=battery_kwh,
        start_soc=start_soc,
        arrival_soc=arrival_soc,
        charge_min_soc=charge_min_soc,
        charge_target_soc=charge_target_soc,
        avg_speed_kmh=avg_speed_kmh,
        smart_plan_enabled=smart_plan_enabled,
        user_target_soc_override=user_target_soc_override,
        optimization_mode=optimization_mode,
        trip_id=trip_id,
        origin_lat=getattr(getattr(request, "start_location", None), "lat", None) if request else None,
        origin_lon=getattr(getattr(request, "start_location", None), "lon", None) if request else None,
        destination_lat=getattr(getattr(request, "end_location", None), "lat", None) if request else None,
        destination_lon=getattr(getattr(request, "end_location", None), "lon", None) if request else None,
        vehicle_id=getattr(request, "vehicle_model_id", "") or "unknown" if request else "unknown",
    )

    strategy = select_strategy(ctx)
    plan = strategy.plan(ctx)

    logger.info(f"Strategy={plan.strategy_name}, fallback_used={plan.fallback_used}")

    return plan.charge_target_soc, plan.sim_result


def _align_station_results_to_hotspots(hotspots, station_results):
    if not station_results:
        return station_results
    if not hotspots:
        logger.warning(
            "Hotspot count changed to zero; dropping station results to avoid stale station legs",
            station_results=len(station_results),
        )
        return []
    if len(hotspots) == len(station_results):
        _validate_station_alignment(hotspots, station_results)
        return station_results

    remaining = list(station_results)
    aligned = []
    for hotspot in hotspots:
        if not remaining:
            raise ValueError(
                "Hotspot/station alignment incomplete: "
                f"{len(hotspots)} final hotspots but only {len(station_results)} station results"
            )
        target_km = float(getattr(hotspot, "distance_from_start_km", 0.0) or 0.0)
        selected = min(
            remaining,
            key=lambda result: abs(
                float(getattr(getattr(result, "hotspot", None), "distance_from_start_km", target_km) or target_km)
                - target_km
            ),
        )
        drift_km = abs(_station_result_hotspot_distance_km(selected, target_km) - target_km)
        if drift_km > MAX_HOTSPOT_STATION_ALIGNMENT_DRIFT_KM:
            raise ValueError(
                "Hotspot/station alignment drift exceeds tolerance: "
                f"target={target_km:.1f}km drift={drift_km:.1f}km "
                f"tolerance={MAX_HOTSPOT_STATION_ALIGNMENT_DRIFT_KM:.1f}km"
            )
        aligned.append(selected)
        remaining.remove(selected)

    logger.warning(
        "Hotspot/station result count changed after final reroute; station results realigned by route distance",
        final_hotspots=len(hotspots),
        previous_station_results=len(station_results),
        aligned_station_results=len(aligned),
    )
    return aligned


def _station_result_hotspot_distance_km(station_result, fallback_km: float) -> float:
    return float(
        getattr(getattr(station_result, "hotspot", None), "distance_from_start_km", fallback_km)
        or fallback_km
    )


def _validate_station_alignment(hotspots, station_results) -> None:
    if len(hotspots) != len(station_results):
        raise ValueError(
            "Hotspot/station alignment count mismatch: "
            f"{len(hotspots)} final hotspots vs {len(station_results)} station results"
        )

    for hotspot, station_result in zip(hotspots, station_results):
        target_km = float(getattr(hotspot, "distance_from_start_km", 0.0) or 0.0)
        drift_km = abs(_station_result_hotspot_distance_km(station_result, target_km) - target_km)
        if drift_km > MAX_HOTSPOT_STATION_ALIGNMENT_DRIFT_KM:
            raise ValueError(
                "Hotspot/station alignment drift exceeds tolerance: "
                f"target={target_km:.1f}km drift={drift_km:.1f}km "
                f"tolerance={MAX_HOTSPOT_STATION_ALIGNMENT_DRIFT_KM:.1f}km"
            )


def _combine_final_route_waypoints(user_waypoints, station_points) -> List[GeoPoint]:
    """Final reroute order is user-defined waypoints first, then charging stops."""
    return list(user_waypoints or []) + list(station_points or [])


async def _snap_display_polyline_for_roads(polyline: str, snap_to_roads) -> tuple[str, bool]:
    """Return the display polyline after a successful Roads snap, preserving fail-soft behavior."""
    display_polyline = polyline
    snap_called = False
    if not polyline:
        return display_polyline, snap_called

    try:
        import polyline as polyline_lib

        decoded = polyline_lib.decode(polyline)
        if not decoded:
            return display_polyline, snap_called

        sample_interval_km = 5.0
        max_snap_points = 400

        sampled_points: List[GeoPoint] = []
        if len(decoded) <= max_snap_points:
            sampled_points = [GeoPoint(lat=lat, lon=lon) for lat, lon in decoded]
        else:
            from app.utils.geo import haversine_km

            accumulated = 0.0
            sampled_points.append(GeoPoint(lat=decoded[0][0], lon=decoded[0][1]))
            last_pt = decoded[0]
            for lat, lon in decoded[1:]:
                accumulated += haversine_km(last_pt[0], last_pt[1], lat, lon)
                if accumulated >= sample_interval_km:
                    sampled_points.append(GeoPoint(lat=lat, lon=lon))
                    accumulated = 0.0
                last_pt = (lat, lon)

            if (decoded[-1][0], decoded[-1][1]) != (sampled_points[-1].lat, sampled_points[-1].lon):
                sampled_points.append(GeoPoint(lat=decoded[-1][0], lon=decoded[-1][1]))

        snap_called = True
        snapped = await snap_to_roads(sampled_points, interpolate=True)
        if not snapped:
            return display_polyline, snap_called

        snapped_coords = [
            (sp["location"]["latitude"], sp["location"]["longitude"])
            for sp in snapped
            if "location" in sp
        ]
        if snapped_coords:
            display_polyline = polyline_lib.encode(snapped_coords)
            logger.info(
                f"Polyline snapped to roads: {len(decoded)} -> {len(sampled_points)} sampled "
                f"-> {len(snapped_coords)} snapped points"
            )
    except Exception as e:
        logger.warning(f"Snap-to-roads failed, keeping original polyline: {e}")

    return display_polyline, snap_called


def _station_charger_type(station) -> str:
    power_kw = float(getattr(station, "power_kw", 0.0) or 0.0)
    if getattr(station, "is_dc", False):
        return "DC"
    if power_kw >= 150:
        return "HPC"
    if power_kw >= 40:
        return "DC"
    if power_kw > 0:
        return "AC"
    return "UNKNOWN"


def _build_station_optimization_inputs(station_results):
    from app.optimization.pareto_solver import StationOptimizationInput
    from app.services.pricing_service import pricing_service

    station_inputs = []
    for index, result in enumerate(station_results or []):
        station = getattr(result, "best_station", None)
        if not station:
            continue
        availability_status = getattr(station, "availability_status", None) or "unknown"
        if availability_status == "unavailable":
            continue

        charger_type = _station_charger_type(station)
        power_kw = float(getattr(station, "power_kw", 0.0) or 0.0)
        power_known = bool(getattr(station, "power_known", power_kw > 0))
        price_power_kw = power_kw if power_kw > 0 else 90.0
        socket_type = "AC" if charger_type == "AC" else "DC"
        price_tl = pricing_service.get_price_per_kwh(
            getattr(station, "station_name", "") or "",
            power_kw=price_power_kw,
            socket_type=socket_type,
        )

        station_inputs.append(StationOptimizationInput(
            stop_index=index,
            station_id=str(getattr(station, "station_id", "") or ""),
            station_name=str(getattr(station, "station_name", "") or ""),
            power_kw=power_kw,
            power_known=power_known,
            charger_type=charger_type,
            availability_status=availability_status,
            price_tl_per_kwh=float(price_tl),
            wait_time_min=0.0,
            source_provider=getattr(station, "source_provider", None),
            source_id=getattr(station, "source_id", None),
        ))
    return station_inputs


def _run_station_aware_pareto(
    *,
    hotspots,
    station_results,
    segments_with_consumption,
    route_distance_km: float,
    battery_kwh: float,
    start_soc: float,
    arrival_soc: float,
    charge_min_soc: float,
    charge_target_soc: float,
    avg_speed_kmh: float,
    request: RouteRequest,
    vehicle,
    temperature_c: Optional[float],
):
    if not hotspots or not station_results:
        return None
    if not bool(getattr(request, "smart_plan_enabled", False)):
        return None
    if getattr(request, "charge_target_soc_percent", None) is not None:
        return None

    from app.optimization.modes import OptimizationMode
    from app.optimization.pareto_solver import ParetoSolver

    station_inputs = _build_station_optimization_inputs(station_results)
    if not station_inputs:
        return None

    try:
        mode = OptimizationMode(getattr(request, "optimization_mode", "balanced"))
    except (ValueError, AttributeError):
        mode = OptimizationMode.BALANCED

    solver = ParetoSolver(mode=mode)
    solution = solver.solve(
        hotspots=hotspots,
        segments_with_consumption=segments_with_consumption,
        battery_kwh=battery_kwh,
        start_soc=start_soc,
        arrival_soc_target=arrival_soc,
        avg_speed_kmh=avg_speed_kmh,
        temperature_c=temperature_c,
        total_distance_km=route_distance_km,
        station_inputs=station_inputs,
        vehicle_dc_max_kw=getattr(vehicle, "dc_max_kw", None),
    )

    sim = SOCSimulator(
        battery_capacity_kwh=battery_kwh,
        start_soc=start_soc,
        target_arrival_soc=arrival_soc,
        charge_min_soc=charge_min_soc,
        charge_target_soc=charge_target_soc,
    )
    final_result = sim.simulate(
        segments_with_consumption,
        route_distance_km,
        per_stop_targets=solution.per_stop_target_soc,
    )
    for hotspot, target in zip(final_result.hotspots, solution.per_stop_target_soc):
        hotspot.recommended_charge_to = float(target)

    return solution, final_result, station_inputs


def _build_plan_quality(
    *,
    station_aware_solution=None,
    station_inputs=None,
    warning_messages=None,
    missing_station_warnings=None,
    feasibility_error=None,
    call_counts=None,
    provider_versions=None,
    cache_metrics=None,
) -> PlanQuality:
    warnings = []
    fallback_reasons = []
    fallback_used = False
    low_confidence_ratio = 0.0

    if station_aware_solution is not None:
        warnings.extend(getattr(station_aware_solution, "warnings", []) or [])
        fallback_used = bool(getattr(station_aware_solution, "fallback_used", False))
        low_confidence_ratio = float(getattr(station_aware_solution, "low_confidence_station_ratio", 0.0) or 0.0)
        if fallback_used:
            fallback_reasons.append("fallback_grid")
    elif station_inputs is None:
        warnings.append("Station-aware Pareto was not used for this plan.")

    warnings.extend(warning_messages or [])
    warnings.extend(missing_station_warnings or [])

    return PlanQuality(
        warnings=warnings,
        fallback_used=fallback_used,
        fallback_reasons=fallback_reasons,
        provider_versions={
            "station_catalog": "normalized_v1",
            "pareto": "station_aware_v1" if station_aware_solution is not None else "legacy_v1",
            **(provider_versions or {}),
        },
        call_counts=call_counts or {},
        cache=cache_metrics or {},
        low_confidence_station_ratio=round(low_confidence_ratio, 3),
        validation_summary={
            "station_inputs": len(station_inputs or []),
            "missing_station_warnings": len(missing_station_warnings or []),
            "feasibility_error": feasibility_error,
        },
    )


# NOT: _run_pareto_simulation kaldırıldı (Refactor 1, 2026-05-05).
# Mantığı app/optimization/strategies/pareto.py → ParetoStrategy.plan() içine taşındı.
# Geriye uyumluluk için bu fonksiyonu çağıran modül kalmadı (grep ile doğrulandı).


# =============================================================================
# ANA ORKESTRASYON
# =============================================================================
def _vehicle_decision_spec(vehicle) -> Dict[str, Any]:
    return {
        "id": getattr(vehicle, "id", None),
        "slug": getattr(vehicle, "slug", None),
        "brand": getattr(vehicle, "brand", None),
        "model": getattr(vehicle, "model", None),
        "variant": getattr(vehicle, "variant", None),
        "battery_kwh": getattr(vehicle, "battery_capacity_kwh", None),
        "dc_max_kw": getattr(vehicle, "dc_max_kw", None),
        "has_heat_pump": getattr(vehicle, "has_heat_pump", None),
        "battery_preconditioning": getattr(vehicle, "battery_preconditioning", None),
        "connector_type": getattr(vehicle, "connector_type", None),
        "curb_weight_kg": getattr(vehicle, "curb_weight_kg", None),
    }


def _segment_summary(
    segments_with_consumption,
    *,
    avg_weather: Optional[WeatherInfo] = None,
    checkpoint_weather=None,
    elevation_gain_m: float = 0.0,
    elevation_loss_m: float = 0.0,
    traffic_ratio: Optional[float] = None,
) -> Dict[str, Any]:
    consumptions = [float(getattr(seg, "consumption_kwh", 0.0) or 0.0) for seg in (segments_with_consumption or [])]
    distances = [
        float(getattr(getattr(seg, "segment", None), "distance_km", 0.0) or 0.0)
        for seg in (segments_with_consumption or [])
    ]
    total_distance = sum(distances)
    checkpoint_values = [
        item[1] for item in (checkpoint_weather or [])
        if isinstance(item, tuple) and len(item) >= 2 and item[1] is not None
    ]
    weather_values = ([avg_weather] if avg_weather else []) + checkpoint_values
    temps = [float(getattr(w, "temp_c", 0.0)) for w in weather_values]
    winds = [float(getattr(w, "wind_speed_mps", 0.0)) for w in weather_values]
    pops = [float(getattr(w, "precipitation_prob", 0.0) or 0.0) for w in weather_values]
    return {
        "count": len(consumptions),
        "total_distance_km": round(total_distance, 2),
        "total_consumption_kwh": round(sum(consumptions), 3),
        "max_segment_consumption_kwh": round(max(consumptions), 3) if consumptions else 0.0,
        "avg_consumption_wh_km": round((sum(consumptions) / total_distance) * 1000, 1) if total_distance > 0 else None,
        "elevation_gain_m": round(float(elevation_gain_m or 0.0), 1),
        "elevation_loss_m": round(float(elevation_loss_m or 0.0), 1),
        "traffic_ratio": round(float(traffic_ratio), 3) if traffic_ratio is not None else None,
        "weather_checkpoint_count": len(checkpoint_values),
        "avg_temp_c": round(sum(temps) / len(temps), 1) if temps else None,
        "min_temp_c": round(min(temps), 1) if temps else None,
        "max_temp_c": round(max(temps), 1) if temps else None,
        "p95_temp_c": round(_percentile(temps, 0.95), 1) if temps else None,
        "max_wind_speed_mps": round(max(winds), 1) if winds else None,
        "p95_wind_speed_mps": round(_percentile(winds, 0.95), 1) if winds else None,
        "max_precipitation_prob": round(max(pops), 3) if pops else None,
        "p95_precipitation_prob": round(_percentile(pops, 0.95), 3) if pops else None,
        "segment_feature_vector": _segment_feature_vector(
            segments_with_consumption,
            checkpoint_weather,
            traffic_ratio,
        ),
    }


def _log_limit_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(f"Invalid {name}={raw!r}; using default={default}")
        return default


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _weather_at_segment(segment, checkpoint_weather):
    if not checkpoint_weather:
        return None
    segment_km = float(getattr(segment, "cumulative_distance_km", 0.0) or 0.0)
    best = None
    best_delta = float("inf")
    for item in checkpoint_weather or []:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        checkpoint, weather = item[0], item[1]
        if weather is None:
            continue
        checkpoint_km = float(getattr(checkpoint, "cumulative_km", segment_km) or 0.0)
        delta = abs(checkpoint_km - segment_km)
        if delta < best_delta:
            best = weather
            best_delta = delta
    return best


def _segment_feature_vector(segments_with_consumption, checkpoint_weather, traffic_ratio: Optional[float]) -> Dict[str, Any]:
    cap = _log_limit_from_env("LOG_DECISIONS_MAX_SEGMENT_FEATURES", DEFAULT_MAX_LOGGED_SEGMENT_FEATURES)
    items = []
    for seg_with_cons in list(segments_with_consumption or [])[:cap]:
        segment = getattr(seg_with_cons, "segment", None)
        distance_km = float(getattr(segment, "distance_km", 0.0) or 0.0)
        consumption_kwh = float(getattr(seg_with_cons, "consumption_kwh", 0.0) or 0.0)
        weather = _weather_at_segment(segment, checkpoint_weather)
        item = {
            "index": int(getattr(segment, "index", len(items)) or 0),
            "distance_km": round(distance_km, 2),
            "cumulative_distance_km": round(float(getattr(segment, "cumulative_distance_km", 0.0) or 0.0), 2),
            "consumption_kwh": round(consumption_kwh, 3),
            "consumption_wh_km": round((consumption_kwh / distance_km) * 1000, 1) if distance_km > 0 else None,
            "elevation_gain_m": round(float(getattr(segment, "elevation_gain_m", 0.0) or 0.0), 1),
            "elevation_loss_m": round(float(getattr(segment, "elevation_loss_m", 0.0) or 0.0), 1),
            "traffic_ratio": round(float(traffic_ratio), 3) if traffic_ratio is not None else None,
        }
        if weather:
            item["weather"] = {
                "temp_c": round(float(getattr(weather, "temp_c", 0.0) or 0.0), 1),
                "wind_speed_mps": round(float(getattr(weather, "wind_speed_mps", 0.0) or 0.0), 1),
                "precipitation_prob": round(float(getattr(weather, "precipitation_prob", 0.0) or 0.0), 3),
                "condition": getattr(getattr(weather, "condition", None), "value", getattr(weather, "condition", None)),
            }
        items.append(item)

    total_count = len(segments_with_consumption or [])
    return {
        "items": items,
        "total_count": total_count,
        "logged_count": len(items),
        "max_logged": cap,
        "truncated": total_count > len(items),
    }


def _selected_station_summaries(station_results) -> List[Dict[str, Any]]:
    summaries = []
    for index, result in enumerate(station_results or []):
        station = getattr(result, "best_station", None)
        if not station:
            continue
        summaries.append({
            "stop_index": index,
            "station_id": str(getattr(station, "station_id", "") or ""),
            "source_provider": getattr(station, "source_provider", None),
            "source_id": getattr(station, "source_id", None),
            "power_kw": float(getattr(station, "power_kw", 0.0) or 0.0),
            "power_known": bool(getattr(station, "power_known", False)),
            "availability_status": getattr(station, "availability_status", None),
            "confidence": getattr(station, "confidence", None),
            "score": round(float(getattr(station, "score", 0.0) or 0.0), 4),
            "deviation_km": round(float(getattr(station, "deviation_km", 0.0) or 0.0), 2),
            "charge_time_min": float(getattr(station, "charge_time_min", 0.0) or 0.0),
            "estimated_cost": float(getattr(station, "estimated_cost", 0.0) or 0.0),
        })
    return summaries


def _station_candidate_summaries(station_results) -> List[Dict[str, Any]]:
    cap = _log_limit_from_env("LOG_DECISIONS_MAX_STATION_CANDIDATES", DEFAULT_MAX_LOGGED_STATION_CANDIDATES)
    candidates: List[Dict[str, Any]] = []
    for stop_index, result in enumerate(station_results or []):
        best = getattr(result, "best_station", None)
        best_id = str(getattr(best, "station_id", "") or "") if best else None
        for rank, station in enumerate(getattr(result, "stations", []) or []):
            if len(candidates) >= cap:
                return candidates
            station_id = str(getattr(station, "station_id", "") or "")
            candidates.append({
                "stop_index": stop_index,
                "rank": rank + 1,
                "station_id": station_id,
                "source_provider": getattr(station, "source_provider", None),
                "source_id": getattr(station, "source_id", None),
                "power_kw": float(getattr(station, "power_kw", 0.0) or 0.0),
                "power_known": bool(getattr(station, "power_known", False)),
                "availability_status": getattr(station, "availability_status", None),
                "score": round(float(getattr(station, "score", 0.0) or 0.0), 4),
                "deviation_km": round(float(getattr(station, "deviation_km", 0.0) or 0.0), 2),
                "selected": bool(best_id and station_id == best_id),
            })
    return candidates


def _station_candidate_metadata(station_results) -> Dict[str, Any]:
    cap = _log_limit_from_env("LOG_DECISIONS_MAX_STATION_CANDIDATES", DEFAULT_MAX_LOGGED_STATION_CANDIDATES)
    total = sum(len(getattr(result, "stations", []) or []) for result in (station_results or []))
    return {
        "total_count": total,
        "logged_count": min(total, cap),
        "max_logged": cap,
        "truncated": total > cap,
    }


def _station_reason_codes(station) -> List[str]:
    reason_codes: List[str] = []
    availability = getattr(station, "availability_status", None)
    if getattr(station, "is_compatible", True) is False:
        reason_codes.append("incompatible_connector")
    if availability == "unavailable":
        reason_codes.append("unavailable")
    if getattr(station, "power_known", True) is False:
        reason_codes.append("low_confidence_power")
    if availability == "unknown":
        reason_codes.append("low_confidence_availability")
    if not reason_codes:
        reason_codes.append("lower_score_than_selected")
    return reason_codes


def _station_reject_audit_summaries(station_results) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for stop_index, result in enumerate(station_results or []):
        audit = getattr(result, "reject_audit", None) or {}
        by_reason = audit.get("by_reason", {}) if isinstance(audit, dict) else {}
        for reason_code, payload in by_reason.items():
            items.append({
                "scope": "raw_prefilter",
                "stop_index": stop_index,
                "reason_code": reason_code,
                "count": int((payload or {}).get("count", 0) or 0),
                "samples": list((payload or {}).get("samples", []) or []),
            })
    return items


def _skipped_station_reasons(station_results) -> List[Dict[str, Any]]:
    skipped: List[Dict[str, Any]] = []
    for stop_index, result in enumerate(station_results or []):
        best = getattr(result, "best_station", None)
        best_id = str(getattr(best, "station_id", "") or "") if best else None
        for station in getattr(result, "stations", []) or []:
            station_id = str(getattr(station, "station_id", "") or "")
            if best_id and station_id == best_id:
                continue
            reason_codes = _station_reason_codes(station)
            skipped.append({
                "scope": "candidate",
                "stop_index": stop_index,
                "station_id": station_id,
                "source_provider": getattr(station, "source_provider", None),
                "reason_code": reason_codes[0],
                "reason_codes": reason_codes,
                "score": round(float(getattr(station, "score", 0.0) or 0.0), 4),
                "selected_station_id": best_id,
            })
    skipped.extend(_station_reject_audit_summaries(station_results))
    return skipped


def _objective_breakdown_payload(station_aware_solution) -> Dict[str, Any]:
    if not station_aware_solution:
        return {}
    breakdown = getattr(station_aware_solution, "breakdown", None)
    if not breakdown:
        return {}
    return asdict(breakdown)


def _provider_api_version(provider_name: Optional[str]) -> Optional[str]:
    if provider_name == "google_routes":
        return "routes_v2_computeRoutes"
    if provider_name == "google_directions":
        return "legacy_directions_v1"
    return None


def _increment_call_count(call_counts: Dict[str, int], key: Optional[str], amount: int = 1) -> None:
    if not key or amount <= 0:
        return
    call_counts[key] = int(call_counts.get(key, 0)) + int(amount)


def _record_station_provider_counts(call_counts: Dict[str, int], hotspots, station_results) -> None:
    _increment_call_count(call_counts, "google_places_new", len(hotspots or []))
    if any(
        getattr(station, "source_provider", None) == "ocm"
        for result in (station_results or [])
        for station in (getattr(result, "stations", []) or [])
    ):
        _increment_call_count(call_counts, "ocm_nearby")


def _soc_trajectory(hotspots, final_soc: float) -> List[Dict[str, Any]]:
    trajectory = []
    for index, hotspot in enumerate(hotspots or []):
        trajectory.append({
            "stop_index": index,
            "distance_km": round(float(getattr(hotspot, "distance_from_start_km", 0.0) or 0.0), 2),
            "arrival_soc": round(float(getattr(hotspot, "soc_at_point", 0.0) or 0.0), 1),
            "departure_soc": round(float(getattr(hotspot, "recommended_charge_to", 0.0) or 0.0), 1),
        })
    trajectory.append({"type": "destination", "arrival_soc": round(float(final_soc), 1)})
    return trajectory


def _plan_quality_dict(plan_quality: PlanQuality) -> Dict[str, Any]:
    if hasattr(plan_quality, "model_dump"):
        return plan_quality.model_dump(mode="json")
    return dict(plan_quality)


def _write_final_decision_log(
    *,
    trip_id: str,
    request: RouteRequest,
    vehicle,
    route_distance_km: float,
    route_duration_min: float,
    battery_kwh: float,
    arrival_soc: float,
    final_soc: float,
    station_results,
    hotspots,
    segments_with_consumption,
    plan_quality: PlanQuality,
    decision_reason: DecisionReason,
    started_at: float,
    station_aware_solution=None,
    avg_weather: Optional[WeatherInfo] = None,
    checkpoint_weather=None,
    elevation_gain_m: float = 0.0,
    elevation_loss_m: float = 0.0,
    traffic_ratio: Optional[float] = None,
) -> None:
    try:
        plan_quality_payload = _plan_quality_dict(plan_quality)
        record = build_route_decision_record(
            trip_id=trip_id,
            origin=(request.start_location.lat, request.start_location.lon),
            destination=(request.end_location.lat, request.end_location.lon),
            total_distance_km=route_distance_km,
            route_duration_min=route_duration_min,
            battery_kwh=battery_kwh,
            start_soc=request.current_soc_percent,
            arrival_soc_target=arrival_soc,
            final_soc=final_soc,
            vehicle_spec=_vehicle_decision_spec(vehicle),
            smart_plan_enabled=bool(getattr(request, "smart_plan_enabled", False)),
            optimization_mode=getattr(request, "optimization_mode", "balanced") or "balanced",
            decision_reason=decision_reason.value if hasattr(decision_reason, "value") else str(decision_reason),
            num_stops=sum(1 for r in station_results or [] if getattr(r, "best_station", None)),
            selected_stations=_selected_station_summaries(station_results),
            station_candidates=_station_candidate_summaries(station_results),
            station_candidate_metadata=_station_candidate_metadata(station_results),
            skipped_station_reasons=_skipped_station_reasons(station_results),
            soc_trajectory=_soc_trajectory(hotspots, final_soc),
            segment_feature_summary=_segment_summary(
                segments_with_consumption,
                avg_weather=avg_weather,
                checkpoint_weather=checkpoint_weather,
                elevation_gain_m=elevation_gain_m,
                elevation_loss_m=elevation_loss_m,
                traffic_ratio=traffic_ratio,
            ),
            plan_quality=plan_quality_payload,
            total_elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            objective_breakdown=_objective_breakdown_payload(station_aware_solution),
        )
        get_decision_logger().write(record)
    except Exception as exc:
        logger.warning(f"Final decision log write failed (non-fatal): {exc}")


async def plan_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    Route Planner - Clean Architecture.
    
    Akış:
    1-3: Rota seçimi, elevation, hava durumu
    4: Varsayılanları çöz (yolcu, yük)
    5: Segmentasyon
    6: Weather checkpoint'leri
    7: Tüketim hesabı (MainCalculator)
    8: Akıllı SOC parametreleri
    9-10: SOC simülasyonu + İstasyon bulma
    10.5: Pass 2 weather refinement
    11: Multi-leg oluşturma
    12-13: Response + CO2 + loglama
    """
    # Trip benzersiz tanımlayıcı (outcome backfill için)
    started_at = time.perf_counter()
    import uuid
    trip_id = str(uuid.uuid4())[:12]
    decision_reason = DecisionReason.ENERGY_REQUIRED
    station_aware_solution = None
    station_aware_inputs = None
    call_counts: Dict[str, int] = {}
    provider_versions: Dict[str, str] = {"station_catalog": "normalized_v1"}
    cache_trace_token = begin_cache_trace()

    logger.info("Route planning started",
                trip_id=trip_id,
                start=f"{request.start_location.lat},{request.start_location.lon}",
                end=f"{request.end_location.lat},{request.end_location.lon}")

    try:
        # STEP 1: Araç bilgilerini al
        try:
            if request.vehicle_spec:
                # .NET Gateway'den gelen MSSQL verisi
                vehicle = resolve_vehicle_spec(request.vehicle_spec)
                logger.info(
                    f"Vehicle resolved from MSSQL payload: {vehicle.display_name}",
                    vehicle_id=vehicle.id,
                    battery_kwh=vehicle.battery_capacity_kwh,
                )
            else:
                # Backward compat: JSON dosyadan oku (local dev / test)
                vehicle = get_vehicle_model(request.vehicle_model_id)
        except ValueError as e:
            return create_error_response("error_vehicle_not_found", str(e))
        
        battery_kwh = vehicle.battery_capacity_kwh
        
        # STEP 2: En iyi rotayı seç
        try:
            # Road avoidances: None geçir eğer hiçbir filtre seçilmemişse
            _ra = None
            if request.preferences and request.preferences.road_avoidances:
                ra = request.preferences.road_avoidances
                # En az bir filtre True ise gönder, aksi halde None bırak
                has_any = (
                    ra.avoid_tolls or ra.avoid_highways or ra.avoid_ferries
                    or ra.avoid_osmangazi_bridge or ra.avoid_canakkale_bridge
                    or ra.avoid_bridges or ra.avoid_private_highways
                )
                if has_any:
                    _ra = ra
                    
            logger.info(
                f"🛣️ road_avoidances: {_ra}",
                avoid_tolls=_ra.avoid_tolls if _ra else False,
                avoid_highways=_ra.avoid_highways if _ra else False,
                avoid_ferries=_ra.avoid_ferries if _ra else False,
                avoid_bridges=_ra.avoid_bridges if _ra else False,
                avoid_private_highways=_ra.avoid_private_highways if _ra else False,
                avoid_osmangazi=_ra.avoid_osmangazi_bridge if _ra else False,
                avoid_canakkale=_ra.avoid_canakkale_bridge if _ra else False,
            )
            route_result = await find_best_route(
                origin=request.start_location,
                destination=request.end_location,
                vehicle_model_id=request.vehicle_model_id,
                extra_load_kg=request.extra_load_kg or 0.0,
                strategy=request.route_strategy,
                departure_time_iso=request.departure_time_iso,
                road_avoidances=_ra,
                waypoints=request.waypoints,
                vehicle_spec=vehicle  # Zaten çözülmüş araç objesini ver
            )
            routing_provider = route_result.get("routing_provider")
            _increment_call_count(call_counts, routing_provider)
            api_version = _provider_api_version(routing_provider)
            if routing_provider and api_version:
                provider_versions[routing_provider] = api_version
        except Exception as e:
            if "NO_ROUTE_WITH_CONSTRAINTS" in str(e):
                return create_error_response(
                    "NO_ROUTE_WITH_CONSTRAINTS",
                    "Yol/köprü tercihlerine uyan rota bulunamadı"
                )
            return create_error_response("error_route_failed", str(e))
        
        selected_route = route_result["selected_route"]
        canonical_route = route_result.get("canonical_route")
        if not canonical_route:
            canonical_route = CanonicalRoute.from_google_directions_route(selected_route, index=0)
        polyline = canonical_route.polyline or route_result.get("polyline", "")
        google_legs = selected_route["legs"]
        first_leg = google_legs[0]
        last_leg = google_legs[-1]

        route_distance_km = canonical_route.distance_km
        route_duration_min = canonical_route.duration_in_traffic_min or canonical_route.duration_min
        traffic_ratio = canonical_route.traffic_ratio
        logger.info(
            f"Using canonical route metrics: {route_duration_min:.1f}min "
            f"(ratio: {traffic_ratio:.2f}) across {len(canonical_route.legs)} legs"
        )

        start_coords = first_leg["start_location"]
        end_coords = last_leg["end_location"]
        logger.info(f"Route selected: {round(route_distance_km, 1)}km, {round(route_duration_min, 1)}min")
        
        # STEP 3: Elevation verisi al
        elevation_gain_m = 0.0
        elevation_loss_m = 0.0
        raw_elevations = []
        if polyline:
            try:
                import math
                num_segments = math.ceil(route_distance_km / 10.0)
                num_samples = max(2, min(500, num_segments + 1))
                
                elevation_stats = await google_maps.get_elevation_stats(polyline, samples=num_samples)
                _increment_call_count(call_counts, "google_elevation")
                elevation_gain_m = elevation_stats.get("gain_m", 0.0)
                elevation_loss_m = elevation_stats.get("loss_m", 0.0)
                raw_elevations = elevation_stats.get("raw_elevations", [])
                logger.info(f"Elevation: +{round(elevation_gain_m)}m / -{round(elevation_loss_m)}m")
            except Exception as e:
                logger.warning(f"Elevation API failed: {e}")
        
        # STEP 4: Varsayılanları çöz
        passenger_count, child_count, extra_load_kg = resolve_defaults(request)
        logger.info(f"Resolved defaults: passengers={passenger_count}, children={child_count}, load={extra_load_kg}kg")
        
        # STEP 4.5: Başlangıç ve varış hava durumunu al
        start_weather, end_weather, avg_weather = await fetch_start_end_weather(
            start_coords=start_coords,
            end_coords=end_coords,
            route_duration_min=route_duration_min,
            weather_service=weather_service,
        )
        _increment_call_count(call_counts, "openweather_current")
        _increment_call_count(call_counts, "openweather_forecast")
        
        # STEP 4.7: Safe Harbor — Varış noktası şarj istasyonu kontrolü
        from app.route_planning.safe_harbor import calculate_safe_harbor_soc
        
        safe_harbor_result = None
        try:
            safe_harbor_result = await calculate_safe_harbor_soc(
                destination=request.end_location,
                vehicle=vehicle,
                battery_capacity_kwh=battery_kwh,
                passenger_count=passenger_count,
                extra_load_kg=extra_load_kg,
                child_count=child_count,
                temperature_celsius=avg_weather.temp_c if avg_weather else 20.0,
                selected_place_id=request.selected_rescue_place_id,
            )
            if not safe_harbor_result.is_destination_covered:
                logger.warning(
                    f"🏠 Safe Harbor ACTIVE: dynamic_arrival_soc="
                    f"{safe_harbor_result.dynamic_min_arrival_soc:.1f}%, "
                    f"rescue_stations={len(safe_harbor_result.rescue_stations)}"
                )
        except Exception as e:
            logger.warning(f"Safe Harbor check failed (continuing without): {e}")
        
        # STEP 5: SegmentFeatureBuilder - CanonicalRoute kaynaklı geometrik segmentasyon
        segment_features = SegmentFeatureBuilder(segment_length_km=10.0).build(
            canonical_route=canonical_route,
            total_elevation_gain_m=elevation_gain_m,
            total_elevation_loss_m=elevation_loss_m,
            raw_elevations=raw_elevations,
        )
        segmenter = segment_features.segmenter
        segments = segment_features.segments
        logger.info(f"Segments created: {len(segments)} segments")
        
        # STEP 6: Weather checkpoint'leri oluştur ve forecast çek
        checkpoint_weather = await fetch_weather_checkpoints(
            segmenter=segmenter,
            route_duration_min=route_duration_min,
            weather_service=weather_service,
        )
        _increment_call_count(call_counts, "openweather_forecast", len(checkpoint_weather))
        
        # STEP 7: EnergyEstimator - CanonicalRoute segmentleri için tüketim
        energy_estimator = ConsumptionEngineEnergyEstimator()
        energy_estimate = energy_estimator.estimate(EnergyEstimateRequest(
            segment_features=segment_features,
            vehicle=vehicle,
            weather_checkpoints=checkpoint_weather,
            extra_load_kg=extra_load_kg,
            passenger_count=passenger_count,
            child_count=child_count,
            driving_style_multiplier=request.driving_style.consumption_multiplier if request.driving_style else 1.0,
            hvac_on=request.hvac_on,
            max_speed_kmh=request.max_speed_kmh,
            consumption_override_wh_km=request.consumption_override_wh_km,
        ))
        segments_with_consumption = energy_estimate.segments
        
        total_consumption = energy_estimate.total_consumption_kwh
        logger.info(f"Total consumption calculated: {round(total_consumption, 2)}kWh")
        
        # STEP 8: Temel SOC Parametreleri (+ Safe Harbor enjeksiyonu)
        charge_min_soc, user_target_soc_override, arrival_soc = calculate_base_soc_params(
            battery_kwh=battery_kwh,
            start_soc=request.current_soc_percent,
            total_consumption_kwh=total_consumption,
            route_distance_km=route_distance_km,
            request=request,
            safe_harbor_result=safe_harbor_result,
        )
        if user_target_soc_override is not None:
            decision_reason = DecisionReason.USER_OVERRIDE
        elif safe_harbor_result and not safe_harbor_result.is_destination_covered:
            decision_reason = DecisionReason.SAFE_HARBOR
        
        # STEP 9: SOC Simülasyonu + Optimizasyon (Pass 1)
        avg_speed_kmh = (route_distance_km / route_duration_min) * 60 if route_duration_min > 0 else 80.0
        
        charge_target_soc, sim_result = _run_soc_simulation(
            segments_with_consumption=segments_with_consumption,
            route_distance_km=route_distance_km,
            battery_kwh=battery_kwh,
            start_soc=request.current_soc_percent,
            arrival_soc=arrival_soc,
            charge_min_soc=charge_min_soc,
            charge_target_soc=user_target_soc_override or DEFAULT_CHARGE_TARGET_SOC,
            user_target_soc_override=user_target_soc_override,
            avg_speed_kmh=avg_speed_kmh,
            request=request,  # Pareto branch için
            trip_id=trip_id,
        )

        # Helper — İlk hotspot origin'e çok yakınsa
        # (low-SOC start senaryosu), istasyon araması origin civarına yönlendirilsin.
        # Aksi halde hotspot segment 0'ın end_point'ine (~10km) düşer ve oradan corridor
        # search yapılır — şehir merkezi istasyonlarını kaçırabilir.
        # Ek: route_polyline_coords boşaltılıyor — perp-distance filtresi bu hotspot'ta
        # gevşesin (şehir içi istasyonlar highway corridor'dan 2-5km uzakta olabilir).
        def _relocate_low_soc_hotspot_to_origin(hs_list):
            if hs_list and hs_list[0].distance_from_start_km < 15.0:
                original_loc = hs_list[0].location
                hs_list[0].location = request.start_location
                hs_list[0].distance_from_start_km = 0.0
                hs_list[0].bypass_perp_filter = True  # Şehir içi istasyon yakalaması için
                logger.info(
                    f"Low-SOC origin hotspot: ilk hotspot lokasyonu origin'e taşındı "
                    f"({original_loc.lat:.4f},{original_loc.lon:.4f} → "
                    f"{request.start_location.lat:.4f},{request.start_location.lon:.4f}) — "
                    f"şehir merkezi istasyonlarını yakala (perp filter bypassed)"
                )

        # STEP 10: Hotspotlar için istasyon bulma
        hotspots = sim_result.hotspots
        _relocate_low_soc_hotspot_to_origin(hotspots)

        station_results = []

        if hotspots:
            from app.station_finder import find_stations_for_hotspots
            prefs_dict = None
            if request.preferences:
                prefs_dict = {
                    "max_detour_km": request.preferences.max_detour_km,
                    "preferred_operators": request.preferences.preferred_operators,
                    "preferred_plug_types": [p.value for p in request.preferences.preferred_plug_types] if request.preferences.preferred_plug_types else [],
                    "amenities_required": [a.value for a in request.preferences.amenities_required] if request.preferences.amenities_required else []
                }
            station_results = await find_stations_for_hotspots(
                hotspots,
                request.vehicle_model_id,
                preferences=prefs_dict,
                vehicle_spec=vehicle,
                route_polyline=polyline,
            )
            _record_station_provider_counts(call_counts, hotspots, station_results)
        
        charge_stops = sum(1 for r in station_results if r.best_station) if station_results else 0

        station_aware = _run_station_aware_pareto(
            hotspots=hotspots,
            station_results=station_results,
            segments_with_consumption=segments_with_consumption,
            route_distance_km=route_distance_km,
            battery_kwh=battery_kwh,
            start_soc=request.current_soc_percent,
            arrival_soc=arrival_soc,
            charge_min_soc=charge_min_soc,
            charge_target_soc=charge_target_soc,
            avg_speed_kmh=avg_speed_kmh,
            request=request,
            vehicle=vehicle,
            temperature_c=avg_weather.temp_c if avg_weather else None,
        )
        if station_aware:
            station_aware_solution, sim_result, station_aware_inputs = station_aware
            charge_target_soc = (
                max(station_aware_solution.per_stop_target_soc)
                if station_aware_solution.per_stop_target_soc
                else charge_target_soc
            )
            previous_hotspot_count = len(hotspots)
            hotspots = sim_result.hotspots
            _relocate_low_soc_hotspot_to_origin(hotspots)
            if len(hotspots) != previous_hotspot_count:
                station_results = _align_station_results_to_hotspots(hotspots, station_results)
            charge_stops = sum(1 for r in station_results if r.best_station) if station_results else 0
            decision_reason = (
                DecisionReason.FALLBACK_GRID
                if station_aware_solution.fallback_used
                else (
                    DecisionReason.DATA_CONFIDENCE_PENALTY
                    if station_aware_solution.low_confidence_station_ratio > 0
                    else DecisionReason.PARETO_OPTIMAL
                )
            )
            logger.info(
                "Station-aware Pareto pass complete",
                targets=station_aware_solution.per_stop_target_soc,
                low_confidence_ratio=round(station_aware_solution.low_confidence_station_ratio, 3),
                fallback_used=station_aware_solution.fallback_used,
            )
        
        logger.info(
            f"Pass 1 complete: {len(hotspots)} hotspots, "
            f"{charge_stops} stations, final_soc={sim_result.final_soc}%"
        )
        
        # STEP 10.5: Pass 2 Weather Refinement
        if hotspots and station_results:
            try:
                refined_weather = await refine_weather_pass2(
                    hotspots=hotspots,
                    station_results=station_results,
                    start_weather=start_weather,
                    end_weather=end_weather,
                    avg_weather=avg_weather,
                    route_distance_km=route_distance_km,
                    weather_service=weather_service,
                )
                
                if refined_weather:
                    _increment_call_count(
                        call_counts,
                        "openweather_current",
                        sum(1 for result in station_results if getattr(result, "best_station", None)),
                    )
                    logger.info("Pass 2: Re-calculating consumption with refined weather...")
                    
                    # Tüketimi yeniden hesapla (aynı canonical segment seti üstünden)
                    energy_estimate = energy_estimator.estimate(EnergyEstimateRequest(
                        segment_features=segment_features,
                        vehicle=vehicle,
                        temperature_celsius=refined_weather.temp_c,
                        wind_speed_mps=refined_weather.wind_speed_mps,
                        weather_condition=refined_weather.condition.value,
                        extra_load_kg=extra_load_kg,
                        passenger_count=passenger_count,
                        child_count=child_count,
                        driving_style_multiplier=request.driving_style.consumption_multiplier if request.driving_style else 1.0,
                        hvac_on=request.hvac_on,
                        max_speed_kmh=request.max_speed_kmh,
                        consumption_override_wh_km=request.consumption_override_wh_km,
                    ))
                    segments_with_consumption = energy_estimate.segments
                    
                    new_total = energy_estimate.total_consumption_kwh
                    logger.info(f"Pass 2 consumption: {total_consumption:.2f} → {new_total:.2f} kWh (diff={new_total-total_consumption:.2f})")
                    total_consumption = new_total

                    # Pass 2 Pareto'yu YENİDEN çalıştırmıyor — sadece tüketim güncellemesi.
                    # Pass 1'in ürettiği per-stop target'ları (hotspot.recommended_charge_to,
                    # Pareto target'larıyla senkronize) doğrudan tekrar simüle edilir. Hava ufak
                    # bir tüketim değişimi yaratır; mevcut plan yakın-optimal kalır.
                    # Hotspot sayısı değişirse loglanır ve gerekirse istasyonlar yeniden aranır.
                    existing_targets = [float(h.recommended_charge_to) for h in sim_result.hotspots]
                    pass2_sim = SOCSimulator(
                        battery_capacity_kwh=battery_kwh,
                        start_soc=request.current_soc_percent,
                        target_arrival_soc=arrival_soc,
                        charge_min_soc=charge_min_soc,
                        charge_target_soc=charge_target_soc,
                    )
                    sim_result = pass2_sim.simulate(
                        segments_with_consumption,
                        route_distance_km,
                        per_stop_targets=existing_targets if existing_targets else None,
                    )
                    logger.info(
                        f"Pass 2 re-sim: existing_targets={existing_targets}, "
                        f"new_hotspots={len(sim_result.hotspots)}, final_soc={sim_result.final_soc}%"
                    )
                    
                    # Hotspot nesnelerini HER ZAMAN Pass 2 sonuçlarıyla güncelle.
                    # Sayı aynı kalsa bile Pass 2'de tüketim yeniden hesaplandığı için
                    # hotspot.soc_at_point ve recommended_charge_to güncellenmiş olabilir.
                    # Leg_builder eski nesneleri kullanırsa yanlış SOC/şarj süresi gösterir.
                    prev_hotspot_count = len(hotspots)
                    hotspots = sim_result.hotspots
                    # Origin override Pass 2 hotspot'larına da uygula
                    _relocate_low_soc_hotspot_to_origin(hotspots)

                    if len(hotspots) != prev_hotspot_count:
                        # Sayı değişti → istasyonları yeniden bul
                        logger.info(f"Pass 2: Hotspot count changed {prev_hotspot_count} → {len(hotspots)}, re-finding stations...")
                        if hotspots:
                            from app.station_finder import find_stations_for_hotspots
                            station_results = await find_stations_for_hotspots(hotspots, request.vehicle_model_id, vehicle_spec=vehicle, route_polyline=polyline)
                            _record_station_provider_counts(call_counts, hotspots, station_results)
                        else:
                            station_results = []
                        charge_stops = sum(1 for r in station_results if r.best_station) if station_results else 0
                    else:
                        # Sayı aynı kaldı — mevcut station_results korunuyor, Pass 2 hotspot verileri güncellendi
                        logger.info(f"Pass 2: Hotspot count unchanged ({len(hotspots)}), keeping station_results, refreshed hotspot SOC/targets.")
                    
                    avg_weather = refined_weather
                    logger.info(f"Pass 2 complete: {len(hotspots)} hotspots, final_soc={sim_result.final_soc}%")
                    
            except Exception as e:
                logger.warning(f"Pass 2 weather refinement failed: {e}")
        

        # STEP 10.5: İstasyonlar bulundu → final canonical route'u istasyonlardan
        # geçecek şekilde yeniden üret ve enerji/SOC hesabını bu son rota üstünden
        # tekrar doğrula. Display polyline daha sonra Snap to Roads ile ayrı
        # iyileştirilebilir; enerji kaynağı canonical_route.polyline olarak kalır.
        selected_station_points: List[GeoPoint] = []
        if station_results:
            for sr in station_results:
                if sr.best_station and sr.best_station.location:
                    selected_station_points.append(sr.best_station.location)

        if selected_station_points:
            try:
                user_waypoints = list(request.waypoints or [])
                combined_waypoints = _combine_final_route_waypoints(user_waypoints, selected_station_points)
                logger.info(
                    f"Re-routing through {len(selected_station_points)} charging stations "
                    f"(+ {len(user_waypoints)} user waypoints) to refresh polyline"
                )
                rerouted = await find_best_route(
                    origin=request.start_location,
                    destination=request.end_location,
                    vehicle_model_id=request.vehicle_model_id,
                    extra_load_kg=request.extra_load_kg or 0.0,
                    strategy=request.route_strategy,
                    departure_time_iso=request.departure_time_iso,
                    road_avoidances=_ra,
                    waypoints=combined_waypoints,
                    vehicle_spec=vehicle,
                )
                reroute_provider = rerouted.get("routing_provider")
                _increment_call_count(call_counts, reroute_provider)
                api_version = _provider_api_version(reroute_provider)
                if reroute_provider and api_version:
                    provider_versions[reroute_provider] = api_version
                new_polyline = rerouted.get("polyline", "")
                if new_polyline:
                    canonical_route = rerouted.get("canonical_route") or CanonicalRoute.from_google_directions_route(
                        rerouted["selected_route"],
                        index=0,
                    )
                    selected_route = rerouted["selected_route"]
                    google_legs = selected_route["legs"]
                    first_leg = google_legs[0]
                    last_leg = google_legs[-1]
                    start_coords = first_leg["start_location"]
                    end_coords = last_leg["end_location"]
                    polyline = canonical_route.polyline
                    route_distance_km = canonical_route.distance_km
                    route_duration_min = canonical_route.duration_in_traffic_min or canonical_route.duration_min
                    traffic_ratio = canonical_route.traffic_ratio
                    route_result = rerouted

                    # Final route geometry changed; recalculate elevation, segments,
                    # energy, and SOC so the map route and energy route match.
                    elevation_gain_m = 0.0
                    elevation_loss_m = 0.0
                    raw_elevations = []
                    try:
                        import math
                        num_segments = math.ceil(route_distance_km / 10.0)
                        num_samples = max(2, min(500, num_segments + 1))
                        elevation_stats = await google_maps.get_elevation_stats(polyline, samples=num_samples)
                        _increment_call_count(call_counts, "google_elevation")
                        elevation_gain_m = elevation_stats.get("gain_m", 0.0)
                        elevation_loss_m = elevation_stats.get("loss_m", 0.0)
                        raw_elevations = elevation_stats.get("raw_elevations", [])
                    except Exception as elevation_error:
                        logger.warning(f"Final route elevation refresh failed: {elevation_error}")

                    segment_features = SegmentFeatureBuilder(segment_length_km=10.0).build(
                        canonical_route=canonical_route,
                        total_elevation_gain_m=elevation_gain_m,
                        total_elevation_loss_m=elevation_loss_m,
                        raw_elevations=raw_elevations,
                    )
                    segmenter = segment_features.segmenter
                    segments = segment_features.segments
                    checkpoint_weather = await fetch_weather_checkpoints(
                        segmenter=segmenter,
                        route_duration_min=route_duration_min,
                        weather_service=weather_service,
                    )
                    _increment_call_count(call_counts, "openweather_forecast", len(checkpoint_weather))
                    energy_estimate = energy_estimator.estimate(EnergyEstimateRequest(
                        segment_features=segment_features,
                        vehicle=vehicle,
                        weather_checkpoints=checkpoint_weather,
                        extra_load_kg=extra_load_kg,
                        passenger_count=passenger_count,
                        child_count=child_count,
                        driving_style_multiplier=request.driving_style.consumption_multiplier if request.driving_style else 1.0,
                        hvac_on=request.hvac_on,
                        max_speed_kmh=request.max_speed_kmh,
                        consumption_override_wh_km=request.consumption_override_wh_km,
                    ))
                    segments_with_consumption = energy_estimate.segments
                    total_consumption = energy_estimate.total_consumption_kwh

                    existing_targets = [float(h.recommended_charge_to) for h in hotspots]
                    final_sim = SOCSimulator(
                        battery_capacity_kwh=battery_kwh,
                        start_soc=request.current_soc_percent,
                        target_arrival_soc=arrival_soc,
                        charge_min_soc=charge_min_soc,
                        charge_target_soc=charge_target_soc,
                    )
                    sim_result = final_sim.simulate(
                        segments_with_consumption,
                        route_distance_km,
                        per_stop_targets=existing_targets if existing_targets else None,
                    )
                    prev_hotspot_count = len(hotspots)
                    hotspots = sim_result.hotspots
                    _relocate_low_soc_hotspot_to_origin(hotspots)
                    if len(hotspots) != prev_hotspot_count:
                        logger.warning(
                            "Final canonical route revalidation changed hotspot count; keeping selected station order",
                            previous_hotspots=prev_hotspot_count,
                            final_hotspots=len(hotspots),
                        )
                        station_results = _align_station_results_to_hotspots(hotspots, station_results)
                    charge_stops = sum(1 for r in station_results if r.best_station) if station_results else 0

                    avg_speed_kmh = (route_distance_km / route_duration_min) * 60 if route_duration_min > 0 else 80.0
                    logger.info(
                        "Final canonical route refreshed with charging-station waypoints",
                        distance_km=round(route_distance_km, 1),
                        duration_min=round(route_duration_min, 1),
                        consumption_kwh=round(total_consumption, 2),
                        final_soc=round(sim_result.final_soc, 1),
                        avg_speed_kmh=round(avg_speed_kmh, 1),
                    )
            except Exception as e:
                logger.warning(f"Re-routing through stations failed, keeping original polyline: {e}")

        # STEP 10.7: Roads API Snap-to-Roads — polyline'ı yol geometrisine oturt.
        # Ham Directions polyline'ı bazen yol çizgisinden saparak görünür; snapToRoads
        # noktaları en yakın yol segmentine oturtur. Fail-soft: API başarısız olursa
        # canonical energy polyline korunur, sadece display_polyline güncellenir.
        display_polyline, snap_called = await _snap_display_polyline_for_roads(polyline, google_maps.snap_to_roads)
        if snap_called:
            _increment_call_count(call_counts, "google_roads_snap_to_roads")

        # STEP 11: Multi-Leg Builder
        legs, missing_station_warnings = build_multi_legs(
            start_point=GeoPoint(lat=start_coords["lat"], lon=start_coords["lng"]),
            end_point=GeoPoint(lat=end_coords["lat"], lon=end_coords["lng"]),
            total_distance_km=route_distance_km,
            total_duration_min=route_duration_min,
            segments_with_consumption=segments_with_consumption,
            start_soc=request.current_soc_percent,
            final_soc=sim_result.final_soc,
            hotspots=hotspots,
            station_results=station_results,
            polyline=display_polyline,
            battery_capacity_kwh=battery_kwh,
            temperature_c=avg_weather.temp_c if avg_weather else None,
            weather_info=avg_weather,
            vehicle_spec=vehicle
        )

        # Plan fiziksel olarak yapılabilir mi? Hard validation.
        # Tüm hotspot'lar istasyonsuz veya leg fiziksel imkansız ise erken hata dön.
        avg_cons_wh_km = (total_consumption / route_distance_km) * 1000 if route_distance_km > 0 else 250.0
        feasibility_error = validate_plan_feasibility(
            legs,
            missing_station_warnings,
            battery_capacity_kwh=battery_kwh,
            avg_consumption_wh_km=avg_cons_wh_km,
        )
        if feasibility_error:
            logger.error(f"[FEASIBILITY] Plan reddedildi: {feasibility_error}")
            return create_error_response("error_no_stations_in_corridor", feasibility_error)

        end_soc = sim_result.final_soc
        
        # STEP 12: CO2 tasarrufu
        co2_savings = calculate_co2(route_distance_km, sim_result)
        
        # Mesaj oluştur
        message = build_message(
            charge_stops=charge_stops,
            charge_target_soc=charge_target_soc,
            end_soc=end_soc,
            can_complete_without_charging=sim_result.can_complete_without_charging,
        )
        logger.info(f"Route planning completed: {message}")
        
        # STEP 13: Training data logging
        log_training_data(
            request=request,
            battery_kwh=battery_kwh,
            route_distance_km=route_distance_km,
            route_duration_min=route_duration_min,
            elevation_gain_m=elevation_gain_m,
            elevation_loss_m=elevation_loss_m,
            total_consumption=total_consumption,
            end_soc=end_soc,
            charge_stops=charge_stops,
            co2_savings=co2_savings,
            route_result=route_result,
            sim_result=sim_result,
            segments_with_consumption=segments_with_consumption,
            charge_min_soc=charge_min_soc,
            charge_target_soc=charge_target_soc,
            arrival_soc=arrival_soc,
            passenger_count=passenger_count,
            child_count=child_count,
            extra_load_kg=extra_load_kg,
            avg_weather=avg_weather,
        )
        
        # Uyarı mesajları
        warning_messages = build_warnings(
            end_soc=end_soc,
            charge_stops=charge_stops,
            traffic_ratio=traffic_ratio,
            station_results=station_results,
            missing_station_warnings=missing_station_warnings,
        )

        # Post-route plan sanity check — saçma plan üretildiyse uyar
        sanity_warnings = validate_plan_sanity(
            legs=legs,
            charge_stops=charge_stops,
            total_distance_km=route_distance_km,
            total_consumption_kwh=total_consumption,
            battery_kwh=battery_kwh,
            end_soc=end_soc,
        )
        if sanity_warnings:
            for w in sanity_warnings:
                logger.warning(f"[PLAN_SANITY] {w}")
            warning_messages.extend(sanity_warnings)

        # 🏠 Safe Harbor uyarılarını ekle
        if safe_harbor_result and safe_harbor_result.warning_message:
            warning_messages.insert(0, safe_harbor_result.warning_message)
        
        # 🧠 STEP 12.5: Akıllı Seyahat Asistanı — Insight Engine
        insights = insight_engine.analyze(
            route_distance_km=route_distance_km,
            route_duration_min=route_duration_min,
            elevation_gain_m=elevation_gain_m,
            elevation_loss_m=elevation_loss_m,
            total_consumption_kwh=total_consumption,
            start_soc=request.current_soc_percent,
            end_soc=end_soc,
            charge_stops=charge_stops,
            battery_kwh=battery_kwh,
            start_weather=start_weather,
            end_weather=end_weather,
            avg_weather=avg_weather,
            checkpoint_weather=checkpoint_weather,
            legs=legs,
            co2_savings_kg=co2_savings,
            total_charging_cost=sum(
                getattr(l, 'estimated_cost', 0) or 0 for l in legs
                if hasattr(l, 'estimated_cost')
            ),
        )
        
        # 🏠 Safe Harbor bilgisini response'a ekle (V2 — per-station SOC)
        safe_harbor_info = None
        if safe_harbor_result and not safe_harbor_result.is_destination_covered:
            safe_harbor_info = {
                "active": True,
                "selected_station_index": safe_harbor_result.selected_station_index,
                "dynamic_min_arrival_soc_percent": safe_harbor_result.dynamic_min_arrival_soc,
                "search_radius_used_km": safe_harbor_result.search_radius_used_km,
                "rescue_stations": [
                    {
                        "name": rs.name,
                        "place_id": rs.place_id,
                        "location": {"lat": rs.location.lat, "lon": rs.location.lon},
                        "distance_km": rs.distance_km,
                        "route_distance_km": rs.route_distance_km,
                        "max_power_kw": rs.max_power_kw,
                        "rating": rs.rating,
                        "return_consumption_kwh": rs.return_consumption_kwh,
                        "return_soc_needed_percent": rs.return_soc_needed,
                        "required_arrival_soc_percent": rs.required_arrival_soc,
                        "is_selected": rs.is_selected,
                        "elevation": {
                            "gain_m": rs.elevation_gain_m,
                            "loss_m": rs.elevation_loss_m,
                        },
                    }
                    for rs in safe_harbor_result.rescue_stations
                ],
            }

        plan_quality = _build_plan_quality(
            station_aware_solution=station_aware_solution,
            station_inputs=station_aware_inputs,
            warning_messages=warning_messages,
            missing_station_warnings=missing_station_warnings,
            call_counts=call_counts,
            provider_versions=provider_versions,
            cache_metrics=get_cache_metrics_snapshot(),
        )
        
        # Final response
        response = build_route_response(
            route_distance_km=route_distance_km,
            route_duration_min=route_duration_min,
            co2_savings=co2_savings,
            total_consumption=total_consumption,
            legs=legs,
            charge_stops=charge_stops,
            message=message,
            request=request,
            traffic_ratio=traffic_ratio,
            route_leg=first_leg,
            start_weather=start_weather,
            end_weather=end_weather,
            missing_station_warnings=missing_station_warnings,
            warning_messages=warning_messages,
            insights=insights,
            overview_polyline=display_polyline,
        )
        
        # Safe Harbor bilgisini enjekte et
        if safe_harbor_info:
            response.safe_harbor_info = safe_harbor_info

        # Outcome backfill için trip_id'yi response'a yaz
        response.trip_id = trip_id
        response.decision_reason = decision_reason
        response.plan_quality = plan_quality

        _write_final_decision_log(
            trip_id=trip_id,
            request=request,
            vehicle=vehicle,
            route_distance_km=route_distance_km,
            route_duration_min=route_duration_min,
            battery_kwh=battery_kwh,
            arrival_soc=arrival_soc,
            final_soc=end_soc,
            station_results=station_results,
            hotspots=hotspots,
            segments_with_consumption=segments_with_consumption,
            plan_quality=plan_quality,
            decision_reason=decision_reason,
            started_at=started_at,
            station_aware_solution=station_aware_solution,
            avg_weather=avg_weather,
            checkpoint_weather=checkpoint_weather,
            elevation_gain_m=elevation_gain_m,
            elevation_loss_m=elevation_loss_m,
            traffic_ratio=traffic_ratio,
        )

        return response
        
    except Exception as e:
        logger.exception(f"Route planning failed: {e}")
        return create_error_response("error_unknown", str(e))
    finally:
        end_cache_trace(cache_trace_token)
