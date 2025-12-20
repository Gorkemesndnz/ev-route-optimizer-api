"""Route Selector v3.0
====================

Google Directions API'den alternatif rotaları alır ve stratejiye göre en uygun olanı seçer.

Stratejiler:
- FASTEST: En kısa süreli rota (trafik dahil)
- EFFICIENT: En az enerji tüketen rota
- OPTIMAL: Süre + enerji dengesi (varsayılan)
- CHEAPEST: En düşük şarj maliyetli rota (fiyat verisi gerektirir)
- RENEWABLE: Yenilenebilir enerji istasyonları öncelikli (veri gerektirir)

Kullanım:
    from app.route_selector import find_best_route
    from app.models import RouteStrategy
    
    result = await find_best_route(
        origin=GeoPoint(lat=41.0, lon=29.0),
        destination=GeoPoint(lat=39.0, lon=32.0),
        vehicle_model_id="mg4_51kwh",
        strategy=RouteStrategy.FASTEST,
        departure_time=None  # None = şimdi
    )
"""

import time
from typing import Dict, Any, List, Union, Optional
from app.models import GeoPoint, RouteStrategy
from app.services.google_service import google_maps
from app.services.pricing_service import pricing_service, DEFAULT_PRICE_PER_KWH
from app.consumption_engine.vehicle_models import get_vehicle_model
from app.utils.config_manager import config
from app.utils.logger import get_logger

logger = get_logger("route_selector")

# =============================================================================
# STRATEGY WEIGHTS (OPTIMAL strateji için)
# =============================================================================
OPTIMAL_WEIGHT_TIME = 0.4
OPTIMAL_WEIGHT_ENERGY = 0.4
OPTIMAL_WEIGHT_COST = 0.2  # Cheapest hazır olunca aktif olacak

def _parse_geopoint(location: Union[GeoPoint, str]) -> GeoPoint:
    """
    GeoPoint veya string koordinatı GeoPoint'e çevirir.
    
    Args:
        location: GeoPoint objesi veya "lat,lon" formatında string
    
    Returns:
        GeoPoint objesi
    """
    if isinstance(location, GeoPoint):
        return location
    
    if isinstance(location, str):
        try:
            lat, lon = location.split(",")
            return GeoPoint(lat=float(lat.strip()), lon=float(lon.strip()))
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Geçersiz koordinat formatı: {location}") from e
    
    raise TypeError(f"Beklenmeyen tip: {type(location)}")


def _analyze_route(route: dict, index: int) -> Dict[str, Any]:
    """
    Tek bir rotayı analiz eder - trafik süresi dahil.
    
    Args:
        route: Google Directions route objesi
        index: Rota indeksi
    
    Returns:
        Analiz sonucu dict:
        - duration_min: Trafiksiz süre
        - duration_in_traffic_min: Trafikli süre (varsa, yoksa duration_min)
        - traffic_ratio: Trafik oranı (1.0 = normal, >1 = trafik var)
    """
    try:
        leg = route["legs"][0]
        distance_km = leg["distance"]["value"] / 1000
        duration_min = leg["duration"]["value"] / 60
        
        # Trafik süresi (varsa)
        duration_in_traffic_sec = leg.get("duration_in_traffic", {}).get("value")
        if duration_in_traffic_sec:
            duration_in_traffic_min = duration_in_traffic_sec / 60
            traffic_ratio = duration_in_traffic_sec / leg["duration"]["value"] if leg["duration"]["value"] > 0 else 1.0
        else:
            duration_in_traffic_min = duration_min
            traffic_ratio = 1.0
        
        # Tahmini enerji tüketimi (basit hesap: 0.18 kWh/km ortalama)
        # Gerçek tüketim route_planner'da segment bazlı hesaplanır
        avg_consumption_per_km = 0.18
        estimated_consumption_kwh = distance_km * avg_consumption_per_km
        
        # Tahmini şarj maliyeti - ortalama piyasa fiyatı ile hesapla
        # (Gerçek maliyet istasyon seçiminden sonra kesinleşir)
        avg_price_per_kwh = pricing_service.get_average_dc_price()
        estimated_cost_try = estimated_consumption_kwh * avg_price_per_kwh
        
        return {
            "route_index": index,
            "route": route,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "duration_in_traffic_min": duration_in_traffic_min,
            "traffic_ratio": traffic_ratio,
            "estimated_consumption_kwh": estimated_consumption_kwh,
            "estimated_cost_try": estimated_cost_try,  # Cheapest için placeholder
            "polyline": route.get("overview_polyline", {}).get("points", ""),
            "summary": route.get("summary", f"Rota {index + 1}")
        }
    except (KeyError, IndexError) as e:
        logger.warning(f"Rota {index + 1} analizi eksik veri", error=str(e))
        return None


def _select_by_strategy(
    route_analyses: List[Dict[str, Any]],
    strategy: RouteStrategy,
    battery_kwh: float,
    threshold_percent: float
) -> tuple:
    """
    Stratejiye göre en iyi rotayı seçer.
    
    Args:
        route_analyses: Analiz edilmiş rotalar
        strategy: Seçim stratejisi
        battery_kwh: Batarya kapasitesi (kWh)
        threshold_percent: Eşik yüzdesi
    
    Returns:
        (selected_route_analysis, selection_reason)
    """
    if not route_analyses:
        return None, "no_routes"
    
    # Tek rota varsa direkt seç
    if len(route_analyses) == 1:
        return route_analyses[0], "single_route"
    
    # FASTEST: En kısa trafikli süre
    if strategy == RouteStrategy.FASTEST:
        selected = min(route_analyses, key=lambda x: x["duration_in_traffic_min"])
        logger.info(
            f"FASTEST strategy: {selected['summary']}",
            duration_traffic=round(selected["duration_in_traffic_min"], 1),
            traffic_ratio=f"{selected['traffic_ratio']:.2f}"
        )
        return selected, "fastest_by_traffic_duration"
    
    # EFFICIENT: En az enerji tüketimi (en kısa mesafe)
    if strategy == RouteStrategy.EFFICIENT:
        selected = min(route_analyses, key=lambda x: x["estimated_consumption_kwh"])
        logger.info(
            f"EFFICIENT strategy: {selected['summary']}",
            consumption_kwh=round(selected["estimated_consumption_kwh"], 2),
            distance_km=round(selected["distance_km"], 1)
        )
        return selected, "efficient_by_consumption"
    
    # CHEAPEST: En düşük maliyet (placeholder - fiyat verisi gerektirir)
    if strategy == RouteStrategy.CHEAPEST:
        # Şimdilik estimated_cost_try kullanıyoruz
        # Gerçek implementasyonda istasyon fiyatları ile hesaplanacak
        selected = min(route_analyses, key=lambda x: x["estimated_cost_try"])
        logger.info(
            f"CHEAPEST strategy: {selected['summary']}",
            estimated_cost=round(selected["estimated_cost_try"], 2),
            note="Placeholder - gerçek istasyon fiyatları ile güncellenecek"
        )
        return selected, "cheapest_by_estimated_cost"
    
    # RENEWABLE: Rota değil istasyon seçimini etkiler
    # Rota olarak OPTIMAL kullan
    if strategy == RouteStrategy.RENEWABLE:
        logger.info("RENEWABLE strategy: Rota için OPTIMAL kullanılıyor, istasyon filtresi ayrıca uygulanacak")
        strategy = RouteStrategy.OPTIMAL
    
    # OPTIMAL (varsayılan): Süre + Enerji dengesi
    # Normalize edilmiş skorlama
    min_time = min(r["duration_in_traffic_min"] for r in route_analyses)
    max_time = max(r["duration_in_traffic_min"] for r in route_analyses)
    min_energy = min(r["estimated_consumption_kwh"] for r in route_analyses)
    max_energy = max(r["estimated_consumption_kwh"] for r in route_analyses)
    
    time_range = max_time - min_time if max_time > min_time else 1
    energy_range = max_energy - min_energy if max_energy > min_energy else 1
    
    best_score = float('inf')
    selected = route_analyses[0]
    
    for r in route_analyses:
        # Normalize (0-1 arası, düşük = iyi)
        norm_time = (r["duration_in_traffic_min"] - min_time) / time_range
        norm_energy = (r["estimated_consumption_kwh"] - min_energy) / energy_range
        
        # Ağırlıklı skor (düşük = iyi)
        score = OPTIMAL_WEIGHT_TIME * norm_time + OPTIMAL_WEIGHT_ENERGY * norm_energy
        
        if score < best_score:
            best_score = score
            selected = r
    
    logger.info(
        f"OPTIMAL strategy: {selected['summary']}",
        duration_traffic=round(selected["duration_in_traffic_min"], 1),
        consumption_kwh=round(selected["estimated_consumption_kwh"], 2),
        score=round(best_score, 3)
    )
    
    return selected, "optimal_balanced"


# =============================================================================
# MAIN FUNCTION
# =============================================================================

def _parse_departure_time(departure_time_iso: Optional[str]) -> Optional[int]:
    """
    ISO 8601 formatındaki çıkış zamanını Unix epoch'a çevirir.
    
    Args:
        departure_time_iso: ISO 8601 formatında zaman ("2025-11-25T12:30:00Z")
    
    Returns:
        Unix epoch (saniye) veya None (şimdi için)
    """
    if not departure_time_iso:
        return None
    
    try:
        from datetime import datetime
        # ISO 8601 parse
        if departure_time_iso.endswith('Z'):
            dt = datetime.fromisoformat(departure_time_iso.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(departure_time_iso)
        return int(dt.timestamp())
    except Exception as e:
        logger.warning(f"departure_time_iso parse hatası: {e}, 'now' kullanılıyor")
        return None


async def find_best_route(
    origin: Union[GeoPoint, str],
    destination: Union[GeoPoint, str],
    vehicle_model_id: str,
    extra_load_kg: float = 0.0,
    temperature_celsius: float = 20.0,
    strategy: RouteStrategy = RouteStrategy.OPTIMAL,
    departure_time_iso: Optional[str] = None
) -> Dict[str, Any]:
    """
    Google'dan alternatif rotaları alır ve stratejiye göre en uygun olanı seçer.
    
    Args:
        origin: Başlangıç noktası (GeoPoint veya "lat,lon" string)
        destination: Bitiş noktası (GeoPoint veya "lat,lon" string)
        vehicle_model_id: Araç model ID'si
        extra_load_kg: Ekstra yük (kg)
        temperature_celsius: Ortam sıcaklığı (°C)
        strategy: Rota seçim stratejisi (fastest, efficient, optimal, cheapest, renewable)
        departure_time_iso: ISO 8601 formatında çıkış zamanı. None = şimdi
    
    Returns:
        {
            "selected_route": dict,      # Seçilen rota (Google format)
            "selection_reason": str,     # Seçim nedeni
            "route_analyses": list,      # Tüm rotaların analizi
            "selected_distance_km": float,
            "selected_duration_min": float,  # Trafiksiz süre
            "selected_duration_in_traffic_min": float,  # Trafikli süre
            "traffic_ratio": float,      # Trafik oranı
            "polyline": str
        }
    
    Raises:
        ValueError: API hatası veya veri eksikliği durumunda
    """
    try:
        # --- 1. Parametreleri normalize et ---
        start = _parse_geopoint(origin)
        end = _parse_geopoint(destination)
        departure_time = _parse_departure_time(departure_time_iso)
        
        logger.info(
            "Rota seçimi başlatıldı",
            origin=f"{start.lat},{start.lon}",
            destination=f"{end.lat},{end.lon}",
            vehicle=vehicle_model_id,
            strategy=strategy.value,
            departure_time=departure_time_iso or "now"
        )
        
        # --- 2. Araç bilgisini al ---
        vehicle = get_vehicle_model(vehicle_model_id)
        
        # --- 3. Google'dan alternatif rotaları al (trafik dahil) ---
        directions_response = await google_maps.get_route_alternatives_cached(
            start=start,
            end=end,
            alternatives=True,
            departure_time=departure_time,
            traffic_model="best_guess"
        )
        
        # API status kontrolü
        status = directions_response.get("status")
        if status != "OK":
            error_msg = directions_response.get("error_message", "Bilinmeyen hata")
            raise ValueError(f"Google Directions API hatası: {status} - {error_msg}")
        
        routes = directions_response.get("routes", [])
        if not routes:
            raise ValueError("Google Directions API'den rota bulunamadı")
        
        logger.info(f"{len(routes)} rota alternatifi alındı")
        
        # --- 4. Her rotayı analiz et ---
        route_analyses = []
        
        for i, route in enumerate(routes):
            analysis = _analyze_route(route, i)
            if analysis:
                route_analyses.append(analysis)
                logger.info(
                    f"Rota {i + 1} analiz edildi",
                    distance_km=round(analysis["distance_km"], 1),
                    duration_min=round(analysis["duration_min"]),
                    duration_traffic=round(analysis["duration_in_traffic_min"]),
                    traffic_ratio=f"{analysis['traffic_ratio']:.2f}",
                    summary=analysis["summary"]
                )
        
        if not route_analyses:
            raise ValueError("Hiçbir rota analiz edilemedi")
        
        # --- 5. Stratejiye göre en iyi rotayı seç ---
        threshold_percent = config.get_route_selector_threshold_percent()
        battery_kwh = vehicle.battery_capacity_kwh
        
        selected, reason = _select_by_strategy(
            route_analyses=route_analyses,
            strategy=strategy,
            battery_kwh=battery_kwh,
            threshold_percent=threshold_percent
        )
        
        if not selected:
            raise ValueError("Rota seçilemedi")
        
        logger.info(
            f"Rota seçildi: {selected['summary']}",
            strategy=strategy.value,
            reason=reason,
            distance_km=round(selected["distance_km"], 1),
            duration_traffic=round(selected["duration_in_traffic_min"], 1)
        )
        
        return {
            "selected_route": selected["route"],
            "selection_reason": reason,
            "route_analyses": route_analyses,
            "selected_distance_km": selected["distance_km"],
            "selected_duration_min": selected["duration_min"],
            "selected_duration_in_traffic_min": selected["duration_in_traffic_min"],
            "traffic_ratio": selected["traffic_ratio"],
            "estimated_consumption_kwh": selected["estimated_consumption_kwh"],
            "polyline": selected["polyline"]
        }
        
    except ValueError:
        # Bilinen hatalar - tekrar raise et
        raise
    except Exception as e:
        logger.error(
            "Route seçimi başarısız",
            error=str(e),
            error_type=type(e).__name__
        )
        raise ValueError(f"Route seçimi başarısız: {str(e)}") from e
