# Vehicle Catalog - abstraction layer for vehicle data access
from .models import VehicleSpec, ChargeCurve, ChargeCurvePoint, ConnectorType, VehicleType
from .interfaces import IVehicleCatalog
from .file_catalog import FileVehicleCatalog


# =============================================================================
# SINGLETON CATALOG INSTANCE
# =============================================================================

_catalog = None

def _get_catalog() -> FileVehicleCatalog:
    """Lazy-load singleton FileVehicleCatalog."""
    global _catalog
    if _catalog is None:
        _catalog = FileVehicleCatalog()
    return _catalog


# =============================================================================
# BACKWARD COMPATIBILITY (eski vehicle_models.py yerine)
# =============================================================================

# VehicleModel artık VehicleSpec'in alias'ı
VehicleModel = VehicleSpec
VehiclePhysicsProfile = VehicleSpec


def get_vehicle_model(model_id: str) -> VehicleSpec:
    """
    Araç modeli ID'sine göre VehicleSpec döner.
    
    Eski vehicle_models.py ile aynı davranış:
    - Bulunursa VehicleSpec döner
    - Bulunamazsa ValueError fırlatır
    
    Args:
        model_id: Araç tanımlayıcısı (ör. "tesla_model_3_long_range_2019")
    
    Returns:
        VehicleSpec instance
    
    Raises:
        ValueError: Araç bulunamazsa
    """
    catalog = _get_catalog()
    spec = catalog.get_by_id(model_id)
    if spec is not None:
        return spec
    raise ValueError(f"Unknown vehicle model: {model_id}")


def get_available_vehicle_ids() -> list:
    """Tüm mevcut araç ID'lerini döner."""
    catalog = _get_catalog()
    return sorted(v.id for v in catalog.search(limit=5000))


__all__ = [
    "VehicleSpec",
    "VehicleModel",
    "VehiclePhysicsProfile",
    "ChargeCurve", 
    "ChargeCurvePoint",
    "ConnectorType",
    "VehicleType",
    "IVehicleCatalog",
    "FileVehicleCatalog",
    "get_vehicle_model",
    "get_available_vehicle_ids",
]
