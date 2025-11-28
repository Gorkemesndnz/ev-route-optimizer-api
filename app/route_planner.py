"""
Route Planner v2.0
===================

V2.0: Clean Architecture - Tek sorumluluk prensibi.

Akis:
1. Route Selector - En iyi rota
2. Google Elevation - Rakim verisi
3. Route Segmenter V2 - Geometrik segmentler
4. Main Calculator - Her segment icin tuketim (TEK KAYNAK)
5. SOC Simulator - SOC simulasyonu + Hotspot tespiti
6. Station Finder - Hotspotlara istasyon
"""

import asyncio
from typing import List, Optional, Dict, Any

from app.models import (
    RouteRequest, 
    MultiStopRouteResponse, 
    DriveLeg,
    ChargeLeg,
    GeoPoint,
    StationInfo,
    ConnectorInfo,
    PlugType,
    ChargerType,
    WeatherInfo
)

from app.route_segmenter import RouteSegmenter, RouteSegment
from app.soc_simulator import (
    SOCSimulator, 
    ChargeHotspot, 
    SegmentWithConsumption,
    ChargePlanOptimizer  # Yeni: Durak sayısını minimize eden optimizer
)
from app.charging_model import calculate_charge_time
from app.consumption_engine.main_calculator import calculate_route_consumption
from app.consumption_engine.vehicle_models import get_vehicle_model
from app.route_selector import find_best_route
# Station finder artık SOCSimulator içinden çağrılıyor
from app.services.weather_service import WeatherService
from app.services.google_service import google_maps
from app.sustainability_calculator import calculate_co2_savings
from app.utils.logger import get_logger

logger = get_logger("route_planner")
weather_service = WeatherService()

DEFAULT_TEMPERATURE_C = 20.0

# =============================================================================
# VARSAYILAN DEĞERLER
# =============================================================================
DEFAULT_PASSENGER_COUNT = 1
DEFAULT_CHILD_COUNT = 0
DEFAULT_EXTRA_LOAD_KG = 0.0

# Şarj parametreleri aralıkları (V1 kural tabanlı)
MIN_SOC_RANGE = (15.0, 25.0)      # Şarj eşiği
TARGET_SOC_RANGE = (75.0, 95.0)   # Şarj hedefi
ARRIVAL_SOC_RANGE = (10.0, 25.0)  # Varış hedefi


def _resolve_defaults(request: RouteRequest) -> tuple:
    """
    Yolcu ve yük için varsayılanları çöz.
    
    Returns:
        (passenger_count, child_count, extra_load_kg)
    """
    passenger_count = request.passenger_count if request.passenger_count is not None else DEFAULT_PASSENGER_COUNT
    child_count = request.child_count if request.child_count is not None else DEFAULT_CHILD_COUNT
    extra_load_kg = request.extra_load_kg if request.extra_load_kg is not None else DEFAULT_EXTRA_LOAD_KG
    
    return passenger_count, child_count, extra_load_kg


def _calculate_base_soc_params(
    battery_kwh: float,
    start_soc: float,
    total_consumption_kwh: float,
    route_distance_km: float,
    request: RouteRequest
) -> tuple:
    """
    Temel SOC parametrelerini hesapla (charge_min_soc ve arrival_soc).
    
    NOT: charge_target_soc artık ChargePlanOptimizer tarafından dinamik olarak belirlenir.
    80% sabit hedef KALDIRILDI - optimizer 75-95% arasında en iyi değeri seçer.
    
    Kullanıcı değer girdiyse aynen kullan, None ise optimize et.
    
    Returns:
        (charge_min_soc, user_target_soc_override, arrival_soc)
        user_target_soc_override: Kullanıcı değer girdiyse o değer, yoksa None
    """
    # Mevcut enerji ve ihtiyaç
    current_energy_kwh = (start_soc / 100) * battery_kwh
    
    # Tek şarjla gidebilir miyiz?
    can_complete_direct = current_energy_kwh >= total_consumption_kwh * 1.15  # %15 güvenlik
    
    # 1. Varış SOC
    if request.target_arrival_soc_percent is not None:
        arrival_soc = request.target_arrival_soc_percent
    else:
        if can_complete_direct:
            # Şarj gerekmiyorsa, kalan SOC'u hesapla
            remaining_percent = ((current_energy_kwh - total_consumption_kwh) / battery_kwh) * 100
            arrival_soc = max(ARRIVAL_SOC_RANGE[0], min(remaining_percent, ARRIVAL_SOC_RANGE[1]))
        else:
            # Şarj gerekiyorsa, minimum varış hedefi
            arrival_soc = 15.0
    
    # 2. Şarj Eşiği (min_soc)
    if request.charge_min_soc_percent is not None:
        charge_min_soc = request.charge_min_soc_percent
    else:
        # Rota uzunluğuna göre ayarla
        if route_distance_km < 200:
            charge_min_soc = 15.0  # Kısa rota
        elif route_distance_km < 400:
            charge_min_soc = 20.0  # Orta rota
        else:
            charge_min_soc = 25.0  # Uzun rota - daha güvenli
    
    # 3. Kullanıcı target_soc override'ı (None ise optimizer belirler)
    user_target_soc_override = request.charge_target_soc_percent
    
    logger.info(
        f"Base SOC params: min={charge_min_soc}%, arrival={arrival_soc}% "
        f"(user_target_override={user_target_soc_override})"
    )
    
    return charge_min_soc, user_target_soc_override, arrival_soc


def _create_error_response(status: str, message: str = None) -> MultiStopRouteResponse:
    return MultiStopRouteResponse(
        status=status,
        total_distance_km=0,
        total_duration_minutes=0,
        total_co2_savings_kg=0,
        legs=[],
        message=message
    )


def _build_multi_legs(
    start_point: GeoPoint,
    end_point: GeoPoint,
    total_distance_km: float,
    total_duration_min: float,
    total_consumption_kwh: float,
    start_soc: float,
    final_soc: float,
    hotspots: List[ChargeHotspot],
    station_results: List,
    charge_target_soc: float,
    polyline: str,
    battery_capacity_kwh: float = 51.0,
    temperature_c: Optional[float] = None
) -> List:
    """
    Multi-leg yapısı oluştur: DriveLeg + ChargeLeg + DriveLeg + ...
    
    Her şarj durağı için:
    1. DriveLeg (önceki nokta → şarj istasyonu)
    2. ChargeLeg (şarj süresi)
    
    Son olarak:
    3. DriveLeg (son şarj istasyonu → varış)
    """
    legs = []
    
    # Şarj durağı yoksa tek DriveLeg
    if not hotspots or not station_results:
        avg_speed = (total_distance_km / total_duration_min) * 60 if total_duration_min > 0 else 60
        legs.append(DriveLeg(
            type="drive",
            start_point=start_point,
            end_point=end_point,
            distance_km=round(total_distance_km, 1),
            duration_minutes=round(total_duration_min, 1),
            avg_speed_kmh=round(avg_speed, 1),
            consumption_kwh=round(total_consumption_kwh, 2),
            start_soc_percent=round(start_soc, 1),
            end_soc_percent=round(final_soc, 1),
            polyline=polyline
        ))
        return legs
    
    # Multi-leg: Şarj durakları var
    current_point = start_point
    current_soc = start_soc
    remaining_distance = total_distance_km
    remaining_duration = total_duration_min
    remaining_consumption = total_consumption_kwh
    
    # Her hotspot + istasyon için leg oluştur
    for i, (hotspot, station_result) in enumerate(zip(hotspots, station_results)):
        if not station_result.best_station:
            continue
        
        station = station_result.best_station
        station_location = station.location
        
        # Bu segmentin mesafesi (orantılı hesapla)
        segment_ratio = hotspot.distance_from_start_km / total_distance_km if total_distance_km > 0 else 0
        leg_distance = hotspot.distance_from_start_km - (total_distance_km - remaining_distance)
        leg_duration = remaining_duration * (leg_distance / remaining_distance) if remaining_distance > 0 else 0
        leg_consumption = remaining_consumption * (leg_distance / remaining_distance) if remaining_distance > 0 else 0
        
        # 1. DriveLeg: Mevcut nokta → Şarj istasyonu
        avg_speed = (leg_distance / leg_duration) * 60 if leg_duration > 0 else 60
        legs.append(DriveLeg(
            type="drive",
            start_point=current_point,
            end_point=station_location,
            distance_km=round(max(0, leg_distance), 1),
            duration_minutes=round(max(0, leg_duration), 1),
            avg_speed_kmh=round(avg_speed, 1),
            consumption_kwh=round(max(0, leg_consumption), 2),
            start_soc_percent=round(current_soc, 1),
            end_soc_percent=round(hotspot.soc_at_point, 1)
        ))
        
        # 2. ChargeLeg: Şarj süresi hesapla (gerçekçi eğri modeli)
        # Her hotspot için BAĞIMSIZ şarj hedefi (recommended_charge_to)
        hotspot_target_soc = hotspot.recommended_charge_to
        charge_power_kw = station.power_kw if station.power_kw > 0 else 50.0
        
        # Gerçekçi şarj süresi hesapla (3 fazlı CC-CV eğrisi + hava durumu)
        charge_result = calculate_charge_time(
            start_soc=hotspot.soc_at_point,
            target_soc=hotspot_target_soc,
            battery_capacity_kwh=battery_capacity_kwh,
            peak_power_kw=charge_power_kw,
            temperature_c=temperature_c
        )
        charge_duration = charge_result.duration_minutes
        kwh_to_add = charge_result.energy_added_kwh
        
        # StationInfo oluştur (model uyumlu)
        station_info = StationInfo(
            id=station.station_id,
            name=station.station_name,
            location=station_location,
            connectors=[
                ConnectorInfo(
                    plug_type=PlugType.CCS2,
                    charger_type=ChargerType.DC,
                    power_kw=station.power_kw if station.power_kw > 0 else 50.0
                )
            ]
        )
        
        legs.append(ChargeLeg(
            type="charge",
            station=station_info,
            arrival_soc_percent=round(hotspot.soc_at_point, 1),
            target_soc_percent=round(hotspot_target_soc, 1),
            energy_added_kwh=round(kwh_to_add, 2),
            duration_minutes=round(max(10, charge_duration), 1)
        ))
        
        # Güncellemeler
        current_point = station_location
        current_soc = hotspot_target_soc
        remaining_distance -= leg_distance
        remaining_duration -= leg_duration
        remaining_consumption -= leg_consumption
    
    # Son DriveLeg: Son şarj istasyonu → Varış
    if remaining_distance > 0:
        avg_speed = (remaining_distance / remaining_duration) * 60 if remaining_duration > 0 else 60
        legs.append(DriveLeg(
            type="drive",
            start_point=current_point,
            end_point=end_point,
            distance_km=round(remaining_distance, 1),
            duration_minutes=round(max(0, remaining_duration), 1),
            avg_speed_kmh=round(avg_speed, 1),
            consumption_kwh=round(max(0, remaining_consumption), 2),
            start_soc_percent=round(current_soc, 1),
            end_soc_percent=round(final_soc, 1)
        ))
    
    logger.info(f"Multi-leg built: {len(legs)} legs (drive + charge)")
    return legs


async def plan_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    Route Planner - Clean Architecture.
    
    Akış:
    1-4: Rota seçimi, elevation, hava durumu
    5: Varsayılanları çöz (yolcu, yük)
    6: Segmentasyon
    7: Tüketim hesabı (MainCalculator)
    8: Akıllı SOC parametreleri (V1 kural tabanlı, V2'de ML)
    9-10: SOC simülasyonu + İstasyon bulma
    11: Multi-leg oluşturma
    12: CO2 tasarrufu
    """
    logger.info("Route planning started", 
                start=f"{request.start_location.lat},{request.start_location.lon}",
                end=f"{request.end_location.lat},{request.end_location.lon}")
    
    try:
        # STEP 1: Arac bilgilerini al
        try:
            vehicle = get_vehicle_model(request.vehicle_model_id)
        except ValueError as e:
            return _create_error_response("error_vehicle_not_found", str(e))
        
        battery_kwh = vehicle.battery_capacity_kwh
        
        # STEP 2: En iyi rotayi sec
        try:
            route_result = await find_best_route(
                origin=request.start_location,
                destination=request.end_location,
                vehicle_model_id=request.vehicle_model_id,
                extra_load_kg=request.extra_load_kg
            )
        except Exception as e:
            return _create_error_response("error_route_failed", str(e))
        
        selected_route = route_result["selected_route"]
        polyline = route_result.get("polyline", "")
        route_leg = selected_route["legs"][0]
        
        route_distance_km = route_leg["distance"]["value"] / 1000
        route_duration_min = route_leg["duration"]["value"] / 60
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
        
        # STEP 4: Hava durumu al
        avg_weather = None
        try:
            start_weather = await weather_service.get_weather_at_point(
                request.start_location.lat, request.start_location.lon
            )
            end_weather = await weather_service.get_weather_at_point(
                request.end_location.lat, request.end_location.lon
            )
            
            if start_weather and end_weather:
                avg_temp = (start_weather.temp_c + end_weather.temp_c) / 2
                avg_wind = (start_weather.wind_speed_mps + end_weather.wind_speed_mps) / 2
                avg_weather = WeatherInfo(
                    temp_c=avg_temp,
                    condition=start_weather.condition,
                    wind_speed_mps=avg_wind,
                    wind_direction_deg=0,
                    precipitation_prob=0.0
                )
                logger.info(f"Weather: {round(avg_temp, 1)}C, {start_weather.condition.value}")
        except Exception as e:
            logger.warning(f"Weather API failed: {e}")
        
        # STEP 5: Varsayılanları Çöz (yolcu, yük)
        passenger_count, child_count, extra_load_kg = _resolve_defaults(request)
        logger.info(f"Resolved defaults: passengers={passenger_count}, children={child_count}, load={extra_load_kg}kg")
        
        # STEP 6: Route Segmenter - Geometrik segmentasyon
        segmenter = RouteSegmenter(segment_length_km=10.0)
        segments = segmenter.create_segments(
            polyline=polyline,
            total_elevation_gain_m=elevation_gain_m,
            total_elevation_loss_m=elevation_loss_m
        )
        
        logger.info(f"Segments created: {len(segments)} segments")
        
        # STEP 7: Main Calculator - Her segment için tüketim (TEK KAYNAK)
        segments_with_consumption = calculate_route_consumption(
            vehicle=vehicle,
            segments=segments,
            temperature_celsius=avg_weather.temp_c if avg_weather else DEFAULT_TEMPERATURE_C,
            wind_speed_mps=avg_weather.wind_speed_mps if avg_weather else 0.0,
            weather_condition=avg_weather.condition.value if avg_weather else "clear",
            extra_load_kg=extra_load_kg,
            passenger_count=passenger_count,
            child_count=child_count
        )
        
        total_consumption = sum(s.consumption_kwh for s in segments_with_consumption)
        logger.info(f"Total consumption calculated: {round(total_consumption, 2)}kWh")
        
        # STEP 8: Temel SOC Parametreleri (min_soc, arrival_soc)
        charge_min_soc, user_target_soc_override, arrival_soc = _calculate_base_soc_params(
            battery_kwh=battery_kwh,
            start_soc=request.current_soc_percent,
            total_consumption_kwh=total_consumption,
            route_distance_km=route_distance_km,
            request=request
        )
        
        # STEP 9: ChargePlanOptimizer - Durak sayısını minimize eden optimal target_soc
        # Kullanıcı target_soc verdiyse → doğrudan kullan (override)
        # Kullanıcı vermediyse → optimizer 75-95% arasında en iyi değeri seçer
        
        avg_speed_kmh = (route_distance_km / route_duration_min) * 60 if route_duration_min > 0 else 80.0
        
        if user_target_soc_override is not None:
            # Kullanıcı override → eski yöntem (sabit target_soc)
            charge_target_soc = user_target_soc_override
            simulator = SOCSimulator(
                battery_capacity_kwh=battery_kwh,
                start_soc=request.current_soc_percent,
                target_arrival_soc=arrival_soc,
                charge_min_soc=charge_min_soc,
                charge_target_soc=charge_target_soc
            )
            sim_result = simulator.simulate(segments_with_consumption, route_distance_km)
            logger.info(f"User override target_soc={charge_target_soc}%, stops={len(sim_result.hotspots)}")
        else:
            # Optimizer → 75-95% arasında en az durak üreten target_soc'u bul
            optimizer = ChargePlanOptimizer(battery_capacity_kwh=battery_kwh)
            charge_target_soc, sim_result = optimizer.find_optimal_plan(
                segments_with_consumption=segments_with_consumption,
                total_distance_km=route_distance_km,
                battery_capacity_kwh=battery_kwh,
                start_soc=request.current_soc_percent,
                target_arrival_soc=arrival_soc,
                charge_min_soc=charge_min_soc,
                avg_speed_kmh=avg_speed_kmh
            )
        
        # STEP 10: Hotspotlar için istasyon bulma
        hotspots = sim_result.hotspots
        station_results = []
        
        if hotspots:
            from app.station_finder import find_stations_for_hotspots
            station_results = await find_stations_for_hotspots(
                hotspots,
                request.vehicle_model_id
            )
        
        charge_stops = sum(1 for r in station_results if r.best_station) if station_results else 0
        
        logger.info(
            f"SOC simulation + stations: {len(hotspots)} hotspots, "
            f"{charge_stops} stations, final_soc={sim_result.final_soc}%"
        )
        
        # STEP 11: Multi-Leg Builder (gerçekçi şarj süreleri için hava durumu dahil)
        legs = _build_multi_legs(
            start_point=GeoPoint(lat=start_coords["lat"], lon=start_coords["lng"]),
            end_point=GeoPoint(lat=end_coords["lat"], lon=end_coords["lng"]),
            total_distance_km=route_distance_km,
            total_duration_min=route_duration_min,
            total_consumption_kwh=total_consumption,
            start_soc=request.current_soc_percent,
            final_soc=sim_result.final_soc,
            hotspots=hotspots,
            station_results=station_results,
            charge_target_soc=charge_target_soc,
            polyline=polyline,
            battery_capacity_kwh=battery_kwh,
            temperature_c=avg_weather.temp_c if avg_weather else None
        )
        
        end_soc = sim_result.final_soc
        
        # STEP 12: CO2 tasarrufu
        try:
            co2_savings = calculate_co2_savings(route_distance_km)
        except:
            co2_savings = 0.0
        
        # Mesaj oluştur
        if charge_stops > 0:
            message = f"{charge_stops} şarj durağı gerekli (hedef: %{round(charge_target_soc)})"
        elif sim_result.can_complete_without_charging:
            message = f"Şarj gerekmez. Varış SOC: %{round(end_soc)}"
        else:
            message = f"Dikkat! Varış SOC: %{round(end_soc)}"
        
        logger.info(f"Route planning completed: {message}")
        
        return MultiStopRouteResponse(
            status="success",
            total_distance_km=round(route_distance_km, 1),
            total_duration_minutes=round(route_duration_min, 1),
            total_co2_savings_kg=round(co2_savings, 2),
            consumption_kwh=round(total_consumption, 1),
            legs=legs,
            charge_stops=charge_stops,
            message=message
        )
        
    except Exception as e:
        logger.exception(f"Route planning failed: {e}")
        return _create_error_response("error_unknown", str(e))
