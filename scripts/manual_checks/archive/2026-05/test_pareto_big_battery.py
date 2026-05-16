"""
Pareto Optimizasyon Test — 75 kWh batarya ile orta mesafe
==========================================================
Daha buyuk batarya = backward planner daha dusuk min_target uretebilir
= Pareto gercek bir grid uzerinde optimize edebilir.

Bursa -> Eskisehir, ~250 km — tek stop bile yeterli olabilir.
"""
import httpx
import json
import time
import sys

BASE_URL = "http://127.0.0.1:8000"

# Tesla Model 3 LR sim (75 kWh)
VEHICLE_BIG = {
    "id": 2,
    "slug": "tesla-m3-lr",
    "brand": "Tesla",
    "model": "Model 3",
    "variant": "Long Range",
    "year": 2023,
    "battery_useable_kwh": 75.0,
    "battery_nominal_kwh": 79.5,
    "battery_chemistry": "NMC",
    "battery_thermal_management": "Sivi Sogutmali",
    "heat_pump": True,
    "wltp_range_tel_km": 580,
    "wltp_nominal_consumption_wh_km": 140,
    "real_range_km": 480,
    "efficiency_wh_km": 165,
    "curb_weight_kg": 1844,
    "drag_coefficient": 0.23,
    "frontal_area_m2": 2.22,
    "max_charge_dc_kw": 250,
    "max_charge_ac_kw": 11,
    "connector_type": "CCS2",
    "vehicle_type": "BEV",
    "drivetrain": "AWD",
    "tires_size": "235/45R18",
}


SCENARIOS = [
    # Orta mesafe, dusuk SOC -> 1-2 stop bandi, Pareto optimize edebilir
    {
        "name": "Ankara->Konya (260km, 60% SOC, 75kWh)",
        "start_location": {"lat": 39.9334, "lon": 32.8597},
        "end_location": {"lat": 37.8746, "lon": 32.4932},
        "current_soc_percent": 60,
    },
    # Orta mesafe, normal SOC
    {
        "name": "Bursa->Ankara (380km, 70% SOC, 75kWh)",
        "start_location": {"lat": 40.1826, "lon": 29.0665},
        "end_location": {"lat": 39.9334, "lon": 32.8597},
        "current_soc_percent": 70,
    },
]


def run(scenario):
    body = {
        "start_location": scenario["start_location"],
        "end_location": scenario["end_location"],
        "vehicle_model_id": "tesla_m3_lr",
        "vehicle_spec": VEHICLE_BIG,
        "current_soc_percent": scenario["current_soc_percent"],
        "smart_plan_enabled": True,
        "optimization_mode": "balanced",
    }
    print(f"\n{'='*70}\n  {scenario['name']}\n{'='*70}")
    t0 = time.time()
    with httpx.Client(timeout=300.0) as client:
        r = client.post(f"{BASE_URL}/optimize_route", json=body)
    dt = round(time.time() - t0, 1)
    print(f"Status: {r.status_code} ({dt}s)")
    if r.status_code != 200:
        print(r.text[:1500]); return
    data = r.json()["data"]
    print(f"Distance: {data.get('total_distance_km')}km | Duration: {data.get('total_duration_minutes')}dk | Stops: {data.get('charge_stops')}")
    for i, leg in enumerate(data.get("legs", [])):
        if leg.get("type") == "drive":
            print(f"  {i+1}. DRIVE {leg.get('distance_km')}km, SOC {leg.get('start_soc_percent')}%->{leg.get('end_soc_percent')}%, {leg.get('consumption_kwh')}kWh")
        else:
            station = leg.get("station", {})
            connectors = station.get("connectors", [{}])
            power = connectors[0].get("power_kw", "?") if connectors else "?"
            print(f"  {i+1}. CHARGE {station.get('name')} ({power}kW), {leg.get('arrival_soc_percent')}%->{leg.get('target_soc_percent')}%, {leg.get('duration_minutes')}dk, ${leg.get('estimated_cost')}")


if __name__ == "__main__":
    for s in SCENARIOS:
        run(s)
