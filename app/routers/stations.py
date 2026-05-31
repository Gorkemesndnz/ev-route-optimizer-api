import time
from typing import List

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.core.api_response import ApiResponse
from app.models.route_models import (
    ChargerType,
    ConnectorInfo,
    GeoPoint,
    PlugType,
    RecalculateResponse,
    RouteRequest,
    StationFeedbackRequest,
    StationInfo,
    SwitchStationRequest,
)
from app.services.base_service import ExternalAPIError
from app.services.feedback_service import feedback_manager
from app.services.google_service import google_maps
from app.services.route_service import plan_route
from app.utils.logger import get_logger

router = APIRouter(tags=["Stations & Feedback"])
logger = get_logger("stations_router")


def _fail(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ApiResponse.fail(message, code=code).model_dump(mode="json"),
    )


@router.get("/api/map_stations", response_model=ApiResponse[List[StationInfo]])
async def get_map_stations(
    lat: float = Query(..., description="Map center latitude"),
    lon: float = Query(..., description="Map center longitude"),
    radius_km: float = Query(..., description="View radius in km"),
    zoom: int = Query(..., description="Map zoom level"),
) -> ApiResponse[List[StationInfo]]:
    """Dynamic map stations from Google Places cache."""
    try:
        raw_places = await google_maps.get_map_stations(
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            zoom=zoom,
        )
        stations = []
        for p in raw_places:
            location = p.get("geometry", {}).get("location", {})
            st_lat, st_lon = location.get("lat"), location.get("lng")
            if st_lat is None or st_lon is None:
                continue

            power_kw = p.get("max_power_kw", 0.0)
            c_type = ChargerType.HPC if power_kw >= 150 else (
                ChargerType.DC if power_kw >= 50 else ChargerType.AC
            )

            stations.append(
                StationInfo(
                    id=p.get("place_id", ""),
                    name=p.get("name", "Bilinmeyen Istasyon"),
                    operator="Google",
                    location=GeoPoint(lat=st_lat, lon=st_lon),
                    rating=p.get("rating", 0.0),
                    user_ratings_total=p.get("user_ratings_total", 0),
                    connectors=[
                        ConnectorInfo(
                            plug_type=PlugType.TYPE2,
                            charger_type=c_type,
                            power_kw=power_kw,
                            status="Available",
                            price_per_kwh=None,
                            currency="TRY",
                        )
                    ],
                    distance_from_route_km=0.0,
                    data_source="google",
                    place_id=p.get("place_id", ""),
                    vicinity=p.get("vicinity", ""),
                    is_open_now=True,
                )
            )
        return ApiResponse.ok(stations)
    except ExternalAPIError as e:
        logger.error(
            f"Map Stations external API error: source={e.source} status={e.status_code}",
            error=str(e),
        )
        return _fail(502, f"Dis API hatasi ({e.source})", "STATION_PROVIDER_ERROR")
    except Exception as e:
        logger.error(f"Map Stations Error: {e}")
        return _fail(500, "Istasyonlar getirilemedi", "INTERNAL_ERROR")


@router.post("/station_feedback", response_model=ApiResponse[RecalculateResponse])
async def station_feedback(request: StationFeedbackRequest) -> ApiResponse[RecalculateResponse]:
    """User station feedback and full-route recalculation."""
    request_id = f"feedback_{int(time.time() * 1000)}"
    logger.info(f"[{request_id}] Station feedback received", station_id=request.station_id)

    try:
        feedback_result = await feedback_manager.report_station(
            station_id=request.station_id,
            user_id=request_id,
            reason=request.feedback_type.value,
        )

        new_route_request = RouteRequest(
            start_location=request.current_location,
            end_location=request.destination,
            vehicle_model_id=request.vehicle_model_id,
            current_soc_percent=request.current_soc_percent,
        )
        new_route = await plan_route(new_route_request)

        feedback_msg = feedback_result["message"]
        if feedback_result["is_blocked"]:
            feedback_msg += " Istasyon gecici olarak bloklandi."

        return ApiResponse.ok(
            RecalculateResponse(
                status="success",
                message=f"Rota yeniden hesaplandi. {feedback_msg}",
                recalculate_type="full_route",
                route=new_route,
                affected_legs=list(range(len(new_route.legs))),
            )
        )

    except ValueError as e:
        logger.warning(f"[{request_id}] Feedback validation failed", error=str(e))
        return _fail(400, f"Gecersiz istek: {str(e)}", "VALIDATION_ERROR")
    except ExternalAPIError as e:
        logger.error(f"[{request_id}] Feedback external API error", api_source=e.source, detail=str(e))
        return _fail(502, f"Dis API hatasi ({e.source})", "EXTERNAL_API_ERROR")
    except Exception as e:
        logger.error(f"[{request_id}] Feedback processing failed", error=str(e))
        return _fail(500, f"Rota yeniden hesaplanamadi: {str(e)}", "INTERNAL_ERROR")


@router.post("/switch_station", response_model=ApiResponse[RecalculateResponse])
async def switch_station(request: SwitchStationRequest) -> ApiResponse[RecalculateResponse]:
    """Switch station by recalculating the full route."""
    request_id = f"switch_{int(time.time() * 1000)}"
    logger.info(
        f"[{request_id}] Station switch requested",
        original=request.original_station_id,
        new=request.new_station_id,
    )

    try:
        if request.original_station_id:
            await feedback_manager.report_station(
                station_id=request.original_station_id,
                user_id=request_id,
                reason="user_switched",
            )

        new_route_request = RouteRequest(
            start_location=request.current_location,
            end_location=request.destination,
            vehicle_model_id=request.vehicle_model_id,
            current_soc_percent=request.current_soc_percent,
        )
        new_route = await plan_route(new_route_request)

        return ApiResponse.ok(
            RecalculateResponse(
                status="success",
                message="Istasyon degistirildi ve rota yeniden hesaplandi.",
                recalculate_type="full_route",
                route=new_route,
                affected_legs=list(range(len(new_route.legs))),
            )
        )

    except ValueError as e:
        logger.warning(f"[{request_id}] Switch validation failed", error=str(e))
        return _fail(400, f"Gecersiz istek: {str(e)}", "VALIDATION_ERROR")
    except ExternalAPIError as e:
        logger.error(f"[{request_id}] Switch external API error", api_source=e.source, detail=str(e))
        return _fail(502, f"Dis API hatasi ({e.source})", "EXTERNAL_API_ERROR")
    except Exception as e:
        logger.error(f"[{request_id}] Station switch failed", error=str(e))
        return _fail(500, f"Istasyon degistirilemedi: {str(e)}", "INTERNAL_ERROR")
