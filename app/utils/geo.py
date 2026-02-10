"""
Geo Utilities — Merkezi Coğrafi Hesaplama Fonksiyonları
=======================================================

haversine_km ve calculate_bearing fonksiyonlarının tek kanonik kaynağı.
Tüm modüller bu dosyadan import etmelidir.

Kullanım:
    from app.utils.geo import haversine_km, calculate_bearing
"""

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    İki koordinat arasındaki mesafeyi Haversine formülü ile km olarak hesaplar.

    Args:
        lat1, lon1: Başlangıç koordinatı (derece)
        lat2, lon2: Bitiş koordinatı (derece)

    Returns:
        Mesafe (km)
    """
    R = 6371.0  # Dünya yarıçapı (km)

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    İki nokta arasındaki pusula yönünü (bearing) hesaplar.

    Args:
        lat1, lon1: Başlangıç koordinatı (derece)
        lat2, lon2: Bitiş koordinatı (derece)

    Returns:
        0-360 derece arası bearing (0=Kuzey, 90=Doğu, 180=Güney, 270=Batı)
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)

    x = math.sin(delta_lon) * math.cos(lat2_rad)
    y = (math.cos(lat1_rad) * math.sin(lat2_rad) -
         math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon))

    bearing = (math.degrees(math.atan2(x, y)) + 360) % 360
    return bearing
