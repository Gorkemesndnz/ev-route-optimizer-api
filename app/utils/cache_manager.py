import time
import functools
from typing import Any, Callable
from app.utils.config_manager import config

class BaseCache:
    """Cache backend'leri için soyut temel sınıf (V2'de Redis'e geçiş için)."""
    def get(self, key: str) -> Any:
        raise NotImplementedError
    
    def set(self, key: str, value: Any, ttl_seconds: int):
        raise NotImplementedError

class MemoryCache(BaseCache):
    """V1 için basit, 'in-memory' (sözlük tabanlı) cache."""
    _cache: dict = {}
    _expirations: dict = {}
    
    def get(self, key: str) -> Any:
        if key in self._expirations and self._expirations[key] < time.time():
            # Cache süresi dolmuş, sil
            self._cache.pop(key, None)
            self._expirations.pop(key, None)
            return None
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int):
        self._cache[key] = value
        self._expirations[key] = time.time() + ttl_seconds

# V1 için MemoryCache'i varsayılan olarak kullan
cache_backend = MemoryCache()

def cacheable(prefix: str, ttl_seconds: int | None = None):
    """
    Fonksiyon sonuçlarını önbelleğe alan dekoratör. TTL (Time-To-Live) 
    .env'den okunur veya manuel olarak ayarlanır.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # TTL'i belirle
            if ttl_seconds is not None:
                effective_ttl = ttl_seconds
            else:
                # TTL .env'den okunur (prefix'e göre)
                if "ocm" in prefix:
                    effective_ttl = config.get_cache_ttl_ocm()
                elif "weather" in prefix:
                    effective_ttl = config.get_cache_ttl_weather()
                elif "places" in prefix:
                    effective_ttl = config.get_cache_ttl_google_places()
                else:
                    effective_ttl = 3600  # Varsayılan 1 saat

            # Cache anahtarını oluştur
            key_parts = [prefix] + [str(arg) for arg in args] + \
                        [f"{k}={v}" for k, v in sorted(kwargs.items())]
            cache_key = ":".join(key_parts)

            # 1. Cache'i kontrol et
            cached_result = cache_backend.get(cache_key)
            if cached_result is not None:
                return cached_result

            # 2. Cache'de yoksa, fonksiyonu çalıştır
            result = await func(*args, **kwargs)

            # 3. Sonucu cache'e kaydet
            if result is not None:
                cache_backend.set(cache_key, result, effective_ttl)

            return result
        return wrapper
    return decorator
