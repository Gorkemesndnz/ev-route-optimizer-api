"""
Sustainability Calculator v2.0
===============================

V1.3 Enterprise uyumlu CO2 tasarrufu ve çevresel etki hesaplama modülü.

Özellikler:
- CO2 tasarrufu hesaplama (bölgeye özel)
- Equivalent metrics (ağaç, benzin, enerji)
- Validation ve type safety
- Structured logging ile detaylı takip
- Region-specific multipliers

Kullanım:
    from app.sustainability_calculator import calculate_co2_savings
    
    co2_kg = calculate_co2_savings(452.0)
    trees = calculate_equivalent_trees(co2_kg)
    fuel_liters = calculate_fuel_savings_liters(co2_kg)
"""

from typing import Optional, Dict, Any
from app.utils.config_manager import config
from app.utils.logger import get_logger


# =============================================================================
# CONSTANTS
# =============================================================================
CO2_KG_PER_LITER_GASOLINE = 2.31  # Benzin litre başına CO2 (kg)
CO2_ABSORPTION_PER_TREE_YEAR_KG = 21.0  # 1 ağacın yıllık CO2 absorpsiyonu
KWH_PER_LITER_GASOLINE = 8.89  # 1 litre benzin ≈ 8.89 kWh enerji

logger = get_logger("sustainability_calculator")


# =============================================================================
# MAIN CALCULATION FUNCTIONS
# =============================================================================

def calculate_co2_savings(
    total_distance_km: float, 
    region_code: str = "TR"
) -> float:
    """
    Elektrikli araç kullanımında tasarruf edilen CO2 miktarını (kg) hesaplar.
    
    Args:
        total_distance_km: Toplam mesafe (km)
        region_code: Bölge kodu (varsayılan "TR")
    
    Returns:
        CO2 tasarrufu (kg), 2 decimal hassasiyetle
    
    Raises:
        ValueError: Negatif mesafe için
    """
    try:
        # Validation
        if total_distance_km < 0:
            raise ValueError("Distance cannot be negative")
        
        if total_distance_km == 0:
            return 0.0
        
        # Configuration values
        co2_avg_ice_l_per_100km = config.get_co2_avg_ice_consumption()
        region_multiplier = _get_region_multiplier(region_code)
        
        # CO2 calculation
        total_liters = (co2_avg_ice_l_per_100km / 100.0) * total_distance_km
        total_co2_kg_produced = total_liters * CO2_KG_PER_LITER_GASOLINE * region_multiplier
        
        result = round(total_co2_kg_produced, 2)
        
        # Structured logging
        logger.info(
            "CO2 savings calculated",
            distance_km=total_distance_km,
            region_code=region_code,
            co2_kg=result,
            ice_consumption=co2_avg_ice_l_per_100km,
            region_multiplier=region_multiplier
        )
        
        return result
        
    except ValueError as e:
        logger.warning("CO2 calculation validation error", error=str(e), distance=total_distance_km)
        raise
    except Exception as e:
        logger.error("CO2 calculation failed", error=str(e), distance=total_distance_km, region=region_code)
        return 0.0


def calculate_equivalent_trees(co2_kg: float) -> int:
    """
    CO2 miktarının kaç ağaca denk geldiğini hesaplar.
    
    Args:
        co2_kg: CO2 miktarı (kg)
    
    Returns:
        Equivalent ağaç sayısı (tamsayı)
    """
    if co2_kg <= 0:
        return 0
    
    trees = round(co2_kg / CO2_ABSORPTION_PER_TREE_YEAR_KG)
    
    logger.debug(
        "Tree equivalent calculated",
        co2_kg=co2_kg,
        equivalent_trees=trees
    )
    
    return trees


def calculate_fuel_savings_liters(co2_kg: float) -> float:
    """
    CO2 tasarrufunun kaç litre benzine denk geldiğini hesaplar.
    
    Args:
        co2_kg: CO2 miktarı (kg)
    
    Returns:
        Equivalent benzin litresi
    """
    if co2_kg <= 0:
        return 0.0
    
    liters = round(co2_kg / CO2_KG_PER_LITER_GASOLINE, 2)
    
    logger.debug(
        "Fuel equivalent calculated",
        co2_kg=co2_kg,
        fuel_liters=liters
    )
    
    return liters


def calculate_energy_savings_kwh(co2_kg: float) -> float:
    """
    CO2 tasarrufunun kaç kWh enerjiye denk geldiğini hesaplar.
    
    Args:
        co2_kg: CO2 miktarı (kg)
    
    Returns:
        Equivalent enerji (kWh)
    """
    if co2_kg <= 0:
        return 0.0
    
    kwh = round(co2_kg / CO2_KG_PER_LITER_GASOLINE * KWH_PER_LITER_GASOLINE, 2)
    
    logger.debug(
        "Energy equivalent calculated",
        co2_kg=co2_kg,
        energy_kwh=kwh
    )
    
    return kwh


# =============================================================================
# COMPREHENSIVE CALCULATION
# =============================================================================

def calculate_sustainability_metrics(
    total_distance_km: float,
    region_code: str = "TR"
) -> Dict[str, Any]:
    """
    Tüm sürdürülebilirlik metriklerini hesaplar.
    
    Args:
        total_distance_km: Toplam mesafe (km)
        region_code: Bölge kodu
    
    Returns:
        Sürdürülebilirlik metrikleri sözlüğü
    """
    try:
        co2_kg = calculate_co2_savings(total_distance_km, region_code)
        
        metrics = {
            "distance_km": total_distance_km,
            "region_code": region_code,
            "co2_savings_kg": co2_kg,
            "equivalent_trees": calculate_equivalent_trees(co2_kg),
            "fuel_savings_liters": calculate_fuel_savings_liters(co2_kg),
            "energy_savings_kwh": calculate_energy_savings_kwh(co2_kg),
            "calculation_timestamp": logger.handlers[0].formatter.formatTime(
                logger.makeRecord("", 0, "", 0, "", (), None)
            ) if logger.handlers else None
        }
        
        logger.info(
            "Sustainability metrics calculated",
            **metrics
        )
        
        return metrics
        
    except Exception as e:
        logger.error("Sustainability metrics calculation failed", error=str(e))
        return {
            "distance_km": total_distance_km,
            "region_code": region_code,
            "co2_savings_kg": 0.0,
            "equivalent_trees": 0,
            "fuel_savings_liters": 0.0,
            "energy_savings_kwh": 0.0,
            "error": str(e)
        }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _get_region_multiplier(region_code: str) -> float:
    """
    Bölge koduna göre CO2 çarpanını döndürür.
    
    Args:
        region_code: Bölge kodu (TR, EU, US, vb.)
    
    Returns:
        Bölgeye özel CO2 çarpanı
    """
    # Region-specific multipliers
    region_multipliers = {
        "TR": config.get_co2_region_multiplier_tr(),  # Türkiye
        "EU": 1.0,    # Avrupa Birliği
        "US": 1.2,    # Amerika Birleşik Devletleri
        "CN": 1.8,    # Çin
        "IN": 1.6,    # Hindistan
    }
    
    multiplier = region_multipliers.get(region_code.upper(), 1.0)
    
    logger.debug(
        "Region multiplier applied",
        region_code=region_code,
        multiplier=multiplier
    )
    
    return multiplier


# =============================================================================
# BACKWARD COMPATIBILITY
# =============================================================================

def calculate_co2_savings_legacy(total_distance_km: float) -> float:
    """
    Legacy calculate_co2_savings fonksiyonu.
    
    Geriye dönük uyumluluk için korunmuştur.
    Yeni kod calculate_co2_savings() fonksiyonunu kullanmalıdır.
    """
    logger.warning(
        "Legacy function called",
        function="calculate_co2_savings_legacy",
        recommendation="Use calculate_co2_savings() instead"
    )
    return calculate_co2_savings(total_distance_km, "TR")
