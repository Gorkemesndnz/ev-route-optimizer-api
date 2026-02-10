"""
SOC Parameters & Defaults
===========================

Dinamik SOC parametreleri, varsayılan değer çözümleme ve hata response oluşturma.

Orijinal: route_planner.py → _resolve_defaults, _calculate_base_soc_params, _create_error_response
"""

from typing import Optional
from app.models import RouteRequest, MultiStopRouteResponse
from app.utils.logger import get_logger
from app.constants import (
    HARD_MIN_SOC,
    TARGET_ARRIVAL_SOC,
    TARGET_CHARGE_MIN_SOC,
    MIN_CHARGE_THRESHOLD_PERCENT,
    DEFAULT_PASSENGER_COUNT,
    DEFAULT_CHILD_COUNT,
    DEFAULT_EXTRA_LOAD_KG,
)

logger = get_logger("route_planning.soc_params")


def resolve_defaults(request: RouteRequest) -> tuple:
    """
    Yolcu ve yük için varsayılanları çöz.
    
    Returns:
        (passenger_count, child_count, extra_load_kg)
    """
    passenger_count = request.passenger_count if request.passenger_count is not None else DEFAULT_PASSENGER_COUNT
    child_count = request.child_count if request.child_count is not None else DEFAULT_CHILD_COUNT
    extra_load_kg = request.extra_load_kg if request.extra_load_kg is not None else DEFAULT_EXTRA_LOAD_KG
    
    return passenger_count, child_count, extra_load_kg


def calculate_base_soc_params(
    battery_kwh: float,
    start_soc: float,
    total_consumption_kwh: float,
    route_distance_km: float,
    request: RouteRequest
) -> tuple:
    """
    🔧 V3.5: Dinamik SOC parametreleri - gereksiz durak önleme.
    
    MANTIK:
    1. HARD_MIN_SOC (8%) = Mutlak minimum, bunun altına düşmemeli
    2. TARGET_ARRIVAL_SOC (15%) = Tercih edilen, ama zorunlu değil
    3. Eğer HARD_MIN_SOC üzerinde varabiliyorsak, şarj ATLANIR
    
    Returns:
        (charge_min_soc, user_target_soc_override, arrival_soc)
    """
    # Mevcut enerji ve ihtiyaç
    current_energy_kwh = (start_soc / 100) * battery_kwh
    
    # Tahmini varış SOC'u hesapla (şarjsız)
    projected_arrival_soc = ((current_energy_kwh - total_consumption_kwh) / battery_kwh) * 100
    
    # 🔧 V3.5: "CAN I MAKE IT?" KONTROLÜ
    can_reach_with_hard_min = projected_arrival_soc >= HARD_MIN_SOC
    can_reach_with_target = projected_arrival_soc >= TARGET_ARRIVAL_SOC
    
    logger.info(
        f"Projected arrival SOC: {projected_arrival_soc:.1f}% "
        f"(hard_min={HARD_MIN_SOC}%, target={TARGET_ARRIVAL_SOC}%, "
        f"can_reach_hard={can_reach_with_hard_min}, can_reach_target={can_reach_with_target})"
    )
    
    # 1. Varış SOC - DİNAMİK
    if request.target_arrival_soc_percent is not None:
        arrival_soc = request.target_arrival_soc_percent
    else:
        if can_reach_with_hard_min:
            arrival_soc = max(HARD_MIN_SOC, projected_arrival_soc)
            logger.info(f"Can reach destination without charging! arrival_soc={arrival_soc:.1f}%")
        else:
            arrival_soc = TARGET_ARRIVAL_SOC
    
    # 2. Şarj Eşiği (charge_min_soc) - 🔧 V3.5: Bacak uzunluğuna göre esnek
    if request.charge_min_soc_percent is not None:
        charge_min_soc = request.charge_min_soc_percent
    else:
        if route_distance_km < 100:
            charge_min_soc = HARD_MIN_SOC
        elif route_distance_km < 200:
            charge_min_soc = MIN_CHARGE_THRESHOLD_PERCENT
        elif route_distance_km < 400:
            charge_min_soc = TARGET_CHARGE_MIN_SOC
        else:
            charge_min_soc = TARGET_ARRIVAL_SOC
    
    # 3. Kullanıcı target_soc override'ı (None ise optimizer belirler)
    user_target_soc_override = request.charge_target_soc_percent
    
    logger.info(
        f"Base SOC params: min={charge_min_soc}%, arrival={arrival_soc}% "
        f"(user_target_override={user_target_soc_override})"
    )
    
    return charge_min_soc, user_target_soc_override, arrival_soc


def create_error_response(status: str, message: str = None) -> MultiStopRouteResponse:
    """Hata durumunda minimal MultiStopRouteResponse oluştur."""
    return MultiStopRouteResponse(
        status=status,
        total_distance_km=0,
        total_duration_minutes=0,
        total_co2_savings_kg=0,
        legs=[],
        message=message
    )
