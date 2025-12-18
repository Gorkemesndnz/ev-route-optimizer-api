"""
Station Finder v2.0
====================

V2.0 Google Places Öncelikli + OCM Fallback İstasyon Arama Modülü.

Özellikler:
- Google Places API ile EV şarj istasyonu arama (birincil)
- OCM API fallback (Google boş dönerse)
- Connector uyumluluğu kontrolü
- DC/AC istasyon ayrımı
- Haversine mesafe filtreleme
- V1.5 Koridor bazlı arama (Corridor Search)
- Hotspot bazlı akıllı istasyon seçimi
- Greedy station selection
- Ağırlıklı skorlama sistemi

Flow:
1. Google Places'tan EV istasyonlarını ara
2. Google boş dönerse → OCM'ye fallback
3. Operasyonel ve uyumlu olanları filtrele
4. DC/AC ayrımı yap
5. Haversine/Koridor mesafe filtreleme
6. Ağırlıklı skorlama ile en iyiyi seç

Kullanım:
    from app.station_finder import (
        find_best_station,              # Tek nokta bazlı
        find_stations_for_hotspots,     # V1.5 çoklu hotspot
        CorridorSearcher                # V1.5 arama sınıfı
    )
"""

import asyncio
import math
from typing import Tuple, Optional, Dict, Any, List
from dataclasses import dataclass, field

from app.models import GeoPoint
from app.soc_simulator import ChargeHotspot
from app.services.ocm_service import ocm_service
from app.services.google_service import google_maps
from app.services.weather_service import weather_service
from app.consumption_engine.vehicle_models import get_vehicle_model, VehicleModel
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

# Koridor sabitleri (V1.5)
CORRIDOR_LENGTH_KM = 50.0
CORRIDOR_WIDTH_KM = 15.0
MIN_DC_POWER_KW = 50.0
MAX_STATIONS_PER_HOTSPOT = 5

# Skorlama ağırlıkları
WEIGHT_DEVIATION = 0.5
WEIGHT_POWER = 0.3
WEIGHT_RATING = 0.2

# Greedy selection ağırlıkları (V1.5)
GREEDY_WEIGHT_POWER = 0.45
GREEDY_WEIGHT_DEVIATION = 0.35
GREEDY_WEIGHT_RATING = 0.20


# =============================================================================
# DATA CLASSES (V1.5)
# =============================================================================

@dataclass
class CorridorStation:
    """
    Koridor içinde bulunan istasyon.
    """
    station_info: Dict[str, Any]
    distance_from_hotspot_km: float
    deviation_km: float = 0.0
    power_kw: float = 0.0
    is_dc: bool = False
    is_compatible: bool = True
    rating: float = 4.0
    score: float = 0.0
    
    @property
    def station_id(self) -> str:
        return str(self.station_info.get("ID", "unknown"))
    
    @property
    def station_name(self) -> str:
        address_info = self.station_info.get("AddressInfo", {})
        return address_info.get("Title", "Unnamed Station")
    
    @property
    def location(self) -> GeoPoint:
        address_info = self.station_info.get("AddressInfo", {})
        return GeoPoint(
            lat=address_info.get("Latitude", 0.0),
            lon=address_info.get("Longitude", 0.0)
        )


@dataclass
class CorridorSearchResult:
    """
    Koridor arama sonucu.
    """
    hotspot: ChargeHotspot
    stations: List[CorridorStation] = field(default_factory=list)
    best_station: Optional[CorridorStation] = None
    search_radius_km: float = CORRIDOR_LENGTH_KM
    total_found: int = 0
    dc_compatible: int = 0


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    İki koordinat arasındaki mesafeyi Haversine formülü ile km olarak hesaplar.
    """
    R = 6371.0
    
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
    """
    deviation_score = max(0, 1 - (deviation_minutes / MAX_DEVIATION_MINUTES))
    power_score = power_kw / max_power_kw if max_power_kw > 0 else 0
    rating_score = rating / 5.0
    
    return (WEIGHT_DEVIATION * deviation_score) + (WEIGHT_POWER * power_score) + (WEIGHT_RATING * rating_score)


# =============================================================================
# V1.5 CORRIDOR SEARCHER CLASS
# =============================================================================

class CorridorSearcher:
    """
    V1.5 Koridor Bazlı İstasyon Arama Motoru.
    
    Kullanım:
        searcher = CorridorSearcher(vehicle_model_id="mg4_51kwh")
        result = await searcher.search_for_hotspot(hotspot)
        best_station = result.best_station
    """
    
    def __init__(
        self,
        vehicle_model_id: str,
        corridor_length_km: float = CORRIDOR_LENGTH_KM,
        corridor_width_km: float = CORRIDOR_WIDTH_KM,
        min_dc_power_kw: float = MIN_DC_POWER_KW
    ):
        self.vehicle = get_vehicle_model(vehicle_model_id)
        self.vehicle_model_id = vehicle_model_id
        self.corridor_length_km = corridor_length_km
        self.corridor_width_km = corridor_width_km
        self.min_dc_power_kw = min_dc_power_kw
        
        logger.info(
            "CorridorSearcher initialized",
            vehicle=self.vehicle.model_name,
            connector=self.vehicle.connector_type,
            corridor_length=corridor_length_km
        )
    
    async def search_for_hotspot(self, hotspot: ChargeHotspot) -> CorridorSearchResult:
        """
        Bir hotspot için koridor araması yap.
        
        Strateji: Google Places öncelikli, OCM fallback.
        """
        result = CorridorSearchResult(
            hotspot=hotspot,
            search_radius_km=self.corridor_length_km
        )
        
        logger.info(
            "Corridor search started (Google-first strategy)",
            hotspot_segment=hotspot.segment_index,
            location=f"({hotspot.location.lat:.4f}, {hotspot.location.lon:.4f})",
            soc=hotspot.soc_at_point
        )
        
        try:
            # 1. Google Places'tan istasyonları çek (birincil)
            raw_stations, source = await self._fetch_stations_google_first(hotspot)
            result.total_found = len(raw_stations)
            
            if not raw_stations:
                logger.warning("No stations found from any source")
                return result
            
            # 2. Kaynak bazlı filtreleme ve skorlama
            if source == "google":
                corridor_stations = self._filter_and_score_google_stations(raw_stations, hotspot)
            else:
                corridor_stations = self._filter_and_score_stations(raw_stations, hotspot)
            
            result.dc_compatible = len(corridor_stations)
            
            if not corridor_stations:
                logger.warning("No compatible DC stations found")
                return result
            
            # Sırala ve en iyi N'i al
            corridor_stations.sort(key=lambda s: s.score, reverse=True)
            result.stations = corridor_stations[:MAX_STATIONS_PER_HOTSPOT]
            
            # Greedy selection
            result.best_station = self.greedy_select(result.stations, hotspot.soc_at_point)
            
            logger.info(
                "Corridor search completed",
                source=source,
                total_found=result.total_found,
                dc_compatible=result.dc_compatible,
                best_station=result.best_station.station_name if result.best_station else None
            )
            
            return result
            
        except Exception as e:
            logger.exception("Corridor search failed", error=str(e))
            return result
    
    async def _fetch_stations_google_first(self, hotspot: ChargeHotspot) -> Tuple[List[Dict[str, Any]], str]:
        """
        Google Places öncelikli istasyon arama.
        Google boş dönerse OCM'ye fallback yapar.
        
        Returns:
            (stations_list, source) - source: "google" veya "ocm"
        """
        search_radius_m = int(max(self.corridor_length_km, self.corridor_width_km) * 1000)
        
        # 1. Google Places'tan dene
        try:
            google_stations = await google_maps.search_ev_charging_stations(
                lat=hotspot.location.lat,
                lon=hotspot.location.lon,
                radius_m=search_radius_m,
                max_results=50
            )
            
            if google_stations:
                logger.info(f"Google Places returned {len(google_stations)} stations")
                return google_stations, "google"
            else:
                logger.info("Google Places returned empty, falling back to OCM")
                
        except Exception as e:
            logger.warning(f"Google Places search failed: {e}, falling back to OCM")
        
        # 2. OCM fallback
        try:
            ocm_stations = await ocm_service.get_nearby_stations_raw(
                lat=hotspot.location.lat,
                lon=hotspot.location.lon,
                radius_km=max(self.corridor_length_km, self.corridor_width_km)
            )
            
            if ocm_stations:
                logger.info(f"OCM fallback returned {len(ocm_stations)} stations")
                return ocm_stations, "ocm"
                
        except Exception as e:
            logger.error(f"OCM API call also failed: {e}")
        
        return [], "none"

    async def _fetch_stations_from_ocm(self, hotspot: ChargeHotspot) -> List[Dict[str, Any]]:
        """OCM API'den istasyonları çek (legacy - backward compatibility)."""
        try:
            search_radius = max(self.corridor_length_km, self.corridor_width_km)
            
            stations = await ocm_service.get_nearby_stations_raw(
                lat=hotspot.location.lat,
                lon=hotspot.location.lon,
                radius_km=search_radius
            )
            
            return stations
            
        except Exception as e:
            logger.error("OCM API call failed", error=str(e))
            return []
    
    def _filter_and_score_google_stations(
        self,
        google_stations: List[Dict[str, Any]],
        hotspot: ChargeHotspot
    ) -> List[CorridorStation]:
        """
        Google Places verilerini filtrele ve skorla.
        
        Google Places formatı:
        {
            "place_id": "...",
            "name": "...",
            "geometry": {"location": {"lat": ..., "lng": ...}},
            "rating": 4.5,
            "user_ratings_total": 100,
            "business_status": "OPERATIONAL",
            "vicinity": "..."
        }
        """
        filtered_stations = []
        
        for station in google_stations:
            try:
                # İşletme durumu kontrolü
                business_status = station.get("business_status", "OPERATIONAL")
                if business_status not in ("OPERATIONAL", None):
                    continue
                
                # Konum al
                geometry = station.get("geometry", {})
                location = geometry.get("location", {})
                station_lat = location.get("lat", 0)
                station_lng = location.get("lng", 0)
                
                if not station_lat or not station_lng:
                    continue
                
                # Mesafe hesapla
                distance = haversine_km(
                    hotspot.location.lat, hotspot.location.lon,
                    station_lat, station_lng
                )
                
                if distance > self.corridor_length_km:
                    continue
                
                # Rating al (Google doğrudan sağlar)
                rating = station.get("rating", 4.0)
                user_ratings_total = station.get("user_ratings_total", 0)
                
                # Google Places EV charging station tipi varsayılan olarak DC kabul et
                # (Google filtrelemesi zaten electric_vehicle_charging_station)
                # Güç bilgisi Google'dan doğrudan gelmiyor, varsayılan değer kullan
                estimated_power_kw = 50.0  # Varsayılan DC güç
                
                # Station info'yu Google formatında oluştur (OCM uyumlu dict)
                station_info = {
                    "ID": station.get("place_id", ""),
                    "AddressInfo": {
                        "Title": station.get("name", "Unknown Station"),
                        "Latitude": station_lat,
                        "Longitude": station_lng,
                        "AddressLine1": station.get("vicinity", "")
                    },
                    "Connections": [{
                        "PowerKW": estimated_power_kw,
                        "ConnectionType": {"Title": "CCS"}
                    }],
                    "StatusType": {"IsOperational": True},
                    "_source": "google",
                    "_rating": rating,
                    "_user_ratings_total": user_ratings_total,
                    "_place_id": station.get("place_id", "")
                }
                
                corridor_station = CorridorStation(
                    station_info=station_info,
                    distance_from_hotspot_km=round(distance, 2),
                    deviation_km=round(distance, 2),
                    power_kw=estimated_power_kw,
                    is_dc=True,  # Google EV charging genelde DC
                    is_compatible=True,  # Tip kontrolü sonra yapılabilir
                    rating=rating
                )
                
                filtered_stations.append(corridor_station)
                
            except Exception as e:
                logger.warning(f"Failed to process Google station: {e}")
                continue
        
        # Skorlama
        max_power = max((s.power_kw for s in filtered_stations), default=50.0)
        for station in filtered_stations:
            deviation_minutes = (station.deviation_km / 50.0) * 60.0
            station.score = _calculate_station_score(
                deviation_minutes=deviation_minutes,
                power_kw=station.power_kw,
                max_power_kw=max_power,
                rating=station.rating
            )
        
        logger.info(f"Filtered {len(filtered_stations)} Google stations (of {len(google_stations)} total)")
        return filtered_stations
    
    def _filter_and_score_stations(
        self,
        raw_stations: List[Dict[str, Any]],
        hotspot: ChargeHotspot
    ) -> List[CorridorStation]:
        """İstasyonları filtrele ve skorla."""
        filtered_stations = []
        max_power_in_batch = 0.0
        
        for station in raw_stations:
            # Operasyonel kontrolü
            status_type = station.get("StatusType", {})
            is_operational = status_type.get("IsOperational", True)
            
            if not is_operational:
                continue
            
            # Bağlayıcı bilgileri
            connections = station.get("Connections", [])
            if not connections:
                continue
            
            # Güç kontrolü
            power_kw = _get_max_power_kw(connections)
            if power_kw < self.min_dc_power_kw:
                continue
            
            max_power_in_batch = max(max_power_in_batch, power_kw)
            
            # Connector uyumluluk kontrolü
            is_compatible = _is_connector_compatible(connections, self.vehicle.connector_type)
            if not is_compatible:
                continue
            
            # Mesafe hesapla
            address_info = station.get("AddressInfo", {})
            station_lat = address_info.get("Latitude", 0)
            station_lon = address_info.get("Longitude", 0)
            
            distance = haversine_km(
                hotspot.location.lat, hotspot.location.lon,
                station_lat, station_lon
            )
            
            if distance > self.corridor_length_km:
                continue
            
            # Rating
            user_comments = station.get("UserComments", [])
            rating = 4.0
            if user_comments:
                ratings = [c.get("Rating", 4) for c in user_comments if c.get("Rating")]
                if ratings:
                    rating = sum(ratings) / len(ratings)
            
            corridor_station = CorridorStation(
                station_info=station,
                distance_from_hotspot_km=round(distance, 2),
                deviation_km=round(distance, 2),
                power_kw=power_kw,
                is_dc=power_kw >= DC_POWER_THRESHOLD_KW,
                is_compatible=is_compatible,
                rating=rating
            )
            
            filtered_stations.append(corridor_station)
        
        # Skorlama
        for station in filtered_stations:
            deviation_minutes = (station.deviation_km / 50.0) * 60.0
            station.score = _calculate_station_score(
                deviation_minutes=deviation_minutes,
                power_kw=station.power_kw,
                max_power_kw=max_power_in_batch,
                rating=station.rating
            )
        
        return filtered_stations
    
    def greedy_select(
        self,
        stations: List[CorridorStation],
        current_soc: float
    ) -> Optional[CorridorStation]:
        """Greedy algoritma ile en iyi istasyonu seç."""
        if not stations:
            return None
        
        # SOC düşükse güce daha fazla ağırlık
        power_weight = GREEDY_WEIGHT_POWER
        deviation_weight = GREEDY_WEIGHT_DEVIATION
        
        if current_soc < 20.0:
            power_weight = 0.55
            deviation_weight = 0.25
        elif current_soc < 30.0:
            power_weight = 0.50
            deviation_weight = 0.30
        
        best_station = None
        best_greedy_score = -1
        
        for station in stations:
            power_score = station.power_kw / 350.0
            deviation_score = max(0, 1.0 - (station.deviation_km / self.corridor_length_km))
            rating_score = station.rating / 5.0
            
            greedy_score = (
                power_weight * power_score +
                deviation_weight * deviation_score +
                GREEDY_WEIGHT_RATING * rating_score
            )
            
            if greedy_score > best_greedy_score:
                best_greedy_score = greedy_score
                best_station = station
        
        if best_station:
            logger.info(
                "Greedy selection completed",
                station=best_station.station_name,
                power_kw=best_station.power_kw,
                deviation_km=best_station.deviation_km
            )
        
        return best_station


# =============================================================================
# V1.5 CONVENIENCE FUNCTIONS
# =============================================================================

async def find_stations_for_hotspots(
    hotspots: List[ChargeHotspot],
    vehicle_model_id: str,
    min_distance_between_stations_km: float = 50.0
) -> List[CorridorSearchResult]:
    """
    Birden fazla hotspot için akıllı istasyon seçimi.
    
    Özellikler:
    - Aynı istasyonu tekrar seçmez
    - Birbirine çok yakın istasyonları önler
    - Her hotspot için alternatif istasyon bulur
    
    Args:
        hotspots: Şarj gerekli noktalar
        vehicle_model_id: Araç modeli
        min_distance_between_stations_km: İstasyonlar arası minimum mesafe
    """
    if not hotspots:
        return []
    
    searcher = CorridorSearcher(vehicle_model_id=vehicle_model_id)
    
    # Paralel arama yap (tüm istasyonları bul)
    tasks = [searcher.search_for_hotspot(hotspot) for hotspot in hotspots]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_results = []
    used_station_ids = set()
    last_station_location = None
    
    for i, result in enumerate(raw_results):
        if isinstance(result, Exception):
            logger.error(f"Hotspot {i} search failed", error=str(result))
            continue
        
        # Akıllı istasyon seçimi
        if result.stations:
            # Daha önce seçilen istasyonları filtrele
            available_stations = [
                s for s in result.stations 
                if s.station_id not in used_station_ids
            ]
            
            # Çok yakın istasyonları filtrele
            if last_station_location and available_stations:
                available_stations = [
                    s for s in available_stations
                    if haversine_km(
                        last_station_location.lat, last_station_location.lon,
                        s.location.lat, s.location.lon
                    ) >= min_distance_between_stations_km
                ]
            
            # Sırala ve en iyiyi seç
            if available_stations:
                available_stations.sort(key=lambda s: s.score, reverse=True)
                result.best_station = available_stations[0]
                used_station_ids.add(result.best_station.station_id)
                last_station_location = result.best_station.location
                
                logger.info(
                    f"Smart station selection: Hotspot {i+1} → {result.best_station.station_name} "
                    f"(avoided {len(result.stations) - len(available_stations)} duplicates)"
                )
            else:
                # Alternatif bulunamazsa, en iyiyi kullan (uyarı ile)
                if result.stations:
                    result.best_station = result.stations[0]
                    logger.warning(
                        f"Hotspot {i+1}: No alternative station, using {result.best_station.station_name} again"
                    )
        
        valid_results.append(result)
    
    logger.info(
        "Smart multi-hotspot search completed",
        total_hotspots=len(hotspots),
        successful=len(valid_results),
        unique_stations=len(used_station_ids)
    )
    
    return valid_results


async def find_best_station_for_hotspot(
    hotspot: ChargeHotspot,
    vehicle_model_id: str
) -> Tuple[Optional[CorridorStation], CorridorSearchResult]:
    """Tek bir hotspot için en iyi istasyonu bul."""
    searcher = CorridorSearcher(vehicle_model_id=vehicle_model_id)
    result = await searcher.search_for_hotspot(hotspot)
    return result.best_station, result


# =============================================================================
# LEGACY FUNCTION (V1.3 Uyumluluk)
# =============================================================================

async def find_best_station(
    latitude: float,
    longitude: float,
    vehicle_model_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    V1.3 "Station Funnel" mantığı ile en uygun şarj istasyonunu bulur.
    Legacy API uyumluluğu için korunuyor.
    """
    try:
        vehicle = get_vehicle_model(vehicle_model_id)
        
        logger.info(
            "Station search started (legacy)",
            lat=latitude,
            lon=longitude,
            vehicle=vehicle.model_name
        )
        
        # Paralel veri çekimi
        stations_task = ocm_service.get_nearby_stations(
            lat=latitude,
            lon=longitude,
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
        
        if isinstance(stations_data, Exception):
            logger.error("OCM station data fetch failed", error=str(stations_data))
            stations_data = []
        
        if isinstance(weather_forecast, Exception):
            logger.warning("Weather forecast fetch failed")
            weather_forecast = None
        
        if not stations_data:
            return None, weather_forecast
        
        # Filtrele
        filtered_stations = []
        for station in stations_data:
            try:
                status_type = station.get("StatusType", {})
                if not status_type.get("IsOperational", True):
                    continue
                
                connections = station.get("Connections", [])
                if not _is_connector_compatible(connections, vehicle.connector_type):
                    continue
                
                power_kw = _get_max_power_kw(connections)
                if power_kw < DC_POWER_THRESHOLD_KW:
                    continue
                
                station["_max_power_kw"] = power_kw
                
                address_info = station.get("AddressInfo", {})
                station_lat = address_info.get("Latitude", 0)
                station_lon = address_info.get("Longitude", 0)
                
                distance = haversine_km(latitude, longitude, station_lat, station_lon)
                station["_haversine_distance_km"] = distance
                
                if distance <= MAX_HAVERSINE_DISTANCE_KM:
                    filtered_stations.append(station)
                    
            except Exception as e:
                continue
        
        if not filtered_stations:
            return None, weather_forecast
        
        # En iyiyi seç
        filtered_stations.sort(key=lambda x: x.get("_haversine_distance_km", 999))
        best_station = filtered_stations[0] if filtered_stations else None
        
        if best_station:
            logger.info(
                "Best station selected (legacy)",
                station_id=best_station.get("ID"),
                station_name=best_station.get("AddressInfo", {}).get("Title"),
                power_kw=best_station.get("_max_power_kw")
            )
        
        return best_station, weather_forecast
        
    except Exception as e:
        logger.exception("Station finder failed", error=str(e))
        return None, None


# Backward compatibility alias
async def find_charging_station(
    lat: float,
    lon: float,
    vehicle_model_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Alias for backward compatibility."""
    return await find_best_station(lat, lon, vehicle_model_id)
