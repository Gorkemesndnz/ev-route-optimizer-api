"""
Vehicle Catalog Interfaces
===========================

Protocol definitions for vehicle catalog implementations.
Using Protocol (structural subtyping) for flexibility.
"""

from typing import Protocol, List, Optional, runtime_checkable
from .models import VehicleSpec, ChargeCurve


@runtime_checkable
class IVehicleCatalog(Protocol):
    """
    Vehicle catalog interface.
    
    Implementations:
    - FileVehicleCatalog: Reads from JSON files (current)
    - DbVehicleCatalog: Reads from database (future)
    
    Usage:
        catalog: IVehicleCatalog = FileVehicleCatalog()
        vehicles = catalog.search(query="Tesla Model 3")
        spec = catalog.get_by_id("tesla_model_3_long_range_2019")
        curve = catalog.get_charge_curve("tesla_model_3_long_range_2019")
    """
    
    def get_vehicle_count(self) -> int:
        """Get total number of vehicles in catalog"""
        ...
    
    def get_all_brands(self) -> List[str]:
        """
        Get all available brand names.
        
        Returns:
            Sorted list of unique brand names
        """
        ...
    
    def search(
        self,
        query: str = "",
        brand: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        limit: int = 50
    ) -> List[VehicleSpec]:
        """
        Search vehicles with optional filters.
        
        Args:
            query: Free text search (matches brand, model, variant)
            brand: Filter by exact brand name (case-insensitive)
            year_min: Minimum release year (inclusive)
            year_max: Maximum release year (inclusive)
            limit: Maximum number of results
            
        Returns:
            List of matching VehicleSpec objects
        """
        ...
    
    def get_by_id(self, vehicle_id: str) -> Optional[VehicleSpec]:
        """
        Get vehicle by its unique ID.
        
        Args:
            vehicle_id: Unique vehicle identifier (e.g. "tesla_model_3_long_range_2019")
            
        Returns:
            VehicleSpec if found, None otherwise
        """
        ...
    
    def get_charge_curve(self, vehicle_id: str) -> Optional[ChargeCurve]:
        """
        Get charging curve for a vehicle.
        
        Args:
            vehicle_id: Unique vehicle identifier
            
        Returns:
            ChargeCurve if available, None otherwise
        """
        ...
    
    def get_default_curve(self, voltage: int = 400) -> ChargeCurve:
        """
        Get default charging curve for vehicles without specific curves.
        
        The default curve is a multiplier curve (0-1) that should be
        multiplied by the vehicle's dc_max_kw to get actual power.
        
        Args:
            voltage: Charging voltage (400 or 800)
            
        Returns:
            Default ChargeCurve with multiplier values
        """
        ...
