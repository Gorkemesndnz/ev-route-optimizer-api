"""
Vehicle Catalog Data Models
============================

Dataclass definitions for vehicle specifications and charging curves.
These models are used by IVehicleCatalog implementations.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class ConnectorType(str, Enum):
    """Supported EV connector types"""
    CCS = "CCS"
    CHADEMO = "CHAdeMO"
    TYPE2 = "Type2"
    TESLA_SUC = "Tesla_SUC"
    
    @classmethod
    def from_string(cls, value: str) -> "ConnectorType":
        """Parse connector type from string (case-insensitive)"""
        value_upper = value.upper().replace(" ", "_").replace("-", "_")
        for member in cls:
            if member.value.upper() == value_upper or member.name == value_upper:
                return member
        return cls.CCS  # Default fallback


class VehicleType(str, Enum):
    """Vehicle type classification"""
    CAR = "car"
    SUV = "suv"
    COMPACT = "compact"
    MOTORBIKE = "motorbike"
    MICROCAR = "microcar"


@dataclass
class ChargeCurvePoint:
    """
    Single point on a charging curve.
    
    Attributes:
        soc_percent: State of charge (0-100)
        power_kw: Charging power at this SOC (kW)
    """
    soc_percent: float
    power_kw: float
    
    def __post_init__(self):
        if not 0 <= self.soc_percent <= 100:
            raise ValueError(f"soc_percent must be 0-100, got {self.soc_percent}")
        if self.power_kw < 0:
            raise ValueError(f"power_kw must be non-negative, got {self.power_kw}")


@dataclass
class ChargeCurve:
    """
    Complete charging curve for a vehicle.
    
    Attributes:
        vehicle_id: Reference to the vehicle
        is_measured: True if from real measurements, False if estimated
        points: List of SOC->Power points (sorted by SOC)
    """
    vehicle_id: str
    is_measured: bool = False
    points: List[ChargeCurvePoint] = field(default_factory=list)
    
    def __post_init__(self):
        # Sort points by SOC
        self.points = sorted(self.points, key=lambda p: p.soc_percent)
    
    def get_power_at_soc(self, soc: float) -> float:
        """
        Get charging power at a given SOC using linear interpolation.
        
        Args:
            soc: State of charge (0-100)
            
        Returns:
            Power in kW at the given SOC
        """
        if not self.points:
            return 0.0
        
        if soc <= self.points[0].soc_percent:
            return self.points[0].power_kw
        if soc >= self.points[-1].soc_percent:
            return self.points[-1].power_kw
        
        # Find surrounding points and interpolate
        for i in range(len(self.points) - 1):
            p1, p2 = self.points[i], self.points[i + 1]
            if p1.soc_percent <= soc <= p2.soc_percent:
                ratio = (soc - p1.soc_percent) / (p2.soc_percent - p1.soc_percent)
                return p1.power_kw + ratio * (p2.power_kw - p1.power_kw)
        
        return self.points[-1].power_kw


@dataclass
class VehicleSpec:
    """
    Complete vehicle specification.
    
    This dataclass maps 1:1 with vehicles_master.json entries.
    """
    # Identifiers
    id: str                          # e.g. "tesla_model_3_long_range_2019"
    source_id: str                   # Original UUID from Open-EV-Data
    
    # Vehicle identity
    brand: str
    model: str
    variant: str
    year: int
    display_name: str
    
    # Battery & consumption
    battery_capacity_kwh: float      # Usable battery capacity (kWh)
    base_consumption_wh_km: float    # Base consumption (Wh/km)
    
    # Charging specifications
    connector_type: ConnectorType
    ac_max_kw: float                 # Max AC charging power (kW)
    dc_max_kw: float                 # Max DC charging power (kW)
    charging_voltage: int            # 400 or 800 (V)
    
    # Physical properties (with defaults for missing data)
    curb_weight_kg: int = 1700
    auxiliary_power_kw: float = 1.2
    
    # 🔧 V2.9: Rejeneratif frenleme özellikleri
    regen_efficiency: float = 0.65  # Nominal regen verimliliği (0-1)
    regen_max_power_kw: float = 70.0  # Maksimum regen gücü (kW)
    battery_chemistry: str = "NMC"  # NMC, LFP, NCA - soğuk hava davranışı için
    
    # Metadata
    has_real_curve: bool = False
    vehicle_type: VehicleType = VehicleType.CAR
    
    def get_consumption_kwh_100km(self) -> float:
        """Get consumption in kWh/100km (common unit)"""
        return self.base_consumption_wh_km / 10.0
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "brand": self.brand,
            "model": self.model,
            "variant": self.variant,
            "year": self.year,
            "display_name": self.display_name,
            "battery_capacity_kwh": self.battery_capacity_kwh,
            "base_consumption_wh_km": self.base_consumption_wh_km,
            "connector_type": self.connector_type.value,
            "ac_max_kw": self.ac_max_kw,
            "dc_max_kw": self.dc_max_kw,
            "charging_voltage": self.charging_voltage,
            "curb_weight_kg": self.curb_weight_kg,
            "auxiliary_power_kw": self.auxiliary_power_kw,
            "regen_efficiency": self.regen_efficiency,
            "regen_max_power_kw": self.regen_max_power_kw,
            "battery_chemistry": self.battery_chemistry,
            "has_real_curve": self.has_real_curve,
            "vehicle_type": self.vehicle_type.value,
        }


# Default weights by vehicle type (used when curb_weight is missing)
DEFAULT_WEIGHTS_BY_TYPE = {
    VehicleType.CAR: 1700,
    VehicleType.SUV: 2100,
    VehicleType.COMPACT: 1500,
    VehicleType.MOTORBIKE: 250,
    VehicleType.MICROCAR: 600,
}
