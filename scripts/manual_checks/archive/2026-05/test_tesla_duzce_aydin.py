"""
Tesla Model 3 LR 75 kWh ile Düzce→Aydın — fix doğrulaması.
Daha büyük batarya → daha düşük leg drop → Pareto gerçek optimizasyon yapabilir.
"""
import httpx, json, time, sys

BASE_URL = "http://127.0.0.1:8000"

VEHICLE_SPEC = {
    "id": 2, "slug": "tesla-m3-lr-75",
    "brand": "Tesla", "model": "Model 3", "variant": "Long Range",
    "year": 2023,
    "battery_useable_kwh": 75.0, "battery_nominal_kwh": 78.0,
    "battery_chemistry": "NMC", "battery_thermal_management": "Sivi Sogutmali",
    "heat_pump": True,
    "wltp_range_tel_km": 580, "wltp_nominal_consumption_wh_km": 142,
    "real_range_km": 480, "efficiency_wh_km": 160,
    "curb_weight_kg": 1844, "drag_coefficient": 0.23, "frontal_area_m2": 2.22,
    "max_charge_dc_kw": 250, "max_charge_ac_kw": 11,
    "connector_type": "CCS2", "vehicle_type": "BEV",
    "drivetrain": "AWD", "tires_size": "235/45R18",
}

TEST = {
    "name": "Tesla M3 LR 75: Duzce -> Aydin (~700 km)",
    "start_location": {"lat": 40.8438, "lon": 31.1565},
    "end_location": {"lat": 37.8560, "lon": 27.8416},
    "vehicle_model_id": "tesla_m3_lr_75",
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
    print(f"Response: {r.status_code} ({round(time.time()-t0,1)}s)")
    if r.status_code != 200:
        print(r.text[:1500]); sys.exit(1)
    payload = r.json()
    if not payload.get("success"):
        print(json.dumps(payload, indent=2, ensure_ascii=False)[:1500]); sys.exit(1)
    data = payload["data"]
    print(f"\n  Total km:     {data.get('total_distance_km')}")
    print(f"  Total dk:     {data.get('total_duration_minutes')}")
    print(f"  Charge stops: {data.get('charge_stops')}")
    legs = data.get("legs", [])
    print(f"\n  Bacaklar:")
    for i, leg in enumerate(legs):
        if leg.get("type") == "drive":
            print(f"    {i+1}. DRIVE  {leg.get('distance_km'):>6.1f} km, "
                  f"SOC {leg.get('start_soc_percent'):>5}% -> {leg.get('end_soc_percent'):>5}%")
        elif leg.get("type") == "charge":
            station = leg.get("station", {})
            print(f"    {i+1}. CHARGE {station.get('name','?')[:40]:<40} "
                  f"SOC {leg.get('arrival_soc_percent')}% -> {leg.get('target_soc_percent')}%")


if __name__ == "__main__":
    main()
