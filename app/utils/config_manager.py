import os
from functools import lru_cache
from dotenv import load_dotenv

# .env dosyasını (eğer varsa) yükle. Üretimde environment variable'lar kullanılır.
load_dotenv()

@lru_cache(maxsize=None)
class ConfigManager:
    """ .env veya environment variable'ları okumak için merkezi, cache'li bir konfigürasyon yöneticisi. """
    
    @staticmethod
    def get_google_api_key() -> str:
        return os.getenv("GOOGLE_API_KEY", "")

    @staticmethod
    def get_ocm_api_key() -> str:
        return os.getenv("OCM_API_KEY", "")

    @staticmethod
    def get_weather_api_key() -> str:
        return os.getenv("WEATHER_API_KEY", "")

    @staticmethod
    def get_cache_ttl_ocm() -> int:
        return int(os.getenv("CACHE_TTL_OCM", 14400))

    @staticmethod
    def get_cache_ttl_weather() -> int:
        return int(os.getenv("CACHE_TTL_WEATHER", 3600))

    @staticmethod
    def get_cache_ttl_google_places() -> int:
        return int(os.getenv("CACHE_TTL_GOOGLE_PLACES", 86400))

    @staticmethod
    def get_route_selector_threshold_percent() -> float:
        return float(os.getenv("ROUTE_SELECTOR_THRESHOLD_PERCENT", 0.10))

    @staticmethod
    def get_co2_avg_ice_consumption() -> float:
        return float(os.getenv("CO2_AVG_ICE_CONSUMPTION", 7.0))

    @staticmethod
    def get_co2_region_multiplier_tr() -> float:
        return float(os.getenv("CO2_REGION_MULTIPLIER_TR", 1.1))

# Kullanım kolaylığı için bir instance oluştur
config = ConfigManager()
