import asyncio
import math
from app.services.ocm_service import ocm_service
from app.services.google_service import maps_service
from app.services.weather_service import weather_service
from app.consumption_engine.vehicle_models import VehicleModel
from app.utils.config_manager import config
from app.utils.logger import get_logger

logger = get_logger("station_finder")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    İki koordinat arasındaki mesafeyi Haversine formülü ile km olarak hesaplar.
    Earth radius: 6371 km
    """
    R = 6371.0  # Dünya yarıçapı (km)
    
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


async def find_best_station(
    latitude: float,
    longitude: float,
    vehicle: VehicleModel
) -> tuple:
    """
    V1.3 "Station Funnel" mantığı ile en uygun şarj istasyonunu bulur.
    Return: (best_station_dict, weather_forecast_dict) veya (None, None)
    """
    try:
        # --- STEP 1: Paralel veri çekimi ---
        logger.info("İstasyon arama başlatıldı", lat=latitude, lon=longitude)
        
        stations_task = ocm_service.get_stations_nearby(
            latitude=latitude,
            longitude=longitude,
            radius_km=30
        )
        
        weather_task = weather_service.get_forecast(
            latitude=latitude,
            longitude=longitude
        )
        
        stations_data, weather_forecast = await asyncio.gather(
            stations_task,
            weather_task,
            return_exceptions=True
        )
        
        # Hata kontrolü
        if isinstance(stations_data, Exception):
            logger.error("İstasyon verisi alınamadı", error=str(stations_data))
            stations_data = []
        
        if isinstance(weather_forecast, Exception):
            logger.error("Hava durumu alınamadı", error=str(weather_forecast))
            weather_forecast = None
        
        logger.info(f"{len(stations_data)} istasyon bulundu")
        
        # --- STEP 2: Operasyonel ve uyumlu istasyonları filtrele ---
        filtered_stations = []
        
        for station in stations_data:
            try:
                # Operasyonel kontrolü - daha toleranslı
                status_type = station.get("StatusType", {})
                status_title = status_type.get("Title", "")
                
                if not status_title or "operational" not in status_title.lower():
                    continue
                
                # Bağlantı tipi uyumluluğu kontrolü - partial string matching
                connections = station.get("Connections", [])
                has_compatible_connector = False
                
                for conn in connections:
                    connection_type = conn.get("ConnectionType", {})
                    connection_title = connection_type.get("Title", "")
                    
                    if connection_title and vehicle.connector_type.lower() in connection_title.lower():
                        has_compatible_connector = True
                        break
                
                if not has_compatible_connector:
                    continue
                
                filtered_stations.append(station)
                
            except Exception as e:
                logger.warning("İstasyon filtreleme hatası", station_id=station.get("ID"), error=str(e))
                continue
        
        logger.info(
            f"İstasyon filtreleme sonuçları",
            total_ocm_results=len(stations_data),
            filtered_count=len(filtered_stations)
        )
        
        if not filtered_stations:
            return None, weather_forecast
        
        # --- STEP 3: DC ve AC istasyonlarını ayır ---
        dc_stations = []
        ac_stations = []
        
        for station in filtered_stations:
            try:
                connections = station.get("Connections", [])
                max_power = 0
                
                for conn in connections:
                    power_kw = conn.get("PowerKW", 0)
                    if power_kw > max_power:
                        max_power = power_kw
                
                station["_max_power_kw"] = max_power
                
                if max_power > 40:  # DC hızlı şarj
                    dc_stations.append(station)
                else:  # AC normal şarj
                    ac_stations.append(station)
                    
            except Exception as e:
                logger.warning("İstasyon güç analizi hatası", station_id=station.get("ID"), error=str(e))
                continue
        
        # DC istasyonları tercih et, yoksa AC'ye geç
        candidate_stations = dc_stations if dc_stations else ac_stations
        logger.info(f"DC: {len(dc_stations)}, AC: {len(ac_stations)}, Adaylar: {len(candidate_stations)}")
        
        if not candidate_stations:
            return None, weather_forecast
        
        # --- STEP 4: Mesafe filtreleme ve Distance Matrix ---
        # Haversine ile 50km'den uzak olanları çıkar
        nearby_stations = []
        
        for station in candidate_stations:
            try:
                address_info = station.get("AddressInfo", {})
                station_lat = address_info.get("Latitude")
                station_lon = address_info.get("Longitude")
                
                if not station_lat or not station_lon:
                    continue
                
                distance_km = haversine_km(latitude, longitude, station_lat, station_lon)
                
                if distance_km <= 50:  # 50km içindeki istasyonlar
                    station["_haversine_distance_km"] = distance_km
                    nearby_stations.append(station)
                    
            except Exception as e:
                logger.warning("Mesafe hesaplama hatası", station_id=station.get("ID"), error=str(e))
                continue
        
        logger.info(
            f"Mesafe filtreleme sonuçları",
            candidates_before=len(candidate_stations),
            nearby_after_haversine=len(nearby_stations)
        )
        
        if not nearby_stations:
            return None, weather_forecast
        
        # Google API limiti için adayları 100 ile sınırla
        nearby_stations.sort(key=lambda x: x["_haversine_distance_km"])
        top_nearby_stations = nearby_stations[:100]
        
        # Google Distance Matrix çağrısı
        origin_str = f"{latitude},{longitude}"
        destinations = []
        
        for station in top_nearby_stations:
            address_info = station.get("AddressInfo", {})
            dest_str = f"{address_info.get('Latitude')},{address_info.get('Longitude')}"
            destinations.append(dest_str)
        
        try:
            distance_matrix = await maps_service.get_distance_matrix(
                origins=[origin_str],
                destinations=destinations
            )
            
            # 15 dakikadan fazla sapma olanları çıkar
            final_stations = []
            
            for i, station in enumerate(top_nearby_stations):
                try:
                    if (distance_matrix.get("rows") and 
                        distance_matrix["rows"][0].get("elements") and
                        i < len(distance_matrix["rows"][0]["elements"])):
                        
                        element = distance_matrix["rows"][0]["elements"][i]
                        if element.get("status") == "OK":
                            duration_sec = element.get("duration", {}).get("value", 0)
                            duration_min = duration_sec / 60
                            
                            if duration_min <= 15:  # 15 dakika içinde
                                station["_deviation_minutes"] = duration_min
                                final_stations.append(station)
                                
                except Exception as e:
                    logger.warning("Distance matrix analizi hatası", station_id=station.get("ID"), error=str(e))
                    continue
            
            logger.info(
                f"Distance Matrix sonrası {len(final_stations)} istasyon kaldı",
                deviation_threshold_minutes=15
            )
            
            # Eğer DC istasyonları varsa ama hepsi filtrelendiyse, AC'ye fallback yap
            if not final_stations and dc_stations:
                logger.info("DC istasyonları filtrelendi, AC istasyonlarına fallback yapılıyor")
                # AC istasyonları için aynı Distance Matrix işlemini yap
                ac_nearby_stations = []
                
                for station in ac_stations:
                    try:
                        address_info = station.get("AddressInfo", {})
                        station_lat = address_info.get("Latitude")
                        station_lon = address_info.get("Longitude")
                        
                        if not station_lat or not station_lon:
                            continue
                        
                        distance_km = haversine_km(latitude, longitude, station_lat, station_lon)
                        
                        if distance_km <= 50:
                            station["_haversine_distance_km"] = distance_km
                            ac_nearby_stations.append(station)
                            
                    except Exception as e:
                        continue
                
                if ac_nearby_stations:
                    ac_nearby_stations.sort(key=lambda x: x["_haversine_distance_km"])
                    ac_top_stations = ac_nearby_stations[:100]
                    
                    ac_destinations = []
                    for station in ac_top_stations:
                        address_info = station.get("AddressInfo", {})
                        dest_str = f"{address_info.get('Latitude')},{address_info.get('Longitude')}"
                        ac_destinations.append(dest_str)
                    
                    try:
                        ac_distance_matrix = await maps_service.get_distance_matrix(
                            origins=[origin_str],
                            destinations=ac_destinations
                        )
                        
                        for i, station in enumerate(ac_top_stations):
                            try:
                                if (ac_distance_matrix.get("rows") and 
                                    ac_distance_matrix["rows"][0].get("elements") and
                                    i < len(ac_distance_matrix["rows"][0]["elements"])):
                                    
                                    element = ac_distance_matrix["rows"][0]["elements"][i]
                                    if element.get("status") == "OK":
                                        duration_sec = element.get("duration", {}).get("value", 0)
                                        duration_min = duration_sec / 60
                                        
                                        if duration_min <= 15:
                                            station["_deviation_minutes"] = duration_min
                                            final_stations.append(station)
                                            
                            except Exception:
                                continue
                        
                        logger.info(f"AC fallback sonrası {len(final_stations)} istasyon kaldı")
                        
                    except Exception as e:
                        logger.error("AC fallback Distance Matrix başarısız", error=str(e))
            
            if not final_stations:
                return None, weather_forecast
                
        except Exception as e:
            logger.error("Distance Matrix çağrısı başarısız", error=str(e))
            return None, weather_forecast
        
        # --- STEP 5: İlk 3 için Place Details ---
        final_stations.sort(key=lambda x: x["_deviation_minutes"])
        top_3_stations = final_stations[:3]
        
        enriched_stations = []
        
        for i, station in enumerate(top_3_stations):
            try:
                # Rate limiting için küçük gecikme
                if i > 0:
                    await asyncio.sleep(0.3)
                
                address_info = station.get("AddressInfo", {})
                place_id = address_info.get("PlaceID")
                
                if place_id:
                    place_details = await maps_service.get_place_details(place_id)
                    station["_place_details"] = place_details
                
                enriched_stations.append(station)
                
            except Exception as e:
                logger.warning("Place Details zenginleştirme hatası", station_id=station.get("ID"), error=str(e))
                enriched_stations.append(station)  # Yine de ekle
        
        # --- STEP 6: Skorlama ve en iyiyi seç ---
        best_station = None
        best_score = -1
        
        # Max güç değerini normalize etmek için hesapla
        max_power = max([station.get("_max_power_kw", 0) for station in enriched_stations] + [1])
        
        for station in enriched_stations:
            try:
                # Normalize edilmiş sapma skoru (düşük daha iyi)
                deviation_minutes = station.get("_deviation_minutes", 15)
                deviation_score = 1 - (deviation_minutes / 15)  # 1.0'dan 0.0'a
                deviation_score = max(0, deviation_score)  # Negatif olmasın
                
                # Normalize edilmiş şarj gücü skoru
                power_kw = station.get("_max_power_kw", 0)
                power_score = power_kw / max_power if max_power > 0 else 0
                
                # Rating skoru
                rating = 0
                place_details = station.get("_place_details", {})
                if place_details.get("result"):
                    rating = place_details["result"].get("rating", 0)
                rating_score = rating / 5.0  # 5 yıldız üzerinden normalize
                
                # Ağırlıklı toplam skor
                total_score = (0.5 * deviation_score) + (0.3 * power_score) + (0.2 * rating_score)
                
                station["_final_score"] = total_score
                
                if total_score > best_score:
                    best_score = total_score
                    best_station = station
                    
                logger.info(
                    "İstasyon skorlandı",
                    station_id=station.get("ID"),
                    deviation_minutes=deviation_minutes,
                    deviation_score=round(deviation_score, 2),
                    power_score=round(power_score, 2),
                    rating_score=round(rating_score, 2),
                    total_score=round(total_score, 2)
                )
                
            except Exception as e:
                logger.warning("İstasyon skorlama hatası", station_id=station.get("ID"), error=str(e))
                continue
        
        if best_station:
            logger.info(
                "En iyi istasyon seçildi",
                station_id=best_station.get("ID"),
                station_name=best_station.get("AddressInfo", {}).get("Title"),
                final_score=round(best_station.get("_final_score", 0), 2),
                deviation_minutes=best_station.get("_deviation_minutes"),
                power_kw=best_station.get("_max_power_kw")
            )
        else:
            logger.warning("Hiçbir istasyon seçilemedi")
        
        return best_station, weather_forecast
        
    except Exception as e:
        logger.error("İstasyon bulma işlemi başarısız", error=str(e))
        return None, None
