"""Battery UI SOC correctness test

Checks that the UI should display route start SOC -> final arrival SOC.
"""
import httpx

url = "http://127.0.0.1:8000/optimize_route"

payload = {
    "start_location": {"lat": 41.0082, "lon": 28.9784},
    "end_location": {"lat": 39.9334, "lon": 32.8597},
    "vehicle_model_id": "tesla_model_3_long_range",
    "current_soc_percent": 85
}

resp = httpx.post(url, json=payload, timeout=120.0)
data = resp.json()

legs = data.get("legs", [])
first_drive = next((l for l in legs if l.get("type") == "drive" and l.get("start_soc_percent") is not None), None)
last_drive = next((l for l in reversed(legs) if l.get("type") == "drive" and l.get("end_soc_percent") is not None), None)

print("status=", data.get("status"))
print("route_start_soc=", first_drive.get("start_soc_percent") if first_drive else None)
print("route_end_soc=", last_drive.get("end_soc_percent") if last_drive else None)
print("legs=", len(legs))
