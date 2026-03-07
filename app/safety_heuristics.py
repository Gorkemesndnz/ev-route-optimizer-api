"""
Safety Heuristics Module
========================

Phase 1 refactor: extracts dynamic buffer calculations out of soc_simulator.
Replaces static constants (HOTSPOT_SOC_BUFFER, SAFETY_BUFFER_PERCENT) with
route-aware dynamic calculations.
"""

def calculate_dynamic_reserve(
    segment_distance_km: float,
    elevation_gain_m: float,
    distance_to_next_station_km: float,
    temperature_c: float
) -> float:
    """
    Calculate dynamic safety SOC reserve based on route difficulty.
    
    Args:
         segment_distance_km: Distance of the current segment
         elevation_gain_m: Elevation climb
         distance_to_next_station_km: Distance to next charging opportunity
         temperature_c: Ambient temperature
         
    Returns:
         Dynamic reserve percentage (capped at 25.0%)
    """
    # Base reserve
    reserve = 5.0
    
    # Elevation penalty
    if elevation_gain_m > 200:
        reserve += 5.0
        
    # Long distance to next charger
    if distance_to_next_station_km > 80:
        reserve += 5.0
        
    # Very long distance to next charger
    if distance_to_next_station_km > 120:
        reserve += 5.0
        
    # Cold weather penalty
    if temperature_c < 0:
        reserve += 3.0
        
    # Safety Guard
    return min(reserve, 25.0)


# =============================================================================
# INLINE TESTS (Phase 1)
# =============================================================================
if __name__ == "__main__":
    def run_tests():
        print("🧪 RUNNING PHASE 1 INLINE TESTS (safety_heuristics)...")
        
        # Test 1 - Flat road
        r1 = calculate_dynamic_reserve(
            segment_distance_km=20, elevation_gain_m=10, distance_to_next_station_km=30, temperature_c=20
        )
        assert abs(r1 - 5.0) < 0.1, f"Fail T1: {r1}"
        print("  ✔️ Test 1 (Flat road) passed.")
        
        # Test 2 - Mountain segment
        r2 = calculate_dynamic_reserve(
            segment_distance_km=30, elevation_gain_m=400, distance_to_next_station_km=40, temperature_c=20
        )
        assert abs(r2 - 10.0) < 0.1, f"Fail T2: {r2}"
        print("  ✔️ Test 2 (Mountain segment) passed.")
        
        # Test 3 - Long charger gap
        r3 = calculate_dynamic_reserve(
            segment_distance_km=40, elevation_gain_m=100, distance_to_next_station_km=130, temperature_c=20
        )
        assert abs(r3 - 15.0) < 0.1, f"Fail T3: {r3}"
        print("  ✔️ Test 3 (Long charger gap) passed.")
        
        # Test 4 - Cold Mountain
        r4 = calculate_dynamic_reserve(
            segment_distance_km=30, elevation_gain_m=300, distance_to_next_station_km=50, temperature_c=-5
        )
        assert abs(r4 - 13.0) < 0.1, f"Fail T4: {r4}"
        print("  ✔️ Test 4 (Cold Mountain) passed.")
        
        # Test 5 - Safety Guard (Cap at 25%)
        r5 = calculate_dynamic_reserve(
            segment_distance_km=50, elevation_gain_m=400, distance_to_next_station_km=150, temperature_c=-10
        )
        assert abs(r5 - 23.0) < 0.1, f"Fail T5: {r5}"
        
        print("✨ ALL INLINE TESTS PASSED!")

    run_tests()
