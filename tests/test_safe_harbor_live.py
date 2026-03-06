"""
Safe Harbor Test Script — Canlı API ile 5 Rota Testi
=====================================================

5 farklı rota senaryosu ile Safe Harbor algoritmasını test eder.
Canlı Google API kullanır (mock yok).

Kullanım:
    python tests/test_safe_harbor_live.py
"""

import asyncio
import json
import sys
import os

# Proje kökünü path'e ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import RouteRequest, GeoPoint, RoutePreferences


# =============================================================================
# 5 TEST ROTASI
# =============================================================================

TEST_ROUTES = [
    {
        "name": "1. İstanbul → Ankara (Şehirler arası — Normal)",
        "description": "Her iki şehirde de bol şarj istasyonu var. Safe Harbor PASSIVE olmalı.",
        "request": RouteRequest(
            start_location=GeoPoint(lat=41.0082, lon=28.9784),     # İstanbul
            end_location=GeoPoint(lat=39.9334, lon=32.8597),       # Ankara
            vehicle_model_id="mg_electric_51kwh_2022",
            current_soc_percent=90.0,
        ),
        "expect_safe_harbor": False,
    },
    {
        "name": "2. Ankara → Çankırı Ilgaz Köyü (Kırsal — Safe Harbor Bekleniyor)",
        "description": "Ilgaz Dağı civarı kırsal alan. 10km içinde istasyon olmayabilir.",
        "request": RouteRequest(
            start_location=GeoPoint(lat=39.9334, lon=32.8597),     # Ankara
            end_location=GeoPoint(lat=40.9167, lon=33.7333),       # Ilgaz civarı köy
            vehicle_model_id="mg_electric_51kwh_2022",
            current_soc_percent=85.0,
        ),
        "expect_safe_harbor": True,
    },
    {
        "name": "3. İstanbul → Bolu Göynük Köyü (Dağlık Kırsal — Safe Harbor Bekleniyor)",
        "description": "Bolu dağlarında kırsal alan. Elevation yüksek, istasyon seyrek.",
        "request": RouteRequest(
            start_location=GeoPoint(lat=41.0082, lon=28.9784),     # İstanbul
            end_location=GeoPoint(lat=40.3892, lon=30.7931),       # Göynük civarı
            vehicle_model_id="mg_electric_51kwh_2022",
            current_soc_percent=95.0,
        ),
        "expect_safe_harbor": True,
    },
    {
        "name": "4. İstanbul → Antalya (Uzun Şehirler Arası — Normal)",
        "description": "Antalya'da bol istasyon. Uzun rota ama Safe Harbor PASSIVE olmalı.",
        "request": RouteRequest(
            start_location=GeoPoint(lat=41.0082, lon=28.9784),     # İstanbul
            end_location=GeoPoint(lat=36.8969, lon=30.7133),       # Antalya
            vehicle_model_id="mg_electric_51kwh_2022",
            current_soc_percent=90.0,
        ),
        "expect_safe_harbor": False,
    },
    {
        "name": "5. Ankara → Kastamonu Küre Dağları (Uzak Kırsal — Safe Harbor Bekleniyor)",
        "description": "Küre Dağları Milli Parkı civarı. Çok uzak, istasyon yok.",
        "request": RouteRequest(
            start_location=GeoPoint(lat=39.9334, lon=32.8597),     # Ankara
            end_location=GeoPoint(lat=41.7833, lon=33.4333),       # Küre Dağları civarı
            vehicle_model_id="mg_electric_51kwh_2022",
            current_soc_percent=90.0,
        ),
        "expect_safe_harbor": True,
    },
]


async def run_test(test_case: dict, index: int):
    """Tek bir test rotasını çalıştır."""
    from app.route_planning.orchestrator import plan_route
    
    print(f"\n{'='*80}")
    print(f"📍 {test_case['name']}")
    print(f"   {test_case['description']}")
    print(f"{'='*80}")
    
    request = test_case["request"]
    print(f"   Start: ({request.start_location.lat}, {request.start_location.lon})")
    print(f"   End:   ({request.end_location.lat}, {request.end_location.lon})")
    print(f"   SOC:   {request.current_soc_percent}%")
    print()
    
    try:
        response = await plan_route(request)
        
        print(f"   ✅ Status: {response.status}")
        print(f"   📏 Distance: {response.total_distance_km} km")
        print(f"   ⏱ Duration: {response.total_duration_minutes:.0f} min")
        print(f"   🔋 Consumption: {response.consumption_kwh:.1f} kWh")
        print(f"   ⚡ Charge stops: {response.charge_stops}")
        print(f"   📝 Message: {response.message}")
        
        # Safe Harbor sonucu kontrol et
        if response.safe_harbor_info:
            sh = response.safe_harbor_info
            print(f"\n   🏠 SAFE HARBOR AKTİF:")
            print(f"      Dinamik Varış SOC: %{sh['dynamic_min_arrival_soc_percent']}")
            print(f"      Seçili İstasyon Index: {sh['selected_station_index']}")
            print(f"      Arama Yarıçapı: {sh['search_radius_used_km']} km")
            print(f"\n      📋 KURTARICI İSTASYON SEÇENEKLERİ:")
            print(f"      {'─'*60}")
            for i, rs in enumerate(sh["rescue_stations"]):
                marker = "→ " if rs.get("is_selected") else "  "
                print(f"      {marker}[{i+1}] {rs['name']}")
                print(f"           Mesafe: {rs['route_distance_km']}km (haversine: {rs['distance_km']}km)")
                print(f"           Güç: {rs['max_power_kw']}kW | Puan: ★{rs['rating']}")
                print(f"           Dönüş Tüketimi: {rs['return_consumption_kwh']:.2f} kWh ({rs['return_soc_needed_percent']:.1f}%)")
                print(f"           Gereken Varış SOC: %{rs['required_arrival_soc_percent']}")
                elev = rs.get("elevation", {})
                print(f"           Elevation: +{elev.get('gain_m', 0):.0f}m / -{elev.get('loss_m', 0):.0f}m")
                if i < len(sh["rescue_stations"]) - 1:
                    print(f"      {'─'*60}")
            
            if not test_case["expect_safe_harbor"]:
                print(f"\n   ⚠️ BEKLENMEYEN: Safe Harbor beklenMİYORdu ama AKTİF!")
        else:
            print(f"\n   ✅ Safe Harbor: PASSIVE (varışta istasyon mevcut)")
            if test_case["expect_safe_harbor"]:
                print(f"\n   ⚠️ BEKLENMEYEN: Safe Harbor bekleniyordu ama PASSIVE!")
        
        # Uyarılar
        if response.warning_messages:
            print(f"\n   ⚠️ Uyarılar:")
            for w in response.warning_messages:
                print(f"      {w}")
        
        return response
                
    except Exception as e:
        print(f"   ❌ HATA: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    print("🔋 Safe Harbor Test Suite — Canlı API")
    print("=" * 80)
    print(f"📊 {len(TEST_ROUTES)} rota test edilecek")
    print()
    
    results = []
    for i, test in enumerate(TEST_ROUTES):
        result = await run_test(test, i)
        results.append((test, result))
    
    # Özet
    print(f"\n\n{'='*80}")
    print("📊 TEST ÖZETİ")
    print("=" * 80)
    for test, result in results:
        if result is None:
            status = "❌ HATA"
        elif result.safe_harbor_info:
            if test["expect_safe_harbor"]:
                status = "✅ DOĞRU (Safe Harbor AKTİF)"
            else:
                status = "⚠️ BEKLENMEYEN (Safe Harbor AKTİF)"
        else:
            if not test["expect_safe_harbor"]:
                status = "✅ DOĞRU (Safe Harbor PASSIVE)"
            else:
                status = "⚠️ BEKLENMEYEN (Safe Harbor PASSIVE)"
        print(f"  {test['name'].split('(')[0].strip()}: {status}")
    
    print(f"\n{'='*80}")
    print("✅ Test sırası tamamlandı")


if __name__ == "__main__":
    asyncio.run(main())
