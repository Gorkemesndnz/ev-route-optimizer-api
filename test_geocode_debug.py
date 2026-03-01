import asyncio
import httpx
from app.services.google_service import google_maps
from app.utils.config_manager import config

async def test():
    key = config.get_google_api_key()
    print(f"API Key length: {len(key)}")
    print(f"API Key first 8: {key[:8]}")
    print(f"API Key last 8: {key[-8:]}")
    
    # 1) google_service uzerinden test
    print("\n--- Test 1: google_service.geocode() ---")
    try:
        result = await google_maps.geocode("Istanbul, Turkey")
        print(f"SUCCESS: lat={result.lat}, lon={result.lon}")
    except Exception as e:
        print(f"ERROR TYPE: {type(e).__name__}")
        print(f"ERROR: {e}")
    
    # 2) Dogrudan httpx ile test
    print("\n--- Test 2: Direct httpx call ---")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": "Istanbul, Turkey", "key": key}
        )
        data = r.json()
        print(f"HTTP Status: {r.status_code}")
        print(f"API Status: {data.get('status')}")
        if data.get("error_message"):
            print(f"Error msg: {data['error_message']}")
        if data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            print(f"Result: lat={loc['lat']}, lng={loc['lng']}")

    # 3) base_service request ile test
    print("\n--- Test 3: base_service.request() ---")
    try:
        raw = await google_maps.request(
            method="GET",
            endpoint="/geocode/json",
            params={"address": "Istanbul, Turkey", "key": key}
        )
        print(f"Raw status: {raw.get('status')}")
        if raw.get("error_message"):
            print(f"Raw error: {raw['error_message']}")
        if raw.get("results"):
            print(f"Results count: {len(raw['results'])}")
    except Exception as e:
        print(f"ERROR TYPE: {type(e).__name__}")
        print(f"ERROR: {e}")

asyncio.run(test())
