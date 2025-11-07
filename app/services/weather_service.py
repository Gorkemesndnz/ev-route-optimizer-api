from app.services.base_service import BaseService, ExternalAPIError
from app.utils.config_manager import config
from app.utils.cache_manager import cacheable
from app.utils.logger import get_logger

logger = get_logger("weather_service")


class WeatherService(BaseService):
    def __init__(self):
        super().__init__(base_url="https://api.openweathermap.org/data/2.5")
        self.api_key = config.get_weather_api_key()

    @cacheable(prefix="weather_forecast", ttl_seconds=None)
    async def get_forecast(self, latitude: float, longitude: float) -> dict:
        """
        OpenWeatherMap API - Belirtilen koordinatlar için hava durumu tahmini alır
        """
        try:
            params = {
                "lat": latitude,
                "lon": longitude,
                "units": "metric",  # Celsius için
                "appid": self.api_key
            }
            return await self._call_api("GET", "/forecast", params=params)
        except ExternalAPIError as e:
            logger.error(
                "Weather API çağrısı başarısız",
                latitude=latitude,
                longitude=longitude,
                error=e.detail
            )
            raise


# Kolay erişim için instance
weather_service = WeatherService()
