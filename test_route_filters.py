import requests
import json
import time

URL = "http://localhost:8000/optimize_route"

# İstanbul -> Ankara
payload_base = {
    "start_location": {"lat": 41.0082, "lon": 28.9784},
    "end_location": {"lat": 39.9334, "lon": 32.8597},
    "vehicle_model_id": "abarth_500e_hatchback_2024",
    "current_soc_percent": 90.0,
    "extra_load_kg": 0.0,
    "passenger_count": 1,
    "preferences": {} # Doldurulacak
}

def print_result(name, data):
    print(f"\n{'='*50}")
    print(f"TEST: {name}")
    print(f"{'='*50}")
    
    if data["status"] != "success":
        print(f"HATA: {data.get('message', data)}")
        return
        
    print(f"Toplam Mesafe: {data['total_distance_km']:.1f} km")
    print(f"Toplam Süre: {data['total_duration_minutes']:.1f} dk")
    print("\nAdımlar:")
    for i, leg in enumerate(data['legs'], 1):
        if leg['type'] == 'drive':
            print(f"  {i}. Sürüş: {leg['distance_km']:.1f} km, {leg['duration_minutes']:.1f} dk")
        else: # charge
            station = leg.get('station', {})
            print(f"  {i}. Şarj: {station.get('name', 'Bilinmeyen İstasyon')} - {leg['duration_minutes']:.1f} dk şarj")
    print("="*50)


# TEST 1: Filtresiz
print("Test 1 başlatılıyor (Filtresiz)...")
payload_base["preferences"]["road_avoidances"] = {}
r1 = requests.post(URL, json=payload_base)
try:
    print_result("Filtresiz (Normal) Rota", r1.json())
except Exception as e:
    print(f"Error parsing response 1: {e}\n{r1.text}")

print("\n--- 5 saniye bekleniyor ---\n")
time.sleep(5)

# TEST 2: Otoyol ve Ücretli Yol Kaçınmalı
print("Test 2 başlatılıyor (Otoyol ve Ücretli Yol kaçınmalı)...")
payload_base["preferences"]["road_avoidances"] = {
    "avoid_tolls": True,
    "avoid_highways": True,
    "avoid_ferries": False,
    "avoid_osmangazi_bridge": False,
    "avoid_canakkale_bridge": False
}
r2 = requests.post(URL, json=payload_base)
try:
    print_result("Otoyol ve Paralı Yol İPTAL Rota", r2.json())
except Exception as e:
    print(f"Error parsing response 2: {e}\n{r2.text}")
