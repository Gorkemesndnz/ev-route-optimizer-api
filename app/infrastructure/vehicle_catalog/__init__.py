# Vehicle Catalog - abstraction layer for vehicle data access
from .models import VehicleSpec, ChargeCurve, ChargeCurvePoint, ConnectorType, VehicleType
from .interfaces import IVehicleCatalog
from .file_catalog import FileVehicleCatalog

__all__ = [
    "VehicleSpec",
    "ChargeCurve", 
    "ChargeCurvePoint",
    "ConnectorType",
    "VehicleType",
    "IVehicleCatalog",
    "FileVehicleCatalog",
]
