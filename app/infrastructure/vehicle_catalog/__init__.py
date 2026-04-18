# Vehicle Catalog - abstraction layer for vehicle data access
from .models import VehicleSpec, ChargeCurve, ChargeCurvePoint, ConnectorType, VehicleType
from .resolver import resolve_vehicle_spec

# Geriye dönük uyumluluk aliasları
VehicleModel = VehicleSpec
VehiclePhysicsProfile = VehicleSpec
def get_vehicle_model(model_id: str) -> VehicleSpec:
    """
    Kullanımdan Kaldırıldı. Araçlar artık .NET üzerinden VehiclePayload olarak gelmektedir.
    """
    raise ValueError(f"Mock vehicle catalog is deprecated. MSSQL database payload required. Model asked: {model_id}")


def get_available_vehicle_ids() -> list:
    """Sadece geriye dönük uyumluluk için boş döner."""
    return []


__all__ = [
    "VehicleSpec",
    "VehicleModel",
    "VehiclePhysicsProfile",
    "ChargeCurve", 
    "ChargeCurvePoint",
    "ConnectorType",
    "VehicleType",
    "get_vehicle_model",
    "get_available_vehicle_ids",
    "resolve_vehicle_spec",
]
