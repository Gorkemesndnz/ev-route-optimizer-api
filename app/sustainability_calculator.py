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
from datetime import datetime
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
    ev_consumption_kwh: float = 0.0,  # 🆕 EV tüketimi parametresi
    region_code: str = "TR"
) -> float:
    """
    Elektrikli araç kullanımında tasarruf edilen NET CO2 miktarını (kg) hesaplar.
    
    🆕 Gerçekçi Formül: CO2 Tasarrufu = (Benzinli Araç CO2) - (Elektrikli Araç CO2)
    
    Args:
        total_distance_km: Toplam mesafe (km)
        ev_consumption_kwh: Elektrikli aracın toplam elektrik tüketimi (kWh)
        region_code: Bölge kodu (varsayılan "TR")
    
    Returns:
        NET CO2 tasarrufu (kg), 2 decimal hassasiyetle
        Pozitif = tasarruf, Negatif = EV daha fazla CO2 üretiyor
    
    Raises:
        ValueError: Negatif mesafe veya tüketim için
    """
    try:
        # Validation
        if total_distance_km < 0:
            raise ValueError("Distance cannot be negative")
        if ev_consumption_kwh < 0:
            raise ValueError("EV consumption cannot be negative")
        
        if total_distance_km == 0:
            return 0.0
        
        # Configuration values
        co2_avg_ice_l_per_100km = config.get_co2_avg_ice_consumption()
        region_multiplier = _get_region_multiplier(region_code)
        electricity_grid_factor = _get_electricity_emission_factor(region_code)
        
        # 🆕 ICE araç CO2 hesabı (mevcut mantık)
        total_liters = (co2_avg_ice_l_per_100km / 100.0) * total_distance_km
        ice_co2_kg = total_liters * CO2_KG_PER_LITER_GASOLINE * region_multiplier
        
        # 🆕 EV araç CO2 hesabı (yeni mantık)
        # gCO2/kWh -> kgCO2/kWh çevirimi
        ev_co2_kg = (ev_consumption_kwh * electricity_grid_factor) / 1000.0
        
        # 🆕 NET CO2 tasarrufu
        net_co2_savings = ice_co2_kg - ev_co2_kg
        
        result = round(net_co2_savings, 2)
        
        # Structured logging
        logger.info(
            "CO2 savings calculated",
            distance_km=total_distance_km,
            region_code=region_code,
            ev_consumption_kwh=ev_consumption_kwh,
            ice_co2_kg=round(ice_co2_kg, 2),
            ev_co2_kg=round(ev_co2_kg, 2),
            net_co2_savings_kg=result,
            ice_consumption=co2_avg_ice_l_per_100km,
            region_multiplier=region_multiplier,
            electricity_grid_factor=electricity_grid_factor
        )
        
        return result
        
    except ValueError as e:
        logger.warning("CO2 calculation validation error", error=str(e), distance=total_distance_km)
        raise
    except Exception as e:
        logger.error("CO2 calculation failed", error=str(e), distance=total_distance_km, region=region_code)
        raise  # Hatayı yukarı ilet, gizleme


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


def calculate_energy_savings_kwh(total_distance_km: float) -> float:
    """
    Elektrikli araç yerine benzinli araç kullanılsaydı harcanan enerji eşdeğerini hesaplar.
    
    Formül: Mesafe → Benzin litresi → kWh enerji
    
    Args:
        total_distance_km: Toplam mesafe (km)
    
    Returns:
        Equivalent enerji tasarrufu (kWh)
    """
    if total_distance_km <= 0:
        return 0.0
    
    # Configuration'dan benzin tüketimini al
    co2_avg_ice_l_per_100km = config.get_co2_avg_ice_consumption()
    
    # Toplam benzin tüketimini hesapla
    total_liters = (co2_avg_ice_l_per_100km / 100.0) * total_distance_km
    
    # Benzini enerji eşdeğerine çevir
    kwh = round(total_liters * KWH_PER_LITER_GASOLINE, 2)
    
    logger.debug(
        "Energy equivalent calculated",
        distance_km=total_distance_km,
        fuel_liters=total_liters,
        energy_kwh=kwh
    )
    
    return kwh


# =============================================================================
# COMPREHENSIVE CALCULATION
# =============================================================================

def calculate_sustainability_metrics(
    total_distance_km: float,
    ev_consumption_kwh: float = 0.0,  # 🆕 EV tüketimi parametresi
    region_code: str = "TR"
) -> Dict[str, Any]:
    """
    🆕 Tüm sürdürülebilirlik metriklerini hesaplar (gerçekçi CO2 ile).
    
    Args:
        total_distance_km: Toplam mesafe (km)
        ev_consumption_kwh: Elektrikli aracın toplam elektrik tüketimi (kWh)
        region_code: Bölge kodu
    
    Returns:
        Sürdürülebilirlik metrikleri sözlüğü
    """
    try:
        co2_kg = calculate_co2_savings(total_distance_km, ev_consumption_kwh, region_code)
        
        metrics = {
            "distance_km": total_distance_km,
            "region_code": region_code,
            "co2_savings_kg": co2_kg,
            "equivalent_trees": calculate_equivalent_trees(co2_kg),
            "fuel_savings_liters": calculate_fuel_savings_liters(co2_kg),
            "energy_savings_kwh": calculate_energy_savings_kwh(total_distance_km),  # Mesafeden hesapla
            "calculation_timestamp": datetime.utcnow().isoformat()
        }
        
        logger.info(
            "Sustainability metrics calculated",
            **metrics
        )
        
        return metrics
        
    except Exception as e:
        logger.error("Sustainability metrics calculation failed", error=str(e))
        raise  # Hatayı yukarı ilet, gizleme


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


def _get_electricity_emission_factor(region_code: str) -> float:
    """
    🆕 Bölge koduna göre elektrik şebekesi emisyon faktörünü döndürür (gCO2/kWh).
    
    Args:
        region_code: Bölge kodu (TR, EU, US, vb.)
    
    Returns:
        Bölgeye özel elektrik şebekesi emisyon faktörü (gCO2/kWh)
    """
    # 🆕 Region-specific electricity emission factors (gCO2/kWh)
    electricity_emission_factors = {
        "TR": config.get_electricity_emission_factor_tr(),  # Türkiye: 475 gCO2/kWh
        "EU": config.get_electricity_emission_factor_eu(),  # AB: 300 gCO2/kWh
        "US": config.get_electricity_emission_factor_us(),  # ABD: 400 gCO2/kWh
        "CN": 600.0,  # Çin: 600 gCO2/kWh
        "IN": 550.0,  # Hindistan: 550 gCO2/kWh
    }
    
    factor = electricity_emission_factors.get(region_code.upper(), 475.0)  # Default: Türkiye
    
    logger.debug(
        "Electricity emission factor applied",
        region_code=region_code,
        emission_factor_gco2_per_kwh=factor
    )
    
    return factor


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
    # 🔧 DÜZELT: Doğru parametre sırası ile çağır
    # calculate_co2_savings(distance, ev_consumption_kwh, region_code)
    return calculate_co2_savings(total_distance_km, 0.0, "TR")
