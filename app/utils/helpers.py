"""
Utility Helper Functions
========================

Genel yardımcı fonksiyonlar.
- safe_divide: Sıfıra bölme koruması
- sanitize_params: API key maskeleme
"""

from typing import Any, Dict, Optional, Union


def safe_divide(
    numerator: float,
    denominator: Optional[float],
    default: float = 0.0
) -> float:
    """
    None-safe ve zero-safe bölme işlemi.
    
    Args:
        numerator: Bölünen
        denominator: Bölen (None veya 0 olabilir)
        default: Bölme yapılamazsa döndürülecek değer
        
    Returns:
        Bölüm sonucu veya default değer
        
    Examples:
        >>> safe_divide(100, 0)
        0.0
        >>> safe_divide(100, None, default=1.0)
        1.0
        >>> safe_divide(100, 20)
        5.0
    """
    if denominator is None or denominator == 0:
        return default
    return numerator / denominator


def sanitize_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    API parametrelerinden hassas bilgileri maskeler.
    Loglama için güvenli versiyon oluşturur.
    
    Maskelenen anahtarlar:
    - key (Google API key)
    - appid (OpenWeatherMap API key)
    - apikey / api_key
    - authorization
    - token
    
    Args:
        params: API parametreleri dict
        
    Returns:
        Maskelenmiş parametreler
        
    Examples:
        >>> sanitize_params({"key": "abc123", "origin": "Istanbul"})
        {"key": "***REDACTED***", "origin": "Istanbul"}
    """
    if not params:
        return {}
    
    sensitive_keys = {
        "key", "apikey", "api_key", "appid", 
        "authorization", "token", "secret", "password"
    }
    
    sanitized = params.copy()
    for key in sanitized:
        if key.lower() in sensitive_keys:
            sanitized[key] = "***REDACTED***"
    
    return sanitized


def coalesce(*values: Any, default: Any = None) -> Any:
    """
    İlk None olmayan değeri döndürür (SQL COALESCE benzeri).
    
    Args:
        *values: Kontrol edilecek değerler
        default: Tümü None ise döndürülecek değer
        
    Returns:
        İlk None olmayan değer veya default
        
    Examples:
        >>> coalesce(None, None, 5)
        5
        >>> coalesce(None, "hello", 5)
        "hello"
    """
    for value in values:
        if value is not None:
            return value
    return default
