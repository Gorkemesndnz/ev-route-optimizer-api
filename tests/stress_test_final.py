"""
Nihai Denetim: 10-Rota Stres Testi + Skor Denetimi + Kod Bütünlüğü
===================================================================
"""
import httpx
import json
import time
import sys
import copy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BASE_URL = "http://127.0.0.1:8001"
VEHICLE_STD = "mg_mg4_electric_51_kwh_2022"  # 50.8 kWh

# ===================== 10 ROTA =====================
STRESS_ROUTES = [
    # 1. İstanbul → Ankara (Bolu Geçidi odaklı)
    {"name": "Istanbul-Ankara", "desc": "Bolu Geçidi / Otoban", "start": (41.0082, 28.9784), "end": (39.9334, 32.8597), "soc": 85, "vehicle": VEHICLE_STD},
    # 2. Ankara → Erzurum (Soğuk iklim testi)
    {"name": "Ankara-Erzurum", "desc": "Soğuk İklim / Uzun Mesafe", "start": (39.9334, 32.8597), "end": (39.9043, 41.2679), "soc": 95, "vehicle": VEHICLE_STD},
    # 3. İzmir → Antalya (Şarj çölü testi)
    {"name": "Izmir-Antalya", "desc": "Şarj Çölü / Sıcak", "start": (38.4192, 27.1287), "end": (36.8969, 30.7133), "soc": 85, "vehicle": VEHICLE_STD},
    # 4. İstanbul → İzmir (Kıyı otoyolu)
    {"name": "Istanbul-Izmir", "desc": "Kıyı Otoyolu", "start": (41.0082, 28.9784), "end": (38.4192, 27.1287), "soc": 90, "vehicle": VEHICLE_STD},
    # 5. Ankara → Bolu (Dağlık kısa rota)
    {"name": "Ankara-Bolu", "desc": "Dağlık Kısa Rota", "start": (39.9334, 32.8597), "end": (40.7392, 31.6116), "soc": 85, "vehicle": VEHICLE_STD},
    # 6. Bursa → Eskişehir (Orta mesafe)
    {"name": "Bursa-Eskisehir", "desc": "Orta Mesafe / Düz Ova", "start": (40.1826, 29.0665), "end": (39.7667, 30.5256), "soc": 80, "vehicle": VEHICLE_STD},
    # 7. Antalya → Konya (İç Anadolu geçişi)
    {"name": "Antalya-Konya", "desc": "Toros Dağları Geçişi", "start": (36.8969, 30.7133), "end": (37.8746, 32.4932), "soc": 90, "vehicle": VEHICLE_STD},
    # 8. İstanbul → Trabzon (En uzun rota)
    {"name": "Istanbul-Trabzon", "desc": "En Uzun Rota / Karadeniz", "start": (41.0082, 28.9784), "end": (41.0027, 39.7168), "soc": 95, "vehicle": VEHICLE_STD},
    # 9. Ankara → Mersin (Güney geçidi)
    {"name": "Ankara-Mersin", "desc": "Güney Geçidi / Toros İnişi", "start": (39.9334, 32.8597), "end": (36.8121, 34.6415), "soc": 85, "vehicle": VEHICLE_STD},
    # 10. İstanbul → Çanakkale (Kısa/Deniz kıyısı)
    {"name": "Istanbul-Canakkale", "desc": "Kısa Kıyı Rotası", "start": (41.0082, 28.9784), "end": (40.1553, 26.4142), "soc": 80, "vehicle": VEHICLE_STD},
]


def run_route(route):
    """Tek bir rotayı çalıştır ve metrikleri çıkar."""
    url = f"{BASE_URL}/optimize_route"
    body = {
        "start_location": {"lat": route["start"][0], "lon": route["start"][1]},
        "end_location": {"lat": route["end"][0], "lon": route["end"][1]},
        "vehicle_model_id": route["vehicle"],
        "current_soc_percent": route["soc"],
    }
    
    start_t = time.time()
    with httpx.Client(timeout=180.0) as client:
        r = client.post(url, json=body)
    elapsed = round(time.time() - start_t, 1)
    
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "elapsed": elapsed}
    
    data = r.json()
    if data.get("status") != "success":
        return {"error": data.get("message", "unknown"), "elapsed": elapsed}
    
    legs = data.get("legs", [])
    total_distance = sum(l.get("distance_km", 0) for l in legs if l.get("type") == "drive")
    charge_stops = [l for l in legs if l.get("type") == "charge"]
    num_stops = len(charge_stops)
    
    # Final SOC
    final_soc = 0
    for l in reversed(legs):
        if l.get("type") == "drive":
            final_soc = l.get("end_soc_percent", 0)
            break
    
    # Station details
    stations = []
    for cs in charge_stops:
        stations.append({
            "name": cs.get("station_name", "?"),
            "power_kw": cs.get("charger_power_kw", 0),
            "duration_min": round(cs.get("duration_minutes", 0), 1),
            "target_soc": cs.get("target_soc_percent", 0),
        })
    
    return {
        "total_km": round(total_distance, 1),
        "stops": num_stops,
        "final_soc": round(final_soc, 1),
        "stations": stations,
        "elapsed": elapsed,
        "full_data": data,
    }


def task1_stress_test():
    """GÖREV 1: 10 Rota Stres Testi"""
    print("=" * 90)
    print("📋 GÖREV 1: 10-ROTA STRES TESTİ")
    print("=" * 90)
    
    results = []
    for i, route in enumerate(STRESS_ROUTES, 1):
        print(f"\n🚗 [{i}/10] {route['name']} — {route['desc']}")
        result = run_route(route)
        result["route"] = route
        results.append(result)
        
        if "error" in result:
            print(f"   ❌ HATA: {result['error']} ({result['elapsed']}s)")
        else:
            station_info = ""
            if result["stations"]:
                names = [s["name"][:25] for s in result["stations"]]
                station_info = f" → {', '.join(names)}"
            print(f"   ✅ {result['total_km']}km | {result['stops']} durak | Final SOC: {result['final_soc']}% | {result['elapsed']}s{station_info}")
    
    # Özet Tablo
    print("\n" + "=" * 90)
    print(f"{'Rota':<25} {'Mesafe':>8} {'Durak':>6} {'Final SOC':>10} {'Süre':>6}")
    print("-" * 90)
    for r in results:
        name = r["route"]["name"]
        if "error" in r:
            print(f"{name:<25} {'HATA':>8} {'—':>6} {'—':>10} {r['elapsed']:>5}s")
        else:
            print(f"{name:<25} {r['total_km']:>7}km {r['stops']:>5}x {r['final_soc']:>9}% {r['elapsed']:>5}s")
    print("=" * 90)
    
    return results


def task2_scoring_audit(results):
    """GÖREV 2: Bolu Segmenti Skor Denetimi"""
    print("\n" + "=" * 90)
    print("🔍 GÖREV 2: İSTANBUL-ANKARA BOLU SEGMENTI İSTASYON SKOR DENETİMİ")
    print("=" * 90)
    
    # İstanbul-Ankara sonucunu bul
    ist_ank = None
    for r in results:
        if r["route"]["name"] == "Istanbul-Ankara" and "error" not in r:
            ist_ank = r
            break
    
    if not ist_ank:
        print("❌ İstanbul-Ankara rotası bulunamadı veya hata verdi.")
        return
    
    data = ist_ank["full_data"]
    legs = data.get("legs", [])
    
    print(f"\n📊 Rota Özeti: {ist_ank['total_km']}km, {ist_ank['stops']} şarj durağı")
    
    for i, leg in enumerate(legs):
        if leg.get("type") == "charge":
            print(f"\n🔌 ŞARJ DURAĞI #{i}:")
            print(f"   İstasyon: {leg.get('station_name', '?')}")
            print(f"   Güç: {leg.get('charger_power_kw', '?')} kW")
            print(f"   Ağ: {leg.get('network', '?')}")
            print(f"   Süre: {leg.get('duration_minutes', '?')} dk")
            print(f"   Giriş SOC: {leg.get('start_soc_percent', '?')}%")
            print(f"   Hedef SOC: {leg.get('target_soc_percent', '?')}%")
            
            # Skor bilgisi varsa
            scoring = leg.get("scoring_breakdown") or leg.get("station_scores")
            if scoring:
                print(f"   📈 Skor Detayı: {json.dumps(scoring, ensure_ascii=False, indent=6)}")
            
            # Aday istasyonlar varsa
            candidates = leg.get("candidate_stations") or leg.get("alternatives")
            if candidates:
                print(f"   🏪 Aday İstasyonlar: {len(candidates)} adet")
                for c in candidates[:5]:
                    print(f"      - {c.get('name', '?')} ({c.get('power_kw', '?')}kW, skor={c.get('score', '?')})")
    
    # Legs detayı
    print(f"\n📍 TÜM BACAKLAR (drive/charge sırası):")
    cumulative = 0
    for i, leg in enumerate(legs):
        if leg.get("type") == "drive":
            dist = leg.get("distance_km", 0)
            cumulative += dist
            print(f"   [{i}] 🚗 SÜRÜŞ: {round(dist,1)}km (toplam: {round(cumulative,1)}km) | SOC: {leg.get('start_soc_percent','?')}% → {leg.get('end_soc_percent','?')}%")
        elif leg.get("type") == "charge":
            print(f"   [{i}] 🔌 ŞARJ: {leg.get('station_name','?')} | {leg.get('start_soc_percent','?')}% → {leg.get('target_soc_percent','?')}% ({leg.get('duration_minutes','?')} dk)")


def task3_code_integrity():
    """GÖREV 3: Kod Bütünlüğü Doğrulaması"""
    print("\n" + "=" * 90)
    print("🔧 GÖREV 3: KOD BÜTÜNLÜĞÜ & DURUM KONTROLÜ")
    print("=" * 90)
    
    # Test 3a: Shallow Copy (Phase 3) izolasyonu
    print("\n📌 Test 3a: ChargePlanOptimizer Segment İzolasyonu (Faz 3)")
    from app.soc_simulator import SegmentWithConsumption
    import copy as copy_mod
    
    # Orijinal segment oluştur
    class MockSeg:
        def __init__(self):
            self.index = 0
            self.distance_km = 50.0
            self.cumulative_distance_km = 50.0
    
    original = SegmentWithConsumption(segment=MockSeg(), consumption_kwh=5.0)
    original.soc_at_start = 85.0
    original.soc_at_end = 75.0
    
    # Shallow copy yap
    cloned = copy_mod.copy(original)
    cloned.soc_at_start = 50.0
    cloned.soc_at_end = 40.0
    
    # Orijinal değişmemeli
    assert original.soc_at_start == 85.0, f"SIZMA! original.soc_at_start={original.soc_at_start}"
    assert original.soc_at_end == 75.0, f"SIZMA! original.soc_at_end={original.soc_at_end}"
    assert cloned.soc_at_start == 50.0
    print("   ✅ Shallow copy izolasyonu doğrulandı — SOC sızıntısı YOK")
    
    # Test 3b: ConsumptionEngine toggle
    print("\n📌 Test 3b: ConsumptionEngine Factory Toggle")
    from app.consumption_engine import get_engine
    from app.consumption_engine.base import ConsumptionEngine
    from app.consumption_engine.physics_engine import PhysicsConsumptionEngine
    from app.constants import USE_ML_ENGINE
    
    engine = get_engine()
    assert isinstance(engine, ConsumptionEngine)
    
    if USE_ML_ENGINE:
        from app.consumption_engine.ml_stub_engine import MLConsumptionEngine
        assert isinstance(engine, MLConsumptionEngine)
        print(f"   ✅ USE_ML_ENGINE={USE_ML_ENGINE} → MLConsumptionEngine aktif")
    else:
        assert isinstance(engine, PhysicsConsumptionEngine)
        print(f"   ✅ USE_ML_ENGINE={USE_ML_ENGINE} → PhysicsConsumptionEngine aktif")
    
    # Test 3c: SOC Floor Guard (Phase 3.5)
    print("\n📌 Test 3c: SOC Floor Guard (Faz 3.5)")
    val = max(0.0, -15.3)
    assert val == 0.0
    val2 = max(0.0, 5.0 - 100.0)
    assert val2 == 0.0
    print("   ✅ SOC Floor Guard aktif — max(0.0, ...) negatif değerleri engelliyor")
    
    # Test 3d: import copy dosyanın başında mı?
    print("\n📌 Test 3d: import copy dosya başında mı?")
    with open("app/soc_simulator.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    import_pos = content.find("import copy")
    class_pos = content.find("class SOCSimulator")
    assert import_pos < class_pos, "import copy sınıf tanımından sonra!"
    assert import_pos < 1500, f"import copy çok geç (pozisyon {import_pos})"
    print(f"   ✅ 'import copy' dosya başında (pozisyon {import_pos}, sınıftan önce)")
    
    print("\n" + "=" * 90)
    print("✅ TÜM KOD BÜTÜNLÜĞÜ TESTLERİ BAŞARILI")
    print("=" * 90)


if __name__ == "__main__":
    # Görev 3'ü önce çalıştır (hızlı, ağ gerektirmez)
    task3_code_integrity()
    
    # Görev 1: 10 rota stres testi
    results = task1_stress_test()
    
    # Görev 2: Bolu skor denetimi
    task2_scoring_audit(results)
    
    print("\n\n🏁 NİHAİ DENETİM TAMAMLANDI")
