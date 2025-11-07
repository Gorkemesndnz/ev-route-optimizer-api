from fastapi import FastAPI
from app.models import RouteRequest, MultiStopRouteResponse
from app.route_planner import plan_full_route
from app.services.base_service import ExternalAPIError
from app.utils.logger import get_logger

# Uygulamayı ve Logger'ı Başlat
app = FastAPI(title="Akıllı EV Rota Asistanı API", version="1.3")
logger = get_logger("main_api")


@app.get("/", tags=["Info"])
async def root():
    """API ana sayfası - Hoş geldiniz mesajı"""
    return {
        "message": "Akıllı EV Rota Asistanı API'ye Hoş Geldiniz!",
        "version": "v1.3",
        "documentation": "/docs",
        "health_check": "/health",
        "endpoints": {
            "optimize_route": "POST /optimize_route"
        }
    }


@app.get("/health", tags=["Monitoring"])
async def health():
    """API sağlık kontrolü endpoint'i"""
    return {
        "status": "ok", 
        "version": "v1.3",
        "service": "Akıllı EV Rota Asistanı"
    }


@app.post("/optimize_route", response_model=MultiStopRouteResponse, tags=["Routing"])
async def optimize_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    Ana rota optimizasyonu endpoint'i.
    V1.3 mimarisine göre çok duraklı şarj planlaması yapar.
    """
    try:
        logger.info(
            "Rota planlama isteği alındı",
            start=request.start_location,
            end=request.end_location,
            vehicle=request.vehicle_model_id,
            initial_soc=request.initial_soc_percent,
            extra_load=request.extra_load_kg
        )
        
        plan = await plan_full_route(request)
        return plan
        
    except ExternalAPIError as e:
        logger.error(
            "Dış API hatası yakalandı",
            service=e.source,
            status_code=e.status_code,
            detail=str(e)
        )
        return MultiStopRouteResponse(
            status=f"error_api_failed_{e.source}", 
            total_distance_km=0,
            total_duration_minutes=0,
            total_co2_savings_kg=0,
            legs=[]
        )
        
    except Exception as e:
        logger.critical(
            "Beklenmedik bir sunucu hatası oluştu",
            error=str(e),
            error_type=type(e).__name__
        )
        return MultiStopRouteResponse(
            status="error_internal_server",
            total_distance_km=0,
            total_duration_minutes=0,
            total_co2_savings_kg=0,
            legs=[]
        )
