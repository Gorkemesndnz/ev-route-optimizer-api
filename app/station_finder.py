"""
Station Finder
===============

Google Places öncelikli, OCM fallback'li EV şarj istasyonu arama modülü.

Özellikler:
- Google Places API ile EV şarj istasyonu arama (birincil)
- OCM API fallback (Google boş dönerse)
- Connector uyumluluğu kontrolü
- DC/AC istasyon ayrımı (≥40 kW DC, altı AC)
- Haversine + koridor + polyline-perpendicular mesafe filtreleme
- Hotspot bazlı akıllı istasyon seçimi
- Greedy station selection
- Ağırlıklı skorlama sistemi (StationScorer)

Flow:
1. Google Places'tan EV istasyonlarını ara
2. Google boş dönerse → OCM'ye fallback
3. Operasyonel ve uyumlu olanları filtrele
4. DC/AC ayrımı yap
5. Haversine/Koridor/Polyline mesafe filtreleme
6. Ağırlıklı skorlama ile en iyiyi seç

Kullanım:
    from app.station_finder import (
        find_best_station_for_hotspot,  # Tek hotspot için
        find_stations_for_hotspots,     # Çoklu hotspot
        CorridorSearcher                # Arama sınıfı
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
from app.infrastructure.station_catalog import (
    DATA_CONFIDENCE_PENALTY_UNKNOWN_AVAILABILITY,
    DATA_CONFIDENCE_PENALTY_UNKNOWN_POWER,
    UNKNOWN_POWER_PLANNING_KW,
    AvailabilityStatus,
    parse_google_place,
    parse_ocm_station,
)
from app.infrastructure.vehicle_catalog import get_vehicle_model, VehicleSpec as VehicleModel
from app.utils.config_manager import config
from app.utils.logger import get_logger
from app.utils.charging_estimator import estimate_dc_charging_power
from app.services.feedback_service import feedback_manager
from app.services.station_logic import StationScorer, StationFilter
from app.utils.geo import haversine_km
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

# Koridor sabitleri
CORRIDOR_LENGTH_KM = 50.0
CORRIDOR_WIDTH_KM = 10.0
MIN_DC_POWER_KW = 50.0
MAX_STATIONS_PER_HOTSPOT = 5
MAX_REJECT_AUDIT_SAMPLES_PER_REASON = 3

# Kademeli arama yarıçapları (istasyon bulunamazsa genişlet)
SEARCH_RADII_KM = [50, 80, 120]  # km - 3 kademeli arama

# Skorlama ve filtreleme sabitleri station_logic/ altına taşındı.
# Import: from app.services.station_logic.scorer import WEIGHT_*, AMENITY_*, etc.


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class CorridorStation:
    """Koridor içinde bulunan istasyon (amenities + weighted rating destekli).

    Sprint 5: Provider-neutral normalization alanlarını taşır (power_known,
    availability_status, source_provider, source_id, available_count, ...).
    Bu alanlar StationInfo response'una downstream'de aktarılır.
    """
    station_info: Dict[str, Any]
    distance_from_hotspot_km: float
    deviation_km: float = 0.0
    power_kw: float = 0.0
    is_dc: bool = False
    is_compatible: bool = True
    rating: float = 4.0
    user_ratings_total: int = 0  # Weighted rating için
    score: float = 0.0
    # Amenities alanları
    has_toilet: bool = False
    has_food: bool = False
    has_shopping: bool = False
    has_parking: bool = False
    is_open_now: Optional[bool] = None
    # Sprint 5 — Provider-neutral normalization alanları
    power_known: bool = True
    availability_status: Optional[str] = None  # 'available' | 'unavailable' | 'unknown'
    available_count: Optional[int] = None
    out_of_service_count: Optional[int] = None
    availability_last_update_time: Optional[str] = None
    source_provider: Optional[str] = None  # 'google' | 'ocm'
    source_id: Optional[str] = None
    
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
    weather_forecast: Optional[Dict[str, Any]] = None  # İstasyon için forecast
    amenities_warning: Optional[str] = None  # Zorunlu imkan bulunamadı uyarısı
    distance_warning: Optional[str] = None  # Min mesafe filtresi gevşetildi uyarısı
    reject_audit: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
# Polyline perpendicular distance filter:
# Refactor 2 Stage 2 (2026-05-06) ile app/services/station_logic/polyline_filter.py
# altina tasindi. Geriye donuk uyumluluk icin yerel isimler import ediliyor.

from app.services.station_logic.polyline_filter import (  # noqa: E402
    PERP_DISTANCE_THRESHOLD_KM,
    RELAXED_PERP_DISTANCE_THRESHOLDS_KM,
    decode_route_polyline_coords as _decode_route_polyline_coords,
    min_distance_to_polyline_km as _min_distance_to_polyline_km,
    check_stations_on_polyline as _check_stations_on_polyline,
    check_stations_on_polyline_with_relaxed_fallback as _check_stations_on_polyline_with_relaxed_fallback,
)


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


# Refactor 2 Stage 1 (2026-05-05): _calculate_weighted_rating, _calculate_amenities_score,
# _calculate_popularity_score, _calculate_station_score modul-level fonksiyonlari kaldirildi.
# StationScorer (app/services/station_logic/scorer.py) icindeki ayni metodlar kullaniliyor.
# Modul-level singleton: _scorer; metodlari tum sinif disinda da call edilebilir.
_scorer = StationScorer()


def _new_reject_audit(source_provider: str) -> Dict[str, Any]:
    return {
        "source_provider": source_provider,
        "total_rejected": 0,
        "by_reason": {},
    }


def _safe_reject_sample(source_provider: str, station: Dict[str, Any], reason_code: str, **extra) -> Dict[str, Any]:
    if source_provider == "google":
        source_id = station.get("place_id") or station.get("id") or ""
        display_name = station.get("name")
        if not display_name and isinstance(station.get("displayName"), dict):
            display_name = station["displayName"].get("text")
    else:
        source_id = str(station.get("ID", "") or "")
        display_name = (station.get("AddressInfo", {}) or {}).get("Title")
    sample = {
        "source_provider": source_provider,
        "source_id": str(source_id),
        "reason_code": reason_code,
    }
    if display_name:
        sample["name_hash"] = str(abs(hash(str(display_name))) % 10_000_000)
    for key, value in extra.items():
        if value is not None:
            sample[key] = value
    return sample


def _record_reject(audit: Dict[str, Any], reason_code: str, sample: Dict[str, Any]) -> None:
    audit["total_rejected"] = int(audit.get("total_rejected", 0)) + 1
    by_reason = audit.setdefault("by_reason", {})
    payload = by_reason.setdefault(reason_code, {"count": 0, "samples": []})
    payload["count"] = int(payload.get("count", 0)) + 1
    samples = payload.setdefault("samples", [])
    if len(samples) < MAX_REJECT_AUDIT_SAMPLES_PER_REASON:
        samples.append(sample)


def _merge_reject_audit(target: Dict[str, Any], source: Dict[str, Any], *, radius_km: float) -> Dict[str, Any]:
    if not target:
        target = _new_reject_audit(source.get("source_provider", "unknown") if source else "unknown")
    if not source:
        return target
    target["total_rejected"] = int(target.get("total_rejected", 0)) + int(source.get("total_rejected", 0))
    by_reason = target.setdefault("by_reason", {})
    for reason_code, payload in (source.get("by_reason", {}) or {}).items():
        existing = by_reason.setdefault(reason_code, {"count": 0, "samples": []})
        existing["count"] = int(existing.get("count", 0)) + int(payload.get("count", 0))
        samples = existing.setdefault("samples", [])
        for sample in payload.get("samples", []) or []:
            if len(samples) >= MAX_REJECT_AUDIT_SAMPLES_PER_REASON:
                break
            sample = dict(sample)
            sample["search_radius_km"] = radius_km
            samples.append(sample)
    return target


# =============================================================================
# CORRIDOR SEARCHER CLASS
# =============================================================================

class CorridorSearcher:
    """
    Koridor bazlı istasyon arama motoru.

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
        min_dc_power_kw: float = MIN_DC_POWER_KW,
        vehicle_spec = None  # Zaten çözülmüş VehicleSpec (MSSQL'den geliyorsa)
    ):
        self.vehicle = vehicle_spec if vehicle_spec else get_vehicle_model(vehicle_model_id)
        self.vehicle_model_id = vehicle_model_id
        self.corridor_length_km = corridor_length_km
        self.corridor_width_km = corridor_width_km
        self.min_dc_power_kw = min_dc_power_kw
        self._last_reject_audit: Dict[str, Any] = {}
        
        logger.info(
            "CorridorSearcher initialized",
            vehicle=self.vehicle.display_name,
            connector=self.vehicle.connector_type,
            corridor_length=corridor_length_km
        )
    
    async def search_for_hotspot(self, hotspot: ChargeHotspot) -> CorridorSearchResult:
        """
        Google Places öncelikli + OCM fallback istasyon arama.

        Args:
            hotspot: Şarj gerekli olan nokta

        Returns:
            CorridorSearchResult: Bulunan istasyonlar ve seçilen
        """
        logger.info(
            f"Searching stations for hotspot at {hotspot.location.lat:.4f}, {hotspot.location.lon:.4f} "
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
            # Kademeli arama yarıçapı - istasyon bulunamazsa genişlet
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
                        corridor_stations = await self._filter_and_score_google_stations(
                            raw_stations,
                            hotspot,
                            max_distance_km=radius,
                        )
                    else:
                        corridor_stations = await self._filter_and_score_stations(
                            raw_stations,
                            hotspot,
                            max_distance_km=radius,
                        )
                    result.reject_audit = _merge_reject_audit(
                        result.reject_audit,
                        self._last_reject_audit,
                        radius_km=radius,
                    )
                    
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
            if result.best_station and used_radius > self.corridor_length_km:
                result.distance_warning = (
                    f"⚠️ Bu şarj durağı için istasyon arama yarıçapı "
                    f"{self.corridor_length_km:.0f}km → {used_radius:.0f}km genişletildi. "
                    f"Rota tercihleri nedeniyle istasyon ana koridordan daha uzakta olabilir."
                )
            
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

        - Hibrit sistem: Google'da kW yoksa OCM'den cross-reference.
        - Kademeli arama yarıçapı desteklenir.

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
        
        # 2. Hibrit - Sadece connector_count>0 olan ama kW=0 olan istasyonlar için OCM crossref
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
    
    async def _filter_and_score_google_stations(
        self,
        google_stations: List[Dict[str, Any]],
        hotspot: ChargeHotspot,
        max_distance_km: Optional[float] = None,
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
        # Ön filtre: operasyonel + konumlu + EV verisine sahip istasyonları topla
        pre_filtered = []
        reject_audit = _new_reject_audit("google")
        rej_blocked = rej_status = rej_geo = rej_far = rej_no_ev = rej_ac = 0
        for station in google_stations:
            place_id = station.get("place_id", "")
            if place_id and feedback_manager.is_station_blocked(place_id):
                rej_blocked += 1
                _record_reject(reject_audit, "blocked_by_feedback", _safe_reject_sample("google", station, "blocked_by_feedback"))
                continue
            business_status = station.get("business_status", "OPERATIONAL")
            if business_status not in ("OPERATIONAL", None):
                rej_status += 1
                _record_reject(reject_audit, "non_operational", _safe_reject_sample("google", station, "non_operational"))
                continue
            geometry = station.get("geometry", {})
            location = geometry.get("location", {})
            station_lat = location.get("lat", 0)
            station_lng = location.get("lng", 0)
            if not station_lat or not station_lng:
                rej_geo += 1
                _record_reject(reject_audit, "invalid_geometry", _safe_reject_sample("google", station, "invalid_geometry"))
                continue
            distance = haversine_km(
                hotspot.location.lat, hotspot.location.lon,
                station_lat, station_lng
            )
            effective_max_distance_km = self.corridor_length_km if max_distance_km is None else max_distance_km
            if distance > effective_max_distance_km:
                rej_far += 1
                _record_reject(
                    reject_audit,
                    "too_far_from_route",
                    _safe_reject_sample("google", station, "too_far_from_route", distance_km=round(distance, 2)),
                )
                continue
            connector_count = station.get("connector_count", 0)
            max_power = station.get("max_power_kw", 0)
            station_types = station.get("types", []) or []
            # Google Places New bazen Türkiye için connector_count=0, max_power=0 dönüyor.
            # types içinde 'electric_vehicle_charging_station' varsa yine de kabul et.
            is_ev_by_type = "electric_vehicle_charging_station" in station_types
            if connector_count == 0 and max_power == 0 and not is_ev_by_type:
                rej_no_ev += 1
                _record_reject(reject_audit, "no_ev_charge_data", _safe_reject_sample("google", station, "no_ev_charge_data"))
                logger.debug(f"Station filtered (no EV charge data, no EV type): {station.get('name')}")
                continue
            # Google istasyonlarda DC/AC kontrolü.
            # Gerçek max_power_kw biliniyorsa ve DC eşiğinin (40 kW) altındaysa AC kabul et ve REDDET.
            # Sadece max_power=0 (Google bilgi vermedi) durumunda fallback 120 kW DC olarak devam et.
            if max_power > 0 and max_power < DC_POWER_THRESHOLD_KW:
                rej_ac += 1
                _record_reject(
                    reject_audit,
                    "below_min_power",
                    _safe_reject_sample("google", station, "below_min_power", power_kw=float(max_power)),
                )
                logger.debug(
                    f"Google station filtered (AC charger, "
                    f"power={max_power}kW < DC_THRESHOLD={DC_POWER_THRESHOLD_KW}kW): "
                    f"{station.get('name')}"
                )
                continue
            pre_filtered.append((station, station_lat, station_lng, distance))

        if google_stations and not pre_filtered:
            logger.warning(
                f"Pre-filter eliminated all {len(google_stations)} Google stations | "
                f"blocked={rej_blocked}, status={rej_status}, no_geo={rej_geo}, "
                f"too_far={rej_far}, no_ev_data={rej_no_ev}, ac_charger={rej_ac}"
            )

        # Polyline-perpendicular filter (Roads API yerine, geometrik)
        coords = [(lat, lng) for _, lat, lng, _ in pre_filtered]
        on_route_flags, corridor_threshold_km = _check_stations_on_polyline_with_relaxed_fallback(
            coords,
            hotspot.route_polyline_coords,
        )
        if (
            corridor_threshold_km is not None
            and corridor_threshold_km > PERP_DISTANCE_THRESHOLD_KM
            and any(on_route_flags)
        ):
            logger.warning(
                "Google station route corridor relaxed: "
                f"{PERP_DISTANCE_THRESHOLD_KM}km -> {corridor_threshold_km}km "
                f"(thresholds={RELAXED_PERP_DISTANCE_THRESHOLDS_KM})"
            )

        filtered_stations = []
        for (station, station_lat, station_lng, distance), on_route in zip(pre_filtered, on_route_flags):
            if not on_route:
                _record_reject(
                    reject_audit,
                    "too_far_from_route",
                    _safe_reject_sample("google", station, "too_far_from_route", distance_km=round(distance, 2)),
                )
                logger.debug(
                    "Google station filtered "
                    f"(off-route, perp > {corridor_threshold_km or PERP_DISTANCE_THRESHOLD_KM}km): "
                    f"{station.get('name')}"
                )
                continue
            try:

                # Rating al (Google doğrudan sağlar)
                rating = station.get("rating", 4.0)
                user_ratings_total = station.get("user_ratings_total", 0)

                # Google Places types'tan amenities çıkar
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
                is_rest_area = any(kw in place_name or kw in vicinity for kw in [
                    "mola", "dinlenme", "rest area", "service area",
                    "highway", "tesis", "hızlı şarj istasyonu"
                ])
                if is_rest_area:
                    has_toilet = True
                    has_food = True
                    has_parking = True
                    has_shopping = True  # Highway tesisleri genellikle AVM/market içerir
                
                # is_open_now (Google Places opening_hours'dan)
                opening_hours = station.get("opening_hours", {})
                is_open_now = opening_hours.get("open_now") if opening_hours else None

                # Sprint 5: Google raw'u NormalizedStation'a indirgey (provider-neutral)
                # power_known=False ise UNKNOWN_POWER_PLANNING_KW (=50 kW) konservatif
                # planlama gücü kullanılır; istasyon adayda kalır ama düşük güven cezası alır.
                # 120 kW iyimser fallback artık YOK — kontrat Sprint 5'te kilitlendi.
                normalized = parse_google_place(station)
                real_power_kw = station.get("max_power_kw", 0)
                power_known = bool(normalized.power_known) or (real_power_kw > 0)
                estimated_power_kw = (
                    float(real_power_kw) if real_power_kw > 0
                    else normalized.planning_power_kw  # = UNKNOWN_POWER_PLANNING_KW (50)
                )
                
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
                    # Amenities bilgileri
                    "_has_toilet": has_toilet,
                    "_has_food": has_food,
                    "_has_shopping": has_shopping,
                    "_has_parking": has_parking,
                    "_is_open_now": is_open_now
                }
                
                # is_dc gerçek güçten türetiliyor (≥40 kW DC, altı AC).
                # Pre-filter zaten max_power>0 ve <40 kW olanları reddetti, yani burada ya
                # estimated >= 40 kW (gerçek DC) ya da fallback 120 kW (DC varsayımı).
                is_dc_charger = estimated_power_kw >= DC_POWER_THRESHOLD_KW

                corridor_station = CorridorStation(
                    station_info=station_info,
                    distance_from_hotspot_km=round(distance, 2),
                    deviation_km=round(distance, 2),
                    power_kw=estimated_power_kw,
                    is_dc=is_dc_charger,
                    is_compatible=True,
                    rating=rating,
                    user_ratings_total=user_ratings_total,
                    has_toilet=has_toilet,
                    has_food=has_food,
                    has_shopping=has_shopping,
                    has_parking=has_parking,
                    is_open_now=is_open_now,
                    # Sprint 5 normalization alanları
                    power_known=power_known,
                    availability_status=normalized.availability_status.value,
                    available_count=normalized.total_available_count,
                    out_of_service_count=normalized.total_out_of_service_count,
                    availability_last_update_time=normalized.availability_last_update_time,
                    source_provider=normalized.source_provider,
                    source_id=normalized.source_id or station.get("place_id", ""),
                )

                filtered_stations.append(corridor_station)

            except Exception as e:
                logger.warning(f"Failed to process Google station: {e}")
                continue
        
        # Skorlama (weighted rating + amenities dahil) — StationScorer kullaniyor
        max_power = max((s.power_kw for s in filtered_stations), default=50.0)
        for station in filtered_stations:
            deviation_minutes = (station.deviation_km / 50.0) * 60.0
            base_score = _scorer.calculate_score(
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
            # Sprint 5: Düşük güven cezası — unknown kW ve unknown availability
            confidence_multiplier = 1.0
            if not station.power_known:
                confidence_multiplier *= (1.0 - DATA_CONFIDENCE_PENALTY_UNKNOWN_POWER)
            if station.availability_status == AvailabilityStatus.UNKNOWN.value:
                confidence_multiplier *= (1.0 - DATA_CONFIDENCE_PENALTY_UNKNOWN_AVAILABILITY)
            station.score = base_score * confidence_multiplier

        logger.info(f"Filtered {len(filtered_stations)} Google stations (of {len(google_stations)} total)")
        self._last_reject_audit = reject_audit
        return filtered_stations
    
    async def _filter_and_score_stations(
        self,
        raw_stations: List[Dict[str, Any]],
        hotspot: ChargeHotspot,
        max_distance_km: Optional[float] = None,
    ) -> List[CorridorStation]:
        """İstasyonları filtrele ve skorla."""
        # Ön filtre: temel kriterler
        pre_filtered = []
        reject_audit = _new_reject_audit("ocm")
        max_power_in_batch = 0.0

        for station in raw_stations:
            ocm_id = str(station.get("ID", ""))
            if ocm_id and feedback_manager.is_station_blocked(ocm_id):
                _record_reject(reject_audit, "blocked_by_feedback", _safe_reject_sample("ocm", station, "blocked_by_feedback"))
                continue
            status_type = station.get("StatusType", {})
            if not status_type.get("IsOperational", True):
                _record_reject(reject_audit, "out_of_service", _safe_reject_sample("ocm", station, "out_of_service"))
                continue
            connections = station.get("Connections", [])
            if not connections:
                _record_reject(reject_audit, "no_ev_charge_data", _safe_reject_sample("ocm", station, "no_ev_charge_data"))
                continue
            power_kw = _get_max_power_kw(connections)
            if power_kw < self.min_dc_power_kw:
                _record_reject(
                    reject_audit,
                    "below_min_power",
                    _safe_reject_sample("ocm", station, "below_min_power", power_kw=float(power_kw)),
                )
                continue
            is_compatible = _is_connector_compatible(connections, self.vehicle.connector_type)
            if not is_compatible:
                _record_reject(reject_audit, "incompatible_connector", _safe_reject_sample("ocm", station, "incompatible_connector"))
                continue
            address_info = station.get("AddressInfo", {})
            station_lat = address_info.get("Latitude", 0)
            station_lon = address_info.get("Longitude", 0)
            if not station_lat or not station_lon:
                _record_reject(reject_audit, "invalid_geometry", _safe_reject_sample("ocm", station, "invalid_geometry"))
                continue
            distance = haversine_km(
                hotspot.location.lat, hotspot.location.lon,
                station_lat, station_lon
            )
            effective_max_distance_km = self.corridor_length_km if max_distance_km is None else max_distance_km
            if distance > effective_max_distance_km:
                _record_reject(
                    reject_audit,
                    "too_far_from_route",
                    _safe_reject_sample("ocm", station, "too_far_from_route", distance_km=round(distance, 2)),
                )
                continue
            max_power_in_batch = max(max_power_in_batch, power_kw)
            pre_filtered.append((station, station_lat, station_lon, distance, power_kw, is_compatible))

        # Polyline-perpendicular filter (Roads API yerine, geometrik)
        coords = [(lat, lon) for _, lat, lon, _, _, _ in pre_filtered]
        on_route_flags, corridor_threshold_km = _check_stations_on_polyline_with_relaxed_fallback(
            coords,
            hotspot.route_polyline_coords,
        )
        if (
            corridor_threshold_km is not None
            and corridor_threshold_km > PERP_DISTANCE_THRESHOLD_KM
            and any(on_route_flags)
        ):
            logger.warning(
                "OCM station route corridor relaxed: "
                f"{PERP_DISTANCE_THRESHOLD_KM}km -> {corridor_threshold_km}km "
                f"(thresholds={RELAXED_PERP_DISTANCE_THRESHOLDS_KM})"
            )

        filtered_stations = []
        for (station, station_lat, station_lon, distance, power_kw, is_compatible), on_route in zip(pre_filtered, on_route_flags):
            address_info = station.get("AddressInfo", {})
            if not on_route:
                _record_reject(
                    reject_audit,
                    "too_far_from_route",
                    _safe_reject_sample("ocm", station, "too_far_from_route", distance_km=round(distance, 2)),
                )
                logger.debug(
                    "OCM station filtered "
                    f"(off-route, perp > {corridor_threshold_km or PERP_DISTANCE_THRESHOLD_KM}km): "
                    f"{address_info.get('Title')}"
                )
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
            
            # OCM GeneralComments'ten amenities çıkar
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
            
            # Sprint 5: OCM raw'u NormalizedStation'a indirgey (provider-neutral)
            normalized_ocm = parse_ocm_station(station)
            ocm_avail = (
                normalized_ocm.availability_status.value
                if normalized_ocm is not None
                else AvailabilityStatus.UNKNOWN.value
            )
            ocm_source_id = normalized_ocm.source_id if normalized_ocm else str(station.get("ID", ""))

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
                is_open_now=True if is_24_7 else None,  # 24/7 ise açık kabul et
                # Sprint 5 normalization alanları (OCM kaynaklı istasyon)
                power_known=power_kw > 0,
                availability_status=ocm_avail,
                available_count=normalized_ocm.total_available_count if normalized_ocm else None,
                out_of_service_count=normalized_ocm.total_out_of_service_count if normalized_ocm else None,
                availability_last_update_time=None,
                source_provider="ocm",
                source_id=ocm_source_id,
            )

            filtered_stations.append(corridor_station)
        
        # Skorlama (weighted rating + amenities dahil) — StationScorer kullaniyor
        for station in filtered_stations:
            deviation_minutes = (station.deviation_km / 50.0) * 60.0
            base_score = _scorer.calculate_score(
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
            # Sprint 5: Düşük güven cezası — OCM tarafı için de uygulanır
            confidence_multiplier = 1.0
            if not station.power_known:
                confidence_multiplier *= (1.0 - DATA_CONFIDENCE_PENALTY_UNKNOWN_POWER)
            if station.availability_status == AvailabilityStatus.UNKNOWN.value:
                confidence_multiplier *= (1.0 - DATA_CONFIDENCE_PENALTY_UNKNOWN_AVAILABILITY)
            station.score = base_score * confidence_multiplier

        self._last_reject_audit = reject_audit
        return filtered_stations
    
    def greedy_select(
        self,
        stations: List[CorridorStation],
        current_soc: float
    ) -> Optional[CorridorStation]:
        """
        Greedy algoritma ile en iyi istasyonu seç.

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
        
        # Adaptive Power Scoring için araç gücünü al
        try:
            vehicle_max_kw = self.vehicle.max_charge_power_kw if self.vehicle else None
        except AttributeError:
            vehicle_max_kw = None

        for station in stations:
            # Adaptive Power Score
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
            
            # Weighted rating kullan — StationScorer
            weighted_rating = _scorer.calculate_weighted_rating(station.rating, station.user_ratings_total)
            rating_score = weighted_rating / 5.0

            # Amenities skoru
            amenities_score = _scorer.calculate_amenities_score(
                station.has_toilet,
                station.has_food,
                station.has_shopping,
                station.has_parking,
                station.is_open_now
            )

            # Popülerlik skoru
            popularity_score = _scorer.calculate_popularity_score(station.user_ratings_total)
            
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
# CONVENIENCE FUNCTIONS
# =============================================================================

async def find_stations_for_hotspots(
    hotspots: List[ChargeHotspot],
    vehicle_model_id: str,
    min_distance_between_stations_km: float = 50.0,
    preferences: Optional[Dict[str, Any]] = None,
    vehicle_spec = None,  # Zaten çözülmüş VehicleSpec (MSSQL'den geliyorsa)
    route_polyline: Optional[str] = None,  # Rota polyline (Roads API snap için)
) -> List[CorridorSearchResult]:
    """
    Birden fazla hotspot için akıllı istasyon seçimi.

    Özellikler:
    - Aynı istasyonu tekrar seçmez
    - Birbirine çok yakın istasyonları önler
    - Her hotspot için alternatif istasyon bulur
    - Kullanıcı tercihlerine göre filtreleme
    - Polyline-perpendicular filter (Roads API'nin yerini aldı)

    Args:
        hotspots: Şarj gerekli noktalar
        vehicle_model_id: Araç modeli
        min_distance_between_stations_km: İstasyonlar arası minimum mesafe
        preferences: Kullanıcı tercihleri (max_detour_km, preferred_operators, vb.)
        vehicle_spec: Zaten çözülmüş araç spesifikasyonu (MSSQL'den geliyorsa)
        route_polyline: Google encoded polyline (perpendicular distance filter için)
    """
    if not hotspots:
        return []

    # Polyline'ı bir kere decode et, tüm hotspot'lar paylaşır.
    # Roads API çağrısı yok — pure geometrik (haversine vertex distance).
    all_hotspots_bypass = all(getattr(hotspot, "bypass_perp_filter", False) for hotspot in hotspots)
    polyline_coords: List[Tuple[float, float]] = [] if all_hotspots_bypass else _decode_route_polyline_coords(route_polyline or "")
    if polyline_coords:
        logger.info(
            f"Polyline decoded: {len(polyline_coords)} sampled points "
            f"(perp threshold = {PERP_DISTANCE_THRESHOLD_KM} km)"
        )
    elif all_hotspots_bypass:
        logger.info("Polyline corridor filter bypassed for low-SOC origin hotspots")

    # Her hotspot'a polyline coords ata (filtre fonksiyonları buradan okur).
    # Aynı liste referansı paylaşılıyor — read-only kullanıldığı için güvenli.
    # bypass_perp_filter=True olan hotspot'lar (low-SOC origin) için boş liste ver
    # → perp filter fail-open davranır, şehir içi istasyonlar kabul edilir.
    for hotspot in hotspots:
        if getattr(hotspot, "bypass_perp_filter", False):
            hotspot.route_polyline_coords = None
        else:
            hotspot.route_polyline_coords = polyline_coords

    # Preferences'dan max_detour_km al
    max_detour_km = CORRIDOR_LENGTH_KM  # Default: 50km
    if preferences and preferences.get("max_detour_km"):
        max_detour_km = min(preferences["max_detour_km"], CORRIDOR_LENGTH_KM)

    searcher = CorridorSearcher(
        vehicle_model_id=vehicle_model_id,
        corridor_length_km=max_detour_km,
        vehicle_spec=vehicle_spec
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
            # Exception durumunda boş result ekle (liste boyutu korunsun)
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
            
            # Kullanıcı tercihlerine göre filtrele
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
                            if amenity in ("toilet", "rest_area") and not station.has_toilet:
                                return False
                            if amenity in ("food", "cafe", "restaurant", "rest_area") and not station.has_food:
                                return False
                            if amenity in ("shopping", "supermarket") and not station.has_shopping:
                                return False
                            if amenity in ("parking", "rest_area") and not station.has_parking:
                                return False
                            # hotel ve gas_station için katı filtreleme yapmıyoruz çünkü mevcut has_* booleam map'inde yok, 
                            # ama arama algoritmamız restoran/market vb barındıran yerleri de puanlıyor
                        return True
                    
                    filtered = [s for s in available_stations if has_required_amenities(s)]
                    if filtered:  # Sonuç varsa uygula
                        available_stations = filtered
                        logger.info(f"Amenities filter applied: {len(filtered)} stations have {req_amenities}")
                    else:
                        # Uygun istasyon yoksa warning ekle (soft filter fallback)
                        amenity_names = {
                            "toilet": "tuvalet", 
                            "food": "yiyecek", 
                            "shopping": "market", 
                            "parking": "otopark",
                            "hotel": "otel",
                            "cafe": "kafe/kahve",
                            "restaurant": "restoran",
                            "rest_area": "dinlenme tesisi",
                            "supermarket": "süpermarket",
                            "gas_station": "akaryakıt istasyonu"
                        }
                        missing_amenities = [amenity_names.get(a, a) for a in req_amenities]
                        result.amenities_warning = f"⚠️ İstenen imkanlara ({', '.join(missing_amenities)}) sahip istasyon bulunamadı. En yakın istasyonlar gösteriliyor."
                        logger.warning(f"No stations found with required amenities {req_amenities}, showing all stations")
            
            # Akıllı mesafe filtresi - kademeli esnetme
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
                        # Kullanıcıya da bildir (sadece log değil)
                        result.distance_warning = (
                            f"⚠️ {i+1}. şarj durağı için minimum mesafe filtresi gevşetildi "
                            f"({min_distance_between_stations_km:.0f}km → {half_distance:.0f}km). "
                            f"Bu durağı bir öncekine yakın bulabilirsiniz."
                        )
                        logger.warning(
                            f"Hotspot {i+1}: Distance filter relaxed from {min_distance_between_stations_km}km to {half_distance}km"
                        )
                    # Hala boşsa orijinal listeyi koru (sadece duplicate filtresi)
                    elif original_available:
                        available_stations = original_available
                        distance_filter_relaxed = True
                        # Mesafe filtresi tamamen kapalı — kullanıcı bilsin
                        result.distance_warning = (
                            f"⚠️ {i+1}. şarj durağı için uygun aralıklı istasyon bulunamadı; "
                            f"mesafe filtresi devre dışı. Bu durak bir öncekine çok yakın olabilir, "
                            f"alternatif istasyonları kontrol edin."
                        )
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


# Refactor 2 Stage 3 (2026-05-06): Legacy fonksiyonlar (find_best_station,
# find_charging_station) kaldirildi. Tum cagrilar CorridorSearcher / find_best_station_for_hotspot
# uzerinden yapilmalidir.
