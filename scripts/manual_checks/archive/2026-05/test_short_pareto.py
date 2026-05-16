"""
Kisa Rota Pareto Test — Istanbul→Ankara, ~450 km, %85 SOC
Beklenen: Pareto tam grid'i deniyor (5 stop'tan az), <95% target ureteebilir.
"""
import httpx
import json
import time
import sys

BASE_URL = "http://127.0.0.1:8000"

VEHICLE_SPEC = {
    "id": 1,
    "slug": "mg4-51",
    "brand": "MG",
    "model": "MG4",
    "variant": "Standard Range",
    "year": 2023,
    "battery_useable_kwh": 50.8,
    "battery_nominal_kwh": 51.0,
    "battery_chemistry": "LFP",
    "battery_thermal_management": "Sivi Sogutmali",
    "heat_pump": False,
    "wltp_range_tel_km": 350,
    "wltp_nominal_consumption_wh_km": 145,
    "real_range_km": 280,
    "efficiency_wh_km": 180,
    "curb_weight_kg": 1635,
    "drag_coefficient": 0.27,
    "frontal_area_m2": 2.30,
    "max_charge_dc_kw": 117,
    "max_charge_ac_kw": 11,
    "connector_type": "CCS2",
    "vehicle_type": "BEV",
    "drivetrain": "RWD",
    "tires_size": "215/55R17",
}

TEST = {
    "name": "Istanbul->Ankara (450km)",
    "start_location": {"lat": 41.0082, "lon": 28.9784},
    "end_location": {"lat": 39.9334, "lon": 32.8597},
    "vehicle_model_id": "mg4_51",
    "vehicle_spec": VEHICLE_SPEC,
    "current_soc_percent": 85,
    "smart_plan_enabled": True,
    "optimization_mode": "balanced",
}


def main():
    body = {k: v for k, v in TEST.items() if k != "name"}
    print(f"\n{'='*70}\n  {TEST['name']}\n{'='*70}")
    t0 = time.time()
    with httpx.Client(timeout=300.0) as client:
        r = client.post(f"{BASE_URL}/optimize_route", json=body)
    dt = round(time.time() - t0, 1)
    print(f"Status: {r.status_code} ({dt}s)")
    if r.status_code != 200:
        print(r.text[:1500]); sys.exit(1)
    data = r.json()["data"]
    print(f"Status: {data.get('status')} | Distance: {data.get('total_distance_km')}km | Stops: {data.get('charge_stops')}")
    for i, leg in enumerate(data.get("legs", [])):
        if leg.get("type") == "drive":
            print(f"  {i+1}. DRIVE {leg.get('distance_km')}km, SOC {leg.get('start_soc_percent')}%->{leg.get('end_soc_percent')}%")
        else:
            station = leg.get("station", {})
            connectors = station.get("connectors", [{}])
            power = connectors[0].get("power_kw", "?") if connectors else "?"
            print(f"  {i+1}. CHARGE {station.get('name')} ({power}kW), {leg.get('arrival_soc_percent')}%->{leg.get('target_soc_percent')}%, {leg.get('duration_minutes')}dk, ${leg.get('estimated_cost')}")
    for w in (data.get("warnings") or []):
        print(f"  WARN: {w}")


if __name__ == "__main__":
    main()
