"""
Config Manager v2.0
===================

Enterprise-level merkezi konfigürasyon yöneticisi.

Özellikler:
- .env + environment variable birleşik okuma
- Type-safe getter fonksiyonları
- Production / Staging / Development profile sistemi
- Eksik env'ler için otomatik uyarı loglama
- API key & hassas veriler için güvenli kullanım
- Ayarların gruplanmış olarak okunabilmesi
"""

import os
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from app.utils.logger import get_logger

logger = get_logger("ConfigManager")

# .env dosyasını yükle
load_dotenv()


class ConfigManager:
    """
    Merkezi configuration yöneticisi.
    Her env değişkenini güvenli şekilde okur, tip dönüşümü yapar ve uyarı verir.
    """

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    @staticmethod
    def _get(key: str, default: str = "", warn_if_empty: bool = False) -> str:
        """Environment variable oku, yoksa default döndür."""
        value = os.getenv(key, default)
        if warn_if_empty and (value is None or value == ""):
            logger.warning(f"Config key missing or empty: {key}")
        return value or default

    @staticmethod
    def _get_int(key: str, default: int) -> int:
        value = os.getenv(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            logger.error(f"Invalid INT config: {key}='{value}', using default={default}")
            return default

    @staticmethod
    def _get_float(key: str, default: float) -> float:
        value = os.getenv(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            logger.error(f"Invalid FLOAT config: {key}='{value}', using default={default}")
            return default

    @staticmethod
    def _get_bool(key: str, default: bool = False) -> bool:
        value = os.getenv(key)
        if value is None:
            return default
        return value.lower() in ("1", "true", "yes", "on")

    # -------------------------------------------------------------------------
    # ENVIRONMENT
    # -------------------------------------------------------------------------

    @staticmethod
    def environment() -> str:
        env = ConfigManager._get("ENVIRONMENT", "production").lower()
        if env not in ("development", "production", "test", "staging"):
            logger.warning(f"Unknown ENVIRONMENT '{env}', using 'development'")
            return "development"
        return env

    @staticmethod
    def is_debug() -> bool:
        return ConfigManager.environment() == "development"

    @staticmethod
    def is_production() -> bool:
        return ConfigManager.environment() == "production"

    # -------------------------------------------------------------------------
    # API KEYS
    # -------------------------------------------------------------------------

    @staticmethod
    def google_api_key() -> str:
        return ConfigManager._get("GOOGLE_API_KEY", "", warn_if_empty=True)

    @staticmethod
    def ocm_api_key() -> str:
        return ConfigManager._get("OCM_API_KEY", "", warn_if_empty=True)

    @staticmethod
    def weather_api_key() -> str:
        # Önce OPENWEATHER_API_KEY, yoksa WEATHER_API_KEY
        key = os.getenv("OPENWEATHER_API_KEY") or os.getenv("WEATHER_API_KEY") or ""
        if not key:
            logger.warning("Config key missing: OPENWEATHER_API_KEY or WEATHER_API_KEY")
        return key

    # Alias
    @staticmethod
    def get_openweather_api_key() -> str:
        return ConfigManager.weather_api_key()

    # -------------------------------------------------------------------------
    # CACHE TTL
    # -------------------------------------------------------------------------

    @staticmethod
    def ttl_ocm() -> int:
        return ConfigManager._get_int("CACHE_TTL_OCM", 14400)

    @staticmethod
    def ttl_weather() -> int:
        return ConfigManager._get_int("CACHE_TTL_WEATHER", 3600)

    @staticmethod
    def ttl_places() -> int:
        return ConfigManager._get_int("CACHE_TTL_GOOGLE_PLACES", 86400)

    @staticmethod
    def ttl_directions() -> int:
        return ConfigManager._get_int("CACHE_TTL_GOOGLE_DIRECTIONS", 3600)

    # -------------------------------------------------------------------------
    # ROUTE SELECTOR
    # -------------------------------------------------------------------------

    @staticmethod
    def selector_threshold() -> float:
        return ConfigManager._get_float("ROUTE_SELECTOR_THRESHOLD_PERCENT", 0.10)

    # -------------------------------------------------------------------------
    # SUSTAINABILITY
    # -------------------------------------------------------------------------

    @staticmethod
    def co2_avg_ice_consumption() -> float:
        return ConfigManager._get_float("CO2_AVG_ICE_CONSUMPTION", 7.0)

    @staticmethod
    def co2_region_multiplier() -> float:
        return ConfigManager._get_float("CO2_REGION_MULTIPLIER_TR", 1.1)

    @staticmethod
    def electricity_emission_factor_tr() -> float:
        """Türkiye elektrik şebekesi emisyon faktörü (gCO2/kWh)"""
        return ConfigManager._get_float("ELECTRICITY_EMISSION_FACTOR_TR", 475.0)

    @staticmethod
    def electricity_emission_factor_eu() -> float:
        """AB elektrik şebekesi emisyon faktörü (gCO2/kWh)"""
        return ConfigManager._get_float("ELECTRICITY_EMISSION_FACTOR_EU", 300.0)

    @staticmethod
    def electricity_emission_factor_us() -> float:
        """ABD elektrik şebekesi emisyon faktörü (gCO2/kWh)"""
        return ConfigManager._get_float("ELECTRICITY_EMISSION_FACTOR_US", 400.0)

    # -------------------------------------------------------------------------
    # LOGGING
    # -------------------------------------------------------------------------

    @staticmethod
    def log_level() -> str:
        return ConfigManager._get("LOG_LEVEL", "INFO")

    # -------------------------------------------------------------------------
    # DEBUG / PRINT CONFIG
    # -------------------------------------------------------------------------

    @staticmethod
    def dump_config() -> Dict[str, Any]:
        """
        Debug amaçlı tüm kritik config değerlerini döner.
        Hassas dataları (API key'ler) her ortamda maskeler.
        """
        return {
            "environment": ConfigManager.environment(),
            "google_api_key": "***HIDDEN***",
            "ocm_api_key": "***HIDDEN***",
            "weather_api_key": "***HIDDEN***",
            "cache_ttl": {
                "ocm": ConfigManager.ttl_ocm(),
                "weather": ConfigManager.ttl_weather(),
                "places": ConfigManager.ttl_places(),
                "directions": ConfigManager.ttl_directions(),
            },
            "selector_threshold": ConfigManager.selector_threshold(),
            "co2": {
                "avg_ice": ConfigManager.co2_avg_ice_consumption(),
                "region_multiplier": ConfigManager.co2_region_multiplier(),
            },
        }

    # -------------------------------------------------------------------------
    # BACKWARD COMPATIBILITY ALIASES
    # -------------------------------------------------------------------------
    # Bu alias'lar mevcut kodun bozulmaması için eklenmiştir.
    # Yeni kod yeni method isimlerini kullanmalıdır.
    
    # API Keys
    get_google_api_key = google_api_key
    get_ocm_api_key = ocm_api_key
    get_weather_api_key = weather_api_key
    
    # Cache TTL
    get_cache_ttl_ocm = ttl_ocm
    get_cache_ttl_weather = ttl_weather
    get_cache_ttl_google_places = ttl_places
    get_cache_ttl_google_directions = ttl_directions
    
    # Route Selector
    get_route_selector_threshold_percent = selector_threshold
    
    # CO2 / Sustainability
    get_co2_avg_ice_consumption = co2_avg_ice_consumption
    get_co2_region_multiplier_tr = co2_region_multiplier
    get_electricity_emission_factor_tr = electricity_emission_factor_tr
    get_electricity_emission_factor_eu = electricity_emission_factor_eu
    get_electricity_emission_factor_us = electricity_emission_factor_us
    
    # Environment
    get_environment = environment
    get_log_level = log_level


# Singleton instance
config = ConfigManager()
