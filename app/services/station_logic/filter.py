"""
Station Filter - İstasyon Filtreleme Motoru
============================================

station_finder.py'den çıkarılmış filtreleme mantığı.
Connector uyumluluğu, güç kontrolü, mesafe ve amenity filtreleri.

Kullanım:
    from app.services.station_logic import StationFilter
    
    filter = StationFilter(vehicle_connector="CCS", min_power_kw=50)
    if filter.is_connector_compatible(connections):
        # İstasyonu dahil et
"""

import math
from typing import List, Dict, Any, Optional
from app.models import GeoPoint
from app.utils.logger import get_logger

logger = get_logger("StationFilter")


# =============================================================================
# FILTER CONSTANTS
# =============================================================================

# Mesafe sabitleri
MAX_HAVERSINE_DISTANCE_KM = 50.0
CORRIDOR_LENGTH_KM = 50.0
CORRIDOR_WIDTH_KM = 10.0

# Güç sabitleri
DC_POWER_THRESHOLD_KW = 40.0
MIN_DC_POWER_KW = 50.0


# =============================================================================
# GEO HELPER FUNCTIONS — app/utils/geo.py'den import
# =============================================================================

from app.utils.geo import haversine_km, calculate_bearing


# =============================================================================
# STATION FILTER CLASS
# =============================================================================

class StationFilter:
    """
    İstasyon filtreleme motoru.
    
    Connector uyumluluğu, güç kontrolü, mesafe ve amenity filtreleri.
    """
    
    def __init__(
        self,
        vehicle_connector: str = "CCS",
        min_power_kw: float = MIN_DC_POWER_KW,
        corridor_length_km: float = CORRIDOR_LENGTH_KM,
        corridor_width_km: float = CORRIDOR_WIDTH_KM
    ):
        self.vehicle_connector = vehicle_connector.lower()
        self.min_power_kw = min_power_kw
        self.corridor_length_km = corridor_length_km
        self.corridor_width_km = corridor_width_km
    
    def is_connector_compatible(self, connections: List[Dict[str, Any]]) -> bool:
        """
        İstasyonun bağlayıcı tipinin araçla uyumlu olup olmadığını kontrol et.
        
        Args:
            connections: OCM connection listesi
        
        Returns:
            True if compatible
        """
        for conn in connections:
            connection_type = conn.get("ConnectionType", {})
            connection_title = connection_type.get("Title", "")
            
            if connection_title and self.vehicle_connector in connection_title.lower():
                return True
        
        return False
    
    def get_max_power_kw(self, connections: List[Dict[str, Any]]) -> float:
        """İstasyonun maksimum şarj gücünü döndür."""
        max_power = 0.0
        
        for conn in connections:
            power_kw = conn.get("PowerKW") or 0
            if power_kw > max_power:
                max_power = float(power_kw)
        
        return max_power
    
    def is_power_sufficient(self, connections: List[Dict[str, Any]]) -> bool:
        """İstasyon gücünün yeterli olup olmadığını kontrol et."""
        return self.get_max_power_kw(connections) >= self.min_power_kw
    
    def is_within_corridor(
        self,
        station_lat: float,
        station_lon: float,
        hotspot_lat: float,
        hotspot_lon: float
    ) -> bool:
        """İstasyonun koridor içinde olup olmadığını kontrol et."""
        distance = haversine_km(hotspot_lat, hotspot_lon, station_lat, station_lon)
        return distance <= self.corridor_length_km
    
    def is_on_route_side(
        self,
        route_bearing: float,
        hotspot_lat: float,
        hotspot_lon: float,
        station_lat: float,
        station_lon: float,
        max_perpendicular_distance_km: float = 1.5
    ) -> tuple:
        """
        İstasyonun rota yönünde olup olmadığını kontrol et.
        Otoyolda karşı taraftaki istasyonları filtrelemek için kullanılır.
        
        Returns:
            (is_valid: bool, perpendicular_distance: float)
        """
        station_bearing = calculate_bearing(hotspot_lat, hotspot_lon, station_lat, station_lon)
        bearing_diff = abs(station_bearing - route_bearing)
        
        if bearing_diff > 180:
            bearing_diff = 360 - bearing_diff
        
        distance = haversine_km(hotspot_lat, hotspot_lon, station_lat, station_lon)
        perpendicular_distance = distance * math.sin(math.radians(bearing_diff))
        
        if perpendicular_distance > max_perpendicular_distance_km:
            return False, perpendicular_distance
        
        if bearing_diff > 90:
            return False, perpendicular_distance
        
        return True, perpendicular_distance
    
    def matches_charger_type(self, power_kw: float, charger_type: str) -> bool:
        """
        İstasyonun şarj tipi tercihine uyup uymadığını kontrol et.
        
        Args:
            power_kw: İstasyon gücü
            charger_type: "HPC", "DC", "AC" veya ""
        
        Returns:
            True if matches preference
        """
        if not charger_type:
            return True
        
        charger_type = charger_type.upper()
        
        if charger_type == "HPC":
            return power_kw >= 180
        elif charger_type == "DC":
            return power_kw >= 50
        elif charger_type == "AC":
            return power_kw < 50
        
        return True
    
    def has_required_amenities(
        self,
        required_amenities: List[str],
        has_toilet: bool = False,
        has_food: bool = False,
        has_shopping: bool = False,
        has_parking: bool = False
    ) -> bool:
        """
        İstasyonun zorunlu olanaklara sahip olup olmadığını kontrol et.
        
        Args:
            required_amenities: ["toilet", "food", "shopping", "parking"]
            has_*: İstasyon olanak durumları
        
        Returns:
            True if all required amenities are present
        """
        if not required_amenities:
            return True
        
        for amenity in required_amenities:
            amenity = amenity.lower()
            if amenity == "toilet" and not has_toilet:
                return False
            if amenity == "food" and not has_food:
                return False
            if amenity == "shopping" and not has_shopping:
                return False
            if amenity == "parking" and not has_parking:
                return False
        
        return True
    
    def is_operational(self, station: Dict[str, Any]) -> bool:
        """OCM istasyonunun operasyonel olup olmadığını kontrol et."""
        status_type = station.get("StatusType", {})
        return status_type.get("IsOperational", True)
    
    def is_google_operational(self, station: Dict[str, Any]) -> bool:
        """Google Places istasyonunun operasyonel olup olmadığını kontrol et."""
        business_status = station.get("business_status", "OPERATIONAL")
        return business_status in ("OPERATIONAL", None)
    
    def has_valid_ev_charge_data(self, station: Dict[str, Any]) -> bool:
        """
        Google Places istasyonunun gerçek EV şarj verisi olup olmadığını kontrol et.
        
        connector_count veya max_power_kw yoksa bu POI şarj istasyonu değil.
        """
        connector_count = station.get("connector_count", 0)
        max_power = station.get("max_power_kw", 0)
        return connector_count > 0 or max_power > 0


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

station_filter = StationFilter()
