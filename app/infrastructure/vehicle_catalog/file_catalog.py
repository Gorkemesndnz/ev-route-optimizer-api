"""
File-based Vehicle Catalog Implementation
==========================================

Reads vehicle data from JSON files in the data/processed directory.
Implements IVehicleCatalog protocol.
"""

import json
from pathlib import Path
from typing import List, Optional, Dict
from .models import (
    VehicleSpec,
    ChargeCurve,
    ChargeCurvePoint,
    ConnectorType,
    VehicleType,
)


class FileVehicleCatalog:
    """
    File-based implementation of IVehicleCatalog.
    
    Reads from:
    - data/processed/vehicles_master.json
    - data/processed/charge_curves.json
    
    Thread-safe for read operations (data loaded once at init).
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        """
        Initialize catalog from JSON files.
        
        Args:
            data_dir: Path to processed data directory.
                      Defaults to project_root/data/processed
        """
        if data_dir is None:
            # Navigate from this file to project root
            data_dir = Path(__file__).parents[3] / "data" / "processed"
        
        self._data_dir = data_dir
        self._vehicles: Dict[str, VehicleSpec] = {}
        self._curves: Dict[str, ChargeCurve] = {}
        self._brands: List[str] = []
        
        self._load_data()
    
    def _load_data(self) -> None:
        """Load data from JSON files"""
        self._load_vehicles()
        self._load_curves()
        self._brands = sorted(set(v.brand for v in self._vehicles.values()))
    
    def _load_vehicles(self) -> None:
        """Load vehicles from vehicles_master.json"""
        vehicles_path = self._data_dir / "vehicles_master.json"
        
        if not vehicles_path.exists():
            return
        
        with open(vehicles_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        for v in data.get("vehicles", []):
            try:
                # Parse connector type
                connector_str = v.get("connector_type", "CCS")
                connector_type = ConnectorType.from_string(connector_str)
                
                # Parse vehicle type
                vtype_str = v.get("vehicle_type", "car")
                try:
                    vehicle_type = VehicleType(vtype_str.lower())
                except ValueError:
                    vehicle_type = VehicleType.CAR
                
                spec = VehicleSpec(
                    id=v["id"],
                    source_id=v.get("source_id", ""),
                    brand=v["brand"],
                    model=v["model"],
                    variant=v.get("variant", ""),
                    year=v.get("year", 2023),
                    display_name=v.get("display_name", f"{v['brand']} {v['model']}"),
                    battery_capacity_kwh=float(v["battery_capacity_kwh"]),
                    base_consumption_wh_km=float(v["base_consumption_wh_km"]),
                    connector_type=connector_type,
                    ac_max_kw=float(v.get("ac_max_kw", 11.0)),
                    dc_max_kw=float(v.get("dc_max_kw", 50.0)),
                    charging_voltage=int(v.get("charging_voltage", 400)),
                    curb_weight_kg=int(v.get("curb_weight_kg", 1700)),
                    auxiliary_power_kw=float(v.get("auxiliary_power_kw", 1.2)),
                    has_real_curve=bool(v.get("has_real_curve", False)),
                    vehicle_type=vehicle_type,
                )
                self._vehicles[spec.id] = spec
            except (KeyError, ValueError) as e:
                # Skip invalid entries, log in production
                continue
    
    def _load_curves(self) -> None:
        """Load charging curves from charge_curves.json"""
        curves_path = self._data_dir / "charge_curves.json"
        
        if not curves_path.exists():
            return
        
        with open(curves_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        for vehicle_id, curve_data in data.get("curves", {}).items():
            try:
                points = []
                for p in curve_data.get("points", []):
                    point = ChargeCurvePoint(
                        soc_percent=float(p["soc"]),
                        power_kw=float(p["power_kw"])
                    )
                    points.append(point)
                
                is_measured = curve_data.get("source") == "measured"
                
                curve = ChargeCurve(
                    vehicle_id=vehicle_id,
                    is_measured=is_measured,
                    points=points
                )
                self._curves[vehicle_id] = curve
            except (KeyError, ValueError) as e:
                # Skip invalid entries
                continue
    
    def get_vehicle_count(self) -> int:
        """Get total number of vehicles in catalog"""
        return len(self._vehicles)
    
    def get_all_brands(self) -> List[str]:
        """Get all available brand names (sorted)"""
        return self._brands.copy()
    
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
            List of matching VehicleSpec objects, sorted by display_name
        """
        results = []
        query_lower = query.lower().strip()
        
        for v in self._vehicles.values():
            # Brand filter
            if brand and v.brand.lower() != brand.lower():
                continue
            
            # Year filters
            if year_min and v.year < year_min:
                continue
            if year_max and v.year > year_max:
                continue
            
            # Query filter (fuzzy search in brand, model, variant)
            if query_lower:
                searchable = f"{v.brand} {v.model} {v.variant}".lower()
                if query_lower not in searchable:
                    continue
            
            results.append(v)
            
            if len(results) >= limit:
                break
        
        return sorted(results, key=lambda x: x.display_name)
    
    def get_by_id(self, vehicle_id: str) -> Optional[VehicleSpec]:
        """Get vehicle by its unique ID"""
        return self._vehicles.get(vehicle_id)
    
    def get_charge_curve(self, vehicle_id: str) -> Optional[ChargeCurve]:
        """Get charging curve for a vehicle"""
        return self._curves.get(vehicle_id)
    
    def get_default_curve(self, voltage: int = 400) -> ChargeCurve:
        """
        Get default charging curve for vehicles without specific curves.
        
        Returns a multiplier curve (0-1) that should be multiplied
        by the vehicle's dc_max_kw to get actual power values.
        
        Args:
            voltage: Charging voltage (400 or 800)
            
        Returns:
            Default ChargeCurve with multiplier values (0-1)
        """
        if voltage == 800:
            # 800V vehicles have flatter curves
            points = [
                ChargeCurvePoint(0, 1.0),
                ChargeCurvePoint(30, 1.0),
                ChargeCurvePoint(50, 0.9),
                ChargeCurvePoint(70, 0.7),
                ChargeCurvePoint(80, 0.5),
                ChargeCurvePoint(100, 0.15),
            ]
        else:
            # 400V standard taper curve
            points = [
                ChargeCurvePoint(0, 1.0),
                ChargeCurvePoint(20, 1.0),
                ChargeCurvePoint(40, 0.8),
                ChargeCurvePoint(60, 0.6),
                ChargeCurvePoint(80, 0.4),
                ChargeCurvePoint(100, 0.15),
            ]
        
        return ChargeCurve(
            vehicle_id="_default",
            is_measured=False,
            points=points
        )
