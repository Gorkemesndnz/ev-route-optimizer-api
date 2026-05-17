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
from contextvars import ContextVar, Token
from typing import Any, Callable, Optional, Dict, Tuple
from app.utils.config_manager import config

# =============================================================================
# MEMORY CACHE
# =============================================================================

class MemoryCache:
    """Basit in-memory cache with TTL and max size limit."""
    
    MAX_SIZE = 1000  # Maksimum cache entry sayısı
    CLEANUP_RATIO = 0.2  # Temizlik oranı (%20 en eski entry silinir)
    
    def __init__(self):
        self._store: Dict[str, Tuple[Any, float, float]] = {}  # value, expiration, last_access
    
    def get(self, key: str) -> Optional[Any]:
        """Cache'den değer al."""
        if key not in self._store:
            return None
        value, expiration, _ = self._store[key]
        if expiration < time.time():
            del self._store[key]
            return None
        # Last access zamanını güncelle (LRU için)
        self._store[key] = (value, expiration, time.time())
        return value
    
    def set(self, key: str, value: Any, ttl: int) -> None:
        """Cache'e değer yaz. Max size aşılırsa eski entry'leri temizle."""
        if len(self._store) >= self.MAX_SIZE:
            self._evict_old_entries()
        self._store[key] = (value, time.time() + ttl, time.time())
    
    def _evict_old_entries(self) -> None:
        """En eski erişilen entry'lerin %20'sini sil (LRU-benzeri)."""
        # Önce expired olanları temizle
        now = time.time()
        expired_keys = [k for k, (_, exp, _) in self._store.items() if exp < now]
        for k in expired_keys:
            del self._store[k]
        
        # Hala doluysa, en eski erişilenleri sil
        if len(self._store) >= self.MAX_SIZE:
            entries_to_remove = int(self.MAX_SIZE * self.CLEANUP_RATIO)
            sorted_by_access = sorted(self._store.items(), key=lambda x: x[1][2])
            for k, _ in sorted_by_access[:entries_to_remove]:
                del self._store[k]
    
    def clear(self) -> None:
        """Cache'i temizle."""
        self._store.clear()
    
    def size(self) -> int:
        return len(self._store)


# Singleton
_cache = MemoryCache()
_cache_metrics: ContextVar[Optional[Dict[str, Any]]] = ContextVar("cache_metrics", default=None)


def begin_cache_trace() -> Token:
    """Start request-scope cache hit/miss accounting."""
    return _cache_metrics.set({
        "total_hits": 0,
        "total_misses": 0,
        "total_sets": 0,
        "by_prefix": {},
    })


def get_cache_metrics_snapshot() -> Dict[str, Any]:
    metrics = _cache_metrics.get()
    if not metrics:
        return {}
    return {
        "total_hits": int(metrics.get("total_hits", 0)),
        "total_misses": int(metrics.get("total_misses", 0)),
        "total_sets": int(metrics.get("total_sets", 0)),
        "by_prefix": {
            prefix: dict(values)
            for prefix, values in dict(metrics.get("by_prefix", {})).items()
        },
    }


def end_cache_trace(token: Token) -> None:
    _cache_metrics.reset(token)


def _record_cache_metric(prefix: str, event: str) -> None:
    metrics = _cache_metrics.get()
    if metrics is None:
        return
    plural = "misses" if event == "miss" else f"{event}s"
    metrics[f"total_{plural}"] = int(metrics.get(f"total_{plural}", 0)) + 1
    by_prefix = metrics.setdefault("by_prefix", {})
    item = by_prefix.setdefault(prefix, {"hits": 0, "misses": 0, "sets": 0})
    item[plural] = int(item.get(plural, 0)) + 1


# =============================================================================
# TTL HELPER
# =============================================================================

def _get_ttl(prefix: str) -> int:
    """Prefix'e göre TTL belirle."""
    prefix_lower = prefix.lower()
    # Trafikli istekler için kısa TTL (trafik hızla değişir)
    if "traffic" in prefix_lower:
        return 120  # 2 dakika
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


def _normalize_key_part(value: Any) -> str:
    """Normalize cache key values with stable handling for route models."""
    if hasattr(value, 'lat') and hasattr(value, 'lon'):
        return f"{value.lat},{value.lon}"

    if isinstance(value, dict):
        items = [
            f"{k}:{_normalize_key_part(value[k])}"
            for k in sorted(value.keys())
        ]
        return "{" + ",".join(items) + "}"

    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_normalize_key_part(item) for item in value) + "]"

    if hasattr(value, "model_dump"):
        return _normalize_key_part(value.model_dump(mode="json"))

    return str(value)


def _make_key(prefix: str, args: tuple, kwargs: dict) -> str:
    """Cache key oluştur."""
    parts = [prefix]
    
    for arg in args:
        # self/instance'ları atla (Service, Manager, Calculator vb.)
        if hasattr(arg, '__class__'):
            cls_name = arg.__class__.__name__
            if any(x in cls_name for x in ('Service', 'Manager', 'Calculator', 'Planner')):
                continue
        parts.append(_normalize_key_part(arg))
    
    for k, v in sorted(kwargs.items()):
        parts.append(f"{k}={_normalize_key_part(v)}")
    
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
                _record_cache_metric(prefix, "hit")
                return cached
            
            # Cache miss - çalıştır ve kaydet
            _record_cache_metric(prefix, "miss")
            result = await func(*args, **kwargs)
            if result is not None:
                _cache.set(key, result, ttl)
                _record_cache_metric(prefix, "set")
            
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
