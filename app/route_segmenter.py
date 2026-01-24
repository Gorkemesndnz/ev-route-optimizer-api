"""
Route Segmenter v2.0
=====================

V2.0: Sadece geometrik segmentasyon.
Tüketim hesabı MainCalculator'a, SOC simülasyonu SOCSimulator'a devredildi.

Görev:
- Google Polyline decode
- Rota segmentasyonu (her X km'de bir)
- Her segment için elevation dağılımı

Flow:
1. Polyline → Koordinat listesi
2. Koordinatlar → RouteSegment listesi (elevation ile)

Kullanım:
    from app.route_segmenter_v2 import RouteSegmenter
    
    segmenter = RouteSegmenter(segment_length_km=10.0)
    segments = segmenter.create_segments(polyline, elevation_gain, elevation_loss)
"""

import math
from typing import List, Tuple
from dataclasses import dataclass
from app.models import GeoPoint
from app.utils.logger import get_logger

logger = get_logger("route_segmenter")


# =============================================================================
# CONSTANTS
# =============================================================================

DEFAULT_SEGMENT_KM = 10.0  # Her 10km'de bir segment


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class RouteSegment:
    """
    Rota üzerindeki bir segment (V2.0 - Sadece geometri).
    
    Attributes:
        index: Segment sırası (0'dan başlar)
        start_point: Başlangıç koordinatı
        end_point: Bitiş koordinatı
        distance_km: Segment mesafesi
        cumulative_distance_km: Toplam kümülatif mesafe
        elevation_gain_m: Yükselme (metre)
        elevation_loss_m: İniş (metre)
    """
    index: int
    start_point: GeoPoint
    end_point: GeoPoint
    distance_km: float
    cumulative_distance_km: float = 0.0
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0


@dataclass
class WeatherCheckpoint:
    """
    V2.0: Hava durumu checkpoint'i (her 100km'de bir).
    
    Attributes:
        lat: Enlem
        lon: Boylam
        eta_minutes: Bu noktaya tahmini varış süresi (dakika)
        cumulative_km: Başlangıçtan itibaren mesafe (km)
    """
    lat: float
    lon: float
    eta_minutes: float
    cumulative_km: float


# =============================================================================
# POLYLINE DECODER
# =============================================================================

def decode_polyline(polyline_str: str) -> List[Tuple[float, float]]:
    """
    Google Encoded Polyline'ı koordinat listesine çevirir.
    
    Args:
        polyline_str: Google Directions API'den gelen encoded polyline
        
    Returns:
        List of (latitude, longitude) tuples
    """
    index = 0
    lat = 0
    lng = 0
    coordinates = []
    
    while index < len(polyline_str):
        # Latitude decode
        shift = 0
        result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if result & 1 else result >> 1
        lat += dlat
        
        # Longitude decode
        shift = 0
        result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if result & 1 else result >> 1
        lng += dlng
        
        coordinates.append((lat / 1e5, lng / 1e5))
    
    return coordinates


def calculate_distance_km(point1: Tuple[float, float], point2: Tuple[float, float]) -> float:
    """
    İki koordinat arası Haversine mesafesi (km).
    """
    lat1, lon1 = point1
    lat2, lon2 = point2
    
    R = 6371  # Dünya yarıçapı (km)
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_lat / 2) ** 2 + 
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


# =============================================================================
# ROUTE SEGMENTER CLASS (V2.0 - Sadece Geometri)
# =============================================================================

class RouteSegmenter:
    """
    V2.0: Sadece geometrik segmentasyon.
    
    Tüketim hesabı MainCalculator'a devredildi.
    SOC simülasyonu SOCSimulator'a devredildi.
    
    Görev:
    - Polyline'ı segmentlere böl
    - Her segment için elevation dağılımı yap
    
    Kullanım:
        segmenter = RouteSegmenter(segment_length_km=10.0)
        segments = segmenter.create_segments(polyline, elevation_gain, elevation_loss)
    """
    
    def __init__(self, segment_length_km: float = DEFAULT_SEGMENT_KM):
        """
        Args:
            segment_length_km: Her segment uzunluğu (default 10km)
        """
        self.segment_length_km = segment_length_km
        self.segments: List[RouteSegment] = []
        self.total_distance_km: float = 0.0
        
        logger.debug(f"RouteSegmenter V2.0 initialized, segment_length={segment_length_km}km")
    
    def create_segments(
        self,
        polyline: str,
        total_elevation_gain_m: float = 0.0,
        total_elevation_loss_m: float = 0.0
    ) -> List[RouteSegment]:
        """
        Polyline'dan segment listesi oluşturur.
        
        V2.0: Sadece geometrik segmentasyon + elevation dağılımı.
        Tüketim hesabı YAPILMAZ.
        
        Args:
            polyline: Google encoded polyline
            total_elevation_gain_m: Toplam yükselme (metre)
            total_elevation_loss_m: Toplam iniş (metre)
            
        Returns:
            RouteSegment listesi (elevation ile)
        """
        # 1️⃣ Polyline'ı decode et
        coordinates = decode_polyline(polyline)
        
        if len(coordinates) < 2:
            logger.warning("Polyline too short, returning empty segments")
            return []
        
        logger.info(f"Decoded polyline: {len(coordinates)} points")
        
        # 2️⃣ Koordinatları segmentlere ayır
        segments = []
        current_segment_distance = 0.0
        segment_start_idx = 0
        cumulative_distance = 0.0
        
        for i in range(1, len(coordinates)):
            point_distance = calculate_distance_km(coordinates[i-1], coordinates[i])
            current_segment_distance += point_distance
            cumulative_distance += point_distance
            
            # Segment tamamlandı mı?
            if current_segment_distance >= self.segment_length_km or i == len(coordinates) - 1:
                segment = RouteSegment(
                    index=len(segments),
                    start_point=GeoPoint(
                        lat=coordinates[segment_start_idx][0], 
                        lon=coordinates[segment_start_idx][1]
                    ),
                    end_point=GeoPoint(
                        lat=coordinates[i][0], 
                        lon=coordinates[i][1]
                    ),
                    distance_km=round(current_segment_distance, 2),
                    cumulative_distance_km=round(cumulative_distance, 2)
                )
                segments.append(segment)
                
                # Reset
                current_segment_distance = 0.0
                segment_start_idx = i
        
        self.total_distance_km = cumulative_distance
        
        # 3️⃣ Elevation dağılımı (orantılı)
        if total_elevation_gain_m > 0 or total_elevation_loss_m > 0:
            for segment in segments:
                ratio = segment.distance_km / self.total_distance_km if self.total_distance_km > 0 else 0
                segment.elevation_gain_m = round(total_elevation_gain_m * ratio, 1)
                segment.elevation_loss_m = round(total_elevation_loss_m * ratio, 1)
        
        self.segments = segments
        
        logger.info(
            f"Segments created: count={len(segments)}, "
            f"total_distance={round(self.total_distance_km, 1)}km"
        )
        
        return segments
    
    def get_segment_at_distance(self, distance_km: float) -> RouteSegment | None:
        """
        Belirli bir mesafedeki segmenti döndürür.
        
        Args:
            distance_km: Başlangıçtan itibaren mesafe
            
        Returns:
            O mesafedeki RouteSegment veya None
        """
        for segment in self.segments:
            if segment.cumulative_distance_km >= distance_km:
                return segment
        return self.segments[-1] if self.segments else None
    
    def get_coordinates_at_distance(self, distance_km: float) -> GeoPoint | None:
        """
        Belirli bir mesafedeki koordinatı döndürür (lineer interpolasyon).
        
        Args:
            distance_km: Başlangıçtan itibaren mesafe
            
        Returns:
            O mesafedeki GeoPoint veya None
        """
        segment = self.get_segment_at_distance(distance_km)
        if not segment:
            return None
        
        # Segment başlangıç mesafesi
        segment_start_km = segment.cumulative_distance_km - segment.distance_km
        
        # Segment içindeki pozisyon oranı (0.0 - 1.0)
        if segment.distance_km > 0:
            ratio = (distance_km - segment_start_km) / segment.distance_km
            ratio = max(0.0, min(1.0, ratio))  # Clamp [0, 1]
        else:
            ratio = 0.0
        
        # Lineer interpolasyon
        interpolated_lat = segment.start_point.lat + ratio * (segment.end_point.lat - segment.start_point.lat)
        interpolated_lon = segment.start_point.lon + ratio * (segment.end_point.lon - segment.start_point.lon)
        
        return GeoPoint(lat=interpolated_lat, lon=interpolated_lon)
    
    def create_weather_checkpoints(
        self,
        total_duration_minutes: float,
        checkpoint_interval_km: float = 100.0
    ) -> List[WeatherCheckpoint]:
        """
        V2.0: Her 100km'de bir hava durumu checkpoint'i oluşturur.
        
        KULLANIM: create_segments() çağrıldıktan SONRA çağrılmalıdır.
        
        Args:
            total_duration_minutes: Toplam rota süresi (dakika)
            checkpoint_interval_km: Checkpoint aralığı (default 100km)
            
        Returns:
            WeatherCheckpoint listesi (başlangıç + ara noktalar + varış)
        """
        if not self.segments:
            logger.warning("No segments available, run create_segments() first")
            return []
        
        checkpoints: List[WeatherCheckpoint] = []
        
        # Ortalama hız hesapla (ETA için)
        avg_speed_kmh = (self.total_distance_km / total_duration_minutes) * 60 if total_duration_minutes > 0 else 60.0
        
        # Başlangıç noktası (ETA = 0)
        start_point = self.segments[0].start_point
        checkpoints.append(WeatherCheckpoint(
            lat=start_point.lat,
            lon=start_point.lon,
            eta_minutes=0.0,
            cumulative_km=0.0
        ))
        
        # Ara checkpoint'ler (her 100km'de bir)
        current_km = checkpoint_interval_km
        while current_km < self.total_distance_km:
            coord = self.get_coordinates_at_distance(current_km)
            if coord:
                eta_minutes = (current_km / avg_speed_kmh) * 60
                checkpoints.append(WeatherCheckpoint(
                    lat=coord.lat,
                    lon=coord.lon,
                    eta_minutes=round(eta_minutes, 1),
                    cumulative_km=round(current_km, 1)
                ))
            current_km += checkpoint_interval_km
        
        # Varış noktası
        end_point = self.segments[-1].end_point
        checkpoints.append(WeatherCheckpoint(
            lat=end_point.lat,
            lon=end_point.lon,
            eta_minutes=round(total_duration_minutes, 1),
            cumulative_km=round(self.total_distance_km, 1)
        ))
        
        logger.info(
            f"Weather checkpoints created: count={len(checkpoints)}, "
            f"interval={checkpoint_interval_km}km, total_distance={round(self.total_distance_km, 1)}km"
        )
        
        return checkpoints


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_route_segments(
    polyline: str,
    total_elevation_gain_m: float = 0.0,
    total_elevation_loss_m: float = 0.0,
    segment_length_km: float = DEFAULT_SEGMENT_KM
) -> List[RouteSegment]:
    """
    Convenience function: Tek satırda segment oluştur.
    
    Args:
        polyline: Google encoded polyline
        total_elevation_gain_m: Toplam yükselme
        total_elevation_loss_m: Toplam iniş
        segment_length_km: Segment uzunluğu
        
    Returns:
        RouteSegment listesi
    """
    segmenter = RouteSegmenter(segment_length_km=segment_length_km)
    return segmenter.create_segments(
        polyline=polyline,
        total_elevation_gain_m=total_elevation_gain_m,
        total_elevation_loss_m=total_elevation_loss_m
    )
