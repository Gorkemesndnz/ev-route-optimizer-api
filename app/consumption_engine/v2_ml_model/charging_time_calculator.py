"""
Charging Time Calculator
=========================

Calculates accurate charging times using SOC-based charging curves.

This replaces the naive "energy / avg_power" calculation with a more
accurate step-by-step integration that accounts for power tapering
at higher SOC levels.

Usage:
    from app.consumption_engine.v2_ml_model.charging_time_calculator import ChargingTimeCalculator
    from app.infrastructure.vehicle_catalog import FileVehicleCatalog
    
    catalog = FileVehicleCatalog()
    spec = catalog.get_by_id("tesla_model_3_long_range_2019")
    curve = catalog.get_charge_curve(spec.id) or catalog.get_default_curve(spec.charging_voltage)
    
    calculator = ChargingTimeCalculator(curve, spec.dc_max_kw)
    time_minutes = calculator.calculate_charge_time(
        battery_kwh=spec.battery_capacity_kwh,
        soc_start=15.0,
        soc_target=80.0,
        station_max_kw=150.0
    )
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
from app.infrastructure.vehicle_catalog.models import ChargeCurve, ChargeCurvePoint


@dataclass
class ChargingStep:
    """Single step in charging process"""
    soc_from: float
    soc_to: float
    power_kw: float
    energy_kwh: float
    time_minutes: float


class ChargingTimeCalculator:
    """
    Calculates charging time using SOC-based power curves.
    
    The calculation works by:
    1. Dividing the SOC range into small steps (default 1%)
    2. For each step, determining the effective power as min(curve_power, station_power)
    3. Calculating time = energy / power for each step
    4. Summing all step times
    
    This accounts for:
    - Power tapering at higher SOC (the charging curve)
    - Station power limits
    - Battery capacity
    """
    
    def __init__(
        self,
        curve: ChargeCurve,
        vehicle_dc_max_kw: float,
        is_multiplier_curve: bool = False
    ):
        """
        Initialize calculator with a charging curve.
        
        Args:
            curve: The charging curve (SOC -> power)
            vehicle_dc_max_kw: Vehicle's maximum DC charging power
            is_multiplier_curve: If True, curve values are 0-1 multipliers
                                 that should be scaled by vehicle_dc_max_kw
        """
        self.curve = curve
        self.vehicle_dc_max_kw = vehicle_dc_max_kw
        self.is_multiplier_curve = is_multiplier_curve
        
        # Detect if this is a multiplier curve (all values <= 1)
        if curve.points:
            max_power = max(p.power_kw for p in curve.points)
            if max_power <= 1.0:
                self.is_multiplier_curve = True
    
    def get_power_at_soc(self, soc: float) -> float:
        """
        Get charging power at a given SOC.
        
        Args:
            soc: State of charge (0-100)
            
        Returns:
            Power in kW at the given SOC
        """
        raw_power = self.curve.get_power_at_soc(soc)
        
        if self.is_multiplier_curve:
            return raw_power * self.vehicle_dc_max_kw
        
        return min(raw_power, self.vehicle_dc_max_kw)
    
    def calculate_charge_time(
        self,
        battery_kwh: float,
        soc_start: float,
        soc_target: float,
        station_max_kw: float,
        step_percent: float = 1.0,
        return_steps: bool = False
    ) -> Tuple[float, Optional[List[ChargingStep]]]:
        """
        Calculate total charging time.
        
        Args:
            battery_kwh: Battery capacity (kWh)
            soc_start: Starting SOC (0-100)
            soc_target: Target SOC (0-100)
            station_max_kw: Station's maximum power (kW)
            step_percent: SOC step size for integration (default 1%)
            return_steps: If True, also return detailed step breakdown
            
        Returns:
            Tuple of (total_time_minutes, optional_steps_list)
        """
        # Validate inputs
        if soc_start >= soc_target:
            return (0.0, []) if return_steps else (0.0, None)
        
        if battery_kwh <= 0 or station_max_kw <= 0:
            return (0.0, []) if return_steps else (0.0, None)
        
        soc_start = max(0.0, min(100.0, soc_start))
        soc_target = max(0.0, min(100.0, soc_target))
        
        total_minutes = 0.0
        steps = [] if return_steps else None
        current_soc = soc_start
        
        while current_soc < soc_target:
            # Calculate next SOC (don't overshoot target)
            next_soc = min(current_soc + step_percent, soc_target)
            delta_soc = next_soc - current_soc
            
            # Energy for this step
            delta_kwh = (delta_soc / 100.0) * battery_kwh
            
            # Power at current SOC (limited by curve, vehicle max, and station)
            curve_power = self.get_power_at_soc(current_soc)
            effective_power = min(curve_power, station_max_kw, self.vehicle_dc_max_kw)
            
            # Time calculation (prevent division by zero)
            if effective_power > 0:
                time_hours = delta_kwh / effective_power
                time_minutes = time_hours * 60
            else:
                time_minutes = 0.0
            
            total_minutes += time_minutes
            
            if steps is not None:
                steps.append(ChargingStep(
                    soc_from=round(current_soc, 1),
                    soc_to=round(next_soc, 1),
                    power_kw=round(effective_power, 1),
                    energy_kwh=round(delta_kwh, 3),
                    time_minutes=round(time_minutes, 2)
                ))
            
            current_soc = next_soc
        
        return (round(total_minutes, 1), steps)
    
    def estimate_charge_time_simple(
        self,
        battery_kwh: float,
        soc_start: float,
        soc_target: float,
        station_max_kw: float
    ) -> float:
        """
        Simple wrapper that just returns total time in minutes.
        
        Args:
            battery_kwh: Battery capacity (kWh)
            soc_start: Starting SOC (0-100)
            soc_target: Target SOC (0-100)
            station_max_kw: Station's maximum power (kW)
            
        Returns:
            Total charging time in minutes
        """
        time_minutes, _ = self.calculate_charge_time(
            battery_kwh=battery_kwh,
            soc_start=soc_start,
            soc_target=soc_target,
            station_max_kw=station_max_kw,
            return_steps=False
        )
        return time_minutes


def calculate_naive_charge_time(
    battery_kwh: float,
    soc_start: float,
    soc_target: float,
    avg_power_kw: float
) -> float:
    """
    Naive charging time calculation (for comparison).
    
    This is the old method that doesn't account for power tapering.
    
    Args:
        battery_kwh: Battery capacity (kWh)
        soc_start: Starting SOC (0-100)
        soc_target: Target SOC (0-100)
        avg_power_kw: Average charging power (kW)
        
    Returns:
        Charging time in minutes
    """
    if soc_start >= soc_target or avg_power_kw <= 0:
        return 0.0
    
    energy_kwh = (soc_target - soc_start) / 100.0 * battery_kwh
    time_hours = energy_kwh / avg_power_kw
    return round(time_hours * 60, 1)
