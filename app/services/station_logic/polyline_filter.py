"""
Polyline Perpendicular Distance Filter
=======================================

station_finder.py'den çıkarılmış rota polyline filtreleme mantığı.

Tasarım kararı: Google Roads API (snap_to_roads / nearest_roads) yerine
rota polyline'ından dik mesafe (haversine vertex distance) kontrolü kullanılır.

Roads API yaklaşımı şu sebeple kaldırıldı:
  - Otoban placeId'leri her ~3 km değişiyor → çok ince granularite
  - EV istasyonları otoyol "service area"da olduklarında bile servis yolu /
    access road / parking lot placeId'sine snap oluyor → otoyol placeId
    set'inde bulunmuyor → "yanlış yol" diye reddediliyor
  - Sonuç: gerçek log örneğinde 50+ ZES/Trugo/Eşarj/Shell istasyonu
    reddedildi, 4 hotspot'tan 2'sinde hiç istasyon bulunamadı.

Yeni yaklaşım: istasyonun polyline'a en yakın noktadan uzaklığı (haversine)
bir eşikten küçükse "rotada" kabul edilir. Service alanları (~300 m), çıkış
rampaları (~500 m–1.5 km) kabul; uzak detour istasyonları (>1 km) eler.
Karşı şerit istasyonları (perpendicular ~100 m) BU yöntemle ayırt EDİLEMEZ;
bilinçli tradeoff (Seçenek B).
"""

from typing import List, Optional, Tuple

from app.services.base_service import ExternalAPIError
from app.utils.geo import haversine_km
from app.utils.logger import get_logger

logger = get_logger("polyline_filter")


# =============================================================================
# CONSTANTS
# =============================================================================

# Preferred strict route corridor distance (km).
PERP_DISTANCE_THRESHOLD_KM = 1.0

# If strict filtering removes every candidate, progressively relax the corridor.
# Highway service areas and access roads can be a few km away from Google's
# canonical road polyline, especially on sparse long-distance routes.
RELAXED_PERP_DISTANCE_THRESHOLDS_KM = (PERP_DISTANCE_THRESHOLD_KM, 3.0, 5.0, 10.0)

# Polyline örnekleme aralığı (km). 500 m: 1 km eşik için yeterli granularite,
# perf için ham polyline'ı (5–15 bin nokta) seyrekleştirir.
_POLYLINE_SAMPLE_INTERVAL_KM = 0.5

# Perf koruması: maksimum örnek nokta sayısı (~1000 km @ 500 m = 2000 nokta).
_POLYLINE_MAX_SAMPLES = 2000


# =============================================================================
# HELPERS
# =============================================================================

def decode_route_polyline_coords(route_polyline: str) -> List[Tuple[float, float]]:
    """
    Google encoded polyline → seyrek örneklenmiş (lat, lon) listesi.

    Hata veya boş input → boş liste (filtre devre dışı kalır, tüm
    istasyonlar kabul edilir → fail-open davranış).
    """
    if not route_polyline:
        raise ExternalAPIError("PolylineCorridor", 502, "Route polyline is empty")
    try:
        import polyline as polyline_lib
        coords = polyline_lib.decode(route_polyline)
    except Exception as e:
        logger.warning(f"Polyline decode failed: {e}")
        raise ExternalAPIError("PolylineCorridor", 502, "Route polyline could not be decoded") from e

    if not coords:
        raise ExternalAPIError("PolylineCorridor", 502, "Route polyline decoded to zero points")

    # Mesafe bazlı seyreltme — her ~500 m'de bir nokta.
    sampled: List[Tuple[float, float]] = []
    last_kept: Optional[Tuple[float, float]] = None
    accumulated = 0.0
    for lat, lon in coords:
        if last_kept is None:
            sampled.append((lat, lon))
            last_kept = (lat, lon)
            continue
        accumulated += haversine_km(last_kept[0], last_kept[1], lat, lon)
        if accumulated >= _POLYLINE_SAMPLE_INTERVAL_KM:
            sampled.append((lat, lon))
            last_kept = (lat, lon)
            accumulated = 0.0
            if len(sampled) >= _POLYLINE_MAX_SAMPLES:
                break

    # Son noktayı (varış) ekle — varışa yakın istasyonların kaybolmaması için.
    if coords[-1] != sampled[-1]:
        sampled.append(coords[-1])

    return sampled


def min_distance_to_polyline_km(
    station_lat: float,
    station_lon: float,
    polyline_coords: List[Tuple[float, float]],
) -> float:
    """
    İstasyondan polyline'ın en yakın vertex'ine haversine mesafe (km).
    Boş polyline → +inf.

    Not: Vertex bazlı (segment bazlı değil). 500 m örnekleme ile yaklaşık
    perpendicular distance'a denk; worst case ~250 m fazla tahmin.
    """
    if not polyline_coords:
        return float("inf")
    return min(
        haversine_km(station_lat, station_lon, plat, plon)
        for plat, plon in polyline_coords
    )


def check_stations_on_polyline(
    station_coords: List[Tuple[float, float]],
    polyline_coords: Optional[List[Tuple[float, float]]],
    max_perp_km: float = PERP_DISTANCE_THRESHOLD_KM,
) -> List[bool]:
    """
    Her istasyon için: polyline'a minimum mesafe ≤ max_perp_km mı?

    Davranış:
        - station_coords boş → boş liste
        - polyline_coords boş (decode hatası vs.) → hepsi True (fail-open)
        - Aksi halde her istasyon için True/False
    """
    if not station_coords:
        return []
    if polyline_coords is None:
        return [True] * len(station_coords)
    if not polyline_coords:
        raise ExternalAPIError("PolylineCorridor", 502, "Polyline corridor filter has no route geometry")

    return [
        min_distance_to_polyline_km(slat, slon, polyline_coords) <= max_perp_km
        for slat, slon in station_coords
    ]


def check_stations_on_polyline_with_relaxed_fallback(
    station_coords: List[Tuple[float, float]],
    polyline_coords: Optional[List[Tuple[float, float]]],
    thresholds_km: Tuple[float, ...] = RELAXED_PERP_DISTANCE_THRESHOLDS_KM,
) -> Tuple[List[bool], Optional[float]]:
    """
    Apply the strict corridor first, then relax only if every candidate fails.

    Returns:
        (flags, threshold_used)

    ``polyline_coords is None`` is an explicit caller bypass and returns every
    candidate as accepted with ``threshold_used=None``. An empty list is not a
    bypass: it means decoded route geometry was invalid and remains a typed
    upstream contract error.
    """
    if not thresholds_km:
        raise ValueError("At least one polyline corridor threshold is required")

    if not station_coords:
        return [], thresholds_km[0]
    if polyline_coords is None:
        return [True] * len(station_coords), None
    if not polyline_coords:
        raise ExternalAPIError("PolylineCorridor", 502, "Polyline corridor filter has no route geometry")

    last_flags: List[bool] = []
    for threshold_km in thresholds_km:
        flags = check_stations_on_polyline(
            station_coords,
            polyline_coords,
            max_perp_km=threshold_km,
        )
        if any(flags):
            return flags, threshold_km
        last_flags = flags

    return last_flags, thresholds_km[-1]
