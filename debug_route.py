"""
Debug script: İstanbul-Ankara rotası için hotspot ve istasyon debug
"""
import asyncio
import json
from app.models import RouteRequest, GeoPoint
from app.route_planner import plan_route

async def debug_route():
    """İstanbul-Ankara rotasını debug et."""
    
    # İstanbul → Ankara
    request = RouteRequest(
        start_location=GeoPoint(lat=41.0082, lon=28.9784),  # İstanbul
        end_location=GeoPoint(lat=39.9334, lon=32.8597),    # Ankara
        vehicle_model_id="mg4_51kwh",
        current_soc_percent=85,
        route_strategy="optimal"
    )
    
    print("=" * 80)
    print("DEBUG: Istanbul -> Ankara Rota Planlamasi")
    print("=" * 80)
    print(f"Start: {request.start_location.lat}, {request.start_location.lon}")
    print(f"End: {request.end_location.lat}, {request.end_location.lon}")
    print(f"Vehicle: {request.vehicle_model_id}")
    print(f"Start SOC: {request.current_soc_percent}%")
    print("=" * 80)
    
    try:
        result = await plan_route(request)
        
        print("\nSONUC:")
        print(f"Status: {result.status}")
        print(f"Toplam mesafe: {result.total_distance_km:.1f} km")
        print(f"Toplam süre: {result.total_duration_minutes:.0f} dakika")
        
        print("\nSARJ DURAKLARI:")
        charge_legs = [leg for leg in result.legs if hasattr(leg, 'station') and leg.station]
        
        if not charge_legs:
            print("Hic sarj duragi bulunamadi!")
        else:
            for i, leg in enumerate(charge_legs, 1):
                station = leg.station
                print(f"\n  Durak {i}: {station.name}")
                print(f"    Koordinat: {station.location.lat:.4f}, {station.location.lon:.4f}")
                print(f"    Sart suresi: {leg.duration_minutes:.0f} dakika")
                print(f"    Varis SOC: {leg.arrival_soc_percent:.1f}%")
                print(f"    Hedef SOC: {leg.target_soc_percent:.1f}%")
        
        # Tüm leg'leri göster
        print("\nTUM BACAKLAR:")
        for i, leg in enumerate(result.legs):
            leg_type = getattr(leg, 'type', 'drive')
            if leg_type == 'charge':
                print(f"  {i+1}. [ŞARJ] {leg.station.name if hasattr(leg, 'station') and leg.station else 'Bilinmiyor'}")
            else:
                start = getattr(leg, 'start_point', None)
                end = getattr(leg, 'end_point', None)
                if start and end:
                    print(f"  {i+1}. [SÜRÜŞ] {leg.distance_km:.1f} km, {start.lat:.4f},{start.lon:.4f} → {end.lat:.4f},{end.lon:.4f}")
                else:
                    print(f"  {i+1}. [SÜRÜŞ] {leg.distance_km:.1f} km")
        
        return result
        
    except Exception as e:
        print(f"\nHATA: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    asyncio.run(debug_route())
