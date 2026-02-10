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
from typing import List, Optional, Dict, Any

from app.models import (
    RouteRequest, 
    MultiStopRouteResponse, 
    GeoPoint,
    WeatherInfo,
    WeatherCondition,
)

from app.route_segmenter import RouteSegmenter
from app.soc_simulator import (
    SOCSimulator, 
    ChargePlanOptimizer,
)
from app.consumption_engine.main_calculator import calculate_route_consumption
from app.infrastructure.vehicle_catalog import get_vehicle_model
from app.route_selector import find_best_route
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
)

logger = get_logger("route_planner")
weather_service = WeatherService()


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
# SOC Simülasyonu — Pass 1 ve Pass 2'de ortak kullanılır
# =============================================================================
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
):
    """
    SOC simülasyonu çalıştır (Pass 1 ve Pass 2 için ortak).
    
    Returns:
        (charge_target_soc, sim_result)
    """
    if user_target_soc_override is not None:
        simulator = SOCSimulator(
            battery_capacity_kwh=battery_kwh,
            start_soc=start_soc,
            target_arrival_soc=arrival_soc,
            charge_min_soc=charge_min_soc,
            charge_target_soc=charge_target_soc,
            user_override_target=True
        )
        sim_result = simulator.simulate(segments_with_consumption, route_distance_km)
        logger.info(f"User override target_soc={charge_target_soc}%, stops={len(sim_result.hotspots)}")
        return charge_target_soc, sim_result
    else:
        optimizer = ChargePlanOptimizer(battery_capacity_kwh=battery_kwh)
        new_target_soc, sim_result = optimizer.find_optimal_plan(
            segments_with_consumption=segments_with_consumption,
            total_distance_km=route_distance_km,
            battery_capacity_kwh=battery_kwh,
            start_soc=start_soc,
            target_arrival_soc=arrival_soc,
            charge_min_soc=charge_min_soc,
            avg_speed_kmh=avg_speed_kmh
        )
        return new_target_soc, sim_result


# =============================================================================
# ANA ORKESTRASYON
# =============================================================================
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
    logger.info("Route planning started", 
                start=f"{request.start_location.lat},{request.start_location.lon}",
                end=f"{request.end_location.lat},{request.end_location.lon}")
    
    try:
        # STEP 1: Araç bilgilerini al
        try:
            vehicle = get_vehicle_model(request.vehicle_model_id)
        except ValueError as e:
            return create_error_response("error_vehicle_not_found", str(e))
        
        battery_kwh = vehicle.battery_capacity_kwh
        
        # STEP 2: En iyi rotayı seç
        try:
            route_result = await find_best_route(
                origin=request.start_location,
                destination=request.end_location,
                vehicle_model_id=request.vehicle_model_id,
                extra_load_kg=request.extra_load_kg,
                strategy=request.route_strategy,
                departure_time_iso=request.departure_time_iso
            )
        except Exception as e:
            return create_error_response("error_route_failed", str(e))
        
        selected_route = route_result["selected_route"]
        polyline = route_result.get("polyline", "")
        route_leg = selected_route["legs"][0]
        
        route_distance_km = route_leg["distance"]["value"] / 1000
        duration_in_traffic = route_leg.get("duration_in_traffic", {}).get("value")
        if duration_in_traffic:
            route_duration_min = duration_in_traffic / 60
            traffic_ratio = route_result.get("traffic_ratio", 1.0)
            logger.info(f"Using traffic duration: {route_duration_min:.1f}min (ratio: {traffic_ratio:.2f})")
        else:
            route_duration_min = route_leg["duration"]["value"] / 60
            traffic_ratio = 1.0
        
        start_coords = route_leg["start_location"]
        end_coords = route_leg["end_location"]
        logger.info(f"Route selected: {round(route_distance_km, 1)}km, {round(route_duration_min, 1)}min")
        
        # STEP 3: Elevation verisi al
        elevation_gain_m = 0.0
        elevation_loss_m = 0.0
        if polyline:
            try:
                elevation_stats = await google_maps.get_elevation_stats(polyline)
                elevation_gain_m = elevation_stats.get("gain_m", 0.0)
                elevation_loss_m = elevation_stats.get("loss_m", 0.0)
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
        
        # STEP 5: Route Segmenter - Geometrik segmentasyon
        segmenter = RouteSegmenter(segment_length_km=10.0)
        segments = segmenter.create_segments(
            polyline=polyline,
            total_elevation_gain_m=elevation_gain_m,
            total_elevation_loss_m=elevation_loss_m
        )
        logger.info(f"Segments created: {len(segments)} segments")
        
        # STEP 6: Weather checkpoint'leri oluştur ve forecast çek
        checkpoint_weather = await fetch_weather_checkpoints(
            segmenter=segmenter,
            route_duration_min=route_duration_min,
            weather_service=weather_service,
        )
        
        # STEP 7: Main Calculator - Her segment için tüketim
        segments_with_consumption = calculate_route_consumption(
            vehicle=vehicle,
            segments=segments,
            weather_checkpoints=checkpoint_weather,
            extra_load_kg=extra_load_kg,
            passenger_count=passenger_count,
            child_count=child_count
        )
        
        total_consumption = sum(s.consumption_kwh for s in segments_with_consumption)
        logger.info(f"Total consumption calculated: {round(total_consumption, 2)}kWh")
        
        # STEP 8: Temel SOC Parametreleri
        charge_min_soc, user_target_soc_override, arrival_soc = calculate_base_soc_params(
            battery_kwh=battery_kwh,
            start_soc=request.current_soc_percent,
            total_consumption_kwh=total_consumption,
            route_distance_km=route_distance_km,
            request=request
        )
        
        # STEP 9: SOC Simülasyonu + Optimizasyon (Pass 1)
        avg_speed_kmh = (route_distance_km / route_duration_min) * 60 if route_duration_min > 0 else 80.0
        
        charge_target_soc, sim_result = _run_soc_simulation(
            segments_with_consumption=segments_with_consumption,
            route_distance_km=route_distance_km,
            battery_kwh=battery_kwh,
            start_soc=request.current_soc_percent,
            arrival_soc=arrival_soc,
            charge_min_soc=charge_min_soc,
            charge_target_soc=user_target_soc_override or 80,
            user_target_soc_override=user_target_soc_override,
            avg_speed_kmh=avg_speed_kmh,
        )
        
        # STEP 10: Hotspotlar için istasyon bulma
        hotspots = sim_result.hotspots
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
                preferences=prefs_dict
            )
        
        charge_stops = sum(1 for r in station_results if r.best_station) if station_results else 0
        
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
                    logger.info("Pass 2: Re-calculating consumption with refined weather...")
                    
                    # Tüketimi yeniden hesapla
                    segments_with_consumption = calculate_route_consumption(
                        vehicle=vehicle,
                        segments=segments,
                        temperature_celsius=refined_weather.temp_c,
                        wind_speed_mps=refined_weather.wind_speed_mps,
                        weather_condition=refined_weather.condition.value,
                        extra_load_kg=extra_load_kg,
                        passenger_count=passenger_count,
                        child_count=child_count
                    )
                    
                    new_total = sum(s.consumption_kwh for s in segments_with_consumption)
                    logger.info(f"Pass 2 consumption: {total_consumption:.2f} → {new_total:.2f} kWh (diff={new_total-total_consumption:.2f})")
                    total_consumption = new_total
                    
                    # SOC simülasyonunu yeniden çalıştır
                    charge_target_soc, sim_result = _run_soc_simulation(
                        segments_with_consumption=segments_with_consumption,
                        route_distance_km=route_distance_km,
                        battery_kwh=battery_kwh,
                        start_soc=request.current_soc_percent,
                        arrival_soc=arrival_soc,
                        charge_min_soc=charge_min_soc,
                        charge_target_soc=charge_target_soc,
                        user_target_soc_override=user_target_soc_override,
                        avg_speed_kmh=avg_speed_kmh,
                    )
                    
                    # Hotspot sayısı değiştiyse istasyonları yeniden bul
                    if len(sim_result.hotspots) != len(hotspots):
                        logger.info(f"Pass 2: Hotspot count changed {len(hotspots)} → {len(sim_result.hotspots)}, re-finding stations...")
                        hotspots = sim_result.hotspots
                        if hotspots:
                            from app.station_finder import find_stations_for_hotspots
                            station_results = await find_stations_for_hotspots(hotspots, request.vehicle_model_id)
                        else:
                            station_results = []
                        charge_stops = sum(1 for r in station_results if r.best_station) if station_results else 0
                    
                    avg_weather = refined_weather
                    logger.info(f"Pass 2 complete: {len(hotspots)} hotspots, final_soc={sim_result.final_soc}%")
                    
            except Exception as e:
                logger.warning(f"Pass 2 weather refinement failed: {e}")
        
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
            polyline=polyline,
            battery_capacity_kwh=battery_kwh,
            temperature_c=avg_weather.temp_c if avg_weather else None,
            weather_info=avg_weather,
            vehicle_model_id=request.vehicle_model_id
        )
        
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
        
        # Final response
        return build_route_response(
            route_distance_km=route_distance_km,
            route_duration_min=route_duration_min,
            co2_savings=co2_savings,
            total_consumption=total_consumption,
            legs=legs,
            charge_stops=charge_stops,
            message=message,
            request=request,
            traffic_ratio=traffic_ratio,
            route_leg=route_leg,
            start_weather=start_weather,
            end_weather=end_weather,
            missing_station_warnings=missing_station_warnings,
            warning_messages=warning_messages,
        )
        
    except Exception as e:
        logger.exception(f"Route planning failed: {e}")
        return create_error_response("error_unknown", str(e))
