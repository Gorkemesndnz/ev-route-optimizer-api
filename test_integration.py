#!/usr/bin/env python3
"""
Test route planner integration with realistic CO2 calculation
"""

from app.sustainability_calculator import calculate_co2_savings

def test_integration():
    """Test integration values that would come from route planner"""
    
    print("🧪 Route Planner Entegrasyon Testi")
    print("=" * 50)
    
    # Simüle edilmiş route planner değerleri
    test_cases = [
        {
            "name": "İstanbul-Ankara",
            "distance_km": 445.0,
            "ev_consumption_kwh": 60.0,
            "expected_legacy": 79.15,
            "expected_realistic": 50.65
        },
        {
            "name": "Kısa Şehir İçi",
            "distance_km": 50.0,
            "ev_consumption_kwh": 7.0,
            "expected_legacy": 8.89,
            "expected_realistic": 5.57
        },
        {
            "name": "Uzun Mesafe",
            "distance_km": 1000.0,
            "ev_consumption_kwh": 135.0,
            "expected_legacy": 177.8,
            "expected_realistic": 113.7
        }
    ]
    
    for case in test_cases:
        print(f"\n📍 {case['name']}:")
        print(f"   Mesafe: {case['distance_km']} km")
        print(f"   EV Tüketimi: {case['ev_consumption_kwh']} kWh")
        
        # Legacy (sadece distance)
        legacy_result = calculate_co2_savings(case['distance_km'], 0.0, "TR")
        
        # Realistic (distance + ev_consumption)
        realistic_result = calculate_co2_savings(
            case['distance_km'], 
            case['ev_consumption_kwh'], 
            "TR"
        )
        
        print(f"   📊 ESKİ: {legacy_result:.2f} kg CO2")
        print(f"   📊 YENİ: {realistic_result:.2f} kg CO2")
        print(f"   📈 Net Tasarruf: {(realistic_result/legacy_result)*100:.1f}%")
        
        # Validation
        if abs(realistic_result - case['expected_realistic']) < 1.0:
            print(f"   ✅ Beklenen değerle uyumlu")
        else:
            print(f"   ❌ Beklenen: {case['expected_realistic']:.2f}")

if __name__ == "__main__":
    test_integration()
