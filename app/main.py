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
- GET  /           - Web arayüzü (index.html)
- GET  /api/info   - API bilgisi (JSON)
- GET  /health     - Health check
- POST /optimize_route - Main route optimization
- GET  /test       - Development test endpoint
- GET  /debug      - Debug information
"""

import time
import traceback
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.models import (
    RouteRequest, 
    MultiStopRouteResponse, 
    GeoPoint
)
from app.route_planner import plan_route
from app.services.base_service import ExternalAPIError, close_global_client
from app.services.google_service import google_maps
from app.utils.logger import get_logger
from app.utils.config_manager import config
from app.infrastructure.vehicle_catalog import get_vehicle_model, get_available_vehicle_ids
from app.services.feedback_service import feedback_manager


# =============================================================================
# LIFECYCLE & SETUP
# =============================================================================

logger = get_logger("main_api")

# Araç yönetimi .NET gateway'de yapıldığından yerel DB kaldırıldı
# vehicle_catalog = FileVehicleCatalog()


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
        vehicle_count=len(get_available_vehicle_ids()),
    )
    
    yield
    
    # Shutdown: HTTP client'ı kapat (kaynak sızıntısını önle)
    logger.info("EV Route Optimizer API shutting down")
    await close_global_client()


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

# CORS - Environment'a göre dinamik yapılandırma
_cors_origins = ["*"] if config.is_debug() else [
    "https://ev-route-optimizer.com",  # Production domain (gerektiğinde güncelle)
    "https://www.ev-route-optimizer.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"] if not config.is_debug() else ["*"],
    allow_headers=["Content-Type", "Authorization"] if not config.is_debug() else ["*"],
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

@app.get("/api/maps-key", tags=["Info"])
async def maps_key():
    """Frontend Google Maps JS yüklemesi için API key döner."""
    key = config.get_google_api_key()
    if not key:
        raise HTTPException(status_code=500, detail="Google API key not configured")
    return {"key": key}

@app.get("/api/info", tags=["Info"])
async def api_info():
    """API bilgi endpoint'i - JSON formatında API detayları"""
    return {
        "message": "Akıllı EV Rota Asistanı API'ye Hoş Geldiniz!",
        "version": "v1.3.0",
        "environment": config.get_environment(),
        "web_interface": "/",
        "documentation": "/docs",
        "redoc": "/redoc",
        "health_check": "/health",
        "endpoints": {
            "api_info": "GET /api/info",
            "optimize_route": "POST /optimize_route",
            "test": "GET /test",
            "debug": "GET /debug"
        },
        "available_vehicles": get_available_vehicle_ids()
    }


@app.get("/health", tags=["Monitoring"])
async def health():
    """Detaylı sağlık kontrolü"""
    try:
        # Test vehicle model loading
        vehicle_ids = get_available_vehicle_ids()
        if not vehicle_ids:
            raise RuntimeError("No vehicles loaded in catalog")
        test_vehicle = get_vehicle_model(vehicle_ids[0])
        
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
            "vehicle_catalog_count": len(vehicle_ids),
            "test_vehicle": {
                "model": test_vehicle.display_name,
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
        # Not: SOC validation Pydantic tarafından yapılır (ge=0, le=100 → 422)
        # Burada sadece iş mantığı kontrolü: SOC=0 ile rota planlamak anlamsız
        if request.current_soc_percent == 0:
            raise ValueError("Batarya tamamen boş (SOC=0) ile rota planlanamaz")
        
        # Route planning
        logger.debug(
            f"[{request_id}] Starting route planning",
            step="planning_start"
        )
        
        plan = await plan_route(request)
        
        planning_duration = time.time() - request_start_time
        
        logger.info(
            f"[{request_id}] Route planning completed successfully",
            status=plan.status,
            total_distance_km=plan.total_distance_km,
            total_duration_min=plan.total_duration_minutes,
            consumption_kwh=sum(leg.consumption_kwh for leg in plan.legs if hasattr(leg, 'consumption_kwh')),
            charge_stops=len([leg for leg in plan.legs if getattr(leg, 'type', '') == "charge"]),
            processing_time_ms=round(planning_duration * 1000, 1)
        )
        
        # Add debug info to response
        if config.is_debug():
            plan.debug_info = {
                "request_id": request_id,
                "processing_time_ms": round(planning_duration * 1000, 1),
                "request_details": request_details,
                "vehicle_info": {
                    "model": request.vehicle_spec.slug if request.vehicle_spec else request.vehicle_model_id,
                    "battery_kwh": request.vehicle_spec.battery_useable_kwh if request.vehicle_spec else get_vehicle_model(request.vehicle_model_id).battery_capacity_kwh
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
    """Development test endpoint - sistem durumu hakkında detaylı bilgi (sadece development)"""
    if not config.is_debug():
        raise HTTPException(status_code=403, detail="Test endpoint sadece development modunda kullanılabilir")
    
    try:
        # Test route planning with sample data
        sample_request = RouteRequest(
            start_location=GeoPoint(lat=41.0, lon=29.0),
            end_location=GeoPoint(lat=39.0, lon=32.0),
            vehicle_model_id="abarth_500e_hatchback_2024",
            current_soc_percent=80.0,
            extra_load_kg=50.0
        )
        
        return {
            "status": "test_mode_active",
            "environment": config.get_environment(),
            "debug_mode": config.is_debug(),
            "available_vehicles": {
                vid: {
                    "model": get_vehicle_model(vid).display_name,
                    "battery_kwh": get_vehicle_model(vid).battery_capacity_kwh,
                    "consumption_wh_km": get_vehicle_model(vid).base_consumption_wh_km
                }
                for vid in get_available_vehicle_ids()
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
    if not config.is_debug():
        raise HTTPException(status_code=403, detail="Debug endpoint sadece development modunda kullanılabilir")
    
    return {
        "debug_info": {
            "environment": config.get_environment(),
            "config_dump": config.dump_config(),
            "logger_config": {
                "level": "DEBUG",
                "structured_logging": True
            },
            "vehicle_database_count": len(get_available_vehicle_ids()),
            "api_endpoints": [
                {"method": "GET", "path": "/", "description": "Web interface (index.html)"},
                {"method": "GET", "path": "/api/info", "description": "API info (JSON)"},
                {"method": "GET", "path": "/health", "description": "Health check"},
                {"method": "POST", "path": "/optimize_route", "description": "Route optimization"},
                {"method": "GET", "path": "/test", "description": "Development test"},
                {"method": "GET", "path": "/debug", "description": "Debug information"}
            ]
        }
    }


@app.get("/validate/{vehicle_id}", tags=["Development"])
async def validate_vehicle(vehicle_id: str):
    """Vehicle model validation endpoint (sadece development)"""
    if not config.is_debug():
        raise HTTPException(status_code=403, detail="Validate endpoint sadece development modunda kullanılabilir")
    
    try:
        vehicle = get_vehicle_model(vehicle_id)
        return {
            "status": "valid",
            "vehicle": {
                "model": vehicle.display_name,
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
            "available_vehicles": get_available_vehicle_ids()
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


@app.get("/autocomplete", tags=["Utilities"])
async def autocomplete_address(query: str):
    """
    Kullanıcının girdiği metne göre adres önerileri sunar.
    
    Args:
        query: Aranacak adres metni (örn: "Düzce")
        
    Returns:
        Kullanıcıya gösterilecek adres önerileri listesi
    """
    if len(query) < 3:
        return {"status": "success", "predictions": []}
        
    try:
        predictions = await google_maps.get_place_autocomplete(query)
        return {
            "status": "success",
            "predictions": predictions
        }
    except ExternalAPIError as e:
        logger.error(f"Autocomplete failed for query: {query}", error=str(e))
        raise HTTPException(
            status_code=400,
            detail="Otomatik tamamlama hatası"
        )
    except Exception as e:
        logger.error(f"Unexpected autocomplete error: {query}", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Sunucu hatası"
        )

# =============================================================================
# VEHICLE CATALOG ENDPOINTS (UI Support)
# =============================================================================

@app.get("/vehicles/brands", tags=["Vehicles"])
async def list_vehicle_brands():
    try:
        brands = sorted(list(set(get_vehicle_model(vid).brand for vid in get_available_vehicle_ids())))
        return {
            "status": "success",
            "brands": brands,
        }
    except Exception as e:
        logger.error("Vehicle brands endpoint failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Marka listesi alınamadı: {str(e)}")


@app.get("/vehicles/by_brand", tags=["Vehicles"])
async def list_vehicles_by_brand(
    brand: str = Query(..., min_length=1),
    limit: int = Query(500, ge=1, le=5000),
):
    try:
        all_vids = get_available_vehicle_ids()
        vehicles = []
        for vid in all_vids:
            v = get_vehicle_model(vid)
            if v.brand.lower() == brand.lower():
                vehicles.append(v)
            if len(vehicles) >= limit:
                break
                
        return {
            "status": "success",
            "brand": brand,
            "vehicles": [
                {
                    "id": v.id,
                    "display_name": v.display_name,
                    "year": v.year,
                    "battery_kwh": v.battery_capacity_kwh,
                    "dc_max_kw": v.dc_max_kw,
                }
                for v in vehicles
            ],
        }
    except Exception as e:
        logger.error("Vehicles by brand endpoint failed", error=str(e), brand=brand)
        raise HTTPException(status_code=500, detail=f"Araç listesi alınamadı: {str(e)}")


@app.get("/vehicles/search", tags=["Vehicles"])
async def search_vehicles(
    query: str = Query("", min_length=0),
    limit: int = Query(50, ge=1, le=500),
):
    try:
        all_vids = get_available_vehicle_ids()
        vehicles = []
        for vid in all_vids:
            v = get_vehicle_model(vid)
            if not query or query.lower() in v.display_name.lower():
                vehicles.append(v)
            if len(vehicles) >= limit:
                break
        return {
            "status": "success",
            "query": query,
            "vehicles": [
                {
                    "id": v.id,
                    "display_name": v.display_name,
                    "brand": v.brand,
                    "model": v.model,
                    "variant": v.variant,
                    "year": v.year,
                }
                for v in vehicles
            ],
        }
    except Exception as e:
        logger.error("Vehicle search endpoint failed", error=str(e), query=query)
        raise HTTPException(status_code=500, detail=f"Araç araması başarısız: {str(e)}")


# =============================================================================
# 🔧 V3.1: FEEDBACK & RECALCULATE ENDPOINTS
# =============================================================================

from app.models import (
    StationFeedbackRequest,
    SwitchStationRequest,
    RecalculateResponse,
    FeedbackType,
    StationInfo,
    ConnectorInfo,
    PlugType,
    ChargerType
)


@app.get("/api/map_stations", response_model=list[StationInfo], tags=["Stations"])
async def get_map_stations(
    lat: float = Query(..., description="Harita Merkezi Enlem"),
    lon: float = Query(..., description="Harita Merkezi Boylam"),
    radius_km: float = Query(..., description="Görünüm Çapı (km)"),
    zoom: int = Query(..., description="Harita Zoom Seviyesi")
):
    """
    Dinamik Harita İstasyonları (Google Places Cached)
    Frontend ekranı kaydırdıkça bu endpoint'e istek atılır.
    Google Places API ile entegre, ancak bounding box ve zoom limitlerine göre optimize çalışır.
    """
    from app.services.google_service import google_maps
    
    try:
        raw_places = await google_maps.get_map_stations(lat=lat, lon=lon, radius_km=radius_km, zoom=zoom)
        
        stations = []
        for p in raw_places:
            location = p.get("geometry", {}).get("location", {})
            st_lat, st_lon = location.get("lat"), location.get("lng")
            if not st_lat or not st_lon:
                continue
                
            power_kw = p.get("max_power_kw", 0.0)
            c_type = ChargerType.HPC if power_kw >= 150 else (ChargerType.DC if power_kw >= 50 else ChargerType.AC)
            
            # Veriyi Pydantic modeline MAP et
            stations.append(
                StationInfo(
                    id=p.get("place_id", ""),
                    name=p.get("name", "Bilinmeyen İstasyon"),
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
                            currency="TRY"
                        )
                    ],
                    distance_from_route_km=0.0,
                    data_source="google",
                    place_id=p.get("place_id", ""),
                    vicinity=p.get("vicinity", ""),
                    is_open_now=True
                )
            )
        return stations
    except Exception as e:
        logger.error(f"Map Stations Error: {e}")
        return []


@app.post("/station_feedback", response_model=RecalculateResponse, tags=["Feedback"])
async def station_feedback(request: StationFeedbackRequest) -> RecalculateResponse:
    """
    🔧 V3.1: Kullanıcı istasyon feedback'i ve yeniden rota hesaplama.
    
    Kullanıcı mevcut rotadaki bir istasyon hakkında sorun bildirdiğinde:
    1. Sorunlu istasyonu blacklist'e al
    2. Kullanıcının mevcut konumundan varışa yeni rota hesapla
    3. Yeni rotayı döndür
    """
    request_id = f"feedback_{int(time.time() * 1000)}"
    
    logger.info(
        f"[{request_id}] Station feedback received",
        station_id=request.station_id,
        feedback_type=request.feedback_type.value,
        current_soc=request.current_soc_percent
    )
    
    try:
        # 🔧 V3.2: FeedbackManager ile istasyonu raporla
        # user_id yoksa request_id kullan (anonim feedback)
        user_id = getattr(request, 'user_id', None) or request_id
        feedback_result = await feedback_manager.report_station(
            station_id=request.station_id,
            user_id=user_id,
            reason=request.feedback_type.value
        )
        
        logger.info(
            f"[{request_id}] Feedback recorded",
            station_id=request.station_id,
            feedback_status=feedback_result["status"],
            is_blocked=feedback_result["is_blocked"]
        )
        
        # Yeni rota isteği oluştur
        new_route_request = RouteRequest(
            start_location=request.current_location,
            end_location=request.destination,
            vehicle_model_id=request.vehicle_model_id,
            current_soc_percent=request.current_soc_percent
        )
        
        # Yeni rota hesapla (bloklu istasyonlar otomatik filtrelenir)
        new_route = await plan_route(new_route_request)
        
        logger.info(
            f"[{request_id}] New route calculated",
            charge_stops=new_route.charge_stops,
            total_distance=new_route.total_distance_km
        )
        
        # Feedback durumuna göre mesaj oluştur
        feedback_msg = feedback_result["message"]
        if feedback_result["is_blocked"]:
            feedback_msg += " İstasyon geçici olarak bloklandı."
        
        return RecalculateResponse(
            status="success",
            message=f"Rota yeniden hesaplandı. {feedback_msg}",
            recalculate_type="full_route",
            route=new_route,
            affected_legs=list(range(len(new_route.legs)))
        )
        
    except Exception as e:
        logger.error(f"[{request_id}] Feedback processing failed", error=str(e))
        return RecalculateResponse(
            status="error",
            message=f"Rota yeniden hesaplanamadı: {str(e)}",
            recalculate_type="none",
            route=None,
            affected_legs=[]
        )


@app.post("/switch_station", response_model=RecalculateResponse, tags=["Feedback"])
async def switch_station(request: SwitchStationRequest) -> RecalculateResponse:
    """
    🔧 V3.3: İstasyon değiştirme — her zaman tam rota yeniden hesaplama.
    
    Kullanıcı alternatif istasyonlardan birini seçtiğinde,
    mevcut konumdan varışa kadar tüm rota yeniden hesaplanır.
    Eski istasyon blacklist'e alınarak yeni planda dışlanır.
    """
    request_id = f"switch_{int(time.time() * 1000)}"
    
    logger.info(
        f"[{request_id}] Station switch requested",
        original=request.original_station_id,
        new=request.new_station_id,
        leg_index=request.leg_index
    )
    
    try:
        # Eski istasyonu blacklist'e al
        if request.original_station_id:
            user_id = request_id
            await feedback_manager.report_station(
                station_id=request.original_station_id,
                user_id=user_id,
                reason="user_switched"
            )
        
        # Mevcut konumdan varışa tam rota yeniden hesapla
        new_route_request = RouteRequest(
            start_location=request.current_location,
            end_location=request.destination,
            vehicle_model_id=request.vehicle_model_id,
            current_soc_percent=request.current_soc_percent
        )
        
        new_route = await plan_route(new_route_request)
        
        logger.info(
            f"[{request_id}] Route recalculated after station switch",
            charge_stops=new_route.charge_stops,
            total_distance=new_route.total_distance_km
        )
        
        return RecalculateResponse(
            status="success",
            message=f"İstasyon değiştirildi ve rota yeniden hesaplandı.",
            recalculate_type="full_route",
            route=new_route,
            affected_legs=list(range(len(new_route.legs)))
        )
        
    except Exception as e:
        logger.error(f"[{request_id}] Station switch failed", error=str(e))
        return RecalculateResponse(
            status="error",
            message=f"İstasyon değiştirilemedi: {str(e)}",
            recalculate_type="none",
            route=None,
            affected_legs=[]
        )



if __name__ == "__main__":
    import uvicorn
    
    logger.info("Starting server", environment=config.get_environment())
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=config.is_debug(),  # Sadece development'ta reload aktif
        log_level="debug" if config.is_debug() else "info"
    )
