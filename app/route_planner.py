"""
Route Planner v2.0
===================

V1.3 Enterprise Route Planner - Tek DriveLeg ile basit rota planlaması.

Özellikler:
- Google Directions alternatif rotaları ile optimal seçim
- Elevation API ile yükselme/iniş hesabı
- V1 kural tabanlı tüketim motoru
- CO2 tasarrufu hesaplama
- ML training data loglama

Kullanım:
    from app.route_planner import plan_multi_stop_route, plan_full_route
    
    response = await plan_full_route(request)
"""

from typing import Dict, Any, Optional
from app.models import (
    RouteRequest, 
    MultiStopRouteResponse, 
    DriveLeg, 
    GeoPoint
)
from app.route_selector import find_best_route
from app.consumption_engine.main_calculator import calculate_segment_consumption_kwh
from app.consumption_engine.vehicle_models import get_vehicle_model
from app.sustainability_calculator import calculate_co2_savings
from app.utils.logger import get_logger
from app.utils.data_logger import log_training_data
from app.services.google_service import google_maps
from app.services.base_service import ExternalAPIError

# =============================================================================
# CONSTANTS
# =============================================================================
logger = get_logger("route_planner")

SIMULATION_SEGMENT_KM = 1.0
DEFAULT_TARGET_SOC_PERCENT = 80
SOC_SAFETY_BUFFER_PERCENT = 15.0
DEFAULT_TEMPERATURE_C = 20.0


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _create_error_response(status: str, message: str = None) -> MultiStopRouteResponse:
    """Hata durumunda standart response oluştur."""
    return MultiStopRouteResponse(
        status=status,
        total_distance_km=0,
        total_duration_minutes=0,
        total_co2_savings_kg=0,
        legs=[],
        message=message
    )


def _safe_soc_percent(soc_kwh: float, battery_kwh: float) -> float:
    """SOC'u güvenli yüzdeye çevir (0-100 arası)."""
    if battery_kwh <= 0:
        return 0.0
    percent = (soc_kwh / battery_kwh) * 100
    return max(0.0, min(100.0, percent))


# =============================================================================
# MAIN PLANNER FUNCTION
# =============================================================================

async def plan_multi_stop_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    V1.3 Enterprise Route Planner
    ==============================
    
    Tek DriveLeg ile basit rota planlaması yapar.
    Şarj durakları V2'de eklenecek.
    
    Args:
        request: RouteRequest - başlangıç/bitiş, araç ve SOC bilgileri
    
    Returns:
        MultiStopRouteResponse: Planlanmış rota ve tüketim bilgileri
    
    Flow:
        1. Araç bilgilerini al
        2. Route selector ile en iyi rotayı seç
        3. Elevation API'den yükselme/iniş verisi al
        4. Consumption engine ile tüketim hesapla
        5. CO2 tasarrufu hesapla
        6. Training data logla
        7. Response döndür
    """
    logger.debug(
        "Route planning initiated",
        start=f"{request.start_location.lat},{request.start_location.lon}",
        end=f"{request.end_location.lat},{request.end_location.lon}",
        vehicle=request.vehicle_model_id,
        initial_soc=request.current_soc_percent
    )
    
    try:
        # =====================================================================
        # STEP A: Araç bilgilerini al
        # =====================================================================
        try:
            vehicle = get_vehicle_model(request.vehicle_model_id)
        except ValueError as e:
            logger.error("Vehicle model not found", vehicle_id=request.vehicle_model_id, error=str(e))
            return _create_error_response(
                "error_vehicle_not_found",
                f"Araç modeli bulunamadı: {request.vehicle_model_id}"
            )
        
        logger.info(
            "Route planning started",
            start=f"{request.start_location.lat},{request.start_location.lon}",
            end=f"{request.end_location.lat},{request.end_location.lon}",
            vehicle=vehicle.model_name,
            battery_kwh=vehicle.battery_capacity_kwh,
            initial_soc=request.current_soc_percent,
            extra_load_kg=request.extra_load_kg
        )
        
        # =====================================================================
        # STEP B: En iyi rotayı seç (route_selector)
        # =====================================================================
        try:
            route_result = await find_best_route(
                origin=request.start_location,
                destination=request.end_location,
                vehicle_model_id=request.vehicle_model_id,
                extra_load_kg=request.extra_load_kg
            )
        except Exception as e:
            logger.exception("Route selector failed", error=str(e))
            return _create_error_response(
                "error_route_selector_failed",
                f"Rota seçimi başarısız: {str(e)}"
            )
        
        selected_route = route_result["selected_route"]
        selection_reason = route_result["selection_reason"]
        polyline = route_result.get("polyline", "")
        
        logger.info(
            "Route selected",
            selection_reason=selection_reason,
            distance_km=route_result.get("selected_distance_km"),
            duration_min=route_result.get("selected_duration_min")
        )
        
        # =====================================================================
        # STEP C: Rota verilerini çıkar
        # =====================================================================
        try:
            route_leg = selected_route["legs"][0]
            start_coords = route_leg["start_location"]
            end_coords = route_leg["end_location"]
            
            route_distance_km = route_leg["distance"]["value"] / 1000  # metre → km
            route_duration_min = route_leg["duration"]["value"] / 60   # saniye → dakika
            
            # Polyline yoksa route'dan al
            if not polyline:
                polyline = selected_route.get("overview_polyline", {}).get("points", "")
                
        except (KeyError, IndexError) as e:
            logger.error("Route data extraction failed", error=str(e))
            return _create_error_response(
                "error_no_route_legs_found",
                "Google Directions verisi eksik"
            )
        
        logger.info(
            "Route data extracted",
            distance_km=round(route_distance_km, 1),
            duration_min=round(route_duration_min, 1)
        )
        
        # =====================================================================
        # STEP D: Elevation verisi al
        # =====================================================================
        elevation_gain_m = 0.0
        elevation_loss_m = 0.0
        
        if polyline:
            try:
                elevation_stats = await google_maps.get_elevation_stats(polyline)
                elevation_gain_m = elevation_stats.get("gain_m", 0.0)
                elevation_loss_m = elevation_stats.get("loss_m", 0.0)
                
                logger.info(
                    "Elevation data retrieved",
                    gain_m=round(elevation_gain_m, 1),
                    loss_m=round(elevation_loss_m, 1)
                )
            except Exception as e:
                logger.warning("Elevation API failed, using defaults", error=str(e))
        
        # =====================================================================
        # STEP E: Tüketim hesapla (V1 Engine)
        # =====================================================================
        try:
            segment_consumption_kwh = calculate_segment_consumption_kwh(
                vehicle=vehicle,
                segment_distance_km=route_distance_km,
                segment_elevation_gain_m=elevation_gain_m,
                segment_elevation_loss_m=elevation_loss_m,
                temperature_celsius=DEFAULT_TEMPERATURE_C,
                extra_load_kg=request.extra_load_kg,
                passenger_count=request.passenger_count,
                engine_version="v1"
            )
        except Exception as e:
            logger.exception("Consumption calculation failed", error=str(e))
            return _create_error_response(
                "error_consumption_failed",
                f"Tüketim hesaplaması başarısız: {str(e)}"
            )
        
        # =====================================================================
        # STEP F: SOC hesapla
        # =====================================================================
        battery_kwh = vehicle.battery_capacity_kwh
        start_soc_kwh = battery_kwh * (request.current_soc_percent / 100)
        arrival_soc_kwh = start_soc_kwh - segment_consumption_kwh
        
        start_soc_percent = _safe_soc_percent(start_soc_kwh, battery_kwh)
        arrival_soc_percent = _safe_soc_percent(arrival_soc_kwh, battery_kwh)
        
        logger.info(
            "Battery calculation completed",
            start_soc_percent=round(start_soc_percent, 1),
            arrival_soc_percent=round(arrival_soc_percent, 1),
            consumption_kwh=round(segment_consumption_kwh, 2)
        )
        
        # =====================================================================
        # STEP G: DriveLeg oluştur
        # =====================================================================
        avg_speed_kmh = (route_distance_km / route_duration_min) * 60 if route_duration_min > 0 else 0
        
        drive_leg = DriveLeg(
            type="drive",
            start_point=GeoPoint(lat=start_coords["lat"], lon=start_coords["lng"]),
            end_point=GeoPoint(lat=end_coords["lat"], lon=end_coords["lng"]),
            distance_km=round(route_distance_km, 1),
            duration_minutes=round(route_duration_min, 1),
            avg_speed_kmh=round(avg_speed_kmh, 1),
            consumption_kwh=round(segment_consumption_kwh, 2),
            start_soc_percent=round(start_soc_percent, 1),
            end_soc_percent=round(arrival_soc_percent, 1),
            elevation_gain_m=round(elevation_gain_m, 1),
            elevation_loss_m=round(elevation_loss_m, 1),
            polyline=polyline
        )
        
        # =====================================================================
        # STEP H: CO2 tasarrufu hesapla
        # =====================================================================
        try:
            co2_savings = calculate_co2_savings(route_distance_km)
        except Exception as e:
            logger.warning("CO2 calculation failed", error=str(e))
            co2_savings = 0.0
        
        # =====================================================================
        # STEP I: Training data logla
        # =====================================================================
        try:
            training_data = {
                "start_lat": request.start_location.lat,
                "start_lon": request.start_location.lon,
                "end_lat": request.end_location.lat,
                "end_lon": request.end_location.lon,
                "vehicle_model": vehicle.model_name,
                "battery_kwh": battery_kwh,
                "initial_soc_percent": request.current_soc_percent,
                "distance_km": route_distance_km,
                "duration_min": route_duration_min,
                "elevation_gain_m": elevation_gain_m,
                "elevation_loss_m": elevation_loss_m,
                "consumption_kwh": segment_consumption_kwh,
                "arrival_soc_percent": arrival_soc_percent,
                "selection_reason": selection_reason,
                "co2_savings_kg": co2_savings
            }
            log_training_data(training_data, category="route")
        except Exception as e:
            logger.warning("Training data logging failed", error=str(e))
        
        # =====================================================================
        # STEP J: Response döndür
        # =====================================================================
        logger.info(
            "Route planning completed successfully",
            total_distance_km=round(route_distance_km, 1),
            total_duration_min=round(route_duration_min, 1),
            consumption_kwh=round(segment_consumption_kwh, 2),
            co2_savings_kg=round(co2_savings, 2)
        )
        
        return MultiStopRouteResponse(
            status="success",
            total_distance_km=round(route_distance_km, 1),
            total_duration_minutes=round(route_duration_min, 1),
            total_co2_savings_kg=round(co2_savings, 2),
            legs=[drive_leg],
            charge_stops=0
        )
        
    except ExternalAPIError as e:
        logger.error(
            "External API error",
            source=e.source,
            status_code=e.status_code,
            detail=str(e)
        )
        return _create_error_response(
            f"error_api_{e.source.lower()}",
            f"API hatası ({e.source}): {str(e)}"
        )
        
    except Exception as e:
        logger.exception(
            "Route planning failed with unexpected error",
            error=str(e),
            error_type=type(e).__name__
        )
        return _create_error_response(
            "error_unknown",
            f"Beklenmeyen hata: {str(e)}"
        )


# =============================================================================
# ALIAS FOR MAIN.PY COMPATIBILITY
# =============================================================================

async def plan_full_route(request: RouteRequest) -> MultiStopRouteResponse:
    """
    plan_multi_stop_route için alias.
    main.py'de bu isimle import ediliyor.
    """
    return await plan_multi_stop_route(request)
