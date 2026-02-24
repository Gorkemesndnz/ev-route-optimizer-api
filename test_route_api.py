
import requests
import json
import sys

def test_route():
    url = "http://localhost:8000/optimize_route"
    
    # Payload matching RouteRequest schema
    payload = {
        "start_location": {"lat": 41.0082, "lon": 28.9784}, # Istanbul
        "end_location": {"lat": 39.9334, "lon": 32.8597},   # Ankara
        "vehicle_model_id": "abarth_500e_hatchback_2024",
        "current_soc_percent": 80.0,
        "passenger_count": 1
    }

    try:
        print(f"Sending request to {url}...")
        resp = requests.post(url, json=payload)
        
        if resp.status_code != 200:
            print(f"Error: Status {resp.status_code}")
            print(resp.text)
            return

        data = resp.json()
        print("Response received.")

        legs = data.get("legs", [])
        print(f"Total legs: {len(legs)}")

        missing_polyline_count = 0
        total_drive_legs = 0

        for i, leg in enumerate(legs):
            l_type = leg.get("type")
            if l_type == "drive":
                total_drive_legs += 1
                poly = leg.get("polyline")
                if not poly:
                    print(f"Leg {i} (drive) missing polyline!")
                    missing_polyline_count += 1
                else:
                    print(f"Leg {i} (drive) has polyline (length: {len(poly)})")
            elif l_type == "charge":
                print(f"Leg {i} (charge) at {leg.get('station', {}).get('name')}")

        if missing_polyline_count == 0 and total_drive_legs > 0:
            print("SUCCESS: All drive legs have polylines.")
        elif total_drive_legs == 0:
            print("WARNING: No drive legs found (maybe short distance or error).")
        else:
            print(f"FAILURE: {missing_polyline_count} drive legs missing polylines.")

    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_route()
