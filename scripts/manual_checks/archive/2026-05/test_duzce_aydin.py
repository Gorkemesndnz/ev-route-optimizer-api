"""
Düzce -> Aydın rota testi (~700 km)
Refactor 2/3 sonrası end-to-end doğrulama.
"""
import httpx
import json
import time
import sys

BASE_URL = "http://127.0.0.1:8000"

# MG4 51 kWh — orta menzilli BEV (zorlu test)
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
    "name": "Duzce -> Aydin (~700 km)",
    "start_location": {"lat": 40.8438, "lon": 31.1565},   # Duzce
    "end_location": {"lat": 37.8560, "lon": 27.8416},     # Aydin
    "vehicle_model_id": "mg4_51",
    "vehicle_spec": VEHICLE_SPEC,
    "current_soc_percent": 85,
    "smart_plan_enabled": True,
    "optimization_mode": "balanced",
}


def main():
    body = {k: v for k, v in TEST.items() if k != "name"}
    print(f"\n{'='*70}\n  {TEST['name']}\n{'='*70}")
    print(f"Start SOC: {TEST['current_soc_percent']}%, Mode: {TEST['optimization_mode']}, Smart Plan: {TEST['smart_plan_enabled']}")
    t0 = time.time()
    with httpx.Client(timeout=300.0) as client:
        r = client.post(f"{BASE_URL}/optimize_route", json=body)
    dt = round(time.time() - t0, 1)
    print(f"\nResponse status: {r.status_code} ({dt}s)")

    if r.status_code != 200:
        print(f"HATA:\n{r.text[:2500]}")
        sys.exit(1)

    payload = r.json()
    if not payload.get("success"):
        print(f"FAIL: {json.dumps(payload, indent=2, ensure_ascii=False)[:1500]}")
        sys.exit(1)

    data = payload["data"]
    print("\n" + "-" * 70)
    print(f"  GENEL SONUC")
    print("-" * 70)
    print(f"  Status:        {data.get('status')}")
    print(f"  Total km:      {data.get('total_distance_km')}")
    print(f"  Total dk:      {data.get('total_duration_minutes')}")
    print(f"  Charge stops:  {data.get('charge_stops')}")
    print(f"  Consumption:   {data.get('consumption_kwh')} kWh")
    print(f"  CO2 saved:     {data.get('total_co2_savings_kg')} kg")
    print(f"  Charging cost: {data.get('total_charging_cost')}")
    print(f"  trip_id:       {data.get('trip_id')}")
    print(f"  Message:       {data.get('message')}")

    legs = data.get("legs", [])
    print(f"\n  Bacaklar (toplam {len(legs)}):")
    for i, leg in enumerate(legs):
        if leg.get("type") == "drive":
            print(
                f"    {i+1}. DRIVE  {leg.get('distance_km'):>6.1f} km, "
                f"{leg.get('duration_minutes'):>4} dk, "
                f"SOC {leg.get('start_soc_percent'):>5}% -> {leg.get('end_soc_percent'):>5}%, "
                f"cons={leg.get('consumption_kwh'):>5.1f} kWh"
            )
        elif leg.get("type") == "charge":
            station = leg.get("station", {})
            connectors = station.get("connectors") or [{}]
            power = connectors[0].get("power_kw") if connectors else "?"
            print(
                f"    {i+1}. CHARGE {station.get('name', '?')} "
                f"({power} kW), "
                f"SOC {leg.get('arrival_soc_percent')}% -> {leg.get('target_soc_percent')}%, "
                f"+{leg.get('energy_added_kwh')} kWh, {leg.get('duration_minutes')} dk, "
                f"{leg.get('estimated_cost')}"
            )

    warnings = data.get("warning_messages") or data.get("warnings") or []
    if warnings:
        print(f"\n  Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")

    insights = data.get("insights") or []
    if insights:
        print(f"\n  Insights ({len(insights)}):")
        for ins in insights[:5]:
            print(f"    - [{ins.get('type')}] {ins.get('message')}")


if __name__ == "__main__":
    main()
