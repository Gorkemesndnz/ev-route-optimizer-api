import time
import traceback
import uuid
from typing import Dict, Any, Optional

from fastapi import APIRouter
from app.models.route_models import RouteRequest, MultiStopRouteResponse
from app.services.route_service import plan_route
from app.services.base_service import ExternalAPIError
from app.utils.logger import get_logger
from app.core.config import config
from app.infrastructure.vehicle_catalog import get_vehicle_model
from app.core.api_response import ApiResponse

router = APIRouter(tags=["Routing"])
logger = get_logger("optimize_router")

def _format_geopoint(point) -> str:
    return f"{point.lat},{point.lon}"

def _extract_request_details(request: RouteRequest) -> Dict[str, Any]:
    return {
        "start": _format_geopoint(request.start_location),
        "end": _format_geopoint(request.end_location),
        "vehicle": request.vehicle_model_id,
        "initial_soc": request.current_soc_percent,
        "extra_load": request.extra_load_kg,
        "passengers": request.passenger_count,
        "departure_time": request.departure_time_iso
    }

def _create_error_response(
    status: str,
    message: str,
    debug_info: Optional[Dict[str, Any]] = None
) -> MultiStopRouteResponse:
    return MultiStopRouteResponse(
        status=status,
        total_distance_km=0,
        total_duration_minutes=0,
        total_co2_savings_kg=0,
        legs=[],
        message=message,
        debug_info=debug_info or {}
    )

@router.post("/optimize_route", response_model=ApiResponse[MultiStopRouteResponse])
async def optimize_route(request: RouteRequest) -> ApiResponse[MultiStopRouteResponse]:
    """Ana rota optimizasyonu endpoint'i"""
    request_start_time = time.time()
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    request_details = _extract_request_details(request)

    logger.info(f"[{request_id}] Route optimization request received", **request_details)

    try:
        logger.debug(f"[{request_id}] Starting route planning", step="planning_start")
        plan = await plan_route(request)
        planning_duration = time.time() - request_start_time
        
        logger.info(
            f"[{request_id}] Route planning completed successfully",
            status=plan.status,
            charge_stops=plan.charge_stops,
            processing_time_ms=round(planning_duration * 1000, 1)
        )
        
        if config.is_debug():
            plan.debug_info = {
                "request_id": request_id,
                "processing_time_ms": round(planning_duration * 1000, 1),
                "request_details": request_details
            }
        
        return ApiResponse.ok(plan)
        
    except ValueError as e:
        logger.warning(f"[{request_id}] Request validation failed", error=str(e))
        return ApiResponse.fail(f"Geçersiz istek: {str(e)}")
        
    except ExternalAPIError as e:
        logger.error(f"[{request_id}] External API error", api_source=e.source, status_code=e.status_code, detail=str(e))
        return ApiResponse.fail(f"Dış API hatası ({e.source}): {str(e)}")
        
    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.critical(f"[{request_id}] Unexpected server error", error=str(e), traceback=error_traceback)
        return ApiResponse.fail(f"Sunucu hatası: {str(e)}" if config.is_debug() else "İç sunucu hatası")
