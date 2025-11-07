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
                # Operasyonel kontrolü
                if not station.get("StatusType", {}).get("IsOperational", False):
                    continue
                
                # Bağlantı tipi uyumluluğu kontrolü
                connections = station.get("Connections", [])
                has_compatible_connector = False
                
                for conn in connections:
                    if conn.get("ConnectionType", {}).get("Title") == vehicle.connector_type:
                        has_compatible_connector = True
                        break
                
                if not has_compatible_connector:
                    continue
                
                filtered_stations.append(station)
                
            except Exception as e:
                logger.warning("İstasyon filtreleme hatası", station_id=station.get("ID"), error=str(e))
                continue
        
        logger.info(f"Operasyonel ve uyumlu {len(filtered_stations)} istasyon kaldı")
        
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
        
        if not nearby_stations:
            return None, weather_forecast
        
        # Distance Matrix için en yakın 10 istasyonu al
        nearby_stations.sort(key=lambda x: x["_haversine_distance_km"])
        top_nearby_stations = nearby_stations[:10]
        
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
            
            logger.info(f"Distance Matrix sonrası {len(final_stations)} istasyon kaldı")
            
            if not final_stations:
                return None, weather_forecast
                
        except Exception as e:
            logger.error("Distance Matrix çağrısı başarısız", error=str(e))
            return None, weather_forecast
        
        # --- STEP 5: İlk 3 için Place Details ---
        final_stations.sort(key=lambda x: x["_deviation_minutes"])
        top_3_stations = final_stations[:3]
        
        enriched_stations = []
        
        for station in top_3_stations:
            try:
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
        
        for station in enriched_stations:
            try:
                # Sapma skoru (düşük daha iyi)
                deviation_score = max(0, 15 - station.get("_deviation_minutes", 15)) / 15
                
                # Şarj gücü skoru
                power_score = min(1.0, station.get("_max_power_kw", 0) / 150)  # 150kW max referans
                
                # Rating skoru
                rating = 0
                place_details = station.get("_place_details", {})
                if place_details.get("result"):
                    rating = place_details["result"].get("rating", 0)
                rating_score = rating / 5.0  # 5 yıldız üzerinden normalize
                
                # Ağırlıklı toplam skor
                total_score = (deviation_score * 0.5) + (power_score * 0.3) + (rating_score * 0.2)
                
                station["_final_score"] = total_score
                
                if total_score > best_score:
                    best_score = total_score
                    best_station = station
                    
                logger.info(
                    "İstasyon skorlandı",
                    station_id=station.get("ID"),
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
                final_score=round(best_station.get("_final_score", 0), 2)
            )
        
        return best_station, weather_forecast
        
    except Exception as e:
        logger.error("İstasyon bulma işlemi başarısız", error=str(e))
        return None, None
