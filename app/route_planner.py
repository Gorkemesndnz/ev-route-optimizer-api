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
    StationAmenity,  # 🔧 V2.8
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
from app.utils.data_logger import log_route_decision, log_consumption

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


def _extract_weather_from_forecast(
    forecast_data: Optional[Dict[str, Any]],
    eta_minutes: float = 0.0
) -> Optional[WeatherInfo]:
    """
    🔧 V2.7: Forecast verisinden ETA'ya en yakın hava durumunu çıkar.
    
    OpenWeatherMap forecast 3 saatlik dilimler verir.
    ETA'ya en yakın dilimi seçerek gerçek varış anı havasını döndürür.
    
    Args:
        forecast_data: OpenWeatherMap forecast API yanıtı
        eta_minutes: Tahmini varış süresi (dakika)
    
    Returns:
        WeatherInfo veya None
    """
    if not forecast_data or not isinstance(forecast_data, dict):
        return None
    
    forecast_list = forecast_data.get("list", [])
    if not forecast_list:
        return None
    
    import time
    
    # ETA timestamp hesapla
    current_time = time.time()
    eta_timestamp = current_time + (eta_minutes * 60)
    
    # En yakın forecast dilimini bul
    closest_forecast = None
    min_diff = float('inf')
    
    for item in forecast_list:
        dt = item.get("dt", 0)
        diff = abs(dt - eta_timestamp)
        if diff < min_diff:
            min_diff = diff
            closest_forecast = item
    
    if not closest_forecast:
        return None
    
    # WeatherInfo oluştur
    try:
        main = closest_forecast.get("main", {})
        wind = closest_forecast.get("wind", {})
        weather_list = closest_forecast.get("weather", [])
        
        temp_c = float(main.get("temp", 20.0))
        wind_mps = float(wind.get("speed", 0.0))
        wind_deg = int(wind.get("deg", 0))
        pop = float(closest_forecast.get("pop", 0.0))  # Yağış olasılığı
        
        # Condition mapping
        from app.models import WeatherCondition
        condition = WeatherCondition.CLOUDY
        if weather_list:
            condition_id = weather_list[0].get("id", 800)
            # Basit mapping
            if condition_id == 800:
                condition = WeatherCondition.CLEAR
            elif 801 <= condition_id <= 804:
                condition = WeatherCondition.CLOUDY
            elif 500 <= condition_id < 600:
                condition = WeatherCondition.RAIN
            elif 600 <= condition_id < 700:
                condition = WeatherCondition.SNOW
            elif 700 <= condition_id < 800:
                condition = WeatherCondition.FOG
        
        return WeatherInfo(
            temp_c=temp_c,
            condition=condition,
            wind_speed_mps=wind_mps,
            wind_direction_deg=wind_deg,
            precipitation_prob=pop
        )
    except Exception as e:
        logger.warning(f"Failed to parse forecast: {e}")
        return None


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
    segments_with_consumption: List,  # SegmentWithConsumption listesi
    start_soc: float,
    final_soc: float,
    hotspots: List[ChargeHotspot],
    station_results: List,
    polyline: str,
    battery_capacity_kwh: float = 51.0,
    temperature_c: Optional[float] = None,
    weather_info: Optional[WeatherInfo] = None
) -> List:
    """
    Multi-leg yapısı oluştur: DriveLeg + ChargeLeg + DriveLeg + ...
    
    🔧 V2.0: SEGMENT BAZLI TÜKETİM
    Her leg için gerçek segment tüketimlerini toplar.
    Artık SOC farkı veya orantılı dağıtım YOK.
    
    Args:
        segments_with_consumption: MainCalculator'dan gelen tüketimli segmentler
    """
    legs = []
    total_consumption_kwh = sum(s.consumption_kwh for s in segments_with_consumption)
    
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
    
    # Hotspot'ları segment index'lerine göre grupla
    # Her hotspot hangi segment'e kadar olan tüketimi içerir
    hotspot_segment_indices = [h.segment_index for h in hotspots]
    
    # Multi-leg: Şarj durakları var
    current_point = start_point
    current_soc = start_soc
    remaining_distance = total_distance_km  # Kalan mesafe takibi
    remaining_duration = total_duration_min  # Kalan süre takibi
    last_segment_index = -1  # Son işlenen segment
    
    # Her hotspot + istasyon için leg oluştur
    for i, (hotspot, station_result) in enumerate(zip(hotspots, station_results)):
        if not station_result.best_station:
            continue
        
        station = station_result.best_station
        station_location = station.location
        
        # 🔧 V2.1: SOC ve TÜKETİM HESABI
        # SOCSimulator zaten doğru hesaplamış - onun değerlerini kullan
        # hotspot.soc_at_point = o noktadaki SOC (şarj öncesi)
        
        # Mesafe hesabı (hotspot konumuna kadar)
        leg_distance = hotspot.distance_from_start_km - (total_distance_km - remaining_distance)
        leg_distance = max(0, leg_distance)  # Negatif olmasın
        
        # Süre hesabı (mesafe oranına göre)
        leg_duration = (leg_distance / total_distance_km) * total_duration_min if total_distance_km > 0 else 0
        
        # 🔧 FIX: Tüketim = SOC farkı × batarya kapasitesi
        # Bu değer fiziksel olarak doğru ve batarya kapasitesini aşamaz
        end_soc = hotspot.soc_at_point  # SOCSimulator'dan gelen değer
        soc_drop = current_soc - end_soc
        leg_consumption = (soc_drop / 100) * battery_capacity_kwh
        
        # 🔧 DEBUG: Leg tüketim kontrolü
        logger.debug(
            f"[LEG {i+1}] dist={leg_distance:.1f}km, soc={current_soc:.1f}%→{end_soc:.1f}%, "
            f"drop={soc_drop:.1f}%, cons={leg_consumption:.2f}kWh"
        )
        
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
            end_soc_percent=round(max(0, end_soc), 1)
        ))
        
        # 2. ChargeLeg: Şarj süresi hesapla
        hotspot_target_soc = hotspot.recommended_charge_to
        charge_power_kw = station.power_kw if station.power_kw > 0 else 50.0
        
        charge_result = calculate_charge_time(
            start_soc=max(0, end_soc),
            target_soc=hotspot_target_soc,
            battery_capacity_kwh=battery_capacity_kwh,
            peak_power_kw=charge_power_kw,
            temperature_c=temperature_c
        )
        charge_duration = charge_result.duration_minutes
        kwh_to_add = charge_result.energy_added_kwh
        
        # V2.0: Google Places verilerini dahil et
        station_source = station.station_info.get("_source", "ocm")
        station_place_id = station.station_info.get("_place_id")
        station_rating = station.station_info.get("_rating", station.rating)
        station_user_ratings = station.station_info.get("_user_ratings_total")
        station_vicinity = station.station_info.get("AddressInfo", {}).get("AddressLine1", "")
        
        # 🔧 V2.8: Amenities bilgilerini CorridorStation'dan al
        station_amenities = StationAmenity(
            has_toilet=station.has_toilet,
            has_food=station.has_food,
            has_shopping=station.has_shopping,
            has_parking=station.has_parking,
            is_24_7=station.is_open_now is True  # None değilse ve True ise
        )
        
        station_info = StationInfo(
            id=station.station_id,
            name=station.station_name,
            location=station_location,
            rating=station_rating,
            user_ratings_total=station_user_ratings,
            connectors=[
                ConnectorInfo(
                    plug_type=PlugType.CCS2,
                    charger_type=ChargerType.DC,
                    power_kw=station.power_kw if station.power_kw > 0 else 50.0
                )
            ],
            amenities=station_amenities,  # 🔧 V2.8
            data_source=station_source,
            place_id=station_place_id,
            vicinity=station_vicinity,
            distance_from_route_km=station.deviation_km,
            is_open_now=station.is_open_now  # 🔧 V2.8
        )
        
        # 🔧 V2.7: Forecast'ten ETA bazlı hava durumu çıkar
        # Toplam geçen süre = başlangıçtan bu durağa kadar
        elapsed_duration = total_duration_min - remaining_duration + leg_duration
        station_weather = _extract_weather_from_forecast(
            station_result.weather_forecast,
            eta_minutes=elapsed_duration
        )
        # Forecast yoksa fallback olarak genel weather_info kullan
        charge_weather = station_weather if station_weather else weather_info
        
        legs.append(ChargeLeg(
            type="charge",
            station=station_info,
            arrival_soc_percent=round(max(0, end_soc), 1),
            target_soc_percent=round(hotspot_target_soc, 1),
            energy_added_kwh=round(kwh_to_add, 2),
            duration_minutes=round(max(10, charge_duration), 1),
            weather_context=charge_weather  # 🔧 V2.7: ETA bazlı forecast hava durumu
        ))
        
        # Güncellemeler
        current_point = station_location
        current_soc = hotspot_target_soc
        remaining_distance -= leg_distance
        remaining_duration -= leg_duration
        last_segment_index = hotspot.segment_index
    
    # Son DriveLeg: Son şarj istasyonu → Varış
    if remaining_distance > 0:
        # 🔧 V2.1: SOC farkından tüketim hesapla
        final_soc_drop = current_soc - final_soc
        final_leg_consumption = (final_soc_drop / 100) * battery_capacity_kwh
        final_leg_distance = remaining_distance
        final_leg_duration = remaining_duration
        
        avg_speed = (final_leg_distance / final_leg_duration) * 60 if final_leg_duration > 0 else 60
        
        # 🔧 DEBUG
        logger.debug(
            f"[FINAL LEG] dist={final_leg_distance:.1f}km, soc={current_soc:.1f}%→{final_soc:.1f}%, "
            f"cons={final_leg_consumption:.2f}kWh"
        )
        
        legs.append(DriveLeg(
            type="drive",
            start_point=current_point,
            end_point=end_point,
            distance_km=round(final_leg_distance, 1),
            duration_minutes=round(max(0, final_leg_duration), 1),
            avg_speed_kmh=round(avg_speed, 1),
            consumption_kwh=round(max(0, final_leg_consumption), 2),
            start_soc_percent=round(current_soc, 1),
            end_soc_percent=round(max(0, final_soc), 1)
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
        # 🔧 V2.7: Başlangıç için current, varış için forecast (ETA bazlı)
        avg_weather = None
        start_weather = None
        end_weather = None
        try:
            # Başlangıç: Current weather (şimdi çıkıyorsun)
            start_weather = await weather_service.get_weather_at_point(
                request.start_location.lat, request.start_location.lon
            )
            
            # Varış: Forecast (ETA sonra varıyorsun)
            end_forecast = await weather_service.get_forecast_for_point(
                request.end_location.lat, request.end_location.lon
            )
            end_weather = _extract_weather_from_forecast(end_forecast, eta_minutes=route_duration_min)
            
            # Forecast başarısız olursa current'a fallback
            if not end_weather:
                end_weather = await weather_service.get_weather_at_point(
                    request.end_location.lat, request.end_location.lon
                )
                logger.debug("End weather: fallback to current (forecast failed)")
            else:
                logger.debug(f"End weather: forecast for ETA={route_duration_min:.0f}min")
            
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
                logger.info(f"Weather: start={start_weather.temp_c:.1f}C, end(forecast)={end_weather.temp_c:.1f}C, avg={avg_temp:.1f}C")
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
            # Kullanıcı override → sabit target_soc, dinamik hesaplama ATLA
            charge_target_soc = user_target_soc_override
            simulator = SOCSimulator(
                battery_capacity_kwh=battery_kwh,
                start_soc=request.current_soc_percent,
                target_arrival_soc=arrival_soc,
                charge_min_soc=charge_min_soc,
                charge_target_soc=charge_target_soc,
                user_override_target=True  # 🔧 V2.6: Dinamik hedef hesaplamasını atla
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
            f"Pass 1 complete: {len(hotspots)} hotspots, "
            f"{charge_stops} stations, final_soc={sim_result.final_soc}%"
        )
        
        # =================================================================
        # 🔧 STEP 10.5: 2-PASS PLANLAMA - Durak hava durumları ile refine
        # =================================================================
        # Pass 1: Start+End ortalaması ile ilk plan (yukarıda tamamlandı)
        # Pass 2: Hotspot lokasyonlarının hava durumu → daha doğru tüketim
        # =================================================================
        
        if hotspots and station_results:
            try:
                # Hotspot/istasyon lokasyonlarından hava durumu al
                weather_points = []
                weather_weights = []  # Mesafe bazlı ağırlıklar
                
                # Start noktası
                if start_weather:
                    weather_points.append(start_weather)
                    weather_weights.append(hotspots[0].distance_from_start_km if hotspots else route_distance_km / 2)
                
                # Her hotspot için hava durumu
                prev_km = 0.0
                for i, (hotspot, station_result) in enumerate(zip(hotspots, station_results)):
                    if station_result.best_station:
                        station = station_result.best_station
                        hotspot_weather = await weather_service.get_weather_at_point(
                            station.lat, station.lon
                        )
                        if hotspot_weather:
                            weather_points.append(hotspot_weather)
                            # Bu bacağın mesafesi (ağırlık)
                            leg_distance = hotspot.distance_from_start_km - prev_km
                            weather_weights.append(leg_distance)
                            prev_km = hotspot.distance_from_start_km
                            logger.debug(f"Hotspot {i+1} weather: {hotspot_weather.temp_c}°C at {station.name}")
                
                # End noktası
                if end_weather:
                    weather_points.append(end_weather)
                    remaining_distance = route_distance_km - prev_km
                    weather_weights.append(remaining_distance)
                
                # Ağırlıklı ortalama hesapla (en az 2 nokta varsa)
                if len(weather_points) >= 2 and sum(weather_weights) > 0:
                    total_weight = sum(weather_weights)
                    refined_temp = sum(w.temp_c * wt for w, wt in zip(weather_points, weather_weights)) / total_weight
                    refined_wind = sum(w.wind_speed_mps * wt for w, wt in zip(weather_points, weather_weights)) / total_weight
                    
                    # Yeni ağırlıklı ortalama hava durumu
                    refined_weather = WeatherInfo(
                        temp_c=refined_temp,
                        condition=weather_points[0].condition,  # İlk noktanın durumu
                        wind_speed_mps=refined_wind,
                        wind_direction_deg=0,
                        precipitation_prob=0.0
                    )
                    
                    old_temp = avg_weather.temp_c if avg_weather else DEFAULT_TEMPERATURE_C
                    temp_diff = abs(refined_temp - old_temp)
                    
                    logger.info(
                        f"Pass 2 weather: {len(weather_points)} points, "
                        f"refined_temp={refined_temp:.1f}°C (was {old_temp:.1f}°C, diff={temp_diff:.1f}°C)"
                    )
                    
                    # 🔧 V2.7: Her zaman refined weather ile tüketimi yeniden hesapla (2°C eşiği kaldırıldı)
                    logger.info("Pass 2: Re-calculating consumption with refined weather...")
                    
                    # Tüketimi yeniden hesapla
                    segments_with_consumption = calculate_route_consumption(
                        vehicle=vehicle,
                        segments=segments,
                        temperature_celsius=refined_temp,
                        wind_speed_mps=refined_wind,
                        weather_condition=refined_weather.condition.value,
                        extra_load_kg=extra_load_kg,
                        passenger_count=passenger_count,
                        child_count=child_count
                    )
                    
                    new_total = sum(s.consumption_kwh for s in segments_with_consumption)
                    old_total = total_consumption
                    total_consumption = new_total
                    
                    logger.info(f"Pass 2 consumption: {old_total:.2f} → {new_total:.2f} kWh (diff={new_total-old_total:.2f})")
                    
                    # SOC simülasyonunu yeniden çalıştır (durak sayısı değişebilir)
                    if user_target_soc_override is not None:
                        simulator = SOCSimulator(
                            battery_capacity_kwh=battery_kwh,
                            start_soc=request.current_soc_percent,
                            target_arrival_soc=arrival_soc,
                            charge_min_soc=charge_min_soc,
                            charge_target_soc=charge_target_soc,
                            user_override_target=True
                        )
                        sim_result = simulator.simulate(segments_with_consumption, route_distance_km)
                    else:
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
                    
                    # Hotspot sayısı değiştiyse istasyonları yeniden bul
                    if len(sim_result.hotspots) != len(hotspots):
                        logger.info(f"Pass 2: Hotspot count changed {len(hotspots)} → {len(sim_result.hotspots)}, re-finding stations...")
                        hotspots = sim_result.hotspots
                        if hotspots:
                            station_results = await find_stations_for_hotspots(hotspots, request.vehicle_model_id)
                        else:
                            station_results = []
                        charge_stops = sum(1 for r in station_results if r.best_station) if station_results else 0
                    
                    # Refined weather'ı kullan
                    avg_weather = refined_weather
                    
                    logger.info(f"Pass 2 complete: {len(hotspots)} hotspots, final_soc={sim_result.final_soc}%")
                        
            except Exception as e:
                logger.warning(f"Pass 2 weather refinement failed: {e}")
        
        # STEP 11: Multi-Leg Builder (SEGMENT BAZLI TÜKETİM)
        legs = _build_multi_legs(
            start_point=GeoPoint(lat=start_coords["lat"], lon=start_coords["lng"]),
            end_point=GeoPoint(lat=end_coords["lat"], lon=end_coords["lng"]),
            total_distance_km=route_distance_km,
            total_duration_min=route_duration_min,
            segments_with_consumption=segments_with_consumption,  # 🔧 Gerçek segment tüketimleri
            start_soc=request.current_soc_percent,
            final_soc=sim_result.final_soc,
            hotspots=hotspots,
            station_results=station_results,
            polyline=polyline,
            battery_capacity_kwh=battery_kwh,
            temperature_c=avg_weather.temp_c if avg_weather else None,
            weather_info=avg_weather  # 🔧 V2.6: Şarj istasyonları için hava durumu
        )
        
        end_soc = sim_result.final_soc
        
        # STEP 12: 🆕 Gerçekçi CO2 tasarrufu (EV elektrik tüketimi dahil)
        try:
            # 🎯 Yeni formül: CO2 Tasarrufu = (ICE CO2) - (EV Elektrik CO2)
            ev_consumption_kwh = sim_result.total_consumption_kwh
            
            # 🔧 Validation: EV tüketimi verisi kontrolü
            if ev_consumption_kwh <= 0:
                logger.warning("EV consumption data missing or zero, using legacy calculation")
                co2_savings = calculate_co2_savings(route_distance_km, 0.0, "TR")
            else:
                co2_savings = calculate_co2_savings(route_distance_km, ev_consumption_kwh, "TR")
            
            logger.info(
                "Realistic CO2 savings calculated",
                distance_km=route_distance_km,
                ev_consumption_kwh=ev_consumption_kwh,
                net_co2_savings_kg=co2_savings
            )
        except Exception as e:
            logger.warning(f"CO2 calculation failed, using 0: {e}")
            co2_savings = 0.0
        
        # Mesaj oluştur
        if charge_stops > 0:
            message = f"{charge_stops} şarj durağı gerekli (hedef: %{round(charge_target_soc)})"
        elif sim_result.can_complete_without_charging:
            message = f"Şarj gerekmez. Varış SOC: %{round(end_soc)}"
        else:
            message = f"Dikkat! Varış SOC: %{round(end_soc)}"
        
        logger.info(f"Route planning completed: {message}")
        
        # STEP 13: Training data logging (route decision + consumption)
        log_route_decision({
            "start_lat": request.start_location.lat,
            "start_lon": request.start_location.lon,
            "end_lat": request.end_location.lat,
            "end_lon": request.end_location.lon,
            "vehicle_model": request.vehicle_model_id,
            "battery_kwh": battery_kwh,
            "initial_soc_percent": request.current_soc_percent,
            "distance_km": round(route_distance_km, 3),
            "duration_min": round(route_duration_min, 1),
            "elevation_gain_m": round(elevation_gain_m, 1),
            "elevation_loss_m": round(elevation_loss_m, 1),
            "consumption_kwh": round(total_consumption, 3),
            "arrival_soc_percent": round(end_soc, 1),
            "selection_reason": route_result.get("selection_reason"),
            "charge_stops": charge_stops,
            "co2_savings_kg": round(co2_savings, 2)
        })
        
        log_consumption({
            "vehicle_model": request.vehicle_model_id,
            "battery_kwh": battery_kwh,
            "total_segments": len(segments_with_consumption),
            "total_consumption_kwh": round(total_consumption, 3),
            "simulated_consumption_kwh": round(sim_result.total_consumption_kwh, 3),
            "start_soc_percent": request.current_soc_percent,
            "final_soc_percent": round(end_soc, 1),
            "charge_min_soc_percent": charge_min_soc,
            "target_soc_percent": charge_target_soc,
            "arrival_soc_target_percent": arrival_soc,
            "passenger_count": passenger_count,
            "child_count": child_count,
            "extra_load_kg": extra_load_kg,
            "avg_temp_c": round(avg_weather.temp_c, 1) if avg_weather else DEFAULT_TEMPERATURE_C,
            "avg_wind_speed_mps": round(avg_weather.wind_speed_mps, 1) if avg_weather else 0.0
        })
        
        return MultiStopRouteResponse(
            status="success",
            total_distance_km=round(route_distance_km, 1),
            total_duration_minutes=round(route_duration_min, 1),
            total_co2_savings_kg=round(co2_savings, 2),
            consumption_kwh=round(total_consumption, 1),
            legs=legs,
            charge_stops=charge_stops,
            message=message,
            # 🔧 V2.7: Başlangıç ve varış hava durumu
            start_weather=start_weather,
            end_weather=end_weather
        )
        
    except Exception as e:
        logger.exception(f"Route planning failed: {e}")
        return _create_error_response("error_unknown", str(e))
