from app.services.google_service import maps_service
from app.consumption_engine.main_calculator import calculate_segment_consumption_kwh
from app.consumption_engine.vehicle_models import get_vehicle_model
from app.utils.config_manager import config
from app.utils.logger import get_logger

logger = get_logger("route_selector")


async def find_best_route(
    origin: str,
    destination: str,
    vehicle_model_id: str,
    extra_load_kg: int,
    temperature_celsius: float = 20.0
) -> dict:
    """
    Google'dan 3 rota alternatifi alır ve en uygun olanı seçer.
    Seçim kriteri: Dinamik eşik mantığına göre tüketim farkı
    """
    try:
        # 1. 3 rota alternatifini al
        directions_response = await maps_service.get_directions(
            origin=origin,
            destination=destination
        )
        
        # Google API status kontrolü
        google_status = directions_response.get("status")
        if google_status != "OK":
            raise ValueError(f"Google Directions API hatası: {google_status} - {directions_response.get('error_message', '')}")
        
        if not directions_response.get("routes"):
            raise ValueError("Google Directions API'den rota bulunamadı")
        
        routes = directions_response["routes"]
        logger.info(f"Google'dan {len(routes)} rota alternatifi alındı")
        
        # 2. Araç bilgisini al
        vehicle = get_vehicle_model(vehicle_model_id)
        if not vehicle:
            raise ValueError(f"Araç modeli bulunamadı: {vehicle_model_id}")
        
        # 3. Her rotayı analiz et
        route_analyses = []
        
        for i, route in enumerate(routes):
            try:
                # Rota özetini al
                leg = route["legs"][0]  # İlk ve tek bacak
                total_distance_km = leg["distance"]["value"] / 1000  # metre -> km
                total_duration_min = leg["duration"]["value"] / 60  # saniye -> dakika
                
                # Rota polyline'ını al
                polyline = route["overview_polyline"]["points"]
                
                # Rakım verilerini al (isteğe bağlı, V1 için basitleştirme)
                elevation_response = await maps_service.get_elevation_for_path(polyline)
                
                # Basit rakım hesabı (V1 için ortalama)
                elevation_gain_m = 0
                if elevation_response.get("results"):
                    elevations = [point["elevation"] for point in elevation_response["results"]]
                    if len(elevations) > 1:
                        elevation_gain_m = max(elevations) - min(elevations)
                
                # Tüketimi hesapla
                consumption_kwh = calculate_segment_consumption_kwh(
                    vehicle=vehicle,
                    segment_distance_km=total_distance_km,
                    segment_elevation_gain_m=elevation_gain_m,
                    temperature_celsius=temperature_celsius,
                    extra_load_kg=extra_load_kg,
                    engine_version="v1"
                )
                
                route_analysis = {
                    "route_index": i,
                    "route": route,
                    "total_distance_km": total_distance_km,
                    "total_duration_min": total_duration_min,
                    "consumption_kwh": consumption_kwh,
                    "elevation_gain_m": elevation_gain_m
                }
                
                route_analyses.append(route_analysis)
                logger.info(
                    f"Rota {i+1} analiz edildi",
                    distance_km=round(total_distance_km, 1),
                    duration_min=round(total_duration_min),
                    consumption_kwh=round(consumption_kwh, 2)
                )
                
            except Exception as e:
                logger.error(f"Rota {i+1} analizi başarısız", error=str(e))
                continue
        
        if not route_analyses:
            raise ValueError("Hiçbir rota analiz edilemedi")
        
        # 4. Popüler ve En Verimli rotayı bul
        popular_route = route_analyses[0]  # Google'ın ilk önerisi
        most_efficient_route = min(route_analyses, key=lambda x: x["consumption_kwh"])
        
        logger.info(
            "Rota karşılaştırması",
            popular_consumption_kwh=round(popular_route["consumption_kwh"], 2),
            efficient_consumption_kwh=round(most_efficient_route["consumption_kwh"], 2)
        )
        
        # 5. Dinamik Eşik Mantığı
        threshold_percent = config.get_route_selector_threshold_percent()
        battery_capacity_kwh = vehicle.battery_capacity_kwh
        threshold_kwh = battery_capacity_kwh * threshold_percent
        
        consumption_difference = popular_route["consumption_kwh"] - most_efficient_route["consumption_kwh"]
        
        logger.info(
            "Eşik analizi",
            consumption_difference_kwh=round(consumption_difference, 2),
            threshold_kwh=round(threshold_kwh, 2),
            threshold_percent=threshold_percent
        )
        
        # 6. Karar ver
        if consumption_difference < threshold_kwh:
            # Fark çok küçük, popüler rotayı kullan
            selected_route = popular_route["route"]
            selection_reason = "popular_route"
            logger.info("Popüler rota seçildi - fark eşikten küçük")
        else:
            # Fark önemli, en verimli rotayı kullan
            selected_route = most_efficient_route["route"]
            selection_reason = "most_efficient"
            logger.info("En verimli rota seçildi - fark eşikten büyük")
        
        return {
            "selected_route": selected_route,
            "selection_reason": selection_reason,
            "route_analyses": route_analyses
        }
        
    except Exception as e:
        logger.error("Route seçimi başarısız", error=str(e))
        raise
