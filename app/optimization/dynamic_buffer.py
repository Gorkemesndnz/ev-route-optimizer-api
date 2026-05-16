"""
Dynamic Buffer — Leg-bazlı Güvenlik Payı
==========================================

Statik %15-20 arrival_soc yerine, her leg için context'e göre dinamik buffer:
- Hava durumu (rüzgar, yağış)
- Yokuş (elevation gain)
- Trafik tıkanıklığı
- Kullanıcı range-anxiety profili
- Son leg asimetrisi (sondaki miscalc → tow truck, ortadaki miscalc → bir sonraki istasyon kaçar)
"""

from dataclasses import dataclass
from typing import Optional

from app.models import WeatherInfo, WeatherCondition
from app.utils.logger import get_logger

logger = get_logger("DynamicBuffer")


# =============================================================================
# CONSTANTS
# =============================================================================

BASE_SAFETY_PERCENT = 5.0   # %5 zemin pay
MAX_BUFFER_PERCENT = 20.0   # üst sınır (UI'da kafa kıracak kadar yüksek olmasın)

# Hava durumu cezaları
WIND_THRESHOLD_MPS = 8.0
WIND_PENALTY = 3.0
PRECIPITATION_PENALTY = 2.0  # rain veya snow

# Yokuş cezası: 100m gain başına +%1
ELEVATION_PENALTY_PER_100M = 1.0
ELEVATION_PENALTY_CAP = 5.0

# Trafik cezaları
TRAFFIC_HEAVY_THRESHOLD = 1.3  # duration_in_traffic / duration
TRAFFIC_HEAVY_PENALTY = 2.0
TRAFFIC_MODERATE_THRESHOLD = 1.1
TRAFFIC_MODERATE_PENALTY = 1.0

# Asimetri çarpanları
FINAL_LEG_MULTIPLIER = 1.5    # son leg → tow truck riski → daha yüksek pay
MID_LEG_MULTIPLIER = 0.7      # ortadaki leg → bir sonraki istasyona ulaşamama → düşük pay


# =============================================================================
# DATA CLASS
# =============================================================================

@dataclass
class BufferContext:
    """Bir leg için buffer hesabı girdileri."""
    leg_distance_km: float
    leg_kwh_estimate: float
    is_final_leg: bool
    weather: Optional[WeatherInfo] = None
    elevation_gain_m: float = 0.0
    traffic_factor: float = 1.0          # 1.0 = trafik yok, 1.3+ = ağır trafik
    user_anxiety_factor: float = 0.0     # 0.0-5.0 (UI'dan ileride alınabilir)


# =============================================================================
# CALCULATION
# =============================================================================

def calculate_dynamic_buffer(ctx: BufferContext) -> float:
    """
    Bir leg için dinamik güvenlik payını hesapla (% cinsinden).

    Returns:
        SOC % (örn. 7.3 → arrival_soc'a +%7.3 eklenir)
    """
    weather_penalty = _weather_penalty(ctx.weather)
    elevation_penalty = min(ELEVATION_PENALTY_CAP, ctx.elevation_gain_m / 100.0 * ELEVATION_PENALTY_PER_100M)
    traffic_penalty = _traffic_penalty(ctx.traffic_factor)
    anxiety = max(0.0, min(5.0, ctx.user_anxiety_factor))

    raw_buffer = BASE_SAFETY_PERCENT + weather_penalty + elevation_penalty + traffic_penalty + anxiety

    # Asimetri: son leg vs orta leg
    multiplier = FINAL_LEG_MULTIPLIER if ctx.is_final_leg else MID_LEG_MULTIPLIER
    buffer = raw_buffer * multiplier

    final_buffer = min(MAX_BUFFER_PERCENT, max(0.0, buffer))

    logger.debug(
        f"buffer leg(final={ctx.is_final_leg}, dist={ctx.leg_distance_km:.1f}km): "
        f"base={BASE_SAFETY_PERCENT} + weather={weather_penalty:.1f} + "
        f"elev={elevation_penalty:.1f} + traffic={traffic_penalty:.1f} + "
        f"anxiety={anxiety:.1f} -> raw={raw_buffer:.1f} x {multiplier} = {final_buffer:.1f}%"
    )

    return final_buffer


def _weather_penalty(weather: Optional[WeatherInfo]) -> float:
    if weather is None:
        return 0.0
    penalty = 0.0
    if weather.wind_speed_mps and weather.wind_speed_mps > WIND_THRESHOLD_MPS:
        penalty += WIND_PENALTY
    if weather.condition in (WeatherCondition.RAIN, WeatherCondition.SNOW):
        penalty += PRECIPITATION_PENALTY
    return penalty


def _traffic_penalty(traffic_factor: float) -> float:
    if traffic_factor >= TRAFFIC_HEAVY_THRESHOLD:
        return TRAFFIC_HEAVY_PENALTY
    if traffic_factor >= TRAFFIC_MODERATE_THRESHOLD:
        return TRAFFIC_MODERATE_PENALTY
    return 0.0
