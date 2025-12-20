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
from app.constants import (
    HARD_MIN_SOC,
    TARGET_MIN_SOC,
    SAFETY_BUFFER_PERCENT,
    MIN_CHARGE_THRESHOLD_PERCENT,
    MIN_DISTANCE_BETWEEN_STOPS_KM,
    STOP_PENALTY_MINUTES,
    SHORT_INTERVAL_PENALTY_MINUTES,
    MIN_DRIVING_INTERVAL_MINUTES,
    TARGET_SOC_MIN,
    TARGET_SOC_MAX,
    TARGET_SOC_STEP,
    MAX_MIN_REQUIRED_SOC,
    HOTSPOT_SOC_BUFFER,
    MULTI_STOP_TARGET_MIN,
    MULTI_STOP_TARGET_MAX,
    FINAL_STOP_TARGET_MIN,
    FINAL_STOP_TARGET_MAX,
    DEFAULT_ARRIVAL_SOC,
    LONG_ROUTE_ARRIVAL_SOC
)

logger = get_logger("soc_simulator")


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
        route_bearing: Rota yönü (derece, 0-360) - V2.9: Otoyol filtresi için
    """
    segment_index: int
    location: GeoPoint
    soc_at_point: float
    distance_from_start_km: float
    remaining_distance_km: float
    min_required_soc: float
    recommended_charge_to: float = 80.0
    route_bearing: float = 0.0  # 🔧 V2.9: İstasyon yön filtresi için


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
        charge_target_soc: float = 80.0,
        user_override_target: bool = False
    ):
        """
        Args:
            battery_capacity_kwh: Batarya kapasitesi (kWh)
            start_soc: Başlangıç batarya yüzdesi
            target_arrival_soc: Varışta hedef batarya yüzdesi
            charge_min_soc: Şarj eşiği - bu %'e düşünce şarj et
            charge_target_soc: Şarj hedefi - bu %'e kadar şarj et
            user_override_target: Kullanıcı target_soc'u manuel belirledi mi?
        """
        self.battery_capacity_kwh = battery_capacity_kwh
        self.start_soc = start_soc
        self.target_arrival_soc = target_arrival_soc
        self.charge_min_soc = charge_min_soc
        self.charge_target_soc = min(100.0, charge_target_soc)
        self.user_override_target = user_override_target
        
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
        
        # MainCalculator'dan gelen GERÇEK toplam tüketimi hesapla
        route_total_consumption = sum(s.consumption_kwh for s in segments_with_consumption)
        avg_consumption_per_km = route_total_consumption / total_distance_km if total_distance_km > 0 else 0.20
        
        # 🔧 V2.5 FIX: Kalan segment tüketimlerini önceden hesapla (suffix-sum)
        # Her segment için "bu segmentten sonraki toplam tüketim (kWh)"
        # remaining_consumption_suffix[i] = segment[i+1] + segment[i+2] + ... + segment[n-1]
        num_segments = len(segments_with_consumption)
        remaining_consumption_suffix = [0.0] * (num_segments + 1)  # +1 for boundary
        for i in range(num_segments - 1, -1, -1):
            remaining_consumption_suffix[i] = (
                segments_with_consumption[i].consumption_kwh + remaining_consumption_suffix[i + 1]
            )
        
        logger.info(
            f"SOC simulation started: {num_segments} segments, "
            f"start_soc={self.start_soc}%, "
            f"total_consumption={route_total_consumption:.2f}kWh (MainCalculator segment-sum)"
        )
        
        for seg_with_cons in segments_with_consumption:
            segment = seg_with_cons.segment
            consumption_kwh = seg_with_cons.consumption_kwh
            
            # SOC başlangıç değeri
            seg_with_cons.soc_at_start = current_soc
            soc_before_segment = current_soc  # Segment öncesi SOC (hotspot için)
            
            # 🔧 DEBUG: Segment bazlı tüketim kontrolü
            logger.debug(
                f"[SIM] seg={segment.index}, dist={segment.distance_km:.1f}km, "
                f"cum={segment.cumulative_distance_km:.1f}km, cons={consumption_kwh:.2f}kWh, "
                f"soc_before={current_soc:.1f}%"
            )
            
            # Bu segment sonrası SOC ne olacak? (önceden hesapla)
            soc_drop = (consumption_kwh / self.battery_capacity_kwh) * 100
            projected_soc_after = current_soc - soc_drop
            
            # 🔧 KRİTİK: Eğer tek segment tüketimi bataryayı aşıyorsa HEMEN hotspot oluştur
            if soc_drop > current_soc:
                logger.warning(
                    f"[CRITICAL] Segment {segment.index} tüketimi SOC'u aşıyor! "
                    f"soc_drop={soc_drop:.1f}%, current_soc={current_soc:.1f}%"
                )
            
            # Kalan mesafe (segment sonunda)
            remaining_distance = total_distance_km - segment.cumulative_distance_km
            
            # 🔧 V2.5 FIX: Kalan segment tüketimi (MainCalculator'dan GERÇEK değer)
            # segment.index sonrasındaki tüm segmentlerin toplam tüketimi
            remaining_consumption_kwh = remaining_consumption_suffix[segment.index + 1]
            
            # Minimum gerekli SOC hesapla (artık GERÇEK kalan tüketimle)
            min_required_soc = self._calculate_min_required_soc(remaining_consumption_kwh)
            
            # 🔧 DEBUG: Hotspot karar verme
            logger.debug(
                f"[REQ] remaining={remaining_distance:.1f}km, remaining_kwh={remaining_consumption_kwh:.2f}, "
                f"min_req={min_required_soc:.1f}%, projected_soc={projected_soc_after:.1f}%"
            )
            
            # 🔧 ERKEN HOTSPOT TESPİTİ: Segment SONRASI SOC çok düşecekse, ÖNCE şarj et
            should_create_hotspot = self._should_create_hotspot(
                current_soc=projected_soc_after,  # Segment SONRASI SOC ile kontrol
                min_required_soc=min_required_soc,
                cumulative_km=segment.cumulative_distance_km,
                last_hotspot_km=last_hotspot_km
            )
            
            if should_create_hotspot:
                # Hotspot'u segment ÖNCESİNDE oluştur (SOC henüz yüksek)
                prev_cumulative_km = segment.cumulative_distance_km - segment.distance_km
                
                # 🔧 V2.9: Rota yönünü hesapla (istasyon yön filtresi için)
                route_bearing = 0.0
                if segment.start_point and segment.end_point:
                    import math
                    lat1 = math.radians(segment.start_point.lat)
                    lat2 = math.radians(segment.end_point.lat)
                    dlon = math.radians(segment.end_point.lon - segment.start_point.lon)
                    y = math.sin(dlon) * math.cos(lat2)
                    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
                    route_bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
                
                hotspot = ChargeHotspot(
                    segment_index=max(0, segment.index - 1),  # Önceki segment
                    location=segment.end_point,  # Yaklaşık konum
                    soc_at_point=round(max(self.charge_min_soc, soc_before_segment), 1),  # Segment ÖNCESİ SOC
                    distance_from_start_km=max(0, prev_cumulative_km),
                    remaining_distance_km=round(remaining_distance + segment.distance_km, 1),
                    min_required_soc=round(min_required_soc, 1),
                    recommended_charge_to=self.charge_target_soc,
                    route_bearing=route_bearing  # 🔧 V2.9
                )
                hotspots.append(hotspot)
                last_hotspot_km = prev_cumulative_km
                
                # Şarj sonrası SOC'u simüle et
                current_soc = self.charge_target_soc
                
                logger.info(
                    f"Hotspot detected BEFORE segment {segment.index}",
                    soc_at_hotspot=hotspot.soc_at_point,
                    soc_after_charge=current_soc,
                    distance_km=hotspot.distance_from_start_km
                )
            
            # Segment tüketimini uygula (hotspot varsa şarjlı SOC'tan başlar)
            soc_drop = (consumption_kwh / self.battery_capacity_kwh) * 100
            current_soc = max(0, current_soc - soc_drop)
            total_consumption += consumption_kwh
            
            # SOC bitiş değeri
            seg_with_cons.soc_at_end = current_soc
        
        # Çok yakın hotspotları birleştir (36 dk gibi kısa aralıkları önle)
        if len(hotspots) > 1:
            original_count = len(hotspots)
            hotspots = self._merge_close_hotspots(hotspots, avg_consumption_per_km)
            if len(hotspots) < original_count:
                logger.info(f"Hotspots merged: {original_count} → {len(hotspots)}")
        
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
        # 🔧 V2.6: Kullanıcı override verdiyse dinamik hesaplamayı atla
        if hotspots and not self.user_override_target:
            self._calculate_smart_charge_targets(
                hotspots, 
                total_distance_km, 
                avg_consumption_per_km
            )
        elif hotspots and self.user_override_target:
            logger.info(f"User override target_soc={self.charge_target_soc}% - skipping dynamic calculation")
        
        return result
    
    def _merge_close_hotspots(
        self,
        hotspots: List[ChargeHotspot],
        avg_consumption_per_km: float,
        avg_speed_kmh: float = 80.0
    ) -> List[ChargeHotspot]:
        """
        Birbirine çok yakın hotspotları birleştir.
        
        36 dk gibi kısa sürüş aralıklarını önler.
        İlk hotspot'u korur (daha erken şarj = daha güvenli).
        
        Args:
            hotspots: Hotspot listesi
            avg_consumption_per_km: Ortalama tüketim
            avg_speed_kmh: Ortalama hız (sürüş süresi hesabı için)
        
        Returns:
            Birleştirilmiş hotspot listesi
        """
        if len(hotspots) <= 1:
            return hotspots
        
        merged = [hotspots[0]]
        
        for i in range(1, len(hotspots)):
            current = hotspots[i]
            last = merged[-1]
            
            # İki hotspot arası mesafe ve süre
            distance_between = current.distance_from_start_km - last.distance_from_start_km
            driving_minutes = (distance_between / avg_speed_kmh) * 60
            
            # MIN_DRIVING_INTERVAL'dan kısa ise birleştir
            if driving_minutes < MIN_DRIVING_INTERVAL_MINUTES:
                logger.info(
                    f"Merging hotspot: {current.distance_from_start_km:.0f}km "
                    f"(only {driving_minutes:.0f}min after previous) - keeping earlier one"
                )
                # İlk hotspot'u koru, sonrakini atla
                # Ama son hotspot'un remaining_distance bilgisini güncelle
                continue
            
            merged.append(current)
        
        return merged
    
    def _calculate_smart_charge_targets(
        self,
        hotspots: List[ChargeHotspot],
        total_distance_km: float,
        avg_consumption_per_km: float
    ) -> None:
        """
        🔧 V2.3: Her hotspot için DİNAMİK şarj hedefi hesapla.
        
        UZUN ROTA STRATEJİSİ (hotspots > 1):
        - Ara duraklar: %72-82 bandı (90-95 DEĞİL!)
        - Son durak: %60-85 bandı
        - Sonraki durağa %25 SOC ile varmayı hedefle
        
        TEK DURAK STRATEJİSİ (hotspots == 1):
        - Mevcut davranış: %90'a kadar izin ver
        - Hedefe yetecek kadar şarj et
        """
        num_hotspots = len(hotspots)
        is_long_route = num_hotspots > 1  # Birden fazla durak = uzun rota
        
        for i, hotspot in enumerate(hotspots):
            is_last_stop = (i == num_hotspots - 1)
            
            if is_last_stop:
                # SON DURAK: Hedefe yetecek kadar
                remaining_km = hotspot.remaining_distance_km
                required_kwh = remaining_km * avg_consumption_per_km
                required_soc = (required_kwh / self.battery_capacity_kwh) * 100
                
                # Hedef = varış SOC + kalan mesafe tüketimi + güvenlik
                # 🔧 Uzun rotalarda varış hedefi daha düşük (%15-20)
                arrival_target = LONG_ROUTE_ARRIVAL_SOC if is_long_route else self.target_arrival_soc
                target = arrival_target + required_soc + SAFETY_BUFFER_PERCENT
                
                if is_long_route:
                    # 🔧 UZUN ROTA: Son durak için daha düşük hedef (%60-85)
                    target = max(FINAL_STOP_TARGET_MIN, min(FINAL_STOP_TARGET_MAX, target))
                else:
                    # TEK DURAK: Mevcut davranış (%50-90)
                    target = max(50.0, min(90.0, target))
                
            else:
                # ARA DURAK: Sonraki durağa yetecek + güvenlik (DİNAMİK)
                next_hotspot = hotspots[i + 1]
                distance_to_next = next_hotspot.distance_from_start_km - hotspot.distance_from_start_km
                required_kwh = distance_to_next * avg_consumption_per_km
                required_soc = (required_kwh / self.battery_capacity_kwh) * 100
                
                # 🔧 V2.3: Sonraki durağa %25 SOC ile varmayı hedefle (40 değil!)
                target = 25.0 + required_soc + SAFETY_BUFFER_PERCENT
                
                # 🔧 UZUN ROTA: Ara duraklar için daha düşük bant (%72-82)
                # ESKİ: max(80.0, min(95.0, target)) - çok yüksek!
                # YENİ: max(72, min(82, target))
                target = max(MULTI_STOP_TARGET_MIN, min(MULTI_STOP_TARGET_MAX, target))
            
            # Mevcut SOC'dan düşük olamaz (en az %15 şarj et)
            target = max(target, hotspot.soc_at_point + 15)
            
            # Uzun rotalarda üst sınır: %85 (tek durakta %95)
            max_target = 85.0 if is_long_route else 95.0
            target = min(max_target, target)
            
            old_target = hotspot.recommended_charge_to
            hotspot.recommended_charge_to = round(target, 0)
            
            logger.info(
                f"Dynamic charge target: Hotspot {i+1}/{num_hotspots} - "
                f"{old_target}% → {hotspot.recommended_charge_to}% "
                f"({'SON DURAK' if is_last_stop else f'ARA DURAK - sonraki {distance_to_next:.0f}km'})"
            )
    
    def _calculate_min_required_soc(self, remaining_consumption_kwh: float) -> float:
        """
        🔧 V2.5: Kalan yol için gereken minimum SOC hesapla.
        
        Args:
            remaining_consumption_kwh: Kalan segmentlerin TOPLAM tüketimi (MainCalculator'dan)
        
        Mantık:
        - MainCalculator'ın segment bazlı gerçek tüketimlerini kullan
        - Ortalama tüketim × mesafe yaklaşımı KALDIRILDI
        """
        # Kalan yol için gereken SOC yüzdesi (GERÇEK segment tüketimleriyle)
        required_percent = (remaining_consumption_kwh / self.battery_capacity_kwh) * 100
        
        # Varışa ulaşmak için gereken minimum SOC
        # = kalan yol tüketimi + varış hedefi + güvenlik
        min_required = self.target_arrival_soc + required_percent + SAFETY_BUFFER_PERCENT
        
        # Eğer 100%'ü aşıyorsa, ara şarj kaçınılmaz
        if min_required > 100.0:
            # Bir şarjla ne kadar gidilebilir? (80% kullanılabilir enerji)
            max_single_charge_kwh = 0.80 * self.battery_capacity_kwh
            
            # Kalan tüketim bir şarjdan fazlaysa, en az %50 SOC'ta şarj et
            if remaining_consumption_kwh > max_single_charge_kwh:
                return max(50.0, self.charge_min_soc + 30)  # En az %50
            else:
                # Tek şarjla bitirilecek - standart eşik
                return max(35.0, self.charge_min_soc + SAFETY_BUFFER_PERCENT)
        
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
        
        🔧 V2.2: YUMUŞATILMIŞ KOŞULLAR
        - SOC %60'a düşse bile panikleyip hotspot üretme
        - min_required_soc'a buffer ekle (esneklik)
        - Gereksiz erken hotspot'ları önle
        """
        # Son hotspot'tan mesafe kontrolü
        distance_since_last = cumulative_km - last_hotspot_km
        if distance_since_last < MIN_DISTANCE_BETWEEN_STOPS_KM and last_hotspot_km > 0:
            return False
        
        # 🔧 min_required_soc üst sınırı (agresif hotspot önleme)
        capped_min_required = min(min_required_soc, MAX_MIN_REQUIRED_SOC)
        
        # Acil durum (çok düşük SOC) - en öncelikli
        if current_soc <= MIN_CHARGE_THRESHOLD_PERCENT:
            return True
        
        # Kullanıcı eşiği
        if current_soc <= self.charge_min_soc:
            return True
        
        # 🔧 YUMUŞATILMIŞ: Dinamik eşik - BUFFER ile kontrol
        # Eğer SOC, (min_required - buffer)'ın altına düştüyse hotspot oluştur
        # Bu sayede %60 SOC'ta panikleyip hotspot üretmez
        if current_soc < (capped_min_required - HOTSPOT_SOC_BUFFER):
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
        best_target_soc = 75.0
        best_result = None
        single_stop_result = None  # Tek durak çözümü için
        single_stop_soc = None
        
        # 🔧 DİNAMİK ARALIK: %65-95 arası TÜM değerleri dene
        target_soc_range = range(TARGET_SOC_MIN, TARGET_SOC_MAX + 1, TARGET_SOC_STEP)
        
        logger.info(
            f"ChargePlanOptimizer: Testing {len(list(target_soc_range))} target SOC values "
            f"({TARGET_SOC_MIN}%-{TARGET_SOC_MAX}%)"
        )
        
        for target_soc in target_soc_range:
            # Bu target_soc ile simülasyon yap
            simulator = SOCSimulator(
                battery_capacity_kwh=battery_capacity_kwh,
                start_soc=start_soc,
                target_arrival_soc=target_arrival_soc,
                charge_min_soc=charge_min_soc,
                charge_target_soc=target_soc
            )
            
            result = simulator.simulate(segments_with_consumption, total_distance_km)
            
            # EARLY TERMINATION: Şarj gerekmiyorsa hemen dön
            if len(result.hotspots) == 0:
                logger.info(f"ChargePlanOptimizer: No charging needed! (early exit)")
                return target_soc, result
            
            # 🔧 TEK DURAK OPTİMİZASYONU: En düşük SOC'lu tek durak çözümünü sakla
            if len(result.hotspots) == 1 and result.final_soc >= target_arrival_soc:
                if single_stop_result is None or target_soc < single_stop_soc:
                    single_stop_result = result
                    single_stop_soc = target_soc
                    logger.debug(f"  Single-stop solution found at {target_soc}%")
            
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
        
        # 🔧 TEK DURAK TERCİHİ: Eğer tek durakla gidilebiliyorsa, onu tercih et
        if single_stop_result is not None:
            single_stop_score = self._calculate_plan_score(single_stop_result, single_stop_soc, avg_speed_kmh)
            # Tek durak çözümü en iyi veya çok yakınsa, onu kullan
            if single_stop_score <= best_score * 1.1:  # %10 tolerans
                logger.info(
                    f"ChargePlanOptimizer: Single-stop solution preferred - "
                    f"target_soc={single_stop_soc}%, score={single_stop_score:.1f}"
                )
                return single_stop_soc, single_stop_result
        
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
        
        # 4. YÜKSEK SOC PENALTİSİ (rota türüne göre)
        # 🔧 V2.3: Uzun rotalarda yüksek SOC'u daha fazla penalize et
        base_high_soc_penalty = apply_high_soc_penalty(target_soc)
        
        if num_stops == 1:
            # TEK DURAK: Yüksek SOC penaltisini çok azalt (%90 makul)
            high_soc_penalty = base_high_soc_penalty * 0.3
        else:
            # UZUN ROTA: %80 üzeri hedefleri agresif penalize et
            # Her durak için penaltı katlanır (2+ durak = çok fazla şarj süresi)
            high_soc_penalty = base_high_soc_penalty * num_stops * 1.5
        
        # 🔧 V2.3: Yeni skor formülü
        # Durak sayısı en önemli faktör (60 dk/durak)
        total_score = stop_penalty + total_charge_time + short_interval_penalty + high_soc_penalty
        
        return total_score
