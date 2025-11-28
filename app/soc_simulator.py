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
from app.charging_model import calculate_charge_time, apply_high_soc_penalty

logger = get_logger("soc_simulator")


# =============================================================================
# CONSTANTS
# =============================================================================

SAFETY_BUFFER_PERCENT = 10.0  # Güvenlik marjı
MIN_CHARGE_THRESHOLD_PERCENT = 15.0  # Minimum şarj seviyesi
MIN_DISTANCE_BETWEEN_STOPS_KM = 50.0  # Şarj durakları arası minimum mesafe

# Optimizer sabitleri
STOP_PENALTY_MINUTES = 25.0  # Her ek durak için zaman penaltisi (dk) - AĞIR
SHORT_INTERVAL_PENALTY_MINUTES = 15.0  # Kısa aralıklı durak penaltisi (dk)
MIN_DRIVING_INTERVAL_MINUTES = 60.0  # 1 saatten kısa sürüş aralıkları penalize edilir
TARGET_SOC_CANDIDATES = [80.0, 85.0, 90.0, 95.0]  # Yüksek hedefler - az durak için


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


@dataclass
class SimulationResultWithStations:
    """
    SOC simülasyonu + İstasyon arama sonucu (birleşik).
    """
    simulation: SimulationResult
    station_results: List  # List[CorridorSearchResult]
    
    @property
    def hotspots(self) -> List[ChargeHotspot]:
        return self.simulation.hotspots
    
    @property
    def final_soc(self) -> float:
        return self.simulation.final_soc
    
    @property
    def total_consumption_kwh(self) -> float:
        return self.simulation.total_consumption_kwh
    
    @property
    def charge_stops_count(self) -> int:
        return sum(1 for r in self.station_results if r.best_station)


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
        
        # Her hotspot için bağımsız şarj hedefi hesapla
        if hotspots:
            self._calculate_smart_charge_targets(
                hotspots, 
                total_distance_km, 
                avg_consumption_per_km
            )
        
        return result
    
    def _calculate_smart_charge_targets(
        self,
        hotspots: List[ChargeHotspot],
        total_distance_km: float,
        avg_consumption_per_km: float
    ) -> None:
        """
        Her hotspot için akıllı şarj hedefi hesapla.
        
        YENİ MANTIK (durak sayısını minimize et):
        - ARA DURAKLAR: Optimizer'ın bulduğu yüksek hedefi KORU (charge_target_soc)
          → Yüksek şarjla daha uzun gidilir, daha az durak gerekir
        - SON DURAK: Hedefe tam yetecek kadar (gereksiz yüksek şarj önlenir)
        
        ESKİ YANLIŞ MANTIK: Her durağı "sonrakine yetecek" şarj ediyordu
        → Bu çok düşük hedefler üretiyordu (%58, %69)
        → Sonuç: Sürekli şarj gerekiyor, 3-4 durak
        """
        num_hotspots = len(hotspots)
        
        for i, hotspot in enumerate(hotspots):
            is_last_stop = (i == num_hotspots - 1)
            
            if is_last_stop:
                # SON DURAK: Hedefe yetecek kadar (gereksiz şarj önle)
                remaining_km = hotspot.remaining_distance_km
                required_kwh = remaining_km * avg_consumption_per_km
                required_soc = (required_kwh / self.battery_capacity_kwh) * 100
                
                # Hedef = varış SOC + kalan mesafe tüketimi + güvenlik
                target = self.target_arrival_soc + required_soc + SAFETY_BUFFER_PERCENT
                
                # Sınırla: min %50, max %95
                target = max(50.0, min(95.0, target))
                
            else:
                # ARA DURAK: Optimizer'ın bulduğu YÜKSEK hedefi KORU
                # Yüksek şarj = daha uzun menzil = daha az durak = daha iyi UX
                target = self.charge_target_soc
            
            # Mevcut SOC'dan düşük olamaz (en az %15 şarj et)
            target = max(target, hotspot.soc_at_point + 15)
            target = min(95.0, target)
            
            old_target = hotspot.recommended_charge_to
            hotspot.recommended_charge_to = round(target, 0)
            
            logger.info(
                f"Smart charge target: Hotspot {i+1}/{num_hotspots} - "
                f"{old_target}% → {hotspot.recommended_charge_to}% "
                f"({'SON - hedefe yetecek' if is_last_stop else 'ARA - yüksek şarj'})"
            )
    
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
    
    async def simulate_with_stations(
        self,
        segments_with_consumption: List[SegmentWithConsumption],
        total_distance_km: float,
        vehicle_model_id: str
    ) -> SimulationResultWithStations:
        """
        SOC simülasyonu + Hotspot tespiti + İstasyon bulma (TEK ADIMDA).
        
        Args:
            segments_with_consumption: MainCalculator'dan gelen tüketimli segmentler
            total_distance_km: Toplam rota mesafesi
            vehicle_model_id: Araç modeli ID (istasyon uyumluluğu için)
            
        Returns:
            SimulationResultWithStations: Simülasyon + İstasyon sonuçları
        """
        # 1. SOC simülasyonu ve hotspot tespiti
        sim_result = self.simulate(segments_with_consumption, total_distance_km)
        
        # 2. Hotspot varsa istasyon ara
        station_results = []
        if sim_result.hotspots:
            from app.station_finder import find_stations_for_hotspots
            station_results = await find_stations_for_hotspots(
                sim_result.hotspots,
                vehicle_model_id
            )
            
            logger.info(
                f"Stations found for {len(sim_result.hotspots)} hotspots: "
                f"{sum(1 for r in station_results if r.best_station)} successful"
            )
        
        return SimulationResultWithStations(
            simulation=sim_result,
            station_results=station_results
        )


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


# =============================================================================
# CHARGE PLAN OPTIMIZER
# =============================================================================

class ChargePlanOptimizer:
    """
    Şarj Planı Optimize Edici v1.0
    
    Farklı target_soc değerlerini deneyerek minimum durak sayısı ve
    optimal toplam süre için en iyi planı seçer.
    
    Prensipler:
    1. 80% sabit hedef YOK - 75-95% arasında dinamik seçim
    2. Durak sayısını minimize et (1-2 durak >> 3+ durak)
    3. Kısa aralıklı durakları (30-40 dk) penalize et
    4. Toplam süreyi optimize et (şarj süresi + penaltiler)
    
    Kullanım:
        optimizer = ChargePlanOptimizer()
        optimal_soc, result = optimizer.find_optimal_plan(
            segments, distance, battery, start_soc, arrival_soc, min_soc
        )
    """
    
    def __init__(self, battery_capacity_kwh: float = 51.0):
        """
        Args:
            battery_capacity_kwh: Batarya kapasitesi (şarj süresi hesabı için)
        """
        self.battery_capacity_kwh = battery_capacity_kwh
    
    def find_optimal_plan(
        self,
        segments_with_consumption: List[SegmentWithConsumption],
        total_distance_km: float,
        battery_capacity_kwh: float,
        start_soc: float,
        target_arrival_soc: float,
        charge_min_soc: float,
        avg_speed_kmh: float = 80.0
    ) -> tuple:
        """
        75-95% arasında target_soc denerek en iyi plan bulunur.
        
        Args:
            segments_with_consumption: Tüketimli segmentler
            total_distance_km: Toplam rota mesafesi
            battery_capacity_kwh: Batarya kapasitesi
            start_soc: Başlangıç SOC
            target_arrival_soc: Varışta hedef SOC
            charge_min_soc: Şarj eşiği (bu %'e düşünce şarj et)
            avg_speed_kmh: Ortalama hız (kısa aralık hesabı için)
        
        Returns:
            tuple: (optimal_target_soc, best_result: SimulationResult)
        """
        self.battery_capacity_kwh = battery_capacity_kwh
        
        best_score = float('inf')
        best_target_soc = 80.0
        best_result = None
        
        logger.info(
            f"ChargePlanOptimizer: Testing {len(TARGET_SOC_CANDIDATES)} target SOC candidates"
        )
        
        for target_soc in TARGET_SOC_CANDIDATES:
            # Bu target_soc ile simülasyon yap
            simulator = SOCSimulator(
                battery_capacity_kwh=battery_capacity_kwh,
                start_soc=start_soc,
                target_arrival_soc=target_arrival_soc,
                charge_min_soc=charge_min_soc,
                charge_target_soc=target_soc
            )
            
            result = simulator.simulate(segments_with_consumption, total_distance_km)
            
            # Plan skorunu hesapla
            score = self._calculate_plan_score(result, target_soc, avg_speed_kmh)
            
            logger.debug(
                f"  target_soc={target_soc}%: "
                f"stops={len(result.hotspots)}, score={score:.1f}"
            )
            
            # En iyi planı güncelle
            if score < best_score:
                best_score = score
                best_target_soc = target_soc
                best_result = result
        
        logger.info(
            f"ChargePlanOptimizer: Optimal plan found - "
            f"target_soc={best_target_soc}%, stops={len(best_result.hotspots)}, "
            f"score={best_score:.1f}"
        )
        
        return best_target_soc, best_result
    
    def _calculate_plan_score(
        self,
        result: SimulationResult,
        target_soc: float,
        avg_speed_kmh: float
    ) -> float:
        """
        Plan skoru hesapla (düşük skor = daha iyi plan).
        
        Skor Bileşenleri:
        1. Durak penaltisi: Her durak için STOP_PENALTY_MINUTES dk
        2. Şarj süresi: Tahmini toplam şarj süresi
        3. Kısa aralık penaltisi: 45 dk'dan kısa sürüş aralıkları için
        
        Args:
            result: Simülasyon sonucu
            target_soc: Şarj hedefi %
            avg_speed_kmh: Ortalama hız
        
        Returns:
            float: Toplam skor (dakika cinsinden)
        """
        num_stops = len(result.hotspots)
        
        # Şarj gerekmiyorsa en iyi skor
        if num_stops == 0:
            return 0.0
        
        # 1. DURAK PENALTİSİ (ağır)
        # Her ek durak = park et, bul, tak, bekle, çık → 15 dk kayıp
        stop_penalty = num_stops * STOP_PENALTY_MINUTES
        
        # 2. TAHMİNİ ŞARJ SÜRESİ (gerçekçi eğri modeli)
        # charging_model: 3 fazlı şarj eğrisi (CC-CV)
        total_charge_time = 0.0
        avg_charger_power = 100.0  # Ortalama DC şarjcı varsayımı (optimizer için)
        
        for hotspot in result.hotspots:
            charge_result = calculate_charge_time(
                start_soc=hotspot.soc_at_point,
                target_soc=target_soc,
                battery_capacity_kwh=self.battery_capacity_kwh,
                peak_power_kw=avg_charger_power,
                temperature_c=None  # Optimizer'da bilinmiyor
            )
            total_charge_time += max(5, charge_result.duration_minutes)
        
        # 3. KISA ARALIK PENALTİSİ
        # 45 dk'dan kısa sürüş aralıkları kötü kullanıcı deneyimi
        short_interval_penalty = 0.0
        prev_km = 0.0
        
        for hotspot in result.hotspots:
            interval_km = hotspot.distance_from_start_km - prev_km
            interval_minutes = (interval_km / avg_speed_kmh) * 60 if avg_speed_kmh > 0 else 0
            
            if interval_minutes < MIN_DRIVING_INTERVAL_MINUTES and interval_km > 0:
                # Ne kadar kısa, o kadar penaltı
                penalty_factor = 1 - (interval_minutes / MIN_DRIVING_INTERVAL_MINUTES)
                short_interval_penalty += SHORT_INTERVAL_PENALTY_MINUTES * penalty_factor
            
            prev_km = hotspot.distance_from_start_km
        
        # 4. YÜKSEK SOC PENALTİSİ
        # %80 üzeri şarj çok yavaş - charging_model'den penaltı al
        high_soc_penalty = apply_high_soc_penalty(target_soc) * num_stops
        
        total_score = stop_penalty + total_charge_time + short_interval_penalty + high_soc_penalty
        
        return total_score
