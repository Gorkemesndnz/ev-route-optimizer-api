"""
Vehicle Models
===============

V2.0 - Hybrid approach with FileVehicleCatalog integration.

This module provides backward compatibility with the legacy VEHICLE_DB
while also supporting the new FileVehicleCatalog for expanded vehicle data.

Lookup order:
1. FileVehicleCatalog (1000+ vehicles from Open-EV-Data)
2. Legacy VEHICLE_DB (fallback for known vehicles)
"""

from dataclasses import dataclass
from typing import Optional

# Lazy import to avoid circular dependencies
_catalog = None


def _get_catalog():
    """Lazy load the FileVehicleCatalog singleton"""
    global _catalog
    if _catalog is None:
        try:
            from app.infrastructure.vehicle_catalog import FileVehicleCatalog
            _catalog = FileVehicleCatalog()
        except Exception:
            _catalog = None
    return _catalog


@dataclass
class VehicleModel:
    """
    V2.9 - MainCalculator ile %100 uyumlu araç veri yapısı
    
    Bu dataclass hem legacy VEHICLE_DB hem de yeni FileVehicleCatalog
    ile uyumlu çalışır.
    
    V2.9: Rejeneratif frenleme alanları eklendi.
    """
    model_name: str

    # Fiziksel özellikler
    curb_weight_kg: int
    battery_capacity_kwh: float

    # Tüketim (Wh/km) → MainCalculator'da kwh/100km'e çevriliyor
    base_consumption_wh_km: float     

    # Şarj özellikleri
    connector_type: str               # "CCS" | "CHAdeMO" | "Type2"
    avg_dc_charge_rate_kw: float      # DC max power
    avg_ac_charge_rate_kw: float      # AC max power

    # Yardımcı sistemler (HVAC, farlar, elektronik)
    auxiliary_power_kw: float = 1.2
    
    # 🔧 V2.9: Rejeneratif frenleme özellikleri
    regen_efficiency: float = 0.65    # Nominal regen verimliliği (0-1)
    regen_max_power_kw: float = 70.0  # Maksimum regen gücü (kW)
    battery_chemistry: str = "NMC"    # NMC, LFP, NCA - soğuk hava davranışı için


# =============================================================================
# LEGACY VEHICLE DATABASE (backward compatibility)
# =============================================================================

LEGACY_VEHICLE_DB = {
    "mg4_51kwh": VehicleModel(
        model_name="MG4 Electric 51 kWh",
        curb_weight_kg=1736,             # ✅ V1.6: base_weight_kg → curb_weight_kg
        battery_capacity_kwh=50.8,
        base_consumption_wh_km=159.0,    # 15.9 kWh/100km
        auxiliary_power_kw=1.2,          # ✅ V1.6: HVAC için
        connector_type="CCS",
        avg_dc_charge_rate_kw=85.0,      # gerçek ortalama
        avg_ac_charge_rate_kw=11.0,
    ),

    "tesla_model_3_long_range": VehicleModel(
        model_name="Tesla Model 3 Long Range",
        curb_weight_kg=1847,             # ✅ V1.6: base_weight_kg → curb_weight_kg
        battery_capacity_kwh=75.0,       # usable
        base_consumption_wh_km=145.0,    # 14.5 kWh/100km
        auxiliary_power_kw=1.0,          # ✅ V1.6: Tesla daha verimli HVAC
        connector_type="CCS",
        avg_dc_charge_rate_kw=250.0,
        avg_ac_charge_rate_kw=11.0,
    ),

    "opel_frontera_44": VehicleModel(
        model_name="Opel Frontera Electric 44 kWh",
        curb_weight_kg=1589,             # ✅ V1.6: base_weight_kg → curb_weight_kg
        battery_capacity_kwh=43.8,
        base_consumption_wh_km=183.0,    # 18.3 kWh/100km (Real)
        auxiliary_power_kw=1.3,          # ✅ V1.6: Opel daha yüksek HVAC
        connector_type="CCS",
        avg_dc_charge_rate_kw=60.0,      # avg based on 10→80 data
        avg_ac_charge_rate_kw=7.4,
    ),
}


# =============================================================================
# VEHICLE_DB ALIAS (backward compatibility)
# =============================================================================

VEHICLE_DB = LEGACY_VEHICLE_DB


# =============================================================================
# VEHICLE LOOKUP FUNCTIONS
# =============================================================================

def _convert_spec_to_model(spec) -> VehicleModel:
    """Convert VehicleSpec from catalog to legacy VehicleModel"""
    return VehicleModel(
        model_name=spec.display_name,
        curb_weight_kg=spec.curb_weight_kg,
        battery_capacity_kwh=spec.battery_capacity_kwh,
        base_consumption_wh_km=spec.base_consumption_wh_km,
        connector_type=spec.connector_type.value,
        avg_dc_charge_rate_kw=spec.dc_max_kw,
        avg_ac_charge_rate_kw=spec.ac_max_kw,
        auxiliary_power_kw=spec.auxiliary_power_kw,
        # 🔧 V2.9: Rejeneratif frenleme alanları
        regen_efficiency=getattr(spec, 'regen_efficiency', 0.65),
        regen_max_power_kw=getattr(spec, 'regen_max_power_kw', 70.0),
        battery_chemistry=getattr(spec, 'battery_chemistry', 'NMC'),
    )


def get_vehicle_model(model_id: str) -> VehicleModel:
    """
    Get vehicle model by ID.
    
    Lookup order:
    1. FileVehicleCatalog (if available and data loaded)
    2. Legacy VEHICLE_DB
    
    Args:
        model_id: Vehicle identifier (e.g. "tesla_model_3_long_range")
        
    Returns:
        VehicleModel instance
        
    Raises:
        ValueError: If vehicle not found in any source
    """
    # Try catalog first
    catalog = _get_catalog()
    if catalog is not None:
        spec = catalog.get_by_id(model_id)
        if spec is not None:
            return _convert_spec_to_model(spec)
    
    # Fallback to legacy DB
    model = LEGACY_VEHICLE_DB.get(model_id)
    if model is not None:
        return model
    
    raise ValueError(f"Unknown vehicle model: {model_id}")


def get_available_vehicle_ids() -> list:
    """
    Get list of all available vehicle IDs.
    
    Returns:
        List of vehicle ID strings
    """
    ids = set(LEGACY_VEHICLE_DB.keys())
    
    catalog = _get_catalog()
    if catalog is not None:
        for v in catalog.search(limit=2000):
            ids.add(v.id)
    
    return sorted(ids)


# Alias for main_calculator.py compatibility
VehiclePhysicsProfile = VehicleModel
