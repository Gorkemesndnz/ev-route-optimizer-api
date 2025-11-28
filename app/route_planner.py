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
    ChargerType
)

from app.route_segmenter import RouteSegmenter, RouteSegment
from app.soc_simulator import SOCSimulator, ChargeHotspot, SegmentWithConsumption
from app.consumption_engine.main_calculator import calculate_route_consumption
from app.consumption_engine.vehicle_models import get_vehicle_model
from app.route_selector import find_best_route
# Station finder artık SOCSimulator içinden çağrılıyor
from app.services.weather_service import WeatherService
from app.services.google_service import google_maps
from app.sustainability_calculator import calculate_co2_savings
from app.utils.logger import get_logger

logger = get_logger("route_planner_v2")
weather_service = WeatherService()

DEFAULT_TEMPERATURE_C = 20.0


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
    polyline: str
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
        
        # 2. ChargeLeg: Şarj süresi hesapla
        soc_to_add = charge_target_soc - hotspot.soc_at_point
        charge_power_kw = station.power_kw if station.power_kw > 0 else 50.0
        # Basit hesap: kWh = SOC * batarya / 100
        kwh_to_add = (soc_to_add / 100) * 51  # TODO: Gerçek batarya kapasitesi
        charge_duration = (kwh_to_add / charge_power_kw) * 60  # dakika
        
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
            target_soc_percent=round(charge_target_soc, 1),
            energy_added_kwh=round(kwh_to_add, 2),
            duration_minutes=round(max(10, charge_duration), 1)
        ))
        
        # Güncellemeler
        current_point = station_location
        current_soc = charge_target_soc
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


async def plan_route_v2(request: RouteRequest) -> MultiStopRouteResponse:
    """
    V2.0 Route Planner - Clean Architecture.
    """
    logger.info("V2.0 Route planning started", 
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
        
        # STEP 5: Route Segmenter V2 - Geometrik segmentasyon
        segmenter = RouteSegmenter(segment_length_km=10.0)
        segments = segmenter.create_segments(
            polyline=polyline,
            total_elevation_gain_m=elevation_gain_m,
            total_elevation_loss_m=elevation_loss_m
        )
        
        logger.info(f"Segments created: {len(segments)} segments")
        
        # STEP 6: Main Calculator - Her segment icin tuketim (TEK KAYNAK)
        segments_with_consumption = calculate_route_consumption(
            vehicle=vehicle,
            segments=segments,
            temperature_celsius=avg_weather.temp_c if avg_weather else DEFAULT_TEMPERATURE_C,
            wind_speed_mps=avg_weather.wind_speed_mps if avg_weather else 0.0,
            weather_condition=avg_weather.condition.value if avg_weather else "clear",
            extra_load_kg=request.extra_load_kg,
            passenger_count=request.passenger_count,
            child_count=request.child_count
        )
        
        total_consumption = sum(s.consumption_kwh for s in segments_with_consumption)
        logger.info(f"Total consumption calculated: {round(total_consumption, 2)}kWh")
        
        # STEP 7: SOC Simulator - Hotspot tespiti
        simulator = SOCSimulator(
            battery_capacity_kwh=battery_kwh,
            start_soc=request.current_soc_percent,
            target_arrival_soc=request.target_arrival_soc_percent,
            charge_min_soc=request.charge_min_soc_percent,
            charge_target_soc=request.charge_target_soc_percent
        )
        
        # STEP 7+8: SOC Simülasyonu + İstasyon Bulma (TEK ADIMDA)
        sim_with_stations = await simulator.simulate_with_stations(
            segments_with_consumption, 
            route_distance_km,
            request.vehicle_model_id
        )
        
        hotspots = sim_with_stations.hotspots
        charge_stops = sim_with_stations.charge_stops_count
        
        logger.info(
            f"SOC simulation + stations: {len(hotspots)} hotspots, "
            f"{charge_stops} stations, final_soc={sim_with_stations.final_soc}%"
        )
        
        # STEP 9: Multi-Leg Builder
        legs = _build_multi_legs(
            start_point=GeoPoint(lat=start_coords["lat"], lon=start_coords["lng"]),
            end_point=GeoPoint(lat=end_coords["lat"], lon=end_coords["lng"]),
            total_distance_km=route_distance_km,
            total_duration_min=route_duration_min,
            total_consumption_kwh=total_consumption,
            start_soc=request.current_soc_percent,
            final_soc=sim_with_stations.final_soc,
            hotspots=hotspots,
            station_results=sim_with_stations.station_results,
            charge_target_soc=request.charge_target_soc_percent,
            polyline=polyline
        )
        
        end_soc = sim_with_stations.final_soc
        
        # STEP 10: CO2 tasarrufu
        try:
            co2_savings = calculate_co2_savings(route_distance_km)
        except:
            co2_savings = 0.0
        
        # Mesaj olustur
        if charge_stops > 0:
            message = f"V2.0: {charge_stops} sarj duragi gerekli"
        elif sim_with_stations.simulation.can_complete_without_charging:
            message = f"V2.0: Sarj gerekmez. Varis SOC: %{round(end_soc)}"
        else:
            message = f"V2.0: Dikkat! Varis SOC: %{round(end_soc)}"
        
        logger.info(f"V2.0 Route planning completed: {message}")
        
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
        logger.exception(f"V2.0 Route planning failed: {e}")
        return _create_error_response("error_unknown", str(e))
