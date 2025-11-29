#!/usr/bin/env python3
"""
Test script for new realistic CO2 savings calculation
"""

from app.sustainability_calculator import (
    calculate_co2_savings, 
    calculate_sustainability_metrics,
    calculate_co2_savings_legacy
)

def test_co2_calculation():
    """Test realistic CO2 savings calculation"""
    
    print("🧪 CO2 Tasarrufu Testleri")
    print("=" * 50)
    
    # Test Case 1: Istanbul-Ankara (445 km, 60 kWh)
    distance_km = 445.0
    ev_consumption_kwh = 60.0
    region_code = "TR"
    
    print(f"\n📍 Test Case 1: İstanbul-Ankara")
    print(f"   Mesafe: {distance_km} km")
    print(f"   EV Tüketimi: {ev_consumption_kwh} kWh")
    print(f"   Bölge: {region_code}")
    
    # Legacy calculation (EV consumption ignored)
    legacy_co2 = calculate_co2_savings_legacy(distance_km)
    print(f"\n📊 ESKİ Hesaplama (EV tüketimi yok):")
    print(f"   CO2 Tasarrufu: {legacy_co2:.2f} kg")
    
    # New realistic calculation
    realistic_co2 = calculate_co2_savings(distance_km, ev_consumption_kwh, region_code)
    print(f"\n📊 YENİ Gerçekçi Hesaplama:")
    print(f"   CO2 Tasarrufu: {realistic_co2:.2f} kg")
    
    # Difference
    difference = legacy_co2 - realistic_co2
    print(f"\n📈 Fark:")
    print(f"   EV CO2 maliyeti: {difference:.2f} kg")
    print(f"   Net tasarruf oranı: {(realistic_co2/legacy_co2)*100:.1f}%")
    
    # Test Case 2: Short trip (50 km, 7 kWh)
    print(f"\n📍 Test Case 2: Kısa Mesafe")
    short_distance = 50.0
    short_ev_consumption = 7.0
    
    short_legacy = calculate_co2_savings_legacy(short_distance)
    short_realistic = calculate_co2_savings(short_distance, short_ev_consumption, region_code)
    
    print(f"   {short_distance} km, {short_ev_consumption} kWh")
    print(f"   ESKİ: {short_legacy:.2f} kg")
    print(f"   YENİ: {short_realistic:.2f} kg")
    print(f"   Net tasarruf oranı: {(short_realistic/short_legacy)*100:.1f}%")
    
    # Test comprehensive metrics
    print(f"\n📊 Comprehensive Metrics Test:")
    metrics = calculate_sustainability_metrics(distance_km, ev_consumption_kwh, region_code)
    
    for key, value in metrics.items():
        print(f"   {key}: {value}")

if __name__ == "__main__":
    test_co2_calculation()
