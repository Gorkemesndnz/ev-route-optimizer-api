"""
Smart Charging Power Estimator
==============================

Provides realistic DC charging power estimates based on battery capacity
when actual data is missing or incorrect.

This module addresses the common data quality issue where dc_max_kw
incorrectly equals battery_capacity_kwh in vehicle databases.
"""

from typing import Tuple


def estimate_dc_charging_power(battery_capacity_kwh: float) -> float:
    """
    Estimate realistic DC charging power based on battery capacity.
    
    Uses industry-standard C-rate ranges:
    - Small batteries (<40 kWh): ~1.5C rate (e.g., 40 kWh → 60 kW)
    - Medium batteries (40-70 kWh): ~1.8C rate (e.g., 60 kWh → 108 kW)
    - Large batteries (70-100 kWh): ~2.0C rate (e.g., 80 kWh → 160 kW)
    - Very large batteries (>100 kWh): ~2.2C rate (e.g., 120 kWh → 264 kW)
    
    Args:
        battery_capacity_kwh: Battery capacity in kWh
        
    Returns:
        Estimated DC charging power in kW
    """
    if battery_capacity_kwh <= 0:
        return 50.0  # Minimum fallback
    
    if battery_capacity_kwh < 40:
        # Small batteries: ~1.5C rate
        c_rate = 1.5
    elif battery_capacity_kwh < 70:
        # Medium batteries: ~1.8C rate
        c_rate = 1.8
    elif battery_capacity_kwh < 100:
        # Large batteries: ~2.0C rate
        c_rate = 2.0
    else:
        # Very large batteries: ~2.2C rate (Tesla, Lucid, etc.)
        c_rate = 2.2
    
    estimated_power = battery_capacity_kwh * c_rate
    
    # Cap at realistic maximum (350 kW for current infrastructure)
    return min(estimated_power, 350.0)


def is_dc_max_suspicious(dc_max_kw: float, battery_capacity_kwh: float) -> bool:
    """
    Check if dc_max_kw value is suspicious (likely wrong data).
    
    Common data errors:
    1. dc_max_kw equals battery_capacity_kwh (copy-paste error)
    2. dc_max_kw is unrealistically low for battery size
    
    Args:
        dc_max_kw: Reported DC max charging power
        battery_capacity_kwh: Battery capacity
        
    Returns:
        True if the value is suspicious and should be replaced
    """
    if battery_capacity_kwh <= 0 or dc_max_kw <= 0:
        return True
    
    # Check if dc_max equals battery capacity (common data error)
    if abs(dc_max_kw - battery_capacity_kwh) < 1.0:
        return True
    
    # Check if dc_max is unrealistically low (< 0.8C rate for >50 kWh battery)
    if battery_capacity_kwh > 50 and dc_max_kw < battery_capacity_kwh * 0.8:
        return True
    
    return False


def is_curve_suspicious(curve_peak_kw: float, battery_capacity_kwh: float) -> bool:
    """
    Check if charging curve peak is suspicious (likely wrong data).
    
    Args:
        curve_peak_kw: Peak power in the charging curve
        battery_capacity_kwh: Battery capacity
        
    Returns:
        True if the curve peak is suspicious
    """
    if battery_capacity_kwh <= 0 or curve_peak_kw <= 0:
        return True
    
    # Check if curve peak equals battery capacity (common data error)
    if abs(curve_peak_kw - battery_capacity_kwh) < 1.0:
        return True
    
    return False


def get_smart_dc_max(
    spec_dc_max_kw: float,
    battery_capacity_kwh: float,
    curve_peak_kw: float = 0.0,
    station_power_kw: float = 0.0
) -> Tuple[float, str]:
    """
    Get smart DC max value, using estimation if data is suspicious.
    
    Priority:
    1. If spec_dc_max is valid (not suspicious) → use it
    2. If curve_peak is valid (not suspicious) → use it
    3. Otherwise → estimate from battery capacity
    
    Args:
        spec_dc_max_kw: DC max from vehicle spec
        battery_capacity_kwh: Battery capacity
        curve_peak_kw: Peak power from charging curve (if available)
        station_power_kw: Station power (for upper bound)
        
    Returns:
        Tuple of (dc_max_kw, source) where source is 'spec', 'curve', or 'estimated'
    """
    # Check if spec value is valid
    if spec_dc_max_kw > 0 and not is_dc_max_suspicious(spec_dc_max_kw, battery_capacity_kwh):
        return (spec_dc_max_kw, 'spec')
    
    # Check if curve peak is valid
    if curve_peak_kw > 1.0 and not is_curve_suspicious(curve_peak_kw, battery_capacity_kwh):
        return (curve_peak_kw, 'curve')
    
    # Estimate from battery capacity
    estimated = estimate_dc_charging_power(battery_capacity_kwh)
    
    # If station power is known, use it as upper bound
    if station_power_kw > 0:
        estimated = min(estimated, station_power_kw)
    
    return (estimated, 'estimated')


def generate_fallback_curve_points(battery_capacity_kwh: float, dc_max_kw: float = 0.0) -> list:
    """
    Generate realistic charging curve points when no curve data is available.
    
    Based on typical Li-ion charging behavior:
    - 0-30% SOC: Peak power (constant current phase)
    - 30-60% SOC: Slight taper (~95% of peak)
    - 60-80% SOC: Moderate taper (~70% of peak)
    - 80-100% SOC: Heavy taper (~30% of peak)
    
    Args:
        battery_capacity_kwh: Battery capacity
        dc_max_kw: Known DC max (if 0, will be estimated)
        
    Returns:
        List of {soc, power_kw} points
    """
    if dc_max_kw <= 0:
        dc_max_kw = estimate_dc_charging_power(battery_capacity_kwh)
    
    return [
        {"soc": 0, "power_kw": round(dc_max_kw, 1)},
        {"soc": 30, "power_kw": round(dc_max_kw * 0.95, 1)},
        {"soc": 60, "power_kw": round(dc_max_kw * 0.70, 1)},
        {"soc": 80, "power_kw": round(dc_max_kw * 0.40, 1)},
        {"soc": 100, "power_kw": round(dc_max_kw * 0.15, 1)}
    ]
