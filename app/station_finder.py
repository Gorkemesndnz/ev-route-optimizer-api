"""
Station Finder v2.0
====================

V1.3 Enterprise "Station Funnel" algoritması ile en uygun şarj istasyonunu bulur.

Özellikler:
- OCM API ile istasyon verisi çekme
- Connector uyumluluğu kontrolü
- DC/AC istasyon ayrımı
- Haversine mesafe filtreleme
- Google Distance Matrix ile gerçek sürüş süresi
- Google Place Details ile rating bilgisi
- Ağırlıklı skorlama sistemi

Flow:
1. OCM'den yakın istasyonları al
2. Operasyonel ve uyumlu olanları filtrele
3. DC/AC ayrımı yap
4. Haversine mesafe filtreleme (50km)
5. Distance Matrix ile gerçek süre kontrolü (15 dakika)
6. Place Details ile zenginleştir
7. Ağırlıklı skorlama ile en iyiyi seç
"""

import asyncio
import math
from typing import Tuple, Optional, Dict, Any, List

from app.models import GeoPoint
from app.services.ocm_service import ocm_service
from app.services.google_service import google_maps
from app.services.weather_service import weather_service
from app.consumption_engine.vehicle_models import get_vehicle_model
from app.utils.config_manager import config
from app.utils.logger import get_logger


# =============================================================================
# CONSTANTS & LOGGER
# =============================================================================

logger = get_logger("station_finder")

# Filtreleme sabitleri
MAX_HAVERSINE_DISTANCE_KM = 50.0
MAX_DEVIATION_MINUTES = 15.0
DC_POWER_THRESHOLD_KW = 40.0
MAX_DISTANCE_MATRIX_DESTINATIONS = 100
TOP_STATIONS_FOR_DETAILS = 3

# Skorlama ağırlıkları
WEIGHT_DEVIATION = 0.5
WEIGHT_POWER = 0.3
WEIGHT_RATING = 0.2


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

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


def _is_connector_compatible(connections: List[dict], vehicle_connector: str) -> bool:
    """İstasyonun bağlayıcı tipinin araçla uyumlu olup olmadığını kontrol et."""
    vehicle_connector_lower = vehicle_connector.lower()
    
    for conn in connections:
        connection_type = conn.get("ConnectionType", {})
        connection_title = connection_type.get("Title", "")
        
        if connection_title and vehicle_connector_lower in connection_title.lower():
            return True
    
    return False


def _get_max_power_kw(connections: List[dict]) -> float:
    """İstasyonun maksimum şarj gücünü döndür."""
    max_power = 0.0
    
    for conn in connections:
        power_kw = conn.get("PowerKW") or 0
        if power_kw > max_power:
            max_power = float(power_kw)
    
    return max_power


def _calculate_station_score(
    deviation_minutes: float,
    power_kw: float,
    max_power_kw: float,
    rating: float
) -> float:
    """
    İstasyon için ağırlıklı skor hesapla.
    
    - Deviation: Düşük daha iyi (0-15 dakika → 1.0-0.0)
    - Power: Yüksek daha iyi (normalize edilmiş)
    - Rating: Yüksek daha iyi (0-5 → 0.0-1.0)
    """
    # Sapma skoru (düşük daha iyi)
    deviation_score = max(0, 1 - (deviation_minutes / MAX_DEVIATION_MINUTES))
    
    # Güç skoru (yüksek daha iyi)
    power_score = power_kw / max_power_kw if max_power_kw > 0 else 0
    
    # Rating skoru
    rating_score = rating / 5.0
    
    # Ağırlıklı toplam
    return (WEIGHT_DEVIATION * deviation_score) + (WEIGHT_POWER * power_score) + (WEIGHT_RATING * rating_score)


# =============================================================================
# MAIN FUNCTION
# =============================================================================

async def find_best_station(
    latitude: float,
    longitude: float,
    vehicle_model_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    V1.3 "Station Funnel" mantığı ile en uygun şarj istasyonunu bulur.
    
    Args:
        latitude: Mevcut konum enlemi
        longitude: Mevcut konum boylamı
        vehicle_model_id: Araç modeli ID'si
    
    Returns:
        Tuple[best_station_dict, weather_forecast_dict] veya (None, None)
    """
    try:
        # Araç bilgilerini al
        try:
            vehicle = get_vehicle_model(vehicle_model_id)
        except ValueError as e:
            logger.error("Vehicle model not found", vehicle_id=vehicle_model_id, error=str(e))
            return None, None
        
        logger.info(
            "Station search started",
            lat=latitude,
            lon=longitude,
            vehicle=vehicle.model_name,
            connector=vehicle.connector_type
        )
        
        # =====================================================================
        # STEP 1: Paralel veri çekimi (OCM + Weather)
        # =====================================================================
        stations_task = ocm_service.get_stations_nearby(
            latitude=latitude,
            longitude=longitude,
            radius_km=30
        )
        
        weather_task = weather_service.get_forecast_for_point(
            lat=latitude,
            lon=longitude
        )
        
        stations_data, weather_forecast = await asyncio.gather(
            stations_task,
            weather_task,
            return_exceptions=True
        )
        
        # Hata kontrolü
        if isinstance(stations_data, Exception):
            logger.error("OCM station data fetch failed", error=str(stations_data))
            stations_data = []
        
        if isinstance(weather_forecast, Exception):
            logger.warning("Weather forecast fetch failed", error=str(weather_forecast))
            weather_forecast = None
        
        logger.info(f"OCM returned {len(stations_data)} stations")
        
        if not stations_data:
            return None, weather_forecast
        
        # =====================================================================
        # STEP 2: Operasyonel ve uyumlu istasyonları filtrele
        # =====================================================================
        filtered_stations = []
        
        for station in stations_data:
            try:
                # Operasyonel kontrolü
                status_type = station.get("StatusType", {})
                status_title = status_type.get("Title", "")
                
                if not status_title or "operational" not in status_title.lower():
                    continue
                
                # Connector uyumluluğu
                connections = station.get("Connections", [])
                if not _is_connector_compatible(connections, vehicle.connector_type):
                    continue
                
                # Max güç hesapla
                station["_max_power_kw"] = _get_max_power_kw(connections)
                filtered_stations.append(station)
                
            except Exception as e:
                logger.warning("Station filtering error", station_id=station.get("ID"), error=str(e))
                continue
        
        logger.info(
            "Station filtering completed",
            total_ocm=len(stations_data),
            filtered=len(filtered_stations)
        )
        
        if not filtered_stations:
            return None, weather_forecast
        
        # =====================================================================
        # STEP 3: DC ve AC istasyonlarını ayır
        # =====================================================================
        dc_stations = [s for s in filtered_stations if s.get("_max_power_kw", 0) > DC_POWER_THRESHOLD_KW]
        ac_stations = [s for s in filtered_stations if s.get("_max_power_kw", 0) <= DC_POWER_THRESHOLD_KW]
        
        # DC tercih et, yoksa AC
        candidate_stations = dc_stations if dc_stations else ac_stations
        
        logger.info(f"DC stations: {len(dc_stations)}, AC stations: {len(ac_stations)}, Candidates: {len(candidate_stations)}")
        
        if not candidate_stations:
            return None, weather_forecast
        
        # =====================================================================
        # STEP 4: Haversine mesafe filtreleme
        # =====================================================================
        nearby_stations = []
        
        for station in candidate_stations:
            try:
                address_info = station.get("AddressInfo", {})
                station_lat = address_info.get("Latitude")
                station_lon = address_info.get("Longitude")
                
                if not station_lat or not station_lon:
                    continue
                
                distance_km = haversine_km(latitude, longitude, station_lat, station_lon)
                
                if distance_km <= MAX_HAVERSINE_DISTANCE_KM:
                    station["_haversine_distance_km"] = distance_km
                    nearby_stations.append(station)
                    
            except Exception as e:
                logger.warning("Distance calculation error", station_id=station.get("ID"), error=str(e))
                continue
        
        logger.info(
            "Haversine filtering completed",
            before=len(candidate_stations),
            after=len(nearby_stations)
        )
        
        if not nearby_stations:
            return None, weather_forecast
        
        # Mesafeye göre sırala ve limitle
        nearby_stations.sort(key=lambda x: x["_haversine_distance_km"])
        top_nearby_stations = nearby_stations[:MAX_DISTANCE_MATRIX_DESTINATIONS]
        
        # =====================================================================
        # STEP 5: Google Distance Matrix ile gerçek süre kontrolü
        # =====================================================================
        origin_point = GeoPoint(lat=latitude, lon=longitude)
        destination_points = []
        
        for station in top_nearby_stations:
            address_info = station.get("AddressInfo", {})
            station_lat = address_info.get("Latitude")
            station_lon = address_info.get("Longitude")
            
            if station_lat and station_lon:
                destination_points.append(GeoPoint(lat=station_lat, lon=station_lon))
        
        final_stations = []
        
        try:
            distance_matrix = await google_maps.get_distance_matrix(
                origins=[origin_point],
                destinations=destination_points
            )
            
            for i, station in enumerate(top_nearby_stations):
                try:
                    if (distance_matrix.get("rows") and 
                        distance_matrix["rows"][0].get("elements") and
                        i < len(distance_matrix["rows"][0]["elements"])):
                        
                        element = distance_matrix["rows"][0]["elements"][i]
                        if element.get("status") == "OK":
                            duration_sec = element.get("duration", {}).get("value", 0)
                            duration_min = duration_sec / 60
                            
                            if duration_min <= MAX_DEVIATION_MINUTES:
                                station["_deviation_minutes"] = duration_min
                                final_stations.append(station)
                                
                except Exception as e:
                    logger.warning("Distance matrix element error", station_id=station.get("ID"), error=str(e))
                    continue
            
            logger.info(
                "Distance Matrix filtering completed",
                before=len(top_nearby_stations),
                after=len(final_stations),
                threshold_min=MAX_DEVIATION_MINUTES
            )
            
        except Exception as e:
            logger.error("Distance Matrix API failed", error=str(e))
            # Fallback: Haversine mesafesini kullan
            for station in top_nearby_stations[:10]:
                station["_deviation_minutes"] = station.get("_haversine_distance_km", 10) * 1.5  # Yaklaşık süre
                final_stations.append(station)
        
        # DC başarısız olduysa AC'ye fallback
        if not final_stations and dc_stations and ac_stations:
            logger.info("DC stations filtered out, falling back to AC stations")
            # AC için aynı işlemi tekrarla (basitleştirilmiş)
            ac_nearby = [s for s in ac_stations if s.get("_haversine_distance_km", 100) <= MAX_HAVERSINE_DISTANCE_KM]
            for station in ac_nearby[:5]:
                station["_deviation_minutes"] = station.get("_haversine_distance_km", 10) * 1.5
                final_stations.append(station)
        
        if not final_stations:
            return None, weather_forecast
        
        # =====================================================================
        # STEP 6: Place Details ile zenginleştir (Top 3)
        # =====================================================================
        final_stations.sort(key=lambda x: x.get("_deviation_minutes", 999))
        top_stations = final_stations[:TOP_STATIONS_FOR_DETAILS]
        
        enriched_stations = []
        
        for i, station in enumerate(top_stations):
            try:
                if i > 0:
                    await asyncio.sleep(0.3)  # Rate limiting
                
                address_info = station.get("AddressInfo", {})
                place_id = address_info.get("PlaceID")
                
                if place_id:
                    try:
                        place_details = await google_maps.get_place_details(place_id)
                        station["_place_details"] = place_details
                    except Exception as e:
                        logger.warning("Place Details failed", place_id=place_id, error=str(e))
                
                enriched_stations.append(station)
                
            except Exception as e:
                logger.warning("Station enrichment error", station_id=station.get("ID"), error=str(e))
                enriched_stations.append(station)
        
        # =====================================================================
        # STEP 7: Skorlama ve en iyiyi seç
        # =====================================================================
        if not enriched_stations:
            return None, weather_forecast
        
        # Max güç hesapla (normalizasyon için)
        max_power_kw = max([s.get("_max_power_kw", 0) for s in enriched_stations] + [1])
        
        best_station = None
        best_score = -1
        
        for station in enriched_stations:
            try:
                deviation_min = station.get("_deviation_minutes", MAX_DEVIATION_MINUTES)
                power_kw = station.get("_max_power_kw", 0)
                
                # Rating'i place details'dan al
                rating = 0
                place_details = station.get("_place_details", {})
                if place_details.get("result"):
                    rating = place_details["result"].get("rating", 0)
                
                # Skor hesapla
                score = _calculate_station_score(deviation_min, power_kw, max_power_kw, rating)
                station["_final_score"] = score
                
                if score > best_score:
                    best_score = score
                    best_station = station
                
                logger.debug(
                    "Station scored",
                    station_id=station.get("ID"),
                    deviation_min=round(deviation_min, 1),
                    power_kw=power_kw,
                    rating=rating,
                    score=round(score, 3)
                )
                
            except Exception as e:
                logger.warning("Station scoring error", station_id=station.get("ID"), error=str(e))
                continue
        
        if best_station:
            logger.info(
                "Best station selected",
                station_id=best_station.get("ID"),
                station_name=best_station.get("AddressInfo", {}).get("Title"),
                score=round(best_station.get("_final_score", 0), 3),
                deviation_min=best_station.get("_deviation_minutes"),
                power_kw=best_station.get("_max_power_kw")
            )
        else:
            logger.warning("No station could be selected")
        
        return best_station, weather_forecast
        
    except Exception as e:
        logger.exception("Station finder failed", error=str(e))
        return None, None


# =============================================================================
# BACKWARD COMPATIBILITY ALIAS
# =============================================================================

async def find_charging_station(
    lat: float,
    lon: float,
    vehicle_model_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Alias for find_best_station for backward compatibility."""
    return await find_best_station(lat, lon, vehicle_model_id)
