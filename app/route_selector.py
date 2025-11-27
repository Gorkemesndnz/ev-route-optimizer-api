"""
Route Selector v2.0
====================

Google Directions API'den alternatif rotaları alır ve en uygun olanı seçer.

Seçim Kriterleri:
- Mesafe farkı analizi
- Dinamik eşik mantığı (batarya kapasitesine göre)
- Popüler vs Verimli rota karşılaştırması

Kullanım:
    from app.route_selector import find_best_route
    
    result = await find_best_route(
        origin=GeoPoint(lat=41.0, lon=29.0),
        destination=GeoPoint(lat=39.0, lon=32.0),
        vehicle_model_id="mg4_51kwh"
    )
"""

from typing import Dict, Any, List, Union
from app.models import GeoPoint
from app.services.google_service import google_maps
from app.consumption_engine.vehicle_models import get_vehicle_model
from app.utils.config_manager import config
from app.utils.logger import get_logger

logger = get_logger("route_selector")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

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
    Tek bir rotayı analiz eder.
    
    Args:
        route: Google Directions route objesi
        index: Rota indeksi
    
    Returns:
        Analiz sonucu dict
    """
    try:
        leg = route["legs"][0]
        distance_km = leg["distance"]["value"] / 1000
        duration_min = leg["duration"]["value"] / 60
        
        return {
            "route_index": index,
            "route": route,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "polyline": route.get("overview_polyline", {}).get("points", ""),
            "summary": route.get("summary", f"Rota {index + 1}")
        }
    except (KeyError, IndexError) as e:
        logger.warning(f"Rota {index + 1} analizi eksik veri", error=str(e))
        return None


# =============================================================================
# MAIN FUNCTION
# =============================================================================

async def find_best_route(
    origin: Union[GeoPoint, str],
    destination: Union[GeoPoint, str],
    vehicle_model_id: str,
    extra_load_kg: float = 0.0,
    temperature_celsius: float = 20.0
) -> Dict[str, Any]:
    """
    Google'dan alternatif rotaları alır ve en uygun olanı seçer.
    
    Args:
        origin: Başlangıç noktası (GeoPoint veya "lat,lon" string)
        destination: Bitiş noktası (GeoPoint veya "lat,lon" string)
        vehicle_model_id: Araç model ID'si
        extra_load_kg: Ekstra yük (kg)
        temperature_celsius: Ortam sıcaklığı (°C)
    
    Returns:
        {
            "selected_route": dict,      # Seçilen rota (Google format)
            "selection_reason": str,     # Seçim nedeni
            "route_analyses": list       # Tüm rotaların analizi
        }
    
    Raises:
        ValueError: API hatası veya veri eksikliği durumunda
    """
    try:
        # --- 1. Parametreleri normalize et ---
        start = _parse_geopoint(origin)
        end = _parse_geopoint(destination)
        
        logger.info(
            "Rota seçimi başlatıldı",
            origin=f"{start.lat},{start.lon}",
            destination=f"{end.lat},{end.lon}",
            vehicle=vehicle_model_id
        )
        
        # --- 2. Araç bilgisini al ---
        vehicle = get_vehicle_model(vehicle_model_id)
        
        # --- 3. Google'dan alternatif rotaları al ---
        directions_response = await google_maps.get_route_alternatives(
            start=start,
            end=end,
            alternatives=True
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
                    summary=analysis["summary"]
                )
        
        if not route_analyses:
            raise ValueError("Hiçbir rota analiz edilemedi")
        
        # --- 5. En iyi rotayı seç ---
        popular_route = route_analyses[0]  # Google'ın ilk önerisi
        most_efficient = min(route_analyses, key=lambda x: x["distance_km"])
        
        logger.info(
            "Rota karşılaştırması",
            popular_km=round(popular_route["distance_km"], 1),
            efficient_km=round(most_efficient["distance_km"], 1),
            popular_summary=popular_route["summary"],
            efficient_summary=most_efficient["summary"]
        )
        
        # --- 6. Dinamik eşik kontrolü ---
        threshold_percent = config.get_route_selector_threshold_percent()
        battery_kwh = vehicle.battery_capacity_kwh
        
        # Mesafe farkı km cinsinden
        distance_diff_km = abs(popular_route["distance_km"] - most_efficient["distance_km"])
        
        # Tahmini tüketim farkı (basit hesap: 0.18 kWh/km ortalama)
        avg_consumption_per_km = 0.18
        consumption_diff_kwh = distance_diff_km * avg_consumption_per_km
        
        # Eşik değeri (batarya kapasitesinin %'si)
        threshold_kwh = battery_kwh * threshold_percent
        
        logger.info(
            "Eşik analizi",
            distance_diff_km=round(distance_diff_km, 1),
            consumption_diff_kwh=round(consumption_diff_kwh, 2),
            threshold_kwh=round(threshold_kwh, 2),
            threshold_percent=f"{threshold_percent * 100:.0f}%"
        )
        
        # --- 7. Karar ---
        if consumption_diff_kwh < threshold_kwh:
            # Fark küçük → popüler rotayı tercih et (daha iyi yol kalitesi varsayımı)
            selected = popular_route
            reason = "popular_route"
            logger.info(
                "Popüler rota seçildi",
                reason="Tüketim farkı eşikten küçük"
            )
        else:
            # Fark önemli → verimli rotayı tercih et
            selected = most_efficient
            reason = "most_efficient"
            logger.info(
                "Verimli rota seçildi",
                reason="Tüketim farkı eşikten büyük"
            )
        
        return {
            "selected_route": selected["route"],
            "selection_reason": reason,
            "route_analyses": route_analyses,
            "selected_distance_km": selected["distance_km"],
            "selected_duration_min": selected["duration_min"],
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
