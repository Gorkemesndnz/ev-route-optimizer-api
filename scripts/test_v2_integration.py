#!/usr/bin/env python3
"""
V2 Integration Test
====================

Full integration test for the new vehicle catalog and charging time calculator.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_vehicle_catalog():
    """Test FileVehicleCatalog functionality"""
    print("\n" + "=" * 60)
    print("TEST 1: FileVehicleCatalog")
    print("=" * 60)
    
    from app.infrastructure.vehicle_catalog import FileVehicleCatalog
    
    catalog = FileVehicleCatalog()
    
    # Basic stats
    vehicle_count = catalog.get_vehicle_count()
    brands = catalog.get_all_brands()
    
    print(f"  Vehicles loaded: {vehicle_count}")
    print(f"  Brands: {len(brands)}")
    print(f"  Sample brands: {brands[:5]}...")
    
    assert vehicle_count > 1000, f"Expected 1000+ vehicles, got {vehicle_count}"
    assert len(brands) > 50, f"Expected 50+ brands, got {len(brands)}"
    
    # Search test
    tesla_results = catalog.search(query="tesla model 3", limit=10)
    print(f"\n  Search 'tesla model 3': {len(tesla_results)} results")
    for v in tesla_results[:3]:
        print(f"    - {v.display_name}")
    
    assert len(tesla_results) > 0, "Expected Tesla Model 3 results"
    
    # Get by ID test (search first to get a valid ID)
    if tesla_results:
        test_id = tesla_results[0].id
        spec = catalog.get_by_id(test_id)
        print(f"\n  Get by ID '{test_id}':")
        print(f"    - Brand: {spec.brand}")
        print(f"    - Battery: {spec.battery_capacity_kwh} kWh")
        print(f"    - DC Max: {spec.dc_max_kw} kW")
        print(f"    - Consumption: {spec.base_consumption_wh_km} Wh/km")
    
    print("\n  TEST 1 PASSED")
    return True


def test_charging_curves():
    """Test charging curve loading and interpolation"""
    print("\n" + "=" * 60)
    print("TEST 2: Charging Curves")
    print("=" * 60)
    
    from app.infrastructure.vehicle_catalog import FileVehicleCatalog
    
    catalog = FileVehicleCatalog()
    
    # Find a vehicle with a real curve
    vehicles_with_curves = 0
    test_vehicle = None
    
    for v in catalog.search(limit=100):
        curve = catalog.get_charge_curve(v.id)
        if curve and curve.is_measured:
            vehicles_with_curves += 1
            if test_vehicle is None:
                test_vehicle = v
    
    print(f"  Vehicles with real curves (sample): {vehicles_with_curves}/100")
    
    if test_vehicle:
        curve = catalog.get_charge_curve(test_vehicle.id)
        print(f"\n  Test vehicle: {test_vehicle.display_name}")
        print(f"  Curve points: {len(curve.points)}")
        
        # Test interpolation
        for soc in [0, 25, 50, 75, 100]:
            power = curve.get_power_at_soc(soc)
            print(f"    SOC {soc}%: {power:.1f} kW")
    
    # Test default curve
    default_400v = catalog.get_default_curve(400)
    default_800v = catalog.get_default_curve(800)
    
    print(f"\n  Default 400V curve: {len(default_400v.points)} points")
    print(f"  Default 800V curve: {len(default_800v.points)} points")
    
    print("\n  TEST 2 PASSED")
    return True


def test_charging_time_calculator():
    """Test ChargingTimeCalculator accuracy"""
    print("\n" + "=" * 60)
    print("TEST 3: ChargingTimeCalculator")
    print("=" * 60)
    
    from app.infrastructure.vehicle_catalog import FileVehicleCatalog
    from app.consumption_engine.v2_ml_model import ChargingTimeCalculator, calculate_naive_charge_time
    
    catalog = FileVehicleCatalog()
    
    # Find Tesla Model 3 or similar
    vehicles = catalog.search(query="tesla model 3 long range", limit=5)
    if not vehicles:
        vehicles = catalog.search(query="tesla", limit=5)
    
    if not vehicles:
        print("  No Tesla found, using first available vehicle")
        vehicles = catalog.search(limit=1)
    
    spec = vehicles[0]
    curve = catalog.get_charge_curve(spec.id)
    
    if curve is None:
        curve = catalog.get_default_curve(spec.charging_voltage)
        print(f"  Using default curve for {spec.display_name}")
    else:
        print(f"  Using real curve for {spec.display_name}")
    
    # Create calculator
    calculator = ChargingTimeCalculator(curve, spec.dc_max_kw)
    
    # Test scenario: 15% -> 80% at 150kW station
    battery_kwh = spec.battery_capacity_kwh
    soc_start = 15.0
    soc_target = 80.0
    station_kw = 150.0
    
    time_curve, steps = calculator.calculate_charge_time(
        battery_kwh=battery_kwh,
        soc_start=soc_start,
        soc_target=soc_target,
        station_max_kw=station_kw,
        return_steps=True
    )
    
    # Compare with naive calculation
    time_naive = calculate_naive_charge_time(
        battery_kwh=battery_kwh,
        soc_start=soc_start,
        soc_target=soc_target,
        avg_power_kw=spec.dc_max_kw * 0.7  # Assume 70% avg
    )
    
    print(f"\n  Vehicle: {spec.display_name}")
    print(f"  Battery: {battery_kwh} kWh")
    print(f"  Scenario: {soc_start}% -> {soc_target}% at {station_kw}kW station")
    print(f"\n  Curve-based time: {time_curve} minutes")
    print(f"  Naive time (70% avg): {time_naive} minutes")
    print(f"  Difference: {time_curve - time_naive:+.1f} minutes")
    
    # Show some steps
    if steps:
        print(f"\n  Sample charging steps:")
        for step in steps[::10]:  # Every 10th step
            print(f"    {step.soc_from}% -> {step.soc_to}%: {step.power_kw}kW, {step.time_minutes:.1f}min")
    
    assert time_curve > 0, "Charging time should be positive"
    assert time_curve < 180, "Charging time should be reasonable (<3 hours)"
    
    print("\n  TEST 3 PASSED")
    return True


def test_vehicle_models_integration():
    """Test vehicle_models.py integration with new catalog"""
    print("\n" + "=" * 60)
    print("TEST 4: vehicle_models.py Integration")
    print("=" * 60)
    
    from app.consumption_engine.vehicle_models import (
        get_vehicle_model,
        get_available_vehicle_ids,
        VEHICLE_DB,
        LEGACY_VEHICLE_DB
    )
    
    # Test legacy vehicles still work
    print("\n  Testing legacy vehicles:")
    for legacy_id in ["mg4_51kwh", "tesla_model_3_long_range", "opel_frontera_44"]:
        try:
            model = get_vehicle_model(legacy_id)
            print(f"    {legacy_id}: {model.model_name} ({model.battery_capacity_kwh} kWh)")
        except ValueError as e:
            print(f"    {legacy_id}: FAILED - {e}")
    
    # Test catalog vehicles
    print("\n  Testing catalog lookup:")
    available_ids = get_available_vehicle_ids()
    print(f"    Total available IDs: {len(available_ids)}")
    
    # Try to get a vehicle from catalog (not in legacy)
    catalog_only_ids = [id for id in available_ids if id not in LEGACY_VEHICLE_DB]
    if catalog_only_ids:
        test_id = catalog_only_ids[0]
        try:
            model = get_vehicle_model(test_id)
            print(f"    Catalog vehicle '{test_id}': {model.model_name}")
        except ValueError as e:
            print(f"    Catalog lookup FAILED: {e}")
    
    assert len(available_ids) > 1000, f"Expected 1000+ vehicles, got {len(available_ids)}"
    
    print("\n  TEST 4 PASSED")
    return True


def main():
    """Run all tests"""
    print("=" * 60)
    print("V2 ML INTEGRATION TEST SUITE")
    print("=" * 60)
    
    tests = [
        ("FileVehicleCatalog", test_vehicle_catalog),
        ("Charging Curves", test_charging_curves),
        ("ChargingTimeCalculator", test_charging_time_calculator),
        ("vehicle_models.py Integration", test_vehicle_models_integration),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result, None))
        except Exception as e:
            import traceback
            results.append((name, False, str(e)))
            traceback.print_exc()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = 0
    for name, result, error in results:
        status = "PASS" if result else "FAIL"
        print(f"  {name}: {status}")
        if error:
            print(f"    Error: {error}")
        if result:
            passed += 1
    
    print(f"\n  Total: {passed}/{len(tests)} tests passed")
    
    if passed == len(tests):
        print("\n  ALL TESTS PASSED!")
        return 0
    else:
        print("\n  SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    exit(main())
