# Vehicle Catalog - abstraction layer for vehicle data access
from .models import VehicleSpec, ChargeCurve, ChargeCurvePoint, ConnectorType, VehicleType
from .resolver import resolve_vehicle_spec
from .mock_data import MOCK_NET_VEHICLE_PAYLOAD


# =============================================================================
# BACKWARD COMPATIBILITY
# =============================================================================

# VehicleModel artık VehicleSpec'in alias'ı
VehicleModel = VehicleSpec
VehiclePhysicsProfile = VehicleSpec


def get_vehicle_model(model_id: str) -> VehicleSpec:
    """
    Testler veya backward compatibility icin sadece 1 adet Mock aracı(.NET payload formatında) dondurur.
    Gercek kullanimlarda artik orchestration tarafinda "request.vehicle_spec"(.NET response'u) kullanılmaktadir.
    
    Args:
        model_id: "abarth_500e_hatchback_2024" (Mock ID)
    
    Returns:
        VehicleSpec instance
    """
    if model_id == MOCK_NET_VEHICLE_PAYLOAD.slug:
        return resolve_vehicle_spec(MOCK_NET_VEHICLE_PAYLOAD)
    
    raise ValueError(f"Unknown vehicle model: {model_id} (Only \"{MOCK_NET_VEHICLE_PAYLOAD.slug}\" is available for mocking)")


def get_available_vehicle_ids() -> list:
    """Sadece test mock ID'sini döner."""
    return [MOCK_NET_VEHICLE_PAYLOAD.slug]


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
