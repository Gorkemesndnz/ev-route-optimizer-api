from app.services.base_service import BaseService, ExternalAPIError
from app.utils.config_manager import config
from app.utils.cache_manager import cacheable
from app.utils.logger import get_logger

logger = get_logger("google_service")


class GoogleMapsService(BaseService):
    def __init__(self):
        super().__init__(base_url="https://maps.googleapis.com/maps/api")
        self.api_key = config.get_google_api_key()

    @cacheable(prefix="google_directions", ttl_seconds=3600)
    async def get_directions(self, origin: str, destination: str) -> dict:
        """
        Google Directions API - Rota ve polyline verisi alır
        """
        try:
            params = {
                "origin": origin,
                "destination": destination,
                "key": self.api_key
            }
            return await self._call_api("GET", "/directions/json", params=params)
        except ExternalAPIError as e:
            logger.error(
                "Google Directions API çağrısı başarısız",
                origin=origin,
                destination=destination,
                error=e.detail
            )
            raise

    @cacheable(prefix="google_elevation", ttl_seconds=3600)
    async def get_elevation_for_path(self, path: str) -> dict:
        """
        Google Elevation API - Rota üzerindeki rakım verilerini alır
        """
        try:
            params = {
                "path": path,
                "samples": "100",  # Yol boyunca 100 örnek nokta
                "key": self.api_key
            }
            return await self._call_api("GET", "/elevation/json", params=params)
        except ExternalAPIError as e:
            logger.error(
                "Google Elevation API çağrısı başarısız",
                path=path,
                error=e.detail
            )
            raise

    @cacheable(prefix="google_distance", ttl_seconds=3600)
    async def get_distance_matrix(self, origins: list, destinations: list) -> dict:
        """
        Google Distance Matrix API - Çoklu mesafe ve süre hesaplaması
        """
        try:
            params = {
                "origins": "|".join(origins),
                "destinations": "|".join(destinations),
                "key": self.api_key
            }
            return await self._call_api("GET", "/distancematrix/json", params=params)
        except ExternalAPIError as e:
            logger.error(
                "Google Distance Matrix API çağrısı başarısız",
                origins=origins,
                destinations=destinations,
                error=e.detail
            )
            raise

    @cacheable(prefix="google_places", ttl_seconds=None)
    async def get_place_details(self, place_id: str) -> dict:
        """
        Google Places API - Mekan detaylarını alır
        """
        try:
            params = {
                "place_id": place_id,
                "fields": "name,formatted_address,geometry",
                "key": self.api_key
            }
            return await self._call_api("GET", "/place/details/json", params=params)
        except ExternalAPIError as e:
            logger.error(
                "Google Places API çağrısı başarısız",
                place_id=place_id,
                error=e.detail
            )
            raise


# Kolay erişim için instance
maps_service = GoogleMapsService()
