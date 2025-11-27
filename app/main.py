"""
Akıllı EV Rota Asistanı API - v2.0
===================================

V1.3 Enterprise FastAPI Application

Özellikler:
- Multi-stop route optimization
- Detailed error handling with debug info
- Test endpoints for development
- CORS support for frontend
- Structured logging with request tracking

Endpoints:
- GET  /           - API info & redirection
- GET  /health     - Health check
- POST /optimize_route - Main route optimization
- GET  /test       - Development test endpoint
- GET  /debug      - Debug information
"""

import time
import traceback
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.models import (
    RouteRequest, 
    MultiStopRouteResponse, 
    GeoPoint
)
from app.route_planner import plan_full_route
from app.services.base_service import ExternalAPIError
from app.services.google_service import google_maps
from app.utils.logger import get_logger
from app.utils.config_manager import config
from app.consumption_engine.vehicle_models import get_vehicle_model, VEHICLE_DB


# =============================================================================
# LIFECYCLE & SETUP
# =============================================================================

logger = get_logger("main_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle"""
    logger.info(
        "EV Route Optimizer API starting up",
        version="v1.3",
        environment=config.get_environment(),
        debug_mode=config.log_level() == "DEBUG"
    )
    
    # Log available vehicles
    logger.info(
        "Available vehicle models",
        vehicle_count=len(VEHICLE_DB),
        vehicle_ids=list(VEHICLE_DB.keys())
    )
    
    yield
    
    logger.info("EV Route Optimizer API shutting down")


# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

app = FastAPI(
    title="Akıllı EV Rota Asistanı API",
    description="Multi-stop EV route optimization with charging station planning",
    version="1.3.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Production'da kısıtla
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# =============================================================================
# ROOT ROUTE - Serve index.html
# =============================================================================

@app.get("/")
async def read_index():
    """Serve the main index.html file"""
    from fastapi.responses import FileResponse
    return FileResponse("static/index.html")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _format_geopoint(point: GeoPoint) -> str:
    """GeoPoint'i string formatına çevir"""
    return f"{point.lat},{point.lon}"


def _create_error_response(
    status: str, 
    message: str, 
    debug_info: Dict[str, Any] = None
) -> MultiStopRouteResponse:
    """Standart hata response'u oluştur"""
    return MultiStopRouteResponse(
        status=status,
        total_distance_km=0,
        total_duration_minutes=0,
        total_co2_savings_kg=0,
        legs=[],
        message=message,
        debug_info=debug_info or {}
    )


def _extract_request_details(request: RouteRequest) -> Dict[str, Any]:
    """Request detaylarını loglama için çıkar"""
    return {
        "start": _format_geopoint(request.start_location),
        "end": _format_geopoint(request.end_location),
        "vehicle": request.vehicle_model_id,
        "initial_soc": request.current_soc_percent,
        "extra_load": request.extra_load_kg,
        "passengers": request.passenger_count,
        "departure_time": request.departure_time_iso
    }


# =============================================================================
# MAIN ENDPOINTS
# =============================================================================

@app.get("/", tags=["Info"])
async def root():
    """API ana sayfası - Bilgi ve yönlendirme"""
    return {
        "message": "Akıllı EV Rota Asistanı API'ye Hoş Geldiniz!",
        "version": "v1.3.0",
        "environment": config.get_environment(),
        "web_interface": "/static/index.html",
        "documentation": "/docs",
        "redoc": "/redoc",
        "health_check": "/health",
        "endpoints": {
            "optimize_route": "POST /optimize_route",
            "test": "GET /test",
            "debug": "GET /debug"
        },
        "available_vehicles": list(VEHICLE_DB.keys())
    }


@app.get("/health", tags=["Monitoring"])
async def health():
    """Detaylı sağlık kontrolü"""
    try:
        # Test vehicle model loading
        test_vehicle = get_vehicle_model("mg4_51kwh")
        
        return {
            "status": "ok",
            "version": "v1.3.0",
            "service": "Akıllı EV Rota Asistanı",
            "environment": config.get_environment(),
            "timestamp": time.time(),
            "components": {
                "vehicle_models": "ok",
                "route_planner": "ok",
                "logger": "ok",
                "config_manager": "ok"
            },
            "test_vehicle": {
                "model": test_vehicle.model_name,
                "battery_kwh": test_vehicle.battery_capacity_kwh
            }
        }
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")


@app.post("/optimize_route", response_model=MultiStopRouteResponse, tags=["Routing"])
async def optimize_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    Ana rota optimizasyonu endpoint'i
    
    V1.3 Enterprise mimarisine göre multi-stop rota planlaması yapar.
    Şarj durakları, tüketim hesaplaması ve CO2 tasarrufu dahil.
    """
    request_start_time = time.time()
    request_id = f"req_{int(request_start_time * 1000)}"
    
    request_details = _extract_request_details(request)
    
    logger.info(
        f"[{request_id}] Route optimization request received",
        **request_details
    )
    
    try:
        # Request validation
        if request.current_soc_percent <= 0:
            raise ValueError("initial_soc_percent must be greater than 0")
        
        if request.current_soc_percent > 100:
            raise ValueError("initial_soc_percent must be less than or equal to 100")
        
        # Route planning
        logger.debug(
            f"[{request_id}] Starting route planning",
            step="planning_start"
        )
        
        plan = await plan_full_route(request)
        
        planning_duration = time.time() - request_start_time
        
        logger.info(
            f"[{request_id}] Route planning completed successfully",
            status=plan.status,
            total_distance_km=plan.total_distance_km,
            total_duration_min=plan.total_duration_minutes,
            consumption_kwh=sum(leg.consumption_kwh for leg in plan.legs),
            charge_stops=len([leg for leg in plan.legs if leg.type == "charge"]),
            processing_time_ms=round(planning_duration * 1000, 1)
        )
        
        # Add debug info to response
        if config.is_debug():
            plan.debug_info = {
                "request_id": request_id,
                "processing_time_ms": round(planning_duration * 1000, 1),
                "request_details": request_details,
                "vehicle_info": {
                    "model": request.vehicle_model_id,
                    "battery_kwh": get_vehicle_model(request.vehicle_model_id).battery_capacity_kwh
                }
            }
        
        return plan
        
    except ValueError as e:
        # Validation errors
        logger.warning(
            f"[{request_id}] Request validation failed",
            error=str(e),
            error_type="validation_error"
        )
        return _create_error_response(
            status="error_invalid_request",
            message=f"Geçersiz istek: {str(e)}",
            debug_info={
                "request_id": request_id,
                "request_details": request_details
            }
        )
        
    except ExternalAPIError as e:
        # External API errors (Google, OCM, Weather)
        logger.error(
            f"[{request_id}] External API error",
            api_source=e.source,
            status_code=e.status_code,
            detail=str(e),
            processing_time_ms=round((time.time() - request_start_time) * 1000, 1)
        )
        return _create_error_response(
            status=f"error_api_failed_{e.source.lower()}",
            message=f"Dış API hatası ({e.source}): {str(e)}",
            debug_info={
                "request_id": request_id,
                "api_source": e.source,
                "api_status_code": e.status_code,
                "processing_time_ms": round((time.time() - request_start_time) * 1000, 1)
            }
        )
        
    except Exception as e:
        # Unexpected errors
        error_traceback = traceback.format_exc()
        
        logger.critical(
            f"[{request_id}] Unexpected server error",
            error=str(e),
            error_type=type(e).__name__,
            processing_time_ms=round((time.time() - request_start_time) * 1000, 1),
            traceback=error_traceback
        )
        
        # In debug mode, return full error details
        debug_info = {
            "request_id": request_id,
            "error_type": type(e).__name__,
            "processing_time_ms": round((time.time() - request_start_time) * 1000, 1),
            "request_details": request_details
        }
        
        if config.is_debug():
            debug_info.update({
                "error_message": str(e),
                "traceback": error_traceback
            })
        
        return _create_error_response(
            status="error_internal_server",
            message=f"Sunucu hatası: {str(e)}" if config.is_debug() else "İç sunucu hatası",
            debug_info=debug_info
        )


# =============================================================================
# DEVELOPMENT & TEST ENDPOINTS
# =============================================================================

@app.get("/test", tags=["Development"])
async def test_endpoint():
    """Development test endpoint - sistem durumu hakkında detaylı bilgi"""
    try:
        # Test route planning with sample data
        sample_request = RouteRequest(
            start_location=GeoPoint(lat=41.0, lon=29.0),
            end_location=GeoPoint(lat=39.0, lon=32.0),
            vehicle_model_id="mg4_51kwh",
            current_soc_percent=80.0,
            extra_load_kg=50.0
        )
        
        return {
            "status": "test_mode_active",
            "environment": config.get_environment(),
            "debug_mode": config.is_debug_mode(),
            "available_vehicles": {
                vid: {
                    "model": vehicle.model_name,
                    "battery_kwh": vehicle.battery_capacity_kwh,
                    "consumption_wh_km": vehicle.base_consumption_wh_km
                }
                for vid, vehicle in VEHICLE_DB.items()
            },
            "sample_request": sample_request.model_dump(),
            "api_keys_configured": {
                "google": bool(config.get_google_api_key()),
                "openweather": bool(config.get_openweather_api_key()),
                "ocm": bool(config.get_ocm_api_key())
            }
        }
        
    except Exception as e:
        logger.error("Test endpoint failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Test failed: {str(e)}")


@app.get("/debug", tags=["Development"])
async def debug_info():
    """Debug bilgileri - sadece development modunda"""
    if not config.is_debug_mode():
        raise HTTPException(status_code=404, detail="Debug mode not enabled")
    
    return {
        "debug_info": {
            "environment": config.get_environment(),
            "config_dump": config.dump_config(),
            "logger_config": {
                "level": "DEBUG",
                "structured_logging": True
            },
            "vehicle_database": VEHICLE_DB,
            "api_endpoints": [
                {"method": "GET", "path": "/", "description": "API info"},
                {"method": "GET", "path": "/health", "description": "Health check"},
                {"method": "POST", "path": "/optimize_route", "description": "Route optimization"},
                {"method": "GET", "path": "/test", "description": "Development test"},
                {"method": "GET", "path": "/debug", "description": "Debug information"}
            ]
        }
    }


@app.get("/validate/{vehicle_id}", tags=["Development"])
async def validate_vehicle(vehicle_id: str):
    """Vehicle model validation endpoint"""
    try:
        vehicle = get_vehicle_model(vehicle_id)
        return {
            "status": "valid",
            "vehicle": {
                "model": vehicle.model_name,
                "battery_kwh": vehicle.battery_capacity_kwh,
                "consumption_wh_km": vehicle.base_consumption_wh_km,
                "curb_weight_kg": vehicle.curb_weight_kg,
                "auxiliary_power_kw": vehicle.auxiliary_power_kw
            }
        }
    except ValueError as e:
        return {
            "status": "invalid",
            "error": str(e),
            "available_vehicles": list(VEHICLE_DB.keys())
        }


@app.get("/geocode", tags=["Utilities"])
async def geocode_address(address: str):
    """
    Adres string'ini koordinata çevirir.
    
    Frontend'in GeoPoint formatında koordinat alması için kullanılır.
    
    Args:
        address: Adres string'i (örn: "Istanbul, Turkey")
        
    Returns:
        GeoPoint: {lat, lon} koordinatları
    """
    try:
        geo_point = await google_maps.geocode(address)
        return {
            "status": "success",
            "address": address,
            "location": {
                "lat": geo_point.lat,
                "lon": geo_point.lon
            }
        }
    except ExternalAPIError as e:
        logger.error(f"Geocoding failed for address: {address}", error=str(e))
        raise HTTPException(
            status_code=400,
            detail=f"Adres bulunamadı: {address}"
        )
    except Exception as e:
        logger.error(f"Unexpected geocoding error: {address}", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Geocoding hatası"
        )


if __name__ == "__main__":
    import uvicorn
    
    logger.info("Starting development server")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
