"""
Route Planner — Backward Compatibility Proxy
==============================================

Bu dosya geriye uyumluluk için korunmaktadır.
Tüm mantık app.route_planning/ paketine taşındı.

Gerçek orkestrasyon: app.route_planning.orchestrator
"""

# Ana fonksiyon
from app.route_planning.orchestrator import plan_route

# Backward compat: testler ve diğer modüller bu isimleri import eder
from app.route_planning.orchestrator import (
    _resolve_defaults,
    _calculate_base_soc_params,
    _create_error_response,
    _extract_weather_from_forecast,
)

# Sabitler — testler bunları route_planner'dan import eder
from app.constants import (
    HARD_MIN_SOC,
    TARGET_ARRIVAL_SOC,
    TARGET_CHARGE_MIN_SOC,
    MIN_CHARGE_THRESHOLD_PERCENT,
    DEFAULT_PASSENGER_COUNT,
    DEFAULT_CHILD_COUNT,
    DEFAULT_EXTRA_LOAD_KG,
    DEFAULT_TEMPERATURE_C,
    MIN_SOC_RANGE,
    TARGET_SOC_RANGE,
    ARRIVAL_SOC_RANGE
)
