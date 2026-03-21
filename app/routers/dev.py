import time
from typing import Dict, Any

from fastapi import APIRouter
from app.core.config import config
from app.utils.logger import get_logger
from app.infrastructure.vehicle_catalog import get_vehicle_model, get_available_vehicle_ids
from app.models.route_models import RouteRequest, GeoPoint
from app.core.api_response import ApiResponse

router = APIRouter(tags=["Info & Dev"])
logger = get_logger("dev_router")

@router.get("/api/maps-key", response_model=ApiResponse[Dict[str, str]])
async def maps_key() -> ApiResponse[Dict[str, str]]:
    """Frontend Google Maps JS yüklemesi için API key döner."""
    key = config.get_google_api_key()
    if not key:
        return ApiResponse.fail("Google API key not configured")
    return ApiResponse.ok({"key": key})

@router.get("/api/info", response_model=ApiResponse[Dict[str, Any]])
async def api_info() -> ApiResponse[Dict[str, Any]]:
    """API bilgi endpoint'i"""
    data = {
        "message": "Akıllı EV Rota Asistanı API'ye Hoş Geldiniz!",
        "version": "v2.0.0 (Stateless Compute via Refactored Routers)",
        "environment": config.get_environment()
    }
    return ApiResponse.ok(data)

@router.get("/health", response_model=ApiResponse[Dict[str, Any]])
async def health() -> ApiResponse[Dict[str, Any]]:
    """Detaylı sağlık kontrolü"""
    try:
        vehicle_ids = get_available_vehicle_ids()
        if not vehicle_ids:
            return ApiResponse.fail("No vehicles loaded in catalog")
        
        test_vehicle = get_vehicle_model(vehicle_ids[0])
        data = {
            "status": "ok",
            "environment": config.get_environment(),
            "timestamp": time.time(),
            "vehicle_catalog_count": len(vehicle_ids),
            "test_vehicle": {
                "model": test_vehicle.display_name,
                "battery_kwh": test_vehicle.battery_capacity_kwh
            }
        }
        return ApiResponse.ok(data)
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return ApiResponse.fail(f"Service unhealthy: {str(e)}")

@router.get("/test", response_model=ApiResponse[Dict[str, Any]])
async def test_endpoint() -> ApiResponse[Dict[str, Any]]:
    """Development test endpoint - sistem durumu hakkında detaylı bilgi"""
    if not config.is_debug():
        return ApiResponse.fail("Test endpoint sadece development modunda kullanılabilir")
    
    try:
        sample_request = RouteRequest(
            start_location=GeoPoint(lat=41.0, lon=29.0),
            end_location=GeoPoint(lat=39.0, lon=32.0),
            vehicle_model_id="abarth_500e_hatchback_2024",
            current_soc_percent=80.0,
            extra_load_kg=50.0
        )
        
        data = {
            "status": "test_mode_active",
            "environment": config.get_environment(),
            "debug_mode": config.is_debug(),
            "sample_request": sample_request.model_dump(),
            "api_keys_configured": {
                "google": bool(config.get_google_api_key()),
                "openweather": bool(config.get_openweather_api_key()),
                "ocm": bool(config.get_ocm_api_key())
            }
        }
        return ApiResponse.ok(data)
    except Exception as e:
        logger.error("Test endpoint failed", error=str(e))
        return ApiResponse.fail(f"Test failed: {str(e)}")

@router.get("/debug", response_model=ApiResponse[Dict[str, Any]])
async def debug_info() -> ApiResponse[Dict[str, Any]]:
    """Debug bilgileri"""
    if not config.is_debug():
        return ApiResponse.fail("Debug endpoint sadece development modunda kullanılabilir")
    
    data = {
        "environment": config.get_environment(),
        "config_dump": config.dump_config(),
        "logger_config": {"level": "DEBUG", "structured_logging": True},
        "vehicle_database_count": len(get_available_vehicle_ids())
    }
    return ApiResponse.ok(data)

@router.get("/validate/{vehicle_id}", response_model=ApiResponse[Dict[str, Any]])
async def validate_vehicle(vehicle_id: str) -> ApiResponse[Dict[str, Any]]:
    if not config.is_debug():
        return ApiResponse.fail("Validate endpoint sadece development modunda kullanılabilir")
    
    try:
        vehicle = get_vehicle_model(vehicle_id)
        data = {
            "vehicle": {
                "model": vehicle.display_name,
                "battery_kwh": vehicle.battery_capacity_kwh,
                "consumption_wh_km": vehicle.base_consumption_wh_km,
                "curb_weight_kg": vehicle.curb_weight_kg
            }
        }
        return ApiResponse.ok(data)
    except ValueError as e:
        return ApiResponse.fail(str(e))
