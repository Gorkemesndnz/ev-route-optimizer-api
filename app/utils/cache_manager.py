"""
Cache Manager (Minimal)
========================

Basit in-memory cache sistemi.

Kullanım:
    from app.utils.cache_manager import cacheable
    
    @cacheable(prefix="directions", ttl_seconds=3600)
    async def get_directions(...):
        ...
"""

import time
import functools
from typing import Any, Callable, Optional, Dict, Tuple
from app.utils.config_manager import config

# =============================================================================
# MEMORY CACHE
# =============================================================================

class MemoryCache:
    """Basit in-memory cache with TTL."""
    
    def __init__(self):
        self._store: Dict[str, Tuple[Any, float]] = {}
    
    def get(self, key: str) -> Optional[Any]:
        """Cache'den değer al."""
        if key not in self._store:
            return None
        value, expiration = self._store[key]
        if expiration < time.time():
            del self._store[key]
            return None
        return value
    
    def set(self, key: str, value: Any, ttl: int) -> None:
        """Cache'e değer yaz."""
        self._store[key] = (value, time.time() + ttl)
    
    def clear(self) -> None:
        """Cache'i temizle."""
        self._store.clear()
    
    def size(self) -> int:
        return len(self._store)


# Singleton
_cache = MemoryCache()


# =============================================================================
# TTL HELPER
# =============================================================================

def _get_ttl(prefix: str) -> int:
    """Prefix'e göre TTL belirle."""
    prefix_lower = prefix.lower()
    if "direction" in prefix_lower:
        return config.get_cache_ttl_google_directions()
    if "place" in prefix_lower:
        return config.get_cache_ttl_google_places()
    if "weather" in prefix_lower:
        return config.get_cache_ttl_weather()
    if "ocm" in prefix_lower:
        return config.get_cache_ttl_ocm()
    if "elevation" in prefix_lower:
        return 86400  # 24 saat (elevation data nadir değişir)
    return 3600  # Default 1 saat


def _make_key(prefix: str, args: tuple, kwargs: dict) -> str:
    """Cache key oluştur."""
    parts = [prefix]
    
    for arg in args:
        # self/instance'ları atla (Service, Manager, Calculator vb.)
        if hasattr(arg, '__class__'):
            cls_name = arg.__class__.__name__
            if any(x in cls_name for x in ('Service', 'Manager', 'Calculator', 'Planner')):
                continue
        # GeoPoint desteği
        if hasattr(arg, 'lat') and hasattr(arg, 'lon'):
            parts.append(f"{arg.lat},{arg.lon}")
        else:
            parts.append(str(arg))
    
    for k, v in sorted(kwargs.items()):
        parts.append(f"{k}={v}")
    
    return ":".join(parts)


# =============================================================================
# CACHEABLE DECORATOR
# =============================================================================

def cacheable(prefix: str, ttl_seconds: Optional[int] = None):
    """
    Async fonksiyon sonuçlarını cache'leyen dekoratör.
    
    Args:
        prefix: Cache key prefix'i
        ttl_seconds: Cache süresi (saniye), None ise config'den okunur
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            ttl = ttl_seconds or _get_ttl(prefix)
            key = _make_key(prefix, args, kwargs)
            
            # Cache hit?
            cached = _cache.get(key)
            if cached is not None:
                return cached
            
            # Cache miss - çalıştır ve kaydet
            result = await func(*args, **kwargs)
            if result is not None:
                _cache.set(key, result, ttl)
            
            return result
        return wrapper
    return decorator


# =============================================================================
# UTILITY
# =============================================================================

def clear_cache() -> None:
    """Cache'i temizle."""
    _cache.clear()


def get_cache_size() -> int:
    """Cache boyutu."""
    return _cache.size()
