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
from app.utils.charging_estimator import estimate_dc_charging_power
from app.services.feedback_service import feedback_manager
from app.services.station_logic import StationScorer, StationFilter
from app.utils.geo import haversine_km, calculate_bearing
from app.services.station_logic.scorer import (
    WEIGHT_DEVIATION, WEIGHT_POWER, WEIGHT_RATING, WEIGHT_AMENITIES, WEIGHT_POPULARITY,
    GREEDY_WEIGHT_POWER, GREEDY_WEIGHT_DEVIATION, GREEDY_WEIGHT_RATING,
    GREEDY_WEIGHT_AMENITIES, GREEDY_WEIGHT_POPULARITY,
    RATING_CONFIDENCE_THRESHOLD, RATING_PRIOR,
    POPULARITY_HIGH_THRESHOLD, POPULARITY_VERY_HIGH_THRESHOLD,
    AMENITY_BONUS_TOILET, AMENITY_BONUS_FOOD, AMENITY_BONUS_SHOPPING,
    AMENITY_BONUS_PARKING, AMENITY_BONUS_OPEN_NOW, MAX_DEVIATION_MINUTES
)


# =============================================================================
# CONSTANTS & LOGGER
# =============================================================================

logger = get_logger("station_finder")

# Filtreleme sabitleri
MAX_HAVERSINE_DISTANCE_KM = 50.0
DC_POWER_THRESHOLD_KW = 40.0
MAX_DISTANCE_MATRIX_DESTINATIONS = 100
TOP_STATIONS_FOR_DETAILS = 3

# Koridor sabitleri (V1.5)
CORRIDOR_LENGTH_KM = 50.0
CORRIDOR_WIDTH_KM = 10.0
MIN_DC_POWER_KW = 50.0
MAX_STATIONS_PER_HOTSPOT = 5

# 🔧 V3.3: Kademeli arama yarıçapları (istasyon bulunamazsa genişlet)
SEARCH_RADII_KM = [50, 80, 120]  # km - 3 kademeli arama

# 🔧 V3.2: Skorlama ve filtreleme sabitleri station_logic/ altına taşındı
# Import: from app.services.station_logic.scorer import WEIGHT_*, AMENITY_*, etc.


# =============================================================================
# DATA CLASSES (V1.5)
# =============================================================================

@dataclass
class CorridorStation:
    """
    Koridor içinde bulunan istasyon.
    🔧 V2.8: Amenities ve weighted rating desteği eklendi.
    """
    station_info: Dict[str, Any]
    distance_from_hotspot_km: float
    deviation_km: float = 0.0
    power_kw: float = 0.0
    is_dc: bool = False
    is_compatible: bool = True
    rating: float = 4.0
    user_ratings_total: int = 0  # 🔧 V2.8: Weighted rating için
    score: float = 0.0
    # 🔧 V2.8: Amenities alanları
    has_toilet: bool = False
    has_food: bool = False
    has_shopping: bool = False
    has_parking: bool = False
    is_open_now: Optional[bool] = None
    
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
    weather_forecast: Optional[Dict[str, Any]] = None  # 🔧 V2.7: İstasyon için forecast
    amenities_warning: Optional[str] = None  # 🔧 V3.2: Zorunlu imkan bulunamadı uyarısı


# =============================================================================
# HELPER FUNCTIONS



def _is_station_on_route_side(
    route_bearing: float,
    hotspot_lat: float,
    hotspot_lon: float,
    station_lat: float,
    station_lon: float,
    max_perpendicular_distance_km: float = 1.0
) -> Tuple[bool, float]:
    """
    🔧 V3.1: İstasyonun rotanın doğru tarafında olup olmadığını kontrol et.
    
    Otoyolda karşı yöndeki istasyonları filtrelemek için:
    - Rota yönüne dik mesafeyi hesapla
    - Açı farkı kontrolü (90°+ = muhtemelen karşı tarafta)
    - Mesafeye göre dinamik tolerans
    
    Args:
        route_bearing: Rotanın gittiği yön (derece)
        hotspot_lat/lon: Şarj gerekli nokta
        station_lat/lon: İstasyon konumu
        max_perpendicular_distance_km: Rotaya dik maksimum mesafe (km)
    
    Returns:
        (is_valid, perpendicular_distance_km)
    """
    # İstasyona olan bearing
    station_bearing = _calculate_bearing(hotspot_lat, hotspot_lon, station_lat, station_lon)
    
    # Rota ile istasyon arasındaki açı farkı
    angle_diff = abs(station_bearing - route_bearing)
    if angle_diff > 180:
        angle_diff = 360 - angle_diff
    
    # İstasyona olan toplam mesafe
    total_distance = haversine_km(hotspot_lat, hotspot_lon, station_lat, station_lon)
    
    # Rotaya dik mesafe (perpendicular) = toplam mesafe × sin(açı farkı)
    perpendicular_distance = total_distance * math.sin(math.radians(angle_diff))
    
    # İleri yönde mesafe (along-route) = toplam mesafe × cos(açı farkı)
    along_route_distance = total_distance * math.cos(math.radians(angle_diff))
    
    # 🔧 V3.1: Dinamik tolerans - yakın istasyonlar için daha sıkı kontrol
    # Otoyolda karşı şerit sadece 50-200m uzaklıkta, bu yüzden yakın mesafelerde
    # daha sıkı filtreleme gerekiyor
    if total_distance <= 1.0:
        # Çok yakın istasyonlar (1 km içinde) - sıkı kontrol
        effective_max_perp = min(max_perpendicular_distance_km, 0.3)  # Max 300m
    elif total_distance <= 3.0:
        # Yakın istasyonlar (1-3 km) - orta sıkılıkta
        effective_max_perp = min(max_perpendicular_distance_km, 0.5)  # Max 500m
    else:
        # Uzak istasyonlar - normal tolerans
        effective_max_perp = max_perpendicular_distance_km
    
    # 🔧 V3.1: Açı farkı kontrolü
    # Eğer istasyon rotanın neredeyse ters yönündeyse (135°+), büyük ihtimalle
    # karşı yönde veya çok farklı bir yerde
    is_opposite_direction = angle_diff >= 135.0
    
    # Kriterler:
    # 1. İstasyon ileri yönde olmalı (arkada değil) - along_route >= -1 km tolerans
    # 2. Rotaya dik mesafe effective_max_perp'den küçük olmalı
    # 3. Ters yönde olmamalı (135°+ açı farkı)
    is_forward = along_route_distance >= -1.0  # 1km geriye tolerans (daraltıldı)
    is_close_to_route = perpendicular_distance <= effective_max_perp
    
    is_valid = is_forward and is_close_to_route and not is_opposite_direction
    
    return is_valid, perpendicular_distance


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


def _calculate_weighted_rating(rating: float, user_ratings_total: int) -> float:
    """
    🔧 V2.8: Weighted rating hesapla.
    
    Az yorumlu istasyonlarda rating güvenilirliği düşük olduğundan,
    yorum sayısına göre rating'i bir prior ile karıştırır.
    
    Formül: weighted = confidence * rating + (1 - confidence) * prior
    
    Args:
        rating: Google/OCM rating (0-5)
        user_ratings_total: Toplam yorum sayısı
    
    Returns:
        Güven ayarlı rating (0-5)
    """
    if user_ratings_total <= 0:
        return RATING_PRIOR
    
    # Güven katsayısı: 50+ yorum = %100 güven
    confidence = min(1.0, user_ratings_total / RATING_CONFIDENCE_THRESHOLD)
    
    # Weighted rating
    weighted = confidence * rating + (1 - confidence) * RATING_PRIOR
    
    return weighted


def _calculate_amenities_score(
    has_toilet: bool = False,
    has_food: bool = False,
    has_shopping: bool = False,
    has_parking: bool = False,
    is_open_now: Optional[bool] = None
) -> float:
    """
    🔧 V2.8: Amenities (tesis olanakları) skoru hesapla.
    
    Kullanıcılar rota sapması benzer ise tuvalet, yemek, market
    gibi olanakları olan istasyonları tercih eder.
    
    Returns:
        0.0 - 1.0 arası amenities skoru
    """
    score = 0.0
    
    if has_toilet:
        score += AMENITY_BONUS_TOILET
    if has_food:
        score += AMENITY_BONUS_FOOD
    if has_shopping:
        score += AMENITY_BONUS_SHOPPING
    if has_parking:
        score += AMENITY_BONUS_PARKING
    if is_open_now is True:  # Explicitly True (None = bilinmiyor)
        score += AMENITY_BONUS_OPEN_NOW
    
    # Normalize to 0-1 (max possible = 1.0)
    return min(1.0, score)


def _calculate_popularity_score(user_ratings_total: int) -> float:
    """
    🔧 V2.9: Popülerlik skoru hesapla.
    
    Yüksek yorum sayısına sahip istasyonlar (Highway gibi) bonus alır.
    
    Returns:
        0.0 - 1.0 arası popülerlik skoru
    """
    if user_ratings_total >= POPULARITY_VERY_HIGH_THRESHOLD:
        return 1.0  # 500+ yorum = maksimum bonus
    elif user_ratings_total >= POPULARITY_HIGH_THRESHOLD:
        return 0.7  # 200-500 yorum = yüksek bonus
    elif user_ratings_total >= 50:
        return 0.4  # 50-200 yorum = orta bonus
    elif user_ratings_total >= 10:
        return 0.2  # 10-50 yorum = düşük bonus
    else:
        return 0.0  # 10'dan az yorum = bonus yok




def _calculate_station_score(
    deviation_minutes: float,
    power_kw: float,
    max_power_kw: float,
    rating: float,
    user_ratings_total: int = 0,
    has_toilet: bool = False,
    has_food: bool = False,
    has_shopping: bool = False,
    has_parking: bool = False,
    is_open_now: Optional[bool] = None
) -> float:
    """
    🔧 V2.9: İstasyon için ağırlıklı skor hesapla.
    
    V2.9 Güncellemeleri:
    - Rating ağırlığı artırıldı (%15 → %25)
    - Popülerlik skoru eklendi (yüksek yorum sayısı = bonus)
    """
    deviation_score = max(0, 1 - (deviation_minutes / MAX_DEVIATION_MINUTES))
    power_score = power_kw / max_power_kw if max_power_kw > 0 else 0
    
    # Weighted rating kullan
    weighted_rating = _calculate_weighted_rating(rating, user_ratings_total)
    rating_score = weighted_rating / 5.0
    
    # Amenities skoru
    amenities_score = _calculate_amenities_score(
        has_toilet, has_food, has_shopping, has_parking, is_open_now
    )
    
    # 🔧 V2.9: Popülerlik skoru (yüksek yorum sayısı = güvenilir istasyon)
    popularity_score = _calculate_popularity_score(user_ratings_total)
    
    return (
        WEIGHT_DEVIATION * deviation_score + 
        WEIGHT_POWER * power_score + 
        WEIGHT_RATING * rating_score +
        WEIGHT_AMENITIES * amenities_score +
        WEIGHT_POPULARITY * popularity_score
    )


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
        🔧 V3.1: Google Places öncelikli + OCM fallback istasyon arama.
        
        Args:
            hotspot: Şarj gerekli olan nokta
            
        Returns:
            CorridorSearchResult: Bulunan istasyonlar ve seçilen
        """
        logger.info(
            f"🔍 Searching stations for hotspot at {hotspot.location.lat:.4f}, {hotspot.location.lon:.4f} "
            f"(distance: {hotspot.distance_from_start_km:.0f}km from start, "
            f"remaining: {hotspot.remaining_distance_km:.0f}km to end, "
            f"corridor: {self.corridor_length_km}km length, {self.corridor_width_km}km width)"
        )
        
        logger.info(
            "Corridor search started (Google-first strategy)",
            hotspot_segment=hotspot.segment_index,
            location=f"({hotspot.location.lat:.4f}, {hotspot.location.lon:.4f})",
            soc=hotspot.soc_at_point
        )
        
        # Initialize result
        result = CorridorSearchResult(
            hotspot=hotspot,
            stations=[],
            best_station=None,
            total_found=0,
            dc_compatible=0,
            weather_forecast=None
        )
        
        try:
            # 🔧 V3.3: Kademeli arama yarıçapı - istasyon bulunamazsa genişlet
            raw_stations = []
            source = "none"
            used_radius = SEARCH_RADII_KM[0]
            
            for radius in SEARCH_RADII_KM:
                used_radius = radius
                stations_task = self._fetch_stations_google_first(hotspot, search_radius_km=radius)
                forecast_task = weather_service.get_forecast_for_point(
                    lat=hotspot.location.lat,
                    lon=hotspot.location.lon
                )
                
                (fetch_result, source), forecast_data = await asyncio.gather(
                    stations_task,
                    forecast_task,
                    return_exceptions=True
                )
                
                # Forecast sonucunu işle (sadece ilk iterasyonda)
                if radius == SEARCH_RADII_KM[0]:
                    if isinstance(forecast_data, Exception):
                        logger.warning(f"Forecast fetch failed for hotspot: {forecast_data}")
                        forecast_data = None
                    result.weather_forecast = forecast_data
                
                # İstasyon sonucunu işle
                if isinstance(fetch_result, Exception):
                    logger.error(f"Station fetch failed at {radius}km: {fetch_result}")
                    continue
                
                raw_stations = fetch_result
                result.total_found = len(raw_stations)
                
                if raw_stations:
                    # Kaynak bazlı filtreleme ve skorlama
                    if source == "google":
                        corridor_stations = self._filter_and_score_google_stations(raw_stations, hotspot)
                    else:
                        corridor_stations = self._filter_and_score_stations(raw_stations, hotspot)
                    
                    result.dc_compatible = len(corridor_stations)
                    
                    if corridor_stations:
                        logger.info(f"Found {len(corridor_stations)} DC stations at {radius}km radius")
                        break
                    else:
                        logger.warning(f"No compatible DC stations at {radius}km, expanding search...")
                else:
                    logger.warning(f"No stations found at {radius}km radius, expanding search...")
            
            # Tüm yarıçaplarda istasyon bulunamadı
            if not raw_stations:
                logger.warning(f"No stations found after trying all radii: {SEARCH_RADII_KM}")
                return result
            
            result.search_radius_km = used_radius
            
            if not corridor_stations:
                logger.warning("No compatible DC stations found after expanding search")
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
    
    async def _fetch_stations_google_first(self, hotspot: ChargeHotspot, search_radius_km: float = None) -> Tuple[List[Dict[str, Any]], str]:
        """
        Google Places öncelikli istasyon arama.
        🔧 V3.2: Hibrit sistem - Google'da kW yoksa OCM'den cross-reference.
        🔧 V3.3: Kademeli arama yarıçapı desteği.
        
        Args:
            hotspot: Şarj gerekli olan nokta
            search_radius_km: Arama yarıçapı (None ise varsayılan kullanılır)
        
        Returns:
            (stations_list, source) - source: "google" veya "ocm"
        """
        if search_radius_km is None:
            search_radius_km = max(self.corridor_length_km, self.corridor_width_km)
        search_radius_m = int(search_radius_km * 1000)
        
        # 1. Google Places API (New) - evChargeOptions ile gerçek güç bilgisi
        google_stations = []
        try:
            google_stations = await google_maps.search_ev_charging_stations_new(
                lat=hotspot.location.lat,
                lon=hotspot.location.lon,
                radius_m=search_radius_m,
                max_results=20  # API (New) max 20
            )
            
            if google_stations:
                logger.info(f"Google Places (New) returned {len(google_stations)} stations (radius={search_radius_km}km)")
            else:
                logger.info(f"Google Places (New) returned empty at {search_radius_km}km, falling back to OCM")
                
        except Exception as e:
            logger.warning(f"Google Places search failed: {e}")
        
        # 2. 🔧 V3.1: Hibrit - Sadece connector_count>0 olan ama kW=0 olan istasyonlar için OCM crossref
        # Bu sayede yanlış POI'lere (oto yıkama gibi) OCM'den güç yapıştırılmaz
        stations_need_power = [
            s for s in google_stations 
            if s.get("max_power_kw", 0) == 0 and s.get("connector_count", 0) > 0
        ]
        
        if google_stations and stations_need_power:
            logger.info(f"Hybrid mode: {len(stations_need_power)} verified EV stations need OCM power lookup")
            
            try:
                # OCM'den de istasyonları al
                ocm_stations = await ocm_service.get_nearby_stations_raw(
                    lat=hotspot.location.lat,
                    lon=hotspot.location.lon,
                    radius_km=search_radius_km
                )
                
                if ocm_stations:
                    # OCM istasyonlarından güç değerlerini çıkar (konum -> güç map)
                    ocm_power_map = self._build_ocm_power_map(ocm_stations)
                    
                    # Google istasyonlarını OCM ile zenginleştir (sadece connector_count>0 olanlar)
                    enriched_count = 0
                    for station in stations_need_power:
                        # Bu istasyonun konumuna yakın OCM istasyonu var mı?
                        lat = station.get("geometry", {}).get("location", {}).get("lat", 0)
                        lng = station.get("geometry", {}).get("location", {}).get("lng", 0)
                        
                        ocm_power = self._find_ocm_power_nearby(lat, lng, ocm_power_map)
                        if ocm_power > 0:
                            station["max_power_kw"] = ocm_power
                            station["_power_source"] = "ocm_crossref"
                            enriched_count += 1
                    
                    if enriched_count > 0:
                        logger.info(f"Hybrid: enriched {enriched_count} stations with OCM power data")
                        
            except Exception as e:
                logger.warning(f"OCM cross-reference failed: {e}")
        
        # 3. Google sonuçları varsa döndür
        if google_stations:
            return google_stations, "google"
        
        # 4. Google boşsa OCM fallback
        try:
            ocm_stations = await ocm_service.get_nearby_stations_raw(
                lat=hotspot.location.lat,
                lon=hotspot.location.lon,
                radius_km=search_radius_km
            )
            
            if ocm_stations:
                logger.info(f"OCM fallback returned {len(ocm_stations)} stations")
                return ocm_stations, "ocm"
                
        except Exception as e:
            logger.error(f"OCM API call also failed: {e}")
        
        return [], "none"
    
    def _build_ocm_power_map(self, ocm_stations: List[Dict[str, Any]]) -> List[Tuple[float, float, float]]:
        """
        OCM istasyonlarından (lat, lon, power_kw) listesi oluştur.
        """
        power_map = []
        for station in ocm_stations:
            address = station.get("AddressInfo", {})
            lat = address.get("Latitude", 0)
            lon = address.get("Longitude", 0)
            
            if not lat or not lon:
                continue
            
            # Max gücü bul
            connections = station.get("Connections", [])
            max_power = 0.0
            for conn in connections:
                power = conn.get("PowerKW") or 0
                if power > max_power:
                    max_power = float(power)
            
            if max_power > 0:
                power_map.append((lat, lon, max_power))
        
        return power_map
    
    def _find_ocm_power_nearby(self, lat: float, lng: float, ocm_power_map: List[Tuple[float, float, float]], threshold_km: float = 0.2) -> float:
        """
        Verilen konuma yakın (200m içinde) OCM istasyonunun gücünü bul.
        """
        for ocm_lat, ocm_lon, power in ocm_power_map:
            distance = haversine_km(lat, lng, ocm_lat, ocm_lon)
            if distance <= threshold_km:
                return power
        return 0.0

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
                # 🔧 V3.2: Feedback blok kontrolü
                place_id = station.get("place_id", "")
                if place_id and feedback_manager.is_station_blocked(place_id):
                    logger.debug(f"Station filtered (blocked by feedback): {station.get('name')}")
                    continue
                
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
                
                # 🔧 V3.1: Gerçek EV şarj istasyonu doğrulaması (connector_count / evChargeOptions)
                # Google Places (New) sonucunda connector_count veya max_power_kw yoksa bu POI şarj istasyonu değil
                connector_count = station.get("connector_count", 0)
                max_power = station.get("max_power_kw", 0)
                
                # Eğer hem connector_count hem max_power 0 ise, bu gerçek bir şarj istasyonu değil
                if connector_count == 0 and max_power == 0:
                    logger.debug(f"Station filtered (no EV charge data): {station.get('name')} - connector_count=0, max_power=0")
                    continue
                
                # 🔧 V3.3: Perpendicular distance filtresi kaldırıldı
                # Service alanları genellikle otobana dik bağlantı yollarıyla bağlı
                # Koridor genişliği (3km) zaten yeterli filtreleme sağlıyor
                
                # Rating al (Google doğrudan sağlar)
                rating = station.get("rating", 4.0)
                user_ratings_total = station.get("user_ratings_total", 0)
                
                # 🔧 V2.8: Google Places types'tan amenities çıkar
                place_types = station.get("types", [])
                place_name = station.get("name", "").lower()
                vicinity = station.get("vicinity", "").lower()
                
                # Amenities detection from types and name/vicinity
                has_parking = any(t in place_types for t in ["parking", "car_park"])
                has_food = any(t in place_types for t in ["restaurant", "food", "cafe", "meal_takeaway"])
                has_shopping = any(t in place_types for t in ["shopping_mall", "store", "convenience_store", "supermarket"])
                
                # Name/vicinity'den ek ipuçları
                has_toilet = any(kw in place_name or kw in vicinity for kw in ["wc", "tuvalet", "toilet", "restroom"])
                if not has_food:
                    has_food = any(kw in place_name or kw in vicinity for kw in ["restoran", "restaurant", "cafe", "kafe", "yemek"])
                if not has_shopping:
                    has_shopping = any(kw in place_name or kw in vicinity for kw in ["market", "avm", "mall", "shop"])
                if not has_parking:
                    has_parking = any(kw in place_name or kw in vicinity for kw in ["otopark", "parking", "park"])
                
                # Mola tesisi genelde her şeyi içerir
                is_rest_area = any(kw in place_name or kw in vicinity for kw in ["mola", "dinlenme", "rest area", "service area"])
                if is_rest_area:
                    has_toilet = True
                    has_food = True
                    has_parking = True
                
                # is_open_now (Google Places opening_hours'dan)
                opening_hours = station.get("opening_hours", {})
                is_open_now = opening_hours.get("open_now") if opening_hours else None
                
                # 🔧 V3.3: Google Places API (New) - evChargeOptions'dan gerçek güç bilgisi
                # Akıllı fallback: 50 kW sabit değer yerine tipik DC şarj gücü (120 kW)
                real_power_kw = station.get("max_power_kw", 0)
                estimated_power_kw = real_power_kw if real_power_kw > 0 else 120.0  # Akıllı fallback
                
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
                    "_place_id": station.get("place_id", ""),
                    # 🔧 V2.8: Amenities bilgileri
                    "_has_toilet": has_toilet,
                    "_has_food": has_food,
                    "_has_shopping": has_shopping,
                    "_has_parking": has_parking,
                    "_is_open_now": is_open_now
                }
                
                corridor_station = CorridorStation(
                    station_info=station_info,
                    distance_from_hotspot_km=round(distance, 2),
                    deviation_km=round(distance, 2),
                    power_kw=estimated_power_kw,
                    is_dc=True,  # Google EV charging genelde DC
                    is_compatible=True,
                    rating=rating,
                    user_ratings_total=user_ratings_total,
                    has_toilet=has_toilet,
                    has_food=has_food,
                    has_shopping=has_shopping,
                    has_parking=has_parking,
                    is_open_now=is_open_now
                )
                
                filtered_stations.append(corridor_station)
                
            except Exception as e:
                logger.warning(f"Failed to process Google station: {e}")
                continue
        
        # 🔧 V2.8: Skorlama (weighted rating + amenities dahil)
        max_power = max((s.power_kw for s in filtered_stations), default=50.0)
        for station in filtered_stations:
            deviation_minutes = (station.deviation_km / 50.0) * 60.0
            station.score = _calculate_station_score(
                deviation_minutes=deviation_minutes,
                power_kw=station.power_kw,
                max_power_kw=max_power,
                rating=station.rating,
                user_ratings_total=station.user_ratings_total,
                has_toilet=station.has_toilet,
                has_food=station.has_food,
                has_shopping=station.has_shopping,
                has_parking=station.has_parking,
                is_open_now=station.is_open_now
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
            # 🔧 V3.2: Feedback blok kontrolü (OCM)
            ocm_id = str(station.get("ID", ""))
            if ocm_id and feedback_manager.is_station_blocked(ocm_id):
                logger.debug(f"OCM station filtered (blocked by feedback): {station.get('AddressInfo', {}).get('Title')}")
                continue
            
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
            
            # 🔧 V2.9: Otoyol yön filtresi - yolun karşı tarafındaki istasyonları filtrele
            if hotspot.route_bearing > 0 and distance <= 5.0:
                is_valid, perp_dist = _is_station_on_route_side(
                    route_bearing=hotspot.route_bearing,
                    hotspot_lat=hotspot.location.lat,
                    hotspot_lon=hotspot.location.lon,
                    station_lat=station_lat,
                    station_lon=station_lon,
                    max_perpendicular_distance_km=1.5
                )
                if not is_valid:
                    logger.debug(f"OCM station filtered (wrong side): {address_info.get('Title')} - perp_dist={perp_dist:.2f}km")
                    continue
            
            # Rating
            user_comments = station.get("UserComments", [])
            rating = 4.0
            user_ratings_total = 0
            if user_comments:
                ratings = [c.get("Rating", 4) for c in user_comments if c.get("Rating")]
                if ratings:
                    rating = sum(ratings) / len(ratings)
                    user_ratings_total = len(ratings)
            
            # 🔧 V2.8: OCM GeneralComments'ten amenities çıkar
            general_comments = (station.get("GeneralComments") or "").lower()
            station_name = address_info.get("Title", "").lower()
            
            has_toilet = any(kw in general_comments or kw in station_name for kw in ["wc", "tuvalet", "toilet", "restroom"])
            has_food = any(kw in general_comments or kw in station_name for kw in ["restoran", "restaurant", "cafe", "kafe", "yemek", "food"])
            has_shopping = any(kw in general_comments or kw in station_name for kw in ["market", "avm", "mall", "shop", "mağaza"])
            has_parking = any(kw in general_comments or kw in station_name for kw in ["otopark", "parking", "park"])
            
            # Mola tesisi genelde her şeyi içerir
            is_rest_area = any(kw in general_comments or kw in station_name for kw in ["mola", "dinlenme", "rest area", "service area"])
            if is_rest_area:
                has_toilet = True
                has_food = True
                has_parking = True
            
            # is_24_7 kontrolü
            is_24_7 = any(kw in general_comments for kw in ["24/7", "24h", "24 hour", "24 saat"])
            
            corridor_station = CorridorStation(
                station_info=station,
                distance_from_hotspot_km=round(distance, 2),
                deviation_km=round(distance, 2),
                power_kw=power_kw,
                is_dc=power_kw >= DC_POWER_THRESHOLD_KW,
                is_compatible=is_compatible,
                rating=rating,
                user_ratings_total=user_ratings_total,
                has_toilet=has_toilet,
                has_food=has_food,
                has_shopping=has_shopping,
                has_parking=has_parking,
                is_open_now=True if is_24_7 else None  # 24/7 ise açık kabul et
            )
            
            filtered_stations.append(corridor_station)
        
        # 🔧 V2.8: Skorlama (weighted rating + amenities dahil)
        for station in filtered_stations:
            deviation_minutes = (station.deviation_km / 50.0) * 60.0
            station.score = _calculate_station_score(
                deviation_minutes=deviation_minutes,
                power_kw=station.power_kw,
                max_power_kw=max_power_in_batch,
                rating=station.rating,
                user_ratings_total=station.user_ratings_total,
                has_toilet=station.has_toilet,
                has_food=station.has_food,
                has_shopping=station.has_shopping,
                has_parking=station.has_parking,
                is_open_now=station.is_open_now
            )
        
        return filtered_stations
    
    def greedy_select(
        self,
        stations: List[CorridorStation],
        current_soc: float
    ) -> Optional[CorridorStation]:
        """
        🔧 V3.4: Greedy algoritma ile en iyi istasyonu seç.
        
        V3.4 Güncellemeleri:
        - Adaptive Power Scoring: Araç kapasitesine göre güç puanlama
        - Fallback: Araç bilgisi yoksa 350kW referans
        """
        if not stations:
            return None
        
        # SOC düşükse güce daha fazla ağırlık
        power_weight = GREEDY_WEIGHT_POWER
        deviation_weight = GREEDY_WEIGHT_DEVIATION
        amenities_weight = GREEDY_WEIGHT_AMENITIES
        rating_weight = GREEDY_WEIGHT_RATING
        popularity_weight = GREEDY_WEIGHT_POPULARITY
        
        if current_soc < 20.0:
            # Acil durum: güç en önemli, popülerlik hala önemli
            power_weight = 0.45
            deviation_weight = 0.15
            amenities_weight = 0.05
            rating_weight = 0.20
            popularity_weight = 0.15
        elif current_soc < 30.0:
            power_weight = 0.40
            deviation_weight = 0.20
            amenities_weight = 0.05
            rating_weight = 0.20
            popularity_weight = 0.15
        
        best_station = None
        best_greedy_score = -1
        
        # 🔧 V3.4: Adaptive Power Scoring için araç gücünü al
        try:
            vehicle_max_kw = self.vehicle.max_charge_power_kw if self.vehicle else None
        except AttributeError:
            vehicle_max_kw = None
        
        for station in stations:
            # 🔧 V3.4: Adaptive Power Score
            # Araç kapasitesi biliniyorsa: Efektif güç / Araç kapasitesi
            # Bilinmiyorsa: İstasyon gücü / 350kW (fallback)
            try:
                if vehicle_max_kw and vehicle_max_kw > 0:
                    effective_power = min(station.power_kw, vehicle_max_kw)
                    power_score = effective_power / vehicle_max_kw
                else:
                    power_score = station.power_kw / 350.0
            except (ZeroDivisionError, TypeError):
                power_score = station.power_kw / 350.0
                
            deviation_score = max(0, 1.0 - (station.deviation_km / self.corridor_length_km))
            
            # Weighted rating kullan
            weighted_rating = _calculate_weighted_rating(station.rating, station.user_ratings_total)
            rating_score = weighted_rating / 5.0
            
            # Amenities skoru
            amenities_score = _calculate_amenities_score(
                station.has_toilet,
                station.has_food,
                station.has_shopping,
                station.has_parking,
                station.is_open_now
            )
            
            # Popülerlik skoru
            popularity_score = _calculate_popularity_score(station.user_ratings_total)
            
            greedy_score = (
                power_weight * power_score +
                deviation_weight * deviation_score +
                rating_weight * rating_score +
                amenities_weight * amenities_score +
                popularity_weight * popularity_score
            )
            
            if greedy_score > best_greedy_score:
                best_greedy_score = greedy_score
                best_station = station
        
        if best_station:
            logger.info(
                "Greedy selection completed",
                station=best_station.station_name,
                power_kw=best_station.power_kw,
                deviation_km=best_station.deviation_km,
                score=round(best_greedy_score, 3)
            )
        
        return best_station


# =============================================================================
# V1.5 CONVENIENCE FUNCTIONS
# =============================================================================

async def find_stations_for_hotspots(
    hotspots: List[ChargeHotspot],
    vehicle_model_id: str,
    min_distance_between_stations_km: float = 50.0,
    preferences: Optional[Dict[str, Any]] = None
) -> List[CorridorSearchResult]:
    """
    Birden fazla hotspot için akıllı istasyon seçimi.
    
    Özellikler:
    - Aynı istasyonu tekrar seçmez
    - Birbirine çok yakın istasyonları önler
    - Her hotspot için alternatif istasyon bulur
    - 🔧 V3.1: Kullanıcı tercihlerine göre filtreleme
    
    Args:
        hotspots: Şarj gerekli noktalar
        vehicle_model_id: Araç modeli
        min_distance_between_stations_km: İstasyonlar arası minimum mesafe
        preferences: Kullanıcı tercihleri (max_detour_km, preferred_operators, vb.)
    """
    if not hotspots:
        return []
    
    # 🔧 V3.1: Preferences'dan max_detour_km al
    max_detour_km = CORRIDOR_LENGTH_KM  # Default: 50km
    if preferences and preferences.get("max_detour_km"):
        max_detour_km = min(preferences["max_detour_km"], CORRIDOR_LENGTH_KM)
    
    searcher = CorridorSearcher(
        vehicle_model_id=vehicle_model_id,
        corridor_length_km=max_detour_km
    )
    
    # Paralel arama yap (tüm istasyonları bul)
    tasks = [searcher.search_for_hotspot(hotspot) for hotspot in hotspots]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_results = []
    used_station_ids = set()
    last_station_location = None
    
    for i, result in enumerate(raw_results):
        if isinstance(result, Exception):
            import traceback
            logger.error(f"Hotspot {i+1} search failed: {result}\n{traceback.format_exception(type(result), result, result.__traceback__)}")
            # 🔧 V3.3: Exception durumunda boş result ekle (liste boyutu korunsun)
            empty_result = CorridorSearchResult(
                hotspot=hotspots[i],
                stations=[],
                best_station=None,
                total_found=0,
                dc_compatible=0
            )
            valid_results.append(empty_result)
            continue
        
        # Akıllı istasyon seçimi
        if result.stations:
            # Daha önce seçilen istasyonları filtrele
            available_stations = [
                s for s in result.stations 
                if s.station_id not in used_station_ids
            ]
            
            # 🔧 V3.1: Kullanıcı tercihlerine göre filtrele
            if preferences and available_stations:
                # Şarj tipi filtresi (HPC/DC/AC)
                charger_type = preferences.get("preferred_charger_type", "")
                if charger_type:
                    def matches_charger_type(station):
                        power = station.power_kw
                        if charger_type == "HPC":
                            return power >= 180  # HPC: 180+ kW
                        elif charger_type == "DC":
                            return 50 <= power < 180  # DC: 50-180 kW
                        elif charger_type == "AC":
                            return power < 50  # AC: 22 kW ve altı
                        return True
                    
                    filtered = [s for s in available_stations if matches_charger_type(s)]
                    if filtered:
                        available_stations = filtered
                        logger.info(f"Charger type filter applied: {len(filtered)} stations match {charger_type}")
                
                # Operatör filtresi (istasyon adında operatör adı aranır)
                pref_operators = preferences.get("preferred_operators", [])
                if pref_operators:
                    filtered = [
                        s for s in available_stations
                        if any(op.lower() in s.station_name.lower() for op in pref_operators)
                    ]
                    if filtered:  # Sonuç varsa uygula, yoksa tüm istasyonları koru
                        available_stations = filtered
                        logger.info(f"Operator filter applied: {len(filtered)} stations match {pref_operators}")
                
                # Zorunlu imkanlar filtresi (amenities_required)
                req_amenities = preferences.get("amenities_required", [])
                if req_amenities:
                    def has_required_amenities(station):
                        for amenity in req_amenities:
                            if amenity == "toilet" and not station.has_toilet:
                                return False
                            if amenity == "food" and not station.has_food:
                                return False
                            if amenity == "shopping" and not station.has_shopping:
                                return False
                            if amenity == "parking" and not station.has_parking:
                                return False
                        return True
                    
                    filtered = [s for s in available_stations if has_required_amenities(s)]
                    if filtered:  # Sonuç varsa uygula
                        available_stations = filtered
                        logger.info(f"Amenities filter applied: {len(filtered)} stations have {req_amenities}")
                    else:
                        # 🔧 V3.2: Uygun istasyon yoksa warning ekle (soft filter)
                        amenity_names = {"toilet": "tuvalet", "food": "yemek", "shopping": "market", "parking": "otopark"}
                        missing_amenities = [amenity_names.get(a, a) for a in req_amenities]
                        result.amenities_warning = f"⚠️ İstenen imkanlara ({', '.join(missing_amenities)}) sahip istasyon bulunamadı. En yakın istasyonlar gösteriliyor."
                        logger.warning(f"No stations found with required amenities {req_amenities}, showing all stations")
            
            # 🔧 V3.3: Akıllı mesafe filtresi - kademeli esnetme
            original_available = available_stations.copy()
            distance_filter_relaxed = False
            
            # Çok yakın istasyonları filtrele
            if last_station_location and available_stations:
                filtered_by_distance = [
                    s for s in available_stations
                    if haversine_km(
                        last_station_location.lat, last_station_location.lon,
                        s.location.lat, s.location.lon
                    ) >= min_distance_between_stations_km
                ]
                
                # Filtre sonrası istasyon kaldıysa kullan
                if filtered_by_distance:
                    available_stations = filtered_by_distance
                else:
                    # Filtre çok katı - yarı mesafe ile tekrar dene
                    half_distance = min_distance_between_stations_km / 2
                    filtered_half = [
                        s for s in available_stations
                        if haversine_km(
                            last_station_location.lat, last_station_location.lon,
                            s.location.lat, s.location.lon
                        ) >= half_distance
                    ]
                    
                    if filtered_half:
                        available_stations = filtered_half
                        distance_filter_relaxed = True
                        logger.warning(
                            f"Hotspot {i+1}: Distance filter relaxed from {min_distance_between_stations_km}km to {half_distance}km"
                        )
                    # Hala boşsa orijinal listeyi koru (sadece duplicate filtresi)
                    elif original_available:
                        available_stations = original_available
                        distance_filter_relaxed = True
                        logger.warning(
                            f"Hotspot {i+1}: Distance filter disabled - only duplicate filter active"
                        )
            
            # Sırala ve en iyiyi seç
            if available_stations:
                available_stations.sort(key=lambda s: s.score, reverse=True)
                result.best_station = available_stations[0]
                used_station_ids.add(result.best_station.station_id)
                last_station_location = result.best_station.location
                
                relaxed_note = " (distance filter relaxed)" if distance_filter_relaxed else ""
                logger.info(
                    f"Smart station selection: Hotspot {i+1} → {result.best_station.station_name} "
                    f"(avoided {len(result.stations) - len(available_stations)} duplicates){relaxed_note}"
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
