from app.services.base_service import BaseService, ExternalAPIError
from app.utils.config_manager import config
from app.utils.cache_manager import cacheable
from app.utils.logger import get_logger

logger = get_logger("ocm_service")


class OCMService(BaseService):
    def __init__(self):
        super().__init__(base_url="https://api.openchargemap.io/v3")
        self.api_key = config.get_ocm_api_key()

    @cacheable(prefix="ocm_stations", ttl_seconds=None)
    async def get_stations_nearby(self, latitude: float, longitude: float, radius_km: int) -> list:
        """
        Open Charge Map API - Belirtilen koordinatlar yakınındaki şarj istasyonlarını alır
        """
        try:
            params = {
                "latitude": latitude,
                "longitude": longitude,
                "distance": radius_km,
                "distanceunit": "KM",
                "maxresults": 50,  # Maksimum 50 istasyon
                "compact": "true",  # Daha küçük response
                "verbose": "false"  # Detaysız response
            }
            
            # API key varsa ekle
            if self.api_key:
                params["key"] = self.api_key
                
            return await self._call_api("GET", "/poi", params=params)
        except ExternalAPIError as e:
            logger.error(
                "OCM API çağrısı başarısız",
                latitude=latitude,
                longitude=longitude,
                radius_km=radius_km,
                error=e.detail
            )
            return []


# Kolay erişim için instance
ocm_service = OCMService()
