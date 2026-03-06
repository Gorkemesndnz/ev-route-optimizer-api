import json
with open('direct_output.json', encoding='utf-16le') as f:
    data = json.load(f)
    print("Total KM:", data['route']['total_distance_km'])
    # Check if there are hotspots in sim_result metadata
    print("MetaData:", list(data.keys()))
    for leg in data['route']['legs']:
        print(f"Leg {leg['type']} - dist: {leg.get('distance_km', 0)}km, cons: {leg.get('consumption_kwh', 0)}kWh, SOC {leg.get('start_soc_percent')}->{leg.get('end_soc_percent')}")
