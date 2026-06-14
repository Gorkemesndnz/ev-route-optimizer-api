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

import math
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

    # Adaptif örnekleme aralığı: rota _POLYLINE_MAX_SAMPLES × 500 m'den (~1000 km)
    # uzunsa aralık büyütülür; örnekleme hiçbir zaman rotanın bir bölümünü
    # kapsam dışı bırakmaz. Sabit aralık + erken break, >1000 km rotalarda
    # varışa kadar olan geometriyi tek kirişe indirip yol üstü istasyonları
    # yanlışlıkla off-route sayıyordu (PMR-20260612-001).
    total_route_km = sum(
        haversine_km(a_lat, a_lon, b_lat, b_lon)
        for (a_lat, a_lon), (b_lat, b_lon) in zip(coords, coords[1:])
    )
    sample_interval_km = max(
        _POLYLINE_SAMPLE_INTERVAL_KM,
        total_route_km / max(1, _POLYLINE_MAX_SAMPLES - 1),
    )

    # Mesafe bazlı seyreltme — adaptif aralıkla tüm rota kapsanır.
    # Birikim ardışık nokta mesafeleriyle (yol boyu) hesaplanır; önceki kod
    # her adımda "son tutulan noktaya" olan mesafeyi yeniden ekleyerek
    # birikimi şişiriyor ve örnek sayısını öngörülemez yapıyordu.
    sampled: List[Tuple[float, float]] = []
    prev: Optional[Tuple[float, float]] = None
    accumulated = 0.0
    for lat, lon in coords:
        if prev is None:
            sampled.append((lat, lon))
            prev = (lat, lon)
            continue
        accumulated += haversine_km(prev[0], prev[1], lat, lon)
        prev = (lat, lon)
        if accumulated >= sample_interval_km:
            sampled.append((lat, lon))
            accumulated = 0.0

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
    İstasyondan polyline'ın en yakın segmentine mesafe (km).
    Boş polyline → +inf.

    Kısa rota parçaları için lokal equirectangular projeksiyon yeterli
    hassasiyet verir ve sparse overview polyline'larda vertex arası istasyonları
    yanlışlıkla off-route saymayı önler.
    """
    if not polyline_coords:
        return float("inf")
    if len(polyline_coords) == 1:
        plat, plon = polyline_coords[0]
        return haversine_km(station_lat, station_lon, plat, plon)

    return min(
        _point_to_segment_distance_km(station_lat, station_lon, a_lat, a_lon, b_lat, b_lon)
        for (a_lat, a_lon), (b_lat, b_lon) in zip(polyline_coords, polyline_coords[1:])
    )


def _point_to_segment_distance_km(
    point_lat: float,
    point_lon: float,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
) -> float:
    lat_scale_km = 110.574
    lon_scale_km = 111.320 * math.cos(math.radians(point_lat))

    ax = (start_lon - point_lon) * lon_scale_km
    ay = (start_lat - point_lat) * lat_scale_km
    bx = (end_lon - point_lon) * lon_scale_km
    by = (end_lat - point_lat) * lat_scale_km

    abx = bx - ax
    aby = by - ay
    ab_len_sq = abx * abx + aby * aby
    if ab_len_sq <= 0:
        return haversine_km(point_lat, point_lon, start_lat, start_lon)

    t = max(0.0, min(1.0, -(ax * abx + ay * aby) / ab_len_sq))
    closest_x = ax + t * abx
    closest_y = ay + t * aby
    return (closest_x * closest_x + closest_y * closest_y) ** 0.5


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
