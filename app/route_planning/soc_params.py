"""
SOC Parameters & Defaults
===========================

Dinamik SOC parametreleri, varsayılan değer çözümleme ve hata response oluşturma.

Orijinal: route_planner.py → _resolve_defaults, _calculate_base_soc_params, _create_error_response
"""

from typing import Optional
from app.models import RouteRequest, MultiStopRouteResponse
from app.utils.logger import get_logger
from app.models.route_models import ChargingFrequency
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
    request: RouteRequest,
    safe_harbor_result=None,
) -> tuple:
    """
    🔧 V3.5: Dinamik SOC parametreleri - gereksiz durak önleme.
    🔧 V4.0: Safe Harbor entegrasyonu.
    🔧 V4.3 (CB-1/CB-2 fix): smart_plan_enabled=True iken manuel SOC override'ları YOK SAYILIR.
              Aksi halde kullanıcının absurd değeri (örn. min=44%) Pareto solver'ı kandırır
              ve 10+ gereksiz şarj durağı üretir.

    MANTIK:
    1. HARD_MIN_SOC (8%) = Mutlak minimum, bunun altına düşmemeli
    2. TARGET_ARRIVAL_SOC (15%) = Tercih edilen, ama zorunlu değil
    3. Eğer HARD_MIN_SOC üzerinde varabiliyorsak, şarj ATLANIR
    4. Safe Harbor aktifse, arrival_soc = dynamic_min_arrival_soc
    5. smart_plan_enabled=True → manuel override'lar yok sayılır (engine karar verir)

    Returns:
        (charge_min_soc, user_target_soc_override, arrival_soc)
    """
    # 🚨 V4.3 — Smart Plan vs Manuel Override Tutarsızlığı
    # API contract: smart_plan_enabled=True iken motor karar verir, manuel SOC alanları İHMAL edilir.
    # Aksi halde kullanıcı absurd değer (charge_min_soc=44%) gönderirse plan bozulur.
    smart_plan = bool(getattr(request, "smart_plan_enabled", False))
    if smart_plan:
        if request.charge_min_soc_percent is not None:
            logger.warning(
                f"smart_plan_enabled=True; charge_min_soc_percent={request.charge_min_soc_percent}% "
                f"YOK SAYILIYOR (motor kendisi belirler). Manuel kontrol için smart_plan_enabled=False gönder."
            )
            request.charge_min_soc_percent = None
        if request.target_arrival_soc_percent is not None:
            logger.warning(
                f"smart_plan_enabled=True; target_arrival_soc_percent={request.target_arrival_soc_percent}% "
                f"YOK SAYILIYOR."
            )
            request.target_arrival_soc_percent = None
        if request.charge_target_soc_percent is not None:
            logger.warning(
                f"smart_plan_enabled=True; charge_target_soc_percent={request.charge_target_soc_percent}% "
                f"YOK SAYILIYOR."
            )
            request.charge_target_soc_percent = None
        # 🔧 F-20: charging_frequency hint'i de yok say (sadece OPTIMAL olmayan değerler loglanır).
        # F-9 üç SOC alanını sıfırlar ama charging_frequency hâlâ OPTIMAL olduğundan
        # charge_min_soc_hint=17 devreye giriyordu — distance-based dynamic default (8/10/12/15)
        # yerine 17 kullanılması uzun rotalarda daha sık hotspot tetikliyordu.
        if getattr(request, "charging_frequency", None) and request.charging_frequency != ChargingFrequency.OPTIMAL:
            logger.warning(
                f"smart_plan_enabled=True; charging_frequency={request.charging_frequency} "
                f"YOK SAYILIYOR — motor distance-based default kullanacak."
            )
        # None yap → aşağıdaki elif koluna düşmez, distance-based default seçilir
        request.charging_frequency = None
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
    
    # 🔧 V4.0: Safe Harbor Override — Uzak varış noktalarında minimum varış SOC'u yükselt
    if safe_harbor_result and not safe_harbor_result.is_destination_covered:
        safe_harbor_soc = safe_harbor_result.dynamic_min_arrival_soc
        if safe_harbor_soc > arrival_soc:
            logger.info(
                f"🏠 Safe Harbor OVERRIDE: arrival_soc {arrival_soc:.1f}% → "
                f"{safe_harbor_soc:.1f}% (return trip: "
                f"{safe_harbor_result.return_soc_needed:.1f}% + "
                f"buffer {safe_harbor_result.dynamic_min_arrival_soc - safe_harbor_result.return_soc_needed:.1f}%)"
            )
            arrival_soc = safe_harbor_soc
    
    # 2. Şarj Eşiği (charge_min_soc) - 🔧 V3.5: Bacak uzunluğuna göre esnek
    # 🔧 V4.3: Manuel mod (smart_plan=False) iken kullanıcı override'ı kabul ama CLAMP edilir.
    SAFE_CHARGE_MIN_UPPER_BOUND = 35.0  # %35 üstü = aşırı sık şarj durağı tetikler
    if request.charge_min_soc_percent is not None:
        raw_min = request.charge_min_soc_percent
        if raw_min > SAFE_CHARGE_MIN_UPPER_BOUND:
            logger.warning(
                f"charge_min_soc_percent={raw_min}% mantıksız yüksek; "
                f"%{SAFE_CHARGE_MIN_UPPER_BOUND}'e clamp ediliyor (gereksiz şarj durağını önlemek için)"
            )
            charge_min_soc = SAFE_CHARGE_MIN_UPPER_BOUND
        else:
            charge_min_soc = raw_min
    elif getattr(request, "charging_frequency", None):
        charge_min_soc = request.charging_frequency.charge_min_soc_hint
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
    # 🔧 V4.3 (CB-5 fix): charging_frequency.charge_target_soc_hint Pareto path'ı BYPASS ediyordu.
    # Smart plan iken hint'i override olarak kullanma — sadece açık kullanıcı override'ı geçerli.
    # Aksi halde orchestrator._run_soc_simulation `user_target_soc_override is not None` görüp
    # Pareto solver'ı atlıyor ve tüm stop'lar 80% target ile sonuçlanıyor (gereksiz fazla durak).
    user_target_soc_override = request.charge_target_soc_percent
    if user_target_soc_override is None and not smart_plan and getattr(request, "charging_frequency", None):
        user_target_soc_override = request.charging_frequency.charge_target_soc_hint
    
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
