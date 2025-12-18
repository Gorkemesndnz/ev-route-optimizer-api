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
    # 1b) DIRECTIONS API → Ham JSON (route_selector için)
    # ============================================================
    @cacheable(prefix="google_directions_raw", ttl_seconds=3600)
    async def get_route_alternatives(
        self, 
        start: GeoPoint, 
        end: GeoPoint,
        alternatives: bool = True
    ) -> dict:
        """
        Google Directions API'den ham JSON döndürür.
        Route selector için alternatif rotaları karşılaştırmak amacıyla kullanılır.
        
        Args:
            start: Başlangıç noktası
            end: Bitiş noktası
            alternatives: True ise 3 alternatif rota alır
        
        Returns:
            Ham Google Directions API response (dict)
        """
        origin = f"{start.lat},{start.lon}"
        destination = f"{end.lat},{end.lon}"

        params = {
            "origin": origin,
            "destination": destination,
            "units": "metric",
            "alternatives": "true" if alternatives else "false",
            "key": self.api_key
        }

        data = await self.request(
            method="GET",
            endpoint="/directions/json",
            params=params
        )

        return data

    # ============================================================
    # 2) ELEVATION API → path + samples = rota boyunca tırmanış
    # ============================================================
    @cacheable(prefix="google_elevation", ttl_seconds=3600)
    async def get_elevation_stats(self, polyline: str, samples: int = 50) -> dict:
        """
        Rota polyline üzerinden örnekleme yaparak toplam yükseliş/iniş döndürür.
        """
        # Polyline zaten Google'dan encoded geliyor, tekrar quote etme!
        params = {
            "path": f"enc:{polyline}",
            "samples": samples,
            "key": self.api_key
        }
        
        logger.info(
            "Elevation API request",
            polyline_length=len(polyline),
            samples=samples
        )

        data = await self.request(
            method="GET",
            endpoint="/elevation/json",
            params=params
        )
        
        status = data.get("status")
        if status != "OK":
            logger.error(
                "Elevation API error",
                status=status,
                error_message=data.get("error_message", "Unknown")
            )
            raise ExternalAPIError("GoogleElevation", 200, f"status={status}, error={data.get('error_message')}")

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

    # ============================================================
    # 5) PLACES NEARBY SEARCH → EV Şarj İstasyonları
    # ============================================================
    @cacheable(prefix="google_ev_stations", ttl_seconds=3600)
    async def search_ev_charging_stations(
        self,
        lat: float,
        lon: float,
        radius_m: int = 50000,
        max_results: int = 20
    ) -> list:
        """
        Belirli bir konumun etrafındaki EV şarj istasyonlarını arar.
        
        Args:
            lat: Merkez enlem
            lon: Merkez boylam
            radius_m: Arama yarıçapı (metre, max 50000)
            max_results: Maksimum sonuç sayısı
            
        Returns:
            Google Places sonuç listesi (dict) - sadece EV charging stations
        """
        params = {
            "location": f"{lat},{lon}",
            "radius": min(radius_m, 50000),
            "keyword": "electric vehicle charging station",
            "type": "electric_vehicle_charging_station",
            "key": self.api_key
        }

        try:
            data = await self.request(
                method="GET",
                endpoint="/place/nearbysearch/json",
                params=params
            )

            status = data.get("status")
            if status not in ("OK", "ZERO_RESULTS"):
                logger.warning(f"Places Nearby Search status: {status}")
                return []

            raw_results = data.get("results", [])
            
            # Sadece gerçek EV charging station olanları filtrele
            ev_stations = []
            for place in raw_results:
                place_types = place.get("types", [])
                place_name = place.get("name", "").lower()
                
                # EV charging station tipinde olanları al
                is_ev_station = "electric_vehicle_charging_station" in place_types
                
                # Veya isimde şarj ile ilgili kelime varsa
                charging_keywords = ["şarj", "charge", "charging", "supercharger", "eşarj", "zes", "trugo", "voltrun", "astor"]
                has_charging_keyword = any(kw in place_name for kw in charging_keywords)
                
                if is_ev_station or has_charging_keyword:
                    ev_stations.append(place)
            
            results = ev_stations[:max_results]
            
            logger.info(
                f"Google EV stations found: {len(results)} (filtered from {len(raw_results)}) near ({lat:.4f}, {lon:.4f})",
                radius_m=radius_m
            )
            
            return results
            
        except Exception as e:
            logger.error(f"Google Places search failed: {e}")
            return []

    # ============================================================
    # 6) PLACE DETAILS (Enhanced) → Detaylı istasyon bilgisi
    # ============================================================
    @cacheable(prefix="google_station_details", ttl_seconds=86400)
    async def get_station_details(self, place_id: str) -> dict:
        """
        Şarj istasyonu için detaylı bilgi al.
        Rating, yorum sayısı, açık/kapalı durumu vb.
        
        Args:
            place_id: Google Place ID
            
        Returns:
            {name, rating, user_ratings_total, types, opening_hours, vicinity, geometry}
        """
        params = {
            "place_id": place_id,
            "fields": "name,rating,user_ratings_total,types,opening_hours,vicinity,geometry,formatted_address,business_status",
            "key": self.api_key
        }

        try:
            data = await self.request(
                method="GET",
                endpoint="/place/details/json",
                params=params
            )

            if data.get("status") != "OK":
                return {}

            result = data.get("result", {})
            
            return {
                "place_id": place_id,
                "name": result.get("name", ""),
                "rating": result.get("rating", 0.0),
                "user_ratings_total": result.get("user_ratings_total", 0),
                "types": result.get("types", []),
                "is_open_now": result.get("opening_hours", {}).get("open_now"),
                "vicinity": result.get("vicinity", ""),
                "formatted_address": result.get("formatted_address", ""),
                "business_status": result.get("business_status", ""),
                "location": result.get("geometry", {}).get("location", {})
            }
            
        except Exception as e:
            logger.warning(f"Station details failed for {place_id}: {e}")
            return {}

    # ============================================================
    # 7) GEOCODING API → Adres string'ini koordinata çevirir
    # ============================================================
    @cacheable(prefix="google_geocode", ttl_seconds=86400)  # 24 saat cache
    async def geocode(self, address: str) -> GeoPoint:
        """
        Adres string'ini GeoPoint koordinatına çevirir.
        
        Args:
            address: Adres string'i (örn: "Istanbul, Turkey")
            
        Returns:
            GeoPoint: {lat, lon} koordinatları
        """
        params = {
            "address": address,
            "key": self.api_key
        }

        data = await self.request(
            method="GET",
            endpoint="/geocode/json",
            params=params
        )

        status = data.get("status")
        if status != "OK":
            raise ExternalAPIError("GoogleGeocode", 200, f"status={status}, address={address}")

        results = data.get("results", [])
        if not results:
            raise ExternalAPIError("GoogleGeocode", 200, f"No results for address: {address}")

        location = results[0]["geometry"]["location"]
        
        logger.info(
            f"Geocoded address: {address}",
            lat=location["lat"],
            lng=location["lng"]
        )
        
        return GeoPoint(lat=location["lat"], lon=location["lng"])


# Tek instance
google_maps = GoogleMapsService()
