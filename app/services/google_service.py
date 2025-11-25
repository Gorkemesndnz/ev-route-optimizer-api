import urllib.parse
from typing import List, Optional
from app.services.base_service import BaseService, ExternalAPIError
from app.utils.config_manager import config
from app.utils.cache_manager import cacheable
from app.utils.logger import get_logger
from app.models import GeoPoint, DriveLeg, WeatherInfo

logger = get_logger("GoogleMapsService")


class GoogleMapsService(BaseService):
    """
    Google Directions, Elevation, DistanceMatrix, Places API
    için merkezi servis.
    """

    def __init__(self):
        super().__init__(base_url="https://maps.googleapis.com/maps/api")
        self.api_key = config.get_google_api_key()

    # ============================================================
    # 1) DIRECTIONS API → DriveLeg listesi üretir
    # ============================================================
    @cacheable(prefix="google_directions", ttl_seconds=3600)
    async def get_directions(self, start: GeoPoint, end: GeoPoint) -> List[DriveLeg]:
        origin = f"{start.lat},{start.lon}"
        destination = f"{end.lat},{end.lon}"

        params = {
            "origin": origin,
            "destination": destination,
            "units": "metric",
            "alternatives": "false",
            "key": self.api_key
        }

        data = await self.request(
            method="GET",
            endpoint="/directions/json",
            params=params
        )

        status = data.get("status")
        if status != "OK":
            raise ExternalAPIError("GoogleDirections", 200, f"status={status}")

        legs = []
        route = data["routes"][0]

        for leg in route["legs"]:
            legs.append(
                DriveLeg(
                    start_point=start,
                    end_point=end,
                    distance_km=leg["distance"]["value"] / 1000,
                    duration_minutes=leg["duration"]["value"] / 60,
                    avg_speed_kmh=max(1, leg["distance"]["value"] / 1000) / (leg["duration"]["value"] / 3600),
                    consumption_kwh=0,  # Planner içinde hesaplanacak
                    start_soc_percent=0,  # Planner dolduracak
                    end_soc_percent=0,    # Planner dolduracak
                    elevation_gain_m=0,   # Elevation API ile sonra doldurulacak
                    elevation_loss_m=0,
                    polyline=route["overview_polyline"]["points"],
                    weather_context=None
                )
            )

        return legs

    # ============================================================
    # 2) ELEVATION API → path + samples = rota boyunca tırmanış
    # ============================================================
    @cacheable(prefix="google_elevation", ttl_seconds=3600)
    async def get_elevation_stats(self, polyline: str, samples: int = 50) -> dict:
        """
        Rota polyline üzerinden örnekleme yaparak toplam yükseliş/iniş döndürür.
        """
        encoded = urllib.parse.quote(polyline)

        params = {
            "path": f"enc:{encoded}",
            "samples": samples,
            "key": self.api_key
        }

        data = await self.request(
            method="GET",
            endpoint="/elevation/json",
            params=params
        )

        if data.get("status") != "OK":
            raise ExternalAPIError("GoogleElevation", 200, f"status={data.get('status')}")

        elevations = [p["elevation"] for p in data.get("results", [])]

        total_gain = 0
        total_loss = 0

        for i in range(1, len(elevations)):
            diff = elevations[i] - elevations[i - 1]
            if diff > 0:
                total_gain += diff
            else:
                total_loss += abs(diff)

        return {"gain_m": total_gain, "loss_m": total_loss}

    # ============================================================
    # 3) DISTANCE MATRIX API → Metre ve süre bilgisi döner
    # ============================================================
    @cacheable(prefix="google_distancematrix", ttl_seconds=3600)
    async def get_distance_matrix(self, origins: List[GeoPoint], destinations: List[GeoPoint]) -> dict:
        origin_str = "|".join([f"{o.lat},{o.lon}" for o in origins])
        dest_str = "|".join([f"{d.lat},{d.lon}" for d in destinations])

        params = {
            "origins": origin_str,
            "destinations": dest_str,
            "units": "metric",
            "key": self.api_key
        }

        data = await self.request(
            method="GET",
            endpoint="/distancematrix/json",
            params=params
        )

        if data.get("status") != "OK":
            raise ExternalAPIError("GoogleDistanceMatrix", 200, f"status={data.get('status')}")

        return data

    # ============================================================
    # 4) PLACES DETAILS API → sadece ihtiyaç duyulan alanlar
    # ============================================================
    @cacheable(prefix="google_place_details", ttl_seconds=86400)
    async def get_place_details(self, place_id: str) -> dict:
        params = {
            "place_id": place_id,
            "fields": "name,rating,user_ratings_total,geometry",
            "key": self.api_key
        }

        data = await self.request(
            method="GET",
            endpoint="/place/details/json",
            params=params
        )

        if data.get("status") != "OK":
            raise ExternalAPIError("GooglePlaceDetails", 200, f"status={data.get('status')}")

        return data


# Tek instance
google_maps = GoogleMapsService()
