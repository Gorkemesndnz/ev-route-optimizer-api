"""
Charging Tariffs Module
=======================

🔧 V3.1: Türkiye şarj istasyonu fiyat tarifeleri.

Operatör bazlı dinamik fiyatlandırma:
- İstasyon adından operatör tespiti
- Güç seviyesine göre fiyat seçimi (DC/AC)
- Varsayılan fiyat fallback
"""

import json
import os
from typing import Optional, Tuple
import structlog

logger = structlog.get_logger(__name__)

# JSON dosyasının yolu
TARIFFS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "charging_tariffs.json")

# Varsayılan fiyatlar (operatör bulunamazsa)
DEFAULT_DC_PRICE = 11.00  # TRY/kWh
DEFAULT_AC_PRICE = 9.00   # TRY/kWh

# Cache
_tariffs_cache: Optional[list] = None


def _load_tariffs() -> list:
    """Tarifeleri JSON'dan yükle (cache ile)."""
    global _tariffs_cache
    
    if _tariffs_cache is not None:
        return _tariffs_cache
    
    try:
        with open(TARIFFS_FILE, "r", encoding="utf-8") as f:
            _tariffs_cache = json.load(f)
            logger.info(f"Loaded {len(_tariffs_cache)} operator tariffs from JSON")
            return _tariffs_cache
    except FileNotFoundError:
        logger.warning(f"Tariffs file not found: {TARIFFS_FILE}")
        _tariffs_cache = []
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in tariffs file: {e}")
        _tariffs_cache = []
        return []


def _find_operator(station_name: str) -> Optional[dict]:
    """İstasyon adından operatör bul."""
    tariffs = _load_tariffs()
    station_lower = station_name.lower()
    
    for operator in tariffs:
        # Brand adı kontrolü
        if operator["brand"].lower() in station_lower:
            return operator
        
        # Alias kontrolü
        for alias in operator.get("aliases", []):
            if alias.lower() in station_lower:
                return operator
    
    return None


def get_price_for_station(
    station_name: str,
    power_kw: float,
    is_dc: bool = True
) -> Tuple[float, str]:
    """
    İstasyon için kWh fiyatını hesapla.
    
    Args:
        station_name: İstasyon adı (operatör tespiti için)
        power_kw: Şarj gücü (kW)
        is_dc: DC şarj mı? (False = AC)
    
    Returns:
        (price_per_kwh, operator_name)
    """
    operator = _find_operator(station_name)
    
    if not operator:
        # Operatör bulunamadı - varsayılan fiyat
        default_price = DEFAULT_DC_PRICE if is_dc else DEFAULT_AC_PRICE
        logger.debug(f"Operator not found for '{station_name}', using default: {default_price} TRY/kWh")
        return default_price, "Unknown"
    
    socket_type = "DC" if is_dc else "AC"
    
    # Uygun tarifi bul
    for tariff in operator.get("tariffs", []):
        if tariff["socket_type"] != socket_type:
            continue
        
        threshold = tariff.get("power_threshold_kw", 100)
        
        if power_kw < threshold:
            price = tariff["below_price"]
        else:
            price = tariff["above_price"]
        
        logger.debug(
            f"Price found: {operator['brand']} {socket_type} {power_kw}kW -> {price} TRY/kWh"
        )
        return price, operator["brand"]
    
    # Tarif bulunamadı - varsayılan
    default_price = DEFAULT_DC_PRICE if is_dc else DEFAULT_AC_PRICE
    return default_price, operator["brand"]


def get_operator_from_station_name(station_name: str) -> Optional[str]:
    """İstasyon adından operatör adını çıkar."""
    operator = _find_operator(station_name)
    return operator["brand"] if operator else None


# Test
if __name__ == "__main__":
    test_cases = [
        ("ZES Charging Station Istanbul", 150, True),
        ("Eşarj DC Fast Charger", 50, True),
        ("Tesla Supercharger V3", 250, True),
        ("Shell Recharge Point", 180, True),
        ("Trugo Ankara", 120, True),
        ("Random Station", 100, True),
        ("Voltrun AC Charger", 22, False),
    ]
    
    for name, power, is_dc in test_cases:
        price, operator = get_price_for_station(name, power, is_dc)
        print(f"{name} ({power}kW {'DC' if is_dc else 'AC'}) -> {operator}: {price} TRY/kWh")
