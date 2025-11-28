"""
Route Segmenter v1.5
=====================

V1.5 Dinamik Şarj Planlama için rota segmentasyonu ve SOC takibi.

Özellikler:
- Google Polyline decode
- Rota segmentasyonu (her X km'de bir)
- Segment bazlı SOC hesaplama
- Şarj gerekli noktaların (Hotspot) tespiti
- Dinamik eşik hesaplama (varış hedefine göre)

Flow:
1. Polyline → Koordinat listesi
2. Koordinatlar → RouteSegment listesi
3. Her segment için consumption hesapla
4. SOC takibi yap
5. Şarj gerekli noktaları tespit et

Kullanım:
    from app.route_segmenter import RouteSegmenter
    
    segmenter = RouteSegmenter(vehicle, start_soc=80.0, target_arrival_soc=30.0)
    segments = segmenter.create_segments_from_polyline(polyline)
    hotspots = segmenter.find_charge_hotspots()
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from app.models import GeoPoint
from app.consumption_engine.vehicle_models import VehicleModel
from app.utils.logger import get_logger

logger = get_logger("route_segmenter")


# =============================================================================
# CONSTANTS
# =============================================================================

DEFAULT_SEGMENT_KM = 10.0  # Her 10km'de bir segment
SAFETY_BUFFER_PERCENT = 10.0  # Güvenlik marjı
MIN_CHARGE_THRESHOLD_PERCENT = 15.0  # Minimum şarj seviyesi


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class RouteSegment:
    """
    Rota üzerindeki bir segment.
    
    Attributes:
        index: Segment sırası (0'dan başlar)
        start_point: Başlangıç koordinatı
        end_point: Bitiş koordinatı
        distance_km: Segment mesafesi
        cumulative_distance_km: Toplam kümülatif mesafe
        elevation_gain_m: Yükselme (metre)
        elevation_loss_m: İniş (metre)
        soc_at_start: Segment başındaki batarya (%)
        soc_at_end: Segment sonundaki batarya (%)
        consumption_kwh: Bu segmentte harcanan enerji
        is_charge_needed: Bu segmentten sonra şarj gerekli mi?
    """
    index: int
    start_point: GeoPoint
    end_point: GeoPoint
    distance_km: float
    cumulative_distance_km: float = 0.0
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    soc_at_start: float = 100.0
    soc_at_end: float = 100.0
    consumption_kwh: float = 0.0
    is_charge_needed: bool = False


@dataclass
class ChargeHotspot:
    """
    Şarj gerekli olan nokta.
    
    Attributes:
        segment_index: Hangi segment sonrası
        location: Şarj gerekli koordinat
        soc_at_point: O noktadaki batarya (%)
        remaining_distance_km: Varışa kalan mesafe
        min_required_soc: Devam için gereken minimum batarya
        recommended_charge_to: Önerilen şarj hedefi (%)
    """
    segment_index: int
    location: GeoPoint
    soc_at_point: float
    remaining_distance_km: float
    min_required_soc: float
    recommended_charge_to: float = 80.0


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
        
    Reference:
        https://developers.google.com/maps/documentation/utilities/polylinealgorithm
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
    
    Args:
        point1: (lat, lon) tuple
        point2: (lat, lon) tuple
        
    Returns:
        Mesafe (km)
    """
    import math
    
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
# ROUTE SEGMENTER CLASS
# =============================================================================

class RouteSegmenter:
    """
    V1.5 Rota Segmentasyon ve SOC Takip Motoru.
    
    Kullanım:
        segmenter = RouteSegmenter(vehicle, start_soc=80.0, target_arrival_soc=30.0)
        segments = segmenter.create_segments_from_polyline(polyline)
        hotspots = segmenter.find_charge_hotspots()
    """
    
    def __init__(
        self,
        vehicle: VehicleModel,
        start_soc: float = 100.0,
        target_arrival_soc: float = 20.0,
        charge_min_soc: float = 20.0,
        charge_target_soc: float = 80.0,
        segment_length_km: float = DEFAULT_SEGMENT_KM
    ):
        """
        Args:
            vehicle: Araç modeli
            start_soc: Başlangıç batarya yüzdesi
            target_arrival_soc: Varışta hedef batarya yüzdesi
            charge_min_soc: Şarj istasyonuna varış eşiği (bu %'e düşünce şarj et)
            charge_target_soc: Şarj istasyonunda hedef % (max 100)
            segment_length_km: Her segment uzunluğu
        """
        self.vehicle = vehicle
        self.start_soc = start_soc
        self.target_arrival_soc = target_arrival_soc
        self.charge_min_soc = charge_min_soc
        self.charge_target_soc = min(100.0, charge_target_soc)  # Max %100
        self.segment_length_km = segment_length_km
        
        self.segments: List[RouteSegment] = []
        self.total_distance_km: float = 0.0
        self.total_consumption_kwh: float = 0.0
        
        # Araç özellikleri
        self.battery_capacity_kwh = vehicle.battery_capacity_kwh
        self.base_consumption_wh_km = getattr(vehicle, 'base_consumption_wh_km', 180.0)
        
        logger.info(
            "RouteSegmenter initialized",
            vehicle=vehicle.model_name,
            battery_kwh=self.battery_capacity_kwh,
            start_soc=start_soc,
            target_arrival_soc=target_arrival_soc
        )
    
    def create_segments_from_polyline(
        self,
        polyline: str,
        total_elevation_gain_m: float = 0.0,
        total_elevation_loss_m: float = 0.0
    ) -> List[RouteSegment]:
        """
        Polyline'dan segment listesi oluşturur ve SOC hesaplar.
        
        Args:
            polyline: Google encoded polyline
            total_elevation_gain_m: Toplam yükselme (metre)
            total_elevation_loss_m: Toplam iniş (metre)
            
        Returns:
            RouteSegment listesi
        """
        # 1️⃣ Polyline'ı decode et
        coordinates = decode_polyline(polyline)
        
        if len(coordinates) < 2:
            logger.warning("Polyline too short, returning empty segments")
            return []
        
        logger.info(f"Decoded polyline with {len(coordinates)} points")
        
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
                    start_point=GeoPoint(lat=coordinates[segment_start_idx][0], lon=coordinates[segment_start_idx][1]),
                    end_point=GeoPoint(lat=coordinates[i][0], lon=coordinates[i][1]),
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
                ratio = segment.distance_km / self.total_distance_km
                segment.elevation_gain_m = round(total_elevation_gain_m * ratio, 1)
                segment.elevation_loss_m = round(total_elevation_loss_m * ratio, 1)
        
        # 4️⃣ SOC hesapla
        self._calculate_soc_for_segments(segments)
        
        self.segments = segments
        
        logger.info(
            "Segments created",
            segment_count=len(segments),
            total_distance_km=round(self.total_distance_km, 1),
            total_consumption_kwh=round(self.total_consumption_kwh, 2)
        )
        
        return segments
    
    def _calculate_soc_for_segments(self, segments: List[RouteSegment]) -> None:
        """
        Her segment için SOC hesapla.
        
        Forward simulation: İlk segmentten başla, her segment için
        tüketimi hesapla ve kalan SOC'u takip et.
        """
        current_soc = self.start_soc
        total_consumption = 0.0
        
        for segment in segments:
            # Segment başlangıç SOC
            segment.soc_at_start = round(current_soc, 1)
            
            # Bu segment için tüketim hesapla
            consumption_kwh = self._estimate_segment_consumption(segment)
            segment.consumption_kwh = round(consumption_kwh, 3)
            total_consumption += consumption_kwh
            
            # SOC düşüşü hesapla
            soc_drop = (consumption_kwh / self.battery_capacity_kwh) * 100
            current_soc = max(0, current_soc - soc_drop)
            segment.soc_at_end = round(current_soc, 1)
            
            # Şarj gerekli mi kontrol et (dinamik eşik)
            remaining_distance = self.total_distance_km - segment.cumulative_distance_km
            min_required = self._calculate_min_required_soc(remaining_distance)
            
            if current_soc < min_required:
                segment.is_charge_needed = True
                logger.debug(
                    f"Charge needed at segment {segment.index}",
                    soc=round(current_soc, 1),
                    min_required=round(min_required, 1),
                    remaining_km=round(remaining_distance, 1)
                )
        
        self.total_consumption_kwh = total_consumption
    
    def _estimate_segment_consumption(self, segment: RouteSegment) -> float:
        """
        Segment için tüketim tahmini (kWh).
        
        Basitleştirilmiş hesaplama:
        - Baz tüketim (Wh/km → kWh)
        - Elevation faktörü
        """
        # Baz tüketim
        base_kwh = (self.base_consumption_wh_km / 1000.0) * segment.distance_km
        
        # Elevation etkisi (basit fizik)
        # Tırmanış: +enerji, iniş: -enerji (regen)
        gravity = 9.81
        vehicle_mass = getattr(self.vehicle, 'curb_weight_kg', 1700)
        regen_efficiency = 0.6
        joule_to_kwh = 3_600_000
        
        uphill_kwh = (vehicle_mass * gravity * segment.elevation_gain_m) / joule_to_kwh
        downhill_kwh = (vehicle_mass * gravity * segment.elevation_loss_m * regen_efficiency) / joule_to_kwh
        
        elevation_kwh = uphill_kwh - downhill_kwh
        
        total_kwh = base_kwh + elevation_kwh
        
        return max(0.0, total_kwh)
    
    def _calculate_min_required_soc(self, remaining_distance_km: float) -> float:
        """
        Kalan mesafe için gereken minimum SOC hesapla.
        
        Formül:
        min_required = target_arrival_soc + safety_buffer + remaining_consumption_percent
        """
        # Kalan mesafe için tahmini tüketim
        remaining_consumption_kwh = (self.base_consumption_wh_km / 1000.0) * remaining_distance_km
        remaining_consumption_percent = (remaining_consumption_kwh / self.battery_capacity_kwh) * 100
        
        min_required = (
            self.target_arrival_soc + 
            SAFETY_BUFFER_PERCENT + 
            remaining_consumption_percent
        )
        
        return min(100.0, max(MIN_CHARGE_THRESHOLD_PERCENT, min_required))
    
    def find_charge_hotspots(self) -> List[ChargeHotspot]:
        """
        Şarj gerekli noktaları (hotspots) tespit et.
        
        V1.5: Şarj sonrası SOC simülasyonu ile gerçekçi hotspot tespiti.
        Bir hotspot'ta şarj yapıldığını varsayarak sonraki hotspot'ları hesapla.
        
        Returns:
            ChargeHotspot listesi
        """
        hotspots = []
        simulated_soc = self.start_soc  # Simüle edilen SOC
        last_hotspot_km = 0.0  # Son hotspot mesafesi
        min_distance_between_stops = 100.0  # Min 100km aralık
        
        # V1.5: Kullanıcının belirlediği şarj eşikleri
        user_charge_threshold = self.charge_min_soc  # Bu %'e düşünce şarj et
        user_charge_target = self.charge_target_soc  # Bu %'e kadar şarj et (max 100)
        
        logger.info(
            f"Hotspot detection started",
            user_threshold=user_charge_threshold,
            user_target=user_charge_target,
            start_soc=self.start_soc
        )
        
        for segment in self.segments:
            # Simüle edilen SOC ile segment tüketimini hesapla
            consumption_percent = (segment.consumption_kwh / self.battery_capacity_kwh) * 100
            simulated_soc = max(0, simulated_soc - consumption_percent)
            
            # Kalan mesafe
            remaining_distance = self.total_distance_km - segment.cumulative_distance_km
            
            # V1.5 FIX: SADECE kullanıcının belirlediği eşiğe düşünce şarj et
            # Emergency: Sadece batarya %10'un altına düşerse (güvenlik için)
            emergency_charge = simulated_soc <= 10.0
            
            # Kullanıcı eşiği: simulated_soc kullanıcının belirlediği değere düştüyse
            charge_needed = simulated_soc <= user_charge_threshold or emergency_charge
            
            # Şarj gerekli mi? (Ve son hotspot'tan yeterli mesafe var mı?)
            distance_since_last = segment.cumulative_distance_km - last_hotspot_km
            
            if charge_needed and distance_since_last >= min_distance_between_stops:
                # V1.5 FIX: Kullanıcının hedefini kullan, max %100
                recommended_charge = min(100.0, user_charge_target)
                
                # Kalan mesafe için gereken minimum SOC
                min_to_finish = (remaining_distance * self.base_consumption_wh_km / 1000) / self.battery_capacity_kwh * 100
                
                hotspot = ChargeHotspot(
                    segment_index=segment.index,
                    location=segment.end_point,
                    soc_at_point=round(simulated_soc, 1),
                    remaining_distance_km=round(remaining_distance, 1),
                    min_required_soc=round(min_to_finish + self.target_arrival_soc, 1),
                    recommended_charge_to=round(recommended_charge, 1)
                )
                hotspots.append(hotspot)
                
                # Şarj yapıldığını varsay - SOC'u güncelle
                simulated_soc = recommended_charge
                last_hotspot_km = segment.cumulative_distance_km
                
                logger.info(
                    f"Charge hotspot found",
                    segment=segment.index,
                    arrival_soc=hotspot.soc_at_point,
                    target_soc=hotspot.recommended_charge_to,
                    emergency=emergency_charge
                )
        
        logger.info(f"Total hotspots found: {len(hotspots)}")
        return hotspots
    
    def get_route_summary(self) -> dict:
        """
        Rota özeti döndür.
        """
        return {
            "total_distance_km": round(self.total_distance_km, 1),
            "total_consumption_kwh": round(self.total_consumption_kwh, 2),
            "segment_count": len(self.segments),
            "start_soc": self.start_soc,
            "estimated_arrival_soc": self.segments[-1].soc_at_end if self.segments else self.start_soc,
            "target_arrival_soc": self.target_arrival_soc,
            "charge_stops_needed": sum(1 for s in self.segments if s.is_charge_needed),
            "vehicle": self.vehicle.model_name
        }


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_route_segments(
    polyline: str,
    vehicle: VehicleModel,
    start_soc: float,
    target_arrival_soc: float,
    elevation_gain_m: float = 0.0,
    elevation_loss_m: float = 0.0
) -> Tuple[List[RouteSegment], List[ChargeHotspot], dict]:
    """
    Convenience function: Polyline'dan segmentler ve hotspotlar oluştur.
    
    Args:
        polyline: Google encoded polyline
        vehicle: Araç modeli
        start_soc: Başlangıç SOC (%)
        target_arrival_soc: Hedef varış SOC (%)
        elevation_gain_m: Toplam yükselme
        elevation_loss_m: Toplam iniş
        
    Returns:
        (segments, hotspots, summary) tuple
    """
    segmenter = RouteSegmenter(
        vehicle=vehicle,
        start_soc=start_soc,
        target_arrival_soc=target_arrival_soc
    )
    
    segments = segmenter.create_segments_from_polyline(
        polyline=polyline,
        total_elevation_gain_m=elevation_gain_m,
        total_elevation_loss_m=elevation_loss_m
    )
    
    hotspots = segmenter.find_charge_hotspots()
    summary = segmenter.get_route_summary()
    
    return segments, hotspots, summary
