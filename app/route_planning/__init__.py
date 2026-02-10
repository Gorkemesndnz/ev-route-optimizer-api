"""
Route Planning Sub-modules
===========================

route_planner.py'nin modüler alt bileşenleri.

Modüller:
- soc_params: SOC parametreleri ve varsayılan değerler
- weather_pipeline: Hava durumu pipeline (checkpoint + refinement)
- leg_builder: Multi-leg yapısı oluşturma
- response_builder: Final response + uyarılar + loglama
"""

from app.route_planning.soc_params import (
    resolve_defaults,
    calculate_base_soc_params,
    create_error_response,
)

from app.route_planning.weather_pipeline import (
    extract_weather_from_forecast,
    fetch_weather_checkpoints,
    refine_weather_pass2,
)

from app.route_planning.leg_builder import build_multi_legs

from app.route_planning.response_builder import (
    build_warnings,
    build_route_response,
    log_training_data,
)

from app.route_planning.orchestrator import plan_route
