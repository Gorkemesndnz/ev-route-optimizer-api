"""
SOC Simulator v2.0
===================

V2.0: MainCalculator sonuçlarını kullanarak SOC simülasyonu ve Hotspot tespiti.

Görev:
- Segment tüketimlerini kullanarak SOC takibi
- Şarj gerekli noktaların (Hotspot) tespiti
- Dinamik eşik hesaplama (varış hedefine göre)

Flow:
1. Segments (with consumption) → SOC simulation
2. SOC simulation → Hotspot detection
3. Hotspots → Station search için koordinatlar

Kullanım:
    from app.soc_simulator import SOCSimulator
    
    simulator = SOCSimulator(
        battery_capacity_kwh=50.8,
        start_soc=80.0,
        target_arrival_soc=20.0,
        charge_min_soc=20.0,
        charge_target_soc=80.0
    )
    
    hotspots = simulator.simulate_and_find_hotspots(segments_with_consumption)
"""

from typing import List, Optional
from dataclasses import dataclass
from app.models import GeoPoint
from app.route_segmenter import RouteSegment
from app.utils.logger import get_logger

logger = get_logger("soc_simulator")


# =============================================================================
# CONSTANTS
# =============================================================================

SAFETY_BUFFER_PERCENT = 10.0  # Güvenlik marjı
MIN_CHARGE_THRESHOLD_PERCENT = 15.0  # Minimum şarj seviyesi
MIN_DISTANCE_BETWEEN_STOPS_KM = 50.0  # Şarj durakları arası minimum mesafe


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SegmentWithConsumption:
    """
    Tüketim hesaplanmış segment.
    MainCalculator'dan gelen sonuç.
    """
    segment: RouteSegment
    consumption_kwh: float
    soc_at_start: float = 0.0  # Simülasyon sonrası doldurulur
    soc_at_end: float = 0.0    # Simülasyon sonrası doldurulur


@dataclass
class ChargeHotspot:
    """
    Şarj gerekli olan nokta.
    
    Attributes:
        segment_index: Hangi segment sonrası
        location: Şarj gerekli koordinat
        soc_at_point: O noktadaki batarya (%)
        distance_from_start_km: Başlangıçtan mesafe
        remaining_distance_km: Varışa kalan mesafe
        min_required_soc: Devam için gereken minimum batarya
        recommended_charge_to: Önerilen şarj hedefi (%)
    """
    segment_index: int
    location: GeoPoint
    soc_at_point: float
    distance_from_start_km: float
    remaining_distance_km: float
    min_required_soc: float
    recommended_charge_to: float = 80.0


@dataclass
class SimulationResult:
    """
    SOC simülasyon sonucu.
    """
    segments: List[SegmentWithConsumption]
    hotspots: List[ChargeHotspot]
    final_soc: float
    total_consumption_kwh: float
    can_complete_without_charging: bool


# =============================================================================
# SOC SIMULATOR CLASS
# =============================================================================

class SOCSimulator:
    """
    V2.0: SOC Simülasyon ve Hotspot Tespit Motoru.
    
    Tüketim hesabı MainCalculator'da yapılır.
    Bu sınıf sadece SOC takibi ve hotspot tespiti yapar.
    
    Kullanım:
        simulator = SOCSimulator(battery_kwh=50.8, start_soc=80.0)
        result = simulator.simulate(segments_with_consumption)
        hotspots = result.hotspots
    """
    
    def __init__(
        self,
        battery_capacity_kwh: float,
        start_soc: float = 100.0,
        target_arrival_soc: float = 20.0,
        charge_min_soc: float = 20.0,
        charge_target_soc: float = 80.0
    ):
        """
        Args:
            battery_capacity_kwh: Batarya kapasitesi (kWh)
            start_soc: Başlangıç batarya yüzdesi
            target_arrival_soc: Varışta hedef batarya yüzdesi
            charge_min_soc: Şarj eşiği - bu %'e düşünce şarj et
            charge_target_soc: Şarj hedefi - bu %'e kadar şarj et
        """
        self.battery_capacity_kwh = battery_capacity_kwh
        self.start_soc = start_soc
        self.target_arrival_soc = target_arrival_soc
        self.charge_min_soc = charge_min_soc
        self.charge_target_soc = min(100.0, charge_target_soc)
        
        logger.info(
            "SOCSimulator initialized",
            battery_kwh=battery_capacity_kwh,
            start_soc=start_soc,
            target_arrival_soc=target_arrival_soc,
            charge_min_soc=charge_min_soc,
            charge_target_soc=charge_target_soc
        )
    
    def simulate(
        self,
        segments_with_consumption: List[SegmentWithConsumption],
        total_distance_km: float
    ) -> SimulationResult:
        """
        SOC simülasyonu yap ve hotspot tespit et.
        
        Args:
            segments_with_consumption: MainCalculator'dan gelen tüketimli segmentler
            total_distance_km: Toplam rota mesafesi
            
        Returns:
            SimulationResult: Simülasyon sonucu + hotspotlar
        """
        current_soc = self.start_soc
        hotspots = []
        total_consumption = 0.0
        last_hotspot_km = 0.0
        
        # MainCalculator'dan gelen GERÇEK ortalama tüketimi hesapla
        route_total_consumption = sum(s.consumption_kwh for s in segments_with_consumption)
        avg_consumption_per_km = route_total_consumption / total_distance_km if total_distance_km > 0 else 0.20
        
        logger.info(
            f"SOC simulation started: {len(segments_with_consumption)} segments, "
            f"start_soc={self.start_soc}%, "
            f"avg_consumption={avg_consumption_per_km:.3f} kWh/km (MainCalculator)"
        )
        
        for seg_with_cons in segments_with_consumption:
            segment = seg_with_cons.segment
            consumption_kwh = seg_with_cons.consumption_kwh
            
            # SOC başlangıç değeri
            seg_with_cons.soc_at_start = current_soc
            
            # SOC düşüşü hesapla
            soc_drop = (consumption_kwh / self.battery_capacity_kwh) * 100
            current_soc = max(0, current_soc - soc_drop)
            total_consumption += consumption_kwh
            
            # SOC bitiş değeri
            seg_with_cons.soc_at_end = current_soc
            
            # Kalan mesafe
            remaining_distance = total_distance_km - segment.cumulative_distance_km
            
            # Minimum gerekli SOC hesapla (GERÇEK tüketim değeriyle)
            min_required_soc = self._calculate_min_required_soc(remaining_distance, avg_consumption_per_km)
            
            # Hotspot kontrolü
            should_create_hotspot = self._should_create_hotspot(
                current_soc=current_soc,
                min_required_soc=min_required_soc,
                cumulative_km=segment.cumulative_distance_km,
                last_hotspot_km=last_hotspot_km
            )
            
            if should_create_hotspot:
                hotspot = ChargeHotspot(
                    segment_index=segment.index,
                    location=segment.end_point,
                    soc_at_point=round(current_soc, 1),
                    distance_from_start_km=segment.cumulative_distance_km,
                    remaining_distance_km=round(remaining_distance, 1),
                    min_required_soc=round(min_required_soc, 1),
                    recommended_charge_to=self.charge_target_soc
                )
                hotspots.append(hotspot)
                last_hotspot_km = segment.cumulative_distance_km
                
                # Şarj sonrası SOC'u simüle et (bir sonraki segmente devam için)
                current_soc = self.charge_target_soc
                
                logger.info(
                    f"Hotspot detected at segment {segment.index}",
                    soc_before_charge=hotspot.soc_at_point,
                    soc_after_charge=current_soc,
                    distance_km=segment.cumulative_distance_km
                )
        
        # Sonuç
        can_complete = len(hotspots) == 0 or current_soc >= self.target_arrival_soc
        
        result = SimulationResult(
            segments=segments_with_consumption,
            hotspots=hotspots,
            final_soc=round(current_soc, 1),
            total_consumption_kwh=round(total_consumption, 2),
            can_complete_without_charging=len(hotspots) == 0
        )
        
        logger.info(
            f"SOC simulation completed: "
            f"hotspots={len(hotspots)}, "
            f"final_soc={result.final_soc}%, "
            f"total_consumption={result.total_consumption_kwh}kWh"
        )
        
        return result
    
    def _calculate_min_required_soc(self, remaining_distance_km: float, avg_consumption_per_km: float) -> float:
        """
        Kalan mesafe için gereken minimum SOC hesapla.
        
        Args:
            remaining_distance_km: Kalan mesafe (km)
            avg_consumption_per_km: MainCalculator'dan gelen GERÇEK ortalama tüketim (kWh/km)
        
        Mantık:
        - Varışa yetecek kadar SOC var mı kontrol et
        - Yoksa şarj gerekli
        """
        # Kalan mesafe için gereken enerji (GERÇEK tüketim değeriyle)
        required_kwh = avg_consumption_per_km * remaining_distance_km
        required_percent = (required_kwh / self.battery_capacity_kwh) * 100
        
        # Varışa ulaşmak için gereken minimum SOC
        # = kalan mesafe tüketimi + varış hedefi + güvenlik
        min_required = self.target_arrival_soc + required_percent + SAFETY_BUFFER_PERCENT
        
        # Eğer 100%'ü aşıyorsa, ara şarj kaçınılmaz
        # Bu durumda sadece charge_min_soc + güvenlik döndür
        if min_required > 100.0:
            return self.charge_min_soc + SAFETY_BUFFER_PERCENT  # 30%
        
        return max(MIN_CHARGE_THRESHOLD_PERCENT, min_required)
    
    def _should_create_hotspot(
        self,
        current_soc: float,
        min_required_soc: float,
        cumulative_km: float,
        last_hotspot_km: float
    ) -> bool:
        """
        Bu noktada hotspot oluşturulmalı mı?
        
        Koşullar:
        1. SOC, charge_min_soc'un altına düştü
        2. VEYA SOC, min_required_soc'un altına düştü
        3. VE son hotspot'tan yeterli mesafe var
        """
        # Son hotspot'tan mesafe kontrolü
        distance_since_last = cumulative_km - last_hotspot_km
        if distance_since_last < MIN_DISTANCE_BETWEEN_STOPS_KM and last_hotspot_km > 0:
            return False
        
        # Kullanıcı eşiği
        if current_soc <= self.charge_min_soc:
            return True
        
        # Dinamik eşik (varışa yetmeyecek)
        if current_soc < min_required_soc:
            return True
        
        # Acil durum (çok düşük SOC)
        if current_soc <= MIN_CHARGE_THRESHOLD_PERCENT:
            return True
        
        return False


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def simulate_route_soc(
    segments_with_consumption: List[SegmentWithConsumption],
    total_distance_km: float,
    battery_capacity_kwh: float,
    start_soc: float = 100.0,
    target_arrival_soc: float = 20.0,
    charge_min_soc: float = 20.0,
    charge_target_soc: float = 80.0
) -> SimulationResult:
    """
    Convenience function: Tek satırda SOC simülasyonu.
    
    Args:
        segments_with_consumption: Tüketimli segmentler
        total_distance_km: Toplam mesafe
        battery_capacity_kwh: Batarya kapasitesi
        start_soc: Başlangıç SOC
        target_arrival_soc: Varış hedef SOC
        charge_min_soc: Şarj eşiği
        charge_target_soc: Şarj hedefi
        
    Returns:
        SimulationResult
    """
    simulator = SOCSimulator(
        battery_capacity_kwh=battery_capacity_kwh,
        start_soc=start_soc,
        target_arrival_soc=target_arrival_soc,
        charge_min_soc=charge_min_soc,
        charge_target_soc=charge_target_soc
    )
    return simulator.simulate(segments_with_consumption, total_distance_km)
