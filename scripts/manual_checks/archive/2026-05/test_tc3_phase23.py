"""
TC-3 Regression Test — Phase 2.3 (F-25..F-29 + Refactor 1)
============================================================
Manisa -> Trabzon, ~1304 km, %80 SOC start
Beklenen: 4 stop (eski: 5 stop)
"""
import httpx
import json
import time
import sys

BASE_URL = "http://127.0.0.1:8000"

# Manisa: 38.6191, 27.4289
# Trabzon: 41.0027, 39.7168
# ≈1300+ km
TC3 = {
    "name": "TC-3: Manisa->Trabzon",
    "start_location": {"lat": 38.6191, "lon": 27.4289},
    "end_location": {"lat": 41.0027, "lon": 39.7168},
    "vehicle_model_id": "mg4_51",
    "current_soc_percent": 80,
    "smart_plan_enabled": True,  # Pareto path
    "optimization_mode": "balanced",
}

# MSSQL VehiclePayload formatı — MG4 51 kWh
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
    "battery_thermal_management": "Sıvı Soğutmalı",
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


def run_test():
    url = f"{BASE_URL}/optimize_route"
    body = {
        "start_location": TC3["start_location"],
        "end_location": TC3["end_location"],
        "vehicle_model_id": TC3["vehicle_model_id"],
        "vehicle_spec": VEHICLE_SPEC,
        "current_soc_percent": TC3["current_soc_percent"],
        "smart_plan_enabled": TC3["smart_plan_enabled"],
        "optimization_mode": TC3["optimization_mode"],
    }

    print(f"\n{'='*70}")
    print(f"  {TC3['name']}")
    print(f"{'='*70}")
    print(f"Body: {json.dumps(body, indent=2, ensure_ascii=False)}")
    print(f"\nİstek gönderiliyor...")

    t0 = time.time()
    with httpx.Client(timeout=300.0) as client:
        r = client.post(url, json=body)
    elapsed = round(time.time() - t0, 1)

    print(f"\nResponse status: {r.status_code} ({elapsed}s)")

    if r.status_code != 200:
        print(f"HATA: {r.text[:1000]}")
        return False

    payload = r.json()
    if not payload.get("success"):
        print(f"FAIL: {payload}")
        return False

    data = payload["data"]
    print("\n" + "─" * 70)
    print(f"  SONUÇLAR")
    print("─" * 70)
    print(f"  Status:        {data.get('status')}")
    print(f"  Total km:      {data.get('total_distance_km')}")
    print(f"  Total dk:      {data.get('total_duration_minutes')}")
    print(f"  Charge stops:  {data.get('charge_stops')}")
    print(f"  CO2 saved kg:  {data.get('total_co2_savings_kg')}")
    print(f"  Message:       {data.get('message')}")

    legs = data.get("legs", [])
    print(f"\n  Bacaklar ({len(legs)} toplam):")
    for i, leg in enumerate(legs):
        if leg.get("type") == "drive":
            print(
                f"    {i+1}. DRIVE: {leg.get('distance_km')}km, "
                f"{leg.get('duration_minutes')}dk, "
                f"SOC {leg.get('start_soc_percent')}%->{leg.get('end_soc_percent')}%, "
                f"cons={leg.get('consumption_kwh')}kWh"
            )
        elif leg.get("type") == "charge":
            station = leg.get("station", {})
            connectors = station.get("connectors", [{}])
            power = connectors[0].get("power_kw", "?") if connectors else "?"
            print(
                f"    {i+1}. CHARGE: {station.get('name')} "
                f"({power}kW), "
                f"SOC {leg.get('arrival_soc_percent')}%->{leg.get('target_soc_percent')}%, "
                f"+{leg.get('energy_added_kwh')}kWh, {leg.get('duration_minutes')}dk, "
                f"₺{leg.get('estimated_cost')}"
            )

    warnings = data.get("warnings", []) or []
    if warnings:
        print(f"\n  Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")

    # Doğrulama
    charge_stops = data.get("charge_stops", 0)
    print("\n" + "═" * 70)
    print(f"  DOĞRULAMA")
    print("═" * 70)
    expected_max = 4
    if charge_stops <= expected_max:
        print(f"  ✓ PASS — charge_stops={charge_stops} (<= {expected_max} beklenen)")
    else:
        print(f"  ✗ FAIL — charge_stops={charge_stops} (> {expected_max} beklenen)")

    return charge_stops <= expected_max


if __name__ == "__main__":
    ok = run_test()
    sys.exit(0 if ok else 1)
