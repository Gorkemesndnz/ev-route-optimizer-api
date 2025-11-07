import asyncio
from app.models import (
    RouteRequest, MultiStopRouteResponse, DriveLeg, ChargeLeg, WeatherPrediction
)
from app.route_selector import find_best_route
from app.station_finder import find_best_station
from app.consumption_engine.main_calculator import calculate_segment_consumption_kwh
from app.consumption_engine.vehicle_models import get_vehicle_model, VehicleModel
from app.sustainability_calculator import calculate_co2_savings
from app.utils.logger import get_logger
from app.utils.data_logger import log_training_data
from app.services.google_service import maps_service
from app.services.base_service import ExternalAPIError

# Constants
logger = get_logger("route_planner")
SIMULATION_SEGMENT_KM = 1.0
DEFAULT_TARGET_SOC_PERCENT = 80
SOC_SAFETY_BUFFER_PERCENT = 15.0


async def plan_full_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    V1.3 "Main Orchestrator" - Tam çok duraklı rota planlaması
    """
    try:
        # --- STEP A: Araç bilgilerini al ---
        vehicle = get_vehicle_model(request.vehicle_model_id)
        if not vehicle:
            logger.error(
                "Araç modeli bulunamadı",
                vehicle_model_id=request.vehicle_model_id
            )
            return MultiStopRouteResponse(
                status="error_vehicle_not_found",
                total_distance_km=0,
                total_duration_minutes=0,
                total_co2_savings_kg=0,
                legs=[]
            )
        
        logger.info(
            "Rota planlaması başlatıldı",
            start=request.start_location,
            end=request.end_location,
            vehicle=vehicle.model_name,
            initial_soc=request.initial_soc_percent
        )
        
        # --- STEP B: En iyi rotayı seç ---
        route_result = await find_best_route(
            origin=request.start_location,
            destination=request.end_location,
            vehicle_model_id=request.vehicle_model_id,
            extra_load_kg=request.extra_load_kg
        )
        
        selected_route = route_result["selected_route"]
        selection_reason = route_result["selection_reason"]
        
        logger.info(
            "Rota seçildi",
            selection_reason=selection_reason
        )
        
        # --- STEP C: Rakım verilerini al ---
        polyline = selected_route["overview_polyline"]["points"]
        elevation_response = await maps_service.get_elevation_for_path(polyline)
        
        elevation_points = []
        if elevation_response.get("results"):
            elevation_points = elevation_response["results"]
        
        logger.info(f"{len(elevation_points)} rakım noktası alındı")
        
        # --- STEP D: Başlangıç durumunu ayarla ---
        legs = []
        current_soc_kwh = vehicle.battery_capacity_kwh * (request.initial_soc_percent / 100)
        safety_buffer_kwh = vehicle.battery_capacity_kwh * (SOC_SAFETY_BUFFER_PERCENT / 100)
        target_soc_kwh = vehicle.battery_capacity_kwh * (DEFAULT_TARGET_SOC_PERCENT / 100)
        
        # Rota koordinatlarını al
        route_leg = selected_route["legs"][0]
        start_coords = route_leg["start_location"]
        end_coords = route_leg["end_location"]
        
        current_lat = start_coords["lat"]
        current_lon = start_coords["lng"]
        
        # --- STEP E & F: Segment simülasyonu ve şarj kontrolü ---
        total_distance_km = 0
        total_duration_min = 0
        
        # Basitleştirilmiş segment simülasyonu (V1 için)
        route_distance_km = route_leg["distance"]["value"] / 1000
        route_duration_min = route_leg["duration"]["value"] / 60
        
        # Rota boyunca ilerle ve şarj noktalarını belirle
        remaining_distance = route_distance_km
        segment_start_lat = current_lat
        segment_start_lon = current_lon
        last_charge_location = (current_lat, current_lon)
        
        # Segment bazında simülasyon (her 50km'de şarj kontrolü)
        segment_size_km = 50.0
        segments_processed = 0
        
        while remaining_distance > 0:
            current_segment_distance = min(segment_size_km, remaining_distance)
            
            # Basit rakım tahmini (V1 için)
            elevation_gain_m = 0
            if elevation_points:
                # Segment başı ve sonu için rakım tahmini
                start_idx = int((segments_processed * segment_size_km / route_distance_km) * len(elevation_points))
                end_idx = int(((segments_processed * segment_size_km + current_segment_distance) / route_distance_km) * len(elevation_points))
                
                if 0 <= start_idx < len(elevation_points) and 0 <= end_idx < len(elevation_points):
                    start_elev = elevation_points[start_idx]["elevation"]
                    end_elev = elevation_points[end_idx]["elevation"]
                    elevation_gain_m = end_elev - start_elev
            
            # Segment tüketimini hesapla
            segment_consumption_kwh = calculate_segment_consumption_kwh(
                vehicle=vehicle,
                segment_distance_km=current_segment_distance,
                segment_elevation_gain_m=elevation_gain_m,
                temperature_celsius=20.0,  # V1 için sabat
                extra_load_kg=request.extra_load_kg,
                engine_version="v1"
            )
            
            # SOC'u güncelle
            current_soc_kwh -= segment_consumption_kwh
            total_distance_km += current_segment_distance
            segments_processed += 1
            remaining_distance -= current_segment_distance
            
            # --- Şarj kontrolü ---
            if current_soc_kwh < safety_buffer_kwh and remaining_distance > 0:
                logger.info(
                    "Şarj gerekli",
                    current_soc_kwh=round(current_soc_kwh, 2),
                    safety_buffer_kwh=round(safety_buffer_kwh, 2)
                )
                
                # Mevcut DriveLeg'i ekle
                drive_leg = DriveLeg(
                    type="drive",
                    start_point=f"{segment_start_lat},{segment_start_lon}",
                    end_point=f"{current_lat},{current_lon}",
                    distance_km=total_distance_km - sum(leg.distance_km for leg in legs if leg.type == "drive"),
                    duration_minutes=int(route_duration_min * (total_distance_km / route_distance_km)),
                    start_soc_percent=int((current_soc_kwh + segment_consumption_kwh) / vehicle.battery_capacity_kwh * 100),
                    arrival_soc_percent=int(current_soc_kwh / vehicle.battery_capacity_kwh * 100),
                    route_polyline=polyline,  # Basitleştirme
                    consumption_kwh=segment_consumption_kwh
                )
                legs.append(drive_leg)
                
                # İstasyon ara
                station, weather_forecast = await find_best_station(
                    latitude=current_lat,
                    longitude=current_lon,
                    vehicle=vehicle
                )
                
                if not station:
                    logger.error("Şarj istasyonu bulunamadı")
                    return MultiStopRouteResponse(
                        status="error_no_station_found",
                        total_distance_km=total_distance_km,
                        total_duration_minutes=total_duration_min,
                        total_co2_savings_kg=0,
                        legs=legs
                    )
                
                # Şarj süresini hesapla
                charge_needed_kwh = target_soc_kwh - current_soc_kwh
                charge_power_kw = station.get("_max_power_kw", 50)  # Varsayılan 50kW
                
                if charge_power_kw > 40:  # DC
                    charge_rate_kw = vehicle.avg_dc_charge_rate_kw
                else:  # AC
                    charge_rate_kw = vehicle.avg_ac_charge_rate_kw
                
                charge_duration_min = int((charge_needed_kwh / min(charge_rate_kw, charge_power_kw)) * 60)
                
                # Hava durumu tahmini
                weather_prediction = WeatherPrediction(
                    temperature_celsius=20,  # V1 için sabit
                    condition_icon="clear-day",
                    description="Açık hava"
                )
                
                if weather_forecast and weather_forecast.get("list"):
                    first_weather = weather_forecast["list"][0]
                    weather_prediction = WeatherPrediction(
                        temperature_celsius=int(first_weather["main"]["temp"]),
                        condition_icon=first_weather["weather"][0]["icon"].replace("n", "d"),  # Gündüz ikonu
                        description=first_weather["weather"][0]["description"]
                    )
                
                # ChargeLeg oluştur
                charge_leg = ChargeLeg(
                    type="charge",
                    station_name=station.get("AddressInfo", {}).get("Title", "Bilinmeyen İstasyon"),
                    station_id=str(station.get("ID")),
                    charge_speed_type="fast" if charge_power_kw > 40 else "slow",
                    arrival_soc_percent=int(current_soc_kwh / vehicle.battery_capacity_kwh * 100),
                    target_soc_percent=DEFAULT_TARGET_SOC_PERCENT,
                    charge_added_kwh=charge_needed_kwh,
                    charge_duration_minutes=charge_duration_min,
                    predicted_weather_at_arrival=weather_prediction
                )
                legs.append(charge_leg)
                
                # SOC'u sıfırla
                current_soc_kwh = target_soc_kwh
                total_duration_min += charge_duration_min
                
                # İstasyon koordinatlarını güncelle
                station_address = station.get("AddressInfo", {})
                current_lat = station_address.get("Latitude", current_lat)
                current_lon = station_address.get("Longitude", current_lon)
                segment_start_lat = current_lat
                segment_start_lon = current_lon
                
                logger.info(
                    "Şarj eklendi",
                    station_name=charge_leg.station_name,
                    charge_added_kwh=round(charge_needed_kwh, 2),
                    charge_duration_min=charge_duration_min
                )
        
        # --- STEP G: Son DriveLeg'i ekle ---
        final_drive_leg = DriveLeg(
            type="drive",
            start_point=f"{segment_start_lat},{segment_start_lon}",
            end_point=f"{end_coords['lat']},{end_coords['lng']}",
            distance_km=route_distance_km - total_distance_km,
            duration_minutes=int(route_duration_min - total_duration_min),
            start_soc_percent=int(current_soc_kwh / vehicle.battery_capacity_kwh * 100),
            arrival_soc_percent=int(max(0, current_soc_kwh - segment_consumption_kwh) / vehicle.battery_capacity_kwh * 100),
            route_polyline=polyline,
            consumption_kwh=segment_consumption_kwh
        )
        legs.append(final_drive_leg)
        
        total_distance_km = route_distance_km
        total_duration_min = route_duration_min + sum(
            leg.charge_duration_minutes for leg in legs if leg.type == "charge"
        )
        
        # --- STEP H: CO2 tasarrufunu hesapla ---
        co2_savings = calculate_co2_savings(total_distance_km)
        
        # --- STEP I: Eğitim verisini logla ---
        training_data = {
            "request": request.dict(),
            "selected_route_summary": {
                "selection_reason": selection_reason,
                "total_distance_km": total_distance_km,
                "total_duration_min": total_duration_min,
                "charge_stops": len([leg for leg in legs if leg.type == "charge"])
            },
            "vehicle_info": {
                "model": vehicle.model_name,
                "battery_capacity_kwh": vehicle.battery_capacity_kwh,
                "base_consumption_wh_km": vehicle.base_consumption_wh_km
            }
        }
        
        log_training_data(training_data)
        
        # --- STEP J: Başarılı response döndür ---
        logger.info(
            "Rota planlaması tamamlandı",
            total_distance_km=round(total_distance_km, 1),
            total_duration_min=total_duration_min,
            total_charge_stops=len([leg for leg in legs if leg.type == "charge"]),
            co2_savings_kg=co2_savings
        )
        
        return MultiStopRouteResponse(
            status="multi_stop_plan_success",
            total_distance_km=round(total_distance_km, 1),
            total_duration_minutes=total_duration_min,
            total_co2_savings_kg=co2_savings,
            legs=legs
        )
        
    except ExternalAPIError as e:
        logger.error(
            "API hatası nedeniyle rota planlaması başarısız",
            source=e.source,
            status_code=e.status_code,
            detail=e.detail
        )
        return MultiStopRouteResponse(
            status="error_api_failed",
            total_distance_km=0,
            total_duration_minutes=0,
            total_co2_savings_kg=0,
            legs=[]
        )
        
    except Exception as e:
        logger.error(
            "Rota planlaması başarısız",
            error=str(e),
            error_type=type(e).__name__
        )
        return MultiStopRouteResponse(
            status="error_unknown",
            total_distance_km=0,
            total_duration_minutes=0,
            total_co2_savings_kg=0,
            legs=[]
        )
