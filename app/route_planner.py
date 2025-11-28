"""
Route Planner v2.0
===================

V1.3 Enterprise Route Planner - Tek DriveLeg ile basit rota planlaması.

Özellikler:
- Google Directions alternatif rotaları ile optimal seçim
- Elevation API ile yükselme/iniş hesabı
- V1 kural tabanlı tüketim motoru
- CO2 tasarrufu hesaplama
- ML training data loglama

Kullanım:
    from app.route_planner import plan_multi_stop_route, plan_full_route
    
    response = await plan_full_route(request)
"""

import asyncio
from typing import Dict, Any, Optional, List, Union
from app.models import (
    RouteRequest, 
    MultiStopRouteResponse, 
    DriveLeg,
    ChargeLeg,
    GeoPoint,
    StationInfo,
    ConnectorInfo,
    StationAmenity,
    PlugType,
    ChargerType
)
# V1.5 Modülleri
from app.route_segmenter import RouteSegmenter, ChargeHotspot, create_route_segments
from app.station_finder import CorridorSearcher, CorridorStation, find_stations_for_hotspots
from app.services.ocm_service import ocm_service
from app.services.weather_service import WeatherService
from app.route_selector import find_best_route
from app.consumption_engine.main_calculator import calculate_segment_consumption_kwh
from app.consumption_engine.vehicle_models import get_vehicle_model
from app.sustainability_calculator import calculate_co2_savings
from app.utils.logger import get_logger
from app.utils.data_logger import log_training_data
from app.services.google_service import google_maps
from app.services.base_service import ExternalAPIError

# =============================================================================
# CONSTANTS
# =============================================================================
logger = get_logger("route_planner")
weather_service = WeatherService()

SIMULATION_SEGMENT_KM = 1.0
DEFAULT_TARGET_SOC_PERCENT = 80
SOC_SAFETY_BUFFER_PERCENT = 15.0
DEFAULT_TEMPERATURE_C = 20.0


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _create_error_response(status: str, message: str = None) -> MultiStopRouteResponse:
    """Hata durumunda standart response oluştur."""
    return MultiStopRouteResponse(
        status=status,
        total_distance_km=0,
        total_duration_minutes=0,
        total_co2_savings_kg=0,
        legs=[],
        message=message
    )


def _safe_soc_percent(soc_kwh: float, battery_kwh: float) -> float:
    """SOC'u güvenli yüzdeye çevir (0-100 arası)."""
    if battery_kwh <= 0:
        return 0.0
    percent = (soc_kwh / battery_kwh) * 100
    return max(0.0, min(100.0, percent))


def _corridor_station_to_station_info(corridor_station: CorridorStation) -> StationInfo:
    """
    V1.5: CorridorStation'ı StationInfo modeline dönüştür.
    
    Args:
        corridor_station: Koridor aramasından gelen istasyon
        
    Returns:
        StationInfo modeli
    """
    station_data = corridor_station.station_info
    address_info = station_data.get("AddressInfo", {})
    connections = station_data.get("Connections", [])
    
    # Connector bilgilerini dönüştür
    connectors = []
    for conn in connections:
        power_kw = conn.get("PowerKW") or 0
        if power_kw <= 0:
            continue
            
        # Charger type belirle
        if power_kw >= 150:
            charger_type = ChargerType.HPC
        elif power_kw >= 40:
            charger_type = ChargerType.DC
        else:
            charger_type = ChargerType.AC
        
        # Plug type belirle
        connection_type = conn.get("ConnectionType", {})
        type_id = connection_type.get("ID", 0)
        plug_type = PlugType.CCS2  # Default
        if type_id == 2:
            plug_type = PlugType.CHADEMO
        elif type_id in (25, 1036):
            plug_type = PlugType.TYPE2
        elif type_id == 33:
            plug_type = PlugType.CCS2
        
        connectors.append(ConnectorInfo(
            plug_type=plug_type,
            charger_type=charger_type,
            power_kw=float(power_kw),
            status="Available"
        ))
    
    # En az bir connector olmalı
    if not connectors:
        connectors.append(ConnectorInfo(
            plug_type=PlugType.CCS2,
            charger_type=ChargerType.DC,
            power_kw=corridor_station.power_kw,
            status="Available"
        ))
    
    return StationInfo(
        id=corridor_station.station_id,
        name=corridor_station.station_name,
        operator=station_data.get("OperatorInfo", {}).get("Title"),
        location=corridor_station.location,
        rating=corridor_station.rating,
        connectors=connectors,
        amenities=StationAmenity(),
        distance_from_route_km=corridor_station.deviation_km
    )


def _calculate_charge_duration_minutes(
    current_soc: float,
    target_soc: float,
    battery_kwh: float,
    power_kw: float
) -> float:
    """
    Şarj süresini hesapla.
    
    Basit linear model (V2'de charging curve eklenecek)
    
    Args:
        current_soc: Mevcut SOC (%)
        target_soc: Hedef SOC (%)
        battery_kwh: Batarya kapasitesi
        power_kw: Şarj gücü
        
    Returns:
        Şarj süresi (dakika)
    """
    if power_kw <= 0:
        return 0.0
    
    soc_delta = target_soc - current_soc
    if soc_delta <= 0:
        return 0.0
    
    energy_needed_kwh = (soc_delta / 100.0) * battery_kwh
    
    # Şarj verimliliği %90
    efficiency = 0.90
    actual_power = power_kw * efficiency
    
    # %80 üstünde şarj yavaşlar (basit model)
    if target_soc > 80:
        # Ortalama güç düşüşü
        actual_power *= 0.7
    
    hours = energy_needed_kwh / actual_power
    return hours * 60  # dakikaya çevir


def _create_charge_leg(
    station: StationInfo,
    arrival_soc: float,
    target_soc: float,
    battery_kwh: float
) -> ChargeLeg:
    """
    V1.5: ChargeLeg oluştur.
    
    Args:
        station: İstasyon bilgisi
        arrival_soc: Varış SOC (%)
        target_soc: Hedef SOC (%) - Max 100
        battery_kwh: Batarya kapasitesi
        
    Returns:
        ChargeLeg modeli
    """
    # V1.5 FIX: Max %100 limiti
    target_soc = min(100.0, target_soc)
    arrival_soc = max(0.0, min(100.0, arrival_soc))
    
    # En yüksek güçlü connector'ı bul
    max_power = max((c.power_kw for c in station.connectors), default=50.0)
    
    # Şarj süresi hesapla
    duration = _calculate_charge_duration_minutes(
        current_soc=arrival_soc,
        target_soc=target_soc,
        battery_kwh=battery_kwh,
        power_kw=max_power
    )
    
    # Enerji miktarı
    energy_added = ((target_soc - arrival_soc) / 100.0) * battery_kwh
    
    # Fiyat tahmini (ortalama 8 TL/kWh)
    price_per_kwh = 13.0
    estimated_cost = energy_added * price_per_kwh
    
    return ChargeLeg(
        type="charge",
        station=station,
        arrival_soc_percent=round(arrival_soc, 1),
        target_soc_percent=round(target_soc, 1),
        energy_added_kwh=round(energy_added, 2),
        duration_minutes=round(duration, 1),
        price_per_kwh=price_per_kwh,
        estimated_cost=round(estimated_cost, 2),
        currency="TRY"
    )


# =============================================================================
# MAIN PLANNER FUNCTION
# =============================================================================

async def plan_multi_stop_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    V1.3 Enterprise Route Planner
    ==============================
    
    Tek DriveLeg ile basit rota planlaması yapar.
    Şarj durakları V2'de eklenecek.
    
    Args:
        request: RouteRequest - başlangıç/bitiş, araç ve SOC bilgileri
    
    Returns:
        MultiStopRouteResponse: Planlanmış rota ve tüketim bilgileri
    
    Flow:
        1. Araç bilgilerini al
        2. Route selector ile en iyi rotayı seç
        3. Elevation API'den yükselme/iniş verisi al
        4. Consumption engine ile tüketim hesapla
        5. CO2 tasarrufu hesapla
        6. Training data logla
        7. Response döndür
    """
    logger.debug(
        "Route planning initiated",
        start=f"{request.start_location.lat},{request.start_location.lon}",
        end=f"{request.end_location.lat},{request.end_location.lon}",
        vehicle=request.vehicle_model_id,
        initial_soc=request.current_soc_percent
    )
    
    try:
        # =====================================================================
        # STEP A: Araç bilgilerini al
        # =====================================================================
        try:
            vehicle = get_vehicle_model(request.vehicle_model_id)
        except ValueError as e:
            logger.error("Vehicle model not found", vehicle_id=request.vehicle_model_id, error=str(e))
            return _create_error_response(
                "error_vehicle_not_found",
                f"Araç modeli bulunamadı: {request.vehicle_model_id}"
            )
        
        logger.info(
            "Route planning started",
            start=f"{request.start_location.lat},{request.start_location.lon}",
            end=f"{request.end_location.lat},{request.end_location.lon}",
            vehicle=vehicle.model_name,
            battery_kwh=vehicle.battery_capacity_kwh,
            initial_soc=request.current_soc_percent,
            extra_load_kg=request.extra_load_kg
        )
        
        # =====================================================================
        # STEP B: En iyi rotayı seç (route_selector)
        # =====================================================================
        try:
            route_result = await find_best_route(
                origin=request.start_location,
                destination=request.end_location,
                vehicle_model_id=request.vehicle_model_id,
                extra_load_kg=request.extra_load_kg
            )
        except Exception as e:
            logger.exception("Route selector failed", error=str(e))
            return _create_error_response(
                "error_route_selector_failed",
                f"Rota seçimi başarısız: {str(e)}"
            )
        
        selected_route = route_result["selected_route"]
        selection_reason = route_result["selection_reason"]
        polyline = route_result.get("polyline", "")
        
        logger.info(
            "Route selected",
            selection_reason=selection_reason,
            distance_km=route_result.get("selected_distance_km"),
            duration_min=route_result.get("selected_duration_min")
        )
        
        # =====================================================================
        # STEP C: Rota verilerini çıkar
        # =====================================================================
        try:
            route_leg = selected_route["legs"][0]
            start_coords = route_leg["start_location"]
            end_coords = route_leg["end_location"]
            
            route_distance_km = route_leg["distance"]["value"] / 1000  # metre → km
            route_duration_min = route_leg["duration"]["value"] / 60   # saniye → dakika
            
            # Polyline yoksa route'dan al
            if not polyline:
                polyline = selected_route.get("overview_polyline", {}).get("points", "")
                
        except (KeyError, IndexError) as e:
            logger.error("Route data extraction failed", error=str(e))
            return _create_error_response(
                "error_no_route_legs_found",
                "Google Directions verisi eksik"
            )
        
        logger.info(
            "Route data extracted",
            distance_km=round(route_distance_km, 1),
            duration_min=round(route_duration_min, 1)
        )
        
        # =====================================================================
        # STEP D: Elevation verisi al
        # =====================================================================
        elevation_gain_m = 0.0
        elevation_loss_m = 0.0
        
        if polyline:
            try:
                elevation_stats = await google_maps.get_elevation_stats(polyline)
                elevation_gain_m = elevation_stats.get("gain_m", 0.0)
                elevation_loss_m = elevation_stats.get("loss_m", 0.0)
                
                logger.info(
                    "Elevation data retrieved",
                    gain_m=round(elevation_gain_m, 1),
                    loss_m=round(elevation_loss_m, 1)
                )
            except Exception as e:
                logger.warning(
                    "Elevation API failed, using defaults", 
                    error=str(e),
                    error_type=type(e).__name__,
                    polyline_length=len(polyline) if polyline else 0
                )
        
        # =====================================================================
        # STEP E: Tüketim hesapla (V1 Engine)
        # =====================================================================
        try:
            segment_consumption_kwh = calculate_segment_consumption_kwh(
                vehicle=vehicle,
                segment_distance_km=route_distance_km,
                segment_elevation_gain_m=elevation_gain_m,
                segment_elevation_loss_m=elevation_loss_m,
                temperature_celsius=DEFAULT_TEMPERATURE_C,
                extra_load_kg=request.extra_load_kg,
                passenger_count=request.passenger_count,
                engine_version="v1"
            )
        except Exception as e:
            logger.exception("Consumption calculation failed", error=str(e))
            return _create_error_response(
                "error_consumption_failed",
                f"Tüketim hesaplaması başarısız: {str(e)}"
            )
        
        # =====================================================================
        # STEP F: SOC hesapla
        # =====================================================================
        battery_kwh = vehicle.battery_capacity_kwh
        start_soc_kwh = battery_kwh * (request.current_soc_percent / 100)
        arrival_soc_kwh = start_soc_kwh - segment_consumption_kwh
        
        start_soc_percent = _safe_soc_percent(start_soc_kwh, battery_kwh)
        arrival_soc_percent = _safe_soc_percent(arrival_soc_kwh, battery_kwh)
        
        logger.info(
            "Battery calculation completed",
            start_soc_percent=round(start_soc_percent, 1),
            arrival_soc_percent=round(arrival_soc_percent, 1),
            consumption_kwh=round(segment_consumption_kwh, 2)
        )
        
        # =====================================================================
        # STEP G: Weather verisi al (başlangıç ve varış noktaları)
        # =====================================================================
        start_weather = None
        end_weather = None
        
        try:
            # Parallel weather API calls
            start_weather_task = weather_service.get_weather_at_point(
                lat=request.start_location.lat,
                lon=request.start_location.lon
            )
            end_weather_task = weather_service.get_weather_at_point(
                lat=request.end_location.lat,
                lon=request.end_location.lon
            )
            
            start_weather, end_weather = await asyncio.gather(
                start_weather_task,
                end_weather_task,
                return_exceptions=True
            )
            
            # Exception handling
            if isinstance(start_weather, Exception):
                logger.warning("Start weather fetch failed", error=str(start_weather))
                start_weather = None
            if isinstance(end_weather, Exception):
                logger.warning("End weather fetch failed", error=str(end_weather))
                end_weather = None
                
            logger.info(
                "Weather data retrieved",
                start_temp=start_weather.temp_c if start_weather else None,
                end_temp=end_weather.temp_c if end_weather else None
            )
            
        except Exception as e:
            logger.warning("Weather API failed, using defaults", error=str(e))
        
        # =====================================================================
        # STEP H: Average weather hesapla ve consumption engine'e geç
        # =====================================================================
        from app.models import WeatherInfo, WeatherCondition
        
        # Average weather hesapla (start + end)
        avg_weather = None
        if start_weather and end_weather:
            avg_temp_c = (start_weather.temp_c + end_weather.temp_c) / 2
            avg_wind_speed_mps = (start_weather.wind_speed_mps + end_weather.wind_speed_mps) / 2
            avg_wind_direction_deg = (start_weather.wind_direction_deg + end_weather.wind_direction_deg) / 2
            avg_precipitation_prob = (start_weather.precipitation_prob + end_weather.precipitation_prob) / 2
            
            # Condition: daha kötü hava durumunu seç
            if start_weather.condition in (WeatherCondition.RAIN, WeatherCondition.SNOW) or end_weather.condition in (WeatherCondition.RAIN, WeatherCondition.SNOW):
                avg_condition = WeatherCondition.RAIN
            elif start_weather.condition == WeatherCondition.FOG or end_weather.condition == WeatherCondition.FOG:
                avg_condition = WeatherCondition.FOG
            elif start_weather.condition == WeatherCondition.WINDY or end_weather.condition == WeatherCondition.WINDY:
                avg_condition = WeatherCondition.WINDY
            else:
                avg_condition = WeatherCondition.CLEAR
            
            avg_weather = WeatherInfo(
                temp_c=avg_temp_c,
                condition=avg_condition,
                wind_speed_mps=avg_wind_speed_mps,
                wind_direction_deg=int(avg_wind_direction_deg),
                precipitation_prob=avg_precipitation_prob
            )
            
            logger.info(
                "Average weather calculated",
                avg_temp_c=round(avg_temp_c, 1),
                avg_condition=avg_condition.value,
                avg_wind_speed_mps=round(avg_wind_speed_mps, 1)
            )
        
        # =====================================================================
        # STEP I: Tüketim hesapla (V1 Engine with REAL weather)
        # =====================================================================
        try:
            segment_consumption_kwh = calculate_segment_consumption_kwh(
                vehicle=vehicle,
                segment_distance_km=route_distance_km,
                segment_elevation_gain_m=elevation_gain_m,
                segment_elevation_loss_m=elevation_loss_m,
                temperature_celsius=avg_weather.temp_c if avg_weather else DEFAULT_TEMPERATURE_C,
                extra_load_kg=request.extra_load_kg,
                passenger_count=request.passenger_count,
                engine_version="v1"
            )
        except Exception as e:
            logger.exception("Consumption calculation failed", error=str(e))
            return _create_error_response(
                "error_consumption_failed",
                f"Tüketim hesaplaması başarısız: {str(e)}"
            )
        
        # =====================================================================
        # STEP J: Multi-Leg Route with Charging Algorithm
        # =====================================================================
        legs = []
        charge_stops = 0
        total_distance = route_distance_km
        total_duration = route_duration_min
        
        # Max range hesapla (mevcut SOC ile)
        available_kwh = (request.current_soc_percent / 100.0) * battery_kwh
        usable_kwh = available_kwh * (1.0 - SOC_SAFETY_BUFFER_PERCENT / 100.0)
        consumption_per_km = vehicle.base_consumption_wh_km / 1000.0  # Wh/km -> kWh/km
        max_range_km = usable_kwh / consumption_per_km if consumption_per_km > 0 else 0
        
        logger.info(
            "Route segment analysis",
            current_soc=round(request.current_soc_percent, 1),
            available_kwh=round(available_kwh, 2),
            usable_kwh=round(usable_kwh, 2),
            max_range_km=round(max_range_km, 1),
            route_distance_km=round(route_distance_km, 1)
        )
        
        # Şarj gerekli mi kontrol et
        charging_needed = max_range_km < route_distance_km
        route_message = None
        charge_stops = 0
        
        # =================================================================
        # V1.5: Dinamik Şarj Planlama Algoritması
        # =================================================================
        if charging_needed:
            logger.info(
                "V1.5 charging algorithm started",
                max_range_km=round(max_range_km, 1),
                route_distance_km=round(route_distance_km, 1)
            )
            
            try:
                # 1️⃣ Route Segmentation - Polyline'ı parçalara ayır
                # V1.5: Kullanıcıdan gelen hedef SOC değerlerini kullan
                target_arrival_soc = getattr(request, 'target_arrival_soc_percent', 20.0)
                charge_min_soc = getattr(request, 'charge_min_soc_percent', 20.0)
                charge_target_soc = getattr(request, 'charge_target_soc_percent', 80.0)
                
                logger.info(
                    "V1.5 SOC settings",
                    target_arrival=target_arrival_soc,
                    charge_min=charge_min_soc,
                    charge_target=charge_target_soc
                )
                
                segmenter = RouteSegmenter(
                    vehicle=vehicle,
                    start_soc=request.current_soc_percent,
                    target_arrival_soc=target_arrival_soc,
                    charge_min_soc=charge_min_soc,
                    charge_target_soc=charge_target_soc
                )
                
                segments = segmenter.create_segments_from_polyline(
                    polyline=polyline,
                    total_elevation_gain_m=elevation_gain_m,
                    total_elevation_loss_m=elevation_loss_m
                )
                
                # 2️⃣ Hotspot Detection - Şarj gerekli noktaları bul
                hotspots = segmenter.find_charge_hotspots()
                
                logger.info(
                    "Route segmentation completed",
                    segment_count=len(segments),
                    hotspot_count=len(hotspots)
                )
                
                if hotspots:
                    # 3️⃣ Corridor Search - Her hotspot için istasyon ara
                    search_results = await find_stations_for_hotspots(
                        hotspots=hotspots,
                        vehicle_model_id=request.vehicle_model_id
                    )
                    
                    # 4️⃣ Multi-Leg Route Oluştur
                    current_soc = request.current_soc_percent
                    current_point = GeoPoint(lat=start_coords["lat"], lon=start_coords["lng"])
                    leg_start_km = 0.0
                    
                    for i, result in enumerate(search_results):
                        if result.best_station:
                            station_info = _corridor_station_to_station_info(result.best_station)
                            hotspot = result.hotspot
                            
                            # DriveLeg - Mevcut noktadan istasyona
                            drive_distance = hotspot.segment_index * 10.0  # Segment * 10km
                            drive_duration = (drive_distance / route_distance_km) * route_duration_min
                            
                            drive_leg = DriveLeg(
                                type="drive",
                                start_point=current_point,
                                end_point=station_info.location,
                                distance_km=round(drive_distance - leg_start_km, 1),
                                duration_minutes=round(drive_duration, 1),
                                avg_speed_kmh=round((drive_distance / drive_duration) * 60 if drive_duration > 0 else 60, 1),
                                consumption_kwh=round((drive_distance - leg_start_km) * consumption_per_km, 2),
                                start_soc_percent=round(current_soc, 1),
                                end_soc_percent=round(hotspot.soc_at_point, 1),
                                elevation_gain_m=round(elevation_gain_m * (drive_distance / route_distance_km), 1),
                                elevation_loss_m=round(elevation_loss_m * (drive_distance / route_distance_km), 1),
                                polyline=""
                            )
                            legs.append(drive_leg)
                            
                            # ChargeLeg - İstasyonda şarj
                            # V1.5 FIX: Akıllı şarj hedefi
                            is_last_stop = (i == len(search_results) - 1) or not any(
                                r.best_station for r in search_results[i+1:]
                            )
                            
                            if is_last_stop:
                                # Son durak: Varışa yetecek kadar şarj et
                                remaining_km = route_distance_km - drive_distance
                                soc_needed = (remaining_km * consumption_per_km / battery_kwh) * 100
                                min_required = soc_needed + target_arrival_soc
                            else:
                                # Ara durak: Sonraki durağa yetecek kadar + kullanıcı eşiği
                                next_hotspot_km = search_results[i+1].hotspot.segment_index * 10.0
                                distance_to_next = next_hotspot_km - drive_distance
                                soc_needed = (distance_to_next * consumption_per_km / battery_kwh) * 100
                                min_required = soc_needed + charge_min_soc  # Sonrakine varınca şarj eşiğinde ol
                            
                            # Kullanıcı hedefi yeterliyse onu kullan, yetmezse minimum gerekli
                            actual_charge_target = min(100.0, max(charge_target_soc, min_required))
                            
                            charge_leg = _create_charge_leg(
                                station=station_info,
                                arrival_soc=hotspot.soc_at_point,
                                target_soc=actual_charge_target,
                                battery_kwh=battery_kwh
                            )
                            legs.append(charge_leg)
                            charge_stops += 1
                            
                            # Durumu güncelle
                            current_soc = actual_charge_target
                            current_point = station_info.location
                            leg_start_km = drive_distance
                            total_duration += charge_leg.duration_minutes
                            
                            logger.info(
                                f"Charge stop {charge_stops} planned",
                                station=station_info.name,
                                soc_before=round(hotspot.soc_at_point, 1),
                                soc_after=round(current_soc, 1),
                                charge_time=round(charge_leg.duration_minutes, 1)
                            )
                    
                    # Son DriveLeg - Son istasyondan hedefe
                    remaining_distance = route_distance_km - leg_start_km
                    remaining_duration = (remaining_distance / route_distance_km) * route_duration_min
                    final_consumption = remaining_distance * consumption_per_km
                    final_soc = current_soc - (final_consumption / battery_kwh) * 100
                    
                    final_drive_leg = DriveLeg(
                        type="drive",
                        start_point=current_point,
                        end_point=GeoPoint(lat=end_coords["lat"], lon=end_coords["lng"]),
                        distance_km=round(remaining_distance, 1),
                        duration_minutes=round(remaining_duration, 1),
                        avg_speed_kmh=round((remaining_distance / remaining_duration) * 60 if remaining_duration > 0 else 60, 1),
                        consumption_kwh=round(final_consumption, 2),
                        start_soc_percent=round(current_soc, 1),
                        end_soc_percent=round(max(0, final_soc), 1),
                        elevation_gain_m=round(elevation_gain_m * (remaining_distance / route_distance_km), 1),
                        elevation_loss_m=round(elevation_loss_m * (remaining_distance / route_distance_km), 1),
                        polyline=polyline,
                        weather_context={
                            "start_weather": {
                                "temp_c": start_weather.temp_c if start_weather else None,
                                "condition": start_weather.condition.value if start_weather else None,
                                "wind_speed_mps": start_weather.wind_speed_mps if start_weather else None,
                                "precipitation_prob": start_weather.precipitation_prob if start_weather else None
                            },
                            "end_weather": {
                                "temp_c": end_weather.temp_c if end_weather else None,
                                "condition": end_weather.condition.value if end_weather else None,
                                "wind_speed_mps": end_weather.wind_speed_mps if end_weather else None,
                                "precipitation_prob": end_weather.precipitation_prob if end_weather else None
                            }
                        }
                    )
                    legs.append(final_drive_leg)
                    
                    # Route message güncelle
                    total_charge_time = sum(leg.duration_minutes for leg in legs if isinstance(leg, ChargeLeg))
                    route_message = f"🔋 {charge_stops} şarj durağı planlandı. Toplam şarj süresi: {round(total_charge_time)}dk"
                    
                else:
                    # Hotspot bulunamadı - tek leg olarak devam
                    route_message = f"⚠️ Şarj gerekli ancak istasyon bulunamadı. Menzil: {round(max_range_km)}km"
                    
            except Exception as e:
                logger.exception("V1.5 charging algorithm failed", error=str(e))
                route_message = f"⚠️ Şarj planlaması başarısız: {str(e)}"
        
        else:
            route_message = f"✅ Şarj gerekmez. Menzil: {round(max_range_km)}km, Rota: {round(route_distance_km)}km"
        
        # Eğer legs boşsa (şarj gerekmiyorsa veya hata olduysa) tek DriveLeg ekle
        if not legs:
            avg_speed_kmh = (route_distance_km / route_duration_min) * 60 if route_duration_min > 0 else 0
            
            drive_leg = DriveLeg(
                type="drive",
                start_point=GeoPoint(lat=start_coords["lat"], lon=start_coords["lng"]),
                end_point=GeoPoint(lat=end_coords["lat"], lon=end_coords["lng"]),
                distance_km=round(route_distance_km, 1),
                duration_minutes=round(route_duration_min, 1),
                avg_speed_kmh=round(avg_speed_kmh, 1),
                consumption_kwh=round(segment_consumption_kwh, 2),
                start_soc_percent=round(start_soc_percent, 1),
                end_soc_percent=round(arrival_soc_percent, 1),
                elevation_gain_m=round(elevation_gain_m, 1),
                elevation_loss_m=round(elevation_loss_m, 1),
                polyline=polyline,
                weather_context={
                    "start_weather": {
                        "temp_c": start_weather.temp_c if start_weather else None,
                        "condition": start_weather.condition.value if start_weather else None,
                        "wind_speed_mps": start_weather.wind_speed_mps if start_weather else None,
                        "precipitation_prob": start_weather.precipitation_prob if start_weather else None
                    },
                    "end_weather": {
                        "temp_c": end_weather.temp_c if end_weather else None,
                        "condition": end_weather.condition.value if end_weather else None,
                        "wind_speed_mps": end_weather.wind_speed_mps if end_weather else None,
                        "precipitation_prob": end_weather.precipitation_prob if end_weather else None
                    }
                }
            )
            legs.append(drive_leg)
        
        # =====================================================================
        # STEP H: CO2 tasarrufu hesapla
        # =====================================================================
        try:
            co2_savings = calculate_co2_savings(route_distance_km)
        except Exception as e:
            logger.warning("CO2 calculation failed", error=str(e))
            co2_savings = 0.0
        
        # =====================================================================
        # STEP I: Training data logla
        # =====================================================================
        try:
            training_data = {
                "start_lat": request.start_location.lat,
                "start_lon": request.start_location.lon,
                "end_lat": request.end_location.lat,
                "end_lon": request.end_location.lon,
                "vehicle_model": vehicle.model_name,
                "battery_kwh": battery_kwh,
                "initial_soc_percent": request.current_soc_percent,
                "distance_km": route_distance_km,
                "duration_min": route_duration_min,
                "elevation_gain_m": elevation_gain_m,
                "elevation_loss_m": elevation_loss_m,
                "consumption_kwh": segment_consumption_kwh,
                "arrival_soc_percent": arrival_soc_percent,
                "selection_reason": selection_reason,
                "co2_savings_kg": co2_savings
            }
            log_training_data(training_data, category="route")
        except Exception as e:
            logger.warning("Training data logging failed", error=str(e))
        
        # =====================================================================
        # STEP J: Response döndür
        # =====================================================================
        logger.info(
            "Route planning completed successfully",
            total_distance_km=round(route_distance_km, 1),
            total_duration_min=round(route_duration_min, 1),
            consumption_kwh=round(segment_consumption_kwh, 2),
            co2_savings_kg=round(co2_savings, 2)
        )
        
        # V1.5: Toplam tüketimi hesapla
        total_consumption_kwh = sum(
            leg.consumption_kwh for leg in legs 
            if hasattr(leg, 'consumption_kwh') and leg.consumption_kwh
        )
        
        return MultiStopRouteResponse(
            status="success",
            total_distance_km=round(total_distance, 1),
            total_duration_minutes=round(total_duration, 1),
            total_co2_savings_kg=round(co2_savings, 2),
            consumption_kwh=round(total_consumption_kwh, 1),
            legs=legs,
            charge_stops=charge_stops,
            message=route_message
        )
        
    except ExternalAPIError as e:
        logger.error(
            "External API error",
            source=e.source,
            status_code=e.status_code,
            detail=str(e)
        )
        return _create_error_response(
            f"error_api_{e.source.lower()}",
            f"API hatası ({e.source}): {str(e)}"
        )
        
    except Exception as e:
        logger.exception(
            "Route planning failed with unexpected error",
            error=str(e),
            error_type=type(e).__name__
        )
        return _create_error_response(
            "error_unknown",
            f"Beklenmeyen hata: {str(e)}"
        )


# =============================================================================
# ALIAS FOR MAIN.PY COMPATIBILITY
# =============================================================================

async def plan_full_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    plan_multi_stop_route için alias.
    main.py'de bu isimle import ediliyor.
    """
    return await plan_multi_stop_route(request)
