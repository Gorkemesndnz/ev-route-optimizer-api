"""
Backward Planner — Sondan Başa min_target_soc İndüksiyonu
==========================================================

Greedy "her durakta %80'e şarj" yerine:
1. Son leg'den başla, varış SOC + buffer hesapla
2. Geri git: her durakta MİN gerekli target_soc'u hesapla
3. Bu min_target ParetoSolver'a "alt sınır" olarak verilir

Sonuç: gereksiz şarj zamanı önlenir; her durakta sadece "bir sonraki durağa
güvenle ulaşacak kadar" şarj edilir.
"""

from dataclasses import dataclass
from typing import List, Optional

from app.soc_simulator import ChargeHotspot, SegmentWithConsumption
from app.optimization.dynamic_buffer import (
    BufferContext,
    calculate_dynamic_buffer,
)
from app.utils.logger import get_logger

logger = get_logger("BackwardPlanner")


# =============================================================================
# CONSTANTS
# =============================================================================

MAX_CHARGE_TARGET = 95.0  # Phase2 üstü zaman maliyetli
ABS_MIN_TARGET = 30.0     # Sub-30% target çok düşük (şarj eğrisi verimsiz)
MIN_SAFE_ARRIVAL = 15.0   # Mid-stop'a varışta minimum SOC (tükenmeme garantisi)
                          # Final stop arrival_soc_target kullanır; mid stop'lar bunu kullanır


# =============================================================================
# DATA CLASS
# =============================================================================

@dataclass
class PlannedStop:
    """Bir durağın backward induction sonucu."""
    hotspot_index: int
    hotspot: ChargeHotspot
    leg_to_next_kwh: float           # Bu duraktan sonrakine (veya varışa) tüketim
    leg_to_next_distance_km: float
    min_target_soc: float            # Bu durakta MİN şarj edilecek SOC (%)
    dynamic_buffer: float            # Bu leg için kullanılan buffer (%)
    is_final_leg: bool               # Sondan ikinci durak (sonrası varış)


# =============================================================================
# CALCULATION
# =============================================================================

def plan_backwards(
    hotspots: List[ChargeHotspot],
    segments: List[SegmentWithConsumption],
    battery_kwh: float,
    arrival_soc_target: float,
    weather_per_leg: Optional[List] = None,    # her leg için WeatherInfo (opsiyonel)
    traffic_factors: Optional[List[float]] = None,  # her leg için trafik faktörü
    user_anxiety_factor: float = 0.0,
) -> List[PlannedStop]:
    """
    Sondan başa: her durak için minimum target_soc hesapla.

    Args:
        hotspots: SOCSimulator pass-1'den gelen hotspot'lar (sıralı)
        segments: Tüketimli segmentler (her hotspot'un index'i bu listeyi referans alır)
        battery_kwh: Araç batarya kapasitesi
        arrival_soc_target: Varışta hedef SOC (%)
        weather_per_leg: Her leg için WeatherInfo (None ise hava cezası uygulanmaz)
        traffic_factors: Her leg için duration_in_traffic/duration oranı
        user_anxiety_factor: 0.0-5.0 (UI'dan ileride alınabilir)

    Returns:
        Hotspot başına PlannedStop listesi (giriş sırasıyla)
    """
    n = len(hotspots)
    if n == 0:
        return []

    planned: List[Optional[PlannedStop]] = [None] * n
    next_min_arrival = arrival_soc_target

    for i in reversed(range(n)):
        leg_kwh, leg_km, leg_elev_gain, leg_weather = _aggregate_leg_after(
            hotspot_index=i,
            hotspots=hotspots,
            segments=segments,
            weather_per_leg=weather_per_leg,
        )
        is_final = (i == n - 1)
        traffic = (
            traffic_factors[i]
            if traffic_factors and i < len(traffic_factors)
            else 1.0
        )

        ctx = BufferContext(
            leg_distance_km=leg_km,
            leg_kwh_estimate=leg_kwh,
            is_final_leg=is_final,
            weather=leg_weather,
            elevation_gain_m=leg_elev_gain,
            traffic_factor=traffic,
            user_anxiety_factor=user_anxiety_factor,
        )
        buffer = calculate_dynamic_buffer(ctx)

        # MİN target_soc: bir sonraki min_arrival + leg tüketimi + buffer
        leg_soc_drop = (leg_kwh / battery_kwh) * 100.0 if battery_kwh > 0 else 0.0
        raw_min_target = next_min_arrival + leg_soc_drop + buffer

        # Sınırlar: [ABS_MIN_TARGET, MAX_CHARGE_TARGET]
        min_target = max(ABS_MIN_TARGET, min(MAX_CHARGE_TARGET, raw_min_target))

        planned[i] = PlannedStop(
            hotspot_index=i,
            hotspot=hotspots[i],
            leg_to_next_kwh=round(leg_kwh, 2),
            leg_to_next_distance_km=round(leg_km, 1),
            min_target_soc=round(min_target, 1),
            dynamic_buffer=round(buffer, 1),
            is_final_leg=is_final,
        )

        # Bir önceki iterasyon (i-1) için: i'ye varış ihtiyacı.
        # Final stop'a varış = arrival_soc_target (zaten kullanıldı).
        # Mid stop'a varış = MIN_SAFE_ARRIVAL — orada şarj edileceği için
        # sadece tükenmeme garantisi yeterli, leave-SOC propagate etmek
        # compound inflation'a yol açar (her durağı 95'e clamp eder).
        next_min_arrival = MIN_SAFE_ARRIVAL

    logger.info(
        f"BackwardPlanner: {n} stops, target_socs={[p.min_target_soc for p in planned]}, "
        f"buffers={[p.dynamic_buffer for p in planned]}"
    )

    return planned  # type: ignore[return-value]


# =============================================================================
# HELPERS
# =============================================================================

def _aggregate_leg_after(
    hotspot_index: int,
    hotspots: List[ChargeHotspot],
    segments: List[SegmentWithConsumption],
    weather_per_leg: Optional[List],
) -> tuple:
    """
    hotspot_index'inci durağın bittiği yerden sonraki durağa (veya varışa)
    kadar olan leg'in (kwh, km, elev_gain, weather) toplamını döner.
    """
    # hotspot.segment_index: hangi segment SONRASI olduğu
    start_seg = hotspots[hotspot_index].segment_index + 1

    if hotspot_index < len(hotspots) - 1:
        end_seg = hotspots[hotspot_index + 1].segment_index
    else:
        end_seg = len(segments) - 1

    if start_seg > end_seg:
        return 0.0, 0.0, 0.0, None

    total_kwh = 0.0
    total_km = 0.0
    total_elev_gain = 0.0

    for s_idx in range(start_seg, min(end_seg + 1, len(segments))):
        seg_with_cons = segments[s_idx]
        # SegmentWithConsumption.segment ve .consumption_kwh attribute'larına eriş
        inner_seg = getattr(seg_with_cons, "segment", seg_with_cons)
        total_kwh += getattr(seg_with_cons, "consumption_kwh", 0.0)
        total_km += getattr(inner_seg, "distance_km", 0.0)
        total_elev_gain += getattr(inner_seg, "elevation_gain_m", 0.0)

    # Leg'in temsili hava durumu: ilk segmentinkini al
    leg_weather = None
    if weather_per_leg and start_seg < len(weather_per_leg):
        leg_weather = weather_per_leg[start_seg]

    return total_kwh, total_km, total_elev_gain, leg_weather
