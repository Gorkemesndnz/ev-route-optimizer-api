#!/usr/bin/env python3
"""Quick import test for vehicle_catalog module"""

import sys
sys.path.insert(0, str(__file__).replace("scripts\\test_imports.py", ""))

try:
    from app.infrastructure.vehicle_catalog import (
        VehicleSpec,
        ChargeCurve,
        ChargeCurvePoint,
        ConnectorType,
        VehicleType,
        IVehicleCatalog,
        FileVehicleCatalog,
    )
    print("IMPORT_OK: All vehicle_catalog imports successful")
    
    # Quick sanity check
    catalog = FileVehicleCatalog()
    print(f"  - FileVehicleCatalog created, {catalog.get_vehicle_count()} vehicles loaded")
    
    # Test default curve
    default_curve = catalog.get_default_curve(400)
    print(f"  - Default 400V curve has {len(default_curve.points)} points")
    
    # Test ChargeCurve interpolation
    power_at_50 = default_curve.get_power_at_soc(50)
    print(f"  - Power at 50% SOC: {power_at_50}")
    
    print("ALL_TESTS_PASSED")
    
except Exception as e:
    print(f"IMPORT_ERROR: {e}")
    import traceback
    traceback.print_exc()
