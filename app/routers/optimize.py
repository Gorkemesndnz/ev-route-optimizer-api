import time
import traceback
import uuid
from typing import Dict, Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
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


def _external_api_error_code(error: ExternalAPIError) -> str:
    if getattr(error, "code", None):
        return error.code
    if error.source == "SnapToRoads":
        return "SNAP_TO_ROADS_FAILED"
    if error.source == "PolylineCorridor":
        return "POLYLINE_DECODE_FAILED"
    if error.source == "HotspotAlignment":
        return "HOTSPOT_ALIGNMENT_FAILED"
    return "EXTERNAL_API_ERROR"


def _external_api_status_code(error: ExternalAPIError) -> int:
    if _external_api_error_code(error) == "TOO_MANY_WAYPOINTS":
        return 400
    return 502


def _route_plan_error_status(plan: MultiStopRouteResponse) -> Optional[tuple[int, str]]:
    if plan.status == "NO_ROUTE_WITH_CONSTRAINTS":
        return 422, "NO_ROUTE_WITH_CONSTRAINTS"
    if plan.status.startswith("error"):
        return 502, "ROUTE_PLANNING_FAILED"
    return None

@router.post("/internal/routes/optimize", response_model=ApiResponse[MultiStopRouteResponse])
@router.post(
    "/optimize_route",
    response_model=ApiResponse[MultiStopRouteResponse],
    include_in_schema=False,
)
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

        error_status = _route_plan_error_status(plan)
        if error_status:
            status_code, error_code = error_status
            logger.error(
                f"[{request_id}] Route planning returned error status",
                status=plan.status,
                message=plan.message,
                processing_time_ms=round(planning_duration * 1000, 1),
            )
            return JSONResponse(
                status_code=status_code,
                content=ApiResponse.fail(
                    plan.message or "Rota hesaplanamadi",
                    code=error_code,
                ).model_dump(mode="json"),
            )

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
        return JSONResponse(
            status_code=400,
            content=ApiResponse.fail(
                f"Geçersiz istek: {str(e)}",
                code="VALIDATION_ERROR",
            ).model_dump(mode="json"),
        )

    except ExternalAPIError as e:
        logger.error(f"[{request_id}] External API error", api_source=e.source, status_code=e.status_code, detail=str(e))
        return JSONResponse(
            status_code=_external_api_status_code(e),
            content=ApiResponse.fail(
                f"Dış API hatası ({e.source}): {str(e)}",
                code=_external_api_error_code(e),
            ).model_dump(mode="json"),
        )

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.critical(f"[{request_id}] Unexpected server error", error=str(e), traceback=error_traceback)
        detail = f"Sunucu hatası: {str(e)}" if config.is_debug() else "İç sunucu hatası"
        return JSONResponse(
            status_code=500,
            content=ApiResponse.fail(detail, code="INTERNAL_ERROR").model_dump(mode="json"),
        )
