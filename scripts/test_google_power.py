"""Test Google Places API (New) with evChargeOptions for real power (kW) info."""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.google_service import google_maps


async def main():
    lat, lon = 41.0, 29.0
    print(f"Testing Google Places API (New) near ({lat}, {lon})...")
    print("=" * 70)
    
    stations = await google_maps.search_ev_charging_stations_new(
        lat=lat, lon=lon, radius_m=30000, max_results=10
    )

    print(f"\nTotal stations found: {len(stations)}")
    print("-" * 70)

    for i, s in enumerate(stations):
        name = s.get("name", "Unknown")
        max_power = s.get("max_power_kw", 0)
        connector_count = s.get("connector_count", 0)
        rating = s.get("rating", 0)
        ev_options = s.get("ev_charge_options", {})
        
        print(f"\n[{i+1}] {name}")
        print(f"    Max Power: {max_power} kW")
        print(f"    Connectors: {connector_count}")
        print(f"    Rating: {rating}")
        
        # Show connector details if available
        connectors = ev_options.get("connectorAggregation", [])
        if connectors:
            print(f"    Connector Details:")
            for conn in connectors:
                conn_type = conn.get("type", "Unknown")
                count = conn.get("count", 0)
                rate = conn.get("maxChargeRateKw", 0)
                print(f"      - {conn_type}: {count}x @ {rate} kW")


if __name__ == "__main__":
    asyncio.run(main())
