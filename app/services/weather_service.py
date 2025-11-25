from typing import Optional, Dict, Any
from app.services.base_service import BaseService, ExternalAPIError
from app.utils.config_manager import config
from app.utils.cache_manager import cacheable
from app.utils.logger import get_logger
from app.models import WeatherInfo, WeatherCondition

logger = get_logger("WeatherService")


class WeatherService(BaseService):
    """
    OpenWeatherMap üzerinden hava durumu bilgisi çeken servis.
    Dönen veriler domain modeli olan WeatherInfo’ya dönüştürülür.
    Forecast (tahmin) endpoint'i ileride ML modeli için desteklenecek.
    """

    # OpenWeatherMap condition ID -> WeatherCondition mapping
    CONDITION_MAP = {
        # 2xx: Thunderstorm
        200: WeatherCondition.RAIN, 201: WeatherCondition.RAIN, 202: WeatherCondition.RAIN,
        210: WeatherCondition.RAIN, 211: WeatherCondition.RAIN, 212: WeatherCondition.RAIN,
        221: WeatherCondition.RAIN, 230: WeatherCondition.RAIN, 231: WeatherCondition.RAIN, 232: WeatherCondition.RAIN,

        # 3xx: Drizzle
        300: WeatherCondition.RAIN, 301: WeatherCondition.RAIN, 302: WeatherCondition.RAIN,
        310: WeatherCondition.RAIN, 311: WeatherCondition.RAIN, 312: WeatherCondition.RAIN,
        313: WeatherCondition.RAIN, 314: WeatherCondition.RAIN, 321: WeatherCondition.RAIN,

        # 5xx: Rain
        500: WeatherCondition.RAIN, 501: WeatherCondition.RAIN, 502: WeatherCondition.RAIN,
        503: WeatherCondition.RAIN, 504: WeatherCondition.RAIN,
        511: WeatherCondition.SNOW,  # Freezing rain → EV consumption için buz + kar sayıyoruz
        520: WeatherCondition.RAIN, 521: WeatherCondition.RAIN,
        522: WeatherCondition.RAIN, 531: WeatherCondition.RAIN,

        # 6xx: Snow
        600: WeatherCondition.SNOW, 601: WeatherCondition.SNOW, 602: WeatherCondition.SNOW,
        611: WeatherCondition.SNOW, 612: WeatherCondition.SNOW, 613: WeatherCondition.SNOW,
        615: WeatherCondition.SNOW, 616: WeatherCondition.SNOW,
        620: WeatherCondition.SNOW, 621: WeatherCondition.SNOW, 622: WeatherCondition.SNOW,

        # 7xx: Atmosphere (Fog, Dust, Mist)
        701: WeatherCondition.FOG, 711: WeatherCondition.FOG, 721: WeatherCondition.FOG,
        731: WeatherCondition.FOG, 741: WeatherCondition.FOG, 751: WeatherCondition.FOG,
        761: WeatherCondition.FOG, 762: WeatherCondition.FOG,
        771: WeatherCondition.WINDY,  # Squalls → rüzgar
        781: WeatherCondition.WINDY,  # Tornado

        # 800: Clear
        800: WeatherCondition.CLEAR,

        # 801–804: Clouds
        801: WeatherCondition.CLEAR,
        802: WeatherCondition.CLOUDY,
        803: WeatherCondition.CLOUDY,
        804: WeatherCondition.CLOUDY,
    }

    def __init__(self):
        super().__init__(base_url="https://api.openweathermap.org/data/2.5")
        self.api_key = config.get_openweather_api_key()

    # ============================================================
    # 1) CURRENT WEATHER — Rota üzerindeki noktalar için
    # ============================================================
    @cacheable(prefix="weather_point", ttl_seconds=1800)  # 30 dakika
    async def get_weather_at_point(
        self,
        lat: float,
        lon: float,
        time_iso: Optional[str] = None
    ) -> Optional[WeatherInfo]:
        """
        Belirli bir nokta için anlık hava durumu döndürür.
        EV tüketim modeli için yeterli olan:
        temp, condition, wind_speed, wind_direction, precipitation_prob
        bilgilerini içeriyor.
        """
        params = {
            "lat": lat,
            "lon": lon,
            "units": "metric",
            "lang": "en",
            "appid": self.api_key,
        }

        try:
            data = await self.request(
                method="GET",
                endpoint="/weather",
                params=params
            )
        except ExternalAPIError as e:
            logger.error("Weather API request failed", lat=lat, lon=lon, error=e.detail)
            return None

        if not data or str(data.get("cod")) != "200":
            return None

        # 1) Weather Condition
        weather_list = data.get("weather", [])
        if weather_list:
            condition_id = weather_list[0].get("id", 800)
            condition = self.CONDITION_MAP.get(condition_id, WeatherCondition.CLOUDY)
        else:
            condition = WeatherCondition.CLOUDY

        # 2) Temp / Wind
        main = data.get("main", {})
        wind = data.get("wind", {})

        temp_c = float(main.get("temp", 20.0))
        wind_mps = float(wind.get("speed", 0.0))
        wind_deg = int(wind.get("deg", 0))

        # 3) Yağış Olasılığı — Current endpoint pop vermez
        # Basit ama EV consumption için etkili bir heuristik:
        if condition in (WeatherCondition.RAIN, WeatherCondition.SNOW):
            precip_prob = 0.8
        elif condition in (WeatherCondition.CLOUDY, WeatherCondition.FOG):
            precip_prob = 0.2
        else:
            precip_prob = 0.0

        return WeatherInfo(
            temp_c=temp_c,
            condition=condition,
            wind_speed_mps=wind_mps,
            wind_direction_deg=wind_deg,
            precipitation_prob=precip_prob,
        )

    # ============================================================
    # 2) FORECAST WEATHER (V2 için hazır hook)
    # ============================================================
    @cacheable(prefix="weather_forecast", ttl_seconds=1800)
    async def get_forecast_for_point(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """
        Forecast (3 hour / 5 day) verisi.
        V1'de planner bunu kullanmayacak, V2 ML modeline veri üretmek için kullanacağız.
        """
        params = {
            "lat": lat,
            "lon": lon,
            "units": "metric",
            "appid": self.api_key
        }

        try:
            return await self.request(
                method="GET",
                endpoint="/forecast",
                params=params
            )
        except ExternalAPIError as e:
            logger.error("Weather forecast request failed", lat=lat, lon=lon, error=e.detail)
            return None


# Tek instance
weather_service = WeatherService()
